"""Wire-contract tests for OpenAIAdapter.stream_with_tools.

Pins the Responses-API request shape so a silent regression in how the adapter
threads tool results is caught here — not two weeks later in a slow, flaky e2e.
This is the legitimate parity-test carve-out: the contract lives across two
genuinely separate surfaces (adapter -> gateway), not a duplicated helper.

The adapter is tested against a fake :class:`ResponsesGateway` that plays the
gateway's post-parsing role directly (it hands back typed `GatewayEvent`s, the
same as the real one) — the SDK-facing wire parsing/error/usage/early-stop
behavior these tests used to pin now lives at the gateway and is tested in
``test_openai_gateway.py``.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any

import httpx
import openai
import pytest

from dbt_charts.ai.failures import AITurnFailure
from dbt_charts.ai.llm import (
    REASONING_EFFORTS,
    LLMClientError,
    OpenAIAdapter,
    _to_strict_json_schema,
    classify,
    create_client,
    normalize_openai_tools,
)
from dbt_charts.ai.messages import (
    AgentMessage,
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from dbt_charts.ai.openai_gateway import (
    FunctionCall,
    GatewayError,
    GatewayEvent,
    ReasoningSummaryDelta,
    ReasoningSummaryDone,
    ResponsesRequest,
    TextDelta,
)
from dbt_charts.ai.tool_schemas import (
    AGENT_TOOLS,
    RENDER_BOARD,
    SUGGEST_BOARD_PLACEMENT,
    subset_tool,
)

from .conftest import strict_mode_violations


class _FakeGateway:
    """Captures the request passed to ``stream``/``create``; yields *events*."""

    total_input_tokens = 0
    total_output_tokens = 0
    total_cached_input_tokens = 0
    total_reasoning_output_tokens = 0
    calls = 0

    def __init__(self, events: list[GatewayEvent] | None = None) -> None:
        self.last_request: ResponsesRequest | None = None
        self._events = events or []

    def create(self, request: ResponsesRequest) -> Any:
        self.calls += 1
        self.last_request = request
        return None

    def stream(self, request: ResponsesRequest) -> Iterator[GatewayEvent]:
        self.calls += 1
        self.last_request = request
        yield from self._events


def _make_client(**kwargs: Any) -> tuple[OpenAIAdapter, _FakeGateway]:
    gateway = _FakeGateway()
    client = OpenAIAdapter(gateway, model="gpt-test", **kwargs)
    return client, gateway


def test_opening_request_has_no_function_call_output() -> None:
    """An opening turn (user message only) must not include function_call_output items."""
    client, gateway = _make_client()
    messages: list[AgentMessage] = [UserMessage(content="show revenue")]
    list(client.stream_with_tools(messages=messages, system_prompt="you are helpful"))

    assert gateway.last_request is not None
    input_items = gateway.last_request.input or []
    types = [item.get("type") for item in input_items]
    assert "function_call_output" not in types


def test_follow_up_request_carries_function_call_output_and_no_previous_response_id() -> (
    None
):
    """A follow-up turn must carry function_call_output with the originating call_id
    and must not include previous_response_id anywhere in the request.

    The absence of previous_response_id is the stateless contract introduced
    2026-07-23 to replace server-side response chaining.
    """
    client, gateway = _make_client()
    messages: list[AgentMessage] = [
        UserMessage(content="show revenue"),
        AssistantMessage(
            tool_calls=[
                ToolCall(
                    id="call_abc", name="execute_query", arguments={"sql": "SELECT 1"}
                )
            ]
        ),
        ToolResultMessage(tool_call_id="call_abc", name="execute_query", content="[1]"),
    ]
    list(client.stream_with_tools(messages=messages, system_prompt="you are helpful"))

    assert gateway.last_request is not None
    assert "previous_response_id" not in (gateway.last_request.model_extra or {})

    input_items = gateway.last_request.input or []
    tool_results = [
        item for item in input_items if item.get("type") == "function_call_output"
    ]
    assert len(tool_results) == 1
    assert tool_results[0]["call_id"] == "call_abc"


def test_normalized_openai_tools_carry_strict_flag() -> None:
    """normalize_openai_tools must set strict=True on every emitted tool.

    Once the open-keyed variables schema is removed, strict mode is valid for
    every tool. This pins that the flag is wired — a future revert silently
    killing it would fail here before reaching a live API call.
    """
    client, gateway = _make_client()
    list(
        client.stream_with_tools(messages=[UserMessage(content="x")], system_prompt="y")
    )
    assert gateway.last_request is not None
    tools = gateway.last_request.tools or []
    assert tools, "no tools emitted"
    for tool in tools:
        assert tool.get("strict") is True, (
            f"tool {tool.get('name')!r} missing strict=True: {tool}"
        )


def test_normalized_openai_tools_satisfy_strict_mode_schema_rules() -> None:
    """Every first-party tool schema must satisfy OpenAI's strict-mode /
    Structured Outputs constraints, not just the `format` allowlist.

    Generalizes the #7013 regression (`render_board` / `execute_query` /
    `describe_board`'s `path: Path` fields serializing to
    `format: "path"`, which OpenAI validates before dispatching any call —
    400ing every chat request regardless of prompt) to the full ruleset a
    *different* violation would break identically and just as invisibly, via
    the shared `strict_mode_violations` checker in `conftest.py` (also used
    by `test_tool_schemas.py` — one validator, not two). This test adds the
    one check that's specific to the full payload rather than any one
    schema: the root of each tool's parameters must itself be an object.
    This is offline and fast; `apps/evals/tests/e2e/test_openai_tool_schema_acceptance.py`
    is the live-API companion that catches a rule OpenAI adds or tightens later.

    Covers the full first-party surface Cloud actually sends
    (`AGENT_TOOLS + SUGGEST_BOARD_PLACEMENT`), not just `ALL_TOOLS` — a
    violation added to a file tool must fail here too. MCP passthrough
    schemas are excluded on purpose: they are third-party, never
    strict-marked, so OpenAI does not apply these rules to them.
    """
    tools = normalize_openai_tools([*AGENT_TOOLS, SUGGEST_BOARD_PLACEMENT])
    assert tools, "no tools emitted"
    violations: list[str] = []
    for tool in tools:
        parameters = tool["parameters"]
        if parameters.get("type") != "object":
            violations.append(f"{tool['name']}: root schema is not type object")
        violations.extend(strict_mode_violations(parameters, tool["name"]))
    assert not violations, "strict-mode schema violations found:\n" + "\n".join(
        violations
    )


def test_normalized_openai_tools_drop_mcp_annotations() -> None:
    """`annotations` is MCP wire metadata; the model-facing function payload
    is built from an explicit key set, so it must never leak into it."""
    assert all("annotations" in tool for tool in AGENT_TOOLS)
    for tool in normalize_openai_tools(AGENT_TOOLS):
        assert set(tool) == {"type", "strict", "name", "description", "parameters"}


def test_normalize_does_not_mutate_the_canonical_tool_definitions() -> None:
    """The canonical tool dicts survive normalization byte-identical.

    `_to_strict_json_schema` edits in place (`del schema["format"]`,
    `schema["required"] = ...`) and only `normalize_openai_tools`' deepcopy
    stands between it and the module-level constants — which are shared, not
    copied, by every consumer and by `subset_tool`. Drop that deepcopy and the
    first normalize call rewrites `RENDER_BOARD` for the rest of the process, so
    MCP would advertise a silently strict-ified schema with no test failing.
    """
    before = copy.deepcopy(RENDER_BOARD)
    normalize_openai_tools([subset_tool(RENDER_BOARD, ["path"]), *AGENT_TOOLS])
    assert before == RENDER_BOARD


def test_to_strict_json_schema_unravels_ref_with_sibling_keys() -> None:
    """A `$ref` carrying sibling keys is inlined, not passed through.

    OpenAI rejects `{"$ref": ..., "description": ...}` outright — its own SDK
    unravels the shape (`openai/lib/_pydantic.py`: "we can't use `$ref`s if
    there are also other properties defined"). Pydantic emits exactly that for
    a nested model field annotated with `Field(description=...)`, which is a
    mandated convention in this repo, so this is the most reachable way to
    reproduce #7013's outage: a 400 on the entire tools array, every chat
    request dead, offline suite green. Today's payload is sibling-free only by
    accident — no current Args model nests another model with a description.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "nested": {"$ref": "#/$defs/Nested", "description": "a nested model"}
        },
        "$defs": {
            "Nested": {"type": "object", "properties": {"a": {"type": "string"}}}
        },
    }

    result = _to_strict_json_schema(schema, schema)

    nested = result["properties"]["nested"]
    assert "$ref" not in nested, f"$ref survived with siblings: {nested}"
    assert nested["type"] == "object"
    assert nested["description"] == "a nested model"
    assert nested["additionalProperties"] is False
    assert nested["required"] == ["a"]


