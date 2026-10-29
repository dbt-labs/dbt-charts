"""Semantic SQL on a source with a `metricflow:` bundle compiles in process, no Trino.

The bundle is MetricFlow's v1 fragment format; `metricflow-sql` compiles `AGG()`
and relationship paths to the warehouse dialect, and the source's own driver runs
the result. A query that reads none of the bundle's datasets or constellations is
untouched.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml
from pydantic import ValidationError

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import (
    CsvSourceConfig,
    DbtProfileSourceConfig,
    DuckDBSourceConfig,
)
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry

FRAGMENT = """\
schema_version: v1
bundle: shop
generation_meta: {}
dialect: duckdb
datasets:
- name: accounts
  source:
    location: warehouse.main.accounts
    columns:
    - {name: id, datatype: VARCHAR}
    - {name: industry, datatype: VARCHAR}
  key: [id]
- name: invoices
  source:
    location: warehouse.main.invoices
    columns:
    - {name: id, datatype: VARCHAR}
    - {name: account_id, datatype: VARCHAR}
    - {name: total, datatype: DOUBLE}
    - {name: created_at, datatype: TIMESTAMP}
  key: [id]
  calculations:
  - {name: invoiced_total, expr: SUM(total)}
  - {name: invoice_count, expr: COUNT(*)}
  relationships:
  - name: account
    target: account
    key: [{name: id, expr: account_id}]
- name: charges
  source:
    location: warehouse.main.charges
    columns:
    - {name: id, datatype: VARCHAR}
    - {name: account_id, datatype: VARCHAR}
    - {name: amount, datatype: DOUBLE}
    - {name: created_at, datatype: TIMESTAMP}
  key: [id]
  calculations:
  - {name: charged_amount, expr: SUM(amount)}
  relationships:
  - name: account
    target: account
    key: [{name: id, expr: account_id}]
entities:
- name: account
  key: [{name: id, datatype: VARCHAR}]
  sources: [{dataset: accounts, on: id = :id}]
  attributes:
  - {name: industry, expr: accounts.industry}
constellations:
- name: billing
  roles:
  - {name: account, datatype: entity('account')}
  - {name: month, datatype: TIMESTAMP}
  metrics:
  - {name: invoiced_total, expr: AGG(invoices.invoiced_total)}
  - {name: charged_amount, expr: AGG(charges.charged_amount)}
  assignments:
  - dataset: invoices
    roles:
    - {name: account, expr: account}
    - {name: month, expr: "DATE_TRUNC('month', created_at)"}
  - dataset: charges
    roles:
    - {name: account, expr: account}
    - {name: month, expr: "DATE_TRUNC('month', created_at)"}
"""

_VARIABLES = """\
variables:
  since:
    input: select
    default: '2026-02-01'
    options:
      static: ['2026-01-01', '2026-02-01']
