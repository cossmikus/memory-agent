"""Self-eval: runs all fixtures against the live service and reports a
per-scenario coverage metric.

The score for each probe is binary: 1 if any of `expected_any` appears in
the recall context (case-insensitive substring), else 0. For probes marked
`expected_empty`, score 1 if the context is empty, else 0. The aggregate
score is the mean across all probes.

Run with `pytest tests/eval/test_recall_quality.py -s` to see the table.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

FIXTURES_DIR = Path(__file__).parents[2] / "fixtures" / "scenarios"


def _load_fixtures() -> list[dict]:
    out: list[dict] = []
    for p in sorted(FIXTURES_DIR.glob("*.json")):
        out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


async def _ingest(client: AsyncClient, fixture: dict) -> None:
    user_id = fixture["user_id"]
    for turn in fixture["turns"]:
        r = await client.post(
            "/turns",
            json={
                "session_id": turn["session_id"],
                "user_id": user_id,
                "messages": turn["messages"],
                "timestamp": turn["timestamp"],
                "metadata": {},
            },
        )
        assert r.status_code == 201, r.text


async def _probe(client: AsyncClient, user_id: str, query: str) -> str:
    r = await client.post(
        "/recall",
        json={
            "query": query,
            "session_id": f"{user_id}-eval",
            "user_id": user_id,
            "max_tokens": 512,
        },
    )
    assert r.status_code == 200
    return r.json()["context"]


def _score_probe(context: str, probe: dict) -> tuple[bool, str]:
    if probe.get("expected_empty"):
        empty = not context.strip()
        return empty, "empty" if empty else "leaked"
    expected = probe.get("expected_any", [])
    lc = context.lower()
    for needle in expected:
        if needle.lower() in lc:
            return True, f"matched:{needle}"
    return False, "missing"


@pytest.mark.asyncio
async def test_recall_quality_self_eval(client: AsyncClient, capsys) -> None:
    fixtures = _load_fixtures()
    rows: list[tuple[str, int, int, str]] = []
    total, hit = 0, 0

    for fix in fixtures:
        await _ingest(client, fix)
        user_id = fix["user_id"]
        scenario_hit = 0
        scenario_total = 0
        notes: list[str] = []
        for probe in fix["probes"]:
            ctx = await _probe(client, user_id, probe["query"])
            ok, why = _score_probe(ctx, probe)
            scenario_total += 1
            total += 1
            if ok:
                scenario_hit += 1
                hit += 1
            notes.append(f"  - [{('OK' if ok else 'FAIL')}] {probe['query']!r} → {why}")
        rows.append(
            (
                fix["scenario"],
                scenario_hit,
                scenario_total,
                "\n".join(notes),
            )
        )

    with capsys.disabled():
        print("\n\nRecall-quality self-eval")
        print("=" * 60)
        for scenario, h, t, notes in rows:
            print(f"\n[{scenario}]  {h}/{t}")
            print(notes)
        print("-" * 60)
        print(f"TOTAL: {hit}/{total}  ({hit / total:.0%})")
        print("=" * 60)

    # Don't hard-fail the test on quality regressions — that's the CHANGELOG's
    # job. We only assert the harness itself runs cleanly.
    assert total > 0
