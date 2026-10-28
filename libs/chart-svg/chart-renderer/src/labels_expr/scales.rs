//! The scales an axis `labels.expr` reads through `scale()`, `invert()`, `domain()`, `range()` and
//! `bandwidth()`: d3-scale's, as Vega registers them (vega-scale's band and point included).

use super::funcs::{js_pow, js_sign};
use crate::families::cartesian::BandGeometry;
use super::val::{throws, EvalError, Res, Val};

/// The most ticks a label expression may ask a scale for: far past any axis, short of an allocation that aborts.
const MAX_TICKS: f64 = 1_000_000.0;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Kind {
    Linear,
    Log,
    Pow(f64),
    Symlog(f64),
    Time,
    Utc,
    Band { padding_inner: f64, padding_outer: f64 },
    Point { padding_outer: f64 },
    Ordinal,
}

impl Kind {
    /// vega-scale's name for the type, which `scale.type` carries.
    pub fn name(self) -> &'static str {
        match self {
            Kind::Linear => "linear",
            Kind::Log => "log",
            Kind::Pow(e) if e == 0.5 => "sqrt",
            Kind::Pow(_) => "pow",
            Kind::Symlog(_) => "symlog",
            Kind::Time => "time",
            Kind::Utc => "utc",
            Kind::Band { .. } => "band",
            Kind::Point { .. } => "point",
            Kind::Ordinal => "ordinal",
        }
    }

    fn continuous(self) -> bool {
        matches!(self, Kind::Linear | Kind::Log | Kind::Pow(_) | Kind::Symlog(_) | Kind::Time | Kind::Utc)
    }
}

#[derive(Debug, Clone)]
struct Bands {
    step: f64,
    bandwidth: f64,
    values: Vec<f64>,
}

/// A scale at a given range: d3's object, built once and read many times.
#[derive(Debug, Clone)]
pub struct Scale {
    kind: Kind,
    domain: Vec<Val>,
    range: Vec<Val>,
    bands: Option<Bands>,
}

type Fx = Box<dyn Fn(f64) -> f64>;

/// d3's `(transform, untransform)` pair for a continuous scale with the domain `domain`.
fn transforms(kind: Kind, domain: &[f64]) -> (Fx, Fx) {
    match kind {
        Kind::Log if domain.first().is_some_and(|d| *d < 0.0) => (Box::new(|x| -libm::log(-x)), Box::new(|x| -libm::exp(-x))),
        Kind::Log => (Box::new(libm::log), Box::new(libm::exp)),
        Kind::Pow(e) if e == 1.0 => (Box::new(|x| x), Box::new(|x| x)),
        Kind::Pow(e) if e == 0.5 => (
            Box::new(|x| if x < 0.0 { -(-x).sqrt() } else { x.sqrt() }),
            Box::new(|x| if x < 0.0 { -x * x } else { x * x }),
        ),
        Kind::Pow(e) => (
            Box::new(move |x| if x < 0.0 { -js_pow(-x, e) } else { js_pow(x, e) }),
            Box::new(move |x| if x < 0.0 { -js_pow(-x, 1.0 / e) } else { js_pow(x, 1.0 / e) }),
        ),
        Kind::Symlog(c) => (
            Box::new(move |x| js_sign(x) * libm::log1p((x / c).abs())),
            Box::new(move |x| js_sign(x) * libm::expm1(x.abs()) * c),
        ),
        _ => (Box::new(|x| x), Box::new(|x| x)),
    }
}

/// d3's `normalize(a, b)`.
fn normalize(a: f64, b: f64, x: f64) -> f64 {
    let span = b - a;
    if span != 0.0 { (x - a) / span } else if span.is_nan() { f64::NAN } else { 0.5 }
}

fn lerp(a: f64, b: f64, t: f64) -> f64 {
    a * (1.0 - t) + b * t
}

