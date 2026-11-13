//! A board's variable controls, settled: the widget each visible variable is, its label, the text it
//! displays and the facts the strip publishes on it. `render/variables_resolve.py` (`resolve_controls`,
//! `format_variable_display_value`, `format_date_label`) decides the same facts. Data only: where a
//! control sits and what it measures to is layout's.
//!
//! Settled from `RunResult.variables` and `overridden`, by `variables_resolve.py`'s laws: the
//! widget, label, drawn and committed text, ticked state, slider bounds and unset label of a
//! `text`, `number`, `checkbox`, `slider`, `select` (static options) or `date` input.
//!
//! Compile settles `input: auto` to a widget, so an unsettled one is an internal error. What this
//! slice does not model is refused by name: a multiselect, a daterange, a radio and the aliases of
//! the inputs it does draw, a select whose options a query answers, an `enabled:` that is not a
//! literal, and a value a widget does not show.

use board_model::board::BoardBody;
use board_model::style::style::Style;
use board_model::unit::Number;
use board_model::ids::QueryId;
use board_model::variable::{refine_options, Enabled, InputType, OptionsQuery, StaticOptions, Variable};
use std::collections::BTreeMap;
use board_model::variable_value::{Atom, VariableValue};
use d3rs::py_float::float_repr;
use diagnostic::{diag, refuse, Diagnostic};
use display_text::language::tr;
use model_core::Engine;

use crate::run::RunResult;

/// A chart's legend view lives in the variable `_dct_legend_<chart-id>` (`board_variables.py`'s
/// `LEGEND_VIEW_PREFIX`); a board's reset item clears it.
const LEGEND_VIEW_PREFIX: &str = "_dct_legend_";

/// The inputs the band draws.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Widget {
    Text,
    Number,
    Checkbox,
    Slider,
    Select,
    Date,
}

/// Where a field's width comes from (`InputTraits.sizing`): the text it draws, the theme's text
/// width as a floor, its number width as a floor, or a shape of its own.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Sizing {
    Content,
    Text,
    Number,
    Checkbox,
    Slider,
}

/// The glyph marking a field's type inside its right edge (`InputTraits.ornament`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Ornament {
    Arrow,
    Calendar,
    None,
}

impl Widget {
    /// The row of `_INPUT_TRAITS` (`variables_layout.py`) for this input.
    pub fn sizing(self) -> Sizing {
        match self {
            Widget::Select | Widget::Date => Sizing::Content,
            Widget::Text => Sizing::Text,
            Widget::Number => Sizing::Number,
            Widget::Checkbox => Sizing::Checkbox,
            Widget::Slider => Sizing::Slider,
        }
    }

    pub fn ornament(self) -> Ornament {
        match self {
            Widget::Select => Ornament::Arrow,
            Widget::Date => Ornament::Calendar,
            _ => Ornament::None,
        }
    }

    /// The `data-dbt-input` the control publishes.
    pub fn name(self) -> &'static str {
        match self {
            Widget::Text => "text",
            Widget::Number => "number",
            Widget::Checkbox => "checkbox",
            Widget::Slider => "slider",
            Widget::Select => "select",
            Widget::Date => "date",
        }
    }
}

/// The bounds a control publishes (`InputTraits.bounds`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Bounds {
    None,
    /// A slider's range, refined from the board or the theme, and where its thumb sits in it.
    Slider {
        min: f64,
        max: f64,
        step: f64,
        fraction: f64,
    },
    /// What the board sets on a number, and nothing else.
    Authored {
        min: Option<f64>,
        max: Option<f64>,
        step: Option<f64>,
    },
}

/// Whether an empty value is a legal state and what it reads as (`can_unset`, `read_only_unset_label`).
#[derive(Debug, Clone, PartialEq)]
pub struct Unset {
    pub can: bool,
    pub label: String,
}

/// One variable, settled.
#[derive(Debug, Clone, PartialEq)]
pub struct Control {
    pub name: String,
    pub widget: Widget,
    pub label: String,
    /// The text the field draws; a checkbox draws none.
    pub value: String,
    /// The value in the form a host commits it (`_committed_value`), not the drawn text.
    pub committed: String,
    pub checked: bool,
    /// Whether the drawn text is the placeholder standing in for a value.
    pub hint: bool,
    /// The authored hint a free-entry field publishes.
    pub placeholder: Option<String>,
    pub bounds: Bounds,
    /// Only the inputs that offer a "no value" state.
    pub unset: Option<Unset>,
}

