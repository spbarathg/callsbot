import asyncio
import json
import logging
import os
import tempfile
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


async def write_json_atomic(path: str, data: Any) -> None:
    def _write() -> None:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp_path, path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    await asyncio.to_thread(_write)


async def read_json(path: str) -> Any | None:
    def _read() -> Any | None:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    return await asyncio.to_thread(_read)


