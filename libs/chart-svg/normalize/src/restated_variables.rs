//! WARN-RESTATED-INFERRED for a variable's authored `input` and `data_type` that the rest of its
//! declaration already implies (Python's `variable_restated`), read off the board as written,
//! before `settle_input` fills the control in: afterwards an authored `select` and a detected one
//! cannot be told apart.
//!
//! `input` is compared with the control inference picks without it, and not at all when that waits
//! on query results (an options query or a column binding). Static options go through the
//! data-aware refinement `refine_input_type_from_data` applies, which can turn a select into a
//! datepicker, a checkbox or a slider. An integral number is spelled as an integer, as the YAML
//! `1` and `1.0` are not told apart here.

use board_model::board::layout::{LayoutAuthored, LayoutItemAuthored};
use board_model::variable::{
    DefaultValue, InputSignals, InputType, Member, OptionsQuery, StaticOptions,
};
use diagnostic::{diag, restated_text, Diagnostic, ModelPath};
use model_core::{Engine, Tri};
use serde_json::Value;

use crate::restated::dotted;

/// What inference reads of a declaration.
struct Signals<'a> {
    list_default: bool,
    slider_field: bool,
    bool_default: bool,
    has_options: bool,
    has_column: bool,
    options_query: bool,
    options: Option<&'a StaticOptions>,
}

/// The control `input` omitted settles on, `_inferred_input`: `None` when it depends on query
/// results.
fn inferred(s: &Signals) -> Option<InputType> {
    let base = InputSignals {
        list_default: s.list_default,
        slider_field: s.slider_field,
        bool_default: s.bool_default,
        has_options: s.has_options,
        has_column: s.has_column,
    }
    .detect();
    if base != InputType::Select {
        return Some(base);
    }
    if s.has_column || s.options_query {
        return None;
    }
    let refined = s
        .options
        .and_then(|o| board_model::variable::refine_options(&spelled(o)));
    Some(refined.map_or(InputType::Select, |r| r.input))
}

/// The option values as Python's `str(value)` spells them.
fn spelled(options: &StaticOptions) -> Vec<String> {
    match options {
        StaticOptions::Strings(s) => s.clone(),
        StaticOptions::Numbers(n) => n
            .iter()
            .map(|n| match n.fract() == 0.0 && n.abs() < 1e15 {
                true => format!("{n:.0}"),
                false => d3rs::py_float::float_repr(*n),
            })
            .collect(),
    }
}

/// The findings for one variable declared at `here` (`variables.region`, `rows.0.variables.region`).
macro_rules! judge {
    ($var:expr, $here:expr, $out:expr) => {{
        let (var, here, out): (_, &str, &mut Vec<Diagnostic>) = ($var, $here, $out);
        let options = match &var.options {
            Tri::Value(o) => Some(o),
            _ => None,
        };
        let statics = match options.map(|o| &o.r#static) {
            Some(Tri::Value(s)) => Some(s),
            _ => None,
        };
        let non_empty = |s: &StaticOptions| match s {
            StaticOptions::Strings(v) => !v.is_empty(),
            StaticOptions::Numbers(v) => !v.is_empty(),
        };
        let query = match options.map(|o| &o.query) {
            Some(Tri::Value(OptionsQuery::Sql(sql))) => !sql.is_empty(),
            Some(Tri::Value(OptionsQuery::Name(_))) => true,
            _ => false,
        };
        let signals = Signals {
            list_default: matches!(var.default, Tri::Value(DefaultValue::List(_))),
            slider_field: [&var.min, &var.max, &var.step]
                .iter()
                .any(|t| matches!(t, Tri::Value(_))),
            bool_default: matches!(var.default, Tri::Value(DefaultValue::Bool(_))),
            has_options: statics.is_some_and(non_empty) || query,
            has_column: matches!(&var.column, Tri::Value(c) if !c.is_empty()),
            options_query: query,
            options: statics,
        };
        let authored = match &var.input {
            Some(Engine::Value(input)) => Some(*input),
            _ => None,
        };
        let finding = |field: &str, value: Value, source: &str| {
            let at = format!("{here}.{field}");
            let model_path = ModelPath(at.split('.').map(str::to_string).collect());
            diag::warn_restated_inferred(at, restated_text(&value), source)
                .with_sink_param("why", "inferred".into())
                .at_path(model_path)
        };
        if let Some(input) = authored {
            if inferred(&signals) == Some(input) {
                out.push(finding("input", Value::from(input.as_str()), "the input its options infer"));
            }
        }
        if let Tri::Value(data_type) = var.data_type {
            let effective = authored.or_else(|| inferred(&signals));
            let choice = matches!(
                effective,
                Some(InputType::Select | InputType::Radio | InputType::Multiselect)
            );
            if choice && Member::of(Some(data_type), statics) == Member::of(None, statics) {
                out.push(finding("data_type", Value::from(data_type.as_str()), "what its static options infer"));
            }
        }
    }};
}

/// The restated `input` and `data_type` of every variable the board declares, the root's first.
pub(crate) fn variables(
    patch: &crate::extends::Patch,
    imported: &std::collections::BTreeSet<String>,
) -> Vec<Diagnostic> {
    let mut out = Vec::new();
    for (name, var) in &patch.body.variables {
        if imported.contains(name) {
            continue;
        }
        judge!(var, &format!("variables.{name}"), &mut out);
    }
    if let Some(layout) = &patch.body.layout {
        nested(layout, &mut Vec::new(), imported, &mut out);
    }
    out
}

fn nested<'k>(
    layout: &'k LayoutAuthored,
    at: &mut Vec<board_model::ids::Step<'k>>,
    imported: &std::collections::BTreeSet<String>,
    out: &mut Vec<Diagnostic>,
) {
    use board_model::ids::{ItemId, Step};
    let item = |item: &'k LayoutItemAuthored, at: &mut Vec<Step<'k>>, out: &mut Vec<Diagnostic>| {
        let LayoutItemAuthored::Board(board) = item else {
            return;
        };
        let prefix = dotted(&ItemId::at(at));
        for (name, var) in &board.body.variables {
            if !imported.contains(name) {
                judge!(var, &format!("{prefix}.variables.{name}"), out);
            }
        }
        if let Some(inner) = &board.body.layout {
            nested(inner, at, imported, out);
        }
    };
    match layout {
        LayoutAuthored::Rows(items) | LayoutAuthored::Cols(items) => {
            at.push(Step::Key(if matches!(layout, LayoutAuthored::Rows(_)) {
                "rows"
            } else {
                "cols"
            }));
            for (i, it) in items.iter().enumerate() {
                at.push(Step::Index(i));
                item(it, at, out);
                at.pop();
            }
            at.pop();
        }
        LayoutAuthored::Grid(g) => {
            at.push(Step::Key("grid"));
            at.push(Step::Key("items"));
            for (i, cell) in g.items.iter().flatten().enumerate() {
                if let Some(it) = &cell.item {
                    at.push(Step::Index(i));
                    at.push(Step::Key("item"));
                    item(it, at, out);
                    at.truncate(at.len() - 2);
                }
            }
            at.truncate(at.len() - 2);
        }
        LayoutAuthored::Tabs(t) => {
            at.push(Step::Key("tabs"));
            at.push(Step::Key("items"));
            for (i, tab) in t.items.iter().flatten().enumerate() {
                at.push(Step::Index(i));
                if let Some(inner) = &tab.layout {
                    nested(inner, at, imported, out);
                }
                at.pop();
            }
            at.truncate(at.len() - 2);
        }
    }
}
