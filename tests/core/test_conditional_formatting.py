"""Tests for conditional formatting: predicate operators and table/KPI rendering."""

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


class TestEvaluateCondition:
    """Test individual condition operators."""

    def test_eq_matches_string(self):
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(eq="At Risk")
        assert match_predicate(rule, "At Risk") is True
        assert match_predicate(rule, "On Track") is False

    def test_eq_matches_number(self):
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(eq=42)
        assert match_predicate(rule, 42) is True
        assert match_predicate(rule, 43) is False

    def test_ne_operator(self):
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(ne="OK")
        assert match_predicate(rule, "Bad") is True
        assert match_predicate(rule, "OK") is False

    def test_lt_operator(self):
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(lt=0)
        assert match_predicate(rule, -5) is True
        assert match_predicate(rule, 0) is False
        assert match_predicate(rule, 10) is False

    def test_lte_operator(self):
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(lte=0)
        assert match_predicate(rule, -5) is True
        assert match_predicate(rule, 0) is True
        assert match_predicate(rule, 1) is False

    def test_gt_operator(self):
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(gt=100)
        assert match_predicate(rule, 200) is True
        assert match_predicate(rule, 100) is False

    def test_gte_operator(self):
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(gte=100)
        assert match_predicate(rule, 100) is True
        assert match_predicate(rule, 99) is False

    def test_none_value_never_matches(self):
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(eq=0)
        assert match_predicate(rule, None) is False

    def test_type_mismatch_does_not_crash(self):
        """Comparing string value against numeric operator returns False, not error."""
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(lt=0)
        assert match_predicate(rule, "hello") is False

    def test_eq_zero_does_not_match_false(self):
        """Guard against Python's bool ⊂ int: eq: 0 should not match False."""
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(eq=0)
        assert match_predicate(rule, False) is False
        assert match_predicate(rule, 0) is True

    def test_ne_zero_does_not_match_true_as_not_equal(self):
        """ne: 0 should not interact with True/False."""
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(ne=0)
        assert match_predicate(rule, True) is True  # different types
        assert match_predicate(rule, 0) is False

    def test_bool_excluded_from_numeric_operators(self):
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(gt=0)
        assert match_predicate(rule, True) is False


# ---------------------------------------------------------------------------
# Style resolution tests
# ---------------------------------------------------------------------------


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

        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "amount": TableColumnConfig(
                        format=",.0f",
                        background="#ffffff",
                        conditional=[
                            {
                                "lt": 0,
                                "background": "#fee2e2",
                                "font": {"color": "#991b1b"},
                            }
                        ],
                    )
                },
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

        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "amount": TableColumnConfig(
                        background="#ffffff",
                        conditional=[{"lt": 0, "background": "#fee2e2"}],
                    )
                },
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
        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "status": {
                        "conditional": [
                            {
                                "eq": "Critical",
                                "background": "#fee2e2",
                                "font": {"color": "#991b1b", "weight": "bold"},
                            }
                        ]
                    }
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
        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "amount": {
                        "conditional": [{"gte": 1000, "font": {"weight": "bold"}}]
                    }
                }
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
        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "amount": {"conditional": [{"lt": 0, "background": "#fee2e2"}]},
                    "status": {
                        "conditional": [{"eq": "Critical", "background": "#fecaca"}]
                    },
                }
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

        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "revenue": TableColumnConfig(
                        background="_bg_col",
                        conditional=[{"lt": 0, "background": "#fee2e2"}],
                    )
                },
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
        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "status": {
                        "conditional": [
                            {
                                "eq": "Pending",
                                "font": {"style": "italic"},
                            }
                        ]
                    }
                }
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

        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "status": {
                        "conditional": [
                            {
                                "eq": "open",
                                "font": {"color": "category[1]"},
                            }
                        ]
                    }
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

    def test_category_token_indexes_the_boards_own_palette(self, make_chart):
        import dataclasses

        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "status": {
                        "conditional": [
                            {"eq": "open", "font": {"color": "category[2]"}}
                        ]
                    }
                }
            },
        )
        data = [{"status": "open"}]
        board = dataclasses.replace(_BOARD_STYLE, palette=["#B5427F", "#112233"])
        chart = resolve(chart, data, chart_style_context=board)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert 'fill="#112233"' in svg

    def test_conditional_strikethrough_renders_in_svg(self, make_chart):
        """font.decoration=line-through emits text-decoration on matching rows."""
        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "amount": {
                        "conditional": [
                            {
                                "lt": 0,
                                "font": {"decoration": "line-through"},
                            }
                        ]
                    }
                }
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
        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "amount": {
                        "conditional": [
                            {
                                "between": [10, 20],
                                "background": "#dcfce7",
                            }
                        ]
                    }
                }
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
        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "amount": {
                        "conditional": [
                            {
                                "between": [10, 20],
                                "background": "#dcfce7",
                            }
                        ]
                    }
                }
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
        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "status": {
                        "conditional": [
                            {
                                "in": ["At Risk", "Critical"],
                                "background": "#fee2e2",
                                "font": {"color": "#991b1b"},
                            }
                        ]
                    }
                }
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
        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "amount": {
                        "conditional": [{"is_null": True, "background": "#e5e7eb"}]
                    }
                }
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
        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "amount": {
                        "conditional": [{"is_null": False, "background": "#dcfce7"}]
                    }
                }
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
        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "amount": {
                        "conditional": [
                            {"lt": 0, "background": "#fee2e2"},
                            {"gt": 100, "background": "#dcfce7"},
                            {"default": True, "background": "#f3f4f6"},
                        ]
                    }
                }
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
        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "amount": {
                        "conditional": [
                            {"lt": 0, "background": "#fee2e2"},
                            {"default": True, "background": "#f3f4f6"},
                        ]
                    }
                }
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

        from ._engine_render import render_kpi_svg

        chart = KpiChart(
            id="kpi_notnull",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="kpi",
            value="arr",
            style={
                "conditional": {"arr": [{"is_null": False, "background": "#dcfce7"}]}
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

        from ._engine_render import render_kpi_svg

        chart = KpiChart(
            id="kpi_default",
            query=SqlQuery(sql="SELECT 1", source="test"),
            query_name="q",
            type="kpi",
            value="arr",
            style={
                "conditional": {
                    "arr": [
                        {"lt": 0, "background": "#fee2e2"},
                        {"default": True, "background": "#f3f4f6"},
                    ]
                }
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
