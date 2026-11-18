//! A spark_bar: an optional title row, a label column, a proportional bar track per row and a
//! tabular count column. `plan` decides what a width and the style decide (the card's width, the
//! bar area, its least height); `draw` places the nodes in a slot of a given height.
//!
//! The rules are `chart-renderer/src/families/spark_bar.rs`'s (`vertical`, `layout`), cited at
//! each constant; the card's `<svg>` is part of the DOM contract and is emitted here.

use bind::bound::SparkBar;
use board_model::primitives::PaletteChoice;
use board_model::style::board::EmojiMode;
use board_model::style::spark_bar::SparkBarChartStyle;
use board_model::style::style::Style;
use diagnostic::{diag, Diagnostic};
use scene::{Attrs, Chars, Escape, Extent, Node, Role, Run, Shape};

use super::failed::{Face, Weight};
use crate::board::band::TitleShift;

/// spark_bar.rs:`TEXT_BASELINE_OFFSET`, `SIDE_PADDING`, `AVG_CHAR_WIDTH_PX`, `TABULAR_NUMS`.
const TEXT_BASELINE_OFFSET: f64 = 4.0;
const SIDE_PADDING: f64 = 4.0;
const AVG_CHAR_WIDTH_PX: f64 = 7.0;
const TABULAR_NUMS: &str = "font-variant-numeric: tabular-nums lining-nums;";
/// spark_bar.rs:`layout`. The bar area is never narrower than this.
const MIN_BAR_AREA: f64 = 20.0;
/// spark_bar.rs:`MORE_ROWS_OFFSET_Y`, `MORE_ROWS_BOTTOM_PADDING`: the "+N" note's baseline under the
/// last row, and the room under it.
const MORE_ROWS_OFFSET_Y: f64 = 12.0;
const MORE_ROWS_BOTTOM_PADDING: f64 = 8.0;
/// estimates.py:`SPARK_BAR_TITLE_HEIGHT_PX`, `SPARK_BAR_TITLE_BASELINE_Y_PX`: the title row's height
/// and its baseline, which production fixes rather than measures.
const TITLE_ROW: f64 = 24.0;
const TITLE_BASELINE: f64 = 16.0;

#[derive(Debug, Clone, Copy)]
pub struct SparkBarCard<'a> {
    /// The chart's public id, which a warning names.
    pub id: &'a str,
    pub spark: &'a SparkBar,
    pub heading: &'a Heading,
    pub style: &'a SparkBarChartStyle,
    pub face: &'a Face,
    /// The bar fill: [`fill_of`].
    pub fill: &'a str,
}

/// A spark_bar's title and subtitle above its rows (spark_bar.rs:`title_block`, `vertical`), in the
/// fixed title row, moved by its `cols` row's shift.
#[derive(Debug, Clone, PartialEq)]
pub struct Heading {
    title: Option<TitleLine>,
    subtitle: Option<SubtitleLine>,
    /// The title's whole-pixel size, which a subtitle under it moves down by.
    title_size: f64,
    shift: TitleShift,
}

#[derive(Debug, Clone, PartialEq)]
struct TitleLine {
    text: String,
    size: f64,
    weight: String,
    slant: String,
    family: String,
}

#[derive(Debug, Clone, PartialEq)]
struct SubtitleLine {
    text: String,
    size: f64,
    color: String,
    slant: Option<String>,
}

