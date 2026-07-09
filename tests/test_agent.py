from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from apscheduler.jobstores.base import JobLookupError

from summary_messages.configs import Settings
from summary_messages.graph.tools import build_tools


def build_settings(**overrides) -> Settings:
    values = {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "LLM_PROVIDER": "ollama",
        "LLM_MODEL": "test-model",
        "POSTGRES_URL": "postgresql://postgres:postgres@localhost:5432/summary_bot_test",
        "GROUP_NAME": "COPPSARY",
        "GROUP_MEMBERS": "Alice, Bob",
        "FALLBACK_STICKER_FILE_ID": "fallback-sticker-id",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def build_tool_set(**overrides):
    database = SimpleNamespace(
        create_reminder=AsyncMock(return_value=1),
        list_reminders_for_user=AsyncMock(return_value=[]),
        delete_reminder=AsyncMock(return_value=True),
        update_reminder=AsyncMock(
            return_value={"id": 1, "text": "check the oven", "remind_at": datetime(2026, 7, 8, 16, 0, tzinfo=timezone.utc)}
        ),
        get_random_sticker=AsyncMock(return_value=None),
    )
    scheduler = SimpleNamespace(add_job=lambda *a, **k: None, remove_job=lambda job_id: None)
    bot = SimpleNamespace(
        send_reminder=AsyncMock(),
        application=SimpleNamespace(bot=SimpleNamespace(send_sticker=AsyncMock())),
    )

    kwargs = dict(
        settings=build_settings(),
        database=database,
        chat_id=-1001,
        user_id=99,
        user_name="User A",
        timezone_name="UTC",
        scheduler=scheduler,
        bot=bot,
    )
    kwargs.update(overrides)
    tools = build_tools(**kwargs)
    return {t.name: t for t in tools}, kwargs["database"], kwargs["scheduler"], kwargs["bot"]


def future_date_time(minutes: int = 10, timezone_name: str = "Asia/Phnom_Penh") -> tuple[str, str]:
    future = datetime.now(ZoneInfo(timezone_name)) + timedelta(minutes=minutes)
    return future.strftime("%Y-%m-%d"), future.strftime("%H:%M")


@pytest.mark.asyncio
async def test_create_reminder_schedules_job_and_persists_row() -> None:
    tools, database, scheduler, _ = build_tool_set()
    # add_job is sync in APScheduler; wrap to record the call instead
    calls = []
    scheduler.add_job = lambda *a, **k: calls.append((a, k))

    date, time = future_date_time()
    result = await tools["create_reminder"].ainvoke({"text": "check the oven", "date": date, "time": time})

    database.create_reminder.assert_awaited_once()
    assert calls, "expected scheduler.add_job to be called"
    assert "Reminder #1 set" in result


@pytest.mark.asyncio
async def test_create_reminder_rejects_invalid_format() -> None:
    tools, database, _, _ = build_tool_set()

    result = await tools["create_reminder"].ainvoke({"text": "check the oven", "date": "not-a-date", "time": "asdf"})

    database.create_reminder.assert_not_awaited()
    assert "Invalid date/time" in result


@pytest.mark.asyncio
async def test_create_reminder_rejects_past_time() -> None:
    tools, database, _, _ = build_tool_set()

    result = await tools["create_reminder"].ainvoke({"text": "check the oven", "date": "2020-01-01", "time": "09:00"})

    database.create_reminder.assert_not_awaited()
    assert "in the past" in result


@pytest.mark.asyncio
async def test_create_reminder_rejects_missing_date_and_relative_day() -> None:
    tools, database, _, _ = build_tool_set()

    result = await tools["create_reminder"].ainvoke({"text": "check the oven", "time": "09:00"})

    database.create_reminder.assert_not_awaited()
    assert "need a date or a relative day" in result


@pytest.mark.asyncio
async def test_create_reminder_rejects_unknown_relative_day() -> None:
    tools, database, _, _ = build_tool_set()

    result = await tools["create_reminder"].ainvoke({"text": "check the oven", "time": "09:00", "relative_day": "someday"})

    database.create_reminder.assert_not_awaited()
    assert "don't recognize" in result


@pytest.mark.asyncio
async def test_create_reminder_resolves_relative_day_deterministically() -> None:
    tools, database, _, _ = build_tool_set()

    result = await tools["create_reminder"].ainvoke({"text": "submit report", "time": "23:00", "relative_day": "tomorrow"})

    database.create_reminder.assert_awaited_once()
    kwargs = database.create_reminder.await_args.kwargs
    expected = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    assert kwargs["remind_at"].date() == expected
    assert "Reminder #1 set" in result


@pytest.mark.asyncio
async def test_list_reminders_formats_rows() -> None:
    tools, database, _, _ = build_tool_set()
    database.list_reminders_for_user = AsyncMock(
        return_value=[{"id": 1, "text": "check the oven", "remind_at": datetime(2026, 7, 8, 16, 0, tzinfo=timezone.utc)}]
    )

    result = await tools["list_reminders"].ainvoke({})

    assert "#1" in result
    assert "check the oven" in result


@pytest.mark.asyncio
async def test_list_reminders_converts_utc_to_local_timezone() -> None:
    tools, database, _, _ = build_tool_set(timezone_name="Asia/Phnom_Penh")
    # Stored in UTC (as it always is); local Asia/Phnom_Penh (UTC+7) should show 19:00, not 12:00.
    database.list_reminders_for_user = AsyncMock(
        return_value=[{"id": 1, "text": "submit report", "remind_at": datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)}]
    )

    result = await tools["list_reminders"].ainvoke({})

    assert "07:00 PM" in result
    assert "12:00" not in result


