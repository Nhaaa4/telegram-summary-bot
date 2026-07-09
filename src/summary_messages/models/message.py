from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class StoredMessage:
    chat_id: int
    chat_title: str
    message_id: int
    user_id: int | None
    user_name: str
    text: str
    created_at: datetime


@dataclass(slots=True)
class ChatRecord:
    chat_id: int
    chat_title: str
    daily_summary_enabled: bool
    daily_summary_time: str
    timezone: str
    summary_language: str
