from __future__ import annotations

import logging

from .bot import SummaryBot
from .config import Settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)

    settings = Settings()
    bot = SummaryBot(settings)
    bot.run()


if __name__ == "__main__":
    main()
