from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from langchain_core.tools import BaseTool, tool
from tavily import AsyncTavilyClient

from ..configs import Settings
from ..repositories import Database

logger = logging.getLogger(__name__)

# Phnom Penh, Cambodia.
_PHNOM_PENH_LATITUDE = 11.5564
_PHNOM_PENH_LONGITUDE = 104.9282

# WMO weather codes (https://open-meteo.com/en/docs), collapsed to plain-English labels.
_WEATHER_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "light rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "light rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "light snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with light hail", 99: "thunderstorm with heavy hail",
}

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _resolve_relative_day(relative_day: str, now: datetime) -> str | None:
    """Resolve "today"/"tomorrow"/a weekday name to YYYY-MM-DD deterministically.

    Small/quantized LLMs are unreliable at this kind of date arithmetic, so weekday
    resolution happens here in code instead of being left to the model.
    """
    key = relative_day.strip().lower()
    if key == "today":
        return now.strftime("%Y-%m-%d")
    if key == "tomorrow":
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    if key in _WEEKDAYS:
        # "this Sunday" said on a Thursday means the upcoming Sunday (3 days out); said on
        # a Sunday it means today. (target - today) % 7 gives exactly that.
        offset = (_WEEKDAYS[key] - now.weekday()) % 7
        return (now + timedelta(days=offset)).strftime("%Y-%m-%d")
    return None


