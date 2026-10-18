//! Temporal x-axis cadence for date-valued x axes.
//!
//! Production detects the time-unit grain from the row data at render time,
//! then coarsens the *visible* opener set (never the label text itself) until
//! consecutive labels clear each other. This module ports that logic into
//! Rust so line/area/bar charts can format date labels without Vega-Lite.
//! See [`resolve_label_visibility`] for the format-vs-visibility split.
//!
//! ## Coarsening ladder (in order tried)
//!
//! `YearMonthDate` → `YearWeek` (Monday rung) → `YearMonth` → `YearQuarter` → `Year`
//! `YearWeek`      → `YearMonth` → `YearQuarter` → `Year`
//!
//! The ladder loops (`resolve_temporal_label_visibility`): each rung is
//! measured and the next tried while `overlap.skip` allows; the day/week→month
//! leg is a structural promotion (unconditional once ≥2 months are spanned),
//! not a measured one.
//!
//! ## Label vocabulary (matches production)
//!
//! | Grain           | row1        | row2                           |
//! |-----------------|-------------|--------------------------------|
//! | `Year`          | "2024"      | ""                             |
//! | `YearQuarter`   | "Q1"        | "2024" at anchor / yr-start    |
//! | `YearMonth`     | "Jan"       | "2024" at anchor / Jan         |
//! | `YearWeek`      | "3" (day)   | "Jan'24" / "Jan" / ""          |
//! | `YearMonthDate` | "3" (day)   | "Jan'24" / "Jan" / ""          |
//!
//! An authored time format ([`AuthoredTimeFormat`]) replaces every row above
//! with its own single row of text, at every grain; the ladder measures that
//! text instead.

use crate::constants::axis::{LABEL_GAP_SPACES, LABEL_GAP_SPACES_NUMERIC, SPARSE_CEILING_PX};
use super::table_dates::{strftime, Moment};
use std::collections::BTreeMap;
use std::sync::LazyLock;

use regex::Regex;

/// Detected or authored calendar bucketing grain.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
pub enum TimeUnit {
    Year,
    YearQuarter,
    YearMonth,
    YearWeek,
    YearMonthDate,
}

/// True for a d3-time-format directive — `%` then an optional `-_0`
/// padding modifier then a letter. Port of production's `is_time_format`
/// (`text/format_d3.py`); `%%` is a literal-percent escape, stripped first.
pub fn is_time_format(fmt: &str) -> bool {
    let stripped = fmt.replace("%%", "");
    let chars: Vec<char> = stripped.chars().collect();
    chars.iter().enumerate().any(|(i, &c)| {
        if c != '%' {
            return false;
        }
        let next = if chars.get(i + 1).is_some_and(|m| matches!(m, '-' | '_' | '0')) {
            chars.get(i + 2)
        } else {
            chars.get(i + 1)
        };
        next.is_some_and(char::is_ascii_alphabetic)
    })
}

/// An `axis_x.labels.format` that is a d3 time format on a temporal x axis: the
/// label TEXT production paints one row per speaking tick
/// (`default_label_expr_for(authored_format=...)`, `time_unit_detect.py`) and
/// measures in the cadence ladder (`cadence_label_text`). The format decides
/// the text only; which ticks speak is still the opener gate and the ladder.
///
/// Validated once at [`AuthoredTimeFormat::parse`], so [`AuthoredTimeFormat::text`]
/// fails only on a date outside the years it can paint.
#[derive(Debug, Clone, Copy)]
pub struct AuthoredTimeFormat<'a>(&'a str);

/// The directives Vega paints (`utcFormat` of a UTC-midnight date, d3-time-format
/// en-US) and `portable_strftime` measures (`text/format_d3.py`, C locale)
/// identically, all inside production's compile-time gate
/// (`VEGA_SAFE_TIME_DIRECTIVES`). Every other letter refuses by name: it differs
/// between the two (`%Z %z %c %x %X`, the Python-only `%C %D %F ...`), is outside
/// the gate (`%g`, `%s`), is not ported to `table_dates::strftime` (`%q %L %Q`),
/// or is a time-of-day field a date-bucketed x axis does not carry.
const AUTHORED_DIRECTIVES: &str = "aAbBdeGjmuUVwWyY";

impl<'a> AuthoredTimeFormat<'a> {
    /// `None` for an absent format or a number format; `Some` for a d3 time
    /// format made only of [`AUTHORED_DIRECTIVES`]; `Err` naming the directive
    /// (or the spec fault) for anything else.
    pub fn parse(family: &str, labels_format: Option<&'a str>) -> Result<Option<Self>, String> {
        let Some(fmt) = labels_format.filter(|f| is_time_format(f)) else {
            return Ok(None);
        };
        let mut chars = fmt.chars();
        while let Some(c) = chars.next() {
            if c != '%' {
                continue;
            }
            let mut letter = chars.next();
            if letter == Some('%') {
                continue;
            }
            if matches!(letter, Some('-' | '_' | '0')) {
                letter = chars.next();
            }
            if let Some(l) = letter.filter(|l| l.is_ascii_alphabetic() && !AUTHORED_DIRECTIVES.contains(*l)) {
                return Err(format!(
                    "{family}: authored time format {fmt:?} uses %{l}, which a temporal x axis does \
                     not paint (supported directives: {})",
                    AUTHORED_DIRECTIVES.chars().map(|d| format!("%{d}")).collect::<Vec<_>>().join(" ")
                ));
            }
        }
        // The modifier and trailing-`%` faults `portable_strftime` rejects.
        strftime(Moment::midnight(2000, 1, 1), fmt)
            .map_err(|e| format!("{family}: authored time format {fmt:?}: {e}"))?;
        Ok(Some(Self(fmt)))
    }

    /// The text one tick paints: `portable_strftime` of the tick's date.
    /// d3 pads `%Y` to four digits where strftime does not, so a year before
    /// 1000 paints its `%Y` padded (`%-Y` bare, `%_Y` space-padded, `%0Y`
    /// zero-padded); a year before 100 refuses (JavaScript's Date reads it as
    /// 19xx).
    pub fn text(self, year: i32, month: u8, day: u8) -> Result<String, String> {
        // The real gap: Vega's timeunit transform yields NaN for a year below 100, so
        // production draws no tick for it and plots its mark at x 0 (path broken, domain
        // excluded); the "19xx" in the message is the ledger's identity, not the cause.
        if year < 100 {
            return Err(format!(
                "authored time format {:?} on the year {year} is not supported (JavaScript's Date reads years 0-99 as 19xx)",
                self.0
            ));
        }
        let moment = Moment::midnight(year as u32, month.into(), day.into());
        if year >= 1000 {
            return Ok(strftime(moment, self.0).expect("the spec was validated at parse"));
        }
        let mut spec = String::with_capacity(self.0.len() + 4);
        let mut chars = self.0.chars().peekable();
        while let Some(c) = chars.next() {
            if c != '%' {
                spec.push(c);
                continue;
            }
            match chars.next() {
                Some('Y') => spec.push_str(&format!("{year:04}")),
                Some(pad @ ('-' | '_' | '0')) if chars.peek() == Some(&'Y') => {
                    chars.next();
                    spec.push_str(&match pad {
                        '-' => year.to_string(),
                        '_' => format!("{year:>4}"),
                        _ => format!("{year:04}"),
                    });
                }
                Some(other) => {
                    spec.push('%');
                    spec.push(other);
                }
                None => spec.push('%'),
            }
        }
        Ok(strftime(moment, &spec).expect("the spec was validated at parse"))
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
    fn grain_unit(self) -> Option<&'static str> {
        match self {
            TimeUnit::Year => Some("year"),
            TimeUnit::YearQuarter => Some("yearquarter"),
            TimeUnit::YearMonth => Some("yearmonth"),
            TimeUnit::YearWeek => Some("yearweek"),
            TimeUnit::YearMonthDate => None,
        }
    }
}

/// The strftime spec a board redefined `tu`'s `date_<grain>` name to
/// (`ResolvedAxisStyle.grain_formats`), if it did.
pub fn grain_format(formats: &BTreeMap<String, String>, tu: Option<TimeUnit>) -> Option<&str> {
    formats.get(tu?.grain_unit()?).map(String::as_str)
}

/// `authored_time_format` (`_label_overlap.py`): `labels.format` when it is a
/// time spec, else the board's redefinition of the axis's detected grain.
pub fn axis_time_format<'a>(
    labels_format: Option<&'a str>,
    formats: &'a BTreeMap<String, String>,
    tu: Option<TimeUnit>,
) -> Option<&'a str> {
    labels_format.filter(|f| is_time_format(f)).or_else(|| grain_format(formats, tu))
}

/// A time `labels.format` on a standard date no calendar grain explains: the band
/// arm of a family (bar) refuses by name instead of painting raw band strings
/// where production labels every tick through the format.
pub fn refuse_authored_time_format(family: &str, labels_format: Option<&str>) -> Result<(), String> {
    match labels_format {
        Some(fmt) if is_time_format(fmt) => Err(format!(
            "{family}: an authored time `axis_x.labels.format` ({fmt:?}) on a temporal x is not \
             supported; production paints it as the label text"
        )),
        _ => Ok(()),
    }
}

/// What `resolve_label_time_unit` leaves an ordinal calendar axis to paint.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LabelUnit {
    /// A calendar grain's vocabulary: the authored `labels.time_unit`, else the
    /// encoding's own grain.
    Calendar(TimeUnit),
    /// `none` or a time-part grain: no smart `labelExpr`, so Vega paints the
    /// raw band strings. The part names the vocabulary the crowding test
    /// measures instead (`_GENERIC_FORMATTERS`).
    Raw(Option<TimePart>),
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

    /// `_GENERIC_FORMATTERS`: the text the crowding test measures for a date.
    fn text(self, y: i32, m: u8, d: u8, hour: u32) -> String {
        let moment = || Moment::midnight(y as u32, m.into(), d.into());
        match self {
            TimePart::MonthOfYear => strftime(moment(), "%b").expect("%b is a plain directive"),
            TimePart::DayOfWeek => strftime(moment(), "%a").expect("%a is a plain directive"),
            TimePart::DayOfMonth => d.to_string(),
            TimePart::DayOfYear => (days_from_civil(y, m, d) - days_from_civil(y, 1, 1) + 1).to_string(),
            TimePart::HourOfDay => hour.to_string(),
        }
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

    /// What Vega-Lite's own axis format paints at a tick of the part's scale
    /// (`timeUnitSpecifier`): `%b`, `%a`, `%d`, `%j`, `%H:00`.
    fn tick_text(self, instant: f64) -> String {
        let (y, m, d) = civil_from_days(instant.floor() as i64);
        match self {
            TimePart::MonthOfYear => MONTH_ABBR[(m - 1) as usize].to_string(),
            TimePart::DayOfWeek => WEEKDAY_ABBR[weekday(y, m, d) as usize].to_string(),
            TimePart::DayOfMonth => format!("{d:02}"),
            TimePart::DayOfYear => format!("{:03}", days_from_civil(y, m, d) - days_from_civil(y, 1, 1) + 1),
            TimePart::HourOfDay => format!("{:02}:00", clock_hour(instant)),
        }
    }

    /// Vega-Lite's default `tickCount` for the part's axis: none for the
    /// parts whose unit it exempts, else `ceil(plot_w / 40)`.
    fn default_tick_count(self, plot_w: f64) -> Option<usize> {
        match self {
            TimePart::MonthOfYear | TimePart::DayOfWeek | TimePart::HourOfDay => None,
            TimePart::DayOfMonth | TimePart::DayOfYear => {
                Some(crate::families::cartesian::vl_default_tick_count(plot_w).max(1))
            }
        }
    }

    /// The `tickMinStep` Vega-Lite compiles for the part's unit, in days.
    fn min_step_days(self) -> Option<f64> {
        match self {
            TimePart::MonthOfYear => Some(31.0),
            TimePart::DayOfWeek => None,
            TimePart::DayOfMonth | TimePart::DayOfYear => Some(1.0),
            TimePart::HourOfDay => Some(1.0 / 24.0),
        }
    }
}

fn clock_hour(day: f64) -> u32 {
    (((day - day.floor()) * 86_400.0).round() as u32 / 3600).min(23)
}

/// The labels whose widths the crowding test measures on a time-part axis
/// (`_generic_temporal_labels`): one text per band, a band being the distinct
/// text of the label vocabulary (the axis's part, an authored part, or an
/// authored calendar grain's single row); an authored format repaints each
/// band's first date.
pub fn part_band_texts(
    values: &[String],
    axis_part: TimePart,
    labels_time_unit: Option<&str>,
    authored: Option<AuthoredTimeFormat>,
) -> Result<Vec<String>, String> {
    let vocabulary = |(y, m, d): (i32, u8, u8), hour: u32| -> Result<String, String> {
        match labels_time_unit {
            None | Some("auto" | "none") => Ok(axis_part.text(y, m, d, hour)),
            Some(name) => match TimePart::parse(name) {
                Some(part) => Ok(part.text(y, m, d, hour)),
                None => {
                    let tu = parse_authored_time_unit(name)
                        .ok_or_else(|| format!("axis_x.labels.time_unit {name:?} is not a label time unit"))?;
                    pinned_label_text((y, m, d), tu, None)
                }
            },
        }
    };
    let mut bands: Vec<(String, (i32, u8, u8))> = Vec::new();
    for value in values {
        let day = date_text_day(value).ok_or_else(|| format!("x value {value:?} is not an ISO date"))?;
        let date = civil_from_days(day.floor() as i64);
        let label = vocabulary(date, clock_hour(day))?;
        if !bands.iter().any(|(seen, _)| *seen == label) {
            bands.push((label, date));
        }
    }
    bands
        .into_iter()
        .map(|(label, (y, m, d))| match authored {
            Some(format) => format.text(y, m, d),
            None => Ok(label),
        })
        .collect()
}

/// One tick of a time-part axis.
pub struct PartTick {
    /// The tick's instant, in days since the epoch.
    pub at: f64,
    /// Its position as a fraction of the scale domain.
    pub frac: f64,
    pub text: String,
}

/// Vega's ticks of a time-part axis: d3's local-time ladder over the part's
/// `domain` (days, fractional for a clock), `authored` replacing Vega-Lite's
/// default `tickCount` and both capped by the unit's `tickMinStep`
/// (vega-scale `tickCount`); `format` replaces the part's own text.
pub fn time_part_ticks(
    domain: (f64, f64),
    plot_w: f64,
    part: TimePart,
    authored: Option<usize>,
    format: Option<AuthoredTimeFormat>,
) -> Result<Vec<PartTick>, String> {
    let (lo, hi) = domain;
    let text = |at: f64| -> Result<String, String> {
        let (y, m, d) = civil_from_days(at.floor() as i64);
        match format {
            Some(f) => f.text(y, m, d),
            None => Ok(part.tick_text(at)),
        }
    };
    if lo == hi {
        return Ok(vec![PartTick { at: lo, frac: 0.5, text: text(lo)? }]);
    }
    // Vega asks d3 for ten ticks when no `tickCount` reaches the axis.
    let count = authored.or_else(|| part.default_tick_count(plot_w)).unwrap_or(10);
    let count = part.min_step_days().map_or(count, |step| count.min(min_step_ticks(hi - lo, step)));
    utc_instants(lo, hi, count)?
        .into_iter()
        .map(|at| Ok(PartTick { at, frac: (at - lo) / (hi - lo), text: text(at)? }))
        .collect()
}

impl LabelUnit {
    /// `resolve_label_time_unit` over `labels.time_unit`: unauthored or `auto`
    /// inherits the encoding grain `data_tu`.
    pub fn resolve(data_tu: TimeUnit, authored: Option<&str>) -> Result<Self, String> {
        Ok(match authored {
            None | Some("auto") => LabelUnit::Calendar(data_tu),
            Some("none") => LabelUnit::Raw(None),
            Some(other) => match TimePart::parse(other) {
                Some(part) => LabelUnit::Raw(Some(part)),
                None => LabelUnit::Calendar(
                    parse_authored_time_unit(other)
                        .ok_or_else(|| format!("axis_x.labels.time_unit {other:?} is not a label time unit"))?,
                ),
            },
        })
    }

    /// The calendar grain the axis's `date_<grain>` redefinition is keyed by.
    pub fn grain(self) -> Option<TimeUnit> {
        match self {
            LabelUnit::Calendar(tu) => Some(tu),
            LabelUnit::Raw(_) => None,
        }
    }

    /// The text a raw-labeled band's crowding is measured on: the author's
    /// format, else the time part's vocabulary, else the band string itself
    /// (`_generic_temporal_labels`).
    pub fn raw_measure_text(
        self,
        band: &str,
        authored: Option<AuthoredTimeFormat>,
    ) -> Result<String, String> {
        let LabelUnit::Raw(part) = self else {
            return Err("raw_measure_text on a calendar label unit".into());
        };
        let Some((y, m, d)) = parse_iso_date(band) else { return Ok(band.to_string()) };
        match (authored, part) {
            (Some(format), _) => format.text(y, m, d),
            (None, Some(part)) => Ok(part.text(y, m, d, date_text_day(band).map_or(0, clock_hour))),
            (None, None) => Ok(band.to_string()),
        }
    }
}

/// Next-coarser label cadence in the coarsening chain.
/// Returns `None` when already at `Year`.
pub fn next_coarser(tu: TimeUnit) -> Option<TimeUnit> {
    match tu {
        TimeUnit::YearMonthDate | TimeUnit::YearWeek => Some(TimeUnit::YearMonth),
        TimeUnit::YearMonth => Some(TimeUnit::YearQuarter),
        TimeUnit::YearQuarter => Some(TimeUnit::Year),
        TimeUnit::Year => None,
    }
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

// ── Date parsing ─────────────────────────────────────────────────────────────

/// A date cell as days since the epoch, with a `[T ]HH:MM[:SS[.f]]` clock as the
/// day's fraction and an optional `Z` or `±HH:MM` offset. A clock without an
/// offset is UTC (the render pins the zone). Fractional seconds keep V8's
/// millisecond precision. `None` for any other shape.
pub fn date_text_day(text: &str) -> Option<f64> {
    let digits = |part: &str, max: u32| -> Option<f64> {
        (part.len() == 2 && part.bytes().all(|b| b.is_ascii_digit()))
            .then(|| part.parse::<u32>().ok().filter(|&n| n < max).map(f64::from))
            .flatten()
    };
    let (y, m, d) = parse_iso_date(text.get(..text.len().min(10))?)?;
    let day = days_from_civil(y, m, d) as f64;
    if text.len() <= 10 {
        return Some(day);
    }
    if !matches!(text.as_bytes()[10], b'T' | b' ') {
        return None;
    }
    let clock = text.get(11..)?;
    let (clock, offset_minutes) = match clock.char_indices().find(|(_, c)| matches!(c, 'Z' | '+' | '-')) {
        None => (clock, 0.0),
        Some((i, 'Z')) if i + 1 == clock.len() => (&clock[..i], 0.0),
        Some((i, sign @ ('+' | '-'))) => {
            let zone = clock[i + 1..].split(':').collect::<Vec<_>>();
            let [hours, minutes] = zone[..] else { return None };
            let total = digits(hours, 24)? * 60.0 + digits(minutes, 60)?;
            (&clock[..i], if sign == '+' { total } else { -total })
        }
        Some(_) => return None,
    };
    let mut parts = clock.split(':');
    let hours = digits(parts.next()?, 24)?;
    let minutes = digits(parts.next()?, 60)?;
    let (seconds, millis) = match parts.next() {
        None => (0.0, 0.0),
        Some(field) => {
            let (whole, fraction) = field.split_once('.').map_or((field, ""), |(w, f)| (w, f));
            if field.contains('.') && (fraction.is_empty() || !fraction.bytes().all(|b| b.is_ascii_digit())) {
                return None;
            }
            let padded = format!("{fraction:0<3}");
            (digits(whole, 60)?, padded[..3].parse::<f64>().ok()?)
        }
    };
    if parts.next().is_some() {
        return None;
    }
    Some(day + (hours * 3600.0 + minutes * 60.0 + seconds + millis / 1000.0 - offset_minutes * 60.0) / 86_400.0)
}

/// Parse an ISO date string `"YYYY-MM-DD"` → `(year, month, day)`.
/// Also accepts `"YYYY-MM"` (day = 1), `"YYYY"` (month = 1, day = 1), and a
/// datetime `"YYYY-MM-DD[T ]…"` (date part; matches production's
/// _ISO_DATETIME_RE in time_unit_detect.py). Returns `None` for any other shape.
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
            if m >= 1 && m <= 12 && d >= 1 && d <= 31 {
                Some((y, m, d))
            } else {
                None
            }
        }
        7 if b[4] == b'-' => {
            let y = parse_digits4(&b[0..4])?;
            let m = parse_digits2(&b[5..7])?;
            if m >= 1 && m <= 12 {
                Some((y, m, 1))
            } else {
                None
            }
        }
        4 => {
            let y = parse_digits4(&b[0..4])?;
            Some((y, 1, 1))
        }
        _ => None,
    }
}

fn parse_digits4(b: &[u8]) -> Option<i32> {
    if b.iter().all(|c| c.is_ascii_digit()) {
        Some(
            (b[0] - b'0') as i32 * 1000
                + (b[1] - b'0') as i32 * 100
                + (b[2] - b'0') as i32 * 10
                + (b[3] - b'0') as i32,
        )
    } else {
        None
    }
}

fn parse_digits2(b: &[u8]) -> Option<u8> {
    if b.iter().all(|c| c.is_ascii_digit()) {
        Some((b[0] - b'0') * 10 + (b[1] - b'0'))
    } else {
        None
    }
}

/// Day-of-week (0=Mon..6=Sun) using Zeller's formula for the Gregorian calendar.
fn weekday(year: i32, month: u8, day: u8) -> u8 {
    let mut y = year;
    let mut m = month as i32;
    if m < 3 {
        m += 12;
        y -= 1;
    }
    let k = y % 100;
    let j = y / 100;
    // `day + ... - 2*j` can be negative (e.g. any Sunday in 1900-2000, where
    // `j == 19`), and Rust's `%` keeps the dividend's sign, not Python's —
    // `rem_euclid` matches the always-non-negative modulus Zeller's formula
    // assumes.
    let h = (day as i32 + (13 * (m + 1)) / 5 + k + k / 4 + j / 4 - 2 * j).rem_euclid(7);
    // h: 0=Sat, 1=Sun, 2=Mon…6=Fri  → convert to 0=Mon..6=Sun
    ((h + 5).rem_euclid(7)) as u8
}

// ── Time-unit detection ───────────────────────────────────────────────────────

