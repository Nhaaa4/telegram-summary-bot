from __future__ import annotations

from datetime import datetime, timezone
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
            "POSTGRES_URL": "postgresql://postgres:postgres@postgres:5432/summary_bot_test",
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


@pytest.mark.asyncio
async def test_summary_command_rejects_private_chats() -> None:
    bot = SummaryBot(build_settings())
    bot.database.upsert_chat = AsyncMock()
    bot.service.summarize_chat = AsyncMock()

    message = SimpleNamespace(reply_text=AsyncMock(), reply_sticker=AsyncMock())
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=123, type="private", title=None, full_name="Private User"),
    )
    context = SimpleNamespace(args=[])

    await bot.summary_command(update, context)

    message.reply_text.assert_awaited_once_with("This command only works in group chats where I can store messages.")
    bot.database.upsert_chat.assert_not_awaited()
    bot.service.summarize_chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_summary_command_handles_provider_errors() -> None:
    bot = SummaryBot(build_settings())
    bot.database.upsert_chat = AsyncMock()
    bot.service.summarize_chat = AsyncMock(side_effect=RuntimeError("provider failure"))

    message = SimpleNamespace(reply_text=AsyncMock(), reply_sticker=AsyncMock())
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=-1001, type="supergroup", title="Group A", full_name=None),
    )
    context = SimpleNamespace(args=[])

    await bot.summary_command(update, context)

    message.reply_text.assert_awaited_once_with(
        "The summary provider failed right now. Try again later or switch to a different provider/model in .env."
    )
    message.reply_sticker.assert_awaited_once()


@pytest.mark.asyncio
async def test_private_chat_handler_replies_to_private_text() -> None:
    bot = SummaryBot(build_settings())
    bot.client = SimpleNamespace(summarize=AsyncMock(return_value="Hello from bot"))

    message = SimpleNamespace(
        text="hello",
        caption=None,
        reply_chat_action=AsyncMock(),
        reply_text=AsyncMock(),
        reply_sticker=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=123, type="private", title=None, full_name="Private User"),
        effective_user=SimpleNamespace(id=99, full_name="User A"),
    )

    await bot.private_chat_handler(update, None)

    message.reply_chat_action.assert_awaited_once_with("typing")
    message.reply_text.assert_awaited_once_with("@coppsary_bot Hello from bot")
    message.reply_sticker.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_chat_handler_reports_provider_errors() -> None:
    bot = SummaryBot(build_settings())
    bot.client = SimpleNamespace(summarize=AsyncMock(side_effect=RuntimeError("provider failure")))

    message = SimpleNamespace(
        text="hello",
        caption=None,
        reply_chat_action=AsyncMock(),
        reply_text=AsyncMock(),
        reply_sticker=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=123, type="private", title=None, full_name="Private User"),
        effective_user=SimpleNamespace(id=99, full_name="User A"),
    )

    await bot.private_chat_handler(update, None)

    message.reply_text.assert_awaited_once_with(
        "The summary provider failed right now. Try again later or switch to a different provider/model in .env."
    )
    message.reply_sticker.assert_awaited_once()


@pytest.mark.asyncio
async def test_mention_handler_replies_when_only_bot_is_mentioned() -> None:
    bot = SummaryBot(build_settings())

    message = SimpleNamespace(
        text="@coppsary_bot",
        entities=[SimpleNamespace(type="mention", offset=0, length=13)],
        reply_text=AsyncMock(),
        reply_chat_action=AsyncMock(),
        reply_sticker=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=-1001, type="supergroup", title="Group A", full_name=None),
        effective_user=SimpleNamespace(id=99, full_name="User A"),
    )
    context = SimpleNamespace(bot=SimpleNamespace(username="coppsary_bot"))

    await bot.mention_handler(update, context)

    message.reply_text.assert_awaited_once_with("I'm here @coppsary_bot. Tell me what you need.")
