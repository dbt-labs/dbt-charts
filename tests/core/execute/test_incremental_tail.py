"""Executor incremental tail query read-merge-write path.

TDD assertions per the task worksheet:
1. Tail query issued only over the new range (watermark filter in SQL).
2. Merged series equals a full refresh.
3. Incremental watermark-column dedup drops superseded rows at the watermark boundary.
4. `incremental: false` path is byte-identical to the normal cache path.
5. Hash change (SQL edit) forces full refresh.

Plus compile-time assertion:
6. A bare `incremental: true` (no watermark column) raises CompilationError.

Regression tests for CRITICAL/HIGH review findings:
7.  Datetime/date/Decimal/apostrophe-string watermarks produce dialect-correct SQL.
8.  Cache-ref queries with an incremental column set fall back to full refresh (not
    tail-wrapped Jinja sent to adapter).
9.  Queries with LIMIT / window functions / ORDER BY at the top level are
    ineligible for the tail path → full refresh with a warning.
10. A missing incremental watermark column raises QueryError, not a silent full refresh.
11. A nested board's explicit `incremental: false` overrides the parent.
12. A non-SQL query with an explicit `incremental: <column>` is a compile error.
13. Flipping `incremental: false` forces a cache refresh (key includes the flag).
14. Merged set truncation keeps the newest rows.
15. A cache backend that violates the type-fidelity contract (coerces
    date/datetime to ISO strings on `get()`, unlike its own `put()` input)
    makes the merge raise QueryError rather than silently misordering rows.
16. A tz-aware datetime incremental watermark column round-trips through TrivialDuckDBCache
    with tzinfo intact, and its predicate is a TIMESTAMPTZ-typed CAST.
17. An unparseable cached string, where the tail's real key type is
    date/datetime, raises QueryError rather than silently skipping it.
18. A genuinely-VARCHAR incremental watermark column holding ISO-date-shaped
    values ('2026-08-22', '20260822') is never reinterpreted as DATE — the tail
    predicate stays a string literal and every one of 4 consecutive renders
    against a real DuckDB warehouse succeeds.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import duckdb
import pytest
import sqlglot
import sqlglot.expressions as exp

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as df_compile
from dbt_charts.core.compile.compiler import compile_file
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry
from dbt_charts.core.execute.cache_backend import CacheHit
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

# ─── helpers ─────────────────────────────────────────────────────────────────


def _adapter(*datasets: list[dict]) -> Mock:
    """Mock adapter returning successive result sets on each execute() call."""
    registry = Mock()
    results = []
    for data in datasets:
        r = Mock()
        r.is_success = True
        r.data = list(data)
        r.resolved_relations = None
        r.truncated_reason = None
        results.append(r)
    registry.execute.side_effect = results
    registry.project.file_version = Mock(return_value="v1")
    registry.project_file_sources = Mock(return_value={})
    # Default dialect for the mocked source — individual tests override this
    # to exercise dialect-specific tail-SQL rendering (mysql, bigquery, tsql).
    registry.resolve_query_source.return_value = SimpleNamespace(type="duckdb")
    return registry


def _executor(board_result, adapter, cache) -> Executor:
    return Executor(
        board_result.board,
        adapter_registry=adapter,
        query_registry=board_result.query_registry,
        result_cache=cache,
    )


_BOARD_INC = """\
title: T
incremental: ts
queries:
  q:
    sql: SELECT ts, val FROM events
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""

_BOARD_NO_INC = """\
title: T
queries:
  q:
    sql: SELECT ts, val FROM events
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""


# ─── Test 1: tail SQL contains watermark filter ───────────────────────────────


class TestTailQueryWatermarkFilter:
    """The SQL passed to the adapter on the second render wraps the original
    with a watermark predicate keyed on the incremental column."""

    def test_tail_sql_contains_watermark_and_key(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(_BOARD_INC)

        initial = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        _executor(result, _adapter(initial), cache).execute_query("q")

        # Second render — should issue a tail-bounded query
        tail = [{"ts": 3, "val": 30}]
        ad2 = _adapter(tail)
        _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        call_query = ad2.execute.call_args[0][0]
        sql_lower = call_query.sql.lower()
        # The tail SQL must reference the incremental column and the watermark value
        assert "ts" in sql_lower
        assert "2" in call_query.sql  # watermark = MAX(ts) = 2


# ─── Test 2: merged result equals a full refresh ─────────────────────────────


class TestMergedEqualsFullRefresh:
    """Incremental merge produces the same row set as running the full query."""

    def test_incremental_merge_matches_full_refresh(self, tmp_path) -> None:
        result = df_compile(_BOARD_INC)
        cache_inc = TrivialDuckDBCache(db_path=tmp_path / "inc.duckdb")

        initial = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        _executor(result, _adapter(initial), cache_inc).execute_query("q")

        # Tail rows: ts=2 is re-fetched (>= watermark 2), ts=3 is new
        tail = [{"ts": 2, "val": 20}, {"ts": 3, "val": 30}]
        merged = _executor(result, _adapter(tail), cache_inc).execute_query("q")

        # A fresh full refresh would return all three rows
        all_rows = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}, {"ts": 3, "val": 30}]
        assert sorted(merged, key=lambda r: r["ts"]) == all_rows


# ─── Test 3: dedup drops superseded rows ─────────────────────────────────────


class TestPkDedupDropsSupersededRows:
    """When a row at the watermark boundary is restated in the tail, the
    merged result keeps the tail (newer) version and drops the prior one."""

    def test_dedup_keeps_tail_row_for_restated_key(self, tmp_path) -> None:
        result = df_compile(_BOARD_INC)
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")

        initial = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        _executor(result, _adapter(initial), cache).execute_query("q")

        # ts=2 is corrected (restated), ts=3 is new
        tail = [{"ts": 2, "val": 99}, {"ts": 3, "val": 30}]
        merged = _executor(result, _adapter(tail), cache).execute_query("q")

        by_ts = {r["ts"]: r["val"] for r in merged}
        assert by_ts == {1: 10, 2: 99, 3: 30}  # ts=2 has the corrected value
        assert len(merged) == 3  # no duplicates


# ─── Test 4: incremental: false takes the normal cache path ──────────────────


class TestIncrementalFalseBypassesTailPath:
    """With incremental: false the second render serves from cache without
    any additional adapter call — byte-identical to the non-incremental path."""

    def test_false_serves_from_cache_no_tail_call(self, tmp_path) -> None:
        result = df_compile(_BOARD_NO_INC)
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")

        rows = [{"ts": 1, "val": 10}]
        _executor(result, _adapter(rows), cache).execute_query("q")

        # Second render: cache is warm, no adapter call expected
        ad2 = _adapter()  # no results — call would fail
        data = _executor(result, ad2, cache).execute_query("q")
        ad2.execute.assert_not_called()
        assert data == rows


# ─── Test 5: hash change forces full refresh ─────────────────────────────────


class TestHashChangeForcesFull:
    """Editing the query SQL changes its cache key, so a second render
    executes the full query rather than taking the incremental tail path."""

    def test_sql_change_invalidates_cache(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")

        board_v1 = """\
