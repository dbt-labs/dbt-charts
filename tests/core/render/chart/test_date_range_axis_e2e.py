"""A date-range filter on the column behind x pins the x axis to the range.

Drives real duckdb queries through resolve and the emitters. The bound is proven
from the query's own lineage; every unproven shape must leave the axis as it was.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_yaml
from dbt_charts.core.compile.config import (
    ProjectSourcesConfig,
    get_default_theme_name,
    get_theme_style,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.chart_resolution import resolve_chart_with_runtime_inputs
from dbt_charts.core.render import render
from dbt_charts.core.render.chart.emitters import get_emitter
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox

# Mondays: the first and last of March 2026 (Sun the 1st, Tue the 31st) both sit
# in weeks with no order, and the week of the 16th has none either.
_ORDERS = ["2026-03-02", "2026-03-09", "2026-03-23"]
_MARCH = [date(2026, 3, 1), date(2026, 3, 31)]
_WEEKS_OF_MARCH = [
    "2026-02-23",
    "2026-03-02",
    "2026-03-09",
    "2026-03-16",
    "2026-03-23",
    "2026-03-30",
]


def _yaml(
    chart_type: str = "bar",
    *,
    select: str = "date_trunc('week', created_at) AS week",
    group_by: str = "1",
    where: str = "{{ filter_date_range('created_at', date_range) }}",
    x: str = "week",
    table: str = "orders",
    style: str = "",
) -> str:
    return f"""
variables:
  date_range:
    input: daterange
queries:
  q:
    type: sql
    source: mem
    sql: |
      SELECT {select}, SUM(n) AS total
      FROM {table}
      WHERE {where}
      GROUP BY {group_by}
      ORDER BY 1
charts:
  c:
    query: q
    type: {chart_type}
    x: {x}
    y: total
{style}
rows:
  - c