/// Detect the calendar bucketing grain from a slice of ISO date strings.
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
/// Returns `None` if fewer than 2 distinct parseable values.
pub fn detect_time_unit(strs: &[&str]) -> Option<TimeUnit> {
    let mut dates: Vec<(i32, u8, u8)> = strs
        .iter()
        .filter_map(|s| parse_iso_date(s))
        .collect();
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
/// at least date-like, else nominal. Dates and strings share one wire form,
/// so a DATE column types the way its ISO text does.
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

pub fn infer_vega_type(rows: &[serde_json::Map<String, serde_json::Value>], field: &str) -> VegaType {
    use serde_json::Value;
    if !rows.first().is_some_and(|row| row.contains_key(field)) {
        return VegaType::Nominal;
    }
    let sample: Vec<&Value> = rows.iter().take(10).filter_map(|row| row.get(field)).filter(|v| !v.is_null()).collect();
    if sample.iter().all(|v| matches!(v, Value::Number(_) | Value::Bool(_))) {
        return VegaType::Quantitative;
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
    match (all_temporal, all_date_like) {
        (true, _) => VegaType::Temporal,
        (_, true) => VegaType::Ordinal,
        _ => VegaType::Nominal,
    }
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
fn is_standard_date_shape(s: &str) -> bool {
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

/// `is_date_like_string` (`type_inference.py`'s `DATE_LIKE_PATTERNS`) — the
/// 13 labeled-bucket shapes distinct from a standard date, e.g. `"2024-Q1"`,
/// `"FY2024"`, `"Week 3 2024"`, `"Jan 2024"`, `"Jan 15, 2024"`.
fn is_date_like_shape(s: &str) -> bool {
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

static DATE_LIKE_ISO_SHAPES: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^(?:\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$")
        .expect("a literal pattern compiles")
});

/// `is_date_like_string`: the shapes `DATE_LIKE_PATTERNS` lists — the labeled
/// buckets `is_date_like_shape` covers plus its three standard-date entries.
pub fn is_date_like_string(s: &str) -> bool {
    is_date_like_shape(s) || DATE_LIKE_ISO_SHAPES.is_match(s)
}

// ── `_BUCKET_FAMILIES` (`time_unit_detect.py:96-108`) ──────────────────────
//
// Production's own CLOSED family list, distinct from `DATE_LIKE_PATTERNS`
// (`is_date_like_shape` above): `normalize_labeled_temporal` rewrites a
// value matching one of THESE 11 regexes to an ISO anchor date before
// `resolve_cartesian_x_type` ever classifies the column, so a match
// resolves `temporal` in production (never `ordinal`) — `DATE_LIKE_
// PATTERNS` is a wider look-alike set VL type inference falls back to for
// values NEITHER standard-date NOR bucket-family shaped (e.g. a half-year,
// which matches `DATE_LIKE_PATTERNS` but no `_BUCKET_FAMILIES` entry, so it
// is never rewritten and resolves `ordinal`, sorted). Ported here
// byte-for-byte, not derived from `is_date_like_shape`: case sensitivity,
// value ranges (a month must be `01`-`12`, an ISO week `01`-`53`), and
// whitespace rules (`_WEEK_SPELLED_RE` requires `\s+` before the year,
// where `DATE_LIKE_PATTERNS`' own look-alike accepts `\s*`) all differ by
// design between the two lists, and approximating one from the other
// silently drops or admits the wrong shapes.
//
// `mm_dd_yyyy` (`_BUCKET_FAMILIES`'s own MM/DD/YYYY entry) is not
// re-checked here: production's OWN `is_standard_date` structural check
// already matches that shape directly, so `x_is_standard_date` covers it
// without a separate branch — a strict subset, so nothing is lost.

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
/// `None`). Reuses this module's own `days_from_civil`/`civil_from_days`
/// (Howard Hinnant's civil-calendar arithmetic, defined further down for
/// the tick-label emitters) rather than a second copy.
fn iso_week_monday(year: i32, week: u32) -> Option<(i32, u32, u32)> {
    if week < 1 || week > iso_weeks_in_year(year) {
        return None;
    }
    let jan4_weekday = weekday(year, 1, 4); // 0=Mon..6=Sun
    let week1_monday = days_from_civil(year, 1, 4) - i64::from(jan4_weekday);
    let (y, m, d) = civil_from_days(week1_monday + (i64::from(week) - 1) * 7);
    Some((y, u32::from(m), u32::from(d)))
}

pub(crate) fn is_leap_year(year: i32) -> bool {
    (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
}

pub(crate) fn days_in_month(year: i32, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if is_leap_year(year) {
                29
            } else {
                28
            }
        }
        _ => 0,
    }
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
pub(crate) fn parse_bucket_string(s: &str) -> Option<(i32, u32, u32)> {
    bucket_anchor(classify_bucket(s)?, s)
}

/// `normalize_labeled_temporal`: a year-shaped column becomes `YYYY-01-01`,
/// a column of one labeled-bucket family becomes each bucket's first day;
/// anything else is returned unchanged. The errors are the `ValueError`s
/// production raises for a mixed or unrecognized column.
pub fn normalize_labeled_temporal(
    rows: &[serde_json::Map<String, serde_json::Value>],
    field: &str,
) -> Result<Vec<serde_json::Map<String, serde_json::Value>>, String> {
    use serde_json::Value;
    let present: Vec<&Value> = rows.iter().filter_map(|r| r.get(field)).filter(|v| !v.is_null()).collect();
    let rewrite = |convert: &dyn Fn(&Value) -> Result<String, String>| -> Result<Vec<_>, String> {
        rows.iter()
            .map(|row| {
                let mut row = row.clone();
                if let Some(cell) = row.get_mut(field).filter(|c| !c.is_null()) {
                    *cell = Value::String(convert(cell)?);
                }
                Ok(row)
            })
            .collect()
    };
    if present.is_empty() {
        return Ok(rows.to_vec());
    }
    if crate::common::is_year_shaped(rows, field) {
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
        return Ok(rows.to_vec());
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

/// The ISO-anchored domain a year-shaped x column resolves to in
/// production — every value in `domain_strs` mapped to `YYYY-01-01` via
/// [`year_from_row_str`], mirroring `normalize_labeled_temporal`'s own
/// row-rewrite (`time_unit_detect.py:937-945`) rather than just detecting
/// its grain: production rewrites the DATA itself before anything
/// downstream (type inference, tick/label positions) ever runs, and this
/// is the port's one seam for that same rewrite — callers that need
/// year-shaped x parsed as a calendar date (position fractions, label
/// text) read this instead of the raw domain. `None` when any value
/// cannot anchor (a null x value, which `row_str` renders as `""`, is the
/// only shape `x_is_year_shaped` admits that still fails here) — the
/// caller refuses by name rather than silently fall through to even
/// nominal spacing.
pub fn year_shaped_iso_domain(domain_strs: &[&str]) -> Option<Vec<String>> {
    domain_strs.iter().map(|v| year_from_row_str(v).map(|y| format!("{y:04}-01-01"))).collect()
}

/// Parses a year from its `row_str`-rendered form — a bare integer
/// (`"2019"`) or an integral float (`"2019.0"`, the shape a warehouse
/// DECIMAL/NUMERIC year takes once `Decimal` round-trips through JSON as a
/// float, matching `common::is_year_shaped`'s own Decimal arm,
/// `utils.py:284-313`). A non-integral value, an empty string (a null x
/// value — `row_str` renders that as `""`), or any other unparseable
/// string returns `None` so the caller can refuse rather than silently
/// treat the domain as evenly spaced.
fn year_from_row_str(s: &str) -> Option<i32> {
    if let Ok(y) = s.trim().parse::<i32>() {
        return Some(y);
    }
    let f: f64 = s.trim().parse().ok()?;
    (f.fract() == 0.0).then_some(f as i32)
}

// ── Label cadence resolution ──────────────────────────────────────────────────
//
// Production (`time_unit_detect.py` + `emitters/_label_overlap.py`) separates
// two concepts this module used to conflate into one `label_tu`:
//
// - `format_tu` — the text vocabulary a label is written in ("Jan", "Q1",
//   "2024", a day number). Fixed once, early: either the data's own native
//   grain, or (for daily/weekly data whose native cadence doesn't fit) the
//   grain it gets *structurally promoted* to. It never changes again.
// - `visibility_tu` — which cadence-opener positions actually carry a label.
//   Starts equal to `format_tu`; may step ONE rung coarser
//   (`next_coarser`) if `format_tu`'s own openers don't clear each other.
//
// The two can diverge: 130 weekly points promote `format_tu` to `yearmonth`
// ("Jan"/"Apr"/...), and if even monthly openers don't clear, `visibility_tu`
// steps to `yearquarter` — but the *text* at those surviving ticks is still
// month names ("Jan Apr Jul Oct"), never "Q1 Q2 Q3 Q4". A label names the
// tick it sits on, never the coarser interval that tick opens.

/// True when `month` (1-12) opens a fiscal period of `period_len` months
/// under a fiscal year starting in `fiscal_start` (1-12). Port of
/// `_opens_fiscal_period` (`time_unit_detect.py`): `fiscal_start == 1`
/// reproduces the plain calendar check.
fn opens_fiscal_period(m: u8, period_len: i32, fiscal_start: u8) -> bool {
    (m as i32 - fiscal_start as i32).rem_euclid(period_len) == 0
}

/// True when `(year, month, day)` (data grain `data_tu`) opens a new period
/// at `label_tu`. Mirrors production's `is_label_opener`
/// (`time_unit_detect.py`): same-cadence or unhandled combinations fall
/// through to `true`. `fiscal_start` (1=Jan..12=Dec) shifts the
/// year/yearquarter boundary check — a no-op at the default `1`.
///
/// `pub(crate)`, not `pub`: `openers_at` above is this module's own axis-
/// label caller (dedups to one index per period); `support_table_strip`'s
/// `label_period_keep` is the other in-crate caller, mirroring production's
/// `_label_period_filter_expr` VL row filter, which runs the gate per row
/// with NO dedup — that caller needs the raw per-row predicate, not
/// `openers_at`'s label-specific set.
pub(crate) fn is_opener(
    y: i32,
    m: u8,
    d: u8,
    data_tu: TimeUnit,
    label_tu: TimeUnit,
    fiscal_start: u8,
) -> bool {
    match label_tu {
        TimeUnit::Year => {
            let year_open = opens_fiscal_period(m, 12, fiscal_start);
            if matches!(data_tu, TimeUnit::YearWeek | TimeUnit::YearMonthDate) {
                year_open && d <= 7
            } else {
                year_open && d == 1
            }
        }
        TimeUnit::YearQuarter => {
            let quarter_open = opens_fiscal_period(m, 3, fiscal_start);
            match data_tu {
                TimeUnit::YearWeek => quarter_open && d <= 7,
                _ => quarter_open && d == 1,
            }
        }
        TimeUnit::YearMonth => match data_tu {
            TimeUnit::YearWeek => d <= 7,
            TimeUnit::YearMonthDate => d == 1,
            _ => true,
        },
        TimeUnit::YearWeek => data_tu != TimeUnit::YearMonthDate || weekday(y, m, d) == 0,
        TimeUnit::YearMonthDate => true,
    }
}

/// Return every domain index that opens a `label_tu` period, deduplicated to
/// one index per period (first occurrence wins — mirrors `build_temporal_labels`'
/// own dedup, factored out so the fit test and the label builder agree on
/// exactly which ticks are "the opener set").
fn openers_at(
    dates: &[(i32, u8, u8)],
    data_tu: TimeUnit,
    label_tu: TimeUnit,
    fiscal_start: u8,
) -> Vec<usize> {
    let mut out = Vec::new();
    let mut prev_key: Option<(i32, u16)> = None;
    for (idx, &(y, m, d)) in dates.iter().enumerate() {
        if !is_opener(y, m, d, data_tu, label_tu, fiscal_start) {
            continue;
        }
        let key = opener_key(y, m, d, label_tu);
        if prev_key == Some(key) {
            continue;
        }
        prev_key = Some(key);
        out.push(idx);
    }
    out
}

/// True when the date at `(y, m, d)` sits on a native Vega calendar tick for
/// `format_tu` — used only to decide whether the trailing edge of a flushed
/// axis reserves its full label width (mirrors `_is_calendar_tick`).
fn is_calendar_tick(
    y: i32,
    m: u8,
    d: u8,
    data_tu: TimeUnit,
    format_tu: TimeUnit,
    fiscal_start: u8,
) -> bool {
    match format_tu {
        TimeUnit::Year => d == 1 && opens_fiscal_period(m, 12, fiscal_start),
        TimeUnit::YearQuarter => d == 1 && opens_fiscal_period(m, 3, fiscal_start),
        TimeUnit::YearMonth => d == 1,
        TimeUnit::YearWeek => weekday(y, m, d) == 0,
        TimeUnit::YearMonthDate => format_tu == data_tu,
    }
}

/// Rendered width (px) of the label's own row 1 at `dates[pos]` in
/// `format_tu`'s vocabulary — port of `_cadence_token_width`
/// (`time_unit_detect.py:235-284`). `yearmonth`/`yearquarter` return row 1
/// ALONE: their row 2 (the stacked year-context line) paints as a separate
/// text line and is checked as its own, independent clearance by
/// [`year_row_width`]/`pair_clears`, never folded into this width via
/// `max()`. `yearmonthdate` still folds its row 2 in here — production
/// leaves that grain unsplit too (`time_unit_detect.py:260-284`, an explicit
/// `max()` with its own docstring: the day path's row 2 is a three-way shape
/// `_pair_clears` has no independent check for). `yearweek` folds its row 2
/// here too, but production does NOT: `time_unit_detect.py:258-259` measures
/// `W%V` alone, no `max()`, no row 2 — a pre-existing over-reservation on
/// this grain (the old `label_token_width` folded identically), not
/// introduced here and not fixed here.
fn cadence_token_width(
    dates: &[(i32, u8, u8)],
    pos: usize,
    format_tu: TimeUnit,
    is_first: bool,
    fiscal_start: u8,
    authored: Option<AuthoredTimeFormat>,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<f64, String> {
    let (y, m, d) = dates[pos];
    let (row1, row2) = label_rows(y, m, d, format_tu, is_first, fiscal_start, authored)?;
    let w1 = measure(&row1)?;
    match format_tu {
        TimeUnit::YearMonth | TimeUnit::YearQuarter => Ok(w1),
        _ => {
            // `yearweek`/`yearmonthdate`: see the doc comment above.
            let w2 = if row2.is_empty() { 0.0 } else { measure(&row2)? };
            Ok(w1.max(w2))
        }
    }
}

/// Rendered width (px) of the stacked year-context row at `dates[pos]`, or
/// `None` when this tick doesn't carry one — port of `_year_row_width`,
/// gated exactly the way `_pair_clears` gates it (`format_tu` stacks a year
/// row at all only for `yearmonth`/`yearquarter`; even then, only some of
/// their ticks carry it — `format_label`'s row 2 is already empty on the
/// ticks that don't).
fn year_row_width(
    dates: &[(i32, u8, u8)],
    pos: usize,
    format_tu: TimeUnit,
    is_first: bool,
    fiscal_start: u8,
    authored: Option<AuthoredTimeFormat>,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<Option<f64>, String> {
    if !matches!(format_tu, TimeUnit::YearMonth | TimeUnit::YearQuarter) {
        return Ok(None);
    }
    let (y, m, d) = dates[pos];
    let (_, row2) = label_rows(y, m, d, format_tu, is_first, fiscal_start, authored)?;
    if row2.is_empty() {
        Ok(None)
    } else {
        Ok(Some(measure(&row2)?))
    }
}

/// True when labeled buckets at `labeled[a]`/`labeled[b]` (domain indices
/// `i < j`) do not overlap. Port of `_pair_clears`: clearance is `(j - i) *
/// band`; each side reserves half its label width, except a genuinely
/// flushed domain edge, which reserves its full width (`text-anchor:
/// start`/`end` doubles its reach from the edge instead of centering). Row 1
/// and the stacked year row are two independent clearances, not one
/// `max()`'d width (`_pair_clears:359-366`): row 1 of one tick can only ever
/// collide with row 1 of its neighbor; the year row only with a
/// neighbor's year row, and only when both ticks actually carry one.
fn pair_clears(
    labeled_pos_i: usize,
    i: usize,
    j: usize,
    dates: &[(i32, u8, u8)],
    data_tu: TimeUnit,
    format_tu: TimeUnit,
    band: f64,
    edge_labels_flushed: bool,
    fiscal_start: u8,
    authored: Option<AuthoredTimeFormat>,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<bool, String> {
    let is_first_i = labeled_pos_i == 0;
    let left_flush = edge_labels_flushed && i == 0;
    let clearance = (j - i) as f64 * band;

    let (yj, mj, dj) = dates[j];
    let right_flush = edge_labels_flushed
        && j == dates.len() - 1
        && is_calendar_tick(yj, mj, dj, data_tu, format_tu, fiscal_start);

    let wi = cadence_token_width(dates, i, format_tu, is_first_i, fiscal_start, authored, measure)?;
    let wj = cadence_token_width(dates, j, format_tu, false, fiscal_start, authored, measure)?;
    let left_extent = if left_flush { wi } else { wi / 2.0 };
    let right_extent = if right_flush { wj } else { wj / 2.0 };
    if left_extent + right_extent > clearance {
        return Ok(false);
    }

    let year_row_i = year_row_width(dates, i, format_tu, is_first_i, fiscal_start, authored, measure)?;
    let year_row_j = year_row_width(dates, j, format_tu, false, fiscal_start, authored, measure)?;
    if let (Some(year_row_i), Some(year_row_j)) = (year_row_i, year_row_j) {
        let left_year_extent = if left_flush { year_row_i } else { year_row_i / 2.0 };
        let right_year_extent = if right_flush { year_row_j } else { year_row_j / 2.0 };
        if left_year_extent + right_year_extent > clearance {
            return Ok(false);
        }
    }

    Ok(true)
}

/// True when every consecutive pair of `labeled` positions clears the gap
/// (port of `temporal_visibility_fits`).
fn visibility_fits(
    labeled: &[usize],
    dates: &[(i32, u8, u8)],
    data_tu: TimeUnit,
    format_tu: TimeUnit,
    band: f64,
    edge_labels_flushed: bool,
    fiscal_start: u8,
    authored: Option<AuthoredTimeFormat>,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<bool, String> {
    if labeled.len() < 2 {
        return Ok(true);
    }
    for pos in 0..labeled.len() - 1 {
        if !pair_clears(
            pos,
            labeled[pos],
            labeled[pos + 1],
            dates,
            data_tu,
            format_tu,
            band,
            edge_labels_flushed,
            fiscal_start,
            authored,
            measure,
        )? {
            return Ok(false);
        }
    }
    Ok(true)
}

/// Visibility coarsening once `format_tu` is fixed at a bucketed calendar
/// grain: [`resolve_visibility_loop`]'s ladder, with the vocabulary promoted
/// to the bare year at year cadence (`_temporal_layout`'s
/// `promoted_format_time_unit`). Returns `(format_tu, visibility_tu)`; tilt
/// and parity handle the remainder in production and are out of scope here.
fn resolve_visibility(
    dates: &[(i32, u8, u8)],
    data_tu: TimeUnit,
    format_tu: TimeUnit,
    band: f64,
    edge_labels_flushed: bool,
    fiscal_start: u8,
    skip: bool,
    authored: Option<AuthoredTimeFormat>,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<(TimeUnit, TimeUnit), String> {
    let (visibility_tu, _fits) = resolve_visibility_loop(
        dates,
        data_tu,
        format_tu,
        band,
        edge_labels_flushed,
        fiscal_start,
        skip,
        authored,
        measure,
    )?;
    let promoted = if visibility_tu == TimeUnit::Year { TimeUnit::Year } else { format_tu };
    Ok((promoted, visibility_tu))
}

/// Resolve `(format_tu, visibility_tu)` from the data grain and measured
/// label widths. Port of `_temporal_layout`'s bucketed-calendar branches.
///
/// An authored `axis_x.labels.time_unit` pins `format_tu` outright and skips
/// the day/week native/Monday-rung candidates below (production only tries
/// those when `axis.labels.time_unit in (None, "auto")`) — but it does
/// **not** skip [`resolve_visibility`]'s coarsening: production always runs
/// `resolve_temporal_label_visibility` on whatever `format_tu` it lands on,
/// authored or not (`_temporal_layout`'s generic bucketed branch has no
/// "authored, so skip" gate). An authored `yearmonthdate` format on a domain
/// too wide for daily labels still thins to month openers — it just never
/// gets promoted to `yearweek`/`yearmonth` text. `skip` is the axis's
/// `labels.overlap.skip`: the ladder steps coarser only under it.
///
/// An authored time format (`authored`) replaces the text and skips the same
/// native/Monday-rung candidates (`authored_format is None` gates both
/// branches), so day/week data enters the ladder at its own grain.
///
/// Without an authored format, weekly/daily data first tries its own native
/// cadence flat (every bucket labeled); daily also tries a Monday-only rung
/// before falling back further — but only when the axis is `bucket_aligned`
/// or `continuous_temporal` (`_label_overlap.py:570-573`:
/// `bucket_aligned_temporal or (continuous_temporal and encoding_time_unit
/// == "yearmonthdate")`; the candidate loop is `:576-598`).
/// Neither is a coarsening *step* — they're read as-is, only promoting to
/// `yearmonth` when the domain spans ≥2 months. These two checks add one
/// measured space of gap per label, the legacy arm's stand-in for
/// `_submonth_candidate_fits`' per-row gaps ([`submonth_candidate_fits`] is
/// the exact port the Vega arms use); [`resolve_visibility`]'s
/// `_pair_clears` carries no gap.
///
/// `bucket_aligned` mirrors production's `bucket_aligned_temporal` flag
/// (`curve == "step"`, `_cartesian.py:503`): bar's discrete/ordinal x-scale
/// always passes `true`; line/area pass `false` for every curve this Rust
/// port supports, since it never emits `step`.
///
/// `continuous_temporal` mirrors `vl_type == "temporal"`
/// (`_cartesian.py:505`, `bar.py:987`): line/area's utc-scale axis. It adds
/// the Monday rung to a daily axis and changes what is measured: every
/// calendar bucket between the extremes, whether or not a row falls in it
/// (`enumerated_axis_values`, `time_unit_detect.py:1132`;
/// `_label_overlap.py:539-546`), where an ordinal axis keeps one band per row
/// ([`enumerate_continuous_span`]).
pub fn resolve_label_visibility(
    dates: &[(i32, u8, u8)],
    data_tu: TimeUnit,
    authored_label_tu: Option<TimeUnit>,
    plot_width: f64,
    edge_labels_flushed: bool,
    bucket_aligned: bool,
    continuous_temporal: bool,
    fiscal_start: u8,
    skip: bool,
    authored: Option<AuthoredTimeFormat>,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<(TimeUnit, TimeUnit), String> {
    let span;
    let dates = if continuous_temporal {
        span = enumerate_continuous_span(dates, data_tu, fiscal_start);
        span.as_slice()
    } else {
        dates
    };
    if dates.len() < 2 {
        let tu = authored_label_tu.unwrap_or(data_tu);
        return Ok((tu, tu));
    }
    let band = plot_width / dates.len() as f64;

    if authored_label_tu.is_none()
        && authored.is_none()
        && matches!(data_tu, TimeUnit::YearWeek | TimeUnit::YearMonthDate)
    {
        // A single space's measured width stands in for production's
        // `label_gap_spaces`/`label_gap_spaces_numeric` (`_submonth_candidate_fits`)
        // — the only fit test in production with an explicit gap term.
        let gap = measure(" ")?;
        let mut gapped = |s: &str| -> Result<f64, String> { Ok(measure(s)? + gap) };
        let native = openers_at(dates, data_tu, data_tu, fiscal_start);
        if visibility_fits(
            &native,
            dates,
            data_tu,
            data_tu,
            band,
            edge_labels_flushed,
            fiscal_start,
            None,
            &mut gapped,
        )? {
            return Ok((data_tu, data_tu));
        }
        if (bucket_aligned || continuous_temporal) && data_tu == TimeUnit::YearMonthDate {
            let weekly = openers_at(dates, data_tu, TimeUnit::YearWeek, fiscal_start);
            if weekly.len() >= 2
                && visibility_fits(
                    &weekly,
                    dates,
                    data_tu,
                    TimeUnit::YearWeek,
                    band,
                    edge_labels_flushed,
                    fiscal_start,
                    None,
                    &mut gapped,
                )?
            {
                return Ok((TimeUnit::YearWeek, TimeUnit::YearWeek));
            }
        }
        let month_openers = openers_at(dates, data_tu, TimeUnit::YearMonth, fiscal_start);
        if month_openers.len() < 2 {
            return Ok((data_tu, data_tu));
        }
        return resolve_visibility(
            dates,
            data_tu,
            TimeUnit::YearMonth,
            band,
            edge_labels_flushed,
            fiscal_start,
            skip,
            None,
            measure,
        );
    }

    let format_tu = authored_label_tu.unwrap_or(data_tu);
    resolve_visibility(
        dates,
        data_tu,
        format_tu,
        band,
        edge_labels_flushed,
        fiscal_start,
        skip,
        authored,
        measure,
    )
}

/// One shared entry point for line/bar/area: measured widths + plot width →
/// the formatted x-axis label sequence.
///
/// `edge_labels_flushed` mirrors production's `temporal_edge_labels_flushed`
/// (`type_inference.py`): `true` for a genuinely temporal (continuous
/// utc-scale) x-axis — line/area, unless authored `axis_x.labels.flush:
/// false` — `false` for bar's discrete/ordinal x-scale. Vega flushes a
/// flushed axis's literal first tick to the plot's left edge, so that tick
/// reserves its *full* measured width in the fit test rather than half —
/// measured against `playground/xaxis-cadence-ladder`'s `nov_line` (16
/// monthly points, 484px plot): production's `Nov`→`Dec` pair only fails to
/// clear (triggering the month→quarter coarsening hop) because `Nov` is
/// flushed and reserves its full ~21px, not half; passing `false` here for a
/// continuous axis lets that pair "fit" and renders every month instead of
/// the quarterly openers production shows.
/// Returns the label sequence plus the visibility grain the ladder settled
/// on — `visibility_tu != data_tu` is production's "labels grew sparser than
/// buckets" fact (`visibility_thinned` / `label_tick_cadence` in
/// type_inference.py), which the bar family reads to restore its tick marks.
pub fn resolve_temporal_labels(
    domain: &[String],
    data_tu: TimeUnit,
    authored_label_tu: Option<TimeUnit>,
    plot_width: f64,
    edge_labels_flushed: bool,
    bucket_aligned: bool,
    continuous_temporal: bool,
    fiscal_start: u8,
    skip: bool,
    authored: Option<AuthoredTimeFormat>,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<(Vec<TemporalLabel>, TimeUnit), String> {
    let dates = parse_domain_dates(domain);
    let (format_tu, visibility_tu) = resolve_label_visibility(
        &dates,
        data_tu,
        authored_label_tu,
        plot_width,
        edge_labels_flushed,
        bucket_aligned,
        continuous_temporal,
        fiscal_start,
        skip,
        authored,
        measure,
    )?;
    Ok((
        build_temporal_labels(domain, data_tu, format_tu, visibility_tu, fiscal_start, authored)?,
        visibility_tu,
    ))
}

// ── Vega-arm overlap remedies (L1-L5) ───────────────────────────────────────
//
// The functions above (`resolve_label_visibility`/`resolve_temporal_labels`)
// stay untouched — they are what the legacy margin arm and bar's bucketed
// axis call, and both must keep drawing every label with no thinning
// (AGENTS.md: "the legacy margin arms draw every label"). Everything below
// is additive, called only from line/area's fit and rail-concat arms.


/// L2: fold a two-row temporal label onto one row for a steep tilt
/// (`abs(angle) >= 90`) — port of `_day_number_label`/`_year_context_row`
/// with `inline=True`. Day/week labels put the day number first
/// (`"1 Jan'24"`); month/quarter labels put the year context first
/// (`"2024 Jan"`, `"2024 Q1"`). A label with no context row (row2 empty,
/// e.g. an inner tick, or the bare `year` vocabulary) is returned as-is.
pub fn inline_label(label: &TemporalLabel, format_tu: TimeUnit) -> String {
    if label.row2.is_empty() {
        return label.row1.clone();
    }
    match format_tu {
        TimeUnit::YearWeek | TimeUnit::YearMonthDate => format!("{} {}", label.row1, label.row2),
        _ => format!("{} {}", label.row2, label.row1),
    }
}

/// Production's `steep_tilt`: an authored angle of 90 degrees or more.
pub fn is_steep_tilt(angle: f64) -> bool {
    angle.abs() >= 90.0
}

/// A steep authored angle flows each label's context row inline on one row.
pub fn fold_steep(labels: Vec<TemporalLabel>, angle: Option<f64>, format_tu: TimeUnit) -> Vec<TemporalLabel> {
    if !angle.is_some_and(is_steep_tilt) {
        return labels;
    }
    labels.into_iter().map(|l| TemporalLabel { row1: inline_label(&l, format_tu), row2: String::new(), ..l }).collect()
}

/// An authored `labels.expr` rewrites each label's text from the raw band
/// (`datum.value`): one row, no calendar vocabulary.
pub fn rewrite_with_expr(labels: Vec<TemporalLabel>, domain: &[String], n: i64) -> Vec<TemporalLabel> {
    labels
        .into_iter()
        .map(|l| TemporalLabel { row1: crate::common::apply_labels_expr(&domain[l.domain_idx], n), row2: String::new(), ..l })
        .collect()
}

/// Which buckets an authored `labels.expr` rewrites on an ordinal date axis.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ExprBands {
    /// Every bucket `values` keeps, ungated: a bar keeps one tick per bucket.
    AllBuckets,
    /// The ticks the cadence ladder keeps: a heatmap collapses `values` to them.
    KeptByLadder,
}

/// The labels an ordinal calendar-grain x paints, as `build_cartesian_x_encoding`
/// and `resolve_axis_x_overlap` decide them: `none` and the time-part grains paint
/// the raw band strings (crowded by `_generic_layout` over the author's format or
/// the time part's vocabulary), an authored `labels.angle` returns before the
/// cadence ladder, and an authored `labels.expr` rewrites the text of `expr_bands`.
pub struct OrdinalDateLabels {
    pub labels: Vec<TemporalLabel>,
    /// The axis keeps a tick at every bucket once its labels grow sparser than its
    /// buckets (`_LABEL_THINNING_TICK_MARK_TYPES`).
    pub ticks_restored: bool,
    /// The tilt the crowding remedies picked; an authored angle wins over it.
    pub angle: Option<f64>,
    /// Vega's `labelOverlap: parity` is asked for: label text only, never ticks.
    pub parity: bool,
}

#[allow(clippy::too_many_arguments)]
pub fn ordinal_date_labels(
    domain: &[String],
    data_tu: TimeUnit,
    unit: LabelUnit,
    labels_cfg: &crate::common::AxisLabels,
    fiscal_start: u8,
    authored: Option<AuthoredTimeFormat>,
    // The width the cadence ladder measures against, and the chart's own width
    // that the crowding remedies scale by `label_usable_ratio`.
    ladder_w: f64,
    chart_w: f64,
    usable_ratio: Option<f64>,
    expr_n: Option<i64>,
    expr_bands: ExprBands,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
    font_size: f64,
) -> Result<OrdinalDateLabels, String> {
    let skip = labels_cfg.overlap.as_ref().is_some_and(|o| o.skip);
    let (mut labels, ticks_restored, angle, parity) = match unit {
        LabelUnit::Raw(_) => {
            let (mut angle, mut parity) = (None, false);
            if let (None, Some(overlap)) = (labels_cfg.angle, &labels_cfg.overlap) {
                let ratio = usable_ratio.ok_or("label_usable_ratio absent (theme required for overlap)")?;
                let gap = measure(" ")?;
                let widths = domain
                    .iter()
                    .map(|band| unit.raw_measure_text(band, authored).and_then(|t| measure(&t).map(|w| w + gap)))
                    .collect::<Result<Vec<_>, _>>()?;
                let layout = generic_layout(
                    &widths,
                    chart_w * ratio,
                    overlap.skip,
                    overlap.tilt,
                    labels_cfg.tilt_increments.as_deref(),
                    font_size,
                )?;
                (angle, parity) = (Some(layout.angle), layout.parity);
            }
            let labels = domain
                .iter()
                .enumerate()
                .map(|(domain_idx, band)| TemporalLabel { domain_idx, row1: band.clone(), row2: String::new() })
                .collect();
            (labels, false, angle, parity)
        }
        LabelUnit::Calendar(label_tu) if labels_cfg.angle.is_some() => {
            let labels = build_temporal_labels(domain, data_tu, label_tu, label_tu, fiscal_start, authored)?;
            (fold_steep(labels, labels_cfg.angle, label_tu), label_tu != data_tu, None, false)
        }
        LabelUnit::Calendar(label_tu) => {
            let authored_label_tu = labels_cfg.time_unit.as_deref().filter(|u| *u != "auto").map(|_| label_tu);
            let (labels, visibility_tu) = resolve_temporal_labels(
                domain,
                data_tu,
                authored_label_tu,
                ladder_w,
                // An ordinal axis is never `vl_type == "temporal"`, so
                // `temporal_edge_labels_flushed` is false for it.
                false,
                true, // discrete/ordinal x-scale: bucket-aligned
                false,
                fiscal_start,
                skip,
                authored,
                measure,
            )?;
            (labels, visibility_tu != data_tu, None, false)
        }
    };
    if let Some(n) = expr_n {
        labels = match (unit, expr_bands) {
            (_, ExprBands::KeptByLadder) => labels,
            (LabelUnit::Calendar(label_tu), ExprBands::AllBuckets) if label_tu != data_tu => {
                build_temporal_labels(domain, data_tu, label_tu, label_tu, fiscal_start, authored)?
            }
            _ => (0..domain.len())
                .map(|domain_idx| TemporalLabel { domain_idx, row1: String::new(), row2: String::new() })
                .collect(),
        };
        labels = rewrite_with_expr(labels, domain, n);
    }
    Ok(OrdinalDateLabels { labels, ticks_restored, angle, parity })
}

/// L1's outcome once a native/bucketed cadence is chosen: whether Vega's
/// parity thinning is requested (`overlap.skip` fired and the flat test
/// failed) and the tilt angle (0 when untilted).
pub struct GenericLayout {
    pub parity: bool,
    pub angle: f64,
    /// `AxisLabelLayout.label_block_height`.
    pub block_height: f64,
    /// The labels measured when every enabled strategy was tried and they
    /// still collide (`AxisLabelLayout.collision_label_count`).
    pub collision: Option<usize>,
}

/// Port of `_generic_layout` (`emitters/_label_overlap.py`): flat first: if
/// `widths` clears `usable_width` (`_fits_flat` — mean-of-adjacent-pair vs
/// band, no flushed-edge doubling), draw every label flat. Otherwise, under
/// `overlap.skip`, halve to alternating labels and retry flat; failing that
/// (or with `overlap.skip` off), under `overlap.tilt` walk the ladder on
/// whatever's still under consideration (halved or full).
pub fn generic_layout(
    widths: &[f64],
    usable_width: f64,
    skip: bool,
    tilt: bool,
    tilt_increments: Option<&[f64]>,
    font_size: f64,
) -> Result<GenericLayout, String> {
    if widths.is_empty() {
        return Ok(GenericLayout { parity: false, angle: 0.0, block_height: font_size, collision: None });
    }
    let band0 = usable_width / widths.len() as f64;
    if crate::vega_fit::fits_flat(widths, band0) {
        return Ok(GenericLayout { parity: false, angle: 0.0, block_height: font_size, collision: None });
    }
    let mut parity = false;
    let mut considered: Vec<f64> = widths.to_vec();
    if skip {
        parity = true;
        let halved: Vec<f64> = widths.iter().step_by(2).copied().collect();
        let band1 = usable_width / halved.len().max(1) as f64;
        if crate::vega_fit::fits_flat(&halved, band1) {
            return Ok(GenericLayout { parity, angle: 0.0, block_height: font_size, collision: None });
        }
        considered = halved;
    }
    if tilt {
        let increments = tilt_increments
            .ok_or("line: labels.tilt_increments absent (theme required for overlap.tilt)")?;
        let band = usable_width / considered.len().max(1) as f64;
        let tilt = crate::vega_fit::pick_tilt(&considered, band, increments, font_size);
        return Ok(GenericLayout {
            parity,
            angle: tilt.angle,
            block_height: tilt.block_height,
            collision: (!tilt.fits).then_some(considered.len()),
        });
    }
    Ok(GenericLayout { parity, angle: 0.0, block_height: font_size, collision: Some(considered.len()) })
}

/// L4: the bucketed cadence ladder LOOPS — port of
/// `resolve_temporal_label_visibility`. Steps `next_coarser` repeatedly
/// (only when `allow_skip`, mirroring production's `overlap.skip` gate on
/// the loop itself — coarsening the label set is its own kind of thinning)
/// until a rung's own openers clear each other, preferring the finest rung
/// whose average spacing lands within [`SPARSE_CEILING_PX`] but accepting a
/// sparser clearing rung over none at all. Returns `(visibility_tu, fits)`.
fn resolve_visibility_loop(
    dates: &[(i32, u8, u8)],
    data_tu: TimeUnit,
    format_tu: TimeUnit,
    band: f64,
    edge_labels_flushed: bool,
    fiscal_start: u8,
    allow_skip: bool,
    authored: Option<AuthoredTimeFormat>,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<(TimeUnit, bool), String> {
    walk_label_cadence_ladder(dates, data_tu, format_tu, band, fiscal_start, allow_skip, &mut |labeled, vocab_tu| {
        visibility_fits(
            labeled,
            dates,
            data_tu,
            vocab_tu,
            band,
            edge_labels_flushed,
            fiscal_start,
            authored,
            measure,
        )
    })
}

/// `walk_label_cadence_ladder` (`time_unit_detect.py`): walks the label
/// cadence from `format_tu` coarser until `fits(labeled, vocab_tu)` clears,
/// preferring the finest rung no sparser than [`SPARSE_CEILING_PX`]. At
/// `year` cadence the clearance test itself uses the bare-year vocabulary
/// (every visible tick is a January, so measuring the ORIGINAL format_tu's
/// two-row shape there would test text nothing draws); every other rung keeps
/// `format_tu`. Direction-agnostic: the horizontal axis measures widths, the
/// vertical one heights.
fn walk_label_cadence_ladder(
    dates: &[(i32, u8, u8)],
    data_tu: TimeUnit,
    format_tu: TimeUnit,
    band: f64,
    fiscal_start: u8,
    allow_skip: bool,
    fits: &mut dyn FnMut(&[usize], TimeUnit) -> Result<bool, String>,
) -> Result<(TimeUnit, bool), String> {
    let mut unit = format_tu;
    let mut sparse_fit: Option<TimeUnit> = None;
    loop {
        let labeled = openers_at(dates, data_tu, unit, fiscal_start);
        let vocab_tu = if unit == TimeUnit::Year { TimeUnit::Year } else { format_tu };
        if fits(&labeled, vocab_tu)? {
            let spacing = if labeled.len() >= 2 {
                band * (labeled[labeled.len() - 1] - labeled[0]) as f64 / (labeled.len() - 1) as f64
            } else {
                0.0
            };
            if spacing <= SPARSE_CEILING_PX {
                return Ok((unit, true));
            }
            if sparse_fit.is_none() {
                sparse_fit = Some(unit);
            }
        }
        let next_unit = if allow_skip { next_coarser(unit) } else { None };
        let Some(next_unit) = next_unit else {
            return Ok(sparse_fit.map(|u| (u, true)).unwrap_or((unit, false)));
        };
        let next_labeled = openers_at(dates, data_tu, next_unit, fiscal_start);
        if next_labeled.len() < 2 {
            return Ok(sparse_fit.map(|u| (u, true)).unwrap_or((unit, false)));
        }
        unit = next_unit;
    }
}

/// A date band axis read top to bottom: the calendar labels that survive the
/// height-budgeted cadence, and whether that cadence cleared.
pub struct VerticalTemporalLabels {
    pub labels: Vec<TemporalLabel>,
    /// False when even the coarsest rung collides: production then hands the
    /// leftovers to Vega's `labelOverlap: greedy`.
    pub fits: bool,
}

/// `resolve_axis_y_overlap` (`emitters/_label_overlap.py`): the vertical twin
/// of the x cadence for a date-valued band axis. Two adjacent visible labels
/// clear when their band spacing covers half of each text block plus
/// `min_gap`. A block is `font_size` per row: two on the anchor and on a
/// year-opening month/quarter tick, two on every day/week tick, else one;
/// an authored time format paints one row everywhere.
/// `plot_height` is the chart height times `label_usable_ratio`.
pub fn resolve_vertical_temporal_labels(
    domain: &[String],
    data_tu: TimeUnit,
    plot_height: f64,
    fiscal_start: u8,
    skip: bool,
    font_size: f64,
    min_gap: f64,
    authored: Option<AuthoredTimeFormat>,
) -> Result<VerticalTemporalLabels, String> {
    let dates = parse_domain_dates(domain);
    let band = plot_height / dates.len() as f64;
    let (visibility_tu, fits) = walk_label_cadence_ladder(
        &dates,
        data_tu,
        data_tu,
        band,
        fiscal_start,
        skip,
        &mut |labeled, vocab_tu| {
            let anchor = chronological_first(&dates, labeled);
            let heights: Vec<f64> = labeled
                .iter()
                .enumerate()
                .map(|(k, &i)| {
                    let (y, m, d) = dates[i];
                    let rows = match vocab_tu {
                        _ if authored.is_some() => 1,
                        TimeUnit::Year => 1,
                        TimeUnit::YearWeek | TimeUnit::YearMonthDate => 2,
                        _ if k == anchor || is_opener(y, m, d, data_tu, TimeUnit::Year, fiscal_start) => 2,
                        _ => 1,
                    };
                    font_size * rows as f64
                })
                .collect();
            Ok(labeled.windows(2).enumerate().all(|(k, pair)| {
                (pair[1] - pair[0]) as f64 * band >= (heights[k] + heights[k + 1]) / 2.0 + min_gap
            }))
        },
    )?;
    let format_tu = if visibility_tu == TimeUnit::Year { TimeUnit::Year } else { data_tu };
    let labels = build_temporal_labels(domain, data_tu, format_tu, visibility_tu, fiscal_start, authored)?;
    Ok(VerticalTemporalLabels { labels, fits })
}

/// L1-L4's full Vega-arm layout: format/visibility grain, whether Vega's
/// parity thinning fires, and the tilt angle — everything
/// [`resolve_label_visibility`] decides for the legacy arm, plus the
/// overlap remedies production actually applies once the cadence choice is
/// made. Only line/area's fit and rail-concat arms call this.
pub struct VegaLabelLayout {
    pub format_tu: TimeUnit,
    pub visibility_tu: TimeUnit,
    /// Vega's own runtime `labelOverlap: parity` pass should run over the
    /// drawn labels' real ink (`vega_fit::parity_visible`) — true whenever
    /// `overlap.skip` fired against a band that didn't clear.
    pub parity: bool,
    pub angle: f64,
    /// `AxisLabelLayout.label_block_height`.
    pub block_height: f64,
    /// The labels measured when every enabled strategy was tried and they
    /// still collide (`AxisLabelLayout.collision_label_count`).
    pub collision: Option<usize>,
}

/// Port of `_temporal_layout`'s day/week-elif branch plus the bucketed
/// branch that follows it (`emitters/_label_overlap.py`). `format_tu` is
/// the caller's `authored_label_tu.unwrap_or(data_tu)`; `label_auto` is
/// whether `labels.time_unit` was unauthored/`"auto"`. Production's OWN
/// `BUCKETED_CALENDAR_UNITS` (`time_unit_detect.py:189-191`) is ALL FIVE
/// calendar grains — `year`, `yearquarter`, `yearmonth`, `yearweek`,
/// `yearmonthdate` — not just the three bucketed-axis grains bar's own
/// scaffold gate cares about, so `yearweek`/`yearmonthdate` are ordinary
/// starting rungs of the SAME bucketed ladder `resolve_vega_bucketed`
/// already walks for `year`/`yearquarter`/`yearmonth` — never refused,
/// never folded into the native branch. The native day/week SPECIAL branch
/// below (`_fits_flat` + the month-opener count / native-fits early
/// return) fires ONLY when `label_auto` — production's own gate is
/// `axis.labels.time_unit in (None, "auto")` — an authored day/week format
/// (whether or not it matches the detected grain) skips straight to the
/// bucketed ladder, starting AT that grain (`native_week`'s authored
/// `yearweek` steps week → month → quarter measuring the SAME two-row
/// vocabulary at each rung, landing on `yearquarter` visibility with
/// `yearweek` format — unit-test-pinned
/// (`authored_native_grain_enters_the_bucketed_ladder_at_its_own_grain`),
/// not a vl-convert probe).
/// Line/area is never `bucket_aligned_temporal` (that is `curve == "step"`,
/// which this port refuses). Its continuous utc-scale axis is
/// `continuous_temporal` (`vl_type == "temporal"`, `_cartesian.py:505`): over
/// daily data it tries the native cadence and then the Monday rung with
/// [`submonth_candidate_fits`] before the month-opener ladder
/// (`_label_overlap.py:570-598`), measured over every calendar bucket
/// between the extremes (`_label_overlap.py:539-546`; see
/// [`enumerate_continuous_span`]).
#[allow(clippy::too_many_arguments)]
pub fn resolve_vega_label_layout(
    dates: &[(i32, u8, u8)],
    data_tu: TimeUnit,
    format_tu: TimeUnit,
    label_auto: bool,
    usable_width: f64,
    edge_labels_flushed: bool,
    continuous_temporal: bool,
    fiscal_start: u8,
    skip: bool,
    tilt: bool,
    tilt_increments: Option<&[f64]>,
    font_size: f64,
    authored: Option<AuthoredTimeFormat>,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<VegaLabelLayout, String> {
    let span;
    let dates = if continuous_temporal {
        span = enumerate_continuous_span(dates, data_tu, fiscal_start);
        span.as_slice()
    } else {
        dates
    };
    if dates.len() < 2 {
        return Ok(VegaLabelLayout {
            format_tu,
            visibility_tu: format_tu,
            parity: false,
            angle: 0.0,
            block_height: font_size,
            collision: None,
        });
    }
    if continuous_temporal && data_tu == TimeUnit::YearMonthDate && label_auto && authored.is_none() {
        for candidate in [TimeUnit::YearMonthDate, TimeUnit::YearWeek] {
            if submonth_candidate_fits(dates, data_tu, candidate, fiscal_start, usable_width, measure)? {
                return Ok(VegaLabelLayout {
                    format_tu: candidate,
                    visibility_tu: candidate,
                    parity: false,
                    angle: 0.0,
                    block_height: font_size,
                    collision: None,
                });
            }
        }
        if openers_at(dates, data_tu, TimeUnit::YearMonth, fiscal_start).len() >= 2 {
            return resolve_vega_bucketed(
                dates,
                data_tu,
                TimeUnit::YearMonth,
                usable_width,
                edge_labels_flushed,
                fiscal_start,
                skip,
                tilt,
                tilt_increments,
                font_size,
                None,
                measure,
            );
        }
        // Fewer than two months to promote to: the Monday rung is the last
        // cadence, tilted rather than thinned (`_label_overlap.py:603-628`).
        let mondays = submonth_candidate_indices(dates, data_tu, TimeUnit::YearWeek);
        if mondays.is_empty() {
            return Err("line: a continuous daily axis spanning no Monday has no weekly rung".into());
        }
        let widths: Vec<f64> =
            mondays.iter().map(|&i| measure(&dates[i].2.to_string())).collect::<Result<_, _>>()?;
        let (angle, block_height, fits) = if tilt {
            let increments = tilt_increments
                .ok_or("line: labels.tilt_increments absent (theme required for overlap.tilt)")?;
            let pick = crate::vega_fit::pick_tilt(&widths, usable_width / widths.len() as f64, increments, font_size);
            (pick.angle, pick.block_height, pick.fits)
        } else {
            (0.0, font_size, crate::vega_fit::fits_flat(&widths, usable_width / widths.len() as f64))
        };
        return Ok(VegaLabelLayout {
            format_tu: TimeUnit::YearWeek,
            visibility_tu: TimeUnit::YearWeek,
            parity: false,
            angle,
            block_height,
            collision: (!fits).then_some(widths.len()),
        });
    }
    // Production's gate is `encoding_time_unit in {yearweek, yearmonthdate}
    // and axis.labels.time_unit in (None, "auto")` (`_label_overlap.py:287-289`)
    // — keyed on `data_tu` (encoding), not `format_tu`: an authored
    // `yearmonthdate` on MONTHLY data must NOT enter this native branch
    // (production wouldn't either), and an authored day/week format
    // matching the native grain must NOT enter it either (`label_auto`
    // false either way) — both fall through to the bucketed ladder below,
    // starting at `format_tu` itself.
    if matches!(data_tu, TimeUnit::YearWeek | TimeUnit::YearMonthDate) && label_auto && authored.is_none()
    {
        let gap = measure(" ")?;
        let mut gapped = |s: &str| -> Result<f64, String> { Ok(measure(s)? + gap) };
        let native_widths: Vec<f64> = (0..dates.len())
            .map(|pos| cadence_token_width(dates, pos, format_tu, pos == 0, fiscal_start, None, &mut gapped))
            .collect::<Result<_, _>>()?;
        let month_openers = openers_at(dates, data_tu, TimeUnit::YearMonth, fiscal_start);
        let native_fits = crate::vega_fit::fits_flat(
            &native_widths,
            usable_width / native_widths.len().max(1) as f64,
        );
        if month_openers.len() < 2 || (format_tu == TimeUnit::YearMonthDate && native_fits) {
            let gl = generic_layout(&native_widths, usable_width, skip, tilt, tilt_increments, font_size)?;
            return Ok(VegaLabelLayout {
                format_tu,
                visibility_tu: format_tu,
                parity: gl.parity,
                angle: gl.angle,
                block_height: gl.block_height,
                collision: gl.collision,
            });
        }
        return resolve_vega_bucketed(
            dates,
            data_tu,
            TimeUnit::YearMonth,
            usable_width,
            edge_labels_flushed,
            fiscal_start,
            skip,
            tilt,
            tilt_increments,
            font_size,
            None,
            measure,
        );
    }
    // Every other combination (a bucketed year/quarter/month format, OR an
    // authored day/week format regardless of label_auto) enters the SAME
    // bucketed ladder, starting at `format_tu` itself.
    resolve_vega_bucketed(
        dates,
        data_tu,
        format_tu,
        usable_width,
        edge_labels_flushed,
        fiscal_start,
        skip,
        tilt,
        tilt_increments,
        font_size,
        authored,
        measure,
    )
}

#[allow(clippy::too_many_arguments)]
fn resolve_vega_bucketed(
    dates: &[(i32, u8, u8)],
    data_tu: TimeUnit,
    format_tu: TimeUnit,
    usable_width: f64,
    edge_labels_flushed: bool,
    fiscal_start: u8,
    skip: bool,
    tilt: bool,
    tilt_increments: Option<&[f64]>,
    font_size: f64,
    authored: Option<AuthoredTimeFormat>,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<VegaLabelLayout, String> {
    let band = usable_width / dates.len() as f64;
    let (visibility_tu, fits) = resolve_visibility_loop(
        dates,
        data_tu,
        format_tu,
        band,
        edge_labels_flushed,
        fiscal_start,
        skip,
        authored,
        measure,
    )?;
    // L4: at `year` cadence the vocabulary promotes to the bare year — every
    // visible tick is a January, so "Jan" repeated everywhere carries no
    // information.
    let promoted_format_tu = if visibility_tu == TimeUnit::Year { TimeUnit::Year } else { format_tu };
    if fits {
        return Ok(VegaLabelLayout {
            format_tu: promoted_format_tu,
            visibility_tu,
            parity: false,
            angle: 0.0,
            block_height: font_size,
            collision: None,
        });
    }
    // L4's `visible_dates[::2]` (`_label_overlap.py:406-408`) feeds ONLY the
    // tilt-angle measurement below — it is never returned as the label set;
    // the emitted `values` stay every visibility-grain opener, unhalved,
    // with Vega's own runtime `labelOverlap: parity` (this `parity` flag)
    // doing the actual thinning against real ink.
    let mut visible = openers_at(dates, data_tu, visibility_tu, fiscal_start);
    let parity = skip && visibility_tu == TimeUnit::Year;
    if parity {
        visible = visible.into_iter().step_by(2).collect();
    }
    let widths: Vec<f64> = visible
        .iter()
        .enumerate()
        .map(|(pos, &p)| cadence_token_width(dates, p, promoted_format_tu, pos == 0, fiscal_start, authored, measure))
        .collect::<Result<_, _>>()?;
    // Without a tilt to try, `widths` is the set the visibility loop already
    // judged not to fit; only the year-cadence parity halving produced a set
    // that was never checked and needs a fresh verdict.
    let (angle, block_height, fits) = if tilt {
        let increments = tilt_increments
            .ok_or("line: labels.tilt_increments absent (theme required for overlap.tilt)")?;
        let band2 = usable_width / widths.len().max(1) as f64;
        let pick = crate::vega_fit::pick_tilt(&widths, band2, increments, font_size);
        (pick.angle, pick.block_height, pick.fits)
    } else {
        (0.0, font_size, parity && crate::vega_fit::fits_flat(&widths, usable_width / widths.len().max(1) as f64))
    };
    Ok(VegaLabelLayout {
        format_tu: promoted_format_tu,
        visibility_tu,
        parity,
        angle,
        block_height,
        collision: (!fits).then_some(widths.len()),
    })
}

/// L5: `labels.time_unit: "none"` disables dbt charts' smart labelExpr —
/// production emits no `labelExpr`/`values`, so Vega falls back to its own
/// per-timeUnit default single-row format (`type_inference.py:256`; probe-
/// pinned in `test_vl_probe.py::vl_default_temporal_format_by_grain`). Every
/// bucket keeps a label (no cadence coarsening — visibility stays at
/// `data_tu`); `YearWeek` is refused since this port carries no ISO
/// week-number arithmetic, and no corpus case authors `none` on weekly data.
pub fn vl_default_format(y: i32, m: u8, d: u8, tu: TimeUnit) -> Result<String, String> {
    match tu {
        TimeUnit::Year => Ok(format!("{y}")),
        TimeUnit::YearQuarter => display_text::language::tr(
            "time.year_quarter",
            None,
            &[("year", &y.to_string()), ("n", &((m - 1) / 3 + 1).to_string())],
        ),
        TimeUnit::YearMonth => Ok(format!("{} {y}", MONTH_ABBR[(m - 1) as usize])),
        TimeUnit::YearMonthDate => Ok(format!("{} {d:02}, {y}", MONTH_ABBR[(m - 1) as usize])),
        TimeUnit::YearWeek => {
            Err("line: labels.time_unit: none on weekly data is unmodeled (no ISO week arithmetic)".into())
        }
    }
}

/// The single row Vega paints at a date on a `labels.time_unit: none` axis:
/// the author's format, else the grain's own [`vl_default_format`].
fn none_label_text(y: i32, m: u8, d: u8, tu: TimeUnit, authored: Option<AuthoredTimeFormat>) -> Result<String, String> {
    match authored {
        Some(fmt) => fmt.text(y, m, d),
        None => vl_default_format(y, m, d, tu),
    }
}

/// The labels of a `labels.time_unit: none` bucketed axis on Vega's interval
/// ticks: no `labelExpr`, so every tick speaks in [`none_label_text`], an
/// empty bucket's included. `TemporalLabel.domain_idx` indexes `tick_days`.
pub fn none_tick_labels(
    tick_days: &[i64],
    data_tu: TimeUnit,
    authored: Option<AuthoredTimeFormat>,
) -> Result<Vec<TemporalLabel>, String> {
    tick_days
        .iter()
        .enumerate()
        .map(|(domain_idx, &day)| {
            let (y, m, d) = civil_from_days(day);
            Ok(TemporalLabel { domain_idx, row1: none_label_text(y, m, d, data_tu, authored)?, row2: String::new() })
        })
        .collect()
}

/// L5 in full: every native bucket keeps a label (no cadence coarsening),
/// text from [`vl_default_format`], then the SAME `_generic_layout`
/// parity/tilt remedy L1's native day/week branch runs — production
/// measures/tests the widths whatever the vocabulary, "none" included.
#[allow(clippy::too_many_arguments)]
fn resolve_none_layout(
    domain: &[String],
    data_tu: TimeUnit,
    fiscal_start: u8,
    usable_width: f64,
    skip: bool,
    tilt: bool,
    tilt_increments: Option<&[f64]>,
    font_size: f64,
    authored: Option<AuthoredTimeFormat>,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<(Vec<TemporalLabel>, GenericLayout), String> {
    let dated: Vec<(usize, (i32, u8, u8))> = domain
        .iter()
        .enumerate()
        .filter_map(|(idx, s)| parse_iso_date(s).map(|d| (idx, d)))
        .collect();
    let dates: Vec<(i32, u8, u8)> = dated.iter().map(|&(_, d)| d).collect();
    let openers = openers_at(&dates, data_tu, data_tu, fiscal_start);
    // Production's fit decision measures the author's format, else the RAW domain
    // values, plus `label_gap_spaces` (`_label_overlap.py:341-358`:
    // `format_time_unit` is "" for "none", so `_generic_temporal_labels` returns
    // `values` unchanged without a format) — a different, wider text than what Vega actually draws
    // (`vl_default_format`, no gap). Both are computed here: `widths` feeds
    // `generic_layout`'s flat/parity/tilt decision; `text` is what the
    // caller renders.
    let gap = measure(" ")?;
    let mut widths = Vec::with_capacity(openers.len());
    let mut labels = Vec::with_capacity(openers.len());
    for &p in &openers {
        let (domain_idx, (y, m, d)) = dated[p];
        widths.push(measure(&LabelUnit::Raw(None).raw_measure_text(&domain[domain_idx], authored)?)? + gap);
        labels.push(TemporalLabel { domain_idx, row1: none_label_text(y, m, d, data_tu, authored)?, row2: String::new() });
    }
    let gl = generic_layout(&widths, usable_width, skip, tilt, tilt_increments, font_size)?;
    Ok((labels, gl))
}

/// The text one band paints in `_generic_temporal_labels`' single-row
/// vocabulary (`cadence_label_text` and the `yearmonthdate` form), what
/// production measures for a tilt the author pinned.
fn pinned_label_text(
    (y, m, d): (i32, u8, u8),
    format_tu: TimeUnit,
    authored: Option<AuthoredTimeFormat>,
) -> Result<String, String> {
    if let Some(format) = authored {
        return format.text(y, m, d);
    }
    let spec = match format_tu {
        TimeUnit::Year => return Ok(y.to_string()),
        TimeUnit::YearQuarter => return Ok(format!("Q{}", (m - 1) / 3 + 1)),
        TimeUnit::YearMonth => "%b",
        TimeUnit::YearWeek => "W%V",
        TimeUnit::YearMonthDate => "%-d %b",
    };
    strftime(Moment::midnight(y as u32, m.into(), d.into()), spec)
}

/// `_pinned_angle_block_height` over a bucketed temporal axis: the widest
/// label across every band, measured bare, rotated by the author's `angle`.
pub fn pinned_block_height(
    domain: &[String],
    format_tu: TimeUnit,
    authored: Option<AuthoredTimeFormat>,
    angle: f64,
    font_size: f64,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<f64, String> {
    let mut widest = 0.0_f64;
    for date in parse_domain_dates(domain) {
        widest = widest.max(measure(&pinned_label_text(date, format_tu, authored)?)?);
    }
    Ok(crate::vega_fit::label_block_height(widest, font_size, angle))
}

/// The labels a Vega arm (fit or rail-concat) actually builds under L1-L5,
/// short of the ink-position-dependent runtime parity pass ([`vega_fit::
/// parity_visible`]) — only the caller knows its own arm's pixel geometry,
/// so `parity` here is a request the caller must still act on once it has
/// real label ink.
pub struct ResolvedVegaLabels {
    pub labels: Vec<TemporalLabel>,
    pub angle: f64,
    /// `AxisLabelLayout.label_block_height`.
    pub block_height: f64,
    pub parity: bool,
    pub collision: Option<usize>,
    pub format_tu: TimeUnit,
    pub visibility_tu: TimeUnit,
}

/// One shared entry point for line/area's fit and rail-concat arms: L1-L5
/// end to end except the runtime parity pass. `authored_label_tu_str` is
/// the raw `axis_x.labels.time_unit` string (so `"none"` — L5 — can be told
/// apart from unauthored/`"auto"`, which `parse_authored_time_unit` alone
/// cannot).
#[allow(clippy::too_many_arguments)]
pub fn resolve_vega_x_labels(
    domain: &[String],
    data_tu: TimeUnit,
    authored_label_tu_str: Option<&str>,
    usable_width: f64,
    edge_labels_flushed: bool,
    continuous_temporal: bool,
    fiscal_start: u8,
    skip: bool,
    tilt: bool,
    tilt_increments: Option<&[f64]>,
    font_size: f64,
    authored: Option<AuthoredTimeFormat>,
    authored_angle: Option<f64>,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<ResolvedVegaLabels, String> {
    if authored_label_tu_str == Some("none") {
        let (labels, gl) = resolve_none_layout(
            domain, data_tu, fiscal_start, usable_width, skip, tilt, tilt_increments, font_size, authored, measure,
        )?;
        return match authored_angle {
            None => Ok(ResolvedVegaLabels {
                labels,
                angle: gl.angle,
                block_height: gl.block_height,
                parity: gl.parity,
                collision: gl.collision,
                format_tu: data_tu,
                visibility_tu: data_tu,
            }),
            Some(angle) => {
                let mut widest = 0.0_f64;
                for value in domain {
                    widest = widest.max(measure(value)?);
                }
                Ok(ResolvedVegaLabels {
                    labels,
                    angle,
                    block_height: crate::vega_fit::label_block_height(widest, font_size, angle),
                    parity: false,
                    collision: None,
                    format_tu: data_tu,
                    visibility_tu: data_tu,
                })
            }
        };
    }
    // A time-PART grain (`monthofyear`/`dayofweek`/`dayofmonth`/
    // `dayofyear`/`hourofday` — `style/theme/axis.py`'s
    // `_TIME_UNIT_LITERAL`, alongside `auto`/`none`/the five calendar
    // grains this module already models) is legal to author but unmodeled
    // here: production routes it through `_generic_temporal_labels`, not
    // this bucketed calendar ladder (`_label_overlap.py:339-360` — that
    // format is not in `BUCKETED_CALENDAR_UNITS`). `parse_authored_time_unit`
    // returning `None` for it must not be read as "unauthored" (which
    // would silently misroute it into the bucketed ladder below, keyed on
    // `data_tu`'s vocabulary instead of refusing).
    if let Some(s) = authored_label_tu_str {
        if s != "auto" && parse_authored_time_unit(s).is_none() {
            return Err(format!(
                "line: axis_x.labels.time_unit {s:?} (a time-part grain) is unmodeled on the Vega arms"
            ));
        }
    }
    let authored_label_tu = authored_label_tu_str.and_then(parse_authored_time_unit);
    let format_tu = authored_label_tu.unwrap_or(data_tu);
    let label_auto = authored_label_tu_str.is_none() || authored_label_tu_str == Some("auto");
    if let Some(angle) = authored_angle {
        // An authored angle short-circuits the overlap ladder
        // (`resolve_axis_x_overlap`): every band keeps its label at the
        // axis's own grain.
        return Ok(ResolvedVegaLabels {
            labels: build_temporal_labels(domain, data_tu, format_tu, format_tu, fiscal_start, authored)?,
            angle,
            block_height: pinned_block_height(domain, format_tu, authored, angle, font_size, measure)?,
            parity: false,
            collision: None,
            format_tu,
            visibility_tu: format_tu,
        });
    }
    let dates = parse_domain_dates(domain);
    let layout = resolve_vega_label_layout(
        &dates,
        data_tu,
        format_tu,
        label_auto,
        usable_width,
        edge_labels_flushed,
        continuous_temporal,
        fiscal_start,
        skip,
        tilt,
        tilt_increments,
        font_size,
        authored,
        measure,
    )?;
    let labels = build_temporal_labels(
        domain,
        data_tu,
        layout.format_tu,
        layout.visibility_tu,
        fiscal_start,
        authored,
    )?;
    Ok(ResolvedVegaLabels {
        labels,
        angle: layout.angle,
        block_height: layout.block_height,
        parity: layout.parity,
        collision: layout.collision,
        format_tu: layout.format_tu,
        visibility_tu: layout.visibility_tu,
    })
}

// ── Label formatting ──────────────────────────────────────────────────────────

pub(crate) const MONTH_ABBR: [&str; 12] = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/// Full calendar-date text for a structured-tooltip identity header --
/// `tooltip_header_date_expr` (time_unit_detect.py:2159-2183). Unlike an
/// axis label, a tooltip header has no neighboring tick to borrow context
/// from, so it always renders the FULL bucket ("Feb 2024", never bare
/// "Feb"); a week reads as its start-to-end range (`_week_range_expr`).
/// `tu: None` is the continuous/sub-daily case, sharing
/// `YearMonthDate`'s day-level format (production's own `else` branch).
pub fn tooltip_header_date(y: i32, m: u8, d: u8, tu: Option<TimeUnit>) -> Result<String, String> {
    let mon = |m: u8| MONTH_ABBR[(m - 1) as usize];
    Ok(match tu {
        Some(TimeUnit::Year) => format!("{y}"),
        Some(TimeUnit::YearQuarter) => display_text::language::tr(
            "time.quarter_year",
            None,
            &[("year", &y.to_string()), ("n", &((m - 1) / 3 + 1).to_string())],
        )?,
        Some(TimeUnit::YearMonth) => format!("{} {y}", mon(m)),
        Some(TimeUnit::YearWeek) => {
            let (ey, em, ed) = civil_from_days(days_from_civil(y, m, d) + 6);
            if ey != y {
                format!("{d} {} {y} \u{2013} {ed} {} {ey}", mon(m), mon(em))
            } else if em != m {
                format!("{d} {} \u{2013} {ed} {} {ey}", mon(m), mon(em))
            } else {
                format!("{d}\u{2013}{ed} {} {ey}", mon(em))
            }
        }
        Some(TimeUnit::YearMonthDate) | None => format!("{d} {} {y}", mon(m)),
    })
}

/// A formatted two-row x-axis label.
#[derive(Debug, Clone, serde::Serialize)]
pub struct TemporalLabel {
    /// 0-based index into the x domain at which this label is placed.
    pub domain_idx: usize,
    /// Primary label row (always non-empty for visible ticks).
    pub row1: String,
    /// Context row — year or "Mon'YY" anchor, empty for inner ticks.
    pub row2: String,
}

/// Build the label sequence for a temporal x-axis.
///
/// Returns one `TemporalLabel` per opener position in `domain` at
/// `visibility_tu` (see [`resolve_label_visibility`] for why visibility and
/// text format are separate grains). Domain entries that are not openers at
/// `visibility_tu` are silently skipped (no label emitted at those x
/// positions); the ones that survive are rendered in `format_tu`'s
/// vocabulary — a label names the tick it sits on, never the coarser
/// interval `visibility_tu` may have stepped to.
pub fn build_temporal_labels(
    domain: &[String],
    data_tu: TimeUnit,
    format_tu: TimeUnit,
    visibility_tu: TimeUnit,
    fiscal_start: u8,
    authored: Option<AuthoredTimeFormat>,
) -> Result<Vec<TemporalLabel>, String> {
    // Keep each parsed date's real position in `domain` — unparseable entries
    // are dropped (as `parse_domain_dates` does), but `TemporalLabel.domain_idx`
    // must still name the original domain slot, not a position in the
    // filtered-down date list.
    let dated: Vec<(usize, (i32, u8, u8))> = domain
        .iter()
        .enumerate()
        .filter_map(|(idx, s)| parse_iso_date(s).map(|d| (idx, d)))
        .collect();
    let dates: Vec<(i32, u8, u8)> = dated.iter().map(|&(_, d)| d).collect();
    let openers = openers_at(&dates, data_tu, visibility_tu, fiscal_start);
    label_openers(&dated, &dates, &openers, format_tu, fiscal_start, authored)
}

/// [`build_temporal_labels`] for an unthinned axis: every domain entry is a
/// tick that speaks, whatever the opener rule would say of it (production
/// keeps `values` at the full bucket list until the labels thin).
pub fn build_all_temporal_labels(
    domain: &[String],
    format_tu: TimeUnit,
    fiscal_start: u8,
    authored: Option<AuthoredTimeFormat>,
) -> Result<Vec<TemporalLabel>, String> {
    let dated: Vec<(usize, (i32, u8, u8))> = domain
        .iter()
        .enumerate()
        .filter_map(|(idx, s)| parse_iso_date(s).map(|d| (idx, d)))
        .collect();
    let dates: Vec<(i32, u8, u8)> = dated.iter().map(|&(_, d)| d).collect();
    let all: Vec<usize> = (0..dated.len()).collect();
    label_openers(&dated, &dates, &all, format_tu, fiscal_start, authored)
}

fn label_openers(
    dated: &[(usize, (i32, u8, u8))],
    dates: &[(i32, u8, u8)],
    openers: &[usize],
    format_tu: TimeUnit,
    fiscal_start: u8,
    authored: Option<AuthoredTimeFormat>,
) -> Result<Vec<TemporalLabel>, String> {
    let anchor = chronological_first(dates, openers);
    openers
        .iter()
        .enumerate()
        .map(|(pos, &p)| {
            let (domain_idx, (y, m, d)) = dated[p];
            let (row1, row2) = label_rows(y, m, d, format_tu, pos == anchor, fiscal_start, authored)?;
            Ok(TemporalLabel { domain_idx, row1, row2 })
        })
        .collect()
}

/// Position within `labeled` of the earliest date: the tick that carries the
/// year-context row, wherever a descending domain paints it.
fn chronological_first(dates: &[(i32, u8, u8)], labeled: &[usize]) -> usize {
    (0..labeled.len()).min_by_key(|&k| dates[labeled[k]]).unwrap_or(0)
}

/// Deduplication key for a cadence opener. Only the bucketed grains
/// (`year`/`yearquarter`/`yearmonth`) collapse multiple data points into one
/// period — `yearweek`/`yearmonthdate` are the finest grains this module
/// ever labels at, so every date that opens one is its own period (a week's
/// worth of daily data collapsing to a single Monday would be a second,
/// unwanted coarsening step hiding inside "dedup").
fn opener_key(year: i32, month: u8, day: u8, label_tu: TimeUnit) -> (i32, u16) {
    match label_tu {
        TimeUnit::Year => (year, 0),
        TimeUnit::YearQuarter => (year, (month - 1) as u16 / 3),
        TimeUnit::YearMonth => (year, month as u16),
        TimeUnit::YearWeek | TimeUnit::YearMonthDate => (year, month as u16 * 100 + day as u16),
    }
}

/// The rows one tick paints: the author's own format on one row (no stacked
/// context row — `cadence_label_text`, `_authored_format_label`), else
/// [`format_label`]'s per-grain vocabulary.
fn label_rows(
    year: i32,
    month: u8,
    day: u8,
    label_tu: TimeUnit,
    is_first: bool,
    fiscal_start: u8,
    authored: Option<AuthoredTimeFormat>,
) -> Result<(String, String), String> {
    match authored {
        Some(format) => Ok((format.text(year, month, day)?, String::new())),
        None => format_label(year, month, day, label_tu, is_first, fiscal_start),
    }
}

/// Format a single cadence-opener date into (row1, row2).
///
/// Matches the production label vocabulary described in `time_unit_detect.py`:
/// - `year`:         ("2024", "")
/// - `yearquarter`:  ("Q1",   "2024" at anchor/yr-start, else "")
/// - `yearmonth`:    ("Jan",  "2024" at anchor/Jan, else "")
/// - `yearweek`/`yearmonthdate`:
///                   (day_of_month, "Jan'24" at anchor/yr-start opener,
///                                  "Jan" at other month openers, else "")
fn format_label(
    year: i32,
    month: u8,
    day: u8,
    label_tu: TimeUnit,
    is_first: bool,
    fiscal_start: u8,
) -> Result<(String, String), String> {
    // Mirrors `_fiscal_month_is_year_start`: true when `month` falls in the
    // fiscal year's first month. A no-op at the default `fiscal_start == 1`.
    let is_year_start = opens_fiscal_period(month, 12, fiscal_start);
    let year2 = year % 100;

    Ok(match label_tu {
        TimeUnit::Year => (format!("{year}"), String::new()),

        TimeUnit::YearQuarter => {
            // Mirrors `_quarter_label`'s `_fiscal_month_expr`: shift the
            // 0-indexed calendar month so 0 lines up with `fiscal_start`.
            let offset = fiscal_start as i32 - 1;
            let fiscal_month = (month as i32 - 1 - offset).rem_euclid(12);
            let q = fiscal_month / 3 + 1;
            let row1 = display_text::language::tr("time.quarter", None, &[("n", &q.to_string())])?;
            let row2 = if is_first || is_year_start {
                format!("{year}")
            } else {
                String::new()
            };
            (row1, row2)
        }

        TimeUnit::YearMonth => {
            let row1 = MONTH_ABBR[(month - 1) as usize].to_string();
            let row2 = if is_first || is_year_start {
                format!("{year}")
            } else {
                String::new()
            };
            (row1, row2)
        }

        TimeUnit::YearWeek | TimeUnit::YearMonthDate => {
            let row1 = format!("{day}");
            // Row 2: "Mon'YY" at anchor or (month-opener AND year-start), "Mon" at other month openers
            let opens_month = match label_tu {
                TimeUnit::YearWeek => day <= 7,
                _ => day == 1,
            };
            let row2 = if is_first || (opens_month && is_year_start) {
                // Anchor or fiscal-year-opening month opener: "Jan'24"
                let mon = MONTH_ABBR[(month - 1) as usize];
                format!("{mon}'{year2:02}")
            } else if opens_month {
                // Other month openers: bare month name
                MONTH_ABBR[(month - 1) as usize].to_string()
            } else {
                String::new()
            };
            (row1, row2)
        }
    })
}

/// Parse distinct ISO date strings from the x domain.
/// Non-parseable values are silently dropped (treated as non-temporal).
pub fn parse_domain_dates(domain: &[String]) -> Vec<(i32, u8, u8)> {
    domain.iter().filter_map(|s| parse_iso_date(s)).collect()
}

/// Days since the civil epoch 1970-01-01 (Howard Hinnant's `days_from_civil`).
/// The utc-scale position fraction `(d[i] − d[0]) / (d[n−1] − d[0])` computed
/// on day numbers equals vega's millisecond fraction exactly for the
/// date-grained buckets `TimeUnit` models.
pub fn days_from_civil(y: i32, m: u8, d: u8) -> i64 {
    let y = i64::from(y) - i64::from(m <= 2);
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (i64::from(m) + 9) % 12;
    let doy = (153 * mp + 2) / 5 + i64::from(d) - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// Inverse of `days_from_civil` (Hinnant's `civil_from_days`).
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

/// Start of the bucket enclosing `date` (`_floor_to_bucket_start`): year
/// and quarter floor to their fiscal-anchored period, month to its first
/// day; a week's start is whatever weekday the data uses and a day is its
/// own bucket, so both stay as they are.
pub(crate) fn floor_to_bucket_start(date: (i32, u8, u8), tu: TimeUnit, fiscal_start: u8) -> (i32, u8, u8) {
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

/// The ISO start of the bucket `text` falls inside (`_enclosing_bucket`) when it
/// is not already a bucket start: a month-end, quarter-end or off-anchor
/// yearly date is keyed by the bucket that contains it. `None` for a date on its
/// bucket's start, an unparseable value, and a week (its start is wherever the
/// data's own first week puts it).
pub fn off_anchor_bucket_start(text: &str, tu: TimeUnit, fiscal_start: u8) -> Option<String> {
    if tu == TimeUnit::YearWeek {
        return None;
    }
    let date = parse_iso_date(text)?;
    let start = floor_to_bucket_start(date, tu, fiscal_start);
    (start != date).then(|| format!("{:04}-{:02}-{:02}", start.0, start.1, start.2))
}

/// The bucket after `date` (`_next_bucket`): a fixed 1/3/12-month or 1/7-day
/// step, which keeps a fiscal anchor's month once the bucket start is anchored.
pub(crate) fn next_bucket(date: (i32, u8, u8), tu: TimeUnit) -> (i32, u8, u8) {
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


/// Port of `_submonth_candidate_indices` (`_label_overlap.py:254-259`): the
/// buckets a daily axis labels at the weekly candidate are its Mondays.
fn submonth_candidate_indices(dates: &[(i32, u8, u8)], data_tu: TimeUnit, candidate: TimeUnit) -> Vec<usize> {
    if data_tu == TimeUnit::YearMonthDate && candidate == TimeUnit::YearWeek {
        (0..dates.len()).filter(|&i| weekday(dates[i].0, dates[i].1, dates[i].2) == 0).collect()
    } else {
        (0..dates.len()).collect()
    }
}

/// Port of `_submonth_candidate_fits` (`_label_overlap.py:262-297`): whether
/// `candidate`'s labels over daily/weekly `dates` clear each other. Each of
/// the day-number row and the context row is its own clearance, so a row's
/// neighbors only compete with each other; the clearance between two labels
/// is the calendar distance of their indices times the per-bucket band.
fn submonth_candidate_fits(
    dates: &[(i32, u8, u8)],
    data_tu: TimeUnit,
    candidate: TimeUnit,
    fiscal_start: u8,
    usable_width: f64,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<bool, String> {
    let indices = submonth_candidate_indices(dates, data_tu, candidate);
    if indices.is_empty() {
        return Ok(false);
    }
    let band = usable_width / dates.len() as f64;
    let space = measure(" ")?;
    let mut day_row: Vec<(usize, String)> = Vec::with_capacity(indices.len());
    let mut context_row: Vec<(usize, String)> = Vec::new();
    for (position, &index) in indices.iter().enumerate() {
        let (y, m, d) = dates[index];
        let (day, context) = format_label(y, m, d, candidate, position == 0, fiscal_start)?;
        day_row.push((index, day));
        if !context.is_empty() {
            context_row.push((index, context));
        }
    }
    for (row, gap) in [
        (day_row, LABEL_GAP_SPACES_NUMERIC as f64 * space),
        (context_row, LABEL_GAP_SPACES as f64 * space),
    ] {
        for pair in row.windows(2) {
            let ((left_index, left), (right_index, right)) = (&pair[0], &pair[1]);
            let widths = measure(left)? + gap + measure(right)? + gap;
            if widths / 2.0 > (right_index - left_index) as f64 * band {
                return Ok(false);
            }
        }
    }
    Ok(true)
}

/// Every bucket from the floored minimum to the maximum of `dates`: the
/// dates a continuous temporal axis measures, whether or not a row falls in
/// each bucket (`enumerated_axis_values` → `_enumerate_buckets`,
/// `time_unit_detect.py:1132`; used at `_label_overlap.py:539-546`). The
/// ladder decides fit and cadence over these; label texts are still built
/// from the rows.
fn enumerate_continuous_span(dates: &[(i32, u8, u8)], tu: TimeUnit, fiscal_start: u8) -> Vec<(i32, u8, u8)> {
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
pub fn complete_buckets(domain: &[String], tu: TimeUnit, fiscal_start: u8) -> Vec<String> {
    let mut by_date: Vec<((i32, u8, u8), &String)> =
        domain.iter().filter_map(|s| parse_iso_date(s).map(|d| (d, s))).collect();
    by_date.sort();
    let Some(&(min, _)) = by_date.first() else { return Vec::new() };
    let max = by_date.last().map(|&(d, _)| d).unwrap();
    let mut out = Vec::new();
    let mut cursor = floor_to_bucket_start(min, tu, fiscal_start);
    let mut present = by_date.iter().peekable();
    while cursor <= max {
        let mut matched = false;
        while let Some(&&(d, s)) = present.peek() {
            if d < cursor {
                present.next();
            } else if d == cursor {
                out.push(s.clone());
                present.next();
                matched = true;
            } else {
                break;
            }
        }
        if !matched {
            let (y, m, d) = cursor;
            out.push(format!("{y:04}-{m:02}-{d:02}"));
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
            super::x_type::parse_date(text).map(|(date, _)| (date, row))
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
            _ => crate::common::raw_x_order(a, b).expect("one kind of value is ordered"),
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

/// Per-index utc-scale position fractions for a temporal domain: parsed
/// day-number offsets normalized over the domain EXTENT — Vega's utc domain
/// is a min/max field aggregate (`domain: {fields: [...]}` in the compiled
/// specs), order-independent, so an unsorted or ragged multi-series union
/// still maps every value inside [0, 1]. `None` when any entry fails to
/// parse or the extent is degenerate — callers keep their point-scale
/// fractions in that case.
pub fn utc_position_fractions(domain: &[String]) -> Option<Vec<f64>> {
    let extent = utc_domain_extent(domain)?;
    domain.iter().map(|s| utc_fraction_at(s, extent)).collect()
}

/// `(min, max)` of a UTC domain in days-since-epoch — [`utc_position_fractions`]'s
/// extent, exposed so a caller can evaluate OTHER dates' fractions against
/// the same span (L9: the vertex domain floors to the week start, but the
/// opener labels' own raw dates are positioned against that floored
/// extent, not their own). `None` on the same guards as
/// `utc_position_fractions` (fewer than 2 entries, a parse failure, or a
/// degenerate span).
pub fn utc_domain_extent(domain: &[String]) -> Option<(i64, i64)> {
    if domain.len() < 2 {
        return None;
    }
    let days: Vec<i64> = domain
        .iter()
        .map(|s| parse_iso_date(s).map(|(y, m, d)| days_from_civil(y, m, d)))
        .collect::<Option<_>>()?;
    let d0 = *days.iter().min().unwrap();
    let dn = *days.iter().max().unwrap();
    if dn <= d0 {
        return None;
    }
    Some((d0, dn))
}

/// Position fraction of `date` within an `extent` from [`utc_domain_extent`].
/// Unlike [`utc_position_fractions`], `date` need not be a member of the
/// domain that produced `extent` — the fraction can fall outside `[0, 1]`
/// when it doesn't (Vega drops an axis label whose scale position lands
/// outside the range, which is production's actual behavior for a raw
/// weekly opener past the floored domain's own end).
pub fn utc_fraction_at(date: &str, extent: (i64, i64)) -> Option<f64> {
    let (y, m, d) = parse_iso_date(date)?;
    let days = days_from_civil(y, m, d);
    Some((days - extent.0) as f64 / (extent.1 - extent.0) as f64)
}

/// Floors an ISO date to its containing week's Sunday — d3's
/// `utcSunday.floor`, the transform Vega-Lite's `utcyearweek` timeUnit
/// applies to every value under that encoding (both the scale domain and
/// each mark's own position). Production keeps the RAW (unfloored) dates
/// for its opener `values` (`label_opener_values`,
/// `time_unit_detect.py:1376`), so a weekly axis's ticks/labels sit off
/// their own vertex by the data's weekday offset from Sunday.
pub fn week_floor(date: &str) -> Option<String> {
    let (y, m, d) = parse_iso_date(date)?;
    let days = days_from_civil(y, m, d) - (i64::from(weekday(y, m, d)) + 1) % 7;
    let (fy, fm, fd) = civil_from_days(days);
    Some(format!("{fy:04}-{fm:02}-{fd:02}"))
}

/// How Vega-Lite's default description prints a `timeUnit`-grained x value
/// (`%Y W%U` for a week, `%b %Y` for a month): the text a support-table cell
/// carries beside the mark's own structured tooltip. Only the grains the
/// corpus exercises are modeled.
pub fn default_time_unit_text(date: &str, unit: TimeUnit) -> Result<String, String> {
    let (spec, bucket) = match unit {
        TimeUnit::YearWeek => ("%Y W%U", week_floor(date)),
        TimeUnit::YearMonth => ("%b %Y", Some(date.to_string())),
        TimeUnit::Year | TimeUnit::YearQuarter | TimeUnit::YearMonthDate => {
            return Err(format!("time unit {unit:?} has no modeled default description"));
        }
    };
    let bucket = bucket.ok_or_else(|| format!("unparsed date {date:?}"))?;
    let (y, m, d) = parse_iso_date(&bucket).ok_or_else(|| format!("unparsed date {date:?}"))?;
    let year = u32::try_from(y).map_err(|_| format!("year {y} out of range"))?;
    super::table_dates::strftime(
        super::table_dates::Moment::midnight(year, u32::from(m), u32::from(d)),
        spec,
    )
}

/// The instant Vega-Lite's `timeUnit` transform puts a row at: its date
/// floored to the enclosing bucket of the encoding's grain — a Sunday for
/// `yearweek` ([`week_floor`]), the 1st of the month, of the calendar quarter
/// or of the year for the coarser grains (`utcyearmonth` and friends floor
/// to the CALENDAR bucket whatever the fiscal start), the day itself for
/// `yearmonthdate`. A column grained by position already sits on bucket
/// starts; one grained by cadence ([`detect_cadence`]) does not, and its
/// marks, gridlines and scale domain all sit on the floored dates.
pub fn time_unit_floor(date: &str, tu: TimeUnit) -> Option<String> {
    if tu == TimeUnit::YearWeek {
        return week_floor(date);
    }
    let (y, m, d) = parse_iso_date(date)?;
    let (fy, fm, fd) = floor_to_bucket_start((y, m, d), tu, 1);
    Some(format!("{fy:04}-{fm:02}-{fd:02}"))
}

// ── A2: Vega's bare temporal axis ───────────────────────────────────────────
//
// When `detect_time_unit` finds no cadence (28-day steps, irregular series)
// production's `_temporal_layout` returns an empty `AxisLabelLayout`
// (`_label_overlap.py:215-222`): no `values`, no `labelExpr`, no `timeUnit`,
// and `labelOverlap` OMITTED. Vega-Lite then compiles its bare continuous
// default — probed on `area_review`'s own spec, the axis is
// `{scale, orient: bottom, labelSeparation: 16, labelOverlap: true,
// tickCount: {signal: ceil(concat_0_width/40)}}` — so the ticks are Vega's
// own: d3-time's `timeTicks(domain, ceil(plot_w / 40))` on the local time
// scale (no `timeUnit` is emitted, `_label_overlap.py:215-222`, so VL has no
// `utc*` unit to put the scale on UTC; renders run `TZ=UTC`), each labeled by
// d3's multi-scale UTC format, then the runtime parity pass. They are dates, not
// data points: a 2024-01-07 → 2025-06-29 domain of 20 rows draws 17 MONTHLY
// ticks.

/// One tick of Vega's bare temporal axis.
#[derive(Debug, Clone, serde::Serialize)]
pub struct BareTick {
    /// The tick's day number.
    pub day: i64,
    /// Position as a fraction of the scale domain ([`utc_fraction_at`]).
    pub frac: f64,
    /// The tick's label under d3's multi-scale UTC format.
    pub text: String,
}

const MS_PER_DAY: f64 = 864e5;
const MS_PER_YEAR: f64 = 31536e6;

/// d3-time's `tickIntervals` durations in ms (v3.1.0, read out of the vega
/// source vl-convert bundles): 1/5/15/30 s, 1/5/15/30 min, 1/3/6/12 h,
/// 1/2 day, 1 week, 1/3 month, 1 year. The sub-day rungs are listed because
/// `utcTickInterval`'s bisect compares the entries on BOTH sides of its
/// target — selecting one refuses, since the day is this module's finest
/// grain.
const D3_TICK_INTERVAL_MS: [f64; 18] = [
    1e3, 5e3, 15e3, 30e3, 6e4, 3e5, 9e5, 18e5, 36e5, 108e5, 216e5, 432e5, 864e5, 1728e5, 6048e5,
    2592e6, 7776e6, 31536e6,
];

/// A d3-time calendar interval with its `every(step)` filter, in the units
/// this module models: the one the count-driven ladder (`utcTickInterval`)
/// picks, or the one an authored `ticks.time_unit` + `step` names. Each
/// variant's filter is d3's own: a field-based interval keeps the instants
/// whose field value divides the step, so `Day(2)` restarts at the 1st of
/// each month and `Month(3)` is Jan/Apr/Jul/Oct — not a running stride
/// (d3-time `src/interval.js` `every`, `src/day.js` `utcDay` field
/// `getUTCDate() - 1`, `src/month.js` `utcMonth` field `getUTCMonth()`,
/// `src/year.js` `utcYear.every`). A week has no field, so `Week(step)`
/// counts Sundays from the epoch's first one (`interval.count(0, date) %
/// step`, 1969-12-28).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UtcTickInterval {
    Day(i64),
    /// d3's `unixDay`, the day rung of `utcTicks`: its `every(step)` keeps the
    /// days whose epoch day number divides the step (no calendar field).
    EpochDay(i64),
    Week(i64),
    Month(i64),
    Year(i64),
}

/// d3-time `utcTickInterval(start, stop, count)`: bisect the ladder on the
/// target span, then keep whichever neighbor is the smaller stretch of it.
fn utc_tick_interval(lo: f64, hi: f64, count: usize) -> Result<UtcTickInterval, String> {
    let rung = utc_tick_rung(lo, hi, count);
    if rung == D3_TICK_INTERVAL_MS.len() {
        let lo_years = lo * MS_PER_DAY / MS_PER_YEAR;
        let hi_years = hi * MS_PER_DAY / MS_PER_YEAR;
        // `utcYear.every(k)` floors k and returns a null interval — no
        // ticks at all — for anything below 1. That cannot arise here: this
        // rung is only reached when `target >= 31536e6`, i.e. the span is
        // already at least `count` years, so `tickStep`'s own
        // `(hi - lo) / count` is >= 1 and its 10^n·{1,2,5} result with it.
        return Ok(UtcTickInterval::Year(
            d3rs::ticks::d3_tick_step(lo_years, hi_years, count).floor() as i64,
        ));
    }
    match rung {
        12 => Ok(UtcTickInterval::Day(1)),
        13 => Ok(UtcTickInterval::Day(2)),
        14 => Ok(UtcTickInterval::Week(1)),
        15 => Ok(UtcTickInterval::Month(1)),
        16 => Ok(UtcTickInterval::Month(3)),
        17 => Ok(UtcTickInterval::Year(1)),
        _ => Err(
            "line: a bare temporal axis whose d3 tick interval is finer than a day is unmodeled"
                .into(),
        ),
    }
}

/// The rung of [`D3_TICK_INTERVAL_MS`] `utcTickInterval` picks (its length
/// when the span wants years): bisect on the target span, then keep whichever
/// neighbor is the smaller stretch of it.
fn utc_tick_rung(lo: f64, hi: f64, count: usize) -> usize {
    let target = (hi - lo) * MS_PER_DAY / count.max(1) as f64;
    let i = D3_TICK_INTERVAL_MS.partition_point(|&d| d <= target);
    if i == D3_TICK_INTERVAL_MS.len() {
        return i;
    }
    if i > 0 && target / D3_TICK_INTERVAL_MS[i - 1] < D3_TICK_INTERVAL_MS[i] / target { i - 1 } else { i }
}

/// [`utc_ticks_exact`] in fractional days, with the sub-day rungs the day
/// ladder refuses: a second, minute or hour interval keeps the instants of
/// `[lo, hi]` whose UTC field divides its step (d3-time `second`, `minute`
/// and `hour` `every`).
pub fn utc_instants(lo: f64, hi: f64, count: usize) -> Result<Vec<f64>, String> {
    const STEPS: [(f64, i64); 12] = [
        (1.0 / 86_400.0, 1),
        (1.0 / 86_400.0, 5),
        (1.0 / 86_400.0, 15),
        (1.0 / 86_400.0, 30),
        (1.0 / 1_440.0, 1),
        (1.0 / 1_440.0, 5),
        (1.0 / 1_440.0, 15),
        (1.0 / 1_440.0, 30),
        (1.0 / 24.0, 1),
        (1.0 / 24.0, 3),
        (1.0 / 24.0, 6),
        (1.0 / 24.0, 12),
    ];
    let rung = utc_tick_rung(lo, hi, count);
    let Some(&(unit, step)) = STEPS.get(rung) else {
        return Ok(utc_ticks_exact(lo, hi, count)?.into_iter().map(|d| d as f64).collect());
    };
    // The unit's field is its count within the next larger one: seconds in a
    // minute, minutes in an hour, hours in a day.
    let per_parent = if unit == 1.0 / 86_400.0 || unit == 1.0 / 1_440.0 { 60 } else { 24 };
    let first = (lo / unit).ceil() as i64;
    let last = (hi / unit).floor() as i64;
    Ok((first..=last).filter(|n| n.rem_euclid(per_parent) % step == 0).map(|n| n as f64 * unit).collect())
}

/// `interval.range(start, +stop + 1)` in day numbers — every aligned instant
/// in `[lo, hi]`, the end included (d3 asks for one millisecond past `stop`).
fn utc_tick_days(interval: UtcTickInterval, lo: i64, hi: i64) -> Vec<i64> {
    let mut out = Vec::new();
    match interval {
        UtcTickInterval::Day(step) => {
            for day in lo..=hi {
                let (_, _, d) = civil_from_days(day);
                if i64::from(d - 1) % step == 0 {
                    out.push(day);
                }
            }
        }
        UtcTickInterval::EpochDay(step) => out.extend((lo..=hi).filter(|day| day.rem_euclid(step) == 0)),
        UtcTickInterval::Week(step) => {
            for day in lo..=hi {
                let (y, m, d) = civil_from_days(day);
                // Day -4 is 1969-12-28, the Sunday d3's `count(0, date)`
                // floors the epoch to.
                if weekday(y, m, d) == 6 && (day + 4).div_euclid(7) % step == 0 {
                    out.push(day);
                }
            }
        }
        UtcTickInterval::Month(step) => {
            let (y0, m0, _) = civil_from_days(lo);
            let (mut y, mut m) = (y0, m0);
            loop {
                let day = days_from_civil(y, m, 1);
                if day > hi {
                    break;
                }
                if day >= lo && i64::from(m - 1) % step == 0 {
                    out.push(day);
                }
                m += 1;
                if m > 12 {
                    m = 1;
                    y += 1;
                }
            }
        }
        UtcTickInterval::Year(step) => {
            let (y0, _, _) = civil_from_days(lo);
            let (y1, _, _) = civil_from_days(hi);
            for y in y0..=y1 {
                let day = days_from_civil(y, 1, 1);
                if day >= lo && day <= hi && i64::from(y) % step == 0 {
                    out.push(day);
                }
            }
        }
    }
    out
}

const MONTH_FULL: [&str; 12] = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
];
const WEEKDAY_ABBR: [&str; 7] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/// d3's multi-scale UTC format — the tick's own finest non-zero unit picks
/// the specifier. From d3-scale's `calendar` (the bundled vega source:
/// `second<t ? ".%L" : minute<t ? ":%S" : hour<t ? "%I:%M" : day<t ? "%I %p"
/// : month<t ? (week<t ? "%a %d" : "%b %d") : year<t ? "%B" : "%Y"`), whose
/// day-grained tail is all this module can reach: a year start takes `%Y`,
/// any other month start `%B`, a Sunday `%b %d`, any other day `%a %d`.
fn d3_multi_format(y: i32, m: u8, d: u8) -> String {
    if d != 1 {
        if weekday(y, m, d) == 6 {
            format!("{} {d:02}", MONTH_ABBR[(m - 1) as usize])
        } else {
            format!("{} {d:02}", WEEKDAY_ABBR[weekday(y, m, d) as usize])
        }
    } else if m != 1 {
        MONTH_FULL[(m - 1) as usize].to_string()
    } else {
        format!("{y}")
    }
}

/// d3-time's ticks of the local time scale (`timeTicks`, which under `TZ=UTC`
/// differs from `utcTicks` only in the day rung's calendar-field filter) for
/// `count` ticks, in day numbers — every aligned calendar boundary in
/// `extent`, the end included. Refuses an interval finer than a
/// day, this module's finest grain.
pub fn utc_ticks(extent: (i64, i64), count: usize) -> Result<Vec<i64>, String> {
    let interval = utc_tick_interval(extent.0 as f64, extent.1 as f64, count)?;
    Ok(utc_tick_days(interval, extent.0, extent.1))
}

/// [`utc_ticks`] over a fractional-day extent: the interval comes from the exact
/// span, and only ticks inside `[lo, hi]` survive.
pub fn utc_ticks_exact(lo: f64, hi: f64, count: usize) -> Result<Vec<i64>, String> {
    let interval = utc_tick_interval(lo, hi, count)?;
    Ok(utc_tick_days(interval, lo.floor() as i64, hi.ceil() as i64)
        .into_iter()
        .filter(|&t| (t as f64) >= lo && (t as f64) <= hi)
        .collect())
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

/// The d3 interval production asks Vega for when `style.axis_x.ticks.time_unit`
/// is authored on a temporal x axis: `type_inference.py:1026-1052` maps the
/// unit through `_TEMPORAL_TICK_INTERVAL` (`:132-138`) to Vega-Lite's
/// `axis.tickCount: {interval}` and adds `step` only when `ticks.step` is
/// authored (`:1043-1047`) — so the table's own step (3 for `yearquarter`)
/// belongs to the unauthored default cadence ([`native_tick_interval`]),
/// never to this one. Vega turns the pair into `timeInterval(interval)` and
/// `.every(step)` when a step is set (vega-scale `tickCount`), d3's
/// `interval.every` flooring the step.
///
/// A unit outside the table is a `ChartDataError` in production
/// (`:1036-1041`); a step d3 cannot apply (`every` returns null below 1 and
/// floors a fraction) is refused rather than floored.
pub fn authored_tick_interval(
    time_unit: &str,
    step: Option<f64>,
) -> Result<UtcTickInterval, String> {
    let step = match step {
        None => 1,
        Some(s) if s.is_finite() && s >= 1.0 && s.fract() == 0.0 => s as i64,
        Some(s) => {
            return Err(format!(
                "axis_x.ticks.step {s} is not a whole number of at least 1 — \
                 d3's interval.every floors it and returns no interval below 1"
            ))
        }
    };
    match parse_authored_time_unit(time_unit) {
        Some(unit) => Ok(tick_interval_entry(unit).0(step)),
        None => Err(format!(
            "axis_x.ticks.time_unit {time_unit:?} has no step-anchored cadence — production \
             (type_inference.py:1036) raises for all but year, yearquarter, yearmonth, \
             yearweek and yearmonthdate"
        )),
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

/// Every tick of `interval` inside `extent`, in day numbers — the end
/// included, the same `interval.range(start, stop + 1)` d3 runs for any
/// scale's ticks.
pub fn interval_tick_days(extent: (i64, i64), interval: UtcTickInterval) -> Vec<i64> {
    utc_tick_days(interval, extent.0, extent.1)
}

/// Production's `opens_label_period` gate (`time_unit_detect.py:1870-1912`)
/// on a Vega TICK date: whether a tick of a `data_tu` axis opens a `label_tu`
/// period. Unlike [`is_opener`], which is the per-ROW predicate, this keeps
/// the gate's own clauses exactly, so a monthly axis carries no day clause
/// (every tick inside an opening month passes) and equal or unlisted grain
/// pairs have no gate at all.
fn opens_label_period(
    y: i32,
    m: u8,
    d: u8,
    data_tu: TimeUnit,
    label_tu: TimeUnit,
    fiscal_start: u8,
) -> bool {
    use TimeUnit::{Year, YearMonth, YearMonthDate, YearQuarter, YearWeek};
    let day_clause = || match data_tu {
        YearWeek => d <= 7,
        YearMonthDate => d == 1,
        _ => true,
    };
    match (label_tu, data_tu) {
        (YearQuarter, YearMonth | YearWeek | YearMonthDate) => {
            opens_fiscal_period(m, 3, fiscal_start) && day_clause()
        }
        (YearMonth, YearWeek) => d <= 7,
        (YearMonth, YearMonthDate) => d == 1,
        (YearWeek, YearMonthDate) => weekday(y, m, d) == 0,
        (Year, tu) if tu != Year => opens_fiscal_period(m, 12, fiscal_start) && day_clause(),
        _ => true,
    }
}

/// The labels production paints on Vega's interval ticks — authored
/// (`ticks.time_unit`) or the default cadence ([`native_tick_interval`]): the
/// `labelExpr` (`default_label_expr_for`, `time_unit_detect.py:2058-2060`)
/// is `(anchor || opens_label_period) ? text : ''` per Vega tick, so a tick
/// speaks because its own date opens a label period — never because a data
/// row sits on it. `TemporalLabel.domain_idx` indexes `tick_days` here.
///
/// The anchor takes the year context: the tick whose `data_tu`-grain date
/// is the first label-period opener of the enumerated span when the label
/// grain is coarser than the data's (`type_inference.py:694-707`), else
/// tick 0 (`datum.index === 0`).
///
/// Neither cadence injects `values`, so `ticks_are_buckets` is false and a
/// weekly axis in the week vocabulary reads each Sunday tick as its Monday
/// bucket, `utcOffset('day', toDate(datum.value), 1)`
/// (`time_unit_detect.py:2013-2017`): the text, the gate and the anchor all
/// see the Monday.
pub fn interval_tick_labels(
    tick_days: &[i64],
    domain: &[String],
    data_tu: TimeUnit,
    format_tu: TimeUnit,
    visibility_tu: TimeUnit,
    fiscal_start: u8,
    authored: Option<AuthoredTimeFormat>,
) -> Result<Vec<TemporalLabel>, String> {
    let monday_shift = i64::from(data_tu == TimeUnit::YearWeek && format_tu == TimeUnit::YearWeek);
    // `anchor_formats` (`time_unit_detect.py:2025-2031`): the data grain's own
    // `utcFormat` pattern, so a week compares as `%Y-%U`.
    let anchor_key = |(y, m, d): (i32, u8, u8)| match data_tu {
        TimeUnit::Year => (y, 0, 0),
        TimeUnit::YearQuarter | TimeUnit::YearMonth => (y, m, 0),
        TimeUnit::YearWeek => (y, sunday_week_of_year(y, m, d), 0),
        TimeUnit::YearMonthDate => (y, m, d),
    };
    let anchor = (visibility_tu != data_tu)
        .then(|| first_span_opener(domain, data_tu, visibility_tu, fiscal_start))
        .flatten()
        .map(anchor_key);
    let mut labels = Vec::new();
    for (idx, &day) in tick_days.iter().enumerate() {
        let (y, m, d) = civil_from_days(day + monday_shift);
        let is_anchor = match anchor {
            Some(key) => anchor_key((y, m, d)) == key,
            None => idx == 0,
        };
        if is_anchor || opens_label_period(y, m, d, data_tu, visibility_tu, fiscal_start) {
            let (row1, row2) = label_rows(y, m, d, format_tu, is_anchor, fiscal_start, authored)?;
            labels.push(TemporalLabel { domain_idx: idx, row1, row2 });
        }
    }
    Ok(labels)
}

/// strftime `%U`: the Sunday-based week of the year, 0 before the year's
/// first Sunday (d3-time-format `formatWeekNumberSunday`,
/// `timeSunday.count(timeYear(d) - 1, d)`; Vega's `utcFormat` is its UTC twin).
fn sunday_week_of_year(y: i32, m: u8, d: u8) -> u8 {
    let day = days_from_civil(y, m, d);
    let yday = day - days_from_civil(y, 1, 1);
    // Day 0 is a Thursday, so `(day + 4) mod 7` is the days since Sunday.
    let since_sunday = (day + 4).rem_euclid(7);
    ((yday + 7 - since_sunday) / 7) as u8
}

/// `label_opener_values(enumerated_axis_values(domain))[0]`
/// (`time_unit_detect.py:1132`, `:1633`): the first calendar bucket of the
/// domain's whole span, stepped at the data grain, that opens a
/// `visibility_tu` period — or the first bucket when none does.
fn first_span_opener(
    domain: &[String],
    data_tu: TimeUnit,
    visibility_tu: TimeUnit,
    fiscal_start: u8,
) -> Option<(i32, u8, u8)> {
    let dates = parse_domain_dates(domain);
    let (lo, hi) = (dates.iter().min()?, dates.iter().max()?);
    let mut bucket = floor_to_bucket_start(*lo, data_tu, fiscal_start);
    let first = bucket;
    while bucket <= *hi {
        let (y, m, d) = bucket;
        if is_opener(y, m, d, data_tu, visibility_tu, fiscal_start) {
            return Some(bucket);
        }
        bucket = match data_tu {
            TimeUnit::YearMonthDate => civil_from_days(days_from_civil(y, m, d) + 1),
            TimeUnit::YearWeek => civil_from_days(days_from_civil(y, m, d) + 7),
            TimeUnit::YearMonth => step_months(y, m, 1),
            TimeUnit::YearQuarter => step_months(y, m, 3),
            TimeUnit::Year => (y + 1, m, 1),
        };
    }
    Some(first)
}

fn step_months(y: i32, m: u8, by: u8) -> (i32, u8, u8) {
    let total = i32::from(m) - 1 + i32::from(by);
    (y + total / 12, (total % 12) as u8 + 1, 1)
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
}

impl AuthoredTickCadence {
    /// The d3 interval the cadence names over a scale domain: an authored
    /// interval is its own, a count is the rung `utcTickInterval` picks for
    /// that many ticks across `extent`.
    pub fn interval(self, extent: (f64, f64)) -> Result<UtcTickInterval, String> {
        match self {
            Self::Interval(interval) => Ok(interval),
            Self::Count(count) => utc_tick_interval(extent.0, extent.1, count),
        }
    }

    /// [`Self::interval`] on a bucketed axis, whose scale is UTC and whose
    /// axis Vega-Lite gives the grain's `tickMinStep`: a number `tickCount` is
    /// capped at `floor(span / minStep) + 1` (vega-scale `tickCount`), and the
    /// ladder's 2-day rung strides epoch days ([`UtcTickInterval::EpochDay`]).
    pub fn bucketed_interval(self, grain: TimeUnit, extent: (f64, f64)) -> Result<UtcTickInterval, String> {
        let Self::Count(count) = self else { return self.interval(extent) };
        let capped = count.min(min_step_ticks(extent.1 - extent.0, tick_min_step_days(grain)));
        Ok(match utc_tick_interval(extent.0, extent.1, capped)? {
            UtcTickInterval::Day(2) => UtcTickInterval::EpochDay(2),
            interval => interval,
        })
    }
}

/// vega-scale `tickCount`'s cap, `floor(span / minStep || 1) + 1`, with both
/// lengths in whole milliseconds as JavaScript holds them.
fn min_step_ticks(span_days: f64, step_days: f64) -> usize {
    let ratio = (span_days * MS_PER_DAY).round() / (step_days * MS_PER_DAY).round();
    (if ratio == 0.0 || ratio.is_nan() { 1.0 } else { ratio }).floor() as usize + 1
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

/// An authored `ticks.count` as the count d3 asks for. The model types it
/// `int`; d3 would also take a fraction or zero, which no board can author,
/// so one that arrives is refused rather than rounded.
pub fn authored_tick_count(count: f64) -> Result<usize, String> {
    if count.is_finite() && count >= 1.0 && count.fract() == 0.0 {
        Ok(count as usize)
    } else {
        Err(format!("axis_x.ticks.count {count} is not a whole number of at least 1"))
    }
}

/// Vega's own ticks for a temporal axis carrying no `values`, multi-scale
/// formatted: d3 `ticks(domain, count)` with Vega-Lite's continuous
/// `tickCount` default `ceil(plot_w / 40)` — or, when the axis authors a
/// cadence, that one instead ([`AuthoredTickCadence`]): an authored
/// `ticks.count` replaces the default count, an authored `ticks.time_unit`
/// ([`authored_tick_interval`]) names the interval outright; an authored time
/// `format` replaces the multi-scale text on every tick. `extent` is
/// [`utc_domain_extent`]'s.
pub fn bare_temporal_ticks(
    domain: (f64, f64),
    plot_w: f64,
    authored: Option<AuthoredTickCadence>,
    format: Option<AuthoredTimeFormat>,
) -> Result<Vec<BareTick>, String> {
    let (lo, hi) = domain;
    // A one-instant domain ticks that instant, the finest interval d3 tries.
    if lo == hi {
        let day = lo as i64;
        let (y, m, d) = civil_from_days(day);
        let text = match format {
            Some(f) => f.text(y, m, d)?,
            None => d3_multi_format(y, m, d),
        };
        return Ok(vec![BareTick { day, frac: 0.5, text }]);
    }
    let (first, last) = (lo.floor() as i64, hi.ceil() as i64);
    let interval = match authored {
        Some(cadence) => cadence.interval((lo, hi))?,
        None => {
            let count = crate::families::cartesian::vl_default_tick_count(plot_w).max(1);
            utc_tick_interval(lo, hi, count)?
        }
    };
    utc_tick_days(interval, first, last)
        .into_iter()
        .filter(|&day| lo <= day as f64 && day as f64 <= hi)
        .map(|day| {
            let (y, m, d) = civil_from_days(day);
            let text = match format {
                Some(f) => f.text(y, m, d)?,
                None => d3_multi_format(y, m, d),
            };
            Ok(BareTick { day, frac: (day as f64 - lo) / (hi - lo), text })
        })
        .collect()
}

/// Rendered half-width (px) of a temporal label's wider row: the max of
/// `row1`/`row2`'s measured widths, halved (a two-row label is centered on its
/// band, both rows share the same center, so only the wider row's extent past
/// that center matters).
pub fn temporal_label_half_width(
    label: &TemporalLabel,
    measure: &mut dyn FnMut(&str) -> Result<f64, String>,
) -> Result<f64, String> {
    let w1 = measure(&label.row1)?;
    let w2 = if label.row2.is_empty() { 0.0 } else { measure(&label.row2)? };
    Ok(w1.max(w2) / 2.0)
}

// ── Unit tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    /// One reading of a date cell for every family: clock as the day's fraction
    /// (V8's millisecond precision), an offset applied, any other shape unread.
    #[test]
    fn a_date_text_is_its_day_with_the_clock_as_the_fraction() {
        let jan_first = 19723.0;
        assert_eq!(date_text_day("2024-01-01"), Some(jan_first));
        assert_eq!(date_text_day("2024-01"), Some(jan_first));
        assert_eq!(date_text_day("2024-01-01T12:00:00"), Some(jan_first + 0.5));
        assert_eq!(date_text_day("2024-01-01 06:00"), Some(jan_first + 0.25));
        assert_eq!(date_text_day("2024-01-01T12:00:00Z"), Some(jan_first + 0.5));
        assert_eq!(date_text_day("2024-01-01T12:00:00+06:00"), Some(jan_first + 0.25));
        assert_eq!(date_text_day("2024-01-01T12:00:00-06:00"), Some(jan_first + 0.75));
        assert_eq!(date_text_day("2024-01-01T12:00:00.5"), Some(jan_first + 0.5 + 0.5 / 86_400.0));
        assert_eq!(date_text_day("2024-01-01T12:00:00Z05:00"), None);
        assert_eq!(date_text_day("2024-01-01T12:00:00."), None);
        assert_eq!(date_text_day("2024-01-01T12:00:00.123456"), Some(jan_first + 0.5 + 0.123 / 86_400.0));
        for unread in ["soon", "2024-01-01T", "2024/01/05", "2024-01-01T24:00:00", "2024-01-01T1:00", "2024-01-01T12:00:00+6", "2024-01-01T12:00:00.x", "2024-01-01T12:00:00Zulu"] {
            assert_eq!(date_text_day(unread), None, "{unread}");
        }
    }

    use super::*;

    // ── `_BUCKET_FAMILIES` / `parse_bucket_string` ─────────────────────────

    #[test]
    fn parse_bucket_string_resolves_every_family_to_its_anchor_date() {
        // iso_week: 2024-01-01 is a Monday, so week 1's anchor is itself.
        assert_eq!(parse_bucket_string("2024-W01"), Some((2024, 1, 1)));
        // iso_quarter
        assert_eq!(parse_bucket_string("2024-Q1"), Some((2024, 1, 1)));
        assert_eq!(parse_bucket_string("2024-Q4"), Some((2024, 10, 1)));
        // yearmonth_str
        assert_eq!(parse_bucket_string("2024-01"), Some((2024, 1, 1)));
        assert_eq!(parse_bucket_string("2024-12"), Some((2024, 12, 1)));
        assert_eq!(parse_bucket_string("2024-13"), None);
        // mon_yyyy (case-insensitive)
        assert_eq!(parse_bucket_string("Jan 2024"), Some((2024, 1, 1)));
        assert_eq!(parse_bucket_string("jan 2024"), Some((2024, 1, 1)));
        // mm_yyyy
        assert_eq!(parse_bucket_string("01/2024"), Some((2024, 1, 1)));
        assert_eq!(parse_bucket_string("13/2022"), None);
        // q_yyyy (case-insensitive, optional whitespace)
        assert_eq!(parse_bucket_string("Q1 2024"), Some((2024, 1, 1)));
        assert_eq!(parse_bucket_string("q1 2024"), Some((2024, 1, 1)));
        assert_eq!(parse_bucket_string("Q12024"), Some((2024, 1, 1)));
        // yyyy_q (case-insensitive)
        assert_eq!(parse_bucket_string("2024Q1"), Some((2024, 1, 1)));
        assert_eq!(parse_bucket_string("2024q1"), Some((2024, 1, 1)));
        // fy (case-insensitive)
        assert_eq!(parse_bucket_string("FY2024"), Some((2024, 1, 1)));
        assert_eq!(parse_bucket_string("fy2024"), Some((2024, 1, 1)));
        // mon_dd_yyyy — a comma is optional, `\s+` before the year is not,
        // and the day is validated against the month's real length.
        assert_eq!(parse_bucket_string("Jan 15, 2024"), Some((2024, 1, 15)));
        assert_eq!(parse_bucket_string("Jan 15 2024"), Some((2024, 1, 15)));
        assert_eq!(parse_bucket_string("Jan 15,2024"), None);
        assert_eq!(parse_bucket_string("Feb 30, 2024"), None);
        assert_eq!(parse_bucket_string("Feb 29, 2024"), Some((2024, 2, 29)));
        assert_eq!(parse_bucket_string("Feb 29, 2023"), None);
        // week_spelled — mandatory `\s+` before the year; `Week` prefix
        // optional, case-insensitive; the anchor is the ISO week's Monday.
        assert_eq!(parse_bucket_string("W1 2024"), Some((2024, 1, 1)));
        assert_eq!(parse_bucket_string("Week 1 2024"), Some((2024, 1, 1)));
        assert_eq!(parse_bucket_string("week 1 2024"), Some((2024, 1, 1)));
    }

    #[test]
    fn parse_bucket_string_is_case_sensitive_exactly_where_production_is() {
        // `_ISO_QUARTER_RE`/`_ISO_WEEK_RE` require an uppercase Q/W — the
        // lowercase forms fall through to no family, so they stay raw
        // strings and resolve ordinal (sorted), unlike the uppercase forms.
        assert_eq!(parse_bucket_string("2024-q1"), None);
        assert_eq!(parse_bucket_string("2024-w01"), None);
    }

    #[test]
    fn parse_bucket_string_enforces_the_mandatory_whitespace_before_a_week_spelled_year() {
        // `_WEEK_SPELLED_RE` needs `\s+` before the year — unlike the wider
        // `DATE_LIKE_PATTERNS` look-alike, which accepts `\s*` and matches
        // "W12024" with no separating space at all.
        assert_eq!(parse_bucket_string("W12024"), None);
    }

    #[test]
    fn parse_bucket_string_rejects_an_out_of_range_iso_week() {
        // 2023 has 52 ISO weeks (`p(2023) != 4`, `p(2022) != 3`).
        assert_eq!(parse_bucket_string("2023-W53"), None);
        assert_eq!(parse_bucket_string("Week 53 2023"), None);
    }

    #[test]
    fn weekday_stays_in_range_on_a_pre_1970_sunday() {
        // 1900-03-04 is a Sunday (verified against Python's `date.
        // weekday()`). Rust's `%` keeps the dividend's sign, so the
        // un-rem_euclid'd formula computed `h == -1` here (`j == 19`
        // drives `- 2*j` negative enough that the whole sum is negative),
        // and `(h + 5) % 7` on a still-negative `h` cast to `255u8`
        // instead of `6` (Sunday, 0=Mon..6=Sun).
        assert_eq!(weekday(1900, 3, 4), 6);
    }

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

    // ── tooltip_header_date ──────────────────────────────────────────────

    fn in_language<T>(code: &str, f: impl FnOnce() -> T) -> T {
        display_text::language::with_language(display_text::language::Language::new(code).unwrap(), f)
    }

    /// The quarter words follow the board language: the house tooltip header
    /// puts the quarter before the year where Vega-Lite's default format puts
    /// it after, and a language may spell both its own way.
    #[test]
    fn quarter_labels_follow_the_board_language() {
        let header = |code: &str| {
            in_language(code, || tooltip_header_date(2024, 2, 3, Some(TimeUnit::YearQuarter))).unwrap()
        };
        assert_eq!(header("en"), "Q1 2024");
        assert_eq!(header("fr"), "T1 2024");
        assert_eq!(header("ja"), "2024年第1四半期");
        let native = |code: &str| in_language(code, || vl_default_format(2024, 11, 3, TimeUnit::YearQuarter)).unwrap();
        assert_eq!(native("en"), "2024 Q4");
        assert_eq!(native("tr"), "2024 Ç4");
        let axis = in_language("tr", || format_label(2024, 5, 1, TimeUnit::YearQuarter, true, 1)).unwrap();
        assert_eq!(axis, ("Ç2".to_string(), "2024".to_string()));
    }

    /// Ports `tooltip_header_date_expr` (time_unit_detect.py:2159-2183):
    /// full bucket text, never the coarsened bare form an axis label uses.
    #[test]
    fn tooltip_header_date_matches_production_vocabulary() {
        assert_eq!(tooltip_header_date(2024, 2, 3, Some(TimeUnit::Year)).unwrap(), "2024");
        assert_eq!(
            tooltip_header_date(2024, 2, 3, Some(TimeUnit::YearQuarter)).unwrap(),
            "Q1 2024"
        );
        assert_eq!(
            tooltip_header_date(2024, 11, 3, Some(TimeUnit::YearQuarter)).unwrap(),
            "Q4 2024"
        );
        assert_eq!(
            tooltip_header_date(2024, 2, 3, Some(TimeUnit::YearMonth)).unwrap(),
            "Feb 2024"
        );
        assert_eq!(
            tooltip_header_date(2024, 2, 3, Some(TimeUnit::YearWeek)).unwrap(),
            "3\u{2013}9 Feb 2024"
        );
        assert_eq!(
            tooltip_header_date(2024, 1, 29, Some(TimeUnit::YearWeek)).unwrap(),
            "29 Jan \u{2013} 4 Feb 2024"
        );
        assert_eq!(
            tooltip_header_date(2025, 12, 27, Some(TimeUnit::YearWeek)).unwrap(),
            "27 Dec 2025 \u{2013} 2 Jan 2026"
        );
        // YearMonthDate and the continuous/sub-daily `None` case share one
        // fallback format (production's own `else` branch).
        assert_eq!(
            tooltip_header_date(2024, 2, 3, Some(TimeUnit::YearMonthDate)).unwrap(),
            "3 Feb 2024"
        );
        assert_eq!(tooltip_header_date(2024, 2, 3, None).unwrap(), "3 Feb 2024");
    }

    // ── detect_time_unit ──────────────────────────────────────────────────

    #[test]
    fn detect_year() {
        let dates = ["2022-01-01", "2023-01-01", "2024-01-01"];
        assert_eq!(
            detect_time_unit(&dates),
            Some(TimeUnit::Year)
        );
    }

    #[test]
    fn detect_yearquarter() {
        let dates = ["2024-01-01", "2024-04-01", "2024-07-01", "2024-10-01"];
        assert_eq!(detect_time_unit(&dates), Some(TimeUnit::YearQuarter));
    }

    // ── x_values_are_temporal_candidates ─────────────────────────────────

    #[test]
    fn bare_4_digit_years_are_not_temporal_candidates() {
        // A bare year PARSES via parse_iso_date but fails every structural
        // pattern `infer_vega_type_from_data` checks, so this raw-shape gate
        // rejects it. Years in [1700, 2100] never reach the gate raw —
        // `is_year_shaped` rewrites them to `YYYY-01-01` first.
        let years = ["1800", "1900", "1950", "2000", "2025"];
        assert!(!x_values_are_temporal_candidates(&years));
    }

    #[test]
    fn iso_dates_are_temporal_candidates() {
        let dates = ["2024-01-01", "2024-01-08", "2024-01-15"];
        assert!(x_values_are_temporal_candidates(&dates));
    }

    #[test]
    fn iso_yearmonth_is_a_temporal_candidate() {
        assert!(x_values_are_temporal_candidates(&["2024-01", "2024-02"]));
    }

    #[test]
    fn us_slash_dates_are_temporal_candidates() {
        assert!(x_values_are_temporal_candidates(&["01/15/2024", "02/20/2024"]));
    }

    #[test]
    fn labeled_bucket_shapes_are_temporal_candidates() {
        assert!(x_values_are_temporal_candidates(&["2024-Q1", "2024-Q2"]));
        assert!(x_values_are_temporal_candidates(&["Q1 2024", "Q2 2024"]));
        assert!(x_values_are_temporal_candidates(&["2024Q1", "2024Q2"]));
        assert!(x_values_are_temporal_candidates(&["FY2024", "FY2025"]));
        assert!(x_values_are_temporal_candidates(&["H1 2024", "H2 2024"]));
        assert!(x_values_are_temporal_candidates(&["2024-H1", "2024-H2"]));
        assert!(x_values_are_temporal_candidates(&["2024-W01", "2024-W02"]));
        assert!(x_values_are_temporal_candidates(&["Week 1 2024", "Week 2 2024"]));
        assert!(x_values_are_temporal_candidates(&["W1 2024", "W2 2024"]));
        assert!(x_values_are_temporal_candidates(&["01/2024", "02/2024"]));
        assert!(x_values_are_temporal_candidates(&["Jan 2024", "Feb 2024"]));
        assert!(x_values_are_temporal_candidates(&["Jan 15, 2024", "Feb 20, 2024"]));
        assert!(x_values_are_temporal_candidates(&["Jan 15,\u{a0}2024", "Feb 20,\u{a0}2024"]));
        assert!(x_values_are_temporal_candidates(&["Jan 15 2024", "Feb 20 2024"]));
    }

    /// `is_date_like_string` is `DATE_LIKE_PATTERNS`: the bucket shapes plus the ISO date,
    /// US date and naive timestamp, but not a slash-ordered year-first date.
    #[test]
    fn date_like_strings_are_the_date_like_patterns_and_no_wider() {
        for yes in ["2024-Q1", "Jan 2024", "2024-01-15", "01/15/2024", "2024-01-15 13:30", "2024-01-15T13:30:00.5"] {
            assert!(is_date_like_string(yes), "{yes}");
        }
        for no in ["North", "2024/01/15", "2024-01-15T13", "15", ""] {
            assert!(!is_date_like_string(no), "{no}");
        }
    }

    #[test]
    fn quarter_shapes_are_case_insensitive_like_productions_date_like_regexes() {
        // Production compiles DATE_LIKE_PATTERNS with re.IGNORECASE; the
        // port hardcoded uppercase `Q` in the `\d{4}-Q[1-4]` and
        // `\d{4}Q[1-4]` arms.
        assert!(x_values_are_temporal_candidates(&["2024-q1", "2024-q2"]));
        assert!(x_values_are_temporal_candidates(&["2024q1", "2024q2"]));
    }

    #[test]
    fn week_number_shapes_backtrack_between_one_and_two_digits() {
        // `^W(?:eek\s*)?\d{1,2}\s*\d{4}$` backtracks in Python: a greedy
        // 2-digit week number that leaves a non-4-digit tail retries with
        // 1 digit. `"W12024"` only works under 1 digit ("1" + "2024");
        // the port's `take_while` used to consume all 5 digits greedily
        // with no retry.
        assert!(x_values_are_temporal_candidates(&["W12024", "W22024"]));
    }

    #[test]
    fn month_day_year_requires_whitespace_before_the_year_even_after_a_comma() {
        // `\s+` before the year in `Mon D[D],? YYYY` is mandatory in the
        // regex; `trim_start()` used to accept zero whitespace.
        assert!(!x_values_are_temporal_candidates(&["Jan 15,2024", "Feb 20,2024"]));
    }

    #[test]
    fn plain_category_strings_are_not_temporal_candidates() {
        assert!(!x_values_are_temporal_candidates(&["North", "South", "East"]));
        assert!(!x_values_are_temporal_candidates(&["claude-opus-4-7", "claude-haiku"]));
    }

    #[test]
    fn an_empty_or_mixed_sample_is_not_a_temporal_candidate() {
        assert!(!x_values_are_temporal_candidates(&[]));
        // Mixed shapes: production requires ALL sampled values to pass.
        assert!(!x_values_are_temporal_candidates(&["2024-01-01", "1800"]));
    }

    #[test]
    fn only_the_first_ten_values_are_sampled() {
        // A bare-year value past the 10th sampled row does not poison the
        // verdict — matches production's `data[:min(10, len(data))]`.
        let mut values: Vec<&str> = vec![
            "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
            "2024-01-06", "2024-01-07", "2024-01-08", "2024-01-09", "2024-01-10",
        ];
        values.push("1800");
        assert!(x_values_are_temporal_candidates(&values));
    }

    #[test]
    fn non_ascii_x_values_do_not_panic_the_shape_gate() {
        // Regression: `is_date_like_shape` used to byte-slice a `&str` at
        // fixed offsets (`s[0..2]`, `rest[..3]`) to compare against `"FY"`
        // / `"eek"`. On a non-ASCII value those offsets can land mid
        // char, which panics `&str` indexing instead of returning a
        // verdict. Six-byte CJK strings hit the `b.len() == 6` FY arm
        // exactly, matching the reviewer's repro.
        assert!(!x_values_are_temporal_candidates(&["日本語です", "テスト"]));
        assert!(!x_values_are_temporal_candidates(&["日本", "テスト"]));
        // "W" + "1日1800": byte offset 3 of the post-"W" remainder lands
        // mid-character (日 spans bytes 1..4 of the remainder), hitting
        // the other unsafe slice (`rest[..3]`) in the `W(eek)?` arm.
        assert!(!x_values_are_temporal_candidates(&["W1日1800", "W1日1900"]));
    }

    #[test]
    fn detect_yearmonth() {
        // 12 monthly data points
        let dates: Vec<String> = (1u8..=12).map(|m| format!("2024-{m:02}-01")).collect();
        let refs: Vec<&str> = dates.iter().map(String::as_str).collect();
        assert_eq!(detect_time_unit(&refs), Some(TimeUnit::YearMonth));
    }

    #[test]
    fn detect_yearweek() {
        // 8 Mondays spaced 7 days apart
        let dates = [
            "2024-01-01", "2024-01-08", "2024-01-15", "2024-01-22",
            "2024-01-29", "2024-02-05", "2024-02-12", "2024-02-19",
        ];
        assert_eq!(detect_time_unit(&dates), Some(TimeUnit::YearWeek));
    }

    #[test]
    fn weekly_rung_reads_exact_gaps_and_the_true_median() {
        // Gaps [14, 14]: the median is 14, so the column is weekly.
        assert_eq!(
            detect_time_unit(&["2024-02-19", "2024-03-04", "2024-03-18"]),
            Some(TimeUnit::YearWeek),
        );
        // Gaps [7, 21]: an even count averages the two middle gaps to 14,
        // where picking the upper one would read 21.
        assert_eq!(
            detect_time_unit(&["2024-01-01", "2024-01-08", "2024-01-29"]),
            Some(TimeUnit::YearWeek),
        );
        // Gaps [28, 28]: the same weekday at a four-week cadence is not weekly.
        assert_eq!(detect_time_unit(&["2024-01-01", "2024-01-29", "2024-02-26"]), None);
    }

    #[test]
    fn detect_yearmonthdate() {
        // Daily data — first few days of January 2024
        let dates = [
            "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04",
            "2024-01-05", "2024-01-06", "2024-01-07",
        ];
        assert_eq!(detect_time_unit(&dates), Some(TimeUnit::YearMonthDate));
    }

    /// Every row of this table is `time_unit_detect.detect_time_unit`'s own
    /// answer for the column, read out of production.
    #[test]
    fn cadence_rung_matches_productions_detection() {
        use TimeUnit::{Year, YearMonth, YearMonthDate, YearQuarter};
        let cases: [(&str, &[&str], Option<TimeUnit>); 14] = [
            (
                "gaps 30, 30, 31 … from the 15th and 14th: no row on a month start",
                &[
                    "2024-01-15", "2024-02-14", "2024-03-15", "2024-04-14", "2024-05-15",
                    "2024-06-14", "2024-07-15", "2024-08-14",
                ],
                Some(YearMonth),
            ),
            (
                "gaps 30, 31, 33, 29 … wobbling around a month, still within 0.15 of a period",
                &[
                    "2024-01-03", "2024-02-02", "2024-03-04", "2024-04-06", "2024-05-05",
                    "2024-06-08", "2024-07-07", "2024-08-09",
                ],
                Some(YearMonth),
            ),
            (
                "month ends",
                &["2024-01-31", "2024-02-29", "2024-03-31", "2024-04-30", "2024-05-31"],
                Some(YearMonth),
            ),
            (
                "quarter ends",
                &["2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31"],
                Some(YearQuarter),
            ),
            (
                "quarterly on the 5th",
                &["2024-01-05", "2024-04-05", "2024-07-05", "2024-10-05"],
                Some(YearQuarter),
            ),
            (
                "one day a year",
                &["2019-06-15", "2020-06-15", "2021-06-15"],
                Some(Year),
            ),
            (
                "the median gap, not the worst one: an outlier month does not disqualify",
                &["2024-01-15", "2024-02-14", "2024-03-15", "2024-06-14", "2024-07-15"],
                Some(YearMonth),
            ),
            (
                "three values are two gaps, the least that is a rhythm",
                &["2024-01-15", "2024-02-14", "2024-03-15"],
                Some(YearMonth),
            ),
            (
                "one gap is a measurement, not a rhythm",
                &["2024-01-15", "2024-02-14"],
                Some(YearMonthDate),
            ),
            (
                "two values share a month, so the grain is declined for a finer one",
                &["2024-01-02", "2024-01-31", "2024-03-01", "2024-03-31", "2024-04-29"],
                Some(YearMonthDate),
            ),
            (
                "the last value shares a quarter with the one before it under the calendar \
                 anchor, so quarters are declined and months accepted",
                &["2024-01-05", "2024-04-05", "2024-07-05", "2024-10-05", "2024-12-30"],
                Some(YearMonth),
            ),
            (
                "25-day gaps sit 0.18 of a period off a month",
                &[
                    "2024-01-02", "2024-01-27", "2024-02-21", "2024-03-17", "2024-04-11",
                    "2024-05-06",
                ],
                Some(YearMonthDate),
            ),
            (
                "45-day gaps sit half a period off a month",
                &["2024-01-02", "2024-02-16", "2024-04-01", "2024-05-16", "2024-06-30"],
                Some(YearMonthDate),
            ),
            (
                "a two-week rhythm is no calendar period",
                &["2024-01-02", "2024-01-16", "2024-01-31", "2024-02-14"],
                Some(YearMonthDate),
            ),
        ];
        for (name, dates, expected) in cases {
            assert_eq!(detect_time_unit(dates), expected, "{name}: {dates:?}");
        }
    }

    #[test]
    fn same_weekday_columns_never_reach_the_cadence_rung() {
        // Gaps of 28 days from one Sunday look monthly, but the weekly rung
        // owns every same-weekday column and declines it.
        let first = days_from_civil(2024, 1, 7);
        let dates: Vec<String> = (0..8)
            .map(|i| {
                let (y, m, d) = civil_from_days(first + i * 28);
                format!("{y:04}-{m:02}-{d:02}")
            })
            .collect();
        let refs: Vec<&str> = dates.iter().map(String::as_str).collect();
        assert_eq!(detect_time_unit(&refs), None);
    }

    // ── parse_iso_date ─────────────────────────────────────────────────────

    #[test]
    fn parse_full_date() {
        assert_eq!(parse_iso_date("2024-03-15"), Some((2024, 3, 15)));
    }

    #[test]
    fn parse_yearmonth() {
        assert_eq!(parse_iso_date("2024-03"), Some((2024, 3, 1)));
    }

    #[test]
    fn parse_year_only() {
        assert_eq!(parse_iso_date("2024"), Some((2024, 1, 1)));
    }

    #[test]
    fn parse_invalid() {
        assert_eq!(parse_iso_date("not-a-date"), None);
        assert_eq!(parse_iso_date("Jan"), None);
        assert_eq!(parse_iso_date("2025-01-06X00:00:00"), None);
        assert_eq!(parse_iso_date("2025-1-06 00:00:00"), None);
    }

    #[test]
    fn parse_datetime_takes_date_part() {
        // Production's _ISO_DATETIME_RE accepts "YYYY-MM-DD" followed by "T"
        // or " " (time_unit_detect.py); grain detection uses the date part.
        assert_eq!(parse_iso_date("2025-01-06 00:00:00"), Some((2025, 1, 6)));
        assert_eq!(parse_iso_date("2025-01-06T12:30:00"), Some((2025, 1, 6)));
    }

    // ── label formatting ───────────────────────────────────────────────────

    /// yearmonth cadence: first label gets year row, others only on January.
    #[test]
    fn yearmonth_labels_anchor_and_jan() {
        // Jan 2024 (first) → "Jan" / "2024"
        let (r1, r2) = format_label(2024, 1, 1, TimeUnit::YearMonth, true, 1).unwrap();
        assert_eq!(r1, "Jan");
        assert_eq!(r2, "2024");

        // Feb 2024 (inner) → "Feb" / ""
        let (r1, r2) = format_label(2024, 2, 1, TimeUnit::YearMonth, false, 1).unwrap();
        assert_eq!(r1, "Feb");
        assert_eq!(r2, "");

        // Jan 2025 (year boundary, not first) → "Jan" / "2025"
        let (r1, r2) = format_label(2025, 1, 1, TimeUnit::YearMonth, false, 1).unwrap();
        assert_eq!(r1, "Jan");
        assert_eq!(r2, "2025");
    }

    /// yearquarter cadence: Q labels with year at anchor / Q1.
    #[test]
    fn yearquarter_labels() {
        let (r1, r2) = format_label(2024, 1, 1, TimeUnit::YearQuarter, true, 1).unwrap();
        assert_eq!(r1, "Q1");
        assert_eq!(r2, "2024");

        let (r1, r2) = format_label(2024, 4, 1, TimeUnit::YearQuarter, false, 1).unwrap();
        assert_eq!(r1, "Q2");
        assert_eq!(r2, "");

        let (r1, r2) = format_label(2025, 1, 1, TimeUnit::YearQuarter, false, 1).unwrap();
        assert_eq!(r1, "Q1");
        assert_eq!(r2, "2025");
    }

    /// year cadence: plain year, no row2.
    #[test]
    fn year_labels() {
        let (r1, r2) = format_label(2024, 1, 1, TimeUnit::Year, true, 1).unwrap();
        assert_eq!(r1, "2024");
        assert_eq!(r2, "");
    }

    /// fiscal_year_start_month shifts the quarter boundary: with a fiscal
    /// year starting in March (production's `xaxis-cadence-ladder` board),
    /// March opens Q1, so June/September/December are Q2/Q3/Q4 and January/
    /// February fall in the *previous* fiscal year's Q4. Deleting the
    /// `fiscal_start` shift (falling back to the calendar convention) would
    /// paint every one of these as the plain-calendar quarter instead —
    /// this test goes red under that regression.
    #[test]
    fn yearquarter_labels_honor_fiscal_year_start_month() {
        let fiscal_start = 3; // March

        // March is the fiscal year's first month → Q1, with year row (fiscal
        // year opener, mirrors the `is_first`/year-start OR in production).
        let (r1, r2) = format_label(2024, 3, 1, TimeUnit::YearQuarter, false, fiscal_start).unwrap();
        assert_eq!(r1, "Q1");
        assert_eq!(r2, "2024");

        // June/September/December step through Q2/Q3/Q4.
        let (r1, _) = format_label(2024, 6, 1, TimeUnit::YearQuarter, false, fiscal_start).unwrap();
        assert_eq!(r1, "Q2");
        let (r1, _) = format_label(2024, 9, 1, TimeUnit::YearQuarter, false, fiscal_start).unwrap();
        assert_eq!(r1, "Q3");
        let (r1, _) = format_label(2024, 12, 1, TimeUnit::YearQuarter, false, fiscal_start).unwrap();
        assert_eq!(r1, "Q4");

        // January/February still belong to the fiscal year that opened the
        // previous March — still Q4, and NOT a fiscal year-start month, so
        // no year row.
        let (r1, r2) = format_label(2025, 1, 1, TimeUnit::YearQuarter, false, fiscal_start).unwrap();
        assert_eq!(r1, "Q4");
        assert_eq!(r2, "");
    }

    /// is_opener honors fiscal_year_start_month for both the yearquarter and
    /// year cadences — the same boundary shift `format_label` above pins for
    /// text, here pinned for which domain positions actually open a period.
    #[test]
    fn is_opener_honors_fiscal_year_start_month() {
        let fiscal_start = 3; // March
        assert!(is_opener(2024, 3, 1, TimeUnit::YearMonthDate, TimeUnit::YearQuarter, fiscal_start));
        assert!(!is_opener(2024, 1, 1, TimeUnit::YearMonthDate, TimeUnit::YearQuarter, fiscal_start));
        assert!(is_opener(2024, 3, 1, TimeUnit::YearMonthDate, TimeUnit::Year, fiscal_start));
        assert!(!is_opener(2024, 1, 1, TimeUnit::YearMonthDate, TimeUnit::Year, fiscal_start));
    }

    /// yearmonthdate cadence: day number, two-row at month/year openers.
    #[test]
    fn yearmonthdate_labels_anchor_and_openers() {
        // First label (Jan 1): "1" / "Jan'24"
        let (r1, r2) = format_label(2024, 1, 1, TimeUnit::YearMonthDate, true, 1).unwrap();
        assert_eq!(r1, "1");
        assert_eq!(r2, "Jan'24");

        // Feb 1 (month opener, not year start): "1" / "Feb"
        let (r1, r2) = format_label(2024, 2, 1, TimeUnit::YearMonthDate, false, 1).unwrap();
        assert_eq!(r1, "1");
        assert_eq!(r2, "Feb");

        // Jan 1 2025 (year boundary, not first): "1" / "Jan'25"
        let (r1, r2) = format_label(2025, 1, 1, TimeUnit::YearMonthDate, false, 1).unwrap();
        assert_eq!(r1, "1");
        assert_eq!(r2, "Jan'25");

        // Inner day: "15" / ""
        let (r1, r2) = format_label(2024, 3, 15, TimeUnit::YearMonthDate, false, 1).unwrap();
        assert_eq!(r1, "15");
        assert_eq!(r2, "");
    }

    /// build_temporal_labels produces month-opener labels for daily data.
    #[test]
    fn temporal_labels_daily_at_month_cadence() {
        // 60 daily data points: Jan + Feb 2024
        let domain: Vec<String> = (0..60)
            .map(|i| {
                let m = if i < 31 { 1u8 } else { 2 };
                let d = if i < 31 { i + 1 } else { i - 30 };
                format!("2024-{m:02}-{d:02}")
            })
            .collect();
        let labels = build_temporal_labels(
            &domain,
            TimeUnit::YearMonthDate,
            TimeUnit::YearMonth,
            TimeUnit::YearMonth,
            1,
            None,
        )
        .unwrap();
        // Should produce: Jan (first, with 2024 context) and Feb
        assert_eq!(labels.len(), 2);
        assert_eq!(labels[0].row1, "Jan");
        assert_eq!(labels[0].row2, "2024");
        assert_eq!(labels[1].row1, "Feb");
        assert_eq!(labels[1].row2, "");
    }

    // ── resolve_label_visibility / resolve_temporal_labels ──────────────────
    //
    // `measure` below is a synthetic char-width function (7px/char, no
    // font dependency) — the fit arithmetic only needs *some* realistic
    // width, not exact font metrics; production's own font measurer is
    // wired in at the call sites (line.rs/bar.rs).

    fn char_measure(s: &str) -> Result<f64, String> {
        Ok(s.chars().count() as f64 * 7.0)
    }

    fn iso_range(start: (i32, u8, u8), step_days: i64, count: usize) -> Vec<String> {
        // Simple proleptic-Gregorian day stepper, good enough for test fixtures.
        fn from_days_approx(mut y: i32, mut m: u8, mut d: u8, mut delta: i64) -> (i32, u8, u8) {
            // Walk day-by-day — test-only, count is small (≤365).
            while delta > 0 {
                d += 1;
                let dim = match m {
                    1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
                    4 | 6 | 9 | 11 => 30,
                    _ => {
                        if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 {
                            29
                        } else {
                            28
                        }
                    }
                };
                if d > dim {
                    d = 1;
                    m += 1;
                    if m > 12 {
                        m = 1;
                        y += 1;
                    }
                }
                delta -= 1;
            }
            (y, m, d)
        }
        (0..count)
            .map(|i| {
                let (y, m, d) = from_days_approx(start.0, start.1, start.2, i as i64 * step_days);
                format!("{y:04}-{m:02}-{d:02}")
            })
            .collect()
    }

    /// 52 ISO weekly Mondays from 2025-01-06, at a wide (~1128px) plot: every
    /// week's day number ("6, 3, 3, 7…") does not fit — production coarsens
    /// straight to month openers, Jan..Dec, year on the first label only.
    #[test]
    fn wk_52_coarsens_to_month_openers() {
        let domain = iso_range((2025, 1, 6), 7, 52);
        let (labels, _vt) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearWeek,
            None,
            1128.0,
            false,
            true,
            false,
            1,
            true,
            None,
            &mut char_measure,
        )
        .unwrap();
        let row1s: Vec<&str> = labels.iter().map(|l| l.row1.as_str()).collect();
        assert_eq!(
            row1s,
            vec![
                "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
            ]
        );
        assert_eq!(labels[0].row2, "2025");
        assert!(labels[1..].iter().all(|l| l.row2.is_empty()));
    }

    /// 90 daily points from 2025-01-01 at a half-width (~460px) slot: daily
    /// numbers don't fit, but the Monday rung does — day number stays on row
    /// one, month (or "Jan'25" at the anchor) lands on row two only at each
    /// month-opening Monday.
    #[test]
    fn dy_90_coarsens_to_monday_rung() {
        let domain = iso_range((2025, 1, 1), 1, 90);
        let (labels, _vt) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearMonthDate,
            None,
            460.0,
            false,
            true,
            false,
            1,
            true,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert_eq!(labels[0].row1, "6");
        assert_eq!(labels[0].row2, "Jan'25");
        let month_context: Vec<&str> =
            labels.iter().filter(|l| !l.row2.is_empty()).map(|l| l.row2.as_str()).collect();
        assert_eq!(month_context, vec!["Jan'25", "Feb", "Mar"]);
        // Every labeled tick is a Monday — day-of-week cadence, not daily.
        for label in &labels {
            let (y, m, d) = parse_iso_date(&domain[label.domain_idx]).unwrap();
            assert_eq!(weekday(y, m, d), 0, "{label:?} at {y}-{m}-{d} is not a Monday");
        }
    }

    /// 12 monthly points at a generous width: native monthly cadence already
    /// fits, so no coarsening happens — every month gets its own label.
    #[test]
    fn monthly_data_needs_no_coarsening() {
        let domain: Vec<String> = (1u8..=12).map(|m| format!("2024-{m:02}-01")).collect();
        let (labels, _vt) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearMonth,
            None,
            1200.0,
            false,
            true,
            false,
            1,
            true,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert_eq!(labels.len(), 12);
        assert_eq!(labels[0].row1, "Jan");
        assert_eq!(labels[0].row2, "2024");
        assert_eq!(labels[11].row1, "Dec");
        assert_eq!(labels[11].row2, "");
    }

    /// 12 monthly points where every label's OWN row ("Jan".."Dec", all 3
    /// chars) clears pairwise at this band — but the anchor tick (Jan) also
    /// stacks a "2024" year row, 1 char wider than any row 1. Folding that
    /// year row into the anchor's reserved width via `max()` (the bug) pads
    /// the anchor-adjacent pair past the band and coarsens the whole axis to
    /// quarters; production's `_cadence_token_width` reserves row 1 ALONE
    /// (`time_unit_detect.py:243-257`) and checks the year row as its own,
    /// independent clearance (`_pair_clears:394-400`) — which never fires
    /// here, since no other tick in this domain also carries a year row.
    #[test]
    fn monthly_row1_fits_despite_wider_anchor_year_row() {
        let domain: Vec<String> = (1u8..=12).map(|m| format!("2024-{m:02}-01")).collect();
        let (labels, _vt) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearMonth,
            None,
            12.0 * 22.0, // band = 22px: row1-only clears (21 <= 22), folded doesn't (24.5 > 22)
            false,
            true,
            false,
            1,
            true,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert_eq!(
            labels.len(),
            12,
            "row 1 alone clears at this band; folding the year row into it should not coarsen"
        );
    }

    /// Consecutive Dec→Jan tick pair where BOTH carry a year row (Dec is the
    /// domain's anchor, so its row 2 is a bare year per `format_label`'s
    /// `is_first` branch; Jan opens the next year). Row 1 ("Dec"/"Jan")
    /// clears easily, but the two year rows ("2024"/"2025", same width)
    /// alone do not at this band — production's independent year-row check
    /// (`_pair_clears:394-400`, gated on `i_carries_year_row &&
    /// j_carries_year_row`) must still coarsen the axis even though row 1
    /// fits; deleting that check would wrongly leave it flat.
    #[test]
    fn year_row_own_independent_clearance_still_coarsens() {
        let mut domain = vec!["2024-12-01".to_string()];
        for m in 1u8..=11 {
            domain.push(format!("2025-{m:02}-01"));
        }
        let (labels, _vt) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearMonth,
            None,
            12.0 * 27.0, // band = 27px: row1 pair clears (21), year-row pair does not (28)
            false,
            true,
            false,
            1,
            true,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert!(
            labels.len() < 12,
            "the Dec/Jan year-row collision must coarsen the axis even though row 1 fits"
        );
    }

    /// 130 weekly points (~2.5 years): month cadence itself doesn't clear at
    /// this width either, so visibility steps one more rung to quarter
    /// openers — but the *text* stays month names ("Jan Apr Jul Oct"), never
    /// "Q1 Q2 Q3 Q4" — a label names the tick, not the coarser interval.
    #[test]
    fn wk_130_visibility_steps_to_quarter_openers_with_month_text() {
        let domain = iso_range((2025, 1, 6), 7, 130);
        let (labels, _vt) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearWeek,
            None,
            460.0,
            false,
            true,
            false,
            1,
            true,
            None,
            &mut char_measure,
        )
        .unwrap();
        let row1s: Vec<&str> = labels.iter().map(|l| l.row1.as_str()).collect();
        assert_eq!(row1s, vec!["Jan", "Apr", "Jul", "Oct", "Jan", "Apr", "Jul", "Oct", "Jan", "Apr"]);
        assert!(row1s.iter().all(|s| !s.starts_with('Q')));
    }

    /// Authored `axis_x.labels.time_unit` pins format and visibility to the
    /// same grain — no auto-coarsening runs at all.
    #[test]
    fn authored_label_time_unit_skips_coarsening() {
        // Wide enough that all 52 weekly labels clear each other — authored
        // format used as-is, no visibility thinning needed.
        let domain = iso_range((2025, 1, 6), 7, 52);
        let (labels, _vt) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearWeek,
            Some(TimeUnit::YearWeek),
            5200.0,
            false,
            true,
            false,
            1,
            true,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert_eq!(labels.len(), 52);
    }

    /// An authored format that matches the native grain still has its
    /// *visibility* thinned when the width doesn't hold every label —
    /// production runs the same coarsening on an authored format
    /// as it does on an auto-detected one (only the day/week
    /// native/Monday-rung candidates are auto-only). Port of the
    /// `native_day` fixture ("Encoding: yearmonthdate · labels:
    /// yearmonthdate"): thins to month openers, but the surviving ticks
    /// still read as day numbers, never month names.
    #[test]
    fn authored_native_format_still_thins_visibility() {
        let domain = iso_range((2025, 1, 1), 1, 90);
        let (labels, _vt) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearMonthDate,
            Some(TimeUnit::YearMonthDate),
            460.0,
            false,
            true,
            false,
            1,
            true,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert_eq!(labels.len(), 3); // one opener per month: Jan, Feb, Mar
        assert_eq!(labels[0].row1, "1");
        assert_eq!(labels[0].row2, "Jan'25");
        assert!(labels[1..].iter().all(|l| l.row1 == "1"));
    }

    // ── continuous / line+area utc-scale axis ───────────────────────────────
    //
    // Production's `_temporal_layout` tries the Monday-rung candidate when
    // `bucket_aligned_temporal` is true (bar's discrete x-scale, or a
    // `step`-curve line/area) or the axis is `continuous_temporal` over daily
    // data (`_label_overlap.py:570-573`); with neither it skips straight from
    // native-flat to month openers when native doesn't fit.

    /// A short continuous daily domain that fits flat needs no promotion —
    /// the Monday rung only matters when native doesn't fit.
    #[test]
    fn continuous_axis_keeps_native_daily_cadence_when_it_fits() {
        let domain = iso_range((2025, 1, 20), 1, 6);
        let (labels, _vt) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearMonthDate,
            None,
            1200.0,
            true,
            false,
            true,
            1,
            true,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert_eq!(labels.len(), 6);
        assert!(labels.iter().all(|l| l.row1.parse::<u32>().is_ok()));
    }

    /// 150 daily points from 2025-02-19 at a 900px plot: daily labels (21px
    /// each at a 6px band) cannot fit, the Monday rung (42px pitch) can. A
    /// continuous axis takes that rung before it falls to month openers
    /// (`_label_overlap.py:570-598`), measured over every calendar day.
    #[test]
    fn continuous_daily_axis_takes_the_weekly_rung_before_month_openers() {
        let domain = iso_range((2025, 2, 19), 1, 150);
        let (labels, (format_tu, visibility_tu)) = {
            let dates = parse_domain_dates(&domain);
            let tus = resolve_label_visibility(
                &dates,
                TimeUnit::YearMonthDate,
                None,
                900.0,
                true,
                false,
                true,
                1,
                true,
                None,
                &mut char_measure,
            )
            .unwrap();
            (build_temporal_labels(&domain, TimeUnit::YearMonthDate, tus.0, tus.1, 1, None).unwrap(), tus)
        };
        assert_eq!((format_tu, visibility_tu), (TimeUnit::YearWeek, TimeUnit::YearWeek));
        assert_eq!(labels[0].row1, "24");
        assert_eq!(labels[0].row2, "Feb'25");
        for label in &labels {
            let (y, m, d) = parse_iso_date(&domain[label.domain_idx]).unwrap();
            assert_eq!(weekday(y, m, d), 0, "{label:?} at {y}-{m}-{d} is not a Monday");
        }
    }

    /// The same domain and width with neither gate set (not bucket-aligned,
    /// not continuous) jumps from the native misfit straight to month openers.
    #[test]
    fn daily_axis_without_either_gate_keeps_month_openers() {
        let domain = iso_range((2025, 2, 19), 1, 150);
        let (labels, visibility_tu) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearMonthDate,
            None,
            900.0,
            true,
            false,
            false,
            1,
            true,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert_eq!(visibility_tu, TimeUnit::YearMonth);
        let row1s: Vec<&str> = labels.iter().map(|l| l.row1.as_str()).collect();
        assert_eq!(row1s, ["Mar", "Apr", "May", "Jun", "Jul"]);
    }

    /// A continuous axis measures every calendar day between its extremes
    /// (`enumerated_axis_values`, `_label_overlap.py:539-546`), whether or not
    /// the rows cover each day: dropping a day from the domain leaves the
    /// visibility and the label texts unchanged.
    #[test]
    fn continuous_daily_axis_with_a_missing_day_resolves_like_the_full_domain() {
        let full = iso_range((2025, 2, 19), 1, 150);
        let mut gapped = full.clone();
        gapped.remove(10);
        let resolve = |domain: &[String]| {
            let (labels, visibility_tu) = resolve_temporal_labels(
                domain,
                TimeUnit::YearMonthDate,
                None,
                900.0,
                true,
                false,
                true,
                1,
                true,
                None,
                &mut char_measure,
            )
            .unwrap();
            let rows: Vec<(String, String)> = labels.into_iter().map(|l| (l.row1, l.row2)).collect();
            (visibility_tu, rows)
        };
        let (full_visibility, full_rows) = resolve(&full);
        assert_eq!(full_visibility, TimeUnit::YearWeek);
        assert_eq!(resolve(&gapped), (full_visibility, full_rows));
    }

    /// Rows Jan–Jun 2022 and Dec 2023: per-row, seven monthly labels clear;
    /// over the 24 months a continuous axis measures
    /// (`enumerated_axis_values`), the band is too narrow for them, so the
    /// ladder coarsens. Without `continuous_temporal` the rows alone decide.
    #[test]
    fn continuous_monthly_axis_measures_the_gap_between_rows() {
        let domain: Vec<String> = ["2022-01-01", "2022-02-01", "2022-03-01", "2022-04-01", "2022-05-01", "2022-06-01", "2023-12-01"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let visibility = |continuous_temporal: bool| {
            resolve_temporal_labels(
                &domain,
                TimeUnit::YearMonth,
                None,
                480.0,
                false,
                false,
                continuous_temporal,
                1,
                true,
                None,
                &mut char_measure,
            )
            .unwrap()
            .1
        };
        assert_eq!(visibility(false), TimeUnit::YearMonth);
        assert_ne!(visibility(true), TimeUnit::YearMonth);
    }

    /// Regression for `playground/xaxis-cadence-ladder`'s `nov_line`: 16
    /// monthly points (Nov 2024 – Feb 2026) on a continuous (line) axis at a
    /// half-width plot. Measured against the real render: `band=28.2`,
    /// `"Nov"`/`"Dec"` each ~21px wide. With `edge_labels_flushed=true`, the
    /// domain's first tick ("Nov", flushed to the plot's left edge) reserves
    /// its *full* width rather than half, so `Nov`→`Dec` fails to clear and
    /// the ladder promotes visibility straight to quarter
    /// openers — matching production exactly. Deletion counterfactual:
    /// hardcoding `edge_labels_flushed=false` here (the pre-fix behavior)
    /// makes every month "fit" and this test fails with 16 monthly labels
    /// instead of 5 quarterly ones.
    #[test]
    fn flushed_continuous_axis_coarsens_month_to_quarter_on_first_pair_overflow() {
        let domain: Vec<String> = (0..16)
            .map(|i| {
                let total = 10 + i; // Nov = 0-based month index 10
                let year = 2024 + total / 12;
                let month = total % 12 + 1;
                format!("{year:04}-{month:02}-01")
            })
            .collect();
        let (labels, _vt) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearMonth,
            None,
            28.2 * 16.0,
            true,
            false,
            false,
            1,
            true,
            None,
            &mut char_measure,
        )
        .unwrap();
        let row1s: Vec<&str> = labels.iter().map(|l| l.row1.as_str()).collect();
        assert_eq!(row1s, vec!["Jan", "Apr", "Jul", "Oct", "Jan"], "coarsens to quarter openers");
    }

    #[test]
    fn label_half_width_takes_the_wider_row() {
        let label = TemporalLabel { domain_idx: 0, row1: "Jan".to_string(), row2: "2025".to_string() };
        let widths = [("Jan", 18.9546), ("2025", 27.0435)];
        let mut measure = |s: &str| -> Result<f64, String> {
            Ok(widths.iter().find(|(t, _)| *t == s).unwrap().1)
        };
        let half = temporal_label_half_width(&label, &mut measure).unwrap();
        assert!((half - 27.0435 / 2.0).abs() < 1e-9, "half-width should use the wider row (2025), got {half}");
    }

    #[test]
    fn label_half_width_ignores_empty_row2() {
        let label = TemporalLabel { domain_idx: 0, row1: "15".to_string(), row2: String::new() };
        let mut measure = |_: &str| -> Result<f64, String> { Ok(10.0) };
        let half = temporal_label_half_width(&label, &mut measure).unwrap();
        assert!((half - 5.0).abs() < 1e-9);
    }

    #[test]
    fn utc_fractions_are_time_proportional() {
        // Jan/Feb/Mar starts: spans 31 and 28 days — Feb sits at 31/59,
        // not the point-scale midpoint (oracle: area-charts_3's scenegraph
        // puts Feb at 98.5 of pw 480 = 31/151 of a Jan..Jun domain).
        let dom = vec![
            "2026-01-01".to_string(),
            "2026-02-01".to_string(),
            "2026-03-01".to_string(),
        ];
        let fr = utc_position_fractions(&dom).unwrap();
        assert_eq!(fr[0], 0.0);
        assert!((fr[1] - 31.0 / 59.0).abs() < 1e-12, "{fr:?}");
        assert_eq!(fr[2], 1.0);
        // Unparseable domain → None (callers keep point fractions).
        assert!(utc_position_fractions(&["a".into(), "b".into()]).is_none());
        // Unordered domain (ragged multi-series union): the extent is a
        // min/max aggregate like Vega's, so every fraction stays in [0, 1].
        let unordered = vec![
            "2026-02-01".to_string(),
            "2026-01-01".to_string(),
            "2026-03-01".to_string(),
        ];
        let fr = utc_position_fractions(&unordered).unwrap();
        assert!((fr[0] - 31.0 / 59.0).abs() < 1e-12, "{fr:?}");
        assert_eq!((fr[1], fr[2]), (0.0, 1.0));
        // Leap-century arithmetic: days_from_civil epoch identity.
        assert_eq!(days_from_civil(1970, 1, 1), 0);
        assert_eq!(days_from_civil(2026, 1, 1), 20454);
    }

    // ── week_floor / utc_domain_extent / utc_fraction_at (L9) ────────────────

    #[test]
    fn week_floor_snaps_to_the_containing_sunday() {
        // Vega-Lite's `utcyearweek` timeUnit floors every value to d3's
        // `utcSunday.floor` — Monday 2025-09-08 belongs to the week that
        // opened Sunday 2025-09-07.
        assert_eq!(week_floor("2025-09-08").as_deref(), Some("2025-09-07"));
        // A Sunday is already its own week start.
        assert_eq!(week_floor("2025-09-07").as_deref(), Some("2025-09-07"));
        // Saturday floors back six days.
        assert_eq!(week_floor("2025-09-13").as_deref(), Some("2025-09-07"));
        assert_eq!(week_floor("not-a-date"), None);
    }

    #[test]
    fn label_fraction_diverges_from_vertex_fraction_on_a_floored_week_domain() {
        // The vertex domain floors to Sunday week starts; a raw (unfloored)
        // opener value evaluated against that SAME floored extent lands off
        // the vertex position by the weekday offset — L9's "+1 day" shape,
        // generalized to any weekday the data carries.
        let raw = vec!["2025-09-08".to_string(), "2025-09-22".to_string()];
        let floored: Vec<String> = raw.iter().map(|d| week_floor(d).unwrap()).collect();
        assert_eq!(floored, vec!["2025-09-07", "2025-09-21"]);
        let vertex_fracs = utc_position_fractions(&floored).unwrap();
        assert_eq!(vertex_fracs, vec![0.0, 1.0]);
        let extent = utc_domain_extent(&floored).unwrap();
        let label_frac0 = utc_fraction_at(&raw[0], extent).unwrap();
        // Monday sits 1 of 14 floored days after the Sunday domain start —
        // NOT at the vertex's own 0.0.
        assert!((label_frac0 - 1.0 / 14.0).abs() < 1e-12, "{label_frac0}");
    }

    #[test]
    fn complete_buckets_enumerates_every_bucket_from_the_floored_minimum() {
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        // Weeks keep the data's own anchor weekday and string form.
        let weeks = complete_buckets(
            &s(&["2025-01-20T00:00:00", "2025-01-06T00:00:00", "2025-02-10T00:00:00"]),
            TimeUnit::YearWeek,
            1,
        );
        assert_eq!(
            weeks,
            s(&["2025-01-06T00:00:00", "2025-01-13", "2025-01-20T00:00:00", "2025-01-27", "2025-02-03", "2025-02-10T00:00:00"])
        );
        // Quarters floor to the fiscal anchor: April-start year, data from June.
        let quarters = complete_buckets(&s(&["2025-06-01", "2025-10-01"]), TimeUnit::YearQuarter, 4);
        assert_eq!(quarters, s(&["2025-04-01", "2025-07-01", "2025-10-01"]));
        // A yearly grain floors to the fiscal anchor (a February start puts the
        // first Jan-1 date in the previous February) and steps a year at a time
        // from there, keeping the anchor's month.
        assert_eq!(
            complete_buckets(&s(&["2024-01-01", "2025-01-01"]), TimeUnit::Year, 2),
            s(&["2023-02-01", "2024-02-01"])
        );
        // Months across a year end.
        assert_eq!(
            complete_buckets(&s(&["2024-11-01", "2025-02-01"]), TimeUnit::YearMonth, 1),
            s(&["2024-11-01", "2024-12-01", "2025-01-01", "2025-02-01"])
        );
        assert_eq!(civil_from_days(days_from_civil(2024, 2, 29) + 1), (2024, 3, 1));
    }

    #[test]
    fn labeled_buckets_and_years_normalize_to_iso_and_invalid_ones_err() {
        let rows = |cells: &[&str]| -> Vec<serde_json::Map<String, serde_json::Value>> {
            cells.iter().map(|c| serde_json::Map::from_iter([("x".to_string(), serde_json::json!(c))])).collect()
        };
        let x = |rows: Vec<serde_json::Map<String, serde_json::Value>>| -> Vec<String> {
            rows.iter().map(|r| r["x"].as_str().unwrap().to_string()).collect()
        };
        assert_eq!(x(normalize_labeled_temporal(&rows(&["2024-Q1", "2024-Q2"]), "x").unwrap()), ["2024-01-01", "2024-04-01"]);
        assert_eq!(x(normalize_labeled_temporal(&rows(&["2019", "2020"]), "x").unwrap()), ["2019-01-01", "2020-01-01"]);
        assert!(normalize_labeled_temporal(&rows(&["2023-W51", "2023-W53"]), "x").is_err(), "an out-of-range ISO week raises");
        assert!(normalize_labeled_temporal(&rows(&["2024-Q1", "2024-01-05"]), "x").is_err(), "a mix of label and date raises");
    }

    // ── resolve_vega_label_layout (L1-L4) ────────────────────────────────

    const TILT_LADDER: [f64; 5] = [0.0, -30.0, -45.0, -60.0, -90.0];

    /// The Vega arm's continuous daily axis: 150 days from 2025-02-19 at 900px.
    /// Native daily labels cannot clear, the Monday rung can
    /// (`_submonth_candidate_fits`), so it wins before the month-opener ladder
    /// (`_label_overlap.py:570-598`) — flat, no parity, no tilt.
    #[test]
    fn vega_continuous_daily_axis_takes_the_monday_rung_before_month_openers() {
        let dates = parse_domain_dates(&iso_range((2025, 2, 19), 1, 150));
        let layout = resolve_vega_label_layout(
            &dates,
            TimeUnit::YearMonthDate,
            TimeUnit::YearMonthDate,
            true,
            900.0,
            true,
            true,
            1,
            true,
            true,
            Some(&TILT_LADDER),
            11.0,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert_eq!((layout.format_tu, layout.visibility_tu), (TimeUnit::YearWeek, TimeUnit::YearWeek));
        assert!(!layout.parity && layout.angle == 0.0);
    }

    /// Without `continuous_temporal` the same domain goes native-misfit to the
    /// month-opener ladder: the Monday rung is a continuous-axis law.
    #[test]
    fn vega_daily_axis_that_is_not_continuous_keeps_month_openers() {
        let dates = parse_domain_dates(&iso_range((2025, 2, 19), 1, 150));
        let layout = resolve_vega_label_layout(
            &dates,
            TimeUnit::YearMonthDate,
            TimeUnit::YearMonthDate,
            true,
            900.0,
            true,
            false,
            1,
            true,
            true,
            Some(&TILT_LADDER),
            11.0,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert_eq!(layout.visibility_tu, TimeUnit::YearMonth);
    }

    /// A month with a single opener has nothing to promote to, so an
    /// overflowing Monday rung tilts (`_label_overlap.py:603-628`) with the
    /// directive `allow`, unlike the native generic layout. Production also
    /// records `collision_label_count` when the steepest tilt still misfits
    /// and may then defer thinning; this arm, like the bucketed arm, does
    /// not carry that flag, so `allow` here is the port's reading, not a
    /// production pin.
    #[test]
    fn vega_continuous_daily_axis_inside_one_month_tilts_the_monday_rung() {
        let dates = parse_domain_dates(&iso_range((2025, 1, 1), 1, 30));
        let layout = resolve_vega_label_layout(
            &dates,
            TimeUnit::YearMonthDate,
            TimeUnit::YearMonthDate,
            true,
            40.0,
            true,
            true,
            1,
            true,
            true,
            Some(&TILT_LADDER),
            11.0,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert_eq!((layout.format_tu, layout.visibility_tu), (TimeUnit::YearWeek, TimeUnit::YearWeek));
        assert!(!layout.parity);
        assert_eq!(layout.angle, -90.0);
    }

    /// The Vega arm measures the enumerated days too: a missing day leaves the
    /// layout unchanged.
    #[test]
    fn vega_continuous_daily_axis_with_a_missing_day_resolves_like_the_full_domain() {
        let full = iso_range((2025, 2, 19), 1, 150);
        let mut gapped = full.clone();
        gapped.remove(10);
        let layout = |domain: &[String]| {
            let layout = resolve_vega_label_layout(
                &parse_domain_dates(domain),
                TimeUnit::YearMonthDate,
                TimeUnit::YearMonthDate,
                true,
                900.0,
                true,
                true,
                1,
                true,
                true,
                Some(&TILT_LADDER),
                11.0,
                None,
                &mut char_measure,
            )
            .ok()
            .expect("resolves");
            (layout.format_tu, layout.visibility_tu, layout.parity, layout.angle)
        };
        assert_eq!(layout(&gapped), layout(&full));
        assert_eq!((layout(&full).0, layout(&full).1), (TimeUnit::YearWeek, TimeUnit::YearWeek));
    }

    /// The Vega arm's monthly ladder runs over the enumerated months as well
    /// (same rows and width as the legacy-arm test).
    #[test]
    fn vega_continuous_monthly_axis_measures_the_gap_between_rows() {
        let dates = parse_domain_dates(
            &["2022-01-01", "2022-02-01", "2022-03-01", "2022-04-01", "2022-05-01", "2022-06-01", "2023-12-01"]
                .iter()
                .map(|s| s.to_string())
                .collect::<Vec<_>>(),
        );
        let visibility = |continuous_temporal: bool| {
            resolve_vega_label_layout(
                &dates,
                TimeUnit::YearMonth,
                TimeUnit::YearMonth,
                true,
                480.0,
                false,
                continuous_temporal,
                1,
                true,
                false,
                None,
                11.0,
                None,
                &mut char_measure,
            )
            .ok()
            .expect("resolves")
            .visibility_tu
        };
        assert_eq!(visibility(false), TimeUnit::YearMonth);
        assert_ne!(visibility(true), TimeUnit::YearMonth);
    }

    /// L4: a yearquarter ladder that fails at its own grain (band too tight
    /// for `Q1`/`Q2` to clear) steps to `year` and promotes the vocabulary
    /// to the bare year — mirrors `native_quarter` (14 quarterly openers,
    /// 4 clearing Januaries at a spacing under the sparse ceiling).
    #[test]
    fn yearquarter_ladder_steps_to_year_and_promotes_the_bare_year_vocabulary() {
        let mut dates = Vec::new();
        for y in 2023..=2026 {
            for q in 0..4 {
                dates.push((y, q * 3 + 1, 1u8));
            }
        }
        let layout = resolve_vega_label_layout(
            &dates,
            TimeUnit::YearQuarter,
            TimeUnit::YearQuarter,
            true,
            176.0, // usable_width / 16 dates = 11px band — clears the
            // flushed first year label's FULL (not halved) width against
            // its neighbor 4 indices away (`_pair_clears`'s flush rule).
            true,
            false,
            1,
            true,
            true,
            Some(&TILT_LADDER),
            11.0,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert_eq!(layout.format_tu, TimeUnit::Year);
        assert_eq!(layout.visibility_tu, TimeUnit::Year);
        assert!(!layout.parity, "4 Januaries at 44px spacing clear under the 90px sparse ceiling");
        assert_eq!(layout.angle, 0.0);
    }

    /// L1+L3: 30 consecutive daily dates in one month (<2 month openers) at
    /// a band too tight even halved — mirrors `orders`: parity thinning
    /// plus the steepest tilt rung (day/week labels are short enough that
    /// only -90 clears a very narrow band).
    #[test]
    fn daily_generic_layout_parities_then_tilts_to_ninety() {
        let domain = iso_range((2024, 1, 1), 1, 30);
        let dates = parse_domain_dates(&domain);
        let layout = resolve_vega_label_layout(
            &dates,
            TimeUnit::YearMonthDate,
            TimeUnit::YearMonthDate,
            true,
            300.0, // usable_width / 30 dates = 10px band
            true,
            false,
            1,
            true,
            true,
            Some(&TILT_LADDER),
            11.0,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert_eq!(layout.format_tu, TimeUnit::YearMonthDate);
        assert_eq!(layout.visibility_tu, TimeUnit::YearMonthDate);
        assert!(layout.parity, "9.67px band can't clear even halved");
        assert_eq!(layout.angle, -90.0);
    }

    /// L1: a daily domain whose native labels already fit flat draws every
    /// day, untilted, unthinned — the plain end of `_generic_layout`.
    #[test]
    fn daily_native_labels_that_fit_flat_stay_flat() {
        let domain = iso_range((2024, 1, 1), 1, 5);
        let dates = parse_domain_dates(&domain);
        let layout = resolve_vega_label_layout(
            &dates,
            TimeUnit::YearMonthDate,
            TimeUnit::YearMonthDate,
            true,
            10_000.0,
            true,
            false,
            1,
            true,
            true,
            Some(&TILT_LADDER),
            11.0,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert!(!layout.parity);
        assert_eq!(layout.angle, 0.0);
    }

    /// CRITICAL-1 regression: an authored `labels.time_unit` equal to the
    /// data's own detected grain (`yearweek` on `yearweek` data —
    /// production's `native_week` case) is NOT auto, so it skips the
    /// native day/week branch entirely (`_label_overlap.py:287-289` gates
    /// on `axis.labels.time_unit in (None, "auto")`) and enters the SAME
    /// bucketed ladder every other grain uses — `BUCKETED_CALENDAR_UNITS`
    /// (`time_unit_detect.py:189-191`) is all FIVE calendar grains, not
    /// just year/quarter/month. The ladder tests week's own openers first
    /// (not a structural month promotion), then steps month -> quarter.
    #[test]
    fn authored_native_grain_enters_the_bucketed_ladder_at_its_own_grain() {
        let domain = iso_range((2024, 1, 1), 7, 52); // 52 weekly Mondays
        let dates = parse_domain_dates(&domain);
        let layout = resolve_vega_label_layout(
            &dates,
            TimeUnit::YearWeek,
            TimeUnit::YearWeek, // authored labels.time_unit: yearweek
            false,              // NOT auto — explicitly authored
            208.0,              // usable_width / 52 dates = 4px band
            true,
            false,
            1,
            true,
            true,
            Some(&TILT_LADDER),
            11.0,
            None,
            &mut char_measure,
        )
        .unwrap();
        // format stays yearweek (visibility never reaches `year`, so no
        // bare-year promotion); visibility steps two rungs: week -> month
        // -> quarter, same two-row day/context vocabulary at each rung.
        assert_eq!(layout.format_tu, TimeUnit::YearWeek);
        assert_eq!(layout.visibility_tu, TimeUnit::YearQuarter);
        assert!(!layout.parity);
    }

    /// L2: a steep tilt inlines the two-row label onto one row, day-number
    /// first for day/week vocab; month/quarter vocab puts the year first.
    /// A label with no context row is unaffected.
    #[test]
    fn inline_label_orders_day_first_month_orders_year_first() {
        let day = TemporalLabel { domain_idx: 0, row1: "1".into(), row2: "Jan'24".into() };
        assert_eq!(inline_label(&day, TimeUnit::YearMonthDate), "1 Jan'24");
        let month = TemporalLabel { domain_idx: 0, row1: "Jan".into(), row2: "2024".into() };
        assert_eq!(inline_label(&month, TimeUnit::YearMonth), "2024 Jan");
        let quarter = TemporalLabel { domain_idx: 0, row1: "Q1".into(), row2: "2024".into() };
        assert_eq!(inline_label(&quarter, TimeUnit::YearQuarter), "2024 Q1");
        let inner = TemporalLabel { domain_idx: 5, row1: "15".into(), row2: String::new() };
        assert_eq!(inline_label(&inner, TimeUnit::YearMonthDate), "15");
    }

    /// L5: Vega-Lite's own default single-row temporal format per grain
    /// (probe-pinned: `test_vl_probe.py::vl_default_temporal_format_by_grain`).
    #[test]
    fn vl_default_format_matches_vega_lite_defaults() {
        assert_eq!(vl_default_format(2024, 1, 1, TimeUnit::Year).unwrap(), "2024");
        assert_eq!(vl_default_format(2024, 4, 1, TimeUnit::YearQuarter).unwrap(), "2024 Q2");
        assert_eq!(vl_default_format(2024, 2, 1, TimeUnit::YearMonth).unwrap(), "Feb 2024");
        assert_eq!(vl_default_format(2024, 1, 5, TimeUnit::YearMonthDate).unwrap(), "Jan 05, 2024");
        assert!(vl_default_format(2024, 1, 1, TimeUnit::YearWeek).is_err());
    }

    /// MEDIUM(b) round-2 regression: `axis_x.labels.time_unit` also
    /// accepts the five time-PART grains (`monthofyear`, `dayofweek`,
    /// `dayofmonth`, `dayofyear`, `hourofday` — `style/theme/axis.py`'s
    /// `_TIME_UNIT_LITERAL`), which production routes through
    /// `_generic_temporal_labels`, not the bucketed calendar ladder
    /// (`_label_overlap.py:339-360`: that format is not in
    /// `BUCKETED_CALENDAR_UNITS`). `parse_authored_time_unit` doesn't
    /// recognize them, so `format_tu` silently fell back to `data_tu` and
    /// `label_auto` stayed false — the SAME shape as an unauthored axis,
    /// drawing the wrong (bucketed) vocabulary instead of refusing an
    /// unmodeled combination.
    #[test]
    fn a_time_part_labels_time_unit_refuses_rather_than_misrouting_to_the_bucketed_ladder() {
        let dates: Vec<(i32, u8, u8)> = (1..=12).map(|m| (2024, m, 1)).collect();
        for tu in ["monthofyear", "dayofweek", "dayofmonth", "dayofyear", "hourofday"] {
            let domain: Vec<String> =
                dates.iter().map(|&(y, m, d)| format!("{y:04}-{m:02}-{d:02}")).collect();
            let result = resolve_vega_x_labels(
                &domain,
                TimeUnit::YearMonth,
                Some(tu),
                1000.0,
                true,
                false,
                1,
                true,
                true,
                Some(&TILT_LADDER),
                11.0,
                None,
                None,
                &mut char_measure,
            );
            match result {
                Ok(_) => panic!("{tu:?} must refuse, not silently draw the bucketed ladder"),
                Err(err) => assert!(err.contains(tu), "expected {tu:?} named in the refusal, got: {err}"),
            }
        }
    }

    /// A2's whole ladder, pinned against `vl_convert.vega_to_scenegraph` on
    /// `area_review`'s own compiled spec with the pane width and the data
    /// domain swept (`scratchpad/a2sweep2.py`). Each row is
    /// `(domain, plot width, the labels Vega draws)` — the rung d3 picks
    /// AND the multi-scale specifier the tick's finest non-zero unit gives
    /// it: `%Y` at a year start, `%B` at any other month start, `%b %d` on
    /// a Sunday, `%a %d` on any other day.
    #[test]
    fn the_bare_axis_ticks_are_d3s_own_across_the_whole_interval_ladder() {
        let cases: &[(&str, &str, f64, &[&str])] = &[
            ("2024-01-07", "2025-06-29", 80.0, &["2025"]),
            ("2024-01-07", "2025-06-29", 120.0, &["April", "July", "October", "2025", "April"]),
            ("2024-01-07", "2025-06-29", 320.0, &["April", "July", "October", "2025", "April"]),
            (
                "2024-01-07",
                "2025-06-29",
                564.0,
                &[
                    "February", "March", "April", "May", "June", "July", "August", "September",
                    "October", "November", "December", "2025", "February", "March", "April",
                    "May", "June",
                ],
            ),
            (
                "2024-01-03",
                "2024-03-03",
                400.0,
                &[
                    "Jan 07", "Jan 14", "Jan 21", "Jan 28", "Feb 04", "Feb 11", "Feb 18",
                    "Feb 25", "Mar 03",
                ],
            ),
            (
                "2024-01-03",
                "2024-01-20",
                400.0,
                &[
                    "Wed 03", "Fri 05", "Jan 07", "Tue 09", "Thu 11", "Sat 13", "Mon 15",
                    "Wed 17", "Fri 19",
                ],
            ),
            // The 2-day rung's ANCHOR: `utcDay.every(2)` is d3's field
            // filter (`getUTCDate() - 1` divisible by the step), so it
            // starts on the 11th, not on the domain's own 10th — a stride
            // running from `lo` would give `Feb 10, 12, … 28, Mar 02, 04`.
            // February 2022 has 28 days, so day-of-month parity carries
            // across the boundary here and this row says nothing about the
            // month RESTART; the row below pins that.
            (
                "2022-02-10",
                "2022-03-04",
                430.0,
                &[
                    "Fri 11", "Feb 13", "Tue 15", "Thu 17", "Sat 19", "Mon 21", "Wed 23",
                    "Fri 25", "Feb 27", "March", "Thu 03",
                ],
            ),
            // The 2-day rung's month RESTART, across a 31-day month: the
            // filter re-anchors on the 1st, so `Mon 31` is followed by
            // `February` — a ONE-day step, which no stride of 2 can
            // produce however it is anchored.
            (
                "2022-01-20",
                "2022-02-11",
                430.0,
                &[
                    "Fri 21", "Jan 23", "Tue 25", "Thu 27", "Sat 29", "Mon 31", "February",
                    "Thu 03", "Sat 05", "Mon 07", "Wed 09", "Fri 11",
                ],
            ),
            (
                "2024-01-03",
                "2024-01-12",
                400.0,
                &[
                    "Wed 03", "Thu 04", "Fri 05", "Sat 06", "Jan 07", "Mon 08", "Tue 09",
                    "Wed 10", "Thu 11", "Fri 12",
                ],
            ),
            ("2020-01-15", "2035-06-29", 120.0, &["2025", "2030", "2035"]),
            ("2020-01-15", "2075-06-29", 120.0, &["2040", "2060"]),
        ];
        for &(lo, hi, plot_w, want) in cases {
            let extent = utc_domain_extent(&[lo.to_string(), hi.to_string()]).unwrap();
            let ticks = bare_temporal_ticks((extent.0 as f64, extent.1 as f64), plot_w, None, None).expect("ticks");
            let got: Vec<&str> = ticks.iter().map(|t| t.text.as_str()).collect();
            assert_eq!(got, want, "{lo}..{hi} at {plot_w}px");
            let span = (extent.1 - extent.0) as f64;
            for t in &ticks {
                let day = extent.0 + (t.frac * span).round() as i64;
                assert!(
                    (0.0..=1.0).contains(&t.frac) && utc_fraction_at(
                        &{ let (y, m, d) = civil_from_days(day); format!("{y:04}-{m:02}-{d:02}") },
                        extent,
                    ) == Some(t.frac),
                    "every tick sits inside the domain at its own UTC fraction"
                );
            }
        }
    }

    /// An authored `ticks.count` is Vega-Lite's `tickCount: N`
    /// (`type_inference.py:417-418`), which the scale hands to d3
    /// the local time scale's `ticks(domain, N)` — the count replaces `ceil(plot_w / 40)` and the
    /// pane width stops mattering. Each row is the label list Vega draws for
    /// a bare temporal axis over the domain (`vl_convert` scenegraph, pinned
    /// by `tests/test_vl_probe.py::test_utc_ticks_at_an_authored_count`).
    #[test]
    fn an_authored_count_runs_utc_ticks_at_that_count_whatever_the_plot_width() {
        let months: &[&str] = &[
            "February", "March", "April", "May", "June", "July", "August", "September",
            "October", "November", "December", "2025", "February", "March", "April", "May",
            "June",
        ];
        let quarters: &[&str] = &["April", "July", "October", "2025", "April"];
        let weeks: &[&str] = &["Jan 07", "Jan 14", "Jan 21", "Jan 28", "Feb 04", "Feb 11"];
        let two_days: &[&str] = &[
            "2024", "Wed 03", "Fri 05", "Jan 07", "Tue 09", "Thu 11", "Sat 13", "Mon 15",
            "Wed 17", "Fri 19", "Jan 21", "Tue 23", "Thu 25", "Sat 27", "Mon 29", "Wed 31",
            "February", "Sat 03", "Mon 05", "Wed 07", "Fri 09", "Feb 11",
        ];
        let cases: &[(&str, &str, usize, &[&str])] = &[
            ("2024-01-01", "2024-02-12", 3, weeks),
            ("2024-01-01", "2024-02-12", 5, weeks),
            ("2024-01-01", "2024-02-12", 12, two_days),
            ("2024-01-07", "2025-06-23", 3, quarters),
            ("2024-01-07", "2025-06-23", 5, quarters),
            ("2024-01-07", "2025-06-23", 12, months),
        ];
        for &(lo, hi, count, want) in cases {
            let extent = utc_domain_extent(&[lo.to_string(), hi.to_string()]).unwrap();
            for plot_w in [120.0, 4000.0] {
                let ticks = bare_temporal_ticks(
                    (extent.0 as f64, extent.1 as f64),
                    plot_w,
                    Some(AuthoredTickCadence::Count(count)),
                    None,
                )
                .expect("ticks");
                let got: Vec<&str> = ticks.iter().map(|t| t.text.as_str()).collect();
                assert_eq!(got, want, "{lo}..{hi} at count {count}, {plot_w}px");
            }
        }
    }

    #[test]
    fn an_authored_count_d3_cannot_apply_is_refused_by_name() {
        assert_eq!(authored_tick_count(8.0), Ok(8));
        for bad in [0.0, -3.0, 2.5, f64::NAN, f64::INFINITY] {
            let err = authored_tick_count(bad).expect_err("not a whole count of at least 1");
            assert!(err.contains("axis_x.ticks.count"), "{bad}: {err}");
        }
    }

    fn authored_tick_texts(lo: &str, hi: &str, unit: &str, step: Option<f64>) -> Vec<String> {
        let extent = utc_domain_extent(&[lo.to_string(), hi.to_string()]).unwrap();
        let interval = authored_tick_interval(unit, step).expect("modeled interval");
        let authored = Some(AuthoredTickCadence::Interval(interval));
        let ticks = bare_temporal_ticks((extent.0 as f64, extent.1 as f64), 600.0, authored, None).expect("ticks");
        ticks.into_iter().map(|t| t.text).collect()
    }

    /// Production hands an authored `ticks.time_unit` + `step` to Vega as
    /// `tickCount: {interval, step}`, which Vega turns into
    /// `timeInterval(interval).every(step)` — the field-based d3 filters, not
    /// the count-driven ladder. Expected ticks probed against
    /// `vl_convert.vegalite_to_svg` (axis `tickCount: {interval, step}`).
    #[test]
    fn an_authored_year_step_ticks_only_the_years_it_divides() {
        assert_eq!(
            authored_tick_texts("2006-01-01", "2025-01-01", "year", Some(5.0)),
            ["2010", "2015", "2020", "2025"]
        );
    }

    #[test]
    fn an_authored_yearmonth_ticks_every_month_start_in_the_domain() {
        let got = authored_tick_texts("2024-02-01", "2026-01-01", "yearmonth", None);
        assert_eq!(got.len(), 24, "{got:?}");
        assert_eq!(&got[..3], ["February", "March", "April"]);
        assert_eq!(got[11], "2025");
        assert_eq!(got[23], "2026");
    }

    /// `type_inference.py:1043-1047` reads only the interval from
    /// `_TEMPORAL_TICK_INTERVAL` for an authored unit and adds a step only
    /// when authored: `yearquarter` alone is a monthly interval, and its
    /// table step of 3 belongs to the unauthored default cadence.
    #[test]
    fn an_authored_yearquarter_is_monthly_until_a_step_is_authored() {
        assert_eq!(authored_tick_texts("2024-02-01", "2026-01-01", "yearquarter", None).len(), 24);
        assert_eq!(
            authored_tick_texts("2024-02-01", "2026-01-01", "yearquarter", Some(3.0)),
            ["April", "July", "October", "2025", "April", "July", "October", "2026"]
        );
    }

    /// d3-time `interval.every(k)` on a field-based interval keeps the
    /// instants whose field value divides `k`: months by `getUTCMonth()`
    /// (Jan, Jun, Nov for 5), days by `getUTCDate() - 1` (restarting each
    /// month).
    #[test]
    fn an_authored_step_filters_by_the_calendar_field_not_a_running_stride() {
        assert_eq!(
            authored_tick_texts("2024-02-01", "2025-06-01", "yearmonth", Some(5.0)),
            ["June", "November", "2025", "June"]
        );
        assert_eq!(
            authored_tick_texts("2024-01-03", "2024-02-12", "yearmonthdate", Some(3.0)),
            [
                "Thu 04", "Jan 07", "Wed 10", "Sat 13", "Tue 16", "Fri 19", "Mon 22", "Thu 25",
                "Jan 28", "Wed 31", "February", "Feb 04", "Wed 07", "Sat 10"
            ]
        );
    }

    /// utcWeek has no field, so `every(k)` counts weeks from the epoch's
    /// first Sunday (1969-12-28): d3-time `interval.js` `count(0, d) % k`.
    #[test]
    fn an_authored_week_step_counts_sundays_from_the_epoch() {
        assert_eq!(
            authored_tick_texts("2024-01-03", "2024-03-20", "yearweek", Some(3.0)),
            ["Jan 14", "Feb 04", "Feb 25", "Mar 17"]
        );
        assert_eq!(authored_tick_texts("2024-01-03", "2024-03-20", "yearweek", None).len(), 11);
    }

    #[test]
    fn an_authored_tick_unit_production_has_no_interval_for_refuses_by_name() {
        for unit in ["month", "quarter", "week", "day", "none", "yearmonthdatehours"] {
            let err = authored_tick_interval(unit, None).expect_err(unit);
            assert!(
                err.contains("ticks.time_unit") && err.contains(unit),
                "names the setting and the value: {err}"
            );
        }
    }

    #[test]
    fn an_authored_tick_step_d3_cannot_apply_refuses_by_name() {
        for step in [0.0, -2.0, 2.5, f64::NAN] {
            let err = authored_tick_interval("year", Some(step)).expect_err("bad step");
            assert!(err.contains("ticks.step"), "{err}");
        }
    }

    fn civil_iso(day: i64) -> String {
        let (y, m, d) = civil_from_days(day);
        format!("{y:04}-{m:02}-{d:02}")
    }

    fn domain_of(dates: &[&str]) -> Vec<String> {
        dates.iter().map(|d| d.to_string()).collect()
    }

    /// Production labels each Vega interval TICK whose own date opens a label
    /// period (`labelExpr`: `(anchor || opens_label_period) ? text : ''`,
    /// `time_unit_detect.py:2058-2060`), whether or not a data row sits on it.
    /// Weekly Monday rows under monthly ticks: all eleven month starts speak
    /// (a data-row filter kept only the two whose 1st is a Monday).
    #[test]
    fn interval_ticks_over_weekly_rows_label_every_month_start() {
        let mondays: Vec<String> =
            (0..46).map(|i| civil_iso(days_from_civil(2024, 1, 1) + 7 * i)).collect();
        let floored: Vec<String> =
            mondays.iter().map(|d| week_floor(d).expect("a date")).collect();
        let extent = utc_domain_extent(&floored).unwrap();
        let ticks = interval_tick_days(extent, UtcTickInterval::Month(1));
        let labels = interval_tick_labels(
            &ticks,
            &mondays,
            TimeUnit::YearWeek,
            TimeUnit::YearMonth,
            TimeUnit::YearMonth,
            1,
            None,
        )
        .expect("labels");
        let rows: Vec<(&str, &str)> =
            labels.iter().map(|l| (l.row1.as_str(), l.row2.as_str())).collect();
        assert_eq!(rows.len(), 11, "{rows:?}");
        assert_eq!(rows[0], ("Jan", "2024"));
        assert_eq!(&rows[1..3], [("Feb", ""), ("Mar", "")]);
        assert_eq!(rows[10], ("Nov", ""));
    }

    /// The gate has no day clause for monthly data (`opens_label_period`,
    /// `time_unit_detect.py:1884-1891`), so under weekly ticks every Sunday
    /// of a quarter-opening month speaks, and each January one carries the
    /// year. Probed: 32 labels over 24 monthly rows.
    #[test]
    fn interval_ticks_finer_than_the_rows_repeat_the_opening_months_label() {
        let months: Vec<String> =
            (0..24).map(|i| format!("{}-{:02}-01", 2024 + i / 12, i % 12 + 1)).collect();
        let extent = utc_domain_extent(&months).unwrap();
        let ticks = interval_tick_days(extent, UtcTickInterval::Week(1));
        let labels = interval_tick_labels(
            &ticks,
            &months,
            TimeUnit::YearMonth,
            TimeUnit::YearMonth,
            TimeUnit::YearQuarter,
            1,
            None,
        )
        .expect("labels");
        let rows: Vec<(&str, &str)> =
            labels.iter().map(|l| (l.row1.as_str(), l.row2.as_str())).collect();
        assert_eq!(rows.len(), 32, "{rows:?}");
        assert_eq!(&rows[..4], [("Jan", "2024"); 4]);
        assert_eq!(rows[4], ("Apr", ""));
        assert_eq!(rows[16], ("Jan", "2025"));
    }

    /// A tick the data has no row on still speaks: Q3 and Q4 2024 sit in a
    /// six-month hole of the rows.
    #[test]
    fn interval_ticks_label_a_period_the_rows_skip() {
        let mut dates = vec!["2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01"];
        dates.extend(["2024-12-01", "2025-01-01", "2025-06-01", "2025-12-01"]);
        let domain = domain_of(&dates);
        let extent = utc_domain_extent(&domain).unwrap();
        let ticks = interval_tick_days(extent, UtcTickInterval::Month(1));
        let labels = interval_tick_labels(
            &ticks,
            &domain,
            TimeUnit::YearMonth,
            TimeUnit::YearQuarter,
            TimeUnit::YearQuarter,
            1,
            None,
        )
        .expect("labels");
        let rows: Vec<(&str, &str)> =
            labels.iter().map(|l| (l.row1.as_str(), l.row2.as_str())).collect();
        assert_eq!(
            rows,
            [
                ("Q2", "2024"),
                ("Q3", ""),
                ("Q4", ""),
                ("Q1", "2025"),
                ("Q2", ""),
                ("Q3", ""),
                ("Q4", ""),
            ]
        );
    }

    /// The anchor compares at the data's own grain, and for weekly data that
    /// is `%Y-%U` (Sunday-based week of the year), not the exact date: the
    /// Sunday Mar 31 tick shares the anchor Monday Apr 1's week, so it is the
    /// anchor and wears the year (probed against the reference render).
    #[test]
    fn the_weekly_anchor_matches_by_sunday_week_of_the_year() {
        let mondays: Vec<String> =
            (0..20).map(|i| civil_iso(days_from_civil(2024, 4, 1) + 7 * i)).collect();
        let floored: Vec<String> =
            mondays.iter().map(|d| week_floor(d).expect("a date")).collect();
        let extent = utc_domain_extent(&floored).unwrap();
        let ticks = interval_tick_days(extent, UtcTickInterval::Week(1));
        let labels = interval_tick_labels(
            &ticks,
            &mondays,
            TimeUnit::YearWeek,
            TimeUnit::YearMonth,
            TimeUnit::YearMonth,
            1,
            None,
        )
        .expect("labels");
        let rows: Vec<(&str, &str)> =
            labels.iter().map(|l| (l.row1.as_str(), l.row2.as_str())).collect();
        assert_eq!(rows[0], ("Mar", "2024"), "{rows:?}");
        assert_eq!(rows[1], ("Apr", ""), "{rows:?}");
        assert_eq!(labels[0].domain_idx, 0);
    }

    fn tick_rows(labels: &[TemporalLabel]) -> Vec<(&str, &str)> {
        labels.iter().map(|l| (l.row1.as_str(), l.row2.as_str())).collect()
    }

    /// `default_label_expr_for` reads a week-on-week axis's Vega tick, a
    /// Sunday, as its Monday bucket (`utcOffset('day', toDate(datum.value),
    /// 1)`, `time_unit_detect.py:2013-2017`) before the day-number text, the
    /// month-opener test (`utcdate <= 7`) and the anchor. Sunday Mar 31 is
    /// therefore "1 / Apr", not a March day; probed against the reference
    /// render of weekly Monday rows (labels 18, 25, 1, 8 under tick dates
    /// Mar 17, 24, 31, Apr 7).
    #[test]
    fn interval_ticks_in_the_week_vocabulary_read_the_monday_bucket() {
        let mondays: Vec<String> =
            (0..4).map(|i| civil_iso(days_from_civil(2024, 3, 18) + 7 * i)).collect();
        let floored: Vec<String> =
            mondays.iter().map(|d| week_floor(d).expect("a date")).collect();
        let extent = utc_domain_extent(&floored).unwrap();
        let ticks = interval_tick_days(extent, UtcTickInterval::Week(1));
        assert_eq!(ticks.len(), 4);
        let labels = interval_tick_labels(
            &ticks, &mondays, TimeUnit::YearWeek, TimeUnit::YearWeek, TimeUnit::YearWeek, 1, None,
        )
        .expect("labels");
        assert_eq!(
            tick_rows(&labels),
            [("18", "Mar'24"), ("25", ""), ("1", "Apr"), ("8", "")]
        );
    }

    /// A daily axis in the day vocabulary has no shift: the tick is the
    /// bucket, and a month's first day carries its month.
    #[test]
    fn interval_ticks_in_the_day_vocabulary_read_the_tick_date() {
        let days = domain_of(&["2024-03-30", "2024-04-02"]);
        let extent = utc_domain_extent(&days).unwrap();
        let ticks = interval_tick_days(extent, UtcTickInterval::Day(1));
        let labels = interval_tick_labels(
            &ticks,
            &days,
            TimeUnit::YearMonthDate,
            TimeUnit::YearMonthDate,
            TimeUnit::YearMonthDate,
            1,
            None,
        )
        .expect("labels");
        assert_eq!(
            tick_rows(&labels),
            [("30", "Mar'24"), ("31", ""), ("1", "Apr"), ("2", "")]
        );
    }

    /// The default cadence is `_TEMPORAL_TICK_INTERVAL`'s own `(interval,
    /// step)` per label grain (`type_inference.py:132-138`, `:1049-1052`);
    /// `tests/test_temporal_tick_interval_table.py` holds the table to
    /// production's.
    #[test]
    fn the_native_tick_interval_is_the_tables_interval_and_step() {
        assert_eq!(native_tick_interval(TimeUnit::Year), UtcTickInterval::Year(1));
        assert_eq!(native_tick_interval(TimeUnit::YearQuarter), UtcTickInterval::Month(3));
        assert_eq!(native_tick_interval(TimeUnit::YearMonth), UtcTickInterval::Month(1));
        assert_eq!(native_tick_interval(TimeUnit::YearWeek), UtcTickInterval::Week(1));
        assert_eq!(native_tick_interval(TimeUnit::YearMonthDate), UtcTickInterval::Day(1));
    }

    /// The day is this module's finest temporal grain, so a domain narrow
    /// enough for d3 to pick an hourly rung refuses rather than inventing
    /// a ladder it cannot format.
    #[test]
    fn a_sub_day_tick_interval_refuses() {
        let extent = utc_domain_extent(&["2024-01-03".into(), "2024-01-05".into()]).unwrap();
        let err = bare_temporal_ticks((extent.0 as f64, extent.1 as f64), 1200.0, None, None).expect_err("sub-day rung is unmodeled");
        assert!(err.contains("finer than a day"), "{err}");
    }

    // ── AuthoredTimeFormat ──────────────────────────────────────────────────

    fn month_domain(first_year: i32, count: usize) -> Vec<String> {
        (0..count)
            .map(|i| format!("{:04}-{:02}-01", first_year + (i / 12) as i32, i % 12 + 1))
            .collect()
    }

    fn authored(fmt: &str) -> Option<AuthoredTimeFormat<'_>> {
        AuthoredTimeFormat::parse("bar", Some(fmt)).unwrap()
    }

    #[test]
    fn an_authored_format_paints_portable_strftime_of_the_tick_date() {
        let _locale = crate::time_locale::en_us_scope();
        for (fmt, want) in [
            ("%b %Y", "Mar 2024"),
            ("%B %Y", "March 2024"),
            ("%-d %b", "5 Mar"),
            ("%d/%m/%y", "05/03/24"),
            ("%a %A", "Tue Tuesday"),
            ("%Y-%m-%d (week %U/%W/%V, day %j)", "2024-03-05 (week 09/10/10, day 065)"),
            ("%e|%-m|%_d|%%", " 5|3| 5|%"),
        ] {
            assert_eq!(authored(fmt).unwrap().text(2024, 3, 5).unwrap(), want, "{fmt}");
        }
    }

    #[test]
    fn a_number_format_or_no_format_is_not_an_authored_time_format() {
        assert!(AuthoredTimeFormat::parse("bar", None).unwrap().is_none());
        assert!(AuthoredTimeFormat::parse("bar", Some(",.0f")).unwrap().is_none());
        assert!(AuthoredTimeFormat::parse("bar", Some("100%%")).unwrap().is_none());
    }

    /// A directive whose Vega paint (d3-time-format, en-US) and cadence
    /// measurement (`portable_strftime`) can differ, a d3-only one, and a time
    /// of day refuse naming the directive; so do a modifier on a non-numeric
    /// directive and a trailing `%`.
    #[test]
    fn an_authored_format_refuses_what_vega_and_the_measurement_may_paint_differently() {
        for letter in ['q', 'g', 'H', 'M', 'c', 'x', 'X', 'Z', 'z', 's', 'F', 'C', 'p', 'L'] {
            let fmt = format!("%b %{letter}");
            let err = AuthoredTimeFormat::parse("bar", Some(&fmt)).expect_err(&fmt);
            assert!(err.starts_with("bar: authored time format"), "{err}");
            assert!(err.contains(&format!("uses %{letter},")), "{fmt}: {err}");
        }
        for (fmt, named) in [("%-b", "%-b"), ("%Y%", "trailing")] {
            let err = AuthoredTimeFormat::parse("bar", Some(fmt)).expect_err(fmt);
            assert!(err.contains(named), "{fmt}: {err}");
        }
    }

    #[test]
    fn a_year_before_1000_paints_padded_to_four_digits_as_d3_does() {
        assert_eq!(authored("%Y").unwrap().text(100, 1, 1).unwrap(), "0100");
        assert!(authored("%Y").unwrap().text(99, 1, 1).is_err());
        assert_eq!(authored("%Y-%m").unwrap().text(999, 3, 1).unwrap(), "0999-03");
        assert_eq!(authored("%-Y").unwrap().text(999, 1, 1).unwrap(), "999");
        assert_eq!(authored("%_Y").unwrap().text(999, 1, 1).unwrap(), " 999");
        assert_eq!(authored("%0Y-%m").unwrap().text(999, 3, 1).unwrap(), "0999-03");
    }

    #[test]
    fn the_label_unit_reads_none_and_the_time_parts_as_raw_bands() {
        use TimeUnit::{Year, YearMonth};
        assert_eq!(LabelUnit::resolve(YearMonth, None), Ok(LabelUnit::Calendar(YearMonth)));
        assert_eq!(LabelUnit::resolve(YearMonth, Some("auto")), Ok(LabelUnit::Calendar(YearMonth)));
        assert_eq!(LabelUnit::resolve(YearMonth, Some("year")), Ok(LabelUnit::Calendar(Year)));
        assert_eq!(LabelUnit::resolve(YearMonth, Some("none")), Ok(LabelUnit::Raw(None)));
        assert_eq!(
            LabelUnit::resolve(YearMonth, Some("dayofweek")),
            Ok(LabelUnit::Raw(Some(TimePart::DayOfWeek)))
        );
        assert!(LabelUnit::resolve(YearMonth, Some("fortnight")).is_err());
        assert_eq!(LabelUnit::Calendar(Year).grain(), Some(Year));
        assert_eq!(LabelUnit::Raw(None).grain(), None);
    }

    /// A raw band's crowding is measured on the author's format first, then the
    /// time part's vocabulary, then the band string itself.
    #[test]
    fn a_raw_band_measures_the_format_then_the_time_part_then_itself() {
        let _locale = crate::time_locale::en_us_scope();
        let format = AuthoredTimeFormat::parse("bar", Some("%b %Y")).unwrap();
        let none = LabelUnit::Raw(None);
        assert_eq!(none.raw_measure_text("2024-03-05", format).unwrap(), "Mar 2024");
        assert_eq!(none.raw_measure_text("2024-03-05", None).unwrap(), "2024-03-05");
        let part = |p| LabelUnit::Raw(Some(p)).raw_measure_text("2024-03-05", None).unwrap();
        assert_eq!(part(TimePart::MonthOfYear), "Mar");
        assert_eq!(part(TimePart::DayOfWeek), "Tue");
        assert_eq!(part(TimePart::DayOfMonth), "5");
        assert_eq!(part(TimePart::DayOfYear), "65");
        assert_eq!(part(TimePart::HourOfDay), "0");
        assert_eq!(LabelUnit::Raw(None).raw_measure_text("Core", format).unwrap(), "Core");
        assert!(LabelUnit::Calendar(TimeUnit::Year).raw_measure_text("2024-03-05", None).is_err());
    }

    /// `labels.time_unit: none` measures the author's format, not the raw band:
    /// "Jan" clears twelve bands that "2024-01-01" would crowd into parity.
    #[test]
    fn the_none_layout_measures_the_authored_format() {
        let _locale = crate::time_locale::en_us_scope();
        let domain = months(12);
        let format = AuthoredTimeFormat::parse("line", Some("%b")).unwrap();
        let layout = |authored| {
            let mut measure = |s: &str| Ok(s.len() as f64 * 6.0);
            resolve_vega_x_labels(
                &domain, TimeUnit::YearMonth, Some("none"), 300.0, false, true, 1, true, false, None, 11.0, authored, None,
                &mut measure,
            )
            .unwrap()
        };
        assert!(!layout(format).parity);
        assert!(layout(None).parity);
    }

    // ── the authored format in the cadence ladder ───────────────────────────

    /// 36 months on a 400px plot (11px bands): "Jan 2022" (8 chars at 7px) does
    /// not clear at the monthly or quarterly rung, so the ladder loops to the
    /// yearly openers and paints the author's text there — not the bare year
    /// the built-in vocabulary promotes to (`cadence_label_text`).
    #[test]
    fn an_authored_format_is_measured_by_the_ladder_and_painted_at_the_cadence_it_lands_on() {
        let _locale = crate::time_locale::en_us_scope();
        let domain = month_domain(2022, 36);
        let (labels, vis) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearMonth,
            None,
            400.0,
            false,
            true,
            false,
            1,
            true,
            authored("%b %Y"),
            &mut char_measure,
        )
        .unwrap();
        assert_eq!(vis, TimeUnit::Year);
        let rows: Vec<(&str, &str)> = labels.iter().map(|l| (l.row1.as_str(), l.row2.as_str())).collect();
        assert_eq!(rows, [("Jan 2022", ""), ("Jan 2023", ""), ("Jan 2024", "")]);
    }

    /// The same domain with no authored format stops at the quarterly rung,
    /// where "Jan" (21px) and the stacked year row (28px) clear 33px: the
    /// format changes the cadence only through the text it measures.
    #[test]
    fn the_built_in_vocabulary_clears_a_rung_the_authored_text_does_not() {
        let domain = month_domain(2022, 36);
        let (labels, vis) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearMonth,
            None,
            400.0,
            false,
            true,
            false,
            1,
            true,
            None,
            &mut char_measure,
        )
        .unwrap();
        assert_eq!(vis, TimeUnit::YearQuarter);
        assert_eq!(labels[0].row2, "2022");
    }

    /// `overlap.skip` off: the ladder never steps coarser (`allow_skip`), so the
    /// authored text paints at every month even where it collides.
    #[test]
    fn the_ladder_steps_coarser_only_under_overlap_skip() {
        let _locale = crate::time_locale::en_us_scope();
        let domain = month_domain(2022, 36);
        let (labels, vis) = resolve_temporal_labels(
            &domain, TimeUnit::YearMonth, None, 400.0, false, true, false, 1, false, authored("%b %Y"),
            &mut char_measure,
        )
        .unwrap();
        assert_eq!(vis, TimeUnit::YearMonth);
        assert_eq!(labels.len(), 36);
    }

    /// An authored format skips the native day/week candidates
    /// (`authored_format is None` gates both branches), so daily data enters
    /// the ladder at the day grain: 45 days at 20px bands never promote to
    /// month openers by structure, only by failing to clear.
    #[test]
    fn an_authored_format_on_daily_data_enters_the_ladder_at_the_day_grain() {
        let domain = iso_range((2024, 1, 1), 1, 45);
        let (labels, vis) = resolve_temporal_labels(
            &domain,
            TimeUnit::YearMonthDate,
            None,
            900.0,
            false,
            true,
            false,
            1,
            true,
            authored("%-d"),
            &mut char_measure,
        )
        .unwrap();
        // "31" is 14px: clears a 20px band, so every day paints.
        assert_eq!(vis, TimeUnit::YearMonthDate);
        assert_eq!(labels.len(), 45);
        assert!(labels.iter().all(|l| l.row2.is_empty()));
        assert_eq!(labels[31].row1, "1");
    }

    fn months(count: usize) -> Vec<String> {
        (0..count).map(|i| format!("{}-{:02}-01", 2024 + i / 12, i % 12 + 1)).collect()
    }

    fn first_rows(labels: &[TemporalLabel]) -> Vec<&str> {
        labels.iter().map(|l| l.row1.as_str()).collect()
    }

    /// 11px rows, 16px gap: a 24-month domain in 272px (band 11.3) clears at
    /// quarter openers, not months, and the year rides under the first.
    #[test]
    fn a_vertical_ladder_steps_from_months_to_quarter_openers() {
        let domain = months(24);
        let out = resolve_vertical_temporal_labels(&domain, TimeUnit::YearMonth, 272.0, 1, true, 11.0, 16.0, None).unwrap();
        assert!(out.fits);
        assert_eq!(first_rows(&out.labels), ["Jan", "Apr", "Jul", "Oct", "Jan", "Apr", "Jul", "Oct"]);
        assert_eq!(out.labels[0].row2, "2024");
        assert_eq!(out.labels[4].row2, "2025");
        assert_eq!(out.labels[1].row2, "");
    }

    /// Past the quarter rung the vocabulary promotes to the bare year, and a
    /// domain too short for even that reports it did not fit.
    #[test]
    fn a_vertical_ladder_ends_at_years_and_reports_a_collision() {
        let domain = months(37);
        let years = resolve_vertical_temporal_labels(&domain, TimeUnit::YearMonth, 260.0, 1, true, 11.0, 16.0, None).unwrap();
        assert!(years.fits);
        assert_eq!(first_rows(&years.labels), ["2024", "2025", "2026", "2027"]);
        let tight = resolve_vertical_temporal_labels(&domain, TimeUnit::YearMonth, 20.0, 1, true, 11.0, 16.0, None).unwrap();
        assert!(!tight.fits);
    }

    /// With skipping off the ladder never steps: every month paints.
    #[test]
    fn a_vertical_ladder_without_skip_keeps_every_label() {
        let domain = months(24);
        let out = resolve_vertical_temporal_labels(&domain, TimeUnit::YearMonth, 100.0, 1, false, 11.0, 16.0, None).unwrap();
        assert!(!out.fits);
        assert_eq!(out.labels.len(), 24);
    }

    /// Vega-Lite's `timeUnit: month` keeps the month and puts it in 2012; a
    /// `day` maps a Sunday to Jan 1 and the rest of the week after it.
    #[test]
    fn a_time_part_maps_an_instant_into_2012() {
        let day = |y, m, d| days_from_civil(y, m, d) as f64;
        assert_eq!(TimePart::MonthOfYear.instant(day(2024, 5, 17)), day(2012, 5, 1));
        assert_eq!(TimePart::DayOfWeek.instant(day(2024, 1, 21)), day(2012, 1, 1));
        assert_eq!(TimePart::DayOfWeek.instant(day(2024, 1, 15)), day(2012, 1, 2));
        assert_eq!(TimePart::DayOfMonth.instant(day(2023, 11, 28)), day(2012, 1, 28));
        assert_eq!(TimePart::DayOfYear.instant(day(2023, 3, 1)), day(2012, 1, 1) + 59.0);
        let clock = day(2024, 2, 4) + (14.0 * 3600.0 + 45.0 * 60.0) / 86_400.0;
        assert_eq!(TimePart::HourOfDay.instant(clock), day(2012, 1, 1) + 14.0 / 24.0);
    }

    fn part_texts(ticks: &[PartTick]) -> Vec<&str> {
        ticks.iter().map(|t| t.text.as_str()).collect()
    }

    /// Vega asks d3 for ten ticks, but `tickMinStep` (a month is 31 days)
    /// caps that at `floor(span / 31) + 1`: five over Feb..Jul, so the month rung.
    #[test]
    fn a_month_part_axis_ticks_month_starts_under_its_min_step() {
        let lo = days_from_civil(2012, 2, 1) as f64;
        let hi = days_from_civil(2012, 7, 1) as f64;
        let ticks = time_part_ticks((lo, hi), 455.0, TimePart::MonthOfYear, None, None).unwrap();
        assert_eq!(part_texts(&ticks), ["Feb", "Mar", "Apr", "May", "Jun", "Jul"]);
        assert_eq!(ticks[0].frac, 0.0);
        assert_eq!(ticks[5].frac, 1.0);
    }

    /// An hour part spans one hour here: the one-hour min step caps the ten
    /// ticks at two, which d3 answers with the 30-minute rung.
    #[test]
    fn an_hour_part_axis_over_one_hour_ticks_the_half_hour() {
        let lo = days_from_civil(2012, 1, 1) as f64 + 9.0 / 24.0;
        let ticks = time_part_ticks((lo, lo + 1.0 / 24.0), 445.0, TimePart::HourOfDay, None, None).unwrap();
        assert_eq!(part_texts(&ticks), ["09:00", "09:00", "10:00"]);
    }

    /// A day part has no `tickMinStep`; its default count is Vega's ten, so a
    /// four-day span ticks every 12 hours.
    #[test]
    fn a_weekday_part_axis_ticks_every_half_day() {
        let lo = days_from_civil(2012, 1, 1) as f64;
        let ticks = time_part_ticks((lo, lo + 4.0), 455.0, TimePart::DayOfWeek, None, None).unwrap();
        assert_eq!(part_texts(&ticks), ["Sun", "Sun", "Mon", "Mon", "Tue", "Tue", "Wed", "Wed", "Thu"]);
    }

    /// A number `tickCount` on a bucketed axis is capped by the grain's
    /// `tickMinStep` and its 2-day rung strides epoch days, not month dates.
    #[test]
    fn a_bucketed_count_is_capped_by_the_grain_min_step_and_strides_epoch_days() {
        let lo = days_from_civil(2024, 1, 1) as f64;
        let monthly = AuthoredTickCadence::Count(12).bucketed_interval(TimeUnit::YearMonth, (lo, lo + 152.0)).unwrap();
        assert_eq!(monthly, UtcTickInterval::Month(1));
        let daily = AuthoredTickCadence::Count(5).bucketed_interval(TimeUnit::YearMonthDate, (lo, lo + 9.0)).unwrap();
        assert_eq!(daily, UtcTickInterval::EpochDay(2));
        let days = interval_tick_days((lo as i64, lo as i64 + 9), daily);
        assert_eq!(days, [lo as i64 + 1, lo as i64 + 3, lo as i64 + 5, lo as i64 + 7, lo as i64 + 9]);
    }

    /// The crowding test measures one text per band of the part's vocabulary:
    /// every 15th is one band, and an authored format repaints its first date.
    #[test]
    fn a_part_axis_measures_one_text_per_band() {
        let dates: Vec<String> = ["2023-01-15", "2024-02-15", "2024-03-02"].map(String::from).to_vec();
        let texts = part_band_texts(&dates, TimePart::DayOfMonth, None, None).unwrap();
        assert_eq!(texts, ["15", "2"]);
        let authored = AuthoredTimeFormat::parse("scatter", Some("%d.%m.%Y")).unwrap();
        assert_eq!(part_band_texts(&dates, TimePart::DayOfMonth, None, authored).unwrap(), ["15.01.2023", "02.03.2024"]);
    }

    /// Under `labels.time_unit: none` every tick speaks in Vega-Lite's own
    /// per-grain format, an empty bucket's included.
    #[test]
    fn a_none_axis_labels_every_tick_in_the_grains_own_format() {
        let days = [days_from_civil(2024, 3, 1), days_from_civil(2024, 4, 1)];
        let labels = none_tick_labels(&days, TimeUnit::YearMonth, None).unwrap();
        assert_eq!(labels.iter().map(|l| l.row1.as_str()).collect::<Vec<_>>(), ["Mar 2024", "Apr 2024"]);
        assert!(labels.iter().all(|l| l.row2.is_empty()));
    }

    /// `complete_ordinal_time_series` crosses the buckets with each series: a
    /// month one series lacks gets a null row for it, bucket-major with the
    /// series ascending, and the other series keep their rows.
    #[test]
    fn a_gap_fill_with_a_series_field_synthesizes_each_series_missing_slot() {
        let row = |x: &str, series: &str, v: f64| {
            serde_json::json!({"x": x, "seg": series, "v": v}).as_object().unwrap().clone()
        };
        let rows = [row("2024-01-01", "b", 1.0), row("2024-02-01", "a", 2.0), row("2024-03-01", "a", 3.0), row("2024-03-01", "b", 4.0)];
        let filled = complete_ordinal_rows(&rows, "x", &["seg"], TimeUnit::YearMonth, "null", 1).unwrap();
        let shape: Vec<(String, String, serde_json::Value)> = filled
            .iter()
            .map(|r| (r["x"].as_str().unwrap().to_string(), r["seg"].as_str().unwrap().to_string(), r["v"].clone()))
            .collect();
        let null = serde_json::Value::Null;
        let at = |x: &str, seg: &str, v: serde_json::Value| (x.to_string(), seg.to_string(), v);
        assert_eq!(
            shape,
            [
                at("2024-01-01", "a", null.clone()),
                at("2024-01-01", "b", serde_json::json!(1.0)),
                at("2024-02-01", "a", serde_json::json!(2.0)),
                at("2024-02-01", "b", null),
                at("2024-03-01", "a", serde_json::json!(3.0)),
                at("2024-03-01", "b", serde_json::json!(4.0)),
            ]
        );
    }

    /// A date off its bucket's start is keyed by the bucket that holds it;
    /// a date on a start, and a week (anchored by the data), are left alone.
    #[test]
    fn a_date_off_its_bucket_start_is_keyed_by_the_enclosing_bucket() {
        assert_eq!(off_anchor_bucket_start("2024-01-31", TimeUnit::YearMonth, 1).as_deref(), Some("2024-01-01"));
        assert_eq!(off_anchor_bucket_start("2024-01-01", TimeUnit::Year, 4).as_deref(), Some("2023-04-01"));
        assert_eq!(off_anchor_bucket_start("2024-04-01", TimeUnit::Year, 4), None);
        assert_eq!(off_anchor_bucket_start("2024-01-31", TimeUnit::YearWeek, 1), None);
    }

    /// A bool series field is ordered like the ints Python reads it as: false,
    /// true, then a null last.
    #[test]
    fn a_gap_fill_orders_a_bool_series_field_false_true_null() {
        let row = |x: &str, seg: serde_json::Value| serde_json::json!({"x": x, "seg": seg, "v": 1.0}).as_object().unwrap().clone();
        let rows = [row("2024-01-01", serde_json::json!(true)), row("2024-01-01", serde_json::json!(false)), row("2024-01-01", serde_json::Value::Null), row("2024-03-01", serde_json::json!(true))];
        let filled = complete_ordinal_rows(&rows, "x", &["seg"], TimeUnit::YearMonth, "null", 1).unwrap();
        let first: Vec<&serde_json::Value> = filled.iter().take(3).map(|r| &r["seg"]).collect();
        assert_eq!(first, [&serde_json::json!(false), &serde_json::json!(true), &serde_json::Value::Null]);
        assert_eq!(filled.len(), 9);
    }
}
