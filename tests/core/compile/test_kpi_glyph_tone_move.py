"""Tests for the KPI glyph/tone grammar: content at chart root, tone in style.

Covers:
* Rejection of tone: at chart root with a did-you-mean hint; glyph: is a root
  field.
* The root glyph compiles through the cascade and renders correctly.
* style.tone is retired entirely — tone lives only on style.support.tone (the
  block it paints); it is rejected at the chart root, the style namespace, and
  the support block.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.authored import (
    KpiChart as AuthoredKpiChart,
    KpiSupportConfig,
)
from dbt_charts.core.compile.models.chart.normalized import Chart, KpiChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

from .._engine_render import render_kpi_svg

_DUMMY_QUERY = SqlQuery(sql="SELECT 1", source="t")


def _es():
    return resolve_chart_style_context(get_theme_style())


def _board_style():
    return resolve_style(get_theme_style())


def _chart_style_context():
    return resolve_chart_style_context(get_theme_style())


# ---------------------------------------------------------------------------
# Rejection: glyph / tone at chart root must raise with did-you-mean hint
# ---------------------------------------------------------------------------


class TestChartRootRejection:
    def test_glyph_at_chart_root_accepted(self):
        """glyph: is the headline glyph's content, so it lives at the chart root."""
        chart = AuthoredKpiChart(type="kpi", value="revenue", glyph="▲")
        assert chart.glyph == "▲"

    def test_tone_at_chart_root_rejected_with_hint(self):
        """tone: at KPI chart root must be rejected; author must use style.support.tone —
        tone lives on the block it paints (the support row), not the headline."""
        with pytest.raises(ValidationError, match="style.support.tone"):
            AuthoredKpiChart(type="kpi", value="revenue", tone="positive")

    def test_tone_rejection_message_recommended_shape_validates(self):
        """The exact shape recommended in the rejection message is valid.

        style.support.tone needs no support content block, so authors who
        follow the hint will not hit a second validation error.
        """
        with pytest.raises(ValidationError) as exc_info:
            AuthoredKpiChart(type="kpi", value="revenue", tone="positive")
        assert "style:\n    support:\n      tone: positive" in str(exc_info.value)
        chart = AuthoredKpiChart(
            type="kpi",
            value="revenue",
            style=KpiChartStylePatch.model_validate({"support": {"tone": "positive"}}),
        )
        assert chart.style is not None
        assert chart.style.support is not None
        assert chart.style.support.tone == "positive"

    def test_tone_on_support_block_rejected(self):
        """KpiSupportConfig carries content only; tone is style.support.tone."""
        with pytest.raises(ValidationError, match="tone"):
            KpiSupportConfig(value="delta_col", tone="positive")  # type: ignore[call-arg]

    def test_both_at_chart_root_rejected(self):
        with pytest.raises(ValidationError):
            AuthoredKpiChart(type="kpi", value="revenue", glyph="▲", tone="positive")

    def test_format_at_chart_root_rejected_with_hint(self):
        """format: at KPI chart root must be rejected; author must use style.value.format."""
        with pytest.raises(ValidationError, match="style.value.format"):
            AuthoredKpiChart(type="kpi", value="revenue", format=",.0f")

    def test_formatter_at_chart_root_rejected_with_hint(self):
        """formatter: at KPI chart root must be rejected; author must use style.value.format."""
        with pytest.raises(ValidationError, match="style.value.format"):
            AuthoredKpiChart(type="kpi", value="revenue", formatter=",.0f")

    def test_style_value_format_accepted(self):
        """The corrected style.value.format shape is accepted at the chart root."""
        chart = AuthoredKpiChart(
            type="kpi",
            value="revenue",
            style=KpiChartStylePatch.model_validate({"value": {"format": ",.0f"}}),
        )
        assert chart.style is not None
        assert chart.style.value is not None
        assert chart.style.value.format == ",.0f"  # type: ignore[union-attr]

    def test_style_tone_rejected(self):
        """style.tone no longer exists on KpiChartStylePatch — extra=forbid rejects it."""
        with pytest.raises(ValidationError, match="tone"):
            KpiChartStylePatch.model_validate({"tone": "positive"})


