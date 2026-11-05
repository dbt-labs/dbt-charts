"""find_queries: the boards and queries that answer a question, with what is cached."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from dbt_charts.agent_api.find_queries import (
    FindQueriesArgs,
    find_queries,
    terse_age,
)
from dbt_charts.agent_api.query import BoardFailure, load_board
from dbt_charts.agent_api.search import search_boards_hits
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.constants.find_queries import (
    CACHED_WINDOW_DAYS,
    INLINE_ROW_BUDGET,
    INLINE_ROWS,
    MAX_CACHED_VARIANTS,
)
from dbt_charts.core.execute.adapters import AdapterRegistry, build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.plan import plan
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

from .._results import rows_result

_REVENUE = """title: Revenue by region
notes: Quarterly revenue across regions.
source: db
variables:
  region:
    input: select
    options:
      static: [US, EU, APAC]
    default: US
queries:
  arr:
    notes: "Answers: revenue by region"
    sql: select region, arr from t where region = {{ region }}
  plain:
    sql: select 1 as n
charts:
  c:
    query: arr
    type: table
rows:
  - c
"""

_TICKETS = """title: Open tickets
source: db
queries:
  tix:
    sql: select n from tickets
charts:
  c:
    query: tix
    type: table
rows:
  - c
"""


@pytest.fixture
def project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "rev.yml").write_text(_REVENUE)
    (tmp_path / "charts" / "tickets.yml").write_text(_TICKETS)
    return local_project(tmp_path)


@pytest.fixture
def cache() -> Iterator[TrivialDuckDBCache]:
    cache = TrivialDuckDBCache()
    try:
        yield cache
    finally:
        cache.close()


@pytest.fixture
def registry(project: FilesystemProject) -> AdapterRegistry:
    return build_adapter_registry(project, read_only=False)


Put = Callable[..., tuple[str, str, str]]


@pytest.fixture
def put(
    project: FilesystemProject, registry: AdapterRegistry, cache: TrivialDuckDBCache
) -> Put:
    """Cache rows for *board*'s *query* under the key the executor reads them
    with today, as *writer* (default: that board and query) wrote them *age*
    ago; returns the key."""

    def put(
        age: timedelta,
        region: str | None,
        rows: int = 3,
        board: str = "rev",
        query: str = "arr",
        writer: tuple[str, str] | None = None,
    ) -> tuple[str, str, str]:
        loaded = load_board(Path(f"{board}.yml"), project)
        assert not isinstance(loaded, BoardFailure)
        variables = {"region": region} if region is not None else {}
        executor = Executor(
            loaded.board,
            adapter_registry=registry,
            query_registry=loaded.query_registry,
        )
        [planned] = plan(executor.ctx, [query], variables)
        key = planned.cache_key
        slug, name = writer or (board, query)
        cache.put(
            *key,
            rows_result([{"region": region, "arr": i} for i in range(rows)]),
            board_slug=slug,
            query_name=name,
            variables=variables,
        )
        written = (datetime.now(UTC) - age).replace(tzinfo=None)
        with cache._connection() as conn:
            conn.execute(
                "UPDATE _query_outcomes SET written_at_utc = ? "
                "WHERE source_hash = ? AND query_hash = ? AND variables_hash = ?",
                [written, *cache.listed_key(key)],
            )
        return key

    return put


def test_ranking_follows_the_board_search(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    put: Put,
) -> None:
    result = find_queries(project, cache, registry, "revenue", None)

    ranked = [hit.board_path for hit in search_boards_hits("revenue", project)]
    assert [b.board_path for b in result.boards] == ranked == ["rev.yml"]


def test_a_board_lists_its_variables_and_every_query(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    put: Put,
) -> None:
    [board] = find_queries(project, cache, registry, "revenue", None).boards

    assert (board.file_path, board.title) == ("charts/rev.yml", "Revenue by region")
    assert board.notes == "Quarterly revenue across regions."
    assert board.match_reasons
    [region] = board.variables
    assert (region.name, region.type, region.default) == ("region", "select", "US")
    assert region.options == ["US", "EU", "APAC"]
    arr, plain = board.queries
    assert (arr.name, arr.source, arr.notes) == (
        "arr",
        "db",
        "Answers: revenue by region",
    )
    assert plain.name == "plain"
    assert plain.notes is None


def test_a_small_default_slice_carries_its_rows(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    put: Put,
) -> None:
    """A total or a short breakdown is the answer itself, so the listing hands
    the rows over; a slice past the inline cap, or any non-default slice, is
    left to `read`."""
    put(timedelta(hours=1), "EU", rows=2)
    put(timedelta(minutes=5), "US", rows=3)
    put(timedelta(minutes=1), "APAC", rows=INLINE_ROWS + 1)

    [board] = find_queries(project, cache, registry, "revenue", None).boards
    arr, _plain = board.queries

    us, apac, eu = arr.cached
    assert us.variables == {"region": "US"}
    assert us.data == [
        {"region": "US", "arr": 0},
        {"region": "US", "arr": 1},
        {"region": "US", "arr": 2},
    ]
    assert apac.data is None and eu.data is None


def test_inlined_rows_stay_within_the_listing_budget(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    put: Put,
) -> None:
    (Path(project.root) / "charts" / "big.yml").write_text(
        "title: Revenue totals\nsource: db\nqueries:\n  big:\n    sql: select 1\n"
        "charts:\n  c:\n    query: big\n    type: table\nrows:\n  - c\n"
    )
    put(timedelta(minutes=5), "US", rows=INLINE_ROW_BUDGET - 10)
    put(timedelta(minutes=5), None, rows=20, board="big", query="big")

    boards = find_queries(project, cache, registry, "revenue", None).boards
    data = {
        (b.board_path, q.name): q.cached[0].data
        for b in boards
        for q in b.queries
        if q.cached
    }
    # Whichever board ranks first takes its rows; the other would overrun the
    # budget and is left to `read`.
    sizes = [len(d) if d is not None else None for d in data.values()]
    assert len(sizes) == 2 and sum(s is not None for s in sizes) == 1
    assert all(s is None or s <= INLINE_ROW_BUDGET for s in sizes)


def test_synthetic_queries_are_not_listed(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    put: Put,
) -> None:
    """A chart's inline query and a variable's options query compile under
    names SQL cannot reference, so they are not offered."""
    (Path(project.root) / "charts" / "inline.yml").write_text(
        "title: Inline signups\n"
        "source: db\n"
        "variables:\n"
        "  plan:\n"
        "    input: select\n"
        "    options:\n"
        "      query: select distinct plan from signups\n"
        "queries:\n"
        "  by_month:\n"
        "    sql: select month, n from signups\n"
        "charts:\n"
        "  c:\n"
        "    query:\n"
        "      sql: select 1 as n\n"
        "    type: table\n"
        "rows:\n"
        "  - c\n"
    )
    [board] = find_queries(project, cache, registry, "signups", None).boards

    assert [q.name for q in board.queries] == ["by_month"]


def test_a_query_notes_match_finds_the_board(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    put: Put,
) -> None:
    result = find_queries(project, cache, registry, "answers revenue by region", None)

    assert [b.board_path for b in result.boards] == ["rev.yml"]


def test_a_cached_query_lists_its_variants_default_slice_then_newest_first(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    put: Put,
) -> None:
    put(timedelta(hours=1), "EU", rows=2)
    put(timedelta(days=3), "US", rows=5)
    put(timedelta(minutes=10), "APAC", rows=1)

    [board] = find_queries(project, cache, registry, "revenue", None).boards
    arr, plain = board.queries

    assert [(v.variables, v.age, v.rows) for v in arr.cached] == [
        ({"region": "US"}, "3d", 5),
        ({"region": "APAC"}, "10min", 1),
        ({"region": "EU"}, "1h", 2),
    ]
    assert arr.columns == ["region", "arr"]
    assert arr.older == 0
    # The listing names the call that reads the rows, so the agent needs no
    # second look-up to use them.
    assert arr.read == (
        'execute_sql(board="rev.yml", sql="SELECT * FROM {{ queries.arr.cache }}")'
    )
    assert plain.cached == []
    assert plain.columns is None and plain.read is None


def test_read_names_the_variables_of_a_non_default_slice(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    put: Put,
) -> None:
    """With only another region cached, the call that reads it must pass that
    region; under the board's default it would miss and run on the warehouse."""
    put(timedelta(hours=1), "EU")

    [board] = find_queries(project, cache, registry, "revenue", None).boards
    arr, _plain = board.queries

    assert arr.read == (
        'execute_sql(board="rev.yml", sql="SELECT * FROM {{ queries.arr.cache }}", '
        'variables=[{"name": "region", "value": "EU"}])'
    )


