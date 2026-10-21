"""A query's ``formats:`` names how its columns print, wherever they are painted."""

from __future__ import annotations

import re
from unittest.mock import Mock

import pytest

from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.format import (
    column_format,
    finalize_kpi_value_format,
    kpi_format_native,
)
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.style.authored import TableColumnConfig
from dbt_charts.core.compile.resolve.chart._table import _with_resolved_scale_stops
from dbt_charts.core.execute import Executor, QueryError

from .._svg_render import render_board_to_svg

_KPI_COMPACT = get_theme_style().charts.kpi.value.compact

_STYLE = "style:\n  formats:\n    currency: '$,.0f'\n    integer: ',.0f'\n"
_FORMATS = "    formats:\n      revenue: currency\n"


def _board(
    chart: str,
    *,
    style: str = _STYLE,
    formats: str = _FORMATS,
) -> str:
    return f"""
title: T
{style}
queries:
  q:
    type: values
    rows:
      - {{month: 2026-01-01, revenue: 1234567, orders: 1234, year: 2025}}
      - {{month: 2026-02-01, revenue: 2345678, orders: 2345, year: 2026}}
{formats}
charts:
  c:
    query: q
{chart}
rows:
  - c
"""


_BAR = "    type: bar\n    x: month\n    y: revenue\n"
_TABLE = "    type: table\n"
_KPI_ROWS = "    type: kpi\n    value: revenue\n"


def _kpi_board(chart: str = _KPI_ROWS, **kwargs: str) -> str:
    return _board(chart, **kwargs).replace(
        "      - {month: 2026-02-01, revenue: 2345678, orders: 2345, year: 2026}\n", ""
    )


def test_one_query_format_reaches_axis_tooltip_table_cell_and_kpi() -> None:
    bar = render_board_to_svg(_board(_BAR))
    assert "$1,000,000" in bar
    assert "$2,345,678" in bar
    table = render_board_to_svg(_board(_TABLE))
    assert ">$</tspan>" in table and "1,234,567" in table
    kpi = render_board_to_svg(_kpi_board())
    assert ">$</t" in kpi and "1,234,567" in kpi


def test_without_formats_the_engine_number_prints() -> None:
    assert "$1,000,000" not in render_board_to_svg(_board(_BAR, formats=""))


def test_the_column_format_beats_a_board_level_axis_format() -> None:
    style = _STYLE + "  charts:\n    axis_y:\n      labels:\n        format: integer\n"
    assert "$1,000,000" in render_board_to_svg(_board(_BAR, style=style))


def test_a_slot_format_beats_the_column_format() -> None:
    kpi = _KPI_ROWS + "    style:\n      value:\n        format: integer\n"
    svg = render_board_to_svg(_kpi_board(kpi))
    assert "1,234,567" in svg and ">$</t" not in svg
    table = (
        _TABLE
        + "    style:\n      columns:\n        revenue:\n          format: integer\n"
    )
    assert ">$</tspan>" not in render_board_to_svg(_board(table))


def test_axis_labels_format_beats_the_column_format() -> None:
    chart = (
        _BAR + "    style:\n      axis_y:\n        labels:\n          format: integer\n"
    )
    assert "$1,000,000" not in render_board_to_svg(_board(chart))


def test_a_numeric_x_axis_takes_its_columns_format() -> None:
    chart = "    type: scatter\n    x: orders\n    y: revenue\n"
    formats = "    formats:\n      orders: integer\n"
    assert "1.5k" in _tick_labels(_board(chart, formats=""))
    ticks = _tick_labels(_board(chart, formats=formats))
    assert "1,500" in ticks and "1.5k" not in ticks


def _tick_labels(board: str) -> list[str]:
    return re.findall(r">([^<>]+)</text>", render_board_to_svg(board))


_KPI_USD = "    style:\n      value:\n        format:\n          suffix: ' USD'\n"
_BOARD_KPI_USD = (
    _STYLE + "  charts:\n    kpi:\n      value:\n        format:\n"
    "          suffix: ' USD'\n"
)


def test_an_affix_only_format_keeps_its_affix_whichever_tier_it_is() -> None:
    suffix = FormatConfig(suffix=" USD")
    as_slot = finalize_kpi_value_format(suffix, 12345.0, compact=_KPI_COMPACT)
    as_lower_tier = finalize_kpi_value_format(
        None,
        12345.0,
        {"usd": suffix},
        compact=_KPI_COMPACT,
        query_formats={"c": "usd"},
        columns="c",
    )
    assert as_slot is not None and as_lower_tier is not None
    assert as_lower_tier.suffix == as_slot.suffix == " USD"
    assert as_lower_tier.spec == as_slot.spec


