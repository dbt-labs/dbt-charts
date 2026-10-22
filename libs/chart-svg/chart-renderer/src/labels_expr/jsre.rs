//! ECMAScript regular expressions on `fancy-regex`: the pattern is translated so the classes and anchors mean
//! what they mean in a JavaScript RegExp (`\w \d \b` ASCII, `\s` the ECMAScript white space, `.` and the `m`
//! anchors over the four line terminators, `\1` before its group a legacy octal escape).

use fancy_regex::{Regex, RegexBuilder};

use super::val::{Bounded, EvalError, Res, Text};

/// How many backtracking steps a match may take. V8 has no limit and answers an expensive pattern in seconds; past
/// this, the engine refuses rather than say there is no match.
const BACKTRACK_LIMIT: usize = 25_000_000;

const WORD: &str = "A-Za-z0-9_";
const SPACE: &str = "\\t\\n\\x0B\\x0C\\r \\x{A0}\\x{1680}\\x{2000}-\\x{200A}\\x{2028}\\x{2029}\\x{202F}\\x{205F}\\x{3000}\\x{FEFF}";
const LINE: &str = "\\n\\r\\x{2028}\\x{2029}";

pub struct JsRegex {
    re: Regex,
    pub global: bool,
    sticky: bool,
    /// `u`/`v`: the pattern and text are code points; otherwise they are UTF-16 code units.
    unicode: bool,
    /// Group names by 1-based index.
    names: Vec<Option<String>>,
}

#[derive(Debug, Clone)]
pub struct Match {
    pub start: usize,
    pub end: usize,
    /// Capture groups 1..n: byte ranges, `None` when the group did not participate.
    pub groups: Vec<Option<(usize, usize)>>,
}

/// A UTF-16 code unit as one `char`: surrogate halves, which `char` cannot hold, go to the plane-16 private use
/// area, so a non-Unicode RegExp sees an astral character as the two units JavaScript sees.
fn unit_char(unit: u32) -> char {
    if (0xD800..0xE000).contains(&unit) {
        char::from_u32(0x10_0000 + unit).unwrap_or('\u{fffd}')
    } else {
        char::from_u32(unit).unwrap_or('\u{fffd}')
    }
}

fn to_units(s: &str) -> String {
    s.chars()
        .flat_map(|c| {
            let mut buf = [0u16; 2];
            c.encode_utf16(&mut buf).iter().map(|u| unit_char(u32::from(*u))).collect::<Vec<_>>()
        })
        .collect()
}

fn from_units(s: &str) -> String {
    let units: Vec<u16> = s
        .chars()
        .map(|c| if (0x10_D800..0x10_E000).contains(&(c as u32)) { (c as u32 - 0x10_0000) as u16 } else { 0 })
        .collect();
    let mut out = String::new();
    let mut i = 0;
    let chars: Vec<char> = s.chars().collect();
    while i < chars.len() {
        let c = chars[i];
        if (0x10_D800..0x10_E000).contains(&(c as u32)) {
            let hi = units[i];
            if (0xD800..0xDC00).contains(&hi) && i + 1 < chars.len() && (0x10_DC00..0x10_E000).contains(&(chars[i + 1] as u32)) {
                let lo = units[i + 1];
                out.push(char::from_u32(0x10000 + ((u32::from(hi) - 0xD800) << 10) + (u32::from(lo) - 0xDC00)).unwrap_or('\u{fffd}'));
                i += 2;
                continue;
            }
            out.push('\u{fffd}');
        } else {
            out.push(c);
        }
        i += 1;
    }
    out
}

fn lit(c: char) -> String {
    format!("\\x{{{:X}}}", c as u32)
}

struct Translator {
    chars: Vec<char>,
    at: usize,
    unicode: bool,
    ignore_case: bool,
    dot_all: bool,
    multiline: bool,
    ngroups: usize,
}

