"""Engine names for ids, durations, file sizes, coordinates and ratings."""

from __future__ import annotations

import json
import re

import pytest
import vl_convert as vlc

from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.format_value import format_kpi_parts, format_value
from dbt_charts.core.compile.models.primitives import FormatTable, ResolvedFormat
from dbt_charts.core.render.chart.vl_field_maps import compose_axis_format
from dbt_charts.core.text.format_d3 import format_d3
from dbt_charts.core.text.predefined_formats import PREDEFINED_NATIVE

from .._svg_render import render_board_svg_result, render_board_to_svg

_CELLS = [
    ("id", 1204331, "1204331"),
    ("id", 0, "0"),
    ("duration", 0, "0s"),
    ("duration", 45, "45s"),
    ("duration", 59, "59s"),
    ("duration", 60, "1m"),
    ("duration", 65, "1m 5s"),
    ("duration", 3599, "59m 59s"),
    ("duration", 3599.6, "1h"),
    ("duration", 3600, "1h"),
    ("duration", 5025, "1h 23m"),
    ("duration", 86400, "1d"),
    ("duration", 273600, "3d 4h"),
    ("duration", -5025, "−1h 23m"),
    ("duration", -0.2, "0s"),
    ("duration", None, "—"),
    ("bytes", 0, "0 B"),
    ("bytes", 950, "950 B"),
    ("bytes", 1000, "1 kB"),
    ("bytes", 999600, "1 MB"),
    ("bytes", 1534000, "1.53 MB"),
    ("bytes", -1534000, "−1.53 MB"),
    ("bytes", 2.5e12, "2.5 TB"),
    ("bytes", 0.5, "0.5 B"),
    ("bytes", None, "—"),
    ("latitude", 37.7749, "37.7749°"),
    ("latitude", -33, "−33°"),
    ("longitude", -122.41942, "−122.4194°"),
    ("longitude", -122, "−122°"),
    ("rating", 4.5, "4.5"),
    ("rating", 4, "4.0"),
]


@pytest.mark.parametrize(("name", "value", "text"), _CELLS)
def test_name_formats_cell(name: str, value: float | None, text: str) -> None:
    assert format_value(value, name) == text


def test_id_passes_a_string_cell_through_unchanged() -> None:
    svg = render_board_to_svg(
        """
title: T
queries:
  q:
    columns: [order_id, qty]
    values:
      - ["A-1204331", 1204331]
charts:
  t:
    query: q
    type: table
    style:
      columns:
        order_id:
          format: id
        qty:
          format: id
rows:
  - t
"""
    )
    assert "A-1204331" in svg
    assert "1204331" in svg


def test_kpi_parts_split_the_unit_from_the_number() -> None:
    assert format_kpi_parts(1534000, "bytes") == ("", "1.53", " MB")
    assert format_kpi_parts(37.7749, "latitude") == ("", "37.7749", "°")


def test_an_authored_suffix_replaces_the_engine_one() -> None:
    assert format_value(37.7749, {"spec": "latitude", "suffix": " N"}) == "37.7749 N"


def test_a_board_redefinition_drops_the_engine_suffix() -> None:
    assert (
        format_value(
            37.7749, "latitude", FormatTable(aliases={"latitude": ".1f"}, currency=None)
        )
        == "37.8"
    )


_DURATION_VALUES = [0, 1, 45, 59.6, 60, 65, 3599, 3600, 5025, 86400, 273600, -5025]
_BYTES_VALUES = [0, 0.5, 950, 999, 999600, 1000, 1534000, -1534000, 2.5e12, 3e24]


def _vega_text(expression: str, value: float) -> str:
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"v": value}]},
        "transform": [{"calculate": expression, "as": "label"}],
        "mark": "text",
        "encoding": {"text": {"field": "label", "type": "nominal"}},
    }
    match = re.search(r'aria-label="label: (.*?)"', vlc.vegalite_to_svg(spec))
    assert match, "no label rendered"
    return match.group(1)


@pytest.mark.parametrize(
    ("name", "values"),
    [("duration", _DURATION_VALUES), ("bytes", _BYTES_VALUES)],
)
def test_vega_arm_matches_the_python_formatter(name: str, values: list[float]) -> None:
    arm = PREDEFINED_NATIVE[name].vega
    assert arm is not None
    for value in values:
        number, unit = PREDEFINED_NATIVE[name].python(value)
        assert _vega_text(arm("datum.v"), value) == number + unit


_BOARD = """
title: T
queries:
  q:
    type: values
    rows:
      - {{day: a, secs: 3600}}
      - {{day: b, secs: 7200}}
charts:
  c:
    query: q
    type: bar
    x: day
    y: secs
    style:
      orientation: vertical
      axis_y:
        labels:
          format: {name}
rows:
  - c
"""


@pytest.mark.parametrize("name", ["duration", "bytes"])
def test_vega_capable_native_compiles_on_an_axis(name: str) -> None:
    result = compile_board(_BOARD.format(name=name))
    assert result.success, result.errors


def test_duration_paints_on_the_axis() -> None:
    svg = render_board_to_svg(_BOARD.format(name="duration"))
    assert "2h" in svg
    assert "1h" in svg


def test_axis_label_expr_comes_from_the_native_arm() -> None:
    d: dict[str, object] = {}
    compose_axis_format(d, ResolvedFormat(spec="duration"), True)
    assert "labelExpr" in d
    assert "format" not in d
    assert "duration" not in json.dumps(d["labelExpr"])


