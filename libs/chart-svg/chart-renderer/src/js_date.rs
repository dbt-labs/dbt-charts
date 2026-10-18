//! `Date.parse` as V8 reads it, with the local zone pinned to UTC.
//!
//! Vega parses every cell of a date-typed field with `Date.parse`, and the
//! strings a column can hold go well past ISO 8601: V8 falls back to a legacy
//! grammar (`10/15/2024`, `Oct 15, 2024`, `Mon Oct 15 2024 10:00 PST`, ...) and
//! accepts lenient day-of-month overflow. A port of V8's `dateparser.cc`
//! (`DateParser::Parse` with `ParseES5DateTime`, `DayComposer`, `TimeComposer`,
//! `TimeZoneComposer`).

use crate::families::temporal::days_from_civil;

const MS_PER_DAY: f64 = 86_400_000.0;
const MAX_TIME_MS: f64 = 8.64e15;
const NONE: i64 = i64::MAX;

#[derive(Clone, Copy, PartialEq)]
enum Keyword {
    Invalid,
    Month,
    Zone,
    Separator,
    AmPm,
}

#[derive(Clone, Copy)]
enum Token {
    End,
    Number { value: i64, len: usize },
    Symbol(char),
    Keyword { kind: Keyword, value: i64, len: usize },
    Space,
    Unknown,
    Invalid,
}

impl Token {
    fn is_number(self) -> bool {
        matches!(self, Token::Number { .. })
    }

    fn fixed_number(self, digits: usize) -> Option<i64> {
        match self {
            Token::Number { value, len } if len == digits => Some(value),
            _ => None,
        }
    }

    fn is_symbol(self, c: char) -> bool {
        matches!(self, Token::Symbol(s) if s == c)
    }

    fn sign(self) -> Option<i64> {
        match self {
            Token::Symbol('+') => Some(1),
            Token::Symbol('-') => Some(-1),
            _ => None,
        }
    }

    fn is_keyword_z(self) -> bool {
        matches!(self, Token::Keyword { kind: Keyword::Zone, len: 1, .. })
    }

    fn is_separator(self) -> bool {
        matches!(self, Token::Keyword { kind: Keyword::Separator, .. })
    }
}

/// `KeywordTable`: the first three letters of a word, lowercased. Only a month
/// name may run longer than its three letters.
const KEYWORDS: [(&str, Keyword, i64); 27] = [
    ("jan", Keyword::Month, 1),
    ("feb", Keyword::Month, 2),
    ("mar", Keyword::Month, 3),
    ("apr", Keyword::Month, 4),
    ("may", Keyword::Month, 5),
    ("jun", Keyword::Month, 6),
    ("jul", Keyword::Month, 7),
    ("aug", Keyword::Month, 8),
    ("sep", Keyword::Month, 9),
    ("oct", Keyword::Month, 10),
    ("nov", Keyword::Month, 11),
    ("dec", Keyword::Month, 12),
    ("am", Keyword::AmPm, 0),
    ("pm", Keyword::AmPm, 12),
    ("ut", Keyword::Zone, 0),
    ("utc", Keyword::Zone, 0),
    ("z", Keyword::Zone, 0),
    ("gmt", Keyword::Zone, 0),
    ("cdt", Keyword::Zone, -5),
    ("cst", Keyword::Zone, -6),
    ("edt", Keyword::Zone, -4),
    ("est", Keyword::Zone, -5),
    ("mdt", Keyword::Zone, -6),
    ("mst", Keyword::Zone, -7),
    ("pdt", Keyword::Zone, -7),
    ("pst", Keyword::Zone, -8),
    ("t", Keyword::Separator, 0),
];

/// ECMAScript `WhiteSpace` and `LineTerminator`.
fn is_space(c: u16) -> bool {
    matches!(
        c,
        0x09..=0x0D | 0x20 | 0xA0 | 0x1680 | 0x2000..=0x200A | 0x2028 | 0x2029 | 0x202F | 0x205F | 0x3000 | 0xFEFF
    )
}

