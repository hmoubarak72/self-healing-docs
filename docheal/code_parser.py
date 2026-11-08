"""Phase 1.1 — parse a Python codebase into semantic chunks.

Extracted units (each with a stable ``path::qualname`` identifier):

* functions / methods (signature + docstring)
* classes (bases + ``__init__`` signature + declared fields)
* API endpoints (FastAPI / Flask style ``@app.get("/route")`` decorators)
* CLI commands (click / typer ``@cli.command()`` + ``@click.option`` flags)
* configuration schemas (pydantic ``BaseSettings`` / ``BaseModel`` / dataclass
  config classes, module-level UPPER_CASE constants, ``os.getenv`` keys)

Two hashes are recorded per chunk so the change detector can tell *what kind*
of change happened:

* ``signature_hash`` — public API surface
* ``behavior_hash``  — the AST with docstrings removed (comments and
  whitespace never reach the AST, so cosmetic edits do not change it)
"""

from __future__ import annotations

import ast
import copy
import fnmatch
import hashlib
import os
from collections.abc import Iterable
from pathlib import Path

from .models import ChunkKind, CodeChunk

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "route", "api_route", "websocket"}
CONFIG_BASES = {"BaseSettings", "BaseModel", "TypedDict", "NamedTuple"}
DEFAULT_EXCLUDES = [
    "**/.git/**", "**/node_modules/**", "**/.venv/**", "**/venv/**",
    "**/__pycache__/**", "**/build/**", "**/dist/**", "**/.docheal/**",
]


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def is_test_file(path: str) -> bool:
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    return (
        "/tests/" in f"/{p}" or "/test/" in f"/{p}"
        or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
    )


