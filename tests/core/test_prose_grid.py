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


def prose_starts_without_tab_label(svg: str) -> list[float]:
    """ROOT prose line-starts, minus the tab bar's label ("One"), which falls in its span."""
    label_x = next(x for x, _, t in text_lines(svg) if t == "One")
    return [
        x
        for x in column_starts(svg, ["ROOT"])["ROOT"]
        if x != pytest.approx(label_x, abs=0.5)
    ]


def test_prose_above_a_root_tabs_board_follows_the_panel_gap() -> None:
    """Tab panels are full width; the cards inside place with the inherited style.gap."""
    svg = render_board_to_svg(
        f"width: 1200\n{QUERY}style:\n  gap: 40\ntext: |\n{_text_block('ROOT', 1400)}\n"
        "tabs:\n  items:\n    - title: One\n      cols:\n        - a\n        - b\n        - c\n"
    )
    starts = prose_starts_without_tab_label(svg)
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
    starts = prose_starts_without_tab_label(svg)
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


# --- A lone column stays wide unless some block on the board goes multi-column ---


def block_lines(svg: str, marker: str) -> list[str]:
    """Text lines of the block opening with ``marker``, down to the next card header."""
    lines = text_lines(svg)
    top = min(y for _, y, t in lines if t.startswith(marker))
    end = min([y for _, y, t in lines if t == "Label" and y > top] + [float("inf")])
    return [t for x, y, t in lines if top - 0.5 <= y < end - 0.5 and 50 <= x <= 1000]


def widest(svg: str, marker: str) -> int:
    return max(len(t) for t in block_lines(svg, marker))


def test_short_blocks_alone_stay_wide_and_left_aligned() -> None:
    """No block goes multi-column, so none is squeezed into a third."""
    svg = render_board_to_svg(
        board(
            1200,
            prose_row(passage("ONE", 200)),
            prose_row(passage("TWO", 200)),
            cards(3),
        )
    )
    starts = column_starts(svg, ["ONE", "TWO"])
    assert_on(starts["ONE"], card_edges(svg, 3)[:1])
    assert starts["TWO"] == starts["ONE"]
    assert widest(svg, "ONE") > 55 and widest(svg, "TWO") > 55


def test_a_multi_column_block_snaps_the_short_blocks_to_a_span() -> None:
    svg = render_board_to_svg(
        board(
            1200,
            prose_row(passage("ONE", 200)),
            prose_row(passage("BIG", 1400)),
            cards(3),
        )
    )
    starts = column_starts(svg, ["ONE", "BIG"])
    assert len(starts["BIG"]) == 3
    assert_on(starts["ONE"], card_edges(svg, 3)[:1])
    assert widest(svg, "ONE") <= 55


def test_a_multi_column_block_in_a_narrow_card_counts() -> None:
    card_text = "\n".join(
        "          " + line for line in passage("CARD", 1400).split("\n")
    )
    svg = render_board_to_svg(
        f"width: 1200\n{QUERY}rows:\n"
        + prose_row(passage("ONE", 200))
        + "  - cols:\n      - width: 66.6667%\n"
        f"        text: |\n{card_text}\n      - c\n"
    )
    assert len(column_starts(svg, ["ONE", "CARD"])["CARD"]) == 2
    assert widest(svg, "ONE") <= 55


def test_a_block_that_is_multi_column_only_at_full_width_does_not_count() -> None:
    """In its real 25% slot the block is one column, so the short row stays wide."""
    card_text = "\n".join(
        "          " + line for line in passage("CARD", 500).split("\n")
    )
    svg = render_board_to_svg(
        f"width: 1200\n{QUERY}rows:\n"
        + prose_row(passage("ONE", 200))
        + "  - cols:\n      - width: 25%\n"
        f"        text: |\n{card_text}\n      - c\n"
    )
    assert len(column_starts(svg, ["CARD"])["CARD"]) == 1
    assert widest(svg, "ONE") > 55


