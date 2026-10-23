"""Tests for per-chart-type style field wiring through the unified mark emitter.

After unification, mark properties flow to spec.mark.{...} (or layer[0].mark
for layered specs). Tests validate distinctive values propagate correctly.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import KpiChart, PieChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError

from ._engine_render import render_pie_svg

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