def _strip_docstrings(node: ast.AST) -> ast.AST:
    node = copy.deepcopy(node)
    for n in ast.walk(node):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = getattr(n, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                n.body = body[1:] or [ast.Pass()]
    return node


def _dump(node: ast.AST) -> str:
    return ast.dump(_strip_docstrings(node), annotate_fields=True, include_attributes=False)


def _params(args: ast.arguments) -> list[dict]:
    out: list[dict] = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for a, d in zip(positional, defaults):
        out.append({
            "name": a.arg,
            "annotation": ast.unparse(a.annotation) if a.annotation else None,
            "default": ast.unparse(d) if d is not None else None,
            "kind": "positional",
        })
    if args.vararg:
        out.append({"name": "*" + args.vararg.arg, "annotation": None, "default": None, "kind": "vararg"})
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        out.append({
            "name": a.arg,
            "annotation": ast.unparse(a.annotation) if a.annotation else None,
            "default": ast.unparse(d) if d is not None else None,
            "kind": "keyword",
        })
    if args.kwarg:
        out.append({"name": "**" + args.kwarg.arg, "annotation": None, "default": None, "kind": "varkw"})
    return [p for p in out if p["name"] not in ("self", "cls")]


def _decorator_info(dec: ast.expr) -> tuple[str, list[str], dict[str, str]]:
    """Return (dotted name, positional string args, keyword string args)."""
    call_args: list[str] = []
    call_kwargs: dict[str, str] = {}
    target = dec
    if isinstance(dec, ast.Call):
        target = dec.func
        for a in dec.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                call_args.append(a.value)
        for kw in dec.keywords:
            if kw.arg and isinstance(kw.value, ast.Constant):
                call_kwargs[kw.arg] = str(kw.value.value)
    try:
        name = ast.unparse(target)
    except Exception:  # pragma: no cover - defensive
        name = ""
    return name, call_args, call_kwargs


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str, source: str):
        self.path = path
        self.source = source
        self.lines = source.splitlines()
        self.chunks: list[CodeChunk] = []
        self.stack: list[str] = []

    # -- helpers -------------------------------------------------------------
    def _segment(self, node: ast.AST) -> str:
        start = getattr(node, "lineno", 1)
        if getattr(node, "decorator_list", None):
            start = min(start, *[d.lineno for d in node.decorator_list])
        end = getattr(node, "end_lineno", start)
        return "\n".join(self.lines[start - 1:end])

    def _add(self, *, node: ast.AST, name: str, kind: ChunkKind, signature: str, docstring: str,
             params: list[dict], behavior_node: ast.AST | None = None, extra: dict | None = None,
             qualname: str | None = None) -> None:
        qual = qualname or ".".join(self.stack + [name])
        start = getattr(node, "lineno", 1)
        if getattr(node, "decorator_list", None):
            start = min(start, *[d.lineno for d in node.decorator_list])
        self.chunks.append(CodeChunk(
            id=f"{self.path}::{qual}",
            path=self.path,
            name=name,
            qualname=qual,
            kind=kind.value,
            signature=signature,
            docstring=docstring or "",
            source=self._segment(node),
            start_line=start,
            end_line=getattr(node, "end_lineno", start),
            signature_hash=_h(signature + repr(sorted((extra or {}).items(), key=str))),
            behavior_hash=_h(_dump(behavior_node if behavior_node is not None else node)),
            params=params,
            extra=extra or {},
        ))

    # -- functions -----------------------------------------------------------
    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        name = node.name
        in_class = bool(self.stack)  # the stack only ever holds enclosing classes
        if name.startswith("_") and name != "__init__":
            return  # private helpers rarely appear in docs
        params = _params(node.args)
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        signature = f"{prefix} {name}({ast.unparse(node.args)}){ret}"
        kind = ChunkKind.METHOD if in_class else ChunkKind.FUNCTION
        extra: dict = {}
        options: list[str] = []
        for dec in node.decorator_list:
            dname, dargs, dkw = _decorator_info(dec)
            last = dname.rsplit(".", 1)[-1]
            if last in HTTP_METHODS and dargs:
                kind = ChunkKind.ENDPOINT
                extra["http_method"] = last.upper() if last not in ("route", "api_route") else dkw.get("methods", "GET")
                extra["route"] = dargs[0]
            elif last == "command" or last == "group":
                kind = ChunkKind.CLI_COMMAND
                extra["command"] = dargs[0] if dargs else dkw.get("name", name.replace("_", "-"))
            elif last in ("option", "argument"):
                options.extend(a for a in dargs if a.startswith("-") or last == "argument")
        if kind == ChunkKind.CLI_COMMAND:
            if not options:  # typer: parameters become options
                options = [f"--{p['name'].replace('_', '-')}" for p in params if p["default"] is not None]
            extra["options"] = sorted(options)
        self._add(node=node, name=name, kind=kind, signature=signature,
                  docstring=ast.get_docstring(node) or "", params=params, extra=extra)

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    # -- classes -------------------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name.startswith("_"):
            return
        bases = [ast.unparse(b) for b in node.bases]
        decorators = [_decorator_info(d)[0] for d in node.decorator_list]
        fields: list[dict] = []
        init_sig = ""
        non_method_body: list[ast.stmt] = []
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields.append({
                    "name": stmt.target.id,
                    "annotation": ast.unparse(stmt.annotation),
                    "default": ast.unparse(stmt.value) if stmt.value is not None else None,
                    "kind": "field",
                })
                non_method_body.append(stmt)
            elif isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    if isinstance(t, ast.Name):
                        fields.append({"name": t.id, "annotation": None,
                                       "default": ast.unparse(stmt.value), "kind": "field"})
                non_method_body.append(stmt)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == "__init__":
                init_sig = f"({ast.unparse(stmt.args)})"
            elif not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                non_method_body.append(stmt)
        is_config = (
            any(b.rsplit(".", 1)[-1] in CONFIG_BASES for b in bases)
            or (any(d.endswith("dataclass") for d in decorators)
                and any(node.name.endswith(s) for s in ("Config", "Settings", "Options")))
            or node.name.endswith(("Config", "Settings"))
        )
        kind = ChunkKind.CONFIG if is_config else ChunkKind.CLASS
        sig = f"class {node.name}({', '.join(bases)})" + (f" __init__{init_sig}" if init_sig else "")
        if fields:
            sig += " {" + "; ".join(
                f"{f['name']}" + (f": {f['annotation']}" if f["annotation"] else "")
                + (f" = {f['default']}" if f["default"] is not None else "") for f in fields) + "}"
        # Behavior of the class itself = declaration + fields (methods are separate chunks).
        shell = ast.ClassDef(name=node.name, bases=node.bases, keywords=node.keywords,
                             body=non_method_body or [ast.Pass()], decorator_list=node.decorator_list,
                             type_params=getattr(node, "type_params", []))
        self._add(node=node, name=node.name, kind=kind, signature=sig,
                  docstring=ast.get_docstring(node) or "", params=fields, behavior_node=shell,
                  extra={"bases": bases})
        self.stack.append(node.name)
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.visit(stmt)
        self.stack.pop()

    # -- module-level config -------------------------------------------------
    def visit_Module(self, node: ast.Module) -> None:
        for stmt in node.body:
            targets: list[ast.expr] = []
            value = None
            if isinstance(stmt, ast.Assign):
                targets, value = stmt.targets, stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                targets, value = [stmt.target], stmt.value
            for t in targets:
                if isinstance(t, ast.Name) and t.id.isupper() and len(t.id) > 1 and value is not None:
                    v = ast.unparse(value)
                    self._add(node=stmt, name=t.id, kind=ChunkKind.CONFIG, signature=f"{t.id} = {v}",
                              docstring="", params=[{"name": t.id, "annotation": None, "default": v,
                                                     "kind": "constant"}])
            if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                self.visit(stmt)
        self._env_vars(node)

    def _env_vars(self, tree: ast.Module) -> None:
        seen: set[str] = set()
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fn = ""
            try:
                fn = ast.unparse(n.func)
            except Exception:  # pragma: no cover
                continue
            if fn.endswith(("getenv", "environ.get")) and n.args and isinstance(n.args[0], ast.Constant):
                key = str(n.args[0].value)
                default = ast.unparse(n.args[1]) if len(n.args) > 1 else None
                if key in seen:
                    continue
                seen.add(key)
                self._add(node=n, name=key, kind=ChunkKind.CONFIG, qualname=f"env:{key}",
                          signature=f"env {key} (default={default})", docstring="",
                          params=[{"name": key, "annotation": "env", "default": default, "kind": "env"}],
                          extra={"env_var": key})


