"""TDD: baseline-anchored invisible hover band on bar charts.

Every bar chart (vertical, horizontal, grouped, stacked, mixed-sign) gets an
invisible companion bar mark anchored at scale('ch', 0) extending by
``constants.bar.HOVER_BAND_EXTEND_FRACTION`` of the plot extent toward the
sign of each datum's value.

This widens the hover hit-target for near-zero bars without touching the rendered visual.

Hover band properties:
- mark="bar", mark_props={"opacity": 0, "style": "dct-hover-band"} -- no aria
  override, no data/transform of its own; inherits spec.data and the shared
  structured-tooltip description like any ordinary sub-layer, so it gets its
  own correct aria-label (see module docstring in bar_hover_band.py for why
  private data and a transform were both tried and rejected).
- measure_ch: {"value": {"expr": "scale('ch', 0)"}}
- measure_ch+2: {"value": {"expr": "isValid(datum['field']) ? (scale('ch', 0)
  + (datum['field'] >= 0 ? +/-band : -/+band)) : scale('ch', 0)"}} -- a null
  measure collapses the band to zero extent via this same ternary, not via a
  transform or private data.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.constants.bar import (
    HOVER_BAND_EXTEND_FRACTION,
    HOVER_BAND_TRIGGER_FRACTION,
)
from dbt_charts.core.render.chart.features.bar_hover_band import (
    _HOVER_BAND_STYLE,
    _append_bar_hover_area,
)
from dbt_charts.core.render.chart.spec import ChartSpec
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_POS_DATA = [{"cat": "A", "val": 100}, {"cat": "B", "val": 1}]
_MIX_DATA = [{"cat": "A", "val": 100}, {"cat": "B", "val": -1}]
_STACKED_DATA = [
    {"month": "Jan", "series": "up", "val": 5},
    {"month": "Jan", "series": "down", "val": -3},
    {"month": "Feb", "series": "up", "val": 7},
    {"month": "Feb", "series": "down", "val": -2},
]
# Feb's positive stacked total (2) is under 10% of Jan's (500) -- Feb is the
# "tiny total" category; Jan is not.
_STACKED_TINY_DATA = [
    {"month": "Jan", "series": "A", "val": 100},
    {"month": "Jan", "series": "B", "val": 400},
    {"month": "Feb", "series": "A", "val": 1},
    {"month": "Feb", "series": "B", "val": 1},
]


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset_config()
    monkeypatch.setenv("DCT_DEFAULT_THEME", "stark")
    yield
    reset_config()


def _bar_spec(
    data: list[dict[str, Any]],
    orientation: str = "vertical",
) -> dict[str, Any]:
    style = BarChartStylePatch.model_validate({"orientation": orientation})
    chart = BarChart(
        id="t",
        type="bar",
        x="cat",
        y="val",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
        style=style,
    )
    return generate_vega_lite_spec(chart, data, width=400)


def _hover_band_layers(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find hover band sub-layers by their style marker."""
    return [
        la
        for la in layers
        if isinstance(la.get("mark"), dict)
        and la["mark"].get("style") == _HOVER_BAND_STYLE
    ]


def _chart_pane(spec: dict[str, Any]) -> dict[str, Any]:
    """The main chart pane, unwrapping the legend hconcat a color-bound bar gets.

    A color-bound chart's spec is ``hconcat`` (chart pane at index 0, legend pane
    at index 1); a plain bar chart's spec carries ``layer`` at the top level.
    """
    return spec["hconcat"][0] if "hconcat" in spec else spec


# ── vertical bar ──────────────────────────────────────────────────────────────


def test_vertical_bar_gets_hover_band_layer() -> None:
    """Every vertical bar chart must have exactly one invisible hover band sub-layer."""
    spec = _bar_spec(_POS_DATA)
    layers = spec.get("layer", [])
    bands = _hover_band_layers(layers)
    assert len(bands) == 1, (
        f"Expected exactly one hover band layer in {[la.get('mark') for la in layers]!r}"
    )


