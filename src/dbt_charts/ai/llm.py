"""Thin LLM client adapters for the terminal agent."""

from __future__ import annotations

import copy
import json
import os
import sys
from collections.abc import Iterator
from typing import Any, Protocol

from dbt_charts.ai.events import (
    ContentDelta,
    StreamEvent,
    ThinkingStatus,
    ToolCallEvent,
)
from dbt_charts.ai.failures import AITurnFailure
from dbt_charts.ai.messages import (
    AgentMessage,
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)
from dbt_charts.ai.openai_gateway import (
    CompletedResponse,
    FunctionCall,
    GatewayError,
    OpenAIGateway,
    ReasoningSummaryDelta,
    ReasoningSummaryDone,
    ResponsesGateway,
    ResponsesRequest,
    TextDelta,
    is_connection_fault,
)
from dbt_charts.ai.tool_schemas import ALL_TOOLS

if sys.version_info >= (3, 11):
    from typing import assert_never
else:
    from typing_extensions import assert_never

DEFAULT_OPENAI_MODEL = "gpt-5.6-terra"


def _parse_data_uri(uri: str) -> tuple[str, str]:
    """Split a base64 ``data:`` URI into its media type and payload.

    Raises ``LLMClientError`` on anything that isn't a base64 data URI — the
    frontend and view already validate the shape, so a miss here is a bug, not
    user input to tolerate.
    """
    prefix, _, data = uri.partition(",")
    if not (prefix.startswith("data:") and prefix.endswith(";base64") and data):
        raise LLMClientError(f"Attachment is not a base64 data URI: {uri[:32]!r}...")
    media_type = prefix[len("data:") : -len(";base64")]
    return media_type, data


def _openai_content_block(block: dict[str, Any]) -> dict[str, Any]:
    """Convert one provider-neutral content block to a Responses API input block."""
    block_type = block.get("type")
    if block_type == "text" and isinstance(block.get("text"), str):
        return {"type": "input_text", "text": block["text"]}
    if block_type == "image_url":
        image_url = block.get("image_url")
        if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
            return {"type": "input_image", "image_url": image_url["url"]}
    if block_type == "document" and isinstance(block.get("url"), str):
        media_type, _ = _parse_data_uri(block["url"])
        subtype = media_type.rpartition("/")[2] or "bin"
        return {
            "type": "input_file",
            "filename": f"attachment.{subtype}",
            "file_data": block["url"],
        }
    raise LLMClientError(f"Unsupported OpenAI message content block: {block_type!r}.")


def _resolve_model(
    explicit_model: str | None,
    env_var: str,
    default_model: str,
) -> str:
    return explicit_model or os.getenv(env_var) or default_model


# OpenAI's Structured Outputs / strict function-calling schema validator only
# accepts this fixed allowlist for a string "format" — anything else (e.g. the
# "path" pydantic emits for pathlib.Path fields) is a 400 at request time,
# rejecting the whole tools array regardless of which tool the format lives on.
_OPENAI_ALLOWED_STRING_FORMATS = {
    "date-time",
    "time",
    "date",
    "duration",
    "email",
    "hostname",
    "ipv4",
    "ipv6",
    "uuid",
}


def _resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    """Resolve a local ``#/`` JSON-Schema pointer against the document root."""
    if not ref.startswith("#/"):
        raise ValueError(f"Unexpected $ref format {ref!r}; does not start with '#/'")
    resolved: Any = root
    for key in ref[2:].split("/"):
        resolved = resolved[key]
    if not isinstance(resolved, dict):
        raise ValueError(
            f"$ref {ref!r} resolved to {type(resolved).__name__}, not dict"
        )
    return resolved


