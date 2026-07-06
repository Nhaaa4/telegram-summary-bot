from __future__ import annotations

from summary_messages.config import Settings


def test_settings_accept_deepseek_provider() -> None:
    settings = Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "test-token",
            "LLM_PROVIDER": "deepseek",
            "LLM_MODEL": "deepseek-chat",
            "DEEPSEEK_API_KEY": "test-key",
            "POSTGRES_URL": "postgresql://postgres:postgres@localhost:5432/summary_bot_test",
        }
    )

    assert settings.llm_provider == "deepseek"
    assert settings.deepseek_api_key == "test-key"