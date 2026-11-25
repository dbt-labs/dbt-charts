"""Resolved line/area mark builders and label-format helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from dbt_charts.core.compile.format import (
    column_format,
    declared_column_format,
    inferred_number_format,
    resolve_label_format,
)
from dbt_charts.core.compile.models.primitives import (
    AuthoredFormat,
    BareRun,
    FormatConfig,
    FormatTable,
    ResolvedFormat,
    StrokeStyle,
    resolved_as,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedAreaLineStyle,
    ResolvedAreaMarkStyle,
    ResolvedAxisStyle,
    ResolvedLineMarkStyle,
    ResolvedPointLabels,
    ResolvedPointMarkStyle,
    ResolvedRulerAxis,
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
from dbt_charts.core.compile.resolve.style.chart_context import CartesianFamilyChart
from dbt_charts.core.compile.resolve.style.inherit_resolver import settled
from dbt_charts.core.text.format_d3 import is_d3_si_spec
from dbt_charts.core.text.numeral_scale import (
    bare_run_decimals,
    bare_run_grid,
    percent_run_decimals,
    run_joins_axis,
    without_symbol,
)

__all__ = [
    "_apply_line_width",
    "_build_resolved_area_line",
    "_build_resolved_area_mark",
    "_build_resolved_line_mark",
    "_label_format_fallback",
    "_measure_tooltip_format",
    "_resolve_authored_label_format",
    "_resolved_point_mark",
    "_run_axis",
    "_with_label_format",
]

_TRIMMED_PERCENT_NAMES = frozenset({"percent", "percent_delta"})

_LineOrAreaLineT = TypeVar("_LineOrAreaLineT", LineMarkStyle, AreaLineStyle)


def _apply_line_width(
    mark: _LineOrAreaLineT, adaptive: float, fallback: float
) -> _LineOrAreaLineT:
    """Resolve ``mark.stroke.width`` for a line-like mark.

    A width any cascade tier pinned wins, including the ``0.0`` "no stroke"
    sentinel. Otherwise the density-adaptive ``adaptive`` applies, or
    ``fallback`` when it is ``0.0`` (no data, no x field, or a base chart that
    measures no density).
    """
    if mark.stroke is None or mark.stroke.width is not None:
        return mark
    width = adaptive if adaptive > 0 else fallback
    return mark.model_copy(
        update={"stroke": mark.stroke.model_copy(update={"width": width})}
    )


def _measure_tooltip_format(
    normalized: CartesianFamilyChart,
    primary: Any,
    chart_style_context: ChartStyleContext,
    y_channel_type: str = "quantitative",
    *,
    values: Iterable[float | None],
) -> ResolvedFormat | None:
    """Resolve the chart-authored measure (y-axis) tooltip format, if any.

    Precedence, strongest first: the chart's own axis_y labels.format → the
    query's declared format for the y column(s). Returns None when neither is
    set, so callers fall back to the board default tooltip format themselves.
    ``y_channel_type`` matters for scatter, whose y can be nominal (dot plot) —
    a nominal y takes no declared format.
    ``values`` is every value this chart's own y measure(s) will paint,
    passed to the sub-$1 vote.
    """
    formats = chart_style_context.formats
    local = None
    if primary is not None and primary.axis_y is not None:
        local = primary.axis_y.labels.format if primary.axis_y.labels else None
    query_formats = (
        normalized.query.formats
        if normalized.query and y_channel_type in ("quantitative", "temporal")
        else None
    )
    if local is None and (
        declared_column_format(
            query_formats, normalized.y, formats, time=y_channel_type == "temporal"
        )
        is None
    ):
        return None
    return column_format(
        "number",
        formats,
        local,
        query_formats=query_formats,
        columns=normalized.y,
        values=values,
    )


_ResolvedLabelsT = TypeVar("_ResolvedLabelsT", bound=BaseModel)
_ResolvedPointT = TypeVar("_ResolvedPointT", bound=ResolvedPointMarkStyle)
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


def _run_axis(
    labels: MarkLabelsStyle | BarLabelsStyle | PointLabelsStyle,
    value_axis: ResolvedAxisStyle,
) -> ResolvedAxisStyle | None:
    """``value_axis`` when ``labels`` print the measure on every mark: a run
    beside that axis. Anchored subsets and a ``field`` column are not a run."""
    return value_axis if labels.visible is True and labels.field is None else None


def _label_format_fallback(
    labels: _AuthoredLabelsT,
    resolved_cls: type[_ResolvedLabelsT],
    axis_format: ResolvedFormat | None,
    axis_house_default: bool,
    formats: FormatTable | None,
    values: Iterable[float | None] = (),
    run_axis: ResolvedAxisStyle | None = None,
) -> tuple[_ResolvedLabelsT, bool]:
    """Resolve ``labels`` as ``resolved_cls``, falling an unset format back to the axis's.

    Returns ``(labels, is_house)``: house paints the narrative register
    (1.2m), otherwise the spec goes straight to Vega (1.2M). An explicit
    format is resolved by ``resolve_label_format``; the fallback inherits the
    axis format un-voted; under a house axis it prints the narrative register.
    ``values`` are what the label paints, for the sub-$1 vote. ``run_axis`` is
    the value axis beside a run of labels on every point: when it is visible
    and declares its unit (an SI ruler, or a house percent format) the fallback
    drops that unit (``run_label_format``, ``percent_run_format``).
    """
    if labels.format is not None:
        resolved, is_house = resolve_label_format(labels.format, formats, values)
        return _with_label_format(labels, resolved_cls, resolved), is_house
    in_run = run_axis is not None and run_axis.labels.visible is not False
    if (
        in_run
        and axis_format is not None
        and axis_format.is_house
        and axis_format.spec.endswith("%")
    ):
        run_format = percent_run_format(axis_format, values)
        return _with_label_format(labels, resolved_cls, run_format), False
    if axis_format is None or not axis_house_default:
        return (
            _with_label_format(
                labels,
                resolved_cls,
                None
                if axis_format is None
                else trim_house_percent(axis_format, formats),
            ),
            False,
        )
    # The axis spells its letter analytic (K); a label beside a mark sets the
    # narrative register (k) as its own layer and takes the rest from the axis.
    narrative = column_format(
        "number", formats, FormatConfig(notation="narrative"), board=axis_format
    )
    if run_axis is not None and in_run and run_axis.ruler is not None:
        narrative = run_label_format(narrative, run_axis.ruler, values)
    return _with_label_format(labels, resolved_cls, narrative), True


def _bare_run_format(fmt: ResolvedFormat, bare: BareRun | None) -> ResolvedFormat:
    """``fmt`` without the symbol or prefix the axis declares, painting bare
    digits at ``bare`` (exponent, decimals) or, when None, with its own letter."""
    return ResolvedFormat(
        spec=without_symbol(fmt.spec),
        prefix="",
        suffix=fmt.suffix,
        notation=fmt.notation,
        sign_placement=fmt.sign_placement,
        repeat=fmt.repeat,
        raw=fmt.raw,
        bare=bare,
    )


def run_label_format(
    narrative: ResolvedFormat,
    ruler: ResolvedRulerAxis,
    values: Iterable[float | None],
) -> ResolvedFormat:
    """The format of a run of labels beside a value axis that declares the unit.

    The run joins the axis's magnitude (bare digits at shared decimals) or
    carries its own letter (``numeral_scale.run_joins_axis``); either way the
    symbol the axis declares is dropped. One decision for the whole run, read by
    the label's fit and its paint.
    """
    painted = [v for v in values if v is not None]
    if not run_joins_axis(painted, ruler.exponent):
        return _bare_run_format(narrative, None)
    decimals = bare_run_decimals(painted, ruler.exponent, narrative.spec)
    digits, pad = bare_run_grid(painted, ruler.exponent, decimals, True)
    return _bare_run_format(narrative, BareRun(ruler.exponent, decimals, digits, pad))


def trim_house_percent(
    fmt: ResolvedFormat, formats: FormatTable | None
) -> ResolvedFormat:
    """A value label's house ``percent``/``percent_delta`` trimming a trailing
    zero (``40%``, not ``40.0%``). A board that redefines the name keeps its own
    spec."""
    if (
        fmt.raw not in _TRIMMED_PERCENT_NAMES
        or (formats is not None and fmt.raw in formats)
        or fmt.has_affix
    ):
        return fmt
    return ResolvedFormat(
        spec=fmt.spec.replace("%", "~%"),
        sign_placement=fmt.sign_placement,
        repeat=fmt.repeat,
        raw=fmt.raw,
    )


def percent_run_format(
    axis_format: ResolvedFormat, values: Iterable[float | None]
) -> ResolvedFormat:
    """A run of labels under a house percent axis: bare percent digits, or the
    axis's own format (each label keeps its ``%``) when one would print below 0.1."""
    painted = [v for v in values if v is not None]
    decimals = percent_run_decimals(painted, axis_format.spec)
    if decimals is None:
        return axis_format
    digits, pad = bare_run_grid(painted, -2, decimals, False)
    return _bare_run_format(axis_format, BareRun(-2, decimals, digits, pad))


