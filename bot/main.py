import asyncio
import logging
import os
import signal

from config.config import setup_logging, validate_required_config, validate_ranges
from bot.telegram import Bot
from bot.apis import http_client
from bot.apis import get_dex_metrics
from config.config import STATS_SNAPSHOT_INTERVAL_SEC
from bot.vip import vip_watcher_loop
from config.config import ENABLE_STATS
from config.config import METRICS_ENABLED, METRICS_PORT
from bot.metrics import start_observability_server, loop_duration_seconds


async def _run() -> None:
    # Ensure var/ directories exist for logs/state/sessions
    try:
        import os as _os
        _os.makedirs("var", exist_ok=True)
    except Exception:
        pass
    setup_logging()
    validate_required_config()
    validate_ranges()
    # Create bot
    bot = Bot()
    if METRICS_ENABLED:
        # Start observability server (metrics + health) with real bot reference
        asyncio.create_task(start_observability_server(bot=bot, http_client=http_client, port=METRICS_PORT))
    await http_client.start()

    
    await bot.start()
    # Initialize stats DB if enabled
    try:
        if getattr(bot, 'stats', None):
            await bot.stats.init()
    except Exception:
        pass
    # Start VIP watcher tethered to evaluator state if evaluator is enabled
    stop_event = asyncio.Event()
    if bot.evaluator:
        asyncio.create_task(vip_watcher_loop(bot.evaluator.state.mentions_by_ca, bot.evaluator.state.vip_holders_by_ca, stop_event, getattr(bot, 'stats', None)))
    # Background: market snapshots loop to persist time-series data and derive outcomes accurately
    if ENABLE_STATS and getattr(bot, 'stats', None):
        async def _snapshots_loop() -> None:
            try:
                while not stop_event.is_set():
                    await asyncio.sleep(max(15, STATS_SNAPSHOT_INTERVAL_SEC))
                    try:
                        if getattr(bot, 'stats', None) and bot.stats.enabled:
                            import time as _time
                            _start = _time.perf_counter()
                            cas = list(getattr(bot, 'coin_counts', {}).keys())[:500]
                            for ca in cas:
                                try:
                                    metrics = await get_dex_metrics(ca)
                                    if metrics:
                                        await bot.stats.record_snapshot(ca, metrics)
                                        # Opportunistically compute outcomes from snapshots
                                        await bot.stats.maybe_record_outcomes_from_snapshots(ca)
                                except Exception:
                                    continue
                            loop_duration_seconds.labels("snapshots").observe(_time.perf_counter() - _start)
                    except Exception:
                        pass
            except asyncio.CancelledError:
                return
        asyncio.create_task(_snapshots_loop())

        # Maintenance loop (VACUUM, JSONL rotation)
        from config.config import STATS_MAINTENANCE_INTERVAL_SEC
        async def _maintenance_loop() -> None:
            try:
                while not stop_event.is_set():
                    await asyncio.sleep(STATS_MAINTENANCE_INTERVAL_SEC)
                    try:
                        import time as _time
                        _start = _time.perf_counter()
                        await bot.stats.maybe_maintain_storage()
                        loop_duration_seconds.labels("maintenance").observe(_time.perf_counter() - _start)
                    except Exception:
                        pass
            except asyncio.CancelledError:
                return
        asyncio.create_task(_maintenance_loop())
    
    # Graceful signal handling (best-effort on Windows)
    loop = asyncio.get_running_loop()
    def _signal_shutdown(sig: str) -> None:
        logging.getLogger(__name__).info(f"Signal received: {sig}. Shutting down...")
        asyncio.create_task(bot.client.disconnect())
    try:
        loop.add_signal_handler(signal.SIGTERM, lambda: _signal_shutdown("SIGTERM"))
        loop.add_signal_handler(signal.SIGINT, lambda: _signal_shutdown("SIGINT"))
    except NotImplementedError:
        # Windows: signal handlers may not be available; rely on KeyboardInterrupt
        pass
    try:
        await bot.client.run_until_disconnected()
    finally:
        stop_event.set()
        # Shut down bot and HTTP cleanly
        try:
            await bot.stop()
        except Exception:
            pass
        await http_client.close()


def main() -> None:
    if os.name == "nt":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass
    asyncio.run(_run())


if __name__ == "__main__":
    main()


