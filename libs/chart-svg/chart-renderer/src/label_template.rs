//! Pie/donut slice-label templates, evaluated per visible slice the way
//! production's label Jinja environment evaluates them.
//!
//! Production renders `labels.template` at resolve time
//! (`compile/resolve/chart/label_data.py::prepare_label_data`) and again from
//! the same rows at render; the resolved payload carries only the template
//! string and which slices survived the `where`/min-share gate
//! (`slice_label_indices`), so the engine evaluates the text itself.
//!
//! This is not a Jinja engine. It evaluates exactly the expression shapes the
//! example corpus authors and refuses every other with
//! `pie: slice-label template shape not supported: ...`:
//!
//! - `{{ name }}`
//! - `{{ '<lit>{:<spec>}<lit>'.format(name) }}` (Python `str.format`)
//! - `{{ name | format('<d3 spec>') }}` and
//!   `{{ (name / <number>) | format('<d3 spec>') }}`
//!
//! Scope (`label_data.py::prepare_pie_label_data`): the row's columns, then
//! `percent` (share of the total), `value` (the theta cell), `total` and
//! `color` (the identity cell) overriding any same-named column.
//! `label_jinja_env` is `StrictUndefined`, so an unknown name errs, and its
//! `finalize` prints a NULL as empty text. A template's rendered text splits
//! on `\n` into lines.

use d3rs::py_float::float_repr;
use serde_json::{Map, Value};

use crate::families::table::NULL_DISPLAY;

/// The value a template name resolves to.
#[derive(Clone, Debug, PartialEq)]
enum Val {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
}

impl Val {
    fn from_json(name: &str, v: &Value) -> Result<Val, String> {
        Ok(match v {
            Value::Null => Val::Null,
            Value::Bool(b) => Val::Bool(*b),
            Value::Number(n) if n.is_u64() && n.as_i64().is_none() => {
                return Err(format!("pie: slice-label template value '{name}' exceeds the integer range"))
            }
            Value::Number(n) => match n.as_i64() {
                Some(i) => Val::Int(i),
                None => Val::Float(n.as_f64().expect("a JSON number is an f64")),
            },
            Value::String(s) => Val::Str(s.clone()),
            Value::Array(_) | Value::Object(_) => {
                return Err(format!("pie: slice-label template value '{name}' is not a scalar"))
            }
        })
    }

    fn as_f64(&self, name: &str) -> Result<f64, String> {
        match self {
            Val::Int(i) => Ok(*i as f64),
            Val::Float(f) if f.is_finite() => Ok(*f),
            other => Err(format!("pie: slice-label template '{name}' is not a finite number: {other:?}")),
        }
    }

    /// Jinja's `str(value)` after `finalize` (`None` prints as empty text).
    fn py_str(&self) -> String {
        match self {
            Val::Null => String::new(),
            Val::Bool(true) => "True".into(),
            Val::Bool(false) => "False".into(),
            Val::Int(i) => i.to_string(),
            Val::Float(f) => float_repr(*f),
            Val::Str(s) => s.clone(),
        }
    }
}

/// One slice's template scope.
pub struct Scope<'a> {
    pub row: &'a Map<String, Value>,
    pub theta_field: &'a str,
    pub color_field: Option<&'a str>,
    pub percent: f64,
    pub total: f64,
}

impl Scope<'_> {
    fn get(&self, name: &str) -> Result<Val, String> {
        let cell = |field: &str| self.row.get(field).map_or(Ok(Val::Null), |v| Val::from_json(field, v));
        match name {
            "percent" => Ok(Val::Float(self.percent)),
            "total" => Ok(Val::Float(self.total)),
            // `_coerce_numeric` would turn a numeric string into its number, but
            // the wheel itself reads only JSON numbers as slice sizes.
            "value" => match cell(self.theta_field)? {
                Val::Str(s) => Err(format!("pie: slice-label template value is the string {s:?}, not a number")),
                v => Ok(v),
            },
            "color" => self.color_field.map_or(Ok(Val::Null), cell),
            "index" | "is_first" | "is_last" => {
                Err(format!("pie: slice-label template variable not modeled: {name}"))
            }
            column => match self.row.get(column) {
                Some(v) => Val::from_json(column, v),
                None => Err(format!("pie: slice-label template variable '{name}' is undefined")),
            },
        }
    }
}

/// A Python format spec `[,][.N](f|%)`, or `,` alone.
#[derive(Debug, PartialEq)]
struct PySpec {
    comma: bool,
    precision: Option<usize>,
    kind: Option<char>,
}

#[derive(Debug, PartialEq)]
enum Operand {
    Var(String),
    Div(String, f64),
}

