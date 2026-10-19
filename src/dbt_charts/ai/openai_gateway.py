"""OpenAI Responses API transport.

Owns SDK construction, error translation, and usage/early-stop accounting —
everything about *talking to the wire*. Absorbs the SDK's untyped
dict-vs-typed-object duality at the boundary and emits its own Pydantic wire
models (``ResponsesRequest`` in, ``GatewayEvent``/``CompletedResponse`` out),
so parsing happens once, here. :class:`ResponsesGateway` is the seam a host
decorates to observe provider requests.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)


class GatewayError(Exception):
    """A provider-request failure: an API/network error or an early stop.

    The message carries the phase or early-stop reason.
    """


#: Connect budget for a caller-supplied read timeout. Named so the callers that
#: reason about a total wall-clock bound can do the arithmetic against a
#: constant rather than a duplicated literal.
CONNECT_TIMEOUT_SECONDS = 5.0


def _llm_timeout() -> httpx.Timeout:
    """HTTP timeout for LLM calls.

    Bounds every LLM request so a stalled connection surfaces as an error
    instead of hanging for the SDK default (~10 min). ``read`` is the max gap
    between streamed chunks; kept well under callers' own stall watchdogs.
    Overridable via env for unusually slow models.
    """
    return httpx.Timeout(
        float(os.getenv("DCT_LLM_READ_TIMEOUT_SECONDS", "60")),
        connect=float(os.getenv("DCT_LLM_CONNECT_TIMEOUT_SECONDS", "10")),
    )


def _is_openai_api_error(exc: Exception) -> bool:
    try:
        from openai import APIError
    except ImportError:
        return isinstance(exc, httpx.HTTPError)
    return isinstance(exc, (APIError, httpx.HTTPError))


def is_connection_fault(exc: BaseException) -> bool:
    """Whether *exc* is a transport failure rather than a provider answer.

    The OpenAI SDK wraps httpx rather than subclassing it, so an isinstance
    check against httpx alone misses the pre-stream timeouts and connection
    resets — the very cases this classification exists for. Import lazily, as
    ``_is_openai_api_error`` does: openai is a bring-your-own dependency.
    """
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    try:
        from openai import APIConnectionError
    except ImportError:
        return False
    # APITimeoutError subclasses APIConnectionError.
    return isinstance(exc, APIConnectionError)


def _is_wire_error(exc: Exception) -> bool:
    """True for anything that must surface as GatewayError: an OpenAI/httpx
    transport failure, or a wire payload that fails this gateway's own
    parsing shim (a ValidationError from one of the models above)."""
    return isinstance(exc, ValidationError) or _is_openai_api_error(exc)


# -- Request envelope ---------------------------------------------------------


class ResponsesRequest(BaseModel):
    """The Responses API request, 80/20-typed.

    Names the eight fields every caller in this codebase sets; everything else
    a caller passes (e.g. ``text=`` for structured JSON output) rides along
    as an extra field — `extra="allow"` here is not wire-shape parsing, it's
    a deliberate concession to comprehensiveness-is-not-the-goal: giving the
    common fields a name and a type without mirroring the full Responses API.
    Interiors of ``input``/``tools`` stay loose dicts.
    """

    model_config = ConfigDict(extra="allow")

    model: str
    instructions: str | None = None
    input: list[dict[str, Any]] | None = None
    tools: list[dict[str, Any]] | None = None
    reasoning: dict[str, Any] | None = None
    stream: bool = False
    store: bool | None = None
    include: list[str] | None = None


# -- Wire-boundary parsing models ---------------------------------------------
#
# `extra="ignore", from_attributes=True` on every model below: each validates
# a third-party wire payload (a typed SDK object for most models, a raw dict
# for others — gpt-4.1-mini, gpt-4o) that carries fields this gateway doesn't
# track. That dict-vs-typed-object duality used to be absorbed by a hand-rolled
# attribute-or-dict reader; here Pydantic does it, uniformly, at every nesting level.


class _InputTokensDetails(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    cached_tokens: int | None = None


class _OutputTokensDetails(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    reasoning_tokens: int | None = None


class TokenUsage(BaseModel):
    """Token usage as read off one Responses-API payload."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    input_tokens: int = 0
    output_tokens: int = 0
    # Nested inside the two above, not disjoint from them: cache-read input is
    # part of input, reasoning output is part of output. Left ``None`` (not
    # coerced to 0) when the payload omits the block or reports it as null —
    # that's "not reported", not "zero cached tokens", and a fabricated 0
    # would read downstream as a total cache miss.
    input_tokens_details: _InputTokensDetails | None = None
    output_tokens_details: _OutputTokensDetails | None = None

    @field_validator("input_tokens", "output_tokens", mode="before")
    @classmethod
    def _none_to_zero(cls, value: object) -> object:
        return 0 if value is None else value


