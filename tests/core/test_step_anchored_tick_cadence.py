"""Tick cadence on style.axis_x: ticks.time_unit/step and a bare numeric step.

Thin passthroughs to VL's own cadence properties, chosen by the axis type
the data resolves to:

  temporal     time_unit (+ optional step) -> axis.tickCount: {interval, step}
               count                       -> axis.tickCount: <int>
  quantitative count                       -> axis.tickCount: <int> (advisory)
               step                        -> axis.tickMinStep: <int> (a floor)
  ordinal      neither applies

``time_unit`` uses dbt charts' own calendar-bucketing grain vocabulary
(``year``/``yearquarter``/``yearmonth``/...) — distinct from VL's own
d3-interval vocabulary (``year``/``month``/...) that the old
``ticks.interval`` field spoke directly. The render layer derives the VL
interval name via ``_TEMPORAL_TICK_INTERVAL``.

Model-level gate (Pydantic — raised at authoring/construction time via
DimensionTicksStyle's own ``__constraints__``, no
compile pass needed):
  1. ticks.time_unit + ticks.count together -> ValidationError

Compile-time gate (fires in _bake_cartesian_axes):
  2. ticks.time_unit/step on axis_y -> compile error (measure axis is never
     temporal in dbt charts' cartesian model; DimensionTicksStyle is also
     axis_x-only at the type level, so this is belt-and-suspenders on the
     authored surface)

Render-time gates (data-dependent — vl_type is only known once data resolves
the quantitative/ordinal/temporal fork, see the engine's x-axis builder):
  3. ticks.time_unit/step on an x-axis that resolves to non-temporal
     (ordinal) -> render error, no silent no-op
  4. a bare ticks.step (no time_unit) on an x-axis that resolves to anything
     but quantitative -> render error. Formerly gate 1, enforced in the model;
     it moved here because "is this axis quantitative" is a data-dependent
     answer the model cannot see.
"""

from __future__ import annotations

import pydantic
import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    HeatmapChart,
    LineChart,
)
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
    AxisYStylePatch,
    BarChartStylePatch,
    DimensionTicksStylePatch,
    HeatmapChartStylePatch,
    LineChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _annual_data(start: int = 1955, end: int = 2025) -> list[dict]:
    """~70 annual points — the domain width editorial 5/10-year cadence targets."""
    return [
        {"year": f"{y}-01-01", "value": 100 + i * 3}
        for i, y in enumerate(range(start, end + 1))
    ]


class TestSchemaField:
    """time_unit/step exist on DimensionTicksStylePatch and flow through the cascade."""

    def test_axis_ticks_style_patch_accepts_time_unit_and_step(self):
        patch = DimensionTicksStylePatch(time_unit="year", step=5)
        assert patch.time_unit == "year"
        assert patch.step == 5


class TestModelValidationGates:
    """Gate 1: the one co-occurrence constraint Pydantic still owns, enforced on the
    compiled ``DimensionTicksStyle`` itself (not its all-Optional Patch —
    ``build_patch_model`` doesn't carry model_validators, so the check fires
    once at the merged theme-tier result, matching ScaleContinuousStyle's
    established convention).
    """

    def test_step_without_time_unit_is_accepted_at_construction(self):
        """A bare ``step`` is the quantitative-axis cadence lever, and whether
        this axis is quantitative is only known once data resolves — so the
        model accepts it and the render gate (TestRenderGate) decides."""
        from dbt_charts.core.compile.models.style.theme import DimensionTicksStyle

        assert DimensionTicksStyle(step=1000).step == 1000

    def test_time_unit_alone_is_valid(self):
        patch = DimensionTicksStylePatch(time_unit="year")
        assert patch.time_unit == "year"
        assert patch.step is None

    def test_time_unit_and_count_together_raises_at_construction(self):
        from dbt_charts.core.compile.models.style.theme import DimensionTicksStyle

        with pytest.raises(pydantic.ValidationError, match="ticks.time_unit"):
            DimensionTicksStyle(time_unit="year", count=8)


class TestCompileGates:
    """Gate 2: axis_y rejection (measure axis is never temporal).

    ``time_unit`` itself is structurally x-only (DimensionTicksStyle is not
    the type of ``AxisYStyle.ticks``) — Pydantic rejects it as an unknown
    field at authoring time, no compile pass needed. ``step`` alone still
    lives on the universal AxisTicksStyle base shared by axis_y, so it's the
    one field this compile-time gate still has to guard.
    """

    def test_time_unit_on_axis_y_ticks_is_rejected_at_authoring(self):
        with pytest.raises(pydantic.ValidationError, match="time_unit"):
            AxisYStylePatch(ticks={"time_unit": "year"})

    def test_step_on_axis_y_raises(self):
        style = BarChartStylePatch(axis_y=AxisYStylePatch(ticks={"step": 5}))
        chart = BarChart(id="bar", type="bar", x="year", y="value", style=style)
        with pytest.raises(CompilationError, match="axis_y"):
            resolve(chart, _annual_data(), chart_style_context=_BOARD_CTX)


