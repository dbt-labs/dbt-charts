"""Regression tests for bundled real italic font faces."""

from __future__ import annotations

from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from dbt_charts.core.fonts import (
    INTER_VARIABLE_FONT_FAMILY,
    SOURCE_SERIF_4_FONT_FAMILY,
    get_face,
)


def _name_values(path: Path, name_id: int) -> set[str]:
    font = TTFont(path)
    return {name.toUnicode() for name in font["name"].names if name.nameID == name_id}


def _axis_tags(path: Path) -> set[str]:
    font = TTFont(path)
    return {axis.axisTag for axis in font["fvar"].axes}


@pytest.mark.parametrize(
    ("font_path", "expected_family", "expected_axes"),
    [
        (
            get_face(INTER_VARIABLE_FONT_FAMILY, "italic").measure_path,
            INTER_VARIABLE_FONT_FAMILY,
            {"opsz", "wght"},
        ),
        (
            get_face(SOURCE_SERIF_4_FONT_FAMILY, "italic").measure_path,
            "Source Serif 4",
            {"opsz", "wght"},
        ),
    ],
)
def test_italic_font_assets_are_real_italic_variable_fonts(
    font_path: Path, expected_family: str, expected_axes: set[str]
) -> None:
    assert font_path.exists()
    assert expected_family in _name_values(font_path, 1)
    assert "Italic" in _name_values(font_path, 2)
    assert _axis_tags(font_path) == expected_axes
