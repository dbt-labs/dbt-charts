"""The shared MCP instructions speak only of tools every host serves."""

from __future__ import annotations

from dbt_charts.ai.mcp.server import MCP_INSTRUCTIONS


def test_instructions_never_name_find_queries() -> None:
    """Cloud denies `find_queries` and a default `dct mcp serve` opens no cache, so
    an instruction to look there first would send most hosts to a missing tool;
    the tool's own description carries that guidance where it is served."""
    assert "find_queries" not in MCP_INSTRUCTIONS
