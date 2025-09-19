import asyncio
import logging
import os
import signal

from config.config import setup_logging, validate_required_config
from bot.telegram import Bot
from bot.apis import http_client
from bot.vip import vip_watcher_loop


async def _run() -> None:
    # Ensure var/ directories exist for logs/state/sessions
    try:
        import os as _os
        _os.makedirs("var", exist_ok=True)
    except Exception:
        pass
    setup_logging()
    validate_required_config()
    await http_client.start()

    bot = Bot()
    await bot.start()
    # Start VIP watcher tethered to evaluator state if evaluator is enabled
    stop_event = asyncio.Event()
    if bot.evaluator:
        asyncio.create_task(vip_watcher_loop(bot.evaluator.state.mentions_by_ca, bot.evaluator.state.vip_holders_by_ca, stop_event))
    
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


