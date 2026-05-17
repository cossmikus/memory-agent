"""Recall service — hybrid retrieval, RRF fusion, multi-hop expansion, optional rerank.

The pipeline:
    query
      │
      ├── embed (OpenAI)                  ─┐
      ├── BM25 search via FTS5            ─┤  (asyncio.gather, parallel)
      └── keyword LIKE backstop           ─┘
      ▼
    vector search via sqlite-vec (uses embedding)
      ▼
    fuse with reciprocal rank fusion
      ▼
    if first-pass refers to an entity that doesn't directly answer,
    expand via triple traversal
      ▼
    optional LLM rerank (feature-flagged)
      ▼
    return scored memories
"""
from __future__ import annotations

import asyncio

from memory_service.config import Settings
from memory_service.core.logging import get_logger
from memory_service.domain.models import Memory, ScoredMemory, Triple
from memory_service.domain.ports import (
    EmbedderPort,
    MemoriesRepoPort,
    RerankerPort,
)

log = get_logger(__name__)


class RecallService:
    def __init__(
        self,
        memories_repo: MemoriesRepoPort,
        embedder: EmbedderPort,
        reranker: RerankerPort | None,
        settings: Settings,
    ) -> None:
        self._repo = memories_repo
        self._embedder = embedder
        self._reranker = reranker
        self._settings = settings

    async def recall(
        self,
        user_id: str,
        query: str,
        max_candidates: int = 20,
    ) -> list[ScoredMemory]:
        if not query.strip():
            return []

        # Phase 1: parallel retrieval.
        embed_task = asyncio.create_task(self._embedder.embed(query))
        fts_task = asyncio.create_task(
            self._repo.search_fts(user_id, query, self._settings.bm25_top_k)
        )
        kw_task = asyncio.create_task(
            self._repo.search_keyword(user_id, query, 10)
        )

        query_embedding = await embed_task
        vec_hits_raw = await self._repo.search_vector(
            user_id, query_embedding, self._settings.vector_top_k
        )
        # Noise resistance: drop vector hits below the similarity floor.
        vec_hits = [
            (m, s) for m, s in vec_hits_raw if s >= self._settings.vector_score_floor
        ]
        fts_hits = await fts_task
        kw_hits = await kw_task

        # Phase 2: RRF fusion.
        fused = _rrf_fuse(
            [
                (fts_hits, "bm25"),
                ([(m, s) for m, s in vec_hits], "vector"),
                (kw_hits, "keyword"),
            ],
            k=self._settings.rrf_k,
        )

        # Phase 3: multi-hop expansion if first-pass found entities but not answers.
        expanded = await self._multihop_expand(user_id, query, fused, max_candidates)

        # Phase 4: optional LLM rerank of the top window.
        if self._reranker and self._settings.reranker_enabled:
            window = expanded[: max(10, max_candidates)]
            pairs = [(sm.memory.id, _content_for_rerank(sm.memory)) for sm in window]
            try:
                reranked = await self._reranker.rerank(query, pairs, len(pairs))
                rerank_map = {mid: score for mid, score in reranked}
                for sm in window:
                    if sm.memory.id in rerank_map:
                        sm.score = 0.5 * sm.score + 0.5 * rerank_map[sm.memory.id]
                window.sort(key=lambda sm: sm.score, reverse=True)
                expanded = window + expanded[len(window) :]
            except Exception as exc:  # pragma: no cover
                log.warning("rerank_skipped", error=str(exc))

        # Phase 5: noise floor.
        return [sm for sm in expanded if sm.score >= self._settings.min_recall_score][
            :max_candidates
        ]

    async def _multihop_expand(
        self,
        user_id: str,
        query: str,
        fused: list[ScoredMemory],
        max_candidates: int,
    ) -> list[ScoredMemory]:
        """If the query mentions a named entity (e.g., "Biscuit") and the
        first-pass hits are about that entity but don't answer the query
        (the query asks "where", first-pass returns "has pet"), traverse
        triples to find facts about the related subject."""
        if not fused:
            return fused

        # Cheap entity heuristic: capitalized words 3+ chars not at sentence start.
        candidates = _extract_entity_candidates(query)
        if not candidates:
            return fused

        existing_ids = {sm.memory.id for sm in fused}
        appended: list[ScoredMemory] = []

        # Build an id→ScoredMemory index for O(1) boost lookups.
        by_id = {sm.memory.id: sm for sm in fused}
        MULTIHOP_BOOST = 0.55

        for entity in candidates:
            # Find triples whose OBJECT matches the entity → resolve subject(s).
            tris = await self._repo.find_triples_by_object(user_id, entity)
            subjects: set[str] = {t.subject for t in tris if t.subject}

            # Also try subject-side (entity might be the subject already).
            if not subjects:
                subj_tris = await self._repo.find_triples_by_subject(user_id, entity)
                if subj_tris:
                    subjects.add(entity)

            for subj in subjects:
                # Fetch all memories of that subject (typically "user").
                if subj.lower() == "user":
                    user_mems = await self._repo.list_user_memories(
                        user_id, only_active=True
                    )
                    for mem in user_mems:
                        if mem.id in by_id:
                            # Already in fused — BOOST its score so it survives
                            # the min_recall_score filter.
                            by_id[mem.id].score += MULTIHOP_BOOST
                            by_id[mem.id].source += "+multihop"
                        elif mem.id not in existing_ids:
                            sm = ScoredMemory(
                                memory=mem, score=MULTIHOP_BOOST, source="multihop"
                            )
                            appended.append(sm)
                            by_id[mem.id] = sm
                            existing_ids.add(mem.id)
                            if len(appended) + len(fused) >= max_candidates:
                                break

        combined = fused + appended
        combined.sort(key=lambda sm: sm.score, reverse=True)
        return combined


def _rrf_fuse(
    rankings: list[tuple[list[tuple[Memory, float]], str]],
    k: int = 60,
) -> list[ScoredMemory]:
    """Reciprocal Rank Fusion. Score(m) = sum over rankings of 1 / (k + rank)."""
    accum: dict[str, ScoredMemory] = {}
    for hits, source in rankings:
        for rank, (mem, _) in enumerate(hits, start=1):
            contribution = 1.0 / (k + rank)
            if mem.id in accum:
                accum[mem.id].score += contribution
                accum[mem.id].source = f"{accum[mem.id].source}+{source}"
            else:
                accum[mem.id] = ScoredMemory(memory=mem, score=contribution, source=source)
    fused = list(accum.values())
    fused.sort(key=lambda sm: sm.score, reverse=True)
    return fused


def _content_for_rerank(memory: Memory) -> str:
    return f"{memory.key}: {memory.value}"


def _extract_entity_candidates(query: str) -> list[str]:
    """Heuristic entity extractor: pulls Title-Case words that look like names.

    We deliberately don't NER here — the goal is to enable multi-hop on
    explicit names like "Biscuit" or "Stripe", not to be a real linguistic
    parser. Lowercased tokens are skipped.
    """
    words = query.replace("?", " ").replace(".", " ").split()
    out: list[str] = []
    for i, w in enumerate(words):
        if len(w) < 3:
            continue
        stripped = w.strip(",.'\"!?")
        # Capitalized, not at the start unless it's clearly a name.
        if stripped[:1].isupper() and stripped[1:].islower():
            # Skip pronouns/common starters that happen to be capitalized.
            if stripped.lower() in {"what", "where", "who", "how", "why", "when", "the", "user"}:
                continue
            out.append(stripped)
        elif "named" in (words[i - 1].lower() if i else ""):
            out.append(stripped)
    return out
