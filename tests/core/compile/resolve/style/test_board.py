"""The canvas tone: one light/dark fact per resolved canvas, derived from its color."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.colors import is_light_canvas
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)

_BUILT_IN_TONES = {
    "clarity": "light",
    "paper": "light",
    "stark": "light",
    "vivid": "light",
    "neon": "dark",
}


@pytest.mark.parametrize(("theme", "tone"), _BUILT_IN_TONES.items())
def test_every_built_in_theme_resolves_its_canvas_tone(theme: str, tone: str) -> None:
    style, context = resolve_style_and_context(get_theme_style(theme))
    assert style.canvas_tone == tone
    assert context.canvas_tone == tone


@pytest.mark.parametrize("theme", _BUILT_IN_TONES)
def test_a_built_in_themes_table_text_sits_opposite_its_canvas_tone(
    theme: str,
) -> None:
    """Light table text on a dark canvas, dark text on a light one: the tone
    and the text color the table palette swap used to read never disagree."""
    _, context = resolve_style_and_context(get_theme_style(theme))
    text_is_light = is_light_canvas(context.table.font.color)
    assert text_is_light == (context.canvas_tone == "dark")


def test_an_authored_board_background_sets_the_board_tone() -> None:
    patch = StylePatch.model_validate({"background": "#101820"})
    style, context = resolve_style_and_context(get_theme_style("clarity"), patch)
    assert style.canvas_tone == "dark"
    assert context.canvas_tone == "dark"


def test_a_chart_local_background_sets_that_charts_tone_only() -> None:
    style, context = resolve_style_and_context(get_theme_style("clarity"))
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "c",
            "type": "bar",
            "x": "month",
            "y": "value",
            "query": SqlQuery(sql="SELECT 1", source="db"),
            "query_name": "q",
            "style": {"background": "#101820"},
        }
    )
    assert build_chart_style_context(context, chart).canvas_tone == "dark"
    assert style.canvas_tone == "light"
