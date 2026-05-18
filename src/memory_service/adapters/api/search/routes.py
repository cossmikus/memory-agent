"""POST /search — structured search results for agent tool calls."""
from __future__ import annotations

from fastapi import APIRouter

from memory_service.adapters.api.search.schemas import (
    SearchRequest,
    SearchResponse,
    SearchResultOut,
)
from memory_service.deps import AuthDep, ServiceDep
from memory_service.domain.service import MemoryService

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def post_search(
    payload: SearchRequest,
    _: None = AuthDep,
    service: MemoryService = ServiceDep,
) -> SearchResponse:
    results = await service.search(
        user_id=payload.user_id,
        query=payload.query,
        limit=payload.limit,
    )
    return SearchResponse(
        results=[
            SearchResultOut(
                content=r.content,
                score=r.score,
                session_id=r.session_id,
                timestamp=r.timestamp,
                metadata=r.metadata,
            )
            for r in results
        ]
    )