fn tokenize(text: &str) -> Vec<Token> {
    let s: Vec<u16> = text.encode_utf16().collect();
    let mut tokens = Vec::new();
    let mut i = 0;
    while i < s.len() {
        let c = s[i];
        if (u16::from(b'0')..=u16::from(b'9')).contains(&c) {
            let start = i;
            let mut value: i64 = 0;
            let mut significant = 0;
            while i < s.len() && (u16::from(b'0')..=u16::from(b'9')).contains(&s[i]) {
                let digit = i64::from(s[i] - u16::from(b'0'));
                if digit != 0 || significant > 0 {
                    if significant < 9 {
                        value = value * 10 + digit;
                    }
                    significant += 1;
                }
                i += 1;
            }
            tokens.push(Token::Number { value, len: i - start });
        } else if let Some(sym) = [':', '-', '+', '.', ')'].into_iter().find(|&sym| u16::from(sym as u8) == c) {
            tokens.push(Token::Symbol(sym));
            i += 1;
        } else if c >= u16::from(b'A') && !is_space(c) {
            let mut prefix = String::new();
            let mut len = 0;
            while i < s.len() && s[i] >= u16::from(b'A') && !is_space(s[i]) {
                if len < 3 {
                    prefix.push(char::from_u32(u32::from(s[i])).map_or('\u{0}', |ch| ch.to_ascii_lowercase()));
                }
                len += 1;
                i += 1;
            }
            let (kind, value) = KEYWORDS
                .iter()
                .find(|(word, kind, _)| *word == prefix && (len <= 3 || *kind == Keyword::Month))
                .map_or((Keyword::Invalid, 0), |&(_, kind, value)| (kind, value));
            tokens.push(Token::Keyword { kind, value, len });
        } else if is_space(c) {
            tokens.push(Token::Space);
            i += 1;
        } else if c == u16::from(b'(') {
            let mut balance = 0;
            loop {
                if s[i] == u16::from(b')') {
                    balance -= 1;
                } else if s[i] == u16::from(b'(') {
                    balance += 1;
                }
                i += 1;
                if balance <= 0 || i >= s.len() {
                    break;
                }
            }
            tokens.push(Token::Unknown);
        } else {
            tokens.push(Token::Unknown);
            i += 1;
        }
    }
    tokens.push(Token::End);
    tokens
}

struct Scanner {
    tokens: Vec<Token>,
    at: usize,
}

impl Scanner {
    fn peek(&self) -> Token {
        self.tokens[self.at]
    }

    fn next(&mut self) -> Token {
        let token = self.tokens[self.at];
        if self.at + 1 < self.tokens.len() {
            self.at += 1;
        }
        token
    }

    fn skip_symbol(&mut self, c: char) -> bool {
        let found = self.peek().is_symbol(c);
        if found {
            self.next();
        }
        found
    }
}

fn is_month(n: i64) -> bool {
    (1..=12).contains(&n)
}

fn is_day(n: i64) -> bool {
    (1..=31).contains(&n)
}

fn is_hour(n: i64) -> bool {
    (0..=23).contains(&n)
}

fn is_minute(n: i64) -> bool {
    (0..=59).contains(&n)
}

fn is_millisecond(n: i64) -> bool {
    (0..=999).contains(&n)
}

#[derive(Default)]
struct Day {
    parts: Vec<i64>,
    named_month: Option<i64>,
    iso: bool,
}

impl Day {
    fn add(&mut self, n: i64) -> bool {
        let room = self.parts.len() < 3;
        if room {
            self.parts.push(n);
        }
        room
    }

    /// `DayComposer::Write`: `(year, month, day)` with the month 1-based.
    fn write(mut self) -> Option<(i64, i64, i64)> {
        if self.parts.is_empty() {
            return None;
        }
        self.parts.resize(3, 1);
        let c = &self.parts;
        let (mut year, month, day) = match self.named_month {
            None if self.iso || !is_day(c[0]) => (c[0], c[1], c[2]),
            None => (c[2], c[0], c[1]),
            Some(month) if !is_day(c[0]) => (c[0], month, c[1]),
            Some(month) => (c[1], month, c[0]),
        };
        if !self.iso {
            if (0..=49).contains(&year) {
                year += 2000;
            } else if (50..=99).contains(&year) {
                year += 1900;
            }
        }
        (is_month(month) && is_day(day)).then_some((year, month, day))
    }
}

struct Time {
    parts: Vec<i64>,
    hour_offset: Option<i64>,
}

impl Time {
    fn add(&mut self, n: i64) -> bool {
        let room = self.parts.len() < 4;
        if room {
            self.parts.push(n);
        }
        room
    }

    fn add_final(&mut self, n: i64) -> bool {
        let room = self.add(n);
        if room {
            self.parts.resize(4, 0);
        }
        room
    }

