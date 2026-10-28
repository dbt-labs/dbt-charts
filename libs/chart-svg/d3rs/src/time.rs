//! d3-time 3.1.0's local intervals and `timeTicks`, with a jiff `TimeZone` as the runtime's local zone. d3
//! reads and sets a `Date`'s local fields; V8 resolves a wall time that a transition skips or repeats as
//! jiff's `compatible` does (a gap moves forward, a fold takes the earlier instant). Instants are epoch
//! milliseconds, a JS `Date`'s value.

use jiff::civil::{Date, DateTime, Time};
use jiff::tz::TimeZone;
use jiff::{Span, Timestamp};

use crate::ticks::d3_tick_step;

const SECOND: i64 = 1_000;
const MINUTE: i64 = 60 * SECOND;
const HOUR: i64 = 60 * MINUTE;
const DAY: i64 = 24 * HOUR;
const WEEK: i64 = 7 * DAY;
const MONTH: i64 = 30 * DAY;
const YEAR: i64 = 365 * DAY;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Unit {
    Millisecond,
    Second,
    Minute,
    Hour,
    Day,
    /// The week that starts on this weekday, 0 = Sunday (`timeSunday` .. `timeSaturday`).
    Week(u8),
    Month,
    Year,
}

#[derive(Clone, Debug)]
pub struct Interval {
    unit: Unit,
    step: i64,
    zone: TimeZone,
}

fn local(zone: &TimeZone, t: i64) -> Result<DateTime, String> {
    let at = Timestamp::from_millisecond(t).map_err(|e| format!("instant {t} ms: {e}"))?;
    Ok(zone.to_datetime(at))
}

fn instant(zone: &TimeZone, wall: DateTime) -> Result<i64, String> {
    let at = zone.to_ambiguous_timestamp(wall).compatible().map_err(|e| format!("wall time {wall}: {e}"))?;
    Ok(at.as_millisecond())
}

fn offset_ms(zone: &TimeZone, t: i64) -> Result<i64, String> {
    let at = Timestamp::from_millisecond(t).map_err(|e| format!("instant {t} ms: {e}"))?;
    Ok(i64::from(zone.to_offset(at).seconds()) * SECOND)
}

fn add_days(date: Date, days: i64) -> Result<Date, String> {
    date.checked_add(Span::new().days(days)).map_err(|e| format!("{date} + {days} days: {e}"))
}

fn midnight(zone: &TimeZone, date: Date) -> Result<i64, String> {
    instant(zone, date.to_datetime(Time::midnight()))
}

/// JS `setFullYear(year, month0, day)`: a day past the month's end rolls into the next month.
fn js_date(year: i64, month0: i64, day: i64) -> Result<Date, String> {
    let (year, month0) = (year + month0.div_euclid(12), month0.rem_euclid(12));
    let year = i16::try_from(year).map_err(|_| format!("year {year} is out of range"))?;
    let first = Date::new(year, month0 as i8 + 1, 1).map_err(|e| e.to_string())?;
    add_days(first, day - 1)
}

impl Unit {
    fn floor(self, zone: &TimeZone, t: i64) -> Result<i64, String> {
        let sub_second = || -> Result<(DateTime, i64), String> {
            let wall = local(zone, t)?;
            Ok((wall, i64::from(wall.millisecond())))
        };
        match self {
            Unit::Millisecond => Ok(t),
            Unit::Second => Ok(t - t.rem_euclid(SECOND)),
            Unit::Minute => {
                let (wall, ms) = sub_second()?;
                Ok(t - ms - i64::from(wall.second()) * SECOND)
            }
            Unit::Hour => {
                let (wall, ms) = sub_second()?;
                Ok(t - ms - i64::from(wall.second()) * SECOND - i64::from(wall.minute()) * MINUTE)
            }
            Unit::Day => midnight(zone, local(zone, t)?.date()),
            Unit::Week(start) => {
                let date = local(zone, t)?.date();
                let back = (i64::from(date.weekday().to_sunday_zero_offset()) + 7 - i64::from(start)) % 7;
                midnight(zone, add_days(date, -back)?)
            }
            Unit::Month => midnight(zone, local(zone, t)?.date().first_of_month()),
            Unit::Year => {
                let date = local(zone, t)?.date();
                midnight(zone, js_date(i64::from(date.year()), 0, 1)?)
            }
        }
    }

