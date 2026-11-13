//! A drawn chart's slot: the board lays out around each chart as production sizes it, with the
//! static estimate of its family. A chart that resolved takes its own padding in it
//! (`layout_sizing.py::_vertical_inset`), a failed one the board's (`_unresolved_height`).
//!
//! Slot heights by family:
//! - A failed chart is a card slotted at its family's static estimate.
//! - A KPI card is inset by the card and chart padding in the chart wrapper, and slotted at
//!   the same estimate on the chart's own padding.
//! - A callout card fills the chart wrapper with no padding, slotted at its natural height at the
//!   width it gets.
//! - A plain table of text columns is inset like a KPI's, slotted at the height the table draws at
//!   in the width its padding leaves; its wrapper carries the chart canvas as `Slot.canvas`.
//! - A direct-mode pie or donut is slotted at `pie_content_height`, the board's pie aspect ratio at
//!   the slot width clamped to the board's own `charts.min_height`/`max_height`, never the
//!   family's, plus the card and family padding. The wrapper's selection boxes sit around the
//!   chart's own `<svg>` and its canvas is `Pie.canvas`. A pie on an unsized board, or placed
//!   twice, is refused.
//!
//! A KPI's and a callout's family style (faces, card, slot width and padding) is read from
//! `ChartStyles` at the placement (`Slot.placed`), so a nested scope's `charts.kpi` or
//! `charts.callout` restyle draws in that scope. A callout placed in scopes that style it
//! differently is refused by name, because production caches its card by chart id, width and
//! height, so placements of one size share one scope's card.

use std::collections::BTreeMap;

use bind::board::ChartStyles;
use bind::failed::FailedText;
use bind::bound::{self, Failure, Family, Frame, Sizing};
use board_model::board::layout::LayoutItem;
use board_model::board::{items, Board, BoardBody};
use board_model::chart::{BarChart, Chart as ModelChart, ScatterChart};
use board_model::ids::{ChartId, ItemId};
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use serde_json::json;
use board_model::board::layout::TabLayout;
use layout::board::{bands, chrome};
use layout::board::band::TitleShift;
use layout::families::failed::ChartTitle;
use layout::title::TitleMetrics;
use measure::shaped::FontBook;
use bind::variables::Controls;
use layout::board::assemble::{board_geometry, leaf, Facts, NestedBand, Own, StylePatch};
use layout::board::height::NaturalPlan;
use layout::board::prose;
use layout::board::tree::{Chart, Spare, Title};
use layout::board::width::IntrinsicWidth;
use layout::families::callout::{CalloutCard, CalloutStyle};
use layout::families::failed::{ErrorCard, ErrorTile};
use layout::families::kpi::{KpiCard, KpiFaces};
use layout::family::{Env, Family as _};
use layout::families::spark_bar;
use layout::families::table::{self, TableCard, TableFaces};

use crate::compose::{px, title_text, Header, HeaderFrame};
use crate::EngineFacts;

/// What the engine knows of one drawn chart before layout asks.
pub(crate) struct Slot<'a> {
    /// The board body that places it, whose style sizes and paints it.
    pub body: &'a BoardBody,
    pub chart: &'a ModelChart,
    /// The style of this placement: the chart's own `style:` folded over the scope it sits in,
    /// whose family slot is the chart's whole total family style.
    pub placed: &'a Style,
    /// The chart's own `style:` tier, read only for a failed plot: production's `_unresolved_height`
    /// takes its `aspect_ratio`, `min_height` and `max_height` off the normalized chart.
    own: Option<&'a StylePatch>,
    /// What it bound to, which a callout's natural height wraps.
    drawn: &'a Result<Family, Failure>,
    /// The sizing and padding facts of a drawn plot.
    frame: Option<&'a Frame>,
    /// A drawn plot's authored title and subtitle, which its title band is laid out from.
    pub title: Option<&'a str>,
    subtitle: Option<&'a str>,
    pub facts: Chart,
    /// The slot height every width gets: the family's estimate, not its card.
    pub estimate: f64,
    /// The canvas a table paints on, which its wrapper records (`ChartIdentity.canvas`); a KPI,
    /// callout and failed chart carry none.
    pub canvas: Option<String>,
    /// The nested board that declares the chart, which its authored path starts from; a chart of
    /// the root has none.
    pub authored_in: Option<ItemId>,
}

pub(crate) struct Slots<'a> {
    /// Each drawn chart's slot, by where it is placed: a chart placed twice has two.
    by_placement: BTreeMap<ItemId, Slot<'a>>,
    /// The root's body and each drawn nested board's, by where it sits.
    pub boards: BTreeMap<ItemId, &'a BoardBody>,
    /// The width facts of a chart in a closed tab, which nothing binds: a tabs board hugs the widest of
    /// all its tabs.
    closed: BTreeMap<ItemId, Chart>,
    /// Each board's semantic heading level: the count of titled boards from the root down to it, itself
    /// included (`dispatch.py`'s `parent_level + (1 if board.title else 0)`).
    pub levels: BTreeMap<ItemId, u32>,
    /// What the run hides, opens and selects.
    pub drawn: &'a bound::DrawnSet,
    /// The resolved facts of the board's placements, tabs and details.
    pub styles: &'a ChartStyles,
    /// The total style of each tab pane, by the pane's position: a pane has no body.
    panes: BTreeMap<ItemId, &'a Style>,
    /// Whether the root authors its `width:`; an unauthored one hugs its title's and text's width.
    width_authored: bool,
}

