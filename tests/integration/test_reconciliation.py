"""Fact evolution: contradiction → supersession; opinion arc preserved."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_employer_supersession(client: AsyncClient) -> None:
    """Stripe → Notion: the active fact must flip, but the chain is preserved."""
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

    # Second turn — same canonical key (employer), different value.
    await client.post(
        "/turns",
        json={
            "session_id": "s-may",
            "user_id": "alice",
            "messages": [{"role": "user", "content": "I just joined Notion."}],
            "timestamp": "2025-05-01T11:00:00Z",
            "metadata": {},
        },
    )

    mems = (await client.get("/users/alice/memories")).json()["memories"]
    employer_mems = [m for m in mems if m["key"] == "employer"]
    assert len(employer_mems) == 2, f"expected 2 employer rows (chain preserved), got {employer_mems}"

    active = [m for m in employer_mems if m["active"]]
    inactive = [m for m in employer_mems if not m["active"]]
    assert len(active) == 1
    assert len(inactive) == 1
    assert active[0]["value"] == "Notion"
    assert inactive[0]["value"] == "Stripe"
    assert active[0]["supersedes"] == inactive[0]["id"]

    # /recall must surface the current fact only.
    r = await client.post(
        "/recall",
        json={
            "query": "Where does this user work?",
            "session_id": "fresh",
            "user_id": "alice",
            "max_tokens": 256,
        },
    )
    text = r.json()["context"]
    assert "Notion" in text