def _tabs_board(default: str) -> str:
    short = "\n".join("            " + t for t in passage("TABA", 200).split("\n"))
    big = "\n".join("            " + t for t in passage("TABB", 1400).split("\n"))
    return (
        f"width: 1200\n{QUERY}rows:\n"
        + prose_row(passage("ONE", 200))
        + f"  - tabs:\n      default: {default}\n      items:\n"
        f"        - title: First\n          text: |\n{short}\n"
        f"        - title: Second\n          text: |\n{big}\n"
    )


def test_multi_column_text_in_an_inactive_tab_does_not_snap_the_board() -> None:
    svg = render_board_to_svg(_tabs_board("First"))
    assert widest(svg, "ONE") > 55


def test_multi_column_text_in_the_active_tab_snaps_the_board() -> None:
    svg = render_board_to_svg(_tabs_board("Second"))
    assert widest(svg, "ONE") <= 55


def _conditional_board(head: str, *, probe: bool = False) -> str:
    """A short intro row, then a row with ``head`` holding a 1400-char passage."""
    big = "\n".join("      " + t for t in passage("BIG", 1400).split("\n"))
    queries = QUERY.replace(
        "queries:\n", "queries:\n  flags:\n    rows:\n      - {show: true}\n", 1
    )
    return (
        f"width: 1200\n{queries if probe else QUERY}rows:\n"
        + prose_row(passage("ONE", 200))
        + f"  - {head}\n    text: |\n{big}\n"
    )


def test_a_hidden_block_does_not_snap_the_board() -> None:
    svg = render_board_to_svg(_conditional_board("visible: false"))
    assert widest(svg, "ONE") > 55


def test_a_visible_block_does_snap_the_board() -> None:
    svg = render_board_to_svg(_conditional_board("visible: true"))
    assert widest(svg, "ONE") <= 55


def test_collapsed_details_content_does_not_snap_the_board() -> None:
    svg = render_board_to_svg(_conditional_board('details: "Show more"'))
    assert widest(svg, "ONE") > 55


def test_expanded_details_content_snaps_the_board() -> None:
    head = "details:\n      summary: Show more\n      expanded: true"
    svg = render_board_to_svg(_conditional_board(head))
    assert widest(svg, "ONE") <= 55


def test_a_query_backed_visible_counts_as_drawn() -> None:
    """The data is unknown when the plan is made, so the block may be drawn."""
    head = "visible:\n      query: flags\n      column: show"
    svg = render_board_to_svg(_conditional_board(head, probe=True))
    assert widest(svg, "ONE") <= 55


def lines_from(svg: str, marker: str) -> list[tuple[float, float, str]]:
    """Text lines from ``marker``'s line down to the card row, in reading order."""
    lines = text_lines(svg)
    top = min(y for _, y, t in lines if t.startswith(marker))
    bottom = min((y for _, y, t in lines if t == "Label" and y > top), default=1e9)
    return sorted(
        (line for line in lines if top - 0.5 <= line[1] < bottom - 0.5),
        key=lambda c: (c[1], c[0]),
    )


def columns_of(lines: list[tuple[float, float, str]]) -> dict[float, list[float]]:
    """Baselines of each column, keyed by its left edge."""
    columns: dict[float, list[float]] = defaultdict(list)
    for x, y, _ in lines:
        columns[round(x, 1)].append(y)
    return dict(columns)


