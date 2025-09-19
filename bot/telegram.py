import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError

from config.config import (
    API_ID,
    API_HASH,
    SESSION_NAME,
    MONITORED_GROUPS,
    TARGET_GROUP,
    ENABLE_EVALUATOR,
    ENABLE_TIERED_ALERTS,
    T1_IMMEDIATE,
    T2_THRESHOLD_CALLS,
    T3_THRESHOLD_CALLS,
    COOLDOWN_MINUTES_T1,
    HOT_THRESHOLD,
    HOT_RESET_HOURS,
    CA_PATTERN,
    STATE_FILE,
    STATE_SAVE_SECONDS,
    HEALTH_LOG_SECONDS,
)
from bot.evaluator import Evaluator
from bot.utils import read_json, write_json_atomic

logger = logging.getLogger(__name__)


class Bot:
    def __init__(self) -> None:
        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        self.evaluator: Optional[Evaluator] = None
        self.coin_counts: dict[str, int] = {}
        self.coin_tier_state: dict[str, int] = {}
        self.last_t1_sent_utc: dict[str, datetime] = {}
        self.last_reset_utc = datetime.utcnow()
        self._bg_tasks: set[asyncio.Task] = set()
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        await self.client.start()
        self.evaluator = Evaluator(self._send_evaluator_message)
        await self._load_state()
        self.client.add_event_handler(self._on_message, events.NewMessage(chats=MONITORED_GROUPS if MONITORED_GROUPS else None))
        logger.info("Client started. Monitoring groups... Press Ctrl+C to stop.")
        self._bg_tasks.add(asyncio.create_task(self._state_saver_loop()))
        self._bg_tasks.add(asyncio.create_task(self._health_loop()))

    async def stop(self) -> None:
        self._stop_event.set()
        for t in list(self._bg_tasks):
            t.cancel()
        try:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        finally:
            self._bg_tasks.clear()
        await self._save_state()
        await self.client.disconnect()

    def _maybe_reset_counts(self) -> None:
        if HOT_RESET_HOURS <= 0:
            return
        if datetime.utcnow() - self.last_reset_utc >= timedelta(hours=HOT_RESET_HOURS):
            self.coin_counts = {}
            self.last_reset_utc = datetime.utcnow()
            logger.info("Hot counts reset due to window elapsed")
        # Guardrail: cap coin_counts size to avoid unbounded memory
        max_entries = 5000
        if len(self.coin_counts) > max_entries:
            # keep top-N by count
            top = sorted(self.coin_counts.items(), key=lambda kv: kv[1], reverse=True)[:1000]
            self.coin_counts = {k: v for k, v in top}

    async def _on_message(self, event: events.NewMessage.Event) -> None:
        try:
            self._maybe_reset_counts()
            if getattr(event, "is_reply", False) or getattr(event, "fwd_from", None):
                return
            sender = await event.get_chat()
            group_name = getattr(sender, "title", "Unknown Group")
            username = getattr(sender, "username", None)
            channel_key = ("@" + username) if username else group_name

            text = event.raw_text or ""
            match = CA_PATTERN.search(text)
            if not match:
                return
            ca = match.group(1)
            new_count = self.coin_counts.get(ca, 0) + 1
            self.coin_counts[ca] = new_count
            logger.info(f"Detected CA {ca} in {group_name} (Count: {new_count})")

            if ENABLE_EVALUATOR and self.evaluator:
                await self.evaluator.process_mention(ca, channel_key)
                # prune periodically to avoid growth
                if self.evaluator and (len(self.evaluator.state.mentions_by_ca) % 25 == 0):
                    self.evaluator.prune_memory()
            elif ENABLE_TIERED_ALERTS:
                await self._maybe_send_tiered_alert(ca, group_name, new_count)
            else:
                if new_count == HOT_THRESHOLD:
                    await self._send_alert_message(ca, tier_label=f"T3 x{HOT_THRESHOLD}", header_prefix=">>> ALERT", group_name=group_name)
        except Exception as e:
            logger.exception(f"Handler error: {e}")

    async def _send_evaluator_message(self, ca: str, classification: str, body: str) -> None:
        try:
            links = self._build_links_line(ca)
            await self.client.send_message(TARGET_GROUP, body + "\n" + links, link_preview=False)
        except FloodWaitError as e:
            logger.warning(f"Flood wait {e.seconds}s on alert send; delaying...")
            await asyncio.sleep(e.seconds)
        except RPCError as e:
            logger.error(f"Telegram RPC error while sending alert: {e}")

    def _build_links_line(self, ca: str) -> str:
        ds = f"https://dexscreener.com/solana/{ca}"
        be = f"https://birdeye.so/token/{ca}?chain=solana"
        jup = f"https://jup.ag/swap/SOL-{ca}"
        return f"Links: DexScreener {ds} | Birdeye {be} | Jupiter {jup}"

    async def _send_alert_message(self, ca: str, tier_label: str, header_prefix: str, group_name: str) -> None:
        short = f"{ca[:4]}...{ca[-4:]}"
        links = self._build_links_line(ca)
        msg = (
            f"{header_prefix} [{tier_label}]\n"
            f"CA: {ca} ({short})\n"
            f"Source: {group_name}\n"
            f"{links}"
        )
        try:
            await self.client.send_message(TARGET_GROUP, msg, link_preview=False)
            logger.info(f"Alert sent [{tier_label}] for {ca}")
        except FloodWaitError as e:
            logger.warning(f"Flood wait {e.seconds}s on alert send; delaying...")
            await asyncio.sleep(e.seconds)
        except RPCError as e:
            logger.error(f"Telegram RPC error while sending alert: {e}")

    async def _maybe_send_tiered_alert(self, ca: str, group_name: str, count: int) -> None:
        highest = self.coin_tier_state.get(ca, 0)
        if T1_IMMEDIATE and count == 1 and highest < 1:
            now = datetime.utcnow()
            last_ts = self.last_t1_sent_utc.get(ca)
            if COOLDOWN_MINUTES_T1 > 0 and last_ts is not None:
                if (now - last_ts) < timedelta(minutes=COOLDOWN_MINUTES_T1):
                    return
            await self._send_alert_message(ca, tier_label="T1 Fresh", header_prefix=">>> SIGNAL", group_name=group_name)
            self.coin_tier_state[ca] = 1
            self.last_t1_sent_utc[ca] = now
            return
        if count >= T2_THRESHOLD_CALLS and highest < 2:
            await self._send_alert_message(ca, tier_label=f"T2 Heating ({count} mentions)", header_prefix=">>> SIGNAL", group_name=group_name)
            self.coin_tier_state[ca] = 2
            return
        if count >= T3_THRESHOLD_CALLS and highest < 3:
            await self._send_alert_message(ca, tier_label=f"T3 GO ({count} mentions)", header_prefix=">>> SIGNAL", group_name=group_name)
            self.coin_tier_state[ca] = 3
            return

    async def _load_state(self) -> None:
        try:
            data = await read_json(STATE_FILE)
            if not data:
                return
            if isinstance(data, dict):
                lrs = data.get("last_rank_sent")
                t1 = data.get("t1_price_usd")
                first_seen = data.get("first_seen_ts")
                peak_liq = data.get("peak_liquidity_usd")
                tiers = data.get("coin_tier_state")
                last_t1 = data.get("last_t1_sent_utc")
                if self.evaluator:
                    self.evaluator.load_persisted_state({
                        "last_rank_sent": lrs,
                        "t1_price_usd": t1,
                        "first_seen_ts": first_seen,
                        "peak_liquidity_usd": peak_liq,
                    })
                if isinstance(tiers, dict):
                    self.coin_tier_state.update({str(k): int(v) for k, v in tiers.items()})
                if isinstance(last_t1, dict):
                    # store as ISO strings
                    for k, v in last_t1.items():
                        try:
                            self.last_t1_sent_utc[str(k)] = datetime.fromisoformat(str(v))
                        except Exception:
                            continue
            logger.info("State loaded")
        except Exception as e:
            logger.warning(f"Failed to load state: {e}")

    async def _save_state(self) -> None:
        try:
            payload = {
                **(self.evaluator.to_persisted_state() if self.evaluator else {}),
                "coin_tier_state": dict(self.coin_tier_state),
                "last_t1_sent_utc": {k: v.isoformat() for k, v in self.last_t1_sent_utc.items()},
            }
            await write_json_atomic(STATE_FILE, payload)
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")

    async def _state_saver_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(STATE_SAVE_SECONDS)
                await self._save_state()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning(f"State saver error: {e}")

    async def _health_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(HEALTH_LOG_SECONDS)
                num_groups = len(MONITORED_GROUPS or [])
                num_coins = len(self.coin_counts)
                lrs = len(self.evaluator.state.last_rank_sent) if self.evaluator else 0
                mentions_keys = len(self.evaluator.state.mentions_by_ca) if self.evaluator else 0
                logger.info(
                    f"HEALTH groups={num_groups} coins_seen={num_coins} mentions_tracked={mentions_keys} last_ranked={lrs}"
                )
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning(f"Health loop error: {e}")


