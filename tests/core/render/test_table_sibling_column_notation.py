"""Sibling numeric columns of one table share a notation."""

from __future__ import annotations

import re
from pathlib import Path

from .._svg_render import render_board_to_svg

# `low` straddles 1,000 (its vote declines to compact); `high` never drops under it.
_BOARD = """
title: Sibling notation
width: 480
queries:
  q:
    columns: [account, low, high, small]
    values:
      - [A, 1266.5, 1440.5, 3.5]
      - [B, 770.5, 1230.5, 5.2]
      - [C, 1898.5, 1320.5, 12.1]
      - [D, 1385.5, 1170.5, 7.4]
charts:
  q:
    query: q
    type: table
    title: ""
    style:
      pagination:
        enabled: false
rows:
  - q
"""


def _cell_texts(tmp_path: Path, board: str) -> list[str]:
    svg = render_board_to_svg(board)
    texts = re.findall(r'data-col="[1-3]"[^>]*>(?:<tspan[^>]*>)?([^<]*)', svg)
    assert texts, "no numeric cells captured"
    return texts


def test_a_column_that_cannot_compact_follows_its_compacting_sibling(
    tmp_path: Path,
) -> None:
    texts = _cell_texts(tmp_path, _BOARD)
    low = texts[0::3]
    assert not [t for t in texts if "," in t], (
        f"exact-digit cells beside compact ones: {texts}"
    )
    assert low[0].startswith("1.27"), f"low should print in thousands: {low}"


def test_plain_digit_siblings_do_not_pull_a_declined_column_off_exact_digits(
    tmp_path: Path,
) -> None:
    board = (
        _BOARD.replace("1440.5", "14.5")
        .replace("1230.5", "12.5")
        .replace("1320.5", "13.5")
        .replace("1170.5", "11.5")
    )
    texts = _cell_texts(tmp_path, board)
    assert texts[0::3][0] == "1,266.5", texts


def test_a_string_year_sibling_does_not_pull_a_declined_column_off_exact_digits(
    tmp_path: Path,
) -> None:
    board = (
        _BOARD.replace(
            "columns: [account, low, high, small]", "columns: [account, low, year]"
        )
        .replace(", 1440.5, 3.5]", ', "2021"]')
        .replace(", 1230.5, 5.2]", ', "2022"]')
        .replace(", 1320.5, 12.1]", ', "2023"]')
        .replace(", 1170.5, 7.4]", ', "2024"]')
    )
    texts = _cell_texts(tmp_path, board)
    assert texts[0::2][0] == "1,266.5", texts


def _full_cells(tmp_path: Path, board: str) -> list[list[str]]:
    """Each numeric column's cells as printed, suffix tspan included, padding dropped."""
    svg = render_board_to_svg(board)
    cells = re.findall(r'data-col="([1-3])"[^>]*>(.*?)</text>', svg)
    return [
        [
            re.sub(r"<[^>]+>|[\u2007\u2008\u200b ]", "", text)
            for col, text in cells
            if col == str(c)
        ]
        for c in (1, 2, 3)
    ]


def test_a_column_straddling_a_thousand_prints_in_one_unit_beside_a_compact_sibling(
    tmp_path: Path,
) -> None:
    board = _BOARD.replace("1266.5", "858.5")
    low = _full_cells(tmp_path, board)[0]
    # Every cell of a shared-scale column is in its unit, and says so.
    assert low == ["0.86K", "0.77K", "1.9K", "1.39K"], low


def test_sibling_columns_print_the_unit_on_every_cell(tmp_path: Path) -> None:
    board = _BOARD.replace("1266.5", "858.5")
    low, high, _ = _full_cells(tmp_path, board)
    assert all(t.endswith("K") for t in low), low
    assert all(t.endswith("K") for t in high), high


def _board_with(rows: list[tuple[float, float]]) -> str:
    values = "\n".join(f"      - [{n}, {a}, {b}, 1]" for n, (a, b) in enumerate(rows))
    head, _, tail = _BOARD.partition("    values:\n")
    return f"{head}    values:\n{values}\ncharts:{tail.partition('charts:')[2]}"


# The shared maximum is what the sibling columns' own data need, so it varies
# with the table; each value still prints only the places it has.
def test_sibling_columns_take_their_precision_from_the_majority_tier(
    tmp_path: Path,
) -> None:
    board = _board_with([(858.5, 1437.5), (1137.5, 1300.5), (1170.5, 1400.5)])
    low, high, _ = _full_cells(tmp_path, board)
    # Two places from the rows at or above 1K; 0.859 prints on that grid.
    assert low == ["0.86K", "1.14K", "1.17K"], low
    assert high == ["1.44K", "1.3K", "1.4K"], high


def test_sibling_columns_share_a_maximum_precision_of_two(tmp_path: Path) -> None:
    board = _board_with([(1500.5, 150000.5), (2314.0, 160250.0), (130000.5, 170000.5)])
    low, high, _ = _full_cells(tmp_path, board)
    # `high` alone needs none (160); `low`'s 1.5 needs two.
    assert low == ["1.5K", "2.31K", "130K"], low
    assert high == ["150K", "160.25K", "170K"], high


def test_a_shared_maximum_precision_of_zero_prints_whole_numbers(
    tmp_path: Path,
) -> None:
    board = _board_with([(120000.5, 150000.5), (125000.5, 160000.5)])
    low, high, _ = _full_cells(tmp_path, board)
    assert low == ["120K", "125K"], low
    assert high == ["150K", "160K"], high
