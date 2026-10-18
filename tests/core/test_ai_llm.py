"""Tests for provider selection and message/tool-call translation in
``dbt_charts.ai.llm``.

Wire-transport concerns (SDK construction, error translation, usage/early-stop
accounting) moved to the ``dbt_charts.ai.openai_gateway`` module and are tested
alongside it; the wire-contract-shaped request-kwargs tests live in the
sibling ``ai`` test package's ``test_llm`` module. This file covers what's
left in ``dbt_charts.ai.llm``: provider selection and dbt-charts <-> OpenAI
vocabulary translation.
"""

from typing import Any

import pytest


def test_create_client_uses_openai_by_default() -> None:
    """create_client returns the OpenAI adapter for any model name."""
    from dbt_charts.ai.llm import OpenAIAdapter, create_client

    client = create_client(model="gpt-4.1-mini")

    assert isinstance(client, OpenAIAdapter)


def test_create_client_uses_openai_when_only_openai_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no model arg and OPENAI_API_KEY set, pick OpenAI."""
    from dbt_charts.ai.llm import OpenAIAdapter, create_client

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    client = create_client()

    assert isinstance(client, OpenAIAdapter)


def test_create_client_errors_clearly_when_no_keys_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No model and no keys should fail fast with a useful message."""
    from dbt_charts.ai.llm import create_client

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        create_client()

    message = str(exc_info.value)
    assert "OPENAI_API_KEY" in message


def test_create_client_rejects_unsupported_provider() -> None:
    """A provider other than openai fails like any other bad input."""
    from dbt_charts.ai.llm import create_client

    with pytest.raises(ValueError, match="Unsupported provider"):
        create_client(provider="not-a-real-provider")


def test_openai_default_model_is_current_gpt5() -> None:
    """Regression: keep the OpenAI default on the current GPT-5 line.

    The stale `gpt-4o` default shipped to production Cloud chat; this pins the
    upgrade so a revert can't silently drop the whole product back to 4o.
    """
    from dbt_charts.ai.llm import DEFAULT_OPENAI_MODEL

    assert DEFAULT_OPENAI_MODEL == "gpt-5.6-terra"
    assert not DEFAULT_OPENAI_MODEL.startswith("gpt-4")


class _ScriptedGateway:
    """A fake ``ResponsesGateway`` whose ``stream`` yields the next scripted
    turn's events and records the ``input`` field each call was given."""

    total_input_tokens = 0
    total_output_tokens = 0
    calls = 0

    def __init__(self, turns: list[list[Any]]) -> None:
        self._turns = turns
        self._call_index = 0
        self.captured_inputs: list[list[dict[str, Any]]] = []

    def create(self, request: Any) -> Any:
        raise NotImplementedError

    def stream(self, request: Any):  # noqa: ANN201 - test double, shape pinned by use
        self.calls += 1
        self.captured_inputs.append(request.input)
        turn_index = min(self._call_index, len(self._turns) - 1)
        self._call_index += 1
        yield from self._turns[turn_index]


def test_openai_adapter_second_call_replays_tool_history_without_assistant_echo() -> (
    None
):
    """Regression (2026-07-23 leaked-tool-envelope incident): after a tool-calling
    turn, the next call's input must carry the tool result and must never echo the
    assistant's preamble text as a bare disconnected message — that echo is what
    degrades the model into writing the tool call as prose instead of using it.

    Must fail against current main: the incremental branch
    (``OpenAIAdapter._messages_to_input``) has no case for an assistant message
    carrying ``tool_calls``, so it falls through to the generic role-based branch
    and emits ``{"role": "assistant", "content": <preamble>}`` — the exact echo
    this test forbids.
    """
    from dbt_charts.ai.llm import OpenAIAdapter
    from dbt_charts.ai.messages import (
        AssistantMessage,
        ToolCall,
        ToolResultMessage,
        UserMessage,
    )
    from dbt_charts.ai.openai_gateway import FunctionCall, TextDelta

    first_turn = [
        FunctionCall(call_id="call_1", name="execute_sql", arguments={}),
    ]
    second_turn = [TextDelta(text="Done.")]

    gateway = _ScriptedGateway([first_turn, second_turn])
    client = OpenAIAdapter(gateway, model="gpt-5.6")

    messages = [UserMessage(content="render a chart")]
    list(client.stream_with_tools(messages=messages, system_prompt="SYSTEM", tools=[]))

    # Simulate what run_agent appends after the tool call: the assistant's
    # preamble + tool_calls, then the tool result.
    messages.append(
        AssistantMessage(
            content="Let me render that for you.",
            tool_calls=[ToolCall(id="call_1", name="execute_sql", arguments={})],
        )
    )
    messages.append(
        ToolResultMessage(tool_call_id="call_1", name="execute_sql", content="{}")
    )

    list(client.stream_with_tools(messages=messages, system_prompt="SYSTEM", tools=[]))

    second_call_input = gateway.captured_inputs[1]
    assert not any(
        item.get("role") == "assistant" and item.get("content")
        for item in second_call_input
    ), f"assistant preamble echoed into input: {second_call_input}"
    assert any(
        item.get("type") == "function_call_output" for item in second_call_input
    ), f"tool result missing from second call input: {second_call_input}"


