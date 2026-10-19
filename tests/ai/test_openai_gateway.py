"""Behavior tests for the OpenAI wire transport: typed request/response
vocabulary, error translation, and usage accounting (including early-stop).

The gateway is the seam a host decorates to watch provider requests
(`ResponsesGateway`) — these tests pin that it does the SDK-facing job
completely on its own, emitting its own typed models rather than raw SDK
events, with no dbt-charts vocabulary involved.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest import mock

import httpx
import openai
import pytest
from pydantic import BaseModel

from dbt_charts.ai.openai_gateway import (
    CONNECT_TIMEOUT_SECONDS,
    ApplyPatchCall,
    FunctionCall,
    GatewayError,
    OpenAIGateway,
    ReasoningItems,
    ReasoningSummaryDelta,
    ReasoningSummaryDone,
    ResponsesRequest,
    TextDelta,
    _llm_timeout,
    early_stop_reason,
)


class _SentinelTimeout(httpx.Timeout):
    """Distinct from plain ``httpx.Timeout`` so a test can tell whether the
    gateway built its timeout through ``openai.Timeout`` (this class, once
    patched in as that attribute) or reverted to constructing
    ``httpx.Timeout`` directly."""


class _StubResponses:
    """Captures the kwargs passed to responses.create; returns *result*."""

    def __init__(self, result: Any = None) -> None:
        self.last_kwargs: dict[str, Any] = {}
        self.result = result if result is not None else []

    def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        return self.result


class _StubClient:
    def __init__(self, result: Any = None) -> None:
        self.responses = _StubResponses(result)


def _gateway_with_stub(
    result: Any = None,
) -> tuple[OpenAIGateway, _StubResponses]:
    gateway = OpenAIGateway(api_key="fake")
    stub = _StubClient(result)
    gateway._client = stub
    return gateway, stub.responses


def _request(**kwargs: Any) -> ResponsesRequest:
    kwargs.setdefault("model", "gpt-test")
    return ResponsesRequest(**kwargs)


def _as_dict(obj: Any) -> Any:
    """Recursively convert a `SimpleNamespace` tree into nested dicts — lets
    one canonical typed fixture double as its own dict-shaped (gpt-4.1-mini,
    gpt-4o) twin without hand-duplicating every nested field."""
    if isinstance(obj, SimpleNamespace):
        return {k: _as_dict(v) for k, v in vars(obj).items()}
    return obj


#: Every stream-event test below runs once against a typed SimpleNamespace
#: fixture and once against its dict-shaped twin — some models (gpt-4.1-mini,
#: gpt-4o) hand the SDK back raw dicts instead of typed objects.
_WIRE_SHAPES: list[Callable[[Any], Any]] = [lambda e: e, _as_dict]
_WIRE_SHAPE_IDS = ["typed", "dict"]


def test_gateway_is_model_agnostic() -> None:
    """The gateway takes no model at construction — it's a request field."""
    OpenAIGateway(api_key="fake")


def test_create_translates_api_errors_to_gateway_error() -> None:
    gateway, stub = _gateway_with_stub()

    def _raise(**kwargs: Any) -> Any:
        raise httpx.ReadError("socket lost")

    stub.create = _raise
    with pytest.raises(GatewayError):
        gateway.create(_request())


def test_create_reraises_unclassified_errors() -> None:
    gateway, stub = _gateway_with_stub()

    def _raise(**kwargs: Any) -> Any:
        raise ValueError("not an API error")

    stub.create = _raise
    with pytest.raises(ValueError, match="not an API error"):
        gateway.create(_request())


def test_create_returns_completed_response_with_output_text() -> None:
    """CompletedResponse is shaped by what callers actually read — just the
    text. That's every real caller (titles.py, suggestions.py, generate_sql,
    chart-lab compare) today."""
    gateway, _stub = _gateway_with_stub(
        SimpleNamespace(output_text="hello", usage=None)
    )

    response = gateway.create(_request())

    assert response.output_text == "hello"


