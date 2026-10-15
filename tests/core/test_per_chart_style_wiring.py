"""Tests for per-chart-type style field wiring through the unified mark emitter.

After unification, mark properties flow to spec.mark.{...} (or layer[0].mark
for layered specs). Tests validate distinctive values propagate correctly.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.normalized import Chart, KpiChart, PieChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from ._engine_render import render_pie_svg

_DATA = [{"month": "Jan", "rev": 100}]


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _spec_with_theme(chart_type: str, **chart_overrides):
    """Render a chart using a theme with distinctive mark overrides, return VL spec."""
    compiled = get_theme_style()
    charts = compiled.charts
    for style_key, field_overrides in chart_overrides.items():
        chart_style = getattr(charts, style_key)
        # For nested dict values, merge into the existing sub-model so required
        # fields (e.g. MarkPointStyle.size) are preserved from the theme default.
        coerced: dict = {}
        for fname, val in field_overrides.items():
            if isinstance(val, dict):
                sub = getattr(chart_style, fname, None)
                if hasattr(sub, "model_dump"):
                    val = type(sub).model_validate({**sub.model_dump(), **val})
            coerced[fname] = val
        updated = chart_style.model_copy(update=coerced)
        charts = charts.model_copy(update={style_key: updated})
    resolved, _ctx = resolve_style_and_context(
        compiled.model_copy(update={"charts": charts})
    )

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": "month",
            "y": "rev",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
        }
    )
    return generate_vega_lite_spec(
        chart, _DATA, board_style=resolved, chart_style_context=_ctx, width=400
    )


def _get_mark(spec: dict) -> dict:
    """Get the foreground mark dict from single-spec or layered spec.

    For halo line charts, the foreground is the last line-type layer.
    For other specs, returns the single mark or first layer.
    """
    m = spec.get("mark", {})
    if isinstance(m, dict) and m:
        return m
    layers = spec.get("layer", [])
    if not layers:
        return {}
    for layer in reversed(layers):
        lm = layer.get("mark", {})
        if isinstance(lm, dict) and lm.get("type") == "line":
            return lm
    return layers[0].get("mark", {})


class TestLineCurveWiring:
    """LineChartStyle.marks.line.curve → spec.mark.interpolate."""

    def test_distinctive_curve_appears_in_mark(self):
        spec = _spec_with_theme("line", line={"marks": {"line": {"curve": "monotone"}}})
        assert _get_mark(spec).get("interpolate") == "monotone"

    def test_curve_propagation_differs_across_values(self):
        spec_linear = _spec_with_theme(
            "line", line={"marks": {"line": {"curve": "linear"}}}
        )
        spec_step = _spec_with_theme(
            "line", line={"marks": {"line": {"curve": "step"}}}
        )
        assert _get_mark(spec_linear).get("interpolate") != _get_mark(spec_step).get(
            "interpolate"
        )


def _visible_point_marks(spec: dict) -> list[dict]:
    """Layered-spec point marks with opacity != 0 (the hover overlay is always
    present at opacity 0 and is never a "visible point" for these purposes).

    With halo enabled (default), this includes a halo point ring alongside the
    foreground point -- the halo ring's size independently scales off the
    foreground size (see _layers.py's halo_multiplier), so use
    _foreground_point_mark to check an authored size value.
    """
    return [
        lyr["mark"]
        for lyr in spec.get("layer", [])
        if isinstance(lyr.get("mark"), dict)
        and lyr["mark"].get("type") == "point"
        and lyr["mark"].get("opacity", 1) != 0
    ]


def _foreground_point_mark(spec: dict) -> dict | None:
    """The last visible point-type layer -- the foreground point, after any
    halo ring (see _visible_point_marks)."""
    visible = _visible_point_marks(spec)
    return visible[-1] if visible else None


class TestLinePointSizeWiring:
    """LineChartStyle.marks.point.size → point marks in spec.

    An explicit size at ANY tier (theme, board, or chart) is authored: it
    survives verbatim and is never replaced by the density-driven default.
    Only a value no tier ever set (None all the way down) is density-driven
    (bake_point_companions). This single-point fixture (n_pts=1, always past
    the density trigger) would show auto-sized points regardless -- so a
    non-zero-size test must check the actual baked size, not just presence,
    to prove the authored value (not an auto-computed one) landed in the spec.
    """

    def test_nonzero_point_size_enables_line_points(self):
        spec = _spec_with_theme("line", line={"marks": {"point": {"size": 50.0}}})
        # With halo enabled (default), points are separate layers.
        # Without halo, mark.point is set.
        layers = spec.get("layer", [])
        if layers:
            point_marks = [
                lyr
                for lyr in layers
                if isinstance(lyr.get("mark"), dict)
                and lyr["mark"].get("type") == "point"
            ]
            assert len(point_marks) > 0
        else:
            mark = _get_mark(spec)
            assert mark.get("point") is not None and mark.get("point") is not False

    def test_theme_tier_nonzero_size_is_respected_verbatim(self):
        """A theme-level size must land in the spec unchanged, not be replaced
        by bake_point_companions's density-driven auto-size."""
        spec = _spec_with_theme("line", line={"marks": {"point": {"size": 50.0}}})
        fg = _foreground_point_mark(spec)
        assert fg is not None and fg.get("size") == 50.0, fg

    def test_theme_tier_size_zero_disables_points(self):
        """A theme-level point.size=0 must turn points off board/theme-wide.

        Regression for the tier bug: the authored-size check used to read only the
        chart's own style: block, so a theme (or board) setting size=0 was
        indistinguishable from the unset global default and got silently
        overridden by the density-driven auto-size -- the exact surface
        PointMarkStyle.size's own description promises ("0 disables points").
        """
        spec = _spec_with_theme("line", line={"marks": {"point": {"size": 0.0}}})
        assert _visible_point_marks(spec) == []

    def test_zero_point_size_does_not_enable_line_points(self):
        """An author-pinned (chart-tier) point.size=0 must not add visible
        point marks -- the innermost tier keeps working exactly as before."""
        compiled = get_theme_style()
        resolved, ctx = resolve_style_and_context(compiled)
        chart = TypeAdapter(Chart).validate_python(
            {
                "id": "t",
                "type": "line",
                "x": "month",
                "y": "rev",
                "query": SqlQuery(sql="SELECT 1", source="src"),
                "query_name": "q",
                "style": {"marks": {"point": {"size": 0.0}}},
            }
        )
        spec = generate_vega_lite_spec(
            chart, _DATA, board_style=resolved, chart_style_context=ctx, width=400
        )
        assert _visible_point_marks(spec) == []


def _spec_with_global_point_override(chart_type: str, **point_overrides):
    """Render a chart with overrides on the effective (global or family) marks.point style.

    Injects at the family tier when the chart type has its own family-level point mark,
    otherwise injects at the global tier. Matches the _board_with_stroke pattern so that
    family-level point defaults (e.g. scatter.marks.point.size) are preserved.
    """
    compiled = get_theme_style()
    chart_style = getattr(compiled.charts, chart_type)
    family_marks = chart_style.marks
    base_point = (
        family_marks.point
        if family_marks.point is not None
        else compiled.charts.marks.point
    )
    new_point = base_point.model_copy(update=point_overrides)
    new_family_marks = family_marks.model_copy(update={"point": new_point})
    updated_chart = chart_style.model_copy(update={"marks": new_family_marks})
    charts = compiled.charts.model_copy(update={chart_type: updated_chart})
    resolved, _ctx = resolve_style_and_context(
        compiled.model_copy(update={"charts": charts})
    )

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": "month",
            "y": "rev",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
        }
    )
    return generate_vega_lite_spec(
        chart, _DATA, board_style=resolved, chart_style_context=_ctx, width=400
    )


