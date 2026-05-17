"""Token counting for the budget-aware context assembler.

tiktoken cl100k_base matches gpt-4o-mini tokenization closely enough that the
assembler can stay within budget. Falls back to len(text)/4 if tiktoken misses.
"""
from __future__ import annotations

import functools

try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")

    def _count(text: str) -> int:
        return len(_enc.encode(text, disallowed_special=()))

except Exception:  # pragma: no cover

    def _count(text: str) -> int:
        return max(1, len(text) // 4)


@functools.lru_cache(maxsize=4096)
def count_tokens(text: str) -> int:
    return _count(text)
