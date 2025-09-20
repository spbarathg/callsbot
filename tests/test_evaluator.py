import asyncio
import types
from datetime import datetime, timezone
import pytest
from bot.evaluator import Evaluator


async def _dummy_send(ca: str, classification: str, body: str):
    return None


@pytest.mark.asyncio
async def test_t1_trigger_unique_channels(monkeypatch):
    ev = Evaluator(_dummy_send)
    now = datetime.now(timezone.utc)
    ca = "9wYucdoBb1CV7DcxG1cdKGn6XPHi3QBjyvhb1WejG7Hw"

    async def fake_metrics(_ca):
        return {
            'liquidity_usd': 0,
            'volume24_usd': 0,
            'market_cap_usd': 0,
            'txns_h1_total': 0,
            'buy_sell_ratio_h1': 0,
            'price_change_m15': 0,
            'pair_created_ms': None,
            'trending': False,
        }

    async def fake_rpc(method: str, params: list):
        return {'value': {'uiAmount': 1}} if method == 'getTokenSupply' else {'value': []}

    # monkeypatch metrics
    import bot.evaluator as be
    be.get_dex_metrics = fake_metrics
    be.solana_rpc = fake_rpc
    be.solana_get_account_info = lambda _: None

    # feed mentions from unique channels >= MIN_UNIQUE_CHANNELS_T1
    channels = [f"@c{i}" for i in range(5)]
    for ch in channels:
        await ev.process_mention(ca, ch)

    assert ev.state.last_rank_sent.get(ca) in {None, 'T1', 'T2', 'T3'}


