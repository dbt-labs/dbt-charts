//! Calendar grains and the date arithmetic that reads only a date: no label, tick or width.

use column_types::ColumnTypes;
use d3rs::calendar::{civil_from_days, days_from_civil, days_in_month, weekday, MS_PER_DAY};

use crate::time_axis::Dates;

/// Detected or authored calendar bucketing grain.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
pub enum TimeUnit {
    Year,
    YearQuarter,
    YearMonth,
    YearWeek,
    YearMonthDate,
}

/// A cyclic time part, `labels.time_unit`'s `monthofyear` .. `hourofday`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TimePart {
    MonthOfYear,
    DayOfWeek,
    DayOfMonth,
    DayOfYear,
    HourOfDay,
}

impl TimePart {
    /// The part an `axis_x.time_unit` / `labels.time_unit` name picks.
    pub fn parse(name: &str) -> Option<Self> {
        Some(match name {
            "monthofyear" => TimePart::MonthOfYear,
            "dayofweek" => TimePart::DayOfWeek,
            "dayofmonth" => TimePart::DayOfMonth,
            "dayofyear" => TimePart::DayOfYear,
            "hourofday" => TimePart::HourOfDay,
            _ => return None,
        })
    }

    /// Where Vega-Lite's `timeUnit` puts the instant `day` (days since the
    /// epoch, fractional for a clock): its unit's own field kept, every other
    /// field on 2012-01-01, a leap year that starts on a Sunday.
    pub fn instant(self, day: f64) -> f64 {
        let whole = day.floor();
        let (y, m, d) = civil_from_days(whole as i64);
        let base = days_from_civil(2012, 1, 1);
        match self {
            TimePart::MonthOfYear => days_from_civil(2012, m, 1) as f64,
            TimePart::DayOfWeek => (base + i64::from((weekday(y, m, d) + 1) % 7)) as f64,
            TimePart::DayOfMonth => days_from_civil(2012, 1, d) as f64,
            TimePart::DayOfYear => (base + days_from_civil(y, m, d) - days_from_civil(y, 1, 1)) as f64,
            TimePart::HourOfDay => base as f64 + f64::from(clock_hour(day)) / 24.0,
        }
    }

    /// The strftime spec Vega-Lite's own axis format paints the part with
    /// (`timeUnitSpecifier`).
    pub fn format_spec(self) -> &'static str {
        match self {
            TimePart::MonthOfYear => "%b",
            TimePart::DayOfWeek => "%a",
            TimePart::DayOfMonth => "%d",
            TimePart::DayOfYear => "%j",
            TimePart::HourOfDay => "%H:00",
        }
    }
}

impl TimeUnit {
    /// True for the grains whose bucket start moves with
    /// `fiscal_year_start_month` (production's `_ANCHOR_SENSITIVE_UNITS`, the
    /// only grains `resolve_cartesian_x_type` flips to ordinal under one).
    pub fn is_fiscal_sensitive(self) -> bool {
        matches!(self, TimeUnit::Year | TimeUnit::YearQuarter)
    }

    /// The calendar unit `ResolvedAxisStyle.grain_formats` keys by
    /// (`GRAIN_FORMAT_NAMES`); a day has no `date_<grain>` name.
    pub fn grain_unit(self) -> Option<&'static str> {
        match self {
            TimeUnit::Year => Some("year"),
            TimeUnit::YearQuarter => Some("yearquarter"),
            TimeUnit::YearMonth => Some("yearmonth"),
            TimeUnit::YearWeek => Some("yearweek"),
            TimeUnit::YearMonthDate => None,
        }
    }
}


pub fn clock_hour(day: f64) -> u32 {
    (((day - day.floor()) * 86_400.0).round() as u32 / 3600).min(23)
}


/// Start of the bucket enclosing `date` (`_floor_to_bucket_start`): year
/// and quarter floor to their fiscal-anchored period, month to its first
/// day; a week's start is whatever weekday the data uses and a day is its
/// own bucket, so both stay as they are.
pub fn floor_to_bucket_start(date: (i32, u8, u8), tu: TimeUnit, fiscal_start: u8) -> (i32, u8, u8) {
    let (y, m, _) = date;
    let period = match tu {
        TimeUnit::Year => 12,
        TimeUnit::YearQuarter => 3,
        TimeUnit::YearMonth => return (y, m, 1),
        TimeUnit::YearWeek | TimeUnit::YearMonthDate => return date,
    };
    // Months since the fiscal anchor, floored to a period boundary.
    let offset = (i32::from(m) - i32::from(fiscal_start)).rem_euclid(12);
    let start = i32::from(m) - offset % period;
    let (sy, sm) = if start < 1 { (y - 1, start + 12) } else { (y, start) };
    (sy, sm as u8, 1)
}


/// The bucket after `date` (`_next_bucket`): a fixed 1/3/12-month or 1/7-day
/// step, which keeps a fiscal anchor's month once the bucket start is anchored.
pub fn next_bucket(date: (i32, u8, u8), tu: TimeUnit) -> (i32, u8, u8) {
    let (y, m, d) = date;
    let months = match tu {
        TimeUnit::Year => return (y + 1, m, 1),
        TimeUnit::YearQuarter => 3,
        TimeUnit::YearMonth => 1,
        TimeUnit::YearWeek => return civil_from_days(days_from_civil(y, m, d) + 7),
        TimeUnit::YearMonthDate => return civil_from_days(days_from_civil(y, m, d) + 1),
    };
    let total = i32::from(m) - 1 + months;
    (y + total / 12, (total % 12 + 1) as u8, 1)
}


/// `(min, max)` of `days`; `None` for fewer than two or a zero span, which d3 centers instead.
pub fn extent_of(days: &[f64]) -> Option<(f64, f64)> {
    if days.len() < 2 {
        return None;
    }
    let d0 = days.iter().copied().fold(f64::INFINITY, f64::min);
    let dn = days.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    (dn > d0).then_some((d0, dn))
}


/// [`time_unit_floor`] on a day number: the day the enclosing bucket of `tu` starts.
pub fn floor_day(day: i64, tu: TimeUnit) -> i64 {
    let (y, m, d) = civil_from_days(day);
    if tu == TimeUnit::YearWeek {
        return day - (i64::from(weekday(y, m, d)) + 1) % 7;
    }
    let (fy, fm, fd) = floor_to_bucket_start((y, m, d), tu, 1);
    days_from_civil(fy, fm, fd)
}


/// Days in the bucket of `tu` holding `day`.
pub fn bucket_days(day: f64, tu: TimeUnit) -> f64 {
    let start = floor_day(day.floor() as i64, tu);
    let (y, m, d) = civil_from_days(start);
    let (ny, nm, nd) = next_bucket((y, m, d), tu);
    (days_from_civil(ny, nm, nd) - start) as f64
}

