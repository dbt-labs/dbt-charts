"""A number format authored on an axis whose field cannot carry it.

``style.axis_x`` addresses the DIMENSION channel on every cartesian family,
whatever the chart's visual orientation. On a horizontal bar that channel is
drawn down the left edge, so an author reading the picture calls the value
axis "the x axis" and writes the currency format there — and d3 formats every
category string as ``$NaN``. The same shape reaches a vertical bar, where the
mistake is less inviting but just as silent.

The house rule is one gate on every surface — axis_x, axis_y, the mirror
ghost, heatmap's y, and a temporal axis of either channel all raise the same
``ERR-LABEL-FORMAT-AXIS-MISMATCH``. Two things deliberately stay legal:

- numeric ticks on a band scale — ``stage_id: 1, 2, 3`` and numeric strings
  alike — format cleanly, so the engine cannot tell an intended format from a
  misaddressed one. Only the author can.
- a *time* spec is a separate grammar with its own date-like-ordinal paths,
  on any channel.

A *temporal* axis gets NO numeric-tick exemption: there is no reading of
``$,.0f`` over dates an author wanted, so any non-time spec there raises
unconditionally. Date-like buckets are the band-scale case worth naming:
``2024-01`` / ``Q1 2024`` are band-scale strings, not numbers, and "revenue
by quarter as a horizontal bar" is routine.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    Chart,
    HeatmapChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.style.authored import (
    AxisLabelStylePatch,
    AxisXStylePatch,
    AxisYStylePatch,
    BarChartStylePatch,
    DimensionLabelStylePatch,
    HeatmapChartStylePatch,
    LineChartStylePatch,
    ScatterChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_LABEL_FORMAT_AXIS_MISMATCH,
    ERR_TIME_FORMAT_NOT_TEMPORAL,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

_STAGES: list[dict[str, Any]] = [
    {"stage": "Prospect", "revenue": 1_200_000.0},
    {"stage": "Qualified", "revenue": 800_000.0},
    {"stage": "Closed", "revenue": 450_000.0},
]


def _x_format(fmt: str) -> AxisXStylePatch:
    return AxisXStylePatch(labels=DimensionLabelStylePatch(format=fmt))


def _render(chart: Chart, data: list[dict[str, Any]]) -> dict[str, Any]:
    return render_resolved_chart(
        resolve(chart, data, chart_style_context=_BOARD_CTX), data, _BOARD_STYLE
    ).payload


class TestNumberFormatOnCategoryAxisRaises:
    def test_horizontal_bar_axis_x_currency_names_the_measure_axis(self) -> None:
        """The reported case: the currency belongs on axis_y, and the error
        has to say so — a horizontal bar's axis_x is its categorical axis even
        though the reader sees it drawn vertically."""
        chart = BarChart(
            id="hbar",
            type="bar",
            x="stage",
            y="revenue",
            style=BarChartStylePatch(
                orientation="horizontal", axis_x=_x_format("currency")
            ),
        )
        with pytest.raises(ChartDataError) as exc:
            _render(chart, _STAGES)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH
        message = str(exc.value)
        assert "axis_y" in message
        assert "orientation" in message

    def test_vertical_bar_axis_x_currency_raises(self) -> None:
        """Same silent NaN without any orientation confusion to excuse it.

        Also the only pin on the DEFAULT remedy — every other raise test here
        traverses a call site that passes its own text."""
        chart = BarChart(
            id="vbar",
            type="bar",
            x="stage",
            y="revenue",
            style=BarChartStylePatch(
                orientation="vertical", axis_x=_x_format("currency")
            ),
        )
        with pytest.raises(ChartDataError) as exc:
            _render(chart, _STAGES)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH
        assert "style.axis_y.labels.format" in str(exc.value)

    def test_line_axis_x_currency_raises(self) -> None:
        """the engine's x-axis builder is the shared path — pinned on a second
        family so bar's own emitter isn't the only thing holding the gate."""
        chart = LineChart(
            id="line",
            type="line",
            x="stage",
            y="revenue",
            style=LineChartStylePatch(axis_x=_x_format("currency")),
        )
        with pytest.raises(ChartDataError) as exc:
            _render(chart, _STAGES)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH

    @pytest.mark.parametrize(
        ("label", "buckets"),
        [
            ("month", ["2024-01", "2024-02", "2024-03"]),
            ("quarter", ["Q1 2024", "Q2 2024", "Q3 2024"]),
            ("fiscal_year", ["FY2024", "FY2025", "FY2026"]),
        ],
    )
    def test_horizontal_bar_date_like_buckets_raise(
        self, label: str, buckets: list[str]
    ) -> None:
        """The reported shape with the categories re-shaped. A date-like bucket
        infers ordinal rather than nominal, but it is still a band-scale string
        and NaNs identically — "revenue by quarter" as a horizontal bar is the
        routine board that lands here."""
        data: list[dict[str, Any]] = [
            {label: b, "revenue": 100.0 * i} for i, b in enumerate(buckets, 1)
        ]
        chart = BarChart(
            id=f"hbar-{label}",
            type="bar",
            x=label,
            y="revenue",
            style=BarChartStylePatch(
                orientation="horizontal", axis_x=_x_format("currency")
            ),
        )
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH

    @pytest.mark.parametrize("y", ["m1", ["m1", "m2"]])
    def test_heatmap_axis_x_currency_names_the_color_channel(
        self, y: str | list[str]
    ) -> None:
        """Both heatmap shapes, because they reach the gate by different routes:
        a single measure through the engine's x-axis builder's mark_type branch,
        a list-y through the emitter's own call. Only the second draws no x axis
        dict at all — the band still paints category labels either way.

        A heatmap has no measure axis, so the default remedy (which names
        axis_y) would send the author straight back into NaN. Pinned on both
        routes so dropping either remedy is a test failure, not a silent
        regression.
        """
        data = [
            {"region": r, "m1": float(i), "m2": i * 2.0} for i, r in enumerate("NSE")
        ]
        chart = HeatmapChart(
            id="hm",
            type="heatmap",
            x="region",
            y=y,
            style=HeatmapChartStylePatch(axis_x=_x_format("currency")),
        )
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH
        message = str(exc.value)
        assert "color" in message
        assert "axis_y" not in message
        # The remedy must not name a field the schema would then reject.
        assert "legend" not in message

    def test_non_numeric_tail_row_still_raises(self) -> None:
        """Vega paints a tick per domain value, not per sampled row. Ten numeric
        rows then one string is the original NaN, and a sampled window would
        wave it through."""
        data: list[dict[str, Any]] = [
            {"code": float(i), "revenue": float(i)} for i in range(10)
        ]
        data.append({"code": "AAA", "revenue": 1.0})
        chart = BarChart(
            id="tail",
            type="bar",
            x="code",
            y="revenue",
            style=BarChartStylePatch(
                orientation="horizontal", axis_x=_x_format("currency_whole")
            ),
        )
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH

    def test_scatter_axis_x_currency_raises(self) -> None:
        """Scatter forks to its own quantitative fast path before the shared
        cartesian encoding, so its categorical branch needs its own pin."""
        chart = ScatterChart(
            id="scatter",
            type="scatter",
            x="stage",
            y="revenue",
            style=ScatterChartStylePatch(axis_x=_x_format("currency")),
        )
        with pytest.raises(ChartDataError) as exc:
            _render(chart, _STAGES)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH


class TestNumberFormatOnCategoryYAxisRaises:
    """The house rule folds in axis_y, the mirror ghost, heatmap's y, and a
    temporal axis of either channel — surfaces 2-5 from the task worksheet,
    each silently dropping or mispainting the format before this change."""

    def test_scatter_categorical_y_currency_raises(self) -> None:
        """Scatter's y (a dot plot) used to pop the format silently where
        axis_x's twin raises — the divergence this task retires."""
        chart = ScatterChart(
            id="scatter-dot",
            type="scatter",
            x="hours",
            y="team",
            style=ScatterChartStylePatch(
                axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(format="currency"))
            ),
        )
        data = [
            {"team": "Onboarding", "hours": 1.8},
            {"team": "Support", "hours": 2.4},
        ]
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH
        assert "axis_y" in str(exc.value)

    def test_heatmap_axis_y_currency_raises(self) -> None:
        """Heatmap's y is always nominal and was never gated — a currency
        format there painted ``$NaN`` row labels with no diagnostic."""
        data = [{"region": r, "m1": float(i)} for i, r in enumerate("NSE")]
        chart = HeatmapChart(
            id="hm-y",
            type="heatmap",
            x="m1",
            y="region",
            style=HeatmapChartStylePatch(
                axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(format="currency"))
            ),
        )
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH
        message = str(exc.value)
        assert "axis_y" in message
        assert "color" in message

    def test_scatter_categorical_y_mirror_format_currency_raises(self) -> None:
        """The mirror ghost re-adds a format the primary axis carries none
        of at all — ``mirror.format`` is authored-only, and it must gate the
        same way instead of painting ``$NaN`` on the opposite edge."""
        chart = ScatterChart(
            id="scatter-mirror",
            type="scatter",
            x="hours",
            y="team",
            style=ScatterChartStylePatch(
                axis_y=AxisYStylePatch(mirror={"format": "currency"})
            ),
        )
        data = [
            {"team": "Onboarding", "hours": 1.8},
            {"team": "Support", "hours": 2.4},
        ]
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH
        message = str(exc.value)
        # The opening clause must name the key the author actually wrote.
        # `axis_y.labels.format` is a different, real setting — pointing at
        # it sends them to edit something they never authored.
        assert message.startswith("style.axis_y.mirror.format")
        # A dot plot IS the shape whose measure is axis_x — its y is the
        # category — the opposite routing from the bar case below.
        assert "style.axis_x.labels.format" in message
        assert "style.axis_y.labels.format" not in message

    def test_heatmap_mirror_format_gets_the_no_measure_axis_remedy(self) -> None:
        """A heatmap has no measure axis at all — both axes are grid
        dimensions and the value is on color — so routing to either
        labels.format would raise again."""
        data = [{"region": r, "m1": float(i)} for i, r in enumerate("NSE")]
        chart = HeatmapChart(
            id="hm-mirror",
            type="heatmap",
            x="m1",
            y="region",
            style=HeatmapChartStylePatch(
                axis_y=AxisYStylePatch(mirror={"format": "currency"})
            ),
        )
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH
        message = str(exc.value)
        assert message.startswith("style.axis_y.mirror.format")
        assert "color channel" in message
        assert "style.axis_y.labels.format" not in message
        assert "style.axis_x.labels.format" not in message

    def test_scatter_temporal_y_mirror_format_gets_the_time_remedy(self) -> None:
        """A temporal y must fall through to the gate's own remedy. The
        channel-swap text would assert the scale is categorical when it is a
        date, and routing the author to another axis raises again — the
        gate's docstring makes "a remedy must not send them back" a
        contract."""
        chart = ScatterChart(
            id="scatter-mirror-temporal",
            type="scatter",
            x="hours",
            y="signup",
            style=ScatterChartStylePatch(
                axis_y=AxisYStylePatch(mirror={"format": "currency"})
            ),
        )
        data = [
            {"signup": "2024-01-15", "hours": 1.8},
            {"signup": "2024-02-15", "hours": 2.4},
        ]
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH
        message = str(exc.value)
        assert "time spec" in message
        assert "categorical" not in message

    def test_horizontal_bar_mirror_format_raises(self) -> None:
        """The common bar shape, not just a dot plot. Bar defaults to
        horizontal on a categorical x, which puts the category on VL's own
        y — so `axis_y.mirror.format` mirrors the CATEGORY edge and raises,
        even though the author followed the documented "axis_y is the
        measure" convention."""
        chart = BarChart(
            id="bar-mirror-category",
            type="bar",
            x="month",
            y="revenue",
            style=BarChartStylePatch(
                axis_y=AxisYStylePatch(mirror={"format": "currency"})
            ),
        )
        data = [{"month": "Jan", "revenue": 1.0}, {"month": "Feb", "revenue": 2.0}]
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH
        message = str(exc.value)
        assert message.startswith("style.axis_y.mirror.format")
        # A bar's measure is axis_y in BOTH orientations — `orientation:
        # horizontal` flips the VL channels, not the authored ones. Routing
        # to axis_x here would land the author in this same error code.
        assert "style.axis_y.labels.format" in message
        assert "style.axis_x.labels.format" not in message

    def test_multi_measure_heatmap_axis_y_currency_raises(self) -> None:
        """A wide (list) y builds per-measure layer encodings with no axis
        dict and puts no y on the top encoding, so an authored format can
        never reach Vega — it neither took effect nor refused, the one
        surface the house rule missed."""
        data = [{"region": r, "2023": 1.0, "2024": 2.0} for r in "NSE"]
        chart = HeatmapChart(
            id="hm-wide-y",
            type="heatmap",
            x="region",
            # Year columns on purpose: the commonest wide shape, and the one
            # a numeric-tick exemption would wave through. Nothing about the
            # measure names can make an inert format take effect.
            y=["2023", "2024"],
            style=HeatmapChartStylePatch(
                axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(format="currency"))
            ),
        )
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH
        message = str(exc.value)
        assert "axis_y" in message
        assert "color" in message

    def test_line_axis_x_currency_on_temporal_raises(self) -> None:
        """A number spec on a temporal x used to paint the literal text
        across the axis, deliberately carved out of the old gate. Temporal
        folds in: dates get no numeric-tick exemption, so this raises like
        every other axis now, and the remedy points at a time spec or
        ``style.time_format`` instead of a channel swap."""
        data = [
            {"month": f"2024-{m:02d}-01", "revenue": float(m)} for m in range(1, 13)
        ]
        chart = LineChart(
            id="temporal-currency",
            type="line",
            x="month",
            y="revenue",
            style=LineChartStylePatch(axis_x=_x_format("currency")),
        )
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code is ERR_LABEL_FORMAT_AXIS_MISMATCH
        assert "time spec" in str(exc.value)


