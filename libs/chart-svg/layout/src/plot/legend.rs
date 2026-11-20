//! The side legend of a cartesian plot: production's Vega symbol legend on a left or right strip,
//! one column of entries under an optional title, as `chart-renderer/src/families/legend.rs`
//! lays and draws it (`legend_grid`, `legend_from_widths`, `legend_edge_origin`,
//! `legend_fit_bounds`, `symbol_legend`, and the scatter fit and emission passes for the right strip;
//! cited by function, since a line number moves).
//!
//! What a plot with a side legend carries: bind's [`Legend`] (ordered, labeled, titled; `legend.values`
//! and the title's letter case are bind's) and the walked [`AuthoredLegend`] style, whose `auto`
//! leaves are decided here as `decide_legend_position` and `decide_legend_direction`
//! (`compile/resolve/style/legend_position.py`) decide them: no authored edge and no overlay
//! is the right edge, a side legend is vertical.
//!
//! The strip joins the plot's fit through its far edge alone (`legend_fit_bounds`), so it is one
//! more gutter in [`SideLegend::widen`]: the plot's own axes and the legend each reach past the
//! plot and the wider wins.
//!
//! A point mark's authored `shape` and `filled: false` change the swatch (`Swatch`): the mark's own
//! symbol, a ring over the mark's interior; the strip's rows are then as tall as that symbol reaches
//! (`legend_grid`'s `Glyph` and `Ring` extents). A shape channel draws a legend of its own
//! (`ShapeLegend`, `scatter_shape.rs`: `ShapeLegend`), stacked under the side legend on the right strip
//! (`stack_legends`): [`Legends`] holds both.
//!
//! Refused by name, because production draws them with laws not ported here: a top or bottom
//! strip, a floating or slid legend, a horizontal direction or an authored column count, an
//! authored symbol shape or fill, a hidden legend, a label or title cut at its limit (the hover
//! title), a legend with more entries than `symbolLimit` (the "+N more" fold), one taller than its
//! card (bar's fold), and any swatch but a point mark's. The endpoint rail is refused by the
//! caller: bind binds none yet, and its probe-shrink concat has no port.
//!
//! A legend that hides series (`Legend::toggle`, built by bind where the chart's view can hide a
//! series) makes each label a button: its group carries the board param a click sets and the value it
//! sets, which from all series shown is the one series as a JSON list (`LegendView::clickable`).
//! Nothing is hidden at default variables, so no entry recedes and none is marked hidden.

use bind::legend::SYMBOL_LIMIT;
use bind::bound::{AuthoredLegend, Face, Legend, Mark, Paint, Plane, Shape, Toggle, Unfilled};
use board_model::rule::Auto;
use board_model::style::board::EmojiMode;
use board_model::style::font::{FontSlant, FontStyle, TextDecoration};
use board_model::facet::FontWeightValue;
use board_model::style::legend::{LegendAlign, LegendDirection, LegendEdge, LegendStyleBase};
use d3rs::vega_symbol::{Extents, VegaSymbol};
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::FontBook;
use measure::baseline_shift;
use model_core::Engine;
use scene::{Attrs, Node, Role};

use crate::contract::{legend_attrs, legend_entries_attrs, legend_label_attrs, legend_symbol_attrs, legend_title_attrs};
use crate::geom::{Insets, Size};
use crate::plot::points::miter_limit;
use crate::plot::cartesian::Ink;
use crate::scene_path::scene_path;

/// Vega's default legend `offset` from the anchoring edge (`VEGA_LEGEND_OFFSET`).
pub const OFFSET: f64 = 18.0;
/// Vega's default legend `titlePadding` (`VEGA_LEGEND_TITLE_PADDING`).
const TITLE_PADDING: f64 = 5.0;
/// Vega's `labelLimit` and `titleLimit` when the theme names none (`VEGA_LEGEND_LABEL_LIMIT`,
/// `VEGA_LEGEND_TITLE_LIMIT`).
const LABEL_LIMIT: f64 = 160.0;
const TITLE_LIMIT: f64 = 180.0;
/// A default symbol's size, its side `sqrt(100)`, and `symbolStrokeWidth` (`VEGA_SYMBOL_SIZE`,
/// `VEGA_SYMBOL_STROKE_WIDTH`).
const SYMBOL_SIZE: f64 = 100.0;
const STROKE: f64 = 1.5;
/// `VEGA_ROW_PADDING`.
const ROW_PADDING: f64 = 2.0;

