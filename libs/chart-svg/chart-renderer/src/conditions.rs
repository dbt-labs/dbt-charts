//! Boolean conditions authored in YAML — a layout item's `visible` and a
//! variable's `enabled` (`render/conditions.py::eval_bool_condition`).
//!
//! A string condition is a Jinja expression, auto-wrapped in `{{ }}` when it
//! carries none (`conditions.py:75-79`), rendered against the request's
//! variable values with `StrictUndefined` (`jinja.py:65`), and the rendered
//! text coerced by `coerce_bool` (`conditions.py:25-39`). This models the
//! expression shapes the corpus and the docs author — a variable, a string
//! literal, `==`, and `and` — with Python's value semantics, and refuses every
//! other operator, literal, and template shape by name rather than
//! approximating a Jinja evaluator. A `{query, column}` probe needs an
//! executor this composer does not have and refuses too.

use serde_json::{Map, Value};

fn unsupported(subject: &str, shape: &str) -> String {
    format!("{subject} condition {shape} not supported")
}

/// `eval_bool_condition`. `subject` names the surface in every message
/// ("board item visible", "board variable enabled"); `if_none` is the meaning
/// of a missing condition (`True` for both surfaces today).
pub fn eval_bool_condition(
    expr: &Value,
    if_none: bool,
    current_values: &Map<String, Value>,
    subject: &str,
) -> Result<bool, String> {
    match expr {
        Value::Null => Ok(if_none),
        Value::Bool(b) => Ok(*b),
        Value::String(text) => {
            let value = eval_expression(template_expression(text, subject)?, current_values, subject)?;
            coerce_bool(&python_str(&value), subject)
        }
        Value::Object(_) => Err(unsupported(subject, "query probe")),
        Value::Number(_) => Err(unsupported(subject, "of type number")),
        Value::Array(_) => Err(unsupported(subject, "of type list")),
    }
}

/// `coerce_bool` on the rendered text (`conditions.py:33-39`).
fn coerce_bool(rendered: &str, subject: &str) -> Result<bool, String> {
    match rendered.trim().to_lowercase().as_str() {
        "true" | "1" | "yes" => Ok(true),
        "false" | "0" | "no" => Ok(false),
        _ => Err(format!(
            "{subject} condition cannot coerce {rendered:?} to bool; expected true/false/1/0/yes/no"
        )),
    }
}

/// Python's `str(value)` for what `{{ }}` prints. A float always carries a
/// `.`, an exponent, or `inf`/`nan`, and a list or mapping prints its brackets,
/// so none of them can spell a `coerce_bool` word whatever the spelling here.
fn python_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(b) => if *b { "True" } else { "False" }.to_string(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// The expression inside the one `{{ }}` a condition may carry.
/// `conditions.py:76-77` wraps a string without `{{` itself; a string with
/// `{{` is the whole template, of which only a single braced expression
/// (whitespace around it is dropped by `coerce_bool`'s `strip`) is modeled.
fn template_expression<'a>(text: &'a str, subject: &str) -> Result<&'a str, String> {
    if text.contains("{%") {
        return Err(unsupported(subject, "{% %} block"));
    }
    if !text.contains("{{") {
        return Ok(text);
    }
    let inner = text
        .trim()
        .strip_prefix("{{")
        .and_then(|rest| rest.strip_suffix("}}"))
        .filter(|inner| !inner.contains("{{") && !inner.contains("}}"));
    inner.ok_or_else(|| unsupported(subject, "template text outside a single {{ }} expression"))
}

enum Tok {
    Name(String),
    Str(String),
    Sym(String),
}

fn tokenize(source: &str, subject: &str) -> Result<Vec<Tok>, String> {
    let chars: Vec<char> = source.chars().collect();
    let mut tokens = Vec::new();
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
            tokens.push(Tok::Name(chars[start..i].iter().collect()));
        } else if c.is_ascii_digit() {
            let start = i;
            while i < chars.len() && (chars[i].is_ascii_alphanumeric() || chars[i] == '.') {
                i += 1;
            }
            let number: String = chars[start..i].iter().collect();
            return Err(unsupported(subject, &format!("number literal '{number}'")));
        } else if c == '\'' || c == '"' {
            let end = chars[i + 1..]
                .iter()
                .position(|&q| q == c)
                .ok_or_else(|| unsupported(subject, "unterminated string"))?;
            let literal: String = chars[i + 1..i + 1 + end].iter().collect();
            if literal.contains('\\') {
                return Err(unsupported(subject, "string escape"));
            }
            tokens.push(Tok::Str(literal));
            i += end + 2;
        } else {
            let pair: String = chars[i..(i + 2).min(chars.len())].iter().collect();
            let symbol = if ["==", "!=", "<=", ">=", "//", "**"].contains(&pair.as_str()) {
                pair
            } else {
                c.to_string()
            };
            i += symbol.chars().count();
            tokens.push(Tok::Sym(symbol));
        }
    }
    Ok(tokens)
}

enum Expr {
    Var(String),
    Str(String),
    Eq(Box<Expr>, Box<Expr>),
    /// Python `and`: the first falsy operand, else the last.
    And(Vec<Expr>),
}

/// Names `resolve_jinja_template` binds in the render scope after the request's
/// variables, so a variable of that name is shadowed (`self` is the template
/// reference Jinja itself binds). Each prints as an object, never a value.
const RENDER_SCOPE_NAMES: [&str; 3] = ["filter", "filter_date_range", "self"];

/// Jinja's default globals: bound only when no variable carries the name.
const JINJA_GLOBALS: [&str; 6] = ["cycler", "dict", "joiner", "lipsum", "namespace", "range"];

