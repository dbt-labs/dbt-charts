//! Python 3.13 `re` semantics for a `str` pattern on the `regex` crate: the
//! one place the ported Pygments lexers' regexes are translated.
//!
//! A Pygments rule is `re.compile(pattern, flags).match(text, pos)`. The
//! `regex` crate differs in these ways, each absorbed here and pinned by the
//! tests below and the differential in `tests/test_mdsvg_highlight.py`:
//!
//! - `match(text, pos)` anchors at `pos` but sees the whole string. The rule
//!   matches `text[pos..]` anchored at `\A`; leading `\A`, `^`, `(?<!\.)` and
//!   `\b` are conditions on `text[..pos]`, checked by [`Pattern::find`].
//!   Python's `\A` is the start of `text`, never of `pos`.
//! - `\w`, `\d` are `str` classes (`str.isalnum() or '_'`, `str.isdecimal()`),
//!   generated from CPython's Unicode tables; `\s` also covers U+001C-001F
//!   (`str.isspace`).
//! - A trailing `(?=X)` is not supported: `P(?P<t>X)` matches the same `P`
//!   (the lookahead only filters `P`'s backtracking paths and `regex` prefers
//!   paths in Python's order) and the rule keeps the end of `P`, the start of
//!   group `t`. `(?!\s|$)` is "a non-space character follows".
//! - A trailing `\b` after a pattern whose last character is a word character
//!   is "no word character follows", the same tail. `(\s*)\b` after one
//!   (Bash `case` keywords) is `(\s+)` then a word character, or no
//!   whitespace then a non-word character or the end: a shorter `\s*` leaves
//!   whitespace on both sides of the boundary.
//! - `(?!X)(\s*)` at the end, X one class with no whitespace (Bash keywords),
//!   is `(\s+)`, or no whitespace then the end or a character outside X.
//! - A trailing `(?!X)` is checked at the end of the match: exact when the
//!   head has one match, which both rules using it (`python.py:222-228`,
//!   `shell.py:110`) satisfy.
//! - `re.IGNORECASE` is not the `regex` crate's `(?i)`: a class with no cased
//!   member is not folded at all (`sre_compile.py`), so `[^\w]` still matches
//!   U+0345, and `i`, `k`, `s` also take U+0130/U+0131, U+212A, U+017F. The
//!   crate's flag is never set; every ASCII letter becomes the class of the
//!   characters Python equates to it (`ASCII_IGNORECASE`, generated). A
//!   non-ASCII literal or a `\x`, `\u` escape under IGNORECASE has no such
//!   table and is refused; so is any `re` flag but IGNORECASE, MULTILINE,
//!   DOTALL. Without MULTILINE a leading `^` is `\A`.
//! - `{,n}` is `{0,n}`; a `[` or `&`/`~` inside a class is a literal.
//! - Inline flags are hoisted: Python 3.11+ allows them only at the start.

use std::iter::Peekable;
use std::str::Chars;

use once_cell::sync::Lazy;
use regex::{Captures, Regex};

use crate::pygments_lexers::{ASCII_IGNORECASE, PY_DIGIT, PY_WORD};

