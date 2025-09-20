import os
import logging
import sys
import io
from logging.handlers import RotatingFileHandler
from typing import List

from dotenv import load_dotenv


# Load .env early
load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_list(value: str | None) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(',') if item.strip()]


# ================== CORE TELEGRAM CONFIG ==================
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_NAME = os.getenv("SESSION_NAME", "var/memecoin_session")

# Comma-separated `@channel` usernames or titles. Empty -> defaults below
ENV_MONITORED = os.getenv("MONITORED_GROUPS", "").strip()
TARGET_GROUP = os.getenv("TARGET_GROUP", "@callbotmemecoin")

DEFAULT_MONITORED_GROUPS = [
    '@MooDengPresidentCallers',
    '@Bot_NovaX',
    '@Ranma_Calls_Solana',
    '@MarksGems',
    '@Alphakollswithins',
    '@mattprintalphacalls',
    '@ReVoX_Academy',
    '@pfultimate',
    '@pumpfunvolumeby4AM',
    '@SouthParkCall',
    '@batman_gem',
    '@wifechangingcallss',
    '@SAVANNAHCALLS',
]


def parse_monitored_groups() -> list[str]:
    if not ENV_MONITORED:
        return DEFAULT_MONITORED_GROUPS
    return [item.strip() for item in ENV_MONITORED.split(',') if item.strip()]


MONITORED_GROUPS = parse_monitored_groups()


# ================== FEATURE FLAGS ==================
ENABLE_EVALUATOR = _env_bool("ENABLE_EVALUATOR", True)
ENABLE_TIERED_ALERTS = _env_bool("ENABLE_TIERED_ALERTS", True)
T1_IMMEDIATE = _env_bool("T1_IMMEDIATE", True)
COOLDOWN_MINUTES_T1 = int(os.getenv("COOLDOWN_MINUTES_T1", "0"))

T2_THRESHOLD_CALLS = int(os.getenv("T2_THRESHOLD_CALLS", "2"))
HOT_THRESHOLD = int(os.getenv("HOT_THRESHOLD", "4"))
T3_THRESHOLD_CALLS = int(os.getenv("T3_THRESHOLD_CALLS", str(HOT_THRESHOLD)))
HOT_RESET_HOURS = int(os.getenv("HOT_RESET_HOURS", "24"))


# ================== EVALUATOR THRESHOLDS ==================
# Social consensus
OVERLAP_WINDOW_MIN = int(os.getenv("OVERLAP_WINDOW_MIN", "15"))
MIN_UNIQUE_CHANNELS_T1 = int(os.getenv("T1_UNIQUE_GROUPS", os.getenv("MIN_UNIQUE_CHANNELS_T1", "4")))
T3_MIN_UNIQUE_CHANNELS = int(os.getenv("T3_MIN_UNIQUE_CHANNELS", "8"))
VEL5_WINDOW_MIN = int(os.getenv("VEL5_WINDOW_MIN", "5"))
VEL10_WINDOW_MIN = int(os.getenv("VEL10_WINDOW_MIN", "10"))
MENTION_DECAY_HALF_LIFE_MIN = float(os.getenv("MENTION_DECAY_HALF_LIFE_MIN", "60"))

# Quality & safety
LIQ_THRESHOLD = float(os.getenv("LIQ_THRESHOLD", "30000"))
VOL_1H_THRESHOLD = float(os.getenv("VOL_1H_THRESHOLD", "50000"))
VOL_24H_THRESHOLD = float(os.getenv("VOL_24H_THRESHOLD", "150000"))
HOLDERS_THRESHOLD = int(os.getenv("HOLDERS_THRESHOLD", "75"))
LARGEST_WALLET_MAX = float(os.getenv("LARGEST_WALLET_MAX", "40"))
MINT_SAFETY_REQUIRED = _env_bool("MINT_SAFETY_REQUIRED", True)

