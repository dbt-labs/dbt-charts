"""Enumerate the categorical values a board draws, then plan its color scales.

Stage: EXECUTE. The planner itself is pure and lives in compile
(``compile/resolve/style/category_colors.py``); the values it plans over can
only be known after the queries run, which is why the walk lives here.

Queries are board-global and executor-cached, so this pass costs one dict walk
per chart — the rows it reads are the same ones the sizing pass reads next.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
    PieChart,
    ScatterChart,
    _CartesianChartFields,
)
from dbt_charts.core.compile.models.chart.normalized._base import (
    _BaseChartFields,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorBinding,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartRows
from dbt_charts.core.compile.resolve.style.category_colors import (
    CategoryColorScale,
    ChartFieldValues,
    FieldObservation,
    categorical_channel_fields,
    plan_category_colors,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    context_with_category_colors,
    family_patch_for,
)
from dbt_charts.core.compile.template.variables import color_link_variable
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_compile import ERR_CATEGORY_COLOR_PIN_CONFLICT
from dbt_charts.core.utils import is_date_like, series_means, series_totals

if TYPE_CHECKING:
    from dbt_charts.core.execute.chart_data_provider import (
        ChartDataProvider,
        ResolveDataProvider,
    )


@dataclass(frozen=True)
class DefaultView:
    """The board under its variables' defaults: the view colors are ranked on.

    A filter changes which rows a chart draws, never which color a value owns,
    so the size ranking reads this view rather than the filtered one.
    """

    results: ChartDataProvider
    variables: VariableValues


# Choice variable → its option cells, as authored or queried.
OptionCells = Mapping[str, Sequence[Any]]  # type-state: explicit_any — option cells


# The text the engine names a null color cell by; a drawn null owns a slot like any value.
_NULL_SERIES = "null"


def _column_values(rows: ChartRows, field: str) -> tuple[tuple[str, ...], bool, bool]:
    """(distinct non-null values of ``field`` in first-seen order, whether any
    cell is null, whether the field is disqualified). Any non-null value that is
    not a plain string disqualifies: numbers are measured, not named."""
    values: dict[str, None] = {}
    has_null = False
    for row in rows:
        value = row.get(field)
        if value is None:
            has_null = True
        elif not isinstance(value, str):
            return (), False, True
        else:
            values[value] = None
    return tuple(values), has_null, False


def _size_field(chart: _BaseChartFields) -> str | None:
    """The column whose sum is a series' size on this chart, if it has one."""
    if isinstance(chart, PieChart):
        return chart.theta
    if isinstance(chart, _CartesianChartFields) and isinstance(chart.y, str):
        return chart.y
    return None


def _bar_lengths(chart: _BaseChartFields, rows: ChartRows, measure: str) -> ChartRows:
    """A ranged bar's rows with the measure replaced by each bar's drawn length.

    A waterfall's bar runs from ``y_start`` to ``y``, so where it ends says
    nothing about how big it is. Other charts' rows pass through.
    """
    if not isinstance(chart, BarChart) or chart.y_start is None:
        return rows
    start = chart.y_start
    return [
        {**row, measure: abs(float(row[measure]) - float(row[start]))}
        if _is_number(row.get(measure)) and _is_number(row.get(start))
        else {**row, measure: None}
        for row in rows
    ]


def _is_number(value: int | float | Decimal | str | None) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def _runs_backwards(chart: _BaseChartFields) -> bool:
    """Whether the author runs the chart's measure axis backwards (`domain: [6, 1]`)."""
    if not isinstance(chart, BarChart | LineChart | AreaChart | ScatterChart):
        return False
    style = family_patch_for(chart.style, chart.type)
    scale = None if style is None or style.axis_y is None else style.axis_y.scale
    domain = (
        None if scale is None or scale.continuous is None else scale.continuous.domain
    )
    return (
        domain is not None
        and isinstance(domain.min, int | float)
        and isinstance(domain.max, int | float)
        and domain.min > domain.max
    )


def _explicit_series_order(
    chart: _BaseChartFields, context: ChartStyleContext
) -> str | None:
    """The `series_order` the author set for this chart: its own style, else the
    board's. Read off the patches rather than a resolved cascade, so a chart
    whose style fails to resolve still fails as that chart, not the board."""
    if not isinstance(chart, BarChart | AreaChart):
        return None
    inherited = context.bar if chart.type == "bar" else context.area
    own = family_patch_for(chart.style, chart.type)
    if own is None or own.series_order is None:
        return inherited.series_order
    return own.series_order


