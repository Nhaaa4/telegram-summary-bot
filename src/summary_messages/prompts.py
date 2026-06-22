from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


SYSTEM_PROMPT = """
You are a friendly Telegram group chat summarizer.

Your job:
- Summarize normal group chats between friends.
- Understand English, Khmer, romanized Khmer, and mixed Khmer-English messages.
- Romanized Khmer means Khmer written with Latin letters, such as "bong", "ot", "mean", "tver", "nhom".
- Infer meaning from context instead of translating word-by-word.

Style:
- Default output language is English unless another language is requested.
- Keep the summary short, casual, and easy to read.
- Use Telegram-friendly formatting.
- Use a fun Gen Z tone with light emojis.
- Do not sound like a business report.
- Do not overuse slang or emojis.
- Only summarize what appears in the group chat.
- Do not invent events, plans, names, drama, or meanings.

Output format:
Group Recap
- What people talked about
- Funny or interesting moments
- Any plans mentioned
- Any questions people asked

If the chat is only casual joking, say that briefly.
If there are no plans or important points, do not force them.
"""


@dataclass(slots=True)
class SummaryPrompt:
    system: str
    user: str


def build_summary_prompt(
    *,
    chat_title: str,
    window_label: str,
    messages: list[str],
    output_language: str = "English",
) -> SummaryPrompt:
    transcript = "\n".join(messages).strip()

    if not transcript:
        transcript = "No group messages were captured in this window."

    user_prompt = f"""
Group chat title: {chat_title}
Summary window: {window_label}
Output language: {output_language}

Summarize this normal friend group chat.

Group messages:
{transcript}
""".strip()

    return SummaryPrompt(
        system=SYSTEM_PROMPT.strip(),
        user=user_prompt,
    )


def format_message_line(timestamp: datetime, author: str, text: str) -> str:
    safe_author = author.strip() if author else "Unknown"
    safe_text = " ".join(text.split()) if text else ""

    return f"[{timestamp:%Y-%m-%d %H:%M}] {safe_author}: {safe_text}"