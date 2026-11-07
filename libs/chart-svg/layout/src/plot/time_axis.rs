//! A cartesian plot's temporal x axis: the rungs it ticks at a plot width, their text, which
//! labels show, where they sit, and the gutter their ink takes past the plot.
//!
//! An axis over no calendar grain (bind's `TimeDomain.unit` is `None`) is Vega's continuous time
//! axis: d3's tick rung for a count of a fortieth of the width, or the author's interval, each
//! printed by d3's multi-scale format and thinned by Vega's parity pass.
//!
//! Against [`crate::plot::negotiate::negotiate`] the gutter is a contraction while every shown label
//! is no wider than the plot: a centered label at fraction `f` of the plot hands back `f` of its
//! growth on the left or `1 - f` on the right, and only one side overhangs at a time, so at most a
//! half. A label wider than the plot overhangs both sides and hands back all of it, and the flush
//! anchor switching one pixel from an edge makes the gutter jump by up to half a label; the
//! negotiation then has no fixed point and lands where production's third pass does.
//!
//! The gutter is the labels' ink; the outermost ticks' stroke is the caller's.
//!
//! An axis over a calendar grain (a year, quarter, month, week or day) labels by the cadence ladder
//! ([`crate::plot::time_cadence`]); when its labels name periods coarser than the data and nothing
//! is thinned, it ticks the period boundaries and centers each label between two of them. Every
//! other grained axis stands its labels on its ticks (`on_ticks`).

mod on_ticks;

use bind::model::TimeDomain;
use bind::plot::time::TimeUnitSetting;
use bind::temporal::{enumerate_continuous_span, floor_to_bucket_start, next_bucket, TimeUnit};
use d3rs::calendar::{civil_from_days, days_from_civil, MS_PER_DAY};
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::{Font, FontBook};
use scene::Bounds;

use crate::geom::{Insets, Size};
use crate::plot::time_cadence::{self, label_rows, Cadence, Crowding, LABEL_GAP_SPACES};
use crate::time_ladder::{
    interval_instants, ladder_zone, no_cadence, zone_instants, AuthoredTickCadence,
};

/// An x axis' labels as resolved: the face, the width a label is cut at, how far below the axis
/// line they hang (tick size plus label padding), the least gap Vega's parity rule leaves between
/// labels (`labelSeparation`), and the two `time_unit` keys as authored: `ticks.time_unit` with
/// its `ticks.step`, and `labels.time_unit`.
///
/// A grained axis also reads how its crowding resolves: `crowding` (its `flush`, `skip`, `tilt`
/// and the width it is judged against) and `edge_width`, the plot width a clipped edge period's
/// label is judged against: production's estimate, the card less the value axis, before the fit
/// settles it. A continuous axis reads neither.
///
/// A grained axis whose labels stand on its ticks also assumes the caller refused what layout cannot
/// see and production reads there: an authored `labels.align`, `labels.angle`, `labels.values`
/// or `labels.expr` (`labels_unpinned`, `x_label_align`) keep end-point labels away or move the
/// anchor, so such a board must not reach this axis.
#[derive(Debug, Clone, Copy)]
pub struct TimeLabels<'a> {
    pub font: Font<'a>,
    pub limit: f64,
    pub offset: f64,
    pub min_gap: f64,
    pub tick_unit: TimeUnitSetting,
    pub tick_step: Option<f64>,
    pub label_unit: TimeUnitSetting,
    pub crowding: Crowding<'a>,
    /// The board language's quarter words, resolved by bind (`bind::time_locale::quarter_words`).
    pub quarters: &'a [String; 4],
    pub edge_width: f64,
    /// `labels.font.case` is set: production measures and draws the cased text, which is not modeled.
    pub cased: bool,
    /// Production labels this axis by the curated ladder on ticks rather than as this axis: its
    /// x-type law dropped the grain for the scaffold budget (bind's `TimeDomain` still carries it),
    /// or the author set `axis_x.time_unit: none`. Layout cannot see either; the caller states it.
    pub ladder_bare: bool,
}

/// Where a label's anchor sits along its text.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Align {
    Left,
    Center,
    Right,
}

/// Which edge of its text sits on a label's anchor.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Baseline {
    Top,
    Middle,
    Bottom,
}

