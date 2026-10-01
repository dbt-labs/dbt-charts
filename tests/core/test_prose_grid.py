"""Prose columns sit on the board's card grid.

Every test renders a real board and reads where the text landed. The expected
edges are computed here from the board's own width, margin, ``cols.gap`` and
card padding -- the arithmetic a ``cols:`` row of cards uses -- not from the
engine's helpers, so the two can disagree.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.render import prose
from dbt_charts.core.render.board_resolve import build_resolved_board_static

from ._svg_render import render_board_to_svg

SENTENCES = (
    "Paid search held its conversion rate within a point of plan across the "
    "quarter, so the shortfall is concentrated upstream rather than in the "
    "closing motion. Field events, which carried a third of pipeline a year "
    "ago, produced fewer registrations and a materially lower show rate. "
    "North America's primary issue is insufficient MQL supply, not a regional "
    "Stage 0 target miss or a broad loss of selling capacity. "
)
LIST_ITEMS = [
    "Paid search held conversion within a point of plan all quarter",
    "Field events produced fewer registrations than last year",
    "Show rate at field events fell by roughly a third year over year",
    "North America's gap is MQL supply, not a Stage 0 target miss",
    "EMEA held pipeline coverage above three times through the quarter",
    "Selling capacity was flat; ramped reps were unchanged",
    "Partner-sourced pipeline grew for the third straight quarter",
    "Win rates held steady against the two main competitors",
]
QUERY = """
queries:
  k:
    type: values
    columns: [label, value]
    values:
      - [A, 1]
      - [B, 2]
charts:
  a: {type: table, query: k}
  b: {type: table, query: k}
  c: {type: table, query: k}
