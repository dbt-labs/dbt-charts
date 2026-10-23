"""Detector: WARN_LAYER_DATA_OUTSIDE_DOMAIN — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

A layer's rows come from its own query when it overrides the chart's, else from
the chart's result set. One diagnostic per offending layer, anchored on its
authored ``axis_y.scale.domain``.
"""

from __future__ import annotations

import math

from dbt_charts.core.compile.models.chart.resolved._layer import (
    LayeredResolvedChart,
    ResolvedLayer,
)
from dbt_charts.core.diagnostics import WARN_LAYER_DATA_OUTSIDE_DOMAIN, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def painted_float(
    value: object,  # type-state: object_annotation — duck-types an arbitrary row value through float()
) -> float | None:
    """The finite float the chart paints for ``value``, else ``None``.

    Anything JS's ``Number()`` accepts paints, including a numeric string (a
    ``::text``-cast or CSV-sourced column); a non-finite or non-numeric value is
    dropped. All three ``except`` members are load-bearing: ``ArithmeticError``
    also catches ``OverflowError`` from a Python ``int`` too large to represent as
    a ``float`` — reachable via an authored inline ``values:`` query.
    """
    try:
        f = float(value)  # type: ignore[arg-type]  # type-state: type_ignore — duck-typed row value; the except below is the real type guard
    except (TypeError, ValueError, ArithmeticError):
        return None
    return f if math.isfinite(f) else None


def paints_outside_domain(
    value: object,  # type-state: object_annotation — duck-typed row value, see painted_float
    domain: tuple[float, float],
) -> bool:
    """True iff ``painted_float(value)`` lands outside ``domain``."""
    painted = painted_float(value)
    if painted is None:
        return False
    lo, hi = domain
    return not lo <= painted <= hi


def layers_have_own_y_scale(layers: tuple[ResolvedLayer, ...]) -> bool:
    """True iff the layers get independent y scales, so a layer's authored
    ``scale.domain`` is honored and clipped.

    Chart-wide: one layer pinning ``axis_y.position`` (either side) makes every
    layer independent; with none pinned they all share the base scale.
    """
    return any(layer.axis_y.position in ("left", "right") for layer in layers)


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per layer whose clipped domain hides some of its data."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, LayeredResolvedChart) or chart_id not in (
            ctx.chart_results
        ):
            continue
        for idx, layer in enumerate(chart.layers):
            scale = layer.axis_y.scale
            if layer.y is None or scale is None or scale.domain is None:
                continue
            if not scale.clip or not layers_have_own_y_scale(chart.layers):
                continue
            layer_rows = ctx.layer_results.get(
                chart_id, {}
            )  # type-state: silent_fallback — sparse: no per-layer query, no entry
            if layer.query_name == chart.query_name:
                rows = ctx.chart_results[chart_id]
            elif layer.query_name in layer_rows:
                rows = layer_rows[layer.query_name]
            else:
                continue
            if not rows:
                continue
            lo, hi = min(scale.domain), max(scale.domain)
            if not any(
                paints_outside_domain(row.get(layer.y), (lo, hi)) for row in rows
            ):
                continue
            values = [
                f for row in rows if (f := painted_float(row.get(layer.y))) is not None
            ]
            warnings.append(
                Diagnostic.from_code(
                    WARN_LAYER_DATA_OUTSIDE_DOMAIN,
                    chart=chart_id,
                    field=layer.y,
                    path=f"charts.{chart_id}.layers.{idx}.axis_y.scale.domain",
                    message=WARN_LAYER_DATA_OUTSIDE_DOMAIN.message_template.format(
                        chart_id=chart_id,
                        layer_col=layer.y,
                        data_min=min(values),
                        data_max=max(values),
                        lo=lo,
                        hi=hi,
                    ),
                    fix=WARN_LAYER_DATA_OUTSIDE_DOMAIN.fix_template,
                )
            )

    return warnings