/// d3's `bimap`/`polymap`: the piecewise-linear map from `domain` onto `range`.
fn piecewise(domain: &[f64], range: &[f64], x: f64) -> f64 {
    let n = domain.len().min(range.len());
    if n > 2 {
        let j = n - 1;
        let (mut d, mut r) = (domain[..n].to_vec(), range[..n].to_vec());
        if d[j] < d[0] {
            d.reverse();
            r.reverse();
        }
        let i = d[1..j].partition_point(|v| *v <= x);
        let i = i.min(j - 1);
        return lerp(r[i], r[i + 1], normalize(d[i], d[i + 1], x));
    }
    let (d0, d1, r0, r1) = (domain[0], domain[1], range[0], range[1]);
    if d1 < d0 { lerp(r1, r0, normalize(d1, d0, x)) } else { lerp(r0, r1, normalize(d0, d1, x)) }
}

/// `InternMap`'s key equality: `valueOf` for objects, then `SameValueZero`.
pub(super) fn same_key(a: &Val, b: &Val) -> bool {
    let key = |v: &Val| match v {
        Val::Date(ms) => Val::Num(*ms),
        other => other.clone(),
    };
    match (key(a), key(b)) {
        (Val::Num(x), Val::Num(y)) => x == y || (x.is_nan() && y.is_nan()),
        (x, y) => x.strict_eq(&y),
    }
}

fn nums(v: &[Val]) -> Vec<f64> {
    v.iter().map(Val::to_number).collect()
}

impl Scale {
    pub fn new(kind: Kind, domain: Vec<Val>, range: Vec<Val>) -> Self {
        let mut scale = Scale { kind, domain, range, bands: None };
        scale.rescale();
        scale
    }

    pub fn kind(&self) -> Kind {
        self.kind
    }

    fn rescale(&mut self) {
        let (inner, outer) = match self.kind {
            Kind::Band { padding_inner, padding_outer } => (padding_inner, padding_outer),
            Kind::Point { padding_outer } => (1.0, padding_outer),
            _ => return,
        };
        let r = nums(&self.range);
        let reverse = r[1] < r[0];
        let (start, stop) = if reverse { (r[1], r[0]) } else { (r[0], r[1]) };
        let geometry = BandGeometry::new(self.domain.len(), start, stop, inner, outer);
        let mut values: Vec<f64> = (0..self.domain.len()).map(|i| geometry.edge(i, 0.0)).collect();
        if reverse {
            values.reverse();
        }
        self.bands = Some(Bands { step: geometry.step(), bandwidth: geometry.bandwidth(), values });
    }

    fn index_of(&self, v: &Val) -> Option<usize> {
        self.domain.iter().position(|d| same_key(d, v))
    }

    /// `scale(value)`.
    pub fn call(&self, v: &Val) -> Val {
        match self.kind {
            Kind::Band { .. } | Kind::Point { .. } => match self.index_of(v) {
                Some(i) => Val::Num(self.bands.as_ref().expect("a band scale is rescaled").values[i]),
                None => Val::Undefined,
            },
            Kind::Ordinal => match self.index_of(v) {
                Some(i) => self.range.get(i % self.range.len().max(1)).cloned().unwrap_or(Val::Undefined),
                None => Val::Undefined,
            },
            _ => {
                if matches!(v, Val::Null | Val::Undefined) {
                    return Val::Undefined;
                }
                let x = v.to_number();
                if x.is_nan() {
                    return Val::Undefined;
                }
                let domain = nums(&self.domain);
                let (t, _) = transforms(self.kind, &domain);
                let d: Vec<f64> = domain.iter().map(|d| t(*d)).collect();
                Val::Num(piecewise(&d, &nums(&self.range), t(x)))
            }
        }
    }

    fn invert_continuous(&self, y: f64) -> f64 {
        let domain = nums(&self.domain);
        let (t, u) = transforms(self.kind, &domain);
        let d: Vec<f64> = domain.iter().map(|d| t(*d)).collect();
        u(piecewise(&nums(&self.range), &d, y))
    }

    fn date_or_num(&self, n: f64) -> Val {
        // `new Date(n)` clips to the instants a Date holds, and is invalid past them.
        if matches!(self.kind, Kind::Time | Kind::Utc) { Val::Date(super::date::time_clip(n)) } else { Val::Num(n) }
    }

