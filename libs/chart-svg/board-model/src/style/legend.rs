//! `style/theme/legend.py`: legend placement, entries, label and title.

use crate::facet::Color;
use crate::primitives::BorderStyle;
use crate::rule::Auto;
use crate::string_enum;
use crate::style::font::{FontStyle, SymbolShape};
use crate::unit::{Fraction, Px};
use model_core::Engine;
use model_derive::Model;

string_enum!(
    /// The side of the plot (or wheel) a legend sits on.
    LegendEdge { Left = "left", Right = "right", Top = "top", Bottom = "bottom" }
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
    /// Width in pixels of an outline in the legend's background color behind each label; 0
    /// draws none.
    pub halo: Px,
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

/// Legend placement: edge, align along it, overlay or reserve.
///
/// Every leaf is `auto` on every built-in theme: the engine decides and bakes the
/// concrete placement into the resolved legend.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LegendPositionStyle {
    #[flatten]
    pub leaves: PlacementLeaves,
    /// Side of the plot the legend sits on. 'auto' = the engine decides.
    #[model(fill = "auto")]
    pub edge: Engine<LegendEdge, Auto>,
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

/// The glyph each legend entry draws.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(size, gt = 0)))]
pub struct LegendSymbolStyle {
    /// Override the legend glyph shape with a Vega symbol name. 'auto' uses the
    /// mark-aware glyph derived from the chart's mark type.
    #[model(fill = "auto")]
    pub shape: Engine<SymbolShape, Auto>,
    /// When False, draws a hollow (stroke-only) legend glyph. 'auto' draws a filled symbol.
    #[model(fill = "auto")]
    pub fill: Engine<bool, Auto>,
    /// Width of the glyph box in pixels. A line's stroke glyph is twice as long.
    pub size: f64,
}

/// Every legend field except `position`, whose shape differs per family.
#[derive(Debug, Clone, PartialEq, Model)]
// `range` takes one field.
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(range(columns, ge = 0)),
    constraint(range(compact_columns, ge = 1)),
    constraint(range(limit, ge = 1))
)]
pub struct LegendStyleBase {
    /// Legend layout direction. 'auto' infers it from the edge: horizontal on
    /// top/bottom, vertical on left/right.
    #[model(fill = "auto")]
    pub direction: Engine<LegendDirection, Auto>,
    /// Legend entry columns. Zero keeps the renderer default; a positive value
    /// sets the columns, and a bar's top or bottom legend takes fewer when its
    /// labels would not fit.
    pub columns: u32,
    /// Most entry columns when a horizontal legend wraps (the automatic compact
    /// top legend, or an inferred-horizontal legend too wide for one row); it
    /// takes fewer when its labels would not fit. At least 1.
    pub compact_columns: u32,
    /// Gap in pixels between one entry and the next: along a row, and between grid columns.
    pub entry_gap: Px,
    /// Legend glyph style.
    pub symbol: LegendSymbolStyle,
    /// Fill behind the legend; the canvas the chart paints on by default.
    pub background: Color,
    /// Background fill opacity, 0 to 1.
    pub opacity: Fraction,
    /// Space in pixels between the entries and the background's edge. 'auto' is 5 while the
    /// legend floats over the plot (position.overlay) and 0 otherwise; a number applies to every
    /// legend.
    #[model(fill = "auto")]
    pub padding: Engine<Px, Auto>,
    /// Legend border: width, color, radius, dash_array, line_cap.
    pub border: BorderStyle,
    /// Legend label style.
    pub label: LegendLabelStyle,
    /// Legend title style.
    pub title: LegendTitleStyle,
    /// Maximum number of legend entries to display; maps to VL symbolLimit.
    /// A positive integer prevents overflow on high-cardinality series.
    #[model(absent = "no cap")]
    pub limit: Option<u32>,
    /// Explicit legend entry order/filter; each entry resolves against the real
    /// legend domain by its rendered text or its column/measure name
    /// (case/separator-insensitive).
    #[model(absent = "order from the data")]
    pub values: Option<Vec<String>>,
}

/// Legend style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LegendStyle {
    #[flatten]
    pub base: LegendStyleBase,
    /// Legend edge, align along it, overlay or reserve.
    pub position: LegendPositionStyle,
}

/// A pie's key legend is its attached table: `position` places it, nothing else styles it.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PieLegendStyle {
    /// Pie key placement: edge and align along it.
    pub position: PiePositionStyle,
}
