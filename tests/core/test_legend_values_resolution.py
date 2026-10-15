"""End-to-end coverage for ``style.legend.values``: engine display order is
pinned by ``apply_legend_entry_order`` (``emitters/_channels.py``), the ONE
writer of ``encoding.color.legend.values``; an authored list is resolved
against the real domain and wins outright instead of being clobbered.

Families: stacked bar, wide bar, multi-series line, ``color:`` area, and a
field-colored overlay. An unresolvable entry never silently disappears,
but it never fails the render either: it is always dropped, and the
render-warnings detector (covered separately by
``tests/render/warnings/test_legend_values_unresolved.py``) is what
surfaces it to the author.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    wide_measure_labels_for,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

from ._engine_render import render_pie_svg
from ._svg_render import legend_label_order
from .conftest import chart_pane


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


_SERIES_ROWS = [
    {"month": "Jan", "series": "A", "value": 10},
    {"month": "Jan", "series": "B", "value": 20},
    {"month": "Feb", "series": "A", "value": 15},
    {"month": "Feb", "series": "B", "value": 25},
]


def _color_legend_values(spec: dict, *, overlay: bool = False) -> list[str] | None:
    pane = chart_pane(spec)
    if overlay:
        pane = pane["layer"][0]
    return pane["encoding"]["color"]["legend"]["values"]


def _render(make_chart, chart_type: str, style: dict, rows, **kwargs):
    theme = get_theme_style()
    context = resolve_chart_style_context(theme)
    chart = make_chart(chart_type, style=style, **kwargs)
    resolved = resolve(chart, rows, chart_style_context=context)
    artifact = render_resolved_chart(resolved, rows, resolve_style(theme))
    return artifact.payload


class TestAuthoredValuesSurvive:
    def test_stacked_bar(self, make_chart):
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "legend": {"values": ["B", "A"]},
                "endpoint_labels": {"visible": False},
            },
            _SERIES_ROWS,
            x="month",
            y="value",
            color="series",
        )
        assert _color_legend_values(spec) == ["B", "A"]

    def test_multi_series_line(self, make_chart):
        # Authors the OPPOSITE of the engine's own last-value order for
        # this data (["B", "A"], verified separately) -- a test asserting
        # the engine's own order would pass whether or not `authored` is
        # actually honored.
        spec = _render(
            make_chart,
            "line",
            {
                "legend": {"visible": True, "values": ["A", "B"]},
                "endpoint_labels": {"visible": False},
            },
            _SERIES_ROWS,
            x="month",
            y="value",
            color="series",
        )
        assert _color_legend_values(spec) == ["A", "B"]

    def test_multi_series_line_with_no_x_resolves(self, make_chart):
        # No chart.x: sorted_series_by_last_value can't run (it needs an x
        # to find each series' "last" point), so the domain falls back to
        # plain insertion order (`else series`) -- a line: color: chart
        # with no x is a real, if rare, authorable shape (ResolvedLineChart.
        # x is str | None), not dead code.
        rows = [
            {"series": "B", "value": 20},
            {"series": "A", "value": 10},
        ]
        spec = _render(
            make_chart,
            "line",
            {
                "legend": {"visible": True, "values": ["A", "B"]},
                "endpoint_labels": {"visible": False},
            },
            rows,
            x=None,
            y="value",
            color="series",
        )
        assert _color_legend_values(spec) == ["A", "B"]

    def test_color_area(self, make_chart):
        spec = _render(
            make_chart,
            "area",
            {
                "stack": "zero",
                "legend": {"visible": True, "values": ["B", "A"]},
                "endpoint_labels": {"visible": False},
            },
            _SERIES_ROWS,
            x="month",
            y="value",
            color="series",
        )
        assert _color_legend_values(spec) == ["B", "A"]

    def test_color_area_unstacked_with_no_x_resolves(self, make_chart):
        # No stack, no chart.x: _area_spatial_order returns an empty
        # `order` (no x/y anchor to spatially order by), so `resolve_order`
        # falls back to plain `series` -- a real, if rare, authorable shape
        # (ResolvedAreaChart.x is str | None), not dead code.
        rows = [
            {"series": "B", "value": 20},
            {"series": "A", "value": 10},
        ]
        spec = _render(
            make_chart,
            "area",
            {
                "legend": {"visible": True, "values": ["A", "B"]},
                "endpoint_labels": {"visible": False},
            },
            rows,
            x=None,
            y="value",
            color="series",
        )
        assert _color_legend_values(spec) == ["A", "B"]

    def test_wide_bar_raw_column_name(self, make_chart):
        rows = [
            {"month": "Jan", "net_revenue": 10, "gross_revenue": 20},
            {"month": "Feb", "net_revenue": 15, "gross_revenue": 25},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "legend": {"values": ["gross_revenue", "net_revenue"]},
                "endpoint_labels": {"visible": False},
            },
            rows,
            x="month",
            y=["net_revenue", "gross_revenue"],
        )
        # The raw column name resolves to its humanized label.
        assert _color_legend_values(spec) == ["gross revenue", "net revenue"]

    def test_wide_bar_resolves_even_with_an_empty_authored_palette(self, make_chart):
        # style.color.categorical.palette: [] is legal authored YAML
        # (CategoricalColorStyle has no non-empty validator) -- resolution
        # must not sit behind `if palette:`, or a raw measure name in
        # `values:` pins verbatim against the now-humanized domain instead
        # of resolving -- a phantom, unmatched swatch.
        rows = [
            {"month": "Jan", "actual_revenue": 10, "forecast_revenue": 20},
            {"month": "Feb", "actual_revenue": 15, "forecast_revenue": 25},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "color": {"categorical": {"palette": []}},
                "legend": {"values": ["actual_revenue"]},
                "endpoint_labels": {"visible": False},
            },
            rows,
            x="month",
            y=["actual_revenue", "forecast_revenue"],
        )
        assert _color_legend_values(spec) == ["actual revenue"]

    def test_wide_bar_humanized_label(self, make_chart):
        rows = [
            {"month": "Jan", "net_revenue": 10, "gross_revenue": 20},
            {"month": "Feb", "net_revenue": 15, "gross_revenue": 25},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "legend": {"values": ["gross revenue", "net revenue"]},
                "endpoint_labels": {"visible": False},
            },
            rows,
            x="month",
            y=["net_revenue", "gross_revenue"],
        )
        assert _color_legend_values(spec) == ["gross revenue", "net revenue"]

    def test_wide_bar_humanized_label_carries_the_unit_suffix(self, make_chart):
        """`net_revenue`/`gross_revenue` (the sibling test above) humanize
        identically whether `_label_expression` calls `field_title`
        or the bare `slug_to_text` it wraps -- neither has a unit suffix to
        drop. A `_usd` measure does: only this end-to-end path through the
        emitted VL `calculate` expression (not `slug_to_text`'s own unit
        tests elsewhere) proves the wide legend text actually carries it."""
        rows = [
            {"month": "Jan", "revenue_usd": 10, "units_sold": 2},
            {"month": "Feb", "revenue_usd": 15, "units_sold": 3},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "endpoint_labels": {"visible": False},
            },
            rows,
            x="month",
            y=["revenue_usd", "units_sold"],
        )
        assert _color_legend_values(spec) == ["units sold", "revenue ($)"]

    def test_wide_bar_with_dimension_raw_composite_resolves_to_humanized_form(
        self, make_chart
    ):
        """A wide chart authoring `color:` as a dimension crosses the
        measures with it into composite series. The alias map
        keys each (already-humanized) composite by its own RAW spelling
        too, so an authored `<dimension value> — <raw measure>` composite
        must resolve to the humanized composite the legend actually
        paints -- proven end to end through the emitter, not just the
        detector (which recomputes independently and cannot catch an
        emitter-side alias-map regression)."""
        rows = [
            {"month": "Jan", "region": "west", "revenue_usd": 10, "cost": 4},
            {"month": "Jan", "region": "east", "revenue_usd": 12, "cost": 5},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "legend": {"values": ["west - revenue_usd"]},
                "endpoint_labels": {"visible": False},
            },
            rows,
            x="month",
            y=["revenue_usd", "cost"],
            color="region",
        )
        assert _color_legend_values(spec) == ["west - revenue ($)"]

    def test_wide_bar_with_dimension_bare_measure_name_is_unmatched(self, make_chart):
        """The bare raw measure name alone ("revenue_usd", no dimension
        prefix) is genuinely ambiguous once a dimension is authored -- it
        could mean any dimension value's revenue_usd -- so it must NOT
        resolve as an alias, unlike the dimensionless wide chart where the
        bare measure name is the whole identity. An unmatched entry falls
        back to the full engine order rather than guessing which
        composite the author meant."""
        rows = [
            {"month": "Jan", "region": "west", "revenue_usd": 10, "cost": 4},
            {"month": "Jan", "region": "east", "revenue_usd": 12, "cost": 5},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "legend": {"values": ["revenue_usd"]},
                "endpoint_labels": {"visible": False},
            },
            rows,
            x="month",
            y=["revenue_usd", "cost"],
            color="region",
        )
        assert _color_legend_values(spec) == [
            "west - cost",
            "east - cost",
            "west - revenue ($)",
            "east - revenue ($)",
        ]

    def test_field_colored_overlay(self, make_chart):
        from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

        rows = [
            {"month": "Jan", "series": "A", "value": 10, "target": 30},
            {"month": "Jan", "series": "B", "value": 20, "target": 30},
            {"month": "Feb", "series": "A", "value": 15, "target": 32},
            {"month": "Feb", "series": "B", "value": 25, "target": 32},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "legend": {"values": ["B", "A", "target"]},
            },
            rows,
            x="month",
            y="value",
            color="series",
            layers=[LineLayer(type="line", y="target")],
        )
        assert _color_legend_values(spec, overlay=True) == ["B", "A", "target"]

    def test_field_colored_overlay_layer_legend_carries_no_raw_values(self, make_chart):
        """Regression: apply_color_legend ran on EVERY layer's own color
        encoding, including the added LineLayer's, copying the raw authored
        `legend.values` verbatim there too -- inert today only because
        Vega-Lite's legend merge takes the base layer's list, but it
        contradicts apply_legend_entry_order being the ONE writer of a
        resolved legend.values. Only the base (layer[0]) may carry one."""
        from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

        rows = [
            {"month": "Jan", "series": "A", "value": 10, "target": 30},
            {"month": "Feb", "series": "B", "value": 15, "target": 32},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "legend": {"values": ["B", "A", "target"]},
            },
            rows,
            x="month",
            y="value",
            color="series",
            layers=[LineLayer(type="line", y="target")],
        )
        pane = chart_pane(spec)
        assert "values" not in pane["layer"][1]["encoding"]["color"]["legend"]

    def test_single_series_base_overlay(self, make_chart):
        # No color: on the base -- exercises the `if base_label is not
        # None:` arm (a bar+line combo with no color field anywhere).
        # base_label defaults to field_title("value") == "value";
        # the layer label defaults to field_title("target").
        from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

        rows = [
            {"month": "Jan", "value": 10, "target": 30},
            {"month": "Feb", "value": 15, "target": 32},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "legend": {"visible": True, "values": ["target", "value"]},
                "endpoint_labels": {"visible": False},
            },
            rows,
            x="month",
            y="value",
            layers=[LineLayer(type="line", y="target")],
        )
        assert _color_legend_values(spec, overlay=True) == ["target", "value"]


class TestOtherFamiliesResolveToo:
    """Wide/overlay/bar-color aren't the only families with a legend --
    pie, scatter, heatmap and grouped (stack: none) bar must resolve an
    authored `legend.values` too, not write it through `legend_to_vl`
    verbatim (a grouped-bar author entry matching no real series must not
    paint a phantom swatch)."""

    def test_grouped_bar_resolves_case_fold_and_drops_bogus_entry(self, make_chart):
        rows = [
            {"cat": "x", "series": "A", "val": 1},
            {"cat": "x", "series": "B", "val": 2},
        ]
        # lowercase "b"/"a" fold-resolve; "TOTALLY_BOGUS" drops (WARN, no crash).
        chart = make_chart(
            "bar",
            x="cat",
            y="val",
            color="series",
            style={
                "orientation": "vertical",
                "stack": "none",
                "legend": {"values": ["b", "a", "TOTALLY_BOGUS"]},
            },
        )
        theme = get_theme_style()
        context = resolve_chart_style_context(theme)
        resolved = resolve(chart, rows, chart_style_context=context)
        spec = render_resolved_chart(resolved, rows, resolve_style(theme)).payload
        assert _color_legend_values(spec) == ["B", "A"]

    def test_pie_resolves_and_filters(self, make_chart):
        rows = [
            {"segment": "Direct", "revenue": 10},
            {"segment": "Organic", "revenue": 20},
            {"segment": "Paid", "revenue": 15},
        ]
        chart = make_chart(
            "pie",
            style={"legend": {"visible": True, "values": ["paid", "direct"]}},
            theta="revenue",
            color="segment",
        )
        assert legend_label_order(render_pie_svg(chart, rows)) == ["Paid", "Direct"]

    def test_scatter_resolves(self, make_chart):
        rows = [
            {"x_field": 1, "y_field": 2, "series": "A"},
            {"x_field": 2, "y_field": 3, "series": "B"},
        ]
        spec = _render(
            make_chart,
            "scatter",
            {"legend": {"values": ["b", "a"]}},
            rows,
            x="x_field",
            y="y_field",
            color="series",
        )
        assert _color_legend_values(spec) == ["B", "A"]

    def test_scatter_size_legend_ignores_an_authored_color_legend_values(
        self, make_chart
    ):
        """Regression: apply_color_legend(size_enc, chart.legend) passes
        the resolved legend verbatim, so an authored `values:` meant to
        reorder the categorical color legend baked onto the quantitative
        size legend (a gradient tick ladder) too -- drop_values=True keeps
        the size legend's own injected config, minus the categorical
        entries that don't apply to it."""
        rows = [
            {"x_field": 1, "y_field": 2, "series": "A", "weight": 10},
            {"x_field": 2, "y_field": 3, "series": "B", "weight": 20},
        ]
        spec = _render(
            make_chart,
            "scatter",
            {"legend": {"values": ["b", "a"]}},
            rows,
            x="x_field",
            y="y_field",
            color="series",
            size="weight",
        )
        size_legend = chart_pane(spec)["encoding"]["size"]["legend"]
        assert "values" not in (size_legend or {})

    def test_heatmap_resolves(self, make_chart):
        rows = [
            {"x_field": "Mon", "y_field": "AM", "series": "A"},
            {"x_field": "Tue", "y_field": "PM", "series": "B"},
        ]
        spec = _render(
            make_chart,
            "heatmap",
            {"legend": {"values": ["b", "a"]}},
            rows,
            x="x_field",
            y="y_field",
            color="series",
        )
        assert _color_legend_values(spec) == ["B", "A"]

    def test_geoshape_nominal_choropleth_resolves(self):
        from dbt_charts.core.compile.config import (
            get_default_theme_name,
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.normalized.geoshape import (
            GeoshapeChart,
        )
        from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
        from dbt_charts.core.compile.resolve.chart.geo import _resolve_geoshape
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )
        from dbt_charts.core.render.chart.emitters.geo import GeoshapeEmitter
        from dbt_charts.core.render.chart.spec import RenderBox
        from dbt_charts.core.render.chart.translate import translate_to_vl

        board_style = resolve_chart_style_context(
            get_theme_style(get_default_theme_name())
        )
        data = [
            {"state": "CA", "tier": "west"},
            {"state": "TX", "tier": "south"},
        ]
        # "WEST"/"SOUTH" (uppercase, reversed) must fold-resolve to the
        # real domain entries "west"/"south" in THAT authored order -- a
        # byte-verbatim copy would instead pin the literal uppercase
        # strings, which match nothing Vega actually enumerates for this
        # field.
        compiled = GeoshapeChart(
            id="geo1",
            type="geoshape",
            geo_source="fake-geo-source",
            lookup="state",
            value="tier",
            style={"legend": {"values": ["WEST", "SOUTH"]}},
        )
        resolved = _resolve_geoshape(compiled, data, board_style, 800.0, None)
        vl = translate_to_vl(
            GeoshapeEmitter().emit(
                resolved, RenderBox(width=600.0, height=300.0), regroup((), data)
            )
        )
        color_enc = vl["layer"][1]["encoding"]["color"]
        assert color_enc["legend"]["values"] == ["west", "south"]


