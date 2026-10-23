"""Tests for the `clarity`/`paper` themes' editorial voice, compared against `stark`.

The `clarity` theme adds an editorial voice, such as a serif KPI value font;
legend and axis-domain defaults come from `_base`. `paper` extends `clarity`
and inherits the same overrides on a warm canvas.
These tests assert the overrides exist (and propagate), without pinning the
specific aesthetic values they resolve to — those values are tunable and
shouldn't break the test when re-tuned (per dbt-charts/AGENTS.md: *"Don't pin
theme/default values in tests. Test structure, presence, and behavior under
override."*).
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)


def _themed(name: str):
    return resolve_style_and_context(get_theme_style(name))[1].pre_style


EDITORIAL_THEMES = ["clarity", "paper"]


# --------------------------------------------------------------------------
# KPI value serif font — `default` overrides the `stark` tabular sans with a
# serif. `cream` inherits via `extends: default`.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
def test_kpi_value_font_family_overrides_stark(theme_name: str) -> None:
    """The editorial-voiced themes set a serif `kpi.value.font.family` distinct from stark."""
    stark = _themed("stark").charts.kpi.value.font.family
    editorial = _themed(theme_name).charts.kpi.value.font.family
    assert editorial is not None, (
        f"{theme_name}.charts.kpi.value.font.family is None — "
        f"default.yaml did not set the override."
    )
    assert editorial != stark, (
        f"{theme_name}.charts.kpi.value.font.family resolved to the same "
        f"stack as stark ({stark!r}); the editorial-voice override "
        f"didn't apply."
    )
    # `default` KPI value uses a serif (paired with the proportional
    # narrative voice). Specific stack is tunable; "serif" in the stack
    # is the structural intent.
    assert "serif" in editorial.lower(), (
        f"{theme_name}.charts.kpi.value.font.family is {editorial!r}; "
        f"the editorial-voiced themes use a serif KPI value font."
    )


# --------------------------------------------------------------------------
# Editorial-voice overrides over `stark`. Boolean defaults (legend.visible,
# axis_*.domain.visible) are an explicit core AGENTS.md carve-out from the
# don't-pin rule — they assert UX-default identity, not aesthetic value, so
# a direct `is False` assertion is fine.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
def test_legend_auto_by_default(theme_name: str) -> None:
    theme = get_theme_style(theme_name)
    assert theme.charts.legend.visible == "auto", (
        f"{theme_name}.charts.legend.visible is "
        f"{theme.charts.legend.visible!r}; the editorial-voiced themes leave "
        f"the legend to the engine, which retires it behind direct labels."
    )


@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
@pytest.mark.parametrize("axis", ["axis_x", "axis_y"])
def test_axis_domain_not_visible_by_default(theme_name, axis):
    """Axis domain lines are hidden on the editorial-voiced themes — verified
    through the resolved cascade rather than at the pre-cascade theme layer.

    The hiding happens at `_base` (axis.line.visible: false, which cascades to
    axis_x and axis_y), so no theme needs to re-assert it. The
    user-facing contract — "domain lines off by default on the editorial
    themes" — is preserved.
    """
    ctx = resolve_chart_style_context(get_theme_style(theme_name))
    channel_type = "ordinal" if axis == "axis_x" else "quantitative"
    axis_style = resolved_axis_style(
        ctx, axis, channel_type, chart_type="", labeled=False
    )
    assert axis_style.line.visible is False, (
        f"resolved {theme_name}.charts.{axis}.line.visible is "
        f"{axis_style.line.visible!r}; the editorial-voiced themes must "
        f"hide axis domain lines after cascade resolution."
    )


# --------------------------------------------------------------------------
# Inheritance — `paper` picks up `clarity`'s overrides via `extends:`.
# Spot-check via the KPI override since it's the most distinctive.
# --------------------------------------------------------------------------


def test_paper_inherits_clarity_kpi_value_font():
    """`paper` inherits its parent `clarity`'s KPI value font."""
    parent = _themed("clarity").charts.kpi.value.font.family
    child = _themed("paper").charts.kpi.value.font.family
    assert child == parent, (
        f"paper.charts.kpi.value.font.family is {child!r}, "
        f"clarity parent is {parent!r}; the `extends: clarity` "
        f"inheritance chain didn't propagate the override."
    )


# --------------------------------------------------------------------------
# Legend visible propagates through resolve_style — the editorial-voiced
# themes must have visible=False survive the compiled→resolved conversion.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("theme_name", EDITORIAL_THEMES)
def test_legend_auto_propagates_through_resolve_style(theme_name: str) -> None:
    """legend.visible=auto on the compiled theme must survive resolve_style."""
    compiled = get_theme_style(theme_name)
    ctx = resolve_chart_style_context(compiled)
    assert ctx.pre_style.charts.legend.visible == "auto", (
        f"{theme_name}.charts.legend.visible lost during resolve_style: "
        f"{ctx.pre_style.charts.legend.visible!r}"
    )


# --------------------------------------------------------------------------
# Sparse y-axis tick count — the editorial themes set ticks.count: 6 on
# axis_quantitative so out-of-box bar/line/area charts render 5–8 ticks, not
# the ~20 VL picks by default on a 0–4000 range. Enforced via tickValues emit
# (VL's tickCount is advisory and frequently ignored).
# --------------------------------------------------------------------------
