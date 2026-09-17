"""Tests for the TOO_MANY_COLOR_CATEGORIES render-warning detector.

See ``too_many_color_categories.py``'s module docstring for the detection
rule. A quantitative (gradient) color never trips it.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    Chart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.style.authored.bar import BarChartStylePatch
from dbt_charts.core.compile.models.style.authored.line import LineChartStylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.diagnostics import WARN_TOO_MANY_COLOR_CATEGORIES, Diagnostic
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart
from dbt_charts.core.render.warnings import (
    WarningContext,
    too_many_color_categories as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _rows(n: int) -> list[dict[str, Any]]:
    return [{"x": i, "val": i, "series": f"s{i}"} for i in range(n)]


def _make_ctx(
    chart: Chart, rows: list[dict[str, Any]], color_type: str
) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={resolved.id: {"encoding": {"color": {"type": color_type}}}},
    )


def test_fires_above_threshold_on_nominal_color() -> None:
    chart = LineChart(
        id="c1", type="line", query_name="q", x="x", y="val", color="series"
    )
    palette_size = len(make_test_resolved_chart(chart).palette)
    warnings = detector.detect(_make_ctx(chart, _rows(palette_size + 1), "nominal"))
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_TOO_MANY_COLOR_CATEGORIES.code
    assert w.field == "series"
    assert str(palette_size + 1) in w.message
    assert w.fix is not None


def test_no_fire_at_threshold() -> None:
    chart = LineChart(
        id="c1", type="line", query_name="q", x="x", y="val", color="series"
    )
    palette_size = len(make_test_resolved_chart(chart).palette)
    assert detector.detect(_make_ctx(chart, _rows(palette_size), "nominal")) == []


def test_no_fire_on_quantitative_color() -> None:
    """A continuous color gradient is not a categorical legend."""
    chart = ScatterChart(
        id="c1", type="scatter", query_name="q", x="x", y="val", color="series"
    )
    assert detector.detect(_make_ctx(chart, _rows(40), "quantitative")) == []


def test_no_fire_without_color() -> None:
    chart = BarChart(id="c1", type="bar", query_name="q", x="x", y="val")
    assert detector.detect(_make_ctx(chart, _rows(40), "nominal")) == []


def _wide_rows(lists: int) -> list[dict[str, Any]]:
    return [
        {"x": i, "list": f"l{j}", "a": i, "b": i, "c": i}
        for i in range(3)
        for j in range(lists)
    ]


def test_fires_on_wide_chart_whose_composite_series_exceed_the_palette() -> None:
    """The series a wide chart renders are its fold's composites (measures ×
    dimension), which no raw row carries — counting must see the unfolded rows."""
    chart = BarChart(
        id="c1", type="bar", query_name="q", x="x", y=["a", "b", "c"], color="list"
    )
    warnings = detector.detect(_make_ctx(chart, _wide_rows(5), "nominal"))
    assert len(warnings) == 1
    assert "15" in warnings[0].message


def test_fires_on_wide_chart_without_color_and_names_y() -> None:
    palette_size = len(
        make_test_resolved_chart(
            BarChart(id="c1", type="bar", query_name="q", x="x", y=["a"])
        ).palette
    )
    measures = [f"m{i:02d}" for i in range(palette_size + 1)]
    chart = BarChart(id="c1", type="bar", query_name="q", x="x", y=measures)
    rows = [{"x": i, **dict.fromkeys(measures, i)} for i in range(3)]
    warnings = detector.detect(_make_ctx(chart, rows, "nominal"))
    assert len(warnings) == 1
    assert warnings[0].field == "y"
    assert warnings[0].path == "charts.c1.y"


def test_no_fire_on_wide_chart_within_the_palette() -> None:
    chart = BarChart(
        id="c1", type="bar", query_name="q", x="x", y=["a", "b", "c"], color="list"
    )
    palette_size = len(make_test_resolved_chart(chart).palette)
    lists_within_palette = palette_size // 3
    assert (
        detector.detect(_make_ctx(chart, _wide_rows(lists_within_palette), "nominal"))
        == []
    )


def test_fires_when_series_exceed_a_short_authored_palette() -> None:
    """A 6-color palette recycles colors at 7 series."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="x",
        y="val",
        color="series",
        style=LineChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": "hero-6"}}}
        ),
    )
    warnings = detector.detect(_make_ctx(chart, _rows(7), "nominal"))
    assert len(warnings) == 1
    assert warnings[0].message == (
        "Chart 'c1': color encoding on 'series' yields 7 distinct series; the "
        "palette only has 6 distinct colors before recycling."
    )


