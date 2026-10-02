"""Width-tier resolution for series-label typography and endpoint labels."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.resolved import ResolvedLineChart
from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart
from dbt_charts.core.render.converters.chart import render_vega_spec


def _multi_series_data() -> list[dict[str, int | str]]:
    return [
        {"date": "2024-01-01", "value": 100, "series": "Core"},
        {"date": "2024-02-01", "value": 120, "series": "Core"},
        {"date": "2024-01-01", "value": 200, "series": "Growth"},
        {"date": "2024-02-01", "value": 180, "series": "Growth"},
    ]


def _board_with_distinctive_series_label_sizes(
    model_copy_at: Callable[[Any, str, Any], Any],
    *,
    regular_size: float = 83.0,
    compact_size: float = 47.0,
) -> ChartStyleContext:
    from dbt_charts.core.compile.models.style.theme.marks import SeriesLabelFontStyle

    seed = model_copy_at(
        get_theme_style(),
        "charts.series_label.font",
        SeriesLabelFontStyle(
            size=regular_size,
            weight=731.0,
            compact_size=compact_size,
            compact_weight=587.0,
        ),
    )
    seed = model_copy_at(
        seed,
        "charts.line.endpoint_labels",
        EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
    )
    seed = model_copy_at(seed, "charts.legend.compact_columns", 7)
    return resolve_chart_style_context(seed)


def test_width_tier_boundary_table() -> None:
    """Every threshold belongs to exactly the higher width tier."""
    from dbt_charts.core.compile.resolve.style.typography import (
        _NARROW_MAX,
        _TINY_MAX,
        _WIDE_MIN,
        width_tier,
    )

    assert width_tier(_TINY_MAX - 0.1) == "tiny"
    assert width_tier(_TINY_MAX) == "narrow"
    assert width_tier(_NARROW_MAX - 0.1) == "narrow"
    assert width_tier(_NARROW_MAX) == "medium"
    assert width_tier(_WIDE_MIN) == "wide"


@pytest.mark.parametrize(
    ("width_name", "expected_size", "expected_weight"),
    [
        ("tiny", 47.0, "587"),
        ("narrow", 47.0, "587"),
        ("medium", 83.0, "731"),
        ("wide", 83.0, "731"),
    ],
)
def test_series_label_font_size_follows_width_tier(
    make_chart: Callable[..., Any],
    model_copy_at: Callable[[Any, str, Any], Any],
    width_name: str,
    expected_size: float,
    expected_weight: str,
) -> None:
    """The compact size and weight apply only to tiny and narrow cards."""
    from dbt_charts.core.compile.resolve.style.typography import (
        _NARROW_MAX,
        _TINY_MAX,
        _WIDE_MIN,
    )

    widths = {
        "tiny": _TINY_MAX - 0.1,
        "narrow": _NARROW_MAX - 0.1,
        "medium": _NARROW_MAX,
        "wide": _WIDE_MIN,
    }
    chart = make_chart("line", x="date", y="value", color="series")
    resolved = resolve(
        chart,
        _multi_series_data(),
        _board_with_distinctive_series_label_sizes(model_copy_at),
        width=widths[width_name],
    )

    assert isinstance(resolved, ResolvedLineChart)
    assert resolved.style.series_label.font_size == expected_size
    assert resolved.style.series_label.font_weight == expected_weight


def test_tiny_width_swaps_endpoint_labels_for_top_legend(
    make_chart: Callable[..., Any],
    model_copy_at: Callable[[Any, str, Any], Any],
) -> None:
    """Tiny cards restore the legend and leave the y-axis on the right."""
    from dbt_charts.core.compile.resolve.style.typography import _TINY_MAX

    chart = make_chart("line", x="date", y="value", color="series")
    resolved = resolve(
        chart,
        _multi_series_data(),
        _board_with_distinctive_series_label_sizes(model_copy_at),
        width=_TINY_MAX - 0.1,
    )

    assert isinstance(resolved, ResolvedLineChart)
    assert resolved.style.endpoint_labels.visible is False
    assert resolved.legend.visible is True
    assert resolved.style.axis_y.position == "right"
    assert resolved.legend.position.edge == "top"
    assert resolved.legend.direction == "horizontal"
    assert resolved.legend.columns == 7
    assert resolved.legend.title.visible is False
    from dbt_charts.core.render.chart.vl_field_maps import legend_to_vl

    emitted = legend_to_vl(resolved.legend)
    assert emitted is not None
    assert emitted["columns"] == 7
    assert emitted["title"] is None


def test_tiny_width_owns_legend_position_when_endpoint_labels_are_disabled(
    make_chart: Callable[..., Any],
    model_copy_at: Callable[[Any, str, Any], Any],
) -> None:
    """The tiny series-naming policy overrides an authored legend position."""
    from dbt_charts.core.compile.resolve.style.typography import _TINY_MAX

    chart = make_chart(
        "line",
        x="date",
        y="value",
        color="series",
        style={
            "endpoint_labels": {"visible": False},
            "legend": {"position": {"edge": "right"}, "direction": "vertical"},
        },
    )
    resolved = resolve(
        chart,
        _multi_series_data(),
        _board_with_distinctive_series_label_sizes(model_copy_at),
        width=_TINY_MAX - 0.1,
    )

    assert isinstance(resolved, ResolvedLineChart)
    assert resolved.style.endpoint_labels.visible is False
    assert resolved.legend.position.edge == "top"
    assert resolved.legend.direction == "horizontal"


@pytest.mark.parametrize(
    ("authored_visible", "expected_visible"),
    [(None, False), (False, False), (True, True)],
)
def test_top_horizontal_legend_title_defaults_hidden_with_explicit_opt_in(
    make_chart: Callable[..., Any],
    authored_visible: bool | None,
    expected_visible: bool,
) -> None:
    """Every top-horizontal legend hides its title unless the author opts in."""
    title = {} if authored_visible is None else {"visible": authored_visible}
    chart = make_chart(
        "line",
        x="date",
        y="value",
        color="series",
        style={
            "legend": {
                "position": {"edge": "top"},
                "direction": "horizontal",
                "title": title,
            }
        },
    )
    resolved = resolve(
        chart,
        _multi_series_data(),
        resolve_chart_style_context(get_theme_style()),
        width=700.0,
    )

    assert isinstance(resolved, ResolvedLineChart)
    assert resolved.legend.title.visible is expected_visible


def test_top_horizontal_legend_preserves_board_level_title_opt_in(
    make_chart: Callable[..., Any],
) -> None:
    from dbt_charts.core.compile.models.style.authored import StylePatch

    board_style = StylePatch.model_validate(
        {
            "charts": {
                "legend": {
                    "position": {"edge": "top"},
                    "direction": "horizontal",
                    "title": {"visible": True},
                }
            }
        }
    )
    board_style = resolve_chart_style_context(get_theme_style(), board_style)
    chart = make_chart("line", x="date", y="value", color="series")

    resolved = resolve(chart, _multi_series_data(), board_style, width=700.0)

    assert isinstance(resolved, ResolvedLineChart)
    assert resolved.legend.title.visible is True


def test_narrow_width_keeps_endpoint_label_mechanism(
    make_chart: Callable[..., Any],
    model_copy_at: Callable[[Any, str, Any], Any],
) -> None:
    """Narrow cards compact the labels without replacing their mechanism."""
    from dbt_charts.core.compile.resolve.style.typography import _NARROW_MAX

    chart = make_chart("line", x="date", y="value", color="series")
    resolved = resolve(
        chart,
        _multi_series_data(),
        _board_with_distinctive_series_label_sizes(model_copy_at),
        width=_NARROW_MAX - 0.1,
    )

    assert isinstance(resolved, ResolvedLineChart)
    assert resolved.style.endpoint_labels.visible is True
    assert resolved.legend.visible is False
    assert resolved.style.axis_y.position == "left"


def test_narrow_endpoint_labels_keep_minimum_readable_collision_gap(
    make_chart: Callable[..., Any],
    model_copy_at: Callable[[Any, str, Any], Any],
) -> None:
    """Compact endpoint text retains a layout-level readable gap — tiered to
    its own (compact) type size, not the full-size floor.

    Delivered spacing is only knowable once the real plot geometry is known —
    render through the full pipeline (``render_vega_spec``, not just
    ``render_resolved_chart``) and measure the label pane's rendered text
    marks, matching ``test_endpoint_label_gap.py``.
    """
    import re

    from dbt_charts.core.compile.resolve.style.typography import _NARROW_MAX

    compact_size = 11.0
    height = 500.0
    width = _NARROW_MAX - 0.1
    chart = make_chart("line", x="date", y="value", color="series")
    data = [
        {"date": "2024-01-01", "value": 0, "series": "A"},
        {"date": "2024-02-01", "value": 500, "series": "A"},
        {"date": "2024-01-01", "value": 1000, "series": "B"},
        {"date": "2024-02-01", "value": 501, "series": "B"},
    ]
    board_context = _board_with_distinctive_series_label_sizes(
        model_copy_at,
        regular_size=12.0,
        compact_size=compact_size,
    )
    resolved = resolve(chart, data, board_context, width=width)

    assert isinstance(resolved, ResolvedLineChart)
    assert resolved.style.series_label.font_size == compact_size
    minimum_gap = resolved.style.series_label.gap_px
    board_style = resolve_style_and_context(get_theme_style())[0]
    artifact = render_resolved_chart(
        resolved, data, board_style, width=width, height=height
    )
    assert artifact.kind == "vega_spec"
    svg = render_vega_spec(
        artifact.payload,
        "svg",
        board_style,
        width=width,
        height=height,
        is_placeholder=False,
        chart_id="test",
    )
    match = re.search(r"concat_1_marks[^>]*>(.*?)</g></g>", svg, re.DOTALL)
    assert match is not None, "expected a concat_1 (label pane) marks group in the SVG"
    ys = {
        text: float(y)
        for y, text in re.findall(
            r'transform="translate\([\-0-9.]+,\s*([\-0-9.]+)\)"[^>]*>([^<]*)</text>',
            match.group(1),
        )
    }
    pixel_gap = abs(ys["B"] - ys["A"])

    assert pixel_gap >= minimum_gap - 0.5, (
        "Compact endpoint text must retain the minimum readable collision gap; "
        f"got {pixel_gap}px for {compact_size}px text and a {minimum_gap}px minimum"
    )
