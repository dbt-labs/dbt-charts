"""Tests for render/svg_utils.py helpers."""

from dbt_charts.core.compile.models.primitives import BorderStyle
from dbt_charts.core.render.svg_utils import (
    border_dash_attrs,
    divider_dash_attr,
    round_clipped,
    translate_group,
)


def test_border_dash_attrs_empty_when_solid():
    border = BorderStyle(width=1, color="#333", radius=0)
    assert border_dash_attrs(border, 80) == ""


def test_border_dash_attrs_dasharray_only():
    border = BorderStyle(width=1, color="#333", radius=0, dash_array=[4, 4])
    assert border_dash_attrs(border, 80) == ' stroke-dasharray="4,4"'


def test_border_dash_attrs_full():
    border = BorderStyle(
        width=1,
        color="#333",
        radius=0,
        dash_array=[8, 4, 1, 4],
        line_cap="round",
        dash_offset=2.0,
    )
    assert border_dash_attrs(border, 170) == (
        ' stroke-dasharray="8,4,1,4" stroke-linecap="round" stroke-dashoffset="2"'
    )


def _dasharray(attrs: str) -> list[float]:
    return [float(v) for v in attrs.split('"')[1].split(",")]


def test_border_dash_attrs_closes_the_outline_on_a_whole_period():
    border = BorderStyle(width=1, color="#333", radius=0, dash_array=[6, 3])
    dashes = _dasharray(border_dash_attrs(border, 100))
    assert abs(round(100 / sum(dashes)) * sum(dashes) - 100) < 0.01


def test_border_dash_attrs_leaves_an_open_rail_as_authored():
    border = BorderStyle(width=1, color="#333", radius=0, dash_array=[6, 3])
    assert border_dash_attrs(border, None) == ' stroke-dasharray="6,3"'


def test_divider_dash_attr_ends_the_line_on_ink():
    dashes = _dasharray(divider_dash_attr("6 3", 100).replace(" ", ""))
    n = round((100 + dashes[1]) / sum(dashes))
    assert abs(n * sum(dashes) - dashes[1] - 100) < 0.01


def test_divider_dash_attr_empty_when_solid():
    assert divider_dash_attr(None, 100) == ""


def test_translate_group_skips_wrapper_when_translate_is_a_noop():
    assert translate_group(0, 0, "<rect/>") == "<rect/>"


def test_translate_group_wraps_when_x_is_nonzero():
    assert translate_group(5, 0, "<rect/>") == (
        '<g transform="translate(5, 0)"><rect/></g>'
    )


def test_translate_group_wraps_when_y_is_nonzero():
    assert translate_group(0, 5, "<rect/>") == (
        '<g transform="translate(0, 5)"><rect/></g>'
    )


def test_translate_group_wraps_a_noop_translate_when_extra_attrs_given():
    assert translate_group(0, 0, "<rect/>", extra_attrs=' class="foo"') == (
        '<g transform="translate(0, 0)" class="foo"><rect/></g>'
    )


def test_a_growing_card_clip_takes_an_id_of_its_own_per_placement() -> None:
    shared = round_clipped("<g/>", 100, 50, 4)
    first = round_clipped("<g/>", 100, 50, 4, "t")
    second = round_clipped("<g/>", 100, 50, 4, "t#2")

    assert "data-dbt-flow-bg" not in shared
    assert 'id="dbt-card-clip-100-50-4-t"' in first
    assert 'id="dbt-card-clip-100-50-4-t-2"' in second
    assert "url(#dbt-card-clip-100-50-4-t-2)" in second
    assert 'data-dbt-flow-bg=""' in second
