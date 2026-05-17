"""Contract tests — exercise every endpoint with the exact shapes from §3 of the spec."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "starting"}
    assert "version" in body


@pytest.mark.asyncio
async def test_turn_roundtrip_with_recall(client: AsyncClient) -> None:
    payload = {
        "session_id": "s1",
        "user_id": "u1",
        "messages": [
            {
                "role": "user",
                "content": "I just moved to Berlin from NYC last month. Loving it so far.",
            },
            {
                "role": "assistant",
                "content": "That sounds exciting! How are you settling in?",
            },
        ],
        "timestamp": "2025-03-15T10:30:00Z",
        "metadata": {},
    }
    resp = await client.post("/turns", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "id" in body and isinstance(body["id"], str)

    # /recall on a *different* session for the same user must surface Berlin.
    rresp = await client.post(
        "/recall",
        json={
            "query": "Where does this user live?",
            "session_id": "s2",
            "user_id": "u1",
            "max_tokens": 512,
        },
    )
    assert rresp.status_code == 200
    rbody = rresp.json()
    assert "context" in rbody
    assert "citations" in rbody
    # The regex extractor recognizes "moved to <City>". Berlin should appear.
    assert "Berlin" in rbody["context"]


@pytest.mark.asyncio
async def test_search_returns_structured(client: AsyncClient) -> None:
    await client.post(
        "/turns",
        json={
            "session_id": "s1",
            "user_id": "u1",
            "messages": [{"role": "user", "content": "I moved to Berlin last week."}],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )
    resp = await client.post(
        "/search",
        json={"query": "Berlin", "user_id": "u1", "limit": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "results" in body
    if body["results"]:
        r = body["results"][0]
        assert "content" in r and "score" in r and "metadata" in r


@pytest.mark.asyncio
async def test_user_memories_inspection(client: AsyncClient) -> None:
    await client.post(
        "/turns",
        json={
            "session_id": "s1",
            "user_id": "u1",
            "messages": [
                {"role": "user", "content": "I have a dog named Biscuit and I live in Berlin."}
            ],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )
    resp = await client.get("/users/u1/memories")
    assert resp.status_code == 200
    body = resp.json()
    assert "memories" in body
    # Regex extractor catches "I live in" → location_city, "I have a dog named" → pet
    assert len(body["memories"]) > 0
    for m in body["memories"]:
        assert {"id", "type", "key", "value", "active", "created_at"} <= set(m.keys())


@pytest.mark.asyncio
async def test_delete_session(client: AsyncClient) -> None:
    await client.post(
        "/turns",
        json={
            "session_id": "s1",
            "user_id": "u1",
            "messages": [{"role": "user", "content": "I live in Berlin."}],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )
    resp = await client.delete("/sessions/s1")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_user(client: AsyncClient) -> None:
    await client.post(
        "/turns",
        json={
            "session_id": "s1",
            "user_id": "u1",
            "messages": [{"role": "user", "content": "I live in Berlin."}],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )
    resp = await client.delete("/users/u1")
    assert resp.status_code == 204

    # Memories are gone.
    mresp = await client.get("/users/u1/memories")
    assert mresp.status_code == 200
    assert mresp.json() == {"memories": []}


@pytest.mark.asyncio
async def test_recall_cold_session_returns_empty(client: AsyncClient) -> None:
    resp = await client.post(
        "/recall",
        json={
            "query": "anything",
            "session_id": "fresh",
            "user_id": "never-seen",
            "max_tokens": 256,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"context": "", "citations": []}


@pytest.mark.asyncio
async def test_malformed_input_returns_4xx(client: AsyncClient) -> None:
    # Missing required fields.
    resp = await client.post("/turns", json={"session_id": "s1"})
    assert resp.status_code == 422

    # Invalid JSON.
    resp = await client.post(
        "/turns",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code in (400, 422)

    # Unicode handling.
    resp = await client.post(
        "/turns",
        json={
            "session_id": "s1",
            "user_id": "u1",
            "messages": [{"role": "user", "content": "👋 Привет 你好 🚀"}],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )
    assert resp.status_code == 201
