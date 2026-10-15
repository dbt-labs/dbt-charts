"""Bar orientation: strict type-based auto-flip for all x-column types.

The regression: `_bar_orientation` (v2 compile path) used `classify_column_type`
without `db_type_str`, which coerces numeric-looking strings ("10", "20") to
"numeric" → vertical bar. The correct behavior: Python str → discrete → horizontal.

This suite pins the correct strict behavior via the v2 pipeline.
"""

from __future__ import annotations

import datetime
from typing import Any

from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery


def _sql() -> SqlQuery:
    return SqlQuery(sql="SELECT 1", source="src")


def _board_style() -> Any:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _bar(x: str, y: str = "revenue") -> BarChart:
    return BarChart(id="b", type="bar", x=x, y=y, query=_sql(), query_name="q")


# ---------------------------------------------------------------------------
# is_column_discrete_for_bar_orientation — unit tests on the canonical helper
# ---------------------------------------------------------------------------


class TestIsColumnDiscreteForBarOrientation:
    """Canonical helper: strict (Python str → discrete; int/float/datetime → continuous)."""

    def test_string_categories_are_discrete(self) -> None:
        from dbt_charts.core.compile.resolve.chart.enrich import (
            is_column_discrete_for_bar_orientation,
        )

        assert is_column_discrete_for_bar_orientation(["USD", "EUR", "GBP"]) is True

    def test_numeric_looking_strings_are_discrete(self) -> None:
        """VARCHAR "123" must not be coerced to numeric — the core regression."""
        from dbt_charts.core.compile.resolve.chart.enrich import (
            is_column_discrete_for_bar_orientation,
        )

        assert is_column_discrete_for_bar_orientation(["10", "20", "30"]) is True

    def test_native_ints_are_continuous(self) -> None:
        from dbt_charts.core.compile.resolve.chart.enrich import (
            is_column_discrete_for_bar_orientation,
        )

        assert is_column_discrete_for_bar_orientation([1, 2, 3]) is False

    def test_native_floats_are_continuous(self) -> None:
        from dbt_charts.core.compile.resolve.chart.enrich import (
            is_column_discrete_for_bar_orientation,
        )

        assert is_column_discrete_for_bar_orientation([1.5, 2.5, 3.0]) is False

    def test_date_objects_are_continuous(self) -> None:
        from dbt_charts.core.compile.resolve.chart.enrich import (
            is_column_discrete_for_bar_orientation,
        )

        dates = [datetime.date(2024, 1, 1), datetime.date(2024, 2, 1)]
        assert is_column_discrete_for_bar_orientation(dates) is False

    def test_datetime_objects_are_continuous(self) -> None:
        from dbt_charts.core.compile.resolve.chart.enrich import (
            is_column_discrete_for_bar_orientation,
        )

        dts = [datetime.datetime(2024, 1, 1, 0, 0), datetime.datetime(2024, 2, 1, 0, 0)]
        assert is_column_discrete_for_bar_orientation(dts) is False

    def test_iso_date_strings_are_continuous(self) -> None:
        from dbt_charts.core.compile.resolve.chart.enrich import (
            is_column_discrete_for_bar_orientation,
        )

        assert (
            is_column_discrete_for_bar_orientation(["2024-01-01", "2024-02-01"])
            is False
        )

    def test_year_month_strings_are_continuous(self) -> None:
        """Month-bucket strings like "2024-01" are temporal → continuous."""
        from dbt_charts.core.compile.resolve.chart.enrich import (
            is_column_discrete_for_bar_orientation,
        )

        assert (
            is_column_discrete_for_bar_orientation(["2024-01", "2024-02", "2024-03"])
            is False
        )

    def test_iso_week_strings_are_continuous(self) -> None:
        from dbt_charts.core.compile.resolve.chart.enrich import (
            is_column_discrete_for_bar_orientation,
        )

        # "2024-W01" ISO 8601 week strings → temporal → continuous (not discrete).
        assert (
            is_column_discrete_for_bar_orientation(["2024-W01", "2024-W02", "2024-W03"])
            is False
        )

    def test_quarter_strings_are_continuous(self) -> None:
        from dbt_charts.core.compile.resolve.chart.enrich import (
            is_column_discrete_for_bar_orientation,
        )

        assert is_column_discrete_for_bar_orientation(["Q1 2024", "Q2 2024"]) is False

    def test_empty_samples_are_not_discrete(self) -> None:
        from dbt_charts.core.compile.resolve.chart.enrich import (
            is_column_discrete_for_bar_orientation,
        )

        assert is_column_discrete_for_bar_orientation([]) is False

    def test_booleans_are_discrete(self) -> None:
        from dbt_charts.core.compile.resolve.chart.enrich import (
            is_column_discrete_for_bar_orientation,
        )

        assert is_column_discrete_for_bar_orientation([True, False, True]) is True


# ---------------------------------------------------------------------------
# v2 path: _bar_orientation regression
# ---------------------------------------------------------------------------


