//! Port of d3-scale 4.0.2's linear `scaleSequential` and `scaleDiverging` (`sequential.js`, `diverging.js`).
//! A d3 sequential scale is `interpolator(position(x))`: the position along the unit interval is the scale's
//! whole law, and the interpolator is any function of it (`color::piecewise` over a scheme, for the engine), so a
//! scale here is the position. NaN is d3's `unknown`: `None`.
//! References: https://github.com/d3/d3-scale/blob/v4.0.2/src/sequential.js

/// `Math.max(0, Math.min(1, t))`, which keeps a NaN.
fn clamp01(t: f64) -> f64 {
    if t.is_nan() {
        t
    } else {
        t.clamp(0.0, 1.0)
    }
}

/// `scaleSequential().domain([x0, x1])`'s position of `x`: 0 at `x0`, 1 at `x1`, unbounded outside unless clamped.
/// A zero-width domain puts every `x` mid-ramp.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Sequential {
    x0: f64,
    k10: f64,
    clamp: bool,
}

impl Sequential {
    pub fn new([x0, x1]: [f64; 2], clamp: bool) -> Sequential {
        Sequential { x0, k10: if x0 == x1 { 0.0 } else { 1.0 / (x1 - x0) }, clamp }
    }

    /// `scale(x)` before the interpolator.
    pub fn position(&self, x: f64) -> Option<f64> {
        if x.is_nan() {
            return None;
        }
        if self.k10 == 0.0 {
            return Some(0.5);
        }
        let t = (x - self.x0) * self.k10;
        Some(if self.clamp { clamp01(t) } else { t })
    }
}

/// `scaleDiverging().domain([x0, x1, x2])`'s position of `x`: 0 at `x0`, 0.5 at the pivot `x1`, 1 at `x2`, each arm
/// scaled by its own width (a collapsed arm has none: it keeps the pivot's 0.5). A descending domain runs backwards.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Diverging {
    x1: f64,
    k10: f64,
    k21: f64,
    s: f64,
    clamp: bool,
}

impl Diverging {
    pub fn new([x0, x1, x2]: [f64; 3], clamp: bool) -> Diverging {
        Diverging {
            x1,
            k10: if x0 == x1 { 0.0 } else { 0.5 / (x1 - x0) },
            k21: if x1 == x2 { 0.0 } else { 0.5 / (x2 - x1) },
            s: if x1 < x0 { -1.0 } else { 1.0 },
            clamp,
        }
    }

    /// `scale(x)` before the interpolator.
    pub fn position(&self, x: f64) -> Option<f64> {
        if x.is_nan() {
            return None;
        }
        let k = if self.s * x < self.s * self.x1 { self.k10 } else { self.k21 };
        let t = 0.5 + (x - self.x1) * k;
        Some(if self.clamp { clamp01(t) } else { t })
    }
}
