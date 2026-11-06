"""Regression: render_inspect_dashboard resolves schema_name from resolver, not cache.

Cold start = dbt project + DuckDB adapter, no target/super_schema.json.
Schema-shaped panels must render (column inventory appears in HTML) without
a cache file. The old implementation would fall through to schema_name=""
on cold start and miss the schema; the new implementation walks the resolver
to find the schema containing the model.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

import duckdb
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.inspect.renderer import render_inspect_dashboard


def _template(name: str) -> str:
    return (
        files("dbt_charts.core.inspect.templates").joinpath(f"{name}.yml").read_text()
    )


@pytest.fixture
def cold_project(tmp_path: Path) -> Path:
    """Project root with a DuckDB file and NO target/super_schema.json."""
    project = tmp_path / "project"
    project.mkdir()
    # Intentionally no target/ directory — cold start.

    db_path = project / "warehouse.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE main.orders AS "
        "SELECT range::INTEGER AS order_id, "
        "       (range * 10)::DOUBLE AS amount, "
        "       CASE WHEN range % 2 = 0 THEN 'active' ELSE 'pending' END AS status "
        "FROM range(10)"
    )
    con.close()
    (project / "dbt_charts.yml").write_text(
        f"sources:\n  warehouse:\n    type: duckdb\n    path: '{db_path}'\n"
    )

    return project


def test_cold_start_no_cache_renders_column_inventory(
    cold_project: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """schema_name must resolve via the resolver on cold start (no cache file)."""
    assert not (cold_project / "target" / "super_schema.json").exists()

    project = local_project(cold_project)
    env = build_exec_env(project)
    html = render_inspect_dashboard(
        _template("model"),
        {"model": "orders"},
        project=project,
        env=env,
    )

    # The resolver should discover schema "main" for table "orders" and the
    # model template's schema queries should return column rows.
    assert "orders" in html
    # Without the fix schema_name="" and resolver queries return no columns;
    # with the fix the column names appear as text content in the rendered HTML.
    # We check for the column name as a standalone word to avoid matching HTML
    # attribute substrings (e.g. "id=" which appears everywhere in attributes).
    for col in ("order_id", "amount", "status"):
        assert f">{col}<" in html or f" {col} " in html or f">{col} " in html, (
            f"expected column '{col}' as rendered text in HTML but it was absent"
        )


def test_cold_start_no_cache_file_not_created(
    cold_project: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Rendering must not create a super_schema.json as a side effect."""
    project = local_project(cold_project)
    env = build_exec_env(project)
    render_inspect_dashboard(
        _template("model"),
        {"model": "orders"},
        project=project,
        env=env,
    )
    assert not (cold_project / "target" / "super_schema.json").exists()
