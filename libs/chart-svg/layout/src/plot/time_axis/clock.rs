//! A continuous time axis over times of day: production's sub-day treatment (`families/subday.rs`,
//! `bare_temporal_ticks`, `SubdayPlan::with_points`).
//!
//! The plan reads the distinct instants of the data: with a time of day among them, a single clock
//! (all naive, or all absolute; a mix is Vega's own axis) and at least two instants, the axis
//! ticks the finest clock step that fits (a naive column only) and words each tick on the clock
//! face ("Midnight", "6am", ":30"), stacking the date under a midnight where the axis crosses one.
//! Where no clock vocabulary applies (a span of a year or more, or a day-sized tick step against
//! a count too small) the ticks and their d3 text stay the continuous axis's own. Day labels, centered
//! at noon between midnight ticks, replace the clock labels when asked for or when the ticks are
//! a day or more apart, and the data's first and last instants are named where there is room.
//!
//! The axis is the line and area axis. Production's scatter builds the same plan but draws only the clock
//! ticks (no end points, no day labels: `scatter_axis.rs:675, 908-934`), so a scatter caller must not reach it.
//!
//! What layout cannot see and production reads is the caller's to refuse: `labels.values`, `labels.expr`
//! and `scale.continuous.domain` (each makes production keep Vega's own labels or extent).

use bind::model::TimeDomain;
use bind::temporal::TimeUnit;
use d3rs::calendar::MS_PER_DAY;
use d3rs::time::Unit;
use diagnostic::{diag, Diagnostic};
use jiff::tz::TimeZone;

use super::intervals::Intervals;
use super::on_ticks::edge_clear;
use super::TimeAxis;
use crate::plot::time_cadence::decision_width;
use crate::time_ladder::{every_instants, ms_of, AuthoredTickCadence};

const SPAN_CEILING_MS: i64 = 365 * DAY_MS;
const TICK_PITCH_PX: f64 = 40.0;
const ESTIMATED_Y_AXIS_CHROME_PX: f64 = 100.0;
const LADDER_PITCH_PX: f64 = 64.0;
/// `sqrt(12 * 24)` hours: where d3's tick ladder steps from hours to days.
const DAY_STEP_BOUNDARY_HOURS: f64 = 16.970_562_748_477_14;
/// `sqrt(30 * 60)` seconds: where d3's tick ladder steps from seconds to minutes.
const MINUTE_STEP_BOUNDARY_SECONDS: f64 = 42.426_406_871_192_85;
/// The card width below which production's tiers call a chart tiny (`typography.width_tier`).
const TINY_MAX_PX: f64 = 352.5;
const MINUTE_MS: i64 = 60_000;
const HOUR_MS: i64 = 3_600_000;
const DAY_MS: i64 = 86_400_000;

/// A clock-aligned tick step a person reads off a clock face (`subday.rs` `LADDER`).
const LADDER: [(Unit, i64); 10] = [
    (Unit::Minute, 1),
    (Unit::Minute, 5),
    (Unit::Minute, 10),
    (Unit::Minute, 15),
    (Unit::Minute, 30),
    (Unit::Hour, 1),
    (Unit::Hour, 2),
    (Unit::Hour, 3),
    (Unit::Hour, 6),
    (Unit::Hour, 12),
];

/// Day labels over timed data: asked for by `labels.time_unit: yearmonthdate`, or left to the axis,
/// which draws them where its ticks are a day or more apart.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DayLabels {
    Off,
    Authored,
    OnDayTicks,
}

/// What a clock axis reads that the domain does not state. Only a domain with a time of day reads it.
#[derive(Debug, Clone, Copy)]
pub struct Clock<'a> {
    /// `labels.clock` is 24; any other value reads the 12-hour clock.
    pub hours24: bool,
    /// The chart's outer width: below 352.5 px the labels read numerals ("12am") rather than words.
    pub outer_width: f64,
    /// The width the clock ladder is chosen against: the outer width less the endpoint rail's.
    pub available_width: f64,
    /// The day labels a line or area axis derives (production's `plan_for`) from `labels.time_unit`,
    /// `labels.align`, `labels.visible` and the formats: `Authored` for `yearmonthdate`, `OnDayTicks` for
    /// an unset or `auto` unit when interval labels are allowed (`interval_labels_allowed`), else `Off`.
    /// A scatter has no day labels and never reaches this axis.
    pub day_labels: DayLabels,
    /// Midnight and noon in the board language (`bind::time_locale::clock_words`).
    pub words: &'a [String; 2],
}

/// How a tick paints: both, a boundary with no label between, or a label no tick marks.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum Mark {
    Both,
    TickOnly,
    LabelOnly,
}

