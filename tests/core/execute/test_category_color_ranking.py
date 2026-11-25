"""Which palette slot a series value takes, whatever draws it.

One ladder decides a field's slot order on every chart type, whether one chart or
several color by the field: the author's stated order, then the values' own order,
then size. These tests pin that ladder on a board whose first-seen, alphabetical and
size orders all differ, so a rule that reads row order or sorts by name fails.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.resolve.chart._wide_fields import raw_wide_series_names
from dbt_charts.core.compile.resolve.style.category_colors import CategoryColorScale
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_compile import ERR_CATEGORY_COLOR_PIN_CONFLICT
from dbt_charts.core.execute.category_colors import (
    DefaultView,
    plan_board_category_colors,
)
from dbt_charts.core.execute.env import build_exec_env

from ..._compile import remember
from ..._run import Run

_SOURCES_DIR = Path(tempfile.mkdtemp(prefix="dct-category-rank-"))
(_SOURCES_DIR / "dbt_charts.yml").write_text(
    'sources:\n  mem:\n    type: duckdb\n    path: ":memory:"\n'
)

# Size: gamma (150) > beta (90) > alpha (10). First seen: beta, gamma, alpha
# (alpha has no January row). Alphabetical: alpha, beta, gamma.
_SIZE_RANK = {"gamma": 0, "beta": 1, "alpha": 2}

_QUERIES = """
queries:
  long:
    type: values
    rows:
      - {month: "2024-01-01", series: beta, n: 30}
      - {month: "2024-01-01", series: gamma, n: 50}
      - {month: "2024-02-01", series: alpha, n: 5}
      - {month: "2024-02-01", series: beta, n: 30}
      - {month: "2024-02-01", series: gamma, n: 50}
      - {month: "2024-03-01", series: alpha, n: 5}
      - {month: "2024-03-01", series: beta, n: 30}
      - {month: "2024-03-01", series: gamma, n: 50}
  wide:
    type: values
    rows:
      - {month: "2024-01-01", alpha: 5, beta: 30, gamma: 50}
"""

_CHARTS = {
    "line": "{type: line, query: long, x: month, y: n, color: series}",
    "bar": "{type: bar, query: long, x: month, y: n, color: series}",
    "area": "{type: area, query: long, x: month, y: n, color: series}",
}


def _board(charts: list[str], extra: str = "") -> Board:
    defined = "\n".join(f"  {c}:\n    {_expanded(c)}" for c in charts)
    text = f"{_QUERIES}{extra}charts:\n{defined}\nrows:\n" + "\n".join(
        f"  - {c}" for c in charts
    )
    return _compiled(text)


def _expanded(chart: str) -> str:
    return _CHARTS[chart].replace("{", "").replace("}", "").replace(", ", "\n    ")


def _compiled(text: str) -> Board:
    project = FilesystemProject(_SOURCES_DIR)
    result = compile(text, project_sources=project.sources)
    assert result.success and result.board is not None, result.errors
    remember(result.board, text)
    return result.board


def _plan(
    board: Board, default_view: DefaultView | None = None
) -> dict[str, CategoryColorScale]:
    run = Run(board, env=build_exec_env(FilesystemProject(_SOURCES_DIR)))
    scales = plan_board_category_colors(board, run, {}, {}, default_view)
    return {s.field: s for s in scales}


@pytest.mark.parametrize("chart", ["line", "bar", "area"])
def test_a_lone_chart_seats_the_largest_series_first(chart: str) -> None:
    assert _plan(_board([chart]))["series"].slots == _SIZE_RANK


def test_every_chart_type_together_seats_the_same_slots() -> None:
    assert _plan(_board(["line", "bar", "area"]))["series"].slots == _SIZE_RANK


def test_a_wide_chart_seats_measures_in_y_order() -> None:
    assert raw_wide_series_names(["gamma", "alpha", "beta"], None, []) == [
        "gamma",
        "alpha",
        "beta",
    ]


def test_a_chart_on_another_measure_weighs_by_share_not_magnitude() -> None:
    """A chart on a far larger measure counts as shares, not raw magnitude."""
    scaled = _QUERIES.replace(
        "wide:",
        """scaled:
    type: values
    rows:
      - {month: "2024-01-01", series: alpha, n: 9000}
      - {month: "2024-01-01", series: beta, n: 1000}
      - {month: "2024-01-01", series: gamma, n: 100}
  wide:""",
    )
    text = (
        f"{scaled}charts:\n"
        "  a:\n    type: bar\n    query: long\n    x: month\n    y: n\n    color: series\n"
        "  b:\n    type: bar\n    query: scaled\n    x: month\n    y: n\n    color: series\n"
        "rows:\n  - a\n  - b\n"
    )
    # Shares of 250 and 10100: alpha .04 + .89, gamma .6 + .01, beta .36 + .10. Raw
    # sums would seat beta before gamma.
    assert _plan(_compiled(text))["series"].slots == {"alpha": 0, "gamma": 1, "beta": 2}


def test_an_authored_series_order_data_follows_the_rows() -> None:
    text = (
        f"{_QUERIES}charts:\n"
        "  a:\n    type: area\n    query: long\n    x: month\n    y: n\n"
        "    color: series\n    style:\n      series_order: data\n"
        "rows:\n  - a\n"
    )
    assert _plan(_compiled(text))["series"].slots == {"beta": 0, "gamma": 1, "alpha": 2}


def test_an_authored_series_order_alphabetical_follows_the_names() -> None:
    text = (
        f"{_QUERIES}charts:\n"
        "  a:\n    type: area\n    query: long\n    x: month\n    y: n\n"
        "    color: series\n    style:\n      series_order: alphabetical\n"
        "rows:\n  - a\n"
    )
    assert _plan(_compiled(text))["series"].slots == {"alpha": 0, "beta": 1, "gamma": 2}


def test_pins_come_before_size() -> None:
    extra = """style:
  charts:
    category_colors:
      series:
        values:
          alpha: category[1]
