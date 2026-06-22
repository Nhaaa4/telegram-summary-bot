from __future__ import annotations

import aiosqlite
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    chat_title TEXT NOT NULL,
    daily_summary_enabled INTEGER NOT NULL DEFAULT 1,
    daily_summary_time TEXT NOT NULL DEFAULT '23:00',
    timezone TEXT NOT NULL DEFAULT 'UTC',
    summary_language TEXT NOT NULL DEFAULT 'English',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    chat_title TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    user_id INTEGER,
    user_name TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    UNIQUE(chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS summary_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TRIGGER IF NOT EXISTS delete_old_messages
AFTER INSERT ON messages
BEGIN
    DELETE FROM messages
    WHERE created_at < datetime('now', '-2 days');
END;
"""


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


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as conn:
            await conn.executescript(SCHEMA)
            await conn.commit()

    async def upsert_chat(
        self,
        *,
        chat_id: int,
        chat_title: str,
        daily_summary_enabled: bool = True,
        daily_summary_time: str = "23:00",
        timezone_name: str = "UTC",
        summary_language: str = "English",
    ) -> None:
        updated_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                """
                INSERT INTO chats (
                    chat_id, chat_title, daily_summary_enabled, daily_summary_time,
                    timezone, summary_language, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    chat_title=excluded.chat_title,
                    daily_summary_enabled=excluded.daily_summary_enabled,
                    daily_summary_time=excluded.daily_summary_time,
                    timezone=excluded.timezone,
                    summary_language=excluded.summary_language,
                    updated_at=excluded.updated_at
                """,
                (
                    chat_id,
                    chat_title,
                    1 if daily_summary_enabled else 0,
                    daily_summary_time,
                    timezone_name,
                    summary_language,
                    updated_at,
                ),
            )
            await conn.commit()

    async def store_message(self, message: StoredMessage) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                """
                INSERT OR IGNORE INTO messages (
                    chat_id, chat_title, message_id, user_id, user_name, text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.chat_id,
                    message.chat_title,
                    message.message_id,
                    message.user_id,
                    message.user_name,
                    message.text,
                    message.created_at.astimezone(timezone.utc).isoformat(),
                ),
            )
            await conn.commit()

    async def get_messages(
        self,
        *,
        chat_id: int,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[StoredMessage]:
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT chat_id, chat_title, message_id, user_id, user_name, text, created_at
                FROM messages
                WHERE chat_id = ? AND created_at >= ? AND created_at <= ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (
                    chat_id,
                    start.astimezone(timezone.utc).isoformat(),
                    end.astimezone(timezone.utc).isoformat(),
                    limit,
                ),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [
            StoredMessage(
                chat_id=row["chat_id"],
                chat_title=row["chat_title"],
                message_id=row["message_id"],
                user_id=row["user_id"],
                user_name=row["user_name"],
                text=row["text"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    async def list_active_chats(self) -> list[ChatRecord]:
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT chat_id, chat_title, daily_summary_enabled, daily_summary_time, timezone, summary_language
                FROM chats
                WHERE daily_summary_enabled = 1
                ORDER BY chat_title COLLATE NOCASE ASC
                """
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [
            ChatRecord(
                chat_id=row["chat_id"],
                chat_title=row["chat_title"],
                daily_summary_enabled=bool(row["daily_summary_enabled"]),
                daily_summary_time=row["daily_summary_time"],
                timezone=row["timezone"],
                summary_language=row["summary_language"],
            )
            for row in rows
        ]

    async def save_summary_run(
        self,
        *,
        chat_id: int,
        window_start: datetime,
        window_end: datetime,
        summary_text: str,
    ) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                """
                INSERT INTO summary_runs (chat_id, window_start, window_end, summary_text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    window_start.astimezone(timezone.utc).isoformat(),
                    window_end.astimezone(timezone.utc).isoformat(),
                    summary_text,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await conn.commit()
