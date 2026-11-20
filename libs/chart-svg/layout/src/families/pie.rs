//! A pie or donut: every wedge with its slice label and a donut's center total, in the box the size
//! stage gives the wheel, and in the table modes beside or under a table that lists its slices.
//! `plan` decides the mode, solves the wheel the box lets it draw (`d3rs::radial_fit`) and composes
//! the table; `draw` places the nodes under the chart's root `<svg>`.
//!
//! The rules are `chart-renderer/src/families/pie.rs` (`pie_layout_classified`, `candidate`) and
//! `pie/layout.rs` (`solve_wheel`, `wheel`, `attached_wheel`) with `compile/resolve/chart/pie_attachment.py`
//! (`plan_attachment`, `choose_table_placement`, `build_attached_table_columns`), cited at each law.
//!
//! The size stage decides the mode at one box (`pie_layout_classified`): the labeled mode the data
//! allows (`Pie::labeled`) stands when its wheel keeps 0.6 of the bare wheel's radius and its hole
//! holds the center total, else the pie is drawn as the table of every slice (`Pie::full`),
//! which always stands. The table modes draw the wheel at the theme's view height beside, above or under
//! the table, which the table family lays out; the canvas takes its height from them, so `Plan::height`
//! is the composed height the slot must be given. The authored `legend.position` places the table
//! (`choose_table_placement`, `decide_table_align`). A title over a table above the wheel, an authored side the card is too narrow for, and
//! the other shapes this slice does not draw, are refused by name.
//!
//! `draw` is the chart's root `<svg class="marks">` with the wedges under [`crate::knockout`]'s mask,
//! the center total and the slice labels. The stroke's `theme.background` names the board's card
//! canvas, not the chart's own. A pie over no rows draws no wheel: its title and the shared "No results" card
//! ([`crate::plot::empty::body`]).

use board_model::primitives::LinkOpen;
use bind::bound::{Href, Listed, Pie, PieKeyEdge, PieLabeled, PieTable, Table, TableCell, TableColumn};
use board_model::style::legend::LegendAlign;
use board_model::style::board::{EmojiMode, OverflowMode};
use board_model::style::pie::PieChartStyle;
use board_model::style::style::Style;
use board_model::style::table::TableChartStyle;
use d3rs::arc::Arc;
use d3rs::pie::pie;
use d3rs::radial_fit::{self, Callout, Ink, SolveError};
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::{Font, FontBook};
use measure::{baseline_shift, stand_in_family};
use model_core::Engine;
use scene::{Attrs, Chars, Escape, Extent, Node, Role, Run, Shape};

use super::failed::Weight;
use super::table::{self, TableCard, TableFaces};
use crate::contract::{aria_attrs, hidden_mark_group, link, mark_group, ItemRole};
use crate::knockout::{knock_out, safe_scope};
use crate::scene_path::scene_path;
use crate::geom::Rect;
use crate::text_ink;
use crate::board::band::TitleShift;
use crate::title::{chart_title, ChartTitle};

/// `WHEEL_DOMINANCE_MIN_RATIO`: below this fraction of the bare wheel's radius, a labeled mode
/// starves the wheel badly enough to prefer the table.
const WHEEL_DOMINANCE_MIN_RATIO: f64 = 0.6;
/// `HOLE_TOTAL_MARGIN_PX`: the gap the center total keeps from the inner ring.
const HOLE_TOTAL_MARGIN_PX: f64 = 4.0;
/// `OUTER_FRACTION` in `core/constants/pie.py`: a wheel with nothing to make room for leaves its
/// box a tenth of whitespace.
const OUTER_FRACTION: f64 = 0.9;
/// `ATTACHED_TABLE_GAP_PX` in `core/constants/pie.py`: the gap between the wheel and its table.
const ATTACHED_TABLE_GAP: f64 = 12.0;
/// `SIDE_TABLE_INK_GAP_PX`: the gap between a table beside the wheel and the wheel's ink.
const SIDE_TABLE_INK_GAP: f64 = 24.0;
/// `HYBRID_HEADING_GAP_PX`: the gap under the hybrid table's heading.
const HYBRID_HEADING_GAP: f64 = 6.0;
/// `RIGHT_PLACEMENT_MIN_WIDTH_FRACTION` and `_PX`: the table goes beside the wheel only when the wheel
/// region it leaves is at least this share of the card and this wide; `RIGHT_PLACEMENT_MAX_WHEEL_PX`
/// caps that region.
const BESIDE_MIN_FRACTION: f64 = 0.5;
const BESIDE_MIN_PX: f64 = 280.0;
const BESIDE_MAX_WHEEL_PX: f64 = 480.0;
/// `build_attached_table_columns`: the swatch column's width, and the floors of the others.
const SWATCH_COLUMN_PX: f64 = 24.0;
const SHARE_COLUMN_FLOOR: f64 = 36.0;
const NAME_COLUMN_FLOOR: f64 = 60.0;
const VALUE_COLUMN_FLOOR: f64 = 50.0;

/// A pie this slice does not draw.
#[derive(Clone, Copy)]
enum Refused {
    Stroke,
    TitledTop,
    NarrowSide,
    NoRoom,
    WideTotal,
    Weight,
    TitleClip,
}

