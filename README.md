# Summary Messages

Telegram group summarizer built with Python, `uv`, Postgres, and pluggable LLM providers.

## Features

- Stores group messages in Postgres
- Summarizes a custom time window with `/summary 1h`
- Sends a daily digest automatically
- Supports English, Khmer, and Sing Khmer / romanized Khmer chat
- Can use Gemini, OpenRouter, DeepSeek, or Ollama as the summarization model backend

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

## Docker

Use `POSTGRES_URL` for an external Postgres database such as Supabase, then start the bot:

```bash
docker compose up --build
```

If you want a local Postgres container instead, use the optional profile:

```bash
docker compose --profile local-db up --build
```

## Telegram Commands

- `/summary` - summarize the default window
- `/summary 1h` - summarize the last hour
- `/summary 24h` - summarize the last 24 hours

## Environment

- `TELEGRAM_BOT_TOKEN` - Telegram bot token from BotFather
- `LLM_PROVIDER` - `gemini`, `openrouter`, `deepseek`, `huggingface`, `hashn0de`, or `ollama`
- `LLM_MODEL` - provider model name
- `GEMINI_API_KEY` - required when `LLM_PROVIDER=gemini`
- `OPENROUTER_API_KEY` - required when `LLM_PROVIDER=openrouter`
- `DEEPSEEK_API_KEY` - required when `LLM_PROVIDER=deepseek`
- `OLLAMA_BASE_URL` - OpenAI-compatible Ollama endpoint
- `POSTGRES_URL` - Postgres connection string. For Supabase, use the pooler/direct URL from the project dashboard
- `DAILY_SUMMARY_TIME` - daily digest time in 24-hour `HH:MM`
- `TIMEZONE` - timezone for daily scheduling
