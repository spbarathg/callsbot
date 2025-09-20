import asyncio
import pytest
from bot.apis import HttpClient


@pytest.mark.asyncio
async def test_http_client_get_json(monkeypatch):
    hc = HttpClient()

    class _Resp:
        status = 200
        async def text(self):
            return '{"ok":true}'
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False

    class _Sess:
        closed = False
        def get(self, *_, **__):
            return _Resp()

    await hc.start()
    hc.session = _Sess()
    data = await hc.get_json("http://x/y")
    assert data.get("ok") is True


