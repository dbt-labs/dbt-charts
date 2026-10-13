"""Tests for the LEGEND_VALUES_UNRESOLVED render-warning detector.

Detection rule: fires on ANY family whose authored `style.legend.values`
names an entry that doesn't resolve against the chart's legend domain --
a plain `color: <col>` chart, a wide `y: [...]` chart, an overlay's
shared color scale (field-colored or a pure author-derived base/layer
label), and a nominal geoshape choropleth. An unresolved entry is always
dropped, never a hard failure, so this is the only diagnostic for the
concern. DATA-dependent: recomputes the domain from ``ctx.chart_results``
independently of anything the emitter already resolved into
``ctx.vega_specs``.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.diagnostics import WARN_LEGEND_VALUES_UNRESOLVED, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    legend_values_unresolved as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart

_ROWS: list[dict[str, Any]] = [
    {"cat": "a", "series": "A", "val": 1},
    {"cat": "a", "series": "B", "val": 2},
]


def _bar(**kwargs: Any) -> BarChart:
    return BarChart(
        **{
            "id": "c1",
            "type": "bar",
            "query_name": "q",
            "x": "cat",
            "y": "val",
            "color": "series",
            **kwargs,
        }
    )


def _ctx(chart: Any, rows: list[dict[str, Any]] = _ROWS) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={},
    )


def test_fires_when_an_authored_entry_matches_no_series() -> None:
    chart = _bar(
        style=BarChartStylePatch.model_validate({"legend": {"values": ["A", "C"]}})
    )
    warnings = detector.detect(_ctx(chart))
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_LEGEND_VALUES_UNRESOLVED.code
    assert w.chart == "c1"
    assert "'C'" in w.message
    assert "A" in w.message and "B" in w.message


def test_silent_when_every_authored_entry_resolves() -> None:
    chart = _bar(
        style=BarChartStylePatch.model_validate({"legend": {"values": ["B", "A"]}})
    )
    assert detector.detect(_ctx(chart)) == []


def test_silent_when_nothing_authored() -> None:
    chart = _bar()
    assert detector.detect(_ctx(chart)) == []


def test_fires_on_wide_chart_when_an_entry_matches_no_measure() -> None:
    """A wide y: [...] chart's legend domain is static config (the
    measure list itself, no data dependence). An unresolved entry there
    is dropped, not a hard failure the emitter catches on its own, so
    this detector must cover it or a dropped entry ships silently."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="cat",
        y=["named", "non_named"],
        style=BarChartStylePatch.model_validate({"legend": {"values": ["nope"]}}),
    )
    rows = [{"cat": "a", "named": 1, "non_named": 2}]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "nope" in warnings[0].message


def test_silent_on_wide_chart_when_every_entry_resolves() -> None:
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="cat",
        y=["named", "non_named"],
        style=BarChartStylePatch.model_validate(
            {"legend": {"values": ["Non Named", "named"]}}
        ),
    )
    rows = [{"cat": "a", "named": 1, "non_named": 2}]
    assert detector.detect(_ctx(chart, rows)) == []


def test_fires_on_wide_chart_with_a_dimension_when_a_composite_matches_nothing() -> (
    None
):
    """Regression: a wide y: [...] chart authoring color: as a dimension
    (the measures cross with it into composite
    series) drops an unresolved composite the same as any other family.
    Covers the new composite domain, not just the plain measure-list
    shape the class above already pins."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="cat",
        y=["messages", "fixes"],
        color="list",
        style=BarChartStylePatch.model_validate(
            {"legend": {"values": ["bugs - messages", "nope"]}}
        ),
    )
    rows = [
        {"cat": "a", "list": "bugs", "messages": 1, "fixes": 2},
        {"cat": "a", "list": "chores", "messages": 3, "fixes": 4},
    ]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "nope" in warnings[0].message


def test_silent_on_wide_chart_with_a_dimension_when_every_composite_resolves() -> None:
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="cat",
        y=["messages", "fixes"],
        color="list",
        style=BarChartStylePatch.model_validate(
            {
                "legend": {
                    "values": [
                        "bugs - messages",
                        "bugs - fixes",
                        "chores - messages",
                        "chores - fixes",
                    ]
                }
            }
        ),
    )
    rows = [
        {"cat": "a", "list": "bugs", "messages": 1, "fixes": 2},
        {"cat": "a", "list": "chores", "messages": 3, "fixes": 4},
    ]
    assert detector.detect(_ctx(chart, rows)) == []


def test_fires_on_layered_field_colored_base_when_entry_matches_nothing() -> None:
    """The emitter never raises for an unresolved entry on any family, so
    this detector is the only diagnostic for it."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    chart = _bar(
        style=BarChartStylePatch.model_validate(
            {"legend": {"values": ["A", "B", "TOTALLY_BOGUS"]}}
        ),
        layers=[LineLayer(type="line", y="target")],
    )
    rows = [
        {"cat": "a", "series": "A", "val": 1, "target": 5},
        {"cat": "a", "series": "B", "val": 2, "target": 5},
    ]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "TOTALLY_BOGUS" in warnings[0].message
    assert "'A'" not in warnings[0].message
    assert "'B'" not in warnings[0].message