    fn offset(self, zone: &TimeZone, t: i64, step: i64) -> Result<i64, String> {
        let shifted = |date: fn(Date, i64) -> Result<Date, String>| -> Result<i64, String> {
            let wall = local(zone, t)?;
            instant(zone, date(wall.date(), step)?.to_datetime(wall.time()))
        };
        match self {
            Unit::Millisecond => Ok(t + step),
            Unit::Second => Ok(t + step * SECOND),
            Unit::Minute => Ok(t + step * MINUTE),
            Unit::Hour => Ok(t + step * HOUR),
            Unit::Day => shifted(add_days),
            Unit::Week(_) => shifted(|date, step| add_days(date, step * 7)),
            Unit::Month => shifted(|date, step| {
                js_date(i64::from(date.year()), i64::from(date.month()) - 1 + step, i64::from(date.day()))
            }),
            Unit::Year => shifted(|date, step| {
                js_date(i64::from(date.year()) + step, i64::from(date.month()) - 1, i64::from(date.day()))
            }),
        }
    }

    /// The field `every(step)` keeps the multiples of. A week has none: d3 counts its weeks from the epoch's
    /// (`interval.count(0, date)`), in local days, so a transition does not shorten one.
    fn field(self, zone: &TimeZone, t: i64) -> Result<i64, String> {
        let wall = || local(zone, t);
        Ok(match self {
            Unit::Second => t.div_euclid(SECOND).rem_euclid(60),
            Unit::Minute => i64::from(wall()?.minute()),
            Unit::Hour => i64::from(wall()?.hour()),
            Unit::Day => i64::from(wall()?.day()) - 1,
            Unit::Month => i64::from(wall()?.month()) - 1,
            Unit::Week(_) => {
                let (start, end) = (self.floor(zone, 0)?, self.floor(zone, t)?);
                (end - start + offset_ms(zone, end)? - offset_ms(zone, start)?).div_euclid(WEEK)
            }
            Unit::Millisecond | Unit::Year => unreachable!("a millisecond or year step floors on its own multiples"),
        })
    }
}

impl Interval {
    /// `unit.every(step)`: `None` where d3 returns null (a step below 1 or not finite).
    pub fn every(unit: Unit, step: f64, zone: &TimeZone) -> Option<Interval> {
        let step = step.floor();
        (step.is_finite() && step > 0.0).then(|| Interval { unit, step: step as i64, zone: zone.clone() })
    }

    fn keeps(&self, t: i64) -> Result<bool, String> {
        Ok(self.unit.field(&self.zone, t)? % self.step == 0)
    }

    /// `interval.floor`.
    pub fn floor(&self, t: i64) -> Result<i64, String> {
        let (zone, step) = (&self.zone, self.step);
        match self.unit {
            _ if step == 1 => self.unit.floor(zone, t),
            Unit::Millisecond => Ok(t.div_euclid(step) * step),
            Unit::Year => {
                let year = i64::from(local(zone, t)?.year());
                midnight(zone, js_date(year.div_euclid(step) * step, 0, 1)?)
            }
            unit => {
                let mut t = t;
                loop {
                    t = unit.floor(zone, t)?;
                    if self.keeps(t)? {
                        return Ok(t);
                    }
                    t -= 1;
                }
            }
        }
    }

