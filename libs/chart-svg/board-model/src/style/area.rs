//! `style/theme/area.py`: the area chart family.

use crate::primitives::{PatternStyle, StrokeStyle};
use crate::style::bar::{StackMode, StackOrder};
use crate::style::chart_base::{
    AnnotatedChartStyleMixin, CartesianChartStyle, QuantitativeAxisChartStyleMixin,
};
use crate::style::endpoint_labels::EndpointLabelsConfig;
use crate::style::marks::{AreaMarkStyle, PointLabelsStyle, PointMarkStyle};
use model_derive::Model;

/// Stroke/halo/labels for an area chart's top-edge line and value labels.
///
/// The edge is a separate line mark, so its stroke geometry belongs here and not on
/// `AreaMarkStyle` (fill only). Deliberately not the full `LineMarkStyle`: no `curve` or
/// `connect`. Curve is one shared value driving both the fill and this edge (they trace one
/// silhouette) and lives solely on `AreaMarkStyle.curve`; a second `curve` here would silently
/// do nothing.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AreaLineStyle {
    /// Area top-edge line stroke style.
    pub stroke: StrokeStyle,
    /// Halo stroke width multiplier relative to stroke.width; 0 disables the halo.
    pub halo_multiplier: f64,
    /// Value label style for the area's plotted points; inherits from global.
    pub labels: PointLabelsStyle,
}

/// Area-family mark overrides.
///
/// `area` owns fill (opacity, curve, stacked recipe). `line` owns the top-edge line's
/// stroke/halo/labels. `point` overlays real point markers at each plotted value (size 0 by
/// default: invisible unless authored). No `text`: no text-mark authored surface exists for area.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AreaChartMarksStyle {
    /// Area fill mark overrides; inherits from global.
    pub area: AreaMarkStyle,
    /// Top-edge line stroke/halo/label overrides; inherits from global.
    pub line: AreaLineStyle,
    /// Point-overlay mark overrides; inherits from global.
    pub point: PointMarkStyle,
}

/// Required wrapper for area-layer mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(complete)]
pub struct AreaLayerStyle {
    /// Mark overrides for this area layer.
    pub marks: AreaChartMarksStyle,
}

/// Area chart style: chart-level fields + marks sub-block.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AreaChartStyle {
    #[flatten]
    pub annotated: AnnotatedChartStyleMixin,
    #[flatten]
    pub quantitative: QuantitativeAxisChartStyleMixin,
    #[flatten]
    pub cartesian: CartesianChartStyle,
    /// Default stack mode for area charts: 'none', 'zero', 'normalize', or 'center'.
    pub stack: StackMode,
    /// Z-order of stacked segments. Unset or 'value' puts the largest aggregate at baseline. 'data' stacks series in first-appearance order: SQL row order for color:, the listed order for y: [a, b, ...] (a measure null in the first rows appears later). 'alphabetical' sorts by series name. Ignored when stacking is off or there is only one series.
    #[model(absent = "ranks by value")]
    pub stack_order: Option<StackOrder>,
    /// Series names printed on the bands instead of in a legend.
    pub endpoint_labels: EndpointLabelsConfig,
    /// How the `pattern:` channel paints fills.
    pub pattern: PatternStyle,
    /// Area-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: AreaChartMarksStyle,
}