def test_a_board_level_affix_only_kpi_format_still_prints_its_affix() -> None:
    svg = render_board_to_svg(_kpi_board(style=_BOARD_KPI_USD, formats=""))
    assert "USD" in svg


def test_a_declared_format_beats_a_board_level_kpi_value_format() -> None:
    style = _STYLE + "  charts:\n    kpi:\n      value:\n        format: integer\n"
    assert ">$</t" in render_board_to_svg(_kpi_board(style=style))


def test_an_affix_only_kpi_slot_takes_its_spec_from_the_column() -> None:
    svg = render_board_to_svg(_kpi_board(_KPI_ROWS + _KPI_USD))
    assert ">$</t" in svg and "USD" in svg


def test_a_date_kpi_takes_the_declared_time_format_under_a_board_affix() -> None:
    style = (
        "style:\n  formats:\n    isoday: '%Y/%m/%d'\n  charts:\n    kpi:\n"
        "      value:\n        format:\n          suffix: ' UTC'\n"
    )
    board = _kpi_board(
        "    type: kpi\n    value: day\n",
        style=style,
        formats="    formats:\n      day: isoday\n",
    ).replace("year: 2025}", "year: 2025, day: 2026-03-04}")
    svg = render_board_to_svg(board)
    assert "2026/03/04" in svg and "UTC" in svg


def test_the_native_flag_holds_for_a_declared_alias_under_a_board_affix() -> None:
    money = {"money": ",.0f"}
    declared = {"query_formats": {"c": "money"}, "columns": "c"}
    assert kpi_format_native(money, None, **declared)
    assert kpi_format_native(money, None, board=FormatConfig(suffix=" USD"), **declared)
    assert not kpi_format_native(money, None, board=FormatConfig(suffix=" USD"))


def test_a_board_layer_of_the_other_kind_contributes_nothing_not_even_its_affix() -> (
    None
):
    style = (
        "style:\n  charts:\n    kpi:\n      value:\n        format:\n"
        "          spec: date_short\n          suffix: ' (FY)'\n"
    )
    svg = render_board_to_svg(_kpi_board(style=style, formats=""))
    assert "(FY)" not in svg and "1.2" in svg


def test_a_table_defaults_layer_of_the_other_kind_contributes_nothing() -> None:
    table = (
        _TABLE + "    style:\n      column_defaults:\n        format:\n"
        "          spec: '%b %Y'\n          prefix: 'FY '\n"
    )
    textual = (
        _board(table, formats="")
        .replace("2026-01-01", "Jan")
        .replace("2026-02-01", "Feb")
    )
    assert "FY" not in render_board_to_svg(textual)


def test_a_declared_format_with_a_sign_placement_is_refused_in_a_table() -> None:
    style = (
        _STYLE + "    signed:\n      spec: '+,.0f'\n      prefix: '$'\n"
        "      sign_placement: after_prefix\n"
    )
    formats = "    formats:\n      revenue: signed\n"
    svg = render_board_to_svg(_board(_TABLE, style=style, formats=formats))
    assert "ERR-FORMAT-SIGN-PLACEMENT-TABLE-UNSUPPORTED" in svg


def test_a_table_column_keeps_its_own_options_over_the_declared_spec() -> None:
    columns = {
        "revenue": TableColumnConfig(format=FormatConfig(prefix="~", repeat="anchor"))
    }
    formats = {"tagged": FormatConfig(spec=",.0f", suffix=" USD")}
    resolved = _with_resolved_scale_stops(
        columns,
        None,
        formats,
        "Arial",
        [{"revenue": 1234567}],
        True,
        {"revenue": columns["revenue"].format},
        None,
        {"revenue": "tagged"},
        filter_variables=frozenset(),
        link_open="page",
    )
    parts = resolved["revenue"].format
    assert parts is not None
    assert (parts.prefix, parts.suffix, parts.repeat) == ("~", " USD", "anchor")
    assert parts.spec == ",.0f"


def test_a_donut_total_takes_the_declared_format_over_a_board_slot() -> None:
    style = (
        _STYLE
        + "  charts:\n    pie:\n      total:\n        value:\n          format: integer\n"
    )
    chart = (
        "    type: donut\n    theta: revenue\n    color: month\n"
        "    total:\n      visible: true\n"
    )
    svg = render_board_to_svg(_board(chart, style=style))
    assert ">$3,580,245</t" in svg


