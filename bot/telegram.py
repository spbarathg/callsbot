import logging
import asyncio
import re
from datetime import datetime, timedelta
from typing import Optional, List, Set

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import (
    MessageEntityCode,
    MessageEntityPre,
    MessageEntityTextUrl,
)

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
    CA_PATTERNS,
    STATE_FILE,
    STATE_SAVE_SECONDS,
    HEALTH_LOG_SECONDS,
)
from bot.evaluator import Evaluator
from bot.utils import read_json, write_json_atomic
from bot.stats import StatsRecorder, SignalEvent
from bot.metrics import messages_processed_total, alerts_sent_total, errors_total

logger = logging.getLogger(__name__)


def extract_contract_addresses_from_message(event: events.NewMessage.Event) -> Set[str]:
    """
    Extract contract addresses from various message formats and entities.
    Handles: raw text, code blocks, pre-formatted text, URLs, and other entities.
    """
    contract_addresses: Set[str] = set()
    
    # Get the message object
    message = event.message
    if not message:
        return contract_addresses
    
    # Normalize and prepare text for extraction
    raw_text = normalize_text_for_extraction(event.raw_text or "")
    message_text = normalize_text_for_extraction(getattr(message, 'text', '') or "")
    
    # Combine all text sources
    all_texts = [raw_text, message_text]
    if raw_text != message_text:
        all_texts.append(raw_text + " " + message_text)
    
    # Extract using all patterns from all text sources
    for text in all_texts:
        if not text:
            continue
            
        # Apply all regex patterns
        for pattern in CA_PATTERNS:
            try:
                matches = pattern.findall(text)
                if isinstance(matches, list):
                    for match in matches:
                        if isinstance(match, tuple):
                            # Handle patterns that return multiple groups
                            for group in match:
                                if group and isinstance(group, str):
                                    contract_addresses.add(group)
                        elif isinstance(match, str):
                            contract_addresses.add(match)
            except Exception as e:
                logger.debug(f"Error applying pattern {pattern.pattern}: {e}")
                continue
    
    # Extract from message entities
    if hasattr(message, 'entities') and message.entities:
        for entity in message.entities:
            try:
                # Handle code blocks (```code```)
                if isinstance(entity, MessageEntityCode):
                    start = entity.offset
                    length = entity.length
                    if start + length <= len(raw_text):
                        code_text = raw_text[start:start + length]
                        for pattern in CA_PATTERNS:
                            matches = pattern.findall(code_text)
                            if isinstance(matches, list):
                                for match in matches:
                                    if isinstance(match, tuple):
                                        for group in match:
                                            if group and isinstance(group, str):
                                                contract_addresses.add(group)
                                    elif isinstance(match, str):
                                        contract_addresses.add(match)
                
                # Handle pre-formatted text (```language\ncode```)
                elif isinstance(entity, MessageEntityPre):
                    start = entity.offset
                    length = entity.length
                    if start + length <= len(raw_text):
                        pre_text = raw_text[start:start + length]
                        for pattern in CA_PATTERNS:
                            matches = pattern.findall(pre_text)
                            if isinstance(matches, list):
                                for match in matches:
                                    if isinstance(match, tuple):
                                        for group in match:
                                            if group and isinstance(group, str):
                                                contract_addresses.add(group)
                                    elif isinstance(match, str):
                                        contract_addresses.add(match)
                
                # Handle text URLs (might contain contract addresses)
                elif isinstance(entity, MessageEntityTextUrl):
                    if entity.url:
                        # Check if URL contains a contract address
                        for pattern in CA_PATTERNS:
                            url_matches = pattern.findall(entity.url)
                            if isinstance(url_matches, list):
                                for match in url_matches:
                                    if isinstance(match, tuple):
                                        for group in match:
                                            if group and isinstance(group, str):
                                                contract_addresses.add(group)
                                    elif isinstance(match, str):
                                        contract_addresses.add(match)
                    
                    # Also check the text part of the URL entity
                    start = entity.offset
                    length = entity.length
                    if start + length <= len(raw_text):
                        url_text = raw_text[start:start + length]
                        for pattern in CA_PATTERNS:
                            matches = pattern.findall(url_text)
                            if isinstance(matches, list):
                                for match in matches:
                                    if isinstance(match, tuple):
                                        for group in match:
                                            if group and isinstance(group, str):
                                                contract_addresses.add(group)
                                    elif isinstance(match, str):
                                        contract_addresses.add(match)
                
                # Handle other text entities (mentions, hashtags, etc.)
                elif hasattr(entity, 'offset') and hasattr(entity, 'length'):
                    start = entity.offset
                    length = entity.length
                    if start + length <= len(raw_text):
                        entity_text = raw_text[start:start + length]
                        for pattern in CA_PATTERNS:
                            matches = pattern.findall(entity_text)
                            if isinstance(matches, list):
                                for match in matches:
                                    if isinstance(match, tuple):
                                        for group in match:
                                            if group and isinstance(group, str):
                                                contract_addresses.add(group)
                                    elif isinstance(match, str):
                                        contract_addresses.add(match)
                        
            except Exception as e:
                logger.debug(f"Error processing entity {type(entity).__name__}: {e}")
                continue
    
    # Extract from additional formats
    for text in all_texts:
        additional_addresses = extract_from_additional_formats(text)
        contract_addresses.update(additional_addresses)
    
    # Handle split addresses (addresses broken across lines or spaces)
    for text in all_texts:
        if not text:
            continue
        # Look for potential split addresses
        words = text.split()
        for i in range(len(words) - 1):
            combined = words[i] + words[i + 1]
            if 32 <= len(combined) <= 44 and re.match(r'^[1-9A-HJ-NP-Za-km-z]+$', combined):
                contract_addresses.add(combined)
    
    # Clean and validate contract addresses
    validated_addresses: Set[str] = set()
    for ca in contract_addresses:
        # Basic validation: should be 32-44 characters, Base58
        if 32 <= len(ca) <= 44 and re.match(r'^[1-9A-HJ-NP-Za-km-z]+$', ca):
            validated_addresses.add(ca)
    
    return validated_addresses