def _observe(
    chart: _BaseChartFields,
    rows: ChartRows,
    ranking_rows: ChartRows,
    pinned_fields: frozenset[str],
    series_order: str | None,
) -> tuple[ChartFieldValues, frozenset[str]]:
    """Return (field → what this chart draws, disqualified fields).

    ``rows`` are what the chart draws now; ``ranking_rows`` are its default
    view, which decides size. A value only ``rows`` hold still gets a value
    entry (it needs a slot) but no size.

    A null cell is not disqualifying: a column with one empty cell is still a
    column of names, and the engine draws it as a series named ``null``, which
    needs a slot like any other value. A field with NO rows — an empty result
    set — is not disqualified either: there is nothing that could be
    mis-seated by any scale, so it contributes nothing to the domain and
    poisons nothing (see ``plan_board_category_colors``, which only poisons
    fields in the returned disqualified set).

    Any *non-null* value that is not a plain string disqualifies the field:
    numbers are measured, not named. Dates are disqualifying too — a time axis
    is ordered, not a set of names, so binding one would mean the wrong thing
    and burn a palette slot per timestamp. The date test is applied
    **unanimously**, matching ``classify_date_column_align``: one year-shaped
    value like "2024" among real names must not veto the column. A field
    pinned under ``style.charts.category_colors`` skips the date test: the
    author named its values as categories, and "2020" pinned to a color is
    a name, not a timestamp.

    ``series_order`` of ``data`` or ``alphabetical`` is the order the author asked
    of this chart; it becomes the chart's say over slot order.
    """
    observed: dict[str, FieldObservation] = {}
    disqualified_fields: set[str] = set()
    measure = _size_field(chart)
    sized = measure is not None and all(
        row.get(measure) is None or _is_number(row.get(measure)) for row in ranking_rows
    )
    for field in categorical_channel_fields(chart):
        ranking_values, ranking_null, ranking_bad = _column_values(ranking_rows, field)
        drawn_values, drawn_null, drawn_bad = _column_values(rows, field)
        if ranking_bad or drawn_bad:
            disqualified_fields.add(field)
            continue
        named = tuple(dict.fromkeys([*ranking_values, *drawn_values]))
        if field not in pinned_fields and named and all(is_date_like(v) for v in named):
            disqualified_fields.add(field)
            continue
        if ranking_null:
            ranking_values += (_NULL_SERIES,)
        if drawn_null:
            drawn_values += (_NULL_SERIES,)
        values = tuple(dict.fromkeys([*ranking_values, *drawn_values]))
        if not values:
            continue
        totals: dict[str, float] = {}
        if sized and measure is not None:
            # A scatter's sum only counts its points, so it is sized by the average.
            totals = (
                series_means(ranking_rows, field, measure)
                if isinstance(chart, ScatterChart)
                else series_totals(
                    _bar_lengths(chart, ranking_rows, measure), field, measure
                )
            )
        if totals and _runs_backwards(chart):
            # A backwards measure axis (a rank, 1 on top) makes the smaller value the better one.
            # Flipped by magnitude, the scale size ranks on.
            sizes = {value: abs(total) for value, total in totals.items()}
            top = max(sizes.values()) + min(sizes.values())
            totals = {value: top - size for value, size in sizes.items()}
        if series_order == "data":
            ordered = ranking_values + tuple(
                v for v in drawn_values if v not in ranking_values
            )
        elif series_order == "alphabetical":
            ordered = tuple(sorted(values))
        else:
            ordered = ()
        observed[field] = FieldObservation(values, totals, ordered)
    return observed, frozenset(disqualified_fields)


