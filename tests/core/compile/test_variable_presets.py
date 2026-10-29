"""A daterange variable's `presets:` and the project-wide default menu."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile import compile

_BOARD = """
{style}
variables:
  span:
    {variable}
queries:
  q:
    type: values
    rows:
      - {{month: Jan, revenue: 100}}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""


def _compile(variable: str, style: str = ""):
    return compile(_BOARD.format(variable=variable, style=style))


def test_the_base_theme_menu_is_the_original_seven() -> None:
    result = _compile("input: daterange")

    assert result.success, result.errors
    assert result.board.resolved_style.variables.presets == (
        "today",
        "last_7_days",
        "last_28_days",
        "last_90_days",
        "last_12_months",
        "month_to_date",
        "year_to_date",
    )


def test_a_board_style_sets_the_default_menu() -> None:
    result = _compile(
        "input: daterange",
        "style:\n  variables:\n    presets:\n      - last_30_days\n      - this_year\n",
    )

    assert result.success, result.errors
    assert result.board.resolved_style.variables.presets == (
        "last_30_days",
        "this_year",
    )


def test_a_variable_sets_its_own_menu_and_default_stays_independent() -> None:
    result = _compile(
        "input: daterange\n    default: last_90_days\n    presets:\n"
        "      - last_30_days\n      - previous_3_quarters\n"
    )

    assert result.success, result.errors
    var = result.board.variable_registry["span"]
    assert var.presets == ("last_30_days", "previous_3_quarters")
    assert var.default == "last_90_days"


@pytest.mark.parametrize("bad", ["last_0_days", "last_week", "this_fiscal_month"])
def test_a_preset_outside_the_grammar_fails_compile(bad: str) -> None:
    result = _compile(f"input: daterange\n    presets:\n      - {bad}\n")

    assert not result.success
    assert "last_<N>_<unit>" in str(result.errors)
    assert bad in str(result.errors)


def test_a_bad_style_menu_entry_fails_compile() -> None:
    result = _compile(
        "input: daterange",
        "style:\n  variables:\n    presets:\n      - last_week\n",
    )

    assert not result.success
    assert "last_<N>_<unit>" in str(result.errors)


def test_presets_on_a_non_daterange_variable_fails_compile() -> None:
    result = _compile("input: text\n    presets:\n      - today\n")

    assert not result.success
    assert "presets" in str(result.errors)
    assert "daterange" in str(result.errors)


def test_an_empty_menu_fails_compile() -> None:
    result = _compile("input: daterange\n    presets: []\n")

    assert not result.success


def test_the_board_clock_carries_the_axis_fiscal_start_month() -> None:
    from datetime import UTC, datetime

    result = _compile(
        "input: daterange",
        "style:\n  charts:\n    axis_x:\n      fiscal_year_start_month: 7\n",
    )

    assert result.success, result.errors
    clock = result.board.clock(datetime(2026, 10, 20, 12, tzinfo=UTC))
    assert (clock.today.isoformat(), clock.fiscal_start_month) == ("2026-10-20", 7)