def test_reasoning_effort_defaults_to_medium() -> None:
    """The default preserves the pre-`--effort` wire shape exactly.

    The model is passed to the constructor rather than assigned afterwards: the
    level is resolved against the model once, at construction, so that
    `client.effort` is always the level actually sent.
    """
    gateway = _FakeGateway()
    client = OpenAIAdapter(gateway, model="gpt-5.6-luna")
    list(
        client.stream_with_tools(messages=[UserMessage(content="hi")], system_prompt="")
    )

    assert gateway.last_request is not None
    assert gateway.last_request.reasoning == {"effort": "medium", "summary": "auto"}


def test_reasoning_effort_is_threaded_to_the_request() -> None:
    """An explicit effort reaches the Responses call, with summaries still on.

    `summary: auto` is not optional at any level: it is why the hardcoded
    medium existed — without it gpt-5.x emits no reasoning items and the UI
    hangs on a generic "Thinking...".
    """
    gateway = _FakeGateway()
    client = OpenAIAdapter(gateway, model="gpt-5.6-luna", effort="max")
    list(
        client.stream_with_tools(messages=[UserMessage(content="hi")], system_prompt="")
    )

    assert gateway.last_request is not None
    assert gateway.last_request.reasoning == {
        "effort": "max",
        "summary": "auto",
    }


