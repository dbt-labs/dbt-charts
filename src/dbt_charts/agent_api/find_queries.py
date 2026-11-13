"""Find the boards and queries that answer a question, with what is cached."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts import _engine
from dbt_charts.agent_api.describe import VariableDescription, describe_variables
from dbt_charts.agent_api.query import BoardFailure, load_board
from dbt_charts.agent_api.search import (
    BoardListing,
    BoardMatch,
    brief as _brief,
    search_boards_hits,
)
from dbt_charts.core.board import plan_for_board
from dbt_charts.core.compile.engine import refusal_diagnostics
from dbt_charts.core.compile.models.query.normalized import AnyQuery, SqlQuery
from dbt_charts.core.constants.find_queries import (
    CACHED_WINDOW_DAYS,
    INLINE_ROW_BUDGET,
    INLINE_ROWS,
    MAX_CACHED_VARIANTS,
)
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.cache_backend import (
    BrowsableResultCache,
    CacheEntry,
    CacheUser,
    QueryResultCache,
)
from dbt_charts.core.execute.duckdb_cache import compute_relevant_variables
from dbt_charts.core.execute.env import ExecEnv
from dbt_charts.core.execute.plan import make_context, plan, utc_now
from dbt_charts.core.execute.plan_reader import plan_variables
from dbt_charts.core.project import Project, board_identity
from dbt_charts.core.validate import normalize_data_for_json

DEFAULT_BOARD_LIMIT = 5
MAX_BOARD_LIMIT = 20


class FindQueriesArgs(BaseModel):
    """The first call for a data question: the boards and queries that answer it, ranked, with each query's columns, variables and cached rows (how fresh). A board query's cached rows are its governed answer as of `age` ago: for a question about a period that had closed by then, read them with the query's `read` call (execute_sql over {{ queries.<name>.cache }}, no warehouse call) rather than re-running the same SQL on the warehouse, which only costs more to return the same numbers. Go to the warehouse when the question needs today's data, cuts off before the rows were cached inside a period still open then (to-date counts grow past the cutoff), needs a slice the cached variables do not cover, or a metric no board query produces."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        description="Keywords for the question, matched against board titles, "
        "notes, tags, chart titles and query names and notes. Empty lists boards "
        "in path order."
    )
    limit: int | None = Field(
        None,
        ge=1,
        le=MAX_BOARD_LIMIT,
        description=f"Boards to return (default {DEFAULT_BOARD_LIMIT}, "
        f"max {MAX_BOARD_LIMIT}).",
    )


class CachedSlice(BaseModel):
    """One set of cached rows of a query."""

    model_config = ConfigDict(frozen=True)

    variables: dict[str, Any] = Field(
        description="The variable values the rows were computed under; empty "
        "when the query takes none."
    )
    age: str = Field(
        description="Time since the rows were written: now, 5min, 2h, 3d, 5w."
    )
    rows: int
    truncated: bool = Field(
        description="True when the rows were cut at the render's row or byte "
        "ceiling, so a total over them is not the query's total."
    )
    data: list[dict[str, Any]] | None = Field(
        default=None,
        description=f"The rows themselves when the slice holds at most "
        f"{INLINE_ROWS}: a total or a short breakdown is the answer, read it "
        "here. Absent for larger slices; `read` returns those.",
    )


class QueryAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Pass as {{ queries.<name>.cache }} in execute_sql.")
    notes: str | None = None
    source: str | None = None
    columns: list[str] | None = Field(
        default=None, description="Absent when the query has never been cached."
    )
    cached: list[CachedSlice] = Field(
        description=f"Cached slices from the last {CACHED_WINDOW_DAYS} days, the "
        f"board's default variables first, then newest first; at most "
        f"{MAX_CACHED_VARIANTS}."
    )
    older: int = Field(
        description="Cached slices beyond those listed, because of the window or the cap."
    )
    read: str | None = Field(
        default=None,
        description="The execute_sql call that returns the first listed slice's "
        "cached rows with no warehouse query; absent when nothing is cached.",
    )


class BoardAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    board_path: str = Field(description="Pass as execute_sql's `board`.")
    file_path: str
    title: str
    notes: str
    match_reasons: list[str]
    variables: list[VariableDescription]
    queries: list[QueryAnswer]
    errors: list[str] = Field(
        default_factory=list,
        description="Why the board's queries could not be listed; it does not compile.",
    )


class FindQueriesResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    cache: Literal["attached", "not_browsable", "none"] = Field(
        description=(
            "Whether cached rows can be listed: 'none' means the server runs with no "
            "result cache (DCT_CACHE_PATH attaches one) and 'not_browsable' means its "
            "cache cannot be listed, so an empty `cached` says nothing about what was "
            "cached."
        )
    )
    boards: list[BoardAnswer]


def terse_age(seconds: int) -> str:
    """``seconds`` as the largest whole unit: now, 5min, 2h, 3d, 5w.

    Minutes are spelled ``min``: a bare ``m`` reads as months.
    """
    for unit, size in (("w", 7 * 86400), ("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return "now"


def _json_normal(values: dict[str, Any]) -> dict[str, Any]:
    """*values* as the cache stores them, so a default compares equal to a stored slice."""
    return json.loads(json.dumps(values, sort_keys=True, default=str))


class _InlineBudget:
    """Rows one listing may still inline; shared by every slice it builds."""

    def __init__(self) -> None:
        self.left = INLINE_ROW_BUDGET

    def take(self, rows: int) -> bool:
        if rows > INLINE_ROWS or rows > self.left:
            return False
        self.left -= rows
        return True


def _inline_rows(
    cache: BrowsableResultCache | None, entry: CacheEntry, budget: _InlineBudget
) -> list[dict[str, Any]] | None:
    if cache is None or not budget.take(entry.row_count):
        return None
    hit = cache.read(entry.key)
    return normalize_data_for_json(hit.rows) if hit is not None else None


def _query_answer(
    name: str,
    query: AnyQuery,
    slug: str,
    board_path: str,
    cache: BrowsableResultCache | None,
    entries: list[CacheEntry],
    current: tuple[str, str] | None,
    default_variables: dict[str, Any],
    now: datetime,
    budget: _InlineBudget,
) -> QueryAnswer:
    user = CacheUser(slug, name)
    # An entry written under an earlier definition of the query (or its source)
    # holds another query's rows. A query composed over `.cache` refs folds its
    # upstream keys, variables included, into the source hash, so only its
    # default slice can match.
    mine = [e for e in entries if user in e.used_by and e.key[:2] == current]
    window_start = now - timedelta(days=CACHED_WINDOW_DAYS)
    default_slice = _json_normal(compute_relevant_variables(query, default_variables))
    # sorted() is stable: the default slice moves up, the rest stay newest first.
    recent = sorted(
        (e for e in mine if e.written_at >= window_start),
        key=lambda e: e.variables != default_slice,
    )[:MAX_CACHED_VARIANTS]
    return QueryAnswer(
        name=name,
        notes=_brief(query.notes) if query.notes else None,
        source=query.source if isinstance(query, SqlQuery) else None,
        columns=mine[0].columns if mine else None,
        cached=[
            CachedSlice(
                variables=e.variables,
                age=terse_age(max(0, int((now - e.written_at).total_seconds()))),
                rows=e.row_count,
                truncated=e.truncated_reason is not None,
                # Only the first slice (the board's default variables when
                # cached) carries its rows: that is the one a question with no
                # filter of its own is answered from.
                data=_inline_rows(cache, e, budget) if i == 0 else None,
            )
            for i, e in enumerate(recent)
        ],
        older=len(mine) - len(recent),
        read=_read_call(board_path, name, recent[0].variables, default_slice)
        if recent
        else None,
    )


def _read_call(
    board_path: str,
    name: str,
    variables: dict[str, Any],
    default_slice: dict[str, Any],
) -> str:
    """The execute_sql call reading the first listed slice; one cached under
    other values than the board's defaults passes them, or the read would miss."""
    call = (
        f'execute_sql(board="{board_path}", '
        f'sql="SELECT * FROM {{{{ queries.{name}.cache }}}}"'
    )
    if variables != default_slice:
        pairs = [{"name": k, "value": v} for k, v in variables.items()]
        call += f", variables={json.dumps(pairs)}"
    return call + ")"


def _board_answer(
    hit: BoardMatch | BoardListing,
    project: Project,
    env: ExecEnv,
    cache: BrowsableResultCache | None,
    entries: list[CacheEntry],
    budget: _InlineBudget,
) -> BoardAnswer:
    shown = {
        "board_path": hit.board_path,
        "file_path": hit.file_path,
        "title": hit.title,
        "notes": hit.summary,
        "match_reasons": hit.match_reasons if isinstance(hit, BoardMatch) else [],
    }
    loaded = load_board(Path(hit.board_path), project)
    if isinstance(loaded, BoardFailure):
        return BoardAnswer(**shown, variables=[], queries=[], errors=loaded.errors)
    board = loaded.board
    now = datetime.now(UTC)
    try:
        board_plan = plan_for_board(
            loaded.board_file,
            project,
            {},
            None,
            utc_now(),
            strict=True,
            active_tab_only=False,
            all_queries=True,
        )
    except _engine.CompileError as refusal:
        errors = [d.message for d in refusal_diagnostics(refusal, hit.file_path)]
        return BoardAnswer(**shown, variables=[], queries=[], errors=errors)
    except ExecutionError as exc:
        return BoardAnswer(**shown, variables=[], queries=[], errors=[str(exc)])
    defaults = plan_variables(board_plan)
    slug = board_identity(hit.file_path)
    # Synthetic queries (`_inline_query_*`, `_var_options_*`) have no
    # `{{ queries.<name> }}` form, so SQL could not reach them.
    named = [(n, q) for n, q in loaded.all_queries.items() if not n.startswith("_")]
    # With no cache there are no entries, so no key to match them against.
    current: dict[str, tuple[str, str]] = {}
    if cache is not None:
        ctx = make_context(board, board_plan, env)
        try:
            # A query nothing places or reads, a values one, is not in the plan and has no key.
            current = {
                planned.name: cache.listed_key(planned.cache_key)[:2]
                for planned in plan(
                    ctx, [name for name, _ in named if name in board_plan["queries"]]
                )
            }
        except ExecutionError as exc:
            return BoardAnswer(**shown, variables=[], queries=[], errors=[str(exc)])
    return BoardAnswer(
        **shown,
        variables=describe_variables(board.variables),
        queries=[
            _query_answer(
                name,
                query,
                slug,
                hit.board_path,
                cache,
                entries,
                current.get(name),
                defaults,
                now,
                budget,
            )
            for name, query in named
        ],
    )


def find_queries(
    project: Project,
    cache: QueryResultCache | None,
    env: ExecEnv,
    text: str,
    limit: int | None,
) -> FindQueriesResult:
    """The best-matching boards for *text*, each with its queries and cached slices.

    Ranking is the board search's. A cache that cannot be listed by board and
    query (or no cache) leaves every ``cached`` empty, and ``cache`` says so.
    """
    hits = search_boards_hits(text, project)[
        : DEFAULT_BOARD_LIMIT if limit is None else limit
    ]
    browsable = cache if isinstance(cache, BrowsableResultCache) else None
    entries = browsable.entries() if browsable is not None else []
    budget = _InlineBudget()
    return FindQueriesResult(
        cache=(
            "none"
            if cache is None
            else "attached"
            if browsable is not None
            else "not_browsable"
        ),
        boards=[
            _board_answer(hit, project, env, browsable, entries, budget) for hit in hits
        ],
    )
