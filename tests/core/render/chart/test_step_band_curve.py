"""TDD tests for the band-aware ``step`` curve + ``connect`` toggle on line marks.

On a categorical (nominal/ordinal) x-axis, ``curve: step`` draws a full-band-width
plateau per x-value (a target overlay that spans the bars it compares against),
instead of VL's native centered ``step`` which anchors line points at band
centers. The render layer implements this by doubling each row to the band's two
edges via an ``xOffset`` point scale (range ``[0, bandwidth('x')]``) — VL has no
per-row ``bandPosition``, so the band-spanning lives in an ``xOffset`` channel
bound to a synthetic edge column. VL itself only ever sees a ``step-after``
interpolation over the doubled rows, so the categorical x-domain is never
expanded (the axis stays clean).

On a continuous (temporal/quantitative) x-axis, ``curve: step`` is NOT
band-aware — it falls straight through to Vega-Lite's own native ``step``
interpolate over the un-doubled rows. No row doubling, no error.

``connect: false`` (band mode only) adds a ``detail`` channel keyed on the x
field so each band is its own path — disconnected plateaus, no vertical
bridges (the bullet-chart target-marker pattern) — and insets the xOffset
range so adjacent plateaus at a similar y don't optically weld into one bar.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    Chart,
    LineChart,
)
from dbt_charts.core.compile.models.primitives import StrokeStyle
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.step_band import STEP_BAND_EDGE_FIELD
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_V2_QUERY_REGISTRY: dict[str, Any] = {"q": SqlQuery(sql="SELECT 1", source="test")}


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _v2_spec(
    chart_def: dict[str, Any], data: list[dict[str, Any]], board
) -> dict[str, Any]:
    """Generate the render-v2 Vega-Lite spec straight off an authored chart dict.

    Mirrors production: compile.normalize.charts → resolve → BoardRenderSession.
    board is a (ResolvedStyle, ChartStyleContext) tuple from _board_with_mark().
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.render.chart.session import BoardRenderSession

    board_rs, board_ctx = board
    compiled = normalize_chart("v2chart", chart_def, _V2_QUERY_REGISTRY, sources={})
    resolved = resolve(compiled, data, chart_style_context=board_ctx)
    session = BoardRenderSession.create(board_rs)
    return session.finalize_vl(
        session.emit_chart(resolved, _DEFAULT_BOX, {resolved.query_name: data})
    )


# 6 bands so plateaus + jumps are unambiguous.
BARS = [
    {"month": "Jan", "actual": 30, "target": 45},
    {"month": "Feb", "actual": 55, "target": 45},
    {"month": "Mar", "actual": 20, "target": 50},
    {"month": "Apr", "actual": 45, "target": 50},
    {"month": "May", "actual": 38, "target": 40},
    {"month": "Jun", "actual": 60, "target": 40},
]
# Numeric x → quantitative (continuous), one of the two non-band VL types.
CONTINUOUS = [
    {"day": 1, "actual": 30, "target": 45},
    {"day": 2, "actual": 55, "target": 45},
    {"day": 3, "actual": 20, "target": 50},
]
# ISO-date-shaped x (a typical monthly series) — resolves as a BUCKETED_CALENDAR_UNITS
# grain via time_unit detection, distinct from BARS's plain "Jan"/"Feb" labels which
# never exercise that path. Regression fixture for the line/area-always-temporal
# branch colliding with step-band's band-x requirement.
MONTHLY_DATES = [
    {"month": "2024-01-01", "actual": 30, "target": 45},
    {"month": "2024-02-01", "actual": 55, "target": 45},
    {"month": "2024-03-01", "actual": 20, "target": 50},
]


# ── Board builders: set curve/connect on a family mark style ──────────────────


def _board_with_mark(family: str, **mark_overrides: Any):
    """Resolve a board with the given family's mark style overridden.

    family is "line" or "area"; mark_overrides go onto charts.<family>.marks.<family>.
    halo_multiplier=0 keeps the spec to a single foreground mark for easy assertions.

    Returns (ResolvedStyle, ChartStyleContext) — unpack both at each call site.
    """
    compiled = get_theme_style("clarity")
    fam_style = getattr(compiled.charts, family)
    base_mark = getattr(fam_style.marks, family)
    new_mark = base_mark.model_copy(update={"halo_multiplier": 0.0, **mark_overrides})
    new_marks = fam_style.marks.model_copy(update={family: new_mark})
    new_fam = fam_style.model_copy(update={"marks": new_marks})
    charts = compiled.charts.model_copy(update={family: new_fam})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _is_step_band_offset(encoding: dict[str, Any]) -> bool:
    """True iff ``encoding["xOffset"]`` is the real band-step point scale.

    A zero-anchor baseline rule (``full_rule_at``) also sets a literal
    ``xOffset: {"value": 0}`` for its own full-width positioning, unrelated
    to band-step curves — only the real step-band encoding carries a
    ``scale`` (see ``step_band.py``'s ``encoding["xOffset"]`` bake).
    """
    offset = encoding.get("xOffset")
    return isinstance(offset, dict) and "scale" in offset


def _step_band_encoding(spec: dict[str, Any]) -> dict[str, Any] | None:
    """Return the encoding dict carrying the step xOffset.

    Standalone charts put it on the shared top-level ``spec["encoding"]``;
    layered charts put it on the overlay layer's own encoding.
    """
    if _is_step_band_offset(spec.get("encoding", {})):
        return spec["encoding"]
    for lyr in spec.get("layer", []):
        if _is_step_band_offset(lyr.get("encoding", {})):
            return lyr["encoding"]
    return None


