//! Vega's expression function table: vega-expression's built-ins and vega-functions' context functions, each
//! as the generated code evaluates it.

use super::colors;
use super::date::{self, Unit};
use super::jsre;
use super::parse::Node;
use super::val::{self, throws, EvalError, Res, Text, Val};

/// How this engine answers a name in Vega's function table.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Support {
    /// Evaluated here, to what Vega computes.
    Evaluated,
    /// Evaluated here; it draws on the clock or on a random source, as it does in Vega.
    Impure,
    /// The generated code throws when the label is painted (`event` is not defined in a label encoder).
    Throws,
}

/// Every name vega-expression and vega-functions register, in the table's own spelling.
pub const TABLE: [(&str, Support); 183] = {
    use Support::*;
    [
        ("_bandwidth", Evaluated), ("_range", Evaluated), ("_scale", Evaluated), ("abs", Evaluated), ("acos", Evaluated),
        ("asin", Evaluated), ("atan", Evaluated), ("atan2", Evaluated), ("atob", Evaluated), ("bandspace", Evaluated),
        ("bandwidth", Evaluated), ("btoa", Evaluated), ("ceil", Evaluated), ("clamp", Evaluated), ("clampRange", Evaluated),
        ("containerSize", Evaluated), ("contrast", Evaluated), ("copy", Evaluated), ("cos", Evaluated),
        ("cumulativeLogNormal", Evaluated), ("cumulativeNormal", Evaluated), ("cumulativeUniform", Evaluated),
        ("data", Evaluated), ("date", Evaluated), ("datetime", Evaluated), ("day", Evaluated), ("dayAbbrevFormat", Evaluated),
        ("dayFormat", Evaluated), ("dayofyear", Evaluated), ("debug", Evaluated), ("densityLogNormal", Evaluated),
        ("densityNormal", Evaluated), ("densityUniform", Evaluated), ("domain", Evaluated), ("encode", Evaluated),
        ("encodeURIComponent", Evaluated), ("exp", Evaluated), ("extent", Evaluated), ("floor", Evaluated),
        ("flush", Evaluated), ("format", Evaluated), ("geoArea", Evaluated), ("geoBounds", Evaluated),
        ("geoCentroid", Evaluated), ("geoScale", Evaluated), ("geoShape", Evaluated), ("gradient", Evaluated),
        ("group", Throws), ("hcl", Evaluated), ("hours", Evaluated), ("hsl", Evaluated), ("hypot", Evaluated), ("if", Evaluated),
        ("inScope", Evaluated), ("indata", Evaluated), ("indexof", Evaluated), ("info", Evaluated), ("inrange", Evaluated),
        ("intersect", Evaluated), ("intersectLasso", Evaluated), ("invert", Evaluated), ("isArray", Evaluated),
        ("isBoolean", Evaluated), ("isDate", Evaluated), ("isDefined", Evaluated), ("isFinite", Evaluated), ("isNaN", Evaluated),
        ("isNumber", Evaluated), ("isObject", Evaluated), ("isRegExp", Evaluated), ("isString", Evaluated),
        ("isTuple", Evaluated), ("isValid", Evaluated), ("item", Throws), ("join", Evaluated), ("lab", Evaluated),
        ("lassoAppend", Evaluated), ("lassoPath", Evaluated), ("lastindexof", Evaluated), ("length", Evaluated),
        ("lerp", Evaluated), ("log", Evaluated), ("lower", Evaluated), ("luminance", Evaluated), ("max", Evaluated),
        ("merge", Evaluated), ("milliseconds", Evaluated), ("min", Evaluated), ("minutes", Evaluated), ("modify", Evaluated),
        ("month", Evaluated), ("monthAbbrevFormat", Evaluated), ("monthFormat", Evaluated), ("now", Impure),
        ("pad", Evaluated), ("panLinear", Evaluated), ("panLog", Evaluated), ("panPow", Evaluated), ("panSymlog", Evaluated),
        ("parseFloat", Evaluated), ("parseInt", Evaluated), ("pathShape", Evaluated), ("peek", Evaluated),
        ("pinchAngle", Evaluated), ("pinchDistance", Evaluated), ("pluck", Evaluated), ("pow", Evaluated),
        ("quantileLogNormal", Evaluated), ("quantileNormal", Evaluated), ("quantileUniform", Evaluated), ("quarter", Evaluated),
        ("random", Impure), ("range", Evaluated), ("regexp", Evaluated), ("replace", Evaluated), ("reverse", Evaluated),
        ("rgb", Evaluated), ("round", Evaluated), ("sampleLogNormal", Impure), ("sampleNormal", Impure),
        ("sampleUniform", Impure), ("scale", Evaluated), ("screen", Evaluated), ("seconds", Evaluated), ("sequence", Evaluated),
        ("setdata", Evaluated), ("sin", Evaluated), ("slice", Evaluated), ("sort", Evaluated), ("span", Evaluated),
        ("split", Evaluated), ("sqrt", Evaluated), ("substring", Evaluated), ("tan", Evaluated), ("test", Evaluated),
        ("time", Evaluated), ("timeFormat", Evaluated), ("timeOffset", Evaluated), ("timeParse", Evaluated),
        ("timeSequence", Evaluated), ("timeUnitSpecifier", Evaluated), ("timezoneoffset", Evaluated), ("toBoolean", Evaluated),
        ("toDate", Evaluated), ("toNumber", Evaluated), ("toString", Evaluated), ("treeAncestors", Evaluated),
        ("treePath", Evaluated), ("trim", Evaluated), ("truncate", Evaluated), ("upper", Evaluated), ("utc", Evaluated),
        ("utcFormat", Evaluated), ("utcOffset", Evaluated), ("utcParse", Evaluated), ("utcSequence", Evaluated),
        ("utcdate", Evaluated), ("utcday", Evaluated), ("utcdayofyear", Evaluated), ("utchours", Evaluated),
        ("utcmilliseconds", Evaluated), ("utcminutes", Evaluated), ("utcmonth", Evaluated), ("utcquarter", Evaluated),
        ("utcseconds", Evaluated), ("utcweek", Evaluated), ("utcyear", Evaluated), ("view", Throws),
        ("vlSelectionIdTest", Evaluated), ("vlSelectionResolve", Evaluated), ("vlSelectionTest", Evaluated),
        ("vlSelectionTuples", Evaluated), ("warn", Evaluated), ("week", Evaluated), ("windowSize", Evaluated), ("x", Throws),
        ("xy", Throws), ("y", Throws), ("year", Evaluated), ("zoomLinear", Evaluated), ("zoomLog", Evaluated),
        ("zoomPow", Evaluated), ("zoomSymlog", Evaluated),
    ]
};

/// Whether the function reads a scale, whose range is the plot rectangle of the pass being painted.
pub fn reads_scales(name: &str) -> bool {
    matches!(
        name,
        "scale" | "domain" | "range" | "invert" | "bandwidth" | "copy" | "_scale" | "_range" | "_bandwidth" | "gradient" | "geoArea" | "geoBounds" | "geoCentroid" | "geoShape" | "geoScale"
    )
}

/// Whether the function reads the chart's datasets.
pub fn reads_data(name: &str) -> bool {
    matches!(name, "data" | "indata" | "treePath" | "treeAncestors" | "setdata" | "modify" | "vlSelectionTest" | "vlSelectionIdTest" | "vlSelectionResolve" | "vlSelectionTuples")
}

/// Functions generated as `cast(arg0).method(...)`, whose first argument is required to compile.
const NEEDS_FIRST_ARG: [&str; 30] = [
    "date", "day", "year", "month", "hours", "minutes", "seconds", "milliseconds", "time", "timezoneoffset", "utcdate",
    "utcday", "utcyear", "utcmonth", "utchours", "utcminutes", "utcseconds", "utcmilliseconds", "length", "upper", "lower",
    "substring", "split", "trim", "test", "clamp", "if", "atob", "btoa", "parseFloat",
];

