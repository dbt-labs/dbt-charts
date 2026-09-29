"""Neutral numeric primitives shared by compile and render.

Leaf module — no dependency on compile/, execute/, or render/ (see the
module-dependency-direction section in ``core/AGENTS.md``). Both compile
(real axis tick baking) and render (upper-bound label-width estimation) import
from here.
"""

from __future__ import annotations

import math


def aspect_ratio_height(
    width: float, aspect_ratio: float, min_height: float, max_height: float
) -> float:
    """Height implied by an aspect ratio at a given width, clamped to [min_height, max_height].

    The one canonical form of the ``width / aspect_ratio`` clamp shared by
    ``render/sizing.py``'s ``get_chart_content_height`` (the render-time static
    height estimate) and ``compile/resolve/chart/_axes.py``'s
    ``estimate_cartesian_plot_height`` (the resolve-time plot-height estimate
    every cartesian family, not just bar, builds its own legend-placement and
    legend-yield decisions on). Callers supply their own min/max — the two
    sit at different points in the style cascade (global vs per-family) —
    this only owns the shared arithmetic.
    """
    return max(min_height, min(max_height, width / aspect_ratio))


def nice_tick_values(
    domain_min: float,
    domain_max: float,
    target_count: int,
    *,
    min_step: float | None = None,
) -> list[float]:
    """Return <= target_count tick values at a "nice" step: 1, 2, or 5 x 10^k.

    This mirrors the spirit of d3-scale's tick algorithm, but enforces an upper
    bound on the number of ticks.

    ``min_step``, when given, floors the chosen step: a fixed-decimal axis
    format (``tick_min_step_for_format``, ``compile/format.py``) can't paint
    ticks finer than its own precision as distinct labels, so a ladder step
    below that floor is bumped up to it. ``min_step`` is always a bare power
    of ten (``10 ** -precision``), which is itself one of this function's own
    "nice" candidates, so a bumped step stays nice.

    The floored ladder's own rounded extent may reach past ``[domain_min,
    domain_max]`` on either side, same as an unfloored ladder always can --
    fine as long as at least one rung still lands inside the domain (the
    caller's, not this function's, since a caller may leave its own domain
    bound narrower than this ladder's extent). When none does, this function
    returns its unfloored pick instead: still duplicating under the format,
    but every painted rung is a real, correctly positioned value.

    The important detail is that tick count must be checked against the actual
    rounded extent:

        floor(domain_min / step) * step
        ceil(domain_max / step) * step

    rather than just the raw span. Rounding the extent outward can add extra
    ticks.

    Examples:
        nice_tick_values(0, 4000, 6) -> [0, 1000, 2000, 3000, 4000]
        nice_tick_values(0, 1400, 5) -> [0, 500, 1000, 1500]
        nice_tick_values(0, 60, 6)   -> [0, 20, 40, 60]

        nice_tick_values(99.8, 124.6, 6)
            -> [90, 100, 110, 120, 130]

        The step 5 would produce:
            [95, 100, 105, 110, 115, 120, 125]
        which has 7 ticks, so step 10 is chosen instead.
    """
    if target_count <= 1 or domain_min == domain_max:
        return [round(domain_min, 10)]

    reverse = domain_max < domain_min
    if reverse:
        domain_min, domain_max = domain_max, domain_min

    span = domain_max - domain_min
    raw_step = span / (target_count - 1)

    exp = math.floor(math.log10(raw_step)) if raw_step > 0 else 0
    magnitude = 10.0**exp

    # Include the next magnitude so there is always a fallback candidate.
    nice_steps = [
        magnitude,
        2 * magnitude,
        5 * magnitude,
        10 * magnitude,
    ]

    def rounded_extent_for_step(step: float) -> tuple[float, float]:
        start = math.floor(domain_min / step) * step
        end = math.ceil(domain_max / step) * step
        return start, end

    def tick_count_for_step(step: float) -> int:
        start, end = rounded_extent_for_step(step)
        return round((end - start) / step) + 1

    natural_step = next(
        (
            candidate
            for candidate in nice_steps
            if tick_count_for_step(candidate) <= target_count
        ),
        nice_steps[-1],
    )

    def ticks_for_step(step: float) -> list[float]:
        start, end = rounded_extent_for_step(step)
        intervals = round((end - start) / step)
        return [round(start + i * step, 10) for i in range(intervals + 1)]

    ticks = ticks_for_step(natural_step)
    if min_step is not None and natural_step < min_step:
        floored = ticks_for_step(min_step)
        if any(domain_min <= t <= domain_max for t in floored):
            ticks = floored

    if reverse:
        ticks.reverse()

    return ticks
