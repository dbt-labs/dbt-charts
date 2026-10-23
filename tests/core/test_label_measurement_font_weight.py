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

from typing import Any

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve.chart._axes import legend_row_fits
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.font_measure import get_weighted_font_measurer


def _charts() -> Any:
    return resolve_chart_style_context(get_theme_style())


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
            chrome = legend.row.swatch_width + legend.label.padding
            row_total = sum(
                chrome + measurer.measure(entry, legend.label.font.size)
                for entry in entries
            )
            return row_total + legend.row.entry_gap * (len(entries) - 1)

        width = (total("400") + total("800")) / 2
        assert legend_row_fits(entries, width, legend) is True, (
            "precondition: this row must measure as fitting at the normal "
            "weight, or this test no longer reproduces the bug"
        )
        assert legend_row_fits(entries, width, bold_legend) is False, (
            "a legend authored with a bold label weight must be measured "
            "against its own, wider glyphs — not the unweighted default"
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
