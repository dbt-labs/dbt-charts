"""Cache cascade wiring through the authored surfaces and normalize_query.

Pins the four-scope resolution (project config root → source → board → query)
stamping a resolved CachePolicy on normalized queries, plus the authored
`cache:` field on queries, sources, and boards.
"""

from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import CompileResult, compile_file
from dbt_charts.core.compile.config import get_config, reset_config
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.cache import CachePatch, CachePolicy
from dbt_charts.core.compile.models.query.authored import AuthoredQuery
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import parse_source_config
from dbt_charts.core.compile.normalize.dispatch import normalize_board
from dbt_charts.core.compile.normalize.queries import normalize_query
from dbt_charts.core.compile.parse.parser import parse_yaml

from .conftest import cache_layer, compile_with_board_sources

_SOURCES: dict[str, Any] = {
    "db": {"type": "duckdb", "path": "warehouse.duckdb"},
    "cached_db": {
        "type": "duckdb",
        "path": "warehouse.duckdb",
        "cache": {"ttl": "1h"},
    },
}


def _root_ttl() -> str | None:
    """The shipped/project cascade-root ttl, read from config (never pinned)."""
    return get_config().cache.ttl  # type: ignore[union-attr]


# ── authored surfaces ────────────────────────────────────────────────────────


def test_authored_query_accepts_cache_block() -> None:
    query = TypeAdapter(AuthoredQuery).validate_python(
        {"type": "sql", "sql": "SELECT 1", "cache": {"ttl": "5m"}}
    )
    assert isinstance(query.cache, CachePatch)


@pytest.mark.parametrize(
    ("authored", "enabled", "ttl"),
    [(False, False, None), (True, True, None), ("5m", True, "5m")],
)
def test_authored_query_accepts_scalarcache_layer(
    authored: bool | str, enabled: bool, ttl: str | None
) -> None:
    query = TypeAdapter(AuthoredQuery).validate_python(
        {"type": "sql", "sql": "SELECT 1", "cache": authored}
    )
    assert isinstance(query.cache, CachePatch)
    assert query.cache.enabled is enabled
    assert query.cache.ttl == ttl


def test_authored_query_rejects_malformed_cache_block() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(AuthoredQuery).validate_python(
            {
                "type": "sql",
                "sql": "SELECT 1",
                "cache": {"ttl": "1 fortnight"},
            }
        )


def test_source_config_accepts_cache_block() -> None:
    config = parse_source_config(dict(_SOURCES["cached_db"]))
    assert isinstance(config.cache, CachePatch)
    assert config.cache.ttl == "1h"


def test_source_config_accepts_scalarcache_layer() -> None:
    config = parse_source_config(
        {"type": "duckdb", "path": "warehouse.duckdb", "cache": False}
    )
    assert config.cache is not None
    assert config.cache.enabled is False


# ── normalized model: fail-safe NEVER_CACHED default ─────────────────────────


def test_normalized_query_defaults_to_never_cached() -> None:
    """A direct constructor that never runs the cascade (tests, a future
    lowering path that forgets to stamp) gets NEVER_CACHED, not a permissive
    always-cache default — a missed stamp fails safe (no caching, a visible
    perf cost), never silently caches forever."""
    from dbt_charts.core.compile.models.cache import NEVER_CACHED

    query = SqlQuery(sql="SELECT 1", source="db")
    assert query.cache is NEVER_CACHED


def test_normalized_query_rejects_boolcache_layer() -> None:
    """The bool-coercing convenience is gone along with the authored `cache:
    false` shorthand above — a direct constructor needs an explicit
    CachePolicy, never a bare bool."""
    with pytest.raises(ValidationError):
        SqlQuery(sql="SELECT 1", source="db", cache=False)


# ── cascade through normalize_query ──────────────────────────────────────────


def test_normalize_stamps_root_policy_when_nothing_authored() -> None:
    query = normalize_query(
        "q", {"sql": "SELECT 1", "source": "db"}, sources=dict(_SOURCES)
    )
    assert isinstance(query.cache, CachePolicy)
    assert query.cache.enabled is True
    assert query.cache.ttl == _root_ttl()


def test_query_cache_false_disables_but_inherits_root_ttl() -> None:
    query = normalize_query(
        "q",
        {"sql": "SELECT 1", "source": "db", "cache": False},
        sources=dict(_SOURCES),
    )
    assert query.cache.enabled is False
    assert query.cache.ttl == _root_ttl()


def test_query_ttl_overrides_root() -> None:
    query = normalize_query(
        "q",
        {"sql": "SELECT 1", "source": "db", "cache": {"ttl": "5m"}},
        sources=dict(_SOURCES),
    )
    assert query.cache.enabled is True
    assert query.cache.ttl == "5m"


def test_source_cache_layer_is_inherited() -> None:
    query = normalize_query(
        "q", {"sql": "SELECT 1", "source": "cached_db"}, sources=dict(_SOURCES)
    )
    assert query.cache.ttl == "1h"


def test_query_scalar_cache_ttl_overrides_root() -> None:
    query = normalize_query(
        "q",
        {"sql": "SELECT 1", "source": "db", "cache": "5m"},
        sources=dict(_SOURCES),
    )
    assert query.cache.enabled is True
    assert query.cache.ttl == "5m"