def build_tools(
    *,
    settings: Settings,
    database: Database,
    chat_id: int,
    user_id: int,
    user_name: str,
    timezone_name: str,
    scheduler: AsyncIOScheduler,
    bot,
) -> list[BaseTool]:
    @tool(description="Create a reminder for the user at a specific future date and time.")
    async def create_reminder(text: str, time: str, date: str | None = None, relative_day: str | None = None) -> str:
        """Create a reminder for the user at a specific future date and time.

        Args:
            text: What to remind the user about.
            time: The reminder time as 24-hour HH:MM (e.g. "09:00", "16:30").
            date: The reminder date as YYYY-MM-DD. Only use this for an absolute date the
                user actually stated (e.g. "August 15", "2026-09-01"). For anything relative
                ("today", "tomorrow", a weekday name like "Sunday" or "next Friday"), use
                `relative_day` instead and leave this out — do not compute the date yourself.
            relative_day: "today", "tomorrow", or a weekday name ("monday".."sunday") when
                the user said something relative like "tomorrow" or "this Sunday"/"next
                Friday" (weekday names always mean the closest upcoming occurrence). Leave
                this out and use `date` instead if the user gave an absolute calendar date.
        Returns:
            A confirmation message with the reminder's id and scheduled time, or an error
            message if the date/time was invalid or in the past.
        """
        logger.info(
            "tool call: create_reminder text=%r date=%s relative_day=%s time=%s chat_id=%s user_id=%s",
            text, date, relative_day, time, chat_id, user_id,
        )
        now_local = datetime.now(ZoneInfo(timezone_name))
        if relative_day:
            resolved = _resolve_relative_day(relative_day, now_local)
            if not resolved:
                logger.warning("tool result: create_reminder rejected unknown relative_day %r", relative_day)
                return f"I don't recognize '{relative_day}' as today/tomorrow/a weekday name."
            date = resolved
        if not date:
            logger.warning("tool result: create_reminder rejected missing date")
            return "I need a date or a relative day (today/tomorrow/a weekday name) to set the reminder."

        try:
            naive = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        except ValueError:
            logger.warning("tool result: create_reminder rejected invalid date/time %r %r", date, time)
            return f"Invalid date/time '{date} {time}'. Use YYYY-MM-DD for date and 24-hour HH:MM for time."

        remind_at = naive.replace(tzinfo=ZoneInfo(timezone_name))

        now = datetime.now(remind_at.tzinfo)
        if remind_at <= now:
            logger.warning("tool result: create_reminder rejected past time remind_at=%s now=%s", remind_at, now)
            return "That time is in the past. Ask the user for a future time."

        reminder_id = await database.create_reminder(
            chat_id=chat_id,
            user_id=user_id,
            user_name=user_name,
            text=text,
            remind_at=remind_at,
        )

        scheduler.add_job(
            bot.send_reminder,
            trigger="date",
            run_date=remind_at,
            args=[reminder_id, chat_id, user_name, text],
            id=f"reminder-{reminder_id}",
            replace_existing=True,
        )

        logger.info("tool result: create_reminder created id=%s remind_at=%s", reminder_id, remind_at)
        return f"Reminder #{reminder_id} set for {remind_at.strftime('%Y-%m-%d %I:%M %p')}: {text}"

    @tool(description="List the user's pending reminders in this chat.")
    async def list_reminders() -> str:
        """List the user's pending reminders in this chat.

        Returns:
            A newline-separated list of "#id — text at time", or "No pending reminders."
        """
        logger.info("tool call: list_reminders chat_id=%s user_id=%s", chat_id, user_id)
        reminders = await database.list_reminders_for_user(chat_id, user_id)
        if not reminders:
            logger.info("tool result: list_reminders found none")
            return "No pending reminders."
        tz = ZoneInfo(timezone_name)
        lines = [
            f"#{r['id']} — {r['text']} at {r['remind_at'].astimezone(tz).strftime('%Y-%m-%d %I:%M %p')}"
            for r in reminders
        ]
        logger.info("tool result: list_reminders found %s", len(reminders))
        return "\n".join(lines)

    @tool(description="Cancel a pending reminder by its id.")
    async def cancel_reminder(reminder_id: int) -> str:
        """Cancel a pending reminder by its id.

        Args:
            reminder_id: The reminder's id, as shown by list_reminders.
        Returns:
            A confirmation message, or a "not found" message if it doesn't exist for this user.
        """
        logger.info("tool call: cancel_reminder reminder_id=%s chat_id=%s user_id=%s", reminder_id, chat_id, user_id)
        deleted = await database.delete_reminder(reminder_id, user_id)
        if not deleted:
            logger.warning("tool result: cancel_reminder not found id=%s", reminder_id)
            return f"No reminder #{reminder_id} found for this user."

        try:
            scheduler.remove_job(f"reminder-{reminder_id}")
        except JobLookupError:
            pass

        logger.info("tool result: cancel_reminder cancelled id=%s", reminder_id)
        return f"Reminder #{reminder_id} cancelled."

    @tool(description="Update the text and/or the date/time of an existing pending reminder.")
    async def update_reminder(
        reminder_id: int,
        text: str | None = None,
        date: str | None = None,
        relative_day: str | None = None,
        time: str | None = None,
    ) -> str:
        """Update the text and/or the date/time of an existing pending reminder.

        Args:
            reminder_id: The reminder's id, as shown by list_reminders.
            text: The new reminder text, or omit to leave it unchanged.
            date: The new reminder date as YYYY-MM-DD, or omit to leave it unchanged. Only
                use this for an absolute date the user actually stated (e.g. "August 15") —
                for anything relative, use `relative_day` instead. Provide together with `time`.
            relative_day: "today", "tomorrow", or a weekday name ("monday".."sunday") when
                the user gave a relative day like "tomorrow" or "this Sunday" (weekday names
                always mean the closest upcoming occurrence). Provide together with `time`.
            time: The new reminder time as 24-hour HH:MM, or omit to leave it unchanged.
                Provide together with `date` or `relative_day`.
        Returns:
            A confirmation message with the updated details, or an error message if the
            reminder wasn't found, the date/time was invalid, or the new time is in the past.
        """
        logger.info(
            "tool call: update_reminder reminder_id=%s text=%r date=%s relative_day=%s time=%s chat_id=%s user_id=%s",
            reminder_id, text, date, relative_day, time, chat_id, user_id,
        )
        if relative_day:
            resolved = _resolve_relative_day(relative_day, datetime.now(ZoneInfo(timezone_name)))
            if not resolved:
                logger.warning("tool result: update_reminder rejected unknown relative_day %r", relative_day)
                return f"I don't recognize '{relative_day}' as today/tomorrow/a weekday name."
            date = resolved

        remind_at = None
        if date or time:
            if not (date and time):
                logger.warning("tool result: update_reminder rejected partial date/time date=%r time=%r", date, time)
                return "To change the time, provide both date (or relative_day) and time together."
            try:
                naive = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
            except ValueError:
                logger.warning("tool result: update_reminder rejected invalid date/time %r %r", date, time)
                return f"Invalid date/time '{date} {time}'. Use YYYY-MM-DD for date and 24-hour HH:MM for time."

            remind_at = naive.replace(tzinfo=ZoneInfo(timezone_name))
            now = datetime.now(remind_at.tzinfo)
            if remind_at <= now:
                logger.warning("tool result: update_reminder rejected past time remind_at=%s now=%s", remind_at, now)
                return "That time is in the past. Ask the user for a future time."

        row = await database.update_reminder(reminder_id, user_id, text=text, remind_at=remind_at)
        if not row:
            logger.warning("tool result: update_reminder not found id=%s", reminder_id)
            return f"No pending reminder #{reminder_id} found for this user."

        if remind_at is not None:
            scheduler.add_job(
                bot.send_reminder,
                trigger="date",
                run_date=remind_at,
                args=[reminder_id, chat_id, user_name, row["text"]],
                id=f"reminder-{reminder_id}",
                replace_existing=True,
            )

        logger.info("tool result: update_reminder updated id=%s remind_at=%s", reminder_id, row["remind_at"])
        local_remind_at = row["remind_at"].astimezone(ZoneInfo(timezone_name))
        return f"Reminder #{reminder_id} updated: {row['text']} at {local_remind_at.strftime('%Y-%m-%d %I:%M %p')}"

    @tool(description="Answer questions about who created, made, built, or owns this bot.")
    async def about_creator() -> str:
        """Answer questions about who created, made, built, or owns this bot.

        Returns:
            A description of the group that created the bot, its members, and GitHub link.
        """
        logger.info("tool call: about_creator")
        members = settings.group_members_list
        parts = [f"I was created by the {settings.group_name} group."]
        parts.append(
            f"{settings.group_name} is a software and design project or collective team. Members associated "
            "with the project include technology students and aspiring software developers, such as those at "
            "the Cambodia Academy of Digital Technology."
        )
        if members:
            parts.append(f"Members: {', '.join(members)}.")
        parts.append(f"GitHub: {settings.group_github_url}")
        logger.info("tool result: about_creator succeeded")
        return " ".join(parts)

    @tool(description="Send a sticker to the chat matching a mood.", return_direct=True)
    async def send_sticker(mood: str) -> str:
        """Send a sticker to the chat matching a mood. Use this when the user explicitly asks
        for a sticker, or when your reply carries one of these strong emotions.

        Args:
            mood: One of "funny", "sad", "cry", or "fun".
        Returns:
            A confirmation message, or an explanation if no sticker is available to send.
        """
        logger.info("tool call: send_sticker mood=%r chat_id=%s", mood, chat_id)
        # Prefer a sticker the group itself has sent before (auto-stored from chat history);
        # fall back to the single fixed sticker configured in .env if none has been stored yet.
        sticker_id = await database.get_random_sticker(chat_id)
        source = "stored"
        if not sticker_id:
            sticker_id = settings.fallback_sticker_file_id
            source = "configured"
        if not sticker_id:
            logger.warning("tool result: send_sticker no sticker available for mood=%r", mood)
            return f"No sticker available to send for mood '{mood}'."

        application = getattr(bot, "application", None)
        if application is None:
            logger.warning("tool result: send_sticker application not initialized")
            return "Can't send a sticker right now."

        await application.bot.send_sticker(chat_id=chat_id, sticker=sticker_id)
        logger.info("tool result: send_sticker sent mood=%r source=%s", mood, source)
        return f"Sent a {mood} sticker."

    @tool(description="Get today's weather in Phnom Penh, Cambodia.")
    async def get_weather() -> str:
        """Get the current weather in Phnom Penh, Cambodia.

        Returns:
            A short weather summary (conditions, temperature, feels-like, humidity, wind),
            or an explanation if the weather service failed.
        """
        logger.info("tool call: get_weather chat_id=%s", chat_id)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": _PHNOM_PENH_LATITUDE,
                        "longitude": _PHNOM_PENH_LONGITUDE,
                        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                        "timezone": "Asia/Bangkok",
                    },
                )
                response.raise_for_status()
                current = response.json()["current"]
        except Exception as exc:
            logger.warning("tool result: get_weather failed error=%s", exc)
            return "Couldn't fetch the weather right now — try again in a bit."

        condition = _WEATHER_CODES.get(current["weather_code"], "unknown conditions")
        summary = (
            f"Phnom Penh: {condition}, {current['temperature_2m']}°C "
            f"(feels like {current['apparent_temperature']}°C), "
            f"{current['relative_humidity_2m']}% humidity, wind {current['wind_speed_10m']} km/h."
        )
        logger.info("tool result: get_weather succeeded")
        return summary

    tools: list[BaseTool] = [
        create_reminder,
        list_reminders,
        cancel_reminder,
        update_reminder,
        about_creator,
        send_sticker,
        get_weather,
    ]

    if settings.tavily_api_key:
        tavily_client = AsyncTavilyClient(api_key=settings.tavily_api_key)

        @tool(description="Search the web for current information.")
        async def web_search(query: str) -> str:
            """Search the web for current information — news, facts, prices, scores, anything
            you don't already know or that could have changed since your training.

            Args:
                query: What to search for. Keep it short and specific, like a search engine query.
            Returns:
                A short answer plus a few source snippets, or an explanation if the search failed.
            """
            logger.info("tool call: web_search query=%r chat_id=%s", query, chat_id)
            try:
                response = await tavily_client.search(query, max_results=5, include_answer=True)
            except Exception as exc:
                logger.warning("tool result: web_search failed query=%r error=%s", query, exc)
                return "Web search failed right now — answer from what you already know, or say you're not sure."

            parts = []
            answer = response.get("answer")
            if answer:
                parts.append(answer)
            for result in response.get("results", [])[:5]:
                title = result.get("title", "").strip()
                url = result.get("url", "").strip()
                content = " ".join(result.get("content", "").split())[:300]
                parts.append(f"- {title} ({url}): {content}")

            if not parts:
                logger.info("tool result: web_search found nothing query=%r", query)
                return f"No web results found for '{query}'."

            logger.info("tool result: web_search succeeded query=%r results=%s", query, len(response.get("results", [])))
            return "\n".join(parts)

        tools.append(web_search)

    return tools
