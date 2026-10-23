//! `Placed` items to SVG: one plane's marks (clipped) and text (unclipped).

use std::fmt::Write as _;

use d3rs::shape::{line_path, Curve as D3Curve};

use super::model::{Curve, Stroke, Style};
use super::place::{Connector, Mark, Placed};
use super::{text, AnnotationCtx, Plane};
use crate::contract::{aria_attr, ItemRole};
use crate::diagnostic::Diagnostic;
use crate::svg::{num};
use print::{xml_escape_attr};

fn d3_curve(curve: Curve) -> D3Curve {
    match curve {
        Curve::Linear => D3Curve::Linear,
        Curve::Natural => D3Curve::Natural,
    }
}

fn stroke_attrs(svg: &mut String, stroke: &Stroke) {
    let _ = write!(svg, " stroke=\"{}\" stroke-width=\"{}\"", xml_escape_attr(&stroke.color), num(stroke.width));
    if let Some(dash) = &stroke.dasharray {
        let _ = write!(svg, " stroke-dasharray=\"{}\"", xml_escape_attr(dash));
    }
}

fn path(svg: &mut String, d: &str) {
    let _ = write!(svg, "<path d=\"{d}\"");
}

/// One mark in the style slot its kind reads: a segment is the rule, a rect
/// the band or enclosure rect, a polyline the trend line, an area its
/// interval, a circle the enclosure.
fn mark(svg: &mut String, m: &Mark, style: &Style) {
    match m {
        Mark::Segment { a, b } => {
            let _ = write!(
                svg,
                "<line x1=\"{}\" y1=\"{}\" x2=\"{}\" y2=\"{}\" fill=\"none\" opacity=\"{}\"",
                num(a.0),
                num(a.1),
                num(b.0),
                num(b.1),
                num(style.rule.opacity)
            );
            stroke_attrs(svg, &style.rule.stroke);
        }
        Mark::Rect { x, y, w, h } => {
            let _ = write!(
                svg,
                "<rect x=\"{}\" y=\"{}\" width=\"{}\" height=\"{}\" fill=\"{}\" opacity=\"{}\"",
                num(*x),
                num(*y),
                num(*w),
                num(*h),
                xml_escape_attr(&style.rect.fill),
                num(style.rect.opacity)
            );
            if let Some(stroke) = &style.rect.stroke {
                stroke_attrs(svg, stroke);
            }
        }
        Mark::Polyline(points) => {
            path(svg, &line_path(&[points.clone()], d3_curve(style.line.curve)));
            let _ = write!(svg, " fill=\"none\" opacity=\"{}\"", num(style.line.opacity));
            stroke_attrs(svg, &style.line.stroke);
        }
        Mark::Area { hi, lo } => {
            let curve = d3_curve(style.line.curve);
            let back: Vec<(f64, f64)> = lo.iter().rev().copied().collect();
            let lo_path = line_path(&[back], curve);
            let d = format!("{}L{}Z", line_path(&[hi.clone()], curve), lo_path.strip_prefix('M').expect("a moveto"));
            path(svg, &d);
            let _ = write!(
                svg,
                " fill=\"{}\" opacity=\"{}\"",
                xml_escape_attr(&style.area.fill),
                num(style.area.opacity)
            );
        }
        Mark::Circle { c, r } => {
            let _ = write!(
                svg,
                "<circle cx=\"{}\" cy=\"{}\" r=\"{}\" fill=\"none\" opacity=\"{}\"",
                num(c.0),
                num(c.1),
                num(*r),
                num(style.circle.opacity)
            );
            stroke_attrs(svg, &style.circle.stroke);
        }
    }
    svg.push_str("/>");
}

fn connector(svg: &mut String, c: &Connector, style: &Style) {
    let mut d = format!("M{},{}", num(c.from.0), num(c.from.1));
    match c.control {
        Some(q) => {
            let _ = write!(d, "Q{},{},{},{}", num(q.0), num(q.1), num(c.to.0), num(c.to.1));
        }
        None => {
            let _ = write!(d, "L{},{}", num(c.to.0), num(c.to.1));
        }
    }
    path(svg, &d);
    let _ = write!(svg, " fill=\"none\" opacity=\"{}\"", num(style.line.opacity));
    stroke_attrs(svg, &style.line.stroke);
    svg.push_str(" aria-hidden=\"true\"/>");
    if let Some([a, b, t]) = c.arrow {
        let d = format!("M{},{}L{},{}L{},{}Z", num(a.0), num(a.1), num(b.0), num(b.1), num(t.0), num(t.1));
        path(svg, &d);
        let _ = write!(
            svg,
            " fill=\"{}\" opacity=\"{}\" aria-hidden=\"true\"/>",
            xml_escape_attr(&style.line.stroke.color),
            num(style.line.opacity)
        );
    }
}

fn plane_name(plane: Plane) -> &'static str {
    match plane {
        Plane::Background => "background",
        Plane::Foreground => "foreground",
    }
}

/// The item's one accessible node goes on its text when it has text, else
/// on its marks; whatever does not carry it is hidden.
fn open_item(svg: &mut String, p: &Placed, accessible: bool) -> Result<(), Diagnostic> {
    if accessible {
        let _ = write!(svg, "<g{}>", aria_attr(Some(&p.label), ItemRole::Annotation)?);
    } else {
        svg.push_str("<g aria-hidden=\"true\">");
    }
    Ok(())
}

pub(crate) fn paint(ctx: &AnnotationCtx, items: &[Placed], plane: Plane, svg: &mut String) -> Result<(), Diagnostic> {
    if plane == Plane::Background && !items.is_empty() {
        let _ = write!(
            svg,
            "<clipPath id=\"{}\"><rect x=\"0\" y=\"0\" width=\"{}\" height=\"{}\"/></clipPath>",
            xml_escape_attr(&ctx.clip_id),
            num(ctx.plot.w),
            num(ctx.plot.h)
        );
    }
    let mine: Vec<&Placed> = items.iter().filter(|p| p.plane == plane).collect();
    if mine.is_empty() {
        return Ok(());
    }
    let _ = write!(
        svg,
        "<g class=\"annotations annotations-{}\" transform=\"translate({},{})\">",
        plane_name(plane),
        num(ctx.plot.x),
        num(ctx.plot.y)
    );
    let _ = write!(svg, "<g class=\"annotations-marks\" clip-path=\"url(#{})\">", xml_escape_attr(&ctx.clip_id));
    for p in &mine {
        if p.mark.is_none() && p.interval.is_none() && p.enclosure.is_none() && p.connector.is_none() {
            continue;
        }
        open_item(svg, p, p.text.is_none())?;
        for m in [&p.interval, &p.mark, &p.enclosure].into_iter().flatten() {
            mark(svg, m, p.style);
        }
        if let Some(c) = &p.connector {
            connector(svg, c, p.style);
        }
        svg.push_str("</g>");
    }
    svg.push_str("</g><g class=\"annotations-text\">");
    for p in &mine {
        if let Some(b) = &p.text {
            open_item(svg, p, true)?;
            text::emit(svg, b, &p.style.text, ctx.canvas);
            svg.push_str("</g>");
        }
    }
    svg.push_str("</g></g>");
    Ok(())
}