class TestAHeadingTitlesItsColumns:
    """A heading that opens a block sits across its columns, not inside the first."""

    def test_columns_start_level_under_the_heading(self) -> None:
        marker = "TITLE"
        svg = render_board_to_svg(
            board(1200, prose_row(f"## {marker}\n\n" + passage("BODY", 1400)), cards(3))
        )
        heading_y = next(y for _, y, t in text_lines(svg) if t == marker)
        columns = columns_of(
            [line for line in lines_from(svg, marker) if line[1] > heading_y + 0.5]
        )
        assert len(columns) == 3
        tops = [min(ys) for ys in columns.values()]
        assert max(tops) - min(tops) == pytest.approx(0.0, abs=0.5)
        assert min(tops) > heading_y

    def test_the_columns_end_within_a_line_of_each_other(self) -> None:
        """The heading's height is out of the flow, so it cannot unbalance column one."""
        marker = "TITLE"
        svg = render_board_to_svg(
            board(1200, prose_row(f"## {marker}\n\n" + passage("BODY", 1400)), cards(3))
        )
        heading_y = next(y for _, y, t in text_lines(svg) if t == marker)
        columns = columns_of(
            [line for line in lines_from(svg, marker) if line[1] > heading_y + 0.5]
        )
        counts = [len(ys) for ys in columns.values()]
        assert max(counts) - min(counts) <= 1

    def test_a_heading_that_only_wraps_in_a_column_reserves_one_line(self) -> None:
        """A heading is measured across the columns it is painted across."""
        title = "HEADING wide enough to wrap inside one column of the three"
        svg = render_board_to_svg(
            board(1200, prose_row(f"## {title}\n\n" + passage("BODY", 1400)), cards(3))
        )
        lines = text_lines(svg)
        heading = [y for _, y, t in lines if t.startswith("HEADING")]
        body = min(y for _, y, t in lines if t.startswith("BODY"))
        assert len({y for _, y, t in lines if y < body and y >= heading[0]}) == 1
        assert body - heading[0] == pytest.approx(2 * 19.6)

    def test_a_single_column_keeps_its_heading_on_the_column(self) -> None:
        marker = "TITLE"
        svg = render_board_to_svg(
            board(1200, prose_row(f"## {marker}\n\n" + passage("BODY", 90)), cards(3))
        )
        assert list(columns_of(lines_from(svg, marker))) == [
            pytest.approx(card_edges(svg, 3)[0], abs=0.5)
        ]


class TestAListFlowsLikeProse:
    """A list earns columns by its lines and breaks between items."""

    @staticmethod
    def long_items(count: int) -> str:
        return "\n".join(f"- {passage(f'ITEM{i}', 260)}" for i in range(count))

    def test_a_long_list_earns_columns(self) -> None:
        svg = render_board_to_svg(board(1200, prose_row(self.long_items(6)), cards(3)))
        starts = sorted({x for x, _, t in text_lines(svg) if t.startswith("ITEM")})
        assert len(starts) >= 2

    def test_no_item_is_cut_across_a_column(self) -> None:
        svg = render_board_to_svg(board(1200, prose_row(self.long_items(6)), cards(3)))
        lines = lines_from(svg, "ITEM0")
        openers = {x for x, _, t in lines if t.startswith("ITEM")}
        by_item = sorted(
            (x, y) for x, y, t in lines if t.startswith("ITEM") and x in openers
        )
        for x in openers:
            top = min(y for ix, y in by_item if ix == x)
            first_line_of_column = next(t for lx, y, t in lines if lx == x and y == top)
            assert first_line_of_column.startswith("ITEM")

    def test_numbering_continues_across_columns(self) -> None:
        items = "\n".join(f"{i + 1}. {passage(f'ITEM{i}', 260)}" for i in range(6))
        svg = render_board_to_svg(board(1200, prose_row(items), cards(3)))
        numbers = {t for _, _, t in text_lines(svg) if t.rstrip(".").isdigit()}
        assert {f"{i + 1}." for i in range(6)} <= numbers

    def test_a_short_list_stays_one_column(self) -> None:
        items = "\n".join(f"- item {i}" for i in range(3))
        svg = render_board_to_svg(
            board(
                1200,
                prose_row(passage("READOUT", 1400)),
                prose_row(items),
                cards(3),
            )
        )
        assert len({x for x, _, t in text_lines(svg) if t.startswith("item")}) == 1