fn in_ranges(ranges: &[(char, char)], c: char) -> bool {
    ranges
        .binary_search_by(|&(lo, hi)| {
            if c < lo {
                std::cmp::Ordering::Greater
            } else if c > hi {
                std::cmp::Ordering::Less
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .is_ok()
}

/// Python's `\w` for one character.
pub fn is_py_word(c: char) -> bool {
    in_ranges(PY_WORD, c)
}

fn class_body(ranges: &[(char, char)]) -> String {
    ranges
        .iter()
        .map(|&(lo, hi)| format!("\\x{{{:x}}}-\\x{{{:x}}}", lo as u32, hi as u32))
        .collect()
}

static WORD: Lazy<String> = Lazy::new(|| class_body(PY_WORD));
static DIGIT: Lazy<String> = Lazy::new(|| class_body(PY_DIGIT));

/// The `re` flags a lexer or an inline `(?x)` sets.
#[derive(Clone, Copy, Default)]
pub struct Flags {
    pub ignore_case: bool,
    pub multiline: bool,
    pub dot_all: bool,
}

impl Flags {
    /// From `re.RegexFlag` bits: IGNORECASE 2, MULTILINE 8, DOTALL 16 (and
    /// UNICODE 32, the `str` default). Any other flag, such as VERBOSE,
    /// changes how the pattern parses and is refused.
    pub fn from_bits(bits: u32) -> Flags {
        assert!(bits & !(2 | 8 | 16 | 32) == 0, "re flag bits {bits:#x}");
        Flags {
            ignore_case: bits & 2 != 0,
            multiline: bits & 8 != 0,
            dot_all: bits & 16 != 0,
        }
    }

    fn prefix(self) -> String {
        let mut on = String::new();
        for (flag, letter) in [(self.multiline, 'm'), (self.dot_all, 's')] {
            if flag {
                on.push(letter);
            }
        }
        if on.is_empty() {
            String::new()
        } else {
            format!("(?{on})")
        }
    }
}

enum Matcher {
    Re(Regex),
    /// A rule the `regex` crate cannot express: the end of the match at `pos`.
    Custom(fn(&str, usize) -> Option<usize>),
}

/// One compiled rule pattern: [`Pattern::find`] is `re.match(text, pos)`.
pub struct Pattern {
    matcher: Matcher,
    at_start: bool,
    line_start: bool,
    not_after_dot: bool,
    not_after_word: bool,
    /// `(?!X)` at the end of the pattern.
    deny: Option<Regex>,
}

/// A match at `pos`: its end and capture groups, numbered as in Python.
pub struct Hit<'t> {
    text: &'t str,
    base: usize,
    caps: Option<Captures<'t>>,
    pub end: usize,
}

impl<'t> Hit<'t> {
    /// Group `i`'s byte span in the whole text; `None` when it did not take
    /// part in the match.
    pub fn group(&self, i: usize) -> Option<(usize, usize)> {
        let m = self.caps.as_ref()?.get(i)?;
        Some((self.base + m.start(), self.base + m.end()))
    }

    pub fn group_str(&self, i: usize) -> Option<&'t str> {
        self.group(i).map(|(s, e)| &self.text[s..e])
    }
}

impl Pattern {
    pub fn custom(f: fn(&str, usize) -> Option<usize>) -> Pattern {
        Pattern {
            matcher: Matcher::Custom(f),
            at_start: false,
            line_start: false,
            not_after_dot: false,
            not_after_word: false,
            deny: None,
        }
    }

    /// Compile the Python pattern `py` under a lexer's `flags`. A construct
    /// this translation does not carry panics: it is a port error, found by
    /// the first test that builds the lexer.
    pub fn new(py: &str, flags: Flags) -> Pattern {
        let mut flags = flags;
        let mut rest = py;
        while let Some(inline) = leading_flags(rest) {
            for letter in inline[2..inline.len() - 1].chars() {
                match letter {
                    'i' => flags.ignore_case = true,
                    's' => flags.dot_all = true,
                    _ => panic!("inline flag `{letter}` in {py}"),
                }
            }
            rest = &rest[inline.len()..];
        }

        let (mut at_start, mut line_start) = (false, false);
        let (mut not_after_dot, mut not_after_word) = (false, false);
        let mut opens = String::new();
        loop {
            if let Some(r) = rest.strip_prefix(r"\A") {
                at_start = true;
                rest = r;
            } else if let Some(r) = rest.strip_prefix('^') {
                // Without MULTILINE `^` is the start of `text`, like `\A`.
                if flags.multiline {
                    line_start = true;
                } else {
                    at_start = true;
                }
                rest = r;
            } else if let Some(r) = rest.strip_prefix(r"(?<!\.)") {
                not_after_dot = true;
                rest = r;
            } else if let Some(r) = rest.strip_prefix(r"\b") {
                assert!(edge_is_word(r, false), "\\b before a non-word start: {py}");
                not_after_word = true;
                rest = r;
            } else if let Some(r) = rest.strip_prefix('(').filter(|r| !r.starts_with('?')) {
                opens.push('(');
                rest = r;
            } else {
                break;
            }
        }
        let body = hoist_boundary(format!("{opens}{rest}"));

        let (body, deny) = match trailing_group(&body) {
            Some(g) if g.starts_with("(?!") && g != r"(?!\s|$)" => {
                let deny = format!(
                    "{}\\A(?:{})",
                    flags.prefix(),
                    translate(&g[3..g.len() - 1], flags)
                );
                (
                    &body[..body.len() - g.len()],
                    Some(Regex::new(&deny).unwrap_or_else(|e| panic!("{py}: {e}"))),
                )
            }
            _ => (body.as_str(), None),
        };

        let (head, tail) = split_tail(body, flags);
        let re = format!("{}\\A(?:{}){tail}", flags.prefix(), translate(head, flags));
        let re = Regex::new(&re).unwrap_or_else(|e| panic!("{py}: {e}"));
        Pattern {
            matcher: Matcher::Re(re),
            at_start,
            line_start,
            not_after_dot,
            not_after_word,
            deny,
        }
    }

    pub fn find<'t>(&self, text: &'t str, pos: usize) -> Option<Hit<'t>> {
        let before = &text[..pos];
        if self.at_start && pos != 0
            || self.line_start && pos > 0 && !before.ends_with('\n')
            || self.not_after_dot && before.ends_with('.')
            || self.not_after_word && before.chars().next_back().is_some_and(is_py_word)
        {
            return None;
        }
        let hit = match &self.matcher {
            Matcher::Custom(f) => Hit {
                text,
                base: pos,
                caps: None,
                end: f(text, pos)?,
            },
            Matcher::Re(re) => {
                let caps = re.captures(&text[pos..])?;
                let whole = caps.get(0).expect("group 0 always participates");
                let end = match caps.name("t").or_else(|| caps.name("u")) {
                    Some(tail) => tail.start(),
                    None => whole.end(),
                };
                Hit {
                    text,
                    base: pos,
                    end: pos + end,
                    caps: Some(caps),
                }
            }
        };
        match &self.deny {
            Some(deny) if deny.is_match(&text[hit.end..]) => None,
            _ => Some(hit),
        }
    }
}

