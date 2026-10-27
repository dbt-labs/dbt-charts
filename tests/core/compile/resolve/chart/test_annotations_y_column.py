"""`y_column` names the y column an annotation reads: its scale and its rows."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import BarChart, LineChart
from dbt_charts.core.compile.models.chart.resolved._annotation import (
    ResolvedAnnotation,
    ResolvedCategoryAnchor,
    ResolvedLayerAxis,
    ResolvedValueAnchor,
)
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.session import BoardRenderSession

NOW = datetime(2024, 6, 14, 9, 30, tzinfo=UTC)

REGIONS = ("East", "West", "North", "South")
ROWS: list[dict[str, Any]] = [
    {"region": r, "revenue": rev, "cost": cost, "churn_rate": churn}
    for r, rev, cost, churn in zip(
        REGIONS,
        (10.0, 14.0, 8.0, 12.0),
        (1.0, 2.0, 3.0, 6.0),
        (0.02, 0.10, 0.04, 0.06),
        strict=True,
    )
]
MONTHS: list[dict[str, Any]] = [
    {"month": date(2024, m, 1), "revenue": 10.0 + m, "churn_rate": 0.10 - 0.01 * m}
    for m in range(1, 7)
]
PINNED_LINE = {"type": "line", "y": "churn_rate", "axis_y": {"position": "right"}}


def _bar(
    annotations: list[dict[str, Any]],
    rows: list[dict[str, Any]] = ROWS,
    datasets: dict[str, list[dict[str, Any]]] | None = None,
    **chart: Any,
) -> ResolvedBarChart:
    normalized = BarChart.model_validate(
        {
            "id": "rev",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "query_name": "q",
            "annotations": annotations,
            **chart,
        }
    )
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    resolved = resolve(
        normalized,
        rows,
        ctx,
        rendered_at=NOW,
        **({} if datasets is None else {"datasets": datasets}),
    )
    assert isinstance(resolved, ResolvedBarChart)
    return resolved


def _line(
    annotations: list[dict[str, Any]],
    rows: list[dict[str, Any]] = MONTHS,
    datasets: dict[str, list[dict[str, Any]]] | None = None,
    **chart: Any,
) -> ResolvedLineChart:
    normalized = LineChart.model_validate(
        {
            "id": "rev",
            "type": "line",
            "x": "month",
            "y": "revenue",
            "query_name": "q",
            "annotations": annotations,
            **chart,
        }
    )
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    resolved = resolve(
        normalized,
        rows,
        ctx,
        rendered_at=NOW,
        **({} if datasets is None else {"datasets": datasets}),
    )
    assert isinstance(resolved, ResolvedLineChart)
    return resolved


def _one(chart: ResolvedBarChart | ResolvedLineChart) -> ResolvedAnnotation:
    [item] = chart.annotations
    return item


def _y(annotation: ResolvedAnnotation) -> float:
    assert isinstance(annotation.y, ResolvedValueAnchor)
    assert isinstance(annotation.y.value, float)
    return annotation.y.value


# --- the scale -------------------------------------------------------------


def test_a_literal_on_a_pinned_layer_column_reads_that_layers_scale() -> None:
    chart = _bar(
        [{"type": "rule", "y": 0.05, "y_column": "churn_rate"}], layers=[PINNED_LINE]
    )
    rule = _one(chart)
    assert rule.y_axis == ResolvedLayerAxis(layer=0)
    assert _y(rule) == 0.05
    plain = _bar([], layers=[PINNED_LINE])
    assert chart.style.axis_y.tick_values == plain.style.axis_y.tick_values


def test_a_literal_on_a_base_column_keeps_the_base_scale_beside_layers() -> None:
    rule = _one(
        _bar([{"type": "rule", "y": 9, "y_column": "revenue"}], layers=[PINNED_LINE])
    )
    assert rule.y_axis == "base"


def test_a_literal_without_y_column_reads_the_base_scale() -> None:
    rule = _one(_bar([{"type": "rule", "y": 9}], layers=[PINNED_LINE]))
    assert rule.y_axis == "base"


def test_a_layer_sharing_the_base_scale_reads_the_base_scale() -> None:
    layer = {"type": "line", "y": "cost"}
    rule = _one(_bar([{"type": "rule", "y": 3, "y_column": "cost"}], layers=[layer]))
    assert rule.y_axis == "base"


def test_the_layer_index_counts_every_layer() -> None:
    layers = [
        {"type": "line", "y": "cost", "axis_y": {"position": "left"}},
        PINNED_LINE,
    ]
    rule = _one(
        _bar([{"type": "rule", "y": 0.05, "y_column": "churn_rate"}], layers=layers)
    )
    assert rule.y_axis == ResolvedLayerAxis(layer=1)


def test_a_layer_annotation_prints_its_value_in_the_layers_format() -> None:
    layer = {
        **PINNED_LINE,
        "axis_y": {"position": "right", "labels": {"format": "percent_whole"}},
    }
    rule = _one(
        _bar(
            [
                {
                    "type": "rule",
                    "y": 0.05,
                    "y_column": "churn_rate",
                    "text": "Target {{ value | format }}",
                }
            ],
            layers=[layer],
        )
    )
    assert rule.text == "Target 5%"


# --- the rows --------------------------------------------------------------


def test_an_average_on_a_multi_y_chart_computes_over_the_named_column() -> None:
    rule = _one(
        _bar(
            [{"type": "rule", "y": "avg()", "y_column": "cost"}],
            y=["revenue", "cost"],
        )
    )
    assert _y(rule) == pytest.approx(3.0)
    assert rule.y_axis == "base"


def test_an_average_over_a_layer_column_uses_the_layers_rows() -> None:
    rule = _one(
        _bar(
            [{"type": "rule", "y": "avg()", "y_column": "churn_rate"}],
            layers=[PINNED_LINE],
        )
    )
    assert _y(rule) == pytest.approx(0.055)
    assert rule.y_axis == ResolvedLayerAxis(layer=0)


def test_an_average_over_a_layer_with_its_own_query_uses_that_querys_rows() -> None:
    own = [{"region": r, "churn_rate": 0.5} for r in REGIONS]
    layer = {**PINNED_LINE, "query": "other"}
    rule = _one(
        _bar(
            [{"type": "rule", "y": "avg()", "y_column": "churn_rate"}],
            layers=[layer],
            datasets={"other": own},
        )
    )
    assert _y(rule) == pytest.approx(0.5)


def test_a_derived_note_on_a_layer_sits_at_that_columns_extreme() -> None:
    note = _one(
        _bar(
            [{"type": "note", "y": "max()", "y_column": "churn_rate"}],
            layers=[PINNED_LINE],
        )
    )
    assert note.x == ResolvedCategoryAnchor(kind="category", value="West")
    assert _y(note) == 0.10
    assert note.y_axis == ResolvedLayerAxis(layer=0)


def test_a_trend_on_a_layer_fits_that_columns_rows() -> None:
    chart = _line(
        [{"type": "trend", "fit": "linear()", "y_column": "churn_rate"}],
        layers=[PINNED_LINE],
    )
    trend = _one(chart)
    assert trend.trend is not None
    first, last = trend.trend.line[0][1], trend.trend.line[-1][1]
    assert last < first
    assert trend.y_axis == ResolvedLayerAxis(layer=0)
    base = _one(
        _line(
            [{"type": "trend", "fit": "linear()", "y_column": "revenue"}],
            layers=[PINNED_LINE],
        )
    )
    assert base.trend is not None
    assert base.trend.line[-1][1] > base.trend.line[0][1]
    assert base.y_axis == "base"


# --- errors ----------------------------------------------------------------


@pytest.mark.parametrize(
    "annotation",
    [
        {"type": "rule", "y": "avg()"},
        {"type": "note", "y": "max()"},
        {"type": "trend", "fit": "linear()"},
    ],
)
def test_a_computed_y_beside_layers_needs_y_column(
    annotation: dict[str, Any],
) -> None:
    rows = MONTHS
    with pytest.raises(CompilationError, match=r"set y_column.*revenue.*churn_rate"):
        _line([annotation], rows=rows, layers=[PINNED_LINE])


def test_a_computed_y_on_a_multi_y_chart_needs_y_column() -> None:
    with pytest.raises(CompilationError, match=r"set y_column.*revenue.*cost"):
        _bar([{"type": "rule", "y": "avg()"}], y=["revenue", "cost"])


def test_a_computed_y_on_a_single_y_chart_without_layers_needs_no_y_column() -> None:
    assert _y(_one(_bar([{"type": "rule", "y": "avg()"}]))) == pytest.approx(11.0)


def test_an_unknown_y_column_lists_the_plotted_columns() -> None:
    with pytest.raises(
        CompilationError, match=r"'nope'.*not a plotted y column.*revenue.*churn_rate"
    ):
        _bar([{"type": "rule", "y": 1, "y_column": "nope"}], layers=[PINNED_LINE])


def test_a_column_plotted_on_both_axes_is_ambiguous() -> None:
    layer = {
        "type": "line",
        "y": "revenue",
        "query": "other",
        "axis_y": {"position": "right"},
    }
    with pytest.raises(
        CompilationError, match=r"'revenue' is plotted by both y and layers\[0\]\.y"
    ):
        _bar(
            [{"type": "rule", "y": 1, "y_column": "revenue"}],
            layers=[layer],
            datasets={"other": ROWS},
        )


def test_a_band_with_only_an_x_span_takes_no_y_column() -> None:
    with pytest.raises(CompilationError, match="y_column.*no y anchor"):
        _line(
            [
                {
                    "type": "band",
                    "x": {"from": "2024-02-01", "to": "2024-04-01"},
                    "y_column": "revenue",
                }
            ]
        )


def test_a_frame_note_takes_no_y_column() -> None:
    with pytest.raises(CompilationError, match="y_column.*no y anchor"):
        _line([{"type": "note", "text": "Hi", "y_column": "revenue"}])


def test_a_vertical_rule_takes_no_y_column() -> None:
    with pytest.raises(CompilationError, match="y_column.*no y anchor"):
        _line([{"type": "rule", "x": "2024-03-01", "y_column": "revenue"}])


# --- drawing ---------------------------------------------------------------


def _svg(annotations: list[dict[str, Any]]) -> str:
    spec: dict[str, object] = {
        "type": "bar",
        "query": "q",
        "x": "region",
        "y": "revenue",
        "layers": [PINNED_LINE],
        "annotations": annotations,
    }
    style, ctx = resolve_style_and_context(get_theme_style(get_default_theme_name()))
    resolved = resolve(
        normalize_chart("c1", spec, {"q": ValuesQuery(rows=ROWS)}, sources={}),
        ROWS,
        chart_style_context=ctx,
        rendered_at=NOW,
    )
    svg = BoardRenderSession(style).render_svg_family(
        resolved, ROWS, width=600.0, height=300.0
    )
    assert svg is not None
    return svg


def _rule_y(svg: str) -> float:
    marks = re.search(r'<g class="annotations-marks"[^>]*>(.*?)</g></g>', svg)
    assert marks is not None
    [y1] = re.findall(r'<line x1="0" y1="([\d.]+)"', marks.group(1))
    return float(y1)


def test_the_engine_draws_a_layer_rule_on_the_layers_scale() -> None:
    on_layer = _rule_y(_svg([{"type": "rule", "y": 0.05, "y_column": "churn_rate"}]))
    on_base = _rule_y(_svg([{"type": "rule", "y": 0.05}]))
    # The layer's 0 to 10% axis puts 5% at mid-height of the 252px plot.
    assert on_layer == pytest.approx(126.0, abs=1.0)
    assert on_base > 250.0


def test_a_layer_rule_past_the_layers_data_widens_the_layers_axis() -> None:
    svg = _svg([{"type": "rule", "y": 0.30, "y_column": "churn_rate"}])
    assert "300m" in re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    assert _rule_y(svg) == pytest.approx(0.0, abs=0.5)


def test_a_gradient_colored_layer_takes_only_series_total() -> None:
    layer = {"type": "line", "y": "cost", "color": "churn_rate"}
    with pytest.raises(CompilationError, match="gradient"):
        _bar(
            [{"type": "rule", "y": "max()", "y_column": "cost", "series": "each"}],
            layers=[layer],
        )
    total = _bar(
        [{"type": "rule", "y": "max()", "y_column": "cost", "series": "total"}],
        layers=[layer],
    )
    assert _y(_one(total)) == 6.0


def _colored_layer_rule(series: str, rows: list[dict[str, Any]]) -> ResolvedBarChart:
    layer = {**PINNED_LINE, "color": "segment", "query": "other"}
    return _bar(
        [{"type": "rule", "y": "avg()", "y_column": "churn_rate", "series": series}],
        layers=[layer],
        datasets={"other": rows},
    )


def test_a_gradient_layer_color_has_no_series_to_split_by() -> None:
    rows = [
        {"region": r, "churn_rate": 0.1, "segment": i} for i, r in enumerate(REGIONS)
    ]
    with pytest.raises(CompilationError, match="is a gradient"):
        _colored_layer_rule("each", rows)


def test_an_empty_categorical_layer_under_series_each_is_a_gap_not_a_gradient() -> None:
    chart = _colored_layer_rule("each", [])
    assert chart.annotations == ()
    [gap] = chart.annotation_gaps
    assert "no plotted rows" in gap.reason


def test_a_circle_on_a_line_layer_clears_that_layers_points() -> None:
    layer = {**PINNED_LINE, "style": {"marks": {"point": {"size": 100}}}}
    note = {"type": "note", "x": "West", "y": "max()", "enclose": "circle"}
    on_layer = _one(_bar([{**note, "y_column": "churn_rate"}], layers=[layer]))
    on_base = _one(_bar([{**note, "y_column": "revenue"}], layers=[layer]))
    assert on_layer.style.circle is not None and on_base.style.circle is not None
    assert on_layer.style.circle.point_radius == 5.0
    assert on_layer.style.circle.radius == 9.0
    assert on_base.style.circle.point_radius == 0.0
    assert on_base.style.circle.radius == 6.0


MULTI_Y = {"rows": MONTHS, "x": "month", "y": ["revenue", "churn_rate"]}
MULTI_Y_TREND = {"type": "trend", "fit": "linear()", "y_column": "churn_rate"}


def test_a_y_column_trend_on_a_stacked_multi_y_chart_errors() -> None:
    with pytest.raises(CompilationError, match=r"stacked.*style"):
        _bar([MULTI_Y_TREND], **MULTI_Y, stack="zero")


def test_a_stacked_multi_y_trend_with_an_explicit_color_resolves() -> None:
    trend = {
        **MULTI_Y_TREND,
        "style": {"marks": {"line": {"stroke": {"color": "#112233"}}}},
    }
    assert _one(_bar([trend], **MULTI_Y, stack="zero")).trend is not None


def test_a_y_column_trend_on_an_unstacked_multi_y_chart_resolves() -> None:
    chart = _bar([MULTI_Y_TREND], **MULTI_Y, stack="none")
    assert _one(chart).trend is not None


def test_an_unfilled_scatter_layer_point_radius_includes_its_stroke() -> None:
    def radius(filled: bool) -> float | None:
        layer = {
            "type": "scatter",
            "y": "churn_rate",
            "axis_y": {"position": "right"},
            "style": {"marks": {"point": {"size": 100, "filled": filled}}},
        }
        note = {"type": "note", "x": "West", "y": "max()", "enclose": "circle"}
        item = _one(_bar([{**note, "y_column": "churn_rate"}], layers=[layer]))
        assert item.style.circle is not None
        return item.style.circle.point_radius

    assert radius(False) == pytest.approx(radius(True) + 2.5)
