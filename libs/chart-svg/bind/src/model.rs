//! The bound model: bind's output and layout's input (spec § 5.2). Every field is a
//! data fact: no pixel, font or width. Types with an invariant keep their fields
//! private behind a checked constructor, so layout never sees a broken value.

use std::cmp::Ordering;
use std::hash::{Hash, Hasher};

use std::collections::{BTreeMap, BTreeSet};

use board_model::board::{items, Board, BoardBody, LayoutItem};
use board_model::ids::{BoardId, ChartId, ItemId};
use board_model::style::board::{ChartTitleStyle, PaddingStyle};
use board_model::style::legend::{LegendStyle, SideLegendStyle};
use color::Rgba;
use diagnostic::Diagnostic;
use indexmap::IndexMap;

use crate::failed::checked_href;
use crate::formats::ResolvedFormat;
use crate::temporal::TimeUnit;
use crate::time_axis::{Instant, TimeAxis};

/// A bound value that breaks a construction invariant: a bind bug, never a user error.
#[derive(Debug, Clone, PartialEq)]
pub enum Invalid {
    Extent { lo: f64, hi: f64 },
    Month(u8),
    BandLength { keys: usize, text: usize },
    BandDuplicate(Key),
    GridDuplicate(Key),
    /// A grid that facets on neither rows nor columns.
    GridNoAxis,
    RungOutside { value: f64 },
    GridCells { expected: usize, got: usize },
    /// A grid's panel ids are not `0, 1, 2, …` in cell order.
    GridPanels,
    NoMeasureScale,
    /// A plot with no datum in any layer or panel: no rows is [`Body::Empty`], the one empty path.
    EmptyPlot,
    /// A point layer on a plot whose mark style walked no point mark.
    PointsUnstyled { layer: usize },
    /// A layer reads a measure scale `y` does not hold.
    LayerYScale {
        layer: usize,
        y_scale: usize,
        scales: usize,
    },
    /// A datum names a series the plot does not hold.
    DatumSeries {
        layer: usize,
        series: usize,
        series_len: usize,
    },
    /// A panel no layer draws a datum in: an absent facet combination is a `None` grid cell.
    EmptyPanel { panel: usize },
    /// A layer holds a different number of panels than the plot's [`Panels`].
    LayerPanels {
        layer: usize,
        expected: usize,
        got: usize,
    },
    /// A datum's numeric x, base or value is NaN or infinite; `datum` indexes its layer's datums
    /// across panels, in order.
    NonFinite {
        layer: usize,
        datum: usize,
    },
    /// A datum's position is not of the kind the x scale's [`ScaleKind`] takes.
    PosKind {
        layer: usize,
    },
    /// A band position past the end of the x scale's domain.
    PosOutside {
        layer: usize,
        index: usize,
        len: usize,
    },
    /// The rail, a top-legend entry or a legend entry names a series the plot does not hold.
    NamingSeries {
        series: usize,
        series_len: usize,
    },
    /// A failed chart's failure names another chart than the one placed where it is bound.
    FailureChart { key: ItemId, chart: ChartId },
    /// A chart's frame is present exactly when its body is a [`Body::Plot`] or [`Body::Empty`].
    ChartFrame(ItemId),
    /// A drawn placement with no bound chart.
    DrawnUnbound(ItemId),
    /// A bound chart at a place no drawn item holds.
    BoundUndrawn(ItemId),
    /// The bound charts are the drawn placements, but not in board order.
    ChartOrder,
    /// A tabs layout written directly in a tab pane: its open tab has no key in a
    /// [`DrawnSet`], which keys open tabs by the body that arranges them.
    NestedTabs(ItemId),
}

/// A board's drawn set, its bound charts and its data diagnostics.
#[derive(Debug)]
pub struct BoundBoard {
    drawn: DrawnSet,
    charts: IndexMap<ItemId, BoundChart>,
    /// Data WARNs; layout appends its own.
    pub diagnostics: Vec<Diagnostic>,
}

