"""KPI style slots: single-line fonts, inherit links, and theme defaults."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.primitives import FontStyle, SingleLineFontStyle
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.theme import KpiChartStyle
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context


def _kpi(style: dict[str, Any] | None = None) -> tuple[KpiChartStyle, Any]:
    patches = [] if style is None else [StylePatch.model_validate(style)]
    resolved, ctx = resolve_style_and_context(get_theme_style(), *patches)
    assert isinstance(ctx, ChartStyleContext)
    return ctx.kpi, resolved


def test_single_line_font_has_no_line_height() -> None:
    assert "line_height" not in SingleLineFontStyle.model_fields
    assert set(FontStyle.model_fields) - set(SingleLineFontStyle.model_fields) == {
        "line_height"
    }
    with pytest.raises(ValidationError):
        SingleLineFontStyle.model_validate({"line_height": 1.2})


def test_default_slot_typography_matches_the_previous_slots() -> None:
    kpi, _ = _kpi()
    label_size = kpi.label.font.size
    assert kpi.value.affix.font.size == label_size
    assert kpi.value.glyph.font.size == label_size
    assert kpi.support.font.size == kpi.font.size == 11
    assert kpi.support.font.family == kpi.font.family
    assert kpi.support.font.weight == kpi.font.weight
    assert kpi.support.glyph.font.size == kpi.support.font.size
    assert kpi.variant == "stacked"


def test_parent_font_ignores_a_cascaded_line_height() -> None:
    kpi, _ = _kpi({"charts": {"font": {"line_height": 1.6, "decoration": "underline"}}})
    assert kpi.font.decoration == "underline"
    assert "line_height" not in type(kpi.font).model_fields


def test_named_font_with_line_height_expands_on_a_kpi_slot() -> None:
    kpi, _ = _kpi({"charts": {"kpi": {"value": {"font": "body"}}}})
    assert kpi.value.font.size == 14


def test_named_font_expands_on_affix_and_glyph() -> None:
    kpi, _ = _kpi(
        {
            "fonts": {"label": {"size": 9}},
            "charts": {
                "kpi": {
                    "value": {"affix": {"font": "label"}, "glyph": {"font": "label"}}
                }
            },
        }
    )
    assert kpi.value.affix.font.size == 9
    assert kpi.value.glyph.font.size == 9


def test_support_glyph_color_token_resolves() -> None:
    kpi, _ = _kpi(
        {
            "charts": {
                "kpi": {"support": {"glyph": {"font": {"color": "chrome.heading"}}}}
            }
        }
    )
    color = kpi.support.glyph.font.color
    assert color is not None and color.startswith("#")


def test_support_glyph_font_inherits_from_support_font() -> None:
    kpi, _ = _kpi({"charts": {"kpi": {"support": {"font": {"size": 15}}}}})
    assert kpi.support.glyph.font.size == 15
    assert kpi.label.font.size != 15


def test_board_title_overflow_reaches_the_kpi_label() -> None:
    kpi, _ = _kpi({"charts": {"title": {"overflow": "clip"}}})
    assert kpi.label.overflow == "clip"


def test_kpi_label_overflow_overrides_the_title_overflow() -> None:
    kpi, _ = _kpi(
        {
            "charts": {
                "title": {"overflow": "clip"},
                "kpi": {"label": {"overflow": "wrap"}},
            }
        }
    )
    assert kpi.label.overflow == "wrap"


def test_kpi_style_has_no_title_or_flat_slots() -> None:
    fields = set(KpiChartStyle.model_fields)
    assert {"title", "affix", "glyph"}.isdisjoint(fields)
    assert {"value", "label", "support", "variant"} <= fields
