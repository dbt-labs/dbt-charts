"""Regression test: per-family theme overrides of TitleStyle work.

Mirrors ``test_per_family_theme_legend_patch.py``'s "family theme ->
chart-local" merge order and fast-path bypass; both fields share the
``_FAMILY_THEN_LOCAL_FIELDS`` cascade.

No built-in theme currently authors a per-family ``title`` override, so these
use a synthetic ``StylePatch`` instead of a real theme (unlike the legend
suite, which exercises ``clarity``'s real ``bar.legend.visible: true``).
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.normalized import BarChart, LineChart
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch, StylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)


def _board(patch: StylePatch | None = None):
    theme = get_theme_style(get_default_theme_name())
    return (
        resolve_chart_style_context(theme, patch)
        if patch
        else resolve_chart_style_context(theme)
    )


def test_family_theme_title_override_reaches_effective_title():
    """style.charts.bar.title.min_height overrides the board default for bar,
    with no chart-local override authored anywhere."""
    patch = StylePatch.model_validate(
        {"charts": {"bar": {"title": {"min_height": 40}}}}
    )
    board = _board(patch)
    effective = build_chart_style_context(board, BarChart(id="b", type="bar"))
    assert effective.title.min_height == 40.0
    # Untouched fields still fall back to the board default.
    assert effective.title.font.family == board.title.font.family


def test_line_title_inherits_board_default_without_family_override():
    """A family with no per-family title patch inherits the board default,
    same as legend's test_line_legend_visible_false_inherits_global."""
    patch = StylePatch.model_validate(
        {"charts": {"bar": {"title": {"min_height": 40}}}}
    )
    board = _board(patch)
    effective = build_chart_style_context(board, LineChart(id="l", type="line"))
    assert effective.title.min_height == board.title.min_height


def test_chart_local_title_wins_over_family_theme_title():
    """A chart-local style.title override wins over the family-theme one for
    the field it sets; fields neither sets keep falling back to the board
    default (three-tier merge: board -> family theme -> chart-local)."""
    patch = StylePatch.model_validate(
        {"charts": {"bar": {"title": {"min_height": 40, "overflow": "clip"}}}}
    )
    board = _board(patch)
    chart = BarChart(
        id="b",
        type="bar",
        style=BarChartStylePatch.model_validate({"title": {"min_height": 60}}),
    )
    effective = build_chart_style_context(board, chart)
    assert effective.title.min_height == 60.0  # chart-local wins
    assert effective.title.overflow == "clip"  # family theme survives
    assert effective.title.font.family == board.title.font.family  # board default


def test_no_title_override_returns_base_charts():
    """Without any family or chart-local title override, the fast path fires
    (same invariant as legend's test_no_chart_type_returns_base_charts)."""
    board = _board()
    effective = build_chart_style_context(board, LineChart(id="l", type="line"))
    assert effective is board


def test_board_title_field_survives_a_sparse_family_patch():
    """A board-level style.charts.title.compact_weight override must survive
    a family theme patch that leaves compact_weight unset -- pins that
    TitleStyle.compact_weight staying unmarked is load-bearing: re-adding
    Inherit(from_path="Style.title.compact_weight") would let apply_inherit
    fill the family patch's compact_weight from Style.title, silently
    overwriting the board's own override once merged."""
    patch = StylePatch.model_validate(
        {
            "charts": {
                "title": {"compact_weight": 700},
                "bar": {"title": {"min_height": 40}},
            }
        }
    )
    board = _board(patch)
    effective = build_chart_style_context(board, BarChart(id="b", type="bar"))
    assert effective.title.compact_weight == 700.0
    assert effective.title.min_height == 40.0