    fn invert_bands(&self, lo: f64, hi: f64) -> Val {
        let bands = self.bands.as_ref().expect("a band scale is rescaled");
        let r = nums(&self.range);
        let reverse = r[1] < r[0];
        let mut values = bands.values.clone();
        if reverse {
            values.reverse();
        }
        let n = values.len() as isize - 1;
        let (mut lo, mut hi) = (lo, hi);
        if lo.is_nan() || hi.is_nan() {
            return Val::Undefined;
        }
        if hi < lo {
            std::mem::swap(&mut lo, &mut hi);
        }
        if hi < values.first().copied().unwrap_or(f64::NAN) || lo > r[usize::from(!reverse)] {
            return Val::Undefined;
        }
        let bisect_right = |x: f64| values.partition_point(|v| *v <= x) as isize;
        let mut a = (bisect_right(lo) - 1).max(0);
        let mut b = if lo == hi { a } else { bisect_right(hi) - 1 };
        if lo - values[a as usize] > bands.bandwidth + 1e-10 {
            a += 1;
        }
        if reverse {
            let t = a;
            a = n - b;
            b = n - t;
        }
        if a > b || a < 0 || b as usize >= self.domain.len() {
            return Val::Undefined;
        }
        Val::Array(self.domain[a as usize..=b as usize].to_vec())
    }

    /// `invert(value)` the way Vega's `invert()` calls it: a scalar through `invert` (or `invertExtent`), a
    /// pair through `invertRange`.
    pub fn invert(&self, arg: &Val) -> Res<Val> {
        match (self.kind, arg) {
            (Kind::Ordinal, _) => throws("(intermediate value)(...) is not a function"),
            (Kind::Band { .. } | Kind::Point { .. }, Val::Array(pair)) => {
                if matches!(pair.first(), Some(Val::Null | Val::Undefined) | None) || matches!(pair.get(1), Some(Val::Null | Val::Undefined) | None) {
                    return Ok(Val::Undefined);
                }
                Ok(self.invert_bands(pair[0].to_number(), pair[1].to_number()))
            }
            (Kind::Band { .. } | Kind::Point { .. }, v) => {
                let x = v.to_number();
                Ok(match self.invert_bands(x, x) {
                    Val::Array(items) => items.into_iter().next().unwrap_or(Val::Undefined),
                    other => other,
                })
            }
            (_, Val::Array(pair)) => {
                let at = |i: usize| pair.get(i).map_or(Val::Undefined, Val::clone);
                let (mut lo, mut hi) = (at(0), at(1));
                if hi.less_than(&lo) == Some(true) {
                    std::mem::swap(&mut lo, &mut hi);
                }
                Ok(Val::Array(vec![self.date_or_num(self.invert_continuous(lo.to_number())), self.date_or_num(self.invert_continuous(hi.to_number()))]))
            }
            (_, v) => Ok(self.date_or_num(self.invert_continuous(v.to_number()))),
        }
    }

    /// `scale.domain()`.
    pub fn domain(&self) -> Val {
        if self.kind.continuous() && matches!(self.kind, Kind::Time | Kind::Utc) {
            return Val::Array(nums(&self.domain).into_iter().map(Val::Date).collect());
        }
        Val::Array(self.domain.clone())
    }

    /// `scale.range()`.
    pub fn range(&self) -> Val {
        Val::Array(self.range.clone())
    }

    /// `scale.bandwidth()` where the scale has one, else Vega's `0`.
    pub fn bandwidth(&self) -> f64 {
        self.bands.as_ref().map_or(0.0, |b| b.bandwidth)
    }

    pub fn step(&self) -> f64 {
        self.bands.as_ref().map_or(f64::NAN, |b| b.step)
    }

    /// `String(scale.copy())`: the source of the closure d3 builds, as the minifier left it in the bundle that
    /// renders production's charts.
    pub fn copy_source(&self) -> String {
        match self.kind {
            Kind::Band { .. } | Kind::Point { .. } | Kind::Ordinal => {
                "function a(n){let a=t.get(n);if(void 0===a){if(u!==z)return u;t.set(n,a=r.push(n)-1)}return e[a%e.length]}"
            }
            _ => "function g(t){return null==t||isNaN(t=+t)?r:(u||(u=e(o.map(n),i,c)))(n(p(t)))}",
        }
        .to_string()
    }

