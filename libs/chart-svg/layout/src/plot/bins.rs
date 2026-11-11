//! A histogram layer's marks in plot-local coordinates: one bar per populated bin, as production paints
//! them (`histogram/layout.rs`: `bar_edges`, `stack_segment`). A bin spans its edges on the linear x scale
//! and a bar fills `band_width` of that span (`bucket_bar_extent`); a bar's top corners take the authored
//! radius, held to a quarter of its width and half its height (`bar_corner_radius`). Bind draws bins only
//! as one solid series on a linear x of fixed ladder, so anything else reaching the painter is an engine bug.

use bind::bound::{Datum, Mark, MarkLayer, Paint, Plane, Pos};
use diagnostic::{diag, Diagnostic};
use scene::{Attrs, Node, PathData, Role};

use crate::contract::{aria_attrs, mark_group, ItemRole};
use crate::geom::Size;
use crate::plot::points::linear;
use crate::plot::value_axis::fraction;

/// A bar a bucket draws is never narrower than this (`MIN_BAR_PX`).
const MIN_BAR_PX: f64 = 1.0;

/// A corner is at most this share of the bar's width (`BAR_CORNER_THICKNESS_SHARE`).
const CORNER_THICKNESS_SHARE: f64 = 0.25;

/// `bucket_bar_extent`: the left edge and width of the bar over the pixel span `[start, end]`. A bucket
/// under a pixel wide draws whole, a pixel wide at least, so a dense series stays visible.
fn bar_extent(start: f64, end: f64, band_width: f64) -> (f64, f64) {
    let span = (end - start).abs();
    if span * band_width - 1.0 < MIN_BAR_PX {
        return (start.min(end), span.max(MIN_BAR_PX));
    }
    let edge = (1.0 - band_width) / 2.0;
    let (from, to) = (start + (1.0 - edge) * span, start + edge * span + 1.0);
    (from.min(to), (to - from).abs())
}

/// The bar from `top` down to `bottom` over `[x, x + w]`, its top corners rounded by `r`: a rect, or the
/// path that follows the corner circles centered `r` below the top and `r` in from each side.
fn bar(role: Role, (x, w): (f64, f64), (top, bottom): (f64, f64), r: f64, attrs: Attrs) -> Node {
    if r <= 0.0 {
        return Node::rect(role, (x, top, w, bottom - top), attrs);
    }
    let arc_end = bottom.min(top + r);
    let side = |y: f64| r - (r * r - (top + r - y).powi(2)).max(0.0).sqrt();
    let x2 = x + w;
    let straight = bottom > arc_end;
    let start = if straight {
        PathData::new().move_to(x, bottom).line_to(x, arc_end)
    } else {
        PathData::new().move_to(x + side(arc_end), arc_end)
    };
    let d = start.arc_to(r, true, x + side(top), top).line_to(x2 - side(top), top).arc_to(r, true, x2 - side(arc_end), arc_end);
    let d = if straight { d.line_to(x2 - side(arc_end), bottom) } else { d };
    Node::path(role, d.close(), attrs)
}

/// The datums of `marks`, the plot's layer `layer`, in one panel on a plot of `size`, under the plot's bin
/// style. `group` is the mark group's name.
pub fn bins(plot: &Plane, layer: usize, marks: &MarkLayer, datums: &[Datum], size: Size, group: &str) -> Result<Vec<Node>, Diagnostic> {
    if marks.mark != Mark::Bins {
        return Err(diag::err_internal(format!("bins painting a {:?} layer", marks.mark)));
    }
    let Some(style) = &plot.mark_style().bins else {
        return Err(diag::err_internal("a bin layer on a plot with no bin style"));
    };
    let x = &linear(plot.x(), "x")?.domain;
    let y = &linear(&plot.y()[marks.y_scale], "y")?.domain;
    let Paint::Solid(fill) = plot.series()[0].paint;
    if fill.3 != 1.0 {
        return Err(diag::err_internal(format!("a translucent bin fill {fill:?}")));
    }
    let fill = dbt_charts_color::rgb01_to_hex(fill.0, fill.1, fill.2);
    let bars = datums
        .iter()
        .map(|datum| {
            let Datum { x: Pos::Span(start, end), y0, y1, tooltip, row, .. } = datum else {
                return Err(diag::err_internal(format!("a bin at x {:?}", datum.x)));
            };
            if *y0 != 0.0 {
                return Err(diag::err_internal(format!("a bin that starts at {y0}, not at the count axis's zero")));
            }
            let (left, width) = bar_extent(fraction(x, *start) * size.width, fraction(x, *end) * size.width, style.band_width);
            let top = (1.0 - fraction(y, *y1)) * size.height;
            let radius = style.radius.min(CORNER_THICKNESS_SHARE * width).min((size.height - top) / 2.0);
            let attrs = Attrs::new()
                .set("fill", fill.as_str())
                .set("stroke-width", "0")
                .extend(aria_attrs(Some(&tooltip.text()), ItemRole::Bar).map_err(diag::err_internal)?);
            let role = Role::Mark { layer, series: None, datum: Some(row.0) };
            Ok(bar(role, (left, width), (top, size.height), radius, attrs))
        })
        .collect::<Result<Vec<_>, Diagnostic>>()?;
    Ok(vec![mark_group("rect", group, bars).map_err(diag::err_internal)?])
}