#[derive(Debug, PartialEq)]
enum Expr {
    Var(String),
    /// `'<prefix>{:<spec>}<suffix>'.format(arg)`
    PyFormat { prefix: String, spec: PySpec, suffix: String, arg: String },
    /// `<operand> | format('<d3 spec>')`
    D3Filter { operand: Operand, spec: String },
}

#[derive(Debug, PartialEq)]
enum Part {
    Text(String),
    Expr(Expr),
}

/// A parsed label template.
#[derive(Debug)]
pub struct Template(Vec<Part>);

fn unsupported(what: &str) -> String {
    format!("pie: slice-label template shape not supported: {what}")
}

#[derive(Debug, PartialEq)]
enum Tok {
    Ident(String),
    Str(String),
    Num(f64),
    Punct(char),
}

fn tokenize(src: &str) -> Result<Vec<Tok>, String> {
    let chars: Vec<char> = src.chars().collect();
    let mut toks = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c.is_whitespace() {
            i += 1;
        } else if c.is_ascii_alphabetic() || c == '_' {
            let start = i;
            while i < chars.len() && (chars[i].is_ascii_alphanumeric() || chars[i] == '_') {
                i += 1;
            }
            toks.push(Tok::Ident(chars[start..i].iter().collect()));
        } else if c.is_ascii_digit() {
            let start = i;
            while i < chars.len() && (chars[i].is_ascii_digit() || chars[i] == '.') {
                i += 1;
            }
            let text: String = chars[start..i].iter().collect();
            toks.push(Tok::Num(text.parse().map_err(|_| unsupported(src))?));
        } else if c == '\'' || c == '"' {
            let close = chars[i + 1..].iter().position(|&q| q == c).ok_or_else(|| unsupported(src))?;
            let body: String = chars[i + 1..i + 1 + close].iter().collect();
            if body.contains('\\') {
                return Err(unsupported(src));
            }
            toks.push(Tok::Str(body));
            i += close + 2;
        } else if "()|./".contains(c) {
            toks.push(Tok::Punct(c));
            i += 1;
        } else {
            return Err(unsupported(src));
        }
    }
    Ok(toks)
}

fn parse_py_spec(spec: &str, src: &str) -> Result<PySpec, String> {
    let mut rest = spec;
    let comma = rest.starts_with(',');
    if comma {
        rest = &rest[1..];
    }
    let mut precision = None;
    if let Some(after_dot) = rest.strip_prefix('.') {
        let digits = after_dot.chars().take_while(char::is_ascii_digit).count();
        precision = Some(after_dot[..digits].parse().map_err(|_| unsupported(src))?);
        rest = &after_dot[digits..];
    }
    let kind = match rest {
        "" => None,
        "f" => Some('f'),
        "%" => Some('%'),
        _ => return Err(unsupported(src)),
    };
    // Without a type Python's `.N` means general format, and an empty spec is
    // `str()`: neither is a corpus shape.
    if kind.is_none() && (precision.is_some() || !comma) {
        return Err(unsupported(src));
    }
    Ok(PySpec { comma, precision, kind })
}

/// Split `'<prefix>{:<spec>}<suffix>'` around its one replacement field.
fn parse_py_format(literal: &str, src: &str) -> Result<(String, PySpec, String), String> {
    let (open, close) = literal.find("{:").zip(literal.find('}')).ok_or_else(|| unsupported(src))?;
    let (prefix, suffix) = (&literal[..open], &literal[close + 1..]);
    if open > close || [prefix, suffix, &literal[open + 2..close]].iter().any(|s| s.contains(['{', '}'])) {
        return Err(unsupported(src));
    }
    Ok((prefix.to_string(), parse_py_spec(&literal[open + 2..close], src)?, suffix.to_string()))
}

fn parse_expr(src: &str) -> Result<Expr, String> {
    let toks = tokenize(src)?;
    let filter_spec = |tail: &[Tok]| -> Result<String, String> {
        match tail {
            [Tok::Punct('|'), Tok::Ident(f), Tok::Punct('('), Tok::Str(spec), Tok::Punct(')')]
                if f == "format" && !spec.is_empty() =>
            {
                Ok(spec.clone())
            }
            _ => Err(unsupported(src)),
        }
    };
    match toks.as_slice() {
        [Tok::Ident(name)] => Ok(Expr::Var(name.clone())),
        [Tok::Str(lit), Tok::Punct('.'), Tok::Ident(f), Tok::Punct('('), Tok::Ident(arg), Tok::Punct(')')]
            if f == "format" =>
        {
            let (prefix, spec, suffix) = parse_py_format(lit, src)?;
            Ok(Expr::PyFormat { prefix, spec, suffix, arg: arg.clone() })
        }
        [Tok::Ident(name), tail @ ..] => {
            Ok(Expr::D3Filter { operand: Operand::Var(name.clone()), spec: filter_spec(tail)? })
        }
        [Tok::Punct('('), Tok::Ident(name), Tok::Punct('/'), Tok::Num(divisor), Tok::Punct(')'), tail @ ..]
            if *divisor != 0.0 =>
        {
            Ok(Expr::D3Filter { operand: Operand::Div(name.clone(), *divisor), spec: filter_spec(tail)? })
        }
        _ => Err(unsupported(src)),
    }
}

