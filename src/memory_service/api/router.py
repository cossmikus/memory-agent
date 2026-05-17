"""Aggregates all routers and mounts them on the FastAPI app at the root path
(no /v1 prefix — the spec contract is unversioned)."""
from __future__ import annotations

from fastapi import FastAPI

from memory_service.api import admin, health, memories, recall, search, turns


def register_routes(app: FastAPI) -> None:
    app.include_router(health.router)
    app.include_router(turns.router)
    app.include_router(recall.router)
    app.include_router(search.router)
    app.include_router(memories.router)
    app.include_router(admin.router)