def test_vertical_hover_band_y_encoding_is_baseline_expr() -> None:
    """Hover band y encoding must anchor at scale('y', 0) — the measure baseline."""
    spec = _bar_spec(_POS_DATA)
    layers = spec.get("layer", [])
    bands = _hover_band_layers(layers)
    assert len(bands) == 1
    y_enc = bands[0].get("encoding", {}).get("y", {})
    assert y_enc == {"value": {"expr": "scale('y', 0)"}}, (
        f"Hover band y must be {{value: {{expr: \"scale('y', 0)\"}}}}, got {y_enc!r}"
    )


def test_vertical_hover_band_y2_encoding_extends_toward_sign() -> None:
    """Hover band y2 must extend toward datum sign, only when the bar's own
    rendered height is under the band's size -- a null measure or an
    already-adequately-tall bar both collapse to baseline."""
    spec = _bar_spec(_POS_DATA)
    layers = spec.get("layer", [])
    bands = _hover_band_layers(layers)
    assert len(bands) == 1
    y2_enc = bands[0].get("encoding", {}).get("y2", {})
    expected_band = f"height * {HOVER_BAND_EXTEND_FRACTION}"
    trigger_px = f"height * {HOVER_BAND_TRIGGER_FRACTION}"
    baseline = "scale('y', 0)"
    signed = f"{baseline} + (datum['val'] >= 0 ? -{expected_band} : {expected_band})"
    own_extent = f"abs(scale('y', datum['val']) - {baseline})"
    expected_expr = (
        f"isValid(datum['val']) && ({own_extent} < {trigger_px}) "
        f"? ({signed}) : {baseline}"
    )
    assert y2_enc == {"value": {"expr": expected_expr}}, (
        f"Hover band y2 must be {{value: {{expr: {expected_expr!r}}}}}, got {y2_enc!r}"
    )


# ── horizontal bar ────────────────────────────────────────────────────────────


def test_horizontal_bar_gets_hover_band_layer() -> None:
    """Horizontal bar charts must also get exactly one invisible hover band sub-layer."""
    spec = _bar_spec(_POS_DATA, orientation="horizontal")
    layers = spec.get("layer", [])
    bands = _hover_band_layers(layers)
    assert len(bands) == 1, (
        f"Expected exactly one hover band layer for horizontal bar; "
        f"got {[la.get('mark') for la in layers]!r}"
    )


def test_horizontal_hover_band_x_encoding_is_baseline_expr() -> None:
    """Horizontal hover band x encoding must anchor at scale('x', 0)."""
    spec = _bar_spec(_POS_DATA, orientation="horizontal")
    layers = spec.get("layer", [])
    bands = _hover_band_layers(layers)
    assert len(bands) == 1
    x_enc = bands[0].get("encoding", {}).get("x", {})
    assert x_enc == {"value": {"expr": "scale('x', 0)"}}, (
        f"Horizontal hover band x must be {{value: {{expr: \"scale('x', 0)\"}}}}, got {x_enc!r}"
    )


def test_horizontal_hover_band_x2_encoding_extends_toward_sign() -> None:
    """Horizontal hover band x2 must extend rightward for positive, leftward for
    negative, only when the bar's own rendered width is under the band's size."""
    spec = _bar_spec(_POS_DATA, orientation="horizontal")
    layers = spec.get("layer", [])
    bands = _hover_band_layers(layers)
    assert len(bands) == 1
    x2_enc = bands[0].get("encoding", {}).get("x2", {})
    expected_band = f"width * {HOVER_BAND_EXTEND_FRACTION}"
    trigger_px = f"width * {HOVER_BAND_TRIGGER_FRACTION}"
    baseline = "scale('x', 0)"
    signed = f"{baseline} + (datum['val'] >= 0 ? {expected_band} : -{expected_band})"
    own_extent = f"abs(scale('x', datum['val']) - {baseline})"
    expected_expr = (
        f"isValid(datum['val']) && ({own_extent} < {trigger_px}) "
        f"? ({signed}) : {baseline}"
    )
    assert x2_enc == {"value": {"expr": expected_expr}}, (
        f"Horizontal hover band x2 must be {{value: {{expr: {expected_expr!r}}}}}, got {x2_enc!r}"
    )


# ── mixed-sign bar ────────────────────────────────────────────────────────────


