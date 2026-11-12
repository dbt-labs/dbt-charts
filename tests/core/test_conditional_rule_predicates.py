"""Tests for extended ConditionalPredicate predicates: between, in, is_null, default.

These four predicates extend the v1 set (eq/ne/lt/lte/gt/gte). Each is a
first-class field on ConditionalPredicate:

- ``between: [low, high]`` — closed-interval inclusive match on numeric values.
- ``in: [v1, v2, ...]`` — equality set-membership match (same type guards as ``eq``).
- ``is_null: bool`` — true matches None, false matches not-None.
- ``default: true`` — catch-all; only legal as the final rule in a ``when`` list.

Validation rules:
- ConditionalPredicate still requires exactly one predicate operator.
- ``between`` must be a 2-element list of numbers with low <= high.
- ``in`` must be a non-empty list.
- ``is_null`` must be a boolean (not truthy/falsy).
- ``default`` must equal ``true`` (only legal value).
- ``default`` rules may only appear at the end of a ``when`` list.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.conditional_predicate import (
    ConditionalPredicate,
    default_rule_last,
)


class TestBetweenPredicate:
    def test_between_accepts_two_numbers(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate.model_validate({"between": [0, 100]})
        assert rule.between == [0, 100]
        assert match_predicate(rule, 50) is True

    def test_between_accepts_floats(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate.model_validate({"between": [0.5, 99.5]})
        assert rule.between == [0.5, 99.5]
        assert match_predicate(rule, 0.6) is True

    def test_between_rejects_non_list(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        with pytest.raises(ValidationError):
            ConditionalPredicate.model_validate({"between": 50})

    def test_between_rejects_wrong_length(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        with pytest.raises(ValidationError):
            ConditionalPredicate.model_validate({"between": [0, 50, 100]})
        with pytest.raises(ValidationError):
            ConditionalPredicate.model_validate({"between": [50]})

    def test_between_rejects_low_greater_than_high(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        with pytest.raises(ValidationError):
            ConditionalPredicate.model_validate({"between": [100, 0]})

    def test_between_allows_equal_low_high(self) -> None:
        """A degenerate [x, x] interval matches exactly x — allowed."""
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        rule = ConditionalPredicate.model_validate({"between": [5, 5]})
        assert rule.between == [5, 5]

    def test_between_rejects_non_numeric_bounds(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        with pytest.raises(ValidationError):
            ConditionalPredicate.model_validate({"between": ["a", "z"]})


class TestInPredicate:
    def test_in_accepts_string_list(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        rule = ConditionalPredicate.model_validate({"in": ["At Risk", "Critical"]})
        assert rule.in_ == ["At Risk", "Critical"]

    def test_in_accepts_numeric_list(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        rule = ConditionalPredicate.model_validate({"in": [1, 2, 3]})
        assert rule.in_ == [1, 2, 3]

    def test_in_rejects_empty_list(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        with pytest.raises(ValidationError):
            ConditionalPredicate.model_validate({"in": []})

    def test_in_rejects_non_list(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        with pytest.raises(ValidationError):
            ConditionalPredicate.model_validate({"in": "At Risk"})

    def test_in_does_not_accept_in_underscore_alias(self) -> None:
        """Author-facing YAML uses ``in:``; the Python attribute is ``in_``
        (since ``in`` is a Python keyword)."""
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        rule = ConditionalPredicate.model_validate({"in": ["x"]})
        assert rule.in_ == ["x"]


class TestIsNullPredicate:
    def test_is_null_true(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        rule = ConditionalPredicate.model_validate({"is_null": True})
        assert rule.is_null is True

    def test_is_null_false(self) -> None:
        """is_null: false means match any non-null value."""
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        rule = ConditionalPredicate.model_validate({"is_null": False})
        assert rule.is_null is False

    def test_is_null_rejects_non_bool(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        with pytest.raises(ValidationError):
            ConditionalPredicate.model_validate({"is_null": "yes"})


class TestDefaultPredicate:
    def test_default_true_is_valid(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        rule = ConditionalPredicate.model_validate({"default": True})
        assert rule.default is True

    def test_default_false_is_rejected(self) -> None:
        """default: false is meaningless — reject it."""
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        with pytest.raises(ValidationError):
            ConditionalPredicate.model_validate({"default": False})


class TestExactlyOnePredicateWithNewOps:
    def test_between_plus_eq_rejected(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        with pytest.raises(ValidationError):
            ConditionalPredicate.model_validate({"between": [0, 10], "eq": 5})

    def test_in_plus_lt_rejected(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        with pytest.raises(ValidationError):
            ConditionalPredicate.model_validate({"in": [1, 2], "lt": 5})

    def test_is_null_plus_default_rejected(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        with pytest.raises(ValidationError):
            ConditionalPredicate.model_validate({"is_null": True, "default": True})

    def test_default_plus_eq_rejected(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
        )

        with pytest.raises(ValidationError):
            ConditionalPredicate.model_validate({"default": True, "eq": 5})


class TestDefaultRulePositionValidation:
    """``default: true`` is only legal as the final rule in a column's list."""

    @staticmethod
    def _rules(*specs: dict) -> list[ConditionalPredicate]:
        return [ConditionalPredicate.model_validate(spec) for spec in specs]

    def test_default_rule_at_end_is_valid(self) -> None:
        rules = self._rules({"lt": 0}, {"default": True})
        assert default_rule_last(rules) == rules

    def test_default_rule_not_at_end_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be the last"):
            default_rule_last(self._rules({"default": True}, {"lt": 0}))

    def test_multiple_default_rules_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be the last"):
            default_rule_last(self._rules({"default": True}, {"default": True}))

    def test_only_default_rule_is_valid(self) -> None:
        rules = self._rules({"default": True})
        assert default_rule_last(rules) == rules


