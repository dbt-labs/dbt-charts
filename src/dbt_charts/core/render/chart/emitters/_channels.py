"""Channel-to-encoding helpers for emitters."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedAxisStyle,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import (
    ChartDataset,
    PanelRows,
    map_panels,
)
from dbt_charts.core.render.chart.time_unit_detect import (
    BUCKETED_CALENDAR_UNITS,
    canonicalize_and_sort_ordinal_x,
    complete_ordinal_time_series,
    detect_time_unit,
    range_extension_keeps_band_axis,
)
from dbt_charts.core.render.chart.type_inference import (
    DetectedTimeUnit,
    infer_vega_type_from_data,
    resolve_authored_x_type,
    resolve_cartesian_x_type,
)
from dbt_charts.core.text.case import field_title


def gap_fill_ordinal_time(
    ax: ResolvedAxisStyle,
    x_field: str | None,
    color_field: str | None,
    data: list[dict[str, Any]],
    resolves_cartesian_x: bool,
    *,
    is_temporal: bool | None,
    detected_time_unit: DetectedTimeUnit,
    x_date_range: tuple[date, date] | None,
) -> tuple[list[dict[str, Any]] | None, bool]:
    """Bucket + gap-fill ordinal bucketed-time data, or just normalize it.

    Shared by the line/area/bar emitters. When the x axis carries an ordinal
    bucketed calendar grain (authored ``time_unit`` or auto-detected), collapse
    the rows to one per (bucket, series) and fill missing buckets per ``ax.fill``
    — mirrors V1 ``profile._resolve_ordinal_time_unit`` + ``_gap_fill``. When
    that grain instead resolves to a continuous temporal scale (line/area's
    implicit default) under ``fill: "null"``, no buckets are synthesized, but
    the rows are still sorted and x-canonicalized via
    ``canonicalize_and_sort_ordinal_x`` — this always returns a transformed
    list on either path, never a "just gap-filled" one.

    Returns ``(rows, x_authored_temporal)``. ``rows`` is None only when
    neither path applies (no data mutation needed). ``x_authored_temporal``
    is the ``resolve_authored_x_type(ax) == "temporal"`` verdict this
    function gates on — the ONE evaluation of that predicate for the whole
    chart render. Callers thread it straight into
    ``render_cartesian_overlay``'s ``base_x_authored_temporal`` instead of
    re-deriving it; ``x_field`` accepts None so a caller can call this
    unconditionally (no "does this chart even have an x field" branch of
    its own) and still get the real verdict back either way.

    ``resolves_cartesian_x`` must be False for a caller whose x-field instead
    renders as a plain categorical axis outside ``resolve_cartesian_x_type``'s
    resolution (e.g. horizontal bar, whose y-axis is a nominal category
    field) — such a caller always needs the full ordinal scaffold, since its
    axis can never resolve to a continuous temporal scale no matter what the
    density gate says. This function itself never calls
    ``resolve_cartesian_x_type`` — the ordinal-vs-temporal verdict for a
    caller with ``resolves_cartesian_x`` True arrives pre-computed via
    ``is_temporal``; ``mark_type``/``is_band_step_curve`` (the inputs
    ``resolve_cartesian_x_type`` needs to agree with the x-encoding) live
    only on the caller that computes it, not here.

    ``is_temporal`` is this function's one caller's (``gap_fill_ordinal_time_per_panel``)
    own ordinal-vs-temporal verdict, computed once against the whole
    (pre-panel-split) dataset and threaded through every panel, instead of
    each panel re-deriving it from its own (smaller) ``data`` — so a small
    panel's own bucket count can never diverge from the chart-wide
    x-encoding decision (a panel is a subset of the whole, so it can only
    have fewer distinct buckets, never push a whole-set-temporal verdict to
    ordinal). It is ``None`` only when the caller's own gate
    (``resolves_cartesian_x and x_field and data``) doesn't hold — which, by
    that same gate repeated on the branch below, means this function never
    reads ``is_temporal`` while it's ``None``: the branch below requires
    ``resolves_cartesian_x`` True, and this function's own early return two
    paragraphs up already exited for empty ``data``/``x_field`` on THIS
    panel, which (a panel being a subset of the whole) can only happen when
    the caller's pooled data/x_field were empty too.

    ``detected_time_unit`` likewise overrides the auto-detect branch below
    instead of re-running ``detect_time_unit`` against this call's own
    ``data`` — threaded through exactly like ``is_temporal``, for the same
    reason: re-deriving per panel lets sibling panels bucket at different
    grains, and runs ``detect_time_unit``'s ≥10% unparseable-value check
    against a much smaller per-panel denominator, so a chart that resolves
    fine pooled can newly raise for one sparse panel alone.

    Raises:
        ChartDataError: (ERR-GAP-FILL-BUCKET-COLLISION) via
            ``complete_ordinal_time_series`` when two rows collapse to the
            same (bucket, series) key.
    """
    x_authored_temporal = resolve_authored_x_type(ax) == "temporal"

    if not data or not x_field:
        return None, x_authored_temporal

    if x_authored_temporal:
        return None, True

    # Author explicitly disabled bucketing.
    authored_tu = ax.time_unit
    if authored_tu == "none":
        return None, False

    time_unit_authored = bool(authored_tu)
    # Resolve the effective time_unit: authored if explicit, else the
    # pooled-detected grain threaded in by the caller.
    time_unit: DetectedTimeUnit = (
        authored_tu if time_unit_authored else detected_time_unit
    )

    if time_unit is None or time_unit not in BUCKETED_CALENDAR_UNITS:
        return None, False

    # Only fire for ordinal (default) and auto; temporal handled above.
    if ax.type not in (None, "auto", "ordinal"):
        return None, False

    if (
        resolves_cartesian_x
        and ax.fill == "null"
        and ax.type in (None, "auto")
        and not time_unit_authored
    ):
        # is_temporal is never None here: this branch requires
        # resolves_cartesian_x True, and the caller only omits is_temporal
        # (leaves it None) when its own gate — the same
        # resolves_cartesian_x/x_field/data condition, evaluated pooled —
        # doesn't hold. See the is_temporal docstring paragraph above.
        assert is_temporal is not None
        if is_temporal:
            # No missing-bucket synthesis needed on a continuous temporal
            # scale, but the other two complete_ordinal_time_series side
            # effects (chronological sort, date-only ISO canonicalization)
            # still apply — see canonicalize_and_sort_ordinal_x.
            return canonicalize_and_sort_ordinal_x(data, x_field), False

    dim_fields = [color_field] if color_field else []
    assert ax.fill is not None, (
        "x-axis fill must be resolved before ordinal time-series completion"
    )
    if x_date_range is not None and not range_extension_keeps_band_axis(
        [[row.get(x_field) for row in data]], time_unit, x_date_range
    ):
        x_date_range = None
    return (
        complete_ordinal_time_series(
            data,
            x_field,
            time_unit,
            dim_fields,
            ax.fill,
            ax.fiscal_year_start_month,
            x_date_range,
        ),
        False,
    )


def gap_fill_ordinal_time_per_panel(
    ax: ResolvedAxisStyle,
    x_field: str | None,
    color_field: str | None,
    dataset: ChartDataset,
    mark_type: str,
    is_band_step_curve: bool,
    resolves_cartesian_x: bool,
    x_date_range: tuple[date, date] | None,
) -> tuple[list[dict[str, Any]] | None, bool]:
    """``gap_fill_ordinal_time``, run once per small-multiples panel.

    ``dataset`` must already be panel-shaped for this chart (the resolve-baked
    split, or a caller's ``restripe()`` of it after a render-time value
    mutation) — this never re-derives panel membership from row values.
    Nulls ``color_field`` first when it is itself a partition field
    (``map_panels``' rule 2 — the channel is constant within a panel, nothing
    left to cross-join over), then runs ``gap_fill_ordinal_time`` per panel
    and concatenates. ``dim_fields = [color_field]`` inside it stays
    unchanged and becomes correct: within a panel the grain genuinely is
    ``(x, color)``. A non-faceted chart is the N=1 case — one panel holding
    every row, so its single ``gap_fill_ordinal_time`` call is already the
    only source of order — the final pooled re-sort below is gated on
    ``bucketed_any`` (whether any panel actually bucketed, NOT on panel
    count) instead of running unconditionally: it is not
    idempotent on ``complete_ordinal_time_series``'s identity-return path
    (unfired: rows returned unmodified, in the query's own order), so an
    unconditional call would silently replace a preaggregated N=1 chart's
    query-ordered wire data with alphabetical order.

    Same ``(rows | None, x_authored_temporal)`` contract as
    ``gap_fill_ordinal_time``: returns ``None`` when it fired for no panel,
    so a caller's ``if filled is not None`` no-op path is unchanged.

    The ordinal-vs-temporal verdict AND the auto-detected time_unit are both
    decided once here, against the whole (pre-panel-split)
    ``dataset.all_rows()`` — see ``gap_fill_ordinal_time``'s ``is_temporal``/
    ``detected_time_unit`` docstrings for why: a per-panel re-derivation
    could see a smaller panel's own bucket count fall under
    ``MAX_ORDINAL_BUCKETS`` while the chart-wide x-encoding (and every
    higher-cardinality sibling panel) resolves temporal, synthesizing a
    gap-fill null row under a continuous scale that draws no mark for a
    missing bucket and needs no synthetic row for it — and could bucket at
    a coarser or finer grain than a denser sibling, or newly raise on a
    ≥10% unparseable-value ratio a smaller panel alone doesn't clear.

    Each panel still enumerates ONLY ITS OWN [min, max] bucket range (never
    the pooled whole-dataset range) — pooling it would synthesize a
    scaffold row for every bucket between two panels' dates even when each
    panel is individually dense over a narrow window (e.g. one panel's data
    sits entirely in 2000, a sibling's entirely in 2020: pooling would
    scaffold the 20-year gap between them for BOTH panels, ~240 bogus
    buckets, which would itself cross ``MAX_ORDINAL_BUCKETS`` and flip the
    chart to a continuous temporal scale the density gate above was built
    to avoid; see ``TestGapFillTimeUnitCentralizedAcrossPanels`` and the
    local-time-label-expr warning detector's density-gate tests). Disjoint
    per-panel ranges therefore concatenate (``map_panels`` -> ``all_rows()``,
    panel-declaration order) in a NON-chronological sequence when the
    panels don't happen to already be date-ordered — fixed below by a final
    chronological re-sort of the pooled flat result, which costs nothing
    per panel's own bucket count.

    Raises:
        ChartDataError: (ERR-GAP-FILL-BUCKET-COLLISION) via
            ``gap_fill_ordinal_time`` when two rows in one panel collapse
            to the same (bucket, series) key.
    """
    data = dataset.all_rows()
    panel_fields = tuple(axis.field for axis in dataset.axes)
    is_temporal = None
    if resolves_cartesian_x and x_field and data:
        vl_type, _, _ = resolve_cartesian_x_type(
            data, x_field, ax, mark_type, is_band_step_curve, panel_fields
        )
        is_temporal = vl_type == "temporal"
    # A pure function of `ax` (the resolved axis style), not of any panel's
    # rows — computed once here so a chart resolved WITH data (baked
    # `panel_axes` non-empty) but rendered against zero rows (`dataset` has
    # 0 panels, `fill_one_panel` never runs) still gets the real verdict
    # instead of regressing to the `False` this variable would otherwise
    # stay initialized to. A chart resolved with NO data bakes
    # `panel_axes == ()`, the trivial N=1 case — one (empty) panel, so
    # `fill_one_panel` does run there. Mirrors gap_fill_ordinal_time's own
    # first line.
    x_authored_temporal = resolve_authored_x_type(ax) == "temporal"
    detected_time_unit: DetectedTimeUnit = None
    # Same predicates gap_fill_ordinal_time gates its own (pre-hoist)
    # detect_time_unit call on: an authored time_unit (including the
    # explicit "none" opt-out) or a temporal x-axis both skip auto-detect
    # entirely, so this must never run — and never raise — when either
    # applies. See that function's `detected_time_unit` docstring.
    if x_field and data and not ax.time_unit and not x_authored_temporal:
        x_values = [row.get(x_field) for row in data if x_field in row]
        x_type = (
            infer_vega_type_from_data(data, x_field)
            if x_field in data[0]
            else "nominal"
        )
        detected_time_unit = (
            detect_time_unit(x_values) if x_type == "temporal" else None
        )
    fired = False
    bucketed_any = False

    def fill_one_panel(
        effective_color: str | None, panel_rows: PanelRows
    ) -> list[dict[str, Any]]:
        nonlocal fired, bucketed_any
        filled, _x_authored_temporal = gap_fill_ordinal_time(
            ax,
            x_field,
            effective_color,
            panel_rows,
            resolves_cartesian_x,
            is_temporal=is_temporal,
            detected_time_unit=detected_time_unit,
            x_date_range=x_date_range,
        )
        if filled is None:
            return panel_rows
        fired = True
        # complete_ordinal_time_series returns its `data` argument unchanged
        # (the same list object) on every identity path — no rows, no parseable
        # x values, no dates. Identity means nothing was bucketed, so the rows
        # are still in the query's own order and the pooled re-sort below must
        # not touch them.
        if filled is not panel_rows:
            bucketed_any = True
        return filled

    result = map_panels(dataset, color_field, fill_one_panel)
    if not fired:
        return None, x_authored_temporal
    filled_rows = result.all_rows()
    if x_field and bucketed_any:
        # complete_ordinal_time_series ran per panel above (never pooled —
        # see the density-gate paragraph in this function's docstring), so
        # ChartDataset.all_rows()'s panel-order concatenation is not
        # guaranteed chronological when panels' own ranges are disjoint.
        # canonicalize_and_sort_ordinal_x re-sorts the FLAT pooled result by
        # bucket key, independent of any panel's own enumerated range, so
        # the ordinal x scale's encounter-order domain reads chronologically
        # regardless of how the panels' dates interleave. NOT gated on
        # ``is_temporal``: that verdict is decided on the RAW rows, and only
        # ``fill: null`` lets it short-circuit gap-fill — under any other
        # authored fill the buckets get enumerated anyway and the emitter,
        # re-resolving against those filled rows, lands back on ordinal, where
        # encounter order IS the domain. Gating on the pre-fill verdict left
        # that axis reading 2020→2021 then 2000→2001. Running the sort on a
        # genuinely continuous scale is near-free (marks position by literal
        # date value), which is the cheaper side to be wrong on. Not entirely
        # free: one order-dependent consumer survives there — a faceted line
        # chart with ``style.dashes`` and a non-partition ``color`` reaches
        # ``_distinct_in_order`` (line.py), so the sort can shift that scale's
        # domain order and its palette slots. Cosmetic, and arguably the more
        # correct order.
        # Skipped when no panel actually bucketed (``bucketed_any``): this
        # sort is not idempotent on
        # ``complete_ordinal_time_series``'s identity-return path, where the
        # rows are unmodified and still in the query's own order, so running it
        # there would silently replace query-ordered wire data with
        # alphabetical order — for a faceted chart as much as an N=1 one.
        # Panel count is the wrong gate: whether bucketing ran is the property
        # that makes the re-sort safe, and a faceted chart on an unbucketable
        # x column takes the same identity path a single-panel one does.
        filled_rows = canonicalize_and_sort_ordinal_x(filled_rows, x_field)
    return filled_rows, x_authored_temporal


def categorical_color_encoding(
    color_ch: ResolvedStyleChannel | None, enc_type: str | None
) -> bool:
    """True when a color encoding is a categorical series worth resolving
    ``legend.values`` against -- a series-mode channel, a nominal or
    ordinal VL type, and a bound field.

    ``enc_type`` is the caller's own VL type for this encoding (typically
    ``enc.get("type")``; the render-warnings detector, which has no
    ``enc``, passes its own independently-inferred type instead). Every
    family checked this same triple before calling
    ``apply_legend_entry_order``, spelled differently per call site --
    this is the one place that spells it.
    """
    return (
        color_ch is not None
        and color_ch.mode == "series"
        and enc_type in ("nominal", "ordinal")
        and bool(color_ch.data_field)
    )


_LEGEND_TOKEN_FOLD = re.compile(r"[-_\s]+")


def _fold_legend_token(token: str) -> str:
    """Case/separator-fold a legend token for alias matching.

    Collapses ``-``, ``_`` and runs of whitespace to a single space, then
    casefolds, so ``net_revenue``, ``Net-Revenue`` and ``net revenue`` all
    fold to the same key. Only ever compares spellings a caller already
    enumerated (the domain entry itself, plus its ``aliases``) -- never used
    to invent or guess one.
    """
    return _LEGEND_TOKEN_FOLD.sub(" ", token).strip().casefold()


def resolve_legend_entries(
    authored: list[str],
    domain: list[str],
    aliases: dict[str, frozenset[str]] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve an authored ``legend.values`` list against the real domain.

    ``domain`` is the display-order list (a permutation of the legend's
    real entries). ``aliases`` maps a domain entry to the extra spellings
    that also resolve to it (e.g. a wide chart's raw measure name for its
    humanized label), built forward from each entry's own provenance.

    Two tiers, exact then fold. An exact spelling of a domain entry or
    its alias always wins, even over a fold match on a different entry.
    Fold matching (case/separator-insensitive) only runs when there is
    no exact spelling, and only that tier can be ambiguous.

    Returns ``(resolved, unmatched)``: ``resolved`` is the matched
    entries in authored order; ``unmatched`` is every authored token
    that matched nothing or matched more than one entry ambiguously --
    never guessed, never raised. The caller drops an unmatched entry;
    the render-warnings detector reports it.
    """
    # Pass 1 claims every domain entry's own name first, locked into
    # own_names, so pass 2's alias fill can never overwrite it with an
    # alias that collides with a DIFFERENT entry's own name.
    exact_to_entry: dict[str, str | None] = {}
    for entry in domain:
        exact_to_entry.setdefault(entry, entry)
    own_names = frozenset(exact_to_entry)
    for entry in domain:
        entry_aliases = (
            aliases.get(
                entry, ()
            )  # type-state: silent_fallback — no aliases is a legal empty set, not masked bad input
            if aliases
            else ()
        )
        for spelling in entry_aliases:
            if spelling in own_names:
                continue
            if spelling not in exact_to_entry:
                exact_to_entry[spelling] = entry
            elif exact_to_entry[spelling] != entry:
                exact_to_entry[spelling] = (
                    None  # two aliases collide -- fall through to fold tier
                )

    folded_to_entries: dict[str, list[str]] = {}
    for entry in domain:
        entry_aliases = (
            aliases.get(
                entry, ()
            )  # type-state: silent_fallback — no aliases is a legal empty set, not masked bad input
            if aliases
            else ()
        )
        # Same own_names exclusion, carried into the fold tier: a
        # case/separator variant of an entry's own name must resolve as
        # unambiguously as the literal spelling does.
        for spelling in {entry, *entry_aliases}:
            if spelling in own_names and spelling != entry:
                continue
            folded_to_entries.setdefault(_fold_legend_token(spelling), []).append(entry)

    resolved: list[str] = []
    unmatched: list[str] = []
    for token in authored:
        exact_hit = exact_to_entry.get(token)
        if exact_hit is not None:
            resolved.append(exact_hit)
            continue
        candidates = list(
            dict.fromkeys(
                folded_to_entries.get(  # type-state: silent_fallback — zero candidates is legal, feeds the unmatched branch below, never hides an error
                    _fold_legend_token(token), []
                )
            )
        )
        if len(candidates) != 1:
            unmatched.append(token)
            continue
        resolved.append(candidates[0])
    return resolved, unmatched


def layer_label_and_aliases(
    authored_label: str | None, y_field: str, field_labels: Mapping[str, str]
) -> tuple[str, frozenset[str]]:
    """The legend label for a colorless overlay layer (or a colorless
    base), and its alias set: the label itself, the raw y-column, and the
    column's default title -- an authored ``legend.values`` entry may name
    any of the three.

    Pure config-to-config: takes no rows, so it cannot diverge in
    behavior between callers. Shared by the overlay emitter
    (``_overlay.py``) and the render-warnings detector
    (``legend_values_unresolved.py``).
    """
    default = field_title(y_field, field_labels)
    label = authored_label or default
    return label, frozenset({label, y_field, default})


__all__ = [
    "categorical_color_encoding",
    "infer_vega_type_from_data",
    "resolve_legend_entries",
]