impl Refused {
    fn refusal(self) -> Diagnostic {
        let what = match self {
            Refused::Stroke => "whose slice stroke is not a set color, width and join",
            Refused::TitledTop => "with a title over a table above the wheel",
            Refused::NarrowSide => "with an authored table side the card is too narrow for",
            Refused::NoRoom => "drawn in a box its labels leave no room in",
            Refused::WideTotal => "whose total is wider than its hole",
            Refused::Weight => "whose font weight names no axis value",
            Refused::TitleClip => "whose title `overflow: clip` cuts it",
        };
        Diagnostic::refused(refuse!("a pie {what} is not supported"))
    }
}

/// A text face as the pie paints and measures it.
#[derive(Debug, Clone, PartialEq)]
pub struct PieFace {
    family: String,
    /// The family production measures in.
    measured: String,
    size: f64,
    weight: Weight,
    italic: bool,
    color: String,
}

impl PieFace {
    fn of(family: &str, size: f64, weight: &board_model::facet::FontWeightValue, italic: bool, color: &str, emoji: &EmojiMode) -> Self {
        Self {
            family: bind::font_stack::with_emoji(family, emoji),
            measured: stand_in_family(family).to_string(),
            size,
            weight: Weight::of(weight),
            italic,
            color: color.to_string(),
        }
    }

    /// `Font::measure_weighted`: the width at the weight the text paints at.
    fn width(&self, book: &FontBook, text: &str) -> Result<f64, Diagnostic> {
        let weight = measure::fonts::css_weight_axis(&self.weight.attr()).map_err(diag::err_internal)?.ok_or_else(|| Refused::Weight.refusal())?;
        book.weighted_advance(text, &Font { family: &self.measured, size: self.size, weight, italic: self.italic }).map_err(diag::err_internal)
    }

    fn attrs(&self, anchor: &str) -> Attrs {
        Attrs::new()
            .set("text-anchor", anchor)
            .vega("font-family", self.family.as_str())
            .num("font-size", self.size)
            .set("font-style", if self.italic { "italic" } else { "normal" })
            .set("font-weight", self.weight.attr())
            .set("fill", self.color.as_str())
    }
}

/// The chart's pie and the style it draws with.
pub struct PieCard<'a> {
    pub pie: &'a Pie,
    pub style: &'a PieChartStyle,
    /// The placement's total style, which the chart title slot is read from.
    pub placed: &'a Style,
    pub chart_id: &'a str,
    pub emoji: &'a EmojiMode,
}

/// A solved wheel: where it sits in its box and its radius, and the table composed with it.
#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    cx: f64,
    cy: f64,
    r: f64,
    labels: Vec<Placed>,
    center: Option<CenterInk>,
    /// The title block in the card's padding, which the wheel's box sits under.
    title: Option<ChartTitle>,
    /// The title block's top left, the card's padding corner.
    title_at: (f64, f64),
    /// The table the wheel is drawn beside or under (`Pie::labeled`'s hybrid table or `Pie::full`).
    attached: Option<Attached>,
    /// The canvas's height: the box's own for a wheel alone, else what the wheel and table stack to.
    height: f64,
    /// A pie over no rows draws no wheel: its card's "No results" text, centered in the plot box.
    no_results: Vec<Node>,
}

/// Where the table sits (`LegendEdge`): the authored edge, else the width decides (`choose_table_placement`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Placement {
    Left,
    Right,
    Top,
    Bottom,
}

/// The hybrid table's heading (`Heading`): the table's body font at the header's weight.
#[derive(Debug, Clone, PartialEq)]
struct Heading {
    text: String,
    size: f64,
    family: String,
    weight: String,
    color: String,
}

/// A pie's table measured and laid out: the synthetic table `attachment_for` resolves (no header,
/// no paging, no stripe) over the rows a mode lists.
#[derive(Debug, Clone, PartialEq)]
struct Attached {
    placement: Placement,
    table: Table,
    style: TableChartStyle,
    faces: TableFaces,
    laid: table::Plan,
    table_width: f64,
    wheel_width: f64,
    /// The theme's view height the wheel is drawn at (`view.continuous_height`), plus the table's room
    /// when the table sits above it.
    wheel_height: f64,
    /// Where the table starts and ends across or down its side (`decide_table_align`).
    align: LegendAlign,
    /// The top left of the wheel's box and of the table in the canvas: above or below the wheel as
    /// the table is measured, the rest once the wheel is solved (`wheel_plan`).
    wheel_at: (f64, f64),
    table_at: (f64, f64),
    /// The canvas width and its left and right padding, which a table beside the wheel stays inside.
    across: (f64, f64, f64),
    heading: Option<Heading>,
    /// The table's height with its heading.
    height: f64,
}

impl Plan {
    /// The warnings the card's width raised against its title and subtitle.
    pub fn diagnostics(&self) -> &[Diagnostic] {
        self.title.as_ref().map_or(&[], |title| &title.diagnostics)
    }

    /// The canvas's height: the box's own for a wheel alone, else the wheel and its table stacked.
    pub fn height(&self) -> f64 {
        self.height
    }

    /// The pie is drawn beside or under a table, which composes its own canvas.
    pub fn is_table(&self) -> bool {
        self.attached.is_some()
    }
}

/// Which of a wheel's ink groups a solve makes room for (`Parts` in `chart-renderer`).
#[derive(Clone, Copy)]
struct Parts {
    labels: bool,
    total: bool,
}

/// `Mode.parts`: the direct and hybrid modes draw the labels and the total, the full table no labels; `BARE`
/// is the reference wheel with no ink group.
const LABELED: Parts = Parts { labels: true, total: true };
const BARE: Parts = Parts { labels: false, total: false };
const NO_LABELS: Parts = Parts { labels: false, total: true };

