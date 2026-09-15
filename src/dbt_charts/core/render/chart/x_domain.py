"""The categorical x scale's rendered value order.

A band scale starts in query row order; an authored ``sort:`` can reorder it,
and an overlay layer can widen it. Three consumers need
that final order — ``emitters/_overlay.py``, which pins it onto the shared x
encoding; ``features/value_labels.py``, whose band-edge labels have to know
which category actually renders at each end; and
``features/endpoint_labels.py``, whose rail anchors each series on the category
drawn last. All three read it from here so there is one definition of "the
order Vega-Lite will draw".
"""

from __future__ import annotations

from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.type_inference import is_lex_sortable_date_like
from dbt_charts.core.render.chart.x_domain_paint_order import (
    XDomainPaintOrder,
    record_x_domain_paint_order,
)
from dbt_charts.core.render.utils import (
    DomainValue,
    normalize_scalar_for_json,
    ordered_distinct_values,
)
from dbt_charts.core.utils import (
    Rows,
    VlSortOp,
    coerce_numeric_cell,
    x_domain_order,
)


def vl_sort_op(sort: VLDict | None) -> VlSortOp:
    """The aggregate Vega-Lite folds a category's rows into before applying
    ``sort``.

    Every sort an authored ``chart.sort`` produces pins ``op`` (see
    ``dimension_sort_to_vl`` and ``bar_sort_to_vl``, ``emitters/_cartesian.py``,
    for which aggregate and why), so this reads the pin back rather than
    re-deriving it.

    The ``sum`` fallback covers the one unpinned sort this engine emits: a
    horizontal bar with no color channel defaults to largest-measure-first
    (``emitters/bar.py``). A category holds one row there — ``ERR-BAR-
    DUPLICATE-ROWS`` rejects a repeat — so every aggregate agrees and ``sum``
    is inherited rather than chosen.
    """
    return "min" if isinstance(sort, dict) and sort.get("op") == "min" else "sum"


def defined_domain_order(values: list[DomainValue]) -> list[DomainValue] | None:
    """``values`` in their one defined ascending order, or None if they have none.

    Two rules count as defined, and only two: every value is a number (numeric
    strings included — some adapters return a bucket column as text), or every
    value is a date-like bucket whose lexicographic order IS its chronological
    order (``is_lex_sortable_date_like``). Everything else — month
    abbreviations, ``low/medium/high``, region names — carries an order the
    data does not state, so this returns None and the caller must not invent
    one. Booleans are excluded rather than treated as 0/1: a two-value domain
    has no reading order worth imposing. ``date``/``datetime`` objects never
    reach here — ``DomainValue`` is what a domain value is *after*
    ``normalize_scalar_for_json``, which stringifies them.
    """
    if not values:
        return None
    if any(isinstance(v, bool) for v in values):
        return None
    if all(isinstance(v, str) and is_lex_sortable_date_like(v) for v in values):
        return sorted(values)
    numeric: dict[DomainValue, float] = {}
    for value in values:
        coerced = coerce_numeric_cell(value)
        if coerced is None:
            return None
        numeric[value] = coerced
    return sorted(values, key=numeric.__getitem__)


def extend_domain_in_base_order(
    base: list[DomainValue], extra: list[DomainValue]
) -> list[DomainValue] | None:
    """``base + extra`` in the order ``base`` itself states, or None if it states none.

    The base query's row order is authoritative — it reflects that query's
    ``ORDER BY`` — so this never re-sorts the base against its own grain. It
    only asks which direction of ``defined_domain_order`` the base already
    follows, ascending or descending, and puts ``extra`` where that same rule
    says they go. A base ordered most-recent-first therefore keeps taking new
    dates at the front, not at the end.

    Returns None — meaning the caller must leave paint order alone and warn —
    when the base's values have no defined order at all, when the base does not
    follow it in either direction (a genuinely unordered base states nothing to
    extend), or when ``extra`` is not orderable against them.
    """
    ordered_base = defined_domain_order(base)
    if ordered_base is None:
        return None
    if base == ordered_base:
        ascending = True
    elif base == ordered_base[::-1]:
        ascending = False
    else:
        return None
    merged = defined_domain_order(base + extra)
    if merged is None:
        return None
    return merged if ascending else merged[::-1]


