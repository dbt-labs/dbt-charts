//! The one recipe from a `normalize::CompiledBoard` to the engine's inputs: the board, each
//! placement's total family style and the scopes layout reads. The Python host calls it through
//! `_engine.render_board`, and the equivalence chart-side and the engine's tests call it too, so
//! what chart-mode equivalence proves is what production runs.

use std::collections::{BTreeMap, BTreeSet};

use bind::board::ChartStyles;
use board_model::board::{Board, BoardBody};
use board_model::ids::BoardId;
use board_model::style::style::Style;
use diagnostic::{diag, Diagnostic};
use layout::board::assemble::StylePatch;
use model_core::Resolved;

use bind::run::{RenderEnv, RunResult};
use board_model::ids::{ChartId, QueryId};
use board_model::variable_value::VariableValue;
use indexmap::IndexMap;
use serde_json::{json, Value};

use crate::EngineFacts;

pub const DOCS_SITE: &str = "https://docs.dbtcharts.com";

/// What layout reads of a compiled board, and each placement's total family style, which bind and
/// the slots read.
pub struct Facts {
    pub root: BoardId,
    /// What layout reads: the root scope's cascade with every leaf set.
    pub style: StylePatch,
    /// What each nested board lays out and draws with, by the board's id.
    pub nested: BTreeMap<BoardId, StylePatch>,
    pub styles: ChartStyles,
    /// The body of the root's `<style>`: empty when the caller draws the page's chrome and CSS.
    pub stylesheet: String,
}

/// The board, its placements' styles, the root's resolved style and each nested board's style by its id.
pub type Prepared = (
    Board,
    ChartStyles,
    Resolved<Style>,
    BTreeMap<BoardId, StylePatch>,
);

/// `compiled`'s board and the style scopes layout reads: everything a render needs of the compile
/// but the run's own variables, which are the caller's (`plan`'s).
pub fn prepare(compiled: &normalize::CompiledBoard) -> Result<Prepared, Diagnostic> {
    let board = compiled
        .board()
        .map_err(|e| diag::err_internal(format!("{e:?}")))?;
    // `EngineFacts::style` is asked by body, and the compiled scopes are keyed by position.
    let mut nested = BTreeMap::new();
    for (at, id) in &compiled.structure.layout.board_ids {
        let Some(scope) = compiled.nested.get(at.as_str()) else {
            continue;
        };
        let id = BoardId::try_from(id.clone()).map_err(|e| diag::err_internal(format!("{e:?}")))?;
        nested.insert(id, *scope.inherited.clone());
    }
    let styles = crate::chart_styles(compiled)?;
    Ok((board, styles, compiled.style.clone(), nested))
}

impl Facts {
    pub fn of(
        board: &Board,
        styles: ChartStyles,
        resolved: &Resolved<Style>,
        nested: BTreeMap<BoardId, StylePatch>,
        stylesheet: String,
    ) -> Self {
        Facts {
            root: board.body.id.clone(),
            style: *resolved.inherited.clone(),
            nested,
            styles,
            stylesheet,
        }
    }
}

impl EngineFacts for Facts {
    fn style(&self, body: &BoardBody) -> Result<&StylePatch, Diagnostic> {
        if body.id == self.root {
            return Ok(&self.style);
        }
        self.nested.get(&body.id).ok_or_else(|| {
            diag::err_internal(format!(
                "no resolved style for the nested board {}",
                body.id
            ))
        })
    }
    fn pane_style(&self, body: &BoardBody, tab: usize) -> Result<&StylePatch, Diagnostic> {
        let pane = BoardId::try_from(format!("{}_tab{tab}", body.id))
            .map_err(|e| diag::err_internal(format!("{e:?}")))?;
        self.nested
            .get(&pane)
            .ok_or_else(|| diag::err_internal(format!("no resolved style for the tab {pane}")))
    }
    fn docs_site(&self) -> &str {
        DOCS_SITE
    }
    fn stylesheet(&self) -> &str {
        &self.stylesheet
    }
}

