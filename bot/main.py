import asyncio
import logging
import os

from config.config import setup_logging, validate_required_config
from bot.telegram import Bot
from bot.apis import http_client
from bot.vip import vip_watcher_loop
import asyncio


async def _run() -> None:
    setup_logging()
    validate_required_config()
    await http_client.start()

    bot = Bot()
    await bot.start()
    # Start VIP watcher tethered to evaluator state if evaluator is enabled
    stop_event = asyncio.Event()
    if bot.evaluator:
        asyncio.create_task(vip_watcher_loop(bot.evaluator.state.mentions_by_ca, bot.evaluator.state.vip_holders_by_ca, stop_event))
    try:
        await bot.client.run_until_disconnected()
    finally:
        stop_event.set()
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