title: T
incremental: ts
queries:
  q:
    sql: SELECT ts, val FROM events
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        board_v2 = """\
title: T
incremental: ts
queries:
  q:
    sql: SELECT ts, val FROM events WHERE ts > 0
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        rows_v1 = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        result_v1 = df_compile(board_v1)
        _executor(result_v1, _adapter(rows_v1), cache).execute_query("q")

        # Second render with modified SQL — full query must be re-executed
        rows_v2 = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}, {"ts": 3, "val": 30}]
        result_v2 = df_compile(board_v2)
        ad2 = _adapter(rows_v2)
        data = _executor(result_v2, ad2, cache).execute_query("q")
        # Adapter must have been called (not served from cache or incremental)
        ad2.execute.assert_called_once()
        assert sorted(data, key=lambda r: r["ts"]) == rows_v2


# ─── Test 6: compile-time validation ─────────────────────────────────────────


class TestIncrementalValidation:
    """A bare `incremental: true` (no watermark column) is a compile-time
    error — the single `incremental:` field holds the column name directly,
    so `true` has nothing to key on."""

    def test_incremental_true_without_key_raises(self) -> None:
        board_yaml = """\
title: T
incremental: true
queries:
  q:
    sql: SELECT ts, val FROM events
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        result = df_compile(board_yaml)
        assert not result.success
        assert any("watermark column" in str(e) for e in result.errors)

    def test_board_incremental_cascades_to_query(self) -> None:
        """A board-level watermark column applies to a query that sets none."""
        result = df_compile(_BOARD_INC)
        assert result.board.queries["q"].incremental == "ts"

    def test_query_level_incremental_without_board_setting(self) -> None:
        """A query can enable incremental refresh even when its board hasn't."""
        board_yaml = """\
title: T
queries:
  q:
    sql: SELECT ts, val FROM events
    source: db
    incremental: ts
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        # Should not raise
        result = df_compile(board_yaml)
        assert result.board.queries["q"].incremental == "ts"

    def test_query_level_incremental_overrides_board_with_false(self) -> None:
        """A query can opt out of a board-level incremental column."""
        board_yaml = """\
title: T
incremental: ts
queries:
  q:
    sql: SELECT ts, val FROM events
    source: db
    incremental: false
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        result = df_compile(board_yaml)
        assert result.board.queries["q"].incremental is None

    def test_query_level_incremental_overrides_board_with_other_column(self) -> None:
        """A query can override the board's watermark column with its own."""
        board_yaml = """\
title: T
incremental: ts
queries:
  q:
    sql: SELECT ts, val FROM events
    source: db
    incremental: updated_at
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        result = df_compile(board_yaml)
        assert result.board.queries["q"].incremental == "updated_at"


# ─────────────────────────────────────────────────────────────────────────────
# Regression tests for CRITICAL/HIGH review findings
# ─────────────────────────────────────────────────────────────────────────────

# ─── CRITICAL-1: Watermark must be rendered as dialect-correct SQL literal ───


_BOARD_TS_INC = """\
title: T
incremental: ts
queries:
  q:
    sql: SELECT ts, val FROM events
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""


