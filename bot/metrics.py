import logging
from typing import Optional

from prometheus_client import Counter, Histogram, Gauge, start_http_server

logger = logging.getLogger(__name__)


# Core counters
messages_processed_total = Counter(
    "messages_processed_total", "Total Telegram messages processed"
)
alerts_sent_total = Counter(
    "alerts_sent_total", "Total alerts sent by tier", ["tier"]
)
errors_total = Counter(
    "errors_total", "Total errors encountered", ["component"]
)


# External calls
rpc_calls_total = Counter(
    "solana_rpc_calls_total", "Total Solana RPC calls", ["method", "status"]
)
http_requests_total = Counter(
    "http_requests_total", "Total HTTP requests", ["target", "status"]
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds", "HTTP request duration", ["target"]
)
loop_duration_seconds = Histogram(
    "loop_duration_seconds", "Background loop iteration duration", ["loop"]
)

inflight_rpc = Gauge("inflight_rpc", "In-flight Solana RPC requests")
inflight_http = Gauge("inflight_http", "In-flight HTTP requests")


def start_metrics_server(port: int) -> None:
    try:
        start_http_server(port)
        logger.info(f"Prometheus metrics server started on :{port}")
    except Exception as e:
        logger.warning(f"Failed to start metrics server on :{port}: {e}")


