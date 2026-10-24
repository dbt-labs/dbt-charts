"""Resolve a line, area, bar or scatter chart's authored annotations into render payloads.

Everything the engine would otherwise compute happens here: anchor literals are
read in the x column's type, aggregates and trends run over the plotted rows
(per panel, per series), clock anchors read the render time, text renders, and
the theme's annotation style is merged with the series color and the author's
overrides. The engine maps the values to pixels and draws.
"""

from __future__ import annotations

import calendar
import math
import statistics
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, TypeVar

import pydantic_core

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import resolve_format_parts
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.authored import MultiplesConfig
from dbt_charts.core.compile.models.chart.authored._annotation import (
    AggregateAnchor,
    Anchor,
    AnchorRange,
    AnchorScalar,
    Annotation,
    BandAnnotation,
    ClockAnchor,
    NoteAnnotation,
    RuleAnnotation,
    TrendAnnotation,
)
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedBarLayer,
    ResolvedLayer,
)
from dbt_charts.core.compile.models.chart.resolved._annotation import (
    ResolvedAnchor,
    ResolvedAnnotation,
    ResolvedAnnotationArea,
    ResolvedAnnotationBox,
    ResolvedAnnotationBoxStroke,
    ResolvedAnnotationCircle,
    ResolvedAnnotationFont,
    ResolvedAnnotationGap,
    ResolvedAnnotationLine,
    ResolvedAnnotationPosition,
    ResolvedAnnotationRect,
    ResolvedAnnotationRule,
    ResolvedAnnotationStroke,
    ResolvedAnnotationStyle,
    ResolvedAnnotationText,
    ResolvedCategoryAnchor,
    ResolvedLayerAxis,
    ResolvedRangeAnchor,
    ResolvedScalar,
    ResolvedTrend,
    ResolvedValueAnchor,
)
from dbt_charts.core.compile.models.primitives import ResolvedFormat
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedAxisStyle,
    ResolvedPointMarkStyle,
    ResolvedTickLabel,
)
from dbt_charts.core.compile.models.style.theme.annotations import (
    AnnotationPositionStyle,
    AnnotationStrokeStyle,
    AnnotationTextMarkStyle,
    BandAnnotationMarksStyle,
    BandAnnotationStyle,
    NoteAnnotationMarksStyle,
    NoteAnnotationThemeStyle,
    NoteLineMarkStyle,
    NoteRectMarkStyle,
    RuleAnnotationMarksStyle,
    RuleAnnotationStyle,
    TrendAnnotationMarksStyle,
    TrendAnnotationThemeStyle,
)
from dbt_charts.core.compile.models.style.theme.category_colors import (
    category_scale_for,
    series_colors,
)
from dbt_charts.core.compile.resolve.chart._annotation_stats import (
    FitError,
    TooFewRows,
    aggregate,
    fit,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import (
    ChartDataset,
    LayerDatasets,
    partition,
)
from dbt_charts.core.compile.resolve.chart._domain import layers_own_y_scale
from dbt_charts.core.compile.resolve.chart._layer_ink import LayerInk
from dbt_charts.core.compile.resolve.chart._palette import _with_color_tokens
from dbt_charts.core.compile.resolve.style.tokens import (
    SERIES_COLOR_TOKEN,
    substitute_tokens,
)
from dbt_charts.core.compile.template.annotation_text import (
    AnnotationTextError,
    AnnotationValue,
    render_annotation_text,
)
from dbt_charts.core.constants.marks import TREND_SAMPLES
from dbt_charts.core.diagnostics.codes_compile import ERR_ANNOTATION_INVALID
from dbt_charts.core.font_measure import (
    css_weight_to_axis,
    get_weighted_font_measurer,
)
from dbt_charts.core.text.numeral_scale import with_symbol
from dbt_charts.core.utils import coerce_numeric_cell, is_year_shaped

XKind = Literal["temporal", "quantitative", "nominal"]
YKind = Literal["quantitative", "nominal"]
Axis = Literal["x", "y"]
_T = TypeVar("_T")
# (x, y) query cells; the measured side is a float.
_Point = tuple[
    Any,  # type-state: explicit_any — a query cell
    Any,  # type-state: explicit_any — a query cell
]

_EPOCH = date(1970, 1, 1)

# An `auto` circle clears the annotated point by the text gap, at least this
# far out; an `auto` wrap measure is this many "n" widths (a typical prose glyph).
_TEXT_GAP = 4.0
_MIN_AUTO_RADIUS = 6.0
_AUTO_WRAP_CHARS = 28
# The area range, px squared, a scatter's size channel scales its marks over
# (`SIZE_SCALE_RANGE` in the engine's scatter family).
_SIZE_SCALE_RANGE = (4.0, 361.0)


def unfilled_ring(point_mark: ResolvedPointMarkStyle) -> float:
    """The extra radius an unfilled symbol's stroke adds; 0 for a filled one."""
    if point_mark.filled is not False:
        return 0.0
    return point_mark.stroke_width or 0.0  # type-state: silent_fallback — no stroke


def point_radius(
    size: float | None,
    ring: float = 0.0,
    size_channel: Sequence[
        Any  # type-state: explicit_any — a query cell
    ]
    | None = None,
) -> float | None:
    """The outer radius of a chart's drawn point marks; None when it draws none.

    ``size`` is a mark's area in px squared and ``ring`` the extra ink of an
    unfilled symbol's stroke. A ``size_channel`` (the size column's cells) scales
    the marks linearly over the area range from 0 to the largest cell; the
    biggest mark sets the radius.
    """
    if size_channel is not None:
        cells = [v for v in map(coerce_numeric_cell, size_channel) if v is not None]
        if not cells:
            return None
        low, high = min(0.0, *cells), max(0.0, *cells)
        unit = 0.5 if high == low else (max(cells) - low) / (high - low)
        size = _SIZE_SCALE_RANGE[0] + unit * (
            _SIZE_SCALE_RANGE[1] - _SIZE_SCALE_RANGE[0]
        )
    if size is None or size <= 0:
        return None
    return math.sqrt(size) / 2 + ring


# Every color slot a series color fills when the author did not set one; box
# fill and outlines are chrome, not series ink. A note's rect is its enclosing
# box, chrome too, so notes skip it.
# mark -> the sub-object carrying its ink; None means the mark's own `fill`.
_SERIES_INK = {
    "rule": "stroke",
    "rect": None,
    "line": "stroke",
    "area": None,
    "circle": "stroke",
    "text": "font",
}
_TypeDefaults = (
    RuleAnnotationStyle
    | BandAnnotationStyle
    | NoteAnnotationThemeStyle
    | TrendAnnotationThemeStyle
)
_Marks = (
    RuleAnnotationMarksStyle
    | BandAnnotationMarksStyle
    | NoteAnnotationMarksStyle
    | TrendAnnotationMarksStyle
)


@dataclass(frozen=True)
class LayerY:
    """The y column a `layers:` entry plots and where its rows come from."""

    index: int
    column: str
    # The layer's own query; None reads the chart's rows.
    query: str | None
    color_field: str | None
    ink: LayerInk


@dataclass(frozen=True)
class AnnotationChart:
    """The facts about a chart its annotations resolve against."""

    chart_id: str
    # The chart type: names the `ChartStyleContext` family whose `annotations`
    # default each annotation type for this chart.
    family: Literal["line", "area", "bar", "scatter"]
    x_field: str
    x_kind: XKind
    y_kind: YKind
    y_fields: tuple[str, ...]
    color_field: str | None
    stacked: bool
    layers: tuple[LayerY, ...]
    # Layers draw on their own y scales once any pins a side; else on the base's.
    own_y_scale: bool
    multiples: MultiplesConfig | None
    # Rules and trends over a filled area stay above it; elsewhere they sit under the data.
    is_area: bool
    # Ink of a chart with no color channel: what an uncolored trend wears.
    series_fill: str
    # The ink of each color value, where resolve baked it (a layer's); else
    # derived from the palette.
    series_inks: Mapping[str, str] | None
    # The color channel is a gradient (a layer's numeric or date color).
    gradient: bool
    # Bars drawn sideways: the x channel runs down the screen, y across it.
    horizontal: bool


def annotation_chart(
    chart: LineChart | AreaChart | BarChart | ScatterChart,
    x_kind: str,
    y_kind: str,
    stacked: bool,
    series_fill: str,
    horizontal: bool,
    inks: Sequence[LayerInk],
) -> AnnotationChart:
    """The facts a cartesian chart's annotations resolve against."""
    assert chart.x is not None and chart.y is not None, (
        "line, area, bar and scatter bind x and y"
    )
    assert chart.type != "histogram", "a histogram refuses annotations"
    assert x_kind in ("temporal", "quantitative", "nominal"), x_kind
    assert y_kind in ("quantitative", "nominal"), y_kind
    return AnnotationChart(
        chart_id=chart.id,
        family=chart.type,
        x_field=chart.x,
        x_kind=x_kind,
        y_kind=y_kind,
        y_fields=(chart.y,) if isinstance(chart.y, str) else tuple(chart.y),
        color_field=chart.color,
        stacked=stacked,
        layers=tuple(
            LayerY(
                index,
                layer.y,
                None if layer.query in (None, chart.query_name) else layer.query,
                layer.color,
                inks[index],
            )
            for index, layer in enumerate(chart.layers)
            if layer.y is not None
        ),
        own_y_scale=layers_own_y_scale(chart),
        multiples=chart.multiples,
        is_area=isinstance(chart, AreaChart),
        series_fill=series_fill,
        series_inks=None,
        gradient=False,
        horizontal=horizontal,
    )


@dataclass(frozen=True)
class _Draft:
    """A resolved annotation awaiting its text.

    The y axis's label format depends on the tick ladder, which depends on the
    annotation values, so text renders once the axis exists.
    """

    index: int
    y: ResolvedAnchor | None
    trend: ResolvedTrend | None
    y_axis: Literal["base"] | ResolvedLayerAxis
    # The layer whose column the annotation reads, if any; its marks set the radius.
    layer: int | None
    render: Callable[
        [ResolvedFormat | None, ResolvedFormat | None, float | None], ResolvedAnnotation
    ]


@dataclass(frozen=True)
class ResolvedAnnotations:
    """What a chart's annotations resolved to, before their text renders.

    ``y_values`` join the base y domain as data does; an annotation on a
    layer's own scale joins that layer's in the engine.
    """

    drafts: tuple[_Draft, ...]
    y_values: tuple[float, ...]
    gaps: tuple[ResolvedAnnotationGap, ...]

    def index_of_y(self, refused: Callable[[float], bool]) -> int | None:
        """The first authored annotation with a base-scale y value ``refused`` takes."""
        return next(
            (
                draft.index
                for draft in self.drafts
                if draft.y_axis == "base" and any(map(refused, _y_values([draft])))
            ),
            None,
        )

    def render(
        self,
        axis_y: ResolvedAxisStyle,
        axis_x: ResolvedAxisStyle,
        layers: Sequence[ResolvedLayer],
        point_radius: float | None,
    ) -> tuple[ResolvedAnnotation, ...]:
        """The annotations, their `{{ value | format }}` in the anchor axis's format.

        ``point_radius`` is the outer radius of the chart's drawn point marks, if any;
        an `auto` circle clears it.
        """
        y_format = _label_format(axis_y.labels.format, axis_y.tick_label)
        x_format = _label_format(axis_x.labels.format, axis_x.tick_label)
        layer_formats = [
            _label_format(
                None if layer.axis_y.labels is None else layer.axis_y.labels.format,
                layer.axis_y.tick_label,
            )
            for layer in layers
        ]
        return tuple(
            draft.render(
                y_format
                if draft.y_axis == "base"
                else layer_formats[draft.y_axis.layer],
                x_format,
                point_radius
                if draft.layer is None
                else _layer_point_radius(layers[draft.layer]),
            )
            for draft in self.drafts
        )


def _layer_point_radius(layer: ResolvedLayer) -> float | None:
    """The outer radius of a layer's drawn point marks; None when it draws none."""
    if isinstance(layer, ResolvedBarLayer):
        return None
    return point_radius(layer.point_mark.size, ring=unfilled_ring(layer.point_mark))


def _label_format(
    fmt: ResolvedFormat | None, tick_label: ResolvedTickLabel | None
) -> ResolvedFormat | None:
    if fmt is not None and tick_label is not None:
        return resolve_format_parts(
            fmt, None, spec=with_symbol(tick_label.format, tick_label.prefix)
        )
    return fmt


NO_ANNOTATIONS = ResolvedAnnotations(drafts=(), y_values=(), gaps=())


class _Invalid(ValueError):
    """An annotation that does not fit its chart; the message is the reason."""


class _NoFit(_Invalid):
    """A scope whose rows cannot carry a trend: that scope draws nothing."""


@dataclass(frozen=True)
class _Scope:
    """The plotted points one expansion computes over."""

    panel: str | None
    series: str | None
    points: tuple[_Point, ...]


def resolve_annotations(
    annotations: Sequence[Annotation],
    chart: AnnotationChart,
    dataset: ChartDataset,
    datasets: LayerDatasets,
    ctx: ChartStyleContext,
    rendered_at: datetime,
) -> ResolvedAnnotations:
    """Resolve each authored annotation, in authored order."""
    if not annotations:
        return NO_ANNOTATIONS
    drafts: list[_Draft] = []
    gaps: list[ResolvedAnnotationGap] = []
    for index, annotation in enumerate(annotations):
        empty: list[str] = []
        try:
            drafts.extend(
                _resolve_one(
                    index, annotation, chart, dataset, datasets, ctx, rendered_at, empty
                )
            )
        except (_Invalid, FitError) as exc:
            raise CompilationError.from_code(
                ERR_ANNOTATION_INVALID,
                chart_id=chart.chart_id,
                index=index,
                reason=str(exc),
            ) from exc
        if empty:
            gaps.append(ResolvedAnnotationGap(index=index, reason="; ".join(empty)))
    base = [draft for draft in drafts if draft.y_axis == "base"]
    return ResolvedAnnotations(
        drafts=tuple(drafts), y_values=tuple(_y_values(base)), gaps=tuple(gaps)
    )


def _resolve_one(
    index: int,
    annotation: Annotation,
    plotted: AnnotationChart,
    plotted_dataset: ChartDataset,
    datasets: LayerDatasets,
    ctx: ChartStyleContext,
    rendered_at: datetime,
    empty: list[str],
) -> Iterator[_Draft]:
    """Each draft of one annotation; a scope with nothing to compute over adds
    its reason to ``empty`` instead of failing the chart."""
    computed = _is_computed(annotation)
    target = _target(annotation, plotted, plotted_dataset, datasets, computed)
    chart, dataset = target.chart, target.dataset
    shared_ink = (
        annotation.y_column is not None
        and plotted.color_field is None
        and len(plotted.y_fields) > 1
    )
    if annotation.y_column is not None and shared_ink and not plotted.stacked:
        ink = series_colors(sorted(plotted.y_fields), ctx.palette, None)
        chart = replace(chart, series_fill=ink[annotation.y_column])
    # Stacked columns paint in baseline order, which resolve does not know yet.
    ink_unknown = shared_ink and plotted.stacked
    _check_series(annotation, chart, dataset, computed)
    colors = (
        chart.series_inks
        if chart.series_inks is not None
        else series_colors(
            _series_domain(chart, dataset),
            ctx.palette,
            category_scale_for(ctx.category_colors, chart.color_field),
        )
        if chart.color_field is not None and computed
        else {}
    )
    axes: tuple[tuple[Axis, str, str], ...] = (
        ("x", chart.x_field, chart.x_kind),
        ("y", chart.y_fields[0], chart.y_kind),
    )
    categories: dict[Axis, frozenset[str]] = {
        axis: frozenset(map(str, dataset.column_values(field)))
        for axis, field, kind in axes
        if kind == "nominal"
    }
    iterated = False
    for scope in _scopes(annotation, chart, dataset, computed):
        iterated = True
        if computed and not scope.points:
            empty.append(f"no plotted rows in {_describe(scope)}")
            continue
        series_ink = (
            None if ink_unknown else _series_ink(chart, scope.series, colors, ctx)
        )
        resolver = _AnchorResolver(chart, scope, rendered_at, categories)
        try:
            placements = list(_placements(annotation, chart, resolver, scope))
        except _NoFit as exc:
            empty.append(f"{exc} in {_describe(scope)}")
            continue
        if series_ink is None:
            for x, y, _, _, _ in placements:
                _style(annotation, scope.series, None, ctx, chart, x, y, None)
        for x, y, trend, value_axis, value in placements:
            yield _Draft(
                index=index,
                y=y,
                trend=trend,
                y_axis=target.y_axis,
                layer=target.layer,
                render=_renderer(
                    index,
                    annotation,
                    chart,
                    scope,
                    x,
                    y,
                    trend,
                    value,
                    value_axis,
                    series_ink,
                    ctx,
                    target.y_axis,
                ),
            )
    if computed and not iterated:
        empty.append("no plotted rows in the chart")


def _describe(scope: _Scope) -> str:
    where = [] if scope.series is None else [f"series {scope.series!r}"]
    if scope.panel is not None:
        where.append(f"panel {scope.panel}")
    return " of ".join(where) or "the chart"  # type-state: silent_fallback — unscoped


def _series_ink(
    chart: AnnotationChart,
    series: str | None,
    colors: Mapping[str, str],
    ctx: ChartStyleContext,
) -> str:
    """The color `theme.series` stands for: the scope's series, the chart's own
    ink when it has no color channel, else the secondary ink for several series
    combined."""
    if series is not None:
        return colors[series]
    return (
        ctx.cascaded.secondary_ink()
        if chart.color_field is not None
        else chart.series_fill
    )


def _default_plane(
    annotation: Annotation, chart: AnnotationChart
) -> Literal["background", "foreground"]:
    if isinstance(annotation, NoteAnnotation):
        return "foreground"
    if chart.is_area and annotation.type != "band":
        return "foreground"
    return "background"


def _renderer(
    index: int,
    annotation: Annotation,
    chart: AnnotationChart,
    scope: _Scope,
    x: ResolvedAnchor | None,
    y: ResolvedAnchor | None,
    trend: ResolvedTrend | None,
    value: AnnotationValue,
    value_axis: Axis,
    series_ink: str | None,
    ctx: ChartStyleContext,
    y_axis: Literal["base"] | ResolvedLayerAxis,
) -> Callable[
    [ResolvedFormat | None, ResolvedFormat | None, float | None], ResolvedAnnotation
]:
    def render(
        y_format: ResolvedFormat | None,
        x_format: ResolvedFormat | None,
        point_radius: float | None,
    ) -> ResolvedAnnotation:
        try:
            text = (
                None
                if annotation.text is None
                else render_annotation_text(
                    annotation.text,
                    value,
                    scope.series,
                    chart.color_field is not None,
                    y_format if value_axis == "y" else x_format,
                    ctx.formats,
                )
            )
        except AnnotationTextError as exc:
            raise CompilationError.from_code(
                ERR_ANNOTATION_INVALID,
                chart_id=chart.chart_id,
                index=index,
                reason=str(exc),
            ) from exc
        return ResolvedAnnotation(
            index=index,
            type=annotation.type,
            plane=annotation.plane or _default_plane(annotation, chart),
            panel=scope.panel,
            series=scope.series,
            y_axis=y_axis,
            x=x,
            y=y,
            text=text,
            connector=_connector(annotation),
            enclose=annotation.enclose
            if isinstance(annotation, NoteAnnotation)
            else None,
            trend=trend,
            style=_style(
                annotation, scope.series, series_ink, ctx, chart, x, y, point_radius
            ),
        )

    return render


def _is_computed(annotation: Annotation) -> bool:
    if isinstance(annotation, TrendAnnotation):
        return True
    anchors: list[object] = [  # type-state: object_annotation — any anchor
        annotation.x,
        annotation.y,
    ]
    for anchor in list(anchors):
        if isinstance(anchor, AnchorRange):
            anchors.extend((anchor.from_, anchor.to))
    return any(isinstance(a, AggregateAnchor) for a in anchors)


def _derived_axis(annotation: Annotation) -> Axis | None:
    """The axis a note's `min()`/`max()` sits on when the other is omitted; the
    model admits no other note with one coordinate."""
    if not isinstance(annotation, NoteAnnotation) or (
        (annotation.x is None) == (annotation.y is None)
    ):
        return None
    return "x" if annotation.x is not None else "y"


def _is_frame_note(annotation: Annotation) -> bool:
    return (
        isinstance(annotation, NoteAnnotation)
        and annotation.x is None
        and annotation.y is None
    )


def _computes_on_y(annotation: Annotation) -> bool:
    if isinstance(annotation, TrendAnnotation) or _derived_axis(annotation):
        return True
    y = annotation.y
    bounds = (y.from_, y.to) if isinstance(y, AnchorRange) else (y,)
    return any(isinstance(b, AggregateAnchor) for b in bounds)


@dataclass(frozen=True)
class _Target:
    """What an annotation reads: its y column's scale, series and rows."""

    chart: AnnotationChart
    dataset: ChartDataset
    y_axis: Literal["base"] | ResolvedLayerAxis
    layer: int | None


@dataclass(frozen=True)
class _Plotted:
    column: str
    place: str
    layer: LayerY | None


def _plotted_columns(chart: AnnotationChart) -> list[_Plotted]:
    return [
        *(_Plotted(column, "y", None) for column in chart.y_fields),
        *(
            _Plotted(layer.column, f"layers[{layer.index}].y", layer)
            for layer in chart.layers
        ),
    ]


def _listed(plotted: Sequence[_Plotted]) -> str:
    return ", ".join(f"{p.column} ({p.place})" for p in plotted)


def _target(
    annotation: Annotation,
    chart: AnnotationChart,
    dataset: ChartDataset,
    datasets: LayerDatasets,
    computed: bool,
) -> _Target:
    """The y column the annotation names: which scale it reads, which rows it
    computes over, and which color channel splits them."""
    name = annotation.y_column
    plotted = _plotted_columns(chart)
    if name is None:
        if _computes_on_y(annotation) and len(plotted) > 1:
            raise _Invalid(
                "the chart plots several y columns, so a computed y anchor, trend "
                f"or derived note is ambiguous; set y_column to one of: {_listed(plotted)}"
            )
        return _Target(chart, dataset, "base", None)
    if not _reads_y(annotation):
        raise _Invalid(
            f"y_column {name!r} has no effect: this {annotation.type} has no y anchor"
        )
    matches = [p for p in plotted if p.column == name]
    if not matches:
        raise _Invalid(
            f"y_column {name!r} is not a plotted y column; plotted: {_listed(plotted)}"
        )
    first = matches[0]
    for other in matches[1:]:
        if _reads(chart, other) != _reads(chart, first):
            raise _Invalid(
                f"y_column {name!r} is plotted by both {first.place} and "
                f"{other.place}, so it is ambiguous; rename one in its query"
            )
    scoped = replace(chart, y_fields=(name,))
    layer = first.layer
    if layer is None:
        return _Target(scoped, dataset, "base", None)
    scoped = replace(
        scoped,
        y_kind="quantitative",
        color_field=layer.color_field,
        stacked=False,
        series_fill=scoped.series_fill if layer.ink.ink is None else layer.ink.ink,
        series_inks=layer.ink.series,
        gradient=layer.ink.gradient,
    )
    return _Target(
        scoped,
        _layer_dataset(chart, layer, dataset, datasets) if computed else dataset,
        ResolvedLayerAxis(layer=layer.index) if chart.own_y_scale else "base",
        layer.index,
    )


def _reads(
    chart: AnnotationChart, plotted: _Plotted
) -> tuple[int | str, str | None, str | None]:
    """What a plotted column reads: two with the same key are one column."""
    layer = plotted.layer
    if layer is None:
        return ("base", None, chart.color_field)
    axis = layer.index if chart.own_y_scale else "base"
    return (axis, layer.query, layer.color_field)


def _layer_dataset(
    chart: AnnotationChart,
    layer: LayerY,
    dataset: ChartDataset,
    datasets: LayerDatasets,
) -> ChartDataset:
    if layer.query is None:
        return dataset
    if datasets is ... or layer.query not in datasets:
        raise _Invalid(
            f"layers[{layer.index}] reads query {layer.query!r}, whose rows are "
            "not available to compute over"
        )
    return partition(chart.multiples, datasets[layer.query])


def _reads_y(annotation: Annotation) -> bool:
    return (
        isinstance(annotation, TrendAnnotation)
        or annotation.y is not None
        or _is_computed(annotation)
    )


def _series_domain(chart: AnnotationChart, dataset: ChartDataset) -> list[str]:
    assert chart.color_field is not None
    return sorted(
        {str(v) for v in dataset.column_values(chart.color_field) if v is not None}
    )


def _check_series(
    annotation: Annotation,
    chart: AnnotationChart,
    dataset: ChartDataset,
    computed: bool,
) -> None:
    series = annotation.series
    if not computed:
        if series is not None:
            raise _Invalid(
                "series applies only to computed anchors and trends; "
                "this annotation's anchors are literal"
            )
        return
    if chart.color_field is None:
        if series is not None:
            raise _Invalid("series needs a chart with color")
        return
    if chart.gradient:
        if series != "total":
            raise _Invalid(
                f"the layer's color {chart.color_field!r} is a gradient, so it has "
                "no series to split by; set series to total"
            )
        return
    if series is None:
        raise _Invalid(
            f"the chart colors by {chart.color_field!r}; set series to each, "
            "total or a list of series"
        )
    if chart.stacked and series != "total":
        raise _Invalid("a stacked chart takes only series: total")
    if isinstance(series, list):
        known = set(_series_domain(chart, dataset))
        unknown = [name for name in series if name not in known]
        if unknown:
            raise _Invalid(
                f"series {', '.join(map(repr, unknown))} not in the chart's "
                f"{chart.color_field!r} values"
            )


def _scopes(
    annotation: Annotation,
    chart: AnnotationChart,
    dataset: ChartDataset,
    computed: bool,
) -> Iterator[_Scope]:
    if not computed:
        yield _Scope(panel=None, series=None, points=())
        return
    series = annotation.series
    names: list[str | None]
    if chart.color_field is None or series in (None, "total"):
        names = [None]
    elif series == "each":
        names = list(_series_domain(chart, dataset))
    else:
        assert isinstance(series, list)
        names = list(series)
    for panel in dataset.panels:
        key = pydantic_core.to_json(list(panel.key)).decode() if dataset.axes else None
        for name in names:
            points = _points(chart, panel.rows, name)
            # A panel draws only the series it has rows for.
            if series == "each" and not points:
                continue
            yield _Scope(panel=key, series=name, points=points)


def _points(
    chart: AnnotationChart,
    rows: Sequence[dict[str, Any]],  # type-state: explicit_any — query rows
    series: str | None,
) -> tuple[_Point, ...]:
    """The (x, y) points a scope computes over. A stacked chart's total is the
    per-x sum across series; any other total pools every series' rows. A dot
    plot (a category y) never stacks, so it computes over every plotted row,
    its x as plotted."""
    y_field = chart.y_fields[0]
    if chart.y_kind == "nominal":
        return tuple(
            (x, row[y_field])
            for row in rows
            if row.get(y_field) is not None
            and (x := _plotted_x(chart, row.get(chart.x_field))) is not None
            and _in_series(chart, row, series)
        )
    # A non-finite y is not plotted, so it is not computed over either.
    selected = [
        (x, y)
        for row in rows
        if (x := _plotted_x(chart, row.get(chart.x_field))) is not None
        and (y := coerce_numeric_cell(row.get(y_field))) is not None
        and _in_series(chart, row, series)
    ]
    if chart.color_field is None or not chart.stacked or series is not None:
        return tuple(selected)
    totals: dict[Any, float] = {}  # type-state: explicit_any — query cells
    for x, y in selected:
        total = totals.get(x, 0.0)  # type-state: silent_fallback — a sum starts at 0
        totals[x] = total + y
    return tuple(totals.items())


def _in_series(
    chart: AnnotationChart,
    row: dict[str, Any],  # type-state: explicit_any — a query row
    series: str | None,
) -> bool:
    return series is None or (
        chart.color_field is not None and str(row[chart.color_field]) == series
    )


def _plotted_x(
    chart: AnnotationChart,
    cell: Any,  # type-state: explicit_any — a query cell
) -> Any:  # type-state: explicit_any — a query cell
    """An x cell as plotted: a date or category as is, a number only if finite;
    else None."""
    if cell is None or chart.x_kind in ("temporal", "nominal"):
        return cell
    return coerce_numeric_cell(cell)


@dataclass(frozen=True)
class _AnchorResolver:
    chart: AnnotationChart
    scope: _Scope
    rendered_at: datetime
    categories: dict[Axis, frozenset[str]]

    def value(self, axis: Axis, anchor: AnchorScalar) -> ResolvedScalar:
        """One value on a continuous ``axis``."""
        if isinstance(anchor, ClockAnchor):
            self._clock_axis(axis, anchor)
            if anchor.fn == "current":
                raise _Invalid(
                    f"current({anchor.unit}) is a period; use it on a band or as "
                    "a range bound"
                )
            return _iso_datetime(self.rendered_at)
        if isinstance(anchor, AggregateAnchor):
            return self._aggregate(axis, anchor)
        return self._literal(axis, anchor)

    def bound(
        self, axis: Axis, anchor: AnchorScalar | None, end: Literal["from", "to"]
    ) -> ResolvedScalar | None:
        """A range bound; a current() period gives its start or its end."""
        if anchor is None:
            return None
        if isinstance(anchor, ClockAnchor) and anchor.fn == "current":
            self._clock_axis(axis, anchor)
            start, stop = _period(anchor, self.rendered_at)
            return (start if end == "from" else stop).isoformat()
        if _is_category(self.chart, axis):
            raise _Invalid(f"a range on a category {axis}; name one category instead")
        return self.value(axis, anchor)

    def point(
        self, axis: Axis, anchor: AnchorScalar
    ) -> ResolvedValueAnchor | ResolvedCategoryAnchor:
        """A rule's or note's position: a category's slot center, else a value."""
        if not _is_category(self.chart, axis):
            return ResolvedValueAnchor(kind="value", value=self.value(axis, anchor))
        if isinstance(anchor, AggregateAnchor | ClockAnchor):
            raise _Invalid(f"{anchor.fn}() on a category {axis}")
        return ResolvedCategoryAnchor(
            kind="category", value=self.category(axis, anchor)
        )

    def category(self, axis: Axis, anchor: AnchorScalar) -> str:
        if not isinstance(anchor, str) or anchor not in self.categories[axis]:
            raise _Invalid(
                f"{anchor!r} is not a category of the {axis} column; categories: "
                + ", ".join(sorted(self.categories[axis]))
            )
        return anchor

    def _clock_axis(self, axis: Axis, anchor: ClockAnchor) -> None:
        if axis != "x" or self.chart.x_kind != "temporal":
            raise _Invalid(f"{anchor.fn}() needs a date or timestamp x")

    def _aggregate(self, axis: Axis, anchor: AggregateAnchor) -> ResolvedScalar:
        name = anchor.fn if anchor.fn != "percentile" else "percentile()"
        if axis == "y":
            return aggregate(
                anchor.fn, anchor.percent, [y for _, y in self.scope.points]
            )
        match self.chart.x_kind:
            case "nominal":
                raise AssertionError("a category x resolves through point()")
            case "temporal":
                if anchor.fn not in ("min", "max"):
                    raise _Invalid(f"{name}() on a date x; only min() and max()")
                days = [_x_days(x) for x, _ in self.scope.points]
                return _iso_days(aggregate(anchor.fn, None, days))
            case "quantitative":
                return aggregate(
                    anchor.fn,
                    anchor.percent,
                    [float(x) for x, _ in self.scope.points],
                )

    def _literal(
        self,
        axis: Axis,
        anchor: object,  # type-state: object_annotation — an authored scalar
    ) -> ResolvedScalar:
        if axis == "y":
            if isinstance(anchor, bool) or not isinstance(anchor, int | float):
                raise _Invalid(f"y {anchor!r} is not a number")
            return float(anchor)
        match self.chart.x_kind:
            case "temporal":
                return _iso_value(anchor)
            case "quantitative":
                if isinstance(anchor, bool) or not isinstance(anchor, int | float):
                    raise _Invalid(f"x {anchor!r} is not a number")
                return float(anchor)
            case "nominal":
                raise AssertionError("a category x resolves through point()")


def _anchors(
    annotation: Annotation,
    chart: AnnotationChart,
    resolver: _AnchorResolver,
    scope: _Scope,
) -> tuple[ResolvedAnchor | None, ResolvedAnchor | None, ResolvedTrend | None]:
    match annotation:
        case RuleAnnotation():
            x = _anchor(resolver, "x", annotation.x)
            y = _anchor(resolver, "y", annotation.y)
            if isinstance(x, ResolvedRangeAnchor | None) and isinstance(
                y, ResolvedRangeAnchor | None
            ):
                raise _Invalid(
                    "a rule needs a single value on x or y; a range only limits "
                    "how far it runs"
                )
            if not isinstance(x, ResolvedRangeAnchor | None) and not isinstance(
                y, ResolvedRangeAnchor | None
            ):
                raise _Invalid(
                    "a rule takes a value on one axis; use a note for a point"
                )
            return x, y, None
        case BandAnnotation():
            return (
                _band_anchor(resolver, chart, "x", annotation.x),
                _band_anchor(resolver, chart, "y", annotation.y),
                None,
            )
        case NoteAnnotation():
            if annotation.x is None or annotation.y is None:
                assert _derived_axis(annotation) is None, "derived notes expand first"
                return None, None, None
            return (
                resolver.point("x", annotation.x),
                resolver.point("y", annotation.y),
                None,
            )
        case TrendAnnotation():
            x = None if annotation.x is None else _range(resolver, "x", annotation.x)
            return x, None, _trend(annotation, chart, scope, x)


_Placement = tuple[
    ResolvedAnchor | None,
    ResolvedAnchor | None,
    ResolvedTrend | None,
    Axis,
    ResolvedScalar | None,
]


def _placements(
    annotation: Annotation,
    chart: AnnotationChart,
    resolver: _AnchorResolver,
    scope: _Scope,
) -> Iterator[_Placement]:
    """Each place the annotation draws in this scope, with the axis and value
    `{{ value }}` prints."""
    derived = _derived_axis(annotation)
    if derived is None:
        x, y, trend = _anchors(annotation, chart, resolver, scope)
        yield (
            x,
            y,
            trend,
            "y" if isinstance(y, ResolvedValueAnchor) else "x",
            (_single_value(x, y)),
        )
        return
    assert isinstance(annotation, NoteAnnotation)
    anchor = annotation.x if derived == "x" else annotation.y
    assert isinstance(anchor, AggregateAnchor) and anchor.fn in ("min", "max")
    for cell_x, cell_y in _extreme_rows(chart, scope, derived, anchor.fn):
        x = _cell_anchor(resolver, "x", cell_x)
        y = _cell_anchor(resolver, "y", cell_y)
        yield x, y, None, derived, (x if derived == "x" else y).value


def _extreme_rows(
    chart: AnnotationChart, scope: _Scope, axis: Axis, fn: Literal["min", "max"]
) -> list[_Point]:
    """The distinct plotted points holding ``axis``'s extreme, ties included."""
    if _is_category(chart, axis):
        raise _Invalid(f"{fn}() on a category {axis}")
    temporal = axis == "x" and chart.x_kind == "temporal"
    keys = [
        p[1] if axis == "y" else _x_days(p[0]) if temporal else float(p[0])
        for p in scope.points
    ]
    extreme = aggregate(fn, None, keys)
    return list(
        dict.fromkeys(
            p for p, key in zip(scope.points, keys, strict=True) if key == extreme
        )
    )


def _cell_anchor(
    resolver: _AnchorResolver,
    axis: Axis,
    cell: Any,  # type-state: explicit_any — a query cell
) -> ResolvedValueAnchor | ResolvedCategoryAnchor:
    """A plotted cell as a note's position: a category's slot, else its value."""
    chart = resolver.chart
    if _is_category(chart, axis):
        return ResolvedCategoryAnchor(
            kind="category", value=resolver.category(axis, str(cell))
        )
    if axis == "x" and chart.x_kind == "temporal":
        return ResolvedValueAnchor(kind="value", value=_iso_value(cell))
    return ResolvedValueAnchor(kind="value", value=float(cell))


def _anchor(
    resolver: _AnchorResolver, axis: Axis, anchor: Anchor | None
) -> ResolvedAnchor | None:
    if anchor is None:
        return None
    if isinstance(anchor, AnchorRange):
        return _range(resolver, axis, anchor)
    return resolver.point(axis, anchor)


def _band_anchor(
    resolver: _AnchorResolver, chart: AnnotationChart, axis: Axis, anchor: Anchor | None
) -> ResolvedAnchor | None:
    if anchor is None:
        return None
    if isinstance(anchor, AnchorRange):
        return _range(resolver, axis, anchor)
    if isinstance(anchor, ClockAnchor) and anchor.fn == "current":
        return _range(resolver, axis, AnchorRange(**{"from": anchor, "to": anchor}))
    if _is_category(chart, axis):
        return ResolvedCategoryAnchor(
            kind="category", value=resolver.category(axis, anchor)
        )
    raise _Invalid(
        f"a band on {axis} takes a range (from, to)"
        + (" or current(<unit>)" if axis == "x" and chart.x_kind == "temporal" else "")
    )


def _is_category(chart: AnnotationChart, axis: Axis) -> bool:
    kind = chart.x_kind if axis == "x" else chart.y_kind
    return kind == "nominal"


def _range(
    resolver: _AnchorResolver, axis: Axis, anchor: AnchorRange
) -> ResolvedRangeAnchor:
    low = resolver.bound(axis, anchor.from_, "from")
    high = resolver.bound(axis, anchor.to, "to")
    if low is not None and high is not None and _order(low) > _order(high):
        raise _Invalid(f"{axis} range from {low!r} is past to {high!r}")
    return ResolvedRangeAnchor(kind="range", **{"from": low, "to": high})


def _single_value(
    x: ResolvedAnchor | None, y: ResolvedAnchor | None
) -> ResolvedScalar | None:
    """The one value `{{ value }}` prints: y's, else x's."""
    for anchor in (y, x):
        if isinstance(anchor, ResolvedValueAnchor | ResolvedCategoryAnchor):
            return anchor.value
    return None


def _connector(annotation: Annotation) -> Literal["none", "line", "arrow"]:
    if not isinstance(annotation, NoteAnnotation) or annotation.connector is None:
        return "none"
    return annotation.connector


def _trend(
    annotation: TrendAnnotation,
    chart: AnnotationChart,
    scope: _Scope,
    span: ResolvedRangeAnchor | None,
) -> ResolvedTrend:
    if chart.x_kind == "nominal":
        raise _Invalid("a trend on a category x; fits need a number or date x")
    if chart.y_kind == "nominal":
        raise _Invalid("a trend on a category y; fits need a number y")
    temporal = chart.x_kind == "temporal"
    xs = [_x_days(x) if temporal else float(x) for x, _ in scope.points]
    ys = [y for _, y in scope.points]
    if len(set(xs)) < 2:
        raise _NoFit("fewer than two distinct plotted x values to fit")
    try:
        curve = fit(annotation.fit.kind, annotation.fit.order, xs, ys)
    except TooFewRows as exc:
        raise _NoFit(str(exc)) from exc
    distinct = sorted(set(xs))
    # Date bars draw a trend across whole bands: the fit is evaluated half a
    # band past the first and last bar (the neighboring gap). A range gets two
    # median gaps of slack each side for the engine to cut at its bands' edges.
    band = chart.family == "bar" and temporal
    gaps = [b - a for a, b in zip(distinct, distinct[1:], strict=False)]
    reach_low, reach_high = 0.0, 0.0
    if band and span is None:
        reach_low, reach_high = gaps[0] / 2, gaps[-1] / 2
    elif band:
        reach_low = reach_high = 2 * statistics.median(gaps)
    low = (
        min(xs) if span is None or span.from_ is None else _order(span.from_)
    ) - reach_low
    high = (
        max(xs) if span is None or span.to is None else _order(span.to)
    ) + reach_high
    if span is None and len(distinct) < TREND_SAMPLES:
        samples = [low, *distinct, high] if band else distinct
    else:
        samples = [
            low + (high - low) * i / (TREND_SAMPLES - 1) for i in range(TREND_SAMPLES)
        ]
    out = [_iso_days(x) if temporal else x for x in samples]
    try:
        line = tuple((o, curve.predict(x)) for o, x in zip(out, samples, strict=True))
        interval = (
            None
            if annotation.interval is None
            else tuple(
                (
                    o,
                    *curve.interval(
                        x, annotation.interval.kind, annotation.interval.percent
                    ),
                )
                for o, x in zip(out, samples, strict=True)
            )
        )
    except TooFewRows as exc:
        raise _NoFit(str(exc)) from exc
    except FitError:
        raise
    except (ValueError, OverflowError) as exc:
        raise _Invalid(f"the {annotation.fit.kind} fit cannot reach x {exc}") from exc
    if any(not math.isfinite(y) for _, y in line):
        raise _Invalid(f"the {annotation.fit.kind} fit overflows over this x range")
    return ResolvedTrend(line=line, interval=interval)


def _period(anchor: ClockAnchor, now: datetime) -> tuple[date, date]:
    """The calendar period holding ``now``: [start, next start)."""
    today = now.astimezone(UTC).date()
    match anchor.unit:
        case "day":
            return today, today + timedelta(days=1)
        case "week":
            start = today - timedelta(days=today.weekday())
            return start, start + timedelta(days=7)
        case "month":
            start = today.replace(day=1)
            return start, start + timedelta(
                days=calendar.monthrange(today.year, today.month)[1]
            )
        case "quarter":
            first = 3 * ((today.month - 1) // 3) + 1
            start = today.replace(month=first, day=1)
            return start, (
                date(today.year + 1, 1, 1)
                if first == 10
                else start.replace(month=first + 3)
            )
        case "year":
            return date(today.year, 1, 1), date(today.year + 1, 1, 1)
        case None:
            raise AssertionError("the parser requires current()'s unit")


def _x_days(
    value: object,  # type-state: object_annotation — a cell or literal
) -> float:
    """A temporal x value as days since the epoch, UTC."""
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.timestamp() / 86400
    if isinstance(value, date):
        return float((value - _EPOCH).days)
    if (year := _year_start(value)) is not None:
        return float((year - _EPOCH).days)
    if isinstance(value, str):
        return _x_days(_parse_date(value))
    raise _Invalid(f"{value!r} is not a date")


def _year_start(
    value: object,  # type-state: object_annotation — a cell or literal
) -> date | None:
    """January 1 of a year-shaped int, integral Decimal or four-digit string,
    the same shapes the channel reads as temporal; else None."""
    if isinstance(value, int | Decimal | str) and is_year_shaped([value]):
        return date(int(value), 1, 1)
    return None


def _parse_date(text: str) -> date | datetime:
    try:
        return (
            date.fromisoformat(text)
            if len(text) <= 10
            else datetime.fromisoformat(text)
        )
    except ValueError:
        shape = (
            "timestamp (YYYY-MM-DDTHH:MM[:SS])"
            if len(text) > 10
            else "date (YYYY-MM-DD)"
        )
        raise _Invalid(f"{text!r} is not a {shape}") from None


def _iso_value(
    value: object,  # type-state: object_annotation — a cell or literal
) -> str:
    """A temporal literal as the ISO text the engine reads."""
    if isinstance(value, datetime):
        return _iso_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if (year := _year_start(value)) is not None:
        return year.isoformat()
    if isinstance(value, str):
        return _iso_value(_parse_date(value))
    raise _Invalid(f"x {value!r} is not a date")


def _iso_datetime(value: datetime) -> str:
    moment = value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
    return moment.isoformat(timespec="seconds")


def _iso_days(days: float) -> str:
    whole = math.floor(days)
    if days == whole:
        return (_EPOCH + timedelta(days=whole)).isoformat()
    return _iso_datetime(datetime(1970, 1, 1) + timedelta(seconds=round(days * 86400)))


def _order(value: ResolvedScalar) -> float:
    return _x_days(value) if isinstance(value, str) else value


def _y_values(drafts: Sequence[_Draft]) -> Iterator[float]:
    for item in drafts:
        y = item.y
        if isinstance(y, ResolvedValueAnchor) and isinstance(y.value, float):
            yield y.value
        elif isinstance(y, ResolvedRangeAnchor):
            yield from (b for b in (y.from_, y.to) if isinstance(b, float))
        if item.trend is not None:
            yield from (y for _, y in item.trend.line)
            if item.trend.interval is not None:
                for _, low, high in item.trend.interval:
                    yield from (low, high)


def _style(
    annotation: Annotation,
    series: str | None,
    series_ink: str | None,
    ctx: ChartStyleContext,
    chart: AnnotationChart,
    x: ResolvedAnchor | None,
    y: ResolvedAnchor | None,
    point_radius: float | None,
) -> ResolvedAnnotationStyle:
    theme = _type_defaults(annotation, chart, ctx)
    marks: _Marks = theme.marks
    if isinstance(theme, NoteAnnotationThemeStyle) and _is_frame_note(annotation):
        marks = marks.model_copy(
            update={
                "text": marks.text.model_copy(
                    update={"position": theme.frame.text.position}
                )
            }
        )
    # A trend's color is the theme's to say (`theme.series` by default); a rule,
    # band or note wears its series only when scoped to one.
    if (
        series is not None
        and series_ink is not None
        and not isinstance(annotation, TrendAnnotation)
    ):
        marks = _with_series_color(
            marks, series_ink, isinstance(annotation, NoteAnnotation)
        )
    if annotation.style is not None:
        marks = merge_onto_base(marks, _with_color_tokens(annotation.style.marks, ctx))
    if isinstance(marks, BandAnnotationMarksStyle):
        marks = _auto_band_position(marks, _band_edge(chart.horizontal, x, y))
    if isinstance(marks, TrendAnnotationMarksStyle):
        marks = _label_follows_line(marks)
    if series_ink is None:
        # A trend without an interval draws no area, so its fill is not ink.
        undrawn = (
            {"area"}
            if isinstance(annotation, TrendAnnotation) and annotation.interval is None
            else set()
        )
        if SERIES_COLOR_TOKEN in marks.model_dump_json(exclude=undrawn):
            raise _Invalid(
                "the series color of a y_column on a stacked chart with several y "
                "columns is not known yet; set an explicit color in the "
                "annotation's style"
            )
    tokens = {} if series_ink is None else {SERIES_COLOR_TOKEN: series_ink}
    return _resolved_style(
        substitute_tokens(marks, tokens),
        *_wrap(annotation, marks, theme),
        point_radius,
    )


def _type_defaults(
    annotation: Annotation, chart: AnnotationChart, ctx: ChartStyleContext
) -> _TypeDefaults:
    """The annotation type's defaults: theme and board, then the chart's own."""
    defaults = getattr(ctx.cascaded.annotations, annotation.type)
    chart_defaults = getattr(ctx, chart.family).annotations
    if chart_defaults is None:
        return defaults
    return merge_onto_base(defaults, getattr(chart_defaults, annotation.type))


def _band_edge(
    horizontal: bool, x: ResolvedAnchor | None, y: ResolvedAnchor | None
) -> Literal["top", "right"]:
    """A band's label edge: top on a stripe spanning the screen's x, else right.

    A band spanning both axes reads as an x band.
    """
    spans_screen_x = (x is not None and not horizontal) or (
        y is not None and horizontal
    )
    spans_screen_y = (y is not None and not horizontal) or (
        x is not None and horizontal
    )
    return "right" if spans_screen_y and not spans_screen_x else "top"


def _auto_band_position(
    marks: BandAnnotationMarksStyle, edge: Literal["top", "right"]
) -> BandAnnotationMarksStyle:
    position = marks.text.position
    resolved = position.model_copy(
        update={
            "edge": edge if position.edge == "auto" else position.edge,
            "align": "center" if position.align == "auto" else position.align,
            "overlay": True if position.overlay == "auto" else position.overlay,
        }
    )
    return marks.model_copy(
        update={"text": marks.text.model_copy(update={"position": resolved})}
    )


def _label_follows_line(
    marks: TrendAnnotationMarksStyle,
) -> TrendAnnotationMarksStyle:
    """A trend's label wears its line's color unless the author set the label's."""
    line_color = marks.line.stroke.color
    if marks.text.font.color != SERIES_COLOR_TOKEN or line_color is None:
        return marks
    font = marks.text.font.model_copy(update={"color": line_color})
    return marks.model_copy(
        update={"text": marks.text.model_copy(update={"font": font})}
    )


def _with_series_color(marks: _Marks, color: str, note: bool) -> _Marks:
    update: dict[str, object] = {}  # type-state: object_annotation — mixed marks
    for mark_name in type(marks).model_fields:
        holder = _SERIES_INK[mark_name]
        if note and mark_name == "rect":
            continue
        mark = getattr(marks, mark_name)
        if holder is None:
            update[mark_name] = mark.model_copy(update={"fill": color})
            continue
        inner = getattr(mark, holder)
        if inner is not None:
            update[mark_name] = mark.model_copy(
                update={holder: inner.model_copy(update={"color": color})}
            )
    return marks.model_copy(update=update)


def _set(value: _T | None, what: str) -> _T:
    if value is None:
        raise _Invalid(f"style {what} is unset")
    return value


def _stroke(
    stroke: AnnotationStrokeStyle | None, what: str
) -> ResolvedAnnotationStroke:
    stroke = _set(stroke, what)
    width = _set(stroke.width, f"{what}.width")
    return ResolvedAnnotationStroke(
        color=_set(stroke.color, f"{what}.color"),
        width=width,
        dasharray=_auto_dash(width) if stroke.dasharray == "auto" else stroke.dasharray,
    )


def _auto_dash(width: float) -> str:
    """Dash 3 widths, gap 2.5 widths, each rounded half up to at least 1 px."""
    return " ".join(str(max(1, math.floor(k * width + 0.5))) for k in (3, 2.5))


def _wrap(
    annotation: Annotation, marks: _Marks, theme: _TypeDefaults
) -> tuple[float | Literal["auto"] | None, float | None]:
    """The wrap a text takes: a frame note's measure, or a trend's measure and share."""
    if isinstance(marks, TrendAnnotationMarksStyle) and isinstance(
        theme, TrendAnnotationThemeStyle
    ):
        return marks.text.max_width, theme.label.max_share
    if isinstance(marks, NoteAnnotationMarksStyle) and _is_frame_note(annotation):
        return marks.text.max_width, None
    return None, None


def _resolved_style(
    marks: _Marks,
    max_width: float | Literal["auto"] | None,
    max_share: float | None,
    point_radius: float | None,
) -> ResolvedAnnotationStyle:
    text = _resolved_text(marks.text, max_width, max_share)
    match marks:
        case RuleAnnotationMarksStyle():
            return ResolvedAnnotationStyle(rule=_resolved_rule(marks), text=text)
        case BandAnnotationMarksStyle():
            return ResolvedAnnotationStyle(rect=_resolved_rect(marks), text=text)
        case NoteAnnotationMarksStyle():
            return ResolvedAnnotationStyle(
                rect=_resolved_rect(marks),
                line=_resolved_line(marks),
                circle=_resolved_circle(marks, point_radius),
                text=text,
            )
        case TrendAnnotationMarksStyle():
            return ResolvedAnnotationStyle(
                line=_resolved_line(marks), area=_resolved_area(marks), text=text
            )


def _resolved_rule(marks: RuleAnnotationMarksStyle) -> ResolvedAnnotationRule:
    return ResolvedAnnotationRule(
        opacity=_set(marks.rule.opacity, "marks.rule.opacity"),
        stroke=_stroke(marks.rule.stroke, "marks.rule.stroke"),
    )


def _resolved_rect(
    marks: BandAnnotationMarksStyle | NoteAnnotationMarksStyle,
) -> ResolvedAnnotationRect:
    rect = marks.rect
    return ResolvedAnnotationRect(
        opacity=_set(rect.opacity, "marks.rect.opacity"),
        fill=_set(rect.fill, "marks.rect.fill"),
        stroke=None
        if rect.stroke is None
        else _stroke(rect.stroke, "marks.rect.stroke"),
        padding=_set(rect.padding, "marks.rect.padding")
        if isinstance(rect, NoteRectMarkStyle)
        else None,
    )


def _resolved_line(
    marks: NoteAnnotationMarksStyle | TrendAnnotationMarksStyle,
) -> ResolvedAnnotationLine:
    return ResolvedAnnotationLine(
        opacity=_set(marks.line.opacity, "marks.line.opacity"),
        curve=_set(marks.line.curve, "marks.line.curve"),
        stroke=_stroke(marks.line.stroke, "marks.line.stroke"),
        length=_set(marks.line.length, "marks.line.length")
        if isinstance(marks.line, NoteLineMarkStyle)
        else None,
    )


def _resolved_area(marks: TrendAnnotationMarksStyle) -> ResolvedAnnotationArea:
    return ResolvedAnnotationArea(
        opacity=_set(marks.area.opacity, "marks.area.opacity"),
        fill=_set(marks.area.fill, "marks.area.fill"),
    )


def _resolved_circle(
    marks: NoteAnnotationMarksStyle, point_radius: float | None
) -> ResolvedAnnotationCircle:
    authored = _set(marks.circle.radius, "marks.circle.radius")
    dot = point_radius or 0.0  # type-state: silent_fallback — a chart with no points
    return ResolvedAnnotationCircle(
        opacity=_set(marks.circle.opacity, "marks.circle.opacity"),
        point_radius=dot,
        radius=max(dot + _TEXT_GAP, _MIN_AUTO_RADIUS)
        if isinstance(authored, str)
        else authored,
        stroke=_stroke(marks.circle.stroke, "marks.circle.stroke"),
    )


def _resolved_text(
    text: AnnotationTextMarkStyle,
    max_width: float | Literal["auto"] | None,
    max_share: float | None,
) -> ResolvedAnnotationText:
    box = text.box
    font = ResolvedAnnotationFont(
        family=tuple(
            entry.strip().strip("'\"")
            for entry in _set(text.font.family, "marks.text.font.family").split(",")
        ),
        size=_set(text.font.size, "marks.text.font.size"),
        weight=css_weight_to_axis(_set(text.font.weight, "marks.text.font.weight")),
        style=_set(text.font.style, "marks.text.font.style"),
        line_height=_set(text.font.line_height, "marks.text.font.line_height"),
        color=_set(text.font.color, "marks.text.font.color"),
    )
    return ResolvedAnnotationText(
        font=font,
        halo=_set(text.halo, "marks.text.halo"),
        halo_opacity=_set(text.halo_opacity, "marks.text.halo_opacity"),
        box=ResolvedAnnotationBox(
            visible=_set(box.visible, "marks.text.box.visible"),
            fill=_set(box.fill, "marks.text.box.fill"),
            opacity=_set(box.opacity, "marks.text.box.opacity"),
            padding=_set(box.padding, "marks.text.box.padding"),
            radius=_set(box.radius, "marks.text.box.radius"),
            stroke=None
            if box.stroke is None
            else ResolvedAnnotationBoxStroke(
                color=_set(box.stroke.color, "marks.text.box.stroke.color"),
                width=_set(box.stroke.width, "marks.text.box.stroke.width"),
            ),
        ),
        position=_position(text.position),
        dx=_set(text.dx, "marks.text.dx"),
        dy=_set(text.dy, "marks.text.dy"),
        max_width=_auto_measure(font) if max_width == "auto" else max_width,
        max_share=max_share,
    )


def _auto_measure(font: ResolvedAnnotationFont) -> float:
    measurer = get_weighted_font_measurer(", ".join(font.family), font.weight)
    return measurer.measure("n" * _AUTO_WRAP_CHARS, font.size)


def _position(position: AnnotationPositionStyle) -> ResolvedAnnotationPosition:
    return ResolvedAnnotationPosition(
        edge=_set(position.edge, "marks.text.position.edge"),
        align=_set(position.align, "marks.text.position.align"),
        overlay=_set(position.overlay, "marks.text.position.overlay"),
    )
