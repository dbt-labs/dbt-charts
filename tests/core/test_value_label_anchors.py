"""`labels.visible: last` or an anchor list prints each series' chosen values.

Goes through compile() -> render() -> SVG. Automatic dots (the engine's own on
a sparse line) give way to a dot at each labeled point; authored points stay.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile import CompileResult, compile
from dbt_charts.core.render import RenderResult, render

from .._run import Run
from .._scripted_env import ScriptedBackend, scripted_env

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
    registry = scripted_env(ScriptedBackend(rows))
    executor = Run(result.board, env=registry)
    return render(result.board, executor.ctx, executor.ledger, format="svg")


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
    no_rail = "    style:\n      legend:\n        position:\n          edge: top\n"
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
    assert _compile_errors(chart) == ["ERR-LABELS-ANCHOR-UNSUPPORTED"]


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
    assert _compile_errors(chart) == ["ERR-LABELS-ANCHOR-UNSUPPORTED"]


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
    assert _compile_errors(chart) == ["ERR-LABELS-ANCHOR-UNSUPPORTED"]


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
    assert [d.code for d in out.chart_errors] == ["ERR-LABELS-ANCHOR-UNSUPPORTED"]
    assert "time axis" in out.chart_errors[0].message


def test_bar_null_last_value_labels_the_last_non_null() -> None:
    rows = [*_ROWS[:4], {"m": "2026-05-01", "v": None}]
    svg = _render(_bar_labels("last"), rows, "bar")
    assert _labels(svg) == 1
    assert ">12<" in svg


def _anchor_refusal(body: str, rows: list[dict[str, object]]) -> str:
    result = compile(_board(body, "bar"))
    assert result.success and result.board is not None, result.errors
    out = _run(result, rows)
    assert [d.code for d in out.chart_errors] == ["ERR-LABELS-ANCHOR-UNSUPPORTED"]
    return out.chart_errors[0].message


@pytest.mark.parametrize("visible", ["last", "[max]"])
def test_grouped_bars_reject_anchors(visible: str) -> None:
    body = "    color: s\n" + _bar_labels(visible, "      stack: none\n")
    assert "grouped" in _anchor_refusal(body, _MULTI_ROWS)


@pytest.mark.parametrize("stack", ["normalize", "center"])
def test_stacks_other_than_zero_reject_anchors(stack: str) -> None:
    body = "    color: s\n" + _bar_labels("[max]", f"      stack: {stack}\n")
    assert stack in _anchor_refusal(body, _MULTI_ROWS)


def test_anchors_beside_a_visible_total_label_are_refused() -> None:
    body = "    color: s\n" + _bar_labels("[max]", "      stack: zero\n").replace(
        "            visible: [max]\n",
        "            visible: [max]\n          total_label:\n            visible: true\n",
    )
    assert "total_label" in _anchor_refusal(body, _MULTI_ROWS)


def test_stacked_bars_label_the_last_stack_total() -> None:
    body = "    color: s\n" + _bar_labels("last", "      stack: zero\n")
    svg = _render(body, _MULTI_ROWS, "bar")
    assert _labels(svg) == 0  # a total is no segment's value label
    assert ">36<" in svg
    assert ">44<" not in svg and ">8<" not in svg


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


_STACKED_ROWS = [
    {"m": r["m"], "v": r["v"] * mult, "s": s}
    for s, mult in (("A", 1), ("B", 10))
    for r in _ROWS
]
_NO_RAIL = "    style:\n      legend:\n        position:\n          edge: top\n"


@pytest.mark.parametrize("mark", ["line", "point"])
@pytest.mark.parametrize(
    ("visible", "printed"),
    [
        ("[first, last]", ["5", "8"]),
        ("[min, max]", ["4", "12"]),
        ("[max]", ["12"]),
        ("[last]", ["8"]),
        ("[first, last, min, max]", ["5", "8", "4", "12"]),
    ],
)
def test_a_line_labels_exactly_its_anchors(
    mark: str, visible: str, printed: list[str]
) -> None:
    svg = _render(_style(mark, visible), _ROWS)
    assert _labels(svg) == len(printed)
    for value in printed:
        assert f">{value}<" in svg


def test_a_point_two_anchors_name_prints_once() -> None:
    rows = [{"m": "2026-01-01", "v": 9}, *_ROWS[1:]]
    assert _labels(_render(_style("line", "[first, max]"), rows)) == 2
    rows = [{"m": "2026-01-01", "v": 13}, *_ROWS[1:]]
    assert _labels(_render(_style("line", "[first, max]"), rows)) == 1


def test_a_flat_series_labels_every_tied_point() -> None:
    rows = [{"m": r["m"], "v": 7} for r in _ROWS]
    assert _labels(_render(_style("line", "[min, max]"), rows)) == 5


def test_null_values_are_no_anchor() -> None:
    rows = [{"m": "2026-01-01", "v": None}, *_ROWS[1:4], {"m": "2026-05-01", "v": None}]
    svg = _render(_style("line", "[first, last, min]"), rows)
    assert _labels(svg) == 3
    assert ">9<" in svg and ">12<" in svg and ">4<" in svg


def test_each_series_gets_its_own_anchors() -> None:
    body = "    color: s\n" + _style("line", "[min, max]").replace(
        "    style:\n", _NO_RAIL
    )
    svg = _render(body, _MULTI_ROWS)
    assert _labels(svg) == 4
    for value in ("4", "12", "24", "32"):
        assert f">{value}<" in svg


def test_the_rail_keeps_the_last_value_and_labels_keep_the_rest() -> None:
    svg = _render("    color: s\n" + _style("line", "[first, last]"), _MULTI_ROWS)
    assert _labels(svg) == 2
    assert ">5<" in svg and ">25<" in svg
    assert ">8 A<" in svg and ">28 B<" in svg


def test_automatic_points_give_way_to_a_dot_at_each_anchor() -> None:
    assert _dots(_render(_style("line", "[min, max]"), _ROWS)) == 2


def test_authored_points_stay_beside_anchors() -> None:
    body = _style("line", "[min, max]", "        point:\n          size: 40\n")
    assert _dots(_render(body, _ROWS)) == 5


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
@pytest.mark.parametrize(
    ("visible", "printed"),
    [("[max]", ["12"]), ("[min]", ["4"]), ("[min, max]", ["4", "12"])],
)
def test_a_categorical_bar_takes_value_anchors(
    orientation: str, visible: str, printed: list[str]
) -> None:
    body = _bar_labels(visible, f"      orientation: {orientation}\n")
    svg = _render(body, _CATEGORY_ROWS, "bar")
    assert _labels(svg) == len(printed)
    for value in printed:
        assert f">{value}<" in svg


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_a_time_bar_takes_every_anchor(orientation: str) -> None:
    body = _bar_labels("[first, last, max]", f"      orientation: {orientation}\n")
    svg = _render(body, _ROWS, "bar")
    assert _labels(svg) == 3
    for value in ("5", "8", "12"):
        assert f">{value}<" in svg


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_a_stacked_bar_labels_its_tallest_and_shortest_totals(orientation: str) -> None:
    extra = f"      stack: zero\n      orientation: {orientation}\n"
    body = "    color: s\n" + _bar_labels("[min, max]", extra)
    svg = _render(body, _STACKED_ROWS, "bar")
    assert _labels(svg) == 0
    assert ">132<" in svg and ">44<" in svg
    assert ">99<" not in svg and ">88<" not in svg and ">12<" not in svg


def test_bars_colored_by_their_own_category_take_anchors() -> None:
    svg = _render("    color: m\n" + _bar_labels("[max]"), _CATEGORY_ROWS, "bar")
    assert _labels(svg) == 1
    assert ">12<" in svg


@pytest.mark.parametrize("visible", ["last", "[first]", "[last, max]"])
def test_categorical_bars_reject_position_anchors(visible: str) -> None:
    result = compile(_board(_bar_labels(visible), "bar"))
    assert result.success and result.board is not None, result.errors
    out = _run(result, _CATEGORY_ROWS)
    assert [d.code for d in out.chart_errors] == ["ERR-LABELS-ANCHOR-UNSUPPORTED"]
    assert "time axis" in out.chart_errors[0].message


def test_a_tie_labels_every_tied_bar() -> None:
    tied = [{**r, "v": 1 if i == 0 else 9} for i, r in enumerate(_ROWS)]
    assert _labels(_render(_bar_labels("[max]"), tied, "bar")) == 4


@pytest.mark.parametrize("visible", ["[]", "[max, max]", "[median]", "[true]"])
def test_compile_rejects_a_bad_anchor_list(visible: str) -> None:
    assert not compile(_board(_style("line", visible), "line")).success


def test_compile_rejects_value_anchors_on_scatter() -> None:
    chart = """    type: scatter
    x: v
    y: v
    style:
      marks:
        point:
          labels:
            visible: [max]
"""
    assert _compile_errors(chart) == ["ERR-LABELS-ANCHOR-UNSUPPORTED"]


def test_compile_rejects_value_anchors_on_a_layer() -> None:
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
                visible: [min, max]
"""
    assert _compile_errors(chart) == ["ERR-LABELS-ANCHOR-UNSUPPORTED"]
