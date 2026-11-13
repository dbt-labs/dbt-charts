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

/// `chart_errors` for drawing the one chart placed as `placement`: its own entry as the host sent it, and a
/// placeholder failure for every other chart, so bind still sees each sibling as failed (its dataset is
/// missing) while an error of a sibling the host never asks the engine to draw (one whose template fields are
/// incomplete, say) cannot reject this chart's board.
pub fn cell_chart_errors(chart_errors: &str, placement: &str) -> Result<String, String> {
    let drawn = placement.split_once('#').map_or(placement, |(id, _)| id);
    let Value::Object(mut errors) = serde_json::from_str(chart_errors).map_err(|e| format!("chart_errors: {e}"))? else {
        return Err("chart_errors is not a JSON object".to_string());
    };
    for (chart, error) in errors.iter_mut().filter(|(chart, _)| chart.as_str() != drawn) {
        *error = json!({"code": "ERR-INTERNAL", "fields": {"message": format!("chart {chart} failed to run")}});
    }
    Ok(Value::Object(errors).to_string())
}

/// A run from the host's wire texts: `datasets` is `{query id: {rows, schema}}`, `chart_errors`
/// `{chart id: execute error}` (see [`failure`]), `variables` `{name: VariableValue::to_json}` as
/// `plan` returned them, `presets` `plan`'s `variables.presets` (`{name: preset id}`) and `overridden` the names the caller set, `link_context` the host's `{root, current_board_slug, origin}` when it serves boards. A text that is not that is the
/// host's bug, reported as `ERR-INTERNAL` naming it.
pub fn run_result(
    datasets: &str,
    chart_errors: &str,
    variables: &str,
    presets: &str,
    overridden: Vec<String>,
    rendered_at: &str,
    link_context: Option<&str>,
) -> Result<RunResult, String> {
    fn object(name: &str, text: &str) -> Result<serde_json::Map<String, Value>, String> {
        match serde_json::from_str(text).map_err(|e| format!("{name}: {e}"))? {
            Value::Object(map) => Ok(map),
            _ => Err(format!("{name} is not a JSON object")),
        }
    }
    let mut run_datasets = IndexMap::new();
    for (id, dataset) in object("datasets", datasets)? {
        run_datasets.insert(QueryId::try_from(id.clone())?, bind::run::dataset_of(dataset).map_err(|e| format!("dataset {id}: {e}"))?);
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
    let mut run_presets = IndexMap::new();
    for (name, id) in object("presets", presets)? {
        let Value::String(id) = id else {
            return Err(format!("presets: {name} is not a text"));
        };
        run_presets.insert(name, id);
    }
    Ok(RunResult {
        datasets: run_datasets,
        variables: run_variables,
        presets: run_presets,
        overridden: overridden.into_iter().collect::<BTreeSet<_>>(),
        chart_errors: run_errors,
        env: RenderEnv {
            rendered_at: rendered_at
                .parse()
                .map_err(|e| format!("rendered_at: {e}"))?,
            link_context: link_context
                .map(|text| {
                    let context: bind::links::LinkContext = serde_json::from_str(text).map_err(|e| format!("link_context: {e}"))?;
                    context.check().map_err(|e| format!("link_context: {e}"))?;
                    Ok::<_, String>(context)
                })
                .transpose()?,
        },
    })
}

/// `run.presets` against the board's declarations: a name that is not a declared daterange, or an
/// id outside the preset grammar, is the host's bug (`ERR-INTERNAL`).
pub fn check_presets(board: &Board, run: &RunResult) -> Result<(), Diagnostic> {
    fn declared<'a>(body: &'a BoardBody, out: &mut Vec<(&'a str, &'a board_model::variable::Variable)>) {
        out.extend(body.variables.iter().map(|(n, v)| (n.as_str(), v)));
        for placement in board_model::board::items(body, &board_model::ids::ItemId::root()) {
            if let board_model::board::layout::LayoutItem::Board(nested) = placement.item {
                declared(&nested.body, out);
            }
        }
    }
    let mut all = Vec::new();
    declared(&board.body, &mut all);
    normalize::values::check_presets(all, &run.presets).map_err(diag::err_internal)
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

/// The cell a host draws one chart in, as production's `render_native` takes it: the chart's own
/// box inside the padding the host shrank the slot by. The host is the layout authority, so the
/// padding law lives in one place and the engine never applies it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Cell {
    /// The width inside the padding.
    pub width: f64,
    /// The height inside the padding; `None` is the family's own (a KPI's `default_height`).
    pub height: Option<f64>,
    /// The padding (left, right, top, bottom) the slot was shrunk by, which a card's background
    /// reaches back out over.
    pub inset: (f64, f64, f64, f64),
}

