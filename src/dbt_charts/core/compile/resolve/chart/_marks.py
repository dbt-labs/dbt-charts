"""Resolved line/area mark builders and label-format helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypeVar

from pydantic import BaseModel

from dbt_charts.core.compile.format import (
    resolve_format_parts_for_values,
    resolve_label_format,
)
from dbt_charts.core.compile.models.chart.normalized import (
    Chart,
)
from dbt_charts.core.compile.models.primitives import (
    FormatAliases,
    ResolvedFormat,
    resolved_as,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedAreaLineStyle,
    ResolvedAreaMarkStyle,
    ResolvedLineMarkStyle,
    ResolvedPointLabels,
    ResolvedPointMarkStyle,
    ResolvedStrokeStyle,
)
from dbt_charts.core.compile.models.style.theme import (
    AreaLineStyle,
    AreaMarkStyle,
    BarLabelsStyle,
    BarTotalLabelStyle,
    LineMarkStyle,
    MarkLabelsStyle,
    PointLabelsStyle,
    PointMarkStyle,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    chart_authored_axis_format,
)
from dbt_charts.core.text.predefined_formats import PredefinedNumberFormat

__all__ = [
    "_apply_stroke_width_fallback",
    "_build_resolved_area_line",
    "_build_resolved_area_mark",
    "_build_resolved_line_mark",
    "_label_format_fallback",
    "_measure_tooltip_format",
    "_resolve_authored_label_format",
    "_resolved_point_mark",
    "_with_label_format",
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
) -> ResolvedFormat | None:
    """Resolve the chart-authored measure (y-axis) tooltip format, if any.

    Precedence: chart_authored_axis_format (style.number_format / chart.format)
    → the already-baked axis_y.format. Returns None when neither is set, so
    callers fall back to the board default tooltip format themselves.
    ``y_channel_type`` matters for scatter, whose y can be nominal (dot plot) —
    chart_authored_axis_format returns None for non-quantitative/temporal
    channels, so a nominal y correctly skips the measure-format fallback.
    ``values`` is every value this chart's own y measure(s) will paint,
    passed to the sub-$1 vote.
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
        resolve_format_parts_for_values(
            fmt,
            chart_style_context.formats,
            values,
            no_format_default=PredefinedNumberFormat.number,
        )
        if fmt
        else None
    )


_ResolvedLabelsT = TypeVar("_ResolvedLabelsT", bound=BaseModel)
_AuthoredLabelsT = TypeVar(
    "_AuthoredLabelsT",
    MarkLabelsStyle,
    BarLabelsStyle,
    PointLabelsStyle,
    BarTotalLabelStyle,
)


def _with_label_format(
    labels: MarkLabelsStyle | BarTotalLabelStyle,
    resolved_cls: type[_ResolvedLabelsT],
    fmt: ResolvedFormat | None,
) -> _ResolvedLabelsT:
    """``labels`` as ``resolved_cls`` carrying the resolved ``fmt``."""
    return resolved_as(resolved_cls, labels, format=fmt)


def _label_format_fallback(
    labels: _AuthoredLabelsT,
    resolved_cls: type[_ResolvedLabelsT],
    axis_format: ResolvedFormat | None,
    axis_house_default: bool,
    formats: FormatAliases | None,
    values: Iterable[float | None] = (),
) -> tuple[_ResolvedLabelsT, bool]:
    """Resolve ``labels`` as ``resolved_cls``, falling an unset format back to the axis's.

    Returns ``(labels, is_house)``: house paints the narrative register
    (1.2mn), otherwise the spec goes straight to Vega (1.2M). An explicit
    format is resolved by ``resolve_label_format``; the fallback inherits the
    axis format un-voted, with ``axis_house_default`` so label and axis agree.
    ``values`` are what the label paints, for the sub-$1 vote.
    """
    if labels.format is not None:
        resolved, is_house = resolve_label_format(labels.format, formats, values)
        return _with_label_format(labels, resolved_cls, resolved), is_house
    return _with_label_format(labels, resolved_cls, axis_format), (
        axis_format is not None and axis_house_default
    )


def _resolve_authored_label_format(
    labels: _AuthoredLabelsT,
    resolved_cls: type[_ResolvedLabelsT],
    formats: FormatAliases | None,
    values: Iterable[float | None] = (),
) -> _ResolvedLabelsT:
    """``labels`` with only its own authored format (no axis fallback) resolved."""
    fmt, _ = resolve_label_format(labels.format, formats, values)
    return _with_label_format(labels, resolved_cls, fmt)


def _resolved_point_mark(
    point_mark: PointMarkStyle,
    formats: FormatAliases | None,
    values: Iterable[float | None] = (),
) -> ResolvedPointMarkStyle:
    return resolved_as(
        ResolvedPointMarkStyle,
        point_mark,
        labels=_resolve_authored_label_format(
            point_mark.labels, ResolvedPointLabels, formats, values
        ),
    )


def _build_resolved_line_mark(
    merged: LineMarkStyle, labels: ResolvedPointLabels
) -> ResolvedLineMarkStyle:
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
        labels=labels,
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


def _build_resolved_area_line(
    merged: AreaLineStyle, labels: ResolvedPointLabels
) -> ResolvedAreaLineStyle:
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
        labels=labels,
    )
