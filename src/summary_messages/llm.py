from __future__ import annotations

import asyncio
from dataclasses import dataclass

from google import genai
from openai import OpenAI

from .config import Settings
from .prompts import SummaryPrompt


@dataclass(slots=True)
class SummaryClient:
    settings: Settings

    async def summarize(self, prompt: SummaryPrompt) -> str:
        return await asyncio.to_thread(self._summarize_sync, prompt)

    def _summarize_sync(self, prompt: SummaryPrompt) -> str:
        if self.settings.llm_provider == "gemini":
            api_key = self.settings.gemini_api_key
            if not api_key:
                raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=self.settings.llm_model,
                contents=[prompt.system, prompt.user],
            )
            text = getattr(response, "text", None)
            if text:
                return text.strip()
            return str(response)

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
            )
            choice = response.choices[0].message.content or ""
            return choice.strip()

        if self.settings.llm_provider == "ollama":
            client = OpenAI(api_key="ollama", base_url=self.settings.ollama_base_url)
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

        raise ValueError(f"Unsupported LLM provider: {self.settings.llm_provider}")
