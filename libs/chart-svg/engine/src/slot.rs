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
use bind::bound::{self, Failure, Family, Frame, Sizing};
use board_model::board::layout::LayoutItem;
use board_model::board::{items, Board, BoardBody};
use board_model::chart::{BarChart, Chart as ModelChart, ScatterChart};
use board_model::ids::{ChartId, ItemId};
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use board_model::board::layout::TabLayout;
use layout::board::{bands, chrome};
use layout::title::TitleMetrics;
use measure::shaped::FontBook;
use layout::board::assemble::{leaf, Facts, Own, StylePatch};
use layout::board::height::NaturalPlan;
use layout::board::prose;
use layout::board::tree::{Chart, Spare};
use layout::board::width::IntrinsicWidth;
use layout::families::callout::{self, CalloutCard, CalloutStyle};
use layout::families::spark_bar;
use layout::families::table::{self, TableCard, TableFaces};

use crate::compose::title_text;
use crate::EngineFacts;

/// What the engine knows of one drawn chart before layout asks.
pub(crate) struct Slot<'a> {
    /// The board body that places it, whose style sizes and paints it.
    pub body: &'a BoardBody,
    pub chart: &'a ModelChart,
    /// The style of this placement: the chart's own `style:` folded over the scope it sits in,
    /// whose family slot is the chart's whole total family style.
    pub placed: &'a Style,
    /// What it bound to, which a callout's natural height wraps.
    drawn: &'a Result<Family, Failure>,
    /// The sizing and padding facts of a drawn plot.
    frame: Option<&'a Frame>,
    /// A drawn plot's authored title, which its title band is laid out from.
    title: Option<&'a str>,
    pub facts: Chart,
    /// The slot height every width gets: the family's estimate, not its card.
    pub estimate: f64,
    /// The canvas a table paints on, which its wrapper records (`ChartIdentity.canvas`); a KPI,
    /// callout and failed chart carry none.
    pub canvas: Option<String>,
}

pub(crate) struct Slots<'a> {
    /// Each drawn chart's slot, by where it is placed: a chart placed twice has two.
    by_placement: BTreeMap<ItemId, Slot<'a>>,
    /// The root's body and each drawn nested board's, by where it sits.
    pub boards: BTreeMap<ItemId, &'a BoardBody>,
    /// The resolved facts of the board's placements, tabs and details.
    pub styles: &'a ChartStyles,
    /// The total style of each tab pane, by the pane's position: a pane has no body.
    panes: BTreeMap<ItemId, &'a Style>,
    /// Whether the root authors its `width:`; an unauthored one hugs its title's and text's width.
    width_authored: bool,
}

/// The total style a board scope draws with: normalize puts one on every body.
pub(crate) fn total_style(body: &BoardBody) -> Result<&Style, Diagnostic> {
    body.style.as_deref().ok_or_else(|| diag::err_internal("a normalized board carries its total style"))
}

