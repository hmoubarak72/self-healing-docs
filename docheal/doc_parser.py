"""Phase 1.2 — split markdown documentation into heading-scoped sections.

Each section records its heading path (``Configuration > Environment
Variables``), raw content, line span and the list of code references it
mentions: inline-code spans, identifiers inside fenced code blocks, ALL_CAPS
config keys, ``--cli-flags`` and HTTP routes.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable
from pathlib import Path

from .models import DocSection

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
ENV_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
FLAG_RE = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]+")
ROUTE_RE = re.compile(r"(?<![\w])/(?:[a-z0-9_{}\-]+/?)+")

STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "import", "return", "def", "class",
    "true", "false", "none", "self", "pip", "install", "python", "print", "str", "int", "bool",
    "float", "dict", "list", "async", "await", "lambda", "yield", "not", "set", "get", "http",
    "https", "json", "yaml", "bash", "shell", "run", "echo", "export",
}


def slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s]+", "-", s)


def extract_references(content: str) -> list[str]:
    refs: set[str] = set()
    in_fence = False
    for line in content.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            for tok in IDENT_RE.findall(line):
                refs.add(tok)
                refs.update(tok.split("."))
            refs.update(FLAG_RE.findall(line))
            continue
        for span in INLINE_CODE_RE.findall(line):
            span = span.strip()
            refs.update(FLAG_RE.findall(span))
            refs.update(ROUTE_RE.findall(span))
            for tok in IDENT_RE.findall(span):
                refs.add(tok)
                refs.update(tok.split("."))
        refs.update(ENV_RE.findall(line))
        refs.update(FLAG_RE.findall(line))
    return sorted(r for r in refs if len(r) > 2 and r.lower() not in STOPWORDS)


def parse_markdown(path: str, text: str) -> list[DocSection]:
    path = path.replace("\\", "/")
    lines = text.splitlines()
    sections: list[DocSection] = []
    stack: list[tuple[int, str]] = []
    current: dict | None = None
    in_fence = False

    def close(end_line: int) -> None:
        if current is None:
            return
        body = "\n".join(lines[current["start"] - 1:end_line])
        if not body.strip():
            return
        sections.append(DocSection(
            id=f"{path}#{current['slug']}",
            path=path,
            heading=current["heading"],
            heading_path=current["heading_path"],
            level=current["level"],
            content=body,
            start_line=current["start"],
            end_line=end_line,
            references=extract_references(body),
        ))

    seen_slugs: dict[str, int] = {}
    for i, line in enumerate(lines, start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        m = None if in_fence else HEADING_RE.match(line)
        if m:
            close(i - 1)
            level, heading = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            hp = " > ".join(h for _, h in stack)
            slug = slugify(hp.replace(" > ", " "))
            n = seen_slugs.get(slug, 0)
            seen_slugs[slug] = n + 1
            if n:
                slug = f"{slug}-{n}"
            current = {"start": i, "heading": heading, "heading_path": hp, "level": level, "slug": slug}
        elif current is None and line.strip():
            current = {"start": i, "heading": "(preamble)", "heading_path": "(preamble)", "level": 0,
                       "slug": "preamble"}
    close(len(lines))
    return sections


def iter_doc_files(root: str | Path, include: Iterable[str] = ("**/*.md",),
                   exclude: Iterable[str] = ()) -> list[str]:
    root = Path(root)
    excludes = ["**/node_modules/**", "**/.git/**", "**/.venv/**", "**/CHANGELOG.md", "**/.docheal/**",
                *exclude]
    out = []
    for pattern in include:
        for p in root.glob(pattern):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, e) or fnmatch.fnmatch("/" + rel, e) for e in excludes):
                continue
            out.append(rel)
    return sorted(set(out))


def parse_docs(root: str | Path, include: Iterable[str] = ("**/*.md",),
               exclude: Iterable[str] = ()) -> list[DocSection]:
    sections: list[DocSection] = []
    for rel in iter_doc_files(root, include, exclude):
        try:
            sections.extend(parse_markdown(rel, (Path(root) / rel).read_text(encoding="utf-8")))
        except (UnicodeDecodeError, OSError):
            continue
    return sections


def replace_section(text: str, section: DocSection, new_content: str) -> str:
    """Replace the line span of ``section`` inside ``text`` with ``new_content``."""
    lines = text.splitlines()
    trailing_nl = text.endswith("\n")
    new_lines = new_content.rstrip("\n").splitlines()
    span = lines[section.start_line - 1: section.end_line]
    trailing = len(span) - len("\n".join(span).rstrip().splitlines()) if span else 0
    while new_lines and not new_lines[-1].strip():
        new_lines.pop()
    new_lines += [""] * trailing  # keep the blank line(s) that separate sections
    out = lines[: section.start_line - 1] + new_lines + lines[section.end_line:]
    return "\n".join(out) + ("\n" if trailing_nl else "")
