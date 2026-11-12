"""A bare ``category[N]`` indexes the board's own categorical palette.

The ``category`` role's bound palette is the board's palette only while the
board authors none; a board-authored name or inline list wins. A namespaced
``<palette>.<N>`` pin still addresses that named palette.
"""

from __future__ import annotations

import dataclasses

import pytest

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve.chart._palette import _with_color_tokens
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.chart_style import merge_chart_style
from dbt_charts.core.compile.resolve.style.palette import (
    color,
    color_from_theme,
    palette,
)

_INLINE = ["#B5427F", "#112233", "#445566"]

_BOARD = """
extends: clarity
style:
  charts:
    color:
      categorical:
        palette: {palette}
  roles:
    mine: category[2]
  title:
    font:
      color: {token}
queries:
  q:
    type: values
    rows:
      - {{k: a, v: 1, g: Alpha}}
      - {{k: b, v: 2, g: Beta}}
charts:
  c:
    query: q
    type: bar
    x: k
    y: v
    color: g
rows:
  - c
"""


def _compile(token: str, board_palette: object = _INLINE):  # noqa: ANN202
    return compile_board(_BOARD.format(palette=board_palette, token=token))


def _title_color(token: str, board_palette: object = _INLINE) -> str:
    result = _compile(token, board_palette)
    assert result.success and result.board is not None, result.errors
    return str(result.board.resolved_style.title.font.color).lower()


def test_category_token_indexes_an_inline_board_palette() -> None:
    assert _title_color("category[2]") == "#112233"


def test_category_token_indexes_a_named_board_palette() -> None:
    assert _title_color("category[2]", "hero-6") == palette("hero-6")[1].lower()


def test_category_variant_applies_to_the_board_palette_stop() -> None:
    assert _title_color("category[2].dark") != "#112233"
    assert _title_color("category[2].dark") != _title_color("category[2].light")


def test_a_roles_shortcut_to_category_follows_the_board_palette() -> None:
    assert _title_color("mine") == "#112233"


def test_a_palette_list_may_reorder_the_themes_category_colors() -> None:
    reordered = _compile("category[1]", ["category[3]", "category[1]"])
    theme = _compile("category[1]", "editorial-10")
    assert reordered.board is not None and theme.board is not None
    assert (
        reordered.board.chart_style_context.palette[0]
        == theme.board.chart_style_context.palette[2]
    )
    assert (
        reordered.board.chart_style_context.palette[1]
        == theme.board.chart_style_context.palette[0]
    )


def test_a_namespaced_token_still_addresses_its_named_palette() -> None:
    assert _title_color("vivid-10.1") == color("vivid-10.1").lower()


def test_without_a_board_palette_category_follows_the_theme_role() -> None:
    unset = _BOARD.format(palette="x", token="category[2]").replace(
        "  charts:\n    color:\n      categorical:\n        palette: x\n", ""
    )
    result = compile_board(unset)
    assert result.success and result.board is not None, result.errors
    assert str(result.board.resolved_style.title.font.color).lower() == (
        palette("editorial-10")[1].lower()
    )


@pytest.mark.parametrize(
    ("token", "stop"), [("category[0]", "stop 0"), ("category[4]", "stop 4")]
)
def test_out_of_range_names_the_boards_palette_and_its_stops(
    token: str, stop: str
) -> None:
    result = _compile(token)
    assert not result.success
    text = str(result.errors)
    assert "board's categorical palette" in text
    assert "valid stops 1-3" in text
    assert stop in text
    assert "vivid-10" not in text


def test_color_from_theme_indexes_a_supplied_categorical_palette() -> None:
    kwargs = {"palettes": {"category": "vivid-10"}, "categorical_palette": _INLINE}
    assert color_from_theme("category[2]", **kwargs) == "#112233"
    with pytest.raises(ValueError, match="valid stops 1-3"):
        color_from_theme("category[9]", **kwargs)


_NESTED = """
extends: clarity
style:
  charts:
    color:
      categorical:
        palette: ['#B5427F', '#112233']
rows:
  - style:
      title:
        font:
          color: category[2]
    rows:
      - text: hello
"""


def test_a_nested_board_inherits_the_ancestors_palette() -> None:
    result = compile_board(_NESTED)
    assert result.success and result.board is not None, result.errors
    nested = result.board.layout.items[0].board
    assert nested is not None
    assert str(nested.resolved_style.title.font.color).lower() == "#112233"


def _board_context() -> ChartStyleContext:
    base = resolve_chart_style_context(get_theme_style())
    return dataclasses.replace(base, palette=_INLINE)


def test_a_chart_style_patch_token_indexes_the_board_palette() -> None:
    patch = StylePatch.model_validate({"accent": "category[2]"})
    resolved = _with_color_tokens(patch, _board_context())
    assert str(resolved.accent).lower() == "#112233"


_CHART_STYLE = """
extends: clarity
style:
  charts:
    color:
      categorical:
        palette: ['#B5427F', '#112233', '#445566']
  roles:
    mine: {role_target}
queries:
  q:
    type: values
    rows:
      - {{k: a, v: 1}}
charts:
  c:
    query: q
    type: bar
    x: k
    y: v
    style:
      marks:
        bar:
          border:
            color: category[3]
rows:
  - c
"""


def test_a_chart_local_style_token_indexes_the_board_palette() -> None:
    result = compile_board(_CHART_STYLE.format(role_target="category[1]"))
    assert result.success and result.board is not None, result.errors
    board = result.board
    overrides = merge_chart_style(board.chart_style_context, board.charts["c"])
    assert "#445566" in overrides["bar"].model_dump_json().lower()


def test_a_role_shortcut_outside_the_board_palette_is_refused() -> None:
    result = compile_board(_CHART_STYLE.format(role_target="category[4]"))
    assert not result.success
    assert "valid stops 1-3" in str(result.errors)
