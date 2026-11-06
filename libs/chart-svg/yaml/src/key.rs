//! A map key as the text JSON gives it, and as Python's dict compares it.

use crate::node::{KeyKind, Value};

/// The key's kind and JSON text (`json.dumps` on a non-string key), or `None`
/// for a timestamp, which JSON cannot key.
pub fn json_key(value: &Value) -> Option<(KeyKind, String)> {
    Some(match value {
        Value::Str(s) => (KeyKind::Str, s.clone()),
        Value::Null => (KeyKind::Null, "null".into()),
        Value::Bool(b) => (KeyKind::Bool, b.to_string()),
        Value::Number(n) => match n.as_f64().filter(|_| n.is_f64()) {
            Some(f) => (KeyKind::Float, float_repr(f)),
            None => (KeyKind::Int, n.to_string()),
        },
        Value::Timestamp(_) => return None,
    })
}

/// Python's `repr(key)`, as a duplicate-key message prints it.
pub fn py_repr(kind: KeyKind, text: &str) -> String {
    match kind {
        KeyKind::Str => diagnostic::py_str_repr(text),
        KeyKind::Null => "None".into(),
        KeyKind::Bool if text == "true" => "True".into(),
        KeyKind::Bool => "False".into(),
        KeyKind::Int | KeyKind::Float => text.into(),
    }
}

/// Keys that are equal as Python dict keys share an identity: `1`, `1.0` and
/// `True` are one key.
pub fn identity(kind: KeyKind, text: &str) -> String {
    match kind {
        KeyKind::Str => format!("s{text}"),
        KeyKind::Null => "n".into(),
        KeyKind::Bool => format!("#{}", u8::from(text == "true")),
        KeyKind::Int => format!("#{text}"),
        KeyKind::Float => match text.parse::<f64>() {
            Ok(f) if f.fract() == 0.0 && f.abs() < 2f64.powi(64) => format!("#{}", f as i128),
            _ => format!("#{text}"),
        },
    }
}

/// `repr(float)`: the shortest round-trip digits, fixed notation for decimal
/// exponents in -4..16, otherwise `d.ddde+XX`.
fn float_repr(f: f64) -> String {
    let sci = format!("{f:e}");
    let (mantissa, exp) = sci.split_once('e').unwrap_or((&sci, "0"));
    let exp: i32 = exp.parse().unwrap_or(0);
    let (sign, mantissa) = match mantissa.strip_prefix('-') {
        Some(m) => ("-", m),
        None => ("", mantissa),
    };
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let n = digits.len() as i32;
    let body = if (-4..16).contains(&exp) {
        let point = exp + 1;
        if point <= 0 {
            format!("0.{}{digits}", "0".repeat(point.unsigned_abs() as usize))
        } else if point >= n {
            format!("{digits}{}.0", "0".repeat((point - n) as usize))
        } else {
            let (int, frac) = digits.split_at(point as usize);
            format!("{int}.{frac}")
        }
    } else {
        let (head, tail) = digits.split_at(1);
        let frac = if tail.is_empty() {
            String::new()
        } else {
            format!(".{tail}")
        };
        format!(
            "{head}{frac}e{}{:02}",
            if exp < 0 { '-' } else { '+' },
            exp.abs()
        )
    };
    format!("{sign}{body}")
}
