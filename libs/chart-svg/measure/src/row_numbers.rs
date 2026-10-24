//! The width of a table's row-number column.

use crate::Measure;

impl Measure<'_> {
    /// Sized from the digits of the total row count, never the page's, so the
    /// column is the same width on every page: the wider of that many nines
    /// and the header label, plus both cell paddings.
    pub fn row_number_column(
        &self,
        header: &str,
        total_rows: usize,
        body_family: &str,
        header_family: &str,
        font_size: f64,
        cell_padding: f64,
    ) -> Result<f64, String> {
        let digits = total_rows
            .max(1)
            .to_string()
            .len()
            .max(header.chars().count());
        let text_w = self
            .fonts
            .measure(&"9".repeat(digits), body_family, font_size)?
            .max(self.fonts.measure(header, header_family, font_size)?);
        Ok(text_w + cell_padding.trunc() * 2.0)
    }
}