@pytest.mark.parametrize("authored", [5, ["1h"], {"ttl": "1 fortnight"}])
def test_query_cache_rejects_unauthorable_values(authored: object) -> None:
    """The raw-dict authoring path (`_cache_layer`) is the same enforcement
    point as the pydantic-model authored fields: a duration must be a string
    with a unit, and a bare number, a list, or a malformed duration is a
    compile error — never a silently dropped cache directive."""

    with pytest.raises(CompilationError):
        normalize_query(
            "q",
            {"sql": "SELECT 1", "source": "db", "cache": authored},
            sources=dict(_SOURCES),
        )


def test_prebuilt_normalized_query_keeps_its_policy() -> None:
    disabled = CachePolicy(enabled=False)
    prebuilt = SqlQuery(sql="SELECT 1", source="db", cache=disabled)
    result = normalize_query("q", prebuilt, sources=dict(_SOURCES))
    assert result.cache.enabled is False


# ── project scope: the scalar expands before the config merge ───────────────


def test_project_scalar_cache_replaces_the_shipped_root(
    tmp_path: Any, local_project: Any
) -> None:
    """A project scalar states the whole root policy, so it replaces it."""
    from dbt_charts.core.compile.config import load_config, reset_config

    reset_config()
    (tmp_path / "dbt_charts.yml").write_text("cache: 37m\n")
    try:
        config = load_config(local_project(tmp_path))
        assert config.cache.ttl == "37m"
        assert config.cache.enabled is True
    finally:
        reset_config()


def test_project_cache_block_keeps_the_shipped_ttl(
    tmp_path: Any, local_project: Any
) -> None:
    """Setting `path` — the documented reason to write the block form — must
    not silently turn caching into cache-forever.

    `deep_merge_dict` recurses only where both sides are mappings, so a scalar
    shipped root would be replaced wholesale by this block: `ttl` unset, which
    resolves to "never auto-expire". Nothing in the project's YAML said that.
    """
    from dbt_charts.core.compile.config import load_config, reset_config

    reset_config()
    (tmp_path / "dbt_charts.yml").write_text("cache:\n  path: .dbt-cache.duckdb\n")
    try:
        config = load_config(local_project(tmp_path))
        assert config.cache.path == ".dbt-cache.duckdb"
        assert config.cache.ttl == _root_ttl()
        assert config.cache.enabled is True
    finally:
        reset_config()


def test_project_empty_cache_block_authors_nothing(
    tmp_path: Any, local_project: Any
) -> None:
    """`cache: {}` says nothing, so the shipped root stands — it must not
    resolve to a bare `ValueError` about the cascade root not setting
    `enabled`, which is a key the author cannot write anyway."""
    from dbt_charts.core.compile.config import load_config, reset_config

    reset_config()
    (tmp_path / "dbt_charts.yml").write_text("cache: {}\n")
    try:
        config = load_config(local_project(tmp_path))
        assert config.cache.enabled is True
        assert config.cache.ttl == _root_ttl()
    finally:
        reset_config()


def test_project_cache_true_is_rejected(tmp_path: Any, local_project: Any) -> None:
    """`cache: true` is the one scalar the project root cannot honor.

    Everywhere else it means "on, keeping the ttl from above"; the root has
    nothing above it, and a scalar replaces the shipped block wholesale, so it
    would resolve to caching *forever* — the opposite of the conservative
    reading, and a state nothing in the project's YAML asked for.
    """
    from dbt_charts.core.compile.config import load_config, reset_config

    reset_config()
    (tmp_path / "dbt_charts.yml").write_text("cache: true\n")
    try:
        with pytest.raises(ValidationError, match="forever"):
            load_config(local_project(tmp_path))
    finally:
        reset_config()


def test_project_root_cache_false_disables_a_query(tmp_path: Any) -> None:
    """The root layer, read off `dbt_charts.yml` and handed to `normalize_query`
    the way the compiler threads it — no process-global config in the loop."""
    from dbt_charts.core.compile.config import get_project_cache_root

    (tmp_path / "dbt_charts.yml").write_text("cache: false\n")
    root = get_project_cache_root(FilesystemProject(tmp_path))
    query = normalize_query(
        "q",
        {"sql": "SELECT 1", "source": "db"},
        sources=dict(_SOURCES),
        cache_root=root,
    )
    assert query.cache.enabled is False


# ── board scope: the top-level `cache:` layer ────────────────────────────────


def _board(body: str) -> Any:
    """Compile a one-chart board, asserting success, and return the board."""
    result = compile_with_board_sources(body)
    assert result.success, f"Compilation failed: {result.errors}"
    return result.board


def test_board_cache_applies_to_named_queries() -> None:
    board = _board(
        """
        title: T
        cache: 5m
        queries:
          main: {sql: "SELECT 1", source: db}
        charts:
          c1: {query: main, type: kpi, value: value}
        rows: [c1]
        """
    )
    assert board.queries["main"].cache.ttl == "5m"


def test_board_cache_false_beats_a_caching_source() -> None:
    """A dashboard-level opt-out has to win over a source that sets a ttl —
    otherwise `cache: false` on the board is useless on any cached source."""
    board = _board(
        """
        title: T
        cache: false
        sources:
          cached_db: {type: duckdb, path: warehouse.duckdb, cache: {ttl: 1h}}
        queries:
          main: {sql: "SELECT 1", source: cached_db}
        charts:
          c1: {query: main, type: kpi, value: value}
        rows: [c1]
        """
    )
    assert board.queries["main"].cache.enabled is False


