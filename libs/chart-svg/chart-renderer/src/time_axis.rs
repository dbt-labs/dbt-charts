//! The one temporal x axis of a chart, built in bind and read by every family that has a time x.
//!
//! Each distinct x value is parsed here once, by the only date parser in the engine, into its UTC
//! instant (an offset applied, millisecond precision) and the bucket of its grain. The raw text stays
//! the key rows, links and tooltips join on. The axis sorts a domain by instant, so no family orders
//! or compares dates as text. A value that parses as no date is [`Unreadable`], which every family
//! raises as `ERR-X-VALUE-NOT-A-DATE`.

use std::collections::HashMap;

use crate::diagnostic::Diagnostic;
use crate::families::temporal::{civil_from_days, days_from_civil, days_in_month, floor_day, instant_iso, next_bucket, TimePart, TimeUnit};

/// A date cell as days since the epoch, with any clock `datetime.fromisoformat` takes
/// after `[T ]` as the day's fraction and an optional `Z` or `±HH[[:]MM[[:]SS]]` offset. A clock without an
/// offset is UTC (the render pins the zone). Fractional seconds keep V8's
/// millisecond precision. `None` for any other shape, and for a day the month does not have.
pub fn date_text_day(text: &str) -> Option<f64> {
    read_clock(text).map(|(day, _)| day)
}

/// [`date_text_day`] with whether the wall clock (before any offset) has a nonzero hour, minute or second.
fn read_clock(text: &str) -> Option<(f64, bool)> {
    let (y, m, d) = parse_iso_date(text.get(..text.len().min(10))?)?;
    if text.len() <= 10 {
        return Some((days_from_civil(y, m, d) as f64, false));
    }
    if !matches!(text.as_bytes()[10], b'T' | b' ') {
        return None;
    }
    let clock = text.get(11..)?;
    let (clock, offset_minutes) = match clock.find(['Z', '+', '-']) {
        None => (clock, 0.0),
        Some(i) => {
            let (head, zone) = clock.split_at(i);
            let minutes = match zone.as_bytes()[0] {
                b'Z' if zone.len() == 1 => 0.0,
                sign @ (b'+' | b'-') => {
                    let [hours, minutes, seconds] = crate::families::x_type::clock_fields(&zone[1..], 23)?;
                    let total = f64::from(hours) * 60.0 + f64::from(minutes) + f64::from(seconds) / 60.0;
                    if sign == b'+' { total } else { -total }
                }
                _ => return None,
            };
            (head, minutes)
        }
    };
    let [hours, minutes, seconds] = crate::families::x_type::clock_fields(clock, 23)?;
    let millis = match clock.find(['.', ',']) {
        None => 0.0,
        Some(i) => format!("{:0<3}", &clock[i + 1..])[..3].parse::<f64>().ok()?,
    };
    // Whole milliseconds, so every spelling of one instant is the same float.
    let clock_ms = (i64::from(hours) * 3600 + i64::from(minutes) * 60 + i64::from(seconds)) * 1000 + millis as i64;
    let offset_ms = (offset_minutes * 60_000.0).round() as i64;
    let total_ms = days_from_civil(y, m, d) * 86_400_000 + clock_ms - offset_ms;
    Some((total_ms as f64 / 86_400_000.0, hours != 0 || minutes != 0 || seconds != 0))
}

/// Parse an ISO date string `"YYYY-MM-DD"` → `(year, month, day)`.
/// Also accepts `"YYYY-MM"` (day = 1), `"YYYY"` (month = 1, day = 1), and a
/// datetime `"YYYY-MM-DD[T ]…"` (date part; matches production's
/// _ISO_DATETIME_RE in time_unit_detect.py). Returns `None` for any other shape
/// and for a day the month does not have.
pub fn parse_iso_date(s: &str) -> Option<(i32, u8, u8)> {
    let b = s.as_bytes();
    if b.len() > 10 && (b[10] == b'T' || b[10] == b' ') {
        return parse_iso_date(&s[..10]);
    }
    match b.len() {
        10 if b[4] == b'-' && b[7] == b'-' => {
            let y = parse_digits4(&b[0..4])?;
            let m = parse_digits2(&b[5..7])?;
            let d = parse_digits2(&b[8..10])?;
            ((1..=12).contains(&m) && d >= 1 && u32::from(d) <= days_in_month(y, u32::from(m))).then_some((y, m, d))
        }
        7 if b[4] == b'-' => {
            let y = parse_digits4(&b[0..4])?;
            let m = parse_digits2(&b[5..7])?;
            (1..=12).contains(&m).then_some((y, m, 1))
        }
        4 => Some((parse_digits4(&b[0..4])?, 1, 1)),
        _ => None,
    }
}

