import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def with_retries(fn: Callable[[], Awaitable[T]], retries: int, backoff_sec: float) -> T:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            return await fn()
        except Exception as e:
            last_err = e
            logger.warning(f"Retryable error (attempt {attempt + 1}/{retries}): {e}")
            await asyncio.sleep(backoff_sec * (attempt + 1))
    assert last_err is not None
    raise last_err


