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
//! The x range keeps `TimeLabels::inset` pixels off each plot edge (production pads an unauthored
//! line or area x axis by a mark's radius and two pixels, and an authored `scale.padding` by its
//! own pixels): an instant at fraction `f` of the extent stands at `inset + (width - 2 inset) f`,
//! and the plot's edges are the labels' to overhang. A grained axis flushes a label within a pixel
//! of an end of that inset range; a continuous one flushes by the fraction of the whole plot
//! (`bare_label_anchor`). The end-point clearance and the interval labels' clip test read the
//! whole plot and the card's estimate, as production does. What the pad does to the marks' ink,
//! the annotation scale and a support strip's columns is the caller's.
//!
//! A continuous axis over times of day ([`clock`]) ticks the finest clock step that fits, words each
//! tick on the clock face (stacking the date under a midnight), and names the data's first and last
//! instants or each day where there is room; an aware column reads its clock in the board zone. Every
//! other continuous axis keeps d3's ticks and text. One tick carries several rows there, so a label is
//! as many lines as its ticks stack.
//!
//! An axis over a calendar grain (a year, quarter, month, week or day) labels by the cadence ladder
//! ([`crate::plot::time_cadence`]); when its labels name periods coarser than the data and nothing
//! is thinned, it ticks the period boundaries and centers each label between two of them. Every
//! other grained axis stands its labels on its ticks (`on_ticks`).
//!
//! An authored `ticks.time_unit` pins the ticks to its interval and the labels to them: the period
//! labels of the interval axis and the end-point names are off (`interval_labels_allowed`,
//! `labels_unpinned`), and a tick speaks where its own date opens a label period.

mod clock;
mod intervals;
mod on_ticks;

pub use clock::{Clock, DayLabels};

use bind::bound::TimeDomain;
use bind::plot::time::TimeUnitSetting;
use bind::temporal::{enumerate_continuous_span, floor_to_bucket_start, next_bucket, TimeUnit};
use d3rs::calendar::{civil_from_days, days_from_civil, MS_PER_DAY};
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::{Font, FontBook};
use scene::Bounds;

use crate::geom::{Insets, Size};
use crate::plot::label::{flush_align, parity_visible, vega_space};
use crate::plot::label::{Align, Baseline};
use crate::plot::time_cadence::{self, Cadence, Crowding};
use crate::time_ladder::{
    interval_instants, ladder_zone, no_cadence, zone_instants, AuthoredTickCadence,
};
use clock::{Bare, ClockPlan, Mark};

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
/// anchor, so such a board must not reach this axis. The authored `ticks.count` and label formats
/// layout cannot read are stated (`tick_count`, `date_format`) and refused by name.
///
/// An axis over times of day also assumes the caller refused what layout cannot see and production reads
/// there: `labels.values`, `labels.expr` and `scale.continuous.domain`, each of which makes production
/// keep Vega's own labels or extent. A continuous axis never tilts, so an authored `labels.angle` is
/// the caller's to refuse there as it is on every continuous axis.
///
/// Three laws are the caller's to state where families disagree: `crowding.bucket_aligned` and
/// `spans_buckets` (a bar's), `clock.points` (a scatter has none) and `rail` (the endpoint-rail arm).
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
    /// Pixels the x range keeps off each plot edge: ticks and labels stand over `[inset, width - inset]`
    /// (production's `x_scale_pad`). The caller states it; layout does not derive it. Any finite
    /// value is production's: `x_edge_pad(edge_size)` (at least 2) when `scale.padding` is unauthored
    /// or authored 0, else the authored padding as written, a negative one included (the range
    /// then overruns the plot) and one past half the plot (the range collapses).
    pub inset: f64,
    /// `labels.font.case` is set: production measures and draws the cased text, which is not modeled.
    pub cased: bool,
    /// `ticks.count` is authored: production ticks a capped count (`AuthoredTickCadence::Count`) on a
    /// grained axis and picks d3's rung by it on a continuous one, which is not modeled.
    pub tick_count: bool,
    /// `labels.format` or a redefined `date_<grain>` is authored: production prints every label through
    /// that format (`AuthoredTimeFormat`) and turns the interval labels off, which is not modeled.
    pub date_format: bool,
    /// What an axis over times of day reads; unread without one.
    pub clock: Clock<'a>,
    /// The marks span their buckets, as a bar's do: a grained scale reaches the end of the data's last
    /// bucket (`bar/time_x.rs:382`, `kept.push(end_of(ladder.last()))`), where a line or area's stops at its
    /// last instant. The cadence still reads the data's own buckets. An axis over no grain keeps the
    /// instants (`spanning` is `None`, `:339`).
    pub spans_buckets: bool,
    /// The chart's endpoint rail merges the scales and draws its x ink in closed form over the rows
    /// (`line/layout.rs:2588`): a native cadence ticks only buckets a row fills, so an empty bucket
    /// inside the span, which Vega would tick, is refused by name (`:2594`).
    pub rail: bool,
}