fn parse_digits4(b: &[u8]) -> Option<i32> {
    b.iter().all(u8::is_ascii_digit).then(|| b.iter().fold(0, |n, c| n * 10 + i32::from(c - b'0')))
}

fn parse_digits2(b: &[u8]) -> Option<u8> {
    b.iter().all(u8::is_ascii_digit).then(|| (b[0] - b'0') * 10 + (b[1] - b'0'))
}

/// An x value the axis cannot place.
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum Unreadable {
    /// The text parses as no date or timestamp.
    NotADate(String),
    /// A timestamp whose UTC instant leaves years 0001 to 9999, the span the engine's dates print in.
    OutsideYears(String),
}

impl Unreadable {
    /// `ERR-X-VALUE-NOT-A-DATE` for a value that is no date; the years refusal for one the axis cannot print.
    pub(crate) fn diagnostic(&self, chart_id: &str, x_field: &str, family: &str) -> Diagnostic {
        match self {
            Self::NotADate(value) => Diagnostic::new(
                &crate::codes::ERR_X_VALUE_NOT_A_DATE,
                [
                    ("chart_id", serde_json::json!(chart_id)),
                    ("value", serde_json::json!(value)),
                    ("x_field", serde_json::json!(x_field)),
                ],
            ),
            Self::OutsideYears(_) => crate::refuse!(
                "{family}: a timestamp whose UTC instant falls outside years 0001 to 9999 on a time x axis is not supported"
            )
            .into(),
        }
    }
}

/// A temporal x value, parsed once. Every date computation reads these fields, never the text.
#[derive(Clone, Debug)]
pub(crate) struct Instant {
    /// The UTC instant in days (offset applied, millisecond precision): the ordering tiebreak and the last x.
    pub at: f64,
    /// Where a vertex sits: the grain's floor of the instant's UTC day, the part's cycle position, else `at`.
    pub pos: f64,
    /// The exclusive end of the bucket holding the instant: its grain's next bucket, or the part's span.
    pub end: Option<f64>,
    /// Vega's `timeunit` yields NaN for a year below 100 over a calendar grain.
    pub nan: bool,
    /// The text names an absolute instant: a bare date, or a timestamp carrying `Z` or an offset.
    pub absolute: bool,
    /// The text's wall clock has a nonzero hour, minute or second, whatever its offset.
    pub timed: bool,
    /// The text of the instant: a date as written, a timestamp as its UTC `YYYY-MM-DDTHH:MM:SS[.mmm]`.
    pub iso: String,
    /// The text of where the instant sits: the grain's bucket start as a date, a part axis's cycle instant, else `iso`.
    pub key: String,
}

/// The axis's grain: the calendar bucket or time part its values floor to.
#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct Grain {
    pub unit: Option<TimeUnit>,
    pub part: Option<TimePart>,
    /// A band x: the grain buckets it but Vega's NaN year rule does not apply.
    pub band: bool,
}

impl Instant {
    fn read(text: &str, grain: Grain) -> Result<Self, Unreadable> {
        let (at, timed) = read_clock(text).ok_or_else(|| Unreadable::NotADate(text.to_string()))?;
        let absolute = text.len() <= 10 || text.get(11..).is_some_and(|clock| clock.contains(['Z', '+', '-']));
        let floor = grain.unit.map(|tu| floor_day(at.floor() as i64, tu));
        let pos = match (floor, grain.part) {
            (Some(day), _) => day as f64,
            (None, Some(part)) => part.instant(at),
            (None, None) => at,
        };
        let nan = !grain.band && floor.is_some_and(|day| civil_from_days(day).0 < 100);
        let end = match (grain.unit, grain.part) {
            (Some(tu), _) => Some(day_after(pos, tu)),
            (None, Some(TimePart::HourOfDay)) => Some(pos + 1.0 / 24.0),
            (None, Some(TimePart::MonthOfYear)) => Some(day_after(pos, TimeUnit::YearMonth)),
            (None, Some(_)) => Some(pos + 1.0),
            (None, None) => None,
        };
        let iso = canonical_text(text, at)?;
        let key = match (grain.unit, grain.part) {
            (Some(_), _) => instant_iso_day(pos),
            (None, Some(_)) => instant_iso(pos),
            (None, None) => iso.clone(),
        };
        Ok(Self { at, pos, end, nan, absolute, timed, iso, key })
    }
}

