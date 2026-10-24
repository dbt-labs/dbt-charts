//! The widths of a set of axis or legend labels set in one face.

use crate::Measure;

impl Measure<'_> {
    /// Each label's width in order, capped at the `limit` a renderer enforces on a label
    /// (Vega-Lite's `labelLimit`).
    pub fn capped_widths(
        &self,
        labels: &[String],
        family: &str,
        size: f64,
        limit: f64,
    ) -> Result<Vec<f64>, String> {
        labels
            .iter()
            .map(|label| self.fonts.measure(label, family, size).map(|w| w.min(limit)))
            .collect()
    }

    /// Each label's raw advance in order.
    pub fn widths(&self, labels: &[String], family: &str, size: f64) -> Result<Vec<f64>, String> {
        labels.iter().map(|label| self.fonts.measure(label, family, size)).collect()
    }

    /// Each label's shaped advance in order (Vega sizes an axis gutter from these).
    pub fn shaped_widths(&self, labels: &[String], family: &str, size: f64) -> Result<Vec<f64>, String> {
        labels.iter().map(|label| self.fonts.measure_shaped(label, family, size)).collect()
    }

    /// The widest label's raw advance; `0` for none.
    pub fn widest(&self, labels: &[String], family: &str, size: f64) -> Result<f64, String> {
        labels.iter().try_fold(0.0_f64, |widest, label| {
            self.fonts.measure(label, family, size).map(|w| widest.max(w))
        })
    }

    /// The widest label as the renderer shapes it (legends are sized from shaped advances);
    /// `None` for no labels.
    pub fn widest_shaped(&self, labels: &[String], family: &str, size: f64) -> Result<Option<f64>, String> {
        labels.iter().try_fold(None, |widest: Option<f64>, label| {
            let w = self.fonts.measure_shaped(label, family, size)?;
            Ok(Some(widest.map_or(w, |widest| widest.max(w))))
        })
    }
}
