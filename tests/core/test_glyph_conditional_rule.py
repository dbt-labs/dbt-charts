"""Tests for colored glyphs in TableColumnConfig."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestTableColumnConfigGlyph:
    def test_static_glyph(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )

        cfg = TableColumnConfig.model_validate({"glyph": "●", "glyph_color": "#16a34a"})
        assert cfg.glyph == "●"
        assert cfg.glyph_color == "#16a34a"

    def test_glyph_color_without_glyph_rejected(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )

        with pytest.raises(ValidationError, match="glyph"):
            TableColumnConfig.model_validate({"glyph_color": "#dc2626"})
