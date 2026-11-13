//! WARN-RESTATED-STYLE / -INFERRED for what a board's tree authors beyond the board's own file
//! (Python's `tree_restated`): a chart's own style leaf the scope it sits in already gives it, a
//! table column's `label` equal to the header derived from its name, and a variable's `label`
//! equal to the one inferred from its name. A nested board's style is judged where the scope walk
//! already holds its parent ([`crate::scope`]); this walk only places those findings in Python's
//! order.
//!
//! Each judgment reads a cascade the compile already built: a chart's leaf is looked up in its
//! scope's family slot, enumerated once per scope and slot however many charts sit in it. Nothing
//! is resolved per key. A chart placed twice warns twice, as Python does; each leaf is judged on
//! its own, so two keys that restate each other are both flagged.

use crate::compile::{public_id, CompileError, Structure};
use crate::merge_warnings::leaves;
use board_model::board::layout::{LayoutAuthored, LayoutItemAuthored};
use board_model::chart::query::ChartQuery;
use board_model::chart::ChartAuthored;
use board_model::ids::{ItemId, Step};
use board_model::query::QueryAuthored;
use board_model::regional::TimeLocaleChoice;
use board_model::style::style::Style;
use d3rs::format_spec::declared_column_format;
use diagnostic::{diag, restated_text, Diagnostic, ModelPath};
use display_text::language::{language_of_time_locale, Language};
use model_core::{LeafValues, Resolved, Tri};
use serde_json::Value;
use std::collections::{BTreeMap, HashMap};

/// Family style fields resolved at render by an explicit order (axis cascade) or a global,
/// family, local stack: not one tier below the chart, so equality with the family tier alone could
/// name a key that still matters.
const ORDERED_AT_RENDER: [&str; 7] = [
    "axis",
    "axis_x",
    "axis_y",
    "axis_quantitative",
    "axis_band",
    "legend",
    "title",
];

/// Style keys the normalizer copies onto the chart before resolution: its own value is the one
/// every later stage reads, so the family tier never answers for it.
const PROMOTED: [&str; 4] = ["stack", "aspect_ratio", "min_height", "max_height"];

const FAMILY_SOURCE: &str = "the board's style for this chart type";

/// The scope a board or tab at `key` sits in: its own, else the root's.
fn scope_of<'a>(
    root: &'a Resolved<Style>,
    nested: &'a BTreeMap<String, Resolved<Style>>,
    key: Option<&str>,
) -> Result<&'a Resolved<Style>, CompileError> {
    let Some(key) = key else {
        return Ok(root);
    };
    nested.get(key).ok_or_else(|| {
        CompileError::Invalid(vec![diag::err_internal(format!(
            "no style scope at {key:?}"
        ))])
    })
}

/// `position` as Python's dotted authoring path (`rows.0.cols.1`).
pub(crate) fn dotted(position: &ItemId) -> String {
    position
        .as_str()
        .trim_start_matches('@')
        .replace('[', ".")
        .replace(']', "")
}

/// What a scope sets in one family slot: its layers' own values over the values inheritance fills,
/// by path from the slot. The layers come first because they keep a color token as written.
#[derive(Default)]
struct Slot {
    set: HashMap<String, Value>,
    filled: HashMap<String, Value>,
}

impl Slot {
    fn held(&self, path: &str) -> Option<&Value> {
        self.set.get(path).or_else(|| self.filled.get(path))
    }
}

fn spelled(
    each: impl FnOnce(&mut dyn FnMut(&str, &dyn model_core::Spell)),
) -> HashMap<String, Value> {
    struct Collect<F>(std::cell::RefCell<Option<F>>);
    impl<F: FnOnce(&mut dyn FnMut(&str, &dyn model_core::Spell))> LeafValues for Collect<F> {
        fn leaf_values(&self, _: &str, f: &mut dyn FnMut(&str, &dyn model_core::Spell)) {
            if let Some(each) = self.0.borrow_mut().take() {
                each(f);
            }
        }
    }
    leaves(&Collect(std::cell::RefCell::new(Some(each))))
        .into_iter()
        .collect()
}