/// The visible controls of a board in document order, and whether its reset item is enabled.
#[derive(Debug, Clone, PartialEq)]
pub struct Controls {
    pub list: Vec<Control>,
    pub reset_enabled: bool,
}

/// Settles every visible variable of `body` over the values `run` resolved. `style` is the
/// body's total style.
pub fn controls(body: &BoardBody, run: &RunResult, style: &Style, queries: &BTreeMap<String, QueryId>) -> Result<Controls, Diagnostic> {
    let list = body
        .variables
        .iter()
        .filter(|(_, variable)| variable.visible)
        .map(|(name, variable)| control(name, variable, run, style, queries))
        .collect::<Result<Vec<_>, _>>()?;
    // `render_variables_strip_svg`: any control's name, or any legend view, among the caller's.
    let reset_enabled = list.iter().any(|c| run.overridden.contains(&c.name))
        || run
            .overridden
            .iter()
            .any(|name| name.starts_with(LEGEND_VIEW_PREFIX));
    Ok(Controls {
        list,
        reset_enabled,
    })
}

fn control(
    name: &str,
    variable: &Variable,
    run: &RunResult,
    style: &Style,
    queries: &BTreeMap<String, QueryId>,
) -> Result<Control, Diagnostic> {
    let input = match &variable.input {
        Engine::Value(input) => *input,
        Engine::Rule(_) => {
            return Err(diag::err_internal(format!(
                "variable `{name}` reached bind with `input: auto`, which compile settles"
            )));
        }
    };
    let widget = match input {
        InputType::Text => Widget::Text,
        InputType::Number => Widget::Number,
        InputType::Checkbox => Widget::Checkbox,
        InputType::Slider => Widget::Slider,
        InputType::Select => Widget::Select,
        InputType::Date => Widget::Date,
        other => return Err(not_modeled(other.as_str())),
    };
    // A gated-off control is dimmed (`opacity` on its group), which the comparator cannot rule on.
    match &variable.enabled {
        None | Some(Enabled::Static(true)) => {}
        Some(Enabled::Static(false)) => {
            return Err(Diagnostic::refused(refuse!(
                "a variable with `enabled: false` is not supported"
            )))
        }
        Some(_) => {
            return Err(Diagnostic::refused(refuse!(
                "a variable's `enabled:` expression is not supported"
            )))
        }
    }
    let current = match run.variables.get(name) {
        Some(VariableValue::Unset) => None,
        Some(VariableValue::One(atom)) => Some(atom),
        Some(_) => {
            return Err(Diagnostic::refused(refuse!(
                "a `{}` variable holding a list or a date range is not supported",
                widget.name()
            )))
        }
        None => {
            return Err(diag::err_internal(format!(
                "the run carries no value for variable {name}"
            )))
        }
    };
    let label = match variable
        .label
        .as_ref()
        .map(|label| label.as_str())
        .filter(|label| !label.is_empty())
    {
        Some(label) => label.to_string(),
        None => {
            let words = display_text::slug_to_text(name).map_err(diag::err_internal)?;
            display_text::case::apply_case(&words, style.variables.label.font.case.as_str())
                .map_err(diag::err_internal)?
        }
    };
    let placeholder = variable.placeholder.clone().filter(|p| !p.is_empty());
    let blank = current.is_none_or(|atom| matches!(atom, Atom::Text(text) if text.is_empty()));
    let committed = current.map_or_else(String::new, text_of);
    // The word a "no value" reads as: the board's hint, else the language's.
    let unset_label = |id: &str| -> Result<String, Diagnostic> {
        match &placeholder {
            Some(hint) => Ok(hint.clone()),
            None => tr(id, None, &[]).map_err(diag::err_internal),
        }
    };
    let mut control = Control {
        name: name.to_string(),
        widget,
        label,
        value: String::new(),
        committed,
        checked: false,
        hint: false,
        placeholder: None,
        bounds: Bounds::None,
        unset: None,
    };
    match widget {
        Widget::Text | Widget::Number => {
            control.value = if blank {
                placeholder.clone().unwrap_or_default()
            } else {
                text_of(current.expect("not blank"))
            };
            control.hint = blank && placeholder.is_some();
            control.placeholder = placeholder;
            if widget == Widget::Number {
                control.bounds = Bounds::Authored {
                    min: variable.min.map(Number::get),
                    max: variable.max.map(Number::get),
                    step: variable.step.map(Number::get),
                };
            }
        }
        Widget::Checkbox => {
            control.checked = match current {
                Some(Atom::Text(text)) => {
                    matches!(text.trim_matches(d3rs::py_str::is_py_space).to_lowercase().as_str(), "true" | "yes" | "1")
                }
                Some(Atom::Bool(checked)) => *checked,
                Some(Atom::Int(n)) => *n != 0,
                Some(Atom::Float(n)) => *n != 0.0,
                Some(Atom::Date(_)) => true,
                None => false,
            };
            let word = if control.checked { "controls.yes" } else { "controls.no" };
            control.value = tr(word, None, &[]).map_err(diag::err_internal)?;
        }
        Widget::Slider => {
            let defaults = &style.variables.input.range;
            let min = variable.min.map_or(defaults.default_min, Number::get);
            let max = variable.max.map_or(defaults.default_max, Number::get);
            let step = variable.step.map_or(defaults.default_step, Number::get);
            // A slider always shows a position; with nothing committed that is the low end.
            let at = match current {
                None => min,
                Some(Atom::Int(n)) => *n as f64,
                Some(Atom::Float(n)) => *n,
                Some(_) => {
                    return Err(Diagnostic::refused(refuse!(
                        "a slider whose value is not a number is not supported"
                    )))
                }
            };
            let shown = current.map_or_else(|| float_repr(min), text_of);
            control.value = shown.clone();
            control.committed = shown;
            let fraction = if max <= min {
                0.0
            } else {
                ((at - min) / (max - min)).clamp(0.0, 1.0)
            };
            control.bounds = Bounds::Slider {
                min,
                max,
                step,
                fraction,
            };
        }
        Widget::Select => {
            let statics = variable.options.as_ref().and_then(|o| o.r#static.as_ref()).is_some_and(|options| match options {
                StaticOptions::Strings(v) => !v.is_empty(),
                StaticOptions::Numbers(v) => !v.is_empty(),
            });
            if !statics {
                check_options(name, variable, run, queries)?;
            }
            let all = unset_label("controls.all")?;
            control.value = if blank {
                all.clone()
            } else {
                text_of(current.expect("not blank"))
            };
            control.unset = Some(Unset {
                can: !variable.required,
                label: all,
            });
        }
        Widget::Date => {
            let any = unset_label("controls.any_date")?;
            control.value = if blank {
                any.clone()
            } else {
                date_label(&text_of(current.expect("not blank")))?
            };
            control.unset = Some(Unset {
                can: !variable.required,
                label: any,
            });
        }
    }
    Ok(control)
}

