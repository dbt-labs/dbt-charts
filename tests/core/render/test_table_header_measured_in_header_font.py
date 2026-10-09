"""Table header text is measured in the font it is painted in.

The header paints in its own family. Column demands, word floors and the
one-line wrap check measuring it in the cell font would size columns for text
that is not the text drawn. Inter cells and a Source Serif 4 header give two
fonts whose widths differ (Source Serif 4 is the wider).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.render import render
from dbt_charts.core.render.chart.table_support import (
    measure_column_demands,
    measure_column_word_floors,
)

_CELL_FONT = get_font_measurer("Inter Variable")
_HEADER_FONT = get_font_measurer("Source Serif 4")
_HEADER = "quarterly"
_SIZE = 11.0

_BOARD = """\
style:
  frame:
    width: {width}
queries:
  rev:
    type: values
    rows:
      - {{quarterly: Al, fulfillment: Eng, department: Sales}}
      - {{quarterly: Bo, fulfillment: Ops, department: Ops}}
charts:
  tbl:
    query: rev
    type: table
{chart_style}rows:
  - tbl
"""
_SERIF_HEADER = (
    "    style:\n      header:\n        font:\n          family: Source Serif 4\n"
)
_QUARTERLY_HEADER = re.compile(r'font-weight="600"[^>]*>(<tspan[^>]*>)?Quarterl')


def setup_function() -> None:
    reset_config()


def teardown_function() -> None:
    reset_config()


def _measure_args() -> dict[str, object]:
    return {
        "columns": ["quarterly"],
        "column_configs": {"quarterly": TableColumnConfig()},
        "data": [{"quarterly": "Al"}],
        "measurer": _CELL_FONT,
        "header_measurer": _HEADER_FONT,
        "font_size": _SIZE,
        "header_font_size": _SIZE,
        "cell_pad": 6,
        "column_when_rules": {},
    }


def test_the_fonts_differ() -> None:
    """The tests below mean nothing if the two fonts measure the same."""
    assert _HEADER_FONT.measure(_HEADER, _SIZE) > _CELL_FONT.measure(_HEADER, _SIZE)


def test_header_demand_is_measured_in_the_header_font() -> None:
    cell_demands, header_demands = measure_column_demands(**_measure_args())

    assert header_demands["quarterly"] == pytest.approx(
        _HEADER_FONT.measure(_HEADER, _SIZE)
    )
    assert cell_demands["quarterly"] == pytest.approx(
        _CELL_FONT.measure("Al", _SIZE) + 12
    )


def test_header_word_floor_is_measured_in_the_header_font() -> None:
    floors = measure_column_word_floors(**_measure_args())

    assert floors["quarterly"] == pytest.approx(
        _HEADER_FONT.measure(_HEADER, _SIZE) + 12
    )


def _render_svg(yaml_text: str, local_project: Callable[..., FilesystemProject]) -> str:
    result = compile_board(yaml_text)
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    svg = render(result.board, executor, format="svg").output
    assert isinstance(svg, str)
    return svg


def test_header_wraps_when_only_its_own_font_overflows_the_column(
    local_project: Callable[..., FilesystemProject],
) -> None:
    """At this width "Quarterly" fits its column in Inter but not in Source
    Serif 4, so it wraps only when the one-line check measures the header font."""
    inter = _render_svg(_BOARD.format(width=310, chart_style=""), local_project)
    serif = _render_svg(
        _BOARD.format(width=310, chart_style=_SERIF_HEADER), local_project
    )

    inter_header = _QUARTERLY_HEADER.search(inter)
    serif_header = _QUARTERLY_HEADER.search(serif)
    assert inter_header is not None and serif_header is not None
    assert inter_header.group(1) is None
    assert serif_header.group(1) is not None
