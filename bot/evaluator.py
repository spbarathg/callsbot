import logging
from datetime import datetime, timedelta, timezone
from math import exp
from typing import Any, Dict, List, Optional, Set, Tuple

from config.config import (
    OVERLAP_WINDOW_MIN,
    MIN_UNIQUE_CHANNELS_T1,
    VEL5_WINDOW_MIN,
    VEL10_WINDOW_MIN,
    MENTION_DECAY_HALF_LIFE_MIN,
    LIQ_THRESHOLD,
    VOL_1H_THRESHOLD,
    VOL_24H_THRESHOLD,
    LARGEST_WALLET_MAX,
    HOLDERS_THRESHOLD,
    MINT_SAFETY_REQUIRED,
    PRICE_MULTIPLE_MIN,
    PRICE_MULTIPLE_MAX,
    LIQ_MIN_USD,
    VOL24_MIN_USD,
    T1_MARKET_REQUIRED,
    # philosophy thresholds
    T2_HOLDERS_MIN,
    T2_LIQ_MIN_USD,
    T2_LIQ_DRAWDOWN_MAX_PCT,
    T2_TXNS_H1_MIN,
    T2_BUY_SELL_RATIO_MIN,
    T2_AGE_MIN_MINUTES,
    T2_AGE_MAX_MINUTES,
    T3_MCAP_MIN_USD,
    T3_VOL24_MIN_USD,
    T3_PRICE_MIN_X,
    T3_PRICE_MAX_X,
    T3_HOLDERS_MIN,
    T3_POS_TREND_REQUIRED,
    T3_AGE_MIN_MINUTES,
    T3_AGE_MAX_MINUTES,
)
from bot.apis import get_dex_metrics, solana_get_account_info, solana_rpc
from bot.stats import StatsRecorder, SignalEvent

logger = logging.getLogger(__name__)


class Mention:
    __slots__ = ("timestamp_utc", "channel", "tier", "weight")

    def __init__(self, timestamp_utc: datetime, channel: str, tier: int, weight: float) -> None:
        self.timestamp_utc = timestamp_utc
        self.channel = channel
        self.tier = tier
        self.weight = weight


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _decay_multiplier(age_minutes: float) -> float:
    if age_minutes <= 0:
        return 1.0
    half_life = max(MENTION_DECAY_HALF_LIFE_MIN, 1.0)
    return exp(-0.6931471805599453 * (age_minutes / half_life))


def _summarize_channels(mentions: List[Mention]) -> str:
    uniq: List[str] = []
    seen: Set[str] = set()
    for m in mentions:
        name = m.channel
        if name not in seen:
            uniq.append(name)
            seen.add(name)
        if len(uniq) >= 3:
            break
    return ", ".join([n if len(n) <= 20 else (n[:17] + '...') for n in uniq])


class EvaluatorState:
    def __init__(self) -> None:
        self.mentions_by_ca: Dict[str, List[Mention]] = {}
        self.last_rank_sent: Dict[str, str] = {}
        self.safety_cache: Dict[str, Tuple[bool, bool]] = {}
        self.dex_cache: Dict[str, Dict[str, Any]] = {}
        self.vip_holders_by_ca: Dict[str, Set[str]] = {}
        self.t1_price_usd: Dict[str, float] = {}
        self.first_seen_ts: Dict[str, datetime] = {}
        self.peak_liquidity_usd: Dict[str, float] = {}


