"""``regional.locale`` sets the number separators on every painted surface."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

from ._svg_render import render_board_to_svg

_BOARD = """
title: Locale
regional:
  locale: fr-FR
queries:
  q:
    type: values
    rows:
      - {region: North, revenue: 1234567.5}
      - {region: South, revenue: 2345678.25}
charts:
  bars:
    query: q
    type: bar
    x: region
    y: revenue
    style:
      axis_y:
        labels:
          format: ",.0f"
  tbl:
    query: q
    type: table
    style:
      columns:
        revenue:
          format: ",.2f"
  kpi:
    query: q
    type: kpi
    value: revenue
    style:
      value:
        format: ",.1f"
rows:
  - cols: [bars, tbl, kpi]
"""


def test_fr_fr_separators_on_vega_and_python_surfaces() -> None:
    svg = render_board_to_svg(_BOARD)
    assert "2\xa0000\xa0000" in svg  # Vega axis tick
    assert "1\xa0234\xa0567,50" in svg  # Python table cell
    assert "1\xa0234\xa0567,5" in svg  # Python KPI
    assert "1,234,567" not in svg


def test_an_authored_dollar_spec_paints_the_locales_currency() -> None:
    svg = render_board_to_svg(_BOARD.replace('",.2f"', '"$,.2f"'))
    # The table paints the currency symbol as its own aligned element.
    assert ">1\xa0234\xa0567,50<" in svg
    assert ">€<" in svg
    assert ">$<" not in svg


def test_unknown_locale_is_rejected() -> None:
    result = compile(_BOARD.replace("fr-FR", "xx-YY"))
    assert not result.success
    assert any("xx-YY" in str(e) for e in result.errors)


def test_a_money_name_takes_the_locales_separators_and_the_boards_currency() -> None:
    board = _BOARD.replace("  locale: fr-FR\n", "  locale: fr-FR\n  currency: EUR\n")
    svg = render_board_to_svg(board.replace('",.2f"', "currency_full"))
    # A table column declares its unit once: the currency leads.
    assert ">€<" in svg and ">1\xa0234\xa0567,50<" in svg
    assert ">$<" not in svg


def _render_as(board: str, fmt: str) -> str:
    result = compile(board)
    assert result.success and result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    output = render(result.board, executor, format=fmt).output
    assert isinstance(output, str)
    return output


@pytest.mark.parametrize("fmt", ["text", "terminal"])
def test_the_locale_reaches_text_outputs(fmt: str) -> None:
    output = _render_as(_BOARD, fmt)
    assert "1,234,567" not in output
    assert "1\xa0234\xa0567" in output


def test_an_anchored_currency_prints_once_in_text() -> None:
    board = _BOARD.replace("  locale: fr-FR\n", "  locale: fr-FR\n  currency: EUR\n")
    board = board.replace('format: ",.0f"', "format: currency")
    output = _render_as(board, "text")
    # Prose repeats the unit: EUR follows the digits, never leads them too.
    assert "€" in output and not re.search(r"€\d", output), output


_MAGNITUDE_BOARD = """
title: Magnitudes
regional:
  locale: de-DE
queries:
  q:
    type: values
    rows:
      - {region: North, revenue: 1500000}
      - {region: South, revenue: 2500000}
  total:
    type: values
    rows:
      - {revenue: 4000000}
charts:
  bars:
    query: q
    type: bar
    x: region
    y: revenue
  kpi:
    query: total
    type: kpi
    value: revenue
rows:
  - cols: [bars, kpi]
"""


def test_a_named_locale_abbreviates_magnitudes_in_its_language() -> None:
    texts = re.findall(r">([^<>]+)<", render_board_to_svg(_MAGNITUDE_BOARD))
    assert "1\xa0Mio." in texts  # Vega axis tick
    assert "Mio." in texts  # KPI suffix lane
    assert not [t for t in texts if re.search(r"(^|\d\s?)(M|mn)$", t)], texts


def test_an_anchored_table_column_keeps_a_word_on_every_row() -> None:
    board = """
title: Anchors
regional:
  locale: es-ES
queries:
  q:
    type: values
    rows:
      - {region: A, v: 441200}
      - {region: B, v: 3200000}
      - {region: C, v: 456100}
charts:
  t:
    query: q
    type: table
    style:
      columns:
        v:
          format: number
rows:
  - t
"""
    texts = re.findall(r">([^<>]+)<", render_board_to_svg(board))
    assert texts.count("mil") == 3, texts


_BILLIONS_BOARD = """
title: Billions
regional:
  locale: LOCALE
queries:
  q:
    type: values
    rows:
      - {region: A, v: 300000000}
      - {region: B, v: 9800000000}
charts:
  bars:
    query: q
    type: bar
    x: region
    y: v
    style:
      orientation: vertical
rows:
  - bars
"""


@pytest.mark.parametrize("locale", ["ru-RU", "uk-UA", "es-ES", "mk-MK", "da-DK"])
def test_a_long_locale_word_renders_on_an_axis(locale: str) -> None:
    # Passes when the render does not raise.
    render_board_to_svg(_BILLIONS_BOARD.replace("LOCALE", locale))


def test_a_custom_locale_paints_its_own_words() -> None:
    board = _MAGNITUDE_BOARD.replace(
        "  locale: de-DE\n",
        "  locale:\n"
        '    decimal: ","\n'
        '    thousands: "."\n'
        "    grouping: [3]\n"
        '    currency: ["", " €"]\n'
        '    analytic: [" Tsd", " Mn", " Mrd", " Bn"]\n'
        '    narrative: ["tsd", "mio", "mrd", "bio"]\n',
    )
    texts = re.findall(r">([^<>]+)<", render_board_to_svg(board))
    assert "1mio" in texts  # Vega axis tick that repeats its unit
    assert "mio" in texts  # KPI suffix lane
