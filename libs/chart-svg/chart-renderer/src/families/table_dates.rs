//! A temporal cell's formatting — `format_table_cell_value`'s temporal path
//! (`table_support.py`) over an ISO date or timestamp, painted through
//! `portable_strftime` (`text/format_d3.py`). Month, day and period names and
//! the `%c %x %X` patterns come from the board's time locale; a directive that
//! depends on the host time zone refuses by name.

use crate::time_locale::active_time_locale;

/// glibc's C-locale `%h`, which `portable_strftime` leaves to the host
/// (`_NAME_DIRECTIVES` holds only `a A b B p`).
const C_LOCALE_SHORT_MONTHS: [&str; 12] = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/// Nesting `%c` -> `%x`/`%X` is two deep; more means a locale pattern that
/// expands into itself.
const COMPOSITE_ROUNDS: usize = 8;

/// Why a regex-accepted timestamp does not parse: `Invalid` is what
/// production's `fromisoformat` rejects with a `ValueError` (its message is
/// the text); `Unported` is a shape production fails on in some other way.
#[derive(Debug)]
pub(super) enum TemporalError {
    Invalid(String),
    Unported(String),
}

/// `find_unsupported_directive` (`text/format_d3.py`) as production's
/// `_validate_strftime_spec` words it.
pub(super) fn validate_spec(spec: &str) -> Result<(), String> {
    let stripped = spec.replace("%%", "");
    let mut chars = stripped.chars();
    while let Some(c) = chars.next() {
        if c != '%' {
            continue;
        }
        let mut letter = chars.next();
        if matches!(letter, Some('-' | '_' | '0')) {
            letter = chars.next();
        }
        if let Some(l) = letter.filter(|l| !"aAbBcCdDefFgGhHIjklLmMnpPqQrRsSTuUVwWxXyYzZ".contains(*l)) {
            return Err(format!(
                "Invalid temporal format spec {}: unknown directive %{}",
                crate::diagnostic::py_repr(&serde_json::json!(spec)),
                crate::diagnostic::py_repr(&serde_json::json!(l.to_string())),
            ));
        }
    }
    Ok(())
}

/// A calendar-valid instant: a `date` is midnight and naive (`date.strftime`
/// sees zeros for every time field), a timestamp keeps its time, and an aware
/// one is already normalized to UTC (`format_temporal_value`).
#[derive(Clone, Copy)]
pub(super) struct Moment {
    year: u32,
    month: u32,
    day: u32,
    hour: u32,
    minute: u32,
    second: u32,
    micro: u32,
    utc: bool,
}

impl Moment {
    /// The instant as a tuple that orders chronologically (a moment is already UTC).
    pub(super) fn instant(&self) -> (u32, u32, u32, u32, u32, u32, u32) {
        (self.year, self.month, self.day, self.hour, self.minute, self.second, self.micro)
    }
}

