"""Regression tests: dct query / execute_query / query_board against a file source.

Root cause: agent_api.execute_query and query_board share AdapterRegistry.execute(),
which used to refuse any file-source (csv/json/parquet) query unconditionally,
naming the render path as the only reader. A ProjectSession now supplies
the registry a file-source materializer (the local DuckDB-backed factory when
none is injected), so both verbs return real rows.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.agent_api import ProjectSession
from dbt_charts.cli.filesystem_project import FilesystemProject


def _csv_project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "orders.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\nNorth,150\n"
    )
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  marts:\n    type: csv\n    files:\n      orders: data/orders.csv\n"
    )
    return local_project(tmp_path)


def _write_board(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "board.yml"
    path.write_text(content)
    return path


class TestFileSourceAdHocQueries:
    """A plain ProjectSession.from_project(project) — the shape `dct query`'s
    CLI command actually builds — must be able to read a file source."""

    def test_execute_query_returns_rows_from_a_csv_source(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _csv_project(tmp_path, local_project)
        with ProjectSession.from_project(project) as session:
            result = session.execute_query(
                "SELECT * FROM orders ORDER BY amount", source="marts"
            )

        assert result.success is True, result.errors
        assert [r["region"] for r in result.data] == ["North", "North", "South"]

    def test_query_board_returns_rows_from_a_csv_source(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _csv_project(tmp_path, local_project)
        board_path = _write_board(
            tmp_path,
            "title: probe\n"
            "text: probe board\n"
            "queries:\n"
            "  by_region:\n"
            "    sql: SELECT region, amount FROM orders WHERE region = 'North'\n"
            "    source: marts\n",
        )
        with ProjectSession.from_project(project) as session:
            result = session.query_board("by_region", board_path)

        assert result.success is True, result.errors
        assert len(result.data) == 2
        assert all(r["region"] == "North" for r in result.data)


def test_execute_query_glob_schema_mismatch_names_the_offending_files(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """from_code() no longer puts detail in .fields, so templates that embed
    {detail} must not KeyError.
    """
    data_dir = tmp_path / "data"
    (data_dir / "a").mkdir(parents=True)
    (data_dir / "b").mkdir(parents=True)
    (data_dir / "a" / "report.json").write_text('[{"id": 1, "score": 10}]')
    (data_dir / "b" / "report.json").write_text('[{"id": 2, "extra": "oops"}]')
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n"
        "  runs:\n"
        "    type: json\n"
        "    files:\n"
        "      reports: data/*/report.json\n"
    )
    project = local_project(tmp_path)

    with ProjectSession.from_project(project) as session:
        result = session.execute_query("SELECT * FROM reports", source="runs")

    assert result.success is False
    assert result.errors
    assert "'detail'" not in result.errors[0]
    assert "on column names or types" in result.errors[0]


def test_registry_with_no_materializer_still_refuses(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """The seam is opt-in, not a silent local default: a caller that builds and
    injects its own AdapterRegistry (bypassing ProjectSession's own lazy build,
    which is where the local factory fallback lives) gets no free materializer.
    """
    from dbt_charts.core.execute.adapters import build_adapter_registry

    project = _csv_project(tmp_path, local_project)
    registry = build_adapter_registry(project)

    with ProjectSession.from_project(project, adapter_registry=registry) as session:
        result = session.execute_query("SELECT * FROM orders", source="marts")

    assert result.success is False
    assert result.errors
    assert "materializer" in result.errors[0]


class TestFileSourceDescribeQuery:
    """`--describe` on a csv/json/parquet source: warehouse_check routes a file
    source to the same DuckDB DESCRIBE mechanism it executes on."""

    def test_describe_query_returns_columns_from_a_csv_source(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = _csv_project(tmp_path, local_project)
        with ProjectSession.from_project(project) as session:
            result = session.describe_query(
                "SELECT region, amount FROM orders", source="marts"
            )

        assert result.success is True, result.error
        assert result.columns is not None
        assert {c.name for c in result.columns} == {"region", "amount"}

    def test_describe_query_reports_the_materialized_type_not_a_varchar_guess(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The `amount` column is authored as bare integers in the CSV — its
        DESCRIBE type must come from DuckDB's own type inference over the
        materialized table, not a naive all-VARCHAR guess."""
        project = _csv_project(tmp_path, local_project)
        with ProjectSession.from_project(project) as session:
            result = session.describe_query("SELECT amount FROM orders", source="marts")

        assert result.success is True, result.error
        assert result.columns is not None
        amount = next(c for c in result.columns if c.name == "amount")
        assert amount.type != "VARCHAR"
        assert "INT" in amount.type

    def test_describe_query_with_no_materializer_still_refuses(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Mirrors ``test_registry_with_no_materializer_still_refuses``: the
        DESCRIBE dispatch is not a second, more permissive door into a file
        source — with no materializer configured it refuses exactly like
        execute does."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        project = _csv_project(tmp_path, local_project)
        registry = build_adapter_registry(project)

        with ProjectSession.from_project(project, adapter_registry=registry) as session:
            result = session.describe_query("SELECT * FROM orders", source="marts")

        assert result.success is False
        assert result.error
        assert "materializer" in result.error

    def test_describe_query_of_a_bad_column_surfaces_the_real_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A materializer failure (DuckDB rejects the unknown column) reaches
        the caller with the real detail rather than a raw duckdb exception or
        a silent success.

        It is *not* classified ``invalid`` the way a Postgres/Snowflake
        EXPLAIN rejection is: ``_duckdb_cache_base.execute_file_source_sql``
        wraps every DuckDB error — including a genuine
        ``duckdb.BinderException`` — in a plain ``RuntimeError`` before
        ``handle_adapter_error`` ever sees it, so the code stays at the
        ERR-INTERNAL fallback here and ``_is_query_defect`` falls back to
        ``unchecked``.
        """
        project = _csv_project(tmp_path, local_project)
        with ProjectSession.from_project(project) as session:
            result = session.describe_query(
                "SELECT nonexistent_column FROM orders", source="marts"
            )

        assert result.success is False
        assert result.error
        assert "Binder Error" in result.error
        assert "not found in FROM clause" in result.error
