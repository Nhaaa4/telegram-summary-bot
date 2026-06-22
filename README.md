# Summary Messages

Telegram group summarizer built with Python, `uv`, SQLite, and pluggable LLM providers.

## Features

- Stores group messages in SQLite
- Summarizes a custom time window with `/summary 1h`
- Sends a daily digest automatically
- Supports English, Khmer, and Sing Khmer / romanized Khmer chat
- Can use Gemini, OpenRouter, or Ollama as the summarization model backend

## Quick Start

1. Copy `.env.example` to `.env` and fill in the values.
2. Install dependencies and create the environment:
   ```bash
   uv sync --extra dev
   ```
3. Run the bot:
   ```bash
   uv run summary-messages
   ```

## Telegram Commands

- `/summary` - summarize the default window
- `/summary 1h` - summarize the last hour
- `/summary 24h` - summarize the last 24 hours

## Environment

- `TELEGRAM_BOT_TOKEN` - Telegram bot token from BotFather
- `LLM_PROVIDER` - `gemini`, `openrouter`, or `ollama`
- `LLM_MODEL` - provider model name
- `GEMINI_API_KEY` - required when `LLM_PROVIDER=gemini`
- `OPENROUTER_API_KEY` - required when `LLM_PROVIDER=openrouter`
- `OLLAMA_BASE_URL` - OpenAI-compatible Ollama endpoint
- `SQLITE_PATH` - SQLite database location
- `DAILY_SUMMARY_TIME` - daily digest time in 24-hour `HH:MM`
- `TIMEZONE` - timezone for daily scheduling

## Agent Files

The workspace includes a custom agent in [`.github/agents/telegram-summary-bot.agent.md`](.github/agents/telegram-summary-bot.agent.md) for Telegram summary bot work.
