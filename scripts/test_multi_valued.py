"""Targeted test for multi-valued attributes (allergies, dietary, etc.)

Question: after ingesting "I am vegetarian, allergic to shellfish and peanuts,
and I really dislike cilantro", does the service:

  1. Store BOTH allergies as separate active memories?
  2. Surface BOTH allergies in a /recall on "What is this user allergic to?"
  3. Keep both visible in /users/{id}/memories?

If 1 or 2 fail, multi-valued canonical keys are a real bug.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="mv_test_")) / "memory.db")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from httpx import ASGITransport, AsyncClient  # noqa: E402


def section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


async def main() -> int:
    from memory_service.config import reset_settings_cache
    reset_settings_cache()
    from memory_service.main import create_app

    app = create_app()
    failures: list[str] = []

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            # ─── Ingest a turn with multiple values for the same attribute. ──
            r = await c.post(
                "/turns",
                json={
                    "session_id": "mv-s1",
                    "user_id": "mv-user",
                    "messages": [{
                        "role": "user",
                        "content": (
                            "Before we order food: I am vegetarian, allergic to "
                            "shellfish and peanuts, and I really dislike cilantro."
                        ),
                    }],
                    "timestamp": "2025-04-04T09:00:00Z",
                    "metadata": {},
                },
            )
            assert r.status_code == 201, r.text

            # ─── Assertion 1: BOTH allergies stored as ACTIVE memories. ─────
            # Multi-valued attributes use "<attribute>:<value>" canonical keys
            # (e.g., allergy:shellfish, allergy:peanuts) so each value lives in
            # its own row and supersession never silently drops one.
            mems = (await c.get("/users/mv-user/memories")).json()["memories"]
            section("ALL MEMORIES")
            for m in mems:
                print(
                    f"  [{('A' if m['active'] else 'I'):s}] {m['type']:11s} "
                    f"key={m['key']:32s} value={m['value']!r}"
                )

            allergy_active = [
                m for m in mems if m["key"].startswith("allergy") and m["active"]
            ]
            allergy_all = [m for m in mems if m["key"].startswith("allergy")]
            allergy_values_active = {m["value"].lower() for m in allergy_active}

            section("ALLERGY ROWS")
            print(f"  active count:   {len(allergy_active)}")
            print(f"  total count:    {len(allergy_all)} (including superseded)")
            print(f"  active values:  {sorted(allergy_values_active)}")

            if "shellfish" not in allergy_values_active:
                failures.append(
                    "Expected 'shellfish' to be an ACTIVE allergy memory, "
                    f"but active allergies are {sorted(allergy_values_active)}"
                )
            if "peanuts" not in allergy_values_active:
                failures.append(
                    "Expected 'peanuts' to be an ACTIVE allergy memory, "
                    f"but active allergies are {sorted(allergy_values_active)}"
                )

            # ─── Assertion 2: BOTH surface in /recall. ───────────────────────
            rec = (await c.post(
                "/recall",
                json={
                    "query": "What is this user allergic to?",
                    "session_id": "mv-recall",
                    "user_id": "mv-user",
                    "max_tokens": 512,
                },
            )).json()
            ctx = rec["context"].lower()
            section("RECALL CONTEXT")
            print(rec["context"])

            if "shellfish" not in ctx:
                failures.append("'/recall' context did not mention shellfish")
            if "peanuts" not in ctx:
                failures.append("'/recall' context did not mention peanuts")

            # ─── Assertion 3: dietary_restriction(vegetarian) still active. ─
            veg = [
                m for m in mems
                if m["key"].startswith("dietary_restriction")
                and m["value"].lower() == "vegetarian"
                and m["active"]
            ]
            if not veg:
                failures.append("Vegetarian dietary_restriction not active")

    print()
    if failures:
        print("=" * 60)
        print(f"FAIL — {len(failures)} assertion(s) failed:")
        for f in failures:
            print(f"  - {f}")
        print("=" * 60)
        return 1

    print("=" * 60)
    print("PASS — all multi-valued assertions hold")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