/// The first day of the bucket after the one `start` opens.
fn day_after(start: f64, tu: TimeUnit) -> f64 {
    let (y, m, d) = next_bucket(civil_from_days(start as i64), tu);
    days_from_civil(y, m, d) as f64
}

/// `text` as the axis keys its instant: a date as it was written, a timestamp as its UTC clock.
fn canonical_text(text: &str, at: f64) -> Result<String, Unreadable> {
    if text.len() <= 10 {
        return Ok(text.to_string());
    }
    let (y, ..) = civil_from_days(at.floor() as i64);
    if !(1..=9999).contains(&y) {
        return Err(Unreadable::OutsideYears(text.to_string()));
    }
    Ok(timestamp_text(at))
}

fn instant_iso_day(day: f64) -> String {
    let (y, m, d) = civil_from_days(day.floor() as i64);
    format!("{y:04}-{m:02}-{d:02}")
}

/// `YYYY-MM-DDTHH:MM:SS[.mmm]` of a UTC instant.
fn timestamp_text(at: f64) -> String {
    let millis = (at * 86_400_000.0).round() as i64;
    let (day, ms) = (millis.div_euclid(86_400_000), millis.rem_euclid(86_400_000));
    let (y, m, d) = civil_from_days(day);
    let fraction = if ms % 1000 == 0 { String::new() } else { format!(".{:03}", ms % 1000) };
    format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}{fraction}", ms / 3_600_000, ms / 60_000 % 60, ms / 1000 % 60)
}

/// The distinct x values of a chart, each read once. Keyed by the raw text, which is what rows, links
/// and tooltips carry; sorted by instant on demand.
#[derive(Clone, Debug)]
pub(crate) struct TimeAxis {
    grain: Grain,
    /// A labeled bucket or year the x-type law rewrote to its ISO start; the raw label stays the key.
    rewritten: HashMap<String, String>,
    instants: HashMap<String, Instant>,
}

impl TimeAxis {
    pub(crate) fn new(grain: Grain, rewritten: HashMap<String, String>) -> Self {
        Self { grain, rewritten, instants: HashMap::new() }
    }

    pub(crate) fn grain(&self) -> Grain {
        self.grain
    }

    /// More labeled buckets and years the law rewrote, those of a layer's own x column.
    pub(crate) fn rewrite(&mut self, labels: impl IntoIterator<Item = (String, String)>) {
        self.rewritten.extend(labels);
    }

    /// Reads `key` unless it is held already.
    pub(crate) fn add(&mut self, key: &str) -> Result<(), Unreadable> {
        if self.instants.contains_key(key) {
            return Ok(());
        }
        let text = self.rewritten.get(key).map_or(key, String::as_str);
        let instant = Instant::read(text, self.grain).map_err(|e| match e {
            Unreadable::NotADate(_) => Unreadable::NotADate(key.to_string()),
            other => other,
        })?;
        self.instants.insert(key.to_string(), instant);
        Ok(())
    }

    /// The instant of a key `add` read.
    pub(crate) fn get(&self, key: &str) -> Option<&Instant> {
        self.instants.get(key)
    }

    /// Reads `key` and returns its instant.
    pub(crate) fn read(&mut self, key: &str) -> Result<&Instant, Unreadable> {
        self.add(key)?;
        Ok(&self.instants[key])
    }

    /// The UTC days of every key, or `None` when any is no date: the question a chart asks of a column
    /// that may be a category.
    pub(crate) fn days(&mut self, keys: &[String]) -> Option<Vec<f64>> {
        keys.iter().map(|key| self.read(key).ok().map(|instant| instant.at)).collect()
    }

    /// The start of the bucket of `tu` holding the UTC day of `key`, as a date.
    pub(crate) fn floor(&mut self, key: &str, tu: TimeUnit) -> Result<String, Unreadable> {
        let day = floor_day(self.read(key)?.at.floor() as i64, tu);
        Ok(instant_iso_day(day as f64))
    }