/// A label as placed: its text (the year row of a stacked label under a newline), the instant in
/// days it stands on, its anchor in plot coordinates (the axis line at `y = 0`), the degrees it is
/// turned clockwise about that anchor, whether the parity rule keeps it, and its ink (turned with
/// it). Hidden labels still draw their tick.
#[derive(Debug, Clone, PartialEq)]
pub struct PlacedLabel {
    pub text: String,
    pub day: f64,
    pub x: f64,
    pub y: f64,
    pub align: Align,
    pub baseline: Baseline,
    pub angle: f64,
    pub visible: bool,
    pub ink: Bounds,
}

/// The ticks drawn (instants in days) and the labels on them. Over one instant (one bucket) the
/// single tick and its labels stand at the middle of the plot, as d3 centers a degenerate domain.
#[derive(Debug, Clone, PartialEq)]
pub struct Placement {
    pub ticks: Vec<f64>,
    pub labels: Vec<PlacedLabel>,
    pub gutter: Insets,
}

/// What a calendar grain adds to an axis: the grain, the fiscal year's first month, every bucket
/// from the data's first to its last (the dates the ladder judges and the labels name) and the
/// ladder's verdict.
struct Grained {
    data: TimeUnit,
    fiscal_start: u8,
    span: Vec<(i32, u8, u8)>,
    cadence: Cadence,
}

pub struct TimeAxis<'a> {
    labels: TimeLabels<'a>,
    book: &'a FontBook,
    extent: (f64, f64),
    aware: bool,
    cadence: Option<AuthoredTickCadence>,
    grained: Option<Grained>,
    ascent: f64,
    descent: f64,
}

impl<'a> TimeAxis<'a> {
    pub fn new(
        domain: &TimeDomain,
        labels: TimeLabels<'a>,
        book: &'a FontBook,
    ) -> Result<Self, Diagnostic> {
        if labels.cased {
            return Err(Diagnostic::refused(refuse!(
                "a time axis with labels.font.case is not supported"
            )));
        }
        if labels.ladder_bare {
            return Err(Diagnostic::refused(refuse!(
                "a time axis labeled by the curated ladder on ticks (a grain dropped for the scaffold budget, or axis_x.time_unit: none) is not supported"
            )));
        }
        // Production draws a moved fiscal year or quarter ordinally (`resolve_cartesian_x_type`), so a
        // caller routing on the x type never builds a continuous axis for it.
        if domain.fiscal_start.is_some_and(|m| m.get() != 1)
            && matches!(domain.unit, Some(TimeUnit::Year | TimeUnit::YearQuarter))
        {
            return Err(diag::err_internal(
                "a moved fiscal year or quarter grain reached a continuous time axis",
            ));
        }
        if domain.buckets.iter().any(|b| b.timed) {
            return Err(Diagnostic::refused(refuse!(
                "a time axis over times of day (clock ticks and labels) is not supported"
            )));
        }
        let cadence = match labels.tick_unit {
            TimeUnitSetting::Absent => None,
            TimeUnitSetting::Auto => return Err(no_cadence("auto")),
            TimeUnitSetting::Unit(_) if domain.unit.is_some() => {
                return Err(Diagnostic::refused(refuse!(
                    "an authored tick cadence on a time axis over a calendar grain is not supported"
                )))
            }
            TimeUnitSetting::Unit(unit) => {
                Some(AuthoredTickCadence::authored(unit, labels.tick_step))
            }
        };
        let days = domain.buckets.iter().map(|b| b.pos);
        let extent = (
            days.clone().fold(f64::INFINITY, f64::min),
            days.fold(f64::NEG_INFINITY, f64::max),
        );
        if domain.buckets.is_empty() {
            return Err(diag::err_internal("a time axis over no x values"));
        }
        let run = book.run("0", &labels.font).map_err(diag::err_internal)?;
        let grained = domain
            .unit
            .map(|data| Self::grained(domain, data, &labels, extent, book))
            .transpose()?;
        Ok(Self {
            labels,
            book,
            extent,
            grained,
            // An aware timestamp prints its wall clock in the board zone; a bare date or a naive
            // timestamp keeps the one it was written with.
            aware: domain
                .buckets
                .iter()
                .all(|b| b.absolute && b.iso.len() > 10),
            cadence,
            ascent: run.ascent,
            descent: run.descent,
        })
    }