fn count_groups(chars: &[char]) -> (usize, Vec<Option<String>>) {
    let (mut n, mut names, mut i, mut in_class) = (0, Vec::new(), 0, false);
    while i < chars.len() {
        match chars[i] {
            '\\' => i += 1,
            '[' => in_class = true,
            ']' => in_class = false,
            '(' if !in_class => {
                if chars.get(i + 1) != Some(&'?') {
                    n += 1;
                    names.push(None);
                } else if chars.get(i + 2) == Some(&'<') && !matches!(chars.get(i + 3), Some('=' | '!')) {
                    n += 1;
                    let name: String = chars[i + 3..].iter().take_while(|c| **c != '>').collect();
                    names.push(Some(name));
                }
            }
            _ => {}
        }
        i += 1;
    }
    (n, names)
}

impl Translator {
    fn peek(&self) -> Option<char> {
        self.chars.get(self.at).copied()
    }

    fn syntax<T>(&self, what: &str) -> Result<T, Failure> {
        Err(Failure::Syntax(format!("Invalid regular expression: {what}")))
    }

    fn hex(&mut self, n: usize) -> Option<u32> {
        let s: String = self.chars.get(self.at..self.at + n)?.iter().collect();
        let v = u32::from_str_radix(&s, 16).ok().filter(|_| s.chars().all(|c| c.is_ascii_hexdigit()))?;
        self.at += n;
        Some(v)
    }

    /// The character an escape (after the backslash) denotes, shared by atoms and class members.
    fn char_escape(&mut self, c: char, in_class: bool) -> Result<char, Failure> {
        Ok(match c {
            't' => '\t',
            'n' => '\n',
            'v' => '\u{b}',
            'f' => '\u{c}',
            'r' => '\r',
            '0' if !self.peek().is_some_and(|d| d.is_ascii_digit()) => '\0',
            'b' if in_class => '\u{8}',
            '-' if in_class => '-',
            'c' => match self.peek() {
                Some(l) if l.is_ascii_alphabetic() => {
                    self.at += 1;
                    char::from_u32(l as u32 % 32).unwrap_or('\0')
                }
                _ => {
                    self.at -= 1;
                    '\\'
                }
            },
            'x' => {
                let save = self.at;
                match self.hex(2) {
                    Some(v) => char::from_u32(v).unwrap_or('\u{fffd}'),
                    None if self.unicode => return self.syntax("Invalid escape"),
                    None => {
                        self.at = save;
                        'x'
                    }
                }
            }
            'u' => {
                let save = self.at;
                if self.unicode && self.peek() == Some('{') {
                    self.at += 1;
                    let start = self.at;
                    while self.peek().is_some_and(|c| c.is_ascii_hexdigit()) {
                        self.at += 1;
                    }
                    let digits: String = self.chars[start..self.at].iter().collect();
                    if self.peek() != Some('}') || digits.is_empty() {
                        return self.syntax("Invalid Unicode escape");
                    }
                    self.at += 1;
                    return u32::from_str_radix(&digits, 16)
                        .ok()
                        .and_then(char::from_u32)
                        .map_or_else(|| self.syntax("Invalid Unicode escape"), Ok);
                }
                match self.hex(4) {
                    Some(hi) if self.unicode && (0xD800..0xDC00).contains(&hi) && self.peek() == Some('\\') && self.chars.get(self.at + 1) == Some(&'u') => {
                        let before = self.at;
                        self.at += 2;
                        match self.hex(4) {
                            Some(lo) if (0xDC00..0xE000).contains(&lo) => {
                                char::from_u32(0x10000 + ((hi - 0xD800) << 10) + (lo - 0xDC00)).unwrap_or('\u{fffd}')
                            }
                            _ => {
                                self.at = before;
                                unit_char(hi)
                            }
                        }
                    }
                    Some(v) => unit_char(v),
                    None if self.unicode => return self.syntax("Invalid Unicode escape"),
                    None => {
                        self.at = save;
                        'u'
                    }
                }
            }
            c if c.is_ascii_digit() && !self.unicode => {
                // Annex B legacy octal escape.
                let mut v = c.to_digit(8).unwrap_or(0);
                if c > '7' {
                    return Ok(c);
                }
                let mut n = 1;
                while n < 3 && self.peek().is_some_and(|d| d.is_digit(8)) && v * 8 + self.peek().and_then(|d| d.to_digit(8)).unwrap_or(0) < 256 {
                    v = v * 8 + self.peek().and_then(|d| d.to_digit(8)).unwrap_or(0);
                    self.at += 1;
                    n += 1;
                }
                char::from_u32(v).unwrap_or('\0')
            }
            c if self.unicode && !"^$\\.*+?()[]{}|/".contains(c) => return self.syntax("Invalid escape"),
            other => other,
        })
    }

