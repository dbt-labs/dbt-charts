//! `style/theme/layout.py`: layout containers (rows, cols, grid, tabs, details).

use crate::primitives::BorderStyle;
use crate::style::font::FontStyle;
use model_derive::Model;

/// Plot-area (view) defaults.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ViewStyle {
    /// Plot area border stroke color.
    #[model(absent = "no border")]
    pub stroke: Option<String>,
    /// Default plot width for continuous (quantitative) scales in pixels.
    pub continuous_width: f64,
    /// Default plot height for continuous (quantitative) scales in pixels.
    pub continuous_height: f64,
    /// Default plot width for discrete (ordinal/nominal) scales in pixels.
    #[model(absent = "auto size")]
    pub discrete_width: Option<f64>,
    /// Default plot height for discrete (ordinal/nominal) scales in pixels.
    #[model(absent = "auto size")]
    pub discrete_height: Option<f64>,
}

/// Gap between layout items.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LayoutGapStyle {
    /// Gap between layout items in pixels.
    pub gap: f64,
}

/// Grid layout configuration.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct GridLayoutStyle {
    /// Number of columns in the grid layout.
    pub columns: u32,
    /// Gap between grid cells in pixels.
    pub gap: f64,
}

/// Tabs layout style.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(rail_max_lines, ge = 1)))]
pub struct TabsStyle {
    /// Tab bar height in pixels.
    pub bar_height: f64,
    /// Line drawn along the tab bar and around the active tab: stroke width,
    /// color, and the radius of the active tab's top corners.
    pub border: BorderStyle,
    /// Tab label font style overrides.
    #[model(inherit_slot = "Style.font")]
    pub font: FontStyle,
    /// Minimum tab width in pixels.
    pub min_width: f64,
    /// Maximum tab width in pixels. A longer label wraps in a left rail (up to
    /// `rail_max_lines`) and is otherwise cut with an ellipsis.
    pub max_width: f64,
    /// Lines a left-rail tab label wraps to before it is cut with an ellipsis.
    pub rail_max_lines: u32,
    /// Horizontal padding between a tab's edge and its label in pixels.
    pub padding_x: f64,
    /// Font weight for the active tab label.
    pub active_weight: String,
    /// Font weight for inactive tab labels.
    pub inactive_weight: String,
    /// Vertical offset to align SVG tab label baseline in pixels.
    pub title_baseline_offset: f64,
}

/// Font style for the expand/collapse arrow glyph.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct DetailsArrowFontStyle {
    /// Font size of the arrow glyph in pixels.
    pub size: f64,
}

/// Layout and font style for the expand/collapse arrow chevron.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct DetailsArrowStyle {
    /// X position of the arrow in pixels.
    pub x: f64,
    /// Arrow glyph font style.
    pub font: DetailsArrowFontStyle,
}

/// Details (accordion) layout style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct DetailsStyle {
    /// Height of the details summary (collapsed) row in pixels.
    pub summary_height: f64,
    /// Details element border style.
    pub border: BorderStyle,
    /// Details summary font style overrides.
    #[model(inherit_slot = "Style.font")]
    pub font: FontStyle,
    /// Expand/collapse arrow glyph layout and font style.
    pub arrow: DetailsArrowStyle,
    /// X position of the details summary label text in pixels.
    pub label_x: f64,
    /// Vertical offset to align SVG details text baseline in pixels.
    pub text_baseline_offset: f64,
    /// Y offset of the expanded details content area in pixels.
    pub content_y_offset: f64,
}

/// Every layout container's style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LayoutStyle {
    /// Gap sizing between rows.
    pub rows: LayoutGapStyle,
    /// Gap sizing between columns.
    pub cols: LayoutGapStyle,
    /// Grid layout configuration.
    pub grid: GridLayoutStyle,
    /// Tabs layout style.
    pub tabs: TabsStyle,
    /// Details (accordion) layout style.
    pub details: DetailsStyle,
}
