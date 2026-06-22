from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = Field(validation_alias="TELEGRAM_BOT_TOKEN")
    llm_provider: Literal["gemini", "openrouter", "ollama"] = Field(default="gemini", validation_alias="LLM_PROVIDER")
    llm_model: str = Field(default="gemini-2.0-flash", validation_alias="LLM_MODEL")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    openrouter_api_key: str | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    ollama_base_url: str = Field(default="http://localhost:11434/v1", validation_alias="OLLAMA_BASE_URL")
    sqlite_path: Path = Field(default=Path("data/summary_messages.sqlite3"), validation_alias="SQLITE_PATH")
    summary_language: str = Field(default="English", validation_alias="SUMMARY_LANGUAGE")
    summary_window_default: str = Field(default="24h", validation_alias="SUMMARY_WINDOW_DEFAULT")
    daily_summary_time: str = Field(default="23:00", validation_alias="DAILY_SUMMARY_TIME")
    timezone: str = Field(default="UTC", validation_alias="TIMEZONE")
    max_messages_per_summary: int = Field(default=120, validation_alias="MAX_MESSAGES_PER_SUMMARY")

    @property
    def timezone_info(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def daily_summary_clock(self) -> time:
        hour, minute = self.daily_summary_time.split(":", maxsplit=1)
        return time(hour=int(hour), minute=int(minute), tzinfo=self.timezone_info)