class TestScatterPointWiring:
    """PointMarkStyle fields → spec.mark.{size,opacity,filled} on scatter.

    Injects at the family tier (scatter.marks.point) because scatter has its own
    family-level point default (size: 60 for visible dots). The family tier wins
    over global in the cascade, so family-level overrides are the effective tier.
    """

    def test_distinctive_point_size_appears_in_mark(self):
        spec = _spec_with_global_point_override("scatter", size=99.0)
        assert _get_mark(spec).get("size") == 99.0

    def test_distinctive_opacity_appears_in_mark(self):
        spec = _spec_with_global_point_override("scatter", opacity=0.33)
        assert _get_mark(spec).get("opacity") == pytest.approx(0.33)

    def test_filled_true_appears_in_mark(self):
        spec = _spec_with_global_point_override("scatter", filled=True)
        assert _get_mark(spec).get("filled") is True

    def test_filled_false_appears_in_mark(self):
        spec = _spec_with_global_point_override("scatter", filled=False)
        assert _get_mark(spec).get("filled") is False

    def test_point_size_and_opacity_propagate_independently(self):
        spec_a = _spec_with_global_point_override("scatter", size=10.0, opacity=0.1)
        spec_b = _spec_with_global_point_override("scatter", size=90.0, opacity=0.9)
        assert _get_mark(spec_a).get("size") != _get_mark(spec_b).get("size")
        assert _get_mark(spec_a).get("opacity") != _get_mark(spec_b).get("opacity")


