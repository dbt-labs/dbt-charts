"""Parity test: Phase-1 dct verbs emit consistent --json output.

All three assertions must hold for every verb in the parity-audit set:
  (a) JSON round-trips via model_validate_json
  (b) exclude_none=True applied — no null values in top-level keys
  (c) all top-level keys are valid model fields (no undocumented extras)

Adding a new verb: add one class that calls _assert_parity with its
model class and CLI invocation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from typer.testing import CliRunner

from dbt_charts.cli.main import app

runner = CliRunner()

_SOURCES_YAML = "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
_SIMPLE_BOARD = (
    "source: db\nqueries:\n  q:\n    sql: 'select 1 as x'\n"
    "charts:\n  c:\n    query: q\n    type: table\nrows:\n  - c\n"
)


def _assert_parity(output: str, model_class: type[BaseModel]) -> None:
    data: dict[str, Any] = json.loads(output)
    # (a) round-trips via model_validate_json
    model_class.model_validate_json(output)
    # (b) exclude_none=True applied — no top-level null values
    null_keys = [k for k, v in data.items() if v is None]
    assert not null_keys, f"{model_class.__name__}: null values at keys {null_keys}"
    # (c) no undocumented keys beyond model_fields
    extra = set(data.keys()) - set(model_class.model_fields.keys())
    assert not extra, f"{model_class.__name__}: unexpected keys {extra}"


class TestQueryParityJson:
    def test_parity(self, tmp_path: Path) -> None:
        from dbt_charts.agent_api.query import QueryBoardResult

        (tmp_path / "dbt_charts.yml").write_text(_SOURCES_YAML)
        (tmp_path / "test.yaml").write_text(_SIMPLE_BOARD)
        result = runner.invoke(
            app,
            [
                "query",
                str(tmp_path / "test.yaml"),
                "q",
                "--project-dir",
                str(tmp_path),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        _assert_parity(result.output, QueryBoardResult)


class TestImpactParityJson:
    def test_parity(self, tmp_path: Path) -> None:
        from dbt_charts.agent_api.impact import ColumnImpactResult

        (tmp_path / "dbt_charts.yml").write_text(_SOURCES_YAML)
        charts = tmp_path / "charts"
        charts.mkdir()
        (charts / "test.yaml").write_text(_SIMPLE_BOARD)
        result = runner.invoke(
            app,
            ["impact", "value", "--project-dir", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0, result.output
        _assert_parity(result.output, ColumnImpactResult)


_DESCRIBE_BOARD = (
    "title: Test\n"
    "queries:\n  q:\n    sql: 'select 1'\n    source: db\n"
    "charts:\n  c:\n    query: q\n    type: table\nrows:\n  - c\n"
)


class TestDescribeBoardParityJson:
    def test_parity(self, tmp_path: Path) -> None:
        from dbt_charts.agent_api.describe import DescribeBoardResult

        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        (tmp_path / "test.yml").write_text(_DESCRIBE_BOARD)
        result = runner.invoke(
            app,
            [
                "describe",
                str(tmp_path / "test.yml"),
                "--json",
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        _assert_parity(result.output, DescribeBoardResult)
