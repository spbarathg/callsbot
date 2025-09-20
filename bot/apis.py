import base64
import json
import logging
from typing import Any, Dict, Optional

import aiohttp

from config.config import (
    ENABLE_BIRDEYE,
    BIRDEYE_API_KEY,
    SOLANA_RPC_URLS,
    HTTP_TIMEOUT_SEC,
    HTTP_RETRIES,
    RETRY_BACKOFF_SEC,
    RPC_MAX_RPS,
)
from bot.utils import with_retries

logger = logging.getLogger(__name__)


class HttpClient:
    def __init__(self) -> None:
        self.session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SEC)
            self.session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def get_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        assert self.session is not None

        async def _do() -> Dict[str, Any]:
            async with self.session.get(url, headers=headers) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"GET {url} -> {resp.status} {text[:200]}")
                try:
                    return json.loads(text)
                except Exception:
                    return {"raw": text}

        return await with_retries(_do, HTTP_RETRIES, RETRY_BACKOFF_SEC)

    async def post_json(self, url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        assert self.session is not None
        _headers = {"Content-Type": "application/json"}
        if headers:
            _headers.update(headers)

        async def _do() -> Dict[str, Any]:
            async with self.session.post(url, json=payload, headers=_headers) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"POST {url} -> {resp.status} {text[:200]}")
                try:
                    return json.loads(text)
                except Exception:
                    return {"raw": text}

        return await with_retries(_do, HTTP_RETRIES, RETRY_BACKOFF_SEC)


http_client = HttpClient()


# ================== DexScreener ==================
async def get_dex_metrics(ca: str) -> Dict[str, Any]:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    try:
        data = await http_client.get_json(url, headers={"accept": "application/json", "user-agent": "Mozilla/5.0"})
    except Exception as e:
        logger.warning(f"Dexscreener fetch failed for {ca}: {e}")
        return {}
    pairs = (data.get('pairs') or [])
    pair = None
    for p in pairs:
        if p.get('chainId') == 'solana':
            pair = p
            break
    if not pair and pairs:
        pair = pairs[0]
    if not pair:
        return {}
    liq = ((pair.get('liquidity') or {}).get('usd')) or 0
    vol = ((pair.get('volume') or {}).get('h24')) or 0
    vol1h = ((pair.get('volume') or {}).get('h1')) or 0
    symbol = ((pair.get('baseToken') or {}).get('symbol')) or None
    price = pair.get('priceUsd')
    market_cap = pair.get('marketCap') or pair.get('fdv') or 0
    txns = pair.get('txns') or {}
    tx_h1 = (txns.get('h1') or {}) if isinstance(txns, dict) else {}
    buys_h1 = float(tx_h1.get('buys') or 0)
    sells_h1 = float(tx_h1.get('sells') or 0)
    total_h1 = buys_h1 + sells_h1
    bs_ratio_h1 = (buys_h1 / sells_h1) if sells_h1 > 0 else (buys_h1 if buys_h1 > 0 else 0)
    price_change = pair.get('priceChange') or {}
    pc_m5 = float((price_change.get('m5') or 0) if isinstance(price_change, dict) else 0)
    pc_m15 = float((price_change.get('m15') or 0) if isinstance(price_change, dict) else 0)
    pc_h1 = float((price_change.get('h1') or 0) if isinstance(price_change, dict) else 0)
    created_ms = pair.get('pairCreatedAt') or pair.get('createdAt')
    trending_score = pair.get('trendingScore') or 0
    is_hot = bool(pair.get('isHot')) or (float(trending_score or 0) > 0)
    return {
        'liquidity_usd': float(liq or 0),
        'volume24_usd': float(vol or 0),
        'volume1h_usd': float(vol1h or 0),
        'symbol': symbol,
        'price_usd': float(price or 0) if price else None,
        'market_cap_usd': float(market_cap or 0),
        'txns_h1_buys': int(buys_h1),
        'txns_h1_sells': int(sells_h1),
        'txns_h1_total': int(total_h1),
        'buy_sell_ratio_h1': float(bs_ratio_h1),
        'price_change_m5': pc_m5,
        'price_change_m15': pc_m15,
        'price_change_h1': pc_h1,
        'pair_created_ms': int(created_ms or 0) if created_ms else None,
        'trending': is_hot,
    }


# ================== Birdeye ==================
# Birdeye API removed - not working


# ================== Solana RPC ==================
_rpc_index = 0
_last_rpc_ts = 0.0


async def solana_rpc(method: str, params: list) -> Any:
    global _rpc_index, _last_rpc_ts
    if not SOLANA_RPC_URLS:
        raise RuntimeError("No Solana RPC URLs configured")
    url = SOLANA_RPC_URLS[_rpc_index % len(SOLANA_RPC_URLS)]
    _rpc_index += 1
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    # Simple token-bucket style throttle to respect RPC_MAX_RPS
    if RPC_MAX_RPS and RPC_MAX_RPS > 0:
        import time, asyncio as _asyncio
        min_interval = 1.0 / RPC_MAX_RPS
        now = time.perf_counter()
        sleep_for = _last_rpc_ts + min_interval - now
        if sleep_for > 0:
            await _asyncio.sleep(sleep_for)
        _last_rpc_ts = time.perf_counter()
    try:
        data = await http_client.post_json(url, payload)
    except Exception as e:
        raise RuntimeError(f"RPC {method} failed: {e}")
    if 'error' in data:
        raise RuntimeError(str(data['error']))
    return data.get('result')


async def solana_get_account_info(mint: str) -> Optional[bytes]:
    try:
        result = await solana_rpc("getAccountInfo", [mint, {"encoding": "base64"}])
    except Exception as e:
        logger.warning(f"getAccountInfo failed for {mint}: {e}")
        return None
    if not result or not result.get('value'):
        return None
    data_b64 = result['value']['data'][0]
    try:
        return base64.b64decode(data_b64)
    except Exception:
        return None


