//! Port of d3-array 3.2.4's `bin()` (`bin.js`) with its default extent domain or a given one, the threshold rules
//! `thresholdSturges`, `thresholdScott` and `thresholdFreedmanDiaconis` (`threshold/*.js`), and the `count`,
//! `deviation` and `quantile` they read. A value that is NaN stands for d3's null and undefined: every d3 reducer
//! here skips it, and no bin takes it.
//! References: https://github.com/d3/d3-array/blob/v3.2.4/src/bin.js

use crate::invariant::Invariant;
use crate::js_math::{js_max, js_round, js_truthy, pow};
use crate::ticks::{d3_ticks_fractional, d3_nice_fractional, tick_increment};

/// How `bin().thresholds(...)` is given: one of d3's rules, a count of thresholds that `ticks` turns into uniform
/// edges, or the edges themselves.
#[derive(Clone, Copy, Debug)]
pub enum Thresholds<'a> {
    Sturges,
    Scott,
    FreedmanDiaconis,
    Count(f64),
    Values(&'a [f64]),
}

/// One bin: `[x0, x1)` (the last is closed) and the indices of the values it holds, in input order.
#[derive(Clone, Debug, PartialEq)]
pub struct Bin {
    pub x0: f64,
    pub x1: f64,
    pub members: Vec<usize>,
}

/// d3's `count(values)`: the values that are numbers.
fn count(values: &[f64]) -> usize {
    values.iter().filter(|v| !v.is_nan()).count()
}

/// d3's `deviation(values)`; `None` where d3 returns undefined (fewer than two values) or 0 (`v ? sqrt(v) : v`).
fn deviation(values: &[f64]) -> Option<f64> {
    let (mut n, mut mean, mut sum) = (0.0_f64, 0.0_f64, 0.0_f64);
    for &value in values.iter().filter(|v| !v.is_nan()) {
        let delta = value - mean;
        n += 1.0;
        mean += delta / n;
        sum += delta * (value - mean);
    }
    (n > 1.0).then(|| sum / (n - 1.0)).filter(|v| js_truthy(*v)).map(f64::sqrt)
}

/// d3's `quantile(values, p)`: linear interpolation between the order statistics around `(n - 1) * p`. d3 reaches
/// them with quickselect; sorting yields the same two values.
fn quantile(values: &[f64], p: f64) -> Option<f64> {
    let mut sorted: Vec<f64> = values.iter().copied().filter(|v| !v.is_nan()).collect();
    sorted.sort_by(f64::total_cmp);
    let n = sorted.len();
    if n == 0 || p.is_nan() {
        return None;
    }
    if p <= 0.0 || n < 2 {
        return sorted.first().copied();
    }
    if p >= 1.0 {
        return sorted.last().copied();
    }
    let i = (n - 1) as f64 * p;
    let i0 = i.floor();
    let (value0, value1) = (
        *sorted.get(i0 as usize).held("0 < p < 1 puts i0 inside the sorted values"),
        *sorted.get(i0 as usize + 1).held("0 < p < 1 puts i0 + 1 inside the sorted values"),
    );
    Some(value0 + (value1 - value0) * (i - i0))
}

/// d3's `extent(values)`: NaN, NaN where there is no number.
fn extent(values: &[f64]) -> (f64, f64) {
    let mut numbers = values.iter().copied().filter(|v| !v.is_nan());
    let Some(first) = numbers.next() else { return (f64::NAN, f64::NAN) };
    numbers.fold((first, first), |(min, max), v| (if min > v { v } else { min }, if max < v { v } else { max }))
}

/// `thresholdSturges(values)`.
pub fn threshold_sturges(values: &[f64]) -> f64 {
    js_max(1.0, (libm::log(count(values) as f64) / std::f64::consts::LN_2).ceil() + 1.0)
}

/// `thresholdScott(values, min, max)`. `c && d`: no values, or a zero or missing deviation, is one threshold.
pub fn threshold_scott(values: &[f64], min: f64, max: f64) -> f64 {
    let c = count(values) as f64;
    match deviation(values) {
        Some(d) if c != 0.0 => ((max - min) * libm::cbrt(c) / (3.49 * d)).ceil(),
        _ => 1.0,
    }
}

/// `thresholdFreedmanDiaconis(values, min, max)`.
pub fn threshold_freedman_diaconis(values: &[f64], min: f64, max: f64) -> f64 {
    let c = count(values) as f64;
    let d = quantile(values, 0.75).zip(quantile(values, 0.25)).map(|(q3, q1)| q3 - q1);
    match d {
        Some(d) if c != 0.0 && js_truthy(d) => ((max - min) / (2.0 * d * pow(c, -1.0 / 3.0))).ceil(),
        _ => 1.0,
    }
}

impl Thresholds<'_> {
    /// `threshold(values, x0, x1)`: a count of thresholds (a rule's answer) or the edges.
    fn resolve(self, values: &[f64], x0: f64, x1: f64) -> Result<f64, Vec<f64>> {
        match self {
            Thresholds::Sturges => Ok(threshold_sturges(values)),
            Thresholds::Scott => Ok(threshold_scott(values, x0, x1)),
            Thresholds::FreedmanDiaconis => Ok(threshold_freedman_diaconis(values, x0, x1)),
            Thresholds::Count(count) => Ok(count),
            Thresholds::Values(edges) => Err(edges.to_vec()),
        }
    }
}

