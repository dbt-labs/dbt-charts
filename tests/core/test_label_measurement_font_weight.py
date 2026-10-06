"""Text that sizes layout must be measured at the weight it paints, not a
fixed default weight.

A variable font's glyph advances change with weight — a run painted at 700
is wider than the same run measured at the unweighted default (400).
``font_measure.get_weighted_font_measurer`` exists for exactly this: a
measurer instanced at the weight the caller is about to paint. Each test
below reproduces a call site that painted a label at an authored,
non-default ``font.weight`` but measured it with the plain
``get_font_measurer`` (fixed weight), so the measured width could be
narrower than the painted glyphs — undercounting a rail, a legend row, an
axis label's collision footprint, or an axis title's wrap budget.
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import Any

import pytest

from dbt_charts.core.compile.config import (
    get_chart_rendering,
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.resolve.chart._axes import legend_row_fits
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.font_measure import get_weighted_font_measurer
from dbt_charts.core.render.chart.emitters._cartesian import (
    _LAYOUT_CHROME_PX,
    wrap_axis_title,
)
from dbt_charts.core.render.chart.emitters._endpoint_rail import (
    measure_label_pane_width,
)
from dbt_charts.core.render.chart.emitters._label_overlap import (
    AxisLabelLayout,
    resolve_axis_x_overlap,
)
from mdsvg.fonts import measure_text_precise


@pytest.fixture(autouse=True)
def _reset() -> Any:
    reset_config()
    yield
    reset_config()


def _charts() -> Any:
    return resolve_chart_style_context(get_theme_style())


class TestEndpointRailPaneWidth:
    """``measure_label_pane_width`` sizes the endpoint-label rail
    (``resolve_endpoint_rail_span``) before the crowding decision runs —
    but painted endpoint labels use ``series_label.font_weight``, an
    authorable, real field (``ResolvedSeriesLabelStyle.font_weight``,
    confirmed painted via ``_series_label_font_props``'s ``fontWeight``)."""

    def test_bold_series_name_measures_wider_than_normal(self) -> None:
        name = "Northeast Territory Revenue"
        normal_width, _ = measure_label_pane_width([name], "Inter", 14.0, "400", 2000.0)
        bold_width, _ = measure_label_pane_width([name], "Inter", 14.0, "700", 2000.0)
        assert bold_width > normal_width, (
            "a name painted at font-weight 700 must measure wider than the "
            "same name at 400 — the rail width this function returns feeds "
            "the plot-width subtraction the x-axis crowding decision relies "
            "on, so undercounting it lets the rail collide with axis labels"
        )


class TestLegendRowFits:
    """``legend_row_fits`` is the single source of truth for whether a top
    legend's entries fit one row — but it read only
    ``legend.label.font.family``, never ``legend.label.font.weight``,
    though that weight is painted (``labelFontWeight`` in
    ``vl_field_maps.py``)."""

    def test_bold_legend_label_weight_narrows_capacity(self) -> None:
        legend = _charts().legend
        bold_font = legend.label.font.model_copy(update={"weight": "800"})
        bold_legend = legend.model_copy(
            update={"label": legend.label.model_copy(update={"font": bold_font})}
        )
        entries = ("Northeast Territory Region", "Southwest Territory Region")

        def total(font_weight: str) -> float:
            measurer = get_weighted_font_measurer(legend.label.font.family, font_weight)
            cfg = get_chart_rendering().legend
            chrome = cfg.row_swatch_width_px + legend.label.padding
            row_total = sum(
                chrome + measurer.measure(entry, legend.label.font.size)
                for entry in entries
            )
            return row_total + cfg.row_entry_gap_px * (len(entries) - 1)

        width = (total("400") + total("800")) / 2
        assert legend_row_fits(entries, width, legend) is True, (
            "precondition: this row must measure as fitting at the normal "
            "weight, or this test no longer reproduces the bug"
        )
        assert legend_row_fits(entries, width, bold_legend) is False, (
            "a legend authored with a bold label weight must be measured "
            "against its own, wider glyphs — not the unweighted default"
        )


class TestAxisLabelOverlapPinnedAngle:
    """A pinned ``labels.angle`` skips the tilt ladder, so
    ``_pinned_angle_block_height`` alone sizes the space a consumer
    reserves under the axis — but it measured every label at the default
    weight even though ``axis.labels.font.weight`` is a real, authorable,
    painted field (``labelFontWeight``)."""

    def test_bold_axis_label_weight_grows_the_reserved_block_height(self) -> None:
        charts = _charts()
        axis_x = resolved_axis_style(
            charts, "axis_x", "ordinal", chart_type="", label_authored=False
        )
        label = dataclasses.replace(axis_x.labels, angle=45.0)
        data = [{"category": "Northeast Territory Region"}]

        def layout(weight: str) -> AxisLabelLayout:
            font = label.font.model_copy(update={"weight": weight})
            axis = dataclasses.replace(
                axis_x, labels=dataclasses.replace(label, font=font)
            )
            return resolve_axis_x_overlap(
                axis,
                "category",
                data,
                0.8,
                edge_labels_flushed=False,
                continuous_temporal=False,
                chart_width=600.0,
            )

        normal_height = layout("400").label_block_height
        bold_height = layout("800").label_block_height
        assert bold_height > normal_height, (
            "a pinned-angle axis label painted bold must reserve more "
            "vertical space than the same label measured at the default "
            "weight — otherwise the rail/axis sizing undercounts a bold "
            "label's real footprint"
        )


class TestUnparseableWeightFallsBackToDefault:
    """A `FontStyle.weight` authored as a non-numeric, non-CSS-keyword
    string (e.g. ``semibold`` — not one of normal/bold/bolder/lighter and
    not a number) is not a `wght` axis value `css_weight_to_axis` can
    resolve. Vega-Lite itself does not reject such a value at paint time —
    it simply paints at the browser's default weight instead of raising —
    so a board authoring it rendered on ``main``. `get_weighted_font_measurer`
    must match that: fall back to the unweighted default measurer rather
    than let `css_weight_to_axis`'s `ValueError` propagate and break a
    render that used to work."""

    def test_unparseable_weight_does_not_raise_and_matches_default(self) -> None:
        from dbt_charts.core.font_measure import (
            get_font_measurer,
            get_weighted_font_measurer,
        )

        text = "Northeast Territory Revenue"
        size = 14.0
        default_width = get_font_measurer("Inter").measure(text, size)
        fallback_width = get_weighted_font_measurer("Inter", "semibold").measure(
            text, size
        )
        assert fallback_width == default_width


class TestAxisLabelOverlapAutoTiltDiscreteAndTemporal:
    """`_pinned_angle_block_height` (tested above) is only reached when an
    author pins `labels.angle`. The far more common path is the auto-tilt
    ladder itself — `resolve_axis_x_overlap`'s ordinal branch (~line 898)
    and `_temporal_layout` (~line 526) — which measured `axis.labels.font`
    the same unweighted way before this fix. One assertion each, in the
    same shape as the pinned-angle test: a bold label.font.weight must
    grow the tilt ladder's own measured block height."""

    def test_bold_ordinal_labels_grow_the_auto_tilt_block_height(self) -> None:
        charts = _charts()
        axis_x = resolved_axis_style(
            charts, "axis_x", "ordinal", chart_type="", label_authored=False
        )
        label = dataclasses.replace(axis_x.labels, angle=None)
        data = [
            {"category": c}
            for c in [
                "Northeast Territory Revenue",
                "Southwest Territory Revenue",
                "Central Region Revenue",
                "Pacific Coastal Revenue",
            ]
        ]

        def layout(weight: str) -> AxisLabelLayout:
            font = label.font.model_copy(update={"weight": weight})
            axis = dataclasses.replace(
                axis_x, labels=dataclasses.replace(label, font=font)
            )
            return resolve_axis_x_overlap(
                axis,
                "category",
                data,
                0.8,
                edge_labels_flushed=False,
                continuous_temporal=False,
                chart_width=500.0,
            )

        normal_height = layout("400").label_block_height
        bold_height = layout("800").label_block_height
        assert bold_height > normal_height, (
            "the ordinal auto-tilt ladder must measure labels at their "
            "authored weight, not a fixed default"
        )

    def test_bold_temporal_labels_grow_the_auto_tilt_block_height(self) -> None:
        charts = _charts()
        axis_x = resolved_axis_style(
            charts, "axis_x", "temporal", chart_type="", label_authored=False
        )
        label = dataclasses.replace(
            axis_x.labels, angle=None, tilt_increments=[0.0, -45.0, -90.0]
        )
        start = datetime.date(2024, 1, 1)
        data = [
            {"d": (start + datetime.timedelta(days=i)).isoformat()} for i in range(7)
        ]

        def layout(weight: str) -> AxisLabelLayout:
            font = label.font.model_copy(update={"weight": weight})
            axis = dataclasses.replace(
                axis_x,
                labels=dataclasses.replace(label, font=font),
                time_unit="dayofweek",
            )
            return resolve_axis_x_overlap(
                axis,
                "d",
                data,
                0.8,
                edge_labels_flushed=False,
                continuous_temporal=False,
                chart_width=80.0,
            )

        normal = layout("400")
        bold = layout("800")
        assert normal.angle == -90.0 and bold.angle == -90.0, (
            "precondition: both weights must land on the same tilt rung, or "
            "this test no longer isolates the measurement change"
        )
        assert bold.label_block_height > normal.label_block_height, (
            "_temporal_layout's auto-tilt ladder must measure labels at "
            "their authored weight, not a fixed default"
        )


class TestAxisTitleWrapBudget:
    """``wrap_axis_title`` decides whether an axis title fits one line or
    wraps/ellipsizes — but it measured the title at the default weight
    while every axis title paints at its own ``font.weight`` (default
    theme value: 600, confirmed emitted as ``titleFontWeight``)."""

    def test_bold_axis_title_wraps_where_normal_weight_fits_flat(self) -> None:
        charts = _charts()
        axis_x = resolved_axis_style(
            charts, "axis_x", "ordinal", chart_type="", label_authored=False
        )
        base_font = axis_x.title.font
        text = "Northeast Territory Region"

        def width_at(weight: str) -> float:
            measurer = get_weighted_font_measurer(base_font.family, weight)
            return measure_text_precise(text, base_font.size, measurer)

        w_normal = width_at("400")
        w_bold = width_at("800")
        assert w_bold > w_normal, "sanity: bold glyphs must measure wider"

        extent = (w_normal + w_bold) / 2 + _LAYOUT_CHROME_PX

        normal_font = base_font.model_copy(update={"weight": "400"})
        bold_font = base_font.model_copy(update={"weight": "800"})

        normal_title, normal_truncated = wrap_axis_title(text, extent, normal_font)
        bold_title, bold_truncated = wrap_axis_title(text, extent, bold_font)

        assert normal_title == text and normal_truncated is False, (
            "precondition: the normal-weight title must fit flat at this "
            "extent, or this test no longer reproduces the bug"
        )
        assert bold_title != text or bold_truncated, (
            "a bold-weight axis title must be measured against its own "
            "wider glyphs — wrapping/truncating where the unweighted "
            "measurement said it still fit flat"
        )
