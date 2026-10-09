"""Agent-API tests for the warehouse tier of ``validate_paths``.

The tier is opt-in (``adapter_registry=`` / ``--warehouse``) and *additive*: it
appends to the stateless findings rather than standing in for them. Half of
these tests exist to pin that — a warehouse sweep that quietly replaced the
schema pass would look green on exactly the boards validation is meant to catch.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import duckdb

from dbt_charts.agent_api.validate import validate_paths
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import ProjectSourcesConfig, load_project_sources
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter
from dbt_charts.core.execute.file_source_materializer import FileSourceMaterializer

_HEALTHY_BOARD = (
    "title: Test Board\n"
    "source: testdb\n"
    "queries:\n"
    "  my_query: SELECT id, revenue FROM orders\n"
    "charts:\n"
    "  my_chart:\n"
    "    type: bar\n"
    "    query: queries.my_query\n"
    "    x: id\n"
    "    y: revenue\n"
)


def _write_dbt_charts_yml(project_root: Path, db_path: Path) -> None:
    """Write dbt_charts.yml so FilesystemProject.sources resolves testdb."""
    (project_root / "dbt_charts.yml").write_text(
        f"sources:\n  testdb:\n    type: duckdb\n    path: {db_path}\n"
    )


def _make_registry(project_root: Path, db_path: Path) -> AdapterRegistry:
    """Build an AdapterRegistry with a real read-only DuckDB adapter."""
    project = FilesystemProject(project_root)
    source_cfg = DuckDBSourceConfig(type="duckdb", path=str(db_path))
    project_sources = ProjectSourcesConfig(
        sources={"testdb": {"type": "duckdb", "path": str(db_path)}}
    )
    registry = AdapterRegistry(project=project, project_sources=project_sources)
    registry.register(
        DuckDBAdapter(source_config=source_cfg, data_dir=project_root, read_only=True)
    )
    return registry


def _setup_duckdb_project(
    tmp_path: Path, *, board_yml: str = _HEALTHY_BOARD, with_data: bool = True
) -> tuple[FilesystemProject, AdapterRegistry]:
    """Create a minimal project with a DuckDB file containing an `orders` table."""
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
    if with_data:
        conn.execute("CREATE TABLE orders (id INTEGER, revenue DOUBLE, region VARCHAR)")
        conn.execute(
            "INSERT INTO orders VALUES (1, 100.0, 'North'), (2, 200.0, 'South')"
        )
    conn.close()

    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    (charts_dir / "test_board.yml").write_text(board_yml)
    _write_dbt_charts_yml(tmp_path, db_path)

    return FilesystemProject(tmp_path), _make_registry(tmp_path, db_path)


def _validate(tmp_path: Path, project, registry):
    results = validate_paths(
        [tmp_path / "charts" / "test_board.yml"],
        project=project,
        adapter_registry=registry,
    )
    assert len(results) == 1
    return results[0]


class TestWarehouseValidateDuckDB:
    def test_healthy_board_passes(self, tmp_path):
        r = _validate(tmp_path, *_setup_duckdb_project(tmp_path))
        assert r.success is True
        assert r.errors == []

    def test_invalid_query_produces_err_warehouse_query_invalid(self, tmp_path):
        project, registry = _setup_duckdb_project(
            tmp_path,
            board_yml=_HEALTHY_BOARD.replace(
                "SELECT id, revenue FROM orders", "SELECT nonexistent_col FROM orders"
            ),
        )
        r = _validate(tmp_path, project, registry)
        assert r.success is False
        assert "ERR-WAREHOUSE-QUERY-INVALID" in {e.code for e in r.errors}

    def test_chart_channel_absent_from_result_produces_err(self, tmp_path):
        """The query returns id + revenue; the chart asks for missing_col."""
        project, registry = _setup_duckdb_project(
            tmp_path, board_yml=_HEALTHY_BOARD.replace("y: revenue", "y: missing_col")
        )
        r = _validate(tmp_path, project, registry)
        assert r.success is False
        err = next(e for e in r.errors if e.code == "ERR-CHART-COLUMN-NOT-IN-RESULT")
        assert "missing_col" in err.message
        assert "my_chart" in err.message

    def test_formats_naming_a_column_the_query_lacks_produces_err(self, tmp_path):
        project, registry = _setup_duckdb_project(
            tmp_path,
            board_yml=_HEALTHY_BOARD.replace(
                "SELECT id, revenue FROM orders\n",
                "\n    sql: SELECT id, revenue FROM orders\n"
                "    formats:\n      revenue: currency\n      totl: currency\n",
            ),
        )
        r = _validate(tmp_path, project, registry)
        err = next(e for e in r.errors if e.code == "ERR-QUERY-FORMAT-COLUMN-MISSING")
        assert "column 'totl'" in err.message
        assert "id, revenue" in err.message

    def test_layer_channel_is_checked_too(self, tmp_path):
        """A column referenced only from a layer must be checked like a top-level one."""
        project, registry = _setup_duckdb_project(
            tmp_path,
            board_yml=(
                "title: Layered Board\n"
                "source: testdb\n"
                "queries:\n"
                "  my_query: SELECT id, revenue FROM orders\n"
                "charts:\n"
                "  my_chart:\n"
                "    type: bar\n"
                "    query: queries.my_query\n"
                "    x: id\n"
                "    y: revenue\n"
                "    layers:\n"
                "      - type: line\n"
                "        y: missing_col\n"
            ),
        )
        r = _validate(tmp_path, project, registry)
        err = next(e for e in r.errors if e.code == "ERR-CHART-COLUMN-NOT-IN-RESULT")
        assert "missing_col" in err.message

    def test_each_entry_of_a_list_valued_channel_is_checked(self, tmp_path):
        """A multi-series `y: [a, b]` must be checked per entry.

        Only single-string channels are exercised anywhere else, so deleting
        the list branch of ``_chart_column_refs`` left the suite green while a
        typo in the second series rendered a silently missing series — the
        exact pass this tier exists to prevent.
        """
        project, registry = _setup_duckdb_project(
            tmp_path,
            board_yml=_HEALTHY_BOARD.replace(
                "    y: revenue\n", "    y: [revenue, missing_col]\n"
            ),
        )
        r = _validate(tmp_path, project, registry)
        assert r.success is False
        err = next(e for e in r.errors if e.code == "ERR-CHART-COLUMN-NOT-IN-RESULT")
        assert "y[1]" in err.message
        assert "missing_col" in err.message

    def test_table_columns_are_checked(self, tmp_path):
        """`type: table` names its columns in `columns:`, not in a channel."""
        project, registry = _setup_duckdb_project(
            tmp_path,
            board_yml=(
                "title: Table Board\n"
                "source: testdb\n"
                "queries:\n"
                "  my_query: SELECT id, revenue FROM orders\n"
                "charts:\n"
                "  my_chart:\n"
                "    type: table\n"
                "    query: queries.my_query\n"
                "    columns: [id, missing_col]\n"
            ),
        )
        r = _validate(tmp_path, project, registry)
        assert r.success is False
        err = next(e for e in r.errors if e.code == "ERR-CHART-COLUMN-NOT-IN-RESULT")
        assert "columns[1]" in err.message
        assert "missing_col" in err.message

    def test_sql_the_warehouse_refuses_fails_the_run(self, tmp_path):
        """A parser-level defect DuckDB refuses is an error, not a warning.

        `ORDER BY … ASC DESC` reaches ``_classify_duckdb_error``'s
        ERR-WAREHOUSE-RUNTIME catch-all, which the wrap-synthesis inference
        excused — so ``validate_paths`` reported success with a
        WARN-WAREHOUSE-CHECK-UNAVAILABLE, and the CLI exited 0, on SQL the
        warehouse had flatly refused.
        """
        project, registry = _setup_duckdb_project(
            tmp_path,
            board_yml=_HEALTHY_BOARD.replace(
                "SELECT id, revenue FROM orders",
                "SELECT id, revenue FROM orders ORDER BY id ASC DESC",
            ),
        )
        r = _validate(tmp_path, project, registry)
        assert r.success is False
        assert "ERR-WAREHOUSE-QUERY-INVALID" in {e.code for e in r.errors}
        assert "WARN-WAREHOUSE-CHECK-UNAVAILABLE" not in {w.code for w in r.warnings}

    def test_a_layer_is_checked_against_its_own_query(self, tmp_path):
        """`query:` on a layer overrides the chart's — check it against that one.

        The layer's `region` lives only in the layer's query. Checking it against
        the chart-level query rejects a board that renders fine.
        """
        project, registry = _setup_duckdb_project(
            tmp_path,
            board_yml=(
                "title: Two-Query Board\n"
                "source: testdb\n"
                "queries:\n"
                "  my_query: SELECT id, revenue FROM orders\n"
                "  regions: SELECT id, region FROM orders\n"
                "charts:\n"
                "  my_chart:\n"
                "    type: bar\n"
                "    query: queries.my_query\n"
                "    x: id\n"
                "    y: revenue\n"
                "    layers:\n"
                "      - type: line\n"
                "        query: regions\n"
                "        x: id\n"
                "        y: region\n"
            ),
        )
        r = _validate(tmp_path, project, registry)
        assert r.errors == [], [e.message for e in r.errors]

    def test_a_multi_hop_query_chain_is_resolved_before_the_check(self, tmp_path):
        """`{{ queries.X }}` two hops deep must reach the warehouse rendered.

        AdapterRegistry composes refs in one non-recursive pass, so a chain
        would arrive with the inner `{{ queries.base }}` still in the SQL and
        come back as a syntax error on SQL that renders fine.
        """
        project, registry = _setup_duckdb_project(
            tmp_path,
            board_yml=(
                "title: Chained Board\n"
                "source: testdb\n"
                "queries:\n"
                "  base: SELECT id, revenue FROM orders\n"
                "  mid: SELECT id, revenue FROM {{ queries.base }}\n"
                "  top: SELECT id, revenue FROM {{ queries.mid }}\n"
                "charts:\n"
                "  my_chart:\n"
                "    type: bar\n"
                "    query: queries.top\n"
                "    x: id\n"
                "    y: revenue\n"
            ),
        )
        r = _validate(tmp_path, project, registry)
        assert r.errors == [], [e.message for e in r.errors]


def _setup_csv_project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject], *, board_yml: str
) -> tuple[FilesystemProject, AdapterRegistry]:
    """Create a minimal project with a csv file source containing an `orders` table."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "orders.csv").write_text("id,revenue\n1,100.0\n2,200.0\n")
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  marts:\n    type: csv\n    files:\n      orders: data/orders.csv\n"
    )
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    (charts_dir / "test_board.yml").write_text(board_yml)

    project = local_project(tmp_path)
    registry = AdapterRegistry(
        project=project,
        project_sources=load_project_sources(project),
        file_materializer=FileSourceMaterializer(project),
    )
    return project, registry