/// An instant in fractional days as an ISO date, with its clock when it has one.
pub fn instant_iso(at: f64) -> String {
    let (y, m, d) = civil_from_days(at.floor() as i64);
    let ms = ((at - at.floor()) * MS_PER_DAY).round() as i64;
    if ms == 0 {
        return format!("{y:04}-{m:02}-{d:02}");
    }
    format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}", ms / 3_600_000, ms / 60_000 % 60, ms / 1000 % 60)
}

/// Parse `axis_x.time_unit` JSON string to `TimeUnit`.
/// Returns `None` for unrecognized values (Rust renderer will skip temporal
/// formatting and fall back to raw string labels for unknown grains).
pub fn parse_authored_time_unit(s: &str) -> Option<TimeUnit> {
    match s {
        "year" => Some(TimeUnit::Year),
        "yearquarter" => Some(TimeUnit::YearQuarter),
        "yearmonth" => Some(TimeUnit::YearMonth),
        "yearweek" => Some(TimeUnit::YearWeek),
        "yearmonthdate" => Some(TimeUnit::YearMonthDate),
        _ => None,
    }
}

/// Detect the calendar bucketing grain from the dates of a domain.
///
/// Mirrors Python `detect_time_unit`:
/// * All Jan-1 → `Year`
/// * All first-of-quarter → `YearQuarter`
/// * All first-of-month → `YearMonth`
/// * All same weekday, median gap ≤ 14 days → `YearWeek`
/// * Gaps that are whole years, quarters or months ([`detect_cadence`]) →
///   that grain
/// * Otherwise → `YearMonthDate`
///
/// Returns `None` if fewer than 2 distinct dates.
pub fn detect_time_unit(values: &Dates) -> Option<TimeUnit> {
    let mut dates = values.civils();
    // Deduplicate
    dates.sort_unstable();
    dates.dedup();
    if dates.len() < 2 {
        return None;
    }

    if dates.iter().all(|&(_, m, d)| m == 1 && d == 1) {
        return Some(TimeUnit::Year);
    }
    if dates.iter().all(|&(_, m, d)| d == 1 && matches!(m, 1 | 4 | 7 | 10)) {
        return Some(TimeUnit::YearQuarter);
    }
    if dates.iter().all(|&(_, _, d)| d == 1) {
        return Some(TimeUnit::YearMonth);
    }

    // Weekly: all same weekday AND median gap ≤ 14 days.
    let wd0 = weekday(dates[0].0, dates[0].1, dates[0].2);
    if dates.iter().all(|&(y, m, d)| weekday(y, m, d) == wd0) {
        // `time_unit_detect.py::detect_time_unit` (lines 863-874): exact
        // civil-calendar day gaps between the sorted distinct dates, then
        // `statistics.median` — the middle gap on an odd count, the mean of the
        // two middle gaps on an even one (compared doubled, so the half stays
        // exact).
        let mut gaps: Vec<i64> = dates
            .windows(2)
            .map(|w| days_from_civil(w[1].0, w[1].1, w[1].2) - days_from_civil(w[0].0, w[0].1, w[0].2))
            .collect();
        gaps.sort_unstable();
        let mid = gaps.len() / 2;
        let twice_median = if gaps.len() % 2 == 1 { 2 * gaps[mid] } else { gaps[mid - 1] + gaps[mid] };
        if twice_median <= 2 * 14 {
            return Some(TimeUnit::YearWeek);
        }
        // Same weekday but non-weekly spacing — treat as continuous (no label cadence).
        return None;
    }

    Some(detect_cadence(&dates).unwrap_or(TimeUnit::YearMonthDate))
}

/// `_CADENCE_PERIOD_DAYS` (`time_unit_detect.py:735-739`): the nominal length
/// of each grain `_detect_cadence` tries, coarsest first. The weekly grain is
/// absent there too — the same-weekday rung above owns it.
const CADENCE_PERIOD_DAYS: [(TimeUnit, f64); 3] = [
    (TimeUnit::Year, 365.25),
    (TimeUnit::YearQuarter, 91.31),
    (TimeUnit::YearMonth, 30.44),
];

/// `_CADENCE_TOLERANCE` (`time_unit_detect.py:744`): how far the median gap
/// may sit from a whole number of periods.
const CADENCE_TOLERANCE: f64 = 0.15;

/// `_detect_cadence` (`time_unit_detect.py:750-802`): the coarsest grain whose
/// period the gaps between `dates` (sorted, distinct) are multiples of. A
/// grain is accepted when the MEDIAN gap's deviation from its nearest whole
/// multiple of the period (at least one) is within [`CADENCE_TOLERANCE`], and
/// every date floors to a bucket of its own under every anchor month that
/// moves the grain's boundaries (a fiscal start moves year and quarter
/// boundaries, which detection never sees). One gap is a measurement, not a
/// rhythm, so fewer than three dates detect nothing.
fn detect_cadence(dates: &[(i32, u8, u8)]) -> Option<TimeUnit> {
    let days: Vec<i64> = dates.iter().map(|&(y, m, d)| days_from_civil(y, m, d)).collect();
    let gaps: Vec<f64> = days.windows(2).map(|w| (w[1] - w[0]) as f64).collect();
    if gaps.len() < 2 {
        return None;
    }
    for (unit, period) in CADENCE_PERIOD_DAYS {
        let mut deviations: Vec<f64> = gaps
            .iter()
            .map(|gap| {
                // Python's `round` is half-to-even; the periods make a tie
                // impossible for whole-day gaps, so this only states the rule.
                let steps = (gap / period).round_ties_even().max(1.0);
                (gap / period - steps).abs()
            })
            .collect();
        deviations.sort_by(f64::total_cmp);
        let mid = deviations.len() / 2;
        let median = if deviations.len() % 2 == 1 {
            deviations[mid]
        } else {
            (deviations[mid - 1] + deviations[mid]) / 2.0
        };
        if median > CADENCE_TOLERANCE {
            continue;
        }
        let anchors = if unit.is_fiscal_sensitive() { 1..=12 } else { 1..=1 };
        let each_in_its_own_bucket = anchors.into_iter().all(|anchor| {
            let mut buckets: Vec<(i32, u8, u8)> =
                dates.iter().map(|&date| floor_to_bucket_start(date, unit, anchor)).collect();
            buckets.sort_unstable();
            buckets.dedup();
            buckets.len() == dates.len()
        });
        if each_in_its_own_bucket {
            return Some(unit);
        }
    }
    None
}

