//! The fill pass for what a declared `fill = fn` computes from its neighbors (the constant fills
//! run in the build): a values query's rows from its `columns` and `values`, an `auto` variable
//! input as the control it detects.

use crate::files::ProjectFiles;
use board_model::board::layout::{LayoutAuthored, LayoutItemAuthored};
use board_model::board::{Board, HtmlPolicy};
use board_model::query::{QueryAuthored, Row};
use board_model::rule::Auto;
use board_model::variable::{DefaultValue, InputType, OptionsQuery, StaticOptions};
use model_core::{Engine, Leaf, Tri};
use project::ProjectPath;
use std::fmt;

type Patch = <Board as Leaf>::Patch;

/// A fill the model cannot compute.
#[derive(Debug, Clone, PartialEq)]
pub enum FillError {
    /// `dbt_charts.yml` does not give a usable `html_policy_ceiling`.
    BadCeiling(String),
    /// `card_gap: true` on a nested board (Python: only the root sets it).
    NestedCardGap,
    /// A positional row whose length is not the column count.
    RowShape { query: String, row: usize },
}

impl fmt::Display for FillError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::BadCeiling(why) => f.write_str(why),
            Self::NestedCardGap => {
                f.write_str("card_gap can only be set on the root board, not on nested boards")
            }
            Self::RowShape { query, row } => write!(
                f,
                "query '{query}': values row {row} does not have one cell per column"
            ),
        }
    }
}

impl std::error::Error for FillError {}

/// `rows` of every values query that writes `columns` and `values` instead.
pub fn fill_queries(patch: &mut Patch) -> Result<(), FillError> {
    for (name, query) in patch.body.queries.iter_mut() {
        let Some(QueryAuthored::Values(v)) = query.as_mut() else {
            continue;
        };
        if v.rows.is_some() {
            continue;
        }
        let (Tri::Value(columns), Tri::Value(values)) = (&v.columns, &v.values) else {
            continue;
        };
        // Python builds no rows from an empty column list: they stay empty.
        if columns.is_empty() {
            v.rows = Some(Vec::new());
            continue;
        }
        let mut rows = Vec::new();
        for (i, cells) in values.iter().enumerate() {
            if cells.len() != columns.len() {
                return Err(FillError::RowShape {
                    query: name.clone(),
                    row: i,
                });
            }
            rows.push(Row(columns
                .iter()
                .cloned()
                .zip(cells.iter().cloned())
                .collect()));
        }
        v.rows = Some(rows);
    }
    Ok(())
}

/// Python's `detect_variable_input_type` for an `auto` input, from the variable's own fields
/// in its order of signals: a list default, then a slider field, a bool default, options or a
/// column binding, else text. A variable is a patch in the root body and authored in a nested
/// one, with the same fields.
macro_rules! settle_input {
    ($var:expr) => {{
        let var = $var;
        if matches!(var.input, None | Some(Engine::Rule(Auto::Auto))) {
            let has_options = match &var.options {
                Tri::Value(o) => match (&o.r#static, &o.query) {
                    (Tri::Value(StaticOptions::Strings(s)), _) if !s.is_empty() => true,
                    (Tri::Value(StaticOptions::Numbers(n)), _) if !n.is_empty() => true,
                    (_, Tri::Value(OptionsQuery::Sql(sql))) => !sql.is_empty(),
                    (_, Tri::Value(OptionsQuery::Name(_))) => true,
                    _ => false,
                },
                _ => false,
            };
            let slider = [&var.min, &var.max, &var.step]
                .iter()
                .any(|t| matches!(t, Tri::Value(_)));
            var.input = Some(Engine::Value(match var.default {
                Tri::Value(DefaultValue::List(_)) => InputType::Multiselect,
                _ if slider => InputType::Slider,
                Tri::Value(DefaultValue::Bool(_)) => InputType::Checkbox,
                _ if has_options || matches!(&var.column, Tri::Value(c) if !c.is_empty()) => {
                    InputType::Select
                }
                _ => InputType::Text,
            }));
        }
    }};
}

fn rank(p: HtmlPolicy) -> u8 {
    match p {
        HtmlPolicy::Escaped => 0,
        HtmlPolicy::SafeSubset => 1,
        HtmlPolicy::TrustedRaw => 2,
    }
}