def extract_from_additional_formats(text: str) -> Set[str]:
    """
    Extract contract addresses from additional text formats and edge cases.
    """
    addresses: Set[str] = set()
    
    if not text:
        return addresses
    
    # Handle common formatting variations
    text_variants = [
        text,
        text.replace('\n', ' '),
        text.replace('\r', ' '),
        text.replace('\t', ' '),
        text.replace('  ', ' '),  # Double spaces
        text.replace('`', ''),     # Remove backticks
        text.replace('*', ''),     # Remove asterisks
        text.replace('_', ''),     # Remove underscores
        text.replace('~', ''),     # Remove tildes
    ]
    
    for variant in text_variants:
        for pattern in CA_PATTERNS:
            try:
                matches = pattern.findall(variant)
                if isinstance(matches, list):
                    for match in matches:
                        if isinstance(match, tuple):
                            for group in match:
                                if group and isinstance(group, str):
                                    addresses.add(group)
                        elif isinstance(match, str):
                            addresses.add(match)
            except Exception:
                continue
    
    return addresses


def normalize_text_for_extraction(text: str) -> str:
    """
    Normalize text to improve contract address detection.
    Handles various formatting issues and edge cases.
    """
    if not text:
        return ""
    
    # Remove common formatting characters that might interfere
    normalized = text
    
    # Handle zero-width characters
    normalized = re.sub(r'[\u200b-\u200d\ufeff]', '', normalized)
    
    # Handle various quote types
    # Standardize curly quotes to straight quotes
    normalized = normalized.replace('“', '"').replace('”', '"')
    normalized = normalized.replace('’', "'").replace('‘', "'")
    
    # Handle various dash types
    normalized = normalized.replace('–', '-').replace('—', '-')
    
    # Handle various space types
    normalized = re.sub(r'[\u00a0\u2000-\u200a\u202f\u205f\u3000]', ' ', normalized)
    
    # Normalize multiple spaces
    normalized = re.sub(r'\s+', ' ', normalized)
    
    return normalized.strip()