class TestWideMeasureAllNullDoesNotRaise:
    def test_unstacked_wide_area_resolves_when_a_measure_is_all_null(self, make_chart):
        """Regression: unfold_wide_rows drops a row whose measure is None,
        so an all-null measure never enters _area_spatial_order's `series`/
        `order` for the unstacked (overlap) branch -- unlike wide bar/line,
        which always feed the full raw `measures` list to their own order
        helpers regardless of data. That incomplete list became the
        resolution DOMAIN, so an authored `values:` entry naming the
        all-null measure would otherwise fail to resolve even though it's
        a real, static, authored member of `y: [...]`."""
        rows = [
            {"month": "Jan", "revenue": 10, "cost": None},
            {"month": "Feb", "revenue": 15, "cost": None},
        ]
        spec = _render(
            make_chart,
            "area",
            {
                "stack": "none",
                "legend": {"visible": True, "values": ["revenue", "cost"]},
                "endpoint_labels": {"visible": False},
            },
            rows,
            x="month",
            y=["revenue", "cost"],
        )
        assert _color_legend_values(spec) == ["revenue", "cost"]

    def test_overlay_base_label_resolves_when_layer_color_column_is_all_null(
        self, make_chart
    ):
        """Regression: infer_vega_type_from_data returns "quantitative" for
        an all-null column (nulls are skipped, so all_numeric stays True by
        vacuous truth) -- a layer whose color column happens to be
        all-null in this render must resolve the same authored entry the
        same board resolves once that column has any non-null value, not
        divert into a different (unmatched) outcome just because this
        render's data shape changed the inferred VL type."""
        from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

        rows = [
            {"month": "Jan", "value": 10, "target": 5, "kind": None},
            {"month": "Feb", "value": 15, "target": 8, "kind": None},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "legend": {"visible": True, "values": ["Alpha", "Beta"]},
                "endpoint_labels": {"visible": False},
            },
            rows,
            x="month",
            y="value",
            layers=[LineLayer(type="line", y="target", color="kind", label="Alpha")],
        )
        assert _color_legend_values(spec, overlay=True) == ["Alpha"]