#[derive(Debug, Clone, PartialEq)]
struct Placed {
    slice: usize,
    mid: f64,
    max_w: f64,
    /// The label block's ink top and bottom relative to its anchor row (`Callout::dy` and `ink`).
    ink_y: (f64, f64),
}

#[derive(Debug, Clone, PartialEq)]
struct CenterInk {
    value_w: f64,
    caption_w: f64,
}

/// The ink of a single line of text `width` wide at its anchor (`crate::text_ink`).
fn text_ink(width: f64, font_size: f64, align: &str, baseline: &str) -> Ink {
    let (dx, dy) = (text_ink::left(width, align), text_ink::rise(font_size, baseline));
    Ink { x1: dx, y1: dy, x2: dx + width, y2: dy + font_size }
}

struct Faces {
    label: PieFace,
    value: PieFace,
    caption: PieFace,
}

fn faces(card: &PieCard) -> Faces {
    let labels = &card.style.marks.slice.labels.font;
    let (value, caption) = (&card.style.total.value.slot.font, &card.style.total.label.font);
    let italic = |slant: &str| slant == "italic";
    Faces {
        label: PieFace::of(labels.family.as_str(), labels.size, &labels.weight, italic(labels.style.as_str()), "", card.emoji),
        value: PieFace::of(value.family.as_str(), value.size, &value.weight, italic(value.style.as_str()), value.color.as_str(), card.emoji),
        caption: PieFace::of(caption.family.as_str(), caption.size, &caption.weight, italic(caption.style.as_str()), caption.color.as_str(), card.emoji),
    }
}

fn stroke(card: &PieCard) -> Result<(String, f64, String), Diagnostic> {
    let s = &card.style.marks.slice.stroke;
    match (&s.color, &s.width, &s.join) {
        // `theme.background` is the stroke's self-reference to the board's card canvas (`_resolve_self_tokens`).
        (Engine::Value(color), Engine::Value(width), Some(Engine::Value(join))) => {
            let color = if color.as_str() == "theme.background" { card.pie.ground.as_str() } else { color.as_str() };
            Ok((color.to_string(), width.get(), join.as_str().to_string()))
        }
        _ => Err(Refused::Stroke.refusal()),
    }
}

/// Why a wheel could not be solved: `NoRoom` is the solve's own answer (the ink leaves no positive
/// radius), which the size stage takes as "use the table".
enum Unsolved {
    NoRoom,
    Failed(Diagnostic),
}

struct Solved {
    wheel: radial_fit::Wheel,
    min_radius: Option<f64>,
    placed: Vec<Placed>,
    center: Option<CenterInk>,
}

/// One solve of the wheel in `(w, h)` inside `pad` (left, right, top, bottom): `parts` reserves room
/// for the slice labels and the center total (`solve_wheel`). The radius, and the
/// smallest radius whose hole holds the total.
fn solve(card: &PieCard, book: &FontBook, (w, h): (f64, f64), pad: (f64, f64, f64, f64), parts: Parts) -> Result<Solved, Unsolved> {
    let pie_ = card.pie;
    let faces = faces(card);
    let labels_style = &card.style.marks.slice.labels;
    let mut mids = Vec::with_capacity(pie_.slices.len());
    let mut running = 0.0;
    for slice in &pie_.slices {
        mids.push(std::f64::consts::TAU * (running + slice.value / 2.0) / pie_.total);
        running += slice.value;
    }
    let mut placed = Vec::new();
    let mut center = None;
    if parts.labels {
        for (idx, slice) in pie_.slices.iter().enumerate() {
            let Some(label) = &slice.label else { continue };
            let mut max_w = 0.0_f64;
            for line in &label.lines {
                max_w = max_w.max(faces.label.width(book, line).map_err(Unsolved::Failed)?);
            }
            let lines = label.lines.len() as f64;
            let b = text_ink(max_w, labels_style.font.size, "left", "top");
            let dy = if mids[idx].cos() >= 0.0 { -lines * labels_style.line_height } else { 0.0 };
            placed.push(Placed { slice: idx, mid: mids[idx], max_w, ink_y: (dy + b.y1, dy + b.y2 + (lines - 1.0) * labels_style.line_height) });
        }
    }
    if parts.total {
        if let Some(c) = &pie_.center {
            center = Some(CenterInk { value_w: faces.value.width(book, &c.value).map_err(Unsolved::Failed)?, caption_w: faces.caption.width(book, &c.caption).map_err(Unsolved::Failed)? });
        }
    }
    let (_, stroke_w, _) = stroke(card).map_err(Unsolved::Failed)?;
    let plot = Ink { x1: pad.0, y1: pad.2, x2: w - pad.1, y2: h - pad.3 };
    let cap = (plot.x2 - plot.x1).min(plot.y2 - plot.y1) / 2.0 * OUTER_FRACTION;
    let centered: Vec<Ink> = center
        .iter()
        .flat_map(|c| {
            let centered = |w: f64, size: f64, baseline: &str| text_ink(w, size, "center", baseline);
            [centered(c.value_w, faces.value.size, "bottom"), centered(c.caption_w, faces.caption.size, "top")]
        })
        .collect();
    let (size, line_height, offset) = (labels_style.font.size, labels_style.line_height, labels_style.offset);
    let callouts: Vec<Callout> = placed
        .iter()
        .map(|sl| {
            let lines = pie_.slices[sl.slice].label.as_ref().map_or(0, |l| l.lines.len()) as f64;
            let b = text_ink(sl.max_w, size, if sl.mid.sin() >= 0.0 { "left" } else { "right" }, "top");
            Callout {
                sin: sl.mid.sin(),
                cos: sl.mid.cos(),
                offset,
                dy: if sl.mid.cos() >= 0.0 { -lines * line_height } else { 0.0 },
                ink: Ink { x1: b.x1, y1: b.y1, x2: b.x2, y2: b.y2 + (lines - 1.0) * line_height },
            }
        })
        .collect();
    let wheel = radial_fit::solve(plot, cap, stroke_w, &callouts, &centered).map_err(|e| match e {
        SolveError::NoRoom(_) => Unsolved::NoRoom,
        SolveError::Inconsistent(why) => Unsolved::Failed(diag::err_internal(format!("pie: {why}"))),
    })?;
    let min_radius = radial_fit::min_radius_for_hole(&centered, HOLE_TOTAL_MARGIN_PX, card.pie.hole);
    Ok(Solved { wheel, min_radius, placed, center })
}

