"""Tests for dbt_charts.ai.agent."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from dbt_charts.ai.agent import AgentProfile, run_agent
from dbt_charts.ai.context import DbtChartsAIContext
from dbt_charts.ai.events import AgentError, ContentDelta, ToolCallEvent
from dbt_charts.ai.llm import AITurnFailure, LLMClientError
from dbt_charts.ai.messages import AgentMessage


def test_run_agent_accepts_content_array_prompt() -> None:
    """Passing a list as prompt (vision content array) must not raise."""

    content_array = [
        {"type": "text", "text": "What is in this image?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
    ]
    client = MagicMock()
    client.stream_with_tools.return_value = iter([ContentDelta(delta="It's a chart.")])

    context = MagicMock()
    context.project_session.adapter_registry = MagicMock()

    # Build a minimal profile that doesn't call real schema or file reads
    profile = AgentProfile(
        name="test",
        tools=[],
        build_system_prompt=lambda _ctx: "You are a test assistant.",
    )

    events = list(
        run_agent(content_array, client=client, context=context, profile=profile)
    )
    # Should have yielded ContentDelta and AgentDone — no exception
    assert any(isinstance(e, ContentDelta) for e in events)


def test_run_agent_threads_extra_handlers_to_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host (Cloud) passes extra_handlers through to dispatch_tool_call, so
    host-only tools (with no meaning to dbt_charts.core) still execute."""
    captured: dict[str, object] = {}

    def fake_dispatch(
        name,
        args,
        *,
        context,
        extra_handlers=None,
        tool_overrides=None,
    ):
        captured["extra_handlers"] = extra_handlers
        return {"ok": True}

    monkeypatch.setattr("dbt_charts.ai.agent.dispatch_tool_call", fake_dispatch)

    tool_call = ToolCallEvent(id="1", name="host_only_tool", arguments={})
    client = MagicMock()
    client.stream_with_tools.side_effect = [
        iter([tool_call]),
        iter([ContentDelta(delta="done")]),
    ]
    context = MagicMock()
    context.project_session.adapter_registry = MagicMock()
    profile = AgentProfile(
        name="test", tools=[], build_system_prompt=lambda _ctx: "prompt"
    )

    def handle_host_only(
        args: dict[str, Any], ctx: DbtChartsAIContext
    ) -> dict[str, Any]:
        return {"ok": True}

    extra_handlers = {"host_only_tool": handle_host_only}

    list(
        run_agent(
            "hi",
            client=client,
            context=context,
            profile=profile,
            extra_handlers=extra_handlers,
        )
    )

    assert captured["extra_handlers"] is extra_handlers