/// `bin().thresholds(thresholds)(values)`, the domain being `domain` (`bin().domain(domain)`) or, with `None`, the
/// values' extent: `m + 1` bins over the thresholds that fall strictly inside it. A count of thresholds is turned
/// into `ticks` over the domain. A default domain is niced first, and a last tick on the maximum value extends it
/// one tick so that value gets a bin of its own width; a given domain is used as is and its last tick is dropped.
/// With no number in `values` d3 returns one empty bin with undefined (here NaN) edges.
pub fn bin(values: &[f64], thresholds: Thresholds, domain: Option<[f64; 2]>) -> Vec<Bin> {
    let (mut x0, mut x1) = domain.map_or_else(|| extent(values), |[x0, x1]| (x0, x1));
    let default_domain = domain.is_none();
    let mut step: Option<f64> = None;
    let mut tz = match thresholds.resolve(values, x0, x1) {
        Err(edges) => edges,
        Ok(tn) => {
            let max = x1;
            if default_domain {
                (x0, x1) = d3_nice_fractional(x0, x1, tn);
            }
            let mut tz = d3_ticks_fractional(x0, x1, tn);
            // The domain is aligned with the first tick (by default it is), so values quantize rather than bisect.
            if tz.first().is_some_and(|t| *t <= x0) {
                step = Some(tick_increment(x0, x1, tn));
            }
            if tz.last().is_some_and(|t| *t >= x1) {
                if max >= x1 && default_domain {
                    let step = tick_increment(x0, x1, tn);
                    if step.is_finite() {
                        if step > 0.0 {
                            x1 = ((x1 / step).floor() + 1.0) * step;
                        } else if step < 0.0 {
                            x1 = ((x1 * -step).ceil() + 1.0) / -step;
                        }
                    }
                } else {
                    tz.pop();
                }
            }
            tz
        }
    };

    // Thresholds outside the domain go.
    let (mut a, mut b) = (0, tz.len());
    while tz.get(a).is_some_and(|t| *t <= x0) {
        a += 1;
    }
    while b > 0 && tz.get(b - 1).is_some_and(|t| *t > x1) {
        b -= 1;
    }
    // A reversed domain can leave `a > b`; d3 sizes its bins with `new Array(b - a + 1)`, which throws once two or
    // more thresholds fall inside it, so this fails too instead of inventing a bin.
    tz = tz.get(a..b).held("the trimmed thresholds are an ordered range").to_vec();
    let m = tz.len();

    let mut bins: Vec<Bin> = (0..=m)
        .map(|i| Bin {
            x0: i.checked_sub(1).and_then(|j| tz.get(j)).copied().unwrap_or(x0),
            x1: tz.get(i).copied().unwrap_or(x1),
            members: Vec::new(),
        })
        .collect();
    for (index, &x) in values.iter().enumerate() {
        if !(x0 <= x && x <= x1) {
            continue;
        }
        let at = match step.filter(|s| s.is_finite()) {
            Some(step) if step > 0.0 => Some(m.min(((x - x0) / step).floor() as usize)),
            // d3 handles the off-by-one rounding of a sub-unit step with `tz[j] <= x`.
            Some(step) if step < 0.0 => {
                let j = ((x0 - x) * step).floor() as usize;
                Some(m.min(j + usize::from(tz.get(j).is_some_and(|t| *t <= x))))
            }
            // A zero step is finite and neither sign: d3 bins nothing.
            Some(_) => None,
            None => Some(tz.partition_point(|t| *t <= x)),
        };
        if let Some(at) = at {
            bins.get_mut(at).held("`at` is at most the threshold count").members.push(index);
        }
    }
    bins
}

/// The `[start, stop]` vega-statistics 2.0.0's `bin({extent: [value, value], maxbins})` returns (`bin.js`, with the
/// `base: 10`, `divide: [5, 2]` and `nice` that Vega-Lite's `bin: true` passes): a zero-span extent takes
/// `span = abs(value) || 1`, picks the step Vega would for that span and is niced out to it, so a constant gets a
/// domain one step wide. d3's `bin()` has no such rule: its extent would be zero wide.
pub fn vega_constant_extent(value: f64, maxbins: f64) -> [f64; 2] {
    const BASE: f64 = 10.0;
    let log_base = libm::log(BASE);
    let span = if js_truthy(value) { value.abs() } else { 1.0 };
    let level = (libm::log(maxbins) / log_base).ceil();
    let mut step = js_max(0.0, pow(BASE, js_round(libm::log(span) / log_base) - level));
    while (span / step).ceil() > maxbins {
        step *= BASE;
    }
    for divisor in [5.0, 2.0] {
        let v = step / divisor;
        if v >= 0.0 && span / v <= maxbins {
            step = v;
        }
    }
    let log_step = libm::log(step);
    let precision = if log_step >= 0.0 { 0.0 } else { (-log_step / log_base).trunc() + 1.0 };
    let eps = pow(BASE, -precision - 1.0);
    let floored = (value / step + eps).floor() * step;
    let start = if value < floored { floored - step } else { floored };
    let stop = (value / step).ceil() * step;
    [start, if stop == start { start + step } else { stop }]
}

#[cfg(test)]
mod tests {
    use super::*;

    /// d3 sizes its bins with `new Array(b - a + 1)` after trimming, which throws a `RangeError` when a reversed domain
    /// leaves two or more thresholds past each other; it never returns a plausible-looking bin, so neither does this port.
    #[test]
    #[should_panic(expected = "the trimmed thresholds are an ordered range")]
    fn a_reversed_domain_that_trims_past_itself_fails_loudly() {
        bin(&[1.0, 5.0, 9.0], Thresholds::Values(&[3.0, 5.0]), Some([10.0, 0.0]));
    }
}
