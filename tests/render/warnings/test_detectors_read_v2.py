"""warning detectors work correctly with V2-typed charts.

RED before Step 6: The warning detectors use `getattr(chart, channel)` for
channels like 'size' and 'shape' that don't exist on discriminated V2 models,
raising AttributeError.  After Step 6, detectors use isinstance narrowing and
the tests go GREEN.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.primitives import DEFAULT_REGIONAL
from dbt_charts.core.diagnostics import (
    WARN_LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER,
    WARN_PIE_DOMINANT_SEGMENT,
    WARN_REDUNDANT_ENCODING,
    WARN_TEMPORAL_SINGLE_POINT,
    WARN_TOO_MANY_X_CATEGORIES,
    WARN_Y_ENCODING_MOSTLY_NULL,
)


def _board_style() -> Any:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style(get_default_theme_name()))


def _board_context() -> Any:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _compile_chart_v2(chart_yaml: str, chart_id: str) -> Any:
    """Compile a single chart YAML to ResolvedChart via the V2 normalize path."""
    import yaml

    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.compile.resolve import resolve

    chart_dict = yaml.safe_load(chart_yaml)
    compiled = normalize_chart(chart_id, chart_dict, {}, sources={})
    return resolve(compiled, [], _board_context())


def _make_v2_board(charts: dict[str, Any]) -> Any:
    """Construct a minimal ResolvedBoard with V2-typed charts (empty layout)."""
    from dbt_charts.core.compile.models.board.resolved import (
        ResolvedBoard,
        ResolvedLayout,
    )

    layout = ResolvedLayout(
        type="rows",
        items=(),
        width=800.0,
        height=600.0,
        content_width=800.0,
        content_height=600.0,
    )
    return ResolvedBoard(
        id="test",
        title="",
        notes="",
        tags=(),
        text="",
        base_dir=None,
        regional=DEFAULT_REGIONAL,
        html_policy="none",
        level=1,
        style=_board_style(),
        page_padding=20.0,
        card_padding=16.0,
        card_gap=8.0,
        width=800.0,
        height=600.0,
        layout=layout,
        charts=charts,
        variables={},
        queries={},
        variable_defaults={},
    )


_BAR_WITH_REDUNDANT_Y_COLOR = """
type: bar
x: cat
y: val
color: val
"""

_PIE_CHART = """
type: pie
theta: val
color: cat
"""


class TestRedundantEncodingReadsV2:
    """REDUNDANT_ENCODING detector fires correctly on V2-typed charts."""

    def test_fires_on_v2_bar_with_y_color_redundancy(self) -> None:
        """Detector fires on a V2 bar where y == color (genuinely redundant)."""
        from dbt_charts.core.render.warnings import redundant_encoding as detector
        from dbt_charts.core.render.warnings.base import WarningContext

        bar = _compile_chart_v2(_BAR_WITH_REDUNDANT_Y_COLOR, "bar1")
        board = _make_v2_board({"bar1": bar})
        ctx = WarningContext(
            board_spec=board,
            chart_results={"bar1": [{"cat": "A", "val": 10}]},
            vega_specs={"bar1": {"mark": "bar"}},
        )
        warnings = detector.detect(ctx)
        codes = {w.code for w in warnings}
        assert WARN_REDUNDANT_ENCODING.code in codes, (
            f"Expected REDUNDANT_ENCODING for y==color on bar; got warnings={warnings}"
        )

    def test_x_color_excluded_from_redundant_encoding(self) -> None:
        """The bar x==color case is excluded from REDUNDANT_ENCODING.

        The renderer suppresses grouped-bar offset for this pattern and renders
        full-width category-colored bars — a valid authoring intent.
        """
        from dbt_charts.core.render.warnings import redundant_encoding as detector
        from dbt_charts.core.render.warnings.base import WarningContext

        bar = _compile_chart_v2("type: bar\nx: cat\ny: val\ncolor: cat\n", "bar1")
        board = _make_v2_board({"bar1": bar})
        ctx = WarningContext(
            board_spec=board,
            chart_results={"bar1": [{"cat": "A", "val": 10}]},
            vega_specs={"bar1": {"mark": "bar", "encoding": {"x": {}, "color": {}}}},
        )
        warnings = detector.detect(ctx)
        codes = {w.code for w in warnings}
        assert WARN_REDUNDANT_ENCODING.code not in codes, (
            f"REDUNDANT_ENCODING must not fire for x==color bar; got {codes}"
        )


_LINE_CHART = """
type: line
x: month
y: revenue
"""

_AREA_CHART = """
type: area
x: month
y: revenue
"""

_VERTICAL_BAR = """
type: bar
x: cat
y: revenue
style:
  orientation: vertical
