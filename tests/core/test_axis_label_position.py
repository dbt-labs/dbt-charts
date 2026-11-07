"""``axis_y.labels.position`` is a value-axis-only field that survives the axis cascade."""

from __future__ import annotations

import re
from typing import Literal

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.style.theme import (
    AxisLabelStyle,
    AxisYLabelStyle,
    DimensionLabelStyle,
)
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    _merge_axis_cascade,
    build_resolved_axis,
    resolved_axis_style,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

from ._svg_render import render_board_to_svg


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def test_value_labels_accept_middle_and_above() -> None:
    assert AxisYLabelStyle(position="beside_gridline").position == "beside_gridline"
    assert AxisYLabelStyle(position="above_gridline").position == "above_gridline"


def test_value_labels_reject_any_other_position() -> None:
    with pytest.raises(ValidationError):
        AxisYLabelStyle(position="below")


@pytest.mark.parametrize("model", [AxisLabelStyle, DimensionLabelStyle])
def test_position_is_absent_from_other_label_styles(model: type) -> None:
    with pytest.raises(ValidationError):
        model(position="above_gridline")


def test_authored_position_reaches_the_resolved_value_axis() -> None:
    theme = get_theme_style()
    axis_y = theme.charts.axis_y.model_copy(
        update={
            "labels": theme.charts.axis_y.labels.model_copy(
                update={"position": "above_gridline"}
            )
        }
    )
    patched = theme.model_copy(
        deep=True,
        update={
            "charts": theme.charts.model_copy(deep=True, update={"axis_y": axis_y})
        },
    )
    charts = resolve_chart_style_context(patched)
    axis = resolved_axis_style(
        charts, "axis_y", "quantitative", chart_type="", labeled=False
    )
    assert axis.labels.position == "above_gridline"
    x_axis = resolved_axis_style(
        charts, "axis_x", "temporal", chart_type="", labeled=False
    )
    assert x_axis.labels.position is None


def _resolved(position: str, edge: Literal["left", "right"], **labels: object):
    theme = get_theme_style()
    axis_y = theme.charts.axis_y.model_copy(
        update={
            "labels": theme.charts.axis_y.labels.model_copy(
                update={"position": position, **labels}
            )
        }
    )
    patched = theme.model_copy(
        deep=True,
        update={
            "charts": theme.charts.model_copy(deep=True, update={"axis_y": axis_y})
        },
    )
    merged, _ = _merge_axis_cascade(
        resolve_chart_style_context(patched),
        "axis_y",
        "quantitative",
        chart_type="",
        labeled=False,
    )
    return build_resolved_axis(
        merged, edge=edge, is_quantitative=True, chart_id="t", format=None, formats=None
    )


@pytest.mark.parametrize("position", ["beside_gridline", "above_gridline"])
def test_position_leaves_a_left_axis_on_its_default_anchor(
    position: Literal["beside_gridline", "above_gridline"],
) -> None:
    assert _resolved(position, "left").labels.align is None


def test_an_authored_align_holds_under_above() -> None:
    assert _resolved("above_gridline", "left", align="left").labels.align == "left"


MONTHS = """
queries:
  monthly:
    type: values
    columns: [month, segment, revenue, share]
    values:
      - ['2025-01-01', Direct, 24000, 12]
      - ['2025-02-01', Direct, 31000, 18]
      - ['2025-03-01', Direct, 27000, 15]
      - ['2025-01-01', Partner, 17000, 9]
      - ['2025-02-01', Partner, 21000, 14]
      - ['2025-03-01', Partner, 20000, 11]
"""


def _grid_rules(svg: str) -> list[tuple[float, float, float, float]]:
    ink = _paper_value_axis(is_quantitative=True).grid.color
    pattern = (
        r'<line x1="([\d.-]+)" y1="([\d.-]+)" x2="([\d.-]+)" y2="([\d.-]+)" '
        f'stroke="{ink}"'
    )
    return [
        (float(x1), float(y1), float(x2), float(y2))
        for x1, y1, x2, y2 in re.findall(pattern, svg)
    ]


def test_a_paper_left_ghost_rule_meets_the_panel_rule_on_its_row() -> None:
    svg = render_board_to_svg(
        f"""
theme: paper
{MONTHS}
charts:
  c:
    query: monthly
    type: line
    x: month
    y: revenue
    multiples:
      columns: segment
rows:
  - c
"""
    )
    panel = re.search(
        r'translate\(([\d.]+),([\d.]+)\)"[^>]*>\s*<g[^>]*translate\(([\d.]+),0\)', svg
    )
    assert panel is not None
    origin_x = float(panel[1]) + float(panel[3])
    origin_y = float(panel[2])
    rules = _grid_rules(svg)
    ghosts = [rule for rule in rules if rule[0] != 0.5]
    inner = {rule[1] + origin_y for rule in rules if rule[0] == 0.5}
    assert ghosts
    for _, y1, x2, _ in ghosts:
        assert x2 == origin_x + 0.5
        assert y1 in inner


