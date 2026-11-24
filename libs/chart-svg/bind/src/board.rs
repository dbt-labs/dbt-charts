//! The bind entry over board-model (spec § 5.1): the drawn set, then each drawn chart.
//!
//! `bind(&Board, &ChartStyles, &RunResult)` is the spine entry. `bind_chart` binds one chart
//! at one placement: its error card, else its family's bind under `families/`, in the total
//! style of the body that places it. `placement` builds the `Placed` the family's `bind`
//! derives from, after its own refusals. `placed_as` finds one chart's placement by the key a
//! host's layout walk gives it, reading only the items that place that chart.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

use board_model::board::{items, Board, BoardBody, Layout, LayoutItem, SingleRowBoolProbe, NestedBoard, Visible};
use board_model::chart::Chart;
use board_model::ids::{BoardId, ChartId, ItemId, QueryId};
use board_model::variable_value::{Atom, VariableValue};
use board_model::style::style::Style;
use diagnostic::{codes, diag, refuse, Diagnostic};
use model_core::Resolved;
use indexmap::IndexMap;

use crate::callout::bind_callout;
use crate::category_colors::CategoryColorScale;
use crate::derived::{Placed, StylePatch};
use crate::families;
use crate::kpi::bind_kpi;
use crate::geoshape::bind_geoshape;
use crate::pie::bind_pie;
use crate::table::bind_table;
use crate::bound::{self, DrawnSet, Failure, Family};
use crate::run::RunResult;

/// The styles normalize resolved for each placement of a chart, keyed by position as
/// [`bound::Board::charts`] and the engine's slots are.
///
/// `placed` is a placement's chart style folded over the scope it sits in (`CompiledBoard.placed`):
/// a total, so a consumer that wants the chart's whole family style reads it (a KPI's faces, a
/// callout's style). It is not a specificity tier: a family's `axis_quantitative.X` is folded in
/// at the chart's family slot, so a walk that read it as a tier would rank it above the chart's
/// own `axis.X`, which production ranks above it. A cartesian walk reads the placing scope's
/// style per tier; the chart's own tier alone is `own`.
#[derive(Debug, Default)]
pub struct ChartStyles {
    pub placed: BTreeMap<ItemId, Style>,
    /// The scope a placement sits in, as normalize resolved it: the root's style, or the innermost
    /// nested board's or tab's that holds the position. A walk applies tier overlays over it.
    pub scope: BTreeMap<ItemId, Arc<Resolved<Style>>>,
    /// Each chart's `Chart.id` (`Structure.index.public_ids`): the name production writes in its
    /// errors, which for an inline chart is not its position.
    pub public_ids: BTreeMap<ChartId, String>,
    /// The nested board that declares each chart declared below the root (`Structure.index.chart_boards`
    /// of `declared_below`): the board a chart's authored path starts from. A chart of the root, or one
    /// only placed in a nested board, has no entry.
    pub chart_boards: BTreeMap<ChartId, ItemId>,
    /// The variables each chart reads (`Structure.deps.charts`): the wrapper of a chart stamps
    /// `data-var-<name>` for each so the page can re-render it when one changes.
    pub chart_variables: BTreeMap<ChartId, BTreeSet<String>>,
    /// Each single-ink placement's place in the rhythm of the single-series palette
    /// (`LayoutFacts.chart_facts[..].rhythm_slot`); a placement that paints no one ink has none.
    pub rhythm: BTreeMap<ItemId, u32>,
    /// Each placement's own tier alone (`CompiledBoard.placed_own`): the chart's own `style:` with
    /// its tokens resolved, empty for a chart that authored none. Production reads a chart's fill
    /// from it alone, never from the family or board tier that `placed` folds in.
    pub own: BTreeMap<ItemId, StylePatch>,
    /// Each tabs layout, by the position of the board that owns it (`LayoutFacts.tabs`).
    pub tabs: BTreeMap<ItemId, TabsFacts>,
    /// Each details board, by its position (`LayoutFacts.details`).
    pub details: BTreeMap<ItemId, DetailsFacts>,
    /// Each query of the root board's map (`Structure.policies`), by the name a board's text reads it
    /// under, with the id its result is keyed by in the run.
    pub root_queries: BTreeMap<String, QueryId>,
    /// The query each `queries.<name>` an imported chart's display text reads stands for, by the
    /// chart (`Structure.index.text_queries`): its own file declares it.
    pub text_queries: BTreeMap<ChartId, BTreeMap<String, QueryId>>,
}

