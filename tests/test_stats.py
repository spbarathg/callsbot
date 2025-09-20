import os
import asyncio
from bot.stats import StatsRecorder, SignalEvent


async def test_stats_signal_jsonl(tmp_path):
    os.environ['ENABLE_STATS'] = 'true'
    sr = StatsRecorder()
    sr.dir = str(tmp_path)
    sr.signals_path = os.path.join(sr.dir, 'signals.jsonl')
    s = SignalEvent(
        ts_utc="2020-01-01T00:00:00Z",
        ca="9wYucdoBb1CV7DcxG1cdKGn6XPHi3QBjyvhb1WejG7Hw",
        symbol=None,
        classification="T1",
        source_channels=["@x"],
        uniques_OverlapMin=1,
        mentions_total=1,
        liquidity_usd=0.0,
        volume24_usd=0.0,
        market_cap_usd=0.0,
        txns_h1_total=0,
        buy_sell_ratio_h1=0.0,
        price_change_m15=0.0,
        price_usd=None,
    )
    await sr.record_signal(s)
    assert os.path.exists(sr.signals_path)


