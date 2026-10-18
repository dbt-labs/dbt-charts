"""Tests for the typed agent-conversation message union."""

from __future__ import annotations

import pydantic
import pytest

from dbt_charts.ai.messages import (
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    parse_plain_history,
)


def test_user_message_defaults_role() -> None:
    assert UserMessage(content="hi").role == "user"


def test_assistant_message_defaults_content_and_tool_calls() -> None:
    msg = AssistantMessage()
    assert msg.role == "assistant"
    assert msg.content == ""
    assert msg.tool_calls == []


def test_tool_result_message_requires_fields() -> None:
    msg = ToolResultMessage(tool_call_id="call_1", name="execute_sql", content="[]")
    assert msg.role == "tool"


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(pydantic.ValidationError):
        UserMessage(content="hi", bogus="nope")  # type: ignore[call-arg]


def test_assistant_message_content_must_be_a_string() -> None:
    """Structural guarantee: multimodal content is a user-only concept."""
    with pytest.raises(pydantic.ValidationError):
        AssistantMessage(content=[{"type": "text", "text": "unexpected"}])  # type: ignore[arg-type]


def test_tool_call_arguments_round_trip() -> None:
    tc = ToolCall(id="call_1", name="execute_sql", arguments={"sql": "SELECT 1"})
    assert tc.arguments == {"sql": "SELECT 1"}


class TestParsePlainHistory:
    def test_none_returns_none(self) -> None:
        assert parse_plain_history(None) is None

    def test_empty_list_returns_none(self) -> None:
        assert parse_plain_history([]) is None

    def test_user_and_assistant_turns_convert(self) -> None:
        history = [
            {"role": "user", "content": "show revenue"},
            {"role": "assistant", "content": "Here is revenue."},
        ]
        assert parse_plain_history(history) == [
            UserMessage(content="show revenue"),
            AssistantMessage(content="Here is revenue."),
        ]

    def test_unknown_role_raises(self) -> None:
        with pytest.raises(ValueError, match="Unexpected role"):
            parse_plain_history([{"role": "tool", "content": "x"}])
