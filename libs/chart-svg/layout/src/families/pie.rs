//! A pie or donut in its direct mode: every wedge with its slice label, and a donut's center
//! total. `plan` solves the wheel the box lets it draw (`d3rs::radial_fit`) and refuses what the
//! size stage would not draw directly; `draw` places the nodes under the chart's root `<svg>`.
//!
//! The rules are `chart-renderer/src/families/pie.rs` (`pie_layout_classified`, `candidate`) and
//! `pie/layout.rs` (`solve_wheel`, `wheel`), cited at each law. A pie the size stage would draw as
//! a table, or any other shape this slice does not draw, is refused by name.

use bind::model::Pie;
use board_model::style::board::EmojiMode;
use board_model::style::pie::PieChartStyle;
use d3rs::arc::Arc;
use d3rs::pie::pie;
use d3rs::radial_fit::{self, Callout, Ink, SolveError};
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::{Font, FontBook};
use measure::{baseline_shift, stand_in_family};
use model_core::Engine;
use scene::{Attrs, Chars, Escape, Extent, Node, Role, Run, Shape};

use super::failed::Weight;
use crate::contract::{aria_attrs, hidden_mark_group, mark_group, ItemRole};
use crate::knockout::knock_out;
use crate::scene_path::scene_path;
use crate::text_ink;

/// `WHEEL_DOMINANCE_MIN_RATIO`: below this fraction of the bare wheel's radius, a labeled mode
/// starves the wheel badly enough to prefer the table.
const WHEEL_DOMINANCE_MIN_RATIO: f64 = 0.6;
/// `HOLE_TOTAL_MARGIN_PX`: the gap the center total keeps from the inner ring.
const HOLE_TOTAL_MARGIN_PX: f64 = 4.0;
/// `OUTER_FRACTION` in `core/constants/pie.py`: a wheel with nothing to make room for leaves its
/// box a tenth of whitespace.
const OUTER_FRACTION: f64 = 0.9;

/// A pie this slice does not draw.
#[derive(Clone, Copy)]
enum Refused {
    Stroke,
    Table,
    NoRoom,
    WideTotal,
    Weight,
}

