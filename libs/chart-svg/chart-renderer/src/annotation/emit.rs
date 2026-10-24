//! `Placed` items to nodes: one plane's marks (clipped); all text (unclipped) in the foreground, above the data.

use d3rs::shape::{line_path, Curve as D3Curve};
use scene::{Attrs, Node};

use super::model::{Curve, Stroke, Style};
use super::place::{Connector, Mark, Placed};
use super::{text, AnnotationCtx, Plane};
use crate::contract::{aria_attrs, ItemRole};
use crate::diagnostic::Diagnostic;
use crate::svg::num;

fn d3_curve(curve: Curve) -> D3Curve {
    match curve {
        Curve::Linear => D3Curve::Linear,
        Curve::Natural => D3Curve::Natural,
    }
}

fn stroked(attrs: Attrs, stroke: &Stroke) -> Attrs {
    let attrs = attrs.set("stroke", stroke.color.as_str()).set("stroke-width", num(stroke.width));
    match &stroke.dasharray {
        Some(dash) => attrs.set("stroke-dasharray", dash.as_str()),
        None => attrs,
    }
}

/// One mark in the style slot its kind reads: a segment is the rule, a rect
/// the band or enclosure rect, a polyline the trend line, an area its
/// interval, a circle the enclosure.
fn mark(m: &Mark, style: &Style) -> Node {
    match m {
        Mark::Segment { a, b } => Node::Line(stroked(
            Attrs::new()
                .set("x1", num(a.0))
                .set("y1", num(a.1))
                .set("x2", num(b.0))
                .set("y2", num(b.1))
                .set("fill", "none")
                .set("opacity", num(style.rule().opacity)),
            &style.rule().stroke,
        )),
        Mark::Rect { x, y, w, h } => {
            let attrs = Attrs::new()
                .set("x", num(*x))
                .set("y", num(*y))
                .set("width", num(*w))
                .set("height", num(*h))
                .set("fill", style.rect().fill.as_str())
                .set("opacity", num(style.rect().opacity));
            Node::Rect(match &style.rect().stroke {
                Some(stroke) => stroked(attrs, stroke),
                None => attrs,
            })
        }
        Mark::Polyline(points) => Node::Path(stroked(
            Attrs::new()
                .set("d", line_path(&[points.clone()], d3_curve(style.line().curve)))
                .set("fill", "none")
                .set("opacity", num(style.line().opacity)),
            &style.line().stroke,
        )),
        Mark::Area { hi, lo } => {
            let curve = d3_curve(style.line().curve);
            let back: Vec<(f64, f64)> = lo.iter().rev().copied().collect();
            let lo_path = line_path(&[back], curve);
            let d = format!("{}L{}Z", line_path(&[hi.clone()], curve), lo_path.strip_prefix('M').expect("a moveto"));
            Node::Path(
                Attrs::new()
                    .set("d", d)
                    .set("fill", style.area().fill.as_str())
                    .set("opacity", num(style.area().opacity)),
            )
        }
        Mark::Circle { c, r } => Node::Circle(stroked(
            Attrs::new()
                .set("cx", num(c.0))
                .set("cy", num(c.1))
                .set("r", num(*r))
                .set("fill", "none")
                .set("opacity", num(style.circle().opacity)),
            &style.circle().stroke,
        )),
    }
}

fn connector(c: &Connector, style: &Style) -> Vec<Node> {
    let mut d = format!("M{},{}", num(c.from.0), num(c.from.1));
    match c.control {
        Some(q) => d.push_str(&format!("Q{},{},{},{}", num(q.0), num(q.1), num(c.to.0), num(c.to.1))),
        None => d.push_str(&format!("L{},{}", num(c.to.0), num(c.to.1))),
    }
    let line = stroked(
        Attrs::new().set("d", d).set("fill", "none").set("opacity", num(style.line().opacity)),
        &style.line().stroke,
    )
    .set("aria-hidden", "true");
    let mut nodes = vec![Node::Path(line)];
    if let Some([a, b, t]) = c.arrow {
        let d = format!("M{},{}L{},{}L{},{}Z", num(a.0), num(a.1), num(b.0), num(b.1), num(t.0), num(t.1));
        nodes.push(Node::Path(
            Attrs::new()
                .set("d", d)
                .set("fill", style.line().stroke.color.as_str())
                .set("opacity", num(style.line().opacity))
                .set("aria-hidden", "true"),
        ));
    }
    nodes
}

