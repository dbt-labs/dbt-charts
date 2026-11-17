"""Behavior tests for the KPI quantitative-text-object redesign.

Covers the contract changes:

* ``value:`` (column reference — always a column name) replaces ``metric:``
* ``glyph`` at chart root on the headline value; no ``value_color`` / ``glyph_color``
* ``support: { value, label, glyph }`` block; ``style.support.{format, tone}`` live
  in style, since the support row is the block tone paints (headline value stays neutral)
* Editorial notation default for the headline number
* Three-slot layout (value → label → support) with fixed structural minimum
"""

from __future__ import annotations

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
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

_DUMMY_QUERY = SqlQuery(sql="SELECT 1", source="t")


def _resolved_kpi(**kwargs):
    data = kwargs.pop("_data", None) or [{"revenue": 1_500_000}]
    style = dict(kwargs.pop("style", None) or {})
    if "variant" in kwargs:
        style["variant"] = kwargs.pop("variant")
    fmt = kwargs.pop("format", None)
    if fmt is not None:
        style["value"] = {**style.get("value", {}), "format": fmt}
    support_style = {
        key: kwargs.pop(f"support_{key}")
        for key in ("tone", "format")
        if f"support_{key}" in kwargs
    }
    if support_style:
        style["support"] = {**style.get("support", {}), **support_style}
    if style:
        kwargs["style"] = style
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

    def test_kpi_value_may_be_left_to_bind_by_position(self):
        from dbt_charts.core.compile.models.chart.authored import (
            KpiChart as AuthoredKpiChart,
        )

        # An unnamed value binds the query's one column at render.
        chart = TypeAdapter(AuthoredKpiChart).validate_python({"type": "kpi"})
        assert chart.value is None

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


class TestKpiSupportBlock:
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
                    "value_color": "#0066cc",
                }
            )


# ---------------------------------------------------------------------------
# Tone + glyph + override precedence on the main KPI block
# ---------------------------------------------------------------------------


class TestKpiTonePrecedence:
    def test_style_tone_rejected(self):
        """style.tone is retired — the headline value has no tone field.
        Tone lives on the block it paints: style.support.tone."""
        from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch

        with pytest.raises(ValidationError, match="tone"):
            KpiChartStylePatch.model_validate({"tone": "warning"})

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
    instead of erroring the whole render. A ``style.support.format`` mismatch
    raises, the same as an equivalent mistake would on a table column. A genuinely broken strftime directive raises on
    either slot.
    """

    def test_strftime_alias_applies_to_date_value(self):
        # An inline `%`-spec directly under `format:` is compile-rejected on
        # this slot (`_TIME_CAPABLE_FIELDS` doesn't cover KPI value/support);
        # a `style.formats` alias (validated with `time_format=True`) is the
        # only author-reachable route to a strftime spec here, and resolves
        # through the same `resolve_format` call `kpi_value_text` makes.
        from dbt_charts.core.render.chart.kpi_value import kpi_value_text

        text = kpi_value_text(
            "2026-11-15", "my_date", "t", formats={"my_date": "%Y/%m"}
        )
        assert text == "2026/11"

    def test_negative_value_with_spaced_prefix_places_sign_after_prefix(self):
        """A KPI has no anchor-row concept -- unlike a table's `symbol_mode."""
        from dbt_charts.core.compile.models.primitives import FormatConfig
        from dbt_charts.core.render.chart.kpi_value import kpi_value_text

        text = kpi_value_text(
            -500, FormatConfig(spec=",.0f", prefix="EUR "), "chart_id", None
        )
        assert text == "EUR −500"

    def test_invalid_strftime_alias_raises(self):
        from dbt_charts.core.render.chart.kpi_value import kpi_value_text

        with pytest.raises(ChartDataError, match="unknown directive"):
            kpi_value_text("2026-11-15", "bad_date", "t", formats={"bad_date": "%v"})


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestKpiValidation:
    def test_unbound_value_rejected_at_resolve(self):
        from dbt_charts.core.compile.errors import CompilationError

        chart = KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
        )
        # resolve() binds value from a one-column result; two columns fit no
        # shape, and the resolved contract (a required str) is never reached.
        data = [{"revenue": 1, "cost": 2}]
        with pytest.raises(CompilationError, match="names no `value`"):
            resolve(chart, data, chart_style_context=_BOARD_STYLE)


# ---------------------------------------------------------------------------
# Round-trip + dispatch regression tests (review feedback)
# ---------------------------------------------------------------------------


class TestKpiNewFieldsRoundTripThroughSerializers:
    """The headline glyph is a chart root field; tone lives only on
    style.support.tone. Verify JSON serialization routes them correctly, and
    that yaml_format lists glyph but not tone as a chart root field."""

    def _kpi(self):
        from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch

        return KpiChart(
            id="t",
            query=_DUMMY_QUERY,
            query_name="q",
            type="kpi",
            label="Revenue",
            value="revenue",
            glyph="●",
            style=KpiChartStylePatch.model_validate({"support": {"tone": "positive"}}),
            support=KpiSupportConfig(
                value="delta_pct",
                label="vs LQ",
                glyph="▲",
            ),
        )

    def test_json_emit_preserves_all_kpi_fields(self):
        from dbt_charts.core.render.chart.serialization import build_dbt_charts_json

        chart = self._kpi()
        data = [{"revenue": 1_500_000, "delta_pct": 0.124}]
        out = build_dbt_charts_json(chart, data)
        assert out["value"] == "revenue"
        assert out["glyph"] == "●"
        assert "tone" not in out
        assert out["style"]["support"]["tone"] == "positive"
        # value_color and glyph_color were removed — must NOT be emitted.
        assert "value_color" not in out
        assert "glyph_color" not in out
        # Support is a Pydantic model — must serialize to a plain dict.
        assert out["support"]["value"] == "delta_pct"
        assert out["support"]["label"] == "vs LQ"
        assert out["support"]["glyph"] == "▲"
        assert "tone" not in out["support"]

    def test_yaml_emit_preserves_all_kpi_fields(self):
        from dbt_charts.core.render.board_to_dict import CHART_FIELDS

        assert "tone" not in CHART_FIELDS, "tone should have moved to style.support"
        for field in ("value", "label", "glyph", "support"):
            assert field in CHART_FIELDS, f"CHART_FIELDS missing {field!r}"
        # value_color and glyph_color are removed from the authoring surface.
        assert "value_color" not in CHART_FIELDS
        assert "glyph_color" not in CHART_FIELDS


class TestKpiVariantDispatch:
    """Cross-variant invariants."""

    def test_default_is_stacked(self):
        from dbt_charts.core.compile.models.chart.authored import (
            KpiChart as AuthoredKpiChart,
        )

        # Boards that omit `style.variant` resolve to stacked.
        unset, _ = _resolved_kpi(value="revenue", label="Revenue")
        explicit, _ = _resolved_kpi(value="revenue", label="Revenue", variant="stacked")
        assert unset.variant == explicit.variant == "stacked"
        with pytest.raises(ValidationError, match="variant"):
            TypeAdapter(AuthoredKpiChart).validate_python(
                {"type": "kpi", "value": "revenue", "variant": "stacked"}
            )
