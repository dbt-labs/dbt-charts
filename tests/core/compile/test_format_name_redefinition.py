"""A board restyles an engine format by redefining its name in ``style.formats``."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.format import (
    column_format,
    finalize_kpi_value_format,
    resolve_format_parts,
)
from dbt_charts.core.compile.format_value import format_kpi_parts, format_value
from dbt_charts.core.compile.models.primitives import FormatConfig, FormatTable
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

from .._svg_render import render_board_to_svg

_KPI_COMPACT = get_theme_style().charts.kpi.value.compact

_BOARD = """
title: T
{style}
queries:
  q:
    type: values
    rows:
      - {{month: 2026-01-01, revenue: 1234567, d: 2026-03-04}}
      - {{month: 2026-02-01, revenue: 2345678, d: 2026-03-05}}
  one:
    type: values
    rows:
      - {{revenue: 1234567, d: 2026-03-04}}
charts:
{chart}
rows:
  - c
"""


def _board(style: str, chart: str) -> str:
    return _BOARD.format(style=style, chart=chart)


_BAR = """
  c:
    query: q
    type: bar
    x: month
    y: revenue
"""
_TABLE = """
  c:
    query: q
    type: table
"""
_KPI = """
  c:
    query: one
    type: kpi
    value: revenue
"""
_KPI_DATE = """
  c:
    query: one
    type: kpi
    value: d
