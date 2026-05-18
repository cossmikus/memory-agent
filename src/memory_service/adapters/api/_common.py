"""Shared Pydantic DTOs used by more than one endpoint."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class MessageIn(BaseModel):
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    name: str | None = None