_SLOT_QUERIES = """
queries:
  cat:
    columns: [day, secs]
    formats:
      secs: NAME
    values:
      - [a, 3600]
      - [b, 7200]
  stacked:
    columns: [day, grp, secs]
    values:
      - [a, x, 3600]
      - [a, y, 1800]
      - [b, x, 7200]
      - [b, y, 5400]
  series:
    columns: [date, secs, more]
    formats:
      secs: NAME
      more: NAME
    values:
      - ["2024-01-01", 3600, 60]
      - ["2024-02-01", 7200, 120]
      - ["2024-03-01", 5400, 90]
"""

_SLOT_BOARDS = {
    "bar tooltip": """
query: cat
type: bar
x: day
y: secs
""",
    "vertical bar axis": """
query: cat
type: bar
x: day
y: secs
style:
  orientation: vertical
  axis_y:
    labels:
      format: NAME
""",
    "value labels": """
query: cat
type: bar
x: day
y: secs
style:
  marks:
    bar:
      labels:
        visible: true
        format: NAME
""",
    "stack totals": """
query: stacked
type: bar
x: day
y: secs
color: grp
style:
  stack: zero
  orientation: vertical
  marks:
    bar:
      total_label:
        visible: true
        format: NAME
""",
    "mirror": """
query: series
type: line
x: date
y: secs
style:
  axis_y:
    mirror:
      format: NAME
""",
    "line": """
query: series
type: line
x: date
y: secs
style:
  axis_y:
    labels:
      format: NAME
""",
    "area": """
query: series
type: area
x: date
y: secs
style:
  axis_y:
    labels:
      format: NAME
""",
    "scatter": """
query: series
type: scatter
x: date
y: secs
""",
    "wide scatter": """
query: series
type: scatter
x: date
y: [secs, more]
""",
    "temporal line": """
query: series
type: line
x: date
y: secs
""",
}


_PIE_BOARD = """
query: cat
type: donut
theta: secs
color: day
total:
  label: Total
style:
  total:
    value:
      format: NAME
"""


@pytest.mark.parametrize("name", ["duration", "bytes"])
def test_a_pie_total_paints_the_native_text(name: str) -> None:
    """The engine-drawn pie's center total prints the native's own text."""
    board = (
        f"title: T\n{_SLOT_QUERIES}charts:\n  c:\n"
        + "\n".join("    " + line for line in _PIE_BOARD.splitlines())
        + "\nrows:\n  - c\n"
    ).replace("NAME", name)
    assert f">{format_value(10800, name)}</text>" in render_board_to_svg(board)


@pytest.mark.parametrize(
    ("name", "texts"),
    [("duration", ("1h", "2h")), ("bytes", ("3.6 kB", "7.2 kB"))],
)
def test_value_labels_paint_the_native_text(name: str, texts: tuple[str, ...]) -> None:
    body = _SLOT_BOARDS["value labels"].replace("NAME", name)
    board = (
        f"title: T\n{_SLOT_QUERIES}charts:\n  c:\n"
        + "\n".join("    " + line for line in body.splitlines())
        + "\nrows:\n  - c\n"
    ).replace("NAME", name)
    svg = render_board_to_svg(board)
    for text in texts:
        assert text in svg


_NO_ARM_QUERIES = """
queries:
  cat:
    columns: [day, secs]
    values:
      - [a, 3600]
  grid:
    columns: [day, grp, secs]
    values:
      - [a, x, 3600]
  states:
    columns: [state_id, secs]
    values:
      - [6, 3600]
"""

_NO_ARM_CHARTS = {
    "support table": """
    query: cat
    type: bar
    x: day
    y: secs
    support_table:
      - source: secs
        format: NAME
""",
    "heatmap tooltip": """
    query: grid
    type: heatmap
    x: day
    y: grp
    color: secs
""",
    "geo tooltip": """
    query: states
    type: geoshape
    geo_source: us-states
    lookup: state_id
    color: secs
""",
}


@pytest.mark.parametrize("name", ["duration", "bytes"])
@pytest.mark.parametrize("slot", sorted(_NO_ARM_CHARTS))
def test_a_native_is_rejected_where_vega_cannot_paint_it(slot: str, name: str) -> None:
    chart = _NO_ARM_CHARTS[slot].replace("NAME", name)
    # These families read the board's tooltip format, not a chart-local one.
    board_style = (
        ""
        if slot == "support table"
        else f"style:\n  charts:\n    tooltip:\n      format: {name}\n"
    )
    board = (
        f"title: T\n{board_style}{_NO_ARM_QUERIES}charts:\n  c:{chart}rows:\n  - c\n"
    )
    [error] = render_board_svg_result(board).chart_errors
    assert "is a native formatter with no Vega arm" in error.message


@pytest.mark.parametrize("name", ["duration", "bytes"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_cell_prints_as_missing(name: str, value: float) -> None:
    assert format_value(value, name) == "—"


@pytest.mark.parametrize(
    ("name", "value", "text"), [("duration", 3600, "1h"), ("bytes", 1534000, "1.53 MB")]
)
def test_format_d3_paints_a_native_through_its_python_formatter(
    name: str, value: float, text: str
) -> None:
    assert format_d3(value, name) == text
