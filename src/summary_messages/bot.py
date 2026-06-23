from __future__ import annotations

import re
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import timezone, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import dateparser
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, Update
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Settings
from .db import Database, StoredMessage
from .games import BlackjackGame, coinflip
from .llm import SummaryClient
from .prompts import build_chat_prompt, build_joke_prompt, build_predict_prompt, build_roast_prompt
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
        await message.reply_text(text, parse_mode="MarkdownV2")
        return

    start = 0
    while start < len(text):
        await message.reply_text(text[start : start + 3900], parse_mode="MarkdownV2")
        start += 3900


class SummaryBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.postgres_url)
        self.client = SummaryClient(settings)
        self.service = SummaryService(settings=settings, database=self.database, client=self.client)
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone_info)
        self.balances: dict[int, int] = defaultdict(lambda: 1000)
        self._bj_games: dict[tuple[int, int], BlackjackGame] = {}
        self.conversations: dict[tuple[int, int], list[tuple[str, str]]] = defaultdict(list)

    async def initialize(self) -> None:
        await self.database.initialize()
        logger.info("Database initialized at %s", self.settings.postgres_url)

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
        application.add_handler(CommandHandler("reminder", self.reminder_command))
        application.add_handler(CommandHandler("bj", self.bj_command))
        application.add_handler(CommandHandler("cf", self.cf_command))
        application.add_handler(CommandHandler("fuck", self.fuck_command))
        application.add_handler(CommandHandler("predict", self.predict_command))
        application.add_handler(CommandHandler("joke", self.joke_command))
        application.add_handler(CallbackQueryHandler(self.bj_callback, pattern="^bj_"))
        application.add_handler(MessageHandler(~filters.COMMAND, self.store_message))
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Entity(MessageEntity.MENTION),
                self.mention_handler,
            ),
            group=1,
        )
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
        await self.restore_pending_reminders()
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
            "/summary - summarize the default window\n"
            "/summary 1m - summarize the last minute\n"
            "/summary 1h - summarize the last hour\n"
            "/summary 24h - summarize the last 24 hours\n"
            "/daily_summary - force the daily digest now\n"
            "/reminder <text> at 4 pm tomorrow - set a reminder\n"
            "/bj <bet> - play blackjack\n"
            "/cf - flip a coin\n"
            "/fuck @user - roast a user\n"
            "/predict <question> - AI predicts anything\n"
            "/joke - tell a random joke\n"
            "@botname - chat with the bot"
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
            await message.sticker(self.settings.fallback_sticker_file_id)
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
        await application.bot.send_message(chat_id=chat_id, text=text, parse_mode="MarkdownV2")

    async def reminder_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user

        if not message or not chat or not user:
            return

        raw_text = " ".join(context.args).strip()

        if not raw_text:
            await message.reply_text(
                "Usage:\n"
                "/reminder go to pool 4:00 pm\n"
                "/reminder go to pool at 4 pm tomorrow\n"
                "/reminder drink water in 10 minutes"
            )
            return

        parsed = self.parse_reminder(raw_text=raw_text)

        if not parsed:
            await message.reply_text(
                "I couldn't understand the reminder time.\n\n"
                "Try:\n"
                "/reminder go to pool at 4 pm tomorrow\n"
                "/reminder drink water in 10 minutes"
            )
            return

        raw_text, remind_at = parsed
        now = datetime.now(remind_at.tzinfo)

        if remind_at <= now:
            await message.reply_text("Please choose a future time.")
            return

        reminder_id = await self.database.create_reminder(
            chat_id=chat.id,
            user_id=user.id,
            user_name=user.full_name,
            text=raw_text,
            remind_at=remind_at,
        )

        self.scheduler.add_job(
            self.send_reminder,
            trigger="date",
            run_date=remind_at,
            args=[reminder_id, chat.id, user.full_name, raw_text],
            id=f"reminder-{reminder_id}",
            replace_existing=True,
        )

        await message.reply_text(
            f"✅ Reminder set!\n\n"
            f"Reminder: {raw_text}\n"
            f"Time: {remind_at.strftime('%Y-%m-%d %I:%M %p')}"
        )
    
    
    def parse_reminder(self, raw_text: str) -> tuple[str, datetime] | None:
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

        return reminder_text, remind_at
    
    
    async def send_reminder(
        self,
        reminder_id: int,
        chat_id: int,
        user_name: str,
        text: str,
    ) -> None:
        try:
            await self._send_message(
                chat_id,
                f"⏰ Reminder for {user_name}:\n\n{text}",
            )
            await self.database.mark_reminder_sent(reminder_id)
        except Exception as exc:
            await self.application.bot.sticker(chat_id=chat_id, sticker=self.settings.fallback_sticker_file_id)
            logger.error("Failed to send reminder %s: %s", reminder_id, exc)
    
    async def restore_pending_reminders(self) -> None:
        reminders = await self.database.list_pending_reminders()
        now = datetime.now(timezone.utc)

        for reminder in reminders:
            remind_at = reminder["remind_at"]

            if remind_at <= now:
                await self.send_reminder(
                    reminder["id"],
                    reminder["chat_id"],
                    reminder["user_name"],
                    reminder["text"],
                )
                continue

            self.scheduler.add_job(
                self.send_reminder,
                trigger="date",
                run_date=remind_at,
                args=[
                    reminder["id"],
                    reminder["chat_id"],
                    reminder["user_name"],
                    reminder["text"],
                ],
                id=f"reminder-{reminder['id']}",
                replace_existing=True,
            )

        logger.info("Restored %s pending reminders", len(reminders))
    
    
    async def joke_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message:
            return
        await message.reply_chat_action("typing")
        prompt = build_joke_prompt()
        try:
            joke = await self.client.summarize(prompt)
            await message.reply_text(f"😂 {joke}")
        except Exception as exc:
            logger.error("Joke failed: %s", exc)
            await message.reply_text("😂 Why did the chicken cross the road? To get to the other side!")

    async def mention_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if not message or not chat or not user:
            return
        if chat.type not in {"group", "supergroup"}:
            return

        bot_username = context.bot.username
        if not bot_username:
            return

        mentioned = False
        for entity in message.entities:
            if entity.type == "mention":
                mention = message.text[entity.offset:entity.offset + entity.length]
                if mention.lower() == f"@{bot_username.lower()}":
                    mentioned = True
                    break

        if not mentioned:
            return

        text = message.text or ""
        for entity in reversed(message.entities):
            if entity.type == "mention":
                text = text[:entity.offset] + text[entity.offset + entity.length:]
        text = text.strip()

        if not text:
            return

        key = (chat.id, user.id)
        history = self.conversations.get(key, [])

        lower = text.lower()
        members = self.settings.group_members_list
        member_lower = {m.lower() for m in members}
        creator_keywords = ["who created", "who made", "who build", "who built", "your master", "who owns", "who own", "who is your creator", "who made you", "who created you", "who owns you", "who's your master", "who your master", "your creator"]
        if any(kw in lower for kw in creator_keywords):
            member_list = ", ".join(members) if members else ""
            parts = [f"I was created by the {self.settings.group_name} group."]
            parts.append(f"{self.settings.group_name} is a software and design project or collective team. Members associated with the project include technology students and aspiring software developers, such as those at the Cambodia Academy of Digital Technology.")
            if member_list:
                parts.append(f"Members: {member_list}.")
            parts.append("GitHub: https://github.com/COPPSARY/")
            await message.reply_text(" ".join(parts))
            return

        group_name_lower = self.settings.group_name.lower()
        list_keywords = ["list the people", "list members", "list team", "who are the members", "list names", "people i can smash", "all the members", "the members"]
        if any(kw in lower for kw in list_keywords) and group_name_lower in lower:
            member_list = ", ".join(members) if members else "No members configured."
            await message.reply_text(f"Members of {self.settings.group_name}: {member_list}.")
            return

        for m in member_lower:
            if m in lower and ("do you know" in lower or "who is" in lower or "tell me about" in lower):
                await message.reply_text(f"Yes! {m.capitalize()} is a member of {self.settings.group_name} — a software and design collective of technology students and aspiring developers at the Cambodia Academy of Digital Technology. GitHub: https://github.com/COPPSARY/")
                return

        await message.reply_chat_action("typing")
        prompt = build_chat_prompt(user_name=user.full_name, message=text, history=history)
        try:
            reply = await self.client.summarize(prompt)
            await message.reply_text(reply)
            history.append((text, reply))
            self.conversations[key] = history[-10:]
        except Exception as exc:
            logger.error("Chat reply failed: %s", exc)
            if self.settings.fallback_sticker_file_id:
                await message.reply_sticker(self.settings.fallback_sticker_file_id)
            else:
                await message.reply_text("I can't answer that right now.")

    async def predict_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message:
            return

        question = " ".join(context.args).strip()
        if not question:
            await message.reply_text("Usage: /predict <question>\nExample: /predict who wins the world cup?")
            return

        await message.reply_chat_action("typing")
        prompt = build_predict_prompt(question=question)
        try:
            prediction = await self.client.summarize(prompt)
            await message.reply_text(f"🔮 Prediction: {question}\n\n{prediction}")
        except Exception as exc:
            logger.error("Prediction failed: %s", exc)
            await message.reply_text("🔮 The crystal ball is cloudy right now. Try again later.")
            await message.reply_sticker(self.settings.fallback_sticker_file_id)

    async def bj_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        if not message or not user:
            return

        user_id = user.id
        if context.args:
            try:
                bet = int(context.args[0])
            except ValueError:
                await message.reply_text("Usage: /bj <bet>")
                return
        else:
            bet = 10

        if bet < 1:
            await message.reply_text("Bet must be at least 1.")
            return
        if self.balances[user_id] < bet:
            await message.reply_text(f"You only have ${self.balances[user_id]}. Not enough to bet ${bet}.")
            return

        game = BlackjackGame()
        game.bet = bet
        game.deal()
        chat_id = update.effective_chat.id if update.effective_chat else 0
        self._bj_games[(chat_id, user_id)] = game

        status = f"🃏 Blackjack\nBet: ${bet}\n\nDealer: {game.dealer_hand_str}\nPlayer: {game.player_hand_str}"

        if game.state == "blackjack":
            payout = int(bet * 1.5)
            self.balances[user_id] += payout
            await message.reply_text(
                f"{status}\n\nBlackjack! You win ${payout}!\nBalance: ${self.balances[user_id]}"
            )
            del self._bj_games[(chat_id, user_id)]
        else:
            keyboard = [
                [InlineKeyboardButton("Hit", callback_data="bj_hit"),
                 InlineKeyboardButton("Stand", callback_data="bj_stand")]
            ]
            await message.reply_text(status, reply_markup=InlineKeyboardMarkup(keyboard))

    async def bj_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return

        game = self._bj_games.get((chat.id, user.id))
        if not game:
            await query.edit_message_text("No active game. Start one with /bj <bet>")
            return

        if game.state != "playing":
            return

        action = query.data
        if action == "bj_hit":
            game.hit()
        elif action == "bj_stand":
            game.stand()

        status = f"🃏 Blackjack\nBet: ${game.bet}\n\nDealer: {game.dealer_hand_str}\nPlayer: {game.player_hand_str}"

        if game.state == "playing":
            keyboard = [
                [InlineKeyboardButton("Hit", callback_data="bj_hit"),
                 InlineKeyboardButton("Stand", callback_data="bj_stand")]
            ]
            await query.edit_message_text(status, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            user_id = user.id
            payout = game.payout()
            self.balances[user_id] += payout

            result_map = {
                "blackjack": "Blackjack! 🎉",
                "player_win": "You win! 🎉",
                "dealer_win": "Dealer wins!",
                "push": "Push!",
                "player_bust": "You bust! 💥",
                "dealer_bust": "Dealer busts! 🎉",
            }
            result_line = result_map.get(game.state, "")

            full = f"Dealer: {game.dealer_hand_full} ({game.dealer_value})\nPlayer: {game.player_hand_str} ({game.player_value})"
            await query.edit_message_text(
                f"{result_line}\n\n{full}\n\nPayout: ${payout:+d}\nBalance: ${self.balances[user_id]}"
            )
            del self._bj_games[(chat.id, user.id)]

    async def cf_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message:
            return

        result = coinflip()
        if context.args and context.args[0].lower() in ("heads", "tails"):
            guess = context.args[0].lower()
            user_result = result.lower()
            if guess == user_result:
                await message.reply_text(f"{result} 🎉\nYou guessed right!")
            else:
                await message.reply_text(f"{result}\nWrong! It was {result}.")
        else:
            await message.reply_text(f"🪙 {result}")

    async def fuck_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message:
            return

        if not message.entities:
            await message.reply_text("Mention someone to roast, e.g. /fuck @username")
            return

        mentioned = None
        for entity in message.entities:
            if entity.type == "mention":
                mentioned = message.text[entity.offset:entity.offset + entity.length]
                break
            if entity.type == "text_mention" and entity.user:
                mentioned = entity.user.full_name
                break

        if not mentioned:
            await message.reply_text("Mention someone to roast, e.g. /fuck @username")
            return

        await message.reply_chat_action("typing")
        prompt = build_roast_prompt(user_name=mentioned)
        try:
            roast = await self.client.summarize(prompt)
            await message.reply_text(f"🔥 {mentioned}\n{roast}")
            await message.reply_sticker(self.settings.fallback_sticker_file_id)
        except Exception as exc:
            logger.error("Roast failed: %s", exc)
            await message.reply_text(f" {mentioned}\nfuck you little boy")
            await message.reply_sticker(self.settings.fallback_sticker_file_id)

    application: Application | None = None

    def run(self) -> None:
        application = self.build_application()
        self.application = application
        logger.info("Starting Telegram polling")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