/// The swatch of a point mark's entry: a filled disc with no stroke color
/// (`LegendSymbol::FilledCircle`). A line's disc stroked in its own fill is placed by `line.rs`'s own
/// right-strip rule (it reads the line stroke's overhang), which bind does not carry: refused.
fn points_only(plot: &Plane) -> Result<(), Diagnostic> {
    if plot.layers().iter().all(|layer| matches!(layer.mark, Mark::Points { .. })) {
        Ok(())
    } else {
        Err(Diagnostic::refused("a legend swatch for lines, bars or mixed mark layers is not supported"))
    }
}

/// How far a swatch reaches from the center of its cell, sideways and above and below it: the
/// disc a filled mark's entry draws (`half_extent_x` and `extent_y` of `legend_grid`), or a ring's own
/// symbol and stroke.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Reach {
    x: f64,
    up: f64,
    down: f64,
}

impl Reach {
    /// A filled symbol of the default size: its half side, whatever the symbol (`FilledGlyph`).
    const FILLED: Reach = Reach { x: HALF, up: HALF, down: HALF };

    /// A default-size `symbol` and the stroke around it (`LegendSymbol::Glyph` and `Ring`).
    fn ringed(symbol: VegaSymbol) -> Reach {
        let Extents { x, up, down } = symbol.extents(SYMBOL_SIZE);
        Reach { x: x + STROKE, up: up + STROKE, down: down + STROKE }
    }
}

/// The half extent of a default disc.
const HALF: f64 = 5.0;

/// `legend_grid` over `entries` at `font_size`: per row, its label advance and the reach of its swatch.
#[derive(Debug, Clone, PartialEq)]
pub struct Grid {
    /// The swatch cell's side.
    pub size: f64,
    /// The label's x within the cell: `size + labelOffset`.
    pub label_x: f64,
    /// Each row's top.
    pub rows: Vec<f64>,
    pub height: f64,
    /// The entries' ink width: the widest `label_x + width`, unceiled.
    pub width: f64,
}

/// The cell is the default symbol and its stroke, at least the font size, one entry per row. A row is as
/// tall as its swatch or label text reaches (`y1`, `y2` of `legend_grid`), a row a `ROW_PADDING` and the
/// part of its top above the cell from the last.
pub fn grid(font_size: f64, label_offset: f64, entries: &[(f64, Reach)]) -> Grid {
    let size = (SYMBOL_SIZE.sqrt() + STROKE).ceil().max(font_size);
    let c = size / 2.0;
    let text_top = c + (0.3 * font_size).round() - (0.8 * font_size).round();
    let label_x = size + label_offset;
    if entries.is_empty() {
        return Grid { size, label_x, rows: Vec::new(), height: 0.0, width: 0.0 };
    }
    let behind = |edge: f64| if edge < 0.0 { (-edge).ceil() } else { 0.0 };
    let top = |row: usize| (c - entries[row].1.up).min(text_top);
    let bottom = |row: usize| (c + entries[row].1.down).max(text_top + font_size);
    let mut rows = vec![0.0; entries.len()];
    let mut at = 0.0;
    for row in 1..entries.len() {
        at += ROW_PADDING + behind(top(row)) + bottom(row - 1).ceil();
        rows[row] = at;
    }
    let last = entries.len() - 1;
    let height = (rows[last] + bottom(last)).ceil();
    let width = entries.iter().map(|(width, _)| label_x + width).fold(0.0, f64::max);
    Grid { size, label_x, rows, height, width }
}

/// The legend box: `ceil(max(title, entries))` wide, the entries under a band of
/// `titleFontSize + titlePadding` when titled (`legend_from_widths`).
pub fn boxed(grid: &Grid, title: Option<(f64, f64)>) -> (f64, f64, f64) {
    let (width, band) = title.map_or((0.0, 0.0), |(width, size)| (width, size + TITLE_PADDING));
    (grid.width.max(width).ceil(), band + grid.height, band)
}

/// Which edge the strip sits on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Left,
    Right,
}

/// Why a strip does not lay out; each caller words the ones it can reach.
pub enum Unfit {
    /// A label or the title is wider than its limit, which Vega cuts with an ellipsis.
    Cut,
    Empty,
    /// Taller than the card it was asked to fit.
    TooTall,
    Failed(Diagnostic),
}

impl Unfit {
    /// The refusal the cartesian side legend gives.
    fn side(self) -> Diagnostic {
        match self {
            Unfit::Cut => Diagnostic::refused("a side legend with a label or title cut at its limit is not supported"),
            Unfit::Empty => Diagnostic::refused("a side legend with no entries is not supported"),
            Unfit::TooTall => Diagnostic::refused("a side legend taller than its card is not supported"),
            Unfit::Failed(error) => error,
        }
    }
}