def test_mixed_sign_bar_gets_hover_band() -> None:
    """Mixed-sign bars (already two-layered for rounding) must also get a hover band."""
    spec = _bar_spec(_MIX_DATA)
    layers = spec.get("layer", [])
    bands = _hover_band_layers(layers)
    assert len(bands) == 1, (
        f"Expected exactly one hover band layer on mixed-sign bar spec; "
        f"got {[la.get('mark') for la in layers]!r}"
    )


def test_mixed_sign_hover_band_sign_check_uses_datum_field() -> None:
    """The hover band expression must reference datum['val'] for sign only, not position.

    Position is always scale('y', 0). This confirms no per-segment stack arithmetic
    is embedded in the expression.
    """
    spec = _bar_spec(_MIX_DATA)
    layers = spec.get("layer", [])
    bands = _hover_band_layers(layers)
    assert len(bands) == 1
    y2_expr = (
        bands[0].get("encoding", {}).get("y2", {}).get("value", {}).get("expr", "")
    )
    assert "datum['val']" in y2_expr, (
        f"Hover band y2 expr must reference datum['val'] for sign check; got {y2_expr!r}"
    )
    assert "scale('y', 0)" in y2_expr, (
        f"Hover band y2 expr must reference scale('y', 0) for position; got {y2_expr!r}"
    )
    # Must NOT embed any stack arithmetic (cumulative sum, window, etc.)
    assert "window" not in y2_expr, "Hover band must not use Vega window/cumsum"
    assert "cumulative" not in y2_expr, "Hover band must not compute cumulative sums"


# ── stacked bar ───────────────────────────────────────────────────────────────


def _stacked_bar_spec(data: list[dict[str, Any]]) -> dict[str, Any]:
    style = BarChartStylePatch.model_validate(
        {"orientation": "vertical", "stack": "zero"}
    )
    chart = BarChart(
        id="t",
        type="bar",
        x="month",
        y="val",
        color="series",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
        style=style,
    )
    return generate_vega_lite_spec(chart, data, width=400)


def test_stacked_bar_without_tiny_total_gets_no_hover_band() -> None:
    """A stacked bar chart with no near-invisible column gets no hover band.

    Every per-x total in ``_STACKED_DATA`` (Jan=5, Feb=7) is well above the
    10% tininess threshold relative to the domain max, so nothing needs the
    extended hit area and no band is added.
    """
    spec = _stacked_bar_spec(_STACKED_DATA)
    layers = _chart_pane(spec).get("layer", [])
    bands = _hover_band_layers(layers)
    assert not bands, (
        f"Stacked bar chart with no tiny-total column must not get a hover "
        f"band; got {bands!r} in {[la.get('mark') for la in layers]!r}"
    )


def test_stacked_bar_with_tiny_total_gets_carrier_hover_band() -> None:
    """A stacked column whose TOTAL renders near-invisible gets one shared band.

    Feb's total (2) is under 10% of Jan's (500), so Feb needs the extended
    hit area. Per-segment precision is unnecessary (chart_interactivity.js's
    x-unified tooltip already shows every series at a given x once ANY mark
    there is hovered) -- what matters is picking exactly ONE carrier row for
    Feb, not one band per series. Two or more overlapping per-series bands at
    Feb would resurrect the wrong-neighbor hit-test/link bug
    (test_stacked_bar_excluded_from_hover_band's original regression) that
    stacked exclusion was added to avoid.

    Jan must NOT be a carrier -- its own segments are already tall enough to
    hover directly.
    """
    spec = _stacked_bar_spec(_STACKED_TINY_DATA)
    layers = _chart_pane(spec).get("layer", [])
    bands = _hover_band_layers(layers)
    assert len(bands) == 1, (
        f"Expected exactly one shared hover band layer for the tiny-total "
        f"stacked chart; got {[la.get('mark') for la in layers]!r}"
    )
    y2_expr = (
        bands[0].get("encoding", {}).get("y2", {}).get("value", {}).get("expr", "")
    )
    assert "'Feb'" in y2_expr, (
        f"Hover band must carry a carrier condition for the tiny 'Feb' "
        f"column; got {y2_expr!r}"
    )
    assert "'Jan'" not in y2_expr, (
        f"Jan's total is not tiny -- it must not be an active carrier; got {y2_expr!r}"
    )
    # Exactly one series row must carry the band for Feb, not both -- else
    # Feb's own two segments would overlap the same wrong-neighbor way the
    # per-series design did.
    assert ("'A'" in y2_expr) != ("'B'" in y2_expr), (
        f"Exactly one series must be the Feb carrier, not zero or both; got {y2_expr!r}"
    )


