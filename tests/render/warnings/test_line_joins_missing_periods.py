"""Tests for the LINE_JOINS_MISSING_PERIODS render-warning detector.

A line breaks only at a row whose y is NULL, so a period with no row joins its
neighbors. The detector fires when a temporal series keeps a regular grain and
two plotted neighbors skip one or more periods of it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.diagnostics import WARN_LINE_JOINS_MISSING_PERIODS, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    line_joins_missing_periods as detector,
)

from ..._scripted_env import ScriptedBackend, scripted_env
from ...core._board_utils import (
    make_test_resolved_board,
    make_test_resolved_chart,
)


def _warnings(rows: list[dict[str, Any]], **chart: object) -> list[Diagnostic]:
    spec = TypeAdapter(Chart).validate_python(
        {
            "id": "c1",
            "type": "line",
            "query_name": "q",
            "title": "",
            "x": "month",
            "y": "n",
            **chart,
        }
    )
    resolved = make_test_resolved_chart(spec, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return detector.detect(
        WarningContext(board_spec=board, chart_results={resolved.id: rows})
    )


def _months(*present: int, y: int | None = 1, **extra: object) -> list[dict[str, Any]]:
    return [{"month": date(2024, m, 1).isoformat(), "n": y, **extra} for m in present]


def test_fires_when_a_monthly_series_skips_months() -> None:
    [warning] = _warnings(_months(1, 2, 3, 6, 7, 8, 9))

    assert warning.fix is not None
    assert warning.code == WARN_LINE_JOINS_MISSING_PERIODS.code
    assert warning.chart == "c1"
    assert "2 missing monthly periods" in warning.message
    assert "NULL `n`" in warning.fix
    assert "every month from 2024-01-01 to 2024-09-01" in warning.fix
    assert "warnings_ignore: [WARN-LINE-JOINS-MISSING-PERIODS]" in warning.fix


def test_fires_on_an_area_chart() -> None:
    assert len(_warnings(_months(1, 2, 3, 6, 7, 8, 9), type="area")) == 1


def test_silent_on_a_stacked_area_that_draws_the_gap_as_zero() -> None:
    rows = _months(*range(1, 10), region="EMEA") + _months(
        1, 2, 3, 6, 7, 8, 9, region="APAC"
    )

    assert _warnings(rows, type="area", color="region", stack="zero") == []


def test_fires_on_an_unstacked_area_with_several_series() -> None:
    rows = _months(*range(1, 10), region="EMEA") + _months(
        1, 2, 3, 6, 7, 8, 9, region="APAC"
    )

    assert len(_warnings(rows, type="area", color="region", stack="none")) == 1


def test_silent_on_a_complete_series() -> None:
    assert _warnings(_months(*range(1, 10))) == []


def test_silent_when_a_null_row_already_marks_the_gap() -> None:
    rows = _months(1, 2, 3, 6, 7, 8, 9)
    rows.insert(3, {"month": "2024-04-01", "n": None})
    rows.insert(4, {"month": "2024-05-01", "n": None})

    assert _warnings(rows) == []


def test_fires_when_a_null_row_marks_only_one_of_the_gaps() -> None:
    rows = _months(1, 2, 3, 6, 7, 8, 10, 11, 12)
    rows += [{"month": f"2024-0{m}-01", "n": None} for m in (4, 5)]

    [warning] = _warnings(rows)
    assert "1 missing monthly period" in warning.message


def test_silent_on_irregular_event_dates() -> None:
    offsets = [0, 2, 7, 10, 17, 18, 27, 31]
    rows = [
        {
            "month": date.fromordinal(date(2024, 3, 1).toordinal() + o).isoformat(),
            "n": 1,
        }
        for o in offsets
    ]

    assert _warnings(rows) == []


def test_silent_on_a_series_that_is_regular_at_a_coarser_step() -> None:
    assert _warnings(_months(1, 3, 5, 7, 9, 11)) == []


def test_names_the_coarser_step_when_one_is_skipped() -> None:
    [warning] = _warnings(_months(1, 3, 5, 9, 11))

    assert "1 missing 2-month period" in warning.message


def test_weekends_are_not_missing_days_in_a_business_day_series() -> None:
    weekdays = [d for d in range(1, 29) if date(2024, 1, d).weekday() < 5]
    rows = [{"month": date(2024, 1, d).isoformat(), "n": 1} for d in weekdays]

    assert _warnings(rows) == []


def test_a_missing_weekday_in_a_business_day_series_still_fires() -> None:
    weekdays = [d for d in range(1, 29) if date(2024, 1, d).weekday() < 5 and d != 10]
    rows = [{"month": date(2024, 1, d).isoformat(), "n": 1} for d in weekdays]

    [warning] = _warnings(rows)
    assert "1 missing daily period" in warning.message


def test_each_series_is_judged_on_its_own_rows() -> None:
    complete = _months(*range(1, 10), region="EMEA")
    gappy = _months(1, 2, 3, 6, 7, 8, 9, region="APAC")

    [warning] = _warnings(complete + gappy, color="region")

    assert "'APAC': 2 missing monthly periods" in warning.message
    assert "EMEA" not in warning.message
    assert warning.fix is not None
    assert "crossed with the distinct series values" in warning.fix


def test_a_series_complete_on_its_own_does_not_inherit_another_series_gap() -> None:
    # EMEA has every month APAC lacks; neither series skips within itself.
    emea = _months(1, 3, 5, 7, 9, region="EMEA")
    apac = _months(2, 4, 6, 8, region="APAC")

    assert _warnings(emea + apac, color="region") == []


@pytest.mark.parametrize("chart_type", ["bar", "scatter"])
def test_silent_on_other_chart_types(chart_type: str) -> None:
    assert _warnings(_months(1, 2, 3, 6, 7, 8, 9), type=chart_type) == []


def test_the_warning_surfaces_from_render() -> None:

    from dbt_charts.core.compile import compile
    from dbt_charts.core.render import render

    from ..._run import Run

    result = compile(
        "title: Probe\n"
        "charts:\n  c:\n    query: q\n    type: line\n    x: month\n    y: n\n"
        "queries:\n  q:\n    sql: SELECT * FROM t\n    source: test_source\n"
        "rows:\n  - c\n"
    )
    assert result.success and result.board is not None, result.errors
    registry = scripted_env(ScriptedBackend(_months(1, 2, 3, 6, 7)))
    executor = Run(result.board, env=registry)

    rendered = render(result.board, executor.ctx, executor.ledger, format="svg")

    assert [w.code for w in rendered.warnings] == [WARN_LINE_JOINS_MISSING_PERIODS.code]