/// Production's `infer_vega_type_from_data` (`type_inference.py`) — the
/// STRUCTURAL gate that decides whether an x field's raw string SHAPE is
/// even a temporal/date-like candidate before `detect_time_unit`'s own
/// (looser) semantic parser ever runs. Distinct from `parse_iso_date`: a
/// bare 4-digit year string like `"1000"` PARSES fine (year, month=1,
/// day=1) but fails every structural pattern here (no separator at all),
/// so it never reaches `detect_time_unit` at all and the axis stays
/// nominal (production's own `aria-label` reads
/// "X-axis for a discrete scale", never temporal) -- unless
/// `is_year_shaped` first rewrites it.
///
/// Samples only the first `min(10, len)` values, matching production's own
/// `sample_values = ...[:min(10, len(data))]` — this port's caller passes
/// the DEDUPED x domain rather than the raw row order production samples,
/// which agrees whenever a column's shape is homogeneous (every swept
/// case's is; a column mixing shapes is not a case either side models).
pub fn x_values_are_temporal_candidates(values: &[&str]) -> bool {
    let sample = &values[..values.len().min(10)];
    if sample.is_empty() {
        return false;
    }
    sample.iter().all(|v| is_standard_date_shape(v) || is_date_like_shape(v))
}

/// `infer_vega_type_from_data`'s verdict over a field's first ten sampled
/// cells. A field absent from the first row is nominal; an all-null sample
/// is vacuously quantitative (`vega_infers_quantitative`). A string cell is
/// temporal when every sampled cell is a standard date, ordinal when each is
/// at least date-like, else nominal. A declared date or timestamp column with
/// a cell to read is temporal whatever the cells look like; one with none is
/// vacuously quantitative, as Vega types it. A number column types by its
/// cells, which the year-shaped rewrite may have turned into ISO dates by then.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum VegaType {
    Quantitative,
    Temporal,
    Ordinal,
    Nominal,
}

impl VegaType {
    /// The type's name as Vega-Lite spells it (`vl_type` params).
    pub fn name(self) -> &'static str {
        match self {
            VegaType::Quantitative => "quantitative",
            VegaType::Temporal => "temporal",
            VegaType::Ordinal => "ordinal",
            VegaType::Nominal => "nominal",
        }
    }
}

/// Whether any row's `field` is a labeled bucket (`2024-Q1`) that production rewrites to its ISO start.
pub fn has_labeled_bucket(rows: &[serde_json::Map<String, serde_json::Value>], field: &str) -> bool {
    let texts: Vec<String> = rows.iter().filter_map(|row| row.get(field).and_then(serde_json::Value::as_str)).map(str::to_string).collect();
    let dates = Dates::read(&texts);
    texts.iter().zip(&dates.date).any(|(text, date)| date.is_none() && parse_bucket_string(text).is_some())
}

pub fn infer_vega_type(types: &ColumnTypes, rows: &[serde_json::Map<String, serde_json::Value>], field: &str) -> Result<VegaType, String> {
    use serde_json::Value;
    let declared = types.get(field)?;
    if !rows.first().is_some_and(|row| row.contains_key(field)) {
        return Ok(VegaType::Nominal);
    }
    let sample: Vec<&Value> = rows.iter().take(10).filter_map(|row| row.get(field)).filter(|v| !v.is_null()).collect();
    if sample.iter().all(|v| matches!(v, Value::Number(_) | Value::Bool(_))) {
        return Ok(VegaType::Quantitative);
    }
    if declared.is_temporal() {
        return Ok(VegaType::Temporal);
    }
    let (mut all_temporal, mut all_date_like) = (true, true);
    for value in sample {
        match value {
            Value::String(s) => {
                let standard = is_standard_date_shape(s);
                all_temporal &= standard;
                all_date_like &= standard || is_date_like_shape(s);
            }
            _ => (all_temporal, all_date_like) = (false, false),
        }
    }
    Ok(match (all_temporal, all_date_like) {
        (true, _) => VegaType::Temporal,
        (_, true) => VegaType::Ordinal,
        _ => VegaType::Nominal,
    })
}

/// `infer_vega_type_from_data`'s STRICT `all_temporal` gate: every sampled
/// value matches `is_standard_date` (never the looser `is_date_like`
/// ordinal shape). A field resolving this way types "temporal" regardless
/// of whether `detect_time_unit` finds a bucketed calendar grain
/// (`header_tooltip_field`'s `field_type == "temporal"` branch fires
/// unconditionally) — the structured-tooltip header for such a field still
/// formats through `tooltip_header_date`'s continuous fallback (`time_unit
/// == ""`, `"%-d %b %Y"`) even with no detected grain, distinct from a
/// looser date-like ("ordinal") field, which only formats when a grain IS
/// detected.
pub fn x_values_are_standard_dates(values: &[&str]) -> bool {
    let sample = &values[..values.len().min(10)];
    if sample.is_empty() {
        return false;
    }
    sample.iter().all(|v| is_standard_date_shape(v))
}

/// `is_standard_date` (`type_inference.py`'s inline regex): `YYYY[-/]MM[-/]DD`
/// optionally followed by a `[T ]` + time-of-day start, or `MM/DD/YYYY`.
pub fn is_standard_date_shape(s: &str) -> bool {
    let b = s.as_bytes();
    if b.len() >= 10
        && (b[4] == b'-' || b[4] == b'/')
        && (b[7] == b'-' || b[7] == b'/')
        && b[0..4].iter().all(u8::is_ascii_digit)
        && b[5..7].iter().all(u8::is_ascii_digit)
        && b[8..10].iter().all(u8::is_ascii_digit)
    {
        if b.len() == 10 {
            return true;
        }
        return b.len() > 11
            && (b[10] == b'T' || b[10] == b' ')
            && (b[11].is_ascii_digit() || b[11] == b':' || b[11] == b'.');
    }
    if b.len() == 10 && b[2] == b'/' && b[5] == b'/' {
        return b[0..2].iter().all(u8::is_ascii_digit)
            && b[3..5].iter().all(u8::is_ascii_digit)
            && b[6..10].iter().all(u8::is_ascii_digit);
    }
    false
}

const MONTH_ABBRS: [&str; 12] =
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

/// `s` starting with a case-insensitive month abbreviation immediately
/// followed by whitespace — the shared prefix `DATE_LIKE_PATTERNS`' two
/// month-name forms both require. Returns the trimmed remainder.
fn strip_month_abbr(s: &str) -> Option<&str> {
    let tail = s.get(3..)?;
    if !tail.chars().next().is_some_and(char::is_whitespace) {
        return None;
    }
    let head = s.get(..3)?.to_ascii_lowercase();
    MONTH_ABBRS.contains(&head.as_str()).then(|| tail.trim_start())
}

fn all_digits(s: &str, len: usize) -> bool {
    s.len() == len && s.bytes().all(|c| c.is_ascii_digit())
}

/// [`x_values_are_standard_dates`] for the x column `field`: a declared date or timestamp is one whatever its
/// cells; a column of any other declared type is shaped by its text as ever.
pub fn x_is_standard_date(types: &ColumnTypes, field: &str, values: &[&str]) -> Result<bool, String> {
    if types.get(field)?.is_temporal() {
        return Ok(!values.is_empty());
    }
    Ok(x_values_are_standard_dates(values))
}

/// [`x_values_are_temporal_candidates`] for the x column `field`, as [`x_is_standard_date`].
pub fn x_is_temporal_candidate(types: &ColumnTypes, field: &str, values: &[&str]) -> Result<bool, String> {
    if types.get(field)?.is_temporal() {
        return Ok(!values.is_empty());
    }
    Ok(x_values_are_temporal_candidates(values))
}

