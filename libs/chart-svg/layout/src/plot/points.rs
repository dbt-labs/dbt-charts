//! A point layer's marks in plot-local coordinates: one Vega symbol per datum, each in its own
//! symbol mark group as production paints it (`scatter/layout.rs`: `point_glyph`, `mark_group`).
//! Bind draws points as one Vega symbol per datum (the point mark's authored one, or its shape channel's)
//! at numbers on linear scales, at instants on a time scale (kept off the plot edge by the layer's
//! [`edge_pad`]) or at the band centers of a point scale, in an opaque fill or as a ring over the mark's
//! interior, sized by the point mark or by a size scale, so anything else reaching the painter is an engine bug.

use bind::bound::{self, Datum, Mark, MarkLayer, Paint, Pos, Scale, ScaleKind, Shape, SizeScale, Unfilled, ValueAxis};
use board_model::primitives::SizeScaleType;
use d3rs::scale::Transform;
use d3rs::vega_symbol::VegaSymbol;
use diagnostic::{diag, Diagnostic};
use scene::{Attrs, Node, Role};

use crate::contract::{aria_attrs, mark_group, ItemRole};
use crate::geom::Size;
use crate::plot::band_axis::BandGeometry;
use crate::plot::lines::x_edge_pad;
use crate::plot::time_axis::{day_extent, x_on_range};
use crate::plot::value_axis::fraction;
use crate::scene_path::scene_path;

/// The value axis of a linear `scale`; any other scale is an engine bug in a caller that routes it
/// to a value-axis law.
pub(crate) fn linear<'a>(scale: &'a Scale, axis: &str) -> Result<&'a ValueAxis, Diagnostic> {
    match &scale.kind {
        ScaleKind::Linear(value) => Ok(value),
        _ => Err(diag::err_internal(format!("points on a non-linear {axis} scale"))),
    }
}

/// The area a sized point of value `v` draws: its place in the transformed domain, then linear onto the
/// `style.size` range (the law of chart-renderer's `legend::SizeScale::area`). A zero-width domain maps
/// every value to the middle of the range, as d3's `normalize` does.
fn size_area(size: &SizeScale, v: f64) -> f64 {
    let (lo, hi) = size.domain;
    let transform = match size.style.r#type {
        SizeScaleType::Linear => Transform::Identity,
        SizeScaleType::Sqrt => Transform::Pow(0.5),
        SizeScaleType::Log => Transform::Log { base: 10.0 },
    };
    let at = |x: f64| transform.apply(false, x);
    let unit = if hi == lo { 0.5 } else { (at(v) - at(lo)) / (at(hi) - at(lo)) };
    let range = size.style.range;
    range.low + unit * (range.high - range.low)
}

/// The plot's point mark; [`bound::Plane::new`] holds that a plot with a point layer carries one.
fn point_style(plot: &bound::Plane) -> Result<&bound::PointStyle, Diagnostic> {
    plot.mark_style().points.as_ref().ok_or_else(|| diag::err_internal("a point layer on a plot with no point mark"))
}

/// Where a point scale of `padding` places band `slot` of the domain `keys`, on an axis `extent` px long.
fn point_at(keys: usize, padding: f64, slot: usize, extent: f64) -> f64 {
    BandGeometry::point(padding).center(keys, slot, extent)
}

/// The pixels a continuous x range keeps off each plot edge: the largest dot the layer draws and two
/// (`x_edge_pad`; with a size channel the scale's largest, `scatter.rs`: `x_edge_pad`).
pub fn edge_pad(plot: &bound::Plane) -> Result<f64, Diagnostic> {
    let [layer] = plot.layers() else {
        return Err(diag::err_internal("an edge pad of a plot that is not one layer"));
    };
    let Mark::Points { size: sizes, .. } = &layer.mark else {
        return Err(diag::err_internal(format!("an edge pad of a {:?} layer", layer.mark)));
    };
    let constant = point_style(plot)?.size;
    Ok(x_edge_pad(sizes.as_ref().map_or(constant, |sizes| size_area(sizes, sizes.domain.1))))
}

