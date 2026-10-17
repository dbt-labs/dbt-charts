"""Tests for font.case pipeline: slug_to_text, apply_font_case, field_display_text.

Written before implementation (TDD). These tests drive:
- slug_to_text: tokenization + unit/abbreviation expansion, NO casing
- apply_font_case: delegates to apply_case via ResolvedFontStyle.case
- field_display_text: authored field label verbatim, else slug_to_text → case
- Cascade: child font.case overrides root none
- Enum: 'none' is the preserve/no-op value (not 'preserve')
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.primitives import FontStyle, ResolvedFontStyle
from dbt_charts.core.text.humanize import slug_to_text

# ---------------------------------------------------------------------------
# slug_to_text: tokenization and suffix/abbreviation expansion only — no casing
# ---------------------------------------------------------------------------


def test_slug_to_text_no_capitalize_applied() -> None:
    """slug_to_text must emit lowercase words (no .capitalize() or title logic)."""
    # Plain snake_case slug with no special tokens
    assert slug_to_text("revenue_by_month") == "revenue by month"


def test_slug_to_text_unit_suffix_preserved() -> None:
    """Unit suffix is expanded but NOT title-cased."""
    # revenue_usd → revenue ($) — lower case 'revenue', suffix expanded
    assert slug_to_text("revenue_usd") == "revenue ($)"


def test_slug_to_text_abbreviation_preserved() -> None:
    """Abbreviations retain their canonical form (ARR, YoY, etc.)."""
    assert slug_to_text("arr_usd") == "ARR ($)"


def test_slug_to_text_yoy_growth() -> None:
    assert slug_to_text("yoy_growth_pct") == "YoY growth (%)"


def test_slug_to_text_empty_string() -> None:
    assert slug_to_text("") == ""


def test_slug_to_text_kebab_case() -> None:
    assert slug_to_text("revenue-by-month") == "revenue by month"


def test_slug_to_text_single_word() -> None:
    assert slug_to_text("revenue") == "revenue"


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("$/MTok in / out", "$/MTok in / out"),
        ("Cache-read $/MTok", "Cache read $/MTok"),
        ("pgSQL_hackers", "pgSQL hackers"),
        ("iPhone_units", "iPhone units"),
        ("Order_Month", "Order Month"),
    ],
)
def test_slug_to_text_keeps_mixed_case_tokens(slug: str, expected: str) -> None:
    """A mixed-case token only survives a case-folding warehouse when quoted."""
    assert slug_to_text(slug) == expected


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("REVENUE_USD", "revenue ($)"),
        ("REVENUE", "revenue"),
        ("YOY_GROWTH_PCT", "YoY growth (%)"),
        ("Api_Id", "API ID"),
    ],
)
def test_slug_to_text_folds_single_case_tokens(slug: str, expected: str) -> None:
    """All-caps is indistinguishable from warehouse folding; tables still match."""
    assert slug_to_text(slug) == expected


# ---------------------------------------------------------------------------
# 'none' enum value: no-op after normalization
# ---------------------------------------------------------------------------


def test_font_style_none_case_valid() -> None:
    """FontStyle accepts case='none'."""
    fs = FontStyle(case="none")
    assert fs.case == "none"


def test_merged_font_style_none_case() -> None:
    """ResolvedFontStyle accepts case='none'."""
    mfs = ResolvedFontStyle(
        family="sans-serif",
        color="#000000",
        size=12.0,
        weight="400",
        style="normal",
        decoration="none",
        case="none",
        line_height=1.25,
        tabular_figures=False,
    )
    assert mfs.case == "none"


def test_font_style_rejects_preserve() -> None:
    """'preserve' is not a valid case value — use 'none' instead."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FontStyle(case="preserve")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# apply_font_case: thin wrapper over apply_case that reads ResolvedFontStyle.case
# ---------------------------------------------------------------------------


def test_apply_font_case_none_is_noop() -> None:
    """case='none' passes the string through unchanged."""
    from dbt_charts.core.text.case import apply_font_case

    font = ResolvedFontStyle(
        family="sans-serif",
        color="#000000",
        size=12.0,
        weight="400",
        style="normal",
        decoration="none",
        case="none",
        line_height=1.25,
        tabular_figures=False,
    )
    assert apply_font_case("quarterly revenue", font) == "quarterly revenue"


def test_apply_font_case_title() -> None:
    """case='title' applies Gruber title case."""
    from dbt_charts.core.text.case import apply_font_case

    font = ResolvedFontStyle(
        family="sans-serif",
        color="#000000",
        size=12.0,
        weight="400",
        style="normal",
        decoration="none",
        case="title",
        line_height=1.25,
        tabular_figures=False,
    )
    assert (
        apply_font_case("quarterly revenue by region", font)
        == "Quarterly Revenue by Region"
    )


def test_apply_font_case_upper() -> None:
    from dbt_charts.core.text.case import apply_font_case

    font = ResolvedFontStyle(
        family="sans-serif",
        color="#000000",
        size=12.0,
        weight="400",
        style="normal",
        decoration="none",
        case="upper",
        line_height=1.25,
        tabular_figures=False,
    )
    assert apply_font_case("revenue", font) == "REVENUE"


# ---------------------------------------------------------------------------
# field_title / field_display_text: authored field_labels win, verbatim
# ---------------------------------------------------------------------------


def _font(case: str) -> ResolvedFontStyle:
    return ResolvedFontStyle(
        family="sans-serif",
        color="#000000",
        size=12.0,
        weight="400",
        style="normal",
        decoration="none",
        case=case,
        line_height=1.25,
        tabular_figures=False,
    )


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("none", "revenue ($)"),
        ("title", "Revenue ($)"),
    ],
)
def test_field_display_text_derived_takes_font_case(case: str, expected: str) -> None:
    from dbt_charts.core.text.case import field_display_text

    assert field_display_text("revenue_usd", {}, _font(case)) == expected


def test_field_display_text_abbreviation_survives_title_case() -> None:
    from dbt_charts.core.text.case import field_display_text

    assert field_display_text("yoy_growth_pct", {}, _font("title")) == "YoY Growth (%)"


def test_field_display_text_authored_label_is_verbatim() -> None:
    from dbt_charts.core.text.case import field_display_text

    labels = {"fix-linked": "fix-linked"}
    assert field_display_text("fix-linked", labels, _font("title")) == "fix-linked"


def test_field_title_skips_case_and_prefers_authored_label() -> None:
    from dbt_charts.core.text.case import field_title

    assert field_title("order_month", {}) == "order month"
    assert field_title("order_month", {"order_month": "Month"}) == "Month"


def test_font_case_cascade_none_not_in_theme_resolves() -> None:
    """Theme root case=none cascades to resolved title font."""
    from dbt_charts.core.compile.config import (
        get_config,
        get_theme_style,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    get_config()  # ensure settings are loaded
    resolved = resolve_style(get_theme_style())
    # Root font has case=none; title font may override
    assert resolved.font.case == "none"
