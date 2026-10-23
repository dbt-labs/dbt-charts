"""TDD tests for resolve-time multi-y normalization (bar, area, line).

These tests are written BEFORE implementation and confirm bugs that the
feature fixes:
- endpoint labels never fire for multi-y bar (currently broken)
- legend title is hardcoded 'Series' in _wide.py (currently a bug)
- multi-y line ignores style.legend.visible: false (currently a bug)
- multi-y line/bar silently drops a gradient color: / layers: (should error)
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style("stark"))


def _multi_measure_bar_data() -> list[dict[str, Any]]:
    return [
        {"month": "Jan", "rev": 100, "cost": 40},
        {"month": "Feb", "rev": 200, "cost": 90},
        {"month": "Mar", "rev": 150, "cost": 70},
    ]


def _multi_measure_line_data() -> list[dict[str, Any]]:
    return [
        {"date": "2024-01-01", "rev": 100, "cost": 40},
        {"date": "2024-02-01", "rev": 200, "cost": 90},
        {"date": "2024-03-01", "rev": 150, "cost": 70},
    ]


def _collect_color_titles(node: Any) -> list[Any]:
    """Recursively collect all title values from color encodings."""
    titles: list[Any] = []
    if isinstance(node, dict):
        if "color" in node and isinstance(node["color"], dict):
            color = node["color"]
            if "title" in color:
                titles.append(color["title"])
        for v in node.values():
            titles.extend(_collect_color_titles(v))
    elif isinstance(node, list):
        for item in node:
            titles.extend(_collect_color_titles(item))
    return titles


# ---------------------------------------------------------------------------
# Bar: multi-y endpoint labels (Step 1 TDD -- fails before implementation)
# ---------------------------------------------------------------------------


class TestMultiYBarEndpointLabels:
    def test_multi_y_bar_legend_title_not_hardcoded_series(
        self, make_chart, model_copy_at
    ):
        """Legend color title for multi-y bar is not the literal string 'Series'.

        _wide.py currently hardcodes title='Series' on the fold color encoding.
        After the fix, title should be None (no title) rather than 'Series'.
        """
        chart = make_chart("bar", x="month", y=["rev", "cost"], stack="zero")
        data = _multi_measure_bar_data()
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload

        titles = _collect_color_titles(spec)
        assert "Series" not in titles, (
            f"Found hardcoded 'Series' title in color encoding: {titles}. "
            "The fold-generated color title must not be 'Series'."
        )

    def test_multi_y_area_legend_title_not_hardcoded_series(
        self, make_chart, model_copy_at
    ):
        """Legend color title for multi-y area is not the literal string 'Series'."""
        chart = make_chart("area", x="date", y=["rev", "cost"])
        data = _multi_measure_line_data()
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload

        titles = _collect_color_titles(spec)
        assert "Series" not in titles, (
            f"Found hardcoded 'Series' title in color encoding: {titles}. "
            "The fold-generated color title must not be 'Series'."
        )


# ---------------------------------------------------------------------------
# Bar: multi-y with color: or layers: raises at resolve time
# ---------------------------------------------------------------------------


class TestMultiYBarValidation:
    def test_multi_y_bar_with_gradient_color_raises(self, make_chart):
        """y: [a, b] + a gradient color: raises at resolve time — a series
        column crosses the measures into composite series, a gradient names
        no series (test_wide_measures_with_dimension.py covers the column case)."""
        from dbt_charts.core.compile.errors import CompilationError

        chart = make_chart(
            "bar",
            x="month",
            y=["rev", "cost"],
            color="rev",
            style={"color": {"gradient": {"palette": ["#ffffff", "#0000ff"]}}},
        )
        data = [{"month": "Jan", "rev": 100, "cost": 40, "category": "A"}]
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        with pytest.raises(CompilationError):
            resolve(chart, data, chart_style_context=ctx)

    def test_multi_y_bar_with_layers_raises(self, make_chart):
        """y: [a, b] + layers: raises CompilationError at resolve time.

        Currently this silently ignores the layers.
        """
        from dbt_charts.core.compile.errors import CompilationError

        chart = make_chart(
            "bar",
            x="month",
            y=["rev", "cost"],
            layers=[{"y": "extra", "type": "line"}],
        )
        data = [{"month": "Jan", "rev": 100, "cost": 40, "extra": 10}]
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        with pytest.raises(CompilationError):
            resolve(chart, data, chart_style_context=ctx)


def _multi_measure_scatter_data() -> list[dict[str, Any]]:
    return [
        {"month": "Jan", "rev": 100, "cost": 40},
        {"month": "Feb", "rev": 200, "cost": 90},
        {"month": "Mar", "rev": 150, "cost": 70},
    ]


class TestMultiYScatter:
    def test_multi_y_scatter_resolves_wide_measures_and_color_channel(self, make_chart):
        """y: [a, b] on scatter resolves like bar/area/line: wide_measures is
        baked and a synthetic series-color channel is injected, instead of
        raising the (now-deregistered) unsupported-chart-type error."""
        from dbt_charts.core.compile.models.chart.resolved.scatter import (
            ResolvedScatterChart,
        )

        chart = make_chart("scatter", x="month", y=["rev", "cost"])
        data = _multi_measure_scatter_data()
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        assert isinstance(rc, ResolvedScatterChart)
        assert rc.wide_measures == ("rev", "cost")
        color_ch = rc.resolved_channels.get("color")
        assert color_ch is not None
        assert color_ch.mode == "series"

    def test_multi_y_scatter_with_non_numeric_measure_raises(self, make_chart):
        """y: [a, b] with a non-numeric measure raises ERR-SCATTER-MULTI-Y-NOT-NUMERIC
        instead of silently baking a NaN axis with zero marks.

        A single (non-list) y: may legitimately be non-numeric (the dot-plot
        recipe) -- this guard is scoped to the list case only, mirroring
        bar's/line's own _first_non_numeric_y check.
        """
        from dbt_charts.core.compile.errors import CompilationError
        from dbt_charts.core.diagnostics import ERR_SCATTER_MULTI_Y_NOT_NUMERIC

        chart = make_chart("scatter", x="month", y=["grade", "cost"])
        data = [{"month": "Jan", "grade": "A", "cost": 40}]
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        with pytest.raises(CompilationError) as exc:
            resolve(chart, data, chart_style_context=ctx)
        assert exc.value.code == ERR_SCATTER_MULTI_Y_NOT_NUMERIC

    def test_multi_y_scatter_with_gradient_color_raises(self, make_chart):
        """y: [a, b] + a gradient color: raises via the shared resolver
        conflict check -- same as bar/area/line."""
        from dbt_charts.core.compile.errors import CompilationError
        from dbt_charts.core.diagnostics import ERR_MULTI_Y_COLOR_CONFLICT

        chart = make_chart(
            "scatter",
            x="month",
            y=["rev", "cost"],
            color="rev",
            style={"color": {"gradient": {"palette": ["#ffffff", "#0000ff"]}}},
        )
        data = [{"month": "Jan", "rev": 100, "cost": 40, "category": "A"}]
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        with pytest.raises(CompilationError) as exc:
            resolve(chart, data, chart_style_context=ctx)
        assert exc.value.code == ERR_MULTI_Y_COLOR_CONFLICT

    def test_multi_y_scatter_with_layers_raises(self, make_chart):
        """y: [a, b] + layers: raises via the shared resolver conflict check."""
        from dbt_charts.core.compile.errors import CompilationError
        from dbt_charts.core.diagnostics import ERR_MULTI_Y_LAYERS_CONFLICT

        chart = make_chart(
            "scatter",
            x="month",
            y=["rev", "cost"],
            layers=[{"y": "extra", "type": "line"}],
        )
        data = [{"month": "Jan", "rev": 100, "cost": 40, "extra": 10}]
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        with pytest.raises(CompilationError) as exc:
            resolve(chart, data, chart_style_context=ctx)
        assert exc.value.code == ERR_MULTI_Y_LAYERS_CONFLICT

    def test_multi_y_scatter_click_link_fields(self, make_chart):
        """The click-interactivity y/color link fields for a wide scatter --
        the first authored measure for y, WIDE_KEY_FIELD for color with no
        dimension -- same convention bar/area/line already use for their own
        wide fold (``click_interactivity.py::_channel_field``)."""
        from dbt_charts.core.compile.resolve.chart._wide_fields import WIDE_KEY_FIELD
        from dbt_charts.core.render.chart.features.click_interactivity import (
            _channel_field,
        )

        chart = make_chart("scatter", x="month", y=["rev", "cost"])
        data = _multi_measure_scatter_data()
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        assert _channel_field(rc, "y") == "rev"
        assert _channel_field(rc, "color") == WIDE_KEY_FIELD

        dimensioned = make_chart(
            "scatter", x="month", y=["rev", "cost"], color="region"
        )
        dim_data = [{"month": "Jan", "rev": 100, "cost": 40, "region": "west"}]
        rc_dim = resolve(dimensioned, dim_data, chart_style_context=ctx)
        assert _channel_field(rc_dim, "color") == "region"


# ---------------------------------------------------------------------------
# Line: multi-y legend.visible: false respected (Step 6 TDD)
# ---------------------------------------------------------------------------


class TestMultiYLineLegend:
    def test_multi_y_line_with_gradient_color_raises(self, make_chart):
        """y: [a, b] + a gradient color: raises for line, same as bar."""
        from dbt_charts.core.compile.errors import CompilationError

        chart = make_chart(
            "line",
            x="date",
            y=["rev", "cost"],
            color="rev",
            style={"color": {"gradient": {"palette": ["#ffffff", "#0000ff"]}}},
        )
        data = [{"date": "2024-01-01", "rev": 100, "cost": 40, "category": "A"}]
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        with pytest.raises(CompilationError):
            resolve(chart, data, chart_style_context=ctx)

    def test_multi_y_line_with_layers_raises(self, make_chart):
        """y: [a, b] + layers: raises for line (currently silently dropped)."""
        from dbt_charts.core.compile.errors import CompilationError

        chart = make_chart(
            "line",
            x="date",
            y=["rev", "cost"],
            layers=[{"y": "extra", "type": "bar"}],
        )
        data = [{"date": "2024-01-01", "rev": 100, "cost": 40, "extra": 10}]
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        with pytest.raises(CompilationError):
            resolve(chart, data, chart_style_context=ctx)


class TestLayeredLineListYBehaviorChange:
    def test_layered_line_list_y_with_layers_raises(self, make_chart):
        """Layered line with y: list + layers: raises CompilationError.

        Replaces old behavior where the combination silently rendered without
        the layers. After normalization, the conflict is rejected early.
        """
        from dbt_charts.core.compile.errors import CompilationError

        chart = make_chart(
            "line",
            x="month",
            y=["rev", "cost2"],
            layers=[{"y": "cost", "type": "bar"}],
        )
        data = [
            {"month": "Jan", "rev": 100, "cost2": 40, "cost": 40},
            {"month": "Feb", "rev": 200, "cost2": 90, "cost": 90},
        ]
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        with pytest.raises(CompilationError):
            resolve(chart, data, chart_style_context=ctx)


class TestHistogramListY:
    def test_histogram_with_list_y_resolves_without_error(self, make_chart):
        """histogram + y: [a, b] must resolve without ValidationError.

        Histograms use aggregate-count on y; the authored y value is ignored.
        _resolve_histogram sets y=None rather than forwarding the list, which
        would violate the ResolvedBarChart.y: str | None constraint.
        """
        from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart

        chart = make_chart("histogram", x="amount", y=["a", "b"])
        data = [{"amount": 1}, {"amount": 2}]
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        assert isinstance(rc, ResolvedBarChart)
        assert rc.y is None