/// One chart drawn in its [`Cell`].
#[derive(Debug, Clone, PartialEq)]
pub struct InCell {
    /// The chart's own `<svg>`, at the cell's width and the height it draws to.
    pub svg: String,
    /// The `<svg>`'s own height: the cell's, or the card's natural one when it outgrows it.
    pub height: f64,
    /// The chart's warnings, in production's order.
    pub diagnostics: Vec<Diagnostic>,
}

/// The placement `key` names (`id`, then `id#2` for a second one, as `Report.boxes` is keyed).
fn placed_at(board: &Board, bound: &bind::bound::Board, key: &str) -> Result<(board_model::ids::ItemId, ChartId), Diagnostic> {
    let drawn = bound.drawn().charts(board).map_err(|e| diag::err_internal(format!("bound board is invalid: {e:?}")))?;
    let mut placed: BTreeMap<&str, usize> = BTreeMap::new();
    for (at, id) in drawn {
        let n = placed.entry(id.as_str()).or_default();
        *n += 1;
        if key == if *n == 1 { id.as_str().to_string() } else { format!("{id}#{n}") } {
            return Ok((at, id.clone()));
        }
    }
    Err(diag::err_internal(format!("the board draws no chart placed as {key}")))
}

/// Draws the chart placed as `placement` in `cell`, through the code the board's own render draws it
/// with (bind, the family's plan and draw), without laying the board out or planning its other slots:
/// a refusal that does not involve the chart does not stop it. The card's chrome (border, shadow,
/// selection boxes) is the host's, so it is not drawn here. A chart the engine refuses is that refusal;
/// a family not drawn in a cell yet, and a chart that failed to run, are refused by name.
pub fn render_chart_in_cell(board: &Board, styles: &ChartStyles, run: &RunResult, book: &measure::shaped::FontBook, placement: &str, cell: Cell) -> Result<InCell, Diagnostic> {
    crate::scope::of(board.regional.as_ref())?.within(|| {
        let bound = bind::bind(board, styles, run)?;
        let (at, chart_id) = placed_at(board, &bound, placement)?;
        let chart = bound.charts().get(&at).ok_or_else(|| diag::err_internal(format!("the chart at {at} is unbound")))?;
        match &chart.family {
            Err(failure) if failure.diagnostic.code == diagnostic::codes::ERR_ENGINE_REFUSED.id => Err(failure.diagnostic.clone()),
            Err(_) => Err(Diagnostic::refused(diagnostic::refuse!("a chart that failed to run, drawn as its error card in a host's cell, is not supported"))),
            Ok(bind::bound::Family::Kpi(kpi)) => {
                let body = crate::slot::body_placing(board, &at)?;
                let placed = styles.at(&at)?;
                let height = cell.height.unwrap_or(placed.charts.kpi.default_height);
                let id = styles.public_id(&chart_id)?;
                let (extent, content, diagnostics) = crate::compose::kpi_card(placed, body, kpi, id, cell.inset, (cell.width, height), book)?;
                let svg = scene::Node::new(scene::Role::Chrome, scene::Attrs::new(), scene::Shape::Svg { extent, children: content });
                Ok(InCell { svg: print::print(std::slice::from_ref(&svg)), height: extent.height, diagnostics })
            }
            Ok(_) => Err(Diagnostic::refused(diagnostic::refuse!("drawing this chart family in a host's cell is not supported"))),
        }
    })
}
