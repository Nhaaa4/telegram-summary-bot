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
    llm_provider: Literal["huggingface", "gemini", "openrouter", "ollama"] = Field(default="huggingface", validation_alias="LLM_PROVIDER")
    llm_model: str = Field(default="gemini-2.5-flash", validation_alias="LLM_MODEL")
    
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_api_key2: str | None = Field(default=None, validation_alias="GEMINI_API_KEY2")
    gemini_api_key3: str | None = Field(default=None, validation_alias="GEMINI_API_KEY3")
    gemini_api_key4: str | None = Field(default=None, validation_alias="GEMINI_API_KEY4")
    gemini_api_key5: str | None = Field(default=None, validation_alias="GEMINI_API_KEY5")
    openrouter_api_key: str | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    ollama_base_url: str = Field(default="http://localhost:11434/v1", validation_alias="OLLAMA_BASE_URL")
    hf_token: str | None = Field(default=None, validation_alias="HF_TOKEN")
    
    postgres_url: str = Field(default="postgresql://postgres:postgres@postgres:5432/summary_bot", validation_alias="POSTGRES_URL")

    summary_language: str = Field(default="English", validation_alias="SUMMARY_LANGUAGE")
    summary_window_default: str = Field(default="24h", validation_alias="SUMMARY_WINDOW_DEFAULT")
    
    daily_summary_time: str = Field(default="23:00", validation_alias="DAILY_SUMMARY_TIME")
    timezone: str = Field(default="Asia/Phnom_Penh", validation_alias="TIMEZONE")
    max_messages_per_summary: int = Field(default=120, validation_alias="MAX_MESSAGES_PER_SUMMARY")

    group_name: str = Field(default="COPPSARY", validation_alias="GROUP_NAME")
    group_members: str = Field(default="", validation_alias="GROUP_MEMBERS")
    fallback_sticker_file_id: str | None = Field(default=None, validation_alias="FALLBACK_STICKER_FILE_ID")

    @property
    def gemini_api_keys(self) -> list[str]:
        return [k for k in [self.gemini_api_key, self.gemini_api_key2, self.gemini_api_key3, self.gemini_api_key4, self.gemini_api_key5] if k]

    @property
    def group_members_list(self) -> list[str]:
        return [m.strip() for m in self.group_members.split(",") if m.strip()]

    @property
    def timezone_info(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def daily_summary_clock(self) -> time:
        hour, minute = self.daily_summary_time.split(":", maxsplit=1)
        return time(hour=int(hour), minute=int(minute), tzinfo=self.timezone_info)
