"""Sequential/quantitative gradient legends (heatmap, geoshape) label their
domain's ticks instead of whatever ticks Vega-Lite's own algorithm guesses.

Before this change: a data domain of [3, 97] used to render ticks like
20, 40, 60, 80 with nothing marking the real extremes.

Current default: the domain widens to "nice" round bounds (mirroring
Vega-Lite's own scale.nice) and every nice tick is labeled — not just the
two endpoints — with the widened domain baked into VL `scale.domain` so the
rendered colors actually extend to match the labeled bounds. `nice: false`
opts back into exact-extent, two-endpoint-only behavior.

Scoped to the gradient-only render seam (heatmap.py / geo.py) so a
categorical (bar/pie) legend's rendered ticks stay untouched.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.normalized import BarChart, HeatmapChart
from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedGeoshapeChart
from dbt_charts.core.compile.models.primitives import ScaleTargetConfig
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import HeatmapChartStylePatch
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.chart.geo import _resolve_geoshape
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.emitters._channels import (
    apply_gradient_legend_endpoint_labels,
)
from dbt_charts.core.render.chart.emitters.geo import GeoshapeEmitter
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.translate import translate_to_vl
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())
_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)

HEATMAP_DATA = [
    {"day": "Mon", "hour": "9am", "count": 5},
    {"day": "Tue", "hour": "10am", "count": 8},
]

# Matches the task's own motivating example (a [3, 97] domain) — nice_tick_values(3, 97, 6)
# widens to [0, 20, 40, 60, 80, 100].
HEATMAP_DATA_WIDE = [
    {"day": "Mon", "hour": "9am", "count": 3},
    {"day": "Tue", "hour": "10am", "count": 97},
]

# RJ's own edge case from the design conversation: a naive "round the max to the
# nearest step" approach on [3, 81] would produce an uneven last interval
# (60 -> 81 is a 21-gap vs. ~20 everywhere else). nice_tick_values(3, 81, 6) instead
# widens the whole domain to [0, 100] first, so every interval is a genuine 20.
HEATMAP_DATA_UNEVEN_MAX = [
    {"day": "Mon", "hour": "9am", "count": 3},
    {"day": "Tue", "hour": "10am", "count": 81},
]


def _heatmap_gradient_style(**gradient_overrides: object) -> Any:
    compiled = get_theme_style()
    gradient_patch = HeatmapChartStylePatch.model_validate(
        {"color": {"gradient": {"palette": "dbt-seq-blue", **gradient_overrides}}}
    )
    new_heatmap = merge_onto_base(compiled.charts.heatmap, gradient_patch)
    custom_charts = compiled.charts.model_copy(update={"heatmap": new_heatmap})
    return resolve_style_and_context(
        compiled.model_copy(update={"charts": custom_charts})
    )


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _heatmap_chart(**kwargs: object) -> HeatmapChart:
    return HeatmapChart(
        id="test_heatmap",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="heatmap",
        x="day",
        y="hour",
        color="count",
        **kwargs,
    )


def test_heatmap_legend_labels_default_nice_widens_domain_and_labels_every_tick() -> (
    None
):
    """Default behavior (nice: true): the domain widens to nice round bounds
    and EVERY nice tick is labeled — min, max, and the round in-betweens —
    with the widened bounds baked into VL `scale.domain` so the rendered
    colors actually extend to match what's labeled (not just the legend text
    getting rounder without the swatch moving)."""
    chart = _heatmap_chart()
    spec = generate_vega_lite_spec(
        chart, HEATMAP_DATA_WIDE, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )
    color_scale = spec["encoding"]["color"]["scale"]
    assert color_scale["domain"] == [0, 100]
    assert spec["encoding"]["color"]["legend"]["values"] == [0, 20, 40, 60, 80, 100]


def test_heatmap_legend_labels_nice_widening_uses_even_intervals_not_hand_rounded() -> (
    None
):
    """RJ's own edge case: naively rounding just the max (81 -> 90 at a step
    of 10, or 81 -> 100 at a step of 20 without touching the min) would leave
    an uneven last interval. nice_tick_values widens the WHOLE domain first,
    so every interval is a genuine, even 20 — verifying the chart-level wiring
    reuses that real output rather than a hand-rounded guess."""
    chart = _heatmap_chart()
    spec = generate_vega_lite_spec(
        chart,
        HEATMAP_DATA_UNEVEN_MAX,
        board_style=_BOARD_RS,
        chart_style_context=_BOARD_CTX,
    )
    color_scale = spec["encoding"]["color"]["scale"]
    assert color_scale["domain"] == [0, 100]
    ticks = spec["encoding"]["color"]["legend"]["values"]
    assert ticks == [0, 20, 40, 60, 80, 100]
    gaps = {b - a for a, b in zip(ticks, ticks[1:], strict=False)}
    assert gaps == {20}, f"expected even 20-wide intervals, got gaps {gaps}"


def test_heatmap_legend_labels_nice_false_falls_back_to_exact_data_extent() -> None:
    """`nice: false` opts out of domain widening entirely: falls back to
    the exact-extent behavior — real data extent, domain untouched (no
    domainMin/domainMax baked beyond what already happened), only the two
    endpoints labeled, exact, never nice-rounded."""
    board_rs, board_ctx = _heatmap_gradient_style(nice=False)
    chart = _heatmap_chart()
    spec = generate_vega_lite_spec(
        chart, HEATMAP_DATA_WIDE, board_style=board_rs, chart_style_context=board_ctx
    )
    color_scale = spec["encoding"]["color"]["scale"]
    assert "domain" not in color_scale
    assert spec["encoding"]["color"]["legend"]["values"] == [3, 97]


def test_heatmap_legend_labels_use_authored_min_max_override() -> None:
    """When the chart's gradient scale has an authored min/max override, the
    endpoint labels reflect that domain, not the raw data extent — the scale
    override IS the effective domain, so exact-extent means exact-to-THAT.
    Wins outright over `nice` in either direction (default True here)."""
    board_rs, board_ctx = _heatmap_gradient_style(min=0, max=100)
    chart = _heatmap_chart()
    spec = generate_vega_lite_spec(
        chart, HEATMAP_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    color_scale = spec["encoding"]["color"]["scale"]
    assert color_scale["domain"] == [0, 100]
    assert spec["encoding"]["color"]["legend"]["values"] == [0, 100]


def test_heatmap_legend_labels_authored_min_max_wins_even_with_nice_false() -> None:
    """Author-explicit min/max wins outright regardless of `nice` — `nice`
    only governs the data-derived path, never an authored override."""
    board_rs, board_ctx = _heatmap_gradient_style(min=0, max=100, nice=False)
    chart = _heatmap_chart()
    spec = generate_vega_lite_spec(
        chart, HEATMAP_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    color_scale = spec["encoding"]["color"]["scale"]
    assert color_scale["domain"] == [0, 100]
    assert spec["encoding"]["color"]["legend"]["values"] == [0, 100]


@pytest.mark.parametrize("nice", [True, False])
def test_heatmap_legend_labels_max_only_bound_is_honored_exactly(nice: bool) -> None:
    """A single-sided `max` override must not be silently discarded by
    nice-widening: the authored ceiling stays the effective domain edge, and
    the legend label agrees with it — not a nice-widened domain that
    ignores it, and not a raw-data-extent label that ignores it either."""
    board_rs, board_ctx = _heatmap_gradient_style(max=200, nice=nice)
    chart = _heatmap_chart()
    spec = generate_vega_lite_spec(
        chart, HEATMAP_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    color_scale = spec["encoding"]["color"]["scale"]
    assert "domain" not in color_scale
    assert color_scale["domainMax"] == 200
    assert spec["encoding"]["color"]["legend"]["values"] == [5, 200]


@pytest.mark.parametrize("nice", [True, False])
def test_heatmap_legend_labels_min_only_bound_is_honored_exactly(nice: bool) -> None:
    """Mirror of the max-only case: a single-sided `min` override stays the
    effective domain edge and the legend label agrees with it."""
    board_rs, board_ctx = _heatmap_gradient_style(min=0, nice=nice)
    chart = _heatmap_chart()
    spec = generate_vega_lite_spec(
        chart, HEATMAP_DATA, board_style=board_rs, chart_style_context=board_ctx
    )
    color_scale = spec["encoding"]["color"]["scale"]
    assert "domain" not in color_scale
    assert color_scale["domainMin"] == 0
    assert spec["encoding"]["color"]["legend"]["values"] == [0, 8]


def test_heatmap_gradient_domain_includes_decimal_values() -> None:
    """`Decimal` measure values (BigQuery/Snowflake NUMERIC columns) must
    count toward the domain extent, matching `coerce_numeric_cell`'s shared
    numeric rule — not silently no-op the whole feature the way an
    `isinstance(..., (int, float))` check would."""
    data = [
        {"day": "Mon", "hour": "9am", "count": Decimal("3")},
        {"day": "Tue", "hour": "10am", "count": Decimal("97")},
    ]
    chart = _heatmap_chart()
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )
    color_scale = spec["encoding"]["color"]["scale"]
    assert color_scale["domain"] == [0, 100]
    assert spec["encoding"]["color"]["legend"]["values"] == [0, 20, 40, 60, 80, 100]


def test_heatmap_gradient_domain_excludes_non_finite_values_without_crashing() -> None:
    """NaN/±Infinity measure values must be excluded from the domain extent
    (matching `coerce_numeric_cell`'s null rule) instead of reaching
    `nice_tick_values` and crashing (`math.floor(nan / step)` raises
    `ValueError`, `math.log10(inf)` raises `OverflowError`)."""
    data = [
        {"day": "Mon", "hour": "9am", "count": float("nan")},
        {"day": "Tue", "hour": "10am", "count": 3},
        {"day": "Wed", "hour": "11am", "count": float("inf")},
        {"day": "Thu", "hour": "12pm", "count": 97},
    ]
    chart = _heatmap_chart()
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )
    color_scale = spec["encoding"]["color"]["scale"]
    assert color_scale["domain"] == [0, 100]
    assert spec["encoding"]["color"]["legend"]["values"] == [0, 20, 40, 60, 80, 100]


def test_heatmap_gradient_domain_widening_gated_on_inferred_quantitative_type() -> None:
    """Regression: `enc["type"]` comes from
    `infer_vega_type_from_data`, which only samples the first 10 rows — a
    color column whose first 10 sampled rows are non-numeric strings infers
    `"nominal"` even though a later row is numeric. Domain widening and
    endpoint labels must NOT fire in that case: baking a numeric `domain`/
    `values` onto a scale VL renders nominal is exactly the bug this guards
    (Vega can't match string categories against a numeric domain, so every
    rect paints with no fill). The previous version of this test asserted
    only `domain_widened == labels_present` (a boolean equality) — that was
    green on the real broken output (`type: "nominal"`, a baked numeric
    `domain`, and numeric `legend.values`), since both were simultaneously
    "on". Assert the concrete spec shape instead."""
    data = [
        {"day": "Mon", "hour": f"h{i}", "count": "not-a-number"} for i in range(10)
    ] + [{"day": "Tue", "hour": "h10", "count": 50}]
    chart = _heatmap_chart()
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )
    color_enc = spec["encoding"]["color"]
    assert color_enc["type"] == "nominal"
    assert "domain" not in color_enc.get("scale", {})
    legend = color_enc.get("legend")
    assert legend is None or "values" not in legend


def test_heatmap_numeric_string_color_column_stays_nominal_no_domain_no_labels() -> (
    None
):
    """CRITICAL regression: a color column of numeric-*looking* strings (as
    read from CSV/JSON, `CAST(x AS VARCHAR)`, or any all-varchar `read_csv`)
    must infer `"nominal"` — matching `infer_vega_type_from_data`'s strict
    `isinstance(value, (int, float, Decimal))` rule, which does not accept
    strings — and must get neither a widened `domain` nor `legend.values`.

    Before the fix, `_numeric_extent` used `coerce_numeric_cell`, which DOES
    accept numeric strings — so it derived a 2-element numeric domain from
    the very same data `infer_vega_type_from_data` classified nominal. Vega
    then built an ordinal scale from `type: "nominal"` while `scale.domain`
    held two numbers matching none of the string categories: every rect
    rendered with no fill. `test_heatmap_numeric_string_color_column_renders_painted_cells`
    below confirms this at the actual `vl_convert` render, not just the spec.
    """
    data = [
        {"day": "Mon", "hour": "9am", "count": "0"},
        {"day": "Tue", "hour": "10am", "count": "7"},
        {"day": "Wed", "hour": "11am", "count": "12"},
        {"day": "Thu", "hour": "12pm", "count": "40"},
        {"day": "Fri", "hour": "1pm", "count": "77"},
        {"day": "Sat", "hour": "2pm", "count": "80"},
    ]
    chart = _heatmap_chart()
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )
    color_enc = spec["encoding"]["color"]
    assert color_enc["type"] == "nominal"
    assert "domain" not in color_enc.get("scale", {})
    legend = color_enc.get("legend")
    assert legend is None or "values" not in legend


def test_heatmap_numeric_string_color_column_renders_painted_cells() -> None:
    """CRITICAL regression, render-verified: the same numeric-string color
    column as the spec-level test above, rendered through the real
    `vl_convert` SVG pipeline — every cell must actually be painted with a
    palette fill, not left blank by a numeric domain baked onto a nominal
    scale. Reproduces the review's measured repro (2 fill-less legend
    swatches, 0 painted rects) and confirms the fix restores real fills."""
    try:
        import vl_convert as vlc  # noqa: F401 — skip marker
    except ImportError:
        pytest.skip("vl_convert not installed")

    import re

    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.vega_lite import render_chart

    data = [
        {"day": "Mon", "hour": "9am", "count": "0"},
        {"day": "Tue", "hour": "10am", "count": "7"},
        {"day": "Wed", "hour": "11am", "count": "12"},
        {"day": "Thu", "hour": "12pm", "count": "40"},
        {"day": "Fri", "hour": "1pm", "count": "77"},
        {"day": "Sat", "hour": "2pm", "count": "80"},
    ]
    chart = _heatmap_chart()
    board_style = resolve_style(get_theme_style())
    svg = render_chart(
        chart,
        board_style,
        _BOARD_CTX,
        data,
        format="svg",
        width=600.0,
        height=300.0,
    )
    fills = re.findall(r'<path[^>]*\sfill="(#[0-9a-fA-F]{3,8})"', svg)
    assert len(fills) >= len(data), (
        f"expected at least one painted rect per data row ({len(data)}), "
        f"got {len(fills)} filled paths — cells rendered blank"
    )


@pytest.mark.parametrize(
    "authored_values",
    [["Low", "High"], []],
    ids=["truthy-ladder", "authored-empty-ladder"],
)
def test_apply_gradient_legend_endpoint_labels_does_not_clobber_authored_values(
    authored_values: list[str],
) -> None:
    """An already-authored `legend.values` — including an explicit empty
    ladder (`values: []`, an author's choice to show no tick labels) — must
    not be silently overwritten by the gradient endpoint-label logic. The
    guard checks `is not None`, not truthiness, precisely so the empty-list
    case (falsy, but a real authored value) is respected too."""
    enc: dict[str, Any] = {"legend": {"values": authored_values}}
    scale = ScaleTargetConfig(palette="dbt-seq-blue")
    data = [{"count": 5}, {"count": 8}]
    apply_gradient_legend_endpoint_labels(enc, scale, data, "count")
    assert enc["legend"]["values"] == authored_values


def test_apply_gradient_legend_endpoint_labels_authored_bounds_include_hinge_tick() -> (
    None
):
    """A hinge strictly between the two (authored) endpoints is labeled
    alongside them, so the legend shows where the diverging palette
    actually pivots -- not just the two extremes."""
    enc: dict[str, Any] = {"legend": {}}
    scale = ScaleTargetConfig(palette="dbt-seq-blue", min=-1000, max=1000, hinge=0)
    data = [{"v": -500}, {"v": 500}]
    apply_gradient_legend_endpoint_labels(enc, scale, data, "v")
    assert enc["legend"]["values"] == [-1000, 0, 1000]


def test_apply_gradient_legend_endpoint_labels_hinge_extends_authored_bound() -> None:
    """A hinge outside the authored bound EXTENDS the labeled endpoint to
    match gradient_scale_to_vl's own extended rendered domain -- a legend
    showing [500, 1000] while the scale secretly renders [0, 1000] would
    disagree with what's actually painted. The hinge itself lands exactly
    on the extended endpoint here, so no separate third tick is added."""
    enc: dict[str, Any] = {"legend": {}}
    scale = ScaleTargetConfig(palette="dbt-seq-blue", min=500, max=1000, hinge=0)
    data = [{"v": 600}, {"v": 900}]
    apply_gradient_legend_endpoint_labels(enc, scale, data, "v")
    assert enc["legend"]["values"] == [0, 1000]


def test_apply_gradient_legend_endpoint_labels_nice_ticks_include_hinge() -> None:
    """The nice-widened tick ladder gets the hinge inserted too, when it
    isn't already one of the nice ticks."""
    enc: dict[str, Any] = {"legend": {}}
    scale = ScaleTargetConfig(palette="dbt-seq-blue", hinge=5)
    data = [{"v": 3}, {"v": 97}]
    apply_gradient_legend_endpoint_labels(enc, scale, data, "v")
    values = enc["legend"]["values"]
    assert values == sorted(values)
    assert 5 in values
    assert values[0] == 0
    assert values[-1] == 100


def test_apply_gradient_legend_endpoint_labels_no_hinge_unchanged() -> None:
    """A sequential (no hinge) scale keeps its existing two/nice-ladder
    behavior untouched -- including the authored ints' own type: hinge
    handling must never force an unrelated no-hinge scale's plain int
    bounds into floats in the emitted spec JSON."""
    enc: dict[str, Any] = {"legend": {}}
    scale = ScaleTargetConfig(palette="dbt-seq-blue", min=0, max=100)
    data = [{"v": 5}, {"v": 8}]
    apply_gradient_legend_endpoint_labels(enc, scale, data, "v")
    assert enc["legend"]["values"] == [0, 100]
    assert [type(v) for v in enc["legend"]["values"]] == [int, int]


def test_bar_categorical_legend_has_no_endpoint_values() -> None:
    """A categorical (series-color) legend's rendered ticks stay untouched by
    the gradient seam — the min/max endpoint-label logic (a 2-element numeric
    pair) must live in the gradient-only seam, not leak into
    legend_to_vl/apply_color_legend. A pinned display-order values list (the
    plain category strings, via apply_legend_entry_order) is unrelated and
    expected."""
    chart = BarChart(
        id="test_bar",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        color="region",
    )
    data = [
        {"month": "Jan", "revenue": 5, "region": "West"},
        {"month": "Jan", "revenue": 8, "region": "East"},
    ]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )
    legend = spec["encoding"]["color"].get("legend")
    values = legend.get("values") if legend else None
    assert values is None or set(values) == {"West", "East"}