class TestWideMeasureLabelBakingKeepsRawSeriesIdentity:
    def test_alphabetical_stack_order_is_raw_derived_not_humanized_text(
        self, make_chart
    ):
        """Regression: a site re-deriving `field_title(measure)`
        got the sort order or timing wrong. The label is now baked once
        at resolve time (`_wide_fields.wide_measure_labels_for`) and every consumer
        reads it, never re-derives it -- this pins the one invariant that
        matters: `stack_order: alphabetical`'s baseline order sorts by
        the RAW measure name ("revenue_total" < "revenue_usd"), reversed
        for a vertical stack's display order -- not sorted by the
        already-humanized text ("revenue ($)" < "revenue total", since
        "(" sorts before letters), which would produce the opposite
        domain."""
        rows = [{"month": "Jan", "revenue_total": 30, "revenue_usd": 50}]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "stack_order": "alphabetical",
            },
            rows,
            x="month",
            y=["revenue_total", "revenue_usd"],
        )
        color_scale = chart_pane(spec)["encoding"]["color"]["scale"]
        assert color_scale["domain"] == ["revenue ($)", "revenue total"]


class TestWideMeasureHumanizationCollisionFallsBackToRawNames:
    def test_two_measures_that_humanize_identically_resolve_to_distinct_raw_names(
        self, make_chart
    ):
        """Two measures that humanize to the same label must not raise
        from resolve(): both colliding measures fall back to their own
        raw name as their label, so they stay distinct series -- folding
        them into one Vega series would be a wrong result that looks
        right. WARN_WIDE_MEASURE_LABEL_COLLISION fires instead, covered
        separately by tests/render/warnings/test_wide_measure_label_
        collision.py."""
        theme = get_theme_style()
        context = resolve_chart_style_context(theme)
        chart = make_chart(
            "bar",
            style={"orientation": "vertical", "stack": "zero"},
            x="month",
            y=["churn_pct", "churn_percent"],
        )
        rows = [{"month": "Jan", "churn_pct": 1, "churn_percent": 2}]
        resolved = resolve(chart, rows, chart_style_context=context)
        assert wide_measure_labels_for(
            resolved.wide_measures, resolved.field_labels
        ) == {
            "churn_pct": "churn_pct",
            "churn_percent": "churn_percent",
        }

        artifact = render_resolved_chart(resolved, rows, resolve_style(theme))
        color_scale = chart_pane(artifact.payload)["encoding"]["color"]["scale"]
        assert set(color_scale["domain"]) == {"churn_pct", "churn_percent"}

    def test_a_non_colliding_measure_in_the_same_y_list_keeps_its_humanized_label(
        self, make_chart
    ):
        """Only the colliding group falls back to raw names -- a third,
        non-colliding measure in the same y: list keeps its humanized
        label."""
        theme = get_theme_style()
        context = resolve_chart_style_context(theme)
        chart = make_chart(
            "bar",
            style={"orientation": "vertical", "stack": "zero"},
            x="month",
            y=["churn_pct", "churn_percent", "revenue_usd"],
        )
        rows = [
            {
                "month": "Jan",
                "churn_pct": 1,
                "churn_percent": 2,
                "revenue_usd": 3,
            }
        ]
        resolved = resolve(chart, rows, chart_style_context=context)
        assert wide_measure_labels_for(
            resolved.wide_measures, resolved.field_labels
        ) == {
            "churn_pct": "churn_pct",
            "churn_percent": "churn_percent",
            "revenue_usd": "revenue ($)",
        }

    def test_a_second_order_collision_through_the_raw_fallback_itself_falls_back_too(
        self, make_chart
    ):
        """Regression: the raw-name fallback is only collision-free if a
        raw name can never equal another measure's humanized label --
        false here. ``orders (Count)`` and ``Orders (Count)`` both
        humanize (lowercased) to ``orders (count)`` and fall back to
        their own raw names; but ``orders_cnt`` independently humanizes
        to ``orders (Count)`` -- which is now IDENTICAL to the first
        measure's raw-name fallback. A single fallback pass would leave
        two distinct measures both labeled ``orders (Count)``, exactly
        the silent-fold bug the whole mechanism exists to prevent. The
        fix must keep resolving until every measure's label is unique,
        cascading the fallback to ``orders_cnt`` too."""
        theme = get_theme_style()
        context = resolve_chart_style_context(theme)
        chart = make_chart(
            "bar",
            style={"orientation": "vertical", "stack": "zero"},
            x="month",
            y=["orders (Count)", "Orders (Count)", "orders_cnt"],
        )
        rows = [
            {
                "month": "Jan",
                "orders (Count)": 1,
                "Orders (Count)": 2,
                "orders_cnt": 3,
            }
        ]
        resolved = resolve(chart, rows, chart_style_context=context)
        labels = wide_measure_labels_for(resolved.wide_measures, resolved.field_labels)
        assert len(set(labels.values())) == 3, labels


