//! Conversions between leaf types that a slot mirror crosses: a slot's leaf is narrower than the
//! source it mirrors, so the inherited value is checked as the narrower type (`fill_try`).

use crate::facet::{Color, FontWeightValue};
use crate::primitives::StrokeStylePatch;
use crate::style::annotations::{AnnotationFontWeight, AnnotationStrokeStylePatch};

impl TryFrom<FontWeightValue> for AnnotationFontWeight {
    type Error = String;
    fn try_from(v: FontWeightValue) -> Result<Self, String> {
        match v {
            FontWeightValue::Number(n) => Self::try_from(n),
            FontWeightValue::Name(s) => match s.parse::<f64>() {
                Ok(n) => Self::try_from(n),
                Err(_) => Self::try_from(s.as_str()),
            },
        }
    }
}

impl TryFrom<StrokeStylePatch> for AnnotationStrokeStylePatch {
    type Error = String;
    /// A set mark stroke makes the annotation stroke set. The patch is empty because the generated
    /// inherit mirrors every leaf of the stroke right after (`color`, `width`, `dasharray`).
    fn try_from(_: StrokeStylePatch) -> Result<Self, String> {
        Ok(Self::default())
    }
}

impl From<Color> for String {
    /// `charts.background` is a plain string in Python while the board background it takes is a color.
    fn from(c: Color) -> String {
        c.as_str().to_string()
    }
}