def test_layered_field_colored_base_resolves_a_layer_label_by_y_column_name() -> None:
    """The layer's own label ("Target", field_title("target")) can
    be named by its raw y-column name too, same as the base_label arm's
    alias."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    chart = _bar(
        style=BarChartStylePatch.model_validate(
            {"legend": {"values": ["A", "B", "target"]}}
        ),
        layers=[LineLayer(type="line", y="target")],
    )
    rows = [
        {"cat": "a", "series": "A", "val": 1, "target": 5},
        {"cat": "a", "series": "B", "val": 2, "target": 5},
    ]
    assert detector.detect(_ctx(chart, rows)) == []


def _pie(**kwargs: Any) -> Any:
    from dbt_charts.core.compile.models.chart.normalized import PieChart

    return PieChart(
        **{
            "id": "c1",
            "type": "pie",
            "query_name": "q",
            "theta": "val",
            "color": "series",
            **kwargs,
        }
    )


def _scatter(**kwargs: Any) -> Any:
    from dbt_charts.core.compile.models.chart.normalized import ScatterChart

    return ScatterChart(
        **{
            "id": "c1",
            "type": "scatter",
            "query_name": "q",
            "x": "cat",
            "y": "val",
            "color": "series",
            **kwargs,
        }
    )


def _heatmap(**kwargs: Any) -> Any:
    from dbt_charts.core.compile.models.chart.normalized import HeatmapChart

    return HeatmapChart(
        **{
            "id": "c1",
            "type": "heatmap",
            "query_name": "q",
            "x": "cat",
            "y": "cat2",
            "color": "series",
            **kwargs,
        }
    )


_HEATMAP_ROWS: list[dict[str, Any]] = [
    {"cat": "a", "cat2": "x", "series": "A"},
    {"cat": "a", "cat2": "y", "series": "B"},
]


def test_fires_on_pie_when_an_entry_matches_no_series() -> None:
    from dbt_charts.core.compile.models.style.authored import PieChartStylePatch

    chart = _pie(
        style=PieChartStylePatch.model_validate(
            {"legend": {"visible": True, "values": ["A", "bogus"]}}
        )
    )
    warnings = detector.detect(_ctx(chart))
    assert len(warnings) == 1
    assert "bogus" in warnings[0].message


def test_fires_on_scatter_when_an_entry_matches_no_series() -> None:
    from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch

    chart = _scatter(
        style=ScatterChartStylePatch.model_validate(
            {"legend": {"visible": True, "values": ["A", "bogus"]}}
        )
    )
    warnings = detector.detect(_ctx(chart))
    assert len(warnings) == 1
    assert "bogus" in warnings[0].message


def test_fires_on_wide_scatter_when_an_entry_matches_no_measure() -> None:
    """Same as ``test_fires_on_wide_chart_when_an_entry_matches_no_measure``,
    for scatter's own wide fold -- scatter joined the wide-measures shape
    this detector already covers for bar/area/line.

    Asserts the real resolved domain (``named, non named``), not just that
    something fired -- see the sibling silent test below for why that
    distinction matters here.
    """
    from dbt_charts.core.compile.models.chart.normalized import ScatterChart
    from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch

    chart = ScatterChart(
        id="c1",
        type="scatter",
        query_name="q",
        x="cat",
        y=["named", "non_named"],
        style=ScatterChartStylePatch.model_validate({"legend": {"values": ["nope"]}}),
    )
    rows = [{"cat": "a", "named": 1, "non_named": 2}]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "nope" in warnings[0].message
    assert "named, non named" in warnings[0].message


def test_silent_on_wide_scatter_when_every_entry_resolves() -> None:
    """The discriminating half of the pair above: an authored
    ``legend.values`` that matches every real measure must stay silent --
    the domain check above is only meaningful if a *correctly* resolved
    domain can also clear it."""
    from dbt_charts.core.compile.models.chart.normalized import ScatterChart
    from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch

    chart = ScatterChart(
        id="c1",
        type="scatter",
        query_name="q",
        x="cat",
        y=["named", "non_named"],
        style=ScatterChartStylePatch.model_validate(
            {"legend": {"values": ["non named", "named"]}}
        ),
    )
    rows = [{"cat": "a", "named": 1, "non_named": 2}]
    assert detector.detect(_ctx(chart, rows)) == []


def test_fires_on_field_colored_overlay_with_non_quantitative_base_y() -> None:
    """_overlay.py's use_shared_scale -- and therefore whether a
    colorless layer's label reaches the shared scale_domain at all --
    requires the base's y to resolve quantitative. Scatter's y can
    legitimately be non-numeric (a rotated dot plot); on a
    non-quantitative base y the emitter never adds the layer's label to
    scale_domain, so an authored entry naming it must not silently
    resolve. The detector must gate a colorless layer's label
    contribution on the same base-y check, not add it unconditionally."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch

    chart = _scatter(
        y="cat",  # non-numeric base y: infers nominal, not quantitative
        style=ScatterChartStylePatch.model_validate(
            {"legend": {"values": ["A", "B", "target"]}}
        ),
        layers=[LineLayer(type="line", y="target")],
    )
    rows = [
        {"cat": "a", "series": "A", "val": 1, "target": 5},
        {"cat": "a", "series": "B", "val": 2, "target": 5},
    ]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "target" in warnings[0].message


