"""TDD tests: focused legend label-wrapping regression coverage."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

# Endpoint labels off — a stacked bar with a series color channel otherwise
# suppresses the legend entirely in favor of direct labeling, which would
# leave every legend-layout assertion below with no legend to inspect.
_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(
    get_theme_style().model_copy(
        update={
            "charts": get_theme_style().charts.model_copy(
                update={
                    "bar": get_theme_style().charts.bar.model_copy(
                        update={
                            "endpoint_labels": get_theme_style().charts.bar.endpoint_labels.model_copy(
                                update={"visible": False}
                            )
                        }
                    )
                }
            )
        }
    )
)


SAMPLE_DATA = [
    {"month": "Jan", "revenue": 100, "category": "A"},
    {"month": "Feb", "revenue": 200, "category": "B"},
]


@pytest.fixture(autouse=True)
def stark_fixture(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin to stark so legend defaults remain stable for the regression."""
    reset_config()
    monkeypatch.setenv("DCT_DEFAULT_THEME", "stark")
    yield
    reset_config()


class TestLegendTitleVisible:
    """legend.title.visible: false suppresses the legend title (axis.title.visible analog).

    The suppression lives in the legend config (VL ``legend.title = null``), which
    removes the title from layout while keeping ``encoding.color.title`` intact for
    aria-labels and tooltips — mirroring how axis.title.visible keeps encoding.title.
    """

    def _color_title(self, spec: dict[str, Any]) -> Any:
        return spec.get("encoding", {}).get("color", {}).get("title")


class TestLegendSymbolLimit:
    """Legend symbolLimit caps high-cardinality series to prevent overflow."""

    def test_symbol_limit_unset_in_theme_omits_key(self):
        """When the theme carries no symbol_limit, symbolLimit is absent from the VL spec."""
        from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
        from dbt_charts.core.compile.models.style.resolved import (
            ResolvedLegendElementStyle,
            ResolvedLegendPosition,
            ResolvedLegendStyle,
        )
        from dbt_charts.core.compile.models.style.theme import LegendRowStyle
        from dbt_charts.core.render.chart.vl_field_maps import legend_to_vl

        _font = ResolvedFontStyle(
            family="sans-serif",
            color="#000",
            size=12.0,
            weight="400",
            style="normal",
            decoration="none",
            case="none",
            line_height=1.25,
            tabular_figures=False,
        )
        _elem = ResolvedLegendElementStyle(font=_font, padding=4.0, visible=True)
        legend_no_cap = ResolvedLegendStyle(
            position=ResolvedLegendPosition(edge="right", align="start", overlay=False),
            direction="vertical",
            columns=0,
            compact_columns=2,
            row=LegendRowStyle(swatch_width=10.0, entry_gap=16.0),
            label=_elem,
            title=_elem,
            visible=True,
            symbol_limit=None,
        )
        result = legend_to_vl(legend_no_cap)
        assert result is not None
        assert "symbolLimit" not in result
