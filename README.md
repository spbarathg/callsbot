## Calls Bot — Solana Signals from Telegram Activity and Market Data

This bot monitors configured Telegram groups for Solana contract addresses and sends investment-grade signals (T1/T2/T3) to a target channel. It combines social consensus with live market and on-chain data to reduce noise and highlight higher-quality opportunities.

### Requirements
- Python 3.9+
- Telegram API ID and API Hash (`my.telegram.org`)

### Setup
1. Create and activate a virtual environment:
   - Windows PowerShell:
```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
```
2. Install dependencies:
```bash
pip install -r requirements.txt
```
3. Create a `.env` file and fill in your credentials:
   - `API_ID`, `API_HASH` (required)
   - `TARGET_GROUP`, `MONITORED_GROUPS`
   - `SOLANA_RPC_URLS`

### Run
```bash
python callsbot.py
```

On first run, Telethon will guide you through login (code/2FA) in the console and create a `*.session` file. Keep this file private.

### Persistence & health
- The bot persists minimal state to `STATE_FILE` (default `state.json`) so restarts do not resend old alerts. Configure with env vars `STATE_FILE`, `STATE_SAVE_SECONDS`.
- A heartbeat log prints every `HEALTH_LOG_SECONDS` with counts of groups, coins seen, mentions tracked, and last-ranked tokens.
- VIP watcher runs only if VIP wallets are configured.
- Prometheus metrics server (default `:9000`) exposes counters for messages processed, alerts, HTTP/RPC calls, errors, and loop durations. Toggle with `METRICS_ENABLED`, configure port via `METRICS_PORT`.

### Production notes
- All I/O is non-blocking; HTTP calls use `aiohttp` with retries and backoff.
- Graceful shutdown closes Telegram client, persists state, and closes HTTP session.
- Logs can be JSON (`LOG_JSON=true`) and rotate via `LOG_FILE`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`.

### Docker
Build and run:
```bash
docker build -t callsbot .
docker run --rm -it --env-file .env callsbot
```

Compose (see `docker/docker-compose.yml`):
```bash
docker compose -f docker/docker-compose.yml up -d
```

### systemd
Use `deploy/callsbot.service` as a reference or install roughly as:
```bash
sudo useradd -r -m -d /opt/callsbot callsbot || true
sudo rsync -a --delete . /opt/callsbot/
sudo cp deploy/callsbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now callsbot
```

### Configuration
Key environment variables (defaults exist; see `config/config.py`):
- `API_ID`, `API_HASH`, `SESSION_NAME`
- `MONITORED_GROUPS`, `TARGET_GROUP`
- `ENABLE_EVALUATOR`, `ENABLE_TIERED_ALERTS`, `T1_IMMEDIATE`, `T2_THRESHOLD_CALLS`, `T3_THRESHOLD_CALLS`, `COOLDOWN_MINUTES_T1`, `HOT_THRESHOLD`, `HOT_RESET_HOURS`
- Evaluator thresholds: `OVERLAP_WINDOW_MIN`, `MIN_UNIQUE_CHANNELS_T1`, `VEL5_WINDOW_MIN`, `VEL10_WINDOW_MIN`, `LIQ_THRESHOLD`, `VOL_1H_THRESHOLD`, `VOL_24H_THRESHOLD`, `HOLDERS_THRESHOLD`, `LARGEST_WALLET_MAX`, `MINT_SAFETY_REQUIRED`, `PRICE_MULTIPLE_MIN`, `PRICE_MULTIPLE_MAX`, `LIQ_MIN_USD`, `VOL24_MIN_USD`
- Philosophy-driven tiers:
  - Tier 2 (Confirmation): `T2_HOLDERS_MIN` (default 250), `T2_LIQ_MIN_USD` (50_000), `T2_LIQ_DRAWDOWN_MAX_PCT` (10), `T2_TXNS_H1_MIN` (500), `T2_BUY_SELL_RATIO_MIN` (1.5), `T2_AGE_MIN_MINUTES` (30), `T2_AGE_MAX_MINUTES` (90)
  - Tier 3 (Momentum): `T3_MCAP_MIN_USD` (500_000), `T3_VOL24_MIN_USD` (2_000_000), `T3_PRICE_MIN_X` (5), `T3_PRICE_MAX_X` (20), `T3_HOLDERS_MIN` (1500), `T3_POS_TREND_REQUIRED` (true), `T3_AGE_MIN_MINUTES` (120), `T3_AGE_MAX_MINUTES` (240)
- APIs: `SOLANA_RPC_URLS`
- Logging: `LOG_LEVEL`, `LOG_JSON`, `LOG_FILE`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`
- Metrics: `METRICS_ENABLED`, `METRICS_PORT`, `HTTP_MAX_CONCURRENCY`, `RPC_MAX_CONCURRENCY`
- Stats retention: `STATS_JSONL_MAX_BYTES`, `STATS_MAX_JSONL_FILES`, `STATS_MAINTENANCE_INTERVAL_SEC`

