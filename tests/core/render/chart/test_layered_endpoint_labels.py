"""TDD tests for endpoint_labels.visible on layered single-series charts.

Regression coverage for: endpoint labels cannot be authored when there is no
color encoding. Before this fix, `EndpointLabelFeature.applies_to()` required
a base-level color channel
in "series" mode — a layered chart with no `color:` (e.g. a bar base +
a line overlay, each single-series) could never reach the endpoint-label
rail, even with `endpoint_labels.visible: true` authored.

The fix treats each layer (the base series plus every overlay layer) as its
own labeled endpoint on the SAME right_pane rail multi-series charts already
use — anchored on the base chart's own y-domain (single shared scale only;
a layer that pins its own axis_y.position is a genuine dual-axis chart and
is refused, see test_dual_axis_layer_raises below).
"""

from __future__ import annotations

from collections import Counter

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored._layer import (
    AreaLayer,
    BarLayer,
    LineLayer,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _bar_with_layers_data():
    return [
        {"month": "Jan", "annual": 10.0, "cumulative": 10.0},
        {"month": "Feb", "annual": 20.0, "cumulative": 30.0},
        {"month": "Mar", "annual": 5.0, "cumulative": 35.0},
    ]


def _labels_in_pane(spec: dict) -> dict[str, float]:
    """Return {label: y_value} from the hconcat label pane's inline data."""
    assert "hconcat" in spec, "endpoint-label pane did not fire"
    pane = spec["hconcat"][1]
    values = pane["data"]["values"]
    text_field = pane["encoding"]["text"]["field"]
    y_field = pane["encoding"]["y"]["field"]
    return {row[text_field]: row[y_field] for row in values}


def _shared_scale_domain(spec: dict) -> list[str] | None:
    """The legend's shared color-scale domain, or None when the legend is
    suppressed (every layer's own ``color.legend`` is None).

    ``suppress_legend`` is a single board-wide toggle (``_base_kwargs`` in
    compile), so every layer agrees on whether its own legend entry renders
    — reading the first one found is representative of them all, and every
    layer's ``color.scale`` is the SAME shared-scale dict (``_overlay.py``),
    so this never needs to merge across layers.
    """
    chart_pane = spec["hconcat"][0] if "hconcat" in spec else spec
    # A chart with no color-driven overlay wrapping (no `layers:`, or one
    # wrapped only by BaselineFeature's zero-rule) carries its color
    # encoding at the chart pane's own top level; a real overlay
    # (emitters/_overlay.py) carries it per sub-`layer` instead — check both.
    candidates = [chart_pane, *chart_pane.get("layer", [])]
    for candidate in candidates:
        color = candidate.get("encoding", {}).get("color")
        if not isinstance(color, dict) or color.get("legend") is None:
            continue
        scale = color.get("scale")
        if isinstance(scale, dict) and isinstance(scale.get("domain"), list):
            return [str(v) for v in scale["domain"]]
    return None


def _legend_visible(spec: dict) -> bool:
    """Whether the chart pane's color legend is not suppressed.

    Unlike ``_shared_scale_domain``, this doesn't need an explicit
    ``scale.domain`` in the spec — a plain (non-overlay) series-mode color
    encoding carries no baked domain at all (VL infers it from data; the
    palette lives in ``spec.config.range.category`` instead), so this is the
    right check for shapes with no ``chart.layers`` at all.

    The horizontal stacked-bar top_rail wraps the real chart pane in
    ``vconcat[1]`` (``vconcat[0]`` is the rail row itself, whose own color
    encoding always carries ``legend: null`` — that's the rail's private
    painting scale, not the chart's) — check that pane, not the rail's.
    """
    chart_pane = (
        spec["hconcat"][0]
        if "hconcat" in spec
        else spec["vconcat"][1]
        if "vconcat" in spec
        else spec
    )
    for candidate in [chart_pane, *chart_pane.get("layer", [])]:
        color = candidate.get("encoding", {}).get("color")
        if isinstance(color, dict) and color.get("legend") is not None:
            return True
    return False


def _named_series_counts(spec: dict) -> Counter[str]:
    """How many times each series name is named — by the rail, by the
    legend, or (a bug) both. Zero means unnamed; two means double-named.
    """
    counts: Counter[str] = Counter()
    if "hconcat" in spec:
        counts.update(_labels_in_pane(spec).keys())
    domain = _shared_scale_domain(spec)
    if domain is not None:
        counts.update(domain)
    return counts


def _resolve_and_render(chart, data, *, width=400.0, chart_style_context=None):
    resolved = resolve(
        chart, data, chart_style_context=chart_style_context or _BOARD_CTX, width=width
    )
    artifact = render_resolved_chart(resolved, data, _BOARD_STYLE, width=width)
    assert artifact.kind == "vega_spec"
    return artifact.payload


# --------------------------------------------------------------------------
# One layer, no color encoding — the worksheet's motivating case.
# --------------------------------------------------------------------------


class TestSingleLayerNoColor:
    def test_labels_base_and_layer_endpoints(self, make_chart):
        chart = make_chart(
            "bar",
            x="month",
            y="annual",
            layers=[LineLayer(type="line", y="cumulative", label="Cumulative")],
            style={"endpoint_labels": {"visible": True}},
        )
        spec = _resolve_and_render(chart, _bar_with_layers_data())

        labels = {k.lower(): v for k, v in _labels_in_pane(spec).items()}
        assert labels["annual"] == 5.0  # last row's annual value
        assert labels["cumulative"] == 35.0  # last row's cumulative value

    def test_label_font_style_reaches_mark(self, make_chart):
        """font.style authored on charts.series_label.font reaches the
        layered (chart.layers, no color channel) endpoint-label mark as VL's
        fontStyle — the second endpoint_labels.py call site, distinct from
        the multi-series right_pane path covered in test_render_features.py."""
        compiled = get_theme_style()
        italic_series_label = compiled.charts.series_label.model_copy(
            update={
                "font": compiled.charts.series_label.font.model_copy(
                    update={"style": "italic"}
                )
            }
        )
        charts = compiled.charts.model_copy(
            update={"series_label": italic_series_label}
        )
        board_style, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )

        chart = make_chart(
            "bar",
            x="month",
            y="annual",
            layers=[LineLayer(type="line", y="cumulative", label="Cumulative")],
            style={"endpoint_labels": {"visible": True}},
        )
        spec = _resolve_and_render(
            chart, _bar_with_layers_data(), chart_style_context=board_ctx
        )
        assert "hconcat" in spec, "endpoint-label pane did not fire"
        assert spec["hconcat"][1]["mark"]["fontStyle"] == "italic"

    @pytest.mark.parametrize("width", [400.0, 700.0, 1200.0])
    def test_fires_at_multiple_widths(self, make_chart, width):
        chart = make_chart(
            "bar",
            x="month",
            y="annual",
            layers=[LineLayer(type="line", y="cumulative", label="Cumulative")],
            style={"endpoint_labels": {"visible": True}},
        )
        spec = _resolve_and_render(chart, _bar_with_layers_data(), width=width)
        labels = _labels_in_pane(spec)
        assert len(labels) == 2


