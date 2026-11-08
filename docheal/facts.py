"""Structured "what changed" facts extracted from a code change.

Used two ways:
* as precise hints inside LLM prompts (cuts hallucinated diagnoses), and
* by the deterministic rule engine (offline mode / CI without API keys).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .models import ChangeType, CodeChange, CodeChunk


@dataclass
class Fact:
    kind: str  # rename | removed | param_renamed | param_removed | param_added | default_changed | value_changed | option_renamed | option_removed | route_changed | literal_changed | added
    subject: str  # qualified chunk name
    old: str | None = None
    new: str | None = None
    anchor: str | None = None  # token that must appear on a line for line-scoped replacement
    required: bool = False
    note: str = ""

    def describe(self) -> str:
        k = self.kind
        if k == "rename":
            return f"`{self.old}` was renamed to `{self.new}`"
        if k == "removed":
            what = f"{self.anchor} {self.old}" if self.anchor else self.old
            return f"`{what}` was removed from the codebase"
        if k == "added":
            return f"`{self.new}` was added"
        if k == "param_renamed":
            return f"parameter `{self.old}` of `{self.subject}` was renamed to `{self.new}`"
        if k == "param_removed":
            return f"parameter `{self.old}` of `{self.subject}` was removed"
        if k == "param_added":
            req = "required" if self.required else "optional"
            return f"new {req} parameter `{self.new}` was added to `{self.subject}`"
        if k == "default_changed":
            return f"default of `{self.anchor}` in `{self.subject}` changed from `{self.old}` to `{self.new}`"
        if k == "value_changed":
            return f"value of `{self.anchor}` changed from `{self.old}` to `{self.new}`"
        if k == "option_renamed":
            return f"CLI option `{self.old}` of `{self.subject}` was renamed to `{self.new}`"
        if k == "option_removed":
            return f"CLI option `{self.old}` of `{self.subject}` was removed"
        if k == "option_added":
            return f"CLI option `{self.new}` was added to `{self.subject}`"
        if k == "route_changed":
            return f"route of `{self.subject}` changed from `{self.old}` to `{self.new}`"
        if k == "literal_changed":
            return f"a literal in `{self.subject}` changed from `{self.old}` to `{self.new}`"
        return f"{k}: {self.old} -> {self.new}"


def _literals(src: str) -> list[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            out.append(repr(n.value))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and 0 < len(n.value) <= 40 \
                and "\n" not in n.value:
            out.append(n.value)
    return out


def _strip_quotes(v: str | None) -> str | None:
    if v is None:
        return None
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _const_delta(old_expr: str, new_expr: str) -> tuple[str, str, str | None] | None:
    """If two expressions differ by exactly one literal, return (old, new, keyword-name)."""
    try:
        a, b = ast.parse(old_expr, mode="eval"), ast.parse(new_expr, mode="eval")
    except SyntaxError:
        return None
    if ast.dump(a) == ast.dump(b):
        return None
    na, nb = list(ast.walk(a)), list(ast.walk(b))
    if len(na) != len(nb):
        return None
    diffs = []
    owner: dict[int, str] = {}
    for n in na:
        if isinstance(n, ast.keyword) and n.arg:
            owner[id(n.value)] = n.arg
    for x, y in zip(na, nb):
        if type(x) is not type(y):
            return None
        if isinstance(x, ast.Constant):
            if x.value != y.value:
                diffs.append((x, y))
        elif isinstance(x, (ast.Name, ast.Attribute)) and ast.dump(x) != ast.dump(y) and not diffs:
            pass
    if len(diffs) != 1:
        return None
    x, y = diffs[0]
    return repr(x.value) if not isinstance(x.value, str) else x.value, \
        repr(y.value) if not isinstance(y.value, str) else y.value, owner.get(id(x))


def _param_facts(old: CodeChunk, new: CodeChunk) -> list[Fact]:
    facts: list[Fact] = []
    op = {p["name"]: p for p in old.params}
    np_ = {p["name"]: p for p in new.params}
    removed = [p for p in old.params if p["name"] not in np_]
    added = [p for p in new.params if p["name"] not in op]
    subject = new.qualname
    # 1:1 positional swap -> treat as rename
    paired: set[str] = set()
    if removed and added and len(removed) == len(added):
        for r, a in zip(removed, added):
            facts.append(Fact("param_renamed", subject, r["name"], a["name"]))
            paired.update({r["name"], a["name"]})
            if r.get("default") != a.get("default") and r.get("default") and a.get("default"):
                facts.append(Fact("default_changed", subject, _strip_quotes(r["default"]),
                                  _strip_quotes(a["default"]), anchor=a["name"]))
    for r in removed:
        if r["name"] not in paired:
            facts.append(Fact("param_removed", subject, r["name"]))
    for a in added:
        if a["name"] not in paired:
            facts.append(Fact("param_added", subject, None, a["name"], required=a.get("default") is None
                              and a.get("kind") not in ("vararg", "varkw")))
    for name in op.keys() & np_.keys():
        od, nd = op[name].get("default"), np_[name].get("default")
        if od != nd:
            if od is not None and nd is not None:
                kind = "value_changed" if op[name].get("kind") in ("constant", "env") else "default_changed"
                facts.append(Fact(kind, subject, _strip_quotes(od), _strip_quotes(nd), anchor=name))
                delta = _const_delta(od, nd)
                if delta and delta[0] != _strip_quotes(od):
                    facts.append(Fact(kind, subject, delta[0], delta[1], anchor=delta[2] or name,
                                      note=f"inside `{name}`"))
            elif nd is None:
                facts.append(Fact("param_added", subject, None, name, required=True,
                                  note="parameter became required"))
    return facts


def extract_facts(change: CodeChange) -> list[Fact]:
    ct = ChangeType(change.change_type)
    old, new = change.old, change.new
    if ct == ChangeType.REMOVED and old:
        names = [old.name]
        if old.extra.get("command"):
            names.append(old.extra["command"])
        facts = [Fact("removed", old.qualname, n) for n in dict.fromkeys(names)]
        if old.extra.get("route"):
            # Routes are shared between verbs, so anchor on the HTTP method.
            facts.append(Fact("removed", old.qualname, old.extra["route"],
                              anchor=str(old.extra.get("http_method", "")) or None))
        return facts
    if ct == ChangeType.ADDED and new:
        return [Fact("added", new.qualname, None, new.name)]
    if not (old and new):
        return []
    facts: list[Fact] = []
    if old.name != new.name:
        facts.append(Fact("rename", new.qualname, old.name, new.name))
        oc, nc = old.extra.get("command"), new.extra.get("command")
        if oc and nc and oc != nc:
            facts.append(Fact("rename", new.qualname, oc, nc))
    elif old.extra.get("command") != new.extra.get("command") and old.extra.get("command") and new.extra.get("command"):
        facts.append(Fact("rename", new.qualname, old.extra["command"], new.extra["command"]))
    if old.extra.get("route") and new.extra.get("route") and old.extra["route"] != new.extra["route"]:
        facts.append(Fact("route_changed", new.qualname, old.extra["route"], new.extra["route"]))
    facts += _param_facts(old, new)
    oo, no = set(old.extra.get("options", []) or []), set(new.extra.get("options", []) or [])
    if oo != no:
        gone, came = sorted(oo - no), sorted(no - oo)
        # Typer derives options from params; those are already covered by param facts.
        derived = {f"--{f.old.replace('_', '-')}" for f in facts if f.kind == "param_renamed" and f.old} | \
                  {f"--{f.old.replace('_', '-')}" for f in facts if f.kind == "param_removed" and f.old}
        gone_x = [g for g in gone if g not in derived]
        came_x = [c for c in came if c not in {f"--{f.new.replace('_', '-')}" for f in facts if f.new}]
        if len(gone_x) == len(came_x):
            facts += [Fact("option_renamed", new.qualname, g, c) for g, c in zip(gone_x, came_x)]
        else:
            facts += [Fact("option_removed", new.qualname, g) for g in gone_x]
            facts += [Fact("option_added", new.qualname, None, c) for c in came_x]
        for f in list(facts):
            if f.kind == "param_renamed" and f.old and f.new:
                facts.append(Fact("option_renamed", new.qualname, f"--{f.old.replace('_', '-')}",
                                  f"--{f.new.replace('_', '-')}"))
    if ct == ChangeType.BEHAVIOR and not facts:
        ol, nl = _literals(old.source), _literals(new.source)
        gone = [x for x in ol if x not in nl]
        came = [x for x in nl if x not in ol]
        if len(gone) == 1 and len(came) == 1:
            facts.append(Fact("literal_changed", new.qualname, gone[0], came[0]))
    return facts


def describe_change(change: CodeChange) -> str:
    facts = extract_facts(change)
    head = f"[{change.change_type}] {change.name}"
    if not facts:
        return head + " (no structured facts; inspect the code diff)"
    return head + ": " + "; ".join(f.describe() for f in facts)
