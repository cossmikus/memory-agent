from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    embedding_available: bool
    llm_available: bool
    version: str
