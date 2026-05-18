"""Aggregator — pulls every resource's router and mounts them on the FastAPI app
at the root path (the spec contract is unversioned; no /v1 prefix)."""
from __future__ import annotations

from fastapi import FastAPI

from memory_service.adapters.api.admin import router as admin_router
from memory_service.adapters.api.health import router as health_router
from memory_service.adapters.api.memories import router as memories_router
from memory_service.adapters.api.recall import router as recall_router
from memory_service.adapters.api.search import router as search_router
from memory_service.adapters.api.turns import router as turns_router


def register_routes(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(turns_router)
    app.include_router(recall_router)
    app.include_router(search_router)
    app.include_router(memories_router)
    app.include_router(admin_router)
