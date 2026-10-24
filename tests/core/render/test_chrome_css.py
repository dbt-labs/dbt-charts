"""Tests for shared chrome CSS variable emission."""

from __future__ import annotations

import dataclasses

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.render.chrome_css import (
    chrome_css_variables,
    theme_to_css,
)

_EXPECTED_SYSTEM_VARS = frozenset(
    {
        "--dbt-system-background",
        "--dbt-system-shell-background",
        "--dbt-system-brand",
        "--dbt-system-text",
        "--dbt-system-muted",
        "--dbt-system-accent",
        "--dbt-system-input-background",
        "--dbt-system-input-border",
        "--dbt-system-input-border-style",
        "--dbt-system-menu-background",
        "--dbt-system-radius",
    }
)


def test_theme_to_css_emits_system_vars_for_dark_theme() -> None:
    dark, _ = resolve_style_and_context(get_theme_style("neon"))
    editorial, _ = resolve_style_and_context(get_theme_style("clarity"))
    css = theme_to_css(dark)

    assert css.startswith(":root{")
    assert css.endswith("}")
    assert f"--dbt-system-background: {dark.background}" in css
    assert f"--dbt-system-brand: {dark.chart_defaults.palette[0]}" in css
    assert (
        f"--dbt-system-shell-background: color-mix(in oklch, {dark.background}" in css
    )
    assert ", black)" in css
    assert f"--dbt-system-text: {dark.font.color}" in css
    assert dark.background != editorial.background
    assert dark.font.color != editorial.font.color


def test_chrome_css_variables_cover_contract_slots() -> None:
    rs, _ = resolve_style_and_context(get_theme_style("clarity"))
    variables = chrome_css_variables(rs)
    assert set(variables) == _EXPECTED_SYSTEM_VARS
    assert variables["--dbt-system-muted"] == rs.variables.font.color
    assert variables["--dbt-system-menu-background"] == rs.background
    assert variables["--dbt-system-radius"] == f"{rs.border.radius:g}px"
    assert (
        variables["--dbt-system-accent"] == rs.chart_defaults.single_series_palette[0]
    )
    assert variables["--dbt-system-brand"] == rs.chart_defaults.palette[0]


def test_chrome_css_variables_input_border_style_defaults_to_solid() -> None:
    rs = resolve_style(get_theme_style("clarity"))
    assert rs.variables.border.dash_array is None
    variables = chrome_css_variables(rs)
    assert variables["--dbt-system-input-border-style"] == "solid"


def test_chrome_css_variables_input_border_style_dashed_when_dash_array_set() -> None:
    base = resolve_style(get_theme_style("clarity"))
    dashed_border = base.variables.border.model_copy(update={"dash_array": [4, 4]})
    dashed_variables_style = base.variables.model_copy(update={"border": dashed_border})
    diverged = dataclasses.replace(base, variables=dashed_variables_style)

    variables = chrome_css_variables(diverged)
    assert variables["--dbt-system-input-border-style"] == "dashed"


def test_chrome_css_variables_muted_uses_variables_font_color_not_secondary_font() -> (
    None
):
    # Every shipped theme converges the secondary font and variables.font.color
    # on the same ink, so construct an explicit divergence (distinctive-value
    # pattern) to discriminate which source chrome_css_variables reads.
    base, _ = resolve_style_and_context(get_theme_style("clarity"))
    diverged = dataclasses.replace(base, secondary_color="#ff00ff")
    assert diverged.secondary_color != diverged.variables.font.color
    variables = chrome_css_variables(diverged)
    assert variables["--dbt-system-muted"] == diverged.variables.font.color