impl BoundBoard {
    /// `charts` holds one bound chart per placement `drawn` draws on `board`, in board order;
    /// a failure must name the chart placed at its key.
    pub fn new(
        board: &Board,
        drawn: DrawnSet,
        charts: IndexMap<ItemId, BoundChart>,
        diagnostics: Vec<Diagnostic>,
    ) -> Result<Self, Invalid> {
        let placed = drawn.charts(board)?;
        if let Some((at, _)) = placed.iter().find(|(at, _)| !charts.contains_key(at)) {
            return Err(Invalid::DrawnUnbound(at.clone()));
        }
        if let Some(at) = charts.keys().find(|at| !placed.iter().any(|(drawn_at, _)| drawn_at == *at)) {
            return Err(Invalid::BoundUndrawn(at.clone()));
        }
        for (key, bound) in &charts {
            if let Body::Failed(failure) = &bound.body {
                if placed.iter().any(|(at, chart)| at == key && **chart != failure.chart) {
                    return Err(Invalid::FailureChart { key: key.clone(), chart: failure.chart.clone() });
                }
            }
            if bound.frame.is_some() != matches!(bound.body, Body::Plot(_) | Body::Empty) {
                return Err(Invalid::ChartFrame(key.clone()));
            }
        }
        if !charts.keys().eq(placed.iter().map(|(at, _)| at)) {
            return Err(Invalid::ChartOrder);
        }
        Ok(Self { drawn, charts, diagnostics })
    }

    pub fn drawn(&self) -> &DrawnSet {
        &self.drawn
    }

    pub fn charts(&self) -> &IndexMap<ItemId, BoundChart> {
        &self.charts
    }
}

/// What a board draws, as facts over its board-model layout: the items whose `visible:`
/// came out false, the open tab of each tabs layout and the state of each details
/// board, by their owning body. Layout walks board-model's own tree
/// ([`board_model::board::items`]) and looks each item up.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct DrawnSet {
    hidden: BTreeSet<ItemId>,
    active_tab: BTreeMap<BoardId, usize>,
    details_open: BTreeMap<BoardId, bool>,
}

impl DrawnSet {
    pub fn new(
        hidden: BTreeSet<ItemId>,
        active_tab: BTreeMap<BoardId, usize>,
        details_open: BTreeMap<BoardId, bool>,
    ) -> Self {
        Self { hidden, active_tab, details_open }
    }

    /// Whether the item at `id` takes no space: nothing of it is bound or measured.
    pub fn is_hidden(&self, id: &ItemId) -> bool {
        self.hidden.contains(id)
    }

    /// The open tab of the tabs layout `body` arranges.
    pub fn active_tab(&self, body: &BoardId) -> Option<usize> {
        self.active_tab.get(body).copied()
    }

    /// Whether the details board `body` is expanded.
    pub fn details_open(&self, body: &BoardId) -> Option<bool> {
        self.details_open.get(body).copied()
    }

    /// Every drawn placement on `board` with the chart it shows, in layout order; a chart placed
    /// twice is listed twice. An item in a tab pane draws only in its body's open tab,
    /// and a details board's items only while it is open.
    pub fn charts<'a>(&self, board: &'a Board) -> Result<Vec<(ItemId, &'a ChartId)>, Invalid> {
        let mut out = Vec::new();
        self.collect(&board.body, &ItemId::root(), &mut out)?;
        Ok(out)
    }

    fn collect<'a>(&self, body: &'a BoardBody, at: &ItemId, out: &mut Vec<(ItemId, &'a ChartId)>) -> Result<(), Invalid> {
        if body.details.is_some() && self.details_open(&body.id) != Some(true) {
            return Ok(());
        }
        for placement in items(body, at) {
            let in_open_tab = match placement.pane.as_slice() {
                [] => true,
                [tab] => self.active_tab(&body.id) == Some(*tab),
                _ => return Err(Invalid::NestedTabs(placement.id)),
            };
            if !in_open_tab || self.is_hidden(&placement.id) {
                continue;
            }
            match placement.item {
                LayoutItem::Chart { id, .. } => out.push((placement.id, id)),
                LayoutItem::Board(nested) => self.collect(&nested.body, &placement.id, out)?,
            }
        }
        Ok(())
    }
}

/// A chart as bound: the title text and frame facts its drawn body sits in.
#[derive(Debug)]
pub struct BoundChart {
    pub title: Option<String>,
    pub subtitle: Option<String>,
    /// `Some` for a [`Body::Plot`] or [`Body::Empty`], `None` otherwise: the error card draws
    /// from the error tile and a KPI or callout card from the style its caller resolves, not
    /// from a chart frame. [`BoundBoard::new`] checks it.
    pub frame: Option<Frame>,
    pub body: Body,
}

