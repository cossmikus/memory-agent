"""Robustness: malformed input must 4xx, not crash."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_missing_required(client: AsyncClient) -> None:
    r = await client.post("/turns", json={"session_id": "s1"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_invalid_role(client: AsyncClient) -> None:
    r = await client.post(
        "/turns",
        json={
            "session_id": "s1",
            "user_id": "u1",
            "messages": [{"role": "spaceship", "content": "hi"}],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_invalid_timestamp(client: AsyncClient) -> None:
    r = await client.post(
        "/turns",
        json={
            "session_id": "s1",
            "user_id": "u1",
            "messages": [{"role": "user", "content": "hi"}],
            "timestamp": "not-a-date",
            "metadata": {},
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_oversize_query_rejected(client: AsyncClient) -> None:
    huge = "x" * 5000
    r = await client.post(
        "/recall",
        json={
            "query": huge,
            "session_id": "s1",
            "user_id": "u1",
            "max_tokens": 256,
        },
    )
    assert r.status_code == 422  # exceeds query max_length


@pytest.mark.asyncio
async def test_unicode_roundtrip(client: AsyncClient) -> None:
    r = await client.post(
        "/turns",
        json={
            "session_id": "s1",
            "user_id": "u1",
            "messages": [
                {"role": "user", "content": "I live in München 🇩🇪. Привет!"}
            ],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {"emoji": "🚀"},
        },
    )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_empty_messages_rejected(client: AsyncClient) -> None:
    r = await client.post(
        "/turns",
        json={
            "session_id": "s1",
            "user_id": "u1",
            "messages": [],
            "timestamp": "2025-03-15T10:30:00Z",
            "metadata": {},
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_max_tokens_clamping(client: AsyncClient) -> None:
    # max_tokens out of bounds — Pydantic rejects.
    r = await client.post(
        "/recall",
        json={
            "query": "hi",
            "session_id": "s1",
            "user_id": "u1",
            "max_tokens": 0,
        },
    )
    assert r.status_code == 422
