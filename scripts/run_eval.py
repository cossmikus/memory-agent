"""Standalone self-eval runner.

Boots the FastAPI app with whatever .env / environment is configured, ingests
each fixture scenario, probes the recall endpoints, and prints a coverage
table per scenario. Use this to drive CHANGELOG iteration.

    python scripts/run_eval.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Set a fresh ephemeral DB for the eval run so prior state doesn't leak.
_TMPDIR = tempfile.mkdtemp(prefix="memory_eval_")
os.environ["DB_PATH"] = str(Path(_TMPDIR) / "memory.db")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from memory_service.config import reset_settings_cache  # noqa: E402

FIXTURES = ROOT / "fixtures" / "scenarios"


def load_fixtures() -> list[dict]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(FIXTURES.glob("*.json"))
    ]


async def ingest(client: AsyncClient, fixture: dict) -> float:
    """Returns wall-clock seconds for ingestion."""
    started = time.monotonic()
    for turn in fixture["turns"]:
        r = await client.post(
            "/turns",
            json={
                "session_id": turn["session_id"],
                "user_id": fixture["user_id"],
                "messages": turn["messages"],
                "timestamp": turn["timestamp"],
                "metadata": {},
            },
        )
        if r.status_code != 201:
            raise SystemExit(f"ingest failed: {r.status_code} {r.text}")
    return time.monotonic() - started


async def probe(client: AsyncClient, user_id: str, query: str) -> tuple[str, float]:
    started = time.monotonic()
    r = await client.post(
        "/recall",
        json={
            "query": query,
            "session_id": f"{user_id}-eval",
            "user_id": user_id,
            "max_tokens": 512,
        },
    )
    elapsed = time.monotonic() - started
    if r.status_code != 200:
        raise SystemExit(f"recall failed: {r.status_code} {r.text}")
    return r.json()["context"], elapsed


def score(context: str, p: dict) -> tuple[bool, str]:
    if p.get("expected_empty"):
        ok = not context.strip()
        return ok, "empty" if ok else "leaked"
    for needle in p.get("expected_any", []):
        if needle.lower() in context.lower():
            return True, f"matched:{needle}"
    return False, "missing"


async def main() -> int:
    reset_settings_cache()
    from memory_service.config import get_settings
    from memory_service.main import create_app

    settings = get_settings()
    print(f"Using extraction model: {settings.extraction_model}")
    print(f"Using embedding model:  {settings.embedding_model}")
    print(f"OpenAI key configured:  {settings.has_openai}")
    print(f"DB path:                {settings.db_path}")
    print("-" * 60)

    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            total = 0
            hit = 0
            scenarios = []

            for fix in load_fixtures():
                ingest_time = await ingest(client, fix)

                scenario_hit = 0
                scenario_total = 0
                lines = []
                latencies = []
                for p in fix["probes"]:
                    ctx, latency = await probe(client, fix["user_id"], p["query"])
                    latencies.append(latency)
                    ok, why = score(ctx, p)
                    scenario_total += 1
                    total += 1
                    if ok:
                        scenario_hit += 1
                        hit += 1
                    badge = "OK  " if ok else "FAIL"
                    lines.append(f"    [{badge}] {p['query']!r} → {why}")

                scenarios.append(
                    {
                        "name": fix["scenario"],
                        "hit": scenario_hit,
                        "total": scenario_total,
                        "lines": lines,
                        "ingest_s": ingest_time,
                        "avg_recall_s": sum(latencies) / max(1, len(latencies)),
                    }
                )

            # Inspect one user's memory chain for fact-evolution visualization.
            r = await client.get("/users/fix-bob/memories")
            employer_chain = [
                m for m in r.json()["memories"] if m["key"] == "employer"
            ]

    print()
    print("Recall-quality self-eval")
    print("=" * 70)
    for s in scenarios:
        print(
            f"\n[{s['name']}]  {s['hit']}/{s['total']}  "
            f"ingest={s['ingest_s']:.1f}s  avg_recall={s['avg_recall_s']*1000:.0f}ms"
        )
        for line in s["lines"]:
            print(line)
    print("-" * 70)
    pct = (hit / total) if total else 0
    print(f"TOTAL: {hit}/{total}  ({pct:.0%})")
    print("=" * 70)

    if employer_chain:
        print("\nFact-evolution chain for fix-bob (employer):")
        for m in sorted(employer_chain, key=lambda x: x["created_at"]):
            badge = "ACTIVE  " if m["active"] else "INACTIVE"
            sup = m["supersedes"] or "—"
            print(f"  [{badge}] value={m['value']!r}  supersedes={sup}")

    return 0 if hit == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
