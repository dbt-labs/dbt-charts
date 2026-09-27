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
from dbt_charts.agent_api.describe_query import DescribeQueryArgs
from dbt_charts.agent_api.diagnostics import (
    GetDiagnosticCodeArgs,
    ListDiagnosticCodesArgs,
)
from dbt_charts.agent_api.docs import DocsArgs
from dbt_charts.agent_api.files import (
    DeleteFileArgs,
    EditFileArgs,
    GlobFilesArgs,
    GrepFilesArgs,
    MoveFileArgs,
    ReadFileArgs,
    WriteFileArgs,
)
from dbt_charts.agent_api.query import ExecuteQueryArgs, QueryBoardArgs
from dbt_charts.agent_api.search import SearchBoardsArgs
from dbt_charts.agent_api.skills import GetSkillArgs, SearchSkillsArgs
from dbt_charts.agent_api.validate import ValidateBoardArgs


def _ai_tool(name: str, model: type[BaseModel]) -> dict[str, Any]:
    desc = textwrap.dedent(model.__doc__ or "").strip()
    return {
        "name": name,
        "description": desc,
        "input_schema": model.model_json_schema(by_alias=True),
    }


VALIDATE_BOARD = _ai_tool("validate_board", ValidateBoardArgs)
RENDER_BOARD = _ai_tool("render_board", RenderBoardArgs)
EXECUTE_QUERY = _ai_tool("execute_query", ExecuteQueryArgs)
DESCRIBE_QUERY = _ai_tool("describe_query", DescribeQueryArgs)
SEARCH_BOARDS = _ai_tool("search_boards", SearchBoardsArgs)

DOCS = _ai_tool("docs", DocsArgs)

LIST_DIAGNOSTIC_CODES = _ai_tool("list_diagnostic_codes", ListDiagnosticCodesArgs)
GET_DIAGNOSTIC_CODE = _ai_tool("get_diagnostic_code", GetDiagnosticCodeArgs)

QUERY_BOARD = _ai_tool("query_board", QueryBoardArgs)

DESCRIBE_BOARD = _ai_tool("describe_board", DescribeBoardArgs)

LIST_SKILLS = {
    "name": "list_skills",
    "description": (
        "List all agent skills packaged with dct. "
        "Returns name, description, kind (workflow or pattern), and has_examples "
        "for each skill. Call this first to discover what patterns are available, "
        "then use get_skill to read the full guide for the pattern you want, "
        "or search_skills to filter by keyword."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

GET_SKILL = _ai_tool("get_skill", GetSkillArgs)
SEARCH_SKILLS = _ai_tool("search_skills", SearchSkillsArgs)
READ_FILE = _ai_tool("read_file", ReadFileArgs)
WRITE_FILE = _ai_tool("write_file", WriteFileArgs)
EDIT_FILE = _ai_tool("edit_file", EditFileArgs)
GLOB_FILES = _ai_tool("glob_files", GlobFilesArgs)
GREP_FILES = _ai_tool("grep_files", GrepFilesArgs)
MOVE_FILE = _ai_tool("move_file", MoveFileArgs)
DELETE_FILE = _ai_tool("delete_file", DeleteFileArgs)

ALL_TOOLS: list[dict[str, Any]] = [
    VALIDATE_BOARD,
    RENDER_BOARD,
    EXECUTE_QUERY,
    DESCRIBE_QUERY,
    SEARCH_BOARDS,
    DOCS,
    QUERY_BOARD,
    DESCRIBE_BOARD,
    LIST_SKILLS,
    GET_SKILL,
    SEARCH_SKILLS,
    LIST_DIAGNOSTIC_CODES,
    GET_DIAGNOSTIC_CODE,
]


# General project-file primitives. Deliberately NOT in ALL_TOOLS: the MCP
# surface is for external assistants (Claude Code, Cursor) that already have
# their own file tools — duplicating them there is noise. These belong to the
# in-process agent loop, which has no other way to read/write the project.
# AGENT_TOOLS is that loop's tool list.
FILE_TOOLS: list[dict[str, Any]] = [
    READ_FILE,
    WRITE_FILE,
    EDIT_FILE,
    GLOB_FILES,
    GREP_FILES,
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
SUGGEST_BOARD_PLACEMENT = _ai_tool("suggest_board_placement", SuggestBoardPlacementArgs)

AGENT_TOOLS: list[dict[str, Any]] = ALL_TOOLS + FILE_TOOLS


def restrict_enum(
    tool: dict[str, Any], property: str, allowed: list[str]
) -> dict[str, Any]:
    """A canonical tool definition with *property*'s enum values narrowed to
    *allowed* — for a host that may offer a property but not every value it
    accepts. A lIe offers ``render_board.format`` but excludes ``svg``: its
    own ``routes.py`` renders the SVG, and offering the model the ``svg``
    value makes it paste raw SVG into its narrative instead of clean prose.

    Only handles the ``anyOf: [{enum: [...]}, {type: null}]`` shape pydantic
    emits for an optional ``Literal`` field — the one shape this repo's tool
    schemas produce for such fields today.
    """
    schema = tool["input_schema"]
    prop_schema = schema["properties"].get(property)
    if prop_schema is None:
        raise ValueError(f"{tool['name']} has no property {property!r}")
    enum_branches = [b for b in prop_schema.get("anyOf", []) if "enum" in b]
    if len(enum_branches) != 1:
        raise ValueError(
            f"{tool['name']}.{property} has no single enum branch to restrict"
        )
    (enum_branch,) = enum_branches
    unknown = [v for v in allowed if v not in enum_branch["enum"]]
    if unknown:
        raise ValueError(
            f"{unknown} not a valid value for {tool['name']}.{property} "
            f"(allowed: {enum_branch['enum']})"
        )
    narrowed_branch = {**enum_branch, "enum": allowed}
    narrowed_any_of = [
        narrowed_branch if b is enum_branch else b for b in prop_schema["anyOf"]
    ]
    return {
        **tool,
        "input_schema": {
            **schema,
            "properties": {
                **schema["properties"],
                property: {**prop_schema, "anyOf": narrowed_any_of},
            },
        },
    }


def subset_tool(tool: dict[str, Any], properties: list[str]) -> dict[str, Any]:
    """A canonical tool definition narrowed to *properties*.

    For a host that must not advertise a parameter — A lIe hides
    ``render_board.format`` because offering ``svg`` makes the model paste raw
    SVG into its prose. Canonical shape in, canonical shape out, so the result
    still goes through ``normalize_openai_tools`` like every other tool.
    """
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
