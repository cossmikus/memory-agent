"""Exponential backoff for outbound API calls."""
from __future__ import annotations

import asyncio
import functools
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from memory_service.core.logging import get_logger

log = get_logger(__name__)
T = TypeVar("T")


def async_retry(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs) -> T:
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay = delay * (0.5 + random.random() * 0.5)
                    log.warning(
                        "retry_attempt",
                        fn=fn.__name__,
                        attempt=attempt,
                        error=str(exc),
                        next_delay_s=round(delay, 3),
                    )
                    await asyncio.sleep(delay)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
