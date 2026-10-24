"""Annotation statistics against textbook values."""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from dbt_charts.core.compile.resolve.chart._annotation_stats import (
    FitError,
    FitKind,
    aggregate,
    fit,
    student_t_quantile,
)


@pytest.mark.parametrize(
    ("p", "df", "expected"),
    [
        (0.975, 1, 12.706205),
        (0.975, 10, 2.228139),
        (0.95, 5, 2.015048),
        (0.995, 30, 2.749996),
    ],
)
def test_student_t_quantile_matches_the_tables(
    p: float, df: int, expected: float
) -> None:
    assert student_t_quantile(p, df) == pytest.approx(expected, abs=1e-5)


def test_aggregates() -> None:
    values = [4.0, 1.0, 3.0, 2.0]
    assert aggregate("min", None, values) == 1
    assert aggregate("max", None, values) == 4
    assert aggregate("avg", None, values) == 2.5
    assert aggregate("median", None, values) == 2.5
    assert aggregate("percentile", 25, values) == pytest.approx(1.75)
    assert aggregate("percentile", 100, values) == 4
    with pytest.raises(FitError, match="no plotted values"):
        aggregate("avg", None, [])


def test_linear_fit_recovers_the_line_and_a_known_interval() -> None:
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [2.0, 4.1, 5.9, 8.2, 9.8]
    line = fit("linear", None, xs, ys)
    # slope 1.97, intercept 0.09 (hand-computed OLS)
    assert line.predict(0) == pytest.approx(0.09)
    assert line.predict(1) - line.predict(0) == pytest.approx(1.97)
    low, high = line.interval(3, "confidence", 95)
    # residual SS 0.091 on 3 dof; se at the mean x = s / sqrt(5); t(0.975, 3) = 3.182446
    half = 3.182446 * math.sqrt(0.091 / 3) / math.sqrt(5)
    assert (low, high) == pytest.approx((6.0 - half, 6.0 + half), abs=1e-5)
    p_low, p_high = line.interval(3, "prediction", 95)
    assert p_high - p_low > high - low


@pytest.mark.parametrize(
    ("kind", "order", "truth"),
    [
        ("polynomial", 2, lambda x: 3 - 2 * x + 0.5 * x * x),
        ("exponential", None, lambda x: 2 * math.exp(0.3 * x)),
        ("log", None, lambda x: 1 + 4 * math.log(x)),
        ("power", None, lambda x: 3 * x**1.5),
    ],
)
def test_each_fit_recovers_an_exact_curve(
    kind: FitKind, order: int | None, truth: Callable[[float], float]
) -> None:
    xs = [float(x) for x in range(1, 12)]
    curve = fit(kind, order, xs, [truth(x) for x in xs])
    for x in (1.5, 6.0, 14.0):
        assert curve.predict(x) == pytest.approx(truth(x), rel=1e-9)


def test_polynomial_on_epoch_days_stays_conditioned() -> None:
    xs = [19723.0 + 30 * i for i in range(24)]
    ys = [5 + 0.001 * (x - 19723) ** 2 for x in xs]
    curve = fit("polynomial", 3, xs, ys)
    assert curve.predict(xs[-1]) == pytest.approx(ys[-1], rel=1e-9)


@pytest.mark.parametrize("kind", ["linear", "exponential"])
def test_a_daily_series_in_epoch_days_fits(kind: FitKind) -> None:
    xs = [20700.0 + i for i in range(10)]
    ys = [math.exp(0.1 * i) for i in range(10)]
    curve = fit(kind, None, xs, ys)
    curve.interval(xs[5], "confidence", 95)
    if kind == "exponential":
        assert curve.predict(xs[-1]) == pytest.approx(ys[-1], rel=1e-9)


def test_fits_refuse_rows_they_cannot_take() -> None:
    with pytest.raises(FitError, match="above zero"):
        fit("exponential", None, [1.0, 2.0], [1.0, -1.0])
    with pytest.raises(FitError, match="above zero"):
        fit("log", None, [0.0, 2.0], [1.0, 1.0])
    with pytest.raises(FitError, match="all the same"):
        fit("linear", None, [3.0, 3.0, 3.0], [1.0, 2.0, 3.0])
    with pytest.raises(FitError, match="at least 3"):
        fit("polynomial", 2, [1.0, 2.0], [1.0, 2.0])
    with pytest.raises(FitError, match="more rows"):
        fit("linear", None, [1.0, 2.0], [1.0, 2.0]).interval(1.5, "confidence", 95)
