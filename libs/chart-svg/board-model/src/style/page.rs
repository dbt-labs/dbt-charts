//! `style/theme/page.py`: page chrome (inputs, footer, timestamp).
//!
//! `clock_hides_zone` and `CLOCK_HIDES_ZONE` are not model shape: they lint a
//! timestamp format against the board zone, a pass of their own.

use crate::facet::{Color, FontWeightValue, Url};
use crate::primitives::{CornerStyle, DividerStyle, SpacingValues};
use crate::string_enum;
use crate::style::font::FontStyle;
use crate::unit::{Fraction, Px};
use model_derive::Model;

string_enum!(
    /// Where the timestamp row sits.
    TimestampPosition { Top = "top", Footer = "footer" }
);
string_enum!(
    /// Timestamp horizontal alignment within its row.
    TimestampAlign { Left = "left", Right = "right" }
);

/// Default width per input type.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct InputWidths {
    /// Default width for text inputs in pixels.
    pub text: f64,
    /// Default width for number inputs in pixels.
    pub number: f64,
    /// Default width for range inputs in pixels.
    pub range: f64,
    /// Minimum width for slider value display in pixels.
    pub slider_value_min: f64,
    /// Default width for checkbox inputs in pixels.
    pub checkbox: f64,
    /// Default width for daterange chip triggers in pixels.
    pub daterange: f64,
}

/// Range input defaults.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct RangeDefaults {
    /// Default minimum value for range inputs.
    pub default_min: f64,
    /// Default maximum value for range inputs.
    pub default_max: f64,
    /// Default step size for range inputs.
    pub default_step: f64,
}

/// Input control style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct InputStyle {
    /// Input control height in pixels.
    pub height: f64,
    /// Corner rounding for input controls.
    pub border: CornerStyle,
    /// Input focus ring color.
    #[model(inherit = "Style.accent")]
    pub focus_color: Color,
    /// Input background color.
    pub background: Color,
    /// Fill of the selected segment in a button row. Unset draws a light tint
    /// of the label ink over the input background.
    #[model(absent = "a light tint of the label ink")]
    pub selected_background: Option<Color>,
    /// Text color of the selected segment in a button row.
    #[model(inherit = "Style.variables.value.font.color")]
    pub selected_color: Color,
    /// Opacity of the label-ink tint behind the selected segment of a button
    /// row; unused when a selected fill is set.
    pub selected_tint: Fraction,
    /// Font weight of the selected segment in a button row.
    pub selected_weight: FontWeightValue,
    /// Input inner padding in pixels.
    pub padding: SpacingValues,
    /// Per-input-type default widths.
    pub widths: InputWidths,
    /// Range input default min/max/step values.
    pub range: RangeDefaults,
}

/// The footer hairline, plus the gap it reserves above the footer text.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct FooterDividerStyle {
    #[flatten]
    pub divider: DividerStyle,
    /// Vertical gap between the footer text and its divider.
    pub gap: Px,
}

/// Page footer chrome: visibility, attribution text, font and divider.
///
/// The renderer writes font size and fill straight into SVG, so both are
/// required unless a named font (`extends`) supplies them.
#[derive(Debug, Clone, PartialEq, Model)]
// `requires` takes one field.
#[allow(clippy::duplicated_attributes)]
#[model(constraint(
    requires("font.size", when(is_in("font.extends", null)),
        message = "style.footer.font.size is required (float)"
    ),
    requires("font.color", when(is_in("font.extends", null)),
        message = "style.footer.font.color is required (color string)"
    ),
    value_in(brand_weight, 500, 600),
))]
pub struct FooterStyle {
    /// Show the footer attribution line.
    pub visible: bool,
    /// Attribution text shown in the footer; `auto` takes the board language's
    /// wording around the dbt Charts brand.
    pub text: String,
    /// URL the footer brand phrase 'dbt charts' links to; null renders plain text.
    #[model(absent = "no link")]
    pub link: Option<Url>,
    /// Footer text font style (size and color required).
    #[model(inherit_slot = "Style.font")]
    pub font: FontStyle,
    /// Vertical offset from bottom edge in pixels.
    pub y_offset: f64,
    /// Font weight of the footer brand phrase. Only 500 and 600 have a static
    /// font face to swap in for PNG and PDF export.
    pub brand_weight: u32,
    /// Hairline above the footer text.
    pub divider: FooterDividerStyle,
}

/// Data-freshness chrome: visibility, placement, strftime format, font and y-offset.
#[derive(Debug, Clone, PartialEq, Model)]
// `requires` takes one field.
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(requires("font.size", when(is_in("font.extends", null)),
        message = "style.timestamp.font.size is required (float)"
    )),
    constraint(requires("font.color", when(is_in("font.extends", null)),
        message = "style.timestamp.font.color is required (color string)"
    ))
)]
pub struct TimestampStyle {
    /// Show the data-freshness line.
    pub visible: bool,
    /// Timestamp row: top page chrome or footer baseline.
    pub position: TimestampPosition,
    /// Timestamp horizontal alignment within its row.
    pub align: TimestampAlign,
    /// `auto` takes the board language's wording around the time and date; else
    /// a strftime format for the data-freshness line, including any literal
    /// label text. The value prints in `regional.time_zone`; a format that
    /// prints a clock must show that zone (%Z, %z, or a literal 'UTC' on a UTC
    /// board), else compile rejects it.
    pub format: String,
    /// Y-coordinate for top-positioned timestamp in pixels.
    pub y: f64,
    /// Horizontal gap between the footer timestamp and the attribution.
    pub gap: Px,
    /// Timestamp font style overrides (size, color, weight, ...).
    #[model(inherit_slot = "Style.font")]
    pub font: FontStyle,
}
