"""Provider-neutral typed agent conversation messages.

One shape, shared by every producer (``run_agent``) and every consumer
(``OpenAIAdapter``): a discriminated union on ``role`` instead of the
informal ``list[dict[str, Any]]`` convention the agent conversation used to
be. An unhandled variant is now a construction error, not a silent drop —
the class of bug that let a raw tool-call envelope leak into chat as prose
(2026-07-23 incident; see ``OpenAIAdapter``'s class docstring in
``dbt_charts/ai/llm.py``).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field

__all__ = [
    "AgentMessage",
    "AssistantMessage",
    "ToolCall",
    "ToolResultMessage",
    "UserMessage",
    "parse_plain_history",
]


class ToolCall(BaseModel):
    """One tool invocation the model requested."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    # Arguments are the tool's own JSON-schema-shaped payload — a genuine
    # runtime boundary value, validated by the tool handler, not by this model.
    arguments: dict[str, Any]


class UserMessage(BaseModel):
    """A user turn: plain text or multimodal content blocks.

    ``content`` blocks (text/image_url/document) are validated by each
    provider converter, which already rejects unsupported block shapes.
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["user"] = "user"
    content: str | list[dict[str, Any]]


class AssistantMessage(BaseModel):
    """A model turn: response text plus any tool calls it requested."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # Provider reasoning items (e.g. OpenAI ``encrypted_content``) replayed
    # verbatim before this turn's tool calls; empty for persisted history.
    reasoning_items: list[dict[str, Any]] = Field(default_factory=list)


class ToolResultMessage(BaseModel):
    """The result of executing one tool call, fed back to the model."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["tool"] = "tool"
    tool_call_id: str
    name: str
    content: str
    # The Responses API's built-in apply_patch carries a status on its output
    # item; every other tool's outcome travels in ``content`` alone.
    failed: bool = False


AgentMessage = Annotated[
    UserMessage | AssistantMessage | ToolResultMessage,
    Discriminator("role"),
]


def parse_plain_history(
    history: list[dict[str, Any]] | None,
) -> list[AgentMessage] | None:
    """Convert a plain user/assistant history (no tool-call structure) to typed messages.

    Shared by hosts (Cloud, Playground) whose stored/replayed history is only
    ever ``{"role": "user"|"assistant", "content": ...}`` pairs — no tool_calls,
    no tool results. Raises on any other role rather than silently dropping it.
    """
    if not history:
        return None
    messages: list[AgentMessage] = []
    for turn in history:
        role = turn["role"]
        if role == "user":
            messages.append(UserMessage(content=turn["content"]))
        elif role == "assistant":
            messages.append(AssistantMessage(content=turn["content"]))
        else:
            raise ValueError(f"Unexpected role in plain history: {role!r}")
    return messages
