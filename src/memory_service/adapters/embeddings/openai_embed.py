"""OpenAI embedder. Batched, async, retried."""
from __future__ import annotations

from openai import AsyncOpenAI

from memory_service.core.logging import get_logger
from memory_service.core.retry import async_retry

log = get_logger(__name__)


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str, dim: int) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @async_retry(max_attempts=3, base_delay=0.5)
    async def embed(self, text: str) -> list[float]:
        if not text.strip():
            return [0.0] * self._dim
        resp = await self._client.embeddings.create(model=self._model, input=text)
        return resp.data[0].embedding

    @async_retry(max_attempts=3, base_delay=0.5)
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        clean = [t if t.strip() else " " for t in texts]
        resp = await self._client.embeddings.create(model=self._model, input=clean)
        # OpenAI guarantees response order matches input order.
        return [d.embedding for d in resp.data]


class NullEmbedder:
    """No-op embedder used when OPENAI_API_KEY is absent.

    Returns zero vectors so the contract still holds — the vector index will
    be useless but FTS5 and keyword search will still work. /health stays up.
    """

    def __init__(self, dim: int) -> None:
        self._dim = dim
        log.warning("null_embedder_in_use", reason="no OPENAI_API_KEY configured")

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, text: str) -> list[float]:
        return [0.0] * self._dim

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dim for _ in texts]
