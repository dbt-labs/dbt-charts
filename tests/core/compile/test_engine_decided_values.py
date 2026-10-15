"""`auto` is a color only on the support_table font, and a font value
only on a chart title font."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.markers import AUTO
from dbt_charts.core.compile.models.style.theme import SupportTableStyle

_BOARD = """
title: T
queries:
  q:
    columns: [m, v]
    values: [[a, 1]]
charts:
  c:
    query: q
    type: bar
    x: m
    y: v
style:
{style}
rows: [c]
"""


def _compiles(style: str) -> bool:
    return compile(_BOARD.format(style=style)).success


@pytest.mark.parametrize(
    "style",
    [
        "  font:\n    color: auto",
        "  charts:\n    axis:\n      labels:\n        font:\n          color: auto",
        "  charts:\n    support_table:\n      neutral_color: auto",
        "  charts:\n    kpi:\n      value:\n        font:\n          color: auto",
    ],
    ids=["font", "axis-labels-font", "neutral-color", "kpi-value-font"],
)
def test_auto_color_is_rejected_outside_the_support_table_font(style: str) -> None:
    assert not _compiles(style)


@pytest.mark.parametrize(
    "style",
    [
        "  charts:\n    support_table:\n      font:\n        color: auto",
        "  charts:\n    support_table:\n      label:\n        font:\n          color: auto",
        "  charts:\n    kpi:\n      support:\n        font:\n          color: auto",
        "  charts:\n    kpi:\n      support:\n        glyph:\n          font:\n            color: auto",
    ],
    ids=["font", "label-font", "kpi-support-font", "kpi-support-glyph-font"],
)
def test_auto_color_is_accepted_on_the_support_table_fonts(style: str) -> None:
    assert _compiles(style)


@pytest.mark.parametrize(
    "style",
    [
        "  font:\n    weight: auto",
        "  charts:\n    axis:\n      labels:\n        font:\n          family: auto",
    ],
    ids=["root-weight", "axis-label-family"],
)
def test_auto_is_rejected_outside_a_chart_title_font(style: str) -> None:
    assert not _compiles(style)


def test_auto_is_accepted_on_a_chart_title_font() -> None:
    assert _compiles("  charts:\n    title:\n      font:\n        size: auto")


def test_chart_title_font_has_every_font_style_field() -> None:
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.compile.models.style.theme.board import ChartTitleFontStyle

    assert set(ChartTitleFontStyle.model_fields) == set(FontStyle.model_fields)


def test_schema_rejects_auto_outside_a_chart_title_font() -> None:
    from dbt_charts.core.compile.schema.introspection import introspect
    from dbt_charts.core.compile.schema.renderers.json_schema import (
        render_yaml_schema,
    )

    defs = render_yaml_schema(introspect())["$defs"]
    lookahead = "^(?!auto$)"
    for name in ("FontStyle", "NamedFont", "SupportTableFontStyle"):
        assert defs[name]["properties"]["weight"]["pattern"] == lookahead, name
        assert defs[name]["properties"]["family"]["pattern"].startswith(lookahead), name
    title_font = defs["ChartTitleFontStyle"]["properties"]
    assert "pattern" not in title_font["weight"]
    assert not title_font["family"]["pattern"].startswith(lookahead)


def _baked_bar_table(
    font_color: str, bar_font_color: str | None = None
) -> SupportTableStyle:
    """The bar chart's baked support_table style on clarity with an unset label color."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.support_table import (
        bake_neutral_ink,
        resolve_effective_support_table_style,
    )

    def unlabeled(style: SupportTableStyle) -> SupportTableStyle:
        label = style.label.model_copy(
            update={"font": style.label.font.model_copy(update={"color": AUTO})}
        )
        return style.model_copy(update={"label": label})

    theme = get_theme_style("clarity")
    charts = theme.charts.model_copy(
        update={"support_table": unlabeled(theme.charts.support_table)}
    )
    charts_patch: dict[str, Any] = {"support_table": {"font": {"color": font_color}}}
    if bar_font_color is not None:
        charts_patch["bar"] = {"support_table": {"font": {"color": bar_font_color}}}
    patch = {"charts": charts_patch}
    ctx = resolve_chart_style_context(
        theme.model_copy(update={"charts": charts}), StylePatch.model_validate(patch)
    )
    return bake_neutral_ink(resolve_effective_support_table_style(ctx, "bar"))[0]


def test_unset_label_color_follows_the_table_font_color() -> None:
    assert _baked_bar_table("#123456").label.font.color == "#123456"


def test_unset_label_color_is_the_neutral_ink_under_auto() -> None:
    style = _baked_bar_table("auto")
    assert style.label.font.color == style.neutral_color


def test_unset_label_color_follows_a_family_table_font_color() -> None:
    style = _baked_bar_table("#123456", bar_font_color="#abcdef")
    assert style.label.font.color == "#abcdef"