"""
_CURRENCY = "style:\n  formats:\n    currency: '$,.0f'"


def test_redefined_number_reaches_axis_table_cell_and_kpi() -> None:
    style = "style:\n  formats:\n    number: ',.0f'"
    assert "1,000,000" in render_board_to_svg(_board(style, _BAR))
    assert "1,234,567" in render_board_to_svg(_board(style, _TABLE))
    assert "1,234,567" in render_board_to_svg(_board(style, _KPI))


def test_redefined_currency_reaches_a_formatless_currency_slot() -> None:
    chart = _BAR + "    style:\n      number_format: currency\n"
    assert "$1,000,000" in render_board_to_svg(_board(_CURRENCY, chart))
    table = (
        _TABLE
        + "    style:\n      columns:\n        revenue:\n          format: currency\n"
    )
    svg = render_board_to_svg(_board(_CURRENCY, table))
    assert ">$</tspan>" in svg and "1,234,567" in svg


def test_redefined_date_short_reaches_kpi_and_table_dates() -> None:
    style = "style:\n  formats:\n    date_short: '%Y-%m-%d'"
    assert "2026-03-04" in render_board_to_svg(_board(style, _KPI_DATE))
    assert "2026-03-04" in render_board_to_svg(_board(style, _TABLE))


_NUMBER = "style:\n  formats:\n    number: ',.0f'"


@pytest.mark.parametrize(
    "chart",
    [
        _BAR + "    style:\n      number_format: '.0%'\n",
        _BAR + "    style:\n      axis_y:\n        labels:\n          format: '.0%'\n",
        _TABLE
        + "    style:\n      columns:\n        revenue:\n          format: '.0%'\n",
    ],
    ids=["number_format", "labels.format", "slot format"],
)
def test_a_slots_own_format_beats_the_redefinition(chart: str) -> None:
    redefined = render_board_to_svg(_board(_NUMBER, chart.replace(".0%", ",.0f")))
    own = render_board_to_svg(_board(_NUMBER, chart))
    assert "%" in own and "%" not in redefined
    assert "1,234,567" not in own


def test_redefined_currency_prints_its_own_spec_below_one() -> None:
    assert format_value(0.67, "currency", {"currency": "$.3~s"}) == "$670m"
    assert format_value(0.67, "currency", None) == "$0.67"


def test_engine_currency_swaps_to_the_boards_own_currency_full() -> None:
    formats = FormatTable(aliases={"currency_full": "$,.3f"}, currency="USD")
    assert format_value(0.67, "currency", formats) == "$0.670"
    parts = column_format("number", formats, "currency", values=[0.67])
    assert parts.spec == "$,.3f"


def test_redefined_name_is_not_house() -> None:
    redefined = resolve_format_parts("currency", {"currency": "$,.0f"})
    assert not redefined.is_house
    assert resolve_format_parts("currency", None).is_house


def test_kpi_keeps_engine_compaction_but_prints_a_redefined_number() -> None:
    engine = finalize_kpi_value_format(None, 1_500_000.0, None, compact=_KPI_COMPACT)
    assert engine is not None and engine.spec == ".2~s"
    assert finalize_kpi_value_format(None, 131.0, None, compact=_KPI_COMPACT) is None
    own = finalize_kpi_value_format(
        None, 131.5, {"number": ",.1f"}, compact=_KPI_COMPACT
    )
    assert own is not None and own.spec == ",.1f"
    assert format_kpi_parts(131.5, own, {"number": ",.1f"})[1] == "131.5"


@pytest.mark.parametrize(
    ("name", "spec"),
    [("currency", "%b %Y"), ("date_short", ",.0f"), ("number", "date_short")],
)
def test_redefinition_of_the_wrong_kind_is_rejected(name: str, spec: str) -> None:
    result = compile_board(_board(f"style:\n  formats:\n    {name}: '{spec}'", _BAR))
    assert not result.success
    assert result.errors[0].code == "ERR-FORMAT-KIND-MISMATCH"
    assert f"style.formats.{name}" in result.errors[0].message


def test_nearest_layer_wins_key_by_key() -> None:
    base = get_theme_style().model_copy(
        update={"formats": {"currency": "$.2f", "number": ".1f"}}
    )
    formats = resolve_chart_style_context(
        base,
        StylePatch.model_validate({"formats": {"currency": "$,.1f"}}),
        StylePatch.model_validate({"formats": {"currency": "$,.0f"}}),
    ).formats
    assert formats is not None
    assert formats.aliases == {"currency": "$,.0f", "number": ".1f"}
    assert format_value(1234.5, "currency", formats) == "$1,235"
    assert format_value(1234.5, "number", formats) == "1234.5"


def _currency_svg(extra_chart: str) -> str:
    chart = _BAR + "    style:\n      number_format: currency\n" + extra_chart
    return render_board_to_svg(_board(_CURRENCY, chart))


def test_redefined_currency_reaches_a_tooltip() -> None:
    assert "$1,234,567" in _currency_svg("")


def test_redefined_currency_reaches_a_mark_label() -> None:
    svg = _currency_svg(
        "      marks:\n        bar:\n          labels:\n"
        "            visible: true\n            format: currency\n"
    )
    assert svg.count("$1,234,567") > _currency_svg("").count("$1,234,567")


def test_redefined_currency_reaches_a_data_table_strip() -> None:
    svg = _currency_svg(
        "    support_table:\n      - source: revenue\n        format: currency\n"
    )
    assert svg.count("$1,234,567") > _currency_svg("").count("$1,234,567")


def test_a_redefined_number_reaches_table_cells_and_kpis_with_its_affix() -> None:
    formats = {"number": FormatConfig(spec=",.0f", suffix=" units")}
    cell = format_kpi_parts(1234.0, None, formats, default_number=True)
    assert cell[1:] == ("1,234", "units")
    kpi = finalize_kpi_value_format(None, 1234.0, formats, compact=_KPI_COMPACT)
    assert kpi is not None and (kpi.spec, kpi.suffix) == (",.0f", " units")


def test_a_number_redefined_as_another_engine_name_reaches_the_kpi() -> None:
    small = finalize_kpi_value_format(
        None, 0.25, {"number": "percent"}, compact=_KPI_COMPACT
    )
    assert small is not None and small.raw == "percent"
    money = finalize_kpi_value_format(
        None,
        1_500_000.0,
        FormatTable(aliases={"number": "currency"}, currency="USD"),
        compact=_KPI_COMPACT,
    )
    assert money is not None and money.raw == "currency"


@pytest.mark.parametrize(
    ("name", "target", "code"),
    [
        ("number", "percent_number", "ERR-FORMAT-NATIVE-IN-VEGA-SLOT"),
        ("date_short", "%Y %f", "ERR-FORMAT-TIME-DIRECTIVE-UNSUPPORTED"),
        ("number", "{prefix: '~'}", "ERR-FORMAT-INVALID"),
    ],
)
def test_an_unpaintable_or_empty_redefinition_is_rejected(
    name: str, target: str, code: str
) -> None:
    value = target if target.startswith("{") else f"'{target}'"
    result = compile_board(_board(f"style:\n  formats:\n    {name}: {value}", _BAR))
    assert not result.success
    assert result.errors[0].code == code


def test_a_number_aliasing_the_engine_number_keeps_its_affix_on_the_kpi() -> None:
    formats = {"number": FormatConfig(spec="number", suffix=" widgets")}
    kpi = finalize_kpi_value_format(None, 1_500_000.0, formats, compact=_KPI_COMPACT)
    assert kpi is not None and kpi.suffix == " widgets"


def test_a_number_aliasing_the_engine_number_keeps_its_affix_below_compaction() -> None:
    formats = {"number": FormatConfig(spec="number", suffix=" widgets")}
    kpi = finalize_kpi_value_format(None, 12.0, formats, compact=_KPI_COMPACT)
    assert kpi is not None and kpi.suffix == " widgets"


@pytest.mark.parametrize(
    "redefined",
    [
        FormatConfig(spec="number", suffix=" widgets"),
        FormatConfig(spec=",.0f", suffix=" widgets"),
    ],
)
def test_a_redefined_number_does_not_reach_a_date_headline(
    redefined: FormatConfig,
) -> None:
    assert (
        finalize_kpi_value_format(
            None, None, {"number": redefined}, compact=_KPI_COMPACT
        )
        is None
    )


def test_one_redefined_number_looks_the_same_on_kpi_cell_axis_and_mark_label() -> None:
    style = (
        "style:\n  formats:\n    number:\n      spec: ',.0f'\n      suffix: ' widgets'"
    )
    labeled = (
        _BAR + "    style:\n      marks:\n        bar:\n          labels:\n"
        "            visible: true\n"
    )
    kpi = render_board_to_svg(_board(style, _KPI))
    cell = render_board_to_svg(_board(style, _TABLE))
    chart = render_board_to_svg(_board(style, labeled))
    assert "1,234,567" in kpi and "widgets" in kpi
    assert "1,234,567" in cell and "widgets" in cell
    assert "1,000,000 widgets" in chart  # axis tick
    assert "1,234,567 widgets" in chart  # mark label


_DATE_SHORT_ISO = "style:\n  formats:\n    date_short: '%Y-%m-%d'"


def test_a_redefined_date_short_cannot_carry_an_affix() -> None:
    style = "style:\n  formats:\n    date_short:\n      spec: '%Y'\n      prefix: 'On '"
    result = compile_board(_board(style, _TABLE))
    assert not result.success
    assert result.errors[0].code == "ERR-FORMAT-AFFIX-TIME-UNSUPPORTED"


def test_a_cascaded_number_format_on_a_date_kpi_falls_back_to_the_redefined_date_short() -> (
    None
):
    style = (
        _DATE_SHORT_ISO + "\n  charts:\n    kpi:\n      value:\n        format: ',.0f'"
    )
    assert "2026-03-04" in render_board_to_svg(_board(style, _KPI_DATE))


_DONUT = """
  c:
    query: q
    type: donut
    theta: revenue
    color: month
    total:
      label: Total
"""


def _donut_total_svg(style: str, total_format: str = "") -> str:
    chart = _DONUT + (f"      format: {total_format}\n" if total_format else "")
    return render_board_to_svg(_board(style, chart))


def test_an_unformatted_donut_total_is_an_integer() -> None:
    assert "3,580,245" in _donut_total_svg("")


def test_a_spec_less_affix_donut_total_is_an_integer_too() -> None:
    svg = _donut_total_svg("", "{prefix: '#'}")
    assert "3,580,245" in svg


def test_a_redefined_number_reaches_both_donut_totals() -> None:
    style = "style:\n  formats:\n    number: ',.1f'"
    assert "3,580,245.0" in _donut_total_svg(style)
    assert "3,580,245.0" in _donut_total_svg(style, "{prefix: '#'}")


def test_a_destination_default_name_the_board_redefined_prints_whole() -> None:
    formats = {"integer": FormatConfig(spec=",.0f", suffix=" units")}
    total = column_format("number", formats, None, engine_default=lambda: "integer")
    assert (total.spec, total.suffix) == (",.0f", " units")
