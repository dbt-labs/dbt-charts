//! Anchor geometry to plot-relative pixel geometry, and text placement
//! against it. Everything here is computed in channel space `(a, b)` (x
//! channel, y channel) and swapped to screen space once.

use super::axis_map::{AxisMap, Site};
use super::model::{Align, Anchor, Annotation, Connector as ConnectorKind, Curve, Edge, Enclose, Kind, Position, Style, YAxis};
use super::text::{self, Metrics};
use super::{AnnotationCtx, Orientation, Plane};
use crate::diagnostic::Diagnostic;

/// Text-to-geometry gap, px.
pub(crate) const GAP: f64 = 4.0;

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
    pub text: String,
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

fn value_of(anchor: &Option<Anchor>) -> &super::model::Scalar {
    match anchor {
        Some(Anchor::Value { value }) => value,
        _ => unreachable!("validate guarantees a value anchor"),
    }
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
    let on_y = matches!(ann.y, Some(Anchor::Value { .. }));
    // `at` is the rule's coordinate on the channel it is anchored to; `span`
    // runs along the other channel.
    let (at, span) = if on_y {
        let Some(at) = b_map.position(value_of(&ann.y), site)? else { return Ok(None) };
        let Some(span) = span_or_full(&ctx.x, &ann.x, site)? else { return Ok(None) };
        (at, span)
    } else {
        let Some(at) = ctx.x.position(value_of(&ann.x), site)? else { return Ok(None) };
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
    let Some(a) = ctx.x.position(value_of(&ann.x), site)? else { return Ok(None) };
    let Some(b) = b_map.position(value_of(&ann.y), site)? else { return Ok(None) };
    Ok(Some(screen(ctx.orientation, a, b)))
}

fn trend_shape(ann: &Annotation, ctx: &AnnotationCtx, b_map: &AxisMap, site: &Site) -> Result<Option<(Mark, Option<Mark>, (f64, f64))>, Diagnostic> {
    let trend = ann.trend.as_ref().expect("validate guarantees trend samples");
    let point = |x: &super::model::Scalar, y: f64| -> Result<Option<(f64, f64)>, Diagnostic> {
        let Some(a) = ctx.x.position(x, site)? else { return Ok(None) };
        let b = b_map.position(&super::model::Scalar::Number(y), site)?.expect("a continuous y");
        Ok(Some(screen(ctx.orientation, a, b)))
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
            Some(Mark::Area { hi, lo })
        }
    };
    let end = *line.last().expect("at least two samples");
    Ok(Some((Mark::Polyline(line), area, end)))
}

