"""Behavior tests for the KPI quantitative-text-object redesign.

Covers the contract changes:

* ``value:`` (column reference — always a column name) replaces ``metric:``
* ``style.glyph`` (character) on the headline value; no ``value_color`` / ``glyph_color``
* ``support: { value, label, format, glyph, tone }`` block — ``tone`` lives only
  here, since the support row is the block it paints (headline value stays neutral)
* Editorial notation default for the headline number
* Three-slot layout (value → label → support) with fixed structural minimum
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.authored import (
    KpiSupportConfig,
)
from dbt_charts.core.compile.models.chart.normalized import KpiChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.kpi import render_kpi_svg
from dbt_charts.core.render.errors import RenderError

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

_DUMMY_QUERY = SqlQuery(sql="SELECT 1", source="t")
_NS = {"svg": "http://www.w3.org/2000/svg"}


def _es():
    return resolve_style(get_theme_style()).chart_defaults


def _resolved_kpi(**kwargs):
    from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch

    data = kwargs.pop("_data", None) or [{"revenue": 1_500_000}]
    # format was removed from KpiChart root; move to style.value.format
    fmt = kwargs.pop("format", None)
    if fmt is not None:
        kwargs["style"] = KpiChartStylePatch.model_validate({"value": {"format": fmt}})
    chart = KpiChart(id="t", query=_DUMMY_QUERY, query_name="q", type="kpi", **kwargs)
    resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    return resolved, data


# ---------------------------------------------------------------------------
# Authoring contract — `value:` replaces `metric:`, KPI requires it
# ---------------------------------------------------------------------------


class TestKpiAuthoringContract:
    """Authoring-time validators live on the authored model (extra=forbid,
    subtitle/metric/numeric-literal rejection) — the normalized KpiChart
    used elsewhere in this file carries no author-facing validation."""

    def test_metric_field_rejected(self):
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.chart.authored import (
            KpiChart as AuthoredKpiChart,
        )

        with pytest.raises(ValidationError, match="metric"):
            TypeAdapter(AuthoredKpiChart).validate_python(
                {"type": "kpi", "metric": "revenue"}
            )

    def test_kpi_requires_value(self):
        from dbt_charts.core.compile.models.chart.authored import (
            KpiChart as AuthoredKpiChart,
        )

        # KpiChart.value is required (str, no default). Pydantic raises ValidationError.
        with pytest.raises(ValidationError, match="value"):
            TypeAdapter(AuthoredKpiChart).validate_python({"type": "kpi"})

    def test_kpi_subtitle_rejected_with_migration_hint(self):
        from dbt_charts.core.compile.models.chart.authored import (
            KpiChart as AuthoredKpiChart,
        )

        with pytest.raises(ValidationError, match="support:"):
            TypeAdapter(AuthoredKpiChart).validate_python(
                {"type": "kpi", "value": "revenue", "subtitle": "legacy"}
            )

    def test_value_accepts_column_reference(self):
        from dbt_charts.core.compile.models.chart.authored import (
            KpiChart as AuthoredKpiChart,
        )

        p = TypeAdapter(AuthoredKpiChart).validate_python(
            {"type": "kpi", "value": "revenue"}
        )
        assert p.value == "revenue"

    def test_value_rejects_numeric_literal(self):
        from dbt_charts.core.compile.models.chart.authored import (
            KpiChart as AuthoredKpiChart,
        )

        with pytest.raises(ValidationError, match="numeric literal"):
            TypeAdapter(AuthoredKpiChart).validate_python({"type": "kpi", "value": 42})

    def test_value_string_still_accepted_as_column_reference(self):
        from dbt_charts.core.compile.models.chart.authored import (
            KpiChart as AuthoredKpiChart,
        )

        # A string like "At risk" is valid at parse time — it could be a
        # column name. Column-not-found is caught at render time.
        p = TypeAdapter(AuthoredKpiChart).validate_python(
            {"type": "kpi", "value": "At risk", "label": "Pipeline"}
        )
        assert p.value == "At risk"


# ---------------------------------------------------------------------------
# Rendering — three-slot order (value / title / support)
# ---------------------------------------------------------------------------


class TestKpiRenderOrder:
    def test_value_renders_above_title(self):
        resolved, data = _resolved_kpi(value="revenue", label="Revenue")
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=180,
            board_style=resolve_style(get_theme_style()),
        )
        root = ET.fromstring(svg)
        ys = [float(node.attrib["y"]) for node in root.findall("svg:text", _NS)]
        assert len(ys) >= 2
        # The value baseline sits above the title baseline (smaller y in SVG).
        assert ys[0] < ys[1]

    def test_title_first_meant_below_value(self):
        resolved, data = _resolved_kpi(value="revenue", label="Revenue this quarter")
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=180,
            board_style=resolve_style(get_theme_style()),
        )
        # The title text element carries the authored title; ensure it is the
        # second text element rather than the first (which is the value).
        root = ET.fromstring(svg)
        texts = root.findall("svg:text", _NS)
        # First text is the value row; second is the title label.
        # Neither emits a <title> child (that would trigger browser native tooltip).
        assert texts[0].find("svg:title", _NS) is None
        # Confirm the second element carries the authored title text (via tspan).
        title_tspans = texts[1].findall("svg:tspan", _NS)
        title_text = "".join(t.text or "" for t in title_tspans)
        assert "Revenue" in title_text, (
            f"Second <text> should carry the title label, got tspan text: {title_text!r}"
        )

    def test_no_default_card_border(self):
        resolved, data = _resolved_kpi(value="revenue", label="Revenue")
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=180,
            board_style=resolve_style(get_theme_style()),
        )
        # Background rect is emitted from cascade, but no border stroke by default.
        assert "stroke=" not in svg


class TestKpiSupportBlock:
    def test_support_renders_glyph_value_and_neutral_explainer(self):
        from dbt_charts.core.compile.models.primitives import FormatConfig

        support = KpiSupportConfig(
            value="delta_pct",
            label="vs LQ",
            glyph="▲",
            tone="positive",
            format=FormatConfig(spec=".1%"),
        )
        resolved, data = _resolved_kpi(
            value="revenue",
            label="Revenue",
            support=support,
            _data=[{"revenue": 1_500_000, "delta_pct": 0.124}],
        )
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
        assert "▲" in svg
        assert "12.4%" in svg
        assert "vs LQ" in svg

    def test_support_value_carries_tone_color(self):
        support = KpiSupportConfig(
            value="delta_pct", label="vs LQ", glyph="▲", tone="positive"
        )
        resolved, data = _resolved_kpi(
            value="revenue",
            label="Revenue",
            support=support,
            _data=[{"revenue": 1_500_000, "delta_pct": 0.124}],
        )
        es = _es()
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
        # Tone "positive" resolves through the theme palette — assert that
        # whatever the theme has set for `positive` is present on the row.
        assert es.tones.positive in svg

    def test_support_explainer_remains_neutral_under_negative_tone(self):
        support = KpiSupportConfig(
            value="delta_pct", label="last month", glyph="▼", tone="negative"
        )
        resolved, data = _resolved_kpi(
            value="revenue",
            label="Revenue",
            support=support,
            _data=[{"revenue": 1_500_000, "delta_pct": -0.05}],
        )
        es = _es()
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
        # Negative tone resolves to whatever the theme has for `negative`.
        assert es.tones.negative in svg
        # Explainer text is neutral (muted secondary text from theme).
        assert "last month" in svg

    def test_support_value_color_rejected(self):
        """value_color is no longer accepted on KpiSupportConfig."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="value_color"):
            KpiSupportConfig.model_validate(
                {
                    "value": -0.003,
                    "label": "vs LQ",
                    "glyph": "▼",
                    "tone": "positive",
                    "value_color": "#0066cc",
                }
            )


