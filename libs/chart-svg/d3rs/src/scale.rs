//! Port of d3-scale 4.0.2's continuous scales (`continuous.js`, `linear.js`,
//! `log.js`, `pow.js`, `symlog.js`, `nice.js`) and `scaleBand` / `scalePoint`
//! (`band.js`). A scale is a value; every setter d3 chains is a field, and
//! `rescale` happens in the constructor.
//! References: https://github.com/d3/d3-scale/tree/main/src

use crate::js_math::{exp as js_exp, js_round, js_sign, pow as js_pow};
use crate::ticks::{d3_ticks, tick_increment};

/// The transform a continuous scale interpolates through: d3's `scaleLinear`
/// (`Identity`), `scaleLog` (`Log`, whose `base` sets ticks and nice, not the
/// map), `scalePow` (`Pow`; `scaleSqrt` is `Pow(0.5)`) and `scaleSymlog`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Transform {
    Identity,
    Log { base: f64 },
    Pow(f64),
    Symlog(f64),
}

impl Transform {
    /// d3's `transform(x)`; a log scale over a negative domain reflects (`transformLogn`).
    pub fn apply(self, reflect: bool, x: f64) -> f64 {
        match self {
            Transform::Identity => x,
            Transform::Log { .. } if reflect => -libm::log(-x),
            Transform::Log { .. } => libm::log(x),
            Transform::Pow(e) if e == 1.0 => x,
            Transform::Pow(e) if e == 0.5 => {
                if x < 0.0 {
                    -(-x).sqrt()
                } else {
                    x.sqrt()
                }
            }
            Transform::Pow(e) => {
                if x < 0.0 {
                    -js_pow(-x, e)
                } else {
                    js_pow(x, e)
                }
            }
            Transform::Symlog(c) => js_sign(x) * libm::log1p((x / c).abs()),
        }
    }

    /// d3's `untransform(x)`.
    pub fn undo(self, reflect: bool, x: f64) -> f64 {
        match self {
            Transform::Identity => x,
            Transform::Log { .. } if reflect => -js_exp(-x),
            Transform::Log { .. } => js_exp(x),
            Transform::Pow(e) if e == 1.0 => x,
            Transform::Pow(e) if e == 0.5 => {
                if x < 0.0 {
                    -x * x
                } else {
                    x * x
                }
            }
            Transform::Pow(e) => {
                if x < 0.0 {
                    -js_pow(-x, 1.0 / e)
                } else {
                    js_pow(x, 1.0 / e)
                }
            }
            Transform::Symlog(c) => js_sign(x) * libm::expm1(x.abs()) * c,
        }
    }
}

/// d3-interpolate's `interpolateNumber`: `a * (1 - t) + b * t`, whose rounding
/// differs from `a + t * (b - a)` at a half-pixel tie.
fn interpolate(a: f64, b: f64, t: f64) -> f64 {
    a * (1.0 - t) + b * t
}

/// d3's `normalize(a, b)(x)`: a zero-width span maps everything to 0.5.
fn normalize(a: f64, b: f64, x: f64) -> f64 {
    let span = b - a;
    if span != 0.0 {
        (x - a) / span
    } else if span.is_nan() {
        f64::NAN
    } else {
        0.5
    }
}

/// `bimap` for two stops, `polymap` for more, over any range `interpolate` mixes: a descending domain is
/// reversed with its range, and `polymap` picks the segment by `bisect`.
fn piecewise<T: Copy>(domain: &[f64], range: &[T], x: f64, interpolate: impl Fn(T, T, f64) -> T) -> T {
    let n = domain.len().min(range.len());
    if n > 2 {
        let j = n - 1;
        let (mut d, mut r) = (domain[..n].to_vec(), range[..n].to_vec());
        if d[j] < d[0] {
            d.reverse();
            r.reverse();
        }
        let i = d[1..j].partition_point(|v| *v <= x);
        return interpolate(r[i], r[i + 1], normalize(d[i], d[i + 1], x));
    }
    let (d0, d1, r0, r1) = (domain[0], domain[1], range[0], range[1]);
    if d1 < d0 {
        interpolate(r1, r0, normalize(d1, d0, x))
    } else {
        interpolate(r0, r1, normalize(d0, d1, x))
    }
}

/// `scaleLinear().domain(domain).range(range).interpolate(interpolate).clamp(clamp)(x)` over a range that is
/// not numbers (colors): `domain` and `range` each hold two or more stops. NaN `x` is d3's `unknown`: `None`.
pub fn linear_range<T: Copy>(domain: &[f64], range: &[T], interpolate: impl Fn(T, T, f64) -> T, clamp: bool, x: f64) -> Option<T> {
    assert!(domain.len() >= 2 && range.len() >= 2, "a continuous scale needs two or more stops");
    if x.is_nan() {
        return None;
    }
    let n = domain.len().min(range.len());
    let x = if clamp { x.min(domain[0].max(domain[n - 1])).max(domain[0].min(domain[n - 1])) } else { x };
    Some(piecewise(domain, range, x, interpolate))
}

