"""Prose and board titles ask the board for width, the way charts do.

A board without an exact ``frame.width`` hugs what its items prefer: a prose
block its one-line width capped at ``style.text.preferred_width``, a title its
one-line width, bounded by the board's ``max_width``.
"""

from __future__ import annotations

import re
from pathlib import Path

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.sizing import board_container_width, get_board_gap

from .._svg_render import render_board_file

_LONG = " ".join(["Pipeline coverage held steady across every region."] * 12)
_SHORT = "Updated nightly."
_KPI = """
queries:
  q:
    type: values
    rows:
      - {k: a, v: 1}
charts:
  c1: {query: q, type: kpi, value: v}
"""


def _width(body: str, *, title: str = "T", style: str = "") -> float:
    result = compile_board(f"title: {title}\n{style}{_KPI}{body}")
    assert result.success, result.diagnostics
    return board_container_width(result.board, result.board.variable_defaults)


def _chrome(style: str = "") -> tuple[float, float, float]:
    """The root's ``(margin, card_padding, max_width)``."""
    result = compile_board(f"title: T\n{style}{_KPI}rows:\n  - c1\n")
    frame = result.board.resolved_style.frame
    return float(frame.margin), float(frame.card_padding), float(frame.max_width)


def _prose_cap(style: str = "") -> float:
    result = compile_board(f"title: T\n{style}{_KPI}rows:\n  - c1\n")
    return float(result.board.resolved_style.text.preferred_width)


def test_a_paragraph_asks_for_the_theme_cap_not_the_board_max() -> None:
    margin, pad, max_width = _chrome()
    width = _width(f"rows:\n  - text: {_LONG}\n")
    assert width == _prose_cap() + 2 * pad + 2 * margin < max_width


def test_a_short_caption_asks_only_for_its_own_width() -> None:
    caption = _width(f"rows:\n  - text: {_SHORT}\n")
    paragraph = _width(f"rows:\n  - text: {_LONG}\n")
    assert caption < paragraph


def test_the_cap_is_a_theme_setting() -> None:
    style = "style:\n  text:\n    preferred_width: 300\n"
    margin, pad, _ = _chrome(style)
    assert _width(f"rows:\n  - text: {_LONG}\n", style=style) == (
        300 + 2 * pad + 2 * margin
    )


def test_cols_of_prose_sum_their_widths_and_the_gap() -> None:
    style = "style:\n  text:\n    preferred_width: 300\n"
    margin, _, _ = _chrome(style)
    result = compile_board(
        f"title: T\n{style}{_KPI}cols:\n  - text: {_LONG}\n  - text: {_LONG}\n"
    )
    single = _width(f"rows:\n  - text: {_LONG}\n", style=style) - 2 * margin
    gap = get_board_gap(result.board)
    assert board_container_width(result.board, {}) == 2 * single + gap + 2 * margin


def test_a_long_title_widens_a_board_of_one_small_chart() -> None:
    long_title = "Quarterly pipeline coverage by region and segment"
    assert _width("rows:\n  - c1\n", title=long_title) > _width("rows:\n  - c1\n")


def test_a_title_is_bounded_by_the_board_max_width() -> None:
    _, _, max_width = _chrome()
    assert _width("rows:\n  - c1\n", title=f'"{_LONG}"') == max_width


def test_a_title_only_board_hugs_its_title() -> None:
    result = compile_board("title: Quarterly pipeline coverage\nrows:\n  - text: ' '\n")
    assert result.success, result.diagnostics
    _, _, max_width = _chrome()
    assert board_container_width(result.board, {}) < max_width


def test_an_exact_frame_width_beats_title_and_prose() -> None:
    style = "style:\n  frame:\n    width: 500\n"
    assert _width(f"rows:\n  - text: {_LONG}\n", title=f'"{_LONG}"', style=style) == 500


def test_an_authored_item_width_beats_the_prose_preference() -> None:
    margin, _, _ = _chrome()
    body = f"rows:\n  - text: {_LONG}\n    width: 250\n"
    no_footer = (
        "style:\n  footer:\n    visible: false\n  timestamp:\n    visible: false\n"
    )
    assert _width(body, style=no_footer) == 250 + 2 * margin


def test_a_hugged_prose_block_never_wraps_inside_its_own_width(tmp_path: Path) -> None:
    """A single long word fills the hugged width exactly and must stay on one line."""
    board = tmp_path / "charts" / "word.yml"
    board.parent.mkdir()
    board.write_text("title: Marker\ntext: MARKERFULLRENDER\n")
    svg = render_board_file(board)
    assert "MARKERFULLRENDER</text>" in svg


_WIDE_ROW = """
queries:
  q:
    type: values
    rows:
      - {k: a, v: 1}
charts:
  c1:
    query: q
    type: kpi
    value: v
    style:
      preferred_width: 500
  c2:
    query: q
    type: kpi
    value: v
    style:
      preferred_width: 500
"""


