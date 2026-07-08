from __future__ import annotations

import asyncio
import logging
import sys

from .bot import SummaryBot
from .config import Settings


def configure_event_loop() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def setup_logging(timezone_name: str = "Asia/Phnom_Penh") -> None:
    import logging
    from datetime import datetime
    from zoneinfo import ZoneInfo

    app_timezone = ZoneInfo(timezone_name)

    class TimezoneFormatter(logging.Formatter):
        def formatTime(self, record, datefmt=None):
            dt = datetime.fromtimestamp(record.created, app_timezone)

            if datefmt:
                return dt.strftime(datefmt)

            return dt.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]

    handler = logging.StreamHandler()
    handler.setFormatter(
        TimezoneFormatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
    )

    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        force=True,
    )

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)


def main() -> None:
    configure_event_loop()
    settings = Settings()

    setup_logging(settings.timezone)

    bot = SummaryBot(settings)
    bot.run()


if __name__ == "__main__":
    main()
