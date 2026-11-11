//! The visible variables of a board with their defaults, as Python's `variable_defaults` hold
//! them after `normalize_render_values`: what a control starts at. A default of a shape this
//! reads is not thereby valid: `validate_variable_value` (the type check on `default:`) is a
//! later pass, so an `Ok` here is a value, not a verdict. A default is a value (`FactsDefault`),
//! or a daterange preset id kept as written.

use board_model::variable::{DefaultValue, InputType, VariableAuthored, VariablePatch};
use board_model::variable_value::{Atom, VariableValue};
use model_core::{Engine, Tri};

/// A variable's default.
#[derive(Debug, Clone, PartialEq)]
pub enum FactsDefault {
    Value(VariableValue),
    /// A daterange written as a rolling preset (`last_7_days`), kept as authored. It becomes two
    /// dates on the board's clock and the fiscal settings, which only plan has, so it is not
    /// checked against the preset table here.
    Preset(String),
}

/// One visible variable of a board, in declaration order.
#[derive(Debug, Clone, PartialEq)]
pub struct VariableFacts {
    pub name: String,
    pub default: FactsDefault,
}

fn atom(v: &DefaultValue) -> Result<Option<Atom>, String> {
    Ok(match v {
        DefaultValue::Null => None,
        DefaultValue::Bool(b) => Some(Atom::Bool(*b)),
        DefaultValue::Text(s) => Some(Atom::Text(s.clone())),
        DefaultValue::Date(s) => Some(Atom::Date(s.clone())),
        DefaultValue::DateTime(s) => {
            return Err(format!(
                "{s} is a datetime, and a default is a calendar date"
            ))
        }
        DefaultValue::Number(n) => Some(match (n.as_i64(), n.as_f64()) {
            (Some(i), _) => Atom::Int(i),
            (None, Some(f)) => Atom::Float(f),
            (None, None) => return Err(format!("the number {n} is out of range")),
        }),
        DefaultValue::List(_) | DefaultValue::Map(_) => return Err("not a scalar".to_string()),
    })
}

fn is_blank(v: &DefaultValue) -> bool {
    matches!(v, DefaultValue::Text(s) if s.trim().is_empty())
}

/// The three fields of a variable declaration a default depends on, whichever form it was read in.
pub(crate) struct Var<'a> {
    pub input: Option<&'a InputType>,
    pub default: Option<&'a DefaultValue>,
}

impl<'a> From<&'a VariableAuthored> for Var<'a> {
    fn from(var: &'a VariableAuthored) -> Self {
        Var {
            input: match &var.input {
                Some(Engine::Value(i)) => Some(i),
                _ => None,
            },
            default: match &var.default {
                Tri::Value(DefaultValue::Null) => None,
                Tri::Value(v) => Some(v),
                _ => None,
            },
        }
    }
}

impl<'a> From<&'a VariablePatch> for Var<'a> {
    fn from(var: &'a VariablePatch) -> Self {
        Var {
            input: match &var.input {
                Some(Engine::Value(i)) => Some(i),
                _ => None,
            },
            default: match &var.default {
                Tri::Value(DefaultValue::Null) => None,
                Tri::Value(v) => Some(v),
                _ => None,
            },
        }
    }
}

/// What Python's `normalize_render_values` makes of a variable's default. The input is the
/// written one, or, for `auto`, multiselect when the default is a list (`detect_variable_input_type`).
pub(crate) fn default_of(var: Var<'_>) -> Result<FactsDefault, String> {
    let Var {
        input,
        default: raw,
    } = var;
    let multiselect = matches!(input, Some(InputType::Multiselect))
        || (input.is_none() && matches!(raw, Some(DefaultValue::List(_))));
    if multiselect {
        let members = match raw {
            None => Vec::new(),
            Some(DefaultValue::List(items)) => items.iter().map(atom).collect::<Result<_, _>>()?,
            Some(v) if is_blank(v) => Vec::new(),
            Some(v) => vec![atom(v)?],
        };
        return Ok(FactsDefault::Value(VariableValue::Multiselect(members)));
    }
    if matches!(input, Some(InputType::Daterange)) {
        return match raw {
            None => Ok(FactsDefault::Value(VariableValue::Unset)),
            Some(v) if is_blank(v) => Err("a daterange default is not blank".to_string()),
            Some(DefaultValue::Text(s)) => Ok(FactsDefault::Preset(s.clone())),
            Some(DefaultValue::List(ends)) => match ends.as_slice() {
                [start, end] => {
                    let end_of = |v: &DefaultValue| match v {
                        DefaultValue::Text(_) | DefaultValue::Date(_) | DefaultValue::Null => {
                            atom(v)
                        }
                        _ => Err("a daterange default's ends are dates".to_string()),
                    };
                    Ok(FactsDefault::Value(VariableValue::Daterange {
                        start: end_of(start)?,
                        end: end_of(end)?,
                    }))
                }
                _ => Err("a daterange default is a preset or [start, end]".to_string()),
            },
            Some(_) => Err("a daterange default is a preset or [start, end]".to_string()),
        };
    }
    Ok(FactsDefault::Value(match raw {
        None => VariableValue::Unset,
        Some(DefaultValue::List(_) | DefaultValue::Map(_)) => {
            return Err("a list default needs input: multiselect".to_string())
        }
        Some(v) => atom(v)?.map_or(VariableValue::Unset, VariableValue::One),
    }))
}