class TestDatetimeWatermark:
    """A datetime watermark must produce dialect-correct SQL, not Python repr."""

    def test_datetime_key_sql_is_not_python_repr(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(_BOARD_TS_INC)

        initial = [{"ts": datetime(2026, 8, 22, 12, 0), "val": 10}]
        _executor(result, _adapter(initial), cache).execute_query("q")

        tail = [{"ts": datetime(2026, 8, 22, 13, 0), "val": 20}]
        ad2 = _adapter(tail)
        _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        sql = ad2.execute.call_args[0][0].sql
        # Python repr of datetime looks like: datetime.datetime(2026, 8, 22, 12, 0)
        assert "datetime.datetime" not in sql
        assert "2026" in sql

    def test_date_key_sql_is_not_python_repr(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(_BOARD_TS_INC)

        initial = [{"ts": date(2026, 8, 22), "val": 10}]
        _executor(result, _adapter(initial), cache).execute_query("q")

        tail = [{"ts": date(2026, 8, 23), "val": 20}]
        ad2 = _adapter(tail)
        _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        sql = ad2.execute.call_args[0][0].sql
        assert "datetime.date" not in sql
        assert "2026" in sql
        # The watermark must survive the cache round-trip as a real DATE, not
        # degrade to a bare string — the predicate must be a typed DATE literal
        # (CAST(... AS DATE)) re-parseable as a comparison against a Cast node,
        # not a bare string literal a warehouse would compare against the
        # column's actual DATE type by implicit (or failing) coercion.
        parsed = sqlglot.parse_one(sql)
        predicate = parsed.find(exp.GTE)
        assert predicate is not None
        assert isinstance(predicate.expression, exp.Cast)
        assert predicate.expression.to.this == exp.DataType.Type.DATE
        assert "2026-08-22" in sql

    def test_decimal_key_sql_is_not_python_repr(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(_BOARD_TS_INC)

        initial = [{"ts": Decimal("3.5"), "val": 10}]
        _executor(result, _adapter(initial), cache).execute_query("q")

        tail = [{"ts": Decimal("4.0"), "val": 20}]
        ad2 = _adapter(tail)
        _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        sql = ad2.execute.call_args[0][0].sql
        assert "Decimal" not in sql
        assert "3.5" in sql

    def test_string_with_apostrophe_key_is_properly_escaped(self, tmp_path) -> None:
        """A string value with an apostrophe must be SQL-escaped, not repr'd."""
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(_BOARD_TS_INC)

        initial = [{"ts": "a'b", "val": 10}]
        _executor(result, _adapter(initial), cache).execute_query("q")

        tail = [{"ts": "a'c", "val": 20}]
        ad2 = _adapter(tail)
        _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        sql = ad2.execute.call_args[0][0].sql
        # Must not contain raw unescaped apostrophe in the literal context
        # Python repr of "a'b" is "a'b" — same string, but must be SQL-escaped
        assert "Decimal" not in sql and "datetime" not in sql
        # The literal must be SQL single-quoted and escaped: 'a''b'
        assert "a''b" in sql or "'a\\'b'" in sql or "'a''b'" in sql


# ─── CRITICAL-3: Tail must wrap resolved SQL, not Jinja refs ─────────────────


_BOARD_QUERY_REF_INC = """\
title: T
queries:
  base:
    sql: SELECT ts, val FROM src
    source: db
  main:
    sql: SELECT ts, val FROM {{ queries.base }}
    source: db
    incremental: ts
charts:
  c:
    query: main
    type: line
    x: ts
    y: val
rows:
  - c
"""


class TestTailUsesResolvedSql:
    """The tail SQL wraps the fully resolved query, not the Jinja template."""

    def test_tail_does_not_contain_jinja_ref(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(_BOARD_QUERY_REF_INC)

        # Both base and main are called on first render
        initial_base = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        initial_main = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        ad1 = _adapter(initial_base, initial_main)
        _executor(result, ad1, cache).execute_query("main")

        tail_main = [{"ts": 3, "val": 30}]
        # On second render, main has a cache hit → tail path; base is not re-executed
        ad2 = _adapter(tail_main)
        _executor(result, ad2, cache).execute_query("main")

        ad2.execute.assert_called_once()
        sql = ad2.execute.call_args[0][0].sql
        # Tail SQL must not contain unresolved Jinja
        assert "{{ queries" not in sql


# ─── HIGH-1: Unsafe SQL shapes fall back to full refresh ─────────────────────


class TestIncrementalSafetyPredicate:
    """Queries with LIMIT / window functions / ORDER BY use full refresh."""

    def test_limit_query_falls_back_to_full_refresh(self, tmp_path) -> None:
        board_yaml = """\
title: T
incremental: ts
queries:
  q:
    sql: SELECT ts, val FROM events ORDER BY val DESC LIMIT 10
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(board_yaml)

        rows = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        _executor(result, _adapter(rows), cache).execute_query("q")

        # Second render: full refresh (adapter called with original SQL, not tail)
        rows2 = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}, {"ts": 3, "val": 30}]
        ad2 = _adapter(rows2)
        _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        # Full refresh SQL must equal the original, not a tail-wrapped one
        call_sql = ad2.execute.call_args[0][0].sql
        assert "WHERE" not in call_sql.upper() or "_dct_base" not in call_sql

    def test_window_function_falls_back_to_full_refresh(self, tmp_path) -> None:
        board_yaml = """\
title: T
incremental: ts
queries:
  q:
    sql: "SELECT ts, SUM(val) OVER (ORDER BY ts) AS running FROM events"
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: running
rows:
  - c
"""
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(board_yaml)

        rows = [{"ts": 1, "running": 10}, {"ts": 2, "running": 30}]
        _executor(result, _adapter(rows), cache).execute_query("q")

        rows2 = [
            {"ts": 1, "running": 10},
            {"ts": 2, "running": 30},
            {"ts": 3, "running": 60},
        ]
        ad2 = _adapter(rows2)
        _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        call_sql = ad2.execute.call_args[0][0].sql
        assert "_dct_base" not in call_sql

    def test_order_by_query_falls_back_to_full_refresh(self, tmp_path) -> None:
        board_yaml = """\
title: T
incremental: ts
queries:
  q:
    sql: SELECT ts, val FROM events ORDER BY ts
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(board_yaml)

        rows = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        _executor(result, _adapter(rows), cache).execute_query("q")

        rows2 = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}, {"ts": 3, "val": 30}]
        ad2 = _adapter(rows2)
        _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        call_sql = ad2.execute.call_args[0][0].sql
        assert "_dct_base" not in call_sql


# ─── HIGH-2: Missing incremental watermark column raises a clear error ──────


class TestMissingIncrementalKeyColumn:
    """A misspelled incremental watermark column must raise QueryError on second render."""

    def test_missing_key_column_raises_query_error(self, tmp_path) -> None:
        board_yaml = """\
title: T
incremental: wrong_col
queries:
  q:
    sql: SELECT ts, val FROM events
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        import pytest

        from dbt_charts.core.diagnostics.execution import QueryError

        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(board_yaml)

        rows = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        _executor(result, _adapter(rows), cache).execute_query("q")

        ad2 = _adapter([{"ts": 3, "val": 30}])
        with pytest.raises(QueryError, match="wrong_col"):
            _executor(result, ad2, cache).execute_query("q")


# ─── HIGH-3a: Nested board explicit incremental: false overrides parent ───────


class TestNestedBoardIncrementalFalse:
    """A nested board's explicit `incremental: false` turns off the tail path."""

    def test_nested_false_disables_parent_incremental(self, tmp_path) -> None:
        board_yaml = """\
title: T
incremental: ts
rows:
  - cols:
      - incremental: false
        queries:
          q:
            sql: SELECT ts, val FROM events
            source: db
        charts:
          c:
            query: q
            type: line
            x: ts
            y: val
        rows:
          - c
"""
        result = df_compile(board_yaml)
        assert result.success
        assert result.query_registry["q"].incremental is None


# ─── HIGH-3b: Non-SQL query with incremental: <column> → compile error ───────


class TestNonSqlQueryIncrementalError:
    """An HTTP/values query with an explicit incremental column is a
    compile-time error — only SQL queries can be tailed."""

    def test_http_query_incremental_column_raises(self) -> None:
        board_yaml = """\
title: T
incremental: ts
queries:
  q:
    type: http
    url: https://example.com/data
    incremental: ts
charts:
  c:
    query: q
    type: table
rows:
  - c
"""
        result = df_compile(board_yaml)
        assert not result.success
        assert any("only supported for SQL queries" in str(e) for e in result.errors)


# ─── HIGH-4: incremental flag is part of the cache key ───────────────────────


class TestIncrementalCacheKey:
    """Flipping incremental: false invalidates the accumulated cache."""

    def test_incremental_false_forces_adapter_call(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")

        board_inc = """\
title: T
incremental: ts
queries:
  q:
    sql: SELECT ts, val FROM events
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        board_no_inc = """\
title: T
queries:
  q:
    sql: SELECT ts, val FROM events
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        rows = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        result_inc = df_compile(board_inc)
        _executor(result_inc, _adapter(rows), cache).execute_query("q")

        # Second render with incremental: false — must NOT serve the incremental
        # merged rows from cache; adapter must be called for a fresh read.
        rows2 = [{"ts": 1, "val": 10}]
        result_no_inc = df_compile(board_no_inc)
        ad2 = _adapter(rows2)
        data = _executor(result_no_inc, ad2, cache).execute_query("q")
        ad2.execute.assert_called_once()
        assert data == rows2


# ─── HIGH-5: Merge truncation keeps newest rows ───────────────────────────────


class TestTruncationKeepsNewest:
    """When the merged set exceeds max_rows, the newest rows are kept."""

    def test_newest_rows_kept_on_truncation(self, tmp_path) -> None:
        from unittest.mock import patch as _patch

        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(_BOARD_INC)

        # First render: rows ts=1..4
        initial = [{"ts": i, "val": i * 10} for i in range(1, 5)]
        _executor(result, _adapter(initial), cache).execute_query("q")

        # Second render: tail returns ts=4,5,6 (ts=4 is restated)
        tail = [{"ts": 4, "val": 99}, {"ts": 5, "val": 50}, {"ts": 6, "val": 60}]
        ad2 = _adapter(tail)
        with _patch(
            "dbt_charts.core.compile.config.resolve_max_rows",
            return_value=5,
        ):
            merged = _executor(result, ad2, cache).execute_query("q")

        # After dedup: ts=1,2,3 (from prior) + ts=4,5,6 (tail) = 6 rows,
        # truncated to 5 newest. Newest = ts=2,3,4,5,6 (ts=1 is dropped).
        ts_values = {r["ts"] for r in merged}
        assert 6 in ts_values  # newest must be present
        assert 1 not in ts_values  # oldest must be dropped


# ─────────────────────────────────────────────────────────────────────────────
# Round-2 review regression tests
# ─────────────────────────────────────────────────────────────────────────────

# ─── CRITICAL-1: file-source queries must never hit the tail path ────────────


class TestFileSourceIncrementalDoesNotIntercept:
    """An incremental watermark column on a CSV/file-source query must never reach the
    tail path — it has no warehouse dialect and a tail-wrapped query raises
    "Unknown dialect 'csv'" on every render after the first. A warm render
    must serve the file source exactly like a non-incremental cached query:
    from the result cache, without re-materializing and without error."""

    def test_csv_source_incremental_survives_warm_render(self, tmp_path) -> None:
        import yaml

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )
        from dbt_charts.core.execute.file_source_materializer import (
            FileSourceMaterializer,
        )

        (tmp_path / "data").mkdir()
        (tmp_path / "data/events.csv").write_text("ts,val\n1,10\n2,20\n")
        (tmp_path / "dbt_charts.yml").write_text(
            yaml.dump(
                {
                    "sources": {
                        "events": {
                            "type": "csv",
                            "files": {"events": "data/events.csv"},
                        }
                    }
                }
            )
        )
        project = FilesystemProject(tmp_path)
        board = df_compile(
            yaml.dump(
                {
                    "title": "T",
                    "incremental": "ts",
                    "queries": {
                        "q": {"sql": "SELECT ts, val FROM events", "source": "events"}
                    },
                    "charts": {"c": {"query": "q", "type": "table"}},
                }
            )
        ).board
        assert board is not None
        result_cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")

        class _CountingMaterializer(FileSourceMaterializer):
            def __init__(self, project: FilesystemProject) -> None:
                super().__init__(project)
                self.run_calls = 0

            def materialize_and_run(self, source, sql, variables, source_name):  # type: ignore[override]
                self.run_calls += 1
                return super().materialize_and_run(source, sql, variables, source_name)

        # Render 1 (cold): materializer runs, results cached.
        mat1 = _CountingMaterializer(project)
        ex1 = Executor(
            board,
            adapter_registry=build_adapter_registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=result_cache,
            file_materializer=mat1,
        )
        rows1 = ex1.execute_query("q")
        assert [r["ts"] for r in rows1] == [1, 2]
        assert mat1.run_calls == 1

        # Render 2 (warm): must NOT raise "Unknown dialect 'csv'" — served
        # from the result cache exactly like a non-incremental query.
        mat2 = _CountingMaterializer(project)
        ex2 = Executor(
            board,
            adapter_registry=build_adapter_registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=result_cache,
            file_materializer=mat2,
        )
        rows2 = ex2.execute_query("q")
        assert [r["ts"] for r in rows2] == [1, 2]
        assert mat2.run_calls == 0


# ─── CRITICAL-2: dialect-aware identifier quoting in the tail predicate ──────


class TestDialectAwareTailPredicate:
    """The tail predicate must quote the incremental watermark column identifier per the
    query's resolved dialect. Hardcoded ANSI double-quotes tokenize as a
    STRING LITERAL on MySQL/BigQuery, so the predicate silently becomes
    `WHERE 'ts' >= ...` — always false — and the board freezes at its first
    snapshot with no error."""

    @pytest.mark.parametrize("dialect_name", ["mysql", "bigquery"])
    def test_tail_predicate_is_column_comparison_under_dialect(
        self, tmp_path, dialect_name
    ) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(_BOARD_INC)

        initial = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        ad1 = _adapter(initial)
        ad1.resolve_query_source.return_value = SimpleNamespace(type=dialect_name)
        _executor(result, ad1, cache).execute_query("q")

        ad2 = _adapter([{"ts": 3, "val": 30}])
        ad2.resolve_query_source.return_value = SimpleNamespace(type=dialect_name)
        _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        sql = ad2.execute.call_args[0][0].sql
        # Backtick-quoted per mysql/bigquery generation rules.
        assert "`ts`" in sql
        # Re-parsing under the *same* dialect must yield a Column comparison,
        # not a string literal — the exact collision round-1/round-2 missed.
        parsed = sqlglot.parse_one(sql, read=dialect_name)
        predicate = parsed.find(exp.GTE)
        assert predicate is not None
        assert isinstance(predicate.this, exp.Column)
        assert not isinstance(predicate.this, exp.Literal)


# ─── HIGH-1: safety predicate escapes ─────────────────────────────────────────


class TestSafetyPredicateDialectAndSetOperationEscapes:
    """Three escapes from the LIMIT/ORDER BY/window safety guard: dialect-
    specific top-N (T-SQL TOP), ORDER BY hanging off a set operation instead
    of a bare Select, and a bare aggregate with no GROUP BY changing row
    count on merge."""

    def test_tsql_top_n_falls_back_to_full_refresh(self, tmp_path) -> None:
        board_yaml = """\
title: T
incremental: ts
queries:
  q:
    sql: SELECT TOP 10 ts, val FROM events
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(board_yaml)

        rows = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        ad1 = _adapter(rows)
        ad1.resolve_query_source.return_value = SimpleNamespace(type="mssql")
        _executor(result, ad1, cache).execute_query("q")

        rows2 = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}, {"ts": 3, "val": 30}]
        ad2 = _adapter(rows2)
        ad2.resolve_query_source.return_value = SimpleNamespace(type="mssql")
        _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        call_sql = ad2.execute.call_args[0][0].sql
        assert "_dct_base" not in call_sql

    def test_union_order_by_falls_back_to_full_refresh(self, tmp_path) -> None:
        board_yaml = """\
title: T
incremental: ts
queries:
  q:
    sql: SELECT ts, val FROM events UNION ALL SELECT ts, val FROM other ORDER BY ts
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(board_yaml)

        rows = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        _executor(result, _adapter(rows), cache).execute_query("q")

        rows2 = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}, {"ts": 3, "val": 30}]
        ad2 = _adapter(rows2)
        _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        call_sql = ad2.execute.call_args[0][0].sql
        assert "_dct_base" not in call_sql

    def test_bare_aggregate_falls_back_to_full_refresh(self, tmp_path) -> None:
        board_yaml = """\
title: T
incremental: ts
queries:
  q:
    sql: SELECT COUNT(*) AS n, MAX(ts) AS ts FROM events
    source: db
charts:
  c:
    query: q
    type: table
rows:
  - c
"""
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(board_yaml)

        rows = [{"n": 100, "ts": 5}]
        _executor(result, _adapter(rows), cache).execute_query("q")

        rows2 = [{"n": 150, "ts": 9}]
        ad2 = _adapter(rows2)
        _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        call_sql = ad2.execute.call_args[0][0].sql
        assert "_dct_base" not in call_sql

    def test_fetch_first_falls_back_to_full_refresh(self, tmp_path) -> None:
        """ANSI ``FETCH FIRST n ROWS ONLY`` parses as sqlglot's ``exp.Fetch``,
        not ``exp.Limit`` — a guard that only checks ``exp.Limit`` lets a
        row-capped query through the tail path, where the merge can return
        more rows than the author's declared cap."""
        board_yaml = """\
title: T
incremental: ts
queries:
  q:
    sql: SELECT ts, val FROM events FETCH FIRST 2 ROWS ONLY
    source: db
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(board_yaml)

        rows = [{"ts": 1, "val": 10}, {"ts": 2, "val": 20}]
        _executor(result, _adapter(rows), cache).execute_query("q")

        rows2 = [{"ts": 3, "val": 30}, {"ts": 4, "val": 40}]
        ad2 = _adapter(rows2)
        _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        call_sql = ad2.execute.call_args[0][0].sql
        assert "_dct_base" not in call_sql


# ─── HIGH-2: date watermark through the real cache ────────────────────────────


class TestDateWatermarkThroughRealCache:
    """A `date`-typed incremental watermark column must round-trip through the real
    TrivialDuckDBCache with its type intact, so the merge/sort/dedup after a
    tail render is correct rather than silently unsorted or double-counted."""

    def test_merge_is_correctly_ordered_and_deduped(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(_BOARD_TS_INC)

        initial = [
            {"ts": date(2026, 8, 20), "val": 20},
            {"ts": date(2026, 8, 21), "val": 21},
            {"ts": date(2026, 8, 22), "val": 22},
        ]
        _executor(result, _adapter(initial), cache).execute_query("q")

        # ts=2026-08-21 is restated; 2026-08-23 is new.
        tail = [
            {"ts": date(2026, 8, 21), "val": 210},
            {"ts": date(2026, 8, 23), "val": 23},
        ]
        merged = _executor(result, _adapter(tail), cache).execute_query("q")

        # Descending by key so head-truncation keeps the newest — no
        # TypeError-suppressed unsorted fallback.
        assert [r["ts"] for r in merged] == [
            date(2026, 8, 23),
            date(2026, 8, 22),
            date(2026, 8, 21),
            date(2026, 8, 20),
        ]
        by_ts = {r["ts"]: r["val"] for r in merged}
        assert by_ts[date(2026, 8, 21)] == 210  # tail wins, not double-counted
        assert len(merged) == 4


class TestDecimalWatermarkThroughRealCache:
    """A `Decimal`-typed incremental watermark column that is not exactly representable as
    a binary float (`Decimal('101.20')`) must round-trip through the real
    TrivialDuckDBCache exactly. Before the fix, both shipped cache backends
    coerced Decimal -> float on write, so a prior row's key came back as
    `101.2` (float) while the freshly-queried tail row's key stayed
    `Decimal('101.20')` — `Decimal('101.20') == 101.2` is False, so the
    dedup in `_merge_incremental_rows` never recognized the boundary row as
    restated and it duplicated on every warm render, unbounded."""

    def test_four_renders_do_not_duplicate_the_boundary_row(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(_BOARD_TS_INC)

        initial = [
            {"ts": Decimal("100.10"), "val": 1},
            {"ts": Decimal("101.20"), "val": 2},
        ]
        merged = _executor(result, _adapter(initial), cache).execute_query("q")
        assert len(merged) == 2

        for _ in range(3):
            tail = [{"ts": Decimal("101.20"), "val": 2}]
            merged = _executor(result, _adapter(tail), cache).execute_query("q")
            assert len(merged) == 2, [r["ts"] for r in merged]

    def test_large_integral_decimal_watermark_survives_full_precision(
        self, tmp_path
    ) -> None:
        """A Decimal above 2**53 must not silently lose digits on the round
        trip through the cache — a lossy watermark understates MAX(key_col)
        and re-fetches (and re-duplicates) rows already cached."""
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(_BOARD_TS_INC)

        big = Decimal("12345678901234567890")
        initial = [{"ts": big, "val": 1}]
        _executor(result, _adapter(initial), cache).execute_query("q")

        tail = [{"ts": big + 1, "val": 2}]
        ad2 = _adapter(tail)
        merged = _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        sql = ad2.execute.call_args[0][0].sql
        assert "12345678901234567890" in sql
        assert len(merged) == 2
        assert Decimal("12345678901234567890") in {r["ts"] for r in merged}


class TestIncomparableWatermarkTypesRaise:
    """When cached and tail rows genuinely disagree on the key column's type
    (not the cache-roundtrip case above, which now preserves type), the merge
    must raise a clear error rather than silently return an unsorted result."""

    def test_merge_raises_on_incomparable_key_types(self) -> None:
        from dbt_charts.core.diagnostics.execution import QueryError
        from dbt_charts.core.execute.executor import _merge_incremental_rows

        tail_rows = [{"ts": 3, "val": 30}]
        prior_rows = [{"ts": "not-a-number", "val": 10}]
        with pytest.raises(QueryError, match="not.*orderable"):
            _merge_incremental_rows(tail_rows, prior_rows, "ts", "q")


# ─── CRITICAL (round 3): temporal incremental watermark column across cache backends ──


class _StringCoercingStubCache:
    """QueryResultCache stub violating the type-fidelity contract
    (cache_backend.py's ``QueryResultCache.get()`` docstring: a warm hit must
    hand back each column with its original ``put()``-time Python type):
    date/datetime values are stored as their ISO 8601 string on write and
    never restored on read, so `get()` hands the executor `str` where the
    source column is actually temporal — unlike TrivialDuckDBCache and the
    fixed PostgresResultCache, both of which round-trip the Python type
    (a typed DuckDB column and a per-column temporal type map, respectively).

    This is deliberately a NON-COMPLIANT backend now — round 3 made the
    executor paper over exactly this violation by guessing the type back
    from string shape, which broke genuinely-VARCHAR incremental watermark columns
    holding ISO-date-shaped values (see TestVarcharIncrementalKeySurvivesRealCache
    below). The contract now lives on the backend, not the executor: a
    backend that lies about its own types gets a raised QueryError, not a
    silently "fixed" result.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str, str], CacheHit] = {}

    def get(
        self,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
        *,
        ttl: timedelta | None = None,
    ) -> CacheHit | None:
        del ttl
        return self._store.get((source_hash, query_hash, variables_hash))

    def put(
        self,
        source_hash: str,
        query_hash: str,
        variables_hash: str,
        outcome: Any,
        **kwargs: Any,
    ) -> None:
        del kwargs
        if isinstance(outcome, Exception):
            return
        rows = [
            {
                k: v.isoformat() if isinstance(v, (date, datetime)) else v
                for k, v in row.items()
            }
            for row in outcome
        ]
        self._store[(source_hash, query_hash, variables_hash)] = CacheHit(
            rows=rows, written_at=datetime.now(UTC)
        )

    def clear(self, source_hash: str, query_hash: str, variables_hash: str) -> None:
        self._store.pop((source_hash, query_hash, variables_hash), None)

    def close(self) -> None:
        pass

    def supports_cache_refs(self) -> bool:
        return True


