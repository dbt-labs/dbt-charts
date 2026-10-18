"""Tests for the session-owned CSV/JSON/Parquet FileSourceMaterializer.

TDD order: each test was written before its implementation existed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.source import (
    CsvSourceConfig,
    JsonSourceConfig,
    ParquetSourceConfig,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import ERR_RESULT_DUPLICATE_COLUMN
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.file_source_materializer import FileSourceMaterializer
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(
    tmp_path: Path,
    files: dict[str, str],
    local_project: Callable[..., FilesystemProject],
) -> FilesystemProject:
    """Create a Project with the given relpath→content mapping on disk."""
    for relpath, content in files.items():
        dest = tmp_path / relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    return local_project(tmp_path)


def _make_board(
    sources: dict[str, Any],
    queries: dict[str, Any],
) -> Board:
    """Build a minimal Board from source configs and query objects."""

    board_yaml: dict[str, Any] = {}
    if sources:
        board_yaml["sources"] = sources
    board_yaml["queries"] = {
        name: {"sql": q.sql, "source": q.source} for name, q in queries.items()
    }
    # Compile via the normalizer so we get a proper Board
    import yaml

    board_text = yaml.dump(board_yaml)
    from dbt_charts.core.compile import compile

    result = compile(board_text)
    assert result.board is not None
    return result.board


def _csv_source(files: dict[str, str], **kw: Any) -> CsvSourceConfig:
    return CsvSourceConfig(type="csv", files=files, **kw)


def _json_source(files: dict[str, str], **kw: Any) -> JsonSourceConfig:
    return JsonSourceConfig(type="json", files=files, **kw)


def test_importing_materializer_does_not_import_duckdb() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from dbt_charts.core.execute.file_source_materializer import "
            "FileSourceMaterializer; import sys; "
            "assert 'duckdb' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_exact_file_version_is_read_once_per_materializer(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _make_project(
        tmp_path, {"data/sales.csv": "amount\n1\n2\n"}, local_project
    )
    source = _csv_source({"sales": "data/sales.csv"})
    materializer = FileSourceMaterializer(project)
    original = project.read_bytes
    reads: list[str] = []

    def counting_read(relpath: str) -> bytes:
        reads.append(relpath)
        return original(relpath)

    monkeypatch.setattr(project, "read_bytes", counting_read)

    assert materializer.materialize_and_run(
        source, "SELECT SUM(amount) AS total FROM sales", {}, "sales"
    ) == [{"total": 3}]
    assert materializer.materialize_and_run(
        source, "SELECT COUNT(*) AS total FROM sales", {}, "sales"
    ) == [{"total": 2}]
    assert reads == ["data/sales.csv"]


def test_duplicate_result_columns_keep_their_registered_code(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _make_project(tmp_path, {"data/sales.csv": "amount\n1\n"}, local_project)
    materializer = FileSourceMaterializer(project)
    with pytest.raises(DbtChartsError) as excinfo:
        materializer.materialize_and_run(
            _csv_source({"sales": "data/sales.csv"}),
            "SELECT amount AS a, amount AS a FROM sales",
            {},
            "sales",
        )
    assert excinfo.value.code is ERR_RESULT_DUPLICATE_COLUMN


def test_changed_file_version_replaces_loaded_table(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _make_project(tmp_path, {"data/sales.csv": "amount\n1\n"}, local_project)
    source = _csv_source({"sales": "data/sales.csv"})
    materializer = FileSourceMaterializer(project)

    assert materializer.materialize_and_run(
        source, "SELECT SUM(amount) AS total FROM sales", {}, "sales"
    ) == [{"total": 1}]

    path = tmp_path / "data/sales.csv"
    path.write_text("amount\n9\n")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))

    assert materializer.materialize_and_run(
        source, "SELECT SUM(amount) AS total FROM sales", {}, "sales"
    ) == [{"total": 9}]


def test_failed_replacement_keeps_new_version_retryable(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _make_project(
        tmp_path, {"data/rows.json": '[{"value": 1}]'}, local_project
    )
    source = _json_source({"rows": "data/rows.json"})
    materializer = FileSourceMaterializer(project)
    assert materializer.materialize_and_run(
        source, "SELECT value FROM rows", {}, "rows"
    ) == [{"value": 1}]

    loaded_before = materializer._loaded.copy()
    source_hash, table_name = next(iter(loaded_before))
    physical = materializer._physical_name(source_hash, table_name)
    path = tmp_path / "data/rows.json"
    path.write_text('[{"value": 2}]')
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))

    connection = materializer._connection()

    class FailPhysicalReplacement:
        def __init__(self) -> None:
            self.failed = False

        def __getattr__(self, name: str) -> Any:
            return getattr(connection, name)

        def execute(self, query: str, parameters: Any = None) -> Any:
            if (
                query.startswith(f'CREATE OR REPLACE TABLE "{physical}"')
                and not self.failed
            ):
                self.failed = True
                raise RuntimeError("injected replacement failure")
            return connection.execute(query, parameters)

    failing_connection = FailPhysicalReplacement()
    monkeypatch.setattr(materializer, "_connection", lambda: failing_connection)

    with pytest.raises(RuntimeError, match="injected replacement failure"):
        materializer.materialize_and_run(source, "SELECT value FROM rows", {}, "rows")
    assert failing_connection.failed
    assert connection.execute(f'SELECT value FROM "{physical}"').fetchall() == [(1,)]
    assert materializer._loaded == loaded_before
    assert materializer.materialize_and_run(
        source, "SELECT value FROM rows", {}, "rows"
    ) == [{"value": 2}]


# ---------------------------------------------------------------------------
# 1. CSV: materialize and SQL-query
# ---------------------------------------------------------------------------


class TestCsvMaterialize:
    def test_csv_single_table_select_all(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _make_project(
            tmp_path,
            {"data/sales.csv": "region,amount\nNorth,100\nSouth,200\nNorth,150\n"},
            local_project,
        )
        source = _csv_source({"sales": "data/sales.csv"})
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source, "SELECT * FROM sales ORDER BY amount", {}, "sales"
        )

        # ORDER BY amount ASC: 100 (North), 150 (North), 200 (South)
        assert [r["region"] for r in rows] == ["North", "North", "South"]
        assert [r["amount"] for r in rows] == [100, 150, 200]

    def test_unknown_column_names_the_files_columns(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _make_project(
            tmp_path,
            {"data/sales.csv": "region,amount\nNorth,100\n"},
            local_project,
        )
        mat = FileSourceMaterializer(project)

        with pytest.raises(QueryError) as exc_info:
            mat.materialize_and_run(
                _csv_source({"sales": "data/sales.csv"}),
                "SELECT amoun FROM sales",
                {},
                "sales",
            )

        assert 'Did you mean "amount"?' in str(exc_info.value)

    def test_csv_where_filter(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _make_project(
            tmp_path,
            {"data/sales.csv": "region,amount\nNorth,100\nSouth,200\nNorth,150\n"},
            local_project,
        )
        source = _csv_source({"sales": "data/sales.csv"})
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source,
            "SELECT region, amount FROM sales WHERE region = 'North'",
            {},
            "sales",
        )
        assert len(rows) == 2
        assert all(r["region"] == "North" for r in rows)

    def test_csv_delimiter_option(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _make_project(
            tmp_path,
            {"data/pipe.csv": "city|pop\nOslo|700000\nBergen|280000\n"},
            local_project,
        )
        source = _csv_source({"cities": "data/pipe.csv"}, delimiter="|")
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source, "SELECT city FROM cities ORDER BY city", {}, "cities"
        )
        assert [r["city"] for r in rows] == ["Bergen", "Oslo"]


# ---------------------------------------------------------------------------
# 2. JSON: materialize and SQL-query
# ---------------------------------------------------------------------------


class TestJsonMaterialize:
    def test_authored_table_name_cannot_collide_with_private_staging(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _make_project(
            tmp_path,
            {
                "data/first.json": '[{"value": 1}]',
                "data/second.json": '[{"value": 2}]',
            },
            local_project,
        )
        source = _json_source(
            {
                "_dct_file_rows": "data/first.json",
                "second": "data/second.json",
            }
        )

        rows = FileSourceMaterializer(project).materialize_and_run(
            source,
            "SELECT value FROM _dct_file_rows UNION ALL SELECT value FROM second "
            "ORDER BY value",
            {},
            "values",
        )

        assert rows == [{"value": 1}, {"value": 2}]

    def test_heterogeneous_values_follow_row_binding_coercion(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _make_project(
            tmp_path,
            {"data/values.json": '[{"v": "1"}, {"v": 2}]'},
            local_project,
        )
        source = _json_source({"values": "data/values.json"})

        rows = FileSourceMaterializer(project).materialize_and_run(
            source,
            "SELECT v, typeof(v) AS type FROM values ORDER BY v",
            {},
            "values",
        )

        assert rows == [
            {"v": "1", "type": "VARCHAR"},
            {"v": "2", "type": "VARCHAR"},
        ]

    def test_nested_values_remain_json_columns(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _make_project(
            tmp_path,
            {"data/values.json": '[{"object": {"a": 1}, "array": [1, 2]}]'},
            local_project,
        )
        source = _json_source({"values": "data/values.json"})

        rows = FileSourceMaterializer(project).materialize_and_run(
            source,
            'SELECT "object", "array", typeof("object") AS object_type, '
            'typeof("array") AS array_type FROM values',
            {},
            "values",
        )

        assert rows == [
            {
                "object": {"a": 1},
                "array": [1, 2],
                "object_type": "JSON",
                "array_type": "JSON",
            }
        ]

    def test_json_array_of_objects(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        project = _make_project(
            tmp_path, {"data/users.json": json.dumps(data)}, local_project
        )
        source = _json_source({"users": "data/users.json"})
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source, "SELECT name FROM users ORDER BY id", {}, "users"
        )
        assert [r["name"] for r in rows] == ["Alice", "Bob"]

    def test_ndjson_detected_by_extension(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """.jsonl extension triggers NDJSON parser."""
        ndjson = '{"id": 1}\n{"id": 2}\n'
        project = _make_project(tmp_path, {"data/items.jsonl": ndjson}, local_project)
        source = _json_source({"items": "data/items.jsonl"})
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source, "SELECT id FROM items ORDER BY id", {}, "items"
        )
        assert [r["id"] for r in rows] == [1, 2]

    def test_json_parse_error_not_confused_with_ndjson(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Malformed .json file reports a JSON parse error, not an NDJSON error."""
        bad_json = '{"key": "value",}'  # trailing comma — invalid JSON
        project = _make_project(tmp_path, {"data/bad.json": bad_json}, local_project)
        source = _json_source({"data": "data/bad.json"})
        mat = FileSourceMaterializer(project)

        with pytest.raises(ValueError, match="JSON file is not valid JSON"):
            mat.materialize_and_run(source, "SELECT * FROM data", {}, "data")

    def test_union_by_name_preserves_numeric_type_when_subset_sorts_first(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Numeric column absent in the first (alphabetically-first) file stays BIGINT.

        This is the critical regression direction: if type inference reads only row 0,
        the None-padded value would make the column VARCHAR, breaking numeric queries.
        """
        # a/ sorts before b/ — so a/summary.json is the first file parsed.
        # a/ does NOT have 'score'; b/ has score=42 (integer).
        file_a = '[{"id": 1, "name": "Alice"}]'
        file_b = '[{"id": 2, "name": "Bob", "score": 42}]'
        project = _make_project(
            tmp_path,
            {
                "data/runs/a/summary.json": file_a,
                "data/runs/b/summary.json": file_b,
            },
            local_project,
        )
        source = _json_source(
            {"summaries": "data/runs/*/summary.json"}, union_by_name=True
        )
        mat = FileSourceMaterializer(project)

        # If score were typed VARCHAR due to row-0 None, SUM(score) would error.
        rows = mat.materialize_and_run(
            source,
            "SELECT SUM(score) AS total FROM summaries",
            {},
            "summaries",
        )
        assert rows[0]["total"] == 42

    def test_union_by_name_pads_missing_columns(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """union_by_name=True pads rows with None for columns absent in that file."""
        file_a = '[{"id": 1, "name": "Alice", "extra": "x"}]'
        file_b = '[{"id": 2, "name": "Bob"}]'
        project = _make_project(
            tmp_path,
            {
                "data/runs/a/summary.json": file_a,
                "data/runs/b/summary.json": file_b,
            },
            local_project,
        )
        source = _json_source(
            {"summaries": "data/runs/*/summary.json"}, union_by_name=True
        )
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source,
            "SELECT id, name, extra FROM summaries ORDER BY id",
            {},
            "summaries",
        )
        assert rows[0] == {"id": 1, "name": "Alice", "extra": "x"}
        assert rows[1]["id"] == 2
        assert rows[1]["name"] == "Bob"
        assert rows[1]["extra"] is None


# ---------------------------------------------------------------------------
# 3. Multi-table JOIN in a single source namespace
# ---------------------------------------------------------------------------


class TestMultiTableJoin:
    def test_two_csv_tables_join(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _make_project(
            tmp_path,
            {
                "data/orders.csv": "order_id,customer_id,amount\n1,10,500\n2,20,300\n",
                "data/customers.csv": "customer_id,name\n10,Alice\n20,Bob\n",
            },
            local_project,
        )
        source = _csv_source(
            {
                "orders": "data/orders.csv",
                "customers": "data/customers.csv",
            }
        )
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source,
            "SELECT c.name, o.amount FROM orders o JOIN customers c ON o.customer_id = c.customer_id ORDER BY o.order_id",
            {},
            "orders",
        )
        assert len(rows) == 2
        assert rows[0]["name"] == "Alice"
        assert rows[1]["name"] == "Bob"


# ---------------------------------------------------------------------------
# 4. External-access blocked: read_csv('http://...') must fail
# ---------------------------------------------------------------------------


class TestExternalAccessBlocked:
    def test_file_readers_never_run_where_author_sql_runs(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DuckDB's file readers run on a private scratch connection; the
        connection that runs author SQL is sandboxed and never sees a reader."""
        import duckdb

        project = _make_project(
            tmp_path, {"data/sales.csv": "x,y\n1,2\n"}, local_project
        )
        source = _csv_source({"sales": "data/sales.csv"})
        real_connect = duckdb.connect
        DuckDBConfig = dict[str, str | bool | int | float | list[str]]
        statements: dict[str, list[str]] = {"sandboxed": [], "scratch": []}

        class RecordingConnection:
            def __init__(
                self, connection: duckdb.DuckDBPyConnection, log: list[str]
            ) -> None:
                self._connection = connection
                self._log = log

            def __getattr__(self, name: str) -> Any:
                return getattr(self._connection, name)

            def execute(
                self, query: str, parameters: Any = None
            ) -> duckdb.DuckDBPyConnection:
                self._log.append(query.lower())
                return self._connection.execute(query, parameters)

            def close(self) -> None:
                self._connection.close()

        def recording_connect(
            database: str = ":memory:",
            *,
            config: DuckDBConfig | None = None,
        ) -> RecordingConnection:
            sandboxed = (
                config is not None and config["enable_external_access"] == "false"
            )
            log = statements["sandboxed" if sandboxed else "scratch"]
            return RecordingConnection(real_connect(database, config=config or {}), log)

        monkeypatch.setattr(duckdb, "connect", recording_connect)

        materializer = FileSourceMaterializer(project)
        assert materializer.materialize_and_run(
            source, "SELECT SUM(x) AS total FROM sales", {}, "sales"
        ) == [{"total": 1}]
        assert any("read_csv" in q for q in statements["scratch"])
        assert not any("sum(x)" in q for q in statements["scratch"])
        assert any("sum(x)" in q for q in statements["sandboxed"])
        assert not any(
            reader in q
            for q in statements["sandboxed"]
            for reader in ("read_csv", "read_json", "read_parquet")
        )

    def test_http_read_csv_in_sql_is_blocked(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Author SQL that tries to read_csv from HTTP must be rejected."""
        project = _make_project(
            tmp_path, {"data/sales.csv": "x,y\n1,2\n"}, local_project
        )
        source = _csv_source({"sales": "data/sales.csv"})
        mat = FileSourceMaterializer(project)

        with pytest.raises(
            QueryError, match="(?i)(external|access|http|blocked|forbidden)"
        ):
            mat.materialize_and_run(
                source,
                "SELECT * FROM read_csv('http://evil.example.com/data.csv')",
                {},
                "sales",
            )


# ---------------------------------------------------------------------------
# 5. One materializer serializes alias installation and execution
def test_parallel_queries_with_same_alias_are_isolated(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _make_project(
        tmp_path,
        {
            "data/a.csv": "value\na\n",
            "data/b.csv": "value\nb\n",
        },
        local_project,
    )
    materializer = FileSourceMaterializer(project)
    source_a = _csv_source({"shared": "data/a.csv"})
    source_b = _csv_source({"shared": "data/b.csv"})

    def run(source: CsvSourceConfig) -> str:
        return materializer.materialize_and_run(
            source, "SELECT value FROM shared", {}, "source"
        )[0]["value"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [source_a, source_b] * 10))

    assert results == ["a", "b"] * 10


# ---------------------------------------------------------------------------
# 6. Parquet: materialize and SQL-query
# ---------------------------------------------------------------------------


class TestParquetMaterialize:
    def test_parquet_table_queryable(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            pytest.skip("pyarrow not installed")

        table = pa.table({"product": ["A", "B", "C"], "sales": [10, 20, 30]})
        parquet_path = tmp_path / "data" / "products.parquet"
        parquet_path.parent.mkdir(parents=True)
        pq.write_table(  # type: ignore[no-untyped-call]  # type-state: type_ignore — pyarrow's stubs are placeholders; upstream gap
            table, parquet_path
        )

        project = local_project(tmp_path)
        source = ParquetSourceConfig(
            type="parquet", files={"products": "data/products.parquet"}
        )
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source,
            "SELECT product, sales FROM products ORDER BY sales DESC",
            {},
            "products",
        )
        assert len(rows) == 3
        assert rows[0]["product"] == "C"


# ---------------------------------------------------------------------------
# 7. Executor integration: SqlQuery with file source routes via materializer
# ---------------------------------------------------------------------------


class TestExecutorIntegration:
    def test_executor_routes_file_source_sql_query(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """End-to-end: Executor.execute_query picks up a CSV file source."""
        csv_content = "month,revenue\nJan,1000\nFeb,1500\nMar,1200\n"
        project = _make_project(
            tmp_path, {"data/revenue.csv": csv_content}, local_project
        )

        # D-12: an inline file-path `source:` — no project-level sources
        # registry entry needed. Table name is the file's stem ("revenue").
        # Needs at least one chart for the normalizer to accept it.
        board_yaml = """
queries:
  monthly:
    type: sql
    sql: "SELECT month, revenue FROM revenue ORDER BY revenue DESC"
    source: ./data/revenue.csv
charts:
  rev:
    type: kpi
    query: monthly
    value: revenue
"""
        from dbt_charts.core.compile import compile as df_compile

        result = df_compile(board_yaml, base_dir=project.directory())
        assert result.success, f"Compile failed: {result.errors}"
        board = result.board
        assert board is not None

        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        registry = build_adapter_registry(project)
        cache = TrivialDuckDBCache()
        mat = FileSourceMaterializer(project)
        executor = Executor(
            board,
            registry,
            result_cache=cache,
            file_materializer=mat,
        )

        rows = executor.execute_query("monthly")
        assert len(rows) == 3
        assert rows[0]["month"] == "Feb"  # highest revenue
        assert rows[0]["revenue"] == 1500

    def test_file_content_change_produces_fresh_rows(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Editing a data file produces updated rows on the next executor call.

        Regression: the outer result cache (keyed by source config, not file
        content) must NOT shadow the materializer's versioned table.
        If it does, the second query returns stale old_rows even after the file
        is updated.
        """
        csv_v1 = "month,revenue\nJan,1000\n"
        csv_v2 = "month,revenue\nJan,9999\n"

        data_file = tmp_path / "data" / "revenue.csv"
        data_file.parent.mkdir(parents=True)
        data_file.write_text(csv_v1)

        project = _make_project(tmp_path, {}, local_project)  # file already on disk

        # D-12: inline file-path source — table name is the stem ("revenue").
        board_yaml = """
queries:
  monthly:
    type: sql
    sql: "SELECT month, revenue FROM revenue"
    source: ./data/revenue.csv
charts:
  rev:
    type: kpi
    query: monthly
    value: revenue
"""
        from dbt_charts.core.compile import compile as df_compile
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        result = df_compile(board_yaml, base_dir=project.directory())
        assert result.success
        board = result.board
        assert board is not None
        registry = build_adapter_registry(project)

        # First render — v1 content
        cache = TrivialDuckDBCache()
        mat = FileSourceMaterializer(project)
        executor = Executor(board, registry, result_cache=cache, file_materializer=mat)
        rows_v1 = executor.execute_query("monthly")
        assert rows_v1[0]["revenue"] == 1000

        # Update the file on disk
        data_file.write_text(csv_v2)

        # Second render — same persistent cache, new file content.
        # Must return v2 rows, not stale v1 rows.
        mat2 = FileSourceMaterializer(project)
        executor2 = Executor(
            board, registry, result_cache=cache, file_materializer=mat2
        )
        rows_v2 = executor2.execute_query("monthly")
        assert rows_v2[0]["revenue"] == 9999, (
            "Expected fresh rows after file content change, got stale rows"
        )


# ---------------------------------------------------------------------------
# 8. project.read_bytes routes through the file plugin
# ---------------------------------------------------------------------------


class TestProjectReadBytes:
    def test_read_bytes_returns_file_content(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Project.read_bytes delegates to the file plugin."""
        data_path = tmp_path / "data" / "test.csv"
        data_path.parent.mkdir(parents=True)
        data_path.write_bytes(b"col1,col2\n1,2\n")
        project = local_project(tmp_path)
        result = project.read_bytes("data/test.csv")
        assert result == b"col1,col2\n1,2\n"


# ---------------------------------------------------------------------------
# 9. Variable substitution in file-source SQL
# ---------------------------------------------------------------------------


class TestVariableSubstitution:
    def test_file_source_quoted_variable_substitution(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Quoted {{ var }} in file-source SQL must substitute, not match literal string.

        Before fix: WHERE region = '{{ region }}' with region='North' silently
        returns empty rows because DuckDB matches the literal string '{{ region }}'.
        After fix: render_parameterized converts to '$1' with params=['North'], so
        the query correctly returns only matching rows.
        """
        project = _make_project(
            tmp_path,
            {"data/sales.csv": "region,amount\nNorth,100\nSouth,200\nNorth,150\n"},
            local_project,
        )
        source = _csv_source({"sales": "data/sales.csv"})
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source,
            "SELECT region, amount FROM sales WHERE region = '{{ region }}'",
            {"region": "North"},
            "sales",
        )

        # Should return only North rows, not empty (pre-fix) or all rows
        assert len(rows) == 2
        assert all(r["region"] == "North" for r in rows)


# ---------------------------------------------------------------------------
# 10. Project-level CSV sources route through FileSourceMaterializer
# ---------------------------------------------------------------------------


class TestProjectLevelFileSources:
    def test_executor_routes_project_level_csv_source(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Executor routes a query against a project-level CSV source through
        FileSourceMaterializer, not through the SQL adapter.

        Regression: before fix, `source: kpi_specimens` (defined in
        dbt_charts.yml as type: csv) fell through to the SQL adapter which
        failed with "csv SQL execution failed: Unknown dialect 'csv'".
        """
        csv_content = "name,value\nalpha,10\nbeta,20\n"
        dbt_charts_yml = "sources:\n  kpi_data:\n    type: csv\n    files:\n      kpi_data: data/kpi.csv\n"
        project = _make_project(
            tmp_path,
            {
                "data/kpi.csv": csv_content,
                "dbt_charts.yml": dbt_charts_yml,
            },
            local_project,
        )

        board_yaml = """
queries:
  q:
    type: sql
    sql: "SELECT name, value FROM kpi_data ORDER BY value"
    source: kpi_data
charts:
  c:
    type: kpi
    query: q
    value: value
"""
        from dbt_charts.core.compile import compile as df_compile

        result = df_compile(board_yaml, base_dir=project.directory())
        assert result.success, f"Compile failed: {result.errors}"
        board = result.board
        assert board is not None

        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        registry = build_adapter_registry(project)
        cache = TrivialDuckDBCache()
        mat = FileSourceMaterializer(project)
        executor = Executor(board, registry, result_cache=cache, file_materializer=mat)

        rows = executor.execute_query("q")
        assert len(rows) == 2, f"Expected 2 rows, got error or wrong count: {rows}"
        assert rows[0]["name"] == "alpha"
        assert rows[1]["name"] == "beta"


# ---------------------------------------------------------------------------
# 10. File-table type fidelity
# ---------------------------------------------------------------------------


class TestFileTableTypeFidelity:
    """A file table exposes the file's own column types to SQL — the result
    cache's storage encoding (a uniformly-Decimal column kept as exact text
    inside the payload) must never leak into it.

    The query-result half of that contract — a uniformly-Decimal *query* result
    still round-tripping exactly through ``get()`` — is pinned by
    ``tests/core/test_duckdb_cache_decimal256.py::
    test_duckdb_cache_put_with_normal_decimal_round_trips``.
    """

    @staticmethod
    def _decimal_parquet_project(
        tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> tuple[FilesystemProject, ParquetSourceConfig]:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.table(
            {
                "amount": pa.array(
                    [Decimal("1.10"), Decimal("2.20")], type=pa.decimal128(18, 2)
                )
            }
        )
        parquet_path = tmp_path / "data" / "orders.parquet"
        parquet_path.parent.mkdir(parents=True)
        pq.write_table(  # type: ignore[no-untyped-call]  # type-state: type_ignore — pyarrow's stubs are placeholders; upstream gap
            table, parquet_path
        )
        return local_project(tmp_path), ParquetSourceConfig(
            type="parquet", files={"orders": "data/orders.parquet"}
        )

    def test_parquet_decimal_column_sums_exactly(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project, source = self._decimal_parquet_project(tmp_path, local_project)
        mat = FileSourceMaterializer(project)

        summed = mat.materialize_and_run(
            source, "SELECT SUM(amount) AS s FROM orders", {}, "orders"
        )
        assert summed[0]["s"] == Decimal("3.30")

    def test_decimal_type_survives_session_reuse(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A loaded parsed table retains the source's type for the session."""
        project, source = self._decimal_parquet_project(tmp_path, local_project)
        mat = FileSourceMaterializer(project)
        mat.materialize_and_run(source, "SELECT * FROM orders", {}, "orders")
        for _ in range(2):
            rows = mat.materialize_and_run(
                source,
                "SELECT SUM(amount) AS s, ANY_VALUE(typeof(amount)) AS t FROM orders",
                {},
                "orders",
            )
            assert rows[0] == {"s": Decimal("3.30"), "t": "DECIMAL(18,2)"}

    def test_csv_date_bool_and_int_columns_keep_their_types(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _make_project(
            tmp_path,
            {
                "data/events.csv": "day,active,n\n2026-01-02,true,5\n2026-01-03,false,6\n"
            },
            local_project,
        )
        source = _csv_source({"events": "data/events.csv"})
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source,
            "SELECT typeof(day) AS d, typeof(active) AS a, typeof(n) AS n "
            "FROM events LIMIT 1",
            {},
            "events",
        )
        assert rows[0] == {"d": "DATE", "a": "BOOLEAN", "n": "BIGINT"}

    def test_all_null_csv_column_is_varchar_and_reads_back_null(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _make_project(
            tmp_path,
            {"data/sparse.csv": "region,note\nNorth,\nSouth,\n"},
            local_project,
        )
        source = _csv_source({"sparse": "data/sparse.csv"})
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source,
            "SELECT region, note, typeof(note) AS t, coalesce(note, '-') AS c "
            "FROM sparse WHERE note IS DISTINCT FROM 'x' ORDER BY region",
            {},
            "sparse",
        )
        assert [r["region"] for r in rows] == ["North", "South"]
        assert all(r["note"] is None for r in rows)
        assert rows[0]["t"] == "VARCHAR"
        assert rows[0]["c"] == "-"

    def test_glob_files_with_conflicting_column_types_raise(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Same column name, genuinely different types across a glob's files:
        a stamped error naming the two files that disagree, not a raw engine
        error the executor would stamp ERR-INTERNAL.

        Three shards, so the blamed pair is the offender and its neighbor —
        shard 1 is not in the message at all."""
        project = _make_project(
            tmp_path,
            {
                "data/2023/sales.csv": "region,amount\nEast,50\n",
                "data/2024/sales.csv": "region,amount\nNorth,100\n",
                "data/2025/sales.csv": "region,amount\nSouth,1.5\n",
            },
            local_project,
        )
        source = _csv_source({"sales": "data/*/sales.csv"})
        mat = FileSourceMaterializer(project)

        with pytest.raises(DbtChartsError) as excinfo:
            mat.materialize_and_run(source, "SELECT * FROM sales", {}, "sales_source")
        assert excinfo.value.code is not None
        assert excinfo.value.code.code == "ERR-GLOB-SCHEMA-MISMATCH"
        message = str(excinfo.value)
        assert "data/2025/sales.csv" in message
        assert "data/2024/sales.csv" in message
        assert "data/2023/sales.csv" not in message
        assert "amount" in message

    def test_glob_shard_with_an_all_null_column_takes_the_sibling_type(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """An empty column in one shard is ordinary: it has no type of its own,
        and it must adopt the sibling shard's type rather than fail the render."""
        project = _make_project(
            tmp_path,
            {
                "data/2024/sales.csv": "region,note\nNorth,\n",
                "data/2025/sales.csv": "region,note\nSouth,ok\n",
            },
            local_project,
        )
        source = _csv_source({"sales": "data/*/sales.csv"})
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source,
            "SELECT region, note, typeof(note) AS t FROM sales ORDER BY region",
            {},
            "sales_source",
        )
        assert [(r["region"], r["note"]) for r in rows] == [
            ("North", None),
            ("South", "ok"),
        ]
        assert rows[0]["t"] == "VARCHAR"

    @pytest.mark.parametrize(
        ("sibling", "expected_type", "expected_value"),
        [
            ("100", "BIGINT", 100),
            ("true", "BOOLEAN", True),
            ("2025-01-01", "DATE", date(2025, 1, 1)),
        ],
    )
    def test_glob_shard_with_an_all_null_column_takes_a_non_text_sibling_type(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        sibling: str,
        expected_type: str,
        expected_value: object,
    ) -> None:
        """The all-null column must reach the glob merge still typed ``null`` so
        ``promote_options="default"`` can give it the sibling's type. Turning it
        into text first makes ``string`` vs ``int64`` a schema mismatch between
        two files that agree — the string-sibling test above cannot see that,
        because ``null → string`` satisfies it either way."""
        project = _make_project(
            tmp_path,
            {
                "data/2024/sales.csv": "region,amount\nNorth,\n",
                "data/2025/sales.csv": f"region,amount\nSouth,{sibling}\n",
            },
            local_project,
        )
        source = _csv_source({"sales": "data/*/sales.csv"})
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source,
            "SELECT region, amount, typeof(amount) AS t FROM sales ORDER BY region",
            {},
            "sales_source",
        )
        assert [(r["region"], r["amount"]) for r in rows] == [
            ("North", None),
            ("South", expected_value),
        ]
        assert rows[0]["t"] == expected_type

    def test_glob_whose_column_is_empty_in_every_shard_is_varchar(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """No sibling to adopt a type from: the merged column is still ``null``
        and gets the VARCHAR the single-file case gets."""
        project = _make_project(
            tmp_path,
            {
                "data/2024/sales.csv": "region,note\nNorth,\n",
                "data/2025/sales.csv": "region,note\nSouth,\n",
            },
            local_project,
        )
        source = _csv_source({"sales": "data/*/sales.csv"})
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source,
            "SELECT coalesce(note, '-') AS n, typeof(note) AS t FROM sales",
            {},
            "sales_source",
        )
        assert {r["n"] for r in rows} == {"-"}
        assert rows[0]["t"] == "VARCHAR"

    @pytest.mark.parametrize(
        "wide_first", [False, True], ids=["narrow-first", "wide-first"]
    )
    @pytest.mark.parametrize(
        ("narrow", "wide", "expected_type"),
        [
            ("int32", "int64", "BIGINT"),
            ("float", "double", "DOUBLE"),
            ("timestamp[ms]", "timestamp[us]", "TIMESTAMP"),
            ("decimal128(18, 2)", "decimal128(38, 2)", "DECIMAL(38,2)"),
        ],
    )
    def test_parquet_shards_differing_only_in_type_width_merge_to_the_wider(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        narrow: str,
        wide: str,
        expected_type: str,
        wide_first: bool,
    ) -> None:
        """Two producers write the same column at different widths (Spark
        emits timestamp[ms], DuckDB timestamp[us]; one month's export is
        DECIMAL(18,2), the next DECIMAL(38,2)). That is not a type conflict:
        the row-dict path loaded every such pair losslessly, and so must the
        typed path — in either shard order."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        def _value(type_name: str) -> object:
            if type_name.startswith("timestamp"):
                return datetime(2025, 1, 1, 12, 0, 0)
            if type_name.startswith("decimal"):
                return Decimal("12.50")
            return 7

        def _arrow_type(type_name: str) -> Any:
            if type_name.startswith("decimal128("):
                precision, scale = type_name[len("decimal128(") : -1].split(",")
                return pa.decimal128(int(precision), int(scale))
            return pa.type_for_alias(type_name)

        # Globs expand sorted, so the shard name decides which side is the
        # accumulated table — both fold directions must widen.
        shards = (
            (("2024", wide), ("2025", narrow))
            if wide_first
            else (
                ("2024", narrow),
                ("2025", wide),
            )
        )
        for shard, type_name in shards:
            path = tmp_path / "data" / shard / "t.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            arrow_type = _arrow_type(type_name)
            pq.write_table(  # type: ignore[no-untyped-call]  # type-state: type_ignore — pyarrow's stubs are placeholders; upstream gap
                pa.table({"v": pa.array([_value(type_name)], type=arrow_type)}),
                path,
            )
        source = ParquetSourceConfig(type="parquet", files={"t": "data/*/t.parquet"})
        mat = FileSourceMaterializer(local_project(tmp_path))

        rows = mat.materialize_and_run(
            source, "SELECT v, typeof(v) AS t FROM t", {}, "t_source"
        )
        assert len(rows) == 2
        assert {r["t"] for r in rows} == {expected_type}
        assert rows[0]["v"] == rows[1]["v"]

    def test_a_nanosecond_shard_does_not_drag_a_far_future_sibling_out_of_range(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """timestamp[ns] has the finest unit and the NARROWEST range (to 2262).
        An SCD2 sentinel like 9999-12-31 in a millisecond shard must not be
        cast to nanoseconds to match an INT96 sibling; DuckDB's TIMESTAMP is
        microseconds, so the merge caps there and the glob renders as on main."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        for shard, unit, value in (
            ("2024", "ms", datetime(9999, 12, 31)),
            ("2025", "ns", datetime(2025, 1, 1)),
        ):
            path = tmp_path / "data" / shard / "t.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(  # type: ignore[no-untyped-call]  # type-state: type_ignore — pyarrow's stubs are placeholders; upstream gap
                pa.table({"valid_to": pa.array([value], type=pa.timestamp(unit))}),
                path,
            )
        source = ParquetSourceConfig(type="parquet", files={"t": "data/*/t.parquet"})
        mat = FileSourceMaterializer(local_project(tmp_path))

        rows = mat.materialize_and_run(
            source,
            "SELECT valid_to, typeof(valid_to) AS t FROM t ORDER BY 1",
            {},
            "t_source",
        )
        assert [r["valid_to"] for r in rows] == [
            datetime(2025, 1, 1),
            datetime(9999, 12, 31),
        ]
        assert rows[0]["t"] == "TIMESTAMP"

    def test_a_nanosecond_remainder_that_microseconds_cannot_hold_is_a_stamped_mismatch(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The cap makes ns → us the one widening cast that can refuse. It must
        refuse inside the merge guard, as a coded mismatch naming both shards —
        never as a raw engine message, since DuckDB's own cast truncates."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        for shard, unit, value in (
            ("2024", "us", 1_735_689_600_000_000),
            ("2025", "ns", 1_735_689_600_000_000_123),
        ):
            path = tmp_path / "data" / shard / "t.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(  # type: ignore[no-untyped-call]  # type-state: type_ignore — pyarrow's stubs are placeholders; upstream gap
                pa.table({"ts": pa.array([value], type=pa.timestamp(unit))}), path
            )
        source = ParquetSourceConfig(type="parquet", files={"t": "data/*/t.parquet"})
        mat = FileSourceMaterializer(local_project(tmp_path))

        with pytest.raises(DbtChartsError) as excinfo:
            mat.materialize_and_run(source, "SELECT ts FROM t", {}, "t_source")
        assert excinfo.value.code is not None
        assert excinfo.value.code.code == "ERR-GLOB-SCHEMA-MISMATCH"
        assert "data/2024/t.parquet" in str(excinfo.value)
        assert "data/2025/t.parquet" in str(excinfo.value)

    def test_glob_shards_with_reordered_headers_union_by_name(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Column order is not part of a glob's contract — the first-row-keys
        check compares sets, so two shards that disagree only on header order
        have always been legal."""
        project = _make_project(
            tmp_path,
            {
                "data/2024/sales.csv": "region,amount\nNorth,100\n",
                "data/2025/sales.csv": "amount,region\n200,South\n",
            },
            local_project,
        )
        source = _csv_source({"sales": "data/*/sales.csv"})
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            source, "SELECT region, amount FROM sales ORDER BY amount", {}, "sales"
        )
        assert rows == [
            {"region": "North", "amount": 100},
            {"region": "South", "amount": 200},
        ]


class TestReaderParity:
    """Cases where DuckDB's readers default differently from what file sources
    have always loaded."""

    def test_an_empty_csv_text_field_is_the_empty_string(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _make_project(
            tmp_path, {"data/t.csv": "id,note,blank\n1,,\n2,ok,\n"}, local_project
        )
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            _csv_source({"t": "data/t.csv"}),
            "SELECT note, blank FROM t ORDER BY id",
            {},
            "t_source",
        )
        assert rows == [{"note": "", "blank": None}, {"note": "ok", "blank": None}]

    def test_missing_value_tokens_are_null_except_in_text(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _make_project(
            tmp_path,
            {"data/t.csv": "x,s,n,f\n1,abc,NA,1.5\nN/A,NA,NA,nan\n2,,NULL,2\n"},
            local_project,
        )
        mat = FileSourceMaterializer(project)

        rows = mat.materialize_and_run(
            _csv_source({"t": "data/t.csv"}),
            "SELECT typeof(x) AS tx, x, s, n, f FROM t",
            {},
            "t_source",
        )
        assert rows == [
            {"tx": "BIGINT", "x": 1, "s": "abc", "n": None, "f": 1.5},
            {"tx": "BIGINT", "x": None, "s": "NA", "n": None, "f": None},
            {"tx": "BIGINT", "x": 2, "s": "", "n": None, "f": 2.0},
        ]

    def test_a_latin_1_csv_decodes(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        path = tmp_path / "data" / "t.csv"
        path.parent.mkdir(parents=True)
        path.write_bytes("city\nZürich\n".encode("latin-1"))
        mat = FileSourceMaterializer(local_project(tmp_path))

        rows = mat.materialize_and_run(
            _csv_source({"t": "data/t.csv"}, encoding="latin-1"),
            "SELECT city FROM t",
            {},
            "t_source",
        )
        assert rows == [{"city": "Zürich"}]

    def test_an_all_null_parquet_column_keeps_its_declared_type(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        path = tmp_path / "data" / "t.parquet"
        path.parent.mkdir(parents=True)
        pq.write_table(  # type: ignore[no-untyped-call]  # type-state: type_ignore — pyarrow's stubs are placeholders; upstream gap
            pa.table({"d": pa.array([None], pa.date32()), "n": pa.array([None])}),
            path,
        )
        mat = FileSourceMaterializer(local_project(tmp_path))

        rows = mat.materialize_and_run(
            ParquetSourceConfig(type="parquet", files={"t": "data/t.parquet"}),
            "SELECT typeof(d) AS d, typeof(n) AS n FROM t",
            {},
            "t_source",
        )
        assert rows == [{"d": "DATE", "n": "VARCHAR"}]

    def test_a_parquet_json_column_stays_json(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        path = tmp_path / "data" / "t.parquet"
        path.parent.mkdir(parents=True)
        pq.write_table(  # type: ignore[no-untyped-call]  # type-state: type_ignore — pyarrow's stubs are placeholders; upstream gap
            pa.table({"j": pa.array(['{"a": 1}'], pa.json_())}), path
        )
        mat = FileSourceMaterializer(local_project(tmp_path))

        rows = mat.materialize_and_run(
            ParquetSourceConfig(type="parquet", files={"t": "data/t.parquet"}),
            "SELECT typeof(j) AS t, j->>'a' AS a FROM t",
            {},
            "t_source",
        )
        assert rows == [{"t": "JSON", "a": "1"}]


class TestParquetTypesDuckDBRefuses:
    """Parquet can declare a decimal wider than DuckDB's 38 digits (its
    FIXED_LEN_BYTE_ARRAY backing allows it, and BigQuery BIGNUMERIC exports use
    it routinely). DuckDB reads such a column as DOUBLE, at some precisions as
    wrong values, so it is refused with a stamped code before the read, at the
    top level or nested. ``float16`` DuckDB reads as FLOAT.
    """

    @staticmethod
    def _write(
        tmp_path: Path, values: list[Decimal], precision: int, scale: int
    ) -> ParquetSourceConfig:
        import pyarrow as pa
        import pyarrow.parquet as pq

        parquet_path = tmp_path / "data" / "wide.parquet"
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(  # type: ignore[no-untyped-call]  # type-state: type_ignore — pyarrow's stubs are placeholders; upstream gap
            pa.table(
                {"amount": pa.array(values, type=pa.decimal256(precision, scale))}
            ),
            parquet_path,
        )
        return ParquetSourceConfig(type="parquet", files={"wide": "data/wide.parquet"})

    def test_values_that_fit_are_still_refused(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The declared precision decides, not the values: DuckDB has no exact
        read for a precision-40 column even when every value would fit."""
        source = self._write(tmp_path, [Decimal("1.10"), Decimal("2.20")], 40, 2)
        mat = FileSourceMaterializer(local_project(tmp_path))

        with pytest.raises(DbtChartsError) as excinfo:
            mat.materialize_and_run(source, "SELECT * FROM wide", {}, "wide_source")
        assert excinfo.value.code is not None
        assert excinfo.value.code.code == "ERR-FILE-SOURCE-DECIMAL-TOO-WIDE"

    def test_a_value_wider_than_38_digits_is_refused(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        source = self._write(tmp_path, [Decimal("1" * 39 + ".00")], 41, 2)
        mat = FileSourceMaterializer(local_project(tmp_path))

        with pytest.raises(DbtChartsError) as excinfo:
            mat.materialize_and_run(source, "SELECT * FROM wide", {}, "wide_source")
        assert excinfo.value.code is not None
        assert excinfo.value.code.code == "ERR-FILE-SOURCE-DECIMAL-TOO-WIDE"
        message = str(excinfo.value)
        assert "amount" in message
        assert "data/wide.parquet" in message
        assert "41" in message
        # The fields ride into `dct render --format json` and the
        # `--diagnostics-json` stream verbatim, so they must be plain JSON.
        json.dumps(excinfo.value.fields)
        dumped = excinfo.value.to_diagnostic(file="charts/x.yml").model_dump(
            mode="json"
        )
        assert dumped["fields"]["column_type"] == "DECIMAL(41,2)"

    @staticmethod
    def _write_parquet(tmp_path: Path, table: Any) -> ParquetSourceConfig:
        import pyarrow.parquet as pq

        parquet_path = tmp_path / "data" / "t.parquet"
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(  # type: ignore[no-untyped-call]  # type-state: type_ignore — pyarrow's stubs are placeholders; upstream gap
            table, parquet_path
        )
        return ParquetSourceConfig(type="parquet", files={"t": "data/t.parquet"})

    def test_float16_column_widens_to_float(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A halffloat column rendered before the Arrow path existed (to_pylist
        gave a float and _infer_type said DOUBLE); widening to float32 is
        lossless and keeps it renderable."""
        import pyarrow as pa

        source = self._write_parquet(
            tmp_path, pa.table({"v": pa.array([1.5, 2.5], type=pa.float16())})
        )
        mat = FileSourceMaterializer(local_project(tmp_path))

        rows = mat.materialize_and_run(
            source,
            "SELECT SUM(v) AS s, ANY_VALUE(typeof(v)) AS t FROM t",
            {},
            "t_source",
        )
        assert rows[0] == {"s": 4.0, "t": "FLOAT"}

    def test_decimal256_nested_in_a_list_is_refused(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        import pyarrow as pa

        source = self._write_parquet(
            tmp_path,
            pa.table(
                {
                    "v": pa.array(
                        [[Decimal("1.10"), Decimal("2.20")]],
                        type=pa.list_(pa.decimal256(40, 2)),
                    )
                }
            ),
        )
        mat = FileSourceMaterializer(local_project(tmp_path))

        with pytest.raises(DbtChartsError) as excinfo:
            mat.materialize_and_run(source, "SELECT * FROM t", {}, "t_source")
        assert excinfo.value.code is not None
        assert excinfo.value.code.code == "ERR-FILE-SOURCE-DECIMAL-TOO-WIDE"

    def test_float16_nested_in_a_struct_reads_as_float(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        import pyarrow as pa

        source = self._write_parquet(
            tmp_path,
            pa.table(
                {
                    "v": pa.array(
                        [{"half": 1.5}],
                        type=pa.struct([("half", pa.float16())]),
                    )
                }
            ),
        )
        mat = FileSourceMaterializer(local_project(tmp_path))

        rows = mat.materialize_and_run(
            source, "SELECT typeof(v) AS t FROM t", {}, "t_source"
        )
        assert rows[0]["t"] == "STRUCT(half FLOAT)"

    def test_duplicate_parquet_column_names_are_stamped_not_renamed(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Arrow permits duplicate column names and DuckDB's reader would
        rename the second one; the author sees a coded refusal instead."""
        import pyarrow as pa

        source = self._write_parquet(
            tmp_path,
            pa.Table.from_arrays([pa.array([1]), pa.array([2])], names=["a", "a"]),
        )
        mat = FileSourceMaterializer(local_project(tmp_path))

        with pytest.raises(DbtChartsError) as exc:
            mat.materialize_and_run(source, "SELECT * FROM t", {}, "t_source")
        assert exc.value.code is not None
        assert exc.value.code.code == "ERR-FILE-SOURCE-DUPLICATE-COLUMN"
        assert "'a'" in str(exc.value)

    def test_duplicate_csv_header_is_stamped_not_internal(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """DuckDB's CSV reader renames a repeated header; the author sees a coded
        error naming the table instead."""
        project = _make_project(
            tmp_path, {"data/dup.csv": "a,A,b\n1,2,3\n"}, local_project
        )
        source = _csv_source({"dup": "data/dup.csv"})
        mat = FileSourceMaterializer(project)

        with pytest.raises(DbtChartsError) as exc:
            mat.materialize_and_run(source, "SELECT * FROM dup", {}, "dup")
        assert exc.value.code is not None
        assert exc.value.code.code == "ERR-FILE-SOURCE-DUPLICATE-COLUMN"
        assert "'dup'" in str(exc.value)
