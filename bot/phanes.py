import logging
from typing import Any, Dict, Optional

from config.config import (
    PHANES_ENABLED,
    PHANES_WEBHOOK_URL,
    PHANES_API_KEY,
)
from bot.apis import http_client


logger = logging.getLogger(__name__)


def _headers() -> Dict[str, str]:
    h: Dict[str, str] = {"Content-Type": "application/json", "user-agent": "callsbot/phanes-integration"}
    if PHANES_API_KEY:
        h["authorization"] = f"Bearer {PHANES_API_KEY}"
    return h


def phanes_is_enabled() -> bool:
    return bool(PHANES_ENABLED and PHANES_WEBHOOK_URL)


async def phanes_forward_signal(signal_obj: Dict[str, Any]) -> None:
    if not phanes_is_enabled():
        return
    payload: Dict[str, Any] = {
        "type": "signal",
        "source": "callsbot",
        "version": 1,
        "data": signal_obj,
    }
    try:
        await http_client.post_json(PHANES_WEBHOOK_URL, payload, headers=_headers())
    except Exception as e:
        logger.warning(f"Phanes forward signal failed: {e}")


async def phanes_forward_outcome(outcome_obj: Dict[str, Any]) -> None:
    if not phanes_is_enabled():
        return
    payload: Dict[str, Any] = {
        "type": "outcome",
        "source": "callsbot",
        "version": 1,
        "data": outcome_obj,
    }
    try:
        await http_client.post_json(PHANES_WEBHOOK_URL, payload, headers=_headers())
    except Exception as e:
        logger.warning(f"Phanes forward outcome failed: {e}")


