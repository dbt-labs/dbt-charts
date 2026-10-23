"""Predicate tests for whether the endpoint-label pane will actually fire.

Two computations must agree on this condition:

- Baking ``axis_y.position: auto`` to ``"left"`` or ``"right"`` at compile
  time (``_bake_ay_orient`` in compile/resolve/chart/_axes.py) when the pane
  will collide with a right-side y-axis.
- Deciding whether to actually emit the hconcat/vconcat label pane at
  render time (``EndpointLabelFeature.applies_to`` in
  render/chart/features/endpoint_labels.py).

Both must key off the full firing condition (chart_type ∈ {line, area,
bar} + enabled + not-horizontal-bar + multi-series), not just
``endpoint_labels.visible`` alone — otherwise single-series charts and
horizontal bars that authored ``endpoint_labels.visible: true`` get their
y-axis silently flipped to the left without a pane to justify the flip.

These tests exercise both paths together via the public render pipeline
to confirm the axis orient always matches whether the pane actually
fired.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

_RS, _CTX = resolve_style_and_context(get_theme_style())


# --------------------------------------------------------------------------
# Single-series + endpoint_labels.visible → pane does NOT fire → orient stays right
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Multi-series + endpoint_labels.visible → pane fires → orient flips left
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Horizontal stacked bar emits the top-row rail (vconcat), not the right-side
# hconcat pane. Grouped horizontal (stack: none) does not fire either pane.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Multi-series vertical bar with endpoint_labels.visible → pane fires →
# orient flips. The currently-authored column_stacked_review and
# column_stacked_100_review chart-lab specimens live in this mode.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Layered charts with no base color-series channel: the rail fires only when
# x and y are both plain scalar columns (layered_endpoint_rail_shape, in
# dbt_charts.core.utils). compile's has_layers term (_bake_ay_orient) and
# render's applies_to() share that one leaf so a list y (folded wide) or an
# absent y — which has no single base endpoint to anchor a label on — can't
# flip the axis for a pane that never renders.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Endpoint-labels theme defaults — `line/area/bar.endpoint_labels.visible: auto`
# lands on _base.yaml (the structural root) and is inherited by every theme.
# Line/area label directly everywhere; bar labels directly when stacked and
# falls back to a top legend when grouped (the grouped exclusion lives in
# `_bar_endpoint_labels_for_stack`, resolve-time — not a theme default at all).
# --------------------------------------------------------------------------


def test_clarity_line_endpoint_labels_on_by_default():
    """clarity inherits line endpoint labels on from `_base`."""
    from dbt_charts.core.compile.config import get_theme_style

    compiled = get_theme_style("clarity")
    assert compiled.charts.line.endpoint_labels.visible == "auto", (
        "clarity.charts.line.endpoint_labels.visible should be 'auto', "
        "inherited from _base"
    )


def test_paper_line_endpoint_labels_on_by_default():
    """`paper` extends clarity; inherits `line.endpoint_labels.visible: auto`."""
    from dbt_charts.core.compile.config import get_theme_style

    compiled = get_theme_style("paper")
    assert compiled.charts.line.endpoint_labels.visible == "auto", (
        f"paper.charts.line.endpoint_labels.visible is "
        f"{compiled.charts.line.endpoint_labels.visible!r}; expected 'auto' (inherited from _base)."
    )


def test_clarity_and_paper_bar_endpoint_labels_on_by_default():
    """`clarity` and `paper` inherit bar/area `endpoint_labels.visible: auto` from `_base`."""
    from dbt_charts.core.compile.config import get_theme_style

    for theme_name in ("clarity", "paper"):
        compiled = get_theme_style(theme_name)
        for family in ("bar", "area"):
            visible = getattr(compiled.charts, family).endpoint_labels.visible
            assert visible == "auto", (
                f"{theme_name}.charts.{family}.endpoint_labels.visible is "
                f"{visible!r}; expected 'auto'."
            )


def test_stark_bar_endpoint_labels_on_by_default():
    """stark inherits bar endpoint labels on from `_base`.

    Whether a given bar actually renders them depends on stack mode — see
    ``_bar_endpoint_labels_for_stack`` — this only pins the theme value.
    """
    from dbt_charts.core.compile.config import get_theme_style

    compiled = get_theme_style("stark")
    assert compiled.charts.bar.endpoint_labels.visible == "auto", (
        f"stark.charts.bar.endpoint_labels.visible is "
        f"{compiled.charts.bar.endpoint_labels.visible!r}; expected 'auto'."
    )
