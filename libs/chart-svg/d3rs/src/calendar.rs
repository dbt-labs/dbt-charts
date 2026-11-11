//! The proleptic Gregorian calendar and a JavaScript `Date`'s fields: the one calendar the engine and
//! d3rs's time ports stand on.
//!
//! The civil-date functions are Howard Hinnant's division-free algorithms. A JS `Date` is epoch
//! milliseconds (`f64`, NaN for Invalid Date) read as UTC fields; a local clock is the instant plus
//! the zone offset the caller resolved.

pub const MS_PER_DAY: f64 = 86_400_000.0;
const MAX_TIME: f64 = 8.64e15;

/// Days since 1970-01-01 of the civil date `y-m-d`.
pub fn days_from_civil(y: i32, m: u8, d: u8) -> i64 {
    let y = i64::from(y) - i64::from(m <= 2);
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (i64::from(m) + 9) % 12;
    let doy = (153 * mp + 2) / 5 + i64::from(d) - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// The civil date `(year, month 1-12, day)` of a day number since 1970-01-01.
pub fn civil_from_days(z: i64) -> (i32, u8, u8) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u8;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u8;
    ((y + i64::from(m <= 2)) as i32, m, d)
}

/// Day of the week of a civil date, 0 = Monday .. 6 = Sunday (1970-01-01 was a Thursday).
pub fn weekday(year: i32, month: u8, day: u8) -> u8 {
    (days_from_civil(year, month, day) + 3).rem_euclid(7) as u8
}

pub fn is_leap_year(year: i32) -> bool {
    (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
}

/// The days of `month` (1-12) in `year`; 0 for a month outside 1-12.
pub fn days_in_month(year: i32, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if is_leap_year(year) => 29,
        2 => 28,
        _ => 0,
    }
}

/// `"YYYY-MM-DD"` as written: four, two and two ASCII digits and a day the month has.
pub fn parse_ymd(s: &str) -> Option<(i32, u8, u8)> {
    let b = s.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return None;
    }
    let digits = |r: std::ops::Range<usize>| -> Option<i32> {
        let part = b.get(r)?;
        part.iter().all(u8::is_ascii_digit).then(|| part.iter().fold(0, |n, c| n * 10 + i32::from(c - b'0')))
    };
    let (y, m, d) = (digits(0..4)?, digits(5..7)?, digits(8..10)?);
    let (m, d) = (u8::try_from(m).ok()?, u8::try_from(d).ok()?);
    ((1..=12).contains(&m) && d >= 1 && u32::from(d) <= days_in_month(y, u32::from(m))).then_some((y, m, d))
}

/// A valid instant broken into calendar fields.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Dt {
    pub year: i64,
    /// 0-11.
    pub month: i64,
    pub day: i64,
    pub hour: i64,
    pub minute: i64,
    pub second: i64,
    pub milli: i64,
    /// 0 is Sunday.
    pub weekday: i64,
}

/// The UTC fields of `ms`; `None` for Invalid Date.
pub fn breakdown(ms: f64) -> Option<Dt> {
    if !ms.is_finite() || ms.abs() > MAX_TIME {
        return None;
    }
    let days = (ms / MS_PER_DAY).floor();
    let rest = (ms - days * MS_PER_DAY) as i64;
    let days = days as i64;
    let (year, month, day) = civil_from_days(days);
    Some(Dt {
        year: i64::from(year),
        month: i64::from(month) - 1,
        day: i64::from(day),
        hour: rest / 3_600_000,
        minute: rest / 60_000 % 60,
        second: rest / 1000 % 60,
        milli: rest % 1000,
        weekday: (days + 4).rem_euclid(7),
    })
}

fn to_integer(n: f64) -> f64 {
    if n.is_nan() { 0.0 } else { n.trunc() }
}

/// `Date.UTC`'s `TimeClip(MakeDate(MakeDay(y, m, d), MakeTime(h, mi, s, ms)))`, with the year as given (`Date.UTC`
/// maps 0-99 to 1900-1999; see [`full_year`]).
pub fn make_time(y: f64, mo: f64, d: f64, h: f64, mi: f64, s: f64, ms: f64) -> f64 {
    if ![y, mo, d, h, mi, s, ms].iter().all(|v| v.is_finite()) {
        return f64::NAN;
    }
    let (y, mo, d) = (to_integer(y), to_integer(mo), to_integer(d));
    let ym = y + (mo / 12.0).floor();
    if ym.abs() > 400_000.0 {
        return f64::NAN;
    }
    let month = mo.rem_euclid(12.0) as u8;
    let day = days_from_civil(ym as i32, month + 1, 1) as f64 + d - 1.0;
    let time = to_integer(h) * 3_600_000.0 + to_integer(mi) * 60_000.0 + to_integer(s) * 1000.0 + to_integer(ms);
    time_clip(day * MS_PER_DAY + time)
}

pub fn time_clip(t: f64) -> f64 {
    if !t.is_finite() || t.abs() > MAX_TIME { f64::NAN } else { t.trunc() + 0.0 }
}

/// The year argument of `new Date(y, ...)` and `Date.UTC(y, ...)`: 0-99 mean 1900-1999.
pub fn full_year(y: f64) -> f64 {
    if y.is_nan() {
        return y;
    }
    let t = y.trunc();
    if (0.0..=99.0).contains(&t) { 1900.0 + t } else { y }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn days_from_civil_anchors_the_epoch() {
        assert_eq!(days_from_civil(1970, 1, 1), 0);
        assert_eq!(days_from_civil(2026, 1, 1), 20454);
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        // 2000-03-01 is a well-known Hinnant test vector.
        assert_eq!(civil_from_days(11017), (2000, 3, 1));
        assert_eq!(civil_from_days(days_from_civil(2024, 2, 29) + 1), (2024, 3, 1));
    }

    #[test]
    fn the_civil_pair_round_trips_across_centuries() {
        for day in (-800_000..800_000).step_by(37) {
            let (y, m, d) = civil_from_days(day);
            assert_eq!(days_from_civil(y, m, d), day);
        }
    }

    #[test]
    fn weekday_stays_in_range_on_a_pre_1970_sunday() {
        // 1900-03-04 is a Sunday (verified against Python's `date.weekday()`).
        assert_eq!(weekday(1900, 3, 4), 6);
        assert_eq!(weekday(2024, 1, 1), 0);
        assert_eq!(weekday(1, 1, 1), 0);
        assert_eq!(weekday(2000, 1, 9), 6);
    }

    #[test]
    fn leap_years_and_month_lengths() {
        assert!(is_leap_year(2000) && is_leap_year(2024) && !is_leap_year(1900) && !is_leap_year(2023));
        assert_eq!([days_in_month(2024, 2), days_in_month(2023, 2), days_in_month(2023, 4), days_in_month(2023, 12)], [29, 28, 30, 31]);
        assert_eq!([days_in_month(2023, 0), days_in_month(2023, 13)], [0, 0]);
    }

    #[test]
    fn the_calendar_round_trips_and_names_the_weekday() {
        let t = make_time(2020.0, 0.0, 2.0, 10.0, 30.0, 0.0, 0.0);
        let d = breakdown(t).unwrap();
        assert_eq!((d.year, d.month, d.day, d.hour, d.minute, d.weekday), (2020, 0, 2, 10, 30, 4));
        assert!(breakdown(f64::NAN).is_none());
    }
}
