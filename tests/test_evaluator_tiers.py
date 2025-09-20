import pytest
from datetime import datetime, timezone, timedelta
from bot.evaluator import Evaluator


@pytest.mark.asyncio
async def test_t2_boundary(monkeypatch):
    async def _send(*_a, **_k):
        return None
    ev = Evaluator(_send)
    ca = "GxNa3Fza4e3GSDqccT978EuheTwu7af3DaB6JkqPtjU5"

    # First seen now minus 45 minutes (within T2 window 30–90)
    now = datetime.now(timezone.utc)
    ev.state.first_seen_ts[ca] = now - timedelta(minutes=45)
    ev.state.mentions_by_ca[ca] = []

    async def fake_metrics(_):
        return {
            'liquidity_usd': 60000,
            'volume24_usd': 300000,
            'market_cap_usd': 800000,
            'txns_h1_total': 600,
            'buy_sell_ratio_h1': 1.6,
            'price_change_m15': 1.0,
            'pair_created_ms': None,
            'trending': False,
        }

    async def fake_rpc(method: str, params: list):
        if method == 'getTokenSupply':
            return {'value': {'uiAmount': 1_000_000}}
        if method == 'getTokenLargestAccounts':
            # Many accounts with small balances so largest% small and unique holders >= threshold
            return {'value': [{'uiAmount': 5000} for _ in range(300)]}
        return {'value': []}

    import bot.evaluator as be
    be.get_dex_metrics = fake_metrics
    be.solana_rpc = fake_rpc
    be.solana_get_account_info = lambda _: None

    # Add VIP holder evidence to satisfy VIP >=1 gate
    ev.state.vip_holders_by_ca[ca] = {"vip1"}

    # Push some mentions (unique channels)
    for ch in ["@a", "@b", "@c", "@d"]:
        await ev.process_mention(ca, ch)

    assert ev.state.last_rank_sent.get(ca) in {"T2", "T3", "T1"}


@pytest.mark.asyncio
async def test_t3_boundary(monkeypatch):
    async def _send2(*_a, **_k):
        return None
    ev = Evaluator(_send2)
    ca = "GKT2j5gPqY2ZKfhaGB5cn5bTHLSKTUrULe8wG1QwmLpt"
    now = datetime.now(timezone.utc)
    ev.state.first_seen_ts[ca] = now - timedelta(minutes=180)  # 3h in window
    ev.state.t1_price_usd[ca] = 1.0

    async def fake_metrics(_):
        return {
            'liquidity_usd': 100000,
            'volume24_usd': 3_000_000,
            'market_cap_usd': 1_000_000,
            'txns_h1_total': 1000,
            'buy_sell_ratio_h1': 1.8,
            'price_change_m15': 1.0,
            'pair_created_ms': None,
            'trending': True,
            'price_usd': 8.0,
        }

    async def fake_rpc(method: str, params: list):
        if method == 'getTokenSupply':
            return {'value': {'uiAmount': 10_000_000}}
        if method == 'getTokenLargestAccounts':
            return {'value': [{'uiAmount': 10000} for _ in range(2000)]}
        return {'value': []}

    import bot.evaluator as be
    be.get_dex_metrics = fake_metrics
    be.solana_rpc = fake_rpc
    be.solana_get_account_info = lambda _: None

    for ch in ["@a", "@b", "@c", "@d", "@e"]:
        await ev.process_mention(ca, ch)

    assert ev.state.last_rank_sent.get(ca) in {"T3", "T2", "T1"}


