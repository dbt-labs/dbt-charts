"""TDD tests for flat-prefix → nested-block renames.

Each rename has two tests:
  - new nested form parses correctly
  - old flat form raises ValidationError (extra="forbid")
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.style.authored import (
    BaseScaleStylePatch,
    TableColumnDefaultsConfig,
)
from dbt_charts.core.compile.models.style.theme import (
    BaseAxisGridStyle,
    BaseScaleStyle,
    DetailsArrowFontStyle,
    DetailsArrowStyle,
    DetailsStyle,
    TableChartStyle,
    TableRowStripeStyle,
    TableRowStyle,
)

# ── BaseAxisGridStyle: zero_color / zero_width (historical flat) → threshold: {color, width} (current nested) ──


def test_axis_grid_threshold_nested_parses() -> None:
    """New nested threshold block is accepted by BaseAxisGridStyle (every axis slot)."""
    grid = BaseAxisGridStyle.model_validate(
        {"threshold": {"color": "#ff0000", "width": 2.0}}
    )
    assert grid.threshold is not None
    assert grid.threshold.color == "#ff0000"
    assert grid.threshold.width == 2.0


def test_axis_grid_zero_color_flat_rejected() -> None:
    """Old flat zero_color field (the historical pre-nested spelling, from
    before the threshold sub-block existed at all) is rejected
    (extra='forbid'). Pins a past-release shape -- not this task's
    zero -> threshold rename, which is a separate, already-migrated grammar
    change (see compile/migrations/versions/v0_7_0.py)."""
    with pytest.raises(ValidationError):
        BaseAxisGridStyle.model_validate({"zero_color": "#ff0000"})


def test_axis_grid_zero_width_flat_rejected() -> None:
    """Old flat zero_width field (the historical pre-nested spelling) is
    rejected (extra='forbid'). See test_axis_grid_zero_color_flat_rejected."""
    with pytest.raises(ValidationError):
        BaseAxisGridStyle.model_validate({"zero_width": 2.0})


# ── TableChartStyle: flat rule_color is rejected ──────────────────────────────


def test_table_rule_color_flat_rejected() -> None:
    """Old flat rule_color field is rejected by TableChartStyle (extra='forbid')."""
    with pytest.raises(ValidationError):
        TableChartStyle.model_validate({"rule_color": "#00bb00"})


# ── DetailsStyle: arrow_x / arrow_font_size → arrow: {x, font: {size}} ───────


def test_details_arrow_style_parses() -> None:
    """New DetailsArrowStyle sub-model parses correctly."""
    arrow = DetailsArrowStyle.model_validate({"x": 14.0, "font": {"size": 13.0}})
    assert arrow.x == 14.0
    assert arrow.font.size == 13.0


def test_details_arrow_font_style_parses() -> None:
    """New DetailsArrowFontStyle sub-model parses correctly."""
    font = DetailsArrowFontStyle.model_validate({"size": 13.0})
    assert font.size == 13.0


def test_details_arrow_x_flat_rejected() -> None:
    """Old flat arrow_x field is rejected by DetailsStyle (extra='forbid')."""
    with pytest.raises(ValidationError):
        DetailsStyle.model_validate({"arrow_x": 14.0})


def test_details_arrow_font_size_flat_rejected() -> None:
    """Old flat arrow_font_size field is rejected by DetailsStyle (extra='forbid')."""
    with pytest.raises(ValidationError):
        DetailsStyle.model_validate({"arrow_font_size": 13.0})


# ── TableRowStyle: stripe_color → stripe: TableRowStripeStyle {color} ─────────


def test_table_row_stripe_style_parses() -> None:
    """New TableRowStripeStyle sub-model parses correctly."""
    stripe = TableRowStripeStyle.model_validate({"color": "#f0f0f0"})
    assert stripe.color == "#f0f0f0"


def test_table_row_stripe_color_flat_rejected() -> None:
    """Old flat stripe_color field is rejected by TableRowStyle (extra='forbid')."""
    with pytest.raises(ValidationError):
        TableRowStyle.model_validate({"stripe_color": "#f0f0f0"})


# ── BaseScaleStyle: max_band_size / min_band_size → mark_size: {min_size, max_size} ─


def test_scale_mark_size_group_rejected() -> None:
    """mark_size scale config was deleted (2026-08 trim) — BaseScaleStyle
    rejects the group entirely, not just its flat predecessor fields."""
    with pytest.raises(ValidationError):
        BaseScaleStyle.model_validate(
            {"mark_size": {"min_size": 5.0, "max_size": 40.0}}
        )


def test_scale_max_band_size_flat_rejected() -> None:
    """Old flat max_band_size field is rejected (extra='forbid')."""
    with pytest.raises(ValidationError):
        BaseScaleStyle.model_validate({"max_band_size": 40.0})


def test_scale_min_band_size_flat_rejected() -> None:
    """Old flat min_band_size field is rejected (extra='forbid')."""
    with pytest.raises(ValidationError):
        BaseScaleStyle.model_validate({"min_band_size": 5.0})


def test_scale_patch_mark_size_group_rejected() -> None:
    """mark_size scale config was deleted (2026-08 trim) — BaseScaleStylePatch
    rejects the group entirely too."""
    with pytest.raises(ValidationError):
        BaseScaleStylePatch.model_validate({"mark_size": {"min_size": 5.0}})


# ── TableColumnDefaultsConfig: label rejected ────────────────────────────────


def test_table_column_defaults_label_rejected() -> None:
    with pytest.raises(ValidationError):
        TableColumnDefaultsConfig.model_validate({"label": "My Column"})