/// `is_date_like_string` (`type_inference.py`'s `DATE_LIKE_PATTERNS`) — the
/// 13 labeled-bucket shapes distinct from a standard date, e.g. `"2024-Q1"`,
/// `"FY2024"`, `"Week 3 2024"`, `"Jan 2024"`, `"Jan 15, 2024"`.
pub fn is_date_like_shape(s: &str) -> bool {
    let b = s.as_bytes();
    // YYYY-Qn / YYYYQn — production compiles DATE_LIKE_PATTERNS with
    // re.IGNORECASE.
    if b.len() == 7
        && b[0..4].iter().all(u8::is_ascii_digit)
        && b[4] == b'-'
        && (b[5] == b'Q' || b[5] == b'q')
        && matches!(b[6], b'1'..=b'4')
    {
        return true;
    }
    if b.len() == 6
        && b[0..4].iter().all(u8::is_ascii_digit)
        && (b[4] == b'Q' || b[4] == b'q')
        && matches!(b[5], b'1'..=b'4')
    {
        return true;
    }
    // Qn YYYY (optional whitespace)
    if b.len() >= 2 && (b[0] == b'Q' || b[0] == b'q') && matches!(b[1], b'1'..=b'4') {
        let rest = s[2..].trim_start();
        if all_digits(rest, 4) {
            return true;
        }
    }
    // YYYY-MM
    if b.len() == 7 && b[4] == b'-' && b[0..4].iter().all(u8::is_ascii_digit) && b[5..7].iter().all(u8::is_ascii_digit) {
        return true;
    }
    // FYYYYY
    if b.len() == 6 && b[0..2].eq_ignore_ascii_case(b"FY") && b[2..6].iter().all(u8::is_ascii_digit) {
        return true;
    }
    // Hn YYYY / YYYY-Hn
    if b.len() >= 2 && (b[0] == b'H' || b[0] == b'h') && matches!(b[1], b'1' | b'2') {
        let rest = s[2..].trim_start();
        if all_digits(rest, 4) {
            return true;
        }
    }
    if b.len() == 7 && b[0..4].iter().all(u8::is_ascii_digit) && b[4] == b'-' && (b[5] == b'H' || b[5] == b'h') && matches!(b[6], b'1' | b'2') {
        return true;
    }
    // YYYY-Wnn (ISO week)
    if b.len() == 8 && b[0..4].iter().all(u8::is_ascii_digit) && b[4] == b'-' && (b[5] == b'W' || b[5] == b'w') && b[6..8].iter().all(u8::is_ascii_digit) {
        return true;
    }
    // W(eek)? n[n] YYYY
    if b.len() >= 2 && (b[0] == b'W' || b[0] == b'w') {
        let mut rest = &s[1..];
        if rest.len() >= 3 && rest.as_bytes()[..3].eq_ignore_ascii_case(b"eek") {
            rest = &rest[3..];
        }
        let rest = rest.trim_start();
        // `\d{1,2}` backtracks in the source regex: try the greedy 2-digit
        // week number first, then retry at 1 digit if that leaves a tail
        // that isn't the 4-digit year (`"W12024"` only parses as "1" +
        // "2024" — a bare `take_while` digit run would over-consume all 5
        // digits and never retry).
        let available = rest.bytes().take_while(u8::is_ascii_digit).count();
        let matched = [2usize, 1usize].into_iter().any(|digit_count| {
            digit_count <= available && all_digits(rest[digit_count..].trim_start(), 4)
        });
        if matched {
            return true;
        }
    }
    // MM/YYYY
    if b.len() == 7 && b[2] == b'/' && b[0..2].iter().all(u8::is_ascii_digit) && b[3..7].iter().all(u8::is_ascii_digit) {
        return true;
    }
    // MM/DD/YYYY — already covered by is_standard_date_shape, listed in
    // DATE_LIKE_PATTERNS too; the caller's `||` makes this redundant but
    // harmless, so it is not re-checked here.
    // Mon YYYY / Mon D[D][,] YYYY
    if let Some(rest) = strip_month_abbr(s) {
        if all_digits(rest, 4) {
            return true;
        }
        let digit_count = rest.bytes().take_while(u8::is_ascii_digit).count();
        if digit_count == 1 || digit_count == 2 {
            let mut tail = &rest[digit_count..];
            if let Some(stripped) = tail.strip_prefix(',') {
                tail = stripped;
            }
            // `\s+` before the year is mandatory in the source regex even
            // after a comma — `"Jan 15,2024"` (no space) does not match.
            if tail.chars().next().is_some_and(char::is_whitespace)
                && all_digits(tail.trim_start(), 4)
            {
                return true;
            }
        }
    }
    false
}

/// `date.fromisocalendar`'s own validity rule (Richards' algorithm): a
/// year has 53 ISO weeks exactly when `p(year) == 4` or `p(year-1) == 3`,
/// where `p(y) = (y + y/4 - y/100 + y/400) mod 7`. `production's
/// `_week_to_iso`/`_parse_bucket_string` both delegate to `date.
/// fromisocalendar`, which raises (production: returns `None`) for a
/// week the regex admits (`01`-`53`) but the year doesn't actually have.
fn iso_weeks_in_year(year: i32) -> u32 {
    let p = |y: i32| -> i32 {
        let y = y as i64;
        ((y + y / 4 - y / 100 + y / 400).rem_euclid(7)) as i32
    };
    if p(year) == 4 || p(year - 1) == 3 {
        53
    } else {
        52
    }
}

/// `date.fromisocalendar(year, week, 1)` — the Monday of ISO `week` in
/// `year`. `None` when the regex-admitted week (`01`-`53`) does not exist
/// in this particular year (`iso_weeks_in_year`), matching production's
/// own `_week_to_iso`/`_parse_bucket_string` (`try/except ValueError` →
/// `None`).
fn iso_week_monday(year: i32, week: u32) -> Option<(i32, u32, u32)> {
    if week < 1 || week > iso_weeks_in_year(year) {
        return None;
    }
    let jan4_weekday = weekday(year, 1, 4); // 0=Mon..6=Sun
    let week1_monday = days_from_civil(year, 1, 4) - i64::from(jan4_weekday);
    let (y, m, d) = civil_from_days(week1_monday + (i64::from(week) - 1) * 7);
    Some((y, u32::from(m), u32::from(d)))
}

const QUARTER_FIRST_MONTH: [u32; 4] = [1, 4, 7, 10];

/// Case-insensitive 3-letter month-abbreviation index (`Jan` → 1, …,
/// `Dec` → 12), reusing `MONTH_ABBRS` (`strip_month_abbr`'s own table).
fn month_abbr_index(s: &str) -> Option<u32> {
    let lower = s.get(..3)?.to_ascii_lowercase();
    MONTH_ABBRS.iter().position(|m| *m == lower).map(|i| i as u32 + 1)
}

