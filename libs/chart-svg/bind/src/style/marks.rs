//! A layer's mark slot: the layer's own `style.marks.<mark>` over the mark of its parent
//! chart family (`_layers.py`: `merge_unset(parent.marks.line, layer.marks.line)`).
//! Normalize already folded the chart's and the family's marks into the parent's total,
//! so the list has two rungs. The parent is the chart's own `style.marks` when the layer
//! is of the chart's family, else the board's `charts.<family>.marks`.

use board_model::chart::layer::CartesianLayer;
use board_model::chart::Chart;
use board_model::style::area::AreaChartMarksStyle;
use board_model::style::bar::BarChartMarksStyle;
use board_model::style::charts::ChartsStyle;
use board_model::style::line::LineChartMarksStyle;
use board_model::style::scatter::ScatterChartMarksStyle;
use model_core::{Leaf, Patch};

use crate::walk::{Ladder, Rung};

/// The marks of the family a layer draws, as its parent holds them.
pub enum Parent<'a> {
    Bar(&'a BarChartMarksStyle),
    Line(&'a LineChartMarksStyle),
    Area(&'a AreaChartMarksStyle),
    Scatter(&'a ScatterChartMarksStyle),
}

/// The marks `layer` is styled over.
pub fn layer_parent<'a>(
    layer: &CartesianLayer,
    chart: &'a Chart,
    charts: &'a ChartsStyle,
) -> Parent<'a> {
    match layer {
        CartesianLayer::Bar(_) => Parent::Bar(match chart {
            Chart::Bar(c) => &c.style.marks,
            _ => &charts.bar.marks,
        }),
        CartesianLayer::Line(_) => Parent::Line(match chart {
            Chart::Line(c) => &c.style.marks,
            _ => &charts.line.marks,
        }),
        CartesianLayer::Area(_) => Parent::Area(match chart {
            Chart::Area(c) => &c.style.marks,
            _ => &charts.area.marks,
        }),
        CartesianLayer::Scatter(_) => Parent::Scatter(match chart {
            Chart::Scatter(c) => &c.style.marks,
            _ => &charts.scatter.marks,
        }),
    }
}

/// One mark's list: `layer` is the layer's patch of that mark, `parent` the parent's mark.
pub fn mark_ladder<'a, M: Leaf>(layer: Option<&'a Patch<M>>, parent: &'a M) -> Ladder<Rung<'a, M>> {
    Rung::ladder([layer], parent)
}
