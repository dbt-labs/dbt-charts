"""CLI tests for the expanded dct query dispatch (D10).

Covers every cell of the dispatch matrix:
  board-query execute, board-query --validate, raw-SQL execute,
  raw-SQL --validate, --file variants,
  and every error case pinned in the Dispatch rule section of the task worksheet.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

runner = CliRunner()

SIMPLE_BOARD = """\
source: db
queries:
  revenue:
    sql: "SELECT 1 AS amount"
charts:
  c:
    query: revenue
    type: table
rows:
  - c
"""


@pytest.fixture
def project_dir(tmp_path: Path, sources_yaml: str) -> Path:
    (tmp_path / "dbt_charts.yml").write_text(sources_yaml)
    (tmp_path / "test.yaml").write_text(SIMPLE_BOARD)
    return tmp_path


# ---------------------------------------------------------------------------
# Board-query execute
# ---------------------------------------------------------------------------


class TestNamedQueryExecute:
    def test_named_query_executes(self, project_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                str(project_dir / "test.yaml"),
                "revenue",
                "--project-dir",
                str(project_dir),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert "amount" in data["columns"]

    def test_named_query_var_flag(self, project_dir: Path) -> None:
        var_board = project_dir / "var.yaml"
        var_board.write_text(
            "source: db\nvariables:\n  x:\n    input: text\n    default: A\n"
            "queries:\n  q:\n    sql: \"SELECT '{{ x }}' AS val\"\n"
            "charts:\n  c:\n    query: q\n    type: table\nrows:\n  - c\n"
        )
        result = runner.invoke(
            app,
            [
                "query",
                str(var_board),
                "q",
                "--project-dir",
                str(project_dir),
                "--var",
                "x=Z",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["data"][0]["val"] == "Z"


# ---------------------------------------------------------------------------
# Board-query --validate
# ---------------------------------------------------------------------------


class TestNamedQueryValidate:
    def test_named_query_validate_passes_clean_sql(self, project_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                str(project_dir / "test.yaml"),
                "revenue",
                "--project-dir",
                str(project_dir),
                "--validate",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "No issues" in result.output

    def test_named_query_validate_json_output(self, project_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                str(project_dir / "test.yaml"),
                "revenue",
                "--project-dir",
                str(project_dir),
                "--validate",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_named_query_validate_error_exits_one(self, project_dir: Path) -> None:
        bad_board = project_dir / "bad.yaml"
        bad_board.write_text(
            "queries:\n  q:\n    sql: 'SELECT * FROM a, b'\n"
            "charts:\n  c:\n    query: q\n    type: table\nrows:\n  - c\n"
        )
        result = runner.invoke(
            app,
            [
                "query",
                str(bad_board),
                "q",
                "--project-dir",
                str(project_dir),
                "--validate",
            ],
        )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Raw-SQL execute (new capability — parity with MCP execute_query)
# ---------------------------------------------------------------------------


class TestRawSqlExecute:
    def test_raw_sql_execute_with_source(self, project_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "SELECT 42 AS n",
                "--project-dir",
                str(project_dir),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["columns"] == ["n"]
        assert data["data"][0]["n"] == 42

    def test_raw_sql_execute_requires_source(self, project_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                "SELECT 1",
                "SELECT 1",
                "--project-dir",
                str(project_dir),
                "--json",
            ],
        )
        assert result.exit_code == 1
        assert "unknown source" in result.output.lower()

    def test_raw_sql_execute_var_flag(self, project_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "SELECT '{{ region }}' AS r",
                "--var",
                "region=EU",
                "--project-dir",
                str(project_dir),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["data"][0]["r"] == "EU"


# ---------------------------------------------------------------------------
# Raw-SQL --validate (formerly dct validate-query)
# ---------------------------------------------------------------------------


class TestRawSqlValidate:
    def test_safe_sql_exits_zero(self, project_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "SELECT id FROM users",
                "--validate",
                "--project-dir",
                str(project_dir),
            ],
        )
        assert result.exit_code == 0
        assert "No issues" in result.output

    def test_error_sql_exits_one(self, project_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "SELECT * FROM a, b",
                "--validate",
                "--project-dir",
                str(project_dir),
            ],
        )
        assert result.exit_code == 1
        assert "WARN-MISSING-JOIN-PREDICATE" in result.output

    def test_validate_json_output(self, project_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "SELECT * FROM a, b",
                "--validate",
                "--json",
                "--project-dir",
                str(project_dir),
            ],
        )
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert any(d["code"] == "WARN-MISSING-JOIN-PREDICATE" for d in parsed)

    def test_validate_show_suppressed_flag(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        sql = (
            "-- dct:ignore WARN-REAGGREGATION\nSELECT AVG(t) FROM "
            "(SELECT region, SUM(x) AS t FROM orders GROUP BY region) s"
        )
        sql_file = tmp_path / "q.sql"
        sql_file.write_text(sql)
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "--file",
                str(sql_file),
                "--validate",
                "--show-suppressed",
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert "suppressed" in result.stdout.lower()

    def test_validate_dialect_flag(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "SELECT id FROM users",
                "--validate",
                "--dialect",
                "duckdb",
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert "No issues" in result.output


# ---------------------------------------------------------------------------
# --file flag
# ---------------------------------------------------------------------------


class TestFileFlag:
    def test_file_validate(self, tmp_path: Path) -> None:
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        sql_file = tmp_path / "q.sql"
        sql_file.write_text("SELECT id FROM users")
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "--file",
                str(sql_file),
                "--validate",
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0

    def test_file_execute(self, tmp_path: Path, project_dir: Path) -> None:
        sql_file = tmp_path / "q.sql"
        sql_file.write_text("SELECT 99 AS z")
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "--file",
                str(sql_file),
                "--project-dir",
                str(project_dir),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["data"][0]["z"] == 99


# ---------------------------------------------------------------------------
# Error cases (from Dispatch rule)
# ---------------------------------------------------------------------------


class TestDispatchErrors:
    def test_sql_operand_and_file_conflict_errors(self, project_dir: Path) -> None:
        sql_file = project_dir / "q.sql"
        sql_file.write_text("SELECT 1")
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "SELECT 1",
                "--file",
                str(sql_file),
                "--project-dir",
                str(project_dir),
            ],
        )
        assert result.exit_code == 1
        assert "SQL" in result.output or "--file" in result.output

    def test_no_input_exits_one(self, project_dir: Path) -> None:
        result = runner.invoke(
            app, ["query", "--validate", "--project-dir", str(project_dir)]
        )
        assert result.exit_code == 1

    def test_source_without_sql_or_file_exits_one(self, project_dir: Path) -> None:
        result = runner.invoke(app, ["query", "db", "--project-dir", str(project_dir)])
        assert result.exit_code == 1
        assert "SQL" in result.output or "--file" in result.output

    def test_file_not_found_exits_one(self, project_dir: Path) -> None:
        result = runner.invoke(
            app,
            ["query", "db", "--file", "/nonexistent/path.sql", "--validate"],
        )
        assert result.exit_code == 1

    def test_unknown_query_name_hint_uses_user_input(self, project_dir: Path) -> None:
        # "rev" is close to "revenue" — hint should suggest the correct name.
        result = runner.invoke(
            app,
            [
                "query",
                str(project_dir / "test.yaml"),
                "rev",
                "--validate",
                "--project-dir",
                str(project_dir),
            ],
        )
        assert result.exit_code == 1
        assert "revenue" in result.output  # "Did you mean: revenue?"

    def test_sql_shaped_input_with_board_suggests_source_context(
        self, project_dir: Path
    ) -> None:
        # SQL-shaped input passed as board query name should point away from board mode.
        result = runner.invoke(
            app,
            [
                "query",
                str(project_dir / "test.yaml"),
                "SELECT 1",
                "--validate",
                "--project-dir",
                str(project_dir),
            ],
        )
        assert result.exit_code == 1
        assert "source" in result.output.lower() or "SQL" in result.output

    def test_source_without_sql_json_exits_one(self, project_dir: Path) -> None:
        result = runner.invoke(
            app,
            ["query", "revenue", "--json", "--project-dir", str(project_dir)],
        )
        assert result.exit_code == 1
        assert "SQL" in result.output or "--file" in result.output

    def test_board_execute_with_sql_shaped_reference_exits_one(
        self, project_dir: Path
    ) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                str(project_dir / "test.yaml"),
                "SELECT 1",
                "--project-dir",
                str(project_dir),
            ],
        )
        assert result.exit_code == 1
        assert "source" in result.output.lower() or "SQL" in result.output

    # ------------------------------------------------------------------
    # JSON envelope shape: sibling tests for hint-text tests above.
    # query/schema use print_json_result (agent_api result models with
    # success=False), NOT the Diagnostic envelope from _error_format.
    # ------------------------------------------------------------------

    def test_sql_operand_and_file_conflict_json_exits_one(
        self, project_dir: Path
    ) -> None:
        """SQL operand + --file conflict with --json exits 1 as a plain error."""
        sql_file = project_dir / "q.sql"
        sql_file.write_text("SELECT 1")
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "SELECT 1",
                "--file",
                str(sql_file),
                "--json",
                "--project-dir",
                str(project_dir),
            ],
        )
        assert result.exit_code == 1
        # This error fires before JSON output is set up — it is a plain stderr msg
        assert "SQL" in result.output or "--file" in result.output

    def test_no_input_json_exits_one(self, project_dir: Path) -> None:
        """No context with --validate --json exits 1 (non-JSON plain error)."""
        result = runner.invoke(
            app,
            ["query", "--validate", "--json", "--project-dir", str(project_dir)],
        )
        assert result.exit_code == 1

    def test_unknown_query_name_json_envelope(self, project_dir: Path) -> None:
        """Unknown query name with --json returns QueryBoardResult with success=False."""
        from dbt_charts.agent_api.query import QueryBoardResult

        result = runner.invoke(
            app,
            [
                "query",
                str(project_dir / "test.yaml"),
                "rev",
                "--project-dir",
                str(project_dir),
                "--json",
            ],
        )
        assert result.exit_code == 1
        parsed = QueryBoardResult.model_validate_json(result.output)
        assert parsed.success is False
        assert len(parsed.errors) > 0

    def test_file_validate_nonexistent_exits_one(self, project_dir: Path) -> None:
        """--file + --validate against a non-existent path exits 1."""
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "--file",
                "/nonexistent/q.sql",
                "--validate",
                "--project-dir",
                str(project_dir),
            ],
        )
        assert result.exit_code == 1

    def test_file_empty_with_validate_json(self, project_dir: Path) -> None:
        """Empty --file with --validate --json exits 1 with a WARN-PARSE-ERROR diagnostic."""
        empty_file = project_dir / "empty.sql"
        empty_file.write_text("")
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "--file",
                str(empty_file),
                "--validate",
                "--json",
                "--project-dir",
                str(project_dir),
            ],
        )
        # Current behavior: empty SQL produces a WARN-PARSE-ERROR, exits 1
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert any(d["code"] == "WARN-PARSE-ERROR" for d in data)
        # --validate --json serializes exclude_none like every other query verb:
        # unpopulated optional fields are absent, never null.
        for d in data:
            assert None not in d.values(), d


# ---------------------------------------------------------------------------
# Help text — recipe block must be present. The multiline `help=` on the
# `query` decorator is the single source of truth for `dct query --help`
# and the Data & SQL panel row in `dct --help`.
# ---------------------------------------------------------------------------


class TestHelpText:
    def test_help_shows_recipe(self) -> None:
        import re

        result = runner.invoke(app, ["query", "--help"])
        assert result.exit_code == 0
        text = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "common forms" in text
        # Each recipe form must appear on its own line — without the Click
        # `\b` no-rewrap marker, Typer's rich rendering glues the four forms
        # onto wrapped prose lines.
        lines = [line.strip() for line in text.splitlines()]
        for form in (
            "dct query SOURCE 'SQL'",
            "dct query BOARD.yml REFERENCE",
            "dct query SOURCE 'SQL' --validate",
        ):
            assert form in lines, (
                f"Recipe form {form!r} not on its own line. Full output:\n{text}"
            )


def test_cli_query_command_execute_path_uses_project_session_with_block(
    tmp_path: Path,
) -> None:
    """dct query SOURCE SQL (execute path) constructs ProjectSession.from_project and routes
    through project_session.execute_query — never calls agent_api functions directly."""
    (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
    fake_project = MagicMock(name="ProjectSession")
    fake_project.execute_query.return_value = MagicMock(
        success=True,
        columns=["n"],
        data=[{"n": 42}],
        row_count=1,
        truncated=False,
    )

    @contextmanager
    def fake_from_project(project, **_kwargs):
        yield fake_project

    with patch(
        "dbt_charts.agent_api.ProjectSession.from_project",
        side_effect=fake_from_project,
    ) as from_project_mock:
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "SELECT 42 AS n",
                "--project-dir",
                str(tmp_path),
                "--json",
            ],
        )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert from_project_mock.call_count == 1, (
        "query_command execute path must construct exactly one ProjectSession.from_project(...)"
    )
    # query verbs do not consume the cache; ProjectSession.from_project must be called without it.
    assert "cache" not in from_project_mock.call_args.kwargs
    fake_project.execute_query.assert_called_once()


def test_raw_sql_validate_uses_project_session_open(tmp_path: Path) -> None:
    """dct query SOURCE SQL --validate routes through ProjectSession.from_project →
    project_session.validate_query.

    Raw-SQL validate-only previously short-circuited before ProjectSession construction;
    now it must build one exactly once so relationship context is threaded into core.
    """
    (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
    fake_project = MagicMock(name="ProjectSession")
    fake_project.validate_query.return_value = []

    @contextmanager
    def fake_from_project(project, **_kwargs):
        yield fake_project

    with patch(
        "dbt_charts.agent_api.ProjectSession.from_project",
        side_effect=fake_from_project,
    ) as from_project_mock:
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "SELECT id FROM users",
                "--validate",
                "--project-dir",
                str(tmp_path),
            ],
        )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert from_project_mock.call_count == 1, (
        "raw-SQL --validate must construct exactly one ProjectSession.from_project(...)"
    )
    fake_project.validate_query.assert_called_once()


class TestBoardContextExtensions:
    """Board-query mode must accept every suffix the project calls a board.

    Regression: `_is_board_context` hardcoded `.yaml`, so a `.yml` board path
    fell through to RAW-SQL mode and the query reference was validated as if it
    were SQL text. That fails silently — `--validate` prints "No issues found."
    for a reference that does not exist — which is worse than an error. Every
    other test in this file uses `.yaml`, the one suffix that worked, which is
    why it went unnoticed; `.yml` is what every board in `examples/` uses.

    The discriminator is a bogus query reference: board mode knows the board's
    query names and rejects it, raw-SQL mode lints it as SQL and passes.
    """

    @pytest.mark.parametrize("suffix", [".yml", ".yaml", ".md", ".markdown"])
    def test_board_suffixes_dispatch_to_board_mode(
        self, tmp_path: Path, sources_yaml: str, suffix: str
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text(sources_yaml)
        board = tmp_path / f"board{suffix}"
        if suffix in (".md", ".markdown"):
            # A markdown board carries the board under a `board:` frontmatter
            # key; the bare YAML body is not one.
            body = "\n".join(
                "  " + ln if ln else ln for ln in SIMPLE_BOARD.splitlines()
            )
            board.write_text(f"---\nboard:\n{body}\n---\n\n# Board\n")
        else:
            board.write_text(SIMPLE_BOARD)

        result = runner.invoke(
            app,
            [
                "query",
                str(board),
                "nosuchquery",
                "--project-dir",
                str(tmp_path),
                "--validate",
            ],
        )
        assert "ERR-BOARD-QUERY-NOT-FOUND" in result.output, (
            f"{suffix} did not dispatch to board mode: {result.output}"
        )
        assert "revenue" in result.output, result.output
