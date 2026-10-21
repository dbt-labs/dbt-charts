"""WARN-RESTATED-INFERRED: an ``options.static`` list that is every value of the filtered column."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Any

import duckdb
import pytest

from dbt_charts.agent_api.cache import open_cache
from dbt_charts.agent_api.project_session import ProjectSession
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.diagnostics import Diagnostic

_BOARD = """
source: cars
variables:
  first_year:
    options:
      static: {static}
    input: select
queries:
  q:
    sql: |
      SELECT model_year, COUNT(*) AS n FROM cars
      WHERE {where}
      GROUP BY 1
charts:
  c:
    type: bar
    query: q
    x: model_year
    y: n
rows:
  - c
"""

_FILTER = "{{ filter('model_year', first_year, '>=') }}"


def _warnings(tmp_path: Path, static: str, where: str = _FILTER) -> list[Diagnostic]:
    reset_config()
    with duckdb.connect(str(tmp_path / "cars.duckdb")) as con:
        con.execute(
            "CREATE TABLE cars AS SELECT * FROM (VALUES (1970), (1971), (1972)) t(model_year)"
        )
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  cars:\n    type: duckdb\n    path: cars.duckdb\n", encoding="utf-8"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts/board.yml").write_text(
        dedent(_BOARD.format(static=static, where=where)), encoding="utf-8"
    )
    cache = open_cache(None)
    with ProjectSession.open(str(tmp_path), cache=cache) as session:
        result = session.render_board(
            session.project.path("charts/board.yml").read_board(),
            format="svg",
            use_cache=False,
            restated_check=True,
        )
    cache.close()
    assert result.status != "error", (result.validation_errors, result.board_error)
    return [w for w in result.warnings if w.code == "WARN-RESTATED-INFERRED"]


def test_static_list_of_every_value_warns(tmp_path: Path) -> None:
    (warning,) = _warnings(tmp_path, "[1970, 1971, 1972]")
    assert warning.path == "variables.first_year.options.static"
    assert warning.message == (
        "`variables.first_year.options.static: [1970, 1971, 1972]` is already "
        "every value of `cars.model_year`, as `column: cars.model_year` lists "
        "them; remove it."
    )
    assert warning.fix == "Replace `options.static` with `column: cars.model_year`."


@pytest.mark.parametrize(
    ("static", "where"),
    [
        pytest.param("[1970, 1971]", _FILTER, id="a subset of the values"),
        pytest.param("[1970, 1971, 1972, 1973]", _FILTER, id="a superset"),
        pytest.param("[1972, 1971, 1970]", _FILTER, id="a curated order"),
        pytest.param(
            "[1970, 1971, 1972]",
            "model_year >= 1970 AND {{ first_year }} IS NOT NULL",
            id="no filter() use: column unknown",
        ),
    ],
)
def test_other_lists_do_not_warn(tmp_path: Path, static: str, where: str) -> None:
    assert _warnings(tmp_path, static, where) == []


def test_a_failing_probe_skips_the_advice_and_keeps_the_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dbt_charts.core.diagnostics.execution import ExecutionError
    from dbt_charts.core.execute.executor import Executor

    original = Executor.execute_query

    def failing(self: Executor, query_name: str, *args: Any, **kwargs: Any) -> Any:
        if query_name.startswith("_restated_distinct_"):
            raise ExecutionError("probe failed")
        return original(self, query_name, *args, **kwargs)

    monkeypatch.setattr(Executor, "execute_query", failing)

    assert _warnings(tmp_path, "[1970, 1971, 1972]") == []
