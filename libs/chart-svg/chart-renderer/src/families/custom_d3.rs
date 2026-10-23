//! The `d3` namespace of a custom chart's template: d3's scales, shapes and
//! layouts as native functions over the `d3rs` ports, so a template draws with
//! the numbers the built-in families draw with. Names follow d3 in snake_case.
//!
//! Like `math`, the namespace holds plain functions and immutable callable
//! scale objects, and every misuse is an error naming the call. One deviation
//! from d3's defaults: `d3.pie` keeps data order (the query owns ordering)
//! where d3's `pie()` sorts descending by value.

use std::collections::{BTreeMap, HashMap};
use std::f64::consts::TAU;
use std::sync::Arc;

use d3rs::scale::{Band, Continuous, Transform};
use d3rs::shape::Curve;
use d3rs::format::{tick_format, spec_width};
use minijinja::value::{Kwargs, Object, ObjectRepr, Rest, Value, ValueKind};
use minijinja::{Error, State};

use super::custom::{invalid, number};

const DEFAULT_TICK_COUNT: usize = 10;

/// A tick list is built in one native call, so its size is capped here rather than by the template's instruction budget.
const MAX_TICK_COUNT: usize = 10_000;

pub(super) fn d3_namespace() -> Value {
    let mut m: BTreeMap<&str, Value> = BTreeMap::new();
    m.insert(
        "scale_linear",
        Value::from_function(|args: Rest<Value>| continuous("scale_linear", Transform::Identity, &args)),
    );
    m.insert(
        "scale_sqrt",
        Value::from_function(|args: Rest<Value>| continuous("scale_sqrt", Transform::Sqrt, &args)),
    );
    m.insert("scale_band", Value::from_function(|args: Rest<Value>| band(&args)));
    m.insert("line", Value::from_function(|args: Rest<Value>| line(&args)));
    m.insert("area", Value::from_function(|args: Rest<Value>| area(&args)));
    m.insert("arc", Value::from_function(|args: Rest<Value>| arc(&args)));
    m.insert("pie", Value::from_function(|args: Rest<Value>| pie(&args)));
    Value::from(m)
}

// ── Argument handling ───────────────────────────────────────────────────────

/// `args` bound to `names` the way Python binds them: positionals first, then
/// keywords by name. The first `required` names must be given.
fn bind<const N: usize>(
    site: &str,
    names: [&str; N],
    required: usize,
    args: &[Value],
) -> Result<[Option<Value>; N], Error> {
    let (positional, kwargs) = match args.split_last() {
        Some((last, rest)) if last.kind() == ValueKind::Map => match Kwargs::try_from(last.clone()) {
            Ok(kwargs) => (rest, Some(kwargs)),
            Err(_) => (args, None),
        },
        _ => (args, None),
    };
    if positional.len() > N {
        return Err(invalid(format!(
            "{site}: takes at most {N} arguments ({}), got {}",
            names.join(", "),
            positional.len()
        )));
    }
    let mut bound: [Option<Value>; N] = std::array::from_fn(|i| positional.get(i).cloned());
    if let Some(kwargs) = kwargs {
        for key in kwargs.args() {
            let Some(i) = names.iter().position(|name| *name == key) else {
                return Err(invalid(format!(
                    "{site}: unknown keyword argument '{key}'; takes {}",
                    names.join(", ")
                )));
            };
            if bound[i].is_some() {
                return Err(invalid(format!("{site}: got multiple values for '{key}'")));
            }
            bound[i] = Some(kwargs.peek::<Value>(key)?);
        }
    }
    if let Some(i) = (0..required).find(|i| bound[*i].is_none()) {
        return Err(invalid(format!("{site}: missing argument '{}'", names[i])));
    }
    Ok(bound)
}

fn finite(site: &str, name: &str, value: &Value) -> Result<f64, Error> {
    number(value)
        .filter(|n| n.is_finite())
        .ok_or_else(|| invalid(format!("{site}: {name} must be a finite number, got {value:?}")))
}

