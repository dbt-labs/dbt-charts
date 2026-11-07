//! The tick ladder of a time axis over instants (`d3.timeTicks`), in the board's zone.
//!
//! An axis's ticks and a `labels.expr` scale's `ticks()` walk the same ladder, so both read it here.

use ::bind::temporal::TimeUnit;
use d3rs::calendar::MS_PER_DAY;
use d3rs::time::{Interval, Unit};
use diagnostic::{diag, Diagnostic};
use jiff::tz::TimeZone;

/// The zone a time axis over instants ticks in: the board zone when the axis's values are `aware` (each carries
/// `Z` or an offset) and the board is outside UTC; `None` otherwise, UTC days, where a naive wall clock reads as
/// UTC and a calendar bucket is a bare date.
pub fn ladder_zone(aware: bool) -> Option<TimeZone> {
    let zone = ::bind::time_zone::active_time_zone();
    (aware && zone != TimeZone::UTC).then_some(zone)
}

/// The zone and week start a [`ladder_zone`] ticks with: the board's week start in a zone, Sunday's in UTC.
pub fn ladder_clock(zone: Option<&TimeZone>) -> (TimeZone, u8) {
    match zone {
        Some(zone) => (zone.clone(), ::bind::time_locale::active_week_start()),
        None => (TimeZone::UTC, 0),
    }
}

/// Fractional days to whole milliseconds, the way d3's dates hold them.
pub fn ms_of(day: f64) -> i64 {
    (day * MS_PER_DAY).round() as i64
}

/// d3-time's ticks of a time scale (`timeTicks`, [`d3rs::time::ticks`]) for `count` ticks, in fractional days,
/// every aligned instant of `[lo, hi]`: the day, week, month and year rungs fall on the zone's midnights across its
/// transitions, and a span finer than the ladder ticks the milliseconds `tickStep` divides.
pub fn zone_instants(
    lo: f64,
    hi: f64,
    count: usize,
    zone: Option<&TimeZone>,
) -> Result<Vec<f64>, String> {
    let (zone, week_start) = ladder_clock(zone);
    let ms = d3rs::time::ticks(ms_of(lo), ms_of(hi), count, &zone, week_start)?;
    Ok(last_of_each_second(&ms))
}

/// Vega keys an axis tick by its `Date`, whose string stops at the second, so the ticks of one second collapse
/// into the last of them.
pub fn last_of_each_second(ms: &[i64]) -> Vec<f64> {
    let kept = ms.iter().enumerate().filter(|&(i, &at)| {
        ms.get(i + 1)
            .is_none_or(|&next| next.div_euclid(1000) != at.div_euclid(1000))
    });
    kept.map(|(_, &at)| at as f64 / MS_PER_DAY).collect()
}

/// A d3-time calendar interval with its `every(step)` filter, in the units
/// this module models: the one an authored `ticks.time_unit` + `step` names
/// or the default cadence of a grain. Each variant's filter is d3's own: a
/// field-based interval keeps the instants whose field value divides the
/// step, so `Day(2)` restarts at the 1st of each month and `Month(3)` is
/// Jan/Apr/Jul/Oct — not a running stride (d3-time `src/interval.js` `every`).
/// A week has no field, so `Week(step)` counts weeks from the epoch's first
/// one (`interval.count(0, date) % step`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UtcTickInterval {
    Day(i64),
    Week(i64),
    Month(i64),
    Year(i64),
}

impl UtcTickInterval {
    /// The d3-time unit and its step; a week starts on `week_start` (0 = Sunday).
    fn unit_step(self, week_start: u8) -> (Unit, i64) {
        match self {
            Self::Day(step) => (Unit::Day, step),
            Self::Week(step) => (Unit::Week(week_start), step),
            Self::Month(step) => (Unit::Month, step),
            Self::Year(step) => (Unit::Year, step),
        }
    }
}

/// Every boundary of `unit.every(step)` in `zone` inside `[lo, hi]` (fractional days), as instants in days: d3's
/// `interval.range(lo, hi + 1ms)`. The ends are rounded to whole milliseconds once, as d3's dates are, so a tick on
/// an end is never lost to float division.
pub fn every_instants(
    unit: Unit,
    step: i64,
    zone: &TimeZone,
    lo: f64,
    hi: f64,
) -> Result<Vec<f64>, String> {
    let interval =
        Interval::every(unit, step as f64, zone).expect("a tick interval steps at least 1");
    // d3 keeps stepping dates JavaScript holds to year 275760; the calendar here ends at year 9999.
    let range = interval.range(ms_of(lo), ms_of(hi) + 1).map_err(|why| {
        diagnostic::refuse!("a tick step of {step} {unit:?} reaching past the year 9999 the calendar holds is not supported: {why}")
    })?;
    Ok(range.into_iter().map(|at| at as f64 / MS_PER_DAY).collect())
}

/// Every tick of an authored `interval` inside `[lo, hi]`, as instants: in the [`ladder_zone`], or in UTC.
pub fn interval_instants(
    interval: UtcTickInterval,
    lo: f64,
    hi: f64,
    zone: Option<&TimeZone>,
) -> Result<Vec<f64>, String> {
    let (zone, week_start) = ladder_clock(zone);
    let (unit, step) = interval.unit_step(week_start);
    every_instants(unit, step, &zone, lo, hi)
}

