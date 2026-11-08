//! The interval axis (production's `interval_axis` and `IntervalAxis::crowded`, `temporal.rs`): ticks on
//! the boundaries of a calendar period, each label centered on the slice of its period in view. The
//! grained axis whose labels name a coarser period and the clock axis's day labels both read it.

use bind::temporal::{floor_to_bucket_start, next_bucket, TimeUnit};
use d3rs::calendar::{civil_from_days, days_from_civil};
use diagnostic::Diagnostic;

use super::TimeAxis;
use crate::plot::time_cadence::{decision_width, label_rows, Rows, LABEL_GAP_SPACES};

/// A period's label and the slice of the extent it centers on.
pub(super) struct Span {
    pub rows: Rows,
    pub lo: f64,
    pub hi: f64,
}

/// The tick days and the labeled spans of an interval axis.
pub(super) struct Intervals {
    pub ticks: Vec<f64>,
    pub spans: Vec<Span>,
}

impl TimeAxis<'_> {
    /// The interval axis over the extent in `period`s from the fiscal start, judged against a plot
    /// `clip_width` across: a period clipped by the extent's ends loses its label when the text plus a
    /// space does not fit its slice. Periods are never thinned (the caller bails first on a thinned
    /// layout). `None` where no boundary falls past the extent's start: the labels stay on their ticks.
    pub(super) fn intervals(
        &self,
        period: TimeUnit,
        fiscal_start: u8,
        clip_width: f64,
    ) -> Result<Option<Intervals>, Diagnostic> {
        let (lo, hi) = self.extent;
        let font = &self.labels.font;
        let (y, m, d) = civil_from_days(lo.floor() as i64);
        let mut start = floor_to_bucket_start((y, m, d), period, fiscal_start);
        let mut periods = Vec::new();
        while days_from_civil(start.0, start.1, start.2) as f64 <= hi {
            let end = next_bucket(start, period);
            periods.push((
                start,
                days_from_civil(start.0, start.1, start.2) as f64,
                days_from_civil(end.0, end.1, end.2) as f64,
            ));
            start = end;
        }
        let ticks: Vec<f64> = periods
            .iter()
            .map(|p| p.1)
            .filter(|&from| from >= lo)
            .collect();
        if !ticks.iter().any(|&day| day > lo) {
            return Ok(None);
        }
        let decided = |text: &str| decision_width(self.book, text, font);
        let gap = LABEL_GAP_SPACES * decided(" ")?;
        let mut spans: Vec<Span> = Vec::new();
        for &(date, from, to) in &periods {
            let (slice_lo, slice_hi) = (from.max(lo), to.min(hi));
            if slice_hi <= slice_lo {
                continue;
            }
            let rows = label_rows(
                date,
                period,
                spans.is_empty(),
                fiscal_start,
                self.labels.quarters,
            )?;
            let clipped = slice_lo != from || slice_hi != to;
            let width = decided(&rows.0)?.max(if rows.1.is_empty() {
                0.0
            } else {
                decided(&rows.1)?
            });
            if clipped && width + gap > (slice_hi - slice_lo) / (hi - lo) * clip_width {
                continue;
            }
            spans.push(Span {
                rows,
                lo: slice_lo,
                hi: slice_hi,
            });
        }
        Ok(Some(Intervals { ticks, spans }))
    }

    /// `IntervalAxis::crowded`: whether a label comes closer than a space to its neighbor across a plot
    /// `plot_w` wide, the layout a cadence ladder would thin.
    pub(super) fn crowded(&self, spans: &[Span], plot_w: f64) -> Result<bool, Diagnostic> {
        let (lo, hi) = self.extent;
        let font = &self.labels.font;
        let gap = LABEL_GAP_SPACES * decision_width(self.book, " ", font)?;
        let per_day = plot_w / (hi - lo);
        let mut placed = Vec::with_capacity(spans.len());
        for span in spans {
            let (row1, row2) = &span.rows;
            let width = decision_width(self.book, row1, font)?.max(if row2.is_empty() {
                0.0
            } else {
                decision_width(self.book, row2, font)?
            });
            placed.push(((span.lo + span.hi) / 2.0 * per_day, width));
        }
        Ok(placed
            .windows(2)
            .any(|p| p[1].0 - p[0].0 < (p[0].1 + p[1].1) / 2.0 + gap))
    }
}
