"""Phase 2.1 / 2.2 — parse the git diff and classify code changes.

The diff is mapped onto *semantic chunks* rather than raw lines: both sides of
every changed file are parsed and chunks are compared by stable id. That makes
filtering precise:

* comment / whitespace edits  -> AST identical      -> ``cosmetic`` (skipped)
* docstring-only edits        -> behavior hash same -> ``docstring_only`` (skipped)
* test files                  -> skipped entirely
* signature changes, added / removed / renamed chunks, behavior changes
  -> meaningful, forwarded to staleness detection
"""

from __future__ import annotations

import difflib
import fnmatch
import logging
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .code_parser import is_test_file, parse_source
from .models import MEANINGFUL_CHANGES, ChangeType, CodeChange, CodeChunk

log = logging.getLogger(__name__)
WORKTREE = "WORKTREE"


def git(repo: str | Path, *args: str, check: bool = True) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout


@dataclass
class FileChange:
    status: str  # A / M / D / R
    path: str
    old_path: str | None = None


@dataclass
class DiffResult:
    files: list[FileChange] = field(default_factory=list)
    changes: list[CodeChange] = field(default_factory=list)
    skipped: list[CodeChange] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)

    @property
    def meaningful(self) -> list[CodeChange]:
        return self.changes


def changed_files(repo: str | Path, base: str, head: str = WORKTREE) -> list[FileChange]:
    if head == WORKTREE:
        out = git(repo, "diff", "--name-status", "-M", base)
        untracked = git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    else:
        out = git(repo, "diff", "--name-status", "-M", f"{base}...{head}")
        untracked = []
    files: list[FileChange] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        st = parts[0][0]
        if st == "R" and len(parts) >= 3:
            files.append(FileChange("R", parts[2], parts[1]))
        elif len(parts) >= 2:
            files.append(FileChange(st, parts[1]))
    files += [FileChange("A", p) for p in untracked]
    return files


def file_at(repo: str | Path, ref: str, path: str) -> str | None:
    if ref == WORKTREE:
        p = Path(repo) / path
        return p.read_text(encoding="utf-8") if p.exists() else None
    res = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{path}"], capture_output=True, text=True)
    return res.stdout if res.returncode == 0 else None


def merge_base(repo: str | Path, base: str, head: str) -> str:
    if head == WORKTREE:
        return base
    try:
        return git(repo, "merge-base", base, head).strip() or base
    except RuntimeError:
        return base


def classify(old: CodeChunk | None, new: CodeChunk | None) -> ChangeType | None:
    if old is None and new is None:
        return None
    if old is None:
        return ChangeType.ADDED
    if new is None:
        return ChangeType.REMOVED
    if old.source == new.source:
        return None
    if old.signature_hash != new.signature_hash:
        return ChangeType.SIGNATURE
    if old.behavior_hash != new.behavior_hash:
        return ChangeType.BEHAVIOR
    if old.docstring != new.docstring:
        return ChangeType.DOCSTRING
    return ChangeType.COSMETIC


def _rename_score(a: CodeChunk, b: CodeChunk) -> float:
    body_a = a.source.replace(a.name, "§")
    body_b = b.source.replace(b.name, "§")
    return difflib.SequenceMatcher(None, body_a, body_b).ratio()


def _pair_renames(changes: list[CodeChange], threshold: float = 0.7) -> list[CodeChange]:
    removed = [c for c in changes if c.change_type == ChangeType.REMOVED.value]
    added = [c for c in changes if c.change_type == ChangeType.ADDED.value]
    used: set[int] = set()
    out = [c for c in changes if c.change_type not in (ChangeType.REMOVED.value, ChangeType.ADDED.value)]
    for r in removed:
        best, best_score = None, threshold
        for i, a in enumerate(added):
            if i in used or a.new.kind != r.old.kind or a.new.path != r.old.path:
                continue
            s = _rename_score(r.old, a.new)
            if s >= best_score:
                best, best_score = i, s
        if best is None:
            out.append(r)
        else:
            used.add(best)
            a = added[best]
            a.new.extra = {**a.new.extra, "renamed_from": r.old.qualname}
            out.append(CodeChange(chunk_id=r.chunk_id, change_type=ChangeType.SIGNATURE.value,
                                  old=r.old, new=a.new))
    out += [a for i, a in enumerate(added) if i not in used]
    return out


def detect_changes(repo: str | Path, base: str, head: str = WORKTREE,
                   code_globs: Iterable[str] = ("**/*.py",), exclude: Iterable[str] = ()) -> DiffResult:
    base_ref = merge_base(repo, base, head)
    result = DiffResult(files=changed_files(repo, base_ref, head))
    code_globs = list(code_globs)
    exclude = list(exclude)
    raw: list[CodeChange] = []
    for fc in result.files:
        if not fc.path.endswith(".py"):
            continue
        if not any(fnmatch.fnmatch(fc.path, g) or fnmatch.fnmatch("/" + fc.path, g) or g == "**/*.py"
                   for g in code_globs):
            continue
        if any(fnmatch.fnmatch(fc.path, e) for e in exclude) or is_test_file(fc.path):
            result.skipped_files.append(fc.path)
            continue
        old_src = None if fc.status == "A" else file_at(repo, base_ref, fc.old_path or fc.path)
        new_src = None if fc.status == "D" else file_at(repo, head, fc.path)
        old_chunks = {c.qualname: c for c in parse_source(fc.old_path or fc.path, old_src or "")}
        new_chunks = {c.qualname: c for c in parse_source(fc.path, new_src or "")}
        for q in sorted(set(old_chunks) | set(new_chunks)):
            o, n = old_chunks.get(q), new_chunks.get(q)
            ct = classify(o, n)
            if ct is None:
                continue
            raw.append(CodeChange(chunk_id=(n or o).id, change_type=ct.value, old=o, new=n))
    for ch in _pair_renames(raw):
        if ChangeType(ch.change_type) in MEANINGFUL_CHANGES:
            result.changes.append(ch)
        else:
            result.skipped.append(ch)
    log.info("diff: %d files, %d meaningful chunk changes, %d skipped",
             len(result.files), len(result.changes), len(result.skipped))
    return result