def _geo_board_style() -> Any:
    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _geo_chart(**kwargs: object) -> ResolvedGeoshapeChart:
    compiled = GeoshapeChart(
        id="geo1",
        type="geoshape",
        geo_source="fake-geo-source",
        lookup="state",
        value="pop",
        **kwargs,
    )
    return _resolve_geoshape(compiled, [], _geo_board_style(), 800.0, None)


def test_geoshape_legend_labels_do_not_derive_from_raw_data() -> None:
    """Geoshape's choropleth gradient legend does NOT label endpoints from the
    raw pre-join data extent, unlike heatmap.

    Regression: `data` here is pre-lookup-join. VL's own rendered domain is
    computed from whichever rows survive the client-side geo-key join (which
    dbt charts never parses/validates here) — a row whose lookup key doesn't
    match a real feature (e.g. a zero-padded FIPS code against an unpadded
    preset) still counts toward `data`'s min/max, so trusting the raw extent
    can label an endpoint that corresponds to nothing shaded on the map. Only
    an author-declared min/max override is trustworthy for geoshape.
    """
    data = [{"state": "CA", "pop": 39_500_000}, {"state": "TX", "pop": 29_000_000}]
    vc = _geo_chart()
    v2_vl = translate_to_vl(GeoshapeEmitter().emit(vc, _DEFAULT_BOX, regroup((), data)))
    color_enc = v2_vl["layer"][1]["encoding"]["color"]
    legend = color_enc.get("legend")
    assert legend is None or "values" not in legend
    assert "domain" not in color_enc["scale"]