### Phanes DApp integration
- Set the following environment variables to forward your bot's analytics to Phanes (or any compatible collector):
  - `PHANES_ENABLED=true`
  - `PHANES_WEBHOOK_URL=https://<your-phanes-endpoint>/ingest`
  - `PHANES_API_KEY=<optional bearer token>`

When enabled, the bot forwards every Signal and Outcome event as JSON to the webhook, while still writing to `var/stats/*.jsonl` and SQLite. To use the Phanes DApp UI directly, invite the Phanes bot to your `TARGET_GROUP`, run `/dapp_active on`, and use `/dapp` to get a login link.

### Notes
- Create a `.gitignore` to exclude `.env`, Telethon session files, `var/`, SQLite DBs, and SSH keys.
- Windows compatibility handled automatically.
- See additional docs in `docs/` (Runbook, Alerts).

### Philosophy-driven tiers (behavior)
- T1 (Consensus): Social-only unique group consensus. Captures safe launch and initial traction.
- T2 (Confirmation): Requires age window 30–90 min, holders ≥ `T2_HOLDERS_MIN`, liquidity ≥ `T2_LIQ_MIN_USD` with ≤ `T2_LIQ_DRAWDOWN_MAX_PCT` drawdown from peak, ≥ `T2_TXNS_H1_MIN` swaps with buy/sell ≥ `T2_BUY_SELL_RATIO_MIN`, and ≥1 VIP holder.
- T3 (Momentum): Requires age window 2–4 hours, market cap ≥ `T3_MCAP_MIN_USD`, vol24 ≥ `T3_VOL24_MIN_USD`, price multiple in [`T3_PRICE_MIN_X`, `T3_PRICE_MAX_X`] vs first T1 price, positive 15m trend if `T3_POS_TREND_REQUIRED`, and holders ≥ `T3_HOLDERS_MIN`.

### Operations
- Status/logs:
  - `systemctl status callsbot | cat`
  - `journalctl -u callsbot -f`
- Update code:
  - `git pull && pip install -r requirements.txt && systemctl restart callsbot`
- Adjust thresholds:
  - edit `.env`, then `systemctl restart callsbot`
- Data safety:
  - backup `var/memecoin_session.session` to preserve Telegram login if migrating

### Philosophy-driven tiers (behavior)
- T1 (Consensus): Social-only unique group consensus. Captures safe launch and initial traction.
- T2 (Confirmation): Requires age window 30–90 min, holders ≥ `T2_HOLDERS_MIN`, liquidity ≥ `T2_LIQ_MIN_USD` with ≤ `T2_LIQ_DRAWDOWN_MAX_PCT` drawdown from peak, ≥ `T2_TXNS_H1_MIN` swaps with buy/sell ≥ `T2_BUY_SELL_RATIO_MIN`, and ≥1 VIP holder.
- T3 (Momentum): Requires age window 2–4 hours, market cap ≥ `T3_MCAP_MIN_USD`, vol24 ≥ `T3_VOL24_MIN_USD`, price multiple in [`T3_PRICE_MIN_X`, `T3_PRICE_MAX_X`] vs first T1 price, positive 15m trend if `T3_POS_TREND_REQUIRED`, and holders ≥ `T3_HOLDERS_MIN`.

### Project structure
```
bot/
  __init__.py
  main.py          # entrypoint
  telegram.py      # group listening + posting
  evaluator.py     # tiering logic
  apis.py          # Dex, Birdeye, RPC helpers
  utils.py         # retries, helpers
  vip.py           # VIP watcher
config/
  config.py        # constants / env loader
```


