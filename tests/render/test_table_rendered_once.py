"""A table's sizing probes and its paint share one engine render per board render."""

from __future__ import annotations

import contextlib
import re
from typing import Any

import pytest

from dbt_charts import _engine
from dbt_charts.core.board import BoardRenderResult, render_dashboard
from dbt_charts.core.compile.models.source import (
    DuckDBSourceConfig,
    ResolvedSourceConfig,
)
from dbt_charts.core.execute.env import ExecEnv
from dbt_charts.core.project import InMemoryBoard
from dbt_charts.core.render import renderer

from .._scripted_env import ScriptedBackend, scripted_env

_EXAMPLES_DB: dict[str, ResolvedSourceConfig] = {
    "examples_db": DuckDBSourceConfig(type="duckdb", path=":memory:")
}

BOARD = """
source: examples_db
queries:
  q: SELECT id, name FROM t
charts:
  t:
    query: q
    type: table
rows:
  - t
"""

PAGED = """
source: examples_db
queries:
  q: SELECT id, name FROM t
charts:
  t:
    query: q
    type: table
    style:
      pagination:
        mode: pages
        page_rows: 5
rows:
  - t
"""


def _render(
    tmp_path: Any,
    local_project: Any,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    board: str = BOARD,
) -> tuple[BoardRenderResult, list[str]]:
    calls: list[str] = []
    real = _engine.render_chart

    def spy(rows_handle: Any, datasets: Any, layout_json: str) -> Any:
        calls.append(layout_json)
        return real(rows_handle, datasets, layout_json)

    monkeypatch.setattr(_engine, "render_chart", spy)
    project = local_project(tmp_path)
    env: ExecEnv = scripted_env(ScriptedBackend(rows), named_sources=_EXAMPLES_DB)
    result = render_dashboard(
        board=InMemoryBoard(board, path=project.path("charts/_t.yml")),
        env=env,
        format="svg",
        project=project,
        result_cache=None,
    )
    return result, calls


def test_a_table_is_drawn_by_the_engine_once(
    tmp_path: Any, local_project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{"id": i, "name": f"row {i}"} for i in range(50)]
    _, calls = _render(tmp_path, local_project, monkeypatch, rows)
    assert len(calls) == 1, len(calls)


def test_a_paint_served_by_the_sizing_render_records_its_warnings(
    tmp_path: Any, local_project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{"id": i, "name": f"row {i}"} for i in range(1001)]
    result, calls = _render(tmp_path, local_project, monkeypatch, rows)
    assert len(calls) == 1, len(calls)
    codes = [w.code for w in result.warnings]
    assert codes.count("WARN-TABLE-SCROLL-ROWS-CAPPED") == 1, codes


def test_a_paged_table_served_by_its_sizing_render_matches_a_fresh_paint(
    tmp_path: Any, local_project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{"id": i, "name": f"row {i}"} for i in range(8)]
    shared, shared_calls = _render(tmp_path, local_project, monkeypatch, rows, PAGED)
    assert len(shared_calls) == 1, len(shared_calls)
    monkeypatch.setattr(renderer, "engine_draws", contextlib.nullcontext)
    fresh, calls = _render(tmp_path, local_project, monkeypatch, rows, PAGED)
    assert len(calls) > 1, len(calls)

    def stamped(svg: Any) -> str:
        return re.sub(r'data-rendered-at="[^"]*"', "", str(svg))

    assert stamped(shared.data) == stamped(fresh.data)