/// The board body that places the item at `target`: the root's, or the nested board's that holds it.
pub(crate) fn body_placing<'a>(board: &'a Board, target: &ItemId) -> Result<&'a BoardBody, Diagnostic> {
    fn find<'a>(body: &'a BoardBody, at: &ItemId, target: &ItemId) -> Option<&'a BoardBody> {
        items(body, at).find_map(|placement| match placement.item {
            LayoutItem::Chart { .. } if &placement.id == target => Some(body),
            LayoutItem::Board(nested) => find(&nested.body, &placement.id, target),
            LayoutItem::Chart { .. } => None,
        })
    }
    find(&board.body, &ItemId::root(), target).ok_or_else(|| diag::err_internal(format!("no board places the chart at {target}")))
}

/// The total style a board scope draws with: normalize puts one on every body.
pub(crate) fn total_style(body: &BoardBody) -> Result<&Style, Diagnostic> {
    body.style.as_deref().ok_or_else(|| diag::err_internal("a normalized board carries its total style"))
}

pub(crate) fn plan<'a>(board: &'a Board, styles: &'a ChartStyles, bound: &'a bound::Board, facts: &impl EngineFacts) -> Result<Slots<'a>, Diagnostic> {
    let mut slots = Slots { by_placement: BTreeMap::new(), boards: BTreeMap::from([(ItemId::root(), &board.body)]), levels: BTreeMap::new(), drawn: bound.drawn(), closed: BTreeMap::new(), panes: BTreeMap::new(), styles, width_authored: board.width.is_some() };
    walk(&board.body, &ItemId::root(), 0, false, board, styles, bound, facts, &mut slots)?;
    refuse_callout_scopes(&slots)?;
    // An unpinned table hugs the board's width to its columns (`_make_chart_width_measurer`).
    if board.width.is_none() && slots.by_placement.values().any(|slot| matches!(slot.drawn, Ok(Family::Table(_)))) {
        return Err(Diagnostic::refused(refuse!("a table on a board with no authored `width:` is not supported")));
    }
    if board.width.is_none() && slots.by_placement.values().any(|slot| matches!(slot.drawn, Ok(Family::Pie(_))) || matches!((slot.drawn, slot.chart), (Err(_), ModelChart::Pie(_) | ModelChart::Donut(_)))) {
        return Err(Diagnostic::refused(refuse!("a pie on a board with no authored `width:` is not supported")));
    }
    // Every id a chart defines ends with its scope, which a later placement of the chart suffixes.
    let mut pies = BTreeMap::new();
    for slot in slots.by_placement.values().filter(|slot| matches!(slot.drawn, Ok(Family::Pie(_)))) {
        if pies.insert(slot.facts.id.as_str(), ()).is_some() {
            return Err(Diagnostic::refused(refuse!("a pie placed more than once is not supported")));
        }
    }
    Ok(slots)
}

/// Production caches a callout's rendered card by chart id, width and height, so a callout placed in
/// scopes that style it differently draws one scope's card at every placement that shares a size.
/// The engine draws each in its own scope, so it refuses the placements production would conflate.
fn refuse_callout_scopes(slots: &Slots) -> Result<(), Diagnostic> {
    let mut seen = BTreeMap::new();
    for slot in slots.by_placement.values().filter(|slot| matches!(slot.drawn, Ok(Family::Callout(_)))) {
        // What draws a callout: its slot width and the style it resolves to, which also reads the
        // scope's emoji family, bold weight and code family.
        let drawn = (slot.placed.charts.callout.preferred_width, CalloutStyle::of(&slot.placed.charts.callout, total_style(slot.body)?)?);
        if *seen.entry(slot.facts.id.as_str()).or_insert_with(|| drawn.clone()) != drawn {
            return Err(Diagnostic::refused(refuse!("a callout placed in scopes that style it differently is not supported")));
        }
    }
    Ok(())
}