    /// The cadence ladder over a grained domain. `labels.time_unit` names the label grain; one
    /// whose period the data's extent does not span is no cadence (`label_unit_with_boundary`):
    /// with no boundary to center between, the finer on-tick labels read better.
    fn grained(
        domain: &TimeDomain,
        data: TimeUnit,
        labels: &TimeLabels,
        extent: (f64, f64),
        book: &FontBook,
    ) -> Result<Grained, Diagnostic> {
        let fiscal_start = domain.fiscal_start.map_or(1, bind::model::Month::get);
        let (format, label_auto) = match labels.label_unit {
            TimeUnitSetting::Absent | TimeUnitSetting::Auto => (data, true),
            // A week names an instant rather than a span, so production keeps it whatever the
            // extent (`names_span`); every other grain needs a boundary to center between.
            TimeUnitSetting::Unit(unit) if unit == TimeUnit::YearWeek => (unit, false),
            TimeUnitSetting::Unit(unit) => {
                let (y, m, d) = civil_from_days(extent.0.floor() as i64);
                let (ny, nm, nd) =
                    next_bucket(floor_to_bucket_start((y, m, d), unit, fiscal_start), unit);
                if days_from_civil(ny, nm, nd) as f64 <= extent.1 {
                    (unit, false)
                } else {
                    (data, true)
                }
            }
        };
        let dates: Vec<_> = domain.buckets.iter().map(|b| b.date).collect();
        let span = enumerate_continuous_span(&dates, data, fiscal_start);
        let mut measure = |text: &str| time_cadence::decision_width(book, text, &labels.font);
        let cadence = time_cadence::resolve(
            &span,
            data,
            format,
            label_auto,
            fiscal_start,
            &labels.crowding,
            labels.quarters,
            &mut measure,
        )?;
        Ok(Grained {
            data,
            fiscal_start,
            span,
            cadence,
        })
    }

    /// The cadence the ladder settled on; `None` over no calendar grain.
    pub fn cadence(&self) -> Option<Cadence> {
        self.grained.as_ref().map(|g| g.cadence)
    }

    /// `WARN-AXIS-LABEL-COLLISION` when every strategy was tried and the labels still collide.
    pub fn collision(&self, chart_id: &str, field: &str) -> Option<Diagnostic> {
        let count = self.grained.as_ref()?.cadence.collision?;
        Some(diag::warn_axis_label_collision(chart_id, field, count))
    }

    /// The instants ticked on an axis `length` px long: the authored interval, else d3's rung for
    /// `ceil(length / 40)` ticks. A domain of one instant ticks it, the finest interval d3 tries.
    fn instants(&self, length: f64) -> Result<Vec<f64>, Diagnostic> {
        let (lo, hi) = self.extent;
        if lo == hi {
            return Ok(vec![lo]);
        }
        let zone = ladder_zone(self.aware);
        let ticks = match self.cadence {
            Some(AuthoredTickCadence::Interval(interval)) => {
                interval_instants(interval, lo, hi, zone.as_ref())
            }
            Some(AuthoredTickCadence::UncappedCount(n)) => zone_instants(lo, hi, n, zone.as_ref()),
            Some(AuthoredTickCadence::Count(_)) => {
                unreachable!("an authored tick unit never yields a plain count")
            }
            None => zone_instants(
                lo,
                hi,
                ((length / 40.0).ceil() as usize).max(1),
                zone.as_ref(),
            ),
        };
        ticks.map_err(Diagnostic::refused)
    }

    /// The ticks and labels on a plot `plot` across.
    pub fn place(&self, plot: Size) -> Result<Placement, Diagnostic> {
        match &self.grained {
            None => self.place_continuous(plot),
            Some(grained) => match self.place_intervals(grained, plot)? {
                Some(placed) => Ok(placed),
                None => self.place_on_ticks(grained, plot),
            },
        }
    }

