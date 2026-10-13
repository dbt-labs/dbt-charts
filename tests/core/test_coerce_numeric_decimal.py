"""Regression: color scales and conditional formatting silently did nothing on
decimal.Decimal columns (BigQuery NUMERIC/BIGNUMERIC, Postgres numeric,
Snowflake NUMBER).

The root cause was a duplicate coercion helper, ``coerce_numeric``, that
rejected ``Decimal`` — it has been deleted; all call sites now use the canonical
``coerce_numeric_cell`` in ``core/utils.py``, which has always accepted
``Decimal``. The fix also adds a ``math.isfinite`` guard to ``coerce_numeric_cell``
so non-finite values return ``None`` across all call sites, unifying the null rule.

Tests cover the non-finite guard and the render call sites.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from dbt_charts.core.compile.models.chart.authored import (
    ConditionalRule,
    ScaleTargetConfig,
    match_predicate,
)
from dbt_charts.core.render.chart.table_support import compute_scale_domain
from dbt_charts.core.utils import coerce_numeric_cell

from ._engine_render import kpi_gradient_fill

# ---------------------------------------------------------------------------
# coerce_numeric_cell unit tests — the non-finite guard
# ---------------------------------------------------------------------------


class TestCoerceNumericDecimal:
    def test_decimal_returns_float(self) -> None:
        result = coerce_numeric_cell(Decimal("0.4583333333333333"))
        assert result is not None
        assert result == pytest.approx(0.4583333333333333)

    def test_decimal_zero(self) -> None:
        assert coerce_numeric_cell(Decimal("0.0")) == pytest.approx(0.0)

    def test_decimal_negative(self) -> None:
        result = coerce_numeric_cell(Decimal("-12.5"))
        assert result is not None
        assert result == pytest.approx(-12.5)

    def test_decimal_nan_is_none(self) -> None:
        assert coerce_numeric_cell(Decimal("NaN")) is None

    def test_decimal_infinity_is_none(self) -> None:
        assert coerce_numeric_cell(Decimal("Infinity")) is None
        assert coerce_numeric_cell(Decimal("-Infinity")) is None

    def test_bool_still_none(self) -> None:
        """bool guard must survive the Decimal addition."""
        assert coerce_numeric_cell(True) is None
        assert coerce_numeric_cell(False) is None

    def test_int_and_float_unaffected(self) -> None:
        assert coerce_numeric_cell(5) == pytest.approx(5.0)
        assert coerce_numeric_cell(3.14) == pytest.approx(3.14)

    def test_none_is_none(self) -> None:
        assert coerce_numeric_cell(None) is None


# ---------------------------------------------------------------------------
# Call site 1: CF rule comparison (match_predicate)
# ---------------------------------------------------------------------------


class TestMatchPredicateDecimal:
    def test_gt_rule_matches_decimal(self) -> None:
        rule = ConditionalRule(gt=0.3, background="#ff0000")
        assert match_predicate(rule, Decimal("0.4583")) is True

    def test_gt_rule_rejects_small_decimal(self) -> None:
        rule = ConditionalRule(gt=0.5, background="#ff0000")
        assert match_predicate(rule, Decimal("0.1")) is False

    def test_lt_rule_matches_decimal(self) -> None:
        rule = ConditionalRule(lt=1.0, background="#00ff00")
        assert match_predicate(rule, Decimal("0.0")) is True

    def test_between_rule_matches_decimal(self) -> None:
        rule = ConditionalRule.model_validate({"between": [0, 1], "background": "#abc"})
        assert match_predicate(rule, Decimal("0.5")) is True
        assert match_predicate(rule, Decimal("1.5")) is False


# ---------------------------------------------------------------------------
# Call site 2: table scale.background domain (compute_scale_domain)
# ---------------------------------------------------------------------------


class TestComputeScaleDomainDecimal:
    def test_decimal_rows_produce_correct_domain(self) -> None:
        data = [
            {"revenue": Decimal("10.00")},
            {"revenue": Decimal("50.00")},
            {"revenue": Decimal("30.00")},
        ]
        cfg = ScaleTargetConfig(palette=["#000000", "#ffffff"])
        lo, hi = compute_scale_domain(data, "revenue", cfg)
        assert lo == pytest.approx(10.0)
        assert hi == pytest.approx(50.0)

    def test_decimal_booleans_excluded_from_domain(self) -> None:
        """bool guard: True/False must not inflate the domain past [5, 20]."""
        data = [
            {"v": Decimal("5")},
            {"v": True},
            {"v": Decimal("20")},
            {"v": False},
        ]
        cfg = ScaleTargetConfig(palette=["#000", "#fff"])
        lo, hi = compute_scale_domain(data, "v", cfg)
        assert lo == pytest.approx(5.0)
        assert hi == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Call site 3: KPI background.scale (_evaluate_channel_for_row, gradient mode)
# ---------------------------------------------------------------------------


class TestKpiBackgroundScaleDecimal:
    def test_gradient_fill_for_decimal(self) -> None:
        """A Decimal value must produce an interpolated fill, not the null color."""
        fill = kpi_gradient_fill(
            Decimal("0.4583333333333333"), ["#000000", "#ffffff"], 0, 1
        )
        assert fill not in ("#000000", "#ffffff", "none")