impl Template {
    /// Parse `source`, dropping the one trailing newline Jinja's default
    /// `keep_trailing_newline=False` drops (a YAML `|` block scalar ends in
    /// one).
    pub fn parse(source: &str) -> Result<Template, String> {
        let source = source.strip_suffix('\n').unwrap_or(source);
        let mut parts = Vec::new();
        let mut rest = source;
        while let Some(at) = rest
            .match_indices('{')
            .map(|(i, _)| i)
            .find(|&i| matches!(rest.as_bytes().get(i + 1), Some(b'{' | b'%' | b'#')))
        {
            if at > 0 {
                parts.push(Part::Text(rest[..at].to_string()));
            }
            let after = &rest[at + 2..];
            if !rest[at..].starts_with("{{") {
                return Err(unsupported(&format!("{} ... tag in {source:?}", &rest[at..at + 2])));
            }
            let end = after.find("}}").ok_or_else(|| unsupported(&format!("unterminated '{{{{' in {source:?}")))?;
            let inner = &after[..end];
            parts.push(Part::Expr(parse_expr(inner)?));
            rest = &after[end + 2..];
        }
        if !rest.is_empty() {
            parts.push(Part::Text(rest.to_string()));
        }
        Ok(Template(parts))
    }

    /// The template's rendered lines for one slice.
    pub fn render(&self, scope: &Scope) -> Result<Vec<String>, String> {
        let mut text = String::new();
        for part in &self.0 {
            match part {
                Part::Text(t) => text.push_str(t),
                Part::Expr(e) => text.push_str(&e.eval(scope)?),
            }
        }
        Ok(text.split('\n').map(str::to_string).collect())
    }
}

impl Expr {
    fn eval(&self, scope: &Scope) -> Result<String, String> {
        match self {
            Expr::Var(name) => Ok(scope.get(name)?.py_str()),
            Expr::PyFormat { prefix, spec, suffix, arg } => {
                Ok(format!("{prefix}{}{suffix}", py_format(spec, &scope.get(arg)?, arg)?))
            }
            Expr::D3Filter { operand, spec } => {
                let (name, divisor) = match operand {
                    Operand::Var(name) => (name, None),
                    Operand::Div(name, divisor) => (name, Some(*divisor)),
                };
                let val = scope.get(name)?;
                // `format_d3`: a NULL prints the house dash; a divided NULL is
                // Python's TypeError.
                if val == Val::Null && divisor.is_none() {
                    return Ok(NULL_DISPLAY.to_string());
                }
                let x = val.as_f64(name)?;
                let x = divisor.map_or(x, |d| x / d);
                let context = |e: String| format!("pie: slice-label template format '{spec}': {e}");
                if let Some((number, unit)) = d3rs::native::predefined_native(spec, x).map_err(context)? {
                    return Ok(format!("{number}{unit}"));
                }
                crate::number_locale::d3_format(spec, x).map_err(context)
            }
        }
    }
}

/// Python's `format(value, spec)` for the `PySpec` subset. Rust's `{:.N}` is
/// the same exact-value, ties-to-even rounding as Python's `f`; `%` scales by
/// 100 in floating point first, as CPython does.
fn py_format(spec: &PySpec, val: &Val, name: &str) -> Result<String, String> {
    let (negative, digits) = match (spec.kind, val) {
        (None, Val::Int(i)) => (*i < 0, i.unsigned_abs().to_string()),
        (None, Val::Float(f)) => (f.is_sign_negative(), float_repr(f.abs())),
        (None, other) => {
            return Err(format!("pie: slice-label template '{name}' is not a number: {other:?}"))
        }
        (Some(kind), val) => {
            let x = val.as_f64(name)?;
            let scaled = if kind == '%' { x.abs() * 100.0 } else { x.abs() };
            let mut s = format!("{:.*}", spec.precision.unwrap_or(6), scaled);
            if kind == '%' {
                s.push('%');
            }
            (x.is_sign_negative(), s)
        }
    };
    let (int_part, tail) = match digits.find(|c: char| !c.is_ascii_digit()) {
        Some(at) => digits.split_at(at),
        None => (digits.as_str(), ""),
    };
    let int_part = if spec.comma { d3rs::format::group_digits(int_part) } else { int_part.to_string() };
    Ok(format!("{}{int_part}{tail}", if negative { "-" } else { "" }))
}
