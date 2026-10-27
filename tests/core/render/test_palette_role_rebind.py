"""A layer or board that rebinds ``style.palettes.<role>`` changes the render.

Role tokens (`category`, `sequence`, ...) resolve against the palettes and
roles maps as they stand after every layer has merged, not against the
theme's own maps.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import pytest

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.compiler import compile_file
from dbt_charts.core.compile.config import get_theme_style, user_facing_theme_names
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.schema_names import PaletteName
from dbt_charts.core.compile.resolve.style.palette import (
    _get_index,
    color_from_theme,
    palette,
)

_BODY = """
queries:
  bars:
    type: values
    rows:
      - {k: a, v: 1, g: Alpha}
      - {k: b, v: 2, g: Beta}
      - {k: c, v: 3, g: Gamma}
  heat:
    type: values
    rows:
      - {x: a, y: p, v: 1}
      - {x: b, y: p, v: 5}
      - {x: a, y: q, v: 9}
      - {x: b, y: q, v: 3}
charts:
  cats:
    query: bars
    type: bar
    x: k
    y: v
    color: g
  grid:
    query: heat
    type: heatmap
    x: x
    y: y
    color: v
rows:
  - cats
  - grid
"""
_REBIND = "style:\n  palettes:\n    category: vivid-10\n    sequence: dbt-seq-teal\n"
_THEME = "extends: clarity\n"


def _board(files: dict[str, str], in_memory_project: Callable) -> Any:
    project = in_memory_project(Path("/tmp/test-palette-rebind"), files)
    result = compile_file(project.path("charts/board.yml").read_board())
    assert result.success and result.board is not None, result.errors
    return result.board


def _title_color(files: dict[str, str], in_memory_project: Callable) -> str:
    board = _board(files, in_memory_project)
    return str(board.resolved_style.title.font.color).lower()


def _chart_context(files: dict[str, str], in_memory_project: Callable) -> Any:
    return _board(files, in_memory_project).chart_style_context


_BOARD_PLAIN = {"charts/board.yml": f"{_THEME}{_BODY}"}
_BOARD_STYLE = {"charts/board.yml": f"{_THEME}{_REBIND}{_BODY}"}
_META_LAYER = {
    "charts/meta.yml": f"{_THEME}{_REBIND}",
    "charts/board.yml": _BODY,
}
_EXTENDS_LAYER = {
    "charts/_brand.yml": f"{_THEME}{_REBIND}",
    "charts/board.yml": f"extends: ./_brand.yml\n{_BODY}",
}


@pytest.mark.parametrize(
    "files",
    [_BOARD_STYLE, _META_LAYER, _EXTENDS_LAYER],
    ids=["board-style", "meta-layer", "extends-layer"],
)
def test_rebinding_category_and_sequence_changes_the_stops_charts_consume(
    files: dict[str, str], in_memory_project: Callable
) -> None:
    before = _chart_context(_BOARD_PLAIN, in_memory_project)
    after = _chart_context(files, in_memory_project)

    assert before.palette != after.palette
    assert [c.lower() for c in after.palette] == [
        c.lower() for c in palette("vivid-10")
    ]
    assert before.palettes["sequence"] != "dbt-seq-teal"
    assert after.palettes["sequence"] == "dbt-seq-teal"


def test_rebinding_diverge_changes_the_colors_its_tokens_resolve_to(
    in_memory_project: Callable,
) -> None:
    def color(rebind: str) -> str:
        board = (
            f"{_THEME}style:\n{rebind}  title:\n    font:\n      color: diverge[1]\n"
            f"{_BODY}"
        )
        return _title_color({"charts/board.yml": board}, in_memory_project)

    shipped = color("")
    rebound = color("  palettes:\n    diverge: dbt-div-coolwarm\n")
    assert rebound == palette("dbt-div-coolwarm")[0].lower()
    assert shipped != rebound


_ROLE_STYLE = "  roles:\n    mine: category[2]\n"
_ROLE_TOKEN = "mine.dark"


def _role_board(token: str) -> str:
    return f"{_THEME}style:\n{_ROLE_STYLE}  title:\n    font:\n      color: {token}\n{_BODY}"


_EXPECTED_MINE = color_from_theme(
    "category[2].dark", palettes=get_theme_style("clarity").palettes
).lower()
_ROLE_LAYER_STYLE = (
    f"{_THEME}style:\n{_ROLE_STYLE}  title:\n    font:\n      color: {_ROLE_TOKEN}\n"
)
_ROLE_FILES = {
    "board-style": {"charts/board.yml": _role_board(_ROLE_TOKEN)},
    "meta-layer": {
        "charts/meta.yml": _ROLE_LAYER_STYLE,
        "charts/board.yml": _BODY,
    },
    "extends-layer": {
        "charts/_brand.yml": _ROLE_LAYER_STYLE,
        "charts/board.yml": f"extends: ./_brand.yml\n{_BODY}",
    },
}


@pytest.mark.parametrize("files", _ROLE_FILES.values(), ids=list(_ROLE_FILES))
def test_a_roles_shortcut_authored_anywhere_resolves_its_tokens(
    files: dict[str, str], in_memory_project: Callable
) -> None:
    assert _title_color(files, in_memory_project) == _EXPECTED_MINE


_NESTED = """
title: T
extends: clarity
style:
  palettes:
    category: hero-6
  title:
    font:
      color: category[1]
