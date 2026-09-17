"""Chart-row and layer-dataset type aliases shared across resolve modules.

Also holds the small-multiples panel split: every chart's data is a sequence
of panels, and a non-faceted chart has exactly one panel keyed ``()`` — the
N=1 case, not a special one.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import EllipsisType
from typing import Any, Literal, NewType

import pydantic_core

from dbt_charts.core.compile.models.chart.authored import MultiplesConfig
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.chart.resolved import PartitionAxis
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_MULTIPLES_FIELD_NOT_FOUND,
    ERR_MULTIPLES_VALUE_COLLISION,
)
from dbt_charts.core.diagnostics.codes_render import (
    ERR_MULTIPLES_ROW_MISSING_PARTITION_FIELD,
    ERR_MULTIPLES_ROW_OUTSIDE_PANEL_AXES,
)
from dbt_charts.core.utils import CellValue

__all__ = [
    "CartesianChart",
    "ChartDataset",
    "ChartRows",
    "LayerDatasets",
    "Panel",
    "PanelRows",
    "canonical_key",
    "fold_panels",
    "map_panels",
    "partition",
    "reduce_panels",
    "regroup",
    "restamp",
    "restripe",
]


ChartRows = list[dict[str, Any]]


LayerDatasets = Mapping[str, ChartRows] | EllipsisType


CartesianChart = BarChart | LineChart | AreaChart | ScatterChart


# Produced only by partition()/regroup()/map_panels() below — a row list that
# has already been through the small-multiples split, so an aggregating
# helper that annotates its parameter as PanelRows can never be handed a
# whole-dataset ChartRows by accident (pyright rejects the mismatch).
PanelRows = NewType("PanelRows", list[dict[str, Any]])


def canonical_key(value: Any) -> str:
    """Canonical string form of a partition value.

    The one shared original->canonical mapping used by both ``partition()``
    and ``regroup()`` — a panel key (``Panel.key``) holds the original value;
    a baked ``PartitionAxis.values`` entry holds this canonical form. Two
    distinct values that canonicalize to the same string are a collision,
    raised by ``partition()`` rather than silently merged into one panel.

    Uses ``pydantic_core.to_json`` rather than ``str()``: a faceted chart's
    baked axes are replayed from a ``BoardRecording``, whose
    ``rows_by_query: dict[str, CacheRows]`` is ``Any``-typed, so
    ``model_dump_json()`` serializes a row's value through this exact
    runtime-type-inferred serializer (``datetime`` -> ISO-8601 with a ``Z``
    UTC suffix, ``timedelta`` -> ISO-8601 duration, ``bytes`` -> decoded
    text, ...) and ``model_validate_json()`` reads it back as that same
    JSON scalar. Canonicalizing bake and replay through the same serializer
    — instead of hand-matching format pairs like ``isoformat()`` — is what
    keeps the two currencies equal by construction rather than by luck.
    """
    return pydantic_core.to_json(value).decode()


@dataclass(frozen=True)
class Panel:
    """One small-multiples panel: its partition key (original values) + rows.

    ``rows`` never carries the partition column(s) — they are constant
    within a panel by construction and held once, on ``key``.

    ``indices`` records each row's position in the ``rows`` argument
    ``partition()``/``regroup()`` split — parallel to ``rows``, same length
    — so ``all_rows()`` can re-interleave panels back into query order
    (non-``None`` is the "tracked" state; ``partition()``/``regroup()`` always
    produce one, even ``()`` for a genuinely empty panel — a real, tracked
    zero-row state, not the untracked one). A transform that changes a
    panel's row composition (``map_panels()``, e.g. gap-fill synthesizing
    bucket rows) cannot keep the mapping honest, so it resets ``indices`` to
    ``None`` ("untracked") rather than carry a stale one — ``all_rows()``
    then falls back to plain panel-order concatenation for the whole
    dataset. ``None``, not ``()``: an untracked panel and a tracked-but-empty
    panel are different states, and collapsing them onto the same empty
    tuple would make the distinction unrepresentable-when-wrong.
    """

    key: tuple[Any, ...]
    rows: PanelRows
    indices: tuple[int, ...] | None


@dataclass(frozen=True)
class ChartDataset:
    """A chart's query rows, split into panels. ``axes == ()`` when not faceted.

    Three accessors, deliberately few:
      - iterate ``panels`` directly for anything that groups or aggregates —
        you only ever hold one panel's rows at a time.
      - ``column_values(field)`` for column-wise/union reads (type inference,
        distinct-series count, sort order, palette domain, tick values) —
        identical whether the chart is faceted or not.
      - ``all_rows()`` for the VL ``data.values`` wire format only.
    """

    axes: tuple[PartitionAxis, ...]
    panels: tuple[Panel, ...]

    def column_values(self, field: str) -> tuple[Any, ...]:
        """Distinct values of ``field``, in first-encounter order.

        A partition field's order comes from its own baked axis
        (``PartitionAxis.values``, computed once from the raw query rows in
        ``partition()``), not from re-deriving order out of panel traversal:
        panels are ordered by the axes' cartesian product, which for a
        second-or-later axis in a sparse grid can disagree with that axis's
        own first-encounter order in the query (a later panel-key value can
        be that axis's actually-earliest row). Original (non-canonical)
        values are recovered from panel keys, cheaper than re-scanning rows,
        and correct even though the column itself does not exist inside any
        single panel's ``rows``.

        A baked axis value with no panel here is skipped, not an error: the
        row-truncated render path (``regroup()``) can hold fewer rows than
        resolve saw, so a value ``partition()`` observed in the full result
        may have zero panels in this particular ``ChartDataset``.
        """
        for index, axis in enumerate(self.axes):
            if axis.field == field:
                original_by_canonical: dict[str, CellValue] = {}
                for panel in self.panels:
                    value = panel.key[index]
                    original_by_canonical.setdefault(canonical_key(value), value)
                return tuple(
                    original_by_canonical[canon]
                    for canon in axis.values
                    if canon in original_by_canonical
                )
        seen = []
        seen_set = set()
        for panel in self.panels:
            for row in panel.rows:
                if field not in row:
                    continue
                value = row[field]
                if value not in seen_set:
                    seen_set.add(value)
                    seen.append(value)
        return tuple(seen)

    def all_rows(self) -> ChartRows:
        """Flatten back to the VL wire format, re-stamping partition values.

        Re-interleaved into the ORIGINAL query row order via each panel's
        ``indices`` (recorded by ``partition()``/``regroup()``) — the flat
        wire order must match the query's own order (the shared nominal x
        domain a VL facet derives from data-encounter order must not move
        just because panels split it), not the panel-grouped order the split
        itself uses. Falls back to plain panel-order concatenation when any
        panel is "untracked" (``map_panels()`` changed its row composition,
        e.g. gap-fill synthesizing bucket rows) — there is no original
        position to restore for a synthesized row. That fallback's panel
        order is NOT guaranteed chronological on its own: each panel of the
        one existing untracking transform, small-multiples gap-fill, still
        enumerates only its OWN bucket range (never a pooled one — see
        ``gap_fill_ordinal_time_per_panel``'s docstring for why pooling it
        would explode disjoint-but-individually-dense panels into a huge
        synthesized bucket count), so two panels with disjoint date ranges
        concatenate out of chronological order here. The caller
        (``gap_fill_ordinal_time_per_panel``) corrects this itself with a
        final chronological re-sort of this method's flat output before
        returning to the emitter — this method's own contract stays plain
        panel-order concatenation, nothing here sorts by field value.
        """
        tracked = True
        stamped: ChartRows = []
        original_indices: list[int] = []
        for panel in self.panels:
            for row in panel.rows:
                if self.axes:
                    row = dict(row)
                    for index, axis in enumerate(self.axes):
                        row[axis.field] = panel.key[index]
                stamped.append(row)
            if panel.indices is None:
                tracked = False
            else:
                original_indices.extend(panel.indices)
        if not tracked:
            return stamped
        order = sorted(range(len(stamped)), key=lambda i: original_indices[i])
        return [stamped[i] for i in order]


def _canonical_groups(
    fields: list[str], rows: ChartRows
) -> tuple[
    dict[tuple[str, ...], list[tuple[int, dict[str, Any]]]],
    dict[tuple[str, ...], tuple[Any, ...]],
]:
    """Group rows (partition columns stripped) by the canonical form of ``fields``.

    Returns ``(canonical_key -> [(original index in rows, stripped row), ...]
    in query order, canonical_key -> first-seen original values)``. The
    recorded index is what ``Panel.indices`` bakes, and what ``all_rows()``
    later uses to re-interleave panels back into query order. Shared by
    ``partition()`` (which also checks for a stringification collision
    across the whole dataset) and ``regroup()`` (which trusts the
    already-baked, collision-free axes).
    """
    field_set = set(fields)
    groups: dict[tuple[str, ...], list[tuple[int, dict[str, Any]]]] = {}
    originals: dict[tuple[str, ...], tuple[Any, ...]] = {}
    for index, row in enumerate(rows):
        original = tuple(row.get(field) for field in fields)
        canonical = tuple(canonical_key(value) for value in original)
        originals.setdefault(canonical, original)
        stripped = {k: v for k, v in row.items() if k not in field_set}
        groups.setdefault(canonical, []).append((index, stripped))
    return groups, originals


_MISSING = object()


def _canonically_distinct(previous: Any, value: Any) -> bool:
    """True when two values sharing one canonical string are a genuine collision.

    Plain ``!=`` calls two occurrences of the same ``NaN`` a collision — under
    IEEE754, ``nan != nan`` is ``True`` even though they canonicalize
    identically and are, for partitioning purposes, the same panel value.
    """
    if previous != previous and value != value:  # both NaN, by float's own quirk
        return False
    return previous != value


def partition(multiples: MultiplesConfig | None, rows: ChartRows) -> ChartDataset:
    """Split query rows into panels — the first act of ``resolve()``.

    ``multiples is None`` (not faceted) and empty ``rows`` both yield the
    trivial N=1 dataset: ``axes == ()``, one panel keyed ``()`` holding every
    row unstripped. Empty ``rows`` performs no missing-column check, mirroring
    ``FacetFeature``'s old ``if data:`` guard — a chart resolved with no data
    must not raise merely because it authors ``multiples``.

    Raises ``ChartDataError`` (``ERR-MULTIPLES-FIELD-NOT-FOUND``) when a
    partition field is not a column of the query result — checked per row,
    not just the first, since ``ValuesQuery.rows`` allows ragged inline data
    (a later row silently missing a column the first row has). A row that
    genuinely carries the field with a SQL ``NULL`` (``row[field] is None``)
    is a legitimate panel value, not an error — only a field *absent from
    the row entirely* raises. And a plain ``ChartDataError`` when two
    distinct values of one partition field canonicalize to the same string
    (a collision would otherwise silently merge two panels into one).
    """
    if not rows or multiples is None:
        return ChartDataset(
            axes=(),
            panels=(
                Panel(
                    key=(),
                    rows=PanelRows(list(rows)),
                    indices=tuple(range(len(rows))),
                ),
            ),
        )
    fields = [f for f in (multiples.rows, multiples.columns) if f is not None]
    axis_canon_order: dict[str, list[str]] = {field: [] for field in fields}
    axis_originals: dict[str, dict[str, Any]] = {field: {} for field in fields}
    for row in rows:
        missing = [field for field in fields if field not in row]
        if missing:
            raise ChartDataError.from_code(
                ERR_MULTIPLES_FIELD_NOT_FOUND,
                fields=missing,
                available=sorted(row.keys()),
            )
        for field in fields:
            value = row[field]
            canon = canonical_key(value)
            previous = axis_originals[field].get(canon, _MISSING)
            if previous is _MISSING:
                axis_originals[field][canon] = value
                axis_canon_order[field].append(canon)
            elif _canonically_distinct(previous, value):
                raise ChartDataError.from_code(
                    ERR_MULTIPLES_VALUE_COLLISION,
                    field=field,
                    previous=previous,
                    value=value,
                    canonical=canon,
                )
    axes = tuple(
        PartitionAxis(field=field, values=tuple(axis_canon_order[field]))
        for field in fields
    )
    groups, group_originals = _canonical_groups(fields, rows)
    panels = [
        Panel(
            key=group_originals[combo],
            rows=PanelRows([row for _, row in groups[combo]]),
            indices=tuple(index for index, _ in groups[combo]),
        )
        for combo in itertools.product(*(axis_canon_order[field] for field in fields))
        if combo in groups
    ]
    return ChartDataset(axes=axes, panels=tuple(panels))


def regroup(axes: tuple[PartitionAxis, ...], rows: ChartRows) -> ChartDataset:
    """Re-apply resolve's baked ``panel_axes`` to rows at render.

    Raises ``ChartDataError`` (``ERR-MULTIPLES-ROW-OUTSIDE-PANEL-AXES``) when
    a row carries a partition value the baked axes do not have — the
    row-truncated render path can only ever hold a subset of the baked axis
    values (never one the axes lack), so this firing means the chart is
    being rendered against data resolve never saw. Raises a distinct code
    (``ERR-MULTIPLES-ROW-MISSING-PARTITION-FIELD``) when a row is missing a
    partition field entirely (checked per row, not via ``.get()`` defaulting
    to ``None``) — a ragged row silently defaulting to ``None`` could
    misroute into a *legitimate* ``None`` panel baked from a real SQL NULL
    elsewhere in the dataset, the same phantom-panel hazard ``partition()``
    guards against, so it gets its own message rather than reusing the
    outside-panel-axes one with a fabricated ``None`` value.
    """
    if not axes:
        return ChartDataset(
            axes=(),
            panels=(
                Panel(
                    key=(),
                    rows=PanelRows(list(rows)),
                    indices=tuple(range(len(rows))),
                ),
            ),
        )
    fields = [axis.field for axis in axes]
    allowed = [set(axis.values) for axis in axes]
    for row in rows:
        for index, field in enumerate(fields):
            if field not in row:
                raise ChartDataError.from_code(
                    ERR_MULTIPLES_ROW_MISSING_PARTITION_FIELD,
                    field=field,
                    available=sorted(row.keys()),
                )
            canon = canonical_key(row[field])
            if canon not in allowed[index]:
                raise ChartDataError.from_code(
                    ERR_MULTIPLES_ROW_OUTSIDE_PANEL_AXES,
                    field=field,
                    value=row[field],
                    allowed=sorted(allowed[index]),
                )
    groups, group_originals = _canonical_groups(fields, rows)
    panels = [
        Panel(
            key=group_originals[combo],
            rows=PanelRows([row for _, row in groups[combo]]),
            indices=tuple(index for index, _ in groups[combo]),
        )
        for combo in itertools.product(*(axis.values for axis in axes))
        if combo in groups
    ]
    return ChartDataset(axes=axes, panels=tuple(panels))


def restripe(dataset: ChartDataset, rows: ChartRows) -> ChartDataset:
    """Re-slice a flattened-then-mutated ``rows`` back into ``dataset``'s panels.

    For a caller that took ``dataset.all_rows()`` and mutated some column's
    *values* in place without changing row count or order (e.g.
    ``normalize_labeled_temporal``) and now needs the panels back:
    ``regroup(dataset.axes, rows)`` would re-derive panel membership from
    ``rows``' own values, which breaks the moment the mutation touches a
    column that is *also* a partition field — the mutated value no longer
    matches the axis baked from the pre-mutation value. Panel membership was
    already decided when ``dataset`` was built; re-deriving it from mutated
    values is redundant when it agrees and wrong when it doesn't.

    Re-splits by each panel's recorded ``indices`` — the position each row
    held in the original ``rows`` argument ``dataset`` was itself built
    from — rather than by position in ``rows`` directly. This is why
    ``rows`` may be either ``dataset.all_rows()`` (query order, after the
    fix above) mutated in place, or the exact same list ``dataset`` was
    built from run through a count/order-preserving transform directly
    (e.g. a warning detector that normalizes ``ctx.chart_results`` before
    ever calling ``regroup()``): both are aligned to ``dataset``'s original
    row order by construction, which is all an index-based re-split needs.
    Raises when a panel is "untracked" (``indices is None``, e.g. this
    ``dataset`` came out of ``map_panels()``) — there is no original
    position to re-split by.
    """
    fields = {axis.field for axis in dataset.axes}
    total_rows = sum(len(panel.rows) for panel in dataset.panels)
    if len(rows) != total_rows:
        raise ValueError(
            f"restripe() got {len(rows)} rows for a dataset with "
            f"{total_rows} panel-slotted rows — a caller must not add or "
            "drop rows between dataset.all_rows() and restripe()"
        )
    panels = []
    for panel in dataset.panels:
        if panel.indices is None:
            raise ValueError(
                "restripe() requires a dataset whose panels still carry "
                "their original row indices (from partition()/regroup()) — "
                "this dataset lost them, most likely via map_panels()"
            )
        stripped = [
            {k: v for k, v in rows[original_index].items() if k not in fields}
            for original_index in panel.indices
        ]
        panels.append(
            Panel(key=panel.key, rows=PanelRows(stripped), indices=panel.indices)
        )
    return ChartDataset(axes=dataset.axes, panels=tuple(panels))


def restamp(dataset: ChartDataset, field: str) -> ChartDataset:
    """Restore one already-baked partition column into every panel's own rows.

    ``field``'s value is constant within a panel and held once, on
    ``Panel.key`` — stripped out of ``rows`` by ``partition()``/``regroup()``.
    A caller that groups or sums *within* a single panel by ``field`` (e.g.
    a stacked-total fold, when ``field`` is also the chart's category axis)
    needs it back on the rows it reads, the same way ``all_rows()`` restores
    every axis for the flat wire format — this restores just the one field,
    keeping the panel structure ``fold_panels``/``reduce_panels`` need. A
    no-op (returns ``dataset`` itself) when ``field`` is not one of
    ``dataset``'s own axes.
    """
    for index, axis in enumerate(dataset.axes):
        if axis.field == field:
            return ChartDataset(
                axes=dataset.axes,
                panels=tuple(
                    Panel(
                        key=panel.key,
                        rows=PanelRows(
                            [{**row, field: panel.key[index]} for row in panel.rows]
                        ),
                        indices=panel.indices,
                    )
                    for panel in dataset.panels
                ),
            )
    return dataset


def map_panels(
    dataset: ChartDataset,
    color_field: str | None,
    fn: Callable[[str | None, PanelRows], list[dict[str, Any]]],
) -> ChartDataset:
    """Run ``fn`` once per panel and reassemble — the panel-iteration combinator.

    Nulls ``color_field`` before ``fn`` sees it when it is a partition field:
    inside a panel a partition field is constant by construction, so a
    channel bound to it degenerates to one value — there is nothing left to
    cross-join over. This is the one place that rule lives; callers (gap-fill,
    per-panel validation) never branch on ``chart.multiples`` themselves.

    ``fn`` returns a plain ``list[dict]``, not ``PanelRows`` — the
    ``PanelRows(...)`` re-wrap happens here, the one place besides
    ``partition()``/``regroup()`` that constructs it, so a caller's ``fn``
    never has to import the constructor itself.

    ``fn`` may add, drop, or reorder rows (gap-fill synthesizes bucket
    rows), so a panel's original ``indices`` can no longer be trusted after
    this — every output panel is stamped "untracked" (``indices=None``, see
    ``Panel``), which makes the result's ``all_rows()`` fall back to plain
    panel-order concatenation rather than reinterleaving into a query order
    that no longer corresponds 1:1 with the output rows.
    """
    partition_fields = {axis.field for axis in dataset.axes}
    effective_color = None if color_field in partition_fields else color_field
    panels = tuple(
        Panel(
            key=panel.key,
            rows=PanelRows(fn(effective_color, panel.rows)),
            indices=None,
        )
        for panel in dataset.panels
    )
    return ChartDataset(axes=dataset.axes, panels=panels)


def reduce_panels(
    dataset: ChartDataset,
    per_panel: Callable[[PanelRows], float | None],
) -> float | None:
    """Reduce a per-panel scalar computation across every panel via ``max``.

    Skips panels where ``per_panel`` returns ``None``. Returns ``None`` when
    no panel produced a value. The N=1 (non-faceted) case is one panel, so
    this equals calling ``per_panel`` on the whole dataset directly.
    """
    values = [
        value
        for panel in dataset.panels
        if (value := per_panel(panel.rows)) is not None
    ]
    return max(values) if values else None


def fold_panels(
    dataset: ChartDataset,
    scale: Literal["shared", "independent"],
    per_panel: Callable[[PanelRows], float | None],
) -> float | None:
    """Reduce a per-panel scalar computation across panels under ``multiples.scale``.

    A facet has one inner Vega-Lite unit spec, so a baked value can only
    exist if it's the same for every panel: ``"shared"`` (the default, and
    the non-faceted N=1 case) reduces across panels via ``reduce_panels``;
    ``"independent"`` bakes nothing — Vega-Lite computes each panel's own
    scale, so this returns ``None`` unconditionally.
    """
    if scale == "independent":
        return None
    return reduce_panels(dataset, per_panel)
