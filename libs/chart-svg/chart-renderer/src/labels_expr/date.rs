//! JavaScript `Date` arithmetic (local time is the board zone over aware instants, else UTC), Vega's
//! multi-scale `timeFormat`, and the d3-time interval helpers vega-time builds on. d3's `timeFormat` and
//! `timeParse` are `d3rs::time_format`.

use d3rs::calendar::{breakdown, days_from_civil, make_time, time_clip, MS_PER_DAY};
use d3rs::time_format::Locale;

use super::val::{EvalError, Res, Text};

/// What `Date.prototype.toString` prints: the engine's own English, whatever the board's time locale.
const JS_SHORT_DAYS: [&str; 7] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const JS_SHORT_MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/// `Date.prototype.toString` in UTC.
pub fn to_date_string(ms: f64) -> String {
    let Some(d) = breakdown(ms) else { return "Invalid Date".into() };
    let year = if d.year < 0 { format!("-{:04}", -d.year) } else { format!("{:04}", d.year) };
    format!(
        "{} {} {:02} {year} {:02}:{:02}:{:02} GMT+0000 (Coordinated Universal Time)",
        JS_SHORT_DAYS[d.weekday as usize], JS_SHORT_MONTHS[d.month as usize], d.day, d.hour, d.minute, d.second
    )
}

/// `Date.parse` / `new Date(string)`: V8's parser, `js_date`'s, which also reads a column's dates. A
/// string naming no zone reads on the local clock.
pub fn parse_date(s: &str) -> Res<f64> {
    // The parser tokenizes the whole string first: a token and its word take about 130 bytes.
    super::val::charge(s.len().saturating_mul(130))?;
    match crate::js_date::parse_ms_zoned(s) {
        Some((ms, true)) => Ok(ms),
        Some((wall, false)) => from_local_wall(wall),
        None => Ok(f64::NAN),
    }
}

/// `ms` as the UTC-reckoned wall clock of JavaScript's local time: what d3's
/// local intervals step on.
pub fn to_local_wall(ms: f64) -> Res<f64> {
    Ok(ms + local_offset_ms(ms)?)
}

/// The instant a local wall clock names: the inverse of [`to_local_wall`].
pub fn from_local_wall(wall: f64) -> Res<f64> {
    crate::time_zone::expression_local_instant_ms(wall).map_err(EvalError::Unmodeled)
}

/// Vega's multi-scale `timeFormat(date)` (no format string): the coarsest unit the instant sits on, by
/// vega-format's `timeMultiFormat`, with `overrides` replacing the per-unit specifiers.
pub fn multi_format(raw: f64, utc: bool, overrides: &[(String, String)], locale: &Locale) -> Res<Text> {
    let ms = time_clip(raw);
    let spec = |key: &str, default: &str| overrides.iter().find(|(k, _)| k == key).map_or(default, |(_, v)| v.as_str()).to_string();
    let shift = if utc { 0.0 } else { local_offset_ms(ms)? };
    let (wall, raw_wall) = (ms + shift, raw + shift);
    let below = |unit: Unit| unit.floor(wall) < raw_wall;
    let chosen = if below(Unit::Seconds) {
        spec("milliseconds", ".%L")
    } else if below(Unit::Minutes) {
        spec("seconds", ":%S")
    } else if below(Unit::Hours) {
        spec("minutes", "%I:%M")
    } else if below(Unit::Date) {
        spec("hours", "%I %p")
    } else if below(Unit::Month) {
        if below(Unit::Week) {
            overrides.iter().find(|(k, _)| k == "date" || k == "day").map_or("%a %d".to_string(), |(_, v)| v.clone())
        } else {
            spec("week", "%b %d")
        }
    } else if below(Unit::Year) {
        if below(Unit::Quarter) { spec("month", "%B") } else { spec("quarter", "%B") }
    } else {
        spec("year", "%Y")
    };
    time_format(&chosen, ms, utc, locale)
}

