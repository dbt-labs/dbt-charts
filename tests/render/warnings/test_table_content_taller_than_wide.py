"""Tests for the TABLE_CONTENT_TALLER_THAN_WIDE render-warning detector.

The engine measures each wrapped text cell where it wraps it and raises one
warning per column (``WarningContext.geometry_warnings``); the detector turns
each into a Diagnostic. Raising it from a real render is covered by
``tests/core/render/test_table_content_tall_e2e.py``.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_TABLE_CONTENT_TALLER_THAN_WIDE
from dbt_charts.core.render.warnings import (
    WarningContext,
    table_content_taller_than_wide,
)

from ...core._board_utils import make_test_resolved_board


def _ctx(*found: dict[str, object]) -> WarningContext:
    return WarningContext(
        board_spec=make_test_resolved_board(charts={}),
        chart_results={},
        geometry_warnings={
            "tickets": {WARN_TABLE_CONTENT_TALLER_THAN_WIDE.code: list(found)}
        },
    )


def _params(column: str) -> dict[str, object]:
    return {
        "chart_id": "tickets",
        "column": column,
        "max_lines": 9,
        "cell_count": 14,
        "column_width": 88.0,
    }


def test_names_the_column_the_worst_cell_and_how_many_cells_tripped() -> None:
    (warning,) = table_content_taller_than_wide.detect(_ctx(_params("notes")))
    assert warning.code == WARN_TABLE_CONTENT_TALLER_THAN_WIDE.code
    assert warning.chart == "tickets"
    assert warning.path == "charts.tickets.style.columns.notes"
    assert "'notes' column" in warning.message
    assert "9 lines" in warning.message
    assert "14 cell(s)" in warning.message
    assert "88px" in warning.message
    assert warning.fix is not None
    assert "style.columns.notes.width" in warning.fix


def test_one_warning_per_column() -> None:
    warnings = table_content_taller_than_wide.detect(
        _ctx(_params("notes"), _params("summary"))
    )
    assert [w.path for w in warnings] == [
        "charts.tickets.style.columns.notes",
        "charts.tickets.style.columns.summary",
    ]


def test_nothing_recorded_yields_nothing() -> None:
    ctx = WarningContext(
        board_spec=make_test_resolved_board(charts={}), chart_results={}
    )
    assert table_content_taller_than_wide.detect(ctx) == []
