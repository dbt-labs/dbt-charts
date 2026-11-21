//! Off-the-chart bars: a vertical bar past an authored value-axis maximum runs to its card's top
//! edge and ends in a torn edge, so the outlier never shrinks the plot. Its value reads upward
//! inside the bar from just inside the plot's top, ending in an arrow, so the header above stays
//! clear for the title: the tear and the arrow say the bar goes on.

use measure::FontBook;
use scene::{Attrs, Node, Role, Shape, Transform};

use super::BarResolved;
use super::labels::{LabelLayer, label_font, label_text};
use super::torn_outlier::{Reveal, Variant, retrace};

/// How far inside the plot's top a dropped label's arrow sits.
const PLOT_GAP: f64 = 6.0;
/// How far under its tear an upright label's arrow sits.
const TEAR_GAP: f64 = 6.0;
const MIN_CONTRAST: f64 = 4.5;
const BOLD: f32 = 600.0;

struct Outlier {
    x: (f64, f64),
    value: f64,
    ink: String,
    /// How deep its tear bites into the card.
    depth: f64,
}

/// Warns when a bar tore under `variant: torn` on a chart shape that cannot draw an off-chart
/// bar: it draws plain, and never as the cap.
pub(super) fn warn_if_undrawable(r: &BarResolved) {
    if r.torn_break.is_some() && r.style.outliers.as_ref().is_some_and(|o| o.variant == Variant::Torn) {
        diagnostic::warn(diagnostic::diag::warn_outliers_torn_unsupported(r.id.as_str()));
    }
}

pub(super) struct OffChart {
    reveal: Reveal,
    max: f64,
    /// The card's top edge, in plot coordinates.
    top: f64,
    canvas: String,
    bars: Vec<Outlier>,
    /// The boxes of the lifted header content, halo included, in plot coordinates.
    content: Vec<scene::Bounds>,
    /// Under `reveal: none`, the header content on its band, painted over the bars.
    front: Vec<Node>,
}

impl OffChart {
    pub(super) fn new(r: &BarResolved, max: f64, top: f64) -> Self {
        let reveal = r.style.outliers.as_ref().map_or(Reveal::Chart, |o| o.reveal);
        Self { reveal, max, top, canvas: r.canvas.clone(), bars: Vec::new(), content: Vec::new(), front: Vec::new() }
    }

    /// Blanks the value labels of the bars that run off the chart: this chart names them itself.
    pub(super) fn mute(max: f64, layers: &mut [LabelLayer]) {
        for layer in layers.iter_mut() {
            for (site, width) in layer.sites.iter_mut().zip(&mut layer.widths) {
                if site.value > max {
                    site.at = max;
                    site.blank = true;
                    site.off_chart = true;
                    *width = 0.0;
                }
            }
        }
    }

    /// `reach` is the value's position on the plot's scale, which the hover runtime draws the bar
    /// to, and `ceiling` the axis maximum's, the plot's top, which its drawing keeps clear of.
    pub(super) fn tear(&mut self, value: f64, x: (f64, f64), (baseline, reach, ceiling): (f64, f64, f64), ink: &str, mut nodes: Vec<Node>) -> Vec<Node> {
        if value > self.max {
            let edge = crate::torn::ragged(x, self.top, 1.0, x.1 - x.0, value.to_bits());
            let depth = edge.iter().map(|p| p.1 - self.top).fold(0.0, f64::max);
            let mut path = scene::PathData::new().move_to(x.0, baseline);
            for &(px, py) in &edge {
                path = path.line_to(px, py);
            }
            retrace(&mut nodes, &path.line_to(x.1, baseline).close());
            if let Some(reveal) = self.reveal.published() {
                stamp_hover(&mut nodes, &Hover { length: baseline - reach, plot: baseline - ceiling, reveal }, &self.canvas);
            }
            self.bars.push(Outlier { x, value, ink: ink.to_string(), depth });
        }
        nodes
    }