class TestScatterPointColorWiring:
    """marks.point.color paints a single-series scatter's dots; fill is a hollow dot's interior."""

    def test_point_color_paints_the_dots(self):
        spec = _spec_with_global_point_override("scatter", color="#ff0000")
        assert _get_mark(spec).get("fill") == "#ff0000"

    def test_hollow_point_rings_in_color_around_its_fill(self):
        spec = _spec_with_global_point_override(
            "scatter", color="#ff0000", fill="#00ff00", filled=False
        )
        mark = _get_mark(spec)
        assert (mark.get("fill"), mark.get("color")) == ("#00ff00", "#ff0000")

    def test_unauthored_color_keeps_the_single_series_ink(self):
        spec = _spec_with_global_point_override("scatter", size=60.0)
        assert _get_mark(spec).get("fill") == "#284980"


_PIE_DATA = [
    {"month": "Jan", "rev": 100},
    {"month": "Feb", "rev": 60},
    {"month": "Mar", "rev": 40},
]


def _pie_wedges(**slice_overrides: Any) -> list[dict[str, str]]:
    """The engine-drawn wedges of a pie under a theme whose
    ``charts.marks.slice`` carries ``slice_overrides``: each wedge's attributes,
    its path as ``d``."""
    compiled = get_theme_style()
    marks = compiled.charts.marks
    updated_slice = marks.slice.model_copy(update=slice_overrides)
    charts = compiled.charts.model_copy(
        update={"marks": marks.model_copy(update={"slice": updated_slice})}
    )
    chart = PieChart(id="t", type="pie", theta="rev", color="month", query_name="q")
    svg = render_pie_svg(
        chart, _PIE_DATA, theme=compiled.model_copy(update={"charts": charts})
    )
    return [
        dict(re.findall(r'([\w-]+)="([^"]*)"', wedge))
        for wedge in re.findall(r'<path [^>]*aria-roledescription="arc mark"', svg)
    ]


def _stroke(**stroke_overrides: Any) -> list[dict[str, str]]:
    stroke = get_theme_style().charts.marks.slice.stroke
    return _pie_wedges(stroke=stroke.model_copy(update=stroke_overrides))


