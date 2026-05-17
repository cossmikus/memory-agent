"""GET /health — liveness + readiness probe."""
from __future__ import annotations

from fastapi import APIRouter, Request

from memory_service import __version__
from memory_service.api.schemas import HealthResponse
from memory_service.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings = get_settings()
    state = request.app.state
    return HealthResponse(
        status="ok" if getattr(state, "ready", False) else "starting",
        embedding_available=settings.has_openai,
        llm_available=settings.has_openai,
        version=__version__,
    )