# --------------------------------------------------------------------------
# Several layers, no color encoding.
# --------------------------------------------------------------------------


class TestSeveralLayersNoColor:
    def test_labels_every_layer_endpoint(self, make_chart):
        data = [
            {"month": "Jan", "revenue": 10.0, "cost": 4.0, "target": 12.0},
            {"month": "Feb", "revenue": 20.0, "cost": 9.0, "target": 18.0},
            {"month": "Mar", "revenue": 15.0, "cost": 6.0, "target": 20.0},
        ]
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            layers=[
                AreaLayer(type="area", y="cost", label="Cost"),
                BarLayer(type="bar", y="target", label="Target"),
            ],
            style={"endpoint_labels": {"visible": True}},
        )
        spec = _resolve_and_render(chart, data)

        labels = _labels_in_pane(spec)
        assert labels.keys() >= {"Cost", "Target"}
        assert labels["Cost"] == 6.0
        assert labels["Target"] == 20.0
        assert len(labels) == 3  # base + 2 overlays


# --------------------------------------------------------------------------
# Default (not opted in): layers keep the legend, never the rail.
# --------------------------------------------------------------------------


def test_layered_chart_without_opt_in_stays_unlabeled(make_chart):
    chart = make_chart(
        "bar",
        x="month",
        y="annual",
        layers=[LineLayer(type="line", y="cumulative")],
    )
    spec = _resolve_and_render(chart, _bar_with_layers_data())
    assert "hconcat" not in spec


