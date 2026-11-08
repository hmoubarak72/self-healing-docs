"""Embedding providers and a file-based vector store.

* ``OpenAIEmbedder`` — ``text-embedding-3-small`` (production default)
* ``HashingEmbedder`` — deterministic, offline, dependency-free fallback used in
  tests, local runs without an API key, and as a cheap pre-filter.

``VectorStore`` wraps a persistent ChromaDB collection (file-based, no server)
and falls back to an in-memory numpy index when ChromaDB is not installed.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from collections.abc import Sequence
from typing import Protocol

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z][a-z0-9]+|[A-Z]+(?![a-z])|\d+")


def _tokens(text: str) -> list[str]:
    """Split identifiers into sub-words: ``max_retries`` / ``maxRetries`` -> max, retries."""
    out: list[str] = []
    for raw in re.split(r"[^A-Za-z0-9]+", text):
        if not raw:
            continue
        parts = _TOKEN_RE.findall(raw) or [raw]
        out.extend(p.lower() for p in parts)
        if len(parts) > 1:
            out.append(raw.lower())
    return out


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Signed feature-hashing bag-of-subwords + bigrams, L2-normalized."""

    name = "hashing-512"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        toks = _tokens(text)
        feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
        for f in feats:
            h = int.from_bytes(hashlib.md5(f.encode()).digest()[:8], "little")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class OpenAIEmbedder:
    name = "text-embedding-3-small"

    def __init__(self, api_key: str | None = None, model: str = "text-embedding-3-small", batch: int = 96):
        from openai import OpenAI  # imported lazily so the package works offline

        self.client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        self.model = model
        self.name = model
        self.batch = batch

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = [t[:8000] or " " for t in texts[i:i + self.batch]]
            resp = self.client.embeddings.create(model=self.model, input=chunk)
            out.extend(d.embedding for d in resp.data)
        return out


def get_embedder(provider: str = "auto", api_key: str | None = None) -> Embedder:
    provider = (provider or "auto").lower()
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if provider in ("openai", "auto") and key:
        try:
            return OpenAIEmbedder(api_key=key)
        except Exception as exc:  # pragma: no cover - depends on env
            log.warning("OpenAI embedder unavailable (%s); falling back to hashing embedder", exc)
    if provider == "openai" and not key:
        log.warning("OPENAI_API_KEY not set; falling back to hashing embedder")
    return HashingEmbedder()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class VectorStore:
    """Persistent ChromaDB collection with a pure-python fallback."""

    def __init__(self, persist_dir: str | None, collection: str, embedder: Embedder):
        self.embedder = embedder
        self._ids: list[str] = []
        self._vecs: list[list[float]] = []
        self._chroma = None
        if persist_dir:
            try:
                import chromadb  # type: ignore

                client = chromadb.PersistentClient(path=persist_dir)
                safe = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{collection}_{embedder.name}")[:60]
                self._chroma = client.get_or_create_collection(safe, metadata={"hnsw:space": "cosine"})
            except Exception as exc:  # ImportError or runtime issues
                log.info("ChromaDB unavailable (%s); using in-memory vector index", exc)
                self._chroma = None

    @property
    def backend(self) -> str:
        return "chromadb" if self._chroma is not None else "memory"

    def upsert(self, ids: Sequence[str], texts: Sequence[str]) -> None:
        if not ids:
            return
        vecs = self.embedder.embed(list(texts))
        if self._chroma is not None:
            self._chroma.upsert(ids=list(ids), embeddings=vecs, documents=[t[:2000] for t in texts])
        lookup = {i: n for n, i in enumerate(self._ids)}
        for i, v in zip(ids, vecs):
            if i in lookup:
                self._vecs[lookup[i]] = v
            else:
                self._ids.append(i)
                self._vecs.append(v)

    def query(self, text: str, k: int = 5) -> list[tuple[str, float]]:
        vec = self.embedder.embed([text])[0]
        return self.query_vec(vec, k)

    def query_vec(self, vec: Sequence[float], k: int = 5) -> list[tuple[str, float]]:
        if self._chroma is not None and self._chroma.count() > 0:
            res = self._chroma.query(query_embeddings=[list(vec)], n_results=min(k, self._chroma.count()))
            return [(i, 1.0 - d) for i, d in zip(res["ids"][0], res["distances"][0])]
        scored = [(i, cosine(vec, v)) for i, v in zip(self._ids, self._vecs)]
        return sorted(scored, key=lambda t: -t[1])[:k]

    def vectors(self) -> dict[str, list[float]]:
        return dict(zip(self._ids, self._vecs))
