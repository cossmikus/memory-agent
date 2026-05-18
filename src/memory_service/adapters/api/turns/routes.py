"""POST /turns — ingest a completed conversation turn synchronously."""
from __future__ import annotations

from fastapi import APIRouter, status

from memory_service.adapters.api.turns.schemas import TurnRequest, TurnResponse
from memory_service.deps import AuthDep, ServiceDep
from memory_service.domain.models import Message, Turn
from memory_service.domain.service import MemoryService

router = APIRouter(tags=["turns"])


@router.post("/turns", status_code=status.HTTP_201_CREATED, response_model=TurnResponse)
async def post_turn(
    payload: TurnRequest,
    _: None = AuthDep,
    service: MemoryService = ServiceDep,
) -> TurnResponse:
    turn = Turn(
        id="",
        session_id=payload.session_id,
        user_id=payload.user_id,
        timestamp=payload.timestamp,
        messages=[
            Message(role=m.role, content=m.content, name=m.name, position=i)
            for i, m in enumerate(payload.messages)
        ],
        metadata=payload.metadata,
    )
    turn_id = await service.ingest_turn(turn)
    return TurnResponse(id=turn_id)