class TestRenderGate:
    """Gates 3-4: an authored cadence the resolved axis type cannot honor raises."""

    def test_ordinal_resolved_bar_x_with_time_unit_raises(self):
        # Low bucket count (24 months) keeps a monthly bar axis ordinal —
        # time_unit/step require continuous temporal, so this must raise.
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(
                ticks=DimensionTicksStylePatch(time_unit="yearmonth")
            )
        )
        data = [{"month": f"2024-{m:02d}-01", "value": m} for m in range(1, 13)] + [
            {"month": f"2025-{m:02d}-01", "value": m} for m in range(1, 13)
        ]
        chart = BarChart(id="bar", type="bar", x="month", y="value", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        with pytest.raises(ChartDataError, match="ticks.time_unit"):
            render_resolved_chart(resolved, data, _BOARD_STYLE)

    def test_temporal_x_with_bare_step_raises_pointing_at_time_unit(self):
        """Gate 4 (bare step) on a temporal axis: a bare number names no calendar cadence,
        so the remedy is ``time_unit``, not a numeric floor."""
        style = LineChartStylePatch(
            axis_x=AxisXStylePatch(ticks=DimensionTicksStylePatch(step=5))
        )
        data = _annual_data()
        chart = LineChart(id="line", type="line", x="year", y="value", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        with pytest.raises(ChartDataError, match="ticks.step") as exc:
            render_resolved_chart(resolved, data, _BOARD_STYLE)
        assert "ticks.time_unit" in str(exc.value)

    def test_ordinal_x_with_bare_step_raises_without_recommending_count(self):
        """Gate 4 (bare step) on an ordinal axis: a discrete domain has no numeric tick
        interval, and ``count`` is inert there too — so the message must not
        send the author to it. That misdirection is what the old model-level
        error did."""
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(ticks=DimensionTicksStylePatch(step=1000))
        )
        data = [{"month": f"2024-{m:02d}-01", "value": m} for m in range(1, 13)] + [
            {"month": f"2025-{m:02d}-01", "value": m} for m in range(1, 13)
        ]
        chart = BarChart(id="bar", type="bar", x="month", y="value", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        with pytest.raises(ChartDataError, match="ticks.step") as exc:
            render_resolved_chart(resolved, data, _BOARD_STYLE)
        assert "ticks.count" not in str(exc.value)


class TestEveryXAxisPathIsCovered:
    """A bare ``ticks.step`` reaches a gate on every cartesian x path.

    The check used to live on the model, where it covered every emitter by
    construction. Moving it to render bought the axis type but gave up that
    guarantee: each emitter that builds an x axis without going through
    the engine's x-axis builder needs the shared emitter wired in, or the
    loud authoring error silently becomes a no-op — the exact failure shape
    this task exists to remove. One test per such path.
    """

    def test_horizontal_bar_x_with_step_raises_naming_the_orientation(self):
        """A horizontal bar's axis_x is the CATEGORICAL axis (it emits as the
        VL y encoding); the measure lives on axis_y. A tick interval against a
        discrete domain is meaningless, and the remedy is the orientation --
        the same shape ``labels.values`` already raises with here."""
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(ticks=DimensionTicksStylePatch(step=1000))
        )
        data = [{"team": t, "value": i} for i, t in enumerate(["Ops", "Eng", "Sales"])]
        chart = BarChart(id="hbar", type="bar", x="team", y="value", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        with pytest.raises(ChartDataError, match="ticks.step") as exc:
            render_resolved_chart(resolved, data, _BOARD_STYLE)
        assert "orientation" in str(exc.value)

    def test_multi_measure_heatmap_x_with_step_raises(self):
        """A list-y heatmap returns a layered spec whose top-level x carries no
        axis dict at all, so there is nothing for a cadence to merge into --
        but the band still draws category labels, and the single-measure
        heatmap path raises here. Validate-only, same rule."""
        style = HeatmapChartStylePatch(
            axis_x=AxisXStylePatch(ticks=DimensionTicksStylePatch(step=1000))
        )
        data = [
            {"region": r, "m1": i, "m2": i * 2}
            for i, r in enumerate(["North", "South", "East"])
        ]
        chart = HeatmapChart(
            id="hm", type="heatmap", x="region", y=["m1", "m2"], style=style
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        with pytest.raises(ChartDataError, match="ticks.step"):
            render_resolved_chart(resolved, data, _BOARD_STYLE)
