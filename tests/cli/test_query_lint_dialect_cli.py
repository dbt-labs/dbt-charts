"""dct query --validate/--describe lint with the source's dialect unless --dialect is given."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app
from dbt_charts.core.execute.warehouse_check import WarehouseCheck, WarehouseCheckColumn

runner = CliRunner()

# MySQL-only syntax: a parse error under the generic dialect.
_MYSQL_ONLY = "SELECT name FROM t FORCE INDEX (i)"


@pytest.fixture
def mysql_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MYSQL_PASSWORD", "x")
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n"
        "  wh:\n"
        "    type: mysql\n"
        "    host: localhost\n"
        "    database: d\n"
        "    user: u\n"
        "    password: \"{{ env_var('MYSQL_PASSWORD') }}\"\n"
    )
    return tmp_path


def _query(project: Path, *args: str, sql: str = _MYSQL_ONLY):
    return runner.invoke(
        app, ["query", "wh", sql, "--project-dir", str(project), *args]
    )


def test_validate_uses_source_dialect(mysql_project: Path) -> None:
    result = _query(mysql_project, "--validate")
    assert result.exit_code == 0, result.output
    assert "WARN-PARSE-ERROR" not in result.output


def test_explicit_dialect_wins_over_source(mysql_project: Path) -> None:
    result = _query(mysql_project, "--validate", "--dialect", "postgres")
    assert result.exit_code == 1, result.output
    assert "WARN-PARSE-ERROR" in result.output


def test_explicit_sqlserver_dialect_is_accepted(mysql_project: Path) -> None:
    result = _query(
        mysql_project,
        "--validate",
        "--dialect",
        "sqlserver",
        sql="SELECT TOP 3 name FROM sys.objects",
    )
    assert result.exit_code == 0, result.output


def test_unknown_dialect_is_a_clear_error(mysql_project: Path) -> None:
    result = _query(mysql_project, "--validate", "--dialect", "nope")
    assert result.exit_code == 1
    assert "Unknown SQL dialect 'nope'" in result.output
    assert not isinstance(result.exception, ValueError)


@pytest.fixture
def stub_warehouse_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from dbt_charts.agent_api import describe_query as mod

    def _check(
        sql: str, *, source: str | None, adapter_registry: object
    ) -> WarehouseCheck:
        return WarehouseCheck(
            status="valid",
            adapter_type="mysql",
            mechanism="dry-run",
            columns_checked=True,
            columns=[WarehouseCheckColumn(name="name", type="VARCHAR")],
        )

    monkeypatch.setattr(mod, "check_ad_hoc_query", _check)


@pytest.mark.usefixtures("stub_warehouse_check")
def test_describe_uses_source_dialect(mysql_project: Path) -> None:
    result = _query(mysql_project, "--describe", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["success"] is True


@pytest.mark.usefixtures("stub_warehouse_check")
def test_validate_and_describe_use_source_dialect(mysql_project: Path) -> None:
    result = _query(mysql_project, "--validate", "--describe", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["describe"]["success"] is True


def test_unknown_dialect_is_a_clear_error_for_describe(mysql_project: Path) -> None:
    result = _query(mysql_project, "--describe", "--dialect", "nope")
    assert result.exit_code == 1
    assert "Unknown SQL dialect 'nope'" in result.output
    assert not isinstance(result.exception, ValueError)


def test_named_query_validate_uses_source_dialect(mysql_project: Path) -> None:
    board = mysql_project / "board.yaml"
    board.write_text(
        "source: wh\n"
        "queries:\n"
        "  q:\n"
        f"    sql: {_MYSQL_ONLY}\n"
        "charts:\n"
        "  c:\n"
        "    query: q\n"
        "    type: table\n"
        "rows:\n"
        "  - c\n"
    )
    result = runner.invoke(
        app,
        ["query", str(board), "q", "--validate", "--project-dir", str(mysql_project)],
    )
    assert result.exit_code == 0, result.output
    assert "WARN-PARSE-ERROR" not in result.output
