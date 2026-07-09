from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from summary_messages.configs import Settings
from summary_messages.models import SummaryWindow
from summary_messages.services import SummaryService


def build_settings() -> Settings:
    return Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "LLM_PROVIDER": "deepseek",
            "LLM_MODEL": "deepseek-chat",
            "DEEPSEEK_API_KEY": "test-key",
            "POSTGRES_URL": "postgresql://postgres:postgres@localhost:5432/summary_bot_test",
        }
    )


@pytest.mark.asyncio
async def test_summarize_window_skips_llm_when_no_messages() -> None:
    database = SimpleNamespace(
        get_messages=AsyncMock(return_value=[]),
        save_summary_run=AsyncMock(),
    )
    client = SimpleNamespace(summarize=AsyncMock())
    service = SummaryService(settings=build_settings(), database=database, client=client)

    window = SummaryWindow(
        label="last 24 hours",
        start=datetime(2026, 7, 3, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc),
    )

    summary = await service.summarize_window(
        chat_id=123,
        chat_title="Group A",
        window=window,
        output_language="English",
        timezone_name="UTC",
    )

    assert summary == "No stored group messages were found for last 24 hours."
    client.summarize.assert_not_awaited()
    database.save_summary_run.assert_not_awaited()