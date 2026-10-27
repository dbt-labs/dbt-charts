"""A choropleth's gradient domain in the engine: nice-widened over the rows that
joined a feature (last row per key, as Vega's lookup keeps), exact when `nice`
is off or the legend is hidden."""

from __future__ import annotations

import re

from .._svg_render import render_board_svg_result


def _gradient_labels(rows: list[str], style: str = "") -> list[float]:
    queries = "".join(f"      - {row}\n" for row in rows)
    board = (
        "title: T\nqueries:\n  q:\n    type: values\n    rows:\n"
        f"{queries}"
        "charts:\n  c:\n    query: q\n    type: map\n"
        "    geo:\n      source: us-states\n"
        f"    lookup: state_id\n    value: pop\n{style}"
        "rows:\n  - c\n"
    )
    result = render_board_svg_result(board)
    assert not result.chart_errors
    group = re.search(
        r'<g class="mark-text role-legend-label"[^>]*>(.*?)</g>', result.output
    )
    if group is None:
        return []
    return sorted(
        float(t.replace(",", "")) for t in re.findall(r">([^<]+)</text>", group[1])
    )


def _style(gradient: str = "", legend: str = "") -> str:
    out = "    style:\n"
    if gradient:
        out += f"      color:\n        gradient:\n          {gradient}\n"
    if legend:
        out += f"      legend:\n        {legend}\n"
    return out


_JOINED_AND_UNMATCHED = [
    "{state_id: 6, pop: 12}",
    "{state_id: 48, pop: 88}",
    "{state_id: 999, pop: 5000}",
    "{state_id: 7, pop: -400}",
]


def test_an_unset_nice_widens_the_joined_rows_to_round_ends() -> None:
    labels = _gradient_labels(_JOINED_AND_UNMATCHED)
    assert labels[0] == 0
    assert labels[-1] == 100


def test_nice_false_or_a_bound_keeps_the_exact_extent() -> None:
    rows = ["{state_id: 6, pop: 12}", "{state_id: 48, pop: 88}"]
    assert _gradient_labels(rows, _style("nice: false")) == [12, 88]
    bounded = _gradient_labels(rows, _style("min: 0"))
    assert bounded[0] == 0
    assert bounded[-1] <= 88


def test_the_domain_reads_the_last_row_per_key() -> None:
    labels = _gradient_labels(
        [
            "{state_id: 6, pop: 12}",
            "{state_id: 6, pop: 140}",
            "{state_id: 48, pop: 88}",
        ],
        _style("nice: false"),
    )
    assert labels == [88, 140]


def test_a_later_null_row_shadows_an_earlier_number() -> None:
    labels = _gradient_labels(
        [
            "{state_id: 6, pop: 500}",
            "{state_id: 6, pop: null}",
            "{state_id: 48, pop: 88}",
            "{state_id: 36, pop: 12}",
        ]
    )
    assert labels[0] == 0
    assert labels[-1] == 100


def test_a_hidden_legend_draws_no_gradient_labels() -> None:
    rows = ["{state_id: 6, pop: 12}", "{state_id: 48, pop: 88}"]
    assert _gradient_labels(rows, _style(legend="visible: false")) == []
