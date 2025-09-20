import asyncio
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config.config import (
    ENABLE_STATS,
    STATS_DIR,
    STATS_FILE_SIGNALS,
    STATS_FILE_OUTCOMES,
    STATS_ROI_HORIZONS_MIN,
    STATS_DAILY_ROLLOVER_HOUR_UTC,
    STATS_DB_PATH,
    STATS_SNAPSHOT_INTERVAL_SEC,
)
from config.config import STATS_JSONL_MAX_BYTES, STATS_MAX_JSONL_FILES
from bot.phanes import phanes_forward_signal, phanes_forward_outcome, phanes_is_enabled


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


@dataclass
class SignalEvent:
    ts_utc: str
    ca: str
    symbol: Optional[str]
    classification: str
    source_channels: List[str]
    uniques_OverlapMin: int
    mentions_total: int
    liquidity_usd: float
    volume24_usd: float
    market_cap_usd: float
    txns_h1_total: int
    buy_sell_ratio_h1: float
    price_change_m15: float
    price_usd: Optional[float]


@dataclass
class OutcomeEvent:
    ts_utc: str
    ca: str
    horizon_min: int
    roi_pct: float
    price_start_usd: Optional[float]
    price_end_usd: Optional[float]


class StatsRecorder:
    def __init__(self) -> None:
        self.enabled = ENABLE_STATS
        self.dir = STATS_DIR
        self.signals_path = os.path.join(self.dir, STATS_FILE_SIGNALS)
        self.outcomes_path = os.path.join(self.dir, STATS_FILE_OUTCOMES)
        self.roi_horizons_min = STATS_ROI_HORIZONS_MIN
        _ensure_dir(self.dir)
        # in-memory start prices by CA to evaluate ROIs
        self._start_price_by_ca: Dict[str, float] = {}
        self._db_path = STATS_DB_PATH
        self._initialized = False
        self._phanes_enabled = phanes_is_enabled()

    async def init(self) -> None:
        if self._initialized or not self.enabled:
            return
        import aiosqlite
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT,
                    ca TEXT,
                    symbol TEXT,
                    classification TEXT,
                    source_channels TEXT,
                    uniques_overlap_min INTEGER,
                    mentions_total INTEGER,
                    liquidity_usd REAL,
                    volume24_usd REAL,
                    market_cap_usd REAL,
                    txns_h1_total INTEGER,
                    buy_sell_ratio_h1 REAL,
                    price_change_m15 REAL,
                    price_usd REAL
                )
                """
            )
            # Time-series snapshots for market data
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT,
                    ca TEXT,
                    price_usd REAL,
                    liquidity_usd REAL,
                    volume24_usd REAL,
                    volume1h_usd REAL,
                    market_cap_usd REAL,
                    txns_h1_total INTEGER,
                    buy_sell_ratio_h1 REAL,
                    price_change_m5 REAL,
                    price_change_m15 REAL,
                    price_change_h1 REAL,
                    pair_created_ms INTEGER,
                    trending INTEGER,
                    UNIQUE(ts_utc, ca) ON CONFLICT IGNORE
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_snap_ca_ts ON snapshots(ca, ts_utc)")
            # Mentions table for raw Telegram detections
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS mentions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT,
                    ca TEXT,
                    channel TEXT,
                    message_id TEXT,
                    UNIQUE(ts_utc, ca, channel) ON CONFLICT IGNORE
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_mentions_ca_ts ON mentions(ca, ts_utc)")
            # Basic coin directory
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS coins (
                    ca TEXT PRIMARY KEY,
                    chain TEXT NOT NULL DEFAULT 'solana',
                    first_seen_ts TEXT,
                    pair_created_ms INTEGER,
                    symbol TEXT,
                    last_seen_ts TEXT
                )
                """
            )
            # Holders / concentration snapshots
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS holders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT,
                    ca TEXT,
                    supply REAL,
                    largest_wallet_pct REAL,
                    approx_unique_holders INTEGER,
                    UNIQUE(ts_utc, ca) ON CONFLICT REPLACE
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_holders_ca_ts ON holders(ca, ts_utc)")
            # VIP holder evidence
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS vip_holders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT,
                    ca TEXT,
                    wallet TEXT,
                    UNIQUE(ts_utc, ca, wallet) ON CONFLICT IGNORE
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT,
                    ca TEXT,
                    horizon_min INTEGER,
                    roi_pct REAL,
                    price_start_usd REAL,
                    price_end_usd REAL
                )
                """
            )
            await db.commit()
        self._initialized = True

    async def record_signal(self, s: SignalEvent) -> None:
        if not self.enabled:
            return
        if s.price_usd is not None and s.price_usd > 0:
            self._start_price_by_ca[s.ca] = s.price_usd
        obj = asdict(s)
        await asyncio.to_thread(self._append_jsonl, self.signals_path, obj)
        await asyncio.to_thread(self._rotate_jsonl_if_needed, self.signals_path)
        await self._insert_signal_db(s)
        # Forward to Phanes if configured (fire-and-forget)
        if self._phanes_enabled:
            try:
                await phanes_forward_signal(obj)
            except Exception:
                pass

    async def record_outcome(self, o: OutcomeEvent) -> None:
        if not self.enabled:
            return
        obj = asdict(o)
        await asyncio.to_thread(self._append_jsonl, self.outcomes_path, obj)
        await asyncio.to_thread(self._rotate_jsonl_if_needed, self.outcomes_path)
        await self._insert_outcome_db(o)
        if self._phanes_enabled:
            try:
                await phanes_forward_outcome(obj)
            except Exception:
                pass

    # ============ New helpers: mentions, coins, snapshots, holders, vip ============
    async def record_mention(self, ts_utc: str, ca: str, channel: str, message_id: Optional[str] = None) -> None:
        if not self.enabled:
            return
        import aiosqlite
        await self.init()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO mentions(ts_utc, ca, channel, message_id) VALUES(?, ?, ?, ?)",
                (ts_utc, ca, channel, message_id),
            )
            await db.commit()

    async def upsert_coin(self, ca: str, symbol: Optional[str], pair_created_ms: Optional[int], seen_ts: str) -> None:
        if not self.enabled:
            return
        import aiosqlite
        await self.init()
        async with aiosqlite.connect(self._db_path) as db:
            # If not exists, create with first_seen_ts
            await db.execute(
                "INSERT OR IGNORE INTO coins(ca, first_seen_ts, last_seen_ts, symbol, pair_created_ms) VALUES(?, ?, ?, ?, ?)",
                (ca, seen_ts, seen_ts, symbol, pair_created_ms or 0),
            )
            # Always update last_seen, and optionally symbol/pair_created_ms
            await db.execute(
                "UPDATE coins SET last_seen_ts = ?, symbol = COALESCE(?, symbol), pair_created_ms = CASE WHEN ? > 0 THEN ? ELSE pair_created_ms END WHERE ca = ?",
                (seen_ts, symbol, int(pair_created_ms or 0), int(pair_created_ms or 0), ca),
            )
            await db.commit()

    async def record_snapshot(self, ca: str, metrics: Dict[str, Any], ts_utc: Optional[str] = None) -> None:
        if not self.enabled:
            return
        import aiosqlite
        from datetime import datetime, timezone
        await self.init()
        ts = ts_utc or datetime.now(timezone.utc).isoformat()
        await self.upsert_coin(ca, metrics.get('symbol'), metrics.get('pair_created_ms'), ts)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO snapshots(
                    ts_utc, ca, price_usd, liquidity_usd, volume24_usd, volume1h_usd, market_cap_usd,
                    txns_h1_total, buy_sell_ratio_h1, price_change_m5, price_change_m15, price_change_h1,
                    pair_created_ms, trending
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    ca,
                    float(metrics.get('price_usd') or 0) if metrics.get('price_usd') is not None else None,
                    float(metrics.get('liquidity_usd') or 0),
                    float(metrics.get('volume24_usd') or 0),
                    float(metrics.get('volume1h_usd') or 0),
                    float(metrics.get('market_cap_usd') or 0),
                    int(metrics.get('txns_h1_total') or 0),
                    float(metrics.get('buy_sell_ratio_h1') or 0),
                    float(metrics.get('price_change_m5') or 0),
                    float(metrics.get('price_change_m15') or 0),
                    float(metrics.get('price_change_h1') or 0),
                    int(metrics.get('pair_created_ms') or 0),
                    1 if metrics.get('trending') else 0,
                ),
            )
            await db.commit()

    async def record_holders(self, ca: str, supply: float, largest_wallet_pct: float, approx_unique_holders: int, ts_utc: Optional[str] = None) -> None:
        if not self.enabled:
            return
        import aiosqlite
        from datetime import datetime, timezone
        await self.init()
        ts = ts_utc or datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO holders(ts_utc, ca, supply, largest_wallet_pct, approx_unique_holders) VALUES(?, ?, ?, ?, ?)",
                (ts, ca, float(supply or 0), float(largest_wallet_pct or 0), int(approx_unique_holders or 0)),
            )
            await db.commit()

    async def record_vip_holder(self, ca: str, wallet: str, ts_utc: Optional[str] = None) -> None:
        if not self.enabled:
            return
        import aiosqlite
        from datetime import datetime, timezone
        await self.init()
        ts = ts_utc or datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO vip_holders(ts_utc, ca, wallet) VALUES(?, ?, ?)",
                (ts, ca, wallet),
            )
            await db.commit()

    async def maybe_record_outcomes_from_snapshots(self, ca: str) -> None:
        """Compute horizon ROIs using first signal price vs future snapshots. Idempotent."""
        if not self.enabled:
            return
        import aiosqlite
        from datetime import datetime
        await self.init()
        async with aiosqlite.connect(self._db_path) as db:
            # Determine base signal timestamp and start price
            cur = await db.execute("SELECT MIN(ts_utc) FROM signals WHERE ca = ?", (ca,))
            row = await cur.fetchone()
            base_ts = row[0] if row and row[0] else None
            await cur.close()
            if not base_ts:
                return
            start_price = self._start_price_by_ca.get(ca)
            if not start_price:
                # fallback to snapshot at or before base_ts
                cur = await db.execute(
                    "SELECT price_usd FROM snapshots WHERE ca = ? AND ts_utc <= ? ORDER BY ts_utc DESC LIMIT 1",
                    (ca, base_ts),
                )
                r = await cur.fetchone()
                await cur.close()
                if not r or r[0] is None or float(r[0]) <= 0:
                    return
                start_price = float(r[0])
            # For each horizon, if not recorded, find the nearest snapshot at or after target time
            for minutes in self.roi_horizons_min:
                cur = await db.execute(
                    "SELECT COUNT(1) FROM outcomes WHERE ca = ? AND horizon_min = ? AND ts_utc >= ?",
                    (ca, int(minutes), base_ts),
                )
                exists = (await cur.fetchone())[0] > 0
                await cur.close()
                if exists:
                    continue
                # find price after horizon
                cur = await db.execute(
                    "SELECT ts_utc, price_usd FROM snapshots WHERE ca = ? AND ts_utc >= datetime(?, '+' || ? || ' minutes') AND price_usd IS NOT NULL ORDER BY ts_utc ASC LIMIT 1",
                    (ca, base_ts, int(minutes)),
                )
                snap = await cur.fetchone()
                await cur.close()
                if not snap:
                    continue
                end_price = float(snap[1] or 0)
                if end_price <= 0:
                    continue
                roi_pct = (end_price - start_price) / start_price * 100.0
                o = OutcomeEvent(ts_utc=snap[0], ca=ca, horizon_min=int(minutes), roi_pct=roi_pct,
                                 price_start_usd=float(start_price), price_end_usd=end_price)
                await self.record_outcome(o)

    def get_start_price(self, ca: str) -> Optional[float]:
        return self._start_price_by_ca.get(ca)

    def _append_jsonl(self, path: str, obj: Dict[str, Any]) -> None:
        _ensure_dir(os.path.dirname(path) or ".")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, separators=(",", ":")) + "\n")

    def _rotate_jsonl_if_needed(self, path: str) -> None:
        try:
            if not os.path.exists(path):
                return
            size = os.path.getsize(path)
            if size < STATS_JSONL_MAX_BYTES:
                return
            # rotate old files: path.N -> path.(N+1)
            for idx in range(STATS_MAX_JSONL_FILES - 1, 0, -1):
                older = f"{path}.{idx}"
                newer = f"{path}.{idx + 1}"
                if os.path.exists(older):
                    try:
                        if idx + 1 >= STATS_MAX_JSONL_FILES and os.path.exists(newer):
                            os.remove(newer)
                        os.replace(older, newer)
                    except Exception:
                        continue
            try:
                os.replace(path, f"{path}.1")
            except Exception:
                pass
        except Exception:
            pass

    async def _insert_signal_db(self, s: SignalEvent) -> None:
        if not self.enabled:
            return
        import aiosqlite
        await self.init()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO signals (
                    ts_utc, ca, symbol, classification, source_channels, uniques_overlap_min, mentions_total,
                    liquidity_usd, volume24_usd, market_cap_usd, txns_h1_total, buy_sell_ratio_h1, price_change_m15, price_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    s.ts_utc, s.ca, s.symbol, s.classification, json.dumps(s.source_channels, ensure_ascii=False),
                    s.uniques_OverlapMin, s.mentions_total, s.liquidity_usd, s.volume24_usd, s.market_cap_usd,
                    s.txns_h1_total, s.buy_sell_ratio_h1, s.price_change_m15, s.price_usd,
                ),
            )
            await db.commit()

    async def _insert_outcome_db(self, o: OutcomeEvent) -> None:
        if not self.enabled:
            return
        import aiosqlite
        await self.init()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO outcomes (ts_utc, ca, horizon_min, roi_pct, price_start_usd, price_end_usd)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (o.ts_utc, o.ca, o.horizon_min, o.roi_pct, o.price_start_usd, o.price_end_usd),
            )
            await db.commit()


async def evaluate_roi_for_ca(ca: str, minutes: int, fetch_price_fn) -> Optional[OutcomeEvent]:
    try:
        start_price = await fetch_price_fn(ca, at_start=True)
        end_price = await fetch_price_fn(ca, at_start=False)
        if start_price is None or end_price is None or start_price <= 0:
            return None
        roi_pct = (end_price - start_price) / start_price * 100.0
        return OutcomeEvent(ts_utc=_utc_now_iso(), ca=ca, horizon_min=minutes, roi_pct=roi_pct,
                            price_start_usd=start_price, price_end_usd=end_price)
    except Exception:
        return None



