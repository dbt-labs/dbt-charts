from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

from ._paths import DBT_CHARTS_DIR


def test_git_init_writes_no_hook_samples(tmp_path: Path) -> None:
    template_dir = os.environ.get("GIT_TEMPLATE_DIR")

    assert template_dir
    assert list(Path(template_dir).iterdir()) == []

    result = subprocess.run(
        ["git", "init"], cwd=tmp_path, text=True, capture_output=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert list((tmp_path / ".git" / "hooks").glob("*.sample")) == []


def test_xdist_workers_are_capped() -> None:
    pyproject = tomllib.loads((DBT_CHARTS_DIR / "pyproject.toml").read_text("utf-8"))

    assert "--maxprocesses=6" in pyproject["tool"]["pytest"]["ini_options"]["addopts"]


def test_pg_cluster_users_are_marked_postgres() -> None:
    """Hosts without `pg_ctl` deselect these with `-m "not postgres"`."""
    tests = DBT_CHARTS_DIR / "tests"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "not postgres",
            str(tests / "integration" / "test_postgres_explain_check.py"),
            str(tests / "core" / "test_variable_sql_cross_dialect.py"),
        ],
        cwd=DBT_CHARTS_DIR,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "TestDuckDB" in result.stdout
    assert "test_unreachable_server_is_unchecked_not_invalid" in result.stdout
    assert "test_dropped_column_fails_through_a_real_explain" not in result.stdout
    assert "TestPostgres" not in result.stdout