    /// `scale.ticks(count)` of the continuous scales that have it; `None` for a band, point or ordinal scale.
    pub fn ticks(&self, count: f64) -> Res<Option<Vec<Val>>> {
        let d = nums(&self.domain);
        let (Some(&u), Some(&v)) = (d.first(), d.last()) else { return Ok(None) };
        let count_n = if count.is_nan() { 10.0 } else { count };
        if count_n > MAX_TICKS {
            return Err(EvalError::Unmodeled(crate::refuse!("labels.expr asking a scale for more than {MAX_TICKS} ticks is not supported")));
        }
        Ok(match self.kind {
            Kind::Linear | Kind::Pow(_) | Kind::Symlog(_) => {
                Some(d3rs::ticks::d3_ticks(u, v, count_n.max(0.0) as usize).into_iter().map(Val::Num).collect())
            }
            Kind::Time | Kind::Utc => {
                let (lo, hi) = (u.min(v) / 864e5, u.max(v) / 864e5);
                // A `time` scale ticks JavaScript-local time; a `utc` one never leaves UTC.
                let zone = crate::families::temporal::ladder_zone(self.kind == Kind::Time && crate::time_zone::expression_local());
                let mut days = crate::families::temporal::zone_instants(lo, hi, count_n.max(1.0) as usize, zone.as_ref())
                    .map_err(EvalError::Throws)?;
                if v < u {
                    days.reverse();
                }
                Some(days.into_iter().map(|day| Val::Date(day * 864e5)).collect())
            }
            Kind::Log => {
                let base = 10.0_f64;
                let reflect = u.min(v) < 0.0 || d[0] < 0.0;
                let (lo, hi) = (u.min(v), u.max(v));
                let rev = v < u;
                let logs = |x: f64| if reflect { -libm::log10(-x) } else { libm::log10(x) };
                let pow10 = |x: f64| -> f64 {
                    if x.is_finite() { format!("1e{}", super::val::num_to_str(x)).parse::<f64>().unwrap_or(f64::NAN) } else if x < 0.0 { 0.0 } else { x }
                };
                let pows = |x: f64| -> f64 { if reflect { -pow10(-x) } else { pow10(x) } };
                let (mut i, mut j) = (logs(lo), logs(hi));
                let mut z: Vec<f64> = Vec::new();
                if j - i < count_n {
                    i = i.floor();
                    j = j.ceil();
                    if lo > 0.0 {
                        let mut ii = i;
                        while ii <= j {
                            for k in 1..(base as i32) {
                                let t = if ii < 0.0 { f64::from(k) / pows(-ii) } else { f64::from(k) * pows(ii) };
                                if t < lo {
                                    continue;
                                }
                                if t > hi {
                                    break;
                                }
                                z.push(t);
                            }
                            ii += 1.0;
                        }
                    } else {
                        let mut ii = i;
                        while ii <= j {
                            for k in (1..(base as i32)).rev() {
                                let t = if ii > 0.0 { f64::from(k) / pows(-ii) } else { f64::from(k) * pows(ii) };
                                if t < lo {
                                    continue;
                                }
                                if t > hi {
                                    break;
                                }
                                z.push(t);
                            }
                            ii += 1.0;
                        }
                    }
                    if (z.len() as f64) * 2.0 < count_n {
                        z = d3rs::ticks::d3_ticks(lo, hi, count_n as usize);
                    }
                } else {
                    z = d3rs::ticks::d3_ticks(i, j, (j - i).min(count_n).max(0.0) as usize).into_iter().map(pows).collect();
                }
                if rev {
                    z.reverse();
                }
                Some(z.into_iter().map(Val::Num).collect())
            }
            _ => None,
        })
    }

    /// vega-scale's `scaleFraction`: this scale's kind over `[min, max]` onto `[0, 1]`.
    pub fn fraction(&self, min: f64, max: f64) -> Scale {
        Scale::new(self.kind, vec![if matches!(self.kind, Kind::Time | Kind::Utc) { Val::Date(min) } else { Val::Num(min) }, if matches!(self.kind, Kind::Time | Kind::Utc) { Val::Date(max) } else { Val::Num(max) }], vec![Val::Num(0.0), Val::Num(1.0)])
    }
}
