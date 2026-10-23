"""TDD tests for emitter cleanup: legend suppression at resolve time, shared helpers."""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.resolve import resolve

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_MULTI_SERIES_DATA: list[dict[str, Any]] = [
    {"month": "Jan", "value": 100, "series": "A"},
    {"month": "Jan", "value": 50, "series": "B"},
    {"month": "Feb", "value": 200, "series": "A"},
    {"month": "Feb", "value": 80, "series": "B"},
]


def _board_style_with_endpoint_labels(
    chart_type: str, enabled: bool, model_copy_at: Any
) -> Any:
    """Board style with endpoint_labels.visible set for the given chart type.

    Seeds charts.legend.visible: auto explicitly, so these tests observe
    resolve's endpoint-label legend suppression rather than a theme pin.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    with_legend = model_copy_at(
        get_theme_style("stark"), "charts.legend.visible", "auto"
    )
    seed = model_copy_at(
        with_legend,
        f"charts.{chart_type}.endpoint_labels",
        EndpointLabelsConfig(
            visible="auto" if enabled else False, label_offset=5.0, height=20.0
        ),
    )
    return resolve_chart_style_context(seed)


# ---------------------------------------------------------------------------
# Change 1: legend.visible baked False at resolve time when endpoint labels fire
# ---------------------------------------------------------------------------


def test_legend_baked_false_for_bar_with_series_color_and_endpoint_labels(
    make_chart: Any, model_copy_at: Any
) -> None:
    """resolved bar.legend.visible is False when series color + endpoint_labels.visible.

    Explicit stack: 'zero' — this seeds endpoint labels on the *board* style,
    which a grouped bar reads as the default and steps aside from (only the
    chart's own patch outranks that), so it needs a stacked bar to exercise
    the fires-path this test targets.
    """
    board_style = _board_style_with_endpoint_labels("bar", True, model_copy_at)
    chart = make_chart("bar", x="month", y="value", color="series", stack="zero")
    rc = resolve(chart, _MULTI_SERIES_DATA, chart_style_context=board_style)
    assert rc.legend.visible is False, (
        "legend.visible must be baked False at resolve time when endpoint labels fire"
    )


def test_legend_baked_false_for_line_with_series_color_and_endpoint_labels(
    make_chart: Any, model_copy_at: Any
) -> None:
    """resolved line.legend.visible is False when series color + endpoint_labels.visible."""
    board_style = _board_style_with_endpoint_labels("line", True, model_copy_at)
    chart = make_chart("line", x="month", y="value", color="series")
    rc = resolve(chart, _MULTI_SERIES_DATA, chart_style_context=board_style)
    assert rc.legend.visible is False


def test_legend_baked_false_for_area_with_series_color_and_endpoint_labels(
    make_chart: Any, model_copy_at: Any
) -> None:
    """resolved area.legend.visible is False when series color + endpoint_labels.visible."""
    board_style = _board_style_with_endpoint_labels("area", True, model_copy_at)
    chart = make_chart("area", x="month", y="value", color="series")
    rc = resolve(chart, _MULTI_SERIES_DATA, chart_style_context=board_style)
    assert rc.legend.visible is False


def test_legend_not_suppressed_for_single_series(
    make_chart: Any, model_copy_at: Any
) -> None:
    """No suppression when there is no series color channel."""
    board_style = _board_style_with_endpoint_labels("line", True, model_copy_at)
    chart = make_chart("line", x="month", y="value")  # no color channel
    single_data = [{"month": "Jan", "value": 100}, {"month": "Feb", "value": 200}]
    rc = resolve(chart, single_data, chart_style_context=board_style)
    assert rc.legend.visible is True


def test_legend_not_suppressed_when_endpoint_labels_disabled(
    make_chart: Any, model_copy_at: Any
) -> None:
    """No suppression when endpoint_labels.visible is False."""
    board_style = _board_style_with_endpoint_labels("line", False, model_copy_at)
    chart = make_chart("line", x="month", y="value", color="series")
    rc = resolve(chart, _MULTI_SERIES_DATA, chart_style_context=board_style)
    assert rc.legend.visible is True


# ---------------------------------------------------------------------------
# Fix 1+2: ay.position baked concrete (not "auto") at resolve time
# ---------------------------------------------------------------------------


def test_ay_position_baked_for_bar_with_endpoint_labels(
    make_chart: Any, model_copy_at: Any
) -> None:
    """bar with series color + endpoint_labels.visible → ay.position is 'left', not 'auto'.

    Explicit stack: 'zero' — see the legend test above for why the grouped
    default can't exercise this path.
    """
    board_style = _board_style_with_endpoint_labels("bar", True, model_copy_at)
    chart = make_chart("bar", x="month", y="value", color="series", stack="zero")
    rc = resolve(chart, _MULTI_SERIES_DATA, chart_style_context=board_style)
    assert rc.style.axis_y.position == "left", (
        "axis_y.position must be baked 'left' at resolve time when endpoint labels fire"
    )


def test_ay_position_baked_left_for_bar_without_endpoint_labels(
    make_chart: Any, model_copy_at: Any
) -> None:
    """bar without endpoint labels → ay.position is still 'left', not 'auto'.

    This bar's categorical x (month="Jan"/"Feb") auto-classifies as
    horizontal orientation (see _bar_orientation's column-type inference),
    whose categorical y-axis always defaults 'left' (matching the deleted
    categorical_orient field's static default) independent of endpoint
    labels — endpoint_labels is a line/area concept, bar never fires it.
    """
    board_style = _board_style_with_endpoint_labels("bar", False, model_copy_at)
    chart = make_chart("bar", x="month", y="value", color="series")
    rc = resolve(chart, _MULTI_SERIES_DATA, chart_style_context=board_style)
    assert rc.style.axis_y.position == "left"


def test_ay_position_baked_for_line_with_endpoint_labels(
    make_chart: Any, model_copy_at: Any
) -> None:
    """line with series color + endpoint_labels → ay.position is 'left'."""
    board_style = _board_style_with_endpoint_labels("line", True, model_copy_at)
    chart = make_chart("line", x="month", y="value", color="series")
    rc = resolve(chart, _MULTI_SERIES_DATA, chart_style_context=board_style)
    assert rc.style.axis_y.position == "left"


def test_ay_position_baked_for_area_with_endpoint_labels(
    make_chart: Any, model_copy_at: Any
) -> None:
    """area with series color + endpoint_labels → ay.position is 'left'."""
    board_style = _board_style_with_endpoint_labels("area", True, model_copy_at)
    chart = make_chart("area", x="month", y="value", color="series")
    rc = resolve(chart, _MULTI_SERIES_DATA, chart_style_context=board_style)
    assert rc.style.axis_y.position == "left"


def test_ay_position_never_auto_after_resolve(
    make_chart: Any,
) -> None:
    """Resolved axis_y.position is never 'auto' — it's always concrete."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_style = resolve_chart_style_context(get_theme_style("stark"))
    for chart_type in ("bar", "line", "area"):
        chart = make_chart(chart_type, x="month", y="value", color="series")
        rc = resolve(chart, _MULTI_SERIES_DATA, chart_style_context=board_style)
        assert rc.style.axis_y.position != "auto", (
            f"{chart_type}: axis_y.position must not be 'auto' after resolve"
        )


# ---------------------------------------------------------------------------
# Fix 4: grouped bar scale padding set by the emitter (apply_grouped_bar_spacing)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Change 5: sorted_series_by_stack_order shared helper
# ---------------------------------------------------------------------------


def test_sorted_series_alphabetical() -> None:
    """stack_order='alphabetical' → sorted alphabetically."""
    from dbt_charts.core.utils import sorted_series_by_stack_order

    candidates = ["Zebra", "Apple", "Mango"]
    result = sorted_series_by_stack_order(candidates, [], "series", "alphabetical")
    assert result == ["Apple", "Mango", "Zebra"]


def test_sorted_series_data_order() -> None:
    """stack_order='data' → first-encounter order."""
    from dbt_charts.core.utils import sorted_series_by_stack_order

    data = [
        {"series": "C"},
        {"series": "A"},
        {"series": "B"},
        {"series": "C"},
    ]
    candidates = ["A", "B", "C"]
    result = sorted_series_by_stack_order(candidates, data, "series", "data")
    assert result == ["C", "A", "B"]


def test_sorted_series_value_order() -> None:
    """stack_order=None/'value' → largest global sum at baseline (descending)."""
    from dbt_charts.core.utils import sorted_series_by_stack_order

    data = [
        {"series": "A", "y": 10.0},
        {"series": "B", "y": 50.0},
        {"series": "A", "y": 20.0},
    ]
    candidates = ["A", "B"]
    result = sorted_series_by_stack_order(candidates, data, "series", None, y_field="y")
    # B has sum 50; A has sum 30 → B at baseline (descending)
    assert result == ["B", "A"]


# ---------------------------------------------------------------------------
# Scatter size-legend: theme styling must propagate to size encoding
# ---------------------------------------------------------------------------