/// Where `pos` stands on the x scale of a plot `width` across, as an offset from its left edge: a number by
/// its fraction of the domain, an instant on the range `pad` px off each edge leaves.
fn x_at(scale: &Scale, pos: &Pos, pad: f64, width: f64) -> Result<f64, Diagnostic> {
    match (&scale.kind, pos) {
        (ScaleKind::Linear(axis), Pos::Num(value)) => Ok(fraction(&axis.domain, *value) * width),
        (ScaleKind::Time(domain), Pos::Time(instant)) => Ok(x_on_range(day_extent(domain, false), pad, instant.pos, width)),
        (ScaleKind::Point { domain, padding }, Pos::Band(slot)) => Ok(point_at(domain.keys().len(), *padding, *slot, width)),
        _ => Err(diag::err_internal(format!("a point at x {pos:?} on a {:?} scale", scale.kind))),
    }
}

/// Where the value `y1` stands on the y scale of a plot `height` across, as an offset from its top edge:
/// a number by its fraction of the domain, a band's index at its center.
fn y_at(scale: &Scale, y1: f64, height: f64) -> Result<f64, Diagnostic> {
    match &scale.kind {
        ScaleKind::Linear(axis) => Ok((1.0 - fraction(&axis.domain, y1)) * height),
        ScaleKind::Point { domain, padding } => Ok(point_at(domain.keys().len(), *padding, y1 as usize, height)),
        _ => Err(diag::err_internal(format!("a point at y {y1} on a {:?} scale", scale.kind))),
    }
}

/// A datum's drawn area: the size scale's, else the point mark's constant size. [`bound::Plane::new`]
/// holds that a sized layer has a size for every datum's row.
fn area_of(constant: f64, sizes: Option<&SizeScale>, datum: &Datum) -> f64 {
    sizes.map_or(constant, |sizes| size_area(sizes, sizes.by_row[datum.row.0]))
}

/// How far a point's disc reaches past the plot's left and right edges on a plot of `size`, at most each:
/// a right value axis stands off the plot by the right reach (`edge_reach` in `scatter/layout.rs`), and
/// both join the fit as mark ink. Zero for a plot without a point layer.
pub fn edge_overhang(plot: &bound::Plane, size: Size) -> Result<(f64, f64), Diagnostic> {
    let [layer] = plot.layers() else { return Ok((0.0, 0.0)) };
    let Mark::Points { size: sizes, .. } = &layer.mark else { return Ok((0.0, 0.0)) };
    let (pad, style) = (edge_pad(plot)?, point_style(plot)?);
    // `boundStroke` grows an unfilled symbol's ink by the full stroke width, a filled one's by nothing
    // (`symbol_stroke_ink`), whatever its shape: the fit reaches by the circle's radius.
    let ring = style.unfilled.as_ref().map_or(0.0, |_| style.stroke_width);
    let (mut left, mut right) = (0.0_f64, 0.0_f64);
    for datum in layer.panels.iter().flatten() {
        let radius = VegaSymbol::radius(area_of(style.size, sizes.as_ref(), datum)) + ring;
        let at = x_at(plot.x(), &datum.x, pad, size.width)?;
        left = left.max(radius - at);
        right = right.max(at + radius - size.width);
    }
    Ok((left, right))
}