pub(crate) struct Walk<'a> {
    structure: &'a Structure,
    root: &'a Resolved<Style>,
    nested: &'a BTreeMap<String, Resolved<Style>>,
    /// Each nested board's own findings, by its position, from the scope walk.
    boards: &'a BTreeMap<String, Vec<Diagnostic>>,
    slots: HashMap<(String, &'static str), Slot>,
    out: Vec<Diagnostic>,
}

/// The findings of the tree, within themselves in Python's order (the compile report appends them
/// to the structure's own findings): per board its variable labels, then each item in
/// layout order (a chart's, or a nested board's own style findings and then everything inside it).
pub(crate) fn tree(
    structure: &Structure,
    root: &Resolved<Style>,
    nested: &BTreeMap<String, Resolved<Style>>,
    boards: &BTreeMap<String, Vec<Diagnostic>>,
) -> Result<Vec<Diagnostic>, CompileError> {
    let mut walk = Walk {
        structure,
        root,
        nested,
        boards,
        slots: HashMap::new(),
        out: Vec::new(),
    };
    let body = &structure.patch.body;
    let labels = body
        .variables
        .iter()
        .filter_map(|(name, var)| match &var.label {
            Tri::Value(label) if !structure.imported_variables.contains(name) => {
                Some((name.as_str(), label.as_str()))
            }
            _ => None,
        })
        .collect();
    walk.variables(labels, "", root)?;
    if let Some(layout) = &body.layout {
        walk.layout(layout, &mut Vec::new(), None)?;
    }
    Ok(walk.out)
}

