"""Fixed synthetic field names for the wide-measure fold transform, the
shared resolve-time validation/channel-injection every wide-capable cartesian
family resolver (bar, area, line, scatter) uses, and the Python mirror of the fold.

The constants are used by both the resolver (to bake WIDE_VALUE_FIELD as the
resolved y column) and the render emitter (to build the VL fold transform).
They live in compile/ so the resolver can import them without creating a
compile -> render circular dependency.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import _CartesianChartFields
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved._channel import ResolvedStyleChannel
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.chart.resolved.scatter import ResolvedScatterChart
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_FIELD_LABELS_WIDE_COLLISION,
    ERR_MULTI_Y_COLOR_CONFLICT,
    ERR_MULTI_Y_LAYERS_CONFLICT,
    ERR_WIDE_MEASURE_NAME_CONTAINS_SEPARATOR,
)
from dbt_charts.core.text.case import field_title
from dbt_charts.core.utils import Rows

# Field that VL fold emits for the folded numeric value.
WIDE_VALUE_FIELD = "__dbt_charts_wide_value__"
# Field that VL fold emits for the folded RAW measure/column name (e.g.
# "revenue_usd") -- the fold's key, before WIDE_LABEL_FIELD's humanization
# calculate transform runs on it. Consumers that need the real column name
# a measure came from (a click-through link's `{{ color }}`, resolution's
# own raw-domain identity) read this field, not WIDE_LABEL_FIELD.
WIDE_KEY_FIELD = "__dbt_charts_wide_key__"
# Human-readable series label used as the VL color field: the HUMANIZED
# measure name, prefixed by the dimension value verbatim when the chart
# also authors ``color:``. A few Python-side mirrors that need RAW
# measure identity for their own stack-order math instead stamp the raw
# measure name under this same key.
WIDE_LABEL_FIELD = "__dbt_charts_wide_label__"
# Synthetic sort-order field appended when stack != 'none'.
WIDE_SERIES_SEPARATOR = " - "

# The resolved chart types whose resolver folds an authored y: [a, b] list
# into `wide_measures` + WIDE_VALUE_FIELD -- heatmap is deliberately excluded
# (its own, different one-rect-layer-per-measure multi-measure path never
# populates `wide_measures`). The one place this tuple is spelled; every
# consumer that needs to gate on "is this chart's y: folded" imports it from
# here instead of re-spelling it -- a re-spelled copy can drift (add a fifth
# wide-capable family here and a hand-rolled tuple elsewhere silently stays
# stale).
WideMeasureChart = (
    ResolvedBarChart | ResolvedAreaChart | ResolvedLineChart | ResolvedScatterChart
)
WIDE_MEASURE_FAMILIES: Final = (
    ResolvedBarChart,
    ResolvedAreaChart,
    ResolvedLineChart,
    ResolvedScatterChart,
)


def wide_measure_fields(chart: ResolvedChart) -> tuple[str, ...]:
    """The real authored measure columns for a folded chart, or `()`.

    `()` both when `chart` isn't a wide-capable family (see
    `WIDE_MEASURE_FAMILIES`) and when it is one but wasn't authored wide --
    callers that only need a bool test truthiness; callers that need the
    actual columns (to read raw query rows instead of the synthetic
    WIDE_VALUE_FIELD, which is absent from them) get them directly, so a
    caller can never have the bool right and the field list wrong.
    """
    if isinstance(chart, WIDE_MEASURE_FAMILIES):
        return chart.wide_measures
    return ()


def unfold_wide_rows(
    data: Rows, measures: Sequence[str], dimension: str | None
) -> Rows:
    """Pre-fold wide (one row per x[, dimension], N measure columns) rows into
    long form.

    Mirrors the VL ``fold`` + ``calculate`` pair ``render/chart/emitters/_wide.py``
    emits for the actual mark data, so Python-side code that must reason about
    real values can feed a wide chart's data through the same functions an
    authored ``color:`` chart's long-form rows already go through. VL's own
    fold runs client-side, after this spec ships, so it never does this
    unpivot for us.

    ``dimension`` is the authored ``color:`` column when the measures are
    grouped by one: the composite key is then ``<dimension value> - <measure>``,
    using the RAW measure name -- this is Python-side identity math (stack
    order, alias sets), not the display string. ``_wide._label_expression``
    builds the humanized display label from the same raw measure via
    ``wide_measure_labels_for``; the two strings differ on purpose whenever a
    measure name needs humanizing. A row whose dimension is null names no
    series, same as ``distinct_series_values``.
    """
    return [
        {
            **row,
            WIDE_LABEL_FIELD: (
                measure
                if dimension is None
                else f"{row[dimension]}{WIDE_SERIES_SEPARATOR}{measure}"
            ),
            WIDE_VALUE_FIELD: row[measure],
        }
        for row in data
        if dimension is None or row.get(dimension) is not None
        for measure in measures
        if row.get(measure) is not None
    ]


def wide_dimension_values(data: Rows, dimension: str) -> list[Any]:  # type-state: explicit_any — raw dimension cells  # fmt: skip
    """Distinct non-null values of the ``dimension`` column, in first-seen order.

    Raw values, not their ``str()`` — the VL label expression compares the
    datum against each one as a JSON literal, so it must see the value the
    row actually carries — but deduplicated by the ``str()`` that names the
    series, so a column mixing ``True`` and ``1`` pins one domain entry per
    label. A null never names a series.
    """
    labels: set[str] = set()
    values = []
    for row in data:
        value = row.get(dimension)
        if value is not None and str(value) not in labels:
            labels.add(str(value))
            values.append(value)
    return values


def raw_wide_series_names(
    measures: Sequence[str], dimension: str | None, data: Rows
) -> list[str]:
    """Sorted RAW (unhumanized) wide series identity: the authored measures,
    crossed with every observed ``dimension`` value when one is authored.

    Measures come from the authored list, not observed rows — a measure that
    is null on every row would otherwise drop out of the domain and desync
    palette slots from the chart's own scale. Dimension values come from the
    rows, as an authored ``color:`` chart's do; a null never names a series.

    This is the RAW identity the fold's own ``WIDE_LABEL_FIELD`` stamp
    uses. A caller correlating against real row data (a stack/last-value
    order, an anchor-row collision check) needs this, not
    ``wide_series_names``'s humanized text -- humanize the output
    afterward, per-entry, with ``humanize_wide_series_name``, rather
    than sorting or matching on humanized text.
    """
    if dimension is None:
        return sorted(measures)
    return sorted(
        f"{v}{WIDE_SERIES_SEPARATOR}{m}"
        for v in wide_dimension_values(data, dimension)
        for m in measures
    )


def humanize_wide_series_name(
    name: str, dimension: str | None, wide_measure_labels: dict[str, str]
) -> str:
    """Humanize one RAW wide series identity (``raw_wide_series_names``'s
    own output, or ``unfold_wide_rows``'s ``WIDE_LABEL_FIELD`` value) into
    ``wide_series_names``'s display text. Only the measure component is
    humanized; a dimension value composes back in verbatim, since it's a
    query value, not a column name.

    Splits on the LAST separator occurrence, assuming everything after it
    is the measure name -- safe because ``resolve_wide_measure_channels``
    already rejects (``ERR_WIDE_MEASURE_NAME_CONTAINS_SEPARATOR``) any
    measure column whose own name contains the separator, at resolve time,
    before a chart carrying ``dimension`` can reach here.
    """
    if dimension is None:
        return wide_measure_labels[name]
    value, _, measure = name.rpartition(WIDE_SERIES_SEPARATOR)
    return f"{value}{WIDE_SERIES_SEPARATOR}{wide_measure_labels[measure]}"


def wide_series_names(
    measures: Sequence[str],
    dimension: str | None,
    data: Rows,
    wide_measure_labels: dict[str, str],
) -> list[str]:
    """Color-scale domain of a wide chart: ``raw_wide_series_names``
    (already sorted), with each entry humanized -- in that same
    RAW-sorted order, never re-sorted by the humanized text. Humanizing
    is not order-preserving (a `_usd` suffix injects `(` before a sort
    would see the letters), so sorting after humanizing can assign a
    different palette slot than sorting on the raw identity first.

    Must build the identical string ``_wide.py``'s ``_label_expression``
    does, or an authored ``legend.values`` entry resolves against a
    domain the emitter never actually paints.
    """
    return [
        humanize_wide_series_name(name, dimension, wide_measure_labels)
        for name in raw_wide_series_names(measures, dimension, data)
    ]


def wide_legend_aliases(
    measures: Sequence[str],
    dimension: str | None,
    data: Rows,
    wide_measure_labels: dict[str, str],
) -> dict[str, frozenset[str]]:
    """Alias every ``wide_series_names`` domain entry by its raw spelling,
    so an authored ``legend.values`` may name either. A dimensioned entry's
    alias set deliberately excludes the bare raw measure name alone --
    that spelling is ambiguous across dimension values on that shape, so
    an author typing it belongs in the alias-collision path (unmatched,
    not guessed), never seated as an unambiguous alias.
    """
    if dimension is None:
        return {
            wide_measure_labels[m]: frozenset({m, wide_measure_labels[m]})
            for m in measures
        }
    return {
        humanize_wide_series_name(raw, dimension, wide_measure_labels): frozenset(
            {raw, humanize_wide_series_name(raw, dimension, wide_measure_labels)}
        )
        for raw in raw_wide_series_names(measures, dimension, data)
    }


def validate_wide_measure_labels(normalized: _CartesianChartFields) -> None:
    """Raise ``ERR-FIELD-LABELS-WIDE-COLLISION`` when authored ``field_labels``
    leave two ``y: [...]`` measures sharing one label."""
    if not isinstance(normalized.y, list):
        return
    labels, _ = resolve_wide_measure_labels(
        tuple(normalized.y), normalized.field_labels
    )
    by_label: dict[str, list[str]] = {}
    for measure, label in labels.items():
        by_label.setdefault(label, []).append(measure)
    for label, group in by_label.items():
        if len(group) > 1:
            raise CompilationError.from_code(
                ERR_FIELD_LABELS_WIDE_COLLISION,
                chart_id=normalized.id,
                measures=", ".join(map(repr, group)),
                label=label,
            )


def resolve_wide_measure_channels(
    normalized: _CartesianChartFields,
    channels: dict[str, ResolvedStyleChannel],
    chart_type: str,
    *,
    has_layers: bool,
) -> tuple[dict[str, ResolvedStyleChannel], bool]:
    """Validate + inject the synthetic color channel for a wide (``y: [a, b]``)
    chart. Returns ``(channels, wide_measure_series)`` — ``channels`` is
    returned unchanged when ``normalized.y`` isn't a list.

    An authored ``color:`` column composes with the fold: it stays on the
    resolved chart's ``color`` field as the dimension the measures are grouped
    by, and the fold's series become ``<value> - <measure>`` composites. Any
    other ``color:`` (a literal hue, a gradient, a conditional scale) names
    nothing to cross the measures with and raises
    ``ERR_MULTI_Y_COLOR_CONFLICT``. ``ERR_MULTI_Y_LAYERS_CONFLICT`` covers
    ``layers:`` — a layer can author its own ``color:``, and even a colorless
    layer's overlay-merge assumes the base's ``y`` is a real, single field,
    not the synthetic ``WIDE_VALUE_FIELD``. A measure column whose own name
    contains ``WIDE_SERIES_SEPARATOR`` can't be split back into a dimension
    value and a measure once folded, and raises
    ``ERR_WIDE_MEASURE_NAME_CONTAINS_SEPARATOR`` -- checked here, from the
    authored ``y:`` list alone, rather than in ``humanize_wide_series_name``
    once real rows happen to produce a dimension value. Any resolve reaches
    this regardless of row count, including a zero-row resolve.

    Shared by every wide-capable cartesian family resolver (bar, area, line,
    scatter) — one validation/injection site instead of four near-identical
    copies that could silently drift.
    """
    wide_measure_series = isinstance(normalized.y, list)
    if not wide_measure_series:
        return channels, False
    if normalized.color is not None and channels["color"].mode != "series":
        raise CompilationError.from_code(
            ERR_MULTI_Y_COLOR_CONFLICT,
            chart_id=normalized.id,
            chart_type=chart_type,
            color_field=normalized.color,
        )
    if has_layers:
        raise CompilationError.from_code(
            ERR_MULTI_Y_LAYERS_CONFLICT,
            chart_id=normalized.id,
            chart_type=chart_type,
        )
    assert isinstance(normalized.y, list)
    validate_wide_measure_labels(normalized)
    if normalized.color is not None:
        for measure in normalized.y:
            if WIDE_SERIES_SEPARATOR in measure:
                raise CompilationError.from_code(
                    ERR_WIDE_MEASURE_NAME_CONTAINS_SEPARATOR,
                    measure=measure,
                    separator=WIDE_SERIES_SEPARATOR,
                )
    # Inject a synthetic series-color channel so every consumer that gates on
    # resolved_channels["color"].mode == "series" (endpoint labels, grouped
    # bar spacing, legend, palette ordering, tooltip) picks up multi-y for
    # free — same path as an authored color: field.
    channels = {
        **channels,
        "color": ResolvedStyleChannel(
            channel="color", mode="series", data_field=WIDE_LABEL_FIELD
        ),
    }
    return channels, True


def resolve_wide_measure_labels(
    measures: tuple[str, ...], field_labels: Mapping[str, str]
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Resolve each measure's label (``field_title``), falling colliding measures
    back to their own raw name -- iterated to a fixed point. An authored
    ``field_labels`` entry never falls back.

    A single fallback pass is not collision-free: a raw name can itself
    equal another measure's humanized label (``orders_cnt`` humanizes to
    ``orders (Count)``, which can collide with a measure literally named
    that). So this re-groups by the current label after every fallback
    round, until a round produces no new collision -- each round strictly
    grows the set of measures pinned to their own raw name (a colliding
    group holds at least two distinct strings sharing one label, so
    falling every member back to its own name always changes at least one
    of them), bounded by ``len(measures)``, so this always terminates.

    Known from ``y: [...]`` alone, no data needed.

    Returns ``(labels, collision_groups)``: ``labels`` is the final
    measure -> display-label map, baked into the resolved chart; the raw
    measure name stays every caller's series identity, this map is
    display text only. ``collision_groups`` maps every label that was
    actually shared to the distinct raw measures that shared it, for the
    render-warnings detector to report.
    """
    labels = {measure: field_title(measure, field_labels) for measure in measures}
    collision_groups: dict[str, list[str]] = {}
    while True:
        by_label: dict[str, list[str]] = {}
        for measure in measures:
            bucket = by_label.setdefault(labels[measure], [])
            if measure not in bucket:
                bucket.append(measure)
        colliding = {
            label: group for label, group in by_label.items() if len(group) > 1
        }
        if not colliding:
            return labels, collision_groups
        progressed = False
        for label, group in colliding.items():
            collision_groups[label] = group
            for measure in group:
                if measure not in field_labels and labels[measure] != measure:
                    labels[measure] = measure
                    progressed = True
        if not progressed:
            return labels, collision_groups


def wide_measure_labels_for(
    measures: tuple[str, ...], field_labels: Mapping[str, str]
) -> dict[str, str]:
    """Humanized display label for each raw wide measure name -- see
    ``resolve_wide_measure_labels`` for the fallback algorithm; this
    returns just its label half.

    A collision fires ``WARN_WIDE_MEASURE_LABEL_COLLISION`` from a
    render-tier detector rather than from here, since resolve() has no
    warnings-output channel to emit it through. The detector recomputes
    the same fixed point from the baked ``wide_measures`` tuple, since a
    raw-name fallback can't be distinguished from a measure whose raw
    name already equals its own humanized form.
    """
    labels, _ = resolve_wide_measure_labels(measures, field_labels)
    return labels


def bake_wide_measures_kwargs(
    normalized_y: str | list[str] | None,
    cartesian_kwargs: dict[str, Any],
) -> tuple[
    dict[str, Any],  # type-state: explicit_any — constructor kwargs
    tuple[str, ...],
]:
    """Resolve ``wide_measures`` from the authored ``y:`` list, and override the
    ``_cartesian_kwargs()``-built ``"y"`` entry to the synthetic
    ``WIDE_VALUE_FIELD`` when it's non-empty (``_cartesian_kwargs`` bakes
    ``normalized.y`` verbatim, list included — this is the one place that
    narrows it to the resolved model's ``y: str | None``).

    Returns ``(cartesian_kwargs, wide_measures)``; the caller spreads both
    into the resolved chart constructor (``**_ck, wide_measures=wide_measures``).
    ``wide_measure_labels`` is not a resolved-chart field -- every reader
    calls ``wide_measure_labels_for`` directly.
    """
    wide_measures = tuple(normalized_y) if isinstance(normalized_y, list) else ()
    if wide_measures:
        cartesian_kwargs = {**cartesian_kwargs, "y": WIDE_VALUE_FIELD}
    return cartesian_kwargs, wide_measures
