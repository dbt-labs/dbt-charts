"""`dct query --validate` compiles semantic SQL and prints the engine's error with
a "did you mean" instead of waiting for the warehouse run."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli import main as cli_main

from .._paths import DBT_CHARTS_DIR

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("metricflow_sql") is None,
    reason="metricflow-sql is not installed",
)

runner = CliRunner()


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    shutil.copytree(
        DBT_CHARTS_DIR / "tests" / "fixtures" / "metricflow-billing",
        tmp_path,
        dirs_exist_ok=True,
    )
    return tmp_path


def _validate(project_dir: Path, sql: str) -> tuple[int, str]:
    result = runner.invoke(
        cli_main.app,
        ["query", "db", sql, "--validate", "--project-dir", str(project_dir)],
    )
    # The console wraps long lines.
    return result.exit_code, " ".join(result.stdout.split())


def test_a_mistyped_calculation_exits_1_with_the_suggestion(project_dir: Path) -> None:
    code, out = _validate(project_dir, "SELECT AGG(invoiced_totl) FROM invoices")

    assert code == 1
    assert "could not be resolved" in out
    assert "Did you mean `invoiced_total`?" in out


def test_valid_semantic_sql_has_no_issues(project_dir: Path) -> None:
    code, out = _validate(
        project_dir,
        "SELECT account.industry, AGG(invoiced_total) FROM invoices GROUP BY 1",
    )

    assert code == 0
    assert "No issues found." in out