# ---------------------------------------------------------------------------
# Tone + glyph + override precedence on the main KPI block
# ---------------------------------------------------------------------------


class TestKpiTonePrecedence:
    def test_style_tone_rejected(self):
        """style.tone is retired — the headline value has no tone field.
        Tone lives on the block it paints: support.tone."""
        from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch

        with pytest.raises(ValidationError, match="tone"):
            KpiChartStylePatch.model_validate({"tone": "warning"})

    def test_main_value_stays_neutral_when_support_carries_tone(self):
        """The headline value's fill never picks up support.tone — value and
        support are disjoint tone targets."""
        resolved, data = _resolved_kpi(
            value="revenue",
            label="Revenue",
            support=KpiSupportConfig(label="vs LQ", tone="warning"),
            _data=[{"revenue": 1_500_000}],
        )
        es = _es()
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
        value_fill = resolved.style.kpi.font.color
        assert value_fill is not None
        # The value tspan's own fill is the neutral chrome color, never the
        # support tone hex.
        import re

        value_tspan = re.search(r"<tspan[^>]*>1\.5[^<]*</tspan>", svg)
        assert value_tspan is not None
        assert es.tones.warning not in value_tspan.group(0)

    def test_value_color_rejected_at_chart_root(self):
        """value_color is no longer accepted at chart root — extra=forbid rejects it."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="value_color"):
            _resolved_kpi(value="revenue", label="Revenue", value_color="#123456")

    def test_glyph_color_rejected_at_chart_root(self):
        """glyph_color is no longer accepted at chart root — extra=forbid rejects it."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="glyph_color"):
            _resolved_kpi(
                value="revenue", label="Revenue", glyph="●", glyph_color="#0066cc"
            )


# ---------------------------------------------------------------------------
# Narrative notation default
# ---------------------------------------------------------------------------


class TestKpiNarrativeDefault:
    def test_default_format_uses_narrative_compact(self):
        resolved, data = _resolved_kpi(value="revenue", label="Revenue")
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=160,
            board_style=resolve_style(get_theme_style()),
        )
        # 1,500,000 → "1.5mn" under narrative notation (.2s = 2 sig figs, d3-correct).
        assert "mn" in svg
        assert "1.5" in svg

    def test_explicit_analytic_notation_overrides_default(self):
        resolved, data = _resolved_kpi(
            value="revenue",
            label="Revenue",
            format={"spec": ".2s", "notation": "analytic"},
        )
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=160,
            board_style=resolve_style(get_theme_style()),
        )
        # Analytic register suffixes the magnitude as uppercase "M".
        assert ">M<" in svg
        assert "mn" not in svg

    def test_explicit_full_format_disables_compact(self):
        resolved, data = _resolved_kpi(value="revenue", label="Revenue", format=",.0f")
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=160,
            board_style=resolve_style(get_theme_style()),
        )
        assert "1,500,000" in svg


class TestKpiDigitIntegrity:
    """Rounding is acceptable under SI compaction, never on plain digits.

    An unformatted KPI value below the first SI threshold (|v| < 1000) must
    render its exact digits — the old ``.2s`` default rounded 131 to "130",
    silently dropping a digit. At/above 1000, compact narrative notation is
    used (2026 -> "2k" — 2 sig figs with trailing-zero trim, narrative
    register keeps lowercase "k").
    """

    def _render(self, value):
        resolved, data = _resolved_kpi(
            value="metric", label="Metric", _data=[{"metric": value}]
        )
        return render_kpi_svg(
            resolved,
            data,
            width=300,
            height=160,
            board_style=resolve_style(get_theme_style()),
        )

    def test_below_threshold_int_renders_exact_digits(self):
        svg = self._render(131)
        assert ">131</tspan>" in svg
        assert ">130<" not in svg
        assert 'dx="2"' not in svg  # no magnitude-suffix tspan emitted

    def test_below_threshold_float_renders_exact_digits(self):
        svg = self._render(131.5)
        assert ">131.50</tspan>" in svg
        assert 'dx="2"' not in svg

    def test_at_threshold_year_keeps_compact_narrative(self):
        # 2026 is >= 1000 (the SI k threshold) — compaction to "2k" stays
        # the deliberate default. A KPI value is a measure, not a dimension:
        # a real count/amount that happens to equal a year-range number
        # (e.g. 2050 units sold) is indistinguishable from an actual year
        # without column-name context, which the value-only heuristic must
        # never use. An author who wants exact-digit years sets an explicit
        # format.
        # Compact path uses ".2~s" (2 sig figs, trim): 2026 → "2.0k" → trim
        # trailing .0 → "2k" (narrative "k").
        svg = self._render(2026)
        assert ">2</tspan>" in svg
        assert ">k</tspan>" in svg
        assert ">2.0</tspan>" not in svg

    def test_compact_value_unchanged(self):
        svg = self._render(1234)
        assert ">1.2</tspan>" in svg
        assert ">k</tspan>" in svg

    def test_larger_compact_value_unchanged(self):
        svg = self._render(12345)
        assert ">12</tspan>" in svg
        assert ">k</tspan>" in svg

    def test_decimal_headline_value_compacts_same_as_int(self):
        # Warehouse NUMERIC/DECIMAL columns surface as decimal.Decimal at
        # render time too — resolve's format decision and render's own
        # numeric check must agree, or a Decimal KPI silently renders exact
        # digits instead of the narrative-compact default an equal int gets.
        from decimal import Decimal

        svg = self._render(Decimal("1500000"))
        assert "mn" in svg
        assert "1.5" in svg

    def test_authored_notation_without_spec_below_threshold_renders_exact_digits(self):
        # `format: {notation: narrative}` (no spec) below threshold hits the
        # authored-FormatConfig branch of finalize_kpi_value_format(), not the
        # format_input is None branch the tests above exercise — that branch
        # also gates `.2s` on compact_eligible, so this must behave
        # identically to the fully-unformatted case.
        resolved, data = _resolved_kpi(
            value="metric",
            label="Metric",
            format={"notation": "narrative"},
            _data=[{"metric": 131}],
        )
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=160,
            board_style=resolve_style(get_theme_style()),
        )
        assert ">131</tspan>" in svg
        assert ">130<" not in svg
        assert 'dx="2"' not in svg


# ---------------------------------------------------------------------------
# Temporal value formatting (dbt-labs/dbt-charts#15)
# ---------------------------------------------------------------------------


