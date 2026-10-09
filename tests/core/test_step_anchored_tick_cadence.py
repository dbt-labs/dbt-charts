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
the quantitative/ordinal/temporal fork, see build_cartesian_x_encoding):
  3. ticks.time_unit/step on an x-axis that resolves to non-temporal
     (ordinal) -> render error, no silent no-op
  4. a bare ticks.step (no time_unit) on an x-axis that resolves to anything
     but quantitative -> render error. Formerly gate 1, enforced in the model;
     it moved here because "is this axis quantitative" is a data-dependent
     answer the model cannot see.
"""

from __future__ import annotations

import html
import json
import re

import pydantic
import pytest
import vl_convert as vlc

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    HeatmapChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
    AxisYStylePatch,
    BarChartStylePatch,
    DimensionTicksStylePatch,
    HeatmapChartStylePatch,
    LineChartStylePatch,
    ScatterChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.session import BoardRenderSession
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _annual_data(start: int = 1955, end: int = 2025) -> list[dict]:
    """~70 annual points — the domain width editorial 5/10-year cadence targets."""
    return [
        {"year": f"{y}-01-01", "value": 100 + i * 3}
        for i, y in enumerate(range(start, end + 1))
    ]


def _customer_scatter_data() -> list[dict]:
    """The reported shape: a customer count spanning 0-4k against a measure."""
    return [{"customers": i * 140.0, "arr": float(i)} for i in range(30)]


def _scatter_with_ticks(ticks: DimensionTicksStylePatch | None) -> ScatterChart:
    style = ScatterChartStylePatch(
        axis_x=AxisXStylePatch(ticks=ticks) if ticks is not None else None
    )
    return ScatterChart(
        id="scatter", type="scatter", x="customers", y="arr", style=style
    )


def _rendered_x_tick_labels(chart: ScatterChart, data: list[dict]) -> list[str]:
    """The x tick text Vega actually drew, in domain order.

    Scoped to the x-axis group by its aria-label rather than by position:
    both axes emit identically-classed label groups, so an index would pick
    up the y ladder the moment the emit order shifts.
    """
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
    session = BoardRenderSession.create(_BOARD_STYLE)
    spec = session.finalize_vl(
        session.emit_chart(
            resolved,
            RenderBox(width=600.0, height=300.0),
            {resolved.query_name: data},
        )
    )
    svg = vlc.vegalite_to_svg(json.dumps(spec))
    x_axis = svg[svg.index("X-axis titled") :]
    group = re.search(
        r'<g class="mark-text role-axis-label"[^>]*>(.*?)</g>', x_axis, re.S
    )
    assert group is not None, "no x-axis label group in the rendered SVG"
    labels = [
        html.unescape(text)
        for text in re.findall(r"<text[^>]*>(.*?)</text>", group.group(1), re.S)
    ]
    return [_parse_si(label) for label in labels]


_SI_MULTIPLIER = {"k": 1_000, "M": 1_000_000, "G": 1_000_000_000}


def _parse_si(label: str) -> float:
    """A rendered tick label back to the number it names.

    Tests assert on the ladder, not on the theme's number format — reading
    "1k" as 1000 keeps a cadence test failing only when the cadence changes.
    """
    text = label.replace(",", "")
    if text and text[-1] in _SI_MULTIPLIER:
        return float(text[:-1]) * _SI_MULTIPLIER[text[-1]]
    return float(text)


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


class TestEmission:
    """time_unit/step emit axis.tickCount: {interval, step} (VL's own
    d3-interval vocabulary, derived from the dbt charts grain) — no
    axis.values, no materialized date list."""

    def test_time_unit_only_emits_tick_count_interval(self):
        style = LineChartStylePatch(
            axis_x=AxisXStylePatch(ticks=DimensionTicksStylePatch(time_unit="year"))
        )
        data = _annual_data()
        chart = LineChart(id="line", type="line", x="year", y="value", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_STYLE).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "temporal"
        x_axis = x_enc.get("axis", {})
        assert x_axis.get("tickCount") == {"interval": "year"}
        assert "values" not in x_axis

    def test_time_unit_and_step_emits_both(self):
        style = LineChartStylePatch(
            axis_x=AxisXStylePatch(
                ticks=DimensionTicksStylePatch(time_unit="year", step=5)
            )
        )
        data = _annual_data()
        chart = LineChart(id="line", type="line", x="year", y="value", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_STYLE).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "temporal"
        x_axis = x_enc.get("axis", {})
        assert x_axis.get("tickCount") == {"interval": "year", "step": 5}
        assert "values" not in x_axis

    def test_yearquarter_and_yearmonth_grains_map_to_month_interval(self):
        """Neither dbt charts grain has a bare VL "quarter" interval — both
        derive VL's "month" interval via _TEMPORAL_TICK_INTERVAL. An explicit
        ticks.time_unit with no ticks.step emits interval alone; the grain's
        own anchoring step (3 for yearquarter, 1 for yearmonth) only applies
        to the auto-derived (no explicit ticks.time_unit) cadence path."""
        for grain in ("yearquarter", "yearmonth"):
            style = LineChartStylePatch(
                axis_x=AxisXStylePatch(ticks=DimensionTicksStylePatch(time_unit=grain))
            )
            data = _annual_data()
            chart = LineChart(id="line", type="line", x="year", y="value", style=style)
            resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
            spec = render_resolved_chart(resolved, data, _BOARD_STYLE).payload
            x_axis = spec.get("encoding", {}).get("x", {}).get("axis", {})
            assert x_axis.get("tickCount") == {"interval": "month"}, grain


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


class TestQuantitativeCadence:
    """A quantitative x-axis (scatter against a numeric column) reaches VL's
    numeric cadence properties: ``count`` -> ``tickCount``, ``step`` ->
    ``tickMinStep``. Before this existed, both fields validated, read as
    authored intent, and were dropped at the render boundary — ``count: 2``,
    ``count: 3`` and ``count: 5`` all produced the same ladder.
    """

    def test_count_emits_tick_count(self):
        data = _customer_scatter_data()
        chart = _scatter_with_ticks(DimensionTicksStylePatch(count=4))
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_STYLE).payload

        x_enc = spec.get("encoding", {}).get("x", {})
        assert x_enc.get("type") == "quantitative"
        assert x_enc.get("axis", {}).get("tickCount") == 4

    def test_step_emits_tick_min_step_not_explicit_values(self):
        """``tickMinStep`` is a floor Vega keeps honoring as the domain grows.
        A baked ``values`` ladder would freeze the axis against today's data —
        the exact trap the ``scale.values`` workaround has."""
        data = _customer_scatter_data()
        chart = _scatter_with_ticks(DimensionTicksStylePatch(step=1000))
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_STYLE).payload

        x_axis = spec.get("encoding", {}).get("x", {}).get("axis", {})
        assert x_axis.get("tickMinStep") == 1000
        assert "values" not in x_axis


class TestQuantitativeCadenceRendered:
    """Rendered through real Vega (vl_convert), not the emitted spec alone.

    ``TestQuantitativeCadence`` above already pins the emitted spec, and it
    routes through the same scatter fast path, so it is what guards the
    boundary the field used to die at. These tests answer the separate
    question that spec assertions cannot: whether Vega, given those
    properties, actually draws a different ladder. Labels are read as numbers
    rather than as text so a theme tweaking its number format doesn't fail a
    cadence test.
    """

    @pytest.mark.parametrize(
        ("step", "expected"),
        [
            (1000, [0, 1000, 2000, 3000, 4000]),
            (2000, [0, 2000, 4000]),
        ],
    )
    def test_step_renders_exactly_that_interval(self, step, expected):
        """The reported case: a 0-4k customer axis Vega ticks at 0.5k, which
        reads badly at distance. ``step`` is the lever that names the interval,
        and unlike ``count`` it is exact — ``tickMinStep`` is a floor Vega
        cannot go finer than. Two intervals, because a single one can coincide
        with whatever ladder the theme's own tick density would have produced.
        """
        data = _customer_scatter_data()
        # count is authored alongside step so the pinned ladder is step's
        # doing alone — inheriting the theme's own tick count would make a
        # theme tweak fail this test with no product defect.
        chart = _scatter_with_ticks(DimensionTicksStylePatch(count=6, step=step))
        assert _rendered_x_tick_labels(chart, data) == expected

    def test_count_changes_the_rendered_ladder(self):
        """Relational, not exact: VL's ``tickCount`` is advisory on a
        quantitative scale — Vega rounds to a nice step near the target, so
        pinning an exact count would encode d3's rounding rather than our
        contract. What must hold is that the field moves the ladder at all,
        which is precisely what it failed to do (2/3/5 all rendered nine
        ticks). ``step`` is the lever with an exact guarantee; see above.
        """
        data = _customer_scatter_data()
        sparse = _rendered_x_tick_labels(
            _scatter_with_ticks(DimensionTicksStylePatch(count=3)), data
        )
        dense = _rendered_x_tick_labels(
            _scatter_with_ticks(DimensionTicksStylePatch(count=10)), data
        )
        assert len(sparse) < len(dense)


class TestEveryXAxisPathIsCovered:
    """A bare ``ticks.step`` reaches a gate on every cartesian x path.

    The check used to live on the model, where it covered every emitter by
    construction. Moving it to render bought the axis type but gave up that
    guarantee: each emitter that builds an x axis without going through
    ``build_cartesian_x_encoding`` needs the shared emitter wired in, or the
    loud authoring error silently becomes a no-op — the exact failure shape
    this task exists to remove. One test per such path.
    """

    def test_histogram_x_honors_step(self):
        """A histogram's x IS the quantitative binned axis, so step applies
        there rather than raising."""
        style = BarChartStylePatch(
            axis_x=AxisXStylePatch(ticks=DimensionTicksStylePatch(step=1000))
        )
        data = [{"amount": float(i) * 140.0} for i in range(30)]
        chart = BarChart(id="hist", type="histogram", x="amount", y=None, style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_STYLE).payload
        assert spec["encoding"]["x"]["axis"].get("tickMinStep") == 1000

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

    @pytest.mark.parametrize("chart_type", ["bar", "line"])
    def test_numeric_x_bar_and_line_honor_step(self, chart_type):
        """Scatter and histogram are the obvious quantitative-x families, but
        any cartesian family gets a quantitative x from a numeric column —
        and with it the theme's own quantitative tick density. Pinned here so
        the vertical path's coverage isn't inferred from scatter alone."""
        cls = {"bar": BarChart, "line": LineChart}[chart_type]
        patch = {"bar": BarChartStylePatch, "line": LineChartStylePatch}[chart_type]
        style = patch(axis_x=AxisXStylePatch(ticks=DimensionTicksStylePatch(step=1000)))
        data = [{"customers": i * 140.0, "arr": float(i)} for i in range(30)]
        chart = cls(id=chart_type, type=chart_type, x="customers", y="arr", style=style)
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_STYLE).payload
        x_enc = spec["encoding"]["x"]
        assert x_enc["type"] == "quantitative"
        assert x_enc["axis"].get("tickMinStep") == 1000

    def test_scatter_authored_scale_values_still_wins_over_theme_count(self):
        """``scale.values`` names every tick outright. The theme's own
        quantitative tick density now reaches a quantitative x, so an
        unguarded merge would silently thin an authored ladder -- and
        ``scale.values`` is precisely the workaround boards use today.
        """
        values = [float(v) for v in range(0, 4400, 400)]
        style = ScatterChartStylePatch(axis_x=AxisXStylePatch(scale={"values": values}))
        data = _customer_scatter_data()
        chart = ScatterChart(
            id="scatter", type="scatter", x="customers", y="arr", style=style
        )
        resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, data, _BOARD_STYLE).payload
        x_axis = spec["encoding"]["x"]["axis"]
        assert x_axis["values"] == values
        assert "tickCount" not in x_axis
