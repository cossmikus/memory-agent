"""GET /users/{user_id}/memories — structured memory inspection endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Path

from memory_service.adapters.api.memories.schemas import MemoriesResponse, MemoryOut
from memory_service.deps import AuthDep, ServiceDep
from memory_service.domain.service import MemoryService

router = APIRouter(tags=["memories"])


@router.get("/users/{user_id}/memories", response_model=MemoriesResponse)
async def get_user_memories(
    user_id: str = Path(min_length=1, max_length=200),
    _: None = AuthDep,
    service: MemoryService = ServiceDep,
) -> MemoriesResponse:
    memories = await service.list_user_memories(user_id)
    return MemoriesResponse(
        memories=[
            MemoryOut(
                id=m.id,
                type=m.type.value,
                key=m.key,
                value=m.value,
                confidence=m.confidence,
                source_session=m.source_session_id,
                source_turn=m.source_turn_id,
                created_at=m.created_at,
                updated_at=m.updated_at,
                supersedes=m.supersedes,
                active=m.active,
                history=m.history,
            )
            for m in memories
        ]
    )
