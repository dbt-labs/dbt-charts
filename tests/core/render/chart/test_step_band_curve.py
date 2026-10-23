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

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    Chart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    StylePatch,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError

from ..._engine_render import render_chart_svg

_V2_QUERY_REGISTRY: dict[str, Any] = {"q": SqlQuery(sql="SELECT 1", source="test")}


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


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


# ── curve: step on a temporal/quantitative x falls through to plain VL step ───


# ── connect on a non-band curve is a no-op ────────────────────────────────────


# ── Standalone (non-layered) line/area support band-aware step ───────────────


# ── Regression: ISO-date x must still band for a step curve on line/area ──────


# ── connect: false inset vs connect: true full bandwidth ──────────────────────


# ── stroke-linecap: disconnected plateaus end flush at the band edge ──────────
#
# A disconnected band-step draws one flat sub-path per band, so a round cap
# overhangs BOTH ends of every plateau by half the stroke width — eating the
# gap the connect:false inset exists to create. Connected steps keep round:
# there the cap touches only the two outer ends of one continuous path, and
# the staircase corners are strokeJoin. Both values are theme-declared
# (marks.line.stroke.cap / marks.line.disconnected_cap); the emitter only
# selects between them.


def test_stacked_stroke_without_cap_or_join_raises():
    """The guard for the wholesale replace. A stacked recipe that omits cap or
    join would silently drop the SVG attributes; this must fail loudly at
    resolve rather than render a spiked perimeter."""
    # `cap: auto` leaves the stacked recipe without a cap, the state the guard names.
    board_rs, board_ctx = resolve_style_and_context(
        get_theme_style("clarity"),
        StylePatch.model_validate(
            {"charts": {"marks": {"area": {"stacked": {"stroke": {"cap": "auto"}}}}}}
        ),
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
        render_chart_svg(
            chart, BARS, board_style=board_rs, chart_style_context=board_ctx
        )
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-AREA-STACKED-STROKE-INCOMPLETE"


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
    with pytest.raises(ChartDataError, match="multi-metric") as exc_info:
        render_chart_svg(
            chart, BARS, board_style=board_rs, chart_style_context=board_ctx
        )
    assert exc_info.value.code.code == "ERR-BAND-STEP-MULTI-METRIC"


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
    with pytest.raises(ChartDataError, match="multi-series color") as exc_info:
        render_chart_svg(
            chart, MULTI_SERIES, board_style=board_rs, chart_style_context=board_ctx
        )
    assert exc_info.value.code.code == "ERR-STEP-BAND-MULTI-SERIES"


# ── Non-band area curve emits interpolate on fill + top-edge line ────────────


# ── Standalone area halo path: fill marks must also carry the step interpolate ─


# ─────────────────────────────────────────────────────────────────────────────
# Render-v2 parity: the v2 emitter stack must produce the same band-spanning,
# bar-centering, and tooltip behavior as the direct entry point above.
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Per-layer-query overlay is painted in authored order: layers[0] paints
# first (behind), the base chart's own series always paints first of all —
# the authored order is the z-order, the emitter never reorders layers. See
# render/chart/AGENTS.md's "Paint order is a dbt charts contract".
# ─────────────────────────────────────────────────────────────────────────────


# ── stroke-dasharray: disconnected plateaus fit whole dashes to the band ──────
#
# Each disconnected plateau is its own path, so SVG restarts the dash phase on
# every one. The authored period is pixels but the plateau length is
# ``bandwidth('x') * disconnected_width``, so a literal dasharray leaves a stub
# at the right edge. The emitter rewrites it as a Vega expression fitted to N
# dashes + N-1 gaps: ink at both plateau edges.
