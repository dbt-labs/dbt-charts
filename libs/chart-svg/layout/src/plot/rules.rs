//! The reference rules a plot draws across its marks as production paints them (`BaselineFeature`,
//! `baseline.py`): the heavy rule at zero on the value axis and the one at zero on a linear x axis,
//! each a mark set of its own named for its layer. The cartesian frame calls it.
//!
//! A bar paints its rule after the layers' marks, numbered past them. A point or line paints it as an
//! underlay, before the marks (`_insert_rule` routes it to `spec.underlays`), numbered from 0 in the
//! order the rules fire: the y rule, then the x rule. The unity rule is not ported: it needs a
//! percent axis format, which bind refuses, so bind never fires it.

use bind::bound::{Line, Mark, Plane};
use diagnostic::{diag, Diagnostic};
use scene::{Attrs, Node, Role};

use crate::contract::hidden_mark_group;
use crate::geom::{Insets, Size};
use crate::plot::points::linear;
use crate::plot::tear::snap;
use crate::plot::value_axis::fraction;

/// A rule's position along the axis it crosses and its stroke.
struct Rule {
    at: f64,
    color: String,
    width: f64,
}

/// The rules a plot draws, placed on a plot of a known size.
pub(crate) struct Rules {
    y: Option<Rule>,
    x: Option<Rule>,
    under: bool,
    first_layer: usize,
}

/// The threshold style production paints a rule in: solid and opaque.
fn stroke(style: &Option<Line>) -> Result<(String, f64), Diagnostic> {
    let Some(line) = style else {
        return Err(diag::err_internal("a baseline rule fires on an axis with no threshold style"));
    };
    if line.dash.is_some() || line.opacity != 1.0 {
        return Err(diag::err_internal("a baseline rule is solid and opaque: the threshold style has no dash or opacity"));
    }
    Ok((line.color.clone(), line.width))
}

/// The mark group name of a point layer: production numbers it after the rule layers painted before
/// it, and leaves the unlayered spec's default name when none fired.
pub fn points_group(plot: &Plane) -> String {
    match usize::from(plot.baselines().zero) + usize::from(plot.baselines().x_threshold) {
        0 => "marks".to_string(),
        rules => format!("layer_{rules}_marks"),
    }
}

impl Rules {
    pub(crate) fn of(plot: &Plane, size: Size) -> Result<Self, Diagnostic> {
        let styles = plot.axis_styles();
        let y = plot
            .baselines()
            .zero
            .then(|| {
                let (color, width) = stroke(&styles.y[0].threshold)?;
                let domain = &linear(&plot.y()[0], "y")?.domain;
                Ok::<_, Diagnostic>(Rule { at: (1.0 - fraction(domain, 0.0)) * size.height, color, width })
            })
            .transpose()?;
        let x = plot
            .baselines()
            .x_threshold
            .then(|| {
                let (color, width) = stroke(&styles.x.threshold)?;
                let domain = &linear(plot.x(), "x")?.domain;
                Ok::<_, Diagnostic>(Rule { at: fraction(domain, 0.0) * size.width, color, width })
            })
            .transpose()?;
        let under = plot.layers().iter().all(|layer| layer.mark != Mark::Bars);
        Ok(Self { y, x, under, first_layer: if under { 0 } else { plot.layers().len() } })
    }

    /// Whether the rules paint before the marks (an underlay) rather than after them.
    pub(crate) fn under(&self) -> bool {
        self.under
    }

    /// How far the rules' ink reaches past a plot of `size`: each is a stroke the plot's full width
    /// or height, its extent grown by the stroke's full width on every side (`Bounds.expand`).
    pub(crate) fn ink(&self, size: Size) -> Insets {
        let lines = self
            .y
            .iter()
            .map(|rule| ((0.0, rule.at), (size.width, rule.at), rule.width))
            .chain(self.x.iter().map(|rule| ((rule.at, 0.0), (rule.at, size.height), rule.width)));
        lines.fold(Insets::ZERO, |reach, ((x1, y1), (x2, y2), width)| Insets {
            top: reach.top.max(width - y1.min(y2)),
            right: reach.right.max(x1.max(x2) + width - size.width),
            bottom: reach.bottom.max(y1.max(y2) + width - size.height),
            left: reach.left.max(width - x1.min(x2)),
        })
    }

    /// The stroke of a horizontal rule standing on the plot's bottom edge, which the x axis's tick
    /// marks then take (`rule_on_edge`, `common.rs`).
    pub(crate) fn floor_color(&self, plot_height: f64) -> Option<&str> {
        self.y.as_ref().filter(|rule| (rule.at - plot_height).abs() < 0.5).map(|rule| rule.color.as_str())
    }

    /// The vertical rule's `(position, stroke, width)` when the x tick mark snapped to `stop` stands at
    /// its value: that tick is the rule continued past the plot edge (`tick_look`, `common.rs`).
    pub(crate) fn tick_look(&self, stop: f64) -> Option<(f64, &str, f64)> {
        self.x.as_ref().filter(|rule| (snap(rule.at) - stop).abs() < 1e-6).map(|rule| (rule.at, rule.color.as_str(), rule.width))
    }