/// JavaScript's local offset at `ms` (`crate::time_zone::expression_local_offset_ms`).
pub fn local_offset_ms(ms: f64) -> Res<f64> {
    crate::time_zone::expression_local_offset_ms(ms).map_err(EvalError::Unmodeled)
}

/// `timeFormat(spec)(date)` / `utcFormat(spec)(date)`: local time reads the
/// expression's local zone, UTC reads UTC.
pub fn time_format(spec: &str, ms: f64, utc: bool, locale: &Locale) -> Res<Text> {
    super::val::charge(spec.len().saturating_mul(4))?;
    let offset_ms = match (utc, ms.is_finite()) {
        (true, _) => 0.0,
        (false, true) => local_offset_ms(ms)?,
        // An Invalid Date has no offset: d3 prints `%Z` as `+00NaN`.
        (false, false) => f64::NAN,
    };
    Text::new(d3rs::time_format::format(spec, ms, offset_ms, locale).map_err(EvalError::Throws)?)
}

/// `timeParse(spec)(text)` / `utcParse(spec)(text)`: a local parse of a text with no zone of its own reads its
/// fields on the expression's local clock.
pub fn time_parse(spec: &str, text: &str, locale: &Locale, utc: bool) -> Res<Option<f64>> {
    match d3rs::time_format::parse(spec, text, locale, utc).map_err(EvalError::Throws)? {
        Some(parsed) if parsed.wall => from_local_wall(parsed.ms).map(Some),
        parsed => Ok(parsed.map(|parsed| parsed.ms)),
    }
}

// ---------------------------------------------------------------------------------------------------------
// d3-time intervals (UTC), as vega-time's timeOffset/timeSequence/week/dayofyear use them.

#[derive(Clone, Copy, PartialEq)]
pub enum Unit {
    Year,
    Quarter,
    Month,
    Week,
    Date,
    Day,
    DayOfYear,
    Hours,
    Minutes,
    Seconds,
    Milliseconds,
}

impl Unit {
    /// An hour or finer steps the instant by a fixed duration, in local time as in UTC (d3-time).
    pub fn is_fixed(self) -> bool {
        matches!(self, Unit::Hours | Unit::Minutes | Unit::Seconds | Unit::Milliseconds)
    }

    pub fn parse(name: &str) -> Option<Self> {
        Some(match name {
            "year" => Unit::Year,
            "quarter" => Unit::Quarter,
            "month" => Unit::Month,
            "week" => Unit::Week,
            "date" => Unit::Date,
            "day" => Unit::Day,
            "dayofyear" => Unit::DayOfYear,
            "hours" => Unit::Hours,
            "minutes" => Unit::Minutes,
            "seconds" => Unit::Seconds,
            "milliseconds" => Unit::Milliseconds,
            _ => return None,
        })
    }

    fn base(self) -> Unit {
        match self {
            Unit::Quarter => Unit::Month,
            Unit::Date | Unit::Day | Unit::DayOfYear => Unit::Date,
            other => other,
        }
    }

    /// `interval.floor(date)`; a quarter is d3's `month.every(3)`, a filter that floors back to a passing month.
    pub fn floor(self, ms: f64) -> f64 {
        let Some(d) = breakdown(ms) else { return f64::NAN };
        match self.base() {
            Unit::Year => day_start(d.year, 0, 1),
            Unit::Month => {
                let mut floored = day_start(d.year, d.month, 1);
                if self == Unit::Quarter {
                    while breakdown(floored).is_some_and(|f| f.month % 3 != 0) {
                        floored = Unit::Month.floor(floored - 1.0);
                    }
                }
                floored
            }
            Unit::Week => day_start(d.year, d.month, d.day) - d.weekday as f64 * MS_PER_DAY,
            Unit::Date => day_start(d.year, d.month, d.day),
            Unit::Hours => (ms / 3_600_000.0).floor() * 3_600_000.0,
            Unit::Minutes => (ms / 60_000.0).floor() * 60_000.0,
            Unit::Seconds => (ms / 1000.0).floor() * 1000.0,
            _ => ms,
        }
    }

