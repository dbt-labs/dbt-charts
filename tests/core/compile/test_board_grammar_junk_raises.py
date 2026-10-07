"""Junk spacing, border, gap, header_overflow and theme values raise instead of coercing."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.board.authored import AUTHORED_BOARD_ADAPTER
from dbt_charts.core.compile.models.primitives import BorderStylePatch
from dbt_charts.core.compile.models.style.authored import StylePatch


@pytest.mark.parametrize(
    ("style", "field"),
    [
        ({"padding": "junk"}, "padding"),
        ({"padding": "1rem"}, "padding"),
        ({"padding": True}, "padding"),
        ({"padding": "1px 2px 3px 4px 5px"}, "padding"),
        ({"margin": "8px junk"}, "margin"),
        ({"border": ""}, "border"),
        ({"border": "junk"}, "border"),
        ({"border": "thick solid red"}, "border"),
        ({"border": "1px double red"}, "border"),
        ({"border": "1px solid Red"}, "border"),
        ({"charts": {"border": "2px solid notacolor"}}, "border"),
        ({"padding": "\u0663px"}, "padding"),
        ({"gap": "1h"}, "gap"),
        ({"gap": "md"}, "gap"),
    ],
)
def test_junk_style_value_raises_naming_the_field(
    style: dict[str, Any], field: str
) -> None:
    with pytest.raises(ValidationError, match=field):
        StylePatch.model_validate(style)


@pytest.mark.parametrize(
    "style",
    [
        {"padding": "16px"},
        {"padding": "8px 16px 12px 4px"},
        {"padding": "0 0 20px 0"},
        {"padding": 12},
        {"margin": "8px"},
        {"border": "2px solid #667eea"},
        {"border": "1px solid red"},
        {"border": "rgb(1, 2, 3)"},
        {"border": "1px solid steelblue"},
        {"border": "#e79491"},
        {"border": "0px"},
        {"gap": 12},
        {"gap": "12px"},
    ],
)
def test_documented_shorthand_still_parses(style: dict[str, Any]) -> None:
    StylePatch.model_validate(style)


@pytest.mark.parametrize(
    ("css", "sides"),
    [
        ("16px", (16, 16, 16, 16)),
        ("8px 16px", (8, 16, 8, 16)),
        ("8px 16px 12px", (8, 16, 12, 16)),
        ("8px 16px 12px 4px", (8, 16, 12, 4)),
    ],
)
def test_spacing_values_by_arity(css: str, sides: tuple[float, ...]) -> None:
    padding = StylePatch.model_validate({"padding": css}).padding
    assert padding is not None
    assert (padding.top, padding.right, padding.bottom, padding.left) == sides


def test_border_shorthand_values() -> None:
    def border(css: str) -> BorderStylePatch:
        parsed = StylePatch.model_validate({"border": css}).border
        assert parsed is not None
        return parsed

    assert border("#e79491").color == "#e79491"
    assert border("#e79491").width is None
    assert border("0px").width == 0
    dashed = border("2px dashed red")
    assert (dashed.width, dashed.color, dashed.dash_array) == (2, "red", [4, 4])
    dotted = border("dotted red")
    assert (dotted.dash_array, dotted.line_cap) == ([1, 3], "round")
    assert border("1px solid red").dash_array is None


def test_kpi_content_padding_junk_raises() -> None:
    board = {
        "title": "t",
        "queries": {"q": {"sql": "select 1 as a"}},
        "charts": {
            "c": {
                "query": "q",
                "type": "kpi",
                "value": "a",
                "style": {"content_padding": "junk"},
            }
        },
        "rows": ["c"],
    }
    with pytest.raises(ValidationError, match="content_padding"):
        AUTHORED_BOARD_ADAPTER.validate_python(board)


def test_header_overflow_requires_canonical_spelling() -> None:
    def board(value: str) -> dict[str, Any]:
        return {
            "title": "t",
            "queries": {"q": {"sql": "select 1 as a"}},
            "charts": {
                "c": {
                    "query": "q",
                    "type": "table",
                    "style": {"columns": {"a": {"header_overflow": value}}},
                }
            },
            "rows": ["c"],
        }

    with pytest.raises(ValidationError, match="header_overflow"):
        AUTHORED_BOARD_ADAPTER.validate_python(board("WRAP_TWO"))
    AUTHORED_BOARD_ADAPTER.validate_python(board("wrap-two"))


@pytest.mark.parametrize("theme", [None, ["paper"], 3])
def test_theme_must_be_a_string(theme: Any) -> None:
    with pytest.raises(ValidationError, match="must be a theme name string"):
        AUTHORED_BOARD_ADAPTER.validate_python(
            {"title": "t", "text": "x", "theme": theme}
        )


@pytest.mark.parametrize("slot", ["title", "subtitle"])
def test_title_overflow_requires_canonical_spelling(slot: str) -> None:
    style = {"title": {"overflow": "WRAP_TWO"}}
    if slot == "subtitle":
        style = {"title": {"subtitle": {"overflow": "WRAP_TWO"}}}
    with pytest.raises(ValidationError, match="overflow"):
        StylePatch.model_validate(style)
