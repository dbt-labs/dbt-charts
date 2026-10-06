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
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedLineChart,
)
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


def _collect_color_legends(node: Any) -> list[Any]:
    """Recursively collect all 'legend' values from color encodings."""
    legends: list[Any] = []
    if isinstance(node, dict):
        if "color" in node and isinstance(node["color"], dict):
            color = node["color"]
            if "legend" in color:
                legends.append(color["legend"])
        for v in node.values():
            legends.extend(_collect_color_legends(v))
    elif isinstance(node, list):
        for item in node:
            legends.extend(_collect_color_legends(item))
    return legends


def _find_field_titles(node: Any, field_name: str) -> list[Any]:
    """Recursively collect 'title' values from any encoding channel whose
    'field' matches field_name — orientation/vconcat-agnostic (a wide chart's
    measure axis lands on VL x or y depending on auto-detected orientation)."""
    titles: list[Any] = []
    if isinstance(node, dict):
        if node.get("field") == field_name and "title" in node:
            titles.append(node["title"])
        for v in node.values():
            titles.extend(_find_field_titles(v, field_name))
    elif isinstance(node, list):
        for item in node:
            titles.extend(_find_field_titles(item, field_name))
    return titles


def _find_fold_lists(node: Any) -> list[list[Any]]:
    """Recursively collect every VL {"fold": [...]} transform's measure list.

    Structure-agnostic (vconcat/hconcat/layer) — a wide chart's fold
    transform can land at any nesting depth depending on orientation/
    endpoint-label wrapping."""
    folds: list[list[Any]] = []
    if isinstance(node, dict):
        if isinstance(node.get("fold"), list):
            folds.append(node["fold"])
        for v in node.values():
            folds.extend(_find_fold_lists(v))
    elif isinstance(node, list):
        for item in node:
            folds.extend(_find_fold_lists(item))
    return folds


# ---------------------------------------------------------------------------
# Bar: multi-y endpoint labels (Step 1 TDD -- fails before implementation)
# ---------------------------------------------------------------------------


class TestMultiYBarEndpointLabels:
    def test_multi_y_bar_endpoint_labels_fires_rail(self, make_chart, model_copy_at):
        """Multi-y bar with endpoint_labels.visible:true fires the endpoint-label rail.

        x="month" is a string column so the bar resolves to horizontal orientation
        (categorical x → horizontal per _bar_orientation). Horizontal stacked bars
        with a color series use the top_rail layout (vconcat). After normalization,
        multi-y bar injects a synthetic color channel so the ordinary series path
        fires for horizontal too.
        """
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig

        chart = make_chart("bar", x="month", y=["rev", "cost"], stack="zero")
        data = _multi_measure_bar_data()
        seed = model_copy_at(
            get_theme_style("stark"),
            "charts.bar.endpoint_labels",
            EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
        )
        ctx = resolve_chart_style_context(seed)
        rc = resolve(chart, data, chart_style_context=ctx)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload
        assert "vconcat" in spec, (
            "Multi-y horizontal stacked bar with endpoint_labels.visible:true should fire "
            "the endpoint-label top_rail (vconcat). "
            f"Top-level keys: {list(spec)}"
        )

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


class TestMultiYAxisTitle:
    """A wide chart's measure axis has no single field to derive a title
    from (it's a list) — the title must join the humanized measure names,
    same as wide_measures_title, not go blank.

    The measure field lands on VL x or y depending on auto-detected
    orientation (bar), so assertions search for the WIDE_VALUE_FIELD encoding
    wherever it landed rather than assuming a channel or top-level shape."""

    def test_multi_y_bar_axis_title_joins_measure_names(
        self, make_chart, model_copy_at
    ):
        from dbt_charts.core.render.chart.emitters._cartesian import (
            wide_measures_title,
        )

        chart = make_chart("bar", x="month", y=["rev", "cost"], stack="zero")
        data = _multi_measure_bar_data()
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        assert isinstance(rc, ResolvedBarChart)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload
        expected = wide_measures_title(("rev", "cost"), {})
        titles = _find_field_titles(spec, "__dbt_charts_wide_value__")
        assert titles == [expected], titles

    def test_multi_y_area_axis_title_joins_measure_names(
        self, make_chart, model_copy_at
    ):
        from dbt_charts.core.render.chart.emitters._cartesian import (
            wide_measures_title,
        )

        chart = make_chart("area", x="date", y=["rev", "cost"])
        data = _multi_measure_line_data()
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        assert isinstance(rc, ResolvedAreaChart)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload
        expected = wide_measures_title(("rev", "cost"), {})
        titles = _find_field_titles(spec, "__dbt_charts_wide_value__")
        assert titles == [expected], titles

    def test_multi_y_line_axis_title_joins_measure_names(
        self, make_chart, model_copy_at
    ):
        from dbt_charts.core.render.chart.emitters._cartesian import (
            wide_measures_title,
        )

        chart = make_chart("line", x="date", y=["rev", "cost"])
        data = _multi_measure_line_data()
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        assert isinstance(rc, ResolvedLineChart)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload
        expected = wide_measures_title(("rev", "cost"), {})
        titles = _find_field_titles(spec, "__dbt_charts_wide_value__")
        assert titles == [expected], titles

    def test_multi_y_bar_axis_title_respects_authored_y_label(
        self, make_chart, model_copy_at
    ):
        """An explicit y_label always wins over the derived join."""
        chart = make_chart(
            "bar", x="month", y=["rev", "cost"], stack="zero", y_label="Totals"
        )
        data = _multi_measure_bar_data()
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload
        titles = _find_field_titles(spec, "__dbt_charts_wide_value__")
        assert titles == ["Totals"], titles


