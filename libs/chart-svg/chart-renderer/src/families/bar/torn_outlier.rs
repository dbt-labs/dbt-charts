//! Torn outlier bars (`style.outliers.variant: torn_with_cap`): the value axis ends at the
//! resolved `torn_break`, every segment that crosses it is torn there, and each
//! segment end above it floats as a labeled cap in a band beyond the plot. One
//! law for both orientations: geometry is kept in (band-axis, value-axis)
//! coordinates and `torn_path` transposes it for a horizontal bar. Every wave
//! comes from `crate::torn`.

use std::collections::HashMap;
use std::hash::Hash;

use d3rs::scale::Continuous;
use measure::FontBook;
use scene::{Attrs, Node, Role, Shape};
use serde::Deserialize;

use super::BarResolved;
use super::labels::{LabelLayer, label_font, label_text};
use crate::torn::{Cubic, torn_fill, torn_slice};

/// The gap between a cap or bar and its label.
const CAP_GAP: f64 = 4.0;

/// The shortest a cap gets, in label lines: long enough to read as a piece of its bar.
const MIN_CAP_LINES: f64 = 2.0;

/// How far the lowest cap floats past the break, in label lines: far enough to read as
/// lifted off its bar.
const LIFT_LINES: f64 = 2.0;

/// The space between caps that clear one another, in label lines.
const SPACE_LINES: f64 = 1.0;

/// How a torn bar is drawn (`style.outliers.variant`).
#[derive(Deserialize, PartialEq, Clone, Copy)]
#[serde(rename_all = "snake_case")]
pub(super) enum Variant {
    Torn,
    TornWithCap,
}

/// What hover redraws at true length (`style.outliers.reveal`).
#[derive(Deserialize, PartialEq, Clone, Copy)]
#[serde(rename_all = "snake_case")]
pub(super) enum Reveal {
    Bar,
    Chart,
    None,
}

impl Reveal {
    /// The `data-dbt-*` value the hover runtime reads, or `None` when hover draws nothing.
    pub(super) fn published(self) -> Option<&'static str> {
        match self {
            Reveal::Bar => Some("bar"),
            Reveal::Chart => Some("chart"),
            Reveal::None => None,
        }
    }
}

/// The cap's size (`style.outliers.cap`). The tear itself is torn paper sized to the bars
/// (`torn::ragged`), so the payload's tooth settings are not read.
#[derive(Deserialize)]
pub(super) struct CapSize {
    /// The fraction of the plot's value-axis length the cap band takes beyond the tear.
    band: f64,
    /// A cap's length along the value axis, as a multiple of the bar's thickness.
    length: f64,
}

/// The resolved `style.outliers`.
#[derive(Deserialize)]
pub(super) struct Style {
    pub(super) variant: Variant,
    pub(super) reveal: Reveal,
    cap: CapSize,
}

/// Where a segment lies against the break.
#[derive(PartialEq, Debug)]
enum Part {
    Below,
    Crossing,
    Above,
}

pub(super) struct Torn {
    pub(super) break_value: f64,
    horizontal: bool,
    band: f64,
    cap: f64,
    /// Value labels show on the bars below the break.
    labeled: bool,
    /// The band's length beyond the plot, decided once by `plan`.
    band_px: f64,
}

/// One cap in value order: its column, its value, and whether it starts a segment.
type Rung<K> = (K, f64, bool);

/// The caps in value order, lowest first; at a segment boundary the lower segment's end
/// cap comes before the upper one's start cap.
struct Ladder<K> {
    rungs: Vec<Rung<K>>,
    counts: HashMap<K, usize>,
    /// The space between caps that clear one another.
    space: f64,
}

impl<K: Eq + Hash + Clone> Ladder<K> {
    fn new(mut rungs: Vec<Rung<K>>, space: f64) -> Self {
        rungs.sort_by(|a, b| a.1.total_cmp(&b.1).then(a.2.cmp(&b.2)));
        let mut counts = HashMap::new();
        for rung in &rungs {
            *counts.entry(rung.0.clone()).or_default() += 1;
        }
        Self { rungs, counts, space }
    }

