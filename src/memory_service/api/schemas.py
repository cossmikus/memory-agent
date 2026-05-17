"""Pydantic request/response DTOs at the HTTP boundary."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MessageIn(BaseModel):
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    name: str | None = None


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str = Field(min_length=1, max_length=200)
    user_id: str | None = Field(default=None, max_length=200)
    messages: list[MessageIn] = Field(min_length=1, max_length=200)
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class TurnResponse(BaseModel):
    id: str


class RecallRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(min_length=1, max_length=200)
    user_id: str | None = Field(default=None, max_length=200)
    max_tokens: int = Field(default=1024, ge=16, le=8192)


class CitationOut(BaseModel):
    turn_id: str
    score: float
    snippet: str


class RecallResponse(BaseModel):
    context: str
    citations: list[CitationOut]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=200)
    user_id: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=10, ge=1, le=100)


class SearchResultOut(BaseModel):
    content: str
    score: float
    session_id: str
    timestamp: datetime
    metadata: dict[str, Any]


class SearchResponse(BaseModel):
    results: list[SearchResultOut]


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


class HealthResponse(BaseModel):
    status: str
    embedding_available: bool
    llm_available: bool
    version: str