def test_geoshape_nice_true_still_defers_widening_to_vl_default_ticks() -> None:
    """`nice: true` (the default) still doesn't widen geoshape's domain or
    label anything automatically — nice-WIDENING isn't wired to geoshape yet.
    Only `nice: false`'s exact-endpoint mode is safe to compute client-side
    (see test_geoshape_nice_false_generates_client_side_domain_signal) —
    reading a scale's own resolved domain sidesteps the join-drop problem,
    but nice-widening a domain computed that way is a separate, larger
    change not attempted here."""
    data = [{"state": "CA", "pop": 39_500_000}, {"state": "TX", "pop": 29_000_000}]
    vc = _geo_chart(style={"color": {"gradient": {"nice": True}}})
    v2_vl = translate_to_vl(GeoshapeEmitter().emit(vc, _DEFAULT_BOX, regroup((), data)))
    color_enc = v2_vl["layer"][1]["encoding"]["color"]
    legend = color_enc.get("legend")
    assert legend is None or "values" not in legend
    assert "domain" not in color_enc["scale"]


def test_geoshape_nice_false_generates_client_side_domain_signal() -> None:
    """`nice: false` labels geoshape's legend with a Vega signal that reads
    the color scale's OWN resolved domain (`domain('color')`) at render
    time, rather than a Python-computed extent over pre-lookup-join `data`.

    This is the fix for the bug Round 1 could only refuse to trigger
    (`allow_data_derived=False`): Vega resolves `domain('color')` from the
    ACTUAL post-join mark data, so a row whose lookup key never matches a
    shape is automatically excluded the same way VL's own rendered color
    scale already excludes it — no join-awareness needed in Python, and no
    risk of labeling an endpoint that doesn't correspond to anything shaded
    on the map.

    The upper edge reads `peek(domain('color'))` rather than
    `domain('color')[1]`: a diverging `hinge` can bake a 3-element resolved
    domain (`[min, pivot, max]`), and index `[1]` would then read the pivot
    instead of the true max. `peek` (Vega's "last element" function) is
    correct for both the 2- and 3-element case.
    """
    data = [{"state": "CA", "pop": 39_500_000}, {"state": "TX", "pop": 29_000_000}]
    vc = _geo_chart(style={"color": {"gradient": {"nice": False}}})
    v2_vl = translate_to_vl(GeoshapeEmitter().emit(vc, _DEFAULT_BOX, regroup((), data)))
    color_enc = v2_vl["layer"][1]["encoding"]["color"]
    assert color_enc["legend"]["values"] == {
        "signal": "[domain('color')[0], peek(domain('color'))]"
    }