    fn class_escape(&mut self, c: char) -> Option<String> {
        Some(match c {
            'd' => "0-9".into(),
            'D' => "[^0-9]".into(),
            'w' => WORD.into(),
            'W' => format!("[^{WORD}]"),
            's' => SPACE.into(),
            'S' => format!("[^{SPACE}]"),
            _ => return None,
        })
    }

    fn property(&mut self, negated: bool) -> Result<String, Failure> {
        if self.peek() != Some('{') {
            return self.syntax("Invalid property name");
        }
        let start = self.at;
        while self.peek().is_some_and(|c| c != '}') {
            self.at += 1;
        }
        let body: String = self.chars[start..self.at].iter().collect();
        self.at += 1;
        Ok(format!("\\{}{body}}}", if negated { 'P' } else { 'p' }))
    }

    fn class(&mut self) -> Result<String, Failure> {
        let negated = self.peek() == Some('^');
        if negated {
            self.at += 1;
        }
        let mut items = String::new();
        let mut closed = false;
        while let Some(c) = self.peek() {
            self.at += 1;
            if c == ']' {
                closed = true;
                break;
            }
            let atom: Option<char> = if c == '\\' {
                let Some(e) = self.peek() else { return self.syntax("\\ at end of pattern") };
                self.at += 1;
                if let Some(set) = self.class_escape(e) {
                    items.push_str(&set);
                    continue;
                }
                if self.unicode && matches!(e, 'p' | 'P') {
                    items.push_str(&self.property(e == 'P')?);
                    continue;
                }
                Some(self.char_escape(e, true)?)
            } else {
                Some(c)
            };
            let Some(a) = atom else { continue };
            if self.peek() == Some('-') && self.chars.get(self.at + 1).is_some_and(|n| *n != ']') {
                let save = self.at;
                self.at += 1;
                let hi = self.chars[self.at];
                self.at += 1;
                let hi = if hi == '\\' {
                    let e = self.peek().ok_or_else(|| Failure::Syntax("\\ at end of pattern".to_string()))?;
                    self.at += 1;
                    if self.class_escape(e).is_some() {
                        // `[a-\d]`: Annex B reads the dash literally.
                        self.at = save;
                        items.push_str(&lit(a));
                        continue;
                    }
                    self.char_escape(e, true)?
                } else {
                    hi
                };
                if hi < a {
                    return self.syntax("Range out of order in character class");
                }
                items.push_str(&format!("{}-{}", lit(a), lit(hi)));
                continue;
            }
            items.push_str(&lit(a));
        }
        if !closed {
            return self.syntax("Unterminated character class");
        }
        Ok(match (negated, items.is_empty()) {
            (false, true) => "(?!)".into(),
            (true, true) => "(?s:.)".into(),
            (false, false) => format!("[{items}]"),
            (true, false) => format!("[^{items}]"),
        })
    }