def datum_format(
    labels_format: AuthoredFormat | None,
    formats: FormatTable | None,
    query_formats: Mapping[str, str] | None,
    column: str,
    rows: Sequence[Mapping[str, Any]],  # type-state: explicit_any — warehouse rows
) -> tuple[ResolvedFormat, bool]:
    """The format a datum prints in beside its mark, never the ruler's or the tooltip's.

    The authored format, then the column's declared one, then the type default
    (an integer column prints whole), then the board's default for the kind.
    Returns ``(format, is_house)``.
    """
    cells = [row.get(column) for row in rows]
    fmt = column_format(
        "number",
        formats,
        labels_format,
        query_formats=query_formats,
        columns=column,
        inferred=inferred_number_format(column, cells),
        values=[c for c in cells if c is not None],
    )
    return fmt, fmt.is_house and is_d3_si_spec(fmt.spec)


def callout_label_format(
    labels: _AuthoredLabelsT,
    resolved_cls: type[_ResolvedLabelsT],
    formats: FormatTable | None,
    query_formats: Mapping[str, str] | None,
    column: str,
    rows: Sequence[Mapping[str, Any]],  # type-state: explicit_any — warehouse rows
) -> tuple[_ResolvedLabelsT, bool]:
    """``labels`` resolved as a callout: a datum beside its mark (see ``datum_format``)."""
    fmt, is_house = datum_format(labels.format, formats, query_formats, column, rows)
    return _with_label_format(labels, resolved_cls, fmt), is_house


