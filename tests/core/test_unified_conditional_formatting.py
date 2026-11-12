"""Tests for the unified chart-level ``conditional_formatting`` block.

Revised M4 spec: a single top-level ``conditional_formatting:`` block on the
chart config, indexed by column name, with only ``when:`` lists under each
column. This supersedes the per-channel ``when:`` shape shipped in PR #1423
and deletes ``ConditionalValueRule``.

The same ``ConditionalRule`` type (with nested ``font: FontStyle``) is shared
by tables and charts. Chart rules carry ``background`` and ``font``; the
renderer projects the rule outputs onto the appropriate chart mark / text.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.authored import AuthoredChart
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
)

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

_chart_patch_adapter = TypeAdapter(AuthoredChart)

# ============================================================================
# conditional_formatting block on AuthoredChart
# ============================================================================


class TestConditionalFormattingBlockShape:
    """AuthoredChart accepts the new ``conditional_formatting`` block."""

    def test_block_parses_with_single_column(self):
        patch = _chart_patch_adapter.validate_python(
            {
                "type": "table",
                "style": {
                    "columns": {
                        "arr": {
                            "conditional": [
                                {
                                    "gt": 1_000_000,
                                    "background": "#166534",
                                    "font": {"color": "#ffffff", "weight": "bold"},
                                }
                            ]
                        }
                    }
                },
            }
        )
        assert patch.style is not None
        arr_rules = patch.style.columns["arr"].conditional
        assert len(arr_rules) == 1
        rule = arr_rules[0]
        assert rule.gt == 1_000_000
        assert rule.background == "#166534"
        assert rule.font is not None
        assert rule.font.color == "#ffffff"
        assert rule.font.weight == "bold"

    def test_block_parses_with_multiple_columns(self):
        patch = _chart_patch_adapter.validate_python(
            {
                "type": "table",
                "style": {
                    "columns": {
                        "arr": {
                            "conditional": [
                                {
                                    "gt": 1_000_000,
                                    "background": "#166534",
                                },
                                {"lte": 0, "background": "#fee2e2"},
                            ]
                        },
                        "status": {
                            "conditional": [
                                {
                                    "eq": "at_risk",
                                    "font": {"style": "italic"},
                                },
                            ]
                        },
                    }
                },
            }
        )
        columns = patch.style.columns
        assert len(columns["arr"].conditional) == 2
        assert columns["arr"].conditional[0].background == "#166534"
        assert columns["status"].conditional[0].eq == "at_risk"

    def test_block_rule_without_style_output_raises(self):
        """Each rule still requires at least one style to apply."""
        with pytest.raises(ValidationError, match="at least one style"):
            _chart_patch_adapter.validate_python(
                {
                    "type": "table",
                    "style": {"columns": {"arr": {"conditional": [{"gt": 0}]}}},
                }
            )


# ============================================================================
# Per-channel `when:` is deleted
# ============================================================================


class TestPerChannelWhenRejected:
    """Authoring ``when:`` inside a channel (color/background/opacity/stroke)
    raises a clear error after the revision."""

    def test_color_channel_when_rejected(self):
        """``when`` is not a valid channel key — rejected as an unknown key."""
        from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel

        with pytest.raises(ValueError, match="unknown keys"):
            parse_style_channel(
                {"column": "arr", "when": [{"gt": 0, "value": "#166534"}]},
                "color",
            )

    def test_background_channel_when_rejected(self):
        from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel

        with pytest.raises(ValueError, match="unknown keys"):
            parse_style_channel(
                {"column": "arr", "when": [{"gt": 0, "value": "#166534"}]},
                "background",
            )

    def test_opacity_channel_when_rejected(self):
        from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel

        with pytest.raises(ValueError, match="unknown keys"):
            parse_style_channel(
                {"column": "arr", "when": [{"gt": 0, "value": 0.5}]},
                "opacity",
            )

    def test_chart_patch_color_when_rejected(self):
        """AuthoredChart rejects color: {when: ...} at model parse time."""
        with pytest.raises(ValidationError, match="Inline conditional"):
            _chart_patch_adapter.validate_python(
                {
                    "type": "bar",
                    "x": "q",
                    "y": "arr",
                    "color": {"column": "arr", "when": [{"gt": 0, "value": "#166534"}]},
                }
            )
