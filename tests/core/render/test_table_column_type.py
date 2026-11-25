"""``style.columns.<col>.type``: a column printed as text, number, date or datetime.

The text preview (``board_to_dict._table_text_rows``) prints the cell the table
paints; the painted side is pinned in
``libs/chart-svg/chart-renderer/tests/it/table_format_knobs.rs``.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.format_value import cast_table_cell
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.models.chart.resolved.table import ResolvedTableChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import TableChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.board_to_dict import _table_text_rows


def _resolved(
    cells: list[Any],
    column: dict[str, Any],
    defaults: dict[str, Any] | None = None,
    query_formats: dict[str, str] | None = None,
) -> tuple[ResolvedTableChart, list[dict[str, Any]], Any]:
    _, context = resolve_style_and_context(get_theme_style(get_default_theme_name()))
    style: dict[str, Any] = {"columns": {"v": column}}
    if defaults is not None:
        style["column_defaults"] = defaults
    chart = TableChart(
        id="t1",
        type="table",
        style=TableChartStylePatch.model_validate(style),
        query=SqlQuery(sql="SELECT 1", source="t", formats=query_formats),
    )
    data = [{"v": cell} for cell in cells]
    resolved = resolve(chart, data, chart_style_context=context)
    assert isinstance(resolved, ResolvedTableChart)
    return resolved, data, context


def _preview(
    cells: list[Any],
    column: dict[str, Any],
    defaults: dict[str, Any] | None = None,
    query_formats: dict[str, str] | None = None,
) -> list[str]:
    resolved, data, context = _resolved(cells, column, defaults, query_formats)
    return [row["v"] for row in _table_text_rows(resolved, data, context.formats)]


def test_text_prints_every_cell_as_stored() -> None:
    assert _preview(["2028-07-02", "1200.5", "Q1 2024"], {"type": "text"}) == [
        "2028-07-02",
        "1200.5",
        "Q1 2024",
    ]
    assert _preview(["", None], {"type": "text"}) == ["—", "—"]
    assert _preview([1234.5, 7], {"type": "text"}) == ["1234.5", "7"]


def test_number_reads_text_cells_through_the_column_format() -> None:
    column = {"type": "number", "format": "$,.2f"}
    assert _preview(["1200.5", " 3 ", "1_000"], column) == [
        "$1,200.50",
        "$3.00",
        "$1,000.00",
    ]
    assert _preview(["", None], column) == ["—", "—"]


def test_date_and_datetime_print_as_a_date_column_does() -> None:
    assert _preview(["2024-07-02"], {"type": "date"}) == ["2 Jul 2024"]
    assert _preview(
        ["2024-07-02", "2024-07-03T10:30:00"], {"type": "datetime", "format": "%b %Y"}
    ) == [
        "Jul 2024",
        "Jul 2024",
    ]


def test_an_inherited_format_of_another_kind_does_not_apply_to_a_typed_column() -> None:
    assert _preview(["2024-07-02"], {"type": "date"}, {"format": "$,.2f"}) == [
        "2 Jul 2024"
    ]
    assert _preview(["1200.5"], {"type": "number"}, {"format": "%b %Y"}) == ["1.2K"]


@pytest.mark.parametrize(
    ("column_type", "cell"),
    [
        ("number", "abc"),
        ("number", "nan"),
        ("number", True),
        ("date", "July 2"),
        ("date", "2024-07-02T10:30:00"),
        ("date", "2024-13-40"),
        ("datetime", "2024-07-02 at noon"),
    ],
)
def test_a_cell_that_does_not_cast_is_an_error_naming_column_cell_and_type(
    column_type: str, cell: Any
) -> None:
    with pytest.raises(ChartDataError) as exc:
        _preview([cell], {"type": column_type})
    assert exc.value.code is not None
    assert exc.value.code.code == "ERR-TABLE-CELL-CAST"
    assert f"column 'v' has type {column_type}" in str(exc.value)
    assert repr(cell) in str(exc.value)


def test_a_typed_column_resolves_whatever_format_its_values_or_query_would_pick() -> (
    None
):
    # a query `formats:` currency entry, a number default on all-null cells and an
    # alias the model cannot classify all leave the typed column's own kind alone
    assert _preview(["x"], {"type": "text"}, query_formats={"v": "currency"}) == ["x"]
    assert _preview([None, ""], {"type": "number"}, {"format": "$,.2f"}) == ["—", "—"]
    assert _preview(["2024-07-02"], {"type": "date"}, {"format": "currency"}) == [
        "2 Jul 2024"
    ]
    assert _preview(
        ["2024-07-02"], {"type": "date"}, query_formats={"v": "currency"}
    ) == ["2 Jul 2024"]
    resolved, *_ = _resolved(["x"], {"type": "text"}, {"format": "unresolved_alias"})
    assert resolved.columns is not None
    assert resolved.columns["v"].format is None


def test_number_leaves_an_already_numeric_cell_unchanged() -> None:
    cells = [1.5, float("nan"), float("inf"), Decimal("2.50"), 3]
    for cell in cells:
        cast = cast_table_cell(cell, "number", "v")
        assert cast is cell


@pytest.mark.parametrize(
    ("column_type", "cell"),
    [
        ("datetime", "2024-01-01T25:00:00"),
        ("number", "١٢٣"),
        ("text", True),
        ("text", ["a"]),
        ("number", {"a": 1}),
    ],
)
def test_the_cast_refuses_what_the_renderer_refuses(
    column_type: str, cell: Any
) -> None:
    with pytest.raises(ChartDataError, match="ERR-TABLE-CELL-CAST|has type"):
        cast_table_cell(cell, column_type, "v")


def test_text_prints_the_spelling_the_table_paints() -> None:
    when = datetime.datetime(2024, 7, 2, 10, 30)
    assert cast_table_cell(when, "text", "v") == "2024-07-02T10:30:00"
    assert cast_table_cell(Decimal("2.50"), "text", "v") == "2.50"
    assert cast_table_cell(float("nan"), "text", "v") is None
    assert cast_table_cell("2024-07-02 ", "date", "v") == "2024-07-02"


def test_a_string_column_typed_date_resolves_right_aligned_but_datetime_does_not() -> (
    None
):
    def align(column_type: str, cells: list[Any]) -> str | None:
        resolved, *_ = _resolved(cells, {"type": column_type})
        assert resolved.columns is not None
        return resolved.columns["v"].align

    assert align("date", ["2024-07-02"]) == "right"
    assert align("datetime", ["2024-07-03T10:30:00"]) is None
    assert align("text", ["2024-07-02"]) is None


def test_date_keeps_every_other_date_spelling_as_written() -> None:
    spellings = ["03/15/2024", "Mar 15", "2024-03"]
    assert _preview(spellings, {"type": "date"}) == spellings
    assert _preview(["Q1 2024", "2024"], {"type": "date", "format": "%b %Y"}) == [
        "Q1 2024",
        "2024",
    ]
    assert cast_table_cell("Mar 15, 2024", "date", "v") == "Mar 15, 2024"