fn walk<'a>(
    body: &'a BoardBody,
    at: &ItemId,
    parent_level: u32,
    closed: bool,
    board: &'a Board,
    styles: &'a ChartStyles,
    bound: &'a bound::Board,
    facts: &impl EngineFacts,
    out: &mut Slots<'a>,
) -> Result<(), Diagnostic> {
    let level = parent_level + u32::from(title_text(body).is_some());
    out.levels.insert(at.clone(), level);
    // A collapsed details board draws its summary bar alone, and a tabs board its open tab.
    if body.details.is_some() && bound.drawn().details_open(&body.id) != Some(true) {
        return Ok(());
    }
    let open = bound.drawn().active_tab(&body.id);
    if let board_model::board::Layout::Tabs(tabs) = &body.layout {
        for (index, tab) in tabs.items.iter().enumerate() {
            let pane = tab.style.as_deref().ok_or_else(|| diag::err_internal("a normalized tab carries its total style"))?;
            out.panes.insert(at.key("tabs").key("items").index(index), pane);
        }
    }
    for placement in items(body, at) {
        // A closed tab is walked for its width only: the board hugs the widest of all its tabs.
        let closed = closed || open.is_some_and(|open| placement.pane.first() != Some(&open));
        if closed {
            // The run records nothing about an item in a closed tab: not whether it is shown, not a nested
            // tabs board's open tab or a section's state. Layout leaves the last two out; a hug that would
            // need any of them is refused.
            let unread = layout::board::assemble::opens_a_closed_tab_cannot_hold(placement.item);
            let held = unread
                || match placement.item {
                    LayoutItem::Board(nested) => nested.visible.is_some(),
                    LayoutItem::Chart { visible, .. } => visible.is_some(),
                };
            if held && !out.width_authored {
                return Err(Diagnostic::refused(refuse!("a conditionally shown item, or a tabs or details board, in a closed tab of a board with no authored `width:` is not supported")));
            }
            if unread {
                continue;
            }
        }
        if bound.drawn().is_hidden(&placement.id) {
            continue;
        }
        match placement.item {
            LayoutItem::Board(nested) => {
                out.boards.insert(placement.id.clone(), &nested.body);
                walk(&nested.body, &placement.id, level, closed, board, styles, bound, facts, out)?
            }
            LayoutItem::Chart { id, .. } if closed => {
                let chart = board.body.charts.get(id.as_str()).ok_or_else(|| diag::err_internal(format!("chart {id} is not in the board")))?;
                out.closed.insert(placement.id.clone(), closed_chart(chart, styles.at(&placement.id)?, &placement.id, id, out.width_authored)?);
            }
            LayoutItem::Chart { id, .. } => {
                let chart = board.body.charts.get(id.as_str()).ok_or_else(|| diag::err_internal(format!("chart {id} is not in the board")))?;
                let drawn = bound.charts().get(&placement.id).ok_or_else(|| diag::err_internal(format!("chart {id} at {} is not bound", placement.id)))?;
                let placed = styles.at(&placement.id)?;
                let own = if drawn.family.is_err() { Some(styles.own_at(&placement.id)?) } else { None };
                let mut slot = slot(body, chart, placed, own, &placement.id, id, drawn, facts.style(body)?)?;
                slot.authored_in = styles.chart_boards.get(id).cloned();
                if let (ModelChart::Table(_), Ok(Family::Table(_))) = (chart, &drawn.family) {
                    let placed = bind::board::placement(board, styles, &placement.id, chart)?;
                    slot.canvas = Some(placed.canvas(&placed.own.charts.table.background, placed.scope())?);
                }
                if let (ModelChart::SparkBar(_), Ok(Family::SparkBar(_))) = (chart, &drawn.family) {
                    let placed = bind::board::placement(board, styles, &placement.id, chart)?;
                    slot.canvas = Some(placed.canvas(&model_core::Tri::Unset, placed.scope())?);
                }
                out.by_placement.insert(placement.id.clone(), slot);
            }
        }
    }
    Ok(())
}

/// A chart in a closed tab, as the width hug sees it: the preferred width its placement's style gives its
/// family, with no data (`without_data`). A table's and a pie's widths read their data, which a closed
/// tab has none of.
fn closed_chart(chart: &ModelChart, placed: &Style, at: &ItemId, id: &ChartId, width_authored: bool) -> Result<Chart, Diagnostic> {
    let preferred = match chart {
        ModelChart::Kpi(_) => placed.charts.kpi.preferred_width,
        ModelChart::Callout(_) => placed.charts.callout.preferred_width,
        ModelChart::SparkBar(_) => placed.charts.spark_bar.preferred_width,
        ModelChart::Scatter(_) => placed.charts.scatter.cartesian.painted.base.preferred_width,
        ModelChart::Bar(_) => placed.charts.bar.cartesian.painted.base.preferred_width,
        ModelChart::Line(_) => placed.charts.line.cartesian.painted.base.preferred_width,
        ModelChart::Histogram(_) => placed.charts.histogram.cartesian.painted.base.preferred_width,
        // An authored `width:` ignores every chart's own, so what the hug cannot measure is not asked.
        _ if width_authored => 0.0,
        _ => return Err(Diagnostic::refused(refuse!("a table or pie in a closed tab of a board with no authored `width:` is not supported"))),
    };
    let width = IntrinsicWidth { min: None, preferred: Some(preferred) };
    Ok(Chart { at: at.clone(), id: id.as_str().to_string(), width: None, intrinsic: width, without_data: width, content_sized: true, table: false, spare: Spare::WhenAlone, floor: None })
}

