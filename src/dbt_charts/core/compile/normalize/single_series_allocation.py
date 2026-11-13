"""Single-series rhythm-palette allocator.

The theme's ``single_series_palette`` is a multi-stop list (3 stops on
editorial and cream; 1 stop on stark and dark). This pass walks the
root board's full tree in document order and assigns each single-series-
eligible chart a slot index. Render later reads
``palette[chart.rhythm_slot % len(palette)]`` so the cycle wraps.

Eligibility — a chart consumes a slot only when its render path actually
falls through to ``theme.charts.single_series_palette``:

* ``type`` ∈ ``SINGLE_INK_CHART_TYPES`` — types whose mark paints with
  a single fill/stroke when not color-encoded.
* No ``color:`` encoding field (color encoding means multi-series via the
  categorical palette).
* ``y`` is not a multi-element list (multi-metric overlay uses the
  categorical palette).

Ineligible charts (multi-series, KPI, table, pie, etc.) do NOT get a slot
and do NOT advance the counter — adding or removing such a chart leaves the
rhythm of other authored-single-series charts unchanged.

Author overrides (``style.color`` or ``style.<family>.palette``) still
consume a slot: the slot is assigned by position regardless. Precedence
in ``_effective_single_series_fill`` (``compile/resolve/chart/_palette.py``)
lets the override win, but the slot a chart "would have used" stays
anchored to its position — so adding or removing an override on chart B
does not shift chart C's color.

Scope is the root board's whole tree. The walk descends through nested
boards (``cols:`` / ``rows:`` / titled sub-sections) in document order so
the rhythm reads as one continuous sequence across the whole dashboard,
not restart-per-wrapper. The allocator runs only at the root
``normalize_board`` call (``depth == 0``); recursive normalize_board calls
do not re-allocate. A chart that names no channel is eligible here but may bind
a ``color`` once its query's columns are known; ``with_bound_channels`` re-slots
the bound board with the same predicate.
"""

from __future__ import annotations

from collections.abc import Iterator

from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.normalized import Chart, _CartesianChartFields

# Canonical set of chart types whose mark color is the single-series ink
# from theme.charts.single_series_palette.
SINGLE_INK_CHART_TYPES: frozenset[str] = frozenset(
    {"bar", "funnel", "line", "area", "scatter", "circle", "point", "histogram"}
)


def is_single_series_eligible(chart: Chart) -> bool:
    if chart.type not in SINGLE_INK_CHART_TYPES:
        return False
    if not isinstance(chart, _CartesianChartFields):
        return False
    if chart.color is not None:
        return False
    return not (isinstance(chart.y, list) and len(chart.y) > 1)


def iter_charts_in_reading_order(board: Board) -> Iterator[Chart]:
    """DFS through the board's layout, yielding charts in document order.

    Descends into nested boards (wrappers, titled sub-sections, and tab
    items — each tab is normalized into its own nested ``Board``) so the
    rhythm is one continuous sequence across the whole dashboard. Charts
    sitting directly in ``board.charts`` but not in the layout tree are
    not visited — they are not part of the visible reading order.

    The yielded instances are the same objects stored in ``board.charts``
    (guaranteed by normalize_board's collection step), so in-place
    mutations on the yielded charts propagate to ``board.charts`` directly.
    """
    for item in board.layout.items:
        if item.type == "chart" and item.chart is not None:
            yield item.chart
        elif item.type == "board" and item.board is not None:
            yield from iter_charts_in_reading_order(item.board)


def single_series_slots(board: Board) -> dict[int, int]:
    """``id(chart)`` to rhythm slot, one slot per eligible placement in reading order.

    Keyed by instance, not chart id: ids are local to a board, so a nested
    board's ``row0`` is not the outer ``row0``. A chart placed twice at one
    board level is one shared instance and keeps the later placement's slot,
    because that placement writes last; across levels it is two instances.
    """
    slots: dict[int, int] = {}
    slot = 0
    for chart in iter_charts_in_reading_order(board):
        if is_single_series_eligible(chart):
            slots[id(chart)] = slot
            slot += 1
    return slots


def allocate_single_series_slots(board: Board) -> None:
    """Assign ``rhythm_slot`` to single-series-eligible charts across the
    full board tree in document order. In-place mutation of the compiled
    Chart models. Call once on the root board; do not re-call on nested
    boards — the root walk already descends into every nested sub-board, so
    a per-sub-board call would double-count and restart the rhythm."""
    slots = single_series_slots(board)
    for chart in iter_charts_in_reading_order(board):
        if id(chart) in slots:
            chart.rhythm_slot = slots[id(chart)]