impl Heading {
    /// The `title` and `subtitle` as drawn (an empty one is none) of a chart whose slot is `width`
    /// wide. The title paints in `chart_title_spec`'s face at that width with the title slot's slant,
    /// cased as the board title is; the subtitle in `charts.spark_bar.subtitle.font`.
    pub fn of(title: Option<&str>, subtitle: Option<&str>, placed: &Style, width: f64, shift: TitleShift) -> Result<Self, Diagnostic> {
        let (size, weight, family) = crate::title::chart_title_spec(placed, width)?;
        let title = title
            .filter(|text| !text.is_empty())
            .map(|text| {
                // The board title's case, not the chart title slot's: chart-renderer spark_bar.rs:`bind` reads
                // `board_style.title_case`, which engine.py fills from `board_style.title.font.case`.
                let text = display_text::case::apply_case(text, placed.title.font.case.as_str()).map_err(diag::err_internal)?;
                // The title's stack paints under the board's emoji mode, as every face does.
                let family = Face::of(&family, size, &weight, "", &placed.font.emoji).family;
                Ok::<_, Diagnostic>(TitleLine { text, size, weight: Weight::of(&weight).attr(), slant: placed.charts.title.font.style.as_str().to_string(), family })
            })
            .transpose()?;
        let subtitle = subtitle
            .filter(|text| !text.is_empty())
            .map(|text| {
                let unset = |leaf: &str| diag::err_internal(format!("the theme's `charts.spark_bar.subtitle.font.{leaf}` is unset"));
                let font = placed.charts.spark_bar.subtitle.font.as_ref().ok_or_else(|| unset("*"))?;
                Ok::<_, Diagnostic>(SubtitleLine {
                    text: text.to_string(),
                    size: font.size.ok_or_else(|| unset("size"))?,
                    color: font.color.as_ref().ok_or_else(|| unset("color"))?.as_str().to_string(),
                    slant: font.style.map(|slant| slant.as_str().to_string()),
                })
            })
            .transpose()?;
        Ok(Self { title, subtitle, title_size: size.trunc(), shift })
    }

    /// The block's height before the shift: a row for a title or a subtitle, and a subtitle under a
    /// title adds the title's size.
    fn block(&self) -> f64 {
        match (&self.title, &self.subtitle) {
            (None, None) => 0.0,
            (Some(_), Some(_)) => TITLE_ROW + self.title_size,
            _ => TITLE_ROW,
        }
    }

    /// How much taller its row's band makes the card than the slot it is given.
    pub fn body_dy(&self) -> f64 {
        self.shift.body_dy
    }

    /// Where the rows start: under the shifted block, at the top when there is none.
    fn top(&self) -> f64 {
        if self.title.is_some() || self.subtitle.is_some() { self.block() + self.shift.body_dy } else { 0.0 }
    }

    /// A titled card's `(ascent, tail)` in its row's band from the card's top (title_band.py:
    /// `chart_title_metrics`); a subtitle alone keeps a card out of the band.
    pub fn band(&self) -> Option<(f64, f64)> {
        self.title.as_ref().map(|_| (TITLE_BASELINE, self.block() - TITLE_BASELINE))
    }

    fn nodes(&self, ink: &str, body_family: &str) -> Vec<Node> {
        let baseline = TITLE_BASELINE + self.shift.title_dy;
        let mut nodes = Vec::new();
        if let Some(title) = &self.title {
            let attrs = Attrs::new().num("font-size", title.size).quick("font-style", title.slant.as_str()).quick("font-weight", title.weight.as_str()).quick("fill", ink).quick("font-family", title.family.as_str()).quick("data-authored-kind", "title");
            nodes.push(text(Role::Title, (0.0, baseline), attrs, &title.text));
        }
        if let Some(subtitle) = &self.subtitle {
            let at = if self.title.is_some() { baseline + self.title_size } else { baseline };
            let mut attrs = Attrs::new().num("font-size", subtitle.size);
            if let Some(slant) = &subtitle.slant {
                attrs = attrs.quick("font-style", slant.as_str());
            }
            let attrs = attrs.quick("fill", subtitle.color.as_str()).quick("font-family", body_family).quick("data-authored-kind", "subtitle");
            nodes.push(text(Role::Title, (0.0, at), attrs, &subtitle.text));
        }
        nodes
    }
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
    /// Each row's label as drawn: cut to its column.
    labels: Vec<String>,
    /// The "+N" note of a chart cut to `max_bars`.
    more: Option<MoreNote>,
    /// The card's least height: its rows and the padding under them.
    pub natural_height: f64,
    /// `WARN-SPARK-LABEL-TRUNCATED`, when a label is cut.
    pub diagnostics: Vec<Diagnostic>,
}