/// `X(Y\b)` -> `X(Y)\b` when the capture group is the pattern's last element:
/// a zero-width assertion at the end of the group is one at the end of the
/// match, and the tail then keeps its peeked character out of the group.
fn hoist_boundary(body: String) -> String {
    match trailing_group(&body) {
        Some(g) if g.starts_with('(') && !g.starts_with("(?") && g.ends_with(r"\b)") => {
            let head = &body[..body.len() - g.len()];
            format!(r"{head}{})\b", &g[..g.len() - 3])
        }
        _ => body,
    }
}

/// A leading inline flag group such as `(?i)` or `(?s)`.
fn leading_flags(py: &str) -> Option<&str> {
    let inner = py.strip_prefix("(?")?;
    let close = inner.find(')')?;
    (close > 0 && inner[..close].chars().all(|c| c.is_ascii_lowercase())).then(|| &py[..close + 3])
}

/// The last top-level `( ... )` group, when the pattern ends with one.
fn trailing_group(py: &str) -> Option<&str> {
    if !py.ends_with(')') {
        return None;
    }
    let mut starts: Vec<usize> = Vec::new();
    let mut last_top = None;
    let mut in_class = false;
    let mut skip = false;
    for (i, c) in py.char_indices() {
        if skip {
            skip = false;
            continue;
        }
        match c {
            '\\' => skip = true,
            '[' if !in_class => in_class = true,
            ']' if in_class => in_class = false,
            '(' if !in_class => starts.push(i),
            ')' if !in_class => {
                let s = starts.pop().expect("unbalanced pattern");
                if starts.is_empty() && i + 1 == py.len() {
                    last_top = Some(s);
                }
            }
            _ => {}
        }
    }
    last_top.map(|s| &py[s..])
}