#[cfg(test)]
mod tests {
    use super::beside;

    /// `compose`'s `beside` pins: a centered wheel the sides mirror around, and on a tight card the
    /// far ink kept inside it (700px, a 246px reach away from a left table: the center stops at 438).
    #[test]
    fn a_table_beside_the_wheel_mirrors_and_keeps_the_far_ink_in_the_card() {
        let (pad, ink) = ((16.0, 16.0), (156.0, 246.0));
        assert_eq!(beside(true, 1200.0, pad, 200.0, ink), (600.0, 870.0));
        assert_eq!(beside(false, 1200.0, pad, 200.0, ink), (600.0, 130.0));
        assert_eq!(beside(false, 700.0, pad, 200.0, ink), (438.0, 16.0));
        assert_eq!(beside(true, 700.0, pad, 200.0, (246.0, 156.0)), (262.0, 484.0));
    }
}

/// The title block a wheel framed in `width` takes the top of the padding for, and the padding
/// the wheel then sits in.
struct Framed {
    title: Option<ChartTitle>,
    title_at: (f64, f64),
    pad: (f64, f64, f64, f64),
}

fn framed(card: &PieCard, book: &FontBook, tier: f64, width: f64, pad: (f64, f64, f64, f64)) -> Result<Framed, Diagnostic> {
    // The title takes the top of the card's padding box, in the mode guard's solve as in the drawn one.
    let title = chart_title(card.chart_id, card.pie.title.as_deref().unwrap_or_default(), card.pie.subtitle.as_deref(), card.placed, tier, width - pad.0 - pad.1, book)?;
    // Production clips by its own estimate, not by the painted face: a title `clip` cuts is refused
    // until the clip law is ported.
    let clipped = card.placed.charts.title.base.overflow == OverflowMode::Clip
        && title.as_ref().is_some_and(|t| t.diagnostics.iter().any(|d| d.code == diagnostic::codes::WARN_CHART_TITLE_TRUNCATED.id && d.params.get("authored_field").and_then(|f| f.as_str()) == Some("title")));
    if clipped {
        return Err(Refused::TitleClip.refusal());
    }
    let title_h = title.as_ref().map_or(0.0, |t| t.band.height());
    Ok(Framed { title, title_at: (pad.0, pad.2), pad: (pad.0, pad.1, pad.2 + title_h, pad.3) })
}

/// `plan_attachment`'s `column_width` over the strings a column is measured at, at the table's body
/// weight: the share and name columns' cells (a null name measures as nothing, as production's
/// `str(row["name"] or "")` does), the value column's `value_widths`.
fn column_width<'t>(face: &PieFace, book: &FontBook, texts: impl IntoIterator<Item = std::borrow::Cow<'t, str>>, floor: f64, pad: f64) -> Result<f64, Diagnostic> {
    let mut widest = 0.0_f64;
    for text in texts {
        widest = widest.max(face.width(book, &text)?);
    }
    Ok(floor.max((widest + pad * 2.0 + 3.0).ceil()))
}

fn cell_texts(column: &TableColumn) -> impl Iterator<Item = std::borrow::Cow<'_, str>> {
    column.cells.iter().map(|cell| match cell {
        TableCell::Null => "".into(),
        cell => cell.text(),
    })
}

