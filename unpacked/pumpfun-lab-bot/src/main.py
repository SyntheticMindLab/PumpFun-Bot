from __future__ import annotations

import asyncio
import logging

from .config import load_settings
from .db import Database
from .engine import Engine
from .pumpportal import PumpPortalStream
from .telegram_bot import TelegramBot


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = Database(settings.database_path)
    db.init()

    engine = Engine(settings, db)
    telegram = TelegramBot(settings, db, engine)
    engine.set_telegram(telegram)

    stream = PumpPortalStream(settings.pumpportal_api_key, engine.handle_pump_event)
    engine.set_stream(stream)

    app = telegram.build()
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.gather(stream.run(), engine.start_price_loop())
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
