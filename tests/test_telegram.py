import asyncio
import pytest
from types import SimpleNamespace
from bot.telegram import Bot


class _Client:
    async def start(self):
        return None
    async def send_message(self, *_args, **_kwargs):
        return None
    async def disconnect(self):
        return None


@pytest.mark.asyncio
async def test_tiered_alert_cooldown(monkeypatch):
    b = Bot()
    b.client = _Client()
    # Disable evaluator and enable tiered alerts path
    import config.config as cfg
    monkeypatch.setattr(cfg, 'ENABLE_EVALUATOR', False, raising=False)
    monkeypatch.setattr(cfg, 'ENABLE_TIERED_ALERTS', True, raising=False)
    monkeypatch.setattr(cfg, 'T1_IMMEDIATE', True, raising=False)
    monkeypatch.setattr(cfg, 'COOLDOWN_MINUTES_T1', 60, raising=False)

    # Force send without hitting Telegram by monkeypatching method
    sent = {"n": 0}
    async def fake_send(*_a, **_k):
        sent["n"] += 1
    b._send_alert_message = fake_send  # type: ignore

    ca = '9wYucdoBb1CV7DcxG1cdKGn6XPHi3QBjyvhb1WejG7Hw'
    await b._maybe_send_tiered_alert(ca, 'group', 1)
    await b._maybe_send_tiered_alert(ca, 'group', 1)

    # Due to cooldown, second call should not send
    assert sent["n"] == 1