/// A label as placed: its text (the year row of a stacked label under a newline), the instant in
/// days it stands on, its anchor in plot coordinates (the axis line at `y = 0`), the degrees it is
/// turned clockwise about that anchor, whether the parity rule keeps it, its ink (turned with
/// it), and the room production reserves for it ([`vega_space`]), which the gutter reads. Hidden
/// labels still draw their tick.
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
    pub space: Bounds,
}

/// The ticks drawn (instants in days) and the labels on them. Over one instant (one bucket) the
/// single tick and its labels stand at the middle of the plot, as d3 centers a degenerate domain.
#[derive(Debug, Clone, PartialEq)]
pub struct Placement {
    pub ticks: Vec<f64>,
    pub labels: Vec<PlacedLabel>,
    pub gutter: Insets,
    /// The labels stand between period boundaries (`place_intervals`): production draws this axis'
    /// tick marks (`x_ticks_visible = ticks.visible.unwrap_or(true) || interval_axis.is_some()`).
    pub interval: bool,
}

/// The first and last day an axis over `domain` spans: the data's own, or, when the marks span
/// their buckets, to the end of the last bucket.
pub fn day_extent(domain: &TimeDomain, spans_buckets: bool) -> (f64, f64) {
    let days = domain.buckets.iter().map(|b| b.pos);
    let (first, last) = (
        days.clone().fold(f64::INFINITY, f64::min),
        days.fold(f64::NEG_INFINITY, f64::max),
    );
    let fiscal_start = domain.fiscal_start.map_or(1, bind::bound::Month::get);
    match domain.unit.filter(|_| spans_buckets) {
        Some(unit) => {
            let (y, m, d) = civil_from_days(last as i64);
            let (y, m, d) = next_bucket(floor_to_bucket_start((y, m, d), unit, fiscal_start), unit);
            (first, days_from_civil(y, m, d) as f64)
        }
        None => (first, last),
    }
}

/// The x of `day` on a plot `width` across when the axis spans `extent` and keeps `inset` px off
/// each edge: the range collapses to its start once the insets meet, and a single instant stands at
/// the middle of the range, as d3 centers a degenerate domain.
pub fn x_on_range(extent: (f64, f64), inset: f64, day: f64, width: f64) -> f64 {
    let frac = if extent.0 == extent.1 { 0.5 } else { (day - extent.0) / (extent.1 - extent.0) };
    inset + (width - 2.0 * inset).max(0.0) * frac
}

/// What a calendar grain adds to an axis: the grain, the fiscal year's first month, every bucket
/// from the data's first to its last (the dates the ladder judges and the labels name) and the
/// ladder's verdict.
struct Grained {
    data: TimeUnit,
    fiscal_start: u8,
    span: Vec<(i32, u8, u8)>,
    /// The distinct days of the buckets a row fills, ascending.
    filled: Vec<f64>,
    cadence: Cadence,
}

