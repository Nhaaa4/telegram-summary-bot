# Repository Guidelines

## Project Structure & Module Organization
Source code lives in `src/summary_messages/`. Keep bot orchestration in `bot.py`, time-window and digest logic in `service.py`, persistence in `db.py`, provider integrations in `llm.py`, and prompt text in `prompts.py`. Runtime configuration is defined in `config.py`, and the CLI entrypoint is `__main__.py`. Tests live under `tests/` and should mirror the module they cover, for example `tests/test_service.py`. SQLite data is stored in `data/summary_messages.sqlite3`. Repository-level docs and agent notes live in `README.md` and `.github/agents/`.

## Build, Test, and Development Commands
Use `uv` for all local workflows:

- `uv sync --extra dev` installs runtime and dev dependencies.
- `uv run summary-messages` starts the Telegram bot.
- `uv run pytest` runs the test suite.
- `uv run ruff check .` runs linting.

Copy `.env.example` to `.env` before running the bot and set `TELEGRAM_BOT_TOKEN` plus the selected LLM provider credentials.

## Coding Style & Naming Conventions
Target Python 3.11+ and follow PEP 8 with 4-space indentation. Prefer type hints on public functions and dataclasses for structured records already modeled that way in the codebase. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes; and keep async function names verb-based, such as `summarize_chat` or `store_message`. Keep changes narrow and consistent with the existing module boundaries.

## Testing Guidelines
Tests use `pytest` with `pytest-asyncio` enabled. Name files `test_<module>.py` and keep fixtures or helpers local unless reused broadly. Add focused tests for duration parsing, database round-trips, and summary generation behavior when changing those areas. Prefer small, deterministic tests over network-backed provider calls.

## Commit & Pull Request Guidelines
This workspace does not include `.git` history, so follow a simple convention: use imperative, scoped commit messages such as `feat: add 7d summary window` or `fix: ignore empty group messages`. Pull requests should describe the user-facing change, note any `.env` or schema impact, list test commands run, and include chat-command examples when bot behavior changes.

## Security & Configuration Tips
Do not commit `.env`, API keys, or generated chat data. Treat stored group messages as sensitive. When changing prompts or storage behavior, preserve multilingual handling for English, Khmer, and romanized Khmer chats.