def test_openai_adapter_converts_multimodal_content_to_responses_input() -> None:
    """Provider-neutral image messages must use Responses API block names."""
    from dbt_charts.ai.llm import OpenAIAdapter
    from dbt_charts.ai.messages import AssistantMessage, UserMessage

    client = OpenAIAdapter(_ScriptedGateway([]), model="gpt-5.6")
    image_uri = "data:image/png;base64,aW1hZ2U="

    items = client._messages_to_input(
        [
            UserMessage(
                content=[
                    {"type": "text", "text": "What is in this image?"},
                    {"type": "image_url", "image_url": {"url": image_uri}},
                ],
            ),
            AssistantMessage(content="It is a chart."),
        ],
    )

    assert items == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "What is in this image?"},
                {"type": "input_image", "image_url": image_uri},
            ],
        },
        {"role": "assistant", "content": "It is a chart."},
    ]


def test_openai_adapter_converts_document_block_to_input_file() -> None:
    """Provider-neutral document blocks become Responses `input_file` items.

    A `file_data` data-URI needs an accompanying filename; the subtype drives
    the synthesized extension so the model sees a plausibly-named PDF.
    """
    from dbt_charts.ai.llm import OpenAIAdapter
    from dbt_charts.ai.messages import UserMessage

    client = OpenAIAdapter(_ScriptedGateway([]), model="gpt-5.6")
    pdf_uri = "data:application/pdf;base64,cGRm"

    items = client._messages_to_input(
        [
            UserMessage(
                content=[
                    {"type": "text", "text": "Summarize this."},
                    {"type": "document", "url": pdf_uri},
                ],
            )
        ],
    )

    assert items == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Summarize this."},
                {
                    "type": "input_file",
                    "filename": "attachment.pdf",
                    "file_data": pdf_uri,
                },
            ],
        }
    ]


def test_parse_data_uri_rejects_malformed_uri() -> None:
    """A non-base64 / non-data URI must fail fast, not silently split wrong."""
    from dbt_charts.ai.llm import LLMClientError, _parse_data_uri

    with pytest.raises(LLMClientError, match="not a base64 data URI"):
        _parse_data_uri("https://example.com/foo.png")


def test_openai_adapter_rejects_unsupported_content_block() -> None:
    """Unknown internal block shapes must not reach the provider API."""
    from dbt_charts.ai.llm import LLMClientError, OpenAIAdapter
    from dbt_charts.ai.messages import UserMessage

    client = OpenAIAdapter(_ScriptedGateway([]), model="gpt-5.6")

    with pytest.raises(LLMClientError, match="Unsupported OpenAI message"):
        client._messages_to_input(
            [UserMessage(content=[{"type": "input_file"}])],
        )


def test_reasoning_summary_parts_get_distinct_block_keys() -> None:
    """Each reasoning block carries its own key, so consumers can collapse per block.

    Deltas restate the whole summary so far, so a consumer that appends would
    show one paragraph many times. Adjacency cannot separate them: two summary
    parts arrive back to back with no tool call between, so only the key
    distinguishes "same block, longer text" from "next block".
    """
    from dbt_charts.ai.events import ThinkingStatus
    from dbt_charts.ai.llm import OpenAIAdapter
    from dbt_charts.ai.messages import UserMessage
    from dbt_charts.ai.openai_gateway import ReasoningSummaryDelta, ReasoningSummaryDone

    events = [
        ReasoningSummaryDelta(block="rs_1:0", text="Checking the schema."),
        ReasoningSummaryDone(block="rs_1:0", text="Checking the schema."),
        ReasoningSummaryDelta(block="rs_1:1", text="Now picking a chart type."),
    ]

    client = OpenAIAdapter(_ScriptedGateway([events]), model="gpt-5.6")

    out = list(
        client.stream_with_tools(
            messages=[UserMessage(content="Build me a chart.")],
            system_prompt="SYSTEM",
            tools=[],
        )
    )

    assert out == [
        ThinkingStatus(status="Checking the schema.", block="rs_1:0"),
        ThinkingStatus(status="Checking the schema.", block="rs_1:0"),
        ThinkingStatus(status="Now picking a chart type.", block="rs_1:1"),
    ]
