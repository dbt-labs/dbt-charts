//! How much width a column asks for.

use crate::Measure;

/// Rows sampled for a column's demand and its word floor.
pub const SAMPLE_ROWS: usize = 50;

/// The p95 of the first [`SAMPLE_ROWS`] cell widths plus both cell paddings; an empty column asks
/// for the padding alone. The widths are the caller's: a table measures raw advances.
pub fn column_demand_of(widths: impl IntoIterator<Item = Result<f64, String>>, cell_padding: f64) -> Result<f64, String> {
    let mut widths = widths.into_iter().take(SAMPLE_ROWS).collect::<Result<Vec<f64>, String>>()?;
    if widths.is_empty() {
        return Ok(2.0 * cell_padding);
    }
    widths.sort_by(|a, b| a.partial_cmp(b).expect("a measured width is a number"));
    let idx = ((widths.len() as f64 * 0.95) as usize).min(widths.len() - 1);
    Ok(widths[idx] + 2.0 * cell_padding)
}

impl Measure<'_> {
    /// [`column_demand_of`] under the book's measure. Each cell is a `(text, family)` pair: a column
    /// mixes the numeral face and the body's.
    pub fn column_demand<'t>(
        &self,
        texts: impl IntoIterator<Item = (&'t str, &'t str)>,
        font_size: f64,
        cell_padding: f64,
    ) -> Result<f64, String> {
        column_demand_of(texts.into_iter().take(SAMPLE_ROWS).map(|(text, family)| self.fonts.measure(text, family, font_size)), cell_padding)
    }
}
