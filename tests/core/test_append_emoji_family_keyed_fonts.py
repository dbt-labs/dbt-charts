"""The emoji fallback reaches fonts held in dicts and lists, not just model fields."""

from __future__ import annotations

from pydantic import BaseModel

from dbt_charts.core.compile.models.primitives import ColumnFontStyle, NamedFont
from dbt_charts.core.compile.resolve.style.tokens import _append_emoji_family

_EMOJI = "Noto Emoji"


class _Holder(BaseModel):
    fonts: dict[str, NamedFont]
    columns: dict[str, ColumnFontStyle]
    stack: list[ColumnFontStyle]


def test_emoji_family_appended_inside_dict_and_list_fonts() -> None:
    holder = _Holder(
        fonts={"brand": NamedFont(family="Lora, serif")},
        columns={"name": ColumnFontStyle(family="Inter")},
        stack=[ColumnFontStyle(family="Inter")],
    )

    out = _append_emoji_family(holder, _EMOJI)

    assert out.fonts["brand"].family == "Lora, 'Noto Emoji', serif"
    assert out.columns["name"].family == "Inter, 'Noto Emoji'"
    assert out.stack[0].family == "Inter, 'Noto Emoji'"
    assert holder.fonts["brand"].family == "Lora, serif"
