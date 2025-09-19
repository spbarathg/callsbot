import logging
from datetime import datetime, timedelta, timezone
from math import exp
from typing import Any, Dict, List, Optional, Set, Tuple

from config.config import (
    OVERLAP_WINDOW_MIN,
    MIN_UNIQUE_CHANNELS_T1,
    T3_MIN_UNIQUE_CHANNELS,
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
    ENABLE_BIRDEYE,
    BIRDEYE_API_KEY,
)
from bot.apis import get_birdeye_overview, get_dex_metrics, solana_get_account_info, solana_rpc

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


class Evaluator:
    def __init__(self, send_message_fn) -> None:
        self.state = EvaluatorState()
        self.send_message = send_message_fn

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
            if ENABLE_BIRDEYE and BIRDEYE_API_KEY:
                d = await get_birdeye_overview(ca)
                holders = int(d.get('holder') or d.get('holders') or d.get('holders_count') or 0)
                largest = float(
                    d.get('largest_holder_percent') or
                    d.get('top_holder_percent') or
                    d.get('top_holders_percent') or
                    d.get('topHoldersPercent') or 0
                )
                return holders >= HOLDERS_THRESHOLD and largest <= LARGEST_WALLET_MAX
            # Fallback to RPC approximation
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
            return (unique_holders >= HOLDERS_THRESHOLD and largest_pct <= LARGEST_WALLET_MAX)
        except Exception as e:
            logger.warning(f"Holders/whale check failed for {ca}: {e}")
            return False

    async def process_mention(self, ca: str, channel_key: str) -> None:
        now = _now_utc()
        arr = self.state.mentions_by_ca.setdefault(ca, [])
        arr.append(Mention(now, channel_key, 3, 1.0))

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
        volume24_usd = float(dex.get('volume24_usd', 0) if dex else 0)
        symbol = (dex or {}).get('symbol')

        market_sane = liquidity_usd >= LIQ_MIN_USD and volume24_usd >= VOL24_MIN_USD

        classification: Optional[str] = None
        if safe_ok and market_sane:
            k_unique = len(unique_channels_recent)

            def mentions_in_window(minutes: int) -> int:
                cutoff = now - timedelta(minutes=minutes)
                return sum(1 for m in self.state.mentions_by_ca[ca] if m.timestamp_utc >= cutoff)

            vel5 = mentions_in_window(VEL5_WINDOW_MIN)
            vel10 = mentions_in_window(VEL10_WINDOW_MIN)

            # T3: stronger thresholds
            price_ok = True
            if 'price_usd' in (dex or {}) and ca in self.state.t1_price_usd:
                try:
                    cur = float(dex.get('price_usd') or 0)
                    base = float(self.state.t1_price_usd.get(ca) or 0)
                    if cur > 0 and base > 0:
                        multiple = cur / base
                        price_ok = (multiple >= PRICE_MULTIPLE_MIN and multiple < PRICE_MULTIPLE_MAX)
                except Exception:
                    price_ok = True

            if (
                k_unique >= T3_MIN_UNIQUE_CHANNELS and
                vel10 >= 6 and
                liquidity_usd >= 50000 and
                volume24_usd >= 200000 and
                price_ok
            ):
                classification = 'T3'
            elif (
                k_unique >= 6 and
                ((float(dex.get('volume1h_usd', 0)) >= VOL_1H_THRESHOLD) or (volume24_usd >= VOL_24H_THRESHOLD)) and
                liquidity_usd >= LIQ_THRESHOLD and
                (await self.holders_and_whales_ok(ca)) and
                safe_ok
            ):
                classification = 'T2'
            elif k_unique >= MIN_UNIQUE_CHANNELS_T1:
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
        holders_str = (dex or {}).get('holders', '-')  # placeholder if available in future

        msg = (
            f"{'🔥 Consensus T1' if classification=='T1' else ('🚀 UPGRADE: T2' if classification=='T2' else '🚀🚀 UPGRADE: T3')} — ${symbol or '?'}\n"
            f"CA: {ca} ({ca[:4]}...{ca[-4:]})\n"
            f"Mentions: {len(self.state.mentions_by_ca.get(ca, []))} | Unique {OVERLAP_WINDOW_MIN}m: {len(unique_channels_recent)} ({channels_line})\n"
            f"Velocity: {vel5}/{VEL5_WINDOW_MIN}m, {vel10}/{VEL10_WINDOW_MIN}m | VIP: {len(self.state.vip_holders_by_ca.get(ca, set()))}\n"
            f"Liquidity: ${int(liquidity_usd):,} | Volume: ${int(volume24_usd):,} | Holders: {holders_str}\n"
            f"Safety: {'✅' if mint_revoked else '❌'} Mint revoked, {'✅' if freeze_revoked else '❌'} Freeze revoked\n"
        )

        await self.send_message(ca, classification, msg)
        self.state.last_rank_sent[ca] = classification
        logger.info(f"Consensus alert sent [{classification}] for {ca} (unique={len(unique_channels_recent)})")


def parse_mint_safety(data: bytes) -> Tuple[bool, bool]:
    try:
        if len(data) < 82:
            return False, False
        mint_auth_opt = int.from_bytes(data[0:4], 'little')
        freeze_auth_opt = int.from_bytes(data[45:49], 'little')
        return (mint_auth_opt == 0), (freeze_auth_opt == 0)
    except Exception:
        return False, False


