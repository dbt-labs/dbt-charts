"""Tests for extended ConditionalRule predicates: between, in, is_null, default.

These four predicates extend the v1 set (eq/ne/lt/lte/gt/gte). Each is a
first-class field on ConditionalRule:

- ``between: [low, high]`` — closed-interval inclusive match on numeric values.
- ``in: [v1, v2, ...]`` — equality set-membership match (same type guards as ``eq``).
- ``is_null: bool`` — true matches None, false matches not-None.
- ``default: true`` — catch-all; only legal as the final rule in a ``when`` list.

Validation rules:
- ConditionalRule still requires exactly one predicate operator.
- ``between`` must be a 2-element list of numbers with low <= high.
- ``in`` must be a non-empty list.
- ``is_null`` must be a boolean (not truthy/falsy).
- ``default`` must equal ``true`` (only legal value).
- ``default`` rules may only appear at the end of a ``when`` list.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestBetweenPredicate:
    def test_between_accepts_two_numbers(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule.model_validate(
            {"between": [0, 100], "background": "#dcfce7"}
        )
        assert rule.between == [0, 100]
        assert match_predicate(rule, 50) is True

    def test_between_accepts_floats(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule.model_validate(
            {"between": [0.5, 99.5], "background": "#dcfce7"}
        )
        assert rule.between == [0.5, 99.5]
        assert match_predicate(rule, 0.6) is True

    def test_between_rejects_non_list(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate({"between": 50, "background": "#dcfce7"})

    def test_between_rejects_wrong_length(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate(
                {"between": [0, 50, 100], "background": "#dcfce7"}
            )
        with pytest.raises(ValidationError):
            ConditionalRule.model_validate({"between": [50], "background": "#dcfce7"})

    def test_between_rejects_low_greater_than_high(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate(
                {"between": [100, 0], "background": "#dcfce7"}
            )

    def test_between_allows_equal_low_high(self) -> None:
        """A degenerate [x, x] interval matches exactly x — allowed."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate(
            {"between": [5, 5], "background": "#dcfce7"}
        )
        assert rule.between == [5, 5]

    def test_between_rejects_non_numeric_bounds(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate(
                {"between": ["a", "z"], "background": "#dcfce7"}
            )


class TestInPredicate:
    def test_in_accepts_string_list(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate(
            {"in": ["At Risk", "Critical"], "background": "#fee2e2"}
        )
        assert rule.in_ == ["At Risk", "Critical"]

    def test_in_accepts_numeric_list(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate(
            {"in": [1, 2, 3], "background": "#fee2e2"}
        )
        assert rule.in_ == [1, 2, 3]

    def test_in_rejects_empty_list(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate({"in": [], "background": "#fee2e2"})

    def test_in_rejects_non_list(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate({"in": "At Risk", "background": "#fee2e2"})

    def test_in_does_not_accept_in_underscore_alias(self) -> None:
        """Author-facing YAML uses ``in:``; the Python attribute is ``in_``
        (since ``in`` is a Python keyword)."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate({"in": ["x"], "background": "#fff"})
        assert rule.in_ == ["x"]


class TestIsNullPredicate:
    def test_is_null_true(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate(
            {"is_null": True, "background": "#e5e7eb"}
        )
        assert rule.is_null is True

    def test_is_null_false(self) -> None:
        """is_null: false means match any non-null value."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate(
            {"is_null": False, "background": "#dcfce7"}
        )
        assert rule.is_null is False

    def test_is_null_rejects_non_bool(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate({"is_null": "yes", "background": "#fff"})


class TestDefaultPredicate:
    def test_default_true_is_valid(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        rule = ConditionalRule.model_validate(
            {"default": True, "background": "#f3f4f6"}
        )
        assert rule.default is True

    def test_default_false_is_rejected(self) -> None:
        """default: false is meaningless — reject it."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate({"default": False, "background": "#f3f4f6"})

    def test_default_rule_still_requires_style_override(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate({"default": True})


class TestExactlyOnePredicateWithNewOps:
    def test_between_plus_eq_rejected(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate(
                {"between": [0, 10], "eq": 5, "background": "#fff"}
            )

    def test_in_plus_lt_rejected(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate(
                {"in": [1, 2], "lt": 5, "background": "#fff"}
            )

    def test_is_null_plus_default_rejected(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate(
                {"is_null": True, "default": True, "background": "#fff"}
            )

    def test_default_plus_eq_rejected(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
        )

        with pytest.raises(ValidationError):
            ConditionalRule.model_validate(
                {"default": True, "eq": 5, "background": "#fff"}
            )


class TestDefaultRulePositionValidation:
    """``default: true`` is only legal as the final rule in a ``when`` list."""

    def test_default_rule_at_end_is_valid(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            FieldConditionalFormatting,
        )

        entry = FieldConditionalFormatting.model_validate(
            {
                "when": [
                    {"lt": 0, "background": "#fee2e2"},
                    {"default": True, "background": "#f3f4f6"},
                ]
            }
        )
        assert len(entry.when) == 2
        assert entry.when[-1].default is True

    def test_default_rule_not_at_end_is_rejected(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            FieldConditionalFormatting,
        )

        with pytest.raises(ValidationError):
            FieldConditionalFormatting.model_validate(
                {
                    "when": [
                        {"default": True, "background": "#f3f4f6"},
                        {"lt": 0, "background": "#fee2e2"},
                    ]
                }
            )

    def test_multiple_default_rules_rejected(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            FieldConditionalFormatting,
        )

        with pytest.raises(ValidationError):
            FieldConditionalFormatting.model_validate(
                {
                    "when": [
                        {"default": True, "background": "#eee"},
                        {"default": True, "background": "#f3f4f6"},
                    ]
                }
            )

    def test_only_default_rule_is_valid(self) -> None:
        """A single ``default: true`` rule is a valid (if unusual) list."""
        from dbt_charts.core.compile.models.chart.authored import (
            FieldConditionalFormatting,
        )

        entry = FieldConditionalFormatting.model_validate(
            {"when": [{"default": True, "background": "#f3f4f6"}]}
        )
        assert entry.when[0].default is True


class TestMatchPredicateBetween:
    def test_match_between_inclusive_low(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(between=[10, 20], background="#dcfce7")
        assert match_predicate(rule, 10) is True

    def test_match_between_inclusive_high(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(between=[10, 20], background="#dcfce7")
        assert match_predicate(rule, 20) is True

    def test_match_between_middle(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(between=[10, 20], background="#dcfce7")
        assert match_predicate(rule, 15) is True

    def test_match_between_below(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(between=[10, 20], background="#dcfce7")
        assert match_predicate(rule, 9) is False

    def test_match_between_above(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(between=[10, 20], background="#dcfce7")
        assert match_predicate(rule, 21) is False

    def test_match_between_none_never_matches(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(between=[10, 20], background="#dcfce7")
        assert match_predicate(rule, None) is False

    def test_match_between_bool_never_matches(self) -> None:
        """Booleans must not slip through the numeric predicate."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(between=[0, 1], background="#dcfce7")
        assert match_predicate(rule, True) is False
        assert match_predicate(rule, False) is False

    def test_match_between_numeric_string_matches(self) -> None:
        # 1B.6b: CSV-typed numeric strings must coerce and match.
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(between=[10, 20], background="#dcfce7")
        assert match_predicate(rule, "15") is True

    def test_match_between_numeric_string_outside_range(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(between=[0, 100], background="#dcfce7")
        assert match_predicate(rule, "-1") is False
        assert match_predicate(rule, "101") is False

    def test_match_between_unparseable_string_never_matches(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(between=[10, 20], background="#dcfce7")
        assert match_predicate(rule, "abc") is False
        assert match_predicate(rule, "") is False


class TestMatchPredicateIn:
    def test_match_in_string(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(**{"in": ["At Risk", "Critical"]}, background="#fee2e2")
        assert match_predicate(rule, "At Risk") is True
        assert match_predicate(rule, "Critical") is True
        assert match_predicate(rule, "OK") is False

    def test_match_in_numeric(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(**{"in": [1, 2, 3]}, background="#fff")
        assert match_predicate(rule, 1) is True
        assert match_predicate(rule, 4) is False

    def test_match_in_none_never_matches(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(**{"in": [1, 2, 3]}, background="#fff")
        assert match_predicate(rule, None) is False

    def test_match_in_bool_vs_int_does_not_cross_match(self) -> None:
        """Consistency with eq: True must not match the number 1 in ``in``."""
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(**{"in": [1, 2]}, background="#fff")
        assert match_predicate(rule, True) is False
        assert match_predicate(rule, 1) is True


class TestMatchPredicateIsNull:
    def test_match_is_null_true_matches_none(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(is_null=True, background="#e5e7eb")
        assert match_predicate(rule, None) is True

    def test_match_is_null_true_does_not_match_zero(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(is_null=True, background="#e5e7eb")
        assert match_predicate(rule, 0) is False

    def test_match_is_null_true_does_not_match_empty_string(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(is_null=True, background="#e5e7eb")
        assert match_predicate(rule, "") is False

    def test_match_is_null_false_matches_any_non_null(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(is_null=False, background="#dcfce7")
        assert match_predicate(rule, 42) is True
        assert match_predicate(rule, "ok") is True
        assert match_predicate(rule, 0) is True
        assert match_predicate(rule, "") is True

    def test_match_is_null_false_does_not_match_none(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(is_null=False, background="#dcfce7")
        assert match_predicate(rule, None) is False


class TestMatchPredicateDefault:
    def test_match_default_always_true(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(default=True, background="#f3f4f6")
        assert match_predicate(rule, None) is True
        assert match_predicate(rule, 0) is True
        assert match_predicate(rule, "anything") is True
        assert match_predicate(rule, 999.5) is True


# ---------------------------------------------------------------------------
# 1B.6b: match_predicate numeric-string coercion (lt/lte/gt/gte)
# ---------------------------------------------------------------------------


class TestMatchPredicateCsvStringCoercion:
    """CSV-loaded numeric strings must fire all numeric predicates."""

    def test_lt_matches_numeric_string(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(lt=100, background="#fee2e2")
        assert match_predicate(rule, "50") is True
        assert match_predicate(rule, "150") is False

    def test_lte_matches_numeric_string(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(lte=100, background="#fee2e2")
        assert match_predicate(rule, "100") is True
        assert match_predicate(rule, "101") is False

    def test_gt_matches_numeric_string(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(gt=0, background="#dcfce7")
        assert match_predicate(rule, "1") is True
        assert match_predicate(rule, "0") is False

    def test_gte_matches_numeric_string(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(gte=0, background="#dcfce7")
        assert match_predicate(rule, "0") is True
        assert match_predicate(rule, "-1") is False

    def test_unparseable_string_still_excluded(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(lt=100, background="#fee2e2")
        assert match_predicate(rule, "abc") is False
        assert match_predicate(rule, "") is False

    def test_bool_still_excluded_from_numeric_predicates(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        # True is 1, False is 0 in Python — both must be rejected.
        rule = ConditionalRule(lt=100, background="#fee2e2")
        assert match_predicate(rule, True) is False
        assert match_predicate(rule, False) is False

    def test_native_int_still_works(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(lt=100, background="#fee2e2")
        assert match_predicate(rule, 50) is True
        assert match_predicate(rule, 150) is False

    def test_native_float_still_works(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(between=[0, 100], background="#dcfce7")
        assert match_predicate(rule, 50.5) is True
        assert match_predicate(rule, 100.1) is False

    def test_inf_and_nan_strings_never_match_numeric_predicates(self) -> None:
        # Guards the isfinite filter in coerce_numeric_cell: "inf"/"nan" must
        # not route around the fix and re-enable the domain-poisoning behavior.
        from dbt_charts.core.compile.models.chart.authored import (
            ConditionalRule,
            match_predicate,
        )

        rule = ConditionalRule(gt=100, background="#fee2e2")
        assert match_predicate(rule, "inf") is False
        assert match_predicate(rule, "Infinity") is False
        assert match_predicate(rule, "nan") is False
        assert match_predicate(rule, float("inf")) is False
        assert match_predicate(rule, float("nan")) is False
