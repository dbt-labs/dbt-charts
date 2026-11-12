//! d3-time-format 4.1.0: `locale(definition).format(spec)` and `.parse(spec)`, plus d3-scale's multi-scale
//! tick format, over epoch milliseconds (a JS `Date`'s value; NaN is Invalid Date).
//!
//! d3 formats a `Date`'s local fields (`timeFormat`) or UTC fields (`utcFormat`). Here the caller resolves the
//! zone: a local clock is `ms + offset_ms` with the offset the zone has at `ms` (0 for UTC; NaN for an Invalid
//! Date, as `getTimezoneOffset` is), and a local parse returns the wall clock for the caller to resolve in its
//! zone. A locale pattern that expands into itself overflows d3's stack; here it is an error.

use crate::invariant::{self, Invariant, at};

use serde::Deserialize;

use crate::calendar::{breakdown, civil_from_days, days_from_civil, make_time, time_clip, Dt};
use crate::js_math::{is_js_space, js_number_string};

/// How deep `%c`, `%x` and `%X` may nest. A real locale needs three (`%c` -> `%x` -> text).
const MAX_DEPTH: usize = 8;

/// A d3 `timeFormatLocale` definition.
#[derive(Clone, Debug, PartialEq, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Locale {
    pub date_time: String,
    pub date: String,
    pub time: String,
    pub periods: [String; 2],
    /// Sunday first, as d3 lists them.
    pub days: [String; 7],
    pub short_days: [String; 7],
    pub months: [String; 12],
    pub short_months: [String; 12],
}

// d3-time-format/src/locale.js `pad`: JavaScript number-to-string, sign first, zero fill to `width`.
fn pad(value: f64, fill: &str, width: usize) -> String {
    let (sign, magnitude) = if value < 0.0 { ("-", -value) } else { ("", value) };
    let digits = js_number_string(magnitude);
    let missing = width.saturating_sub(digits.len());
    format!("{sign}{}{digits}", fill.repeat(missing))
}

/// The instant being formatted: its value, the zone offset, and the clock fields (`None` for Invalid Date).
struct Moment {
    ms: f64,
    offset_ms: f64,
    wall: Option<Dt>,
}

impl Moment {
    fn field(&self, f: impl Fn(&Dt) -> i64) -> f64 {
        self.wall.as_ref().map_or(f64::NAN, |d| f(d) as f64)
    }

    fn day_number(d: &Dt) -> i64 {
        days_from_civil(d.year as i32, d.month as u8 + 1, d.day as u8)
    }

    /// d3's `dISO`: the Thursday of the date's ISO week, as a day number.
    fn iso_thursday(d: &Dt) -> i64 {
        let day = Self::day_number(d);
        if d.weekday >= 4 || d.weekday == 0 {
            day - (d.weekday - 4).rem_euclid(7)
        } else {
            day + (4 - d.weekday).rem_euclid(7)
        }
    }

    fn iso_year(d: &Dt) -> i64 {
        i64::from(civil_from_days(Self::iso_thursday(d)).0)
    }

    /// `interval.count(year start - 1, d)` of the week interval starting on `first_weekday` (0 = Sunday): the
    /// weeks begun since the year began.
    fn weeks_since_year_start(d: &Dt, first_weekday: i64) -> i64 {
        let floor_week = |day: i64| day - (day + 4 - first_weekday).rem_euclid(7);
        let jan1 = days_from_civil(d.year as i32, 1, 1);
        (floor_week(Self::day_number(d)) - floor_week(jan1 - 1)) / 7
    }

    fn iso_week(d: &Dt) -> i64 {
        let thursday = Self::iso_thursday(d);
        let jan1 = days_from_civil(civil_from_days(thursday).0, 1, 1);
        let floor_thursday = |day: i64| day - day.rem_euclid(7);
        (floor_thursday(thursday) - floor_thursday(jan1)) / 7 + i64::from((jan1 + 4).rem_euclid(7) == 4)
    }
}

/// `locale.format(spec)(date)` / `utcFormat`: the text of `ms` on the clock `offset_ms` ahead of UTC. Like
/// `newFormat`'s `new Date(+date)`, `ms` is clipped to a whole millisecond within a `Date`'s range, else Invalid Date.
pub fn format(spec: &str, ms: f64, offset_ms: f64, locale: &Locale) -> Result<String, String> {
    let ms = time_clip(ms);
    let moment = Moment { ms, offset_ms, wall: breakdown(ms + offset_ms) };
    let mut out = String::new();
    expand(spec, &moment, locale, 0, &mut out)?;
    Ok(out)
}