/// Split the tail assertion off `body`, returning the head to translate and
/// the regex text of the tail (empty when there is none).
fn split_tail(body: &str, flags: Flags) -> (&str, String) {
    let word = &*WORD;
    if let Some(g) = trailing_group(body) {
        let head = &body[..body.len() - g.len()];
        if g == r"(?!\s|$)" {
            return (head, r"(?P<t>[^\s\x1c-\x1f])".to_string());
        }
        if let Some(la) = g.strip_prefix("(?=") {
            return (
                head,
                format!("(?P<t>{})", translate(&la[..la.len() - 1], flags)),
            );
        }
        assert!(
            !g.starts_with("(?!") && !g.starts_with("(?<"),
            "lookaround in {body}"
        );
    }
    if let Some(before) = body.strip_suffix(r"(\s*)") {
        if let Some(g) = trailing_group(before).filter(|g| g.starts_with("(?!")) {
            let set = &g[3..g.len() - 1];
            assert!(
                set.starts_with('[') && !set.starts_with("[^") && set.ends_with(']'),
                "`(?!X)(\\s*)` with X not one class: {body}"
            );
            let set = translate(set, flags);
            let space = Regex::new(&set).expect("translated class");
            assert!(
                !space.is_match(" ") && !space.is_match("\n"),
                "`(?!X)(\\s*)` with whitespace in X: {body}"
            );
            let tail = format!(r"(?:([\s\x1c-\x1f]+)|(?P<u>\z|[^{set}]))");
            return (&before[..before.len() - g.len()], tail);
        }
    }
    let Some(head) = body.strip_suffix(r"\b") else {
        return (body, String::new());
    };
    if let Some(before) = head.strip_suffix(r"(\s*)") {
        assert!(
            edge_is_word(before, true),
            "\\b after a non-word end: {body}"
        );
        let tail = format!(r"(?:([\s\x1c-\x1f]+)(?P<t>[{word}])|(?P<u>\z|[^{word}]))");
        return (before, tail);
    }
    assert!(edge_is_word(head, true), "\\b after a non-word end: {body}");
    (head, format!(r"(?P<t>\z|[^{word}])"))
}

/// Rewrite Python `str` pattern text for the `regex` crate (module docs).
fn translate(py: &str, flags: Flags) -> String {
    let mut out = String::new();
    let mut chars = py.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '\\' => {
                let e = chars.next().expect("dangling escape");
                assert_ignore_case_escape(e, flags);
                if e == 'b' {
                    let head =
                        &py[..py.len() - chars.clone().map(char::len_utf8).sum::<usize>() - 2];
                    let rest: String = chars.clone().collect();
                    out.push_str(&inner_boundary(head, &rest));
                } else {
                    out.push_str(&escape(e, false));
                }
            }
            '[' => out.push_str(&class(&mut chars, flags)),
            '(' if chars.peek() == Some(&'?') => {
                chars.next();
                match chars.next() {
                    Some(':') => out.push_str("(?:"),
                    other => panic!("unsupported group `(?{other:?}` in {py}"),
                }
            }
            '{' if chars.peek() == Some(&',') => out.push_str("{0"),
            '^' => panic!("`^` past the start of {py}"),
            '$' => {
                assert!(flags.multiline, "`$` without MULTILINE in {py}");
                out.push('$');
            }
            c if flags.ignore_case && !c.is_ascii() => {
                panic!("non-ASCII literal `{c}` under IGNORECASE in {py}")
            }
            c if flags.ignore_case && c.is_ascii_alphabetic() => {
                out.push('[');
                out.push_str(&same_letter(c));
                out.push(']');
            }
            _ => out.push(c),
        }
    }
    out
}

