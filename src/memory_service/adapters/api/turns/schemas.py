from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from memory_service.adapters.api._common import MessageIn


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str = Field(min_length=1, max_length=200)
    user_id: str | None = Field(default=None, max_length=200)
    messages: list[MessageIn] = Field(min_length=1, max_length=200)
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class TurnResponse(BaseModel):
    id: str