def test_query_cache_overrides_the_board_layer() -> None:
    board = _board(
        """
        title: T
        cache: 1h
        queries:
          main: {sql: "SELECT 1", source: db, cache: 5m}
        charts:
          c1: {query: main, type: kpi, value: value}
        rows: [c1]
        """
    )
    assert board.queries["main"].cache.ttl == "5m"


@pytest.mark.parametrize("scope", ["board", "query"])
def test_scalar_and_block_spellings_resolve_identically(scope: str) -> None:
    """`cache: 5m` is pure sugar for `cache: {ttl: 5m}` — same resolved policy.

    Checked under a parent that already set a ttl, because that is where sugar
    could plausibly diverge: the scalar must refine the inherited layer
    field-by-field exactly as the block does, not replace it.
    """

    def compiled(spelling: str) -> Any:
        return _board(
            f"""
        title: T
        cache: {spelling if scope == "board" else "true"}
        sources:
          cached_db: {{type: duckdb, path: warehouse.duckdb, cache: {{ttl: 1h}}}}
        queries:
          main:
            sql: "SELECT 1"
            source: cached_db
            cache: {spelling if scope == "query" else "true"}
        charts:
          c1: {{query: main, type: kpi, value: value}}
        rows: [c1]
        """
        )

    assert (
        compiled("5m").queries["main"].cache
        == compiled("{ttl: 5m}").queries["main"].cache
    )
    assert compiled("5m").queries["main"].cache.ttl == "5m"


def test_blank_board_still_inherits_the_source_layer() -> None:
    """The board layer only refines: authoring no `cache:` leaves the source's
    own ttl in force rather than shadowing it with the project root."""
    board = _board(
        """
        title: T
        sources:
          cached_db: {type: duckdb, path: warehouse.duckdb, cache: {ttl: 1h}}
        queries:
          main: {sql: "SELECT 1", source: cached_db}
        charts:
          c1: {query: main, type: kpi, value: value}
        rows: [c1]
        """
    )
    assert board.queries["main"].cache.ttl == "1h"


def test_board_cache_reaches_inline_chart_and_variable_queries() -> None:
    """Named, inline-on-a-chart, and inline-on-a-variable queries all sit at the
    same scope, so one `cache:` covers them. A per-lane miss shows up here as a
    query still on the root ttl. (The two cross-file import lanes need real
    files on disk; they are covered in the `compile_file` section below.)"""
    result = compile_with_board_sources(
        """
        title: T
        cache: 5m
        source: db
        variables:
          category:
            input: select
            options:
              query: "SELECT DISTINCT category FROM products"
        queries:
          main: {sql: "SELECT 1", source: db}
        charts:
          c1: {query: main, type: kpi, value: value}
          c2:
            query: {sql: "SELECT 2 AS value", source: db}
            type: kpi
            value: value
        rows: [c1, c2]
        """
    )
    assert result.success, f"Compilation failed: {result.errors}"
    assert len(result.query_registry) == 3, sorted(result.query_registry)
    for name, query in result.query_registry.items():
        assert query.cache.ttl == "5m", f"{name} missed the board cache layer"


def _nested_board(board: Any) -> Any:
    """The board nested in the first layout slot (narrowed for the type checker)."""
    nested = board.layout.items[0].board
    assert nested is not None, "expected a nested board in the first layout slot"
    return nested


def test_nested_board_inherits_the_parent_boardcache_layer() -> None:
    """A `cache:` on the outer dashboard covers the boards composed inside it."""
    board = _board(
        """
        title: T
        cache: 5m
        rows:
          - queries:
              nested_q: {sql: "SELECT 1", source: db}
            charts:
              nc: {query: nested_q, type: kpi, value: value}
            rows: [nc]
        """
    )
    nested = _nested_board(board)
    assert nested.queries["nested_q"].cache.ttl == "5m"


def test_nested_board_cache_overrides_the_parent() -> None:
    """Nearest authored scope wins, so a composed board can refine what it inherits."""
    board = _board(
        """
        title: T
        cache: 5m
        rows:
          - cache: 1h
            queries:
              nested_q: {sql: "SELECT 1", source: db}
            charts:
              nc: {query: nested_q, type: kpi, value: value}
            rows: [nc]
        """
    )
    nested = _nested_board(board)
    assert nested.queries["nested_q"].cache.ttl == "1h"


@pytest.mark.parametrize("authored", [False, True, "4h", "forever", {"ttl": "4h"}])
def test_board_scope_accepts_the_same_authoring_forms(authored: object) -> None:
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    parsed = AuthoredBoard.model_validate({"title": "T", "cache": authored})
    assert isinstance(parsed.cache, CachePatch)


# ── synthetic variable-option queries inherit the source cache layer ────────


def test_synthetic_option_query_inherits_source_cache_layer() -> None:
    """A variable's inline `options.query` SQL string is promoted to a
    synthetic query with no board/query cascade context of its own — but it
    DOES have a source (the default source), so that source's `cache:`
    layer must still apply, not just the project root."""
    result = compile_with_board_sources(
        """
        title: T
        source: cached_db
        sources:
          cached_db:
            type: duckdb
            path: warehouse.duckdb
            cache:
              ttl: 1h
        variables:
          category:
            input: select
            options:
              query: "SELECT DISTINCT category FROM products"
        queries:
          main:
            sql: "SELECT 1"
            source: cached_db
        charts:
          c1: {query: main, type: kpi, value: value}
        rows: [c1]
        """
    )
    assert result.success, f"Compilation failed: {result.errors}"
    synthetic = result.board.queries["_var_options_category"]
    assert synthetic.cache.ttl == "1h"


