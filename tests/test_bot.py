from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from summary_messages.bot import SummaryBot
from summary_messages.config import Settings


def build_settings() -> Settings:
    return Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "LLM_PROVIDER": "ollama",
            "LLM_MODEL": "test-model",
            "OLLAMA_BASE_URL": "http://localhost:11434/v1",
            "SQLITE_PATH": str(Path("data") / "test.sqlite3"),
        }
    )


@pytest.mark.asyncio
async def test_store_message_persists_captioned_group_messages() -> None:
    bot = SummaryBot(build_settings())
    bot.database.upsert_chat = AsyncMock()
    bot.database.store_message = AsyncMock()

    update = SimpleNamespace(
        effective_message=SimpleNamespace(
            message_id=42,
            text=None,
            caption="caption-only message",
            date=datetime(2026, 6, 22, 8, 0, tzinfo=timezone.utc),
        ),
        effective_chat=SimpleNamespace(id=-1001, type="supergroup", title="Group A", full_name=None),
        effective_user=SimpleNamespace(id=99, full_name="User A"),
    )

    await bot.store_message(update, None)

    bot.database.upsert_chat.assert_awaited_once()
    bot.database.store_message.assert_awaited_once()
    stored = bot.database.store_message.await_args.args[0]
    assert stored.chat_id == -1001
    assert stored.message_id == 42
    assert stored.text == "caption-only message"


@pytest.mark.asyncio
async def test_store_message_skips_private_chats() -> None:
    bot = SummaryBot(build_settings())
    bot.database.upsert_chat = AsyncMock()
    bot.database.store_message = AsyncMock()

    update = SimpleNamespace(
        effective_message=SimpleNamespace(
            message_id=7,
            text="hello",
            caption=None,
            date=datetime(2026, 6, 22, 8, 0, tzinfo=timezone.utc),
        ),
        effective_chat=SimpleNamespace(id=123, type="private", title=None, full_name="Private User"),
        effective_user=SimpleNamespace(id=99, full_name="User A"),
    )

    await bot.store_message(update, None)

    bot.database.upsert_chat.assert_not_awaited()
    bot.database.store_message.assert_not_awaited()