/// A tabs layout: the variable that selects a tab, and each tab's slug.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TabsFacts {
    pub variable: String,
    pub slugs: Vec<String>,
}

/// A collapsible board: the variable that holds its state and whether it starts open.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DetailsFacts {
    pub variable: String,
    pub expanded: bool,
}

impl ChartStyles {
    pub fn at(&self, at: &ItemId) -> Result<&Style, Diagnostic> {
        self.placed.get(at).ok_or_else(|| diag::err_internal(format!("no resolved chart style for the placement {at}")))
    }

    pub fn scope_at(&self, at: &ItemId) -> Result<&Resolved<Style>, Diagnostic> {
        self.scope.get(at).map(|scope| &**scope).ok_or_else(|| diag::err_internal(format!("no resolved scope for the placement {at}")))
    }

    pub fn own_at(&self, at: &ItemId) -> Result<&StylePatch, Diagnostic> {
        self.own.get(at).ok_or_else(|| diag::err_internal(format!("no own chart style for the placement {at}")))
    }

    /// The id production names the chart `id` by (`Chart.id`); an inline chart's is not its position.
    pub fn public_id(&self, id: &ChartId) -> Result<&str, Diagnostic> {
        self.public_ids.get(id).map(String::as_str).ok_or_else(|| diag::err_internal(format!("no public id for the chart {id}")))
    }
}

/// What `run` draws of `board`: its hidden items, each tabs layout's open pane and each details
/// section's state, decided once for every stage that reads them.
pub fn drawn_set(board: &Board, styles: &ChartStyles, run: &RunResult) -> Result<DrawnSet, Diagnostic> {
    let mut state = State::default();
    hide(&board.body, &ItemId::root(), styles, run, &mut state)?;
    Ok(DrawnSet::new(state.hidden, state.active_tab, state.details_open))
}

