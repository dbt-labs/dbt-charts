"""A render runs Python's compile and the engine's compile and plan at once."""

import threading
import time
from pathlib import Path

import pytest

from dbt_charts import _engine
from dbt_charts.agent_api.project_session import ProjectSession
from dbt_charts.core import board as board_module

SLEEP = 1.0

BOARD = """
queries:
  q:
    columns: [n]
    values:
      - [1]
charts:
  c:
    query: q
    type: kpi
    value: n
rows:
  - c
"""


@pytest.fixture
def intervals(monkeypatch: pytest.MonkeyPatch) -> dict[str, tuple[float, float]]:
    """Each compile stubbed to take SLEEP seconds on top of its real work; records when it
    ran, and on which thread."""
    spans: dict[str, tuple[float, float]] = {}
    threads: dict[str, int] = {}

    def slowed(name: str, real):  # type: ignore[no-untyped-def] — a test wrapper over either compile
        def run(*args, **kwargs):  # type: ignore[no-untyped-def] — forwards the wrapped call
            start = time.perf_counter()
            threads[name] = threading.get_ident()
            time.sleep(SLEEP)
            try:
                return real(*args, **kwargs)
            finally:
                spans[name] = (start, time.perf_counter())

        return run

    monkeypatch.setattr(
        board_module, "compile_file", slowed("python", board_module.compile_file)
    )
    monkeypatch.setattr(
        board_module, "engine_compile", slowed("engine", board_module.engine_compile)
    )
    spans["threads"] = (0.0, 0.0)
    yield spans
    assert threads["python"] != threads["engine"]


def render(tmp_path: Path) -> float:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "b.yml").write_text(BOARD)
    session = ProjectSession.open(tmp_path)
    board = session.project.path("charts/b.yml").read_board(named=True)
    # The first render of a process pays imports and fonts, which are not what is timed.
    session.render_board(board, format="svg", use_cache=False)
    start = time.perf_counter()
    result = session.render_board(board, format="svg", use_cache=False)
    elapsed = time.perf_counter() - start
    assert result.status == "ok", result.board_error
    return elapsed


def test_a_render_takes_the_slower_compile_not_both(
    tmp_path: Path, intervals: dict[str, tuple[float, float]]
) -> None:
    elapsed = render(tmp_path)

    assert elapsed < 1.5 * SLEEP, f"{elapsed:.2f}s for two {SLEEP}s compiles"


def test_the_two_compiles_overlap_in_time(
    tmp_path: Path, intervals: dict[str, tuple[float, float]]
) -> None:
    render(tmp_path)

    python, engine = intervals["python"], intervals["engine"]
    assert python[0] < engine[1] and engine[0] < python[1]


def test_a_pathless_board_plans_without_the_projects_meta_yml(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from dbt_charts.core.board import plan_for_board
    from dbt_charts.core.project import InMemoryBoard

    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "meta.yml").write_text("source: from_meta\n")
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  from_meta:\n    type: duckdb\n    path: ':memory:'\n"
    )
    text = "queries:\n  q:\n    sql: SELECT 1 AS n\ncharts:\n  c:\n    query: q\n    type: kpi\n    value: n\nrows:\n  - c\n"
    (tmp_path / "charts" / "located.yml").write_text(text)
    session = ProjectSession.open(tmp_path)

    def source_of(board: object) -> str | None:
        plan = plan_for_board(
            board,  # type: ignore[arg-type] — a BoardFile
            session.project,
            {},
            None,
            datetime.now(UTC),
            strict=True,
            active_tab_only=False,
        )
        return plan["queries"]["q"]["source"]

    located = session.project.path("charts/located.yml").read_board(named=True)
    assert source_of(located) == "from_meta"
    with pytest.raises(_engine.CompileError, match="must have a source"):
        source_of(InMemoryBoard(text, path=None))