pub(crate) fn plan<'a>(board: &'a Board, styles: &'a ChartStyles, bound: &'a bound::Board, facts: &impl EngineFacts) -> Result<Slots<'a>, Diagnostic> {
    let mut slots = Slots { by_placement: BTreeMap::new(), boards: BTreeMap::from([(ItemId::root(), &board.body)]), panes: BTreeMap::new(), styles, width_authored: board.width.is_some() };
    walk(&board.body, &ItemId::root(), board, styles, bound, facts, &mut slots)?;
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
    board: &'a Board,
    styles: &'a ChartStyles,
    bound: &'a bound::Board,
    facts: &impl EngineFacts,
    out: &mut Slots<'a>,
) -> Result<(), Diagnostic> {
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
    if open.is_some() && !out.width_authored {
        // A tabs board hugs the widest of all its tabs, whose closed charts nothing binds.
        return Err(Diagnostic::refused(refuse!("tabs on a board with no authored `width:` are not supported")));
    }
    for placement in items(body, at) {
        if open.is_some_and(|open| placement.pane.first() != Some(&open)) {
            continue;
        }
        if bound.drawn().is_hidden(&placement.id) {
            continue;
        }
        match placement.item {
            LayoutItem::Board(nested) => {
                out.boards.insert(placement.id.clone(), &nested.body);
                walk(&nested.body, &placement.id, board, styles, bound, facts, out)?
            }
            LayoutItem::Chart { id, .. } => {
                let chart = board.body.charts.get(id.as_str()).ok_or_else(|| diag::err_internal(format!("chart {id} is not in the board")))?;
                let drawn = bound.charts().get(&placement.id).ok_or_else(|| diag::err_internal(format!("chart {id} at {} is not bound", placement.id)))?;
                let mut slot = slot(body, chart, styles.at(&placement.id)?, &placement.id, id, drawn, facts.style(body)?)?;
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

fn slot<'a>(body: &'a BoardBody, chart: &'a ModelChart, placed: &'a Style, at: &ItemId, id: &ChartId, bound: &'a bound::Chart, style: &StylePatch) -> Result<Slot<'a>, Diagnostic> {
    let drawn = &bound.family;
    // The words and font of a title above the failed card are measured text; a KPI's label and a
    // callout's title are drawn inside their cards.
    let failed = matches!(drawn, Err(_));
    let (preferred, title, notes, content, table) = match chart {
        ModelChart::Kpi(k) => (Some(placed.charts.kpi.base.preferred_width), failed && k.label.is_some(), &k.base.notes, leaf(style.charts.kpi.default_height, "style.charts.kpi.default_height")?, false),
        ModelChart::Table(t) => (Some(placed.charts.table.preferred_width), failed && t.shared.title.is_some(), &t.shared.base.notes, leaf(style.charts.default_table_height, "style.charts.default_table_height")?, true),
        ModelChart::Callout(c) => (Some(placed.charts.callout.preferred_width), failed && c.title.is_some(), &None, 0.0, false),
        ModelChart::Pie(c) | ModelChart::Donut(c) => (None, failed && c.radial.shared.title.is_some(), &c.radial.shared.base.notes, 0.0, false),
        ModelChart::SparkBar(s) if matches!(drawn, Ok(Family::SparkBar(_))) => {
            let content = match drawn {
                Ok(Family::SparkBar(spark)) => spark_bar::natural_height(spark, &placed.charts.spark_bar),
                _ => return Err(diag::err_internal("a drawn spark_bar chart binds to a spark_bar body")),
            };
            (Some(placed.charts.spark_bar.preferred_width), false, &s.shared.base.notes, content, false)
        }
        ModelChart::Scatter(s) if matches!(drawn, Ok(Family::Cartesian(_))) => (Some(placed.charts.scatter.cartesian.painted.base.preferred_width), false, &s.cartesian.shared.base.notes, 0.0, false),
        ModelChart::Histogram(h) if matches!(drawn, Ok(Family::Cartesian(_))) => (Some(placed.charts.histogram.cartesian.painted.base.preferred_width), false, &h.cartesian.shared.base.notes, 0.0, false),
        _ if matches!(drawn, Ok(Family::Cartesian(bound::Cartesian::Plane(_)))) => return Err(Diagnostic::refused(refuse!("sizing the slot of the plot of chart {id} is not supported"))),
        _ => return Err(Diagnostic::refused(refuse!("sizing the slot of a failed {id} chart of this family is not supported"))),
    };
    if title {
        return Err(Diagnostic::refused(refuse!("drawing the title of a failed chart is not supported")));
    }
    if matches!(chart, ModelChart::Scatter(ScatterChart { cartesian, .. }) | ModelChart::Histogram(BarChart { cartesian, .. }) if cartesian.width.is_some()) {
        return Err(Diagnostic::refused(refuse!("a cartesian chart's `width:` is not supported")));
    }
    if notes.is_some() {
        return Err(Diagnostic::refused(refuse!("a chart's notes are not supported")));
    }
    if matches!(drawn, Ok(Family::Kpi(_)) | Ok(Family::Pie(_))) && !style.formats.is_empty() {
        return Err(Diagnostic::refused(refuse!("a board's `style.formats` is not supported")));
    }
    let card_padding = leaf(style.frame.card_padding, "style.frame.card_padding")?;
    let padding = match chart {
        ModelChart::Kpi(k) if !failed => (placed.charts.kpi.base.padding.top, placed.charts.kpi.base.padding.bottom),
        ModelChart::Table(_) if !failed => (placed.charts.table.padding.top, placed.charts.table.padding.bottom),
        ModelChart::SparkBar(_) => (placed.charts.spark_bar.padding.top, placed.charts.spark_bar.padding.bottom),
        ModelChart::Pie(_) | ModelChart::Donut(_) if !failed => (placed.charts.pie.base.padding.top, placed.charts.pie.base.padding.bottom),
        ModelChart::Scatter(_) | ModelChart::Histogram(_) => {
            let frame = bound.frame.as_ref().ok_or_else(|| diag::err_internal("a plot carries its frame"))?;
            (frame.padding.top, frame.padding.bottom)
        }
        _ => (leaf(style.charts.padding.top, "style.charts.padding.top")?, leaf(style.charts.padding.bottom, "style.charts.padding.bottom")?),
    };
    let width = IntrinsicWidth { min: None, preferred };
    let pie = matches!(drawn, Ok(Family::Pie(_)));
    let (content_sized, spare) = match (chart, bound.frame.as_ref()) {
        (ModelChart::Scatter(_) | ModelChart::Histogram(_), Some(frame)) => plot_sizing(&frame.sizing),
        _ if pie => (false, Spare::Never),
        _ => (true, Spare::WhenAlone),
    };
    Ok(Slot {
        body,
        chart,
        placed,
        drawn,
        frame: bound.frame.as_ref(),
        title: bound.title.as_deref(),
        // kpi, table and callout draw to their content and take spare height only alone
        // (`NON_ASPECT_RATIO_TYPES`, `_takes_spare_height`).
        facts: Chart { at: at.clone(), id: id.as_str().to_string(), width: None, intrinsic: width, without_data: width, content_sized, table, spare, floor: None },
        estimate: content + 2.0 * card_padding + padding.0 + padding.1,
        canvas: None,
    })
}

/// `pie_content_height` plus the padding around it (`pie_box`) for a drawn pie: the placement's pie
/// aspect ratio. The family's `min_height` and `max_height` are not read: production's sizing reads
/// `chart_defaults` alone.
pub(crate) fn pie_height(slot: &Slot, style: &StylePatch, width: f64) -> Result<f64, Diagnostic> {
    let padding = &slot.placed.charts.pie.base.padding;
    pie_slot(slot, slot.placed.charts.pie.aspect_ratio, style, width, (padding.top, padding.bottom))
}

/// A pie whose query failed is slotted at `pie_content_height` inside the card and the board's
/// chart padding (`_unresolved_height`), on the board's pie or donut aspect ratio. The chart's own
/// `style:` is not read here, since the resolved chart that carries it is what is missing.
fn failed_pie_height(slot: &Slot, style: &StylePatch, width: f64) -> Result<f64, Diagnostic> {
    let board = total_style(slot.body)?;
    let donut = matches!(slot.chart, ModelChart::Donut(_));
    let family = board.charts.donut.as_ref().filter(|_| donut).and_then(|patch| patch.aspect_ratio).unwrap_or(board.charts.pie.aspect_ratio);
    pie_slot(slot, family, style, width, (leaf(style.charts.padding.top, "style.charts.padding.top")?, leaf(style.charts.padding.bottom, "style.charts.padding.bottom")?))
}

/// `pie_content_height` and the card padding around it: the chart's own `height`, else the family's
/// aspect ratio (the board's global one when that is 0) at `width`, clamped to the board's height
/// range, else `default_chart_height` when `width` or the ratio is not positive.
fn pie_slot(slot: &Slot, family_aspect: f64, style: &StylePatch, width: f64, (top, bottom): (f64, f64)) -> Result<f64, Diagnostic> {
    let (ModelChart::Pie(chart) | ModelChart::Donut(chart)) = slot.chart else {
        return Err(diag::err_internal("a pie's chart is a pie or donut"));
    };
    let aspect = if family_aspect != 0.0 { family_aspect } else { total_style(slot.body)?.charts.aspect_ratio };
    let card_padding = leaf(style.frame.card_padding, "style.frame.card_padding")?;
    let content = if let Some(height) = chart.radial.height {
        height.get()
    } else if width > 0.0 && aspect > 0.0 {
        let (min, max) = (leaf(style.charts.min_height, "style.charts.min_height")?, leaf(style.charts.max_height, "style.charts.max_height")?);
        min.max(max.min(width / aspect))
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

impl<F: EngineFacts> Facts for Adapter<'_, '_, F> {
    fn style(&self, body: &BoardBody) -> Result<&StylePatch, Diagnostic> {
        self.facts.style(body)
    }

    fn own(&mut self, body: &BoardBody, root: bool) -> Result<Own, Diagnostic> {
        if !root && !body.variables.is_empty() {
            return Err(Diagnostic::refused(refuse!("a nested board's variables are not supported")));
        }
        if (title_text(body).is_some() || body.text.is_some()) && !root {
            return Err(Diagnostic::refused(refuse!("laying out a nested board's title or text is not supported")));
        }
        let own = bands::own(root, total_style(body)?, self.book)?;
        // An authored `width:` ignores a board's own content; a hugged board takes its title's and text's.
        if self.slots.width_authored || (title_text(body).is_none() && body.text.is_none()) {
            return Ok(own);
        }
        let ask = prose::own_width(total_style(body)?, title_text(body), body.text.as_ref().map(|text| text.as_str()), self.facts.fonts_dir())?;
        Ok(Own { width: own.width.combine_max(ask), ..own })
    }

    fn root_band(&mut self, body: &BoardBody, content_width: f64) -> Result<f64, Diagnostic> {
        let Some(title) = title_text(body) else { return Ok(0.0) };
        let style = total_style(body)?;
        let width = (content_width - 2.0 * style.frame.card_padding).max(0.0);
        Ok(prose::title(style, title, self.facts.fonts_dir(), width)?.height)
    }

    fn chart(&mut self, at: &ItemId, _id: &ChartId) -> Result<Chart, Diagnostic> {
        Ok(self.slots.get(at)?.facts.clone())
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

    fn natural(&mut self, chart: &Chart, width: f64) -> Result<NaturalPlan, Diagnostic> {
        // `plan` filled a slot for every placement the layout asks about.
        let slot = &self.slots.by_placement[&chart.at];
        let height = match (slot.drawn, slot.chart) {
            // A callout's slot is its card: `_build_height_provider` renders it at the slot's width and
            // takes its natural height, with no card padding.
            (Ok(Family::Callout(callout)), ModelChart::Callout(_)) => {
                let style = CalloutStyle::of(&slot.placed.charts.callout, total_style(slot.body)?)?;
                callout::plan(&CalloutCard { callout, style: &style }, self.facts.fonts_dir(), width)?.natural_height
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
                return Ok(NaturalPlan { height: planned.height + top + bottom, title });
            }
            // A pie's slot is its content height (`pie_content_height`) inside the card and chart padding.
            (Ok(Family::Pie(bound_pie)), ModelChart::Pie(_) | ModelChart::Donut(_)) => {
                let style = self.facts.style(slot.body)?;
                let height = pie_height(slot, style, width)?;
                let (left, right, top, _) = additive_padding(leaf(style.frame.card_padding, "style.frame.card_padding")?, &slot.placed.charts.pie.base.padding);
                let id = slot.facts.id.as_str();
                let title = match bound_pie.title.as_deref() {
                    Some(text) => layout::title::chart_title(id, text, slot.placed, width, width - left - right, self.book)?.map(|t| TitleMetrics { ascent: top + t.metrics.ascent, tail: t.metrics.tail }),
                    None => None,
                };
                return Ok(NaturalPlan { height, title });
            }
            (Err(_), ModelChart::Pie(_) | ModelChart::Donut(_)) => failed_pie_height(slot, self.facts.style(slot.body)?, width)?,
            // `get_chart_content_height`: the authored height, else the aspect ratio's at this width
            // clamped, under the card and chart padding the slot carries.
            (Ok(Family::Cartesian(_)), ModelChart::Scatter(_) | ModelChart::Histogram(_)) => {
                let frame = plot_frame(slot)?;
                let height = content_height(&frame.sizing, width)? + slot.estimate;
                let card_padding = leaf(self.facts.style(slot.body)?.frame.card_padding, "style.frame.card_padding")?;
                let (left, right, top) = (card_padding + frame.padding.left, card_padding + frame.padding.right, card_padding + frame.padding.top);
                let title = match slot.title {
                    Some(text) => layout::title::chart_title(slot.facts.id.as_str(), text, slot.placed, width, width - left - right, self.book)?.map(|t| TitleMetrics { ascent: top + t.metrics.ascent, tail: t.metrics.tail }),
                    None => None,
                };
                return Ok(NaturalPlan { height, title });
            }
            _ => slot.estimate,
        };
        Ok(NaturalPlan { height, title: None })
    }
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
