"""Phase 1.3 / 1.4 — build and persist the code-to-docs link graph.

Links come from two sources:

1. **Reference links** (high precision): a doc section mentions an identifier
   that exists in the code — function / class / qualified name, CLI command,
   HTTP route, env var, or a distinctive config field (``max_retries``).
2. **Embedding links** (recall): cosine similarity between the doc section and
   the code chunk embeddings above a threshold (top-k per chunk).

The graph is persisted to ``.docheal/index.json`` so it can be committed to
the repo, inspected, diffed, and reused between runs.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .code_parser import code_identifiers, parse_codebase
from .doc_parser import parse_docs
from .embeddings import Embedder, HashingEmbedder, VectorStore, cosine
from .models import ChunkKind, CodeChunk, DocSection, Link

GENERIC_NAMES = {
    "main", "run", "get", "set", "init", "__init__", "start", "stop", "close", "open", "load", "save",
    "data", "config", "settings", "name", "value", "values", "result", "items", "update", "create",
    "delete", "list", "read", "write", "call", "handle", "process", "build", "parse", "test", "app",
}


def reference_keys(chunk: CodeChunk) -> dict[str, float]:
    """Identifier -> link strength for a chunk."""
    keys: dict[str, float] = {}
    for ident in code_identifiers(chunk):
        if ident.lower() in GENERIC_NAMES:
            continue
        keys[ident] = 1.0
    # Distinctive config fields / parameters are weaker evidence of a link.
    if chunk.kind in (ChunkKind.CONFIG.value, ChunkKind.CLASS.value, ChunkKind.CLI_COMMAND.value,
                      ChunkKind.FUNCTION.value, ChunkKind.METHOD.value, ChunkKind.ENDPOINT.value):
        for p in chunk.params:
            n = p["name"].lstrip("*")
            if "_" in n and len(n) >= 6 and n.lower() not in GENERIC_NAMES:
                keys.setdefault(n, 0.8)
                if chunk.kind == ChunkKind.CONFIG.value and "BaseSettings" in " ".join(chunk.extra.get("bases", [])):
                    keys.setdefault(n.upper(), 0.9)
    for opt in chunk.extra.get("options", []) or []:
        if opt.startswith("--") and len(opt) > 4:
            keys.setdefault(opt, 0.9)
    return keys


@dataclass
class DocGraph:
    code_chunks: dict[str, CodeChunk] = field(default_factory=dict)
    doc_sections: dict[str, DocSection] = field(default_factory=dict)
    links: list[Link] = field(default_factory=list)
    embedder_name: str = "hashing-512"

    # -- queries -----------------------------------------------------------------
    def docs_for_code(self, code_id: str) -> list[Link]:
        return [l for l in self.links if l.code_id == code_id]

    def code_for_doc(self, doc_id: str) -> list[Link]:
        return [l for l in self.links if l.doc_id == doc_id]

    def stats(self) -> dict:
        by_method: dict[str, int] = defaultdict(int)
        for l in self.links:
            by_method[l.method] += 1
        linked_docs = {l.doc_id for l in self.links}
        return {
            "code_chunks": len(self.code_chunks),
            "doc_sections": len(self.doc_sections),
            "links": len(self.links),
            "links_by_method": dict(by_method),
            "doc_coverage": round(len(linked_docs) / max(1, len(self.doc_sections)), 3),
        }

    # -- persistence -------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": __version__,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "embedder": self.embedder_name,
            "stats": self.stats(),
            "code_chunks": [c.to_dict() for c in self.code_chunks.values()],
            "doc_sections": [d.to_dict() for d in self.doc_sections.values()],
            "links": [l.to_dict() for l in self.links],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> DocGraph:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            code_chunks={c["id"]: CodeChunk.from_dict(c) for c in d["code_chunks"]},
            doc_sections={s["id"]: DocSection.from_dict(s) for s in d["doc_sections"]},
            links=[Link(**l) for l in d["links"]],
            embedder_name=d.get("embedder", "hashing-512"),
        )


def reference_links(sections: Iterable[DocSection], chunks: Iterable[CodeChunk]) -> list[Link]:
    index: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for c in chunks:
        for key, w in reference_keys(c).items():
            index[key].append((c.id, w))
    sections = list(sections)
    # Document frequency: weak keys (parameter names) that appear in many sections
    # are not distinctive and would create noisy links.
    df: dict[str, int] = defaultdict(int)
    for s in sections:
        for ref in set(s.references):
            if ref in index:
                df[ref] += 1
    max_df = max(2, int(0.15 * len(sections)))
    links: dict[tuple[str, str], Link] = {}
    for s in sections:
        haystack = set(s.references)
        for ref in haystack:
            for code_id, w in index.get(ref, []):
                if w < 1.0 and df[ref] > max_df:
                    continue
                k = (s.id, code_id)
                if k not in links or links[k].score < w:
                    links[k] = Link(doc_id=s.id, code_id=code_id, method="reference", score=w)
    return list(links.values())


def embedding_links(sections: list[DocSection], chunks: list[CodeChunk], embedder: Embedder,
                    threshold: float, top_k: int = 3, persist_dir: str | None = None,
                    existing: set[tuple[str, str]] | None = None) -> list[Link]:
    if not sections or not chunks:
        return []
    existing = existing or set()
    store = VectorStore(persist_dir, "doc_sections", embedder)
    store.upsert([s.id for s in sections], [s.embedding_text() for s in sections])
    doc_vecs = store.vectors()
    code_vecs = embedder.embed([c.embedding_text() for c in chunks])
    out: list[Link] = []
    for c, cv in zip(chunks, code_vecs):
        scored = sorted(((sid, cosine(cv, dv)) for sid, dv in doc_vecs.items()), key=lambda t: -t[1])
        for sid, score in scored[:top_k]:
            if score >= threshold and (sid, c.id) not in existing:
                out.append(Link(doc_id=sid, code_id=c.id, method="embedding", score=round(score, 4)))
    return out


def link(sections: list[DocSection], chunks: list[CodeChunk], embedder: Embedder | None = None,
         threshold: float = 0.45, top_k: int = 3, persist_dir: str | None = None,
         use_embeddings: bool = True) -> list[Link]:
    links = reference_links(sections, chunks)
    if use_embeddings:
        embedder = embedder or HashingEmbedder()
        existing = {(l.doc_id, l.code_id) for l in links}
        links += embedding_links(sections, chunks, embedder, threshold, top_k, persist_dir, existing)
    return links


def build_index(root: str | Path, code_globs: Iterable[str] = ("**/*.py",),
                doc_globs: Iterable[str] = ("**/*.md",), exclude: Iterable[str] = (),
                embedder: Embedder | None = None, threshold: float = 0.45,
                persist_dir: str | None = None, use_embeddings: bool = True) -> DocGraph:
    embedder = embedder or HashingEmbedder()
    chunks = parse_codebase(root, code_globs, exclude)
    sections = parse_docs(root, doc_globs, exclude)
    links = link(sections, chunks, embedder, threshold, persist_dir=persist_dir, use_embeddings=use_embeddings)
    return DocGraph(
        code_chunks={c.id: c for c in chunks},
        doc_sections={s.id: s for s in sections},
        links=links,
        embedder_name=embedder.name,
    )