class TestMatchPredicateBetween:
    def test_match_between_inclusive_low(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(between=[10, 20])
        assert match_predicate(rule, 10) is True

    def test_match_between_inclusive_high(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(between=[10, 20])
        assert match_predicate(rule, 20) is True

    def test_match_between_middle(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(between=[10, 20])
        assert match_predicate(rule, 15) is True

    def test_match_between_below(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(between=[10, 20])
        assert match_predicate(rule, 9) is False

    def test_match_between_above(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(between=[10, 20])
        assert match_predicate(rule, 21) is False

    def test_match_between_none_never_matches(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(between=[10, 20])
        assert match_predicate(rule, None) is False

    def test_match_between_bool_never_matches(self) -> None:
        """Booleans must not slip through the numeric predicate."""
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(between=[0, 1])
        assert match_predicate(rule, True) is False
        assert match_predicate(rule, False) is False

    def test_match_between_numeric_string_matches(self) -> None:
        # 1B.6b: CSV-typed numeric strings must coerce and match.
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(between=[10, 20])
        assert match_predicate(rule, "15") is True

    def test_match_between_numeric_string_outside_range(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(between=[0, 100])
        assert match_predicate(rule, "-1") is False
        assert match_predicate(rule, "101") is False

    def test_match_between_unparseable_string_never_matches(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(between=[10, 20])
        assert match_predicate(rule, "abc") is False
        assert match_predicate(rule, "") is False


class TestMatchPredicateIn:
    def test_match_in_string(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(**{"in": ["At Risk", "Critical"]})
        assert match_predicate(rule, "At Risk") is True
        assert match_predicate(rule, "Critical") is True
        assert match_predicate(rule, "OK") is False

    def test_match_in_numeric(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(**{"in": [1, 2, 3]})
        assert match_predicate(rule, 1) is True
        assert match_predicate(rule, 4) is False

    def test_match_in_none_never_matches(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(**{"in": [1, 2, 3]})
        assert match_predicate(rule, None) is False

    def test_match_in_bool_vs_int_does_not_cross_match(self) -> None:
        """Consistency with eq: True must not match the number 1 in ``in``."""
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(**{"in": [1, 2]})
        assert match_predicate(rule, True) is False
        assert match_predicate(rule, 1) is True


class TestMatchPredicateIsNull:
    def test_match_is_null_true_matches_none(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(is_null=True)
        assert match_predicate(rule, None) is True

    def test_match_is_null_true_does_not_match_zero(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(is_null=True)
        assert match_predicate(rule, 0) is False

    def test_match_is_null_true_does_not_match_empty_string(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(is_null=True)
        assert match_predicate(rule, "") is False

    def test_match_is_null_false_matches_any_non_null(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(is_null=False)
        assert match_predicate(rule, 42) is True
        assert match_predicate(rule, "ok") is True
        assert match_predicate(rule, 0) is True
        assert match_predicate(rule, "") is True

    def test_match_is_null_false_does_not_match_none(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(is_null=False)
        assert match_predicate(rule, None) is False


class TestMatchPredicateDefault:
    def test_match_default_always_true(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(default=True)
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
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(lt=100)
        assert match_predicate(rule, "50") is True
        assert match_predicate(rule, "150") is False

    def test_lte_matches_numeric_string(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(lte=100)
        assert match_predicate(rule, "100") is True
        assert match_predicate(rule, "101") is False

    def test_gt_matches_numeric_string(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(gt=0)
        assert match_predicate(rule, "1") is True
        assert match_predicate(rule, "0") is False

    def test_gte_matches_numeric_string(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(gte=0)
        assert match_predicate(rule, "0") is True
        assert match_predicate(rule, "-1") is False

    def test_unparseable_string_still_excluded(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(lt=100)
        assert match_predicate(rule, "abc") is False
        assert match_predicate(rule, "") is False

    def test_bool_still_excluded_from_numeric_predicates(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        # True is 1, False is 0 in Python — both must be rejected.
        rule = ConditionalPredicate(lt=100)
        assert match_predicate(rule, True) is False
        assert match_predicate(rule, False) is False

    def test_native_int_still_works(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(lt=100)
        assert match_predicate(rule, 50) is True
        assert match_predicate(rule, 150) is False

    def test_native_float_still_works(self) -> None:
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(between=[0, 100])
        assert match_predicate(rule, 50.5) is True
        assert match_predicate(rule, 100.1) is False

    def test_inf_and_nan_strings_never_match_numeric_predicates(self) -> None:
        # Guards the isfinite filter in coerce_numeric_cell: "inf"/"nan" must
        # not route around the fix and re-enable the domain-poisoning behavior.
        from dbt_charts.core.compile.models.conditional_predicate import (
            ConditionalPredicate,
            match_predicate,
        )

        rule = ConditionalPredicate(gt=100)
        assert match_predicate(rule, "inf") is False
        assert match_predicate(rule, "Infinity") is False
        assert match_predicate(rule, "nan") is False
        assert match_predicate(rule, float("inf")) is False
        assert match_predicate(rule, float("nan")) is False