fn slot<'a>(body: &'a BoardBody, chart: &'a ModelChart, placed: &'a Style, own: Option<&'a StylePatch>, at: &ItemId, id: &ChartId, bound: &'a bound::Chart, style: &StylePatch) -> Result<Slot<'a>, Diagnostic> {
    let drawn = &bound.family;
    let failed = matches!(drawn, Err(_));
    // A chart the engine refused is already that chart's error card, whose subtitle, width and notes
    // the slot does not model: the refusal that names why stands, and the card takes the room its
    // text needs. What a chart that bound or failed otherwise cannot be placed by is refused by
    // name, and `render_scoped` turns that into the chart's own refusal (`refuse_chart`).
    let refused_card = matches!(drawn, Err(failure) if failure.diagnostic.code == diagnostic::codes::ERR_ENGINE_REFUSED.id);
    let unplaced = |what: Diagnostic| what.with_sink_param("placement", json!(at.to_string()));
    let (preferred, notes, content, table) = match chart {
        ModelChart::Kpi(k) => (Some(placed.charts.kpi.preferred_width), &k.base.notes, leaf(style.charts.kpi.default_height, "style.charts.kpi.default_height")?, false),
        ModelChart::Table(t) => (Some(placed.charts.table.preferred_width), &t.shared.base.notes, leaf(style.charts.default_table_height, "style.charts.default_table_height")?, true),
        ModelChart::Callout(c) => (Some(placed.charts.callout.preferred_width), &None, 0.0, false),
        ModelChart::Pie(c) | ModelChart::Donut(c) => (None, &c.radial.shared.base.notes, 0.0, false),
        ModelChart::SparkBar(s) if matches!(drawn, Ok(Family::SparkBar(_))) => {
            let content = match drawn {
                Ok(Family::SparkBar(spark)) => spark_bar::natural_height(spark, &placed.charts.spark_bar),
                _ => return Err(diag::err_internal("a drawn spark_bar chart binds to a spark_bar body")),
            };
            (Some(placed.charts.spark_bar.preferred_width), &s.shared.base.notes, content, false)
        }
        ModelChart::Scatter(s) if matches!(drawn, Ok(Family::Cartesian(_))) => (Some(placed.charts.scatter.cartesian.painted.base.preferred_width), &s.cartesian.shared.base.notes, 0.0, false),
        ModelChart::Bar(b) if matches!(drawn, Ok(Family::Cartesian(bound::Cartesian::Plane(_)))) => (Some(placed.charts.bar.cartesian.painted.base.preferred_width), &b.cartesian.shared.base.notes, 0.0, false),
        ModelChart::Line(l) if matches!(drawn, Ok(Family::Cartesian(bound::Cartesian::Plane(_)))) => (Some(placed.charts.line.cartesian.painted.base.preferred_width), &l.cartesian.shared.base.notes, 0.0, false),
        ModelChart::Histogram(h) if matches!(drawn, Ok(Family::Cartesian(_))) => (Some(placed.charts.histogram.cartesian.painted.base.preferred_width), &h.cartesian.shared.base.notes, 0.0, false),
        // A failed plot is the error card in a slot sized like any plot's, on the board's width.
        ModelChart::Scatter(s) if failed => (None, &s.cartesian.shared.base.notes, 0.0, false),
        ModelChart::Bar(b) if failed => (None, &b.cartesian.shared.base.notes, 0.0, false),
        ModelChart::Line(l) if failed => (None, &l.cartesian.shared.base.notes, 0.0, false),
        ModelChart::Histogram(h) if failed => (None, &h.cartesian.shared.base.notes, 0.0, false),
        _ if matches!(drawn, Ok(Family::Cartesian(bound::Cartesian::Plane(_)))) => return Err(unplaced(Diagnostic::refused(refuse!("sizing the slot of the plot of chart {id} is not supported")))),
        _ if refused_card => (None, &None, 0.0, false),
        _ => return Err(unplaced(Diagnostic::refused(refuse!("sizing the slot of a failed {id} chart of this family is not supported")))),
    };
    let subtitled = match chart {
        ModelChart::Table(t) => t.shared.subtitle.is_some(),
        ModelChart::Pie(c) | ModelChart::Donut(c) => c.radial.shared.subtitle.is_some(),
        ModelChart::Scatter(s) => s.cartesian.shared.subtitle.is_some(),
        ModelChart::Bar(b) | ModelChart::Histogram(b) => b.cartesian.shared.subtitle.is_some(),
        ModelChart::Line(l) => l.cartesian.shared.subtitle.is_some(),
        _ => false,
    };
    // The band of a failed chart's title takes its subtitle's block; the card draws none.
    if failed && subtitled && !refused_card {
        return Err(unplaced(Diagnostic::refused(refuse!("a failed chart's subtitle is not supported"))));
    }
    if matches!(chart, ModelChart::Scatter(ScatterChart { cartesian, .. }) | ModelChart::Histogram(BarChart { cartesian, .. }) | ModelChart::Bar(BarChart { cartesian, .. }) if cartesian.width.is_some()) || matches!(chart, ModelChart::Line(l) if l.cartesian.width.is_some()) {
        if !refused_card {
            return Err(unplaced(Diagnostic::refused(refuse!("a cartesian chart's `width:` is not supported"))));
        }
    }
    if notes.is_some() && !refused_card {
        return Err(unplaced(Diagnostic::refused(refuse!("a chart's notes are not supported"))));
    }
    let card_padding = leaf(style.frame.card_padding, "style.frame.card_padding")?;
    let padding = match chart {
        ModelChart::Kpi(k) if !failed => (placed.charts.kpi.padding.top, placed.charts.kpi.padding.bottom),
        ModelChart::Table(_) if !failed => (placed.charts.table.padding.top, placed.charts.table.padding.bottom),
        ModelChart::SparkBar(_) => (placed.charts.spark_bar.padding.top, placed.charts.spark_bar.padding.bottom),
        ModelChart::Pie(_) | ModelChart::Donut(_) if !failed => (placed.charts.pie.base.padding.top, placed.charts.pie.base.padding.bottom),
        ModelChart::Scatter(_) | ModelChart::Line(_) | ModelChart::Histogram(_) | ModelChart::Bar(_) if !failed => {
            let frame = bound.frame.as_ref().ok_or_else(|| diag::err_internal("a plot carries its frame"))?;
            (frame.padding.top, frame.padding.bottom)
        }
        _ => (leaf(style.charts.padding.top, "style.charts.padding.top")?, leaf(style.charts.padding.bottom, "style.charts.padding.bottom")?),
    };
    let width = IntrinsicWidth { min: None, preferred };
    let pie = matches!(drawn, Ok(Family::Pie(_)));
    let (content_sized, spare) = match (chart, bound.frame.as_ref()) {
        (ModelChart::Scatter(_) | ModelChart::Line(_) | ModelChart::Histogram(_) | ModelChart::Bar(_), Some(frame)) => plot_sizing(&frame.sizing),
        _ if pie => (false, Spare::Never),
        _ => (true, Spare::WhenAlone),
    };
    Ok(Slot {
        body,
        chart,
        placed,
        own,
        drawn,
        frame: bound.frame.as_ref(),
        title: bound.title.as_deref(),
        subtitle: bound.subtitle.as_deref(),
        // kpi, table and callout draw to their content and take spare height only alone
        // (`NON_ASPECT_RATIO_TYPES`, `_takes_spare_height`).
        facts: Chart { at: at.clone(), id: id.as_str().to_string(), width: None, intrinsic: width, without_data: width, content_sized, table, spare, floor: None },
        estimate: content + 2.0 * card_padding + padding.0 + padding.1,
        canvas: None,
        authored_in: None,
    })
}