def test_geoshape_nice_false_min_only_bound_mixes_literal_and_signal() -> None:
    """A single-sided authored bound is honored exactly as typed (a literal
    in the signal expression); the free edge still reads the scale's real
    resolved domain, matching heatmap's equivalent single-sided-bound
    behavior."""
    data = [{"state": "CA", "pop": 39_500_000}, {"state": "TX", "pop": 29_000_000}]
    vc = _geo_chart(style={"color": {"gradient": {"min": 0, "nice": False}}})
    v2_vl = translate_to_vl(GeoshapeEmitter().emit(vc, _DEFAULT_BOX, regroup((), data)))
    color_enc = v2_vl["layer"][1]["encoding"]["color"]
    assert color_enc["legend"]["values"] == {"signal": "[0, peek(domain('color'))]"}


def test_geoshape_nice_false_hinge_scheme_legend_still_reads_true_max() -> None:
    """Regression: a diverging hinge on a geoshape's Vega-scheme gradient
    bakes ``domainMid`` (see gradient_scale_to_vl), which makes Vega's own
    resolved ``domain('color')`` a 3-element ``[min, pivot, max]`` array
    instead of 2. The legend signal's upper edge must still read the true
    max (`peek`), not the pivot (`[1]`, the pre-fix indexing bug)."""
    data = [{"state": "CA", "pop": 39_500_000}, {"state": "TX", "pop": 29_000_000}]
    vc = _geo_chart(
        style={"color": {"gradient": {"palette": "redblue", "hinge": 0, "nice": False}}}
    )
    v2_vl = translate_to_vl(GeoshapeEmitter().emit(vc, _DEFAULT_BOX, regroup((), data)))
    color_enc = v2_vl["layer"][1]["encoding"]["color"]
    assert color_enc["scale"]["domainMid"] == 0.0
    assert color_enc["legend"]["values"] == {
        "signal": "[domain('color')[0], peek(domain('color'))]"
    }