/// A chart's canvas, walked frame style and the sizing facts its natural height is computed from.
#[derive(Debug, Clone, PartialEq)]
pub struct Frame {
    /// The chart's background composited over the board's: the ground label ink is judged on.
    pub canvas: Rgba,
    pub padding: PaddingStyle,
    /// Title and subtitle styles; the width-tier offsets are part of it, layout picks the tier.
    pub title: ChartTitleStyle,
    pub sizing: Sizing,
}

/// The authored and styled height facts of a chart: the height when authored, else
/// `clamp(width / aspect_ratio, min_height, max_height)`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Sizing {
    pub height: Option<f64>,
    pub aspect_ratio: f64,
    pub min_height: f64,
    pub max_height: f64,
}

/// A chart's body: what its family bound, or the error card it draws instead.
#[derive(Debug)]
pub enum Body {
    /// bar, line, area, scatter, histogram, heatmap.
    Plot(Box<Plot>),
    Kpi(Kpi),
    Callout(Callout),
    /// A family that draws marks, bound over no rows: its title and frame around a muted
    /// "No data" in the plot box. It has no scales, series or legend, so a family never
    /// invents a domain for nothing.
    Empty,
    Failed(Failure),
}

/// A KPI card's data: the headline in the two faces it draws in, and its label.
#[derive(Debug, Clone, PartialEq)]
pub struct Kpi {
    /// The number, its sign included.
    pub number: String,
    /// The magnitude word set beside the number in the affix face; empty for an exact number.
    pub suffix: String,
    /// The label text; an empty label is `None`.
    pub label: Option<String>,
}

/// A callout card's text, as authored markdown.
#[derive(Debug, Clone, PartialEq)]
pub struct Callout {
    /// The title; an empty title is `None`.
    pub title: Option<String>,
    pub message: String,
}

/// Execute error, bind refusal or no data: the card's text is the diagnostic's message.
#[derive(Debug)]
pub struct Failure {
    /// The chart the error belongs to.
    pub chart: ChartId,
    pub diagnostic: Diagnostic,
}

/// The bar orientation law's verdict (RL §1d); every other family is vertical.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Orient {
    Vertical,
    Horizontal,
}

/// The six cartesian families' shared frame: scales, panels, the one series list and the
/// mark layers drawn over them.
#[derive(Debug)]
pub struct Plot {
    orient: Orient,
    x: Scale,
    y: Vec<Scale>,
    panels: Panels,
    series: Vec<Series>,
    layers: Vec<MarkLayer>,
    naming: Naming,
    legends: Vec<Legend>,
    support: Option<SupportStrip>,
    baselines: Baselines,
    axis_styles: AxisStyles,
    mark_style: MarkStyle,
}

/// A [`Plot`] before its invariants are checked.
#[derive(Debug)]
pub struct PlotParts {
    pub orient: Orient,
    pub x: Scale,
    /// `y[0]` is the shared measure scale; more only for pinned layer axes.
    pub y: Vec<Scale>,
    pub panels: Panels,
    pub series: Vec<Series>,
    /// Base layer first, then authored order: paint order.
    pub layers: Vec<MarkLayer>,
    pub naming: Naming,
    pub legends: Vec<Legend>,
    pub support: Option<SupportStrip>,
    pub baselines: Baselines,
    pub axis_styles: AxisStyles,
    pub mark_style: MarkStyle,
}

