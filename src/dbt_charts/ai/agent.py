"""Provider-neutral agent loop for the terminal agent."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dbt_charts.ai.context import DbtChartsAIContext
from dbt_charts.ai.events import (
    AGENT_ERROR_MESSAGE,
    AgentDone,
    AgentError,
    AgentEvent,
    ContentDelta,
    ToolCallEvent,
    ToolResultEvent,
)
from dbt_charts.ai.failures import AITurnFailure
from dbt_charts.ai.llm import LLMClient, LLMClientError, classify
from dbt_charts.ai.messages import (
    AgentMessage,
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from dbt_charts.ai.prompts import (
    build_docs_pointer,
    build_skills_index,
    load_project_instructions,
)
from dbt_charts.ai.tool_schemas import AGENT_TOOLS
from dbt_charts.ai.tools import ToolHandler, dispatch_tool_call, tool_call_outcome

if TYPE_CHECKING:
    from dbt_charts.core.execute.adapters import AdapterRegistry

logger = logging.getLogger(__name__)

# Detection tripwire (Dave, 2026-07-23): a final response with no structured
# tool_calls can still contain the model writing a tool call as prose instead
# of using the protocol — this is model-side and industry-wide (LiteLLM
# #28978, DeepSeek #1244), not something our converter can prevent outright.
# We never scrub or rewrite the text — silently "fixing" what the user sees
# would hide a real model regression — we alarm instead.
_LEAKED_TOOL_ENVELOPE_RE = re.compile(r"^\s*functions\.[\w.]+\s*\n\{", re.MULTILINE)

#: Process-lifetime count of leaked tool envelopes detected in final
#: responses. Hosts (e.g. Cloud) may read/export this into their own metrics.
leaked_tool_envelope_total = 0


def _check_for_leaked_tool_envelope(response_text: str) -> None:
    global leaked_tool_envelope_total
    if _LEAKED_TOOL_ENVELOPE_RE.search(response_text):
        leaked_tool_envelope_total += 1
        logger.warning(
            "leaked_tool_envelope_detected response_preview=%r",
            response_text[:200],
        )


# Dialect-specific metadata-view examples for the schema exploration
# guidance. Dialects not listed get the generic INFORMATION_SCHEMA example.
_DIALECT_METADATA_HINTS = {
    "bigquery": (
        "- BigQuery: qualify per dataset — "
        "`SELECT table_name, column_name, data_type FROM "
        "<dataset>.INFORMATION_SCHEMA.COLUMNS`; list datasets with "
        "`SELECT schema_name FROM INFORMATION_SCHEMA.SCHEMATA`."
    ),
    "duckdb": (
        "- DuckDB: `SHOW ALL TABLES`, `DESCRIBE <table>`, or standard "
        "INFORMATION_SCHEMA views."
    ),
    "sqlite": (
        "- SQLite: `SELECT name FROM sqlite_master WHERE type = 'table'`, "
        "then `PRAGMA table_info(<table>)`."
    ),
}
_GENERIC_METADATA_HINT = (
    "- `SELECT table_schema, table_name FROM INFORMATION_SCHEMA.TABLES`, then "
    "`SELECT column_name, data_type FROM INFORMATION_SCHEMA.COLUMNS "
    "WHERE table_schema = '<schema>' AND table_name = '<table>'`."
)

_SCHEMA_EXPLORATION_GUIDANCE = """Explore schema yourself with `execute_query` \
against the warehouse's own metadata views — there is no separate schema tool. \
Keep metadata queries scoped (filter by schema/table) instead of dumping every \
column in the warehouse:

{hints}

Never invent columns or assume a schema you have not read: check a table's \
columns before writing a query against it."""


def schema_exploration_guidance(adapter_registry: AdapterRegistry) -> str:
    """Teach the agent to explore schema via its own metadata-view SQL.

    Reads only registered connection config (source types) — never the
    warehouse. Returns "" when no sources are configured (nothing to explore).
    """
    types = {s["type"] for s in adapter_registry.list_sql_sources()}
    if not types:
        return ""
    hints = [
        _DIALECT_METADATA_HINTS[t]
        for t in sorted(types & _DIALECT_METADATA_HINTS.keys())
    ]
    if types - _DIALECT_METADATA_HINTS.keys():
        hints.insert(0, _GENERIC_METADATA_HINT)
    return _SCHEMA_EXPLORATION_GUIDANCE.format(hints="\n".join(hints))