def test_synthetic_var_query_inherits_source_cache_layer() -> None:
    """A variable's inline `query:` (an AuthoredQuery mapping, not the bare-SQL
    `options.query` string) goes through the sibling promotion branch —
    it must inherit the source cache layer too, not just the project root."""
    result = compile_with_board_sources(
        """
        title: T
        source: cached_db
        sources:
          cached_db:
            type: duckdb
            path: warehouse.duckdb
            cache:
              ttl: 1h
        variables:
          category:
            input: select
            query:
              sql: "SELECT DISTINCT category FROM products"
              source: cached_db
        queries:
          main:
            sql: "SELECT 1"
            source: cached_db
        charts:
          c1: {query: main, type: kpi, value: value}
        rows: [c1]
        """
    )
    assert result.success, f"Compilation failed: {result.errors}"
    synthetic = result.board.queries["_var_query_category"]
    assert synthetic.cache.ttl == "1h"


# ── the production path: compile_file, meta pass included ────────────────────
#
# Everything above compiles a YAML *string*, which skips the meta/extends pass.
# Every CLI verb, `dct serve`, and Cloud go through `compile_file`, which
# validates the whole board as a `BoardPatch` and round-trips it through a
# `model_dump()` before normalizing. Both of those steps have eaten authored
# cache state before; these compile real files on disk so they can't again.


def _compile_board(
    tmp_path: Path,
    board_yaml: str,
    meta_yaml: str | None = None,
    project_yaml: str = "",
) -> CompileResult:
    """Compile `charts/board.yml` the way every real invocation does.

    `project_yaml` is appended to `dbt_charts.yml` verbatim, so a caller can aim
    the cascade *root* at a query through the production entrypoint — no
    `load_config` anywhere, because no production surface except `dct serve`
    calls it.
    """
    reset_config()
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: warehouse.duckdb\n" + project_yaml
    )
    boards = tmp_path / "charts"
    boards.mkdir(exist_ok=True)
    if meta_yaml is not None:
        (boards / "meta.yml").write_text(dedent(meta_yaml))
    (boards / "board.yml").write_text(dedent(board_yaml))
    project = FilesystemProject(tmp_path)
    result = compile_file(project.path("charts/board.yml").read_board())
    assert result.success, f"Compilation failed: {result.errors}"
    return result


def _compiled_querycache_layer(
    tmp_path: Path,
    board_yaml: str,
    meta_yaml: str | None = None,
    project_yaml: str = "",
) -> CachePolicy:
    """The policy on the board's `main` query — the usual single assertion."""
    board = _compile_board(tmp_path, board_yaml, meta_yaml, project_yaml).board
    return board.queries["main"].cache


_BOARD = """
    title: T
    {board_cache}
    queries:
      main:
        sql: "SELECT 1 AS value"
        source: db
        {query_cache}
    charts:
      c1: {{query: main, type: kpi, value: value}}
    rows: [c1]
    """


_ROOT_TTL = object()
"""Stands for "whatever the cascade root ships" — the spellings that narrow
only `enabled` leave `ttl` inherited, and pinning the root's literal value here
would break on any change to `default_config.yml`."""


@pytest.mark.parametrize(
    ("authored", "enabled", "ttl"),
    [
        ("1h", True, "1h"),
        ("forever", True, None),
        ("true", True, _ROOT_TTL),
        ("false", False, _ROOT_TTL),
    ],
)
@pytest.mark.parametrize("scope", ["board", "query"])
def test_scalar_cache_survives_a_real_compile(
    tmp_path: Path, scope: str, authored: str, enabled: bool, ttl: object
) -> None:
    """Each scalar spelling at each board-cascade scope, through `compile_file`.

    The point is that all four spellings reach the executor at all — before
    this, the board scope rejected every one of them and the query scope dropped
    `false` on the floor.
    """
    policy = _compiled_querycache_layer(
        tmp_path,
        _BOARD.format(
            board_cache=f"cache: {authored}" if scope == "board" else "",
            query_cache=f"cache: {authored}" if scope == "query" else "",
        ),
    )
    assert policy.enabled is enabled
    assert policy.ttl == (_root_ttl() if ttl is _ROOT_TTL else ttl)


def test_query_cache_false_survives_a_caching_root(tmp_path: Path) -> None:
    """The opt-out is the one that must never be silently dropped.

    Every layer above says "cache for 24h"; the query says no. Losing this is
    not a missing optimization — it serves day-old numbers to someone who
    explicitly asked for live ones.
    """
    policy = _compiled_querycache_layer(
        tmp_path, _BOARD.format(board_cache="cache: 24h", query_cache="cache: false")
    )
    assert policy.enabled is False


