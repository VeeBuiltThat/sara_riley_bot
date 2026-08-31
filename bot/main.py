import asyncio
import logging
import os
import sys

# Allow running as `python bot/main.py` directly (e.g. BisectHosting)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from bot.config import load as load_config
from bot.database import Store
from bot.bot import ModerationBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _run() -> None:
    load_dotenv()
    try:
        cfg = load_config()
    except ValueError as e:
        logger.error("configuration error: %s", e)
        sys.exit(1)

    store = await Store.open(cfg.db_host, cfg.db_port, cfg.db_user, cfg.db_password, cfg.db_name)
    try:
        bot = ModerationBot(cfg, store)
        await bot.start(cfg.discord_token)
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(_run())