"""
    assert _plan(_board(["line"], extra))["series"].slots["alpha"] == 0


@pytest.mark.parametrize(
    "labels",
    [["P10", "P2", "P1", "P9"], ["Tier 3", "Tier 1", "Tier 10"], ["Q4", "Q1", "Q3"]],
)
def test_ranked_labels_take_slots_in_number_order(labels: list[str]) -> None:
    rows = "\n".join(
        f"      - {{k: {label}, n: {100 - i}}}" for i, label in enumerate(labels)
    )
    text = (
        f"queries:\n  q:\n    type: values\n    rows:\n{rows}\n"
        "charts:\n  a:\n    type: bar\n    query: q\n    x: k\n    y: n\n    color: k\n"
        "rows:\n  - a\n"
    )
    slots = _plan(_compiled(text))["k"].slots
    by_number = sorted(labels, key=lambda label: int(label.lstrip("PTierQ ")))
    assert sorted(slots, key=slots.__getitem__) == by_number


_FILTERED = """
variables:
  region:
    input: select
    options:
      static: [east, west]
    default: east
queries:
  sales:
    source: mem
    sql: >-
      SELECT * FROM (VALUES
        ('east', 'alpha', 90), ('east', 'beta', 10),
        ('west', 'alpha', 1), ('west', 'beta', 50), ('west', 'gamma', 40)
      ) AS t(region, series, n) WHERE region = '{{ region }}'
charts:
  a:
    type: bar
    query: sales
    x: series
    y: n
    color: series
rows:
  - a
"""


def test_a_filtered_render_keeps_the_default_view_colors() -> None:
    """Under `west`, beta outweighs alpha, but the default view (`east`) decides."""
    board = _compiled(_FILTERED)
    env = build_exec_env(FilesystemProject(_SOURCES_DIR))
    default = Run(board, env=env)
    filtered = Run(board, env=env)
    scales = plan_board_category_colors(
        board,
        filtered,
        {"region": "west"},
        {},
        DefaultView(default, {"region": "east"}),
    )
    # A value only the filtered view draws (gamma) still gets a slot, after the rest.
    assert scales[0].slots == {"alpha": 0, "beta": 1, "gamma": 2}


def test_an_unfiltered_render_reads_no_second_view() -> None:
    board = _compiled(_FILTERED)
    env = build_exec_env(FilesystemProject(_SOURCES_DIR))
    run = Run(board, env=env)
    scales = plan_board_category_colors(board, run, {"region": "west"}, {})
    assert scales[0].slots == {"beta": 0, "gamma": 1, "alpha": 2}


_SIBLINGS = """
queries:
  q:
    type: values
    rows:
      - {{region: North, n: 1}}
      - {{region: South, n: 2}}
charts:
  a:
    {{type: bar, query: q, x: region, y: n, color: region}}