# --------------------------------------------------------------------------
# With a genuine base color-series channel, the existing rail path still
# fires (base series only) — regression guard, unaffected by this fix.
# --------------------------------------------------------------------------


def test_layered_chart_with_color_encoding_still_fires_base_rail(make_chart):
    data = [
        {"month": "Jan", "annual": 10.0, "series": "A", "cumulative": 10.0},
        {"month": "Feb", "annual": 20.0, "series": "A", "cumulative": 30.0},
        {"month": "Jan", "annual": 4.0, "series": "B", "cumulative": 4.0},
        {"month": "Feb", "annual": 8.0, "series": "B", "cumulative": 12.0},
    ]
    chart = make_chart(
        "bar",
        x="month",
        y="annual",
        color="series",
        layers=[LineLayer(type="line", y="cumulative")],
        style={"endpoint_labels": {"visible": True}},
    )
    spec = _resolve_and_render(chart, data)
    assert "hconcat" in spec


def test_layered_stacked_bar_endpoint_ink_matches_base_stack_palette(make_chart):
    data = [
        {"month": "Jan", "annual": 1.0, "series": "A", "cumulative": 1.0},
        {"month": "Feb", "annual": 1.0, "series": "A", "cumulative": 2.0},
        {"month": "Jan", "annual": 5.0, "series": "B", "cumulative": 5.0},
        {"month": "Feb", "annual": 5.0, "series": "B", "cumulative": 10.0},
    ]
    chart = make_chart(
        "bar",
        x="month",
        y="annual",
        color="series",
        stack="zero",
        layers=[LineLayer(type="line", y="cumulative")],
        style={"endpoint_labels": {"visible": True}},
    )

    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX, width=400.0)
    artifact = render_resolved_chart(resolved, data, _BOARD_STYLE, width=400.0)
    assert artifact.kind == "vega_spec"
    spec = artifact.payload
    chart_pane = spec["hconcat"][0]
    base_scale = next(
        layer["encoding"]["color"]["scale"]
        for layer in chart_pane["layer"]
        if isinstance(layer.get("encoding", {}).get("color"), dict)
        and isinstance(layer["encoding"]["color"].get("scale"), dict)
        and set(layer["encoding"]["color"]["scale"].get("domain", [])) >= {"A", "B"}
    )
    rail_scale = spec["hconcat"][1]["encoding"]["color"]["scale"]

    base_fill = dict(zip(base_scale["domain"], base_scale["range"], strict=True))
    rail_ink = dict(zip(rail_scale["domain"], rail_scale["range"], strict=True))
    assert base_fill["B"] == resolved.palette[0]
    assert base_fill["A"] == resolved.palette[1]
    assert rail_ink == {
        "B": resolved.style.series_label.dark_companion_palette[0],
        "A": resolved.style.series_label.dark_companion_palette[1],
    }


# --------------------------------------------------------------------------
# A layer pinning its own axis_y.position is a genuine dual-axis chart —
# the rail anchors on one shared y-scale, so this must raise, not mislabel.
# --------------------------------------------------------------------------


def test_dual_axis_layer_raises(make_chart):
    from dbt_charts.core.diagnostics.codes_render import (
        ERR_LAYER_AXIS_POSITION_ENDPOINT_LABELS,
    )

    chart = make_chart(
        "bar",
        x="month",
        y="annual",
        layers=[
            LineLayer(
                type="line",
                y="cumulative",
                label="Cumulative",
                axis_y={"position": "right"},
            )
        ],
        style={"endpoint_labels": {"visible": True}},
    )
    with pytest.raises(ChartDataError) as exc_info:
        _resolve_and_render(chart, _bar_with_layers_data())
    err = exc_info.value
    assert err.code is ERR_LAYER_AXIS_POSITION_ENDPOINT_LABELS
    assert "style.endpoint_labels.visible" in str(err)


# --------------------------------------------------------------------------
# Review round 1 regressions: legend suppression, rail ink, collision domain.
# --------------------------------------------------------------------------


