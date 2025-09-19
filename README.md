## Calls Bot

A Telethon-based monitor that watches configured Telegram groups for Solana contract addresses (base58) and relays tiered alerts (T1/T2/T3) to a target group.

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
3. Create a `.env` file (see `.env.example`) and fill in your credentials:
   - `API_ID`, `API_HASH` (required)
   - `TARGET_GROUP`, `MONITORED_GROUPS`
   - `SOLANA_RPC_URLS`, optional `BIRDEYE_API_KEY`

### Run
```bash
python callsbot.py
```

On first run, Telethon will guide you through login (code/2FA) in the console and create a `*.session` file. Keep this file private.

### Configuration
Key environment variables (defaults exist; see `.env.example`):
- `API_ID`, `API_HASH`, `SESSION_NAME`
- `MONITORED_GROUPS`, `TARGET_GROUP`
- `ENABLE_EVALUATOR`, `ENABLE_TIERED_ALERTS`, `T1_IMMEDIATE`, `T2_THRESHOLD_CALLS`, `T3_THRESHOLD_CALLS`, `COOLDOWN_MINUTES_T1`, `HOT_THRESHOLD`, `HOT_RESET_HOURS`
- Evaluator thresholds: `OVERLAP_WINDOW_MIN`, `MIN_UNIQUE_CHANNELS_T1`, `T3_MIN_UNIQUE_CHANNELS`, `VEL5_WINDOW_MIN`, `VEL10_WINDOW_MIN`, `LIQ_THRESHOLD`, `VOL_1H_THRESHOLD`, `VOL_24H_THRESHOLD`, `HOLDERS_THRESHOLD`, `LARGEST_WALLET_MAX`, `MINT_SAFETY_REQUIRED`, `PRICE_MULTIPLE_MIN`, `PRICE_MULTIPLE_MAX`, `LIQ_MIN_USD`, `VOL24_MIN_USD`
- APIs: `SOLANA_RPC_URLS`, `ENABLE_BIRDEYE`, `BIRDEYE_API_KEY`
- Logging: `LOG_LEVEL`, `LOG_JSON`, `LOG_FILE`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`

### Notes
- `.gitignore` excludes `.env` and Telethon session files.
- Windows compatibility handled automatically.

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