rows:
  - title: Inherits
    rows:
      - text: hello
  - title: Switches
    extends: neon
    rows:
      - text: hello
"""


def test_an_ancestor_rebind_reaches_nested_boards_including_a_theme_switch() -> None:
    result = compile_board(_NESTED)
    assert result.success and result.board is not None, result.errors
    hero_first = palette("hero-6")[0].lower()
    inherits, switches = (item.board for item in result.board.layout.items)
    for nested in (inherits, switches):
        assert nested.chart_style_context.palettes["category"] == "hero-6"
        assert str(nested.resolved_style.title.font.color).lower() == hero_first


_INDEX = _get_index()
_ALL_PALETTES = get_args(PaletteName)
_ROLE_FAMILY = {
    "category": "categorical",
    "sequence": "sequential",
    "diverge": "diverging",
}


def _family_of(name: str) -> str:
    return _INDEX[name.partition(".")[0]]["family"]


def _names(family: str) -> list[str]:
    return [n for n in _ALL_PALETTES if _family_of(n) == family]


@pytest.mark.parametrize("family", sorted(set(_ROLE_FAMILY.values())) + ["tone"])
def test_the_sweep_covers_every_indexed_palette_of_a_family(family: str) -> None:
    indexed = {name for name, entry in _INDEX.items() if entry["family"] == family}
    assert indexed
    assert {n.partition(".")[0] for n in _names(family)} == indexed


@pytest.mark.parametrize("theme", user_facing_theme_names())
@pytest.mark.parametrize("role", _ROLE_FAMILY)
def test_every_same_family_rebind_loads_on_every_theme(theme: str, role: str) -> None:
    for palette_name in _names(_ROLE_FAMILY[role]):
        assert get_theme_style(theme, {role: palette_name}).palettes[role] == (
            palette_name
        )


@pytest.mark.parametrize("theme", user_facing_theme_names())
@pytest.mark.parametrize("role", _ROLE_FAMILY)
def test_a_cross_family_rebind_is_refused_on_every_theme_with_the_same_message(
    theme: str, role: str
) -> None:
    refused: dict[str, CompilationError] = {}
    for palette_name in _ALL_PALETTES:
        try:
            get_theme_style(theme, {role: palette_name})
        except CompilationError as e:
            refused[palette_name] = e
    wrong_family = {n for n in _ALL_PALETTES if _family_of(n) != _ROLE_FAMILY[role]}
    assert set(refused) == wrong_family
    for palette_name, error in refused.items():
        assert error.code.code == "ERR-PALETTE-REBIND-UNSATISFIED"
        assert error.fields == {
            "field_path": f"style.palettes.{role}",
            "bound": palette_name,
            "family": _family_of(palette_name),
            "required": _ROLE_FAMILY[role],
        }


def test_the_refusal_names_the_offending_field_among_several_rebinds() -> None:
    with pytest.raises(CompilationError) as exc:
        get_theme_style("clarity", {"category": "vivid-10", "sequence": "positive"})
    assert exc.value.fields["field_path"] == "style.palettes.sequence"


def test_roles_without_a_family_take_any_palette() -> None:
    assert get_theme_style("clarity", {"chrome": "dbt-creams"}).palettes["chrome"] == (
        "dbt-creams"
    )


@pytest.mark.parametrize("short", ["hero-6", "category-6-tonal-blue"])
def test_a_six_stop_category_rebind_keeps_every_theme_loading(short: str) -> None:
    for theme in user_facing_theme_names():
        assert get_theme_style(theme, {"category": short}).palettes["category"] == short


def _single_series(theme: str, rebinds: dict[str, str]) -> list[str]:
    categorical = get_theme_style(theme, rebinds).charts.color.categorical
    return categorical.single_series_palette


def test_clarity_single_series_ink_is_the_dark_variant_of_slot_1() -> None:
    assert _single_series("clarity", {"category": "vivid-10"}) == [
        color_from_theme("category[1].dark", palettes={"category": "vivid-10"})
    ]
    assert _single_series("clarity", {"category": "hero-6"}) != _single_series(
        "clarity", {}
    )


def test_paper_single_series_inks_ignore_a_category_rebind() -> None:
    assert _single_series("paper", {"category": "hero-6"}) == _single_series(
        "paper", {}
    )
