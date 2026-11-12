"""Unit notation rule 3: a support row declares its unit in its row label and
prints bare cells, with or without a value axis."""

from __future__ import annotations

import re

import pytest

from .._svg_render import render_board_to_svg

_DATA = """
queries:
  q:
    columns: [q, pipeline, deals]
    values:
      - [Q1, 412000, 12]
      - [Q2, 388000, 9]
      - [Q3, 406000, 14]
"""


def _board(*, axis: bool) -> str:
    return f"""
width: 700
{_DATA}
charts:
  c:
    query: q
    type: bar
    x: q
    y: pipeline
    style:
      orientation: vertical
      axis_y:
        labels:
          visible: {str(axis).lower()}
          format: currency
    support_table:
      - source: pipeline
        label: Pipeline
        format: currency
      - source: deals
        label: Deals
rows:
  - c
"""


def _texts(board: str) -> list[str]:
    svg = render_board_to_svg(board)
    return re.findall(r">([^<>]+)</text>", svg)


@pytest.mark.parametrize("axis", [True, False])
def test_support_row_cells_are_bare_and_its_label_names_the_unit(axis: bool) -> None:
    texts = _texts(_board(axis=axis))
    assert "Pipeline, $K" in texts, texts
    assert {"412", "388", "406"} <= set(texts), texts
    assert not {"$412k", "$388k", "$406k", "$412K"} & set(texts), texts
    assert "Deals" in texts, texts


_BIG = """
width: 700
queries:
  q:
    columns: [q, pipeline, deals]
    values:
      - [Q1, 4200000, 9511]
      - [Q2, 1800000, 9691]
      - [Q3, 950000, 10924]
"""


def _big_board(chart: str) -> str:
    return f"""{_BIG}
charts:
  c:
    query: q
    x: q
    y: pipeline
{chart}rows:
  - c
"""


def _support(label: str, source: str, fmt: str) -> str:
    return f"""    type: bar
    style:
      orientation: vertical
    support_table:
      - source: {source}
        label: "{label}"
        format: {fmt}
"""


def test_a_row_label_gets_only_the_unit_it_is_missing() -> None:
    texts = _texts(_big_board(_support("Total $", "pipeline", "currency")))
    assert "Total $, M" in texts, texts  # noqa
    texts = _texts(_big_board(_support("Pipeline, $M", "pipeline", "currency")))
    assert "Pipeline, $M" in texts, texts


def test_a_compacted_count_row_prints_bare_cells_and_names_its_tier() -> None:
    texts = _texts(_big_board(_support("Units", "deals", "compact")))
    assert "Units, K" in texts, texts
    assert "9.5" in texts and "9.7" in texts, texts


def test_a_column_block_header_names_the_unit_and_its_cells_are_bare() -> None:
    chart = """    type: bar
    support_table:
      - source: pipeline
        label: Pipeline
        format: currency
"""
    texts = _texts(_big_board(chart))
    assert "Pipeline, $M" in texts, texts
    assert {"4.2", "1.8"} <= set(texts), texts


def test_a_per_series_row_label_cuts_its_name_never_its_unit() -> None:
    from ..test_format_slot_invariants import (
        BANDS,
        _support_table_per_series_entry,
        _to_yaml,
    )

    board = _support_table_per_series_entry("currency", BANDS["large"])
    board["queries"]["s"]["values"][0][1] = "Electronics and accessories"
    board["queries"]["s"]["values"][0][0] = "2026-01-05"
    board["queries"]["s"]["values"].append(
        ["2026-01-12", "Electronics and accessories", 8000000.0]
    )
    board["charts"]["c"]["type"] = "line"
    del board["charts"]["c"]["support_table"]["entries"][0]["label"]
    texts = _texts(_to_yaml(board))
    assert any(t.endswith("…, $M") for t in texts), texts


def test_a_mixed_tier_row_prints_full_digits_and_names_no_tier() -> None:
    chart = """    type: bar
    style:
      orientation: vertical
    support_table:
      - source: pipeline
        label: Pipeline
        format: currency
"""
    board = _big_board(chart).replace("[Q2, 1800000, 9691]", "[Q2, 120, 9691]")
    texts = _texts(board)
    assert {"4,200,000", "120"} <= set(texts), texts
    assert "Pipeline, $" in texts, texts