/// Which of production's 11 `_BUCKET_FAMILIES` regexes (`time_unit_detect.
/// py:52-108`) `s` matches STRUCTURALLY — separators, digit counts, case,
/// and whitespace exactly as each regex is written, with NO calendar-
/// validity check (`bucket_anchor` below owns that). Ported ONCE as the
/// single source of truth production's own `_classify_bucket` is:
/// `x_values_are_bucket_family_shaped` (is this column SHAPED like a
/// bucket — the gate `normalize_labeled_temporal` uses to decide whether
/// to even attempt a rewrite), `parse_bucket_string` (classify, then
/// anchor), and `bucket_family_shaped_but_invalid` (classified but the
/// anchor failed — the shape production recognizes but the VALUE it
/// raises constructing) all read this ONE classification instead of
/// independently hand-copied structural matchers, which is how a
/// whitespace rule (`week_spelled`'s `\s*` applies ONLY inside the
/// optional `(?:eek\s*)?` group — a bare `W` admits no whitespace at all
/// before its digits, so `"W 3 2024"` matches no family) drifted out of
/// sync between two of them.
#[derive(Clone, Copy, PartialEq, Eq)]
enum BucketFamily {
    IsoWeek,
    IsoQuarter,
    YearmonthStr,
    MonYyyy,
    MmYyyy,
    QYyyy,
    YyyyQ,
    Fy,
    MmDdYyyy,
    MonDdYyyy,
    WeekSpelled,
}

fn classify_bucket(s: &str) -> Option<BucketFamily> {
    let b = s.as_bytes();
    // iso_week: `^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$` — CASE-SENSITIVE 'W'.
    if b.len() == 8
        && b[0..4].iter().all(u8::is_ascii_digit)
        && b[4] == b'-'
        && b[5] == b'W'
        && b[6..8].iter().all(u8::is_ascii_digit)
        && s[6..8].parse::<u32>().is_ok_and(|w| (1..=53).contains(&w))
    {
        return Some(BucketFamily::IsoWeek);
    }
    // iso_quarter: `^\d{4}-Q[1-4]$` — CASE-SENSITIVE 'Q'.
    if b.len() == 7
        && b[0..4].iter().all(u8::is_ascii_digit)
        && b[4] == b'-'
        && b[5] == b'Q'
        && matches!(b[6], b'1'..=b'4')
    {
        return Some(BucketFamily::IsoQuarter);
    }
    // yearmonth_str: `^\d{4}-(0[1-9]|1[0-2])$`.
    if b.len() == 7
        && b[0..4].iter().all(u8::is_ascii_digit)
        && b[4] == b'-'
        && b[5..7].iter().all(u8::is_ascii_digit)
        && s[5..7].parse::<u32>().is_ok_and(|m| (1..=12).contains(&m))
    {
        return Some(BucketFamily::YearmonthStr);
    }
    // mon_yyyy: `^(Jan|Feb|...|Dec)\s+(\d{4})$`, IGNORECASE.
    if let Some(rest) = strip_month_abbr(s) {
        if all_digits(rest, 4) {
            return Some(BucketFamily::MonYyyy);
        }
    }
    // mm_yyyy: `^(0[1-9]|1[0-2])/(\d{4})$`.
    if b.len() == 7
        && b[2] == b'/'
        && b[0..2].iter().all(u8::is_ascii_digit)
        && b[3..7].iter().all(u8::is_ascii_digit)
        && s[0..2].parse::<u32>().is_ok_and(|m| (1..=12).contains(&m))
    {
        return Some(BucketFamily::MmYyyy);
    }
    // q_yyyy: `^Q([1-4])\s*(\d{4})$`, IGNORECASE.
    if b.len() >= 2 && (b[0] == b'Q' || b[0] == b'q') && matches!(b[1], b'1'..=b'4') {
        let rest = s[2..].trim_start();
        if all_digits(rest, 4) {
            return Some(BucketFamily::QYyyy);
        }
    }
    // yyyy_q: `^(\d{4})Q([1-4])$`, IGNORECASE.
    if b.len() == 6
        && b[0..4].iter().all(u8::is_ascii_digit)
        && (b[4] == b'Q' || b[4] == b'q')
        && matches!(b[5], b'1'..=b'4')
    {
        return Some(BucketFamily::YyyyQ);
    }
    // fy: `^FY(\d{4})$`, IGNORECASE.
    if b.len() == 6 && b[0..2].eq_ignore_ascii_case(b"FY") && b[2..6].iter().all(u8::is_ascii_digit) {
        return Some(BucketFamily::Fy);
    }
    // mm_dd_yyyy: `^(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/(\d{4})$`.
    if b.len() == 10
        && b[2] == b'/'
        && b[5] == b'/'
        && b[0..2].iter().all(u8::is_ascii_digit)
        && b[3..5].iter().all(u8::is_ascii_digit)
        && b[6..10].iter().all(u8::is_ascii_digit)
        && s[0..2].parse::<u32>().is_ok_and(|m| (1..=12).contains(&m))
        && s[3..5].parse::<u32>().is_ok_and(|d| (1..=31).contains(&d))
    {
        return Some(BucketFamily::MmDdYyyy);
    }
    // mon_dd_yyyy: `^(Jan|...|Dec)\s+(\d{1,2}),?\s+(\d{4})$`, IGNORECASE.
    if let Some(rest) = strip_month_abbr(s) {
        let digit_count = rest.bytes().take_while(u8::is_ascii_digit).count();
        if digit_count == 1 || digit_count == 2 {
            let mut tail = &rest[digit_count..];
            if let Some(stripped) = tail.strip_prefix(',') {
                tail = stripped;
            }
            // `\s+` before the year is mandatory even after a comma.
            if tail.chars().next().is_some_and(char::is_whitespace)
                && all_digits(tail.trim_start(), 4)
            {
                return Some(BucketFamily::MonDdYyyy);
            }
        }
    }
    // week_spelled: `^W(?:eek\s*)?(\d{1,2})\s+(\d{4})$`, IGNORECASE — the
    // ONLY whitespace the regex admits before the week digits is the
    // `\s*` INSIDE the optional `(?:eek\s*)?` group; a bare `W` admits
    // NONE (`"W 3 2024"` does not match; `"Week 3 2024"`/`"Week3 2024"`
    // both do).
    if b.len() >= 2 && (b[0] == b'W' || b[0] == b'w') {
        let after_w = &s[1..];
        let rest = if after_w.len() >= 3 && after_w.as_bytes()[..3].eq_ignore_ascii_case(b"eek") {
            after_w[3..].trim_start()
        } else {
            after_w
        };
        let digit_count = rest.bytes().take_while(u8::is_ascii_digit).count();
        if digit_count == 1 || digit_count == 2 {
            let tail = &rest[digit_count..];
            if tail.chars().next().is_some_and(char::is_whitespace)
                && all_digits(tail.trim_start(), 4)
            {
                return Some(BucketFamily::WeekSpelled);
            }
        }
    }
    None
}