class TestV2BarOrientationVarcharNumeric:
    """v2 _bar_orientation must treat VARCHAR-numeric strings as discrete → horizontal."""

    _varchar_numeric_data: list[dict[str, Any]] = [
        {"page_count": "10", "revenue": 1000},
        {"page_count": "20", "revenue": 2000},
        {"page_count": "5", "revenue": 500},
    ]

    def test_varchar_numeric_x_is_horizontal(self) -> None:
        """Core regression: string x values → horizontal, not vertical."""
        from dbt_charts.core.compile.resolve import resolve

        resolved = resolve(
            _bar(x="page_count"), self._varchar_numeric_data, _board_style()
        )
        assert resolved.orientation == "horizontal", (
            f"VARCHAR-numeric x gave orientation={resolved.orientation!r}; "
            "expected 'horizontal' (discrete string column)"
        )

    def test_native_numeric_x_is_vertical(self) -> None:
        """Native int/float x → vertical (not a regression, confirms baseline)."""
        from dbt_charts.core.compile.resolve import resolve

        data = [{"qty": 10, "revenue": 1000}, {"qty": 20, "revenue": 2000}]
        resolved = resolve(_bar(x="qty"), data, _board_style())
        assert resolved.orientation == "vertical"


# ---------------------------------------------------------------------------
# Bucketed-temporal orientation: time_unit guard
# ---------------------------------------------------------------------------


class TestBucketedTemporalOrientation:
    """When x has a time_unit (e.g. monthofyear), orientation must be vertical.

    monthofyear emits nominal-looking labels ("January", "February") but rides
    a temporal scale — both paths must NOT flip to horizontal.
    """

    _monthofyear_data: list[dict[str, Any]] = [
        {"month_name": "January", "revenue": 1000},
        {"month_name": "February", "revenue": 2000},
        {"month_name": "March", "revenue": 1500},
    ]

    def test_v2_time_unit_forces_vertical(self) -> None:
        """v2: bar with axis_x.time_unit=monthofyear → orientation vertical."""
        from dbt_charts.core.compile.models.style.authored import (
            AxisXStylePatch,
            BarChartStylePatch,
        )
        from dbt_charts.core.compile.resolve import resolve

        bar = BarChart(
            id="b",
            type="bar",
            x="month_name",
            y="revenue",
            query=_sql(),
            query_name="q",
            style=BarChartStylePatch(
                axis_x=AxisXStylePatch(time_unit="monthofyear"),
            ),
        )
        resolved = resolve(bar, self._monthofyear_data, _board_style())
        assert resolved.orientation == "vertical", (
            f"monthofyear time_unit gave orientation={resolved.orientation!r}; "
            "expected 'vertical' (bucketed time → continuous)"
        )

    def test_authored_orientation_wins_over_bucketed_time_default(self) -> None:
        """Authored style.orientation must beat the bucketed-time vertical default."""
        from dbt_charts.core.compile.models.style.authored import (
            AxisXStylePatch,
            BarChartStylePatch,
        )
        from dbt_charts.core.compile.resolve import resolve

        bar = BarChart(
            id="b",
            type="bar",
            x="month_name",
            y="revenue",
            query=_sql(),
            query_name="q",
            style=BarChartStylePatch(
                axis_x=AxisXStylePatch(time_unit="monthofyear"),
                orientation="horizontal",
            ),
        )
        resolved = resolve(bar, self._monthofyear_data, _board_style())
        assert resolved.orientation == "horizontal"


# ---------------------------------------------------------------------------
# Board-level style.charts.bar.orientation cascade
# ---------------------------------------------------------------------------


class TestBoardLevelOrientationCascade:
    """A board-level `style.charts.bar.orientation` must reach the chart,
    with an inline `style.orientation` still winning over it."""

    _string_x_data: list[dict[str, Any]] = [
        {"region": "north", "revenue": 1000},
        {"region": "south", "revenue": 2000},
        {"region": "east", "revenue": 1500},
    ]

    def _context(self, orientation: str) -> Any:
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        patch = StylePatch.model_validate(
            {"charts": {"bar": {"orientation": orientation}}}
        )
        return resolve_chart_style_context(get_theme_style(), patch)

    def test_board_level_vertical_flips_a_string_x_bar(self) -> None:
        """String x infers horizontal; the board tier must override that."""
        from dbt_charts.core.compile.resolve import resolve

        resolved = resolve(
            _bar(x="region"), self._string_x_data, self._context("vertical")
        )
        assert resolved.orientation == "vertical"

    def test_inline_orientation_still_beats_the_board_tier(self) -> None:
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
        from dbt_charts.core.compile.resolve import resolve

        chart = BarChart(
            id="b",
            type="bar",
            x="region",
            y="revenue",
            query=_sql(),
            query_name="q",
            style=BarChartStylePatch(orientation="horizontal"),
        )
        resolved = resolve(chart, self._string_x_data, self._context("vertical"))
        assert resolved.orientation == "horizontal"

    def test_board_level_auto_falls_through_to_inference(self) -> None:
        from dbt_charts.core.compile.resolve import resolve

        resolved = resolve(_bar(x="region"), self._string_x_data, self._context("auto"))
        assert resolved.orientation == "horizontal"

    def test_no_board_style_still_infers_from_the_column(self) -> None:
        from dbt_charts.core.compile.resolve import resolve

        resolved = resolve(_bar(x="region"), self._string_x_data, _board_style())
        assert resolved.orientation == "horizontal"