def test_an_affix_only_layer_keeps_its_options_and_takes_the_spec_below() -> None:
    parts = column_format(
        "number",
        None,
        FormatConfig(prefix="EUR ", repeat="anchor", sign_placement="after_prefix"),
        query_formats={"c": "integer"},
        columns="c",
    )
    assert (parts.prefix, parts.repeat, parts.sign_placement) == (
        "EUR ",
        "anchor",
        "after_prefix",
    )
    assert parts.spec and parts.raw == "integer"


def test_a_chart_kpi_spec_keeps_the_boards_affix() -> None:
    style = (
        "style:\n  charts:\n    kpi:\n      value:\n        format:\n"
        "          spec: ',.2f'\n          suffix: ' USD'\n"
    )
    chart = (
        _KPI_ROWS
        + "    style:\n      value:\n        format:\n          spec: ',.0f'\n"
    )
    svg = render_board_to_svg(_kpi_board(chart, style=style, formats=""))
    assert "USD" in svg and "1,234,567" in svg and "1,234,567.00" not in svg


def test_a_chart_kpi_spec_keeps_the_boards_prefix_only_format() -> None:
    style = (
        "style:\n  charts:\n    kpi:\n      value:\n        format:\n"
        "          prefix: 'approx '\n"
    )
    chart = (
        _KPI_ROWS
        + "    style:\n      value:\n        format:\n          spec: ',.0f'\n"
    )
    svg = render_board_to_svg(_kpi_board(chart, style=style, formats=""))
    assert "approx" in svg and "1,234,567" in svg


def test_a_named_affix_only_table_format_takes_its_spec_from_the_column() -> None:
    style = _STYLE + "    tag:\n      suffix: ' USD'\n"
    table = (
        _TABLE + "    style:\n      columns:\n        revenue:\n          format: tag\n"
    )
    svg = render_board_to_svg(_board(table, style=style))
    assert ">$</tspan>" in svg and "USD" in svg


def test_a_declared_format_beats_table_column_defaults() -> None:
    table = _TABLE + "    style:\n      column_defaults:\n        format: integer\n"
    textual = _board(table).replace("2026-01-01", "Jan").replace("2026-02-01", "Feb")
    assert ">$</tspan>" in render_board_to_svg(textual)


def test_an_affix_only_table_column_format_takes_its_spec_from_the_column() -> None:
    table = (
        _TABLE + "    style:\n      columns:\n        revenue:\n          format:\n"
        "            suffix: ' USD'\n"
    )
    svg = render_board_to_svg(_board(table))
    assert ">$</tspan>" in svg and "USD" in svg


def test_inline_chart_queries_get_the_same_format_checks() -> None:
    def board(formats: str) -> str:
        return f"""
title: T
{_STYLE}
charts:
  c:
    type: bar
    x: month
    y: revenue
    query:
      type: values
      rows:
        - {{month: 2026-01-01, revenue: 1}}
      formats:
        {formats}
rows:
  - c
"""

    for formats, code in (
        ("revenue: curency", "ERR-QUERY-FORMAT-UNKNOWN"),
        ("revenue: '$,.0f'", "ERR-QUERY-FORMAT-SPEC"),
        ("totl: currency", "ERR-QUERY-FORMAT-COLUMN-MISSING"),
    ):
        result = compile_board(board(formats))
        assert not result.success and result.errors[0].code == code


def test_a_year_column_on_a_band_axis_prints_as_is() -> None:
    redefined = _STYLE + "    number: ',.0f'\n"
    for chart_type in ("bar", "line"):
        chart = f"    type: {chart_type}\n    x: year\n    y: revenue\n"
        formats = "    formats:\n      year: year\n"
        svg = render_board_to_svg(_board(chart, style=redefined, formats=formats))
        assert "2,025" not in svg and "2025" in svg


def test_a_scatter_x_tooltip_uses_the_x_columns_own_format() -> None:
    chart = "    type: scatter\n    x: orders\n    y: revenue\n"
    formats = "    formats:\n      revenue: currency\n      orders: integer\n"
    svg = render_board_to_svg(_board(chart, formats=formats))
    assert "Revenue: $1,234,567" in svg and "Orders: 1,234" in svg
    assert "Orders: $" not in svg
    year = "    type: scatter\n    x: year\n    y: revenue\n"
    svg = render_board_to_svg(_board(year, formats="    formats:\n      year: year\n"))
    assert "Year: 2025" in svg and "2,025" not in svg


def test_a_heatmap_cell_tooltip_uses_the_colors_column_format() -> None:
    chart = "    type: heatmap\n    x: month\n    y: year\n    color: revenue\n"
    svg = render_board_to_svg(_board(chart))
    assert "Revenue: $1,234,567" in svg