// d3-time-format/src/locale.js `newFormat`.
fn expand(spec: &str, moment: &Moment, locale: &Locale, depth: usize, out: &mut String) -> Result<(), String> {
    if depth > MAX_DEPTH {
        return Err(format!("the time locale's %c, %x and %X patterns expand into themselves: {spec:?}"));
    }
    let mut chars = spec.chars();
    while let Some(c) = chars.next() {
        if c != '%' {
            out.push(c);
            continue;
        }
        let mut directive = chars.next();
        let fill = match directive {
            Some('-') => Some(""),
            Some('_') => Some(" "),
            Some('0') => Some("0"),
            _ => None,
        };
        if fill.is_some() {
            directive = chars.next();
        }
        let Some(directive) = directive else { break };
        let fill = fill.unwrap_or(if directive == 'e' { " " } else { "0" });
        if !format_directive(directive, fill, moment, locale, depth, out)? {
            out.push(directive);
        }
    }
    Ok(())
}

/// One directive's text appended to `out`; false for a character d3 has no formatter for.
fn format_directive(c: char, fill: &str, m: &Moment, locale: &Locale, depth: usize, out: &mut String) -> Result<bool, String> {
    let number = |value: f64, width: usize| pad(value, fill, width);
    let name = |names: &[String], index: f64| if index.is_nan() { "" } else { at(names, index as usize).as_str() }.to_string();
    let text = match c {
        'a' => name(&locale.short_days, m.field(|d| d.weekday)),
        'A' => name(&locale.days, m.field(|d| d.weekday)),
        'b' => name(&locale.short_months, m.field(|d| d.month)),
        'B' => name(&locale.months, m.field(|d| d.month)),
        'c' | 'x' | 'X' => {
            let pattern = match c {
                'c' => &locale.date_time,
                'x' => &locale.date,
                _ => &locale.time,
            };
            return expand(pattern, m, locale, depth + 1, out).map(|()| true);
        }
        'd' | 'e' => number(m.field(|d| d.day), 2),
        'f' => number(m.field(|d| d.milli), 3) + "000",
        'g' => number(m.field(|d| Moment::iso_year(d) % 100), 2),
        'G' => number(m.field(|d| Moment::iso_year(d) % 10_000), 4),
        'H' => number(m.field(|d| d.hour), 2),
        'I' => number(m.wall.as_ref().map_or(12.0, |d| if d.hour % 12 == 0 { 12.0 } else { (d.hour % 12) as f64 }), 2),
        'j' => number(m.field(|d| 1 + Moment::day_number(d) - days_from_civil(d.year as i32, 1, 1)), 3),
        'L' => number(m.field(|d| d.milli), 3),
        'm' => number(m.field(|d| d.month + 1), 2),
        'M' => number(m.field(|d| d.minute), 2),
        'p' => at(&locale.periods, usize::from(m.wall.as_ref().is_some_and(|d| d.hour >= 12))).clone(),
        'q' => js_number_string(m.wall.as_ref().map_or(1.0, |d| (1 + d.month / 3) as f64)),
        'Q' => js_number_string(m.ms),
        's' => js_number_string((m.ms / 1000.0).floor()),
        'S' => number(m.field(|d| d.second), 2),
        'u' => js_number_string(m.field(|d| if d.weekday == 0 { 7 } else { d.weekday })),
        'U' => number(m.field(|d| Moment::weeks_since_year_start(d, 0)), 2),
        'V' => number(m.field(Moment::iso_week), 2),
        'w' => js_number_string(m.field(|d| d.weekday)),
        'W' => number(m.field(|d| Moment::weeks_since_year_start(d, 1)), 2),
        'y' => number(m.field(|d| d.year % 100), 2),
        'Y' => number(m.field(|d| d.year % 10_000), 4),
        'Z' => zone(m.offset_ms),
        '%' => "%".to_string(),
        _ => return Ok(false),
    };
    out.push_str(&text);
    Ok(true)
}

// `formatZone`: `getTimezoneOffset` is whole minutes, V8's integer division truncating toward zero.
fn zone(offset_ms: f64) -> String {
    let behind = (-offset_ms / 60_000.0).trunc();
    let (sign, minutes) = if behind > 0.0 { ("-", behind) } else { ("+", -behind) };
    let hours = if minutes.is_nan() { 0.0 } else { (minutes / 60.0).trunc() };
    format!("{sign}{}{}", pad(hours, "0", 2), pad(minutes % 60.0, "0", 2))
}

