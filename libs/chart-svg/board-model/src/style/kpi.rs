//! `style/theme/kpi.py`: the KPI chart family and the tone palette it shares with the table.
//!
//! - Every KPI text slot's font is [`SingleLineFontStyle`]: a KPI paints one line.
//! - `KpiSupportFontStyle` is a copy of it whose `color` also takes `auto`: a flatten
//!   cannot retype a field.
//! - A `None` that only meant "not authored" is a total field (`border`, `label.overflow`);
//!   one that is a state is `Option<T>` with `absent` (`format`, `align`).

use crate::facet::{Color, FontFamily, FontWeightValue};
use crate::primitives::{BorderStyle, FormatSpec, SpacingValues, Tone};
use crate::rule::Auto;
use crate::string_enum;
use crate::style::board::OverflowMode;
use crate::style::chart_base::ChartStyleBase;
use crate::style::font::{FontSlant, LetterCase, SingleLineFontStyle, TextDecoration};
use model_core::Engine;
use model_derive::Model;

string_enum!(
    /// Card layout of a KPI.
    KpiVariant { Stacked = "stacked", Inline = "inline", Compact = "compact" }
);
string_enum!(
    /// Horizontal alignment of the text column within a KPI card.
    KpiAlign { Left = "left", Center = "center", Right = "right" }
);

/// A KPI text slot styled by its font alone (affix, headline glyph).
#[derive(Debug, Clone, PartialEq, Model)]
pub struct KpiTextSlotStyle {
    /// Slot font.
    #[model(
        inherit_slot = "Style.charts.kpi.font",
        links(color = "Style.charts.kpi.value.font.color")
    )]
    pub font: SingleLineFontStyle,
}

/// The glyphs a block's `good` direction draws.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct KpiGlyphSet {
    /// Glyph drawn when a block with `good` has a positive value.
    #[model(absent = "no glyph")]
    pub up: Option<String>,
    /// Glyph drawn when a block with `good` has a negative value.
    #[model(absent = "no glyph")]
    pub down: Option<String>,
    /// Glyph drawn when a block with `good` has a value of exactly zero.
    #[model(absent = "no glyph")]
    pub neutral: Option<String>,
}

/// The headline glyph slot: its font and the glyphs `good` draws.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct KpiValueGlyphStyle {
    #[flatten]
    pub glyphs: KpiGlyphSet,
    #[flatten]
    pub slot: KpiTextSlotStyle,
    /// Font for the glyphs `good` draws; an authored `glyph` keeps `font`.
    pub arrow: KpiTextSlotStyle,
}

/// When an unformatted KPI headline compacts, and to what.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(threshold, gt = 0)))]
pub struct KpiCompactStyle {
    /// D3 format spec the headline compacts to.
    pub spec: String,
    /// Smallest absolute value that compacts; below it, exact digits.
    pub threshold: f64,
}

/// KPI headline value slot: font, format, and the affix and glyph beside it.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct KpiValueStyle {
    /// Headline value font.
    #[model(inherit_slot = "Style.charts.kpi.font")]
    pub font: SingleLineFontStyle,
    /// Format for the KPI headline value: D3 format string or preset name. A date value defaults to date_short when unformatted.
    #[model(absent = "inferred from the headline value")]
    pub format: Option<FormatSpec>,
    /// Compaction applied to an unformatted headline number.
    pub compact: KpiCompactStyle,
    /// Currency or percent sign written around the headline number.
    pub affix: KpiTextSlotStyle,
    /// Indicator glyph drawn before the headline value.
    pub glyph: KpiValueGlyphStyle,
}

/// KPI label slot: font and how a long label fits the card.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct KpiLabelStyle {
    /// Label font.
    #[model(inherit_slot = "Style.charts.kpi.font")]
    pub font: SingleLineFontStyle,
    /// How a label too wide for the card is fit.
    #[model(inherit = "Style.charts.title.overflow")]
    pub overflow: OverflowMode,
}

