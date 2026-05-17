"""Shared pytest fixtures.

We patch settings to use a temp DB, disable real OpenAI calls (use regex+null
adapters), and yield an httpx AsyncClient bound to the FastAPI app via ASGI.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from memory_service.config import reset_settings_cache


@pytest.fixture(autouse=True)
def _env_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets a clean SQLite file and no OpenAI key (regex fallback)."""
    db_path = tmp_path / "memory.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    reset_settings_cache()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    # Import here so env vars set by _env_isolation apply.
    from memory_service.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            yield ac
