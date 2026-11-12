//! The reference rules a value axis draws across the plot, as production paints them after the
//! layers' marks: today the zero rule (the grid's heavy threshold line at zero), a mark set of its
//! own named for the layer count. The cartesian frame calls it; a family earns it by a twin.

use bind::bound::Plane;
use diagnostic::{diag, Diagnostic};
use scene::{Attrs, Node, Role};

use crate::contract::hidden_mark_group;
use crate::geom::Size;
use crate::plot::points::linear;
use crate::plot::value_axis::fraction;

/// The heavy rule at zero across the plot, a mark set of its own after the layers' (`layer_N_marks`).
pub(crate) fn zero_rule(plot: &Plane, size: Size) -> Result<Node, Diagnostic> {
    let Some(line) = &plot.axis_styles().y[0].threshold else {
        return Err(diag::err_internal("a zero rule fires on a value axis with no threshold style"));
    };
    if line.dash.is_some() || line.opacity != 1.0 {
        return Err(diag::err_internal("a zero rule is solid and opaque: the threshold style has no dash or opacity"));
    }
    let domain = &linear(&plot.y()[0], "y")?.domain;
    let at = (1.0 - fraction(domain, 0.0)) * size.height;
    let attrs = Attrs::new().set("stroke", line.color.as_str()).num("stroke-width", line.width);
    let name = format!("layer_{}_marks", plot.layers().len());
    Ok(hidden_mark_group("rule", &name, vec![Node::line(Role::Rule, (0.0, at), (size.width, at), attrs)]))
}
