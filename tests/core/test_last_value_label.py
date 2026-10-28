"""`labels.visible: last` prints each series' most recent value once.

Goes through compile() -> render() -> SVG. Automatic dots (the engine's own on
a sparse line) give way to a single endpoint dot; authored points stay.
"""

from __future__ import annotations

import re
from unittest.mock import Mock

import pytest

from dbt_charts.core.compile import CompileResult, compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import RenderResult, render

_ROWS = [{"m": f"2026-0{i}-01", "v": v} for i, v in enumerate([5, 9, 4, 12, 8], 1)]
_MULTI_ROWS = [
    {"m": r["m"], "v": r["v"] + off, "s": s}
    for s, off in (("A", 0), ("B", 20))
    for r in _ROWS
]


def _board(chart_body: str, kind: str) -> str:
    return f"""
title: probe
charts:
  c:
    type: {kind}
    query: q
    x: m
    y: v
    width: 600
{chart_body}
queries:
  q:
    sql: "SELECT 1"
    source: test_source
rows: [{{rows: [c]}}]
"""


def _run(result: CompileResult, rows: list[dict[str, object]]) -> RenderResult:
    assert result.board is not None
    ok = Mock()
    ok.is_success = True
    ok.data = rows
    ok.resolved_relations = None
    ok.truncated_reason = None
    registry = Mock()
    registry.execute.return_value = ok
    executor = Executor(
        result.board, adapter_registry=registry, query_registry=result.query_registry
    )
    return render(result.board, executor, format="svg")


def _render(chart_body: str, rows: list[dict[str, object]], kind: str = "line") -> str:
    result = compile(_board(chart_body, kind))
    assert result.success and result.board is not None, result.errors
    out = _run(result, rows)
    assert not out.chart_errors, out.chart_errors
    assert isinstance(out.output, str)
    return out.output


def _labels(svg: str) -> int:
    groups = re.findall(r'data-dbt-value-label="true"[^>]*>(.*?)</g>', svg, re.S)
    return sum(g.count("<text") for g in groups)


def _dots(svg: str) -> int:
    """Visible point markers: circles that are neither halo rings nor hover targets."""
    circles = re.findall(r"<circle [^>]*>", svg)
    return sum(1 for c in circles if 'opacity="0"' not in c and "stroke-width" not in c)


def _style(mark: str, visible: str, extra: str = "") -> str:
    return f"""    style:
      marks:
        {mark}:
          labels:
            visible: {visible}
            position: top
{extra}"""


@pytest.mark.parametrize("mark", ["line", "point"])
def test_single_series_labels_only_the_last_value(mark: str) -> None:
    svg = _render(_style(mark, "last"), _ROWS)
    assert _labels(svg) == 1
    assert ">8<" in svg  # the last row's value, not the max


def test_every_point_still_labels_all() -> None:
    assert _labels(_render(_style("line", "true"), _ROWS)) == 5


def test_null_last_value_labels_the_last_non_null() -> None:
    rows = [*_ROWS[:4], {"m": "2026-05-01", "v": None}]
    svg = _render(_style("line", "last"), rows)
    assert _labels(svg) == 1
    assert ">12<" in svg


def test_each_series_gets_its_own_last_value() -> None:
    no_rail = "    style:\n      endpoint_labels:\n        visible: false\n"
    body = "    color: s\n" + _style("line", "last").replace("    style:\n", no_rail)
    svg = _render(body, _MULTI_ROWS)
    assert _labels(svg) == 2
    assert ">8<" in svg and ">28<" in svg


def test_a_rail_that_already_names_the_value_is_not_labeled_twice() -> None:
    svg = _render("    color: s\n" + _style("line", "last"), _MULTI_ROWS)
    assert _labels(svg) == 0
    assert ">8 A<" in svg and ">28 B<" in svg


def test_small_multiples_label_the_last_value_per_panel() -> None:
    svg = _render(
        "    multiples:\n      rows: s\n" + _style("line", "last"), _MULTI_ROWS
    )
    assert _labels(svg) == 2


def test_automatic_points_give_way_to_one_endpoint_dot() -> None:
    all_dots = _dots(_render(_style("line", "true"), _ROWS))
    assert all_dots == 5
    assert _dots(_render(_style("line", "last"), _ROWS)) == 1


def test_authored_points_stay() -> None:
    body = _style("line", "last", "        point:\n          size: 40\n")
    assert _dots(_render(body, _ROWS)) == 5


def test_a_line_without_points_gets_the_endpoint_dot() -> None:
    body = _style("line", "last", "        point:\n          size: 0\n")
    assert _dots(_render(body, _ROWS)) == 1


def _compile_errors(chart: str) -> list[str]:
    source = f"""
title: probe
charts:
  c:
    query: q
{chart}
queries:
  q:
    sql: "SELECT 1"
    source: test_source
rows: [{{rows: [c]}}]
"""
    return [d.code for d in compile(source).errors]