# ── hover band config ─────────────────────────────────────────────────────────


# ── self-identifying aria (no aria override) ───────────────────────────────────


def test_hover_band_has_no_aria_override() -> None:
    """Hover band must NOT set aria:false -- it must self-identify on hover.

    Regression: an earlier revision set aria:false so the band would be
    "transparent" to chart_interactivity.js and let its mousemove handler's
    nearestDataMark proximity fallback resolve the real bar underneath. That
    fallback picks whichever LABELED mark is geometrically closest to the
    cursor -- for a short bar's hover band sitting next to a much taller
    neighbor, the neighbor's edge is often closer than the short bar's own
    far-away mark. Confirmed by direct browser dispatch: hovering a short
    bar's band surfaced the TALL NEIGHBOR's tooltip instead of its own. The
    band must carry its own aria-label (via the inherited shared description)
    so hover resolves directly, with no proximity guessing at all.
    """
    spec = _bar_spec(_POS_DATA)
    bands = _hover_band_layers(spec.get("layer", []))
    assert len(bands) == 1
    mark = bands[0].get("mark", {})
    assert "aria" not in mark, (
        f"Hover band mark must not override aria -- it needs its own real "
        f"aria-label (inherited description) to self-identify on hover, not "
        f"rely on a proximity fallback; got mark={mark!r}"
    )


# ── null-row handling ────────────────────────────────────────────────────────


def test_band_layer_has_no_private_data() -> None:
    """The hover band layer must carry NO data of its own -- it inherits spec.data.

    Regression: an earlier revision gave the band its own private, pre-filtered
    dataset (null-measure rows removed in Python) specifically to keep those
    rows out of the shared categorical scale domain. That fixed the domain
    leak, but a sub-layer with private data gets its structured-tooltip
    description forced to None by translate.py's "private data means
    incompatible row shape" guard -- which starved the band of its own
    aria-label and forced the aria:false + proximity-fallback approach this
    module now rejects (see test_hover_band_has_no_aria_override). The band
    now handles a null measure via an encoding-only ternary (collapses to
    zero extent) instead, so it needs no private data at all.
    """
    data_with_null = [
        {"cat": "A", "val": 0.5},
        {"cat": "B", "val": None},
        {"cat": "C", "val": 40},
    ]
    spec = _bar_spec(data_with_null)
    bands = _hover_band_layers(spec.get("layer", []))
    assert len(bands) == 1
    assert "data" not in bands[0], (
        f"Band sub-layer must not carry its own data (would null its "
        f"structured-tooltip description); got data={bands[0].get('data')!r}"
    )


def test_null_measure_row_not_filtered_from_outer_spec() -> None:
    """A null-measure row must not be removed from the VL data pipeline.

    The null-guard for phantom hover targets must NOT use a spec-level
    transform filter: that removes the null row from ALL layers, including the
    real bar's categorical encoding, collapsing the x-axis domain and shifting
    adjacent bar positions.
    """
    data_with_null = [
        {"cat": "A", "val": 0.5},
        {"cat": "B", "val": None},
        {"cat": "C", "val": 40},
    ]
    spec = _bar_spec(data_with_null)
    transforms = spec.get("transform", [])
    assert not any(
        isinstance(t.get("filter"), str) and "isValid" in t["filter"]
        for t in transforms
    ), (
        "An isValid filter on spec.transforms drops the null row from ALL layers, "
        f"collapsing the x-axis domain. Got transforms: {transforms!r}"
    )


# ── no sub-layer transform ────────────────────────────────────────────────────


def test_band_sublayer_has_no_transform() -> None:
    """Band sub-layer must carry no transform array.

    A sub-layer transform causes vl-convert to discard the outer spec's
    encoding.sort, forcing alphabetical ordering regardless of the authored
    sort order (documented in features/value_labels.py).
    """
    spec = _bar_spec(_POS_DATA)
    bands = _hover_band_layers(spec.get("layer", []))
    assert len(bands) == 1
    assert "transform" not in bands[0], (
        f"Band sub-layer must not carry a transform array; "
        f"got transform={bands[0].get('transform')!r}"
    )


