import logging
from typing import Optional

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from aiohttp import web

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


async def _health_handler(request: web.Request) -> web.Response:
    # Dependencies injected via app context
    bot = request.app["bot"]
    http_client = request.app["http_client"]
    ok = True
    details = {}
    try:
        details["telegram_connected"] = bool(getattr(bot.client, "is_connected", False))
        details["http_session"] = bool(http_client.session and not http_client.session.closed)
        ok = details["telegram_connected"] and details["http_session"]
    except Exception as e:
        ok = False
        details["error"] = str(e)
    return web.json_response({"ok": ok, **details}, status=200 if ok else 503)


async def _ready_handler(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    http_client = request.app["http_client"]
    ok = True
    details = {}
    try:
        tg_ok = bool(getattr(bot.client, "is_connected", False))
        http_ok = bool(http_client.session and not http_client.session.closed)
        stats_ok = True
        if getattr(bot, "stats", None) and bot.stats.enabled:
            stats_ok = bool(getattr(bot.stats, "_initialized", False))
        ok = tg_ok and http_ok and stats_ok
        details.update({"telegram_connected": tg_ok, "http_session": http_ok, "stats_initialized": stats_ok})
    except Exception as e:
        ok = False
        details["error"] = str(e)
    return web.json_response({"ok": ok, **details}, status=200 if ok else 503)


async def _metrics_handler(_request: web.Request) -> web.Response:
    payload = generate_latest()
    return web.Response(body=payload, headers={"Content-Type": CONTENT_TYPE_LATEST})


async def start_observability_server(bot, http_client, port: int) -> None:
    app = web.Application()
    app["bot"] = bot
    app["http_client"] = http_client
    app.add_routes([
        web.get("/metrics", _metrics_handler),
        web.get("/healthz", _health_handler),
        web.get("/readyz", _ready_handler),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info(f"Observability server listening on :{port} (/metrics,/healthz,/readyz)")