/// `attachment_for` and `plan_attachment`: the table measured into `width`, where it sits
/// (`choose_table_placement`: beside the wheel when the region it leaves stays wide, else under it) and
/// the width the wheel gets. Production sets the table's header hidden, paging off and its row stripe
/// null: a wedge legend is a key, not a dense data table.
fn attach(card: &PieCard, book: &FontBook, listed: &PieTable, width: f64, card_pad: (f64, f64, f64, f64)) -> Result<Attached, Diagnostic> {
    // A table above the wheel under a title shifts the title down its height (`shift_vega_payload`), which is not ported.
    let authored = card.pie.table_position;
    if authored.edge == Some(PieKeyEdge::Top) && card.pie.title.is_some() {
        return Err(Refused::TitledTop.refusal());
    }
    let mut style = TableChartStyle::clone(&card.placed.charts.table);
    style.header.visible = false;
    style.pagination.enabled = false;
    style.row.stripe = None;
    let faces = TableFaces::of(&style, card.placed, None, None, width)?;
    let font = &style.font;
    let face = PieFace::of(font.family.as_str(), font.size, &font.weight, font.style.as_str() == "italic", "", card.emoji);
    let pad = style.column_layout.cell_padding;
    let mut table = listed.table.clone();
    let [swatch, share, name, value] = table.columns.as_mut_slice() else {
        return Err(diag::err_internal("a pie's attached table holds a swatch, share, name and value column"));
    };
    // The swatch column is a constant (`swatch_width = 24`); the others fit their widest measured text.
    swatch.width = Some(SWATCH_COLUMN_PX);
    share.width = Some(column_width(&face, book, cell_texts(share), SHARE_COLUMN_FLOOR, pad)?);
    name.width = Some(column_width(&face, book, cell_texts(name), NAME_COLUMN_FLOOR, pad)?);
    value.width = Some(column_width(&face, book, listed.value_widths.iter().map(|text| text.as_str().into()), VALUE_COLUMN_FLOOR, pad)?);
    let natural: f64 = table.columns.iter().filter_map(|column| column.width).sum();
    let region = width - natural - ATTACHED_TABLE_GAP;
    let beside = region >= BESIDE_MIN_FRACTION * width && region >= BESIDE_MIN_PX;
    // `choose_table_placement`: a top or bottom edge as authored; none, beside the wheel when it fits, else under;
    // a side edge as authored when it fits. An authored side that does not fit is placed under with a warning
    // (`overridden_edge`), which the engine does not raise, so it is refused.
    let placement = match (authored.edge, beside) {
        (Some(PieKeyEdge::Top), _) => Placement::Top,
        (Some(PieKeyEdge::Bottom), _) => Placement::Bottom,
        (None, true) => Placement::Right,
        (None, false) => Placement::Bottom,
        (Some(PieKeyEdge::Left), true) => Placement::Left,
        (Some(PieKeyEdge::Right), true) => Placement::Right,
        (Some(PieKeyEdge::Left | PieKeyEdge::Right), false) => return Err(Refused::NarrowSide.refusal()),
    };
    let (wheel_width, table_width) = match placement {
        Placement::Left | Placement::Right => (region.min(BESIDE_MAX_WHEEL_PX), natural),
        Placement::Top | Placement::Bottom => (width, natural.min(width)),
    };
    // `decide_table_align`: authored, else the center.
    let align = authored.align.unwrap_or(LegendAlign::Center);
    let (pad_l, pad_r, pad_t, _) = card_pad;
    let laid = table::plan(&TableCard { table: &table, style: &style, faces: &faces, cell: None }, book, table_width, None, TitleShift::default())?;
    let heading = listed.heading.as_ref().map(|text| Heading { text: text.clone(), size: faces.body.size, family: faces.body.family.clone(), weight: faces.header.weight.attr(), color: faces.body.color.clone() });
    let block = heading.as_ref().map_or(0.0, |heading| heading.size + 2.0 + HYBRID_HEADING_GAP);
    let height = laid.height + block;
    // A table above the wheel adds its height and the gap to the wheel's view (`placement == "top"`).
    let wheel_height = card.placed.charts.view.continuous_height + if placement == Placement::Top { height + ATTACHED_TABLE_GAP } else { 0.0 };
    // A table above the wheel sits under the card's top padding; one below it once the wheel is solved.
    let table_y = if placement == Placement::Top { pad_t } else { 0.0 };
    let wheel_at = ((width - wheel_width) / 2.0, 0.0);
    Ok(Attached { placement, height, table, style, faces, laid, table_width, wheel_width, wheel_height, align, wheel_at, table_at: (0.0, table_y), across: (width, pad_l, pad_r), heading })
}

