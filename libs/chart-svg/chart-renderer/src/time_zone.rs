//! The board's `regional.time_zone`: the zone an instant prints its wall clock
//! in. Rows keep true instants; the zone applies only when text is printed.
//!
//! A tz-aware value (an ISO timestamp carrying `Z` or an offset) is an instant
//! and prints in the board zone; a naive one is a wall-clock time and prints as
//! written. [`is_aware`] is the one place that tells them apart.
//!
//! [`with_time_zone`] opens the zone for one render; outside any scope the zone
//! is UTC.

use std::cell::RefCell;

use jiff::tz::{Offset, TimeZone};
use jiff::Timestamp;

thread_local! {
    static ACTIVE: RefCell<TimeZone> = RefCell::new(TimeZone::UTC);
}

struct Restore(Option<TimeZone>);

impl Drop for Restore {
    fn drop(&mut self) {
        if let Some(zone) = self.0.take() {
            ACTIVE.with(|active| *active.borrow_mut() = zone);
        }
    }
}

pub fn with_time_zone<T>(zone: &TimeZone, render: impl FnOnce() -> T) -> T {
    let previous = ACTIVE.with(|active| active.replace(zone.clone()));
    let _restore = Restore(Some(previous));
    render()
}

pub fn active_time_zone() -> TimeZone {
    ACTIVE.with(|active| active.borrow().clone())
}

/// Whether `text` is a tz-aware timestamp: a time of day followed by `Z` or an
/// offset. A bare date or a timestamp without one is naive.
pub fn is_aware(text: &str) -> bool {
    text.len() > 10 && text.get(11..).is_some_and(|clock| clock.contains(['Z', '+', '-']))
}

/// Whether a column of date texts holds instants the board zone reads: it has a
/// text and every one is [`is_aware`]. A bare date or naive timestamp beside them
/// keeps the whole column on its written wall clock.
pub fn all_aware<'a>(texts: impl IntoIterator<Item = &'a str>) -> bool {
    let mut texts = texts.into_iter().peekable();
    texts.peek().is_some() && texts.all(is_aware)
}

/// UTC needs no tz database and converts every instant JavaScript holds.
fn is_utc(zone: &TimeZone) -> bool {
    *zone == TimeZone::UTC
}

/// The zone's offset at the instant `utc_ms` (milliseconds since the epoch).
fn offset_at(zone: &TimeZone, utc_ms: i64) -> Result<Offset, String> {
    if is_utc(zone) {
        return Ok(Offset::UTC);
    }
    let at = Timestamp::from_millisecond(utc_ms)
        .map_err(|_| format!("the instant {utc_ms} ms lies outside the years regional.time_zone converts"))?;
    Ok(zone.to_offset(at))
}

/// The board zone's wall clock at the instant `utc_ms`, in milliseconds on the
/// same epoch reckoning (the wall clock read as if it were UTC).
pub fn wall_ms(utc_ms: i64) -> Result<i64, String> {
    Ok(utc_ms + i64::from(offset_at(&active_time_zone(), utc_ms)?.seconds()) * 1000)
}

/// [`wall_ms`] of an instant in fractional days since the epoch.
pub fn wall_day(at: f64) -> Result<f64, String> {
    const MS_PER_DAY: f64 = 86_400_000.0;
    let zone = active_time_zone();
    if is_utc(&zone) || !at.is_finite() {
        return Ok(at);
    }
    let offset = offset_at(&zone, (at * MS_PER_DAY).round() as i64)?;
    Ok(at + f64::from(offset.seconds()) / 86_400.0)
}

/// The instant whose board-zone wall clock is `wall_ms`; a wall clock a
/// transition skips or repeats resolves as JavaScript's local `Date` does:
/// a skipped one reads on the offset before the gap (02:30 EST is 03:30 EDT),
/// a repeated one takes the earlier instant.
pub fn instant_ms(wall_ms: i64) -> Result<i64, String> {
    let zone = active_time_zone();
    if is_utc(&zone) {
        return Ok(wall_ms);
    }
    let wall = Timestamp::from_millisecond(wall_ms)
        .map_err(|_| format!("the wall clock {wall_ms} ms lies outside the years regional.time_zone converts"))?
        .to_zoned(TimeZone::UTC)
        .datetime();
    let zoned = zone.to_ambiguous_zoned(wall).compatible().map_err(|e| e.to_string())?;
    Ok(zoned.timestamp().as_millisecond())
}

/// strftime's `%z` and `%Z` of the instant `utc_ms` in the board zone, e.g.
/// `-0500` and `EST`.
pub fn zone_names(utc_ms: i64) -> Result<(String, String), String> {
    let zone = active_time_zone();
    let offset = offset_at(&zone, utc_ms)?;
    let seconds = offset.seconds();
    let sign = if seconds < 0 { '-' } else { '+' };
    let minutes = seconds.unsigned_abs() / 60;
    let z = format!("{sign}{:02}{:02}", minutes / 60, minutes % 60);
    let name = if is_utc(&zone) {
        "UTC".to_string()
    } else {
        let at = Timestamp::from_millisecond(utc_ms).map_err(|e| e.to_string())?;
        zone.to_offset_info(at).abbreviation().to_string()
    };
    Ok((z, name))
}

