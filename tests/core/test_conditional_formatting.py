"""Tests for conditional formatting engine.

Covers the ``when`` clause under the chart-level ``conditional_formatting:``
block: condition operators, style overrides, rule ordering / conflict
resolution, and validation. Tables read CF rules directly from the block at
render time — there is no per-column ``when:`` field.
"""

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import KpiChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestConditionalRuleModel:
    """ConditionalRule validates operator + style overrides."""

    def test_basic_rule_with_lt_and_background(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule(lt=0, background="#fee2e2")
        assert rule.lt == 0
        assert rule.background == "#fee2e2"

    def test_basic_rule_with_eq_string(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate(
            {"eq": "At Risk", "background": "#fef3c7", "font": {"color": "#92400e"}}
        )
        assert rule.eq == "At Risk"
        assert rule.background == "#fef3c7"
        assert rule.font is not None
        assert rule.font.color == "#92400e"

    def test_rule_with_multiple_style_keys(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate(
            {
                "gte": 100000,
                "background": "#dcfce7",
                "font": {"color": "#166534", "weight": "bold"},
            }
        )
        assert rule.gte == 100000
        assert rule.font is not None
        assert rule.font.weight == "bold"

    def test_rule_must_have_exactly_one_operator(self):
        """A rule with zero operators is invalid."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule(background="#fee2e2")

    def test_rule_rejects_two_operators(self):
        """A rule with two operators is invalid."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule(lt=0, gt=100, background="#fee2e2")

    def test_rule_must_have_at_least_one_style(self):
        """A rule with an operator but no style overrides is invalid."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule(eq="bad")

    def test_rule_rejects_unknown_font_fields(self):
        """Unknown fields inside font are rejected (FontStyle has extra='forbid')."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate(
                {"eq": "bad", "font": {"unknown_field": "value"}}
            )

    @pytest.mark.parametrize("weight", ["normal", "bold", "100", "400", "700", "900"])
    def test_rule_accepts_valid_font_weights(self, weight):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate({"eq": "x", "font": {"weight": weight}})
        assert rule.font is not None
        assert rule.font.weight == weight


class TestFieldConditionalFormattingBlock:
    """``FieldConditionalFormatting`` carries a column's ``when`` list."""

    def test_block_parses_when_list(self):
        from dbt_charts.core.compile.models.chart.authored import (
            FieldConditionalFormatting,
        )

        entry = FieldConditionalFormatting.model_validate(
            {
                "when": [
                    {
                        "lt": 0,
                        "background": "#fee2e2",
                        "font": {"color": "#991b1b"},
                    },
                    {"gte": 100_000, "background": "#dcfce7"},
                ]
            }
        )
        assert len(entry.when) == 2
        assert entry.when[0].lt == 0
        assert entry.when[0].font is not None
        assert entry.when[0].font.color == "#991b1b"

    def test_block_rejects_extra_keys_on_column_entry(self):
        """Each column entry only accepts ``when:``."""
        from dbt_charts.core.compile.models.chart.authored import (
            FieldConditionalFormatting,
        )

        with pytest.raises(ValidationError):
            FieldConditionalFormatting.model_validate(
                {
                    "when": [{"eq": "x", "background": "#fff"}],
                    "default_background": "#ccc",  # not allowed
                }
            )


# ---------------------------------------------------------------------------
# Rule evaluation tests
# ---------------------------------------------------------------------------


class TestEvaluateCondition:
    """Test individual condition operators."""

    def test_eq_matches_string(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(eq="At Risk", background="#fef3c7")
        assert match_predicate(rule, "At Risk") is True
        assert match_predicate(rule, "On Track") is False

    def test_eq_matches_number(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(eq=42, background="#fff")
        assert match_predicate(rule, 42) is True
        assert match_predicate(rule, 43) is False

    def test_ne_operator(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(ne="OK", background="#fee2e2")
        assert match_predicate(rule, "Bad") is True
        assert match_predicate(rule, "OK") is False

    def test_lt_operator(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(lt=0, background="#fee2e2")
        assert match_predicate(rule, -5) is True
        assert match_predicate(rule, 0) is False
        assert match_predicate(rule, 10) is False

    def test_lte_operator(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(lte=0, background="#fee2e2")
        assert match_predicate(rule, -5) is True
        assert match_predicate(rule, 0) is True
        assert match_predicate(rule, 1) is False

    def test_gt_operator(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )
        from dbt_charts.core.compile.models.primitives import FontStyle

        rule = ConditionalRule(gt=100, font=FontStyle(color="green"))
        assert match_predicate(rule, 200) is True
        assert match_predicate(rule, 100) is False

    def test_gte_operator(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )
        from dbt_charts.core.compile.models.primitives import FontStyle

        rule = ConditionalRule(gte=100, font=FontStyle(color="green"))
        assert match_predicate(rule, 100) is True
        assert match_predicate(rule, 99) is False

    def test_none_value_never_matches(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(eq=0, background="#fee2e2")
        assert match_predicate(rule, None) is False

    def test_type_mismatch_does_not_crash(self):
        """Comparing string value against numeric operator returns False, not error."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(lt=0, background="#fee2e2")
        assert match_predicate(rule, "hello") is False

    def test_eq_zero_does_not_match_false(self):
        """Guard against Python's bool ⊂ int: eq: 0 should not match False."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(eq=0, background="#fee2e2")
        assert match_predicate(rule, False) is False
        assert match_predicate(rule, 0) is True

    def test_ne_zero_does_not_match_true_as_not_equal(self):
        """ne: 0 should not interact with True/False."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(ne=0, background="#fee2e2")
        assert match_predicate(rule, True) is True  # different types
        assert match_predicate(rule, 0) is False

    def test_bool_excluded_from_numeric_operators(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(gt=0, background="#fee2e2")
        assert match_predicate(rule, True) is False


# ---------------------------------------------------------------------------
# Style resolution tests
# ---------------------------------------------------------------------------


class TestResolveConditionalStyles:
    """Test that when rules produce correct merged style overrides."""

    def test_no_rules_returns_empty(self):
        from dbt_charts.core.render.chart.table_support import (
            resolve_conditional_styles,
        )

        assert resolve_conditional_styles(None, 42) == {}
        assert resolve_conditional_styles([], 42) == {}

    def test_single_matching_rule(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.render.chart.table_support import (
            resolve_conditional_styles,
        )

        rules = [
            ConditionalRule(lt=0, background="#fee2e2", font=FontStyle(color="#991b1b"))
        ]
        result = resolve_conditional_styles(rules, -5)
        assert result["background"] == "#fee2e2"
        assert result["color"] == "#991b1b"

    def test_no_matching_rule_returns_empty(self):
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )
        from dbt_charts.core.render.chart.table_support import (
            resolve_conditional_styles,
        )

        rules = [ConditionalRule(lt=0, background="#fee2e2")]
        result = resolve_conditional_styles(rules, 100)
        assert result == {}

    def test_multiple_matching_rules_last_wins(self):
        """When multiple rules match, later rules override earlier ones."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.render.chart.table_support import (
            resolve_conditional_styles,
        )

        rules = [
            ConditionalRule(gt=0, background="#dcfce7"),
            ConditionalRule(
                gte=100, background="#bbf7d0", font=FontStyle(weight="bold")
            ),
        ]
        # Value 200 matches both rules
        result = resolve_conditional_styles(rules, 200)
        # Later rule overrides background, adds font.weight
        assert result["background"] == "#bbf7d0"
        assert result["weight"] == "bold"

    def test_partial_overlap_merges(self):
        """Non-overlapping style keys from multiple rules are merged."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.render.chart.table_support import (
            resolve_conditional_styles,
        )

        rules = [
            ConditionalRule(gt=0, background="#dcfce7"),
            ConditionalRule(gt=0, font=FontStyle(color="#166534")),
        ]
        result = resolve_conditional_styles(rules, 50)
        assert result["background"] == "#dcfce7"
        assert result["color"] == "#166534"


# ---------------------------------------------------------------------------
# End-to-end rendering tests
# ---------------------------------------------------------------------------


class TestConditionalFormattingRendering:
    """Test that conditional formatting applies in rendered SVG output.

    CF rules are authored under the chart-level ``conditional_formatting``
    block, indexed by column name. The table renderer reads the block
    directly at render time.
    """

    def test_negative_value_gets_conditional_background(self, make_chart):
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "amount": {
                    "when": [
                        {
                            "lt": 0,
                            "background": "#fee2e2",
                            "font": {"color": "#991b1b"},
                        },
                    ],
                }
            },
            style={
                "columns": {
                    "amount": TableColumnConfig(format=",.0f", background="#ffffff"),
                }
            },
        )
        data = [{"amount": -500}, {"amount": 200}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert 'fill="#fee2e2"' in svg
        assert 'fill="#991b1b"' in svg

    def test_positive_value_keeps_base_style(self, make_chart):
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "amount": {"when": [{"lt": 0, "background": "#fee2e2"}]},
            },
            style={
                "columns": {
                    "amount": TableColumnConfig(background="#ffffff"),
                }
            },
        )
        data = [{"amount": 200}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert 'fill="#ffffff"' in svg
        assert 'fill="#fee2e2"' not in svg

    def test_string_eq_conditional_formatting(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "status": {
                    "when": [
                        {
                            "eq": "Critical",
                            "background": "#fee2e2",
                            "font": {"color": "#991b1b", "weight": "bold"},
                        },
                    ],
                }
            },
        )
        data = [{"status": "Critical"}, {"status": "OK"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert 'fill="#fee2e2"' in svg
        assert 'font-weight="bold"' in svg

    def test_conditional_font_weight_applied(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "amount": {"when": [{"gte": 1000, "font": {"weight": "bold"}}]},
            },
        )
        data = [{"amount": 5000}, {"amount": 10}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert 'font-weight="bold"' in svg

    def test_multiple_columns_independent_rules(self, make_chart):
        """Each column's rules are evaluated independently."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "amount": {"when": [{"lt": 0, "background": "#fee2e2"}]},
                "status": {"when": [{"eq": "Critical", "background": "#fecaca"}]},
            },
        )
        data = [{"amount": -100, "status": "OK"}, {"amount": 200, "status": "Critical"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )

        assert 'fill="#fee2e2"' in svg
        assert 'fill="#fecaca"' in svg

    def test_when_with_dynamic_field_ref_coexists(self, make_chart):
        """Rules coexist with the dynamic field-ref background mechanism."""
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "revenue": {"when": [{"lt": 0, "background": "#fee2e2"}]},
            },
            style={
                "columns": {
                    "revenue": TableColumnConfig(background="_bg_col"),
                }
            },
        )
        data = [
            {"revenue": 100, "_bg_col": "#e0ffe0"},
            {"revenue": -50, "_bg_col": "#e0ffe0"},
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        # Row with revenue=100 gets the base field-ref background
        assert 'fill="#e0ffe0"' in svg
        # Row with revenue=-50 gets the conditional override
        assert 'fill="#fee2e2"' in svg

    def test_conditional_italic_renders_in_svg(self, make_chart):
        """font.style=italic emits font-style attribute on matching rows."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "status": {"when": [{"eq": "Pending", "font": {"style": "italic"}}]},
            },
        )
        data = [{"status": "Pending"}, {"status": "OK"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert svg.count('font-style="italic"') == 1

    def test_category_role_token_resolves_in_conditional_formatting(self, make_chart):
        """A ``category[n]`` role token in conditional_formatting resolves against
        the theme cascade instead of raising UnknownColorError.

        Role-indirected tokens like ``category[1]`` need theme palettes/roles
        context to resolve — context that only exists at style-resolution time,
        not at normalize time. Regression test for GH dbt-labs/dbt-charts#34.
        """
        from dbt_charts.core.compile.resolve.style.tokens import (
            _resolve_one_color_token,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "status": {
                    "when": [{"eq": "open", "font": {"color": "category[1]"}}],
                }
            },
        )
        data = [{"status": "open"}, {"status": "closed"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        expected_hex = _resolve_one_color_token(
            "category[1]", _BOARD_STYLE.palettes, _BOARD_STYLE.roles
        )
        assert f'fill="{expected_hex}"' in svg

    def test_conditional_strikethrough_renders_in_svg(self, make_chart):
        """font.decoration=line-through emits text-decoration on matching rows."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "amount": {
                    "when": [{"lt": 0, "font": {"decoration": "line-through"}}],
                },
            },
        )
        data = [{"amount": -100}, {"amount": 50}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert svg.count('text-decoration="line-through"') == 1


# ---------------------------------------------------------------------------
# Extended predicates — between, in, is_null, default
# ---------------------------------------------------------------------------


class TestExtendedPredicatesTableRendering:
    """End-to-end render tests for the between/in/is_null/default predicates."""

    def test_between_highlights_only_in_range(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "amount": {"when": [{"between": [10, 20], "background": "#dcfce7"}]},
            },
        )
        data = [{"amount": 5}, {"amount": 15}, {"amount": 25}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        # exactly one matching cell
        assert svg.count('fill="#dcfce7"') == 1

    def test_between_matches_boundaries_inclusive(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "amount": {"when": [{"between": [10, 20], "background": "#dcfce7"}]},
            },
        )
        data = [{"amount": 10}, {"amount": 20}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert svg.count('fill="#dcfce7"') == 2

    def test_in_highlights_matching_values(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "status": {
                    "when": [
                        {
                            "in": ["At Risk", "Critical"],
                            "background": "#fee2e2",
                            "font": {"color": "#991b1b"},
                        },
                    ],
                },
            },
        )
        data = [
            {"status": "At Risk"},
            {"status": "Critical"},
            {"status": "OK"},
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert svg.count('fill="#fee2e2"') == 2
        assert svg.count('fill="#991b1b"') == 2

    def test_is_null_true_matches_null_cells(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "amount": {
                    "when": [{"is_null": True, "background": "#e5e7eb"}],
                },
            },
        )
        data = [{"amount": None}, {"amount": 10}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert svg.count('fill="#e5e7eb"') == 1

    def test_is_null_false_matches_non_null_cells(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "amount": {
                    "when": [{"is_null": False, "background": "#dcfce7"}],
                },
            },
        )
        data = [{"amount": None}, {"amount": 10}, {"amount": 0}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        # None is not matched; 10 and 0 are.
        assert svg.count('fill="#dcfce7"') == 2

    def test_default_fires_when_no_other_rule_matches(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "amount": {
                    "when": [
                        {"lt": 0, "background": "#fee2e2"},
                        {"gt": 100, "background": "#dcfce7"},
                        {"default": True, "background": "#f3f4f6"},
                    ],
                },
            },
        )
        data = [{"amount": -1}, {"amount": 50}, {"amount": 200}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert svg.count('fill="#fee2e2"') == 1  # < 0
        assert svg.count('fill="#dcfce7"') == 1  # > 100
        assert svg.count('fill="#f3f4f6"') == 1  # default

    def test_default_does_not_override_a_matching_rule(self, make_chart):
        """Default is a catch-all — it only fires when NO other rule matched.

        Last-match-wins still holds between non-default rules, but ``default``
        is treated specially: it skips if any non-default rule matched.
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            conditional_formatting={
                "amount": {
                    "when": [
                        {"lt": 0, "background": "#fee2e2"},
                        {"default": True, "background": "#f3f4f6"},
                    ],
                },
            },
        )
        data = [{"amount": -1}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        # lt:0 matched, default skipped.
        assert 'fill="#fee2e2"' in svg
        assert 'fill="#f3f4f6"' not in svg


class TestExtendedPredicatesKpiRendering:
    """KPI-tile background coloring via the new predicates.

    Validates the corpus fix: Looker "equal to NULL" rules on single_value
    tiles should now lower cleanly via ``is_null: true``.
    """

    def test_kpi_is_null_false_applies_to_non_null_metric(self):
        """``is_null: false`` fires on any non-null metric — validates the
        predicate path through the KPI channel evaluator (the null-metric
        path itself is a pre-existing KPI renderer limitation, orthogonal)."""
        from dbt_charts.core.compile.models.chart.normalized import KpiChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.render.chart.kpi import render_kpi_svg

        chart = KpiChart(
            id="kpi_notnull",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="kpi",
            value="arr",
            conditional_formatting={
                "arr": {"when": [{"is_null": False, "background": "#dcfce7"}]},
            },
        )
        data = [{"arr": 42}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_kpi_svg(
            resolved,
            data,
            width=400,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )

        assert "#dcfce7" in svg

    def test_kpi_default_applies_fallback_background(self):
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.render.chart.kpi import render_kpi_svg

        chart = KpiChart(
            id="kpi_default",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="kpi",
            value="arr",
            conditional_formatting={
                "arr": {
                    "when": [
                        {"lt": 0, "background": "#fee2e2"},
                        {"default": True, "background": "#f3f4f6"},
                    ],
                },
            },
        )
        data = [{"arr": 100}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_kpi_svg(
            resolved,
            data,
            width=400,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )

        assert "#f3f4f6" in svg
