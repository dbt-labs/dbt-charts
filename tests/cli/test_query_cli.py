"""Tests for the `dct query` CLI command."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

from .._paths import DBT_CHARTS_DIR

runner = CliRunner()

SIMPLE_BOARD = """\
source: db
queries:
  revenue:
    sql: "select 1 as a, 2 as b"
charts:
  c:
    query: revenue
    type: table
rows:
  - c
"""


@pytest.fixture
def board_dir(tmp_path: Path, sources_yaml: str) -> Path:
    (tmp_path / "dbt_charts.yml").write_text(sources_yaml)
    (tmp_path / "test.yaml").write_text(SIMPLE_BOARD)
    return tmp_path


class TestQueryCliJsonOutput:
    def test_json_round_trip(self, board_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                str(board_dir / "test.yaml"),
                "revenue",
                "--project-dir",
                str(board_dir),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert "a" in data["columns"]
        assert "b" in data["columns"]
        assert len(data["data"]) == 1

    def test_json_validates_against_model(self, board_dir: Path) -> None:
        from dbt_charts.agent_api.query import QueryBoardResult

        result = runner.invoke(
            app,
            [
                "query",
                str(board_dir / "test.yaml"),
                "revenue",
                "--project-dir",
                str(board_dir),
                "--json",
            ],
        )
        assert result.exit_code == 0
        parsed = QueryBoardResult.model_validate_json(result.output)
        assert parsed.success is True


class TestQueryCliUnknownName:
    def test_unknown_name_exits_nonzero(self, board_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                str(board_dir / "test.yaml"),
                "missing",
                "--project-dir",
                str(board_dir),
            ],
        )
        assert result.exit_code != 0

    def test_unknown_name_shows_did_you_mean(
        self, board_dir: Path, sources_yaml: str
    ) -> None:
        board_dir2 = board_dir.parent / "board_dir2"
        board_dir2.mkdir()
        (board_dir2 / "dbt_charts.yml").write_text(sources_yaml)
        (board_dir2 / "multi.yaml").write_text(
            "source: db\nqueries:\n  revenue_q:\n    sql: 'select 1'\n"
            "charts:\n  c:\n    query: revenue_q\n    type: table\nrows:\n  - c\n"
        )
        result = runner.invoke(
            app,
            [
                "query",
                str(board_dir2 / "multi.yaml"),
                "revenue",
                "--project-dir",
                str(board_dir2),
            ],
        )
        assert result.exit_code != 0
        assert "Did you mean" in result.output
        assert "revenue_q" in result.output


class TestQueryCliRelativePath:
    def test_relative_path_resolved_against_project_dir(self, board_dir: Path) -> None:
        # Pass `test.yaml` as a relative path; --project-dir should resolve it.
        result = runner.invoke(
            app,
            [
                "query",
                "test.yaml",
                "revenue",
                "--project-dir",
                str(board_dir),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True


class TestQueryCliProjectDirEnvvar:
    def test_envvar_resolves_project_dir(
        self, board_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No --project-dir; DCT_PROJECT_DIR should be picked up by Typer.
        monkeypatch.setenv("DCT_PROJECT_DIR", str(board_dir))
        result = runner.invoke(
            app,
            ["query", "test.yaml", "revenue", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True

    def test_flag_wins_over_envvar(
        self,
        board_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Env points at a directory with no dbt_charts.yml / test.yaml; flag wins.
        wrong = tmp_path / "wrong"
        wrong.mkdir()
        monkeypatch.setenv("DCT_PROJECT_DIR", str(wrong))
        result = runner.invoke(
            app,
            [
                "query",
                "test.yaml",
                "revenue",
                "--project-dir",
                str(board_dir),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True


class TestQueryCliVarFlag:
    def test_var_flows_through(self, board_dir: Path) -> None:
        var_board = board_dir / "vars.yaml"
        var_board.write_text(
            "source: db\nvariables:\n  country:\n    input: text\n    default: US\n"
            "queries:\n  q:\n    sql: \"select '{{ country }}' as country\"\n"
            "charts:\n  c:\n    query: q\n    type: table\nrows:\n  - c\n"
        )
        result = runner.invoke(
            app,
            [
                "query",
                str(var_board),
                "q",
                "--project-dir",
                str(board_dir),
                "--var",
                "country=FR",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"][0]["country"] == "FR"


_FIXTURES = DBT_CHARTS_DIR / "tests" / "fixtures"


class TestQueryOptionVariety:
    """Option value classes on dct query not pinned elsewhere."""

    def test_dialect_duckdb_on_validate(self, tmp_path: Path) -> None:
        """--dialect duckdb is accepted on --validate (offline lint)."""
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
        assert result.exit_code == 0, result.output
        assert "No issues" in result.output

    def test_dialect_bigquery_on_validate(self, tmp_path: Path) -> None:
        """--dialect bigquery is accepted on --validate (offline lint)."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "SELECT id FROM users",
                "--validate",
                "--dialect",
                "bigquery",
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output

    def test_limit_1_on_named_query_execute(self, board_dir: Path) -> None:
        """--limit 1 on named-query execute returns exactly 1 row, truncated=True."""
        from dbt_charts.agent_api.query import QueryBoardResult

        multi_board = board_dir / "multi.yaml"
        multi_board.write_text(
            'source: db\nqueries:\n  rows:\n    sql: "select 1 as n union all select 2 as n union all select 3 as n"\n'
            "charts:\n  c:\n    query: rows\n    type: table\nrows:\n  - c\n"
        )
        result = runner.invoke(
            app,
            [
                "query",
                str(multi_board),
                "rows",
                "--project-dir",
                str(board_dir),
                "--limit",
                "1",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        parsed = QueryBoardResult.model_validate_json(result.output)
        assert parsed.success is True
        assert parsed.row_count == 1
        assert parsed.truncated is True

    def test_limit_1000_on_named_query_execute(self, board_dir: Path) -> None:
        """--limit 1000 (upper documented bound) is accepted on named-query execute."""
        from dbt_charts.agent_api.query import QueryBoardResult

        result = runner.invoke(
            app,
            [
                "query",
                str(board_dir / "test.yaml"),
                "revenue",
                "--project-dir",
                str(board_dir),
                "--limit",
                "1000",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        parsed = QueryBoardResult.model_validate_json(result.output)
        assert parsed.success is True

    def test_two_var_pairs_both_land_in_query_output(self, board_dir: Path) -> None:
        """Repeatable --var with two k=v pairs: both values reach the query output."""
        from dbt_charts.agent_api.query import QueryBoardResult

        two_var_board = board_dir / "two_vars.yaml"
        two_var_board.write_text(
            "source: db\nvariables:\n"
            "  region:\n    input: text\n    default: US\n"
            "  status:\n    input: text\n    default: active\n"
            "queries:\n"
            "  q:\n"
            "    sql: \"select '{{ region }}' as region, '{{ status }}' as status\"\n"
            "charts:\n  c:\n    query: q\n    type: table\nrows:\n  - c\n"
        )
        result = runner.invoke(
            app,
            [
                "query",
                str(two_var_board),
                "q",
                "--project-dir",
                str(board_dir),
                "--var",
                "region=EU",
                "--var",
                "status=pending",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        parsed = QueryBoardResult.model_validate_json(result.output)
        assert parsed.success is True
        assert parsed.data[0]["region"] == "EU"
        assert parsed.data[0]["status"] == "pending"

    def test_show_suppressed_json_on_validate(self, tmp_path: Path) -> None:
        """--show-suppressed with --json emits {diagnostics, suppressed} dict."""
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        sql = (
            "-- dct:ignore fanout_risk\n"
            "SELECT SUM(o.x), SUM(li.y) FROM orders o JOIN li ON o.id = li.oid GROUP BY o.id"
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
                "--json",
                "--project-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert "diagnostics" in data
        assert "suppressed" in data
        assert isinstance(data["diagnostics"], list)
        assert isinstance(data["suppressed"], list)


class TestQueryWideResultRendering:
    """Off a TTY, Rich falls back to an assumed 80-column width and ellipsizes
    every cell to fit -- an agent capturing stdout got single-character column
    names for a 29-column result. Full names/values must always print."""

    def test_many_columns_render_full_names_not_ellipsized(
        self, project_dir: Path
    ) -> None:
        cols = ", ".join(f"1 as long_column_name_number_{i}" for i in range(29))
        result = runner.invoke(
            app,
            ["query", "db", f"select {cols}", "--project-dir", str(project_dir)],
        )
        assert result.exit_code == 0, result.output
        assert "…" not in result.output
        assert "long_column_name_number_0" in result.output
        assert "long_column_name_number_28" in result.output


class TestQueryTruncationNotice:
    """A row-limit clip must never print a bare 'N rows' that looks complete."""

    def test_truncated_raw_sql_result_names_the_limit_flag(
        self, project_dir: Path
    ) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                "select * from range(30) t(x)",
                "--project-dir",
                str(project_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "truncated" in result.output
        assert "--limit" in result.output

    def test_truncated_describe_result_mentions_describe_flag(
        self, project_dir: Path
    ) -> None:
        cols = ", ".join(f"{i} as c{i}" for i in range(29))
        result = runner.invoke(
            app,
            [
                "query",
                "db",
                f"describe select {cols}",
                "--project-dir",
                str(project_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "truncated" in result.output
        assert "--describe" in result.output


class TestQueryCommonErrors:
    """Every error case asserts documented exit codes.

    The query/schema commands use print_json_result (agent_api result models),
    NOT print_structured_errors — so error JSON paths parse via QueryBoardResult
    / ExecuteQueryResult with success=False, not via Diagnostic envelope.
    """

    def test_board_context_nonexistent_file_exits_one(self, board_dir: Path) -> None:
        """.yaml board context pointing to a non-existent file returns JSON failure."""
        from dbt_charts.agent_api.query import QueryBoardResult

        result = runner.invoke(
            app,
            [
                "query",
                str(board_dir / "nonexistent.yaml"),
                "revenue",
                "--project-dir",
                str(board_dir),
                "--json",
            ],
        )
        assert result.exit_code == 1
        parsed = QueryBoardResult.model_validate_json(result.output)
        assert parsed.success is False
        assert len(parsed.errors) > 0

    def test_malformed_board_yaml_exits_one(self, board_dir: Path) -> None:
        """Malformed board YAML exits 1; JSON has success=False and non-empty errors."""
        from dbt_charts.agent_api.query import QueryBoardResult

        malformed = board_dir / "malformed.yaml"
        shutil.copy(_FIXTURES / "malformed-board" / "charts" / "board.yml", malformed)
        result = runner.invoke(
            app,
            [
                "query",
                str(malformed),
                "sales",
                "--project-dir",
                str(board_dir),
                "--json",
            ],
        )
        assert result.exit_code == 1
        parsed = QueryBoardResult.model_validate_json(result.output)
        assert parsed.success is False
        assert len(parsed.errors) > 0

    def test_unknown_source_on_raw_sql_exits_one(self, board_dir: Path) -> None:
        """Unknown source context on raw-SQL execute exits 1; JSON has success=False."""
        from dbt_charts.agent_api.query import ExecuteQueryResult

        result = runner.invoke(
            app,
            [
                "query",
                "nonexistent",
                "SELECT 1",
                "--project-dir",
                str(board_dir),
                "--json",
            ],
        )
        assert result.exit_code == 1
        parsed = ExecuteQueryResult.model_validate_json(result.output)
        assert parsed.success is False
        assert len(parsed.errors) > 0

    def test_var_without_equals_raises_bad_parameter(self, board_dir: Path) -> None:
        """--var key (no '=') must error with a clear key=value message."""
        var_board = board_dir / "v.yaml"
        var_board.write_text(
            "source: db\nvariables:\n  country:\n    input: text\n    default: US\n"
            "queries:\n  q:\n    sql: \"select '{{ country }}' as country\"\n"
            "charts:\n  c:\n    query: q\n    type: table\nrows:\n  - c\n"
        )
        result = runner.invoke(
            app,
            [
                "query",
                str(var_board),
                "q",
                "--project-dir",
                str(board_dir),
                "--var",
                "country",
                "--json",
            ],
        )
        assert result.exit_code != 0
        combined = (result.output or "") + (result.stderr or "")
        assert "key=value" in combined
        assert "'country'" in combined

    def test_limit_negative_on_named_query(self, board_dir: Path) -> None:
        """--limit -1 (undocumented negative) passes through; pinning current behavior."""
        from dbt_charts.agent_api.query import QueryBoardResult

        result = runner.invoke(
            app,
            [
                "query",
                str(board_dir / "test.yaml"),
                "revenue",
                "--project-dir",
                str(board_dir),
                "--limit",
                "-1",
                "--json",
            ],
        )
        # Current behavior: exit 0 with 0 rows (limit capped to MAX or negative yields empty)
        assert result.exit_code == 0
        parsed = QueryBoardResult.model_validate_json(result.output)
        assert parsed.success is True
        assert parsed.row_count == 0

    def test_file_not_found_with_validate_exits_one(self) -> None:
        """--file pointing to a non-existent path with --validate exits 1 (non-JSON output)."""
        result = runner.invoke(
            app,
            ["query", "db", "--file", "/nonexistent/path.sql", "--validate"],
        )
        assert result.exit_code == 1


class TestQueryIgnoresCacheEnv:
    """query.py no longer reads DCT_CACHE_PATH — the verb doesn't consume the cache."""

    def test_query_succeeds_when_dct_cache_path_points_to_missing_file(
        self, monkeypatch: pytest.MonkeyPatch, board_dir: Path
    ) -> None:
        """DCT_CACHE_PATH pointing to a non-existent file must not affect query.

        `dct query` does not consult the query-result cache (only render and serve
        take --cache / DCT_CACHE_PATH), so the env var must not affect it here.
        """
        monkeypatch.setenv("DCT_CACHE_PATH", "/nonexistent/cache.duckdb")
        result = runner.invoke(
            app,
            [
                "query",
                str(board_dir / "test.yaml"),
                "revenue",
                "--project-dir",
                str(board_dir),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output


REF_BOARD = """\
source: wh
queries:
  orders:
    sql: "SELECT * FROM {{ ref('orders') }}"
charts:
  c:
    query: orders
    type: table
rows:
  - c
"""


class TestQueryCliDbtRefOfflineModes:
    """--validate and --describe reach the warehouse-free preview."""

    @pytest.fixture
    def ref_board_dir(self, file_duckdb_project_dir: Path) -> Path:
        (file_duckdb_project_dir / "b.yml").write_text(REF_BOARD)
        return file_duckdb_project_dir

    @pytest.mark.parametrize(
        "modes", [["--validate"], ["--describe"], ["--validate", "--describe"]]
    )
    def test_offline_modes_resolve_ref(
        self, ref_board_dir: Path, modes: list[str]
    ) -> None:
        result = runner.invoke(
            app,
            [
                "query",
                str(ref_board_dir / "b.yml"),
                "orders",
                "--project-dir",
                str(ref_board_dir),
                *modes,
            ],
        )
        assert result.exit_code == 0, result.output
        if "--describe" in modes:
            # `users` also exists in the fixture and describes cleanly, so the
            # exit code alone would not catch a ref resolved to the wrong table.
            assert "user_id" in result.output