def test_project_root_ttl_reaches_the_query_without_load_config(
    tmp_path: Path,
) -> None:
    """The cascade root has to be threaded, not read off the process global.

    `load_config` is the only thing that merges `dbt_charts.yml` into `_config`,
    and it has one production call site (`dct serve`). `dct render`, `dct mcp
    serve`, `dct query`, and every Cloud request compile without it, so a root
    the cascade reads globally is a root three of four surfaces ignore. Calling
    `load_config` from those surfaces is not the fix — `_config` is a process
    global and one Cloud worker serves many orgs.
    """
    policy = _compiled_querycache_layer(
        tmp_path,
        _BOARD.format(board_cache="", query_cache=""),
        project_yaml="cache: 37m\n",
    )
    assert policy.enabled is True
    assert policy.ttl == "37m"


def test_project_cache_false_reaches_the_query_without_load_config(
    tmp_path: Path,
) -> None:
    """The root opt-out is the one that inverts when the root goes missing.

    A project that writes `cache: false` to keep a PII board off disk resolved
    to the shipped 24h root instead — caching turned *on* by the act of asking
    for it to be off.
    """
    policy = _compiled_querycache_layer(
        tmp_path,
        _BOARD.format(board_cache="", query_cache=""),
        project_yaml="cache: false\n",
    )
    assert policy.enabled is False


def test_project_root_reaches_a_synthetic_variable_query(tmp_path: Path) -> None:
    """Compiler-generated queries root at the same place authored ones do.

    `promote_inline_option_queries` builds a query no author wrote, and
    `Query.cache` defaults to NEVER_CACHED — so a root that never arrives here
    shows up as a dropdown that re-queries the warehouse on every render.
    """
    result = _compile_board(
        tmp_path,
        """
        title: T
        source: db
        variables:
          category:
            input: select
            query:
              sql: "SELECT DISTINCT category FROM t"
              source: db
        queries:
          main: {sql: "SELECT 1 AS value", source: db}
        charts:
          c1: {query: main, type: kpi, value: value}
        rows: [c1]
        """,
        project_yaml="cache: 37m\n",
    )
    assert result.board.queries["_var_query_category"].cache.ttl == "37m"


def test_every_query_type_carries_the_cascaded_policy(tmp_path: Path) -> None:
    """A distinctive project-root ttl, unlikely to appear by coincidence,
    propagated onto every query type a real board can declare (sql/values/
    http/schema — with values_q actually referenced by a chart). Catches a
    stamping miss for any known query type: NEVER_CACHED's ttl is None, so a
    missed stamp would show up here as `ttl != "37m"`, not as an exception."""
    result = _compile_board(
        tmp_path,
        """
        title: T
        queries:
          sql_q: {sql: "SELECT 1", source: db}
          values_q: {type: values, rows: [{a: 1}]}
          http_q: {type: http, url: "https://example.com/data"}
          schema_q: {type: schema, source: db}
        charts:
          c1: {query: sql_q, type: kpi, value: value}
          c2: {query: values_q, type: table}
        rows: [c1, c2]
        """,
        project_yaml="cache: 37m\n",
    )
    assert set(result.board.queries) == {"sql_q", "values_q", "http_q", "schema_q"}
    for query in result.board.queries.values():
        assert query.cache.ttl == "37m"


def test_schema_query_honors_the_source_scope() -> None:
    """A source-level opt-out is the strongest "always live" signal there is.

    `SchemaQuery` carries a populated `source` and runs through the same
    executor cache path as `SqlQuery`, so scoping the source layer to SQL
    queries drops a documented cascade scope for it. (`HttpQuery` and
    `ValuesQuery` have no source and are correctly excluded.)
    """
    sources: dict[str, Any] = {
        "live_db": {"type": "duckdb", "path": "warehouse.duckdb", "cache": False}
    }
    query = normalize_query(
        "cols", {"type": "schema", "source": "live_db"}, sources=sources
    )
    assert query.cache.enabled is False


def test_board_cache_in_meta_yaml_reaches_the_query(tmp_path: Path) -> None:
    """A directory-wide default: `cache:` in `charts/meta.yml`.

    This is the layer the string-compile helper cannot reach at all, and the
    one an author would use to say "this whole folder is hourly".
    """
    policy = _compiled_querycache_layer(
        tmp_path,
        _BOARD.format(board_cache="", query_cache=""),
        meta_yaml="cache: 30m\n",
    )
    assert policy.enabled is True
    assert policy.ttl == "30m"


def test_board_cache_beats_meta_yaml(tmp_path: Path) -> None:
    """Nearest scope wins: the board's own `cache:` refines the directory's."""
    policy = _compiled_querycache_layer(
        tmp_path,
        _BOARD.format(board_cache="cache: 5m", query_cache=""),
        meta_yaml="cache: 30m\n",
    )
    assert policy.ttl == "5m"


def test_board_cache_true_keeps_the_meta_ttl(tmp_path: Path) -> None:
    """`cache: true` means "on, keeping the ttl from above" at every scope.

    It is the one spelling that sets `enabled` and nothing else, so it is the
    only place a whole-layer replacement of the meta block is observable — and
    replacing it there would inflate 5m to the project root's ttl while the
    docs promise "anything you leave out is inherited". The source scope, which
    merges field-by-field, has always read it this way.
    """
    policy = _compiled_querycache_layer(
        tmp_path,
        _BOARD.format(board_cache="cache: true", query_cache=""),
        meta_yaml="cache: 5m\n",
    )
    assert policy.enabled is True
    assert policy.ttl == "5m"