    fn offset_once(self, ms: f64, step: f64) -> f64 {
        let Some(d) = breakdown(ms) else { return f64::NAN };
        let time = ms - day_start(d.year, d.month, d.day);
        match self.base() {
            Unit::Year => make_time(d.year as f64 + step, d.month as f64, d.day as f64, 0.0, 0.0, 0.0, 0.0) + time,
            Unit::Month => make_time(d.year as f64, d.month as f64 + step, d.day as f64, 0.0, 0.0, 0.0, 0.0) + time,
            Unit::Week => ms + step * 7.0 * MS_PER_DAY,
            Unit::Date => ms + step * MS_PER_DAY,
            Unit::Hours => ms + step * 3_600_000.0,
            Unit::Minutes => ms + step * 60_000.0,
            Unit::Seconds => ms + step * 1000.0,
            _ => ms + step,
        }
    }

    /// `interval.offset(date, step)`; `None` is an absent step, which counts one.
    pub fn offset(self, ms: f64, step: Option<f64>) -> f64 {
        let step = step.map_or(1.0, f64::floor);
        if !ms.is_finite() {
            return f64::NAN;
        }
        if self == Unit::Quarter {
            // d3's filtered offset walks whole passing months: a NaN step walks none.
            let mut t = ms;
            let mut n = step;
            while n >= 1.0 {
                n -= 1.0;
                loop {
                    t = Unit::Month.offset_once(t, 1.0);
                    if breakdown(t).is_none_or(|d| d.month % 3 == 0) {
                        break;
                    }
                }
            }
            while n <= -1.0 {
                n += 1.0;
                loop {
                    t = Unit::Month.offset_once(t, -1.0);
                    if breakdown(t).is_none_or(|d| d.month % 3 == 0) {
                        break;
                    }
                }
            }
            return t;
        }
        time_clip(self.offset_once(ms, step))
    }

    /// `interval.ceil(date)`.
    fn ceil(self, ms: f64) -> f64 {
        if !ms.is_finite() {
            return f64::NAN;
        }
        let floored = self.floor(ms - 1.0);
        self.floor(self.offset(floored, None))
    }

    /// `interval.range(start, stop, step)`; `None` where it would hold more than `max` instants.
    pub fn range(self, start: f64, stop: f64, step: Option<f64>, max: usize) -> Option<Vec<f64>> {
        let step = step.map_or(1.0, f64::floor);
        let mut out = Vec::new();
        let mut t = self.ceil(start);
        if !(t < stop) || !(step > 0.0) {
            return Some(out);
        }
        loop {
            let previous = t;
            if out.len() == max {
                return None;
            }
            out.push(previous);
            t = self.floor(self.offset(previous, Some(step)));
            if !(previous < t && t < stop) {
                return Some(out);
            }
        }
    }

    /// `interval.count(start, end)`: boundaries crossed after `start` up to `end`.
    pub fn count(self, start: f64, end: f64) -> f64 {
        let (a, b) = (self.floor(start), self.floor(end));
        match self.base() {
            Unit::Year => match (breakdown(a), breakdown(b)) {
                (Some(da), Some(db)) => (db.year - da.year) as f64,
                _ => f64::NAN,
            },
            Unit::Month => match (breakdown(a), breakdown(b)) {
                (Some(da), Some(db)) => ((db.year - da.year) * 12 + db.month - da.month) as f64,
                _ => f64::NAN,
            },
            Unit::Week => ((b - a) / (7.0 * MS_PER_DAY)).floor(),
            Unit::Date => ((b - a) / MS_PER_DAY).floor(),
            Unit::Hours => ((b - a) / 3_600_000.0).floor(),
            Unit::Minutes => ((b - a) / 60_000.0).floor(),
            Unit::Seconds => ((b - a) / 1000.0).floor(),
            _ => (b - a).floor(),
        }
    }
}

