"""TDD tests for V2 structural parity fixes.

Fix 1: rhythm_slot propagation to single-series fill
Fix 2: endpoint labels only fire when authored visible
Fix 3: area static fill/stroke suppressed with color channel
Fix 4: combo (layered) line point markers
Fix 6: endpoint label pane gets explicit width + font props
Fix 7: vconcat (top_rail) horizontal bar endpoint labels render at slot width
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedStyleChannel,
)
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve.style.typography import concrete_title_style


def _default_legend():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.legend


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _default_charts():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


_DEFAULT_CHARTS = _default_charts()
_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)
_B: dict[str, Any] = {
    "variable_dependencies": frozenset(),
    "palette": (),
    "resolved_channels": {},
    "legend": _default_legend(),
    "background": _DEFAULT_CHARTS.background,
    "canvas": _DEFAULT_CHARTS.ink_canvas,
    "title_style": concrete_title_style(_DEFAULT_CHARTS),
    "layout_padding": _ZERO_PADDING,
}
_C: dict[str, Any] = dict(_B)


# ---------------------------------------------------------------------------
# Fix 1 — rhythm_slot propagation
# ---------------------------------------------------------------------------


def test_base_chart_fields_has_rhythm_slot() -> None:
    """_BaseChartFields carries rhythm_slot with default 0."""
    from dbt_charts.core.compile.models.chart.normalized._base import _BaseChartFields

    assert hasattr(_BaseChartFields, "model_fields")
    assert "rhythm_slot" in _BaseChartFields.model_fields


def test_bar_chart_normalized_inherits_rhythm_slot() -> None:
    """BarChart inherits rhythm_slot = 0 by default from _BaseChartFields."""
    from dbt_charts.core.compile.models.chart.normalized.bar import BarChart

    chart = BarChart(
        id="b1",
        type="bar",
        query_name="q",
    )
    assert chart.rhythm_slot == 0


def test_effective_single_series_fill_uses_rhythm_slot() -> None:
    """_effective_single_series_fill returns pal[rhythm_slot % len(pal)] for non-override case."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.chart._palette import (
        _effective_single_series_fill,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    rcs = resolve_chart_style_context(get_theme_style())
    pal = rcs.single_series_palette
    assert len(pal) >= 1

    # Slot 0 → pal[0]
    assert _effective_single_series_fill(rcs, None, rhythm_slot=0) == pal[0]
    # Slot 1 → pal[1 % len(pal)]
    expected_slot1 = pal[1 % len(pal)]
    assert _effective_single_series_fill(rcs, None, rhythm_slot=1) == expected_slot1


def test_resolved_bar_chart_uses_rhythm_slot_for_single_series_fill() -> None:
    """resolve() on a bar chart with rhythm_slot=2 uses pal[2 % len] for single_series_fill."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.normalized.bar import BarChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_style = resolve_chart_style_context(get_theme_style())
    pal = board_style.single_series_palette
    if len(pal) < 2:
        pytest.skip(
            "single_series_palette has fewer than 2 slots; rhythm test non-deterministic"
        )

    chart = BarChart(id="b2", type="bar", query_name="q", x="date", y="value")
    chart = chart.model_copy(update={"rhythm_slot": 2})

    data = [{"date": "Jan", "value": 100}]
    resolved = resolve(chart, data, board_style)
    from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart

    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.style.single_series_fill == pal[2 % len(pal)]


# ---------------------------------------------------------------------------
# Fix 2 — endpoint labels only fire when authored visible
# ---------------------------------------------------------------------------


def test_endpoint_labels_resolved_bar_respects_authored_visibility() -> None:
    """resolve() on a bar chart without endpoint_labels authored sets endpoint_labels_visible=False."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.normalized.bar import BarChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_style = resolve_chart_style_context(get_theme_style())
    chart = BarChart(
        id="b", type="bar", query_name="q", x="date", y="value", color="region"
    )
    data = [{"date": "Jan", "value": 100, "region": "East"}]
    resolved = resolve(chart, data, board_style)
    from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart

    assert isinstance(resolved, ResolvedBarChart)
    # Theme default is visible=False; author didn't set it → should be False
    assert resolved.style.endpoint_labels.visible is False


