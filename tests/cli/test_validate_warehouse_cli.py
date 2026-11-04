"""CLI surface for ``dct validate --warehouse``.

The flag is the whole reason the tier exists, so these run the real command
against a real DuckDB file: exit codes, and the pin that without the flag the
CLI opens no warehouse connection at all.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

runner = CliRunner()

_BOARD = (
    "title: Sales\n"
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


_VALUES_BOARD = _BOARD.replace(
    "  my_query: SELECT id, revenue FROM orders\n",
    "  my_query:\n    columns: [id, revenue]\n    values:\n      - [1, 2.5]\n",
)


def _project(tmp_path: Path, board_yml: str = _BOARD) -> Path:
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE orders (id INTEGER, revenue DOUBLE)")
    conn.close()
    (tmp_path / "dbt_charts.yml").write_text(
        f"sources:\n  testdb:\n    type: duckdb\n    path: {db_path}\n"
    )
    (tmp_path / "charts").mkdir()
    board = tmp_path / "charts" / "sales.yml"
    board.write_text(board_yml)
    return board


def _run(project_dir: Path, *args: str):
    return runner.invoke(app, ["validate", *args, "--project-dir", str(project_dir)])


def test_warehouse_flag_passes_on_a_healthy_board(tmp_path):
    result = _run(tmp_path, str(_project(tmp_path)), "--warehouse")
    assert result.exit_code == 0, result.output


def test_warehouse_flag_reports_an_invalid_query(tmp_path):
    board = _project(
        tmp_path,
        _BOARD.replace("SELECT id, revenue FROM orders", "SELECT nope FROM orders"),
    )
    result = _run(tmp_path, str(board), "--warehouse")
    assert result.exit_code == 1
    assert "ERR-WAREHOUSE-QUERY-INVALID" in result.output


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, revenue FROM orders ORDER BY id ASC DESC",
        "SELECT id, revenue FROM orders WHERE id IN ()",
        "SELECT id, revenue FROM orders LIMIT 1, 2",
        "SELECT id, revenue FROM orders WHERE id = 1 AND",
    ],
    ids=[
        "doubled_order_direction",
        "empty_in_list",
        "mysql_style_limit_offset",
        "truncated_predicate",
    ],
)
def test_warehouse_flag_exits_1_on_sql_duckdb_refuses(tmp_path, sql):
    """Every one of these exited 0 with a "check unavailable" warning.

    sqlglot accepts what DuckDB then refuses (and vice versa), so these land on
    the classifier's catch-all codes — which the wrap-synthesis inference read
    as "the check's own statement failed", inverting the flag's whole promise
    on the default adapter.
    """
    board = _project(tmp_path, _BOARD.replace("SELECT id, revenue FROM orders", sql))
    result = _run(tmp_path, str(board), "--warehouse")
    assert result.exit_code == 1, result.output
    assert "ERR-WAREHOUSE-QUERY-INVALID" in result.output


def test_a_broken_query_passes_without_the_flag(tmp_path):
    """The pin that --warehouse is doing the work: stateless validation can't see it."""
    board = _project(
        tmp_path,
        _BOARD.replace("SELECT id, revenue FROM orders", "SELECT nope FROM orders"),
    )
    assert _run(tmp_path, str(board)).exit_code == 0


def test_json_output_carries_warehouse_findings(tmp_path):
    import json

    board = _project(
        tmp_path,
        _BOARD.replace("SELECT id, revenue FROM orders", "SELECT nope FROM orders"),
    )
    result = _run(tmp_path, str(board), "--warehouse", "--json")
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "ERR-WAREHOUSE-QUERY-INVALID" in {e["code"] for e in payload["errors"]}


def test_strict_fails_on_an_unchecked_query(tmp_path):
    """--strict means "everything was actually checked" — unchecked is a failure.

    A values query carries no SQL to dry-run, so it is reported unchecked. Plain
    --warehouse exits 0 (nothing is wrong, it is just unknown); --strict, whose
    contract is warnings-are-errors, exits 1.
    """
    board = _project(tmp_path, _VALUES_BOARD)
    assert _run(tmp_path, str(board), "--warehouse").exit_code == 0
    strict = _run(tmp_path, str(board), "--warehouse", "--strict")
    assert strict.exit_code == 1
    assert "WARN-WAREHOUSE-CHECK-UNAVAILABLE" in strict.output


def test_json_and_strict_together_fail_on_an_unchecked_query(tmp_path):
    """--json must not silently drop --strict's contract.

    The plain-text branch of _emit already honors "warnings are errors under
    --strict" (test_strict_fails_on_an_unchecked_query, above). --json is a
    separate branch that used to compute only has_errors and ignore strict
    entirely, so `--json --strict` exited 0 on a warning-only result — the
    natural CI invocation (parse JSON, gate the build) passing green while
    every query went unchecked.
    """
    board = _project(tmp_path, _VALUES_BOARD)
    without_strict = _run(tmp_path, str(board), "--warehouse", "--json")
    assert without_strict.exit_code == 0

    with_strict = _run(tmp_path, str(board), "--warehouse", "--json", "--strict")
    assert with_strict.exit_code == 1