def test_fires_on_field_colored_overlay_with_non_quantitative_base_y_and_layer_color() -> (
    None
):
    """The same use_shared_scale gate must apply to a LAYER's own
    nominal/ordinal color values, not just a colorless layer's label --
    _overlay.py appends `layer_color_values` to `scale_domain` only
    inside `if use_shared_scale:` (requires a quantitative base y), one
    gate over both contributions. The detector must check
    base_y_is_quantitative for both branches, not just the
    colorless-layer one, or it silently widens the domain past what the
    emitter actually paints -- reachable on a scatter base (y is
    data-inferred, not hardcoded quantitative) with a non-numeric y, a
    base color:, and a layer carrying its own color:, where an authored
    entry naming a value only that layer would have supplied must not
    resolve against a domain the render never actually shows it in."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch

    chart = _scatter(
        y="cat",  # non-numeric base y: infers nominal, not quantitative
        style=ScatterChartStylePatch.model_validate(
            {"legend": {"values": ["A", "B", "X"]}}
        ),
        layers=[LineLayer(type="line", y="target", color="kind")],
    )
    rows = [
        {"cat": "a", "series": "A", "val": 1, "target": 5, "kind": "X"},
        {"cat": "a", "series": "B", "val": 2, "target": 5, "kind": "Y"},
    ]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "X" in warnings[0].message


def test_fires_on_heatmap_when_an_entry_matches_no_series() -> None:
    from dbt_charts.core.compile.models.style.authored import HeatmapChartStylePatch

    chart = _heatmap(
        style=HeatmapChartStylePatch.model_validate(
            {"legend": {"visible": True, "values": ["A", "bogus"]}}
        )
    )
    warnings = detector.detect(_ctx(chart, _HEATMAP_ROWS))
    assert len(warnings) == 1
    assert "bogus" in warnings[0].message


def test_layered_chart_uses_a_layers_own_color_field_not_its_label() -> None:
    """A layer that binds its own `color:` field contributes that field's
    OWN distinct values to the domain, not its (never-rendered) label --
    matching what the emitter actually paints. Authoring the layer's real
    category ("East") must resolve; authoring its label ("Target") must
    not."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    chart = _bar(
        style=BarChartStylePatch.model_validate(
            {"legend": {"values": ["A", "B", "East"]}}
        ),
        layers=[LineLayer(type="line", y="target", color="region")],
    )
    rows = [
        {"cat": "a", "series": "A", "val": 1, "target": 5, "region": "East"},
        {"cat": "a", "series": "B", "val": 2, "target": 6, "region": "West"},
    ]
    assert detector.detect(_ctx(chart, rows)) == []