    /// Every outlier's value, upright inside its bar from just under its tear, its arrow at the
    /// top. A label the lifted header content would cover drops to just inside the plot's top
    /// instead; content sits in the same place lifted or not, so a label never moves on hover.
    pub(super) fn labels(&mut self, r: &BarResolved, board_style: Option<&crate::BoardStyle>, fonts: &FontBook) -> Result<Vec<Node>, String> {
        if self.bars.is_empty() {
            return Ok(Vec::new());
        }
        let ml = r.style.mark.labels.as_ref().ok_or("bar: a bar past the axis maximum needs style.mark.labels")?;
        let font = label_font(ml, board_style)?;
        let size = font.size;
        let normal_ink = match &font.color {
            Some(color) => color.clone(),
            None => dbt_charts_color::ensure_readable_ink(&font.ink, &r.canvas, crate::common::OUTSIDE_INK_CONTRAST)?,
        };
        let face = Face { family: &font.family, size, hover: self.reveal.published().is_some() };
        let mut nodes = Vec::new();
        for bar in &self.bars {
            let text = format!("{} \u{2192}", label_text(ml, r.style.label_is_house, bar.value, None)?);
            let length = fonts.measure_at_weight(&text, &font.family, size, Some(BOLD))?;
            let under_tear = self.top + bar.depth + TEAR_GAP;
            let covered = self.content.iter().any(|b| b.x1 < bar.x.1 && b.x2 > bar.x.0 && b.y1 < under_tear + length && b.y2 > under_tear);
            let end = if covered { PLOT_GAP } else { under_tear };
            let at = ((bar.x.0 + bar.x.1) / 2.0 + measure::baseline_shift("middle", size), end);
            // A bar narrower than the type keeps its label legible on the card's color instead.
            let narrow = bar.x.1 - bar.x.0 < size + 2.0;
            let ink = if narrow { normal_ink.clone() } else { dbt_charts_color::ensure_readable_ink(&r.canvas, &bar.ink, MIN_CONTRAST)? };
            if narrow {
                let halo = Shape::Rect { x: at.0 - size, y: end - 1.0, w: size + 3.0, h: length + 2.0, rx: 3.0 };
                nodes.push(Node::new(Role::Chrome, Attrs::new().set("fill", &r.canvas), halo));
            }
            nodes.push(face.text(at, "end", &text, BOLD, &ink).with_transform(Transform::Rotate { deg: -90.0, about: Some(at) }));
        }
        Ok(nodes)
    }
}

impl OffChart {
    /// Marks in `head` (card coordinates, the plot at `origin`) every header line of a class in
    /// `lines` and the legend above the plot as content the hover runtime lifts over the bars
    /// (`data-dbt-lifted`), when this chart has a torn bar, and puts one band of the card's color,
    /// `BAND_OPACITY` opaque and as wide as the text it backs, in front of the first. With a hover
    /// reveal all of it stays where `head` paints, behind the bars: the static picture is torn bars
    /// over the header, and only a hovered chart, drawing each bar at true length, lifts the header
    /// over them. With none, nothing lifts it, so it moves to `front` on its band and paints over
    /// the bars at rest, as on a screen nobody points at or on paper. The band is one shape, the
    /// strips behind each line merged, so where two meet the bars show through it once. All of it
    /// lifts, crossed or not: a true-length drawing can pass over any of it.
    pub(super) fn lift(&mut self, head: &mut Vec<Node>, origin: (f64, f64), lines: &[&str], fonts: &FontBook) -> Result<(), String> {
        if self.bars.is_empty() {
            return Ok(());
        }
        let hover = self.reveal.published().is_some();
        let mut strips: Vec<scene::Bounds> = Vec::new();
        let mut lifted: Vec<usize> = Vec::new();
        let mut first = None;
        for (i, node) in head.iter_mut().enumerate() {
            let class = node.attrs.get("class").unwrap_or_default();
            let strip = if class.split(' ').any(|c| c == "role-legend") {
                measured_bounds(node, fonts)?.filter(|b| b.y1 < origin.1).map(|b| {
                    let pad = BAND_PAD * (b.y2 - b.y1);
                    scene::Bounds { x1: b.x1 - pad, y1: b.y1 - pad, x2: b.x2 + pad, y2: b.y2 + pad }
                })
            } else if lines.iter().any(|line| class.split(' ').any(|c| c == *line)) {
                let size = node.attrs.get_num("font-size").ok_or("bar: a header line without a font-size")?;
                let pad = BAND_PAD * size;
                let b = measured_bounds(node, fonts)?.ok_or("bar: a header line without a font-family")?;
                Some(scene::Bounds { x1: b.x1 - pad, y1: b.y1 - pad, x2: b.x2 + pad, y2: b.y2 + DESCENT * size + pad })
            } else {
                None
            };
            if let Some(strip) = strip {
                strips.push(strip);
                lifted.push(i);
                first.get_or_insert(i);
                if hover {
                    node.attrs = std::mem::take(&mut node.attrs).set("data-dbt-lifted", "");
                }
            }
        }
        let Some(first) = first else {
            return Ok(());
        };
        strips.sort_by(|a, b| a.y1.total_cmp(&b.y1));
        let mut merged: Vec<scene::Bounds> = Vec::new();
        for strip in strips {
            match merged.last_mut() {
                Some(last) if strip.y1 <= last.y2 => *last = last.union(strip),
                _ => merged.push(strip),
            }
        }
        let mut band = scene::PathData::new();
        for b in &merged {
            band = band.move_to(b.x1, b.y1).line_to(b.x2, b.y1).line_to(b.x2, b.y2).line_to(b.x1, b.y2).close();
            self.content.push(scene::Bounds { x1: b.x1 - origin.0, y1: b.y1 - origin.1, x2: b.x2 - origin.0, y2: b.y2 - origin.1 });
        }
        let attrs = Attrs::new().set("fill", &self.canvas).num("fill-opacity", BAND_OPACITY);
        if hover {
            head.insert(first, Node::new(Role::Chrome, attrs.set("data-dbt-lifted", ""), Shape::Path(band)));
            return Ok(());
        }
        self.front.push(Node::new(Role::Chrome, attrs, Shape::Path(band)));
        for i in lifted.into_iter().rev() {
            self.front.insert(1, head.remove(i));
        }
        Ok(())
    }