impl From<Diagnostic> for Unfit {
    fn from(error: Diagnostic) -> Self {
        Unfit::Failed(error)
    }
}

/// A laid-out side legend, in plot-local coordinates once placed.
#[derive(Debug, Clone, PartialEq)]
pub struct SideLegend {
    side: Side,
    labels: Ink,
    title: Option<(Ink, String)>,
    entries: Vec<(String, String)>,
    /// Per entry, the board param its click sets and the value it sets (`LegendView::clickable`
    /// while every series is shown); empty for a legend that hides no series.
    clicks: Vec<(String, String)>,
    /// Whether each label carries its series name as its join key.
    stamps_series: bool,
    grid: Grid,
    width: f64,
    band: f64,
    swatch: Swatch,
}

/// The symbol every entry of a side legend draws, in its series' color: a filled mark's own symbol
/// (`LegendSymbol::FilledGlyph`; the disc when none is authored), or, for an unfilled mark, a ring of it
/// over the mark's interior (`LegendSymbol::Ring`).
#[derive(Debug, Clone, PartialEq)]
struct Swatch {
    symbol: VegaSymbol,
    /// The interior and opacity of an unfilled mark's ring.
    ring: Option<(String, f64)>,
}

impl Swatch {
    const DISC: Swatch = Swatch { symbol: VegaSymbol::Circle, ring: None };

    /// The swatch of the plot's point layer, a disc where it has none. A shape channel's own symbols ride
    /// the shape legend (`color_legend_glyph_names`), so a side legend beside one draws the circle.
    fn of(plot: &Plane) -> Swatch {
        let Some((Mark::Points { shape, .. }, Some(style))) = plot.layers().first().map(|layer| (&layer.mark, plot.mark_style().points.as_ref())) else {
            return Swatch::DISC;
        };
        let symbol = match shape {
            Shape::Symbol(symbol) => *symbol,
            Shape::Channel(_) => VegaSymbol::Circle,
        };
        Swatch { symbol, ring: style.unfilled.as_ref().map(|Unfilled { interior, .. }| (interior.clone(), style.opacity)) }
    }

    fn reach(&self) -> Reach {
        match self.ring {
            Some(_) => Reach::ringed(self.symbol),
            None => Reach::FILLED,
        }
    }
}

/// The click of each entry of a legend that hides series: from all shown it narrows to the one series,
/// written as the JSON list `["name"]` (`LegendView::next`).
fn clicks(toggle: &Toggle) -> Vec<(String, String)> {
    toggle.keys.iter().map(|key| (toggle.param.clone(), serde_json::json!([key]).to_string())).collect()
}

pub(super) fn ink(font: &FontStyle, emoji: &EmojiMode) -> Result<Ink, Diagnostic> {
    if font.style != FontSlant::Normal || font.decoration != TextDecoration::None {
        return Err(Diagnostic::refused("a side legend with a font style or decoration is not supported"));
    }
    let face = Face { family: font.family.as_ref().to_string(), size: font.size, weight: font.weight.clone(), color: font.color.as_ref().to_string() };
    Ink::of(&face, emoji)
}

/// The strip an authored legend sits on, and its style: a left or right edge, not floating, aligned to
/// the start.
fn strip(authored: &AuthoredLegend) -> Result<(&LegendStyleBase, Side), Diagnostic> {
    let style = &authored.style;
    let (base, leaves) = (&style.base, &style.position.leaves);
    let edge = match &style.position.edge {
        Engine::Value(LegendEdge::Left) => Some(Side::Left),
        Engine::Value(LegendEdge::Right) | Engine::Rule(Auto::Auto) => Some(Side::Right),
        Engine::Value(_) => None,
    };
    let Some(side) = edge else { return Err(Diagnostic::refused("a side legend with a top or bottom placement is not supported")) };
    if leaves.overlay == Engine::Value(true) {
        return Err(Diagnostic::refused("a side legend with a floating placement is not supported"));
    }
    if !matches!(leaves.align, Engine::Rule(Auto::Auto) | Engine::Value(LegendAlign::Start)) {
        return Err(Diagnostic::refused("a side legend with a centered or end alignment is not supported"));
    }
    Ok((base, side))
}