def _to_strict_json_schema(
    schema: dict[str, Any], root: dict[str, Any]
) -> dict[str, Any]:
    """Transform a JSON Schema dict in-place to conform to OpenAI strict-mode rules.

    Mirrors openai/lib/_pydantic.py::_ensure_strict_json_schema, plus one rule
    it doesn't need (its own SDK never emits pydantic-only formats): unsupported
    string `format` values are stripped, since OpenAI validates them by allowlist.
    - Every object gets additionalProperties: false.
    - required covers all property keys whenever properties is present.
    - 'default: null' entries are stripped (non-null defaults are left intact).
    - A `$ref` carrying sibling keys is inlined; OpenAI rejects that shape
      outright, and pydantic emits it for a nested model given a Field
      description.
    Recurses into $defs, properties, items, anyOf, allOf, oneOf.
    ``root`` is the document the ``#/`` pointers resolve against — the top-level
    schema, threaded through the recursion unchanged.
    """
    ref = schema.get("$ref")
    if isinstance(ref, str) and len(schema) > 1:
        # Sibling keys win over the target's, matching the OpenAI SDK; then the
        # inlined body still needs the rules below applied to it.
        schema.update({**_resolve_ref(root, ref), **schema})
        del schema["$ref"]

    if schema.get("type") == "object":
        schema.setdefault("additionalProperties", False)
    props = schema.get("properties", {})
    if props:
        schema["required"] = list(props.keys())

    if schema.get("default") is None and "default" in schema:
        del schema["default"]

    fmt = schema.get("format")
    if isinstance(fmt, str) and fmt not in _OPENAI_ALLOWED_STRING_FORMATS:
        del schema["format"]

    for sub in schema.get("$defs", {}).values():
        _to_strict_json_schema(sub, root)

    for sub in schema.get("properties", {}).values():
        _to_strict_json_schema(sub, root)

    for key in ("anyOf", "allOf", "oneOf"):
        for sub in schema.get(key, []):
            _to_strict_json_schema(sub, root)

    if "items" in schema:
        _to_strict_json_schema(schema["items"], root)

    return schema


def _strict_parameters(input_schema: dict[str, Any]) -> dict[str, Any]:
    """Strict-transform a copy of ``input_schema``, resolving refs against itself.

    The copy is what makes the transform safe: ``_to_strict_json_schema`` edits
    in place, and the canonical tool dicts are shared module-level constants.
    """
    schema = copy.deepcopy(input_schema)
    return _to_strict_json_schema(schema, schema)