@pytest.mark.asyncio
async def test_list_reminders_empty() -> None:
    tools, _, _, _ = build_tool_set()

    result = await tools["list_reminders"].ainvoke({})

    assert result == "No pending reminders."


@pytest.mark.asyncio
async def test_cancel_reminder_not_found_when_delete_affects_no_rows() -> None:
    tools, database, _, _ = build_tool_set()
    database.delete_reminder = AsyncMock(return_value=False)

    result = await tools["cancel_reminder"].ainvoke({"reminder_id": 42})

    assert "No reminder #42" in result


@pytest.mark.asyncio
async def test_cancel_reminder_ignores_missing_scheduler_job() -> None:
    tools, database, scheduler, _ = build_tool_set()
    database.delete_reminder = AsyncMock(return_value=True)

    def raise_lookup_error(job_id):
        raise JobLookupError(job_id)

    scheduler.remove_job = raise_lookup_error

    result = await tools["cancel_reminder"].ainvoke({"reminder_id": 42})

    assert "cancelled" in result


@pytest.mark.asyncio
async def test_update_reminder_text_only() -> None:
    tools, database, _, _ = build_tool_set()

    result = await tools["update_reminder"].ainvoke({"reminder_id": 1, "text": "check the stove"})

    database.update_reminder.assert_awaited_once_with(1, 99, text="check the stove", remind_at=None)
    assert "Reminder #1 updated" in result


@pytest.mark.asyncio
async def test_update_reminder_converts_utc_to_local_timezone() -> None:
    tools, database, _, _ = build_tool_set(timezone_name="Asia/Phnom_Penh")
    database.update_reminder = AsyncMock(
        return_value={"id": 1, "text": "submit report", "remind_at": datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)}
    )

    result = await tools["update_reminder"].ainvoke({"reminder_id": 1, "text": "submit report"})

    assert "07:00 PM" in result
    assert "12:00" not in result


@pytest.mark.asyncio
async def test_update_reminder_reschedules_job_on_new_time() -> None:
    tools, database, scheduler, bot = build_tool_set()
    date, time = future_date_time()
    calls = []
    scheduler.add_job = lambda *a, **k: calls.append((a, k))

    result = await tools["update_reminder"].ainvoke({"reminder_id": 1, "date": date, "time": time})

    database.update_reminder.assert_awaited_once()
    assert calls, "expected scheduler.add_job to reschedule the job"
    assert calls[0][1]["id"] == "reminder-1"
    assert "Reminder #1 updated" in result


@pytest.mark.asyncio
async def test_update_reminder_resolves_relative_day_deterministically() -> None:
    tools, database, _, _ = build_tool_set()

    result = await tools["update_reminder"].ainvoke({"reminder_id": 1, "relative_day": "tomorrow", "time": "23:00"})

    database.update_reminder.assert_awaited_once()
    kwargs = database.update_reminder.await_args.kwargs
    expected = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    assert kwargs["remind_at"].date() == expected
    assert "Reminder #1 updated" in result


@pytest.mark.asyncio
async def test_update_reminder_rejects_partial_date_time() -> None:
    tools, database, _, _ = build_tool_set()

    result = await tools["update_reminder"].ainvoke({"reminder_id": 1, "date": "2026-08-01"})

    database.update_reminder.assert_not_awaited()
    assert "provide both date" in result


@pytest.mark.asyncio
async def test_update_reminder_rejects_past_time() -> None:
    tools, database, _, _ = build_tool_set()

    result = await tools["update_reminder"].ainvoke({"reminder_id": 1, "date": "2020-01-01", "time": "09:00"})

    database.update_reminder.assert_not_awaited()
    assert "in the past" in result