/// The pie in the mode the size stage chooses (`pie_layout_classified`) and the wheel in the box it is
/// drawn in. `sized` is the box the mode is decided in (`pie_box`, the slot's natural one), which
/// the guard reads; a wheel alone is drawn in `(w, h)` inside `pad` (left, right, top, bottom). A
/// labeled mode (`Pie::labeled`) stands only if its wheel keeps `WHEEL_DOMINANCE_MIN_RATIO` of the bare
/// wheel's radius and its hole holds the center total, however narrow its table leaves the wheel
/// box; otherwise the pie is the full table, which always stands. A table pie composes its own canvas,
/// `Plan::height` tall: the wheel at the theme's view height, then the table.
/// `shift` is the `cols` row's title shift (`title_shift.rs::shift_vega_payload`). The mode guard
/// solves unshifted, since the size stage decides the mode before shifts are assigned. A directly
/// drawn wheel takes the shift: the top padding grows by `body_dy`, the title moves by `body_dy` less
/// the whole pixels `round(body_dy - title_dy)` the title-to-wheel gap takes, and the wheel's box by all
/// of `body_dy`. A table pie stays out of the `cols` row's title band, so it takes none.
pub fn plan(card: &PieCard, book: &FontBook, sized: (f64, f64), (w, h): (f64, f64), pad: (f64, f64, f64, f64), shift: TitleShift) -> Result<Plan, Diagnostic> {
    let solve = |size: (f64, f64), frame: &Framed, parts: Parts| match solve(card, book, size, frame.pad, parts) {
        Ok(solved) => Ok(Some(solved)),
        Err(Unsolved::NoRoom) => Ok(None),
        Err(Unsolved::Failed(e)) => Err(e),
    };
    let no_room = || Refused::NoRoom.refusal();
    let whole = framed(card, book, sized.0, sized.0, pad)?;
    if let Some(empty) = &card.pie.empty {
        // `no_results::render`: the plot box is the card inside its padding and under the title.
        let frame = framed(card, book, w, w, pad)?;
        let area = Rect { x: pad.0, y: frame.pad.2, width: w - pad.0 - pad.1, height: h - frame.pad.2 - pad.3 };
        let filters = empty.empty.filters.as_deref().map(|line| (&card.placed.charts.title.base.subtitle.font, line));
        let no_results = crate::plot::empty::body(&empty.headline, filters, area, book, card.emoji)?;
        return Ok(Plan { cx: 0.0, cy: 0.0, r: 0.0, labels: Vec::new(), center: None, title: frame.title, title_at: frame.title_at, attached: None, height: h, no_results });
    }
    if shift != TitleShift::default() && whole.title.is_none() {
        return Err(diag::err_internal(format!("the pie {} is given a title shift with no title block to move", card.chart_id)));
    }
    let Some(free) = solve(sized, &whole, BARE)? else { return Err(no_room()) };
    let stands = |ink: &Solved| ink.wheel.r >= WHEEL_DOMINANCE_MIN_RATIO * free.wheel.r && ink.min_radius.is_none_or(|min| ink.wheel.r >= min);
    // A wheel beside, above or under its table, drawn in the box the table leaves it.
    let beside = |listed: &Listed, parts: Parts| -> Result<Option<(Attached, Framed, Solved)>, Diagnostic> {
        if shift != TitleShift::default() {
            return Err(diag::err_internal(format!("the table pie {} is given a title shift, but a table pie stays out of the title band", card.chart_id)));
        }
        let attached = attach(card, book, listed.as_ref().map_err(Clone::clone)?, sized.0, pad)?;
        // The wheel under a table above it starts below it: the top padding grows by the table's room (`bump_padding_top`).
        let room = if attached.placement == Placement::Top { attached.height + ATTACHED_TABLE_GAP } else { 0.0 };
        let frame = framed(card, book, sized.0, attached.wheel_width, (pad.0, pad.1, pad.2 + room, pad.3))?;
        Ok(solve((attached.wheel_width, attached.wheel_height), &frame, parts)?.map(|solved| (attached, frame, solved)))
    };
    match &card.pie.labeled {
        PieLabeled::Bare => {
            let mut frame = framed(card, book, w, w, (pad.0, pad.1, pad.2 + shift.body_dy, pad.3))?;
            frame.title_at.1 -= (shift.body_dy - shift.title_dy).round_ties_even();
            let drawn = solve((w, h), &frame, NO_LABELS)?.ok_or_else(no_room)?;
            return Ok(wheel_plan(drawn, frame, None, h, card.style.marks.slice.labels.offset));
        }
        PieLabeled::Direct => {
            if solve(sized, &whole, LABELED)?.is_some_and(|ink| stands(&ink)) {
                let mut frame = framed(card, book, w, w, (pad.0, pad.1, pad.2 + shift.body_dy, pad.3))?;
                frame.title_at.1 -= (shift.body_dy - shift.title_dy).round_ties_even();
                let drawn = solve((w, h), &frame, LABELED)?.ok_or_else(no_room)?;
                return Ok(wheel_plan(drawn, frame, None, h, card.style.marks.slice.labels.offset));
            }
        }
        PieLabeled::Hybrid(listed) => {
            if let Some((attached, frame, ink)) = beside(listed, LABELED)?.filter(|(_, _, ink)| stands(ink)) {
                return Ok(wheel_plan(ink, frame, Some(attached), 0.0, card.style.marks.slice.labels.offset));
            }
        }
        PieLabeled::None => {}
    }
    let (attached, frame, ink) = beside(&card.pie.full, NO_LABELS)?.ok_or_else(no_room)?;
    Ok(wheel_plan(ink, frame, Some(attached), 0.0, card.style.marks.slice.labels.offset))
}

/// `compose`'s `beside`: where the wheel's center and a table beside it (on the `right`, else the
/// left) sit across a canvas `w` wide inside its `pad` (left, right). The wheel centers in the
/// canvas and the table sits the gap past the wheel's farther ink reach (`ink`: left, right of the
/// center), so the two sides mirror. A table that would cross the padding stops there and takes
/// the wheel along, but never past the wheel's far ink: on a card too tight for both, the gap
/// shrinks.
fn beside(right: bool, w: f64, pad: (f64, f64), table_w: f64, ink: (f64, f64)) -> (f64, f64) {
    let gap = SIDE_TABLE_INK_GAP;
    // Solved for a right table; a left one is its mirror image.
    let solve = |(pad_far, pad_table): (f64, f64), (near, far): (f64, f64)| {
        let half = near.max(far);
        let edge = w - pad_table - table_w - gap;
        let x = if w / 2.0 + half > edge { (edge - half).max(pad_far + far) } else { w / 2.0 };
        (x, (x + half + gap).min(w - pad_table - table_w))
    };
    if right {
        solve(pad, (ink.1, ink.0))
    } else {
        let (x, table_x) = solve((pad.1, pad.0), ink);
        (w - x, w - table_x - table_w)
    }
}