def _render(tmp_path: Path, body: str) -> str:
    board = tmp_path / "charts" / "wide.yml"
    board.parent.mkdir()
    board.write_text(f"title: Wide\n{_WIDE_ROW}{body}")
    svg = render_board_file(board)
    assert isinstance(svg, str)
    return svg


def _lines(svg: str) -> list[str]:
    """The text of each drawn ``<text>`` element, one per rendered line."""
    return [
        "".join(re.findall(r">([^<]+)", element))
        for element in re.findall(r"<text\b.*?</text>", svg, flags=re.S)
    ]


def test_a_sentence_that_fits_the_content_width_stays_on_one_line(
    tmp_path: Path,
) -> None:
    """A wide board must not span a short paragraph into a narrow column."""
    svg = _render(
        tmp_path,
        "rows:\n"
        "  - text: This report shows performance across all product categories.\n"
        "  - cols:\n    - c1\n    - c2\n",
    )
    assert "This report shows performance across all product categories." in _lines(svg)


def test_a_prose_heading_and_nested_title_never_wrap_when_the_board_is_wider(
    tmp_path: Path,
) -> None:
    svg = _render(
        tmp_path,
        "rows:\n"
        "  - text: |\n      # First-Half Performance Report\n      Short intro.\n"
        "  - title: Quarterly Performance Overview Across Regions\n"
        "    text: Short intro.\n"
        "  - cols:\n    - c1\n    - c2\n",
    )
    lines = _lines(svg)
    assert "First-Half Performance Report" in lines
    assert "Quarterly Performance Overview Across Regions" in lines


_BANNER = '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="24"></svg>'


def _board_width(tmp_path: Path, board_yaml: str) -> float:
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "banner.svg").write_text(_BANNER)
    (charts / "b.yml").write_text(board_yaml)
    svg = render_board_file(charts / "b.yml")
    match = re.search(r'<svg[^>]*\bwidth="([\d.]+)"', svg)
    assert match is not None
    return float(match.group(1))


def test_a_project_image_in_prose_asks_for_its_natural_width(tmp_path: Path) -> None:
    assert _board_width(tmp_path, "text: 'Logo ![b](banner.svg)'\n") > 480


def test_a_cased_title_measures_its_image_not_an_upper_cased_path(
    tmp_path: Path,
) -> None:
    board = (
        "title: '![b](banner.svg) sales'\n"
        "text: ' '\n"
        "style:\n  title:\n    font:\n      case: upper\n"
    )
    assert _board_width(tmp_path, board) > 480


def test_text_that_needs_query_data_asks_for_the_cap() -> None:
    paragraph = _width(f"rows:\n  - text: {_LONG}\n")
    for text in (
        "'{{ queries.q.one.v }}'",
        "\"{{ 3 | format('currency') }}\"",
        "\"{{ [1, 2] | map('format', ',.0f') | join(', ') }}\"",
    ):
        assert _width(f"rows:\n  - text: {text}\n") == paragraph, text


def test_chart_focus_compiles_text_that_passes_format_by_name() -> None:
    result = compile_board(
        f"title: T\nchart_focus: c1\n{_KPI}rows:\n  - c1\n"
        "  - text: \"{{ [1000, 2000] | map('format', ',.0f') | join(', ') }}\"\n"
    )
    assert result.success, result.diagnostics


def test_chart_focus_compiles_text_with_format_in_a_fence() -> None:
    result = compile_board(
        f"title: T\nchart_focus: c1\n{_KPI}rows:\n  - c1\n"
        "  - text: |\n      ```\n      {{ 3 | format('currency') }}\n      ```\n"
    )
    assert result.success, result.diagnostics


def _floor(text: str) -> float:
    from dbt_charts.core.compile.resolve.style.markdown_style import (
        prose_content_widths,
    )

    board = compile_board(f"title: T\n{_KPI}rows:\n  - c1\n").board
    return prose_content_widths(text, board.resolved_style, {}, None)[0]


def test_the_floor_of_data_text_is_its_longest_literal_word() -> None:
    short = _floor("a {{ queries.q.one.v }} b")
    long_word = _floor("a {{ queries.q.one.v }} Supercalifragilisticexpialidocious")
    assert long_word > short


def test_a_jinja_comment_does_not_raise_the_floor() -> None:
    plain = _floor("a {{ queries.q.one.v }} b")
    commented = _floor(
        "a {# Supercalifragilisticexpialidocious #}{{ queries.q.one.v }} b"
    )
    assert commented == plain


def test_a_string_literal_holding_a_closing_brace_pair_is_not_text() -> None:
    plain = _floor("a {{ queries.q.one.v }} b")
    literal = _floor(
        'a {{ queries.q.one.v }}{{ "}}Supercalifragilisticexpialidocious" }} b'
    )
    assert literal == plain


def test_raw_blocks_count_as_text() -> None:
    plain = _floor("a {{ queries.q.one.v }} b")
    raw = _floor(
        "a {{ queries.q.one.v }} {% raw %}Supercalifragilisticexpialidocious{% endraw %}"
    )
    assert raw > plain