    fn quantifier_braces(&mut self) -> Option<String> {
        // {n}, {n,}, {n,m}
        let start = self.at;
        let mut i = self.at + 1;
        let digits = |i: &mut usize, chars: &[char]| {
            let s = *i;
            while chars.get(*i).is_some_and(char::is_ascii_digit) {
                *i += 1;
            }
            *i - s
        };
        if digits(&mut i, &self.chars) == 0 {
            return None;
        }
        if self.chars.get(i) == Some(&',') {
            i += 1;
            digits(&mut i, &self.chars);
        }
        if self.chars.get(i) != Some(&'}') {
            return None;
        }
        self.at = i + 1;
        Some(self.chars[start..self.at].iter().collect())
    }

    fn pattern(&mut self, depth: usize) -> Result<String, Failure> {
        if depth >= MAX_GROUP_DEPTH {
            return Err(Failure::TooDeep);
        }
        let mut out = String::new();
        let mut last_atom = false;
        while let Some(c) = self.peek() {
            self.at += 1;
            match c {
                ')' => {
                    if depth == 0 {
                        return self.syntax("Unmatched ')'");
                    }
                    self.at -= 1;
                    return Ok(out);
                }
                '|' => {
                    out.push('|');
                    last_atom = false;
                }
                '(' => {
                    let head = if self.peek() == Some('?') {
                        self.at += 1;
                        match self.peek() {
                            Some(':') => {
                                self.at += 1;
                                "(?:".to_string()
                            }
                            Some('=') => {
                                self.at += 1;
                                "(?=".into()
                            }
                            Some('!') => {
                                self.at += 1;
                                "(?!".into()
                            }
                            Some('<') => {
                                self.at += 1;
                                match self.peek() {
                                    Some('=') => {
                                        self.at += 1;
                                        "(?<=".into()
                                    }
                                    Some('!') => {
                                        self.at += 1;
                                        "(?<!".into()
                                    }
                                    _ => {
                                        let start = self.at;
                                        while self.peek().is_some_and(|c| c != '>') {
                                            self.at += 1;
                                        }
                                        let name: String = self.chars[start..self.at].iter().collect();
                                        self.at += 1;
                                        if name.is_empty() {
                                            return self.syntax("Invalid capture group name");
                                        }
                                        format!("(?P<{name}>")
                                    }
                                }
                            }
                            _ => return self.syntax("Invalid group"),
                        }
                    } else {
                        "(".to_string()
                    };
                    let inner = self.pattern(depth + 1)?;
                    if self.peek() != Some(')') {
                        return self.syntax("Unterminated group");
                    }
                    self.at += 1;
                    out.push_str(&head);
                    out.push_str(&inner);
                    out.push(')');
                    last_atom = true;
                }
                '[' => {
                    out.push_str(&self.class()?);
                    last_atom = true;
                }
                '.' => {
                    out.push_str(&if self.dot_all { "(?s:.)".to_string() } else { format!("[^{LINE}]") });
                    last_atom = true;
                }
                '^' => {
                    out.push_str(&if self.multiline { format!("(?:\\A|(?<=[{LINE}]))") } else { "\\A".to_string() });
                    last_atom = false;
                }
                '$' => {
                    out.push_str(&if self.multiline { format!("(?=[{LINE}]|\\z)") } else { "\\z".to_string() });
                    last_atom = false;
                }
                '*' | '+' | '?' => {
                    if !last_atom {
                        return self.syntax("Nothing to repeat");
                    }
                    out.push(c);
                    if self.peek() == Some('?') {
                        self.at += 1;
                        out.push('?');
                    }
                    last_atom = false;
                }
                '{' => {
                    let save = self.at - 1;
                    self.at = save;
                    match self.quantifier_braces() {
                        Some(q) => {
                            if !last_atom {
                                return self.syntax("Nothing to repeat");
                            }
                            out.push_str(&q);
                            if self.peek() == Some('?') {
                                self.at += 1;
                                out.push('?');
                            }
                            last_atom = false;
                        }
                        None if self.unicode => return self.syntax("Incomplete quantifier"),
                        None => {
                            self.at = save + 1;
                            out.push_str(&lit('{'));
                            last_atom = true;
                        }
                    }
                }
                '}' | ']' => {
                    if self.unicode {
                        return self.syntax("Lone quantifier brackets");
                    }
                    out.push_str(&lit(c));
                    last_atom = true;
                }
                '\\' => {
                    let Some(e) = self.peek() else { return self.syntax("\\ at end of pattern") };
                    self.at += 1;
                    last_atom = true;
                    match e {
                        'b' => {
                            out.push_str(&format!("(?:(?<=[{WORD}])(?![{WORD}])|(?<![{WORD}])(?=[{WORD}]))"));
                            last_atom = false;
                        }
                        'B' => {
                            out.push_str(&format!("(?:(?<=[{WORD}])(?=[{WORD}])|(?<![{WORD}])(?![{WORD}]))"));
                            last_atom = false;
                        }
                        'k' if self.peek() == Some('<') => {
                            let start = self.at + 1;
                            let mut j = start;
                            while self.chars.get(j).is_some_and(|c| *c != '>') {
                                j += 1;
                            }
                            let name: String = self.chars[start..j].iter().collect();
                            self.at = j + 1;
                            out.push_str(&format!("\\k<{name}>"));
                        }
                        'p' | 'P' if self.unicode => out.push_str(&self.property(e == 'P')?),
                        '1'..='9' => {
                            let start = self.at - 1;
                            let mut j = self.at;
                            while self.chars.get(j).is_some_and(char::is_ascii_digit) {
                                j += 1;
                            }
                            let n: usize = self.chars[start..j].iter().collect::<String>().parse().unwrap_or(usize::MAX);
                            if n <= self.ngroups {
                                self.at = j;
                                out.push_str(&format!("(?({n})\\{n}|)"));
                            } else if self.unicode {
                                return self.syntax("Invalid escape");
                            } else {
                                let ch = self.char_escape(e, false)?;
                                out.push_str(&lit(ch));
                            }
                        }
                        _ => {
                            if let Some(set) = self.class_escape(e) {
                                out.push_str(&if set.starts_with('[') { set } else { format!("[{set}]") });
                            } else {
                                let ch = self.char_escape(e, false)?;
                                out.push_str(&lit(ch));
                            }
                        }
                    }
                }
                other => {
                    out.push_str(&lit(other));
                    last_atom = true;
                }
            }
        }
        if depth > 0 {
            return self.syntax("Unterminated group");
        }
        Ok(out)
    }
}