    /// How far past cap `j`'s far edge cap `i` (later in value order) must start: a column's
    /// caps part, except two meeting at a segment boundary, which part by the stack's own
    /// seam, the stroke they carry; a cap of a higher value in another column clears every
    /// lower one; caps of one value in different columns sit level.
    fn parting(&self, j: usize, i: usize) -> Option<f64> {
        let (a, b) = (&self.rungs[j], &self.rungs[i]);
        if a.0 == b.0 {
            Some(if !a.2 && b.2 && a.1 == b.1 { 0.0 } else { self.space })
        } else {
            (a.1 < b.1).then_some(self.space)
        }
    }

    fn lone(&self, i: usize) -> bool {
        self.counts[&self.rungs[i].0] == 1
    }

    /// The longest run of caps that must clear one another.
    fn longest(&self) -> f64 {
        let mut run = vec![1_usize; self.rungs.len()];
        for i in 0..run.len() {
            for j in 0..i {
                if self.parting(j, i).is_some() {
                    run[i] = run[i].max(run[j] + 1);
                }
            }
        }
        run.into_iter().max().unwrap_or(0) as f64
    }

    /// What a vertical band keeps above its top cap: a lone cap's label sits past it, a stacked
    /// column's top label centers on its cap's far edge.
    fn above(&self, horizontal: bool, font_size: f64) -> f64 {
        if horizontal || self.rungs.is_empty() {
            0.0
        } else if (0..self.rungs.len()).any(|i| self.lone(i)) {
            CAP_GAP + font_size
        } else {
            font_size / 2.0
        }
    }
}

/// One segment's cap, before the band places it: the end of a segment above
/// the break, or the start of a segment lying wholly above it.
struct Cap {
    role: Role,
    along: (f64, f64),
    anchor: f64,
    start: bool,
    fill: String,
    stroke: String,
    stroke_w: f64,
    opacity: Option<f64>,
    aria: Option<String>,
}

/// The tear across the plot, the caps, and each column's band extent and the far end
/// of its stack, keyed by the column's start along the band axis.
pub(super) struct Caps {
    tear: Vec<Cubic>,
    caps: Vec<Cap>,
    ends: HashMap<u64, Column>,
}

/// A column's band extent, the far end of its stack, and the hover identity of the segment there.
struct Column {
    along: (f64, f64),
    end: f64,
    aria: Option<String>,
}

impl Caps {
    /// The caps of a plot torn along `tear`, `Torn::tear_line`'s cubics.
    pub(super) fn on(tear: Vec<Cubic>) -> Self {
        Self { tear, caps: Vec::new(), ends: HashMap::new() }
    }
}

/// What one painted segment hands `Torn::segment`.
pub(super) struct Seg<'a> {
    /// The segment's `[start, end]` in the measure's units.
    pub(super) span: (f64, f64),
    /// The segment's extent along the band axis.
    pub(super) along: (f64, f64),
    /// `(fill, stroke, stroke width, fill opacity)`.
    pub(super) paint: (&'a str, &'a str, f64, Option<f64>),
    pub(super) role: Role,
    /// The segment's `aria-label`, which its caps and their labels repeat so hover recedes them with it.
    pub(super) aria: Option<&'a str>,
}

impl Torn {
    /// `None` unless the chart chose the cap variant and resolve found an outlier to break at.
    /// Resolve refuses every shape the cap cannot draw, so a `torn_break` here is drawable.
    /// Ribbons join each bar's full extent, which a tear cuts off the plot, so they refuse.
    pub(super) fn of(r: &BarResolved, horizontal: bool) -> Result<Option<Self>, String> {
        let (Some(style), Some(break_value)) = (r.style.outliers.as_ref().filter(|s| s.variant == Variant::TornWithCap), r.torn_break) else {
            return Ok(None);
        };
        if r.style.mark.ribbons.visible() {
            return Err(diagnostic::refuse!("bar: chart {:?} sets style.outliers.variant torn_with_cap with ribbons, which is not supported", r.id).into());
        }
        Ok(Some(Self {
            break_value,
            horizontal,
            band: style.cap.band,
            cap: style.cap.length,
            labeled: false,
            band_px: 0.0,
        }))
    }

