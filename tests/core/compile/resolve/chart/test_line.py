"""`connect: <column>` on a line chart: authored, normalized, resolved."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.chart.resolved import ResolvedLineChart
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_LOOP: list[dict[str, Any]] = [
    {"week": 1, "day": 1.0, "signups": 10.0},
    {"week": 2, "day": 5.0, "signups": 20.0},
    {"week": 3, "day": 3.0, "signups": 30.0},
]
_MONTHS: list[dict[str, Any]] = [
    {"week": 1, "day": "Jan", "signups": 10.0},
    {"week": 2, "day": "Feb", "signups": 20.0},
]


def _line(**fields: object) -> Chart:
    return normalize_chart(
        chart_id="c",
        chart_def={
            "type": "line",
            "query": {"columns": ["week", "day", "signups"], "values": [[1, 1, 2]]},
            "x": "day",
            "y": "signups",
            **fields,
        },
        query_registry={},
        sources={},
    )


def _resolve(rows: list[dict[str, Any]], **fields: object) -> ResolvedLineChart:
    style = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    resolved = resolve(_line(**fields), rows, chart_style_context=style)
    assert isinstance(resolved, ResolvedLineChart)
    return resolved


def _code(rows: list[dict[str, Any]], **fields: object) -> CompilationError:
    with pytest.raises(CompilationError) as exc_info:
        _resolve(rows, **fields)
    assert exc_info.value.code is not None
    return exc_info.value


def test_connect_names_a_column_that_reaches_the_resolved_chart() -> None:
    assert _line(connect="week").connect == "week"
    assert _resolve(_LOOP, connect="week").connect == "week"


def test_an_unauthored_connect_stays_unset() -> None:
    assert _resolve(_LOOP).connect is None


def test_connect_takes_a_column_name() -> None:
    board = {
        "title": "t",
        "charts": {
            "c": {"type": "line", "query": "q", "x": "a", "y": "b", "connect": True}
        },
    }
    with pytest.raises(ValidationError, match="connect"):
        AuthoredBoard.model_validate(board)


def test_connect_is_a_line_key_only() -> None:
    board = {
        "title": "t",
        "charts": {
            "c": {"type": "bar", "query": "q", "x": "a", "y": "b", "connect": "w"}
        },
    }
    with pytest.raises(ValidationError, match="connect"):
        AuthoredBoard.model_validate(board)


def test_connect_refuses_a_column_the_query_lacks() -> None:
    error = _code(_LOOP, connect="wek")
    assert error.code is not None
    assert error.code.code == "ERR-LINE-CONNECT-COLUMN-NOT-FOUND"
    assert "'wek'" in str(error)
    assert "week" in str(error)


def test_connect_refuses_a_null_in_its_column_naming_the_row() -> None:
    rows = [*_LOOP, {"week": None, "day": 4.0, "signups": 40.0}]
    error = _code(rows, connect="week")
    assert error.code is not None
    assert error.code.code == "ERR-LINE-CONNECT-NULL"
    assert "'week'" in str(error)
    assert "row 4" in str(error)


def test_connect_refuses_a_non_numeric_x() -> None:
    error = _code(_MONTHS, connect="week")
    assert error.code is not None
    assert error.code.code == "ERR-LINE-CONNECT-NEEDS-NUMERIC-X"
    assert "day" in str(error)


@pytest.mark.parametrize(
    "extra",
    [
        {"multiples": {"columns": "week"}},
        {"layers": [{"type": "line", "y": "day"}]},
    ],
)
def test_connect_refuses_layers_and_multiples(extra: dict[str, Any]) -> None:
    error = _code(_LOOP, connect="week", **extra)
    assert error.code is not None
    assert error.code.code == "ERR-LINE-CONNECT-ALONE"
