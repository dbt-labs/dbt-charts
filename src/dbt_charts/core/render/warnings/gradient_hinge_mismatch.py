"""Detector: WARN_GRADIENT_HINGE_MISMATCH — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule: a continuous color scale bound to a data field (a color
channel, which a heatmap's gradient is; a geoshape's gradient; a table column) pivots at its hinge, and the pivot is
not a neutral value the author chose when

  - the palette is diverging and ``hinge`` is unset (neutral = domain midpoint);
  - the palette is sequential and ``hinge`` is set (no neutral center exists);
  - ``hinge`` is ``auto`` and the domain does not cross zero, so the pivot falls
    back to the midpoint (``auto_hinge_pivot`` is the engine's own decision).

An inline stop list has no known family, so only the ``auto`` case applies to it.
The domain is the scale's authored ``min``/``max`` where set, else the data's.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved.geoshape import ResolvedGeoshapeChart
from dbt_charts.core.compile.models.chart.resolved.table import ResolvedTableChart
from dbt_charts.core.compile.models.primitives import (
    ResolvedNamedPaletteScaleTargetConfig,
    ResolvedScaleTarget,
)
from dbt_charts.core.compile.models.schema_names import (
    VEGA_DIVERGING_SCHEME_NAMES,
    VEGA_SEQUENTIAL_SCHEME_NAMES,
)
from dbt_charts.core.diagnostics import WARN_GRADIENT_HINGE_MISMATCH, Diagnostic
from dbt_charts.core.render.chart.table_support import auto_hinge_pivot
from dbt_charts.core.render.warnings.base import WarningContext
from dbt_charts.core.text.predefined_formats import PREDEFINED_SPECS
from dbt_charts.core.utils import Rows, numeric_column_values

# Gradient channels that scale a number, not a color: no palette family applies.
_NUMERIC_CHANNELS = frozenset({"opacity", "stroke_width"})

_Family = Literal["sequential", "diverging"]

# (authored path, data field, scale, number format spec the column prints with)
_Binding = tuple[str | None, str, ResolvedScaleTarget, str | None]


def _family(scale: ResolvedScaleTarget) -> _Family | None:
    palette = scale.palette
    if not isinstance(palette, str):
        return None
    if palette in VEGA_DIVERGING_SCHEME_NAMES:
        return "diverging"
    if palette in VEGA_SEQUENTIAL_SCHEME_NAMES:
        return "sequential"
    if isinstance(scale, ResolvedNamedPaletteScaleTargetConfig):
        return scale.family if scale.family in ("sequential", "diverging") else None
    return None  # categorical or cyclical Vega scheme: no center, no ramp to pivot


def _bindings(chart_id: str, chart: ResolvedChart) -> Iterator[_Binding]:
    for channel in chart.resolved_channels.values():
        if channel.channel in _NUMERIC_CHANNELS:
            continue
        if channel.mode == "gradient" and channel.scale is not None:
            yield None, channel.data_field, channel.scale, None
        if channel.mode == "conditional" and channel.fallback_scale is not None:
            yield None, channel.data_field, channel.fallback_scale, None
    if isinstance(chart, ResolvedGeoshapeChart):
        geoshape = chart.style.geoshape
        color = geoshape.color if geoshape is not None else None
        if color is not None and color.gradient is not None and chart.value_field:
            yield (
                f"charts.{chart_id}.style.color.gradient",
                chart.value_field,
                color.gradient,
                None,
            )
    elif isinstance(chart, ResolvedTableChart) and chart.columns is not None:
        for name, column in chart.columns.items():
            if column.scale is None:
                continue
            fmt = column.format.spec if column.format is not None else None
            if fmt in PREDEFINED_SPECS:
                fmt = PREDEFINED_SPECS[fmt].d3
            for slot in ("background", "color"):
                scale = getattr(column.scale, slot)
                if scale is not None:
                    yield (
                        f"charts.{chart_id}.style.columns.{name}.scale.{slot}",
                        name,
                        scale,
                        fmt,
                    )


def _problem(
    scale: ResolvedScaleTarget, rows: Rows, field: str, col_format: str | None
) -> str | None:
    family = _family(scale)
    if family == "diverging" and scale.hinge is None:
        return (
            f"uses the diverging palette {scale.palette!r} with no hinge, so its "
            "neutral color falls at the middle of the data, not at a chosen value"
        )
    if family == "sequential" and scale.hinge is not None:
        return (
            f"sets a hinge on the sequential palette {scale.palette!r}, which has "
            "no neutral center to pivot on"
        )
    if scale.hinge != "auto":
        return None
    values = numeric_column_values(rows, field)
    lo = scale.min if scale.min is not None else min(values, default=None)
    hi = scale.max if scale.max is not None else max(values, default=None)
    if lo is None or hi is None or lo == hi:
        return None
    if auto_hinge_pivot(lo, hi, col_format) is not None:
        return None
    return (
        f"sets hinge: auto but its domain {lo:g} to {hi:g} does not cross zero, so "
        f"the pivot falls back to the midpoint {(lo + hi) / 2:g}"
    )


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per color scale whose hinge does not fit its palette or data."""
    warnings: list[Diagnostic] = []
    for chart_id, chart in ctx.board_spec.charts.items():
        if chart_id not in ctx.chart_results:
            continue
        rows = ctx.chart_results[chart_id]
        for path, field, scale, col_format in _bindings(chart_id, chart):
            problem = _problem(scale, rows, field, col_format)
            if problem is None:
                continue
            warnings.append(
                Diagnostic.from_code(
                    WARN_GRADIENT_HINGE_MISMATCH,
                    chart=chart_id,
                    field=field,
                    path=path,
                    message=WARN_GRADIENT_HINGE_MISMATCH.message_template.format(
                        chart_id=chart_id, field=field, problem=problem
                    ),
                    fix=WARN_GRADIENT_HINGE_MISMATCH.fix_template,
                )
            )
    return warnings
