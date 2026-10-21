"""Scaffold palettes are addressed by numbered step, lightest to darkest."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.resolve.style.palette import (
    UnknownColorError,
    color,
    color_from_theme,
)

GRAYS = {"chrome": "dbt-grays"}
CREAMS = {"chrome": "dbt-creams"}


@pytest.mark.parametrize(
    ("palette_name", "stop", "expected"),
    [
        ("dbt-grays", 0, "#ffffff"),
        ("dbt-grays", 1, "#fafafa"),
        ("dbt-grays", 11, "#222222"),
        ("dbt-grays", 12, "#161616"),
        ("dbt-grays", 100, "#000000"),
        ("dbt-creams", 0, "#ffffff"),
        ("dbt-creams", 1, "#faf7f0"),
        ("dbt-creams", 12, "#1a1714"),
        ("dbt-creams", 100, "#000000"),
    ],
)
def test_direct_and_bracket_forms_agree(
    palette_name: str, stop: int, expected: str
) -> None:
    assert color(f"{palette_name}.{stop}").lower() == expected
    assert (
        color_from_theme(f"chrome[{stop}]", palettes={"chrome": palette_name}).lower()
        == expected
    )


def test_white_and_black_name_the_declared_endpoints() -> None:
    assert color("dbt-grays.white") == color("dbt-grays.0")
    assert color("dbt-creams.black") == color("dbt-creams.100")
    assert color_from_theme("chrome.white", palettes=CREAMS) == color("dbt-grays.0")


@pytest.mark.parametrize("retired", ["canvas", "ink", "void", "muted", "heading"])
def test_role_names_are_gone(retired: str) -> None:
    with pytest.raises(UnknownColorError, match="no alias"):
        color(f"dbt-grays.{retired}")
    with pytest.raises(UnknownColorError, match="no alias"):
        color_from_theme(f"chrome.{retired}", palettes=GRAYS)


@pytest.mark.parametrize("token", ["dbt-grays.13", "dbt-grays.99", "dbt-grays.101"])
def test_an_undeclared_stop_names_the_valid_stops(token: str) -> None:
    with pytest.raises(UnknownColorError, match=r"valid stops: 0-12, 100"):
        color(token)


def test_an_undeclared_bracket_stop_names_the_valid_stops() -> None:
    with pytest.raises(UnknownColorError, match=r"valid stops: 0-12, 100"):
        color_from_theme("chrome[13]", palettes=GRAYS)


def test_a_numeric_alias_inside_the_ladder_is_rejected_at_load() -> None:
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.palette import Palette

    with pytest.raises(ValidationError, match=r"numeric alias\(es\) \['3'\]"):
        Palette(
            name="p", colors=["#000000", "#111111", "#222222"], aliases={"3": "#fff"}
        )
    Palette(name="p", colors=["#000000"], aliases={"0": "#000000", "100": "#ffffff"})


@pytest.mark.parametrize(
    ("theme", "expected"),
    [
        ("stark", "#313233"),
        ("clarity", "#313233"),
        ("vivid", "#313233"),
        ("neon", "#313233"),
        ("paper", "#403b37"),
    ],
)
def test_the_ink_role_is_chrome_step_ten_of_the_themes_own_scaffold(
    theme: str, expected: str
) -> None:
    from dbt_charts.core.compile.config import get_theme_style

    style = get_theme_style(theme)
    assert style.roles is not None
    assert style.roles["ink"] == "chrome[10]"
    resolved = color_from_theme("ink", palettes=style.palettes, roles=style.roles)
    assert resolved.lower() == expected