/// Production's `_TEMPORAL_TICK_INTERVAL` (`type_inference.py:132-138`): the
/// d3 interval and the table step Vega-Lite's `tickCount: {interval, step}`
/// names for each bucketed grain. `yearquarter` is a monthly interval whose
/// table step is 3.
fn tick_interval_entry(unit: TimeUnit) -> (fn(i64) -> UtcTickInterval, i64) {
    match unit {
        TimeUnit::Year => (UtcTickInterval::Year, 1),
        TimeUnit::YearQuarter => (UtcTickInterval::Month, 3),
        TimeUnit::YearMonth => (UtcTickInterval::Month, 1),
        TimeUnit::YearWeek => (UtcTickInterval::Week, 1),
        TimeUnit::YearMonthDate => (UtcTickInterval::Day, 1),
    }
}

/// The interval of production's default cadence: with no authored
/// `ticks.time_unit` and no injected `values`, `tickCount: {interval, step}`
/// comes from the resolved label grain through the table's own step
/// (`type_inference.py:1049-1052`), so Vega ticks the calendar boundaries of
/// that grain whether or not a row sits on them.
pub fn native_tick_interval(label_tu: TimeUnit) -> UtcTickInterval {
    let (interval, step) = tick_interval_entry(label_tu);
    interval(step)
}

/// What an axis authors to choose where its ticks fall: production's
/// `axis.tickCount`, either `{interval, step}` from `ticks.time_unit` or the
/// bare number from `ticks.count` (`type_inference.py:417-418`,
/// `:1033-1054`). The two are exclusive (`DimensionTicksStyle`'s
/// `AtMostOneOf`), and an unauthored axis has neither.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthoredTickCadence {
    Interval(UtcTickInterval),
    Count(usize),
    /// A count d3 takes as is, with no `tickMinStep` cap: the ten ticks of a
    /// null interval.
    UncappedCount(usize),
}

impl AuthoredTickCadence {
    /// The ticks of the cadence on a bucketed axis, as instants in days: the scale is UTC and Vega-Lite gives the
    /// axis the grain's `tickMinStep`, so a number `tickCount` is capped at `floor(span / minStep) + 1`
    /// (vega-scale `tickCount`), the ladder's 2-day rung strides epoch days (`utcTicks`) and
    /// a span of a couple of days ticks the hours.
    pub fn bucketed_ticks(self, grain: TimeUnit, extent: (f64, f64)) -> Result<Vec<f64>, String> {
        let (lo, hi) = extent;
        match self {
            Self::Interval(interval) => interval_instants(interval, lo, hi, None),
            Self::Count(count) | Self::UncappedCount(count) => {
                let capped = match self {
                    Self::Count(_) => count.min(min_step_ticks(hi - lo, tick_min_step_days(grain))),
                    _ => count,
                };
                if capped == 0 {
                    return Ok(Vec::new());
                }
                Ok(last_of_each_second(&d3rs::time::utc_ticks(
                    ms_of(lo),
                    ms_of(hi),
                    capped,
                )?))
            }
        }
    }
}

/// vega-scale `tickCount`'s cap, `floor(span / minStep || 1) + 1`, with both
/// lengths in whole milliseconds as JavaScript holds them.
pub fn min_step_ticks(span_days: f64, step_days: f64) -> usize {
    let ratio = (span_days * MS_PER_DAY).round() / (step_days * MS_PER_DAY).round();
    (if ratio == 0.0 || ratio.is_nan() {
        1.0
    } else {
        ratio
    })
    .floor() as usize
        + 1
}

/// The `tickMinStep` Vega-Lite compiles for a bucketed grain, in days: the
/// span of one `timeUnit` unit from 2001-01-01 (`datetime(2001, 1, 1) -
/// datetime(2001, 0, 1)` for a month).
fn tick_min_step_days(grain: TimeUnit) -> f64 {
    match grain {
        TimeUnit::Year => 365.0,
        TimeUnit::YearQuarter => 90.0,
        TimeUnit::YearMonth => 31.0,
        TimeUnit::YearWeek => 7.0,
        TimeUnit::YearMonthDate => 1.0,
    }
}

/// The grains `_TEMPORAL_TICK_INTERVAL` maps to a tick interval.
const AUTHORED_TICK_GRAINS: [&str; 5] = [
    "year",
    "yearquarter",
    "yearmonth",
    "yearweek",
    "yearmonthdate",
];

/// `ERR-TICKS-TIME-UNIT-NO-CADENCE` (`type_inference.py:1085-1099`): `time_unit` names no grain of the table.
pub fn no_cadence(time_unit: &str) -> Diagnostic {
    diag::err_ticks_time_unit_no_cadence(time_unit, AUTHORED_TICK_GRAINS.join(", "))
}

impl AuthoredTickCadence {
    /// What an authored `ticks.time_unit` and `ticks.step` ask for (`type_inference.py:1026-1052`): only the table's
    /// interval is read, so a quarter is a monthly interval whose table step of 3 belongs to the default cadence
    /// ([`native_tick_interval`]). A step below 1 is the bare interval, a negative one a null interval whose ticks
    /// are d3's default ten, a fraction floors like d3's `every`.
    pub fn authored(unit: TimeUnit, step: Option<f64>) -> Self {
        let interval = tick_interval_entry(unit).0;
        match step {
            Some(s) if s < 0.0 => Self::UncappedCount(10),
            Some(s) if s >= 1.0 => Self::Interval(interval(s as i64)),
            _ => Self::Interval(interval(1)),
        }
    }
}