    /// The UTC day of a key read already.
    pub(crate) fn day(&self, key: &str) -> f64 {
        self.instants[key].at
    }

    /// The day the bucket holding a key read already starts: its floor to the grain, else its instant.
    pub(crate) fn day_of_bucket(&self, key: &str) -> f64 {
        self.instants[key].pos
    }

    /// [`civil`](Self::civil) for a key read already.
    pub(crate) fn civil_of(&self, key: &str) -> (i32, u8, u8) {
        civil_from_days(self.instants[key].at.floor() as i64)
    }

    /// The civil date of the UTC day `key` falls on.
    pub(crate) fn civil(&mut self, key: &str) -> Result<(i32, u8, u8), Unreadable> {
        Ok(civil_from_days(self.read(key)?.at.floor() as i64))
    }

    /// The start of the bucket after the one `start` opens, as a date: the end a bar spans.
    pub(crate) fn bucket_end(&mut self, start: &str, tu: TimeUnit) -> Result<String, Unreadable> {
        let (y, m, d) = next_bucket(self.civil(start)?, tu);
        Ok(format!("{y:04}-{m:02}-{d:02}"))
    }

    /// The instant an hour after `key`, a folded hour-of-day bar's end.
    pub(crate) fn hour_end(&mut self, key: &str) -> Result<String, Unreadable> {
        let at = self.read(key)?.at + 1.0 / 24.0;
        let (y, m, d) = civil_from_days(at.floor() as i64);
        Ok(format!("{y:04}-{m:02}-{d:02}T{:02}:00:00", ((at - at.floor()) * 24.0).round() as i64))
    }

    /// By instant alone, whatever the grain or cycle a vertex sits on: the order of the dates themselves. Spellings of
    /// one instant keep the order their text sorts in, so rows in any order seat the same. Every key is read already.
    pub(crate) fn sort_by_instant(&self, domain: &mut [String]) {
        domain.sort_by(|a, b| self.instants[a].at.total_cmp(&self.instants[b].at).then_with(|| a.cmp(b)));
    }

    /// Left to right: by vertex position, then by instant. Every key is read already.
    pub(crate) fn sort(&self, domain: &mut [String]) {
        domain.sort_by(|a, b| {
            let (a, b) = (&self.instants[a], &self.instants[b]);
            a.pos.total_cmp(&b.pos).then(a.at.total_cmp(&b.at))
        });
    }

    /// The instants of `domain`, index-aligned with it; `None` when a key was never read.
    pub(crate) fn columns(&self, domain: &[String]) -> Option<TimeColumns> {
        let grain = self.grain;
        let entries = domain.iter().map(|key| self.instants.get(key)).collect::<Option<Vec<_>>>()?;
        Some(TimeColumns {
            at: entries.iter().map(|i| i.at).collect(),
            pos: entries.iter().map(|i| i.pos).collect(),
            nan: entries.iter().map(|i| i.nan).collect(),
            end: entries.iter().map(|i| i.end).collect(),
            iso: entries.iter().map(|i| if grain.part.is_some() { i.key.clone() } else { i.iso.clone() }).collect(),
        })
    }
}

/// The instants of an x domain, index-aligned with it.
#[derive(Clone)]
pub(crate) struct TimeColumns {
    pub at: Vec<f64>,
    pub pos: Vec<f64>,
    pub nan: Vec<bool>,
    pub end: Vec<Option<f64>>,
    pub iso: Vec<String>,
}

impl TimeColumns {
    /// The scale's extent over the rows that have a position; `None` for under two or a zero span.
    pub(crate) fn extent(&self) -> Option<(f64, f64)> {
        let finite: Vec<f64> = self.pos.iter().zip(&self.nan).filter(|&(_, &nan)| !nan).map(|(&pos, _)| pos).collect();
        crate::families::temporal::extent_of(&finite)
    }