/// Binds a normalized board to one run of its queries. A chart that fails becomes its
/// error card; only a board-level error is an `Err`.
pub fn bind(board: &Board, styles: &ChartStyles, run: &RunResult) -> Result<bound::Board, Diagnostic> {
    if board.chart_focus.is_some() {
        return Err(Diagnostic::refused(refuse!("binding a board with `chart_focus` is not supported")));
    }
    let drawn = drawn_set(board, styles, run)?;
    let scales = crate::plan_colors(board, |query| run.datasets.get(query))?;
    let internal = |invalid| diag::err_internal(format!("bound board is invalid: {invalid:?}"));
    let mut charts = IndexMap::new();
    let mut diagnostics = Vec::new();
    for (at, id) in drawn.charts(board).map_err(internal)? {
        // A chart the engine refuses to draw is that chart's failure, as production's error card
        // is: the rest of the board still binds.
        let (mut bound, engine_refused) = match bind_chart(board, styles, &at, id, run, &scales) {
            Err(refusal) if refusal.code == codes::ERR_ENGINE_REFUSED.id => (refused(styles, id, refusal)?, true),
            bound => (bound?, false),
        };
        if bound.family.is_ok() {
            let chart = chart_named(board, id)?;
            let dataset = crate::query_id(chart)?.map(|query| run.dataset(query, id)).transpose()?;
            diagnostics.extend(crate::checks::of(styles.public_id(id)?, chart, dataset));
        } else {
            // A failed chart draws its authored title above the card (`_render_stage_title`), the
            // one the engine refuses to draw included. A title it cannot draw is that chart's
            // refusal as well, unless the chart is already refused: that refusal stands.
            match failed_title(board, styles, &at, id, run) {
                Ok(title) => bound.title = title,
                Err(refusal) if refusal.code == codes::ERR_ENGINE_REFUSED.id && !engine_refused => bound = refused(styles, id, refusal)?,
                Err(refusal) if refusal.code == codes::ERR_ENGINE_REFUSED.id => {}
                Err(other) => return Err(other),
            }
        }
        match &bound.family {
            Ok(Family::Cartesian(bound::Cartesian::Plane(plot))) => diagnostics.extend_from_slice(plot.diagnostics()),
            Ok(Family::Table(table)) if table.empty.is_some() => diagnostics.push(diag::warn_query_returned_zero_rows(styles.public_id(id)?)),
            Ok(Family::Pie(pie)) if pie.empty.is_some() => diagnostics.push(diag::warn_query_returned_zero_rows(styles.public_id(id)?)),
            Ok(Family::Pie(pie)) => {
                if let Some(excess) = &pie.excess_categories {
                    diagnostics.push(diag::warn_too_many_color_categories(styles.public_id(id)?, excess.field.as_str(), excess.count, excess.max_categories));
                }
            }
            Ok(Family::Cartesian(bound::Cartesian::NoData { .. })) => diagnostics.push(diag::warn_query_returned_zero_rows(styles.public_id(id)?)),
            Ok(Family::SparkBar(spark)) if spark.rows.is_empty() && spark.more == 0 => diagnostics.push(diag::warn_query_returned_zero_rows(styles.public_id(id)?)),
            Ok(Family::Kpi(kpi)) => {
                // A KPI over no rows draws its empty state and still says its query returned none.
                let no_rows = crate::query_id(chart_named(board, id)?)?.map(|query| run.dataset(query, id)).transpose()?.is_some_and(|dataset| dataset.rows.is_empty());
                if no_rows {
                    diagnostics.push(diag::warn_query_returned_zero_rows(styles.public_id(id)?));
                }
                if let Some((field, problem)) = &kpi.hinge_mismatch {
                    diagnostics.push(diag::warn_gradient_hinge_mismatch(styles.public_id(id)?, field.as_str(), problem.as_str()));
                }
            }
            _ => {}
        }
        charts.insert(at, bound);
    }
    bound::Board::new(board, drawn, charts, diagnostics).map_err(internal)
}

/// A chart the engine refuses to draw is that chart's failure, as production's error card is.
fn refused(styles: &ChartStyles, id: &ChartId, refusal: Diagnostic) -> Result<bound::Chart, Diagnostic> {
    Ok(bound::Chart { title: None, subtitle: None, frame: None, family: Err(refusal_failure(styles, id, refusal)?) })
}

/// The failure of the chart `id` the engine refused: the refusal, named by the chart and its authored path.
pub fn refusal_failure(styles: &ChartStyles, id: &ChartId, refusal: Diagnostic) -> Result<Failure, Diagnostic> {
    let diagnostic = naming(styles, id, refusal)?.with_sink_param("source_path", serde_json::json!(format!("charts.{id}")));
    Ok(Failure { chart: id.clone(), diagnostic })
}

/// The refusal of the chart `id`, naming it by its public id as production's error names the chart.
pub fn naming(styles: &ChartStyles, id: &ChartId, refusal: Diagnostic) -> Result<Diagnostic, Diagnostic> {
    Ok(refusal.with_sink_param("chart_id", styles.public_id(id)?.into()))
}

/// The authored title a failed chart draws above its card, and the refusal of one whose face is not walked.
fn failed_title(board: &Board, styles: &ChartStyles, at: &ItemId, id: &ChartId, run: &RunResult) -> Result<Option<String>, Diagnostic> {
    let chart = chart_named(board, id)?;
    let title = crate::failed::title(chart, &run.variables)?;
    if title.is_some() {
        let placed = placement(board, styles, at, chart)?;
        crate::failed::refuse_title_style(chart, placed.own, &placed.scope().charts)?;
    }
    Ok(title)
}