/// d3-scale/src/time.js `calendar`'s `tickFormat(date)`: the coarsest unit the instant sits on picks the
/// specifier. The units floor on the clock fields (`ms + offset_ms`), so a local midnight a transition skipped
/// reads as the hour it became.
pub fn multi_format(ms: f64, offset_ms: f64, locale: &Locale) -> Result<String, String> {
    let spec = match breakdown(time_clip(ms) + offset_ms) {
        Some(d) if d.milli != 0 => ".%L",
        Some(d) if d.second != 0 => ":%S",
        Some(d) if d.minute != 0 => "%I:%M",
        Some(d) if d.hour != 0 => "%I %p",
        Some(d) if d.day != 1 && d.weekday != 0 => "%a %d",
        Some(d) if d.day != 1 => "%b %d",
        Some(d) if d.month != 0 => "%B",
        _ => "%Y",
    };
    format(spec, ms, offset_ms, locale)
}

/// A parsed date: `ms` is the instant, or when `wall` the clock fields read as UTC, for the caller to resolve
/// in its zone. A text that names its own zone or instant (`%Z`, `%Q`, `%s`) and any `utcParse` is not a wall.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Parsed {
    pub ms: f64,
    pub wall: bool,
}

/// d3's `newDate` plus the fields a directive sets (`"Q" in d`: `Some`).
struct Fields {
    y: f64,
    m: Option<f64>,
    d: f64,
    h: f64,
    min: f64,
    s: f64,
    l: f64,
    p: Option<f64>,
    q: Option<f64>,
    w: Option<f64>,
    u: Option<f64>,
    week_sunday: Option<f64>,
    week_iso: Option<f64>,
    week_monday: Option<f64>,
    zone: Option<f64>,
    unix_ms: Option<f64>,
    unix_s: Option<f64>,
}

/// `locale.parse(spec)(text)` / `utcParse` (`utc`): `None` where d3 returns null.
pub fn parse(spec: &str, text: &str, locale: &Locale, utc: bool) -> Result<Option<Parsed>, String> {
    let spec: Vec<char> = spec.chars().collect();
    let string: Vec<char> = text.chars().collect();
    let mut f = Fields {
        y: 1900.0,
        m: None,
        d: 1.0,
        h: 0.0,
        min: 0.0,
        s: 0.0,
        l: 0.0,
        p: None,
        q: None,
        w: None,
        u: None,
        week_sunday: None,
        week_iso: None,
        week_monday: None,
        zone: None,
        unix_ms: None,
        unix_s: None,
    };
    if parse_specifier(&mut f, &spec, &string, 0, locale, 0)? != Some(string.len()) {
        return Ok(None);
    }
    if let Some(q) = f.unix_ms {
        return Ok(Some(Parsed { ms: time_clip(q), wall: false }));
    }
    if let Some(s) = f.unix_s {
        return Ok(Some(Parsed { ms: time_clip(s * 1000.0 + f.l), wall: false }));
    }
    if utc && f.zone.is_none() {
        f.zone = Some(0.0);
    }
    if let Some(p) = f.p {
        f.h = f.h % 12.0 + p * 12.0;
    }
    let mut month = f.m.unwrap_or_else(|| f.q.unwrap_or(0.0));
    let mut day = f.d;
    let jan1_weekday = |year: f64| (days_from_civil(year as i32, 1, 1) + 4).rem_euclid(7) as f64;
    if let Some(v) = f.week_iso {
        if !(1.0..=53.0).contains(&v) {
            return Ok(None);
        }
        let w = *f.w.get_or_insert(1.0);
        let jan1 = days_from_civil(f.y as i32, 1, 1);
        let weekday = jan1_weekday(f.y);
        let monday_on_or_before = jan1 - (weekday as i64 + 6) % 7;
        let first_week = if weekday > 4.0 || weekday == 0.0 { monday_on_or_before + 7 } else { monday_on_or_before };
        let (year, m, d) = civil_from_days(first_week + (v as i64 - 1) * 7);
        f.y = f64::from(year);
        month = f64::from(m) - 1.0;
        day = f64::from(d) + (w + 6.0) % 7.0;
    } else if f.week_monday.is_some() || f.week_sunday.is_some() {
        let w = *f.w.get_or_insert(match (f.u, f.week_monday) {
            (Some(u), _) => u % 7.0,
            (None, Some(_)) => 1.0,
            (None, None) => 0.0,
        });
        let weekday = jan1_weekday(f.y);
        month = 0.0;
        day = match (f.week_monday, f.week_sunday) {
            (Some(week), _) => (w + 6.0) % 7.0 + week * 7.0 - (weekday + 5.0) % 7.0,
            (None, Some(week)) => w + week * 7.0 - (weekday + 6.0) % 7.0,
            (None, None) => invariant::broken("the branch holds a week number"),
        };
    }
    // A named zone makes every field UTC, offset by the zone: Z holds the negated offset as ±hhmm.
    let (hour, minute) = f.zone.map_or((f.h, f.min), |z| (f.h + (z / 100.0).trunc(), f.min + z % 100.0));
    Ok(Some(Parsed { ms: date_of(f.y, month, day, hour, minute, f.s, f.l), wall: f.zone.is_none() }))
}

