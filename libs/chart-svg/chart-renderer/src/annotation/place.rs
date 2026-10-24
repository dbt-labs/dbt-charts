//! Anchor geometry to plot-relative pixel geometry, and text placement
//! against it. Everything here is computed in channel space `(a, b)` (x
//! channel, y channel) and swapped to screen space once.

use super::axis_map::{AxisMap, Site};
use super::model::{Align, Anchor, Annotation, Connector as ConnectorKind, Curve, Edge, Enclose, Kind, Position, Style, YAxis};
use super::text::{self, Block};
use super::{AnnotationCtx, Orientation, Plane};
use crate::diagnostic::Diagnostic;

/// Text-to-geometry gap, px.
pub(crate) const GAP: f64 = 4.0;
/// How far text keeps off tick labels beside the plot.
const AXIS_GAP: f64 = 8.0;

#[derive(Debug, Clone)]
pub(crate) struct Placed<'a> {
    pub index: usize,
    pub plane: Plane,
    /// The accessible name.
    pub label: String,
    pub style: &'a Style,
    /// `None` for a note.
    pub mark: Option<Mark>,
    /// A trend's interval area, painted under its line.
    pub interval: Option<Mark>,
    /// The circle or rect around a note's anchor.
    pub enclosure: Option<Mark>,
    pub text: Option<TextBox>,
    pub connector: Option<Connector>,
    /// The axis the collision pass slides the text along.
    pub slide: Slide,
    /// The padding of the rect enclosing the text, which moves with it.
    pub inset: Option<f64>,
    /// The note anchor its connector runs from.
    pub tether: Option<Tether>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Slide {
    X,
    Y,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct Tether {
    pub point: (f64, f64),
    /// How far off `point` the connector starts: an enclosing circle's radius, else the point marker's.
    pub reach: f64,
    pub arrow: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) enum Mark {
    Segment { a: (f64, f64), b: (f64, f64) },
    Rect { x: f64, y: f64, w: f64, h: f64 },
    Polyline(Vec<(f64, f64)>),
    Area { hi: Vec<(f64, f64)>, lo: Vec<(f64, f64)> },
    Circle { c: (f64, f64), r: f64 },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum TextAnchor {
    Start,
    Middle,
    End,
}

/// A placed text box in screen px: the box, its inner padding, the run's
/// ascent and which box edge the text is anchored to.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct TextBox {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
    pub pad: f64,
    pub ascent: f64,
    pub anchor: TextAnchor,
    pub lines: Vec<String>,
    pub line_h: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Connector {
    pub from: (f64, f64),
    /// The quadratic's control point under `curve: natural`.
    pub control: Option<(f64, f64)>,
    pub to: (f64, f64),
    /// The filled triangle at `from`, pointing at the anchor, for `arrow`.
    pub arrow: Option<[(f64, f64); 3]>,
}

/// What text is placed against, in screen px.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) enum Geom {
    HSeg { y: f64, x0: f64, x1: f64 },
    VSeg { x: f64, y0: f64, y1: f64 },
    Rect { x0: f64, y0: f64, x1: f64, y1: f64 },
    /// `reach` is the extra gap past the point (an enclosing circle's radius).
    Point { x: f64, y: f64, reach: f64 },
}

impl Geom {
    fn extent(self) -> (f64, f64, f64, f64) {
        match self {
            Geom::HSeg { y, x0, x1 } => (x0, y, x1, y),
            Geom::VSeg { x, y0, y1 } => (x, y0, x, y1),
            Geom::Rect { x0, y0, x1, y1 } => (x0, y0, x1, y1),
            Geom::Point { x, y, .. } => (x, y, x, y),
        }
    }
}

#[derive(Clone, Copy)]
enum Pin {
    Lo,
    Mid,
    Hi,
}

/// The box's leading coordinate along `[a0, a1]`. Along a side with length
/// the box sits within it; at a point, before (start), on (center) or after
/// (end) it.
fn along(a0: f64, a1: f64, size: f64, align: Align) -> (f64, Pin) {
    if a1 > a0 {
        match align {
            Align::Start => (a0, Pin::Lo),
            Align::Center => ((a0 + a1) / 2.0 - size / 2.0, Pin::Mid),
            Align::End => (a1 - size, Pin::Hi),
        }
    } else {
        match align {
            Align::Start => (a0 - size, Pin::Hi),
            Align::Center => (a0 - size / 2.0, Pin::Mid),
            Align::End => (a0, Pin::Lo),
        }
    }
}

fn anchor_of(pin: Pin) -> TextAnchor {
    match pin {
        Pin::Lo => TextAnchor::Start,
        Pin::Mid => TextAnchor::Middle,
        Pin::Hi => TextAnchor::End,
    }
}

/// The box origin and text-anchor for a `(w, h)` box against `g`, before
/// `dx`/`dy`.
pub(crate) fn place_box(g: Geom, (w, h): (f64, f64), pos: Position) -> (f64, f64, TextAnchor) {
    let (gx0, gy0, gx1, gy1) = g.extent();
    let reach = match g {
        Geom::Point { reach, .. } => reach,
        _ => 0.0,
    };
    let gap = GAP + reach;
    if !pos.overlay {
        return match pos.edge {
            Edge::Top | Edge::Bottom => {
                let (x, pin) = along(gx0, gx1, w, pos.align);
                let y = if pos.edge == Edge::Top { gy0 - gap - h } else { gy1 + gap };
                (x, y, anchor_of(pin))
            }
            Edge::Left => (gx0 - gap - w, along(gy0, gy1, h, pos.align).0, TextAnchor::End),
            Edge::Right => (gx1 + gap, along(gy0, gy1, h, pos.align).0, TextAnchor::Start),
        };
    }
    match g {
        Geom::Point { x, y, .. } => (x - w / 2.0, y - h / 2.0, TextAnchor::Middle),
        Geom::HSeg { y, x0, x1 } => {
            let (x, pin) = along(x0, x1, w, pos.align);
            (x, y - h / 2.0, anchor_of(pin))
        }
        Geom::VSeg { x, y0, y1 } => (x - w / 2.0, along(y0, y1, h, pos.align).0, TextAnchor::Middle),
        Geom::Rect { x0, y0, x1, y1 } => match pos.edge {
            Edge::Top | Edge::Bottom => {
                let (x, pin) = along(x0, x1, w, pos.align);
                let y = if pos.edge == Edge::Top { y0 + GAP } else { y1 - GAP - h };
                (x, y, anchor_of(pin))
            }
            Edge::Left => (x0 + GAP, along(y0, y1, h, pos.align).0, TextAnchor::Start),
            Edge::Right => (x1 - GAP - w, along(y0, y1, h, pos.align).0, TextAnchor::End),
        },
    }
}

fn screen(o: Orientation, a: f64, b: f64) -> (f64, f64) {
    match o {
        Orientation::Vertical => (a, b),
        Orientation::Horizontal => (b, a),
    }
}

fn span_or_full(map: &AxisMap, anchor: &Option<Anchor>, site: &Site) -> Result<Option<(f64, f64)>, Diagnostic> {
    match anchor {
        Some(a) => map.span(a, site),
        None => Ok(Some(map.range_px())),
    }
}

fn point_of(anchor: &Option<Anchor>) -> &Anchor {
    anchor.as_ref().expect("validate guarantees a point anchor")
}

/// Clips `(lo, hi)` to `[0, max]`; `None` when nothing is left.
fn clip(lo: f64, hi: f64, max: f64) -> Option<(f64, f64)> {
    let (lo, hi) = (lo.max(0.0), hi.min(max));
    (hi > lo).then_some((lo, hi))
}

/// The mark and the geometry its text is placed against.
type Shape = (Option<Mark>, Option<Geom>);

fn rule_shape(ann: &Annotation, ctx: &AnnotationCtx, b_map: &AxisMap, site: &Site) -> Result<Option<Shape>, Diagnostic> {
    let (w, h) = (ctx.plot.w, ctx.plot.h);
    let on_y = ann.y.as_ref().is_some_and(Anchor::is_point);
    // `at` is the rule's coordinate on the channel it is anchored to; `span`
    // runs along the other channel.
    let (at, span) = if on_y {
        let Some(at) = b_map.at(point_of(&ann.y), site)? else { return Ok(None) };
        let Some(span) = span_or_full(&ctx.x, &ann.x, site)? else { return Ok(None) };
        (at, span)
    } else {
        let Some(at) = ctx.x.at(point_of(&ann.x), site)? else { return Ok(None) };
        let Some(span) = span_or_full(b_map, &ann.y, site)? else { return Ok(None) };
        (at, span)
    };
    // A y rule on a vertical chart, or an x rule on a horizontal one, runs across.
    let horizontal = on_y == (ctx.orientation == Orientation::Vertical);
    let (cross_max, along_max) = if horizontal { (h, w) } else { (w, h) };
    let clipped = (0.0..=cross_max).contains(&at).then(|| clip(span.0, span.1, along_max)).flatten();
    let Some((lo, hi)) = clipped else {
        site.nothing_drawn("the rule falls outside the plot");
        return Ok(None);
    };
    Ok(Some(if horizontal {
        (Some(Mark::Segment { a: (lo, at), b: (hi, at) }), Some(Geom::HSeg { y: at, x0: lo, x1: hi }))
    } else {
        (Some(Mark::Segment { a: (at, lo), b: (at, hi) }), Some(Geom::VSeg { x: at, y0: lo, y1: hi }))
    }))
}

fn band_shape(ann: &Annotation, ctx: &AnnotationCtx, b_map: &AxisMap, site: &Site) -> Result<Option<Shape>, Diagnostic> {
    let Some(sa) = span_or_full(&ctx.x, &ann.x, site)? else { return Ok(None) };
    let Some(sb) = span_or_full(b_map, &ann.y, site)? else { return Ok(None) };
    let (x0, y0) = screen(ctx.orientation, sa.0, sb.0);
    let (x1, y1) = screen(ctx.orientation, sa.1, sb.1);
    let (Some((x0, x1)), Some((y0, y1))) = (clip(x0, x1, ctx.plot.w), clip(y0, y1, ctx.plot.h)) else {
        site.nothing_drawn("the band falls outside the plot");
        return Ok(None);
    };
    Ok(Some((
        Some(Mark::Rect { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }),
        Some(Geom::Rect { x0, y0, x1, y1 }),
    )))
}

/// The note's anchor point in screen px; `None` for a frame note or an
/// anchor that names no slot.
fn note_point(ann: &Annotation, ctx: &AnnotationCtx, b_map: &AxisMap, site: &Site) -> Result<Option<(f64, f64)>, Diagnostic> {
    let Some(a) = ctx.x.at(point_of(&ann.x), site)? else { return Ok(None) };
    let Some(b) = b_map.at(point_of(&ann.y), site)? else { return Ok(None) };
    Ok(Some(screen(ctx.orientation, a, b)))
}

fn trend_shape(ann: &Annotation, ctx: &AnnotationCtx, b_map: &AxisMap, site: &Site) -> Result<Option<(Mark, Option<Mark>, (f64, f64))>, Diagnostic> {
    let trend = ann.trend.as_ref().expect("validate guarantees trend samples");
    // On bars the authored range cuts the curve at the outer edges of the
    // bands it covers; the family hands over samples past both edges.
    let cut = match (&ctx.x, &ann.x) {
        (AxisMap::Band { .. }, Some(range @ Anchor::Range { .. })) => match ctx.x.span(range, site)? {
            Some(span) => Some(span),
            None => return Ok(None),
        },
        _ => None,
    };
    let point = |x: &super::model::Scalar, y: f64| -> Result<Option<(f64, f64)>, Diagnostic> {
        let Some(a) = ctx.x.curve_position(x, site)? else { return Ok(None) };
        let b = b_map.position(&super::model::Scalar::Number(y), site)?.expect("a continuous y");
        Ok(Some((a, b)))
    };
    let mut line = Vec::with_capacity(trend.line.len());
    for (x, y) in &trend.line {
        let Some(p) = point(x, *y)? else { return Ok(None) };
        line.push(p);
    }
    let area = match &trend.interval {
        None => None,
        Some(samples) => {
            let (mut hi, mut lo) = (Vec::new(), Vec::new());
            for (x, l, h) in samples {
                let (Some(ph), Some(pl)) = (point(x, *h)?, point(x, *l)?) else { return Ok(None) };
                hi.push(ph);
                lo.push(pl);
            }
            Some((hi, lo))
        }
    };
    let (line, area) = match cut {
        Some(span) => (cut_curve(&line, span), area.map(|(hi, lo)| (cut_curve(&hi, span), cut_curve(&lo, span)))),
        None => (line, area),
    };
    if line.len() < 2 {
        site.nothing_drawn("its curve lies outside the range's bands");
        return Ok(None);
    }
    let to_screen = |p: &(f64, f64)| screen(ctx.orientation, p.0, p.1);
    let line: Vec<(f64, f64)> = line.iter().map(to_screen).collect();
    let area = area.map(|(hi, lo)| Mark::Area { hi: hi.iter().map(to_screen).collect(), lo: lo.iter().map(to_screen).collect() });
    let end = *line.last().expect("at least two samples");
    Ok(Some((Mark::Polyline(line), area, end)))
}

/// `points` (along ascending) cut to `lo..=hi` along, the cut ends
/// interpolated between their neighbors.
fn cut_curve(points: &[(f64, f64)], (lo, hi): (f64, f64)) -> Vec<(f64, f64)> {
    let at = |a: &(f64, f64), b: &(f64, f64), x: f64| (x, a.1 + (x - a.0) / (b.0 - a.0) * (b.1 - a.1));
    let mut out = Vec::new();
    for w in points.windows(2) {
        let (a, b) = (&w[0], &w[1]);
        if a.0 < lo && b.0 > lo {
            out.push(at(a, b, lo));
        }
        if a.0 >= lo && a.0 <= hi {
            out.push(*a);
        }
        if a.0 < hi && b.0 > hi {
            out.push(at(a, b, hi));
        }
    }
    if let Some(last) = points.last().filter(|p| p.0 >= lo && p.0 <= hi) {
        out.push(*last);
    }
    out
}

/// The connector from a note's anchor toward the center of `bounds` (the
/// text box, or the rect enclosing it), from `reach` off the anchor to `GAP`
/// short of where it enters `bounds`; `None` when shorter than `GAP`.
pub(crate) fn connector(
    p: (f64, f64),
    reach: f64,
    (x0, y0, x1, y1): (f64, f64, f64, f64),
    curve: Curve,
    stroke_width: f64,
    arrow: bool,
) -> Option<Connector> {
    let (cx, cy) = ((x0 + x1) / 2.0 - p.0, (y0 + y1) / 2.0 - p.1);
    let entry = |d: f64, lo: f64, hi: f64, at: f64| match d {
        d if d > 0.0 => (lo - at) / d,
        d if d < 0.0 => (hi - at) / d,
        _ => f64::NEG_INFINITY,
    };
    let t = entry(cx, x0, x1, p.0).max(entry(cy, y0, y1, p.1));
    if t <= 0.0 {
        return None;
    }
    let len = cx.hypot(cy);
    let (ux, uy) = (cx / len, cy / len);
    let (from_d, to_d) = (reach, t * len - GAP);
    if to_d - from_d < GAP {
        return None;
    }
    let from = (p.0 + ux * from_d, p.1 + uy * from_d);
    let to = (p.0 + ux * to_d, p.1 + uy * to_d);
    let control = (curve == Curve::Natural).then(|| {
        let (cx, cy) = (to.0 - from.0, to.1 - from.1);
        let chord = cx.hypot(cy);
        let (nx, ny) = (-cy / chord, cx / chord);
        ((from.0 + to.0) / 2.0 + nx * 0.2 * chord, (from.1 + to.1) / 2.0 + ny * 0.2 * chord)
    });
    let arrow = arrow.then(|| {
        let tail = control.unwrap_or(to);
        let (ux, uy) = (from.0 - tail.0, from.1 - tail.1);
        let len = ux.hypot(uy);
        let (ux, uy) = (ux / len, uy / len);
        let side = (3.0 * stroke_width).max(6.0);
        let back = side * 3f64.sqrt() / 2.0;
        let (bx, by) = (from.0 - ux * back, from.1 - uy * back);
        let (nx, ny) = (-uy * side / 2.0, ux * side / 2.0);
        [from, (bx + nx, by + ny), (bx - nx, by - ny)]
    });
    Some(Connector { from, control, to, arrow })
}

/// The text slides along the edge it sits on.
fn along_edge(edge: Edge) -> Slide {
    match edge {
        Edge::Top | Edge::Bottom => Slide::X,
        Edge::Left | Edge::Right => Slide::Y,
    }
}

/// A box's leading coordinate along one screen axis of a `max` long plot,
/// clear of the chart's own ink reaching `lo`/`hi` past either end: a box
/// crossing into that ink moves out beyond it when its center is outside the
/// plot, else back inside.
fn clear(at: f64, size: f64, max: f64, lo: f64, hi: f64, gap: f64) -> f64 {
    const EPS: f64 = 1e-9;
    let center = at + size / 2.0;
    if lo > 0.0 && at < -EPS {
        if center < 0.0 { at.min(-lo - gap - size) } else { 0.0 }
    } else if hi > 0.0 && at + size > max + EPS {
        if center > max { at.max(max + hi + gap) } else { max - size }
    } else {
        at
    }
}

/// Rebuilds what follows the text: its enclosing rect and its connector.
fn relink(p: &mut Placed) {
    let Some(b) = &p.text else { return };
    let i = p.inset.unwrap_or(0.0);
    if p.inset.is_some() {
        p.enclosure = Some(Mark::Rect { x: b.x - i, y: b.y - i, w: b.w + 2.0 * i, h: b.h + 2.0 * i });
    }
    if let Some(t) = p.tether {
        let line = p.style.line();
        let bounds = (b.x - i, b.y - i, b.x + b.w + i, b.y + b.h + i);
        p.connector = connector(t.point, t.reach, bounds, line.curve, line.stroke.width, t.arrow);
    }
}

/// How far a text keeps others off its box: its enclosing rect's padding, else its halo, never under `GAP`.
fn text_gap(p: &Placed) -> f64 {
    p.inset.unwrap_or(p.style.text.halo).max(GAP)
}

/// The text's own box.
fn text_box(p: &Placed) -> Option<(f64, f64, f64, f64)> {
    let b = p.text.as_ref()?;
    Some((b.x, b.y, b.x + b.w, b.y + b.h))
}

/// Slides each text, in list order, the least distance along its free axis
/// that clears every earlier text, staying within the plot along that axis
/// (or no further outside it than it was). Two texts clear each other when
/// their boxes are `text_gap` apart, so halos may touch. A tie goes away from
/// the first text it overlaps. Connectors and enclosing rects follow.
pub(crate) fn separate(items: &mut [Placed], ctx: &AnnotationCtx) {
    for i in 0..items.len() {
        let Some(me) = text_box(&items[i]) else { continue };
        let slide = items[i].slide;
        // (lo, hi) along the slide axis, (lo, hi) across it.
        let split = |(x0, y0, x1, y1): (f64, f64, f64, f64)| match slide {
            Slide::X => ((x0, x1), (y0, y1)),
            Slide::Y => ((y0, y1), (x0, x1)),
        };
        let ((lo, hi), (c0, c1)) = split(me);
        let size = hi - lo;
        let mine = text_gap(&items[i]);
        // Leading coordinates `lo` may not take: one open interval per text in the way.
        let blocked: Vec<(f64, f64)> = items[..i]
            .iter()
            .filter_map(|o| Some((text_box(o)?, mine.max(text_gap(o)))))
            .map(|(b, gap)| (split(b), gap))
            .filter(|&((_, (o0, o1)), gap)| o0 - gap < c1 && c0 < o1 + gap)
            .map(|(((o0, o1), _), gap)| (o0 - gap - size, o1 + gap))
            .collect();
        let hit = |at: f64| blocked.iter().copied().find(|&(a, b)| a < at && at < b);
        let Some(first) = hit(lo) else { continue };
        let walk = |up: bool| {
            let mut at = lo;
            while let Some((a, b)) = hit(at) {
                at = if up { b } else { a };
            }
            at
        };
        let max = match slide {
            Slide::X => ctx.plot.w,
            Slide::Y => ctx.plot.h,
        };
        let fits = |at: f64| at >= lo.min(0.0) && at + size <= hi.max(max);
        let (up, down) = (walk(true), walk(false));
        let away_up = lo + size / 2.0 >= (first.0 + size + first.1) / 2.0;
        let pick = match (fits(up), fits(down)) {
            (true, false) => up,
            (false, true) => down,
            _ if (up - lo).abs() == (lo - down).abs() => if away_up { up } else { down },
            _ => if up - lo < lo - down { up } else { down },
        };
        let shift = pick - lo;
        let b = items[i].text.as_mut().expect("text_box found a text");
        match slide {
            Slide::X => b.x += shift,
            Slide::Y => b.y += shift,
        }
        relink(&mut items[i]);
    }
}

/// One annotation's placed geometry; `None` when it draws nothing on this
/// panel or names nothing on the axis (the warning is recorded).
pub(crate) fn place<'a>(ann: &'a Annotation, ctx: &AnnotationCtx) -> Result<Option<Placed<'a>>, Diagnostic> {
    if ann.panel.as_deref().is_some_and(|p| ctx.panel != Some(p)) {
        return Ok(None);
    }
    ann.validate(ctx.chart_id)?;
    let site = Site { chart_id: ctx.chart_id, index: ann.index };
    let b_map = match ann.y_axis {
        YAxis::Base => &ctx.y,
        YAxis::Layer(i) => ctx
            .layer_y
            .get(i)
            .ok_or_else(|| site.payload(format!("y_axis names layer {i}, which the chart does not have")))?,
    };
    let style = &ann.style;
    let mut placed = Placed {
        index: ann.index,
        plane: ann.plane,
        label: ann.label(),
        style,
        mark: None,
        interval: None,
        enclosure: None,
        text: None,
        connector: None,
        slide: Slide::Y,
        inset: None,
        tether: None,
    };
    let mut anchor_point = None;
    let connected = ann.connector != ConnectorKind::None;
    let circle_r = if ann.enclose == Some(Enclose::Circle) { style.circle().radius } else { 0.0 };
    let rect_inset = if ann.enclose == Some(Enclose::Rect) {
        style.rect().padding.expect("validate requires a note padding")
    } else {
        0.0
    };
    let geom = match ann.kind {
        Kind::Rule | Kind::Band => {
            let shape = if ann.kind == Kind::Rule {
                rule_shape(ann, ctx, b_map, &site)?
            } else {
                band_shape(ann, ctx, b_map, &site)?
            };
            let Some((mark, geom)) = shape else { return Ok(None) };
            placed.mark = mark;
            placed.slide = match geom {
                Some(Geom::HSeg { .. }) => Slide::Y,
                Some(Geom::VSeg { .. }) => Slide::X,
                _ => along_edge(style.text.position.edge),
            };
            geom
        }
        Kind::Trend => {
            let Some((line, area, end)) = trend_shape(ann, ctx, b_map, &site)? else { return Ok(None) };
            placed.mark = Some(line);
            placed.interval = area;
            // A zero-length segment: overlay text then honors align (ending at the
            // curve end for `start`) instead of centering on the point. Overlay on
            // the right edge is the plot's inside of that edge: the text ends at
            // the curve end, centered on it.
            let at_end = style.text.position.overlay && style.text.position.edge == Edge::Right;
            // Outside text on the right starts at the plot edge, so `clear` moves it
            // past the axis tick-label column whether or not the curve end reaches it.
            Some(if at_end {
                Geom::Rect { x0: end.0, y0: end.1, x1: end.0, y1: end.1 }
            } else {
                let x = if style.text.position.edge == Edge::Right { end.0.max(ctx.plot.w) } else { end.0 };
                Geom::HSeg { y: end.1, x0: x, x1: x }
            })
        }
        Kind::Note if ann.x.is_none() => {
            placed.slide = along_edge(style.text.position.edge);
            Some(Geom::Rect { x0: 0.0, y0: 0.0, x1: ctx.plot.w, y1: ctx.plot.h })
        }
        Kind::Note => {
            let Some(p) = note_point(ann, ctx, b_map, &site)? else { return Ok(None) };
            anchor_point = Some(p);
            placed.slide = along_edge(style.text.position.edge);
            if ann.enclose == Some(Enclose::Circle) {
                placed.enclosure = Some(Mark::Circle { c: p, r: circle_r });
            }
            let length = if connected { style.line().length.expect("validate requires a note length") } else { 0.0 };
            Some(Geom::Point { x: p.0, y: p.1, reach: circle_r + length + rect_inset })
        }
    };
    if let (Some(text), Some(geom)) = (&ann.text, geom) {
        let pad = if style.text.frame.visible { style.text.frame.padding } else { 0.0 };
        let cap = style.text.max_share.map_or(f64::INFINITY, |share| share * ctx.plot.w);
        let limit = style.text.max_width.map(|px| px.min(ctx.plot.w - 2.0 * (pad + GAP)).min(cap));
        let Block { lines, line_h, w, h, ascent } = text::measure_block(ctx.fonts, text, &style.text, limit)?;
        let size = (w + 2.0 * pad, h + 2.0 * pad);
        let (x, y, anchor) = match (ann.enclose, anchor_point) {
            (Some(Enclose::Rect), Some(p)) if !connected => {
                (p.0 - size.0 / 2.0, p.1 - size.1 / 2.0, TextAnchor::Middle)
            }
            // A connected note sits its line length off the anchor on its edge side;
            // `overlay` there only lets the text draw over the plot.
            (_, Some(_)) if connected => {
                place_box(geom, size, Position { overlay: false, ..style.text.position })
            }
            _ => place_box(geom, size, style.text.position),
        };
        let (x, y) = (x + style.text.dx, y + style.text.dy);
        let (w, h) = size;
        let r = ctx.reach;
        let y = clear(y, h, ctx.plot.h, r.top, r.bottom, GAP);
        // Past a side edge, the axis ink there spans the plot's rows; a box
        // above or below them keeps its x.
        let beside = y + h > -r.top && y < ctx.plot.h + r.bottom;
        let x = if beside { clear(x, w, ctx.plot.w, r.left, r.right, AXIS_GAP) } else { x };
        placed.text = Some(TextBox {
            x,
            y,
            w,
            h,
            pad,
            ascent,
            anchor,
            lines,
            line_h,
        });
        placed.inset = (ann.enclose == Some(Enclose::Rect)).then_some(rect_inset);
        placed.tether = anchor_point
            .filter(|_| connected)
            .map(|point| {
                let reach = if ann.enclose == Some(Enclose::Circle) { circle_r } else { style.circle().point_radius };
                Tether { point, reach, arrow: ann.connector == ConnectorKind::Arrow }
            });
        relink(&mut placed);
    }
    if placed.mark.is_none() && placed.enclosure.is_none() && placed.text.is_none() {
        site.nothing_drawn("it has no text, mark or enclosure");
        return Ok(None);
    }
    Ok(Some(placed))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::annotation::model::tests::contract;
    use crate::annotation::text::tests::fonts;
    use crate::annotation::{PlotRect, Reach};
    use crate::diagnostic::collect;
    use crate::families::temporal::date_text_day;
    use measure::FontBook;

    const W: f64 = 40.0;
    const H: f64 = 12.0;

    fn pos(edge: Edge, align: Align, overlay: bool) -> Position {
        Position { edge, align, overlay }
    }

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() < 0.01
    }

    /// Each geometry by edge, align and overlay, with the expected box origin
    /// and text-anchor for a 40x12 box.
    #[test]
    fn the_placement_matrix() {
        use Align::{Center as C, End as E, Start as S};
        use Edge::{Bottom as Bo, Left as L, Right as R, Top as T};
        use TextAnchor::{End as AE, Middle as AM, Start as AS};
        let hseg = Geom::HSeg { y: 50.0, x0: 10.0, x1: 110.0 };
        let vseg = Geom::VSeg { x: 50.0, y0: 10.0, y1: 110.0 };
        let rect = Geom::Rect { x0: 10.0, y0: 20.0, x1: 110.0, y1: 80.0 };
        let point = Geom::Point { x: 50.0, y: 50.0, reach: 0.0 };
        let frame = Geom::Rect { x0: 0.0, y0: 0.0, x1: 200.0, y1: 100.0 };
        #[rustfmt::skip]
        let rows: &[(Geom, Edge, Align, bool, f64, f64, TextAnchor)] = &[
            // Horizontal segment, outside.
            (hseg, T, S, false, 10.0, 34.0, AS), (hseg, T, C, false, 40.0, 34.0, AM), (hseg, T, E, false, 70.0, 34.0, AE),
            (hseg, Bo, S, false, 10.0, 54.0, AS), (hseg, Bo, C, false, 40.0, 54.0, AM), (hseg, Bo, E, false, 70.0, 54.0, AE),
            (hseg, L, S, false, -34.0, 38.0, AE), (hseg, L, C, false, -34.0, 44.0, AE), (hseg, L, E, false, -34.0, 50.0, AE),
            (hseg, R, S, false, 114.0, 38.0, AS), (hseg, R, C, false, 114.0, 44.0, AS), (hseg, R, E, false, 114.0, 50.0, AS),
            // Horizontal segment, on the line: edge does not matter.
            (hseg, T, S, true, 10.0, 44.0, AS), (hseg, Bo, C, true, 40.0, 44.0, AM), (hseg, L, E, true, 70.0, 44.0, AE),
            (hseg, R, S, true, 10.0, 44.0, AS),
            // Vertical segment, outside: top/bottom are its ends.
            (vseg, T, S, false, 10.0, -6.0, AE), (vseg, T, C, false, 30.0, -6.0, AM), (vseg, T, E, false, 50.0, -6.0, AS),
            (vseg, Bo, S, false, 10.0, 114.0, AE), (vseg, Bo, C, false, 30.0, 114.0, AM), (vseg, Bo, E, false, 50.0, 114.0, AS),
            (vseg, L, S, false, 6.0, 10.0, AE), (vseg, L, C, false, 6.0, 54.0, AE), (vseg, L, E, false, 6.0, 98.0, AE),
            (vseg, R, S, false, 54.0, 10.0, AS), (vseg, R, C, false, 54.0, 54.0, AS), (vseg, R, E, false, 54.0, 98.0, AS),
            // Vertical segment, on the line.
            (vseg, T, S, true, 30.0, 10.0, AM), (vseg, Bo, C, true, 30.0, 54.0, AM), (vseg, R, E, true, 30.0, 98.0, AM),
            // Rect, outside.
            (rect, T, S, false, 10.0, 4.0, AS), (rect, T, C, false, 40.0, 4.0, AM), (rect, T, E, false, 70.0, 4.0, AE),
            (rect, Bo, S, false, 10.0, 84.0, AS), (rect, Bo, C, false, 40.0, 84.0, AM), (rect, Bo, E, false, 70.0, 84.0, AE),
            (rect, L, S, false, -34.0, 20.0, AE), (rect, L, C, false, -34.0, 44.0, AE), (rect, L, E, false, -34.0, 68.0, AE),
            (rect, R, S, false, 114.0, 20.0, AS), (rect, R, C, false, 114.0, 44.0, AS), (rect, R, E, false, 114.0, 68.0, AS),
            // Rect, inside.
            (rect, T, S, true, 10.0, 24.0, AS), (rect, T, C, true, 40.0, 24.0, AM), (rect, T, E, true, 70.0, 24.0, AE),
            (rect, Bo, S, true, 10.0, 64.0, AS), (rect, Bo, C, true, 40.0, 64.0, AM), (rect, Bo, E, true, 70.0, 64.0, AE),
            (rect, L, S, true, 14.0, 20.0, AS), (rect, L, C, true, 14.0, 44.0, AS), (rect, L, E, true, 14.0, 68.0, AS),
            (rect, R, S, true, 66.0, 20.0, AE), (rect, R, C, true, 66.0, 44.0, AE), (rect, R, E, true, 66.0, 68.0, AE),
            // Point, outside: start before, end after.
            (point, T, S, false, 10.0, 34.0, AE), (point, T, C, false, 30.0, 34.0, AM), (point, T, E, false, 50.0, 34.0, AS),
            (point, Bo, S, false, 10.0, 54.0, AE), (point, Bo, C, false, 30.0, 54.0, AM), (point, Bo, E, false, 50.0, 54.0, AS),
            (point, L, S, false, 6.0, 38.0, AE), (point, L, C, false, 6.0, 44.0, AE), (point, L, E, false, 6.0, 50.0, AE),
            (point, R, S, false, 54.0, 38.0, AS), (point, R, C, false, 54.0, 44.0, AS), (point, R, E, false, 54.0, 50.0, AS),
            // Point, on it: centered whatever the edge and align.
            (point, T, S, true, 30.0, 44.0, AM), (point, R, E, true, 30.0, 44.0, AM),
            // Plot frame, as a rect.
            (frame, T, S, true, 0.0, 4.0, AS), (frame, T, C, false, 80.0, -16.0, AM), (frame, Bo, E, true, 160.0, 84.0, AE),
            (frame, R, C, false, 204.0, 44.0, AS),
        ];
        for (n, &(g, edge, align, overlay, x, y, anchor)) in rows.iter().enumerate() {
            let (gx, gy, ga) = place_box(g, (W, H), pos(edge, align, overlay));
            assert!(close(gx, x) && close(gy, y) && ga == anchor, "row {n}: {g:?} {edge:?} {align:?} {overlay}: got ({gx}, {gy}, {ga:?})");
        }
    }

    #[test]
    fn a_circle_s_radius_joins_the_gap() {
        let g = Geom::Point { x: 50.0, y: 50.0, reach: 8.0 };
        let (x, y, _) = place_box(g, (W, H), pos(Edge::Top, Align::Center, false));
        assert!(close(x, 30.0) && close(y, 50.0 - 8.0 - GAP - H));
    }

    fn circle_radius(radius: f64) -> f64 {
        let mut anns = contract();
        anns[7].style.circle.as_mut().unwrap().radius = radius;
        anns[7].enclose = Some(Enclose::Circle);
        let fonts = fonts();
        let c = ctx(&anns, &fonts, Orientation::Vertical);
        let Some(Mark::Circle { r, .. }) = place(&anns[7], &c).unwrap().unwrap().enclosure else { panic!() };
        r
    }

    #[test]
    fn a_circle_takes_the_radius_the_payload_bakes() {
        assert!(close(circle_radius(8.8), 8.8));
        assert!(close(circle_radius(3.0), 3.0));
    }

    fn tb(x: f64, y: f64) -> (f64, f64, f64, f64) {
        (x, y, x + W, y + H)
    }

    #[test]
    fn a_connector_aims_at_the_box_center_and_stops_gap_short_of_its_edge() {
        let c = connector((0.0, 0.0), 0.0, tb(20.0, -6.0), Curve::Linear, 1.0, false).unwrap();
        assert_eq!(c.from, (0.0, 0.0));
        assert!(close(c.to.0, 16.0) && close(c.to.1, 0.0));
        assert!(c.control.is_none() && c.arrow.is_none());
        // Below and left of center: it enters through the bottom edge, on the ray to (40, 6).
        let c = connector((30.0, 40.0), 0.0, tb(20.0, 0.0), Curve::Linear, 1.0, false).unwrap();
        let entry = (30.0 + 10.0 * 28.0 / 34.0, 12.0);
        assert!(close((c.to.0 - entry.0).hypot(c.to.1 - entry.1), GAP));
        assert!(close((c.to.0 - 30.0) * -34.0, (c.to.1 - 40.0) * 10.0));
        // `reach` starts it off the anchor.
        let c = connector((0.0, 0.0), 5.0, tb(20.0, -6.0), Curve::Linear, 1.0, false).unwrap();
        assert!(close(c.from.0, 5.0) && close(c.from.1, 0.0));
    }

    #[test]
    fn a_short_or_inside_connector_is_not_drawn() {
        assert!(connector((25.0, 5.0), 0.0, tb(20.0, 0.0), Curve::Linear, 1.0, false).is_none());
        assert!(connector((15.0, 5.0), 0.0, tb(20.0, 0.0), Curve::Linear, 1.0, false).is_none());
    }

    #[test]
    fn a_natural_connector_bends_and_an_arrow_points_at_the_anchor() {
        let c = connector((0.0, 0.0), 0.0, tb(24.0, -6.0), Curve::Natural, 1.0, true).unwrap();
        let ctrl = c.control.unwrap();
        // Chord (0,0)->(20,0): control 20% of 20px off the midpoint.
        assert!(close(ctrl.0, 10.0) && close(ctrl.1.abs(), 4.0));
        let [tip, b1, b2] = c.arrow.unwrap();
        assert_eq!(tip, c.from);
        // The base faces the box: both base corners are right of the tip.
        assert!(b1.0 > tip.0 && b2.0 > tip.0);
        let side = |p: (f64, f64), q: (f64, f64)| (p.0 - q.0).hypot(p.1 - q.1);
        assert!(close(side(tip, b1), 6.0) && close(side(tip, b2), 6.0) && close(side(b1, b2), 6.0));
        let thick = connector((0.0, 0.0), 0.0, tb(24.0, -6.0), Curve::Linear, 3.0, true).unwrap();
        let [t, a, _] = thick.arrow.unwrap();
        assert!(close(side(t, a), 9.0));
    }

    fn day(s: &str) -> f64 {
        date_text_day(s).unwrap()
    }

    fn ctx<'a>(anns: &'a [Annotation], fonts: &'a FontBook, orientation: Orientation) -> AnnotationCtx<'a> {
        let (along, cross) = match orientation {
            Orientation::Vertical => (200.0, 100.0),
            Orientation::Horizontal => (100.0, 200.0),
        };
        let y_range = match orientation {
            Orientation::Vertical => (cross, 0.0),
            Orientation::Horizontal => (0.0, cross),
        };
        AnnotationCtx {
            annotations: anns,
            chart_id: "c",
            panel: None,
            plot: match orientation {
                Orientation::Vertical => PlotRect { x: 0.0, y: 0.0, w: 200.0, h: 100.0 },
                Orientation::Horizontal => PlotRect { x: 0.0, y: 0.0, w: 200.0, h: 100.0 },
            },
            reach: Reach::default(),
            x: AxisMap::Time { extent: (day("2024-01-01"), day("2024-12-31")), range: (0.0, along) },
            y: AxisMap::Linear { domain: (0.0, 40_000_000.0), range: y_range },
            layer_y: vec![AxisMap::Linear { domain: (0.0, 20.0), range: y_range }],
            orientation,
            fonts,
            canvas: "#fff",
            clip_id: "c-ann0".into(),
        }
    }

    fn placed(i: usize, orientation: Orientation) -> Option<Placed<'static>> {
        let anns: &'static [Annotation] = Box::leak(contract().into_boxed_slice());
        let fonts: &'static FontBook = Box::leak(Box::new(fonts()));
        let c = ctx(anns, fonts, orientation);
        place(&anns[i], &c).unwrap()
    }

    #[test]
    fn a_y_rule_runs_across_the_plot_and_its_text_sits_above_the_right_end() {
        let p = placed(0, Orientation::Vertical).unwrap();
        assert_eq!(p.mark, Some(Mark::Segment { a: (0.0, 37.5), b: (200.0, 37.5) }));
        let t = p.text.unwrap();
        assert!(close(t.x + t.w, 200.0) && close(t.y + t.h, 37.5 - GAP));
        assert_eq!(t.anchor, TextAnchor::End);
    }

    #[test]
    fn orientation_swaps_the_channels_once() {
        let p = placed(0, Orientation::Horizontal).unwrap();
        // The y channel runs along screen x on a horizontal chart.
        assert_eq!(p.mark, Some(Mark::Segment { a: (125.0, 0.0), b: (125.0, 100.0) }));
        let x_rule = placed(1, Orientation::Horizontal).unwrap();
        let Some(Mark::Segment { a, b }) = x_rule.mark else { panic!() };
        assert_eq!(a.1, b.1);
        let v = placed(1, Orientation::Vertical).unwrap();
        let Some(Mark::Segment { a, b }) = v.mark else { panic!() };
        assert_eq!(a.0, b.0);
    }

    #[test]
    fn a_rule_segment_spans_its_range_on_the_other_axis() {
        let p = placed(2, Orientation::Vertical).unwrap();
        let Some(Mark::Segment { a, b }) = p.mark else { panic!() };
        let start = (day("2024-10-01") - day("2024-01-01")) / (day("2024-12-31") - day("2024-01-01")) * 200.0;
        assert!(close(a.0, start) && close(b.0, 200.0));
    }

    #[test]
    fn a_band_is_the_rect_of_its_spans_with_unset_axes_full() {
        let p = placed(4, Orientation::Vertical).unwrap();
        // y from 0.95 up: on a 0..40M axis that is the whole plot above ~0.
        let Some(Mark::Rect { x, y, w, h }) = p.mark else { panic!() };
        assert_eq!((x, y, w), (0.0, 0.0, 200.0));
        assert!(close(h, 100.0));
    }

    #[test]
    fn a_rule_outside_the_plot_warns_and_draws_nothing() {
        let mut anns = contract();
        anns[0].y = Some(Anchor::Value { value: super::super::model::Scalar::Number(90_000_000.0) });
        let fonts = fonts();
        let c = ctx(&anns, &fonts, Orientation::Vertical);
        let (out, warnings) = collect(|| place(&anns[0], &c).unwrap());
        assert!(out.is_none());
        assert_eq!(warnings[0].code, "WARN-ANNOTATION-NOTHING-DRAWN");
    }

    #[test]
    fn a_note_places_its_text_and_connector_against_its_point() {
        let p = placed(7, Orientation::Vertical).unwrap();
        assert!(p.mark.is_none());
        let t = p.text.as_ref().unwrap();
        let conn = p.connector.unwrap();
        let px = (day("2024-07-01T09:30") - day("2024-01-01")) / (day("2024-12-31") - day("2024-01-01")) * 200.0;
        assert!(close(conn.from.0, px) && close(conn.from.1, 100.0 - 18_400_000.0 / 40_000_000.0 * 100.0));
        // dy -46 lifts the box well above the point.
        assert!(t.y + t.h < conn.from.1);
        assert!(conn.control.is_some() && conn.arrow.is_some());
    }

    fn note(edit: impl FnOnce(&mut Annotation)) -> Placed<'static> {
        note_at(None, edit)
    }

    fn note_at(point_radius: Option<f64>, edit: impl FnOnce(&mut Annotation)) -> Placed<'static> {
        let mut anns = contract();
        anns[7].style.text.dx = 0.0;
        anns[7].style.text.dy = 0.0;
        anns[7].style.line.as_mut().unwrap().curve = Curve::Linear;
        anns[7].style.circle.as_mut().unwrap().point_radius = point_radius.unwrap_or(0.0);
        edit(&mut anns[7]);
        let anns: &'static [Annotation] = Box::leak(anns.into_boxed_slice());
        let fonts: &'static FontBook = Box::leak(Box::new(fonts()));
        let c = ctx(anns, fonts, Orientation::Vertical);
        place(&anns[7], &c).unwrap().unwrap()
    }

    fn anchor_7() -> (f64, f64) {
        let px = (day("2024-07-01T09:30") - day("2024-01-01")) / (day("2024-12-31") - day("2024-01-01")) * 200.0;
        (px, 100.0 - 18_400_000.0 / 40_000_000.0 * 100.0)
    }

    #[test]
    fn a_connected_note_sits_its_line_length_off_the_anchor_and_the_arrow_tips_the_anchor() {
        let p = note(|_| {});
        let (px, py) = anchor_7();
        let t = p.text.unwrap();
        let length = p.style.line().length.unwrap();
        assert!(close(t.y + t.h, py - length - GAP), "box bottom {} vs anchor {py}", t.y + t.h);
        let c = p.connector.unwrap();
        assert!(close(c.from.0, px) && close(c.from.1, py));
        assert!(close(c.to.0, px) && close(c.to.1, t.y + t.h + GAP));
        let [tip, b1, b2] = c.arrow.unwrap();
        assert_eq!(tip, c.from);
        // The base sits between the tip and the text.
        assert!(b1.1 < tip.1 && b2.1 < tip.1);
    }

    #[test]
    fn the_connector_s_text_end_tracks_dx_dy() {
        let base = note(|_| {});
        let moved = note(|a| {
            a.style.text.dx = 30.0;
            a.style.text.dy = -10.0;
        });
        let (b, m) = (base.text.unwrap(), moved.text.unwrap());
        assert!(close(m.x, b.x + 30.0) && close(m.y, b.y - 10.0));
        let c = moved.connector.unwrap();
        assert_eq!(c.from, base.connector.unwrap().from);
        // On the ray to the moved box's center, GAP short of its edge.
        let center = (m.x + m.w / 2.0, m.y + m.h / 2.0);
        let (ux, uy) = (center.0 - c.from.0, center.1 - c.from.1);
        let len = ux.hypot(uy);
        let edge = (c.to.0 + ux / len * GAP, c.to.1 + uy / len * GAP);
        assert!(close((c.to.0 - c.from.0) * uy, (c.to.1 - c.from.1) * ux));
        assert!(close(edge.1, m.y + m.h) && (m.x..=m.x + m.w).contains(&edge.0));
    }

    #[test]
    fn an_overlay_connected_note_sits_its_line_length_off_the_anchor_like_a_non_overlay_one() {
        for kind in [ConnectorKind::Line, ConnectorKind::Arrow] {
            let set = |overlay: bool| {
                note(move |a| {
                    a.connector = kind;
                    a.style.text.position.overlay = overlay;
                })
            };
            let (out, over) = (set(false), set(true));
            let (o, v) = (out.text.unwrap(), over.text.unwrap());
            assert!(close(o.x, v.x) && close(o.y, v.y), "{kind:?}: {v:?} vs {o:?}");
            let c = over.connector.expect("the overlay note keeps its connector");
            assert_eq!(c.arrow.is_some(), kind == ConnectorKind::Arrow);
            assert_eq!(c.from, out.connector.unwrap().from);
        }
    }

    #[test]
    fn an_overlay_connected_note_follows_dx_and_the_connector_follows_the_text() {
        let base = note(|a| a.style.text.position.overlay = true);
        let moved = note(|a| {
            a.style.text.position.overlay = true;
            a.style.text.dx = 25.0;
        });
        let (b, m) = (base.text.unwrap(), moved.text.unwrap());
        assert!(close(m.x, b.x + 25.0) && close(m.y, b.y));
        assert!(moved.connector.is_some());
        assert!(!close(moved.connector.unwrap().to.0, base.connector.unwrap().to.0));
    }

    #[test]
    fn an_overlay_note_without_a_connector_stays_centered_on_its_anchor() {
        let p = note(|a| {
            a.connector = ConnectorKind::None;
            a.style.text.position.overlay = true;
        });
        let (px, py) = anchor_7();
        let t = p.text.unwrap();
        assert!(close(t.x + t.w / 2.0, px) && close(t.y + t.h / 2.0, py));
        assert!(p.connector.is_none());
    }

    #[test]
    fn a_note_without_a_connector_sits_gap_off_its_anchor() {
        let p = note(|a| a.connector = ConnectorKind::None);
        let t = p.text.unwrap();
        assert!(close(t.y + t.h, anchor_7().1 - GAP));
        assert!(p.connector.is_none());
    }

    #[test]
    fn an_enclosed_note_s_connector_starts_on_the_enclosure() {
        let circled = note(|a| a.enclose = Some(Enclose::Circle));
        let (px, py) = anchor_7();
        let Some(Mark::Circle { r, .. }) = circled.enclosure else { panic!() };
        let t = circled.text.unwrap();
        let length = circled.style.line().length.unwrap();
        assert!(close(t.y + t.h, py - r - length - GAP));
        let c = circled.connector.unwrap();
        assert!(close(c.from.0, px) && close(c.from.1, py - r));
        assert_eq!(c.arrow.unwrap()[0], c.from);
        let boxed = note(|a| a.enclose = Some(Enclose::Rect));
        let t = boxed.text.unwrap();
        let Some(Mark::Rect { y, h, .. }) = boxed.enclosure else { panic!() };
        assert!(close(y, t.y - 4.0) && y + h < py);
        let c = boxed.connector.unwrap();
        assert!(close(c.from.1, py) && close(c.to.1, y + h + GAP));
    }

    #[test]
    fn an_unenclosed_connector_starts_at_the_point_marker_edge_and_the_arrow_tips_it() {
        let (px, py) = anchor_7();
        let bare = note(|_| {});
        let drawn = note_at(Some(5.0), |_| {});
        let c = drawn.connector.unwrap();
        assert!(close(c.from.0, px) && close(c.from.1, py - 5.0));
        assert_eq!(c.arrow.unwrap()[0], c.from);
        assert!(close(c.to.1, bare.connector.unwrap().to.1));
        let line = note_at(Some(5.0), |a| a.connector = ConnectorKind::Line);
        assert!(close(line.connector.unwrap().from.1, py - 5.0));
    }

    #[test]
    fn an_enclosing_circle_wins_over_the_point_marker_and_a_rect_leaves_it_at_the_point_edge() {
        let (_, py) = anchor_7();
        let plain = note(|a| a.enclose = Some(Enclose::Circle));
        let with_point = note_at(Some(5.0), |a| a.enclose = Some(Enclose::Circle));
        let r = |p: &Placed| match &p.enclosure {
            Some(Mark::Circle { r, .. }) => *r,
            _ => panic!(),
        };
        assert!(close(r(&with_point), r(&plain)) && close(r(&plain), 8.0));
        let circle_r = r(&with_point);
        assert!(close(with_point.connector.unwrap().from.1, py - circle_r));
        let boxed = note_at(Some(5.0), |a| a.enclose = Some(Enclose::Rect));
        assert!(close(boxed.connector.unwrap().from.1, py - 5.0));
    }

    #[test]
    fn enclosures_wrap_the_point_or_the_centered_text() {
        let mut anns = contract();
        for a in &mut anns {
            a.x = a.x.take().map(|x| match x {
                Anchor::Value { value: super::super::model::Scalar::Number(_) } => {
                    Anchor::Value { value: super::super::model::Scalar::Date("2024-06-01".into()) }
                }
                other => other,
            });
        }
        let fonts = fonts();
        let c = ctx(&anns, &fonts, Orientation::Vertical);
        let circle = place(&anns[8], &c).unwrap().unwrap();
        let Some(Mark::Circle { r, .. }) = circle.enclosure else { panic!() };
        assert_eq!(r, 8.0);
        let mut centered = anns[9].clone();
        centered.connector = ConnectorKind::None;
        let rect = place(&centered, &c).unwrap().unwrap();
        let t = rect.text.unwrap();
        let Some(Mark::Rect { x, y, w, h }) = rect.enclosure else { panic!() };
        assert!(close(x, t.x - 4.0) && close(y, t.y - 4.0) && close(w, t.w + 8.0) && close(h, t.h + 8.0));
        assert_eq!(t.anchor, TextAnchor::Middle);
        // The box's own padding is in the box.
        assert!(close(t.pad, 5.0));
    }

    #[test]
    fn a_frame_note_sits_on_the_plot_frame_and_panels_filter() {
        let mut anns = contract();
        anns[10].panel = None;
        let fonts = fonts();
        let c = ctx(&anns, &fonts, Orientation::Vertical);
        let p = place(&anns[10], &c).unwrap().unwrap();
        let t = p.text.unwrap();
        assert!(close(t.x, 0.0) && close(t.y, GAP));
        anns[10].panel = Some("EMEA".into());
        let c = ctx(&anns, &fonts, Orientation::Vertical);
        assert!(place(&anns[10], &c).unwrap().is_none());
        let mut c = ctx(&anns, &fonts, Orientation::Vertical);
        c.panel = Some("EMEA");
        assert!(place(&anns[10], &c).unwrap().is_some());
    }

    const LONG: &str = "Pipeline coverage fell below plan after the pricing change shipped in March, and the enterprise segment has not recovered since then";

    fn frame_box(text: &str, width: Option<f64>, position: Position) -> TextBox {
        let mut anns = contract();
        anns[10].panel = None;
        anns[10].text = Some(text.to_string());
        anns[10].style.text.max_width = width;
        anns[10].style.text.position = position;
        let fonts = fonts();
        let c = ctx(&anns, &fonts, Orientation::Vertical);
        place(&anns[10], &c).unwrap().unwrap().text.unwrap()
    }

    /// What resolve bakes `auto` to: 28 glyph widths of "n".
    fn auto_measure() -> f64 {
        text::measure(&fonts(), &"n".repeat(28), &contract()[10].style.text).unwrap().w
    }

    fn line_width(line: &str) -> f64 {
        text::measure(&fonts(), line, &contract()[10].style.text).unwrap().w
    }

    #[test]
    fn a_long_frame_note_wraps_within_its_measure_and_stays_in_the_plot() {
        let auto = Some(auto_measure());
        for position in [pos(Edge::Bottom, Align::End, true), pos(Edge::Top, Align::Start, true)] {
            let t = frame_box(LONG, auto, position);
            assert!(t.lines.len() > 2, "{:?}", t.lines);
            assert_eq!(t.lines.join(" "), LONG);
            let limit = auto_measure().min(200.0 - 2.0 * GAP);
            assert!(t.lines.iter().all(|l| line_width(l) <= limit + 1e-9), "{:?}", t.lines);
            let single = text::measure(&fonts(), LONG, &contract()[10].style.text).unwrap();
            assert!(close(t.h, single.h + t.line_h * (t.lines.len() - 1) as f64));
            assert!(t.x >= -1e-9 && t.x + t.w <= 200.0 + 1e-9 && t.y >= -1e-9 && t.y + t.h <= 100.0 + 1e-9, "{t:?}");
        }
        let bottom = frame_box(LONG, auto, pos(Edge::Bottom, Align::End, true));
        assert!(close(bottom.y + bottom.h, 100.0 - GAP) && close(bottom.x + bottom.w, 200.0));
    }

    #[test]
    fn a_short_frame_note_stays_on_one_line_and_a_narrower_measure_wraps_more() {
        let auto = Some(auto_measure());
        let top = pos(Edge::Top, Align::Start, true);
        assert_eq!(frame_box("Dashed = forecast", auto, top).lines, ["Dashed = forecast"]);
        let narrow = frame_box(LONG, Some(70.0), top);
        let wide = frame_box(LONG, Some(150.0), top);
        assert!(narrow.lines.len() > wide.lines.len() && wide.lines.len() > 1);
        assert!(narrow.lines.iter().all(|l| line_width(l) <= 70.0 + 1e-9), "{:?}", narrow.lines);
        assert!(wide.w > narrow.w && wide.w <= 150.0 + 1e-9);
    }

    #[test]
    fn a_measure_wider_than_the_plot_wraps_at_the_plots_room() {
        let t = frame_box(LONG, Some(1000.0), pos(Edge::Top, Align::Start, true));
        let room = 200.0 - 2.0 * GAP;
        assert!(t.lines.len() > 1, "{:?}", t.lines);
        assert!(t.lines.iter().all(|l| line_width(l) <= room + 1e-9), "{:?}", t.lines);
    }

    #[test]
    fn a_word_wider_than_the_measure_keeps_its_own_line() {
        let word = "Supercalifragilisticexpialidocious";
        let t = frame_box(&format!("a {word} b"), Some(40.0), pos(Edge::Top, Align::Start, true));
        assert_eq!(t.lines, ["a", word, "b"]);
    }

    #[test]
    fn a_note_without_a_measure_never_wraps_and_only_a_frame_note_may_carry_one() {
        assert_eq!(frame_box(LONG, None, pos(Edge::Top, Align::Start, true)).lines, [LONG]);
        let mut point = contract()[7].clone();
        point.style.text.max_width = Some(60.0);
        assert!(point.validate("c").unwrap_err().params["reason"].as_str().unwrap().contains("only a frame note or a trend wraps"));
    }

    #[test]
    fn a_trend_reads_its_layer_axis_and_places_text_at_its_end() {
        let p = placed(11, Orientation::Vertical).unwrap();
        let Some(Mark::Polyline(line)) = &p.mark else { panic!() };
        // Layer 0 is 0..20 over 100px: y 15 at 25px.
        assert!(close(line[2].1, 25.0));
        let Some(Mark::Area { hi, lo }) = &p.interval else { panic!() };
        assert_eq!((hi.len(), lo.len()), (3, 3));
        assert!(hi[0].1 < lo[0].1);
        let t = p.text.unwrap();
        assert!(close(t.x, 200.0 + GAP) && close(t.y + t.h / 2.0, line[2].1));
    }

    #[test]
    fn overlay_trend_text_ends_at_the_curve_end_instead_of_straddling_it() {
        let mut anns = contract();
        let fonts = fonts();
        let text = &mut anns[11].style.text;
        text.position = Position { edge: Edge::Top, align: Align::Start, overlay: true };
        let c = ctx(&anns, &fonts, Orientation::Vertical);
        let p = place(&anns[11], &c).unwrap().unwrap();
        let Some(Mark::Polyline(line)) = &p.mark else { panic!() };
        let t = p.text.unwrap();
        assert!(close(t.x + t.w, line[2].0) && close(t.y + t.h / 2.0, line[2].1));
    }

    #[test]
    fn right_edge_overlay_trend_text_ends_inside_the_plot_at_the_curve_end() {
        let mut anns = contract();
        let fonts = fonts();
        anns[11].style.text.position = Position { edge: Edge::Right, align: Align::Center, overlay: true };
        let c = ctx(&anns, &fonts, Orientation::Vertical);
        let p = place(&anns[11], &c).unwrap().unwrap();
        let Some(Mark::Polyline(line)) = &p.mark else { panic!() };
        let t = p.text.unwrap();
        assert!(t.x + t.w <= line[2].0 && close(t.y + t.h / 2.0, line[2].1));
    }

    #[test]
    fn bands_and_notes_swap_on_a_horizontal_chart() {
        // Horizontal: the x channel runs down screen y (0..100), y across screen x (0..200).
        let band = placed(3, Orientation::Horizontal).unwrap();
        let Some(Mark::Rect { x, y, w, h }) = band.mark else { panic!() };
        let along = |d: &str| (day(d) - day("2024-01-01")) / (day("2024-12-31") - day("2024-01-01")) * 100.0;
        assert_eq!((x, w), (0.0, 200.0));
        assert!(close(y, along("2024-06-01")) && close(y + h, along("2024-08-01")));
        let note = placed(7, Orientation::Horizontal).unwrap();
        let from = note.connector.unwrap().from;
        assert!(close(from.0, 18_400_000.0 / 40_000_000.0 * 200.0) && close(from.1, along("2024-07-01T09:30")));
    }

    fn resolved(anns: Vec<Annotation>, reach: Reach) -> Vec<Placed<'static>> {
        let anns: &'static [Annotation] = Box::leak(anns.into_boxed_slice());
        let fonts: &'static FontBook = Box::leak(Box::new(fonts()));
        let mut c = ctx(anns, fonts, Orientation::Vertical);
        c.reach = reach;
        let c: &'static AnnotationCtx = Box::leak(Box::new(c));
        super::super::resolve(c).unwrap()
    }

    fn date_value(s: &str) -> Option<super::super::model::Scalar> {
        Some(super::super::model::Scalar::Date(s.into()))
    }

    fn date(s: &str) -> Option<Anchor> {
        Some(Anchor::Value { value: super::super::model::Scalar::Date(s.into()) })
    }

    /// `n` copies of contract annotation `i`, indexed in order, each edited.
    fn copies(i: usize, edits: &[&dyn Fn(&mut Annotation)]) -> Vec<Annotation> {
        edits
            .iter()
            .enumerate()
            .map(|(n, edit)| {
                let mut a = contract()[i].clone();
                a.index = n;
                edit(&mut a);
                a
            })
            .collect()
    }

    fn overlap(a: &Placed, b: &Placed) -> bool {
        let gap = text_gap(a).max(text_gap(b));
        let (a, b) = (text_box(a).unwrap(), text_box(b).unwrap());
        a.0 - gap < b.2 && b.0 - gap < a.2 && a.1 - gap < b.3 && b.1 - gap < a.3
    }

    fn text_of<'a>(p: &'a Placed) -> &'a TextBox {
        p.text.as_ref().unwrap()
    }

    #[test]
    fn close_x_rules_slide_their_texts_apart_across_the_rules() {
        let items = resolved(
            copies(1, &[&|a| a.x = date("2024-02-01"), &|a| {
                a.x = date("2024-02-03");
                a.text = Some("x avg".into());
            }]),
            Reach::default(),
        );
        let alone = resolved(copies(1, &[&|a| {
            a.x = date("2024-02-03");
            a.text = Some("x avg".into());
        }]), Reach::default());
        let (first, second, own) = (text_of(&items[0]), text_of(&items[1]), text_of(&alone[0]));
        assert!(!overlap(&items[0], &items[1]), "{first:?} {second:?}");
        // The first keeps its place; the second moves along x only, past the first.
        assert!(close(first.x, (day("2024-02-01") - day("2024-01-01")) / (day("2024-12-31") - day("2024-01-01")) * 200.0));
        assert!(close(second.y, own.y) && second.x > own.x);
    }

    #[test]
    fn near_the_plot_edge_text_slides_back_into_the_plot() {
        let items = resolved(
            copies(1, &[&|a| a.x = date("2024-12-01"), &|a| a.x = date("2024-12-02")]),
            Reach::default(),
        );
        assert!(!overlap(&items[0], &items[1]));
        assert!(text_of(&items[1]).x < text_of(&items[0]).x);
    }

    #[test]
    fn outside_trend_text_sits_past_the_axis_column_whatever_the_curve_end() {
        let reach = Reach { left: 0.0, top: 0.0, right: 40.0, bottom: 0.0 };
        for end in ["2024-07-01", "2024-12-31"] {
            let items = resolved(copies(11, &[&|a| {
                let trend = a.trend.as_mut().unwrap();
                trend.line.last_mut().unwrap().0 = super::super::model::Scalar::Date(end.into());
                trend.interval = None;
            }]), reach);
            let Some(Mark::Polyline(line)) = &items[0].mark else { panic!() };
            let t = text_of(&items[0]);
            assert!(close(t.x, 200.0 + 40.0 + AXIS_GAP), "{end}: {t:?}");
            assert!(close(t.y + t.h / 2.0, line[2].1), "{end}: {t:?}");
        }
    }

    fn long_trend_text(share: f64) -> TextBox {
        let items = resolved(copies(11, &[&|a| {
            a.text = Some(LONG.into());
            a.style.text.max_share = Some(share);
        }]), Reach::default());
        text_of(&items[0]).clone()
    }

    #[test]
    fn a_long_trend_text_wraps_and_its_share_of_the_plot_caps_the_measure() {
        let (open, capped) = (long_trend_text(1.0), long_trend_text(0.5));
        assert!(open.lines.len() > 1 && open.w < 200.0, "{open:?}");
        assert!(capped.lines.len() > open.lines.len() && capped.w <= 100.0, "{capped:?}");
        // The cap narrows the measure; a word wider than it stays whole.
        let narrow = long_trend_text(0.1);
        assert!(narrow.lines.iter().all(|l| !l.contains(' ')) || narrow.w <= 20.0, "{narrow:?}");
        assert!(narrow.w < capped.w, "{narrow:?}");
    }

    #[test]
    fn trend_texts_sharing_an_end_slide_apart_along_y() {
        let items = resolved(copies(11, &[&|_| {}, &|a| a.text = Some("pred80".into())]), Reach::default());
        let (a, b) = (text_of(&items[0]), text_of(&items[1]));
        assert!(!overlap(&items[0], &items[1]), "{a:?} {b:?}");
        assert!(close(a.x, b.x) && !close(a.y, b.y));
    }

    #[test]
    fn band_texts_on_one_edge_slide_along_it() {
        let mut anns = copies(3, &[&|_| {}]);
        anns.extend(copies(4, &[&|a| {
            a.index = 1;
            a.text = Some("y above".into());
            a.style.text.position = Position { edge: Edge::Top, align: Align::Center, overlay: true };
        }]));
        let items = resolved(anns, Reach::default());
        let (a, b) = (text_of(&items[0]), text_of(&items[1]));
        assert!(!overlap(&items[0], &items[1]), "{a:?} {b:?}");
        assert!(close(a.y, b.y));
    }

    /// A 543x339 plot with the right axis' ink beside it, dated so that
    /// 2025-12-01 sits at x 364.6 and 2026-10-20 at x 520.
    fn wide_resolved(anns: Vec<Annotation>) -> Vec<Placed<'static>> {
        let anns: &'static [Annotation] = Box::leak(anns.into_boxed_slice());
        let fonts: &'static FontBook = Box::leak(Box::new(fonts()));
        let mut c = ctx(anns, fonts, Orientation::Vertical);
        c.plot = PlotRect { x: 0.0, y: 0.0, w: 543.0, h: 339.0 };
        let per_day = (520.0 - 364.6) / (day("2026-10-20") - day("2025-12-01"));
        let start = day("2025-12-01") - 364.6 / per_day;
        c.x = AxisMap::Time { extent: (start, start + 543.0 / per_day), range: (0.0, 543.0) };
        c.y = AxisMap::Linear { domain: (0.0, 31.0), range: (339.0, 0.0) };
        c.reach = Reach { left: 0.0, top: 0.0, right: 40.0, bottom: 30.0 };
        let c: &'static AnnotationCtx = Box::leak(Box::new(c));
        super::super::resolve(c).unwrap()
    }

    #[test]
    fn a_displaced_band_text_stays_beside_what_it_clears() {
        let mut anns = copies(7, &[&|a| {
            a.x = date("2025-12-01");
            a.y = Some(Anchor::Value { value: super::super::model::Scalar::Number(30.75) });
            a.text = Some("Dec peak".into());
            a.connector = ConnectorKind::None;
            a.enclose = Some(Enclose::Circle);
            a.style.text.dx = 0.0;
            a.style.text.dy = 24.4;
        }]);
        anns.extend(copies(3, &[&|a| {
            a.index = 1;
            a.x = Some(Anchor::Range { from: date_value("2026-10-01"), to: date_value("2026-10-31") });
            a.text = Some("current month (outside data)".into());
        }]));
        let items = wide_resolved(anns);
        let (note, band) = (text_of(&items[0]), text_of(&items[1]));
        // The texts share their rows and collide only by their halos: the band's
        // text slides past the note, not across the plot.
        assert!(band.x >= note.x + note.w, "{note:?} {band:?}");
        assert!(band.x + band.w <= 543.0, "{band:?}");
    }

    #[test]
    fn a_slid_note_s_connector_and_rect_follow_its_text() {
        let flat = |a: &mut Annotation| {
            a.style.text.dx = 0.0;
            a.style.text.dy = 0.0;
        };
        let boxed = |a: &mut Annotation| {
            flat(a);
            a.x = date("2024-03-05");
            a.enclose = Some(Enclose::Rect);
        };
        let items = resolved(copies(7, &[&|a| {
            flat(a);
            a.x = date("2024-03-01");
        }, &boxed]), Reach::default());
        let alone = resolved(copies(7, &[&boxed]), Reach::default());
        let (moved, own) = (text_of(&items[1]), text_of(&alone[0]));
        assert!(!overlap(&items[0], &items[1]));
        assert!(close(moved.y, own.y) && !close(moved.x, own.x), "{moved:?} {own:?}");
        let Some(Mark::Rect { x, .. }) = items[1].enclosure else { panic!() };
        assert!(close(x, moved.x - items[1].inset.unwrap()));
        let (c, c0) = (items[1].connector.as_ref().unwrap(), alone[0].connector.as_ref().unwrap());
        assert_eq!(c.from, c0.from);
        assert!(c.to.0 > c0.to.0);
    }

    #[test]
    fn edge_text_outside_the_plot_clears_the_axis_ink_there() {
        let reach = Reach { left: 30.0, top: 6.0, right: 40.0, bottom: 25.0 };
        let bottom = |a: &mut Annotation| a.style.text.position = Position { edge: Edge::Bottom, align: Align::Start, overlay: false };
        let rule = resolved(copies(1, &[&bottom]), reach);
        assert!(close(text_of(&rule[0]).y, 100.0 + 25.0 + GAP));
        let frame = resolved(copies(10, &[&|a| {
            a.panel = None;
            bottom(a);
        }]), reach);
        assert!(close(text_of(&frame[0]).y, 100.0 + 25.0 + GAP));
        // A trend's text right of its end, past the plot: beyond the right axis.
        let trend = resolved(copies(11, &[&|a| {
            let trend = a.trend.as_mut().unwrap();
            trend.line.last_mut().unwrap().0 = super::super::model::Scalar::Date("2024-12-31".into());
            trend.interval = None;
        }]), reach);
        let Some(Mark::Polyline(line)) = &trend[0].mark else { panic!() };
        let t = text_of(&trend[0]);
        assert!(close(line[2].0, 200.0) && close(t.x, 200.0 + 40.0 + AXIS_GAP));
        // No ink past an edge: text there stays where it was placed.
        let bare = resolved(copies(1, &[&bottom]), Reach::default());
        assert!(close(text_of(&bare[0]).y, 100.0 + GAP));
    }

    #[test]
    fn text_mostly_inside_the_plot_pulls_back_off_the_axis_ink() {
        let reach = Reach { left: 30.0, top: 6.0, right: 40.0, bottom: 25.0 };
        // A y rule 12px under the plot top: its text above it crosses the top edge.
        let items = resolved(copies(0, &[&|a| {
            a.y = Some(Anchor::Value { value: super::super::model::Scalar::Number(35_200_000.0) })
        }]), reach);
        let t = text_of(&items[0]);
        assert!(close(t.y, 0.0), "{t:?}");
        // Text in the top margin past the right edge clears the top ink and
        // stays clear of the right axis' rows, so it keeps its x.
        let x = (day("2024-12-20") - day("2024-01-01")) / (day("2024-12-31") - day("2024-01-01")) * 200.0;
        let corner = resolved(copies(1, &[&|a| a.x = date("2024-12-20")]), reach);
        let t = text_of(&corner[0]);
        assert!(close(t.x, x) && close(t.y + t.h, -6.0 - GAP), "{t:?}");
    }

    #[test]
    fn a_rule_and_a_note_sit_at_a_category_s_slot_center() {
        for (o, along) in [(Orientation::Vertical, 200.0), (Orientation::Horizontal, 100.0)] {
            let mut anns = contract();
            let west = Some(Anchor::Category { value: "West".into() });
            anns[1].x = west.clone();
            anns[7].x = west;
            anns[7].enclose = Some(Enclose::Circle);
            let fonts = fonts();
            let mut c = ctx(&anns, &fonts, o);
            c.x = AxisMap::Band {
                domain: vec!["East".into(), "West".into()],
                geometry: crate::families::cartesian::BandGeometry::new(2, 0.0, along, 0.0, 0.0),
                days: None,
                range: (0.0, along),
            };
            let center = along * 0.75;
            let Some(Mark::Segment { a, b }) = place(&anns[1], &c).unwrap().unwrap().mark else { panic!() };
            let note = place(&anns[7], &c).unwrap().unwrap();
            assert!(note.connector.is_some());
            let Some(Mark::Circle { c: at, .. }) = note.enclosure else { panic!() };
            match o {
                Orientation::Vertical => assert!(close(a.0, center) && close(b.0, center) && close(at.0, center)),
                Orientation::Horizontal => assert!(close(a.1, center) && close(b.1, center) && close(at.1, center)),
            }
        }
    }

    /// Four monthly bars over 200px (step 50, centers 25..175), with a trend
    /// whose y is the day count since 2024-01-01 at the given sample days.
    fn monthly_trend(samples: &[&str], range: Option<(&str, &str)>) -> (Vec<(f64, f64)>, Vec<(f64, f64)>) {
        use super::super::model::{Scalar, Trend};
        let days = ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"];
        let fit = |d: &str| (Scalar::Date(d.into()).days().unwrap() - Scalar::Date(days[0].into()).days().unwrap());
        let mut anns = contract();
        anns[11].x = range.map(|(f, t)| Anchor::Range { from: date_value(f), to: date_value(t) });
        anns[11].trend = Some(Trend {
            line: samples.iter().map(|d| (Scalar::Date((*d).into()), fit(d))).collect(),
            interval: Some(samples.iter().map(|d| (Scalar::Date((*d).into()), fit(d) - 1.0, fit(d) + 1.0)).collect()),
        });
        let fonts = fonts();
        let mut c = ctx(&anns, &fonts, Orientation::Vertical);
        c.x = AxisMap::Band {
            domain: days.iter().map(|d| d.to_string()).collect(),
            geometry: crate::families::cartesian::BandGeometry::new(4, 0.0, 200.0, 0.0, 0.0),
            days: Some(days.iter().map(|d| Scalar::Date((*d).into()).days().unwrap()).collect()),
            range: (0.0, 200.0),
        };
        c.layer_y = vec![AxisMap::Linear { domain: (0.0, 200.0), range: (100.0, 0.0) }];
        let p = place(&anns[11], &c).unwrap().unwrap();
        let Some(Mark::Polyline(line)) = p.mark else { panic!() };
        let Some(Mark::Area { hi, .. }) = p.interval else { panic!() };
        (line, hi)
    }

    #[test]
    fn a_band_trend_runs_from_the_first_bands_left_edge_to_the_last_bands_right_edge() {
        // Half a band out: 15.5 days before Jan 1 and after Apr 1.
        let (line, hi) = monthly_trend(&["2023-12-16T12:00:00", "2024-01-01", "2024-04-01", "2024-04-16T12:00:00"], None);
        assert!(close(line[0].0, 0.0) && close(line[3].0, 200.0), "{line:?}");
        assert!(close(line[1].0, 25.0) && close(line[2].0, 175.0), "{line:?}");
        assert!(close(hi[0].0, 0.0) && close(hi[3].0, 200.0));
        // y = days since Jan 1 (-15.5 and 106.5 at the edges), on 0..200 over 100px.
        assert!(close(line[0].1, 100.0 + 15.5 / 2.0) && close(line[3].1, 100.0 - 106.5 / 2.0), "{line:?}");
    }

    #[test]
    fn a_band_trend_range_cuts_at_the_outer_edges_of_the_bands_it_covers() {
        let dense = ["2023-12-01", "2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01"];
        let (line, hi) = monthly_trend(&dense, Some(("2024-02-01", "2024-04-01")));
        // Feb and Mar: 50..150px.
        assert!(close(line.first().unwrap().0, 50.0) && close(line.last().unwrap().0, 150.0), "{line:?}");
        assert!(close(hi.first().unwrap().0, 50.0) && close(hi.last().unwrap().0, 150.0));
        // Half a band out: day 15.5 (Feb 1 less half of January) and day 75.5.
        assert!(close(line[0].1, 100.0 - 15.5 / 2.0) && close(line.last().unwrap().1, 100.0 - 75.5 / 2.0), "{line:?}");
    }

    #[test]
    fn a_missing_layer_is_a_payload_error() {
        let anns = contract();
        let fonts = fonts();
        let mut c = ctx(&anns, &fonts, Orientation::Vertical);
        c.layer_y.clear();
        assert_eq!(place(&anns[11], &c).unwrap_err().code, "ERR-ANNOTATION-PAYLOAD");
    }
}