fn plane_name(plane: Plane) -> &'static str {
    match plane {
        Plane::Background => "background",
        Plane::Foreground => "foreground",
    }
}

/// The item's one accessible node goes on its text when it has text, else
/// on its marks; whatever does not carry it is hidden.
fn item(p: &Placed, accessible: bool, children: Vec<Node>) -> Result<Node, Diagnostic> {
    let attrs = if accessible {
        aria_attrs(Some(&p.label), ItemRole::Annotation)?
    } else {
        Attrs::new().set("aria-hidden", "true")
    };
    Ok(Node::group(attrs, children))
}

pub(crate) fn nodes(ctx: &AnnotationCtx, items: &[Placed], plane: Plane) -> Result<Vec<Node>, Diagnostic> {
    let mut out = Vec::new();
    if plane == Plane::Background && !items.is_empty() {
        out.push(Node::ClipPath {
            attrs: Attrs::new().set("id", ctx.clip_id.as_str()),
            children: vec![Node::Rect(
                Attrs::new().set("x", "0").set("y", "0").set("width", num(ctx.plot.w)).set("height", num(ctx.plot.h)),
            )],
        });
    }
    let mine: Vec<&Placed> = items.iter().filter(|p| p.plane == plane).collect();
    let texts: Vec<&Placed> = if plane == Plane::Foreground { items.iter().filter(|p| p.text.is_some()).collect() } else { Vec::new() };
    if mine.is_empty() && texts.is_empty() {
        return Ok(out);
    }
    let mut marks = Vec::new();
    // Tints first so a rule or trend line never sits under a band's fill.
    let tint = |m: &Mark| matches!(m, Mark::Rect { .. } | Mark::Area { .. });
    for p in &mine {
        let tints: Vec<&Mark> = [&p.interval, &p.mark].into_iter().flatten().filter(|m| tint(m)).collect();
        if tints.is_empty() {
            continue;
        }
        let whole = p.enclosure.is_none() && p.connector.is_none() && p.mark.as_ref().is_none_or(tint);
        marks.push(item(p, whole && p.text.is_none(), tints.into_iter().map(|m| mark(m, p.style)).collect())?);
    }
    for p in &mine {
        let Some(lines) = p.mark.as_ref().filter(|m| !tint(m)) else { continue };
        let accessible = p.text.is_none() && p.enclosure.is_none() && p.connector.is_none();
        marks.push(item(p, accessible, vec![mark(lines, p.style)])?);
    }
    // Connectors and enclosures overhang the plot like text, so they are never clipped.
    let mut links = Vec::new();
    for p in &mine {
        if p.enclosure.is_none() && p.connector.is_none() {
            continue;
        }
        let lines = p.mark.as_ref().is_some_and(|m| !tint(m));
        let mut children = Vec::new();
        if let Some(e) = &p.enclosure {
            children.push(mark(e, p.style));
        }
        if let Some(c) = &p.connector {
            children.extend(connector(c, p.style));
        }
        links.push(item(p, p.text.is_none() && !lines, children)?);
    }
    let mut labels = Vec::new();
    for p in &texts {
        if let Some(b) = &p.text {
            let halo = if matches!(p.enclosure, Some(Mark::Rect { .. })) { &p.style.rect().fill } else { ctx.canvas };
            labels.push(item(p, true, text::nodes(b, &p.style.text, halo))?);
        }
    }
    let layer = |name: &'static str, children| Node::group(Attrs::new().set("class", name), children);
    let marks_group = Node::group(
        Attrs::new().set("class", "annotations-marks").set("clip-path", format!("url(#{})", ctx.clip_id)),
        marks,
    );
    out.push(Node::group(
        Attrs::new()
            .set("class", format!("annotations annotations-{}", plane_name(plane)))
            .set("transform", format!("translate({},{})", num(ctx.plot.x), num(ctx.plot.y))),
        vec![marks_group, layer("annotations-links", links), layer("annotations-text", labels)],
    ));
    Ok(out)
}
