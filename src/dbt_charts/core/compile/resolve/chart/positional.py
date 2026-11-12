"""Bind a chart's channels to its query's columns by position.

A chart that names none of its positional channels reads the query's shape the
way the query was written: dimensions first, the measure last. The table below
is the whole rule. Nothing here reads a column's name or type; a string in the
measure slot meets the family's own not-numeric error.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TypeVar

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored._annotations import ChartSort
from dbt_charts.core.compile.models.chart.authored._support_table import (
    ChartSupportTable,
    ChartSupportTablePerSeries,
)
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.resolve.chart.column_refs import match_column_refs
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_CHART_CHANNELS_INCOMPLETE,
    ERR_CHART_CHANNELS_NO_QUERY,
    ERR_CHART_CHANNELS_UNBOUND,
)

_C = TypeVar("_C", bound=Chart)

_NO_LAYER_COLUMNS: Mapping[str, Sequence[str]] = MappingProxyType({})

_CARTESIAN: Mapping[int, tuple[str, ...]] = {2: ("x", "y"), 3: ("x", "color", "y")}
_RADIAL: Mapping[int, tuple[str, ...]] = {1: ("theta",), 2: ("color", "theta")}

#: Per family: the channels a query's columns bind, by column count.
POSITIONAL_CHANNELS: Mapping[str, Mapping[int, tuple[str, ...]]] = {
    "bar": _CARTESIAN,
    "line": _CARTESIAN,
    "area": _CARTESIAN,
    "scatter": {2: ("x", "y"), 3: ("color", "x", "y")},
    "heatmap": {3: ("x", "y", "color")},
    "pie": _RADIAL,
    "donut": _RADIAL,
    "kpi": {1: ("value",)},
    "histogram": {1: ("x",)},
}

#: Per family: the channels that are named together; the rest are optional.
REQUIRED_CHANNELS: Mapping[str, tuple[str, ...]] = {
    "bar": ("x", "y"),
    "line": ("x", "y"),
    "area": ("x", "y"),
    "scatter": ("x", "y"),
    "heatmap": ("x", "y"),
    "pie": ("theta",),
    "donut": ("theta",),
    "kpi": ("value",),
    "histogram": ("x",),
}

#: Per family: every channel that takes part, optional ones included.
CHANNELS: Mapping[str, tuple[str, ...]] = {
    "bar": ("x", "y", "color", "pattern"),
    "line": ("x", "y", "color"),
    "area": ("x", "y", "color", "pattern"),
    "scatter": ("x", "y", "color", "size", "shape"),
    "heatmap": ("x", "y", "color", "pattern"),
    "pie": ("theta", "color", "pattern"),
    "donut": ("theta", "color", "pattern"),
    "kpi": ("value",),
    "histogram": ("x",),
}

#: Every channel the binder can fill, across families.
POSITIONAL_FIELDS: frozenset[str] = frozenset(
    channel for channels in CHANNELS.values() for channel in channels
)

_LAYERED = ("layers", "multiples", "tooltip")
#: Per family: the fields outside the channel set that name a column. A chart
#: setting one has chosen its columns, so binding the rest by position would
#: read that column a second time; it is half-named, like a `y` with no `x`.
PINNED_FIELDS: Mapping[str, tuple[str, ...]] = {
    "bar": ("y_start", *_LAYERED),
    "line": ("connect", *_LAYERED),
    "area": _LAYERED,
    "scatter": _LAYERED,
    "heatmap": ("multiples",),
    "pie": ("tooltip",),
    "donut": ("tooltip",),
    "kpi": (),
    "histogram": (),
}

_CARTESIAN_ATTACHMENTS = ("annotations", "support_table")
#: Per family: what resolves against the channels and so cannot attach to a
#: chart left unbound by a zero-row result.
ATTACHMENTS: Mapping[str, tuple[str, ...]] = {
    "bar": _CARTESIAN_ATTACHMENTS,
    "line": _CARTESIAN_ATTACHMENTS,
    "area": _CARTESIAN_ATTACHMENTS,
    "scatter": ("annotations",),
    "heatmap": (),
    "pie": (),
    "donut": (),
    "kpi": (),
    "histogram": (),
}

_SIDED = ("sort", "support_table", "labels")
#: Per family: the fields that name a column for a side use (an order, a table
#: beside the chart, a mark label on the chart itself; a cascade's label field
#: is not read). A column selected for that use alone must not fill an optional
#: channel, so a shape with one refuses every side column but the measure.
SIDE_FIELDS: Mapping[str, tuple[str, ...]] = {
    "bar": _SIDED,
    "line": _SIDED,
    "area": _SIDED,
    "scatter": ("sort", "labels"),
    "heatmap": ("sort",),
    "pie": (),
    "donut": (),
    "kpi": (),
    "histogram": (),
}


def columns_of(
    rows: Sequence[Mapping[str, object]],  # type-state: object_annotation — query cells
) -> list[str]:
    """A result's columns in query order; empty for a zero-row result."""
    return list(rows[0]) if rows else []


def _is_named(value: object) -> bool:  # type-state: object_annotation — any channel
    """An empty name or list names nothing, like an absent key."""
    return value is not None and value != "" and value != []


def _named(chart: Chart, fields: Sequence[str]) -> list[str]:
    return [field for field in fields if _is_named(getattr(chart, field))]


def _side_columns(chart: Chart, field: str) -> list[str]:
    if field == "labels":
        assert isinstance(chart, BarChart | LineChart | AreaChart | ScatterChart)
        if chart.style is None:
            return []
        # The style is a patch: `marks` may be unset, which its type omits.
        marks = chart.style.marks
        if not marks:
            return []
        return [
            mark.labels.field
            for name in type(marks).model_fields
            if (mark := getattr(marks, name)) is not None
            and "labels" in type(mark).model_fields
            and mark.labels is not None
            and mark.labels.field
        ]
    value = getattr(chart, field)
    if isinstance(value, ChartSort):
        return [value.by]
    if isinstance(value, ChartSupportTable):
        return [
            entry.per_series
            if isinstance(entry, ChartSupportTablePerSeries)
            else entry.source
            for entry in value.entries
        ]
    return []