/// d3's `utcDate`: `Date.UTC`, except that the years 0-99 stay themselves, which d3 does by building the
/// date in year -1 and then setting the year.
fn date_of(y: f64, m: f64, d: f64, h: f64, min: f64, s: f64, l: f64) -> f64 {
    if !(0.0..100.0).contains(&y) {
        return make_time(y, m, d, h, min, s, l);
    }
    match breakdown(make_time(-1.0, m, d, h, min, s, l)) {
        Some(t) => make_time(y, t.month as f64, t.day as f64, t.hour as f64, t.minute as f64, t.second as f64, t.milli as f64),
        None => f64::NAN,
    }
}

// d3-time-format/src/locale.js `parseSpecifier`: the index after the match, or None for d3's -1.
fn parse_specifier(f: &mut Fields, spec: &[char], string: &[char], mut j: usize, locale: &Locale, depth: usize) -> Result<Option<usize>, String> {
    if depth > MAX_DEPTH {
        return Err(format!("the time locale's %c, %x and %X patterns expand into themselves: {:?}", spec.iter().collect::<String>()));
    }
    let mut i = 0;
    while i < spec.len() {
        if j >= string.len() {
            return Ok(None);
        }
        let c = *at(spec, i);
        i += 1;
        if c == '%' {
            let Some(&(mut directive)) = spec.get(i) else { return Ok(None) };
            i += 1;
            if matches!(directive, '-' | '_' | '0') {
                let Some(&next) = spec.get(i) else { return Ok(None) };
                directive = next;
                i += 1;
            }
            match parse_directive(f, directive, string, j, locale, depth)? {
                Some(end) => j = end,
                None => return Ok(None),
            }
        } else if *at(string, j) != c {
            return Ok(None);
        } else {
            j += 1;
        }
    }
    Ok(Some(j))
}

/// `numberRe`, `/^\s*\d+/`, over `string.slice(i, i + width)`: the number and the characters it consumed.
fn number_at(string: &[char], i: usize, width: usize) -> Option<(f64, usize)> {
    let slice = at(string, i.min(string.len())..i.saturating_add(width).min(string.len()));
    let start = slice.iter().take_while(|c| is_js_space(**c)).count();
    let digits = at(slice, start..).iter().take_while(|c| c.is_ascii_digit()).count();
    if digits == 0 {
        return None;
    }
    let value = at(slice, start..start + digits).iter().collect::<String>().parse().held("digits parse as a number");
    Some((value, start + digits))
}

/// A name regex (`^(?:a|b|…)`, case-insensitive: the first listed name that is a prefix) and its lookup, whose
/// `Map` keeps the last index of a name repeated in lowercase.
fn name_at(string: &[char], i: usize, names: &[String]) -> Option<(f64, usize)> {
    let rest: String = at(string, i.min(string.len())..).iter().collect::<String>().to_lowercase();
    let lowered: Vec<String> = names.iter().map(|n| n.to_lowercase()).collect();
    let first = lowered.iter().position(|n| rest.starts_with(n.as_str()))?;
    let last = lowered.iter().rposition(|n| *n == *at(&lowered, first)).held("the first match is a candidate");
    Some((last as f64, at(&lowered, first).chars().count()))
}