#[derive(Default)]
struct State {
    hidden: BTreeSet<ItemId>,
    active_tab: BTreeMap<BoardId, usize>,
    details_open: BTreeMap<BoardId, bool>,
}

fn fact<'a, T>(facts: &'a BTreeMap<ItemId, T>, at: &ItemId, what: &str) -> Result<&'a T, Diagnostic> {
    facts.get(at).ok_or_else(|| diag::err_internal(format!("no {what} facts for the board at {at}")))
}

/// `is_details_expanded`: the live variable's `str(value).lower() == "true"`, else the authored default.
fn details_open(facts: &DetailsFacts, run: &RunResult) -> bool {
    match run.variables.get(&facts.variable) {
        None => facts.expanded,
        Some(VariableValue::One(Atom::Bool(open))) => *open,
        Some(VariableValue::One(Atom::Text(text))) => text.eq_ignore_ascii_case("true"),
        Some(_) => false,
    }
}

/// Which tabs of `tabs` draw, and the open one: the tab the variable names, else the first drawn
/// tab, and none when no tab draws. A slug naming no tab, or a hidden tab other than the first, is
/// an error.
fn tab_states(tabs: &IndexMap<String, NestedBoard>, facts: &TabsFacts, styles: &ChartStyles, run: &RunResult) -> Result<(Vec<bool>, Option<usize>), Diagnostic> {
    let shown = tabs.values().map(|tab| drawn(tab.visible.as_ref(), styles, run)).collect::<Result<Vec<_>, _>>()?;
    let selected = match run.variables.get(&facts.variable) {
        None | Some(VariableValue::Unset) => 0,
        Some(VariableValue::One(Atom::Text(slug))) if slug.is_empty() => 0,
        Some(VariableValue::One(Atom::Text(slug))) => facts
            .slugs
            .iter()
            .position(|known| known == slug)
            .ok_or_else(|| diag::err_tab_unknown(facts.variable.as_str(), slug.as_str(), serde_json::json!(facts.slugs)))?,
        Some(_) => return Err(Diagnostic::refused(refuse!("a tab variable that is not text is not supported"))),
    };
    let last = shown.len().saturating_sub(1);
    let selected = selected.min(last);
    let kept: Vec<&String> = facts.slugs.iter().zip(&shown).filter(|(_, drawn)| **drawn).map(|(slug, _)| slug).collect();
    if shown.get(selected) == Some(&false) && selected != 0 && !kept.is_empty() {
        return Err(diag::err_tab_unknown(facts.variable.as_str(), facts.slugs[selected].as_str(), serde_json::json!(kept)));
    }
    let open = if shown.get(selected) == Some(&true) { Some(selected) } else { shown.iter().position(|drawn| *drawn) };
    Ok((shown, open))
}

/// `eval_literal_condition` (`dbt_charts/core/compile/conditions.py`): an expression with no `{{` is
/// wrapped in one, rendered over the variables and read through `coerce_bool`'s string arm.
fn expression_drawn(expression: &str, run: &RunResult) -> Result<bool, Diagnostic> {
    let source = if expression.contains("{{") { expression.to_string() } else { format!("{{{{ {expression} }}}}") };
    // Production renders a condition strictly: a name no variable declares raises, where board text prints it empty.
    let info = template::parse(&source, template::environment::Kind::Text).map_err(|errors| {
        let message = errors.first().map_or_else(String::new, |e| e.message.clone());
        Diagnostic::refused(refuse!("a `visible:` expression that does not parse is not supported: {message}"))
    })?;
    if let Some(name) = info.variables.iter().find(|name| !run.variables.contains_key(*name)) {
        return Err(diag::err_unknown_variable("layout item visible", expression, name.as_str()));
    }
    let text = crate::display_text::render_text(&source, &run.variables, &IndexMap::new(), &IndexMap::new())?;
    match text.trim().to_lowercase().as_str() {
        "true" | "1" | "yes" => Ok(true),
        "false" | "0" | "no" => Ok(false),
        _ => Err(Diagnostic::refused(refuse!("a `visible:` expression that does not read as true, false, 1, 0, yes or no is not supported"))),
    }
}