def test_create_counts_a_call_and_accumulates_usage() -> None:
    gateway, _stub = _gateway_with_stub(
        SimpleNamespace(
            output_text="",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
    )

    gateway.create(_request())

    assert gateway.calls == 1
    assert gateway.total_input_tokens == 10
    assert gateway.total_output_tokens == 5


def test_create_accumulates_usage_from_dict_shaped_payload() -> None:
    gateway, _stub = _gateway_with_stub(
        SimpleNamespace(
            output_text="", usage={"input_tokens": 100, "output_tokens": 20}
        )
    )

    gateway.create(_request())

    assert (gateway.total_input_tokens, gateway.total_output_tokens) == (100, 20)


def test_create_accumulates_usage_across_multiple_calls() -> None:
    """The gateway's counters are cumulative across its lifetime — a `+=` to
    `=` mutation (or `self.calls = 1` instead of `+= 1`) must fail here; every
    other usage test in this file drives only one call."""
    gateway, _stub = _gateway_with_stub(
        SimpleNamespace(
            output_text="",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
    )

    gateway.create(_request())
    gateway.create(_request())

    assert gateway.calls == 2
    assert gateway.total_input_tokens == 20
    assert gateway.total_output_tokens == 10


def test_responses_request_forwards_extra_fields_to_the_sdk() -> None:
    """The 80/20 envelope isn't the full Responses API — `text=` (structured
    output, used by generate_sql and chart-lab compare) must still reach the
    wire untouched."""
    gateway, stub = _gateway_with_stub(SimpleNamespace(output_text="{}", usage=None))

    gateway.create(_request(text={"format": {"type": "json_schema"}}))

    assert stub.last_kwargs["text"] == {"format": {"type": "json_schema"}}


@pytest.mark.parametrize("shape", _WIRE_SHAPES, ids=_WIRE_SHAPE_IDS)
def test_stream_yields_text_delta_events(shape: Callable[[Any], Any]) -> None:
    events = [shape(SimpleNamespace(type="response.output_text.delta", delta="hi"))]
    gateway, stub = _gateway_with_stub()
    stub.create = lambda **kwargs: iter(events)

    out = list(gateway.stream(_request(stream=True)))

    assert out == [TextDelta(text="hi")]
    assert gateway.calls == 1


@pytest.mark.parametrize("shape", _WIRE_SHAPES, ids=_WIRE_SHAPE_IDS)
def test_stream_yields_function_call_with_parsed_arguments(
    shape: Callable[[Any], Any],
) -> None:
    """`arguments` is a JSON string on the wire — the gateway parses it once,
    so no consumer duck-types raw-string-vs-dict."""
    events = [
        shape(
            SimpleNamespace(
                type="response.output_item.done",
                item=SimpleNamespace(
                    type="function_call",
                    call_id="call_1",
                    name="execute_sql",
                    arguments='{"sql": "SELECT 1"}',
                ),
            )
        )
    ]
    gateway, stub = _gateway_with_stub()
    stub.create = lambda **kwargs: iter(events)

    out = list(gateway.stream(_request(stream=True)))

    assert out == [
        FunctionCall(
            call_id="call_1", name="execute_sql", arguments={"sql": "SELECT 1"}
        )
    ]


@pytest.mark.parametrize("shape", _WIRE_SHAPES, ids=_WIRE_SHAPE_IDS)
def test_stream_yields_apply_patch_call_with_its_operation(
    shape: Callable[[Any], Any],
) -> None:
    operation = {"type": "update_file", "path": "a.yml", "diff": "@@\n-a\n+b\n"}
    events = [
        shape(
            SimpleNamespace(
                type="response.output_item.done",
                item=SimpleNamespace(
                    type="apply_patch_call", call_id="call_9", operation=operation
                ),
            )
        )
    ]
    gateway, stub = _gateway_with_stub()
    stub.create = lambda **kwargs: iter(events)

    out = list(gateway.stream(_request(stream=True)))

    assert out == [ApplyPatchCall(call_id="call_9", operation=operation)]


def test_stream_apply_patch_operation_from_a_typed_sdk_object() -> None:
    class _Operation(BaseModel):
        type: str
        path: str
        diff: str | None = None

    events = [
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="apply_patch_call",
                call_id="call_9",
                operation=_Operation(type="delete_file", path="a.yml"),
            ),
        )
    ]
    gateway, stub = _gateway_with_stub()
    stub.create = lambda **kwargs: iter(events)

    assert list(gateway.stream(_request(stream=True))) == [
        ApplyPatchCall(
            call_id="call_9", operation={"type": "delete_file", "path": "a.yml"}
        )
    ]


