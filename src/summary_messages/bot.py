from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Settings
from .db import Database, StoredMessage
from .llm import SummaryClient
from .service import SummaryService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BotRuntime:
    settings: Settings
    database: Database
    service: SummaryService
    scheduler: AsyncIOScheduler


async def _reply_chunked(message, text: str) -> None:
    if len(text) <= 3900:
        await message.reply_text(text)
        return

    start = 0
    while start < len(text):
        await message.reply_text(text[start : start + 3900])
        start += 3900


class SummaryBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.sqlite_path)
        self.client = SummaryClient(settings)
        self.service = SummaryService(settings=settings, database=self.database, client=self.client)
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone_info)

    async def initialize(self) -> None:
        await self.database.initialize()
        logger.info("Database initialized at %s", self.settings.sqlite_path)

    def build_application(self) -> Application:
        application = (
            ApplicationBuilder()
            .token(self.settings.telegram_bot_token)
            .post_init(self.post_init)
            .post_shutdown(self.post_shutdown)
            .build()
        )
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("summary", self.summary_command))
        application.add_handler(CommandHandler("daily_summary", self.daily_summary_command))
        application.add_handler(MessageHandler(~filters.COMMAND, self.store_message))
        return application

    async def post_init(self, application: Application) -> None:
        await self.initialize()
        self.scheduler.add_job(
            self.run_daily_summary,
            trigger="cron",
            hour=self.settings.daily_summary_clock.hour,
            minute=self.settings.daily_summary_clock.minute,
            id="daily-summary",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info(
            "Bot started with provider=%s model=%s timezone=%s daily_summary_time=%s",
            self.settings.llm_provider,
            self.settings.llm_model,
            self.settings.timezone,
            self.settings.daily_summary_time,
        )

    async def post_shutdown(self, application: Application) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        logger.info("Bot shutdown complete")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await update.message.reply_text(
            "I am ready. Use /summary 1h to summarize the last hour, or wait for the daily digest."
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await update.message.reply_text(
            "Commands:\n"
            "/summary 1m - summarize the last minute\n"
            "/summary - summarize the default window\n"
            "/summary 1h - summarize the last hour\n"
            "/summary 24h - summarize the last 24 hours\n"
            "/daily_summary - force the daily digest now"
        )

    async def summary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return

        window_spec = " ".join(context.args).strip() or self.settings.summary_window_default
        logger.info("Summary requested for chat_id=%s chat_title=%r window=%s", chat.id, chat.title, window_spec)
        await self.database.upsert_chat(
            chat_id=chat.id,
            chat_title=chat.title or chat.full_name or str(chat.id),
            daily_summary_enabled=True,
            daily_summary_time=self.settings.daily_summary_time,
            timezone_name=self.settings.timezone,
            summary_language=self.settings.summary_language,
        )
        try:
            window, summary = await self.service.summarize_chat(
                chat_id=chat.id,
                chat_title=chat.title or chat.full_name or str(chat.id),
                window_spec=window_spec,
                output_language=self.settings.summary_language,
                timezone_name=self.settings.timezone,
            )
        except ValueError as exc:
            logger.warning("Summary request failed for chat_id=%s: %s", chat.id, exc)
            await message.reply_text(str(exc))
            return

        await _reply_chunked(message, f"Summary for {window.label}\n\n{summary}")
        logger.info("Summary sent for chat_id=%s window=%s", chat.id, window.label)

    async def daily_summary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return
        logger.info("Manual daily summary requested for chat_id=%s chat_title=%r", chat.id, chat.title)
        await self._send_daily_summary_for_chat(chat.id, chat.title or chat.full_name or str(chat.id))
        await message.reply_text("Daily summary sent.")

    async def store_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if not message or not chat or not user:
            logger.info(
                "Skipping update with missing fields: has_message=%s has_chat=%s has_user=%s",
                bool(message),
                bool(chat),
                bool(user),
            )
            return
        if chat.type not in {"group", "supergroup"}:
            logger.info("Skipping non-group message for chat_id=%s chat_type=%s", chat.id, chat.type)
            return
        text = message.text or message.caption
        if not text:
            logger.info(
                "Skipping message without text/caption for chat_id=%s message_id=%s",
                chat.id,
                message.message_id,
            )
            return

        await self.database.upsert_chat(
            chat_id=chat.id,
            chat_title=chat.title or chat.full_name or str(chat.id),
            daily_summary_enabled=True,
            daily_summary_time=self.settings.daily_summary_time,
            timezone_name=self.settings.timezone,
            summary_language=self.settings.summary_language,
        )
        await self.database.store_message(
            StoredMessage(
                chat_id=chat.id,
                chat_title=chat.title or chat.full_name or str(chat.id),
                message_id=message.message_id,
                user_id=user.id,
                user_name=user.full_name,
                text=text,
                created_at=message.date.astimezone(timezone.utc),
            )
        )
        logger.info(
            "Stored message chat_id=%s chat_title=%r message_id=%s user_id=%s text_length=%s",
            chat.id,
            chat.title or chat.full_name or str(chat.id),
            message.message_id,
            user.id,
            len(text),
        )

    async def run_daily_summary(self) -> None:
        chats = await self.database.list_active_chats()
        logger.info("Running scheduled daily summary for %s chats", len(chats))
        for chat in chats:
            await self._send_daily_summary_for_chat(chat.chat_id, chat.chat_title, chat)

    async def _send_daily_summary_for_chat(self, chat_id: int, chat_title: str, chat_record=None) -> None:
        if chat_record is None:
            chats = await self.database.list_active_chats()
            chat_record = next((chat for chat in chats if chat.chat_id == chat_id), None)
        if chat_record is None:
            logger.warning("No chat record found for daily summary chat_id=%s", chat_id)
            return

        summary = await self.service.summarize_daily_chat(chat_record)
        await self._send_message(chat_id, f"Daily summary for {chat_title}\n\n{summary}")
        logger.info("Daily summary sent for chat_id=%s chat_title=%r", chat_id, chat_title)

    async def _send_message(self, chat_id: int, text: str) -> None:
        application = self.application
        if application is None:
            logger.warning("Cannot send message because application is not initialized for chat_id=%s", chat_id)
            return
        await application.bot.send_message(chat_id=chat_id, text=text)

    application: Application | None = None

    def run(self) -> None:
        application = self.build_application()
        self.application = application
        logger.info("Starting Telegram polling")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
