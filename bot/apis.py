import asyncio
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
    return {
        'liquidity_usd': float(liq or 0),
        'volume24_usd': float(vol or 0),
        'volume1h_usd': float(vol1h or 0),
        'symbol': symbol,
        'price_usd': float(price or 0) if price else None,
    }


# ================== Birdeye ==================
async def get_birdeye_overview(ca: str) -> Dict[str, Any]:
    if not ENABLE_BIRDEYE or not BIRDEYE_API_KEY:
        return {}
    url = f"https://public-api.birdeye.so/defi/token_overview?address={ca}"
    headers = {
        "X-API-KEY": BIRDEYE_API_KEY,
        "x-chain": "solana",
        "accept": "application/json",
        "user-agent": "Mozilla/5.0",
    }
    try:
        data = await http_client.get_json(url, headers=headers)
        return (data.get('data') or {})
    except Exception as e:
        logger.warning(f"Birdeye overview failed for {ca}: {e}")
        return {}


# ================== Solana RPC ==================
_rpc_index = 0


async def solana_rpc(method: str, params: list) -> Any:
    global _rpc_index
    if not SOLANA_RPC_URLS:
        raise RuntimeError("No Solana RPC URLs configured")
    url = SOLANA_RPC_URLS[_rpc_index % len(SOLANA_RPC_URLS)]
    _rpc_index += 1
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
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


