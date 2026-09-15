"""Tests for the support_table compiler leaf (compile/support_table.py).

Covers sizing/style resolution knowable before any query runs: row height,
axis offset, style-cascade resolution. Strip height (data-aware — depends on
series_count derived from query rows) and data-aware validation
(validate_support_table_against_data) live at the render boundary — see
tests/core/render/chart/test_support_table_attachment.py.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    list_built_in_themes,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.support_table import resolve_support_table_position


def _charts_style():
    return resolve_chart_style_context(get_theme_style())


def _dt_style(**overrides):
    """Build a SupportTableStyle from the default theme with optional overrides."""
    return get_theme_style().charts.support_table.model_copy(update=overrides)


def test_default_theme_cascade_drops_divider():
    # Pin the actual user-facing defaults — resolved through the default
    # theme cascade, not the bare Pydantic model. The theme YAML used to
    # override divider.width to 1, silently negating the model default.
    cs = _charts_style()
    assert cs.support_table.divider.width == 0, (
        f"default theme should not draw a divider; got width="
        f"{cs.support_table.divider.width}"
    )


def test_axis_offset_reserves_two_label_lines_by_default():
    # Regression: axis_offset previously computed 1× label height, so the strip
    # would overlap two-line labels (e.g. ['Jan', '2024']).  The fix: always
    # reserve style.label_max_lines × label height so layout is stable
    # regardless of which x-tick produces two lines.
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.compile.support_table import axis_offset

    cs = _charts_style()
    style = _dt_style()

    offset_two_line = axis_offset(cs, style, x_label_authored=False, chart_type="")
    # Build a style with label_max_lines=1 so we can compare.
    style_one_line = style.model_copy(update={"label_max_lines": 1})
    offset_one_line = axis_offset(
        cs, style_one_line, x_label_authored=False, chart_type=""
    )

    label_size = resolved_axis_style(
        cs, "axis_x", "band", chart_type="", label_authored=False
    ).labels.font.size
    assert offset_two_line == offset_one_line + label_size, (
        f"two-line reservation must add exactly one extra label-line height "
        f"({label_size}px) vs single-line; got two_line={offset_two_line:.1f}, "
        f"one_line={offset_one_line:.1f}"
    )


def test_label_position_field_rejected_by_validation():
    """YAML that authors the deleted label.position field raises ValidationError at compile time.

    Providing align alongside position ensures the error is for the forbidden
    `position` key (extra="forbid"), not for a missing required field.
    """
    import pytest
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.style.theme import SupportTableLabelStyle

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SupportTableLabelStyle.model_validate({"position": "right"})


def test_axis_offset_uses_merged_visibility_for_authored_label():
    """axis_offset must read the merged axis (Layer 5) not the base (Layer 1+2).

    When the caller passes x_label_authored=True, resolved_axis_style forces
    title.visible=True as a Layer-5 default — a default, not an override, so
    it only matters when something earlier in the cascade (the theme tier,
    Layers 1-4) hides the title. This test synthesizes that condition
    directly on the resolved theme tier (rather than depending on the
    shipped theme's own default, which is visible by default — see the
    default-axis-titles-casing task) so it keeps covering the Layer-5
    mechanism itself: without an authored label the theme's hidden title
    stays hidden (title_h==0); with one, Layer 5 forces it back on so the
    support-table strip is positioned below the title rather than colliding
    with it.

    Regression: before the fix, axis_offset read charts_style.axis_x directly
    (Layers 1+2 only), so the authored-label override was invisible and title_h
    was always 0, causing the strip to overlap the axis title.
    """
    import dataclasses

    from dbt_charts.core.compile.resolve.style.chart_context import (
        build_chart_style_context,
    )
    from dbt_charts.core.compile.support_table import axis_offset

    base_resolved = resolve_chart_style_context(get_theme_style())
    # Synthesize a theme tier that hides the title — decoupled from whatever
    # the shipped theme's own default currently is.
    hidden_title = base_resolved.axis.title.model_copy(update={"visible": False})
    base_resolved = dataclasses.replace(
        base_resolved,
        axis=base_resolved.axis.model_copy(update={"title": hidden_title}),
    )
    dt_style = _dt_style()

    from dbt_charts.core.compile.models.chart.normalized import BarChart

    cs = build_chart_style_context(base_resolved, BarChart(id="t", type="bar"))

    # Without authored label: synthesized theme tier has title.visible=False → title_h==0
    offset_without = axis_offset(cs, dt_style, x_label_authored=False, chart_type="")

    # With authored label: Layer-5 default sets title.visible=True
    offset_with = axis_offset(cs, dt_style, x_label_authored=True, chart_type="")

    assert offset_with > offset_without, (
        f"axis_offset must reserve more space when axis title is visible "
        f"(authored x_label); got without={offset_without}, with={offset_with}"
    )


def test_resolved_axis_offset_reflects_authored_x_label() -> None:
    """Offset tracks whether an axis title is actually drawn.

    Covers the wire, not the leaf: axis_offset only reserves title space if
    the resolve path actually threads the chart's x_label into it. The pair
    above pins the leaf's behavior given the flag; this pins that production
    computes the flag at all, so deleting that one line fails a test rather
    than silently under-reserving the axis title's height.

    The third case is the reclaimed-space outcome: suppressing the title on
    a labeled chart must hand the pixels back, not merely hide the glyphs
    (the whole point of preferring this over ``title.font.size: 0``). It also
    pins ordering on the v1 cascade route that ``axis_offset`` takes
    (``axis_overrides=None``), which the spec-side tests cannot reach — they
    all go through the v2 route. Re-introducing label forcing above the
    chart-local layers would over-reserve here while every spec test stayed
    green.
    """
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
        ChartSupportTableSource,
    )
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    data = [{"seg": "Alpha", "rev": 100.0}, {"seg": "Beta", "rev": 200.0}]
    board_style = resolve_chart_style_context(get_theme_style())
    hide_title = BarChartStylePatch.model_validate(
        {"axis_x": {"title": {"visible": False}}}
    )

    def _offset(
        x_label: str | None, style: BarChartStylePatch | None = None
    ) -> float | None:
        chart = BarChart(
            id="t",
            type="bar",
            x="seg",
            y="rev",
            x_label=x_label,
            style=style,
            support_table=ChartSupportTable.model_validate(
                {"entries": [ChartSupportTableSource(source="rev")]}
            ),
            query=SqlQuery(sql="SELECT 1", source="src"),
            query_name="q",
        )
        return resolve(
            chart, data, chart_style_context=board_style
        ).support_table_axis_offset

    without = _offset(None)
    with_label = _offset("Segment")
    assert without is not None and with_label is not None
    suppressed = _offset("Segment", hide_title)
    assert suppressed is not None
    assert with_label > without, (
        "an authored x_label must widen the data-table axis offset — the "
        f"resolve path is not threading it; got {without} vs {with_label}"
    )
    assert suppressed == without, (
        "an explicit title.visible:false must give the title's pixels back, "
        "leaving the same offset as a chart with no title at all; got "
        f"{suppressed} vs {without}"
    )


def test_axis_offset_honors_axis_band_title_suppression() -> None:
    """axis_offset reserves title space through the same slots emission uses.

    ``axis_offset`` asks the cascade for ``channel_type="band"``, which the
    chart-local walks must treat as a band channel — otherwise a chart
    suppressing its title via ``style.axis_band`` gets no title in the spec
    while the strip still reserves room for one, leaving a phantom gap
    between the axis and the table.
    """
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve.style.chart_context import (
        build_chart_style_context,
    )
    from dbt_charts.core.compile.support_table import axis_offset

    base_resolved = resolve_chart_style_context(get_theme_style())
    dt_style = _dt_style()
    hide_band = BarChartStylePatch.model_validate(
        {"axis_band": {"title": {"visible": False}}}
    )

    plain = build_chart_style_context(base_resolved, BarChart(id="t", type="bar"))
    suppressed = build_chart_style_context(
        base_resolved, BarChart(id="t", type="bar", style=hide_band)
    )

    assert axis_offset(
        plain, dt_style, x_label_authored=True, chart_type=""
    ) > axis_offset(suppressed, dt_style, x_label_authored=True, chart_type=""), (
        "style.axis_band.title.visible:false must give the title's pixels back "
        "in the support-table offset, same as it does in the emitted spec"
    )


def test_support_table_offset_reflects_board_family_axis_override() -> None:
    """The support-table axis-offset path must thread chart_type so a board-level
    style.charts.<family>.axis* key reaches it.

    axis_offset resolves through resolved_axis_style; _support_table_geometry
    passes the chart's type so the board family scope (layer 9) applies. If
    that thread is dropped (chart_type="" reaches the geometry path), the
    family override is skipped and the strip reserves height against a
    cascade that differs from what the render path emits — the collision this
    fix prevents. Pins the wire, not the leaf.
    """
    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTable,
        ChartSupportTableSource,
    )
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve import resolve

    data = [{"seg": "Alpha", "rev": 100.0}, {"seg": "Beta", "rev": 200.0}]

    def _offset(board_patch: StylePatch | None) -> float | None:
        ctx = (
            resolve_chart_style_context(get_theme_style(), board_patch)
            if board_patch is not None
            else resolve_chart_style_context(get_theme_style())
        )
        chart = BarChart(
            id="t",
            type="bar",
            x="seg",
            y="rev",
            support_table=ChartSupportTable.model_validate(
                {"entries": [ChartSupportTableSource(source="rev")]}
            ),
            query=SqlQuery(sql="SELECT 1", source="src"),
            query_name="q",
        )
        return resolve(chart, data, chart_style_context=ctx).support_table_axis_offset

    baseline = _offset(None)
    overridden = _offset(
        StylePatch.model_validate(
            {"charts": {"bar": {"axis": {"labels": {"padding": 40}}}}}
        )
    )
    assert baseline is not None and overridden is not None
    assert overridden != baseline, (
        "a board style.charts.bar.axis.labels.padding must change the "
        "support-table axis offset — the geometry path is not threading chart_type; "
        f"got {baseline} (baseline) vs {overridden} (with board family override)"
    )


# =============================================================================
# POSITION RESOLUTION / VALIDATION
#
# Runs at resolve time (compile.resolve.chart._kwargs._support_table_geometry
# bakes the result onto effective_support_table_style) so a mismatched
# position: is a compile-time error, not a render-time surprise.
# =============================================================================


def test_position_none_defaults_to_top_on_horizontal_category_axis():
    assert resolve_support_table_position(None, False, "left", "chart_id") == "top"


def test_position_none_defaults_to_label_side_on_vertical_category_axis():
    assert resolve_support_table_position(None, True, "left", "chart_id") == "left"
    assert resolve_support_table_position(None, True, "right", "chart_id") == "right"


def test_explicit_position_wins_over_default():
    assert (
        resolve_support_table_position("bottom", False, "left", "chart_id") == "bottom"
    )
    assert resolve_support_table_position("right", True, "left", "chart_id") == "right"


def test_left_right_position_rejected_on_horizontal_category_axis():
    with pytest.raises(CompilationError, match=r"(?i)vertical|orientation"):
        resolve_support_table_position("left", False, "left", "chart_id")
    with pytest.raises(CompilationError, match=r"(?i)vertical|orientation"):
        resolve_support_table_position("right", False, "right", "chart_id")


def test_top_bottom_position_rejected_on_vertical_category_axis():
    with pytest.raises(CompilationError, match=r"(?i)horizontal"):
        resolve_support_table_position("top", True, "left", "chart_id")
    with pytest.raises(CompilationError, match=r"(?i)horizontal"):
        resolve_support_table_position("bottom", True, "right", "chart_id")


# _base is the abstract root — it has no font.family/color and is never used
# directly at render time (see test_fontstyle_floor_guarantee.py's same filter).
_RENDERABLE_THEMES = [t for t in list_built_in_themes() if t != "_base"]


@pytest.mark.parametrize("theme_name", _RENDERABLE_THEMES)
@pytest.mark.parametrize("channel_type", ["band", "temporal", "quantitative"])
def test_axis_offset_font_size_matches_every_axis_x_channel_type(
    theme_name: str, channel_type: str
) -> None:
    """axis_offset (this module) always resolves axis_x with channel_type
    "band" — a bar's categorical axis. render/chart/support_table_attachment.py's
    _tilted_label_axis_offset instead reads resolved_chart.style.axis_x,
    baked with the CHART'S REAL channel type (e.g. "temporal" for a line/area
    chart's date x, "quantitative" for a scatter's numeric x). The render-side
    subtraction (labels.font.size * label_max_lines) only cancels what the
    compile-side bake added if the two font sizes agree — nothing enforces
    that equality, so this test stands in its place: a theme that varied
    axis_x label font size by channel type would under- or over-reserve the
    bottom-strip gap, silently, for exactly the chart families this doesn't
    reach today.
    """
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style

    cs = resolve_chart_style_context(get_theme_style(theme_name))
    band_size = resolved_axis_style(
        cs, "axis_x", "band", chart_type="bar", label_authored=False
    ).labels.font.size
    other_size = resolved_axis_style(
        cs, "axis_x", channel_type, chart_type="bar", label_authored=False
    ).labels.font.size
    assert other_size == band_size, (
        f"{theme_name}: axis_x label font.size diverges between channel_type "
        f"'band' ({band_size}) and '{channel_type}' ({other_size}) — "
        "_tilted_label_axis_offset's subtraction would cancel the wrong term"
    )
