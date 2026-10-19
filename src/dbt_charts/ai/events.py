"""Typed event protocol for the terminal agent loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from dbt_charts.ai.failures import AITurnFailure

# -- Stream events (yielded by LLM provider adapters) -----------------------


@dataclass(slots=True)
class ContentDelta:
    """Incremental text chunk from the model."""

    delta: str


@dataclass(slots=True)
class ThinkingStatus:
    """Reasoning/thinking status from the model.

    ``status`` restates the whole summary so far, not an increment, so a
    consumer that appends every event shows one paragraph many times over.
    ``block`` identifies which reasoning block the text belongs to, letting a
    consumer keep one entry per block and replace its text as it grows.
    Adjacency is not a substitute: consecutive summary parts arrive with no
    other event between them, so only the key separates "same block, longer
    text" from "next block".
    """

    status: str
    block: str


@dataclass(slots=True)
class ToolCallEvent:
    """Model requested a tool invocation."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class ReasoningItemsEvent:
    """The provider reasoning items of one response, in output order.

    Consumed by the agent loop (stored on the turn's ``AssistantMessage`` for
    replay) and never re-yielded to agent-event consumers.
    """

    items: list[dict[str, Any]]


StreamEvent = ContentDelta | ThinkingStatus | ToolCallEvent | ReasoningItemsEvent

# -- Agent-level events (yielded by the agent loop) -------------------------


@dataclass(slots=True)
class ToolResultEvent:
    """Result of executing a tool call.

    ``duration_s`` is measured around ``dispatch_tool_call`` in the agent
    loop, not derived by pairing this event to its ``ToolCallEvent`` — batch
    dispatch runs tool calls sequentially in a loop after the model's stream
    drains, so a call-to-result gap would bill call N for every call before
    it. ``outcome`` is the tri-state read off the handler's return envelope
    by ``dbt_charts.ai.tools.tool_call_outcome`` — a bool can't carry
    ``partial`` (a render that produced output but has chart errors).
    """

    id: str
    name: str
    result: Any
    duration_s: float
    outcome: Literal["ok", "partial", "error"]


@dataclass(slots=True)
class AgentDone:
    """Agent finished with a final text response."""

    response: str


AGENT_ERROR_MESSAGE = "An error occurred while processing your request."


@dataclass(slots=True)
class AgentError:
    """Agent encountered an error.

    ``message`` is always safe to show a user. ``details`` carries the
    underlying third-party text for logs and single-user tools, and is None
    when the message is the whole story (an authored, actionable failure).

    ``reason`` is required and deliberately undefaulted: a fourth terminal
    added later must fail to construct rather than quietly report ``internal``,
    which is how a real failure mode goes uncounted. It is a typed member, never
    parsed back out of ``details`` — that text is third-party prose.
    """

    message: str
    reason: AITurnFailure
    details: str | None = None


# ReasoningItemsEvent is consumed by the loop and never part of what it yields.
AgentEvent = (
    ContentDelta
    | ThinkingStatus
    | ToolCallEvent
    | ToolResultEvent
    | AgentDone
    | AgentError
)