/// The connector from a note's anchor to the nearest point of its box,
/// stopped `GAP` short of the box; `None` when shorter than `GAP`.
pub(crate) fn connector(p: (f64, f64), b: &TextBox, curve: Curve, stroke_width: f64, arrow: bool) -> Option<Connector> {
    let q = (p.0.clamp(b.x, b.x + b.w), p.1.clamp(b.y, b.y + b.h));
    let (dx, dy) = (p.0 - q.0, p.1 - q.1);
    let d = dx.hypot(dy);
    if d - GAP < GAP {
        return None;
    }
    let to = (q.0 + dx / d * GAP, q.1 + dy / d * GAP);
    let control = (curve == Curve::Natural).then(|| {
        let (cx, cy) = (to.0 - p.0, to.1 - p.1);
        let chord = cx.hypot(cy);
        let (nx, ny) = (-cy / chord, cx / chord);
        ((p.0 + to.0) / 2.0 + nx * 0.2 * chord, (p.1 + to.1) / 2.0 + ny * 0.2 * chord)
    });
    let arrow = arrow.then(|| {
        let tail = control.unwrap_or(to);
        let (ux, uy) = (p.0 - tail.0, p.1 - tail.1);
        let len = ux.hypot(uy);
        let (ux, uy) = (ux / len, uy / len);
        let side = (3.0 * stroke_width).max(6.0);
        let back = side * 3f64.sqrt() / 2.0;
        let (bx, by) = (p.0 - ux * back, p.1 - uy * back);
        let (nx, ny) = (-uy * side / 2.0, ux * side / 2.0);
        [p, (bx + nx, by + ny), (bx - nx, by - ny)]
    });
    Some(Connector { from: p, control, to, arrow })
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
    };
    let mut anchor_point = None;
    let geom = match ann.kind {
        Kind::Rule | Kind::Band => {
            let shape = if ann.kind == Kind::Rule {
                rule_shape(ann, ctx, b_map, &site)?
            } else {
                band_shape(ann, ctx, b_map, &site)?
            };
            let Some((mark, geom)) = shape else { return Ok(None) };
            placed.mark = mark;
            geom
        }
        Kind::Trend => {
            let Some((line, area, end)) = trend_shape(ann, ctx, b_map, &site)? else { return Ok(None) };
            placed.mark = Some(line);
            placed.interval = area;
            Some(Geom::Point { x: end.0, y: end.1, reach: 0.0 })
        }
        Kind::Note if ann.x.is_none() => {
            Some(Geom::Rect { x0: 0.0, y0: 0.0, x1: ctx.plot.w, y1: ctx.plot.h })
        }
        Kind::Note => {
            let Some(p) = note_point(ann, ctx, b_map, &site)? else { return Ok(None) };
            anchor_point = Some(p);
            let reach = if ann.enclose == Some(Enclose::Circle) {
                placed.enclosure = Some(Mark::Circle { c: p, r: style.circle.radius });
                style.circle.radius
            } else {
                0.0
            };
            Some(Geom::Point { x: p.0, y: p.1, reach })
        }
    };
    if let (Some(text), Some(geom)) = (&ann.text, geom) {
        let Metrics { w, h, ascent } = text::measure(ctx.fonts, text, &style.text)?;
        let pad = if style.text.frame.visible { style.text.frame.padding } else { 0.0 };
        let size = (w + 2.0 * pad, h + 2.0 * pad);
        let (x, y, anchor) = match (ann.enclose, anchor_point) {
            (Some(Enclose::Rect), Some(p)) => (p.0 - size.0 / 2.0, p.1 - size.1 / 2.0, TextAnchor::Middle),
            _ => place_box(geom, size, style.text.position),
        };
        let b = TextBox {
            x: x + style.text.dx,
            y: y + style.text.dy,
            w: size.0,
            h: size.1,
            pad,
            ascent,
            anchor,
            text: text.clone(),
        };
        if ann.enclose == Some(Enclose::Rect) {
            let inset = style.rect.padding;
            placed.enclosure =
                Some(Mark::Rect { x: b.x - inset, y: b.y - inset, w: b.w + 2.0 * inset, h: b.h + 2.0 * inset });
        }
        if let (Some(p), true) = (anchor_point, ann.connector != ConnectorKind::None) {
            placed.connector =
                connector(p, &b, style.line.curve, style.line.stroke.width, ann.connector == ConnectorKind::Arrow);
        }
        placed.text = Some(b);
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
    use crate::annotation::PlotRect;
    use crate::diagnostic::collect;
    use crate::families::temporal::date_text_day;
    use crate::text::FontBook;

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

    fn tb(x: f64, y: f64) -> TextBox {
        TextBox { x, y, w: W, h: H, pad: 0.0, ascent: 9.0, anchor: TextAnchor::Start, text: "t".into() }
    }

    #[test]
    fn a_connector_runs_to_the_nearest_box_point_stopped_short_by_gap() {
        let c = connector((0.0, 0.0), &tb(20.0, -6.0), Curve::Linear, 1.0, false).unwrap();
        assert_eq!(c.from, (0.0, 0.0));
        assert!(close(c.to.0, 16.0) && close(c.to.1, 0.0));
        assert!(c.control.is_none() && c.arrow.is_none());
        // Below the box: the nearest point is on its bottom edge.
        let c = connector((30.0, 40.0), &tb(20.0, 0.0), Curve::Linear, 1.0, false).unwrap();
        assert!(close(c.to.0, 30.0) && close(c.to.1, 16.0));
    }

    #[test]
    fn a_short_or_inside_connector_is_not_drawn() {
        assert!(connector((25.0, 5.0), &tb(20.0, 0.0), Curve::Linear, 1.0, false).is_none());
        assert!(connector((15.0, 5.0), &tb(20.0, 0.0), Curve::Linear, 1.0, false).is_none());
    }

    #[test]
    fn a_natural_connector_bends_and_an_arrow_points_at_the_anchor() {
        let c = connector((0.0, 0.0), &tb(24.0, -6.0), Curve::Natural, 1.0, true).unwrap();
        let ctrl = c.control.unwrap();
        // Chord (0,0)->(20,0): control 20% of 20px off the midpoint.
        assert!(close(ctrl.0, 10.0) && close(ctrl.1.abs(), 4.0));
        let [tip, b1, b2] = c.arrow.unwrap();
        assert_eq!(tip, c.from);
        // The base faces the box: both base corners are right of the tip.
        assert!(b1.0 > tip.0 && b2.0 > tip.0);
        let side = |p: (f64, f64), q: (f64, f64)| (p.0 - q.0).hypot(p.1 - q.1);
        assert!(close(side(tip, b1), 6.0) && close(side(tip, b2), 6.0) && close(side(b1, b2), 6.0));
        let thick = connector((0.0, 0.0), &tb(24.0, -6.0), Curve::Linear, 3.0, true).unwrap();
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
        let rect = place(&anns[9], &c).unwrap().unwrap();
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
        assert!(close(t.x, line[2].0 + GAP) && close(t.y + t.h / 2.0, line[2].1));
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

    #[test]
    fn a_missing_layer_is_a_payload_error() {
        let anns = contract();
        let fonts = fonts();
        let mut c = ctx(&anns, &fonts, Orientation::Vertical);
        c.layer_y.clear();
        assert_eq!(place(&anns[11], &c).unwrap_err().code, "ERR-ANNOTATION-PAYLOAD");
    }
}