def _resolve_authored_label_format(
    labels: _AuthoredLabelsT,
    resolved_cls: type[_ResolvedLabelsT],
    formats: FormatTable | None,
    values: Iterable[float | None] = (),
) -> _ResolvedLabelsT:
    """``labels`` with only its own authored format (no axis fallback) resolved."""
    fmt, _ = resolve_label_format(labels.format, formats, values)
    return _with_label_format(labels, resolved_cls, fmt)


def _resolved_point_mark(
    point_mark: PointMarkStyle,
    formats: FormatTable | None,
    resolved_cls: type[_ResolvedPointT],
    values: Iterable[float | None] = (),
) -> _ResolvedPointT:
    return resolved_as(
        resolved_cls,
        point_mark,
        labels=_resolve_authored_label_format(
            point_mark.labels, ResolvedPointLabels, formats, values
        ),
    )


def _resolved_stroke(stroke: StrokeStyle | None) -> ResolvedStrokeStyle:
    """The baked stroke; ``_apply_line_width`` has already pinned its width."""
    if stroke is None:
        raise ValueError(
            "marks stroke is None after cascade: an authored `stroke: null` is "
            "not valid on a line or area edge"
        )
    return ResolvedStrokeStyle(
        width=settled(stroke.width),
        color=stroke.color,
        cap=stroke.cap,
        join=stroke.join,
        dasharray=stroke.dasharray,
    )


def _build_resolved_line_mark(
    merged: LineMarkStyle, labels: ResolvedPointLabels
) -> ResolvedLineMarkStyle:
    """Build a ResolvedLineMarkStyle from an already-merged LineMarkStyle."""
    assert merged.halo_multiplier is not None
    assert merged.disconnected_width is not None
    return ResolvedLineMarkStyle(
        stroke=_resolved_stroke(merged.stroke),
        halo_multiplier=merged.halo_multiplier,
        curve=merged.curve,
        connect=merged.connect,
        disconnected_cap=merged.disconnected_cap,
        disconnected_width=merged.disconnected_width,
        labels=labels,
    )


def _build_resolved_area_mark(merged: AreaMarkStyle) -> ResolvedAreaMarkStyle:
    """Build a ResolvedAreaMarkStyle (fill only) from an already-merged AreaMarkStyle."""
    assert merged.opacity is not None
    assert merged.backdrop is not None
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
    assert merged.halo_multiplier is not None
    return ResolvedAreaLineStyle(
        stroke=_resolved_stroke(merged.stroke),
        halo_multiplier=merged.halo_multiplier,
        labels=labels,
    )