class TestATableStaysOutOfTheMeasure:
    """A table takes its own width; the prose around it keeps the run's."""

    TABLE = (
        "| Column one holds a long label | Column two holds a long label | "
        "Column three holds a long label | Column four holds a long label |\n"
        "|---|---|---|---|\n"
        "| a value that is reasonably long | a value that is reasonably long | "
        "a value that is reasonably long | a value that is reasonably long |"
    )

    TWO_SPAN_TABLE = (
        "| Column one holds a long label | Column two holds a long label | "
        "Column three holds a label |\n"
        "|---|---|---|\n"
        "| a value | a value | a value |"
    )

    def test_a_table_does_not_change_how_the_prose_beside_it_wraps(self) -> None:
        """The paragraph wraps the same lines with or without the table in its block."""
        text = passage("AROUND", 300)
        wide = passage("READOUT", 1400)
        alone = render_board_to_svg(
            board(1200, prose_row(wide), prose_row(text), cards(3))
        )
        beside = render_board_to_svg(
            board(1200, prose_row(wide), prose_row(f"{text}\n\n{self.TABLE}"), cards(3))
        )

        def wrapped(svg: str) -> list[str]:
            lines = text_lines(svg)
            top = min(y for _, y, t in lines if t.startswith("AROUND"))
            return [t for _, y, t in sorted(lines, key=lambda c: c[1]) if y >= top][:5]

        assert wrapped(beside) == wrapped(alone)

    def test_long_prose_over_a_table_flows_in_columns(self) -> None:
        svg = render_board_to_svg(
            board(
                1200,
                prose_row(f"{passage('ABOVE', 1000)}\n\n{self.TABLE}"),
                cards(3),
            )
        )
        lines = text_lines(svg)
        table_top = min(y for _, y, t in lines if t.startswith("Column one"))
        above = {round(x, 1) for x, y, _ in lines if y < table_top - 0.5}
        assert len(above) >= 2

    def test_a_table_that_fits_a_column_flows_with_the_prose(self) -> None:
        """Only a table wider than a column has a reason to leave the flow."""
        small = "| Region | Units |\n|---|---|\n| West | 812 |\n| East | 654 |"
        svg = render_board_to_svg(
            board(
                1200,
                prose_row(
                    f"{passage('ABOVE', 300)}\n\n{small}\n\n{passage('BELOW', 500)}"
                ),
                cards(3),
            )
        )
        lines = text_lines(svg)
        above = min(x for x, _, t in lines if t.startswith("ABOVE"))
        below = min(x for x, _, t in lines if t.startswith("BELOW"))
        assert below > above

    def test_a_centered_table_centers_on_the_card_a_lone_column_widened_to(
        self,
    ) -> None:
        svg = render_board_to_svg(
            board(
                1200,
                prose_row(f"{passage('ABOVE', 90)}\n\n{self.TWO_SPAN_TABLE}"),
                cards(3),
                style="style:\n  text:\n    align: center\n",
            )
        )
        left = min(x for x, _, t in text_lines(svg) if t.startswith("Column one"))
        assert left > card_edges(svg, 3)[0] + 16

    def test_prose_after_a_table_starts_below_it(self) -> None:
        svg = render_board_to_svg(
            board(
                1200,
                prose_row(
                    f"{passage('ABOVE', 1000)}\n\n{self.TABLE}\n\n"
                    f"{passage('BELOW', 200)}"
                ),
                cards(3),
            )
        )
        lines = text_lines(svg)
        cell = max(y for _, y, t in lines if t.startswith("a value"))
        below = min(y for _, y, t in lines if t.startswith("BELOW"))
        assert below > cell


class TestColumnsShareAFirstBaseline:
    """The first line of every column in a stretch sits on one baseline."""

    @staticmethod
    def first_line(svg: str, marker: str) -> tuple[float, float]:
        x, y, _ = next(line for line in text_lines(svg) if marker in line[2])
        return x, y

    def test_sections_opening_columns_line_up_from_the_first(self) -> None:
        text = "\n\n".join(
            f"## {name}\n\n{passage(name.upper(), 700)}"
            for name in ("Alpha", "Beta", "Gamma")
        )
        svg = render_board_to_svg(board(1200, prose_row(text), cards(3)))

        heads = [self.first_line(svg, name) for name in ("Alpha", "Beta", "Gamma")]
        bodies = [self.first_line(svg, m) for m in ("ALPHA", "BETA", "GAMMA")]
        assert len({x for x, _ in heads}) == 3, "each section opens its own column"
        assert len({y for _, y in heads}) == 1
        assert len({y for _, y in bodies}) == 1
        assert bodies[0][1] > heads[0][1], "each heading stays above its text"

    def test_a_heading_opening_a_column_meets_the_paragraph_beside_it(self) -> None:
        text = "\n\n".join(
            (
                passage("INTRO", 700),
                f"## Beta\n\n{passage('BETA', 700)}",
                f"## Gamma\n\n{passage('GAMMA', 700)}",
            )
        )
        svg = render_board_to_svg(board(1200, prose_row(text), cards(3)))

        intro, heading = self.first_line(svg, "INTRO"), self.first_line(svg, "Beta")
        assert heading[0] > intro[0], "the heading opens the next column"
        assert heading[1] == pytest.approx(intro[1], abs=1)

    def test_a_lone_opening_heading_still_titles_the_columns(self) -> None:
        text = f"## Alpha\n\n{passage('ALPHA', 1400)}"
        svg = render_board_to_svg(board(1200, prose_row(text), cards(3)))

        heading = self.first_line(svg, "Alpha")
        beside = [y for x, y, _ in text_lines(svg) if x > heading[0]]
        assert min(beside) > heading[1] + 10, "every column opens below the heading"