"""


def passage(marker: str, chars: int) -> str:
    body = (SENTENCES * 20)[:chars]
    return f"{marker} " + body[: body.rfind(" ")] + "."


def prose_row(text: str) -> str:
    indented = "\n".join("      " + line for line in text.split("\n"))
    return f"  - text: |\n{indented}\n"


def cards(n: int) -> str:
    return "  - cols:\n" + "".join(f"      - {'abc'[i]}\n" for i in range(n))


def board(width: int, *rows: str, style: str = "") -> str:
    return f"width: {width}\n{QUERY}{style}rows:\n" + "".join(rows)


def card_edges(svg: str, n: int) -> list[float]:
    """Content edges of the first n-up ``cols:`` row the layout drew.

    Read off the rendered card boxes (``dbt-box-inner``), so the expectation is
    whatever the real layout code placed, not arithmetic restated here.
    """
    rows: dict[float, list[tuple[float, float]]] = defaultdict(list)

    def walk(el: ET.Element, tx: float, ty: float) -> None:
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "svg":
            tx += float(el.get("x", 0))
            ty += float(el.get("y", 0))
        match = re.match(
            r"translate\(([-\d.]+)[ ,]+([-\d.]+)\)", el.get("transform", "")
        )
        if match:
            tx += float(match.group(1))
            ty += float(match.group(2))
        if tag == "rect" and el.get("class") == "dbt-box-inner":
            width = float(el.get("width", 0))
            rows[round(ty + float(el.get("y", 0)))].append(
                (tx + float(el.get("x", 0)), width)
            )
        for child in el:
            walk(child, tx, ty)

    root = ET.fromstring(svg)
    # A card is narrower than the board's content; the row's own container is not.
    content = float(root.get("width", 0)) - 2 * 36.0
    walk(root, 0.0, 0.0)
    for _, boxes in sorted(rows.items()):
        if len(boxes) == n and (n == 1 or all(w < 0.95 * content for _, w in boxes)):
            return sorted(x for x, _ in boxes)
    raise AssertionError(f"no {n}-up card row in the render")


def text_lines(svg: str) -> list[tuple[float, float, str]]:
    """(absolute x, absolute y, text) of every text line with an x."""
    out: list[tuple[float, float, str]] = []

    def walk(el: ET.Element, tx: float, ty: float) -> None:
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "svg":
            tx += float(el.get("x", 0))
            ty += float(el.get("y", 0))
        match = re.match(
            r"translate\(([-\d.]+)[ ,]+([-\d.]+)\)", el.get("transform", "")
        )
        if match:
            tx += float(match.group(1))
            ty += float(match.group(2))
        if tag == "text" and el.get("x") is not None:
            text = "".join(el.itertext()).strip()
            if text:
                out.append(
                    (tx + float(el.get("x", 0)), ty + float(el.get("y", 0)), text)
                )
        for child in el:
            walk(child, tx, ty)

    walk(ET.fromstring(svg), 0.0, 0.0)
    return out


def column_starts(svg: str, markers: list[str]) -> dict[str, list[float]]:
    """Distinct line-start x of each prose block, found by its leading marker.

    A block runs from its marker line down to the next marker or card table
    header ("Label"), whichever comes first.
    """
    lines = text_lines(svg)
    tops = {m: min(y for _, y, t in lines if t.startswith(m)) for m in markers}
    headers = [y for _, y, t in lines if t == "Label"]
    order = sorted(markers, key=tops.__getitem__)
    ends = [
        min(
            [tops[n] for n in order if tops[n] > tops[m]]
            + [y for y in headers if y > tops[m]],
            default=float("inf"),
        )
        for m in order
    ]
    starts: dict[str, set[float]] = defaultdict(set)
    for marker, end in zip(order, ends, strict=True):
        for x, y, _ in lines:
            if tops[marker] - 0.5 <= y < end - 0.5:
                starts[marker].add(round(x, 1))
    return {m: sorted(v) for m, v in starts.items()}


def assert_on(actual: list[float], expected: list[float]) -> None:
    assert len(actual) == len(expected), f"{actual} vs card edges {expected}"
    assert actual == pytest.approx(expected, abs=0.5)


def test_columns_land_on_card_content_edges_of_the_thirds() -> None:
    """A full-width column starts on a card content edge of its grid."""
    svg = render_board_to_svg(
        board(
            1200,
            prose_row(passage("NOTE", 600)),
            prose_row(passage("READOUT", 1400)),
            cards(3),
        )
    )
    starts = column_starts(svg, ["NOTE", "READOUT"])
    thirds = card_edges(svg, 3)
    assert_on(starts["NOTE"], thirds[:2])
    assert_on(starts["READOUT"], thirds)


def test_a_medium_passage_takes_two_of_three_thirds_never_two_halves() -> None:
    svg = render_board_to_svg(
        board(
            1200,
            prose_row(passage("MEDIUM", 650)),
            prose_row(passage("READOUT", 1400)),
            cards(3),
        )
    )
    starts = column_starts(svg, ["MEDIUM", "READOUT"])
    assert_on(starts["MEDIUM"], card_edges(svg, 3)[:2])


def test_a_short_passage_is_one_whole_span() -> None:
    """One column is always available, and it is a span, not a measure."""
    svg = render_board_to_svg(board(1200, prose_row(passage("SHORT", 90)), cards(3)))
    assert_on(column_starts(svg, ["SHORT"])["SHORT"], card_edges(svg, 3)[:1])


def test_thirds_hold_above_a_two_up_row() -> None:
    svg = render_board_to_svg(
        board(1200, prose_row(passage("READOUT", 1400)), cards(2), cards(3))
    )
    assert_on(column_starts(svg, ["READOUT"])["READOUT"], card_edges(svg, 3))


def test_halves_when_a_third_is_under_the_floor() -> None:
    """At 960px a third holds fewer than 45 characters."""
    svg = render_board_to_svg(
        board(960, prose_row(passage("READOUT", 1400)), cards(2), cards(3))
    )
    assert_on(column_starts(svg, ["READOUT"])["READOUT"], card_edges(svg, 2))


def test_one_up_when_a_half_is_under_the_floor() -> None:
    """At 600px a half holds fewer than 45 characters: one whole-slot span."""
    svg = render_board_to_svg(board(600, prose_row(passage("READOUT", 1400)), cards(1)))
    starts = column_starts(svg, ["READOUT"])["READOUT"]
    assert_on(starts, card_edges(svg, 1))


def test_prose_above_a_root_cols_layout_meets_its_cards() -> None:
    """A root `cols:` row gets the theme's cols gap; the prose follows it."""
    text = "\n".join("  " + line for line in passage("ROOT", 1400).split("\n"))
    svg = render_board_to_svg(
        f"width: 1200\n{QUERY}text: |\n{text}\ncols:\n  - a\n  - b\n  - c\n"
    )
    assert_on(column_starts(svg, ["ROOT"])["ROOT"], card_edges(svg, 3))


def test_a_list_heavy_run_switches_the_whole_run_to_halves() -> None:
    """The list picks the grid for every block stacked with it."""
    items = "\n".join(f"- {item}" for item in LIST_ITEMS)
    svg = render_board_to_svg(
        board(
            1200,
            prose_row(f"LIST\n\n{items}"),
            prose_row(passage("PASSAGE", 1400)),
            cards(2),
            cards(3),
        )
    )
    starts = column_starts(svg, ["LIST", "PASSAGE"])
    halves = card_edges(svg, 2)
    assert starts["LIST"][0] == pytest.approx(halves[0], abs=0.5)
    assert_on(starts["PASSAGE"], halves)