impl SideLegend {
    /// The plot's one legend, laid out under the walked style, or `None` where the plot names no
    /// series in a legend.
    pub fn of(plot: &Plane, authored: &AuthoredLegend, emoji: &EmojiMode, book: &FontBook, card_height: f64) -> Result<Option<Self>, Diagnostic> {
        let [legend] = plot.legends() else {
            return if plot.legends().is_empty() { Ok(None) } else { Err(Diagnostic::refused("a side legend with several legends is not supported")) };
        };
        let (base, side) = strip(authored)?;
        if !authored.visible {
            return Err(Diagnostic::refused("a hidden side legend is not supported"));
        }
        Self::lay_out(plot, legend, base, side, emoji, book, card_height).map(Some)
    }

    fn lay_out(plot: &Plane, legend: &Legend, base: &LegendStyleBase, side: Side, emoji: &EmojiMode, book: &FontBook, card_height: f64) -> Result<Self, Diagnostic> {
        if base.direction == Engine::Value(LegendDirection::Horizontal) || base.columns != 0 {
            return Err(Diagnostic::refused("a side legend with a horizontal direction or an authored column count is not supported"));
        }
        if base.symbol_shape != Engine::Rule(Auto::Auto) || base.symbol_fill != Engine::Rule(Auto::Auto) {
            return Err(Diagnostic::refused("a side legend with an authored symbol shape or fill is not supported"));
        }
        let limit = base.symbol_limit.map_or(SYMBOL_LIMIT, |limit| limit as usize);
        if legend.entries.len() > limit {
            return Err(Diagnostic::refused("a side legend with more entries than its symbol limit is not supported"));
        }
        let labels: Vec<&str> = legend.entries.iter().map(|entry| entry.label.as_str()).collect();
        let swatches = || {
            points_only(plot)?;
            legend
                .entries
                .iter()
                .map(|entry| {
                    let Paint::Solid(fill) = plot.series()[entry.series].paint;
                    if fill.3 != 1.0 {
                        return Err(diag::err_internal("a legend swatch fill reaches layout opaque"));
                    }
                    Ok(dbt_charts_color::rgb01_to_hex(fill.0, fill.1, fill.2))
                })
                .collect()
        };
        let clicks = legend.toggle.as_ref().map(clicks).unwrap_or_default();
        Self::build(side, &labels, legend.title.as_deref().filter(|_| base.title.visible != Engine::Value(false)), base, emoji, book, card_height, Swatch::of(plot), clicks, legend.stamps_series, swatches).map_err(Unfit::side)
    }

    /// A strip over `labels` and their swatch fills (`swatches`, read once the texts are measured), under
    /// an optional `title`: each label and the title measured whole (one cut at its limit is refused),
    /// the grid and the box laid out.
    #[allow(clippy::too_many_arguments)]
    fn build(
        side: Side,
        labels: &[&str],
        title: Option<&str>,
        base: &LegendStyleBase,
        emoji: &EmojiMode,
        book: &FontBook,
        card_height: f64,
        swatch: Swatch,
        clicks: Vec<(String, String)>,
        stamps_series: bool,
        swatches: impl FnOnce() -> Result<Vec<String>, Diagnostic>,
    ) -> Result<Self, Unfit> {
        let ink_of = ink(&base.label.element.font, emoji)?;
        let label_limit = base.label.max_width.unwrap_or(LABEL_LIMIT);
        let measure = |text: &str, ink: &Ink, limit: f64| -> Result<f64, Unfit> {
            let width = book.run(text, &ink.font()).map_err(diag::err_internal)?.advance;
            if width > limit {
                return Err(Unfit::Cut);
            }
            Ok(width)
        };
        let widths = labels.iter().map(|label| measure(label, &ink_of, label_limit)).collect::<Result<Vec<_>, _>>()?;
        let title = title.map(|text| Ok::<_, Diagnostic>((ink(&base.title.element.font, emoji)?, text.to_string()))).transpose()?;
        let title_width = title.as_ref().map(|(face, text)| Ok::<_, Unfit>((measure(text, face, TITLE_LIMIT)?, face.size))).transpose()?;
        if widths.is_empty() {
            return Err(Unfit::Empty);
        }
        let fills = swatches()?;
        let slots: Vec<(f64, Reach)> = widths.iter().map(|&width| (width, swatch.reach())).collect();
        let grid = grid(ink_of.size, base.label.element.padding, &slots);
        let (width, height, band) = boxed(&grid, title_width);
        let entries = fills.into_iter().zip(labels).map(|(fill, label)| (fill, label.to_string())).collect();
        if height > card_height {
            return Err(Unfit::TooTall);
        }
        Ok(Self { side, labels: ink_of, title, entries, clicks, stamps_series, grid, width, band, swatch })
    }