/// `pie_content_height` plus the padding around it (`pie_box`) for a drawn pie: the placement's pie
/// aspect ratio. The family's `min_height` and `max_height` are not read: production's sizing reads
/// `chart_defaults` alone.
pub(crate) fn pie_height(slot: &Slot, style: &StylePatch, width: f64) -> Result<f64, Diagnostic> {
    let padding = &slot.placed.charts.pie.base.padding;
    let (ModelChart::Pie(chart) | ModelChart::Donut(chart)) = slot.chart else {
        return Err(diag::err_internal("a pie's chart is a pie or donut"));
    };
    content_slot(slot, chart.radial.height.map(|h| h.get()), slot.placed.charts.pie.aspect_ratio, None, style, width, (padding.top, padding.bottom))
}

/// A chart whose query failed is slotted at its content height inside the card and the board's
/// chart padding (`_unresolved_height`). A plot reads its own `aspect_ratio`, `min_height` and
/// `max_height` first, as production's normalized chart carries them; a pie's own `style:` is not
/// read, since the resolved chart that carries it is what is missing.
fn failed_height(slot: &Slot, style: &StylePatch, width: f64) -> Result<f64, Diagnostic> {
    let board = total_style(slot.body)?;
    let padding = (leaf(style.charts.padding.top, "style.charts.padding.top")?, leaf(style.charts.padding.bottom, "style.charts.padding.bottom")?);
    let (authored, family, own) = match slot.chart {
        ModelChart::Pie(c) => return content_slot(slot, c.radial.height.map(|h| h.get()), board.charts.pie.aspect_ratio, None, style, width, padding),
        ModelChart::Donut(c) => {
            let family = board.charts.donut.as_ref().and_then(|patch| patch.aspect_ratio).unwrap_or(board.charts.pie.aspect_ratio);
            return content_slot(slot, c.radial.height.map(|h| h.get()), family, None, style, width, padding);
        }
        ModelChart::Scatter(c) => (c.cartesian.height, board.charts.scatter.cartesian.painted.aspect_ratio, slot.own.map(|own| own_sizing(&own.charts.scatter.cartesian.painted))),
        ModelChart::Bar(c) => (c.cartesian.height, board.charts.bar.cartesian.painted.aspect_ratio, slot.own.map(|own| own_sizing(&own.charts.bar.cartesian.painted))),
        ModelChart::Histogram(c) => (c.cartesian.height, board.charts.histogram.cartesian.painted.aspect_ratio, slot.own.map(|own| own_sizing(&own.charts.bar.cartesian.painted))),
        ModelChart::Line(c) => (c.cartesian.height, board.charts.line.cartesian.painted.aspect_ratio, slot.own.map(|own| own_sizing(&own.charts.line.cartesian.painted))),
        _ => return Err(diag::err_internal("only a pie or a plot is slotted by its content height")),
    };
    content_slot(slot, authored.map(|h| h.get()), family, own, style, width, padding)
}

/// A chart's own aspect ratio and height range, which win over the board's (`get_chart_content_height`).
type OwnSizing = (Option<f64>, Option<f64>, Option<f64>);

fn own_sizing(painted: &board_model::style::chart_base::PaintedChartStyleBasePatch) -> OwnSizing {
    (painted.aspect_ratio.as_ref().copied(), painted.min_height.as_ref().copied(), painted.max_height.as_ref().copied())
}

/// `get_chart_content_height` and the card padding around it: the chart's own `height`, else its own
/// aspect ratio, else the family's (the board's global one when that is 0) at `width`, clamped to the
/// chart's own height range, else the board's, else `default_chart_height` when `width` or the ratio is
/// not positive.
fn content_slot(slot: &Slot, authored: Option<f64>, family_aspect: f64, own: Option<OwnSizing>, style: &StylePatch, width: f64, (top, bottom): (f64, f64)) -> Result<f64, Diagnostic> {
    let (own_aspect, own_min, own_max) = own.unwrap_or_default();
    let aspect = match own_aspect {
        Some(own) => own,
        None if family_aspect != 0.0 => family_aspect,
        None => total_style(slot.body)?.charts.aspect_ratio,
    };
    let card_padding = leaf(style.frame.card_padding, "style.frame.card_padding")?;
    let content = if authored.is_some() || (width > 0.0 && aspect > 0.0) {
        let min_height = own_min.map_or_else(|| leaf(style.charts.min_height, "style.charts.min_height"), Ok)?;
        let max_height = own_max.map_or_else(|| leaf(style.charts.max_height, "style.charts.max_height"), Ok)?;
        content_height(&Sizing { height: authored, aspect_ratio: aspect, min_height, max_height }, width)?
    } else {
        leaf(style.charts.default_chart_height, "style.charts.default_chart_height")?
    };
    Ok(content + 2.0 * card_padding + top + bottom)
}