thread_local! {
    static EXPRESSION_LOCAL: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Run `eval` with a `labels.expr`'s JavaScript-local time in the board zone
/// when its instants are `aware`, else in UTC, where a naive value's wall
/// clock reads as written.
pub fn with_expression_local<T>(aware: bool, eval: impl FnOnce() -> T) -> T {
    let previous = EXPRESSION_LOCAL.with(|local| local.replace(aware));
    struct Reset(bool);
    impl Drop for Reset {
        fn drop(&mut self) {
            EXPRESSION_LOCAL.with(|local| local.set(self.0));
        }
    }
    let _reset = Reset(previous);
    eval()
}

/// Whether a `labels.expr` reads JavaScript-local time in the board zone: inside
/// [`with_expression_local`] over aware instants.
pub fn expression_local() -> bool {
    EXPRESSION_LOCAL.with(std::cell::Cell::get)
}

/// The JavaScript-local offset of the instant `ms` in milliseconds: the board
/// zone's inside [`with_expression_local`] over aware instants, else 0.
pub fn expression_local_offset_ms(ms: f64) -> Result<f64, String> {
    if !ms.is_finite() || !expression_local() {
        return Ok(0.0);
    }
    Ok(f64::from(offset_at(&active_time_zone(), ms as i64)?.seconds()) * 1000.0)
}

/// The instant a JavaScript-local wall clock `wall` (milliseconds) names; see
/// [`expression_local_offset_ms`].
pub fn expression_local_instant_ms(wall: f64) -> Result<f64, String> {
    if !wall.is_finite() || !expression_local() {
        return Ok(wall);
    }
    Ok(instant_ms(wall as i64)? as f64 + wall.fract())
}

/// The payload's IANA zone name, read from the compiled-in tz database.
pub fn deserialize<'de, D: serde::Deserializer<'de>>(deserializer: D) -> Result<TimeZone, D::Error> {
    let name: String = serde::Deserialize::deserialize(deserializer)?;
    // The tzdb's aliases of UTC need no database and convert every instant.
    if matches!(name.as_str(), "UTC" | "Etc/UTC" | "Etc/UCT" | "UCT" | "Etc/Universal" | "Universal" | "Etc/Zulu" | "Zulu") {
        return Ok(TimeZone::UTC);
    }
    TimeZone::get(&name).map_err(|e| serde::de::Error::custom(format!("time_zone {name:?}: {e}")))
}

/// The zone a payload without `time_zone` prints in.
pub fn utc() -> TimeZone {
    TimeZone::UTC
}

/// Holds `name` as the board zone until dropped (tests that print instants).
#[cfg(test)]
pub(crate) fn zone_scope(name: &str) -> impl Drop {
    let zone = TimeZone::get(name).expect("a bundled zone");
    Restore(Some(ACTIVE.with(|active| active.replace(zone))))
}

#[cfg(test)]
mod tests {
    use super::*;

    const MAR_10_2024_06_UTC: i64 = 1_710_050_400_000;

    #[test]
    fn new_york_crosses_into_daylight_time_on_march_10_2024() {
        let _ny = zone_scope("America/New_York");
        let hour = 3_600_000;
        // 06:00Z is 01:00 EST; 07:00Z is 03:00 EDT (02:00 never happens).
        assert_eq!(wall_ms(MAR_10_2024_06_UTC).unwrap(), MAR_10_2024_06_UTC - 5 * hour);
        assert_eq!(wall_ms(MAR_10_2024_06_UTC + hour).unwrap(), MAR_10_2024_06_UTC - 3 * hour);
        assert_eq!(zone_names(MAR_10_2024_06_UTC).unwrap(), ("-0500".into(), "EST".into()));
        assert_eq!(zone_names(MAR_10_2024_06_UTC + hour).unwrap(), ("-0400".into(), "EDT".into()));
        assert_eq!(instant_ms(MAR_10_2024_06_UTC - 3 * hour).unwrap(), MAR_10_2024_06_UTC + hour);
    }

    #[test]
    fn an_aware_timestamp_prints_on_its_board_zone_date() {
        let _ny = zone_scope("America/New_York");
        assert_eq!(crate::time_axis::parse_iso_date("2024-03-10T03:00:00Z"), Some((2024, 3, 9)));
        assert_eq!(crate::time_axis::parse_iso_date("2024-03-10T03:00:00"), Some((2024, 3, 10)));
    }

    #[test]
    fn a_skipped_wall_clock_reads_the_later_offset_and_a_repeated_one_the_earlier() {
        let _ny = zone_scope("America/New_York");
        let ms = |s: &str| s.parse::<Timestamp>().unwrap().as_millisecond();
        // 02:30 on 2024-03-10 never happens in New York; 01:30 on 2024-11-03 happens twice.
        assert_eq!(instant_ms(ms("2024-03-10T02:30:00Z")).unwrap(), ms("2024-03-10T07:30:00Z"));
        assert_eq!(instant_ms(ms("2024-11-03T01:30:00Z")).unwrap(), ms("2024-11-03T05:30:00Z"));
    }

    #[test]
    fn outside_a_scope_the_zone_is_utc() {
        assert_eq!(wall_ms(MAR_10_2024_06_UTC).unwrap(), MAR_10_2024_06_UTC);
        assert_eq!(zone_names(0).unwrap(), ("+0000".into(), "UTC".into()));
    }

    #[test]
    fn only_a_clock_with_an_offset_is_aware() {
        assert!(is_aware("2024-03-10T06:00:00Z"));
        assert!(is_aware("2024-03-10T06:00:00+00:00"));
        assert!(is_aware("2024-03-10 01:00:00-05:00"));
        assert!(!is_aware("2024-03-10T06:00:00"));
        assert!(!is_aware("2024-03-10"));
    }
}