class TestKpiTemporalFormat:
    """A date-valued KPI must honor its ``format:`` the same way a table cell
    does — ``date_short`` formats the value, and an unformatted date defaults
    to ``date_short`` too, matching table's default.

    ``style.value.format`` can be a cascade result shared across every KPI on
    the board (a theme/board-level default merged with this chart's own
    patch, indistinguishable from each other once merged), so a numeric spec
    that doesn't fit this chart's temporal value degrades to ``date_short``
    instead of erroring the whole render. ``support.format`` is never
    cascaded — ``ResolvedKpiChart.support`` is the authored ``KpiSupportConfig``
    verbatim — so a mismatch there raises, the same as an equivalent mistake
    would on a table column. A genuinely broken strftime directive raises on
    either slot.
    """

    def _render(self, resolved, data):
        return render_kpi_svg(
            resolved,
            data,
            width=300,
            height=160,
            board_style=resolve_style(get_theme_style()),
        )

    def test_named_date_format_applies_to_date_value(self):
        resolved, data = _resolved_kpi(
            value="release_date",
            label="Next release",
            format="date_short",
            _data=[{"release_date": "2026-11-15"}],
        )
        svg = self._render(resolved, data)
        assert "15 Nov 2026" in svg
        assert "2026-11-15" not in svg

    def test_unformatted_date_value_defaults_to_date_short(self):
        resolved, data = _resolved_kpi(
            value="release_date",
            label="Next release",
            _data=[{"release_date": "2026-11-15"}],
        )
        svg = self._render(resolved, data)
        assert "15 Nov 2026" in svg

    def test_mismatched_value_format_falls_back_to_date_short(self):
        # A chart-local `style.value.format: .2s` on a KPI whose value
        # happens to be a date must not crash the render -- see
        # test_true_board_cascade_format_falls_back_to_date_short below for
        # the genuine board-wide-default version of this same scenario.
        resolved, data = _resolved_kpi(
            value="release_date",
            label="Next release",
            format=".2s",
            _data=[{"release_date": "2026-11-15"}],
        )
        svg = self._render(resolved, data)
        assert "15 Nov 2026" in svg

    def test_true_board_cascade_format_falls_back_to_date_short(self):
        # Unlike the test above (a chart-local `style:` patch, itself an
        # authored-for-this-chart decision), this sets the mismatched format
        # on the shared ChartStyleContext -- the actual shape a board/theme
        # level `style.charts.kpi.value.format` cascade default takes before
        # any individual chart's own style is merged in. The KPI here
        # authors no format of its own.
        import dataclasses

        board_style = dataclasses.replace(
            _BOARD_STYLE,
            kpi=_BOARD_STYLE.kpi.model_copy(
                update={
                    "value": _BOARD_STYLE.kpi.value.model_copy(update={"format": ".2s"})
                }
            ),
        )
        chart = KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            value="release_date",
            label="Next release",
        )
        data = [{"release_date": "2026-11-15"}]
        resolved = resolve(chart, data, chart_style_context=board_style)
        svg = self._render(resolved, data)
        assert "15 Nov 2026" in svg

    def test_strftime_alias_applies_to_date_value(self):
        # An inline `%`-spec directly under `format:` is compile-rejected on
        # this slot (`_TIME_CAPABLE_FIELDS` doesn't cover KPI value/support);
        # a `style.formats` alias (validated with `time_format=True`) is the
        # only author-reachable route to a strftime spec here, and resolves
        # through the same `resolve_format` call `_format_value_parts` makes.
        from dbt_charts.core.render.chart.kpi import _format_value_parts

        _, number_str, _, is_numeric = _format_value_parts(
            "2026-11-15", "my_date", "t", formats={"my_date": "%Y/%m"}
        )
        assert number_str == "2026/11"
        assert is_numeric is False

    def test_invalid_strftime_alias_raises(self):
        from dbt_charts.core.render.chart.kpi import _format_value_parts

        with pytest.raises(ChartDataError, match="unknown directive"):
            _format_value_parts(
                "2026-11-15", "bad_date", "t", formats={"bad_date": "%Q"}
            )

    def test_d3_fill_char_percent_spec_falls_back_to_date_short(self):
        # `%` is also a legal d3-format FILL character (e.g. `%>10,.0f`) and
        # the bare percent TYPE (`%`) -- neither is a strftime directive, so
        # a bare `resolved.startswith("%")` check would misroute both: the
        # first crashes on "unknown directive", the second would render the
        # literal string "%" as the KPI's value. `is_time_format` is the
        # correct discriminator and must route both to the date_short default.
        resolved, data = _resolved_kpi(
            value="release_date",
            label="Next release",
            format="%>10,.0f",
            _data=[{"release_date": "2026-11-15"}],
        )
        svg = self._render(resolved, data)
        assert "15 Nov 2026" in svg

    def test_bare_percent_spec_falls_back_to_date_short(self):
        resolved, data = _resolved_kpi(
            value="release_date",
            label="Next release",
            format="%",
            _data=[{"release_date": "2026-11-15"}],
        )
        svg = self._render(resolved, data)
        assert "15 Nov 2026" in svg
        assert ">%<" not in svg

    def test_time_only_spec_reflects_actual_time(self):
        resolved, data = _resolved_kpi(
            value="updated_at",
            label="Last updated",
            format="time_short",
            _data=[{"updated_at": "2026-11-15T14:30:00"}],
        )
        svg = self._render(resolved, data)
        # "14:30" alone is a substring of the raw ISO value too -- assert the
        # value renders as exactly "14:30" and the raw timestamp is gone, so
        # this fails on the pre-fix (unformatted-raw-string) path.
        assert ">14:30<" in svg
        assert "2026-11-15" not in svg

    def test_support_date_value_honors_named_format(self):
        support = KpiSupportConfig(
            value="renewed_on",
            label="renewed",
            format="date_short",
        )
        resolved, data = _resolved_kpi(
            value="revenue",
            label="Revenue",
            support=support,
            _data=[{"revenue": 1_500_000, "renewed_on": "2026-11-15"}],
        )
        svg = self._render(resolved, data)
        assert "15 Nov 2026" in svg

    def test_support_mismatched_format_raises(self):
        # support.format is never a cascade result (ResolvedKpiChart.support
        # is the authored KpiSupportConfig verbatim), so an explicit
        # mismatched format there is unambiguously this chart's own
        # authoring mistake -- it raises, unlike the value slot's fallback.
        support = KpiSupportConfig(
            value="renewed_on",
            label="renewed",
            format="percent",
        )
        resolved, data = _resolved_kpi(
            value="revenue",
            label="Revenue",
            support=support,
            _data=[{"revenue": 1_500_000, "renewed_on": "2026-11-15"}],
        )
        with pytest.raises(ChartDataError, match="is not a date format"):
            self._render(resolved, data)

    def test_non_temporal_non_numeric_value_still_renders_unformatted(self):
        # Existing rule, unchanged: a status-style string value (not a date,
        # not a number) renders verbatim regardless of format:.
        resolved, data = _resolved_kpi(
            value="status",
            label="Pipeline",
            _data=[{"status": "At risk"}],
        )
        svg = self._render(resolved, data)
        assert "At risk" in svg


# ---------------------------------------------------------------------------
# Multi-KPI fixed-slot alignment
# ---------------------------------------------------------------------------