"""


def _setup(yaml: str, tmp_path: Path) -> tuple[Any, Executor]:
    warehouse = tmp_path / "warehouse.duckdb"
    values = ",".join(
        f"(DATE '{day}', DATE '2026-01-01', {i + 1})" for i, day in enumerate(_ORDERS)
    )
    with duckdb.connect(str(warehouse)) as con:
        con.execute(
            f"CREATE OR REPLACE TABLE orders AS SELECT * FROM "
            f"(VALUES {values}) AS t(created_at, signup_date, n)"
        )
        con.execute(
            "CREATE OR REPLACE TABLE events AS SELECT * FROM (VALUES "
            "(TIMESTAMP '2026-03-05 10:00:00', 1), "
            "(TIMESTAMP '2026-03-31 18:00:00', 2)) AS t(ts, n)"
        )
    result = compile_yaml(
        yaml,
        project_sources=ProjectSourcesConfig(
            sources={"mem": {"type": "duckdb", "path": str(warehouse)}}
        ),
    )
    assert result.success and result.board is not None, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(tmp_path)),
        query_registry=result.query_registry,
    )
    return result.board, executor


def _emit(yaml: str, tmp_path: Path, rng: Any = _MARCH) -> tuple[Any, ChartSpec]:
    board, executor = _setup(yaml, tmp_path)
    variables = {"date_range": rng}
    data = executor.execute_query("q", variables)
    style = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    resolved = resolve_chart_with_runtime_inputs(
        board.charts["c"],
        data,
        style,
        600.0,
        executor,
        variables,
        filter_variables=frozenset(),
    )
    spec = get_emitter(resolved).emit(
        resolved, RenderBox(width=600.0, height=300.0), regroup((), data)
    )
    return resolved, spec


def test_resolve_bakes_the_proven_range(tmp_path: Path) -> None:
    resolved, _ = _emit(_yaml("line"), tmp_path)
    assert resolved.x_date_range == (_MARCH[0], _MARCH[1])


def test_temporal_line_axis_spans_the_range(tmp_path: Path) -> None:
    _, spec = _emit(_yaml("line"), tmp_path)
    x = spec.encoding["x"]
    assert x["type"] == "temporal"
    # Vega-Lite draws a `utcyearweek` mark on the Sunday before the data's Monday
    # bucket. The week containing Mar 1 opens Sun Feb 22 and the one containing
    # Mar 31 Sun Mar 29, where its mark sits.
    assert x["scale"]["domain"] == ["2026-02-22", "2026-03-29"]


def test_the_first_week_mark_sits_inside_the_plot(tmp_path: Path) -> None:
    # Orders in the range's first week: its mark is drawn on Sun Mar 1.
    first_week = [date(2026, 3, 2), _MARCH[1]]
    _, spec = _emit(_yaml("line"), tmp_path, rng=first_week)
    assert spec.encoding["x"]["scale"]["domain"] == ["2026-03-01", "2026-03-29"]
    board, executor = _setup(_yaml("line"), tmp_path)
    svg = render(
        board, executor, format="svg", variables={"date_range": first_week}
    ).output
    assert 'd="M-' not in svg


def test_band_axis_gets_a_slot_for_every_week_of_the_range(tmp_path: Path) -> None:
    _, spec = _emit(_yaml("bar"), tmp_path)
    assert spec.encoding["x"]["type"] == "ordinal"
    assert [row["week"] for row in spec.data] == _WEEKS_OF_MARCH
    assert [row["total"] for row in spec.data] == [None, 1, 2, None, 3, None]


def test_edge_buckets_follow_the_axis_fill_like_interior_gaps(tmp_path: Path) -> None:
    style = "    style:\n      axis_x:\n        fill: zero\n"
    _, spec = _emit(_yaml("bar", style=style), tmp_path)
    assert [row["total"] for row in spec.data] == [0, 1, 2, 0, 3, 0]


def test_query_rows_are_untouched(tmp_path: Path) -> None:
    board, executor = _setup(_yaml("bar"), tmp_path)
    rows = executor.execute_query("q", {"date_range": _MARCH})
    assert [r["week"] for r in rows] == [
        date(2026, 3, 2),
        date(2026, 3, 9),
        date(2026, 3, 23),
    ]


@pytest.mark.parametrize(
    "yaml",
    [
        pytest.param(
            _yaml(
                "bar",
                select="date_trunc('week', created_at) AS week",
                where="{{ filter_date_range('signup_date', date_range) }}",
            ),
            id="filter-on-a-column-that-does-not-feed-x",
        ),
        pytest.param(
            _yaml(
                "bar",
                select="EXTRACT(month FROM created_at) AS month",
                x="month",
            ),
            id="extract-month",
        ),
        pytest.param(
            _yaml(
                "bar",
                select="dayofweek(created_at) AS dow",
                x="dow",
            ),
            id="day-of-week",
        ),
        pytest.param(
            _yaml(
                "bar",
                where=("{{ filter_date_range('created_at', date_range) }} OR n > 100"),
            ),
            id="filter-under-or",
        ),
        pytest.param(
            _yaml(
                "bar",
                where=(
                    "{{ filter_date_range('created_at', date_range) }} "
                    "AND {{ filter_date_range('created_at', date_range) }}"
                ),
            ),
            id="two-range-filters-on-the-column",
        ),
    ],
)
def test_unproven_link_leaves_the_axis_alone(yaml: str, tmp_path: Path) -> None:
    resolved, spec = _emit(yaml, tmp_path)
    assert resolved.x_date_range is None
    assert "domain" not in spec.encoding["x"].get("scale", {})


def test_unset_range_pins_nothing(tmp_path: Path) -> None:
    resolved, _ = _emit(_yaml("line"), tmp_path, rng=None)
    assert resolved.x_date_range is None


def test_authored_domain_wins(tmp_path: Path) -> None:
    style = (
        "    style:\n      axis_x:\n        type: temporal\n"
        "        scale:\n          continuous:\n"
        '            domain: ["2026-01-01", "2026-06-30"]\n'
    )
    _, spec = _emit(_yaml("line", style=style), tmp_path)
    assert spec.encoding["x"]["scale"]["domain"] == ["2026-01-01", "2026-06-30"]


def _stable(svg: str) -> str:
    return re.sub(r"clip\d+-[\w-]+|Data as of [^<]+|\d{4}-\d\d-\d\dT[\d:]+Z", "", svg)


def test_timestamp_x_domain_keeps_the_whole_last_day(tmp_path: Path) -> None:
    yaml = _yaml(
        "line",
        select="ts AS t",
        x="t",
        table="events",
        where=("{{ filter_date_range('ts', date_range) }}"),
    )
    resolved, spec = _emit(yaml, tmp_path)
    assert resolved.x_date_range == (_MARCH[0], _MARCH[1])
    # The 18:00 point on Mar 31 is inside the filter, so inside the axis.
    assert spec.encoding["x"]["scale"]["domain"] == [
        "2026-03-01T00:00:00",
        "2026-04-01T00:00:00",
    ]


def test_temporal_bar_axis_spans_the_range(tmp_path: Path) -> None:
    style = "    style:\n      axis_x:\n        type: temporal\n"
    _, spec = _emit(_yaml("bar", style=style), tmp_path)
    assert spec.encoding["x"]["type"] == "temporal"
    assert spec.encoding["x"]["scale"]["domain"] == ["2026-02-22", "2026-04-05"]


def test_a_bar_layer_on_a_line_keeps_its_last_bar_inside_the_domain(
    tmp_path: Path,
) -> None:
    style = "    layers:\n      - type: bar\n        y: total\n"
    _, spec = _emit(_yaml("line", style=style), tmp_path)
    assert spec.encoding["x"]["scale"]["domain"] == ["2026-02-22", "2026-04-05"]


def test_a_layer_with_its_own_x_leaves_the_axis_alone(tmp_path: Path) -> None:
    style = (
        "    layers:\n      - type: scatter\n        x: signup_date\n        y: total\n"
    )
    yaml = _yaml(
        "line",
        select="date_trunc('week', created_at) AS week, MIN(signup_date) AS signup_date",
        style=style,
    )
    resolved, spec = _emit(yaml, tmp_path)
    assert resolved.x_date_range is None
    assert "domain" not in spec.encoding["x"].get("scale", {})


def test_a_range_past_the_band_budget_leaves_the_scaffold_at_the_data(
    tmp_path: Path,
) -> None:
    years = [date(2020, 1, 1), date(2026, 12, 31)]
    resolved, spec = _emit(_yaml("bar"), tmp_path, rng=years)
    assert resolved.x_date_range == (years[0], years[1])
    # ~360 weeks of range would be ~356 empty bands against a budget of 60.
    assert [row["week"] for row in spec.data] == [
        "2026-03-02",
        "2026-03-09",
        "2026-03-16",
        "2026-03-23",
    ]


def test_a_cached_render_does_not_need_its_source_resolvable(tmp_path: Path) -> None:
    board, executor = _setup(_yaml("bar"), tmp_path)
    variables = {"date_range": _MARCH}
    first = render(board, executor, format="svg", variables=variables).output
    with patch.object(
        executor.adapter_registry,
        "resolve_query_source",
        side_effect=socket.gaierror("no dns"),
    ):
        # A fresh render of the same executor reads rows from its memo.
        again = render(board, executor, format="svg", variables=variables)
    assert not again.chart_errors and again.board_error is None
    assert "discrete scale with 6 values" in again.output
    assert _stable(again.output) == _stable(first)


@pytest.mark.parametrize("time_unit", ["monthofyear", "dayofweek", "hourofday"])
def test_a_time_part_axis_is_not_given_a_calendar_domain(
    time_unit: str, tmp_path: Path
) -> None:
    style = f"    style:\n      axis_x:\n        time_unit: {time_unit}\n"
    _, spec = _emit(
        _yaml("bar", select="created_at AS d", x="d", style=style), tmp_path
    )
    assert "domain" not in spec.encoding["x"].get("scale", {})


def test_a_second_render_of_the_same_executor_pins_the_same_axis(
    tmp_path: Path,
) -> None:
    board, executor = _setup(_yaml("bar"), tmp_path)
    variables = {"date_range": _MARCH}
    first = render(board, executor, format="svg", variables=variables).output
    second = render(board, executor, format="svg", variables=variables).output
    for svg in (first, second):
        assert "discrete scale with 6 values" in svg
    assert _stable(first) == _stable(second)


def test_a_replayed_artifact_pins_the_same_axis_as_the_live_render(
    tmp_path: Path,
) -> None:
    from dbt_charts.core.execute.recording import load_board_recording, record_board
    from dbt_charts.core.render.board_replay import (
        dump_board_artifact,
        load_board_artifact,
        render_board_from_artifact,
    )
    from dbt_charts.core.render.board_resolve import build_resolved_board
    from dbt_charts.core.render.boards import render_board_svg

    board, executor = _setup(_yaml("bar"), tmp_path)
    variables = {"date_range": _MARCH}
    resolved, render_cache = build_resolved_board(board, executor, variables)
    background = resolved.style.background
    live = render_board_svg(
        resolved,
        executor,
        variables,
        background=None if background == "transparent" else background,
        render_cache=render_cache,
    )
    recording = load_board_recording(
        record_board(resolved, executor, variables).model_dump_json().encode()
    )

    replayed = render_board_from_artifact(
        load_board_artifact(dump_board_artifact(resolved)),
        recording,
        recording.variables,
    )

    assert "discrete scale with 6 values" in replayed
    assert _stable(replayed) == _stable(live)


def _svg(yaml: str, tmp_path: Path, rng: Any = _MARCH) -> str:
    board, executor = _setup(yaml, tmp_path)
    return render(board, executor, format="svg", variables={"date_range": rng}).output


def _assert_marks_inside_the_plot(svg: str) -> None:
    """Every mark's absolute x lies within the plot area."""
    plot_width = float(re.search(r'class="background"[^>]*\bd="M0,0h([\d.]+)v', svg)[1])
    marks = [
        d
        for d in re.findall(r'<path (?!class="background")[^>]*\bd="(M[^"]+)"', svg)
        if "," in d
    ]
    assert marks, svg
    for d in marks:
        for x in re.findall(r"[ML](-?[\d.]+),", d):
            assert 0 <= float(x) <= plot_width, (d, plot_width)