# ---------------------------------------------------------------------------
# _classify_to_channel_type strict: VARCHAR-numeric → "nominal" not "quantitative"
# ---------------------------------------------------------------------------


class TestClassifyToChannelTypeStrict:
    """_classify_to_channel_type must not coerce numeric-looking strings to quantitative."""

    def test_varchar_numeric_string_x_type_is_nominal(self) -> None:
        """String "10"/"20"/"30" → channel type "nominal", not "quantitative"."""
        from dbt_charts.core.compile.resolve.chart._channels import (
            _classify_to_channel_type,
        )

        samples = [
            {"page_count": "10", "revenue": 1000},
            {"page_count": "20", "revenue": 2000},
        ]
        result = _classify_to_channel_type("page_count", samples, is_dimension=True)
        assert result == "nominal", (
            f"VARCHAR-numeric column got channel type {result!r}; expected 'nominal'"
        )

    def test_native_int_x_type_is_quantitative(self) -> None:
        from dbt_charts.core.compile.resolve.chart._channels import (
            _classify_to_channel_type,
        )

        samples = [{"qty": 10, "rev": 100}, {"qty": 20, "rev": 200}]
        assert (
            _classify_to_channel_type("qty", samples, is_dimension=True)
            == "quantitative"
        )

    def test_date_string_x_type_is_temporal(self) -> None:
        from dbt_charts.core.compile.resolve.chart._channels import (
            _classify_to_channel_type,
        )

        samples = [{"day": "2024-01-01", "rev": 100}, {"day": "2024-02-01", "rev": 200}]
        assert (
            _classify_to_channel_type("day", samples, is_dimension=True) == "temporal"
        )

    def test_leading_nulls_do_not_starve_sampling(self) -> None:
        """First 20 non-null samples, not first 20 rows null-filtered."""
        from dbt_charts.core.compile.resolve.chart._channels import (
            _classify_to_channel_type,
        )

        rows = [{"qty": None} for _ in range(20)] + [{"qty": 10}, {"qty": 20}]
        assert (
            _classify_to_channel_type("qty", rows, is_dimension=True) == "quantitative"
        )

    def test_decimal_x_type_is_quantitative(self) -> None:
        from decimal import Decimal

        from dbt_charts.core.compile.resolve.chart._channels import (
            _classify_to_channel_type,
        )

        samples = [{"qty": Decimal("10.5")}, {"qty": Decimal("20.5")}]
        assert (
            _classify_to_channel_type("qty", samples, is_dimension=True)
            == "quantitative"
        )

    def test_integer_year_x_type_is_temporal_not_quantitative(self) -> None:
        """Year-shaped INTEGER x must bake the band/temporal axis-style cascade,
        not axis_quantitative — otherwise the quantitative SI-suffix default
        (~s) leaks onto the year ticks even after orientation/vl_type agree
        the column is continuous-temporal."""
        from dbt_charts.core.compile.resolve.chart._channels import (
            _classify_to_channel_type,
        )

        samples = [{"launch_year": 2014, "total": 3}, {"launch_year": 2015, "total": 5}]
        assert (
            _classify_to_channel_type("launch_year", samples, is_dimension=True)
            == "temporal"
        )

    def test_varchar_year_x_type_is_temporal(self) -> None:
        from dbt_charts.core.compile.resolve.chart._channels import (
            _classify_to_channel_type,
        )

        samples = [{"yr": "2014", "total": 3}, {"yr": "2015", "total": 5}]
        assert _classify_to_channel_type("yr", samples, is_dimension=True) == "temporal"

    def test_non_year_integer_x_type_stays_quantitative(self) -> None:
        from dbt_charts.core.compile.resolve.chart._channels import (
            _classify_to_channel_type,
        )

        samples = [{"count": 1, "total": 3}, {"count": 2, "total": 5}]
        assert (
            _classify_to_channel_type("count", samples, is_dimension=True)
            == "quantitative"
        )

    def test_year_shaped_measure_is_not_temporal(self) -> None:
        """The year-shape check is dimension/x-only — a measure column (e.g. a
        scatter's y field) that happens to hold values in [1700, 2100] must
        stay quantitative, never bake the band/temporal axis-style cascade.
        A measure is never a year."""
        from dbt_charts.core.compile.resolve.chart._channels import (
            _classify_to_channel_type,
        )

        samples = [{"launch_year": 2014}, {"launch_year": 2015}]
        assert (
            _classify_to_channel_type("launch_year", samples, is_dimension=False)
            == "quantitative"
        )
