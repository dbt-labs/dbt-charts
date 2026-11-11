//! Structured diagnostics: a registered code plus the params its message is
//! rendered from. The registry is data: `codes/<domain>.yaml` (code, level,
//! title, template, fix, doc, docs topic, optional hint id, which `Code::hint`
//! carries) is the only place a code is authored, and
//! `chart_svg_dev/gen_diagnostic_codes.py` generates from it `src/codes.rs`
//! (every domain's `Code`s), `src/diag.rs` (a typed constructor per code,
//! `diag::err_unknown_chart(chart, available)`, so a placeholder mismatch does
//! not compile), the Python `codes_<domain>.py` (hint callables stay in
//! `hints.py`, named by hint id), `compile/src/codes.rs` and
//! `tests/it/messages.json` (the text Python renders per code, which
//! `Diagnostic::message` must reproduce). `tests/test_diagnostic_codes.py`
//! fails on drift.
//!
//! `Diagnostic { code, params, range, path, related, hint }`, the warning sink
//! and `refuse!` live here. `hint` is built in `Diagnostic::new` by
//! `hints.rs`; `message.rs` fills the compiled-in template.
//!
//! Every crate that raises or carries a diagnostic depends on this one. The
//! `test-support` feature adds the text helpers unit tests compare a refusal
//! with.

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::fmt;

#[rustfmt::skip]
pub mod codes;
#[rustfmt::skip]
pub mod diag;
pub mod hints;
mod message;
mod restated;
use d3rs::py_float::float_repr;
pub use message::MessageError;
pub use restated::restated_text;

use serde::Serialize;
use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    Error,
    Warning,
}

impl Level {
    /// The wire spelling, as Python's `Diagnostic.level`.
    pub fn as_str(self) -> &'static str {
        match self {
            Level::Error => "error",
            Level::Warning => "warning",
        }
    }
}

/// A registered diagnostic code: its level, title, message template and the
/// template params (sorted) it formats; instances come only from the generated
/// `codes.rs`. `fix` is a template for a warning and verbatim text for an error.
/// `hint` names the Python generator that writes the diagnostic's hint from its fields;
/// Rust cannot run it, so a surface that shows hints refuses a code that has one.
pub struct Code {
    pub id: &'static str,
    pub level: Level,
    pub title: &'static str,
    pub params: &'static [&'static str],
    pub template: &'static str,
    pub fix: Option<&'static str>,
    pub hint: Option<&'static str>,
}

/// A byte range into the source text of the file a diagnostic is about.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct SourceRange {
    pub start: usize,
    pub end: usize,
    /// The range stands for whole lines: a mark that names no columns even where it sits on one
    /// line (a container collapsed to its key line).
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub lines_only: bool,
}

impl SourceRange {
    pub fn new(start: usize, end: usize) -> Self {
        SourceRange {
            start,
            end,
            lines_only: false,
        }
    }
}

/// Another place a finding names: a range, or a model path the host resolves to one.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Related {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub range: Option<SourceRange>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<ModelPath>,
    pub message: String,
}

/// A hint's text behind a thin pointer: a `Diagnostic` is the `Err` of most results in the spine,
/// and clippy's `result_large_err` measures it against 128 bytes.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(transparent)]
struct Hint(String);

/// A location in the board model, one segment per key or index.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ModelPath(pub Vec<String>);

impl fmt::Display for ModelPath {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0.join("."))
    }
}

/// The level comes from the code; `range` and `path` say where, and `related`
/// carries the other spans the finding names.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Diagnostic {
    pub code: &'static str,
    pub params: BTreeMap<&'static str, Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub range: Option<SourceRange>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<ModelPath>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub related: Vec<Related>,
    /// What to try, for the codes whose Python `hint_generator` is ported ([`hints::PORTED`]) or
    /// a producer that set one; `None` is no hint. Boxed to keep a `Diagnostic` under clippy's
    /// `result_large_err` size, which every `Result<_, Diagnostic>` is measured by; read it with
    /// [`Diagnostic::hint`].
    #[serde(skip_serializing_if = "Option::is_none")]
    hint: Option<Box<Hint>>,
}