impl Plot {
    /// Every layer reads a scale in `y`, holds one datum list per panel, and every datum
    /// names a series and sits, at finite numbers, where the x scale can place it; the rail and top legend name
    /// series the plot holds. A plot with no datum is [`Body::Empty`], not a `Plot`, and every
    /// panel is drawn in by some layer. A row whose value is null is still a datum (`defined:
    /// false`): "empty" means no rows.
    pub fn new(parts: PlotParts) -> Result<Self, Invalid> {
        let PlotParts {
            orient,
            x,
            y,
            panels,
            series,
            layers,
            naming,
            legends,
            support,
            baselines,
            axis_styles,
            mark_style,
        } = parts;
        if y.is_empty() {
            return Err(Invalid::NoMeasureScale);
        }
        let panel_count = panels.count();
        for (layer, mark_layer) in layers.iter().enumerate() {
            if mark_layer.y_scale >= y.len() {
                return Err(Invalid::LayerYScale {
                    layer,
                    y_scale: mark_layer.y_scale,
                    scales: y.len(),
                });
            }
            if matches!(mark_layer.mark, Mark::Points { .. }) && mark_style.points.is_none() {
                return Err(Invalid::PointsUnstyled { layer });
            }
            if mark_layer.panels.len() != panel_count {
                return Err(Invalid::LayerPanels {
                    layer,
                    expected: panel_count,
                    got: mark_layer.panels.len(),
                });
            }
            for (index, datum) in mark_layer.panels.iter().flatten().enumerate() {
                let x_finite = !matches!(datum.x, Pos::Num(x) if !x.is_finite());
                if !(x_finite && datum.y0.is_finite() && datum.y1.is_finite()) {
                    return Err(Invalid::NonFinite { layer, datum: index });
                }
                if datum.series >= series.len() {
                    return Err(Invalid::DatumSeries {
                        layer,
                        series: datum.series,
                        series_len: series.len(),
                    });
                }
                x.kind.place(&datum.x, layer)?;
            }
        }
        if layers
            .iter()
            .all(|layer| layer.panels.iter().all(Vec::is_empty))
        {
            return Err(Invalid::EmptyPlot);
        }
        if let Some(panel) = (0..panel_count).find(|panel| layers.iter().all(|layer| layer.panels[*panel].is_empty())) {
            return Err(Invalid::EmptyPanel { panel });
        }
        let in_legends = legends.iter().flat_map(|legend| &legend.entries);
        let named = naming.entries.iter().chain(in_legends).map(|entry| entry.series).chain(
            naming
                .rail
                .iter()
                .flat_map(|rail| rail.ends.iter().map(|end| end.series)),
        );
        if let Some(outside) = named.into_iter().find(|index| *index >= series.len()) {
            return Err(Invalid::NamingSeries {
                series: outside,
                series_len: series.len(),
            });
        }
        Ok(Self {
            orient,
            x,
            y,
            panels,
            series,
            layers,
            naming,
            legends,
            support,
            baselines,
            axis_styles,
            mark_style,
        })
    }

    pub fn orient(&self) -> Orient {
        self.orient
    }

    /// The position (category) axis.
    pub fn x(&self) -> &Scale {
        &self.x
    }

    pub fn y(&self) -> &[Scale] {
        &self.y
    }

    pub fn panels(&self) -> &Panels {
        &self.panels
    }

    pub fn series(&self) -> &[Series] {
        &self.series
    }

    pub fn layers(&self) -> &[MarkLayer] {
        &self.layers
    }

    pub fn naming(&self) -> &Naming {
        &self.naming
    }

    pub fn legends(&self) -> &[Legend] {
        &self.legends
    }

    pub fn support(&self) -> Option<&SupportStrip> {
        self.support.as_ref()
    }

    pub fn baselines(&self) -> &Baselines {
        &self.baselines
    }

    pub fn axis_styles(&self) -> &AxisStyles {
        &self.axis_styles
    }

    pub fn mark_style(&self) -> &MarkStyle {
        &self.mark_style
    }
}

/// One mark kind drawn over the plot's scales, with its datums per panel.
#[derive(Debug)]
pub struct MarkLayer {
    pub mark: Mark,
    /// Index into [`Plot::y`].
    pub y_scale: usize,
    /// Per panel (in [`PanelId`] order, one for [`Panels::Single`]), the drawn datums in paint order.
    pub panels: Vec<Vec<Datum>>,
}

