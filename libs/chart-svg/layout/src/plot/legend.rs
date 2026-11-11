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

use bind::bound::{AuthoredLegend, Face, Legend, Mark, Paint, Plane, Toggle};
use board_model::rule::Auto;
use board_model::style::board::EmojiMode;
use board_model::style::font::{FontSlant, FontStyle, TextDecoration};
use board_model::style::legend::{LegendAlign, LegendDirection, LegendEdge, LegendPlacementEdge, LegendStyleBase};
use d3rs::vega_symbol::VegaSymbol;
use diagnostic::{diag, Diagnostic};
use measure::shaped::FontBook;
use measure::baseline_shift;
use model_core::Engine;
use scene::{Attrs, Node, Role};

use crate::contract::{legend_attrs, legend_entries_attrs, legend_label_attrs, legend_symbol_attrs, legend_title_attrs};
use crate::geom::{Insets, Size};
use crate::plot::cartesian::Ink;
use crate::scene_path::scene_path;

/// Vega's default legend `offset` from the anchoring edge (`VEGA_LEGEND_OFFSET`).
const OFFSET: f64 = 18.0;
/// Vega's default legend `titlePadding` (`VEGA_LEGEND_TITLE_PADDING`).
const TITLE_PADDING: f64 = 5.0;
/// Vega's `labelLimit` and `titleLimit` when the theme names none (`VEGA_LEGEND_LABEL_LIMIT`,
/// `VEGA_LEGEND_TITLE_LIMIT`).
const LABEL_LIMIT: f64 = 160.0;
const TITLE_LIMIT: f64 = 180.0;
/// Vega's default `symbolLimit` (`VEGA_LEGEND_SYMBOL_LIMIT`).
const SYMBOL_LIMIT: usize = 30;
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

/// The half extent of the disc on each axis (`half_extent_x`, `extent_y`): no stroke.
const HALF: f64 = 5.0;

/// `legend_grid` for one column of `widths` (label advances) at `font_size`.
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