    /// The strip's box.
    pub fn size(&self) -> (f64, f64) {
        (self.width, self.band + self.grid.height)
    }

    /// The group's top-left in plot-local coordinates on a plot of `plot`, whose y axis reaches
    /// `axis` past its edges: a side legend sits `OFFSET` past the plot rect and the axis, the near
    /// edge ceiled and the far one floored (`legend_origin`).
    pub fn origin(&self, plot: Size, axis: Insets) -> (f64, f64) {
        match self.side {
            Side::Right => (right_origin(plot, axis), 0.0),
            Side::Left => ((-axis.left.max(0.0)).floor() - OFFSET - self.width, 0.0),
        }
    }

    /// `gutters` with the strip's reach: it joins the view bounds by its far edge alone
    /// (`legend_fit_bounds`), however tall it is. A left strip is placed by `legend_origin`'s rounded
    /// bounds, fit and draw alike; a right one is fit unceiled and drawn ceiled.
    pub fn widen(&self, gutters: Insets, plot: Size, axis: Insets) -> Insets {
        match self.side {
            Side::Right => right_reach(gutters, axis, self.width),
            Side::Left => Insets { left: gutters.left.max(-self.origin(plot, axis).0), ..gutters },
        }
    }

    /// The legend as Vega draws it (`symbol_legend`), at `at`.
    pub fn nodes(&self, at: (f64, f64)) -> Node {
        let mut children = Vec::new();
        if let Some((face, text)) = &self.title {
            let at = (at.0, at.1 + baseline_shift("top", face.size));
            children.push(Node::group(Role::Legend, legend_title_attrs(), vec![Node::text(Role::Legend, at, face.attrs(Some("start")), text.as_str())]));
        }
        let top = at.1 + self.band;
        let c = self.grid.size / 2.0;
        let disc = scene_path(&self.swatch.symbol.draw(SYMBOL_SIZE));
        let mut cells = Vec::new();
        for (idx, (fill, label)) in self.entries.iter().enumerate() {
            let (x, y) = (at.0, top + self.grid.rows[idx]);
            let paint = match &self.swatch.ring {
                Some((interior, opacity)) => Attrs::new()
                    .set("fill", interior.as_str())
                    .set("stroke", fill.as_str())
                    .num("stroke-width", STROKE)
                    .set_if(miter_limit(self.swatch.symbol).is_some(), "stroke-miterlimit", miter_limit(self.swatch.symbol).unwrap_or_default())
                    .num("opacity", *opacity),
                None => Attrs::new().set("fill", fill.as_str()).num("stroke-width", STROKE),
            };
            let symbol = Node::path(Role::Legend, disc.clone(), paint).translate(x + c, y + c);
            cells.push(Node::group(Role::Legend, legend_symbol_attrs(false), vec![symbol]));
            let text_at = (x + self.grid.label_x, y + c + baseline_shift("middle", self.labels.size));
            let text = Node::text(Role::Legend, text_at, self.labels.attrs(Some("start")), label.as_str());
            let key = self.stamps_series.then_some(label.as_str());
            let button = match self.clicks.get(idx) {
                Some((param, next)) => legend_label_attrs(key, false).set("data-dbt-legend-var", param.as_str()).set("data-dbt-legend-next", next.as_str()),
                None => legend_label_attrs(key, false),
            };
            cells.push(Node::group(Role::Legend, button, vec![text]));
        }
        children.push(Node::group(Role::Legend, legend_entries_attrs(), cells));
        Node::group(Role::Legend, legend_attrs(self.aria().as_deref()), children)
    }

    /// Vega joins the entries with ", " for its own aria-label, ambiguous once a name holds a
    /// comma: the legend then says only how many series it names.
    fn aria(&self) -> Option<String> {
        self.entries.iter().any(|(_, label)| label.contains(',')).then(|| {
            let distinct = self.entries.iter().map(|(_, label)| label).collect::<std::collections::BTreeSet<_>>().len();
            display_text::language::tr("series.count", Some(distinct as u64), &[]).expect("every language catalog words series.count")
        })
    }
}

/// `gutters` with a right strip `width` wide joined at the unceiled ink edge (`scatter/layout.rs`'s fit
/// pass: `right_edge + VEGA_LEGEND_OFFSET + w`); Vega ceils it after the fit, when it is drawn.
fn right_reach(gutters: Insets, axis: Insets, width: f64) -> Insets {
    Insets { right: gutters.right.max(axis.right.max(0.0) + OFFSET + width), ..gutters }
}