/// The error card of the chart `chart` that failed with `error` (`code`, `fields`, and
/// `query_name` when a query failed): the registered code and its fields, over the `query_name`
/// and `chart_id` production stamps on it. An error with no `query_name` is a chart-local failure
/// (an unknown font or color token), which has no query to name.
pub fn failure(error: &Value, chart: &str) -> Result<Diagnostic, String> {
    let id = error["code"]
        .as_str()
        .ok_or("an execute error has no code")?;
    let code =
        diagnostic::codes::lookup(id).ok_or_else(|| format!("{id} is not a registered code"))?;
    let fields = error["fields"]
        .as_object()
        .ok_or_else(|| format!("{id}: the error's fields are not an object"))?;
    let template = code.params.iter().map(|k| {
        fields
            .get(*k)
            .map(|v| (*k, v.clone()))
            .ok_or_else(|| format!("{id}: no field {k}"))
    });
    let diagnostic = Diagnostic::new(code, template.collect::<Result<Vec<_>, _>>()?);
    // `query_name` and `chart_id` are stamped below; any other field the code does not register is
    // refused, because a diagnostic's param names are `'static`.
    if let Some(extra) = fields.keys().find(|k| {
        !code.params.contains(&k.as_str()) && !matches!(k.as_str(), "query_name" | "chart_id")
    }) {
        return Err(format!(
            "{id}: the error carries the field {extra}, which the code does not register"
        ));
    }
    let diagnostic = diagnostic.with_sink_param("chart_id", json!(chart));
    match error.get("query_name") {
        None => Ok(diagnostic),
        Some(Value::String(query_name)) => Ok(diagnostic.with_sink_param("query_name", json!(query_name))),
        Some(other) => Err(format!("{id}: query_name is not a string: {other}")),
    }
}

/// A run from the host's wire texts: `datasets` is `{query id: {rows, schema}}`, `chart_errors`
/// `{chart id: execute error}` (see [`failure`]), `variables` `{name: VariableValue::to_json}` as
/// `plan` returned them and `overridden` the names the caller set. A text that is not that is the
/// host's bug, reported as `ERR-INTERNAL` naming it.
pub fn run_result(
    datasets: &str,
    chart_errors: &str,
    variables: &str,
    overridden: Vec<String>,
    rendered_at: &str,
) -> Result<RunResult, String> {
    fn object(name: &str, text: &str) -> Result<serde_json::Map<String, Value>, String> {
        match serde_json::from_str(text).map_err(|e| format!("{name}: {e}"))? {
            Value::Object(map) => Ok(map),
            _ => Err(format!("{name} is not a JSON object")),
        }
    }
    let mut run_datasets = IndexMap::new();
    for (id, dataset) in object("datasets", datasets)? {
        run_datasets.insert(
            QueryId::try_from(id.clone())?,
            serde_json::from_value(dataset).map_err(|e| format!("dataset {id}: {e}"))?,
        );
    }
    let mut run_errors = IndexMap::new();
    for (chart, error) in object("chart_errors", chart_errors)? {
        run_errors.insert(ChartId::try_from(chart.clone())?, failure(&error, &chart)?);
    }
    let mut run_variables = IndexMap::new();
    for (name, wire) in object("variables", variables)? {
        run_variables.insert(
            name.clone(),
            VariableValue::from_json(&wire).map_err(|e| format!("variable {name}: {e}"))?,
        );
    }
    Ok(RunResult {
        datasets: run_datasets,
        variables: run_variables,
        overridden: overridden.into_iter().collect::<BTreeSet<_>>(),
        chart_errors: run_errors,
        env: RenderEnv {
            rendered_at: rendered_at
                .parse()
                .map_err(|e| format!("rendered_at: {e}"))?,
        },
    })
}

/// Each drawn chart's own `<svg>`, printed: the one nested in the chart's `data-chart-id` wrapper,
/// keyed as `Report.boxes` is (`id`, then `id#2` for a second placement). A chart the board draws
/// inline, with no nested `<svg>` (a table, a kpi, an error card), has no entry.
pub fn chart_svgs(nodes: &[scene::Node]) -> Vec<(String, String)> {
    fn nested_svg(node: &scene::Node) -> Option<&scene::Node> {
        if matches!(node.shape, scene::Shape::Svg { .. }) {
            return Some(node);
        }
        node.children().iter().find_map(nested_svg)
    }
    fn visit(node: &scene::Node, placed: &mut BTreeMap<String, usize>, out: &mut Vec<(String, String)>) {
        if let Some(id) = node.attrs.get("data-chart-id") {
            let n = placed.entry(id.to_string()).or_default();
            *n += 1;
            if let Some(svg) = nested_svg(node) {
                out.push((if *n == 1 { id.to_string() } else { format!("{id}#{n}") }, print::print(std::slice::from_ref(svg))));
            }
            return;
        }
        node.children().iter().for_each(|child| visit(child, placed, out));
    }
    let (mut placed, mut out) = (BTreeMap::new(), Vec::new());
    nodes.iter().for_each(|node| visit(node, &mut placed, &mut out));
    out
}