#[derive(Debug, Clone, PartialEq)]
struct MoreNote {
    size: f64,
    color: String,
    slant: &'static str,
}

/// The height a spark_bar with no rows to measure is estimated at (sizing.py:`get_chart_content_height`):
/// `max_bars` rows and the padding under the last, under the title row when the chart is titled.
pub fn estimated_height(style: &SparkBarChartStyle, titled: bool) -> f64 {
    let body = f64::from(style.max_bars) * (style.bar.height + style.bar.padding) + style.bar.padding;
    if titled { body + TITLE_ROW } else { body }
}

/// spark_bar.rs:`vertical`: the shifted title block, the rows and the padding under the last, or,
/// for a chart cut to `max_bars`, the "+N" note's baseline under the rows and the room under it.
/// Production adds the row's shift to the block even when the card has no title.
pub fn natural_height(spark: &SparkBar, heading: &Heading, style: &SparkBarChartStyle) -> f64 {
    let bars = spark.rows.len() as f64 * (style.bar.height + style.bar.padding);
    if spark.more > 0 { heading.top() + bars + MORE_ROWS_OFFSET_Y + MORE_ROWS_BOTTOM_PADDING } else { heading.block() + heading.shift.body_dy + bars + style.bar.padding }
}

/// spark_bar.rs:`layout`: a label is cut by character count, not measured, to two characters
/// short of the column's count followed by "...".
fn cut_label(label: &str, max_chars: usize) -> Option<String> {
    (label.chars().count() > max_chars).then(|| format!("{}...", label.chars().take(max_chars.saturating_sub(2)).collect::<String>()))
}

/// The "+N" note's slot: the theme binds its size, color and slant.
fn more_note(style: &SparkBarChartStyle) -> Result<MoreNote, Diagnostic> {
    let unset = |leaf: &str| diag::err_internal(format!("the theme's `charts.spark_bar.more_rows.font.{leaf}` is unset"));
    let font = style.more_rows.font.as_ref().ok_or_else(|| unset("*"))?;
    Ok(MoreNote {
        size: font.size.ok_or_else(|| unset("size"))?,
        color: font.color.as_ref().ok_or_else(|| unset("color"))?.as_str().to_string(),
        slant: font.style.ok_or_else(|| unset("style"))?.as_str(),
    })
}

/// The card at `width`: the label column, the bar area and the least height.
pub fn plan(card: &SparkBarCard, width: f64) -> Result<Plan, Diagnostic> {
    let style = card.style;
    // Production counts a cut (and warns) whether or not the label column is drawn.
    let max_chars = (style.label.width / AVG_CHAR_WIDTH_PX) as usize;
    let mut truncated = 0;
    let labels = card.spark.rows.iter().map(|row| cut_label(&row.label, max_chars).inspect(|_| truncated += 1).unwrap_or_else(|| row.label.clone())).collect();
    let diagnostics = if truncated > 0 { vec![diag::warn_spark_label_truncated(card.id, truncated)] } else { Vec::new() };
    let more = if card.spark.more > 0 { Some(more_note(style)?) } else { None };
    let width = width.max(style.min_width);
    let left = if style.label.visible { style.label.width } else { SIDE_PADDING };
    let right = if style.count.visible { style.count.width } else { SIDE_PADDING };
    Ok(Plan { width, left, bar_area: (width - left - right).max(MIN_BAR_AREA), labels, more, natural_height: natural_height(card.spark, card.heading, style), diagnostics })
}

/// A bar's fraction of its span and whether it lies left of the midline: a negative anywhere puts
/// every bar on a shared midline, and an unsigned layout never has one.
fn fraction(value: f64, max: f64, signed: bool) -> (f64, bool) {
    let clamped = if signed { value.clamp(-max, max) } else { value.clamp(0.0, max) };
    (clamped.abs() / max, clamped < 0.0)
}