@pytest.mark.parametrize("shape", _WIRE_SHAPES, ids=_WIRE_SHAPE_IDS)
def test_stream_ignores_output_item_done_for_non_function_call_items(
    shape: Callable[[Any], Any],
) -> None:
    events = [
        shape(
            SimpleNamespace(
                type="response.output_item.done",
                item=SimpleNamespace(type="message"),
            )
        )
    ]
    gateway, stub = _gateway_with_stub()
    stub.create = lambda **kwargs: iter(events)

    assert list(gateway.stream(_request(stream=True))) == []


def test_stream_malformed_function_call_arguments_raises_gateway_error() -> None:
    events = [
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_1",
                name="execute_sql",
                arguments="{not json",
            ),
        )
    ]
    gateway, stub = _gateway_with_stub()
    stub.create = lambda **kwargs: iter(events)

    with pytest.raises(GatewayError, match="function-call"):
        list(gateway.stream(_request(stream=True)))


def test_stream_event_failing_shim_validation_raises_gateway_error() -> None:
    """A wire payload that doesn't match this gateway's own parsing shim
    (here: `arguments` arriving as an object instead of the wire's usual JSON
    string — distinct from the malformed-JSON-string case above) must not
    escape as a bare pydantic ValidationError. Every wire-side failure is a
    GatewayError, full stop."""
    events = [
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_1",
                name="execute_sql",
                arguments={"sql": "SELECT 1"},
            ),
        )
    ]
    gateway, stub = _gateway_with_stub()
    stub.create = lambda **kwargs: iter(events)

    with pytest.raises(GatewayError):
        list(gateway.stream(_request(stream=True)))


def test_create_response_failing_shim_validation_raises_gateway_error() -> None:
    """A create() response that doesn't match `_CreateResponsePayload` (here:
    `output_text` arriving as a non-string) must surface as GatewayError, not
    an unwrapped pydantic ValidationError."""
    gateway, _stub = _gateway_with_stub(SimpleNamespace(output_text=12345, usage=None))

    with pytest.raises(GatewayError):
        gateway.create(_request())


@pytest.mark.parametrize("shape", _WIRE_SHAPES, ids=_WIRE_SHAPE_IDS)
def test_stream_yields_reasoning_summary_events_keyed_by_block(
    shape: Callable[[Any], Any],
) -> None:
    events = [
        shape(
            SimpleNamespace(
                type="response.reasoning_summary_text.delta",
                item_id="rs_1",
                summary_index=0,
                delta="Checking the schema.",
            )
        ),
        shape(
            SimpleNamespace(
                type="response.reasoning_summary_text.done",
                item_id="rs_1",
                summary_index=0,
                text="Checking the schema.",
            )
        ),
    ]
    gateway, stub = _gateway_with_stub()
    stub.create = lambda **kwargs: iter(events)

    out = list(gateway.stream(_request(stream=True)))

    assert out == [
        ReasoningSummaryDelta(block="rs_1:0", text="Checking the schema."),
        ReasoningSummaryDone(block="rs_1:0", text="Checking the schema."),
    ]


def test_stream_absorbs_response_completed_without_yielding_it() -> None:
    """No consumer needs `response.completed` itself — only the usage it
    carries — so it's gateway bookkeeping, never part of GatewayEvent."""
    completed = SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(
            usage=SimpleNamespace(input_tokens=3, output_tokens=4)
        ),
    )
    gateway, stub = _gateway_with_stub()
    stub.create = lambda **kwargs: iter([completed])

    out = list(gateway.stream(_request(stream=True)))

    assert out == []
    assert (gateway.total_input_tokens, gateway.total_output_tokens) == (3, 4)


def test_stream_accumulates_usage_from_dict_shaped_completed_event() -> None:
    """Regression: gpt-4o/gpt-4.1 hand back a dict usage payload on
    ``response.completed``, not a typed object."""
    completed: dict[str, Any] = {
        "type": "response.completed",
        "response": {"usage": {"input_tokens": 42, "output_tokens": 8}},
    }
    gateway, stub = _gateway_with_stub()

    def _create(**kwargs: Any) -> Any:
        return iter([completed])

    stub.create = _create

    list(gateway.stream(_request(stream=True)))

    assert (gateway.total_input_tokens, gateway.total_output_tokens) == (42, 8)


