"""Ports — Protocol interfaces that the domain depends on.

Adapters in src/memory_service/adapters/ implement these. The domain layer
imports only from this file and models.py — never from adapters.
"""
from __future__ import annotations

from typing import Protocol

from memory_service.domain.models import (
    Memory,
    MemoryCandidate,
    Message,
    Triple,
    Turn,
)


class EmbedderPort(Protocol):
    async def embed(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def dim(self) -> int: ...


class ExtractorPort(Protocol):
    """Turns raw messages into structured memory candidates."""

    async def extract(
        self,
        messages: list[Message],
        user_id: str | None,
    ) -> list[MemoryCandidate]: ...


class RerankerPort(Protocol):
    """Optional. Reorders candidates by query relevance."""

    async def rerank(
        self,
        query: str,
        candidates: list[tuple[str, str]],  # (id, content)
        top_k: int,
    ) -> list[tuple[str, float]]: ...


class TurnsRepoPort(Protocol):
    async def save_turn(self, turn: Turn) -> None: ...

    async def get_turn(self, turn_id: str) -> Turn | None: ...

    async def delete_session(self, session_id: str) -> None: ...

    async def delete_user(self, user_id: str) -> None: ...


class MemoriesRepoPort(Protocol):
    async def insert_memory(self, memory: Memory, embedding: list[float] | None) -> None: ...

    async def update_memory(self, memory: Memory) -> None: ...

    async def mark_superseded(self, old_id: str, new_id: str) -> None: ...

    async def get_active_by_key(self, user_id: str, key: str) -> Memory | None: ...

    async def list_user_memories(
        self,
        user_id: str,
        only_active: bool = False,
    ) -> list[Memory]: ...

    async def insert_triples(self, triples: list[Triple]) -> None: ...

    async def search_fts(
        self,
        user_id: str,
        query: str,
        limit: int,
    ) -> list[tuple[Memory, float]]: ...

    async def search_vector(
        self,
        user_id: str,
        embedding: list[float],
        limit: int,
    ) -> list[tuple[Memory, float]]: ...

    async def search_keyword(
        self,
        user_id: str,
        query: str,
        limit: int,
    ) -> list[tuple[Memory, float]]: ...

    async def find_triples_by_object(
        self,
        user_id: str,
        object_value: str,
    ) -> list[Triple]: ...

    async def find_triples_by_subject(
        self,
        user_id: str,
        subject: str,
    ) -> list[Triple]: ...

    async def delete_user(self, user_id: str) -> None: ...

    async def delete_session(self, session_id: str) -> None: ...