def parse_source(path: str, source: str) -> list[CodeChunk]:
    """Parse one Python file's source into chunks. Syntax errors yield []."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    v = _Visitor(path.replace("\\", "/"), source)
    v.visit(tree)
    return v.chunks


def iter_code_files(root: str | Path, include: Iterable[str] = ("**/*.py",),
                    exclude: Iterable[str] = (), skip_tests: bool = True) -> list[str]:
    root = Path(root)
    excludes = list(DEFAULT_EXCLUDES) + list(exclude)
    out: list[str] = []
    for pattern in include:
        for p in root.glob(pattern):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, e) or fnmatch.fnmatch("/" + rel, e) for e in excludes):
                continue
            if skip_tests and is_test_file(rel):
                continue
            out.append(rel)
    return sorted(set(out))


def parse_codebase(root: str | Path, include: Iterable[str] = ("**/*.py",),
                   exclude: Iterable[str] = ()) -> list[CodeChunk]:
    chunks: list[CodeChunk] = []
    for rel in iter_code_files(root, include, exclude):
        try:
            src = (Path(root) / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        chunks.extend(parse_source(rel, src))
    return chunks


def code_identifiers(chunk: CodeChunk) -> set[str]:
    """Names a doc section could use to refer to this chunk."""
    ids = {chunk.name, chunk.qualname}
    if chunk.kind == ChunkKind.ENDPOINT.value and chunk.extra.get("route"):
        ids.add(chunk.extra["route"])
    if chunk.kind == ChunkKind.CLI_COMMAND.value and chunk.extra.get("command"):
        ids.add(chunk.extra["command"])
    if chunk.extra.get("env_var"):
        ids.add(chunk.extra["env_var"])
    return {i for i in ids if i and len(i) > 2}


__all__ = ["code_identifiers", "is_test_file", "iter_code_files", "parse_codebase", "parse_source"]


if __name__ == "__main__":  # pragma: no cover
    import json
    import sys
    for c in parse_codebase(sys.argv[1] if len(sys.argv) > 1 else os.getcwd()):
        print(json.dumps({"id": c.id, "kind": c.kind, "sig": c.signature}))