/// The anchor (first-instant) date for a value already classified into
/// `family` by `classify_bucket` — production's own `_parse_bucket_string`
/// (`time_unit_detect.py:114-172`). `None` means the STRUCTURALLY-matching
/// value is calendar-INVALID (an ISO/spelled week the year doesn't have,
/// a day past its month's end): production's own `dt.date(...)`/`date.
/// fromisocalendar(...)` construction raises `ValueError` there, caught
/// and turned into `None` by the same two call sites. Only `IsoWeek`,
/// `WeekSpelled`, `MmDdYyyy`, and `MonDdYyyy` can produce `None` — every
/// other family's regex bound (`01`-`12` months, `1`-`4` quarters, a bare
/// year) can never admit a structurally-matching, calendar-invalid value.
fn bucket_anchor(family: BucketFamily, s: &str) -> Option<(i32, u32, u32)> {
    let b = s.as_bytes();
    match family {
        BucketFamily::IsoWeek => {
            let year: i32 = s[0..4].parse().ok()?;
            let week: u32 = s[6..8].parse().ok()?;
            iso_week_monday(year, week)
        }
        BucketFamily::IsoQuarter => {
            let year: i32 = s[0..4].parse().ok()?;
            let q = (b[6] - b'0') as usize;
            Some((year, QUARTER_FIRST_MONTH[q - 1], 1))
        }
        BucketFamily::YearmonthStr => {
            let year: i32 = s[0..4].parse().ok()?;
            let month: u32 = s[5..7].parse().ok()?;
            Some((year, month, 1))
        }
        BucketFamily::MonYyyy => {
            let rest = strip_month_abbr(s)?;
            let month = month_abbr_index(s)?;
            let year: i32 = rest.parse().ok()?;
            Some((year, month, 1))
        }
        BucketFamily::MmYyyy => {
            let month: u32 = s[0..2].parse().ok()?;
            let year: i32 = s[3..7].parse().ok()?;
            Some((year, month, 1))
        }
        BucketFamily::QYyyy => {
            let rest = s[2..].trim_start();
            let q = (b[1] - b'0') as usize;
            let year: i32 = rest.parse().ok()?;
            Some((year, QUARTER_FIRST_MONTH[q - 1], 1))
        }
        BucketFamily::YyyyQ => {
            let year: i32 = s[0..4].parse().ok()?;
            let q = (b[5] - b'0') as usize;
            Some((year, QUARTER_FIRST_MONTH[q - 1], 1))
        }
        BucketFamily::Fy => {
            let year: i32 = s[2..6].parse().ok()?;
            Some((year, 1, 1))
        }
        BucketFamily::MmDdYyyy => {
            let month: u32 = s[0..2].parse().ok()?;
            let day: u32 = s[3..5].parse().ok()?;
            let year: i32 = s[6..10].parse().ok()?;
            (day >= 1 && day <= days_in_month(year, month)).then_some((year, month, day))
        }
        BucketFamily::MonDdYyyy => {
            let rest = strip_month_abbr(s)?;
            let digit_count = rest.bytes().take_while(u8::is_ascii_digit).count();
            let day: u32 = rest[..digit_count].parse().ok()?;
            let month = month_abbr_index(s)?;
            let mut tail = &rest[digit_count..];
            if let Some(stripped) = tail.strip_prefix(',') {
                tail = stripped;
            }
            let year: i32 = tail.trim_start().parse().ok()?;
            (day >= 1 && day <= days_in_month(year, month)).then_some((year, month, day))
        }
        BucketFamily::WeekSpelled => {
            let after_w = &s[1..];
            let rest = if after_w.len() >= 3 && after_w.as_bytes()[..3].eq_ignore_ascii_case(b"eek")
            {
                after_w[3..].trim_start()
            } else {
                after_w
            };
            let digit_count = rest.bytes().take_while(u8::is_ascii_digit).count();
            let week: u32 = rest[..digit_count].parse().ok()?;
            let year: i32 = rest[digit_count..].trim_start().parse().ok()?;
            iso_week_monday(year, week)
        }
    }
}

/// Port of production's `_parse_bucket_string` — classify, then anchor.
/// `None` when `s` matches no `_BUCKET_FAMILIES` pattern, or matches one
/// but is calendar-invalid.
pub fn parse_bucket_string(s: &str) -> Option<(i32, u32, u32)> {
    bucket_anchor(classify_bucket(s)?, s)
}

/// `normalize_labeled_temporal`: a year-shaped column becomes `YYYY-01-01`,
/// a column of one labeled-bucket family becomes each bucket's first day;
/// anything else is returned unchanged. The errors are the `ValueError`s
/// production raises for a mixed or unrecognized column.
pub fn normalize_labeled_temporal<'a>(
    types: &column_types::ColumnTypes,
    rows: &'a [serde_json::Map<String, serde_json::Value>],
    field: &str,
) -> Result<std::borrow::Cow<'a, [serde_json::Map<String, serde_json::Value>]>, String> {
    use serde_json::Value;
    use std::borrow::Cow;
    let present: Vec<&Value> = rows.iter().filter_map(|r| r.get(field)).filter(|v| !v.is_null()).collect();
    let rewrite = |convert: &dyn Fn(&Value) -> Result<String, String>| -> Result<Cow<'a, [_]>, String> {
        rows.iter()
            .map(|row| {
                let mut row = row.clone();
                if let Some(cell) = row.get_mut(field).filter(|c| !c.is_null()) {
                    *cell = Value::String(convert(cell)?);
                }
                Ok(row)
            })
            .collect::<Result<Vec<_>, _>>()
            .map(Cow::Owned)
    };
    if present.is_empty() {
        return Ok(Cow::Borrowed(rows));
    }
    if crate::rows::is_year_shaped(types, rows, field)? {
        return rewrite(&|cell| {
            let year = match cell {
                Value::String(s) => s.parse::<i32>().ok(),
                other => other.as_f64().map(|f| f as i32),
            };
            year.map(|y| format!("{y:04}-01-01")).ok_or_else(|| format!("year-shaped x {cell} is not a year"))
        });
    }
    let is_iso = |v: &Value| {
        v.as_str().is_some_and(|s| {
            let b = s.as_bytes();
            b.len() >= 10
                && b[..4].iter().all(u8::is_ascii_digit)
                && b[4] == b'-'
                && b[5..7].iter().all(u8::is_ascii_digit)
                && b[7] == b'-'
                && b[8..10].iter().all(u8::is_ascii_digit)
                && (b.len() == 10 || matches!(b[10], b'T' | b' '))
        })
    };
    let family = |v: &Value| v.as_str().filter(|_| !is_iso(v)).and_then(classify_bucket);
    let labeled: Vec<&Value> = present.iter().copied().filter(|v| family(v).is_some()).collect();
    if labeled.is_empty() {
        return Ok(Cow::Borrowed(rows));
    }
    let mixed = "Couldn't auto-detect timeUnit: column contains mixed label and non-label values. Set style.axis_x.time_unit explicitly.";
    if present.iter().any(|v| is_iso(v)) || labeled.len() < present.len() {
        return Err(mixed.into());
    }
    let first = family(labeled[0]);
    if labeled.iter().any(|v| family(v) != first) {
        return Err(mixed.into());
    }
    rewrite(&|cell| {
        let s = cell.as_str().expect("labeled cells are strings");
        let (y, m, d) = parse_bucket_string(s)
            .ok_or_else(|| format!("Couldn't auto-detect timeUnit: unrecognized bucket format '{s}'."))?;
        Ok(format!("{y:04}-{m:02}-{d:02}"))
    })
}

