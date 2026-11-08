"""End-to-end orchestration: diff -> suspects -> verify -> repair -> validate -> decide."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .diff import DiffResult, detect_changes
from .doc_parser import parse_docs, replace_section
from .embeddings import HashingEmbedder, get_embedder
from .graph import DocGraph, build_index, link
from .llm import Engine, get_engine
from .models import CodeChange, CodeChunk, Correction, DocSection, Verdict

log = logging.getLogger(__name__)


@dataclass
class Suspect:
    section: DocSection
    changes: list[CodeChange]
    link_methods: set[str]
    score: float


@dataclass
class CheckReport:
    engine: str
    diff: DiffResult
    suspects: list[Suspect] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    index_stats: dict = field(default_factory=dict)

    @property
    def accurate(self) -> list[Verdict]:
        return [v for v in self.verdicts if not v.stale]

    @property
    def stale(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.stale]

    @property
    def auto_fixed(self) -> list[Correction]:
        return [c for c in self.corrections if c.mode == "auto_fix"]

    @property
    def flagged(self) -> list[Correction]:
        return [c for c in self.corrections if c.mode == "review"]

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "changed_files": [f.path for f in self.diff.files],
            "meaningful_changes": [{"chunk": c.chunk_id, "type": c.change_type} for c in self.diff.changes],
            "skipped_changes": [{"chunk": c.chunk_id, "type": c.change_type} for c in self.diff.skipped],
            "suspects": [{"doc": s.section.id, "via": sorted(s.link_methods), "score": round(s.score, 3),
                          "changes": [c.chunk_id for c in s.changes]} for s in self.suspects],
            "verdicts": [v.__dict__ for v in self.verdicts],
            "corrections": [c.to_dict() for c in self.corrections],
            "summary": {
                "verified_accurate": len(self.accurate),
                "stale": len(self.stale),
                "auto_fixed": len(self.auto_fixed),
                "flagged": len(self.flagged),
            },
            "index": self.index_stats,
        }


def _changed_chunks(changes: list[CodeChange]) -> list[CodeChunk]:
    out: dict[str, CodeChunk] = {}
    for ch in changes:
        for c in (ch.old, ch.new):
            if c is not None:
                out[f"{c.id}@{'old' if c is ch.old else 'new'}"] = c
    return list(out.values())


def find_suspects(sections: list[DocSection], changes: list[CodeChange], settings: Settings,
                  embedder=None, persisted: DocGraph | None = None) -> list[Suspect]:
    embedder = embedder or HashingEmbedder()
    threshold = settings.similarity_threshold
    if threshold is None:
        threshold = 0.35 if isinstance(embedder, HashingEmbedder) else 0.45
    # Old and new versions share an id unless renamed; tag them so both are linked.
    tagged = []
    owner: dict[str, CodeChange] = {}
    for ch in changes:
        for side, c in (("old", ch.old), ("new", ch.new)):
            if c is None:
                continue
            t = CodeChunk.from_dict({**c.to_dict(), "id": f"{c.id}@{side}"})
            tagged.append(t)
            owner[t.id] = ch
    links = link(sections, tagged, embedder, threshold=threshold, top_k=3)
    if persisted:  # reuse links from the committed index for the pre-change chunks
        changed_ids = {ch.chunk_id for ch in changes}
        for l in persisted.links:
            if l.code_id in changed_ids and l.doc_id in {s.id for s in sections}:
                links.append(type(l)(doc_id=l.doc_id, code_id=f"{l.code_id}@old", method=f"index:{l.method}",
                                     score=l.score))
                owner.setdefault(f"{l.code_id}@old", next(c for c in changes if c.chunk_id == l.code_id))
    by_doc: dict[str, Suspect] = {}
    sec = {s.id: s for s in sections}
    for l in links:
        ch = owner.get(l.code_id)
        if ch is None or l.doc_id not in sec:
            continue
        s = by_doc.get(l.doc_id)
        if s is None:
            s = by_doc[l.doc_id] = Suspect(sec[l.doc_id], [], set(), 0.0)
        if ch not in s.changes:
            s.changes.append(ch)
        s.link_methods.add(l.method)
        s.score = max(s.score, l.score + (1.0 if l.method == "reference" else 0.0))
    # Evidence-weighted prioritisation: sections that literally contain a token the
    # change invalidated (old name / old value) are checked first when capping.
    from .facts import extract_facts

    for s in by_doc.values():
        olds = {f.old for ch in s.changes for f in extract_facts(ch) if f.old}
        if any(o in s.section.content for o in olds):
            s.score += 2.0
    ranked = sorted(by_doc.values(), key=lambda s: -s.score)
    if len(ranked) > settings.max_sections:
        log.warning("Capping suspects at %d (of %d)", settings.max_sections, len(ranked))
    return ranked[: settings.max_sections]


def decide_mode(complexity: str, confidence: float, validation_passed: bool, threshold: float) -> str:
    return "auto_fix" if (complexity == "simple" and validation_passed and confidence >= threshold) else "review"


def run_check(settings: Settings, engine: Engine | None = None) -> CheckReport:
    repo = Path(settings.repo_path)
    engine = engine or get_engine(settings.llm_provider, settings.model)
    embedder = get_embedder(settings.embedding_provider)
    log.info("engine=%s embedder=%s", engine.name, embedder.name)

    diff = detect_changes(repo, settings.base, settings.head, settings.code_globs, settings.exclude)
    report = CheckReport(engine=engine.name, diff=diff)

    persisted = None
    idx = repo / settings.index_path
    if idx.exists():
        try:
            persisted = DocGraph.load(idx)
        except Exception as exc:  # pragma: no cover
            log.warning("Could not load %s: %s", idx, exc)

    if settings.write_index:
        graph = build_index(repo, settings.code_globs, settings.doc_globs, settings.exclude, embedder=embedder,
                            threshold=settings.similarity_threshold or (0.35 if embedder.name.startswith("hashing") else 0.45))
        report.index_stats = graph.stats()
        try:
            graph.save(idx)
        except OSError as exc:  # pragma: no cover
            log.warning("Could not write index: %s", exc)

    if not diff.changes:
        return report

    sections = parse_docs(repo, settings.doc_globs, settings.exclude)
    report.suspects = find_suspects(sections, diff.changes, settings, embedder, persisted)

    for sus in report.suspects:
        verdict = engine.verify(sus.section, sus.changes)
        report.verdicts.append(verdict)
        if not verdict.stale:
            continue
        fix = engine.repair(sus.section, sus.changes, verdict)
        val = engine.validate(sus.section, fix.corrected, sus.changes, verdict)
        conf = min(fix.confidence, val.score if val.passed else fix.confidence * 0.6)
        mode = decide_mode(fix.complexity, conf, val.passed, settings.confidence_threshold)
        report.corrections.append(Correction(
            doc_id=sus.section.id, path=sus.section.path, heading_path=sus.section.heading_path,
            original=sus.section.content, corrected=fix.corrected, mode=mode, confidence=round(conf, 3),
            validation_passed=val.passed, validation_notes=val.notes, explanation=verdict.explanation,
            start_line=sus.section.start_line, end_line=sus.section.end_line,
        ))
    return report


def apply_to_text(text: str, corrections: list[Correction]) -> tuple[str, int]:
    """Apply corrections (all for one file) to ``text``; returns (new_text, applied_count)."""
    applied = 0
    for c in sorted(corrections, key=lambda c: -c.start_line):  # bottom-up keeps line numbers valid
        current = "\n".join(text.splitlines()[c.start_line - 1:c.end_line])
        if current.strip() != c.original.strip():
            log.warning("Section %s differs from the analysed version; skipping", c.doc_id)
            continue
        sec = DocSection(id=c.doc_id, path=c.path, heading=c.heading_path, heading_path=c.heading_path,
                         level=0, content=c.original, start_line=c.start_line, end_line=c.end_line)
        text = replace_section(text, sec, c.corrected)
        applied += 1
    return text, applied


def group_by_file(corrections: list[Correction], modes: tuple[str, ...] = ("auto_fix",)) -> dict[str, list[Correction]]:
    by_file: dict[str, list[Correction]] = defaultdict(list)
    for c in corrections:
        if c.mode in modes:
            by_file[c.path].append(c)
    return dict(by_file)


def apply_corrections(repo: str | Path, corrections: list[Correction], modes: tuple[str, ...] = ("auto_fix",)
                      ) -> dict[str, str]:
    """Apply corrections to files on disk; returns {path: new_content} for files that changed."""
    repo = Path(repo)
    written: dict[str, str] = {}
    for path, items in group_by_file(corrections, modes).items():
        fp = repo / path
        new, n = apply_to_text(fp.read_text(encoding="utf-8"), items)
        if n:
            fp.write_text(new, encoding="utf-8")
            written[path] = new
    return written