/// Resolves a call at parse time, as Vega's code generator does.
pub fn resolve(name: &str, args: Vec<Node>) -> Res<Node> {
    let Some((static_name, _)) = TABLE.iter().find(|(n, _)| *n == name) else {
        return Err(EvalError::Unknown(format!("Unrecognized function: {name}")));
    };
    match *static_name {
        "if" | "clamp" => {
            if args.len() < 3 {
                return Err(EvalError::Unknown(format!("Missing arguments to {name} function.")));
            }
            if args.len() > 3 {
                return Err(EvalError::Unknown(format!("Too many arguments to {name} function.")));
            }
        }
        "length" => {
            // `5.length` is not JavaScript.
            if let Some(Node::Num(_, raw)) = args.first() {
                if raw.chars().all(|c| c.is_ascii_digit()) {
                    return Err(EvalError::Unknown("Invalid or unexpected token".into()));
                }
            }
        }
        "data" | "treePath" | "treeAncestors" | "indata" | "vlSelectionTest" | "vlSelectionIdTest" | "vlSelectionResolve" => {
            if !matches!(args.first(), Some(Node::Lit(_) | Node::Num(..))) {
                return Err(EvalError::Unknown(
                    if name.starts_with("vlSelection") {
                        "First argument to selection functions must be a string literal."
                    } else {
                        "First argument to data functions must be a string literal."
                    }
                    .into(),
                ));
            }
            if *static_name == "indata" {
                match args.get(1) {
                    Some(Node::Lit(Val::Str(field))) => {
                        // The field is compiled into an accessor, which rejects a malformed path.
                        if let Err(EvalError::Throws(what)) = field_path(&Val::Str(field.clone())) {
                            return Err(EvalError::Unknown(what));
                        }
                    }
                    Some(Node::Lit(_) | Node::Num(..)) => {}
                    _ => return Err(EvalError::Unknown("Second argument to indata must be a string literal.".into())),
                }
            }
        }
        _ => {}
    }
    // The scale readers add their scale as a parameter, which reads the type of their first argument.
    if matches!(*static_name, "bandwidth" | "copy" | "domain" | "range" | "invert" | "scale" | "gradient" | "geoArea" | "geoBounds" | "geoCentroid" | "geoShape" | "geoScale")
        && args.is_empty()
    {
        return Err(EvalError::Unknown("TypeError: Cannot read properties of undefined (reading 'type')".into()));
    }
    if matches!(*static_name, "_scale" | "_range" | "_bandwidth") && args.is_empty() {
        return Err(EvalError::Unknown("TypeError: Cannot read properties of undefined (reading 'type')".into()));
    }
    if *static_name == "_scale" && args.len() < 2 {
        return Err(EvalError::Unknown("TypeError: Cannot read properties of undefined (reading 'type')".into()));
    }
    if NEEDS_FIRST_ARG.contains(static_name) && args.is_empty() && !matches!(*static_name, "atob" | "btoa" | "parseFloat") {
        return Err(EvalError::Unknown("TypeError: Cannot read properties of undefined (reading 'type')".into()));
    }
    if *static_name == "if" {
        let mut parts = args.into_iter();
        let (test, yes, no) = (parts.next(), parts.next(), parts.next());
        if let (Some(test), Some(yes), Some(no)) = (test, yes, no) {
            return Ok(Node::Cond(Box::new(test), Box::new(yes), Box::new(no)));
        }
        unreachable!("`if` was checked to have three arguments");
    }
    Ok(Node::Call(static_name, args))
}

struct Args<'a>(&'a [Val]);

impl Args<'_> {
    fn get(&self, i: usize) -> &Val {
        self.0.get(i).unwrap_or(&Val::Undefined)
    }
    fn num(&self, i: usize) -> f64 {
        self.get(i).to_number()
    }
    /// The argument as a string: a copy, which the evaluation is charged for.
    fn string(&self, i: usize) -> Res<String> {
        self.get(i).to_text()
    }
    fn has(&self, i: usize) -> bool {
        self.0.len() > i
    }
}

/// `s` as UTF-16 units, charged at two bytes each.
fn utf16(s: &str) -> Res<Vec<u16>> {
    val::charge(val::utf16_len(s).saturating_mul(2))?;
    Ok(s.encode_utf16().collect())
}

/// Units as a string, charged at the three bytes a unit takes in UTF-8.
fn from16(u: &[u16]) -> Res<String> {
    val::charge(u.len().saturating_mul(3))?;
    Ok(String::from_utf16_lossy(u))
}

/// `ToIntegerOrInfinity`.
fn integer(n: f64) -> f64 {
    if n.is_nan() { 0.0 } else { n.trunc() }
}

/// A relative index clamped to `0..=len` (`slice`, `lastIndexOf`).
fn relative(n: f64, len: usize) -> usize {
    let n = integer(n);
    let len = len as f64;
    (if n < 0.0 { (len + n).max(0.0) } else { n.min(len) }) as usize
}

/// V8's `Math.hypot` over the arguments' numbers.
pub(super) fn hypot(vals: &[Val]) -> f64 {
    d3rs::js_math::hypot(&vals.iter().map(Val::to_number).collect::<Vec<_>>())
}

/// `Math.round`, which keeps the sign of a zero it rounds a small negative to.
pub(super) fn js_round(x: f64) -> f64 {
    let r = d3rs::js_math::js_round(x);
    if r == 0.0 && x.is_sign_negative() { -0.0 } else { r }
}

pub(super) fn js_pow(base: f64, exp: f64) -> f64 {
    if exp.is_nan() || (base.abs() == 1.0 && exp.is_infinite()) {
        f64::NAN
    } else {
        base.powf(exp)
    }
}

fn js_max(args: &[Val]) -> f64 {
    let mut out = f64::NEG_INFINITY;
    for a in args {
        let n = a.to_number();
        if n.is_nan() {
            return f64::NAN;
        }
        if n > out || (n == 0.0 && out == 0.0 && !n.is_sign_negative()) {
            out = n;
        }
    }
    out
}

fn js_min(args: &[Val]) -> f64 {
    let mut out = f64::INFINITY;
    for a in args {
        let n = a.to_number();
        if n.is_nan() {
            return f64::NAN;
        }
        if n < out || (n == 0.0 && out == 0.0 && n.is_sign_negative()) {
            out = n;
        }
    }
    out
}

fn to_date_ms(v: &Val) -> Res<f64> {
    Ok(match v {
        Val::Date(ms) => *ms,
        Val::Undefined => f64::NAN,
        Val::Null => 0.0,
        Val::Bool(b) => f64::from(*b),
        Val::Num(n) => date::time_clip(*n),
        Val::Str(s) => date::parse_date(s)?,
        other => date::parse_date(&other.to_text()?)?,
    })
}

/// `new Date(...args)`.
fn new_date(args: &[Val]) -> Res<f64> {
    Ok(match args {
        [] => now(),
        [one] => to_date_ms(one)?,
        many => {
            let n = |i: usize, default: f64| many.get(i).map_or(default, Val::to_number);
            date::make_time(date::full_year(n(0, f64::NAN)), n(1, 0.0), n(2, 1.0), n(3, 0.0), n(4, 0.0), n(5, 0.0), n(6, 0.0))
        }
    })
}

fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0.0, |d| d.as_millis() as f64)
}

thread_local!(static RANDOM_STATE: std::cell::Cell<u64> = std::cell::Cell::new(now() as u64 ^ 0x9E37_79B9_7F4A_7C15));

/// Starts a render's `random()` from a fresh seed, as a new Vega view gets its own.
pub(super) fn reseed_random() {
    RANDOM_STATE.with(|s| s.set(now() as u64 ^ 0x9E37_79B9_7F4A_7C15));
}

fn random() -> f64 {
    RANDOM_STATE.with(|s| {
        let mut x = s.get() | 1;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        s.set(x);
        (x >> 11) as f64 / (1u64 << 53) as f64
    })
}

/// The elements `v[0] .. v[v.length - 1]` read: a string's code units, an array's items, none for any other
/// value; `null` and `undefined` throw when `strict` (the member read the generated code makes first).
fn elements(v: &Val, strict: bool) -> Res<Vec<Val>> {
    Ok(match v {
        Val::Array(items) => copied(items)?,
        Val::Str(s) => {
            array_fits(utf16_len_of(s))?;
            s.encode_utf16().map(|u| Val::Str(Text::unit(u))).collect()
        }
        Val::Undefined | Val::Null if strict => return throws(format!("Cannot read properties of {} (reading '0')", v.to_js_string())),
        _ => Vec::new(),
    })
}

/// `v[v.length - 1]`.
fn last_of(v: &Val) -> Res<Val> {
    let len = v.property(&Val::Str("length".into()))?.to_number();
    v.property(&Val::Num(len - 1.0))
}

fn array_arg<'a>(v: &'a Val, what: &str) -> Res<&'a Vec<Val>> {
    match v {
        Val::Array(items) => Ok(items),
        other => throws(format!("{what} is not a function on {}", other.to_js_string())),
    }
}

/// A copy of `items`, charged for what it holds.
fn copied(items: &[Val]) -> Res<Vec<Val>> {
    val::charge(items.iter().map(Val::weight).sum())?;
    Ok(items.to_vec())
}

fn regex_arg(v: &Val) -> Res<jsre::JsRegex> {
    match v {
        Val::Regex(source, flags) => jsre::compile(source, flags).map_err(|e| e.into_eval(EvalError::Throws)),
        Val::Undefined => jsre::compile("(?:)", "").map_err(|e| e.into_eval(EvalError::Throws)),
        other => jsre::compile(&other.to_js_string(), "").map_err(|e| e.into_eval(EvalError::Throws)),
    }
}

/// Fails as the sequence limit does for an array of `n` items.
fn array_fits(n: usize) -> Res<()> {
    if n > MAX_SEQUENCE {
        return Err(sequence_too_long());
    }
    val::reserve(n.saturating_mul(val::ELEMENT_BYTES))
}

fn utf16_len_of(s: &str) -> usize {
    val::utf16_len(s)
}

fn replace(text: &str, pattern: &Val, repl: &str) -> Res<Text> {
    match pattern {
        Val::Str(s) => {
            let Some(at) = text.find(s.as_str()) else { return Text::new(text.to_string()) };
            let m = jsre::Match { start: at, end: at + s.len(), groups: Vec::new() };
            let mut out = val::Bounded::default();
            out.push_str(&text[..at])?;
            out.push_str(&jsre::substitute(repl, text, &m, None)?)?;
            out.push_str(&text[m.end..])?;
            Ok(out.finish())
        }
        Val::Regex(source, flags) => {
            let re = jsre::compile(source, flags).map_err(|e| e.into_eval(EvalError::Throws))?;
            let (text, repl) = (re.prepare(text)?, re.prepare(repl)?);
            let mut out = val::Bounded::default();
            let mut last = 0;
            for m in re.matches(&text)? {
                out.push_str(&text[last..m.start])?;
                out.push_str(&jsre::substitute(&repl, &text, &m, Some(&re))?)?;
                last = m.end;
            }
            out.push_str(&text[last..])?;
            Text::new(re.restore(&out.finish())?)
        }
        _ => throws("Please pass a string or RegExp argument to replace."),
    }
}

