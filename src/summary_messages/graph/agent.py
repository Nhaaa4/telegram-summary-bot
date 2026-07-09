from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Sequence, TypedDict
from zoneinfo import ZoneInfo

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import RetryPolicy

from ..configs import Settings
from ..services import CHAT_SYSTEM_PROMPT


def build_chat_model(settings: Settings) -> BaseChatModel:
    provider = settings.llm_provider

    if provider == "gemini":
        keys = settings.gemini_api_keys
        if not keys:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        return ChatGoogleGenerativeAI(model=settings.llm_model, api_key=keys[0])

    if provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        return ChatOpenAI(model=settings.llm_model, api_key=settings.openai_api_key)

    if provider == "openrouter":
        if not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")
        return ChatOpenAI(model=settings.llm_model, api_key=settings.openrouter_api_key, base_url="https://openrouter.ai/api/v1")

    if provider == "hashn0de":
        if not settings.hashn0de_api_key:
            raise ValueError("HASHN0DE_API_KEY is required when LLM_PROVIDER=hashn0de")
        return ChatOpenAI(model=settings.llm_model, api_key=settings.hashn0de_api_key, base_url="https://api.hashn0de.com/v1")

    if provider == "deepseek":
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required when LLM_PROVIDER=deepseek")
        return ChatOpenAI(model=settings.llm_model, api_key=settings.deepseek_api_key, base_url="https://api.deepseek.com")

    if provider == "ollama":
        return ChatOpenAI(model=settings.llm_model, api_key="ollama", base_url="http://localhost:11434/v1")

    if provider == "huggingface":
        if not settings.hf_token:
            raise ValueError("HF_TOKEN is required when LLM_PROVIDER=huggingface")
        return ChatOpenAI(model=settings.llm_model, api_key=settings.hf_token, base_url="https://router.huggingface.co/v1")

    raise ValueError(f"Unsupported LLM provider: {provider}")


class AgentState(TypedDict):
    # add_messages appends new messages to state instead of replacing it.
    messages: Annotated[Sequence[BaseMessage], add_messages]


def _build_upcoming_dates_table(now: datetime, days: int = 14) -> str:
    # Small/quantized models are unreliable at weekday arithmetic ("this Sunday" from a
    # Thursday), so hand them a precomputed lookup table instead of asking them to compute
    # offsets themselves — they just need to read off the right row.
    lines = []
    for offset in range(days):
        day = now + timedelta(days=offset)
        label = "today" if offset == 0 else "tomorrow" if offset == 1 else None
        suffix = f" ({label})" if label else ""
        lines.append(f"{day.strftime('%A')} {day.strftime('%Y-%m-%d')}{suffix}")
    return "\n".join(lines)


def build_graph(
    model: BaseChatModel,
    tools: list[BaseTool],
    timezone_name: str,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    model_with_tools = model.bind_tools(tools)
    tz = ZoneInfo(timezone_name)
    return_direct_tools = {t.name for t in tools if getattr(t, "return_direct", False)}

    def agent(state: AgentState) -> dict:
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            now = datetime.now(tz)
            system_prompt = (
                f"{CHAT_SYSTEM_PROMPT.strip()}\n\n"
                f"[CURRENT TIME]\nIt is currently {now.strftime('%A, %Y-%m-%d %H:%M')} ({timezone_name}).\n\n"
                f"[UPCOMING DATES]\nUse this table to resolve any relative date the user gives (\"this Sunday\", "
                f"\"next Friday\", \"tomorrow\", \"in 3 days\", etc). Look up the row instead of calculating the "
                f"offset yourself — small arithmetic mistakes here are easy to make:\n"
                f"{_build_upcoming_dates_table(now)}"
            )
            messages = [SystemMessage(content=system_prompt), *messages]
        response = model_with_tools.invoke(messages)
        return {"messages": [response]}

    def route_after_tools(state: AgentState) -> str:
        # ToolNode may append several ToolMessages (parallel tool calls) — if any of them
        # came from a return_direct tool (e.g. send_sticker), end the turn immediately
        # instead of looping back for the model to write a redundant text reply.
        last_message = state["messages"][-1]
        if isinstance(last_message, ToolMessage) and last_message.name in return_direct_tools:
            return END
        return "agent"

    workflow = StateGraph(AgentState)
    # Transient errors (network hiccups, provider rate limits) get retried here so a
    # blip doesn't fall all the way through to the generic error message in bot.py.
    workflow.add_node("agent", agent, retry_policy=RetryPolicy(max_attempts=3))
    workflow.add_node("tools", ToolNode(tools, handle_tool_errors=True))

    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", END: END},
    )
    workflow.add_conditional_edges("tools", route_after_tools, {"agent": "agent", END: END})

    return workflow.compile(checkpointer=checkpointer)
