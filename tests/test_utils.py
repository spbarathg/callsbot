import asyncio
import os
from bot import utils


def test_write_read_json_atomic(tmp_path):
    path = tmp_path / "state.json"
    data = {"a": 1, "b": "x"}
    asyncio.get_event_loop().run_until_complete(utils.write_json_atomic(str(path), data))
    loaded = asyncio.get_event_loop().run_until_complete(utils.read_json(str(path)))
    assert loaded == data


def test_with_retries_eventually_succeeds():
    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("fail")
        return 42

    result = asyncio.get_event_loop().run_until_complete(utils.with_retries(flaky, retries=5, backoff_sec=0))
    assert result == 42