def test_geoshape_hinge_scheme_single_sided_bound_survives_domainMid_fallback() -> None:
    """Regression: a geoshape scheme's domainMid fallback (bounds
    unresolved — no data, and here only min authored) must not silently
    drop an authored single-sided bound. Before this fix the scale carried
    only {scheme, domainMid, clamp}; the authored min vanished, so
    apply_geo_choropleth_legend_endpoint_labels's nice:false legend
    labeled the literal authored min while the actual color domain started
    at the data minimum instead — label and colors disagreed."""
    vc = _geo_chart(
        style={"color": {"gradient": {"palette": "redblue", "min": -50, "hinge": 0}}}
    )
    v2_vl = translate_to_vl(
        GeoshapeEmitter().emit(
            vc, _DEFAULT_BOX, regroup((), [{"state": "CA", "pop": 1}])
        )
    )
    color_enc = v2_vl["layer"][1]["encoding"]["color"]
    assert color_enc["scale"]["domainMin"] == -50
    assert color_enc["scale"]["domainMid"] == 0.0
    assert "domainMax" not in color_enc["scale"]


@pytest.mark.network
def test_geoshape_nice_false_legend_renders_true_data_endpoints() -> None:
    """Render-level proof (vl_convert, real `us-states` topology fetched over
    the network) that the client-side signal actually EVALUATES to the real
    data endpoints in the compiled/rendered legend text — a signal that
    never evaluates would still pass the spec-key assertion above."""
    import re

    import vl_convert as vlc

    data = [
        {"state_id": "6", "population": 39_538_223},  # California
        {"state_id": "48", "population": 29_145_505},  # Texas
    ]
    compiled = GeoshapeChart(
        id="geo1",
        type="geoshape",
        geo_source="us-states",
        lookup="state_id",
        value="population",
        style={"color": {"gradient": {"palette": "dbt-seq-blue", "nice": False}}},
    )
    resolved = _resolve_geoshape(compiled, [], _geo_board_style(), 800.0, None)
    v2_vl = translate_to_vl(
        GeoshapeEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data))
    )

    svg = vlc.vegalite_to_svg(v2_vl)
    tick_texts = {
        t.replace(",", "") for t in re.findall(r"<text[^>]*>([^<]+)</text>", svg)
    }
    assert "39538223" in tick_texts, f"legend missing CA endpoint: {tick_texts}"
    assert "29145505" in tick_texts, f"legend missing TX endpoint: {tick_texts}"