class TestEmptyPaletteDoesNotSkipResolution:
    """Resolution must run at the five sites covered here (bar
    histogram/stacked-vertical/stacked-horizontal, line, area) even when
    `style.color.categorical.palette: []` is authored (legal YAML --
    CategoricalColorStyle has no non-empty validator) -- an empty
    palette governs only the color scale's RANGE, never the legend's own
    entry set. Authors lowercase spellings against an UPPERCASE real
    domain so a raw, unresolved pass-through (apply_color_legend copies
    `legend.values` verbatim) is distinguishable from the real,
    fold-resolved series spelling."""

    _EMPTY_PALETTE = {"color": {"categorical": {"palette": []}}}

    def test_histogram(self, make_chart):
        rows = [
            {"value": 1, "series": "A"},
            {"value": 2, "series": "B"},
        ]
        spec = _render(
            make_chart,
            "histogram",
            {**self._EMPTY_PALETTE, "legend": {"visible": True, "values": ["b", "a"]}},
            rows,
            x="value",
            y=None,
            color="series",
        )
        assert _color_legend_values(spec) == ["B", "A"]

    def test_stacked_vertical_bar(self, make_chart):
        spec = _render(
            make_chart,
            "bar",
            {
                **self._EMPTY_PALETTE,
                "orientation": "vertical",
                "stack": "zero",
                "legend": {"values": ["b", "a"]},
                "endpoint_labels": {"visible": False},
            },
            _SERIES_ROWS,
            x="month",
            y="value",
            color="series",
        )
        assert _color_legend_values(spec) == ["B", "A"]

    def test_stacked_horizontal_bar(self, make_chart):
        spec = _render(
            make_chart,
            "bar",
            {
                **self._EMPTY_PALETTE,
                "orientation": "horizontal",
                "stack": "zero",
                "legend": {"values": ["b", "a"]},
                "endpoint_labels": {"visible": False},
            },
            _SERIES_ROWS,
            x="month",
            y="value",
            color="series",
        )
        assert _color_legend_values(spec) == ["B", "A"]

    def test_multi_series_line(self, make_chart):
        spec = _render(
            make_chart,
            "line",
            {
                **self._EMPTY_PALETTE,
                "legend": {"visible": True, "values": ["b", "a"]},
                "endpoint_labels": {"visible": False},
            },
            _SERIES_ROWS,
            x="month",
            y="value",
            color="series",
        )
        assert _color_legend_values(spec) == ["B", "A"]

    def test_color_area(self, make_chart):
        spec = _render(
            make_chart,
            "area",
            {
                **self._EMPTY_PALETTE,
                "stack": "zero",
                "legend": {"visible": True, "values": ["b", "a"]},
                "endpoint_labels": {"visible": False},
            },
            _SERIES_ROWS,
            x="month",
            y="value",
            color="series",
        )
        assert _color_legend_values(spec) == ["B", "A"]

    def test_multi_series_line_unauthored_gets_no_pin(self, make_chart):
        """Regression: apply_legend_entry_order always writes an explicit
        `legend.values` when called, even with authored=None (engine
        order, for the paint scale it just emitted to agree with). With an
        empty palette there is no scale to agree with and nothing authored
        to resolve, so calling it at all pins a last-value-order
        `legend.values` on a chart that authored no opinion -- an
        unrelated data change can silently reshuffle Vega's own legend
        order underneath it."""
        spec = _render(
            make_chart,
            "line",
            {**self._EMPTY_PALETTE, "legend": {"visible": True}},
            _SERIES_ROWS,
            x="month",
            y="value",
            color="series",
        )
        assert "values" not in chart_pane(spec)["encoding"]["color"]["legend"]