fn split(text: &str, sep: &Val, limit: &Val) -> Res<Val> {
    let lim = if matches!(limit, Val::Undefined) { u32::MAX } else { val::to_int32(limit.to_number()) as u32 };
    if lim == 0 {
        return Ok(Val::Array(Vec::new()));
    }
    let mut parts: Vec<Val> = Vec::new();
    match sep {
        Val::Undefined => parts.push(Val::string(text.to_string())?),
        Val::Regex(source, flags) => {
            let re = jsre::compile(source, &flags.replace('g', "").replace('y', "")).map_err(|e| e.into_eval(EvalError::Throws))?;
            let prepared = re.prepare(text)?;
            let text = prepared.as_str();
            if text.is_empty() {
                if !re.is_match(text)? {
                    parts.push(Val::Str("".into()));
                }
            } else {
                let mut last = 0;
                let mut at = 0;
                while at < text.len() {
                    let Some(m) = re.find_from(text, at)? else { break };
                    if m.end == m.start && (m.start >= text.len() || m.start == last) {
                        at = m.start + text[m.start..].chars().next().map_or(1, char::len_utf8);
                        continue;
                    }
                    if m.start >= text.len() {
                        break;
                    }
                    parts.push(Val::string(text[last..m.start].to_string())?);
                    for g in &m.groups {
                        parts.push(g.map_or(Ok(Val::Undefined), |(a, b)| Val::string(text[a..b].to_string()))?);
                    }
                    array_fits(parts.len())?;
                    last = m.end;
                    at = if m.end == m.start { m.end + text[m.end..].chars().next().map_or(1, char::len_utf8) } else { m.end };
                }
                parts.push(Val::string(text[last..].to_string())?);
            }
            for part in &mut parts {
                if let Val::Str(p) = part {
                    *p = Text::new(re.restore(p)?)?;
                }
            }
        }
        other => {
            let s = other.to_js_string();
            if s.is_empty() {
                array_fits(val::utf16_len(text))?;
                for u in text.encode_utf16() {
                    parts.push(Val::Str(Text::unit(u)));
                }
            } else {
                for p in text.split(s.as_str()) {
                    array_fits(parts.len())?;
                    parts.push(Val::string(p.to_string())?);
                }
            }
        }
    }
    parts.truncate(lim as usize);
    Ok(Val::Array(parts))
}

fn parse_float(s: &str) -> f64 {
    let t = s.trim_start_matches(val::is_js_space);
    let b = t.as_bytes();
    let mut i = 0;
    if matches!(b.first(), Some(b'+' | b'-')) {
        i = 1;
    }
    if t[i..].starts_with("Infinity") {
        return if b.first() == Some(&b'-') { f64::NEG_INFINITY } else { f64::INFINITY };
    }
    let digits = |i: &mut usize| {
        let s = *i;
        while *i < b.len() && b[*i].is_ascii_digit() {
            *i += 1;
        }
        *i - s
    };
    let int = digits(&mut i);
    let mut frac = 0;
    if b.get(i) == Some(&b'.') {
        let save = i;
        i += 1;
        frac = digits(&mut i);
        if int == 0 && frac == 0 {
            i = save;
        }
    }
    if int + frac == 0 {
        return f64::NAN;
    }
    if matches!(b.get(i), Some(b'e' | b'E')) {
        let save = i;
        i += 1;
        if matches!(b.get(i), Some(b'+' | b'-')) {
            i += 1;
        }
        if digits(&mut i) == 0 {
            i = save;
        }
    }
    t[..i].parse().unwrap_or(f64::NAN)
}

fn parse_int(s: &str, radix: f64) -> f64 {
    let t = s.trim_start_matches(val::is_js_space);
    let (sign, mut rest) = match t.chars().next() {
        Some('-') => (-1.0, &t[1..]),
        Some('+') => (1.0, &t[1..]),
        _ => (1.0, t),
    };
    let mut r = val::to_int32(radix);
    let mut strip = true;
    if r != 0 {
        if !(2..=36).contains(&r) {
            return f64::NAN;
        }
        if r != 16 {
            strip = false;
        }
    } else {
        r = 10;
    }
    if strip && (rest.starts_with("0x") || rest.starts_with("0X")) {
        rest = &rest[2..];
        r = 16;
    }
    let digits: Vec<u32> = rest.chars().map_while(|c| c.to_digit(r as u32)).collect();
    if digits.is_empty() {
        return f64::NAN;
    }
    sign * digits.iter().fold(0.0, |acc, d| acc * f64::from(r) + f64::from(*d))
}

fn btoa(s: &str) -> Res<Val> {
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    val::charge(s.len())?;
    let bytes: Vec<u8> = s.chars().map(|c| u8::try_from(c as u32).map_err(|_| EvalError::Throws("Invalid character".into()))).collect::<Res<_>>()?;
    let encoded = bytes.len().div_ceil(3) as f64 * 4.0;
    val::building(encoded, encoded)?;
    let mut out = val::Bounded::default();
    for chunk in bytes.chunks(3) {
        let n = chunk.iter().enumerate().fold(0u32, |acc, (i, b)| acc | u32::from(*b) << (16 - 8 * i));
        for k in 0..4 {
            if k <= chunk.len() {
                out.push(ALPHABET[(n >> (18 - 6 * k) & 63) as usize] as char)?;
            } else {
                out.push('=')?;
            }
        }
    }
    Ok(Val::Str(out.finish()))
}

fn atob(s: &str) -> Res<String> {
    let bad = || throws("The string to be decoded is not correctly encoded.");
    // A character outside ASCII is no base64 digit, so it fails here as it does at the digit.
    if !s.is_ascii() {
        return bad();
    }
    let mut data: Vec<u8> = s.bytes().filter(|c| !matches!(c, b'\t' | b'\n' | 0x0c | b'\r' | b' ')).collect();
    if data.len() % 4 == 0 {
        for _ in 0..2 {
            if data.last() == Some(&b'=') {
                data.pop();
            }
        }
    }
    if data.len() % 4 == 1 {
        return bad();
    }
    let value = |c: u8| -> Option<u32> {
        match c {
            b'A'..=b'Z' => Some(u32::from(c) - 65),
            b'a'..=b'z' => Some(u32::from(c) - 71),
            b'0'..=b'9' => Some(u32::from(c) + 4),
            b'+' => Some(62),
            b'/' => Some(63),
            _ => None,
        }
    };
    let mut out = String::new();
    for chunk in data.chunks(4) {
        let mut n = 0u32;
        for (i, c) in chunk.iter().enumerate() {
            let Some(v) = value(*c) else { return bad() };
            n |= v << (18 - 6 * i);
        }
        for k in 0..chunk.len() - 1 {
            out.push(char::from((n >> (16 - 8 * k) & 255) as u8));
        }
    }
    Ok(out)
}

fn encode_uri_component(s: &str) -> Res<Val> {
    let kept = |b: u8| b.is_ascii_alphanumeric() || b"-_.!~*'()".contains(&b);
    let encoded: f64 = s.bytes().map(|b| if kept(b) { 1.0 } else { 3.0 }).sum();
    val::building(encoded, encoded)?;
    let mut out = val::Bounded::default();
    for b in s.bytes() {
        if kept(b) {
            out.push(b as char)?;
        } else {
            out.push_str(&format!("%{b:02X}"))?;
        }
    }
    Ok(Val::Str(out.finish()))
}

/// The longest array `sequence` and `timeSequence` build here: V8 builds longer ones (up to 2^32 - 1 items) by
/// spending gigabytes this engine will not.
const MAX_SEQUENCE: usize = 2_000_000;

/// What an entry of a merged object costs: its key, its slot and the key again in the index.
const MERGE_ENTRY_BYTES: usize = 192;

fn sequence_too_long() -> EvalError {
    EvalError::Unmodeled(crate::refuse!("labels.expr building a sequence of more than {MAX_SEQUENCE} items is not supported"))
}

/// The board's time vocabulary, which production stamps into the Vega config `timeFormat` and `timeParse` read.
fn time_locale() -> Res<std::rc::Rc<crate::time_locale::TimeLocale>> {
    crate::time_locale::active_time_locale().map_err(EvalError::Unknown)
}

/// vega-util's `repeat`, which concatenates `floor(reps)` copies of `c` until V8 refuses the string.
fn repeat_text(c: &str, reps: f64) -> Res<String> {
    let copies = if reps >= 1.0 { reps.floor() } else { 0.0 };
    val::building(copies * val::utf16_len(c) as f64, copies * c.len() as f64)?;
    Ok(c.repeat(copies as usize))
}

fn pad(args: &Args) -> Res<Val> {
    let s = args.get(0).to_text()?;
    let c = if args.get(2).truthy() { args.get(2).to_js_string() } else { " ".into() };
    let n = args.num(1) - utf16(&s)?.len() as f64;
    let mut out = val::Bounded::default();
    if n <= 0.0 {
        out.push_str(&s)?;
    } else {
        match args.get(3) {
            Val::Str(a) if a == "left" => {
                out.push_str(&repeat_text(&c, n)?)?;
                out.push_str(&s)?;
            }
            Val::Str(a) if a == "center" => {
                out.push_str(&repeat_text(&c, f64::from(val::to_int32(n / 2.0)))?)?;
                out.push_str(&s)?;
                out.push_str(&repeat_text(&c, (n / 2.0).ceil())?)?;
            }
            _ => {
                out.push_str(&s)?;
                out.push_str(&repeat_text(&c, n)?)?;
            }
        }
    }
    Ok(Val::Str(out.finish()))
}

