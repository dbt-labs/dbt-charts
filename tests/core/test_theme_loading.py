"""Tests for built-in theme/preset loading and user config overlays."""

import pytest

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    reset_config,
)

from .chart_default_expectations import (
    shipped_default_theme_name,
    shipped_palette,
)


@pytest.fixture(autouse=True)
def reset_config_before_each():
    """Reset config before each test to ensure clean state."""
    reset_config()
    yield
    reset_config()


def test_built_in_themes_load_via_get_theme_style():
    """Built-in themes are available via get_theme_style(), not config.themes."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    # `vivid` extends `_base` and inherits vivid-10.
    vivid = resolve_style(get_theme_style("vivid"))
    assert vivid.chart_defaults.palette == shipped_palette("vivid-10")

    # The shipped default theme (`default`) carries the editorial voice and
    # the editorial-10 palette. Stark — the opt-in stripped-back theme — keeps
    # vivid-10 (covered by test_stark_theme_still_uses_vivid_10).
    default = resolve_style(get_theme_style(shipped_default_theme_name()))
    assert default.chart_defaults.palette == shipped_palette("editorial-10")


def test_vega_config_defaults_applied():
    """Vega-Lite config defaults flow from the theme cascade via style_to_vega_lite.

    Axis/legend/mark defaults are no longer authored as a static vega.config
    dict — they're produced by the theme emit path. vega.default_theme was
    removed from config when the default theme name moved to the config module
    constant _default_theme_name, exposed via get_default_theme_name().
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    default_theme_name = shipped_default_theme_name()
    # vega.default_theme removed from config; verify via the module-level getter.

    assert get_default_theme_name() == default_theme_name
    # ResolvedStyle.vega_config must be a dict with the 'view' key,
    # confirming the full cascade pipeline runs correctly.
    vega_cfg = resolve_style(get_theme_style()).vega_config
    assert isinstance(vega_cfg, dict)
    assert "view" in vega_cfg
