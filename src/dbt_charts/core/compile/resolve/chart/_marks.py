"""Resolved line/area mark builders and label-format helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypeVar

from dbt_charts.core.compile.format import (
    resolve_format_for_values,
    resolve_label_format,
)
from dbt_charts.core.compile.models.chart.normalized import (
    Chart,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedAreaLineStyle,
    ResolvedAreaMarkStyle,
    ResolvedLineMarkStyle,
    ResolvedStrokeStyle,
)
from dbt_charts.core.compile.models.style.theme import (
    AreaLineStyle,
    AreaMarkStyle,
    LineMarkStyle,
    MarkLabelsStyle,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    chart_authored_axis_format,
)

__all__ = [
    "_apply_stroke_width_fallback",
    "_build_resolved_area_line",
    "_build_resolved_area_mark",
    "_build_resolved_line_mark",
    "_label_format_fallback",
    "_measure_tooltip_format",
]

_LineOrAreaLineT = TypeVar("_LineOrAreaLineT", LineMarkStyle, AreaLineStyle)


def _apply_stroke_width_fallback(
    mark: _LineOrAreaLineT, fallback_width: float
) -> _LineOrAreaLineT:
    """Fill ``mark.stroke.width`` from ``fallback_width`` if still unset.

    Called after cascade + adaptive baking, for any line-like mark
    (line's own stroke, area's overlap-edge or stacked-perimeter stroke, a
    line-type layer's stroke): if no tier pinned a width and adaptive
    couldn't compute one either (no data, no x field, non-cartesian), this
    is the engine's calibrated floor, not a theme choice -- see
    ``chart_rendering.stroke.fallback_width`` / ``.stacked_fallback_width``
    in ``default_config.yml``. A zero width (the "no stroke" sentinel) is
    a real, already-baked value and is left untouched.

    An authored ``stroke: null`` (``mark.stroke is None``) is left alone too
    -- filling one in here would silently manufacture a default stroke out
    of nothing (no cap/join/color) instead of letting the downstream
    ``_build_resolved_*`` guard raise its "stroke is None after cascade"
    error.
    """
    if mark.stroke is None or mark.stroke.width is not None:
        return mark
    return mark.model_copy(
        update={"stroke": mark.stroke.model_copy(update={"width": fallback_width})}
    )


def _measure_tooltip_format(
    normalized: Chart,
    primary: Any,
    chart_style_context: ChartStyleContext,
    y_channel_type: str = "quantitative",
    *,
    values: Iterable[float | None],
) -> str | None:
    """Resolve the chart-authored measure (y-axis) tooltip format, if any.

    Precedence: chart_authored_axis_format (style.number_format / chart.format)
    → the already-baked axis_y.format. Returns None when neither is set, so
    callers fall back to the board default tooltip format themselves.
    ``y_channel_type`` matters for scatter, whose y can be nominal (dot plot) —
    chart_authored_axis_format returns None for non-quantitative/temporal
    channels, so a nominal y correctly skips the measure-format fallback.
    ``values`` is every value this chart's own y measure(s) will paint,
    passed through to resolve_format_for_values's per-chart sub-$1 vote.
    """
    fmt = chart_authored_axis_format(normalized, y_channel_type)
    if (
        fmt is None
        and primary is not None
        and primary.axis_y is not None
        and primary.axis_y.labels is not None
    ):
        fmt = primary.axis_y.labels.format
    return (
        resolve_format_for_values(fmt, chart_style_context.formats, values)
        if fmt
        else None
    )


_LabelsT = TypeVar("_LabelsT", bound=MarkLabelsStyle)


def _label_format_fallback(
    labels: _LabelsT,
    axis_format: str | None,
    axis_house_default: bool,
    formats: dict[str, str] | None,
) -> tuple[_LabelsT, bool]:
    """Fall an unset value-label format back to the resolved measure-axis format.

    Returns ``(updated_labels, is_house)`` where ``is_house`` controls whether
    the render layer wraps the SI format in the narrative register (1.2mn) or
    passes it straight to Vega (raw d3: 1.2M).

    An explicit label format is decided by predefined-membership alone
    (``resolve_label_format`` — see ``ResolvedAxisStyle.format_raw`` for the
    house-vs-literal rule it applies). The fallback branch (no label format
    authored) instead inherits ``axis_house_default`` as callers compute it
    — the axis's own raw format winner run through the same predicate,
    since inheriting a label must agree with what its own axis renders,
    including the axis's own literal-format escape hatch.

    Unlike ``axis_format`` (already resolved via ``resolve_format``), an explicit
    ``labels.format`` passes through ``resolve_label_format`` for both alias
    resolution and round-aware trimming — so ``format: currency`` resolves to
    ``$,.2f`` here, not the literal alias key.
    """
    if labels.format is not None:
        resolved, is_house = resolve_label_format(labels.format, formats)
        return labels.model_copy(update={"format": resolved}), is_house
    if axis_format is None:
        return labels, False
    return labels.model_copy(update={"format": axis_format}), axis_house_default


def _build_resolved_line_mark(merged: LineMarkStyle) -> ResolvedLineMarkStyle:
    """Build a ResolvedLineMarkStyle from an already-merged LineMarkStyle."""
    # merged.stroke.width can't be None here: _apply_stroke_width_fallback
    # already ran and fills width whenever a stroke object exists. Only
    # merged.stroke itself being None (an authored `stroke: null`) can
    # still reach this guard -- the `or merged.stroke.width is None` half is
    # unreachable at runtime but kept so the type checker narrows
    # `merged.stroke.width` from `float | None` to `float` below.
    if merged.stroke is None or merged.stroke.width is None:
        raise ValueError(
            "line.marks.line.stroke is None after cascade — an authored "
            "`stroke: null` is not valid on a line mark"
        )
    if merged.halo_multiplier is None:
        raise ValueError(
            "line.marks.line.halo_multiplier is None after cascade — check theme defaults"
        )
    return ResolvedLineMarkStyle(
        stroke=ResolvedStrokeStyle(
            width=merged.stroke.width,
            color=merged.stroke.color,
            cap=merged.stroke.cap,
            join=merged.stroke.join,
            dasharray=merged.stroke.dasharray,
        ),
        halo_multiplier=merged.halo_multiplier,
        curve=merged.curve,
        connect=merged.connect,
        disconnected_cap=merged.disconnected_cap,
        labels=merged.labels,
    )


def _build_resolved_area_mark(merged: AreaMarkStyle) -> ResolvedAreaMarkStyle:
    """Build a ResolvedAreaMarkStyle (fill only) from an already-merged AreaMarkStyle."""
    if merged.opacity is None:
        raise ValueError(
            "area.marks.area.opacity is None after cascade — check theme defaults"
        )
    if merged.backdrop is None:
        raise ValueError(
            "area.marks.area.backdrop is None after cascade — check theme defaults"
        )
    return ResolvedAreaMarkStyle(
        opacity=merged.opacity, curve=merged.curve, backdrop=merged.backdrop
    )


def _build_resolved_area_line(merged: AreaLineStyle) -> ResolvedAreaLineStyle:
    """Build a ResolvedAreaLineStyle from an already-merged AreaLineStyle.

    Area's top-edge line: stroke/halo geometry + value labels. Mirrors
    _build_resolved_line_mark minus curve/connect (owned solely by
    ResolvedAreaMarkStyle.curve — see AreaLineStyle's docstring).
    """
    # merged.stroke.width can't be None here: _apply_stroke_width_fallback
    # already ran and fills width whenever a stroke object exists. Only
    # merged.stroke itself being None (an authored `stroke: null`) can
    # still reach this guard -- the `or merged.stroke.width is None` half is
    # unreachable at runtime but kept so the type checker narrows
    # `merged.stroke.width` from `float | None` to `float` below.
    if merged.stroke is None or merged.stroke.width is None:
        raise ValueError(
            "area.marks.line.stroke is None after cascade — an authored "
            "`stroke: null` is not valid on an area's top-edge line"
        )
    if merged.halo_multiplier is None:
        raise ValueError(
            "area.marks.line.halo_multiplier is None after cascade — check theme defaults"
        )
    return ResolvedAreaLineStyle(
        stroke=ResolvedStrokeStyle(
            width=merged.stroke.width,
            color=merged.stroke.color,
            cap=merged.stroke.cap,
            join=merged.stroke.join,
            dasharray=merged.stroke.dasharray,
        ),
        halo_multiplier=merged.halo_multiplier,
        labels=merged.labels,
    )