def test_geoshape_legend_labels_authored_bounds_extend_for_hinge_outside_range() -> (
    None
):
    """A hinge outside BOTH authored bounds extends the labeled endpoint to
    match gradient_scale_to_vl's own extended rendered domain -- an
    authored-both-bounds legend is not exempt from hinge-awareness just
    because it skips the join-drop concern. No hinge TICK is added here --
    geoshape is deliberately exempt from that (see
    apply_geo_choropleth_legend_endpoint_labels's own docstring) -- just
    the two endpoints, extended."""
    data = [{"state": "CA", "pop": 600}, {"state": "TX", "pop": 900}]
    vc = _geo_chart(
        style={"color": {"gradient": {"min": 500, "max": 1000, "hinge": 0}}}
    )
    v2_vl = translate_to_vl(GeoshapeEmitter().emit(vc, _DEFAULT_BOX, regroup((), data)))
    color_enc = v2_vl["layer"][1]["encoding"]["color"]
    assert color_enc["scale"]["domain"][0] == 0.0
    assert color_enc["legend"]["values"] == [0.0, 1000]


def test_geoshape_legend_labels_single_sided_bound_signal_extends_for_hinge() -> None:
    """The literal half of the mixed literal+signal expression must also
    reflect hinge extension -- the free (signal) edge already reads Vega's
    own resolved domain automatically, but the literal edge would otherwise
    disagree with what gradient_scale_to_vl actually baked once hinge pulls
    that bound further out."""
    vc = _geo_chart(
        style={
            "color": {
                "gradient": {
                    "palette": "redblue",
                    "min": 500,
                    "hinge": 0,
                    "nice": False,
                }
            }
        }
    )
    v2_vl = translate_to_vl(
        GeoshapeEmitter().emit(
            vc, _DEFAULT_BOX, regroup((), [{"state": "CA", "pop": 1}])
        )
    )
    color_enc = v2_vl["layer"][1]["encoding"]["color"]
    assert color_enc["scale"]["domainMin"] == 0.0
    assert color_enc["legend"]["values"] == {"signal": "[0.0, peek(domain('color'))]"}


