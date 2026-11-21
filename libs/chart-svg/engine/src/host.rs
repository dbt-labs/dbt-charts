//! The one recipe from a `normalize::CompiledBoard` to the engine's inputs: the board, each
//! placement's total family style and the scopes layout reads. The Python host calls it through
//! `_engine.render_board`, and the equivalence chart-side and the engine's tests call it too, so
//! what chart-mode equivalence proves is what production runs.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, OnceLock};

use bind::board::ChartStyles;
use board_model::board::{Board, BoardBody};
use board_model::ids::BoardId;
use board_model::style::style::Style;
use diagnostic::{diag, Diagnostic};
use layout::board::assemble::StylePatch;
use model_core::Resolved;

use bind::run::{RenderEnv, RunResult};
use column_types::Dataset;
use board_model::ids::ChartId;
use board_model::variable_value::VariableValue;
use indexmap::IndexMap;
use serde_json::{json, Value};

use crate::EngineFacts;

/// The cols-row title shift a host gives a [`Cell`].
pub use layout::board::band::TitleShift;

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

/// The total [`Board`] of `compiled`.
pub fn board_of(compiled: &normalize::CompiledBoard) -> Result<Board, Diagnostic> {
    compiled.board().map_err(|e| diag::err_internal(format!("{e:?}")))
}

/// `compiled`'s board and the style scopes layout reads: everything a render needs of the compile
/// but the run's own variables, which are the caller's (`plan`'s).
pub fn prepare(compiled: &normalize::CompiledBoard) -> Result<Prepared, Diagnostic> {
    let board = board_of(compiled)?;
    // `EngineFacts::style` is asked by body, and the compiled scopes are keyed by position.
    let mut nested = BTreeMap::new();
    for (at, id) in &compiled.structure.layout.board_ids {
        let Some(scope) = compiled.nested.get(at.as_str()) else {
            continue;
        };
        let id = BoardId::try_from(id.clone()).map_err(|e| diag::err_internal(format!("{e:?}")))?;
        nested.insert(id, (*scope.inherited).clone());
    }
    let styles = crate::chart_styles(compiled)?;
    Ok((board, styles, compiled.style.clone(), nested))
}

/// What the engine derives from one compile and every call on that compile shares: the total board
/// and the placements' styles, each built on its first use and then handed out as the same value.
/// Pair it with the one [`normalize::CompiledBoard`] it is asked about.
#[derive(Default)]
pub struct Derived {
    board: OnceLock<Result<Arc<Board>, Diagnostic>>,
    styles: OnceLock<Result<Arc<ChartStyles>, Diagnostic>>,
}

impl Derived {
    pub fn board(&self, compiled: &normalize::CompiledBoard) -> Result<Arc<Board>, Diagnostic> {
        self.board.get_or_init(|| board_of(compiled).map(Arc::new)).clone()
    }