@pytest.mark.parametrize("effort", REASONING_EFFORTS)
def test_every_supported_effort_keeps_summaries_on(effort: str) -> None:
    """Measured 2026-08-12 against gpt-5.6-luna: these four levels emit reasoning
    items; `none` and `low` emit zero, which is why they are not offered."""
    gateway = _FakeGateway()
    client = OpenAIAdapter(gateway, model="gpt-5.6-luna", effort=effort)
    list(
        client.stream_with_tools(messages=[UserMessage(content="hi")], system_prompt="")
    )

    assert gateway.last_request is not None
    reasoning = gateway.last_request.reasoning
    assert reasoning is not None
    assert reasoning["summary"] == "auto"


@pytest.mark.parametrize("effort", ["none", "low", "minimal", "MAX", "", "maximum"])
def test_unsupported_effort_raises_instead_of_falling_back(effort: str) -> None:
    """No silent coercion to a working level — a typo must fail loudly at
    construction, not quietly measure a different arm than the one requested."""
    with pytest.raises(ValueError, match="effort"):
        OpenAIAdapter(_FakeGateway(), model="gpt-5.6-luna", effort=effort)


def test_create_client_threads_effort() -> None:
    client = create_client(model="gpt-5.6-luna", effort="xhigh")
    assert client.effort == "xhigh"


def test_non_streaming_create_carries_the_same_effort() -> None:
    """`create()` must send the client's effort too, not just `stream_with_tools`.

    The single-pass BIRD rungs go through `generate_sql` -> `create()`. If only
    the streaming path carried the reasoning block, `--effort max --solver sql`
    would record `effort: max` on a run whose requests never asked for it — a
    provenance claim about a level that never applied, which is worse than no
    field at all.
    """
    gateway = _FakeGateway()
    client = OpenAIAdapter(gateway, model="gpt-5.6-luna", effort="xhigh")
    client.create(model=client.model, input=[])

    assert gateway.last_request is not None
    assert gateway.last_request.reasoning == {
        "effort": "xhigh",
        "summary": "auto",
    }


