"""TableColumnConfig, TitleStyle, and TableRowRoleStyle must carry a
single nested ``font: FontStyle`` block rather than flat ``font_weight`` / ``font_size``
siblings. Old flat keys raise ``ValidationError``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
from dbt_charts.core.compile.models.style.authored import TitleStylePatch as TitleStyle
from dbt_charts.core.compile.models.style.theme import TableRowRoleStyle

# =============================================================================
# TableColumnConfig
# =============================================================================


def test_table_column_config_accepts_nested_font() -> None:
    cfg = TableColumnConfig.model_validate(
        {"font": {"weight": "bold", "color": "#333"}}
    )
    assert cfg.font is not None
    assert cfg.font.weight == "bold"
    assert cfg.font.color == "#333"


def test_table_column_config_rejects_flat_font_weight() -> None:
    with pytest.raises(ValidationError):
        TableColumnConfig.model_validate({"font_weight": "bold"})


def test_table_column_config_rejects_flat_color_at_top_level() -> None:
    # Font-color lives inside font now; top-level `color` is no longer accepted.
    with pytest.raises(ValidationError):
        TableColumnConfig.model_validate({"color": "#333"})


def test_table_column_config_background_still_top_level() -> None:
    # background is not typography — stays at the top level.
    cfg = TableColumnConfig.model_validate(
        {"background": "#fff", "font": {"weight": "bold"}}
    )
    assert cfg.background == "#fff"
    assert cfg.font is not None and cfg.font.weight == "bold"


# =============================================================================
# TitleStyle
# =============================================================================


def test_title_style_accepts_nested_font() -> None:
    ts = TitleStyle.model_validate(
        {"font": {"family": "Inter", "size": 14, "weight": 600, "color": "#333"}}
    )
    assert ts.font is not None
    assert ts.font.family == "Inter"
    assert ts.font.size == 14
    assert ts.font.weight == 600
    assert ts.font.color == "#333"


def test_title_style_rejects_flat_font_size() -> None:
    with pytest.raises(ValidationError):
        TitleStyle.model_validate({"font_size": 14})


def test_title_style_rejects_flat_font_weight() -> None:
    with pytest.raises(ValidationError):
        TitleStyle.model_validate({"font_weight": "bold"})


def test_title_style_rejects_flat_color() -> None:
    # Font-color moved inside font; bare `color` at TitleStyle top-level is gone.
    with pytest.raises(ValidationError):
        TitleStyle.model_validate({"color": "#333"})


def test_title_style_font_string_is_a_named_font_lookup() -> None:
    # `font: <str>` names a `style.fonts` entry; a family goes in `font.family`.
    assert TitleStyle.model_validate({"font": "Inter"}).font.extends == "Inter"


# =============================================================================
# TableRowRoleStyle
# =============================================================================


def test_compiled_table_row_role_style_accepts_nested_font() -> None:
    r = TableRowRoleStyle.model_validate(
        {
            "divider": {
                "visible": False,
                "color": "#000000",
                "width": 1.0,
            },
            "font": {"weight": "bold"},
            "background": "#eee",
        }
    )
    assert r.font is not None and r.font.weight == "bold"
    assert r.background == "#eee"


def test_compiled_table_row_role_style_rejects_flat_font_weight() -> None:
    with pytest.raises(ValidationError):
        TableRowRoleStyle.model_validate({"font_weight": "bold"})