/// A tick of the continuous axis before it is placed: its instant, fraction of the extent and the
/// rows it paints (`row2` is empty off a midnight).
#[derive(Debug, Clone)]
pub(super) struct Bare {
    pub day: f64,
    pub frac: f64,
    pub row1: String,
    pub row2: String,
    pub mark: Mark,
}

#[derive(Debug, Clone, Copy)]
struct Vocab {
    narrow: bool,
    hours24: bool,
    date_row: bool,
    hour_boundary: bool,
}

/// How production ticks and labels a continuous axis over timed values (`SubdayPlan`).
#[derive(Debug, Clone)]
pub(super) struct ClockPlan {
    ladder: Option<(Unit, i64)>,
    vocab: Option<Vocab>,
    day_labels: DayLabels,
}

impl ClockPlan {
    /// `SubdayPlan::of`; `None` where production keeps Vega's own ticks and labels: no time of day among
    /// the values, a mix of clocks, fewer than two instants, or an authored interval (a day or coarser,
    /// so every tick lands on midnight).
    pub(super) fn of(
        domain: &TimeDomain,
        clock: &Clock,
        cadence: Option<AuthoredTickCadence>,
        aware: bool,
    ) -> Result<Option<Self>, Diagnostic> {
        // A short date form is no instant production reads (`SubdaySource::of`).
        let read: Vec<_> = domain
            .buckets
            .iter()
            .filter(|b| b.iso.len() >= 10)
            .collect();
        if !read.iter().any(|b| b.timed) {
            return Ok(None);
        }
        if domain.unit.is_some() {
            return Err(diag::err_internal(
                "a calendar grain keys a time of day to its bucket, so a grained domain holds none",
            ));
        }
        let mut instants: Vec<i64> = read.iter().map(|b| ms_of(b.at)).collect();
        instants.sort_unstable();
        instants.dedup();
        let (absolute, naive) = (
            read.iter().any(|b| b.absolute),
            read.iter().any(|b| !b.absolute),
        );
        if (absolute && naive) || instants.len() < 2 {
            return Ok(None);
        }
        let (first, last) = (instants[0], instants[instants.len() - 1]);
        let span = last - first;
        let (ladder, count, ceiling) = match cadence {
            Some(AuthoredTickCadence::Interval(_)) => return Ok(None),
            Some(AuthoredTickCadence::Count(_)) => {
                unreachable!("an authored tick unit never yields a plain count")
            }
            Some(AuthoredTickCadence::UncappedCount(n)) => (None, Some(n), Some(n)),
            None => match default_ladder(span, absolute, clock.available_width) {
                Some((step, n)) => (Some(step), Some(n), Some(n)),
                None => {
                    let width = clock.available_width - ESTIMATED_Y_AXIS_CHROME_PX;
                    if width > 0.0 {
                        (
                            None,
                            Some((width / TICK_PITCH_PX).ceil() as usize),
                            Some((clock.available_width / TICK_PITCH_PX).ceil() as usize),
                        )
                    } else {
                        (None, None, None)
                    }
                }
            },
        };
        // An aware column's midnights and hours are the board zone's.
        let wall = |ms: i64| {
            if aware {
                bind::time_zone::wall_ms(ms).map_err(diag::err_internal)
            } else {
                Ok(ms)
            }
        };
        let (first_wall, last_wall) = (wall(first)?, wall(last)?);
        let vocab = count.zip(ceiling).and_then(|(count, ceiling)| {
            let span = span as f64;
            if span >= SPAN_CEILING_MS as f64
                || span >= DAY_STEP_BOUNDARY_HOURS * HOUR_MS as f64 * count as f64
                || span < MINUTE_STEP_BOUNDARY_SECONDS * 1000.0 * ceiling as f64
            {
                return None;
            }
            Some(Vocab {
                narrow: clock.outer_width < TINY_MAX_PX,
                hours24: clock.hours24,
                date_row: midnight_count(first_wall, last_wall) > 1,
                hour_boundary: (first_wall + HOUR_MS - 1).div_euclid(HOUR_MS) * HOUR_MS
                    <= last_wall,
            })
        });
        // Authored ticks or `labels.values` keep the clock axis over day labels; the caller refused values.
        let day_labels = if cadence.is_none() {
            clock.day_labels
        } else {
            DayLabels::Off
        };
        Ok(Some(Self {
            ladder,
            vocab,
            day_labels,
        }))
    }

    /// The instants the clock ladder ticks, `None` where the axis keeps d3's own.
    pub(super) fn ticks(&self, lo: f64, hi: f64) -> Option<Result<Vec<f64>, String>> {
        // Only a naive column steps a clock ladder, so its clock is UTC's.
        self.ladder
            .map(|(unit, step)| every_instants(unit, step, &TimeZone::UTC, lo, hi))
    }