def test_a_paper_base_moved_left_by_a_right_layer_ends_on_a_shared_edge() -> None:
    svg = render_board_to_svg(
        """
theme: paper
queries:
  monthly:
    type: values
    columns: [month, revenue, share]
    values:
      - ['2025-01-01', 24000, 12]
      - ['2025-02-01', 31000, 18]
      - ['2025-03-01', 27000, 15]
charts:
  c:
    query: monthly
    type: bar
    x: month
    y: revenue
    layers:
      - type: line
        y: share
        axis_y:
          position: right
rows:
  - c
"""
    )
    left = [
        (float(x), anchor)
        for x, anchor in re.findall(
            r'<text x="([\d.]+)" y="[\d.]+" text-anchor="(\w+)"[^>]*>\s*[\d,]+\s*<', svg
        )
        if float(x) < 100
    ]
    assert len(left) > 1
    assert {anchor for _, anchor in left} == {"end"}
    assert len({x for x, _ in left}) == 1


def test_a_paper_log_axis_runs_only_its_labeled_rules_into_the_gutter() -> None:
    svg = render_board_to_svg(
        """
theme: paper
queries:
  population:
    type: values
    columns: [year, population]
    values:
      - ['1800', 990000000]
      - ['1950', 2536000000]
      - ['2025', 8200000000]
charts:
  c:
    query: population
    type: line
    x: year
    y: population
    style:
      axis_y:
        scale:
          continuous:
            type: log
rows:
  - c
"""
    )
    rules = _grid_rules(svg)
    horizontal = [rule for rule in rules if rule[1] == rule[3]]
    plot_edge = min(rule[2] for rule in horizontal)
    reaching = [rule for rule in horizontal if rule[2] > plot_edge]
    labels = re.findall(r">\s*\d+(?:\.\d+)?[MB]\s*<", svg)
    assert 0 < len(reaching) == len(labels) < len(horizontal)


def test_a_paper_left_axis_keeps_its_house_ruler_and_endpoint_precision() -> None:
    svg = render_board_to_svg(
        """
theme: paper
queries:
  weekly:
    type: values
    columns: [week, series, revenue]
    values:
      - ['2025-01-06', Core, 141000]
      - ['2025-01-13', Core, 152000]
      - ['2025-01-20', Core, 155500]
      - ['2025-01-06', Partner, 160000]
      - ['2025-01-13', Partner, 171000]
      - ['2025-01-20', Partner, 177500]
charts:
  c:
    query: weekly
    type: line
    x: week
    y: revenue
    color: series
    style:
      axis_y:
        labels:
          format: currency
rows:
  - c
"""
    )
    texts = [
        re.sub(r"<[^>]*>", "", text).strip()
        for text in re.findall(r"<text[^>]*>.*?</text>", svg, re.S)
    ]
    assert "$180K" in texts
    assert not [text for text in texts if re.fullmatch(r"\$1[4-7]0k", text)]
    assert "$155.5K Core" in texts


def _paper_value_axis(is_quantitative: bool):
    merged, _ = _merge_axis_cascade(
        resolve_chart_style_context(get_theme_style("paper")),
        "axis_y",
        "quantitative" if is_quantitative else "band",
        chart_type="",
        labeled=False,
    )
    return build_resolved_axis(
        merged,
        edge="left",
        is_quantitative=is_quantitative,
        chart_id="t",
        format=None,
        formats=None,
    )


def test_paper_sets_above_on_a_quantitative_value_axis() -> None:
    assert _paper_value_axis(is_quantitative=True).labels.position == "above_gridline"


def test_a_band_axis_resolves_without_a_position() -> None:
    assert _paper_value_axis(is_quantitative=False).labels.position is None


def _scatter_against(y: str, position: str) -> str:
    svg = render_board_to_svg(
        f"""
theme: paper
queries:
  q:
    type: values
    columns: [hours, team, when]
    values:
      - [3, Alpha, '2024-01-01']
      - [5, Beta, '2024-02-01']
      - [8, Gamma, '2024-03-01']
charts:
  c:
    query: q
    type: scatter
    x: hours
    y: {y}
    style:
      axis_y:
        grid:
          visible: true
        labels:
          position: {position}
rows:
  - c
"""
    )
    return re.sub(r'data-rendered-at="[^"]*"', "", svg)


@pytest.mark.parametrize("y", ["team", "when"])
def test_a_nominal_or_date_scatter_y_ignores_above(y: str) -> None:
    assert _scatter_against(y, "above_gridline") == _scatter_against(
        y, "beside_gridline"
    )
