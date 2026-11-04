//! The time part of a custom chart's `d3` namespace: `scale_utc` (d3-scale's `scaleUtc`), `time_format` and
//! `time_parse` (d3-time-format's `utcFormat` and `utcParse`) over `d3rs::time` and `d3rs::time_format`.
//!
//! An instant is epoch milliseconds or ISO-8601 text (a bare clock is UTC, as everywhere in the engine); every
//! list and number a template gets back is in milliseconds, and text prints in UTC through the board's time locale.

use std::sync::Arc;

use d3rs::calendar::{time_clip, MS_PER_DAY};
use d3rs::scale::Continuous;
use d3rs::time::{utc_tick_interval, utc_ticks};
use minijinja::value::{Object, ObjectRepr, Rest, Value};
use minijinja::{Error, State};

use super::custom::invalid;
use super::custom_d3::{bind, extent_pair, finite, floats, items, tick_count, text};

pub(super) fn register(m: &mut std::collections::BTreeMap<&'static str, Value>) {
    m.insert("scale_utc", Value::from_function(|args: Rest<Value>| scale_utc(&args)));
    m.insert("time_format", Value::from_function(|args: Rest<Value>| time_format(&args)));
    m.insert("time_parse", Value::from_function(|args: Rest<Value>| time_parse(&args)));
}

/// An instant in whole epoch milliseconds, from a number or from ISO-8601 text. As `new Date(+t)` does, a number
/// truncates toward zero; one past what a `Date` holds (8.64e15 ms either side of the epoch) is an error.
fn instant(site: &str, name: &str, value: &Value) -> Result<f64, Error> {
    if let Some(iso) = value.as_str() {
        return crate::time_axis::date_text_day(iso)
            .map(|day| (day * MS_PER_DAY).round())
            .ok_or_else(|| invalid(format!("{site}: {name} is not an ISO-8601 date or timestamp, got {iso:?}")));
    }
    let ms = finite(site, name, value)
        .map_err(|_| invalid(format!("{site}: {name} must be epoch milliseconds or ISO-8601 text, got {value:?}")))?;
    let clipped = time_clip(ms);
    if clipped.is_nan() {
        return Err(invalid(format!("{site}: {name} {ms} is past the instants a date holds (8.64e15 ms either side of the epoch)")));
    }
    Ok(clipped)
}

fn locale(site: &str) -> Result<Arc<d3rs::time_format::Locale>, Error> {
    crate::time_locale::active_time_locale().map(|l| Arc::new((*l).clone())).map_err(|e| invalid(format!("{site}: {e}")))
}

// ── scale_utc ───────────────────────────────────────────────────────────────

/// `d3.scale_utc(domain, range)`: d3's `scaleUtc()` over two instants and two numbers.
fn scale_utc(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.scale_utc()";
    let [domain, range] = bind(SITE, ["domain", "range"], 2, args)?.map(|v| v.expect("required"));
    let pair = items(SITE, "domain", &domain)?;
    let [start, stop] = pair.as_slice() else {
        return Err(invalid(format!("{SITE}: domain must be two instants [start, stop], got {} values", pair.len())));
    };
    let domain = [instant(SITE, "domain", start)?, instant(SITE, "domain", stop)?];
    if domain[0] == domain[1] {
        return Err(invalid(format!("{SITE}: domain must not be zero-width, got [{}, {}]", domain[0], domain[1])));
    }
    let range = extent_pair(SITE, "range", &range)?;
    Ok(Value::from_object(UtcScale { scale: Continuous::linear(domain, range) }))
}

#[derive(Debug)]
struct UtcScale {
    scale: Continuous,
}

impl UtcScale {
    fn domain(&self) -> [i64; 2] {
        [self.scale.domain[0] as i64, self.scale.domain[1] as i64]
    }
}