/// An optional finite number; `none` and absent both take `default`.
fn finite_or(site: &str, name: &str, value: Option<&Value>, default: f64) -> Result<f64, Error> {
    match value {
        Some(v) if !v.is_none() => finite(site, name, v),
        _ => Ok(default),
    }
}

/// An optional non-negative finite number; `none` and absent both take `default`.
fn non_negative_or(site: &str, name: &str, value: Option<&Value>, default: f64) -> Result<f64, Error> {
    let n = finite_or(site, name, value, default)?;
    if n < 0.0 {
        return Err(invalid(format!("{site}: {name} must not be negative, got {n}")));
    }
    Ok(n)
}

fn items(site: &str, name: &str, value: &Value) -> Result<Vec<Value>, Error> {
    match value.kind() {
        ValueKind::Seq | ValueKind::Iterable => Ok(value.try_iter()?.collect()),
        _ => Err(invalid(format!("{site}: {name} must be a list, got {value:?}"))),
    }
}

/// A two-number `[start, stop]` pair of distinct finite numbers.
fn extent_pair(site: &str, name: &str, value: &Value) -> Result<[f64; 2], Error> {
    let pair = items(site, name, value)?;
    let [a, b] = pair.as_slice() else {
        return Err(invalid(format!(
            "{site}: {name} must be two numbers [start, stop], got {} values",
            pair.len()
        )));
    };
    let (a, b) = (finite(site, name, a)?, finite(site, name, b)?);
    if a == b {
        return Err(invalid(format!("{site}: {name} must not be zero-width, got [{a}, {b}]")));
    }
    if !(b - a).is_finite() {
        return Err(invalid(format!("{site}: {name} is too wide: its span overflows")));
    }
    Ok([a, b])
}

/// A tick count: a whole number from 1 to `MAX_TICK_COUNT`, d3's default 10 when absent.
fn tick_count(site: &str, value: Option<&Value>) -> Result<usize, Error> {
    let n = finite_or(site, "count", value, DEFAULT_TICK_COUNT as f64)?;
    if n.fract() != 0.0 || !(1.0..=MAX_TICK_COUNT as f64).contains(&n) {
        return Err(invalid(format!(
            "{site}: count must be a whole number from 1 to {MAX_TICK_COUNT}, got {n}"
        )));
    }
    Ok(n as usize)
}

fn floats(values: &[f64]) -> Value {
    Value::from(values.to_vec())
}

// ── Continuous scales ───────────────────────────────────────────────────────

fn continuous(name: &'static str, transform: Transform, args: &[Value]) -> Result<Value, Error> {
    let site = format!("d3.{name}()");
    let [domain, range] = bind(&site, ["domain", "range"], 2, args)?.map(|v| v.expect("required"));
    let scale = Continuous {
        transform,
        domain: extent_pair(&site, "domain", &domain)?,
        range: extent_pair(&site, "range", &range)?,
    };
    Ok(Value::from_object(ContinuousScale { name, scale }))
}

/// `scaleLinear()` / `scaleSqrt()`.
#[derive(Debug)]
struct ContinuousScale {
    name: &'static str,
    scale: Continuous,
}

impl Object for ContinuousScale {
    fn repr(self: &Arc<Self>) -> ObjectRepr {
        ObjectRepr::Plain
    }

    fn get_value(self: &Arc<Self>, key: &Value) -> Option<Value> {
        match key.as_str()? {
            "domain" => Some(floats(&self.scale.domain)),
            "range" => Some(floats(&self.scale.range)),
            _ => None,
        }
    }

    fn call(self: &Arc<Self>, _state: &State<'_, '_>, args: &[Value]) -> Result<Value, Error> {
        let site = format!("d3.{}()", self.name);
        let [value] = bind(&site, ["value"], 1, args)?.map(|v| v.expect("required"));
        Ok(Value::from(self.scale.scale(finite(&site, "value", &value)?)))
    }

