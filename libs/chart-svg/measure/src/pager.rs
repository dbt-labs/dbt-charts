//! The slots of a table's page controls.

use crate::Measure;

impl Measure<'_> {
    /// A page number's slot: `item_width` holds a two-digit label, and each
    /// further digit widens it by its own measured width so the spacing around
    /// the ellipsis and chevrons does not shrink. `family` is the measuring
    /// face, already resolved.
    pub fn page_slot(
        &self,
        label: &str,
        family: &str,
        font_size: f64,
        item_width: f64,
    ) -> Result<f64, String> {
        let label_w = self.fonts.measure(label, family, font_size)?;
        let two_digit_w = self.fonts.measure("00", family, font_size)?;
        Ok(item_width + (label_w - two_digit_w).max(0.0))
    }
}