class Evaluator:
    def __init__(self, send_message_fn) -> None:
        self.state = EvaluatorState()
        self.send_message = send_message_fn
        self.stats = StatsRecorder()

    def to_persisted_state(self) -> Dict[str, Any]:
        return {
            "last_rank_sent": dict(self.state.last_rank_sent),
            "t1_price_usd": dict(self.state.t1_price_usd),
            "first_seen_ts": {k: v.isoformat() for k, v in self.state.first_seen_ts.items()},
            "peak_liquidity_usd": dict(self.state.peak_liquidity_usd),
        }

    def load_persisted_state(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        lrs = data.get("last_rank_sent") or {}
        t1 = data.get("t1_price_usd") or {}
        first_seen = data.get("first_seen_ts") or {}
        peak_liq = data.get("peak_liquidity_usd") or {}
        if isinstance(lrs, dict):
            self.state.last_rank_sent.update({str(k): str(v) for k, v in lrs.items()})
        if isinstance(t1, dict):
            try:
                self.state.t1_price_usd.update({str(k): float(v) for k, v in t1.items()})
            except Exception:
                pass
        if isinstance(first_seen, dict):
            for k, v in first_seen.items():
                try:
                    self.state.first_seen_ts[str(k)] = datetime.fromisoformat(str(v))
                except Exception:
                    continue
        if isinstance(peak_liq, dict):
            try:
                self.state.peak_liquidity_usd.update({str(k): float(v) for k, v in peak_liq.items()})
            except Exception:
                pass

    def prune_memory(self) -> None:
        now = _now_utc()
        three_hours_ago = now - timedelta(hours=3)
        # Prune mentions
        new_mentions: Dict[str, List[Mention]] = {}
        for ca, arr in self.state.mentions_by_ca.items():
            kept = [m for m in arr if m.timestamp_utc >= three_hours_ago]
            if kept:
                new_mentions[ca] = kept
        self.state.mentions_by_ca = new_mentions
        # Prune dex cache older than 1 hour
        pruned_dex: Dict[str, Dict[str, Any]] = {}
        for ca, d in self.state.dex_cache.items():
            ts = d.get("ts")
            if ts and isinstance(ts, datetime):
                if (now - ts).total_seconds() <= 3600:
                    pruned_dex[ca] = d
        self.state.dex_cache = pruned_dex
        # Limit caches to prevent unbounded growth
        max_keys = 2000
        if len(self.state.last_rank_sent) > max_keys:
            # keep most recent by presence in mentions or arbitrary trim
            keep = set(list(self.state.mentions_by_ca.keys())[:max_keys])
            self.state.last_rank_sent = {k: v for k, v in self.state.last_rank_sent.items() if k in keep}
        if len(self.state.t1_price_usd) > max_keys:
            keep = set(list(self.state.mentions_by_ca.keys())[:max_keys])
            self.state.t1_price_usd = {k: v for k, v in self.state.t1_price_usd.items() if k in keep}

    async def ensure_safety_checked(self, ca: str) -> Tuple[bool, bool]:
        if ca in self.state.safety_cache:
            return self.state.safety_cache[ca]
        mint_revoked = False
        freeze_revoked = False
        try:
            data = await solana_get_account_info(ca)
            if data:
                mint_revoked, freeze_revoked = parse_mint_safety(data)
        except Exception as e:
            logger.warning(f"Safety check failed for {ca}: {e}")
        self.state.safety_cache[ca] = (mint_revoked, freeze_revoked)
        return self.state.safety_cache[ca]

    async def holders_and_whales_ok(self, ca: str) -> bool:
        # Conservative default on failure: False
        try:
            # Use RPC approximation for holder data
            supply_info = await solana_rpc("getTokenSupply", [ca])
            supply = float((((supply_info or {}).get('value') or {}).get('uiAmount')) or 0)
            if supply <= 0:
                return False
            largest_accounts = await solana_rpc("getTokenLargestAccounts", [ca, {"commitment": "confirmed"}])
            values = (largest_accounts or {}).get('value') or []
            max_amount = 0.0
            unique_holders = 0
            for v in values:
                amt = float((v or {}).get('uiAmount') or 0)
                if amt > 0:
                    unique_holders += 1
                if amt > max_amount:
                    max_amount = amt
            largest_pct = (max_amount / supply * 100.0) if supply > 0 else 100.0
            # Record holders snapshot for analytics
            try:
                if self.stats:
                    await self.stats.record_holders(ca, supply, largest_pct, unique_holders)
            except Exception:
                pass
            return (unique_holders >= HOLDERS_THRESHOLD and largest_pct <= LARGEST_WALLET_MAX)
        except Exception as e:
            logger.warning(f"Holders/whale check failed for {ca}: {e}")
            return False

    async def process_mention(self, ca: str, channel_key: str) -> None:
        now = _now_utc()
        arr = self.state.mentions_by_ca.setdefault(ca, [])
        arr.append(Mention(now, channel_key, 3, 1.0))
        # first seen timestamp
        if ca not in self.state.first_seen_ts:
            self.state.first_seen_ts[ca] = now

        # Keep only last 3 hours
        three_hours_ago = now - timedelta(hours=3)
        self.state.mentions_by_ca[ca] = [m for m in arr if m.timestamp_utc >= three_hours_ago]

        # Compute decayed score and unique recent channels
        decayed_sum = 0.0
        unique_channels_recent: Set[str] = set()
        for m in self.state.mentions_by_ca[ca]:
            age_min = (now - m.timestamp_utc).total_seconds() / 60.0
            decayed_sum += m.weight * _decay_multiplier(age_min)
            if age_min <= OVERLAP_WINDOW_MIN:
                unique_channels_recent.add(m.channel)

        # Compute short-window velocities regardless of market/safety
        def mentions_in_window(minutes: int) -> int:
            cutoff = now - timedelta(minutes=minutes)
            return sum(1 for m in self.state.mentions_by_ca[ca] if m.timestamp_utc >= cutoff)

        vel5 = mentions_in_window(VEL5_WINDOW_MIN)
        vel10 = mentions_in_window(VEL10_WINDOW_MIN)

        # Market and safety data
        mint_revoked, freeze_revoked = await self.ensure_safety_checked(ca)
        dex = self.state.dex_cache.get(ca)
        if not dex or (now - dex.get('ts', now)).total_seconds() > 60:
            d = await get_dex_metrics(ca)
            d['ts'] = now
            self.state.dex_cache[ca] = d
            dex = d

        safe_ok = (mint_revoked and freeze_revoked) if MINT_SAFETY_REQUIRED else True
        liquidity_usd = float(dex.get('liquidity_usd', 0) if dex else 0)
        if liquidity_usd > 0:
            prev_peak = self.state.peak_liquidity_usd.get(ca, 0.0)
            if liquidity_usd > prev_peak:
                self.state.peak_liquidity_usd[ca] = liquidity_usd
        volume24_usd = float(dex.get('volume24_usd', 0) if dex else 0)
        symbol = (dex or {}).get('symbol')
        market_cap_usd = float((dex or {}).get('market_cap_usd') or 0)
        txns_h1_total = int((dex or {}).get('txns_h1_total') or 0)
        bs_ratio = float((dex or {}).get('buy_sell_ratio_h1') or 0)
        price_change_m15 = float((dex or {}).get('price_change_m15') or 0)
        pair_created_ms = (dex or {}).get('pair_created_ms')
        trending = bool((dex or {}).get('trending') or False)

        market_sane = liquidity_usd >= LIQ_MIN_USD and volume24_usd >= VOL24_MIN_USD

        classification: Optional[str] = None
        k_unique = len(unique_channels_recent)
        if safe_ok:
            # Tier 2 (Confirmation) — age 30–90 min window
            age_min = None
            if pair_created_ms:
                try:
                    age_min = max(0.0, (now - datetime.fromtimestamp(pair_created_ms / 1000.0, tz=timezone.utc)).total_seconds() / 60.0)
                except Exception:
                    age_min = None
            if age_min is None and ca in self.state.first_seen_ts:
                age_min = (now - self.state.first_seen_ts[ca]).total_seconds() / 60.0

            if age_min is not None and T2_AGE_MIN_MINUTES <= age_min <= T2_AGE_MAX_MINUTES:
                # Holders and whales check using RPC
                holders_ok = False
                try:
                    holders_ok = await self.holders_and_whales_ok(ca)
                except Exception:
                    holders_ok = False

                peak_liq = self.state.peak_liquidity_usd.get(ca, liquidity_usd)
                drawdown_pct = 0.0
                if peak_liq > 0:
                    drawdown_pct = max(0.0, (peak_liq - liquidity_usd) / peak_liq * 100.0)

                if (
                    holders_ok and
                    liquidity_usd >= max(LIQ_MIN_USD, T2_LIQ_MIN_USD) and
                    drawdown_pct <= T2_LIQ_DRAWDOWN_MAX_PCT and
                    txns_h1_total >= T2_TXNS_H1_MIN and
                    bs_ratio >= T2_BUY_SELL_RATIO_MIN and
                    len(self.state.vip_holders_by_ca.get(ca, set())) >= 1
                ):
                    classification = 'T2'

            # Tier 3 (Momentum) — 2–4 hour window after launch
            if not classification:
                if age_min is None and ca in self.state.first_seen_ts:
                    age_min = (now - self.state.first_seen_ts[ca]).total_seconds() / 60.0
                price_ok = True
                if 'price_usd' in (dex or {}) and ca in self.state.t1_price_usd:
                    try:
                        cur = float(dex.get('price_usd') or 0)
                        base = float(self.state.t1_price_usd.get(ca) or 0)
                        if cur > 0 and base > 0:
                            multiple = cur / base
                            price_ok = (multiple >= T3_PRICE_MIN_X and multiple < T3_PRICE_MAX_X)
                    except Exception:
                        price_ok = True
                trend_ok = (price_change_m15 > 0.0) if T3_POS_TREND_REQUIRED else True
                if (
                    (age_min is None or (T3_AGE_MIN_MINUTES <= age_min <= T3_AGE_MAX_MINUTES)) and
                    market_cap_usd >= T3_MCAP_MIN_USD and
                    volume24_usd >= T3_VOL24_MIN_USD and
                    price_ok and
                    trend_ok
                ):
                    # holders threshold for T3 using RPC
                    holders3_ok = False
                    try:
                        # Use RPC approximation for T3 holder check
                        holders3_ok = await self.holders_and_whales_ok(ca)
                    except Exception:
                        holders3_ok = False
                    if holders3_ok:
                        classification = 'T3'

        # T1: social consensus only — requires only unique channels threshold
        if not classification and k_unique >= MIN_UNIQUE_CHANNELS_T1:
            classification = 'T1'

        if not classification:
            return

        prev = self.state.last_rank_sent.get(ca)
        order = {'T1': 1, 'T2': 2, 'T3': 3}
        if prev and order.get(prev, 0) >= order.get(classification, 0):
            return

        if classification == 'T1' and ca not in self.state.t1_price_usd:
            if dex and dex.get('price_usd'):
                try:
                    self.state.t1_price_usd[ca] = float(dex['price_usd'])
                except Exception:
                    pass

        channels_line = _summarize_channels(self.state.mentions_by_ca.get(ca, []))
        holders_str = '-'
        # Note: Holder count not available without Birdeye API

        msg = (
            f"{'🔥 Consensus T1' if classification=='T1' else ('🚀 UPGRADE: T2' if classification=='T2' else '🚀🚀 UPGRADE: T3')} — ${symbol or '?'}\n"
            f"CA: {ca} ({ca[:4]}...{ca[-4:]})\n"
            f"Mentions: {len(self.state.mentions_by_ca.get(ca, []))} | Unique {OVERLAP_WINDOW_MIN}m: {len(unique_channels_recent)} ({channels_line})\n"
            f"Velocity: {vel5}/{VEL5_WINDOW_MIN}m, {vel10}/{VEL10_WINDOW_MIN}m | VIP: {len(self.state.vip_holders_by_ca.get(ca, set()))}\n"
            f"Liquidity: ${int(liquidity_usd):,} | Vol24: ${int(volume24_usd):,} | Mcap: ${int(market_cap_usd):,} | Holders: {holders_str}\n"
            f"Txns(h1): {txns_h1_total} | Buy/Sell: {bs_ratio:.2f} | 15m: {price_change_m15:+.1f}%{' | 🔥Trending' if trending else ''}\n"
            f"Safety: {'✅' if mint_revoked else '❌'} Mint revoked, {'✅' if freeze_revoked else '❌'} Freeze revoked\n"
        )

        await self.send_message(ca, classification, msg)
        self.state.last_rank_sent[ca] = classification
        logger.info(f"Consensus alert sent [{classification}] for {ca} (unique={len(unique_channels_recent)})")

        # Record a structured signal snapshot for analytics
        try:
            if self.stats:
                ev = SignalEvent(
                    ts_utc=datetime.now(timezone.utc).isoformat(),
                    ca=ca,
                    symbol=symbol,
                    classification=classification,
                    source_channels=[m.channel for m in self.state.mentions_by_ca.get(ca, [])[:5]],
                    uniques_OverlapMin=len(set([m.channel for m in self.state.mentions_by_ca.get(ca, [])])),
                    mentions_total=len(self.state.mentions_by_ca.get(ca, [])),
                    liquidity_usd=liquidity_usd,
                    volume24_usd=volume24_usd,
                    market_cap_usd=market_cap_usd,
                    txns_h1_total=txns_h1_total,
                    buy_sell_ratio_h1=bs_ratio,
                    price_change_m15=price_change_m15,
                    price_usd=float(dex.get('price_usd') or 0) if dex and dex.get('price_usd') else None,
                )
                await self.stats.record_signal(ev)
        except Exception:
            pass


def parse_mint_safety(data: bytes) -> Tuple[bool, bool]:
    try:
        if len(data) < 82:
            return False, False
        mint_auth_opt = int.from_bytes(data[0:4], 'little')
        freeze_auth_opt = int.from_bytes(data[45:49], 'little')
        return (mint_auth_opt == 0), (freeze_auth_opt == 0)
    except Exception:
        return False, False