/// The project's `html_policy_ceiling` (`dbt_charts.yml` at the project root), the most permissive
/// tier any board may use; `trusted-raw` (no ceiling) when there is no such file or key. A file that
/// does not read, or a ceiling that is no tier, is an error: a ceiling is never quietly dropped.
/// A deployment may pin one in its environment (Cloud's `DCT_HTML_POLICY_CEILING`); the engine reads
/// no environment, so the host passes it to `compile`, where it replaces this.
pub fn html_policy_ceiling(files: &dyn ProjectFiles) -> Result<HtmlPolicy, FillError> {
    let Some(text) = ProjectPath::try_from("dbt_charts.yml")
        .ok()
        .and_then(|p| files.read(&p))
    else {
        return Ok(HtmlPolicy::TrustedRaw);
    };
    let bad = |why: String| FillError::BadCeiling(why);
    let node = yaml::parse_any(&text).map_err(|d| {
        let first = d.first();
        let why = first
            .and_then(|d| d.message().ok())
            .or_else(|| first.map(|d| d.code.to_string()))
            .unwrap_or_default();
        bad(format!("dbt_charts.yml does not read: {why}"))
    })?;
    let ceiling = match &node.kind {
        yaml::Kind::Scalar(yaml::Value::Null) => return Ok(HtmlPolicy::TrustedRaw),
        yaml::Kind::Map(m) => match m.get("html_policy_ceiling") {
            None => return Ok(HtmlPolicy::TrustedRaw),
            Some(e) => &e.value.kind,
        },
        _ => return Err(bad("dbt_charts.yml is not a mapping".to_string())),
    };
    match ceiling {
        yaml::Kind::Scalar(yaml::Value::Str(s)) => match s.as_str() {
            "trusted-raw" => Ok(HtmlPolicy::TrustedRaw),
            "safe-subset" => Ok(HtmlPolicy::SafeSubset),
            "none" => Ok(HtmlPolicy::Escaped),
            other => Err(bad(format!("html_policy_ceiling {other:?} is not a valid tier; expected one of none, safe-subset, trusted-raw"))),
        },
        _ => Err(bad("html_policy_ceiling must be one of none, safe-subset, trusted-raw".to_string())),
    }
}

/// What a nested board takes from its parent or refuses: a board that leaves `html_policy` unset
/// takes the parent's (Python's `parent_context`: the root's own, else the default every body
/// builds to; a tab's boards take the tabs' parent's), every policy is held to `ceiling` (Python's
/// `cap_html_policy`), and `card_gap: true` is Python's error.
pub fn fill_nested(patch: &mut Patch, ceiling: HtmlPolicy) -> Result<(), FillError> {
    let root = patch.body.html_policy.map(|p| cap(p, ceiling));
    patch.body.html_policy = root;
    patch
        .body
        .variables
        .values_mut()
        .for_each(|v| settle_input!(v));
    match patch.body.layout.as_mut() {
        Some(layout) => inherit(layout, root, ceiling),
        None => Ok(()),
    }
}

fn cap(p: HtmlPolicy, ceiling: HtmlPolicy) -> HtmlPolicy {
    if rank(p) <= rank(ceiling) {
        p
    } else {
        ceiling
    }
}

fn inherit(
    layout: &mut LayoutAuthored,
    parent: Option<HtmlPolicy>,
    ceiling: HtmlPolicy,
) -> Result<(), FillError> {
    let mut item = |item: &mut LayoutItemAuthored| -> Result<(), FillError> {
        let LayoutItemAuthored::Board(nested) = item else {
            return Ok(());
        };
        if nested.body.card_gap == Some(true) {
            return Err(FillError::NestedCardGap);
        }
        let own = nested.body.html_policy.or(parent);
        nested.body.html_policy = own.map(|p| cap(p, ceiling));
        nested
            .body
            .variables
            .values_mut()
            .for_each(|v| settle_input!(v));
        match nested.body.layout.as_mut() {
            Some(inner) => inherit(inner, nested.body.html_policy, ceiling),
            None => Ok(()),
        }
    };
    match layout {
        LayoutAuthored::Rows(items) | LayoutAuthored::Cols(items) => {
            items.iter_mut().try_for_each(&mut item)
        }
        LayoutAuthored::Grid(grid) => grid
            .items
            .iter_mut()
            .flatten()
            .filter_map(|cell| cell.item.as_mut())
            .try_for_each(&mut item),
        LayoutAuthored::Tabs(tabs) => tabs
            .items
            .iter_mut()
            .flatten()
            .filter_map(|tab| tab.layout.as_mut())
            .try_for_each(|inner| inherit(inner, parent, ceiling)),
    }
}
