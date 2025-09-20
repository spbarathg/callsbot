import asyncio
import os
import pytest
from bot.stats import StatsRecorder, SignalEvent


@pytest.mark.asyncio
async def test_outcomes_from_snapshots(tmp_path):
    os.environ['ENABLE_STATS'] = 'true'
    sr = StatsRecorder()
    sr.dir = str(tmp_path)
    sr.signals_path = os.path.join(sr.dir, 'signals.jsonl')
    sr.outcomes_path = os.path.join(sr.dir, 'outcomes.jsonl')
    db = os.path.join(str(tmp_path), 'stats.db')
    sr._db_path = db
    await sr.init()

    # write a signal with price
    s = SignalEvent(
        ts_utc="2020-01-01T00:00:00+00:00",
        ca="G2VzymsKt3zNAn4CKBndYcS67w6Kny5sDEp7Y2W1aTf6",
        symbol="TKN",
        classification="T1",
        source_channels=["@x"],
        uniques_OverlapMin=1,
        mentions_total=1,
        liquidity_usd=10000.0,
        volume24_usd=50000.0,
        market_cap_usd=1000000.0,
        txns_h1_total=100,
        buy_sell_ratio_h1=1.2,
        price_change_m15=1.0,
        price_usd=1.0,
    )
    await sr.record_signal(s)

    # snapshot after 15 minutes with higher price
    metrics = {
        'price_usd': 1.5,
        'liquidity_usd': 15000,
        'volume24_usd': 75000,
        'volume1h_usd': 10000,
        'market_cap_usd': 1200000,
        'txns_h1_total': 150,
        'buy_sell_ratio_h1': 1.3,
        'price_change_m5': 2.0,
        'price_change_m15': 5.0,
        'price_change_h1': 10.0,
        'pair_created_ms': None,
        'trending': False,
    }

    # Record a snapshot; then force outcome derivation
    await sr.record_snapshot(s.ca, metrics, ts_utc="2020-01-01T00:15:00+00:00")
    await sr.maybe_record_outcomes_from_snapshots(s.ca)

    # outcomes.jsonl should exist and contain at least one line
    assert os.path.exists(sr.outcomes_path)
    with open(sr.outcomes_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    assert len(lines) >= 1


