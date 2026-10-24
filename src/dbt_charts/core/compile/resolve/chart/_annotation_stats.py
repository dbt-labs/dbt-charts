"""Annotation statistics: aggregates, least-squares fits and their intervals.

Plain-Python ordinary least squares over a small basis. A trend is fitted to
a chart's plotted rows (tens to thousands of points), so the normal equations
are solved directly; an x column is centered and scaled before a linear or
polynomial basis is built so epoch days stay well conditioned.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

AggregateFn = Literal["min", "max", "avg", "median", "percentile"]
FitKind = Literal["linear", "polynomial", "exponential", "log", "power"]
IntervalKind = Literal["confidence", "prediction"]


class FitError(ValueError):
    """The rows cannot support the requested fit."""


class TooFewRows(FitError):
    """The scope has fewer rows than the fit or its interval needs."""


def aggregate(fn: AggregateFn, percent: float | None, values: Sequence[float]) -> float:
    """``fn`` over ``values``; ``percentile`` interpolates linearly between ranks."""
    if not values:
        raise FitError("there are no plotted values to aggregate")
    match fn:
        case "min":
            return min(values)
        case "max":
            return max(values)
        case "avg":
            return statistics.fmean(values)
        case "median":
            return statistics.median(values)
        case "percentile":
            assert percent is not None, "the parser requires percentile's argument"
            ordered = sorted(values)
            rank = percent / 100 * (len(ordered) - 1)
            low = math.floor(rank)
            high = min(low + 1, len(ordered) - 1)
            return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


@dataclass(frozen=True)
class Fit:
    """A fitted curve: ``predict(x)`` and its interval at ``x``."""

    basis: Callable[[float], tuple[float, ...]]
    back: Callable[[float], float]
    coefficients: tuple[float, ...]
    inverse_gram: tuple[tuple[float, ...], ...]
    residual_variance: float
    degrees_of_freedom: int

    def _linear(self, x: float) -> tuple[float, tuple[float, ...]]:
        row = self.basis(x)
        return sum(c * v for c, v in zip(self.coefficients, row, strict=True)), row

    def predict(self, x: float) -> float:
        return self.back(self._linear(x)[0])

    def interval(
        self, x: float, kind: IntervalKind, percent: float
    ) -> tuple[float, float]:
        if self.degrees_of_freedom < 1:
            raise TooFewRows(
                "an interval needs more rows than the fit has coefficients"
            )
        center, row = self._linear(x)
        leverage = sum(
            row[i] * self.inverse_gram[i][j] * row[j]
            for i in range(len(row))
            for j in range(len(row))
        )
        variance = self.residual_variance * (
            leverage + (1.0 if kind == "prediction" else 0.0)
        )
        half = student_t_quantile(
            1 - (1 - percent / 100) / 2, self.degrees_of_freedom
        ) * math.sqrt(max(variance, 0.0))
        low, high = self.back(center - half), self.back(center + half)
        return (min(low, high), max(low, high))


def fit(
    kind: FitKind, order: int | None, xs: Sequence[float], ys: Sequence[float]
) -> Fit:
    """Least-squares ``kind`` fit of ``ys`` on ``xs``."""
    if kind in ("exponential", "power") and any(y <= 0 for y in ys):
        raise FitError(f"a {kind} fit needs every y above zero")
    if kind in ("log", "power") and any(x <= 0 for x in xs):
        raise FitError(f"a {kind} fit needs every x above zero")
    target = [math.log(y) for y in ys] if kind in ("exponential", "power") else ys
    back: Callable[[float], float] = (
        math.exp if kind in ("exponential", "power") else _identity
    )
    basis = _basis(kind, order, xs)
    rows = [basis(x) for x in xs]
    width = len(rows[0])
    if len(rows) < width:
        raise TooFewRows(f"a {kind} fit needs at least {width} plotted points")
    gram = [
        [sum(r[i] * r[j] for r in rows) for j in range(width)] for i in range(width)
    ]
    inverse = _invert(gram)
    moment = [
        sum(r[i] * t for r, t in zip(rows, target, strict=True)) for i in range(width)
    ]
    coefficients = tuple(
        sum(inverse[i][j] * moment[j] for j in range(width)) for i in range(width)
    )
    residual = sum(
        (t - sum(c * v for c, v in zip(coefficients, r, strict=True))) ** 2
        for r, t in zip(rows, target, strict=True)
    )
    dof = len(rows) - width
    return Fit(
        basis=basis,
        back=back,
        coefficients=coefficients,
        inverse_gram=tuple(tuple(row) for row in inverse),
        residual_variance=residual / dof if dof > 0 else 0.0,
        degrees_of_freedom=dof,
    )


def _identity(v: float) -> float:
    return v


def _basis(
    kind: FitKind, order: int | None, xs: Sequence[float]
) -> Callable[[float], tuple[float, ...]]:
    match kind:
        case "log" | "power":
            return lambda x: (1.0, math.log(x))
        case "linear" | "exponential":
            degree = 1
        case "polynomial":
            assert order is not None, "the parser requires polynomial's order"
            degree = order
    center = statistics.fmean(xs)
    # Equal xs leave a singular system, which the solve rejects.
    spread = max(abs(x - center) for x in xs)
    scale = spread or 1.0  # type-state: silent_fallback — see above
    return lambda x: tuple(((x - center) / scale) ** k for k in range(degree + 1))


def _invert(matrix: list[list[float]]) -> list[list[float]]:
    """Gauss-Jordan inverse with partial pivoting."""
    n = len(matrix)
    work = [
        row[:] + [1.0 if i == j else 0.0 for j in range(n)]
        for i, row in enumerate(matrix)
    ]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(work[r][col]))
        if abs(work[pivot][col]) < 1e-12 * max(1.0, abs(matrix[col][col])):
            raise TooFewRows("the plotted x values are too few or all the same")
        work[col], work[pivot] = work[pivot], work[col]
        lead = work[col][col]
        work[col] = [v / lead for v in work[col]]
        for r in range(n):
            if r != col and work[r][col] != 0.0:
                factor = work[r][col]
                work[r] = [
                    a - factor * b for a, b in zip(work[r], work[col], strict=True)
                ]
    return [row[n:] for row in work]


def student_t_quantile(p: float, df: int) -> float:
    """The ``p`` quantile of Student's t with ``df`` degrees of freedom."""
    if not 0.5 <= p < 1:
        raise ValueError(f"quantile {p} is outside [0.5, 1)")
    low, high = 0.0, 1.0
    while _student_t_cdf(high, df) < p:
        high *= 2
    for _ in range(200):
        mid = (low + high) / 2
        if _student_t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _student_t_cdf(t: float, df: int) -> float:
    return 1 - 0.5 * _incomplete_beta(df / 2, 0.5, df / (df + t * t))


def _incomplete_beta(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b), by Lentz's continued fraction."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    if x > (a + 1) / (a + b + 2):
        return 1 - _incomplete_beta(b, a, 1 - x)
    tiny = 1e-300
    c, d = 1.0, 1 - (a + b) * x / (a + 1)
    d = 1 / (d if abs(d) > tiny else tiny)
    result = d
    for m in range(1, 300):
        for numerator in (
            m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
            -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1)),
        ):
            d = 1 + numerator * d
            d = 1 / (d if abs(d) > tiny else tiny)
            c = 1 + numerator / c
            c = c if abs(c) > tiny else tiny
            result *= c * d
        if abs(c * d - 1) < 1e-15:
            break
    return front * result / a
