//! `validate/layer_axes.py`: a layer's own y axis exists only once some layer on the chart pins
//! `axis_y.position`; until then a layer `axis_y.title`, `color` or grid style has nowhere to go.

use board_model::chart::layer::CartesianLayerAuthored as L;
use board_model::chart::ChartAuthored as C;
use diagnostic::{diag, Diagnostic};
use model_core::Tri;

/// The first layer y-axis title or style on a chart no layer pins a side of, as Python raises
/// it: layers in order, the title before the style.
pub fn layer_axis_error(id: &str, chart: &C) -> Option<Diagnostic> {
    let layers = match chart {
        C::Area(c) => &c.layers,
        C::Bar(c) => &c.layers,
        C::Line(c) => &c.layers,
        C::Scatter(c) => &c.layers,
        _ => return None,
    };
    let axes: Vec<_> = layers
        .iter()
        .flatten()
        .map(|l| match l {
            L::Bar(l) => &l.base.axis_y,
            L::Line(l) => &l.base.axis_y,
            L::Area(l) => &l.base.axis_y,
            L::Scatter(l) => &l.base.axis_y,
        })
        .collect();
    let pinned = axes
        .iter()
        .any(|a| matches!(a, Tri::Value(a) if matches!(a.position, Tri::Value(_))));
    if pinned {
        return None;
    }
    axes.iter().enumerate().find_map(|(i, a)| {
        let Tri::Value(a) = a else { return None };
        let path = format!("charts.{id}.layers.{i}.axis_y");
        if matches!(a.title, Tri::Value(_)) {
            return Some(diag::err_layer_axis_title_without_position(format!("{path}.title")));
        }
        let styled_grid = matches!(&a.grid, Tri::Value(g) if [
            matches!(g.color, Tri::Value(_)),
            matches!(g.width, Tri::Value(_)),
            matches!(g.dash, Tri::Value(_)),
            matches!(g.opacity, Tri::Value(_)),
        ].contains(&true));
        (matches!(a.color, Tri::Value(_)) || styled_grid)
            .then(|| diag::err_layer_axis_style_without_position(path))
    })
}
