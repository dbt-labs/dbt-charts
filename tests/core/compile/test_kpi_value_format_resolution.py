"""KPI headline value format is finalized at resolve, not render.

``ResolvedKpiChart.format`` must carry the final, data-aware format decision
(narrative/analytic notation, SI compaction threshold, explicit authored
precedence, non-numeric passthrough) — render consumes it as-is.
"""

from __future__ import annotations

from decimal import Decimal

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.format import resolve_format
from dbt_charts.core.compile.models.chart.normalized import KpiChart
from dbt_charts.core.compile.models.primitives import FormatConfig, ResolvedFormat
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_DUMMY_QUERY = SqlQuery(sql="SELECT 1", source="t")
_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


def _narrative(spec: str) -> ResolvedFormat:
    return ResolvedFormat(spec=spec, notation="narrative", raw=spec)


def _kpi(*, fmt=None) -> KpiChart:
    style = None
    if fmt is not None:
        style = KpiChartStylePatch.model_validate({"value": {"format": fmt}})
    return KpiChart(
        id="t",
        query=_DUMMY_QUERY,
        query_name="q",
        type="kpi",
        value="metric",
        label="Metric",
        style=style,
    )


class TestUnauthoredFormatThreshold:
    def test_below_threshold_stays_unformatted(self):
        resolved = resolve(_kpi(), [{"metric": 131}], chart_style_context=_BOARD_STYLE)
        assert resolved.format is None

    def test_above_threshold_defaults_to_narrative_compact(self):
        resolved = resolve(
            _kpi(), [{"metric": 1_500_000}], chart_style_context=_BOARD_STYLE
        )
        assert resolved.format == _narrative(".2~s")

    def test_at_threshold_defaults_to_narrative_compact(self):
        resolved = resolve(_kpi(), [{"metric": 1000}], chart_style_context=_BOARD_STYLE)
        assert resolved.format == _narrative(".2~s")

    def test_decimal_headline_value_above_threshold_compacts_same_as_int(self):
        # Warehouse NUMERIC/DECIMAL columns (dbt adapters, BigQuery, DuckDB)
        # surface as decimal.Decimal — must be treated as numeric identically
        # to an int/float cell, not silently fall through to unformatted.
        resolved = resolve(
            _kpi(), [{"metric": Decimal("1500000")}], chart_style_context=_BOARD_STYLE
        )
        assert resolved.format == _narrative(".2~s")


class TestTypedFormatDefaulting:
    def test_missing_notation_and_spec_above_threshold_compacts_narrative(self):
        resolved = resolve(
            _kpi(fmt=FormatConfig()),
            [{"metric": 1_500_000}],
            chart_style_context=_BOARD_STYLE,
        )
        assert resolved.format == _narrative(".2~s")

    def test_missing_spec_below_threshold_stays_exact(self):
        resolved = resolve(
            _kpi(fmt=FormatConfig(notation="narrative")),
            [{"metric": 131}],
            chart_style_context=_BOARD_STYLE,
        )
        assert resolved.format == ResolvedFormat(spec="", notation="narrative")

    def test_explicit_analytic_notation_is_preserved(self):
        resolved = resolve(
            _kpi(fmt=FormatConfig(spec=".2s", notation="analytic")),
            [{"metric": 1_500_000}],
            chart_style_context=_BOARD_STYLE,
        )
        assert resolved.format == ResolvedFormat(
            spec=".2s", notation="analytic", raw=".2s"
        )

    def test_explicit_spec_disables_compaction_default(self):
        resolved = resolve(
            _kpi(fmt=FormatConfig(spec=",.0f")),
            [{"metric": 1_500_000}],
            chart_style_context=_BOARD_STYLE,
        )
        assert resolved.format == _narrative(",.0f")


class TestPlainStringSpec:
    def test_plain_string_spec_forces_narrative_and_disables_compaction(self):
        resolved = resolve(
            _kpi(fmt=",.0f"), [{"metric": 1_500_000}], chart_style_context=_BOARD_STYLE
        )
        assert resolved.format == _narrative(",.0f")


class TestPredefinedNameKeepsIdentity:
    """A predefined format name must reach render as the name, not its d3 spec."""

    def test_plain_string_predefined_name_is_kept(self):
        resolved = resolve(
            _kpi(fmt="currency"), [{"metric": 0.45}], chart_style_context=_BOARD_STYLE
        )
        assert resolved.format == ResolvedFormat(
            spec=resolve_format("currency"), notation="narrative", raw="currency"
        )

    def test_format_config_predefined_name_is_kept_beside_an_affix(self):
        resolved = resolve(
            _kpi(fmt=FormatConfig(spec="currency", suffix=" EUR")),
            [{"metric": 0.45}],
            chart_style_context=_BOARD_STYLE,
        )
        assert resolved.format == ResolvedFormat(
            spec=resolve_format("currency"),
            suffix=" EUR",
            notation="narrative",
            raw="currency",
        )


class TestNonNumericValue:
    def test_non_numeric_value_with_no_authored_format_stays_unformatted(self):
        resolved = resolve(
            _kpi(), [{"metric": "At risk"}], chart_style_context=_BOARD_STYLE
        )
        assert resolved.format is None


class TestIndependentResolutionAcrossRows:
    def test_same_normalized_chart_resolved_against_different_rows_is_independent(
        self,
    ):
        normalized = _kpi(fmt=FormatConfig())
        before = normalized.style.value.format  # type: ignore[union-attr]

        low = resolve(normalized, [{"metric": 131}], chart_style_context=_BOARD_STYLE)
        high = resolve(
            normalized, [{"metric": 1_500_000}], chart_style_context=_BOARD_STYLE
        )

        assert low.format == ResolvedFormat(spec="", notation="narrative")
        assert high.format == _narrative(".2~s")
        # The normalized chart's own style must be untouched by either resolution.
        assert normalized.style.value.format == before  # type: ignore[union-attr]

    def test_resolving_high_then_low_row_does_not_leak_state(self):
        normalized = _kpi()

        high = resolve(
            normalized, [{"metric": 1_500_000}], chart_style_context=_BOARD_STYLE
        )
        low = resolve(normalized, [{"metric": 131}], chart_style_context=_BOARD_STYLE)

        assert high.format == _narrative(".2~s")
        assert low.format is None