class TestArcWiring:
    """SliceMarkStyle fields reach every engine-drawn wedge."""

    def test_distinctive_pad_angle_reaches_the_wedges(self):
        assert [w["d"] for w in _pie_wedges(gap=0.07)] != [
            w["d"] for w in _pie_wedges(gap=0.0)
        ]

    def test_distinctive_corner_radius_reaches_the_wedges(self):
        wedges = _pie_wedges(corner_radius=9.0)
        assert len(wedges) == len(_PIE_DATA)
        assert all("A9,9," in w["d"] for w in wedges)

    def test_distinctive_stroke_reaches_the_wedges(self):
        assert {w["stroke"] for w in _stroke(color="#abcdef")} == {"#abcdef"}

    def test_distinctive_stroke_width_reaches_the_wedges(self):
        assert {w["stroke-width"] for w in _stroke(width=2.5)} == {"2.5"}

    def test_distinctive_stroke_join_reaches_the_wedges(self):
        assert {w["stroke-linejoin"] for w in _stroke(join="bevel")} == {"bevel"}

    def test_arc_fields_propagate_independently(self):
        a = _pie_wedges(corner_radius=2.0)
        b = _pie_wedges(corner_radius=8.0)
        assert len(a) == len(b) == len(_PIE_DATA)
        assert all("A2,2," in w["d"] for w in a)
        assert all("A8,8," in w["d"] for w in b)
        assert {w["stroke-width"] for w in _stroke(width=1.0)} != {
            w["stroke-width"] for w in _stroke(width=3.0)
        }

    def test_distinctive_opacity_reaches_the_wedges(self):
        assert {w["opacity"] for w in _pie_wedges(opacity=0.55)} == {"0.55"}

    def test_color_encoding_fills_each_wedge_its_own_color(self):
        """A pie never paints one static fill over its color encoding."""
        assert len({w["fill"] for w in _pie_wedges()}) == len(_PIE_DATA)


class TestArcTotalStyleCascade:
    """charts.pie.total.{value,label}.font flows through the theme cascade."""

    def test_value_and_label_slots_resolve_to_concrete_font(self):
        compiled = get_theme_style()
        ctx = resolve_chart_style_context(compiled)
        total = ctx.pie.total
        # Cascade fills the family from the parent charts font.
        assert total.value.font.family
        assert total.label.font.family

    def test_distinctive_value_font_size_overrides_cascade(self):
        from dbt_charts.core.compile.models.primitives import FontStyle

        compiled = get_theme_style()
        pie = compiled.charts.pie
        pie_override = pie.model_copy(
            update={
                "total": pie.total.model_copy(
                    update={
                        "value": pie.total.value.model_copy(
                            update={"font": FontStyle(size=99.0)}
                        )
                    }
                )
            }
        )
        compiled_override = compiled.model_copy(
            update={"charts": compiled.charts.model_copy(update={"pie": pie_override})}
        )
        ctx = resolve_chart_style_context(compiled_override)
        assert ctx.pie.total.value.font.size == 99.0


def _resolved_pie_chart(total=None, style_overrides=None):
    """Build a real ResolvedChart for a pie via the public compile path."""
    from dbt_charts.core.compile.models.chart.authored import ChartTotal

    chart = PieChart(
        id="donut",
        type="pie",
        theta="value",
        color="segment",
        style=style_overrides or {},
        total=ChartTotal.model_validate(total) if total else None,
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
    )
    return resolve(
        chart,
        [{"segment": "A", "value": 60}, {"segment": "B", "value": 40}],
        chart_style_context=resolve_chart_style_context(get_theme_style()),
    )


def _slice_labels_style(template=None, where=None):
    """Build a minimal SliceLabelsStyle for label-rendering tests."""
    from dbt_charts.core.compile.models.style.theme import (
        LabelsDefaultTemplate,
        SliceLabelsStyle,
    )

    dt = LabelsDefaultTemplate(
        with_color="{{ color }}: {{ value }}", no_color="{{ value }}"
    )
    return SliceLabelsStyle(
        offset=1, line_height=1, default_template=dt, template=template, where=where
    )


