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
//! slice does not model is refused by name: a radio and the aliases of
//! the inputs it does draw, a select whose options a query answers, an `enabled:` that is not a
//! literal, and a value a widget does not show. A multiselect settles like a select over the list it
//! holds: its text is the one chosen value, or a count beside the first, and it commits the list as JSON.

use board_model::board::BoardBody;
use board_model::style::style::Style;
use board_model::unit::Number;
use board_model::ids::QueryId;
use board_model::style::variables::PresetsDisplay;
use board_model::variable::{refine_options, DefaultValue, Enabled, InputType, OptionsQuery, StaticOptions, Variable};
use std::collections::BTreeMap;
use board_model::variable_value::{Atom, VariableValue};
use d3rs::py_float::float_repr;
use diagnostic::{diag, refuse, Diagnostic};
use display_text::language::tr;
use model_core::Engine;

use crate::run::RunResult;

/// A chart's legend view lives in the variable `_dct_legend_<chart-id>` (`board_variables.py`'s
/// `LEGEND_VIEW_PREFIX`); a board's reset item clears it.
pub(crate) const LEGEND_VIEW_PREFIX: &str = "_dct_legend_";

/// The inputs the band draws.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Widget {
    Text,
    Number,
    Checkbox,
    Slider,
    Select,
    Multiselect,
    Date,
    Daterange,
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
    Daterange,
    /// The theme's text width whatever is chosen, the text cut to fit it (`summary`).
    Summary,
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
            Widget::Daterange => Sizing::Daterange,
            Widget::Multiselect => Sizing::Summary,
        }
    }

    pub fn ornament(self) -> Ornament {
        match self {
            Widget::Select | Widget::Multiselect => Ornament::Arrow,
            Widget::Date | Widget::Daterange => Ornament::Calendar,
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
            Widget::Multiselect => "multiselect",
            Widget::Date => "date",
            Widget::Daterange => "daterange",
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
        InputType::Multiselect => Widget::Multiselect,
        InputType::Date => Widget::Date,
        InputType::Daterange => Widget::Daterange,
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
        Some(VariableValue::Daterange { .. }) if widget == Widget::Daterange => None,
        // A multiselect reads its whole value below: it is a list, a bare scalar or nothing.
        Some(VariableValue::Multiselect(_)) if widget == Widget::Multiselect => None,
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
        Widget::Select | Widget::Multiselect => {
            let statics = variable.options.as_ref().and_then(|o| o.r#static.as_ref()).is_some_and(|options| match options {
                StaticOptions::Strings(v) => !v.is_empty(),
                StaticOptions::Numbers(v) => !v.is_empty(),
            });
            if !statics {
                check_options(name, variable, run, queries)?;
            }
            let all = unset_label("controls.all")?;
            if widget == Widget::Select {
                control.value = if blank {
                    all.clone()
                } else {
                    text_of(current.expect("not blank"))
                };
            } else {
                let chosen = selection(run.variables.get(name));
                control.value = match chosen.as_slice() {
                    [] => all.clone(),
                    [only] => member_text(only),
                    [first, ..] => tr(
                        "controls.selection_summary",
                        None,
                        &[("first", &member_text(first)), ("n", &chosen.len().to_string())],
                    )
                    .map_err(diag::err_internal)?,
                };
                // The coerced list, so nothing chosen commits `[]`, never an empty text.
                control.committed = committed_list(&chosen);
            }
            control.unset = Some(Unset {
                can: !variable.required,
                label: all,
            });
        }
        Widget::Daterange => {
            let all = unset_label("controls.all_dates")?;
            let (label, committed) = daterange_text(name, variable, run, style, all.clone())?;
            control.value = label;
            control.committed = committed;
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

/// `coerce_multiselect`: a multiselect's members however its value arrived, a list, a bare scalar
/// from a link written before the input was one, or nothing. A blank scalar is no selection.
fn selection(held: Option<&VariableValue>) -> Vec<Option<&Atom>> {
    match held {
        Some(VariableValue::Multiselect(members)) => members.iter().map(Option::as_ref).collect(),
        Some(VariableValue::One(Atom::Text(text))) if text.trim_matches(d3rs::py_str::is_py_space).is_empty() => Vec::new(),
        Some(VariableValue::One(atom)) => vec![Some(atom)],
        _ => Vec::new(),
    }
}

/// `str(member)` for display: a member Python holds as `None` reads `None`.
fn member_text(member: &Option<&Atom>) -> String {
    member.map_or_else(|| "None".to_string(), text_of)
}

/// `committed_text` of a list: `json.dumps` with its defaults, so `", "` between members and every
/// non-ASCII code point a `\uXXXX` escape (an astral one a surrogate pair).
fn committed_list(members: &[Option<&Atom>]) -> String {
    let mut out = String::from("[");
    for (i, member) in members.iter().enumerate() {
        if i > 0 {
            out.push_str(", ");
        }
        match member {
            None => out.push_str("null"),
            Some(Atom::Bool(on)) => out.push_str(if *on { "true" } else { "false" }),
            Some(Atom::Int(n)) => out.push_str(&n.to_string()),
            Some(Atom::Float(n)) if n.is_nan() => out.push_str("NaN"),
            Some(Atom::Float(n)) if n.is_infinite() => out.push_str(if *n > 0.0 { "Infinity" } else { "-Infinity" }),
            Some(Atom::Float(n)) => out.push_str(&float_repr(*n)),
            Some(Atom::Text(text) | Atom::Date(text)) => out.push_str(&d3rs::py_str::json_string(text)),
        }
    }
    out.push(']');
    out
}

/// A daterange's drawn text and the value a host commits (`format_variable_display_value`,
/// `_committed_value`). The text always reads the resolved dates. A preset-valued range commits its
/// preset id (`RunResult::presets`), which keeps a link to it rolling; any other range commits its
/// pair of ends, empty ends included; a control holding no value commits nothing, or the blank text
/// it was given. The preset buttons need the preset grammar and are refused by name, as is a value
/// `date.fromisoformat` reads that is not `YYYY-MM-DD`.
fn daterange_text(name: &str, variable: &Variable, run: &RunResult, style: &Style, unset: String) -> Result<(String, String), Diagnostic> {
    if style.variables.presets_display == PresetsDisplay::Buttons {
        return Err(Diagnostic::refused(refuse!("a daterange offering its presets as buttons is not supported")));
    }
    let preset = run.presets.get(name);
    if preset.is_none() && matches!(variable.default, Some(DefaultValue::Text(_))) && !run.overridden.contains(name) {
        return Err(diag::err_internal(format!("the plan carries no preset id for the daterange {name}, whose default is a preset")));
    }
    let held = run.variables.get(name);
    let (start, end) = match held {
        Some(VariableValue::Daterange { start, end }) => (start.as_ref().map(text_of), end.as_ref().map(text_of)),
        Some(VariableValue::Unset) | None => (None, None),
        // `?d=`, the link that unsets a range, arrives as blank text: no value, committed as written.
        Some(VariableValue::One(Atom::Text(text))) if text.trim_matches(d3rs::py_str::is_py_space).is_empty() => (None, None),
        Some(_) => return Err(diag::err_internal(format!("the daterange {name} holds a value that is not a range"))),
    };
    let open = |end: &Option<String>| end.as_deref().is_none_or(str::is_empty);
    // The label first: it refuses any end that is not an ISO date, which is what lets the pair below
    // quote an end as plain text.
    let label = match (open(&start), open(&end)) {
        (true, true) => unset,
        (true, false) => tr("controls.until", None, &[("date", &date_label(end.as_deref().unwrap_or_default())?)]).map_err(diag::err_internal)?,
        (false, true) => tr("controls.from", None, &[("date", &date_label(start.as_deref().unwrap_or_default())?)]).map_err(diag::err_internal)?,
        (false, false) => range_label(start.as_deref().unwrap_or_default(), end.as_deref().unwrap_or_default())?,
    };
    let committed = match (preset, held) {
        (Some(id), _) => id.clone(),
        (None, Some(VariableValue::Daterange { .. })) => {
            let quoted = |end: &Option<String>| end.as_ref().map_or_else(|| "null".to_string(), |text| serde_json::Value::String(text.clone()).to_string());
            format!("[{}, {}]", quoted(&start), quoted(&end))
        }
        (None, Some(VariableValue::One(Atom::Text(blank)))) => blank.clone(),
        (None, _) => String::new(),
    };
    Ok((label, committed))
}

/// `format_daterange_label`: the board language's words for a closed range.
fn range_label(start: &str, end: &str) -> Result<String, Diagnostic> {
    let (s, e) = (iso_date(start)?, iso_date(end)?);
    let no_year = |d: &jiff::civil::Date| tr("controls.date_no_year", None, &[("day", &d.day().to_string()), ("month", &short_month(d)?)]).map_err(diag::err_internal);
    if s == e {
        return date_label(start);
    }
    if (s.year(), s.month()) == (e.year(), e.month()) {
        return tr(
            "controls.range_same_month",
            None,
            &[("start_day", &s.day().to_string()), ("end_day", &e.day().to_string()), ("month", &short_month(&s)?), ("year", &s.year().to_string())],
        )
        .map_err(diag::err_internal);
    }
    let (first, last) = if s.year() == e.year() { (no_year(&s)?, date_label(end)?) } else { (date_label(start)?, date_label(end)?) };
    tr("controls.range", None, &[("start", &first), ("end", &last)]).map_err(diag::err_internal)
}

/// A `YYYY-MM-DD` day; Python reads more spellings (3.11+), which are refused.
fn iso_date(iso: &str) -> Result<jiff::civil::Date, Diagnostic> {
    (iso.len() == 10)
        .then(|| iso.parse::<jiff::civil::Date>().ok())
        .flatten()
        .ok_or_else(|| Diagnostic::refused(refuse!("a date value other than YYYY-MM-DD is not supported")))
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
    let columns: Vec<&str> = first.keys().map(String::as_str).collect();
    let at = format!("options query {}", diagnostic::py_str_repr(key));
    let mut column = None;
    for wanted in ["value", "label", "name", "id"] {
        if let Some(found) = crate::column_refs::match_column(wanted, &columns, &at)? {
            column = Some(found);
            break;
        }
    }
    let column = column
        .or_else(|| columns.first().copied())
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
    let date = iso_date(iso)?;
    tr(
        "controls.date",
        None,
        &[
            ("day", &date.day().to_string()),
            ("month", &short_month(&date)?),
            ("year", &date.year().to_string()),
        ],
    )
    .map_err(diag::err_internal)
}

/// `_month`: the date's month as the board's time locale abbreviates it.
fn short_month(date: &jiff::civil::Date) -> Result<String, Diagnostic> {
    let locale = crate::time_locale::active_time_locale().map_err(diag::err_internal)?;
    Ok(locale.short_months[usize::try_from(date.month() - 1).expect("a month is 1 to 12")].clone())
}

#[cfg(test)]
mod multiselect_tests {
    use super::*;

    fn text(t: &str) -> Atom {
        Atom::Text(t.to_string())
    }

    #[test]
    fn a_list_commits_as_json_dumps_with_ascii_escapes() {
        let (a, b, c) = (text("Ünï \"q\"\n"), Atom::Int(3), text("😀"));
        let held = [Some(&a), Some(&b), None, Some(&c)];
        assert_eq!(
            committed_list(&held),
            r#"["\u00dcn\u00ef \"q\"\n", 3, null, "\ud83d\ude00"]"#
        );
        assert_eq!(committed_list(&[]), "[]");
    }

    #[test]
    fn a_float_commits_as_its_python_repr() {
        let (tiny, whole, big) = (Atom::Float(1e-5), Atom::Float(2.5), Atom::Float(1e16));
        assert_eq!(committed_list(&[Some(&tiny), Some(&whole), Some(&big), Some(&Atom::Bool(true))]).as_str(), "[1e-05, 2.5, 1e+16, true]");
        assert_eq!(committed_list(&[Some(&Atom::Float(f64::NAN)), Some(&Atom::Float(f64::NEG_INFINITY))]), "[NaN, -Infinity]");
    }

    #[test]
    fn a_value_is_one_selection_however_it_arrived() {
        let one = VariableValue::One(text("US"));
        let blank = VariableValue::One(text("  "));
        let list = VariableValue::Multiselect(vec![Some(text("a")), None]);
        assert_eq!(selection(Some(&one)).len(), 1);
        assert!(selection(Some(&blank)).is_empty());
        assert!(selection(Some(&VariableValue::Unset)).is_empty());
        assert!(selection(None).is_empty());
        assert_eq!(selection(Some(&list)), vec![Some(&text("a")), None]);
        assert_eq!(member_text(&None), "None");
    }
}