fn truncate(args: &Args) -> Res<Val> {
    let ellipsis = if matches!(args.get(3), Val::Undefined | Val::Null) { "\u{2026}".to_string() } else { args.get(3).to_js_string() };
    let s = args.get(0).to_text()?;
    let su = utf16(&s)?;
    let n = su.len() as f64;
    let length = args.num(1);
    let l = (length - utf16(&ellipsis)?.len() as f64).max(0.0);
    let slice = |a: f64, b: Option<f64>| -> Res<String> {
        let from = relative(a, su.len());
        let to = b.map_or(su.len(), |b| relative(b, su.len()));
        from16(&su[from..to.max(from)])
    };
    let mut out = val::Bounded::default();
    if n <= length {
        out.push_str(&s)?;
    } else {
        match args.get(2) {
            Val::Str(a) if a == "left" => {
                out.push_str(&ellipsis)?;
                out.push_str(&slice(n - l, None)?)?;
            }
            Val::Str(a) if a == "center" => {
                out.push_str(&slice(0.0, Some((l / 2.0).ceil()))?)?;
                out.push_str(&ellipsis)?;
                out.push_str(&slice(n - f64::from(val::to_int32(l / 2.0)), None)?)?;
            }
            _ => {
                out.push_str(&slice(0.0, Some(l))?)?;
                out.push_str(&ellipsis)?;
            }
        }
    }
    Ok(Val::Str(out.finish()))
}

/// vega-util `ascending`.
fn ascending(u: &Val, v: &Val) -> i32 {
    let nullish = |x: &Val| matches!(x, Val::Undefined | Val::Null);
    let lt = |a: &Val, b: &Val| a.less_than(b) == Some(true);
    if (lt(u, v) || nullish(u)) && !nullish(v) {
        return -1;
    }
    if (lt(v, u) || nullish(v)) && !nullish(u) {
        return 1;
    }
    // `((v = +v), (u = +u)) !== u && v === v` reduces to "u is NaN and v is not" once both are numbers.
    let as_num = |x: &Val| if let Val::Date(ms) = x { Val::Num(*ms) } else { x.clone() };
    let nan = |x: &Val| matches!(as_num(x), Val::Num(n) if n.is_nan());
    if nan(u) && !nan(v) {
        return -1;
    }
    if nan(v) && !nan(u) {
        return 1;
    }
    0
}

/// `Array.prototype.sort`: V8's TimSort, which `d3rs` ports for runs shorter than 64.
fn v8_sort(items: Vec<Val>, cmp: fn(&Val, &Val) -> i32) -> Res<Vec<Val>> {
    let mut order: Vec<usize> = (0..items.len()).collect();
    d3rs::js_sort::js_sort(&mut order, |a, b| f64::from(cmp(&items[a], &items[b])))
        .map_err(|_| EvalError::Unmodeled(crate::refuse!("labels.expr sorting 64 or more items is not supported")))?;
    Ok(order.into_iter().map(|i| items[i].clone()).collect())
}

fn find_index(items: &[Val], needle: &Val, from: usize) -> Option<usize> {
    (from..items.len()).find(|i| items[*i].strict_eq(needle))
}

fn domain_arg(v: &Val) -> Res<Vec<f64>> {
    match v {
        Val::Array(items) if !items.is_empty() => Ok(items.iter().map(Val::to_number).collect()),
        Val::Array(_) => throws("Domain array must not be empty"),
        other => throws(format!("Cannot read properties of {} (reading 'length')", other.to_js_string())),
    }
}

fn lift_ground(kind: &str, p: f64, domain0: f64) -> (Box<dyn Fn(f64) -> f64>, Box<dyn Fn(f64) -> f64>) {
    let sign = if domain0 > 0.0 { 1.0 } else if domain0 < 0.0 { -1.0 } else { domain0 };
    match kind {
        "linear" => (Box::new(|x| x), Box::new(|x| x)),
        "log" => (Box::new(move |x| libm::log(sign * x)), Box::new(move |x| sign * libm::exp(x))),
        "pow" => (
            Box::new(move |x| if x < 0.0 { -js_pow(-x, p) } else { js_pow(x, p) }),
            Box::new(move |x| if x < 0.0 { -js_pow(-x, 1.0 / p) } else { js_pow(x, 1.0 / p) }),
        ),
        _ => (
            Box::new(move |x| js_sign(x) * libm::log1p((x / p).abs())),
            Box::new(move |x| js_sign(x) * libm::expm1(x.abs()) * p),
        ),
    }
}

pub(super) fn js_sign(x: f64) -> f64 {
    if x.is_nan() || x == 0.0 { x } else { x.signum() }
}

fn pan_zoom(name: &str, args: &Args) -> Res<Val> {
    let domain = domain_arg(args.get(0))?;
    let zoom = name.starts_with("zoom");
    let kind = if name.ends_with("Linear") { "linear" } else if name.ends_with("Log") { "log" } else if name.ends_with("Pow") { "pow" } else { "symlog" };
    let extra = if zoom { 3 } else { 2 };
    let param = if kind == "pow" || kind == "symlog" { args.num(extra) } else { f64::NAN };
    let (lift, ground) = lift_ground(kind, param, domain[0]);
    let coerce = |v: &Val| if matches!(v, Val::Null | Val::Undefined) || matches!(v, Val::Str(s) if s.is_empty()) { 0.0 } else { v.to_number() };
    let d_first = if kind == "linear" { coerce(args.get(0).clone().as_array_first()) } else { lift(domain[0]) };
    let d_last = if kind == "linear" { coerce(args.get(0).clone().as_array_last()) } else { lift(*domain.last().unwrap_or(&f64::NAN)) };
    let (a, b) = if zoom {
        let scale = args.num(2);
        let anchor = if matches!(args.get(1), Val::Undefined | Val::Null) { (d_first + d_last) / 2.0 } else if kind == "linear" { coerce(args.get(1)) } else { lift(args.num(1)) };
        (ground(anchor + (d_first - anchor) * scale), ground(anchor + (d_last - anchor) * scale))
    } else {
        let dd = (d_last - d_first) * args.num(1);
        (ground(d_first - dd), ground(d_last - dd))
    };
    Ok(Val::Array(vec![Val::Num(a), Val::Num(b)]))
}

impl Val {
    fn as_array_first(&self) -> &Val {
        match self {
            Val::Array(items) => items.first().unwrap_or(&Val::Undefined),
            _ => &Val::Undefined,
        }
    }
    fn as_array_last(&self) -> &Val {
        match self {
            Val::Array(items) => items.last().unwrap_or(&Val::Undefined),
            _ => &Val::Undefined,
        }
    }
}

fn sqrt2pi() -> f64 {
    (2.0 * std::f64::consts::PI).sqrt()
}

fn cumulative_normal(value: f64, mean: f64, stdev: f64) -> f64 {
    let z = (value - mean) / stdev;
    let zz = z.abs();
    let cd = if zz > 37.0 {
        0.0
    } else {
        let e = libm::exp(-zz * zz / 2.0);
        if zz < 7.071_067_811_865_47 {
            let mut s = 3.526_249_659_989_11e-2 * zz + 0.700_383_064_443_688;
            for c in [6.373_962_203_531_65, 33.912_866_078_383, 112.079_291_497_871, 221.213_596_169_931, 220.206_867_912_376] {
                s = s * zz + c;
            }
            let cd = e * s;
            let mut s = 8.838_834_764_831_84e-2 * zz + 1.755_667_163_182_64;
            for c in [16.064_177_579_207, 86.780_732_202_946_1, 296.564_248_779_674, 637.333_633_378_831, 793.826_512_519_948, 440.413_735_824_752] {
                s = s * zz + c;
            }
            cd / s
        } else {
            let mut s = zz + 0.65;
            s = zz + 4.0 / s;
            s = zz + 3.0 / s;
            s = zz + 2.0 / s;
            s = zz + 1.0 / s;
            e / s / 2.506_628_274_631
        }
    };
    if z > 0.0 { 1.0 - cd } else { cd }
}

