"""Tests for glob pattern support in file sources.

Covers FileSourceMaterializer glob expansion, Executor cache key correctness
with globs, and the validate-time empty-glob check.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.source import (
    CsvSourceConfig,
    JsonSourceConfig,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.file_source_materializer import FileSourceMaterializer
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mat(project: Any) -> FileSourceMaterializer:
    return FileSourceMaterializer(project, TrivialDuckDBCache())


class _CountingMaterializer(FileSourceMaterializer):
    """Materializer that counts materialize_and_run calls."""

    def __init__(self, project: Any, cache: TrivialDuckDBCache) -> None:
        super().__init__(project, cache)
        self.run_calls = 0

    def materialize_and_run(  # type: ignore[override]
        self, source: Any, sql: str, variables: dict[str, Any], source_name: str
    ) -> list[dict[str, Any]]:
        self.run_calls += 1
        return super().materialize_and_run(source, sql, variables, source_name)


# ---------------------------------------------------------------------------
# JSON glob — expansion and union
# ---------------------------------------------------------------------------


class TestJsonGlob:
    def test_glob_expands_to_unioned_table(self, in_memory_project: Any) -> None:
        """A JSON glob pattern in files: unions all matched files into one table."""
        project = in_memory_project(
            Path("/test"),
            {
                "runs/a/report.json": '[{"run": "a", "score": 10}]',
                "runs/b/report.json": '[{"run": "b", "score": 20}]',
            },
        )
        source = JsonSourceConfig(type="json", files={"reports": "runs/*/report.json"})
        mat = _mat(project)

        rows = mat.materialize_and_run(
            source, "SELECT run, score FROM reports ORDER BY run", {}, "evals"
        )

        assert [r["run"] for r in rows] == ["a", "b"]
        assert [r["score"] for r in rows] == [10, 20]

    def test_glob_single_object_wraps_to_row(self, in_memory_project: Any) -> None:
        """A JSON file containing a single object (not array) yields a 1-row table.

        Required for evals boards where each report.json / summary.json is a
        single-object result document, not an array.
        """
        project = in_memory_project(
            Path("/test"),
            {
                "runs/a/report.json": '{"run_id": "a", "pass_rate": 0.9}',
                "runs/b/report.json": '{"run_id": "b", "pass_rate": 0.8}',
            },
        )
        source = JsonSourceConfig(type="json", files={"reports": "runs/*/report.json"})
        mat = _mat(project)

        rows = mat.materialize_and_run(
            source, "SELECT run_id, pass_rate FROM reports ORDER BY run_id", {}, "evals"
        )

        assert [r["run_id"] for r in rows] == ["a", "b"]

    def test_non_glob_single_object_wraps_to_row(self, in_memory_project: Any) -> None:
        """A literal (non-glob) JSON file that contains a single object also works."""
        project = in_memory_project(
            Path("/test"),
            {"data/report.json": '{"run_id": "x", "pass_rate": 0.9}'},
        )
        source = JsonSourceConfig(type="json", files={"report": "data/report.json"})
        mat = _mat(project)

        rows = mat.materialize_and_run(source, "SELECT run_id FROM report", {}, "evals")

        assert len(rows) == 1
        assert rows[0]["run_id"] == "x"

    def test_glob_three_files_all_unioned(self, in_memory_project: Any) -> None:
        """Glob matching three files produces three rows (one per file)."""
        project = in_memory_project(
            Path("/test"),
            {
                "runs/x/report.json": '[{"n": 1}]',
                "runs/y/report.json": '[{"n": 2}]',
                "runs/z/report.json": '[{"n": 3}]',
            },
        )
        source = JsonSourceConfig(type="json", files={"reports": "runs/*/report.json"})
        mat = _mat(project)

        rows = mat.materialize_and_run(
            source, "SELECT n FROM reports ORDER BY n", {}, "evals"
        )

        assert [r["n"] for r in rows] == [1, 2, 3]

    def test_empty_glob_raises_at_materialize(self, in_memory_project: Any) -> None:
        """A glob matching no files raises DbtChartsError at materialize time."""
        project = in_memory_project(Path("/test"), {})
        source = JsonSourceConfig(type="json", files={"reports": "runs/*/report.json"})
        mat = _mat(project)

        with pytest.raises(DbtChartsError, match="matched no files"):
            mat.materialize_and_run(source, "SELECT * FROM reports", {}, "evals")


# ---------------------------------------------------------------------------
# CSV glob — expansion and union
# ---------------------------------------------------------------------------


class TestCsvGlob:
    def test_glob_expands_to_unioned_table(self, in_memory_project: Any) -> None:
        """A CSV glob pattern in files: unions all matched files into one table."""
        project = in_memory_project(
            Path("/test"),
            {
                "data/2024/sales.csv": "region,amount\nNorth,100\n",
                "data/2025/sales.csv": "region,amount\nSouth,200\n",
            },
        )
        source = CsvSourceConfig(type="csv", files={"sales": "data/*/sales.csv"})
        mat = _mat(project)

        rows = mat.materialize_and_run(
            source,
            "SELECT region, amount FROM sales ORDER BY amount",
            {},
            "sales_source",
        )

        assert [r["region"] for r in rows] == ["North", "South"]
        assert [r["amount"] for r in rows] == [100, 200]

    def test_empty_csv_glob_raises(self, in_memory_project: Any) -> None:
        """A CSV glob matching no files raises DbtChartsError at materialize time."""
        project = in_memory_project(Path("/test"), {})
        source = CsvSourceConfig(type="csv", files={"sales": "data/*/sales.csv"})
        mat = _mat(project)

        with pytest.raises(DbtChartsError, match="matched no files"):
            mat.materialize_and_run(source, "SELECT * FROM sales", {}, "sales_source")


# ---------------------------------------------------------------------------
# Fan-out cap
# ---------------------------------------------------------------------------


class TestGlobFanoutCap:
    def test_exceeding_default_cap_raises_before_reads(
        self, in_memory_project: Any
    ) -> None:
        """Glob matching more than max_glob_file_count files raises ValueError."""
        from dbt_charts.core.compile.config import get_execution_config

        cap = get_execution_config().max_glob_file_count
        # One more file than the cap to trigger the error.
        files = {
            f"runs/{i:04d}/report.json": f'[{{"id": {i}}}]' for i in range(cap + 1)
        }
        project = in_memory_project(Path("/test"), files)
        source = JsonSourceConfig(type="json", files={"reports": "runs/*/report.json"})
        mat = _mat(project)

        with pytest.raises(DbtChartsError, match=str(cap)):
            mat.materialize_and_run(source, "SELECT * FROM reports", {}, "evals")

    def test_at_cap_succeeds(self, in_memory_project: Any) -> None:
        """Exactly cap files is allowed."""
        from dbt_charts.core.compile.config import get_execution_config

        cap = get_execution_config().max_glob_file_count
        files = {f"runs/{i:04d}/report.json": f'[{{"id": {i}}}]' for i in range(cap)}
        project = in_memory_project(Path("/test"), files)
        source = JsonSourceConfig(type="json", files={"reports": "runs/*/report.json"})
        mat = _mat(project)

        rows = mat.materialize_and_run(
            source, "SELECT count(*) AS n FROM reports", {}, "evals"
        )
        assert rows[0]["n"] == cap


# ---------------------------------------------------------------------------
# Table-count cap
# ---------------------------------------------------------------------------


class TestFileSourceMaxTablesCap:
    def test_exceeding_cap_raises_before_reads(
        self, in_memory_project: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A files: map with more tables than the cap raises before any read."""
        monkeypatch.setenv("DCT_FILE_SOURCE_MAX_TABLES_CEILING", "2")
        project = in_memory_project(
            Path("/test"),
            {
                "data/a.json": '[{"id": 1}]',
                "data/b.json": '[{"id": 2}]',
                "data/c.json": '[{"id": 3}]',
            },
        )
        source = JsonSourceConfig(
            type="json",
            files={"a": "data/a.json", "b": "data/b.json", "c": "data/c.json"},
        )
        mat = _mat(project)

        read_calls: list[str] = []

        def _spy_read_bytes(relpath: str) -> bytes:
            read_calls.append(relpath)
            return b""

        monkeypatch.setattr(project, "read_bytes", _spy_read_bytes)

        with pytest.raises(DbtChartsError, match="3 tables"):
            mat.materialize_and_run(source, "SELECT * FROM a", {}, "evals")
        assert read_calls == []

    def test_at_cap_succeeds(
        self, in_memory_project: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exactly cap tables is allowed."""
        monkeypatch.setenv("DCT_FILE_SOURCE_MAX_TABLES_CEILING", "2")
        project = in_memory_project(
            Path("/test"),
            {"data/a.json": '[{"id": 1}]', "data/b.json": '[{"id": 2}]'},
        )
        source = JsonSourceConfig(
            type="json", files={"a": "data/a.json", "b": "data/b.json"}
        )
        mat = _mat(project)

        rows = mat.materialize_and_run(source, "SELECT id FROM a", {}, "evals")
        assert rows[0]["id"] == 1

    def test_project_config_cannot_raise_above_ceiling(
        self, in_memory_project: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A project's own dbt_charts.yml can never raise file_source_max_tables
        above the DCT_FILE_SOURCE_MAX_TABLES_CEILING deployment ceiling."""
        from dbt_charts.core.compile.config import get_config, reset_config

        monkeypatch.setenv("DCT_FILE_SOURCE_MAX_TABLES_CEILING", "1")
        reset_config()
        try:
            config = get_config()
            config.execution.file_source_max_tables = 1000
            project = in_memory_project(
                Path("/test"),
                {"data/a.json": '[{"id": 1}]', "data/b.json": '[{"id": 2}]'},
            )
            source = JsonSourceConfig(
                type="json", files={"a": "data/a.json", "b": "data/b.json"}
            )
            mat = _mat(project)

            with pytest.raises(DbtChartsError, match="2 tables"):
                mat.materialize_and_run(source, "SELECT * FROM a", {}, "evals")
        finally:
            reset_config()


# ---------------------------------------------------------------------------
# Materialized byte-size cap
# ---------------------------------------------------------------------------


def _parquet_uncompressed_size(path: Path) -> int:
    """The size the guard will measure, from the guard's own function — used
    to place a fixture's cap on the right side of it, never as the oracle a
    test asserts against."""
    from dbt_charts.core.compile.models.source import ParquetSourceConfig
    from dbt_charts.core.execute.file_source_materializer import _uncompressed_bytes

    return _uncompressed_bytes(
        ParquetSourceConfig(type="parquet", files={"t": "x.parquet"}),
        path.read_bytes(),
    )


class TestFileSourceMaxBytesCap:
    def test_exceeding_cap_raises(
        self, in_memory_project: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A relation whose materialized size exceeds the cap raises."""
        monkeypatch.setenv("DCT_FILE_SOURCE_MAX_BYTES_CEILING", "10")
        project = in_memory_project(
            Path("/test"), {"data/big.json": '[{"id": 1, "note": "hello world"}]'}
        )
        source = JsonSourceConfig(type="json", files={"big": "data/big.json"})
        mat = _mat(project)

        with pytest.raises(DbtChartsError, match="exceeding"):
            mat.materialize_and_run(source, "SELECT * FROM big", {}, "evals")

    def test_under_cap_succeeds(
        self, in_memory_project: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A relation under the byte cap materializes normally."""
        monkeypatch.setenv("DCT_FILE_SOURCE_MAX_BYTES_CEILING", "1000000")
        project = in_memory_project(Path("/test"), {"data/small.json": '[{"id": 1}]'})
        source = JsonSourceConfig(type="json", files={"small": "data/small.json"})
        mat = _mat(project)

        rows = mat.materialize_and_run(source, "SELECT id FROM small", {}, "evals")
        assert rows[0]["id"] == 1

    def test_project_config_cannot_raise_above_ceiling(
        self, in_memory_project: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A project's own dbt_charts.yml can never raise file_source_max_bytes
        above the DCT_FILE_SOURCE_MAX_BYTES_CEILING deployment ceiling."""
        from dbt_charts.core.compile.config import get_config, reset_config

        monkeypatch.setenv("DCT_FILE_SOURCE_MAX_BYTES_CEILING", "10")
        reset_config()
        try:
            config = get_config()
            config.execution.file_source_max_bytes = 5_000_000_000
            project = in_memory_project(
                Path("/test"), {"data/big.json": '[{"id": 1, "note": "hello world"}]'}
            )
            source = JsonSourceConfig(type="json", files={"big": "data/big.json"})
            mat = _mat(project)

            with pytest.raises(DbtChartsError, match="exceeding"):
                mat.materialize_and_run(source, "SELECT * FROM big", {}, "evals")
        finally:
            reset_config()

    def test_json_is_measured_at_its_own_byte_size(
        self, in_memory_project: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A text format's file IS its uncompressed size, so the cap it is
        checked against is the file's own length and nothing else."""
        content = '[{"id": 1, "note": "hello world, this is a json row"}]'
        raw_size = len(content.encode())
        cap = raw_size * 5
        monkeypatch.setenv("DCT_FILE_SOURCE_MAX_BYTES_CEILING", str(cap))
        project = in_memory_project(Path("/test"), {"data/rows.json": content})
        source = JsonSourceConfig(type="json", files={"rows": "data/rows.json"})
        mat = _mat(project)

        rows = mat.materialize_and_run(source, "SELECT id FROM rows", {}, "evals")
        assert rows[0]["id"] == 1

    def test_parquet_is_measured_from_its_footer_not_a_multiplier(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A compressible parquet file that fits the cap by its real
        uncompressed size must load, even though a raw x 20 guess would blow
        the same cap."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        from dbt_charts.core.compile.models.source import ParquetSourceConfig

        table = pa.table({"id": list(range(50))})
        parquet_path = tmp_path / "data" / "events.parquet"
        parquet_path.parent.mkdir(parents=True)
        pq.write_table(table, parquet_path)
        raw_size = parquet_path.stat().st_size
        uncompressed = _parquet_uncompressed_size(parquet_path)

        # Above the file's real uncompressed size, below raw x 20: passes iff
        # the guard measures rather than guesses.
        cap = raw_size * 5
        assert uncompressed < cap < raw_size * 20
        monkeypatch.setenv("DCT_FILE_SOURCE_MAX_BYTES_CEILING", str(cap))

        project = local_project(tmp_path)
        source = ParquetSourceConfig(
            type="parquet", files={"events": "data/events.parquet"}
        )
        mat = _mat(project)

        rows = mat.materialize_and_run(source, "SELECT id FROM events", {}, "evals")
        assert len(rows) == 50

    def test_parquet_over_the_cap_by_measured_size_still_raises(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A relation whose real uncompressed size exceeds the cap raises,
        even though the compressed file on disk fits under it."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        from dbt_charts.core.compile.models.source import ParquetSourceConfig

        # Distinct values, so the encoded column stays large and only the
        # file compression shrinks it: uncompressed > raw, the normal case.
        table = pa.table({"note": [f"row-{i:012d}" for i in range(200_000)]})
        parquet_path = tmp_path / "data" / "events.parquet"
        parquet_path.parent.mkdir(parents=True)
        # Four row groups, so a guard that read only the first would measure a
        # quarter of the relation, land under the cap, and fail here.
        pq.write_table(table, parquet_path, row_group_size=50_000)
        raw_size = parquet_path.stat().st_size
        uncompressed = _parquet_uncompressed_size(parquet_path)

        cap = (raw_size + uncompressed) // 2
        assert raw_size < cap < uncompressed
        monkeypatch.setenv("DCT_FILE_SOURCE_MAX_BYTES_CEILING", str(cap))

        project = local_project(tmp_path)
        source = ParquetSourceConfig(
            type="parquet", files={"events": "data/events.parquet"}
        )
        mat = _mat(project)

        with pytest.raises(DbtChartsError, match="exceeding"):
            mat.materialize_and_run(source, "SELECT * FROM events", {}, "evals")

    def test_parquet_and_csv_of_the_same_rows_are_measured_alike(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Choosing the compact format must not be what gets you rejected.
        The two formats of the same rows must be measured within a small
        factor of each other: the real difference between a CSV's digits and
        an int64 column, and nothing more."""
        import pyarrow as pa
        import pyarrow.csv as pacsv
        import pyarrow.parquet as pq

        from dbt_charts.core.compile.models.source import ParquetSourceConfig

        table = pa.table({"id": list(range(2_000))})
        data = tmp_path / "data"
        data.mkdir(parents=True)
        pq.write_table(table, data / "events.parquet")
        pacsv.write_csv(table, data / "events.csv")

        csv_size = (data / "events.csv").stat().st_size
        parquet_size = _parquet_uncompressed_size(data / "events.parquet")
        assert parquet_size < csv_size * 3

        cap = csv_size * 3
        monkeypatch.setenv("DCT_FILE_SOURCE_MAX_BYTES_CEILING", str(cap))
        project = local_project(tmp_path)

        csv_rows = _mat(project).materialize_and_run(
            CsvSourceConfig(type="csv", files={"events": "data/events.csv"}),
            "SELECT id FROM events",
            {},
            "evals",
        )
        parquet_rows = _mat(project).materialize_and_run(
            ParquetSourceConfig(
                type="parquet", files={"events": "data/events.parquet"}
            ),
            "SELECT id FROM events",
            {},
            "evals",
        )

        assert len(csv_rows) == len(parquet_rows) == 2_000

    def test_the_message_names_no_remedy_a_deployment_ceiling_can_veto(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The cap that fires may be a deployment ceiling, which a project's
        own config can only lower. Cloud pins one at 50 MB, so telling the
        caller to raise execution.file_source_max_bytes is an instruction that
        cannot work on the surface the error fires on."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        from dbt_charts.core.compile.models.source import ParquetSourceConfig

        table = pa.table({"id": list(range(50))})
        parquet_path = tmp_path / "data" / "events.parquet"
        parquet_path.parent.mkdir(parents=True)
        pq.write_table(table, parquet_path)
        # Any file is over a 1-byte cap; this test is about what the message
        # then tells the caller to do, not about where the threshold sits.
        monkeypatch.setenv("DCT_FILE_SOURCE_MAX_BYTES_CEILING", "1")

        project = local_project(tmp_path)
        source = ParquetSourceConfig(
            type="parquet", files={"events": "data/events.parquet"}
        )
        mat = _mat(project)

        with pytest.raises(DbtChartsError) as excinfo:
            mat.materialize_and_run(source, "SELECT * FROM events", {}, "evals")

        message = str(excinfo.value)
        assert "file_source_max_bytes" not in message
        assert "multiplier" not in message
        # A remedy the caller can always reach, whichever limit fired.
        assert "use a database connection" in message


# ---------------------------------------------------------------------------
# Column-key-set consistency check
# ---------------------------------------------------------------------------


class TestGlobColumnConsistency:
    def test_mismatched_columns_raises(self, in_memory_project: Any) -> None:
        """Glob-matched files with different column sets raise a clear error."""
        project = in_memory_project(
            Path("/test"),
            {
                "runs/a/report.json": '[{"id": 1, "score": 10}]',
                "runs/b/report.json": '[{"id": 2, "extra": "oops"}]',
            },
        )
        source = JsonSourceConfig(type="json", files={"reports": "runs/*/report.json"})
        mat = _mat(project)

        with pytest.raises(DbtChartsError, match="on column names or types"):
            mat.materialize_and_run(source, "SELECT * FROM reports", {}, "evals")

    def test_mismatched_columns_second_has_extra(self, in_memory_project: Any) -> None:
        """Error message names both the offending file and the differing columns."""
        project = in_memory_project(
            Path("/test"),
            {
                "runs/a/report.json": '[{"id": 1}]',
                "runs/b/report.json": '[{"id": 2, "bonus": 99}]',
            },
        )
        source = JsonSourceConfig(type="json", files={"reports": "runs/*/report.json"})
        mat = _mat(project)

        with pytest.raises(DbtChartsError, match="bonus"):
            mat.materialize_and_run(source, "SELECT * FROM reports", {}, "evals")

    def test_matching_columns_succeeds(self, in_memory_project: Any) -> None:
        """Files with identical column sets union correctly."""
        project = in_memory_project(
            Path("/test"),
            {
                "runs/a/report.json": '[{"id": 1, "score": 10}]',
                "runs/b/report.json": '[{"id": 2, "score": 20}]',
            },
        )
        source = JsonSourceConfig(type="json", files={"reports": "runs/*/report.json"})
        mat = _mat(project)

        rows = mat.materialize_and_run(
            source, "SELECT id, score FROM reports ORDER BY id", {}, "evals"
        )
        assert [r["id"] for r in rows] == [1, 2]


# ---------------------------------------------------------------------------
# Executor-level glob cache key correctness
# ---------------------------------------------------------------------------


class TestExecutorGlobCacheKey:
    """Glob sources produce stable cache keys; adding or removing a matched
    file changes the version so warm-cache rows stay fresh."""

    def _project(self, tmp_path: Path, files: dict[str, str]) -> Any:
        from dbt_charts.cli.filesystem_project import FilesystemProject

        for relpath, content in files.items():
            dest = tmp_path / relpath
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        (tmp_path / "dbt_charts.yml").write_text(
            yaml.dump(
                {
                    "sources": {
                        "runs": {
                            "type": "json",
                            "files": {"reports": "data/*/report.json"},
                        }
                    }
                }
            )
        )
        return FilesystemProject(tmp_path)

    def _board(self) -> Any:
        from dbt_charts.core.compile import compile as compile_board

        board_yaml = {
            "queries": {
                "q": {"sql": "SELECT * FROM reports ORDER BY id", "source": "runs"}
            },
            "charts": {"c": {"query": "q", "type": "table"}},
        }
        board = compile_board(yaml.dump(board_yaml)).board
        assert board is not None
        return board

    def _registry(self, project: Any) -> Any:
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        return build_adapter_registry(project)

    def test_cold_glob_render_returns_all_matched_rows(self, tmp_path: Path) -> None:
        """Glob source on cold render returns rows from all matched files."""
        project = self._project(
            tmp_path,
            {
                "data/a/report.json": '[{"id": 1}]',
                "data/b/report.json": '[{"id": 2}]',
            },
        )
        board = self._board()
        mat = FileSourceMaterializer(project, TrivialDuckDBCache())
        ex = Executor(
            board,
            adapter_registry=self._registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=TrivialDuckDBCache(),
            file_materializer=mat,
        )

        rows = ex.execute_query("q")
        assert [r["id"] for r in rows] == [1, 2]

    def test_warm_glob_render_hits_cache(self, tmp_path: Path) -> None:
        """Second render with same glob-matched files hits the result cache."""

        project = self._project(
            tmp_path,
            {
                "data/a/report.json": '[{"id": 1}]',
                "data/b/report.json": '[{"id": 2}]',
            },
        )
        board = self._board()
        result_cache = TrivialDuckDBCache()

        mat1 = _CountingMaterializer(project, TrivialDuckDBCache())
        ex1 = Executor(
            board,
            adapter_registry=self._registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=result_cache,
            file_materializer=mat1,
        )
        ex1.execute_query("q")
        assert mat1.run_calls == 1

        mat2 = _CountingMaterializer(project, TrivialDuckDBCache())
        ex2 = Executor(
            board,
            adapter_registry=self._registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=result_cache,
            file_materializer=mat2,
        )
        ex2.execute_query("q")
        # Warm render: result cache hit, materializer never called.
        assert mat2.run_calls == 0

    def test_adding_matching_file_invalidates_cache(self, tmp_path: Path) -> None:
        """Adding a file to the glob match set changes the cache key."""

        project = self._project(
            tmp_path,
            {"data/a/report.json": '[{"id": 1}]'},
        )
        board = self._board()
        result_cache = TrivialDuckDBCache()

        mat1 = _CountingMaterializer(project, TrivialDuckDBCache())
        ex1 = Executor(
            board,
            adapter_registry=self._registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=result_cache,
            file_materializer=mat1,
        )
        rows1 = ex1.execute_query("q")
        assert [r["id"] for r in rows1] == [1]

        # Add a second matching file.
        new_file = tmp_path / "data" / "b" / "report.json"
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_text('[{"id": 2}]')

        mat2 = _CountingMaterializer(project, TrivialDuckDBCache())
        ex2 = Executor(
            board,
            adapter_registry=self._registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=result_cache,
            file_materializer=mat2,
        )
        rows2 = ex2.execute_query("q")
        # New file added → cache key changed → materializer ran again.
        assert mat2.run_calls == 1
        assert [r["id"] for r in rows2] == [1, 2]


# ---------------------------------------------------------------------------
# Executor error-code propagation for glob failures
# ---------------------------------------------------------------------------


class TestExecutorGlobErrorCodes:
    """Glob errors raised in the materializer surface as QueryError with the
    original ErrorCode preserved, so callers (agent API, Cloud) can branch on
    the structured code without string-matching."""

    def _setup(self, tmp_path: Path, files: dict[str, str]) -> tuple[Any, Any, Any]:
        """Return (project, board, adapter_registry) for a JSON glob board."""
        from dbt_charts.cli.filesystem_project import FilesystemProject

        for relpath, content in files.items():
            dest = tmp_path / relpath
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        (tmp_path / "dbt_charts.yml").write_text(
            yaml.dump(
                {
                    "sources": {
                        "runs": {
                            "type": "json",
                            "files": {"reports": "data/*/report.json"},
                        }
                    }
                }
            )
        )
        from dbt_charts.core.compile import compile as compile_board
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        project = FilesystemProject(tmp_path)
        board_yaml = {
            "queries": {"q": {"sql": "SELECT * FROM reports", "source": "runs"}},
            "charts": {"c": {"query": "q", "type": "table"}},
        }
        board = compile_board(yaml.dump(board_yaml)).board
        assert board is not None
        return project, board, build_adapter_registry(project)

    def test_empty_glob_propagates_err_glob_empty_code(self, tmp_path: Path) -> None:
        """Executor.execute_query forwards ERR-GLOB-EMPTY when glob matches nothing."""
        from dbt_charts.core.diagnostics.codes_execute import ERR_GLOB_EMPTY

        project, board, registry = self._setup(tmp_path, {})
        mat = FileSourceMaterializer(project, TrivialDuckDBCache())
        ex = Executor(
            board,
            adapter_registry=registry,
            query_registry={"q": board.queries["q"]},
            result_cache=TrivialDuckDBCache(),
            file_materializer=mat,
        )

        with pytest.raises(QueryError) as exc_info:
            ex.execute_query("q")
        assert exc_info.value.code == ERR_GLOB_EMPTY

    def test_too_many_files_propagates_err_glob_too_many_code(
        self, tmp_path: Path
    ) -> None:
        """Executor.execute_query forwards ERR-GLOB-TOO-MANY when cap is exceeded."""
        from dbt_charts.core.compile.config import get_execution_config
        from dbt_charts.core.diagnostics.codes_execute import ERR_GLOB_TOO_MANY

        cap = get_execution_config().max_glob_file_count
        files = {
            f"data/{i:04d}/report.json": f'[{{"id": {i}}}]' for i in range(cap + 1)
        }
        project, board, registry = self._setup(tmp_path, files)
        mat = FileSourceMaterializer(project, TrivialDuckDBCache())
        ex = Executor(
            board,
            adapter_registry=registry,
            query_registry={"q": board.queries["q"]},
            result_cache=TrivialDuckDBCache(),
            file_materializer=mat,
        )

        with pytest.raises(QueryError) as exc_info:
            ex.execute_query("q")
        assert exc_info.value.code == ERR_GLOB_TOO_MANY

    def test_glob_schema_mismatch_propagates_authored_message(
        self, tmp_path: Path
    ) -> None:
        """from_code() no longer puts detail in .fields, so templates that embed
        {detail} must not KeyError.
        """
        from dbt_charts.core.diagnostics.codes_execute import (
            ERR_GLOB_SCHEMA_MISMATCH,
        )

        files = {
            "data/a/report.json": '[{"id": 1, "score": 10}]',
            "data/b/report.json": '[{"id": 2, "extra": "oops"}]',
        }
        project, board, registry = self._setup(tmp_path, files)
        mat = FileSourceMaterializer(project, TrivialDuckDBCache())
        ex = Executor(
            board,
            adapter_registry=registry,
            query_registry={"q": board.queries["q"]},
            result_cache=TrivialDuckDBCache(),
            file_materializer=mat,
        )

        with pytest.raises(QueryError) as exc_info:
            ex.execute_query("q")
        assert exc_info.value.code == ERR_GLOB_SCHEMA_MISMATCH
        assert "'detail'" not in str(exc_info.value)
        assert "on column names or types" in str(exc_info.value)