/// `match_column` (`compile/column_match.py`): the column `wanted` names, by exact spelling, else
/// by case-folded spelling, which two columns may not share.
fn match_column<'a>(wanted: &str, columns: &[&'a String], at: &str) -> Result<Option<&'a str>, Diagnostic> {
    if let Some(exact) = columns.iter().find(|column| column.as_str() == wanted) {
        return Ok(Some(exact.as_str()));
    }
    let folded = wanted.to_lowercase();
    let matches: Vec<&str> = columns.iter().map(|column| column.as_str()).filter(|column| column.to_lowercase() == folded).collect();
    match matches.as_slice() {
        [] => Ok(None),
        [one] => Ok(Some(one)),
        many => Err(diag::err_column_ref_ambiguous(at.to_string(), wanted.to_string(), many.iter().map(|m| format!("{m:?}")).collect::<Vec<_>>().join(", "))),
    }
}

/// Checks a select whose options a query answers, as `resolve_options` reads them: the first of the
/// `value`, `label`, `name` and `id` columns the rows have (`_option_value_key`), else the first
/// column, each cell as Python's `str` spells it, repeats dropped. The band never prints the options,
/// so only what they would do to the control is kept: options that would turn it into another input
/// (dates, booleans, a numeric range: `refine_options`) are refused, since an authored select and a
/// detected one are no longer told apart.
fn check_options(name: &str, variable: &Variable, run: &RunResult, queries: &BTreeMap<String, QueryId>) -> Result<(), Diagnostic> {
    let Some(OptionsQuery::Name(query)) = variable.options.as_ref().and_then(|o| o.query.as_ref()) else {
        let queried = variable.query.is_some() || variable.column.is_some() || variable.options.as_ref().is_some_and(|o| o.column.is_some());
        return Err(Diagnostic::refused(if queried {
            refuse!("a select whose options come from a query is not supported")
        } else {
            refuse!("a select with no static options is not supported")
        }));
    };
    let key = query.as_str().strip_prefix("queries.").unwrap_or(query.as_str());
    let id = queries.get(key).ok_or_else(|| diag::err_internal(format!("the options query {key} of variable {name} is not one of the board's")))?;
    let dataset = run.datasets.get(id).ok_or_else(|| diag::err_engine_needs_data(id.to_string()))?;
    let Some(first) = dataset.rows.first() else {
        return Ok(());
    };
    let columns: Vec<&String> = first.keys().collect();
    let at = format!("options query {key:?}");
    let mut column = None;
    for wanted in ["value", "label", "name", "id"] {
        if let Some(found) = match_column(wanted, &columns, &at)? {
            column = Some(found);
            break;
        }
    }
    let column = column
        .or_else(|| columns.first().map(|first| first.as_str()))
        .ok_or_else(|| diag::err_internal(format!("the options query {key} of variable {name} returned a row with no columns")))?;
    let mut values: Vec<String> = Vec::new();
    for row in dataset.rows.iter() {
        let text = match row.get(column) {
            None | Some(serde_json::Value::Null) => continue,
            Some(serde_json::Value::String(text)) => text.clone(),
            Some(serde_json::Value::Number(n)) => match (n.as_i64(), n.as_u64(), n.as_f64()) {
                (Some(i), ..) => i.to_string(),
                (None, Some(u), _) => u.to_string(),
                (None, None, Some(f)) => float_repr(f),
                (None, None, None) => return Err(diag::err_internal(format!("the option {n} is not a number"))),
            },
            Some(serde_json::Value::Bool(b)) => text_of(&Atom::Bool(*b)),
            Some(_) => return Err(Diagnostic::refused(refuse!("a select option that is not a plain value is not supported"))),
        };
        if !values.contains(&text) {
            values.push(text);
        }
    }
    if refine_options(&values).is_some() {
        return Err(Diagnostic::refused(refuse!("a select whose options would make it another input is not supported")));
    }
    Ok(())
}

