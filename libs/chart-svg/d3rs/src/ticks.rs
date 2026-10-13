// Ported from dbt-charts/src/dbt_charts/core/numeric.py — nice_tick_values.
// Same nice-step selection and inclusive-bounds behavior as the Python original.

/// Return ≤ `count` tick values at a "nice" step: 1, 2, or 5 × 10^k.
///
/// Mirrors d3-scale's tick algorithm with an upper bound on tick count.
/// `lo == hi` → `[lo]`. Reverse domain (`lo > hi`) → descending ticks.
///
/// `min_step`, when given, floors the chosen step: a fixed-decimal axis
/// format cannot paint ticks finer than its own precision as distinct labels,
/// so a step below the floor is bumped up to it (always a bare power of ten,
/// itself a "nice" candidate). The floored ladder is used only when at least
/// one of its rungs lands inside `[lo, hi]`; otherwise the unfloored pick
/// stands.
pub fn nice_tick_values(lo: f64, hi: f64, count: usize, min_step: Option<f64>) -> Vec<f64> {
    if count <= 1 || lo == hi {
        return vec![round10(lo)];
    }

    let (reverse, domain_min, domain_max) = if hi < lo {
        (true, hi, lo)
    } else {
        (false, lo, hi)
    };

    let span = domain_max - domain_min;
    let raw_step = span / (count - 1) as f64;
    let exp = if raw_step > 0.0 {
        raw_step.log10().floor() as i32
    } else {
        0
    };
    let mag = 10f64.powi(exp);
    let candidates = [mag, 2.0 * mag, 5.0 * mag, 10.0 * mag];

    let natural_step = candidates
        .iter()
        .copied()
        .find(|&s| tick_count(domain_min, domain_max, s) <= count)
        .unwrap_or(candidates[3]);

    let ticks_for_step = |step: f64| -> Vec<f64> {
        let start = (domain_min / step).floor() * step;
        let end = (domain_max / step).ceil() * step;
        let intervals = ((end - start) / step).round() as usize;
        (0..=intervals)
            .map(|i| round10(start + i as f64 * step))
            .collect()
    };

    let mut ticks = ticks_for_step(natural_step);
    if let Some(floor) = min_step.filter(|floor| natural_step < *floor) {
        let floored = ticks_for_step(floor);
        if floored.iter().any(|t| domain_min <= *t && *t <= domain_max) {
            ticks = floored;
        }
    }

    if reverse {
        ticks.reverse();
    }
    ticks
}

const E10: f64 = 7.0710678118654755; // sqrt(50)
const E5: f64 = 3.1622776601683795; // sqrt(10)
const E2: f64 = std::f64::consts::SQRT_2;

/// d3-array `ticks(start, stop, count)` — the exact algorithm Vega runs for
/// an axis with no baked tick ladder: every tick lies INSIDE the domain
/// (unlike `nice_tick_values`, production's cover-ladder, whose ticks
/// extend to enclose it).
pub fn d3_ticks(start: f64, stop: f64, count: usize) -> Vec<f64> {
    if count == 0 {
        return vec![];
    }
    if start == stop {
        return vec![start];
    }
    let reverse = stop < start;
    let (lo, hi) = if reverse {
        (stop, start)
    } else {
        (start, stop)
    };
    let (i1, i2, inc) = tick_spec(lo, hi, count);
    if i2 < i1 {
        return vec![];
    }
    let n = (i2 - i1 + 1) as usize;
    let mut out: Vec<f64> = (0..n)
        .map(|i| {
            let v = i1 + i as i64;
            if inc < 0.0 {
                v as f64 / -inc
            } else {
                v as f64 * inc
            }
        })
        .collect();
    if reverse {
        out.reverse();
    }
    out
}

