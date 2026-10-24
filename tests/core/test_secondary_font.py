"""`style.fonts.secondary` is the one secondary-text ink; no root `muted` remains."""

from __future__ import annotations

import dataclasses

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.models.style.theme.style import Style
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context


def test_muted_is_gone_from_both_style_models() -> None:
    assert "muted" not in Style.model_fields
    assert "muted" not in {f.name for f in dataclasses.fields(ResolvedStyle)}


@pytest.mark.parametrize("theme", ["stark", "clarity", "paper", "vivid", "neon"])
def test_every_secondary_surface_defaults_to_the_secondary_color(theme: str) -> None:
    resolved, context = resolve_style_and_context(get_theme_style(theme))
    color = resolved.secondary_color
    assert context.table.row_numbers.font.color == color
    assert context.table.more_rows.font.color == color
    assert context.table.empty_state.font.color == color
    assert context.spark_bar.subtitle.font.color == color
    assert context.spark_bar.more_rows.font.color == color


def test_a_board_override_of_the_secondary_font_reaches_every_surface() -> None:
    from dbt_charts.core.compile.models.style.authored import StylePatch

    patch = StylePatch.model_validate({"fonts": {"secondary": {"color": "#123456"}}})
    resolved, context = resolve_style_and_context(get_theme_style("clarity"), patch)
    assert resolved.secondary_color == "#123456"
    assert context.table.row_numbers.font.color == "#123456"
    assert context.table.more_rows.font.color == "#123456"
    assert context.table.empty_state.font.color == "#123456"
    assert context.spark_bar.subtitle.font.color == "#123456"
    assert context.spark_bar.more_rows.font.color == "#123456"


def test_a_palette_token_authored_on_a_named_font_resolves_to_hex() -> None:
    from dbt_charts.core.compile.models.style.authored import StylePatch

    patch = StylePatch.model_validate(
        {"fonts": {"secondary": {"color": "category[2]"}}}
    )
    resolved, context = resolve_style_and_context(get_theme_style("clarity"), patch)
    assert resolved.secondary_color.startswith("#")
    assert context.table.row_numbers.font.color == resolved.secondary_color