class _TypedEvent(BaseModel):
    """Just the discriminant every stream event carries."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)
    type: str = ""


class _TextDeltaEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    delta: str = ""


class _ReasoningSummaryDeltaEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    item_id: str = ""
    summary_index: int | str = ""
    delta: str = ""


class _ReasoningSummaryDoneEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    item_id: str = ""
    summary_index: int | str = ""
    text: str = ""


class _FunctionCallItem(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    type: str = ""
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    # An SDK object or a dict, like the other wire items; see _wire_item_to_dict.
    operation: Any = None


class _OutputItemDoneEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    item: _FunctionCallItem | None = None


class _ResponseErrorPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    message: str | None = None


class _IncompleteDetails(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    reason: str | None = None


class _UsageBearingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    usage: TokenUsage | None = None


class _OutputItemType(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    type: str = ""


class _CompletedResponse(_UsageBearingResponse):
    # Raw wire items (SDK objects or dicts), kept opaque so a reasoning item
    # replays exactly as the provider sent it.
    output: list[Any] = Field(default_factory=list)


class _IncompleteResponse(_UsageBearingResponse):
    incomplete_details: _IncompleteDetails | None = None


class _FailedResponse(_UsageBearingResponse):
    error: _ResponseErrorPayload | None = None


class _CompletedEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    response: _CompletedResponse | None = None


class _IncompleteEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    response: _IncompleteResponse | None = None


class _FailedEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    response: _FailedResponse | None = None


class _BareErrorEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    message: str | None = None


class _CreateResponsePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)
    output_text: str = ""
    usage: TokenUsage | None = None


# -- Gateway-owned output vocabulary ------------------------------------------
#
# These are values this module constructs, never validated from raw wire
# payloads — `extra="forbid"` is the right default here, matching every other
# authored model in the codebase.


class TextDelta(BaseModel):
    """A chunk of assistant prose."""

    model_config = ConfigDict(extra="forbid")
    text: str


class ReasoningSummaryDelta(BaseModel):
    """One incremental chunk of a reasoning summary.

    ``text`` is just this chunk — accumulating it into the running summary is
    presentation, and stays the adapter's job.
    """

    model_config = ConfigDict(extra="forbid")
    block: str
    text: str


class ReasoningSummaryDone(BaseModel):
    """A reasoning summary block's final text."""

    model_config = ConfigDict(extra="forbid")
    block: str
    text: str


class FunctionCall(BaseModel):
    """A tool call, arguments already parsed from the wire's JSON string."""

    model_config = ConfigDict(extra="forbid")
    call_id: str
    name: str
    arguments: dict[str, Any]


class ApplyPatchCall(BaseModel):
    """A call to the built-in ``apply_patch`` tool: its ``operation``
    (create_file / update_file / delete_file) rides as the arguments."""

    model_config = ConfigDict(extra="forbid")
    call_id: str
    operation: dict[str, Any]


class ReasoningItems(BaseModel):
    """A response's reasoning output items, as the wire returned them.

    Carries ``encrypted_content`` when the request asked for it, so a stateless
    caller can hand the items back on the next request.
    """

    model_config = ConfigDict(extra="forbid")
    items: list[dict[str, Any]]


#: Every event shape a consumer of `ResponsesGateway.stream()` can see. Wire
#: events no consumer needs (`response.created`, `response.completed`, ...)
#: are absorbed as gateway bookkeeping and never reach this union — grow it
#: only when a consumer needs more.
GatewayEvent = (
    TextDelta
    | ReasoningSummaryDelta
    | ReasoningSummaryDone
    | FunctionCall
    | ApplyPatchCall
    | ReasoningItems
)


class CompletedResponse(BaseModel):
    """The result of a non-streaming call — just what callers read today."""

    model_config = ConfigDict(extra="forbid")
    output_text: str = ""