def test_stream_open_phase_error_translates_to_gateway_error() -> None:
    gateway, stub = _gateway_with_stub()

    def _raise(**kwargs: Any) -> Any:
        raise httpx.ConnectError("could not connect")

    stub.create = _raise
    with pytest.raises(GatewayError) as excinfo:
        list(gateway.stream(_request(stream=True)))

    # classify() reads the provider exception two levels down, through this
    # link and the adapter's: dropping either collapses every label to
    # PROVIDER_ERROR without failing a message-matching test.
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)


def test_stream_read_phase_error_translates_to_gateway_error() -> None:
    def _generator() -> Any:
        yield SimpleNamespace(type="response.created")
        raise httpx.ReadError("dropped mid-stream")

    gateway, stub = _gateway_with_stub()
    stub.create = lambda **kwargs: _generator()

    with pytest.raises(GatewayError) as excinfo:
        list(gateway.stream(_request(stream=True)))

    # The read phase is a second, separate `from exc` site: the stub returns
    # a generator without running its body, so `_open` completes and the
    # error surfaces inside `stream`'s own iteration try.
    assert isinstance(excinfo.value.__cause__, httpx.ReadError)


@pytest.mark.parametrize(
    ("event", "match", "expected_usage"),
    [
        (
            SimpleNamespace(
                type="response.incomplete",
                response=SimpleNamespace(
                    incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                    usage=SimpleNamespace(input_tokens=100, output_tokens=900),
                ),
            ),
            "max_output_tokens",
            (100, 900),
        ),
        (
            SimpleNamespace(
                type="response.failed",
                response=SimpleNamespace(
                    error=SimpleNamespace(message="upstream model overloaded"),
                    usage=SimpleNamespace(input_tokens=12, output_tokens=34),
                ),
            ),
            "upstream model overloaded",
            (12, 34),
        ),
        (
            # The bare "error" event carries no "response" field at all, so
            # there is no usage to accumulate — this pins that absence too,
            # not just the raise.
            SimpleNamespace(type="error", message="server_error while streaming"),
            "server_error while streaming",
            (0, 0),
        ),
    ],
    ids=["response.incomplete", "response.failed", "error"],
)
def test_early_stop_event_raises_after_counting_usage(
    event: Any, match: str, expected_usage: tuple[int, int]
) -> None:
    """Every EARLY_STOP_EVENTS shape counts its usage, then raises — the
    caller must never mistake a truncated turn for a finished one.

    Parametrized over all three shapes so a narrowed EARLY_STOP_EVENTS tuple,
    or a typo in one of its members, fails the raise-routing itself here —
    not only the formatting covered by the early_stop_reason-specific tests.
    """
    gateway, stub = _gateway_with_stub()
    stub.create = lambda **kwargs: iter([event])

    with pytest.raises(GatewayError, match=match):
        list(gateway.stream(_request(stream=True)))

    assert (gateway.total_input_tokens, gateway.total_output_tokens) == expected_usage


def test_early_stop_reason_reads_dict_shaped_events_too() -> None:
    """`early_stop_reason` stays public — any surface driving its own raw
    Responses stream needs the same three-shape detection."""
    event = {
        "type": "response.incomplete",
        "response": {"incomplete_details": {"reason": "content_filter"}},
    }
    assert "content_filter" in early_stop_reason(event)


def test_early_stop_reason_reports_bare_error_message() -> None:
    event = SimpleNamespace(type="error", message="server_error while streaming")
    assert early_stop_reason(event) == "server_error while streaming"


def test_early_stop_reason_reports_absent_error_message_as_absent() -> None:
    """``ResponseErrorEvent.message`` is required by the schema, but the SDK
    does not trust it on the wire and neither does this — a dict-shaped
    payload without it must not render "stopped early: None"."""
    assert early_stop_reason({"type": "error"}) == "the provider reported an error"


def test_early_stop_reason_reports_failed_response_message() -> None:
    event = SimpleNamespace(
        type="response.failed",
        response=SimpleNamespace(
            error=SimpleNamespace(message="upstream model overloaded")
        ),
    )
    assert early_stop_reason(event) == "upstream model overloaded"


def test_early_stop_reason_reports_absent_failed_message_as_absent() -> None:
    """``Response.error`` is Optional too — the `failed` twin of the bare-error case."""
    event = SimpleNamespace(
        type="response.failed", response=SimpleNamespace(error=None)
    )
    assert early_stop_reason(event) == "the response failed without an error"