def plan_board_category_colors(
    board: Board,
    results: ResolveDataProvider,
    variables: VariableValues,
    variable_options: OptionCells,
    default_view: DefaultView | None = None,
) -> tuple[CategoryColorScale, ...]:
    """Return the board's value→color scales, one per field a chart colors by.

    ``variable_options`` maps each choice variable a chart link sets to its
    options. A chart whose link sets one from its color field seeds that
    field's scale with them in option order (see ``_linked_options``), so a
    filter that leaves rows for one option does not move any series' swatch.

    Size ranking reads ``default_view``, the board under its variables'
    defaults, so applying a filter never reshuffles colors. ``None`` ranks from
    the rows drawn now: the caller's variables are the defaults, or it holds
    one variable state only (a replayed recording). A chart whose default view
    cannot be read ranks from the rows it draws now rather than failing a board
    that renders.

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
    `plan_category_colors` applies to palette exhaustion: every chart keeps the
    coloring it has today.

    An EMPTY result — no rows, or a color column that is null in every row —
    is not a disqualification: a chart with no values to seat can never be
    mis-seated by any scale, so it simply contributes nothing to the domain
    and does not poison the field. Without this distinction, a runtime data
    change that empties one chart's result (a `dct serve` variable, a filter)
    would silently drop the shared binding for every OTHER chart on the board.

    Raises:
        ChartDataError: two nested boards pin one value to different colors.
    """
    pins = _authored_pins(board)
    observations: list[ChartFieldValues] = []
    poisoned: set[str] = set()
    for chart, context in _placed_charts(board):
        if chart.query_name is None:
            continue
        try:
            rows = results.execute_query(chart.query_name, variables)
        except DbtChartsError:
            continue
        ranking_rows = rows
        if default_view is not None:
            try:
                ranking_rows = default_view.results.execute_query(
                    chart.query_name, default_view.variables
                )
            except DbtChartsError:
                ranking_rows = rows
        observed, disqualified_fields = _observe(
            chart,
            rows,
            ranking_rows,
            frozenset(pins),
            _explicit_series_order(chart, context),
        )
        poisoned.update(disqualified_fields)
        if observed:
            observations.append(observed)

    plan = plan_category_colors(
        observations,
        pins,
        board.chart_style_context.palette,
        _linked_options(
            [chart for chart, _ in _placed_charts(board)], variable_options
        ),
    )
    for field in poisoned:
        plan.pop(field, None)
    return tuple(plan.values())


def _linked_options(
    charts: Sequence[_BaseChartFields],
    variable_options: OptionCells,
) -> dict[str, tuple[str, ...]]:
    """Field → the options of the variable a chart's link sets from it.

    Each option is seeded as the text the engine prints it as (a JSON scalar's
    own text), so ``static: [1, 2]`` seats the text rows ``"1"`` and ``"2"``. A
    list holding anything but scalars seeds nothing: dates and the like belong
    to a field `_observe` never binds.
    """
    seeded: dict[str, tuple[str, ...]] = {}
    for chart in charts:
        variable = (
            color_link_variable(chart.link) if isinstance(chart.link, str) else None
        )
        if variable is None or variable not in variable_options:
            continue
        options = variable_options[variable]
        if options and all(isinstance(option, str | int | float) for option in options):
            texts = tuple(
                option if isinstance(option, str) else json.dumps(option)
                for option in options
            )
            for field in categorical_channel_fields(chart):
                seeded[field] = texts
    return seeded


def _placed_charts(
    board: Board,
) -> list[tuple[_BaseChartFields, ChartStyleContext]]:
    """Charts the board actually renders, in layout order, each with the style
    context of the board that places it.

    Not `board.charts`: that pool also holds charts declared in `charts:` but
    never placed, and `dct render --chart X` narrows the layout without
    narrowing the pool. Planning over the pool would execute every query on
    the board for a single-chart render, and would let a chart nobody sees
    widen the domain — or push a field past palette capacity so the binding is
    declined for the charts that *are* on screen.
    """
    placed: list[tuple[_BaseChartFields, ChartStyleContext]] = []
    for item in board.layout.items:
        # Callout is the one family outside the shared chart envelope: no
        # query, no channels, nothing to observe.
        if isinstance(item.chart, _BaseChartFields):
            placed.append((item.chart, board.chart_style_context))
        if item.board is not None:
            placed.extend(_placed_charts(item.board))
    # One entry per CHART, not per placement: a board may place the same chart
    # in two slots, and it draws the same values both times.
    unique: dict[str, tuple[_BaseChartFields, ChartStyleContext]] = {}
    for chart, context in placed:
        unique.setdefault(chart.id, (chart, context))
    return list(unique.values())


def _authored_pins(board: Board) -> dict[str, CategoryColorBinding]:
    """Collect `style.charts.category_colors` from the root board and every nested board.

    Pins apply to the whole board wherever they are authored. The root wins a
    whole field over any nested board, as the outer scope. Sibling nested
    boards merge value by value, and two pinning one value differently is an
    error: neither is the outer scope, so neither can win.
    """
    nested: dict[str, dict[str, str]] = {}
    for item in board.layout.items:
        if item.board is None:
            continue
        for field, binding in _authored_pins(item.board).items():
            merged = nested.setdefault(field, {})
            for value, color in binding.values.items():
                first = merged.setdefault(value, color)
                if first.casefold() != color.casefold():
                    raise ChartDataError.from_code(
                        ERR_CATEGORY_COLOR_PIN_CONFLICT,
                        field=field,
                        value=value,
                        first_color=first,
                        second_color=color,
                    )
    pins = {
        field: CategoryColorBinding(values=values) for field, values in nested.items()
    }
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
            "chart_style_context": context_with_category_colors(
                board.chart_style_context, scales
            ),
            "layout": layout,
        }
    )
