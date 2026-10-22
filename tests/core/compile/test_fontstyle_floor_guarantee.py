"""Floor guarantee for FontStyle fields on resolved table chart style.

The render layer previously used `field if field else <hardcoded>` ternaries on
FontStyle fields.  These are unreachable after a normal theme resolution because
_base.yaml guarantees font.size/weight/family for every built-in theme.

These tests lock the invariant so the asserts that replaced the ternaries are safe.
The parameterized test covers every shipped theme so a regression in any single
theme YAML is caught immediately.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    list_built_in_themes,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

# All named (non-infrastructure) themes.  _base is the abstract root — it has no
# font.color and is never used directly at render time.
_RENDERABLE_THEMES = [t for t in list_built_in_themes() if t != "_base"]


@pytest.mark.parametrize("theme_name", _RENDERABLE_THEMES)
def test_table_floor_holds_for_theme(theme_name: str) -> None:
    """FontStyle floor fields are non-None for every renderable built-in theme.

    Covers the four fields asserted by the table render layer:
      - charts.table.font.size       (_base.yaml floor)
      - charts.table.font.weight     (_base.yaml floor)
      - charts.table.font.family     (_base.yaml floor)
      - charts.table.header.font.weight  (_base.yaml floor)
    """
    rs = resolve_chart_style_context(get_theme_style(theme_name))
    assert rs.table.font.size is not None, f"{theme_name}: table font.size is None"
    assert rs.table.font.weight is not None, f"{theme_name}: table font.weight is None"
    assert rs.table.font.family is not None, f"{theme_name}: table font.family is None"
    assert rs.table.header.font.weight is not None, (
        f"{theme_name}: table header font.weight is None"
    )


# Keep the default-theme fixture tests as fast smoke checks that don't require
# parameterization setup — they also serve as the named anchor tests referenced
# in PR review comments.
def test_table_font_size_non_none_default_theme() -> None:
    """Smoke: charts.table.font.size non-None for the default theme."""
    rs = resolve_chart_style_context(get_theme_style())
    assert rs.table.font.size is not None


def test_table_header_font_weight_non_none_default_theme() -> None:
    """Smoke: charts.table.header.font.weight non-None for the default theme."""
    rs = resolve_chart_style_context(get_theme_style())
    assert rs.table.header.font.weight is not None