def test_early_stop_reason_reports_unstated_incomplete_reason() -> None:
    """Dereferencing ``incomplete_details`` blind would raise AttributeError —
    a stack trace naming NoneType instead of a truncated turn."""
    event = SimpleNamespace(
        type="response.incomplete",
        response=SimpleNamespace(incomplete_details=None),
    )
    reason = early_stop_reason(event)
    assert reason == "the response was incomplete for an unstated reason"
    assert "NoneType" not in reason


def test_llm_timeout_defaults_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DCT_LLM_READ_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DCT_LLM_CONNECT_TIMEOUT_SECONDS", raising=False)
    timeout = _llm_timeout()
    assert timeout.read == 60.0
    assert timeout.connect == 10.0

    monkeypatch.setenv("DCT_LLM_READ_TIMEOUT_SECONDS", "5")
    assert _llm_timeout().read == 5.0


def test_gateway_constructed_with_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The OpenAI SDK client is built with an explicit non-None timeout so a
    stalled call surfaces as an error instead of hanging for the SDK default."""
    monkeypatch.delenv("DCT_LLM_READ_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DCT_LLM_CONNECT_TIMEOUT_SECONDS", raising=False)

    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(openai, "Timeout", _SentinelTimeout)
    gateway = OpenAIGateway(api_key="sk-test")
    _ = gateway.client  # trigger lazy construction

    timeout = captured["timeout"]
    assert type(timeout) is _SentinelTimeout
    assert timeout.read == 60.0
    assert timeout.connect == 10.0


class TestTransportOverrides:
    """A caller on a request path needs a hard wall-clock bound, which the
    timeout alone cannot give: it is per-attempt, so SDK retries multiply it."""

    @staticmethod
    def _constructed(**kwargs: object) -> dict[str, object]:
        seen: dict[str, object] = {}

        class FakeOpenAI:
            def __init__(self, **sdk_kwargs: object) -> None:
                seen.update(sdk_kwargs)

        gateway = OpenAIGateway(api_key="test", **kwargs)  # type: ignore[arg-type]
        with mock.patch.dict(
            "sys.modules",
            {"openai": mock.MagicMock(OpenAI=FakeOpenAI, Timeout=_SentinelTimeout)},
        ):
            _ = gateway.client
        return seen

    def test_overrides_reach_the_sdk(self) -> None:
        seen = self._constructed(read_timeout_seconds=8.0, max_retries=0)

        assert seen["max_retries"] == 0
        assert type(seen["timeout"]) is _SentinelTimeout
        assert seen["timeout"].read == 8.0
        assert seen["timeout"].connect == CONNECT_TIMEOUT_SECONDS

    def test_defaults_leave_the_sdk_alone(self) -> None:
        seen = self._constructed()

        assert "max_retries" not in seen
        assert type(seen["timeout"]) is _SentinelTimeout
        assert seen["timeout"].read == 60.0
        assert "http_client" not in seen

    def test_http_client_owns_the_timeout_no_kwarg_forwarded(self) -> None:
        """A host-owned http_client is now the sole timeout authority: the
        gateway no longer restates it as an SDK ``timeout=`` kwarg. Safe
        because construction refuses a default-shaped http_client timeout
        (see test_default_shaped_http_client_timeout_is_refused) — the one
        case the old forwarding existed to paper over."""
        fake_http_client = SimpleNamespace(timeout=httpx.Timeout(12.0, connect=3.0))
        seen = self._constructed(http_client=fake_http_client)

        assert seen["http_client"] is fake_http_client
        assert "timeout" not in seen
        assert "max_retries" not in seen

    def test_http_client_still_forwards_max_retries(self) -> None:
        """SDK retries are SDK-level, independent of the httpx client that
        carries the wire traffic — both knobs must reach the SDK together."""
        fake_http_client = SimpleNamespace(timeout=httpx.Timeout(12.0, connect=3.0))
        seen = self._constructed(http_client=fake_http_client, max_retries=0)

        assert seen["http_client"] is fake_http_client
        assert seen["max_retries"] == 0
        assert "timeout" not in seen

    def test_default_shaped_http_client_timeout_is_refused(self) -> None:
        """A plain ``httpx.Client()`` carries httpx's own default-shaped
        Timeout(5.0), which the OpenAI SDK silently swaps for its own 600s
        default at construction. The gateway must refuse it up front rather
        than silently losing the caller's wall-clock bound."""
        with (
            httpx.Client() as default_client,
            pytest.raises(ValueError, match="explicit timeout"),
        ):
            OpenAIGateway(api_key="test", http_client=default_client)


