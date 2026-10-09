"""File-source result caching + one-shared-materializer-per-render.

Warm dashboards must serve file-source query results from the result cache
without opening the data file; a file edit must invalidate; and the materializer
must be built lazily, once, shared across a render's queries.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.file_source_materializer import FileSourceMaterializer
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache


def _project(tmp_path: Path, files: dict[str, str]) -> FilesystemProject:
    """Write a project with a project-level ``sales`` CSV source over data/sales.csv."""
    import yaml

    for relpath, content in files.items():
        dest = tmp_path / relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    (tmp_path / "dbt_charts.yml").write_text(
        yaml.dump(
            {
                "sources": {
                    "sales": {"type": "csv", "files": {"sales": "data/sales.csv"}}
                }
            }
        )
    )
    return FilesystemProject(tmp_path)


def _board(sql: str) -> Any:
    # File sources live in dbt_charts.yml (inline board sources are rejected); the board
    # just references the project source by name.
    board_yaml = {
        "queries": {"q": {"sql": sql, "source": "sales"}},
        "charts": {"c": {"query": "q", "type": "table"}},
    }
    import yaml

    board = compile_board(yaml.dump(board_yaml)).board
    assert board is not None
    return board


class _CountingMaterializer(FileSourceMaterializer):
    """Materializer that counts materialize_and_run calls."""

    def __init__(self, project: FilesystemProject) -> None:
        super().__init__(project)
        self.run_calls = 0

    def materialize_and_run(
        self, source: Any, sql: str, variables: dict[str, Any], source_name: str
    ) -> list[dict[str, Any]]:  # type: ignore[override]
        self.run_calls += 1
        return super().materialize_and_run(source, sql, variables, source_name)


def _bump_mtime(path: Path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def _registry(project: FilesystemProject) -> Any:
    from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry

    return build_adapter_registry(project)


def _bump_mtime_of(project: FilesystemProject, relpath: str) -> None:
    _bump_mtime(project.root / relpath)


class TestWarmRenderSkipsMaterializer:
    def test_second_render_serves_from_cache_without_materializing(
        self, tmp_path: Path
    ) -> None:
        project = _project(
            tmp_path, {"data/sales.csv": "region,amount\nNorth,100\nSouth,200\n"}
        )
        board = _board("SELECT * FROM sales ORDER BY amount")
        result_cache = TrivialDuckDBCache()

        # Render 1 (cold): materializer runs, result written through to result_cache.
        mat1 = _CountingMaterializer(project)
        ex1 = Executor(
            board,
            adapter_registry=_registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=result_cache,
            file_materializer=mat1,
        )
        rows1 = ex1.execute_query("q")
        assert [r["amount"] for r in rows1] == [100, 200]
        assert mat1.run_calls == 1

        # Render 2 (warm): a fresh executor sharing the persistent result_cache.
        mat2 = _CountingMaterializer(project)
        ex2 = Executor(
            board,
            adapter_registry=_registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=result_cache,
            file_materializer=mat2,
        )
        rows2 = ex2.execute_query("q")
        assert [r["amount"] for r in rows2] == [100, 200]
        # The warm render must NOT touch the materializer at all.
        assert mat2.run_calls == 0

    def test_edit_invalidates_cache(self, tmp_path: Path) -> None:
        project = _project(tmp_path, {"data/sales.csv": "region,amount\nNorth,100\n"})
        board = _board("SELECT * FROM sales ORDER BY amount")
        result_cache = TrivialDuckDBCache()

        mat1 = _CountingMaterializer(project)
        ex1 = Executor(
            board,
            adapter_registry=_registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=result_cache,
            file_materializer=mat1,
        )
        rows1 = ex1.execute_query("q")
        assert [r["amount"] for r in rows1] == [100]

        # Edit the file — new content and a bumped mtime.
        (project.root / "data/sales.csv").write_text(
            "region,amount\nNorth,100\nEast,999\n"
        )
        _bump_mtime_of(project, "data/sales.csv")

        mat2 = _CountingMaterializer(project)
        ex2 = Executor(
            board,
            adapter_registry=_registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=result_cache,
            file_materializer=mat2,
        )
        rows2 = ex2.execute_query("q")
        # Fresh rows, and the materializer had to run again (cache invalidated).
        assert [r["amount"] for r in rows2] == [100, 999]
        assert mat2.run_calls == 1


class TestSourceVersionMemoizedMidRender:
    def test_same_executor_reuses_version_after_mtime_bump(
        self, tmp_path: Path
    ) -> None:
        """`_source_versions` is memoized per source for the executor's lifetime.

        A file edited *mid-render* must not split the read key from the write key:
        the second query in the same executor still hits the in-memory memo and does
        not re-materialize, even though the file's mtime changed underneath it.
        """
        project = _project(tmp_path, {"data/sales.csv": "region,amount\nNorth,100\n"})
        board = _board("SELECT * FROM sales")
        mat = _CountingMaterializer(project)
        ex = Executor(
            board,
            adapter_registry=_registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=TrivialDuckDBCache(),
            file_materializer=mat,
        )
        ex.execute_query("q")
        assert mat.run_calls == 1

        # Bump the file's mtime as if it were edited during the same render.
        _bump_mtime_of(project, "data/sales.csv")

        ex.execute_query("q")  # same executor → memoized version → in-memory hit
        assert mat.run_calls == 1


class TestMultiFileSource:
    def test_editing_one_file_invalidates_the_combined_source(
        self, tmp_path: Path
    ) -> None:
        import yaml

        (tmp_path / "data").mkdir()
        (tmp_path / "data/orders.csv").write_text("order_id,customer_id\n1,10\n2,20\n")
        (tmp_path / "data/customers.csv").write_text(
            "customer_id,name\n10,Alice\n20,Bob\n"
        )
        (tmp_path / "dbt_charts.yml").write_text(
            yaml.dump(
                {
                    "sources": {
                        "shop": {
                            "type": "csv",
                            "files": {
                                "orders": "data/orders.csv",
                                "customers": "data/customers.csv",
                            },
                        }
                    }
                }
            )
        )
        project = FilesystemProject(tmp_path)
        board = compile_board(
            yaml.dump(
                {
                    "queries": {
                        "j": {
                            "sql": (
                                "SELECT c.name FROM orders o "
                                "JOIN customers c ON o.customer_id = c.customer_id "
                                "ORDER BY o.order_id"
                            ),
                            "source": "shop",
                        }
                    },
                    "charts": {"c": {"query": "j", "type": "table"}},
                }
            )
        ).board
        assert board is not None
        result_cache = TrivialDuckDBCache()

        mat1 = _CountingMaterializer(project)
        ex1 = Executor(
            board,
            adapter_registry=_registry(project),
            query_registry={"j": board.queries["j"]},
            result_cache=result_cache,
            file_materializer=mat1,
        )
        assert [r["name"] for r in ex1.execute_query("j")] == ["Alice", "Bob"]

        # Edit only the customers file — the combined source version must change.
        (tmp_path / "data/customers.csv").write_text(
            "customer_id,name\n10,Alice\n20,Robert\n"
        )
        _bump_mtime_of(project, "data/customers.csv")

        mat2 = _CountingMaterializer(project)
        ex2 = Executor(
            board,
            adapter_registry=_registry(project),
            query_registry={"j": board.queries["j"]},
            result_cache=result_cache,
            file_materializer=mat2,
        )
        assert [r["name"] for r in ex2.execute_query("j")] == ["Alice", "Robert"]
        assert mat2.run_calls == 1


class TestLazySharedMaterializer:
    def test_factory_built_once_on_cold_and_never_on_warm(self, tmp_path: Path) -> None:
        project = _project(tmp_path, {"data/sales.csv": "region,amount\nNorth,100\n"})
        board = _board("SELECT * FROM sales")
        result_cache = TrivialDuckDBCache()

        builds = {"n": 0}

        def _factory() -> FileSourceMaterializer:
            builds["n"] += 1
            return FileSourceMaterializer(project)

        # Cold render: the factory builds exactly one shared materializer.
        ex1 = Executor(
            board,
            adapter_registry=_registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=result_cache,
            file_materializer_factory=_factory,
        )
        ex1.execute_query("q")
        assert builds["n"] == 1

        # Warm render: everything is cached, so the factory is never called —
        # no DuckDB is opened at all.
        ex2 = Executor(
            board,
            adapter_registry=_registry(project),
            query_registry={"q": board.queries["q"]},
            result_cache=result_cache,
            file_materializer_factory=_factory,
        )
        ex2.execute_query("q")
        assert builds["n"] == 1


class TestMaterializerReadsOnlyOnMiss:
    def test_repeated_run_reads_file_bytes_once(self, tmp_path: Path) -> None:
        project = _project(tmp_path, {"data/sales.csv": "region,amount\nNorth,100\n"})
        reads: list[str] = []
        orig_read = project.read_bytes

        def _counting_read(relpath: str) -> bytes:
            reads.append(relpath)
            return orig_read(relpath)

        project.read_bytes = _counting_read  # type: ignore[method-assign]

        from dbt_charts.core.compile.models.source import CsvSourceConfig

        source = CsvSourceConfig(type="csv", files={"sales": "data/sales.csv"})
        mat = FileSourceMaterializer(project)

        mat.materialize_and_run(source, "SELECT * FROM sales", {}, "sales")
        mat.materialize_and_run(source, "SELECT COUNT(*) FROM sales", {}, "sales")

        # Bytes read exactly once — the second call hits the version cache.
        assert reads == ["data/sales.csv"]


class TestConcurrentMaterialize:
    def test_parallel_runs_load_each_table_once(self, tmp_path: Path) -> None:
        project = _project(tmp_path, {"data/sales.csv": "region,amount\nNorth,100\n"})
        reads: list[str] = []
        lock = threading.Lock()
        orig_read = project.read_bytes

        def _counting_read(relpath: str) -> bytes:
            with lock:
                reads.append(relpath)
            return orig_read(relpath)

        project.read_bytes = _counting_read  # type: ignore[method-assign]

        from dbt_charts.core.compile.models.source import CsvSourceConfig

        source = CsvSourceConfig(type="csv", files={"sales": "data/sales.csv"})
        mat = FileSourceMaterializer(project)

        barrier = threading.Barrier(8)

        def _run() -> None:
            barrier.wait()
            mat.materialize_and_run(source, "SELECT * FROM sales", {}, "sales")

        threads = [threading.Thread(target=_run) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Eight concurrent runs, one physical load of the file.
        assert reads == ["data/sales.csv"]