# ── value label layer survival ────────────────────────────────────────────────


def test_value_label_layers_survive_layered_promotion() -> None:
    """Text label layers from ValueLabelFeature must survive flat→layered promotion.

    When BarHoverBandFeature promotes a flat bar spec to layered, it must
    preserve any overlay layers (text labels) that ran before it in the
    feature pipeline.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    compiled = get_theme_style("clarity")
    new_labels = compiled.charts.marks.bar.labels.model_copy(update={"visible": True})
    new_bar = compiled.charts.marks.bar.model_copy(update={"labels": new_labels})
    new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    board_style, board_ctx = resolve_style_and_context(
        compiled.model_copy(update={"charts": charts})
    )
    chart = BarChart(
        id="t",
        type="bar",
        x="cat",
        y="val",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
        style=BarChartStylePatch.model_validate({}),
    )
    spec = generate_vega_lite_spec(
        chart,
        _POS_DATA,
        width=400,
        board_style=board_style,
        chart_style_context=board_ctx,
    )
    layers = spec.get("layer", [])
    text_layers = [
        la
        for la in layers
        if la.get("mark") == "text"
        or (isinstance(la.get("mark"), dict) and la["mark"].get("type") == "text")
    ]
    assert text_layers, (
        f"Value label text layers must survive flat->layered promotion; "
        f"got layer marks: {[la.get('mark') for la in layers]!r}"
    )


# ── complementary-axis guard ──────────────────────────────────────────────────


def test_guard_no_cat_encoding_is_noop() -> None:
    """_append_bar_hover_area must not promote when cat_ch is absent from encoding.

    The guard fires when the complementary (categorical) axis channel is missing;
    the spec stays flat and unmodified.
    """
    spec = ChartSpec(mark="bar", encoding={"y": {"field": "v", "type": "quantitative"}})
    _append_bar_hover_area(
        spec,
        "y",
        "v",
        [{"v": 1}],
    )
    assert spec.mark == "bar", (
        "Spec must NOT be promoted to layered when cat 'x' is absent"
    )
    assert not spec.layers, "No sub-layers must be appended without a cat encoding"


# ── combo-layers bar excluded ─────────────────────────────────────────────────


def test_combo_layers_bar_excluded_from_hover_band() -> None:
    """A bar chart with chart.layers (combo/overlay) must NOT get the hover band.

    render_cartesian_overlay wraps the spec into its own layered structure;
    injecting a hover band before that wrap breaks the overlay composition.
    """
    style = BarChartStylePatch.model_validate({})
    chart = BarChart(
        id="t",
        type="bar",
        x="cat",
        y="val",
        layers=[{"type": "line", "y": "val"}],
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
        style=style,
    )
    spec = generate_vega_lite_spec(chart, _POS_DATA, width=400)
    layers = spec.get("layer", [])
    bands = _hover_band_layers(layers)
    assert not bands, (
        f"Combo bar chart (chart.layers set) must not get a hover band; "
        f"got {bands!r} in {[la.get('mark') for la in layers]!r}"
    )


def test_multiples_bar_excluded_from_hover_band() -> None:
    """A bar chart with chart.multiples set must NOT get the hover band.

    The band's extent expression uses `height` / `width` VL signals that
    resolve to root-scope values; faceted views use `child_height` /
    `child_width` instead, so the band would have zero extent.
    """
    from dbt_charts.core.compile.models.chart.authored import MultiplesConfig
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    multiples_data = [
        {"cat": "A", "val": 5, "grp": "x"},
        {"cat": "B", "val": 3, "grp": "y"},
    ]
    chart = BarChart(
        id="t",
        type="bar",
        x="cat",
        y="val",
        multiples=MultiplesConfig(rows="grp"),
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
        style=BarChartStylePatch.model_validate({}),
    )
    spec = generate_vega_lite_spec(chart, multiples_data, width=400)
    # Faceted specs use "facet"/"spec" at the top level, not "layer".
    top_level_layers = spec.get("layer", [])
    inner_layers = spec.get("spec", {}).get("layer", []) if "spec" in spec else []
    all_layers = top_level_layers + inner_layers
    bands = _hover_band_layers(all_layers)
    assert not bands, (
        f"Faceted bar chart (chart.multiples set) must not get a hover band; "
        f"got {bands!r}"
    )


# ── href link regression ──────────────────────────────────────────────────────


def test_hover_band_inherits_href_encoding_correctly() -> None:
    """Hover band sub-layer must inherit the SAME working href as its real sibling.

    A bar chart with chart.link set gets a calculate transform (producing the
    href field) plus encoding.href on the OUTER/shared encoding. The band has
    no private data or encoding.href override of its own, so it inherits both
    the calculate transform's output field AND the href channel exactly like
    its real sibling -- confirmed by the ABSENCE of an "href" key in the
    band's own encoding (VL layer merge: an absent key means "use the shared
    one", not "no href at all").

    Contrast with translate.py::_apply_href_link's explicit-None guard, which
    targets a DIFFERENT sub-layer: one with its own PRIVATE data (e.g.
    BaselineFeature's zero-rule layer), whose rows don't go through the outer
    calculate transform and would otherwise get a broken, undefined-field link.
    """
    chart = BarChart(
        id="t",
        type="bar",
        x="cat",
        y="val",
        link="https://example.com/{{cat}}",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
        style=BarChartStylePatch.model_validate({}),
    )
    spec = generate_vega_lite_spec(chart, _POS_DATA, width=400)
    layers = spec.get("layer", [])
    bands = _hover_band_layers(layers)
    assert len(bands) == 1, (
        f"Expected hover band; got layers={[la.get('mark') for la in layers]!r}"
    )
    band_encoding = bands[0].get("encoding", {})
    assert "href" not in band_encoding, (
        f"Hover band must not override encoding.href -- an absent key means it "
        f"correctly inherits the outer chart's real, working link; an explicit "
        f"None would suppress it entirely. Got encoding={band_encoding!r}"
    )


def test_baseline_rule_layer_href_is_explicitly_suppressed() -> None:
    """The private-data guard _apply_href_link exists for: BaselineFeature's
    zero-rule layer must have encoding.href explicitly set to None.

    Regression: this guard was previously only exercised incidentally by a
    (now-removed) hover band that carried its own private data; once the band
    stopped carrying private data, the guard's only remaining live target was
    the zero-rule layer, and nothing asserted against it directly.
    """
    chart = BarChart(
        id="t",
        type="bar",
        x="cat",
        y="val",
        link="https://example.com/{{cat}}",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
        style=BarChartStylePatch.model_validate({}),
    )
    spec = generate_vega_lite_spec(chart, _POS_DATA, width=400)
    layers = spec.get("layer", [])
    rule_layers = [
        la
        for la in layers
        if isinstance(la.get("mark"), dict) and la["mark"].get("type") == "rule"
    ]
    assert rule_layers, (
        f"Expected a BaselineFeature zero-rule layer; got "
        f"layers={[la.get('mark') for la in layers]!r}"
    )
    for rule in rule_layers:
        assert "data" in rule, (
            f"Rule layer must carry its own private data; got {rule!r}"
        )
        rule_href = rule.get("encoding", {}).get("href")
        assert rule_href is None and "href" in rule.get("encoding", {}), (
            f"Rule layer's private data doesn't go through the outer href "
            f"calculate transform, so its href must be explicitly suppressed "
            f"(present, set to None), not just absent; got "
            f"encoding={rule.get('encoding')!r}"
        )


# ── vl_convert integration ────────────────────────────────────────────────────


def test_bar_hover_band_renders_via_vl_convert() -> None:
    """Hover band renders without VL error, and self-identifies via aria-roledescription.

    The band has no data/transform/aria of its own, so it inherits spec.data
    directly -- there's nothing for it to diverge from (see module docstring
    in bar_hover_band.py: an earlier private-data version needed to reason
    about a raw-vs-transformed-rows split that simply doesn't exist here).
    It carries a real aria-roledescription="bar" alongside its real sibling,
    one pair per row.
    """
    pytest.importorskip("vl_convert")
    import re

    import vl_convert as vlc

    # B is tiny relative to A -- large enough disparity to trigger the band
    # layer at all (not just close-together small values).
    data = [{"cat": "A", "val": 0.01}, {"cat": "B", "val": 1}]
    n_rows = len(data)

    spec = _bar_spec(data)
    svg = vlc.vegalite_to_svg(spec)

    # Real bar + its own hover band, both self-identifying: 2 elements per row.
    bar_elements = re.findall(r'aria-roledescription="bar"', svg)
    assert len(bar_elements) == n_rows * 2, (
        f"Expected {n_rows * 2} bar aria elements (real bar + band per row); "
        f"got {len(bar_elements)}"
    )


def test_hover_band_stays_interactive_on_legend_bearing_chart() -> None:
    """A color-encoded (legend-bearing) bar chart's hover band must not get
    pointer-events:none.

    A bar layer compiled non-interactive paints `pointer-events: none` on the
    WHOLE mark group, so the band would receive no mouse events on exactly the
    multi-series charts most likely to have small segments needing a wider hit
    target.
    """
    pytest.importorskip("vl_convert")
    import re

    import vl_convert as vlc

    # Grouped (stack:none), not stacked -- a stacked+color chart takes the
    # carrier-band path instead (see test_stacked_bar_with_tiny_total_gets_carrier_hover_band),
    # a different code path this test isn't exercising.
    style = BarChartStylePatch.model_validate({"stack": "none"})
    chart = BarChart(
        id="t",
        type="bar",
        x="month",
        y="val",
        color="series",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
        style=style,
    )
    # Deliberately not just abs(_STACKED_DATA): its values (5, 3, 7, 2) are
    # all within the same order of magnitude, and none is tiny enough to
    # trigger the band layer at all under the per-row gate.
    grouped_data = [
        {"month": "Jan", "series": "up", "val": 100},
        {"month": "Jan", "series": "down", "val": 1},
        {"month": "Feb", "series": "up", "val": 90},
        {"month": "Feb", "series": "down", "val": 80},
    ]
    spec = generate_vega_lite_spec(chart, grouped_data, width=400)
    assert _hover_band_layers(spec.get("layer", [])), (
        "Test setup must produce a hover band -- otherwise this test is vacuous"
    )
    svg = vlc.vegalite_to_svg(spec)

    band_groups = re.findall(r'<g class="mark-rect role-mark[^"]*"[^>]*>', svg)
    band_groups = [g for g in band_groups if "layer_" in g]
    assert band_groups, (
        f"Expected mark-rect layer groups in svg; got none. svg={svg[:500]}"
    )
    assert not any('pointer-events="none"' in g for g in band_groups), (
        f"A mark-rect layer group has pointer-events:none on a legend-bearing "
        f"chart -- the hover band (or a real bar layer) is not receiving mouse "
        f"events; got groups={band_groups!r}"
    )


def test_bar_hover_band_pixel_geometry() -> None:
    """A genuinely tiny bar's hover band has height close to
    constants.bar.HOVER_BAND_EXTEND_FRACTION * plot height.

    Two rows: a tall bar sets the axis domain, a tiny bar needs the band --
    a single-row chart can't exercise this, since that row's own value
    defines the domain max and is therefore never "small" relative to it.
    Parses the rendered SVG to find the tiny bar's own invisible band path
    (matched by its aria-label) and measures its actual painted extent.
    Guards against the band silently collapsing to zero (e.g. if the height
    signal resolved to zero).
    """
    pytest.importorskip("vl_convert")
    from xml.etree import ElementTree as ET

    import vl_convert as vlc

    from dbt_charts.core.render.chart.mark_extents import _path_extent

    data = [{"cat": "A", "val": 190}, {"cat": "B", "val": 0.01}]
    spec = _bar_spec(data)

    # Suppress both axis titles so the plot area is exactly the injected
    # height below — axis titles are visible by default now (see the
    # default-axis-titles-casing task) and would otherwise eat into the
    # plot area, decoupling the actual rendered height from the 200px this
    # test controls for. This test is about hover-band fraction math, not
    # axis-title chrome.
    for channel in ("x", "y"):
        axis = spec.get("encoding", {}).get(channel, {}).get("axis")
        if isinstance(axis, dict):
            axis["title"] = None

    # Inject a known height so the 10% check is predictable.
    plot_height = 200.0
    spec = {**spec, "height": plot_height}

    svg = vlc.vegalite_to_svg(spec)
    root = ET.fromstring(svg)

    band_heights: list[float] = []
    for node in root.iter():
        cls = (node.get("class") or "").split()
        if "role-mark" not in cls or "mark-rect" not in cls:
            continue
        for child in node.iter():
            # Hover band paths carry opacity="0"; real bar paths do not.
            if child.get("opacity") != "0":
                continue
            # Only the tiny bar's own band -- the tall bar's band correctly
            # collapses to ~0 (see test_bar_hover_band_collapses_for_adequately_sized_bar).
            if "0.01" not in (child.get("aria-label") or ""):
                continue
            extent = _path_extent(child)
            if extent is not None:
                _w, h = extent
                band_heights.append(h)

    assert band_heights, (
        "Expected the tiny bar's opacity=0 hover band path in the rendered SVG"
    )
    extend_fraction = HOVER_BAND_EXTEND_FRACTION
    expected = plot_height * extend_fraction
    tolerance = expected * 0.15  # 15% tolerance for VL padding/rounding
    for h in band_heights:
        assert abs(h - expected) <= tolerance, (
            f"Hover band height {h:.2f}px does not match expected "
            f"{expected:.2f}px (±{tolerance:.2f}); plot_height={plot_height}"
        )


def test_bar_chart_with_no_tiny_bars_gets_no_hover_band_layer_at_all() -> None:
    """A chart where NOTHING is tiny gets NO hover band layer -- not just a
    collapsed one.

    This is the actual "small bars only" contract: for a normal bar chart,
    the spec (and therefore the rendered SVG) must be identical to a chart
    that never had this feature at all. Merely collapsing the band's geometry
    to zero extent isn't enough -- Vega still emits an invisible zero-area
    path per row for a declared layer, which is markup with no hover value
    and would still show up as noise in every visual golden diff. So the
    layer itself must not be declared when no row in the chart's data is
    tiny relative to the chart's own approximate value range.
    """
    data = [{"cat": "A", "val": 100}, {"cat": "B", "val": 90}]
    spec = _bar_spec(data)
    layers = spec.get("layer", [])
    bands = _hover_band_layers(layers)
    assert not bands, (
        f"Chart with no tiny bars must get no hover band layer at all; "
        f"got {bands!r} in {[la.get('mark') for la in layers]!r}"
    )


def test_bar_hover_band_collapses_for_adequately_sized_bar() -> None:
    """In a chart that DOES get a band layer (because some row is tiny), the
    adequately-sized row's own extent must still be ~0, not
    constants.bar.HOVER_BAND_EXTEND_FRACTION * plot height.

    This is the per-row half of the "small bars only" contract: the band's
    geometry is gated on each row's own rendered extent (via scale()), not
    applied unconditionally to every row once the layer exists.
    """
    pytest.importorskip("vl_convert")
    from xml.etree import ElementTree as ET

    import vl_convert as vlc

    from dbt_charts.core.render.chart.mark_extents import _path_extent

    # B is tiny relative to A, so the layer gets declared; A's own row is far
    # above the 10% collapse threshold and must not extend.
    data = [{"cat": "A", "val": 190}, {"cat": "B", "val": 0.01}]
    spec = _bar_spec(data)

    plot_height = 200.0
    spec = {**spec, "height": plot_height}

    svg = vlc.vegalite_to_svg(spec)
    root = ET.fromstring(svg)

    band_heights: list[float] = []
    for node in root.iter():
        cls = (node.get("class") or "").split()
        if "role-mark" not in cls or "mark-rect" not in cls:
            continue
        for child in node.iter():
            if child.get("opacity") != "0":
                continue
            # Only A's own band -- B's own band correctly extends (see
            # test_bar_hover_band_pixel_geometry).
            if "190" not in (child.get("aria-label") or ""):
                continue
            extent = _path_extent(child)
            if extent is not None:
                _w, h = extent
                band_heights.append(h)

    assert band_heights, "Expected A's opacity=0 hover band path in the rendered SVG"
    extend_fraction = HOVER_BAND_EXTEND_FRACTION
    collapse_threshold = plot_height * extend_fraction * 0.15
    for h in band_heights:
        assert h <= collapse_threshold, (
            f"Hover band height {h:.2f}px must collapse to ~0 for an "
            f"adequately-sized bar (threshold {collapse_threshold:.2f}px) -- "
            f"the band is adding an extended hit area that isn't needed"
        )
