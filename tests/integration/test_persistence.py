"""Restart persistence — write turns, tear down the app, bring it back up
with the same DB file, and verify memories survive."""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from memory_service.config import reset_settings_cache


@pytest.mark.asyncio
async def test_data_survives_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "persist.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reset_settings_cache()

    from memory_service.main import create_app

    # First boot — ingest.
    app1 = create_app()
    async with app1.router.lifespan_context(app1):
        async with AsyncClient(transport=ASGITransport(app=app1), base_url="http://test") as ac:
            r = await ac.post(
                "/turns",
                json={
                    "session_id": "s1",
                    "user_id": "alice",
                    "messages": [
                        {"role": "user", "content": "I moved to Berlin last month."}
                    ],
                    "timestamp": "2025-03-15T10:30:00Z",
                    "metadata": {},
                },
            )
            assert r.status_code == 201

    # Second boot — same DB file, fresh app instance.
    reset_settings_cache()
    app2 = create_app()
    async with app2.router.lifespan_context(app2):
        async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as ac:
            r = await ac.get("/users/alice/memories")
            assert r.status_code == 200
            mems = r.json()["memories"]
            keys = {m["key"] for m in mems}
            assert "location_city" in keys, f"missing location memory, got: {keys}"

            # Note: this test runs without an OpenAI key (per conftest), so
            # the embedder is null and recall has to lean on FTS5 alone.
            # We probe with the literal value to confirm the lexical path
            # works after restart — the LLM-driven natural-language probe
            # is exercised in scripts/run_eval.py end-to-end instead.
            rec = await ac.post(
                "/recall",
                json={
                    "query": "Berlin location",
                    "session_id": "s2",
                    "user_id": "alice",
                    "max_tokens": 256,
                },
            )
            assert rec.status_code == 200
            assert "Berlin" in rec.json()["context"]