def test_a_card_row_ends_the_run() -> None:
    """A list above a card row does not pull the prose below it onto halves."""
    items = "\n".join(f"- {item}" for item in LIST_ITEMS)
    svg = render_board_to_svg(
        board(
            1200,
            prose_row(f"LIST\n\n{items}"),
            cards(3),
            prose_row(passage("PASSAGE", 1400)),
            cards(3),
        )
    )
    assert_on(column_starts(svg, ["LIST", "PASSAGE"])["PASSAGE"], card_edges(svg, 3))


def test_prose_in_a_two_thirds_card_takes_two_columns_on_the_thirds() -> None:
    card_text = "\n".join(
        "          " + line for line in passage("CARD", 1000).split("\n")
    )
    svg = render_board_to_svg(
        f"width: 1200\n{QUERY}rows:\n"
        "  - cols:\n"
        "      - width: 66.6667%\n"
        f"        text: |\n{card_text}\n"
        "      - c\n" + cards(3)
    )
    assert_on(column_starts(svg, ["CARD"])["CARD"], card_edges(svg, 3)[:2])


def test_prose_in_a_half_card_is_one_column() -> None:
    card_text = "\n".join(
        "          " + line for line in passage("CARD", 1000).split("\n")
    )
    svg = render_board_to_svg(
        f"width: 1200\n{QUERY}rows:\n"
        "  - cols:\n"
        f"      - text: |\n{card_text}\n"
        "      - c\n"
    )
    assert_on(column_starts(svg, ["CARD"])["CARD"], card_edges(svg, 2)[:1])


def test_edges_follow_the_card_gap_and_padding_the_theme_sets() -> None:
    """Prose boxes come from the card gap and card_padding, nothing else."""
    svg = render_board_to_svg(
        board(
            1200,
            prose_row(passage("READOUT", 1400)),
            cards(3),
            style="style:\n  gap: 40\n  frame:\n    card_padding: 24\n",
        )
    )
    assert_on(column_starts(svg, ["READOUT"])["READOUT"], card_edges(svg, 3))


def test_one_plan_serves_sizing_and_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    """The grid is decided once per run, and both passes read it."""
    calls: list[int] = []
    real = prose._run_grid

    def counting(*args: object) -> int:
        calls.append(1)
        return real(*args)

    monkeypatch.setattr(prose, "_run_grid", counting)
    yaml = board(
        1200,
        prose_row(passage("ONE", 600)),
        prose_row(passage("TWO", 1400)),
        cards(3),
    )
    render_board_to_svg(yaml)
    assert len(calls) == 1

    result = compile(yaml)
    resolved = build_resolved_board_static(result.board)
    for item, source in zip(
        resolved.layout.items, result.board.layout.items, strict=True
    ):
        assert item.board.prose_plan is source.board.prose_plan


def test_a_board_replanned_at_another_width_follows_the_new_width(
    tmp_path: Path,
) -> None:
    result = compile(board(1200, prose_row(passage("ONE", 1400)), cards(3)))
    nested = result.board.layout.items[0].board
    prose.plan_board_prose(result.board, {}, 1200 - 2 * 36.0)
    assert nested.prose_plan.grid == 3
    prose.plan_board_prose(result.board, {}, 960 - 2 * 36.0)
    assert nested.prose_plan.grid == 2


def _text_block(marker: str, chars: int) -> str:
    return "\n".join("  " + line for line in passage(marker, chars).split("\n"))


def test_prose_above_a_root_grid_layout_meets_its_cards() -> None:
    """A root `grid:` pitches its cards with the grid gap; the prose follows it."""
    svg = render_board_to_svg(
        f"width: 1200\n{QUERY}text: |\n{_text_block('ROOT', 1400)}\n"
        "grid:\n  columns: 3\n  items:\n    - item: a\n    - item: b\n    - item: c\n"
    )
    assert_on(column_starts(svg, ["ROOT"])["ROOT"], card_edges(svg, 3))


def test_a_two_thirds_text_card_in_a_root_cols_row_takes_two_columns() -> None:
    card_text = "\n".join(
        "        " + line for line in passage("CARD", 1000).split("\n")
    )
    svg = render_board_to_svg(
        f"width: 1200\n{QUERY}cols:\n"
        f"  - width: 66.6667%\n    text: |\n{card_text}\n"
        "  - c\n"
    )
    starts = column_starts(svg, ["CARD"])["CARD"]
    assert len(starts) == 2
    assert starts[1] - starts[0] > 300