impl Object for UtcScale {
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
        const SITE: &str = "d3.scale_utc()";
        let [value] = bind(SITE, ["value"], 1, args)?.map(|v| v.expect("required"));
        Ok(Value::from(self.scale.scale(instant(SITE, "value", &value)?)))
    }

    fn call_method(self: &Arc<Self>, _state: &State<'_, '_>, method: &str, args: &[Value]) -> Result<Value, Error> {
        let site = format!("d3.scale_utc().{method}()");
        match method {
            "invert" => {
                let [position] = bind(&site, ["value"], 1, args)?.map(|v| v.expect("required"));
                let ms = time_clip(self.scale.invert(finite(&site, "value", &position)?));
                if ms.is_nan() {
                    return Err(invalid(format!("{site}: {position:?} lies beyond the instants a date holds")));
                }
                Ok(Value::from(ms))
            }
            "ticks" => {
                let [count] = bind(&site, ["count"], 0, args)?;
                let [start, stop] = self.domain();
                let ticks = utc_ticks(start, stop, tick_count(&site, count.as_ref())?).map_err(|e| invalid(format!("{site}: {e}")))?;
                Ok(Value::from(ticks.into_iter().map(|at| at as f64).collect::<Vec<_>>()))
            }
            "nice" => {
                let [count] = bind(&site, ["count"], 0, args)?;
                let [start, stop] = self.domain();
                let Some(interval) = utc_tick_interval(start, stop, tick_count(&site, count.as_ref())?) else {
                    return Ok(Value::from_object(UtcScale { scale: self.scale.clone() }));
                };
                let niced = interval.nice([start, stop]).map_err(|e| invalid(format!("{site}: {e}")))?;
                let scale = Continuous { domain: niced.iter().map(|&at| at as f64).collect(), ..self.scale.clone() };
                Ok(Value::from_object(UtcScale { scale }))
            }
            "tick_format" => {
                let [count, spec] = bind(&site, ["count", "spec"], 0, args)?;
                // d3's time `tickFormat` ignores the count; a bad one is still the template's mistake.
                tick_count(&site, count.as_ref())?;
                let spec = match spec {
                    Some(spec) if !spec.is_none() => Some(text(&site, "spec", &spec)?),
                    _ => None,
                };
                Ok(Value::from_object(InstantFormatter { site: "d3.scale_utc().tick_format()()".into(), spec, locale: locale(&site)? }))
            }
            _ => Err(invalid(format!(
                "d3.scale_utc(): no method {method:?}; it has nice, ticks, tick_format and invert"
            ))),
        }
    }
}

// ── time_format, time_parse ─────────────────────────────────────────────────

/// What `tick_format()` and `time_format(spec)` return: a callable printing an instant in UTC, as `spec` or, with
/// none, d3-scale's multi-scale format.
#[derive(Debug)]
struct InstantFormatter {
    site: String,
    spec: Option<String>,
    locale: Arc<d3rs::time_format::Locale>,
}

impl Object for InstantFormatter {
    fn repr(self: &Arc<Self>) -> ObjectRepr {
        ObjectRepr::Plain
    }

    fn call(self: &Arc<Self>, _state: &State<'_, '_>, args: &[Value]) -> Result<Value, Error> {
        let [value] = bind(&self.site, ["instant"], 1, args)?.map(|v| v.expect("required"));
        let ms = instant(&self.site, "instant", &value)?;
        let text = match &self.spec {
            Some(spec) => d3rs::time_format::format(spec, ms, 0.0, &self.locale),
            None => d3rs::time_format::multi_format(ms, 0.0, &self.locale),
        };
        text.map(Value::from).map_err(|e| invalid(format!("{}: {e}", self.site)))
    }
}

/// `d3.time_format(spec)`: d3's `utcFormat(spec)`, a function of an instant.
fn time_format(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.time_format()";
    let [spec] = bind(SITE, ["spec"], 1, args)?.map(|v| v.expect("required"));
    let spec = text(SITE, "spec", &spec)?;
    Ok(Value::from_object(InstantFormatter { site: format!("d3.time_format({spec:?})()"), spec: Some(spec), locale: locale(SITE)? }))
}

/// `d3.time_parse(spec)`: d3's `utcParse(spec)`, a function of text returning the instant in milliseconds, or `none`
/// where the text does not fit the spec or names no date a `Date` holds.
fn time_parse(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.time_parse()";
    let [spec] = bind(SITE, ["spec"], 1, args)?.map(|v| v.expect("required"));
    Ok(Value::from_object(InstantParser { spec: text(SITE, "spec", &spec)?, locale: locale(SITE)? }))
}

#[derive(Debug)]
struct InstantParser {
    spec: String,
    locale: Arc<d3rs::time_format::Locale>,
}

impl Object for InstantParser {
    fn repr(self: &Arc<Self>) -> ObjectRepr {
        ObjectRepr::Plain
    }

    fn call(self: &Arc<Self>, _state: &State<'_, '_>, args: &[Value]) -> Result<Value, Error> {
        let site = format!("d3.time_parse({:?})()", self.spec);
        let [value] = bind(&site, ["text"], 1, args)?.map(|v| v.expect("required"));
        let parsed = d3rs::time_format::parse(&self.spec, &text(&site, "text", &value)?, &self.locale, true)
            .map_err(|e| invalid(format!("{site}: {e}")))?;
        Ok(match parsed {
            Some(parsed) if !parsed.ms.is_nan() => Value::from(parsed.ms),
            _ => Value::from(()),
        })
    }
}
