//! PyYAML's implicit resolvers (`Resolver`) and constructors (`SafeConstructor`)
//! for a plain scalar.

use std::sync::OnceLock;

use regex::Regex;
use serde_json::Number;

use crate::node::Value;

/// A plain scalar the engine does not carry, and why.
pub enum Refusal {
    /// Production raises (`=` value tag, an integer with no digits, an invalid date).
    Invalid(&'static str),
    /// Production keeps a value JSON cannot (a number beyond 64 bits, inf, nan).
    Uncarried(&'static str),
    /// The merge key `<<`.
    Merge,
    /// Regex tables failed to build.
    Internal,
}

#[derive(Clone, Copy, PartialEq)]
enum Tag {
    Bool,
    Float,
    Int,
    Merge,
    Null,
    Timestamp,
    Value,
}

/// `Resolver.add_implicit_resolver(tag, regexp, first)` calls, in registration order, which is the order they are tried. Python's `$`
/// also matches before a final newline, which a plain scalar never ends with,
/// so `\z` is the same test.
const RESOLVERS: [(Tag, &str, &str); 7] = [
    (
        Tag::Bool,
        "yYnNtTfFoO",
        r"yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF",
    ),
    (
        Tag::Float,
        "-+0123456789.",
        r"[-+]?[0-9][0-9_]*\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)",
    ),
    (
        Tag::Int,
        "-+0123456789",
        r"[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+",
    ),
    (Tag::Merge, "<", r"<<"),
    (Tag::Null, "~nN", r"~|null|Null|NULL"),
    (
        Tag::Timestamp,
        "0123456789",
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{4}-[0-9][0-9]?-[0-9][0-9]?(?:[Tt]|[ \t]+)[0-9][0-9]?:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9]{2})?))?",
    ),
    (Tag::Value, "=", r"="),
];

// `SafeConstructor.timestamp_regexp`
const TIMESTAMP: &str = r"\A(?P<year>[0-9]{4})-(?P<month>[0-9][0-9]?)-(?P<day>[0-9][0-9]?)(?:(?:[Tt]|[ \t]+)(?P<hour>[0-9][0-9]?):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})(?:\.(?P<fraction>[0-9]*))?(?:[ \t]*(?P<tz>Z|(?P<tz_sign>[-+])(?P<tz_hour>[0-9][0-9]?)(?::(?P<tz_minute>[0-9]{2}))?))?)?\z";

struct Tables {
    resolvers: Vec<(Tag, &'static str, Regex)>,
    timestamp: Regex,
}

fn tables() -> Result<&'static Tables, Refusal> {
    static COMPILED: OnceLock<Option<Tables>> = OnceLock::new();
    COMPILED
        .get_or_init(|| {
            let mut resolvers = Vec::new();
            for (tag, first, pattern) in RESOLVERS {
                let re = Regex::new(&format!(r"\A(?:{pattern})\z")).ok()?;
                resolvers.push((tag, first, re));
            }
            Some(Tables {
                resolvers,
                timestamp: Regex::new(TIMESTAMP).ok()?,
            })
        })
        .as_ref()
        .ok_or(Refusal::Internal)
}

/// `BaseResolver.resolve` for a plain scalar: the
/// first resolver whose first-character list holds `value[0]` and whose
/// pattern matches; the empty scalar is null.
fn tag_of(value: &str, t: &Tables) -> Option<Tag> {
    let Some(first) = value.chars().next() else {
        return Some(Tag::Null);
    };
    t.resolvers
        .iter()
        .find(|(_, firsts, re)| firsts.contains(first) && re.is_match(value))
        .map(|(tag, _, _)| *tag)
}

/// What a plain scalar's text is under YAML 1.1.
pub fn plain(value: &str) -> Result<Value, Refusal> {
    let t = tables()?;
    match tag_of(value, t) {
        None => Ok(Value::Str(value.to_string())),
        Some(Tag::Null) => Ok(Value::Null),
        Some(Tag::Merge) => Err(Refusal::Merge),
        // `SafeConstructor` has no constructor for the value tag and raises.
        Some(Tag::Value) => Err(Refusal::Invalid(
            "could not determine a constructor for the tag 'tag:yaml.org,2002:value'",
        )),
        Some(Tag::Bool) => Ok(Value::Bool(matches!(
            value.to_lowercase().as_str(),
            "yes" | "true" | "on"
        ))),
        Some(Tag::Int) => int(value),
        Some(Tag::Float) => float(value),
        Some(Tag::Timestamp) => timestamp(value, t).map(Value::Timestamp),
    }
}