/// The datums of `marks`, the plot's layer `layer`, in one panel on a plot of `size`, under the
/// plot's point mark. `group` is the mark group's name, which production numbers after the rule
/// layers drawn before it. [`Plot::new`] holds that every layer's y scale and every datum's
/// series exist, and that a plot with a point layer carries its point mark.
pub fn points(plot: &bound::Plane, layer: usize, marks: &MarkLayer, datums: &[Datum], size: Size, group: &str) -> Result<Vec<Node>, Diagnostic> {
    let Mark::Points { shape, size: sizes } = &marks.mark else {
        return Err(diag::err_internal(format!("points painting a {:?} layer", marks.mark)));
    };
    let style = point_style(plot)?;
    let pad = edge_pad(plot)?;
    let y_scale = &plot.y()[marks.y_scale];
    let single = plot.series().len() == 1;
    datums
        .iter()
        .enumerate()
        .map(|(index, datum)| {
            let Datum { x: pos, y1, series, tooltip, .. } = datum;
            let drawn = &plot.series()[*series];
            let symbol = match shape {
                Shape::Symbol(symbol) => *symbol,
                Shape::Channel(scale) => scale.of_row(datum.row),
            };
            let d = scene_path(&symbol.draw(area_of(style.size, sizes.as_ref(), datum)));
            let Paint::Solid(fill) = drawn.paint;
            if fill.3 != 1.0 {
                return Err(diag::err_internal(format!("a translucent point fill {fill:?}")));
            }
            let series_hex = dbt_charts_color::rgb01_to_hex(fill.0, fill.1, fill.2);
            let attrs = match &style.unfilled {
                // An unfilled point keeps the authored interior and rings it in the series color, or in
                // the mark's own with no color channel (`point_unfilled` in `scatter/layout.rs`).
                Some(Unfilled { interior, stroke }) => Attrs::new()
                    .set("fill", interior.as_str())
                    .set("stroke", stroke.as_deref().unwrap_or(&series_hex))
                    .num("stroke-width", style.stroke_width)
                    .set_if(miter_limit(symbol).is_some(), "stroke-miterlimit", miter_limit(symbol).unwrap_or_default()),
                // Production prints a filled point's width only when it draws one.
                None => {
                    let attrs = Attrs::new().set("fill", series_hex.as_str());
                    if style.stroke_width > 0.0 { attrs.num("stroke-width", style.stroke_width) } else { attrs }
                }
            };
            let attrs = attrs.num("opacity", style.opacity).extend(aria_attrs(Some(&tooltip.text()), ItemRole::Point).map_err(diag::err_internal)?);
            let role = Role::Mark { layer, series: (!single).then(|| drawn.display.clone()), datum: Some(index) };
            let at = (x_at(plot.x(), pos, pad, size.width)?, y_at(y_scale, *y1, size.height)?);
            mark_group("symbol", group, vec![Node::path(role, d, attrs).translate(at.0, at.1)]).map_err(diag::err_internal)
        })
        .collect()
}

/// The `stroke-miterlimit` a stroked glyph carries: Vega's 10 where a corner is sharp enough that the SVG
/// default of 4 would bevel it, which only the wedge's tip is (`scatter_shape.rs`: `miter_limit`).
pub(crate) fn miter_limit(symbol: VegaSymbol) -> Option<&'static str> {
    (symbol == VegaSymbol::Wedge).then_some("10")
}

#[cfg(test)]
mod tests {
    use super::*;
    use board_model::primitives::{AreaRange, SizeScaleStyle};

    /// The cases chart-renderer's `legend::SizeScale::area` is pinned to: the engine copy draws the same areas.
    #[test]
    fn size_area_maps_the_shared_cases() {
        let cases: serde_json::Value = serde_json::from_str(include_str!("../../../fixtures/size-scale/cases.json")).unwrap();
        for case in cases["cases"].as_array().unwrap() {
            let num = |v: &serde_json::Value| v.as_f64().unwrap();
            let range = case["range"].as_array().unwrap();
            let domain = case["domain"].as_array().unwrap();
            let r#type = match case["type"].as_str().unwrap() {
                "linear" => SizeScaleType::Linear,
                "sqrt" => SizeScaleType::Sqrt,
                _ => SizeScaleType::Log,
            };
            let size = SizeScale {
                domain: (num(&domain[0]), num(&domain[1])),
                style: SizeScaleStyle { range: AreaRange { low: num(&range[0]), high: num(&range[1]) }, r#type },
                by_row: Vec::new(),
            };
            let area = size_area(&size, num(&case["value"]));
            assert!((area - num(&case["area"])).abs() < 1e-9, "{case}: drew {area}");
        }
    }
}
