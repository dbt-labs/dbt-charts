//! A variable's resolved value, as render holds it (`plan`'s `RunResult.variables`).
//!
//! The shapes are what Python's `normalize_render_values` returns, and they follow the value's
//! source, not the variable's `input:`: only a multiselect and a daterange are normalized, so a
//! number set from a URL is still [`Atom::Text`], a `default: 3` is [`Atom::Int`], and an unquoted
//! `default: 2024-01-01` is [`Atom::Date`] while the quoted one is text. An unset multiselect is
//! an empty list, never [`VariableValue::Unset`]; a daterange is `Unset` or two ends, each a date
//! or text (a preset id is already its ISO ends, a blank end is `""`).
//!
//! Pure data. How a value prints in board text (the multiselect join, the daterange refusal) is
//! the text renderer's, so nothing here formats. The wire form is a `{"kind": .., "value": ..}`
//! object, spelled by [`VariableValue::to_json`] and read by [`VariableValue::from_json`].
//! `tests/fixtures/variable_value_cases.json` holds Python's answer per source.

use serde_json::{json, Map, Value};

/// One value: what a multiselect member, a daterange end or a scalar variable holds.
#[derive(Debug, Clone, PartialEq)]
pub enum Atom {
    Text(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    /// A `datetime.date`, as ISO `YYYY-MM-DD`.
    Date(String),
}

/// A variable's value at render.
#[derive(Debug, Clone, PartialEq)]
pub enum VariableValue {
    /// No value: Python's `None`.
    Unset,
    One(Atom),
    /// A member Python holds as `None` (a URL `[null]`) is `None`.
    Multiselect(Vec<Option<Atom>>),
    /// `None` is an end the value does not hold.
    Daterange {
        start: Option<Atom>,
        end: Option<Atom>,
    },
}

fn is_iso_date(text: &str) -> bool {
    let mut parts = text.split('-');
    let mut part = |len: usize| {
        parts
            .next()
            .filter(|p| p.len() == len && p.bytes().all(|b| b.is_ascii_digit()))
            .and_then(|p| p.parse::<u32>().ok())
    };
    let (Some(y), Some(m), Some(d)) = (part(4), part(2), part(2)) else {
        return false;
    };
    let leap = y % 4 == 0 && (y % 100 != 0 || y % 400 == 0);
    let days = match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if leap => 29,
        2 => 28,
        _ => return false,
    };
    parts.next().is_none() && y >= 1 && (1..=days).contains(&d)
}

fn field<'a>(wire: &'a Value, name: &str) -> Result<&'a Value, String> {
    wire.get(name)
        .ok_or_else(|| format!("missing `{name}` in {wire}"))
}

impl Atom {
    pub fn from_json(wire: &Value) -> Result<Self, String> {
        let kind = field(wire, "kind")?
            .as_str()
            .ok_or_else(|| format!("`kind` is not text in {wire}"))?;
        let value = field(wire, "value")?;
        let bad = || format!("a {kind} cannot hold {value}");
        Ok(match kind {
            "text" => Self::Text(value.as_str().ok_or_else(bad)?.to_string()),
            "int" => Self::Int(value.as_i64().ok_or_else(bad)?),
            "float" => Self::Float(value.as_f64().filter(|_| value.is_f64()).ok_or_else(bad)?),
            "bool" => Self::Bool(value.as_bool().ok_or_else(bad)?),
            "date" => Self::Date(
                value
                    .as_str()
                    .filter(|s| is_iso_date(s))
                    .ok_or_else(bad)?
                    .to_string(),
            ),
            other => return Err(format!("`{other}` is not a value kind")),
        })
    }

    pub fn to_json(&self) -> Value {
        match self {
            Self::Text(v) => json!({"kind": "text", "value": v}),
            Self::Int(v) => json!({"kind": "int", "value": v}),
            Self::Float(v) => json!({"kind": "float", "value": v}),
            Self::Bool(v) => json!({"kind": "bool", "value": v}),
            Self::Date(v) => json!({"kind": "date", "value": v}),
        }
    }
}

/// An `Atom`, or `None` where Python holds `None` (a daterange end or a multiselect member).
fn slot_from_json(wire: &Value) -> Result<Option<Atom>, String> {
    if field(wire, "kind")? == "unset" {
        return Ok(None);
    }
    Atom::from_json(wire).map(Some)
}

fn slot_to_json(end: &Option<Atom>) -> Value {
    end.as_ref()
        .map_or_else(|| json!({"kind": "unset"}), Atom::to_json)
}

impl VariableValue {
    pub fn from_json(wire: &Value) -> Result<Self, String> {
        let kind = wire
            .get("kind")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("no `kind` in {wire}"))?;
        Ok(match kind {
            "unset" => Self::Unset,
            "multiselect" => {
                let values = field(wire, "values")?
                    .as_array()
                    .ok_or_else(|| format!("`values` is not a list in {wire}"))?;
                Self::Multiselect(
                    values
                        .iter()
                        .map(slot_from_json)
                        .collect::<Result<_, _>>()?,
                )
            }
            "daterange" => Self::Daterange {
                start: slot_from_json(field(wire, "start")?)?,
                end: slot_from_json(field(wire, "end")?)?,
            },
            _ => Self::One(Atom::from_json(wire)?),
        })
    }

    pub fn to_json(&self) -> Value {
        match self {
            Self::Unset => json!({"kind": "unset"}),
            Self::One(atom) => atom.to_json(),
            Self::Multiselect(atoms) => {
                json!({"kind": "multiselect", "values": atoms.iter().map(slot_to_json).collect::<Vec<_>>()})
            }
            Self::Daterange { start, end } => {
                let mut out = Map::new();
                out.insert("kind".into(), json!("daterange"));
                out.insert("start".into(), slot_to_json(start));
                out.insert("end".into(), slot_to_json(end));
                Value::Object(out)
            }
        }
    }
}