def test_a_board_opt_out_keeps_the_meta_ttl_for_a_query_that_opts_back_in(
    tmp_path: Path,
) -> None:
    """`meta: 1h` → `board: false` → `query: true` resolves to on at 1h.

    The board's `false` says nothing about ttl, so the directory's 1h survives
    into the layer a query can turn back on — the same field-by-field rule
    `merge_cache_layers` applies to the source and query scopes.
    """
    policy = _compiled_querycache_layer(
        tmp_path,
        _BOARD.format(board_cache="cache: false", query_cache="cache: true"),
        meta_yaml="cache: 1h\n",
    )
    assert policy.enabled is True
    assert policy.ttl == "1h"


def test_normalize_board_builds_its_own_registry_with_the_inherited_layer() -> None:
    """`normalize_board` given no registry has to build one that still inherits.

    The registry-building branch exists for direct calls like this one, and a
    direct call is exactly where a board arrives with an inherited layer in its
    parent context. Dropping it there would make inheritance look broken from
    every test written against this entry point.
    """
    board = parse_yaml(
        dedent(
            """
            title: T
            queries:
              main: {sql: "SELECT 1", source: db}
            charts:
              c1: {query: main, type: kpi, value: value}
            rows: [c1]
            """
        )
    )
    compiled = normalize_board(board, parent_context={"board_cache": cache_layer("1h")})
    assert compiled.queries["main"].cache.ttl == "1h"


def test_an_empty_board_cache_block_does_not_replace_the_meta_one(
    tmp_path: Path,
) -> None:
    """Whole-layer replacement is about a value the nearer file authored.

    `cache: {}` authors none — the block is what's left after deleting a ttl,
    or the start of one the author never finished. Counting it as a
    replacement turns a no-op edit into a silent jump from the directory's ttl
    to the project root's.
    """
    policy = _compiled_querycache_layer(
        tmp_path,
        _BOARD.format(board_cache="cache: {}", query_cache=""),
        meta_yaml="cache: 1h\n",
    )
    assert policy.ttl == "1h"


def _imported_querycache_layer(
    tmp_path: Path,
    board_yaml: str,
    query_key: str,
    *,
    source_cache: str = "",
) -> CachePolicy:
    """Compile a board that pulls `totals` out of the sibling `shared.yml`.

    `source_cache` is an authored scalar for the `db` source's own `cache:`
    (empty = the source authors none), so a caller can aim one cascade layer at
    a time and see which lanes it survives.
    """
    reset_config()
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: warehouse.duckdb\n"
        + (f"    cache: {source_cache}\n" if source_cache else "")
    )
    boards = tmp_path / "charts"
    boards.mkdir(exist_ok=True)
    (boards / "shared.yml").write_text(
        dedent(
            """
            title: Shared
            queries:
              totals:
                sql: "SELECT 1 AS value"
                source: db
            charts:
              c1: {query: totals, type: kpi, value: value}
            rows: [c1]
            """
        )
    )
    (boards / "board.yml").write_text(dedent(board_yaml))
    project = FilesystemProject(tmp_path)
    result = compile_file(project.path("charts/board.yml").read_board())
    assert result.success, f"Compilation failed: {result.errors}"
    # The registry, not `board.queries`: an anchored ref used straight from a
    # chart is never declared under `queries:`, so it only exists here.
    return result.query_registry[query_key].cache


def test_board_cache_reaches_a_cross_file_query_import(tmp_path: Path) -> None:
    """`cache:` on a board covers every query in it, imported ones included.

    A cross-file `queries: {name: other.queries.name}` ref takes a different
    normalize path than a local query, and it used to skip the board layer
    entirely — so the one-line board policy quietly meant "every query except
    the ones you can't see from here".
    """
    cache = _imported_querycache_layer(
        tmp_path,
        """
        title: T
        cache: 5m
        queries:
          main: shared.queries.totals
        charts:
          c1: {query: main, type: kpi, value: value}
        rows: [c1]
        """,
        "main",
    )
    assert cache.ttl == "5m"


def test_board_cache_reaches_an_anchored_cross_file_query_ref(tmp_path: Path) -> None:
    """The `shared.yml#totals` spelling is the same import under another name.

    It resolves through `resolve_external_query` rather than the dotted lane
    above, so it needs the board layer threaded separately — the two spellings
    of one concept must not disagree about whether the board's `cache:` applies.
    """
    cache = _imported_querycache_layer(
        tmp_path,
        """
        title: T
        cache: 5m
        charts:
          c1: {query: "shared.yml#totals", type: kpi, value: value}
        rows: [c1]
        """,
        "shared.yml#totals",
    )
    assert cache.ttl == "5m"


_DOTTED_IMPORT = """
    title: T
    queries:
      main: shared.queries.totals
    charts:
      c1: {query: main, type: kpi, value: value}
    rows: [c1]
    """

_ANCHORED_IMPORT = """
    title: T
    charts:
      c1: {query: "shared.yml#totals", type: kpi, value: value}
    rows: [c1]
    """


@pytest.mark.parametrize(
    ("board_yaml", "query_key"),
    [(_DOTTED_IMPORT, "main"), (_ANCHORED_IMPORT, "shared.yml#totals")],
    ids=["dotted", "anchored"],
)
def test_source_cache_reaches_an_imported_query(
    tmp_path: Path, board_yaml: str, query_key: str
) -> None:
    """The `sources:` layer applies to a query however it reached the board.

    `cache:` on a source is a statement about that warehouse's data, so it
    cannot depend on which of the three spellings pulled the query in. Both
    import lanes normalize through their own call, and each one that forgets to
    pass the source registry silently drops this layer and falls through to the
    project root instead.
    """
    cache = _imported_querycache_layer(
        tmp_path, board_yaml, query_key, source_cache="5m"
    )
    assert cache.ttl == "5m"


