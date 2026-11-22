"""``axis_x.type: nominal``: a month-name column draws as plain categories.

Labels such as "Oct 2025", "Nov" read as dates when sniffed. Forcing the axis
nominal paints them verbatim in query order, and an annotation names one of
them as its x.
"""

from __future__ import annotations

import pytest

from .._svg_render import render_board_to_svg

MIXED_MONTHS = ("Oct 2025", "Nov", "Dec", "Jan 2026")
YEARED_MONTHS = ("Aug 2026", "Sep 2026", "Oct 2026")


def _board(
    family: str,
    months: tuple[str, ...],
    *,
    x_type: str | None = None,
    note_x: str | None = None,
) -> str:
    # Descending values: a horizontal bar sorts by its measure, so query order
    # and drawn order agree on every family.
    rows = "\n".join(
        f"      - month: '{m}'\n        n: {9 - i}" for i, m in enumerate(months)
    )
    style = f"    style:\n      axis_x:\n        type: {x_type}\n" if x_type else ""
    note = (
        f"    annotations:\n      - type: note\n        x: {note_x}\n        y: 4\n"
        "        text: Launch\n"
        if note_x
        else ""
    )
    return f"""
queries:
  q1:
    type: values
    rows:
{rows}
charts:
  c1:
    query: q1
    type: {family}
    x: month
    y: n
{style}{note}rows:
  - c1
"""


def _label_offsets(svg: str, labels: tuple[str, ...]) -> list[int]:
    assert "ERR-" not in svg, svg[svg.find("ERR-") : svg.find("ERR-") + 300]
    at = [svg.find(f">{label}<") for label in labels]
    assert -1 not in at, dict(zip(labels, at, strict=True))
    return at


def _assert_drawn(svg: str, labels: tuple[str, ...]) -> None:
    at = _label_offsets(svg, labels)
    assert at == sorted(at), "labels paint in query order"


@pytest.mark.parametrize("family", ["bar", "line", "area"])
def test_nominal_draws_mixed_month_labels_verbatim(family: str) -> None:
    svg = render_board_to_svg(_board(family, MIXED_MONTHS, x_type="nominal"))
    _assert_drawn(svg, MIXED_MONTHS)


def test_nominal_scatter_draws_mixed_month_labels() -> None:
    # A scatter sorts any category band, so only the labels are checked.
    svg = render_board_to_svg(_board("scatter", MIXED_MONTHS, x_type="nominal"))
    _label_offsets(svg, MIXED_MONTHS)


@pytest.mark.parametrize("family", ["bar", "line"])
def test_a_note_anchors_to_a_label_on_a_nominal_axis(family: str) -> None:
    svg = render_board_to_svg(
        _board(family, YEARED_MONTHS, x_type="nominal", note_x="'Sep 2026'")
    )
    _assert_drawn(svg, YEARED_MONTHS)
    assert "Launch" in svg


@pytest.mark.parametrize("family", ["bar", "line"])
def test_a_note_anchors_to_a_date_on_a_sniffed_month_axis(family: str) -> None:
    svg = render_board_to_svg(_board(family, YEARED_MONTHS, note_x="2026-09-01"))
    assert "ERR-" not in svg, svg[svg.find("ERR-") : svg.find("ERR-") + 300]
    assert "Launch" in svg


def _layered_board(layer_type: str, layer_query: str) -> str:
    rows = "\n".join(
        f"      - month: '{m}'\n        n: {9 - i}\n        goal: {i + 2}"
        for i, m in enumerate(MIXED_MONTHS)
    )
    return f"""
queries:
  q1:
    type: values
    rows:
{rows}
  q2:
    type: values
    rows:
{rows}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: n
    style:
      orientation: vertical
      axis_x:
        type: nominal
    layers:
      - type: {layer_type}
        query: {layer_query}
        x: month
        y: goal
rows:
  - c1
"""


@pytest.mark.parametrize(
    ("layer_type", "layer_query"),
    [("line", "q1"), ("line", "q2"), ("bar", "q2")],
)
def test_a_layer_with_its_own_x_shares_the_nominal_axis(
    layer_type: str, layer_query: str
) -> None:
    svg = render_board_to_svg(_layered_board(layer_type, layer_query))
    _assert_drawn(svg, MIXED_MONTHS)