    /// Whether every label stacks a date row under its text.
    pub(super) fn stacked(&self) -> bool {
        self.vocab.is_some_and(|v| v.date_row)
    }
}

/// `default_subday_tick_interval`: the finest clock step that fits the width, with the count of ticks it draws.
fn default_ladder(span: i64, absolute: bool, available_width: f64) -> Option<((Unit, i64), usize)> {
    let max_ticks = ((available_width - ESTIMATED_Y_AXIS_CHROME_PX) / LADDER_PITCH_PX).floor();
    if absolute || span >= SPAN_CEILING_MS {
        return None;
    }
    LADDER.into_iter().find_map(|(unit, step)| {
        let step_ms = step
            * if unit == Unit::Minute {
                MINUTE_MS
            } else {
                HOUR_MS
            };
        let count = (span + step_ms - 1).div_euclid(step_ms);
        (2 <= count && count as f64 <= max_ticks).then_some(((unit, step), count as usize))
    })
}

/// `_midnight_count`: the distinct days' midnights inside `[first, last]` (`last` past `first`), a
/// wrapped day (opening and closing on a midnight) not counting its closing one. Production also guards
/// the wrap with `last > first` and floors the count at 0; only `> 1` reads the count here, `last` is
/// past `first` (two distinct instants), and a count below 1 is no date row either way, so neither changes
/// a verdict.
fn midnight_count(first: i64, last: i64) -> i64 {
    let start = (first + DAY_MS - 1).div_euclid(DAY_MS);
    let mut end = last.div_euclid(DAY_MS);
    if first.rem_euclid(DAY_MS) == 0 && last.rem_euclid(DAY_MS) == 0 {
        end -= 1;
    }
    end - start + 1
}

/// `spec` of the wall instant `at` (days) in the board's time locale, as production's strftime reads it.
fn clock_text(spec: &str, at: f64) -> Result<String, Diagnostic> {
    let locale = bind::time_locale::active_time_locale().map_err(diag::err_internal)?;
    d3rs::time_format::format(spec, ms_of(at) as f64, 0.0, &locale).map_err(diag::err_internal)
}

/// `%p` lowered.
fn meridiem(at: f64) -> Result<String, Diagnostic> {
    Ok(clock_text("%p", at)?.to_lowercase())
}

impl Vocab {
    /// The label of the tick at wall instant `at` (days), `index` counting the ticks drawn: one row,
    /// and the date row where the axis crosses a midnight (empty off one).
    fn label(
        self,
        at: f64,
        index: usize,
        words: &[String; 2],
    ) -> Result<(String, String), Diagnostic> {
        let ms = ms_of(at).rem_euclid(DAY_MS);
        let (hour, minute) = (ms / HOUR_MS, ms / MINUTE_MS % 60);
        let core = if self.hours24 {
            clock_text("%H:%M", at)?
        } else if minute != 0 && !self.hour_boundary && index == 0 {
            format!("{}{}", clock_text("%-I:%M", at)?, meridiem(at)?)
        } else if minute != 0 {
            clock_text(":%M", at)?
        } else if self.narrow {
            format!("{}{}", clock_text("%-I", at)?, meridiem(at)?)
        } else {
            match hour {
                0 => words[0].clone(),
                12 => words[1].clone(),
                _ => format!("{}{}", clock_text("%-I", at)?, meridiem(at)?),
            }
        };
        let row2 = if self.date_row && hour == 0 {
            clock_text("%b %-d", at)?
        } else {
            String::new()
        };
        Ok((core, row2))
    }

    /// An instant no tick marks: its time to the minute, and the date row where the axis crosses a midnight.
    fn point_label(self, at: f64, with_date: bool) -> Result<(String, String), Diagnostic> {
        let text = if self.hours24 {
            clock_text("%H:%M", at)?
        } else {
            format!("{}{}", clock_text("%-I:%M", at)?, meridiem(at)?)
        };
        let row2 = if self.date_row && with_date {
            clock_text("%b %-d", at)?
        } else {
            String::new()
        };
        Ok((text, row2))
    }
}