"""

_BAR_CURRENCY_Y = """
type: bar
x: cat
y: revenue
"""


class TestTemporalSinglePointReadsV2:
    """TEMPORAL_SINGLE_POINT detector fires correctly on V2-typed charts."""

    def test_fires_on_v2_line_with_single_temporal_row(self) -> None:
        """Detector fires on a V2 line chart with one temporal data point."""
        from dbt_charts.core.render.warnings import temporal_single_point as detector
        from dbt_charts.core.render.warnings.base import WarningContext

        chart = _compile_chart_v2(_LINE_CHART, "line1")
        board = _make_v2_board({"line1": chart})
        ctx = WarningContext(
            board_spec=board,
            chart_results={"line1": [{"month": "2024-01", "revenue": 100}]},
            vega_specs={
                "line1": {"encoding": {"x": {"type": "temporal", "field": "month"}}}
            },
        )
        warnings = detector.detect(ctx)
        codes = {w.code for w in warnings}
        assert WARN_TEMPORAL_SINGLE_POINT.code in codes, (
            f"Expected TEMPORAL_SINGLE_POINT for single-row temporal line; got {codes}"
        )

    def test_no_fire_on_v2_line_with_multiple_rows(self) -> None:
        """Detector does NOT fire when there are multiple temporal data points."""
        from dbt_charts.core.render.warnings import temporal_single_point as detector
        from dbt_charts.core.render.warnings.base import WarningContext

        chart = _compile_chart_v2(_LINE_CHART, "line1")
        board = _make_v2_board({"line1": chart})
        ctx = WarningContext(
            board_spec=board,
            chart_results={
                "line1": [
                    {"month": "2024-01", "revenue": 100},
                    {"month": "2024-02", "revenue": 200},
                ]
            },
            vega_specs={
                "line1": {"encoding": {"x": {"type": "temporal", "field": "month"}}}
            },
        )
        warnings = detector.detect(ctx)
        codes = {w.code for w in warnings}
        assert WARN_TEMPORAL_SINGLE_POINT.code not in codes, (
            f"Did not expect TEMPORAL_SINGLE_POINT for multi-row chart; got {codes}"
        )


class TestYEncodingMostlyNullReadsV2:
    """Y_ENCODING_MOSTLY_NULL detector fires correctly on V2-typed charts."""

    def test_fires_on_v2_bar_with_mostly_null_y(self) -> None:
        """Detector fires on a V2 bar chart where >50% of y values are null."""
        from dbt_charts.core.render.warnings import y_encoding_mostly_null as detector
        from dbt_charts.core.render.warnings.base import WarningContext

        chart = _compile_chart_v2(_BAR_CURRENCY_Y, "bar1")
        board = _make_v2_board({"bar1": chart})
        ctx = WarningContext(
            board_spec=board,
            chart_results={
                "bar1": [
                    {"cat": "A", "revenue": None},
                    {"cat": "B", "revenue": None},
                    {"cat": "C", "revenue": None},
                    {"cat": "D", "revenue": 100},
                ]
            },
            vega_specs={"bar1": {"mark": "bar"}},
        )
        warnings = detector.detect(ctx)
        codes = {w.code for w in warnings}
        assert WARN_Y_ENCODING_MOSTLY_NULL.code in codes, (
            f"Expected Y_ENCODING_MOSTLY_NULL for 75% null y; got {codes}"
        )

    def test_no_fire_on_v2_bar_with_few_nulls(self) -> None:
        """Detector does NOT fire when fewer than half of y values are null."""
        from dbt_charts.core.render.warnings import y_encoding_mostly_null as detector
        from dbt_charts.core.render.warnings.base import WarningContext

        chart = _compile_chart_v2(_BAR_CURRENCY_Y, "bar1")
        board = _make_v2_board({"bar1": chart})
        ctx = WarningContext(
            board_spec=board,
            chart_results={
                "bar1": [
                    {"cat": "A", "revenue": 100},
                    {"cat": "B", "revenue": 200},
                    {"cat": "C", "revenue": None},
                ]
            },
            vega_specs={"bar1": {"mark": "bar"}},
        )
        warnings = detector.detect(ctx)
        codes = {w.code for w in warnings}
        assert WARN_Y_ENCODING_MOSTLY_NULL.code not in codes, (
            f"Did not expect Y_ENCODING_MOSTLY_NULL for <50% null y; got {codes}"
        )


class TestTooManyXCategoriesReadsV2:
    """TOO_MANY_X_CATEGORIES detector fires correctly on V2-typed charts."""

    def test_fires_on_v2_bar_with_51_categories(self) -> None:
        """Detector fires on a V2 bar chart with 51 distinct x categories."""
        from dbt_charts.core.render.warnings import too_many_x_categories as detector
        from dbt_charts.core.render.warnings.base import WarningContext

        chart = _compile_chart_v2(_VERTICAL_BAR, "bar1")
        board = _make_v2_board({"bar1": chart})
        rows = [{"cat": f"cat_{i}", "revenue": i} for i in range(51)]
        ctx = WarningContext(
            board_spec=board,
            chart_results={"bar1": rows},
            vega_specs={
                "bar1": {
                    "encoding": {
                        "x": {"type": "nominal", "field": "cat"},
                    }
                }
            },
        )
        warnings = detector.detect(ctx)
        codes = {w.code for w in warnings}
        assert WARN_TOO_MANY_X_CATEGORIES.code in codes, (
            f"Expected TOO_MANY_X_CATEGORIES for 51 categories; got {codes}"
        )

    def test_no_fire_on_v2_bar_with_few_categories(self) -> None:
        """Detector does NOT fire when x categories are within the limit."""
        from dbt_charts.core.render.warnings import too_many_x_categories as detector
        from dbt_charts.core.render.warnings.base import WarningContext

        chart = _compile_chart_v2(_VERTICAL_BAR, "bar1")
        board = _make_v2_board({"bar1": chart})
        rows = [{"cat": f"cat_{i}", "revenue": i} for i in range(10)]
        ctx = WarningContext(
            board_spec=board,
            chart_results={"bar1": rows},
            vega_specs={
                "bar1": {
                    "encoding": {
                        "x": {"type": "nominal", "field": "cat"},
                    }
                }
            },
        )
        warnings = detector.detect(ctx)
        codes = {w.code for w in warnings}
        assert WARN_TOO_MANY_X_CATEGORIES.code not in codes, (
            f"Did not expect TOO_MANY_X_CATEGORIES for 10 categories; got {codes}"
        )


class TestLikelyCurrencyOrPercentMissingFormatterReadsV2:
    """LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER fires on V2-typed charts."""

    def test_fires_on_v2_bar_with_unformatted_currency_y(self) -> None:
        """Detector fires on a V2 bar chart with a currency-like y field and no format."""
        from dbt_charts.core.render.warnings import (
            likely_currency_or_percent_missing_formatter as detector,
        )
        from dbt_charts.core.render.warnings.base import WarningContext

        chart = _compile_chart_v2(_VERTICAL_BAR, "bar1")
        board = _make_v2_board({"bar1": chart})
        ctx = WarningContext(
            board_spec=board,
            chart_results={"bar1": [{"cat": "A", "revenue": 1000}]},
            vega_specs={"bar1": {"mark": "bar"}},
        )
        warnings = detector.detect(ctx)
        codes = {w.code for w in warnings}
        assert WARN_LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER.code in codes, (
            f"Expected LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER for 'revenue' y; got {codes}"
        )

    def test_no_fire_on_v2_bar_with_non_currency_y(self) -> None:
        """Detector does NOT fire when y field does not look like currency/percent."""
        from dbt_charts.core.render.warnings import (
            likely_currency_or_percent_missing_formatter as detector,
        )
        from dbt_charts.core.render.warnings.base import WarningContext

        chart = _compile_chart_v2("type: bar\nx: cat\ny: count\n", "bar1")
        board = _make_v2_board({"bar1": chart})
        ctx = WarningContext(
            board_spec=board,
            chart_results={"bar1": [{"cat": "A", "count": 5}]},
            vega_specs={"bar1": {"mark": "bar"}},
        )
        warnings = detector.detect(ctx)
        codes = {w.code for w in warnings}
        assert WARN_LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER.code not in codes, (
            f"Did not expect formatter warning for 'count' y; got {codes}"
        )


class TestPieDominantSegmentReadsV2:
    """PIE_DOMINANT_SEGMENT detector fires correctly on V2-typed charts."""

    def test_fires_on_v2_pie_with_dominant_segment(self) -> None:
        """Detector fires when a V2 pie chart has a clearly dominant segment."""
        from dbt_charts.core.render.warnings import pie_dominant_segment as detector
        from dbt_charts.core.render.warnings.base import WarningContext

        pie = _compile_chart_v2(_PIE_CHART, "pie1")
        board = _make_v2_board({"pie1": pie})
        rows = [{"cat": "A", "val": 95}, {"cat": "B", "val": 5}]
        ctx = WarningContext(
            board_spec=board,
            chart_results={"pie1": rows},
            vega_specs={},
        )
        warnings = detector.detect(ctx)
        codes = {w.code for w in warnings}
        assert WARN_PIE_DOMINANT_SEGMENT.code in codes, (
            f"Expected PIE_DOMINANT_SEGMENT for 95% slice; got {codes}"
        )

    def test_no_fire_on_v2_pie_with_balanced_segments(self) -> None:
        """Detector does NOT fire when segments are balanced."""
        from dbt_charts.core.render.warnings import pie_dominant_segment as detector
        from dbt_charts.core.render.warnings.base import WarningContext

        pie = _compile_chart_v2(_PIE_CHART, "pie1")
        board = _make_v2_board({"pie1": pie})
        rows = [
            {"cat": "A", "val": 33},
            {"cat": "B", "val": 34},
            {"cat": "C", "val": 33},
        ]
        ctx = WarningContext(
            board_spec=board,
            chart_results={"pie1": rows},
            vega_specs={},
        )
        warnings = detector.detect(ctx)
        codes = {w.code for w in warnings}
        assert WARN_PIE_DOMINANT_SEGMENT.code not in codes, (
            f"Did not expect PIE_DOMINANT_SEGMENT for balanced segments; got {codes}"
        )
