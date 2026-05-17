"""FastAPI dependency providers.

The MemoryService is created during lifespan and stored on app.state; the
get_service dep just hands it out to route handlers.
"""
from __future__ import annotations

from fastapi import Depends, Request

from memory_service.core.auth import require_auth
from memory_service.domain.service import MemoryService


def get_service(request: Request) -> MemoryService:
    svc = getattr(request.app.state, "service", None)
    if svc is None:
        raise RuntimeError("MemoryService not initialized")
    return svc


AuthDep = Depends(require_auth)
ServiceDep = Depends(get_service)