def normalize_openai_tools(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Build the Responses API ``tools`` payload for a set of tool definitions.

    The one place tool schemas are prepared for OpenAI. Hosts that assemble
    their own tool list (Cloud, A lIe) call this rather than re-deriving the
    shape: strict mode and the string-``format`` allowlist are enforced here,
    so a schema rule fixed here is fixed for every surface.
    """
    # None means "use default agent tools"; [] means "no tools, text-only completion".
    source_tools = ALL_TOOLS if tools is None else tools
    result: list[dict[str, Any]] = []
    for tool in source_tools:
        if "input_schema" in tool:
            # First-party tool in MCP/input_schema format — strict-transform and
            # convert to OpenAI Responses API format. Strict mode is a property of
            # schemas we authored, not of the transport; MCP passthrough schemas
            # (already in OpenAI function format) are appended below unchanged.
            result.append(
                {
                    "type": "function",
                    "strict": True,
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": _strict_parameters(tool["input_schema"]),
                }
            )
        else:
            # Already in OpenAI function format (e.g. MCP passthrough) — pass through
            # untouched. Caller-supplied schemas must not be strict-marked or rewritten.
            result.append(tool)
    return result


class LLMClientError(Exception):
    """Raised when a provider request fails.

    The consumer-contract error — every ``LLMClient`` implementation raises
    this, never a provider-specific type. ``OpenAIAdapter`` catches the
    gateway's own :class:`~dbt_charts.ai.openai_gateway.GatewayError` and
    re-raises this; that error type belongs to the wire and stops there.
    """


#: Provider error codes that name a narrower failure than their HTTP status
#: does. `insufficient_quota` is the one that matters: it shares 429 with
#: ordinary throttling, but one clears itself in a minute and the other needs
#: somebody to go and pay.
_FAILURE_BY_ERROR_CODE = {
    "context_length_exceeded": AITurnFailure.CONTEXT_WINDOW_EXCEEDED,
    "rate_limit_exceeded": AITurnFailure.RATE_LIMIT,
    "insufficient_quota": AITurnFailure.USAGE_LIMIT_EXCEEDED,
    "billing_hard_limit_reached": AITurnFailure.USAGE_LIMIT_EXCEEDED,
}


def classify(exc: BaseException) -> AITurnFailure:
    """Name the failure behind *exc*.

    Reads the provider exception preserved on ``__cause__`` — its
    machine-readable ``code`` first, then its HTTP status. Never the message
    text: prose is localized and reworded between SDK releases, so a label
    derived from it would drift without any change on our side.

    The real path double-wraps: ``LLMClientError.__cause__`` is the
    :class:`~dbt_charts.ai.openai_gateway.GatewayError` the adapter caught,
    whose own ``__cause__`` is the raw provider exception — so a
    ``GatewayError`` cause is unwrapped one level further before
    classifying. A test double (or a non-OpenAI ``LLMClient``) may set
    ``__cause__`` to the raw exception directly; that shape classifies the
    same way.

    Anything that is not an ``LLMClientError`` never reached the provider and
    is ours: ``internal``.
    """
    if not isinstance(exc, LLMClientError):
        return AITurnFailure.INTERNAL
    cause = exc.__cause__
    if isinstance(cause, GatewayError):
        cause = cause.__cause__
    if cause is None:
        # Raised by us with no wrapped provider exception — an early-stop
        # terminal (`response.incomplete` / `failed`). Still the provider's
        # turn to answer for, just not one we can name more precisely yet.
        return AITurnFailure.PROVIDER_ERROR
    if is_connection_fault(cause):
        return AITurnFailure.STREAM_DISCONNECTED
    code = getattr(cause, "code", None)
    if isinstance(code, str) and code in _FAILURE_BY_ERROR_CODE:
        return _FAILURE_BY_ERROR_CODE[code]
    status = getattr(cause, "status_code", None)
    if isinstance(status, int):
        if status == 429:
            return AITurnFailure.RATE_LIMIT
        if status >= 500:
            return AITurnFailure.SERVER_OVERLOADED
    return AITurnFailure.PROVIDER_ERROR


# Reasoning levels we offer, cheapest first. The provider also accepts "none" and
# "low", and they are deliberately absent: measured against gpt-5.6-luna
# (2026-08-12) both return zero reasoning items, so `summary: auto` has nothing to
# summarize and the UI hangs on a generic "Thinking...". Medium is the floor that
# actually emits summaries; every level here does.
REASONING_EFFORTS = ("medium", "high", "xhigh", "max")

DEFAULT_REASONING_EFFORT = "medium"


def _validate_effort(effort: str | None) -> str | None:
    """Return the level, or None for the explicit no-reasoning opt-out.

    None is not a fallback — a caller has to type it, and it means "send no
    reasoning block at all". Cloud's fast-cheap filler client is the one caller
    that wants that: it runs inside a request on an 8s single-attempt budget,
    where thinking tokens only make it lose its race.
    """
    if effort is None:
        return None
    if effort not in REASONING_EFFORTS:
        raise ValueError(
            f"Unsupported reasoning effort: {effort!r}. "
            f"Expected one of {', '.join(REASONING_EFFORTS)}."
        )
    return effort


def _supports_reasoning_summaries(model: str) -> bool:
    """Return whether this Responses model accepts reasoning summaries."""
    return model.startswith("gpt-5")


# -- Client protocol and implementation --------------------------------------


class LLMClient(Protocol):
    """Provider-neutral streaming interface."""

    provider: str
    model: str
    # Reasoning level this client actually sends, None when it sends no reasoning
    # block at all — either because the caller opted out or because the model
    # cannot take one. Declared alongside `model` because it is the other half of
    # "which arm ran", and run records read it for provenance: it has to describe
    # what went over the wire, not what was asked for.
    effort: str | None

    # Read-only: a client reports what its wire has spent, it does not let a
    # consumer set it. Cache-read input is part of input and reasoning output
    # is part of output — nested, not disjoint — but billed at different rates,
    # so a cache-hit collapse stays visible instead of hiding in the totals.
    @property
    def total_input_tokens(self) -> int: ...
    @property
    def total_output_tokens(self) -> int: ...
    @property
    def total_cached_input_tokens(self) -> int: ...
    @property
    def total_reasoning_output_tokens(self) -> int: ...
    @property
    def calls(self) -> int: ...

    def stream_with_tools(
        self,
        *,
        messages: list[AgentMessage],
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamEvent]: ...


class OpenAIAdapter:
    """Speaks dbt-charts vocabulary to the OpenAI Responses API.

    Translation only: ``AgentMessage``\\ s -> input items, tools -> strict
    schemas, raw stream events -> :class:`StreamEvent`\\ s. Never touches the
    SDK directly — that is :class:`~dbt_charts.ai.openai_gateway.ResponsesGateway`'s
    job, injected at construction so a host can decorate it to observe
    provider requests.

    Stateless by design: every call sends the full conversation, with no
    server-side response-chaining parameter linking one call to the next.
    (Dave, 2026-07-23) That chaining provoked the mixed-content bug this class
    replaced — it made the model's own tool-call history depend on server
    state we didn't fully control, and a missed shape in the incremental
    converter leaked a raw tool envelope into chat. The trade is losing
    OpenAI's server-side reasoning-item carryover between tool-loop
    iterations within one turn, which may degrade multi-step tool reasoning.
    Worth reconsidering once the provider-side chaining flakiness
    (community.openai.com thread 1354672) is resolved — re-measure against
    the eval canary before restoring it.
    """

    provider = "openai"

    def __init__(
        self,
        gateway: ResponsesGateway,
        model: str | None = None,
        effort: str | None = DEFAULT_REASONING_EFFORT,
    ) -> None:
        self.gateway = gateway
        self.model = _resolve_model(
            model,
            "OPENAI_MODEL",
            DEFAULT_OPENAI_MODEL,
        )
        # Validated at construction, not at call time: an eval arm that asks for a
        # level the provider rejects must fail before it bills a run's worth of
        # tokens under an effort nobody chose. Resolved against the model in the
        # same breath, so `self.effort` is the level that will actually be sent —
        # a run record reading it can never claim a level a gpt-4o baseline never
        # received.
        requested = _validate_effort(effort)
        self.effort = requested if _supports_reasoning_summaries(self.model) else None

    # Read through to the gateway rather than accumulating a second copy:
    # the wire counted these, and two counters for one quantity drift.
    @property
    def total_input_tokens(self) -> int:
        return self.gateway.total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self.gateway.total_output_tokens

    @property
    def total_cached_input_tokens(self) -> int:
        return self.gateway.total_cached_input_tokens

    @property
    def total_reasoning_output_tokens(self) -> int:
        return self.gateway.total_reasoning_output_tokens

    @property
    def calls(self) -> int:
        return self.gateway.calls

    def _reasoning(self) -> dict[str, Any] | None:
        # Effort must be explicit. Left to its default, gpt-5.x emits no
        # reasoning items at all, and "summary" then has nothing to
        # summarize — the UI is stuck on a generic "Thinking...".
        if self.effort is None:
            return None
        return {"effort": self.effort, "summary": "auto"}

    def create(self, **kwargs: Any) -> CompletedResponse:
        """Non-streaming Responses API call, with the same reasoning block
        sent by ``stream_with_tools``: the effort is a property of the
        adapter, so a caller that never streams must not silently run at the
        provider's default. An adapter built with ``effort=None`` sends no
        block on either path.
        """
        reasoning = self._reasoning()
        if reasoning is not None:
            kwargs["reasoning"] = reasoning
        request = ResponsesRequest(**kwargs)
        try:
            return self.gateway.create(request)
        except GatewayError as exc:
            raise LLMClientError(str(exc)) from exc

    def _user_message_input(self, message: UserMessage) -> dict[str, Any] | None:
        content = message.content
        if not content:
            return None
        if isinstance(content, list):
            return {
                "role": "user",
                "content": [_openai_content_block(block) for block in content],
            }
        return {"role": "user", "content": content}

    def _assistant_message_input(
        self, message: AssistantMessage
    ) -> list[dict[str, Any]]:
        # A preamble immediately followed by tool calls is discardable
        # commentary — the function_call items alone fully specify what
        # happened. Echoing that text back as a disconnected message is the
        # 2026-07-23 incident: it broke function_call/function_call_output
        # adjacency and degraded the model into writing the tool call as prose.
        if message.tool_calls:
            return [
                {
                    "type": "function_call",
                    "call_id": tc.id,
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                }
                for tc in message.tool_calls
            ]
        if not message.content:
            return []
        return [{"role": "assistant", "content": message.content}]

    def _messages_to_input(self, messages: list[AgentMessage]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in messages:
            match message:
                case UserMessage():
                    user_item = self._user_message_input(message)
                    if user_item is not None:
                        items.append(user_item)
                case AssistantMessage():
                    items.extend(self._assistant_message_input(message))
                case ToolResultMessage():
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": message.tool_call_id,
                            "output": message.content,
                        }
                    )
                case _:
                    assert_never(message)
        return items

    def stream_with_tools(
        self,
        *,
        messages: list[AgentMessage],
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamEvent]:
        input_items = self._messages_to_input(messages)
        if not input_items:
            return

        request = ResponsesRequest(
            model=self.model,
            instructions=system_prompt,
            input=input_items,
            tools=normalize_openai_tools(tools),
            stream=True,
            reasoning=self._reasoning(),
        )

        # Accumulation is presentation, and stays here: the gateway hands
        # back each reasoning-summary chunk on its own, keyed by block.
        reasoning_summaries: dict[str, str] = {}
        try:
            for event in self.gateway.stream(request):
                match event:
                    case TextDelta():
                        yield ContentDelta(delta=event.text)
                    case ReasoningSummaryDelta():
                        summary = reasoning_summaries.get(event.block, "") + event.text
                        reasoning_summaries[event.block] = summary
                        if summary:
                            yield ThinkingStatus(status=summary, block=event.block)
                    case ReasoningSummaryDone():
                        reasoning_summaries[event.block] = event.text
                        if event.text:
                            yield ThinkingStatus(status=event.text, block=event.block)
                    case FunctionCall():
                        yield ToolCallEvent(
                            id=event.call_id,
                            name=event.name,
                            arguments=event.arguments,
                        )
                    case _:
                        assert_never(event)
        except GatewayError as exc:
            raise LLMClientError(str(exc)) from exc


def create_client(
    provider: str | None = None,
    model: str | None = None,
    effort: str | None = DEFAULT_REASONING_EFFORT,
) -> OpenAIAdapter:
    """Create an LLM client. OpenAI is the only supported provider.

    Concretely typed rather than the narrower ``LLMClient`` streaming
    protocol: OpenAI is the only implementation pre-launch, and callers that
    need usage counters read them off the returned adapter's ``gateway`` —
    the protocol carries none. ``LLMClient`` still exists for consumers that
    only stream and don't care which concrete client they're holding.
    """
    if provider and provider != "openai":
        raise ValueError(f"Unsupported provider: {provider}")
    if not model and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "No LLM API key found. Set OPENAI_API_KEY in the environment, "
            "or pass --model to select a model explicitly."
        )
    return OpenAIAdapter(OpenAIGateway(), model=model, effort=effort)
