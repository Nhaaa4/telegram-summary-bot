from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id BIGINT PRIMARY KEY,
    chat_title TEXT NOT NULL,
    daily_summary_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    daily_summary_time TEXT NOT NULL DEFAULT '23:00',
    timezone TEXT NOT NULL DEFAULT 'UTC',
    summary_language TEXT NOT NULL DEFAULT 'English',
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chats_daily_summary_enabled ON chats(daily_summary_enabled);

CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    chat_title TEXT NOT NULL,
    message_id BIGINT NOT NULL,
    user_id BIGINT,
    user_name TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    UNIQUE(chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_id_created_at ON messages(chat_id, created_at);

CREATE TABLE IF NOT EXISTS summary_runs (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    summary_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE OR REPLACE FUNCTION delete_old_messages_fn()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM messages
    WHERE created_at < NOW() - INTERVAL '7 days';

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS delete_old_messages ON messages;

CREATE TRIGGER delete_old_messages
AFTER INSERT ON messages
FOR EACH ROW
EXECUTE FUNCTION delete_old_messages_fn();
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
    def __init__(self, url: str) -> None:
        self.url = url

    async def get_connection(self) -> psycopg.AsyncConnection:
        if not self.url.startswith(("postgresql://", "postgres://")):
            raise ValueError("Invalid database URL. Must start with 'postgresql://'.")

        return await psycopg.AsyncConnection.connect(self.url, row_factory=dict_row)

    async def initialize(self) -> None:
        async with await self.get_connection() as conn:
            await conn.execute(SCHEMA)
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
        updated_at = datetime.now(timezone.utc)
        async with await self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO chats (
                    chat_id, chat_title, daily_summary_enabled, daily_summary_time,
                    timezone, summary_language, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
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
                    daily_summary_enabled,
                    daily_summary_time,
                    timezone_name,
                    summary_language,
                    updated_at,
                ),
            )
            await conn.commit()

    async def store_message(self, message: StoredMessage) -> None:
        async with await self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO messages (
                    chat_id, chat_title, message_id, user_id, user_name, text, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chat_id, message_id) DO NOTHING
                """,
                (
                    message.chat_id,
                    message.chat_title,
                    message.message_id,
                    message.user_id,
                    message.user_name,
                    message.text,
                    message.created_at.astimezone(timezone.utc),
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
        async with await self.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT chat_id, chat_title, message_id, user_id, user_name, text, created_at
                FROM messages
                WHERE chat_id = %s AND created_at >= %s AND created_at <= %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (
                    chat_id,
                    start.astimezone(timezone.utc),
                    end.astimezone(timezone.utc),
                    limit,
                ),
            )
            rows = await cursor.fetchall()
        return [
            StoredMessage(
                chat_id=row["chat_id"],
                chat_title=row["chat_title"],
                message_id=row["message_id"],
                user_id=row["user_id"],
                user_name=row["user_name"],
                text=row["text"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def list_active_chats(self) -> list[ChatRecord]:
        async with await self.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT chat_id, chat_title, daily_summary_enabled, daily_summary_time, timezone, summary_language
                FROM chats
                WHERE daily_summary_enabled = TRUE
                ORDER BY LOWER(chat_title) ASC
                """
            )
            rows = await cursor.fetchall()
        return [
            ChatRecord(
                chat_id=row["chat_id"],
                chat_title=row["chat_title"],
                daily_summary_enabled=row["daily_summary_enabled"],
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
        async with await self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO summary_runs (chat_id, window_start, window_end, summary_text, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    chat_id,
                    window_start.astimezone(timezone.utc),
                    window_end.astimezone(timezone.utc),
                    summary_text,
                    datetime.now(timezone.utc),
                ),
            )
            await conn.commit()
