"""CLI-level integration test: --dbt-project-dir links an external dbt project.

Proves the seams composed in cli/_project.py (resolve_dbt_project_dir),
cli/filesystem_project.py (dbt_root/dbt_project), core/dbt_manifest.py
(manifest_project dispatch), and core/execute/env.py
(the dbt_root-based sibling rule) work together end-to-end: a board's
ref('orders') resolves against an external directory's target/manifest.json,
not the dct project's own (nonexistent) one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

runner = CliRunner()

_BOARD = (
    "title: B\nsource: s\nqueries:\n"
    "  o: SELECT id FROM {{ ref('orders') }}\n"
    "charts:\n  c:\n    query: o\n    type: table\nrows:\n  - c\n"
)


def _manifest_json() -> str:
    return json.dumps(
        {
            "metadata": {"adapter_type": "postgres"},
            "nodes": {
                "model.p.orders": {
                    "resource_type": "model",
                    "name": "orders",
                    "schema": "main",
                    "raw_code": "SELECT id FROM raw",
                }
            },
        }
    )


def test_dbt_project_dir_flag_resolves_ref_against_external_manifest(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "myproject"
    (project_dir / "charts").mkdir(parents=True)
    (project_dir / "dbt_charts.yml").write_text("name: p\n")
    (project_dir / "charts" / "board.yaml").write_text(_BOARD)

    external_dbt = tmp_path / "external_dbt"
    (external_dbt / "target").mkdir(parents=True)
    (external_dbt / "target" / "manifest.json").write_text(_manifest_json())

    result = runner.invoke(
        app,
        [
            "validate",
            "charts",
            "--project-dir",
            str(project_dir),
            "--dbt-project-dir",
            str(external_dbt),
            "--strict",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "WARN-DBT-MANIFEST-MISSING" not in result.output


def test_without_dbt_project_dir_ref_is_unresolved(tmp_path: Path) -> None:
    """Control: the same board, no --dbt-project-dir -> no manifest at the dct
    project root -> ref('orders') cannot be validated (warns, not linked)."""
    project_dir = tmp_path / "myproject"
    (project_dir / "charts").mkdir(parents=True)
    (project_dir / "dbt_charts.yml").write_text("name: p\n")
    (project_dir / "charts" / "board.yaml").write_text(_BOARD)

    # The external dbt project exists on disk but is never linked.
    external_dbt = tmp_path / "external_dbt"
    (external_dbt / "target").mkdir(parents=True)
    (external_dbt / "target" / "manifest.json").write_text(_manifest_json())

    result = runner.invoke(
        app, ["validate", "charts", "--project-dir", str(project_dir), "--strict"]
    )

    assert result.exit_code == 1, result.output
    assert "WARN-DBT-MANIFEST-MISSING" in result.output


def test_dbt_project_dir_env_var_resolves_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DBT_PROJECT_DIR env var (dbt-core's own name) works the same as the flag."""
    project_dir = tmp_path / "myproject"
    (project_dir / "charts").mkdir(parents=True)
    (project_dir / "dbt_charts.yml").write_text("name: p\n")
    (project_dir / "charts" / "board.yaml").write_text(_BOARD)

    external_dbt = tmp_path / "external_dbt"
    (external_dbt / "target").mkdir(parents=True)
    (external_dbt / "target" / "manifest.json").write_text(_manifest_json())

    monkeypatch.setenv("DBT_PROJECT_DIR", str(external_dbt))
    result = runner.invoke(
        app, ["validate", "charts", "--project-dir", str(project_dir), "--strict"]
    )

    assert result.exit_code == 0, result.output
    assert "WARN-DBT-MANIFEST-MISSING" not in result.output


def test_dbt_project_dir_config_key_resolves_ref(tmp_path: Path) -> None:
    """dbt_project_dir: key in dbt_charts.yml links an external directory
    when no flag/env var is given (lowest-priority branch)."""
    project_dir = tmp_path / "myproject"
    (project_dir / "charts").mkdir(parents=True)
    (project_dir / "charts" / "board.yaml").write_text(_BOARD)

    external_dbt = tmp_path / "external_dbt"
    (external_dbt / "target").mkdir(parents=True)
    (external_dbt / "target" / "manifest.json").write_text(_manifest_json())
    (project_dir / "dbt_charts.yml").write_text(
        "name: p\ndbt_project_dir: ../external_dbt\n"
    )

    result = runner.invoke(
        app, ["validate", "charts", "--project-dir", str(project_dir), "--strict"]
    )

    assert result.exit_code == 0, result.output
    assert "WARN-DBT-MANIFEST-MISSING" not in result.output


def test_bad_dbt_project_dir_config_type_is_a_clean_cli_error(tmp_path: Path) -> None:
    project_dir = tmp_path / "myproject"
    (project_dir / "charts").mkdir(parents=True)
    (project_dir / "dbt_charts.yml").write_text("dbt_project_dir: 3\n")

    result = runner.invoke(
        app, ["validate", "charts", "--project-dir", str(project_dir)]
    )

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    # Rich wraps long error lines on word boundaries, so match against
    # whitespace-collapsed output rather than a literal substring.
    assert "dbt_project_dir must be a string" in " ".join(result.output.split())


def test_missing_dbt_project_dir_config_target_is_a_clean_cli_error(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "myproject"
    (project_dir / "charts").mkdir(parents=True)
    (project_dir / "dbt_charts.yml").write_text("dbt_project_dir: ../typo_dbt\n")

    result = runner.invoke(
        app, ["validate", "charts", "--project-dir", str(project_dir)]
    )

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "does not exist" in " ".join(result.output.split())


def test_malformed_dbt_charts_yml_is_a_clean_cli_error(tmp_path: Path) -> None:
    project_dir = tmp_path / "myproject"
    (project_dir / "charts").mkdir(parents=True)
    (project_dir / "dbt_charts.yml").write_text("dbt_project_dir: [unterminated\n")

    result = runner.invoke(
        app, ["validate", "charts", "--project-dir", str(project_dir)]
    )

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
