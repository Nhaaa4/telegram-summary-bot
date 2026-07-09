from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage
from telegram.constants import ParseMode

from summary_messages.bot import SummaryBot
from summary_messages.configs import Settings


def fake_agent(reply: str = "Hello from bot"):
    return SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": [AIMessage(content=reply)]}))


def build_settings(**overrides) -> Settings:
    values = {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "LLM_PROVIDER": "ollama",
        "LLM_MODEL": "test-model",
        "OLLAMA_BASE_URL": "http://localhost:11434/v1",
        "POSTGRES_URL": "postgresql://postgres:postgres@postgres:5432/summary_bot_test",
    }
    values.update(overrides)
    return Settings.model_validate(values)


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
async def test_store_message_stores_stickers_instead_of_text() -> None:
    bot = SummaryBot(build_settings())
    bot.database.upsert_chat = AsyncMock()
    bot.database.store_message = AsyncMock()
    bot.database.store_sticker = AsyncMock()

    update = SimpleNamespace(
        effective_message=SimpleNamespace(
            message_id=43,
            text=None,
            caption=None,
            sticker=SimpleNamespace(file_id="sticker-file-id"),
            date=datetime(2026, 6, 22, 8, 0, tzinfo=timezone.utc),
        ),
        effective_chat=SimpleNamespace(id=-1001, type="supergroup", title="Group A", full_name=None),
        effective_user=SimpleNamespace(id=99, full_name="User A"),
    )

    await bot.store_message(update, None)

    bot.database.upsert_chat.assert_awaited_once()
    bot.database.store_sticker.assert_awaited_once_with(chat_id=-1001, file_id="sticker-file-id")
    bot.database.store_message.assert_not_awaited()


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
async def test_quote_command_replies_with_client_result() -> None:
    bot = SummaryBot(build_settings())
    bot.client = SimpleNamespace(summarize=AsyncMock(return_value='"Stay hungry, stay foolish." — Steve Jobs'))

    message = SimpleNamespace(reply_chat_action=AsyncMock(), reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message)

    await bot.quote_command(update, None)

    message.reply_text.assert_awaited_once_with(
        '💬 "Stay hungry, stay foolish." — Steve Jobs', parse_mode=ParseMode.MARKDOWN
    )


@pytest.mark.asyncio
async def test_quote_command_falls_back_on_provider_error() -> None:
    bot = SummaryBot(build_settings())
    bot.client = SimpleNamespace(summarize=AsyncMock(side_effect=RuntimeError("provider failure")))

    message = SimpleNamespace(reply_chat_action=AsyncMock(), reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message)

    await bot.quote_command(update, None)

    message.reply_text.assert_awaited_once()
    assert "Steve Jobs" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_private_chat_handler_replies_to_private_text() -> None:
    bot = SummaryBot(build_settings())
    bot._chat_model = SimpleNamespace()

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

    with patch("summary_messages.bot.bot.build_graph", return_value=fake_agent("Hello from bot")):
        await bot.private_chat_handler(update, None)

    message.reply_chat_action.assert_awaited_once_with("typing")
    message.reply_text.assert_awaited_once_with("Hello from bot", parse_mode=ParseMode.MARKDOWN)
    message.reply_sticker.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_chat_handler_extracts_text_from_content_blocks() -> None:
    # Gemini/Gemma "thinking" models can return message.content as a list of content
    # blocks (reasoning + text) instead of a plain string — only the text block should
    # ever reach the user.
    bot = SummaryBot(build_settings())
    bot._chat_model = SimpleNamespace()

    message = SimpleNamespace(
        text="are you alive",
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
    content_blocks = [
        {"type": "thinking", "thinking": "The user is asking if I'm alive..."},
        {"type": "text", "text": "No, I'm just code on a server."},
    ]

    with patch("summary_messages.bot.bot.build_graph", return_value=fake_agent(content_blocks)):
        await bot.private_chat_handler(update, None)

    message.reply_text.assert_awaited_once_with("No, I'm just code on a server.", parse_mode=ParseMode.MARKDOWN)


@pytest.mark.asyncio
async def test_private_chat_handler_reports_provider_errors() -> None:
    bot = SummaryBot(build_settings())
    bot._chat_model = SimpleNamespace()
    failing_agent = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("provider failure")))

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

    with patch("summary_messages.bot.bot.build_graph", return_value=failing_agent):
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


@pytest.mark.asyncio
async def test_predict_command_skips_sticker_when_none_configured() -> None:
    bot = SummaryBot(build_settings(FALLBACK_STICKER_FILE_ID=None))
    bot.client = SimpleNamespace(summarize=AsyncMock(side_effect=RuntimeError("provider failure")))

    message = SimpleNamespace(reply_chat_action=AsyncMock(), reply_text=AsyncMock(), reply_sticker=AsyncMock())
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(args=["who", "wins?"])

    await bot.predict_command(update, context)

    message.reply_text.assert_awaited_once()
    message.reply_sticker.assert_not_awaited()


@pytest.mark.asyncio
async def test_fuck_command_skips_sticker_when_none_configured() -> None:
    bot = SummaryBot(build_settings(FALLBACK_STICKER_FILE_ID=None))
    bot.client = SimpleNamespace(summarize=AsyncMock(return_value="you're mid"))

    message = SimpleNamespace(
        text="/fuck @target",
        entities=[SimpleNamespace(type="mention", offset=6, length=7)],
        reply_chat_action=AsyncMock(),
        reply_text=AsyncMock(),
        reply_sticker=AsyncMock(),
    )
    update = SimpleNamespace(effective_message=message)

    await bot.fuck_command(update, None)

    message.reply_sticker.assert_not_awaited()
