"""Tests for dbt_charts.core.render.utils — shared render-layer helpers."""

from __future__ import annotations

from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.render.utils import font_style_to_mark


def test_font_style_to_mark_copies_all_set_fields() -> None:
    font = FontStyle(
        family="Inter", size=11.0, weight="700", style="italic", color="#123456"
    )
    mark = font_style_to_mark(font)
    assert mark == {
        "font": "Inter",
        "fontSize": 11.0,
        "fontWeight": "700",
        "fontStyle": "italic",
        "fill": "#123456",
    }


def test_font_style_to_mark_omits_none_fields() -> None:
    assert font_style_to_mark(FontStyle()) == {}


def test_font_style_to_mark_style_reaches_fontstyle_key() -> None:
    mark = font_style_to_mark(FontStyle(style="italic"))
    assert mark["fontStyle"] == "italic"
