"""Context assembler respects token budget and section priority."""
from __future__ import annotations

from datetime import datetime

import pytest

from memory_service.domain.assembler import ContextAssembler
from memory_service.domain.models import Memory, MemoryType, ScoredMemory


def _mem(mid: str, key: str, value: str, mtype: MemoryType = MemoryType.FACT) -> Memory:
    return Memory(
        id=mid,
        user_id="u",
        type=mtype,
        key=key,
        value=value,
        value_normalized=value.lower(),
        confidence=0.9,
        salience=0.9,
        source_turn_id=f"turn-{mid}",
        source_session_id="s",
        created_at=datetime(2025, 3, 15),
        updated_at=datetime(2025, 3, 15),
    )


async def _snippet_loader(_tid: str) -> str:
    return "user: relevant snippet"


@pytest.mark.asyncio
async def test_facts_section_takes_priority_under_tight_budget() -> None:
    facts = [_mem(f"f{i}", "employer", "Stripe", MemoryType.FACT) for i in range(3)]
    scored = [
        ScoredMemory(memory=_mem(f"r{i}", "topic", "some content"), score=0.5)
        for i in range(20)
    ]
    asm = ContextAssembler(snippet_loader=_snippet_loader)
    out = await asm.assemble(scored=scored, user_facts=facts, max_tokens=100)
    # Even with a tight budget the facts section appears.
    assert "Known facts" in out.context


@pytest.mark.asyncio
async def test_empty_inputs_yield_empty_context() -> None:
    asm = ContextAssembler(snippet_loader=_snippet_loader)
    out = await asm.assemble(scored=[], user_facts=[], max_tokens=512)
    assert out.context == ""
    assert out.citations == []