    fn next(&self, t: i64) -> Result<i64, String> {
        let (zone, step) = (&self.zone, self.step);
        match self.unit {
            _ if step == 1 => self.unit.offset(zone, t, 1),
            Unit::Millisecond | Unit::Year => self.unit.offset(zone, t, step),
            unit => {
                let mut t = t;
                loop {
                    t = unit.offset(zone, t, 1)?;
                    if self.keeps(t)? {
                        return Ok(t);
                    }
                }
            }
        }
    }

    /// `interval.ceil`.
    pub fn ceil(&self, t: i64) -> Result<i64, String> {
        self.floor(self.next(self.floor(t - 1)?)?)
    }

    /// `interval.range(start, stop)`: every boundary in `[start, stop)`.
    pub fn range(&self, start: i64, stop: i64) -> Result<Vec<i64>, String> {
        let mut out = Vec::new();
        let mut at = self.ceil(start)?;
        if at >= stop {
            return Ok(out);
        }
        loop {
            out.push(at);
            let previous = at;
            at = self.floor(self.next(at)?)?;
            if !(previous < at && at < stop) {
                return Ok(out);
            }
        }
    }
}

/// d3-time `timeTickInterval(start, stop, count)` with the week rung starting on `week_start` (0 = Sunday;
/// d3's own is `timeSunday`). `None` where d3 returns null.
pub fn tick_interval(start: i64, stop: i64, count: usize, zone: &TimeZone, week_start: u8) -> Option<Interval> {
    let rungs: [(Unit, f64, i64); 18] = [
        (Unit::Second, 1.0, SECOND),
        (Unit::Second, 5.0, 5 * SECOND),
        (Unit::Second, 15.0, 15 * SECOND),
        (Unit::Second, 30.0, 30 * SECOND),
        (Unit::Minute, 1.0, MINUTE),
        (Unit::Minute, 5.0, 5 * MINUTE),
        (Unit::Minute, 15.0, 15 * MINUTE),
        (Unit::Minute, 30.0, 30 * MINUTE),
        (Unit::Hour, 1.0, HOUR),
        (Unit::Hour, 3.0, 3 * HOUR),
        (Unit::Hour, 6.0, 6 * HOUR),
        (Unit::Hour, 12.0, 12 * HOUR),
        (Unit::Day, 1.0, DAY),
        (Unit::Day, 2.0, 2 * DAY),
        (Unit::Week(week_start), 1.0, WEEK),
        (Unit::Month, 1.0, MONTH),
        (Unit::Month, 3.0, 3 * MONTH),
        (Unit::Year, 1.0, YEAR),
    ];
    let target = (stop - start).abs() as f64 / count as f64;
    let i = rungs.partition_point(|&(_, _, duration)| duration as f64 <= target);
    if i == rungs.len() {
        let step = d3_tick_step(start as f64 / YEAR as f64, stop as f64 / YEAR as f64, count);
        return Interval::every(Unit::Year, step, zone);
    }
    if i == 0 {
        // JavaScript's `Math.max` keeps a NaN step, which `every` refuses.
        let step = d3_tick_step(start as f64, stop as f64, count);
        return Interval::every(Unit::Millisecond, if step.is_nan() { step } else { step.max(1.0) }, zone);
    }
    let (below, above) = (rungs[i - 1].2 as f64, rungs[i].2 as f64);
    let (unit, step, _) = rungs[if target / below < above / target { i - 1 } else { i }];
    Interval::every(unit, step, zone)
}

/// d3-time `timeTicks(start, stop, count)`: the boundaries of [`tick_interval`] in `[start, stop]`.
pub fn ticks(start: i64, stop: i64, count: usize, zone: &TimeZone, week_start: u8) -> Result<Vec<i64>, String> {
    let (lo, hi) = (start.min(stop), start.max(stop));
    let mut out = match tick_interval(lo, hi, count, zone, week_start) {
        Some(interval) => interval.range(lo, hi + 1)?,
        None => Vec::new(),
    };
    if stop < start {
        out.reverse();
    }
    Ok(out)
}