def test_stream_lifecycle_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    gateway, stub = _gateway_with_stub()
    stub.create = lambda **kwargs: iter([])

    with caplog.at_level("INFO", logger="dbt_charts.ai.openai_gateway"):
        list(gateway.stream(_request(stream=True)))

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "llm_stream_begin" in joined
    assert "llm_stream_end" in joined


class TestUsageDetailAccumulation:
    """The two subset counters (cache-read input, reasoning output) the cost
    metrics read alongside the totals."""

    def test_create_accumulates_cache_and_reasoning_token_details(self) -> None:
        gateway, _stub = _gateway_with_stub(
            SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=1200,
                    output_tokens=800,
                    input_tokens_details=SimpleNamespace(cached_tokens=900),
                    output_tokens_details=SimpleNamespace(reasoning_tokens=500),
                )
            )
        )

        gateway.create(_request())

        assert gateway.total_cached_input_tokens == 900
        assert gateway.total_reasoning_output_tokens == 500

    def test_stream_accumulates_cache_and_reasoning_token_details(self) -> None:
        completed = SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=1200,
                    output_tokens=800,
                    input_tokens_details=SimpleNamespace(cached_tokens=900),
                    output_tokens_details=SimpleNamespace(reasoning_tokens=500),
                )
            ),
        )
        gateway, stub = _gateway_with_stub()
        stub.create = lambda **kwargs: iter([completed])

        list(gateway.stream(_request(stream=True)))

        assert gateway.total_cached_input_tokens == 900
        assert gateway.total_reasoning_output_tokens == 500

    def test_a_null_valued_detail_key_accumulates_nothing(self) -> None:
        """The detail block exists but reports the count as null — the shape a
        `+= <default>` guard on the *block* sails straight past. `int += None`
        is a TypeError escaping the caller, which turns a turn that merely
        lacked a cache stat into a failed one."""
        gateway, _stub = _gateway_with_stub(
            SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=10,
                    output_tokens=20,
                    input_tokens_details=SimpleNamespace(cached_tokens=None),
                    output_tokens_details=SimpleNamespace(reasoning_tokens=None),
                )
            )
        )

        gateway.create(_request())

        assert gateway.total_cached_input_tokens == 0
        assert gateway.total_reasoning_output_tokens == 0

    def test_absent_detail_payloads_accumulate_nothing(self) -> None:
        """A usage payload without the detail blocks means "not reported", not
        "zero cached tokens". Inventing a 0 would read as a total cache miss on
        every model that omits the block."""
        gateway, _stub = _gateway_with_stub(
            SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=20))
        )

        gateway.create(_request())

        assert gateway.total_cached_input_tokens == 0
        assert gateway.total_reasoning_output_tokens == 0


class _WireReasoning(BaseModel):
    """Stands in for the SDK's typed ``ResponseReasoningItem``."""

    type: str = "reasoning"
    id: str
    encrypted_content: str | None = None
    summary: list[Any]
    status: str | None = None


_REASONING_DICT: dict[str, Any] = {
    "type": "reasoning",
    "id": "rs_1",
    "encrypted_content": "enc",
    "summary": [],
}


@pytest.mark.parametrize(
    "reasoning",
    [
        dict(_REASONING_DICT),
        _WireReasoning(id="rs_1", encrypted_content="enc", summary=[]),
    ],
    ids=["dict", "typed"],
)
def test_stream_yields_reasoning_items_from_the_completed_response(
    reasoning: Any,
) -> None:
    """Reasoning items come off ``response.completed`` output, so their order
    relative to the function calls is exactly the provider's."""
    completed = SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(
            usage=None,
            output=[
                reasoning,
                SimpleNamespace(type="function_call", call_id="c1", name="t"),
            ],
        ),
    )
    gateway, stub = _gateway_with_stub()
    stub.create = lambda **kwargs: iter([completed])

    out = list(gateway.stream(_request(stream=True)))

    assert out == [ReasoningItems(items=[_REASONING_DICT])]