/// Whether an item whose `visible:` is `visible` draws.
fn drawn(visible: Option<&Visible>, styles: &ChartStyles, run: &RunResult) -> Result<bool, Diagnostic> {
    match visible {
        None | Some(Visible::Flag(true)) => Ok(true),
        Some(Visible::Flag(false)) => Ok(false),
        Some(Visible::Expression(expression)) => expression_drawn(expression, run),
        Some(Visible::Probe(probe)) => probe_drawn(probe, styles, run),
    }
}

/// `eval_bool_condition`'s probe arm (`dbt_charts/core/render/conditions.py`): the query's one row,
/// its `column` read through `coerce_bool`. Production raises a `ValueError` on any other shape,
/// which fails the board as `ERR-INTERNAL` with that text.
fn probe_drawn(probe: &SingleRowBoolProbe, styles: &ChartStyles, run: &RunResult) -> Result<bool, Diagnostic> {
    let (name, column) = (&probe.query, &probe.column);
    let query = styles
        .root_queries
        .get(name)
        .ok_or_else(|| Diagnostic::refused(refuse!("a query probe `visible:` reading a query the board does not define is not supported")))?;
    let dataset = run.datasets.get(query).ok_or_else(|| diag::err_engine_needs_data(query.to_string()))?;
    let failed = |message: String| diag::err_internal(format!("ValueError: layout item visible query '{name}' {message}"));
    let row = match dataset.rows.as_slice() {
        [row] => row,
        [] => return Err(failed("returned no rows; expected exactly 1".to_string())),
        rows => return Err(failed(format!("returned {} rows; expected exactly 1", rows.len()))),
    };
    let cell = row.get(column).ok_or_else(|| failed(format!("has no column '{column}'; available: {:?}", row.keys().collect::<Vec<_>>())))?;
    match cell {
        serde_json::Value::Bool(drawn) => Ok(*drawn),
        serde_json::Value::Number(n) if n.as_f64() == Some(1.0) => Ok(true),
        serde_json::Value::Number(n) if n.as_f64() == Some(0.0) => Ok(false),
        serde_json::Value::String(text) => match text.trim().to_lowercase().as_str() {
            "true" | "1" | "yes" => Ok(true),
            "false" | "0" | "no" => Ok(false),
            _ => Err(diag::err_internal(format!("ValueError: Cannot coerce '{text}' to bool; expected true/false/1/0/yes/no"))),
        },
        other => Err(diag::err_internal(format!("ValueError: Cannot coerce {other} to bool; expected true/false/1/0/yes/no"))),
    }
}

/// The chart placed as `key` and where it sits: by the chart's public id, `id` for its first drawn
/// placement and `id#2` for its second, as a host's layout walk keys the charts it draws. Charts in
/// two boards may share a public id (`row0` in two tab panes): their drawn placements count as one
/// series, in layout order. Only the
/// items that place that chart are read for whether they draw, so a `visible:`, tab or details
/// state elsewhere on the board does not reach it. A board focused by `chart_focus` draws the
/// focused chart alone, as production's `focus_on_chart` lays it out: from its placement in the
/// root's own layout, whose scope it then sits in.
pub fn placed_as(board: &Board, styles: &ChartStyles, run: &RunResult, key: &str) -> Result<(ItemId, ChartId), Diagnostic> {
    let (public, nth) = match key.rsplit_once('#') {
        Some((public, n)) => (public, n.parse::<usize>().map_err(|_| diag::err_internal(format!("the placement key {key} has no count")))?),
        None => (key, 1),
    };
    let charts: Vec<ChartId> = styles
        .public_ids
        .iter()
        .filter(|(_, id)| id.as_str() == public)
        .map(|(chart, _)| chart.clone())
        .collect();
    if charts.is_empty() {
        return Err(diag::err_internal(format!("the board has no chart {public}")));
    }
    let unplaced = || diag::err_internal(format!("the board draws no chart placed as {key}"));
    if let Some(focus) = &board.chart_focus {
        if !charts.contains(focus) || nth != 1 {
            return Err(unplaced());
        }
        let at = items(&board.body, &ItemId::root())
            .find(|placement| placement.pane.is_empty() && matches!(placement.item, LayoutItem::Chart { id, .. } if id == focus))
            .map(|placement| placement.id)
            .ok_or_else(|| Diagnostic::refused(refuse!("a `chart_focus` on a chart the root's own layout does not place is not supported")))?;
        return Ok((at, focus.clone()));
    }
    let mut seen = 0;
    nth_drawn(&board.body, &ItemId::root(), styles, run, &charts, nth, &mut seen)?.ok_or_else(unplaced)
}