def test_fires_on_a_pure_author_derived_base_label_overlay() -> None:
    """Regression: a `base_label` overlay where NO layer binds its own
    `color:` is 100% author-derived config (base label + layer labels,
    never query data), but the emitter never raises for any shape any
    more -- an unresolved entry there is dropped exactly like the
    field-colored case, so this detector must cover it too, or it ships
    silently."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    chart = _bar(
        color=None,
        style=BarChartStylePatch.model_validate(
            {"legend": {"values": ["val", "bogus"]}}
        ),
        layers=[LineLayer(type="line", y="target")],
    )
    rows = [
        {"cat": "a", "val": 1, "target": 5},
        {"cat": "a", "val": 2, "target": 6},
    ]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "bogus" in warnings[0].message


def test_silent_on_a_pure_author_derived_base_label_overlay_when_resolved() -> None:
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    chart = _bar(
        color=None,
        style=BarChartStylePatch.model_validate({"legend": {"values": ["val"]}}),
        layers=[LineLayer(type="line", y="target")],
    )
    rows = [
        {"cat": "a", "val": 1, "target": 5},
        {"cat": "a", "val": 2, "target": 6},
    ]
    assert detector.detect(_ctx(chart, rows)) == []


def test_silent_on_a_base_label_overlay_with_zero_rows() -> None:
    """base_y_is_quantitative must not re-derive the base's y type via
    infer_vega_type_from_data(rows, chart.y) for bar/area/line -- the
    emitter hardcodes their y as quantitative, never data-infers it, so
    zero rows (infer_vega_type_from_data's own empty-data fallback
    returns "nominal") would otherwise make the detector build a domain
    missing the colorless layer's label, a spurious "unresolved" on a
    board that paints it fine. Scatter is the one family whose y type is
    genuinely data-dependent; bar/area/line must not ask the data at
    all."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    chart = _bar(
        color=None,
        style=BarChartStylePatch.model_validate(
            {"legend": {"values": ["val", "target"]}}
        ),
        layers=[LineLayer(type="line", y="target")],
    )
    assert detector.detect(_ctx(chart, [])) == []


