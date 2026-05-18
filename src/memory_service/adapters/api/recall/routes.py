"""POST /recall — return formatted prose context for the agent's next turn."""
from __future__ import annotations

from fastapi import APIRouter

from memory_service.adapters.api.recall.schemas import (
    CitationOut,
    RecallRequest,
    RecallResponse,
)
from memory_service.deps import AuthDep, ServiceDep
from memory_service.domain.service import MemoryService

router = APIRouter(tags=["recall"])


@router.post("/recall", response_model=RecallResponse)
async def post_recall(
    payload: RecallRequest,
    _: None = AuthDep,
    service: MemoryService = ServiceDep,
) -> RecallResponse:
    result = await service.recall(
        user_id=payload.user_id,
        query=payload.query,
        max_tokens=payload.max_tokens,
    )
    return RecallResponse(
        context=result.context,
        citations=[
            CitationOut(turn_id=c.turn_id, score=c.score, snippet=c.snippet)
            for c in result.citations
        ],
    )
