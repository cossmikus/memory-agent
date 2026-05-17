"""Extraction service.

Calls the LLM/regex extractor, applies salience filtering, normalizes values,
and computes embeddings for each candidate. Returns ready-to-reconcile
candidates plus their parallel embedding vectors.
"""
from __future__ import annotations

import asyncio

from memory_service.config import Settings
from memory_service.core.logging import get_logger
from memory_service.domain.models import MemoryCandidate, Message
from memory_service.domain.ports import EmbedderPort, ExtractorPort

log = get_logger(__name__)


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


class ExtractionService:
    def __init__(
        self,
        extractor: ExtractorPort,
        embedder: EmbedderPort,
        settings: Settings,
    ) -> None:
        self._extractor = extractor
        self._embedder = embedder
        self._settings = settings

    async def extract(
        self,
        messages: list[Message],
        user_id: str | None,
    ) -> tuple[list[MemoryCandidate], list[list[float]]]:
        candidates = await self._extractor.extract(messages, user_id)

        # Salience filter — noise resistance lever.
        filtered = [c for c in candidates if c.salience >= 0.3]
        dropped = len(candidates) - len(filtered)
        if dropped:
            log.info("extraction_low_salience_dropped", count=dropped, user_id=user_id)

        # Embed all candidate values in one batch.
        if not filtered:
            return [], []
        texts = [self._embed_text(c) for c in filtered]
        embeddings = await self._embedder.embed_batch(texts)

        # Normalize value for storage / FTS.
        for c in filtered:
            c.value = c.value.strip()
        return filtered, embeddings

    @staticmethod
    def _embed_text(c: MemoryCandidate) -> str:
        """Compose a richer string for embedding than `value` alone.

        Embedding `key + value + evidence` gives the vector index more
        semantic signal — "employer Stripe" embeds differently from "Stripe".
        """
        parts = [c.key.replace(":", " ").replace("_", " "), c.value]
        if c.evidence_snippet:
            parts.append(c.evidence_snippet)
        return " | ".join(parts)


def normalize(value: str) -> str:
    return _normalize(value)
