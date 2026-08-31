import asyncio
import logging
import sys

from dotenv import load_dotenv

from .config import load as load_config
from .database import Store
from .bot import ModerationBot

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
