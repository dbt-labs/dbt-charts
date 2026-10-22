"""Enumerate the categorical values a board draws, then plan its color scales.

Stage: EXECUTE. The planner itself is pure and lives in compile
(``compile/resolve/style/category_colors.py``); the values it plans over can
only be known after the queries run, which is why the walk lives here.

Queries are board-global and executor-cached, so this pass costs one dict walk
per chart — the rows it reads are the same ones the sizing pass reads next.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
from dbt_charts.core.compile.models.chart.normalized._base import (
    _BaseChartFields,
)
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorBinding,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartRows
from dbt_charts.core.compile.resolve.style.category_colors import (
    CategoryColorScale,
    ChartFieldValues,
    categorical_channel_fields,
    plan_category_colors,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.utils import is_date_like

if TYPE_CHECKING:
    from dbt_charts.core.execute.executor import Executor


def _observe(
    chart: _BaseChartFields, rows: ChartRows, pinned_fields: frozenset[str]
) -> tuple[ChartFieldValues, frozenset[str]]:
    """Return (field → distinct string values this chart draws, disqualified fields).

    Nulls are skipped, not disqualifying: a column with one empty cell is
    still a column of names, and dropping the whole field would hand this
    chart a scale built from its siblings that cannot seat what it draws —
    Vega leaves those marks unpainted. A field with NO values at all — an
    empty result set, or a column that is null in every row — is not
    disqualified either: there is nothing that could be mis-seated by any
    scale, so it contributes nothing to the domain and poisons nothing (see
    ``plan_board_category_colors``, which only poisons fields in the
    returned disqualified set).

    Any *non-null* value that is not a plain string disqualifies the field:
    numbers are measured, not named. Dates are disqualifying too — a time axis
    is ordered, not a set of names, so binding one would mean the wrong thing
    and burn a palette slot per timestamp. The date test is applied
    **unanimously**, matching ``classify_date_column_align``: one year-shaped
    value like "2024" among real names must not veto the column. A field
    pinned under ``style.charts.category_colors`` skips the date test: the
    author named its values as categories, and "2020" pinned to a color is
    a name, not a timestamp.
    """
    observed: dict[str, tuple[str, ...]] = {}
    disqualified_fields: set[str] = set()
    for field in categorical_channel_fields(chart):
        values: list[str] = []
        disqualified = False
        for row in rows:
            value = row.get(field)
            if value is None:
                continue
            if not isinstance(value, str):
                disqualified = True
                break
            if value not in values:
                values.append(value)
        if disqualified:
            disqualified_fields.add(field)
            continue
        if not values:
            continue
        if field not in pinned_fields and all(is_date_like(v) for v in values):
            disqualified_fields.add(field)
            continue
        observed[field] = tuple(values)
    return observed, frozenset(disqualified_fields)


def plan_board_category_colors(
    board: Board,
    executor: Executor,
    variables: VariableValues,
) -> tuple[CategoryColorScale, ...]:
    """Return the board's value→color scales, one per bound field.

    A chart whose query fails is skipped rather than aborting the board: its
    own error surfaces through the normal resolve path, and dropping it here
    only means its values do not widen the domain.

    A field a chart ENCODES (an authored `color:` naming it) but whose values
    `_observe` genuinely DISQUALIFIED for that chart — non-string, or
    unanimously date-like and unpinned — POISONS the field board-wide. `_bound_scales`
    (resolve/chart/_kwargs.py) re-attaches any plan-bound scale to every chart
    whose `color:` names the field, regardless of that chart's own values (it
    cannot know them at resolve time), so a scale that cannot seat this
    chart's values can never safely bind for ANY chart on the board — dropping
    the field here is the only way to keep that promise. This matches the
    same "decline rather than break a board that rendered fine" policy
    `plan_category_colors` already applies to palette exhaustion and the
    two-chart threshold: every chart keeps the coloring it has today.

    An EMPTY result — no rows, or a color column that is null in every row —
    is not a disqualification: a chart with no values to seat can never be
    mis-seated by any scale, so it simply contributes nothing to the domain
    and does not poison the field. Without this distinction, a runtime data
    change that empties one chart's result (a `dct serve` variable, a filter)
    would silently drop the shared binding for every OTHER chart on the board.
    """
    pins = _authored_pins(board)
    observations: list[ChartFieldValues] = []
    poisoned: set[str] = set()
    for chart in _placed_charts(board):
        if chart.query_name is None:
            continue
        try:
            rows = executor.execute_query(chart.query_name, variables)
        except DbtChartsError:
            continue
        observed, disqualified_fields = _observe(chart, rows, frozenset(pins))
        poisoned.update(disqualified_fields)
        if observed:
            observations.append(observed)

    plan = plan_category_colors(
        observations,
        pins,
        board.chart_style_context.palette,
    )
    for field in poisoned:
        plan.pop(field, None)
    return tuple(plan.values())


def _placed_charts(board: Board) -> list[_BaseChartFields]:
    """Charts the board actually renders, in layout order.

    Not `board.charts`: that pool also holds charts declared in `charts:` but
    never placed, and `dct render --chart X` narrows the layout without
    narrowing the pool. Planning over the pool would execute every query on
    the board for a single-chart render, and would let a chart nobody sees
    widen the domain — or push a field past palette capacity so the binding is
    declined for the charts that *are* on screen.
    """
    charts: list[_BaseChartFields] = []
    for item in board.layout.items:
        # Callout is the one family outside the shared chart envelope: no
        # query, no channels, nothing to observe.
        if isinstance(item.chart, _BaseChartFields):
            charts.append(item.chart)
        if item.board is not None:
            charts.extend(_placed_charts(item.board))
    # One entry per CHART, not per placement. A board may place the same
    # chart in two slots; it draws the same values both times, so counting it
    # twice would carry a single-chart board over the two-chart threshold —
    # the case the threshold exists to decline.
    seen: set[str] = set()
    unique: list[_BaseChartFields] = []
    for chart in charts:
        if chart.id in seen:
            continue
        seen.add(chart.id)
        unique.append(chart)
    return unique


def _authored_pins(board: Board) -> dict[str, CategoryColorBinding]:
    """Collect `style.charts.category_colors` from the root board and every nested board.

    Charts live in the root pool but a nested board carries its own chart
    style context, so pins authored there were validated and then never
    read. Root wins on a conflict: it is the outer scope.
    """
    pins: dict[str, CategoryColorBinding] = {}
    for item in board.layout.items:
        if item.board is not None:
            pins.update(_authored_pins(item.board))
    pins.update(board.chart_style_context.category_color_pins)
    return pins


def with_category_colors(board: Board, scales: tuple[CategoryColorScale, ...]) -> Board:
    """Return ``board`` with ``scales`` carried on every chart style context.

    Nested boards draw from the root chart pool but each holds its own context,
    so the binding is threaded into all of them — otherwise a chart placed in a
    nested board would fall back to chart-local coloring and break the very
    consistency this establishes.
    """
    if not scales:
        return board
    layout = board.layout.model_copy(
        update={
            "items": [
                item.model_copy(
                    update={"board": with_category_colors(item.board, scales)}
                )
                if item.board is not None
                else item
                for item in board.layout.items
            ]
        }
    )
    return board.model_copy(
        update={
            # ChartStyleContext is compiler working state, not a Resolved*
            # model — dataclasses.replace on it is the sanctioned build step.
            "chart_style_context": dataclasses.replace(
                board.chart_style_context, category_colors=scales
            ),
            "layout": layout,
        }
    )