class TestDashesDomainSurvivesNonStringColorValues:
    def test_line_with_int_color_and_dashes_does_not_raise(self, make_chart) -> None:
        """Regression: style.dashes routes the color field's RAW domain
        (line.py's _distinct_in_order, no str() cast) into
        apply_legend_entry_order. resolve_legend_entries folds every domain
        entry through re.sub, so an int/date column crashed with TypeError
        and took the whole board down -- breaking apply_legend_entry_order's
        never-raises contract."""
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        compiled = get_theme_style("stark")
        line = compiled.charts.line.model_copy(
            update={
                "endpoint_labels": compiled.charts.line.endpoint_labels.model_copy(
                    update={"visible": False}
                )
            }
        )
        legend = compiled.charts.legend.model_copy(update={"visible": True})
        charts = compiled.charts.model_copy(
            update={"dashes": [[4, 4], [8, 8]], "line": line, "legend": legend}
        )
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        rows = [
            {"month": "Jan", "revenue": 100, "year": 2024},
            {"month": "Feb", "revenue": 120, "year": 2024},
            {"month": "Jan", "revenue": 80, "year": 2025},
            {"month": "Feb", "revenue": 90, "year": 2025},
        ]
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            color="year",
            style={"legend": {"values": ["2024"]}},
        )

        spec = generate_vega_lite_spec(
            chart, data=rows, board_style=board_rs, chart_style_context=board_ctx
        )

        assert _color_legend_values(spec) == ["2024"]

    def test_dashes_resolved_entry_keeps_the_raw_domain_type(self, make_chart) -> None:
        """Regression: infer_vega_type_from_data samples only the first 10
        rows, while _distinct_in_order (dash_domain) scans all of them --
        the nominal/ordinal gate can pass on an all-string sample even
        though a later row's dash_field value is a different type (an
        int). apply_legend_entry_order resolves against
        `[str(v) for v in dash_domain]` (folding needs strings), but
        writing that STRING list straight into `legend["values"]` leaves
        it disagreeing in TYPE with `color_scale["domain"]` (still the
        raw, mixed-type `dash_domain`) -- an authored `values: ["99"]`
        must resolve back to the raw int `99`, matching the domain, not
        the string "99" the detector's own domain never carries either."""
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        compiled = get_theme_style("stark")
        line = compiled.charts.line.model_copy(
            update={
                "endpoint_labels": compiled.charts.line.endpoint_labels.model_copy(
                    update={"visible": False}
                )
            }
        )
        legend = compiled.charts.legend.model_copy(update={"visible": True})
        charts = compiled.charts.model_copy(
            update={"dashes": [[4, 4], [8, 8]], "line": line, "legend": legend}
        )
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        # First 10 rows are all-string ("A") so infer_vega_type_from_data's
        # 10-row sample infers nominal; the 11th row's int (99) never
        # reaches that sample, but _distinct_in_order's full scan sees it.
        rows = [{"month": f"m{i}", "revenue": 100, "kind": "A"} for i in range(10)]
        rows.append({"month": "m10", "revenue": 120, "kind": 99})
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            color="kind",
            style={"legend": {"values": ["99"]}},
        )

        spec = generate_vega_lite_spec(
            chart, data=rows, board_style=board_rs, chart_style_context=board_ctx
        )
        color_enc = chart_pane(spec)["encoding"]["color"]
        assert color_enc["legend"]["values"] == [99]
        assert color_enc["scale"]["domain"] == ["A", 99]


