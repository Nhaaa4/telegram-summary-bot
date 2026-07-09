from __future__ import annotations

import logging
from collections import defaultdict
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timezone, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from openai import APIStatusError
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from ..configs import Settings
from ..graph import build_chat_model, build_graph, build_tools
from ..models import StoredMessage
from ..repositories import Database
from ..services import BlackjackGame, SummaryClient, SummaryService, build_joke_prompt, build_predict_prompt, build_quote_prompt, build_roast_prompt, coinflip

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BotRuntime:
    settings: Settings
    database: Database
    service: SummaryService
    scheduler: AsyncIOScheduler


async def _send_markdown(send, text: str) -> None:
    try:
        await send(text, parse_mode=ParseMode.MARKDOWN)
    except BadRequest:
        await send(text)


async def _reply_markdown(message, text: str) -> None:
    await _send_markdown(message.reply_text, text)


async def _reply_chunked(message, text: str) -> None:
    if len(text) <= 3900:
        await _reply_markdown(message, text)
        return

    start = 0
    while start < len(text):
        await _reply_markdown(message, text[start : start + 3900])
        start += 3900


def _is_group_chat(chat) -> bool:
    return bool(chat) and chat.type in {"group", "supergroup"}


def _chat_display_name(chat) -> str:
    return chat.title or chat.full_name or str(chat.id)


def _extract_reply_text(content) -> str:
    # Some providers (e.g. Gemini/Gemma "thinking" models via ChatGoogleGenerativeAI) return
    # message.content as a list of content blocks (reasoning + text) instead of a plain
    # string. Keep only the actual text blocks so reasoning never leaks into the chat reply.
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts).strip()
    return str(content)


def _llm_error_message(exc: Exception) -> str:
    if isinstance(exc, APIStatusError):
        if exc.status_code == 402:
            return "The configured LLM account has insufficient balance. Add credits or switch to a different provider/model in .env."
        if exc.status_code in {401, 403}:
            return "The configured LLM credentials were rejected. Check the provider key in .env."
        if exc.status_code == 429:
            return "The LLM provider is rate-limiting requests right now. Try again in a moment."
    return "The summary provider failed right now. Try again later or switch to a different provider/model in .env."


class SummaryBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.postgres_url)
        self.client = SummaryClient(settings)
        self.service = SummaryService(settings=settings, database=self.database, client=self.client)
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone_info)
        self.balances: dict[int, int] = defaultdict(lambda: 1000)
        self._bj_games: dict[tuple[int, int], BlackjackGame] = {}
        self._chat_model = None
        self._agent_checkpointer: AsyncPostgresSaver | None = None
        self._checkpointer_stack = AsyncExitStack()
        self.application: Application | None = None

    async def initialize(self) -> None:
        await self.database.initialize()
        logger.info("Database initialized at %s", self.settings.postgres_url)

    async def _upsert_chat_from(self, chat) -> None:
        await self.database.upsert_chat(
            chat_id=chat.id,
            chat_title=_chat_display_name(chat),
            daily_summary_enabled=True,
            daily_summary_time=self.settings.daily_summary_time,
            timezone_name=self.settings.timezone,
            summary_language=self.settings.summary_language,
        )

    def build_application(self) -> Application:
        application = (
            ApplicationBuilder()
            .token(self.settings.telegram_bot_token)
            .post_init(self.post_init)
            .post_shutdown(self.post_shutdown)
            .build()
        )
        application.add_error_handler(self.error_handler)
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("summary", self.summary_command))
        application.add_handler(CommandHandler("daily_summary", self.daily_summary_command))
        application.add_handler(CommandHandler("bj", self.bj_command))
        application.add_handler(CommandHandler("cf", self.cf_command))
        application.add_handler(CommandHandler("fuck", self.fuck_command))
        application.add_handler(CommandHandler("predict", self.predict_command))
        application.add_handler(CommandHandler("joke", self.joke_command))
        application.add_handler(CommandHandler("quote", self.quote_command))
        application.add_handler(CallbackQueryHandler(self.bj_callback, pattern="^bj_"))
        application.add_handler(MessageHandler(~filters.COMMAND, self.store_message))
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Entity(MessageEntity.MENTION),
                self.mention_handler,
            ),
            group=1,
        )
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.private_chat_handler,
            ),
            group=2,
        )
        return application

    async def post_init(self, application: Application) -> None:
        await self.initialize()
        # prepare_threshold=None disables server-side prepared statements — required when
        # postgres_url goes through a transaction-mode pooler (e.g. Supabase's pooler port),
        # which can route the same client connection to different backends and orphan
        # psycopg's cached prepared statements (psycopg.errors.DuplicatePreparedStatement).
        checkpointer_conn = await self._checkpointer_stack.enter_async_context(
            await AsyncConnection.connect(
                self.settings.postgres_url,
                autocommit=True,
                prepare_threshold=None,
                row_factory=dict_row,
            )
        )
        self._agent_checkpointer = AsyncPostgresSaver(conn=checkpointer_conn)
        await self._agent_checkpointer.setup()
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
        await self._checkpointer_stack.aclose()
        logger.info("Bot shutdown complete")

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception("Unhandled bot exception", exc_info=context.error)

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
            "/bj <bet> - play blackjack\n"
            "/cf - flip a coin\n"
            "/fuck @user - roast a user\n"
            "/predict <question> - AI predicts anything\n"
            "/joke - tell a random joke\n"
            "/quote - get today's quote\n"
            "@botname - chat with the bot, and ask it to set, list, or cancel reminders"
        )

    async def summary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return
        if not _is_group_chat(chat):
            await message.reply_text("This command only works in group chats where I can store messages.")
            return

        window_spec = " ".join(context.args).strip() or self.settings.summary_window_default
        logger.info("Summary requested for chat_id=%s chat_title=%r window=%s", chat.id, chat.title, window_spec)
        await self._upsert_chat_from(chat)
        try:
            window, summary = await self.service.summarize_chat(
                chat_id=chat.id,
                chat_title=_chat_display_name(chat),
                window_spec=window_spec,
                output_language=self.settings.summary_language,
                timezone_name=self.settings.timezone,
            )
        except ValueError as exc:
            logger.warning("Summary request failed for chat_id=%s: %s", chat.id, exc)
            await message.reply_text(str(exc))
            if self.settings.fallback_sticker_file_id:
                await message.reply_sticker(sticker=self.settings.fallback_sticker_file_id)
            return
        except Exception as exc:
            logger.exception("Summary generation failed for chat_id=%s", chat.id)
            await message.reply_text(_llm_error_message(exc))
            if self.settings.fallback_sticker_file_id:
                await message.reply_sticker(sticker=self.settings.fallback_sticker_file_id)
            return

        await _reply_chunked(message, f"Summary for {window.label}\n\n{summary}")
        logger.info("Summary sent for chat_id=%s window=%s", chat.id, window.label)

    async def daily_summary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return
        if not _is_group_chat(chat):
            await message.reply_text("This command only works in group chats where I can store messages.")
            return
        logger.info("Manual daily summary requested for chat_id=%s chat_title=%r", chat.id, chat.title)
        await self._send_daily_summary_for_chat(chat.id, _chat_display_name(chat))

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
        if not _is_group_chat(chat):
            logger.info("Skipping non-group message for chat_id=%s chat_type=%s", chat.id, chat.type)
            return
        if getattr(message, "sticker", None):
            await self._upsert_chat_from(chat)
            await self.database.store_sticker(chat_id=chat.id, file_id=message.sticker.file_id)
            logger.info("Stored sticker chat_id=%s file_id=%s", chat.id, message.sticker.file_id)
            return
        text = message.text or message.caption
        if not text:
            logger.info(
                "Skipping message without text/caption for chat_id=%s message_id=%s",
                chat.id,
                message.message_id,
            )
            return

        chat_title = _chat_display_name(chat)
        await self._upsert_chat_from(chat)
        await self.database.store_message(
            StoredMessage(
                chat_id=chat.id,
                chat_title=chat_title,
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
            chat_title,
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
            chat_record = await self.database.get_chat(chat_id)
        if chat_record is None:
            logger.warning("No chat record found for daily summary chat_id=%s", chat_id)
            return

        try:
            summary = await self.service.summarize_daily_chat(chat_record)
            await self._send_message(chat_id, f"Daily summary for {chat_title}\n\n{summary}")
            logger.info("Daily summary sent for chat_id=%s chat_title=%r", chat_id, chat_title)
        except Exception as exc:
            logger.exception("Daily summary failed for chat_id=%s chat_title=%r", chat_id, chat_title)
            await self._send_message(chat_id, _llm_error_message(exc))

    async def _send_message(self, chat_id: int, text: str) -> None:
        application = self.application
        if application is None:
            logger.warning("Cannot send message because application is not initialized for chat_id=%s", chat_id)
            return
        await _send_markdown(
            lambda text, **kwargs: application.bot.send_message(chat_id=chat_id, text=text, **kwargs),
            text,
        )

    async def send_reminder(
        self,
        reminder_id: int,
        chat_id: int,
        user_name: str,
        text: str,
    ) -> None:
        try:
            application = self.application

            if application is None:
                logger.warning("Application is not initialized")
                return

            await application.bot.send_message(
                chat_id=chat_id,
                text=f"⏰ Reminder for {user_name}:\n\n{text}",
            )

            await self.database.mark_reminder_sent(reminder_id)

            logger.info("Reminder sent successfully: id=%s chat_id=%s", reminder_id, chat_id)

        except Exception:
            logger.exception("Failed to send reminder %s", reminder_id)
            if self.settings.fallback_sticker_file_id:
                await application.bot.send_sticker(
                    chat_id=chat_id,
                    sticker=self.settings.fallback_sticker_file_id,
                )

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
            await _reply_markdown(message, f"😂 {joke}")
        except Exception as exc:
            logger.error("Joke failed: %s", exc)
            await message.reply_text("😂 Why did the chicken cross the road? To get to the other side!")

    async def quote_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message:
            return
        await message.reply_chat_action("typing")
        prompt = build_quote_prompt()
        try:
            quote = await self.client.summarize(prompt)
            await _reply_markdown(message, f"💬 {quote}")
        except Exception as exc:
            logger.error("Quote failed: %s", exc)
            await message.reply_text('💬 "The only way to do great work is to love what you do." — Steve Jobs')

    async def _reply_with_agent(
        self,
        *,
        chat_id: int,
        chat_title: str,
        user_id: int,
        user_name: str,
        text: str,
        message,
        is_group_chat: bool,
    ) -> None:
        await message.reply_chat_action("typing")
        try:
            if self._chat_model is None:
                self._chat_model = build_chat_model(self.settings)

            tools = build_tools(
                settings=self.settings,
                database=self.database,
                chat_id=chat_id,
                user_id=user_id,
                user_name=user_name,
                timezone_name=self.settings.timezone,
                scheduler=self.scheduler,
                bot=self,
            )
            graph = build_graph(self._chat_model, tools, self.settings.timezone, checkpointer=self._agent_checkpointer)

            # thread_id scopes the checkpointer's memory to this chat group, so the graph
            # recalls prior turns itself instead of us re-sending history on every call.
            config: RunnableConfig = {
                "configurable": {"thread_id": str(chat_id)},
                "recursion_limit": 10,
            }
            messages = [HumanMessage(content=f"{user_name} said: {text}")]

            result = await graph.ainvoke({"messages": messages}, config=config)
            last_message = result["messages"][-1]

            # send_sticker already sends the sticker itself as a side effect — skip the
            # redundant text reply so the sticker is the only thing the user sees.
            if isinstance(last_message, ToolMessage) and last_message.name == "send_sticker":
                return

            await _reply_markdown(message, _extract_reply_text(last_message.content))
        except Exception as exc:
            logger.error("Agent reply failed: %s", exc)
            await message.reply_text(_llm_error_message(exc))
            if self.settings.fallback_sticker_file_id:
                await message.reply_sticker(self.settings.fallback_sticker_file_id)

    async def private_chat_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if not message or not chat or not user:
            return
        if chat.type != "private":
            return

        text = (message.text or message.caption or "").strip()
        if not text:
            return

        await self._reply_with_agent(
            chat_id=chat.id,
            chat_title=_chat_display_name(chat),
            user_id=user.id,
            user_name=user.full_name,
            text=text,
            message=message,
            is_group_chat=False,
        )

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
            await message.reply_text(f"I'm here @{bot_username}. Tell me what you need.")
            return

        await self._reply_with_agent(
            chat_id=chat.id,
            chat_title=_chat_display_name(chat),
            user_id=user.id,
            user_name=user.full_name,
            text=text,
            message=message,
            is_group_chat=True,
        )

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
            await _reply_markdown(message, f"🔮 Prediction: {question}\n\n{prediction}")
        except Exception as exc:
            logger.error("Prediction failed: %s", exc)
            await message.reply_text("🔮 The crystal ball is cloudy right now. Try again later.")
            if self.settings.fallback_sticker_file_id:
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
            await _reply_markdown(message, f"🔥 {mentioned}\n{roast}")
            if self.settings.fallback_sticker_file_id:
                await message.reply_sticker(self.settings.fallback_sticker_file_id)
        except Exception as exc:
            logger.error("Roast failed: %s", exc)
            await message.reply_text(f" {mentioned}\nfuck you little boy")
            if self.settings.fallback_sticker_file_id:
                await message.reply_sticker(self.settings.fallback_sticker_file_id)

    def run(self) -> None:
        application = self.build_application()
        self.application = application
        logger.info("Starting Telegram polling")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