def test_variants_are_limited_to_the_window_and_the_cap_with_an_older_count(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    put: Put,
) -> None:
    inside = MAX_CACHED_VARIANTS + 2
    for i in range(inside):
        put(timedelta(hours=i + 1), f"R{i}")
    put(timedelta(days=CACHED_WINDOW_DAYS + 2), "OLD")

    [board] = find_queries(project, cache, registry, "revenue", None).boards
    [arr, _plain] = board.queries

    assert len(arr.cached) == MAX_CACHED_VARIANTS
    assert [v.variables["region"] for v in arr.cached] == [
        f"R{i}" for i in range(MAX_CACHED_VARIANTS)
    ]
    assert arr.older == 3


def test_another_boards_entries_are_not_listed(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    put: Put,
) -> None:
    put(timedelta(hours=1), None, board="tickets", query="tix")

    [board] = find_queries(project, cache, registry, "revenue", None).boards

    assert board.queries[0].cached == []


def test_an_entry_another_board_read_is_listed_under_the_reading_name(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    put: Put,
) -> None:
    key = put(timedelta(hours=1), "US", writer=("tickets", "tix"))
    cache.note_use(*key, "rev", "arr")

    [board] = find_queries(project, cache, registry, "revenue", None).boards

    assert [v.age for v in board.queries[0].cached] == ["1h"]


