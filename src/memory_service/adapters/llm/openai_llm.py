"""GPT-4o-mini extraction via function calling, plus optional LLM rerank."""
from __future__ import annotations

import json

from openai import AsyncOpenAI

from memory_service.adapters.llm.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_TOOL_SCHEMA,
    FEW_SHOTS,
)
from memory_service.core.logging import get_logger
from memory_service.core.retry import async_retry
from memory_service.domain.models import MemoryCandidate, MemoryType, Message, Triple

log = get_logger(__name__)


def _build_messages(turn_messages: list[Message]) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": EXTRACTION_SYSTEM_PROMPT}]

    # Few-shots — each as a user/assistant exchange so the model learns the
    # tool-call shape from examples without us forging tool_call_ids.
    for shot in FEW_SHOTS:
        joined = "\n".join(shot["user_messages"])
        msgs.append({"role": "user", "content": f"Example user turn:\n{joined}"})
        msgs.append(
            {
                "role": "assistant",
                "content": (
                    "Expected `record_memories` argument:\n"
                    + json.dumps({"memories": shot["memories"]}, ensure_ascii=False)
                ),
            }
        )

    # The real turn.
    serialized = []
    for m in turn_messages:
        role = m.role
        if m.name:
            serialized.append(f"{role} ({m.name}): {m.content}")
        else:
            serialized.append(f"{role}: {m.content}")
    msgs.append(
        {
            "role": "user",
            "content": (
                "Now extract memories from this real turn. Call `record_memories`.\n\n"
                + "\n".join(serialized)
            ),
        }
    )
    return msgs


class OpenAILLM:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    @async_retry(max_attempts=3, base_delay=1.0)
    async def extract(
        self,
        messages: list[Message],
        user_id: str | None = None,
    ) -> list[MemoryCandidate]:
        if not messages:
            return []
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=_build_messages(messages),
                tools=[EXTRACTION_TOOL_SCHEMA],
                tool_choice={"type": "function", "function": {"name": "record_memories"}},
                temperature=0.0,
            )
        except Exception as exc:
            log.warning("extraction_failed", error=str(exc))
            return []

        choice = resp.choices[0]
        if not choice.message.tool_calls:
            return []

        try:
            args = json.loads(choice.message.tool_calls[0].function.arguments)
        except json.JSONDecodeError:
            log.warning("extraction_invalid_json")
            return []

        return _parse_candidates(args.get("memories", []))

    @async_retry(max_attempts=2, base_delay=0.5)
    async def rerank(
        self,
        query: str,
        candidates: list[tuple[str, str]],
        top_k: int,
    ) -> list[tuple[str, float]]:
        if not candidates:
            return []
        numbered = "\n".join(f"[{i}] {content}" for i, (_, content) in enumerate(candidates))
        sys = (
            "Score each candidate's relevance to the query on a 0-1 scale. "
            "Return JSON: {\"scores\": [number, ...]} with exactly one score per candidate "
            "in input order."
        )
        user = f"Query: {query}\n\nCandidates:\n{numbered}"
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            scores = data.get("scores", [])
        except Exception as exc:
            log.warning("rerank_failed", error=str(exc))
            return [(cid, 0.5) for cid, _ in candidates[:top_k]]

        scored: list[tuple[str, float]] = []
        for (cid, _), score in zip(candidates, scores, strict=False):
            try:
                scored.append((cid, float(score)))
            except (TypeError, ValueError):
                scored.append((cid, 0.0))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


def _parse_candidates(items: list[dict]) -> list[MemoryCandidate]:
    out: list[MemoryCandidate] = []
    for item in items:
        try:
            mtype = MemoryType(item["type"])
        except (KeyError, ValueError):
            continue
        triples = [
            Triple(
                subject=str(t.get("subject", "")).strip(),
                predicate=str(t.get("predicate", "")).strip(),
                object=str(t.get("object", "")).strip(),
            )
            for t in item.get("triples", [])
            if t.get("subject") and t.get("predicate") and t.get("object")
        ]
        try:
            cand = MemoryCandidate(
                type=mtype,
                key=str(item["key"]).strip().lower(),
                value=str(item["value"]).strip(),
                confidence=float(item.get("confidence", 0.7)),
                salience=float(item.get("salience", 0.5)),
                is_implicit=bool(item.get("is_implicit", False)),
                evidence_snippet=str(item.get("evidence_snippet", ""))[:500],
                triples=triples,
            )
        except (KeyError, TypeError, ValueError):
            continue
        if not cand.key or not cand.value:
            continue
        out.append(cand)
    return out