#: Stream events that end a response without `response.completed`. Each is
#: ordinary data on the stream, not a raised exception, so a loop that does not
#: read them cannot tell a truncated turn from a finished one.
EARLY_STOP_EVENTS = ("response.incomplete", "response.failed", "error")


def _early_stop(event_type: str, raw_event: Any) -> tuple[str, TokenUsage | None]:
    """Reason text + usage for one EARLY_STOP_EVENTS member.

    Each event keeps its reason somewhere different, and every one of those
    fields is Optional on the wire — the bare error event's ``message`` is
    required by the schema, but the SDK does not trust it either. So each
    branch reports an absent reason as absent, and the pydantic models above
    absorb the dict-vs-typed-object duality uniformly rather than a
    hand-rolled reader risking an ``AttributeError`` that reaches the user as
    a stack trace naming ``NoneType`` instead of a truncated turn.
    """
    if event_type == "error":
        message = _BareErrorEvent.model_validate(raw_event).message
        return (message or "the provider reported an error", None)

    if event_type == "response.failed":
        failed = _FailedEvent.model_validate(raw_event).response
        message = failed.error.message if failed and failed.error else None
        usage = failed.usage if failed else None
        return (message or "the response failed without an error", usage)

    incomplete = _IncompleteEvent.model_validate(raw_event).response
    reason = (
        incomplete.incomplete_details.reason
        if incomplete and incomplete.incomplete_details
        else None
    )
    usage = incomplete.usage if incomplete else None
    if not reason:
        return ("the response was incomplete for an unstated reason", usage)
    if reason == "max_output_tokens":
        return ("the response hit its output limit (max_output_tokens)", usage)
    return (f"the response was incomplete: {reason}", usage)


def early_stop_reason(event: Any) -> str:
    """Why a response ended without completing, phrased for the reader.

    Public because any surface driving the Responses stream has to read
    these three events or it cannot tell a truncated turn from a finished
    one, and a reason parsed correctly here is parsed correctly for every
    one of them.
    """
    event_type = _TypedEvent.model_validate(event).type
    reason, _usage = _early_stop(event_type, event)
    return reason


def _reasoning_block(item_id: str, summary_index: int | str) -> str:
    """Identity of one reasoning summary block: its item, then its part index.

    Doubles as the accumulator key while a block's deltas stream in, so the
    block's identity has one representation rather than a tuple here and a
    string on the wire.
    """
    return f"{item_id}:{summary_index}"


def _wire_item_to_dict(item: Any) -> dict[str, Any]:
    """Plain-dict form of an output item (SDK object or raw dict)."""
    if isinstance(item, BaseModel):
        return item.model_dump(exclude_none=True)
    return item


def _parse_stream_event(raw_event: Any) -> GatewayEvent | None:
    """Parse one raw SDK event into a typed :data:`GatewayEvent`, or ``None``
    for a wire event this gateway absorbs rather than yields (unmapped types,
    non-function-call output items — grow the union in this module before
    widening what's returned here, not by loosening this to raw passthrough).
    """
    event_type = _TypedEvent.model_validate(raw_event).type

    if event_type == "response.output_text.delta":
        return TextDelta(text=_TextDeltaEvent.model_validate(raw_event).delta)

    if event_type == "response.reasoning_summary_text.delta":
        delta = _ReasoningSummaryDeltaEvent.model_validate(raw_event)
        return ReasoningSummaryDelta(
            block=_reasoning_block(delta.item_id, delta.summary_index),
            text=delta.delta,
        )

    if event_type == "response.reasoning_summary_text.done":
        done = _ReasoningSummaryDoneEvent.model_validate(raw_event)
        return ReasoningSummaryDone(
            block=_reasoning_block(done.item_id, done.summary_index),
            text=done.text,
        )

    if event_type == "response.output_item.done":
        item = _OutputItemDoneEvent.model_validate(raw_event).item
        if item is not None and item.type == "apply_patch_call":
            if item.operation is None:
                raise GatewayError("apply_patch_call item without an operation")
            return ApplyPatchCall(
                call_id=item.call_id, operation=_wire_item_to_dict(item.operation)
            )
        if item is None or item.type != "function_call":
            return None
        try:
            arguments = json.loads(item.arguments) if item.arguments else {}
        except json.JSONDecodeError as exc:
            raise GatewayError(f"Malformed function-call arguments: {exc}") from exc
        return FunctionCall(call_id=item.call_id, name=item.name, arguments=arguments)

    return None