impl Diagnostic {
    /// Panics when `params` names differ from the code's template params:
    /// a mismatch is a programming error that Python would turn into a
    /// `KeyError` while formatting.
    pub fn new(code: &Code, params: impl IntoIterator<Item = (&'static str, Value)>) -> Self {
        let params: BTreeMap<_, _> = params.into_iter().collect();
        assert!(
            params.keys().copied().eq(code.params.iter().copied()),
            "{}: params {:?} differ from its template params {:?}",
            code.id,
            params.keys().collect::<Vec<_>>(),
            code.params,
        );
        let hint = hints::generate(code.id, &params).map(|h| Box::new(Hint(h)));
        Diagnostic {
            code: code.id,
            params,
            range: None,
            path: None,
            related: Vec::new(),
            hint,
        }
    }

    /// A hint a producer sets at its raise site; it wins over the code's generator.
    pub fn with_hint(mut self, hint: impl Into<String>) -> Self {
        self.hint = Some(Box::new(Hint(hint.into())));
        self
    }

    /// The hint, if there is one.
    pub fn hint(&self) -> Option<&str> {
        self.hint.as_deref().map(|h| h.0.as_str())
    }

    pub fn at(mut self, range: SourceRange) -> Self {
        self.range = Some(range);
        self
    }

    pub fn at_path(mut self, path: ModelPath) -> Self {
        self.path = Some(path);
        self
    }

    pub fn relating(mut self, range: SourceRange, label: impl Into<String>) -> Self {
        self.related.push(Related {
            range: Some(range),
            path: None,
            message: label.into(),
        });
        self
    }

    /// Names another place by its model path; the stamping pass resolves it to a range.
    pub fn relating_path(mut self, path: ModelPath, label: impl Into<String>) -> Self {
        self.related.push(Related {
            range: None,
            path: Some(path),
            message: label.into(),
        });
        self
    }

    /// The registered code; `None` only for a diagnostic not built by `new`.
    pub fn registered(&self) -> Option<&'static Code> {
        codes::lookup(self.code)
    }

    /// The message rendered from the code's compiled-in template.
    pub fn message(&self) -> Result<String, MessageError> {
        let code = self
            .registered()
            .ok_or(MessageError::UnknownCode(self.code))?;
        message::render(code.level, code.template, &self.params)
    }

    /// Adds a param the Python sink reads but the code's templates never
    /// print (e.g. a flag that tells a recorded fact from a user warning).
    pub fn with_sink_param(mut self, name: &'static str, value: Value) -> Self {
        self.params.insert(name, value);
        if self.hint.is_none() {
            self.hint = hints::generate(self.code, &self.params).map(|h| Box::new(Hint(h)));
        }
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

/// Declines a shape the renderer does not draw. The text names the shape and
/// ends in the ledger form, "... is not supported"; `refusal_inventory` ledgers
/// every call, and any other error text must be allowlisted there as a payload
/// or mirrored-production error.
#[macro_export]
macro_rules! refuse {
    ($($arg:tt)*) => {{
        let message = format!($($arg)*);
        debug_assert!(
            message.contains("not supported"),
            "refusal outside the ledger form: {message}"
        );
        message
    }};
}

/// Unit tests compare a refusal by its text.
#[cfg(any(test, feature = "test-support"))]
impl Diagnostic {
    pub fn contains(&self, needle: &str) -> bool {
        self.to_string().contains(needle)
    }

    pub fn starts_with(&self, prefix: &str) -> bool {
        self.to_string().starts_with(prefix)
    }
}

#[cfg(any(test, feature = "test-support"))]
impl PartialEq<String> for Diagnostic {
    fn eq(&self, other: &String) -> bool {
        format!("{self}") == *other
    }
}

#[cfg(any(test, feature = "test-support"))]
impl PartialEq<&str> for Diagnostic {
    fn eq(&self, other: &&str) -> bool {
        format!("{self}") == *other
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
        Value::String(s) => py_str_repr(s),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Null => "None".into(),
        Value::Number(n) => match n.as_f64() {
            Some(x) if n.is_f64() => float_repr(x),
            _ => n.to_string(),
        },
        other => other.to_string(),
    }
}

/// `repr(str)`. A character `str.isprintable()` rejects prints escaped; the
/// control, space-separator and format characters are covered, not every
/// unassigned or private-use code point (`mdsvg::css::python_repr` has the
/// full table).
pub fn py_str_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            c if c == quote || c == '\\' => {
                out.push('\\');
                out.push(c);
            }
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            c if !is_printable(c) => match c as u32 {
                n if n < 0x100 => out.push_str(&format!("\\x{n:02x}")),
                n if n < 0x10000 => out.push_str(&format!("\\u{n:04x}")),
                n => out.push_str(&format!("\\U{n:08x}")),
            },
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

fn is_printable(c: char) -> bool {
    !(c.is_control()
        || (c.is_whitespace() && c != ' ')
        || matches!(c as u32, 0xad | 0x200b..=0x200f | 0x202a..=0x202e | 0x2060..=0x2064 | 0xfeff))
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
    let warnings = SINK.with(|sink| sink.replace(outer)).unwrap_or_default();
    (result, warnings)
}