    /// Pixels outward along the value axis: down the screen is positive, so up is `-1`.
    fn outward(&self) -> f64 {
        if self.horizontal { 1.0 } else { -1.0 }
    }

    /// The value domain ends exactly at the break, never niced past it; its low end is zero
    /// or, when a bar hangs below zero, the resolved floor.
    pub(super) fn pin_domain(&self, lo: &mut f64, hi: &mut f64, ticks: &mut Vec<f64>) {
        (*lo, *hi) = (lo.min(0.0), self.break_value);
        ticks.retain(|&tick| tick <= self.break_value * (1.0 + 1e-9));
    }

    fn part(&self, (start, end): (f64, f64)) -> Part {
        if end <= self.break_value {
            Part::Below
        } else if start >= self.break_value {
            Part::Above
        } else {
            Part::Crossing
        }
    }

    /// The torn edge across the plot at the break that every torn bar and its caps take their
    /// stretch of, so a stub and its cap are the two halves of one tear. Torn paper sized to the
    /// bars' `thickness`, seeded by the break. It is never drawn itself.
    pub(super) fn tear(&self, linear: &Continuous, plot_along: f64, plot_depth: f64, thickness: f64) -> Vec<Cubic> {
        let edge = linear.scale(self.break_value);
        crate::torn::ragged_cubics((0.0, plot_along), edge, crate::torn::torn_inward(edge, 0.0, plot_depth), thickness, self.break_value.to_bits())
    }

    /// Category gridlines at `boundaries` (band-axis pixels) from the plot's zero edge up to the break.
    pub(super) fn category_grid(&self, linear: &Continuous, boundaries: &[f64], zero_px: f64, attrs: &Attrs) -> Vec<Node> {
        const CRISP: f64 = 0.5;
        let tear = linear.scale(self.break_value);
        boundaries
            .iter()
            .map(|&along| {
                let (from, to) = if self.horizontal {
                    ((tear, along + CRISP), (zero_px, along + CRISP))
                } else {
                    ((along + CRISP, tear), (along + CRISP, zero_px + CRISP))
                };
                crate::dash::line(Role::Grid, from, to, attrs.clone())
            })
            .collect()
    }

    /// The boundaries between bands: the plot's edges and the midpoint of each gap.
    pub(super) fn band_boundaries(lefts: &[f64], bandwidth: f64, extent: f64) -> Vec<f64> {
        let mut lefts = lefts.to_vec();
        lefts.sort_by(f64::total_cmp);
        let mut boundaries = vec![0.0];
        boundaries.extend(lefts.windows(2).map(|pair| (pair[0] + bandwidth + pair[1]) / 2.0));
        boundaries.push(extent);
        boundaries
    }

    /// The caps a segment `[start, end]` owes: its end above the break, and its start too
    /// when it lies wholly above it.
    fn cap_ends(&self, (start, end): (f64, f64)) -> Vec<(f64, bool)> {
        [(end, false), (start, true)].into_iter().filter(|&(at, _)| at > self.break_value && end > self.break_value).collect()
    }

    /// Sizes the band once, before the plot is fit, from `pane_len`, the chart's length along
    /// the value axis: its share of the plot that fills the rest of the pane.
    pub(super) fn size_band(&mut self, pane_len: f64) {
        self.band_px = self.band * pane_len / (1.0 + self.band);
    }

