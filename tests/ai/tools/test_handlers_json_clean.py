"""Contract: every TOOL_HANDLERS entry must return a JSON-clean dict.

JSON-clean = bare `json.dumps(result)` succeeds without a `default=` fallback.
Bug class this guards against: handlers returning Pydantic `model_dump()`
defaults (mode='python'), leaking PosixPath / datetime / Decimal into the
dict, which crashes the MCP transport's stdlib `json.dumps` at the wire.

The MCP transport (``dbt_charts.ai.mcp.server``) intentionally calls bare
`json.dumps(result)` — no `default=` cushion. If a handler regresses, MCP
crashes with `TypeError`; that loud failure is the desired behavior, not a
silent stringification of a complex object to `<Foo at 0x7f…>`.

Adding a new tool to `TOOL_HANDLERS` requires a row here. Tools that need
live warehouse access (executes SQL against a real source) are skipped — the
E2E lane in `tests/e2e/mcp/test_mcp_*.py` covers those.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dbt_charts.ai.context import DbtChartsAIContext
from dbt_charts.ai.tools import TOOL_HANDLERS

# Tools whose handlers require a live warehouse adapter to run meaningfully.
# Covered by the E2E lane against fixture projects with real data.
_WAREHOUSE_ONLY: frozenset[str] = frozenset({"execute_query"})


def _handler_args() -> dict[str, dict[str, Any]]:
    """Args picked to exercise a graceful path that returns a Pydantic-modeled
    result. Wrong-input results are fine; the contract under test is
    JSON-cleanliness of the dict, not success.
    """
    return {
        "render_board": {"path": "nonexistent.yml"},
        "search_boards": {"query": "anything"},
        "docs": {"topic": "yaml"},
        "describe_board": {"path": "nonexistent.yml"},
        "read_file": {"path": "nonexistent.yml"},
        "write_file": {"path": "out.yml", "content": "title: X\n"},
        "edit_file": {
            "path": "nonexistent.yml",
            "old_string": "a",
            "new_string": "b",
        },
        "glob_files": {"pattern": "*.yml"},
        "grep_files": {"pattern": "anything"},
        "move_file": {"source_path": "nonexistent.yml", "destination_path": "dest.yml"},
        "delete_file": {"path": "nonexistent.yml"},
        "list_skills": {},
        "get_skill": {"name": "kpi-row"},
        "list_diagnostic_codes": {},
        "get_diagnostic_code": {"code": "WARN-REDUNDANT-ENCODING"},
    }


def _tested_tools() -> list[Any]:
    rows: list[Any] = []
    for name in TOOL_HANDLERS:
        if name in _WAREHOUSE_ONLY:
            continue
        rows.append(pytest.param(name, id=name))
    return rows


@pytest.mark.parametrize("tool_name", _tested_tools())
def test_handler_returns_json_clean_dict(
    tool_name: str, context: DbtChartsAIContext
) -> None:
    args = _handler_args()[tool_name]
    result = TOOL_HANDLERS[tool_name](dict(args), context)
    json.dumps(result)


def test_handler_args_cover_every_non_warehouse_tool() -> None:
    """Adding a new tool to TOOL_HANDLERS forces a parametrize row."""
    expected = {name for name in TOOL_HANDLERS if name not in _WAREHOUSE_ONLY}
    assert set(_handler_args()) == expected
