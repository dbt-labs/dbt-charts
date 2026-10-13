"""Tests for colored glyphs in ConditionalRule and TableColumnConfig."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.theme import KpiTonesStyle


class TestConditionalRuleGlyph:
    def test_glyph_only_rule_is_valid(self) -> None:
        """A rule with just a glyph is a valid style override."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate({"lt": 0, "glyph": "●"})
        assert rule.glyph == "●"
        assert rule.glyph_color is None

    def test_glyph_with_color(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate(
            {"lt": 0, "glyph": "▼", "glyph_color": "#dc2626"}
        )
        assert rule.glyph == "▼"
        assert rule.glyph_color == "#dc2626"

    def test_glyph_alongside_other_styles(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate(
            {
                "gt": 0,
                "glyph": "▲",
                "glyph_color": "#16a34a",
                "font": {"weight": "600"},
            }
        )
        assert rule.glyph == "▲"
        assert rule.glyph_color == "#16a34a"
        assert rule.font is not None and rule.font.weight == "600"

    def test_glyph_color_without_glyph_rejected(self) -> None:
        """glyph_color requires glyph — it has nothing to color otherwise."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError, match="glyph"):
            ConditionalRule.model_validate({"lt": 0, "glyph_color": "#dc2626"})

    def test_empty_glyph_rejected(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate({"lt": 0, "glyph": ""})

    def test_whitespace_only_glyph_rejected(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate({"lt": 0, "glyph": "   "})

    def test_is_null_rule_with_glyph(self) -> None:
        """is_null predicates can drive glyphs (e.g. an em-dash for missing)."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate(
            {"is_null": True, "glyph": "—", "glyph_color": "#9ca3af"}
        )
        assert rule.glyph == "—"
        assert rule.glyph_color == "#9ca3af"


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


class TestResolveConditionalStylesGlyph:
    def test_glyph_propagates_to_overrides(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )
        from dbt_charts.core.render.chart.table_support import (
            resolve_conditional_styles,
        )

        rules = [
            ConditionalRule.model_validate(
                {"lt": 0, "glyph": "▼", "glyph_color": "#dc2626"}
            )
        ]
        out = resolve_conditional_styles(rules, -5)
        assert out["glyph"] == "▼"
        assert out["glyph_color"] == "#dc2626"

    def test_no_match_no_glyph_keys(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )
        from dbt_charts.core.render.chart.table_support import (
            resolve_conditional_styles,
        )

        rules = [
            ConditionalRule.model_validate(
                {"lt": 0, "glyph": "▼", "glyph_color": "#dc2626"}
            )
        ]
        out = resolve_conditional_styles(rules, 5)
        assert "glyph" not in out
        assert "glyph_color" not in out

    def test_last_match_wins_for_glyph(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )
        from dbt_charts.core.render.chart.table_support import (
            resolve_conditional_styles,
        )

        rules = [
            ConditionalRule.model_validate({"lt": 100, "glyph": "●"}),
            ConditionalRule.model_validate({"lt": 50, "glyph": "▲"}),
        ]
        out = resolve_conditional_styles(rules, 10)
        assert out["glyph"] == "▲"

    def test_later_rule_without_glyph_color_clears_inherited_color(self) -> None:
        """Rule A: glyph + color. Rule B: glyph only. Result must drop A's color."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )
        from dbt_charts.core.render.chart.table_support import (
            resolve_conditional_styles,
        )

        rules = [
            ConditionalRule.model_validate(
                {"lt": 100, "glyph": "●", "glyph_color": "#dc2626"}
            ),
            ConditionalRule.model_validate({"lt": 50, "glyph": "▲"}),
        ]
        out = resolve_conditional_styles(rules, 10)
        assert out["glyph"] == "▲"
        assert "glyph_color" not in out, (
            "Later rule's glyph without glyph_color must reset color, not inherit"
        )


class TestConditionalRuleTone:
    def _tones(self) -> KpiTonesStyle:
        from dbt_charts.core.compile.models.style.theme import KpiTonesStyle

        return KpiTonesStyle(
            positive="#16a34a",
            negative="#dc2626",
            warning="#d49656",
            info="#2563eb",
        )

    def test_glyph_with_tone_is_valid(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import ConditionalRule

        rule = ConditionalRule.model_validate(
            {"lt": 0, "glyph": "▼", "tone": "negative"}
        )
        assert rule.glyph == "▼"
        assert rule.tone == "negative"

    def test_tone_without_glyph_rejected(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import ConditionalRule

        with pytest.raises(ValidationError, match="glyph"):
            ConditionalRule.model_validate({"lt": 0, "tone": "negative"})

    def test_unknown_tone_rejected(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import ConditionalRule

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate(
                {"lt": 0, "glyph": "▼", "tone": "chartreuse"}
            )

    def test_tone_resolves_to_theme_color(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import ConditionalRule
        from dbt_charts.core.render.chart.table_support import (
            resolve_conditional_styles,
        )

        rules = [
            ConditionalRule.model_validate({"lt": 0, "glyph": "▼", "tone": "negative"})
        ]
        out = resolve_conditional_styles(rules, -5, tones=self._tones())
        assert out["glyph"] == "▼"
        assert out["glyph_color"] == "#dc2626"

    def test_info_tone_resolves_to_info_color(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import ConditionalRule
        from dbt_charts.core.render.chart.table_support import (
            resolve_conditional_styles,
        )

        rules = [
            ConditionalRule.model_validate({"lte": 30, "glyph": "●", "tone": "info"})
        ]
        out = resolve_conditional_styles(rules, 10, tones=self._tones())
        assert out["glyph_color"] == "#2563eb"

    def test_explicit_glyph_color_wins_over_tone(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import ConditionalRule
        from dbt_charts.core.render.chart.table_support import (
            resolve_conditional_styles,
        )

        rules = [
            ConditionalRule.model_validate(
                {"lt": 0, "glyph": "▼", "glyph_color": "#000000", "tone": "negative"}
            )
        ]
        out = resolve_conditional_styles(rules, -5, tones=self._tones())
        assert out["glyph_color"] == "#000000"

    def test_tone_without_tones_arg_leaves_glyph_color_unset(self) -> None:
        """No theme tones supplied (tones=None) — tone can't resolve, no crash."""
        from dbt_charts.core.compile.models.chart.authored import ConditionalRule
        from dbt_charts.core.render.chart.table_support import (
            resolve_conditional_styles,
        )

        rules = [
            ConditionalRule.model_validate({"lt": 0, "glyph": "▼", "tone": "negative"})
        ]
        out = resolve_conditional_styles(rules, -5)
        assert out["glyph"] == "▼"
        assert "glyph_color" not in out