def test_layered_endpoint_labels_suppress_legend(make_chart):
    """The rail firing must retire the legend, or every series is named twice
    (once by the rail, once by the legend) — the exact double-encoding the
    rail exists to remove.
    """
    chart = make_chart(
        "bar",
        x="month",
        y="annual",
        layers=[LineLayer(type="line", y="cumulative", label="Cumulative")],
        style={"endpoint_labels": {"visible": True}},
    )
    spec = _resolve_and_render(chart, _bar_with_layers_data())
    assert "hconcat" in spec, "endpoint-label rail did not fire"
    chart_pane = spec["hconcat"][0]
    color_legends = [
        layer["encoding"]["color"].get("legend")
        for layer in chart_pane.get("layer", [])
        if isinstance(layer.get("encoding", {}).get("color"), dict)
    ]
    assert color_legends, "expected at least one color-encoded layer in the pane"
    assert all(legend is None for legend in color_legends), (
        f"legend not suppressed while the endpoint-label rail fires: "
        f"{color_legends!r} — every series is named twice."
    )


def test_layered_endpoint_label_ink_matches_authored_mark_color(make_chart):
    """The rail's ink for a layer must match what that layer actually paints.

    An authored ``style.marks.line.stroke.color`` is not a palette slot — the
    rail must read the emitter's own shared color scale rather than
    re-derive a positional palette slice that can only drift from it.
    """
    chart = make_chart(
        "bar",
        x="month",
        y="annual",
        layers=[
            LineLayer(
                type="line",
                y="cumulative",
                label="Cumulative",
                style={"marks": {"line": {"stroke": {"color": "#ff0000"}}}},
            )
        ],
        style={"endpoint_labels": {"visible": True}},
    )
    spec = _resolve_and_render(chart, _bar_with_layers_data())
    assert "hconcat" in spec
    pane_color = spec["hconcat"][1]["encoding"]["color"]
    pane_scale = pane_color["scale"]
    rail_ink = dict(zip(pane_scale["domain"], pane_scale["range"], strict=True))
    assert rail_ink["Cumulative"] == "#ff0000", (
        f"rail painted 'Cumulative' {rail_ink['Cumulative']!r}, expected the "
        f"authored mark color '#ff0000' — the rail's ink must match what "
        f"the line layer actually renders, not a positional palette slot."
    )


def test_layered_endpoint_labels_collision_cascade_uses_shared_domain(make_chart):
    """The collision cascade must space labels on the rendered shared scale
    (base ∪ every layer), not the base column's own narrow range — else two
    overlay endpoints far outside the base's range look trivially separated
    on the (much narrower) base-only domain and overprint on the real one.

    The cascade now runs post-probe (``recascade_endpoint_labels``), not at
    spec-build time — render through ``render_vega_spec`` to see it fire.
    """
    from dbt_charts.core.render.converters.chart import render_vega_spec

    data = [
        {"month": "Jan", "annual": 0.1, "cum1": 500.0, "cum2": 501.0},
        {"month": "Feb", "annual": 0.5, "cum1": 500.0, "cum2": 501.0},
        {"month": "Mar", "annual": 1.0, "cum1": 500.0, "cum2": 501.0},
    ]
    chart = make_chart(
        "bar",
        x="month",
        y="annual",
        layers=[
            LineLayer(type="line", y="cum1", label="Cum1"),
            LineLayer(type="line", y="cum2", label="Cum2"),
        ],
        style={"endpoint_labels": {"visible": True}},
    )
    spec = _resolve_and_render(chart, data)
    render_vega_spec(
        spec,
        "svg",
        _BOARD_STYLE,
        width=400,
        height=300,
        is_placeholder=False,
        chart_id="chart",
    )
    labels = _labels_in_pane(spec)
    gap = abs(labels["Cum1"] - labels["Cum2"])
    assert gap > 5.0, (
        f"Cum1/Cum2 endpoints are only 1.0 apart in data units, and the "
        f"shared y-scale spans roughly base(0.1-1.0) union layers(500-501) — "
        f"the collision cascade must widen their spacing on that ~500-unit "
        f"scale, not the narrow base-only (0.1-1.0) domain. Got gap={gap!r}, "
        f"expected the cascade to have pushed them apart."
    )