    fn call_method(
        self: &Arc<Self>,
        _state: &State<'_, '_>,
        method: &str,
        args: &[Value],
    ) -> Result<Value, Error> {
        let site = format!("d3.{}().{method}()", self.name);
        match method {
            "nice" => {
                let [count] = bind(&site, ["count"], 0, args)?;
                let scale = self.scale.nice(tick_count(&site, count.as_ref())?);
                Ok(Value::from_object(ContinuousScale { name: self.name, scale }))
            }
            "ticks" => {
                let [count] = bind(&site, ["count"], 0, args)?;
                Ok(floats(&self.scale.ticks(tick_count(&site, count.as_ref())?)))
            }
            "tick_format" => {
                let [count, spec] = bind(&site, ["count", "spec"], 0, args)?;
                let spec = match spec {
                    Some(spec) if !spec.is_none() => Some(
                        spec.as_str()
                            .ok_or_else(|| invalid(format!("{site}: spec must be a string, got {spec:?}")))?
                            .to_string(),
                    ),
                    _ => None,
                };
                let [start, stop] = self.scale.domain;
                // A bad spec fails here, where it is written, not at the first label.
                if let Some(spec) = &spec {
                    spec_width(spec).map_err(|e| invalid(format!("{site}: {e}")))?;
                }
                let count = tick_count(&site, count.as_ref())?;
                Ok(Value::from_object(TickFormatter { site, span: (start, stop), count, spec }))
            }
            "invert" => {
                let [value] = bind(&site, ["value"], 1, args)?.map(|v| v.expect("required"));
                Ok(Value::from(self.scale.invert(finite(&site, "value", &value)?)))
            }
            _ => Err(invalid(format!(
                "d3.{}(): no method {method:?}; it has nice, ticks, tick_format and invert",
                self.name
            ))),
        }
    }
}

/// What `tick_format()` returns: a callable printing a number through the
/// board's active number locale.
#[derive(Debug)]
struct TickFormatter {
    site: String,
    span: (f64, f64),
    count: usize,
    spec: Option<String>,
}

impl Object for TickFormatter {
    fn repr(self: &Arc<Self>) -> ObjectRepr {
        ObjectRepr::Plain
    }

    fn call(self: &Arc<Self>, _state: &State<'_, '_>, args: &[Value]) -> Result<Value, Error> {
        let [value] = bind(&self.site, ["value"], 1, args)?.map(|v| v.expect("required"));
        let value = finite(&self.site, "value", &value)?;
        let locale = crate::number_locale::active_number_locale();
        tick_format(&locale.locale, self.spec.as_deref(), self.span.0, self.span.1, self.count, value)
            .map(Value::from)
            .map_err(|e| invalid(format!("{}: {e}", self.site)))
    }
}

// ── Band scale ──────────────────────────────────────────────────────────────