    fn expecting(&self, n: i64) -> bool {
        match self.parts.len() {
            1 => is_minute(n),
            2 => is_minute(n),
            3 => is_millisecond(n),
            _ => false,
        }
    }

    /// `TimeComposer::Write`: `(hour, minute, second, millisecond)`.
    fn write(mut self) -> Option<[i64; 4]> {
        self.parts.resize(4, 0);
        let [mut hour, minute, second, ms] = [self.parts[0], self.parts[1], self.parts[2], self.parts[3]];
        if let Some(offset) = self.hour_offset {
            if !(0..=12).contains(&hour) {
                return None;
            }
            hour = hour % 12 + offset;
        }
        let valid = is_hour(hour) && is_minute(minute) && is_minute(second) && is_millisecond(ms);
        (valid || (hour == 24 && minute == 0 && second == 0 && ms == 0)).then_some([hour, minute, second, ms])
    }
}

struct Zone {
    sign: i64,
    hour: i64,
    minute: i64,
}

impl Zone {
    fn set(&mut self, offset_hours: i64) {
        self.sign = if offset_hours < 0 { -1 } else { 1 };
        self.hour = offset_hours.abs();
        self.minute = 0;
    }

    fn expecting(&self, n: i64) -> bool {
        self.hour != NONE && self.minute == NONE && is_minute(n)
    }

    fn is_utc(&self) -> bool {
        self.hour == 0 && self.minute == 0
    }

    /// The offset in seconds; `None` for no zone (local time, UTC here).
    fn write(&self) -> Option<i64> {
        (self.sign != NONE).then(|| {
            let hour = if self.hour == NONE { 0 } else { self.hour };
            let minute = if self.minute == NONE { 0 } else { self.minute };
            self.sign * (hour * 3600 + minute * 60)
        })
    }
}

/// `ReadMilliseconds`: the first three significant digits of the numeral.
fn milliseconds(value: i64, len: usize) -> i64 {
    match len {
        1 => value * 100,
        2 => value * 10,
        0..=3 => value,
        _ => value / 10_i64.pow(len.min(9) as u32 - 3),
    }
}

/// `ParseES5DateTime`: the ES5 date-time string format, or the first token it
/// cannot read, which the legacy loop resumes from.
fn parse_es5(scanner: &mut Scanner, day: &mut Day, time: &mut Time, zone: &mut Zone) -> Token {
    if let Some(sign) = scanner.peek().sign() {
        let sign_token = scanner.next();
        let Some(year) = scanner.peek().fixed_number(6) else { return sign_token };
        scanner.next();
        if sign < 0 && year == 0 {
            return sign_token;
        }
        day.add(sign * year);
    } else if let Some(year) = scanner.peek().fixed_number(4) {
        scanner.next();
        day.add(year);
    } else {
        return scanner.next();
    }
    if scanner.skip_symbol('-') {
        match scanner.peek().fixed_number(2) {
            Some(month) if is_month(month) => {
                scanner.next();
                day.add(month);
            }
            _ => return scanner.next(),
        }
        if scanner.skip_symbol('-') {
            match scanner.peek().fixed_number(2) {
                Some(d) if is_day(d) => {
                    scanner.next();
                    day.add(d);
                }
                _ => return scanner.next(),
            }
        }
    }
    if !scanner.peek().is_separator() {
        if !matches!(scanner.peek(), Token::End) {
            return scanner.next();
        }
    } else {
        scanner.next();
        let Some(hour) = scanner.peek().fixed_number(2).filter(|h| (0..=24).contains(h)) else { return Token::Invalid };
        scanner.next();
        let hour_is_24 = hour == 24;
        time.add(hour);
        if !scanner.skip_symbol(':') {
            return Token::Invalid;
        }
        let Some(minute) = scanner.peek().fixed_number(2).filter(|&m| is_minute(m) && !(hour_is_24 && m > 0)) else {
            return Token::Invalid;
        };
        scanner.next();
        time.add(minute);
        if scanner.skip_symbol(':') {
            let Some(second) = scanner.peek().fixed_number(2).filter(|&s| is_minute(s) && !(hour_is_24 && s > 0)) else {
                return Token::Invalid;
            };
            scanner.next();
            time.add(second);
            if scanner.skip_symbol('.') {
                let Token::Number { value, len } = scanner.peek() else { return Token::Invalid };
                if hour_is_24 && value > 0 {
                    return Token::Invalid;
                }
                scanner.next();
                time.add(milliseconds(value, len));
            }
        }
        if scanner.peek().is_keyword_z() {
            scanner.next();
            zone.set(0);
        } else if let Some(sign) = scanner.peek().sign() {
            scanner.next();
            zone.sign = sign;
            if let Some(hhmm) = scanner.peek().fixed_number(4) {
                scanner.next();
                let (hh, mm) = (hhmm / 100, hhmm % 100);
                if !is_hour(hh) || !is_minute(mm) {
                    return Token::Invalid;
                }
                zone.hour = hh;
                zone.minute = mm;
            } else {
                let Some(hh) = scanner.peek().fixed_number(2).filter(|&h| is_hour(h)) else { return Token::Invalid };
                scanner.next();
                zone.hour = hh;
                if !scanner.skip_symbol(':') {
                    return Token::Invalid;
                }
                let Some(mm) = scanner.peek().fixed_number(2).filter(|&m| is_minute(m)) else { return Token::Invalid };
                scanner.next();
                zone.minute = mm;
            }
        }
        if !matches!(scanner.peek(), Token::End) {
            return Token::Invalid;
        }
    }
    if zone.hour == NONE && time.parts.is_empty() {
        zone.set(0);
    }
    day.iso = true;
    Token::End
}