/// A table chart's authored title text.
pub(crate) fn table_title(chart: &ModelChart) -> Option<&str> {
    match chart {
        ModelChart::Table(t) => t.shared.title.as_ref().map(|title| title.as_str()),
        _ => None,
    }
}

pub(crate) fn additive_padding(card_padding: f64, own: &board_model::style::board::PaddingStyle) -> (f64, f64, f64, f64) {
    (card_padding + own.left, card_padding + own.right, card_padding + own.top, card_padding + own.bottom)
}

impl<'a> Slots<'a> {
    /// The total style of the layout at `at`: a board's, or a tab pane's.
    pub fn style_of(&self, at: &ItemId) -> Result<&'a Style, Diagnostic> {
        match (self.boards.get(at), self.panes.get(at)) {
            (Some(body), _) => total_style(body),
            (None, Some(pane)) => Ok(pane),
            (None, None) => Err(diag::err_internal(format!("the layout at {at} has no style"))),
        }
    }

    pub fn get(&self, at: &ItemId) -> Result<&Slot<'a>, Diagnostic> {
        self.by_placement.get(at).ok_or_else(|| diag::err_internal(format!("the chart at {at} has no slot")))
    }

    pub fn facts<'f, F: EngineFacts>(&'f self, facts: &'f F, book: &'f FontBook) -> Adapter<'f, 'a, F> {
        Adapter { slots: self, facts, book }
    }
}

/// Layout's view of the caller's facts and the slots.
pub(crate) struct Adapter<'f, 'a, F> {
    slots: &'f Slots<'a>,
    facts: &'f F,
    book: &'f FontBook,
}

impl<F: EngineFacts> Adapter<'_, '_, F> {
    /// Which heading a board's title is: the root's, or a nested board's at its depth.
    fn title_kind(&self, at: &ItemId, root: bool) -> Result<prose::TitleKind, Diagnostic> {
        let level = *self.slots.levels.get(at).ok_or_else(|| diag::err_internal(format!("no heading level for the board at {at}")))?;
        Ok(prose::TitleKind { level, root })
    }
}

impl<F: EngineFacts> Facts for Adapter<'_, '_, F> {
    fn style(&self, body: &BoardBody) -> Result<&StylePatch, Diagnostic> {
        self.facts.style(body)
    }

    fn own(&mut self, at: &ItemId, body: &BoardBody, root: bool) -> Result<Own, Diagnostic> {
        if !root && !body.variables.is_empty() {
            return Err(Diagnostic::refused(refuse!("a nested board's variables are not supported")));
        }
        // A heading that opens a nested board's text is drawn as its title, which a `cols` row's band
        // aligns (production lifts it only when the board has no items of its own): refused at any
        // leading `#`, which also refuses prose such as `#tag` that is not a heading.
        if !root && body.text.as_ref().is_some_and(|text| text.as_str().trim_start().starts_with('#')) {
            return Err(Diagnostic::refused(refuse!("a nested board whose text opens with a heading is not supported")));
        }
        let own = bands::own(root, total_style(body)?, self.book)?;
        // An authored `width:` ignores a board's own content; a hugged board takes its title's and text's.
        if self.slots.width_authored || (title_text(body).is_none() && body.text.is_none()) {
            return Ok(own);
        }
        let ask = prose::own_width(total_style(body)?, title_text(body), self.title_kind(at, root)?, body.text.as_ref().map(|text| text.as_str()), self.book)?;
        Ok(Own { width: own.width.combine_max(ask) })
    }

    fn root_band(&mut self, body: &BoardBody, content_width: f64) -> Result<f64, Diagnostic> {
        let Some(title) = title_text(body) else { return Ok(0.0) };
        let style = total_style(body)?;
        let width = (content_width - 2.0 * style.frame.card_padding).max(0.0);
        Ok(prose::title(style, title, self.book, width, prose::TitleKind::ROOT)?.height)
    }

    fn nested_band(&mut self, at: &ItemId, content_width: f64, has_items: bool) -> Result<NestedBand, Diagnostic> {
        let Some(body) = self.slots.boards.get(at) else { return Ok(NestedBand::NONE) };
        if body.text.is_none() && title_text(body).is_none() {
            return Ok(NestedBand::NONE);
        }
        let geometry = board_geometry(self.facts.style(body)?)?;
        let kind = self.title_kind(at, false)?;
        let total = total_style(body)?;
        let frame = HeaderFrame::nested(&geometry, has_items, layout::board::assemble::opens_with_text(body, at, self.slots.drawn), kind, TitleShift::default(), None);
        let header = Header::of(body, total, &frame, self.book, &Controls { list: Vec::new(), reset_enabled: false }, content_width)?;
        let title = match title_text(body) {
            // `board_title_metrics`: the first baseline and the body top of a plain title, from the board's top.
            Some(text) => {
                let card_padding = total.frame.card_padding;
                let block = prose::title(total, text, self.book, (content_width - 2.0 * card_padding).max(1.0), kind)?;
                let height = block.height.max(total.title.base.min_height);
                let gap_below = if body.text.is_some() {
                    0.0
                } else if has_items {
                    geometry.gap.unwrap_or(0.0) + card_padding
                } else {
                    0.0
                };
                Title::Own(TitleMetrics { ascent: geometry.chrome().2 + card_padding + block.baseline, tail: height - block.baseline + gap_below })
            }
            None => Title::None,
        };
        Ok(NestedBand { band: header.band(), title })
    }