// `parseZone`: `/^(Z)|([+-]\d\d)(?::?(\d\d))?/` over `slice(i, i + 6)`. Only the first alternative is
// anchored, so the second matches wherever it first fits, and d3 advances by the match's length alone.
fn zone_at(string: &[char], i: usize) -> Option<(f64, usize)> {
    let slice = at(string, i.min(string.len())..(i + 6).min(string.len()));
    let digits = |at: usize| slice.get(at..at + 2).filter(|pair| pair.iter().all(char::is_ascii_digit));
    for start in 0..slice.len() {
        if start == 0 && *at(slice, 0) == 'Z' {
            return Some((0.0, 1));
        }
        let (Some('+' | '-'), Some(hours)) = (slice.get(start).copied(), digits(start + 1)) else { continue };
        let colon = usize::from(slice.get(start + 3) == Some(&':'));
        let (minutes, len) = match digits(start + 3 + colon) {
            Some(minutes) => (minutes, 5 + colon),
            None => (&['0', '0'][..], 3),
        };
        let magnitude: f64 = hours.iter().chain(minutes).collect::<String>().parse().held("four digits");
        return Some((if *at(slice, start) == '-' { magnitude } else { -magnitude }, len));
    }
    None
}

/// A numeric directive: up to `width` characters, stored by `set`.
fn number(f: &mut Fields, string: &[char], i: usize, width: usize, set: impl FnOnce(&mut Fields, f64)) -> Option<usize> {
    let (n, len) = number_at(string, i, width)?;
    set(f, n);
    Some(i + len)
}

fn named(f: &mut Fields, string: &[char], i: usize, names: &[String], set: impl FnOnce(&mut Fields, f64)) -> Option<usize> {
    let (index, len) = name_at(string, i, names)?;
    set(f, index);
    Some(i + len)
}

fn parse_directive(f: &mut Fields, c: char, string: &[char], i: usize, locale: &Locale, depth: usize) -> Result<Option<usize>, String> {
    let year = |n: f64| n + if n > 68.0 { 1900.0 } else { 2000.0 };
    Ok(match c {
        'a' => named(f, string, i, &locale.short_days, |f, n| f.w = Some(n)),
        'A' => named(f, string, i, &locale.days, |f, n| f.w = Some(n)),
        'b' => named(f, string, i, &locale.short_months, |f, n| f.m = Some(n)),
        'B' => named(f, string, i, &locale.months, |f, n| f.m = Some(n)),
        'c' | 'x' | 'X' => {
            let pattern = match c {
                'c' => &locale.date_time,
                'x' => &locale.date,
                _ => &locale.time,
            };
            return parse_specifier(f, &pattern.chars().collect::<Vec<_>>(), string, i, locale, depth + 1);
        }
        'd' | 'e' => number(f, string, i, 2, |f, n| f.d = n),
        'f' => number(f, string, i, 6, |f, n| f.l = (n / 1000.0).floor()),
        'g' | 'y' => number(f, string, i, 2, |f, n| f.y = year(n)),
        'G' | 'Y' => number(f, string, i, 4, |f, n| f.y = n),
        'H' | 'I' => number(f, string, i, 2, |f, n| f.h = n),
        'j' => number(f, string, i, 3, |f, n| {
            f.m = Some(0.0);
            f.d = n;
        }),
        'L' => number(f, string, i, 3, |f, n| f.l = n),
        'm' => number(f, string, i, 2, |f, n| f.m = Some(n - 1.0)),
        'M' => number(f, string, i, 2, |f, n| f.min = n),
        'p' => named(f, string, i, &locale.periods, |f, n| f.p = Some(n)),
        'q' => number(f, string, i, 1, |f, n| f.q = Some(n * 3.0 - 3.0)),
        'Q' => number(f, string, i, usize::MAX, |f, n| f.unix_ms = Some(n)),
        's' => number(f, string, i, usize::MAX, |f, n| f.unix_s = Some(n)),
        'S' => number(f, string, i, 2, |f, n| f.s = n),
        'u' => number(f, string, i, 1, |f, n| f.u = Some(n)),
        'U' => number(f, string, i, 2, |f, n| f.week_sunday = Some(n)),
        'V' => number(f, string, i, 2, |f, n| f.week_iso = Some(n)),
        'w' => number(f, string, i, 1, |f, n| f.w = Some(n)),
        'W' => number(f, string, i, 2, |f, n| f.week_monday = Some(n)),
        'Z' => zone_at(string, i).map(|(z, len)| {
            f.zone = Some(z);
            i + len
        }),
        '%' => (string.get(i) == Some(&'%')).then_some(i + 1),
        _ => None,
    })
}
