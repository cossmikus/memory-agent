"""MemoryService — the composition root. Wires the domain services together
and exposes the high-level operations the API layer calls.

It owns no I/O; all I/O happens through ports it was constructed with.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from memory_service.config import Settings
from memory_service.core.logging import get_logger
from memory_service.domain.assembler import ContextAssembler
from memory_service.domain.extraction import ExtractionService
from memory_service.domain.models import (
    Memory,
    RecalledContext,
    SearchResult,
    Turn,
)
from memory_service.domain.ports import (
    EmbedderPort,
    ExtractorPort,
    MemoriesRepoPort,
    RerankerPort,
    TurnsRepoPort,
)
from memory_service.domain.recall import RecallService
from memory_service.domain.reconciliation import ReconciliationService

log = get_logger(__name__)


class MemoryService:
    def __init__(
        self,
        turns_repo: TurnsRepoPort,
        memories_repo: MemoriesRepoPort,
        extractor: ExtractorPort,
        embedder: EmbedderPort,
        reranker: RerankerPort | None,
        settings: Settings,
    ) -> None:
        self._turns = turns_repo
        self._memories = memories_repo
        self._embedder = embedder
        self._settings = settings

        self._extraction = ExtractionService(extractor, embedder, settings)
        self._reconciliation = ReconciliationService(memories_repo)
        self._recall = RecallService(memories_repo, embedder, reranker, settings)
        self._assembler = ContextAssembler(snippet_loader=self._turn_snippet)

    # ─── public API ──────────────────────────────────────────────────

    async def ingest_turn(self, turn: Turn) -> str:
        """Persist raw turn → extract → reconcile → return turn id.

        Synchronous: by the time this returns, /recall sees the new memories.
        """
        if not turn.id:
            turn.id = str(uuid.uuid4())
        await self._turns.save_turn(turn)

        if turn.user_id is None:
            # Spec allows user_id=null — store the turn, skip extraction.
            log.info("turn_ingested_anonymous", turn_id=turn.id, session=turn.session_id)
            return turn.id

        candidates, embeddings = await self._extraction.extract(
            turn.messages, turn.user_id
        )
        if not candidates:
            log.info(
                "turn_no_candidates",
                turn_id=turn.id,
                user_id=turn.user_id,
                session=turn.session_id,
            )
            return turn.id

        await self._reconciliation.apply(
            user_id=turn.user_id,
            candidates=candidates,
            embeddings=embeddings,
            source_turn_id=turn.id,
            source_session_id=turn.session_id,
        )
        log.info(
            "turn_ingested",
            turn_id=turn.id,
            user_id=turn.user_id,
            session=turn.session_id,
            candidates=len(candidates),
        )
        return turn.id

    async def recall(
        self,
        user_id: str | None,
        query: str,
        max_tokens: int,
    ) -> RecalledContext:
        if not user_id:
            return RecalledContext(context="", citations=[])

        scored = await self._recall.recall(user_id, query)

        # Noise resistance: if nothing in the user's memory is relevant to the
        # query, return an empty context rather than dumping all known facts.
        # An empty `scored` means hybrid retrieval and multi-hop both failed
        # to anchor anything — the agent should not hallucinate context.
        if not scored:
            return RecalledContext(context="", citations=[])

        all_user_memories = await self._memories.list_user_memories(
            user_id, only_active=True
        )

        return await self._assembler.assemble(
            scored=scored,
            user_facts=all_user_memories,
            max_tokens=max_tokens,
        )

    async def search(
        self,
        user_id: str | None,
        query: str,
        limit: int,
    ) -> list[SearchResult]:
        if not user_id:
            return []
        scored = await self._recall.recall(user_id, query, max_candidates=limit)
        out: list[SearchResult] = []
        for sm in scored[:limit]:
            out.append(
                SearchResult(
                    content=f"{sm.memory.key}: {sm.memory.value}",
                    score=round(sm.score, 4),
                    session_id=sm.memory.source_session_id,
                    timestamp=sm.memory.updated_at,
                    metadata={
                        "type": sm.memory.type.value,
                        "key": sm.memory.key,
                        "confidence": sm.memory.confidence,
                        "active": sm.memory.active,
                        "source_turn_id": sm.memory.source_turn_id,
                    },
                )
            )
        return out

    async def list_user_memories(self, user_id: str) -> list[Memory]:
        return await self._memories.list_user_memories(user_id, only_active=False)

    async def delete_session(self, session_id: str) -> None:
        await self._memories.delete_session(session_id)
        await self._turns.delete_session(session_id)

    async def delete_user(self, user_id: str) -> None:
        await self._memories.delete_user(user_id)
        await self._turns.delete_user(user_id)

    # ─── helpers ─────────────────────────────────────────────────────

    async def _turn_snippet(self, turn_id: str) -> str:
        # The TurnsRepo implementation has get_turn_snippet; we route via the
        # concrete repo when available.
        getter = getattr(self._turns, "get_turn_snippet", None)
        if getter is None:
            turn = await self._turns.get_turn(turn_id)
            if not turn:
                return ""
            return " | ".join(f"{m.role}: {m.content}" for m in turn.messages)[:200]
        return await getter(turn_id, 200)