impl TimeAxis<'_> {
    /// `bare_temporal_ticks` over a clock plan, then `SubdayPlan::with_points`.
    pub(super) fn clock_ticks(
        &self,
        plan: &ClockPlan,
        instants: &[f64],
        plot_w: f64,
    ) -> Result<Vec<Bare>, Diagnostic> {
        let (lo, hi) = self.extent;
        let locale = bind::time_locale::active_time_locale().map_err(diag::err_internal)?;
        let ticks = instants
            .iter()
            .enumerate()
            .map(|(index, &day)| {
                let wall = if self.aware {
                    bind::time_zone::wall_day(day).map_err(diag::err_internal)?
                } else {
                    day
                };
                let (row1, row2) = match plan.vocab {
                    Some(vocab) => vocab.label(wall, index, self.labels.clock.words)?,
                    None => (
                        d3rs::time_format::multi_format((wall * MS_PER_DAY).round(), 0.0, &locale)
                            .map_err(diag::err_internal)?,
                        String::new(),
                    ),
                };
                Ok(Bare {
                    day,
                    frac: (day - lo) / (hi - lo),
                    row1,
                    row2,
                    mark: Mark::Both,
                })
            })
            .collect::<Result<Vec<_>, Diagnostic>>()?;
        self.with_points(plan, ticks, plot_w)
    }

    /// A row's width as production's clearance tests read it (`measure_weighted`).
    fn decided(&self, text: &str) -> Result<f64, Diagnostic> {
        decision_width(self.book, text, &self.labels.font)
    }

    /// The widest row of a tick.
    fn widest(&self, tick: &Bare) -> Result<f64, Diagnostic> {
        Ok(self.decided(&tick.row1)?.max(self.decided(&tick.row2)?))
    }

    /// `SubdayPlan::with_points`: a label per midnight-bounded day, centered between midnight ticks,
    /// and otherwise the data's first and last instants labeled where there is room past the outermost tick.
    fn with_points(
        &self,
        plan: &ClockPlan,
        ticks: Vec<Bare>,
        plot_w: f64,
    ) -> Result<Vec<Bare>, Diagnostic> {
        let (lo, hi) = self.extent;
        let day_ticks = ticks.len() >= 2 && ticks.windows(2).all(|p| p[1].day - p[0].day >= 0.5);
        if plan.day_labels == DayLabels::Authored
            || plan.day_labels == DayLabels::OnDayTicks && day_ticks
        {
            return match self.day_intervals(plot_w)? {
                Some(days) => Ok(days),
                None => Ok(ticks),
            };
        }
        let (Some(vocab), Some(_)) = (plan.vocab, plan.ladder) else {
            return Ok(ticks);
        };
        let n = ticks.len();
        if n < 2 {
            return Ok(ticks);
        }
        let point = |at: f64, with_date: bool, frac: f64| -> Result<Bare, Diagnostic> {
            let (row1, row2) = vocab.point_label(at, with_date)?;
            Ok(Bare {
                day: at,
                frac,
                row1,
                row2,
                mark: Mark::LabelOnly,
            })
        };
        let (first, last) = (point(lo, true, 0.0)?, point(hi, false, 1.0)?);
        let pitch = (
            ticks[1].frac - ticks[0].frac,
            ticks[n - 1].frac - ticks[n - 2].frac,
        );
        let keep_first = edge_clear(
            plot_w,
            self.widest(&first)?,
            Some((ticks[0].frac, self.widest(&ticks[0])?)),
            pitch.0,
        );
        let keep_last = edge_clear(
            plot_w,
            self.widest(&last)?,
            Some((1.0 - ticks[n - 1].frac, self.widest(&ticks[n - 1])?)),
            pitch.1,
        );
        let mut out = Vec::with_capacity(n + 2);
        if keep_first {
            out.push(first);
        }
        out.extend(ticks);
        if keep_last {
            out.push(last);
        }
        Ok(out)
    }

    /// The day labels (`interval_axis` over days, fiscal year from January): a tick on every midnight and a
    /// label centered on each day's visible slice. `None` where the extent holds no midnight past its
    /// start, or the labels crowd: the clock axis stays.
    fn day_intervals(&self, plot_w: f64) -> Result<Option<Vec<Bare>>, Diagnostic> {
        let (lo, hi) = self.extent;
        let Some(Intervals { ticks, spans }) =
            self.intervals(TimeUnit::YearMonthDate, 1, plot_w)?
        else {
            return Ok(None);
        };
        if self.crowded(&spans, plot_w)? {
            return Ok(None);
        }
        let frac = |day: f64| (day - lo) / (hi - lo);
        let boundaries = ticks.into_iter().map(|day| Bare {
            day,
            frac: frac(day),
            row1: String::new(),
            row2: String::new(),
            mark: Mark::TickOnly,
        });
        let labels = spans.into_iter().map(|span| {
            let day = (span.lo + span.hi) / 2.0;
            Bare {
                day,
                frac: frac(day),
                row1: span.rows.0,
                row2: span.rows.1,
                mark: Mark::LabelOnly,
            }
        });
        Ok(Some(boundaries.chain(labels).collect()))
    }
}
