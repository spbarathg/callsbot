import os
import pytest
from datetime import datetime, timezone

from bot.telegram import extract_contract_addresses_from_message
from bot.evaluator import Evaluator


class _Msg:
    def __init__(self, text: str):
        self.text = text
        self.id = 1
        self.entities = []


class _Event:
    def __init__(self, text: str):
        self.raw_text = text
        self.message = _Msg(text)


@pytest.mark.asyncio
async def test_end_to_end_social_to_t1(monkeypatch):
    ca = '9wYucdoBb1CV7DcxG1cdKGn6XPHi3QBjyvhb1WejG7Hw'
    ev = _Event(f'Token: {ca}')
    addrs = extract_contract_addresses_from_message(ev)
    assert ca in addrs

    async def _dummy_send(*_args, **_kwargs):
        return None
    evaluator = Evaluator(_dummy_send)

    async def fake_metrics(_ca):
        return {
            'liquidity_usd': 20000,
            'volume24_usd': 100000,
            'market_cap_usd': 500000,
            'txns_h1_total': 200,
            'buy_sell_ratio_h1': 1.2,
            'price_change_m15': 2.0,
            'pair_created_ms': None,
            'trending': False,
        }

    async def fake_rpc(method: str, params: list):
        if method == 'getTokenSupply':
            return {'value': {'uiAmount': 1000000}}
        if method == 'getTokenLargestAccounts':
            return {'value': [{'uiAmount': 1000}]}
        return {'value': []}

    import bot.evaluator as be
    be.get_dex_metrics = fake_metrics
    be.solana_rpc = fake_rpc
    be.solana_get_account_info = lambda _: None

    # push mentions from 4 unique channels for T1
    channels = ['@a', '@b', '@c', '@d']
    for ch in channels:
        await evaluator.process_mention(ca, ch)

    assert evaluator.state.last_rank_sent.get(ca) in {None, 'T1', 'T2', 'T3'}