    /// The labels on a continuous axis `plot` px across. A label is cut at the limit; which show is Vega's
    /// parity rule over boxes the font size tall, and where they are inked a box is the face's
    /// ascent plus descent tall (quirk class Q2d). An edge label within a pixel of the plot edge
    /// flushes to it. The gutter is the shown labels' overhang.
    fn place_continuous(&self, plot: Size) -> Result<Placement, Diagnostic> {
        let TimeLabels {
            font,
            limit,
            offset,
            ..
        } = self.labels;
        let (lo, hi) = self.extent;
        let locale = bind::time_locale::active_time_locale().map_err(diag::err_internal)?;
        let mut ticks = Vec::new();
        let instants = self.instants(plot.width)?;
        for &day in &instants {
            let wall = if self.aware {
                bind::time_zone::wall_day(day).map_err(diag::err_internal)?
            } else {
                day
            };
            let full = d3rs::time_format::multi_format((wall * MS_PER_DAY).round(), 0.0, &locale)
                .map_err(diag::err_internal)?;
            let (text, _) = self
                .book
                .truncate(&full, &font, limit)
                .map_err(diag::err_internal)?;
            let width = self
                .book
                .run(&full, &font)
                .map_err(diag::err_internal)?
                .advance
                .min(limit);
            let frac = if lo == hi {
                0.5
            } else {
                (day - lo) / (hi - lo)
            };
            // Vega's parity pass reads `measure_shaped`, the face's upright default instance whatever the
            // painted weight and style; the ink is the painted run.
            let parity_width = self
                .book
                .run(
                    &full,
                    &Font {
                        weight: 400.0,
                        italic: false,
                        ..font
                    },
                )
                .map_err(diag::err_internal)?
                .advance
                .min(limit);
            ticks.push((day, text, width, parity_width, frac * plot.width));
        }
        let height = self.ascent + self.descent;
        let boxed = |x: f64, width: f64, align: Align, height: f64| {
            let x1 = match align {
                Align::Left => x,
                Align::Center => x - width / 2.0,
                Align::Right => x - width,
            };
            Bounds {
                x1,
                y1: offset,
                x2: x1 + width,
                y2: offset + height,
            }
        };
        let aligns: Vec<Align> = ticks.iter().map(|t| Self::align(t.4, plot.width)).collect();
        let vega: Vec<Bounds> = ticks
            .iter()
            .zip(&aligns)
            .map(|(t, &align)| boxed(t.4, t.3, align, font.size))
            .collect();
        let shown = parity_visible(&vega, self.labels.min_gap);
        let labels: Vec<PlacedLabel> = ticks
            .into_iter()
            .zip(aligns)
            .zip(shown)
            .map(|(((day, text, width, _, x), align), visible)| PlacedLabel {
                text,
                day,
                x,
                y: offset,
                align,
                baseline: Baseline::Top,
                angle: 0.0,
                visible,
                ink: boxed(x, width, align, height),
            })
            .collect();
        let gutter = gutter_of(&labels, plot.width);
        Ok(Placement {
            ticks: instants,
            labels,
            gutter,
        })
    }

    /// A grained axis whose labels name periods coarser than its data, unthinned: ticks on the
    /// period boundaries, each label centered between two of them over the slice of its period
    /// in view. A period clipped by the data's ends loses its label when the text plus a space does
    /// not fit its slice of `edge_width` (`interval_axis`). The periods end at the next bucket of
    /// their grain from the fiscal start, as production's bucket ends do. `None`: the labels stay on
    /// their ticks (`interval_axis` returns no interval).
    fn place_intervals(
        &self,
        grained: &Grained,
        plot: Size,
    ) -> Result<Option<Placement>, Diagnostic> {
        let Grained {
            data,
            fiscal_start,
            cadence,
            ..
        } = *grained;
        let TimeLabels {
            font,
            limit,
            offset,
            edge_width,
            ..
        } = self.labels;
        let (lo, hi) = self.extent;
        let period = cadence.visibility;
        // A day or a week names an instant, not a span: its labels stay on their ticks.
        if cadence.thinned()
            || rank(cadence.format) < rank(TimeUnit::YearMonth)
            || rank(period) <= rank(data)
            || hi <= lo
        {
            return Ok(None);
        }
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
        // A label's drawn text is cut at the limit; its clip test reads the uncut decision width and
        // its ink the uncut shaped advance capped at the limit.
        let measure = |text: &str| -> Result<(String, f64, f64), Diagnostic> {
            let (cut, _) = self
                .book
                .truncate(text, &font, limit)
                .map_err(diag::err_internal)?;
            let uncut = self
                .book
                .run(text, &font)
                .map_err(diag::err_internal)?
                .advance;
            let decided = time_cadence::decision_width(self.book, text, &font)?;
            Ok((cut, uncut, decided))
        };
        let gap = LABEL_GAP_SPACES * measure(" ")?.2;
        let line = self.ascent + self.descent;
        let mut labels: Vec<PlacedLabel> = Vec::new();
        for &(date, from, to) in &periods {
            let (slice_lo, slice_hi) = (from.max(lo), to.min(hi));
            if slice_hi <= slice_lo {
                continue;
            }
            let (row1, row2) = label_rows(
                date,
                period,
                labels.is_empty(),
                fiscal_start,
                self.labels.quarters,
            )?;
            let (row1, w1, d1) = measure(&row1)?;
            let (row2, w2, d2) = if row2.is_empty() {
                (row2, 0.0, 0.0)
            } else {
                measure(&row2)?
            };
            let width = w1.max(w2);
            let clipped = slice_lo != from || slice_hi != to;
            if clipped && d1.max(d2) + gap > (slice_hi - slice_lo) / (hi - lo) * edge_width {
                continue;
            }
            let day = (slice_lo + slice_hi) / 2.0;
            let x = (day - lo) / (hi - lo) * plot.width;
            let align = Self::align(x, plot.width);
            let width = width.min(limit);
            let x1 = match align {
                Align::Left => x,
                Align::Center => x - width / 2.0,
                Align::Right => x - width,
            };
            let rows = if row2.is_empty() { 1.0 } else { 2.0 };
            labels.push(PlacedLabel {
                text: if row2.is_empty() {
                    row1
                } else {
                    format!("{row1}\n{row2}")
                },
                day,
                x,
                y: offset,
                align,
                baseline: Baseline::Top,
                angle: 0.0,
                visible: true,
                ink: Bounds {
                    x1,
                    y1: offset,
                    x2: x1 + width,
                    y2: offset + rows * line,
                },
            });
        }
        let gutter = gutter_of(&labels, plot.width);
        Ok(Some(Placement {
            ticks,
            labels,
            gutter,
        }))
    }