/// Why the translation of a pattern stopped.
enum Failure {
    Syntax(String),
    TooDeep,
}

/// How many groups may nest. V8 compiles tens of thousands; the regex engine below nests 250 at most and this
/// translation recurses on the caller's stack.
const MAX_GROUP_DEPTH: usize = 100;

/// Why a regexp did not compile.
#[derive(Debug)]
pub enum CompileError {
    /// The `SyntaxError` message V8 gives it.
    Syntax(String),
    /// V8 compiles it; this translation does not.
    Unmodeled(EvalError),
}

impl CompileError {
    /// The failure as the evaluator reports it: a syntax error is `syntax` (a throw at runtime, a compile error
    /// for a literal).
    pub fn into_eval(self, syntax: fn(String) -> EvalError) -> EvalError {
        match self {
            CompileError::Syntax(message) => syntax(message),
            CompileError::Unmodeled(refusal) => refusal,
        }
    }
}

/// The longest pattern compiled. V8 compiles a regexp when it first runs it and throws "Regular expression too
/// large" past a size that depends on what the pattern holds: it already does at about 4000 characters of capture
/// groups, and not before a million of `\\b`. A pattern past this length may be either, so it is refused.
const MAX_PATTERN_UNITS: usize = 4000;

/// The most memory the compiled program and its lazy DFA may take.
const COMPILED_SIZE_LIMIT: usize = 16 << 20;

