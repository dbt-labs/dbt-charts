"""What a board is called in listings and search."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.parse.board_text import parse_board_text


@pytest.mark.parametrize("is_markdown", [False, True])
def test_a_board_with_no_title_is_named_from_its_file(is_markdown: bool) -> None:
    text = parse_board_text("growth/weekly_revenue", "", is_markdown=is_markdown)
    assert text.title == "Weekly Revenue"


def test_an_authored_title_is_kept_as_typed() -> None:
    text = parse_board_text("x", "title: revenue by SQL dialect\n", is_markdown=False)
    assert text.title == "revenue by SQL dialect"


def test_a_markdown_heading_is_kept_as_typed() -> None:
    text = parse_board_text("x", "# the text-to-SQL notes\n", is_markdown=True)
    assert text.title == "the text-to-SQL notes"
