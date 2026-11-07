"""A query result is encoded and parsed by the engine once per board render,
however many sizing probes and paints read it."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts import _engine
from dbt_charts.core.board import render_dashboard
from dbt_charts.core.execute.env import ExecEnv
from dbt_charts.core.project import InMemoryBoard

from .._scripted_env import ScriptedBackend, scripted_env

ROWS = [{"id": i, "name": f"row {i}", "amount": i * 1.5} for i in range(50)]

BOARD = """
source: examples_db
queries:
  q: SELECT id, name, amount FROM t
charts:
  t:
    query: q
    type: table
    title: "Rows"
rows:
  - t
"""


def _registry(rows: list[dict[str, Any]]) -> ExecEnv:
    return scripted_env(ScriptedBackend(rows))


SPARK_BAR_BOARD = """
source: examples_db
queries:
  q: SELECT id, name, amount FROM t
charts:
  s:
    query: q
    type: spark_bar
    x: amount
    y: name
rows:
  - s
"""


@pytest.mark.parametrize(
    ("board", "entry_points"),
    [
        (BOARD, ("render_chart", "measure_table_natural_width")),
        (SPARK_BAR_BOARD, ("render_chart",)),
    ],
    ids=["table", "spark_bar"],
)
def test_a_render_loads_its_rows_into_the_engine_once(
    tmp_path,
    local_project,
    monkeypatch: pytest.MonkeyPatch,
    board: str,
    entry_points: tuple[str, ...],
) -> None:
    loads: list[int] = []
    calls: list[str] = []
    real_load = _engine.load_rows

    def counting_load(rows_json: str) -> Any:
        loads.append(len(rows_json))
        return real_load(rows_json)

    monkeypatch.setattr(_engine, "load_rows", counting_load)
    for name in entry_points:
        real = getattr(_engine, name)

        def spy(*args: Any, _real: Any = real, _name: str = name) -> Any:
            calls.append(_name)
            return _real(*args)

        monkeypatch.setattr(_engine, name, spy)
    project = local_project(tmp_path)
    render_dashboard(
        board=InMemoryBoard(board, path=project.path("charts/_t.yml")),
        env=_registry(ROWS),
        format="svg",
        project=project,
        result_cache=None,
    )
    assert len(calls) > 1, calls
    assert len(loads) == 1, loads