fn band(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.scale_band()";
    let [domain, range, padding, padding_inner, padding_outer, align] = bind(
        SITE,
        ["domain", "range", "padding", "padding_inner", "padding_outer", "align"],
        2,
        args,
    )?;
    let (domain, range) = (domain.expect("required"), range.expect("required"));
    let mut values: Vec<Value> = Vec::new();
    let mut index: HashMap<Value, usize> = HashMap::new();
    for value in items(SITE, "domain", &domain)? {
        if !matches!(value.kind(), ValueKind::String | ValueKind::Number) {
            return Err(invalid(format!("{SITE}: domain values must be strings or numbers, got {value:?}")));
        }
        // d3's InternMap: a repeated domain value keeps its first position.
        if !index.contains_key(&value) {
            index.insert(value.clone(), values.len());
            values.push(value);
        }
    }
    if values.is_empty() {
        return Err(invalid(format!("{SITE}: domain must not be empty")));
    }
    let range = extent_pair(SITE, "range", &range)?;
    if padding.as_ref().is_some_and(|p| !p.is_none()) && (padding_inner.is_some() || padding_outer.is_some()) {
        return Err(invalid(format!("{SITE}: padding sets both paddings; do not combine it with padding_inner or padding_outer")));
    }
    let both = non_negative_or(SITE, "padding", padding.as_ref(), 0.0)?;
    if both > 1.0 {
        return Err(invalid(format!("{SITE}: padding must be between 0 and 1, got {both}")));
    }
    let inner = finite_or(SITE, "padding_inner", padding_inner.as_ref(), both.min(1.0))?;
    let outer = non_negative_or(SITE, "padding_outer", padding_outer.as_ref(), both)?;
    let align = finite_or(SITE, "align", align.as_ref(), 0.5)?;
    if !(0.0..=1.0).contains(&inner) || !(0.0..=1.0).contains(&align) {
        return Err(invalid(format!(
            "{SITE}: padding_inner and align must be between 0 and 1, got padding_inner {inner}, align {align}"
        )));
    }
    let layout = Band::new(values.len(), range, inner, outer, align);
    Ok(Value::from_object(BandScale { values, index, range, layout }))
}

/// `scaleBand()`: calling it maps a domain value to its band's start;
/// `bandwidth()` and `step()` are methods as in d3.
#[derive(Debug)]
struct BandScale {
    values: Vec<Value>,
    index: HashMap<Value, usize>,
    range: [f64; 2],
    layout: Band,
}

impl Object for BandScale {
    fn repr(self: &Arc<Self>) -> ObjectRepr {
        ObjectRepr::Plain
    }

    fn get_value(self: &Arc<Self>, key: &Value) -> Option<Value> {
        match key.as_str()? {
            "domain" => Some(Value::from(self.values.clone())),
            "range" => Some(floats(&self.range)),
            _ => None,
        }
    }

    fn call(self: &Arc<Self>, _state: &State<'_, '_>, args: &[Value]) -> Result<Value, Error> {
        const SITE: &str = "d3.scale_band()";
        let [value] = bind(SITE, ["value"], 1, args)?.map(|v| v.expect("required"));
        let i = self.index.get(&value).ok_or_else(|| {
            invalid(format!(
                "{SITE}: {value:?} is not in the domain {:?}",
                self.values
            ))
        })?;
        Ok(Value::from(self.layout.position(*i)))
    }

    fn call_method(
        self: &Arc<Self>,
        _state: &State<'_, '_>,
        method: &str,
        args: &[Value],
    ) -> Result<Value, Error> {
        let site = format!("d3.scale_band().{method}()");
        let value = match method {
            "bandwidth" => self.layout.bandwidth,
            "step" => self.layout.step,
            _ => {
                return Err(invalid(format!(
                    "d3.scale_band(): no method {method:?}; it has bandwidth and step"
                )))
            }
        };
        bind(&site, [], 0, args)?;
        Ok(Value::from(value))
    }
}

// ── Shapes and layouts ──────────────────────────────────────────────────────

fn curve(site: &str, name: Option<&Value>) -> Result<Curve, Error> {
    let name = match name {
        Some(v) if !v.is_none() => v.as_str().ok_or_else(|| invalid(format!("{site}: curve must be a string, got {v:?}")))?,
        _ => "linear",
    };
    Ok(match name {
        "linear" => Curve::Linear,
        "monotone_x" => Curve::MonotoneX,
        "monotone_y" => Curve::MonotoneY,
        "step" => Curve::Step,
        "step_before" => Curve::StepBefore,
        "step_after" => Curve::StepAfter,
        "basis" => Curve::Basis,
        "natural" => Curve::Natural,
        "cardinal" => Curve::Cardinal,
        _ => {
            return Err(invalid(format!(
                "{site}: unknown curve {name:?}; use linear, monotone_x, monotone_y, step, \
                 step_before, step_after, basis, natural or cardinal"
            )))
        }
    })
}