def test_compile_rejects_last_on_scatter() -> None:
    chart = """    type: scatter
    x: v
    y: v
    style:
      marks:
        point:
          labels:
            visible: last
"""
    assert _compile_errors(chart) == ["ERR-LABELS-LAST-UNSUPPORTED"]


def test_compile_rejects_last_on_a_layer() -> None:
    chart = """    type: line
    x: m
    y: v
    layers:
      - type: line
        y: v
        style:
          marks:
            line:
              labels:
                visible: last
"""
    assert _compile_errors(chart) == ["ERR-LABELS-LAST-UNSUPPORTED"]


def test_compile_rejects_last_on_a_bar_layer() -> None:
    chart = """    type: line
    x: m
    y: v
    layers:
      - type: bar
        y: v
        style:
          marks:
            bar:
              labels:
                visible: last
"""
    assert _compile_errors(chart) == ["ERR-LABELS-LAST-UNSUPPORTED"]


def test_compile_accepts_last_on_a_plain_line() -> None:
    chart = "    type: line\n    x: m\n    y: v\n" + _style("line", "last")
    assert _compile_errors(chart) == []


def _bar_labels(visible: str, extra: str = "") -> str:
    return f"""    style:
{extra}      marks:
        bar:
          labels:
            visible: {visible}
"""


def test_bar_every_label_still_labels_all() -> None:
    assert _labels(_render(_bar_labels("true"), _ROWS, "bar")) == 5


# A time-series bar means its most recent date, whatever order the bars are
# drawn in: horizontal bars sort by value, which would put Mar (4) last.
@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_time_bars_label_the_most_recent_date(orientation: str) -> None:
    body = _bar_labels("last", f"      orientation: {orientation}\n")
    svg = _render(body, _ROWS, "bar")
    assert _labels(svg) == 1
    assert ">8<" in svg


_CATEGORY_ROWS = [{**r, "m": name} for r, name in zip(_ROWS, "ABCDE", strict=True)]


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_categorical_bars_reject_last(orientation: str) -> None:
    body = _bar_labels("last", f"      orientation: {orientation}\n")
    source = _board(body, "bar")
    result = compile(source)
    assert result.success and result.board is not None, result.errors
    out = _run(result, _CATEGORY_ROWS)
    assert [d.code for d in out.chart_errors] == ["ERR-LABELS-LAST-UNSUPPORTED"]
    assert "time axis" in out.chart_errors[0].message


def test_bar_null_last_value_labels_the_last_non_null() -> None:
    rows = [*_ROWS[:4], {"m": "2026-05-01", "v": None}]
    svg = _render(_bar_labels("last"), rows, "bar")
    assert _labels(svg) == 1
    assert ">12<" in svg


def test_grouped_bars_label_each_series_last_bar() -> None:
    body = "    color: s\n" + _bar_labels("last", "      stack: none\n")
    svg = _render(body, _MULTI_ROWS, "bar")
    assert _labels(svg) == 2
    assert ">8<" in svg and ">28<" in svg


def test_stacked_bars_label_each_segment_of_the_last_stack() -> None:
    body = "    color: s\n" + _bar_labels("last", "      stack: zero\n")
    svg = _render(body, _MULTI_ROWS, "bar")
    assert _labels(svg) == 2
    assert ">8<" in svg and ">28<" in svg


def test_bar_small_multiples_label_the_last_bar_per_panel() -> None:
    body = "    multiples:\n      rows: s\n" + _bar_labels("last")
    assert _labels(_render(body, _MULTI_ROWS, "bar")) == 2


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_year_shaped_integer_bars_label_the_latest_year(orientation: str) -> None:
    values = [5, 9, 4, 12, 77]
    rows = [
        {"m": year, "v": v} for year, v in zip(range(2022, 2027), values, strict=True)
    ]
    rows.reverse()  # the latest year is the first row
    body = _bar_labels("last", f"      orientation: {orientation}\n")
    svg = _render(body, rows, "bar")
    assert _labels(svg) == 1
    assert ">77<" in svg


def _render_codes(
    body: str, rows: list[dict[str, object]], extra: str = ""
) -> list[str]:
    source = _board(body, "bar").replace("    width: 600\n", f"    width: 600\n{extra}")
    result = compile(source)
    assert result.success and result.board is not None, result.errors
    return [d.code for d in _run(result, rows).chart_errors]


def _middle_aligned(stack: str = "") -> str:
    return f"""    style:
{stack}      marks:
        bar:
          labels:
            visible: last
            position: middle_aligned
"""


def test_last_labels_keep_the_stacked_middle_aligned_refusal() -> None:
    body = "    color: s\n" + _middle_aligned("      stack: zero\n")
    assert _render_codes(body, _MULTI_ROWS) == ["ERR-STACKED-MIDDLE-ALIGNED-LABELS"]


def test_last_labels_keep_the_span_middle_aligned_refusal() -> None:
    rows = [{**r, "v0": 1} for r in _ROWS]
    assert _render_codes(_middle_aligned(), rows, "    y_start: v0\n") == [
        "ERR-SPAN-MIDDLE-ALIGNED-LABELS"
    ]