/// A `\b` inside a lookahead body, after `head`, before `rest`. As the tail of
/// [`split_tail`] it is "no word character follows" when the atom before it
/// ends in one; consuming that character is harmless only when nothing but
/// closing parentheses follow.
fn inner_boundary(head: &str, rest: &str) -> String {
    assert!(rest.chars().all(|c| c == ')'), "\\b before `{rest}`");
    assert!(
        edge_is_word(last_atom(head), true),
        "\\b after a non-word end"
    );
    format!(r"(?:\z|[^{}])", *WORD)
}

/// The last atom of the innermost open sequence of `head`: a character, an
/// escape, a class or a parenthesized group, without its quantifier.
fn last_atom(head: &str) -> &str {
    let mut open: Vec<Option<usize>> = vec![None];
    let mut chars = head.char_indices();
    while let Some((i, c)) = chars.next() {
        let top = open.len() - 1;
        match c {
            '\\' => {
                open[top] = Some(i);
                chars.next();
            }
            '[' => {
                open[top] = Some(i);
                let mut first = true;
                while let Some((_, q)) = chars.next() {
                    match q {
                        '\\' => {
                            chars.next();
                        }
                        ']' if !first => break,
                        _ => {}
                    }
                    first = false;
                }
            }
            '(' => {
                open[top] = Some(i);
                open.push(None);
            }
            ')' => {
                open.pop();
            }
            '|' => open[top] = None,
            '?' | '*' | '+' => {}
            _ => open[top] = Some(i),
        }
    }
    let at = open.last().copied().flatten().expect("an atom before \\b");
    &head[at..]
}

/// `\xHH`, `\uHHHH` and `\UHHHHHHHH` name a character the letter table of
/// [`same_letter`] would not fold.
fn assert_ignore_case_escape(e: char, flags: Flags) {
    assert!(
        !(flags.ignore_case && matches!(e, 'x' | 'u' | 'U')),
        "`\\{e}` escape under IGNORECASE"
    );
}

fn escape(e: char, in_class: bool) -> String {
    let (word, digit) = (&*WORD, &*DIGIT);
    let space = r"\s\x1c-\x1f";
    let set = |body: &str, negated: bool| match (in_class, negated) {
        (true, false) => body.to_string(),
        (false, false) => format!("[{body}]"),
        (_, true) => format!("[^{body}]"),
    };
    match e {
        's' => set(space, false),
        'S' => set(space, true),
        'w' => set(word, false),
        'W' => set(word, true),
        'd' => set(digit, false),
        'D' => set(digit, true),
        '<' | '>' => e.to_string(),
        'b' | 'B' | 'A' | 'Z' | '1'..='9' => panic!("unsupported escape \\{e}"),
        _ => format!("\\{e}"),
    }
}

/// Every character `re.IGNORECASE` equates to the ASCII letter `c`, as class
/// members.
fn same_letter(c: char) -> String {
    let (_, same) = ASCII_IGNORECASE
        .iter()
        .find(|(letter, _)| *letter == c)
        .expect("every ASCII letter is in the table");
    same.chars()
        .map(|s| format!("\\x{{{:x}}}", s as u32))
        .collect()
}