/// The support row and glyph font. `color` also takes `auto`.
///
/// A copy of `SingleLineFontStyle` with one retyped field: a flatten cannot override.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "extends", constraint(range(size, gt = 0)))]
pub struct KpiSupportFontStyle {
    /// Name of a `style.fonts` entry this slot starts from.
    #[model(absent = "no named font")]
    pub extends: Option<String>,
    /// Font family or CSS stack.
    pub family: FontFamily,
    /// Text color. 'auto' is the support tone's color, or the muted ink when the row has no tone.
    pub color: Engine<Color, Auto>,
    /// Font size in pixels.
    pub size: f64,
    /// How heavy the type is drawn (e.g., 'bold', 400, 700).
    pub weight: FontWeightValue,
    /// Upright or slanted type (normal or italic).
    pub style: FontSlant,
    /// Line drawn on the text (underline, line-through, or none).
    pub decoration: TextDecoration,
    /// Letter-case transform applied at render time.
    pub case: LetterCase,
}

/// KPI support-row glyph slot.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct KpiSupportGlyphStyle {
    #[flatten]
    pub glyphs: KpiGlyphSet,
    /// Glyph font.
    #[model(inherit_slot = "Style.charts.kpi.support.font")]
    pub font: KpiSupportFontStyle,
}

/// KPI support row: font, format, tone, and its glyph.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct KpiSupportStyle {
    /// Support row font; its color paints the support value.
    #[model(inherit_slot = "Style.charts.kpi.font")]
    pub font: KpiSupportFontStyle,
    /// How the support value is written: a D3 spec or a preset name.
    #[model(absent = "inferred from the support value")]
    pub format: Option<FormatSpec>,
    /// Semantic color for the support value and glyph. On a delta format the color follows
    /// the sign and tone is the direction: positive (the default) means up is good, negative
    /// means up is bad; zero is muted. warning and info are fixed colors, as is any tone on a
    /// non-delta format.
    #[model(absent = "muted ink")]
    pub tone: Option<Tone>,
    /// Glyph drawn before the support value.
    pub glyph: KpiSupportGlyphStyle,
}

/// Semantic tone palette shared by the KPI support row and table conditional glyphs.
///
/// Colors are theme tokens the renderer reads through this model, so themes can rebrand
/// the semantic vocabulary. Lives on `Style.tones`, since both KPI and table read it.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct KpiTonesStyle {
    /// Color for positive/good tone indicators.
    pub positive: Color,
    /// Color for negative/bad tone indicators.
    pub negative: Color,
    /// Color for warning/caution tone indicators.
    pub warning: Color,
    /// Color for neutral/informational tone indicators.
    pub info: Color,
}

/// KPI chart style.
///
/// KPI uses a fixed sizing contract and paints no legend or color: it builds on
/// [`ChartStyleBase`] alone, and each text slot paints from its own block.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct KpiChartStyle {
    #[flatten]
    pub base: ChartStyleBase,
    /// Font every KPI text slot starts from.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: SingleLineFontStyle,
    /// Card layout. 'stacked' puts value, label, and support on three lines; 'inline' sets
    /// them on one baseline; 'compact' puts the value left and up to two lines right.
    pub variant: KpiVariant,
    /// KPI headline value slot.
    pub value: KpiValueStyle,
    /// KPI card label slot.
    pub label: KpiLabelStyle,
    /// KPI support row slot.
    pub support: KpiSupportStyle,
    /// Minimum KPI card width in pixels.
    pub min_card_width: f64,
    /// Default KPI card height in pixels.
    pub default_height: f64,
    /// KPI card border style.
    #[model(inherit_slot = "Style.charts.border")]
    pub border: BorderStyle,
    /// Inner inset (top/right/bottom/left) for KPI card content in pixels.
    pub content_padding: SpacingValues,
    /// Horizontal alignment of the value, label, and support text within the card.
    #[model(absent = "left-anchored content")]
    pub align: Option<KpiAlign>,
}
