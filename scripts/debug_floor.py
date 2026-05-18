"""Measure cosine similarity between a query and the user's memories,
so we can pick a sensible vector_score_floor.

Run after ingesting Eve's allergy scenario.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("LOG_LEVEL", "WARNING")

from httpx import ASGITransport, AsyncClient  # noqa: E402


async def main() -> None:
    from memory_service.config import reset_settings_cache
    reset_settings_cache()
    from memory_service.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            # Ingest Eve.
            r = await c.post(
                "/turns",
                json={
                    "session_id": "eve-s1",
                    "user_id": "eve-debug",
                    "messages": [{
                        "role": "user",
                        "content": "I am vegetarian, allergic to shellfish and peanuts, and I really dislike cilantro.",
                    }],
                    "timestamp": "2025-04-04T09:00:00Z",
                    "metadata": {},
                },
            )
            assert r.status_code == 201

            service = app.state.service
            repo = service._memories
            embedder = service._embedder

            queries = [
                "What should I know before ordering food for this user?",
                "What kind of car does this user drive?",
                "Is this user allergic to anything?",
                "What does this user eat?",
                "Tell me about this user's pets",
            ]

            print(f"{'query':<60s} top_cosine")
            print("-" * 80)
            for q in queries:
                qvec = await embedder.embed(q)
                hits = await repo.search_vector("eve-debug", qvec, 5)
                top = max((s for _, s in hits), default=0.0)
                tag = ""
                if top >= 0.35:
                    tag = " (passes 0.35 floor)"
                elif top >= 0.30:
                    tag = " (passes 0.30, blocked at 0.35)"
                elif top >= 0.25:
                    tag = " (passes 0.25, blocked at 0.30)"
                else:
                    tag = " (blocked at 0.25)"
                print(f"{q:<60s} {top:.4f}{tag}")


if __name__ == "__main__":
    asyncio.run(main())
