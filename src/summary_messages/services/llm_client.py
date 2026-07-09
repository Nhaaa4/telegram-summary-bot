from __future__ import annotations

import asyncio
from dataclasses import dataclass

from google import genai
from openai import OpenAI

from ..configs import Settings
from ..models import SummaryPrompt


@dataclass(slots=True)
class SummaryClient:
    settings: Settings

    async def summarize(self, prompt: SummaryPrompt) -> str:
        return await asyncio.to_thread(self._summarize_sync, prompt)

    def _summarize_sync(self, prompt: SummaryPrompt) -> str:
        if self.settings.llm_provider == "gemini":
            keys = self.settings.gemini_api_keys
            if not keys:
                raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
            last_error = None
            for api_key in keys:
                try:
                    client = genai.Client(api_key=api_key)
                    response = client.models.generate_content(
                        model=self.settings.llm_model,
                        contents=[prompt.system, prompt.user],
                    )
                    text = getattr(response, "text", None)
                    if text:
                        return text.strip()
                    return str(response)
                except Exception as exc:
                    last_error = exc
                    continue
            raise last_error  # type: ignore[misc]

        if self.settings.llm_provider == "openai":
            api_key = self.settings.openai_api_key
            if not api_key:
                raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
                temperature=0.2,
                max_tokens=500,
            )
            choice = response.choices[0].message.content or ""
            return choice.strip()

        if self.settings.llm_provider == "openrouter":
            api_key = self.settings.openrouter_api_key
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")
            client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
            response = client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
                temperature=0.2,
                max_tokens=500,
            )
            choice = response.choices[0].message.content or ""
            return choice.strip()

        if self.settings.llm_provider == "hashn0de":
            api_key = self.settings.hashn0de_api_key
            if not api_key:
                raise ValueError("HASHN0DE_API_KEY is required when LLM_PROVIDER=hashn0de")
            client = OpenAI(api_key=api_key, base_url="https://api.hashn0de.com/v1")
            response = client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
                temperature=0.4,
                max_tokens=700,
            )
            choice = response.choices[0].message.content or ""
            return choice.strip()

        if self.settings.llm_provider == "deepseek":
            api_key = self.settings.deepseek_api_key
            if not api_key:
                raise ValueError("DEEPSEEK_API_KEY is required when LLM_PROVIDER=deepseek")
            client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
            response = client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
                temperature=0.2,
                max_tokens=700,
            )
            choice = response.choices[0].message.content or ""
            return choice.strip()

        if self.settings.llm_provider == "ollama":
            client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")
            response = client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
                temperature=0.2,
            )
            choice = response.choices[0].message.content or ""
            return choice.strip()

        if self.settings.llm_provider == "huggingface":
            api_key = self.settings.hf_token
            if not api_key:
                raise ValueError("HF_TOKEN is required when LLM_PROVIDER=huggingface")
            client = OpenAI(api_key=api_key, base_url="https://router.huggingface.co/v1")
            response = client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
                temperature=0.3,
                max_tokens=700
            )
            choice = response.choices[0].message.content or ""
            return choice.strip()

        raise ValueError(f"Unsupported LLM provider: {self.settings.llm_provider}")