class TestKpiFixedSlotAlignment:
    def test_value_baseline_constant_across_title_lengths(self):
        """A KPI with a long wrapping title keeps the same value baseline as
        a KPI with a short single-line title — fixed-slot layout reserves
        space for two title lines regardless of content."""
        short, short_data = _resolved_kpi(value="revenue", label="Revenue")
        long, long_data = _resolved_kpi(
            value="revenue",
            label="New customers acquired this quarter from inbound channels",
        )
        svg_short = render_kpi_svg(
            short,
            short_data,
            width=180,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
        svg_long = render_kpi_svg(
            long,
            long_data,
            width=180,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
        ys_short = [
            float(n.attrib["y"])
            for n in ET.fromstring(svg_short).findall("svg:text", _NS)
        ]
        ys_long = [
            float(n.attrib["y"])
            for n in ET.fromstring(svg_long).findall("svg:text", _NS)
        ]
        # First text element is the value — its y must match across both.
        assert ys_short[0] == ys_long[0]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestKpiValidation:
    def test_missing_value_rejected_at_resolved_boundary(self):
        chart = KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            value="revenue",
        )
        # value is required through the resolved boundary (ResolvedKpiChart.value
        # is a required str). Forcing it to None — bypassing authoring validation
        # via model_copy — is rejected when resolve builds the resolved chart.
        chart = chart.model_copy(update={"value": None})
        data = [{"revenue": 1}]
        with pytest.raises(ValidationError, match="value"):
            resolve(chart, data, chart_style_context=_BOARD_STYLE)

    def test_string_value_not_in_row_raises_chart_data_error(self):
        """A string ``value`` that doesn't match any column in the query result
        raises ``ChartDataError`` with a migration hint."""
        chart = KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            value="Critical",
            label="Pipeline",
        )
        data = [{"revenue": 1}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        with pytest.raises(ChartDataError, match="'Critical' not found"):
            render_kpi_svg(
                resolved,
                data,
                width=300,
                height=140,
                board_style=resolve_style(get_theme_style()),
            )


# ---------------------------------------------------------------------------
# Round-trip + dispatch regression tests (review feedback)
# ---------------------------------------------------------------------------


class TestKpiNewFieldsRoundTripThroughSerializers:
    """KPI style fields (glyph.character) live under style.kpi.* not chart
    root; tone lives only on support.tone. Verify JSON serialization routes
    them correctly, and that yaml_format no longer lists them as standalone
    chart root fields."""

    def _kpi(self):
        from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch

        # glyph moved to style.* — use KpiChartStylePatch directly
        return KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            label="Revenue",
            value="revenue",
            style=KpiChartStylePatch.model_validate({"glyph": {"character": "●"}}),
            support=KpiSupportConfig(
                value="delta_pct",
                label="vs LQ",
                glyph="▲",
                tone="positive",
            ),
        )

    def test_json_emit_preserves_all_kpi_fields(self):
        from dbt_charts.core.render.chart.serialization import build_dbt_charts_json

        chart = self._kpi()
        data = [{"revenue": 1_500_000, "delta_pct": 0.124}]
        out = build_dbt_charts_json(chart, data)
        assert out["value"] == "revenue"
        # glyph and tone are now in style.kpi.*, not at chart root
        assert "glyph" not in out
        assert "tone" not in out
        # value_color and glyph_color were removed — must NOT be emitted.
        assert "value_color" not in out
        assert "glyph_color" not in out
        # Support is a Pydantic model — must serialize to a plain dict.
        assert out["support"]["value"] == "delta_pct"
        assert out["support"]["label"] == "vs LQ"
        assert out["support"]["glyph"] == "▲"
        assert out["support"]["tone"] == "positive"

    def test_yaml_emit_preserves_all_kpi_fields(self):
        from dbt_charts.core.render.board_to_dict import CHART_FIELDS

        # glyph/tone moved to style.kpi.* — no longer in top-level CHART_FIELDS.
        assert "glyph" not in CHART_FIELDS, "glyph should have moved to style.kpi"
        assert "tone" not in CHART_FIELDS, "tone should have moved to style.kpi"
        # Remaining KPI-specific root fields stay.
        for field in ("value", "label", "support"):
            assert field in CHART_FIELDS, f"CHART_FIELDS missing {field!r}"
        # value_color and glyph_color are removed from the authoring surface.
        assert "value_color" not in CHART_FIELDS
        assert "glyph_color" not in CHART_FIELDS


class TestGenerateVegaLiteSpecRejectsKpi:
    """KPI is custom-SVG, not Vega-Lite. ``generate_vega_lite_spec`` must
    raise instead of producing a half-formed spec when called on a KPI."""

    def test_kpi_raises_clear_error(self):
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        chart = KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            value="revenue",
        )
        _rc = resolve(chart, [{"revenue": 1_500_000}], chart_style_context=_BOARD_STYLE)
        with pytest.raises(RenderError, match="does not render to a Vega-Lite spec"):
            generate_vega_lite_spec(chart, [{"revenue": 1_500_000}])