def test_non_streaming_create_omits_reasoning_on_a_model_without_it() -> None:
    """A model that rejects reasoning summaries must not receive the block."""
    gateway = _FakeGateway()
    client = OpenAIAdapter(gateway, model="gpt-4.1-mini")
    client.create(model=client.model, input=[])

    assert gateway.last_request is not None
    assert gateway.last_request.reasoning is None


def test_effort_none_sends_no_reasoning_block() -> None:
    """`effort=None` is the explicit opt-out, and it must reach the wire.

    Cloud's fast-cheap client generates filler (session titles, starter
    suggestions) inside a request, on an 8s single-attempt budget. Thinking
    tokens there buy nothing and lose the race, so it opts out — and the opt-out
    has to be a level the caller can name, not a model check, because the model
    it runs (`gpt-5.4-nano`) does accept reasoning.
    """
    gateway = _FakeGateway()
    client = OpenAIAdapter(gateway, model="gpt-5.4-nano", effort=None)
    client.create(model=client.model, input=[])

    assert gateway.last_request is not None
    assert gateway.last_request.reasoning is None
    assert client.effort is None


def test_effort_is_none_when_the_model_cannot_receive_it() -> None:
    """`client.effort` is what was *sent*, so a non-reasoning model reports None.

    Run records read this for provenance. `--effort` defaults to medium, so a
    `--model gpt-4o` baseline is built with `effort="medium"` while every request
    goes out with no reasoning block at all. Recording "medium" there would file
    that run as a fourth replicate in the ladder's medium bar — a level that
    never applied, which is exactly the invention the ladder's SQL refuses to do
    for pre-flag runs.
    """
    client = OpenAIAdapter(_FakeGateway(), model="gpt-4o", effort="medium")

    assert client.effort is None


def test_reasoning_summaries_map_to_thinking_status_events() -> None:
    """Reasoning summary deltas map to ThinkingStatus, keyed by their block."""
    from dbt_charts.ai.events import ThinkingStatus

    events: list[GatewayEvent] = [
        ReasoningSummaryDelta(block="rs_1:0", text="Checking the schema."),
        ReasoningSummaryDone(block="rs_1:0", text="Checking the schema."),
    ]
    gateway = _FakeGateway(events)
    client = OpenAIAdapter(gateway, model="gpt-5.6")

    out = list(
        client.stream_with_tools(
            messages=[UserMessage(content="hi")], system_prompt="SYSTEM", tools=[]
        )
    )

    assert out == [
        ThinkingStatus(status="Checking the schema.", block="rs_1:0"),
        ThinkingStatus(status="Checking the schema.", block="rs_1:0"),
    ]


def test_content_delta_events_map_to_content_delta() -> None:
    from dbt_charts.ai.events import ContentDelta

    gateway = _FakeGateway([TextDelta(text="hello")])
    client = OpenAIAdapter(gateway, model="gpt-4.1-mini")

    out = list(
        client.stream_with_tools(
            messages=[UserMessage(content="hi")], system_prompt="SYSTEM", tools=[]
        )
    )

    assert out == [ContentDelta(delta="hello")]


def test_stream_with_tools_yields_tool_call_events() -> None:
    from dbt_charts.ai.events import ToolCallEvent

    events: list[GatewayEvent] = [
        FunctionCall(
            call_id="call_1", name="execute_query", arguments={"sql": "SELECT 1"}
        )
    ]
    gateway = _FakeGateway(events)
    client = OpenAIAdapter(gateway, model="gpt-4.1-mini")

    out = list(
        client.stream_with_tools(
            messages=[UserMessage(content="hi")], system_prompt="SYSTEM", tools=[]
        )
    )

    tool_calls = [e for e in out if isinstance(e, ToolCallEvent)]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "execute_query"
    assert tool_calls[0].arguments == {"sql": "SELECT 1"}


