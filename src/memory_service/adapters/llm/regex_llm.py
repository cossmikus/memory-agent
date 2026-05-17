"""Rule-based fallback extractor used when no OPENAI_API_KEY is configured.

Quality is much lower than the LLM extractor — this exists so the service
boots and the contract holds for diagnostics. Documented as a degradation
path in the README.
"""
from __future__ import annotations

import re

from memory_service.domain.models import MemoryCandidate, MemoryType, Message, Triple

# Each pattern allows up to 3 filler words between the subject "I" and the
# verb so "I just moved to Berlin" matches the same rule as "I moved to Berlin".
_PATTERNS: list[tuple[re.Pattern[str], str, MemoryType, str]] = [
    (re.compile(r"\bi(?:\s+\w+){0,3}\s+(?:work|am working) at ([A-Z][\w&.\- ]{1,40})", re.I), "employer", MemoryType.FACT, "employer"),
    (re.compile(r"\bi(?:\s+\w+){0,3}\s+(?:joined|started (?:at|working at)|started) ([A-Z][\w&.\- ]{1,40})", re.I), "employer", MemoryType.FACT, "employer"),
    (re.compile(r"\bi(?:\s+\w+){0,3}\s+(?:live in|am living in|am based in|moved to|relocated to) ([A-Z][\w\- ]{1,40})", re.I), "location_city", MemoryType.FACT, "location_city"),
    (re.compile(r"\b(?:from|previously in|used to live in) ([A-Z][A-Za-z\- ]{1,30}) (?:last|in|to|—|-|,|\.)", re.I), "previous_location_city", MemoryType.FACT, "previous_location_city"),
    (re.compile(r"\bi'?m (?:a|an) ([A-Za-z][\w\- ]{2,40}) at\b", re.I), "job_title", MemoryType.FACT, "job_title"),
    (re.compile(r"\bi (?:love|like|enjoy|prefer) ([A-Za-z][\w\- ]{1,40})", re.I), "preference", MemoryType.PREFERENCE, "preference"),
    (re.compile(r"\bi (?:hate|dislike|don'?t like) ([A-Za-z][\w\- ]{1,40})", re.I), "preference_negative", MemoryType.PREFERENCE, "preference_negative"),
    (re.compile(r"\bi'?m allergic to ([A-Za-z][\w\- ]{1,40})", re.I), "allergy", MemoryType.FACT, "allergy"),
    (re.compile(r"\bi'?m (?:a )?vegetarian\b", re.I), "dietary_restriction", MemoryType.FACT, "dietary_restriction"),
    (re.compile(r"\bi have (?:a|an) (\w+) named ([A-Z][\w]{1,30})", re.I), "pet", MemoryType.FACT, "pet"),
    (re.compile(r"\b(?:walking|feeding|playing with) ([A-Z][\w]{1,30})\b", re.I), "pet_implicit", MemoryType.FACT, "pet_implicit"),
]


class RegexLLM:
    """Implements ExtractorPort + a no-op rerank()."""

    async def extract(
        self,
        messages: list[Message],
        user_id: str | None = None,
    ) -> list[MemoryCandidate]:
        out: list[MemoryCandidate] = []
        for msg in messages:
            if msg.role != "user":
                continue
            text = msg.content
            for pat, key, mtype, fixed_key in _PATTERNS:
                for m in pat.finditer(text):
                    if fixed_key == "pet":
                        species, name = m.group(1), m.group(2)
                        out.append(MemoryCandidate(
                            type=MemoryType.FACT, key=f"pet:{name}:species",
                            value=species, confidence=0.7, salience=0.65,
                            evidence_snippet=m.group(0),
                            triples=[
                                Triple("user", "has_pet", name),
                                Triple(name, "species", species),
                            ],
                        ))
                        out.append(MemoryCandidate(
                            type=MemoryType.FACT, key=f"pet:{name}:name",
                            value=name, confidence=0.7, salience=0.6,
                            evidence_snippet=m.group(0),
                        ))
                    elif fixed_key == "pet_implicit":
                        name = m.group(1)
                        out.append(MemoryCandidate(
                            type=MemoryType.FACT, key=f"pet:{name}:name",
                            value=name, confidence=0.55, salience=0.55,
                            is_implicit=True, evidence_snippet=m.group(0),
                            triples=[Triple("user", "has_pet", name)],
                        ))
                    elif fixed_key == "dietary_restriction":
                        out.append(MemoryCandidate(
                            type=mtype, key=fixed_key, value="vegetarian",
                            confidence=0.8, salience=0.7,
                            evidence_snippet=m.group(0),
                            triples=[Triple("user", "diet", "vegetarian")],
                        ))
                    else:
                        value = m.group(1).strip().rstrip(".,;:!?'\"")
                        out.append(MemoryCandidate(
                            type=mtype, key=fixed_key, value=value,
                            confidence=0.7, salience=0.7,
                            evidence_snippet=m.group(0),
                            triples=[Triple("user", fixed_key, value)],
                        ))
        return out

    async def rerank(
        self,
        query: str,
        candidates: list[tuple[str, str]],
        top_k: int,
    ) -> list[tuple[str, float]]:
        return [(cid, 0.5) for cid, _ in candidates[:top_k]]
