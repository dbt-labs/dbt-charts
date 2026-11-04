"""What a cold `dct search` costs on a 40-board project, phase by phase.

Each `dct` call is a new process, so everything here runs in a subprocess: an
already-imported module would hide the cost being measured. The source carries
an `env_var()` because that is what a real warehouse config looks like, and
rendering one imports dbt-core.

The limits apply to CPU seconds, which equal wall seconds on an idle machine and
do not move with whatever else a busy one is running. Wall time is printed
beside them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

BOARD_COUNT = 40
REPEATS = 3
COLD_LIMIT_SECONDS = 3.0
WARM_LIMIT_SECONDS = 1.0

CONFIG = """\
sources:
  warehouse:
    type: bigquery
    project: fixture-project
    dataset: analytics
    attribution:
      run: "{{ env_var('COLD_START_RUN', 'none') }}"
"""

BOARD_HEADER = """\
title: Board {index} revenue
notes: Revenue and customers for region {index}.
source: warehouse
queries:
"""

QUERY = """\
  query_{chart}:
    sql: |
      select month, region, sum(amount) as revenue, count(*) as orders
      from orders_{index}
      where channel = 'channel_{chart}'
      group by 1, 2
      order by 1, 2
"""

CHART = """\
- chart_{chart}:
    title: Revenue by month {chart}
    query: query_{chart}
    type: bar
    x: month
    y: revenue
    color: region
"""

# Real boards run about 25 KB, and parsing them is most of what a search does.
CHARTS_PER_BOARD = 80

# Phases run in the order a `dct search` pays them. `limit=0` stops right after
# the source lookup, so `sources` is that lookup alone.
PHASE_PROBE = """
import json, sys, time
from pathlib import Path

marks = [time.perf_counter()]
import dbt_charts.cli.main
marks.append(time.perf_counter())
from dbt_charts.agent_api.search import search_files
from dbt_charts.cli.filesystem_project import FilesystemProject
marks.append(time.perf_counter())
project = FilesystemProject(Path(sys.argv[1]))
search_files(project, text="revenue", limit=0)
marks.append(time.perf_counter())
search_files(project, text="revenue", limit=3)
marks.append(time.perf_counter())
names = ("import_cli", "import_search", "sources", "search")
print(json.dumps(dict(zip(names, (b - a for a, b in zip(marks, marks[1:]))))))
"""


NO_BOARD_MODEL = """
import sys

built = [
    name
    for name in sys.modules
    if name.startswith("dbt_charts.core.compile.models.")
    and name.endswith(("board.authored", "board.normalized", "chart.authored"))
]
assert not built, built
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "dbt_charts.yml").write_text(CONFIG)
    charts = tmp_path / "charts"
    charts.mkdir()
    for index in range(BOARD_COUNT):
        (charts / f"board_{index:02d}.yml").write_text(_board(index))
    return tmp_path


def _board(index: int) -> str:
    charts = range(CHARTS_PER_BOARD)
    return "".join(
        [
            BOARD_HEADER.format(index=index),
            *(QUERY.format(index=index, chart=chart) for chart in charts),
            "rows:\n",
            *(CHART.format(chart=chart) for chart in charts),
        ]
    )


def _env() -> dict[str, str]:
    return {**os.environ, "DO_NOT_TRACK": "1", "DCT_NO_WORKSPACE_GUARD": "1"}


def _forget_index(project: Path) -> None:
    shutil.rmtree(project / ".dct", ignore_errors=True)


def _child_cpu() -> float:
    # Not at module scope: no such module on Windows, and collection imports this.
    import resource

    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def _dct_search(project: Path) -> tuple[float, float]:
    """CPU and wall seconds one `dct search` takes, start to exit."""
    cpu_before = _child_cpu()
    started = time.perf_counter()
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from dbt_charts.cli.main import app; app()",
            "search",
            "revenue",
            "--limit",
            "3",
        ],
        cwd=project,
        env=_env(),
        capture_output=True,
        check=True,
        timeout=30,
    )
    wall = time.perf_counter() - started
    return _child_cpu() - cpu_before, wall


def _cold(project: Path) -> tuple[float, float]:
    _forget_index(project)
    return _dct_search(project)


def _phases(project: Path) -> dict[str, float]:
    """Seconds per phase with no index yet, the fastest of ``REPEATS`` runs.

    The fastest run is the one least disturbed by whatever else the machine is
    doing, so it is the one that compares across commits.
    """
    runs: list[dict[str, float]] = []
    for _ in range(REPEATS):
        _forget_index(project)
        completed = subprocess.run(
            [sys.executable, "-c", PHASE_PROBE, str(project)],
            cwd=project,
            env=_env(),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        runs.append(json.loads(completed.stdout))
    return min(runs, key=lambda run: sum(run.values()))


@pytest.mark.slow
def test_search_cold_start(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    phases = _phases(project)
    cold_cpu, cold_wall = min(_cold(project) for _ in range(REPEATS))
    warm_cpu, warm_wall = min(_dct_search(project) for _ in range(REPEATS))
    with capsys.disabled():
        report = " ".join(f"{name}={value:.2f}" for name, value in phases.items())
        print(
            f"\ncold start: {report} "
            f"dct_cold cpu={cold_cpu:.2f} wall={cold_wall:.2f} "
            f"dct_warm cpu={warm_cpu:.2f} wall={warm_wall:.2f}"
        )
    if os.environ.get("CI"):
        pytest.skip("runner speed varies; the import guards below hold there")
    assert cold_cpu < COLD_LIMIT_SECONDS
    assert warm_cpu < WARM_LIMIT_SECONDS


def _assert_no_board_model_in(probe: str, project: Path) -> None:
    subprocess.run(
        [sys.executable, "-c", f"{probe}\n{NO_BOARD_MODEL}"],
        cwd=project,
        env=_env(),
        check=True,
        capture_output=True,
        timeout=30,
    )


def test_importing_search_builds_no_board_model(project: Path) -> None:
    _assert_no_board_model_in(
        "import dbt_charts.agent_api.search, dbt_charts.cli.main", project
    )


def test_a_warm_search_builds_no_board_model(project: Path) -> None:
    _dct_search(project)
    _assert_no_board_model_in(
        """
from dbt_charts.cli.main import app

app(["search", "revenue", "--limit", "3"], standalone_mode=False)
""",
        project,
    )


def test_validate_loads_no_web_server(project: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys

from dbt_charts.cli.main import app

app(["validate", "charts/board_00.yml"], standalone_mode=False)
assert "fastapi" not in sys.modules
""",
        ],
        cwd=project,
        env=_env(),
        check=True,
        capture_output=True,
        timeout=60,
    )