def test_no_fire_when_series_fit_a_short_authored_palette() -> None:
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="x",
        y="val",
        color="series",
        style=LineChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": "hero-6"}}}
        ),
    )
    assert detector.detect(_make_ctx(chart, _rows(6), "nominal")) == []


def test_no_fire_with_an_empty_authored_palette() -> None:
    """`palette: []` is a legal authored value -- the emitters skip the color
    scale entirely rather than recycling from zero colors, so the detector
    must not report a collision that never happens."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="x",
        y="val",
        color="series",
        style=LineChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": []}}}
        ),
    )
    assert detector.detect(_make_ctx(chart, _rows(2), "nominal")) == []


def test_fires_on_a_real_stacked_bar_spec() -> None:
    """Renders through the production pipeline (``render_resolved_chart``)
    instead of the ``_make_ctx`` stub every other test in this file uses."""
    rows = [{"month": "Jan", "region": f"R{i}", "v": 5} for i in range(1, 8)]
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="v",
        color="region",
        style=BarChartStylePatch.model_validate(
            # stack: zero is load-bearing: it is what makes this chart's spec
            # a vconcat root rather than a flat {encoding, layer} one.
            {"stack": "zero", "color": {"categorical": {"palette": "hero-6"}}}
        ),
    )
    resolved = make_test_resolved_chart(chart, rows, width=600)
    spec = render_resolved_chart(
        resolved, rows, resolve_style(get_theme_style())
    ).payload
    assert isinstance(spec, dict)
    assert "vconcat" in spec
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={resolved.id: spec},
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].message == (
        "Chart 'c1': color encoding on 'region' yields 7 distinct series; the "
        "palette only has 6 distinct colors before recycling."
    )


def test_no_fire_on_a_real_line_chart_with_quantitative_color() -> None:
    """The default endpoint-label rail hardcodes a nominal color type on its
    own text mark regardless of the chart's real color type, so a naive walk
    over every mark unit false-fires here."""
    chart = LineChart(
        id="c1", type="line", query_name="q", x="month", y="v", color="yr"
    )
    palette_size = len(make_test_resolved_chart(chart).palette)
    rows = [{"month": "Jan", "yr": 2000 + i, "v": 5} for i in range(palette_size + 1)]
    resolved = make_test_resolved_chart(chart, rows, width=600)
    spec = render_resolved_chart(
        resolved, rows, resolve_style(get_theme_style())
    ).payload
    assert isinstance(spec, dict)
    # Proves the rail is actually present, not merely absent (which would
    # also make the assertion below pass, for the wrong reason).
    assert "hconcat" in spec
    assert any(
        unit.get("mark") == "text" or (unit.get("mark") or {}).get("type") == "text"
        for unit in spec["hconcat"]
    )
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={resolved.id: spec},
    )
    assert detector.detect(ctx) == []


def test_fires_on_wide_scatter_without_color_and_names_y() -> None:
    """Same as ``test_fires_on_wide_chart_without_color_and_names_y``, for
    scatter's own wide fold -- scatter joined the wide-measures shape this
    detector already covers for bar/area/line."""
    palette_size = len(
        make_test_resolved_chart(
            ScatterChart(id="c1", type="scatter", query_name="q", x="x", y=["a"])
        ).palette
    )
    measures = [f"m{i:02d}" for i in range(palette_size + 1)]
    chart = ScatterChart(id="c1", type="scatter", query_name="q", x="x", y=measures)
    rows = [{"x": i, **dict.fromkeys(measures, i)} for i in range(3)]
    warnings = detector.detect(_make_ctx(chart, rows, "nominal"))
    assert len(warnings) == 1
    assert warnings[0].field == "y"
    assert warnings[0].path == "charts.c1.y"