rows:
  - style:
      charts:
        category_colors:
          region:
            values:
              North: "{first}"
    cols:
      - a
  - style:
      charts:
        category_colors:
          region:
            values:
              North: "{second}"
              South: "#333333"
    cols:
      - a
"""


def test_sibling_boards_pinning_a_value_differently_raise() -> None:
    board = _compiled(_SIBLINGS.format(first="#111111", second="#222222"))
    with pytest.raises(ChartDataError) as raised:
        _plan(board)
    assert raised.value.code is ERR_CATEGORY_COLOR_PIN_CONFLICT


def test_sibling_boards_pinning_different_values_both_apply() -> None:
    board = _compiled(_SIBLINGS.format(first="#111111", second="#111111"))
    assert _plan(board)["region"].overrides == {
        "North": "#111111",
        "South": "#333333",
    }


def test_a_null_color_cell_owns_a_slot_as_the_series_the_engine_draws() -> None:
    text = (
        "queries:\n  q:\n    type: values\n    rows:\n"
        "      - {k: a, n: 5}\n      - {k: null, n: 9}\n"
        "charts:\n  a:\n    type: bar\n    query: q\n    x: k\n    y: n\n    color: k\n"
        "rows:\n  - a\n"
    )
    assert _plan(_compiled(text))["k"].slots == {"a": 0, "null": 1}


def _single(rows: list[str], chart: str, field: str) -> dict[str, int]:
    body = "".join(f"      - {row}\n" for row in rows)
    text = f"queries:\n  q:\n    type: values\n    rows:\n{body}charts:\n  c:\n{chart}rows:\n  - c\n"
    return dict(_plan(_compiled(text))[field].slots)


def test_a_backwards_axis_gives_the_lead_color_to_the_smallest_rank() -> None:
    chart = (
        "    type: line\n    query: q\n    x: year\n    y: rank\n    color: who\n"
        "    style:\n      axis_y:\n        scale:\n          continuous:\n            domain: [6, 1]\n"
    )
    rows = ["{year: a, who: best, rank: 1}", "{year: a, who: worst, rank: 6}"]
    assert _single(rows, chart, "who") == {"best": 0, "worst": 1}


def test_a_scatter_colors_by_average_not_point_count() -> None:
    chart = "    type: scatter\n    query: q\n    x: x\n    y: y\n    color: kind\n"
    rows = [f"{{x: {i}, y: 10, kind: many}}" for i in range(5)] + [
        "{x: 9, y: 40, kind: few}"
    ]
    assert _single(rows, chart, "kind") == {"few": 0, "many": 1}


def test_a_waterfall_ranks_its_directions_by_bar_length_whatever_their_names() -> None:
    chart = "    type: bar\n    query: q\n    x: step\n    y: end\n    y_start: start\n    color: direction\n"
    # By end value Gain (140) > Loss (120) > Net (40); by drawn length Gain > Net > Loss.
    rows = [
        "{step: a, start: 0, end: 40, direction: Net}",
        "{step: b, start: 40, end: 140, direction: Gain}",
        "{step: c, start: 140, end: 120, direction: Loss}",
    ]
    assert _single(rows, chart, "direction") == {"Gain": 0, "Net": 1, "Loss": 2}


def test_a_negative_chart_ranks_its_series_by_magnitude() -> None:
    chart = "    type: line\n    query: q\n    x: m\n    y: v\n    color: s\n"
    rows = ["{m: a, s: small, v: -10}", "{m: a, s: big, v: -100}"]
    assert _single(rows, chart, "s") == {"big": 0, "small": 1}


def test_a_mixed_sign_chart_ranks_its_series_by_magnitude() -> None:
    chart = "    type: line\n    query: q\n    x: m\n    y: v\n    color: s\n"
    rows = ["{m: a, s: A, v: 100}", "{m: a, s: B, v: 50}", "{m: a, s: C, v: -160}"]
    assert _single(rows, chart, "s") == {"C": 0, "A": 1, "B": 2}


def test_a_backwards_axis_with_mixed_signs_ranks_the_smallest_magnitude_first() -> None:
    chart = (
        "    type: line\n    query: q\n    x: m\n    y: v\n    color: s\n"
        "    style:\n      axis_y:\n        scale:\n          continuous:\n            domain: [200, -200]\n"
    )
    rows = ["{m: a, s: A, v: 100}", "{m: a, s: B, v: 50}", "{m: a, s: C, v: -160}"]
    assert _single(rows, chart, "s") == {"B": 0, "A": 1, "C": 2}