def test_silent_on_a_base_with_a_non_series_color_channel() -> None:
    """Regression: the emitter's base_label arm only fires when the base
    has NO ``color`` encoding key at all (``_overlay.py``'s
    ``use_shared_scale`` requires ``"color" not in base_spec.encoding or
    field_color_base``) -- a literal/gradient/conditional color channel
    on the base (e.g. authored via a literal fill) still populates
    ``encoding.color`` (as a value/gradient/condition, not a field), so
    ``use_shared_scale`` is False and the emitter never builds a shared
    scale/legend at all. The detector's own top-level gate only checked
    ``color_ch.mode != "series"``, which is also true for a non-series
    mode that ISN'T absent -- so it built a base_label domain and warned
    against a domain the emitter never built. Mirror the emitter: only
    the base_label arm applies when there is no color channel at all."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.chart.resolved._channel import (
        ResolvedStyleChannel,
    )

    chart = _bar(
        y="value",
        color=None,
        style=BarChartStylePatch.model_validate(
            {"legend": {"visible": True, "values": ["Gamma", "value"]}}
        ),
        layers=[LineLayer(type="line", y="target", color="kind")],
    )
    rows = [
        {"cat": "a", "value": 10, "target": 5, "kind": "Alpha"},
        {"cat": "b", "value": 12, "target": 6, "kind": "Beta"},
    ]
    resolved = make_test_resolved_chart(chart, rows)
    # No authoring path reaches a literal color: at chart root (rejected --
    # see test_narrow_color.py); inject the shape directly to prove the
    # detector's own logic, independent of whether today's grammar can
    # author it.
    literal_color = ResolvedStyleChannel(
        channel="color", mode="literal", literal_value="#ff0000"
    )
    resolved = resolved.model_copy(
        update={
            "resolved_channels": {
                **resolved.resolved_channels,
                "color": literal_color,
            }
        }
    )
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={},
    )
    assert detector.detect(ctx) == []


def test_silent_when_the_legend_is_suppressed() -> None:
    """Regression: the detector never checked `chart.legend.visible` --
    with `legend: {visible: false, values: [...]}`,
    `apply_legend_entry_order` no-ops immediately (`enc["legend"]` is
    `None`, not a dict) so nothing is dropped and no legend renders, yet
    the WARN still fired on a chart with nothing wrong to report. Stacked
    (not grouped/"none"), so nothing else reads `legend.values` while
    hidden either -- see the grouped-bar exception below."""
    chart = _bar(
        style=BarChartStylePatch.model_validate(
            {
                "stack": "zero",
                "legend": {"visible": False, "values": ["A", "C"]},
            }
        )
    )
    assert detector.detect(_ctx(chart)) == []


def test_fires_on_a_grouped_bar_even_when_the_legend_is_suppressed() -> None:
    """Regression: unlike the stacked case above, a grouped (`stack:
    "none"`) bar's `_grouped_bar_paint_order` reads `chart.legend.values`
    for the color scale/xOffset reorder regardless of legend visibility
    (bar.py's own gate is deliberately visibility-independent, since
    paint order and legend display are separate concerns) -- an
    unresolved entry there still silently reorders the bars wrong even
    though nothing renders in the (hidden) legend, so the detector must
    not skip this shape on invisibility."""
    chart = _bar(
        style=BarChartStylePatch.model_validate(
            {
                "stack": "none",
                "legend": {"visible": False, "values": ["A", "C"]},
            }
        )
    )
    warnings = detector.detect(_ctx(chart))
    assert len(warnings) == 1
    assert "C" in warnings[0].message


def test_silent_on_a_wide_grouped_bar_with_a_suppressed_legend() -> None:
    """Regression: the grouped-bar carve-out (previous test) only applies
    to a NON-wide bar -- `_grouped_bar_paint_order` (the visibility-
    independent read) lives behind `elif color_ch is not None:` in
    emitters/bar.py, the non-wide branch. A wide `y: [...]` chart takes
    the sibling `if wide is not False:` branch instead, which never
    calls `_grouped_bar_paint_order`, so a hidden-legend wide grouped bar
    has nothing reading `legend.values` while hidden and must not warn."""
    chart = _bar(
        y=["revenue_usd", "cost_usd"],
        color=None,
        style=BarChartStylePatch.model_validate(
            {
                "stack": "none",
                "legend": {"visible": False, "values": ["revenue_usd", "bogus"]},
            }
        ),
    )
    rows = [{"cat": "a", "revenue_usd": 1, "cost_usd": 2}]
    assert detector.detect(_ctx(chart, rows)) == []


def test_silent_on_a_histogram_with_a_suppressed_legend() -> None:
    """Regression: a histogram resolves to ResolvedBarChart too (`chart_
    type == "histogram"`), but `_emit_histogram` is a completely separate
    emitter function that never calls `_grouped_bar_paint_order` -- the
    grouped-bar carve-out must not treat a histogram's `stack == "none"`
    (its baseline default) as the same shape."""
    chart = _bar(
        type="histogram",
        x="val",
        y=None,
        style=BarChartStylePatch.model_validate(
            {
                "legend": {"visible": False, "values": ["A", "bogus"]},
            }
        ),
    )
    rows = [
        {"val": 1, "series": "A"},
        {"val": 2, "series": "B"},
    ]
    assert detector.detect(_ctx(chart, rows)) == []


def test_silent_on_an_ordinal_color_grouped_bar_with_a_suppressed_legend() -> None:
    """Regression: `_grouped_bar_paint_order` is only reached inside the
    `color_enc_type == "nominal"` inner branch -- an ordinal color
    (date-like bucket strings) never reaches it, even though the OUTER
    condition that resolves `legend.values` allows both nominal and
    ordinal. A hidden-legend grouped bar with an ordinal color column has
    nothing reading `legend.values` while hidden."""
    chart = _bar(
        style=BarChartStylePatch.model_validate(
            {
                "stack": "none",
                "legend": {"visible": False, "values": ["2024-Q1", "bogus"]},
            }
        )
    )
    rows = [
        {"cat": "a", "series": "2024-Q1", "val": 1},
        {"cat": "a", "series": "2024-Q2", "val": 2},
    ]
    assert detector.detect(_ctx(chart, rows)) == []


def test_layered_chart_flags_a_layers_never_rendered_label() -> None:
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    chart = _bar(
        style=BarChartStylePatch.model_validate(
            {"legend": {"values": ["A", "B", "Target"]}}
        ),
        layers=[LineLayer(type="line", y="target", color="region")],
    )
    rows = [
        {"cat": "a", "series": "A", "val": 1, "target": 5, "region": "East"},
        {"cat": "a", "series": "B", "val": 2, "target": 6, "region": "West"},
    ]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "Target" in warnings[0].message


def test_silent_on_a_colorless_overlay_with_non_quantitative_base_y() -> None:
    """Regression: mirrors _overlay.py's use_shared_scale in full -- a
    non-quantitative base y (scatter is the one family whose y is
    genuinely data-inferred) means no shared color scale gets built at
    all, so this detector must return None entirely rather than still
    seeding a domain from base_label."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch

    chart = _scatter(
        color=None,
        y="cat",
        style=ScatterChartStylePatch.model_validate(
            {"legend": {"visible": True, "values": ["target"]}}
        ),
        layers=[LineLayer(type="line", y="target")],
    )
    rows = [
        {"cat": "a", "target": 30},
        {"cat": "b", "target": 32},
    ]
    assert detector.detect(_ctx(chart, rows)) == []


