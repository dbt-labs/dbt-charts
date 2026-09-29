"""Unit tests for nice_tick_values — the d3-style "nice step" tick algorithm.

Mirrors the d3-scale tick contract but enforces ≤ N rather than ≈ N, so
editorial themes get predictable tick counts regardless of axis range.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.numeric import nice_tick_values


@pytest.mark.parametrize(
    ("domain_min", "domain_max", "target_count", "expected"),
    [
        # Canonical case from the task description: 0–4000 with target 6.
        # raw_step=800 → step=1000 (first nice step with ≤ 6 ticks) → 5 ticks.
        (0, 4000, 6, [0.0, 1000.0, 2000.0, 3000.0, 4000.0]),
        # 0–1400, target 5: raw_step=350 → step=500 → 4 ticks.
        (0, 1400, 5, [0.0, 500.0, 1000.0, 1500.0]),
        # 0–60, target 6: raw_step=12 → step=20 → 4 ticks.
        (0, 60, 6, [0.0, 20.0, 40.0, 60.0]),
        # 0–3000, target 6: raw_step=600 → step=1000 → 4 ticks.
        (0, 3000, 6, [0.0, 1000.0, 2000.0, 3000.0]),
        # Small range: 0–4, target 6: raw_step=0.8 → step=1 → 5 ticks.
        (0, 4, 6, [0.0, 1.0, 2.0, 3.0, 4.0]),
        # Negative-spanning range: -50 to 200, target 6: raw_step=50 → step=50 → 6 ticks.
        (-50, 200, 6, [-50.0, 0.0, 50.0, 100.0, 150.0, 200.0]),
        # Regression: step=5 gives start=95, end=125 → 7 ticks despite ceil(24.8/5)+1=6.
        # Fix: check actual rounded extent; step falls back to 10 → [90..130] (5 ticks).
        (99.8, 124.6, 6, [90.0, 100.0, 110.0, 120.0, 130.0]),
    ],
)
def test_nice_tick_values_cases(domain_min, domain_max, target_count, expected):
    result = nice_tick_values(domain_min, domain_max, target_count)
    assert result == expected, (
        f"nice_tick_values({domain_min}, {domain_max}, {target_count}) "
        f"returned {result}; expected {expected}"
    )


def test_nice_tick_values_count_lte_target():
    """All cases must return ≤ target_count ticks."""
    cases = [
        (0, 4000, 6),
        (0, 1400, 5),
        (0, 100, 6),
        (0, 0.5, 6),
        (-100, 300, 5),
        (0, 10000, 6),
        (99.8, 124.6, 6),  # regression: rounded extent wider than raw span
        (13.78, 25.0, 6),
        (0, 26.91, 6),
        (97000, 168000, 6),
    ]
    for domain_min, domain_max, target in cases:
        result = nice_tick_values(domain_min, domain_max, target)
        assert len(result) <= target, (
            f"nice_tick_values({domain_min}, {domain_max}, {target}) returned "
            f"{len(result)} values (> {target}): {result}"
        )


def test_nice_tick_values_equal_domain():
    """Degenerate case: min == max returns a single tick."""
    result = nice_tick_values(500, 500, 6)
    assert result == [500]


def test_nice_tick_values_span_domain():
    """Returned values must span the full domain (first ≤ min, last ≥ max)."""
    cases = [(0, 4000, 6), (0, 1400, 5), (10, 90, 6)]
    for domain_min, domain_max, target in cases:
        result = nice_tick_values(domain_min, domain_max, target)
        assert result[0] <= domain_min, (
            f"first tick {result[0]} > domain_min {domain_min}"
        )
        assert result[-1] >= domain_max, (
            f"last tick {result[-1]} < domain_max {domain_max}"
        )


def test_min_step_bumps_a_finer_natural_step():
    # Unfloored, (0, 2, 6) picks step 0.5 -- an integer format's min_step: 1.0
    # must bump it up, not leave 0.5 in place.
    assert nice_tick_values(0, 2, 6, min_step=1.0) == [0.0, 1.0, 2.0]


def test_min_step_is_a_no_op_when_already_coarser():
    # The natural step (1000) is already coarser than min_step (1.0) -- no change.
    assert nice_tick_values(0, 4000, 6, min_step=1.0) == [
        0.0,
        1000.0,
        2000.0,
        3000.0,
        4000.0,
    ]


def test_min_step_with_no_rung_in_domain_falls_back_to_unfloored():
    # min_step (1.0) is wider than the whole domain span (10.7 - 10.2 = 0.5):
    # the floored ladder's own rungs (10.0, 11.0) both fall outside
    # [10.2, 10.7] -- Vega-Lite would clip both, leaving a blank axis. The
    # function falls back to its unfloored pick instead, which still
    # duplicates under the format but paints only real, correctly
    # positioned values.
    result = nice_tick_values(10.2, 10.7, 6, min_step=1.0)
    assert result == nice_tick_values(10.2, 10.7, 6)


def test_min_step_with_a_rung_in_domain_bakes_the_floored_ladder():
    # domain_min (0.0) is itself inside [0.0, 0.5508], so the floored ladder
    # (0.0, 1.0) has a real in-domain rung even though it also extends past
    # domain_max -- that's fine, VL clips the out-of-domain rung and paints
    # the in-domain one at its true position.
    assert nice_tick_values(0.0, 0.5508, 6, min_step=1.0) == [0.0, 1.0]


def test_min_step_equal_to_span_still_bakes_the_boundary_rungs():
    # span == min_step is the boundary: the two rungs bracketing the domain
    # are exactly domain_min and domain_max themselves, both real, both
    # distinct -- a legitimate on-grid ladder, not the "no rung fits" case.
    assert nice_tick_values(10.0, 11.0, 6, min_step=1.0) == [10.0, 11.0]
