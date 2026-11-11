//! The bind entry over board-model (spec § 5.1): the drawn set, then each drawn chart.
//!
//! `bind(&Board, &ChartStyles, &RunResult)` is the spine entry. `bind_chart` binds one chart
//! at one placement: its error card, else its family's bind under `families/`, in the total
//! style of the body that places it. `placement` builds the `Placed` the family's `bind`
//! derives from, after its own refusals.

use std::collections::{BTreeMap, BTreeSet};

use board_model::board::{items, Board, BoardBody, Layout, LayoutItem, Visible};
use board_model::chart::Chart;
use board_model::ids::{BoardId, ChartId, ItemId};
use board_model::variable_value::{Atom, VariableValue};
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use model_core::Resolved;
use indexmap::IndexMap;

use crate::callout::bind_callout;
use crate::category_colors::CategoryColorScale;
use crate::derived::{Placed, StylePatch};
use crate::families;
use crate::kpi::bind_kpi;
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
    pub scope: BTreeMap<ItemId, Resolved<Style>>,
    /// Each chart's `Chart.id` (`Structure.index.public_ids`): the name production writes in its
    /// errors, which for an inline chart is not its position.
    pub public_ids: BTreeMap<ChartId, String>,
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
}

/// A tabs layout: the variable that selects a tab, each tab's slug and the one open on load.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TabsFacts {
    pub variable: String,
    pub slugs: Vec<String>,
    pub default_tab: usize,
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
        self.scope.get(at).ok_or_else(|| diag::err_internal(format!("no resolved scope for the placement {at}")))
    }

    pub fn own_at(&self, at: &ItemId) -> Result<&StylePatch, Diagnostic> {
        self.own.get(at).ok_or_else(|| diag::err_internal(format!("no own chart style for the placement {at}")))
    }

    /// The id production names the chart `id` by (`Chart.id`); an inline chart's is not its position.
    pub fn public_id(&self, id: &ChartId) -> Result<&str, Diagnostic> {
        self.public_ids.get(id).map(String::as_str).ok_or_else(|| diag::err_internal(format!("no public id for the chart {id}")))
    }
}

/// Binds a normalized board to one run of its queries. A chart that fails becomes its
/// error card; only a board-level error is an `Err`.
pub fn bind(board: &Board, styles: &ChartStyles, run: &RunResult) -> Result<bound::Board, Diagnostic> {
    if board.chart_focus.is_some() {
        return Err(Diagnostic::refused(refuse!("binding a board with `chart_focus` is not supported")));
    }
    let mut state = State::default();
    hide(&board.body, &ItemId::root(), styles, run, &mut state)?;
    let drawn = DrawnSet::new(state.hidden, state.active_tab, state.details_open);
    let scales = crate::plan_colors(board, |query| run.datasets.get(query))?;
    let internal = |invalid| diag::err_internal(format!("bound board is invalid: {invalid:?}"));
    let mut charts = IndexMap::new();
    let mut diagnostics = Vec::new();
    for (at, id) in drawn.charts(board).map_err(internal)? {
        let bound = bind_chart(board, styles, &at, id, run, &scales)?;
        match &bound.family {
            Ok(Family::Cartesian(bound::Cartesian::Plane(plot))) => diagnostics.extend_from_slice(plot.diagnostics()),
            Ok(Family::Cartesian(bound::Cartesian::NoData(_))) => diagnostics.push(diag::warn_query_returned_zero_rows(styles.public_id(id)?)),
            _ => {}
        }
        charts.insert(at, bound);
    }
    bound::Board::new(board, drawn, charts, diagnostics).map_err(internal)
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

/// `resolve_active_tab_index`: a truthy variable value names the open tab by slug, else the authored
/// default; never past the last tab. The value is runtime input, so one naming no tab is an error.
fn open_tab(facts: &TabsFacts, tabs: usize, run: &RunResult) -> Result<usize, Diagnostic> {
    let active = match run.variables.get(&facts.variable) {
        None | Some(VariableValue::Unset) => facts.default_tab,
        Some(VariableValue::One(Atom::Text(slug))) if slug.is_empty() => facts.default_tab,
        Some(VariableValue::One(Atom::Text(slug))) => facts
            .slugs
            .iter()
            .position(|known| known == slug)
            .ok_or_else(|| diag::err_tab_unknown(facts.variable.as_str(), slug.as_str(), serde_json::json!(facts.slugs)))?,
        Some(_) => return Err(Diagnostic::refused(refuse!("a tab variable that is not text is not supported"))),
    };
    Ok(active.min(tabs.saturating_sub(1)))
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
            let open = open_tab(fact(&styles.tabs, at, "tabs")?, tabs.items.len(), run)?;
            state.active_tab.insert(body.id.clone(), open);
            Some(open)
        }
        _ => None,
    };
    for placement in items(body, at) {
        if let (Some(open), [tab]) = (open_tab, placement.pane.as_slice()) {
            if *tab != open {
                continue;
            }
        }
        let visible = match placement.item {
            LayoutItem::Board(nested) => nested.visible.as_ref(),
            LayoutItem::Chart { visible, .. } => visible.as_ref(),
        };
        let drawn = match visible {
            None | Some(Visible::Flag(true)) => true,
            Some(Visible::Flag(false)) => false,
            Some(Visible::Expression(expression)) => expression_drawn(expression, run)?,
            Some(Visible::Probe(_)) => return Err(Diagnostic::refused(refuse!("a query probe `visible:` is not supported"))),
        };
        if !drawn {
            state.hidden.insert(placement.id);
        } else if let LayoutItem::Board(nested) = placement.item {
            hide(&nested.body, &placement.id, styles, run, state)?;
        }
    }
    Ok(())
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
    let chart = board.body.charts.get(id.as_str()).ok_or_else(|| {
        diag::err_unknown_chart(id.as_str(), board.body.charts.keys().map(String::as_str).collect::<Vec<_>>())
    })?;
    if let Some(error) = run.chart_errors.get(id) {
        let family = Err(Failure { chart: id.clone(), diagnostic: error.clone() });
        return Ok(bound::Chart { title: None, subtitle: None, frame: None, family });
    }
    match chart {
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
            let family = bind_pie(id, pie, matches!(chart, Chart::Donut(_)), board, run, &placed)?;
            Ok(bound::Chart { title: None, subtitle: None, frame: None, family })
        }
        Chart::Kpi(kpi) => {
            let placed = placement(board, styles, at, chart)?;
            Ok(bound::Chart { title: None, subtitle: None, frame: None, family: bind_kpi(id, kpi, placed.style, placed.own, placed.scope(), board, run)? })
        }
        Chart::Table(table) => Ok(bound::Chart { title: None, subtitle: None, frame: None, family: bind_table(id, table, &styles.at(at)?.charts.table, &styles.own_at(at)?.charts.table, board, run)? }),
        Chart::SparkBar(spark) => Ok(bound::Chart { title: None, subtitle: None, frame: None, family: families::spark_bar::bind(id, spark, &styles.at(at)?.charts.spark_bar, board, run)? }),
        Chart::Callout(callout) => Ok(bound::Chart { title: None, subtitle: None, frame: None, family: bind_callout(callout)? }),
        other => Err(Diagnostic::refused(refuse!("binding a {} chart is not supported", family(other)))),
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
