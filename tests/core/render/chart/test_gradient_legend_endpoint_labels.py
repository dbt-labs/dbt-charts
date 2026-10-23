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

from typing import Any

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedGeoshapeChart
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.chart.geo import _resolve_geoshape
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.emitters.geo import GeoshapeEmitter
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.translate import translate_to_vl

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