/// A `[...]` class, `chars` just past its `[`.
fn class(chars: &mut Peekable<Chars<'_>>, flags: Flags) -> String {
    /// Record a literal `c` (closing the range `prev-c` when one is open) and
    /// the ASCII letters it covers.
    fn literal(c: char, prev: &mut Option<char>, open: &mut bool, letters: &mut Vec<char>) {
        let lo = if *open { prev.unwrap_or(c) } else { c };
        letters.extend((lo..=c).filter(char::is_ascii_alphabetic));
        *prev = Some(c);
        *open = false;
    }

    let mut out = String::from("[");
    let negated = chars.next_if_eq(&'^').is_some();
    if negated {
        out.push('^');
    }
    let (mut first, mut prev, mut open, mut category) = (true, None, false, false);
    let mut letters = Vec::new();
    loop {
        let c = chars.next().expect("unterminated class");
        match c {
            ']' if !first => break,
            '\\' => {
                let e = chars.next().expect("dangling escape");
                assert_ignore_case_escape(e, flags);
                out.push_str(&escape(e, true));
                if "sSwWdD".contains(e) {
                    category = true;
                    prev = None;
                } else {
                    literal(e, &mut prev, &mut open, &mut letters);
                }
            }
            '[' | ']' | '&' | '~' => {
                out.push('\\');
                out.push(c);
                literal(c, &mut prev, &mut open, &mut letters);
            }
            '-' if prev.is_some() && chars.peek() != Some(&']') => {
                out.push('-');
                open = true;
            }
            _ => {
                assert!(
                    c.is_ascii() || !flags.ignore_case,
                    "non-ASCII class member under IGNORECASE"
                );
                out.push(c);
                literal(c, &mut prev, &mut open, &mut letters);
            }
        }
        first = false;
    }
    if flags.ignore_case && !letters.is_empty() {
        assert!(
            !negated && !category,
            "a negated or category class naming letters under IGNORECASE"
        );
        letters.sort_unstable();
        letters.dedup();
        out.extend(letters.into_iter().map(same_letter));
    }
    out.push(']');
    out
}

/// What a `\b` needs to know about a pattern's first or last character.
enum Atom {
    Word,
    NonWord,
    /// Zero width: `\b`.
    Zero,
    /// A construct the analysis does not read; it never proves a word edge.
    Unknown,
    Group(Vec<Vec<Item>>),
}

struct Item {
    atom: Atom,
    optional: bool,
}

fn parse_alts(chars: &mut Peekable<Chars<'_>>) -> Vec<Vec<Item>> {
    let mut alts = vec![Vec::new()];
    while let Some(&c) = chars.peek() {
        if c == ')' {
            break;
        }
        chars.next();
        let atom = match c {
            '|' => {
                alts.push(Vec::new());
                continue;
            }
            '(' => {
                let plain = chars.next_if_eq(&'?').is_none();
                if plain || chars.next_if_eq(&':').is_some() {
                    let inner = parse_alts(chars);
                    chars.next();
                    Atom::Group(inner)
                } else {
                    parse_alts(chars);
                    chars.next();
                    Atom::Unknown
                }
            }
            '\\' => match chars.next().expect("dangling escape") {
                'd' | 'w' => Atom::Word,
                's' | 'W' => Atom::NonWord,
                'b' | 'B' | 'A' | 'Z' => Atom::Zero,
                'S' | 'D' => Atom::Unknown,
                e => literal_atom(e),
            },
            '[' => word_class(chars),
            '.' | '^' | '$' => Atom::Unknown,
            _ => literal_atom(c),
        };
        let mut optional = false;
        match chars.peek() {
            Some('?' | '*') => {
                optional = true;
                chars.next();
            }
            Some('+') => {
                chars.next();
            }
            Some('{') => {
                chars.next();
                optional = matches!(chars.peek(), Some('0' | ','));
                while chars.next().is_some_and(|q| q != '}') {}
            }
            _ => {}
        }
        chars.next_if_eq(&'?');
        alts.last_mut()
            .expect("never empty")
            .push(Item { atom, optional });
    }
    alts
}

