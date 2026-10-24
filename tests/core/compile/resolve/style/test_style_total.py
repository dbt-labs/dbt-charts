"""`assert_style_total` names every None that is neither absence nor a sparse slot."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
    user_facing_theme_names,
)
from dbt_charts.core.compile.models.primitives import StrokeStyle
from dbt_charts.core.compile.models.style.authored import AxisXStylePatch
from dbt_charts.core.compile.models.style.theme import Style
from dbt_charts.core.compile.resolve.style.board import resolve_style
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


def _without_grid_width(style: Style) -> Style:
    grid = style.charts.axis.grid.model_copy(update={"width": None})
    axis = style.charts.axis.model_copy(update={"grid": grid})
    return style.model_copy(
        update={"charts": style.charts.model_copy(update={"axis": axis})}
    )


def test_an_axis_overlay_slot_may_stay_sparse() -> None:
    reported = _reported(_with_none_opacity(_theme()))
    assert not [p for p in reported if ".axis_x." in p or ".axis_y." in p]
    assert not [p for p in reported if ".axis_quantitative." in p]


def test_resolving_a_theme_that_left_a_general_value_unset_names_the_path() -> None:
    with pytest.raises(ValueError, match=r"Style\.charts\.axis\.grid\.width"):
        resolve_style(_without_grid_width(_theme()))


@pytest.mark.parametrize("name", user_facing_theme_names())
def test_every_built_in_theme_resolves_total(name: str) -> None:
    resolve_style(get_theme_style(name))


def test_a_partial_value_under_an_absent_field_is_not_walked() -> None:
    style = _theme()
    marks = style.charts.marks.model_copy(
        update={
            "rect": style.charts.marks.rect.model_copy(
                update={"stroke": StrokeStyle(width=1)}
            )
        }
    )
    style = style.model_copy(
        update={"charts": style.charts.model_copy(update={"marks": marks})}
    )
    assert not [p for p in _reported(_with_none_opacity(style)) if ".rect.stroke." in p]


def test_a_theme_without_a_single_series_palette_names_the_path() -> None:
    style = _theme()
    categorical = style.charts.color.categorical.model_copy(
        update={"single_series_palette": None}
    )
    color = style.charts.color.model_copy(update={"categorical": categorical})
    broken = style.model_copy(
        update={"charts": style.charts.model_copy(update={"color": color})}
    )
    with pytest.raises(
        ValueError, match=r"Style\.charts\.color\.categorical\.single_series_palette"
    ):
        resolve_style(broken)
