//! Structured diagnostics: a registered code plus the params Python formats
//! its message from. Rust carries no message text for a registered code.

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::fmt;

use crate::codes;
use d3rs::py_float::float_repr;

use serde::Serialize;
use serde_json::Value;

/// A registered diagnostic code and the template params it formats;
/// instances come only from the generated `codes.rs`.
pub struct Code {
    pub id: &'static str,
    pub params: &'static [&'static str],
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Diagnostic {
    pub code: &'static str,
    pub params: BTreeMap<&'static str, Value>,
}

impl Diagnostic {
    /// Panics when `params` names differ from the code's template params:
    /// a mismatch is a programming error that Python would turn into a
    /// `KeyError` while formatting.
    pub fn new<const N: usize>(code: &Code, params: [(&'static str, Value); N]) -> Self {
        let params: BTreeMap<_, _> = params.into_iter().collect();
        assert!(
            params.keys().copied().eq(code.params.iter().copied()),
            "{}: params {:?} differ from its template params {:?}",
            code.id,
            params.keys().collect::<Vec<_>>(),
            code.params,
        );
        Diagnostic { code: code.id, params }
    }

    /// Adds a param the Python sink reads but the code's templates never
    /// print (e.g. a flag that tells a recorded fact from a user warning).
    pub fn with_sink_param(mut self, name: &'static str, value: Value) -> Self {
        self.params.insert(name, value);
        self
    }

    /// A refusal gains `context` ahead of its message; a registered code is
    /// the finding itself and passes through unchanged.
    pub fn in_context(self, context: &str) -> Self {
        if self.code == codes::ERR_ENGINE_REFUSED.id {
            Diagnostic::refused(format!("{context}: {self}"))
        } else {
            self
        }
    }

    pub fn refused(message: impl Into<String>) -> Self {
        Diagnostic::new(
            &codes::ERR_ENGINE_REFUSED,
            [("message", Value::String(message.into()))],
        )
    }
}

/// Unit tests compare a refusal by its text.
#[cfg(test)]
impl Diagnostic {
    pub fn contains(&self, needle: &str) -> bool {
        self.to_string().contains(needle)
    }

    pub fn starts_with(&self, prefix: &str) -> bool {
        self.to_string().starts_with(prefix)
    }
}

#[cfg(test)]
impl PartialEq<String> for Diagnostic {
    fn eq(&self, other: &String) -> bool {
        self.to_string() == *other
    }
}

#[cfg(test)]
impl PartialEq<&str> for Diagnostic {
    fn eq(&self, other: &&str) -> bool {
        self.to_string() == *other
    }
}

impl fmt::Display for Diagnostic {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.code == codes::ERR_ENGINE_REFUSED.id {
            if let Some(Value::String(message)) = self.params.get("message") {
                return f.write_str(message);
            }
        }
        write!(f, "{}", self.code)?;
        for (i, (name, value)) in self.params.iter().enumerate() {
            write!(f, "{} {name}={value}", if i == 0 { ":" } else { "," })?;
        }
        Ok(())
    }
}

impl From<String> for Diagnostic {
    fn from(message: String) -> Self {
        Diagnostic::refused(message)
    }
}

impl From<&str> for Diagnostic {
    fn from(message: &str) -> Self {
        Diagnostic::refused(message)
    }
}

/// `repr()` of a JSON cell as Python prints the value it came from.
pub fn py_repr(v: &Value) -> String {
    match v {
        Value::String(s) if s.contains('\'') && !s.contains('"') => format!("\"{s}\""),
        Value::String(s) => format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'")),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Null => "None".into(),
        Value::Number(n) if n.is_f64() => float_repr(n.as_f64().expect("an f64 number")),
        other => other.to_string(),
    }
}

/// `str()` of a JSON cell: the string itself, else its `repr()`.
pub fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => py_repr(other),
    }
}

/// A rendered SVG, the warnings it was drawn with, and the height it takes
/// when given no slot height (`None` for a family whose height core does not
/// read from the render).
#[derive(Debug)]
pub struct RenderOutput {
    pub svg: String,
    pub warnings: Vec<Diagnostic>,
    pub natural_height: Option<f64>,
}

thread_local! {
    static SINK: RefCell<Option<Vec<Diagnostic>>> = const { RefCell::new(None) };
}

/// Record a warning into the innermost open `collect`; a no-op when none is
/// open, like Python's contextvar sinks. A warning with the same code and
/// params as one already recorded is the same finding, not a second one.
pub fn warn(diagnostic: Diagnostic) {
    SINK.with(|sink| {
        if let Some(open) = sink.borrow_mut().as_mut() {
            if !open.contains(&diagnostic) {
                open.push(diagnostic);
            }
        }
    });
}

/// Run `render`, returning its result and the warnings it recorded. Nested
/// calls each collect their own.
pub fn collect<T>(render: impl FnOnce() -> T) -> (T, Vec<Diagnostic>) {
    let outer = SINK.with(|sink| sink.replace(Some(Vec::new())));
    let result = render();
    let warnings = SINK.with(|sink| sink.replace(outer)).expect("sink opened above");
    (result, warnings)
}
