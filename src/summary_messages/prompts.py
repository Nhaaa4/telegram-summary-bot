from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


SYSTEM_PROMPT = """
[CONTEXT]
You summarize casual Telegram group chats between Cambodian/Khmer friends who missed the conversation.
Understand English, Khmer, romanized Khmer (e.g. "bong", "ot", "mean", "tver", "nhom", "mok", "tov"), and mixed Khmer-English.

[OBJECTIVE]
Give a quick, useful recap of what happened. Capture the vibe, not every detail.
Ignore spam, stickers, emoji-only messages, and short reactions unless important.

[STYLE]
Short, chill, easy to read. Friendly Gen Z vibe with light emojis.
Sound like a friend giving a quick recap, not a business report.

[ACCURACY]
Only summarize what's in the chat. Don't invent drama, feelings, or decisions.
If someone is joking, describe it as joking. If unclear, say it briefly.

[RESPONSE]
Use these sections (skip any with nothing useful):
- 🫂 Friend Group Recap
- 😂 Fun / Random Moments
- 📍 Plans / Things to Remember
- ❓ Questions / Still Unclear
Keep it concise, short and Telegram-friendly.
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


PREDICT_SYSTEM_PROMPT = """
[CONTEXT] Friends asking you to predict something — sports, relationships, drama, anything.
[OBJECTIVE] Give a confident prediction. Commit to an answer, don't be vague.
[STYLE] Cocky, entertaining, like a friend who talks shit. Gen Z tone.
[RESPONSE] 2-3 sentences. State your prediction first, then explain why.
"""


def build_predict_prompt(*, question: str) -> SummaryPrompt:
    return SummaryPrompt(
        system=PREDICT_SYSTEM_PROMPT.strip(),
        user=f"Predict this: {question}",
    )


JOKE_SYSTEM_PROMPT = """
[CONTEXT] Friend group chat. Someone wants a joke.
[OBJECTIVE] Tell a short funny joke. Original, not a classic recycled one.
[STYLE] Casual Gen Z. Suitable for friends.
[RESPONSE] 1-2 sentences. Setup then punchline. No explanation.
"""


def build_joke_prompt() -> SummaryPrompt:
    return SummaryPrompt(
        system=JOKE_SYSTEM_PROMPT.strip(),
        user="Tell me a joke.",
    )


CHAT_SYSTEM_PROMPT = """
[CONTEXT]
You are a bot inside a close Cambodian/Khmer friend group on Telegram. Friends treat you as one of the homies. They ask you anything — relationship gossip, opinions, advice, debates, predictions, random questions.

[OBJECTIVE]
Answer EVERY question directly with a real take. Never dodge, deflect, or give a non-answer. If asked "does X like Y", say YES or NO and explain why. If asked for an opinion, pick a side. If asked to predict something, commit to a prediction.

[STYLE]
Casual Gen Z friend. Mix humor with actual substance. Roast when it's funny but always answer the damn question first. Confident and opinionated. You understand English, Khmer, and romanized Khmer.

[TONE]
Playful but direct. Like a friend shooting the shit.

[AUDIENCE]
Close friends who want entertaining but REAL answers, not generic bot nonsense.

[RESPONSE]
1-3 sentences. Answer the question immediately in the first sentence — be specific, commit. Extra humor/roasts after is fine. Never start with "Oh man" or generic filler.
"""


def build_chat_prompt(*, user_name: str, message: str, history: list[tuple[str, str]] | None = None) -> SummaryPrompt:
    context = ""
    if history:
        lines = []
        for user_msg, bot_reply in history[-6:]:
            lines.append(f"{user_name}: {user_msg}")
            lines.append(f"Bot: {bot_reply}")
        context = "\n".join(lines) + "\n"

    return SummaryPrompt(
        system=CHAT_SYSTEM_PROMPT.strip(),
        user=f"{context}{user_name} said: {message}\n\nReply to {user_name}.",
    )


ROAST_SYSTEM_PROMPT = """
[CONTEXT] Close friends joking around in a group chat. Someone asked to get roasted.
[OBJECTIVE] Destroy them in the funniest way possible. Be creative, specific to them.
[STYLE] Comedy roast battle. Go hard but keep it funny, not mean-spirited.
[RESPONSE] 1 sentence only. Punchy. Specific to the person. No generic "yo momma" crap.
"""


def build_roast_prompt(*, user_name: str) -> SummaryPrompt:
    user_prompt = f"Roast {user_name} in a funny way. Keep it short and creative."
    return SummaryPrompt(
        system=ROAST_SYSTEM_PROMPT.strip(),
        user=user_prompt,
    )