class TestMultiYFoldRowOrder:
    """The VL `fold` transform's measure list controls the POST-FOLD row
    sequence, which is also the mark PAINT order whenever there's no
    explicit VL `order` encoding (grouped bar, unstacked area) — this
    theme's bars are wider than their band (deliberate edge overlap), so a
    mismatched paint order is visibly different, not just cosmetic. The
    fold list must follow the same computed display order as the color
    domain/legend, not the raw authored `y:` list order — otherwise a wide
    chart's z-order can end up the OPPOSITE of what an equivalent authored
    `color:` chart on the same data produces."""

    def test_multi_y_bar_fold_order_matches_color_domain_not_authored_order(
        self, make_chart, model_copy_at
    ):
        # rev/cost authored in non-alphabetical order -- alphabetical
        # (cost, rev) is what bar's grouped no-stack path pins as both the
        # color domain and (via gap-fill) long-form's own row order.
        chart = make_chart("bar", x="month", y=["rev", "cost"])
        data = _multi_measure_bar_data()
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload
        folds = _find_fold_lists(spec)
        assert folds == [["cost", "rev"]], folds

    def test_multi_y_area_fold_order_matches_computed_display_order(
        self, make_chart, model_copy_at
    ):
        # Unstacked area orders by last-value (sorted_series_by_last_value via
        # _area_spatial_order), not authored order -- rev's last value (5)
        # ranks it AFTER cost's (50) here, diverging from the authored
        # y: [rev, cost] list order, so this data actually exercises the fix
        # (a fixture where the two orders coincide would pass either way).
        chart = make_chart("area", x="date", y=["rev", "cost"])
        data = [
            {"date": "2024-01-01", "rev": 10, "cost": 1},
            {"date": "2024-02-01", "rev": 20, "cost": 2},
            {"date": "2024-03-01", "rev": 5, "cost": 50},
        ]
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload
        folds = _find_fold_lists(spec)
        assert folds == [["cost", "rev"]], folds


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_multi_y_stacked_bar_baseline_measure_receives_first_palette_slot(
    make_chart: Any, orientation: str
) -> None:
    data = _multi_measure_bar_data()
    chart = make_chart(
        "bar",
        x="month",
        y=["rev", "cost"],
        stack="zero",
        style={"orientation": orientation},
    )
    rc = resolve(chart, data, chart_style_context=_BOARD_CTX)
    assert isinstance(rc, ResolvedBarChart)
    spec = render_resolved_chart(rc, data, _BOARD_STYLE, width=400).payload
    pane = spec["hconcat"][0] if "hconcat" in spec else spec.get("vconcat", [spec])[-1]
    color = pane["encoding"]["color"]
    color_by_measure = dict(
        zip(color["scale"]["domain"], color["scale"]["range"], strict=True)
    )

    assert color_by_measure["rev"] == rc.palette[0]
    assert color_by_measure["cost"] == rc.palette[1]


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

    def test_multi_y_scatter_renders_one_folded_mark_with_color(self, make_chart):
        """A rendered multi-y scatter spec carries the VL fold transform and a
        real color encoding -- one folded point mark, not per-measure layers."""
        from dbt_charts.core.compile.resolve.chart._wide_fields import WIDE_LABEL_FIELD

        chart = make_chart("scatter", x="month", y=["rev", "cost"])
        data = _multi_measure_scatter_data()
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload
        folds = _find_fold_lists(spec)
        assert folds == [["cost", "rev"]], folds

        def _color_fields(node: Any) -> list[Any]:
            found: list[Any] = []
            if isinstance(node, dict):
                color = node.get("color")
                if isinstance(color, dict) and "field" in color:
                    found.append(color["field"])
                for v in node.values():
                    found.extend(_color_fields(v))
            elif isinstance(node, list):
                for item in node:
                    found.extend(_color_fields(item))
            return found

        color_fields = _color_fields(spec)
        assert color_fields and all(f == WIDE_LABEL_FIELD for f in color_fields), (
            f"Expected every color encoding bound to {WIDE_LABEL_FIELD!r} on "
            f"the folded scatter mark, got: {color_fields}"
        )

    def test_multi_y_scatter_tooltip_does_not_leak_synthetic_field_names(
        self, make_chart
    ):
        """The rendered tooltip names real fields (x, the folded measure
        value, the series label) with real titles -- not the raw
        WIDE_VALUE_FIELD/WIDE_LABEL_FIELD sentinel names VL's default
        per-mark tooltip would otherwise fall back to (wide.color's own
        encoding-level title is deliberately null so it doesn't leak into
        the legend heading)."""
        from dbt_charts.core.compile.resolve.chart._wide_fields import (
            WIDE_LABEL_FIELD,
            WIDE_VALUE_FIELD,
        )

        chart = make_chart("scatter", x="month", y=["rev", "cost"])
        data = _multi_measure_scatter_data()
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload

        tooltip = spec["encoding"]["tooltip"]
        assert [entry["field"] for entry in tooltip] == [
            "month",
            WIDE_VALUE_FIELD,
            WIDE_LABEL_FIELD,
        ]
        assert tooltip[0]["title"] == "month"
        assert tooltip[1]["title"] == "rev, cost"
        assert tooltip[2]["title"] == "Series"

    def test_multi_y_scatter_value_labels_render_per_point(self, make_chart):
        """point_mark.labels.visible on a wide scatter still emits a text
        label layer -- _apply_scatter (value_labels.py) has no wide_measures
        early-return the way bar/line/area do (each folded point already
        carries its own real position, so a label built off the synthetic
        WIDE_VALUE_FIELD is correctly positioned per point)."""
        chart = make_chart(
            "scatter",
            x="month",
            y=["rev", "cost"],
            style={"marks": {"point": {"labels": {"visible": True}}}},
        )
        data = _multi_measure_scatter_data()
        ctx = resolve_chart_style_context(get_theme_style("stark"))
        rc = resolve(chart, data, chart_style_context=ctx)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload
        text_layers = [
            lay
            for lay in spec.get("layer", [])
            if lay.get("mark", {}).get("type") == "text"
        ]
        assert text_layers, (
            f"Expected a text label layer, got layers: {spec.get('layer')}"
        )
        assert (
            text_layers[0]["encoding"]["y"]["field"] == "__dbt_charts_wide_value__"
        ), (
            f"Expected the label positioned off the folded value field, got: {text_layers[0]}"
        )

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
    def test_multi_y_line_legend_visible_false_respected(
        self, make_chart, model_copy_at
    ):
        """style.legend.visible: false is respected for multi-y line.

        Currently _emit_multi_metric_line hard-codes datum-driven per-layer
        color and never applies board legend style, so legend.visible: false
        is silently ignored. After normalization, the ordinary fold path wires
        legend suppression correctly.
        """
        chart = make_chart("line", x="date", y=["rev", "cost"])
        data = _multi_measure_line_data()
        seed = model_copy_at(
            get_theme_style("stark"),
            "charts.legend.visible",
            False,
        )
        ctx = resolve_chart_style_context(seed)
        rc = resolve(chart, data, chart_style_context=ctx)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload

        legends = _collect_color_legends(spec)
        # legend.visible: false => VL color encoding gets "legend": null
        assert any(leg is None for leg in legends), (
            f"Expected a color encoding with legend: null (hidden), got: {legends}. "
            "Multi-y line is not respecting style.legend.visible: false."
        )

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


class TestMultiYLineEndpointLabels:
    def test_multi_y_line_endpoint_labels_fires_rail(self, make_chart, model_copy_at):
        """Multi-y line with endpoint_labels.visible:true fires the hconcat rail.

        Currently the endpoint-label rail never fires for multi-y line.
        """
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig

        chart = make_chart("line", x="date", y=["rev", "cost"])
        data = _multi_measure_line_data()
        seed = model_copy_at(
            get_theme_style("stark"),
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
        )
        ctx = resolve_chart_style_context(seed)
        rc = resolve(chart, data, chart_style_context=ctx)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
        spec = artifact.payload
        assert "hconcat" in spec, (
            "Multi-y line with endpoint_labels.visible:true should fire "
            "the endpoint-label rail (hconcat). "
            f"Top-level keys: {list(spec)}"
        )


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