/// A `[...]` class, `chars` just past its `[`: `Word` when it is not negated
/// and every member is a word character.
fn word_class(chars: &mut Peekable<Chars<'_>>) -> Atom {
    let negated = chars.next_if_eq(&'^').is_some();
    let (mut all_word, mut first, mut prev, mut range) = (!negated, true, None::<char>, false);
    loop {
        let c = chars.next().expect("unterminated class");
        let member = match c {
            ']' if !first => break,
            '\\' => match chars.next().expect("dangling escape") {
                'd' | 'w' => None,
                e if "sSWD".contains(e) => {
                    all_word = false;
                    None
                }
                e => Some(e),
            },
            '-' if prev.is_some() && chars.peek() != Some(&']') => {
                range = true;
                None
            }
            c => Some(c),
        };
        if let Some(m) = member {
            let lo = if range { prev.unwrap_or(m) } else { m };
            all_word &= (lo..=m).all(is_py_word);
            prev = Some(m);
            range = false;
        }
        first = false;
    }
    if all_word {
        Atom::Word
    } else {
        Atom::Unknown
    }
}

fn literal_atom(c: char) -> Atom {
    if is_py_word(c) {
        Atom::Word
    } else {
        Atom::NonWord
    }
}

/// `(every path's edge character is a word character, can match empty)`.
fn edge(alts: &[Vec<Item>], end: bool) -> (bool, bool) {
    let mut word = true;
    let mut nullable = false;
    for alt in alts {
        let (w, n) = seq_edge(alt, end);
        word &= w;
        nullable |= n;
    }
    (word, nullable)
}

fn seq_edge(items: &[Item], end: bool) -> (bool, bool) {
    let mut word = true;
    let ordered: Vec<&Item> = if end {
        items.iter().rev().collect()
    } else {
        items.iter().collect()
    };
    for item in ordered {
        let (w, n) = match &item.atom {
            Atom::Word => (true, false),
            Atom::NonWord | Atom::Unknown => (false, false),
            Atom::Zero => (true, true),
            Atom::Group(alts) => edge(alts, end),
        };
        word &= w;
        if !(n || item.optional) {
            return (word, false);
        }
    }
    (word, true)
}

/// Whether `py`'s first (`end == false`) or last (`end == true`) matched
/// character is always a word character and `py` cannot match empty: what
/// makes a `\b` beside it "no word character on the other side". `py` may
/// stop at an unmatched `)`.
fn edge_is_word(py: &str, end: bool) -> bool {
    let (word, nullable) = edge(&parse_alts(&mut py.chars().peekable()), end);
    word && !nullable
}

#[cfg(test)]
mod tests {
    use super::*;

    fn multiline() -> Flags {
        Flags::from_bits(8)
    }

    fn end_of(py: &str, flags: Flags, text: &str, pos: usize) -> Option<usize> {
        Pattern::new(py, flags).find(text, pos).map(|h| h.end)
    }

    #[test]
    fn word_class_is_pythons_not_the_crates() {
        assert!(is_py_word('_') && is_py_word('é') && is_py_word('٣'));
        assert!(!is_py_word('\u{300}') && !is_py_word('-'));
    }

    #[test]
    fn trailing_boundary_is_no_word_character_after() {
        let p = |t| end_of(r"(if|in)\b", multiline(), t, 0);
        assert_eq!(p("if x"), Some(2));
        assert_eq!(p("if"), Some(2));
        assert_eq!(p("ifx"), None);
        assert_eq!(p("if\u{e9}"), None);
        assert_eq!(p("if\u{300}"), Some(2));
    }

    #[test]
    fn boundary_after_whitespace_needs_a_word_or_no_whitespace() {
        let p = |t| end_of(r"\b(if|in)(\s*)\b", multiline(), t, 0);
        assert_eq!(p("if  x"), Some(4));
        assert_eq!(p("if  ("), Some(2));
        assert_eq!(p("if("), Some(2));
        assert_eq!(p("if  "), Some(2));
        assert_eq!(p("ifx"), None);
    }