    fn chart(&mut self, at: &ItemId, _id: &ChartId) -> Result<Chart, Diagnostic> {
        match self.slots.closed.get(at) {
            Some(closed) => Ok(closed.clone()),
            None => Ok(self.slots.get(at)?.facts.clone()),
        }
    }

    fn pane_style(&self, body: &BoardBody, tab: usize) -> Result<&StylePatch, Diagnostic> {
        self.facts.pane_style(body, tab)
    }

    fn pane_top(&mut self, body: &BoardBody, tabs: &TabLayout) -> Result<f64, Diagnostic> {
        let style = total_style(body)?;
        let titles = tabs.items.iter().map(|tab| chrome::plain(&tab.title, "a tab title")).collect::<Result<Vec<_>, _>>()?;
        Ok(chrome::tab_strip(style, tabs.position, &titles, self.book)?.pane_top(style))
    }

    fn summary_height(&mut self, body: &BoardBody) -> Result<f64, Diagnostic> {
        chrome::summary_height(total_style(body)?)
    }

    fn details_offset(&mut self, body: &BoardBody) -> Result<f64, Diagnostic> {
        Ok(total_style(body)?.layout.details.content_y_offset)
    }

    fn natural(&mut self, chart: &Chart, width: f64) -> Result<NaturalPlan, Diagnostic> {
        // `plan` filled a slot for every placement the layout asks about.
        let slot = &self.slots.by_placement[&chart.at];
        let height = match (slot.drawn, slot.chart) {
            // A callout's slot is its card: `_build_height_provider` renders it at the slot's width and
            // takes its natural height, with no card padding.
            (Ok(Family::Callout(callout)), ModelChart::Callout(_)) => {
                let style = CalloutStyle::of(&slot.placed.charts.callout, total_style(slot.body)?)?;
                CalloutCard { callout, style: &style }.plan(&Env { book: self.book }, width)?.natural_height
            }
            // A table's slot is the height the engine draws it at, unbounded, in the width its
            // padding leaves (`_get_table_height_from_data`), plus that padding (`_vertical_inset`).
            (Ok(Family::Table(bound_table)), ModelChart::Table(_)) => {
                let style = self.facts.style(slot.body)?;
                let chart_style = &slot.placed.charts.table;
                let card_padding = leaf(style.frame.card_padding, "style.frame.card_padding")?;
                let (left, right, top, bottom) = additive_padding(card_padding, &chart_style.padding);
                let faces = TableFaces::of(chart_style, slot.placed, table_title(slot.chart), width)?;
                let card = TableCard { table: bound_table, style: chart_style, faces: &faces };
                let planned = table::plan(&card, self.book, (width - left - right).max(0.0))?;
                let title = planned.title_metrics().map(|(ascent, tail)| TitleMetrics { ascent: top + ascent, tail });
                // The card draws inside whole-pixel padding (`compose::px`), so the cell holds the same.
                return Ok(NaturalPlan { height: planned.height + px(top) + px(bottom), title });
            }
            // A pie's slot is its content height (`pie_content_height`) inside the card and chart padding.
            (Ok(Family::Pie(bound_pie)), ModelChart::Pie(_) | ModelChart::Donut(_)) => {
                let style = self.facts.style(slot.body)?;
                let height = pie_height(slot, style, width)?;
                let (left, right, top, _) = additive_padding(leaf(style.frame.card_padding, "style.frame.card_padding")?, &slot.placed.charts.pie.base.padding);
                let title = chart_title_metrics(slot, bound_pie.title.as_deref(), bound_pie.subtitle.as_deref(), width, (left, right, top), self.book)?;
                return Ok(NaturalPlan { height, title });
            }
            // A failed pie or plot draws the error card too, which is at least its own natural height.
            (Err(failure), ModelChart::Pie(_) | ModelChart::Donut(_) | ModelChart::Scatter(_) | ModelChart::Bar(_) | ModelChart::Histogram(_) | ModelChart::Line(_)) => {
                let card = error_card(self.facts, slot, failure, width, TitleShift::default())?.plan(&Env { book: self.book }, width)?.natural_height;
                failed_height(slot, self.facts.style(slot.body)?, width)?.max(card)
            }
            // `get_chart_content_height`: the authored height, else the aspect ratio's at this width
            // clamped, under the card and chart padding the slot carries.
            (Ok(Family::Cartesian(cartesian)), ModelChart::Scatter(_) | ModelChart::Histogram(_) | ModelChart::Bar(_) | ModelChart::Line(_)) if matches!(slot.chart, ModelChart::Scatter(_) | ModelChart::Histogram(_)) || matches!(cartesian, bound::Cartesian::Plane(_)) => {
                let frame = plot_frame(slot)?;
                let height = content_height(&frame.sizing, width)? + slot.estimate;
                let card_padding = leaf(self.facts.style(slot.body)?.frame.card_padding, "style.frame.card_padding")?;
                let (left, right, top) = (card_padding + frame.padding.left, card_padding + frame.padding.right, card_padding + frame.padding.top);
                let title = chart_title_metrics(slot, slot.title, slot.subtitle, width, (left, right, top), self.book)?;
                return Ok(NaturalPlan { height, title });
            }
            // A failed chart's slot is its error card (`_render_chart_resolve_failure`), which draws
            // at least its natural height.
            (Err(failure), _) => {
                let natural = error_card(self.facts, slot, failure, width, TitleShift::default())?.plan(&Env { book: self.book }, width)?.natural_height;
                natural.max(slot.estimate)
            }
            // A KPI's slot holds its card's natural height inside the card and chart padding: the
            // card draws at least that (`kpi::draw`), and production stacks by what it drew.
            (Ok(Family::Kpi(kpi)), ModelChart::Kpi(_)) => {
                let chart_style = &slot.placed.charts.kpi;
                let faces = KpiFaces::of(chart_style, &total_style(slot.body)?.font.emoji).ruled_by(kpi);
                let card = KpiCard { kpi, style: chart_style, faces: &faces };
                let card_padding = leaf(self.facts.style(slot.body)?.frame.card_padding, "style.frame.card_padding")?;
                let (left, right, top, bottom) = additive_padding(card_padding, &chart_style.padding);
                let natural = card.plan(&Env { book: self.book }, (width - left - right).max(0.0))?.natural_height;
                (natural + px(top) + px(bottom)).max(slot.estimate)
            }
            // A spark_bar's slot holds its rows inside the whole-pixel padding it draws in.
            (Ok(Family::SparkBar(spark)), ModelChart::SparkBar(_)) => {
                let (_, _, top, bottom) = additive_padding(leaf(self.facts.style(slot.body)?.frame.card_padding, "style.frame.card_padding")?, &slot.placed.charts.spark_bar.padding);
                (spark_bar::natural_height(spark, &slot.placed.charts.spark_bar) + px(top) + px(bottom)).max(slot.estimate)
            }
            _ => slot.estimate,
        };
        let title = match (slot.drawn, slot.title) {
            (Err(_), Some(_)) => Some(ChartTitle::metrics(slot.placed, width, leaf(self.facts.style(slot.body)?.frame.card_padding, "style.frame.card_padding")?)?),
            _ => None,
        };
        Ok(NaturalPlan { height, title })
    }
}

