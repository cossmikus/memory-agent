"""DELETE /sessions/{id}, DELETE /users/{id} — cleanup endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Path, Response, status

from memory_service.deps import AuthDep, ServiceDep
from memory_service.domain.service import MemoryService

router = APIRouter(tags=["admin"])


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str = Path(min_length=1, max_length=200),
    _: None = AuthDep,
    service: MemoryService = ServiceDep,
) -> Response:
    await service.delete_session(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str = Path(min_length=1, max_length=200),
    _: None = AuthDep,
    service: MemoryService = ServiceDep,
) -> Response:
    await service.delete_user(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