/// `log.js`'s `pow10`: `+("1e" + x)`, decimal-exact for a whole exponent and
/// NaN for a fractional one (not a JS number literal); `-Infinity` is 0.
fn pow10(x: f64) -> f64 {
    if x.is_finite() {
        if x.fract() == 0.0 {
            format!("1e{}", x as i64).parse().unwrap_or(f64::NAN)
        } else {
            f64::NAN
        }
    } else if x < 0.0 {
        0.0
    } else {
        x
    }
}

/// `log.js`'s `powp(base)`.
fn powp(base: f64, x: f64) -> f64 {
    if base == 10.0 {
        pow10(x)
    } else if base == std::f64::consts::E {
        js_exp(x)
    } else {
        js_pow(base, x)
    }
}

/// `log.js`'s `logp(base)`.
fn logp(base: f64, x: f64) -> f64 {
    if base == std::f64::consts::E {
        libm::log(x)
    } else if base == 10.0 {
        libm::log10(x)
    } else if base == 2.0 {
        libm::log2(x)
    } else {
        libm::log(x) / libm::log(base)
    }
}

/// A continuous scale: the transform, a domain and range of two or more stops,
/// and d3's `clamp`.
#[derive(Clone, Debug, PartialEq)]
pub struct Continuous {
    pub transform: Transform,
    pub domain: Vec<f64>,
    pub range: Vec<f64>,
    pub clamp: bool,
}

impl Continuous {
    /// A scale over a two-stop domain and range, unclamped.
    pub fn new(transform: Transform, domain: [f64; 2], range: [f64; 2]) -> Continuous {
        Continuous { transform, domain: domain.to_vec(), range: range.to_vec(), clamp: false }
    }

    /// `scaleLinear(domain, range)`.
    pub fn linear(domain: [f64; 2], range: [f64; 2]) -> Continuous {
        Continuous::new(Transform::Identity, domain, range)
    }

    /// `scaleLog(domain, range)`, base 10.
    pub fn log(domain: [f64; 2], range: [f64; 2]) -> Continuous {
        Continuous::new(Transform::Log { base: 10.0 }, domain, range)
    }

    /// `loggish`'s `rescale`: a domain starting below zero reflects.
    fn reflect(&self) -> bool {
        matches!(self.transform, Transform::Log { .. }) && self.domain.first().is_some_and(|d| *d < 0.0)
    }

    fn transformed_domain(&self) -> Vec<f64> {
        let reflect = self.reflect();
        self.domain.iter().map(|d| self.transform.apply(reflect, *d)).collect()
    }

    /// `clamper(domain[0], domain[n - 1])`.
    fn clamped(&self, x: f64) -> f64 {
        if !self.clamp {
            return x;
        }
        let n = self.domain.len().min(self.range.len());
        let (mut a, mut b) = (self.domain[0], self.domain[n - 1]);
        if a > b {
            std::mem::swap(&mut a, &mut b);
        }
        x.min(b).max(a)
    }

    /// `scale(x)`; NaN where d3 returns `unknown` (undefined).
    pub fn scale(&self, x: f64) -> f64 {
        if x.is_nan() {
            return f64::NAN;
        }
        let x = self.transform.apply(self.reflect(), self.clamped(x));
        piecewise(&self.transformed_domain(), &self.range, x, interpolate)
    }

    /// `scale.invert(y)`.
    pub fn invert(&self, y: f64) -> f64 {
        let x = self.transform.undo(self.reflect(), piecewise(&self.range, &self.transformed_domain(), y, interpolate));
        self.clamped(x)
    }

    fn ends(&self) -> (f64, f64) {
        (self.domain[0], self.domain[self.domain.len() - 1])
    }

    /// `scale.ticks(count)`: `linearish` ticks over the domain's ends, or
    /// `loggish` ticks (every mantissa of each decade in the base when the
    /// domain spans fewer decades than `count`, else ticks of the exponents).
    pub fn ticks(&self, count: usize) -> Vec<f64> {
        let (u, v) = self.ends();
        let Transform::Log { base } = self.transform else {
            return d3_ticks(u, v, count);
        };
        let reverse = v < u;
        let (u, v) = if reverse { (v, u) } else { (u, v) };
        let reflect = self.reflect();
        let logs = |x: f64| if reflect { -logp(base, -x) } else { logp(base, x) };
        let pows = |x: f64| if reflect { -powp(base, -x) } else { powp(base, x) };
        let (mut i, mut j) = (logs(u), logs(v));
        let n = count as f64;
        let mut z: Vec<f64> = Vec::new();
        if base % 1.0 == 0.0 && j - i < n {
            i = i.floor();
            j = j.ceil();
            if u > 0.0 {
                while i <= j {
                    for k in 1..base as i64 {
                        let k = k as f64;
                        let t = if i < 0.0 { k / pows(-i) } else { k * pows(i) };
                        if t < u {
                            continue;
                        }
                        if t > v {
                            break;
                        }
                        z.push(t);
                    }
                    i += 1.0;
                }
            } else {
                while i <= j {
                    for k in (1..base as i64).rev() {
                        let k = k as f64;
                        let t = if i > 0.0 { k / pows(-i) } else { k * pows(i) };
                        if t < u {
                            continue;
                        }
                        if t > v {
                            break;
                        }
                        z.push(t);
                    }
                    i += 1.0;
                }
            }
            if (z.len() as f64) * 2.0 < n {
                z = d3_ticks(u, v, count);
            }
        } else {
            z = crate::ticks::d3_ticks_fractional(i, j, (j - i).min(n)).into_iter().map(pows).collect();
        }
        if reverse {
            z.reverse();
        }
        z
    }

