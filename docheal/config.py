"""Runtime settings, read from CLI flags, ``DOCHEAL_*`` env vars, or Action inputs."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    """Look up DOCHEAL_<NAME>, then the raw Action input form INPUT_<NAME-WITH-HYPHENS>."""
    for key in (f"DOCHEAL_{name}", f"INPUT_{name.replace('_', '-')}", f"INPUT_{name}"):
        v = os.environ.get(key)
        if v is not None and v != "":
            return v
    return default


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _list(v: str | None, default: list[str]) -> list[str]:
    if not v:
        return default
    return [x.strip() for x in v.replace("\n", ",").split(",") if x.strip()]


@dataclass
class Settings:
    repo_path: str = "."
    base: str = "origin/main"
    head: str = "WORKTREE"
    llm_provider: str = "auto"  # auto | anthropic | openai | rules
    model: str | None = None
    embedding_provider: str = "auto"  # auto | openai | hashing
    confidence_threshold: float = 0.8
    similarity_threshold: float | None = None
    auto_merge: bool = False
    mode: str = "auto"  # auto | comment-only | fix-pr
    max_sections: int = 40
    fail_on_stale: bool = False
    code_globs: list[str] = field(default_factory=lambda: ["**/*.py"])
    doc_globs: list[str] = field(default_factory=lambda: ["**/*.md"])
    exclude: list[str] = field(default_factory=list)
    index_path: str = ".docheal/index.json"
    write_index: bool = True

    @classmethod
    def from_env(cls, **overrides) -> Settings:
        s = cls(
            repo_path=_env("REPO_PATH", os.environ.get("GITHUB_WORKSPACE", ".")),
            base=_env("BASE", "origin/main"),
            head=_env("HEAD", "WORKTREE"),
            llm_provider=_env("LLM_PROVIDER", "auto"),
            model=_env("MODEL"),
            embedding_provider=_env("EMBEDDING_PROVIDER", "auto"),
            confidence_threshold=float(_env("CONFIDENCE_THRESHOLD", "0.8")),
            similarity_threshold=float(v) if (v := _env("SIMILARITY_THRESHOLD")) else None,
            auto_merge=_bool(_env("AUTO_MERGE")),
            mode=_env("MODE", "auto"),
            max_sections=int(_env("MAX_SECTIONS", "40")),
            fail_on_stale=_bool(_env("FAIL_ON_STALE")),
            code_globs=_list(_env("CODE_PATHS"), ["**/*.py"]),
            doc_globs=_list(_env("DOC_PATHS"), ["**/*.md"]),
            exclude=_list(_env("EXCLUDE"), []),
            index_path=_env("INDEX_PATH", ".docheal/index.json"),
            write_index=_bool(_env("WRITE_INDEX"), True),
        )
        for k, v in overrides.items():
            if v is not None:
                setattr(s, k, v)
        return s