class TestLabelTemplate:
    """Label template validates; prepare_label_data renders + filters."""

    def test_template_parses_when_valid(self):
        c = _slice_labels_style(template="{{ percent | format('.0%') }} {{ color }}")
        assert c.template

    def test_template_rejects_broken_jinja(self):
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme import (
            LabelsDefaultTemplate,
            SliceLabelsStyle,
        )

        dt = LabelsDefaultTemplate(
            with_color="{{ color }}: {{ value }}", no_color="{{ value }}"
        )
        with pytest.raises(ValidationError):
            SliceLabelsStyle(
                offset=1, line_height=1, default_template=dt, template="{{ broken"
            )

    def test_where_rejects_broken_jinja(self):
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme import (
            LabelsDefaultTemplate,
            SliceLabelsStyle,
        )

        dt = LabelsDefaultTemplate(
            with_color="{{ color }}: {{ value }}", no_color="{{ value }}"
        )
        with pytest.raises(ValidationError):
            SliceLabelsStyle(
                offset=1,
                line_height=1,
                default_template=dt,
                template="x",
                where="value >>>> 2",
            )

    def test_authored_where_survives_resolve(self):
        resolved = _resolved_pie_chart(
            style_overrides={"marks": {"slice": {"labels": {"where": "value > 50"}}}}
        )
        assert resolved.style.slice_mark.labels is not None
        assert resolved.style.slice_mark.labels.where == "value > 50"

    def test_prepare_label_data_renders_per_row(self):
        from dbt_charts.core.compile.resolve.chart.label_data import prepare_label_data

        rows = [
            {"segment": "A", "value": 60},
            {"segment": "B", "value": 40},
        ]
        labels = _slice_labels_style(
            template="{{ percent | format('.0%') }} {{ color }}\n${{ value }}",
        )
        rendered = prepare_label_data(
            rows,
            labels,
            context_extras=lambda row, index: {
                "percent": row["value"] / 100.0,
                "value": row["value"],
                "total": 100,
                "color": row["segment"],
            },
        )
        assert len(rendered) == 2
        assert rendered[0]["__dbt_label"] == ["60% A", "$60"]
        assert rendered[1]["__dbt_label"] == ["40% B", "$40"]

    def test_prepare_label_data_filters_by_where(self):
        from dbt_charts.core.compile.resolve.chart.label_data import prepare_label_data

        rows = [{"value": 1}, {"value": 2}, {"value": 3}]
        labels = _slice_labels_style(
            template="{{ value }}",
            where="value >= 2",
        )
        rendered = prepare_label_data(
            rows,
            labels,
            context_extras=lambda row, index: {"value": row["value"]},
        )
        # Filtered rows stay in the dataset (so the family hook's positioning
        # transforms still reference a row per slice) but get
        # __dbt_label = None so the family layer's VL filter drops the mark.
        assert len(rendered) == 3
        assert rendered[0]["__dbt_label"] is None
        assert rendered[1]["__dbt_label"] == ["2"]
        assert rendered[2]["__dbt_label"] == ["3"]

    def test_prepare_label_data_accepts_brace_wrapped_where(self):
        """Authors may write ``where: '{{ value > 0 }}'`` — strip braces."""
        from dbt_charts.core.compile.resolve.chart.label_data import prepare_label_data

        rows = [{"value": 1}, {"value": 0}]
        labels = _slice_labels_style(template="{{ value }}", where="{{ value > 0 }}")
        rendered = prepare_label_data(
            rows, labels, context_extras=lambda row, index: {"value": row["value"]}
        )
        assert rendered[0]["__dbt_label"] == ["1"]
        assert rendered[1]["__dbt_label"] is None

    def test_prepare_label_data_strict_undefined(self):
        from jinja2 import UndefinedError

        from dbt_charts.core.compile.resolve.chart.label_data import prepare_label_data

        labels = _slice_labels_style(template="{{ unknown_var }}")
        with pytest.raises(UndefinedError):
            prepare_label_data(
                [{"a": 1}],
                labels,
                context_extras=lambda row, index: {},
            )


