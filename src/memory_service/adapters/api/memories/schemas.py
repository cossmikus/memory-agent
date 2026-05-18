from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MemoryOut(BaseModel):
    id: str
    type: str
    key: str
    value: str
    confidence: float
    source_session: str
    source_turn: str
    created_at: datetime
    updated_at: datetime
    supersedes: str | None
    active: bool
    history: list[dict[str, Any]] = Field(default_factory=list)


class MemoriesResponse(BaseModel):
    memories: list[MemoryOut]