    /// `loggish`'s `tickFormat(count)` visibility: whether a tick's label prints
    /// (`i <= k`) or is the empty string that thins a crowded decade. `count`
    /// is d3's default 10 when absent.
    pub fn log_tick_visible(&self, count: usize) -> impl Fn(f64) -> bool + '_ {
        let Transform::Log { base } = self.transform else {
            panic!("log_tick_visible on a scale that is not log");
        };
        let reflect = self.reflect();
        let k = (base * count as f64 / self.ticks(10).len() as f64).max(1.0);
        move |d: f64| {
            let logs = |x: f64| if reflect { -logp(base, -x) } else { logp(base, x) };
            let pows = |x: f64| if reflect { -powp(base, -x) } else { powp(base, x) };
            let mut i = d / pows(js_round(logs(d)));
            if i * base < base - 0.5 {
                i *= base;
            }
            i <= k
        }
    }

    /// `scale.nice(count)` as a new scale: `linearish` widens the ends to the
    /// tick increment until it settles (ten tries, else unchanged); `loggish`
    /// floors and ceils them to powers of the base, `count` ignored.
    pub fn nice(&self, count: usize) -> Continuous {
        let mut domain = self.domain.clone();
        let (mut i0, mut i1) = (0, domain.len() - 1);
        let (mut start, mut stop) = (domain[i0], domain[i1]);
        if stop < start {
            std::mem::swap(&mut start, &mut stop);
            std::mem::swap(&mut i0, &mut i1);
        }
        if let Transform::Log { base } = self.transform {
            let reflect = self.reflect();
            let logs = |x: f64| if reflect { -logp(base, -x) } else { logp(base, x) };
            let pows = |x: f64| if reflect { -powp(base, -x) } else { powp(base, x) };
            domain[i0] = pows(logs(start).floor());
            domain[i1] = pows(logs(stop).ceil());
            return Continuous { domain, ..self.clone() };
        }
        let mut prestep: Option<f64> = None;
        for _ in 0..10 {
            let step = tick_increment(start, stop, count as f64);
            if prestep == Some(step) {
                domain[i0] = start;
                domain[i1] = stop;
                return Continuous { domain, ..self.clone() };
            } else if step > 0.0 {
                start = (start / step).floor() * step;
                stop = (stop / step).ceil() * step;
            } else if step < 0.0 {
                start = (start * step).ceil() / step;
                stop = (stop * step).floor() / step;
            } else {
                break;
            }
            prestep = Some(step);
        }
        self.clone()
    }
}

/// `scaleBand` over `n` domain values: band `i` starts at `position(i)` and
/// is `bandwidth` wide, `step` apart. `scalePoint` is `padding_inner` 1.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Band {
    pub n: usize,
    pub step: f64,
    pub bandwidth: f64,
    start: f64,
    reverse: bool,
}

impl Band {
    /// `rescale()` of band.js; `round` is `scale.round(true)`. `padding_inner`
    /// is capped at 1 and `align` clamped to [0, 1] as d3's setters do.
    pub fn new(n: usize, range: [f64; 2], padding_inner: f64, padding_outer: f64, align: f64, round: bool) -> Band {
        let padding_inner = padding_inner.min(1.0);
        let align = align.clamp(0.0, 1.0);
        let reverse = range[1] < range[0];
        let (mut start, stop) = if reverse { (range[1], range[0]) } else { (range[0], range[1]) };
        let mut step = (stop - start) / (n as f64 - padding_inner + padding_outer * 2.0).max(1.0);
        if round {
            step = step.floor();
        }
        start += (stop - start - step * (n as f64 - padding_inner)) * align;
        let mut bandwidth = step * (1.0 - padding_inner);
        if round {
            start = js_round(start);
            bandwidth = js_round(bandwidth);
        }
        Band { n, step, bandwidth, start, reverse }
    }

    /// `scalePoint`: a band scale with `paddingInner` 1, whose `padding` is the outer padding.
    pub fn point(n: usize, range: [f64; 2], padding: f64, align: f64, round: bool) -> Band {
        Band::new(n, range, 1.0, padding, align, round)
    }

    /// The start of band `index` (0-based, domain order).
    pub fn position(&self, index: usize) -> f64 {
        let slot = if self.reverse { self.n - 1 - index } else { index };
        self.start + self.step * slot as f64
    }

    /// Every band's start, in domain order.
    pub fn positions(&self) -> Vec<f64> {
        (0..self.n).map(|i| self.position(i)).collect()
    }
}