def test_prose_above_a_nested_row_with_its_own_gap_meets_its_cards() -> None:
    svg = render_board_to_svg(
        f"width: 1200\n{QUERY}rows:\n"
        + prose_row(passage("READOUT", 1400))
        + "  - style:\n      gap: 60\n    cols:\n      - a\n      - b\n      - c\n"
    )
    assert_on(column_starts(svg, ["READOUT"])["READOUT"], card_edges(svg, 3))


def test_prose_above_a_root_tabs_board_follows_the_panel_gap() -> None:
    """Tab panels are full width; the cards inside place with the inherited style.gap."""
    svg = render_board_to_svg(
        f"width: 1200\n{QUERY}style:\n  gap: 40\ntext: |\n{_text_block('ROOT', 1400)}\n"
        "tabs:\n  items:\n    - title: One\n      cols:\n        - a\n        - b\n        - c\n"
    )
    # 600 is the centered tab label, not prose
    starts = [x for x in column_starts(svg, ["ROOT"])["ROOT"] if round(x) != 600]
    assert_on(starts, card_edges(svg, 3))


def test_prose_after_a_card_row_follows_that_rows_gap() -> None:
    svg = render_board_to_svg(
        f"width: 1200\n{QUERY}rows:\n"
        "  - style:\n      gap: 60\n    cols:\n      - a\n      - b\n      - c\n"
        + prose_row(passage("READOUT", 1400))
    )
    # the board footer sits below the prose and is not part of it
    starts = [x for x in column_starts(svg, ["READOUT"])["READOUT"] if 50 <= x <= 1000]
    assert_on(starts, card_edges(svg, 3))


def test_a_sibling_cards_row_gap_does_not_leak_into_the_next_card() -> None:
    """Card 2's prose sits on the row holding it, not on card 1's inner row."""
    yaml = (
        f"width: 1200\n{QUERY}style:\n  gap: 0\ncols:\n"
        "  - width: 33.33%\n    rows:\n      - style:\n          gap: 100\n"
        "        cols:\n          - a\n          - b\n"
        "  - width: 66.67%\n    rows:\n"
        f"      - text: |\n          {passage('CARD', 1400)}\n"
        "      - c\n"
    )
    result = compile(yaml)
    prose.plan_board_prose(result.board, {}, 1200 - 2 * 36.0)
    card = result.board.layout.items[1].board.layout.items[0].board
    assert card.prose_plan.grid == 3
    assert card.prose_plan.gap == 20.0
    starts = column_starts(render_board_to_svg(yaml), ["CARD"])["CARD"]
    assert len(starts) == 2


@pytest.mark.parametrize("width", [1205, 1400])
def test_three_columns_hold_at_widths_not_divisible_by_three(width: int) -> None:
    svg = render_board_to_svg(
        board(width, prose_row(passage("READOUT", 1400)), cards(3))
    )
    assert_on(column_starts(svg, ["READOUT"])["READOUT"], card_edges(svg, 3))


def test_prose_above_tabs_follows_the_panel_that_places_the_cards() -> None:
    svg = render_board_to_svg(
        f"width: 1200\n{QUERY}text: |\n{_text_block('ROOT', 1400)}\n"
        "tabs:\n  items:\n    - title: One\n      style:\n        gap: 60\n"
        "      cols:\n        - a\n        - b\n        - c\n"
    )
    starts = [x for x in column_starts(svg, ["ROOT"])["ROOT"] if round(x) != 600]
    assert_on(starts, card_edges(svg, 3))


def test_prose_above_tabs_follows_the_active_panel() -> None:
    """Two panels with different gaps: the default one places the cards."""
    svg = render_board_to_svg(
        f"width: 1200\n{QUERY}text: |\n{_text_block('ROOT', 1400)}\n"
        "tabs:\n  default: Second\n  items:\n"
        "    - title: First\n      style:\n        gap: 10\n"
        "      cols:\n        - a\n        - b\n        - c\n"
        "    - title: Second\n      style:\n        gap: 60\n"
        "      cols:\n        - a\n        - b\n        - c\n"
    )
    lines = text_lines(svg)
    tab_bar = min(y for _, y, t in lines if t == "First")
    starts = sorted({round(x, 1) for x, y, _ in lines if y < tab_bar - 1})
    assert_on(starts, card_edges(svg, 3))