class TestTemporalKeyThroughStringCoercingCache:
    """A cache backend that violates the type-fidelity contract — stores
    date/datetime as str and never restores the type on `get()` — must make
    the merge raise, not silently misorder rows. This used to be the shape
    round 3's `_best_effort_temporal_parse` papered over; that guess broke a
    genuinely-VARCHAR incremental column (TestVarcharIncrementalKeySurvivesRealCache),
    so the executor no longer guesses and the contract violation surfaces."""

    def test_datetime_key_warm_render_raises_on_type_disagreement(
        self, tmp_path
    ) -> None:
        del tmp_path
        from dbt_charts.core.diagnostics.execution import QueryError

        cache = _StringCoercingStubCache()
        result = df_compile(_BOARD_TS_INC)

        initial = [
            {"ts": datetime(2026, 8, 20, 1, 0), "val": 20},
            {"ts": datetime(2026, 8, 21, 1, 0), "val": 21},
        ]
        _executor(result, _adapter(initial), cache).execute_query("q")

        tail = [
            {"ts": datetime(2026, 8, 21, 1, 0), "val": 210},
            {"ts": datetime(2026, 8, 22, 1, 0), "val": 22},
        ]
        with pytest.raises(QueryError, match="not.*orderable"):
            _executor(result, _adapter(tail), cache).execute_query("q")

    def test_date_key_warm_render_raises_on_type_disagreement(self, tmp_path) -> None:
        del tmp_path
        from dbt_charts.core.diagnostics.execution import QueryError

        cache = _StringCoercingStubCache()
        result = df_compile(_BOARD_TS_INC)

        initial = [
            {"ts": date(2026, 8, 20), "val": 20},
            {"ts": date(2026, 8, 21), "val": 21},
        ]
        _executor(result, _adapter(initial), cache).execute_query("q")

        tail = [
            {"ts": date(2026, 8, 21), "val": 210},
            {"ts": date(2026, 8, 22), "val": 22},
        ]
        with pytest.raises(QueryError, match="not.*orderable"):
            _executor(result, _adapter(tail), cache).execute_query("q")