# Momentum & virality
PRICE_MULTIPLE_MIN = float(os.getenv("PRICE_MULTIPLE_MIN", "2"))
PRICE_MULTIPLE_MAX = float(os.getenv("PRICE_MULTIPLE_MAX", "10"))
BUYER_SELLER_RATIO = float(os.getenv("BUYER_SELLER_RATIO", "1.2"))
HOLDER_GROWTH_PCT = float(os.getenv("HOLDER_GROWTH_PCT", "15"))
HOLDER_GROWTH_WINDOW_MIN = int(os.getenv("HOLDER_GROWTH_WINDOW_MIN", "60"))

# Boost thresholds (auxiliary)
PRICE_5M_PCT = float(os.getenv("PRICE_5M_PCT", "10"))
VOL_15M_X = float(os.getenv("VOL_15M_X", "2.5"))

# Market sanity
LIQ_MIN_USD = float(os.getenv("LIQ_MIN_USD", "15000"))
VOL24_MIN_USD = float(os.getenv("VOL24_MIN_USD", "50000"))
T1_MARKET_REQUIRED = _env_bool("T1_MARKET_REQUIRED", False)

# Philosophy-driven Tier thresholds
# Tier 2 (Confirmation)
T2_HOLDERS_MIN = int(os.getenv("T2_HOLDERS_MIN", "250"))
T2_LIQ_MIN_USD = float(os.getenv("T2_LIQ_MIN_USD", "50000"))
T2_LIQ_DRAWDOWN_MAX_PCT = float(os.getenv("T2_LIQ_DRAWDOWN_MAX_PCT", "10"))
T2_TXNS_H1_MIN = int(os.getenv("T2_TXNS_H1_MIN", "500"))
T2_BUY_SELL_RATIO_MIN = float(os.getenv("T2_BUY_SELL_RATIO_MIN", "1.5"))
T2_AGE_MIN_MINUTES = int(os.getenv("T2_AGE_MIN_MINUTES", "30"))
T2_AGE_MAX_MINUTES = int(os.getenv("T2_AGE_MAX_MINUTES", "90"))

# Tier 3 (Momentum)
T3_MCAP_MIN_USD = float(os.getenv("T3_MCAP_MIN_USD", "500000"))
T3_VOL24_MIN_USD = float(os.getenv("T3_VOL24_MIN_USD", "2000000"))
T3_PRICE_MIN_X = float(os.getenv("T3_PRICE_MIN_X", "5"))
T3_PRICE_MAX_X = float(os.getenv("T3_PRICE_MAX_X", "20"))
T3_HOLDERS_MIN = int(os.getenv("T3_HOLDERS_MIN", "1500"))
T3_POS_TREND_REQUIRED = _env_bool("T3_POS_TREND_REQUIRED", True)
T3_AGE_MIN_MINUTES = int(os.getenv("T3_AGE_MIN_MINUTES", "120"))
T3_AGE_MAX_MINUTES = int(os.getenv("T3_AGE_MAX_MINUTES", "240"))

# Scoring thresholds
STRONG_SCORE_MIN = float(os.getenv("STRONG_SCORE_MIN", "8"))
MEDIUM_SCORE_MIN = float(os.getenv("MEDIUM_SCORE_MIN", "5"))


# ================== VIP & ENDPOINTS ==================
VIP_WALLETS = _parse_list(os.getenv("VIP_WALLETS", ""))
VIP_WALLETS_FILE = os.getenv("VIP_WALLETS_FILE", "vip_wallets.json")
VIP_MAX_WALLETS = int(os.getenv("VIP_MAX_WALLETS", "50"))
VIP_POLL_SECONDS = int(os.getenv("VIP_POLL_SECONDS", "60"))
VIP_WALLETS_PER_CYCLE = int(os.getenv("VIP_WALLETS_PER_CYCLE", "50"))

def _normalize_url(u: str) -> str:
    # Trim whitespace and stray trailing slashes or backslashes which can break requests
    return u.strip().rstrip('/').rstrip('\\')

SOLANA_RPC_URLS: List[str] = [_normalize_url(url) for url in os.getenv(
    "SOLANA_RPC_URLS",
    "https://api.mainnet-beta.solana.com,https://rpc.ankr.com/solana",
).split(',') if url.strip()]

