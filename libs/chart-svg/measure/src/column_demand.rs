//! How much width a column asks for.

use crate::Measure;

/// Rows sampled for a column's demand and its word floor.
pub const SAMPLE_ROWS: usize = 50;

impl Measure<'_> {
    /// The p95 of the cells' measured widths over the first [`SAMPLE_ROWS`]
    /// rows, plus both cell paddings; an empty column asks for the padding alone.
    pub fn column_demand<'t>(
        &self,
        texts: impl IntoIterator<Item = &'t str>,
        family: &str,
        font_size: f64,
        cell_padding: f64,
    ) -> Result<f64, String> {
        let mut widths = texts
            .into_iter()
            .take(SAMPLE_ROWS)
            .map(|text| self.fonts.measure(text, family, font_size))
            .collect::<Result<Vec<f64>, String>>()?;
        if widths.is_empty() {
            return Ok(2.0 * cell_padding);
        }
        widths.sort_by(|a, b| a.partial_cmp(b).expect("a measured width is a number"));
        let idx = ((widths.len() as f64 * 0.95) as usize).min(widths.len() - 1);
        Ok(widths[idx] + 2.0 * cell_padding)
    }
}