class TestTzAwareDatetimeWatermark:
    """A tz-aware datetime incremental watermark column must round-trip through the real
    TrivialDuckDBCache with tzinfo intact — BigQuery TIMESTAMP, Postgres
    timestamptz, and Snowflake TIMESTAMP_TZ all return aware datetimes for
    the mainstream incremental key."""

    def test_tz_aware_datetime_round_trips_and_predicate_is_typed(
        self, tmp_path
    ) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        result = df_compile(_BOARD_TS_INC)
        tz = timezone(timedelta(hours=5, minutes=30))

        initial = [{"ts": datetime(2026, 8, 20, 1, 0, tzinfo=tz), "val": 20}]
        _executor(result, _adapter(initial), cache).execute_query("q")

        tail = [{"ts": datetime(2026, 8, 22, 1, 0, tzinfo=tz), "val": 22}]
        ad2 = _adapter(tail)
        merged = _executor(result, ad2, cache).execute_query("q")

        ad2.execute.assert_called_once()
        sql = ad2.execute.call_args[0][0].sql
        predicate = sqlglot.parse_one(sql).find(exp.GTE)
        assert predicate is not None
        assert isinstance(predicate.expression, exp.Cast)
        assert predicate.expression.to.this == exp.DataType.Type.TIMESTAMPTZ

        assert len(merged) == 2
        assert all(r["ts"].tzinfo is not None for r in merged)