class TestOrdinalColorResolves:
    def test_ordinal_date_like_color_drops_unresolved_entry(self, make_chart):
        """Regression: infer_vega_type_from_data returns "ordinal" for a
        date-like category string ("2024-Q1"), but every plain-chart
        resolution gate checked `enc["type"] == "nominal"` only -- an
        ordinal color: column skipped resolution entirely, so
        apply_color_legend's verbatim copy shipped a phantom swatch (no
        matching series) with no diagnostic."""
        rows = [
            {"month": "Jan", "cohort": "2024-Q1", "value": 10},
            {"month": "Jan", "cohort": "2024-Q2", "value": 20},
        ]
        spec = _render(
            make_chart,
            "bar",
            {"legend": {"visible": True, "values": ["2024-Q1", "NOT_A_COHORT"]}},
            rows,
            x="month",
            y="value",
            color="cohort",
        )
        assert _color_legend_values(spec) == ["2024-Q1"]

    def test_unauthored_ordinal_color_emits_no_collateral(self, make_chart):
        """Regression: widening the resolution-call gate from "nominal"
        to ("nominal", "ordinal") widened the whole ENCLOSING BRANCH at
        several sites, not just the resolve_legend_entries call -- an
        unauthored ordinal color: (its own inherent order, e.g. quarters)
        started emitting a paint scale, an explicit legend.values pin, and
        a sorted_series_by_stack_order mark-order transform it never should
        have, so Vega stopped ordering the domain chronologically and
        instead stacked/legended by magnitude. Pins the contract: nothing
        authored means nothing extra."""
        rows = [
            {"region": "east", "quarter": "2024-Q1", "amount": 10},
            {"region": "east", "quarter": "2024-Q2", "amount": 90},
            {"region": "west", "quarter": "2024-Q1", "amount": 15},
            {"region": "west", "quarter": "2024-Q2", "amount": 95},
        ]
        spec = _render(
            make_chart,
            "bar",
            {"stack": "zero", "endpoint_labels": {"visible": False}},
            rows,
            x="region",
            y="amount",
            color="quarter",
        )
        color_enc = chart_pane(spec)["encoding"]["color"]
        assert color_enc.get("type") == "ordinal"
        assert color_enc.get("scale") is None
        assert (color_enc.get("legend") or {}).get("values") is None
        assert chart_pane(spec)["encoding"].get("order") is None

    def test_authored_ordinal_color_resolves_without_reordering(self, make_chart):
        """The companion case: an AUTHORED entry on the same ordinal
        column still has to resolve, but resolving must not reintroduce
        the paint scale or order transform an unauthored board never
        had."""
        rows = [
            {"region": "east", "quarter": "2024-Q1", "amount": 10},
            {"region": "east", "quarter": "2024-Q2", "amount": 90},
            {"region": "west", "quarter": "2024-Q1", "amount": 15},
            {"region": "west", "quarter": "2024-Q2", "amount": 95},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "stack": "zero",
                "endpoint_labels": {"visible": False},
                "legend": {"visible": True, "values": ["2024-Q1", "bogus"]},
            },
            rows,
            x="region",
            y="amount",
            color="quarter",
        )
        color_enc = chart_pane(spec)["encoding"]["color"]
        assert color_enc.get("scale") is None
        assert color_enc["legend"]["values"] == ["2024-Q1"]
        assert chart_pane(spec)["encoding"].get("order") is None