fn not_modeled(input: &str) -> Diagnostic {
    Diagnostic::refused(refuse!("a variable with `input: {input}` is not supported"))
}

/// Python's `str(value)` of a variable's value.
pub fn text_of(atom: &Atom) -> String {
    match atom {
        Atom::Text(text) | Atom::Date(text) => text.clone(),
        Atom::Int(n) => n.to_string(),
        Atom::Float(n) => float_repr(*n),
        Atom::Bool(true) => "True".to_string(),
        Atom::Bool(false) => "False".to_string(),
    }
}

/// `format_date_label`: the board language's date words around the day, short month and year.
/// Production falls back to the raw text on what `date.fromisoformat` rejects, which reads more
/// than `YYYY-MM-DD` (3.11+), so only that form is read and any other is refused.
fn date_label(iso: &str) -> Result<String, Diagnostic> {
    let date = (iso.len() == 10)
        .then(|| iso.parse::<jiff::civil::Date>().ok())
        .flatten();
    let Some(date) = date else {
        return Err(Diagnostic::refused(refuse!(
            "a date value other than YYYY-MM-DD is not supported"
        )));
    };
    let locale = crate::time_locale::active_time_locale().map_err(diag::err_internal)?;
    let month =
        &locale.short_months[usize::try_from(date.month() - 1).expect("a month is 1 to 12")];
    tr(
        "controls.date",
        None,
        &[
            ("day", &date.day().to_string()),
            ("month", month),
            ("year", &date.year().to_string()),
        ],
    )
    .map_err(diag::err_internal)
}

#[cfg(test)]
mod tests {
    #[test]
    fn match_column_prefers_the_exact_spelling_and_refuses_two_folded_matches() {
        let (lower, upper, other) = ("value".to_string(), "VALUE".to_string(), "name".to_string());
        assert_eq!(super::match_column("value", &[&upper, &lower], "q").unwrap(), Some("value"));
        assert_eq!(super::match_column("name", &[&upper, &other], "q").unwrap(), Some("name"));
        assert_eq!(super::match_column("id", &[&upper, &other], "q").unwrap(), None);
        let err = super::match_column("Value", &[&upper, &lower], "options query \"q\"").unwrap_err();
        assert_eq!(err.code, "ERR-COLUMN-REF-AMBIGUOUS");
    }
}