    /// One painted segment: untouched below the break, torn at it across it, and
    /// not drawn above it; the caps it owes join `caps`.
    pub(super) fn segment(&self, caps: &mut Caps, seg: Seg, linear: &Continuous, mut nodes: Vec<Node>) -> Vec<Node> {
        let (start, end) = seg.span;
        let (fill, stroke, stroke_w, opacity) = seg.paint;
        let aria = seg.aria.map(str::to_string);
        let column = caps.ends.entry(seg.along.0.to_bits()).or_insert(Column { along: seg.along, end: 0.0, aria: None });
        if end >= column.end {
            column.end = end;
            column.aria = aria.clone();
        }
        let mut cap = |anchor: f64, start: bool| {
            caps.caps.push(Cap { role: seg.role.clone(), along: seg.along, anchor, start, fill: fill.into(), stroke: stroke.into(), stroke_w, opacity, aria: aria.clone() });
        };
        for (anchor, is_start) in self.cap_ends(seg.span) {
            cap(anchor, is_start);
        }
        match self.part(seg.span) {
            Part::Below => nodes,
            Part::Above => Vec::new(),
            Part::Crossing => {
                // The bar's stretch of the tear, moved out until its highest point is on the break.
                let cubics = torn_slice(&caps.tear, seg.along.0, seg.along.1, self.outward() * slice_top(&caps.tear, seg.along, linear.scale(self.break_value)));
                retrace(&mut nodes, &torn_fill(&cubics, linear.scale(start), self.horizontal));
                nodes
            }
        }
    }

    /// An end above the break prints on its cap, so a plain label anchored there prints
    /// nothing, and its empty mark sits at the break, where the bar now ends.
    pub(super) fn mute(&mut self, layers: &mut [LabelLayer]) {
        for layer in layers.iter_mut() {
            for (site, width) in layer.sites.iter_mut().zip(&mut layer.widths) {
                if site.at > self.break_value {
                    site.at = self.break_value;
                    site.blank = true;
                    *width = 0.0;
                }
            }
        }
        self.labeled = layers.iter().any(|layer| layer.sites.iter().any(|site| !site.blank));
    }

    /// The band's length beyond the plot.
    pub(super) fn band_len(&self) -> f64 {
        self.band_px
    }

    /// The room a horizontal chart's labels take past the last cap, `ends` being every
    /// segment end; a vertical chart's labels sit beside their caps.
    pub(super) fn label_room(&self, ends: impl Iterator<Item = f64>, r: &BarResolved, board_style: Option<&crate::BoardStyle>, fonts: &FontBook) -> Result<f64, String> {
        if !self.horizontal {
            return Ok(0.0);
        }
        let ml = r.style.mark.labels.as_ref().ok_or_else(no_labels)?;
        let font = label_font(ml, board_style)?;
        let mut widest = 0.0_f64;
        for end in ends.filter(|&end| end > self.break_value) {
            widest = widest.max(fonts.measure(&label_text(ml, r.style.label_is_house, end, None)?, &font.family, font.size)?);
        }
        Ok(CAP_GAP + widest)
    }

