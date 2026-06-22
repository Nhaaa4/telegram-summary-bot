from __future__ import annotations

from .bot import SummaryBot
from .config import Settings


def main() -> None:
    settings = Settings()
    bot = SummaryBot(settings)
    bot.run()


if __name__ == "__main__":
    main()