    #[test]
    fn leading_conditions_read_the_text_before_pos() {
        let b = |t, pos| end_of(r"\b\w+", multiline(), t, pos);
        assert_eq!(b("a bc", 2), Some(4));
        assert_eq!(b("abc", 1), None);
        assert_eq!(end_of(r"(?<!\.)x", multiline(), "a.x x", 2), None);
        assert_eq!(end_of(r"(?<!\.)x", multiline(), "a.x x", 4), Some(5));
        assert_eq!(end_of(r"\Ax", multiline(), "xx", 1), None);
        assert_eq!(end_of(r"^x", multiline(), "a\nx", 2), Some(3));
        assert_eq!(end_of(r"(^[ ]*)x", multiline(), "a x", 1), None);
    }

    #[test]
    fn negative_lookahead_is_checked_at_the_end_of_the_head() {
        let p = |t| end_of(r"(^[ ]*)(m|c)\b(?![ ]*(?:[:,]|in\b))", multiline(), t, 0);
        assert_eq!(p("m x"), Some(1));
        assert_eq!(p("m : x"), None);
        assert_eq!(p("m in x"), None);
        assert_eq!(p("m ino x"), Some(1));
    }

    #[test]
    fn ignore_case_is_pythons_equivalence_not_the_crates_folding() {
        let ci = Flags::from_bits(2);
        assert_eq!(end_of(r"[^\w]", ci, "\u{345}", 0), Some(2));
        assert_eq!(end_of(r"[\w$]+", ci, "a\u{345}", 0), Some(1));
        assert_eq!(end_of("(?i)ab", Flags::default(), "AB", 0), Some(2));
        assert_eq!(end_of("in", ci, "\u{130}N", 0), Some(3));
        assert_eq!(end_of("in", ci, "\u{131}n", 0), Some(3));
        assert_eq!(end_of("[a-z_]+", ci, "x\u{130}\u{131}", 0), Some(5));
        assert_eq!(end_of("k", ci, "\u{212a}", 0), Some(3));
        assert_eq!(end_of("s", ci, "\u{17f}", 0), Some(2));
    }

    #[test]
    fn a_caret_without_multiline_is_the_start_of_the_text() {
        let ci = Flags::from_bits(2);
        assert_eq!(end_of("^x", ci, "a\nx", 2), None);
        assert_eq!(end_of("^x", ci, "x", 0), Some(1));
    }

    #[test]
    #[should_panic(expected = "re flag bits 0x40")]
    fn a_flag_that_changes_parsing_is_refused() {
        Flags::from_bits(64);
    }

    #[test]
    #[should_panic(expected = "non-ASCII literal")]
    fn a_non_ascii_literal_under_ignore_case_is_refused() {
        Pattern::new("\u{e9}", Flags::from_bits(2));
    }

    #[test]
    #[should_panic(expected = "escape under IGNORECASE")]
    fn a_hex_escape_under_ignore_case_is_refused() {
        Pattern::new(r"\x41", Flags::from_bits(2));
    }

    #[test]
    fn class_and_repetition_spellings() {
        let m = multiline();
        assert_eq!(end_of(r"[rR]{,2}x", m, "rRx", 0), Some(3));
        assert_eq!(end_of(r"[]{}:(),;[]+", m, "]{[", 0), Some(3));
        assert_eq!(end_of(r"[^\S\n]+", m, " \t\nx", 0), Some(2));
        assert_eq!(end_of(r"[\w\W]+", m, "a\n\u{300}", 0), Some(4));
        assert_eq!(end_of(r"[+&~-]+", m, "&~+-", 0), Some(4));
        assert_eq!(end_of(r"\d+", m, "\u{663}1x", 0), Some(3));
        assert_eq!(end_of(r"(?s)a.b", m, "a\nb", 0), Some(3));
        assert_eq!(end_of(r"a.b", m, "a\nb", 0), None);
        assert_eq!(end_of(r"#.*$", m, "# c\nx", 0), Some(3));
    }
}
