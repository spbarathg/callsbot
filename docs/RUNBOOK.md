# Callsbot Runbook

## Overview
This bot monitors Telegram groups for Solana token mentions, enriches with market/RPC data, classifies signals (T1/T2/T3), and emits alerts while recording analytics.

## Start/Stop
- Docker: `docker-compose up -d` / `docker-compose logs -f`
- Systemd: `systemctl status callsbot | cat`, `journalctl -u callsbot -f`, `systemctl restart callsbot`

## Health & Metrics
- Metrics/health server (default): `http://<host>:9000`
  - `/metrics`: Prometheus format
  - `/healthz`: basic liveness (Telegram connected, HTTP session)
  - `/readyz`: readiness incl. stats DB init

## Common Issues
- Flood wait on Telegram: the bot logs and backs off. Review message rate and target group.
- RPC errors/timeouts: tune `RPC_MAX_RPS`, `RPC_MAX_CONCURRENCY`; ensure reliable RPC endpoints.
- Low/no alerts: check thresholds in `.env`; verify monitored groups and evaluator flags.

## Storage
- DB: `var/stats.db` (SQLite). Maintained hourly; retention `STATS_RETENTION_DAYS`.
- Logs/JSONL: `var/stats/*.jsonl` with rotation by size; consider compressing/archiving externally.
- State: `var/state.json` — backup before migrations; never edit while running.

## Security
- Telethon session under `SESSION_NAME` (default `var/memecoin_session`); treat as credential. Backup securely; avoid committing.
- Use a dedicated OS user / minimal privileges; for containers, run non-root with read-only root FS.

## Scaling
- Increase `HTTP_MAX_CONCURRENCY` / `RPC_MAX_CONCURRENCY` gradually; observe latency/error metrics and Telegram flood limits.
- Reduce evaluator thresholds for noisy channels to maintain precision; prefer VIP gating for T2/T3.

## Recovery
- Corrupt DB: stop bot, take DB backup, `sqlite3 var/stats.db .recover > recovered.sql` then rebuild, or start fresh.
- Lost state: bot may resend ranks; clear `last_rank_sent` in `state.json` if needed.

## Change Management
- Edit `.env`, restart service.
- Check CI status and coverage before deploying.
- Tag releases and maintain CHANGELOG for threshold changes.