/// The x of a right strip: `OFFSET` past the ceiled edge of the plot and its y axis.
fn right_origin(plot: Size, axis: Insets) -> f64 {
    (plot.width + axis.right.max(0.0)).ceil() + OFFSET
}

/// Vega's `legend.layout` margin between legends stacked on a strip (`VEGA_LEGEND_GROUP_MARGIN`).
const GROUP_MARGIN: f64 = 8.0;

/// `ShapeLegend`'s faces: Vega-Lite gives the shape encoding no legend config, so Vega's own `guide-title`
/// (11px bold `#000`) and `guide-label` (10px `#000`) apply (`vega-parser/src/config.js`), `labelOffset` 4.
const SHAPE_TITLE_SIZE: f64 = 11.0;
const SHAPE_LABEL_SIZE: f64 = 10.0;
const SHAPE_LABEL_OFFSET: f64 = 4.0;
const SHAPE_INK: &str = "#000";

/// How a shape legend's symbols are filled (`legend/encode.ts` `symbols()` in vega-lite): an unfilled mark
/// keeps its interior, ringed in the mark's own color unless a color channel makes the stroke a field
/// reference, which leaves it unset; beside a color channel the fill is a field reference, black; else
/// the mark's own fill survives.
#[derive(Debug, Clone, PartialEq)]
enum ShapeFill {
    Mark(String),
    Black,
    Ring { interior: String, stroke: Option<String> },
}

/// A shape channel's legend, which no legend config styles: its title (the field as authored) over one
/// row per category in the scale's order, each bounded by its own symbol.
#[derive(Debug, Clone, PartialEq)]
pub struct ShapeLegend {
    title: (Ink, String),
    labels: Ink,
    entries: Vec<(VegaSymbol, String)>,
    fill: ShapeFill,
    opacity: f64,
    grid: Grid,
    width: f64,
    band: f64,
}

impl ShapeLegend {
    /// The legend of the plot's shape channel, or `None` where it has none. `colored` is whether a side
    /// legend names a color channel.
    fn of(plot: &Plane, authored: &AuthoredLegend, emoji: &EmojiMode, book: &FontBook, colored: bool) -> Result<Option<Self>, Diagnostic> {
        let Some((scale, style)) = plot.layers().iter().find_map(|layer| match (&layer.mark, plot.mark_style().points.as_ref()) {
            (Mark::Points { shape: Shape::Channel(scale), .. }, Some(style)) => Some((scale, style)),
            _ => None,
        }) else {
            return Ok(None);
        };
        // The shape legend always stacks at the right, whatever edge the others take, so a legend placed
        // anywhere else leaves it to a stack this port does not model.
        if strip(authored)?.1 != Side::Right {
            return Err(Diagnostic::refused(refuse!("a scatter `shape:` beside a legend placed off the right strip is not supported")));
        }
        let fill = match (&style.unfilled, colored) {
            (Some(Unfilled { interior, stroke }), _) => ShapeFill::Ring { interior: interior.clone(), stroke: stroke.clone() },
            (None, true) => ShapeFill::Black,
            (None, false) => {
                let [series] = plot.series() else {
                    return Err(diag::err_internal("a shape legend with no color channel beside several series"));
                };
                let Paint::Solid(fill) = series.paint;
                ShapeFill::Mark(dbt_charts_color::rgb01_to_hex(fill.0, fill.1, fill.2))
            }
        };
        let face = |size: f64| Ink::of(&Face { family: scale.font.clone(), size, weight: FontWeightValue::Number(400.0), color: SHAPE_INK.to_string() }, emoji);
        let (title_ink, labels_ink) = (face(SHAPE_TITLE_SIZE)?, face(SHAPE_LABEL_SIZE)?);
        let advance = |text: &str, ink: &Ink| book.run(text, &ink.font()).map(|run| run.advance).map_err(diag::err_internal);
        let entries: Vec<(VegaSymbol, String)> = scale.domain.iter().enumerate().map(|(index, label)| (scale.symbol(index), label.clone())).collect();
        let rows = entries.iter().map(|(symbol, label)| Ok((advance(label, &labels_ink)?, Reach::ringed(*symbol)))).collect::<Result<Vec<_>, Diagnostic>>()?;
        let grid = grid(SHAPE_LABEL_SIZE, SHAPE_LABEL_OFFSET, &rows);
        // The title is measured at the default weight: vl-convert's layout does not re-instance a
        // variable font for its bold.
        let (width, _, band) = boxed(&grid, Some((advance(&scale.title, &title_ink)?, SHAPE_TITLE_SIZE)));
        Ok(Some(Self { title: (title_ink, scale.title.clone()), labels: labels_ink, entries, fill, opacity: style.opacity, grid, width, band }))
    }