@pytest.mark.parametrize("family", ["bar", "line", "area", "scatter", "heatmap"])
def test_a_continuous_color_legend_uses_the_colors_column_format(family: str) -> None:
    axes = {
        "scatter": "x: orders\n    y: year",
        "heatmap": "x: month\n    y: year",
    }.get(family, "x: month\n    y: orders")
    chart = f"    type: {family}\n    {axes}\n    color: revenue\n"
    if family in ("line", "area"):  # which hide their legend for endpoint labels
        chart += "    style:\n      legend:\n        visible: true\n"
        chart += "      endpoint_labels:\n        visible: false\n"
    assert not any("$" in t for t in _tick_labels(_board(chart, formats="")))
    labels = _tick_labels(_board(chart))
    assert any("$" in t for t in labels), labels


def test_a_pie_slice_uses_the_theta_columns_format() -> None:
    chart = "    type: pie\n    theta: revenue\n    color: month\n"
    svg = render_board_to_svg(_board(chart))
    assert "Revenue: $1,234,567" in svg
    assert "Σ: $3,580,245" in svg


def test_an_axis_of_columns_with_different_formats_uses_number() -> None:
    chart = "    type: bar\n    x: month\n    y: [revenue, orders]\n"
    formats = "    formats:\n      revenue: currency\n      orders: integer\n"
    assert "$1,000,000" not in render_board_to_svg(_board(chart, formats=formats))


def test_an_axis_of_columns_sharing_a_format_uses_it() -> None:
    chart = "    type: bar\n    x: month\n    y: [revenue, orders]\n"
    formats = "    formats:\n      revenue: currency\n      orders: currency\n"
    assert "$1,000,000" in render_board_to_svg(_board(chart, formats=formats))


def test_a_name_the_board_does_not_define_is_a_compile_error() -> None:
    result = compile_board(
        _board(_BAR, formats="    formats:\n      revenue: curency\n")
    )
    assert not result.success
    error = result.errors[0]
    assert error.code == "ERR-QUERY-FORMAT-UNKNOWN"
    assert "Query 'q' formats.revenue names 'curency'" in error.message
    assert "currency" in error.message


def test_a_query_names_resolve_against_the_rendering_boards_formats() -> None:
    formats = "    formats:\n      revenue: arr_money\n"
    assert not compile_board(_board(_BAR, formats=formats)).success
    defined = _STYLE + "    arr_money: '$,.1f'\n"
    assert compile_board(_board(_BAR, style=defined, formats=formats)).success


@pytest.mark.parametrize("spec", ["$,.0f", "%b %Y"])
def test_a_spec_as_a_value_is_rejected_with_a_hint_to_name_it(spec: str) -> None:
    result = compile_board(
        _board(_BAR, formats=f"    formats:\n      revenue: '{spec}'\n")
    )
    assert not result.success
    error = result.errors[0]
    assert error.code == "ERR-QUERY-FORMAT-SPEC"
    assert "not a format name" in error.message
    assert "style.formats" in error.message


def test_a_values_query_naming_an_absent_column_errors_at_compile() -> None:
    result = compile_board(_board(_BAR, formats="    formats:\n      totl: currency\n"))
    assert not result.success
    error = result.errors[0]
    assert error.code == "ERR-QUERY-FORMAT-COLUMN-MISSING"
    assert "column 'totl'" in error.message
    assert "month, orders, revenue, year" in error.message


_SQL_BOARD = """
title: T
queries:
  q:
    sql: select 1 as revenue
    source: db
    formats:
      totl: currency
charts:
  c:
    query: q
    type: kpi
    value: revenue
rows:
  - c
"""


def _executor(rows: list[dict[str, int]]) -> Executor:
    compiled = compile_board(_SQL_BOARD)
    assert compiled.success
    ok = Mock(
        is_success=True,
        data=rows,
        column_descriptions=None,
        resolved_relations=None,
        truncated_reason=None,
    )
    registry = Mock()
    registry.execute.return_value = ok
    return Executor(
        compiled.board,
        adapter_registry=registry,
        query_registry=compiled.query_registry,
    )


def test_a_formats_column_missing_from_the_result_is_an_execution_error() -> None:
    with pytest.raises(QueryError) as caught:
        _executor([{"revenue": 1, "orders": 2}]).execute_query("q")
    assert caught.value.code.code == "ERR-QUERY-FORMAT-COLUMN-MISSING"
    assert "column 'totl'" in str(caught.value)
    assert "Returned columns: orders, revenue" in str(caught.value)


def test_a_zero_row_result_has_no_columns_to_check() -> None:
    assert _executor([]).execute_query("q") == []
