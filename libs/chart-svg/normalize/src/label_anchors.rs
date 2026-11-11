//! `validate/label_anchors.py`: `labels.visible` anchors (`first`, `last`, `min`, `max`) pick marks
//! from each series, so a scatter chart (no ordered series) and a layer (none of its own) refuse them.

use board_model::chart::layer::CartesianLayerAuthored as L;
use board_model::chart::ChartAuthored as C;
use board_model::style::marks::LabelVisible;
use diagnostic::{diag, Diagnostic};
use model_core::Tri;

/// The first of line, point and bar labels (in that order) naming anchors, as its path under `style`.
fn first(slots: [(&'static str, &Option<LabelVisible>); 3]) -> Option<&'static str> {
    slots
        .into_iter()
        .find(|(_, v)| v.as_ref().is_some_and(|v| !v.anchors().is_empty()))
        .map(|(path, _)| path)
}

const LINE: &str = "marks.line.labels.visible";
const POINT: &str = "marks.point.labels.visible";
const BAR: &str = "marks.bar.labels.visible";

fn layer_path(layer: &L) -> Option<&'static str> {
    let off = None;
    match layer {
        L::Bar(l) => match &l.style {
            Tri::Value(s) => first([
                (LINE, &off),
                (POINT, &off),
                (BAR, &s.marks.bar.labels.labels.base.visible),
            ]),
            _ => None,
        },
        L::Line(l) => match &l.style {
            Tri::Value(s) => first([
                (LINE, &s.marks.line.labels.base.visible),
                (POINT, &s.marks.point.labels.base.visible),
                (BAR, &off),
            ]),
            _ => None,
        },
        L::Area(l) => match &l.style {
            Tri::Value(s) => first([
                (LINE, &s.marks.line.labels.base.visible),
                (POINT, &s.marks.point.labels.base.visible),
                (BAR, &off),
            ]),
            _ => None,
        },
        L::Scatter(l) => match &l.style {
            Tri::Value(s) => first([
                (LINE, &off),
                (POINT, &s.marks.point.labels.base.visible),
                (BAR, &off),
            ]),
            _ => None,
        },
    }
}

/// The first anchored label under a cartesian chart's own style (a scatter chart) or a layer's,
/// as Python raises it: the chart first, then each layer in order.
pub fn label_anchor_error(id: &str, chart: &C) -> Option<Diagnostic> {
    let layers = match chart {
        C::Area(c) => &c.layers,
        C::Bar(c) => &c.layers,
        C::Line(c) => &c.layers,
        C::Scatter(c) => &c.layers,
        _ => return None,
    };
    let own = match chart {
        C::Scatter(c) => first([
            (LINE, &None),
            (POINT, &c.style.marks.point.labels.base.visible),
            (BAR, &None),
        ])
        .map(|p| (format!("charts.{id}.style.{p}"), "a scatter chart")),
        _ => None,
    };
    let (path, whom) = own.or_else(|| {
        layers.iter().flatten().enumerate().find_map(|(i, l)| {
            layer_path(l).map(|p| (format!("charts.{id}.layers[{i}].style.{p}"), "a layer"))
        })
    })?;
    Some(diag::err_labels_anchor_unsupported(id, path, whom))
}