/// Vega's `tickCount` (vega-scale `ticks.js`, as bundled by vl-convert 1.9,
/// vega v6.2.0) for a numeric `count` on a linear scale: an axis's
/// `tickMinStep` caps the count at `floor(span(domain) / minStep || 1) + 1`,
/// `span` being vega-util's `(last - first) || 0` of the domain in scale
/// order — so a descending domain caps negative and draws no ticks, exactly
/// as Vega does. The cap alone is the whole rule here: the `tickStep` loop
/// a later vega release adds after it is not in the oracle, so a span just
/// over a multiple of `minStep` can still tick finer than `minStep`.
/// `None` is an axis with no `tickMinStep`.
pub fn vega_tick_count(start: f64, stop: f64, count: usize, min_step: Option<f64>) -> usize {
    let Some(min_step) = min_step else {
        return count;
    };
    let quotient = (stop - start) / min_step;
    let quotient = if quotient == 0.0 || quotient.is_nan() {
        1.0
    } else {
        quotient
    };
    (count as f64).min(quotient.floor() + 1.0).max(0.0) as usize
}

fn tick_spec(start: f64, stop: f64, count: usize) -> (i64, i64, f64) {
    let spec = tick_spec_once(start, stop, count);
    // d3's tickSpec retries at count*2 when a count of 1 yields an empty
    // ladder (`if (i2 < i1 && 0.5 <= count && count < 2)`), so a domain too
    // narrow for one round tick still gets a ladder instead of nothing.
    if spec.1 < spec.0 && count == 1 {
        return tick_spec_once(start, stop, 2);
    }
    spec
}

/// d3-array `tickIncrement`: the tick step for [start, stop] at `count`,
/// encoded d3-style — positive = the step itself; negative = −1/step for
/// sub-unit steps (so integer arithmetic stays exact).
fn tick_increment(start: f64, stop: f64, count: usize) -> f64 {
    let step = (stop - start) / count.max(1) as f64;
    let power = step.log10().floor();
    let error = step / 10f64.powf(power);
    let factor = if error >= E10 {
        10.0
    } else if error >= E5 {
        5.0
    } else if error >= E2 {
        2.0
    } else {
        1.0
    };
    if power < 0.0 {
        -(10f64.powf(-power)) / factor
    } else {
        10f64.powf(power) * factor
    }
}

/// d3-array `tickStep(start, stop, count)`: [`tick_increment`] decoded back
/// into a plain positive step. d3-time's `utcTickInterval` calls it to size
/// the multi-year rung of its ladder (`year.every(tickStep(start/durationYear,
/// stop/durationYear, count))`).
pub fn d3_tick_step(start: f64, stop: f64, count: usize) -> f64 {
    let reverse = stop < start;
    let inc = if reverse {
        tick_increment(stop, start, count)
    } else {
        tick_increment(start, stop, count)
    };
    if inc < 0.0 {
        -1.0 / inc
    } else {
        inc
    }
}

/// d3-array `nice(start, stop, count)` — the domain extension Vega's linear
/// scale applies for `nice: true` (d3's default count 10): widen both ends
/// to the tick increment, re-deriving the increment until it stabilizes.
pub fn d3_nice(mut start: f64, mut stop: f64, count: usize) -> (f64, f64) {
    let mut prestep: Option<f64> = None;
    loop {
        let step = tick_increment(start, stop, count);
        if prestep == Some(step) || step == 0.0 || !step.is_finite() {
            return (start, stop);
        }
        if step > 0.0 {
            start = (start / step).floor() * step;
            stop = (stop / step).ceil() * step;
        } else {
            start = (start * step).ceil() / step;
            stop = (stop * step).floor() / step;
        }
        prestep = Some(step);
    }
}

fn tick_spec_once(start: f64, stop: f64, count: usize) -> (i64, i64, f64) {
    let step = (stop - start) / count.max(1) as f64;
    let power = step.log10().floor();
    let error = step / 10f64.powf(power);
    let factor = if error >= E10 {
        10.0
    } else if error >= E5 {
        5.0
    } else if error >= E2 {
        2.0
    } else {
        1.0
    };
    if power < 0.0 {
        let inc = 10f64.powf(-power) / factor;
        let mut i1 = (start * inc).round() as i64;
        let mut i2 = (stop * inc).round() as i64;
        if (i1 as f64) / inc < start {
            i1 += 1;
        }
        if (i2 as f64) / inc > stop {
            i2 -= 1;
        }
        (i1, i2, -inc)
    } else {
        let inc = 10f64.powf(power) * factor;
        let mut i1 = (start / inc).round() as i64;
        let mut i2 = (stop / inc).round() as i64;
        if i1 as f64 * inc < start {
            i1 += 1;
        }
        if i2 as f64 * inc > stop {
            i2 -= 1;
        }
        (i1, i2, inc)
    }
}