def build_sources_context(adapter_registry: AdapterRegistry) -> str:
    """List configured data sources + exploration guidance, from config only.

    The system prompt deliberately embeds no warehouse schema — on large
    warehouses a schema dump exceeds the model context window, and agents
    retrieve schema better through their own scoped metadata queries.
    """
    sources = adapter_registry.list_sql_sources()
    if not sources:
        return ""
    lines = [f"- {s['name']} ({s['type']})" for s in sources]
    return (
        "## Data sources (use these exact names in `source:` and queries)\n\n"
        + "\n".join(lines)
        + "\n\n"
        + schema_exploration_guidance(adapter_registry)
    )


_TOOL_GUIDANCE = """## Tool Use

- Source names in `execute_query` are the project-configured names listed under
  Data sources (e.g. `db`), NOT adapter types (`duckdb`, `postgres`). Inspect
  real data before making any claim — never invent columns or assume a schema
  you have not read.
- Use `search_boards` and `read_file`/`glob_files`/`grep_files` to reuse
  existing boards and project patterns.
- To just preview a chart for the user, render it directly with
  `render_board(yaml_content=...)` — no file needed.
- To create or change a *saved* dashboard, write the YAML to a file: `write_file`
  for a new board, `edit_file` for a targeted change to a board the user named
  (read it first so you don't clobber it). Never silently append a chart to an
  existing board the user did not name — write a new board instead. Boards live
  under `charts/`. Then run `validate_board` and fix every error before
  continuing.
- Once a saved board validates, call `render_board(path=...)` to check it
  renders, and surface the preview URL (`render_board(path=..., as_link=true)`
  returns it) so they can open the dashboard in their browser. The preview
  server is already running — never try to start one.
- If a tool returns an error, explain it clearly and fix it — do not proceed on
  assumptions or hide it.
- If the user asks for a chart shape dbt charts has no chart family for (funnel,
  gauge, sunburst, chord, and others — check `docs(topic="charts")` when
  unsure), never silently build a different shape and label it with the
  requested name. Say plainly that dbt charts can't draw that shape, name what
  you built instead, and let the user decide whether to proceed. An honest
  substitution is fine; an undisclosed one is not.
- When you save a board, tell the user the saved file path and the preview URL.
"""


def build_agent_system_prompt(context: DbtChartsAIContext) -> str:
    """Build the terminal agent system prompt from shared skills and source config."""
    parts = [
        "Write in American English.",
        build_skills_index(),
        build_docs_pointer(),
        build_sources_context(context.project_session.adapter_registry),
    ]

    project_instructions = load_project_instructions(context.project_session.project)
    if project_instructions:
        parts.append(project_instructions)

    # _TOOL_GUIDANCE is OUR policy — it must stay last so it is higher-precedence
    # than anything project-authored above it (see load_project_instructions'
    # docstring for the trust model this ordering enforces).
    parts.append(_TOOL_GUIDANCE)
    return "\n\n---\n\n".join(part for part in parts if part)


@dataclass(frozen=True)
class AgentProfile:
    """A named scoping of the agent loop: which tools + which system prompt.

    The agent is the loop (`run_agent`) plus three things that vary by use case:
    the tool surface, the system prompt, and a name. A profile bundles them so
    each consumer (the dashboard analyst, the BIRD SQL-specialist, experiments)
    is a declared profile rather than an ad-hoc pile of arguments.

    ``build_system_prompt`` is a builder, not a string, because the prompt is
    assembled from live context (schema, project instructions) per invocation.
    A profile with a fixed prompt can pass ``lambda _ctx: text``.
    """

    name: str
    tools: list[dict[str, Any]]
    build_system_prompt: Callable[[DbtChartsAIContext], str]
    # Per-profile override for tool names the shared registry already handles
    # (e.g. Cloud routing move_file/delete_file to a governed service instead
    # of the generic local file-tool handler) — consulted before TOOL_HANDLERS
    # in dispatch_tool_call — one tool name and contract across hosts, two
    # backends. None for profiles with no host-specific behavior.
    tool_overrides: dict[str, ToolHandler] | None = None


# run_agent's default when no profile is supplied: the full tool surface plus
# the general dashboard-analyst prompt. Embedding hosts supply their own
# instead — Cloud via build_cloud_agent_profile, Playground via
# PLAYGROUND_AGENT_PROFILE — so this is the unscoped baseline, not a
# host-specific configuration.
DASHBOARD_PROFILE = AgentProfile(
    name="dashboard-analyst",
    tools=AGENT_TOOLS,
    build_system_prompt=build_agent_system_prompt,
)