# ---------------------------------------------------------------------------
# Positive path: the root glyph compiles and renders; style.support.tone
# paints the support row, never the headline value or its glyph.
# ---------------------------------------------------------------------------


def _compiled_kpi_with_style(
    *, glyph_char: str | None = None, support_tone: str | None = None
) -> Chart:
    """Build a compiled Chart with a root glyph and an optional tone-carrying
    support row (content in ``support``, tone in ``style.support``)."""
    style = (
        KpiChartStylePatch.model_validate({"support": {"tone": support_tone}})
        if support_tone is not None
        else None
    )
    support = (
        KpiSupportConfig(label="vs last quarter") if support_tone is not None else None
    )
    return KpiChart(
        id="t",
        query=_DUMMY_QUERY,
        query_name="q",
        type="kpi",
        label="Revenue",
        value="revenue",
        glyph=glyph_char,
        style=style,
        support=support,
    )


class TestStyleNamespacePositivePath:
    def test_glyph_character_renders_in_svg(self):
        """The root glyph flows through resolve and appears in KPI SVG output."""
        chart = _compiled_kpi_with_style(glyph_char="▲")
        data = [{"revenue": 1_500_000}]
        resolved = resolve(chart, data, chart_style_context=_chart_style_context())
        svg = render_kpi_svg(
            resolved, data, width=300, height=160, board_style=_board_style()
        )
        assert "▲" in svg

    def test_glyph_character_renders_neutral_regardless_of_support_tone(self):
        """The value-row glyph has no tone source of its own — it renders in the
        neutral value color even when the support row carries a tone."""
        chart = _compiled_kpi_with_style(glyph_char="●", support_tone="positive")
        data = [{"revenue": 1_500_000}]
        resolved = resolve(chart, data, chart_style_context=_chart_style_context())
        es = _es()
        svg = render_kpi_svg(
            resolved, data, width=300, height=160, board_style=_board_style()
        )
        assert "●" in svg
        # The value-row glyph tspan carries the neutral kpi font color, not
        # the support tone — assert the tone hex is absent from that tspan.
        import re

        glyph_tspan = re.search(r"<tspan[^>]*>●\s*</tspan>", svg)
        assert glyph_tspan is not None
        assert es.tones.positive not in glyph_tspan.group(0)

    def test_no_glyph_character_no_glyph_in_svg(self):
        """When the root glyph is absent, no glyph tspan is emitted."""
        chart = _compiled_kpi_with_style()
        data = [{"revenue": 1_500_000}]
        resolved = resolve(chart, data, chart_style_context=_chart_style_context())
        svg = render_kpi_svg(
            resolved, data, width=300, height=160, board_style=_board_style()
        )
        # No ▲, ▼, ● or similar glyphs expected in a plain KPI
        for glyph_char in ("▲", "▼", "●"):
            assert glyph_char not in svg

    def test_style_cascade_via_normalize_chart(self):
        """Authored root `glyph` and style dict { support: { tone } } survive
        compile normalization (the _wrap_authored_style path)."""
        from dbt_charts.core.compile.normalize.charts import normalize_chart

        query_registry = {"q": SqlQuery(sql="SELECT 1", source="t")}
        chart = normalize_chart(
            "revenue_kpi",
            {
                "type": "kpi",
                "query": "q",
                "value": "revenue",
                "glyph": "▲",
                "style": {"support": {"tone": "positive"}},
            },
            query_registry,
            sources={},
        )
        assert chart.glyph == "▲"
        assert chart.style is not None
        assert chart.style.support is not None
        assert chart.style.support.tone == "positive"  # type: ignore[union-attr]

    def test_style_tone_via_normalize_chart_rejected(self):
        """style.tone in authored YAML fails compilation — the field is gone."""
        from dbt_charts.core.compile.errors import CompilationError
        from dbt_charts.core.compile.normalize.charts import normalize_chart

        query_registry = {"q": SqlQuery(sql="SELECT 1", source="t")}
        with pytest.raises(CompilationError, match="tone"):
            normalize_chart(
                "revenue_kpi",
                {
                    "type": "kpi",
                    "query": "q",
                    "value": "revenue",
                    "style": {"tone": "positive"},
                },
                query_registry,
                sources={},
            )