def test_a_truncated_x_before_the_range_start_does_not_pin(tmp_path: Path) -> None:
    yaml = _yaml("line", select="date_trunc('month', created_at) AS m", x="m")
    rng = [date(2026, 3, 4), _MARCH[1]]
    _, spec = _emit(yaml, tmp_path, rng=rng)
    assert "domain" not in spec.encoding["x"].get("scale", {})
    _assert_marks_inside_the_plot(_svg(yaml, tmp_path, rng))


@pytest.mark.parametrize("zone", ["UTC", "America/Los_Angeles", "Asia/Tokyo"])
def test_a_timestamp_axis_keeps_its_last_day_inside_the_plot_in_any_timezone(
    zone: str, tmp_path: Path
) -> None:
    # The renderer reads TZ once at process start, so the render runs in a child.
    code = (
        "import sys, tempfile\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
        "import test_date_range_axis_e2e as t\n"
        "y = t._yaml('line', select='ts AS t', x='t', table='events',\n"
        "    where=\"{{ filter_date_range('ts', date_range) }}\")\n"
        "print(t._svg(y, Path(tempfile.mkdtemp())))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "TZ": zone},
        capture_output=True,
        text=True,
        check=True,
    )
    _assert_marks_inside_the_plot(out.stdout)


def test_a_range_that_would_overrun_the_band_budget_is_not_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 40 months fit the 60-bucket band budget; the 70 months of the range do not,
    # and the axis must not flip to continuous time because of the filter.
    months = [f"{2022 + i // 12}-{i % 12 + 1:02d}-15" for i in range(40)]
    monkeypatch.setattr(sys.modules[__name__], "_ORDERS", months)
    yaml = _yaml("bar", select="date_trunc('month', created_at) AS m", x="m")
    rng = [date(2020, 1, 1), date(2025, 10, 31)]
    resolved, spec = _emit(yaml, tmp_path, rng=rng)
    assert resolved.x_date_range == tuple(rng)
    assert spec.encoding["x"]["type"] == "ordinal"
    assert len(spec.data) == 40


def test_a_daily_axis_is_scaffolded_to_the_range_past_the_coarse_band_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 90 daily bands exceed max_ordinal_buckets (60), but only coarse grains are
    # capped on total bands; the 40 empty slots fit the scaffold budget.
    days = [(date(2026, 2, 10) + timedelta(days=i)).isoformat() for i in range(50)]
    monkeypatch.setattr(sys.modules[__name__], "_ORDERS", days)
    yaml = _yaml("bar", select="date_trunc('day', created_at) AS d", x="d")
    rng = [date(2026, 1, 1), date(2026, 3, 31)]
    resolved, spec = _emit(yaml, tmp_path, rng=rng)
    assert resolved.x_date_range == tuple(rng)
    assert spec.encoding["x"]["type"] == "ordinal"
    assert len(spec.data) == 90
    assert spec.data[0]["d"] == "2026-01-01"
    assert spec.data[-1]["d"] == "2026-03-31"
    assert spec.data[0]["total"] is None
