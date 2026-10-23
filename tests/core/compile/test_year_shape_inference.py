"""Value-driven axis-type inference for year-shaped INTEGER/VARCHAR columns.

`is_year_shaped` itself (a neutral value-only predicate, `dbt_charts.core.utils`)
and its own coverage live in `dbt-charts/tests/core/test_utils.py`. These tests
pin its two compile/render consumers, in both directions (year →
temporal-shaped, non-year → unaffected).
"""

from dbt_charts.core.compile.resolve.chart.enrich import (
    is_column_discrete_for_bar_orientation,
)


class TestScatterYearShapedMeasureStaysQuantitative:
    def test_scatter_year_shaped_y_matches_non_year_y_axis_style(self) -> None:
        """A measure is never a year: scatter's y-axis style cascade for a
        year-shaped y (values in [1700, 2100]) must be identical to a
        non-year quantitative y — the year-shape check is dimension-only,
        so it must never fire on a measure channel."""
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.chart.normalized import ScatterChart
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        board_style = resolve_chart_style_context(get_theme_style())
        year_data = [{"x": i, "launch_year": 2014 + i} for i in range(5)]
        plain_data = [{"x": i, "count": 100 + i} for i in range(5)]

        year_chart = ScatterChart(id="s1", type="scatter", x="x", y="launch_year")
        plain_chart = ScatterChart(id="s2", type="scatter", x="x", y="count")

        year_resolved = resolve(year_chart, year_data, chart_style_context=board_style)
        plain_resolved = resolve(
            plain_chart, plain_data, chart_style_context=board_style
        )

        # tick_values are data-derived (the actual y values) and expected to
        # differ; every structural style field must match — in particular
        # format, which only gets the quantitative-cascade merge when the
        # channel classifies as quantitative (never for temporal/band).
        assert (
            year_resolved.style.axis_y.labels.format
            == plain_resolved.style.axis_y.labels.format
        )
        assert year_resolved.style.axis_y.labels.format is not None

    def test_scatter_year_shaped_x_still_classifies_temporal(self) -> None:
        """Contrast case: a year-shaped x (dimension) still bakes a distinct
        axis style from a non-year x — the dimension/measure split is real,
        not a no-op."""
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.chart.normalized import ScatterChart
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        board_style = resolve_chart_style_context(get_theme_style())
        year_data = [{"launch_year": 2014 + i, "y": i} for i in range(5)]
        plain_data = [{"count": 100 + i, "y": i} for i in range(5)]

        year_chart = ScatterChart(id="s3", type="scatter", x="launch_year", y="y")
        plain_chart = ScatterChart(id="s4", type="scatter", x="count", y="y")

        year_resolved = resolve(year_chart, year_data, chart_style_context=board_style)
        plain_resolved = resolve(
            plain_chart, plain_data, chart_style_context=board_style
        )

        assert year_resolved.style.axis_x != plain_resolved.style.axis_x


class TestOrientationReadsYearShape:
    def test_varchar_year_strings_are_continuous(self) -> None:
        # #104: VARCHAR "2010".."2024" must NOT flip bars to horizontal
        assert is_column_discrete_for_bar_orientation(["2010", "2011", "2024"]) is False

    def test_varchar_non_year_still_discrete(self) -> None:
        assert is_column_discrete_for_bar_orientation(["Q1", "Q2", "Q3"]) is True

    def test_integer_years_still_continuous(self) -> None:
        assert is_column_discrete_for_bar_orientation([2010, 2011, 2024]) is False
