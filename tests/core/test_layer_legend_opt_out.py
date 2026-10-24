"""A layer can decline its legend entry (`legend: false`), and a reference line
(a layer whose y never changes) draws no automatic point marks."""

from __future__ import annotations

import re

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.models.chart.authored._layer import (
    CartesianLayer,
    LineLayer,
)

from ._svg_render import legend_label_order, render_board_to_svg

_layer_ta: TypeAdapter[CartesianLayer] = TypeAdapter(CartesianLayer)

_REGIONS = {
    "North": [69, 74, 72, 68, 67, 72],
    "South": [56, 54, 50, 49, 54, 64],
}


def _board(layer: str) -> str:
    rows = "\n".join(
        f"      - {{region: {region}, month: {month}, revenue: {value}}}"
        for region, values in _REGIONS.items()
        for month, value in enumerate(values, start=1)
    )
    return f"""
queries:
  q:
    type: values
    rows:
{rows}
  target:
    type: values
    rows:
      - {{month: 1, goal: 80}}
      - {{month: 6, goal: 80}}
  growth:
    type: values
    rows:
      - {{month: 1, goal: 60}}
      - {{month: 6, goal: 90}}
charts:
  c:
    query: q
    type: line
    x: month
    y: revenue
    color: region
    style:
      legend:
        position:
          edge: right
    layers:
{layer}
rows:
  - c
"""


def _target(extra: str = "") -> str:
    return f"""      - type: line
        query: target
        x: month
        y: goal
        label: Target
{extra}"""


def _lines(svg: str) -> int:
    return len(re.findall(r'class="mark-line role-mark layer_\d+_marks"', svg))


def _target_dots(svg: str) -> int:
    """Painted point marks of the Target layer, not its invisible hover targets."""
    return len(
        re.findall(
            r'<circle [^>]*opacity="1" data-dct-role="point" aria-label="[^"]*Target: ',
            svg,
        )
    )


def test_layer_legend_defaults_to_true() -> None:
    layer = _layer_ta.validate_python({"type": "line", "y": "goal"})
    assert isinstance(layer, LineLayer)
    assert layer.legend is True


def test_a_layer_takes_a_legend_entry_by_default() -> None:
    svg = render_board_to_svg(_board(layer=_target()))
    assert legend_label_order(svg) == ["North", "South", "Target"]


def test_legend_false_drops_the_entry_and_keeps_the_mark() -> None:
    with_entry = render_board_to_svg(_board(layer=_target()))
    declined = render_board_to_svg(_board(layer=_target("        legend: false\n")))
    assert legend_label_order(declined) == ["North", "South"]
    assert _lines(declined) == _lines(with_entry)


def test_a_reference_line_draws_no_automatic_points() -> None:
    flat = render_board_to_svg(_board(layer=_target()))
    sloped = render_board_to_svg(
        _board(layer=_target().replace("query: target", "query: growth"))
    )
    assert _target_dots(flat) == 0
    assert _target_dots(sloped) == 2


def test_a_reference_line_keeps_authored_points() -> None:
    authored = _target(
        "        style:\n          marks:\n            point:\n              size: 60\n"
    )
    assert _target_dots(render_board_to_svg(_board(layer=authored))) == 2


_BAR_ROWS = """
queries:
  q:
    type: values
    rows:
      - {x: 1, name: A, revenue: 10, goal: 12, grade: 1}
      - {x: 2, name: B, revenue: 14, goal: 12, grade: 2}
      - {x: 3, name: C, revenue: 9, goal: 12, grade: 3}
"""


def _bar_board(x: str, layer: str) -> str:
    return f"""{_BAR_ROWS}
charts:
  c:
    query: q
    type: bar
    x: {x}
    y: revenue
    layers:
{layer}
rows:
  - c
"""


@pytest.mark.parametrize("x", ["name", "x"])
@pytest.mark.parametrize("layer_type", ["scatter", "bar", "line"])
def test_a_declined_layer_on_a_bar_base_draws_no_entry(x: str, layer_type: str) -> None:
    layer = f"      - type: {layer_type}\n        y: goal\n        label: Goal\n"
    kept = render_board_to_svg(_bar_board(x=x, layer=layer))
    declined = render_board_to_svg(
        _bar_board(x=x, layer=layer + "        legend: false\n")
    )
    assert "Goal" in legend_label_order(kept)
    assert legend_label_order(declined) == ["Revenue"]


def test_a_numerically_colored_layer_declines_its_gradient_legend() -> None:
    layer = "      - type: scatter\n        y: goal\n        color: grade\n"
    kept = render_board_to_svg(_bar_board(x="name", layer=layer))
    declined = render_board_to_svg(
        _bar_board(x="name", layer=layer + "        legend: false\n")
    )
    assert "layer_grad" in kept
    assert "layer_grad" not in declined


def _field_colored(first: str, second: str) -> str:
    return f"""
queries:
  q:
    type: values
    rows:
      - {{month: 1, region: North, revenue: 5}}
      - {{month: 2, region: North, revenue: 6}}
  plan:
    type: values
    rows:
      - {{month: 1, kind: Plan, goal: 7}}
      - {{month: 2, kind: Plan, goal: 8}}
charts:
  c:
    query: q
    type: line
    x: month
    y: revenue
    color: region
    layers:
      - type: line
        query: plan
        x: month
        y: goal
        color: kind
{first}
      - type: line
        query: plan
        x: month
        y: goal
        color: kind
{second}
rows:
  - c
"""


def test_a_field_colored_layer_declines_its_values() -> None:
    svg = render_board_to_svg(_field_colored("        legend: false", ""))
    assert legend_label_order(svg) == ["North", "Plan"]
    svg = render_board_to_svg(
        _field_colored("        legend: false", "        legend: false")
    )
    assert legend_label_order(svg) == ["North"]


def test_a_later_layer_keeping_its_entries_restores_a_declined_value() -> None:
    svg = render_board_to_svg(_field_colored("        legend: false", ""))
    assert "Plan" in legend_label_order(svg)


def test_a_declined_layer_does_not_title_the_legend() -> None:
    def board(extra: str) -> str:
        return f"""
queries:
  q:
    type: values
    rows:
      - {{month: 1, revenue: 5}}
      - {{month: 2, revenue: 6}}
  plan:
    type: values
    rows:
      - {{month: 1, kind: Plan, goal: 7}}
      - {{month: 2, kind: Plan, goal: 8}}
charts:
  c:
    query: q
    type: line
    x: month
    y: revenue
    style:
      legend:
        position:
          edge: right
    layers:
      - type: line
        query: plan
        x: month
        y: goal
        color: kind
{extra}
rows:
  - c
"""

    kept = render_board_to_svg(board(""))
    declined = render_board_to_svg(board("        legend: false"))
    assert "role-legend-title" in kept
    assert "role-legend-title" not in declined
