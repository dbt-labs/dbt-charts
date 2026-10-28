"""Tests for the chart.support_table style cascade.

Three-tier cascade:
  Tier 1: style.charts.support_table.*
  Tier 2: style.charts.<chart_type>.support_table.*
  Tier 3: chart.style.support_table.*

These tests validate STRUCTURE, PRESENCE, and BEHAVIOR-UNDER-OVERRIDE —
they do NOT pin theme scalars (per CLAUDE.md "What NOT to Test").
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.style.authored import (
    ChartStylePatch,
)
from dbt_charts.core.compile.models.style.theme import (
    SupportTableLabelStyle,
    SupportTableRowStyle,
    SupportTableStyle,
)
from dbt_charts.core.compile.models.style.theme.table import (
    SupportTableDividerStyle,
    SupportTableFontStyle,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context


def _theme_with_support_table(**overrides):
    """Build a Style with a tier-1 support_table override on charts."""
    base = get_theme_style()
    return base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "support_table": base.charts.support_table.model_copy(
                        update=overrides
                    )
                }
            )
        }
    )


# =============================================================================
# STRUCTURE — support_table nested under charts
# =============================================================================


def test_resolved_charts_style_has_support_table_field():
    resolved = resolve_chart_style_context(get_theme_style())
    assert hasattr(resolved, "support_table")
    assert isinstance(resolved.support_table, SupportTableStyle)


def test_compiled_support_table_style_has_all_spec_leaves():
    # Leaves (structure only — no value pinning).

    s = get_theme_style().charts.support_table
    assert hasattr(s, "font")
    assert isinstance(s.font, SupportTableFontStyle)
    assert hasattr(s, "divider")
    assert isinstance(s.divider, SupportTableDividerStyle)
    assert hasattr(s, "row")
    assert isinstance(s.row, SupportTableRowStyle)
    assert hasattr(s, "label")
    assert isinstance(s.label, SupportTableLabelStyle)
    assert hasattr(s, "padding_top")
    assert hasattr(s, "padding_bottom")


def test_compiled_support_table_style_forbids_header_key():
    # The old `header:` block is renamed to `divider:`.
    # Supplying `header:` must raise (extra="forbid").
    with pytest.raises(ValidationError):
        SupportTableStyle.model_validate({"header": {"font": {"size": 12}}})


# =============================================================================
# BEHAVIOR UNDER OVERRIDE — cascade merge semantics
# =============================================================================


def test_support_table_tier1_universal_override():
    # Tier 1: theme sets style.charts.support_table.font.size = 99.5;
    # confirm it propagates to the resolved model.
    theme = _theme_with_support_table(font=SupportTableFontStyle(size=99.5))
    resolved = resolve_chart_style_context(theme)
    assert resolved.support_table.font.size == 99.5


def test_support_table_tier1_override_does_not_cross_into_bar_font():
    # Scope rule: support_table style does NOT cascade into sibling
    # chart-type styles. Setting support_table.font.size must not touch bar mark.
    theme = _theme_with_support_table(font=SupportTableFontStyle(size=77.0))
    resolved = resolve_chart_style_context(theme)
    baseline = resolve_chart_style_context(get_theme_style())
    assert resolved.marks.bar.border.width == baseline.marks.bar.border.width


def test_support_table_divider_width_override():
    theme = _theme_with_support_table(
        divider=SupportTableDividerStyle(
            gap=4.0, visible=True, width=3.0, color="#888888"
        )
    )
    resolved = resolve_chart_style_context(theme)
    assert resolved.support_table.divider.width == 3.0


def test_support_table_padding_top_override():
    theme = _theme_with_support_table(padding_top=42.0)
    resolved = resolve_chart_style_context(theme)
    assert resolved.support_table.padding_top == 42.0


# =============================================================================
# TIER-2 CASCADE — per-chart-type override merges over universal (HIGH 4)
# =============================================================================


def test_support_table_tier2_bar_override_merges_over_tier1():
    # HIGH 4: end-to-end three-tier cascade. Set distinct values at tier 1
    # (universal charts.support_table) AND tier 2 (charts.bar.support_table).
    # resolve_effective_support_table_style(charts, "bar") must return a
    # style where tier-2 leaves win and tier-1 leaves fill in the rest.
    from dbt_charts.core.compile.models.style.authored import SupportTableStylePatch
    from dbt_charts.core.compile.support_table import (
        resolve_effective_support_table_style,
    )

    # Tier 1: universal sets font.size + padding_top.
    tier1 = get_theme_style().charts.support_table.model_copy(
        update={
            "font": SupportTableFontStyle(size=11.0, color="#aaa"),
            "padding_top": 7.0,
        }
    )
    # Tier 2: bar-specific override bumps font.size AND divider.width, but
    # leaves padding_top alone — expect padding_top to inherit from tier 1.
    base = get_theme_style()
    tier2 = merge_onto_base(
        tier1,
        SupportTableStylePatch.model_validate(
            {
                "font": {"size": 23.0},
                "divider": {"width": 4.0},
            }
        ),
    )
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "support_table": tier1,
                    "bar": base.charts.bar.model_copy(update={"support_table": tier2}),
                }
            )
        }
    )
    resolved = resolve_chart_style_context(seed)
    effective = resolve_effective_support_table_style(resolved, "bar")
    # Tier 2 wins where set.
    assert effective.font.size == 23.0
    assert effective.divider.width == 4.0
    # Tier 1 inherits where tier 2 is silent.
    assert effective.padding_top == 7.0
    # Tier 1's font.color survives because tier 2 didn't overwrite font.color.
    assert effective.font.color == "#aaa"


def test_support_table_tier2_unset_falls_through_to_tier1():
    # When a chart type has no support_table override (None), the effective
    # style IS tier 1 — no merging.
    from dbt_charts.core.compile.support_table import (
        resolve_effective_support_table_style,
    )

    theme = _theme_with_support_table(font=SupportTableFontStyle(size=15.0))
    resolved = resolve_chart_style_context(theme)
    effective = resolve_effective_support_table_style(resolved, "bar")
    assert effective.font.size == 15.0


# =============================================================================
# CHART-LOCAL PATCH — `chart.style.support_table`
# =============================================================================


def test_chart_style_patch_accepts_support_table():
    # Tier 3 — authors set chart.style.bar.support_table.* for one-off overrides on cartesian charts.
    patch = ChartStylePatch.model_validate(
        {"bar": {"support_table": {"font": {"weight": 600, "size": 14.0}}}}
    )
    assert patch.bar is not None
    assert patch.bar.support_table is not None
    assert patch.bar.support_table.font is not None
    assert patch.bar.support_table.font.weight == 600
    assert patch.bar.support_table.font.size == 14.0


def test_chart_style_patch_support_table_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        ChartStylePatch.model_validate({"bar": {"support_table": {"foo": "bar"}}})


# =============================================================================
# POSITION FIELD — top / bottom placement knob
# =============================================================================


def test_compiled_support_table_style_has_position_field():
    s = get_theme_style().charts.support_table
    assert s.position == "auto"


def test_support_table_style_rejects_invalid_position():
    # Only "top" and "bottom" are valid; any other string must fail.
    base = get_theme_style().charts.support_table.model_dump()
    base["position"] = "center"
    with pytest.raises(ValidationError):
        SupportTableStyle.model_validate(base)


def test_support_table_position_override_via_tier1():
    # Authors can override position at the theme level (tier 1).
    theme = _theme_with_support_table(position="bottom")
    resolved = resolve_chart_style_context(theme)
    assert resolved.support_table.position == "bottom"


def test_chart_style_patch_accepts_position():
    # Tier 3: chart.style.bar.support_table.position overrides theme on cartesian charts.
    patch = ChartStylePatch.model_validate(
        {"bar": {"support_table": {"position": "bottom"}}}
    )
    assert patch.bar is not None
    assert patch.bar.support_table is not None
    assert patch.bar.support_table.position == "bottom"
