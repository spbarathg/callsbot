import json
import logging
import os
import asyncio
from typing import List, Set, Dict

from config.config import VIP_WALLETS, VIP_WALLETS_FILE, VIP_MAX_WALLETS, VIP_POLL_SECONDS
from bot.apis import solana_rpc

logger = logging.getLogger(__name__)


def load_vip_wallets() -> List[str]:
    wallets: List[str] = []
    try:
        wallets.extend(VIP_WALLETS)
        if VIP_WALLETS_FILE and os.path.exists(VIP_WALLETS_FILE):
            with open(VIP_WALLETS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, str):
                        wallets.append(item)
                    elif isinstance(item, dict):
                        addr = item.get('trackedWalletAddress') or item.get('address')
                        if addr:
                            wallets.append(str(addr))
        # Dedupe preserve order
        seen: Set[str] = set()
        deduped: List[str] = []
        for w in wallets:
            if w and w not in seen:
                seen.add(w)
                deduped.append(w)
        if VIP_MAX_WALLETS > 0:
            deduped = deduped[:VIP_MAX_WALLETS]
        return deduped
    except Exception as e:
        logger.warning(f"Failed to load VIP wallets: {e}")
        return []


async def count_vip_holders_for_token(mint: str, vip_wallets: List[str]) -> Set[str]:
    holders: Set[str] = set()
    for owner in vip_wallets:
        try:
            res = await solana_rpc("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}])
            for acc in (res.get('value') or []):
                amount = ((acc.get('account') or {}).get('data') or {}).get('parsed', {}).get('info', {}).get('tokenAmount', {}).get('uiAmount', 0)
                try:
                    if amount and float(amount) > 0:
                        holders.add(owner)
                        break
                except Exception:
                    continue
        except Exception:
            continue
    return holders


async def vip_watcher_loop(mentions_by_ca: Dict[str, list], vip_holders_by_ca: Dict[str, Set[str]], stop_event: asyncio.Event) -> None:
    vip_wallets = load_vip_wallets()
    if not vip_wallets:
        logger.info("No VIP wallets configured; VIP watcher idle")
        return
    logger.info(f"VIP wallets loaded: {len(vip_wallets)} (poll {VIP_POLL_SECONDS}s)")
    while not stop_event.is_set():
        try:
            cas = list(mentions_by_ca.keys())
            if not cas:
                await asyncio.sleep(VIP_POLL_SECONDS)
                continue
            for ca in cas:
                holders = await count_vip_holders_for_token(ca, vip_wallets)
                if holders:
                    vip_holders_by_ca[ca] = holders
        except Exception as e:
            logger.warning(f"VIP watcher error: {e}")
        await asyncio.sleep(VIP_POLL_SECONDS)


