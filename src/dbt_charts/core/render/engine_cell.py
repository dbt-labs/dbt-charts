"""A chart the engine draws in the cell the host sized (`_engine.PreparedBoard.draw`).

The entry binds only the placed chart, but it finds that placement by the layout's own
rules (a `visible:` probe reads its query's result) and fills the card's text from the
board's variables, so a draw needs the render's engine compile and its run as wire texts:
every query result, the error of each chart whose query failed, the variable values and
the clock. `engine_board` holds them for one render, which the engine prepares once, on
its first cell (`EngineBoard.prepared`). `placements`
numbers each drawn chart's placements (`id`, `id#2`, as the engine keys them) in the order
the layout walk reaches them, which is the engine's own document order of drawn charts.
"""

from __future__ import annotations

import dataclasses
import json
import string
from collections import Counter
from collections.abc import Collection, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from functools import cache, cached_property
from typing import NamedTuple

from dbt_charts import _engine
from dbt_charts.core.compile.image_assets import project_images
from dbt_charts.core.compile.models.board.normalized import NO_TITLE_SHIFT, TitleShift
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedCalloutChart,
    ResolvedChart,
    ResolvedKpiChart,
    ResolvedSparkBarChart,
)
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.diagnostics import ERR_INTERNAL
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.cache_backend import ResultRows
from dbt_charts.core.execute.query_plan import ChartQueries, QueryPlan
from dbt_charts.core.execute.rows_wire import JsonDict
from dbt_charts.core.fonts import get_fonts_dir
from dbt_charts.core.render.board_links import LinkContext
from dbt_charts.core.render.board_variables import (
    current_board_variables,
    current_variable_defs,
)
from dbt_charts.core.render.engine import (
    authored_texts,
    chart_data_error,
    record_engine_warnings,
)
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.variables_resolve import empty_filters


def failed_query(queries: ChartQueries, failed: Collection[str]) -> str | None:
    """The plan key of the first of a chart's queries (its own, then each layer's) that
    failed to run, or None: a chart with one is drawn as its error card, never by the
    engine in a cell."""
    for key in (queries["query"], *queries["layers"]):
        if key is not None and key in failed:
            return key
    return None


# The families the engine draws in the cell production sized; every other family draws
# through `render_native`.
CELL_CHARTS = (ResolvedKpiChart, ResolvedCalloutChart, ResolvedSparkBarChart)


def drawn_in_cell(
    chart: ResolvedChart, queries: ChartQueries, failed: Collection[str]
) -> bool:
    """Whether ``chart`` draws through the engine's cell: a chart of `CELL_CHARTS` none
    of whose queries failed (that chart is its error card)."""
    return isinstance(chart, CELL_CHARTS) and not failed_query(queries, failed)


def failure_wire(query_name: str, error: DbtChartsError) -> JsonDict:
    """A query's execute error as the engine takes it (`engine::host::failure`): its code,
    its template's fields and its message. An error raised without its template's
    fields (`plain_error`) can only be a `{message}` code, whose one field is the text."""
    if error.code is None:
        raise TypeError(f"{query_name}: {type(error).__name__} carries no code")
    params = {
        name
        for _, name, _, _ in string.Formatter().parse(error.code.message_template)
        if name is not None
    }
    fields = (
        {"message": str(error)}
        if params == {"message"} and not params <= error.fields.keys()
        else error.fields
    )
    return {
        "query_name": query_name,
        "code": error.code.code,
        "fields": fields,
        "message": str(error),
    }


@cache
def _fonts() -> _engine.ShapedFonts:
    return _engine.load_shaped_fonts(str(get_fonts_dir()))


@dataclass
class EngineBoard:
    """One render's engine compile and run, and the placements its walk has reached."""

    compiled: _engine.CompiledBoard
    plan: QueryPlan
    datasets: Mapping[str, ResultRows]
    errors: Mapping[str, Exception]
    overridden: frozenset[str]
    # The host's builtin values (directory navigation): the plan echoes them untagged
    # beside the board's variables, and the engine takes only the board's.
    builtins: frozenset[str]
    rendered_at: datetime
    link_context: LinkContext | None

    @cached_property
    def prepared(self) -> _engine.PreparedBoard:
        """The engine's board and run, prepared on the first cell the render draws and
        shared by every later one. A board the engine refuses raises
        `RenderDiagnosticError`, and is tried again on the next cell."""
        datasets = {key: result.engine_rows for key, result in self.datasets.items()}
        chart_errors: dict[str, JsonDict] = {}
        for chart, queries in self.plan["chart_queries"].items():
            key = failed_query(queries, self.errors.keys())
            if key is not None:
                error = self.errors[key]
                chart_errors[chart] = failure_wire(
                    key,
                    error
                    if isinstance(error, DbtChartsError)
                    else RenderError.from_unexpected(error),
                )
        return _engine.PreparedBoard(
            self.compiled,
            datasets,
            json.dumps(chart_errors),
            json.dumps(
                {
                    name: value
                    for name, value in self.plan["variables"]["render"].items()
                    if name not in self.builtins
                }
            ),
            json.dumps(self.plan["variables"]["presets"]),
            sorted(self.overridden),
            _fonts(),
            self.rendered_at.isoformat(),
            None
            if self.link_context is None
            else json.dumps(dataclasses.asdict(self.link_context)),
            "{}",
        )


_current: ContextVar[EngineBoard | None] = ContextVar("_engine_board", default=None)


@contextmanager
def engine_board(board: EngineBoard) -> Generator[None]:
    """`board` is what the engine draws from for the rest of the render."""
    token = _current.set(board)
    try:
        yield
    finally:
        _current.reset(token)


