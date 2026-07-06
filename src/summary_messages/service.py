from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

from .config import Settings
from .db import ChatRecord, Database, StoredMessage
from .llm import SummaryClient
from .prompts import build_summary_prompt, format_message_line

_DURATION_RE = re.compile(r"^(?P<value>\d+)\s*(?P<unit>[mhdw])$", re.IGNORECASE)


@dataclass(slots=True)
class SummaryWindow:
    label: str
    start: datetime
    end: datetime


class SummaryService:
    def __init__(self, *, settings: Settings, database: Database, client: SummaryClient) -> None:
        self.settings = settings
        self.database = database
        self.client = client

    def parse_window(self, spec: str | None, *, now: datetime | None = None) -> SummaryWindow:
        current = now or datetime.now(timezone.utc)
        text = (spec or self.settings.summary_window_default).strip().lower()
        if text == "default":
            text = self.settings.summary_window_default

        if text == "daily":
            text = "24h"

        match = _DURATION_RE.fullmatch(text)
        if not match:
            raise ValueError("Use a duration like 30m, 1h, 24h, or 7d")

        value = int(match.group("value"))
        unit = match.group("unit").lower()
        if unit == "m":
            delta = timedelta(minutes=value)
            label = f"last {value} minute{'s' if value != 1 else ''}"
        elif unit == "h":
            delta = timedelta(hours=value)
            label = f"last {value} hour{'s' if value != 1 else ''}"
        elif unit == "d":
            delta = timedelta(days=value)
            label = f"last {value} day{'s' if value != 1 else ''}"
        else:
            delta = timedelta(weeks=value)
            label = f"last {value} week{'s' if value != 1 else ''}"

        start = current - delta
        return SummaryWindow(label=label, start=start, end=current)

    async def summarize_window(
        self,
        *,
        chat_id: int,
        chat_title: str,
        window: SummaryWindow,
        output_language: str | None = None,
        timezone_name: str = "UTC",
    ) -> str:
        messages = await self.database.get_messages(
            chat_id=chat_id,
            start=window.start,
            end=window.end,
            limit=self.settings.max_messages_per_summary,
        )
        if not messages:
            return f"No stored group messages were found for {window.label}."

        formatted = self._format_messages(messages, timezone_name)
        prompt = build_summary_prompt(
            chat_title=chat_title,
            window_label=window.label,
            messages=formatted,
            output_language=output_language or self.settings.summary_language,
        )
        summary = await self.client.summarize(prompt)
        await self.database.save_summary_run(
            chat_id=chat_id,
            window_start=window.start,
            window_end=window.end,
            summary_text=summary,
        )
        return summary

    async def summarize_chat(
        self,
        *,
        chat_id: int,
        chat_title: str,
        window_spec: str | None = None,
        output_language: str | None = None,
        timezone_name: str = "UTC",
        now: datetime | None = None,
    ) -> tuple[SummaryWindow, str]:
        window = self.parse_window(window_spec, now=now)
        summary = await self.summarize_window(
            chat_id=chat_id,
            chat_title=chat_title,
            window=window,
            output_language=output_language,
            timezone_name=timezone_name,
        )
        return window, summary

    async def summarize_daily_chat(
        self,
        chat: ChatRecord,
        *,
        now: datetime | None = None,
    ) -> str:
        window = self.parse_window("24h", now=now)
        summary = await self.summarize_window(
            chat_id=chat.chat_id,
            chat_title=chat.chat_title,
            window=SummaryWindow(label="daily digest", start=window.start, end=window.end),
            output_language=chat.summary_language,
            timezone_name=chat.timezone,
        )
        return summary

    def _format_messages(self, messages: list[StoredMessage], timezone_name: str) -> list[str]:
        tz = ZoneInfo(timezone_name)
        return [
            format_message_line(message.created_at.astimezone(tz), message.user_name, message.text)
            for message in messages
        ]
