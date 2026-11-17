"""Per-stage cost of a production ``render_dashboard`` over three boards.

Usage (repo root):  uv run python dbt-charts/tests/perf/bench_render_path.py [--runs 15]

Boards carry inline data, so execute is the in-process inline backend, not a warehouse:
``one-kpi`` (one KPI), ``seven`` (three KPIs, a line, a bar, a pie and a table) and
``large`` (a 6x7 grid of mixed charts). Each stage is the summed wall time of the calls
the render makes into it; ``total`` is the whole ``render_dashboard``. The engine compile
and plan run on a thread beside Python's compile, so their rows overlap ``total``.
The first run is discarded; the median of the rest is printed.
"""

from __future__ import annotations

import argparse
import statistics
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from dbt_charts import _engine
from dbt_charts.agent_api.project_session import ProjectSession
from dbt_charts.core import board as board_module
from dbt_charts.core.execute import planned
from dbt_charts.core.render import engine_cell, renderer
from dbt_charts.core.render_format import RenderFormat

MONTHS = [f"2025-{m:02d}-01" for m in range(1, 13)]
ROWS = "\n".join(
    f'      - ["{m}", {100 + 7 * i}, {40 + 3 * i}]' for i, m in enumerate(MONTHS)
)
QUERIES = f"""\
queries:
  headline:
    columns: [revenue, orders, delta]
    values:
      - [1284500, 7320, 0.124]
  series:
    columns: [month, revenue, orders]
    values:
{ROWS}
  cats:
    columns: [category, revenue]
    values:
      - [Electronics, 486200]
      - [Accessories, 312700]
      - [Tools, 274900]
      - [Outdoor, 210700]
"""

KPI = """\
  {name}:
    type: kpi
    query: headline
    label: Revenue
    value: revenue
    support:
      value: delta
      label: vs prior
      good: up
"""
LINE = "  {name}:\n    type: line\n    query: series\n    x: month\n    y: revenue\n"
BAR = "  {name}:\n    type: bar\n    query: cats\n    x: category\n    y: revenue\n"
PIE = "  {name}:\n    type: pie\n    query: cats\n    theta: revenue\n    color: category\n"
TABLE = "  {name}:\n    type: table\n    query: series\n"
KINDS = [KPI, LINE, BAR, PIE, TABLE]


def make_board(title: str, charts: list[str], per_row: int) -> str:
    defs, names = "", []
    for i, template in enumerate(charts):
        name = f"c{i}"
        names.append(name)
        defs += template.format(name=name)
    rows = ""
    for start in range(0, len(names), per_row):
        rows += "  - cols:\n" + "".join(
            f"      - {n}\n" for n in names[start : start + per_row]
        )
    return f"title: {title}\n{QUERIES}charts:\n{defs}rows:\n{rows}"


BOARDS = {
    "one-kpi": make_board("One KPI", [KPI], 1),
    "seven": make_board("Seven", [KPI, KPI, KPI, LINE, BAR, PIE, TABLE], 3),
    "large": make_board("Large", [KINDS[i % 5] for i in range(42)], 6),
}

STAGES = defaultdict[str, float](float)


def _timed(stage: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    def inner(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            STAGES[stage] += (time.perf_counter() - start) * 1000

    return inner


def install_timers() -> None:
    for name in (
        "compile",
        "plan",
        "render_chart",
        "render_board",
        "measure_table_natural_width",
    ):
        setattr(_engine, name, _timed(f"engine.{name}", getattr(_engine, name)))
    _engine.PreparedBoard = _timed("engine.prepare_board", _engine.PreparedBoard)
    for name in ("lookup_names", "run_misses", "collect"):
        setattr(renderer, name, _timed(f"execute.{name}", getattr(renderer, name)))
    engine_cell.draw_in_cell = _timed("cell.draw_in_cell", engine_cell.draw_in_cell)
    planned.plan_board = _timed("py.plan_board (incl. engine.plan)", planned.plan_board)
    planned.parse_query_plan = _timed("py.parse_query_plan", planned.parse_query_plan)
    board_module.plan_board = planned.plan_board
    for module in ("dbt_charts.core.render.chart.session",):
        mod = __import__(module, fromlist=["x"])
        if hasattr(mod, "draw_in_cell"):
            mod.draw_in_cell = _timed("cell.draw_in_cell", mod.draw_in_cell)


def run(name: str, text: str, runs: int, fmt: RenderFormat) -> dict[str, float]:
    with tempfile.TemporaryDirectory() as root:
        charts = Path(root) / "charts"
        charts.mkdir()
        (charts / f"{name}.yml").write_text(text)
        with ProjectSession.open(root) as session:
            file = session.project.path(f"charts/{name}.yml").read_board(named=True)
            samples: list[dict[str, float]] = []
            for i in range(runs + 1):
                STAGES.clear()
                start = time.perf_counter()
                result = session.render_board(file, format=fmt, use_cache=False)
                total = (time.perf_counter() - start) * 1000
                if result.status != "ok":
                    raise SystemExit(
                        f"{name}: {result.status} {result.validation_errors}"
                    )
                if i:
                    samples.append({**STAGES, "total": total})
    keys = sorted({k for s in samples for k in s})
    return {k: statistics.median(s.get(k, 0.0) for s in samples) for k in keys}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=15)
    parser.add_argument("--format", default="html", choices=["html", "svg"])
    parser.add_argument("--boards", nargs="*", default=list(BOARDS))
    args = parser.parse_args()
    install_timers()
    results = {n: run(n, BOARDS[n], args.runs, args.format) for n in args.boards}
    stages = sorted(
        {k for r in results.values() for k in r}, key=lambda k: (k == "total", k)
    )
    print(
        f"{'stage (ms, median of ' + str(args.runs) + ')':<34}"
        + "".join(f"{n:>10}" for n in results)
    )
    for stage in stages:
        print(
            f"{stage:<34}"
            + "".join(f"{r.get(stage, 0.0):>10.1f}" for r in results.values())
        )


if __name__ == "__main__":
    main()
