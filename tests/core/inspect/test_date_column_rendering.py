"""End-to-end regression: DATE columns in the inspect sample-data table.

Before this PR the model.yml used SQL strftime() inside a Jinja template
to format date values. After the fix, DuckDB returns raw datetime.date objects
that the render layer formats via format_table_cell_value / date_short.

This test proves the cascade from real DuckDB table → render_inspect_dashboard
→ rendered HTML actually contains a human-readable date string.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject


def _template(name: str) -> str:
    return (
        files("dbt_charts.core.inspect.templates").joinpath(f"{name}.yml").read_text()
    )


def _make_project_with_dates(tmp_path: Path) -> Path:
    """Build a project root with a DuckDB table containing a DATE column."""
    import duckdb

    project = tmp_path / "project"
    project.mkdir()
    target = project / "target"
    target.mkdir()

    db_path = project / "warehouse.duckdb"
    con = duckdb.connect(str(db_path))
    # Create in default main schema so SELECT * FROM events works without prefix.
    con.execute(
        "CREATE TABLE events AS "
        "SELECT 1 AS id, DATE '2024-05-19' AS event_date, 'launch' AS label"
    )
    con.close()

    # Minimal super_schema so the renderer builds nav_links / column hints.
    super_schema = {
        "version": 2,
        "generated_at": "2025-01-01T00:00:00+00:00",
        "tables": {
            "events": {
                "table_name": "events",
                "schema_name": "",
                "row_count": 1,
                "column_count": 3,
                "profiled_at": "2025-01-01T00:00:00",
                "columns": [
                    {
                        "name": "id",
                        "database_type": "INTEGER",
                        "order": 0,
                        "role": "identifier",
                        "distribution": "unique",
                        "completeness": "complete",
                        "null_percentage": 0,
                        "distinct_count": 1,
                        "uniqueness_ratio": 1.0,
                    },
                    {
                        "name": "event_date",
                        "database_type": "DATE",
                        "order": 1,
                        "role": "dimension",
                        "distribution": "temporal",
                        "completeness": "complete",
                        "null_percentage": 0,
                        "distinct_count": 1,
                        "uniqueness_ratio": 1.0,
                    },
                    {
                        "name": "label",
                        "database_type": "VARCHAR",
                        "order": 2,
                        "role": "dimension",
                        "distribution": "categorical",
                        "completeness": "complete",
                        "null_percentage": 0,
                        "distinct_count": 1,
                        "uniqueness_ratio": 1.0,
                    },
                ],
            }
        },
    }
    (target / "super_schema.json").write_text(json.dumps(super_schema))
    return project


def test_date_column_renders_human_readable_in_sample_data(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """DATE values in inspect sample-data must appear as '19 May 2024', not as
    machine-readable ISO strings or Python repr."""
    from dbt_charts.core.execute.env import build_exec_env
    from dbt_charts.core.inspect.renderer import render_inspect_dashboard

    project_root = _make_project_with_dates(tmp_path)
    db_path = str(project_root / "warehouse.duckdb")
    (project_root / "dbt_charts.yml").write_text(
        f"sources:\n  warehouse:\n    type: duckdb\n    path: '{db_path}'\n"
    )
    project = local_project(project_root)
    env = build_exec_env(project)
    html = render_inspect_dashboard(
        _template("model"),
        {"model": "events"},
        project=project,
        env=env,
    )

    # The formatted date must appear in the output.
    assert "19 May 2024" in html, (
        "Expected '19 May 2024' in rendered HTML; got raw datetime.date repr instead"
    )
    # Sanity: the raw ISO string must NOT appear as a cell value
    # (it would mean the render path bypassed format_table_cell_value).
    assert "2024-05-19" not in html, (
        "Raw ISO date '2024-05-19' appeared in HTML — datetime formatting not applied"
    )
