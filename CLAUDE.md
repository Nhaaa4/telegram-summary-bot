# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Telegram bot (`summary-messages`) that stores group chat messages in Postgres and summarizes them on demand or on a daily schedule via a pluggable LLM backend. Also bundles a few unrelated fun commands (blackjack, coinflip, roast, predict, joke, reminders, mention-based chat).

## Commands

- `uv sync --extra dev` — install runtime + dev dependencies
- `uv run summary-messages` — run the bot (requires `.env`, copy from `.env.example`)
- `uv run pytest` — run the full test suite
- `uv run pytest tests/test_service.py::test_summarize_window_skips_llm_when_no_messages` — run a single test
- `uv run ruff check .` — lint

Docker: `docker compose up --build` (uses external `POSTGRES_URL`, e.g. Supabase) or `docker compose --profile local-db up --build` to also start a local Postgres container.

## Architecture

Code is organized by responsibility under `src/summary_messages/`, each layer a subpackage with an `__init__.py` re-exporting its public names:

- `configs/settings.py` — `Settings` (pydantic-settings), loaded from `.env`. Holds LLM provider selection, up to 5 rotating Gemini keys (`gemini_api_keys`), Postgres URL, timezone, and group metadata.
- `models/` — plain dataclasses shared across layers: `StoredMessage`/`ChatRecord` (message.py), `SummaryWindow` (summary.py), `SummaryPrompt` (prompt.py).
- `repositories/database.py` — `Database`, a thin async wrapper over `psycopg` (raw SQL, no ORM). Schema (`chats`, `messages`, `summary_runs`, `reminders`) is created idempotently in `initialize()` via `CREATE TABLE IF NOT EXISTS`. Note: a Postgres trigger (`delete_old_messages_fn`) auto-deletes messages older than 7 days on every insert — the bot is not a long-term message archive.
- `services/llm_client.py` — `SummaryClient.summarize()` dispatches synchronously (wrapped in `asyncio.to_thread`) to one of: gemini (with key rotation/fallback across `gemini_api_keys`), openai, openrouter, hashn0de, deepseek, ollama, huggingface — all except gemini go through the `openai` SDK client pointed at a different `base_url`. Adding a provider means adding a branch here plus a `Literal` entry in `configs/settings.py`.
- `services/prompts.py` — builds `SummaryPrompt` (system/user text pairs) for summaries, chat replies, jokes, roasts, and predictions. Multilingual handling (English, Khmer, romanized/"Sing Khmer") must be preserved when editing prompt text.
- `services/summary_service.py` — `SummaryService` parses duration strings (`30m`, `1h`, `24h`, `7d`, `daily`) into a `SummaryWindow`, pulls messages from `Database`, formats them, calls `SummaryClient`, and persists the result via `save_summary_run`.
- `services/games.py` — pure, dependency-free game logic (blackjack `Deck`/`BlackjackGame`, `coinflip`) with no bot/LLM coupling — testable in isolation.
- `bot/bot.py` — `SummaryBot` wires everything together: builds the `python-telegram-bot` `Application`, registers command/message handlers, owns an `AsyncIOScheduler` (APScheduler) for the daily digest cron job and one-off reminder jobs, and keeps in-memory per-user state (blackjack games, chat balances, short conversation history for mention/DM replies — none of this is persisted). `_llm_error_message` maps provider errors (e.g. `APIStatusError` 402/401/403/429) to user-facing text.
- `__main__.py` — sets the Windows selector event loop policy when needed, configures a timezone-aware log formatter, and starts the bot.

### Data flow for `/summary`

`bot/bot.py` command handler → `Database.upsert_chat` (ensures chat row exists) → `SummaryService.summarize_chat` → `Database.get_messages` (window-bounded, capped at `max_messages_per_summary`) → `services/prompts.build_summary_prompt` → `SummaryClient.summarize` → `Database.save_summary_run` → chunked reply (Telegram has a message-length limit, see `_reply_chunked`).

### Reminders

Parsed with a small set of regexes plus `dateparser` (`bot/bot.py: parse_reminder`), scheduled via APScheduler `date` triggers, and persisted in the `reminders` table so `restore_pending_reminders()` can re-arm jobs after a restart (firing immediately if the time already passed).

## Testing conventions

- `pytest-asyncio` is in `auto` mode (`pyproject.toml`) — existing tests still include `@pytest.mark.asyncio` explicitly even though it isn't strictly required.
- Tests mock `Database`/`SummaryClient` with `SimpleNamespace` + `AsyncMock` rather than hitting Postgres or a real LLM provider (see `tests/test_service.py`).
- Test files mirror the module they cover (`tests/test_service.py`, `tests/test_bot.py`, `tests/test_config.py`) and import from the package paths (`summary_messages.configs`, `summary_messages.models`, `summary_messages.services`, `summary_messages.bot`).

## Configuration notes

- `LLM_PROVIDER` selects the backend; each provider has its own required API key env var (see `configs/settings.py` for the full `Literal` and `.env.example` for the variable list).
- Gemini supports up to 5 keys (`GEMINI_API_KEY`..`GEMINI_API_KEY5`) for automatic fallback on failure.
- Do not commit `.env`, API keys, or real chat data — stored group messages are treated as sensitive.