@pytest.mark.parametrize(
    ("board_yaml", "query_key"),
    [(_DOTTED_IMPORT, "main"), (_ANCHORED_IMPORT, "shared.yml#totals")],
    ids=["dotted", "anchored"],
)
def test_source_cache_false_is_never_re_enabled_for_an_imported_query(
    tmp_path: Path, board_yaml: str, query_key: str
) -> None:
    """The opt-out direction of the same gap, which is the one that hurts.

    A source that says `cache: false` is a live-data source. Losing the layer
    on an import doesn't merely forget an optimization — it replaces "never
    cache this" with the project root's ttl and serves day-old numbers.
    """
    cache = _imported_querycache_layer(
        tmp_path, board_yaml, query_key, source_cache="false"
    )
    assert cache.enabled is False


def _imported_chart_querycache_layer(
    tmp_path: Path,
    board_yaml: str,
    *,
    source_cache: str = "",
) -> CachePolicy:
    """Compile a board that imports `shared.yml`'s *chart*, and read its query.

    A cross-board chart import resolves the chart's whole query chain lexically
    against the source board, under synthetic keys — so the query never appears
    under a name the board authored, and the chart is the only handle on it.
    """
    reset_config()
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: warehouse.duckdb\n"
        + (f"    cache: {source_cache}\n" if source_cache else "")
    )
    boards = tmp_path / "charts"
    boards.mkdir(exist_ok=True)
    (boards / "shared.yml").write_text(
        dedent(
            """
            title: Shared
            queries:
              totals:
                sql: "SELECT 1 AS value"
                source: db
            charts:
              c1: {query: totals, type: kpi, value: value}
            rows: [c1]
            """
        )
    )
    (boards / "board.yml").write_text(dedent(board_yaml))
    project = FilesystemProject(tmp_path)
    result = compile_file(project.path("charts/board.yml").read_board())
    assert result.success, f"Compilation failed: {result.errors}"
    query = result.board.charts["imported"].query
    assert query is not None
    return query.cache


_CHART_IMPORT = """
    title: T
    {board_cache}
    charts:
      imported: shared.charts.c1
    rows: [imported]
    """


def test_source_cache_reaches_a_cross_board_imported_chart(tmp_path: Path) -> None:
    """The third lane: a chart import drags its own query chain along.

    Those queries are normalized by the lexical-scoping walk rather than by
    either import lane above, so it needs the source registry threaded through
    the chart-registry build — a third place the layer can go missing.
    """
    cache = _imported_chart_querycache_layer(
        tmp_path, _CHART_IMPORT.format(board_cache=""), source_cache="5m"
    )
    assert cache.ttl == "5m"


def test_board_cache_reaches_a_cross_board_imported_chart(tmp_path: Path) -> None:
    """An imported chart is a chart of *this* board, so the board's `cache:`
    covers the query behind it — the same rule the two query-import lanes
    already follow."""
    cache = _imported_chart_querycache_layer(
        tmp_path, _CHART_IMPORT.format(board_cache="cache: 30s")
    )
    assert cache.ttl == "30s"


class TestTtlValueIsBounded:
    """A well-formed but absurd `cache:` value is a compile error, never a crash.

    `parse_duration` validated shape and then built a `timedelta` from an
    unbounded `\\d+`, so the arithmetic — not the validator — decided what
    happened. Pydantic's `AfterValidator` converts `ValueError` into a
    `ValidationError` but lets `OverflowError` through, so those values escaped
    the diagnostic system entirely and reached the author as an interpreter
    traceback.

    Two distinct raw-internals paths from different calls, and neither guard
    closes both: `int()` itself raises past CPython's 4300-digit conversion
    limit (before any sum exists to range-check), while nine digits of weeks
    already overflows `timedelta`. So: a digit bound, *then* a value ceiling.

    Each case asserts the surfaced *message*, not just the exception type — the
    `ttl` union tries `Literal['forever']` first, so its error alone satisfies a
    type-only check while the author still sees nothing actionable.
    """

    @pytest.mark.parametrize(
        ("value", "label"),
        [
            ("999999999w", "nine digits — overflows timedelta, passes any digit bound"),
            ("9" * 20 + "w", "twenty digits — overflows timedelta"),
            ("9" * 5000 + "w", "past CPython's int() digit limit"),
        ],
    )
    def test_absurd_ttl_surfaces_an_actionable_error(
        self, value: str, label: str
    ) -> None:
        with pytest.raises(ValidationError) as exc:
            cache_layer({"ttl": value})
        message = str(exc.value)
        assert "too large" in message, (
            f"{label}: the author must be told the duration is out of range, "
            f"not handed CPython internals. Got:\n{message}"
        )
        assert "sys.set_int_max_str_digits" not in message, (
            f"{label}: a CPython digit-limit message is not actionable authoring "
            f"feedback. Got:\n{message}"
        )

    def test_the_ceiling_is_named_so_the_author_can_act(self) -> None:
        with pytest.raises(ValidationError) as exc:
            cache_layer({"ttl": "999999999w"})
        message = str(exc.value)
        assert "3650d" in message, (
            "the error must name the ceiling — 'too large' alone leaves the "
            f"author guessing what is allowed. Got:\n{message}"
        )

    def test_a_long_but_sane_ttl_still_parses(self) -> None:
        """The ceiling must not reject a real authoring intent."""
        assert cache_layer({"ttl": "52w"}).ttl == "52w"