    /// The header content `lift` moved over the bars, in card coordinates: empty unless
    /// `reveal: none` tore a bar.
    pub(super) fn front(&mut self) -> Vec<Node> {
        std::mem::take(&mut self.front)
    }
}

/// The lifted header band's opacity: the bars it covers show faintly through.
const BAND_OPACITY: f64 = 0.85;
/// The band's reach around a header line, as a share of its type size, or around a legend, of its box's height.
const BAND_PAD: f64 = 0.18;
/// How far a line's descenders reach below its baseline, as a share of its type size.
const DESCENT: f64 = 0.25;

/// A node's box in its parent's frame, its text measured with `fonts`: header text carries no
/// measured box of its own.
fn measured_bounds(node: &Node, fonts: &FontBook) -> Result<Option<scene::Bounds>, String> {
    let own = match &node.shape {
        Shape::Text { at, .. } => {
            let (Some(text), Some(family), Some(size)) = (node.text_content(), node.attrs.get("font-family"), node.attrs.get_num("font-size")) else {
                return Ok(None);
            };
            let width = fonts.measure(&text, family, size)?;
            let left = match node.attrs.get("text-anchor") {
                Some("middle") => at.0 - width / 2.0,
                Some("end") => at.0 - width,
                _ => at.0,
            };
            Some(scene::Bounds { x1: left, y1: at.1 - size, x2: left + width, y2: at.1 })
        }
        Shape::Group(children) | Shape::Link(children) => {
            let mut union: Option<scene::Bounds> = None;
            for child in children {
                if let Some(b) = measured_bounds(child, fonts)? {
                    let b = b.transformed(&child.affine());
                    union = Some(union.map_or(b, |u| u.union(b)));
                }
            }
            union
        }
        _ => node.bounds(),
    };
    Ok(own)
}

/// What the hover runtime needs to draw a bar at true length, in plot pixels.
struct Hover {
    length: f64,
    /// The plot's height, which a spiral keeps clear of.
    plot: f64,
    /// What hovering redraws at true length (`data-dbt-off-chart-reveal`): `bar` or `chart`.
    reveal: &'static str,
}

/// Stamps `hover` on the bar's filled shape (not a second, outline-only pass), with its edge:
/// the width of a canvas-colored outline the knockout cuts out of the bar, so the visible bar
/// sits half of it inside its shape.
fn stamp_hover(nodes: &mut [Node], hover: &Hover, canvas: &str) {
    for node in nodes {
        match &mut node.shape {
            Shape::Path(_) if node.attrs.get("fill") != Some("none") => {
                let haloed = node.attrs.get("stroke").is_some_and(|stroke| stroke.eq_ignore_ascii_case(canvas));
                // SVG's own default stroke width.
                let edge = if haloed { node.attrs.get_num("stroke-width").unwrap_or(1.0) } else { 0.0 };
                node.attrs = std::mem::take(&mut node.attrs)
                    .num("data-dbt-off-chart", hover.length)
                    .num("data-dbt-off-chart-plot", hover.plot)
                    .num("data-dbt-off-chart-edge", edge)
                    .set("data-dbt-off-chart-reveal", hover.reveal);
            }
            Shape::Group(children) | Shape::Link(children) | Shape::ClipPath(children) => stamp_hover(children, hover, canvas),
            _ => {}
        }
    }
}

struct Face<'a> {
    family: &'a str,
    size: f64,
    /// Publish the label for the hover runtime to copy over a true-length drawing.
    hover: bool,
}

impl Face<'_> {
    fn text(&self, at: (f64, f64), anchor: &str, text: &str, weight: f32, fill: &str) -> Node {
        let attrs = Attrs::new().set("text-anchor", anchor).vega("font-family", self.family).num("font-size", self.size).set("font-weight", format!("{weight}")).set("fill", fill);
        // The hover runtime copies an outlier's label over its true-length drawing.
        let attrs = if self.hover { attrs.set("data-dbt-off-chart-label", "") } else { attrs };
        Node::text(Role::Label, at, attrs, text)
    }
}