class TestUnresolvedValuesDropWithoutRaising:
    """An unresolvable entry never fails the render on any family --
    dropped, with the full engine order used as the legend's fallback
    (never an empty legend). tests/render/warnings/test_legend_values_
    unresolved.py covers the WARN diagnostic that surfaces the drop."""

    def test_wide_bar_falls_back_to_engine_order(self, make_chart):
        rows = [{"month": "Jan", "net_revenue": 10, "gross_revenue": 20}]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "legend": {"values": ["not_a_measure"]},
                "endpoint_labels": {"visible": False},
            },
            rows,
            x="month",
            y=["net_revenue", "gross_revenue"],
        )
        assert _color_legend_values(spec) == ["net revenue", "gross revenue"]

    def test_single_series_base_overlay_falls_back_to_engine_order(self, make_chart):
        # No color: on the base -- this is the base_label arm (base label
        # + layer labels, no query data at all for a colorless layer).
        from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

        rows = [
            {"month": "Jan", "value": 10, "target": 30},
            {"month": "Feb", "value": 15, "target": 32},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "legend": {"visible": True, "values": ["nope"]},
                "endpoint_labels": {"visible": False},
            },
            rows,
            x="month",
            y="value",
            layers=[LineLayer(type="line", y="target")],
        )
        assert _color_legend_values(spec, overlay=True) == ["value", "target"]

    def test_colorless_overlay_with_non_quantitative_base_y_keeps_the_authored_entry(
        self, make_chart
    ):
        """A colorless overlay layer's own legend must keep an authored
        `values` entry when the base y is non-quantitative (scatter is
        the one family whose y is genuinely data-inferred) and no shared
        color scale gets built."""
        from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

        rows = [
            {"cat": "a", "target": 30},
            {"cat": "b", "target": 32},
        ]
        spec = _render(
            make_chart,
            "scatter",
            {
                "legend": {"visible": True, "values": ["target"]},
            },
            rows,
            x="cat",
            y="cat",
            layers=[LineLayer(type="line", y="target")],
        )
        layer_color = chart_pane(spec)["layer"][1]["encoding"]["color"]
        assert layer_color["legend"]["values"] == ["target"]

    def test_single_series_base_with_field_colored_layer_does_not_raise(
        self, make_chart
    ):
        # base_label arm (no color: on the base), but the LAYER itself
        # binds `color: kind` -- scale_domain now mixes the author-derived
        # base label with the layer's own distinct_series_values. "Gamma"
        # is dropped here -- tests/render/warnings/test_legend_values_
        # unresolved.py covers the detector actually surfacing that drop.
        from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

        rows = [
            {"month": "Jan", "value": 10, "target": 5, "kind": "Alpha"},
            {"month": "Feb", "value": 12, "target": 6, "kind": "Beta"},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "legend": {"visible": True, "values": ["Gamma", "value"]},
                "endpoint_labels": {"visible": False},
            },
            rows,
            x="month",
            y="value",
            layers=[LineLayer(type="line", y="target", color="kind")],
        )
        assert _color_legend_values(spec, overlay=True) == ["value"]

    def test_field_colored_overlay_unresolved_entry_does_not_raise(self, make_chart):
        # scale_domain here is base_series (query data) plus layer labels.
        # The unresolved entry falls back to the full engine order instead
        # of an empty legend (WARN_LEGEND_VALUES_UNRESOLVED covers the
        # diagnostic separately, see tests/render/warnings/
        # test_legend_values_unresolved.py).
        from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

        rows = [
            {"month": "Jan", "series": "A", "value": 10, "target": 30},
            {"month": "Jan", "series": "B", "value": 20, "target": 30},
        ]
        spec = _render(
            make_chart,
            "bar",
            {
                "orientation": "vertical",
                "stack": "zero",
                "legend": {"values": ["nope"]},
            },
            rows,
            x="month",
            y="value",
            color="series",
            layers=[LineLayer(type="line", y="target")],
        )
        assert _color_legend_values(spec, overlay=True) == ["A", "B", "target"]