def _board() -> EngineBoard:
    board = _current.get()
    if board is None:
        raise RenderError.from_code(
            ERR_INTERNAL, message="a chart is drawn by the engine outside a render"
        )
    return board


def in_cell(chart: ResolvedChart) -> bool:
    """`drawn_in_cell` for ``chart`` in the render in progress."""
    if not isinstance(chart, CELL_CHARTS):
        return False
    board = _board()
    return drawn_in_cell(
        chart, board.plan["chart_queries"][chart.id], board.errors.keys()
    )


_placed: ContextVar[Counter[str] | None] = ContextVar("_placed", default=None)


@contextmanager
def placements() -> Generator[None]:
    """A layout walk: each chart it reaches takes the next key of its placements."""
    token = _placed.set(Counter())
    try:
        yield
    finally:
        _placed.reset(token)


def next_placement(chart_id: str) -> str:
    """The key of `chart_id`'s next placement in the layout walk: `id`, then `id#2`."""
    placed = _placed.get()
    if placed is None:
        raise RenderError.from_code(
            ERR_INTERNAL, message="a chart is placed outside a layout walk"
        )
    placed[chart_id] += 1
    n = placed[chart_id]
    return chart_id if n == 1 else f"{chart_id}#{n}"


def placement_ordinal(chart_id: str, placement: str) -> int:
    """The number of `chart_id`'s placement keyed `placement` (`id` 0, `id#2` 1), which
    the engine scopes the ids it mints by."""
    if placement == chart_id:
        return 0
    prefix = f"{chart_id}#"
    if not placement.startswith(prefix):
        raise RenderError.from_code(
            ERR_INTERNAL, message=f"{placement!r} is not a placement of {chart_id!r}"
        )
    return int(placement.removeprefix(prefix)) - 1


class CellArgs(NamedTuple):
    """What `PreparedBoard.draw` takes after the placement, for one chart's cell."""

    empty_filters: list[tuple[str, str]]
    automatic_link: str | None
    width: float
    height: float | None
    inset: tuple[float, float, float, float]
    title_shift: tuple[float, float]
    images: dict[str, str]


def cell_args(
    chart: ResolvedChart,
    board_style: ResolvedStyle,
    width: float,
    height: float | None,
    padding: Mapping[str, float] | None,
    title_shift: TitleShift,
) -> CellArgs:
    """The cell a placement's sizing gives ``chart``: `width` and `height` (none, or 0,
    is the chart's own) with the `padding` `render_native` draws it inside (a card the
    slot was shrunk by, which its background reaches back over), under the cols-row
    `title_shift`, naming the filters that reach the chart when it has nothing to show
    (`empty_filters`), with the link production resolved for it (`automatic_link`: the
    engine reads it only for a chart that authors none), with the `data:` URI of each project image its text writes
    (`images`): the engine reads no files."""
    values = current_board_variables()
    if values is None:
        raise RenderError.from_code(
            ERR_INTERNAL,
            message="a chart is drawn in its cell outside a board's variables",
        )
    return CellArgs(
        empty_filters(chart, current_variable_defs(), values, board_style.variables),
        chart.link,
        width,
        height if height else None,
        (0.0, 0.0, 0.0, 0.0)
        if padding is None
        else (padding["left"], padding["right"], padding["top"], padding["bottom"]),
        (title_shift.title_dy, title_shift.body_dy),
        _images(chart),
    )


def _images(chart: ResolvedChart) -> dict[str, str]:
    """Each project image ``chart``'s text writes, as `project_images` reads it from the
    project in scope: a callout's title and message are its only markdown."""
    if not isinstance(chart, ResolvedCalloutChart):
        return {}
    texts = [chart.message] if chart.title is None else [chart.title, chart.message]
    return {
        ref: url
        for text in texts
        for ref, url in project_images(text, chart.base_dir).items()
    }


def _draw(placement: str, args: CellArgs) -> _engine.RenderedCell:
    """The engine's draw of the chart placed as `placement` in the cell `args`; an
    engine error, a refusal included, raises `ChartDataError`."""
    try:
        return _board().prepared.draw(placement, *args)
    except _engine.RenderDiagnosticError as exc:
        raise chart_data_error(exc) from exc


def draw_in_cell(chart: ResolvedChart, placement: str, args: CellArgs) -> str:
    """The `<svg>` of the chart placed as `placement`, drawn by the engine in the cell
    `args`. Its warnings are recorded; an engine error, a refusal included, raises
    `ChartDataError`."""
    cell = _draw(placement, args)
    record_engine_warnings(
        chart.id,
        args.width,
        cell.diagnostics,
        authored_texts(chart),
        record_warnings=True,
        measuring=False,
    )
    return cell.svg


def measure_in_cell(
    chart: ResolvedChart,
    board_style: ResolvedStyle,
    width: float,
    padding: Mapping[str, float] | None,
) -> float:
    """The height the engine draws ``chart`` at in a cell `width` wide with no height of
    its own, inside the `padding` the slot is shrunk by (`cell_args`): sizing's measure
    of a chart `in_cell`, drawn by its first placement. A sizing probe records no
    warning; an engine error raises `ChartDataError`."""
    args = cell_args(chart, board_style, width, None, padding, NO_TITLE_SHIFT)
    cell = _draw(chart.id, args)
    record_engine_warnings(
        chart.id,
        width,
        cell.diagnostics,
        authored_texts(chart),
        record_warnings=False,
        measuring=True,
    )
    return cell.height