    /// The rule mark sets in paint order, the horizontal rule spanning `floor` across the plot.
    pub(crate) fn nodes(&self, size: Size, floor: (f64, f64)) -> Vec<Node> {
        let horizontal = self.y.iter().map(|rule| ((floor.0, rule.at), (floor.1, rule.at), rule));
        let vertical = self.x.iter().map(|rule| ((rule.at, 0.0), (rule.at, size.height), rule));
        horizontal
            .chain(vertical)
            .enumerate()
            .map(|(index, (from, to, rule))| {
                let attrs = Attrs::new().set("stroke", rule.color.as_str()).num("stroke-width", rule.width);
                hidden_mark_group("rule", &format!("layer_{}_marks", self.first_layer + index), vec![Node::line(Role::Rule, from, to, attrs)])
            })
            .collect()
    }
}

/// The span a horizontal rule on the floor paints across a plot `width` wide: it covers the outermost
/// stroke of every x tick mark and vertical gridline standing at `stops` (their snapped positions),
/// never shorter than the plot (`floor_rule_span`, `common.rs`). `grid` and `ticks` are those strokes'
/// widths where they draw. Production joins the span to the value axis's tick marks and label edges
/// too; the engine's y axis has neither (it refuses y tick marks and labels above their gridline).
pub(crate) fn floor_span(width: f64, grid: Option<f64>, ticks: Option<f64>, stops: impl IntoIterator<Item = f64>) -> (f64, f64) {
    let Some(stroke) = grid.into_iter().chain(ticks).reduce(f64::max) else {
        return (0.0, width);
    };
    stops.into_iter().fold((0.0, width), |(lo, hi), stop| (lo.min(stop - stroke / 2.0), hi.max(stop + stroke / 2.0)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rule(at: f64, width: f64) -> Rule {
        Rule { at, color: "#333".to_string(), width }
    }

    fn rules(y: Option<Rule>, x: Option<Rule>, under: bool) -> Rules {
        Rules { y, x, under, first_layer: if under { 0 } else { 2 } }
    }

    const SIZE: Size = Size { width: 100.0, height: 50.0 };

    /// The floor span is the plot, widened only by a tick or gridline stroke that overhangs it.
    #[test]
    fn the_floor_span_covers_the_outermost_stroke_and_never_less_than_the_plot() {
        assert_eq!(floor_span(100.0, None, None, [0.5, 99.5]), (0.0, 100.0));
        assert_eq!(floor_span(100.0, Some(1.0), None, [10.5]), (0.0, 100.0));
        assert_eq!(floor_span(100.0, Some(1.0), Some(3.0), [0.5, 100.5]), (-1.0, 102.0));
        assert_eq!(floor_span(100.0, None, Some(2.0), []), (0.0, 100.0));
    }

    /// A rule's ink is its stroke width past each edge it reaches: the horizontal rule on the bottom
    /// edge grows the bottom and both sides, an interior one only the sides.
    #[test]
    fn a_rule_s_ink_reaches_its_stroke_past_every_edge_it_touches() {
        let on_edge = rules(Some(rule(50.0, 2.0)), None, true).ink(SIZE);
        assert_eq!((on_edge.top, on_edge.right, on_edge.bottom, on_edge.left), (0.0, 2.0, 2.0, 2.0));
        let inside = rules(Some(rule(20.0, 2.0)), None, true).ink(SIZE);
        assert_eq!((inside.top, inside.right, inside.bottom, inside.left), (0.0, 2.0, 0.0, 2.0));
        let vertical = rules(None, Some(rule(30.0, 3.0)), true).ink(SIZE);
        assert_eq!((vertical.top, vertical.right, vertical.bottom, vertical.left), (3.0, 0.0, 3.0, 0.0));
    }

    /// Only a horizontal rule standing on the bottom edge gives its stroke to the x tick marks.
    #[test]
    fn only_a_rule_on_the_bottom_edge_colors_the_floor() {
        let floor = rules(Some(rule(50.0, 2.0)), None, true);
        assert_eq!(floor.floor_color(50.0), Some("#333"));
        assert_eq!(rules(Some(rule(20.0, 2.0)), None, true).floor_color(50.0), None);
        assert_eq!(rules(None, None, true).floor_color(50.0), None);
    }

    /// The tick snapped to a vertical rule's value is that rule continued: the rule's own position and
    /// stroke replace the tick's; any other tick is left to the axis.
    #[test]
    fn the_tick_at_a_vertical_rule_takes_the_rules_look() {
        let vertical = rules(None, Some(rule(30.2, 3.0)), true);
        assert_eq!(vertical.tick_look(30.5), Some((30.2, "#333", 3.0)));
        assert_eq!(vertical.tick_look(31.5), None);
        assert_eq!(rules(None, None, true).tick_look(30.5), None);
    }

    /// The y rule paints first, then the x rule, each a mark set numbered from the first layer it
    /// owns: from 0 as an underlay, past the marks' layers after them.
    #[test]
    fn the_rules_paint_the_horizontal_first_and_number_from_their_first_layer() {
        let names = |rules: Rules| -> Vec<String> { rules.nodes(SIZE, (0.0, 100.0)).iter().map(|node| format!("{:?}", node.attrs.get("class"))).collect() };
        let under = names(rules(Some(rule(20.0, 2.0)), Some(rule(30.0, 3.0)), true));
        assert!(under[0].contains("layer_0_marks") && under[1].contains("layer_1_marks"), "{under:?}");
        let over = names(rules(Some(rule(20.0, 2.0)), Some(rule(30.0, 3.0)), false));
        assert!(over[0].contains("layer_2_marks") && over[1].contains("layer_3_marks"), "{over:?}");
    }
}
