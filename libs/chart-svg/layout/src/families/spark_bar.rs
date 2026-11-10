//! A spark_bar: a label column, a proportional bar track per row and a tabular count column. `plan`
//! decides what a width and the style decide (the card's width, the bar area, its least height);
//! `draw` places the nodes in a slot of a given height.
//!
//! The rules are `chart-renderer/src/families/spark_bar.rs`'s (`vertical`, `layout`), cited at
//! each constant; the card's `<svg>` is part of the DOM contract and is emitted here. A label cut
//! to its column, which production warns of, and the "+N" note of a chart cut to `max_bars` are
//! refused by name.

use bind::model::SparkBar;
use board_model::primitives::PaletteChoice;
use board_model::style::board::EmojiMode;
use board_model::style::spark_bar::SparkBarChartStyle;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use scene::{Attrs, Chars, Escape, Extent, Node, Role, Run, Shape};

use super::failed::Face;

/// spark_bar.rs:`TEXT_BASELINE_OFFSET`, `SIDE_PADDING`, `AVG_CHAR_WIDTH_PX`, `TABULAR_NUMS`.
const TEXT_BASELINE_OFFSET: f64 = 4.0;
const SIDE_PADDING: f64 = 4.0;
const AVG_CHAR_WIDTH_PX: f64 = 7.0;
const TABULAR_NUMS: &str = "font-variant-numeric: tabular-nums lining-nums;";
/// common.rs:`BAR_CORNER_THICKNESS_SHARE`: a corner is at most this share of the bar's height.
const CORNER_THICKNESS_SHARE: f64 = 0.25;
/// spark_bar.rs:`layout`. The bar area is never narrower than this.
const MIN_BAR_AREA: f64 = 20.0;

#[derive(Debug, Clone, Copy)]
pub struct SparkBarCard<'a> {
    pub spark: &'a SparkBar,
    pub style: &'a SparkBarChartStyle,
    pub face: &'a Face,
    /// The bar fill: [`fill_of`].
    pub fill: &'a str,
}

/// The face every label and count paints in: the family's body font.
pub fn face_of(style: &SparkBarChartStyle, emoji: &EmojiMode) -> Face {
    let font = &style.font;
    Face::of(font.family.as_str(), font.size, &font.weight, font.color.as_str(), emoji)
}

/// The bar fill: the chart's own color, else the first stop of the board's single-series palette
/// (production's `_seed_spark_colors`, which no chart rhythm slot touches).
pub fn fill_of(placed: &Style) -> Result<String, Diagnostic> {
    if let Some(color) = &placed.charts.spark_bar.bar.color {
        return Ok(color.as_str().to_string());
    }
    let categorical = placed.charts.color.categorical.as_ref().ok_or_else(|| diag::err_internal("the total style holds `charts.color.categorical`"))?;
    match &categorical.single_series_palette.0 {
        PaletteChoice::Colors(colors) => colors.first().map(|c| c.as_str().to_string()).ok_or_else(|| diag::err_internal("a single-series palette has a color")),
        PaletteChoice::Name(name) => Err(diag::err_internal(format!("a palette name {name:?} reaches layout unresolved"))),
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    width: f64,
    left: f64,
    bar_area: f64,
    /// The card's least height: its rows and the padding under them.
    pub natural_height: f64,
}

/// spark_bar.rs:`vertical`, with no title: the rows and the padding under the last.
pub fn natural_height(spark: &SparkBar, style: &SparkBarChartStyle) -> f64 {
    spark.rows.len() as f64 * (style.bar.height + style.bar.padding) + style.bar.padding
}

/// The card at `width`: the label column, the bar area and the least height.
pub fn plan(card: &SparkBarCard, width: f64) -> Result<Plan, Diagnostic> {
    let style = card.style;
    if card.spark.more > 0 {
        return Err(Diagnostic::refused(refuse!("a spark_bar cut to `max_bars` is not supported")));
    }
    // spark_bar.rs:`layout`: the label is cut by character count, not measured, and production
    // counts a cut (and warns) whether or not the label column is drawn.
    let max_chars = (style.label.width / AVG_CHAR_WIDTH_PX) as usize;
    if card.spark.rows.iter().any(|row| row.label.chars().count() > max_chars) {
        return Err(Diagnostic::refused(refuse!("a spark_bar label wider than its column is not supported")));
    }
    let width = width.max(style.min_width);
    let left = if style.label.visible { style.label.width } else { SIDE_PADDING };
    let right = if style.count.visible { style.count.width } else { SIDE_PADDING };
    Ok(Plan { width, left, bar_area: (width - left - right).max(MIN_BAR_AREA), natural_height: natural_height(card.spark, style) })
}

/// A bar's fraction of its span and whether it lies left of the midline: a negative anywhere puts
/// every bar on a shared midline, and an unsigned layout never has one.
fn fraction(value: f64, max: f64, signed: bool) -> (f64, bool) {
    let clamped = if signed { value.clamp(-max, max) } else { value.clamp(0.0, max) };
    (clamped.abs() / max, clamped < 0.0)
}

fn text(at: (f64, f64), attrs: Attrs, body: &str) -> Node {
    let runs = vec![Run::Chars(Chars::new(body, Escape::Quick))];
    Node::new(Role::Label, attrs, Shape::Text { at, bounds: None, runs })
}

/// The card as nodes: its root `<svg>`, drawn in a slot `height` tall.
pub fn draw(card: &SparkBarCard, plan: &Plan, height: f64) -> Vec<Node> {
    let (style, face) = (card.style, card.face);
    let row_height = style.bar.height + style.bar.padding;
    // spark_bar.rs:`layout`: the row renderer takes `int(bar_height)`, and baselines derive from it.
    let bar_height = style.bar.height.trunc();
    let radius = |width: f64| style.border.radius.min(CORNER_THICKNESS_SHARE * bar_height).min(width / 2.0);
    let rect = |role: Role, x: f64, y: f64, w: f64, fill: &str| {
        Node::new(role, Attrs::new().quick("fill", fill), Shape::Rect { x, y, w, h: bar_height, rx: radius(w) })
    };
    let (track_x, half) = (plan.left.trunc(), plan.bar_area / 2.0);
    let mut nodes = Vec::new();
    for (i, row) in card.spark.rows.iter().enumerate() {
        let y = i as f64 * row_height;
        let (share, negative) = fraction(row.value, card.spark.max_value, card.spark.signed);
        let (width, x) = match card.spark.signed {
            true => (share * half, track_x + if negative { half - share * half } else { half }),
            false => (share * plan.bar_area, track_x),
        };
        let baseline = y + bar_height / 2.0 + TEXT_BASELINE_OFFSET;
        let ink = || Attrs::new().num("font-size", face.size).quick("fill", face.color.as_str()).quick("font-family", face.family.as_str());
        if style.label.visible {
            nodes.push(text((0.0, baseline), ink(), &row.label));
        }
        nodes.push(rect(Role::Chrome, track_x, y, plan.bar_area, style.bar.background.as_str()));
        if width > 0.0 {
            let mark = Role::Mark { layer: 0, series: None, datum: Some(i) };
            nodes.push(rect(mark, x, y, width, card.fill));
        }
        if let Some(count) = &row.count {
            let attrs = ink().quick("text-anchor", "end").quick("style", TABULAR_NUMS);
            nodes.push(text((plan.width - SIDE_PADDING, baseline), attrs, count));
        }
    }
    let extent = Extent { width: plan.width, height };
    vec![Node::new(Role::Chrome, Attrs::new(), Shape::Svg { extent, children: nodes })]
}
