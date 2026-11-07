//! The font slots of `models/primitives.py`: `FontStyle` (with leading) and
//! `SingleLineFontStyle` (a one-line slot, KPI), and `SymbolShape`.

use crate::facet::{Color, FontFamily, FontWeightValue};
use crate::string_enum;
use model_derive::Model;

string_enum!(
    /// The named symbols Vega draws for a mark or a legend glyph.
    SymbolShape {
        Circle = "circle",
        Square = "square",
        Cross = "cross",
        Diamond = "diamond",
        TriangleUp = "triangle-up",
        TriangleDown = "triangle-down",
        TriangleRight = "triangle-right",
        TriangleLeft = "triangle-left",
        Stroke = "stroke",
        Arrow = "arrow",
        Wedge = "wedge",
        Triangle = "triangle",
    }
);

string_enum!(
    /// Upright or slanted type.
    FontSlant { Normal = "normal", Italic = "italic" }
);

string_enum!(
    /// Line drawn on the text.
    TextDecoration { None = "none", LineThrough = "line-through", Underline = "underline" }
);

string_enum!(
    /// Letter-case transform applied at render time.
    LetterCase {
        None = "none",
        Sentence = "sentence",
        Title = "title",
        Upper = "upper",
        Lower = "lower",
        Slug = "slug",
        Camel = "camel",
    }
);

/// Text appearance, including leading for text that wraps. Merged as a unit.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "extends", constraint(range(size, gt = 0)))]
pub struct FontStyle {
    /// Name of a `style.fonts` entry this slot starts from; the fields set beside it override that entry.
    #[model(absent = "no named font")]
    pub extends: Option<String>,
    /// Font family or CSS stack; single-quote multiword names: "'Source Serif 4', serif".
    pub family: FontFamily,
    /// Text color as a CSS color string.
    pub color: Color,
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
    /// Line height as a unitless multiple of font size.
    pub line_height: f64,
}

/// Text appearance for a slot that always paints one line: [`FontStyle`] without leading.
///
/// A copy of `FontStyle` less `line_height`: a flatten cannot drop a field.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "extends", constraint(range(size, gt = 0)))]
pub struct SingleLineFontStyle {
    /// Name of a `style.fonts` entry this slot starts from; the fields set beside it override that entry.
    #[model(absent = "no named font")]
    pub extends: Option<String>,
    /// Font family or CSS stack; single-quote multiword names: "'Source Serif 4', serif".
    pub family: FontFamily,
    /// Text color as a CSS color string.
    pub color: Color,
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

/// A `style.fonts` registry entry: a font other slots reference by name.
///
/// Every field is optional: an entry is never resolved on its own, a slot that extends it takes
/// the entry's set fields over its own cascade. A copy of `FontStyle` plus `sizes`: a flatten
/// cannot carry the bare-name shorthand.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "extends", constraint(range(size, gt = 0)))]
pub struct NamedFont {
    /// Name of a `style.fonts` entry this slot starts from; the fields set beside it override that entry.
    #[model(absent = "no named font")]
    pub extends: Option<String>,
    /// Font family or CSS stack; single-quote multiword names: "'Source Serif 4', serif".
    #[model(absent = "the extending slot's family")]
    pub family: Option<FontFamily>,
    /// Text color as a CSS color string.
    #[model(absent = "the extending slot's color")]
    pub color: Option<Color>,
    /// Font size in pixels.
    #[model(absent = "the extending slot's size")]
    pub size: Option<f64>,
    /// How heavy the type is drawn (e.g., 'bold', 400, 700).
    #[model(absent = "the extending slot's weight")]
    pub weight: Option<FontWeightValue>,
    /// Upright or slanted type (normal or italic).
    #[model(absent = "the extending slot's style")]
    pub style: Option<FontSlant>,
    /// Line drawn on the text (underline, line-through, or none).
    #[model(absent = "the extending slot's decoration")]
    pub decoration: Option<TextDecoration>,
    /// Letter-case transform applied at render time.
    #[model(absent = "the extending slot's case")]
    pub case: Option<LetterCase>,
    /// Line height as a unitless multiple of font size.
    #[model(absent = "the extending slot's line height")]
    pub line_height: Option<f64>,
    /// A heading ramp: H1–H6 on `heading`, H2–H6 section titles on `section`. Valid only on those two fonts.
    #[model(absent = "no ramp")]
    pub sizes: Option<Vec<f64>>,
}