class TestUnparseableCachedTemporalKeyRaises:
    """A cached string that fails strict ISO parsing against the tail's real
    date/datetime key type must raise QueryError — never a silent skip that
    leaves the row's key un-normalized in the merge/sort."""

    def test_unparseable_cached_datetime_string_raises(self) -> None:
        from dbt_charts.core.diagnostics.execution import QueryError
        from dbt_charts.core.execute.executor import _merge_incremental_rows

        tail_rows = [{"ts": datetime(2026, 8, 22, 1, 0), "val": 30}]
        prior_rows = [{"ts": "not-a-timestamp", "val": 10}]
        with pytest.raises(QueryError, match="ts"):
            _merge_incremental_rows(tail_rows, prior_rows, "ts", "q")


# ─── CRITICAL (round 4 regression): genuinely-VARCHAR incremental column ────


class _SqlSpyRegistry:
    """Wraps a real AdapterRegistry, recording each executed query's SQL.

    Everything except `execute` delegates straight through — this is a real
    end-to-end render (real FilesystemProject, real DuckDB warehouse file,
    real build_adapter_registry, real TrivialDuckDBCache), not a mock. The
    spy exists only to let the test assert the tail predicate's shape
    without re-parsing warehouse internals.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.sqls: list[str] = []

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def execute(self, query, *args, **kwargs):
        self.sqls.append(query.sql)
        return self._inner.execute(query, *args, **kwargs)


def _varchar_events_project(tmp_path: Path, initial_values: list[str]):
    """A real project on a real DuckDB file with a genuinely-VARCHAR `ts`
    column seeded with *initial_values* — no test doubles anywhere."""
    db_path = tmp_path / "warehouse.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE events (ts VARCHAR, val INTEGER)")
    for i, v in enumerate(initial_values):
        conn.execute("INSERT INTO events VALUES (?, ?)", [v, i])
    conn.close()

    (tmp_path / "dbt_charts.yml").write_text(
        f"sources:\n  db:\n    type: duckdb\n    path: {db_path}\n"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "board.yml").write_text(_BOARD_TS_INC)

    project = FilesystemProject(tmp_path)
    compiled = compile_file(project.path("charts/board.yml").read_board())
    assert compiled.board is not None, compiled.errors
    registry = _SqlSpyRegistry(build_adapter_registry(project, profile_type="duckdb"))
    return db_path, compiled, registry


def _insert_event(db_path: Path, value: str, val: int) -> None:
    conn = duckdb.connect(str(db_path))
    conn.execute("INSERT INTO events VALUES (?, ?)", [value, val])
    conn.close()


class TestVarcharIncrementalKeySurvivesRealCache:
    """A genuinely-VARCHAR incremental watermark column holding ISO-date-shaped values
    must never be reinterpreted as DATE. Reviewer round-4 CRITICAL: sniffing
    the cached watermark string's *shape* (`_best_effort_temporal_parse`,
    since deleted) converted a VARCHAR watermark to a `date`, and the tail
    predicate's `CAST('2026-08-22' AS DATE)` against a VARCHAR column made
    DuckDB raise `Cannot compare VARCHAR and DATE` on every warm render,
    permanently (the persistent cache entry keeps its str keys).

    Driven through a real FilesystemProject + real DuckDB warehouse file +
    real build_adapter_registry + real TrivialDuckDBCache — a mock adapter
    never runs the tail SQL through DuckDB's binder, so it cannot see this
    bug; only real execution can.
    """

    @pytest.mark.parametrize(
        ("initial_values", "new_values"),
        [
            pytest.param(
                ["2026-08-20", "2026-08-21", "2026-08-22"],
                ["2026-08-23", "2026-08-24", "2026-08-25"],
                id="iso_date",
            ),
            pytest.param(
                ["20260820", "20260821", "20260822"],
                ["20260823", "20260824", "20260825"],
                id="compact_yyyymmdd",
            ),
        ],
    )
    def test_four_renders_succeed_with_string_literal_predicate(
        self, tmp_path, initial_values, new_values
    ) -> None:
        db_path, compiled, registry = _varchar_events_project(tmp_path, initial_values)
        cache = TrivialDuckDBCache(db_path=tmp_path / "cache.duckdb")

        def _render() -> list[dict]:
            executor = Executor(
                compiled.board,
                adapter_registry=registry,
                query_registry=compiled.query_registry,
                result_cache=cache,
            )
            return executor.execute_query("q")

        render1 = _render()
        assert len(render1) == 3

        for i, new_value in enumerate(new_values, start=2):
            _insert_event(db_path, new_value, val=100 + i)
            merged = _render()
            assert len(merged) == 2 + i  # grows by one row each render
            tail_sql = registry.sqls[-1]
            predicate = sqlglot.parse_one(tail_sql).find(exp.GTE)
            assert predicate is not None, tail_sql
            assert isinstance(predicate.expression, exp.Literal)
            assert predicate.expression.is_string
            assert "CAST" not in tail_sql


class TestEmptyStringIncrementalRejected:
    """`incremental: ""` must be rejected at compile, not compile clean and
    silently full-refresh while fragmenting the cache key."""

    def _board(self, value: str) -> str:
        return f"""\
title: T
queries:
  q:
    sql: SELECT ts, val FROM events
    source: db
    incremental: "{value}"
charts:
  c:
    query: q
    type: line
    x: ts
    y: val
rows:
  - c
"""

    def test_empty_string_incremental_is_a_compile_error(self) -> None:
        result = df_compile(self._board(""))
        assert not result.success
        assert any("watermark column" in str(e) for e in result.errors)

    def test_whitespace_incremental_is_a_compile_error(self) -> None:
        result = df_compile(self._board("  "))
        assert not result.success
        assert any("watermark column" in str(e) for e in result.errors)
