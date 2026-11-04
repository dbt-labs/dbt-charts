"""End to end: the table wrap warnings surface from render() with the column named.

A header that needs more than two lines is WARN-TABLE-HEADER-EXTRA-WRAP, and
wrapped text taller than its column is wide is WARN-TABLE-CONTENT-TALLER-THAN-WIDE.
Neither is raised for a table that merely has many columns: two-line headers are
the theme default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

_TEN_COLUMNS: dict[str, str] = {
    "region": "EMEA",
    "product_line": "Ingestion Cloud",
    "customer_segment": "Mid-Market",
    "order_channel": "Partner Reseller",
    "fulfillment_status": "Partially Shipped",
    "net_revenue": "2,013,880",
    "gross_margin": "51.2%",
    "units_shipped": "24,553",
    "return_rate": "2.1%",
    "last_order_date": "2025-11-04",
}

_PARAGRAPH = (
    "Customer reported intermittent sync failures across three connectors and "
    "requested a full backfill before the quarter close"
)


def _board(rows: list[dict[str, Any]], table_style: str = "") -> str:
    body = "\n".join(
        "      - {" + ", ".join(f"{k}: '{v}'" for k, v in row.items()) + "}"
        for row in rows
    )
    return (
        "title: T\n"
        "queries:\n  q:\n    type: values\n    rows:\n" + body + "\n"
        "charts:\n  t:\n    query: q\n    type: table\n    style:\n"
        "      preferred_width: 800\n" + table_style + "rows:\n  - t\n"
    )


def _table_warnings(yaml_text: str) -> list[Diagnostic]:
    result = compile_board(yaml_text)
    assert result.success, result.diagnostics
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    output = render(result.board, executor, format="svg")
    return [w for w in output.warnings if w.code.startswith("WARN-TABLE")]


def _codes(yaml_text: str) -> list[str]:
    return [w.code for w in _table_warnings(yaml_text)]


def test_ten_realistic_columns_with_two_line_headers_raise_nothing() -> None:
    """Ten columns need more than their share of the slot, so headers wrap to two
    lines. Two lines are the theme default and every value fits: nothing to say."""
    assert _codes(_board([_TEN_COLUMNS] * 4)) == []


def test_a_wrap_two_header_needing_three_lines_is_extra_wrap_not_truncation() -> None:
    columns = (
        "      columns:\n"
        "        product_line:\n"
        "          width: 60\n"
        "          label: Quarterly recurring revenue per product line\n"
    )
    warnings = _table_warnings(_board([_TEN_COLUMNS] * 2, columns))
    assert [w.code for w in warnings] == ["WARN-TABLE-HEADER-EXTRA-WRAP"]
    (warning,) = warnings
    assert warning.path == "charts.t.style.columns.product_line"
    assert "'product_line' column header" in warning.message
    assert "Only 60px were allocated for this column, in a table of 10 columns" in (
        warning.message
    )


def test_a_wrap_mode_header_that_draws_every_line_warns_too() -> None:
    columns = (
        "      columns:\n"
        "        product_line:\n"
        "          width: 60\n"
        "          header_overflow: wrap\n"
        "          label: Quarterly recurring revenue per product line\n"
    )
    assert _codes(_board([_TEN_COLUMNS] * 2, columns)) == [
        "WARN-TABLE-HEADER-EXTRA-WRAP"
    ]


def test_wrapped_text_taller_than_its_column_is_wide_names_the_column() -> None:
    rows = [{"ticket": f"T-{i:03d}", "notes": _PARAGRAPH} for i in range(4)]
    columns = "      columns:\n        notes:\n          width: 90\n"
    (warning,) = _table_warnings(_board(rows, columns))
    assert warning.code == "WARN-TABLE-CONTENT-TALLER-THAN-WIDE"
    assert warning.path == "charts.t.style.columns.notes"
    assert "4 cell(s)" in warning.message
    assert "90px" in warning.message
    assert "Customer reported" not in warning.message
    assert "sync failures" not in (warning.fix or "")


def test_short_text_in_a_thin_column_is_not_a_tower() -> None:
    rows = [{"ticket": f"T-{i:03d}", "owner": "red blue gray"} for i in range(4)]
    columns = "      columns:\n        owner:\n          width: 60\n"
    assert _codes(_board(rows, columns)) == []


def test_a_text_column_with_room_wraps_without_a_warning() -> None:
    rows = [{"ticket": f"T-{i:03d}", "notes": _PARAGRAPH} for i in range(4)]
    assert _codes(_board(rows)) == []