fn erfinv(x: f64) -> f64 {
    let mut w = -libm::log((1.0 - x) * (1.0 + x));
    let horner = |coeffs: &[f64], w: f64| coeffs.iter().skip(1).fold(coeffs[0], |p, c| c + p * w);
    let p = if w < 6.25 {
        w -= 3.125;
        horner(
            &[
                -3.644_412_064_017_819_7e-21, -1.685_059_138_182_016_6e-19, 1.285_848_071_525_640_0e-18, 1.115_787_767_802_518_1e-17,
                -1.333_171_662_854_620_9e-16, 2.097_276_787_596_856_2e-17, 6.637_638_134_358_324e-15, -4.054_566_272_975_207e-14,
                -8.151_934_197_605_472e-14, 2.633_509_315_308_232_3e-12, -1.297_513_325_345_353_2e-11, -5.415_412_054_294_628e-11,
                1.051_212_273_321_532_3e-9, -4.112_633_980_346_984e-9, -2.907_036_995_788_200_5e-8, 4.234_787_782_793_24e-7,
                -1.365_469_200_083_467_9e-6, -1.388_252_336_278_646_9e-5, 1.867_342_080_340_571_4e-4, -7.407_025_341_662_67e-4,
                -6.033_670_871_430_149e-3, 0.240_158_182_425_589_6, 1.653_654_562_683_102_7,
            ],
            w,
        )
    } else if w < 16.0 {
        w = w.sqrt() - 3.25;
        horner(
            &[
                2.213_737_692_177_578_7e-9, 9.075_656_193_888_539e-8, -2.751_740_629_706_454_5e-7, 1.823_962_921_438_922_8e-8,
                1.502_740_396_890_982_8e-6, -4.013_867_526_981_546e-6, 2.923_444_908_995_544_6e-6, 1.247_530_448_167_177_9e-5,
                -4.731_822_900_905_573e-5, 6.828_485_145_957_317e-5, 2.403_111_038_709_789_4e-5, -3.550_375_203_628_475e-4,
                9.532_893_797_373_805e-4, -1.688_275_556_023_504_7e-3, 2.491_442_096_107_851e-3, -3.751_208_507_569_241e-3,
                5.370_914_553_590_064e-3, 1.005_258_967_694_159_2, 3.083_885_610_492_220_7,
            ],
            w,
        )
    } else if w.is_finite() {
        w = w.sqrt() - 5.0;
        horner(
            &[
                -2.710_992_061_643_857_3e-11, -2.555_641_816_996_525_2e-10, 1.507_657_269_350_054_8e-9, -3.789_465_440_126_737e-9,
                7.615_701_208_078_339e-9, -1.496_002_662_714_924e-8, 2.914_795_345_090_108e-8, -6.771_199_775_845_234e-8,
                2.290_048_222_802_665_5e-7, -9.929_827_294_231_7e-7, 4.526_062_597_223_154e-6, -1.968_177_810_553_167e-5,
                7.599_527_703_001_776e-5, -2.150_301_193_004_447_7e-4, -1.387_193_183_362_312_2e-4, 1.010_300_464_864_534_4,
                4.849_906_401_408_584_5,
            ],
            w,
        )
    } else {
        f64::INFINITY
    };
    p * x
}

fn quantile_normal(p: f64, mean: f64, stdev: f64) -> f64 {
    if p < 0.0 || p > 1.0 { f64::NAN } else { mean + stdev * std::f64::consts::SQRT_2 * erfinv(2.0 * p - 1.0) }
}

/// `x || 0` and `x == null ? 1 : x` applied to a numeric parameter.
fn or_zero(v: &Val) -> f64 {
    if v.truthy() { v.to_number() } else { 0.0 }
}

fn or_one(v: &Val) -> f64 {
    if matches!(v, Val::Undefined | Val::Null) { 1.0 } else { v.to_number() }
}

fn uniform_bounds(a: &Val, b: &Val) -> (Val, Val) {
    if matches!(b, Val::Undefined | Val::Null) {
        (Val::Num(0.0), if matches!(a, Val::Undefined | Val::Null) { Val::Num(1.0) } else { a.clone() })
    } else {
        (a.clone(), b.clone())
    }
}

fn time_unit_specifier(args: &Args) -> Res<Val> {
    const UNITS: [&str; 11] = ["year", "quarter", "month", "week", "date", "day", "dayofyear", "hours", "minutes", "seconds", "milliseconds"];
    let mut spec: Vec<(String, String)> = [
        ("year", "%Y "), ("quarter", "Q%q "), ("month", "%b "), ("date", "%d "), ("week", "W%U "), ("day", "%a "),
        ("dayofyear", "%j "), ("hours", "%H:00"), ("minutes", "00:%M"), ("seconds", ":%S"), ("milliseconds", ".%L"),
        ("year-month", "%Y-%m "), ("year-month-date", "%Y-%m-%d "), ("hours-minutes", "%H:%M"),
    ]
    .iter()
    .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
    .collect();
    if let Val::Object(fields) = args.get(1) {
        for (k, v) in fields {
            if let Some(slot) = spec.iter_mut().find(|(sk, _)| sk == k) {
                slot.1 = v.to_js_string();
            } else {
                spec.push((k.clone(), v.to_js_string()));
            }
        }
    }
    let mut units: Vec<String> = match args.get(0) {
        Val::Undefined | Val::Null => Vec::new(),
        Val::Array(items) => items.iter().map(Val::to_js_string).collect(),
        other => vec![other.to_js_string()],
    };
    if units.is_empty() {
        return throws("Missing time unit.");
    }
    for u in &units {
        if !UNITS.contains(&u.as_str()) {
            return throws(format!("Invalid time unit: {u}."));
        }
    }
    let has = |n: &str| units.iter().any(|u| u == n);
    let kinds = usize::from(has("week") || has("day")) + usize::from(has("quarter") || has("month") || has("date")) + usize::from(has("dayofyear"));
    if kinds > 1 {
        return throws(format!("Incompatible time units: {}", args.get(0).to_js_string()));
    }
    let rank = |u: &str| UNITS.iter().position(|x| *x == u).unwrap_or(0);
    units.sort_by_key(|u| rank(u));
    let mut fmt = String::new();
    let mut start = 0;
    while start < units.len() {
        let matched = (start + 1..=units.len()).rev().find_map(|end| {
            let key = units[start..end].join("-");
            spec.iter().find(|(k, _)| *k == key).map(|(_, v)| (end, v.clone()))
        });
        let Some((end, text)) = matched else { break };
        fmt.push_str(&text);
        start = end;
    }
    Val::string(val::trim_js(&fmt).to_string())
}

/// d3's `rgbConvert` input rule: a color object stands for itself, anything else is read as CSS text, and what
/// d3 cannot read is the all-NaN color.
fn color_of(v: &Val) -> colors::Color {
    match v {
        Val::Color(c) => *c,
        other => colors::Color::parse(&other.to_js_string()).unwrap_or(colors::Color::rgb(f64::NAN, f64::NAN, f64::NAN, f64::NAN)),
    }
}