"""


def _project(tmp_path: Path, metricflow: str | None = "semantics") -> FilesystemProject:
    con = duckdb.connect(str(tmp_path / "warehouse.duckdb"))
    con.execute("CREATE TABLE accounts (id VARCHAR, industry VARCHAR)")
    con.execute("INSERT INTO accounts VALUES ('a1', 'retail'), ('a2', 'media')")
    con.execute(
        "CREATE TABLE invoices "
        "(id VARCHAR, account_id VARCHAR, total DOUBLE, created_at TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO invoices VALUES ('i1', 'a1', 100, '2026-01-05'), "
        "('i2', 'a1', 50, '2026-02-05'), ('i3', 'a2', 10, '2026-02-10')"
    )
    con.execute(
        "CREATE TABLE charges "
        "(id VARCHAR, account_id VARCHAR, amount DOUBLE, created_at TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO charges VALUES ('c1', 'a1', 120, '2026-01-06'), "
        "('c2', 'a2', 5, '2026-02-11')"
    )
    con.execute("CREATE TABLE raw_events (id INTEGER)")
    con.execute("INSERT INTO raw_events VALUES (1), (2), (3)")
    con.close()
    (tmp_path / "semantics").mkdir()
    (tmp_path / "semantics" / "fragment.yaml").write_text(FRAGMENT)
    (tmp_path / "empty").mkdir()
    source: dict[str, Any] = {"type": "duckdb", "path": "warehouse.duckdb"}
    if metricflow is not None:
        source["metricflow"] = metricflow
    (tmp_path / "dbt_charts.yml").write_text(
        yaml.safe_dump({"sources": {"db": source}})
    )
    return FilesystemProject(tmp_path)


def _board(project: FilesystemProject, sql: str) -> Any:
    """A compiled board whose one query, `q`, is `sql`."""
    board_yaml = _VARIABLES + (
        "queries:\n  q:\n    type: sql\n    source: db\n    sql: |\n"
        f"{textwrap.indent(sql, '      ')}\n"
        "charts:\n  c:\n    type: table\n    query: q\nrows:\n  - c\n"
    )
    result = compile(board_yaml, project_sources=project.sources)
    assert result.success, result.errors
    assert result.board is not None
    return result


def _run(
    project: FilesystemProject, sql: str, variables: dict[str, Any] | None = None
) -> Any:
    """Run `sql` as a board query named `q`; the QueryResult."""
    result = _board(project, sql)
    registry = build_adapter_registry(project)
    return registry.execute(
        result.board.queries["q"], variables or {}, board=result.board, query_name="q"
    )


def _rows(result: Any) -> list[tuple[Any, ...]]:
    assert result.error is None, result.error
    return sorted((tuple(row.values()) for row in result.data), key=repr)


needs_metricflow = pytest.mark.skipif(
    importlib.util.find_spec("metricflow_sql") is None,
    reason="metricflow-sql is not installed",
)


@needs_metricflow
def test_dataset_query_compiles_and_runs(tmp_path: Path) -> None:
    rows = _rows(
        _run(
            _project(tmp_path),
            "SELECT account.industry AS industry, AGG(invoiced_total) AS total\n"
            "FROM invoices GROUP BY 1",
        )
    )
    assert rows == [("media", 10.0), ("retail", 150.0)]


@needs_metricflow
def test_plain_query_on_a_semantic_source_is_untouched(tmp_path: Path) -> None:
    rows = _rows(_run(_project(tmp_path), "SELECT COUNT(*) AS n FROM raw_events"))
    assert rows == [(3,)]


@needs_metricflow
def test_variables_bind_once_per_branch_of_a_constellation(tmp_path: Path) -> None:
    """A constellation copies the WHERE into every dataset branch; each copy binds."""
    rows = _rows(
        _run(
            _project(tmp_path),
            "SELECT account.industry AS industry, AGG(invoiced_total) AS invoiced,\n"
            "  AGG(charged_amount) AS charged\n"
            "FROM billing WHERE month >= '{{ since }}' GROUP BY 1",
            {"since": "2026-02-01"},
        )
    )
    assert rows == [("media", 10.0, 5.0), ("retail", 50.0, None)]


@needs_metricflow
def test_variable_in_a_dataset_query_binds(tmp_path: Path) -> None:
    rows = _rows(
        _run(
            _project(tmp_path),
            "SELECT AGG(invoice_count) AS n FROM invoices WHERE created_at >= '{{ since }}'",
            {"since": "2026-02-01"},
        )
    )
    assert rows == [(2,)]


@needs_metricflow
def test_ad_hoc_sql_without_a_board_compiles(tmp_path: Path) -> None:
    registry = build_adapter_registry(_project(tmp_path))
    result = registry.execute(
        SqlQuery(sql="SELECT AGG(invoiced_total) AS total FROM invoices", source="db")
    )
    assert _rows(result) == [(160.0,)]


@needs_metricflow
def test_unknown_attribute_is_a_named_error(tmp_path: Path) -> None:
    result = _run(
        _project(tmp_path),
        "SELECT account.region, AGG(invoiced_total) FROM invoices GROUP BY 1",
    )
    assert result.error is not None
    assert result.error.code.code == "ERR-METRICFLOW-COMPILE-FAILED"
    assert "region" in str(result.error)


@needs_metricflow
def test_a_table_outside_the_bundle_beside_a_dataset_fails_loudly(
    tmp_path: Path,
) -> None:
    result = _run(
        _project(tmp_path),
        "SELECT AGG(invoiced_total) FROM invoices JOIN raw_events ON TRUE",
    )
    assert result.error is not None
    assert result.error.code.code == "ERR-METRICFLOW-COMPILE-FAILED"
    assert "raw_events" in str(result.error)


@needs_metricflow
def test_bundle_without_fragments_is_a_named_error(tmp_path: Path) -> None:
    result = _run(
        _project(tmp_path, metricflow="empty"),
        "SELECT AGG(invoiced_total) FROM invoices",
    )
    assert result.error is not None
    assert result.error.code.code == "ERR-METRICFLOW-BUNDLE-INVALID"
    assert "empty" in str(result.error)


def test_a_bundle_path_outside_the_project_is_a_named_error(tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "fragment.yaml").write_text(FRAGMENT)
    project = _project(tmp_path)
    (tmp_path / "dbt_charts.yml").write_text(
        yaml.safe_dump(
            {
                "sources": {
                    "db": {
                        "type": "duckdb",
                        "path": "warehouse.duckdb",
                        "metricflow": "../elsewhere",
                    }
                }
            }
        )
    )
    result = _run(project, "SELECT AGG(invoiced_total) FROM invoices")
    assert result.error is not None
    assert result.error.code.code == "ERR-METRICFLOW-BUNDLE-INVALID"
    assert "../elsewhere" in str(result.error)


def test_missing_library_is_a_named_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "metricflow_sql", None)
    result = _run(_project(tmp_path), "SELECT AGG(invoiced_total) FROM invoices")
    assert result.error is not None
    assert result.error.code.code == "ERR-METRICFLOW-NOT-INSTALLED"
    assert "metricflow-sql" in str(result.error)


def test_source_without_a_bundle_never_loads_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "metricflow_sql", None)
    rows = _rows(
        _run(
            _project(tmp_path, metricflow=None), "SELECT COUNT(*) AS n FROM raw_events"
        )
    )
    assert rows == [(3,)]


@needs_metricflow
def test_the_layer_is_reused_while_the_bundle_is_unchanged(tmp_path: Path) -> None:
    project = _project(tmp_path)
    registry = build_adapter_registry(project)
    source = registry.resolve_query_source(SqlQuery(sql="SELECT 1", source="db"))
    assert source is not None
    first = registry.semantic_layer(source)
    assert first is registry.semantic_layer(source)
    (tmp_path / "semantics" / "fragment.yaml").write_text(
        FRAGMENT.replace("name: invoice_count", "name: invoice_rows")
    )
    assert registry.semantic_layer(source) is not first


def test_a_changed_bundle_changes_the_cache_key(tmp_path: Path) -> None:
    """Cached rows, or a cached error, from the old bundle must not be replayed.

    One render memoizes the version per source, so the next render is what sees
    the edit.
    """
    project = _project(tmp_path)
    result = _board(project, "SELECT AGG(invoiced_total) AS total FROM invoices")

    def key() -> tuple[str, str, str]:
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(project),
            query_registry=result.query_registry,
        )
        return executor._cache_key("q", result.board.queries["q"], {})

    before = key()
    assert before == key()
    (tmp_path / "semantics" / "fragment.yaml").write_text(
        FRAGMENT.replace("expr: SUM(total)", "expr: SUM(total) * 2")
    )
    assert key() != before


def test_metricflow_is_accepted_on_warehouse_sources_only() -> None:
    assert (
        DuckDBSourceConfig(
            type="duckdb", path="w.duckdb", metricflow="semantics"
        ).metricflow
        == "semantics"
    )
    assert (
        DbtProfileSourceConfig(
            type="dbt_profile", metricflow="target/metricflow"
        ).metricflow
        == "target/metricflow"
    )
    with pytest.raises(ValidationError, match="metricflow"):
        CsvSourceConfig(type="csv", files={"t": "t.csv"}, metricflow="semantics")