def test_stream_with_tools_translates_gateway_errors() -> None:
    """The adapter's own try/except is exactly this translation: a GatewayError
    (the wire's error type) becomes LLMClientError (the consumer contract) —
    each layer owns its error, and nothing outside the adapter should ever
    see a GatewayError."""

    class _RaisingGateway(_FakeGateway):
        def stream(self, request: ResponsesRequest) -> Iterator[GatewayEvent]:
            self.calls += 1
            self.last_request = request
            raise GatewayError("boom")

    client = OpenAIAdapter(_RaisingGateway(), model="gpt-4.1-mini")

    with pytest.raises(LLMClientError, match="boom") as excinfo:
        list(
            client.stream_with_tools(
                messages=[UserMessage(content="hi")], system_prompt="SYSTEM", tools=[]
            )
        )

    assert isinstance(excinfo.value.__cause__, GatewayError)


def test_create_translates_gateway_errors() -> None:
    class _RaisingGateway(_FakeGateway):
        def create(self, request: ResponsesRequest) -> Any:
            self.calls += 1
            self.last_request = request
            raise GatewayError("boom")

    client = OpenAIAdapter(_RaisingGateway(), model="gpt-4.1-mini")

    with pytest.raises(LLMClientError, match="boom") as excinfo:
        client.create(model=client.model, input=[])

    assert isinstance(excinfo.value.__cause__, GatewayError)


def test_usage_counters_delegate_to_the_gateway() -> None:
    """The adapter reads through to the gateway rather than accumulating its
    own copy: a consumer asking the client what a turn cost must get the same
    answer as the wire, with no second counter to drift."""
    gateway = _FakeGateway()
    gateway.total_input_tokens = 111
    gateway.total_output_tokens = 22
    gateway.total_cached_input_tokens = 7
    gateway.total_reasoning_output_tokens = 3
    gateway.calls = 5
    client = OpenAIAdapter(gateway, model="gpt-4.1-mini")

    assert client.total_input_tokens == 111
    assert client.total_output_tokens == 22
    assert client.total_cached_input_tokens == 7
    assert client.total_reasoning_output_tokens == 3
    assert client.calls == 5


def test_usage_counters_track_the_gateway_after_a_call() -> None:
    """Read-through, not copied at construction — non-zero values set on the
    gateway *after* the adapter exists must still be visible."""
    client, gateway = _make_client()

    list(
        client.stream_with_tools(
            messages=[UserMessage(content="hi")], system_prompt="S"
        )
    )
    gateway.total_input_tokens = 40

    assert client.calls == 1
    assert client.total_input_tokens == 40


def test_gateway_attribute_is_the_instance_passed_at_construction() -> None:
    """Public and unmodified: a caller reads usage counters off `client.gateway`
    directly now that they're off the `LLMClient` protocol."""
    gateway = _FakeGateway()
    client = OpenAIAdapter(gateway, model="gpt-4.1-mini")

    assert client.gateway is gateway