def _elide_if_svg(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith("<svg"):
        return f"[svg omitted ({len(value)} chars) — rendered for the user]"
    return value


def _model_facing_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return the copy of a tool result the model reads.

    SVG output is a display artifact for the host (Cloud embeds it; the CLI
    never requests it) — feeding ~100KB of markup to the model wastes context
    and invites it to echo the blob as prose. The model keeps the verdict
    (status, warnings, errors); the host-facing ToolResultEvent keeps the
    full payload.

    Elision applies to every top-level key, not just ``data`` — a host
    ``tool_overrides`` handler (e.g. Cloud's render_board merge) may carry
    pixel SVG under a second key while ``data`` holds the agent's requested
    semantic payload. A key-specific check would let that second key leak the
    blob straight into model context.
    """
    return {key: _elide_if_svg(value) for key, value in result.items()}


def run_agent(
    prompt: str | list[dict[str, Any]],
    *,
    client: LLMClient,
    context: DbtChartsAIContext,
    messages: list[AgentMessage] | None = None,
    max_iterations: int = 25,
    max_identical_calls: int = 3,
    profile: AgentProfile | None = None,
    tools: list[dict[str, Any]] | None = None,
    extra_handlers: dict[str, ToolHandler] | None = None,
) -> Generator[AgentEvent, None, None]:
    """Run the agent loop and stream typed events.

    Args:
        max_identical_calls: Consecutive iterations issuing an identical tool-call
            batch (same names and arguments) before the loop errors instead of
            dispatching again; any different call resets the count.
        profile: An `AgentProfile` supplying the tool surface + system prompt. The
            way to scope the agent (dashboard vs SQL-specialist vs an experiment).
            Defaults to the dashboard analyst.
        tools: Override the profile's tools for this call. Playground uses it
            to gate the surface per turn (its full set, or [] for a
            tools-disabled request) and mirrors the choice into the prompt
            context. Hosts with a fixed surface bake it into the profile
            instead. Defaults to the profile's tools.
        extra_handlers: Per-call handlers for tools with no meaning to dbt_charts.core
            (e.g. Cloud's placement tool) — threaded straight to dispatch_tool_call.
    """
    active = profile if profile is not None else DASHBOARD_PROFILE
    effective_tools = tools if tools is not None else active.tools
    conversation = messages if messages is not None else []
    conversation.append(UserMessage(content=prompt))
    system_prompt = active.build_system_prompt(context)

    last_call_key: tuple[tuple[str, str], ...] | None = None
    identical_count = 0

    for _ in range(max_iterations):
        response_text = ""
        tool_calls: list[ToolCallEvent] = []

        try:
            for event in client.stream_with_tools(
                messages=conversation,
                system_prompt=system_prompt,
                tools=effective_tools,
            ):
                if isinstance(event, ContentDelta):
                    response_text += event.delta
                elif isinstance(event, ToolCallEvent):
                    tool_calls.append(event)
                yield event
        except LLMClientError as exc:
            logger.exception("agent_llm_error")
            yield AgentError(
                message=AGENT_ERROR_MESSAGE,
                reason=classify(exc),
                details=str(exc),
            )
            return

        conversation.append(
            AssistantMessage(
                content=response_text,
                tool_calls=[
                    ToolCall(id=tc.id, name=tc.name, arguments=tc.arguments)
                    for tc in tool_calls
                ],
            )
        )

        if not tool_calls:
            _check_for_leaked_tool_envelope(response_text)
            yield AgentDone(response=response_text)
            return

        call_key = tuple(
            (tc.name, json.dumps(tc.arguments, sort_keys=True)) for tc in tool_calls
        )
        if call_key == last_call_key:
            identical_count += 1
        else:
            identical_count = 1
            last_call_key = call_key

        if identical_count >= max_identical_calls:
            names = ", ".join(dict.fromkeys(tc.name for tc in tool_calls))
            yield AgentError(
                message=(
                    f"Stopped: {names} called with identical arguments "
                    f"{identical_count} times in a row without making progress."
                ),
                # Set literally: this terminal raises nothing, so there is no
                # exception for classify() to read.
                reason=AITurnFailure.LOOP_DETECTED,
            )
            return

        for tc in tool_calls:
            start = time.monotonic()
            result = dispatch_tool_call(
                tc.name,
                tc.arguments,
                context=context,
                extra_handlers=extra_handlers,
                tool_overrides=active.tool_overrides,
            )
            duration_s = time.monotonic() - start
            conversation.append(
                ToolResultMessage(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=json.dumps(_model_facing_result(result), default=str),
                )
            )
            yield ToolResultEvent(
                id=tc.id,
                name=tc.name,
                result=result,
                duration_s=duration_s,
                outcome=tool_call_outcome(result),
            )

    yield AgentError(
        message=(
            f"Reached the {max_iterations}-step limit before finishing. Any files "
            "written so far are saved — open the preview server URL to view them, "
            "or send another message to continue."
        ),
        # Set literally, same as the loop terminal above: falling out of the
        # step budget is a return, not a raise.
        reason=AITurnFailure.STEP_LIMIT_EXCEEDED,
    )