class TestKpiValueFontSizeCascade:
    """Regression: slot font sizes must round-trip from theme → rendered SVG."""

    def test_theme_value_font_size_reaches_rendered_svg(self):
        from dbt_charts.core.compile.resolve.style.board import resolve_style

        es = resolve_style(get_theme_style()).chart_defaults
        # Override value.font.size to a distinctive number and assert it appears
        # in the rendered SVG. Use model_copy to preserve other font fields
        # (family, weight) — replacing the whole FontStyle would lose them.
        kpi = es.kpi
        kpi_override = kpi.model_copy(
            update={
                "value": kpi.value.model_copy(
                    update={"font": kpi.value.font.model_copy(update={"size": 33.0})}
                )
            }
        )
        chart = KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            value="revenue",
            label="Revenue",
        )
        data = [{"revenue": 42}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        resolved = resolved.model_copy(
            update={"style": resolved.style.model_copy(update={"kpi": kpi_override})}
        )
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
        assert 'font-size="33.0"' in svg, (
            "Theme `kpi.value.font.size` did not reach the rendered SVG."
        )

    def test_theme_glyph_font_size_reaches_rendered_svg(self):
        """``kpi.glyph.font.size`` round-trips theme → SVG.
        Glyph character must be set via kpi.glyph.character (moved from chart root)."""
        from dbt_charts.core.compile.resolve.style.board import resolve_style

        es = resolve_style(get_theme_style()).chart_defaults
        kpi = es.kpi
        kpi_override = kpi.model_copy(
            update={
                "glyph": kpi.glyph.model_copy(
                    update={
                        "character": "▲",
                        "font": kpi.glyph.font.model_copy(update={"size": 17.0}),
                    }
                )
            }
        )
        chart = KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            value="revenue",
            label="Revenue",
        )
        data = [{"revenue": 42}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        resolved = resolved.model_copy(
            update={"style": resolved.style.model_copy(update={"kpi": kpi_override})}
        )
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
        assert 'font-size="17.0"' in svg, (
            "Theme `kpi.glyph.font.size` did not reach the rendered SVG."
        )


# ---------------------------------------------------------------------------
# Geometry refinements pass
#
# These tests cover renderer behaviors introduced by the geometry/typography
# pass: body/value font-family split, empty-label slot reservation, per-affix
# `y=` attribute, and SpacingValues content_padding driving the value-text
# x-coordinate.  Each test is a regression guard for a specific code seam.
# ---------------------------------------------------------------------------


class TestKpiGeometryRefinements:
    """Renderer-behavior guards for the geometry refinements pass."""

    @staticmethod
    def _override_kpi(es, **kpi_updates):
        return es.kpi.model_copy(update=kpi_updates)

    def test_editorial_kpi_keeps_sans_support_row(self):
        """An editorial KPI overrides ``kpi.value.font.family`` to a serif
        for the headline number.  The support row must remain on the body
        sans family — the renderer resolves body/value families separately
        so the value override does not leak into support."""
        es = _es()
        # Override only the value family — body family stays at theme default.
        value_font_override = es.kpi.value.font.model_copy(
            update={"family": "Source Serif 4"}
        )
        value_override = es.kpi.value.model_copy(update={"font": value_font_override})
        effective = self._override_kpi(es, value=value_override)

        support = KpiSupportConfig(
            value="delta_pct", label="vs LQ", glyph="▲", tone="positive"
        )
        chart = KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            value="revenue",
            label="Revenue",
            support=support,
        )
        data = [{"revenue": 1_500_000, "delta_pct": 0.124}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        resolved = resolved.model_copy(
            update={"style": resolved.style.model_copy(update={"kpi": effective})}
        )
        svg = render_kpi_svg(
            resolved,
            data,
            width=320,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )

        root = ET.fromstring(svg)
        texts = root.findall("svg:text", _NS)
        # Layout: [value, label, support] in document order.
        assert len(texts) >= 3, "Expected value + label + support text elements"
        support_text = texts[-1]
        family = support_text.attrib.get("font-family", "")
        assert "Source Serif 4" not in family, (
            f"Support row font-family contains 'Source Serif 4' — body/value "
            f"family resolution leaked the value override into support. "
            f"font-family={family!r}"
        )
        # Sanity: the value <text> *did* receive the serif override.
        value_family = texts[0].attrib.get("font-family", "")
        assert "Source Serif 4" in value_family, (
            f"Value <text> font-family did not pick up the serif override; "
            f"got {value_family!r} — test setup may be wrong."
        )

    def test_empty_label_renders_no_text_but_height_consistent(self):
        """``label: ""`` on a KPI must skip the label ``<text>`` element
        while keeping the card height equal to the labeled version — the
        slot is reserved so multi-up rows of mixed-label KPIs stay aligned.
        """
        labeled, lab_data = _resolved_kpi(value="revenue", label="Quarterly revenue")
        empty, emp_data = _resolved_kpi(value="revenue", label="")
        svg_labeled = render_kpi_svg(
            labeled,
            lab_data,
            width=300,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
        svg_empty = render_kpi_svg(
            empty,
            emp_data,
            width=300,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )

        root_l = ET.fromstring(svg_labeled)
        root_e = ET.fromstring(svg_empty)

        # Heights match — slot reservation independent of label content.
        assert root_l.attrib["height"] == root_e.attrib["height"], (
            f"Empty-label SVG height {root_e.attrib['height']!r} does not "
            f"match labeled SVG height {root_l.attrib['height']!r} — "
            f"empty-label slot reservation regressed."
        )

        # The empty-label SVG has one fewer <text> element (no label text).
        n_text_labeled = len(root_l.findall("svg:text", _NS))
        n_text_empty = len(root_e.findall("svg:text", _NS))
        assert n_text_empty == n_text_labeled - 1, (
            f"Expected empty-label SVG to omit the label <text>, "
            f"got {n_text_empty} text elements vs {n_text_labeled} labeled."
        )

        # And no <text> sits at the label baseline in the empty SVG.
        labeled_label = root_l.findall("svg:text", _NS)[1]
        label_y = labeled_label.attrib["y"]
        empty_ys = {t.attrib.get("y") for t in root_e.findall("svg:text", _NS)}
        assert label_y not in empty_ys, (
            f"Empty-label SVG still has a <text> at the label baseline "
            f"y={label_y!r} — the empty-label guard was bypassed."
        )

    def test_affix_tspans_carry_explicit_y_attribute(self):
        """Each tspan in the value row (glyph, prefix, value, suffix) must
        carry its own ``y=`` attribute.  Cumulative ``dy`` re-introduces the
        positioning bug where each affix's vertical offset compounded with
        the previous tspan's, breaking optical alignment."""
        from dbt_charts.core.compile.models.primitives import FormatConfig
        from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch

        # glyph moved to style.glyph.character; format to style.value.format
        chart = KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            value="revenue",
            label="Revenue",
            style=KpiChartStylePatch.model_validate(
                {
                    "glyph": {"character": "▲"},
                    "value": {"format": FormatConfig(spec="$,.2s")},
                }
            ),
        )
        data = [{"revenue": 1_500_000}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_kpi_svg(
            resolved,
            data,
            width=320,
            height=180,
            board_style=resolve_style(get_theme_style()),
        )

        root = ET.fromstring(svg)
        value_text = root.findall("svg:text", _NS)[0]
        tspans = value_text.findall("svg:tspan", _NS)
        # Expect at least glyph + prefix + value (3); a suffix would make 4.
        assert len(tspans) >= 3, (
            f"Expected glyph + prefix + value tspans, got {len(tspans)}"
        )
        for i, t in enumerate(tspans):
            assert "y" in t.attrib, (
                f"Value-row tspan #{i} ({t.text!r}) is missing an explicit y= "
                f"attribute — affix positioning regressed to cumulative dy."
            )

    def test_content_padding_left_drives_value_text_x(self):
        """The ``SpacingValues`` schema seam must actually drive layout —
        ``content_padding.left`` lands at the value ``<text>`` x coordinate.
        A fixed scalar fallback would silently ignore the override."""
        from dbt_charts.core.compile.models.primitives import (
            SpacingValues,
        )  # noqa: PLC0415

        es = _es()
        effective = self._override_kpi(
            es,
            content_padding=SpacingValues(top=20, right=12, bottom=20, left=12),
        )
        chart = KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            value="revenue",
            label="Revenue",
        )
        data = [{"revenue": 1_500_000}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        resolved = resolved.model_copy(
            update={"style": resolved.style.model_copy(update={"kpi": effective})}
        )
        svg = render_kpi_svg(
            resolved,
            data,
            width=300,
            height=180,
            board_style=resolve_style(get_theme_style()),
        )

        root = ET.fromstring(svg)
        value_text = root.findall("svg:text", _NS)[0]
        x = float(value_text.attrib["x"])
        assert x == 12.0, (
            f"Value <text> x={x!r}; expected 12.0 from "
            f"content_padding.left=12 — the SpacingValues field was not "
            f"propagated through to the render contract."
        )


def test_kpi_label_color_no_longer_inherits_style_title_font_color():
    """Pin the deliberate behavior change: ``style.kpi.title.font.color`` no
    longer overrides the KPI label color.

    Pre-cascade-rename, the KPI renderer read ``chart.style.title.font.color``
    directly off the authored Patch and used it as the label color, falling
    back to body color when None. After the cascade rename the resolved
    ``title.font.color`` is always populated by the theme default — keeping
    the legacy coupling would silently flip from body to title color across
    the whole label corpus on any theme.

    The typed slot an author wants is ``style.label.font.color`` (not the
    title slot); it reads on the label now. What stays pinned here is that the
    *title* slot is not that slot, so a future refactor cannot re-introduce
    the silent coupling.
    """
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.compile.models.style.authored import (
        KpiChartStylePatch,
        TitleStylePatch,
    )

    chart = KpiChart(
        id="t",
        query=_DUMMY_QUERY,
        query_name="q",
        type="kpi",
        value="revenue",
        label="Revenue",
        style=KpiChartStylePatch.model_validate(
            {
                "title": TitleStylePatch.model_validate(
                    {"font": FontStyle(color="#ff00ff")}
                )
            }
        ),
    )
    data = [{"revenue": 1_500_000}]
    resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    svg = render_kpi_svg(
        resolved,
        data,
        width=300,
        height=180,
        board_style=resolve_style(get_theme_style()),
    )
    # The authored title color must NOT show up on the label — body color owns it.
    assert "#ff00ff" not in svg, (
        "KPI label color leaked from style.kpi.title.font.color — this coupling "
        "was deliberately removed during the cascade rename. A label-specific "
        "color is authored on style.label.font.color."
    )


# ---------------------------------------------------------------------------
# Label slot collapses to 1 line under single-line overflow modes
# ---------------------------------------------------------------------------


def _charts_style_with_overflow(overflow):
    """Return a ChartsStyle copy with title.overflow set to ``overflow``."""
    import dataclasses

    base = _es()
    new_title = base.title.model_copy(update={"overflow": overflow})
    return dataclasses.replace(base, title=new_title)


class TestKpiLabelSlotByOverflow:
    """``support_baseline`` reserves one line under single-line overflow modes
    (``clip``/``truncate``) instead of the default two-line ``wrap-two`` slot.

    Asserts the slot delta in terms of the layout's own ``label_line_height``
    so the test does not pin a theme literal.
    """

    def _layout(self, overflow, label="Revenue", width=180, height=0):
        from dbt_charts.core.render.chart.kpi import _resolve_kpi_layout

        charts_style = _charts_style_with_overflow(overflow)
        return _resolve_kpi_layout(
            label_text=label,
            requested_width=width,
            requested_height=height,
            kpi_config=charts_style.kpi,
            title_style=charts_style.title,
            has_support=True,
        )

    def test_wrap_two_preserves_two_line_slot(self):
        layout = self._layout("wrap-two")
        delta = layout.support_baseline - layout.label_baseline_first
        assert delta == pytest.approx(2 * layout.label_line_height)

    def test_truncate_collapses_to_one_line_slot(self):
        layout = self._layout("truncate")
        delta = layout.support_baseline - layout.label_baseline_first
        assert delta == pytest.approx(1 * layout.label_line_height)

    def test_clip_collapses_to_one_line_slot(self):
        layout = self._layout("clip")
        delta = layout.support_baseline - layout.label_baseline_first
        assert delta == pytest.approx(1 * layout.label_line_height)

    def test_row_alignment_under_truncate_with_mixed_label_lengths(self):
        """Two KPIs at the same width under ``truncate``: short and would-wrap
        labels share the same support baseline (theme-wide overflow shifts
        every KPI by the same amount)."""
        short = self._layout("truncate", label="Revenue")
        wrappy = self._layout(
            "truncate",
            label="New customers acquired this quarter from inbound channels",
        )
        assert short.support_baseline == wrappy.support_baseline

    def test_minimum_card_height_drops_under_truncate(self):
        """``minimum_card_h`` under ``truncate`` is smaller than under
        ``wrap-two`` by approximately one ``label_line_height``. We read the
        layout's ``height`` with ``requested_height=0`` so it returns the
        structural minimum."""
        wrap = self._layout("wrap-two", height=0)
        trunc = self._layout("truncate", height=0)
        assert wrap.height - trunc.height == pytest.approx(wrap.label_line_height)


# ---------------------------------------------------------------------------
# Support slot collapses when the card has no support content
# ---------------------------------------------------------------------------


class TestKpiSupportSlotCollapse:
    """Cards with no support content drop the reserved third-line slot.

    The legacy layout always reserved one ``label_line_height`` + support
    descender below the label slot for a delta/comparison line, leaving a
    visible gap below rows of bare label+value KPIs. The collapse keeps the
    reservation only when a support row will actually be emitted.
    """

    def _layout(
        self,
        has_support: bool,
        label: str = "Revenue",
        width: float = 180,
        height: float = 0,
    ):
        from dbt_charts.core.render.chart.kpi import _resolve_kpi_layout

        charts_style = _es()
        return _resolve_kpi_layout(
            label_text=label,
            requested_width=width,
            requested_height=height,
            kpi_config=charts_style.kpi,
            title_style=charts_style.title,
            has_support=has_support,
        )

    def test_no_support_card_collapses_one_line(self):
        """Card height without support drops by ~one ``label_line_height``
        compared to with-support — the reserved third line is the support
        row's slot. Tolerance accommodates the descender swap (support font
        vs label font may differ by a few pixels)."""
        without = self._layout(has_support=False)
        with_support = self._layout(has_support=True)
        delta = with_support.height - without.height
        assert delta == pytest.approx(without.label_line_height, abs=4.0)

    def test_homogeneous_no_support_row_uniform_height(self):
        """Two no-support cards at the same width have the same height
        regardless of label content — the label slot stays reserved at two
        lines; only the support slot collapses."""
        short = self._layout(has_support=False, label="Revenue")
        long_ = self._layout(
            has_support=False,
            label="New customers acquired this quarter from inbound channels",
        )
        assert short.height == long_.height


# ---------------------------------------------------------------------------
# link — click-through link on KPI value
# ---------------------------------------------------------------------------


class TestKpiHref:
    def _board(self):
        return resolve_style(get_theme_style())

    def test_link_wraps_value_in_anchor(self):
        """KPI with link: wraps the value text in <a href="...">."""
        chart, data = _resolved_kpi(value="revenue", link="/detail?id=42")
        svg = render_kpi_svg(chart, data, board_style=self._board())
        assert '<a href="/detail?id=42">' in svg
        assert "</a>" in svg

    def test_link_escapes_special_chars(self):
        """link attribute is html.escape(..., quote=True) — ampersands and quotes escaped."""
        chart, data = _resolved_kpi(value="revenue", link='/path?a=1&b="x"')
        svg = render_kpi_svg(chart, data, board_style=self._board())
        assert '<a href="/path?a=1&amp;b=&quot;x&quot;">' in svg

    def test_no_link_produces_no_anchor(self):
        """KPI without link: renders no <a> element."""
        chart, data = _resolved_kpi(value="revenue")
        svg = render_kpi_svg(chart, data, board_style=self._board())
        assert "<a " not in svg

    @pytest.mark.skip(
        reason="render_single deleted; Jinja link variables not threaded through V2 render_chart"
    )
    def test_link_jinja_variables_resolved_via_render_pipeline(self):
        """link: Jinja template is resolved against variables before KPI render.

        Tests the render_single.py path: link {{ region }} → "us" when
        variables={"region": "us"} is threaded through render_chart_to_svg.
        """
        from unittest.mock import MagicMock

        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.chart.render_single import (
            render_unresolved_chart_to_svg as render_chart_to_svg,
        )

        chart = KpiChart(
            id="rev",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            value="revenue",
            link="/region/{{ region }}",
        )
        mock_executor = MagicMock(spec=Executor)
        mock_executor.execute_chart.return_value = [{"revenue": 42}]
        svg, _, _ = render_chart_to_svg(
            chart,
            mock_executor,
            variables={"region": "us"},
            resolved_style=self._board(),
            vega_config={},
            width=300,
        )
        assert '<a href="/region/us">' in svg


# ---------------------------------------------------------------------------
# Variant rendering — stacked (default) / inline / compact
# ---------------------------------------------------------------------------


def _render_kpi(**resolved_kwargs):
    """Build a resolved KPI chart and render to SVG with default board styling."""
    resolved, data = _resolved_kpi(**resolved_kwargs)
    return render_kpi_svg(
        resolved,
        data,
        width=320,
        height=120,
        board_style=resolve_style(get_theme_style()),
    )


def _support_full():
    from dbt_charts.core.compile.models.primitives import FormatConfig

    return KpiSupportConfig(
        value="delta_pct",
        label="vs LQ",
        glyph="▲",
        tone="positive",
        format=FormatConfig(spec=".1%"),
    )


_KPI_DATA = [{"revenue": 1_500_000, "delta_pct": 0.124}]


class TestKpiVariantInline:
    """variant: inline — single row, value + label + support baseline-aligned."""

    def test_renders_as_a_single_text_element(self):
        # The inline emit packs value/label/support into one <text> with
        # sequential tspans (so SVG flows them left-to-right via dx). One
        # <text>, multiple <tspan>s — distinct from stacked which emits three.
        svg = _render_kpi(
            variant="inline",
            value="revenue",
            label="Revenue",
            support=_support_full(),
            _data=_KPI_DATA,
        )
        root = ET.fromstring(svg)
        texts = root.findall("svg:text", _NS)
        assert len(texts) == 1, (
            f"inline should emit exactly one <text>, got {len(texts)}"
        )

    def test_label_and_support_share_value_baseline(self):
        # The whole row is baseline-aligned; the parent <text> y is the value
        # baseline and label / support tspans inherit it (no explicit y).
        svg = _render_kpi(
            variant="inline",
            value="revenue",
            label="Revenue",
            support=_support_full(),
            _data=_KPI_DATA,
        )
        root = ET.fromstring(svg)
        text = root.find("svg:text", _NS)
        assert text is not None
        value_baseline = float(text.attrib["y"])
        # Label tspan and support tspans should NOT carry an explicit y;
        # they inherit the parent text's y (the value baseline).
        # Value-affixes (glyph / prefix / suffix) DO carry per-tspan y for
        # cap-height offsets — that's their separate visual contract.
        tspans = text.findall("svg:tspan", _NS)
        # At least: glyph (if any), prefix, number, suffix, label, support glyph,
        # support value, support explainer.
        assert len(tspans) >= 4, "expected multiple tspans in inline row"
        # The number tspan carries y = value_baseline explicitly.
        number_tspans = [
            t for t in tspans if t.text and "1.5" in t.text
        ]  # currency renders 1.5M
        assert number_tspans, "value tspan missing"
        assert float(number_tspans[0].attrib["y"]) == value_baseline

    def test_value_only_renders_just_the_number(self):
        # All variants degenerate to a bare value when label and support are
        # both unset.
        svg = _render_kpi(variant="inline", value="revenue", _data=_KPI_DATA)
        root = ET.fromstring(svg)
        texts = root.findall("svg:text", _NS)
        assert len(texts) == 1
        text_content = "".join(t.text or "" for t in texts[0].findall("svg:tspan", _NS))
        assert "1.5" in text_content
        assert "Revenue" not in text_content
        assert "▲" not in text_content

    def test_does_not_wrap_label(self):
        # Inline never wraps; a long label that still fits the row stays on
        # it. Stacked's label slot wrap helper (`_wrap_label_lines`) emits
        # one tspan per wrapped line with its own `x=`. Inline must not.
        # The label is long but chosen to fit the 320px card at this size —
        # a label long enough to overflow now falls back to stacked (which
        # does wrap), a distinct, separately-tested contract.
        svg = _render_kpi(
            variant="inline",
            value="revenue",
            label="Net new monthly recurring revenue, enterprise",
            _data=_KPI_DATA,
        )
        root = ET.fromstring(svg)
        texts = root.findall("svg:text", _NS)
        # Confirms the fixture actually stayed inline (one <text>) rather
        # than silently taking the overflow fallback (two-plus <text>s) —
        # the wrap-tspan check below is meaningless against a fallen-back
        # stacked render.
        assert len(texts) == 1, (
            f"expected the fixture label to fit inline, got {len(texts)} <text> "
            "elements — the label may have grown past the overflow fallback "
            "threshold; shorten it back to a fitting length"
        )
        text = texts[0]
        # No tspan carries an `x=` attribute (that's the wrap marker).
        wrap_tspans = [t for t in text.findall("svg:tspan", _NS) if "x" in t.attrib]
        assert not wrap_tspans, (
            f"inline must not wrap; found tspans with x=: {[t.attrib for t in wrap_tspans]}"
        )

    def test_label_carries_resolved_font_weight(self):
        # Companion to compact's weight regression test. Inline previously
        # hardcoded `font-weight="400"` on the label tspan, which silently
        # overrode the theme cascade (default kpi.font.weight=500). The label
        # tspan must emit the cascade-resolved weight so themes that set
        # kpi.label.font.weight reach the rendered SVG.
        svg = _render_kpi(
            variant="inline",
            value="revenue",
            label="Revenue",
            _data=_KPI_DATA,
        )
        root = ET.fromstring(svg)
        text = root.find("svg:text", _NS)
        assert text is not None
        label_tspan = next(
            (
                t
                for t in text.findall("svg:tspan", _NS)
                if (t.text or "").strip() == "Revenue"
            ),
            None,
        )
        assert label_tspan is not None, "label tspan not found"
        weight = label_tspan.attrib.get("font-weight", "")
        # Pin structure (the attribute is present and non-default) rather than
        # a specific theme value — the theme can move 500→400 legitimately.
        assert weight, "inline label must declare an explicit font-weight"
        assert weight != "400", (
            f"inline label dropped through to SVG default; got {weight!r}"
        )

    def test_tone_color_only_on_support_value(self):
        # Vercel's rule: color is meaningful only. Tone color stays on the
        # support glyph + support value; the metric label and the support
        # explainer stay muted.
        svg = _render_kpi(
            variant="inline",
            value="revenue",
            label="Revenue",
            support=_support_full(),
            _data=_KPI_DATA,
        )
        # The positive tone color from the default theme is non-default;
        # whatever it is, it should appear at most as many times as there are
        # tone-bearing tspans (glyph + value = 2). The label and explainer
        # stay on the muted color, which is distinct from the tone color.
        # Pin structure rather than the specific hex.
        root = ET.fromstring(svg)
        text = root.find("svg:text", _NS)
        assert text is not None
        tone_fills = []
        for t in text.findall("svg:tspan", _NS):
            fill = t.attrib.get("fill", "")
            if fill and (t.text or "").strip() in {"▲", "12.4%"}:
                tone_fills.append(fill)
        # Glyph and support value both carry the same tone color.
        assert len(set(tone_fills)) == 1, (
            f"glyph and support value should share the tone color, got fills: {tone_fills}"
        )


class TestKpiVariantCompact:
    """variant: compact — 2-col, bottom right baseline-aligned to value."""

    def test_canonical_emits_three_text_elements(self):
        # Value text on left + top-right (label) + bottom-right (support).
        svg = _render_kpi(
            variant="compact",
            value="revenue",
            label="Revenue",
            support=_support_full(),
            _data=_KPI_DATA,
        )
        root = ET.fromstring(svg)
        texts = root.findall("svg:text", _NS)
        assert len(texts) == 3, (
            f"compact canonical should emit 3 text elements, got {len(texts)}"
        )

    def test_canonical_bottom_right_shares_value_baseline(self):
        # The defining trick of compact: the bottom right line's y equals the
        # value's y (shared baseline), and the top right line sits above it.
        svg = _render_kpi(
            variant="compact",
            value="revenue",
            label="Revenue",
            support=_support_full(),
            _data=_KPI_DATA,
        )
        root = ET.fromstring(svg)
        texts = root.findall("svg:text", _NS)
        ys = sorted(float(t.attrib["y"]) for t in texts)
        # ys[0] is the top-right line; ys[1] and ys[2] should be equal
        # (value text on left and support text on right, both at value baseline).
        assert ys[1] == ys[2], (
            f"value text and bottom-right support text should share baseline; ys={ys}"
        )
        assert ys[0] < ys[1], "top-right (label) should sit above the value baseline"

    def test_only_label_collapses_to_baseline(self):
        # No support → the label moves to the baseline; the top right line is
        # not emitted (no third <text>).
        svg = _render_kpi(
            variant="compact",
            value="revenue",
            label="Revenue",
            _data=_KPI_DATA,
        )
        root = ET.fromstring(svg)
        texts = root.findall("svg:text", _NS)
        assert len(texts) == 2, (
            f"compact + only label should emit 2 text elements "
            f"(value + label-on-baseline), got {len(texts)}"
        )
        # The label text should share y with the value text.
        ys = [float(t.attrib["y"]) for t in texts]
        assert ys[0] == ys[1], f"label should sit on the value baseline; ys={ys}"

    def test_only_support_splits_glyph_value_on_top_label_on_baseline(self):
        # Hero rule for compact: when support is alone AND has both a value
        # and a label sub-part, we split it across the two right-column lines
        # — glyph+value above, explainer on baseline. This is the
        # "really make it compact" case.
        svg = _render_kpi(
            variant="compact",
            value="revenue",
            support=_support_full(),  # has value + label
            _data=_KPI_DATA,
        )
        root = ET.fromstring(svg)
        texts = root.findall("svg:text", _NS)
        assert len(texts) == 3, (
            f"compact + only support (split) should emit 3 text elements, "
            f"got {len(texts)}"
        )
        # Two distinct y values: the top-right line and the shared value
        # baseline (used by both the value text and the bottom-right line).
        text_rows = [
            (
                float(t.attrib["y"]),
                "".join(tspan.text or "" for tspan in t.findall("svg:tspan", _NS)),
            )
            for t in texts
        ]
        text_rows.sort(key=lambda pair: pair[0])
        ys_distinct = sorted({y for y, _ in text_rows})
        assert len(ys_distinct) == 2, (
            f"compact split should have one top y and one baseline y, got: {ys_distinct}"
        )
        top_y, baseline_y = ys_distinct
        top_content = next(content for y, content in text_rows if y == top_y)
        # The bottom-right text on the baseline (not the value text). Pick by
        # content — it's the one with the support explainer.
        baseline_contents = [content for y, content in text_rows if y == baseline_y]
        assert "▲" in top_content and "12.4%" in top_content, (
            f"top right should carry glyph + value, got: {top_content!r}"
        )
        assert any("vs LQ" in c for c in baseline_contents), (
            f"baseline row should carry support label, got: {baseline_contents!r}"
        )

    def test_only_support_with_single_sub_part_stays_atomic_on_baseline(self):
        # No split when support has only one sub-part (just a label here).
        support = KpiSupportConfig(label="vs LQ")
        svg = _render_kpi(
            variant="compact",
            value="revenue",
            support=support,
            _data=_KPI_DATA,
        )
        root = ET.fromstring(svg)
        texts = root.findall("svg:text", _NS)
        assert len(texts) == 2, (
            f"compact + 1-piece support should not split, got {len(texts)} text(s)"
        )
        ys = [float(t.attrib["y"]) for t in texts]
        assert ys[0] == ys[1], "lone support piece sits on value baseline"

    def test_value_only_renders_just_the_number(self):
        svg = _render_kpi(variant="compact", value="revenue", _data=_KPI_DATA)
        root = ET.fromstring(svg)
        texts = root.findall("svg:text", _NS)
        assert len(texts) == 1, "compact with no label/support emits only the value"

    def test_label_carries_its_own_font_weight_not_default(self):
        # Companion regression to the size test below. The default theme
        # cascades `kpi.font.weight: 500` into every slot (per the
        # _base.yaml comment). An earlier compact emit left the right-column
        # tspans without a font-weight, so labels dropped through to the SVG
        # default 400 — visibly diverging from stacked's 500. Every
        # right-column tspan must carry an explicit font-weight.
        svg = _render_kpi(
            variant="compact",
            value="revenue",
            label="Revenue",
            support=_support_full(),
            _data=_KPI_DATA,
        )
        root = ET.fromstring(svg)
        for text in root.findall("svg:text", _NS):
            for tspan in text.findall("svg:tspan", _NS):
                content = (tspan.text or "").strip()
                if content == "Revenue":
                    assert "font-weight" in tspan.attrib, (
                        "compact label tspan must declare its own font-weight "
                        "so kpi.label.font.weight flows through the cascade"
                    )
                    return
        pytest.fail("label tspan not found in compact output")

    def test_label_carries_its_own_font_size_not_supports(self):
        # Regression: an earlier compact emit set the parent <text>'s
        # font-size to support_font_size and let the label tspan inherit
        # it — silently ignoring kpi.label.font.size for any theme that
        # diverges the two. Every right-column tspan must carry its own
        # font-size so the theme cascade reaches it.
        svg = _render_kpi(
            variant="compact",
            value="revenue",
            label="Revenue",
            support=_support_full(),
            _data=_KPI_DATA,
        )
        root = ET.fromstring(svg)
        for text in root.findall("svg:text", _NS):
            for tspan in text.findall("svg:tspan", _NS):
                content = (tspan.text or "").strip()
                if content == "Revenue":
                    assert "font-size" in tspan.attrib, (
                        "compact label tspan must declare its own font-size "
                        "so kpi.label.font.size flows through"
                    )
                    return
        pytest.fail("label tspan not found in compact output")


class TestKpiVariantDispatch:
    """Cross-variant invariants."""

    def test_default_is_stacked(self):
        from dbt_charts.core.compile.models.chart.authored import (
            KpiChart as AuthoredKpiChart,
        )

        # Existing boards that omit `variant:` keep rendering as stacked —
        # the variant field defaults to 'stacked' in the authored model.
        chart_unset = TypeAdapter(AuthoredKpiChart).validate_python(
            {"type": "kpi", "value": "revenue", "label": "Revenue"}
        )
        chart_explicit = TypeAdapter(AuthoredKpiChart).validate_python(
            {
                "type": "kpi",
                "value": "revenue",
                "label": "Revenue",
                "variant": "stacked",
            }
        )
        assert chart_unset.variant == chart_explicit.variant == "stacked"

    def test_all_variants_degenerate_to_bare_value(self):
        # value-only inputs render identically across variants — just the
        # number, no label / support text. This is the equivalence anchor
        # for the degenerate corner of the variant matrix.
        for variant in ("stacked", "inline", "compact"):
            svg = _render_kpi(variant=variant, value="revenue", _data=_KPI_DATA)
            root = ET.fromstring(svg)
            texts = root.findall("svg:text", _NS)
            assert len(texts) == 1, (
                f"variant={variant!r}: bare value should emit one <text>, "
                f"got {len(texts)}"
            )
            content = "".join(t.text or "" for t in texts[0].findall("svg:tspan", _NS))
            assert "1.5" in content
