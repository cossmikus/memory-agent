"""Context assembler — formats recalled memories into prose under a token budget.

Priority logic (defended in README):
  Section 1: Stable user facts (FACT, PREFERENCE) — minimum guaranteed slot
             so the agent always has core identity context.
  Section 2: Query-relevant memories — the hits the recall pipeline surfaced,
             minus anything already shown in §1.
  Section 3: Recent conversational context — a snippet from the most relevant
             source turn, to give the agent grounding for the recall hits.

Greedy fill: §1 up to its quota, §2 up to its quota, §3 with whatever budget
remains. If a section is empty, its budget transfers to the next.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from memory_service.domain.models import (
    Citation,
    Memory,
    MemoryType,
    RecalledContext,
    ScoredMemory,
)
from memory_service.domain.tokens import count_tokens


class ContextAssembler:
    SECTION_HEADERS = {
        "facts": "## Known facts about this user",
        "relevant": "## Relevant from recent conversations",
        "recent": "## Recent context",
    }

    # Soft per-section quotas, expressed as fractions of max_tokens.
    QUOTA_FACTS = 0.45
    QUOTA_RELEVANT = 0.45
    QUOTA_RECENT = 0.10

    def __init__(
        self,
        snippet_loader: Callable[[str], Awaitable[str]],
    ) -> None:
        """`snippet_loader` returns a short snippet for a turn_id — used to
        build §3 recent-context lines and the citation snippets."""
        self._snippet_loader = snippet_loader

    async def assemble(
        self,
        scored: list[ScoredMemory],
        user_facts: list[Memory],
        max_tokens: int,
    ) -> RecalledContext:
        budget = max(64, max_tokens)
        used_ids: set[str] = set()
        citations: list[Citation] = []

        # Section 1: stable user facts.
        facts_budget = int(budget * self.QUOTA_FACTS)
        facts_lines: list[str] = []
        for mem in _rank_facts(user_facts):
            line = self._format_fact_line(mem)
            cost = count_tokens(line) + 1
            if cost > facts_budget:
                continue
            facts_lines.append(line)
            facts_budget -= cost
            used_ids.add(mem.id)

        # Section 2: query-relevant.
        relevant_budget = int(budget * self.QUOTA_RELEVANT) + facts_budget
        relevant_lines: list[str] = []
        for sm in scored:
            if sm.memory.id in used_ids:
                continue
            line = self._format_relevant_line(sm.memory)
            cost = count_tokens(line) + 1
            if cost > relevant_budget:
                continue
            relevant_lines.append(line)
            relevant_budget -= cost
            used_ids.add(sm.memory.id)
            snippet = await self._snippet_loader(sm.memory.source_turn_id)
            citations.append(
                Citation(
                    turn_id=sm.memory.source_turn_id,
                    score=round(sm.score, 4),
                    snippet=snippet[:200] if snippet else sm.memory.value,
                )
            )

        # Section 3: recent context — pull one snippet from the top-scored
        # memory's source turn that we haven't already cited.
        recent_lines: list[str] = []
        recent_budget = int(budget * self.QUOTA_RECENT) + relevant_budget
        cited_turns = {c.turn_id for c in citations}
        for sm in scored[:3]:
            tid = sm.memory.source_turn_id
            if tid in cited_turns:
                continue
            snippet = await self._snippet_loader(tid)
            if not snippet:
                continue
            line = f"- [{sm.memory.created_at.date().isoformat()}] {snippet[:160]}"
            cost = count_tokens(line) + 1
            if cost > recent_budget:
                continue
            recent_lines.append(line)
            recent_budget -= cost
            cited_turns.add(tid)
            break  # one recent-context line is enough for the budget

        sections: list[str] = []
        if facts_lines:
            sections.append(self.SECTION_HEADERS["facts"] + "\n" + "\n".join(facts_lines))
        if relevant_lines:
            sections.append(
                self.SECTION_HEADERS["relevant"] + "\n" + "\n".join(relevant_lines)
            )
        if recent_lines:
            sections.append(self.SECTION_HEADERS["recent"] + "\n" + "\n".join(recent_lines))

        context = "\n\n".join(sections)
        # Final hard-cap safety: trim to 1.05× budget if we somehow overshot.
        if count_tokens(context) > int(budget * 1.05):
            context = _truncate_to_tokens(context, int(budget * 1.05))

        return RecalledContext(context=context, citations=citations)

    @staticmethod
    def _format_fact_line(mem: Memory) -> str:
        date_str = mem.updated_at.date().isoformat()
        history_note = ""
        if mem.history:
            prior = [h for h in mem.history if h.get("kind") == "evolved"]
            if prior:
                history_note = f" (prior stance: {prior[-1].get('value', '')})"
        if mem.supersedes:
            history_note = f" (updated {date_str})"
        return f"- {_pretty_key(mem.key)}: {mem.value}{history_note}"

    @staticmethod
    def _format_relevant_line(mem: Memory) -> str:
        date_str = mem.updated_at.date().isoformat()
        return f"- [{date_str}] {_pretty_key(mem.key)}: {mem.value}"


def _rank_facts(facts: list[Memory]) -> list[Memory]:
    """Stable user facts ordered by salience * confidence, types restricted
    to FACT and PREFERENCE — the agent's core identity context."""
    stable = [
        m
        for m in facts
        if m.active and m.type in (MemoryType.FACT, MemoryType.PREFERENCE)
    ]
    stable.sort(key=lambda m: (m.salience * m.confidence, m.updated_at), reverse=True)
    return stable[:15]


def _pretty_key(key: str) -> str:
    return key.replace("_", " ").replace(":", " ").strip().title()


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    if count_tokens(text) <= max_tokens:
        return text
    # Binary search the longest prefix that fits.
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count_tokens(text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"