impl Walk<'_> {
    fn layout<'k>(
        &mut self,
        layout: &'k LayoutAuthored,
        at: &mut Vec<Step<'k>>,
        scope: Option<&str>,
    ) -> Result<(), CompileError> {
        match layout {
            LayoutAuthored::Rows(items) | LayoutAuthored::Cols(items) => {
                let key = if matches!(layout, LayoutAuthored::Rows(_)) {
                    "rows"
                } else {
                    "cols"
                };
                at.push(Step::Key(key));
                for (i, item) in items.iter().enumerate() {
                    at.push(Step::Index(i));
                    self.item(item, at, scope)?;
                    at.pop();
                }
                at.pop();
            }
            LayoutAuthored::Grid(g) => {
                at.push(Step::Key("grid"));
                at.push(Step::Key("items"));
                for (i, cell) in g.items.iter().flatten().enumerate() {
                    if let Some(item) = &cell.item {
                        at.push(Step::Index(i));
                        at.push(Step::Key("item"));
                        self.item(item, at, scope)?;
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
                    let pane = ItemId::at(at).as_str().to_string();
                    if let Some(found) = self.boards.get(&pane) {
                        self.out.extend(found.iter().cloned());
                    }
                    if let Some(inner) = &tab.layout {
                        self.layout(inner, at, Some(&pane))?;
                    }
                    at.pop();
                }
                at.truncate(at.len() - 2);
            }
        }
        Ok(())
    }

    fn item<'k>(
        &mut self,
        item: &'k LayoutItemAuthored,
        at: &mut Vec<Step<'k>>,
        scope: Option<&str>,
    ) -> Result<(), CompileError> {
        let position = ItemId::at(at);
        match item {
            LayoutItemAuthored::Board(nested) => {
                let key = position.as_str();
                if let Some(found) = self.boards.get(key) {
                    self.out.extend(found.iter().cloned());
                }
                let prefix = format!("{}.", dotted(&position));
                let resolved = scope_of(self.root, self.nested, Some(key))?;
                let labels = nested
                    .body
                    .variables
                    .iter()
                    .filter_map(|(name, var)| match &var.label {
                        Tri::Value(label) if !self.structure.imported_variables.contains(name) => {
                            Some((name.as_str(), label.as_str()))
                        }
                        _ => None,
                    })
                    .collect();
                self.variables(labels, &prefix, resolved)?;
                if let Some(inner) = &nested.body.layout {
                    self.layout(inner, at, Some(key))?;
                }
                Ok(())
            }
            LayoutItemAuthored::Chart(_) | LayoutItemAuthored::InlineChart(_) => {
                let missing = |what: &str| {
                    CompileError::Invalid(vec![diag::err_internal(format!("{what} at {position}"))])
                };
                let name = self
                    .structure
                    .layout
                    .charts
                    .get(&position)
                    .ok_or_else(|| missing("no chart placed"))?;
                let Some(Some(chart)) = self.structure.patch.body.charts.get(name) else {
                    return Err(missing("no chart defined"));
                };
                self.chart(name, chart, scope)
            }
        }
    }

    /// The chart's own style leaves that its scope's family slot already holds.
    fn chart(
        &mut self,
        name: &str,
        chart: &ChartAuthored,
        scope_key: Option<&str>,
    ) -> Result<(), CompileError> {
        let (Some(overlay), Some(family)) = (chart.style_overlay(), chart.family_slot()) else {
            return Ok(());
        };
        let public = public_id(&self.structure.index, name)?;
        let scope = scope_of(self.root, self.nested, scope_key)?;
        let slot = self
            .slots
            .entry((scope_key.unwrap_or("").to_string(), family))
            .or_insert_with(|| Slot {
                set: spelled(|f| chart.family_leaf_values(&scope.raw, f)),
                filled: spelled(|f| chart.family_leaf_values(&scope.inherited, f)),
            });
        let prefix = format!("charts.{family}.");
        let own = leaves(&overlay);
        let mut headers = Vec::new();
        let mut formats = Vec::new();
        let mut slot_formats = Vec::new();
        for (path, value) in own {
            let Some(leaf) = path.strip_prefix(&prefix) else {
                continue;
            };
            if let (true, Some(column), Value::String(label)) = (
                family == "table",
                leaf.strip_prefix("columns.")
                    .and_then(|l| l.strip_suffix(".label")),
                &value,
            ) {
                headers.push((column.to_string(), label.clone()));
            }
            if let (Some(column), Value::String(format)) = (
                leaf.strip_prefix("columns.")
                    .and_then(|l| l.strip_suffix(".format")),
                &value,
            ) {
                formats.push((column.to_string(), format.clone()));
            }
            if let (Some((slot, columns, text)), Value::String(format)) = (
                match leaf {
                    "value.format" => chart.value_columns().map(|c| (leaf, c, true)),
                    "axis_y.labels.format" => chart.y_columns().map(|c| (leaf, c, false)),
                    _ => None,
                },
                &value,
            ) {
                slot_formats.push((slot.to_string(), columns, text, format.clone()));
            }
            let head = leaf.split('.').next().unwrap_or(leaf);
            if ORDERED_AT_RENDER.contains(&head)
                || PROMOTED.contains(&head)
                || leaf.ends_with(".extends")
                || slot.held(leaf) != Some(&value)
            {
                continue;
            }
            let at = format!("charts.{public}.style.{leaf}");
            let model_path = ModelPath(at.split('.').map(str::to_string).collect());
            self.out.push(
                diag::warn_restated_style(at, restated_text(&value), FAMILY_SOURCE)
                    .with_sink_param("why", "board style".into())
                    .at_path(model_path),
            );
        }
        let declared = match chart.base().map(|b| &b.query) {
            Some(Tri::Value(ChartQuery::Hoisted(id))) => {
                let queries = &self.structure.patch.body.queries;
                queries
                    .get(&id.to_string())
                    .or_else(|| queries.get(id.name().as_str()))
            }
            _ => None,
        }
        .and_then(|query| match query.as_ref()? {
            QueryAuthored::Sql(q) => Some(&q.base.formats),
            QueryAuthored::Values(q) => Some(&q.base.formats),
            QueryAuthored::Schema(q) => Some(&q.base.formats),
        });
        let case = scope.total.charts.table.header.font.case.as_str();
        for (column, label) in headers {
            let inferred = display_text::slug_to_text(&column)
                .and_then(|text| display_text::case::apply_case(&text, case))
                .map_err(|e| CompileError::Invalid(vec![diag::err_internal(e)]))?;
            if label == inferred {
                let at = format!("charts.{public}.style.columns.{column}.label");
                let model_path = ModelPath(at.split('.').map(str::to_string).collect());
                self.out.push(
                    diag::warn_restated_inferred(
                        at,
                        label,
                        "the header derived from the column name",
                    )
                    .with_sink_param("why", "derived header".into())
                    .at_path(model_path),
                );
            }
        }
        // A grain name the board did not redefine has no spec to judge; the format checks report it.
        let alias = |name: &str| scope.total.formats.get(name).map(|c| c.spec.as_deref());
        let entry = |column: &str| {
            declared?
                .get(column)?
                .as_ref()
                .map(board_model::facet::FormatName::as_str)
        };
        for (slot, columns, text, format) in slot_formats {
            let restated = declared_column_format(entry, &columns.names, &alias, false, text)
                .ok()
                .flatten()
                == Some(format.as_str());
            if restated {
                let at = format!("charts.{public}.style.{slot}");
                let model_path = ModelPath(at.split('.').map(str::to_string).collect());
                let source = format!("the query's `formats:` entry for `{}`", columns.shown);
                self.out.push(
                    diag::warn_restated_format(at, format, source)
                        .with_sink_param("why", "query format".into())
                        .at_path(model_path),
                );
            }
        }
        for (column, format) in formats {
            if declared
                .and_then(|d| d.get(&column))
                .is_some_and(|d| d.as_ref().is_some_and(|d| d.as_str() == format))
            {
                let at = format!("charts.{public}.style.columns.{column}.format");
                let model_path = ModelPath(at.split('.').map(str::to_string).collect());
                let source = format!("the query's `formats:` entry for `{column}`");
                self.out.push(
                    diag::warn_restated_format(at, format, source)
                        .with_sink_param("why", "query format".into())
                        .at_path(model_path),
                );
            }
        }
        Ok(())
    }

    /// A variable's authored `label` equal to the one its name infers, in the label font's case.
    fn variables(
        &mut self,
        labels: Vec<(&str, &str)>,
        prefix: &str,
        scope: &Resolved<Style>,
    ) -> Result<(), CompileError> {
        let case = scope.total.variables.label.font.case.as_str();
        let language = self.language()?;
        for (name, label) in labels {
            let inferred = display_text::language::with_language(language, || {
                display_text::slug_to_text(name).and_then(|text| {
                    display_text::case::apply_case_in(language.code(), &text, case)
                })
            })
            .map_err(|e| CompileError::Invalid(vec![diag::err_internal(e)]))?;
            if label == inferred {
                let at = format!("{prefix}variables.{name}.label");
                let model_path = ModelPath(at.split('.').map(str::to_string).collect());
                self.out.push(
                    diag::warn_restated_inferred(
                        at,
                        label,
                        "the label inferred from the variable name",
                    )
                    .with_sink_param("why", "inferred".into())
                    .at_path(model_path),
                );
            }
        }
        Ok(())
    }

    /// The language the board's words print in: its `regional.language`, else its time locale's.
    fn language(&self) -> Result<Language, CompileError> {
        let Tri::Value(regional) = &self.structure.patch.regional else {
            return Language::new("en")
                .map_err(|e| CompileError::Invalid(vec![diag::err_internal(e)]));
        };
        let time = match &regional.locale {
            Tri::Value(locale) => match &locale.time {
                Some(TimeLocaleChoice::Name(name)) => Some(name.as_str()),
                _ => None,
            },
            _ => None,
        };
        let code = match &regional.language {
            Tri::Value(language) => language.as_str(),
            _ => language_of_time_locale(time.or(Some("en-US"))),
        };
        Language::new(code).map_err(|e| CompileError::Invalid(vec![diag::err_internal(e)]))
    }
}