# ---------------------------------------------------------------------------
# Fix 3 — area static fill/stroke suppressed with color channel
# ---------------------------------------------------------------------------


_C_WITH_COLOR: dict[str, Any] = {
    "variable_dependencies": frozenset(),
    "palette": (),
    "resolved_channels": {
        "color": ResolvedStyleChannel(
            channel="color", mode="series", data_field="region"
        )
    },
    "legend": _default_legend(),
    "background": _DEFAULT_CHARTS.background,
    "canvas": _DEFAULT_CHARTS.ink_canvas,
    "title_style": concrete_title_style(_DEFAULT_CHARTS),
    "layout_padding": _ZERO_PADDING,
}


# ---------------------------------------------------------------------------
# Fix 6 — endpoint label pane gets explicit width + font props
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fix 7 — vconcat (top_rail) SVG renders at slot width, not VL auto-size
# ---------------------------------------------------------------------------


def test_horizontal_stacked_bar_endpoint_labels_renders_at_slot_width(
    make_chart: Any,
) -> None:
    """Horizontal stacked bar with endpoint_labels must fill the allocated slot width.

    Root cause: VL ignores top-level ``width`` on vconcat. V2 only set it on
    the outer wrapper (ineffective) and never set ``$df_target_width``, so
    ``_correct_concat_overshoot`` never ran and VL auto-sized to ~448px
    instead of the slot width. Fix: propagate ``width`` to each vconcat pane
    and set ``$df_target_width`` so the corrector fires (mirrors V1's
    ``_wrap_horizontal_top_row_rail`` which always set both).
    """
    try:
        import vl_convert as vlc  # noqa: F401 — skip marker
    except ImportError:
        pytest.skip("vl_convert not installed")

    import re

    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import (
        BarChartStylePatch,
    )
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )
    from dbt_charts.core.render.chart.vega_lite import render_chart

    slot_width = 800.0
    data = [
        {"product": "Widget A", "revenue": 60000, "category": "Electronics"},
        {"product": "Widget A", "revenue": 15000, "category": "Tools"},
        {"product": "Widget B", "revenue": 45000, "category": "Electronics"},
        {"product": "Widget B", "revenue": 30000, "category": "Tools"},
        {"product": "Widget C", "revenue": 20000, "category": "Electronics"},
        {"product": "Widget C", "revenue": 50000, "category": "Tools"},
    ]
    chart = make_chart(
        "bar",
        x="product",
        y="revenue",
        color="category",
        style=BarChartStylePatch(
            orientation="horizontal",
            stack="zero",
            endpoint_labels={"visible": True},
        ),
    )
    board_style = resolve_style(get_theme_style())
    chart_style_context = resolve_chart_style_context(get_theme_style())
    svg = render_chart(
        chart,
        board_style,
        chart_style_context,
        data,
        format="svg",
        width=slot_width,
    )

    m = re.search(r'<svg[^>]*\bwidth="([0-9.]+)"', svg)
    assert m, "SVG has no width attribute on root <svg>"
    rendered_width = float(m.group(1))
    # Allow ≤5px tolerance for sub-pixel rounding
    assert rendered_width <= slot_width + 5, (
        f"SVG width {rendered_width}px exceeds slot {slot_width}px by more than 5px — "
        "vconcat spec is overflowing the slot"
    )
    assert rendered_width >= slot_width * 0.85, (
        f"SVG width {rendered_width}px is only {rendered_width / slot_width:.0%} of "
        f"slot {slot_width}px — VL is auto-sizing instead of filling the slot "
        "(likely missing explicit vconcat pane widths or $df_target_width sentinel)"
    )