/// Whether `body` or a board nested in it places one of `charts`, in any tab.
fn places(body: &BoardBody, at: &ItemId, charts: &[ChartId]) -> bool {
    items(body, at).any(|placement| match placement.item {
        LayoutItem::Chart { id, .. } => charts.contains(id),
        LayoutItem::Board(nested) => places(&nested.body, &placement.id, charts),
    })
}

/// The `nth` drawn placement of one of `charts` under `body`, and the chart it places, counting on
/// from `seen`: `hide`'s reading of details, tabs and `visible:`, made only where they are placed.
fn nth_drawn(body: &BoardBody, at: &ItemId, styles: &ChartStyles, run: &RunResult, charts: &[ChartId], nth: usize, seen: &mut usize) -> Result<Option<(ItemId, ChartId)>, Diagnostic> {
    if !places(body, at, charts) {
        return Ok(None);
    }
    if body.details.is_some() && !details_open(fact(&styles.details, at, "details")?, run) {
        return Ok(None);
    }
    let open_tab = match &body.layout {
        Layout::Tabs(tabs) => Some(tab_states(tabs, fact(&styles.tabs, at, "tabs")?, styles, run)?.1),
        _ => None,
    };
    for placement in items(body, at) {
        if let (Some(open), [tab]) = (open_tab, placement.pane.as_slice()) {
            if open != Some(*tab) {
                continue;
            }
        }
        match placement.item {
            LayoutItem::Chart { id, visible } if charts.contains(id) && drawn(visible.as_ref(), styles, run)? => {
                *seen += 1;
                if *seen == nth {
                    return Ok(Some((placement.id, id.clone())));
                }
            }
            LayoutItem::Chart { .. } => {}
            LayoutItem::Board(nested) => {
                if places(&nested.body, &placement.id, charts) && drawn(nested.visible.as_ref(), styles, run)? {
                    if let Some(found) = nth_drawn(&nested.body, &placement.id, styles, run, charts, nth, seen)? {
                        return Ok(Some(found));
                    }
                }
            }
        }
    }
    Ok(None)
}

/// Every item under `body` whose `visible:` is false, the open tab of each tabs layout and the state
/// of each details board. A hidden board, a closed tab and a collapsed details board are not entered.
fn hide(body: &BoardBody, at: &ItemId, styles: &ChartStyles, run: &RunResult, state: &mut State) -> Result<(), Diagnostic> {
    if body.details.is_some() {
        let open = details_open(fact(&styles.details, at, "details")?, run);
        state.details_open.insert(body.id.clone(), open);
        if !open {
            return Ok(());
        }
    }
    let open_tab = match &body.layout {
        Layout::Tabs(tabs) => {
            let (shown, open) = tab_states(tabs, fact(&styles.tabs, at, "tabs")?, styles, run)?;
            // A hidden tab and each item of its pane, so no walk draws, sizes or hugs them.
            for (index, _) in shown.iter().enumerate().filter(|(_, drawn)| !**drawn) {
                state.hidden.insert(at.key("tabs").index(index));
                state.hidden.extend(items(body, at).filter(|p| p.pane == [index]).map(|p| p.id));
            }
            if let Some(open) = open {
                state.active_tab.insert(body.id.clone(), open);
            }
            Some(open)
        }
        _ => None,
    };
    for placement in items(body, at) {
        if let (Some(open), [tab]) = (open_tab, placement.pane.as_slice()) {
            if open != Some(*tab) {
                continue;
            }
        }
        let visible = match placement.item {
            LayoutItem::Board(nested) => nested.visible.as_ref(),
            LayoutItem::Chart { visible, .. } => visible.as_ref(),
        };
        if !drawn(visible, styles, run)? {
            state.hidden.insert(placement.id);
        } else if let LayoutItem::Board(nested) = placement.item {
            hide(&nested.body, &placement.id, styles, run, state)?;
        }
    }
    Ok(())
}