    /// The legend at `at`, as bare marks: Vega-Lite draws no legend group for it.
    fn nodes(&self, at: (f64, f64)) -> Vec<Node> {
        let (title_ink, title) = &self.title;
        let text = |ink: &Ink, weight: Option<&str>, at: (f64, f64), text: &str| {
            let attrs = Attrs::new().vega("font-family", ink.family()).num("font-size", ink.size);
            let attrs = match weight {
                Some(weight) => attrs.set("font-weight", weight),
                None => attrs,
            };
            Node::text(Role::Legend, at, attrs.set("fill", SHAPE_INK), text)
        };
        let mut nodes = vec![text(title_ink, Some("bold"), (at.0, at.1 + baseline_shift("top", SHAPE_TITLE_SIZE)), title)];
        let top = at.1 + self.band;
        let c = self.grid.size / 2.0;
        for ((symbol, label), row) in self.entries.iter().zip(&self.grid.rows) {
            let y = top + row;
            let glyph = match &self.fill {
                ShapeFill::Mark(color) => Attrs::new().set("fill", color.as_str()),
                ShapeFill::Black => Attrs::new().set("fill", "black").set("fill-opacity", "1"),
                ShapeFill::Ring { interior, stroke } => {
                    let ring = Attrs::new().set("fill", interior.as_str());
                    match stroke {
                        Some(stroke) => ring.set("stroke", stroke.as_str()),
                        None => ring,
                    }
                }
            };
            let glyph = glyph.num("stroke-width", STROKE).num("opacity", self.opacity);
            nodes.push(Node::path(Role::Legend, scene_path(&symbol.draw(SYMBOL_SIZE)), glyph).translate(at.0 + c, y + c));
            nodes.push(text(&self.labels, None, (at.0 + self.grid.label_x, y + c + baseline_shift("middle", self.labels.size)), label));
        }
        nodes
    }
}

/// The legends a plot draws on its right strip: the side legend, then the shape legend `GROUP_MARGIN`
/// under it (`stack_legends`), their boxes sharing the strip's origin.
#[derive(Debug, Clone, PartialEq)]
pub struct Legends {
    side: Option<SideLegend>,
    shape: Option<ShapeLegend>,
}

impl Legends {
    /// The plot's legends, or `None` where it names none.
    pub fn of(plot: &Plane, authored: &AuthoredLegend, emoji: &EmojiMode, book: &FontBook, card_height: f64) -> Result<Option<Self>, Diagnostic> {
        let side = SideLegend::of(plot, authored, emoji, book, card_height)?;
        let shape = ShapeLegend::of(plot, authored, emoji, book, side.is_some())?;
        Ok((side.is_some() || shape.is_some()).then_some(Self { side, shape }))
    }

    /// `gutters` with every strip's reach: the widest wins.
    pub fn widen(&self, gutters: Insets, plot: Size, axis: Insets) -> Insets {
        let widened = self.side.as_ref().map(|side| side.widen(gutters, plot, axis));
        match (&self.shape, widened) {
            (Some(shape), Some(side)) => right_reach(side, axis, shape.width),
            (Some(shape), None) => right_reach(gutters, axis, shape.width),
            (None, Some(side)) => side,
            (None, None) => gutters,
        }
    }

    /// The strip's top-left in plot-local coordinates.
    pub fn origin(&self, plot: Size, axis: Insets) -> (f64, f64) {
        self.side.as_ref().map_or((right_origin(plot, axis), 0.0), |side| side.origin(plot, axis))
    }

