//! `style/theme/legend.py`: legend placement, entries, label and title.

use crate::rule::Auto;
use crate::string_enum;
use crate::style::font::{FontStyle, SymbolShape};
use crate::unit::Px;
use model_core::Engine;
use model_derive::Model;

string_enum!(
    /// The side of the plot (or wheel) a legend sits on.
    LegendEdge { Left = "left", Right = "right", Top = "top", Bottom = "bottom" }
);
string_enum!(
    /// A legend edge, or `endpoints` to name the series on the chart itself.
    LegendPlacementEdge {
        Left = "left",
        Right = "right",
        Top = "top",
        Bottom = "bottom",
        Endpoints = "endpoints",
    }
);
string_enum!(
    /// Position along the legend's edge.
    LegendAlign { Start = "start", Center = "center", End = "end" }
);
string_enum!(
    /// Vega-Lite legend direction.
    LegendDirection { Horizontal = "horizontal", Vertical = "vertical" }
);

/// One element (label or title) of a legend.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LegendElementStyle {
    /// Legend element font style overrides.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
    /// Padding between legend symbol and element text in pixels.
    pub padding: f64,
}

/// Legend label style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LegendLabelStyle {
    #[flatten]
    pub element: LegendElementStyle,
    /// Maximum label width in pixels; maps to VL labelLimit.
    #[model(absent = "Vega-Lite's default")]
    pub max_width: Option<f64>,
}

/// Legend title style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LegendTitleStyle {
    #[flatten]
    pub element: LegendElementStyle,
    /// Show the legend title. 'auto' shows it except on a top legend that runs
    /// horizontally; false suppresses it (VL legend.title: null).
    #[model(fill = "auto")]
    pub visible: Engine<bool, Auto>,
}

/// The legend placement leaves every cartesian and geo family shares.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PlacementLeaves {
    /// Position along the edge. 'auto' = the engine decides.
    #[model(fill = "auto")]
    pub align: Engine<LegendAlign, Auto>,
    /// True floats the legend over the plot, reserving no space; False reserves
    /// a strip for it. 'auto' = the engine decides.
    #[model(fill = "auto")]
    pub overlay: Engine<bool, Auto>,
}

/// Legend placement for families that cannot name series on the chart itself.
///
/// Scatter, heatmap, histogram and the geo families: `edge` is a side or `auto`,
/// never `endpoints`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SideLegendPositionStyle {
    #[flatten]
    pub leaves: PlacementLeaves,
    /// Side of the plot the legend sits on. 'auto' = the engine decides.
    #[model(fill = "auto")]
    pub edge: Engine<LegendEdge, Auto>,
}

/// Legend placement for line, area and bar charts, and the board-wide default.
///
/// Every leaf is `auto` on every built-in theme: the engine decides and bakes
/// the concrete placement into the resolved legend.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(forbids(align, when(is_in(edge, "endpoints"), not_in(align, "auto")),
        code = "ERR-LEGEND-ALIGN-WITH-ENDPOINTS"
    )),
    constraint(forbids(overlay, when(is_in(edge, "endpoints"), not_in(overlay, "auto")),
        code = "ERR-LEGEND-OVERLAY-WITH-ENDPOINTS"
    ))
)]
pub struct LegendPositionStyle {
    #[flatten]
    pub leaves: PlacementLeaves,
    /// Legend side, 'endpoints' for on-chart series names, or 'auto' (engine decides).
    #[model(fill = "auto")]
    pub edge: Engine<LegendPlacementEdge, Auto>,
}

/// Pie key placement: edge and align, never overlay.
///
/// A floating key would cover wedges, so a pie key always reserves its room.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PiePositionStyle {
    /// Side of the wheel the key (legend or attached table) sits on. 'auto' = the engine decides.
    pub edge: Engine<LegendEdge, Auto>,
    /// Position of the key along that edge. 'auto' = the engine decides.
    pub align: Engine<LegendAlign, Auto>,
}

/// Fixed chrome around each entry of a single-row top legend.
///
/// Constant regardless of mark size or entry count. The label text is measured
/// against the real resolved label font, never estimated.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LegendRowStyle {
    /// Swatch box width in pixels.
    pub swatch_width: Px,
    /// Gap in pixels between one entry and the next.
    pub entry_gap: Px,
}

/// Every legend field except `position`, whose shape differs per family.
#[derive(Debug, Clone, PartialEq, Model)]
// `range` takes one field.
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(range(columns, ge = 0)),
    constraint(range(compact_columns, ge = 1)),
    constraint(range(symbol_limit, ge = 1))
)]
pub struct LegendStyleBase {
    /// Legend layout direction. 'auto' infers it from the edge: horizontal on
    /// top/bottom, vertical on left/right.
    #[model(fill = "auto")]
    pub direction: Engine<LegendDirection, Auto>,
    /// Legend entry columns. Zero keeps the renderer default; positive values
    /// set Vega-Lite legend columns.
    pub columns: u32,
    /// Entry columns when a horizontal legend wraps: the automatic compact top
    /// legend, or an inferred-horizontal legend too wide for one row. At least 1.
    pub compact_columns: u32,
    /// Chrome around each entry of a single-row top legend.
    pub row: LegendRowStyle,
    /// Legend label style.
    pub label: LegendLabelStyle,
    /// Legend title style.
    pub title: LegendTitleStyle,
    /// Show the legend. 'auto' shows it unless direct labeling (endpoint labels,
    /// or a pie's slice labels) names the series instead; true keeps it beside
    /// direct labels and false hides it.
    pub visible: Engine<bool, Auto>,
    /// Maximum number of legend entries to display; maps to VL symbolLimit.
    /// A positive integer prevents overflow on high-cardinality series.
    #[model(absent = "no cap")]
    pub symbol_limit: Option<u32>,
    /// Explicit legend entry order/filter; each entry resolves against the real
    /// legend domain by its rendered text or its column/measure name
    /// (case/separator-insensitive).
    #[model(absent = "order from the data")]
    pub values: Option<Vec<String>>,
    /// Override the legend glyph shape with a Vega symbol name. 'auto' uses the
    /// mark-aware glyph derived from the chart's mark type.
    #[model(fill = "auto")]
    pub symbol_shape: Engine<SymbolShape, Auto>,
    /// When False, draws a hollow (stroke-only) legend glyph. 'auto' draws a filled symbol.
    #[model(fill = "auto")]
    pub symbol_fill: Engine<bool, Auto>,
}

/// Legend for line, area and bar charts, and the board-wide default.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LegendStyle {
    #[flatten]
    pub base: LegendStyleBase,
    /// Legend edge (or `endpoints`), align along it, overlay or reserve.
    pub position: LegendPositionStyle,
}

/// Legend for scatter, heatmap, histogram and the geo families.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SideLegendStyle {
    #[flatten]
    pub base: LegendStyleBase,
    /// Legend edge, align along it, overlay or reserve.
    pub position: SideLegendPositionStyle,
}

/// Pie legend.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PieLegendStyle {
    #[flatten]
    pub base: LegendStyleBase,
    /// Pie key placement: edge and align along it.
    pub position: PiePositionStyle,
}
