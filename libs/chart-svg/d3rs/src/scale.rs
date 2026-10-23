//! Ports of d3-scale's two-point `scaleLinear` / `scaleSqrt` (continuous.js,
//! linear.js, pow.js) and `scaleBand` (band.js, `round` off).
//! References: https://github.com/d3/d3-scale/tree/main/src

use crate::ticks::{d3_nice, d3_ticks};

/// The monotone map a continuous scale interpolates through: d3's
/// `scaleSqrt` is `scalePow().exponent(0.5)`, whose transform is signed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Transform {
    Identity,
    Sqrt,
}

impl Transform {
    fn apply(self, x: f64) -> f64 {
        match self {
            Transform::Identity => x,
            Transform::Sqrt => x.signum() * x.abs().sqrt(),
        }
    }

    fn undo(self, x: f64) -> f64 {
        match self {
            Transform::Identity => x,
            Transform::Sqrt => x.signum() * x * x,
        }
    }
}

/// A scale from a two-number domain to a two-number range.
#[derive(Clone, Copy, Debug)]
pub struct Continuous {
    pub transform: Transform,
    pub domain: [f64; 2],
    pub range: [f64; 2],
}

fn interpolate(a: f64, b: f64, t: f64) -> f64 {
    a * (1.0 - t) + b * t
}

/// d3's `normalize(a, b)`: a zero-width span maps everything to 0.5.
fn normalize(a: f64, b: f64, x: f64) -> f64 {
    if b - a != 0.0 {
        (x - a) / (b - a)
    } else {
        0.5
    }
}

/// d3's `bimap(domain, range, interpolate)`: a descending domain is
/// normalized ascending and its range interpolated from the other end, which
/// is what rounds `[100, 0] -> [0, 1]` at 33 to 0.6699999999999999.
fn bimap(domain: [f64; 2], range: [f64; 2], x: f64) -> f64 {
    let [d0, d1] = domain;
    let [r0, r1] = range;
    if d1 < d0 {
        interpolate(r1, r0, normalize(d1, d0, x))
    } else {
        interpolate(r0, r1, normalize(d0, d1, x))
    }
}

impl Continuous {
    /// `scale(x)`: `x` normalized over the transformed domain, interpolated into the range.
    pub fn scale(&self, x: f64) -> f64 {
        bimap(self.domain.map(|d| self.transform.apply(d)), self.range, self.transform.apply(x))
    }

    /// `scale.invert(y)`.
    pub fn invert(&self, y: f64) -> f64 {
        self.transform.undo(bimap(self.range, self.domain.map(|d| self.transform.apply(d)), y))
    }

    /// `scale.ticks(count)`: ticks of the untransformed domain.
    pub fn ticks(&self, count: usize) -> Vec<f64> {
        d3_ticks(self.domain[0], self.domain[1], count)
    }

    /// `scale.nice(count)` as a new scale; a descending domain is niced
    /// ascending and put back.
    pub fn nice(&self, count: usize) -> Continuous {
        let [d0, d1] = self.domain;
        let domain = if d1 < d0 {
            let (lo, hi) = d3_nice(d1, d0, count);
            [hi, lo]
        } else {
            let (lo, hi) = d3_nice(d0, d1, count);
            [lo, hi]
        };
        Continuous { domain, ..*self }
    }
}

/// `scaleBand` over `n` domain values: band `i` starts at `position(i)` and
/// is `bandwidth` wide, `step` apart.
#[derive(Clone, Copy, Debug)]
pub struct Band {
    pub n: usize,
    pub step: f64,
    pub bandwidth: f64,
    start: f64,
    reverse: bool,
}

impl Band {
    /// `rescale()` of band.js with `round` off. `n` must be at least 1.
    pub fn new(n: usize, range: [f64; 2], padding_inner: f64, padding_outer: f64, align: f64) -> Band {
        let reverse = range[1] < range[0];
        let (mut start, stop) = if reverse { (range[1], range[0]) } else { (range[0], range[1]) };
        let step = (stop - start) / (n as f64 - padding_inner + padding_outer * 2.0).max(1.0);
        start += (stop - start - step * (n as f64 - padding_inner)) * align;
        Band { n, step, bandwidth: step * (1.0 - padding_inner), start, reverse }
    }

    /// The start of band `index` (0-based, domain order).
    pub fn position(&self, index: usize) -> f64 {
        let slot = if self.reverse { self.n - 1 - index } else { index };
        self.start + self.step * slot as f64
    }
}