/// The cell is the default symbol and its stroke, at least the font size; rows are as tall as the
/// swatch or the label text reaches, a row a `ROW_PADDING` from the last, and a one-column grid
/// does not center its first row.
pub fn grid(widths: &[f64], font_size: f64, label_offset: f64) -> Grid {
    let size = (SYMBOL_SIZE.sqrt() + STROKE).ceil().max(font_size);
    let c = size / 2.0;
    let text_top = c + (0.3 * font_size).round() - (0.8 * font_size).round();
    let top = (c - HALF).min(text_top);
    let bottom = (c + HALF).max(text_top + font_size);
    let above = if top < 0.0 { (-top).ceil() } else { 0.0 };
    let pitch = ROW_PADDING + above + bottom.ceil();
    let rows: Vec<f64> = (0..widths.len()).map(|row| row as f64 * pitch).collect();
    let height = rows.last().map_or(0.0, |last| (last + bottom).ceil());
    let label_x = size + label_offset;
    Grid { size, label_x, rows, height, width: widths.iter().map(|w| label_x + w).fold(0.0, f64::max) }
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

/// A laid-out side legend, in plot-local coordinates once placed.
pub struct SideLegend {
    side: Side,
    labels: Ink,
    title: Option<(Ink, String)>,
    entries: Vec<(String, String)>,
    /// Per entry, the board param its click sets and the value it sets (`LegendView::clickable`
    /// while every series is shown); empty for a legend that hides no series.
    clicks: Vec<(String, String)>,
    grid: Grid,
    width: f64,
    band: f64,
}

/// The click of each entry of a legend that hides series: from all shown it narrows to the one series,
/// written as the JSON list `["name"]` (`LegendView::next`).
fn clicks(toggle: &Toggle) -> Vec<(String, String)> {
    toggle.keys.iter().map(|key| (toggle.param.clone(), serde_json::json!([key]).to_string())).collect()
}

fn ink(font: &FontStyle, emoji: &EmojiMode) -> Result<Ink, Diagnostic> {
    if font.style != FontSlant::Normal || font.decoration != TextDecoration::None {
        return Err(Diagnostic::refused("a side legend with a font style or decoration is not supported"));
    }
    let face = Face { family: font.family.as_ref().to_string(), size: font.size, weight: font.weight.clone(), color: font.color.as_ref().to_string() };
    Ink::of(&face, emoji)
}

impl SideLegend {
    /// The plot's one legend, laid out under the walked style, or `None` where the plot names no
    /// series in a legend.
    pub fn of(plot: &Plane, authored: &AuthoredLegend, emoji: &EmojiMode, book: &FontBook, card_height: f64) -> Result<Option<Self>, Diagnostic> {
        let [legend] = plot.legends() else {
            return if plot.legends().is_empty() { Ok(None) } else { Err(Diagnostic::refused("a side legend with several legends is not supported")) };
        };
        let (base, leaves, edge) = match authored {
            AuthoredLegend::Side(style) => (&style.base, &style.position.leaves, match &style.position.edge {
                Engine::Value(LegendEdge::Left) => Some(Side::Left),
                Engine::Value(LegendEdge::Right) | Engine::Rule(Auto::Auto) => Some(Side::Right),
                Engine::Value(_) => None,
            }),
            AuthoredLegend::Endpoints(style) => (&style.base, &style.position.leaves, match &style.position.edge {
                Engine::Value(LegendPlacementEdge::Left) => Some(Side::Left),
                Engine::Value(LegendPlacementEdge::Right) | Engine::Rule(Auto::Auto) => Some(Side::Right),
                Engine::Value(_) => None,
            }),
        };
        let Some(side) = edge else { return Err(Diagnostic::refused("a side legend with a top, bottom or endpoint placement is not supported")) };
        if leaves.overlay == Engine::Value(true) {
            return Err(Diagnostic::refused("a side legend with a floating placement is not supported"));
        }
        if !matches!(leaves.align, Engine::Rule(Auto::Auto) | Engine::Value(LegendAlign::Start)) {
            return Err(Diagnostic::refused("a side legend with a centered or end alignment is not supported"));
        }
        Self::lay_out(plot, legend, base, side, emoji, book, card_height).map(Some)
    }

    fn lay_out(plot: &Plane, legend: &Legend, base: &LegendStyleBase, side: Side, emoji: &EmojiMode, book: &FontBook, card_height: f64) -> Result<Self, Diagnostic> {
        if base.visible == Engine::Value(false) {
            return Err(Diagnostic::refused("a hidden side legend is not supported"));
        }
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
        let labels = ink(&base.label.element.font, emoji)?;
        let label_limit = base.label.max_width.unwrap_or(LABEL_LIMIT);
        let measure = |text: &str, ink: &Ink, limit: f64| -> Result<f64, Diagnostic> {
            let width = book.run(text, &ink.font()).map_err(diag::err_internal)?.advance;
            if width > limit {
                return Err(Diagnostic::refused("a side legend with a label or title cut at its limit is not supported"));
            }
            Ok(width)
        };
        let widths = legend.entries.iter().map(|entry| measure(&entry.label, &labels, label_limit)).collect::<Result<Vec<_>, _>>()?;
        let shown = legend.title.as_ref().filter(|_| base.title.visible != Engine::Value(false));
        let title = shown.map(|text| Ok::<_, Diagnostic>((ink(&base.title.element.font, emoji)?, text.clone()))).transpose()?;
        let title_width = title.as_ref().map(|(face, text)| Ok::<_, Diagnostic>((measure(text, face, TITLE_LIMIT)?, face.size))).transpose()?;
        if widths.is_empty() {
            return Err(Diagnostic::refused("a side legend with no entries is not supported"));
        }
        points_only(plot)?;
        let grid = grid(&widths, labels.size, base.label.element.padding);
        let (width, height, band) = boxed(&grid, title_width);
        let entries = legend
            .entries
            .iter()
            .map(|entry| {
                let Paint::Solid(fill) = plot.series()[entry.series].paint;
                if fill.3 != 1.0 {
                    return Err(diag::err_internal("a legend swatch fill reaches layout opaque"));
                }
                Ok((dbt_charts_color::rgb01_to_hex(fill.0, fill.1, fill.2), entry.label.clone()))
            })
            .collect::<Result<_, _>>()?;
        if height > card_height {
            return Err(Diagnostic::refused("a side legend taller than its card is not supported"));
        }
        Ok(Self { side, labels, title, entries, clicks: legend.toggle.as_ref().map(clicks).unwrap_or_default(), grid, width, band })
    }

    /// The group's top-left in plot-local coordinates on a plot of `plot`, whose y axis reaches
    /// `axis` past its edges: a side legend sits `OFFSET` past the plot rect and the axis, the near
    /// edge ceiled and the far one floored (`legend_origin`).
    pub fn origin(&self, plot: Size, axis: Insets) -> (f64, f64) {
        match self.side {
            Side::Right => ((plot.width + axis.right.max(0.0)).ceil() + OFFSET, 0.0),
            Side::Left => ((-axis.left.max(0.0)).floor() - OFFSET - self.width, 0.0),
        }
    }

    /// `gutters` with the strip's reach: it joins the view bounds by its far edge alone
    /// (`legend_fit_bounds`), however tall it is. A left strip is placed by `legend_origin`'s rounded
    /// bounds, fit and draw alike; a right one is fit unceiled and drawn ceiled.
    pub fn widen(&self, gutters: Insets, plot: Size, axis: Insets) -> Insets {
        match self.side {
            // A right strip joins the fit at the unceiled ink edge (`scatter/layout.rs`'s fit pass:
            // `right_edge + VEGA_LEGEND_OFFSET + w`); Vega ceils it after the fit, when it is drawn.
            Side::Right => Insets { right: gutters.right.max(axis.right.max(0.0) + OFFSET + self.width), ..gutters },
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
        let disc = scene_path(&VegaSymbol::Circle.draw(SYMBOL_SIZE));
        let mut cells = Vec::new();
        for (i, ((fill, label), row)) in self.entries.iter().zip(&self.grid.rows).enumerate() {
            let y = top + row;
            let paint = Attrs::new().set("fill", fill.as_str()).num("stroke-width", STROKE);
            let symbol = Node::path(Role::Legend, disc.clone(), paint).translate(at.0 + c, y + c);
            cells.push(Node::group(Role::Legend, legend_symbol_attrs(false), vec![symbol]));
            let text_at = (at.0 + self.grid.label_x, y + c + baseline_shift("middle", self.labels.size));
            let text = Node::text(Role::Legend, text_at, self.labels.attrs(Some("start")), label.as_str());
            let button = match self.clicks.get(i) {
                Some((param, next)) => legend_label_attrs(None, false).set("data-dbt-legend-var", param.as_str()).set("data-dbt-legend-next", next.as_str()),
                None => legend_label_attrs(None, false),
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

#[cfg(test)]
mod tests {
    use super::*;

    /// `legend_grid`'s one-column rows. A default disc at 11 px: cell 12, the swatch (5) inside the
    /// text line (y 0..11), so rows pitch `2 + ceil(11)`. At 13 px the text top moves to 0.5 and the
    /// line below it grows to 13.5.
    #[test]
    fn rows_pitch_by_the_swatch_or_the_text_line_that_reaches_further() {
        let at = |size| grid(&[10.0; 3], size, 6.0);
        let small = at(11.0);
        assert_eq!((small.size, small.rows.clone(), small.height), (12.0, vec![0.0, 13.0, 26.0], 37.0));
        let big = at(13.0);
        assert_eq!((big.size, big.rows.clone(), big.height), (13.0, vec![0.0, 16.0, 32.0], 46.0));
    }

    /// The label starts `labelOffset` after the cell and the ink is the widest label's end.
    #[test]
    fn labels_start_after_the_cell_and_the_widest_one_sets_the_width() {
        let laid = grid(&[30.5, 44.25], 11.0, 6.0);
        assert_eq!((laid.label_x, laid.width), (18.0, 62.25));
        assert_eq!(grid(&[], 11.0, 6.0).height, 0.0);
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
        SideLegend { side, labels: ink, title: None, entries: vec![], clicks: vec![], grid: grid(&[], 11.0, 6.0), width, band: 0.0 }
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
