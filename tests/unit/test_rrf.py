"""RRF math is small and easy to break."""
from __future__ import annotations

from datetime import datetime

from memory_service.domain.models import Memory, MemoryType
from memory_service.domain.recall import _rrf_fuse


def _mem(mid: str) -> Memory:
    return Memory(
        id=mid,
        user_id="u",
        type=MemoryType.FACT,
        key="k",
        value="v",
        value_normalized="v",
        confidence=1.0,
        salience=1.0,
        source_turn_id="t",
        source_session_id="s",
        created_at=datetime(2025, 1, 1),
        updated_at=datetime(2025, 1, 1),
    )


def test_rrf_prefers_consensus_across_rankings() -> None:
    a, b, c = _mem("A"), _mem("B"), _mem("C")
    # A is top in both rankings; B is top in one only; C is in none.
    bm25 = [(a, 1.0), (b, 0.9)]
    vec = [(a, 0.99), (c, 0.5)]
    fused = _rrf_fuse([(bm25, "bm25"), (vec, "vec")], k=60)
    ids = [sm.memory.id for sm in fused]
    assert ids[0] == "A"
    assert set(ids) == {"A", "B", "C"}


def test_rrf_empty() -> None:
    assert _rrf_fuse([], k=60) == []