/// Evaluates a call into Vega's function table.
pub fn call(name: &str, vals: &[Val], datum: &Val) -> Res<Val> {
    let a = Args(vals);
    let n = |i: usize| a.num(i);
    let date_arg = || to_date_ms(a.get(0));
    let part = |f: fn(&date::Dt) -> i64| -> Res<Val> { Ok(Val::Num(date::breakdown(date_arg()?).map_or(f64::NAN, |d| f(&d) as f64))) };
    Ok(match name {
        // ---- math
        "isNaN" => Val::Bool(matches!(a.get(0), Val::Num(x) if x.is_nan())),
        "isFinite" => Val::Bool(matches!(a.get(0), Val::Num(x) if x.is_finite())),
        "abs" => Val::Num(n(0).abs()),
        "acos" => Val::Num(libm::acos(n(0))),
        "asin" => Val::Num(libm::asin(n(0))),
        "atan" => Val::Num(libm::atan(n(0))),
        "atan2" => Val::Num(libm::atan2(n(0), n(1))),
        "ceil" => Val::Num(n(0).ceil()),
        "cos" => Val::Num(libm::cos(n(0))),
        "exp" => Val::Num(libm::exp(n(0))),
        "floor" => Val::Num(n(0).floor()),
        "hypot" => Val::Num(hypot(vals)),
        "log" => Val::Num(libm::log(n(0))),
        "max" => Val::Num(js_max(vals)),
        "min" => Val::Num(js_min(vals)),
        "pow" => Val::Num(js_pow(n(0), n(1))),
        "random" => Val::Num(random()),
        "round" => Val::Num(js_round(n(0))),
        "sin" => Val::Num(libm::sin(n(0))),
        "sqrt" => Val::Num(n(0).sqrt()),
        "tan" => Val::Num(libm::tan(n(0))),
        "clamp" => Val::Num(js_max(&[a.get(1).clone(), Val::Num(js_min(&[a.get(2).clone(), a.get(0).clone()]))])),
        // ---- date
        "now" => Val::Num(now()),
        "utc" => {
            let g = |i: usize, d: f64| if a.has(i) { a.num(i) } else { d };
            Val::Num(date::make_time(date::full_year(g(0, f64::NAN)), g(1, 0.0), g(2, 1.0), g(3, 0.0), g(4, 0.0), g(5, 0.0), g(6, 0.0)))
        }
        "datetime" => Val::Date(new_date(vals)?),
        "date" | "utcdate" => part(|d| d.day)?,
        "day" | "utcday" => part(|d| d.weekday)?,
        "year" | "utcyear" => part(|d| d.year)?,
        "month" | "utcmonth" => part(|d| d.month)?,
        "hours" | "utchours" => part(|d| d.hour)?,
        "minutes" | "utcminutes" => part(|d| d.minute)?,
        "seconds" | "utcseconds" => part(|d| d.second)?,
        "milliseconds" | "utcmilliseconds" => part(|d| d.milli)?,
        "time" => Val::Num(date_arg()?),
        "timezoneoffset" => Val::Num(if date_arg()?.is_nan() { f64::NAN } else { 0.0 }),
        "quarter" | "utcquarter" => Val::Num(1.0 + date::breakdown(date_arg()?).map_or(0, |d| d.month / 3) as f64),
        "week" | "utcweek" => Val::Num(date::week_vega(to_date_ms(a.get(0))?)),
        "dayofyear" | "utcdayofyear" => Val::Num(date::day_of_year_vega(to_date_ms(a.get(0))?)),
        // ---- sequence
        "length" => match a.get(0) {
            Val::Str(s) => Val::Num(utf16(s)?.len() as f64),
            Val::Array(items) => Val::Num(items.len() as f64),
            Val::Undefined | Val::Null => return throws(format!("Cannot read properties of {} (reading 'length')", a.get(0).to_js_string())),
            _ => Val::Undefined,
        },
        // ---- string
        "parseFloat" => Val::Num(parse_float(&a.string(0)?)),
        "parseInt" => Val::Num(parse_int(&a.string(0)?, n(1))),
        "upper" | "lower" => {
            let s = a.string(0)?;
            let upper = name == "upper";
            let (mut units, mut bytes) = (0usize, 0usize);
            for c in s.chars() {
                let (u, b) = if upper { (c.to_uppercase().map(char::len_utf16).sum::<usize>(), c.to_uppercase().map(char::len_utf8).sum::<usize>()) } else { (c.to_lowercase().map(char::len_utf16).sum::<usize>(), c.to_lowercase().map(char::len_utf8).sum::<usize>()) };
                units += u;
                bytes += b;
            }
            val::building(units as f64, bytes as f64)?;
            Val::string(if upper { s.to_uppercase() } else { s.to_lowercase() })?
        }
        "substring" => {
            let s = utf16(&a.string(0)?)?;
            let len = s.len() as f64;
            let clamp = |x: f64| integer(x).clamp(0.0, len);
            let (from, to) = (clamp(n(1)), if matches!(a.get(2), Val::Undefined) { len } else { clamp(n(2)) });
            let (lo, hi) = (from.min(to) as usize, from.max(to) as usize);
            Val::string(from16(&s[lo..hi])?)?
        }
        "split" => split(&a.string(0)?, a.get(1), a.get(2))?,
        "trim" => Val::string(val::trim_js(&a.string(0)?).to_string())?,
        "btoa" => btoa(&a.string(0)?)?,
        "atob" => Val::string(atob(&a.string(0)?)?)?,
        "encodeURIComponent" => encode_uri_component(&a.string(0)?)?,
        // ---- regexp
        "regexp" => match a.get(0) {
            Val::Regex(source, flags) if !a.has(1) || matches!(a.get(1), Val::Undefined) => Val::Regex(source.clone(), flags.clone()),
            other => {
                let (source, flags) = match other {
                    Val::Regex(source, _) => (source.clone(), if matches!(a.get(1), Val::Undefined) { String::new() } else { a.string(1)? }),
                    Val::Undefined => (String::new(), if matches!(a.get(1), Val::Undefined) { String::new() } else { a.string(1)? }),
                    v => (v.to_js_string(), if matches!(a.get(1), Val::Undefined) { String::new() } else { a.string(1)? }),
                };
                jsre::compile(&source, &flags).map_err(|e| e.into_eval(EvalError::Throws))?;
                Val::Regex(source, flags)
            }
        },
        "test" => {
            let re = regex_arg(a.get(0))?;
            Val::Bool(re.is_match(&re.prepare(&a.string(1)?)?)?)
        }
        // ---- type checks and conversions
        "isArray" => Val::Bool(matches!(a.get(0), Val::Array(_))),
        "isBoolean" => Val::Bool(matches!(a.get(0), Val::Bool(_))),
        "isDate" => Val::Bool(matches!(a.get(0), Val::Date(_))),
        "isDefined" => Val::Bool(!matches!(a.get(0), Val::Undefined)),
        "isNumber" => Val::Bool(matches!(a.get(0), Val::Num(_))),
        "isObject" => Val::Bool(a.get(0).is_object()),
        "isRegExp" => Val::Bool(matches!(a.get(0), Val::Regex(..))),
        "isString" => Val::Bool(matches!(a.get(0), Val::Str(_))),
        "isTuple" => Val::Bool(matches!(a.get(0), Val::Object(f) if f.iter().any(|(k, _)| k == "\u{0}vega_id"))),
        "isValid" => Val::Bool(!matches!(a.get(0), Val::Undefined | Val::Null) && !matches!(a.get(0), Val::Num(x) if x.is_nan())),
        "toBoolean" => match a.get(0) {
            Val::Undefined | Val::Null => Val::Null,
            Val::Str(s) if s.is_empty() => Val::Null,
            Val::Str(s) if s == "false" || s == "0" => Val::Bool(false),
            v => Val::Bool(v.truthy()),
        },
        "toDate" => match a.get(0) {
            Val::Undefined | Val::Null => Val::Null,
            Val::Str(s) if s.is_empty() => Val::Null,
            Val::Num(x) => Val::Num(*x),
            Val::Date(ms) => Val::Date(*ms),
            other => Val::Num(date::parse_date(&other.to_text()?)?),
        },
        "toNumber" => match a.get(0) {
            Val::Undefined | Val::Null => Val::Null,
            Val::Str(s) if s.is_empty() => Val::Null,
            v => Val::Num(v.to_number()),
        },
        "toString" => match a.get(0) {
            Val::Undefined | Val::Null => Val::Null,
            Val::Str(s) if s.is_empty() => Val::Null,
            v => Val::string(v.to_text()?)?,
        },
        // ---- sequences and objects
        "indexof" | "lastindexof" => {
            let last = name == "lastindexof";
            match a.get(0) {
                Val::Str(s) => {
                    let (h, nd) = (utf16(s)?, utf16(&a.string(1)?)?);
                    let found = if last {
                        let start = if a.has(2) && !n(2).is_nan() { integer(n(2)).clamp(0.0, h.len() as f64) as usize } else { h.len() };
                        (0..=start.min(h.len().saturating_sub(nd.len()))).rev().find(|i| h.len() >= nd.len() && h[*i..*i + nd.len()] == nd[..])
                    } else {
                        let from = integer(n(2)).clamp(0.0, h.len() as f64) as usize;
                        (from..=h.len()).find(|i| h.len() >= i + nd.len() && h[*i..*i + nd.len()] == nd[..])
                    };
                    Val::Num(found.map_or(-1.0, |i| i as f64))
                }
                Val::Array(items) => {
                    let found = if last {
                        let start = if a.has(2) { relative_last(n(2), items.len()) } else { items.len().checked_sub(1) };
                        start.and_then(|s| (0..=s).rev().find(|i| items[*i].strict_eq(a.get(1))))
                    } else {
                        let from = relative(n(2), items.len());
                        find_index(items, a.get(1), from)
                    };
                    Val::Num(found.map_or(-1.0, |i| i as f64))
                }
                other => return throws(format!("Cannot read properties of {} (reading '{}')", other.to_js_string(), if last { "lastIndexOf" } else { "indexOf" })),
            }
        }
        "join" => {
            let items = array_arg(a.get(0), "join")?;
            let sep = if matches!(a.get(1), Val::Undefined) { ",".to_string() } else { a.string(1)? };
            let mut out = val::Bounded::default();
            for (i, v) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(&sep)?;
                }
                if !matches!(v, Val::Undefined | Val::Null) {
                    out.push_str(&v.to_js_string())?;
                }
            }
            Val::Str(out.finish())
        }
        "slice" => match a.get(0) {
            Val::Str(s) => {
                let u = utf16(s)?;
                let from = relative(n(1), u.len());
                let to = if matches!(a.get(2), Val::Undefined) { u.len() } else { relative(n(2), u.len()) };
                Val::string(from16(&u[from..to.max(from)])?)?
            }
            Val::Array(items) => {
                let from = relative(n(1), items.len());
                let to = if matches!(a.get(2), Val::Undefined) { items.len() } else { relative(n(2), items.len()) };
                Val::Array(copied(&items[from..to.max(from)])?)
            }
            other => return throws(format!("Cannot read properties of {} (reading 'slice')", other.to_js_string())),
        },
        "replace" => {
            if let Val::Undefined = a.get(1) {
                return throws("Please pass a string or RegExp argument to replace.");
            }
            Val::Str(replace(&a.string(0)?, a.get(1), &a.string(2)?)?)
        }
        "reverse" => {
            let mut items = copied(array_arg(a.get(0), "reverse")?)?;
            items.reverse();
            Val::Array(items)
        }
        "sort" => {
            let mut items = copied(array_arg(a.get(0), "sort")?)?;
            let (defined, undefined): (Vec<Val>, Vec<Val>) = items.drain(..).partition(|v| !matches!(v, Val::Undefined));
            let mut sorted = v8_sort(defined, ascending)?;
            sorted.extend(undefined);
            Val::Array(sorted)
        }
        "peek" => last_of(a.get(0))?,
        "span" => {
            if !a.get(0).truthy() {
                return Ok(Val::Num(0.0));
            }
            let d = last_of(a.get(0))?.to_number() - a.get(0).property(&Val::Num(0.0))?.to_number();
            Val::Num(if d == 0.0 || d.is_nan() { 0.0 } else { d })
        }
        "lerp" => {
            let (lo, hi) = (a.get(0).property(&Val::Num(0.0))?, last_of(a.get(0))?);
            let f = n(1);
            if matches!(hi, Val::Undefined) || f == 0.0 || f.is_nan() {
                lo
            } else if f == 1.0 {
                hi
            } else {
                lo.add(&Val::Num(f * (hi.to_number() - lo.to_number())))?
            }
        }
        "inrange" => {
            let (mut r0, mut r1) = (a.get(1).property(&Val::Num(0.0))?, last_of(a.get(1))?);
            if r1.less_than(&r0) == Some(true) {
                std::mem::swap(&mut r0, &mut r1);
            }
            let left = !a.has(2) || matches!(a.get(2), Val::Undefined) || a.get(2).truthy();
            let right = !a.has(3) || matches!(a.get(3), Val::Undefined) || a.get(3).truthy();
            let value = a.get(0);
            let le = |x: &Val, y: &Val| y.less_than(x) == Some(false);
            let lt = |x: &Val, y: &Val| x.less_than(y) == Some(true);
            Val::Bool((if left { le(&r0, value) } else { lt(&r0, value) }) && (if right { le(value, &r1) } else { lt(value, &r1) }))
        }
        "clampRange" => {
            let (mut lo, mut hi) = (a.get(0).property(&Val::Num(0.0))?.to_number(), a.get(0).property(&Val::Num(1.0))?.to_number());
            if hi < lo {
                std::mem::swap(&mut lo, &mut hi);
            }
            let span = hi - lo;
            let (min, max) = (n(1), n(2));
            if span >= max - min {
                Val::Array(vec![a.get(1).clone(), a.get(2).clone()])
            } else {
                let lo = js_min(&[Val::Num(js_max(&[Val::Num(lo), Val::Num(min)])), Val::Num(max - span)]);
                Val::Array(vec![Val::Num(lo), Val::Num(lo + span)])
            }
        }
        "extent" => {
            let items = if a.get(0).truthy() { elements(a.get(0), false)? } else { Vec::new() };
            let valid = |v: &Val| !matches!(v, Val::Undefined | Val::Null) && !matches!(v, Val::Num(x) if x.is_nan());
            let start = items.iter().position(valid).unwrap_or(items.len());
            match items.get(start) {
                None => Val::Array(vec![Val::Undefined, Val::Undefined]),
                Some(first) => {
                    let (mut lo, mut hi) = (first.clone(), first.clone());
                    for v in items.iter().skip(start) {
                        if !matches!(v, Val::Undefined | Val::Null) {
                            if v.less_than(&lo) == Some(true) {
                                lo = v.clone();
                            }
                            if hi.less_than(v) == Some(true) {
                                hi = v.clone();
                            }
                        }
                    }
                    Val::Array(vec![lo, hi])
                }
            }
        }
        "flush" => {
            let threshold = a.get(2);
            if !threshold.truthy() && !matches!(threshold, Val::Num(x) if *x == 0.0) {
                return Ok(a.get(5).clone());
            }
            let t = threshold.to_number();
            let mut lo = a.get(0).property(&Val::Num(0.0))?.to_number();
            let last = last_of(a.get(0))?;
            if matches!(last, Val::Undefined) {
                return Ok(a.get(5).clone());
            }
            let mut hi = last.to_number();
            if hi < lo {
                std::mem::swap(&mut lo, &mut hi);
            }
            let (l, r) = ((n(1) - lo).abs(), (hi - n(1)).abs());
            if l < r && l <= t {
                a.get(3).clone()
            } else if r <= t {
                a.get(4).clone()
            } else {
                a.get(5).clone()
            }
        }
        "merge" => {
            let mut fields: Vec<(String, Val)> = Vec::new();
            let mut at: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
            let mut put = |k: String, v: Val| {
                if let Some(&slot) = at.get(&k) {
                    fields[slot].1 = v;
                } else {
                    at.insert(k.clone(), fields.len());
                    fields.push((k, v));
                }
            };
            let mut incoming = 0usize;
            for v in vals {
                incoming = incoming.saturating_add(match v {
                    Val::Object(more) => more.len(),
                    Val::Array(items) => items.len(),
                    Val::Str(text) => val::utf16_len(text),
                    _ => 0,
                });
                array_fits(incoming)?;
                // Each entry is a key string, a value, and the key again in the index.
                val::charge(match v {
                    Val::Object(more) => more.len().saturating_mul(MERGE_ENTRY_BYTES).saturating_add(more.iter().map(|(_, v)| v.weight()).sum()),
                    Val::Array(items) => items.len().saturating_mul(MERGE_ENTRY_BYTES).saturating_add(items.iter().map(Val::weight).sum()),
                    Val::Str(text) => val::utf16_len(text).saturating_mul(MERGE_ENTRY_BYTES + val::ELEMENT_BYTES),
                    _ => 0,
                })?;
                match v {
                    Val::Object(more) => more.iter().filter(|(k, _)| !k.starts_with('\u{0}')).for_each(|(k, v)| put(k.clone(), v.clone())),
                    Val::Array(items) => items.iter().enumerate().for_each(|(i, v)| put(i.to_string(), v.clone())),
                    Val::Str(text) => utf16(text)?.iter().enumerate().for_each(|(i, u)| put(i.to_string(), Val::Str(Text::unit(*u)))),
                    _ => {}
                }
            }
            Val::Object(fields)
        }
        "pluck" => {
            let keys = field_path(a.get(1))?.into_iter().map(Val::string).collect::<Res<Vec<_>>>()?;
            let get = |obj: &Val| -> Res<Val> {
                let Some((first, rest)) = keys.split_first() else { return Ok(obj.clone()) };
                let mut cur = obj.property(first)?;
                for key in rest {
                    cur = cur.property(key)?;
                }
                Ok(cur)
            };
            match a.get(0) {
                Val::Array(items) => {
                    // The result holds one value per item; each is as large as what it reads.
                    array_fits(items.len())?;
                    val::charge(items.len().saturating_mul(val::ELEMENT_BYTES))?;
                    let mut out = Vec::with_capacity(items.len());
                    for item in items {
                        let value = get(item)?;
                        val::charge(value.weight())?;
                        out.push(value);
                    }
                    Val::Array(out)
                }
                other => get(other)?,
            }
        }
        "pad" => pad(&a)?,
        "truncate" => truncate(&a)?,
        "sequence" => {
            let (start, stop, step) = match vals.len() {
                0 | 1 => (0.0, n(0), 1.0),
                2 => (n(0), n(1), 1.0),
                _ => (n(0), n(1), n(2)),
            };
            let len = val::to_int32(((stop - start) / step).ceil().max(0.0));
            if len < 0 {
                return throws("Invalid array length");
            }
            array_fits(len as usize)?;
            Val::Array((0..len as usize).map(|i| Val::Num(start + i as f64 * step)).collect())
        }
        "bandspace" => {
            let (count, inner, outer) = (or_zero(a.get(0)), or_zero(a.get(1)), or_zero(a.get(2)));
            let space = count - inner + outer * 2.0;
            Val::Num(if count != 0.0 { if space > 0.0 { space } else { 1.0 } } else { 0.0 })
        }
        "panLinear" | "panLog" | "panPow" | "panSymlog" | "zoomLinear" | "zoomLog" | "zoomPow" | "zoomSymlog" => pan_zoom(name, &a)?,
        // ---- formats
        "format" => {
            if matches!(a.get(0), Val::Null) {
                Val::Str("null".into())
            } else {
                let spec = a.string(1)?;
                if let Some(width) = d3rs::format::spec_width(&spec).map_err(EvalError::Throws)? {
                    // The padding, and the copies the numeral and grouping steps make of it.
                    val::building(width as f64, width as f64 * 8.0)?;
                }
                Val::string(crate::number_locale::d3_format(&spec, n(0)).map_err(EvalError::Throws)?)?
            }
        }
        "timeFormat" | "utcFormat" => {
            if matches!(a.get(0), Val::Null) {
                Val::Str("null".into())
            } else {
                let raw = match a.get(0) {
                    Val::Date(ms) => *ms,
                    other => other.to_number(),
                };
                let ms = date::time_clip(raw);
                let utc = name == "utcFormat";
                let locale = time_locale()?;
                Val::Str(match a.get(1) {
                    Val::Str(spec) => date::time_format(spec, ms, utc, &locale)?,
                    spec if !spec.truthy() => date::multi_format(raw, utc, &[], &locale)?,
                    Val::Object(fields) => {
                        let overrides: Vec<(String, String)> = fields.iter().filter(|(_, v)| v.truthy()).map(|(k, v)| (k.clone(), v.to_js_string())).collect();
                        date::multi_format(raw, utc, &overrides, &locale)?
                    }
                    Val::Array(_) | Val::Date(_) | Val::Regex(..) => date::multi_format(raw, utc, &[], &locale)?,
                    other => return throws(format!("Invalid time multi-format specifier: {}", other.to_js_string())),
                })
            }
        }
        "timeParse" | "utcParse" => {
            if matches!(a.get(0), Val::Null) {
                Val::Str("null".into())
            } else {
                date::time_parse(&a.string(1)?, &a.string(0)?, &*time_locale()?).map_or(Val::Null, Val::Date)
            }
        }
        "monthFormat" | "monthAbbrevFormat" | "dayFormat" | "dayAbbrevFormat" => {
            let x = a.get(0);
            let int = |v: &Val| matches!(v, Val::Num(x) if x.fract() == 0.0 && x.is_finite());
            if !int(x) {
                Val::Str("".into())
            } else {
                let k = x.to_number();
                let (month, day, spec) = match name {
                    "monthFormat" => (k, 1.0, "%B"),
                    "monthAbbrevFormat" => (k, 1.0, "%b"),
                    "dayFormat" => (0.0, 2.0 + k, "%A"),
                    _ => (0.0, 2.0 + k, "%a"),
                };
                Val::Str(date::time_format(spec, date::make_time(2000.0, month, day, 0.0, 0.0, 0.0, 0.0), false, &*time_locale()?)?)
            }
        }
        "timeUnitSpecifier" => time_unit_specifier(&a)?,
        "timeOffset" | "utcOffset" => match Unit::parse(&a.string(0)?) {
            Some(unit) => Val::Date(unit.offset(n(1), if matches!(a.get(2), Val::Undefined | Val::Null) { None } else { Some(n(2)) })),
            None => Val::Undefined,
        },
        "timeSequence" | "utcSequence" => match Unit::parse(&a.string(0)?) {
            Some(unit) => {
                let step = if matches!(a.get(3), Val::Undefined | Val::Null) { None } else { Some(n(3)) };
                let dates = unit.range(n(1), n(2), step, MAX_SEQUENCE).ok_or_else(sequence_too_long)?;
                val::reserve(dates.len().saturating_mul(val::ELEMENT_BYTES * 2))?;
                Val::Array(dates.into_iter().map(Val::Date).collect())
            }
            None => Val::Undefined,
        },
        // ---- statistics
        "densityNormal" => {
            let stdev = or_one(a.get(2));
            let z = (n(0) - or_zero(a.get(1))) / stdev;
            Val::Num(libm::exp(-0.5 * z * z) / (stdev * sqrt2pi()))
        }
        "cumulativeNormal" => Val::Num(cumulative_normal(n(0), or_zero(a.get(1)), or_one(a.get(2)))),
        "quantileNormal" => Val::Num(quantile_normal(n(0), or_zero(a.get(1)), or_one(a.get(2)))),
        "densityLogNormal" => {
            if n(0) <= 0.0 {
                Val::Num(0.0)
            } else {
                let stdev = or_one(a.get(2));
                let z = (libm::log(n(0)) - or_zero(a.get(1))) / stdev;
                Val::Num(libm::exp(-0.5 * z * z) / (stdev * sqrt2pi() * n(0)))
            }
        }
        "cumulativeLogNormal" => Val::Num(cumulative_normal(libm::log(n(0)), or_zero(a.get(1)), or_one(a.get(2)))),
        "quantileLogNormal" => Val::Num(libm::exp(quantile_normal(n(0), or_zero(a.get(1)), or_one(a.get(2))))),
        "densityUniform" | "cumulativeUniform" | "quantileUniform" => {
            let (lo, hi) = uniform_bounds(a.get(1), a.get(2));
            let (lo, hi, x) = (lo.to_number(), hi.to_number(), n(0));
            Val::Num(match name {
                "densityUniform" => if x >= lo && x <= hi { 1.0 / (hi - lo) } else { 0.0 },
                "cumulativeUniform" => if x < lo { 0.0 } else if x > hi { 1.0 } else { (x - lo) / (hi - lo) },
                _ => if x >= 0.0 && x <= 1.0 { lo + x * (hi - lo) } else { f64::NAN },
            })
        }
        "sampleNormal" => {
            let (mean, stdev) = (or_zero(a.get(0)), or_one(a.get(1)));
            let (mut x, mut y, mut rds);
            loop {
                x = random() * 2.0 - 1.0;
                y = random() * 2.0 - 1.0;
                rds = x * x + y * y;
                if !(rds == 0.0 || rds > 1.0) {
                    break;
                }
            }
            Val::Num(mean + x * (-2.0 * rds.ln() / rds).sqrt() * stdev)
        }
        "sampleLogNormal" => {
            let (mean, stdev) = (or_zero(a.get(0)), or_one(a.get(1)));
            let z = {
                let (mut x, mut rds);
                loop {
                    x = random() * 2.0 - 1.0;
                    let y = random() * 2.0 - 1.0;
                    rds = x * x + y * y;
                    if !(rds == 0.0 || rds > 1.0) {
                        break;
                    }
                }
                x * (-2.0 * rds.ln() / rds).sqrt()
            };
            Val::Num((mean + z * stdev).exp())
        }
        "sampleUniform" => {
            let (lo, hi) = uniform_bounds(a.get(0), a.get(1));
            Val::Num(lo.to_number() + (hi.to_number() - lo.to_number()) * random())
        }
        // ---- logging returns its last argument
        "debug" | "info" | "warn" => vals.last().cloned().unwrap_or(Val::Undefined),
        // ---- environment
        "screen" => Val::Object(Vec::new()),
        "windowSize" | "containerSize" => Val::Array(vec![Val::Undefined, Val::Undefined]),
        "pinchDistance" | "pinchAngle" => {
            let touches = a.get(0).property(&Val::Str("touches".into()))?;
            let point = |i: usize, key: &'static str| -> Res<f64> {
                Ok(touches.property(&Val::Num(i as f64))?.property(&Val::Str(key.into()))?.to_number())
            };
            let (dx, dy) = (point(0, "clientX")? - point(1, "clientX")?, point(0, "clientY")? - point(1, "clientY")?);
            Val::Num(if name == "pinchDistance" { dx.hypot(dy) } else { dy.atan2(dx) })
        }
        "inScope" => match a.get(0) {
            Val::Object(fields) if fields.iter().any(|(k, _)| k == "mark") => {
                // Only the label's own item is reachable: its mark belongs to the axis group the encoder runs in.
                Val::Bool(a.get(0) == &super::item_object(datum))
            }
            v if !v.truthy() => Val::Bool(false),
            other => return throws(format!("Cannot read properties of undefined (reading 'group') of {}", other.to_js_string())),
        },
        // ---- events are undefined in a label encoder
        "view" | "group" | "item" | "xy" | "x" | "y" => return throws("event is not defined"),
        // ---- colors
        "rgb" | "hsl" | "lab" | "hcl" => {
            use colors::{Color, Space};
            let space = match name {
                "rgb" => Space::Rgb,
                "hsl" => Space::Hsl,
                "lab" => Space::Lab,
                _ => Space::Hcl,
            };
            if vals.len() == 1 {
                let c = color_of(a.get(0));
                Val::Color(match space {
                    Space::Rgb => c.to_rgb(),
                    Space::Hsl => c.to_hsl(),
                    Space::Lab => c.to_lab(),
                    Space::Hcl => c.to_hcl(),
                })
            } else {
                let opacity = if matches!(a.get(3), Val::Undefined | Val::Null) { 1.0 } else { n(3) };
                Val::Color(Color { space, c: [n(0), n(1), n(2)], opacity })
            }
        }
        "luminance" => Val::Num(colors::luminance(color_of(a.get(0)))),
        "contrast" => Val::Num(colors::contrast(color_of(a.get(0)), color_of(a.get(1)))),
        other => match super::bound::call(other, vals) {
            Some(result) => result?,
            None => unreachable!("{other} is listed as evaluated but has no implementation"),
        },
    })
}