    pub fn styles(&self, compiled: &normalize::CompiledBoard) -> Result<Arc<ChartStyles>, Diagnostic> {
        self.styles.get_or_init(|| crate::chart_styles(compiled).map(Arc::new)).clone()
    }
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
            style: (*resolved.inherited).clone(),
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

/// The wire text `text` of `name`, which must be a JSON object.
fn object(name: &str, text: &str) -> Result<serde_json::Map<String, Value>, String> {
    match serde_json::from_str(text).map_err(|e| format!("{name}: {e}"))? {
        Value::Object(map) => Ok(map),
        _ => Err(format!("{name} is not a JSON object")),
    }
}

/// The board's charts by the public id the host names them by; an inline chart's id is its position.
fn by_public(compiled: &normalize::CompiledBoard) -> BTreeMap<String, ChartId> {
    compiled.structure.index.public_ids.iter().map(|(id, public)| (public.clone(), id.clone())).collect()
}

/// `errors` (`{chart id: execute error}`) by the board's own chart ids, each decoded by [`failure`].
fn decoded_errors(by_public: &BTreeMap<String, ChartId>, errors: &serde_json::Map<String, Value>) -> Result<IndexMap<ChartId, Diagnostic>, String> {
    errors
        .iter()
        .map(|(chart, error)| {
            let id = by_public.get(chart).ok_or_else(|| format!("chart_errors: the board has no chart {chart}"))?;
            Ok((id.clone(), failure(error, chart)?))
        })
        .collect()
}

/// A run from the host's wire texts: `datasets` is `{plan key: {rows, schema}}` (the keys of the
/// plan's `queries`, which name `compiled`'s queries in `board`), `chart_errors`
/// `{chart id: execute error}` (see [`failure`]), `empty_filters` `{chart id: [[label, text], ...]}`,
/// the filters that reach each chart that can draw empty, `automatic_links` `{chart id: link template}`, the link the host synthesized for each chart at run time (`RunResult::automatic_links`), `variables` `{name: VariableValue::to_json}` as
/// `plan` returned them, `presets` `plan`'s `variables.presets` (`{name: preset id}`) and `overridden` the names the caller set, `link_context` the host's `{root, current_board_slug, origin}` when it serves boards. A text that is not that is the
/// host's bug, reported as `ERR-INTERNAL` naming it.
pub fn run_result(
    compiled: &normalize::CompiledBoard,
    board: &Board,
    datasets: &str,
    chart_errors: &str,
    empty_filters: &str,
    automatic_links: &str,
    variables: &str,
    presets: &str,
    overridden: Vec<String>,
    rendered_at: &str,
    link_context: Option<&str>,
) -> Result<RunResult, String> {
    let mut decoded = IndexMap::new();
    for (id, dataset) in object("datasets", datasets)? {
        decoded.insert(id.clone(), bind::run::dataset_of(dataset).map_err(|e| format!("dataset {id}: {e}"))?);
    }
    run_result_of(compiled, board, decoded, chart_errors, empty_filters, automatic_links, variables, presets, overridden, rendered_at, link_context)
}

/// [`run_result`] over datasets already decoded, `{plan key: Dataset}`: the host that holds a query's
/// rows as a [`Dataset`] hands them over without writing them as text.
pub fn run_result_of(
    compiled: &normalize::CompiledBoard,
    board: &Board,
    datasets: IndexMap<String, Dataset>,
    chart_errors: &str,
    empty_filters: &str,
    automatic_links: &str,
    variables: &str,
    presets: &str,
    overridden: Vec<String>,
    rendered_at: &str,
    link_context: Option<&str>,
) -> Result<RunResult, String> {
    let mut run_datasets = IndexMap::new();
    for (id, dataset) in datasets {
        if !board.body.queries.contains_key(&id) {
            return Err(format!("dataset {id}: the board has no query with that plan key"));
        }
        let query = normalize::sql::query_id(&id, &compiled.structure.file).map_err(|e| format!("dataset {id}: {e:?}"))?;
        run_datasets.insert(query, dataset);
    }
    let run_errors = decoded_errors(&by_public(compiled), &object("chart_errors", chart_errors)?)?;
    let mut run_filters = IndexMap::new();
    for (chart, filters) in object("empty_filters", empty_filters)? {
        let pairs: Vec<(String, String)> = serde_json::from_value(filters).map_err(|e| format!("empty_filters: chart {chart}: {e}"))?;
        run_filters.insert(chart, pairs);
    }
    let mut run_links = IndexMap::new();
    for (chart, link) in object("automatic_links", automatic_links)? {
        let Value::String(link) = link else {
            return Err(format!("automatic_links: chart {chart}: the link is not a text"));
        };
        run_links.insert(chart, link);
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
        empty_filters: run_filters,
        automatic_links: run_links,
        geo: Default::default(),
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

/// The cell a host draws one chart in, as production's `render_native` takes it. The host is the
/// layout authority: its sizing decides the box, the padding and the title shift, so those laws live
/// in one place and the engine never applies them. The chart draws at the larger of the cell's height
/// and its own natural one, and [`InCell::height`] is that height, which the host's sizing reads; a
/// cell refuses no height.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Cell {
    /// The width inside the padding.
    pub width: f64,
    /// The height inside the padding; `None` is the family's own (a KPI's `default_height`, a callout's
    /// content height, a pie's natural one).
    pub height: Option<f64>,
    /// `render_native`'s padding (left, right, top, bottom): the padding the slot was shrunk by, which
    /// a card's background reaches back out over.
    pub inset: (f64, f64, f64, f64),
    /// The cols-row title shift the host's sizing gave the placement (`LayoutItem.title_shift`).
    pub title_shift: TitleShift,
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

/// The drawn chart's project images, by the reference its text writes, each as the `data:` URI its
/// host read from the project (`image_assets.resolve_image_ref`): the engine reads no files.
pub type Images = BTreeMap<String, String>;

/// Draws the chart placed as `placement` in `cell`, through the code the board's own render draws it
/// with (the chart's own bind, the family's plan and draw), binding no other chart and without laying
/// the board out: a refusal that does not involve the chart, a `chart_focus`, and a `visible:` or tab
/// state elsewhere on the board do not stop it. The card's chrome (border, shadow,
/// selection boxes) is the host's, so it is not drawn here. A chart the engine refuses is that refusal;
/// a family not drawn in a cell yet, and a chart that failed to run, are refused by name.
pub fn render_chart_in_cell(board: &Board, styles: &ChartStyles, run: &RunResult, book: &measure::shaped::FontBook, placement: &str, cell: Cell, images: &Images) -> Result<InCell, Diagnostic> {
    crate::scope::of(board.regional.as_ref())?.within(|| {
        let (at, chart_id) = bind::board::placed_as(board, styles, run, placement)?;
        // The board's color plan the family binds with.
        let scales = match board.body.charts.get(chart_id.as_str()) {
            // A KPI, a callout and a spark_bar paint no category color, nor does a base map, which fills every region alike.
            Some(board_model::chart::Chart::Kpi(_) | board_model::chart::Chart::Callout(_) | board_model::chart::Chart::SparkBar(_) | board_model::chart::Chart::Geoshape(_) | board_model::chart::Chart::Map(_)) => Vec::new(),
            // A pie takes its slices' colors from the board-wide category plan, as the board's own bind does.
            Some(board_model::chart::Chart::Pie(_) | board_model::chart::Chart::Donut(_)) => bind::plan_colors(board, |query| run.datasets.get(query))?,
            _ => return Err(Diagnostic::refused(diagnostic::refuse!("drawing this chart family in a host's cell is not supported"))),
        };
        let chart = match bind::board::bind_chart(board, styles, &at, &chart_id, run, &scales) {
            Err(refusal) if refusal.code == diagnostic::codes::ERR_ENGINE_REFUSED.id => return Err(bind::board::naming(styles, &chart_id, refusal)?),
            bound => bound?,
        };
        match &chart.family {
            Err(_) if run.chart_errors.contains_key(&chart_id) => Err(Diagnostic::refused(diagnostic::refuse!("a chart that failed to run, drawn as its error card in a host's cell, is not supported"))),
            // A chart its rows fail (a KPI's second row, a missing value column) is that error,
            // which the host draws as the chart's error card.
            Err(failure) => Err(failure.diagnostic.clone()),
            // Production assigns a KPI no title shift (`title_band.py::chart_title_metrics`).
            Ok(bind::bound::Family::Kpi(_)) if cell.title_shift != TitleShift::default() => {
                Err(diag::err_internal(format!("the KPI {chart_id} is given a title shift, and a KPI takes no title shift")))
            }
            Ok(bind::bound::Family::Kpi(kpi)) => {
                // A chart's notes ride on the host's wrapper, not on the card drawn here.
                let body = crate::slot::body_placing(board, &at)?;
                let placed = styles.at(&at)?;
                let height = cell.height.unwrap_or(placed.charts.kpi.default_height);
                let id = styles.public_id(&chart_id)?;
                let (extent, content, diagnostics) = crate::compose::kpi_card(placed, body, kpi, id, cell.inset, (cell.width, height), book)?;
                let svg = scene::Node::new(scene::Role::Chrome, scene::Attrs::new(), scene::Shape::Svg { extent, children: content });
                Ok(InCell { svg: print::print(std::slice::from_ref(&svg)), height: extent.height, diagnostics })
            }
            // A callout is untitled for the band (`title_band.py::chart_title_metrics`), and the
            // host pads its card from outside, so the card ignores the cell's inset.
            Ok(bind::bound::Family::Callout(_)) if cell.title_shift != TitleShift::default() => {
                Err(diag::err_internal(format!("the callout {chart_id} is given a title shift, and a callout takes no title shift")))
            }
            Ok(bind::bound::Family::Callout(callout)) => {
                let body = crate::slot::body_placing(board, &at)?;
                let id = styles.public_id(&chart_id)?;
                // No cell height is the card's own: its content's.
                let (extent, content, diagnostics) = crate::compose::callout_card(styles.at(&at)?, body, callout, id, images, None, (cell.width, cell.height.unwrap_or(0.0)), book)?;
                let svg = scene::Node::new(scene::Role::Chrome, scene::Attrs::new(), scene::Shape::Svg { extent, children: content });
                Ok(InCell { svg: print::print(std::slice::from_ref(&svg)), height: extent.height, diagnostics })
            }
            // The host pads a spark_bar's card from outside (`SVG_LAYOUT_PADDED_TYPES`), so the card
            // draws in the cell; its title's face is the one the whole slot's width gives it.
            Ok(bind::bound::Family::SparkBar(spark)) => {
                let body = crate::slot::body_placing(board, &at)?;
                let placed = styles.at(&at)?;
                let id = styles.public_id(&chart_id)?;
                let slot_width = cell.width + cell.inset.0 + cell.inset.1;
                let heading = layout::families::spark_bar::Heading::of(spark.title.as_deref(), spark.subtitle.as_deref(), placed, slot_width, cell.title_shift)?;
                let (extent, content, diagnostics) = crate::compose::spark_bar_card(placed, body, spark, &heading, id, (cell.width, cell.height))?;
                let svg = scene::Node::new(scene::Role::Chrome, scene::Attrs::new(), scene::Shape::Svg { extent, children: content });
                Ok(InCell { svg: print::print(std::slice::from_ref(&svg)), height: extent.height, diagnostics })
            }
            // A pie composes its own canvas at the cell's width and its mode's height.
            Ok(bind::bound::Family::Pie(_)) if cell.title_shift != TitleShift::default() => {
                Err(Diagnostic::refused(diagnostic::refuse!("a pie given a title shift in a host's cell is not supported")))
            }
            Ok(bind::bound::Family::Pie(pie)) => {
                let body = crate::slot::body_placing(board, &at)?;
                let chart = board.body.charts.get(chart_id.as_str()).ok_or_else(|| diag::err_internal(format!("the chart {chart_id} is not on the board")))?;
                let site = crate::slot::ChartSite { body, chart, placed: styles.at(&at)?, id: chart_id.as_str() };
                let heights = crate::slot::Heights::of_total(crate::slot::total_style(body)?);
                let (height, svg, _, diagnostics) = crate::compose::pie_card(&site, &heights, pie, (cell.width, cell.height), cell.title_shift, book)?;
                Ok(InCell { svg: print::print(std::slice::from_ref(&svg)), height, diagnostics })
            }
            Ok(bind::bound::Family::Geoshape(_)) if cell.title_shift != TitleShift::default() => {
                Err(Diagnostic::refused(diagnostic::refuse!("a geoshape given a title shift in a host's cell is not supported")))
            }
            // A map draws at the cell's width and the cell's height, or its aspect ratio's.
            Ok(bind::bound::Family::Geoshape(geo)) => {
                let body = crate::slot::body_placing(board, &at)?;
                let chart = board.body.charts.get(chart_id.as_str()).ok_or_else(|| diag::err_internal(format!("the chart {chart_id} is not on the board")))?;
                let site = crate::slot::ChartSite { body, chart, placed: styles.at(&at)?, id: chart_id.as_str() };
                let heights = crate::slot::Heights::of_total(crate::slot::total_style(body)?);
                let (height, svg, _, diagnostics) = crate::compose::geoshape_card(&site, &heights, geo, (cell.width, cell.height), book)?;
                Ok(InCell { svg: print::print(std::slice::from_ref(&svg)), height, diagnostics })
            }
            Ok(_) => Err(diag::err_internal(format!("the chart {chart_id} bound as another family"))),
        }
    })
}

/// One render's board, styles and run, prepared once for every chart the host draws in a cell: the
/// datasets, variables and presets decode once, and each [`CellBoard::draw`] sets only what is the
/// drawn chart's own.
pub struct CellBoard {
    board: Arc<Board>,
    styles: Arc<ChartStyles>,
    run: RunResult,
    by_public: BTreeMap<String, ChartId>,
    /// `{chart id: execute error}` as the host sent it, decoded per draw: an error of a chart the host
    /// never asks to draw (one whose template fields are incomplete, say) cannot reject another's.
    errors: serde_json::Map<String, Value>,
}

impl CellBoard {
    /// `compiled` prepared by [`prepare`] as `board` and `styles`, with the run's wire texts as
    /// [`run_result`] takes them (no `empty_filters` or `automatic_links`: each draw carries its chart's own). A text that is
    /// not that, or a chart error naming no chart of the board, is the host's bug. `geo` is the geo
    /// sources' wire ([`bind::geo_source::geo_inputs`]): `{}` for a board that draws no map.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        compiled: &normalize::CompiledBoard,
        board: Arc<Board>,
        styles: Arc<ChartStyles>,
        datasets: IndexMap<String, Dataset>,
        chart_errors: &str,
        variables: &str,
        presets: &str,
        overridden: Vec<String>,
        rendered_at: &str,
        link_context: Option<&str>,
        geo: &str,
    ) -> Result<Self, String> {
        let by_public = by_public(compiled);
        let errors = object("chart_errors", chart_errors)?;
        if let Some(chart) = errors.keys().find(|chart| !by_public.contains_key(*chart)) {
            return Err(format!("chart_errors: the board has no chart {chart}"));
        }
        let mut run = run_result_of(compiled, &board, datasets, "{}", "{}", "{}", variables, presets, overridden, rendered_at, link_context)?;
        run.geo = bind::geo_source::geo_inputs(geo)?;
        Ok(Self { board, styles, run, by_public, errors })
    }

    /// [`check_presets`] over the render's run, which every draw shares.
    pub fn check_presets(&self) -> Result<(), Diagnostic> {
        check_presets(&self.board, &self.run)
    }

    /// The chart placed as `placement` drawn in `cell` ([`render_chart_in_cell`]), naming
    /// `empty_filters` when it has nothing to show, and `automatic_link` the link the host synthesized for it. Its own chart error is decoded as the host sent
    /// it, and every other failed chart stands as a placeholder failure, so bind still sees each
    /// sibling as failed (its dataset is missing). The outer `Err` is the host's bug (the drawn chart's
    /// own error is not a registered one); the inner, the engine's diagnostic.
    pub fn draw(
        &mut self,
        book: &measure::shaped::FontBook,
        placement: &str,
        empty_filters: Vec<(String, String)>,
        automatic_link: Option<String>,
        cell: Cell,
        images: &Images,
    ) -> Result<Result<InCell, Diagnostic>, String> {
        let drawn = placement.split_once('#').map_or(placement, |(id, _)| id);
        let errors = self
            .errors
            .iter()
            .map(|(chart, error)| {
                let error = if chart == drawn {
                    error.clone()
                } else {
                    json!({"code": "ERR-INTERNAL", "fields": {"message": format!("chart {chart} failed to run")}})
                };
                (chart.clone(), error)
            })
            .collect();
        self.run.chart_errors = decoded_errors(&self.by_public, &errors)?;
        self.run.empty_filters = IndexMap::from([(drawn.to_string(), empty_filters)]);
        self.run.automatic_links = automatic_link.into_iter().map(|link| (drawn.to_string(), link)).collect();
        Ok(render_chart_in_cell(&self.board, &self.styles, &self.run, book, placement, cell, images))
    }
}