/// `new RegExp(source, flags)`.
pub fn compile(source: &str, flags: &str) -> Result<JsRegex, CompileError> {
    if super::val::utf16_len(source) > MAX_PATTERN_UNITS {
        return Err(CompileError::Unmodeled(EvalError::Unmodeled(crate::refuse!("labels.expr compiling a regular expression longer than {MAX_PATTERN_UNITS} characters is not supported"))));
    }
    // Compiling builds the program and its tables: about a kilobyte for each byte of the pattern.
    super::val::charge(source.len().saturating_mul(1024)).map_err(CompileError::Unmodeled)?;
    let mut seen = String::new();
    for f in flags.chars() {
        if !"dgimsuvy".contains(f) || seen.contains(f) {
            return Err(CompileError::Syntax(format!("Invalid flags supplied to RegExp constructor '{flags}'")));
        }
        seen.push(f);
    }
    let unicode = flags.contains('u') || flags.contains('v');
    let chars: Vec<char> = if unicode { source.chars().collect() } else { to_units(source).chars().collect() };
    let (ngroups, names) = count_groups(&chars);
    let mut t = Translator {
        chars,
        at: 0,
        unicode,
        ignore_case: flags.contains('i'),
        dot_all: flags.contains('s'),
        multiline: flags.contains('m'),
        ngroups,
    };
    let body = t.pattern(0).map_err(|failure| match failure {
        Failure::Syntax(message) => CompileError::Syntax(message),
        Failure::TooDeep => CompileError::Unmodeled(EvalError::Unmodeled(crate::refuse!("labels.expr compiling a regular expression with groups nested past {MAX_GROUP_DEPTH} levels is not supported"))),
    })?;
    let ignore_case = t.ignore_case;
    let pattern = if ignore_case { format!("(?i){body}") } else { body };
    let re = RegexBuilder::new(&pattern)
        .backtrack_limit(BACKTRACK_LIMIT)
        .delegate_size_limit(COMPILED_SIZE_LIMIT)
        .delegate_dfa_size_limit(COMPILED_SIZE_LIMIT)
        .build()
        .map_err(|e| match e {
            // The translated pattern is valid by construction, so the engine fails to build it only for its size.
            fancy_regex::Error::CompileError(_) => CompileError::Unmodeled(EvalError::Unmodeled(crate::refuse!("labels.expr compiling a regular expression whose compiled program passes {COMPILED_SIZE_LIMIT} bytes is not supported"))),
            other => CompileError::Syntax(format!("Invalid regular expression: /{source}/: {other}")),
        })?;
    Ok(JsRegex { re, global: flags.contains('g'), sticky: flags.contains('y'), unicode, names })
}

impl JsRegex {
    /// The text as this regexp indexes it: UTF-16 code units unless it is a Unicode regexp.
    pub fn prepare(&self, text: &str) -> Res<String> {
        // A surrogate half becomes a four-byte character, so a text can double.
        super::val::charge(text.len().saturating_mul(2))?;
        Ok(if self.unicode { text.to_string() } else { to_units(text) })
    }

    /// The inverse of [`Self::prepare`].
    pub fn restore(&self, text: &str) -> Res<String> {
        // `from_units` holds the text as characters (4 bytes) and units (2 bytes) beside the result.
        super::val::charge(text.len().saturating_mul(7))?;
        Ok(if self.unicode { text.to_string() } else { from_units(text) })
    }

    /// The first match at or after byte `from` (only at `from` when sticky).
    pub fn find_from(&self, text: &str, from: usize) -> Res<Option<Match>> {
        let found = self
            .re
            .captures_from_pos(text, from)
            .map_err(|_| EvalError::Unmodeled(crate::refuse!("labels.expr matching a regular expression that backtracks past {BACKTRACK_LIMIT} steps is not supported")))?;
        let Some(caps) = found else { return Ok(None) };
        let Some(whole) = caps.get(0) else { return Ok(None) };
        if self.sticky && whole.start() != from {
            return Ok(None);
        }
        Ok(Some(Match {
            start: whole.start(),
            end: whole.end(),
            groups: (1..=self.names.len()).map(|i| caps.get(i).map(|m| (m.start(), m.end()))).collect(),
        }))
    }