pub struct TimeAxis<'a> {
    labels: TimeLabels<'a>,
    book: &'a FontBook,
    extent: (f64, f64),
    aware: bool,
    cadence: Option<AuthoredTickCadence>,
    grained: Option<Grained>,
    clock: Option<ClockPlan>,
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
        if labels.tick_count {
            return Err(Diagnostic::refused(refuse!(
                "a time axis with an authored ticks.count is not supported"
            )));
        }
        if labels.date_format {
            return Err(Diagnostic::refused(refuse!(
                "a time axis with an authored labels.format or date_<grain> format is not supported"
            )));
        }
        if !labels.inset.is_finite() {
            return Err(diag::err_internal(format!(
                "a time axis inset of {} px is not a pixel count",
                labels.inset
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
        let cadence = match labels.tick_unit {
            TimeUnitSetting::Absent => None,
            TimeUnitSetting::Auto => return Err(no_cadence("auto")),
            TimeUnitSetting::Unit(unit) => {
                Some(AuthoredTickCadence::authored(unit, labels.tick_step))
            }
        };
        // An aware timestamp prints its wall clock in the board zone; a bare date or a naive
        // timestamp keeps the one it was written with.
        let aware = domain
            .buckets
            .iter()
            .all(|b| b.absolute && b.iso.len() > 10);
        let clock = ClockPlan::of(domain, &labels.clock, cadence, aware)?;
        // Production draws a dropped-grain axis's ticks itself: the curated ladder for a
        // scaffold-budget drop it keeps (`keeps_dropped_grain_ladder`), d3's ticks for an authored
        // `time_unit: none` or any other axis that names its own. Layout draws neither, unless the
        // axis keeps a clock plan.
        if domain.grain_drop.is_some() && clock.is_none() {
            return Err(Diagnostic::refused(refuse!(
                "a time axis whose calendar grain was dropped (for the scaffold budget, or axis_x.time_unit: none) is not supported: production draws its own ticks there"
            )));
        }
        if domain.buckets.is_empty() {
            return Err(diag::err_internal("a time axis over no x values"));
        }
        let extent = day_extent(domain, labels.spans_buckets);
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
            aware,
            clock,
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
        let fiscal_start = domain.fiscal_start.map_or(1, bind::bound::Month::get);
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
        let mut filled: Vec<f64> = domain.buckets.iter().map(|b| b.pos).collect();
        filled.sort_unstable_by(f64::total_cmp);
        filled.dedup();
        Ok(Grained {
            data,
            fiscal_start,
            span,
            filled,
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
        if let Some(ticks) = self.clock.as_ref().and_then(|c| c.ticks(lo, hi)) {
            return ticks.map_err(Diagnostic::refused);
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

    /// The x of `day` on a plot `width` across (a tick's, where a gridline stands).
    pub fn x_of(&self, day: f64, width: f64) -> f64 {
        x_on_range(self.extent, self.labels.inset, day, width)
    }

    /// A grained label's anchor: Vega's flush read on the inset range (`flat_label_anchor`), its
    /// span left unclamped as production leaves it, at `labels.flush`'s threshold (none centers).
    fn range_align(&self, frac: f64, width: f64) -> Align {
        let span = width - 2.0 * self.labels.inset;
        flush_align(frac * span, span, self.labels.crowding.flush)
    }

    /// The ticks and labels on a plot `plot` across.
    pub fn place(&self, plot: Size) -> Result<Placement, Diagnostic> {
        match &self.grained {
            None => self.place_continuous(plot),
            // An authored cadence pins the labels to its ticks (`interval_labels_allowed`).
            Some(grained) if self.cadence.is_some() => self.place_on_ticks(grained, plot),
            Some(grained) => match self.place_intervals(grained, plot)? {
                Some(placed) => Ok(placed),
                None => self.place_on_ticks(grained, plot),
            },
        }
    }

    /// The labels on a continuous axis `plot` px across. A label is cut at the limit; which show is Vega's
    /// parity rule over boxes the font size tall (a second row adds the font size and two pixels), and
    /// where they are inked a box is the face's ascent plus descent tall per row (quirk class Q2d). An
    /// edge label within a pixel of the plot edge flushes to it. The gutter is the shown labels' overhang,
    /// and a stacked clock axis reserves its second row whatever shows.
    fn place_continuous(&self, plot: Size) -> Result<Placement, Diagnostic> {
        let TimeLabels {
            font,
            limit,
            offset,
            ..
        } = self.labels;
        let instants = self.instants(plot.width)?;
        let bare = match &self.clock {
            Some(clock) => self.clock_ticks(clock, &instants, plot.width)?,
            None => self.plain_ticks(&instants)?,
        };
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
        let upright = Font {
            weight: 400.0,
            italic: false,
            ..font
        };
        // A bare axis flushes on the fraction of the whole plot, not of the inset range
        // (`bare_label_anchor`).
        let mut cut = Vec::new();
        let mut vega = Vec::new();
        let mut inks = Vec::new();
        let mut aligns = Vec::new();
        for tick in bare.iter().filter(|t| t.mark != Mark::TickOnly) {
            let rows: Vec<&str> = std::iter::once(tick.row1.as_str())
                .chain(Some(tick.row2.as_str()).filter(|row| !row.is_empty()))
                .collect();
            let mut text = Vec::new();
            let (mut ink, mut parity) = (0.0_f64, 0.0_f64);
            for row in &rows {
                text.push(
                    self.book
                        .truncate(row, &font, limit)
                        .map_err(diag::err_internal)?
                        .0,
                );
                ink = ink.max(
                    self.book
                        .run(row, &font)
                        .map_err(diag::err_internal)?
                        .advance,
                );
                // Vega's parity pass reads `measure_shaped`, the face's upright default instance whatever the
                // painted weight and style; the ink is the painted run.
                parity = parity.max(
                    self.book
                        .run(row, &upright)
                        .map_err(diag::err_internal)?
                        .advance,
                );
            }
            let align = flush_align(tick.frac * plot.width, plot.width, self.labels.crowding.flush);
            let x = self.x_of(tick.day, plot.width);
            // Flat labels share one top, so a second row never changes a gap between boxes.
            vega.push(boxed(x, parity.min(limit), align, font.size));
            let ink = boxed(x, ink.min(limit), align, height * rows.len() as f64);
            let space = vega_space(
                (ink.x1, ink.x2),
                (x, offset),
                Baseline::Top,
                font.size,
                rows.len(),
                0.0,
            );
            inks.push((ink, space));
            aligns.push((tick, x, align));
            cut.push(text.join("\n"));
        }
        let shown = parity_visible(&vega, self.labels.min_gap);
        let labels: Vec<PlacedLabel> = aligns
            .into_iter()
            .zip(cut)
            .zip(inks)
            .zip(shown)
            .map(
                |((((tick, x, align), text), (ink, space)), visible)| PlacedLabel {
                    text,
                    day: tick.day,
                    x,
                    y: offset,
                    align,
                    baseline: Baseline::Top,
                    angle: 0.0,
                    visible,
                    ink,
                    space,
                },
            )
            .collect();
        let mut gutter = gutter_of(&labels, plot.width);
        if self.clock.as_ref().is_some_and(ClockPlan::stacked) {
            let rows = vega_space((0.0, 0.0), (0.0, offset), Baseline::Top, font.size, 2, 0.0);
            gutter.bottom = gutter.bottom.max(rows.y2);
        }
        Ok(Placement {
            ticks: bare
                .iter()
                .filter(|t| t.mark != Mark::LabelOnly)
                .map(|t| t.day)
                .collect(),
            labels,
            gutter,
            interval: false,
        })
    }

    /// d3's multi-scale text at each tick (Vega's own labels).
    fn plain_ticks(&self, instants: &[f64]) -> Result<Vec<Bare>, Diagnostic> {
        let (lo, hi) = self.extent;
        let locale = bind::time_locale::active_time_locale().map_err(diag::err_internal)?;
        instants
            .iter()
            .map(|&day| {
                let wall = if self.aware {
                    bind::time_zone::wall_day(day).map_err(diag::err_internal)?
                } else {
                    day
                };
                Ok(Bare {
                    day,
                    frac: if lo == hi {
                        0.5
                    } else {
                        (day - lo) / (hi - lo)
                    },
                    row1: d3rs::time_format::multi_format(
                        (wall * MS_PER_DAY).round(),
                        0.0,
                        &locale,
                    )
                    .map_err(diag::err_internal)?,
                    row2: String::new(),
                    mark: Mark::Both,
                })
            })
            .collect()
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
        let Some(intervals::Intervals { ticks, spans }) =
            self.intervals(period, fiscal_start, edge_width)?
        else {
            return Ok(None);
        };
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
        let line = self.ascent + self.descent;
        let mut labels: Vec<PlacedLabel> = Vec::new();
        for intervals::Span {
            rows: (row1, row2),
            lo: slice_lo,
            hi: slice_hi,
        } in spans
        {
            let (row1, w1, _) = measure(&row1)?;
            let (row2, w2, _) = if row2.is_empty() {
                (row2, 0.0, 0.0)
            } else {
                measure(&row2)?
            };
            let width = w1.max(w2);
            let day = (slice_lo + slice_hi) / 2.0;
            let frac = (day - lo) / (hi - lo);
            let x = self.x_of(day, plot.width);
            let align = self.range_align(frac, plot.width);
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
                space: vega_space(
                    (x1, x1 + width),
                    (x, offset),
                    Baseline::Top,
                    font.size,
                    rows as usize,
                    0.0,
                ),
            });
        }
        let gutter = gutter_of(&labels, plot.width);
        Ok(Some(Placement {
            ticks,
            labels,
            gutter,
            interval: true,
        }))
    }
}

/// How far the shown labels' ink reaches past the plot: below it and beyond each side.
fn gutter_of(labels: &[PlacedLabel], width: f64) -> Insets {
    labels
        .iter()
        .filter(|l| l.visible)
        .fold(Insets::ZERO, |g, l| Insets {
            top: 0.0,
            right: g.right.max(l.space.x2 - width),
            bottom: g.bottom.max(l.space.y2),
            left: g.left.max(-l.space.x1),
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