def test_without_a_cache_the_result_says_so(
    project: FilesystemProject, registry: AdapterRegistry
) -> None:
    result = find_queries(project, None, registry, "revenue", None)

    assert result.cache == "none"
    assert [q.cached for q in result.boards[0].queries] == [[], []]


def test_with_a_cache_the_result_says_it_is_attached(
    project: FilesystemProject, cache: TrivialDuckDBCache, registry: AdapterRegistry
) -> None:
    assert find_queries(project, cache, registry, "revenue", None).cache == "attached"


def test_rows_cached_under_an_earlier_definition_are_not_listed(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    put: Put,
) -> None:
    put(timedelta(hours=1), None, query="plain")
    rev = Path(project.root) / "charts" / "rev.yml"
    rev.write_text(_REVENUE.replace("select 1 as n", "select 2 as m"))

    [board] = find_queries(project, cache, registry, "revenue", None).boards
    _arr, plain = board.queries

    assert plain.cached == []
    assert plain.columns is None


def test_limit_takes_the_top_boards(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    put: Put,
) -> None:
    both = find_queries(project, cache, registry, "", None)
    one = find_queries(project, cache, registry, "", 1)

    assert len(both.boards) == 2
    assert [b.board_path for b in one.boards] == [both.boards[0].board_path]


def test_a_board_that_does_not_compile_reports_why(
    project: FilesystemProject,
    cache: TrivialDuckDBCache,
    registry: AdapterRegistry,
    tmp_path: Path,
) -> None:
    (tmp_path / "charts" / "broken.yml").write_text(
        "title: Broken revenue\nrows: [nope]\n"
    )

    result = find_queries(project, cache, registry, "broken revenue", None)

    broken = next(b for b in result.boards if b.board_path == "broken.yml")
    assert broken.errors
    assert broken.queries == []


@pytest.mark.parametrize(
    ("seconds", "age"),
    [
        (0, "now"),
        (59, "now"),
        (60, "1min"),
        (3599, "59min"),
        (3600, "1h"),
        (2 * 3600 + 5, "2h"),
        (86400, "1d"),
        (3 * 86400 + 100, "3d"),
        (7 * 86400, "1w"),
        (5 * 7 * 86400 + 3, "5w"),
    ],
)
def test_terse_age(seconds: int, age: str) -> None:
    assert terse_age(seconds) == age


@pytest.mark.parametrize("limit", [0, 21])
def test_limit_is_bounded(limit: int) -> None:
    with pytest.raises(ValidationError):
        FindQueriesArgs.model_validate({"text": "x", "limit": limit})