class TestWideEndpointLabelColorMatchesMainScale:
    """Regression: a wide line/unstacked-area chart's endpoint-label rail
    painted a series in the WRONG series' ink when field_title
    (non order-preserving -- a `_usd` suffix injects `(` before the sort
    even sees the letters) was applied BEFORE sorting in the rail, while
    the main chart's own palette assignment sorts the RAW names first, then
    humanizes (emitters/_wide.py's fold_wide_measures)."""

    def test_wide_line_endpoint_label_color_matches_main_scale(self, make_chart):
        from dbt_charts.core.render.chart.emitters._cartesian import (
            companion_color_for_fill,
        )

        theme = get_theme_style()
        context = resolve_chart_style_context(theme)
        rows = [
            {"month": "Jan", "revenue_target": 10, "revenue_usd": 30},
            {"month": "Feb", "revenue_target": 20, "revenue_usd": 40},
        ]
        chart = make_chart(
            "line",
            x="month",
            y=["revenue_target", "revenue_usd"],
            style={"endpoint_labels": {"visible": True}},
        )
        resolved = resolve(chart, rows, chart_style_context=context)
        artifact = render_resolved_chart(resolved, rows, resolve_style(theme))
        spec = artifact.payload
        main_color = spec["hconcat"][0]["encoding"]["color"]
        main_by_series = dict(
            zip(
                main_color["scale"]["domain"], main_color["scale"]["range"], strict=True
            )
        )
        label_pane = spec["hconcat"][1]
        label_color = label_pane["encoding"]["color"]
        label_by_series = dict(
            zip(
                label_color["scale"]["domain"],
                label_color["scale"]["range"],
                strict=True,
            )
        )
        assert main_by_series.keys() == label_by_series.keys()
        dark_companion_palette = resolved.style.series_label.dark_companion_palette
        for series, main_fill in main_by_series.items():
            expected = companion_color_for_fill(
                main_fill, resolved.palette, dark_companion_palette
            )
            assert label_by_series[series] == expected, (
                f"{series!r} painted {main_fill!r} on the chart, expected the "
                f"companion ink {expected!r} on its endpoint label, got "
                f"{label_by_series[series]!r}"
            )
