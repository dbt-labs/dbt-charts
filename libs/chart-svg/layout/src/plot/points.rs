//! A point layer's marks in plot-local coordinates: one Vega symbol per datum, each in its own
//! symbol mark group as production paints it (`scatter/layout.rs`: `point_glyph`, `mark_group`).
//! Bind draws points only as constant-size circles at numbers on linear scales, in an opaque
//! fill, so anything else reaching the painter is an engine bug.

use bind::model::{Datum, Mark, MarkLayer, Paint, Plot, Pos, Scale, ScaleKind, Shape, ValueDomain};
use d3rs::vega_symbol::VegaSymbol;
use diagnostic::{diag, Diagnostic};
use scene::{Attrs, Node, Role};

use crate::contract::{aria_attrs, mark_group, ItemRole};
use crate::geom::Size;
use crate::plot::value_axis::fraction;
use crate::scene_path::scene_path;

fn linear<'a>(scale: &'a Scale, axis: &str) -> Result<&'a ValueDomain, Diagnostic> {
    match &scale.kind {
        ScaleKind::Linear(value) => Ok(&value.domain),
        _ => Err(diag::err_internal(format!("points on a non-linear {axis} scale"))),
    }
}

/// The datums of `marks`, the plot's layer `layer`, in one panel on a plot of `size`, under the
/// plot's point mark. `group` is the mark group's name, which production numbers after the rule
/// layers drawn before it. [`Plot::new`] holds that every layer's y scale and every datum's
/// series exist, and that a plot with a point layer carries its point mark.
pub fn points(plot: &Plot, layer: usize, marks: &MarkLayer, datums: &[Datum], size: Size, group: &str) -> Result<Vec<Node>, Diagnostic> {
    let Mark::Points { shape: Shape::Circle, size: None } = marks.mark else {
        return Err(diag::err_internal(format!("points painting a {:?} layer", marks.mark)));
    };
    let Some(style) = &plot.mark_style().points else {
        return Err(diag::err_internal("a point layer on a plot with no point mark"));
    };
    let x = linear(plot.x(), "x")?;
    let y = linear(&plot.y()[marks.y_scale], "y")?;
    let d = scene_path(&VegaSymbol::Circle.draw(style.size));
    let single = plot.series().len() == 1;
    datums
        .iter()
        .enumerate()
        .map(|(index, datum)| {
            let Datum { x: Pos::Num(value), y1, series, tooltip, .. } = datum else {
                return Err(diag::err_internal(format!("a point at x {:?}", datum.x)));
            };
            let drawn = &plot.series()[*series];
            let Paint::Solid(fill) = drawn.paint;
            if fill.3 != 1.0 {
                return Err(diag::err_internal(format!("a translucent point fill {fill:?}")));
            }
            let attrs = Attrs::new().set("fill", dbt_charts_color::rgb01_to_hex(fill.0, fill.1, fill.2));
            // Production prints a filled point's width only when it draws one.
            let attrs = if style.stroke_width > 0.0 { attrs.num("stroke-width", style.stroke_width) } else { attrs };
            let attrs = attrs.num("opacity", style.opacity).extend(aria_attrs(Some(&tooltip.text()), ItemRole::Point).map_err(diag::err_internal)?);
            let role = Role::Mark { layer, series: (!single).then(|| drawn.display.clone()), datum: Some(index) };
            let at = (fraction(x, *value) * size.width, (1.0 - fraction(y, *y1)) * size.height);
            mark_group("symbol", group, vec![Node::path(role, d.clone(), attrs).translate(at.0, at.1)]).map_err(diag::err_internal)
        })
        .collect()
}
