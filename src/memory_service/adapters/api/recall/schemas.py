from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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