def is_unbound(chart: Chart) -> bool:
    """True when the chart's family binds by position and it names no column."""
    channels = CHANNELS.get(chart.type)
    if channels is None:
        return False
    return not _named(chart, (*channels, *PINNED_FIELDS[chart.type]))


def channel_error(chart: Chart, columns: Sequence[str]) -> CompilationError | None:
    """Why the chart's channels cannot bind ``columns``; None when they can.

    A family outside the table, a chart naming its whole required set, and an
    unnamed chart with no columns to bind (a zero-row result, the data-free
    skeleton) all bind nothing and pass.
    """
    channels = CHANNELS.get(chart.type)
    if channels is None:
        return None
    named = _named(chart, channels)
    pinned = _named(chart, PINNED_FIELDS[chart.type])
    if named or pinned:
        missing = [c for c in REQUIRED_CHANNELS[chart.type] if c not in named]
        return (
            incomplete_error(chart.id, chart.type, named + pinned, missing)
            if missing
            else None
        )
    if chart.query_name is None:
        return CompilationError.from_code(
            ERR_CHART_CHANNELS_NO_QUERY,
            chart_id=chart.id,
            chart_type=chart.type,
            channels=_names(REQUIRED_CHANNELS[chart.type]),
        )
    return unbound_columns_error(
        chart.id,
        chart.type,
        columns,
        {field: _side_columns(chart, field) for field in SIDE_FIELDS[chart.type]},
    )


def unbound_columns_error(
    chart_id: str,
    chart_type: str,
    columns: Sequence[str],
    side_columns: Mapping[str, Sequence[str]],
) -> CompilationError | None:
    """Why a chart naming no channel cannot bind ``columns``; None when it can.

    ``side_columns`` is the columns it names for a side use, by field (``SIDE_FIELDS``).
    """
    if not columns:
        return None
    order = POSITIONAL_CHANNELS[chart_type].get(len(columns))
    if order is None:
        return unbound_error(chart_id, chart_type, columns)
    if len(order) > len(REQUIRED_CHANNELS[chart_type]):
        measure = columns[-1]
        sided = [
            field
            for field in SIDE_FIELDS[chart_type]
            if field in side_columns
            and any(c in columns and c != measure for c in side_columns[field])
        ]
        if sided:
            return incomplete_error(
                chart_id, chart_type, sided, REQUIRED_CHANNELS[chart_type]
            )
    return None


def bind_channels(
    chart: _C,
    columns: Sequence[str],
    layer_columns: Mapping[str, Sequence[str]] = _NO_LAYER_COLUMNS,
) -> _C:
    """The chart with its column references and channels bound to ``columns``.

    Every column reference first takes the result's own spelling
    (``match_column_refs``); a layer naming its own query reads that query's
    columns from ``layer_columns``, and keeps its references as authored when
    the query is not there. Then a chart that names a column or a family that
    binds none comes back as is; the error ``channel_error`` finds is raised.
    A result with no columns (zero rows, or the data-free skeleton) leaves the
    chart unbound and drops its ``ATTACHMENTS``, which resolve against the
    channels.
    """
    if chart.query_name is not None:
        chart = match_column_refs(chart, {**layer_columns, chart.query_name: columns})
    error = channel_error(chart, columns)
    if error is not None:
        raise error
    if not is_unbound(chart):
        return chart
    if not columns:
        dropped = _named(chart, ATTACHMENTS[chart.type])
        if not dropped:
            return chart
        fields = type(chart).model_fields
        return chart.model_copy(
            update={
                field: fields[field].get_default(call_default_factory=True)
                for field in dropped
            }
        )
    order = POSITIONAL_CHANNELS[chart.type][len(columns)]
    return chart.model_copy(update=dict(zip(order, columns, strict=True)))


def _names(channels: Sequence[str], joiner: str = "and") -> str:
    quoted = [f"`{channel}`" for channel in channels]
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + f" {joiner} {quoted[-1]}"


def incomplete_error(
    chart_id: str, chart_type: str, named: Sequence[str], missing: Sequence[str]
) -> CompilationError:
    """The error for a chart that names part of its channel set."""
    return CompilationError.from_code(
        ERR_CHART_CHANNELS_INCOMPLETE,
        chart_id=chart_id,
        chart_type=chart_type,
        named=_names(named),
        missing=_names(missing),
        required=_names(REQUIRED_CHANNELS[chart_type]),
    )


def unbound_error(
    chart_id: str, chart_type: str, columns: Sequence[str]
) -> CompilationError:
    """The error for a chart that names no channel and cannot bind ``columns``."""
    shapes = POSITIONAL_CHANNELS[chart_type]
    channels = _names(
        list(dict.fromkeys(c for order in shapes.values() for c in order)), "or"
    )
    outcome = (
        f"Query returned {len(columns)} column{'s' if len(columns) != 1 else ''}: "
        f"{', '.join(columns)}."
        if columns
        else "Query returned no rows, so there are no columns to bind."
    )
    return CompilationError.from_code(
        ERR_CHART_CHANNELS_UNBOUND,
        chart_id=chart_id,
        chart_type=chart_type,
        channels=channels,
        shapes="; ".join(
            f"{count} column{'s bind' if count != 1 else ' binds'} {', '.join(order)}"
            for count, order in shapes.items()
        ),
        outcome=outcome,
    )
