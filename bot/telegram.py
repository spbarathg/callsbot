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
)
from bot.evaluator import Evaluator

logger = logging.getLogger(__name__)


class Bot:
    def __init__(self) -> None:
        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        self.evaluator: Optional[Evaluator] = None
        self.coin_counts: dict[str, int] = {}
        self.coin_tier_state: dict[str, int] = {}
        self.last_t1_sent_utc: dict[str, datetime] = {}
        self.last_reset_utc = datetime.utcnow()

    async def start(self) -> None:
        await self.client.start()
        self.evaluator = Evaluator(self._send_evaluator_message)
        self.client.add_event_handler(self._on_message, events.NewMessage(chats=MONITORED_GROUPS if MONITORED_GROUPS else None))
        logger.info("Client started. Monitoring groups... Press Ctrl+C to stop.")

    async def stop(self) -> None:
        await self.client.disconnect()

    def _maybe_reset_counts(self) -> None:
        if HOT_RESET_HOURS <= 0:
            return
        if datetime.utcnow() - self.last_reset_utc >= timedelta(hours=HOT_RESET_HOURS):
            self.coin_counts = {}
            self.last_reset_utc = datetime.utcnow()
            logger.info("Hot counts reset due to window elapsed")

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


