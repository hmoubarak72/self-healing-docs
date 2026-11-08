"""LLM clients and the two staleness engines.

``LLMEngine``  — Claude Sonnet or GPT-4o, used in production. Three prompts:
                 verify (is this section still accurate?), repair (rewrite only
                 the stale parts) and validate (second-pass quality gate).
``RuleEngine`` — deterministic engine driven by structured change facts. Used
                 when no API key is configured (offline / forks / tests), and
                 as a cheap safety net around LLM output.

Both expose the same interface so the pipeline does not care which is active.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Protocol

from .facts import Fact, describe_change, extract_facts
from .models import CodeChange, DocSection, Verdict

log = logging.getLogger(__name__)

TODO_PREFIX = "> **TODO (DocHeal):**"


# ----------------------------------------------------------------------------- clients
class ChatClient(Protocol):
    model: str

    def complete(self, system: str, user: str) -> str: ...


class AnthropicClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model or os.environ.get("DOCHEAL_MODEL") or "claude-sonnet-5-5"

    def complete(self, system: str, user: str) -> str:
        resp = self.client.messages.create(
            model=self.model, max_tokens=4096, temperature=0, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(getattr(b, "text", "") for b in resp.content)


class OpenAIClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        self.model = model or os.environ.get("DOCHEAL_MODEL") or "gpt-4o"

    def complete(self, system: str, user: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or "{}"


def parse_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


# ----------------------------------------------------------------------------- shared helpers
FENCE = re.compile(r"^\s*(```|~~~)")
INLINE = re.compile(r"`[^`\n]+`")


def _pattern(token: str) -> re.Pattern:
    esc = re.escape(token)
    if token[:1] in "-/":
        return re.compile(rf"(?<![\w\-/]){esc}(?![\w\-/{{}}])")
    return re.compile(rf"(?<![\w]){esc}(?![\w])")


def distinctive(token: str) -> bool:
    """Tokens unlikely to collide with ordinary English prose."""
    return bool(re.search(r"[_./\-{}]|\d|[a-z][A-Z]|^[A-Z0-9_]{3,}$", token)) or token[:1].isupper()


def _map_code(text: str, fn) -> str:
    """Apply ``fn`` only to code contexts: fenced blocks and inline code spans."""
    out, in_fence = [], False
    for line in text.split("\n"):
        if FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
        elif in_fence:
            out.append(fn(line))
        else:
            out.append(INLINE.sub(lambda m: fn(m.group(0)), line))
    return "\n".join(out)


def _code_text(text: str) -> str:
    parts: list[str] = []
    _map_code(text, lambda seg: parts.append(seg) or seg)
    return "\n".join(parts)


def mentions(text: str, token: str | None) -> bool:
    if not token:
        return False
    target = text if distinctive(token) else _code_text(text)
    return bool(_pattern(token).search(target))


def _replace(text: str, old: str, new: str) -> str:
    pat = _pattern(old)
    if distinctive(old):
        return pat.sub(lambda _m: new, text)
    return _map_code(text, lambda seg: pat.sub(lambda _m: new, seg))


def _param_pattern(name: str) -> re.Pattern:
    """A parameter is referenced as ``name=``, an exact inline span `name`, or a ``name:`` field."""
    n = re.escape(name)
    return re.compile(rf"(?<![\w.]){n}(?=\s*=)|(?<=`){n}(?=`)|(?<![\w.]){n}(?=\s*:\s*\w)")


def mentions_param(text: str, name: str | None) -> bool:
    return bool(name) and bool(_param_pattern(name).search(text))


def _replace_param(text: str, old: str, new: str) -> str:
    return _param_pattern(old).sub(lambda _m: new, text)


def _value_forms(v: str | None) -> list[str]:
    """``5.0`` is written as ``5`` in prose; accept both."""
    if not v:
        return []
    forms = [v]
    if re.fullmatch(r"-?\d+\.0", v):
        forms.append(v[:-2])
    return forms


def _new_value_for(old_form: str, old: str, new: str) -> str:
    if old_form != old and re.fullmatch(r"-?\d+\.0", new):
        return new[:-2]
    return new


def _value_hits(lines: list[str], anchor: str, old: str, new: str) -> list[tuple[int, str]]:
    """Line indices (and matched form) where ``old`` appears with ``anchor`` on the same or previous line."""
    forms = _anchor_forms(anchor)
    hits = []
    for i, line in enumerate(lines):
        window = line + "\n" + (lines[i - 1] if i > 0 else "")
        if not any(mentions(window, a) for a in forms):
            continue
        for vf in _value_forms(old):
            if mentions(line, vf) and not mentions(line, _new_value_for(vf, old, new)):
                hits.append((i, vf))
                break
    return hits


def _anchor_forms(anchor: str) -> list[str]:
    forms = {anchor, anchor.upper(), f"--{anchor.replace('_', '-')}"}
    return [f for f in forms if f]


@dataclass
class RepairResult:
    corrected: str
    confidence: float
    complexity: str  # "simple" | "complex"
    summary: str


@dataclass
class ValidationResult:
    passed: bool
    score: float
    notes: str


class Engine(Protocol):
    name: str

    def verify(self, section: DocSection, changes: list[CodeChange]) -> Verdict: ...

    def repair(self, section: DocSection, changes: list[CodeChange], verdict: Verdict) -> RepairResult: ...

    def validate(self, section: DocSection, corrected: str, changes: list[CodeChange],
                 verdict: Verdict) -> ValidationResult: ...


# ----------------------------------------------------------------------------- rule engine
FIXABLE = {"rename", "param_renamed", "option_renamed", "route_changed", "default_changed", "value_changed",
           "literal_changed"}


class RuleEngine:
    """Deterministic, explainable engine built on structured change facts."""

    name = "rules"

    def _findings(self, section: DocSection, changes: list[CodeChange]) -> list[tuple[Fact, bool]]:
        """Return (fact, fixable) pairs that contradict the section."""
        text = section.content
        out: list[tuple[Fact, bool]] = []
        for ch in changes:
            for f in extract_facts(ch):
                k = f.kind
                if k == "param_renamed":
                    if mentions_param(text, f.old) and f.old != f.new:
                        out.append((f, True))
                elif k in ("rename", "option_renamed", "route_changed"):
                    if mentions(text, f.old) and f.old != f.new:
                        out.append((f, True))
                elif k == "param_removed":
                    if mentions_param(text, f.old):
                        out.append((f, False))
                elif k in ("removed", "option_removed"):
                    if f.anchor:
                        if any(mentions(l, f.old) and f.anchor in l for l in text.splitlines()):
                            out.append((f, False))
                    elif mentions(text, f.old):
                        out.append((f, False))
                elif k in ("default_changed", "value_changed"):
                    if _value_hits(text.splitlines(), f.anchor or "", f.old or "", f.new or ""):
                        out.append((f, True))
                elif k == "literal_changed":
                    if f.old and len(f.old) >= 2 and mentions(text, f.old):
                        out.append((f, True))
                elif k == "param_added" and f.required:
                    name = f.subject.rsplit(".", 1)[-1]
                    if re.search(rf"\b{re.escape(name)}\(", text):
                        out.append((f, False))
        return out

    def verify(self, section: DocSection, changes: list[CodeChange]) -> Verdict:
        findings = self._findings(section, changes)
        if not findings:
            return Verdict(doc_id=section.id, stale=False, confidence=0.6,
                           explanation="No statement in this section contradicts the code change.",
                           related_changes=[c.chunk_id for c in changes])
        issues = [f.describe() + ("" if fixable else " (needs a human to rewrite)") for f, fixable in findings]
        conf = 0.9 if all(fx for _, fx in findings) else 0.55
        if any(f.kind == "literal_changed" for f, _ in findings):
            conf = min(conf, 0.7)
        return Verdict(doc_id=section.id, stale=True, confidence=conf, issues=issues,
                       explanation="The section documents behavior that no longer matches the code: "
                                   + "; ".join(issues) + ".",
                       related_changes=[c.chunk_id for c in changes])

    def repair(self, section: DocSection, changes: list[CodeChange], verdict: Verdict) -> RepairResult:
        findings = self._findings(section, changes)
        lines = section.content.splitlines()
        todos: list[str] = []
        for f, fixable in findings:
            if not fixable:
                todos.append(f.describe())
                continue
            if f.kind in ("default_changed", "value_changed"):
                for i, vf in _value_hits(lines, f.anchor or "", f.old or "", f.new or ""):
                    lines[i] = _replace(lines[i], vf, _new_value_for(vf, f.old, f.new))
            elif f.kind == "literal_changed":
                lines = [_replace(l, f.old, f.new) for l in lines]
            elif f.kind == "param_renamed":
                lines = [_replace_param(l, f.old, f.new) for l in lines]
            else:
                lines = [_replace(l, f.old, f.new) for l in lines]
        if todos:
            insert_at = 1 if lines and lines[0].lstrip().startswith("#") else 0
            block = [""] + [f"{TODO_PREFIX} {t} — please review and update this section." for t in todos]
            lines = lines[:insert_at] + block + lines[insert_at:]
        fixable_all = bool(findings) and not todos
        conf = verdict.confidence if fixable_all else min(verdict.confidence, 0.5)
        return RepairResult(
            corrected="\n".join(lines),
            confidence=conf,
            complexity="simple" if fixable_all else "complex",
            summary="; ".join(f.describe() for f, _ in findings) or "no change",
        )

    def validate(self, section: DocSection, corrected: str, changes: list[CodeChange],
                 verdict: Verdict) -> ValidationResult:
        return structural_validation(section, corrected, changes)


def structural_validation(section: DocSection, corrected: str, changes: list[CodeChange]) -> ValidationResult:
    """Model-independent checks every correction must pass."""
    notes: list[str] = []
    orig_lines = section.content.splitlines()
    new_lines = [l for l in corrected.splitlines() if not l.startswith(TODO_PREFIX)]
    if orig_lines and orig_lines[0].startswith("#") and (not new_lines or new_lines[0] != orig_lines[0]):
        notes.append("heading line was altered")
    # stale tokens from fixable renames must be gone
    for ch in changes:
        for f in extract_facts(ch):
            body = "\n".join(new_lines)
            if not f.old:
                continue
            if f.new:
                body = body.replace(f.new, "")
            still = mentions_param(body, f.old) if f.kind == "param_renamed" else \
                mentions(body, f.old) if f.kind in ("rename", "option_renamed", "route_changed") else False
            if still:
                notes.append(f"stale reference `{f.old}` still present")
    # preservation: the edit should be local
    import difflib

    sm = difflib.SequenceMatcher(None, orig_lines, [l for l in new_lines if l.strip() or True])
    changed = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")
    ratio = changed / max(1, len(orig_lines))
    if ratio > 0.6 and len(orig_lines) > 4:
        notes.append(f"correction rewrote {ratio:.0%} of the section (expected a targeted edit)")
    if corrected.strip() == section.content.strip():
        notes.append("correction made no change")
    passed = not notes
    score = 1.0 if passed else max(0.0, 0.6 - 0.15 * len(notes))
    return ValidationResult(passed=passed, score=score, notes="; ".join(notes) or "all structural checks passed")


# ----------------------------------------------------------------------------- LLM engine
VERIFY_SYSTEM = """You are a meticulous senior technical writer and code reviewer.
You decide whether a documentation section is still ACCURATE after a code change.
Only judge factual accuracy (names, parameters, defaults, types, routes, CLI flags, behavior).
Missing documentation for a brand-new optional feature is NOT inaccuracy.
Respond with JSON only: {"stale": bool, "confidence": 0..1, "issues": [string], "explanation": string}"""

REPAIR_SYSTEM = """You repair stale technical documentation with minimal, surgical edits.
Rules:
- Rewrite ONLY the statements that are inaccurate given the new code. Leave every accurate sentence byte-for-byte identical.
- Preserve the heading line, markdown structure, tone, voice, and formatting conventions.
- Never invent behavior that is not visible in the new code.
- If the change is complex (new feature, removed capability, semantics you cannot fully verify), keep your best draft AND add a line starting with "> **TODO (DocHeal):**" describing what a human must confirm.
Respond with JSON only: {"corrected_section": string, "confidence": 0..1, "complexity": "simple"|"complex", "summary": string}"""

VALIDATE_SYSTEM = """You are the quality gate for automated documentation fixes.
Check: (1) does the corrected section accurately describe the NEW code? (2) were already-correct parts preserved?
(3) is style consistent with the original? (4) no hallucinated details?
Respond with JSON only: {"accurate": bool, "preserved": bool, "style_consistent": bool, "score": 0..1, "notes": string}"""


def _code_block(chunk) -> str:
    return f"```python\n{chunk.source}\n```" if chunk else "(does not exist)"


def _changes_prompt(changes: list[CodeChange]) -> str:
    parts = []
    for ch in changes:
        parts.append(
            f"### Change: {describe_change(ch)}\n"
            f"OLD CODE ({ch.old.path if ch.old else '-'}):\n{_code_block(ch.old)}\n"
            f"NEW CODE ({ch.new.path if ch.new else '-'}):\n{_code_block(ch.new)}"
        )
    return "\n\n".join(parts)


class LLMEngine:
    def __init__(self, client: ChatClient):
        self.client = client
        self.name = f"llm:{client.model}"

    def _ask(self, system: str, user: str) -> dict:
        raw = self.client.complete(system, user)
        try:
            return parse_json(raw)
        except Exception as exc:
            log.warning("LLM returned non-JSON output (%s): %.200s", exc, raw)
            return {}

    def verify(self, section: DocSection, changes: list[CodeChange]) -> Verdict:
        user = (f"{_changes_prompt(changes)}\n\n## Documentation section `{section.heading_path}` "
                f"({section.path})\n```markdown\n{section.content}\n```\n\nIs this section still accurate?")
        d = self._ask(VERIFY_SYSTEM, user)
        if not d:
            return RuleEngine().verify(section, changes)
        return Verdict(doc_id=section.id, stale=bool(d.get("stale")), confidence=float(d.get("confidence", 0.5)),
                       issues=[str(i) for i in d.get("issues", [])], explanation=str(d.get("explanation", "")),
                       related_changes=[c.chunk_id for c in changes])

    def repair(self, section: DocSection, changes: list[CodeChange], verdict: Verdict) -> RepairResult:
        user = (f"{_changes_prompt(changes)}\n\n## Staleness diagnosis\n{verdict.explanation}\n"
                + "\n".join(f"- {i}" for i in verdict.issues)
                + f"\n\n## Current section ({section.path})\n```markdown\n{section.content}\n```")
        d = self._ask(REPAIR_SYSTEM, user)
        if not d or not d.get("corrected_section"):
            return RuleEngine().repair(section, changes, verdict)
        corrected = str(d["corrected_section"]).strip("\n")
        complexity = str(d.get("complexity", "complex"))
        if TODO_PREFIX in corrected:
            complexity = "complex"
        return RepairResult(corrected=corrected, confidence=float(d.get("confidence", 0.5)),
                            complexity=complexity, summary=str(d.get("summary", "")))

    def validate(self, section: DocSection, corrected: str, changes: list[CodeChange],
                 verdict: Verdict) -> ValidationResult:
        structural = structural_validation(section, corrected, changes)
        user = (f"{_changes_prompt(changes)}\n\n## Original section\n```markdown\n{section.content}\n```\n\n"
                f"## Corrected section\n```markdown\n{corrected}\n```")
        d = self._ask(VALIDATE_SYSTEM, user)
        if not d:
            return structural
        ok = bool(d.get("accurate")) and bool(d.get("preserved")) and bool(d.get("style_consistent"))
        score = min(float(d.get("score", 0.5)), structural.score)
        notes = str(d.get("notes", ""))
        if not structural.passed:
            notes = f"{notes} | structural: {structural.notes}".strip(" |")
        return ValidationResult(passed=ok and structural.passed, score=score, notes=notes)


def get_engine(provider: str = "auto", model: str | None = None) -> Engine:
    """Pick an engine. ``auto`` prefers Anthropic, then OpenAI, then the rule engine."""
    provider = (provider or "auto").lower()
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    try:
        if provider in ("anthropic", "claude") and has_anthropic or (provider == "auto" and has_anthropic):
            return LLMEngine(AnthropicClient(model=model))
        if provider == "openai" and has_openai or (provider == "auto" and has_openai):
            return LLMEngine(OpenAIClient(model=model))
    except ImportError as exc:  # pragma: no cover
        log.warning("LLM SDK missing (%s); using rule engine", exc)
    if provider not in ("auto", "rules"):
        log.warning("No API key for provider %r; falling back to the deterministic rule engine", provider)
    return RuleEngine()