class TestWarehouseValidateFileSource:
    """A csv/json/parquet source is column-checked like duckdb, not skipped.

    Coverage for the ``--warehouse`` column check reaching a file source: it
    materializes the source's files onto DuckDB and runs the same DESCRIBE
    mechanism as a native duckdb source (see warehouse_check.py).
    """

    def test_csv_board_queries_are_column_checked(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        board_yml = (
            "title: CSV Board\n"
            "source: marts\n"
            "queries:\n"
            "  my_query: SELECT id, revenue FROM orders\n"
            "charts:\n"
            "  my_chart:\n"
            "    type: bar\n"
            "    query: queries.my_query\n"
            "    x: id\n"
            "    y: missing_col\n"
        )
        project, registry = _setup_csv_project(
            tmp_path, local_project, board_yml=board_yml
        )
        r = _validate(tmp_path, project, registry)
        assert r.success is False
        err = next(e for e in r.errors if e.code == "ERR-CHART-COLUMN-NOT-IN-RESULT")
        assert "missing_col" in err.message
        assert "my_chart" in err.message


class TestWarehouseTierIsAdditive:
    """The warehouse tier appends findings; it never replaces the stateless ones."""

    def test_stateless_error_still_reported_under_warehouse(self, tmp_path):
        """A chart naming a query that does not exist is a compile-tier error."""
        project, registry = _setup_duckdb_project(
            tmp_path,
            board_yml=_HEALTHY_BOARD.replace(
                "query: queries.my_query", "query: queries.no_such_query"
            ),
        )
        r = _validate(tmp_path, project, registry)
        assert r.success is False
        # The real compile-tier code, not a generic ERR-INTERNAL from the sweep.
        assert {e.code for e in r.errors} == {"ERR-UNKNOWN-QUERY"}

    def test_data_lint_still_runs_under_warehouse(self, tmp_path):
        """ProjectSession applies the alias lint after the tier, not instead of it."""
        from dbt_charts.agent_api.project_session import ProjectSession

        project, _ = _setup_duckdb_project(
            tmp_path,
            board_yml=_HEALTHY_BOARD + "alias: /data/nosuchsource/orders\n",
        )
        session = ProjectSession(project)
        paths = [tmp_path / "charts" / "test_board.yml"]

        plain = session.validate_paths(paths)[0]
        with_wh = session.validate_paths(paths, warehouse=True)[0]

        assert not plain.success  # the lint fires without the flag...
        assert {e.code for e in plain.errors} <= {e.code for e in with_wh.errors}

    def test_meta_file_still_routes_to_the_meta_validator(self, tmp_path):
        """--warehouse must not send a cascade fragment down the board path."""
        project, registry = _setup_duckdb_project(tmp_path)
        (tmp_path / "charts" / "meta.yml").write_text("source: testdb\n")
        results = validate_paths(
            [tmp_path / "charts" / "meta.yml"],
            project=project,
            adapter_registry=registry,
        )
        assert all(r.success for r in results), [e for r in results for e in r.errors]


class TestWarehouseValidateUnresolvableSource:
    def test_unresolvable_source_produces_an_error(self, tmp_path):
        project, registry = _setup_duckdb_project(
            tmp_path,
            board_yml=_HEALTHY_BOARD.replace("source: testdb", "source: nope"),
            with_data=False,
        )
        r = _validate(tmp_path, project, registry)
        assert r.success is False
        assert r.errors

    def test_unresolvable_source_stops_the_sweep(self, tmp_path):
        """A project-level fault is reported once, not once per query."""
        from dbt_charts.core.diagnostics import ERR_SOURCE_NOT_NAMED
        from dbt_charts.core.diagnostics.base import DbtChartsError

        project, _ = _setup_duckdb_project(tmp_path, with_data=False)
        registry = MagicMock()
        registry.resolve_query_source.side_effect = DbtChartsError.from_code(
            ERR_SOURCE_NOT_NAMED, available=[]
        )

        r = _validate(tmp_path, project, registry)
        registry.execute.assert_not_called()
        assert r.success is False

    def test_stopping_keeps_what_the_earlier_queries_found(self, tmp_path):
        """Dropping them hides a broken query behind an unrelated config fault.

        The author fixes the source, re-runs, and only then learns the first
        query was broken all along.
        """
        from dbt_charts.core.diagnostics import ERR_SOURCE_NOT_NAMED
        from dbt_charts.core.diagnostics.base import DbtChartsError

        project, registry = _setup_duckdb_project(
            tmp_path,
            board_yml=(
                "title: Mixed Board\n"
                "source: testdb\n"
                "queries:\n"
                "  a_broken: SELECT nonexistent_col FROM orders\n"
                "  z_unresolvable: SELECT id FROM orders\n"
                "charts:\n"
                "  my_chart:\n"
                "    type: bar\n"
                "    query: queries.a_broken\n"
                "    x: nonexistent_col\n"
                "    y: id\n"
                "  other_chart:\n"
                "    type: bar\n"
                "    query: queries.z_unresolvable\n"
                "    x: id\n"
                "    y: id\n"
            ),
        )
        real_resolve = registry.resolve_query_source

        def _resolve(query, **kwargs):
            if kwargs.get("query_name") == "z_unresolvable":
                raise DbtChartsError.from_code(ERR_SOURCE_NOT_NAMED, available=[])
            return real_resolve(query, **kwargs)

        registry.resolve_query_source = _resolve

        r = _validate(tmp_path, project, registry)
        codes = {e.code for e in r.errors}
        assert "ERR-WAREHOUSE-QUERY-INVALID" in codes, codes
        assert "ERR-SOURCE-NOT-NAMED" in codes, codes


def _postgres_registry() -> MagicMock:
    """A registry whose mechanism is EXPLAIN: validity only, no result schema."""
    from dbt_charts.core.compile.models.source import parse_source_config
    from dbt_charts.core.execute.adapters.base import QueryResult

    registry = MagicMock()
    registry.resolve_query_source.return_value = parse_source_config(
        {
            "type": "postgres",
            "host": "h",
            "dbname": "d",
            "user": "u",
            "password": "p",
        }
    )
    registry.execute.return_value = QueryResult(data=[{"QUERY PLAN": "Result"}])
    return registry


class TestWarehouseValidateExplainAdapter:
    """A validity-only mechanism (EXPLAIN) warns about columns, not the query."""

    def test_explain_valid_query_warns_column_check_unavailable(self, tmp_path):
        """The query was ruled valid, so the shortfall is columns-only —
        WARN-COLUMN-CHECK-UNAVAILABLE (as for BigQuery's schemaless
        multi-statement dry run), not the nothing-looked-at-it warning."""
        project, _ = _setup_duckdb_project(tmp_path, with_data=False)
        r = _validate(tmp_path, project, _postgres_registry())
        assert not r.errors, [e.model_dump() for e in r.errors]
        warn_codes = {w.code for w in r.warnings}
        assert "WARN-COLUMN-CHECK-UNAVAILABLE" in warn_codes
        assert "WARN-WAREHOUSE-CHECK-UNAVAILABLE" not in warn_codes

    def test_chart_column_refs_are_not_checked_without_a_schema(self, tmp_path):
        """A chart naming a column EXPLAIN cannot see must not error — the
        warning above is the whole answer, never a guessed mismatch."""
        project, _ = _setup_duckdb_project(tmp_path, with_data=False)
        r = _validate(tmp_path, project, _postgres_registry())
        assert "ERR-CHART-COLUMN-NOT-IN-RESULT" not in {e.code for e in r.errors}


class TestWarehouseValidateUncheckedAdapter:
    """An adapter with no cheap validity primitive must say so, not report a pass."""

    def _unchecked_registry(self) -> MagicMock:
        """A trino source — an adapter in the ``anything else`` row: it has no
        entry in ``_PREFIX_CHECKS`` and no dry run, unlike a csv/json/parquet
        file source, which resolves to DuckDB's own DESCRIBE.
        """
        from dbt_charts.core.compile.models.source import DbtTargetSourceConfig

        registry = MagicMock()
        registry.resolve_query_source.return_value = DbtTargetSourceConfig(type="trino")
        return registry

    def test_unchecked_adapter_warns_rather_than_passing_silently(self, tmp_path):
        project, _ = _setup_duckdb_project(tmp_path, with_data=False)
        r = _validate(tmp_path, project, self._unchecked_registry())
        warn_codes = {w.code for w in r.warnings}
        assert "WARN-WAREHOUSE-CHECK-UNAVAILABLE" in warn_codes
        # It was never checked, so it is not a column-check-only shortfall.
        assert "WARN-COLUMN-CHECK-UNAVAILABLE" not in warn_codes

    def test_unchecked_warning_is_suppressible_by_project_warnings_ignore(
        self, tmp_path
    ):
        """The warehouse tier's warnings go through the same suppression as the
        stateless ones, so a ``--warehouse --strict`` run has an escape hatch."""
        project, _ = _setup_duckdb_project(tmp_path, with_data=False)
        config = tmp_path / "dbt_charts.yml"
        config.write_text(
            config.read_text()
            + "warnings:\n  ignore:\n    - WARN-WAREHOUSE-CHECK-UNAVAILABLE\n"
        )
        r = _validate(tmp_path, project, self._unchecked_registry())
        assert "WARN-WAREHOUSE-CHECK-UNAVAILABLE" not in {w.code for w in r.warnings}

    def test_unchecked_warning_names_the_reason(self, tmp_path):
        """ "Unchecked" with no reason is not actionable — the reason is the message."""
        project, _ = _setup_duckdb_project(tmp_path, with_data=False)
        r = _validate(tmp_path, project, self._unchecked_registry())
        warn = next(
            w for w in r.warnings if w.code == "WARN-WAREHOUSE-CHECK-UNAVAILABLE"
        )
        assert "trino" in warn.message

    def test_unchecked_adapter_never_executes_anything(self, tmp_path):
        project, _ = _setup_duckdb_project(tmp_path, with_data=False)
        registry = self._unchecked_registry()
        _validate(tmp_path, project, registry)
        registry.execute.assert_not_called()


class TestNoWarehouseWithoutFlag:
    """Without --warehouse, validation is stateless: zero warehouse calls."""

    def test_project_session_validate_makes_no_warehouse_calls(self, tmp_path):
        """Drive the real CLI seam with a registry that detonates if touched."""
        from dbt_charts.agent_api.project_session import ProjectSession

        project, _ = _setup_duckdb_project(tmp_path)
        session = ProjectSession(project)

        def _boom(*args, **kwargs):
            raise AssertionError("warehouse I/O without --warehouse")

        registry = session.adapter_registry
        registry.execute = _boom  # type: ignore[method-assign]
        registry.resolve_query_source = _boom  # type: ignore[method-assign]

        results = session.validate_paths([tmp_path / "charts" / "test_board.yml"])
        assert len(results) == 1
        assert results[0].success is True


class TestNonSqlQueriesAreNotSilentlyPassed:
    """A query carrying no SQL must be reported unchecked, never skipped silently."""

    _VALUES_BOARD = (
        "title: Test Board\n"
        "source: testdb\n"
        "queries:\n"
        "  my_query:\n"
        "    rows:\n"
        "      - { id: 1, revenue: 100 }\n"
        "charts:\n"
        "  my_chart:\n"
        "    type: bar\n"
        "    query: queries.my_query\n"
        "    x: id\n"
        "    y: revenue\n"
    )

    def test_a_values_query_warns_rather_than_passing_silently(self, tmp_path):
        project, registry = _setup_duckdb_project(
            tmp_path, board_yml=self._VALUES_BOARD, with_data=False
        )
        r = _validate(tmp_path, project, registry)
        warn = next(
            (w for w in r.warnings if w.code == "WARN-WAREHOUSE-CHECK-UNAVAILABLE"),
            None,
        )
        assert warn is not None, [w.code for w in r.warnings]
        assert "values" in warn.message
