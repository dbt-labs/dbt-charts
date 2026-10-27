"""Detector: WARN_LAYERED_CHART_SHARED_Y_AXIS_SCALE_MISMATCH — see its `doc`
in core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  chart has typed overlay ``layers`` (bar/line/area/scatter with layers: [...])
  AND no layer pins its own y-axis (one pinned layer makes every layer
    independent of the base, so there is no shared scale to crush anything)
  AND at least 2 distinct y series share the base chart's y-axis
  AND one series reaches under ``_REACH_THRESHOLD`` of the shared axis.

A series' reach is the height of its drawn band over the axis span: the band
runs from zero when the axis includes zero (a bar hangs from it, a line near
the floor reads the same), else it is the series' own variation. The axis span
is ``effective_measure_domain`` over every compared series (an authored domain
wins; a log or non-quantitative axis abstains). A
series with no height (a constant on a zoomed axis) is flat by its data, not
crushed, and is not compared.

A layer carrying its own ``query:`` is compared like any other — its rows come
from ``ctx.layer_results`` rather than the chart's own result set. That shape
(one query per layer) is what the deterministic migrator emits, so excluding it
would blind the detector to the corpus it helps most. A series is therefore
identified by (query, column), not column alone: two queries both selecting
``value`` are two series on one axis, not one shared scale.

Diagnostic.field is None — the issue is cross-column, not column-scoped.
The message names the widest series and the one with the least reach; the
diagnostic marks where each was authored: `path` on the widest series,
`related` on the one that gets crushed.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved._layer import LayeredResolvedChart
from dbt_charts.core.diagnostics import (
    WARN_LAYERED_CHART_SHARED_Y_AXIS_SCALE_MISMATCH,
    Diagnostic,
    RelatedLocation,
)
from dbt_charts.core.render.chart.emitters._cartesian import (
    authored_measure_domain,
    effective_measure_domain,
)
from dbt_charts.core.render.warnings.base import WarningContext
from dbt_charts.core.utils import numeric_column_values

# Fire when a series' drawn band is under this share of the shared axis.
_REACH_THRESHOLD = 0.06


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart+layers with a ≥100× y-scale mismatch."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, LayeredResolvedChart) or not chart.layers:
            continue
        if chart_id not in ctx.chart_results:
            continue

        base_rows = ctx.chart_results[chart_id]
        layer_rows = ctx.layer_results.get(
            chart_id, {}
        )  # type-state: silent_fallback — sparse: no per-layer query, no entry

        # One pinned layer makes every layer independent of the base: no
        # shared scale, nothing to crush.
        if any(layer.axis_y.position is not None for layer in chart.layers):
            continue

        # One entry per authored y series: (query, column, authored path). A
        # layer with no authored override bakes query_name to the base chart's
        # own (see _resolve_one_layer), so comparing against chart.query_name —
        # not "is not None" — is what tells a real override from an inherited
        # one. The authored path rides along so the two extremes can be marked
        # where they were actually written.
        series: list[tuple[str | None, str, str]] = []
        base_y = chart.y
        if isinstance(base_y, str):
            series.append((chart.query_name, base_y, f"charts.{chart_id}.y"))
        for idx, layer in enumerate(chart.layers):
            if layer.y is not None:
                series.append(
                    (layer.query_name, layer.y, f"charts.{chart_id}.layers.{idx}.y")
                )

        # (path, column, min, max) per series with values to draw.
        extents: list[tuple[str, str, float, float]] = []
        seen_series: set[tuple[str | None, str]] = set()

        for query_name, col, path in series:
            # Same query and same column name is literally the same values —
            # one scale, nothing to compare.
            if (query_name, col) in seen_series:
                continue
            seen_series.add((query_name, col))

            # An override reads its own result set; a layer that inherited the
            # base chart's query reads the chart's own rows.
            if query_name == chart.query_name:
                rows = base_rows
            elif query_name in layer_rows:
                rows = layer_rows[query_name]
            else:
                # The layer's own query failed to execute — nothing to judge.
                continue

            values = numeric_column_values(rows, col)
            if values:
                extents.append((path, col, min(values), max(values)))

        if len(extents) < 2:
            continue

        axis_y = chart.style.axis_y
        cont = axis_y.scale.continuous if axis_y.scale is not None else None
        if not axis_y.is_quantitative or (cont is not None and cont.type == "log"):
            continue
        # The canonical answer to "what range does this axis render": an
        # authored domain wins, then the baked edges, then the data extent.
        domain = effective_measure_domain(
            axis_y, (min(e[2] for e in extents), max(e[3] for e in extents))
        )
        if domain is None:
            continue
        # One fact for both the span and the band: the baked `zero_anchored`
        # (resolve decides it for a bar, which never bakes `zero`). An authored
        # domain is the rendered axis outright, zero or not.
        zero_anchored = axis_y.zero_anchored
        floor, ceiling = domain
        if zero_anchored and authored_measure_domain(axis_y) is None:
            floor, ceiling = min(floor, 0.0), max(ceiling, 0.0)
        span = ceiling - floor
        if span == 0.0:
            continue

        # Reach: the series' drawn band over the axis span, clipped to the axis.
        # On a zero-anchored axis the band runs from zero (a bar's height); on a
        # zoomed axis it is only the series' own variation, so a series with
        # none is flat by its data, not crushed.
        reaches: list[tuple[float, str, str]] = []
        for path, col, lo, hi in extents:
            if zero_anchored:
                lo, hi = min(lo, 0.0), max(hi, 0.0)
            lo, hi = max(lo, floor), min(hi, ceiling)
            if hi > lo:
                reaches.append(((hi - lo) / span, path, col))
        if len(reaches) < 2:
            continue

        min_reach, min_path, min_col = min(reaches)
        _, max_path, max_col = max(reaches)
        if min_reach >= _REACH_THRESHOLD:
            continue

        # The complaint is about one series squeezed by another, so it is
        # anchored on the widest series and carries the crushed one as a related
        # location. Marking only one of the pair, or the whole chart block,
        # loses which two columns are at odds.
        warnings.append(
            Diagnostic.from_code(
                WARN_LAYERED_CHART_SHARED_Y_AXIS_SCALE_MISMATCH,
                chart=chart_id,
                field=None,
                path=max_path,
                related=(
                    RelatedLocation(
                        path=min_path,
                        message=(
                            f"{min_col!r} reaches {min_reach:.1%} of the axis — "
                            "this is the series that flattens"
                        ),
                    ),
                ),
                message=WARN_LAYERED_CHART_SHARED_Y_AXIS_SCALE_MISMATCH.message_template.format(
                    chart_id=chart_id, col_a=max_col, col_b=min_col, reach=min_reach
                ),
                fix=WARN_LAYERED_CHART_SHARED_Y_AXIS_SCALE_MISMATCH.fix_template,
            )
        )

    return warnings