/// Jinja words that are operators, literals, or names the render scope owns.
fn refused_word(word: &str, subject: &str) -> Option<String> {
    match word {
        "or" | "not" | "in" | "is" | "if" | "else" => {
            Some(unsupported(subject, &format!("operator '{word}'")))
        }
        "true" | "false" | "none" | "True" | "False" | "None" => {
            Some(unsupported(subject, &format!("literal '{word}'")))
        }
        _ if RENDER_SCOPE_NAMES.contains(&word) => {
            Some(unsupported(subject, &format!("reserved name '{word}'")))
        }
        _ => None,
    }
}

fn parse_atom(tokens: &[Tok], pos: &mut usize, subject: &str) -> Result<Expr, String> {
    let token = tokens.get(*pos).ok_or_else(|| unsupported(subject, "malformed expression"))?;
    *pos += 1;
    match token {
        Tok::Str(s) => Ok(Expr::Str(s.clone())),
        Tok::Name(name) => match refused_word(name, subject) {
            Some(refusal) => Err(refusal),
            None if name == "and" => Err(unsupported(subject, "malformed expression")),
            None => Ok(Expr::Var(name.clone())),
        },
        Tok::Sym(symbol) => Err(unsupported(subject, &format!("operator '{symbol}'"))),
    }
}

fn parse_comparison(tokens: &[Tok], pos: &mut usize, subject: &str) -> Result<Expr, String> {
    let left = parse_atom(tokens, pos, subject)?;
    if matches!(tokens.get(*pos), Some(Tok::Sym(s)) if s == "==") {
        *pos += 1;
        let right = parse_atom(tokens, pos, subject)?;
        return Ok(Expr::Eq(Box::new(left), Box::new(right)));
    }
    Ok(left)
}

fn parse(source: &str, subject: &str) -> Result<Expr, String> {
    let tokens = tokenize(source, subject)?;
    if tokens.is_empty() {
        return Err(unsupported(subject, "empty expression"));
    }
    let mut pos = 0;
    let mut operands = vec![parse_comparison(&tokens, &mut pos, subject)?];
    while matches!(tokens.get(pos), Some(Tok::Name(n)) if n == "and") {
        pos += 1;
        operands.push(parse_comparison(&tokens, &mut pos, subject)?);
    }
    match tokens.get(pos) {
        None => Ok(Expr::And(operands)),
        Some(Tok::Sym(s)) if s == "==" => Err(unsupported(subject, "chained comparison")),
        Some(Tok::Sym(s)) => Err(unsupported(subject, &format!("operator '{s}'"))),
        Some(Tok::Name(n)) => Err(refused_word(n, subject)
            .unwrap_or_else(|| unsupported(subject, "malformed expression"))),
        Some(Tok::Str(_)) => Err(unsupported(subject, "malformed expression")),
    }
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// A JSON number or bool as Python compares it: an exact integer or a float.
enum Num {
    Int(i128),
    Float(f64),
}

fn as_number(value: &Value) -> Option<Num> {
    match value {
        Value::Bool(b) => Some(Num::Int(i128::from(*b))),
        Value::Number(n) => match (n.as_i64(), n.as_u64()) {
            (Some(i), _) => Some(Num::Int(i128::from(i))),
            (None, Some(u)) => Some(Num::Int(i128::from(u))),
            (None, None) => n.as_f64().map(Num::Float),
        },
        _ => None,
    }
}

/// An integer equals a float only when the float is that exact integer; a
/// JSON integer is at most 64 bits, so no float beyond 2^100 can match.
fn number_eq(left: Num, right: Num) -> bool {
    match (left, right) {
        (Num::Int(a), Num::Int(b)) => a == b,
        (Num::Float(a), Num::Float(b)) => a == b,
        (Num::Int(i), Num::Float(f)) | (Num::Float(f), Num::Int(i)) => {
            f.fract() == 0.0 && f.abs() < 2f64.powi(100) && f as i128 == i
        }
    }
}

/// Python `==` between two condition operands: `True == 1`, `1 == 1.0`
/// exactly at any magnitude, and a numeric string never equals a number.
fn python_eq(left: &Value, right: &Value, subject: &str) -> Result<bool, String> {
    match (left, right) {
        (Value::Array(_) | Value::Object(_), _) | (_, Value::Array(_) | Value::Object(_)) => {
            Err(unsupported(subject, "comparison of a list or mapping"))
        }
        (Value::Null, Value::Null) => Ok(true),
        (Value::String(a), Value::String(b)) => Ok(a == b),
        _ => Ok(match (as_number(left), as_number(right)) {
            (Some(a), Some(b)) => number_eq(a, b),
            _ => false,
        }),
    }
}

fn evaluate(expr: &Expr, values: &Map<String, Value>, subject: &str) -> Result<Value, String> {
    match expr {
        Expr::Var(name) => match values.get(name) {
            Some(value) => Ok(value.clone()),
            None if JINJA_GLOBALS.contains(&name.as_str()) => {
                Err(unsupported(subject, &format!("Jinja global '{name}'")))
            }
            None => Err(format!("{subject} condition variable '{name}' is undefined")),
        },
        Expr::Str(s) => Ok(Value::String(s.clone())),
        Expr::Eq(left, right) => {
            let (left, right) = (evaluate(left, values, subject)?, evaluate(right, values, subject)?);
            python_eq(&left, &right, subject).map(Value::Bool)
        }
        Expr::And(operands) => {
            let (last, leading) = operands.split_last().expect("a parsed `and` has an operand");
            for operand in leading {
                let value = evaluate(operand, values, subject)?;
                if !truthy(&value) {
                    return Ok(value);
                }
            }
            evaluate(last, values, subject)
        }
    }
}

fn eval_expression(source: &str, values: &Map<String, Value>, subject: &str) -> Result<Value, String> {
    evaluate(&parse(source, subject)?, values, subject)
}