def rendered_x_domain(
    x_enc: VLDict,
    base_data: Rows,
    layer_x_columns: list[tuple[str, Rows]],
    chart_id: str | None,
) -> list[DomainValue]:
    """The categorical x scale's value order as Vega-Lite actually renders it.

    Base rows in the encoding's own field ``sort`` order if one is authored,
    else query order. Under a field ``sort``, any layer-only category is
    tailed after the sorted base rows in first-seen order; without one, a
    layer-only category is placed into the order the base itself states,
    when it states one, per ``extend_domain_in_base_order`` above.

    The overlay reconciler (``_reconcile_x_domain``, ``emitters/_overlay.py``)
    pins this computed order onto every layered chart's shared categorical
    scale, and an explicit ``scale.domain`` then overrides whatever native
    order Vega-Lite's own field-sort would otherwise have produced.
    ``_layer_band_anchor`` (also in ``emitters/_overlay.py``) calls this over
    the same encoding, base rows and layer columns the reconciler is about to
    pin, reproducing that same order ahead of it; only ``chart_id`` differs,
    deliberately, and it cannot change the order (see below).

    Order of appearance is only meaningful while the domain comes from ONE
    dataset — there the query owns the order and this function must not touch
    it. The moment a layer contributes a category the base query never
    returned, "first seen" is paint order across two independently-ordered
    result sets, which is not an ordering at all: complementary month buckets
    split over two layer queries render ``2024-01, 2024-03, 2024-05, 2024-02,
    2024-04, 2024-06`` and read as a chronology they are not. So layer-only
    values are placed into the order the BASE already states, by
    ``extend_domain_in_base_order`` — the base's own relative order is never
    disturbed, and a base ordered most-recent-first keeps taking new values at
    the front. When the base states no order the values follow (month
    abbreviations, region names), nothing is guessed: the union keeps paint
    order and earns WARN-LAYER-X-DOMAIN-PAINT-ORDER. None of this applies
    once the encoding carries a field ``sort``: that case returns the plain
    union — base rows in sort order, then layer-only values tailed in their
    own first-seen order — before reaching ``extend_domain_in_base_order``,
    so it never earns the warning regardless of contributor count.

    Declining is recorded against ``chart_id`` (see
    ``x_domain_paint_order.py``) so WARN-LAYER-X-DOMAIN-PAINT-ORDER reports the
    decision this function made rather than re-deriving it from the raw rows.
    It is required rather than defaulted so a caller reading the order without
    owning the chart — ``_layer_band_anchor``, which reads the same domain the
    overlay reconciler is about to pin and record — has to say so.

    An empty base fed by a single contributing dataset is not paint order: the
    union is that one query's row order, and its ``ORDER BY`` is an ordering.
    Two layer columns reading the same diverging query count once between them
    — the second adds no value the first did not.

    The one caller-side thing this does not model is a pinned
    ``scale.domain`` — an explicit domain always wins in Vega-Lite, so a caller
    holding one should read that instead of calling this.
    """
    base_field = x_enc.get("field")
    if not isinstance(base_field, str):
        return []
    sort = x_enc.get("sort")
    sort_field = sort.get("field") if isinstance(sort, dict) else None
    raw_base_domain = x_domain_order(
        base_data,
        base_field,
        sort_field if isinstance(sort_field, str) else "",
        bool(isinstance(sort, dict) and sort.get("order") == "descending"),
        op=vl_sort_op(sort),
    )
    base_domain = list(
        dict.fromkeys(normalize_scalar_for_json(value) for value in raw_base_domain)
    )
    union: dict[DomainValue, None] = dict.fromkeys(base_domain)
    layer_only: list[DomainValue] = []
    # Datasets that actually put a value on the axis. Two layers reading the
    # same diverging query contribute one order between them, not two — the
    # union is that query's ORDER BY and there is no paint order to report.
    contributors = 1 if base_domain else 0
    for layer_field, layer_rows in layer_x_columns:
        added = False
        for value in ordered_distinct_values(layer_rows, layer_field):
            if value not in union:
                union[value] = None
                layer_only.append(value)
                added = True
        if added:
            contributors += 1
    if isinstance(sort, dict):
        return list(union)
    if not layer_only:
        return list(union)
    extended = extend_domain_in_base_order(base_domain, layer_only)
    if extended is not None:
        return extended
    if chart_id is not None and contributors > 1:
        record_x_domain_paint_order(
            chart_id,
            XDomainPaintOrder(x_field=base_field, layer_only=tuple(layer_only)),
        )
    return list(union)
