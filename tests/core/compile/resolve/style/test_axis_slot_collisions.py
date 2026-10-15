"""Where a channel axis slot and a type axis slot both set the same leaf.

``axis_x``/``axis_y`` (channel) and ``axis_quantitative``/``axis_band`` (type)
layer in a fixed order, so a leaf set in both is decided by that order alone.
Pinning the set keeps a change to the cascade from moving a winner unnoticed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import BaseModel

from dbt_charts.core.compile.config import get_theme_style, list_built_in_themes
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_CHANNEL = ("axis_x", "axis_y")
_TYPE = ("axis_quantitative", "axis_band")
_THEMES = [name for name in list_built_in_themes() if name != "_base"]
# Themes whose y-axis restyles grid and tick color for the same leaves as
# axis_quantitative.
_Y_STYLED = {"clarity", "neon", "paper", "vivid"}
# Themes that also pin their own y tick width; elsewhere the base theme's
# `axis.ticks.width` reaches the y-axis without a channel slot setting it.
_Y_HAIRLINE_TICKS = {"neon", "vivid"}


def _leaves(
    node: object, prefix: str = ""
) -> set[
    str
]:  # type-state: object_annotation — recursive walk over a dumped model or parsed YAML tree
    if isinstance(node, BaseModel):
        node = node.model_dump(exclude_none=True)
    if not isinstance(node, dict):
        return set() if node is None or not prefix else {prefix}
    return {
        leaf
        for key, value in node.items()
        for leaf in _leaves(value, f"{prefix}.{key}" if prefix else str(key))
    }


@pytest.mark.parametrize("theme", _THEMES)
def test_builtin_theme_channel_vs_type_slot_collisions(theme: str) -> None:
    ctx = resolve_chart_style_context(get_theme_style(theme))
    quantitative = _leaves(ctx.axis_quantitative)
    collisions = {
        (channel, leaf)
        for channel in _CHANNEL
        for leaf in _leaves(getattr(ctx, channel)) & quantitative
    }
    expected = {("axis_x", "ticks.width")}
    if theme in _Y_STYLED:
        expected |= {("axis_y", "grid.color"), ("axis_y", "ticks.color")}
    if theme in _Y_HAIRLINE_TICKS:
        expected.add(("axis_y", "ticks.width"))
    assert collisions == expected


@pytest.mark.parametrize("theme", _THEMES)
def test_builtin_theme_family_slots_do_not_collide(theme: str) -> None:
    ctx = resolve_chart_style_context(get_theme_style(theme))
    for family in ("bar", "line", "area", "scatter", "heatmap"):
        style = getattr(ctx, family)
        type_leaves = _leaves(getattr(style, "axis_band", None)) | _leaves(
            getattr(style, "axis_quantitative", None)
        )
        for channel in _CHANNEL:
            assert not _leaves(getattr(style, channel)) & type_leaves, (family, channel)


def _mappings(
    node: object,
) -> Iterator[
    dict[str, object]
]:  # type-state: object_annotation — recursive walk over a parsed YAML tree
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _mappings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _mappings(value)
