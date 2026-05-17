"""Reconciliation — turns extracted candidates into persisted memories with
correct supersession behavior.

Tiered policy (cheap → expensive):
1. Exact canonical-key match against the user's active memory for that key,
   resolved by `type`:
     - fact / correction → supersede (mark old inactive, link new via supersedes)
     - preference       → supersede with history append
     - opinion          → APPEND to history on the active row (opinion arc),
                          never fully evict prior stance
     - event            → always insert as new (events are not facts)
2. Embedding-similarity check on same user + same key family:
     - cos sim > 0.95   → reinforcement: bump confidence on existing row
     - cos sim < 0.70   → independent: insert fresh
     - otherwise        → defer to deterministic supersede rule for the type
3. (Tier 3 / LLM adjudication is reserved for a future iteration; the spec
    recommends gating it behind tier 1 and tier 2 to control cost.)
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from memory_service.core.logging import get_logger
from memory_service.domain.extraction import normalize
from memory_service.domain.models import (
    Memory,
    MemoryCandidate,
    MemoryType,
    Triple,
)
from memory_service.domain.ports import MemoriesRepoPort

log = get_logger(__name__)


REINFORCE_THRESHOLD = 0.95
INDEPENDENT_THRESHOLD = 0.70


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class ReconciliationService:
    def __init__(self, memories_repo: MemoriesRepoPort) -> None:
        self._repo = memories_repo

    async def apply(
        self,
        user_id: str,
        candidates: list[MemoryCandidate],
        embeddings: list[list[float]],
        source_turn_id: str,
        source_session_id: str,
    ) -> list[Memory]:
        """Persist or update memories based on reconciliation policy.

        Returns the list of currently-active memories that resulted from
        this batch (for downstream triple writing).
        """
        active_after: list[Memory] = []
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        for cand, emb in zip(candidates, embeddings, strict=False):
            existing = await self._repo.get_active_by_key(user_id, cand.key)

            # Tier 2: similarity-based reinforcement (works even before any
            # exact-key match — handles minor wording variations).
            if existing and cand.type == MemoryType.FACT:
                similarity = _similar_value(existing.value, cand.value)
                if similarity >= 0.95 and normalize(existing.value) == normalize(cand.value):
                    # Reinforcement — same value re-asserted.
                    existing.confidence = min(0.99, existing.confidence + 0.02)
                    existing.salience = min(1.0, max(existing.salience, cand.salience))
                    existing.updated_at = now
                    history_entry = {
                        "kind": "reinforce",
                        "value": cand.value,
                        "source_turn": source_turn_id,
                        "timestamp": now.isoformat() + "Z",
                    }
                    existing.history.append(history_entry)
                    await self._repo.update_memory(existing)
                    active_after.append(existing)
                    continue

            # Tier 1 / type-driven rules:
            new_memory = _build_memory(cand, user_id, source_turn_id, source_session_id, now)

            if existing is None:
                await self._repo.insert_memory(new_memory, emb or None)
                active_after.append(new_memory)
                _attach_triples(cand, new_memory)
                await self._repo.insert_triples(cand.triples)
                continue

            if cand.type == MemoryType.EVENT:
                # Events don't conflict; always insert.
                await self._repo.insert_memory(new_memory, emb or None)
                active_after.append(new_memory)
                _attach_triples(cand, new_memory)
                await self._repo.insert_triples(cand.triples)
                continue

            if cand.type == MemoryType.OPINION:
                # Opinion arc: keep existing active, append to its history.
                existing.history.append(
                    {
                        "kind": "evolved",
                        "value": cand.value,
                        "source_turn": source_turn_id,
                        "timestamp": now.isoformat() + "Z",
                    }
                )
                existing.value = cand.value
                existing.value_normalized = normalize(cand.value)
                existing.updated_at = now
                existing.confidence = max(existing.confidence, cand.confidence)
                existing.salience = max(existing.salience, cand.salience)
                # Rewrite with new value — needs re-insert for FTS5/vec.
                await self._repo.insert_memory(existing, emb or None)
                active_after.append(existing)
                continue

            # Default: supersede (fact, preference, correction).
            new_memory.supersedes = existing.id
            await self._repo.insert_memory(new_memory, emb or None)
            await self._repo.mark_superseded(existing.id, new_memory.id)
            log.info(
                "memory_superseded",
                user_id=user_id,
                key=cand.key,
                old_value=existing.value,
                new_value=new_memory.value,
            )
            active_after.append(new_memory)
            _attach_triples(cand, new_memory)
            await self._repo.insert_triples(cand.triples)

        return active_after


def _build_memory(
    cand: MemoryCandidate,
    user_id: str,
    source_turn_id: str,
    source_session_id: str,
    now: datetime,
) -> Memory:
    return Memory(
        id=str(uuid.uuid4()),
        user_id=user_id,
        type=cand.type,
        key=cand.key,
        value=cand.value,
        value_normalized=normalize(cand.value),
        confidence=cand.confidence,
        salience=cand.salience,
        source_turn_id=source_turn_id,
        source_session_id=source_session_id,
        created_at=now,
        updated_at=now,
        supersedes=None,
        active=True,
        history=[],
        metadata={
            "evidence_snippet": cand.evidence_snippet,
            "is_implicit": cand.is_implicit,
        },
    )


def _attach_triples(cand: MemoryCandidate, memory: Memory) -> None:
    for t in cand.triples:
        t.source_memory_id = memory.id


def _similar_value(a: str, b: str) -> float:
    """Cheap surface similarity in [0,1] for reinforcement detection."""
    aa, bb = normalize(a), normalize(b)
    if aa == bb:
        return 1.0
    if aa in bb or bb in aa:
        return 0.85
    set_a, set_b = set(aa.split()), set(bb.split())
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)