    pub fn is_match(&self, text: &str) -> Res<bool> {
        Ok(self.find_from(text, 0)?.is_some())
    }

    /// Every match a `g` regexp walks (one when it is not global), JS `lastIndex` stepping included.
    pub fn matches(&self, text: &str) -> Res<Vec<Match>> {
        let mut out = Vec::new();
        let mut at = 0;
        while at <= text.len() {
            let Some(m) = self.find_from(text, at)? else { break };
            at = if m.end == m.start { next_char(text, m.end) } else { m.end };
            // A match and its group spans, in a vector that doubles.
            super::val::charge(96 + m.groups.len() * 32)?;
            out.push(m);
            if !self.global {
                break;
            }
        }
        Ok(out)
    }

    fn group_by_name(&self, name: &str) -> Option<usize> {
        self.names.iter().position(|n| n.as_deref() == Some(name)).map(|i| i + 1)
    }
}

fn next_char(text: &str, at: usize) -> usize {
    text[at.min(text.len())..].chars().next().map_or(text.len() + 1, |c| at + c.len_utf8())
}

/// `GetSubstitution` of `String.prototype.replace`.
pub fn substitute(template: &str, text: &str, m: &Match, re: Option<&JsRegex>) -> Res<Text> {
    super::val::charge(template.len().saturating_mul(4))?;
    let chars: Vec<char> = template.chars().collect();
    let mut out = Bounded::default();
    let mut i = 0;
    let group = |n: usize| m.groups.get(n - 1).copied().flatten().map_or("", |(a, b)| &text[a..b]);
    while i < chars.len() {
        if chars[i] != '$' || i + 1 >= chars.len() {
            out.push(chars[i])?;
            i += 1;
            continue;
        }
        let n = chars[i + 1];
        match n {
            '$' => {
                out.push('$')?;
                i += 2;
            }
            '&' => {
                out.push_str(&text[m.start..m.end])?;
                i += 2;
            }
            '`' => {
                out.push_str(&text[..m.start])?;
                i += 2;
            }
            '\'' => {
                out.push_str(&text[m.end..])?;
                i += 2;
            }
            '0'..='9' => {
                let d1 = n.to_digit(10).unwrap_or(0) as usize;
                let d2 = chars.get(i + 2).and_then(|c| c.to_digit(10)).map(|d| d1 * 10 + d as usize);
                let count = m.groups.len();
                if let Some(two) = d2.filter(|v| (1..=count).contains(v)) {
                    out.push_str(group(two))?;
                    i += 3;
                } else if (1..=count).contains(&d1) {
                    out.push_str(group(d1))?;
                    i += 2;
                } else {
                    out.push('$')?;
                    i += 1;
                }
            }
            '<' => {
                let named = re.is_some_and(|r| r.names.iter().any(Option::is_some));
                match (named, chars[i + 2..].iter().position(|c| *c == '>')) {
                    (true, Some(close)) => {
                        let name: String = chars[i + 2..i + 2 + close].iter().collect();
                        if let Some(g) = re.and_then(|r| r.group_by_name(&name)) {
                            out.push_str(group(g))?;
                        }
                        i += 3 + close;
                    }
                    _ => {
                        out.push('$')?;
                        i += 1;
                    }
                }
            }
            _ => {
                out.push('$')?;
                i += 1;
            }
        }
    }
    Ok(out.finish())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn replace_all(src: &str, flags: &str, text: &str, repl: &str) -> String {
        let re = compile(src, flags).unwrap();
        let (text, repl) = (re.prepare(text).unwrap(), re.prepare(repl).unwrap());
        let mut out = String::new();
        let mut last = 0;
        for m in re.matches(&text).unwrap() {
            out.push_str(&text[last..m.start]);
            out.push_str(&substitute(&repl, &text, &m, Some(&re)).unwrap());
            last = m.end;
        }
        re.restore(&(out + &text[last..])).unwrap()
    }

    #[test]
    fn classes_are_ascii_the_way_a_non_unicode_regexp_reads_them() {
        assert_eq!(replace_all(r"\W", "g", "Zürich 7", ""), "Zrich7");
        assert_eq!(replace_all(r"\w+", "g", "Zürich", "<$&>"), "<Z>ü<rich>");
        assert_eq!(replace_all(r"\d", "g", "a٣1", "#"), "a٣#");
        assert_eq!(replace_all(r"\s", "g", "a\u{a0}b c\u{2003}d", "_"), "a_b_c_d");
        assert_eq!(replace_all(r"\bé", "g", "é aé", "x"), "é ax");
        assert_eq!(replace_all(r"\b", "g", "ab cd", "|"), "|ab| |cd|");
        assert_eq!(replace_all(r"[\W_]", "g", "a_b-c", ""), "abc");
    }

    #[test]
    fn dot_and_line_anchors_follow_the_four_line_terminators() {
        assert_eq!(replace_all(".", "g", "a\nb\u{2028}c", "-"), "-\n-\u{2028}-");
        assert_eq!(replace_all(".", "gs", "a\nb", "-"), "---");
        assert_eq!(replace_all("^", "gm", "a\rb\nc", ">"), ">a\r>b\n>c");
        assert_eq!(replace_all("$", "m", "a\nb", "<"), "a<\nb");
        assert_eq!(replace_all("^a", "g", "a\na", "x"), "x\na");
    }

    #[test]
    fn lookaround_backreferences_and_named_groups_work() {
        assert_eq!(replace_all(r"(?<=\d)(?=(\d{3})+$)", "g", "1234567", ","), "1,234,567");
        assert_eq!(replace_all(r"(a)\1", "g", "aab", "X"), "Xb");
        assert_eq!(replace_all(r"(?<y>\d{4})-(?<m>\d\d)", "", "2020-01", "$<m>/$<y>"), "01/2020");
        assert_eq!(replace_all(r"a(?!b)", "g", "abac", "X"), "abXc");
    }

    #[test]
    fn legacy_syntax_is_read_as_annex_b_reads_it() {
        assert_eq!(replace_all(r"a{", "", "a{", "X"), "X");
        assert_eq!(replace_all(r"\1(a)", "", "\u{1}a", "X"), "\u{1}X");
        assert_eq!(replace_all(r"]", "", "a]", "X"), "aX");
        assert_eq!(replace_all(r"[\b]", "", "a\u{8}", "X"), "aX");
        assert_eq!(replace_all(r"\x41B\cJ", "", "AB\n", "X"), "X");
        assert_eq!(replace_all(r"[^]", "g", "a\nb", "."), "...");
        assert!(!compile(r"[]", "").unwrap().is_match("a").unwrap());
    }

    #[test]
    fn flags_and_syntax_errors() {
        assert!(replace_all("A", "i", "a", "x") == "x");
        assert!(compile("(", "").is_err());
        assert!(compile("a", "gg").is_err());
        assert!(compile("*", "").is_err());
        assert!(compile("[b-a]", "").is_err());
        assert_eq!(replace_all("a*", "g", "baac", "-"), "-b--c-");
        assert_eq!(replace_all("^", "y", "ab", "x"), "xab");
    }

    #[test]
    fn replacement_templates() {
        assert_eq!(replace_all("(b)", "", "abc", "[$1|$2|$&|$`|$'|$$|$0]"), "a[b|$2|b|a|c|$|$0]c");
        assert_eq!(replace_all("(a)(b)(c)(d)(e)(f)(g)(h)(i)(j)(k)", "", "abcdefghijk", "$11-$1"), "k-a");
    }
}