/// The wheel solved in its frame as a plan; a table pie's height is what the wheel and table stack to
/// (`compose`): the table under the wheel (the gap between), or beside it aligned down the wheel's
/// ink box. A side table that would ride above the plot's top, into the title, stays there and the
/// wheel moves down to align on it. Beside the wheel the wheel centers in the card and the table
/// sits the gap past its ink (`label_offset` places the slice labels), so the sides mirror; a table
/// that would cross the card's padding stops there and takes the wheel along. The title stays on
/// the card's own corner.
fn wheel_plan(solved: Solved, mut frame: Framed, mut attached: Option<Attached>, h: f64, label_offset: f64) -> Plan {
    let wheel = &solved.wheel;
    // The box the whole wheel consumes around its center: the disk, the slice labels and the center
    // total (left, right, top, bottom). A table aligns on it, not on the disk.
    let total = solved.center.as_ref().map_or(0.0, |center| center.value_w.max(center.caption_w) / 2.0);
    let ink = solved.placed.iter().fold((wheel.r.max(total), wheel.r.max(total), wheel.r, wheel.r), |(left, right, top, bottom), placed| {
        let (reach, rise) = (placed.mid.sin() * (wheel.r + label_offset), -placed.mid.cos() * (wheel.r + label_offset));
        let (top, bottom) = (top.max(-(rise + placed.ink_y.0)), bottom.max(rise + placed.ink_y.1));
        if reach >= 0.0 { (left, right.max(reach + placed.max_w), top, bottom) } else { (left.max(placed.max_w - reach), right, top, bottom) }
    });
    // Above or below the wheel the table aligns across the ink box, inside the card's padding.
    let across = |a: &Attached| {
        let (width, pad_l, pad_r) = a.across;
        let cx = a.wheel_at.0 + wheel.cx;
        match a.align {
            LegendAlign::Start => cx - ink.0,
            LegendAlign::Center => cx + (ink.1 - ink.0) / 2.0 - a.table_width / 2.0,
            LegendAlign::End => cx + ink.1 - a.table_width,
        }
        .min(width - pad_r - a.table_width)
        .max(pad_l)
    };
    let height = match &mut attached {
        None => h,
        Some(a) if a.placement == Placement::Bottom => {
            a.table_at = (across(a), a.wheel_height + ATTACHED_TABLE_GAP);
            a.wheel_height + ATTACHED_TABLE_GAP + a.height
        }
        Some(a) if a.placement == Placement::Top => {
            a.table_at.0 = across(a);
            a.wheel_height
        }
        Some(a) => {
            let table_y = match a.align {
                LegendAlign::Start => wheel.cy - ink.2,
                LegendAlign::Center => wheel.cy + (ink.3 - ink.2) / 2.0 - a.height / 2.0,
                LegendAlign::End => wheel.cy + ink.3 - a.height,
            };
            let wheel_y = (frame.pad.2 - table_y).max(0.0);
            let (width, pad_l, pad_r) = a.across;
            let (center, table_x) = beside(a.placement == Placement::Right, width, (pad_l, pad_r), a.table_width, (ink.0, ink.1));
            a.wheel_at = (center - wheel.cx, wheel_y);
            a.table_at = (table_x, table_y + wheel_y);
            (wheel_y + a.wheel_height).max(table_y + wheel_y + a.height)
        }
    };
    if let Some(a) = &attached {
        frame.title_at = (frame.title_at.0 - a.wheel_at.0, frame.title_at.1 - a.wheel_at.1);
    }
    Plan { cx: wheel.cx, cy: wheel.cy, r: wheel.r, labels: solved.placed, center: solved.center, title: frame.title, title_at: frame.title_at, attached, height, no_results: Vec::new() }
}

/// The chart's root `<svg class="marks">`: the background, the wheel, the center total and the
/// slice labels, in `(w, h)`. A table pie is the wheel (`wheel_nodes` in the box the table leaves
/// it) and the table, in `w` by `Plan::height`.
pub fn draw(card: &PieCard, plan: &Plan, (w, h): (f64, f64)) -> Result<Node, Diagnostic> {
    let Some(attached) = &plan.attached else {
        return Ok(root(w, h, wheel_nodes(card, plan, (w, h))?));
    };
    let wheel = Node::group(Role::Chrome, Attrs::new(), wheel_nodes(card, plan, (attached.wheel_width, attached.wheel_height))?);
    let table_card = TableCard { table: &attached.table, style: &attached.style, faces: &attached.faces, cell: None };
    let mut table_nodes = table::draw(&table_card, &attached.laid)?;
    if let Some(heading) = &attached.heading {
        let block = heading.size + 2.0 + HYBRID_HEADING_GAP;
        let text = Node::text(
            Role::Title,
            (0.0, heading.size * 0.8),
            Attrs::new().set("font-family", heading.family.as_str()).num("font-size", heading.size).set("font-weight", heading.weight.as_str()).set("fill", heading.color.as_str()),
            heading.text.as_str(),
        );
        let table = Node::group(Role::Chrome, Attrs::new(), table_nodes).translate(0.0, block);
        table_nodes = vec![Node::new(Role::Chrome, Attrs::new(), Shape::Svg { extent: Extent { width: attached.table_width, height: attached.height }, children: vec![text, table] })];
    }
    let (wheel_at, table_at) = (attached.wheel_at, attached.table_at);
    let table = Node::group(Role::Chrome, Attrs::new(), table_nodes);
    Ok(root(w, plan.height, vec![wheel.translate(wheel_at.0, wheel_at.1), table.translate(table_at.0, table_at.1)]))
}

/// A card's `<svg class="marks">` of `(w, h)`, the only root of the chart's nodes.
fn root(w: f64, h: f64, children: Vec<Node>) -> Node {
    Node::new(Role::Chrome, Attrs::new().set("class", "marks"), Shape::Svg { extent: Extent { width: w, height: h }, children })
}