# ── prefetch: the flat sibling of ttl ────────────────────────────────────────

_PREFETCH_SOURCES: dict[str, Any] = {
    "db": {"type": "duckdb", "path": "warehouse.duckdb"},
    "warm_db": {
        "type": "duckdb",
        "path": "warehouse.duckdb",
        "cache": {"prefetch": True},
    },
}


def test_prefetch_defaults_false_in_the_shipped_root() -> None:
    query = normalize_query(
        "q", {"sql": "SELECT 1", "source": "db"}, sources=dict(_PREFETCH_SOURCES)
    )
    assert query.cache.prefetch is False


def test_source_prefetch_reaches_its_queries_and_a_query_can_opt_out() -> None:
    warm = normalize_query(
        "q", {"sql": "SELECT 1", "source": "warm_db"}, sources=dict(_PREFETCH_SOURCES)
    )
    cold = normalize_query(
        "q",
        {"sql": "SELECT 1", "source": "warm_db", "cache": {"prefetch": False}},
        sources=dict(_PREFETCH_SOURCES),
    )
    assert warm.cache.prefetch is True
    assert cold.cache.prefetch is False


def test_query_prefetch_beats_a_board_that_opts_out(tmp_path: Path) -> None:
    policy = _compiled_querycache_layer(
        tmp_path,
        _BOARD.format(
            board_cache="cache: {prefetch: false}",
            query_cache="cache: {prefetch: true}",
        ),
    )
    assert policy.prefetch is True


def test_project_prefetch_is_the_default_and_a_board_can_opt_out(
    tmp_path: Path,
) -> None:
    project_yaml = "cache:\n  prefetch: true\n"
    inherited = _compiled_querycache_layer(
        tmp_path,
        _BOARD.format(board_cache="", query_cache=""),
        project_yaml=project_yaml,
    )
    opted_out = _compiled_querycache_layer(
        tmp_path,
        _BOARD.format(board_cache="cache: {prefetch: false}", query_cache=""),
        project_yaml=project_yaml,
    )
    assert inherited.prefetch is True
    assert inherited.ttl == _root_ttl()
    assert opted_out.prefetch is False


def test_meta_prefetch_reaches_the_query_and_the_board_overrides_it(
    tmp_path: Path,
) -> None:
    meta = "cache:\n  prefetch: true\n"
    inherited = _compiled_querycache_layer(
        tmp_path, _BOARD.format(board_cache="", query_cache=""), meta_yaml=meta
    )
    overridden = _compiled_querycache_layer(
        tmp_path,
        _BOARD.format(board_cache="cache: {prefetch: false}", query_cache=""),
        meta_yaml=meta,
    )
    assert inherited.prefetch is True
    assert overridden.prefetch is False


def test_prefetch_under_a_cache_false_board_fails_the_compile(tmp_path: Path) -> None:
    reset_config()
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: warehouse.duckdb\n"
    )
    boards = tmp_path / "charts"
    boards.mkdir()
    (boards / "board.yml").write_text(
        dedent(
            _BOARD.format(
                board_cache="cache: false", query_cache="cache: {prefetch: true}"
            )
        )
    )
    project = FilesystemProject(tmp_path)

    result = compile_file(project.path("charts/board.yml").read_board())

    assert not result.success
    message = "; ".join(e.message for e in result.errors)
    assert "prefetch" in message
    assert "'main'" in message


def test_prefetch_under_a_cache_false_meta_fails_the_compile(
    tmp_path: Path,
) -> None:
    """meta.yml and the board fold into one layer before the cascade sees them."""
    reset_config()
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: warehouse.duckdb\n"
    )
    boards = tmp_path / "charts"
    boards.mkdir()
    (boards / "meta.yml").write_text("cache: false\n")
    (boards / "board.yml").write_text(
        dedent(_BOARD.format(board_cache="cache: {prefetch: true}", query_cache=""))
    )
    project = FilesystemProject(tmp_path)

    result = compile_file(project.path("charts/board.yml").read_board())

    assert not result.success
    assert "prefetch" in "; ".join(e.message for e in result.errors)


def test_prefetch_with_caching_turned_on_in_the_same_layer_is_allowed(
    tmp_path: Path,
) -> None:
    policy = _compiled_querycache_layer(
        tmp_path,
        _BOARD.format(board_cache="cache: {ttl: 1h, prefetch: true}", query_cache=""),
        meta_yaml="cache: false\n",
    )
    assert policy.enabled is True
    assert policy.prefetch is True


def test_prefetch_in_a_nested_board_under_a_cache_false_parent_is_an_error() -> None:
    result = compile_with_board_sources(
        """
        title: T
        cache: false
        rows:
          - cache: {prefetch: true}
            queries:
              nested_q: {sql: "SELECT 1", source: db}
            charts:
              nc: {query: nested_q, type: kpi, value: value}
            rows: [nc]
        """
    )
    assert not result.success
    assert "prefetch" in "; ".join(e.message for e in result.errors)