fn is_leap(year: u32) -> bool {
    (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
}

fn days_in_month(year: u32, month: u32) -> u32 {
    match month {
        2 if is_leap(year) => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// Days from 0001-01-01 (`date.toordinal() - 1`) to the given calendar day.
fn days_from_epoch(year: u32, month: u32, day: u32) -> i64 {
    let y = i64::from(year - 1);
    let before_month: u32 = (1..month).map(|m| days_in_month(year, m)).sum();
    y * 365 + y / 4 - y / 100 + y / 400 + i64::from(before_month + day - 1)
}

/// The inverse of `days_from_epoch` (proleptic Gregorian, civil-from-days).
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 306; // shift the epoch to 0000-03-01
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = yoe + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

impl Moment {
    /// A naive date at midnight (`date.strftime` sees zeros for every time field).
    pub(super) fn midnight(year: u32, month: u32, day: u32) -> Self {
        Moment { year, month, day, hour: 0, minute: 0, second: 0, micro: 0, utc: false }
    }

    fn day_of_year(self) -> u32 {
        (1..self.month)
            .map(|m| days_in_month(self.year, m))
            .sum::<u32>()
            + self.day
    }

    /// Monday = 0; 0001-01-01 is a Monday.
    fn weekday(self) -> usize {
        days_from_epoch(self.year, self.month, self.day).rem_euclid(7) as usize
    }

    fn hour12(self) -> u32 {
        if self.hour % 12 == 0 {
            12
        } else {
            self.hour % 12
        }
    }

    /// `date.isocalendar()` as `(year, week)`.
    fn iso_week(self) -> (u32, u32) {
        let weeks_in = |y: u32| {
            let jan1 = Moment {
                year: y,
                month: 1,
                day: 1,
                ..self
            }
            .weekday();
            if jan1 == 3 || (is_leap(y) && jan1 == 2) {
                53
            } else {
                52
            }
        };
        let week = (self.day_of_year() as i64 - (self.weekday() as i64 + 1) + 10) / 7;
        if week < 1 {
            (self.year - 1, weeks_in(self.year - 1))
        } else if week as u32 > weeks_in(self.year) {
            (self.year + 1, 1)
        } else {
            (self.year, week as u32)
        }
    }

    /// `_week_monday_first` (`text/format_d3.py`): `%W`, with a real week 00.
    fn week_monday_first(self) -> u32 {
        let since_jan1 = self.day_of_year() - 1;
        let to_first_monday = (7 - Moment {
            month: 1,
            day: 1,
            ..self
        }
        .weekday() as u32)
            % 7;
        if since_jan1 < to_first_monday {
            0
        } else {
            (since_jan1 - to_first_monday) / 7 + 1
        }
    }

    /// glibc `%U`: `(tm_yday + 7 - tm_wday) / 7` with Sunday = 0.
    fn week_sunday_first(self) -> u32 {
        (self.day_of_year() - 1 + 7 - (self.weekday() as u32 + 1) % 7) / 7
    }
}

/// `is_temporal_value` (`table_support.py`) then `format_temporal_value`'s
/// parse: `_ISO_TEMPORAL_RE`, the `date.fromisoformat(value[:10])` calendar
/// check, and for a timestamp `datetime.fromisoformat` (a `Z` or offset
/// normalized to UTC). `Ok(None)` for anything else — the caller renders it
/// verbatim, as production's string branch does. A regex-accepted timestamp
/// that `fromisoformat` rejects (hour > 23, minute or second > 59, an offset of
/// 24 hours or more, a result outside years 1-9999) raises in production and
/// errs here.
pub(super) fn parse_temporal(value: &str) -> Result<Option<Moment>, TemporalError> {
    let b = value.as_bytes();
    let digits =
        |r: std::ops::Range<usize>| b.get(r).is_some_and(|d| d.iter().all(u8::is_ascii_digit));
    if b.len() < 10
        || !digits(0..4)
        || b[4] != b'-'
        || !digits(5..7)
        || b[7] != b'-'
        || !digits(8..10)
    {
        return Ok(None);
    }
    let year: u32 = value[0..4].parse().map_err(|_| TemporalError::Unported("year".into()))?;
    let month: u32 = value[5..7].parse().map_err(|_| TemporalError::Unported("month".into()))?;
    let day: u32 = value[8..10].parse().map_err(|_| TemporalError::Unported("day".into()))?;
    if year == 0 || !(1..=12).contains(&month) || day == 0 || day > days_in_month(year, month) {
        return Ok(None);
    }
    let date = Moment::midnight(year, month, day);
    if b.len() == 10 {
        return Ok(Some(date));
    }
    let Some(time) = time_tail(&value[10..]) else {
        return Ok(None);
    };
    // `datetime.fromisoformat`'s own messages, checked in its order (offset,
    // then hour, minute, second).
    if let Some(offset) = time.offset_minutes.filter(|o| o.abs() >= 24 * 60) {
        let seconds = i64::from(offset) * 60;
        let (days, rest) = (seconds.div_euclid(86_400), seconds.rem_euclid(86_400));
        let delta = match rest {
            0 => format!("days={days}"),
            _ if days == 0 => format!("seconds={rest}"),
            _ => format!("days={days}, seconds={rest}"),
        };
        return Err(TemporalError::Invalid(format!(
            "offset must be a timedelta strictly between -timedelta(hours=24) and \
             timedelta(hours=24), not datetime.timedelta({delta})."
        )));
    }
    if time.hour > 23 {
        return Err(TemporalError::Invalid("hour must be in 0..23".into()));
    }
    if time.minute > 59 {
        return Err(TemporalError::Invalid("minute must be in 0..59".into()));
    }
    if time.second > 59 {
        return Err(TemporalError::Invalid("second must be in 0..59".into()));
    }
    let mut moment = Moment {
        hour: time.hour,
        minute: time.minute,
        second: time.second,
        micro: time.micro,
        ..date
    };
    if let Some(offset) = time.offset_minutes {
        let minutes = days_from_epoch(year, month, day) * 1440
            + i64::from(time.hour * 60 + time.minute)
            - i64::from(offset);
        let (y, m, d) = civil_from_days(minutes.div_euclid(1440));
        if !(1..=9999).contains(&y) {
            // Production raises an uncoded `OverflowError` here.
            return Err(TemporalError::Unported(format!(
                "the timestamp {value:?} is outside years 1-9999 in UTC"
            )));
        }
        let of_day = minutes.rem_euclid(1440) as u32;
        moment = Moment {
            year: y as u32,
            month: m,
            day: d,
            hour: of_day / 60,
            minute: of_day % 60,
            utc: true,
            ..moment
        };
    }
    Ok(Some(moment))
}

struct TimeTail {
    hour: u32,
    minute: u32,
    second: u32,
    micro: u32,
    offset_minutes: Option<i32>,
}

/// `([T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)` after the date part.
/// Fractional digits beyond the sixth are dropped and short ones right-padded,
/// as `datetime.fromisoformat` reads them.
fn time_tail(tail: &str) -> Option<TimeTail> {
    let b = tail.as_bytes();
    let digits =
        |r: std::ops::Range<usize>| b.get(r).is_some_and(|d| d.iter().all(u8::is_ascii_digit));
    if b.len() < 9
        || !matches!(b[0], b'T' | b' ')
        || !digits(1..3)
        || b[3] != b':'
        || !digits(4..6)
        || b[6] != b':'
        || !digits(7..9)
    {
        return None;
    }
    let mut rest = &tail[9..];
    let mut micro = 0;
    if let Some(frac) = rest.strip_prefix('.') {
        let n = frac.bytes().take_while(u8::is_ascii_digit).count();
        if n == 0 {
            return None;
        }
        micro = format!("{:0<6}", &frac[..n.min(6)]).parse().ok()?;
        rest = &frac[n..];
    }
    let offset_minutes = if rest.is_empty() {
        None
    } else if rest == "Z" {
        Some(0)
    } else {
        let r = rest.as_bytes();
        let all = |d: &[u8]| d.iter().all(u8::is_ascii_digit);
        let (hh, mm) = match r.len() {
            5 if all(&r[1..]) => (&rest[1..3], &rest[3..5]),
            6 if r[3] == b':' && all(&r[1..3]) && all(&r[4..]) => (&rest[1..3], &rest[4..6]),
            _ => return None,
        };
        let sign = match r[0] {
            b'+' => 1,
            b'-' => -1,
            _ => return None,
        };
        Some(sign * (hh.parse::<i32>().ok()? * 60 + mm.parse::<i32>().ok()?))
    };
    Some(TimeTail {
        hour: tail[1..3].parse().ok()?,
        minute: tail[4..6].parse().ok()?,
        second: tail[7..9].parse().ok()?,
        micro,
        offset_minutes,
    })
}

/// `is_time_format` (`text/format_d3.py`): a d3-time-format directive
/// (`%<letter>`, optional `-`/`_`/`0` modifier) after dropping `%%` escapes.
pub(super) fn is_time_format(spec: &str) -> bool {
    let spec = spec.replace("%%", "");
    let b = spec.as_bytes();
    (0..b.len()).any(|i| {
        b[i] == b'%' && {
            let j = i + 1 + usize::from(matches!(b.get(i + 1), Some(b'-' | b'_' | b'0')));
            b.get(j).is_some_and(u8::is_ascii_alphabetic)
        }
    })
}

/// The text `d.strftime("%<letter>")` gives for the single-field numeric
/// directives `_PADDED_NUMERIC_DIRECTIVES` (`text/format_d3.py`) carries, in
/// the padding the unmodified directive has (`_DIRECTIVE_COMPUTERS` for the
/// nine the CRT lacks, glibc for the rest).
fn padded_numeric(m: Moment, letter: char) -> Option<String> {
    Some(match letter {
        'C' => format!("{:02}", m.year / 100),
        'd' => format!("{:02}", m.day),
        'e' => format!("{:2}", m.day),
        'G' => format!("{:04}", m.iso_week().0),
        'H' => format!("{:02}", m.hour),
        'I' => format!("{:02}", m.hour12()),
        'j' => format!("{:03}", m.day_of_year()),
        'k' => format!("{:2}", m.hour),
        'l' => format!("{:2}", m.hour12()),
        'm' => format!("{:02}", m.month),
        'M' => format!("{:02}", m.minute),
        'S' => format!("{:02}", m.second),
        'u' => (m.weekday() + 1).to_string(),
        'U' => format!("{:02}", m.week_sunday_first()),
        'V' => format!("{:02}", m.iso_week().1),
        'w' => ((m.weekday() + 1) % 7).to_string(),
        'W' => format!("{:02}", m.week_monday_first()),
        'y' => format!("{:02}", m.year % 100),
        'Y' => m.year.to_string(),
        _ => return None,
    })
}

/// `_expand_composites`: `%c %x %X` (modified or not) become the locale's
/// patterns until none is left; `%%` is skipped so `%%c` stays literal.
fn expand_composites(spec: &str) -> Result<String, String> {
    let mut spec = spec.to_string();
    for _ in 0..COMPOSITE_ROUNDS {
        let chars: Vec<char> = spec.chars().collect();
        let mut out = String::new();
        let mut expanded = false;
        let mut i = 0;
        while i < chars.len() {
            if chars[i] != '%' {
                out.push(chars[i]);
                i += 1;
                continue;
            }
            let at = |offset: usize| chars.get(i + offset).copied();
            let letter_at = if matches!(at(1), Some('-' | '_' | '0')) {
                2
            } else {
                1
            };
            match at(letter_at) {
                Some(letter @ ('c' | 'x' | 'X')) => {
                    let locale = active_time_locale()?;
                    out.push_str(match letter {
                        'c' => &locale.date_time,
                        'x' => &locale.date,
                        _ => &locale.time,
                    });
                    expanded = true;
                    i += letter_at + 1;
                }
                _ if at(1) == Some('%') => {
                    out.push_str("%%");
                    i += 2;
                }
                _ => {
                    out.push('%');
                    i += 1;
                }
            }
        }
        if !expanded {
            return Ok(out);
        }
        spec = out;
    }
    Err(format!(
        "time_locale patterns expand `%c %x %X` without end in {spec:?}"
    ))
}

fn locale_word(m: Moment, letter: char) -> Result<String, String> {
    let locale = active_time_locale()?;
    let weekday = (m.weekday() + 1) % 7;
    let month = m.month as usize - 1;
    Ok(match letter {
        'a' => &locale.short_days[weekday],
        'A' => &locale.days[weekday],
        'b' => &locale.short_months[month],
        'B' => &locale.months[month],
        _ => &locale.periods[usize::from(m.hour >= 12)],
    }
    .clone())
}

/// `portable_strftime(d, spec)` (`text/format_d3.py`): the `-`/`_`/`0` padding
/// modifiers on `_PADDED_NUMERIC_DIRECTIVES` (`lstrip("0") or "0"`, then
/// `rjust`), the CRT-unsupported directives computed in Python, the rest by
/// glibc `strftime` in the C locale; the name directives and `%c %x %X` read
/// the active time locale. Refused by name: a modifier on any other
/// directive, `%s` (host time zone), a directive
/// `PYTHON_STRFTIME_DIRECTIVES` rejects, and a trailing `%`.
pub(super) fn strftime(m: Moment, spec: &str) -> Result<String, String> {
    let spec = expand_composites(spec)?;
    let mut out = String::new();
    let mut chars = spec.chars();
    while let Some(c) = chars.next() {
        if c != '%' {
            out.push(c);
            continue;
        }
        let trailing = || "trailing '%' has no directive".to_string();
        let mut letter = chars.next().ok_or_else(trailing)?;
        if letter == '%' {
            out.push('%');
            continue;
        }
        let modifier = matches!(letter, '-' | '_' | '0').then_some(letter);
        if modifier.is_some() {
            letter = chars.next().ok_or_else(trailing)?;
        }
        let name = format!(
            "%{}{letter}",
            modifier.map(String::from).unwrap_or_default()
        );
        let unsupported = || format!("directive {name} is not supported");
        if matches!(letter, 'a' | 'A' | 'b' | 'B' | 'p') {
            out.push_str(&locale_word(m, letter)?);
            continue;
        }
        if let Some(modifier) = modifier {
            let padded = padded_numeric(m, letter).ok_or_else(unsupported)?;
            let value = match padded.trim().trim_start_matches('0') {
                "" => "0",
                t => t,
            };
            match modifier {
                '-' => out.push_str(value),
                '_' => out.push_str(&format!("{value:>width$}", width = padded.len())),
                _ => out.push_str(&format!("{value:0>width$}", width = padded.len())),
            }
            continue;
        }
        let text = match letter {
            'h' => C_LOCALE_SHORT_MONTHS[m.month as usize - 1].to_string(),
            'D' => strftime(m, "%m/%d/%y")?,
            'f' => format!("{:06}", m.micro),
            'F' => strftime(m, "%Y-%m-%d")?,
            'g' => format!("{:02}", m.iso_week().0 % 100),
            'n' => "\n".to_string(),
            'P' => (if m.hour < 12 { "am" } else { "pm" }).to_string(),
            'r' => format!(
                "{} {}",
                strftime(m, "%I:%M:%S")?,
                if m.hour < 12 { "AM" } else { "PM" }
            ),
            'R' => strftime(m, "%H:%M")?,
            'T' => strftime(m, "%H:%M:%S")?,
            'z' => (if m.utc { "+0000" } else { "" }).to_string(),
            'Z' => (if m.utc { "UTC" } else { "" }).to_string(),
            _ => padded_numeric(m, letter).ok_or_else(unsupported)?,
        };
        out.push_str(&text);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::time_locale::{de_de, en_us, with_time_locale, TimeLocale};

    fn in_locale(locale: &TimeLocale, m: Moment, spec: &str) -> Result<String, String> {
        with_time_locale(locale, || strftime(m, spec))
    }

    fn en_strftime(m: Moment, spec: &str) -> Result<String, String> {
        in_locale(&en_us(), m, spec)
    }

    fn de_strftime(input: &str, spec: &str) -> String {
        in_locale(&de_de(), moment(input), spec).unwrap()
    }

    #[test]
    fn names_come_from_the_board_locale() {
        assert_eq!(de_strftime("2024-03-05", "%b %B"), "Mrz März");
        assert_eq!(de_strftime("2024-10-05", "%b"), "Okt");
        assert_eq!(de_strftime("2024-03-05", "%a %A"), "Di Dienstag");
        assert_eq!(de_strftime("2024-03-10", "%A"), "Sonntag");
        assert_eq!(de_strftime("2024-03-09", "%a"), "Sa");
    }

    #[test]
    fn period_names_come_from_the_board_locale() {
        let mut locale = en_us();
        locale.periods = ["vorm.".into(), "nachm.".into()];
        let at = |input| in_locale(&locale, moment(input), "%p").unwrap();
        assert_eq!(at("2024-03-05T09:00:00"), "vorm.");
        assert_eq!(at("2024-03-05T14:30:00"), "nachm.");
    }

    #[test]
    fn composites_expand_to_the_locale_patterns_recursively() {
        let input = "2024-03-05T14:30:00";
        assert_eq!(de_strftime(input, "%x"), "05.03.2024");
        assert_eq!(de_strftime(input, "%X"), "14:30:00");
        assert_eq!(
            de_strftime(input, "%c"),
            "Dienstag, der  5. März 2024, 14:30:00"
        );
        let en = |spec| en_strftime(moment(input), spec).unwrap();
        assert_eq!(en("%x"), "3/5/2024");
        assert_eq!(en("%X"), "2:30:00 PM");
        assert_eq!(en("%c"), "3/5/2024, 2:30:00 PM");
        assert_eq!(en("%-x|%_X"), "3/5/2024|2:30:00 PM");
    }

    #[test]
    fn a_literal_percent_is_not_a_directive() {
        assert_eq!(de_strftime("2024-03-05", "%%b %%c 100%%"), "%b %c 100%");
        let mut locale = en_us();
        locale.months[2] = "50%".into();
        assert_eq!(
            in_locale(&locale, moment("2024-03-05"), "%B").unwrap(),
            "50%"
        );
    }

    #[test]
    fn a_padding_modifier_on_a_name_directive_still_prints_the_locale_word() {
        assert_eq!(
            de_strftime("2024-03-05", "%-b|%_B|%0a|%-A"),
            "Mrz|März|Di|Dienstag"
        );
        assert_eq!(de_strftime("2024-03-05T14:30:00", "%-p"), "PM");
    }

    #[test]
    fn a_name_without_a_scope_refuses_and_a_numeric_spec_does_not() {
        let m = moment("2024-03-05");
        assert!(strftime(m, "%b").unwrap_err().contains("time_locale"));
        assert!(strftime(m, "%x").unwrap_err().contains("time_locale"));
        assert_eq!(strftime(m, "%Y-%m-%d").unwrap(), "2024-03-05");
    }

    #[test]
    fn c_locale_directives_ignore_the_board_locale() {
        assert_eq!(
            de_strftime("2024-10-05T14:30:00", "%h|%P|%r"),
            "Oct|pm|02:30:00 PM"
        );
    }

    #[test]
    fn composites_that_never_terminate_refuse() {
        let mut locale = en_us();
        locale.date = "%x".into();
        let err = in_locale(&locale, moment("2024-03-05"), "%x").unwrap_err();
        assert!(err.contains("%x"), "{err}");
    }

    fn moment(s: &str) -> Moment {
        parse_temporal(s)
            .unwrap()
            .unwrap_or_else(|| panic!("{s} is not temporal"))
    }

    /// `format_temporal_value(input, spec)` run through the production module
    /// over every directive, each padding modifier, the ISO-week edge dates and
    /// the offset shapes.
    #[test]
    fn strftime_matches_production_over_the_golden_table() {
        let golden = include_str!("table_dates_golden.jsonl");
        assert!(golden.lines().count() > 700);
        for line in golden.lines() {
            let [input, spec, expected]: [String; 3] = serde_json::from_str(line).unwrap();
            assert_eq!(
                en_strftime(moment(&input), &spec).unwrap(),
                expected,
                "{spec} of {input}"
            );
        }
    }

    #[test]
    fn a_directive_production_rejects_refuses() {
        // `PYTHON_STRFTIME_DIRECTIVES` holds no `t`, `E`, `O`, `L`, `q` or `Q`.
        for spec in ["%t", "%E", "%Oe", "%L", "%q", "%Q"] {
            assert!(en_strftime(moment("2024-03-05"), spec).is_err(), "{spec}");
        }
    }

    #[test]
    fn weekday_anchors_on_a_known_monday() {
        // 2024-01-01 is a Monday; 0001-01-01 (the epoch of `weekday`) is too.
        assert_eq!(moment("2024-01-01").weekday(), 0);
        assert_eq!(moment("0001-01-01").weekday(), 0);
        assert_eq!(moment("2000-01-09").weekday(), 6);
    }

    #[test]
    fn civil_from_days_inverts_days_from_epoch() {
        for (y, m, d) in [
            (1, 1, 1),
            (4, 2, 29),
            (1900, 3, 1),
            (2000, 2, 29),
            (2024, 12, 31),
            (9999, 12, 31),
        ] {
            let (cy, cm, cd) = civil_from_days(days_from_epoch(y, m, d));
            assert_eq!((cy, cm, cd), (i64::from(y), m, d));
        }
    }

    #[test]
    fn an_offset_normalizes_to_utc_across_a_day_boundary() {
        let m = moment("2024-03-01T01:00:00+05:30");
        assert_eq!(
            en_strftime(m, "%Y-%m-%d %H:%M %z").unwrap(),
            "2024-02-29 19:30 +0000"
        );
        let m = moment("2023-12-31T23:30:00-01:00");
        assert_eq!(
            en_strftime(m, "%Y-%m-%d %H:%M").unwrap(),
            "2024-01-01 00:30"
        );
    }

    #[test]
    fn fractional_seconds_keep_six_digits() {
        assert_eq!(
            en_strftime(moment("2024-01-01T00:00:00.5"), "%f").unwrap(),
            "500000"
        );
        assert_eq!(
            en_strftime(moment("2024-01-01T00:00:00.1234567890"), "%f").unwrap(),
            "123456"
        );
    }

    #[test]
    fn time_tails_follow_the_iso_temporal_pattern() {
        for ok in [
            "2024-01-01T10:30:00",
            "2024-01-01 10:30:00.123",
            "2024-01-01T10:30:00Z",
            "2024-01-01T10:30:00+0530",
            "2024-01-01T10:30:00-05:30",
        ] {
            assert!(parse_temporal(ok).unwrap().is_some(), "{ok}");
        }
        for bad in [
            "2024-01-01T10:30",
            "2024-01-01T10:30:00.",
            "2024-01-01T10:30:00+5",
            "2024-01-01x",
            "2024-02-30",
            "2024-01-01t10:30:00",
        ] {
            assert!(parse_temporal(bad).unwrap().is_none(), "{bad}");
        }
    }

    #[test]
    fn a_timestamp_fromisoformat_rejects_errs() {
        for bad in [
            "2024-01-01T24:00:00",
            "2024-01-01T10:60:00",
            "2024-01-01T10:30:60",
            "2024-01-01T10:30:00+24:00",
            "0001-01-01T00:00:00+05:00",
            "9999-12-31T23:00:00-05:00",
        ] {
            assert!(parse_temporal(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn directives_that_depend_on_the_time_zone_refuse_by_name() {
        let m = moment("2024-03-05T14:30:00");
        for spec in ["%s", "%q", "%Q"] {
            let err = en_strftime(m, spec).unwrap_err();
            assert!(err.contains(&spec[..spec.len()]), "{spec}: {err}");
        }
        assert!(en_strftime(m, "100%").unwrap_err().contains("trailing"));
    }

    #[test]
    fn time_format_detection_ignores_percent_escapes() {
        assert!(is_time_format("%b %Y"));
        assert!(is_time_format("%-d"));
        assert!(!is_time_format("%%Y"));
        assert!(!is_time_format(",.2f"));
        assert!(!is_time_format("%5d"));
    }
}