fn chart_named<'a>(board: &'a Board, id: &ChartId) -> Result<&'a Chart, Diagnostic> {
    board.body.charts.get(id.as_str()).ok_or_else(|| {
        diag::err_unknown_chart(id.as_str(), board.body.charts.keys().map(String::as_str).collect::<Vec<_>>())
    })
}

/// Binds the chart `id` placed at `at` on `board` to `run`: its error card when its query failed, else
/// what its family binds. `scales` is the board-wide category color plan ([`crate::plan_colors`]),
/// from which a chart's `color:` series take their paint.
pub fn bind_chart(
    board: &Board,
    styles: &ChartStyles,
    at: &ItemId,
    id: &ChartId,
    run: &RunResult,
    scales: &[CategoryColorScale],
) -> Result<bound::Chart, Diagnostic> {
    let chart = chart_named(board, id)?;
    if let Some(error) = run.chart_errors.get(id) {
        let family = Err(Failure { chart: id.clone(), diagnostic: error.clone() });
        return Ok(bound::Chart { title: None, subtitle: None, frame: None, family });
    }
    if let Chart::Scatter(scatter) = chart {
        let public = styles.public_id(id)?;
        let query = crate::query_id(chart)?.ok_or_else(|| diag::err_internal("a scatter reaches bind with its query"))?;
        let dataset = run.dataset(query, id)?;
        if let Some(diagnostic) = crate::checks::scatter_invalid(public, scatter, &dataset.schema, &dataset.rows)? {
            let family = Err(Failure { chart: id.clone(), diagnostic });
            return Ok(bound::Chart { title: None, subtitle: None, frame: None, family });
        }
    }
    let bound = match chart {
        Chart::Bar(bar) => {
            let placed = placement(board, styles, at, chart)?;
            families::bar::bind(id, bar, board, run, &placed, scales)
        }
        Chart::Line(line) => {
            let placed = placement(board, styles, at, chart)?;
            families::line::bind(id, line, board, run, &placed)
        }
        Chart::Histogram(histogram) => {
            let placed = placement(board, styles, at, chart)?;
            families::histogram::bind(id, histogram, board, run, &placed)
        }
        Chart::Scatter(scatter) => {
            let placed = placement(board, styles, at, chart)?;
            families::scatter::bind(id, scatter, board, run, &placed, scales)
        }
        Chart::Pie(pie) | Chart::Donut(pie) => {
            let placed = placement(board, styles, at, chart)?;
            let family = bind_pie(id, pie, matches!(chart, Chart::Donut(_)), board, run, &placed, scales)?;
            Ok(bound::Chart { title: None, subtitle: None, frame: None, family })
        }
        Chart::Geoshape(geoshape) | Chart::Map(geoshape) => {
            let placed = placement(board, styles, at, chart)?;
            let family = bind_geoshape(geoshape, run, &placed)?;
            Ok(bound::Chart { title: None, subtitle: None, frame: None, family })
        }
        Chart::Kpi(kpi) => {
            let placed = placement(board, styles, at, chart)?;
            Ok(bound::Chart { title: None, subtitle: None, frame: None, family: bind_kpi(id, kpi, &placed, board, styles, run)? })
        }
        Chart::Table(table) => {
            let placed = placement(board, styles, at, chart)?;
            let subtitle = table.shared.subtitle.as_ref().map(|text| text.as_str().to_string()).filter(|text| !text.is_empty());
            Ok(bound::Chart { title: None, subtitle, frame: None, family: bind_table(id, table, &placed, board, run)? })
        }
        Chart::SparkBar(spark) => Ok(bound::Chart { title: None, subtitle: None, frame: None, family: families::spark_bar::bind(id, spark, &styles.at(at)?.charts.spark_bar, run)? }),
        Chart::Callout(callout) => {
            let placed = placement(board, styles, at, chart)?;
            Ok(bound::Chart { title: None, subtitle: None, frame: None, family: bind_callout(id, callout, &placed, styles, run)? })
        }
        other => Err(Diagnostic::refused(refuse!("binding a {} chart is not supported", family(other)))),
    };
    // A cell its column's declared type does not hold is that chart's error card, as in production.
    match bound {
        Err(error) if error.code == codes::ERR_CHART_CELL_TYPE_MISMATCH.id => {
            let family = Err(Failure { chart: id.clone(), diagnostic: error.with_sink_param("chart_id", serde_json::json!(id.as_str())) });
            Ok(bound::Chart { title: None, subtitle: None, frame: None, family })
        }
        // An axis scale its chart cannot draw is production's compile error, shown as that chart's error card.
        Err(error) if crate::plot::domain::is_scale_error(&error) => {
            let family = Err(Failure { chart: id.clone(), diagnostic: error });
            Ok(bound::Chart { title: None, subtitle: None, frame: None, family })
        }
        other => other,
    }
}