def _step_band_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve the rows feeding the band-step mark.

    Standalone charts double the top-level ``data.values``; a layered overlay
    carries its own inline ``data.values`` so the bar layer keeps undoubled rows.
    """
    for lyr in spec.get("layer", []):
        enc = lyr.get("encoding", {})
        data = lyr.get("data")
        if _is_step_band_offset(enc) and isinstance(data, dict) and "values" in data:
            return data["values"]
    return spec.get("data", {}).get("values", [])


# ── curve: step on a temporal/quantitative x falls through to plain VL step ───


def test_step_on_continuous_x_does_not_raise_and_uses_plain_step():
    """The regression this task fixes: ``curve: step`` on a non-band x must
    NOT hard-error (the deleted crash), must NOT row-double, and must reach
    VL as the plain (non-band) ``step`` interpolate."""
    board_rs, board_ctx = _board_with_mark("line", curve="step")
    chart = LineChart(
        id="cont",
        type="line",
        x="day",
        y="target",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, CONTINUOUS, board_style=board_rs, chart_style_context=board_ctx
    )
    assert _step_band_encoding(spec) is None, "no xOffset on a continuous x"
    assert spec["data"]["values"] == CONTINUOUS, "rows must not be doubled"
    marks = [
        lyr["mark"]
        for lyr in spec.get("layer", [])
        if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "line"
    ]
    assert marks, f"expected a line mark; spec={spec!r}"
    assert all(m.get("interpolate") == "step" for m in marks), (
        f"expected plain VL 'step' (not band-mode 'step-after'), got "
        f"{[m.get('interpolate') for m in marks]!r}"
    )


# ── connect on a non-band curve is a no-op ────────────────────────────────────


def test_connect_is_noop_on_non_band_curve():
    chart = LineChart(
        id="plain",
        type="line",
        x="month",
        y="target",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    board_plain_rs, board_plain_ctx = _board_with_mark("line", curve="linear")
    board_connect_rs, board_connect_ctx = _board_with_mark(
        "line", curve="linear", connect=True
    )

    spec_plain = generate_vega_lite_spec(
        chart, BARS, board_style=board_plain_rs, chart_style_context=board_plain_ctx
    )
    spec_connect = generate_vega_lite_spec(
        chart, BARS, board_style=board_connect_rs, chart_style_context=board_connect_ctx
    )

    assert spec_plain == spec_connect, (
        "connect on a non-band curve must not change the spec"
    )
    # And no band-step machinery leaked in.
    assert _step_band_encoding(spec_connect) is None


# ── Standalone (non-layered) line/area support band-aware step ───────────────


@pytest.mark.parametrize("family", ["line", "area"])
def test_standalone_step_on_band_x_emits_xoffset_and_doubled_data(family):
    board_rs, board_ctx = _board_with_mark(family, curve="step")
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": f"solo_{family}",
            "type": family,
            "x": "month",
            "y": "target",
            "query": SqlQuery(sql="SELECT 1", source="test"),
            "query_name": "q",
        }
    )
    spec = generate_vega_lite_spec(
        chart, BARS, board_style=board_rs, chart_style_context=board_ctx
    )
    enc = _step_band_encoding(spec)
    assert enc is not None, (
        f"standalone {family} band-aware step must emit xOffset; spec={spec!r}"
    )
    rows = _step_band_rows(spec)
    assert len(rows) == 2 * len(BARS)


# ── Regression: ISO-date x must still band for a step curve on line/area ──────


@pytest.mark.parametrize("family", ["line", "area"])
def test_step_with_iso_date_x_stays_ordinal(family: str) -> None:
    # line/area used to always resolve a calendar-bucketed x to "temporal",
    # bypassing the density gate bar marks still use. A band-aware step curve
    # requires a band (nominal/ordinal) x-scale, so this must not raise, and
    # the x-scale itself must still resolve to ordinal — not just avoid the
    # error via a different mechanism.
    board_rs, board_ctx = _board_with_mark(family, curve="step")
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": f"date_{family}",
            "type": family,
            "x": "month",
            "y": "target",
            "query": SqlQuery(sql="SELECT 1", source="test"),
            "query_name": "q",
        }
    )
    spec = generate_vega_lite_spec(
        chart, MONTHLY_DATES, board_style=board_rs, chart_style_context=board_ctx
    )
    enc = _step_band_encoding(spec)
    assert enc is not None, (
        f"standalone {family} band-aware step must emit xOffset; spec={spec!r}"
    )
    assert enc["x"]["type"] == "ordinal", (
        f"band-aware step x-scale must stay ordinal on date-shaped x, got {enc['x']!r}"
    )


# ── connect: false inset vs connect: true full bandwidth ──────────────────────


def test_connected_step_keeps_full_bandwidth_xoffset_range():
    board_rs, board_ctx = _board_with_mark("line", curve="step", connect=True)
    chart = LineChart(
        id="connected",
        type="line",
        x="month",
        y="target",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, BARS, board_style=board_rs, chart_style_context=board_ctx
    )
    enc = _step_band_encoding(spec)
    assert enc is not None
    assert enc["xOffset"]["scale"]["range"] == [0, {"expr": "bandwidth('x')"}]


def test_disconnected_step_insets_xoffset_range():
    board_rs, board_ctx = _board_with_mark("line", curve="step", connect=False)
    chart = LineChart(
        id="disconnected",
        type="line",
        x="month",
        y="target",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, BARS, board_style=board_rs, chart_style_context=board_ctx
    )
    enc = _step_band_encoding(spec)
    assert enc is not None
    rng = enc["xOffset"]["scale"]["range"]
    assert rng != [
        0,
        {"expr": "bandwidth('x')"},
    ], "disconnected markers must inset off the full band edges"
    assert isinstance(rng[0], dict) and "expr" in rng[0]
    assert isinstance(rng[1], dict) and "expr" in rng[1]
    # Inset width comes from the theme, not a Python literal.
    width = get_theme_style().charts.marks.line.disconnected_width
    lo = (1 - width) / 2
    assert rng[0]["expr"] == f"bandwidth('x')*{lo}"
    assert rng[1]["expr"] == f"bandwidth('x')*{1 - lo}"


# ── stroke-linecap: disconnected plateaus end flush at the band edge ──────────
#
# A disconnected band-step draws one flat sub-path per band, so a round cap
# overhangs BOTH ends of every plateau by half the stroke width — eating the
# gap the connect:false inset exists to create. Connected steps keep round:
# there the cap touches only the two outer ends of one continuous path, and
# the staircase corners are strokeJoin. Both values are theme-declared
# (marks.line.stroke.cap / marks.line.disconnected_cap); the emitter only
# selects between them.


def _line_marks(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Every VL line mark dict in the spec, flattened across nesting."""
    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            mark = node.get("mark")
            if isinstance(mark, dict) and mark.get("type") == "line":
                out.append(mark)
            for lyr in node.get("layer", []):
                walk(lyr)

    walk(spec)
    return out