/// `Date.parse(text)` in epoch milliseconds, `None` where it is `NaN`.
pub fn parse_ms(text: &str) -> Option<f64> {
    let mut scanner = Scanner { tokens: tokenize(text), at: 0 };
    let mut day = Day::default();
    let mut time = Time { parts: Vec::new(), hour_offset: None };
    let mut zone = Zone { sign: NONE, hour: NONE, minute: NONE };
    let first = parse_es5(&mut scanner, &mut day, &mut time, &mut zone);
    if matches!(first, Token::Invalid) {
        return None;
    }
    let mut has_read_number = !day.parts.is_empty();
    let mut token = first;
    while !matches!(token, Token::End) {
        match token {
            Token::Number { value: n, .. } => {
                has_read_number = true;
                if scanner.skip_symbol(':') {
                    if scanner.skip_symbol(':') {
                        if !time.parts.is_empty() {
                            return None;
                        }
                        time.add(n);
                        time.add(0);
                    } else {
                        if !time.add(n) {
                            return None;
                        }
                        if scanner.peek().is_symbol('.') {
                            scanner.next();
                        }
                    }
                } else if scanner.skip_symbol('.') && time.expecting(n) {
                    time.add(n);
                    let Token::Number { value, len } = scanner.peek() else { return None };
                    scanner.next();
                    time.add_final(milliseconds(value, len));
                } else if zone.expecting(n) {
                    zone.minute = n;
                } else if time.expecting(n) {
                    time.add_final(n);
                    let peek = scanner.peek();
                    if !matches!(peek, Token::End | Token::Space) && !peek.is_keyword_z() && peek.sign().is_none() {
                        return None;
                    }
                } else {
                    if !day.add(n) {
                        return None;
                    }
                    scanner.skip_symbol('-');
                }
            }
            Token::Keyword { kind, value, .. } => match kind {
                Keyword::AmPm if !time.parts.is_empty() => time.hour_offset = Some(value),
                Keyword::Month => {
                    day.named_month = Some(value);
                    scanner.skip_symbol('-');
                }
                Keyword::Zone if has_read_number => zone.set(value),
                _ => {
                    if has_read_number || scanner.peek().is_number() {
                        return None;
                    }
                }
            },
            Token::Symbol(_) if token.sign().is_some() && (zone.is_utc() || !time.parts.is_empty()) => {
                zone.sign = token.sign().unwrap_or(1);
                let (mut n, mut len) = (0, 0);
                if let Token::Number { value, len: l } = scanner.peek() {
                    scanner.next();
                    (n, len) = (value, l);
                }
                has_read_number = true;
                if scanner.peek().is_symbol(':') {
                    zone.hour = n;
                    zone.minute = NONE;
                } else if len == 1 || len == 2 {
                    zone.hour = n;
                    zone.minute = 0;
                } else if len == 3 || len == 4 {
                    zone.hour = n / 100;
                    zone.minute = n % 100;
                } else {
                    return None;
                }
            }
            Token::Symbol(_) if (token.sign().is_some() || token.is_symbol(')')) && has_read_number => return None,
            _ => {}
        }
        token = scanner.next();
    }
    let (year, month, d) = day.write()?;
    let [hour, minute, second, ms] = time.write()?;
    let offset = zone.write().unwrap_or(0);
    let days = days_from_civil(i32::try_from(year).ok()?, month as u8, 1) + d - 1;
    let value = days as f64 * MS_PER_DAY
        + ((hour * 60 + minute) * 60 + second) as f64 * 1000.0
        + ms as f64
        - offset as f64 * 1000.0;
    (value.abs() <= MAX_TIME_MS).then_some(value)
}

