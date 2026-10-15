"""The band-aware ``step`` curve transform for line/area marks.

On a categorical (nominal/ordinal) x-axis, ``curve: step`` draws a full-band-
width plateau per x-value — a target overlay that visually spans the bars it
compares against — instead of VL's native ``step``, which anchors points at
band centers. On a continuous (temporal/quantitative) x-axis, ``step`` is NOT
band-aware: it falls straight through to VL's own ``step`` interpolate.

Vega-Lite has no per-row ``bandPosition``: it is a per-channel constant, and a
field-valued ``bandPosition`` is silently dropped. So band-spanning is done with
an ``xOffset`` point scale bound to a synthetic edge column: each row is
doubled to ``0`` (band left edge) and ``1`` (band right edge). VL itself only
ever sees ``step-after`` over the doubled rows, so the categorical x-domain is
never expanded — the axis stays clean. ``step-after`` (not centered ``step``)
puts each vertical jump on the RIGHT edge of a doubled pair — i.e. on the band
boundary shared with the next band's first row — which is what the
band-doubled geometry expects. Centered ``step`` would insert a mid-vertex at
the segment midpoint that renders visible mid-band jumps on non-monotonic y.

``connect: false`` adds a ``detail`` channel keyed on the x field so each band
becomes its own path (disconnected plateaus, no vertical bridges between
bands) and insets the xOffset range so adjacent plateaus at a similar y don't
optically weld into one bar. This grouping assumes a single series (the
target-overlay idiom); a data-driven multi-series color encoding is rejected
with a clear error.

Band i's right edge pixel (``x[i] + bandwidth``) and band i+1's left edge
pixel (``x[i+1]``) are meant to be the same point but come from two different
float expressions that occasionally land one ULP apart. Vega-Lite sorts
line/area vertices by x, so at that tie the pair transposes and
``step-after`` draws a zero-width spike into the next band instead of the
plateau. Every emitter driving band-doubled rows through this transform sets
the mark's own ``order: False`` alongside ``BAND_STEP_INTERPOLATE`` — VL's
native switch to draw vertices in dataset order instead of re-sorting them —
so the two always travel together (see ``emitters/_layers.py``'s
``emit_line_layer``/``emit_area_layer``). A synthetic per-row ``order``
*encoding* was tried and rejected for this single-series, non-stacked
band-step case (an ``order`` field inherited from the shared top-level
encoding alongside the ``xOffset`` band scale): Vega-Lite folds the
per-row-distinct field into the area's implicit groupby, so instead of one
continuous filled path it emits one group per doubled row. Each group holds a
single vertex, and an area interpolates *between* vertices — the failure
``sparse_band_transforms`` below exists to prevent — so every group collapses
to a zero-width segment and the filled silhouette is not painted at all. The
chart renders as a bare stroke outline whose plateaus look correct, which is
why a plateau count that scans all paths together cannot detect it.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_STEP_BAND_MULTI_SERIES,
)

BAND_STEP_CURVE = "step"

# The VL interpolate forced onto every mark drawing the band-doubled rows.
BAND_STEP_INTERPOLATE = "step-after"

# Synthetic per-row column the xOffset point scale binds to (0 = band left edge,
# 1 = band right edge). Not a user-meaningful field — added by the transform,
# never present in source data.
STEP_BAND_EDGE_FIELD = "__step_band_pos__"

# A band scale is the categorical case; "temporal"/"quantitative" are continuous.
_BAND_SCALE_TYPES = ("nominal", "ordinal")


def is_band_step(curve: str | None, x_type: str | None) -> bool:
    """Whether ``curve`` triggers the full-band-width plateau on this x-axis.

    Band mode fires only for the literal ``step`` curve on a categorical
    (nominal/ordinal) x-scale. On a continuous (temporal/quantitative) x-scale
    ``step`` is a plain passthrough to VL's own native step interpolate.
    """
    return curve == BAND_STEP_CURVE and x_type in _BAND_SCALE_TYPES


def fit_dash_to_plateau(
    dashes: list[float], disconnected_width: float
) -> list[float] | dict[str, str]:
    """Fit a dasharray to a disconnected plateau as N dashes + N-1 gaps.

    Each plateau is its own path, so dash phase restarts per plateau while its
    length is ``bandwidth('x') * disconnected_width`` — only known to Vega at
    runtime. The returned ``strokeDash`` expression rescales the authored
    pattern so ink lands on both plateau edges.
    """
    if len(dashes) % 2:
        dashes = dashes * 2  # SVG repeats an odd list; its last entry is a gap
    if sum(dashes[0::2]) == 0:
        return dashes  # no ink to redistribute; the fit would divide by zero
    span = f"(bandwidth('x')*{disconnected_width})"
    period, last = sum(dashes), dashes[-1]
    n = f"max(1,round(({span}+{last})/{period}))"
    scale = f"{span}/({n}*{period}-{last})"
    return {"expr": "[" + ",".join(f"{d}*{scale}" for d in dashes) + "]"}


def apply_step_band(
    rows: list[dict[str, Any]],
    encoding: dict[str, Any],
    *,
    chart_id: str,
    disconnected_width: float | None,
) -> list[dict[str, Any]]:
    """Double each row to the band's edges and add the xOffset point scale.

    ``disconnected_width`` is None for a connected band. Otherwise it is the
    share of the band each plateau spans.

    Mutates ``encoding`` in place: adds the ``xOffset`` channel, and (when
    ``disconnected_width`` is set) a ``detail`` channel that breaks the path
    per band, with the xOffset range inset so disconnected plateaus don't optically weld
    into their neighbors. Returns the doubled rows. Callers must only invoke
    this when ``is_band_step`` is True (band x-axis already confirmed); raises
    ``ChartDataError`` only for a data-driven (multi-series) color encoding,
    which the per-band grouping can't honor.
    """
    x_enc = encoding.get("x")
    assert isinstance(x_enc, dict)  # narrowed: callers only invoke in band mode

    # step is a single-series target-overlay idiom in band mode: its per-band
    # xOffset / detail grouping assumes one series. A data-driven color channel
    # ("field") splits into multiple series the grouping can't honor — fail
    # fast rather than render mis-grouped silhouettes. A single-series label
    # (``datum``) or constant (``value``) color is fine.
    color_enc = encoding.get("color")
    if isinstance(color_enc, dict) and color_enc.get("field"):
        raise ChartDataError.from_code(ERR_STEP_BAND_MULTI_SERIES, chart_id=chart_id)

    doubled: list[dict[str, Any]] = []
    for row in rows:
        doubled.append({**row, STEP_BAND_EDGE_FIELD: 0})
        doubled.append({**row, STEP_BAND_EDGE_FIELD: 1})

    if disconnected_width is None:
        offset_range: list[int | dict[str, str]] = [0, {"expr": "bandwidth('x')"}]
    else:
        lo = (1 - disconnected_width) / 2
        offset_range = [
            {"expr": f"bandwidth('x')*{lo}"},
            {"expr": f"bandwidth('x')*{1 - lo}"},
        ]

    encoding["xOffset"] = {
        "field": STEP_BAND_EDGE_FIELD,
        "type": "ordinal",
        "scale": {
            "type": "point",
            "padding": 0,
            "range": offset_range,
        },
    }
    if disconnected_width is not None:
        encoding["detail"] = {"field": x_enc["field"], "type": "nominal"}

    return doubled