def test_layer_whose_own_query_diverges_from_the_base_reads_layer_results() -> None:
    """Regression: `_layer_rows` falls back to `base_rows` only when a
    chart_id has no `layer_results` entry, or the specific layer's
    `query_name` isn't in it -- every `_ctx()` elsewhere in this file omits
    `layer_results` entirely, so both fallback branches only ever ran in
    their DEFAULT state. A layer whose `query:` genuinely diverges from the
    base must read ITS OWN rows
    (`ctx.layer_results[chart_id][layer.query_name]`), not the base's -- a
    value present only in the layer's own query result must resolve."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    chart = _bar(
        style=BarChartStylePatch.model_validate(
            {"legend": {"values": ["A", "B", "Diverging"]}}
        ),
        layers=[LineLayer(type="line", y="target", color="region", query="layer_q")],
    )
    base_rows = [
        {"cat": "a", "series": "A", "val": 1},
        {"cat": "a", "series": "B", "val": 2},
    ]
    resolved = make_test_resolved_chart(chart, base_rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: base_rows},
        layer_results={
            resolved.id: {"layer_q": [{"target": 5, "region": "Diverging"}]}
        },
        vega_specs={},
    )
    assert detector.detect(ctx) == []


def test_geoshape_normalizes_before_building_its_domain_like_geo_py_does() -> None:
    """Regression: the geoshape arm filtered `isinstance(v, str)` on
    UN-normalized rows, while `geo.py` runs `normalize_data_types` before
    its own `seen` build -- a `datetime.date` `value:` column resolves in
    the emitter (normalized to its str form there) but the detector's
    isinstance check dropped the raw (non-str) date entirely, producing a
    false "no domain configured" WARN on a chart that renders correctly."""
    import datetime

    from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
    from dbt_charts.core.compile.models.style.authored.geoshape import (
        GeoshapeChartStylePatch,
    )

    chart = GeoshapeChart(
        id="geo1",
        type="geoshape",
        geo_source="fake-geo-source",
        lookup="state",
        value="tier",
        style=GeoshapeChartStylePatch.model_validate(
            {"legend": {"values": ["2024-01-01"]}}
        ),
    )
    rows = [
        {"state": "CA", "tier": datetime.date(2024, 1, 1)},
        {"state": "TX", "tier": datetime.date(2024, 2, 1)},
    ]
    assert detector.detect(_ctx(chart, rows)) == []


def test_fires_on_geoshape_nominal_choropleth_when_entry_matches_nothing() -> None:
    from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
    from dbt_charts.core.compile.models.style.authored.geoshape import (
        GeoshapeChartStylePatch,
    )

    chart = GeoshapeChart(
        id="geo1",
        type="geoshape",
        geo_source="fake-geo-source",
        lookup="state",
        value="tier",
        style=GeoshapeChartStylePatch.model_validate(
            {"legend": {"values": ["west", "bogus"]}}
        ),
    )
    rows = [
        {"state": "CA", "tier": "west"},
        {"state": "TX", "tier": "south"},
    ]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "bogus" in warnings[0].message


def test_silent_on_geoshape_when_every_entry_resolves() -> None:
    from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
    from dbt_charts.core.compile.models.style.authored.geoshape import (
        GeoshapeChartStylePatch,
    )

    chart = GeoshapeChart(
        id="geo1",
        type="geoshape",
        geo_source="fake-geo-source",
        lookup="state",
        value="tier",
        style=GeoshapeChartStylePatch.model_validate(
            {"legend": {"values": ["WEST", "south"]}}
        ),
    )
    rows = [
        {"state": "CA", "tier": "west"},
        {"state": "TX", "tier": "south"},
    ]
    assert detector.detect(_ctx(chart, rows)) == []


def test_fires_on_single_series_base_overlay_whose_layer_binds_its_own_color() -> None:
    """Regression: a base with NO color: (base_label arm) whose layer binds
    its own field color mixes an author-derived label with query-data
    values in one domain, and never raises -- this detector is the only
    diagnostic for it. Bar `y: value` (no color:), layer `line y=target
    color=kind`, `values: ["Gamma", "value"]` drops "Gamma" with no signal
    unless this fires."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    chart = _bar(
        y="value",
        color=None,
        style=BarChartStylePatch.model_validate(
            {"legend": {"visible": True, "values": ["Gamma", "value"]}}
        ),
        layers=[LineLayer(type="line", y="target", color="kind")],
    )
    rows = [
        {"cat": "a", "value": 10, "target": 5, "kind": "Alpha"},
        {"cat": "b", "value": 12, "target": 6, "kind": "Beta"},
    ]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "Gamma" in warnings[0].message