class TestClassifyTurnFailure:
    """`classify()` is the one place a provider fault becomes a typed reason.

    It reads the provider exception preserved on ``__cause__`` — its
    machine-readable ``code`` and HTTP status — never the prose of the message.
    A message is not an API: it is localized, reworded between SDK releases, and
    would make the metric label depend on vendor copy.
    """

    def _wrapped(self, cause: Exception) -> LLMClientError:
        """An LLMClientError as the adapter raises it: wrapping the
        GatewayError the gateway raised, which itself wraps the raw
        provider exception — `... from exc` at both translation sites."""
        gateway_error = GatewayError(str(cause))
        gateway_error.__cause__ = cause
        err = LLMClientError(str(gateway_error))
        err.__cause__ = gateway_error
        return err

    def _status_error(self, cls: type, status: int, code: str | None) -> Any:
        response = httpx.Response(
            status, request=httpx.Request("POST", "http://provider.test")
        )
        return cls("boom", response=response, body={"code": code} if code else None)

    def test_a_context_overflow_is_its_own_member(self) -> None:
        cause = self._status_error(
            openai.BadRequestError, 400, "context_length_exceeded"
        )
        assert classify(self._wrapped(cause)) is (AITurnFailure.CONTEXT_WINDOW_EXCEEDED)

    def test_a_rate_limit_and_a_spent_quota_are_different_members(self) -> None:
        """Both arrive as 429. One clears on its own in a minute; the other
        needs somebody to go and pay. Folding them together is what makes the
        error series unactionable."""
        throttled = self._status_error(
            openai.RateLimitError, 429, "rate_limit_exceeded"
        )
        spent = self._status_error(openai.RateLimitError, 429, "insufficient_quota")
        assert classify(self._wrapped(throttled)) is AITurnFailure.RATE_LIMIT
        assert classify(self._wrapped(spent)) is AITurnFailure.USAGE_LIMIT_EXCEEDED

    def test_a_bare_429_without_a_code_is_a_rate_limit(self) -> None:
        cause = self._status_error(openai.RateLimitError, 429, None)
        assert classify(self._wrapped(cause)) is AITurnFailure.RATE_LIMIT

    def test_a_provider_5xx_is_server_overloaded(self) -> None:
        cause = self._status_error(openai.InternalServerError, 503, None)
        assert classify(self._wrapped(cause)) is AITurnFailure.SERVER_OVERLOADED

    def test_a_dropped_connection_is_stream_disconnected(self) -> None:
        cause = httpx.ReadError("peer closed connection")
        assert classify(self._wrapped(cause)) is AITurnFailure.STREAM_DISCONNECTED

    def test_the_sdks_own_connection_errors_are_stream_disconnected(self) -> None:
        """The OpenAI SDK wraps httpx rather than subclassing it, so an
        isinstance check against httpx alone misses exactly the pre-stream
        timeouts and connection drops this member exists for."""
        timeout = openai.APITimeoutError(
            request=httpx.Request("POST", "http://provider.test")
        )
        dropped = openai.APIConnectionError(
            message="connection reset",
            request=httpx.Request("POST", "http://provider.test"),
        )
        assert classify(self._wrapped(timeout)) is AITurnFailure.STREAM_DISCONNECTED
        assert classify(self._wrapped(dropped)) is AITurnFailure.STREAM_DISCONNECTED

    def test_a_raw_cause_classifies_the_same_as_a_gateway_wrapped_one(self) -> None:
        """A test double (or non-OpenAI ``LLMClient``) may set ``__cause__``
        directly to the raw provider exception, skipping the gateway's own
        wrapping — the real double-wrap and this single-wrap shape must
        classify identically."""
        cause = self._status_error(openai.RateLimitError, 429, "insufficient_quota")
        bare = LLMClientError(str(cause))
        bare.__cause__ = cause
        assert classify(bare) is AITurnFailure.USAGE_LIMIT_EXCEEDED

    def test_an_unrecognized_provider_fault_is_provider_error(self) -> None:
        """Still a provider fault — it came through LLMClientError — just not
        one we have a narrower name for. `internal` would blame our own code."""
        assert classify(LLMClientError("something new from the vendor")) is (
            AITurnFailure.PROVIDER_ERROR
        )

    def test_a_non_provider_exception_is_internal(self) -> None:
        assert classify(ValueError("a bug in our code")) is AITurnFailure.INTERNAL

    def test_every_result_is_a_member_of_the_closed_enum(self) -> None:
        """The enum is bounded, which is what makes it safe as a Prometheus
        label. A classifier falling back to `type(exc).__name__` would make
        cardinality unbounded from user-triggered input."""
        assert classify(RuntimeError("x")) in set(AITurnFailure)

    def test_tool_error_is_not_a_member(self) -> None:
        """Dropped deliberately: `dispatch_tool_call` converts every handler
        exception into a return envelope, so nothing can emit it. The
        tool-surface task adds it when it has a producer."""
        assert "tool_error" not in {m.value for m in AITurnFailure}