fn tick_count(lo: f64, hi: f64, step: f64) -> usize {
    let start = (lo / step).floor() * step;
    let end = (hi / step).ceil() * step;
    ((end - start) / step).round() as usize + 1
}

// Matches Python's round(x, 10) — removes floating-point noise from step arithmetic.
fn round10(x: f64) -> f64 {
    (x * 1e10).round() / 1e10
}

#[cfg(test)]
mod tests {
    use super::*;

    // All expected values verified against:
    //   uv run python -c "from dbt_charts.core.numeric import nice_tick_values; ..."

    fn approx_eq(a: &[f64], b: &[f64]) -> bool {
        a.len() == b.len() && a.iter().zip(b).all(|(x, y)| (x - y).abs() < 1e-9)
    }

    #[test]
    fn test_basic_cases() {
        assert!(approx_eq(
            &nice_tick_values(0.0, 4000.0, 6, None),
            &[0.0, 1000.0, 2000.0, 3000.0, 4000.0]
        ));
        assert!(approx_eq(
            &nice_tick_values(0.0, 1400.0, 5, None),
            &[0.0, 500.0, 1000.0, 1500.0]
        ));
        assert!(approx_eq(
            &nice_tick_values(0.0, 60.0, 6, None),
            &[0.0, 20.0, 40.0, 60.0]
        ));
    }

    #[test]
    fn test_non_zero_origin() {
        // Step 5 would produce 7 ticks; step 10 is chosen instead.
        assert!(approx_eq(
            &nice_tick_values(99.8, 124.6, 6, None),
            &[90.0, 100.0, 110.0, 120.0, 130.0]
        ));
    }

    // Values from `nice_tick_values(..., min_step=...)` in numeric.py.
    #[test]
    fn test_min_step_floors_the_step_when_a_rung_lands_inside() {
        assert!(approx_eq(
            &nice_tick_values(0.0, 2.0, 6, Some(1.0)),
            &[0.0, 1.0, 2.0]
        ));
        assert!(approx_eq(
            &nice_tick_values(0.0, 0.3, 6, Some(0.1)),
            &[0.0, 0.1, 0.2, 0.3]
        ));
    }

    #[test]
    fn test_min_step_is_ignored_when_no_floored_rung_lands_inside() {
        assert!(approx_eq(
            &nice_tick_values(0.2, 0.8, 6, Some(1.0)),
            &[0.2, 0.4, 0.6, 0.8]
        ));
    }

    #[test]
    fn test_lo_eq_hi() {
        assert!(approx_eq(&nice_tick_values(0.0, 0.0, 5, None), &[0.0]));
        assert!(approx_eq(&nice_tick_values(1.0, 1.0, 5, None), &[1.0]));
    }

    #[test]
    fn test_zero_crossing() {
        assert!(approx_eq(
            &nice_tick_values(-100.0, 100.0, 5, None),
            &[-100.0, -50.0, 0.0, 50.0, 100.0]
        ));
    }

    #[test]
    fn test_fractional_domain() {
        assert!(approx_eq(
            &nice_tick_values(0.0, 1.0, 5, None),
            &[0.0, 0.5, 1.0]
        ));
    }

    #[test]
    fn test_reverse_domain() {
        assert!(approx_eq(
            &nice_tick_values(4000.0, 0.0, 6, None),
            &[4000.0, 3000.0, 2000.0, 1000.0, 0.0]
        ));
    }

    #[test]
    fn test_few_ticks() {
        assert!(approx_eq(
            &nice_tick_values(0.0, 100.0, 3, None),
            &[0.0, 50.0, 100.0]
        ));
    }

    #[test]
    fn test_non_aligned_domain() {
        // Nice step forces start below lo.
        assert!(approx_eq(
            &nice_tick_values(1234.0, 5678.0, 4, None),
            &[0.0, 2000.0, 4000.0, 6000.0]
        ));
    }

