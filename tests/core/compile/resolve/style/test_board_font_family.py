"""A board's ``style.font.family`` reaches the named fonts the board did not set."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.primitives import NamedFont
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

_BOARD_FAMILY = "Roboto"


def _resolve(
    patch: dict[str, Any], theme: str | None = None
) -> tuple[ResolvedStyle, ChartStyleContext]:
    return resolve_style_and_context(
        get_theme_style(theme), StylePatch.model_validate(patch)
    )


def _registry(context: ChartStyleContext) -> dict[str, NamedFont]:
    fonts = context.pre_style.fonts
    assert fonts is not None
    return fonts


@pytest.mark.parametrize("theme", ["clarity", "stark"])
def test_a_board_family_sets_the_numerals_family(theme: str) -> None:
    style, _ = _resolve({"font": {"family": _BOARD_FAMILY}}, theme)
    assert style.numeral_family.startswith(_BOARD_FAMILY)


def test_a_board_family_reaches_every_named_font_but_code() -> None:
    _, baseline = _resolve({})
    _, context = _resolve({"font": {"family": _BOARD_FAMILY}})
    registry = _registry(context)
    for name, font in _registry(baseline).items():
        if font.family is None:
            continue
        family = registry[name].family
        assert family is not None
        if name == "code":
            assert family == font.family
        else:
            assert family.startswith(_BOARD_FAMILY), name


def test_a_board_that_sets_no_family_keeps_the_theme_fonts() -> None:
    baseline_style, baseline = _resolve({})
    style, context = _resolve({"fonts": {"label": {"size": 9}}})
    assert style.numeral_family == baseline_style.numeral_family
    assert {n: f.family for n, f in _registry(context).items()} == {
        n: f.family for n, f in _registry(baseline).items()
    }


def test_a_board_set_numeral_family_wins_over_the_board_family() -> None:
    style, _ = _resolve(
        {
            "font": {"family": _BOARD_FAMILY},
            "fonts": {"numeral": {"family": "Georgia"}},
        }
    )
    assert style.numeral_family.startswith("Georgia")


def test_a_board_set_heading_family_wins_over_the_board_family() -> None:
    _, context = _resolve(
        {
            "font": {"family": _BOARD_FAMILY},
            "fonts": {"heading": {"family": "Georgia"}},
        }
    )
    heading = _registry(context)["heading"].family
    assert heading is not None
    assert heading.startswith("Georgia")
    body = _registry(context)["body"].family
    assert body is not None
    assert body.startswith(_BOARD_FAMILY)


@pytest.mark.parametrize("name", ["numeral", "heading"])
def test_an_authored_family_equal_to_the_themes_string_still_wins(name: str) -> None:
    theme_fonts = get_theme_style().fonts
    assert theme_fonts is not None
    theme_family = theme_fonts[name].family
    assert theme_family is not None
    _, baseline = _resolve({})
    _, context = _resolve(
        {
            "font": {"family": _BOARD_FAMILY},
            "fonts": {name: {"family": theme_family}},
        }
    )
    assert _registry(context)[name].family == _registry(baseline)[name].family


def test_a_custom_named_font_follows_through_extends_and_keeps_its_own_family() -> None:
    _, context = _resolve(
        {
            "font": {"family": _BOARD_FAMILY},
            "fonts": {
                "follower": {"extends": "heading"},
                "owner": {"family": "Georgia"},
            },
        }
    )
    registry = _registry(context)
    follower = registry["follower"].family
    assert follower is not None
    assert follower.startswith(_BOARD_FAMILY)
    owner = registry["owner"].family
    assert owner is not None
    assert owner.startswith("Georgia")


def test_a_board_family_replaces_a_theme_pinned_numeral_family() -> None:
    theme = get_theme_style()
    assert theme.fonts is not None
    pinned = theme.model_copy(
        update={
            "fonts": {
                **theme.fonts,
                "numeral": theme.fonts["numeral"].model_copy(
                    update={"family": "Georgia"}
                ),
            }
        }
    )
    style, _ = resolve_style_and_context(
        pinned, StylePatch.model_validate({"font": {"family": _BOARD_FAMILY}})
    )
    assert style.numeral_family.startswith(_BOARD_FAMILY)