# Birdeye API removed - not working


# ================== NETWORKING & LOGGING ==================
HTTP_TIMEOUT_SEC = float(os.getenv("HTTP_TIMEOUT_SEC", "15"))
HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "3"))
RETRY_BACKOFF_SEC = float(os.getenv("RETRY_BACKOFF_SEC", "1.5"))
RPC_MAX_RPS = float(os.getenv("RPC_MAX_RPS", "10"))  # max RPC requests per second (approx)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_JSON = _env_bool("LOG_JSON", False)
LOG_FILE = os.getenv("LOG_FILE", "var/bot.log")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "1048576"))  # 1MB
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))


# ================== VALIDATION ==================
def validate_required_config() -> None:
    missing = []
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not TARGET_GROUP:
        missing.append("TARGET_GROUP")
    if missing:
        raise SystemExit(f"Missing required configuration: {', '.join(missing)}. Set them in .env or environment.")


# ================== LOGGING SETUP ==================
class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        import json as _json
        payload = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return _json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    # Clear existing handlers to avoid duplicates on reload
    root.handlers.clear()

    if LOG_JSON:
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    # Console with UTF-8 fallback on Windows consoles that default to cp1252
    try:
        stream = sys.stdout
        enc = getattr(stream, "encoding", None)
        if not enc or str(enc).lower() != "utf-8":
            try:
                stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            except Exception:
                stream = sys.stdout
        ch = logging.StreamHandler(stream)
    except Exception:
        ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(formatter)
    root.addHandler(ch)

    # Rotating file handler
    if LOG_FILE:
        fh = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(formatter)
        root.addHandler(fh)


# ================== REGEX (contract address) ==================
import re

# Enhanced patterns for different contract address formats
# Main pattern: 32–44 Base58 chars, excluding ambiguous 0 O I l, with boundaries
CA_PATTERN = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])([1-9A-HJ-NP-Za-km-z]{32,44})(?![1-9A-HJ-NP-Za-km-z])")

# Additional patterns for edge cases and different formats
CA_PATTERNS = [
    # Standard Base58 pattern (primary)
    re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])([1-9A-HJ-NP-Za-km-z]{32,44})(?![1-9A-HJ-NP-Za-km-z])"),
    
    # Pattern for addresses that might have been split across lines
    re.compile(r"([1-9A-HJ-NP-Za-km-z]{16,22})\s*([1-9A-HJ-NP-Za-km-z]{16,22})"),
    
    # Pattern for addresses in URLs or links
    re.compile(r"(?:token|mint|address)[=:]\s*([1-9A-HJ-NP-Za-km-z]{32,44})", re.IGNORECASE),
    
    # Pattern for addresses with common prefixes
    re.compile(r"(?:CA|Contract|Token)[\s:=]*([1-9A-HJ-NP-Za-km-z]{32,44})", re.IGNORECASE),
    
    # Pattern for addresses in code blocks or monospace
    re.compile(r"`([1-9A-HJ-NP-Za-km-z]{32,44})`"),
    
    # Pattern for addresses with quotes
    re.compile(r"['\"]([1-9A-HJ-NP-Za-km-z]{32,44})['\"]"),
    
    # Pattern for addresses with parentheses
    re.compile(r"\(([1-9A-HJ-NP-Za-km-z]{32,44})\)"),
    
    # Pattern for addresses with brackets
    re.compile(r"\[([1-9A-HJ-NP-Za-km-z]{32,44})\]"),
]


# ================== STATE & HEALTH ==================
# Where to write bot state so restarts don't resend old alerts
STATE_FILE = os.getenv("STATE_FILE", "var/state.json")
# How often to save state and emit heartbeat logs
STATE_SAVE_SECONDS = int(os.getenv("STATE_SAVE_SECONDS", "60"))
HEALTH_LOG_SECONDS = int(os.getenv("HEALTH_LOG_SECONDS", "60"))