    #[test]
    fn d3_ticks_stay_inside_the_domain() {
        // Verified against d3-array: d3.ticks(0, 1641600, 6) — step 2e5,
        // no tick past the stop (the cover-ladder would add 2M).
        assert!(approx_eq(
            &d3_ticks(0.0, 1_641_600.0, 6),
            &[
                0.0,
                200_000.0,
                400_000.0,
                600_000.0,
                800_000.0,
                1_000_000.0,
                1_200_000.0,
                1_400_000.0,
                1_600_000.0
            ]
        ));
        // d3.ticks(0, 1, 5) → fifths via the sub-unit branch.
        assert!(approx_eq(
            &d3_ticks(0.0, 1.0, 5),
            &[0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        ));
        // Zoomed domain: ticks clamp inside.
        assert!(approx_eq(
            &d3_ticks(113.36, 209.64, 6),
            &[120.0, 140.0, 160.0, 180.0, 200.0]
        ));
    }

    #[test]
    fn test_count_one() {
        // count <= 1 returns [lo].
        assert!(approx_eq(&nice_tick_values(0.0, 100.0, 1, None), &[0.0]));
    }

    #[test]
    fn d3_nice_matches_d3_scale_nice() {
        // Verified against the captured scatter oracle specs (Vega linear
        // scale nice: true = d3 nice with its default count 10):
        // health_scatter x data [0.52, 0.81] nices to [0.52, 0.82] — a
        // count-6 nice would give [0.50, 0.85] and a flush 50% label the
        // scenegraph does not show.
        assert_eq!(d3_nice(0.52, 0.81, 10), (0.52, 0.82));
        // spend_vs_pipeline x [0, 39] → [0, 40].
        assert_eq!(d3_nice(0.0, 39.0, 10), (0.0, 40.0));
        // types_4 x [0, 139] → [0, 140].
        assert_eq!(d3_nice(0.0, 139.0, 10), (0.0, 140.0));
        // Already-nice domain is a fixed point.
        assert_eq!(d3_nice(0.0, 40.0, 10), (0.0, 40.0));
    }

    /// d3-array's `tickStep`: [`tick_increment`] decoded back to a positive
    /// step, sub-unit `−1/step` form included. The 55-year case is the one
    /// `utcTickInterval`'s year rung asks for on a
    /// `2020-01-15 .. 2075-06-29` domain at count 3, where Vega draws
    /// `2040, 2060`.
    #[test]
    fn tick_step_decodes_the_increment() {
        assert_eq!(d3_tick_step(0.0, 100.0, 10), 10.0);
        assert_eq!(d3_tick_step(0.0, 1.0, 10), 0.1);
        assert_eq!(d3_tick_step(2020.04, 2075.49, 3), 20.0);
    }

    #[test]
    fn vega_tick_count_caps_the_count_at_the_domain_span_over_min_step() {
        // [0, 2.16] at tickMinStep 1: floor(2.16) + 1 = 3 ticks' worth.
        assert_eq!(vega_tick_count(0.0, 2.16, 12, Some(1.0)), 3);
        assert_eq!(d3_ticks(0.0, 2.16, 3), vec![0.0, 1.0, 2.0]);
        // A count already below the cap stands.
        assert_eq!(vega_tick_count(0.0, 100.0, 4, Some(1.0)), 4);
        // No tickMinStep leaves the count alone.
        assert_eq!(vega_tick_count(0.0, 2.16, 12, None), 12);
    }

    #[test]
    fn vega_tick_count_reads_a_zero_span_as_one() {
        // `Math.floor(0 / minStep || 1) + 1` is 2.
        assert_eq!(vega_tick_count(5.0, 5.0, 12, Some(1.0)), 2);
    }

    #[test]
    fn vega_tick_count_of_a_descending_domain_draws_nothing() {
        // span(domain) = first - last < 0, so the cap is negative and d3's
        // `ticks` returns [] for a non-positive count.
        let count = vega_tick_count(2.0, 0.0, 12, Some(1.0));
        assert_eq!(count, 0);
        assert!(d3_ticks(2.0, 0.0, count).is_empty());
    }
}
