"""Core data structures shared across the pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ChunkKind(str, Enum):
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    ENDPOINT = "endpoint"
    CLI_COMMAND = "cli_command"
    CONFIG = "config"


@dataclass
class CodeChunk:
    """A semantic unit of code with a stable identifier (``path::qualname``)."""

    id: str
    path: str
    name: str
    qualname: str
    kind: str
    signature: str
    docstring: str
    source: str
    start_line: int
    end_line: int
    # Hash of the signature only — changes when the public API changes.
    signature_hash: str
    # Hash of the AST with docstrings stripped — changes when behavior changes,
    # but NOT for comment / whitespace / docstring-only edits.
    behavior_hash: str
    params: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CodeChunk:
        return cls(**d)

    def embedding_text(self) -> str:
        return f"{self.kind} {self.qualname}\n{self.signature}\n{self.docstring}".strip()


@dataclass
class DocSection:
    """A markdown section identified by file + heading path."""

    id: str
    path: str
    heading: str
    heading_path: str
    level: int
    content: str
    start_line: int
    end_line: int
    references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DocSection:
        return cls(**d)

    def embedding_text(self) -> str:
        return f"{self.heading_path}\n{self.content}"


@dataclass
class Link:
    doc_id: str
    code_id: str
    method: str  # "reference" | "embedding"
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChangeType(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    SIGNATURE = "signature_changed"
    BEHAVIOR = "behavior_changed"
    DOCSTRING = "docstring_only"
    COSMETIC = "cosmetic"  # comments / whitespace — AST identical


MEANINGFUL_CHANGES = {
    ChangeType.ADDED,
    ChangeType.REMOVED,
    ChangeType.SIGNATURE,
    ChangeType.BEHAVIOR,
}


@dataclass
class CodeChange:
    chunk_id: str
    change_type: str
    old: CodeChunk | None
    new: CodeChunk | None

    @property
    def name(self) -> str:
        c = self.new or self.old
        return c.qualname if c else self.chunk_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "change_type": self.change_type,
            "old": self.old.to_dict() if self.old else None,
            "new": self.new.to_dict() if self.new else None,
        }


@dataclass
class Verdict:
    """Result of the staleness verification for one doc section."""

    doc_id: str
    stale: bool
    explanation: str
    issues: list[str] = field(default_factory=list)
    confidence: float = 0.0
    related_changes: list[str] = field(default_factory=list)


@dataclass
class Correction:
    doc_id: str
    path: str
    heading_path: str
    original: str
    corrected: str
    mode: str  # "auto_fix" | "review"
    confidence: float
    validation_passed: bool
    validation_notes: str
    explanation: str
    start_line: int = 0
    end_line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