def test_layered_endpoint_labels_cascade_uses_zero_anchored_domain(make_chart):
    """The cascade must space labels on the domain VL actually renders — a
    zero-anchored bar's baked ``domainMin: 0``/headroom-applied ``domainMax``
    — not a raw min/max over the rows, which is far narrower than the
    rendered scale whenever the data floor sits well above zero.

    The cascade now runs post-probe (``recascade_endpoint_labels``), not at
    spec-build time — render through ``render_vega_spec`` to see it fire.
    """
    from dbt_charts.core.render.converters.chart import render_vega_spec

    data = [
        {"month": "Jan", "annual": 500.0, "cum1": 500.0, "cum2": 501.0},
        {"month": "Feb", "annual": 520.0, "cum1": 500.0, "cum2": 501.0},
        {"month": "Mar", "annual": 510.0, "cum1": 600.0, "cum2": 604.0},
    ]
    chart = make_chart(
        "bar",
        x="month",
        y="annual",
        layers=[
            LineLayer(type="line", y="cum1", label="Cum1"),
            LineLayer(type="line", y="cum2", label="Cum2"),
        ],
        style={"endpoint_labels": {"visible": True}},
    )
    spec = _resolve_and_render(chart, data, width=400.0)
    render_vega_spec(
        spec,
        "svg",
        _BOARD_STYLE,
        width=400,
        height=300,
        is_placeholder=False,
        chart_id="chart",
    )
    labels = _labels_in_pane(spec)
    gap = abs(labels["Cum1"] - labels["Cum2"])
    # On the raw min/max-over-rows domain (500..604, span 104) the cascade's
    # nudge is ~7 data units — barely more than Cum1/Cum2's raw 4-unit
    # separation. On the real zero-anchored, headroom-applied domain
    # (0..~620, span ~620) the same pixel target is a ~40-unit nudge.
    assert gap > 20.0, (
        f"Cum1 (600) and Cum2 (604) ended up only {gap!r} data units apart — "
        f"a zero-anchored bar's rendered y-scale spans roughly 0..620, not "
        f"the raw row range 500..604, so the collision cascade should have "
        f"pushed them apart by ~40 units, not ~7."
    )


# --------------------------------------------------------------------------
# Review round 2 regressions: color-channel crash, over-suppressed legend,
# duplicate-label collapse.
# --------------------------------------------------------------------------


class TestNonSeriesColorChannelDoesNotCrash:
    """CRITICAL: a layered chart whose base color channel is NOT a nominal/
    ordinal series (gradient) has no shared color scale for the layered
    rail to read — it must decline the rail, not raise.
    """

    def test_gradient_color_declines_rail(self, make_chart):
        data = [
            {"month": "Jan", "annual": 10.0, "cumulative": 10.0},
            {"month": "Feb", "annual": 20.0, "cumulative": 30.0},
        ]
        chart = make_chart(
            "bar",
            x="month",
            y="annual",
            color="annual",
            layers=[LineLayer(type="line", y="cumulative", label="Cumulative")],
            style={
                "endpoint_labels": {"visible": True},
                "color": {"gradient": {"palette": ["#fff", "#000"]}},
            },
        )
        spec = _resolve_and_render(chart, data)
        assert "hconcat" not in spec, (
            "a gradient color channel has no shared color scale to paint "
            "the layered rail from — the rail must decline, not fire (and "
            "must not raise ChartDataError)."
        )