/// A date cell as epoch days, fractional past midnight.
pub fn parse_days(text: &str) -> Option<f64> {
    parse_ms(text).map(|ms| ms / MS_PER_DAY)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `Date.parse` under `TZ=UTC` in Node 22 (V8 12), one row per grammar branch.
    #[test]
    fn reads_what_v8_reads() {
        let rows: &[(&str, Option<f64>)] = &[
        ("2024-01-01", Some(1704067200000.0)),
        ("Jan 1 0000000002024", Some(1704067200000.0)),
        ("2024-01", Some(1704067200000.0)),
        ("2024", Some(1704067200000.0)),
        ("2024-01-01T10:00:00.5Z", Some(1704103200500.0)),
        ("2024-01-01T10:00:00+02:00", Some(1704096000000.0)),
        ("2024-01-01T10:00:00-0530", Some(1704123000000.0)),
        ("2024-01-01T24:00:00", Some(1704153600000.0)),
        ("2024-01-01T24:00:01", None),
        ("2024-02-31", Some(1709337600000.0)),
        ("2024-13-01", None),
        ("2024-1-1", Some(1704067200000.0)),
        ("2024-01-01 10:00", Some(1704103200000.0)),
        ("2024-01-01 10:00 PST", Some(1704132000000.0)),
        ("2024-01-01 10:00 pm", Some(1704146400000.0)),
        ("2024-01-01 12:00 AM", Some(1704067200000.0)),
        ("2024/10/15", Some(1728950400000.0)),
        ("2024/10/15 10:30", Some(1728988200000.0)),
        ("10/15/2024", Some(1728950400000.0)),
        ("10/15", Some(1003104000000.0)),
        ("10/15/24", Some(1728950400000.0)),
        ("10/15/99", Some(939945600000.0)),
        ("10/32/2024", None),
        ("Oct 15, 2024", Some(1728950400000.0)),
        ("15 October 2024", Some(1728950400000.0)),
        ("Oct 15", Some(1003104000000.0)),
        ("Oct 2024", Some(1727740800000.0)),
        ("2024 Oct 15", Some(1728950400000.0)),
        ("Octxyz 15 2024", Some(1728950400000.0)),
        ("Mon, 15 Oct 2024 10:00:00 GMT", Some(1728986400000.0)),
        ("Mon Oct 15 2024 10:00:00 GMT+0200 (CEST)", Some(1728979200000.0)),
        ("Oct 15, 2024 12:30 AM", Some(1728952200000.0)),
        ("Oct 15, 2024 13:30 PM", None),
        ("soon", None),
        ("", None),
        ("abc 12", Some(1007164800000.0)),
        ("12 abc", None),
        ("2024-01-01abc", None),
        ("2024-01-01T", None),
        ("2024-01-01T10:60", None),
        ("-000001-01-01", Some(-62198755200000.0)),
        ("+275760-09-13T00:00:00.000Z", Some(8640000000000000.0)),
        ("+275760-09-13T00:00:00.001Z", None),
        ("0099-01-01", Some(-59042995200000.0)),
        ("99-01-01", Some(915148800000.0)),
        ("1-2-3", Some(1041465600000.0)),
        ("20240101", None),
        ("Jan 1 2024 10:00 GMT-8", Some(1704132000000.0)),
        ("1/1/2024 10:00:00.250", Some(1704103200250.0)),
        ("2024-01-01(comment)", Some(1704067200000.0)),
        ("Dec 25, 49", Some(2524003200000.0)),
        ("Dec 25, 50", Some(-600220800000.0)),
        ("12:30pm Oct 15 2024", None),
        ("2024-Oct-15", Some(1728950400000.0)),
        ("15/Oct/2024", Some(1728950400000.0)),
        ];
        for (text, want) in rows {
            assert_eq!(parse_ms(text), *want, "{text:?}");
        }
    }

    #[test]
    fn days_are_fractional_past_midnight() {
        assert_eq!(parse_days("2024-01-01T12:00:00Z"), Some(19723.5));
        assert_eq!(parse_days("soon"), None);
    }
}