class ResponsesGateway(Protocol):
    """Raw access to the OpenAI Responses API. The seam a host decorates."""

    total_input_tokens: int
    total_output_tokens: int
    # Nested inside the two above, not disjoint from them: cache-read input is
    # part of input, reasoning output is part of output. Carried separately
    # because providers bill them at different rates, so a cache-hit collapse
    # or a reasoning-effort change is invisible in the totals alone.
    total_cached_input_tokens: int
    total_reasoning_output_tokens: int
    calls: int

    def create(self, request: ResponsesRequest) -> CompletedResponse: ...
    def stream(self, request: ResponsesRequest) -> Iterator[GatewayEvent]: ...


class OpenAIGateway:
    """The only real implementation of :class:`ResponsesGateway`.

    Owns the SDK object and nothing else: construction, timeouts/retries,
    error translation, and usage/early-stop accounting. Model-agnostic —
    ``model`` is a request field supplied by the caller, so one gateway
    instance serves every model called against one credential.
    """

    def __init__(
        self,
        api_key: str | None = None,
        read_timeout_seconds: float | None = None,
        max_retries: int | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        if http_client is not None and http_client.timeout == httpx.Timeout(5.0):
            # openai/_base_client.py compares an incoming http_client's timeout
            # against HTTPX_DEFAULT_TIMEOUT and, on a match, silently discards
            # it for the SDK's own 600s default — catch that at construction,
            # not the first stalled call.
            raise ValueError(
                "instrumented http_client must carry an explicit timeout: the "
                "OpenAI SDK silently replaces httpx's default-shaped timeout "
                "with its own 600s default"
            )
        # None means the shared _llm_timeout() budget and the SDK's own retry
        # default. A caller overrides both only when the defaults are wrong for
        # where it runs — a request-path call must fail fast rather than make a
        # user wait out a stalled provider, and the timeout alone cannot do that:
        # it is per-attempt, so retries multiply it.
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._read_timeout_seconds = read_timeout_seconds
        self._max_retries = max_retries
        # An httpx.Client the host owns (timeout included); lets a host
        # observe wire traffic without this module knowing how.
        self._http_client = http_client
        self._client: Any = None
        # Cumulative token usage + call count across every call this gateway
        # makes. Callers that need per-case cost/calls attribution read
        # these directly off the client's `.gateway`: a fresh gateway per
        # case = that case's usage; a shared one = diff before/after.
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cached_input_tokens = 0
        self.total_reasoning_output_tokens = 0
        self.calls = 0

    def _accumulate_usage(self, usage: TokenUsage | None) -> None:
        """Add a Responses-API usage payload to the running token totals.

        The two detail counters are read without an ``or 0`` coercion: a model
        that omits the block — or reports the count as null — is reporting
        nothing, not reporting zero, and a fabricated 0 reads downstream as a
        total cache miss. Guarding the resolved value rather than the field
        default is what covers the null case; a default on the field alone
        leaves ``int += None`` live. The two totals above keep theirs —
        changing what the shipped ``input_tokens`` / ``output_tokens``
        columns mean is a separate decision.
        """
        if usage is None:
            return
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        if (
            usage.input_tokens_details
            and usage.input_tokens_details.cached_tokens is not None
        ):
            self.total_cached_input_tokens += usage.input_tokens_details.cached_tokens
        if (
            usage.output_tokens_details
            and usage.output_tokens_details.reasoning_tokens is not None
        ):
            self.total_reasoning_output_tokens += (
                usage.output_tokens_details.reasoning_tokens
            )

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import openai
                from openai import OpenAI
            except ImportError as exc:
                # Not routed through _install_hint: that helper only builds
                # `<installer> "dbt-charts[...]"` strings, and openai is a
                # bring-your-own dependency here, not a dbt charts extra.
                raise RuntimeError(
                    "OpenAI client is not installed. Install with: pip install openai"
                ) from exc
            # Branch rather than splat a kwargs dict: `max_retries` has an SDK
            # default we don't want to restate, and `**dict` erases the arg
            # types for the type checker.
            if self._http_client is not None:
                # The http_client is the sole timeout authority here — no
                # SDK-level `timeout=` kwarg. __init__ already refused a
                # default-shaped http_client timeout, so there is nothing
                # left for the SDK's own 600s default to silently win.
                if self._max_retries is None:
                    self._client = OpenAI(
                        api_key=self._api_key, http_client=self._http_client
                    )
                else:
                    self._client = OpenAI(
                        api_key=self._api_key,
                        http_client=self._http_client,
                        max_retries=self._max_retries,
                    )
            else:
                timeout = (
                    _llm_timeout()
                    if self._read_timeout_seconds is None
                    else httpx.Timeout(
                        self._read_timeout_seconds, connect=CONNECT_TIMEOUT_SECONDS
                    )
                )
                # openai re-exports its own Timeout at the top level, aliased
                # to whichever httpx-shaped class its own constructor
                # actually wants (recent SDK majors vendor a private copy
                # under a different name). Building through that alias
                # instead of httpx.Timeout directly keeps this correct
                # across majors without this module having to know which one
                # is installed.
                openai_timeout = openai.Timeout(**timeout.as_dict())
                if self._max_retries is None:
                    self._client = OpenAI(api_key=self._api_key, timeout=openai_timeout)
                else:
                    self._client = OpenAI(
                        api_key=self._api_key,
                        timeout=openai_timeout,
                        max_retries=self._max_retries,
                    )
        return self._client

    def _open(self, request: ResponsesRequest) -> Any:
        """Open one Responses API call (streaming or not) and translate the
        open-phase error. Shared by create() and stream() — call counting and
        error translation are written once, here."""
        self.calls += 1
        started = time.monotonic()
        kwargs = request.model_dump(exclude_none=True)
        try:
            return self.client.responses.create(**kwargs)
        except Exception as exc:
            logger.warning(
                "llm_open_error model=%s stream=%s elapsed=%.2fs error=%s",
                request.model,
                request.stream,
                time.monotonic() - started,
                exc.__class__.__name__,
            )
            if _is_openai_api_error(exc):
                raise GatewayError(str(exc)) from exc
            raise

    def create(self, request: ResponsesRequest) -> CompletedResponse:
        """Non-streaming Responses API call with unified error handling."""
        resp = self._open(request)
        try:
            payload = _CreateResponsePayload.model_validate(resp)
        except ValidationError as exc:
            raise GatewayError(str(exc)) from exc
        self._accumulate_usage(payload.usage)
        return CompletedResponse(output_text=payload.output_text)

    def stream(self, request: ResponsesRequest) -> Iterator[GatewayEvent]:
        """Open a streaming Responses API call and yield typed events.

        Usage is extracted from ``response.completed`` and early-stop events
        as a side effect; an early-stop event raises :class:`GatewayError`
        (after counting its usage) instead of being yielded — ending the
        generator here instead would look identical to a finished turn to a
        caller that doesn't re-derive early-stop detection itself.
        """
        stream = self._open(request)
        logger.info("llm_stream_begin model=%s", request.model)
        started = time.monotonic()
        event_count = 0
        try:
            for raw_event in stream:
                event_count += 1
                event_type = _TypedEvent.model_validate(raw_event).type

                if event_type in EARLY_STOP_EVENTS:
                    # Incomplete and failed responses carry usage just as
                    # completed ones do, and a turn cut at the output limit is
                    # the most expensive kind there is — so count it before
                    # raising, or cost attribution silently omits it.
                    reason, usage = _early_stop(event_type, raw_event)
                    self._accumulate_usage(usage)
                    raise GatewayError(f"The model stopped early: {reason}")

                if event_type == "response.completed":
                    completed = _CompletedEvent.model_validate(raw_event)
                    if completed.response is None:
                        continue
                    self._accumulate_usage(completed.response.usage)
                    reasoning = [
                        _wire_item_to_dict(item)
                        for item in completed.response.output
                        if _OutputItemType.model_validate(item).type == "reasoning"
                    ]
                    if reasoning:
                        yield ReasoningItems(items=reasoning)
                    continue

                parsed = _parse_stream_event(raw_event)
                if parsed is not None:
                    yield parsed
        except Exception as exc:
            logger.warning(
                "llm_stream_error phase=read model=%s events=%d elapsed=%.2fs error=%s",
                request.model,
                event_count,
                time.monotonic() - started,
                exc.__class__.__name__,
            )
            if _is_wire_error(exc):
                raise GatewayError(str(exc)) from exc
            raise

        logger.info(
            "llm_stream_end model=%s events=%d elapsed=%.2fs",
            request.model,
            event_count,
            time.monotonic() - started,
        )
