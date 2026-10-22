"""Tests for `dct exposures` — thin wrapper over agent_api.exposures."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dbt_charts.cli import main as cli_main

runner = CliRunner()

_CONFIG = """\
sources:
  db:
    type: duckdb
    path: ':memory:'
exposures:
  owner:
    name: Analytics
"""

_BOARD = """\
title: Sales
source: db
queries:
  o: "SELECT * FROM {{ ref('fct_orders') }}"
charts:
  c:
    query: o
    type: table
rows:
  - c
"""


def _seed(tmp_path: Path, config: str = _CONFIG) -> None:
    (tmp_path / "dbt_charts.yml").write_text(config)
    (tmp_path / "dbt_project.yml").write_text("name: shop\nmodel-paths: [models]\n")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "sales.yml").write_text(_BOARD)


def _run(tmp_path: Path, *args: str) -> tuple[int, str, str]:
    result = runner.invoke(
        cli_main.app, ["exposures", *args, "--project-dir", str(tmp_path)]
    )
    return result.exit_code, result.stdout, result.stderr


def test_check_exits_1_when_stale_and_0_when_current(tmp_path: Path) -> None:
    _seed(tmp_path)

    assert _run(tmp_path, "--check")[0] == 1
    assert _run(tmp_path)[0] == 0
    assert _run(tmp_path, "--check")[0] == 0


def test_error_exits_1_with_message_on_stderr(tmp_path: Path) -> None:
    _seed(tmp_path, _CONFIG + "  path: ../outside.yml\n")

    code, _, stderr = _run(tmp_path)

    assert code == 1
    assert "exposures.path" in stderr


def test_json_lists_exposures(tmp_path: Path) -> None:
    _seed(tmp_path)

    code, stdout, _ = _run(tmp_path, "--json")

    assert code == 0
    payload = json.loads(stdout)
    assert [e["name"] for e in payload["exposures"]] == ["sales"]
    assert payload["changed"] is True
