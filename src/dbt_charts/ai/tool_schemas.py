"""Canonical tool definitions for dbt charts AI interfaces.

Single source of truth for tool names, descriptions, and input schemas.
Consumed by both the MCP server and OpenAI function-calling wrappers.
"""

from __future__ import annotations

import textwrap
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.agent_api.boards import RenderBoardArgs
from dbt_charts.agent_api.describe import DescribeBoardArgs
from dbt_charts.agent_api.diagnostics import (
    GetDiagnosticCodeArgs,
    ListDiagnosticCodesArgs,
)
from dbt_charts.agent_api.files import (
    DeleteFileArgs,
    MoveFileArgs,
    ReadFileArgs,
    WriteFileArgs,
)
from dbt_charts.agent_api.query import ExecuteQueryArgs
from dbt_charts.agent_api.search import SearchFilesArgs
from dbt_charts.agent_api.skills import GetSkillArgs, ListSkillsArgs


def _annotations(
    title: str,
    *,
    read_only: bool = True,
    destructive: bool = False,
    open_world: bool = False,
) -> dict[str, Any]:
    """MCP ToolAnnotations: hosts auto-approve on ``readOnlyHint``, and Claude's
    connector directory requires a title plus a read-only or destructive hint.

    ``execute_sql`` counts as read-only: its SQL runs under a read-only guard.
    ``open_world`` marks tools that run a board's http sources, which can reach
    arbitrary URLs (GET only under a read-only session).
    """
    return {
        "title": title,
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
        "idempotentHint": not destructive,
        "openWorldHint": open_world,
    }


_WRITES = {"read_only": False, "destructive": True}


def _ai_tool(
    name: str,
    model: type[BaseModel],
    title: str,
    *,
    read_only: bool = True,
    destructive: bool = False,
    open_world: bool = False,
) -> dict[str, Any]:
    desc = textwrap.dedent(model.__doc__ or "").strip()
    return {
        "name": name,
        "description": desc,
        "annotations": _annotations(
            title,
            read_only=read_only,
            destructive=destructive,
            open_world=open_world,
        ),
        "input_schema": model.model_json_schema(by_alias=True),
    }


def subset_tool(tool: dict[str, Any], properties: list[str]) -> dict[str, Any]:
    """*tool* narrowed to *properties* (drop-only)."""
    schema = tool["input_schema"]
    unknown = [p for p in properties if p not in schema["properties"]]
    if unknown:
        raise ValueError(f"{tool['name']} has no properties {unknown}")
    narrowed = {
        **schema,
        "properties": {
            k: v for k, v in schema["properties"].items() if k in properties
        },
    }
    if "required" in schema:
        narrowed["required"] = [r for r in schema["required"] if r in properties]
    return {**tool, "input_schema": narrowed}


#: Appended to render_board's agent-facing description -- the default text
#: summary already carries these, so a model rarely needs anything past it.
_SUMMARY_CONTENTS_NOTE = (
    " The response already includes row counts, each field's value range, "
    "category values, KPI values, and a preview of table rows."
)


#: Shared across every agent-facing render_board schema, including Cloud
#: MCP's own hand-written inputSchema (a different wire shape, same words) --
#: never hand-copy this description elsewhere.
INCLUDE_RAW_DATA_PROPERTY: dict[str, Any] = {
    "type": "boolean",
    "description": (
        "Also return the raw query rows. Large; the summary already has "
        "counts, ranges, categories, KPI values and table previews. Set "
        "true only when you need a specific value the summary lacks."
    ),
}


def _render_board_tool() -> dict[str, Any]:
    """render_board's agent-facing shape: renders always return the text
    summary, with 'include_raw_data' appending full rows in place of 'format'.

    ``additionalProperties: false`` is set explicitly (not left to
    ``normalize_openai_tools``'s ``setdefault``) because dct's local MCP
    server serves this schema straight to the MCP SDK, with no normalize
    pass -- without it, a caller could send 'format' anyway and get it
    silently honored.
    """
    tool = _ai_tool("render_board", RenderBoardArgs, "Render board", open_world=True)
    schema = tool["input_schema"]
    properties = {k: v for k, v in schema["properties"].items() if k != "format"}
    properties["include_raw_data"] = INCLUDE_RAW_DATA_PROPERTY
    return {
        **tool,
        "description": tool["description"] + _SUMMARY_CONTENTS_NOTE,
        "input_schema": {
            **schema,
            "properties": properties,
            "required": [r for r in schema.get("required", []) if r != "format"],
            "additionalProperties": False,
        },
    }