def test_run_agent_threads_profile_tool_overrides_to_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A profile's tool_overrides (e.g. Cloud's governed move_file/delete_file)
    is threaded through to dispatch_tool_call."""
    captured: dict[str, object] = {}

    def fake_dispatch(
        name,
        args,
        *,
        context,
        extra_handlers=None,
        tool_overrides=None,
    ):
        captured["tool_overrides"] = tool_overrides
        return {"ok": True}

    monkeypatch.setattr("dbt_charts.ai.agent.dispatch_tool_call", fake_dispatch)

    tool_call = ToolCallEvent(id="1", name="move_file", arguments={})
    client = MagicMock()
    client.stream_with_tools.side_effect = [
        iter([tool_call]),
        iter([ContentDelta(delta="done")]),
    ]
    context = MagicMock()
    context.project_session.adapter_registry = MagicMock()

    def governed_move_file(
        args: dict[str, Any], ctx: DbtChartsAIContext
    ) -> dict[str, Any]:
        return {"success": True}

    overrides = {"move_file": governed_move_file}
    profile = AgentProfile(
        name="test",
        tools=[],
        build_system_prompt=lambda _ctx: "prompt",
        tool_overrides=overrides,
    )

    list(run_agent("hi", client=client, context=context, profile=profile))

    assert captured["tool_overrides"] is overrides


def _run_agent_capture_conversation(
    tool_result: dict[str, Any],
) -> list[AgentMessage]:
    """Run one tool round-trip and return the conversation the model saw."""
    seen: list[list[AgentMessage]] = []

    def _stream(messages: Any, **kwargs: Any) -> Any:
        seen.append(list(messages))
        if len(seen) == 1:
            return iter([ToolCallEvent(id="1", name="render_board", arguments={})])
        return iter([ContentDelta(delta="done")])

    client = MagicMock()
    client.stream_with_tools.side_effect = _stream
    context = MagicMock()
    context.project_session.adapter_registry = MagicMock()
    profile = AgentProfile(
        name="test",
        tools=[],
        build_system_prompt=lambda _ctx: "prompt",
        tool_overrides={"render_board": lambda args, ctx: tool_result},
    )
    list(run_agent("hi", client=client, context=context, profile=profile))
    return seen[1]


def test_svg_tool_result_is_compacted_in_model_conversation() -> None:
    """Raw SVG never reaches the model: the conversation copy replaces the
    display artifact with an omission marker while success/warnings survive.
    Regression for the 2026-07-22 incident where a ~100KB SVG tool result was
    echoed back into chat as assistant prose."""
    svg = "<svg xmlns='http://www.w3.org/2000/svg'>" + "x" * 500 + "</svg>"
    conversation = _run_agent_capture_conversation(
        {"success": True, "data": svg, "warnings": [{"code": "W1"}]}
    )
    tool_msg = next(m for m in conversation if m.role == "tool")
    assert "<svg" not in tool_msg.content
    assert '"success": true' in tool_msg.content
    assert "W1" in tool_msg.content
    assert str(len(svg)) in tool_msg.content  # marker states the omitted size


def test_svg_under_a_non_data_key_is_also_elided_from_model() -> None:
    """A host override (e.g. Cloud's render_board merge) may carry pixel
    SVG under a key other than ``data`` (``data`` stays the agent's requested
    semantic payload). ``_elide_svg`` must elide SVG wherever it
    appears, not just under ``data`` — a second, uninspected key is exactly
    how a ~100KB blob leaks into model context."""
    svg = "<svg xmlns='http://www.w3.org/2000/svg'>" + "x" * 500 + "</svg>"
    conversation = _run_agent_capture_conversation(
        {
            "success": True,
            "data": "title: Preview\nqueries: {}\n",
            "rendered_svg": svg,
        }
    )
    tool_msg = next(m for m in conversation if m.role == "tool")
    assert "<svg" not in tool_msg.content
    assert "title: Preview" in tool_msg.content
    assert str(len(svg)) in tool_msg.content


def test_non_svg_tool_result_reaches_model_unchanged() -> None:
    """Data-bearing results (yaml/terminal text) pass through untouched."""
    conversation = _run_agent_capture_conversation(
        {"success": True, "data": "title: Preview\nqueries: {}\n"}
    )
    tool_msg = next(m for m in conversation if m.role == "tool")
    assert "title: Preview" in tool_msg.content


def test_query_result_reaches_the_model_as_csv_within_the_byte_budget() -> None:
    from dbt_charts.agent_api.query_csv import MODEL_RESULT_BYTE_BUDGET, with_csv

    rows = [{"id": i, "body": '界😀"\\' * 400} for i in range(1000)]
    payload = with_csv(
        {
            "success": True,
            "columns": ["id", "body"],
            "data": rows,
            "row_count": 1000,
            "truncated": False,
        },
        hint="Narrow it.",
    )
    conversation = _run_agent_capture_conversation(payload)
    tool_msg = next(m for m in conversation if m.role == "tool")
    assert len(tool_msg.content.encode("utf-8")) <= MODEL_RESULT_BYTE_BUDGET
    assert '"data"' not in tool_msg.content
    assert '"csv"' in tool_msg.content


def test_identical_consecutive_calls_error_before_third_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool called with identical arguments N consecutive times yields AgentError
    before dispatching the Nth call. Regression guard for the 25-repeat drift bug."""
    from dbt_charts.ai.events import AgentError

    dispatch_count = 0

    def fake_dispatch(name, args, *, context, extra_handlers=None, tool_overrides=None):
        nonlocal dispatch_count
        dispatch_count += 1
        return {"ok": True}

    monkeypatch.setattr("dbt_charts.ai.agent.dispatch_tool_call", fake_dispatch)

    repeated_call = ToolCallEvent(
        id="1", name="render_board", arguments={"path": "charts/rev.yaml"}
    )
    client = MagicMock()
    # Each stream call returns the same tool call forever
    client.stream_with_tools.side_effect = [iter([repeated_call]) for _ in range(10)]

    context = MagicMock()
    context.project_session.adapter_registry = MagicMock()
    profile = AgentProfile(
        name="test", tools=[], build_system_prompt=lambda _ctx: "prompt"
    )

    events = list(run_agent("hi", client=client, context=context, profile=profile))

    error_events = [e for e in events if isinstance(e, AgentError)]
    assert len(error_events) == 1
    assert "render_board" in error_events[0].message
    assert "3" in error_events[0].message
    assert "identical arguments" in error_events[0].message
    # Two dispatches completed (calls 1 and 2); the 3rd was blocked
    assert dispatch_count == 2


def test_varied_arguments_repeat_runs_to_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same tool called with different arguments each time is legitimate progress
    and must NOT be blocked by the identical-call guard."""
    from dbt_charts.ai.events import AgentDone

    dispatch_count = 0

    def fake_dispatch(name, args, *, context, extra_handlers=None, tool_overrides=None):
        nonlocal dispatch_count
        dispatch_count += 1
        return {"ok": True}

    monkeypatch.setattr("dbt_charts.ai.agent.dispatch_tool_call", fake_dispatch)

    client = MagicMock()
    client.stream_with_tools.side_effect = [
        iter(
            [ToolCallEvent(id="1", name="render_board", arguments={"path": "a.yaml"})]
        ),
        iter(
            [ToolCallEvent(id="2", name="render_board", arguments={"path": "b.yaml"})]
        ),
        iter(
            [ToolCallEvent(id="3", name="render_board", arguments={"path": "c.yaml"})]
        ),
        iter([ContentDelta(delta="done")]),
    ]

    context = MagicMock()
    context.project_session.adapter_registry = MagicMock()
    profile = AgentProfile(
        name="test", tools=[], build_system_prompt=lambda _ctx: "prompt"
    )

    events = list(run_agent("hi", client=client, context=context, profile=profile))

    done_events = [e for e in events if isinstance(e, AgentDone)]
    assert len(done_events) == 1
    assert dispatch_count == 3


def test_interleaved_different_tool_resets_identical_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A different tool call between identical repeats resets the counter, so the
    guard only fires on consecutive repeats, not on any repeat at all."""
    from dbt_charts.ai.events import AgentDone, AgentError

    def fake_dispatch(name, args, *, context, extra_handlers=None, tool_overrides=None):
        return {"ok": True}

    monkeypatch.setattr("dbt_charts.ai.agent.dispatch_tool_call", fake_dispatch)

    same = ToolCallEvent(id="1", name="render_board", arguments={"path": "rev.yaml"})
    diff = ToolCallEvent(id="2", name="describe_board", arguments={"path": "rev.yaml"})
    client = MagicMock()
    # same, same, different, same, same — the 'same' counter never reaches 3 consecutively
    client.stream_with_tools.side_effect = [
        iter([same]),
        iter([same]),
        iter([diff]),
        iter([same]),
        iter([same]),
        iter([ContentDelta(delta="done")]),
    ]

    context = MagicMock()
    context.project_session.adapter_registry = MagicMock()
    profile = AgentProfile(
        name="test", tools=[], build_system_prompt=lambda _ctx: "prompt"
    )

    events = list(run_agent("hi", client=client, context=context, profile=profile))

    error_events = [e for e in events if isinstance(e, AgentError)]
    done_events = [e for e in events if isinstance(e, AgentDone)]
    assert not error_events
    assert len(done_events) == 1


def test_svg_tool_result_event_keeps_full_payload() -> None:
    """The host-facing ToolResultEvent keeps the raw SVG (Cloud snapshots it)."""
    from dbt_charts.ai.events import ToolResultEvent

    svg = "<svg>full</svg>"

    def _stream(messages: Any, **kwargs: Any) -> Any:
        if not any(m.role == "tool" for m in messages):
            return iter([ToolCallEvent(id="1", name="render_board", arguments={})])
        return iter([ContentDelta(delta="done")])

    client = MagicMock()
    client.stream_with_tools.side_effect = _stream
    context = MagicMock()
    context.project_session.adapter_registry = MagicMock()
    profile = AgentProfile(
        name="test",
        tools=[],
        build_system_prompt=lambda _ctx: "prompt",
        tool_overrides={
            "render_board": lambda args, ctx: {"success": True, "data": svg}
        },
    )
    events = list(run_agent("hi", client=client, context=context, profile=profile))
    result_event = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result_event.result["data"] == svg


def test_a_truncated_turn_errors_instead_of_reporting_success() -> None:
    """The user-visible half of the silent-truncation bug, end to end.

    Driven through a real OpenAIAdapter over a raw stream that is cut mid-tool-
    call: the prose deltas arrive, then `response.incomplete`, and the
    `response.output_item.done` that would have carried the tool call never
    fires. Left unread, that reaches run_agent as an ordinary no-tool-call turn
    and reports AgentDone — a successful-looking end of turn with half a
    sentence and no board.
    """
    from types import SimpleNamespace

    from dbt_charts.ai.events import AgentDone, AgentError
    from dbt_charts.ai.llm import OpenAIAdapter
    from dbt_charts.ai.openai_gateway import OpenAIGateway

    truncated = [
        SimpleNamespace(type="response.created", response=SimpleNamespace(id="r1")),
        SimpleNamespace(
            type="response.output_text.delta", delta="Sure — building that board"
        ),
        SimpleNamespace(
            type="response.incomplete",
            response=SimpleNamespace(
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                usage=None,
            ),
        ),
    ]

    def _create(**kwargs: Any) -> Iterator[Any]:
        return iter(truncated)

    gateway = OpenAIGateway(api_key="fake")
    gateway._client = SimpleNamespace(responses=SimpleNamespace(create=_create))
    client = OpenAIAdapter(gateway, model="gpt-test")

    context = MagicMock()
    context.project_session.adapter_registry = MagicMock()
    profile = AgentProfile(
        name="test", tools=[], build_system_prompt=lambda _ctx: "prompt"
    )

    events = list(run_agent("hi", client=client, context=context, profile=profile))

    assert not any(isinstance(e, AgentDone) for e in events)
    errors = [e for e in events if isinstance(e, AgentError)]
    assert len(errors) == 1
    details = errors[0].details
    assert details is not None
    assert "max_output_tokens" in details


class TestAgentErrorCarriesATypedReason:
    """Every way the agent loop gives up names itself.

    Two of the three have no exception to classify — the loop-detection and
    step-limit terminals `return` rather than raise — which is exactly why an
    implementation shaped only around `classify(exc)` misses them, and why they
    are the two most actionable reasons on the list.
    """

    def _profile(self) -> AgentProfile:
        return AgentProfile(
            name="test",
            tools=[],
            build_system_prompt=lambda _ctx: "You are a test assistant.",
        )

    def _context(self) -> Any:
        context = MagicMock()
        context.project_session.adapter_registry = MagicMock()
        return context

    def test_a_provider_fault_carries_its_classified_reason(self) -> None:
        cause = openai.RateLimitError(
            "slow down",
            response=httpx.Response(
                429, request=httpx.Request("POST", "http://provider.test")
            ),
            body={"code": "insufficient_quota"},
        )
        wrapped = LLMClientError(str(cause))
        wrapped.__cause__ = cause

        client = MagicMock()
        # An exception as side_effect raises on call, which is where the
        # provider fault surfaces: run_agent's `for event in
        # client.stream_with_tools(...)` is inside the try that catches it.
        client.stream_with_tools.side_effect = wrapped

        events = list(
            run_agent(
                "hi",
                client=client,
                context=self._context(),
                profile=self._profile(),
            )
        )

        errors = [e for e in events if isinstance(e, AgentError)]
        assert len(errors) == 1
        assert errors[0].reason is AITurnFailure.USAGE_LIMIT_EXCEEDED

    def test_the_loop_detector_reports_loop_detected(self) -> None:
        """No exception is raised here at all — the loop yields and returns."""

        def _same_call_every_time(**_kwargs: Any) -> Iterator[Any]:
            yield ToolCallEvent(id="1", name="search_dashboards", arguments={"q": "x"})

        client = MagicMock()
        client.stream_with_tools.side_effect = _same_call_every_time

        events = list(
            run_agent(
                "hi",
                client=client,
                context=self._context(),
                profile=self._profile(),
                max_identical_calls=2,
                max_iterations=25,
            )
        )

        errors = [e for e in events if isinstance(e, AgentError)]
        assert errors, "the loop detector must terminate the run"
        assert errors[-1].reason is AITurnFailure.LOOP_DETECTED

    def test_exhausting_the_step_budget_reports_step_limit_exceeded(self) -> None:
        """Also exception-less: the loop falls out of its range and yields."""
        counter = {"n": 0}

        def _always_a_new_call(**_kwargs: Any) -> Iterator[Any]:
            counter["n"] += 1
            yield ToolCallEvent(
                id=str(counter["n"]),
                name="search_dashboards",
                arguments={"q": str(counter["n"])},
            )

        client = MagicMock()
        client.stream_with_tools.side_effect = _always_a_new_call

        events = list(
            run_agent(
                "hi",
                client=client,
                context=self._context(),
                profile=self._profile(),
                max_iterations=2,
            )
        )

        errors = [e for e in events if isinstance(e, AgentError)]
        assert errors, "the step limit must terminate the run"
        assert errors[-1].reason is AITurnFailure.STEP_LIMIT_EXCEEDED

    def test_reason_is_required_not_defaulted(self) -> None:
        """A fourth terminal added later must fail to construct rather than
        silently report `internal` — a default here is how a real failure mode
        goes uncounted."""
        with pytest.raises(TypeError):
            AgentError(message="boom")  # type: ignore[call-arg]


def test_tool_result_duration_reflects_only_its_own_dispatch_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three tool calls dispatched in one batch, the middle one slow. Each
    ToolResultEvent's duration_s must be independent of the calls dispatched
    before it — dispatch runs the whole batch in a ``for`` loop after the
    stream drains (see agent.py), so pairing ToolCallEvent to ToolResultEvent
    downstream would bill the third call for the second call's slowness too.
    Duration has to come from timing dispatch_tool_call directly."""

    from dbt_charts.ai.events import ToolResultEvent

    dispatch_order: list[str] = []

    class _Clock:
        """A fake monotonic clock. `agent` reads `time` for nothing but this
        measurement, so replacing it makes the assertion exact instead of a
        ratio between real sleeps — which is a flake waiting for a loaded
        CI runner, on the one test that proves durations are independent."""

        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

    clock = _Clock()

    def fake_dispatch(
        name: str,
        args: dict[str, Any],
        *,
        context: Any,
        extra_handlers: Any = None,
        tool_overrides: Any = None,
    ) -> dict[str, Any]:
        dispatch_order.append(name)
        clock.now += 0.08 if len(dispatch_order) == 2 else 0.005
        return {"success": True}

    monkeypatch.setattr("dbt_charts.ai.agent.time", clock)
    monkeypatch.setattr("dbt_charts.ai.agent.dispatch_tool_call", fake_dispatch)

    tool_calls = [
        ToolCallEvent(id="a", name="execute_query", arguments={}),
        ToolCallEvent(id="b", name="execute_query", arguments={}),
        ToolCallEvent(id="c", name="execute_query", arguments={}),
    ]
    client = MagicMock()
    client.stream_with_tools.side_effect = [
        iter(tool_calls),
        iter([ContentDelta(delta="done")]),
    ]
    context = MagicMock()
    context.project_session.adapter_registry = MagicMock()
    profile = AgentProfile(
        name="test", tools=[], build_system_prompt=lambda _ctx: "prompt"
    )

    events = list(run_agent("hi", client=client, context=context, profile=profile))
    results = [e for e in events if isinstance(e, ToolResultEvent)]

    assert [r.id for r in results] == ["a", "b", "c"]
    first, second, third = (r.duration_s for r in results)
    # Each call reports its own elapsed time and nothing else. Pairing
    # ToolCallEvent to ToolResultEvent in the host would give the third call
    # 0.09 — the whole batch — because every call event is emitted before any
    # dispatch begins. That is the misattribution this measurement exists to
    # avoid, and these are the exact numbers that catch it.
    assert first == pytest.approx(0.005)
    assert second == pytest.approx(0.08)
    assert third == pytest.approx(0.005)