    /// Every cap and its label, and the tallest untorn bar's label. Caps of different values
    /// never overlap along the value axis: a higher value always sits wholly further out.
    pub(super) fn caps(&self, caps: Caps, linear: &Continuous, r: &BarResolved, board_style: Option<&crate::BoardStyle>, fonts: &FontBook) -> Result<Vec<Node>, String> {
        let Caps { tear, mut caps, ends } = caps;
        let ml = r.style.mark.labels.as_ref().ok_or_else(no_labels)?;
        let font = label_font(ml, board_style)?;
        let ink = font.color.as_deref().unwrap_or(&font.ink);
        // A label names its bar's datum inside a value-label group, so hover recedes it with its bar.
        let text_node = |at: (f64, f64), anchor: &str, baseline: &str, fill: &str, text: String, aria: Option<&str>| -> Result<Node, String> {
            let attrs = Attrs::new()
                .set("text-anchor", anchor)
                .vega("font-family", font.family.as_str())
                .num("font-size", font.size)
                .set("font-weight", font.weight.as_str())
                .set("fill", fill)
                .extend(::layout::contract::aria_attrs(aria, ::layout::contract::ItemRole::TextMark)?);
            let node = Node::text(Role::Label, (at.0, at.1 + measure::baseline_shift(baseline, font.size)), attrs, text);
            match aria {
                Some(_) => ::layout::contract::value_label_group("layer_0_marks", vec![node]),
                None => Ok(node),
            }
        };
        let s = self.outward();
        let break_px = linear.scale(self.break_value);
        // How far a stretch of the tear sits below the break: its highest point and its lowest notch.
        let below = |cubics: &[Cubic]| cubics.iter().flatten().map(|p| (p.1 - break_px).abs()).fold((f64::MAX, 0.0_f64), |(top, bite), d| (top.min(d), bite.max(d)));
        let mut nodes = Vec::new();
        // The tallest bar the tear leaves whole names its value, short of the torn stub's
        // lowest notch so the stub still reads taller: past its end where that fits, else inside it.
        let tallest = ends.values().filter(|column| column.end <= self.break_value).max_by(|a, b| a.end.total_cmp(&b.end));
        if let (false, Some(&Column { along, end, ref aria })) = (self.labeled, tallest) {
            let text = label_text(ml, r.style.label_is_house, end, None)?;
            let reach = if self.horizontal { fonts.measure(&text, &font.family, font.size)? } else { font.size };
            let (end_px, mid) = (linear.scale(end), (along.0 + along.1) / 2.0);
            let (top, bite) = below(&torn_slice(&tear, along.0, along.1, 0.0));
            let notch = break_px - s * (bite - top);
            let fits = s * (notch - end_px) >= CAP_GAP + reach;
            let (dir, fill) = if fits { (1.0, ink) } else { (-1.0, r.canvas.as_str()) };
            let at = end_px + dir * s * CAP_GAP;
            nodes.push(if self.horizontal {
                text_node((at, mid), if fits { "start" } else { "end" }, "middle", fill, text, aria.as_deref())?
            } else {
                text_node((mid, at), "middle", if fits { "bottom" } else { "top" }, fill, text, aria.as_deref())?
            });
        }
        if caps.is_empty() {
            return Ok(nodes);
        }
        caps.sort_by(|a, b| a.anchor.total_cmp(&b.anchor).then(a.start.cmp(&b.start)).then(a.along.0.total_cmp(&b.along.0)));
        let mut ladder = Ladder::new(caps.iter().map(|cap| (cap.along.0.to_bits(), cap.anchor, cap.start)).collect(), SPACE_LINES * font.size);
        let above = ladder.above(self.horizontal, font.size);
        let band_len = self.band_px - above;
        let longest = ladder.longest();
        let thickest = caps.iter().map(|cap| cap.along.1 - cap.along.0).fold(0.0, f64::max);
        // A band shorter than the ladder wants squeezes its lift and spacing with its caps.
        let wanted = LIFT_LINES * font.size + longest * MIN_CAP_LINES * font.size + (longest - 1.0) * ladder.space;
        let squeeze = (band_len / wanted).min(1.0);
        let lift = LIFT_LINES * font.size * squeeze;
        ladder.space *= squeeze;
        // `cap` bar thicknesses, but never under `MIN_CAP_LINES` label lines, however thin the bar.
        let len = (self.cap * thickest).max(MIN_CAP_LINES * font.size * squeeze).min((band_len - lift - (longest - 1.0) * ladder.space) / longest);
        if len < 1.0 {
            return Err(diagnostic::refuse!("bar: torn_outlier caps that do not fit the cap band are not supported"));
        }
        // Each cap's near edge (its distance past the tear) maps its value linearly onto the
        // band, then moves up just enough to clear the caps it must, and back down under the
        // band's end.
        let (lo, top) = (lift, band_len - len);
        let peak = caps.iter().map(|cap| cap.anchor).fold(f64::MIN, f64::max);
        let mut near: Vec<f64> = caps.iter().map(|cap| lo + (cap.anchor - self.break_value) / (peak - self.break_value) * (top - lo)).collect();
        for i in 0..caps.len() {
            for j in 0..i {
                if let Some(gap) = ladder.parting(j, i) {
                    near[i] = near[i].max(near[j] + len + gap);
                }
            }
        }
        for i in (0..caps.len()).rev() {
            near[i] = near[i].min(top);
            for k in i + 1..caps.len() {
                if let Some(gap) = ladder.parting(i, k) {
                    near[i] = near[i].min(near[k] - len - gap);
                }
            }
        }
        for (i, (cap, near)) in caps.iter().zip(near).enumerate() {
            let (near_px, far_px) = (break_px + s * near, break_px + s * (near + len));
            // A cap's torn edge is its bar's stretch of the tear: an end cap's faces the tear, its
            // lowest notch `near` past it; a start cap's faces away, as a torn bar's top does.
            let (top, depth) = below(&torn_slice(&tear, cap.along.0, cap.along.1, 0.0));
            let (by, flat) = if cap.start { (s * (near + len + top), near_px) } else { (s * (near + depth), far_px) };
            let cubics = torn_slice(&tear, cap.along.0, cap.along.1, by);
            let mut attrs = Attrs::new().set("class", "torn-cap").set("fill", cap.fill.as_str()).num_some("fill-opacity", cap.opacity);
            if cap.stroke_w > 0.0 && cap.stroke != "none" && !cap.stroke.is_empty() {
                attrs = attrs.set("stroke", cap.stroke.as_str()).num("stroke-width", cap.stroke_w);
            }
            let aria = cap.aria.as_deref();
            let path = Node::path(cap.role.clone(), torn_fill(&cubics, flat, self.horizontal), attrs.extend(::layout::contract::aria_attrs(aria, ::layout::contract::ItemRole::Bar)?));
            nodes.push(match aria {
                Some(_) => ::layout::contract::mark_group("rect", "layer_0_marks", vec![path])?,
                None => path,
            });
            if cap.start {
                continue;
            }
            let text = label_text(ml, r.style.label_is_house, cap.anchor, None)?;
            let mid_along = (cap.along.0 + cap.along.1) / 2.0;
            // A lone cap's label sits past it; a stacked column's sit beside their caps at the
            // segment's end, which is the boundary with the next segment's cap.
            nodes.push(match (ladder.lone(i), self.horizontal) {
                (true, true) => text_node((far_px + CAP_GAP, mid_along), "start", "middle", ink, text, aria)?,
                (true, false) => text_node((mid_along, far_px - CAP_GAP), "middle", "bottom", ink, text, aria)?,
                (false, true) => text_node((far_px, cap.along.1 + CAP_GAP), "middle", "top", ink, text, aria)?,
                (false, false) => text_node((cap.along.1 + CAP_GAP, far_px), "start", "middle", ink, text, aria)?,
            });
        }
        Ok(nodes)
    }
}

/// How far below the break the highest point of the tear's stretch across `along` sits: the
/// stretch moves out by this much, so every torn bar reaches the break.
fn slice_top(tear: &[Cubic], along: (f64, f64), break_px: f64) -> f64 {
    torn_slice(tear, along.0, along.1, 0.0).iter().flatten().map(|p| (p.1 - break_px).abs()).fold(f64::MAX, f64::min)
}

fn no_labels() -> String {
    "torn chart missing style.mark.labels".to_string()
}

/// Swaps every rect or path among `nodes` (a mark's group, link and clip included) for `path`.
pub(super) fn retrace(nodes: &mut [Node], path: &scene::PathData) {
    for node in nodes {
        match &mut node.shape {
            Shape::Rect { .. } | Shape::Path(_) => node.shape = Shape::Path(path.clone()),
            Shape::Group(children) | Shape::Link(children) | Shape::ClipPath(children) => retrace(children, path),
            _ => {}
        }
    }
}
