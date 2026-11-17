"""`?_window=<query>` on a board URL returns a slice of that query's rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache
from dbt_charts.core.serve.server import create_server

_BOARD_YAML = """\
title: People
variables:
  team:
    input: select
    options:
      static: [red, blue]
    default: red
queries:
  people:
    columns: [id, name, team]
    values:
      - [1, alice, red]
      - [2, bob, blue]
      - [3, carol, red]
      - [4, alan, red]
  by_team:
    source: db
    sql: |
      SELECT * FROM (VALUES (1, 'alice', 'red'), (2, 'bob', 'blue'),
        (3, 'carol', 'red'), (4, 'alan', 'red')) AS t (id, name, team)
      WHERE team = {{ team }}
charts:
  roster:
    query: by_team
    type: table
  everyone:
    query: people
    type: table
"""


def _client(tmp_path: Path) -> TestClient:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "people.yml").write_text(_BOARD_YAML)
    (tmp_path / "dbt_charts.yml").write_text("sources:\n  db:\n    type: duckdb\n")
    project = FilesystemProject(tmp_path)
    return TestClient(create_server(project, cache_path=tmp_path / "cache.duckdb"))


def test_a_window_searches_sorts_and_pages_a_query(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get(
            "/people",
            params=[
                ("_window", "people"),
                ("_search", "AL"),
                ("_search_in", "name"),
                ("_order", "-id"),
                ("_limit", "1"),
            ],
        )

    assert response.status_code == 200
    assert response.json() == {
        "columns": ["id", "name", "team"],
        "rows": [[4, "alan", "red"]],
        "total": 2,
        "more": True,
        "truncated": False,
    }


def test_a_window_runs_under_the_board_variables(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        default = client.get("/people", params={"_window": "by_team"})
        blue = client.get("/people", params={"_window": "by_team", "team": "blue"})

    assert [row[0] for row in default.json()["rows"]] == [1, 3, 4]
    assert [row[0] for row in blue.json()["rows"]] == [2]


def test_equals_filters_by_exact_values(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get(
            "/people",
            params=[("_window", "people"), ("_equals.id", "2"), ("_equals.id", "3")],
        )

    assert [row[0] for row in response.json()["rows"]] == [2, 3]


def test_a_window_over_a_truncated_result_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "2")
    with _client(tmp_path) as client:
        fresh = client.get("/people", params={"_window": "by_team"}).json()
        cached = client.get("/people", params={"_window": "by_team"}).json()

    assert (fresh["total"], fresh["truncated"]) == (2, True)
    assert (cached["total"], cached["truncated"]) == (2, True)


def test_a_bad_window_is_a_400_naming_the_problem(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        unknown_query = client.get("/people", params={"_window": "nope"})
        unknown_column = client.get(
            "/people", params={"_window": "people", "_order": "nope"}
        )
        bad_limit = client.get(
            "/people", params={"_window": "people", "_limit": "many"}
        )

    assert unknown_query.status_code == 400
    assert "nope" in unknown_query.json()["error"]
    assert unknown_column.status_code == 400
    assert "nope" in unknown_column.json()["error"]
    assert bad_limit.status_code == 400
    assert "_limit" in bad_limit.json()["error"]


def test_a_window_reads_the_rows_the_board_render_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes: list[str] = []
    real_put = TrivialDuckDBCache.put

    def counting_put(self: TrivialDuckDBCache, *args: Any, **kwargs: Any) -> None:
        writes.append(kwargs["query_name"])
        real_put(self, *args, **kwargs)

    monkeypatch.setattr(TrivialDuckDBCache, "put", counting_put)
    with _client(tmp_path) as client:
        assert client.get("/people").status_code == 200
        rendered = len(writes)
        window = client.get("/people", params={"_window": "by_team"})

    assert rendered > 0
    assert window.status_code == 200
    assert len(writes) == rendered


def test_a_window_on_a_missing_board_is_a_404(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/nobody", params={"_window": "people"})

    assert response.status_code == 404


def test_a_board_that_no_longer_compiles_is_a_json_error(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        (tmp_path / "charts" / "people.yml").write_text("title: [broken\n")
        response = client.get("/people", params={"_window": "people"})

    assert response.status_code == 422
    assert response.json()["error"]


def test_a_board_the_engine_refuses_is_a_json_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dbt_charts.core.board as board

    real_plan = board.plan_for_board

    def plan_after_the_board_breaks(*args: Any, **kwargs: Any) -> Any:
        (tmp_path / "charts" / "people.yml").write_text("title: [broken\n")
        return real_plan(*args, **kwargs)

    monkeypatch.setattr(board, "plan_for_board", plan_after_the_board_breaks)
    with _client(tmp_path) as client:
        response = client.get("/people", params={"_window": "people"})

    assert response.status_code == 422
    assert response.json()["error"]