/// The ISO start of the bucket `text` falls inside (`_enclosing_bucket`) when it
/// is not already a bucket start: a month-end, quarter-end or off-anchor
/// yearly date is keyed by the bucket that contains it. `None` for a date on its
/// bucket's start, an unparseable value, and a week (its start is wherever the
/// data's own first week puts it).
pub fn off_anchor_bucket_start(date: (i32, u8, u8), tu: TimeUnit, fiscal_start: u8) -> Option<String> {
    if tu == TimeUnit::YearWeek {
        return None;
    }
    let start = floor_to_bucket_start(date, tu, fiscal_start);
    (start != date).then(|| format!("{:04}-{:02}-{:02}", start.0, start.1, start.2))
}

/// Every bucket from the floored minimum to the maximum of `dates`: the
/// dates a continuous temporal axis measures, whether or not a row falls in
/// each bucket (`enumerated_axis_values` → `_enumerate_buckets`,
/// `time_unit_detect.py:1132`; used at `_label_overlap.py:539-546`). The
/// ladder decides fit and cadence over these; label texts are still built
/// from the rows.
pub fn enumerate_continuous_span(dates: &[(i32, u8, u8)], tu: TimeUnit, fiscal_start: u8) -> Vec<(i32, u8, u8)> {
    let (Some(&min), Some(&max)) = (dates.iter().min(), dates.iter().max()) else { return Vec::new() };
    let mut span = Vec::new();
    let mut cursor = floor_to_bucket_start(min, tu, fiscal_start);
    while cursor <= max {
        span.push(cursor);
        cursor = next_bucket(cursor, tu);
    }
    span
}

/// Production's `complete_ordinal_time_series` on the x domain alone: every
/// bucket from the floored minimum to the maximum, the data's own entries
/// kept verbatim (a row's key is its date, whatever its string form) and
/// the missing ones synthesized as date-only ISO strings, in
/// chronological order. Entries that don't parse are dropped, as
/// production's key lookup never matches them to a bucket.
pub fn complete_buckets(domain: &Dates, tu: TimeUnit, fiscal_start: u8) -> Dates {
    let mut by_date: Vec<((i32, u8, u8), &String, usize)> =
        domain.dated().into_iter().map(|(i, date)| (date, &domain.text[i], i)).collect();
    by_date.sort_by(|a, b| (a.0, a.1).cmp(&(b.0, b.1)));
    let mut out = Dates::default();
    let Some(&(min, ..)) = by_date.first() else { return out };
    let max = by_date.last().map(|&(d, ..)| d).unwrap();
    let mut cursor = floor_to_bucket_start(min, tu, fiscal_start);
    let mut present = by_date.iter().peekable();
    while cursor <= max {
        let mut matched = false;
        while let Some(&&(d, _, i)) = present.peek() {
            if d < cursor {
                present.next();
            } else if d == cursor {
                out.push_from(domain, i);
                present.next();
                matched = true;
            } else {
                break;
            }
        }
        if !matched {
            let (y, m, d) = cursor;
            out.push(format!("{y:04}-{m:02}-{d:02}"), Some(days_from_civil(y, m, d) as f64));
        }
        cursor = next_bucket(cursor, tu);
    }
    out
}