@pytest.mark.asyncio
async def test_update_reminder_not_found() -> None:
    tools, database, _, _ = build_tool_set()
    database.update_reminder = AsyncMock(return_value=None)

    result = await tools["update_reminder"].ainvoke({"reminder_id": 42, "text": "new text"})

    assert "No pending reminder #42" in result


@pytest.mark.asyncio
async def test_about_creator_mentions_group_and_members() -> None:
    tools, _, _, _ = build_tool_set()

    result = await tools["about_creator"].ainvoke({})

    assert "COPPSARY" in result
    assert "Alice" in result and "Bob" in result


@pytest.mark.asyncio
async def test_send_sticker_prefers_stored_sticker() -> None:
    tools, database, _, bot = build_tool_set()
    database.get_random_sticker = AsyncMock(return_value="stored-sticker-id")

    result = await tools["send_sticker"].ainvoke({"mood": "funny"})

    bot.application.bot.send_sticker.assert_awaited_once_with(chat_id=-1001, sticker="stored-sticker-id")
    assert "funny" in result


@pytest.mark.asyncio
async def test_send_sticker_falls_back_to_configured_sticker() -> None:
    tools, database, _, bot = build_tool_set()
    database.get_random_sticker = AsyncMock(return_value=None)

    result = await tools["send_sticker"].ainvoke({"mood": "funny"})

    bot.application.bot.send_sticker.assert_awaited_once_with(chat_id=-1001, sticker="fallback-sticker-id")
    assert "funny" in result


@pytest.mark.asyncio
async def test_send_sticker_reports_none_available() -> None:
    tools, database, _, bot = build_tool_set(settings=build_settings(FALLBACK_STICKER_FILE_ID=None))
    database.get_random_sticker = AsyncMock(return_value=None)

    result = await tools["send_sticker"].ainvoke({"mood": "sad"})

    bot.application.bot.send_sticker.assert_not_awaited()
    assert "No sticker available" in result


def test_web_search_omitted_without_tavily_api_key() -> None:
    tools, _, _, _ = build_tool_set(settings=build_settings(TAVILY_API_KEY=None))

    assert "web_search" not in tools


@pytest.mark.asyncio
async def test_web_search_returns_answer_and_results() -> None:
    fake_client = SimpleNamespace(
        search=AsyncMock(
            return_value={
                "answer": "It's sunny today.",
                "results": [{"title": "Weather", "url": "https://example.com", "content": "Sunny, 30C " * 20}],
            }
        )
    )
    with patch("summary_messages.graph.tools.AsyncTavilyClient", return_value=fake_client):
        tools, _, _, _ = build_tool_set(settings=build_settings(TAVILY_API_KEY="test-key"))

        result = await tools["web_search"].ainvoke({"query": "weather today"})

    fake_client.search.assert_awaited_once_with("weather today", max_results=5, include_answer=True)
    assert "It's sunny today." in result
    assert "Weather" in result
    assert "example.com" in result


@pytest.mark.asyncio
async def test_web_search_reports_failure() -> None:
    fake_client = SimpleNamespace(search=AsyncMock(side_effect=RuntimeError("network error")))
    with patch("summary_messages.graph.tools.AsyncTavilyClient", return_value=fake_client):
        tools, _, _, _ = build_tool_set(settings=build_settings(TAVILY_API_KEY="test-key"))

        result = await tools["web_search"].ainvoke({"query": "weather today"})

    assert "Web search failed" in result


class _FakeWeatherResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeWeatherClient:
    def __init__(self, payload: dict | None = None, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error

    async def __aenter__(self) -> "_FakeWeatherClient":
        return self

    async def __aexit__(self, *args) -> None:
        pass

    async def get(self, *args, **kwargs) -> _FakeWeatherResponse:
        if self._error:
            raise self._error
        return _FakeWeatherResponse(self._payload)


@pytest.mark.asyncio
async def test_get_weather_returns_summary() -> None:
    payload = {
        "current": {
            "temperature_2m": 32.0,
            "apparent_temperature": 37.5,
            "relative_humidity_2m": 60,
            "weather_code": 1,
            "wind_speed_10m": 12.3,
        }
    }
    with patch("summary_messages.graph.tools.httpx.AsyncClient", return_value=_FakeWeatherClient(payload=payload)):
        tools, _, _, _ = build_tool_set()
        result = await tools["get_weather"].ainvoke({})

    assert "Phnom Penh" in result
    assert "mostly clear" in result
    assert "32.0" in result


@pytest.mark.asyncio
async def test_get_weather_reports_failure() -> None:
    with patch(
        "summary_messages.graph.tools.httpx.AsyncClient",
        return_value=_FakeWeatherClient(error=RuntimeError("network error")),
    ):
        tools, _, _, _ = build_tool_set()
        result = await tools["get_weather"].ainvoke({})

    assert "Couldn't fetch the weather" in result