/// `href::linked`: a mark inside its slice's `<a>`, as the shared `href` encoding paints every layer.
fn linked(href: Option<&Href>, open: LinkOpen, mark: Node) -> Node {
    match href {
        Some(href) => link(Attrs::new().quick("href", href.as_str()), open, vec![mark]),
        None => mark,
    }
}

/// The wheel's nodes in a `(w, h)` box: its background, the wedges, the center total and the slice labels.
fn wheel_nodes(card: &PieCard, plan: &Plan, (w, h): (f64, f64)) -> Result<Vec<Node>, Diagnostic> {
    let internal = |e: String| diag::err_internal(e);
    let (pie_, style) = (card.pie, card.style);
    let mut nodes = vec![Node::rect(Role::Chrome, (0.0, 0.0, w, h), Attrs::new().set("fill", pie_.background.as_str()))];
    if let Some(title) = &plan.title {
        nodes.extend(title.nodes(plan.title_at, &card.placed.charts.title, card.emoji));
    }
    if pie_.empty.is_some() {
        nodes.extend(plan.no_results.iter().cloned());
        return Ok(nodes);
    }
    let faces = faces(card);
    let (stroke_color, stroke_w, join) = stroke(card)?;
    let (radius, inner) = (plan.r, plan.r * pie_.hole);
    if let (Some(center), true) = (&plan.center, inner > 0.0) {
        if center.value_w > 2.0 * inner {
            return Err(Refused::WideTotal.refusal());
        }
    }
    let slice = &style.marks.slice;
    let thetas: Vec<f64> = pie_.slices.iter().map(|s| s.value).collect();
    let slices = pie(&thetas, 0.0, std::f64::consts::TAU, slice.gap);
    let mut wedges = Vec::with_capacity(slices.len());
    for wedge in &slices {
        let arc = Arc {
            inner_radius: inner,
            outer_radius: radius,
            corner_radius: slice.corner_radius,
            pad_radius: None,
            start_angle: wedge.start_angle,
            end_angle: wedge.end_angle,
            pad_angle: wedge.pad_angle,
        };
        let bound = &pie_.slices[wedge.index];
        let attrs = Attrs::new()
            .set("fill", bound.fill.as_str())
            .set("stroke", stroke_color.as_str())
            .num("stroke-width", stroke_w)
            .set("stroke-linejoin", join.as_str())
            .extend(aria_attrs(Some(&bound.aria), ItemRole::ArcMark).map_err(internal)?);
        let role = Role::Mark { layer: 0, series: None, datum: Some(wedge.index) };
        let path = Node::path(role, scene_path(&arc.path()), attrs);
        wedges.push(mark_group("arc", "layer_0_marks", vec![linked(bound.href.as_ref(), pie_.link_open, path)]).map_err(internal)?);
    }
    let knocked = knock_out(wedges, &pie_.canvas, "ko0", &safe_scope(card.chart_id), (-radius, -radius, 2.0 * radius, 2.0 * radius));
    nodes.push(Node::group(Role::Chrome, Attrs::new(), knocked.marks).translate(plan.cx, plan.cy));

    if let Some(center) = &pie_.center {
        // `row_number === 1`: the total's texts take the first row's link.
        let href = pie_.slices.first().and_then(|slice| slice.href.as_ref());
        let value_y = plan.cy + baseline_shift("bottom", faces.value.size);
        let value = Node::text(Role::Label, (plan.cx, value_y), faces.value.attrs("middle"), center.value.as_str());
        nodes.push(hidden_mark_group("text", "layer_1_marks", vec![linked(href, pie_.link_open, value)]));
        let caption_y = plan.cy + baseline_shift("top", faces.caption.size);
        let caption = Node::text(Role::Label, (plan.cx, caption_y), faces.caption.attrs("middle"), center.caption.as_str());
        nodes.push(hidden_mark_group("text", "layer_2_marks", vec![linked(href, pie_.link_open, caption)]));
    }

    let labels = &slice.labels;
    for placed in &plan.labels {
        let bound = &pie_.slices[placed.slice];
        let Some(label) = &bound.label else { return Err(diag::err_internal("a placed label has a slice label")) };
        let (top, right) = (placed.mid.cos() >= 0.0, placed.mid.sin() >= 0.0);
        let x = plan.cx + placed.mid.sin() * (radius + labels.offset);
        let lines = label.lines.len() as f64;
        let y = plan.cy - placed.mid.cos() * (radius + labels.offset) + if top { -(lines * labels.line_height) } else { 0.0 };
        let tspans = label
            .lines
            .iter()
            .enumerate()
            .map(|(i, line)| Run::Tspan {
                x: Some(x),
                y: None,
                dx: None,
                dy: Some(if i == 0 { 0.0 } else { labels.line_height }),
                attrs: Attrs::new(),
                runs: vec![Run::Chars(Chars::new(line.as_str(), Escape::Attr))],
            })
            .collect();
        let face = PieFace { color: label.ink.clone(), ..faces.label.clone() };
        let attrs = face.attrs(if right { "start" } else { "end" }).extend(aria_attrs(Some(&bound.aria), ItemRole::TextMark).map_err(internal)?);
        let at = (x, y + baseline_shift("top", faces.label.size));
        let text = Node::new(Role::Label, attrs, Shape::Text { at, bounds: None, runs: tspans });
        nodes.push(mark_group("text", "layer_3_marks", vec![linked(bound.href.as_ref(), pie_.link_open, text)]).map_err(internal)?);
    }
    Ok(nodes)
}
