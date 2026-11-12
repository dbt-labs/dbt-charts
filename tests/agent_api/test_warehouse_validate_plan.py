"""`validate_paths`'s warehouse tier on what only the engine's facts and plan carry.

A finding names a query by where it is authored, and a chart's side columns decide whether an
unbound chart binds.
"""

from __future__ import annotations

import pytest

from dbt_charts.agent_api.validate import validate_paths
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.env import build_exec_env

from .test_warehouse_validate import (
    _HEALTHY_BOARD,
    _setup_duckdb_project,
    _validate,
)


class TestWarehouseValidatePlan:
    def test_unbound_chart_with_a_side_column_in_a_three_column_result_is_incomplete(
        self, tmp_path
    ):
        """`sort: by: region` names a column for a side use only; a result of id,
        region, revenue must not bind it as the color channel."""
        board = _HEALTHY_BOARD.replace(
            "SELECT id, revenue FROM orders", "SELECT id, region, revenue FROM orders"
        ).replace("    x: id\n    y: revenue\n", "    sort:\n      by: region\n")
        r = _validate(tmp_path, *_setup_duckdb_project(tmp_path, board_yml=board))
        assert "ERR-CHART-CHANNELS-INCOMPLETE" in {e.code for e in r.errors}

    def test_an_inline_query_is_named_by_where_it_is_authored(self, tmp_path):
        board = (
            "title: Inline\n"
            "source: testdb\n"
            "charts:\n"
            "  my_chart:\n"
            "    type: bar\n"
            "    query: SELECT nonexistent_col FROM orders\n"
            "    x: id\n"
            "    y: revenue\n"
        )
        r = _validate(tmp_path, *_setup_duckdb_project(tmp_path, board_yml=board))
        err = next(e for e in r.errors if e.code == "ERR-WAREHOUSE-QUERY-INVALID")
        assert "'@charts.my_chart.query'" in err.message

    def test_failing_queries_in_tab_panes_are_each_reported_by_where_they_are_authored(
        self, tmp_path
    ):
        board = (
            "title: Tabbed\n"
            "source: testdb\n"
            "tabs:\n"
            "  items:\n"
            "    - title: One\n"
            "      rows:\n"
            "        - type: bar\n"
            "          query: SELECT first_missing FROM orders\n"
            "          x: id\n"
            "          y: revenue\n"
            "    - title: Two\n"
            "      cols:\n"
            "        - type: bar\n"
            "          query: SELECT second_missing FROM orders\n"
            "          x: id\n"
            "          y: revenue\n"
        )
        r = _validate(tmp_path, *_setup_duckdb_project(tmp_path, board_yml=board))
        invalid = [
            e.message for e in r.errors if e.code == "ERR-WAREHOUSE-QUERY-INVALID"
        ]
        assert len(invalid) == 2
        assert any("'@charts.@tabs.items[0].rows[0].query'" in m for m in invalid)
        assert any("'@charts.@tabs.items[1].cols[0].query'" in m for m in invalid)

    @pytest.mark.parametrize(
        "extra",
        [
            "    support_table:\n      - region\n",
            "    style:\n      marks:\n        bar:\n          labels:\n"
            "            field: region\n",
        ],
        ids=["support_table", "labels"],
    )
    def test_a_column_named_for_a_side_use_does_not_fill_the_color_channel(
        self, tmp_path, extra
    ):
        board = _HEALTHY_BOARD.replace(
            "SELECT id, revenue FROM orders", "SELECT id, region, revenue FROM orders"
        ).replace("    x: id\n    y: revenue\n", extra)
        r = _validate(tmp_path, *_setup_duckdb_project(tmp_path, board_yml=board))
        assert "ERR-CHART-CHANNELS-INCOMPLETE" in {e.code for e in r.errors}

    def test_a_side_column_matches_the_result_ignoring_case(self, tmp_path):
        board = _HEALTHY_BOARD.replace(
            "SELECT id, revenue FROM orders", "SELECT id, region, revenue FROM orders"
        ).replace("    x: id\n    y: revenue\n", "    sort:\n      by: REGION\n")
        r = _validate(tmp_path, *_setup_duckdb_project(tmp_path, board_yml=board))
        assert "ERR-CHART-CHANNELS-INCOMPLETE" in {e.code for e in r.errors}

    def test_a_named_markdown_board_outside_charts_is_checked_against_the_warehouse(
        self, tmp_path
    ):
        project, registry = _setup_duckdb_project(tmp_path)
        (tmp_path / "docs").mkdir()
        note = tmp_path / "docs" / "note.md"
        note.write_text(
            "Prose.\n\n```charts\nc:\n  type: table\n  query:\n    source: testdb\n"
            "    sql: SELECT nonexistent_col FROM orders\n```\n"
        )
        (result,) = validate_paths([note], project=project, env=registry)
        codes = {e.code for e in result.errors}
        assert "ERR-WAREHOUSE-QUERY-INVALID" in codes
        assert "ERR-MARKDOWN-NOT-BOARD" not in codes

    def test_a_plan_the_engine_refuses_is_reported_not_raised(
        self, tmp_path, monkeypatch
    ):
        from dbt_charts.core.diagnostics.codes_render import ERR_ENGINE_REFUSED
        from dbt_charts.core.diagnostics.execution import ExecutionError

        def refuse(*args, **kwargs):
            raise ExecutionError.from_code(ERR_ENGINE_REFUSED, message="not planned")

        monkeypatch.setattr("dbt_charts.agent_api.validate.plan_board", refuse)
        r = _validate(tmp_path, *_setup_duckdb_project(tmp_path))
        assert r.success is False
        assert [e.code for e in r.errors] == ["ERR-ENGINE-REFUSED"]

    def test_a_markdown_board_the_engine_refuses_is_reported_not_raised(self, tmp_path):
        project, registry = _setup_duckdb_project(tmp_path)
        (tmp_path / "docs").mkdir()
        note = tmp_path / "docs" / "note.md"
        note.write_text(
            "Prose.\n\n```charts\nc:\n  type: table\n  query:\n"
            "    source: undeclared\n    sql: SELECT 1\n```\n"
        )
        (result,) = validate_paths([note], project=project, env=registry)
        assert result.success is False
        assert [e.code for e in result.errors] == ["ERR-SOURCE-NOT-FOUND"]

    def test_a_source_whose_credentials_are_rendered_by_the_env_is_resolved(
        self, tmp_path, monkeypatch
    ):
        """The plan is compiled against the env's rendered sources, so a `password:`
        written as `env_var(...)` resolves instead of failing as a missing field."""
        monkeypatch.setenv("DCT_PLAN_TEST_PASSWORD", "secret")
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  pg:\n"
            "    type: postgres\n"
            "    host: 127.0.0.1\n"
            "    port: 1\n"
            "    dbname: wh\n"
            "    user: reader\n"
            "    password: \"{{ env_var('DCT_PLAN_TEST_PASSWORD') }}\"\n"
        )
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "board.yml").write_text(
            "title: T\nsource: pg\nqueries:\n  q: SELECT 1 AS a\n"
            "charts:\n  c:\n    type: table\n    query: q\n"
        )
        project = FilesystemProject(tmp_path)
        (result,) = validate_paths(
            [tmp_path / "charts" / "board.yml"],
            project=project,
            env=build_exec_env(project),
        )
        assert result.success is True, [e.message for e in result.errors]
        assert {w.code for w in result.warnings} == {"WARN-WAREHOUSE-CHECK-UNAVAILABLE"}
