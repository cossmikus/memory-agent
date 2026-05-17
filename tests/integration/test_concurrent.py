"""Concurrent-session scoping:
- Same user across sessions → memories accumulate.
- Different users → memories never bleed.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_same_user_across_sessions_accumulates(client: AsyncClient) -> None:
    await client.post(
        "/turns",
        json={
            "session_id": "s-march",
            "user_id": "alice",
            "messages": [{"role": "user", "content": "I work at Stripe."}],
            "timestamp": "2025-03-10T09:00:00Z",
            "metadata": {},
        },
    )
    await client.post(
        "/turns",
        json={
            "session_id": "s-april",
            "user_id": "alice",
            "messages": [{"role": "user", "content": "I moved to Berlin from NYC."}],
            "timestamp": "2025-04-05T12:00:00Z",
            "metadata": {},
        },
    )

    # A third, brand-new session queries both prior facts.
    r = await client.post(
        "/recall",
        json={
            "query": "Where does this user work and where do they live?",
            "session_id": "s-may",
            "user_id": "alice",
            "max_tokens": 512,
        },
    )
    body = r.json()
    text = body["context"]
    assert "Stripe" in text
    assert "Berlin" in text


@pytest.mark.asyncio
async def test_cross_user_isolation(client: AsyncClient) -> None:
    await client.post(
        "/turns",
        json={
            "session_id": "alice-s1",
            "user_id": "alice",
            "messages": [{"role": "user", "content": "I work at Stripe."}],
            "timestamp": "2025-03-10T09:00:00Z",
            "metadata": {},
        },
    )
    await client.post(
        "/turns",
        json={
            "session_id": "bob-s1",
            "user_id": "bob",
            "messages": [{"role": "user", "content": "I work at Datadog."}],
            "timestamp": "2025-03-11T09:00:00Z",
            "metadata": {},
        },
    )

    bob_recall = await client.post(
        "/recall",
        json={
            "query": "Where does this user work?",
            "session_id": "bob-fresh",
            "user_id": "bob",
            "max_tokens": 256,
        },
    )
    assert "Datadog" in bob_recall.json()["context"]
    assert "Stripe" not in bob_recall.json()["context"]

    alice_mems = await client.get("/users/alice/memories")
    alice_values = {m["value"] for m in alice_mems.json()["memories"]}
    assert "Datadog" not in alice_values
