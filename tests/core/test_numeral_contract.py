"""Numeral contract: plain digits are exact (never padded) and headlines are proportional."""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.format import sub_unit_spec_for_value
from dbt_charts.core.text.format_d3 import d3_format
from dbt_charts.core.text.predefined_formats import PREDEFINED_SPECS

from ._svg_render import render_board_to_svg


@pytest.mark.parametrize(
    ("value", "expected"),
    [(35.6, "35.6"), (12345, "12,345"), (1234.567, "1,234.567"), (0.5, "0.5")],
)
def test_number_full_never_pads_decimals(value: float, expected: str) -> None:
    assert d3_format(PREDEFINED_SPECS["number_full"].d3)(value) == expected


_KPI_BOARD = """
queries:
  q:
    type: values
    rows:
      - {revenue: 1234567.5}
charts:
  kpi:
    query: q
    type: kpi
    value: revenue
    label: Revenue
    style:
      value:
        format: number_full
rows:
  - cols: [kpi]
"""


@pytest.mark.parametrize("theme", ["stark", "clarity", "vivid", "neon"])
def test_kpi_headline_is_proportional(theme: str) -> None:
    svg = render_board_to_svg(f"theme: {theme}\n" + _KPI_BOARD)
    value_text = re.search(r"<tspan[^>]*>1,234,567\.5</tspan>", svg)
    assert value_text, "KPI value not found in the render"
    assert "Tabular" not in value_text.group(0)


@pytest.mark.parametrize("name", ["number", "compact"])
@pytest.mark.parametrize(("value", "expected"), [(0.6710941677, "0.67"), (0.5, "0.5")])
def test_si_members_print_two_unpadded_decimals_below_one(
    name: str, value: float, expected: str
) -> None:
    spec = sub_unit_spec_for_value(name, PREDEFINED_SPECS[name].d3, value, None)
    assert d3_format(spec)(value) == expected
