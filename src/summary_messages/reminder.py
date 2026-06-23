from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
import dateparser


TIME_RE = re.compile(
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ParsedReminder:
    text: str
    remind_at: datetime


def parse_natural_reminder(raw_text: str) -> ParsedReminder | None:
    """
    Examples:
    /reminder go to pool 4:00 pm
    /reminder go to pool at 4 pm tomorrow
    /reminder drink water in 10 minutes
    """

    raw_text = raw_text.strip()

    if not raw_text:
        return None

    # Common time phrases users may write
    time_patterns = [
        r"\bin\s+\d+\s+(?:minute|minutes|hour|hours|day|days)\b",
        r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?(?:\s+tomorrow|\s+tmr)?\b",
        r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)(?:\s+tomorrow|\s+tmr)?\b",
        r"\btomorrow\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b",
        r"\btmr\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b",
    ]

    match = None

    for pattern in time_patterns:
        match = re.search(pattern, raw_text, flags=re.IGNORECASE)
        if match:
            break

    if not match:
        return None

    time_text = match.group(0)

    # Normalize common short word
    time_text = time_text.replace("tmr", "tomorrow")

    remind_at = dateparser.parse(
        time_text,
        settings={
            "TIMEZONE": self.settings.timezone,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )

    if not remind_at:
        return None

    reminder_text = (
        raw_text[: match.start()] + raw_text[match.end() :]
    ).strip()

    reminder_text = re.sub(r"\s+", " ", reminder_text)
    reminder_text = reminder_text.removeprefix("at ").strip()

    if not reminder_text:
        reminder_text = "Reminder"

    return ParsedReminder(text=reminder_text, remind_at=remind_at)