/// A failed chart's card: no padding inset, no card border or shadow, its text from the failure.
/// Its authored title sits above the tile on the band a chart of `width` gives it, moved by its
/// `cols` row's `shift` (`_failed_title`).
pub(crate) fn error_card(facts: &impl EngineFacts, slot: &Slot, failure: &Failure, width: f64, shift: TitleShift) -> Result<ErrorCard, Diagnostic> {
    let card_padding = leaf(facts.style(slot.body)?.frame.card_padding, "style.frame.card_padding")?;
    let title = match slot.title {
        Some(text) => {
            let metrics = ChartTitle::metrics(slot.placed, width, card_padding)?;
            Some(ChartTitle::of(text, slot.placed, width, metrics, shift)?)
        }
        None => None,
    };
    Ok(ErrorCard { text: FailedText::of(failure, facts.docs_site())?, tile: ErrorTile::of(total_style(slot.body)?)?, content_left: card_padding, title })
}

/// What a `cols` row aligns a drawn chart's title by: production's sizing block (`vega_title_block`)
/// at `top` under the card's edge, in the `width` the padding's `left` and `right` leave.
fn chart_title_metrics(slot: &Slot, text: Option<&str>, subtitle: Option<&str>, width: f64, (left, right, top): (f64, f64, f64), book: &FontBook) -> Result<Option<TitleMetrics>, Diagnostic> {
    let Some(text) = text else { return Ok(None) };
    let title = layout::title::chart_title(slot.facts.id.as_str(), text, subtitle, slot.placed, width, width - left - right, book)?;
    Ok(title.map(|t| TitleMetrics { ascent: top + t.metrics.ascent, tail: t.metrics.tail }))
}

fn plot_frame<'a>(slot: &Slot<'a>) -> Result<&'a Frame, Diagnostic> {
    slot.frame.ok_or_else(|| diag::err_internal("a plot carries its frame"))
}

fn content_height(sizing: &Sizing, width: f64) -> Result<f64, Diagnostic> {
    if let Some(height) = sizing.height {
        return Ok(height);
    }
    if sizing.aspect_ratio <= 0.0 {
        return Err(diag::err_internal("a plot's aspect ratio is positive"));
    }
    Ok(sizing.min_height.max(sizing.max_height.min(width / sizing.aspect_ratio)))
}

/// Whether a plot draws to its content, and whether it takes spare height: it draws to its slot, and
/// takes spare height unless its chart-root `height:` is authored (`_takes_spare_height`).
fn plot_sizing(sizing: &Sizing) -> (bool, Spare) {
    (false, if sizing.height.is_some() { Spare::WhenAlone } else { Spare::Always })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sizing(height: Option<f64>) -> Sizing {
        Sizing { height, aspect_ratio: 1.5, min_height: 180.0, max_height: 900.0 }
    }

    #[test]
    fn a_plot_draws_to_its_slot_and_takes_spare_height_unless_its_height_is_authored() {
        assert_eq!(plot_sizing(&sizing(None)), (false, Spare::Always));
        assert_eq!(plot_sizing(&sizing(Some(300.0))), (false, Spare::WhenAlone));
    }

    #[test]
    fn a_plot_s_content_is_its_authored_height_else_the_clamped_aspect_height() {
        assert_eq!(content_height(&sizing(Some(300.0)), 600.0).unwrap(), 300.0);
        assert_eq!(content_height(&sizing(None), 600.0).unwrap(), 400.0);
        assert_eq!(content_height(&sizing(None), 90.0).unwrap(), 180.0);
        assert_eq!(content_height(&sizing(None), 3000.0).unwrap(), 900.0);
        assert!(content_height(&Sizing { aspect_ratio: 0.0, ..sizing(None) }, 600.0).is_err());
    }
}
