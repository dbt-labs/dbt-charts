"""`assert_style_total` names every None that is neither absence nor a sparse slot."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.style.authored import AxisXStylePatch
from dbt_charts.core.compile.models.style.theme import Style
from dbt_charts.core.compile.resolve.style.inherit_resolver import assert_style_total


def _theme() -> Style:
    return get_theme_style(get_default_theme_name())


def _reported(style: Style) -> list[str]:
    with pytest.raises(ValueError, match="left unset") as raised:
        assert_style_total(style)
    return str(raised.value).splitlines()[1:]


def _with_none_opacity(style: Style) -> Style:
    return style.model_copy(update={"opacity": None})


def test_a_none_leaf_in_a_total_slot_raises_naming_the_path() -> None:
    assert "Style.opacity" in _reported(_with_none_opacity(_theme()))


def test_a_none_in_a_sparse_slot_is_allowed() -> None:
    style = _theme()
    bar = style.charts.bar.model_copy(update={"axis_x": AxisXStylePatch()})
    style = style.model_copy(
        update={"charts": style.charts.model_copy(update={"bar": bar})}
    )
    assert not [
        p
        for p in _reported(_with_none_opacity(style))
        if "axis_x" in p and ".bar." in p
    ]


def test_a_none_on_an_absent_field_is_allowed() -> None:
    reported = _reported(_with_none_opacity(_theme()))
    assert "Style.box_shadow" not in reported
