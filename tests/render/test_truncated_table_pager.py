"""A table drawn from a result the row limit cut says so on the page."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.render import render

from .._run import Run
from .._scripted_env import ScriptedBackend, scripted_env

BOARD = """\
title: Probe
charts:
  c:
    query: q
    type: table
    style:
      pagination:
        mode: pages
        page_rows: 2
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - c
"""


def _svg(monkeypatch: pytest.MonkeyPatch, row_ceiling: int) -> str:
    monkeypatch.setenv("DCT_MAX_ROWS_CEILING", str(row_ceiling))
    compiled = compile(BOARD)
    assert compiled.success and compiled.board is not None, compiled.errors
    env = scripted_env(
        ScriptedBackend(
            [
                {"x": i, "label": f"a fairly long label {i}", "note": "n" * 40}
                for i in range(6)
            ]
        )
    )
    run = Run(compiled.board, env=env)
    return str(render(compiled.board, run.ctx, run.ledger, format="svg").output)


def test_a_table_cut_by_the_row_limit_says_so_in_its_pager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svg = _svg(monkeypatch, 5)
    assert "1–2 / 5+ · truncated by row limit" in svg


def test_an_uncut_table_reads_its_exact_total(monkeypatch: pytest.MonkeyPatch) -> None:
    svg = _svg(monkeypatch, 100)
    assert "1–2 / 6<" in svg
    assert "+ ·" not in svg