impl Refused {
    fn refusal(self) -> Diagnostic {
        let what = match self {
            Refused::Stroke => "whose slice stroke is not a set color, width and join",
            Refused::Table => "the size stage draws as a table",
            Refused::NoRoom => "drawn in a box its labels leave no room in",
            Refused::WideTotal => "whose total is wider than its hole",
            Refused::Weight => "whose font weight names no axis value",
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
    pub chart_id: &'a str,
    pub emoji: &'a EmojiMode,
    /// The board scope's background, which the slice stroke's `theme.background` names.
    pub ground: &'a str,
}

/// A solved wheel: where it sits in its box and its radius.
#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    cx: f64,
    cy: f64,
    r: f64,
    labels: Vec<Placed>,
    center: Option<CenterInk>,
}

#[derive(Debug, Clone, PartialEq)]
struct Placed {
    slice: usize,
    mid: f64,
    max_w: f64,
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
        // `theme.background` is the stroke's self-reference to the board's background (`_resolve_self_tokens`).
        (Engine::Value(color), Engine::Value(width), Some(Engine::Value(join))) => {
            let color = if color.as_str() == "theme.background" { card.ground } else { color.as_str() };
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

/// One solve of the wheel in `(w, h)` inside `pad` (left, right, top, bottom): `labeled` reserves
/// room for the slice labels and the center total, otherwise it is the bare wheel
/// (`solve_wheel` with `ALL_PARTS` or `BARE`, no title and no legend). The radius, and the
/// smallest radius whose hole holds the total.
fn solve(card: &PieCard, book: &FontBook, (w, h): (f64, f64), pad: (f64, f64, f64, f64), labeled: bool) -> Result<Solved, Unsolved> {
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
    if labeled {
        for (idx, slice) in pie_.slices.iter().enumerate() {
            let Some(label) = &slice.label else { continue };
            let mut max_w = 0.0_f64;
            for line in &label.lines {
                max_w = max_w.max(faces.label.width(book, line).map_err(Unsolved::Failed)?);
            }
            placed.push(Placed { slice: idx, mid: mids[idx], max_w });
        }
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

/// The wheel in `(w, h)` inside `pad` (left, right, top, bottom). `sized` is the box the size
/// stage decided the pie's mode in (`pie_box`), which the guard reads; the wheel is drawn in
/// `(w, h)`. A pie the size stage would not draw directly (labels that starve the wheel, a hole
/// that cannot hold the total, no room at all) is refused.
pub fn plan(card: &PieCard, book: &FontBook, sized: (f64, f64), (w, h): (f64, f64), pad: (f64, f64, f64, f64)) -> Result<Plan, Diagnostic> {
    let table = || Refused::Table.refusal();
    let solve = |size, labeled| match solve(card, book, size, pad, labeled) {
        Ok(solved) => Ok(Some(solved)),
        Err(Unsolved::NoRoom) => Ok(None),
        Err(Unsolved::Failed(e)) => Err(e),
    };
    let (Some(free), Some(ink)) = (solve(sized, false)?, solve(sized, true)?) else { return Err(table()) };
    if ink.wheel.r < WHEEL_DOMINANCE_MIN_RATIO * free.wheel.r || ink.min_radius.is_some_and(|min| ink.wheel.r < min) {
        return Err(table());
    }
    let Some(drawn) = solve((w, h), true)? else {
        return Err(Refused::NoRoom.refusal());
    };
    Ok(Plan { cx: drawn.wheel.cx, cy: drawn.wheel.cy, r: drawn.wheel.r, labels: drawn.placed, center: drawn.center })
}

/// The chart's root `<svg class="marks">`: the background, the wheel, the center total and the
/// slice labels, in `(w, h)`.
pub fn draw(card: &PieCard, plan: &Plan, (w, h): (f64, f64)) -> Result<Node, Diagnostic> {
    let internal = |e: String| diag::err_internal(e);
    let (pie_, style) = (card.pie, card.style);
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
        wedges.push(mark_group("arc", "layer_0_marks", vec![path]).map_err(internal)?);
    }
    let knocked = knock_out(wedges, &pie_.canvas, "ko0", &safe_scope(card.chart_id), (-radius, -radius, 2.0 * radius, 2.0 * radius));
    let mut nodes = vec![Node::rect(Role::Chrome, (0.0, 0.0, w, h), Attrs::new().set("fill", pie_.background.as_str()))];
    nodes.push(Node::group(Role::Chrome, Attrs::new(), knocked.marks).translate(plan.cx, plan.cy));

    if let Some(center) = &pie_.center {
        let value_y = plan.cy + baseline_shift("bottom", faces.value.size);
        let value = Node::text(Role::Label, (plan.cx, value_y), faces.value.attrs("middle"), center.value.as_str());
        nodes.push(hidden_mark_group("text", "layer_1_marks", vec![value]));
        let caption_y = plan.cy + baseline_shift("top", faces.caption.size);
        let caption = Node::text(Role::Label, (plan.cx, caption_y), faces.caption.attrs("middle"), center.caption.as_str());
        nodes.push(hidden_mark_group("text", "layer_2_marks", vec![caption]));
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
        nodes.push(mark_group("text", "layer_3_marks", vec![text]).map_err(internal)?);
    }
    Ok(Node::new(Role::Chrome, Attrs::new().set("class", "marks"), Shape::Svg { extent: Extent { width: w, height: h }, children: nodes }))
}

/// A chart's id for a chart placed once (`contract.rs::scoped_id`): every character outside a
/// FuncIRI-safe set replaced, and a short digest when that was lossy (`safe_svg_id`).
fn safe_scope(chart_id: &str) -> String {
    use md5::{Digest, Md5};
    if chart_id.is_empty() {
        return "chart.anon".to_string();
    }
    let safe: String = chart_id.chars().map(|c| if c.is_ascii_alphanumeric() || c == '_' || c == '-' { c } else { '_' }).collect();
    if safe == chart_id {
        return safe;
    }
    let digest = Md5::digest(chart_id.as_bytes());
    format!("{safe}-{}", digest.iter().take(4).map(|b| format!("{b:02x}")).collect::<String>())
}
