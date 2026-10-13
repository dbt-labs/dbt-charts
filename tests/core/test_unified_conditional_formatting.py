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
from dbt_charts.core.compile.models.chart.normalized import BarChart, KpiChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
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
                "conditional_formatting": {
                    "arr": {
                        "when": [
                            {
                                "gt": 1_000_000,
                                "background": "#166534",
                                "font": {"color": "#ffffff", "weight": "bold"},
                            }
                        ]
                    }
                },
            }
        )
        assert patch.conditional_formatting is not None
        assert "arr" in patch.conditional_formatting
        arr_rules = patch.conditional_formatting["arr"].when
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
                "conditional_formatting": {
                    "arr": {
                        "when": [
                            {"gt": 1_000_000, "background": "#166534"},
                            {"lte": 0, "background": "#fee2e2"},
                        ]
                    },
                    "status": {
                        "when": [
                            {"eq": "at_risk", "font": {"style": "italic"}},
                        ]
                    },
                },
            }
        )
        assert patch.conditional_formatting is not None
        assert len(patch.conditional_formatting) == 2
        assert len(patch.conditional_formatting["arr"].when) == 2
        assert patch.conditional_formatting["arr"].when[0].background == "#166534"
        assert patch.conditional_formatting["status"].when[0].eq == "at_risk"

    def test_block_only_allows_when_key_under_column(self):
        """Each column entry accepts only ``when:`` — extra keys are rejected."""
        with pytest.raises(ValidationError):
            _chart_patch_adapter.validate_python(
                {
                    "type": "table",
                    "conditional_formatting": {
                        "arr": {
                            "when": [{"gt": 0, "background": "#166534"}],
                            "default": "#ffffff",  # not allowed
                        }
                    },
                }
            )

    def test_block_rule_without_style_output_raises(self):
        """Each rule in ``when:`` still requires at least one style override."""
        with pytest.raises(ValidationError):
            _chart_patch_adapter.validate_python(
                {
                    "type": "table",
                    "conditional_formatting": {
                        "arr": {"when": [{"gt": 0}]},  # no background/font output
                    },
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


# ============================================================================
# Renderer resolves chart-level rules from conditional_formatting
# ============================================================================


class TestKpiRenderingWithConditionalFormatting:
    """KPI chart resolves per-row style overrides from ``conditional_formatting``."""

    def test_kpi_background_from_block(self):
        """KPI card background is set from a matching rule in the unified block."""
        from dbt_charts.core.compile.models.chart.normalized import KpiChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_kpi_svg

        chart = KpiChart(
            id="kpi1",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="kpi",
            value="revenue",
            conditional_formatting={
                "revenue": {
                    "when": [{"gt": 1_000_000, "background": "#166534"}],
                }
            },
        )
        data = [{"revenue": 1_500_000}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_kpi_svg(
            resolved,
            data,
            width=400,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )

        # Background is applied via the rule
        assert "#166534" in svg

    def test_kpi_font_color_from_block(self):
        """KPI text color is set from ``font.color`` in the rule."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._engine_render import render_kpi_svg

        chart = KpiChart(
            id="kpi_color",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="kpi",
            value="revenue",
            conditional_formatting={
                "revenue": {
                    "when": [
                        {"lt": 0, "font": {"color": "#991b1b"}},
                    ],
                }
            },
        )
        data = [{"revenue": -500}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_kpi_svg(
            resolved,
            data,
            width=400,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )
        assert "#991b1b" in svg


# ============================================================================
# conditional_formatting + per-channel scale coexist (kpi's background channel
# is the only remaining family where a real channel and a CF projection can
# collide — bar/pie/etc. never produce a CF projection anymore, per
# _project_conditional_formatting_inputs collapsing to kpi/table only)
# ============================================================================


def test_gradient_fallback_rejects_unknown_authored_channel_field() -> None:
    from dbt_charts.core.compile.models.primitives import ColorStyle, ScaleTargetConfig
    from dbt_charts.core.compile.resolve.chart.channel import normalize_chart_channels

    chart = BarChart(
        id="bar1",
        type="bar",
        x="quarter",
        y="arr",
        color="missing",
        conditional_formatting={
            "arr": {"when": [{"gt": 1_000_000, "background": "#166534"}]}
        },
    )
    fallback = ScaleTargetConfig(palette=["#ffffff", "#1aff3c"])

    with pytest.raises(ValueError, match="column 'missing' not found"):
        normalize_chart_channels(
            chart,
            {"quarter", "arr"},
            style_color=ColorStyle(gradient=fallback),
        )


def test_kpi_conditional_fallback_rejects_scale_without_column() -> None:
    from dbt_charts.core.compile.resolve.chart.channel import normalize_chart_channels

    chart = KpiChart(
        id="kpi1",
        type="kpi",
        value="arr",
        background={"scale": {"palette": ["#ffffff", "#1aff3c"]}},
        conditional_formatting={
            "arr": {"when": [{"gt": 1_000_000, "background": "#166534"}]}
        },
    )

    with pytest.raises(ValueError, match="must specify 'column' or 'value'"):
        normalize_chart_channels(chart, {"arr"})


def test_kpi_background_channel_conflicts_with_conditional_formatting():
    """kpi is the only family left where a real channel and a CF projection
    can land on the same output — an authored field-mode `background:` and
    conditional_formatting rules that also target `background` both want the
    same channel."""
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    chart = KpiChart(
        id="kpi1",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="kpi",
        value="revenue",
        background="segment",
        conditional_formatting={
            "revenue": {"when": [{"gt": 1_000_000, "background": "#166534"}]}
        },
    )

    with pytest.raises(ValueError, match="Use one surface"):
        resolve(
            chart,
            [{"revenue": 1_500_000, "segment": "Enterprise"}],
            chart_style_context=_BOARD_STYLE,
        )


def test_kpi_gradient_background_channel_merges_with_conditional_formatting():
    """kpi is the only family left that can still land a gradient channel
    and a CF projection on the SAME column: unlike a plain field-mode
    channel (which conflicts, see the sibling test above), a gradient
    channel absorbs the CF rules instead of raising -- the resolved channel
    keeps mode="conditional" but carries the gradient as its fallback_scale
    (`channel.py`'s `_GradientChannelInput` branch)."""
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve.chart.channel import normalize_chart_channels

    chart = KpiChart(
        id="kpi1",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="kpi",
        value="revenue",
        background={"column": "revenue", "scale": {"palette": ["#ffffff", "#1aff3c"]}},
        conditional_formatting={
            "revenue": {"when": [{"gt": 1_000_000, "background": "#166534"}]}
        },
    )

    channels = normalize_chart_channels(chart, {"revenue"})

    assert set(channels) == {"background"}
    assert channels["background"].mode == "conditional"
    # fallback_scale is a resolved subtype after normalize_chart_channels -- not
    # the authored ScaleTargetConfig; assert on the palette field value.
    assert channels["background"].fallback_scale is not None
    assert channels["background"].fallback_scale.palette == ["#ffffff", "#1aff3c"]