    /// Vega's `labelFlush` with its default `labelFlushOffset` of 0: a label within a pixel of a
    /// range end anchors on it, the nearer end winning a tie. Production draws the anchor whatever
    /// `labels.flush` says; the setting reaches only the cadence ladder's edge reservation.
    fn align(x: f64, span: f64) -> Align {
        let (near_start, near_end) = (x.abs(), (span - x).abs());
        if near_start < near_end && near_start <= 1.0 {
            Align::Left
        } else if near_end <= 1.0 {
            Align::Right
        } else {
            Align::Center
        }
    }
}

/// Vega's `labelOverlap: parity` over label boxes in axis order: while three or more labels remain
/// and any neighboring pair sits closer than `separation` (the larger of the pair's x and y gaps),
/// every odd-indexed survivor goes; once fewer than three remain with the axis's last label gone,
/// that last label shows in place of the last survivor.
fn parity_visible(ink: &[Bounds], separation: f64) -> Vec<bool> {
    let close = |a: &Bounds, b: &Bounds| {
        separation
            > (b.x1 - a.x2)
                .max(a.x1 - b.x2)
                .max(b.y1 - a.y2)
                .max(a.y1 - b.y2)
    };
    let crowded = |kept: &[usize]| kept.windows(2).any(|w| close(&ink[w[0]], &ink[w[1]]));
    let mut kept: Vec<usize> = (0..ink.len()).collect();
    if kept.len() < 3 || !crowded(&kept) {
        return vec![true; ink.len()];
    }
    while kept.len() >= 3 && crowded(&kept) {
        kept = kept.into_iter().step_by(2).collect();
    }
    let mut visible: Vec<bool> = (0..ink.len()).map(|i| kept.contains(&i)).collect();
    let last = ink.len() - 1;
    if kept.len() < 3 && !visible[last] {
        if kept.len() > 1 {
            visible[kept[kept.len() - 1]] = false;
        }
        visible[last] = true;
    }
    visible
}

/// How far the shown labels' ink reaches past the plot: below it and beyond each side.
fn gutter_of(labels: &[PlacedLabel], width: f64) -> Insets {
    labels
        .iter()
        .filter(|l| l.visible)
        .fold(Insets::ZERO, |g, l| Insets {
            top: 0.0,
            right: g.right.max(l.ink.x2 - width),
            bottom: g.bottom.max(l.ink.y2),
            left: g.left.max(-l.ink.x1),
        })
}

/// How coarse a period is (`period_rank`).
fn rank(unit: TimeUnit) -> u8 {
    match unit {
        TimeUnit::YearMonthDate => 1,
        TimeUnit::YearWeek => 2,
        TimeUnit::YearMonth => 3,
        TimeUnit::YearQuarter => 4,
        TimeUnit::Year => 5,
    }
}