    pub fn nodes(&self, at: (f64, f64)) -> Vec<Node> {
        let below = self.side.as_ref().map_or(0.0, |side| side.size().1 + GROUP_MARGIN);
        self.side.iter().map(|side| side.nodes(at)).chain(self.shape.iter().flat_map(|shape| shape.nodes((at.0, at.1 + below)))).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `legend_grid`'s one-column rows. A default disc at 11 px: cell 12, the swatch (5) inside the
    /// text line (y 0..11), so rows pitch `2 + ceil(11)`. At 13 px the text top moves to 0.5 and the
    /// line below it grows to 13.5.
    #[test]
    fn rows_pitch_by_the_swatch_or_the_text_line_that_reaches_further() {
        let at = |size| grid(size, 6.0, &[(10.0, Reach::FILLED); 3]);
        let small = at(11.0);
        assert_eq!((small.size, small.rows.clone(), small.height), (12.0, vec![0.0, 13.0, 26.0], 37.0));
        let big = at(13.0);
        assert_eq!((big.size, big.rows.clone(), big.height), (13.0, vec![0.0, 16.0, 32.0], 46.0));
    }

    /// An unfilled mark's ring and its stroke reach 6.5 px from the cell's center, so rows pitch 16 rather
    /// than 13 (`scatter_unfilled_color`'s twin: entries at y 23, 39 and 55); a ring after a filled row also
    /// adds the part of its top above its cell, a whole pixel here (`legend_grid`'s `neg`).
    #[test]
    fn a_ringed_row_pitches_by_its_symbol_s_reach() {
        let ring = Reach::ringed(VegaSymbol::Circle);
        assert_eq!(ring, Reach { x: 6.5, up: 6.5, down: 6.5 });
        let laid = grid(11.0, 6.0, &[(10.0, ring); 3]);
        assert_eq!((laid.rows, laid.height), (vec![0.0, 16.0, 32.0], 45.0));
        let mixed = grid(11.0, 6.0, &[(10.0, Reach::FILLED), (10.0, ring), (10.0, Reach::FILLED)]);
        assert_eq!(mixed.rows, vec![0.0, 14.0, 29.0]);
    }

    /// The label starts `labelOffset` after the cell and the ink is the widest label's end.
    #[test]
    fn labels_start_after_the_cell_and_the_widest_one_sets_the_width() {
        let laid = grid(11.0, 6.0, &[(30.5, Reach::FILLED), (44.25, Reach::FILLED)]);
        assert_eq!((laid.label_x, laid.width), (18.0, 62.25));
        assert_eq!(grid(11.0, 6.0, &[]).height, 0.0);
    }

    /// The box is `ceil(max(title, entries))` wide: a 55.45 px column makes 56 (probe-pinned), a
    /// wider title wins; a title adds `size + 5` (17 under a 12 px title, 19 under 14 px).
    #[test]
    fn the_box_takes_the_wider_of_title_and_entries_under_a_title_band() {
        let laid = Grid { size: 12.0, label_x: 18.0, rows: vec![0.0], height: 11.0, width: 55.45 };
        assert_eq!(boxed(&laid, None), (56.0, 11.0, 0.0));
        assert_eq!(boxed(&laid, Some((30.0, 12.0))), (56.0, 28.0, 17.0));
        assert_eq!(boxed(&laid, Some((160.2, 14.0))), (161.0, 30.0, 19.0));
    }

    fn legend(side: Side, width: f64) -> SideLegend {
        let ink = Ink::of(&Face { family: "x".into(), size: 11.0, weight: board_model::facet::FontWeightValue::Number(400.0), color: "#000".into() }, &EmojiMode::Disabled).unwrap();
        SideLegend { side, labels: ink, title: None, entries: vec![], clicks: vec![], stamps_series: false, grid: grid(11.0, 6.0, &[]), width, band: 0.0, swatch: Swatch::DISC }
    }

    /// A right strip is drawn 18 px past the ceiled plot-and-axis edge but fits at the unceiled one
    /// (`scatter/layout.rs`: `right_edge + 18 + w` in the fit pass, `x2.ceil() + 18` when drawn); a
    /// left strip sits 18 px short of the floored near edge, its box ending there, in both.
    #[test]
    fn a_strip_sits_an_offset_past_the_axis_and_reaches_the_view_by_its_far_edge() {
        let plot = Size { width: 449.4, height: 300.0 };
        let axis = Insets { top: 0.0, right: 3.2, bottom: 0.0, left: 40.5 };
        let right = legend(Side::Right, 56.0);
        assert_eq!(right.origin(plot, axis), (471.0, 0.0));
        let reach = right.widen(Insets { top: 1.0, right: 3.2, bottom: 2.0, left: 40.5 }, plot, axis);
        assert_eq!((reach.right, reach.left, reach.top), (3.2 + 18.0 + 56.0, 40.5, 1.0));
        let left = legend(Side::Left, 56.0);
        assert_eq!(left.origin(plot, axis), (-115.0, 0.0));
        assert_eq!(left.widen(Insets::ZERO, plot, axis).left, 115.0);
    }
}