/// `chart` as placed at `at`: the bodies that scope it, its folded style, its rhythm slot and the
/// name production gives it in errors.
pub fn placement<'a>(board: &'a Board, styles: &'a ChartStyles, at: &ItemId, chart: &'a Chart) -> Result<Placed<'a>, Diagnostic> {
    let mut chain = Vec::new();
    let mut id = None;
    if !placing(&board.body, &ItemId::root(), at, &mut chain, &mut id) {
        return Err(diag::err_internal(format!("no chart is placed at {at}")));
    }
    let id = id.ok_or_else(|| diag::err_internal(format!("the chart placed at {at} has no id")))?;
    Ok(Placed {
        chart,
        chain,
        style: styles.at(at)?,
        own: styles.own_at(at)?,
        scope: styles.scope_at(at)?,
        rhythm_slot: styles.rhythm.get(at).copied(),
        public_id: styles.public_id(id)?,
    })
}

/// Fills `chain` with the bodies from `body` (at `here`) down to the one whose own layout places
/// the chart at `at`, whose style the chart draws with, and `id` with the chart's id; false when
/// none does.
fn placing<'a>(body: &'a BoardBody, here: &ItemId, at: &ItemId, chain: &mut Vec<&'a BoardBody>, id: &mut Option<&'a ChartId>) -> bool {
    chain.push(body);
    let found = items(body, here).any(|placement| match placement.item {
        LayoutItem::Chart { id: chart, .. } => {
            let here = placement.id == *at;
            if here {
                *id = Some(chart);
            }
            here
        }
        LayoutItem::Board(nested) => placing(&nested.body, &placement.id, at, chain, id),
    });
    if !found {
        chain.pop();
    }
    found
}

pub(crate) fn family(chart: &Chart) -> &'static str {
    match chart {
        Chart::Scatter(_) => "scatter",
        Chart::Bar(_) => "bar",
        Chart::Histogram(_) => "histogram",
        Chart::Funnel(_) => "funnel",
        Chart::Line(_) => "line",
        Chart::Area(_) => "area",
        Chart::Callout(_) => "callout",
        Chart::Heatmap(_) => "heatmap",
        Chart::Kpi(_) => "kpi",
        Chart::Pie(_) => "pie",
        Chart::Donut(_) => "donut",
        Chart::SparkBar(_) => "spark_bar",
        Chart::Table(_) => "table",
        Chart::Geoshape(_) => "geoshape",
        Chart::Map(_) => "map",
        Chart::PointMap(_) => "point_map",
        Chart::BubbleMap(_) => "bubble_map",
        Chart::Custom(_) => "custom",
    }
}
