//! Renders a code's compiled-in template the way Python's `str.format` does.
//! Supported: `{name}`, `{name!r}`, `{name:.Nf}`, `{name:.N%}`, `{{` and `}}`;
//! the generator refuses any other field in the data.

use std::collections::BTreeMap;
use std::fmt;

use serde_json::Value;

use crate::{py_repr, Level};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MessageError {
    UnknownCode(&'static str),
    MissingParam(String),
    BadTemplate(String),
    BadSpec { spec: String, value: String },
}

impl fmt::Display for MessageError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            MessageError::UnknownCode(code) => write!(f, "unregistered code {code}"),
            MessageError::MissingParam(name) => write!(f, "template param {name:?} not given"),
            MessageError::BadTemplate(template) => write!(f, "malformed template {template:?}"),
            MessageError::BadSpec { spec, value } => {
                write!(f, "format spec {spec:?} does not apply to {value}")
            }
        }
    }
}

impl std::error::Error for MessageError {}

/// A list of strings reads as one comma-joined string in an error's message
/// (an empty one as "none configured"); a warning prints the value as is.
fn shown(level: Level, value: &Value) -> Value {
    match value {
        Value::Array(items) if level == Level::Error => {
            let strings: Option<Vec<&str>> = items.iter().map(Value::as_str).collect();
            match strings {
                Some(s) if s.is_empty() => Value::String("none configured".into()),
                Some(s) => Value::String(s.join(", ")),
                None => value.clone(),
            }
        }
        _ => value.clone(),
    }
}

fn repr(value: &Value) -> String {
    match value {
        Value::Array(items) => {
            format!(
                "[{}]",
                items.iter().map(repr).collect::<Vec<_>>().join(", ")
            )
        }
        Value::Object(map) => {
            let pairs: Vec<String> = map
                .iter()
                .map(|(k, v)| format!("{}: {}", py_repr(&Value::String(k.clone())), repr(v)))
                .collect();
            format!("{{{}}}", pairs.join(", "))
        }
        other => py_repr(other),
    }
}

fn plain(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => repr(other),
    }
}

fn spec(value: &Value, spec: &str) -> Result<String, MessageError> {
    let bad = || MessageError::BadSpec {
        spec: spec.into(),
        value: value.to_string(),
    };
    let (digits, kind) = spec.split_at(spec.len().saturating_sub(1));
    let places: usize = digits
        .strip_prefix('.')
        .and_then(|d| d.parse().ok())
        .ok_or_else(bad)?;
    let x = value.as_f64().ok_or_else(bad)?;
    match kind {
        "f" => Ok(format!("{x:.places$}")),
        "%" => Ok(format!("{:.places$}%", x * 100.0)),
        _ => Err(bad()),
    }
}

pub(crate) fn render(
    level: Level,
    template: &str,
    params: &BTreeMap<&'static str, Value>,
) -> Result<String, MessageError> {
    let mut out = String::new();
    let mut chars = template.chars().peekable();
    while let Some(c) = chars.next() {
        match (c, chars.peek()) {
            ('{', Some('{')) | ('}', Some('}')) => {
                chars.next();
                out.push(c);
            }
            ('}', _) => return Err(MessageError::BadTemplate(template.into())),
            ('{', _) => {
                let field: String = chars.by_ref().take_while(|&c| c != '}').collect();
                let (head, format) = match field.split_once(':') {
                    Some((head, format)) => (head, Some(format)),
                    None => (field.as_str(), None),
                };
                let (name, conv) = match head.split_once('!') {
                    Some((name, conv)) => (name, Some(conv)),
                    None => (head, None),
                };
                let value = params
                    .get(name)
                    .ok_or_else(|| MessageError::MissingParam(name.into()))?;
                let value = shown(level, value);
                out.push_str(&match (conv, format) {
                    (None, None) => plain(&value),
                    (Some("r"), None) => repr(&value),
                    (None, Some(format)) => spec(&value, format)?,
                    _ => return Err(MessageError::BadTemplate(template.into())),
                });
            }
            _ => out.push(c),
        }
    }
    Ok(out)
}