class TestLegalFormatsAreUntouched:
    @pytest.mark.parametrize("family", ["bar", "line"])
    def test_time_format_on_a_category_x_raises_not_invalid_date(
        self, family: str
    ) -> None:
        """A time pattern over team names would paint "Invalid Date"."""
        patch = {"axis_x": _x_format("%b %Y")}
        chart = (
            BarChart(
                id="c",
                type="bar",
                x="stage",
                y="revenue",
                style=BarChartStylePatch(**patch),
            )
            if family == "bar"
            else LineChart(
                id="c",
                type="line",
                x="stage",
                y="revenue",
                style=LineChartStylePatch(**patch),
            )
        )
        with pytest.raises(ChartDataError) as exc:
            _render(chart, _STAGES)
        assert exc.value.code is ERR_TIME_FORMAT_NOT_TEMPORAL
        message = str(exc.value)
        assert "style.axis_x.labels.format" in message
        assert "'stage'" in message
        assert "%b %Y" in message

    def test_time_format_on_a_quantitative_x_raises(self) -> None:
        data = [{"customers": i * 140.0, "arr": float(i)} for i in range(30)]
        chart = LineChart(
            id="quant",
            type="line",
            x="customers",
            y="arr",
            style=LineChartStylePatch(axis_x=_x_format("%b %Y")),
        )
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code is ERR_TIME_FORMAT_NOT_TEMPORAL

    def test_scatter_categorical_y_time_format_raises(self) -> None:
        chart = ScatterChart(
            id="scatter-time-y",
            type="scatter",
            x="hours",
            y="team",
            style=ScatterChartStylePatch(
                axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(format="%b %Y"))
            ),
        )
        data = [
            {"team": "Onboarding", "hours": 1.8},
            {"team": "Support", "hours": 2.4},
        ]
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code is ERR_TIME_FORMAT_NOT_TEMPORAL
        assert "style.axis_y.labels.format" in str(exc.value)
