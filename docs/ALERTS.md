# Alerts

Prometheus/Alertmanager rule ideas:

- No messages processed
  - expr: increase(messages_processed_total[10m]) == 0
  - for: 15m
  - severity: critical

- Evaluator handler errors spike
  - expr: rate(errors_total{component="telegram_handler"}[5m]) > 0.1
  - for: 10m
  - severity: warning

- RPC error rate high
  - expr: rate(solana_rpc_calls_total{status="error"}[5m]) / rate(solana_rpc_calls_total[5m]) > 0.05
  - for: 10m
  - severity: warning

- Snapshots loop stalled
  - expr: histogram_quantile(0.5, rate(loop_duration_seconds_bucket{loop="snapshots"}[10m])) < 0.001
  - for: 15m
  - severity: warning

- Alerts sent drop to zero
  - expr: increase(alerts_sent_total[30m]) == 0
  - for: 60m
  - severity: warning

Tune thresholds based on baseline in your environment.
