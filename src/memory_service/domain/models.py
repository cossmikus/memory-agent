"""Internal domain models. Pure Python — no DB, no HTTP, no LLM imports."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MemoryType(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    OPINION = "opinion"
    EVENT = "event"
    CORRECTION = "correction"


@dataclass(slots=True)
class Message:
    role: str  # "user" | "assistant" | "tool"
    content: str
    name: str | None = None
    position: int = 0


@dataclass(slots=True)
class Turn:
    id: str
    session_id: str
    user_id: str | None
    timestamp: datetime
    messages: list[Message] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Triple:
    """RDF-style triple. Used for multi-hop traversal."""

    subject: str
    predicate: str
    object: str
    source_memory_id: str | None = None


@dataclass(slots=True)
class Memory:
    id: str
    user_id: str
    type: MemoryType
    key: str
    value: str
    value_normalized: str
    confidence: float
    salience: float
    source_turn_id: str
    source_session_id: str
    created_at: datetime
    updated_at: datetime
    supersedes: str | None = None
    active: bool = True
    history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryCandidate:
    """Output of the extractor — not yet persisted, not yet reconciled."""

    type: MemoryType
    key: str
    value: str
    confidence: float
    salience: float
    is_implicit: bool = False
    evidence_snippet: str = ""
    triples: list[Triple] = field(default_factory=list)


@dataclass(slots=True)
class Citation:
    turn_id: str
    score: float
    snippet: str


@dataclass(slots=True)
class RecalledContext:
    context: str
    citations: list[Citation] = field(default_factory=list)


@dataclass(slots=True)
class SearchResult:
    content: str
    score: float
    session_id: str
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScoredMemory:
    """Memory with a fused retrieval score; carries provenance for citations."""

    memory: Memory
    score: float
    source: str = "hybrid"
