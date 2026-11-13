"""`type: funnel` is a bar drawn by the theme's funnel recipe.

The recipe is `style.charts.funnel`, applied over `charts.bar` the way
`charts.donut` applies over `charts.pie`; the author's own style wins over both.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
from dbt_charts.core.compile.normalize.single_series_allocation import (
    is_single_series_eligible,
)
from dbt_charts.core.compile.resolve import resolve

from ..._board_utils import make_test_resolved_chart
from ..._svg_render import render_board_to_svg

_ROWS = [
    {"stage": "Visit", "n": 1000},
    {"stage": "Signup", "n": 600},
    {"stage": "Buy", "n": 200},
]

_BOARD = """
title: Funnel
queries:
  q1:
    type: values
    rows:
      - {{stage: Visit, n: 1000}}
      - {{stage: Signup, n: 600}}
      - {{stage: Buy, n: 200}}
charts:
  c1:
    query: q1
    type: funnel
    x: stage
    y: n
{style}
rows:
  - c1
"""


def _normalized(style: str = "") -> BarChart:
    result = compile(_BOARD.format(style=style))
    assert result.success, result.errors
    chart = result.board.charts["c1"]
    assert isinstance(chart, BarChart)
    return chart


def _resolved(style: str = "") -> ResolvedBarChart:
    chart = make_test_resolved_chart(_normalized(style), _ROWS)
    assert isinstance(chart, ResolvedBarChart)
    return chart


def test_a_funnel_keeps_its_type_and_borrows_no_style_at_normalize() -> None:
    chart = _normalized()
    assert chart.type == "funnel"
    assert chart.style is None
    assert chart.stack is None


def test_the_theme_recipe_draws_a_centered_horizontal_bar_with_ribbons() -> None:
    chart = _resolved()
    assert chart.chart_type == "bar"
    assert chart.stack == "center"
    assert chart.orientation == "horizontal"
    assert chart.style.mark.ribbons.visible is True


def test_a_plain_bar_does_not_take_the_funnel_recipe() -> None:
    result = compile(_BOARD.format(style="").replace("funnel", "bar"))
    chart = make_test_resolved_chart(result.board.charts["c1"], _ROWS)
    assert isinstance(chart, ResolvedBarChart)
    assert chart.stack != "center"
    assert chart.style.mark.ribbons.visible is False


def _with_board_style(style: str) -> ResolvedBarChart:
    board = _BOARD.format(style="").replace("title: Funnel", f"title: Funnel\n{style}")
    result = compile(board)
    assert result.success, result.errors
    chart = resolve(
        result.board.charts["c1"],
        _ROWS,
        chart_style_context=result.board.chart_style_context,
    )
    assert isinstance(chart, ResolvedBarChart)
    return chart


def test_a_boards_funnel_style_restyles_its_funnels() -> None:
    style = "style:\n  charts:\n    funnel:\n      marks:\n        bar:\n          band_width: 0.8"
    assert _with_board_style(style).style.mark.band_width == 0.8


def test_a_boards_bar_style_reaches_its_funnels() -> None:
    style = "style:\n  charts:\n    bar:\n      marks:\n        bar:\n          max_size: 31"
    assert _with_board_style(style).style.mark.max_size == 31


def test_a_boards_bar_style_beats_the_themes_funnel_recipe() -> None:
    """A board's bar style is a higher layer than the theme's funnel recipe, as a
    board's pie style beats the theme's donut default."""
    style = "style:\n  charts:\n    bar:\n      marks:\n        bar:\n          band_width: 0.9"
    assert _with_board_style(style).style.mark.band_width == 0.9


@pytest.mark.parametrize(
    ("style", "check"),
    [
        (
            "    style:\n      marks:\n        bar:\n          ribbons:\n            visible: false",
            lambda c: c.style.mark.ribbons.visible is False,
        ),
        (
            "    style:\n      stack: zero",
            lambda c: c.stack == "zero",
        ),
    ],
    ids=["ribbons", "stack"],
)
def test_an_authored_style_wins_over_the_funnel_default(
    style: str, check: Callable[[ResolvedBarChart], bool]
) -> None:
    assert check(_resolved(style))


def test_a_funnel_draws_ribbons() -> None:
    svg = render_board_to_svg(_BOARD.format(style=""))
    assert 'data-dct-role="ribbon"' in svg


def test_a_funnel_labels_its_ribbons_by_percent_lost() -> None:
    svg = render_board_to_svg(_BOARD.format(style=""))
    assert 'class="role-ribbon-label"' in svg
    assert "\u221240%" in svg
    assert "\u221267%" in svg


def test_a_funnel_refuses_y_start() -> None:
    """A ranged bar cannot be centered, so a funnel with y_start would quietly stop being one."""
    result = compile(_BOARD.format(style="    y_start: n"))
    assert not result.success
    assert "funnel" in str(result.errors)


def _error_codes(board: str) -> list[str]:
    result = compile(board)
    assert not result.success
    return [e.code for e in result.errors]


@pytest.mark.parametrize("position", ["above", "top", "middle_aligned", "bottom"])
def test_a_funnel_refuses_a_label_position_other_than_middle(position: str) -> None:
    style = (
        "    style:\n      marks:\n        bar:\n          labels:\n"
        f"            position: {position}\n"
    )
    result = compile(_BOARD.format(style=style))
    assert [e.code for e in result.errors] == ["ERR-FUNNEL-LABEL-POSITION"]
    assert "places its labels on their marks" in str(result.errors[0])


def test_a_funnel_accepts_the_middle_label_position() -> None:
    style = (
        "    style:\n      marks:\n        bar:\n          labels:\n"
        "            position: middle\n"
    )
    assert compile(_BOARD.format(style=style)).success


def test_a_cohort_funnel_keeps_its_authored_label_position() -> None:
    style = (
        "    color: stage\n    style:\n      marks:\n        bar:\n          labels:\n"
        "            position: above\n"
    )
    assert compile(_BOARD.format(style=style)).success


def test_a_funnel_refuses_a_list_of_measures() -> None:
    board = _BOARD.format(style="").replace("y: n", "y: [n, m]")
    assert _error_codes(board) == ["ERR-FUNNEL-MULTIPLE-MEASURES"]


_BAND = "    style:\n      marks:\n        bar:\n          band_width: {band}"


def test_a_funnels_bars_default_thicker_than_its_ribbons() -> None:
    assert _resolved().style.mark.band_width > 0.5


@pytest.mark.parametrize("band", [0.5, 0.4])
def test_a_funnel_whose_bars_are_not_thicker_than_its_ribbons_is_refused(
    band: float,
) -> None:
    with pytest.raises(CompilationError, match=rf"band_width.*{band}.*above 0\.5"):
        _resolved(_BAND.format(band=band))


def test_a_plain_bar_may_be_thin() -> None:
    board = _BOARD.format(style=_BAND.format(band=0.4)).replace("funnel", "bar")
    result = compile(board)
    assert result.success, result.errors
    chart = make_test_resolved_chart(result.board.charts["c1"], _ROWS)
    assert chart.style.mark.band_width == 0.4


def _labels(style: str = "") -> tuple[object, object]:
    chart = _resolved(style)
    return chart.style.mark.labels, chart.style.mark.ribbons.labels


def test_a_funnel_labels_its_bars_in_the_middle_and_its_ribbons_by_percent_lost() -> (
    None
):
    bars, ribbons = _labels()
    assert bars.visible is True
    assert bars.position == "middle"
    assert bars.template is None
    assert ribbons.visible is True
    assert ribbons.template == "{{ percent | format('.0%') }}"


_TABLE = "    support_table:\n      entries:\n        - source: n\n"


def test_a_funnel_with_a_support_table_leaves_labeling_to_the_table() -> None:
    bars, ribbons = _labels(_TABLE)
    assert bars.visible is False
    assert ribbons.visible is False


def test_a_funnel_with_a_support_table_may_turn_labels_back_on() -> None:
    style = (
        _TABLE
        + "    style:\n      marks:\n        bar:\n          labels:\n            visible: true\n"
        "          ribbons:\n            labels:\n              visible: true\n"
    )
    bars, ribbons = _labels(style)
    assert bars.visible is True
    assert ribbons.visible is True
    assert "\u221240%" in render_board_to_svg(_BOARD.format(style=style))


_RIBBON_LABELS = (
    "    style:\n      marks:\n        bar:\n          ribbons:\n            visible: true\n"
    "            labels:\n              visible: true\n"
)


def test_ribbon_labels_on_a_plain_bar_are_refused() -> None:
    board = _BOARD.format(style=_RIBBON_LABELS).replace("funnel", "bar")
    result = compile(board)
    assert result.success, result.errors
    with pytest.raises(CompilationError, match=r"ribbons\.labels.*funnel"):
        make_test_resolved_chart(result.board.charts["c1"], _ROWS)


def _template(template: str) -> str:
    return (
        "    style:\n      marks:\n        bar:\n          labels:\n"
        f'            template: "{template}"\n'
    )


def test_a_secondary_value_in_parens_is_allowed() -> None:
    both = "{{ value | format(',d') }} ({{ percent | format('.0%') }})"
    assert _labels(_template(both))[0].template == both


@pytest.mark.parametrize(
    "template",
    [
        "{{ value | format('(,d') }} ({{ percent | format('.0%') }})",
        "{{ value | format(',d') }} ({{ percent | format('(.0%') }})",
    ],
)
def test_accounting_parens_beside_a_secondary_value_are_refused(template: str) -> None:
    with pytest.raises(CompilationError, match=r"parens"):
        _resolved(_template(template))


def test_accounting_parens_alone_are_allowed() -> None:
    assert _labels(_template("{{ value | format('(,d') }}"))[0].template


def test_a_vertical_funnel_is_refused() -> None:
    style = "    style:\n      orientation: vertical"
    with pytest.raises(CompilationError, match=r"type: bar.*stack: center"):
        _resolved(style)


_COLORED = """
title: Funnel
queries:
  q1:
    type: values
    rows:
      - {stage: Visit, seg: a, n: 1000}
      - {stage: Visit, seg: b, n: 800}
      - {stage: Signup, seg: a, n: 600}
      - {stage: Signup, seg: b, n: 300}
      - {stage: Buy, seg: a, n: 200}
      - {stage: Buy, seg: b, n: 100}
charts:
  c1:
    query: q1
    type: funnel
    x: stage
    y: n
    color: seg
rows:
  - c1
"""


def test_a_default_colored_funnel_renders() -> None:
    svg = render_board_to_svg(_COLORED)
    assert 'data-dct-role="ribbon"' in svg
    assert "role-ribbon-label" in svg
    assert "\u221240%" in svg, "the first series loses 40% at its second stage"


def _fill_of(svg: str, text: str) -> str:
    match = re.search(rf'fill="(#[0-9A-Fa-f]+)"[^>]*>{re.escape(text)}<', svg)
    assert match, f"no text {text!r} in the render"
    return match.group(1).lower()


@pytest.mark.parametrize("theme", ["clarity", "paper", "stark", "vivid", "neon"])
def test_a_funnels_stage_values_wear_its_stage_names_ink(theme: str) -> None:
    board = _BOARD.format(style=_TABLE).replace("title: Funnel", f"extends: {theme}")
    svg = render_board_to_svg(board)
    assert _fill_of(svg, "600") == _fill_of(svg, "Signup")


def test_a_single_series_funnel_takes_the_single_series_ink() -> None:
    assert is_single_series_eligible(_normalized())
