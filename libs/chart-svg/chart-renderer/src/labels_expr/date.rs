//! JavaScript `Date` arithmetic (local time is the board zone over aware instants, else UTC), d3-time-format's
//! en-US `timeFormat`/`timeParse`, and the d3-time interval helpers vega-time builds on.

pub const MS_PER_DAY: f64 = 86_400_000.0;
const MAX_TIME: f64 = 8.64e15;

/// What `Date.prototype.toString` prints: the engine's own English, whatever the board's time locale.
const JS_SHORT_DAYS: [&str; 7] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const JS_SHORT_MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

use super::val::{Bounded, EvalError, Res, Text};
use crate::time_locale::TimeLocale;

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

/// Days since the epoch of the civil date `y-m-d`: the one calendar law, `families::temporal`'s. A year past what
/// JavaScript's `Date` holds (about 275760) never gets here: its callers clip the instant first.
pub fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    crate::families::temporal::days_from_civil(y as i32, m as u8, d as u8)
}

pub fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let (y, m, d) = crate::families::temporal::civil_from_days(z);
    (i64::from(y), i64::from(m), i64::from(d))
}

pub fn breakdown(ms: f64) -> Option<Dt> {
    if !ms.is_finite() {
        return None;
    }
    let days = (ms / MS_PER_DAY).floor();
    let rest = (ms - days * MS_PER_DAY) as i64;
    let days = days as i64;
    let (year, month, day) = civil_from_days(days);
    Some(Dt {
        year,
        month: month - 1,
        day,
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

/// `TimeClip(MakeDate(MakeDay(y, m, d), MakeTime(h, mi, s, ms)))`, with the year as given.
pub fn make_time(y: f64, mo: f64, d: f64, h: f64, mi: f64, s: f64, ms: f64) -> f64 {
    if ![y, mo, d, h, mi, s, ms].iter().all(|v| v.is_finite()) {
        return f64::NAN;
    }
    let (y, mo, d) = (to_integer(y), to_integer(mo), to_integer(d));
    let ym = y + (mo / 12.0).floor();
    if ym.abs() > 400_000.0 {
        return f64::NAN;
    }
    let month = mo.rem_euclid(12.0) as i64;
    let day = days_from_civil(ym as i64, month + 1, 1) as f64 + d - 1.0;
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

fn pad(value: f64, fill: &str, width: usize) -> String {
    let neg = value < 0.0;
    let s = super::val::num_to_str(value.abs());
    let sign = if neg { "-" } else { "" };
    if s.len() < width { format!("{sign}{}{s}", fill.repeat(width - s.len())) } else { format!("{sign}{s}") }
}

fn js_pad(value: Option<i64>, fill: &str, width: usize) -> String {
    value.map_or_else(|| pad(f64::NAN, fill, width), |v| pad(v as f64, fill, width))
}

fn day_of_year(d: &Dt) -> i64 {
    days_from_civil(d.year, d.month + 1, d.day) - days_from_civil(d.year, 1, 1) + 1
}

/// d3 `timeSunday.count(year start - 1ms, d)` and its Monday twin: whole weeks started since the year began.
fn week_number(d: &Dt, first_weekday: i64) -> i64 {
    let day = days_from_civil(d.year, d.month + 1, d.day);
    let jan1 = days_from_civil(d.year, 1, 1);
    let floor_week = |t: i64| t - (t + 4 - first_weekday).rem_euclid(7);
    (floor_week(day) - floor_week(jan1 - 1)) / 7
}

/// The Thursday of d's ISO week (`dISO`).
fn iso_thursday(d: &Dt) -> (i64, i64) {
    let day = days_from_civil(d.year, d.month + 1, d.day);
    let wd = d.weekday;
    let thursday = if wd >= 4 || wd == 0 {
        // timeThursday(d): the Thursday on or before
        day - (wd - 4).rem_euclid(7)
    } else {
        // timeThursday.ceil(d)
        day + (4 - wd).rem_euclid(7)
    };
    let (y, _, _) = civil_from_days(thursday);
    (thursday, y)
}


/// Vega's multi-scale `timeFormat(date)` (no format string): the coarsest unit the instant sits on, by
/// vega-format's `timeMultiFormat`, with `overrides` replacing the per-unit specifiers.
pub fn multi_format(raw: f64, utc: bool, overrides: &[(String, String)], locale: &TimeLocale) -> Res<Text> {
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
pub fn time_format(spec: &str, ms: f64, utc: bool, locale: &TimeLocale) -> Res<Text> {
    super::val::charge(spec.len().saturating_mul(4))?;
    let chars: Vec<char> = spec.chars().collect();
    let shift = if utc { 0.0 } else { local_offset_ms(ms)? };
    let dt = breakdown(ms + shift);
    let mut out = Bounded::default();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] != '%' {
            out.push(chars[i])?;
            i += 1;
            continue;
        }
        i += 1;
        let Some(&c0) = chars.get(i) else { break };
        let (pad_fill, c) = match c0 {
            '-' => (Some(""), chars.get(i + 1).copied()),
            '_' => (Some(" "), chars.get(i + 1).copied()),
            '0' => (Some("0"), chars.get(i + 1).copied()),
            _ => (None, Some(c0)),
        };
        i += if pad_fill.is_some() { 2 } else { 1 };
        let Some(c) = c else { break };
        let fill = pad_fill.unwrap_or(if c == 'e' { " " } else { "0" });
        match directive(c, &dt, ms, shift, fill, utc, locale)? {
            Some(text) => out.push_str(&text)?,
            None => out.push(c)?,
        }
    }
    Ok(out.finish())
}

fn directive(c: char, dt: &Option<Dt>, ms: f64, shift: f64, fill: &str, utc: bool, locale: &TimeLocale) -> Res<Option<String>> {
    let name = |names: &[String], i: Option<i64>| i.map_or(String::new(), |i| names[i as usize].to_string());
    let d = dt.as_ref();
    Ok(Some(match c {
        'a' => name(&locale.short_days, d.map(|d| d.weekday)),
        'A' => name(&locale.days, d.map(|d| d.weekday)),
        'b' => name(&locale.short_months, d.map(|d| d.month)),
        'B' => name(&locale.months, d.map(|d| d.month)),
        'c' => time_format(&locale.date_time, ms, utc, locale)?.into_string(),
        'd' | 'e' => js_pad(d.map(|d| d.day), fill, 2),
        'f' => js_pad(d.map(|d| d.milli), fill, 3) + "000",
        'g' => js_pad(d.map(|d| iso_thursday(d).1 % 100), fill, 2),
        'G' => js_pad(d.map(|d| iso_thursday(d).1 % 10000), fill, 4),
        'H' => js_pad(d.map(|d| d.hour), fill, 2),
        'I' => js_pad(Some(d.map_or(12, |d| if d.hour % 12 == 0 { 12 } else { d.hour % 12 })), fill, 2),
        'j' => js_pad(d.map(day_of_year), fill, 3),
        'L' => js_pad(d.map(|d| d.milli), fill, 3),
        'm' => js_pad(d.map(|d| d.month + 1), fill, 2),
        'M' => js_pad(d.map(|d| d.minute), fill, 2),
        'p' => locale.periods[d.map_or(0, |d| usize::from(d.hour >= 12))].clone(),
        'q' => d.map_or("1".into(), |d| (1 + d.month / 3).to_string()),
        'Q' => super::val::num_to_str(ms),
        's' => super::val::num_to_str((ms / 1000.0).floor()),
        'S' => js_pad(d.map(|d| d.second), fill, 2),
        'u' => d.map_or("NaN".into(), |d| if d.weekday == 0 { 7 } else { d.weekday }.to_string()),
        'U' => js_pad(d.map(|d| week_number(d, 0)), fill, 2),
        'V' => js_pad(d.map(iso_week_number), fill, 2),
        'w' => d.map_or("NaN".into(), |d| d.weekday.to_string()),
        'W' => js_pad(d.map(|d| week_number(d, 1)), fill, 2),
        'x' => time_format(&locale.date, ms, utc, locale)?.into_string(),
        'X' => time_format(&locale.time, ms, utc, locale)?.into_string(),
        'y' => js_pad(d.map(|d| d.year % 100), fill, 2),
        'Y' => js_pad(d.map(|d| d.year % 10000), fill, 4),
        'Z' if d.is_some() => {
            let minutes = (shift / 60_000.0) as i64;
            let sign = if minutes < 0 { '-' } else { '+' };
            format!("{sign}{:02}{:02}", minutes.abs() / 60, minutes.abs() % 60)
        }
        'Z' => if utc { "+0000".into() } else { "+00NaN".into() },
        '%' => "%".into(),
        _ => return Ok(None),
    }))
}

fn iso_week_number(d: &Dt) -> i64 {
    let (thursday, year) = iso_thursday(d);
    let jan1 = days_from_civil(year, 1, 1);
    let jan1_wd = (jan1 + 4).rem_euclid(7);
    // d3: utcThursday.count(utcYear(d), d) + (utcYear(d).getUTCDay() === 4), where utcYear(d) is Jan 1 of the Thursday's year.
    let floor_thursday = |t: i64| t - (t + 4 - 4).rem_euclid(7) ;
    let count = (floor_thursday(thursday) - floor_thursday(jan1)) / 7;
    count + i64::from(jan1_wd == 4)
}

#[derive(Default)]
struct Parsed {
    y: Option<i64>,
    m: Option<i64>,
    d: Option<i64>,
    h: i64,
    mi: i64,
    s: i64,
    l: i64,
    p: Option<i64>,
    q: Option<i64>,
    w: Option<i64>,
    u: Option<i64>,
    uu: Option<i64>,
    ww: Option<i64>,
    vv: Option<i64>,
    z: Option<i64>,
    unix_ms: Option<i64>,
    unix_s: Option<i64>,
}

/// `utcParse(spec)(string)` / `timeParse(spec)(string)`: the instant, or `None` when the string does not fit.
/// `timeParse(spec)(text)` / `utcParse(spec)(text)`: a local parse with no `%Z`
/// reads its fields on the expression's local clock.
pub fn time_parse(spec: &str, text: &str, locale: &TimeLocale, utc: bool) -> Res<Option<f64>> {
    match parse_fields(spec, text, locale) {
        Some((wall, true)) if !utc => from_local_wall(wall).map(Some),
        parsed => Ok(parsed.map(|(ms, _)| ms)),
    }
}

/// The parsed instant, and whether it is a wall clock (no `%Z`, `%Q` or `%s`).
fn parse_fields(spec: &str, text: &str, locale: &TimeLocale) -> Option<(f64, bool)> {
    let spec_chars: Vec<char> = spec.chars().collect();
    let string: Vec<char> = text.chars().collect();
    let mut p = Parsed { y: Some(1900), d: Some(1), ..Parsed::default() };
    let end = parse_specifier(&mut p, &spec_chars, &string, 0, locale)?;
    if end != string.len() {
        return None;
    }
    if let Some(q) = p.unix_ms {
        return Some((q as f64, false));
    }
    if let Some(s) = p.unix_s {
        return Some(((s * 1000 + p.l) as f64, false));
    }
    if let Some(pm) = p.p {
        p.h = p.h % 12 + pm * 12;
    }
    if p.m.is_none() {
        p.m = Some(p.q.unwrap_or(0));
    }
    let year = p.y.unwrap_or(1900);
    let mut month = p.m.unwrap_or(0);
    let mut day = p.d.unwrap_or(1);
    if let Some(v) = p.vv {
        if !(1..=53).contains(&v) {
            return None;
        }
        let w = *p.w.get_or_insert(1);
        let jan1 = days_from_civil(year, 1, 1);
        let wd = (jan1 + 4).rem_euclid(7);
        let monday_on_or_before = jan1 - (wd + 6).rem_euclid(7);
        let week = if wd > 4 || wd == 0 { monday_on_or_before + 7 } else { monday_on_or_before } + (v - 1) * 7;
        let (y2, m2, d2) = civil_from_days(week);
        return Some((make_time(y2 as f64, (m2 - 1) as f64, (d2 + (w + 6) % 7) as f64, (p.h) as f64, p.mi as f64, p.s as f64, p.l as f64) - zone_ms(&p), p.z.is_none()));
    } else if p.ww.is_some() || p.uu.is_some() {
        let w = *p.w.get_or_insert(if let Some(u) = p.uu_or_u() { u % 7 } else if p.ww.is_some() { 1 } else { 0 });
        let jan1 = days_from_civil(year, 1, 1);
        let wd = (jan1 + 4).rem_euclid(7);
        month = 0;
        day = if let Some(ww) = p.ww { (w + 6) % 7 + ww * 7 - (wd + 5) % 7 } else { w + p.uu.unwrap_or(0) * 7 - (wd + 6) % 7 };
    }
    Some((make_time(year as f64, month as f64, day as f64, p.h as f64, p.mi as f64, p.s as f64, p.l as f64) - zone_ms(&p), p.z.is_none()))
}

fn zone_ms(p: &Parsed) -> f64 {
    // d3 adds `Z / 100 | 0` hours and `Z % 100` minutes to the fields, with Z holding the negated offset.
    p.z.map_or(0.0, |z| -((z / 100) as f64 * 3_600_000.0 + (z % 100) as f64 * 60_000.0))
}

impl Parsed {
    fn uu_or_u(&self) -> Option<i64> {
        self.u
    }
}

fn number_at(string: &[char], i: usize, width: usize) -> Option<(i64, usize)> {
    // /^\s*\d+/ over string.slice(i, i + width)
    let slice = &string[i.min(string.len())..(i + width).min(string.len())];
    let mut k = 0;
    while k < slice.len() && super::val::is_js_space(slice[k]) {
        k += 1;
    }
    let start = k;
    while k < slice.len() && slice[k].is_ascii_digit() {
        k += 1;
    }
    if k == start {
        return None;
    }
    let n: i64 = slice[start..k].iter().collect::<String>().parse().ok()?;
    Some((n, k))
}

fn name_at(string: &[char], i: usize, names: &[String]) -> Option<(i64, usize)> {
    let rest: String = string[i.min(string.len())..].iter().collect::<String>().to_lowercase();
    // The regex alternation takes the first name (in locale order) that is a prefix.
    names.iter().enumerate().find_map(|(k, n)| {
        let nl = n.to_lowercase();
        rest.starts_with(&nl).then(|| (k as i64, nl.chars().count()))
    })
}

fn parse_specifier(p: &mut Parsed, spec: &[char], string: &[char], mut j: usize, locale: &TimeLocale) -> Option<usize> {
    let mut i = 0;
    while i < spec.len() {
        if j >= string.len() {
            return None;
        }
        let c = spec[i];
        i += 1;
        if c == '%' {
            let mut d = *spec.get(i)?;
            i += 1;
            if matches!(d, '-' | '_' | '0') {
                d = *spec.get(i)?;
                i += 1;
            }
            j = parse_directive(p, d, string, j, locale)?;
        } else if string.get(j) != Some(&c) {
            return None;
        } else {
            j += 1;
        }
    }
    Some(j)
}

fn parse_directive(p: &mut Parsed, d: char, string: &[char], i: usize, locale: &TimeLocale) -> Option<usize> {
    let num = |width: usize| number_at(string, i, width);
    Some(match d {
        'a' => {
            let (k, n) = name_at(string, i, &locale.short_days)?;
            p.w = Some(k);
            i + n
        }
        'A' => {
            let (k, n) = name_at(string, i, &locale.days)?;
            p.w = Some(k);
            i + n
        }
        'b' => {
            let (k, n) = name_at(string, i, &locale.short_months)?;
            p.m = Some(k);
            i + n
        }
        'B' => {
            let (k, n) = name_at(string, i, &locale.months)?;
            p.m = Some(k);
            i + n
        }
        'c' => parse_specifier(p, &locale.date_time.chars().collect::<Vec<_>>(), string, i, locale)?,
        'x' => parse_specifier(p, &locale.date.chars().collect::<Vec<_>>(), string, i, locale)?,
        'X' => parse_specifier(p, &locale.time.chars().collect::<Vec<_>>(), string, i, locale)?,
        'd' | 'e' => {
            let (n, k) = num(2)?;
            p.d = Some(n);
            i + k
        }
        'f' => {
            let (n, k) = num(6)?;
            p.l = n / 1000;
            i + k
        }
        'g' => {
            let (n, k) = num(2)?;
            p.y = Some(n + if n > 68 { 1900 } else { 2000 });
            i + k
        }
        'G' | 'Y' => {
            let (n, k) = num(4)?;
            p.y = Some(n);
            i + k
        }
        'H' | 'I' => {
            let (n, k) = num(2)?;
            p.h = n;
            i + k
        }
        'j' => {
            let (n, k) = num(3)?;
            p.m = Some(0);
            p.d = Some(n);
            i + k
        }
        'L' => {
            let (n, k) = num(3)?;
            p.l = n;
            i + k
        }
        'm' => {
            let (n, k) = num(2)?;
            p.m = Some(n - 1);
            i + k
        }
        'M' => {
            let (n, k) = num(2)?;
            p.mi = n;
            i + k
        }
        'p' => {
            let (k, n) = name_at(string, i, &locale.periods)?;
            p.p = Some(k);
            i + n
        }
        'q' => {
            let (n, k) = num(1)?;
            p.q = Some(n * 3 - 3);
            i + k
        }
        'Q' => {
            let (n, k) = number_at(string, i, usize::MAX / 2)?;
            p.unix_ms = Some(n);
            i + k
        }
        's' => {
            let (n, k) = number_at(string, i, usize::MAX / 2)?;
            p.unix_s = Some(n);
            i + k
        }
        'S' => {
            let (n, k) = num(2)?;
            p.s = n;
            i + k
        }
        'u' => {
            let (n, k) = num(1)?;
            p.u = Some(n);
            i + k
        }
        'U' => {
            let (n, k) = num(2)?;
            p.uu = Some(n);
            i + k
        }
        'V' => {
            let (n, k) = num(2)?;
            p.vv = Some(n);
            i + k
        }
        'w' => {
            let (n, k) = num(1)?;
            p.w = Some(n);
            i + k
        }
        'W' => {
            let (n, k) = num(2)?;
            p.ww = Some(n);
            i + k
        }
        'y' => {
            let (n, k) = num(2)?;
            p.y = Some(n + if n > 68 { 1900 } else { 2000 });
            i + k
        }
        'Z' => {
            let rest: String = string[i..(i + 6).min(string.len())].iter().collect();
            if rest.starts_with('Z') {
                p.z = Some(0);
                i + 1
            } else {
                let b = rest.as_bytes();
                if b.len() >= 3 && matches!(b[0], b'+' | b'-') && b[1].is_ascii_digit() && b[2].is_ascii_digit() {
                    let mut len = 3;
                    let mut mm = "00".to_string();
                    let tail = &rest[3..];
                    let tail = tail.strip_prefix(':').unwrap_or(tail);
                    if tail.len() >= 2 && tail.as_bytes()[..2].iter().all(u8::is_ascii_digit) {
                        mm = tail[..2].to_string();
                        len = rest.len() - tail.len() + 2;
                    }
                    let v: i64 = format!("{}{mm}", &rest[1..3]).parse().ok()?;
                    p.z = Some(-(if b[0] == b'-' { -v } else { v }));
                    i + len
                } else {
                    return None;
                }
            }
        }
        '%' => {
            if string.get(i) == Some(&'%') {
                i + 1
            } else {
                return None;
            }
        }
        _ => return None,
    })
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
        let day_start = |y, m, dd| days_from_civil(y, m + 1, dd) as f64 * MS_PER_DAY;
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
        let time = ms - (days_from_civil(d.year, d.month + 1, d.day) as f64 * MS_PER_DAY);
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
        time_clip_keep(self.offset_once(ms, step))
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

fn time_clip_keep(t: f64) -> f64 {
    time_clip(t)
}

/// vega-time `utcdayofyear`.
pub fn day_of_year_vega(ms: f64) -> f64 {
    let Some(d) = breakdown(ms) else { return f64::NAN };
    let y = days_from_civil(d.year, 1, 1) as f64 * MS_PER_DAY;
    Unit::Date.count(y - 1.0, ms)
}

/// vega-time `utcweek`.
pub fn week_vega(ms: f64) -> f64 {
    let Some(d) = breakdown(ms) else { return f64::NAN };
    let y = days_from_civil(d.year, 1, 1) as f64 * MS_PER_DAY;
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
    fn time_format_covers_the_directives() {
        let t = make_time(2020.0, 0.0, 2.0, 15.0, 4.0, 5.0, 7.0);
        for (spec, want) in [
            ("%Y-%m-%d", "2020-01-02"),
            ("%a %A %b %B", "Thu Thursday Jan January"),
            ("%-d/%-m/%y %e", "2/1/20  2"),
            ("%H:%M:%S.%L %p %I", "15:04:05.007 PM 03"),
            ("%j %U %W %V %G %g %u %w", "002 00 00 01 2020 20 4 4"),
            ("%q %Z %% %Q %s", "1 +0000 % 1577977445007 1577977445"),
            ("%c", "1/2/2020, 3:04:05 PM"),
            ("%x | %X", "1/2/2020 | 3:04:05 PM"),
            ("%_d|%0d|%-H", " 2|02|15"),
            ("%f %k", "007000 k"),
        ] {
            assert_eq!(time_format(spec, t, false, &crate::time_locale::en_us()).unwrap(), want, "{spec}");
        }
        assert_eq!(time_format("%b %Y", f64::NAN, false, &crate::time_locale::en_us()).unwrap(), " 0NaN");
    }

    #[test]
    fn week_numbers_match_d3_around_new_year() {
        let at = |y: f64, m: f64, d: f64| make_time(y, m, d, 0.0, 0.0, 0.0, 0.0);
        assert_eq!(time_format("%V %G", at(2021.0, 0.0, 3.0), false, &crate::time_locale::en_us()).unwrap(), "53 2020");
        assert_eq!(time_format("%V %G", at(2018.0, 11.0, 31.0), false, &crate::time_locale::en_us()).unwrap(), "01 2019");
        assert_eq!(time_format("%U %W", at(2021.0, 0.0, 3.0), false, &crate::time_locale::en_us()).unwrap(), "01 00");
    }

    #[test]
    fn time_parse_reads_what_time_format_wrote() {
        let t = make_time(2020.0, 0.0, 2.0, 15.0, 4.0, 5.0, 0.0);
        assert_eq!(time_parse("%Y-%m-%d %H:%M:%S", "2020-01-02 15:04:05", &crate::time_locale::en_us(), false).unwrap(), Some(t));
        assert_eq!(time_parse("%b %d, %Y", "Jan 02, 2020", &crate::time_locale::en_us(), false).unwrap(), Some(make_time(2020.0, 0.0, 2.0, 0.0, 0.0, 0.0, 0.0)));
        assert_eq!(time_parse("%Y-%m-%d", "2020-01-02x", &crate::time_locale::en_us(), false).unwrap(), None);
        assert_eq!(time_parse("%Y-%m-%d", "nope", &crate::time_locale::en_us(), false).unwrap(), None);
        assert_eq!(time_parse("%I:%M %p", "3:04 PM", &crate::time_locale::en_us(), false).unwrap().map(|v| v.rem_euclid(MS_PER_DAY)), Some(15.0 * 3_600_000.0 + 4.0 * 60_000.0));
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
