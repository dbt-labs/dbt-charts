"""Tests for the TABLE_HEADER_EXTRA_WRAP render-warning detector.

The engine measures how many lines a header needs where it lays the header out
and raises one warning per column (``WarningContext.geometry_warnings``); the
detector turns each into a Diagnostic. Raising it from a real render is covered
by ``tests/core/render/test_table_header_warnings_e2e.py``.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_TABLE_HEADER_EXTRA_WRAP
from dbt_charts.core.render.warnings import WarningContext, table_header_extra_wrap

from ...core._board_utils import make_test_resolved_board


def _params(column: str, lines: int = 3) -> dict[str, object]:
    return {
        "chart_id": "orgs",
        "column": column,
        "header": "Weekly skip rate for the trailing year",
        "lines": lines,
        "column_width": 96.4,
        "column_count": 7,
        "table_width": 912.0,
    }


def _ctx(*found: dict[str, object]) -> WarningContext:
    return WarningContext(
        board_spec=make_test_resolved_board(charts={}),
        chart_results={},
        geometry_warnings={"orgs": {WARN_TABLE_HEADER_EXTRA_WRAP.code: list(found)}},
    )


def test_a_header_needing_three_lines_names_the_column_and_the_space() -> None:
    (warning,) = table_header_extra_wrap.detect(_ctx(_params("skip_rate")))
    assert warning.code == WARN_TABLE_HEADER_EXTRA_WRAP.code
    assert warning.chart == "orgs"
    assert warning.path == "charts.orgs.style.columns.skip_rate"
    assert warning.message == (
        "Table 'orgs': the 'skip_rate' column header "
        "'Weekly skip rate for the trailing year' needs 3 lines. Two lines are "
        "fine; more is too much. Only 96px were allocated for this column, in a "
        "table of 7 columns and 912px total width."
    )
    assert warning.fix is not None
    assert "style.columns.skip_rate.width" in warning.fix


def test_one_warning_per_column() -> None:
    warnings = table_header_extra_wrap.detect(_ctx(_params("a"), _params("b", 4)))
    assert [w.path for w in warnings] == [
        "charts.orgs.style.columns.a",
        "charts.orgs.style.columns.b",
    ]


def test_nothing_recorded_yields_nothing() -> None:
    ctx = WarningContext(
        board_spec=make_test_resolved_board(charts={}), chart_results={}
    )
    assert table_header_extra_wrap.detect(ctx) == []