RENDER_BOARD = _render_board_tool()
EXECUTE_SQL = _ai_tool("execute_sql", ExecuteQueryArgs, "Run query", open_world=True)
SEARCH_FILES = _ai_tool("search_files", SearchFilesArgs, "Search files")

LIST_DIAGNOSTIC_CODES = _ai_tool(
    "list_diagnostic_codes", ListDiagnosticCodesArgs, "List diagnostic codes"
)
GET_DIAGNOSTIC_CODE = _ai_tool(
    "get_diagnostic_code", GetDiagnosticCodeArgs, "Get diagnostic code"
)

DESCRIBE_BOARD = _ai_tool("describe_board", DescribeBoardArgs, "Describe board")

LIST_SKILLS = _ai_tool("list_skills", ListSkillsArgs, "List skills")

GET_SKILL = _ai_tool("get_skill", GetSkillArgs, "Get skill")
READ_FILE = _ai_tool("read_file", ReadFileArgs, "Read file")
WRITE_FILE = _ai_tool("write_file", WriteFileArgs, "Write file", **_WRITES)
MOVE_FILE = _ai_tool("move_file", MoveFileArgs, "Move file", **_WRITES)
DELETE_FILE = _ai_tool("delete_file", DeleteFileArgs, "Delete file", **_WRITES)

ALL_TOOLS: list[dict[str, Any]] = [
    RENDER_BOARD,
    EXECUTE_SQL,
    SEARCH_FILES,
    DESCRIBE_BOARD,
    LIST_SKILLS,
    GET_SKILL,
    LIST_DIAGNOSTIC_CODES,
    GET_DIAGNOSTIC_CODE,
]


# General project-file primitives. Deliberately NOT in ALL_TOOLS: the MCP
# surface is for external assistants (Claude Code, Cursor) that already have
# their own file tools — duplicating them there is noise. These belong to the
# in-process agent loop, which has no other way to read/write the project.
# AGENT_TOOLS is that loop's tool list.
# Saved boards are changed with the Responses API's built-in apply_patch tool,
# the editing tool OpenAI models are trained on; it has no schema of ours, so
# normalize_openai_tools passes it through unchanged.
APPLY_PATCH_NAME = "apply_patch"
APPLY_PATCH: dict[str, Any] = {"type": APPLY_PATCH_NAME}


def tool_name(tool: dict[str, Any]) -> str:
    """A tool entry's name: ours carry ``name``, a built-in is identified by its
    ``type``."""
    return tool["name"] if "name" in tool else tool["type"]


FILE_TOOLS: list[dict[str, Any]] = [
    READ_FILE,
    WRITE_FILE,
    MOVE_FILE,
    DELETE_FILE,
]


class SuggestBoardPlacementArgs(BaseModel):
    """Propose a destination folder for the draft dashboard being worked on, so
    Cloud's promote picker can prefill it. Returns the caller's own writable
    folders — the suggestion is advisory only; the promote action re-checks
    permission against the chosen destination regardless of what is proposed
    here."""

    model_config = ConfigDict(extra="forbid")

    draft_slug: str = Field(
        ..., description="The draft's slug within the current session, e.g. 'revenue'"
    )
    suggested_destination: str = Field(
        ...,
        description="Proposed destination path prefix, e.g. 'finance/' or '' "
        "for the project root",
    )
    rationale: str = Field(
        "", description="One-line explanation for the suggestion, shown to the user"
    )


# Cloud-only: needs Cloud/Django state (writable_prefixes over org bindings) that
# dbt_charts.agent_api may never import (agent_api thin-wrapper rule). Deliberately
# absent from ALL_TOOLS (no MCP meaning) and AGENT_TOOLS (drafts/promotion is a
# Cloud concept — confirmed no _drafts/ handling exists outside Cloud).
# Cloud's own agent profile appends this to AGENT_TOOLS.
# It upserts an advisory row, so it is not read-only, but never destructive.
SUGGEST_BOARD_PLACEMENT = _ai_tool(
    "suggest_board_placement",
    SuggestBoardPlacementArgs,
    "Suggest board placement",
    read_only=False,
)

AGENT_TOOLS: list[dict[str, Any]] = [*ALL_TOOLS, *FILE_TOOLS, APPLY_PATCH]
