"""Tests for dbt_charts.ai.tool_schemas — the canonical tool-definition surface."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.ai.tool_schemas import (
    AGENT_TOOLS,
    ALL_TOOLS,
    DELETE_FILE,
    EXECUTE_QUERY,
    FILE_TOOLS,
    MOVE_FILE,
    QUERY_BOARD,
    RENDER_BOARD,
    SUGGEST_BOARD_PLACEMENT,
    subset_tool,
)

from .conftest import strict_mode_violations


class TestOpenAIStrictModeValidity:
    """Schemas emitted by normalize_openai_tools must satisfy all three
    OpenAI strict-mode rules: additionalProperties: false on every object,
    required covering all properties, no default: null keys.

    Tests assert on the *output* of normalize_openai_tools (the actual bytes
    sent to the API), not the raw input_schema, so the transformer itself is
    under test — a future regression in either the transformer or a new tool
    fails here before reaching a live API call.
    """

    def test_all_normalized_tools_pass_openai_strict_rules(self) -> None:
        """Every tool in the Cloud chat surface passes all three strict rules
        after normalization. Covers AGENT_TOOLS + SUGGEST_BOARD_PLACEMENT
        — the exact set agent_adapter.py sends to the Responses API."""
        from dbt_charts.ai.llm import normalize_openai_tools

        normalized = normalize_openai_tools([*AGENT_TOOLS, SUGGEST_BOARD_PLACEMENT])
        for tool in normalized:
            violations = strict_mode_violations(tool["parameters"])
            assert not violations, (
                f"Tool {tool['name']!r} has strict-mode violations in emitted schema:\n"
                + "\n".join(f"  {v}" for v in violations)
            )

    def test_render_board_variables_is_array(self) -> None:
        schema = RENDER_BOARD["input_schema"]
        variables_schema = schema["properties"]["variables"]
        # anyOf: [{type: array, ...}, {type: null}] — not an object
        any_of = variables_schema.get("anyOf", [])
        types_present = {s.get("type") for s in any_of}
        assert "array" in types_present, (
            f"render_board.variables should be an array type, got: {variables_schema}"
        )
        assert "object" not in types_present, (
            f"render_board.variables must not be an open object, got: {variables_schema}"
        )

    def test_execute_query_variables_is_array(self) -> None:
        schema = EXECUTE_QUERY["input_schema"]
        variables_schema = schema["properties"]["variables"]
        any_of = variables_schema.get("anyOf", [])
        types_present = {s.get("type") for s in any_of}
        assert "array" in types_present, (
            f"execute_query.variables should be an array type, got: {variables_schema}"
        )
        assert "object" not in types_present, (
            f"execute_query.variables must not be an open object, got: {variables_schema}"
        )

    def test_query_board_vars_is_array(self) -> None:
        schema = QUERY_BOARD["input_schema"]
        vars_schema = schema["properties"]["vars"]
        any_of = vars_schema.get("anyOf", [])
        types_present = {s.get("type") for s in any_of}
        assert "array" in types_present, (
            f"query_board.vars should be an array type, got: {vars_schema}"
        )
        assert "object" not in types_present, (
            f"query_board.vars must not be an open object, got: {vars_schema}"
        )


class TestSubsetTool:
    """subset_tool narrows a tool in the canonical shape, so a host that hides a
    parameter still sends its schema through normalize_openai_tools."""

    def test_keeps_only_the_named_properties(self) -> None:
        narrowed = subset_tool(RENDER_BOARD, ["path", "yaml_content"])
        assert set(narrowed["input_schema"]["properties"]) == {"path", "yaml_content"}
        assert narrowed["name"] == RENDER_BOARD["name"]

    def test_leaves_the_source_tool_untouched(self) -> None:
        subset_tool(RENDER_BOARD, ["path"])
        assert "include_raw_data" in RENDER_BOARD["input_schema"]["properties"]

    def test_drops_narrowed_out_required_entries(self) -> None:
        narrowed = subset_tool(EXECUTE_QUERY, ["variables"])
        assert "sql" in EXECUTE_QUERY["input_schema"]["required"]
        assert narrowed["input_schema"]["required"] == []

    def test_unknown_property_raises(self) -> None:
        """A renamed field must fail loudly, not silently shrink the schema the
        model is offered."""
        with pytest.raises(ValueError, match="no properties"):
            subset_tool(RENDER_BOARD, ["path", "fromat"])

    def test_narrowed_tool_normalizes_to_a_strict_schema(self) -> None:
        from dbt_charts.ai.llm import normalize_openai_tools

        (tool,) = normalize_openai_tools([subset_tool(RENDER_BOARD, ["yaml_content"])])
        assert tool["strict"] is True
        assert not strict_mode_violations(tool["parameters"])


class TestSuggestBoardPlacement:
    """Cloud-only placement tool: schema follows the shared _ai_tool convention,
    but it is meaningless for core's agent (drafts/promotion is Cloud-only), so
    it must never appear in the universal tool lists."""

    def test_schema_shape(self) -> None:
        assert SUGGEST_BOARD_PLACEMENT["name"] == "suggest_board_placement"
        assert SUGGEST_BOARD_PLACEMENT["description"]
        properties = SUGGEST_BOARD_PLACEMENT["input_schema"]["properties"]
        assert "draft_slug" in properties
        assert "suggested_destination" in properties

    def test_absent_from_all_tools(self) -> None:
        assert SUGGEST_BOARD_PLACEMENT not in ALL_TOOLS

    def test_absent_from_agent_tools(self) -> None:
        assert SUGGEST_BOARD_PLACEMENT not in AGENT_TOOLS

    def test_absent_from_file_tools(self) -> None:
        assert SUGGEST_BOARD_PLACEMENT not in FILE_TOOLS


class TestMoveDeleteFileTools:
    """move_file/delete_file are chat-only (FILE_TOOLS), same precedent as
    read/write/edit/glob/grep — never MCP-visible (ALL_TOOLS)."""

    def test_schema_shape(self) -> None:
        assert MOVE_FILE["name"] == "move_file"
        assert DELETE_FILE["name"] == "delete_file"
        move_properties = MOVE_FILE["input_schema"]["properties"]
        assert "source_path" in move_properties
        assert "destination_path" in move_properties
        assert "path" in DELETE_FILE["input_schema"]["properties"]

    def test_present_in_file_tools(self) -> None:
        assert MOVE_FILE in FILE_TOOLS
        assert DELETE_FILE in FILE_TOOLS

    def test_present_in_agent_tools(self) -> None:
        assert MOVE_FILE in AGENT_TOOLS
        assert DELETE_FILE in AGENT_TOOLS

    def test_absent_from_all_tools(self) -> None:
        """ALL_TOOLS is the MCP-visible surface (dbt_charts.ai.mcp.server) — move/
        delete stay chat-only for v1 (MCP exposure is a noted follow-up, D-10)."""
        assert MOVE_FILE not in ALL_TOOLS
        assert DELETE_FILE not in ALL_TOOLS


class TestMCPPassthrough:
    """Third-party MCP schemas in OpenAI function format must pass through
    normalize_openai_tools unchanged — no strict flag, no schema rewriting."""

    def test_mcp_schema_in_mixed_list_passes_through_unchanged(self) -> None:
        """An MCP tool in OpenAI function format must emerge byte-identical when
        mixed with first-party tools. Previously the function checked only the
        first tool for input_schema, then applied strict transformation to every
        tool — mangling third-party schemas it had no authority over."""
        from dbt_charts.ai.llm import normalize_openai_tools

        mcp_tool: dict[str, Any] = {
            "type": "function",
            "name": "docs__search",
            "description": "search",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": True,
            },
        }
        result = normalize_openai_tools([*AGENT_TOOLS, mcp_tool])
        mcp_out = result[len(AGENT_TOOLS) :]
        assert mcp_out == [mcp_tool]
        assert "strict" not in mcp_out[0]


class TestToStrictJsonSchema:
    """Direct unit tests for _to_strict_json_schema arms not fully exercised
    by the full-payload test."""

    def test_items_recursion_strict_marks_object_inside_array(self) -> None:
        """Object schemas nested in an array items field are also strict-transformed."""
        from dbt_charts.ai.llm import _to_strict_json_schema

        schema: dict[str, Any] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
            },
        }
        _to_strict_json_schema(schema, schema)
        items = schema["items"]
        assert items.get("additionalProperties") is False
        assert items.get("required") == ["x"]

    def test_required_set_for_properties_without_type_object(self) -> None:
        """required is set whenever properties is present, not just under type: object."""
        from dbt_charts.ai.llm import _to_strict_json_schema

        schema: dict[str, Any] = {
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}
        }
        _to_strict_json_schema(schema, schema)
        assert set(schema.get("required", [])) == {"a", "b"}
