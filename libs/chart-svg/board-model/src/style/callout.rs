//! `style/theme/callout.py`: the callout chart family.

use crate::facet::Color;
use crate::primitives::{BorderStyle, Tone};
use crate::style::board::PaddingStyle;
use crate::style::font::FontStyle;
use model_derive::Model;

/// Font + y_offset for a callout title or message.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct CalloutElementStyle {
    /// Element font style overrides. A color of `by_tone` is the tone's color.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
    /// Vertical offset from the element's anchor in pixels.
    pub y_offset: f64,
}

/// Chart-family style for `type: callout` charts and runtime chart-error fallback cards.
///
/// The theme sets background, border color, and title and message font color to
/// `by_tone`; at resolve time each takes its `{tone}.*` palette role (bg,
/// border, solid, text), and any color a board or chart authors wins instead.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct CalloutChartStyle {
    /// Preferred callout width in pixels.
    pub preferred_width: f64,
    /// Semantic tone for palette-role lookup (info | positive | negative | warning).
    pub tone: Tone,
    /// Callout card background color. `by_tone` is the tone's color.
    pub background: Color,
    /// Callout card border style. A color of `by_tone` is the tone's color.
    pub border: BorderStyle,
    /// Padding as {top, right, bottom, left} in pixels, never one number.
    #[model(inherit_slot = "Style.charts.padding")]
    pub padding: PaddingStyle,
    /// Vertical gap between callout title and message in pixels.
    pub section_gap: f64,
    /// Callout title element style.
    pub title: CalloutElementStyle,
    /// Callout message element style.
    pub message: CalloutElementStyle,
}