/// Midnight UTC of the date `year`-`month0`-`day`, in ms.
fn day_start(year: i64, month0: i64, day: i64) -> f64 {
    days_from_civil(year as i32, month0 as u8 + 1, day as u8) as f64 * MS_PER_DAY
}

/// vega-time `utcdayofyear`.
pub fn day_of_year_vega(ms: f64) -> f64 {
    let Some(d) = breakdown(ms) else { return f64::NAN };
    let y = day_start(d.year, 0, 1);
    Unit::Date.count(y - 1.0, ms)
}

/// vega-time `utcweek`.
pub fn week_vega(ms: f64) -> f64 {
    let Some(d) = breakdown(ms) else { return f64::NAN };
    let y = day_start(d.year, 0, 1);
    Unit::Week.count(y - 1.0, ms)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_calendar_round_trips_and_names_the_weekday() {
        let t = make_time(2020.0, 0.0, 2.0, 10.0, 30.0, 0.0, 0.0);
        let d = breakdown(t).unwrap();
        assert_eq!((d.year, d.month, d.day, d.hour, d.minute, d.weekday), (2020, 0, 2, 10, 30, 4));
        assert_eq!(to_date_string(t), "Thu Jan 02 2020 10:30:00 GMT+0000 (Coordinated Universal Time)");
        assert_eq!(to_date_string(f64::NAN), "Invalid Date");
    }

    #[test]
    fn intervals_offset_floor_and_sequence_like_d3() {
        let jan31 = make_time(2020.0, 0.0, 31.0, 0.0, 0.0, 0.0, 0.0);
        assert_eq!(breakdown(Unit::Month.offset(jan31, Some(1.0))).map(|d| (d.month, d.day)), Some((2, 2)));
        let seq = Unit::Month.range(make_time(2020.0, 0.0, 15.0, 0.0, 0.0, 0.0, 0.0), make_time(2020.0, 3.0, 15.0, 0.0, 0.0, 0.0, 0.0), Some(1.0), 100).unwrap();
        assert_eq!(seq.iter().map(|t| breakdown(*t).unwrap().month).collect::<Vec<_>>(), vec![1, 2, 3]);
        assert_eq!(Unit::Quarter.offset(make_time(2020.0, 1.0, 10.0, 0.0, 0.0, 0.0, 0.0), Some(1.0)), make_time(2020.0, 3.0, 10.0, 0.0, 0.0, 0.0, 0.0));
        assert_eq!(day_of_year_vega(make_time(2020.0, 2.0, 1.0, 0.0, 0.0, 0.0, 0.0)), 61.0);
        assert_eq!(week_vega(make_time(2020.0, 0.0, 5.0, 0.0, 0.0, 0.0, 0.0)), 1.0);
    }
}

#[cfg(test)]
mod parse_corpus {
    use super::*;

    /// `new Date(s).getTime()` in V8 for every shape in `corpus/datestr.json` (`corpus/datestr.mjs`, under `TZ=UTC`).
    #[test]
    fn date_strings_parse_exactly_as_v8_parses_them() {
        let rows: Vec<(String, serde_json::Value)> = serde_json::from_str(include_str!("corpus/datestr.json")).unwrap();
        let bad: Vec<String> = rows
            .iter()
            .filter_map(|(s, want)| {
                let got = parse_date(s).unwrap();
                let want = want.as_f64();
                match want {
                    None if got.is_nan() => None,
                    Some(w) if got == w => None,
                    _ => Some(format!("{s:?}: V8 {want:?}, we {got}")),
                }
            })
            .collect();
        assert!(bad.is_empty(), "{}", bad.join("\n"));
    }
}
