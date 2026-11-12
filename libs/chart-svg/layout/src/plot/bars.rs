//! A bar layer's marks in plot-local coordinates, as production paints a vertical single-series
//! bar (`bar/vertical.rs`, `bar/marks.rs::bar_passes`): one filled path per datum in its own
//! `rect` mark group, rounded at the end away from zero, outlined in the canvas color that
//! [`crate::knockout`] cuts into the grid beneath. Bind draws bars only on a band x and a linear y
//! from the zero baseline, one series, an opaque fill and no value label, so anything else
//! reaching the painter is an engine bug.

use bind::bound::{BarStyle, Datum, Mark, MarkLayer, Paint, Plane, Pos, Series};
use d3rs::scale::Band;
use diagnostic::{diag, Diagnostic};
use scene::{Attrs, Node, PathData, Role};

use crate::contract::{aria_attrs, mark_group, ItemRole};
use crate::geom::{bar_corner_radius, Size};
use crate::knockout::knock_out;
use crate::plot::points::linear;
use crate::plot::value_axis::fraction;

/// `FUNCTION APPLICATION`, which opens a tooltip's category header (`contract::ROLE_HEADER`).
const ROLE_HEADER: char = '\u{2061}';

/// The path of a bar whose end away from zero is rounded by `r`: the top corners of a rise, the
/// bottom corners of a fall (the arcs' sweep flips with the mirror).
fn rounded(x: f64, y: f64, w: f64, h: f64, r: f64, rises: bool) -> PathData {
    let (x2, y2) = (x + w, y + h);
    if rises {
        PathData::new().move_to(x, y2).line_to(x, y + r).arc_to(r, true, x + r, y).line_to(x2 - r, y).arc_to(r, true, x2, y + r).line_to(x2, y2).close()
    } else {
        PathData::new().move_to(x, y).line_to(x, y2 - r).arc_to(r, false, x + r, y2).line_to(x2 - r, y2).arc_to(r, false, x2, y2 - r).line_to(x2, y).close()
    }
}

/// One bar: its filled shape and, where it is outlined, the stroke-only pass drawn over it
/// (`bar_passes`).
fn bar(
    style: &BarStyle,
    (x, y, w, h): (f64, f64, f64, f64),
    rises: bool,
    fill: &str,
    label: &str,
    role: Role,
) -> Result<Vec<Node>, Diagnostic> {
    let r = bar_corner_radius(style.radius, w, h);
    // An outline that would leave the bar under half a pixel of fill draws none (`halo_w`).
    let outline = style.outline.as_ref().filter(|(_, width)| w - width >= 0.5);
    let shape = |role: Role, attrs: Attrs| {
        if r > 0.0 {
            Node::path(role, rounded(x, y, w, h, r, rises), attrs)
        } else {
            Node::rect(role, (x, y, w, h), attrs)
        }
    };
    let mut filled = Attrs::new().set("fill", fill);
    if let Some(opacity) = style.opacity {
        filled = filled.num("fill-opacity", opacity);
    }
    if let Some((color, width)) = outline {
        filled = filled.set("stroke", color.as_str()).num("stroke-width", *width);
    }
    let filled = filled.extend(aria_attrs(Some(label), ItemRole::Bar).map_err(diag::err_internal)?);
    let mut nodes = vec![shape(role, filled)];
    if let Some((color, width)) = outline {
        nodes.push(shape(Role::Chrome, Attrs::new().set("fill", "none").set("stroke", color.as_str()).num("stroke-width", *width)));
    }
    mark_group("rect", "layer_0_marks", nodes).map(|group| vec![group]).map_err(diag::err_internal)
}

/// The datums of `marks`, the plot's layer `layer`, on a plot of `size`, with the knockout of their
/// canvas-colored outlines: the undercoat the grid is cut by, then the masked marks. `canvas` is
/// the chart's background and `scope` its id as an SVG id stem.
pub fn bars(plot: &Plane, layer: usize, marks: &MarkLayer, datums: &[Datum], size: Size, canvas: &str, scope: &str) -> Result<Vec<Node>, Diagnostic> {
    if marks.mark != Mark::Bars {
        return Err(diag::err_internal(format!("bars painting a {:?} layer", marks.mark)));
    }
    let Some(style) = &plot.mark_style().bars else {
        return Err(diag::err_internal("a bar layer on a plot with no bar mark"));
    };
    let [drawn] = plot.series() else {
        return Err(diag::err_internal("bars painting more than one series"));
    };
    let y = &linear(&plot.y()[marks.y_scale], "y")?.domain;
    let count = match &plot.x().kind {
        bind::bound::ScaleKind::Band(domain) => domain.keys().len(),
        _ => return Err(diag::err_internal("bars on a scale that is not a band")),
    };
    let band = Band::new(count, [0.0, size.width], style.padding_inner, style.padding_outer, 0.5, false);
    let width = {
        let thickness = (band.bandwidth * style.band_width).min(band.bandwidth);
        style.max_size.map_or(thickness, |max| thickness.min(max))
    };
    let at = |value: f64| (1.0 - fraction(y, value)) * size.height;
    let mut painted = Vec::new();
    for (index, datum) in datums.iter().enumerate() {
        if !datum.defined {
            continue;
        }
        let Pos::Band(slot) = datum.x else {
            return Err(diag::err_internal(format!("a bar at x {:?}", datum.x)));
        };
        let Series { paint: Paint::Solid(fill), .. } = drawn;
        if fill.3 != 1.0 {
            return Err(diag::err_internal(format!("a translucent bar fill {fill:?}")));
        }
        let category = plot_text(plot, slot)?;
        let label = format!("{ROLE_HEADER}{category}; {}", datum.tooltip.text());
        let (end, from) = (at(datum.y1), at(datum.y0));
        let left = band.position(slot) + (band.bandwidth - width) / 2.0;
        let fill = dbt_charts_color::rgb01_to_hex(fill.0, fill.1, fill.2);
        let role = Role::Mark { layer, series: None, datum: Some(index) };
        painted.extend(bar(style, (left, end.min(from), width, (end - from).abs()), datum.y1 >= 0.0, &fill, &label, role)?);
    }
    let knocked = knock_out(painted, canvas, "ko0", scope, (0.0, 0.0, size.width, size.height));
    let mut nodes = knocked.undercoat;
    nodes.extend(knocked.marks);
    Ok(nodes)
}

fn plot_text(plot: &Plane, slot: usize) -> Result<&str, Diagnostic> {
    match &plot.x().kind {
        bind::bound::ScaleKind::Band(domain) => domain.text().get(slot).map(String::as_str).ok_or_else(|| diag::err_internal("a bar's band is in its domain")),
        _ => Err(diag::err_internal("bars on a scale that is not a band")),
    }
}