class TestKpiStyleCascade:
    """kpi.font (parent) cascades into every slot's font block."""

    def _compiled_with_kpi_weight(self, weight: float) -> object:
        from dbt_charts.core.compile.models.primitives import FontStyle

        compiled = get_theme_style()
        kpi = compiled.charts.kpi
        kpi_override = kpi.model_copy(update={"font": FontStyle(weight=weight)})
        return compiled.model_copy(
            update={"charts": compiled.charts.model_copy(update={"kpi": kpi_override})}
        )

    def test_parent_font_weight_cascades_into_value_slot(self):
        """kpi.font.weight must cascade into kpi.value.font.weight."""
        compiled = self._compiled_with_kpi_weight(701)
        ctx = resolve_chart_style_context(compiled)
        assert ctx.kpi.value.font.weight == 701

    def test_parent_font_weight_cascades_into_label_slot(self):
        """kpi.font.weight must cascade into kpi.label.font.weight."""
        compiled = self._compiled_with_kpi_weight(702)
        ctx = resolve_chart_style_context(compiled)
        assert ctx.kpi.label.font.weight == 702

    def test_parent_font_weight_cascades_into_affix_slot(self):
        """kpi.font.weight must cascade into kpi.value.affix.font.weight."""
        compiled = self._compiled_with_kpi_weight(703)
        ctx = resolve_chart_style_context(compiled)
        assert ctx.kpi.value.affix.font.weight == 703

    def test_parent_font_weight_cascades_into_glyph_slot(self):
        """kpi.font.weight must cascade into kpi.value.glyph.font.weight."""
        compiled = self._compiled_with_kpi_weight(704)
        ctx = resolve_chart_style_context(compiled)
        assert ctx.kpi.value.glyph.font.weight == 704

    def test_parent_font_weight_cascades_into_support_slots(self):
        """kpi.font.weight must cascade into kpi.support.font and its glyph."""
        compiled = self._compiled_with_kpi_weight(705)
        ctx = resolve_chart_style_context(compiled)
        assert ctx.kpi.support.font.weight == 705
        assert ctx.kpi.support.glyph.font.weight == 705

    def test_slot_override_wins_over_parent_cascade(self):
        """A slot-level font override takes precedence over parent cascade."""
        from dbt_charts.core.compile.models.primitives import FontStyle

        compiled = get_theme_style()
        kpi = compiled.charts.kpi
        kpi_override = kpi.model_copy(
            update={
                "font": FontStyle(weight=600),
                "value": kpi.value.model_copy(update={"font": FontStyle(weight=800)}),
            }
        )
        ctx = resolve_chart_style_context(
            compiled.model_copy(
                update={
                    "charts": compiled.charts.model_copy(update={"kpi": kpi_override})
                }
            )
        )
        assert ctx.kpi.value.font.weight == 800
        # label/support inherit the parent weight
        assert ctx.kpi.label.font.weight == 600

    def test_value_font_size_accessible_on_value_slot(self):
        """kpi.value.font.size is set by the theme (no floor logic)."""
        compiled = get_theme_style()
        ctx = resolve_chart_style_context(compiled)
        assert ctx.kpi.value.font.size is not None
        assert float(ctx.kpi.value.font.size) > 0

    def test_kpi_value_font_size_none_asserts(self):
        """Render raises loudly when kpi.value.font is unresolved (cascade contract)."""
        import dataclasses

        from dbt_charts.core.compile.models.primitives import FontStyle

        from ._engine_render import render_kpi_svg

        compiled = get_theme_style()
        resolved, ctx = resolve_style_and_context(compiled)
        kpi = ctx.kpi
        kpi_override = kpi.model_copy(
            update={
                "value": kpi.value.model_copy(update={"font": FontStyle(size=None)})
            }
        )
        # Build patched ChartStyleContext for resolve(); use original resolved for render.
        patched_ctx = dataclasses.replace(ctx, kpi=kpi_override)

        data = [{"kpi_val": 42}]
        raw_chart = KpiChart(id="test_kpi", type="kpi", value="kpi_val", label="KPI")
        resolved_chart = resolve(raw_chart, data, chart_style_context=patched_ctx)
        # Inject the nil-size style directly onto the resolved chart.
        patched_chart = resolved_chart.model_copy(
            update={
                "style": resolved_chart.style.model_copy(update={"kpi": kpi_override})
            }
        )

        with pytest.raises(ChartDataError, match="value.font is unresolved"):
            render_kpi_svg(patched_chart, data, board_style=resolved)