def test_silent_on_single_series_base_overlay_when_every_entry_resolves() -> None:
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    chart = _bar(
        y="value",
        color=None,
        style=BarChartStylePatch.model_validate(
            {"legend": {"visible": True, "values": ["value", "alpha"]}}
        ),
        layers=[LineLayer(type="line", y="target", color="kind")],
    )
    rows = [
        {"cat": "a", "value": 10, "target": 5, "kind": "Alpha"},
        {"cat": "b", "value": 12, "target": 6, "kind": "Beta"},
    ]
    assert detector.detect(_ctx(chart, rows)) == []


def test_fires_on_single_series_base_overlay_whose_layer_color_is_all_null() -> None:
    """Regression: infer_vega_type_from_data infers "quantitative" for an
    all-null column (nulls are skipped, so all_numeric stays True by
    vacuous truth), so an all-null-color layer falls to the colorless
    branch (contributes its LABEL, not distinct field values) exactly
    like a layer with no color: at all -- must still resolve/report
    correctly against that label-based domain, not silently miscompute
    it because this render's data happens to be empty."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    chart = _bar(
        y="value",
        color=None,
        style=BarChartStylePatch.model_validate(
            {"legend": {"visible": True, "values": ["Alpha", "Beta"]}}
        ),
        layers=[LineLayer(type="line", y="target", color="kind", label="Alpha")],
    )
    rows = [
        {"cat": "a", "value": 10, "target": 5, "kind": None},
        {"cat": "b", "value": 12, "target": 6, "kind": None},
    ]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "Beta" in warnings[0].message


def test_silent_on_a_quantitative_gradient_legend_with_an_authored_tick_ladder() -> (
    None
):
    """Regression: a quantitative `color:` still resolves to mode="series",
    so VL paints a continuous gradient legend and `legend.values` there is
    the authored tick ladder `apply_gradient_legend_endpoint_labels`
    honors -- not a categorical entry list. Every emit site gates on
    `enc["type"] in ("nominal", "ordinal")`; the detector must too, or it
    names raw data values as a "legend domain" on a correct board."""
    from dbt_charts.core.compile.models.style.authored import HeatmapChartStylePatch

    chart = _heatmap(
        style=HeatmapChartStylePatch.model_validate(
            {"legend": {"visible": True, "values": ["0", "5000", "10000"]}}
        ),
    )
    rows = [
        {"cat": "a", "cat2": "x", "series": 1200.0},
        {"cat": "a", "cat2": "y", "series": 8400.0},
    ]
    assert detector.detect(_ctx(chart, rows)) == []


def test_fires_on_an_ordinal_date_like_color_when_an_entry_matches_nothing() -> None:
    """Regression: infer_vega_type_from_data returns "ordinal" for a
    date-like category string ("2024-Q1"), not "nominal" -- the detector's
    own gate skipped every such chart entirely, so an unresolvable entry
    shipped a phantom swatch on the emit side (which never raises) with
    no diagnostic anywhere."""
    chart = _bar(
        color="cohort",
        style=BarChartStylePatch.model_validate(
            {"legend": {"values": ["2024-Q1", "NOT_A_COHORT"]}}
        ),
    )
    rows = [
        {"cat": "a", "cohort": "2024-Q1", "val": 1},
        {"cat": "a", "cohort": "2024-Q2", "val": 2},
    ]
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "NOT_A_COHORT" in warnings[0].message