class Bot:
    def __init__(self) -> None:
        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        self.evaluator: Optional[Evaluator] = None
        self.stats: Optional[StatsRecorder] = StatsRecorder()
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
            messages_processed_total.inc()
            self._maybe_reset_counts()
            if getattr(event, "is_reply", False) or getattr(event, "fwd_from", None):
                return
            sender = await event.get_chat()
            group_name = getattr(sender, "title", "Unknown Group")
            username = getattr(sender, "username", None)
            channel_key = ("@" + username) if username else group_name

            # Extract contract addresses using enhanced method
            contract_addresses = extract_contract_addresses_from_message(event)
            
            if not contract_addresses:
                return
            
            # Process each detected contract address
            for ca in contract_addresses:
                new_count = self.coin_counts.get(ca, 0) + 1
                self.coin_counts[ca] = new_count
                logger.info(f"Detected CA {ca} in {group_name} (Count: {new_count})")
                # Record mention row for analytics
                try:
                    if self.stats:
                        await self.stats.record_mention(datetime.utcnow().isoformat() + "Z", ca, channel_key, str(getattr(event.message, 'id', '')))
                except Exception:
                    pass

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
                # Record a RAW detection event for analytics
                try:
                    if self.stats:
                        ev = SignalEvent(
                            ts_utc=datetime.utcnow().isoformat() + "Z",
                            ca=ca,
                            symbol=None,
                            classification="RAW",
                            source_channels=[channel_key],
                            uniques_OverlapMin=0,
                            mentions_total=self.coin_counts.get(ca, 0),
                            liquidity_usd=0.0,
                            volume24_usd=0.0,
                            market_cap_usd=0.0,
                            txns_h1_total=0,
                            buy_sell_ratio_h1=0.0,
                            price_change_m15=0.0,
                            price_usd=None,
                        )
                        await self.stats.record_signal(ev)
                except Exception:
                    pass
                        
        except Exception as e:
            errors_total.labels("telegram_handler").inc()
            logger.exception(f"Handler error: {e}")

    async def _send_evaluator_message(self, ca: str, classification: str, body: str) -> None:
        try:
            links = self._build_links_line(ca)
            await self.client.send_message(TARGET_GROUP, body + "\n" + links, link_preview=False)
            # Record structured signal for analytics
            try:
                if self.stats and self.evaluator:
                    st = self.evaluator.state
                    dex = st.dex_cache.get(ca) or {}
                    mentions = st.mentions_by_ca.get(ca, [])
                    channels = list({m.channel for m in mentions})[:5]
                    ev = SignalEvent(
                        ts_utc=datetime.utcnow().isoformat() + "Z",
                        ca=ca,
                        symbol=dex.get('symbol'),
                        classification=classification,
                        source_channels=channels,
                        uniques_OverlapMin=0,
                        mentions_total=len(mentions),
                        liquidity_usd=float(dex.get('liquidity_usd') or 0.0),
                        volume24_usd=float(dex.get('volume24_usd') or 0.0),
                        market_cap_usd=float(dex.get('market_cap_usd') or 0.0),
                        txns_h1_total=int(dex.get('txns_h1_total') or 0),
                        buy_sell_ratio_h1=float(dex.get('buy_sell_ratio_h1') or 0.0),
                        price_change_m15=float(dex.get('price_change_m15') or 0.0),
                        price_usd=(float(dex.get('price_usd')) if dex.get('price_usd') else None),
                    )
                    await self.stats.record_signal(ev)
            except Exception:
                pass
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
            try:
                alerts_sent_total.labels(tier_label).inc()
            except Exception:
                pass
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