fn relative_last(n: f64, len: usize) -> Option<usize> {
    let n = integer(n);
    let len = len as f64;
    let k = if n >= 0.0 { n.min(len - 1.0) } else { len + n };
    if k < 0.0 { None } else { Some(k as usize) }
}

/// vega-util `field(name)`'s path: `splitAccessPath` run over `name` as the generated code runs it, `n` read from
/// `name.length` before `name` is turned into a string.
pub(super) fn field_path(name: &Val) -> Res<Vec<String>> {
    let n = match name {
        Val::Str(s) => utf16(s)?.len(),
        Val::Array(items) => items.len(),
        Val::Undefined | Val::Null => return throws(format!("Cannot read properties of {} (reading 'length')", name.to_js_string())),
        _ => 0,
    };
    // The characters as a vector (four bytes each), the text again, and the pieces of path being cut from it.
    val::charge(name.to_text()?.len().saturating_mul(4 * 4))?;
    let p: Vec<char> = name.to_js_string().chars().collect();
    let sub = |from: usize, to: usize| p[from.min(p.len())..to.min(p.len())].iter().collect::<String>();
    let whole: String = p.iter().collect();
    let mut path = Vec::new();
    let mut q: Option<char> = None;
    let mut b: i64 = 0;
    let mut s = String::new();
    let (mut i, mut j) = (0usize, 0usize);
    while j < n {
        let c = p.get(j).copied();
        if c == Some('\\') {
            s.push_str(&sub(i, j));
            j += 1;
            i = j;
        } else if c.is_some() && c == q {
            path.push(format!("{s}{}", sub(i, j)));
            s.clear();
            i = j + 1;
            q = None;
            b = -1;
        } else if q.is_some() {
        } else if i as i64 == b && matches!(c, Some('"' | '\'')) {
            i = j + 1;
            q = c;
        } else if c == Some('.') && b == 0 {
            if j > i {
                path.push(format!("{s}{}", sub(i, j)));
                s.clear();
                i = j + 1;
            } else {
                i = j + 1;
            }
        } else if c == Some('[') {
            if j > i {
                path.push(format!("{s}{}", sub(i, j)));
                s.clear();
            }
            i = j + 1;
            b = i as i64;
        } else if c == Some(']') {
            if b == 0 {
                return throws(format!("Access path missing open bracket: {whole}"));
            }
            if b > 0 {
                path.push(format!("{s}{}", sub(i, j)));
                s.clear();
            }
            b = 0;
            i = j + 1;
        }
        j += 1;
    }
    if b != 0 {
        return throws(format!("Access path missing closing bracket: {whole}"));
    }
    if q.is_some() {
        return throws(format!("Access path missing closing quote: {whole}"));
    }
    if j > i {
        path.push(format!("{s}{}", sub(i, j + 1)));
    }
    Ok(path)
}