class TestLegendNotOverSuppressed:
    """CRITICAL: legend suppression must only fire when the rail actually
    names every series that would otherwise need the legend — never leaving
    a series with zero names.
    """

    def test_base_color_series_with_layer_keeps_layer_named(self, make_chart):
        """A genuine base color-series channel routes apply() into the
        multi-series branch, which never looks at chart.layers at all — so
        an overlay layer's own series is never in the rail. The legend must
        stay on to name it.
        """
        data = [
            {"month": "Jan", "annual": 10.0, "series": "A", "cumulative": 10.0},
            {"month": "Feb", "annual": 20.0, "series": "A", "cumulative": 30.0},
            {"month": "Jan", "annual": 4.0, "series": "B", "cumulative": 4.0},
            {"month": "Feb", "annual": 8.0, "series": "B", "cumulative": 12.0},
        ]
        chart = make_chart(
            "bar",
            x="month",
            y="annual",
            color="series",
            layers=[LineLayer(type="line", y="cumulative", label="Cumulative")],
            style={"endpoint_labels": {"visible": True}},
        )
        spec = _resolve_and_render(chart, data)
        assert "hconcat" in spec
        domain = _shared_scale_domain(spec)
        assert domain is not None, "legend must stay on to name 'Cumulative'"
        assert "Cumulative" in domain

    def test_layer_with_own_color_field_keeps_subseries_named(self, make_chart):
        """A layer authoring its own ``color:`` splits into several
        sub-series with no single endpoint for the rail to anchor. Rather
        than naming the base + colorless layers on the rail and leaving
        that one sub-series to the legend (which named the base a second
        time — round-2's double-naming regression), the whole rail falls
        back to legend-only: nothing fires, and the legend alone names
        every series exactly once.
        """
        data = [
            {"month": "Jan", "annual": 10.0, "cumulative": 10.0, "cumseries": "X"},
            {"month": "Feb", "annual": 20.0, "cumulative": 30.0, "cumseries": "Y"},
        ]
        chart = make_chart(
            "bar",
            x="month",
            y="annual",
            layers=[
                LineLayer(
                    type="line", y="cumulative", label="Cumulative", color="cumseries"
                )
            ],
            style={"endpoint_labels": {"visible": True}},
        )
        spec = _resolve_and_render(chart, data)
        assert "hconcat" not in spec, (
            "a layer with its own color field can't be named on the rail — "
            "the whole layered rail must decline, not partially fire."
        )
        domain = _shared_scale_domain(spec)
        assert domain is not None, "legend must stay on to name every series"
        assert {"X", "Y"} <= set(domain)
        counts = _named_series_counts(spec)
        assert counts["annual"] == 1, (
            f"the base series must be named exactly once (by the legend, "
            f"since the rail never fired) — got {counts!r}"
        )

    def test_horizontal_grouped_bar_keeps_legend(self, make_chart):
        """A horizontal bar's rail only ever fires when stacked
        (``EndpointLabelFeature.applies_to()``'s horizontal branch) — a
        grouped (``stack: none``) horizontal bar never gets a rail even with
        a color series and an explicit opt-in, so the legend must stay on.
        """
        data = [
            {"cat": "A", "value": 10.0, "series": "X"},
            {"cat": "B", "value": 20.0, "series": "X"},
            {"cat": "A", "value": 4.0, "series": "Y"},
            {"cat": "B", "value": 8.0, "series": "Y"},
        ]
        chart = make_chart(
            "bar",
            x="cat",
            y="value",
            color="series",
            style={
                "orientation": "horizontal",
                "endpoint_labels": {"visible": True},
                "bar": {"stack": "none"},
            },
        )
        spec = _resolve_and_render(chart, data)
        assert "hconcat" not in spec and "vconcat" not in spec, (
            "grouped horizontal has no rail"
        )
        assert _legend_visible(spec), "no rail fired — the legend must stay on"

    def test_horizontal_stacked_bar_with_layer_keeps_overlay_named(self, make_chart):
        """A horizontal stacked bar's color-series rail (top_rail) only ever
        names the color series, never a ``chart.layers`` overlay —
        ``endpoint_label_has_layers`` is zeroed for horizontal (the layered
        rail has no horizontal path), but that same zeroed flag must not be
        read as "no layer exists" by the legend-suppression guard, or the
        overlay layer's own color renders with no legend and no rail entry:
        completely anonymous.
        """
        data = [
            {"cat": "A", "value": 10.0, "series": "X", "target": 3.0},
            {"cat": "B", "value": 20.0, "series": "X", "target": 4.0},
            {"cat": "A", "value": 4.0, "series": "Y", "target": 1.0},
            {"cat": "B", "value": 8.0, "series": "Y", "target": 2.0},
        ]
        chart = make_chart(
            "bar",
            x="cat",
            y="value",
            color="series",
            layers=[LineLayer(type="line", y="target", label="Overlay")],
            style={
                "orientation": "horizontal",
                "endpoint_labels": {"visible": True},
                "bar": {"stack": "zero"},
            },
        )
        spec = _resolve_and_render(chart, data)
        assert "vconcat" in spec, "the color-series top_rail must still fire"
        assert _legend_visible(spec), (
            "the top_rail only names the color series, never the overlay — "
            "the legend must stay on to name 'Overlay', or it is unnamed "
            "anywhere on the chart."
        )