fn text(role: Role, at: (f64, f64), attrs: Attrs, body: &str) -> Node {
    let runs = vec![Run::Chars(Chars::new(body, Escape::Quick))];
    Node::new(role, attrs, Shape::Text { at, bounds: None, runs })
}

/// The card as nodes: its root `<svg>`, drawn in a slot `height` tall, or, cut to `max_bars`, at its
/// natural height (spark_bar.rs:`layout`: the note's bottom edge replaces the slot's).
pub fn draw(card: &SparkBarCard, plan: &Plan, height: f64) -> Vec<Node> {
    let (style, face) = (card.style, card.face);
    let row_height = style.bar.height + style.bar.padding;
    // spark_bar.rs:`layout`: the row renderer takes `int(bar_height)`, and baselines derive from it.
    let bar_height = style.bar.height.trunc();
    let radius = |width: f64| crate::geom::bar_corner_radius(style.border.radius, bar_height, width);
    let rect = |role: Role, x: f64, y: f64, w: f64, fill: &str| {
        Node::new(role, Attrs::new().quick("fill", fill), Shape::Rect { x, y, w, h: bar_height, rx: radius(w) })
    };
    let (track_x, half) = (plan.left.trunc(), plan.bar_area / 2.0);
    let top = card.heading.top();
    let mut nodes = card.heading.nodes(face.color.as_str(), face.family.as_str());
    for (i, (row, label)) in card.spark.rows.iter().zip(&plan.labels).enumerate() {
        let y = top + i as f64 * row_height;
        let (share, negative) = fraction(row.value, card.spark.max_value, card.spark.signed);
        let (width, x) = match card.spark.signed {
            true => (share * half, track_x + if negative { half - share * half } else { half }),
            false => (share * plan.bar_area, track_x),
        };
        let baseline = y + bar_height / 2.0 + TEXT_BASELINE_OFFSET;
        let ink = || Attrs::new().num("font-size", face.size).quick("fill", face.color.as_str()).quick("font-family", face.family.as_str());
        if style.label.visible {
            nodes.push(text(Role::Label, (0.0, baseline), ink(), label));
        }
        nodes.push(rect(Role::Chrome, track_x, y, plan.bar_area, style.bar.background.as_str()));
        if width > 0.0 {
            let mark = Role::Mark { layer: 0, series: None, datum: Some(i) };
            nodes.push(rect(mark, x, y, width, card.fill));
        }
        if let Some(count) = &row.count {
            let attrs = ink().quick("text-anchor", "end").quick("style", TABULAR_NUMS);
            nodes.push(text(Role::Label, (plan.width - SIDE_PADDING, baseline), attrs, count));
        }
    }
    if let Some(note) = &plan.more {
        let at = (plan.width / 2.0, top + card.spark.rows.len() as f64 * row_height + MORE_ROWS_OFFSET_Y);
        let attrs = Attrs::new().num("font-size", note.size).quick("fill", note.color.as_str()).quick("text-anchor", "middle").quick("font-style", note.slant).quick("font-family", face.family.as_str()).quick("style", TABULAR_NUMS);
        nodes.push(text(Role::Label, at, attrs, &format!("+{}", card.spark.more)));
    }
    let height = if plan.more.is_some() { plan.natural_height } else { height };
    let extent = Extent { width: plan.width, height };
    vec![Node::new(Role::Chrome, Attrs::new(), Shape::Svg { extent, children: nodes })]
}

impl crate::family::Family for SparkBarCard<'_> {
    type Plan = Plan;

    fn plan(&self, _env: &crate::family::Env, width: f64) -> Result<Plan, Diagnostic> {
        plan(self, width)
    }

    fn draw(&self, plan: Plan, height: f64) -> Result<Vec<Node>, Diagnostic> {
        Ok(draw(self, &plan, height))
    }
}