/// Production's `complete_ordinal_time_series`: every calendar bucket from the
/// floored minimum to the maximum gets exactly one row per combination of the
/// `dim_fields` values the rows hold (the color series), in chronological
/// order, each bucket's rows in ascending dimension order. A row lands in
/// the bucket enclosing its date and is keyed to that bucket's ISO date; an
/// empty (bucket, series) slot is synthesized with `fill` in every measure
/// column (`"zero"` writes 0, every other mode null, then the interpolating
/// modes fill the interior gaps of each series from its observed rows). Rows
/// that carry no parseable `x` are dropped, as production's key lookup never
/// matches them to a bucket.
pub fn complete_ordinal_rows(
    rows: &[serde_json::Map<String, serde_json::Value>],
    x_field: &str,
    dim_fields: &[&str],
    tu: TimeUnit,
    fill: &str,
    fiscal_start: u8,
) -> Result<Vec<serde_json::Map<String, serde_json::Value>>, String> {
    use serde_json::Value;
    let dated: Vec<((i32, u8, u8), &serde_json::Map<String, Value>)> = rows
        .iter()
        .filter_map(|row| {
            let text = row.get(x_field)?.as_str()?;
            crate::x_type::parse_date(text).map(|(date, _)| (date, row))
        })
        .collect();
    let (Some(&min), Some(&max)) = (dated.iter().map(|(d, _)| d).min(), dated.iter().map(|(d, _)| d).max()) else {
        return Ok(rows.to_vec());
    };
    let mut buckets = Vec::new();
    let mut cursor = floor_to_bucket_start(min, tu, fiscal_start);
    while cursor <= max {
        buckets.push(cursor);
        cursor = next_bucket(cursor, tu);
    }
    // Each dimension's distinct values over every row, ascending, a null last
    // (`sorted(seen, key=lambda x: (x is None, x))`); the combinations are
    // their product, the last dimension varying fastest.
    let mut combos: Vec<Vec<Value>> = vec![Vec::new()];
    for dim in dim_fields {
        let mut values: Vec<Value> = Vec::new();
        for row in rows {
            let v = row.get(*dim).cloned().unwrap_or(Value::Null);
            if !values.contains(&v) {
                values.push(v);
            }
        }
        let numbers = values.iter().all(|v| matches!(v, Value::Null | Value::Number(_) | Value::Bool(_)));
        let texts = values.iter().all(|v| v.is_null() || v.is_string());
        if !numbers && !texts {
            return Err(format!("line: the values of the gap-filled series field {dim:?} mix numbers and text"));
        }
        values.sort_by(|a, b| match (a, b) {
            (Value::Null, Value::Null) => std::cmp::Ordering::Equal,
            (Value::Null, _) => std::cmp::Ordering::Greater,
            (_, Value::Null) => std::cmp::Ordering::Less,
            _ => crate::rows::raw_x_order(a, b).expect("one kind of value is ordered"),
        });
        combos = combos.iter().flat_map(|head| values.iter().map(move |v| [head.clone(), vec![v.clone()]].concat())).collect();
    }
    let combo_of = |row: &serde_json::Map<String, Value>| -> Vec<Value> {
        dim_fields.iter().map(|d| row.get(*d).cloned().unwrap_or(Value::Null)).collect()
    };
    let mut placed: Vec<Vec<Option<&serde_json::Map<String, Value>>>> = vec![vec![None; combos.len()]; buckets.len()];
    for (date, row) in &dated {
        let at = buckets.partition_point(|b| b <= date) - 1;
        let slot = combos.iter().position(|c| *c == combo_of(row)).expect("a row's dimension values are among the combinations");
        if placed[at][slot].replace(row).is_some() {
            return Err(format!("{x_field}: two rows fall in the {tu:?} bucket starting {:?}", buckets[at]));
        }
    }
    let first = rows.first().expect("dated rows imply a first row");
    let fill_value = if fill == "zero" { Value::from(0) } else { Value::Null };
    let mut out: Vec<serde_json::Map<String, Value>> = Vec::with_capacity(buckets.len() * combos.len());
    for (&(y, m, d), slots) in buckets.iter().zip(&placed) {
        for (combo, existing) in combos.iter().zip(slots) {
            let mut row = match existing {
                Some(row) => (*row).clone(),
                None => {
                    let mut synthesized: serde_json::Map<String, Value> =
                        first.keys().map(|k| (k.clone(), fill_value.clone())).collect();
                    for (dim, value) in dim_fields.iter().zip(combo) {
                        synthesized.insert((*dim).to_string(), value.clone());
                    }
                    synthesized
                }
            };
            row.insert(x_field.to_string(), Value::String(format!("{y:04}-{m:02}-{d:02}")));
            out.push(row);
        }
    }
    if !matches!(fill, "linear" | "step-after" | "step-before" | "step-center" | "curve") {
        return Ok(out);
    }
    let measures: Vec<String> =
        first.keys().filter(|k| k.as_str() != x_field && !dim_fields.contains(&k.as_str())).cloned().collect();
    for slot in 0..combos.len() {
        // One series' rows in bucket order; the interpolation never crosses series.
        let series: Vec<usize> = (0..buckets.len()).map(|b| b * combos.len() + slot).collect();
        for col in &measures {
            let anchors: Vec<usize> = (0..series.len()).filter(|&i| !out[series[i]][col].is_null()).collect();
            for i in 0..series.len() {
                if !out[series[i]][col].is_null() {
                    continue;
                }
                let left = anchors.iter().rev().find(|&&a| a < i).copied();
                let right = anchors.iter().find(|&&a| a > i).copied();
                let filled = match (fill, left, right) {
                    ("step-after", Some(l), _) => out[series[l]][col].clone(),
                    ("step-before", _, Some(r)) => out[series[r]][col].clone(),
                    ("step-after" | "step-before", _, _) => continue,
                    (_, Some(l), Some(r)) => {
                        let number = |at: usize| {
                            out[series[at]][col]
                                .as_f64()
                                .ok_or_else(|| format!("gap fill {fill:?}: {col:?} holds a non-numeric value"))
                        };
                        let (lv, rv) = (number(l)?, number(r)?);
                        let t = (i - l) as f64 / (r - l) as f64;
                        match fill {
                            "linear" => Value::from(lv + (rv - lv) * t),
                            "curve" => Value::from(lv + (rv - lv) * (t * t * (3.0 - 2.0 * t))),
                            _ => {
                                let mid = l as f64 + (r - l) as f64 / 2.0;
                                out[series[if (i as f64) < mid { l } else { r }]][col].clone()
                            }
                        }
                    }
                    _ => continue,
                };
                let target = series[i];
                out[target].insert(col.clone(), filled);
            }
        }
    }
    Ok(out)
}

/// Per-index utc-scale position fractions for a temporal domain: the
/// days normalized over the domain EXTENT — Vega's utc domain
/// is a min/max field aggregate (`domain: {fields: [...]}` in the compiled
/// specs), order-independent, so an unsorted or ragged multi-series union
/// still maps every value inside [0, 1]. `None` when any entry fails to
/// parse or the extent is degenerate.
pub fn utc_position_fractions(domain: &Dates) -> Option<Vec<f64>> {
    let extent = utc_domain_extent(domain)?;
    domain.day.iter().map(|day| day.map(|day| frac_of(day, extent))).collect()
}

/// `(min, max)` of a UTC domain in days-since-epoch — [`utc_position_fractions`]'s
/// extent, exposed so a caller can evaluate OTHER dates' fractions against
/// the same span (L9: the vertex domain floors to the week start, but the
/// opener labels' own raw dates are positioned against that floored
/// extent, not their own). `None` on the same guards as
/// `utc_position_fractions` (fewer than 2 entries, a parse failure, or a
/// degenerate span).
pub fn utc_domain_extent(domain: &Dates) -> Option<(f64, f64)> {
    let days: Vec<f64> = domain.day.iter().copied().collect::<Option<_>>()?;
    extent_of(&days)
}

/// Position fraction of `day` within an `extent` from [`extent_of`]. A day
/// outside the domain that produced the extent falls outside `[0, 1]`
/// (Vega drops an axis label whose scale position lands outside the range,
/// which is production's actual behavior for a raw weekly opener past the
/// floored domain's own end).
pub fn frac_of(day: f64, extent: (f64, f64)) -> f64 {
    (day - extent.0) / (extent.1 - extent.0)
}

/// The instant Vega-Lite's `timeUnit` transform puts a row at: its date
/// floored to the enclosing bucket of the encoding's grain — a Sunday for
/// `yearweek` (d3's `utcSunday.floor`), the 1st of the month, of the calendar quarter
/// or of the year for the coarser grains (`utcyearmonth` and friends floor
/// to the CALENDAR bucket whatever the fiscal start), the day itself for
/// `yearmonthdate`. A column grained by position already sits on bucket
/// starts; one grained by cadence ([`detect_cadence`]) does not, and its
/// marks, gridlines and scale domain all sit on the floored dates.
pub fn time_unit_floor((y, m, d): (i32, u8, u8), tu: TimeUnit) -> (String, f64) {
    let floored = floor_day(days_from_civil(y, m, d), tu);
    let (fy, fm, fd) = civil_from_days(floored);
    (format!("{fy:04}-{fm:02}-{fd:02}"), floored as f64)
}

#[cfg(test)]
mod bucket_tests {
    use super::*;

    #[test]
    fn iso_week_monday_always_lands_on_a_monday() {
        for year in [2020, 2021, 2023, 2024, 2025, 2028, 2032] {
            for week in 1..=iso_weeks_in_year(year) {
                let (y, m, d) = iso_week_monday(year, week).expect("regex-admitted week");
                assert_eq!(
                    weekday(y, m as u8, d as u8),
                    0,
                    "ISO week {week} of {year} anchored to a non-Monday: {y}-{m:02}-{d:02}"
                );
            }
        }
    }
}