class TestAWideTableKeepsItsSurroundings:
    TABLE = TestATableStaysOutOfTheMeasure.TABLE

    def test_a_heading_stays_with_the_table_it_heads(self) -> None:
        text = f"{passage('LONG', 1400)}\n\n## Results\n\n{self.TABLE}"
        svg = render_board_to_svg(board(1200, prose_row(text), cards(3)))

        lines = text_lines(svg)
        long_x = min(x for x, _, t in lines if t.startswith("LONG"))
        x, y, _ = next(line for line in lines if "Results" in line[2])
        table_y = min(y_ for _, y_, t in lines if "Column one" in t)
        prose_bottom = max(
            y_
            for _, y_, t in lines
            if not any(w in t for w in ("Results", "Column")) and y_ < table_y
        )
        assert x == long_x, "the heading sits at the table's left edge"
        assert prose_bottom < y < table_y

    def test_a_heading_ending_a_block_stays_with_the_next_row(self) -> None:
        text = f"{passage('LONG', 1400)}\n\n## Results"
        svg = render_board_to_svg(
            board(1200, prose_row(text), prose_row(self.TABLE), cards(3))
        )

        lines = text_lines(svg)
        long_x = min(x for x, _, t in lines if t.startswith("LONG"))
        x, y, _ = next(line for line in lines if "Results" in line[2])
        table_y = min(y_ for _, y_, t in lines if "Column one" in t)
        assert x == long_x
        assert max(y_ for _, y_, t in lines if y_ < y) < y < table_y

    @pytest.mark.parametrize("chars", [200, 1400])
    def test_the_gap_above_a_table_ignores_how_the_text_split(self, chars: int) -> None:
        def gap(length: int) -> float:
            text = f"{passage('LEAD', length)}\n\n{self.TABLE}"
            lines = text_lines(
                render_board_to_svg(board(1200, prose_row(text), cards(3)))
            )
            table_y = min(y for _, y, t in lines if "Column one" in t)
            return table_y - max(y for _, y, _ in lines if y < table_y)

        assert gap(chars) == pytest.approx(gap(300), abs=1.5)


RULE_COLOR = "#123456"


def rule_width(style: str = "") -> float:
    """Width of the one rule a lone ``---`` block draws, in a three-up board."""
    divider = f"style:\n  text:\n    divider:\n      color: '{RULE_COLOR}'\n"
    svg = render_board_to_svg(
        board(1200, prose_row("---"), cards(3), style=divider + style)
    )
    rules = re.findall(rf'<rect [^>]*width="([\d.]+)"[^>]*fill="{RULE_COLOR}"', svg)
    assert len(rules) == 1, rules
    return float(rules[0])


def test_a_lone_rule_spans_the_whole_card() -> None:
    edges = card_edges(
        render_board_to_svg(board(1200, prose_row("SHORT"), cards(3))), 3
    )
    assert rule_width() > 2.5 * (edges[1] - edges[0])


def test_a_lone_rule_keeps_to_an_authored_max_chars() -> None:
    wide = rule_width()
    capped = rule_width("    column:\n      max_chars: 30\n")
    assert 0 < capped < wide / 2
    assert rule_width("    column:\n      max_chars: 9999\n") == wide