/// A family contributes its variant and the laws that fill it.
#[derive(Debug, Clone, PartialEq)]
pub enum Mark {
    Points {
        shape: Shape,
        size: Option<SizeScale>,
    },
    /// One bar per datum, from `y0` to `y1` at its band position.
    Bars,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Shape {
    Circle,
}

/// A point mark's size channel; it has no leaves yet.
#[derive(Debug, Clone, PartialEq)]
pub struct SizeScale {}

/// One drawn mark in data space.
#[derive(Debug, Clone)]
pub struct Datum {
    pub x: Pos,
    /// The base of a stacked or spanned mark; 0 for plain marks.
    pub y0: f64,
    pub y1: f64,
    /// Index into [`Plot::series`].
    pub series: usize,
    pub row: RowRef,
    /// Value label text, formatted.
    pub label: Option<String>,
    pub tooltip: Tooltip,
    pub href: Option<Href>,
    /// A null row breaks a line run; an absent row does not.
    pub defined: bool,
}

/// A link target that passed production's scheme check; the only way to build one is [`Href::new`].
#[derive(Debug, Clone, PartialEq)]
pub struct Href(String);

impl Href {
    pub fn new(href: &str) -> Result<Self, Diagnostic> {
        checked_href(href)?;
        Ok(Self(href.to_string()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// A datum's position on the x scale.
#[derive(Debug, Clone)]
pub enum Pos {
    /// Index into a band or point scale's domain.
    Band(usize),
    Num(f64),
    Time(Instant),
}

/// The row of the chart's dataset a datum draws, by index.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RowRef(pub usize);

/// A datum's hover entries, formatted.
#[derive(Debug, Clone, PartialEq)]
pub struct Tooltip {
    pub entries: Vec<TooltipEntry>,
}

impl Tooltip {
    /// The hover text a mark carries as its `aria-label`: each row's `title: value`, joined `; `.
    pub fn text(&self) -> String {
        self.entries.iter().map(|entry| format!("{}: {}", entry.title, entry.value)).collect::<Vec<_>>().join("; ")
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct TooltipEntry {
    pub title: String,
    pub value: String,
}

/// The ways a chart can name its series on or around the plot; layout chooses among them.
#[derive(Debug, Clone, PartialEq)]
pub struct Naming {
    /// The last value per series with its text, after the data disqualifiers.
    pub rail: Option<Rail>,
    /// The top-legend entries.
    pub entries: Vec<LegendEntry>,
    /// The family's rule: it wants a top strip of entries.
    pub asks_top_strip: bool,
    /// The walked legend style; its `auto` leaves are layout's to decide.
    pub authored: AuthoredLegend,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Rail {
    pub ends: Vec<RailEnd>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RailEnd {
    /// Index into [`Plot::series`].
    pub series: usize,
    pub text: String,
}

/// The legend style a family walked, in board-model's own type for that family: line, area and
/// bar may name series at their endpoints, the others place a legend on a side only.
#[derive(Debug, Clone, PartialEq)]
pub enum AuthoredLegend {
    Endpoints(LegendStyle),
    Side(SideLegendStyle),
}

/// A legend: its entries ordered and labeled.
#[derive(Debug, Clone, PartialEq)]
pub struct Legend {
    pub title: Option<String>,
    pub entries: Vec<LegendEntry>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LegendEntry {
    /// Index into [`Plot::series`].
    pub series: usize,
    pub label: String,
}

/// The per-series table under the x axis; it has no cells yet.
#[derive(Debug, Clone, PartialEq)]
pub struct SupportStrip {}

/// Which reference lines fire.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Baselines {
    pub zero: bool,
    pub unity: bool,
    pub x_threshold: bool,
}

/// The walked axis slots, one per drawn axis; it has no leaves yet.
#[derive(Debug, Clone, PartialEq)]
pub struct AxisStyles {}

/// The walked mark, label and legend slots; only the point mark has leaves yet. A plot with a
/// point layer always carries its point mark.
#[derive(Debug, Clone, PartialEq)]
pub struct MarkStyle {
    pub points: Option<PointStyle>,
}

/// A point mark's walked leaves, as production draws them.
#[derive(Debug, Clone, PartialEq)]
pub struct PointStyle {
    /// The symbol's area in square pixels.
    pub size: f64,
    pub opacity: f64,
    /// The outline width; 0 draws none, as an `auto` width does.
    pub stroke_width: f64,
}

#[derive(Debug)]
pub struct Scale {
    pub kind: ScaleKind,
    pub title: Option<String>,
}

#[derive(Debug)]
pub enum ScaleKind {
    /// Bars, heatmap rows and columns.
    Band(BandDomain),
    /// Nominal x on line and scatter.
    Point(BandDomain),
    Linear(ValueAxis),
    Log(ValueAxis),
    Time(TimeDomain),
}

impl ScaleKind {
    /// Whether `pos` is a position this scale places.
    fn place(&self, pos: &Pos, layer: usize) -> Result<(), Invalid> {
        match (self, pos) {
            (ScaleKind::Band(domain) | ScaleKind::Point(domain), Pos::Band(index)) => {
                if *index < domain.keys.len() {
                    Ok(())
                } else {
                    Err(Invalid::PosOutside {
                        layer,
                        index: *index,
                        len: domain.keys.len(),
                    })
                }
            }
            (ScaleKind::Linear(_) | ScaleKind::Log(_), Pos::Num(_))
            | (ScaleKind::Time(_), Pos::Time(_)) => Ok(()),
            _ => Err(Invalid::PosKind { layer }),
        }
    }
}

/// Distinct keys in their final order (the band order law), each with its display text.
#[derive(Debug, Clone, PartialEq)]
pub struct BandDomain {
    keys: Vec<Key>,
    text: Vec<String>,
}

impl BandDomain {
    pub fn new(keys: Vec<Key>, text: Vec<String>) -> Result<Self, Invalid> {
        if keys.len() != text.len() {
            return Err(Invalid::BandLength { keys: keys.len(), text: text.len() });
        }
        if let Some(key) = duplicate(&keys) {
            return Err(Invalid::BandDuplicate(key));
        }
        Ok(Self { keys, text })
    }

    pub fn keys(&self) -> &[Key] {
        &self.keys
    }

    pub fn text(&self) -> &[String] {
        &self.text
    }
}

fn duplicate(keys: &[Key]) -> Option<Key> {
    let mut sorted: Vec<&Key> = keys.iter().collect();
    sorted.sort();
    sorted.windows(2).find(|pair| pair[0] == pair[1]).map(|pair| pair[0].clone())
}

/// A value scale: its domain and the format its rungs, ticks and labels print through. Band and
/// point scales print their domain text and a time scale its grain's format, so only these carry one.
#[derive(Debug, Clone, PartialEq)]
pub struct ValueAxis {
    pub domain: ValueDomain,
    pub format: ResolvedFormat,
}

/// A value axis as data: layout reads one domain, with the exact bounds the axis pins.
#[derive(Debug, Clone, PartialEq)]
pub struct ValueDomain {
    /// The shared-scale union: base, unpinned layers, annotations.
    pub extent: Extent,
    pub zero: Zero,
    /// The headroom the pins apply; a log axis applies none, so it is 0.
    pub headroom: f64,
    pub authored: Option<Authored>,
    pub ladder: Ladder,
    /// Exact lower bound the scale takes instead of rounding; `None` leaves it to the scale.
    pub min: Option<Pin>,
    /// Exact upper bound, likewise.
    pub max: Option<Pin>,
    /// The scale's final bounds, as layout uses them: niced, pinned or authored. Layout
    /// picks `BySize` rungs inside them and never nices.
    pub bounds: Bounds,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Bounds {
    pub extent: Extent,
    pub reason: BoundsReason,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BoundsReason {
    /// d3's nice at count 10 over the extent below: the default with no pin, no authored
    /// domain, no stack and no vertical bar, or an authored `scale.nice: true` over any.
    Niced,
    /// The data's own extent (zero included when anchored), not niced: a vertical bar,
    /// or an authored `scale.nice: false`.
    Fitted,
    /// A pinned end stands as is; the other end is the data's (or zero), not niced.
    Pinned,
    /// `scale.domain`, as authored.
    Authored,
    /// A stack's own extent, not niced.
    Stacked,
    /// A data-fitted extent clear of zero, niced at the axis's tick count and pushed one step
    /// past a data value on a non-zero edge (`plot::domain::nice_clear_of_data`).
    ClearOfData,
}

/// An authored `scale.domain`. `extent` is sorted; `reversed` keeps the author's direction.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Authored {
    pub extent: Extent,
    pub reversed: bool,
}

/// An exact domain bound and why it is pinned.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Pin {
    pub value: f64,
    pub reason: PinReason,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PinReason {
    /// The data edge expanded by `scale.headroom` (0 pins the data edge itself).
    Headroom,
    /// A mixed-sign axis whose format step floor would round the bottom rung below the data.
    FormatFloor,
    /// A zero stack's top, flush with its tallest total.
    FlushTop,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Zero {
    Anchored,
    Free,
    /// `scale.zero` authored: `true` includes zero, `false` excludes it.
    Pinned(bool),
}

/// The data half of the tick ladder; layout prints nothing the ladder does not name.
#[derive(Debug, Clone, PartialEq)]
pub enum Ladder {
    /// `scale.values`, verbatim.
    Authored(Vec<Rung>),
    /// `ticks.count` set (or `"auto"` = 6): size-free.
    Fixed(FixedLadder),
    /// `ticks.count` unset: layout picks the count from the plot length.
    BySize { min_step: Option<f64> },
    /// No data-chosen ticks: log axes, independent multiples.
    None,
}

/// A fixed tick ladder: every rung inside its bounds.
#[derive(Debug, Clone, PartialEq)]
pub struct FixedLadder {
    rungs: Vec<Rung>,
    bounds: Extent,
}

impl FixedLadder {
    pub fn new(rungs: Vec<Rung>, bounds: Extent) -> Result<Self, Invalid> {
        if let Some(rung) = rungs.iter().find(|rung| !(bounds.lo..=bounds.hi).contains(&rung.value)) {
            return Err(Invalid::RungOutside { value: rung.value });
        }
        Ok(Self { rungs, bounds })
    }

    pub fn rungs(&self) -> &[Rung] {
        &self.rungs
    }

    pub fn bounds(&self) -> Extent {
        self.bounds
    }
}

/// A tick value and its text, printed by the ruler law (RL §3f).
#[derive(Debug, Clone, PartialEq)]
pub struct Rung {
    pub value: f64,
    pub text: String,
}

/// A closed, finite numeric range.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Extent {
    lo: f64,
    hi: f64,
}

impl Extent {
    pub fn new(lo: f64, hi: f64) -> Result<Self, Invalid> {
        if lo.is_finite() && hi.is_finite() && lo <= hi {
            Ok(Self { lo, hi })
        } else {
            Err(Invalid::Extent { lo, hi })
        }
    }

    pub fn lo(self) -> f64 {
        self.lo
    }

    pub fn hi(self) -> f64 {
        self.hi
    }
}

#[derive(Debug)]
pub struct TimeDomain {
    /// Each x parsed once.
    pub axis: TimeAxis,
    /// The detected grain, or the authored one. `None` is production's continuous temporal
    /// axis: no grain detected (fewer than two distinct dates, or no calendar cadence).
    pub unit: Option<TimeUnit>,
    /// After the calendar gap fill (`axis_x.fill`).
    pub buckets: Vec<Instant>,
    pub fiscal_start: Option<Month>,
}

/// A calendar month, 1 through 12.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Month(u8);

impl Month {
    pub fn new(month: u8) -> Result<Self, Invalid> {
        if (1..=12).contains(&month) {
            Ok(Self(month))
        } else {
            Err(Invalid::Month(month))
        }
    }

    pub fn get(self) -> u8 {
        self.0
    }
}

/// One decoded cell, the value a category, band or panel is keyed by.
///
/// Equality and order are by value within a variant; across variants the order is
/// the declaration order and nothing is equal (the declared column type picks one
/// variant per column, so `Int(1)` and `Num(1.0)` never meet). A float or decimal and a
/// date are their wire text, production's partition identity (a value's canonical JSON):
/// `2` and `2.0` are two keys, as are two spellings of one instant (`2024-01-01T00:00:00Z`
/// and `2024-01-01T00:00:00+00:00`). They order by value, then text.
#[derive(Debug, Clone)]
pub enum Key {
    Null,
    Bool(bool),
    Int(i64),
    Num { value: f64, text: String },
    Text(String),
    Date { instant: Instant, text: String },
}

impl Key {
    fn rank(&self) -> u8 {
        match self {
            Key::Null => 0,
            Key::Bool(_) => 1,
            Key::Int(_) => 2,
            Key::Num { .. } => 3,
            Key::Text(_) => 4,
            Key::Date { .. } => 5,
        }
    }
}

fn cmp_num(a: f64, b: f64) -> Ordering {
    a.partial_cmp(&b).unwrap_or_else(|| a.is_nan().cmp(&b.is_nan()))
}

impl Ord for Key {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Key::Bool(a), Key::Bool(b)) => a.cmp(b),
            (Key::Int(a), Key::Int(b)) => a.cmp(b),
            (Key::Num { value: a, text: x }, Key::Num { value: b, text: y }) => cmp_num(*a, *b).then_with(|| x.cmp(y)),
            (Key::Text(a), Key::Text(b)) => a.cmp(b),
            (Key::Date { instant: a, text: x }, Key::Date { instant: b, text: y }) => {
                cmp_num(a.at, b.at).then_with(|| x.cmp(y))
            }
            _ => self.rank().cmp(&other.rank()),
        }
    }
}

impl PartialOrd for Key {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl PartialEq for Key {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}

impl Eq for Key {}

impl Hash for Key {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.rank().hash(state);
        match self {
            Key::Null => {}
            Key::Bool(b) => b.hash(state),
            Key::Int(i) => i.hash(state),
            Key::Num { text, .. } => text.hash(state),
            Key::Text(s) => s.hash(state),
            Key::Date { text, .. } => text.hash(state),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Series {
    pub key: SeriesKey,
    /// `field_labels`, humanized wide labels.
    pub display: String,
    pub paint: Paint,
    /// The label ink companion of `paint`, readable on the chart's canvas.
    pub ink: Rgba,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Paint {
    Solid(Rgba),
}

#[derive(Debug, Clone, PartialEq)]
pub enum SeriesKey {
    /// A color column's raw value.
    Value(Key),
    /// A wide `y` measure's column.
    Measure(String),
    /// A layer's name.
    Layer(String),
}

#[derive(Debug, Clone, PartialEq)]
pub enum Panels {
    Single,
    Grid(Grid),
}

impl Panels {
    /// How many panels a layer holds datums for.
    pub fn count(&self) -> usize {
        match self {
            Panels::Single => 1,
            Panels::Grid(grid) => grid.panel_count(),
        }
    }
}

/// An index into a layer's per-panel lists.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PanelId(pub usize);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Sharing {
    Shared,
    Independent,
}

/// The product of row and column values, row-major; an empty combination's cell is `None`.
/// An axis the chart does not facet on is `None`, so a real null facet value (`Key::Null`)
/// never stands for an absent axis.
#[derive(Debug, Clone, PartialEq)]
pub struct Grid {
    rows: Option<Vec<Key>>,
    cols: Option<Vec<Key>>,
    cells: Vec<Option<PanelId>>,
    x: Sharing,
    y: Sharing,
    mirror: bool,
}

impl Grid {
    /// At least one axis is faceted; row and column keys are distinct; the drawn cells' panel
    /// ids must run `0, 1, 2, …` in cell order.
    pub fn new(
        rows: Option<Vec<Key>>,
        cols: Option<Vec<Key>>,
        cells: Vec<Option<PanelId>>,
        x: Sharing,
        y: Sharing,
        mirror: bool,
    ) -> Result<Self, Invalid> {
        if rows.is_none() && cols.is_none() {
            return Err(Invalid::GridNoAxis);
        }
        let (row_keys, col_keys) = (rows.as_deref().unwrap_or_default(), cols.as_deref().unwrap_or_default());
        if let Some(key) = duplicate(row_keys).or_else(|| duplicate(col_keys)) {
            return Err(Invalid::GridDuplicate(key));
        }
        let span = |axis: &Option<Vec<Key>>| axis.as_ref().map_or(1, Vec::len);
        let expected = span(&rows) * span(&cols);
        if cells.len() != expected {
            return Err(Invalid::GridCells { expected, got: cells.len() });
        }
        if !cells.iter().flatten().enumerate().all(|(i, id)| id.0 == i) {
            return Err(Invalid::GridPanels);
        }
        Ok(Self { rows, cols, cells, x, y, mirror })
    }

    pub fn rows(&self) -> Option<&[Key]> {
        self.rows.as_deref()
    }

    pub fn cols(&self) -> Option<&[Key]> {
        self.cols.as_deref()
    }

    pub fn cells(&self) -> &[Option<PanelId>] {
        &self.cells
    }

    pub fn panel_count(&self) -> usize {
        self.cells.iter().flatten().count()
    }

    pub fn x(&self) -> Sharing {
        self.x
    }

    pub fn y(&self) -> Sharing {
        self.y
    }

    pub fn mirror(&self) -> bool {
        self.mirror
    }
}
