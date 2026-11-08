//! The bind entry over board-model (spec § 5.1): the drawn set, then each drawn chart.

use std::collections::{BTreeMap, BTreeSet};

use board_model::board::{items, Board, BoardBody, Layout, LayoutItem, Visible};
use board_model::chart::Chart;
use board_model::ids::{ChartId, ItemId};
use board_model::style::style::Style;
use model_core::Patch;
use diagnostic::{diag, refuse, Diagnostic};
use indexmap::IndexMap;

use crate::callout::bind_callout;
use crate::category_colors::CategoryColorScale;
use crate::derived::Derived;
use crate::families;
use crate::kpi::bind_kpi;
use crate::model::{Body, BoundBoard, BoundChart, DrawnSet, Failure};
use crate::run::RunResult;

/// The styles normalize resolved for each placement of a chart, keyed by position as
/// [`BoundBoard::charts`] and the engine's slots are.
///
/// `placed` is a placement's chart style folded over the scope it sits in (`CompiledBoard.placed`):
/// a total, so a consumer that wants the chart's whole family style reads it (a KPI's faces, a
/// callout's style). It is not a specificity tier: a family's `axis_quantitative.X` is folded in
/// at the chart's family slot, so a walk that read it as a tier would rank it above the chart's
/// own `axis.X`, which production ranks above it. A cartesian walk reads the placing scope's
/// style per tier.
///
/// `own` is the chart's own `style:` alone, as a board-style layer (`CompiledBoard.placed_own`):
/// the cartesian families' own tier.
#[derive(Debug, Default)]
pub struct ChartStyles {
    pub placed: BTreeMap<ItemId, Style>,
    pub own: BTreeMap<ItemId, Patch<Style>>,
}

impl ChartStyles {
    pub fn at(&self, at: &ItemId) -> Result<&Style, Diagnostic> {
        self.placed.get(at).ok_or_else(|| diag::err_internal(format!("no resolved chart style for the placement {at}")))
    }

    pub fn own_at(&self, at: &ItemId) -> Result<&Patch<Style>, Diagnostic> {
        self.own.get(at).ok_or_else(|| diag::err_internal(format!("no resolved own chart style for the placement {at}")))
    }
}

/// Binds a normalized board to one run of its queries. A chart that fails becomes its
/// error card; only a board-level error is an `Err`. `derived` supplies each drawn chart's
/// derived style facts.
pub fn bind(board: &Board, styles: &ChartStyles, run: &RunResult, derived: &Derived) -> Result<BoundBoard, Diagnostic> {
    if board.chart_focus.is_some() {
        return Err(Diagnostic::refused(refuse!("binding a board with `chart_focus` is not supported")));
    }
    let mut hidden = BTreeSet::new();
    hide(&board.body, &ItemId::root(), &mut hidden)?;
    let drawn = DrawnSet::new(hidden, BTreeMap::new(), BTreeMap::new());
    let scales = crate::plan_colors(board, |query| run.datasets.get(query))?;
    let internal = |invalid| diag::err_internal(format!("bound board is invalid: {invalid:?}"));
    let mut charts = IndexMap::new();
    for (at, id) in drawn.charts(board).map_err(internal)? {
        charts.insert(at.clone(), bind_chart(board, styles, &at, id, run, derived, &scales)?);
    }
    BoundBoard::new(board, drawn, charts, Vec::new()).map_err(internal)
}

/// Every item under `body` whose `visible:` is false. A hidden board is not entered.
fn hide(body: &BoardBody, at: &ItemId, hidden: &mut BTreeSet<ItemId>) -> Result<(), Diagnostic> {
    if body.details.is_some() {
        return Err(Diagnostic::refused(refuse!("a details board is not supported")));
    }
    if matches!(body.layout, Layout::Tabs(_)) {
        return Err(Diagnostic::refused(refuse!("a tabs layout is not supported")));
    }
    for placement in items(body, at) {
        let LayoutItem::Board(nested) = placement.item else { continue };
        match &nested.visible {
            None | Some(Visible::Flag(true)) => hide(&nested.body, &placement.id, hidden)?,
            Some(Visible::Flag(false)) => {
                hidden.insert(placement.id);
            }
            Some(Visible::Expression(_)) => {
                return Err(Diagnostic::refused(refuse!("an expression `visible:` is not supported")))
            }
            Some(Visible::Probe(_)) => {
                return Err(Diagnostic::refused(refuse!("a query probe `visible:` is not supported")))
            }
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
    derived: &Derived,
    scales: &[CategoryColorScale],
) -> Result<BoundChart, Diagnostic> {
    let chart = board.body.charts.get(id.as_str()).ok_or_else(|| {
        diag::err_unknown_chart(id.as_str(), board.body.charts.keys().map(String::as_str).collect::<Vec<_>>())
    })?;
    let body = placing(&board.body, &ItemId::root(), at).ok_or_else(|| diag::err_internal(format!("no chart is placed at {at}")))?;
    if let Some(error) = run.chart_errors.get(id) {
        let body = Body::Failed(Failure { chart: id.clone(), diagnostic: error.clone() });
        return Ok(BoundChart { title: None, subtitle: None, frame: None, body });
    }
    let facts = || {
        derived.charts.get(at).ok_or_else(|| {
            Diagnostic::refused(refuse!("binding a {} chart without its derived style facts is not supported", family(chart)))
        })
    };
    match chart {
        Chart::Bar(bar) => families::bar::bind(id, bar, board, body, styles.own_at(at)?, run, facts()?, scales),
        Chart::Line(line) => families::line::bind(id, line, board, body, styles.own_at(at)?, run, facts()?),
        Chart::Scatter(scatter) => families::scatter::bind(id, scatter, board, body, styles.own_at(at)?, run, facts()?),
        Chart::Kpi(kpi) => Ok(BoundChart { title: None, subtitle: None, frame: None, body: bind_kpi(id, kpi, &styles.at(at)?.charts.kpi, board, run)? }),
        Chart::Callout(callout) => Ok(BoundChart { title: None, subtitle: None, frame: None, body: bind_callout(callout, &styles.at(at)?.charts.callout)? }),
        other => Err(Diagnostic::refused(refuse!("binding a {} chart is not supported", family(other)))),
    }
}

/// The body under `body` (at `here`) whose own layout places the chart at `at`, whose style the
/// chart draws with.
fn placing<'a>(body: &'a BoardBody, here: &ItemId, at: &ItemId) -> Option<&'a BoardBody> {
    items(body, here).find_map(|placement| match placement.item {
        LayoutItem::Chart { .. } => (placement.id == *at).then_some(body),
        LayoutItem::Board(nested) => placing(&nested.body, &placement.id, at),
    })
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