    /// The domain with one more instant at its end (the end of the last bucket a bar occupies).
    pub(crate) fn extended(&self, end_day: f64, end_iso: String) -> Self {
        let mut out = self.clone();
        out.at.push(end_day);
        out.pos.push(end_day);
        out.nan.push(false);
        out.end.push(None);
        out.iso.push(end_iso);
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn axis(unit: Option<TimeUnit>) -> TimeAxis {
        TimeAxis::new(Grain { unit, part: None, band: false }, HashMap::new())
    }

    fn read(axis: &mut TimeAxis, key: &str) -> Instant {
        axis.add(key).unwrap_or_else(|e| panic!("{key}: {e:?}"));
        axis.get(key).unwrap().clone()
    }

    /// One parser per row shape: every spelling of an instant reads as the same UTC day fraction.
    #[test]
    fn every_row_shape_reads_as_its_utc_instant() {
        let mut axis = axis(None);
        let noon = days_from_civil(2024, 3, 5) as f64 + 0.5;
        for text in [
            "2024-03-05T12:00",
            "2024-03-05 12:00:00",
            "2024-03-05T12:00:00Z",
            "2024-03-05T12:00:00.000",
            "2024-03-05T12:00:00,0",
            "2024-03-05T17:30:00+05:30",
            "2024-03-05T07:00:00-0500",
            "2024-03-05T13:00:00+01:00",
        ] {
            assert_eq!(read(&mut axis, text).at, noon, "{text}");
        }
        let midnight = days_from_civil(2024, 3, 1) as f64;
        for text in ["2024-03-01", "2024-03", "2024-03-01T00:00", "2024-03-01T00:00:00Z", "2024-02-29T19:00:00-05:00"] {
            assert_eq!(read(&mut axis, text).at, midnight, "{text}");
        }
        assert_eq!(read(&mut axis, "2024-03-05T12:00:00.250").at, noon + 0.25 / 86_400.0);
        assert_eq!(read(&mut axis, "2024").at, days_from_civil(2024, 1, 1) as f64);
    }

    /// Equal instants are equal floats, whatever their spelling.
    #[test]
    fn every_spelling_of_an_instant_is_the_same_float() {
        let mut axis = axis(None);
        let reference = read(&mut axis, "2024-03-05T10:17:33.123Z").at;
        for text in ["2024-03-05 10:17:33.123", "2024-03-05T15:47:33.123+05:30", "2024-03-05T05:17:33,123-0500", "2024-03-05T10:17:33.1230"] {
            assert_eq!(read(&mut axis, text).at.to_bits(), reference.to_bits(), "{text}");
        }
    }

    #[test]
    fn a_value_that_is_no_date_is_unreadable_by_its_own_text() {
        let mut axis = axis(None);
        for text in ["nope", "", "2024-02-30", "2023-02-29", "2024-13-01", "2024-03-01T25:00", "2024-03-01X10:00", "2024-03-01T10:00+", "2024-3-1"] {
            assert_eq!(axis.add(text), Err(Unreadable::NotADate(text.to_string())), "{text}");
        }
        assert!(axis.add("2024-02-29").is_ok(), "a leap day is a date");
    }

    #[test]
    fn a_domain_sorts_by_instant_not_by_text() {
        let mut axis = axis(None);
        // As text "2024-03-05T13:00:00+05:00" sorts last; as an instant (08:00Z) it is first.
        let mut domain: Vec<String> = ["2024-03-05T09:00:00Z", "2024-03-05T13:00:00+05:00", "2024-03-04", "2024-03-05T10:00"].map(String::from).to_vec();
        for key in &domain {
            axis.add(key).unwrap();
        }
        axis.sort(&mut domain);
        assert_eq!(domain, ["2024-03-04", "2024-03-05T13:00:00+05:00", "2024-03-05T09:00:00Z", "2024-03-05T10:00"]);
    }

    #[test]
    fn two_spellings_of_one_instant_stay_two_keys() {
        let mut axis = axis(None);
        let (a, b) = (read(&mut axis, "2024-03-01"), read(&mut axis, "2024-03-01T00:00:00Z"));
        assert_eq!(a.at, b.at);
        assert_eq!((a.iso.as_str(), b.iso.as_str()), ("2024-03-01", "2024-03-01T00:00:00"));
    }

    /// The bucket is the UTC instant's: a timestamp late in the evening west of Greenwich opens the next day.
    #[test]
    fn a_grain_buckets_the_utc_instant() {
        let mut axis = axis(Some(TimeUnit::YearMonthDate));
        let late = read(&mut axis, "2024-03-01T23:30:00-05:00");
        assert_eq!((late.pos, late.end), (days_from_civil(2024, 3, 2) as f64, Some(days_from_civil(2024, 3, 3) as f64)));
        let mut months = self::axis(Some(TimeUnit::YearMonth));
        let edge = read(&mut months, "2024-01-31T22:00:00-05:00");
        assert_eq!((edge.pos, edge.end), (days_from_civil(2024, 2, 1) as f64, Some(days_from_civil(2024, 3, 1) as f64)));
        assert_eq!(edge.iso, "2024-02-01T03:00:00");
    }

    #[test]
    fn a_part_axis_sits_on_its_cycle() {
        let mut axis = TimeAxis::new(Grain { unit: None, part: Some(TimePart::MonthOfYear), band: false }, HashMap::new());
        let march = read(&mut axis, "2023-03-17");
        assert_eq!((march.pos, march.end), (days_from_civil(2012, 3, 1) as f64, Some(days_from_civil(2012, 4, 1) as f64)));
        assert_eq!(march.at, days_from_civil(2023, 3, 17) as f64);
        let mut hours = TimeAxis::new(Grain { unit: None, part: Some(TimePart::HourOfDay), band: false }, HashMap::new());
        let nine = read(&mut hours, "2024-03-01T09:00:00-05:00");
        assert_eq!(nine.pos, days_from_civil(2012, 1, 1) as f64 + 14.0 / 24.0);
        assert_eq!(nine.end, Some(nine.pos + 1.0 / 24.0));
    }

    #[test]
    fn a_labeled_bucket_reads_as_its_start_under_its_own_key() {
        let mut axis = TimeAxis::new(Grain::default(), HashMap::from([("2024-Q2".to_string(), "2024-04-01".to_string())]));
        let q2 = read(&mut axis, "2024-Q2");
        assert_eq!((q2.at, q2.iso.as_str()), (days_from_civil(2024, 4, 1) as f64, "2024-04-01"));
    }

    #[test]
    fn a_year_below_100_has_no_position_over_a_calendar_grain() {
        let mut axis = axis(Some(TimeUnit::YearMonth));
        assert!(read(&mut axis, "0050-03-01").nan);
        assert!(!read(&mut self::axis(None), "0050-03-01").nan);
    }

    #[test]
    fn a_timestamp_the_years_cannot_print_is_refused() {
        let mut axis = axis(None);
        let err = axis.add("9999-12-31T23:00:00-05:00").unwrap_err();
        assert!(matches!(err, Unreadable::OutsideYears(_)));
        assert!(err.diagnostic("c", "x", "bar").to_string().contains("outside years 0001 to 9999"));
    }

    /// The one parser: nothing else in the engine defines a date reader.
    #[test]
    fn this_module_holds_the_only_date_parser() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let mut stack = vec![root.clone()];
        let mut offenders = Vec::new();
        while let Some(dir) = stack.pop() {
            for entry in std::fs::read_dir(&dir).unwrap() {
                let path = entry.unwrap().path();
                if path.is_dir() {
                    stack.push(path);
                } else if path.extension().is_some_and(|e| e == "rs") && path != root.join("time_axis.rs") {
                    let text = std::fs::read_to_string(&path).unwrap();
                    for needle in ["fn date_text_day(", "fn parse_iso_date("] {
                        if text.contains(needle) {
                            offenders.push(format!("{}: {needle}", path.display()));
                        }
                    }
                }
            }
        }
        assert!(offenders.is_empty(), "{offenders:?}");
    }
}

#[cfg(test)]
pub(crate) mod testing {
    /// Every positioning attribute of an SVG, in document order: the drawn geometry with the
    /// texts that name a row (tooltips, aria labels) left out, so two spellings of one instant
    /// compare equal when they draw the same marks.
    pub(crate) fn geometry(svg: &str) -> Vec<String> {
        const POSITIONING: [&str; 13] =
            ["d", "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "width", "height", "transform", "points"];
        let mut out = Vec::new();
        for tag in svg.split('<').skip(1) {
            let tag = tag.split('>').next().unwrap_or_default();
            let mut rest = tag;
            while let Some(eq) = rest.find("=\"") {
                let name = rest[..eq].rsplit(' ').next().unwrap_or_default();
                let value_and_rest = &rest[eq + 2..];
                let end = value_and_rest.find('"').unwrap_or(value_and_rest.len());
                if POSITIONING.contains(&name) {
                    out.push(format!("{name}={}", &value_and_rest[..end]));
                }
                rest = value_and_rest.get(end + 1..).unwrap_or_default();
            }
        }
        out
    }
}