def test_geoshape_legend_labels_discrete_stop_list_single_bound_does_not_extend() -> (
    None
):
    """A discrete stop list (not a Vega scheme) with unresolved bounds can't
    pivot at all -- gradient_scale_to_vl's own domainMid fallback only fires
    for a scheme (documented, pre-existing limitation). The legend must NOT
    extend the literal bound in this case either, or it disagrees with the
    scale it's labeling (a legend/color mismatch is exactly what this whole
    PR exists to prevent)."""
    vc = _geo_chart(
        style={
            "color": {
                "gradient": {
                    "palette": ["#2166ac", "#f7f7f7", "#b2182b"],
                    "min": 500,
                    "hinge": 0,
                    "nice": False,
                }
            }
        }
    )
    v2_vl = translate_to_vl(
        GeoshapeEmitter().emit(
            vc, _DEFAULT_BOX, regroup((), [{"state": "CA", "pop": 1}])
        )
    )
    color_enc = v2_vl["layer"][1]["encoding"]["color"]
    assert color_enc["scale"]["domainMin"] == 500
    assert color_enc["legend"]["values"] == {"signal": "[500, peek(domain('color'))]"}


def test_geoshape_legend_labels_use_authored_min_max_override() -> None:
    """When the geoshape chart's gradient scale has an authored min/max
    override, the endpoint labels reflect that domain — an author-declared
    domain is trustworthy regardless of any join-drop concern, since it's not
    derived from `data` at all. Also verifies the scale's actual VL `domain`
    matches, so the rendered colors line up with what's labeled."""
    data = [{"state": "CA", "pop": 39_500_000}, {"state": "TX", "pop": 29_000_000}]
    vc = _geo_chart(style={"color": {"gradient": {"min": 0, "max": 50_000_000}}})
    v2_vl = translate_to_vl(GeoshapeEmitter().emit(vc, _DEFAULT_BOX, regroup((), data)))
    color_enc = v2_vl["layer"][1]["encoding"]["color"]
    assert color_enc["scale"]["domain"] == [0, 50_000_000]
    assert color_enc["legend"]["values"] == [0, 50_000_000]