/// `SafeConstructor.construct_yaml_int`.
fn int(value: &str) -> Result<Value, Refusal> {
    let digits: String = value.chars().filter(|c| *c != '_').collect();
    let (sign, unsigned) = match digits.strip_prefix('-') {
        Some(rest) => (-1_i128, rest),
        None => (1, digits.strip_prefix('+').unwrap_or(&digits)),
    };
    let magnitude = if unsigned == "0" {
        Ok(Some(0))
    } else if let Some(rest) = unsigned.strip_prefix("0b") {
        radix(rest, 2)
    } else if let Some(rest) = unsigned.strip_prefix("0x") {
        radix(rest, 16)
    } else if unsigned.starts_with('0') {
        radix(unsigned, 8)
    } else if unsigned.contains(':') {
        sexagesimal_int(unsigned)
    } else {
        radix(unsigned, 10)
    };
    let beyond = Refusal::Uncarried("an integer beyond 64 bits");
    match magnitude {
        // Python's `int('', base)` raises.
        Err(_) => Err(Refusal::Invalid("an integer with no digits")),
        Ok(None) => Err(beyond),
        Ok(Some(magnitude)) => {
            let signed = sign * magnitude;
            if let Ok(n) = i64::try_from(signed) {
                Ok(Value::Number(Number::from(n)))
            } else if let Ok(n) = u64::try_from(signed) {
                Ok(Value::Number(Number::from(n)))
            } else {
                Err(beyond)
            }
        }
    }
}

/// `int(digits, base)`: `Ok(None)` past 128 bits, `Err` when Python raises.
fn radix(digits: &str, base: u32) -> Result<Option<i128>, std::num::ParseIntError> {
    use std::num::IntErrorKind;
    match i128::from_str_radix(digits, base) {
        Ok(n) => Ok(Some(n)),
        Err(e) if matches!(e.kind(), IntErrorKind::PosOverflow) => Ok(None),
        Err(e) => Err(e),
    }
}

/// The sexagesimal branch of `construct_yaml_int`: base 60, least significant last.
fn sexagesimal_int(digits: &str) -> Result<Option<i128>, std::num::ParseIntError> {
    let mut total: i128 = 0;
    let mut base: i128 = 1;
    for part in digits.rsplit(':') {
        let Some(digit) = radix(part, 10)? else {
            return Ok(None);
        };
        let Some(next) = digit
            .checked_mul(base)
            .and_then(|term| total.checked_add(term))
        else {
            return Ok(None);
        };
        total = next;
        let Some(next_base) = base.checked_mul(60) else {
            return Ok(None);
        };
        base = next_base;
    }
    Ok(Some(total))
}

/// `SafeConstructor.construct_yaml_float`.
fn float(value: &str) -> Result<Value, Refusal> {
    let lowered = value.replace('_', "").to_lowercase();
    let (sign, unsigned) = match lowered.strip_prefix('-') {
        Some(rest) => (-1.0, rest),
        None => (1.0, lowered.strip_prefix('+').unwrap_or(&lowered)),
    };
    let nonfinite = Refusal::Uncarried("a non-finite number");
    let bad = || Refusal::Internal;
    let magnitude = if unsigned == ".inf" || unsigned == ".nan" {
        return Err(nonfinite);
    } else if unsigned.contains(':') {
        let mut total = 0.0;
        let mut base = 1.0;
        for part in unsigned.rsplit(':') {
            total += part.parse::<f64>().map_err(|_| bad())? * base;
            base *= 60.0;
        }
        total
    } else {
        unsigned.parse::<f64>().map_err(|_| bad())?
    };
    Number::from_f64(sign * magnitude)
        .map(Value::Number)
        .ok_or(nonfinite)
}

/// `SafeConstructor.construct_yaml_timestamp`, as the ISO string a board dumps
/// (pydantic's JSON mode: microseconds only when nonzero, `Z` for a zero offset).
fn timestamp(value: &str, t: &Tables) -> Result<String, Refusal> {
    let invalid = || Refusal::Invalid("a YAML timestamp that is not a valid date or time");
    let c = t.timestamp.captures(value).ok_or_else(invalid)?;
    let num = |name: &str| c.name(name).and_then(|m| m.as_str().parse::<u32>().ok());
    let (Some(year), Some(month), Some(day)) = (num("year"), num("month"), num("day")) else {
        return Err(invalid());
    };
    let days = i32::try_from(year)
        .map(|y| d3rs::calendar::days_in_month(y, month))
        .map_err(|_| invalid())?;
    if year == 0 || day == 0 || day > days {
        return Err(invalid());
    }
    let date = format!("{year:04}-{month:02}-{day:02}");
    let Some(hour) = num("hour") else {
        return Ok(date);
    };
    let (Some(minute), Some(second)) = (num("minute"), num("second")) else {
        return Err(invalid());
    };
    if hour > 23 || minute > 59 || second > 59 {
        return Err(invalid());
    }
    let mut iso = format!("{date}T{hour:02}:{minute:02}:{second:02}");
    let fraction = c.name("fraction").map_or("", |m| m.as_str());
    let micros: u32 = format!(
        "{:0<6}",
        fraction.get(..fraction.len().min(6)).unwrap_or("")
    )
    .parse()
    .map_err(|_| invalid())?;
    if micros != 0 {
        iso.push_str(&format!(".{micros:06}"));
    }
    if let Some(sign) = c.name("tz_sign") {
        let minutes = num("tz_hour").ok_or_else(invalid)? * 60 + num("tz_minute").unwrap_or(0);
        // `datetime.timezone` takes an offset strictly inside a day.
        if minutes >= 24 * 60 {
            return Err(invalid());
        }
        if minutes == 0 {
            iso.push('Z');
        } else {
            iso.push_str(&format!(
                "{}{:02}:{:02}",
                sign.as_str(),
                minutes / 60,
                minutes % 60
            ));
        }
    } else if c.name("tz").is_some() {
        iso.push('Z');
    }
    Ok(iso)
}