def _stroked_marks(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Every mark dict that can carry a stroke (line or area), flattened."""
    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            mark = node.get("mark")
            if isinstance(mark, dict) and mark.get("type") in ("line", "area"):
                out.append(mark)
            for lyr in node.get("layer", []):
                walk(lyr)

    walk(spec)
    return out


def _caps(spec: dict[str, Any]) -> list[str | None]:
    return [m.get("strokeCap") for m in _line_marks(spec)]


def _cap_spec(**mark_overrides: Any) -> dict[str, Any]:
    """Render a single-series line chart on the band x with the given mark style."""
    board_rs, board_ctx = _board_with_mark("line", **mark_overrides)
    chart = LineChart(
        id="caps",
        type="line",
        x="month",
        y="target",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    return generate_vega_lite_spec(
        chart, BARS, board_style=board_rs, chart_style_context=board_ctx
    )


def test_disconnected_step_defaults_to_butt_cap():
    """The headline default: no authoring, plateaus end flush at the band edge."""
    caps = _caps(_cap_spec(curve="step", connect=False))
    assert caps, "expected at least one line mark"
    assert all(c == "butt" for c in caps), (
        f"disconnected plateaus must not overhang the band edge; got {caps!r}"
    )


def test_connected_step_keeps_round_cap():
    caps = _caps(_cap_spec(curve="step", connect=True))
    assert caps and all(c == "round" for c in caps), (
        f"a connected step is one continuous path — round; got {caps!r}"
    )


def test_step_with_connect_unset_keeps_round_cap():
    caps = _caps(_cap_spec(curve="step"))
    assert caps and all(c == "round" for c in caps), (
        f"connect defaults to connected — round; got {caps!r}"
    )


def test_disconnected_step_on_continuous_x_keeps_round_cap():
    """Band mode never fires on a continuous x, so connect: false is a no-op
    there — one continuous path, and round stays correct."""
    board_rs, board_ctx = _board_with_mark("line", curve="step", connect=False)
    chart = LineChart(
        id="cont-caps",
        type="line",
        x="day",
        y="target",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, CONTINUOUS, board_style=board_rs, chart_style_context=board_ctx
    )
    caps = _caps(spec)
    assert caps and all(c == "round" for c in caps), (
        f"continuous-x step is not band mode; got {caps!r}"
    )


def test_authored_disconnected_cap_overrides_the_default():
    """The author override path. Uses ``square`` rather than ``round``: round is
    what the theme yields with the feature absent entirely, so it would stay
    green against deleted plumbing."""
    caps = _caps(_cap_spec(curve="step", connect=False, disconnected_cap="square"))
    assert caps and all(c == "square" for c in caps), (
        f"authored disconnected_cap must win over the theme default; got {caps!r}"
    )


def test_disconnected_step_halo_tracks_the_foreground_cap():
    """A round halo behind a butt-capped foreground would poke out at 2x the
    stroke width — worse than the overhang this task removes."""
    board_rs, board_ctx = _board_with_mark(
        "line",
        curve="step",
        connect=False,
        halo_multiplier=2.0,
        # Distinctive join: the theme's own is round, so asserting round would
        # pass against the hardcoded literal this replaced. Partial stroke —
        # the cascade refills width/color.
        stroke=StrokeStyle(join="bevel"),
    )
    chart = LineChart(
        id="halo-caps",
        type="line",
        x="month",
        y="target",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, BARS, board_style=board_rs, chart_style_context=board_ctx
    )
    marks = _line_marks(spec)
    widths = {m.get("strokeWidth") for m in marks}
    assert len(widths) > 1, f"expected a halo mark wider than the fg; got {widths!r}"
    assert all(m.get("strokeCap") == "butt" for m in marks), (
        f"halo must track the fg cap; got {[m.get('strokeCap') for m in marks]!r}"
    )
    assert all(m.get("strokeJoin") == "bevel" for m in marks), (
        f"halo must track the fg join, not a hardcoded round; "
        f"got {[m.get('strokeJoin') for m in marks]!r}"
    )


def test_disconnected_cap_resolves_on_an_overlay_layer():
    """The shape real boards use: a target band as a layer over a bar base."""
    board_rs, board_ctx = _board_with_mark("line", curve="step", connect=False)
    spec = _v2_spec(
        {
            "type": "bar",
            "x": "month",
            "y": "actual",
            "query": "q",
            "layers": [
                {
                    "type": "line",
                    "y": "target",
                    "style": {"marks": {"line": {"curve": "step", "connect": False}}},
                }
            ],
        },
        BARS,
        (board_rs, board_ctx),
    )
    caps = _caps(spec)
    assert caps, f"expected a line layer; spec={spec!r}"
    assert all(c == "butt" for c in caps), (
        f"a disconnected-step overlay layer must butt-cap too; got {caps!r}"
    )


def test_overlay_layer_inset_uses_the_layers_own_disconnected_width():
    board_rs, board_ctx = _board_with_mark("line", curve="step", connect=False)
    spec = _v2_spec(
        {
            "type": "bar",
            "x": "month",
            "y": "actual",
            "query": "q",
            "layers": [
                {
                    "type": "line",
                    "y": "target",
                    "style": {
                        "marks": {
                            "line": {
                                "curve": "step",
                                "connect": False,
                                "disconnected_width": 0.5,
                            }
                        }
                    },
                }
            ],
        },
        BARS,
        (board_rs, board_ctx),
    )
    enc = _step_band_encoding(spec)
    assert enc is not None
    rng = enc["xOffset"]["scale"]["range"]
    assert rng[0]["expr"] == "bandwidth('x')*0.25"
    assert rng[1]["expr"] == "bandwidth('x')*0.75"


def test_area_edge_line_is_unaffected_by_the_disconnected_default():
    """Area has no ``connect`` — its edge is always a continuous silhouette, so
    the disconnected-band cap must never reach it.

    Step curves require a single series, and a single-series area renders the
    stacked recipe, so its edge cap comes from ``marks.area.stacked.stroke``.
    Authoring a non-theme cap there proves the edge reads it, rather than
    matching the theme's ``round`` by coincidence.
    """
    board_rs, board_ctx = _board_with_mark("area", curve="step")
    chart = AreaChart(
        id="area-caps",
        type="area",
        x="month",
        y="target",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        style=AreaChartStylePatch.model_validate(
            {"marks": {"area": {"stacked": {"stroke": {"cap": "square"}}}}}
        ),
    )
    spec = generate_vega_lite_spec(
        chart, BARS, board_style=board_rs, chart_style_context=board_ctx
    )
    caps = _caps(spec)
    assert caps and all(c == "square" for c in caps), (
        f"area edge takes marks.area.stacked.stroke.cap; got {caps!r}"
    )


def _joins(spec: dict[str, Any]) -> list[str | None]:
    return [m.get("strokeJoin") for m in _line_marks(spec)]


def test_area_halo_tracks_the_edge_cap_and_join():
    """The area edge's halo is a wider stroke drawn behind it, so it must take
    the edge's own cap/join — a round halo behind a butt edge pokes out at the
    full halo width, the exact defect this change set out to remove.

    Authors a deliberately non-theme cap/join (``square``/``bevel``) on
    ``charts.area.marks.line.stroke`` — the slot the edge actually reads. The
    theme's own values are round/round, so asserting those would pass just as
    well against the hardcoded ``"round"`` literals this replaced; only a
    distinctive value makes this a real detector.

    Wide ``y`` (2+ bands) keeps this chart on the overlap/halo recipe -- a
    colorless, single-band area now takes the stacked recipe instead (see
    ``test_stacked_area_perimeter_keeps_its_cap_and_join``), which has no
    halo to test here.
    """
    compiled = get_theme_style("clarity")
    area = compiled.charts.area
    # A partial stroke: the inherit cascade refills width/color from the global
    # marks.line.stroke, so only cap/join diverge from the theme.
    new_line = area.marks.line.model_copy(
        update={"stroke": StrokeStyle(cap="square", join="bevel")}
    )
    area = area.model_copy(
        update={"marks": area.marks.model_copy(update={"line": new_line})}
    )
    charts = compiled.charts.model_copy(update={"area": area})
    board_rs, board_ctx = resolve_style_and_context(
        compiled.model_copy(update={"charts": charts})
    )
    chart = AreaChart(
        id="area-halo",
        type="area",
        x="month",
        y=["actual", "target"],
        # Wide y draws an endpoint-label rail by default, wrapping the spec in
        # hconcat -- off here so _line_marks's plain layer-array walk applies.
        style=AreaChartStylePatch.model_validate(
            {"endpoint_labels": {"visible": False}}
        ),
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, BARS, board_style=board_rs, chart_style_context=board_ctx
    )
    marks = _line_marks(spec)
    widths = {m.get("strokeWidth") for m in marks}
    assert len(widths) > 1, f"expected a halo wider than the edge; got {widths!r}"
    assert all(m.get("strokeCap") == "square" for m in marks), (
        f"halo must track the edge cap, not a hardcoded round; got {_caps(spec)!r}"
    )
    assert all(m.get("strokeJoin") == "bevel" for m in marks), (
        f"halo must track the edge join; got {_joins(spec)!r}"
    )


@pytest.mark.parametrize("stack", ["zero", "center"])
def test_stacked_area_perimeter_keeps_its_cap_and_join(stack: str):
    """Regression: the stacked recipe REPLACES marks.line.stroke wholesale
    (resolve/chart/area.py) rather than merging into it, so cap/join must be
    declared on marks.area.stacked.stroke. When they are not, the emitter
    writes strokeCap/strokeJoin as None, Vega drops the attributes, and the 1px
    background-knockout separator between bands falls back to SVG's butt/miter
    — spiking every band vertex.
    """
    board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
    chart = AreaChart(
        id=f"stacked-{stack}",
        type="area",
        x="month",
        y="target",
        stack=stack,
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, BARS, board_style=board_rs, chart_style_context=board_ctx
    )
    # The stacked recipe draws the perimeter on the AREA mark itself, not as a
    # separate line mark — so collect any stroked mark, not just type "line".
    marks = [
        m
        for m in _stroked_marks(spec)
        if m.get("strokeWidth") and m.get("stroke") is not None
    ]
    assert marks, f"expected a perimeter-stroked mark; spec={spec!r}"
    # Presence, not the specific value: the regression is the attribute
    # DISAPPEARING (Vega then falls back to butt/miter). Pinning "round" would
    # also fail on a legitimate theme tune, which is not what this guards.
    assert all(m.get("strokeCap") is not None for m in marks), (
        f"stacked perimeter lost its cap; got {[m.get('strokeCap') for m in marks]!r}"
    )
    assert all(m.get("strokeJoin") is not None for m in marks), (
        f"stacked perimeter lost its join; got {[m.get('strokeJoin') for m in marks]!r}"
    )


def test_stacked_stroke_without_cap_or_join_raises():
    """The guard for the wholesale replace. A stacked recipe that omits cap or
    join would silently drop the SVG attributes; this must fail loudly at
    resolve rather than render a spiked perimeter."""
    # The theme declares the stacked recipe on the GLOBAL marks slot; the area
    # family's own copy is a cascade sentinel (None) until resolve fills it, so
    # crippling the family copy would just be refilled from here.
    compiled = get_theme_style("clarity")
    marks = compiled.charts.marks
    stacked = marks.area.stacked
    assert stacked is not None and stacked.stroke is not None
    crippled = stacked.model_copy(
        update={"stroke": stacked.stroke.model_copy(update={"cap": None})}
    )
    new_area_mark = marks.area.model_copy(update={"stacked": crippled})
    board_rs, board_ctx = resolve_style_and_context(
        compiled.model_copy(
            update={
                "charts": compiled.charts.model_copy(
                    update={"marks": marks.model_copy(update={"area": new_area_mark})}
                )
            }
        )
    )
    chart = AreaChart(
        id="stacked-missing-cap",
        type="area",
        x="month",
        y="target",
        stack="zero",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    with pytest.raises(CompilationError) as exc_info:
        generate_vega_lite_spec(
            chart, BARS, board_style=board_rs, chart_style_context=board_ctx
        )
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-AREA-STACKED-STROKE-INCOMPLETE"


def test_non_stacked_area_tolerates_an_authored_null_cap():
    """The guard must NOT fire on the non-stacked path: marks.line.stroke
    survives intact there, and ResolvedStrokeStyle documents None as the legal
    "use the VL default" state. A board authoring `cap: null` renders, and the
    emitter leaves strokeCap/strokeJoin out rather than writing null, which is
    not in Vega-Lite's enum.

    Two measures keep the chart on the overlap recipe. A single-series area
    would take the stacked recipe's stroke instead, so the authored null cap
    would never reach the emitter and this test would pass vacuously.
    """
    compiled = get_theme_style("clarity")
    marks = compiled.charts.marks
    line = marks.line.model_copy(
        update={"stroke": StrokeStyle(cap=None, join=None, width=2.5)}
    )
    board_rs, board_ctx = resolve_style_and_context(
        compiled.model_copy(
            update={
                "charts": compiled.charts.model_copy(
                    update={"marks": marks.model_copy(update={"line": line})}
                )
            }
        )
    )
    chart = AreaChart(
        id="null-cap",
        type="area",
        x="month",
        y=["actual", "target"],
        # Wide y draws an endpoint-label rail by default, wrapping the spec in
        # hconcat -- off here so _stroked_marks's plain layer-array walk applies.
        style=AreaChartStylePatch.model_validate(
            {"endpoint_labels": {"visible": False}}
        ),
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, BARS, board_style=board_rs, chart_style_context=board_ctx
    )
    marks = _stroked_marks(spec)
    assert marks, "a null cap must still render an area"
    assert all(m.get("strokeCap", "unset") is not None for m in marks), marks
    assert all(m.get("strokeJoin", "unset") is not None for m in marks), marks


def test_line_halo_omits_an_authored_null_cap():
    """A line's halo takes its fg line's cap and join, so an authored
    ``cap: null`` must be omitted from the halo too, not written as ``null``,
    which is not in Vega-Lite's enum. Keeps the theme's default halo, unlike
    ``_board_with_mark``, which zeroes it."""
    compiled = get_theme_style("clarity")
    marks = compiled.charts.marks
    line = marks.line.model_copy(
        update={"stroke": StrokeStyle(cap=None, join=None, width=2.5)}
    )
    board_rs, board_ctx = resolve_style_and_context(
        compiled.model_copy(
            update={
                "charts": compiled.charts.model_copy(
                    update={"marks": marks.model_copy(update={"line": line})}
                )
            }
        )
    )
    chart = LineChart(
        id="null-cap-line",
        type="line",
        x="month",
        y="target",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, BARS, board_style=board_rs, chart_style_context=board_ctx
    )
    marks_out = _stroked_marks(spec)
    assert len(marks_out) >= 2, "expected a fg line and its halo"
    assert all(m.get("strokeCap", "unset") is not None for m in marks_out), marks_out
    assert all(m.get("strokeJoin", "unset") is not None for m in marks_out), marks_out


def test_disconnected_cap_is_authorable_as_yaml_on_a_layer():
    """The override path as a board actually writes it — not just as a
    constructed style object."""
    board_rs, board_ctx = _board_with_mark("line")
    spec = _v2_spec(
        {
            "type": "bar",
            "x": "month",
            "y": "actual",
            "query": "q",
            "layers": [
                {
                    "type": "line",
                    "y": "target",
                    "style": {
                        "marks": {
                            "line": {
                                "curve": "step",
                                "connect": False,
                                "disconnected_cap": "round",
                            }
                        }
                    },
                }
            ],
        },
        BARS,
        (board_rs, board_ctx),
    )
    caps = _caps(spec)
    assert caps, f"expected a line layer; spec={spec!r}"
    assert all(c == "round" for c in caps), (
        f"YAML-authored disconnected_cap must reach the emitter; got {caps!r}"
    )


# ── band-aware step on a multi-metric (y: [...]) chart raises (no silent degrade)


@pytest.mark.parametrize("family", ["line", "area"])
def test_step_on_band_x_multi_metric_chart_raises(family):
    # Multi-metric charts route through _map_layered_chart, which does not apply
    # the band-doubling transform. Without a guard the curve→step swap would
    # silently degrade to a plain centered step; assert it raises instead.
    board_rs, board_ctx = _board_with_mark(family, curve="step")
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "multi",
            "type": family,
            "x": "month",
            "y": ["actual", "target"],
            "query": SqlQuery(sql="SELECT 1", source="test"),
            "query_name": "q",
        }
    )
    with pytest.raises(ChartDataError, match="multi-metric"):
        generate_vega_lite_spec(
            chart, BARS, board_style=board_rs, chart_style_context=board_ctx
        )


def test_step_on_temporal_x_multi_metric_chart_does_not_raise():
    """The multi-metric reject is scoped to BAND mode only — plain VL step on
    a continuous x must render normally for a multi-metric chart too."""
    board_rs, board_ctx = _board_with_mark("line", curve="step")
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "multi_cont",
            "type": "line",
            "x": "day",
            "y": ["actual", "target"],
            "query": SqlQuery(sql="SELECT 1", source="test"),
            "query_name": "q",
        }
    )
    spec = generate_vega_lite_spec(
        chart, CONTINUOUS, board_style=board_rs, chart_style_context=board_ctx
    )
    assert _step_band_encoding(spec) is None


# ── band-aware step + a multi-series color encoding raises (no mis-grouped render)

MULTI_SERIES = [
    {"month": "Jan", "value": 30, "series": "A"},
    {"month": "Jan", "value": 20, "series": "B"},
    {"month": "Feb", "value": 40, "series": "A"},
    {"month": "Feb", "value": 25, "series": "B"},
]


@pytest.mark.parametrize("family", ["line", "area"])
def test_step_on_band_x_with_multi_series_color_raises(family):
    # The per-band xOffset/detail grouping assumes a single series; a data-driven
    # color split would render mis-grouped silhouettes. Fail fast instead.
    board_rs, board_ctx = _board_with_mark(family, curve="step")
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "ms",
            "type": family,
            "x": "month",
            "y": "value",
            "color": "series",
            "query": SqlQuery(sql="SELECT 1", source="test"),
            "query_name": "q",
        }
    )
    with pytest.raises(ChartDataError, match="multi-series color"):
        generate_vega_lite_spec(
            chart, MULTI_SERIES, board_style=board_rs, chart_style_context=board_ctx
        )


# ── Non-band area curve emits interpolate on fill + top-edge line ────────────


def test_area_non_band_curve_emits_interpolate_on_fill_and_stroke():
    # AreaMarkStyle.curve passes straight through the area fill (area_mark_to_vl)
    # while the separate top-edge stroke line is built directly. Both must carry
    # the interpolate, and a non-band curve must pass through unchanged.
    board_rs, board_ctx = _board_with_mark("area", curve="monotone")
    chart = AreaChart(
        id="area_monotone",
        type="area",
        x="month",
        y="target",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, BARS, board_style=board_rs, chart_style_context=board_ctx
    )
    marks = [
        lyr["mark"]
        for lyr in spec.get("layer", [])
        if isinstance(lyr.get("mark"), dict)
        and lyr["mark"].get("type") in ("area", "line")
    ]
    assert marks, f"expected area/line marks; spec={spec!r}"
    assert all(m.get("interpolate") == "monotone" for m in marks), (
        f"every area/line mark must carry interpolate=monotone, got "
        f"{[m.get('interpolate') for m in marks]!r}"
    )
    # No band-step machinery for a plain curve.
    assert _step_band_encoding(spec) is None


# ── Standalone area halo path: fill marks must also carry the step interpolate ─


def test_standalone_area_halo_path_fill_marks_carry_interpolate():
    # The default theme enables the area halo (halo_multiplier > 0); that branch
    # builds fill marks as literal dicts (bypassing area_mark_to_vl). Every
    # area/line mark — fill and top-edge stroke — must carry interpolate=
    # step-after so the stepped stroke doesn't float off a linear fill boundary.
    board_rs, board_ctx = _board_with_mark("area", curve="step", halo_multiplier=2.0)
    chart = AreaChart(
        id="area_halo",
        type="area",
        x="month",
        y="target",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, BARS, board_style=board_rs, chart_style_context=board_ctx
    )
    drawing_marks = [
        lyr["mark"]
        for lyr in spec.get("layer", [])
        if isinstance(lyr.get("mark"), dict)
        and lyr["mark"].get("type") in ("area", "line")
    ]
    assert any(m.get("type") == "area" for m in drawing_marks), (
        f"expected halo path to emit area fill marks; spec={spec!r}"
    )
    assert all(m.get("interpolate") == "step-after" for m in drawing_marks), (
        "every area/line mark in the halo path must carry interpolate=step-after, got "
        f"{[(m.get('type'), m.get('interpolate')) for m in drawing_marks]!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Render-v2 parity: the v2 emitter stack must produce the same band-spanning,
# bar-centering, and tooltip behavior as the direct entry point above.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("family", ["line", "area"])
def test_v2_standalone_step_on_band_x_doubles_rows_via_xoffset(family):
    board = _board_with_mark(family, curve="step")
    chart_def = {"type": family, "x": "month", "y": "target", "query": "q"}
    spec = _v2_spec(chart_def, BARS, board)
    enc = _step_band_encoding(spec)
    assert enc is not None, f"v2 standalone {family} must emit xOffset; spec={spec!r}"
    assert enc["xOffset"]["field"] == STEP_BAND_EDGE_FIELD
    assert len(spec["data"]["values"]) == 2 * len(BARS)


@pytest.mark.parametrize("family", ["line", "area"])
def test_v2_step_with_iso_date_x_stays_ordinal(family: str) -> None:
    # Regression: the v2 emitter stack must not force temporal for a
    # band-aware step curve's date-shaped x either.
    board = _board_with_mark(family, curve="step")
    chart_def = {"type": family, "x": "month", "y": "target", "query": "q"}
    spec = _v2_spec(chart_def, MONTHLY_DATES, board)
    enc = _step_band_encoding(spec)
    assert enc is not None, f"v2 standalone {family} must emit xOffset; spec={spec!r}"
    assert enc["x"]["type"] == "ordinal", (
        f"v2 band-aware step x-scale must stay ordinal on date-shaped x, got {enc['x']!r}"
    )


def test_v2_step_on_continuous_x_does_not_raise():
    board = _board_with_mark("line", curve="step")
    chart_def = {"type": "line", "x": "day", "y": "target", "query": "q"}
    spec = _v2_spec(chart_def, CONTINUOUS, board)
    assert _step_band_encoding(spec) is None
    assert spec["data"]["values"] == CONTINUOUS


# ─────────────────────────────────────────────────────────────────────────────
# Per-layer-query overlay is painted in authored order: layers[0] paints
# first (behind), the base chart's own series always paints first of all —
# the authored order is the z-order, the emitter never reorders layers. See
# render/chart/AGENTS.md's "Paint order is a dbt charts contract".
# ─────────────────────────────────────────────────────────────────────────────

_PL_ACTUALS = [{"m": "2025-01-01", "actual": 30}, {"m": "2025-02-01", "actual": 55}]
_PL_GOALS = [{"m": "2025-01-01", "target": 45}, {"m": "2025-02-01", "target": 45}]


def _mark_order(spec: dict[str, Any]) -> tuple[int, int]:
    """(index of the bar mark, index of the line mark) in the assembled layer list."""
    bar_idx = next(
        i
        for i, lyr in enumerate(spec["layer"])
        if lyr.get("mark", {}).get("type") == "bar"
        or any(sub.get("mark", {}).get("type") == "bar" for sub in lyr.get("layer", []))
    )
    line_idx = next(
        i
        for i, lyr in enumerate(spec["layer"])
        if any(
            sub.get("mark", {}).get("type") == "line" for sub in lyr.get("layer", [])
        )
    )
    return bar_idx, line_idx


def test_per_layer_overlay_preserves_authored_layer_order() -> None:
    """A base bar chart with a line-type overlay layer on its own query paints
    the base first (behind) and the layer on top — authored-order z-index."""
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.render.chart.session import BoardRenderSession

    board_rs, board_ctx = _board_with_mark("line", curve="linear")
    query_registry = {
        "a": SqlQuery(sql="SELECT 1", source="test"),
        "g": SqlQuery(sql="SELECT 1", source="test"),
    }
    chart_def = {
        "type": "bar",
        "query": "a",
        "x": "m",
        "y": "actual",
        "layers": [{"type": "line", "query": "g", "y": "target", "label": "Target"}],
    }
    compiled = normalize_chart("tva", chart_def, query_registry, sources={})
    resolved = resolve(compiled, _PL_ACTUALS, chart_style_context=board_ctx)
    datasets = {"a": list(_PL_ACTUALS), "g": list(_PL_GOALS)}
    session = BoardRenderSession.create(board_rs)
    spec = session.finalize_vl(session.emit_chart(resolved, _DEFAULT_BOX, datasets))

    bar_idx, line_idx = _mark_order(spec)
    assert bar_idx < line_idx, (
        f"base bar must paint before the overlay line layer; got "
        f"bar_idx={bar_idx}, line_idx={line_idx}"
    )


# ── stroke-dasharray: disconnected plateaus fit whole dashes to the band ──────
#
# Each disconnected plateau is its own path, so SVG restarts the dash phase on
# every one. The authored period is pixels but the plateau length is
# ``bandwidth('x') * disconnected_width``, so a literal dasharray leaves a stub
# at the right edge. The emitter rewrites it as a Vega expression fitted to N
# dashes + N-1 gaps: ink at both plateau edges.


def _dash(**mark_overrides: Any) -> Any:
    marks = _line_marks(_cap_spec(**mark_overrides))
    assert len(marks) == 1
    return marks[0].get("strokeDash")


def _eval_dash(expr: str, bandwidth: float) -> list[float]:
    """Evaluate an emitted strokeDash expression with a numeric bandwidth."""
    import math

    def rnd(v: float) -> int:  # Vega's round is Math.round, not banker's
        return math.floor(v + 0.5)

    src = expr.replace("bandwidth('x')", repr(bandwidth)).replace("round(", "rnd(")
    return eval(src, {"rnd": rnd, "max": max})  # noqa: S307


def _fit_expr(dashes: list[float]) -> str:
    """Re-derive the expected expression independently of the helper."""
    width = get_theme_style().charts.marks.line.disconnected_width
    span = f"(bandwidth('x')*{width})"
    period, last = sum(dashes), dashes[-1]
    n = f"max(1,round(({span}+{last})/{period}))"
    scale = f"{span}/({n}*{period}-{last})"
    return "[" + ",".join(f"{d}*{scale}" for d in dashes) + "]"


def test_disconnected_step_dasharray_becomes_fitted_expr():
    got = _dash(curve="step", connect=False, stroke=StrokeStyle(dasharray="5 4"))
    assert got == {"expr": _fit_expr([5.0, 4.0])}


@pytest.mark.parametrize("bandwidth", [63.692, 37.5, 36.0, 160.0, 12.0, 5.0])
@pytest.mark.parametrize("dasharray", ["5 4", "6 2 1 2", "6 2 1", "10 2"])
def test_fitted_dasharray_spans_the_plateau_exactly(dasharray: str, bandwidth: float):
    got = _dash(curve="step", connect=False, stroke=StrokeStyle(dasharray=dasharray))
    dashes = _eval_dash(got["expr"], bandwidth)
    authored = [float(v) for v in dasharray.split()]
    if len(authored) % 2:
        authored *= 2  # SVG repeats an odd list, so its last entry is a gap
    assert len(dashes) == len(authored)
    assert all(d > 0 for d in dashes)
    # Authored dash/gap ratios survive the scaling.
    ratios = [d / a for d, a in zip(dashes, authored, strict=True)]
    assert max(ratios) - min(ratios) < 1e-9
    # N dashes + (N-1) gaps cover the plateau: ink at both ends.
    span = bandwidth * get_theme_style().charts.marks.line.disconnected_width
    period, last = sum(dashes), dashes[-1]
    n = (span + last) / period
    assert abs(n - round(n)) * period < 1e-6
    assert n >= 1 - 1e-9


def test_authored_disconnected_width_reaches_the_fitted_dasharray():
    got = _dash(
        curve="step",
        connect=False,
        disconnected_width=0.5,
        stroke=StrokeStyle(dasharray="5 4"),
    )
    assert "bandwidth('x')*0.5" in got["expr"]


@pytest.mark.parametrize("dasharray", ["0 8", "0 0"])
def test_zero_ink_dasharray_stays_literal(dasharray: str):
    got = _dash(curve="step", connect=False, stroke=StrokeStyle(dasharray=dasharray))
    assert got == [float(v) for v in dasharray.split()]


def test_connected_step_dasharray_stays_literal():
    got = _dash(curve="step", connect=True, stroke=StrokeStyle(dasharray="5 4"))
    assert got == [5.0, 4.0]


def test_disconnected_step_without_dasharray_has_no_stroke_dash():
    assert _dash(curve="step", connect=False) is None


def test_disconnected_step_on_continuous_x_keeps_literal_dasharray():
    board_rs, board_ctx = _board_with_mark(
        "line", curve="step", connect=False, stroke=StrokeStyle(dasharray="5 4")
    )
    chart = LineChart(
        id="cont-dash",
        type="line",
        x="day",
        y="target",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, CONTINUOUS, board_style=board_rs, chart_style_context=board_ctx
    )
    assert [m.get("strokeDash") for m in _line_marks(spec)] == [[5.0, 4.0]]


def test_fitted_dasharray_round_trips_through_vl_convert():
    """Pace-overlay shape: every rendered plateau carries ink at both edges."""
    import json
    import re

    import vl_convert as vlc

    spec = _cap_spec(curve="step", connect=False, stroke=StrokeStyle(dasharray="5 4"))
    spec["width"] = 460
    svg = vlc.vegalite_to_svg(json.dumps(spec))
    paths = re.findall(
        r'<path d="M([\d.]+),([\d.]+)L([\d.]+),[\d.]+[^>]*stroke-dasharray="([^"]+)"',
        svg,
    )
    assert len(paths) == len(BARS), svg
    for x0, _y, x1, dash in paths:
        dash_len, gap_len = (float(v) for v in dash.split(","))
        n = (float(x1) - float(x0) + gap_len) / (dash_len + gap_len)
        assert abs(n - round(n)) * (dash_len + gap_len) < 0.01, (x0, x1, dash)