@pytest.mark.parametrize(
    "layers",
    [
        [LineLayer(type="line", y="cumulative", label="Cumulative")],
        [
            AreaLayer(type="area", y="cost", label="Cost"),
            BarLayer(type="bar", y="target", label="Target"),
        ],
    ],
    ids=["one_layer", "several_layers"],
)
def test_every_series_named_exactly_once(make_chart, layers):
    """The core shape this task adds — no base color channel, every layer
    colorless — must name every series exactly once: not zero (the round-2
    crash/over-suppression regressions), not twice (round 1's double-naming
    finding). This is the single assertion that pins both directions.
    """
    data = [
        {
            "month": "Jan",
            "annual": 10.0,
            "cumulative": 10.0,
            "cost": 4.0,
            "target": 12.0,
        },
        {
            "month": "Feb",
            "annual": 20.0,
            "cumulative": 30.0,
            "cost": 9.0,
            "target": 18.0,
        },
        {
            "month": "Mar",
            "annual": 5.0,
            "cumulative": 35.0,
            "cost": 6.0,
            "target": 20.0,
        },
    ]
    chart = make_chart(
        "bar",
        x="month",
        y="annual",
        layers=layers,
        style={"endpoint_labels": {"visible": True}},
    )
    spec = _resolve_and_render(chart, data)
    assert "hconcat" in spec, "endpoint-label rail did not fire"
    counts = _named_series_counts(spec)
    expected = {"Annual", *(layer.label for layer in layers)}
    named = {k.title() for k in counts}
    assert named == expected, f"expected {expected}, got {named} ({counts!r})"
    assert all(n == 1 for n in counts.values()), (
        f"every series must be named exactly once — got {counts!r}"
    )


def test_duplicate_layer_label_raises(make_chart):
    """Two entries in the shared color scale sharing the same label collide
    silently otherwise: the rail's label-keyed anchor dict collapses one
    entry's position, and the legend gets a scale with a duplicated domain
    value. Must raise at emit time instead — validate and error fast.
    """
    chart = make_chart(
        "bar",
        x="month",
        y="annual",
        y_label="Cumulative",
        layers=[LineLayer(type="line", y="cumulative", label="Cumulative")],
        style={"endpoint_labels": {"visible": True}},
    )
    with pytest.raises(ChartDataError, match="collides with an existing color scale"):
        _resolve_and_render(chart, _bar_with_layers_data())


# --------------------------------------------------------------------------
# Round-3 CRITICAL regression: the collision raise must be scoped to the
# layered endpoint-label rail (where a colliding label silently collapses
# the label-keyed anchors dict) — not fire on every layered chart with no
# rail at all, which is ordinary authoring (bars + a same-measure trend
# line; two layers sharing one label with no endpoint_labels opt-in).
# --------------------------------------------------------------------------


def test_layer_label_matching_base_y_title_renders_without_endpoint_labels(make_chart):
    """A trend-line layer whose label equals the base y title (bars plus a
    line of the same measure) must not raise when endpoint labels are never
    opted in — the collision only matters to a rail that never fires.
    """
    chart = make_chart(
        "bar",
        x="month",
        y="annual",
        layers=[LineLayer(type="line", y="annual", label="Annual")],
    )
    spec = _resolve_and_render(chart, _bar_with_layers_data())
    assert "hconcat" not in spec, "no rail fires with no endpoint_labels opt-in"


def test_layers_sharing_a_label_render_without_endpoint_labels(make_chart):
    """Two layers authoring the same label (e.g. a line with point markers,
    both labeled "Target") must not raise when endpoint labels are never
    opted in — the shared color scale just reuses one legend slot for both.
    """
    chart = make_chart(
        "bar",
        x="month",
        y="annual",
        layers=[
            LineLayer(type="line", y="cumulative", label="Target"),
            BarLayer(type="bar", y="cumulative", label="Target"),
        ],
    )
    spec = _resolve_and_render(chart, _bar_with_layers_data())
    assert "hconcat" not in spec, "no rail fires with no endpoint_labels opt-in"
