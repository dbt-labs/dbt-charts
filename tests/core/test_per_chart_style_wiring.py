"""Tests for per-chart-type style field wiring through the unified mark emitter.

After unification, mark properties flow to spec.mark.{...} (or layer[0].mark
for layered specs). Tests validate distinctive values propagate correctly.
"""

from __future__ import annotations

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
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

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

        Regression for the tier bug: size_authored used to read only the
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


def _pie_spec_with_theme(**chart_overrides):
    """Like ``_spec_with_theme`` but builds a pie-shaped chart (theta/color)."""
    compiled = get_theme_style()
    charts = compiled.charts
    for style_key, field_overrides in chart_overrides.items():
        chart_style = getattr(charts, style_key)
        updated = chart_style.model_copy(update=field_overrides)
        charts = charts.model_copy(update={style_key: updated})
    resolved, _ctx = resolve_style_and_context(
        compiled.model_copy(update={"charts": charts})
    )

    chart = PieChart(
        id="t",
        type="pie",
        theta="rev",
        color="month",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
    )
    return generate_vega_lite_spec(
        chart, _DATA, board_style=resolved, chart_style_context=_ctx, width=400
    )


class TestArcWiring:
    """SliceMarkStyle fields → spec.mark.{...} on pie charts."""

    def _pie_spec_with_slice_stroke(self, **stroke_overrides):
        """Build a pie spec with a StrokeStyle override on marks.slice.stroke."""
        compiled = get_theme_style()
        global_slice = compiled.charts.marks.slice
        updated_slice = global_slice.model_copy(
            update={"stroke": global_slice.stroke.model_copy(update=stroke_overrides)}
        )
        updated_marks = compiled.charts.marks.model_copy(
            update={"slice": updated_slice}
        )
        charts = compiled.charts.model_copy(update={"marks": updated_marks})
        resolved, _ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = PieChart(
            id="t",
            type="pie",
            theta="rev",
            color="month",
            query=SqlQuery(sql="SELECT 1", source="src"),
            query_name="q",
        )
        return generate_vega_lite_spec(
            chart, _DATA, board_style=resolved, chart_style_context=_ctx, width=400
        )

    def test_distinctive_pad_angle_appears_in_mark(self):
        compiled = get_theme_style()
        updated_slice = compiled.charts.marks.slice.model_copy(update={"gap": 0.07})
        updated_marks = compiled.charts.marks.model_copy(
            update={"slice": updated_slice}
        )
        charts = compiled.charts.model_copy(update={"marks": updated_marks})
        resolved, _ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = PieChart(
            id="t",
            type="pie",
            theta="rev",
            color="month",
            query=SqlQuery(sql="SELECT 1", source="src"),
            query_name="q",
        )
        spec = generate_vega_lite_spec(
            chart, _DATA, width=400, board_style=resolved, chart_style_context=_ctx
        )
        assert _get_mark(spec).get("padAngle") == pytest.approx(0.07)

    def test_distinctive_corner_radius_appears_in_mark(self):
        compiled = get_theme_style()
        updated_slice = compiled.charts.marks.slice.model_copy(
            update={"corner_radius": 9.0}
        )
        updated_marks = compiled.charts.marks.model_copy(
            update={"slice": updated_slice}
        )
        charts = compiled.charts.model_copy(update={"marks": updated_marks})
        resolved, _ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = PieChart(
            id="t",
            type="pie",
            theta="rev",
            color="month",
            query=SqlQuery(sql="SELECT 1", source="src"),
            query_name="q",
        )
        spec = generate_vega_lite_spec(
            chart, _DATA, width=400, board_style=resolved, chart_style_context=_ctx
        )
        assert _get_mark(spec).get("cornerRadius") == pytest.approx(9.0)

    def test_distinctive_stroke_appears_in_mark(self):
        spec = self._pie_spec_with_slice_stroke(color="#abcdef")
        assert _get_mark(spec).get("stroke") == "#abcdef"

    def test_distinctive_stroke_width_appears_in_mark(self):
        spec = self._pie_spec_with_slice_stroke(width=2.5)
        assert _get_mark(spec).get("strokeWidth") == pytest.approx(2.5)

    def test_distinctive_stroke_join_appears_in_mark(self):
        spec = self._pie_spec_with_slice_stroke(join="bevel")
        assert _get_mark(spec).get("strokeJoin") == "bevel"

    def test_arc_fields_propagate_independently(self):
        compiled = get_theme_style()

        def _pie_with(corner_radius: float, stroke_width: float):
            global_slice = compiled.charts.marks.slice
            updated_slice = global_slice.model_copy(
                update={
                    "corner_radius": corner_radius,
                    "stroke": global_slice.stroke.model_copy(
                        update={"width": stroke_width}
                    ),
                }
            )
            updated_marks = compiled.charts.marks.model_copy(
                update={"slice": updated_slice}
            )
            charts = compiled.charts.model_copy(update={"marks": updated_marks})
            resolved, _ctx = resolve_style_and_context(
                compiled.model_copy(update={"charts": charts})
            )
            chart = PieChart(
                id="t",
                type="pie",
                theta="rev",
                color="month",
                query=SqlQuery(sql="SELECT 1", source="src"),
                query_name="q",
            )
            return generate_vega_lite_spec(
                chart, _DATA, width=400, board_style=resolved, chart_style_context=_ctx
            )

        spec_a = _pie_with(corner_radius=1.0, stroke_width=1.0)
        spec_b = _pie_with(corner_radius=8.0, stroke_width=4.0)
        assert _get_mark(spec_a).get("cornerRadius") != _get_mark(spec_b).get(
            "cornerRadius"
        )
        assert _get_mark(spec_a).get("strokeWidth") != _get_mark(spec_b).get(
            "strokeWidth"
        )

    def test_distinctive_opacity_appears_in_mark(self):
        compiled = get_theme_style()
        updated_slice = compiled.charts.marks.slice.model_copy(update={"opacity": 0.55})
        updated_marks = compiled.charts.marks.model_copy(
            update={"slice": updated_slice}
        )
        charts = compiled.charts.model_copy(update={"marks": updated_marks})
        resolved, _ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = PieChart(
            id="t",
            type="pie",
            theta="rev",
            color="month",
            query=SqlQuery(sql="SELECT 1", source="src"),
            query_name="q",
        )
        spec = generate_vega_lite_spec(
            chart, _DATA, width=400, board_style=resolved, chart_style_context=_ctx
        )
        assert _get_mark(spec).get("opacity") == pytest.approx(0.55)

    def test_arc_mark_does_not_set_fill_so_color_encoding_wins(self):
        """Arc is the one mark family where ``mark.fill`` overrides
        ``encoding.color`` (opposite of bar/line/area). _build_mark_style
        must not set palette[0] on arc/pie or every slice renders the
        same color."""
        spec = _pie_spec_with_theme()
        assert "fill" not in _get_mark(spec)


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
        """kpi.font.weight must cascade into kpi.affix.font.weight."""
        compiled = self._compiled_with_kpi_weight(703)
        ctx = resolve_chart_style_context(compiled)
        assert ctx.kpi.affix.font.weight == 703

    def test_parent_font_weight_cascades_into_glyph_slot(self):
        """kpi.font.weight must cascade into kpi.glyph.font.weight."""
        compiled = self._compiled_with_kpi_weight(704)
        ctx = resolve_chart_style_context(compiled)
        assert ctx.kpi.glyph.font.weight == 704

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
        # label/affix/glyph inherit the parent weight
        assert ctx.kpi.label.font.weight == 600

    def test_value_font_size_accessible_on_value_slot(self):
        """kpi.value.font.size is set by the theme (no floor logic)."""
        compiled = get_theme_style()
        ctx = resolve_chart_style_context(compiled)
        assert ctx.kpi.value.font.size is not None
        assert float(ctx.kpi.value.font.size) > 0

    def test_kpi_value_font_size_none_asserts(self):
        """Assert fires loudly when kpi.value.font.size is None (cascade contract)."""
        import dataclasses

        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.render.chart.kpi import render_kpi_svg

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

        with pytest.raises(
            AssertionError, match="theme must supply kpi.value.font.size"
        ):
            render_kpi_svg(patched_chart, data, board_style=resolved)