/// `[[x, y], ...]` as finite pairs.
fn points(site: &str, value: &Value) -> Result<Vec<(f64, f64)>, Error> {
    items(site, "points", value)?
        .iter()
        .enumerate()
        .map(|(i, point)| {
            let xy = items(site, &format!("points[{i}]"), point)?;
            let [x, y] = xy.as_slice() else {
                return Err(invalid(format!("{site}: points[{i}] must be an [x, y] pair, got {point:?}")));
            };
            Ok((finite(site, &format!("points[{i}][0]"), x)?, finite(site, &format!("points[{i}][1]"), y)?))
        })
        .collect()
}

fn line(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.line()";
    let [pts, curve_name] = bind(SITE, ["points", "curve"], 1, args)?;
    let pts = points(SITE, &pts.expect("required"))?;
    let curve = curve(SITE, curve_name.as_ref())?;
    Ok(Value::from(d3rs::shape::line_path(&[pts], curve)))
}

fn area(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.area()";
    let [pts, y0, curve_name] = bind(SITE, ["points", "y0", "curve"], 2, args)?;
    let pts = points(SITE, &pts.expect("required"))?;
    let y0 = finite(SITE, "y0", &y0.expect("required"))?;
    let curve = curve(SITE, curve_name.as_ref())?;
    Ok(Value::from(d3rs::shape::area_path(&[pts], y0, curve)))
}

fn arc(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.arc()";
    let [inner, outer, start, end, pad, corner] = bind(
        SITE,
        ["inner_radius", "outer_radius", "start_angle", "end_angle", "pad_angle", "corner_radius"],
        4,
        args,
    )?;
    let required = |name: &str, v: Option<Value>| finite(SITE, name, &v.expect("required"));
    let radius = |name: &str, v: Option<Value>| -> Result<f64, Error> {
        let r = required(name, v)?;
        if r < 0.0 {
            return Err(invalid(format!("{SITE}: {name} must not be negative, got {r}")));
        }
        Ok(r)
    };
    let arc = d3rs::arc::Arc {
        inner_radius: radius("inner_radius", inner)?,
        outer_radius: radius("outer_radius", outer)?,
        corner_radius: non_negative_or(SITE, "corner_radius", corner.as_ref(), 0.0)?,
        pad_radius: None,
        start_angle: required("start_angle", start)?,
        end_angle: required("end_angle", end)?,
        pad_angle: non_negative_or(SITE, "pad_angle", pad.as_ref(), 0.0)?,
    };
    Ok(Value::from(arc.path()))
}

fn pie(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.pie()";
    let [values, start, end, pad] = bind(SITE, ["values", "start_angle", "end_angle", "pad_angle"], 1, args)?;
    let values = items(SITE, "values", &values.expect("required"))?
        .iter()
        .enumerate()
        .map(|(i, v)| {
            let name = format!("values[{i}]");
            let n = finite(SITE, &name, v)?;
            if n < 0.0 {
                return Err(invalid(format!("{SITE}: {name} must not be negative, got {n}")));
            }
            Ok(n)
        })
        .collect::<Result<Vec<f64>, Error>>()?;
    let slices = d3rs::pie::pie(
        &values,
        finite_or(SITE, "start_angle", start.as_ref(), 0.0)?,
        finite_or(SITE, "end_angle", end.as_ref(), TAU)?,
        non_negative_or(SITE, "pad_angle", pad.as_ref(), 0.0)?,
    );
    Ok(Value::from(
        slices
            .iter()
            .map(|s| {
                Value::from(BTreeMap::from([
                    ("index", Value::from(s.index)),
                    ("value", Value::from(values[s.index])),
                    ("start_angle", Value::from(s.start_angle)),
                    ("end_angle", Value::from(s.end_angle)),
                    ("pad_angle", Value::from(s.pad_angle)),
                ]))
            })
            .collect::<Vec<Value>>(),
    ))
}
