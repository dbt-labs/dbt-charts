//! JavaScript values and the language's own coercions (ECMAScript 2024 abstract operations), as Vega's
//! generated expression code runs them.

use super::date;

#[derive(Debug, Clone, PartialEq)]
pub enum Val {
    Undefined,
    Null,
    Bool(bool),
    Num(f64),
    Str(Text),
    /// A `Date`: epoch milliseconds, NaN for an invalid date.
    Date(f64),
    /// A regular expression object: its source and flags.
    Regex(String, String),
    Array(Vec<Val>),
    Object(Vec<(String, Val)>),
    /// A d3 color object.
    Color(super::colors::Color),
    /// A function value, known by the source `Function.prototype.toString` gives.
    Func(String),
}

/// Why an evaluation failed.
#[derive(Debug, Clone, PartialEq)]
pub enum EvalError {
    /// The generated code throws at run time (`TypeError`, a bad `RegExp`, ...): Vega aborts the view.
    Throws(String),
    /// Vega's compiler rejects the expression: an unknown function, signal, or a syntax error.
    Unknown(String),
    /// Vega state bound to the view that this engine does not model (`item.bounds`, `item.mark.items`, a derived
    /// dataset, ...): the `refuse!` message that names it.
    Unmodeled(String),
}

pub type Res<T> = Result<T, EvalError>;

/// V8's longest string, in UTF-16 units: building a longer one throws `RangeError: Invalid string length`.
const V8_STRING_UNITS: usize = (1 << 29) - 24;

/// The memory one evaluation may account for, in the bytes this engine holds (a `Val` is [`ELEMENT_BYTES`] and
/// text is its length). V8's old space defaults to a quarter of the host's memory up to 4 GiB (4192 MiB on the
/// machine this was measured on), so a worker of 8 GiB or more has at least 2 GiB; this is an eighth of that floor,
/// because the evaluator copies a value at most a few times per node while V8 packs a number in 8 to 16 bytes.
const V8_EVAL_BUDGET: usize = 256 << 20;

/// What one `Val` costs, whatever it holds: the enum (40 bytes) plus the slack of an array that grows by doubling.
pub const ELEMENT_BYTES: usize = 64;

/// What a string's heap allocation costs beyond its bytes.
const STRING_SLACK: usize = 32;

/// A date's bytes: it prints as `Mon Jan 05 2020 00:00:00 GMT+0000 (Coordinated Universal Time)`, 63 units.
const DATE_BYTES: usize = 80;

#[cfg(test)]
thread_local! {
    static TEST_LIMITS: std::cell::Cell<Option<(usize, usize)>> = const { std::cell::Cell::new(None) };
}

/// The longest string a builder may make: V8's, or a test's smaller one on this thread.
pub fn max_string_units() -> usize {
    #[cfg(test)]
    if let Some((units, _)) = TEST_LIMITS.get() {
        return units;
    }
    V8_STRING_UNITS
}

fn eval_budget() -> usize {
    #[cfg(test)]
    if let Some((_, budget)) = TEST_LIMITS.get() {
        return budget;
    }
    V8_EVAL_BUDGET
}

/// Lowers the string limit and the evaluation budget on this thread until the guard drops, so a test reaches each
/// builder's limit with small inputs.
#[cfg(test)]
pub fn small_limits(units: usize, budget: usize) -> impl Drop {
    struct Restore;
    impl Drop for Restore {
        fn drop(&mut self) {
            TEST_LIMITS.set(None);
        }
    }
    TEST_LIMITS.set(Some((units, budget)));
    Restore
}

thread_local! {
    /// The bytes the running evaluation has accounted for, or `None` outside one.
    static USED: std::cell::Cell<Option<usize>> = const { std::cell::Cell::new(None) };
}

/// Holds an evaluation open: the budget counts from zero until it drops.
pub struct Evaluation;

impl Evaluation {
    pub fn begin() -> Evaluation {
        USED.set(Some(0));
        Evaluation
    }
}

impl Drop for Evaluation {
    fn drop(&mut self) {
        USED.set(None);
    }
}

fn over_budget() -> EvalError {
    EvalError::Unmodeled(crate::refuse!("labels.expr using more than {V8_EVAL_BUDGET} bytes of memory in one evaluation is not supported"))
}

/// Fails if `bytes` more would pass the evaluation's budget, before anything is allocated for them.
pub fn reserve(bytes: usize) -> Res<()> {
    match USED.get() {
        Some(used) if used.saturating_add(bytes) > eval_budget() => Err(over_budget()),
        _ => Ok(()),
    }
}

/// Counts `bytes` against the evaluation's budget.
pub fn charge(bytes: usize) -> Res<()> {
    reserve(bytes)?;
    USED.set(USED.get().map(|used| used + bytes));
    Ok(())
}

/// The UTF-16 length of `s`, which is what `String.prototype.length` counts.
pub fn utf16_len(s: &str) -> usize {
    if s.is_ascii() { s.len() } else { s.encode_utf16().count() }
}

/// Fails as V8 does when a string of `units` UTF-16 units is asked for. Every builder checks its result's length
/// here before it allocates.
pub fn string_fits(units: f64) -> Res<()> {
    if units > max_string_units() as f64 {
        return throws("Invalid string length");
    }
    Ok(())
}

/// For a builder about to allocate `bytes` for a string of `units` UTF-16 units: V8's length first, then the bytes
/// against the evaluation's budget. The buffer a builder fills, and each copy it makes on the way, are charged.
pub fn building(units: f64, bytes: f64) -> Res<()> {
    string_fits(units)?;
    charge(bytes.min(usize::MAX as f64) as usize)
}

/// The bytes the evaluation may still account for.
pub fn remaining() -> usize {
    USED.get().map_or(eval_budget(), |used| eval_budget().saturating_sub(used))
}

/// A JavaScript string value. The field is private to this module: a `Text` only comes out of [`Text::new`], which
/// refuses a string past V8's limit, out of a [`Bounded`] builder, or from a literal, so no `Val::Str` can hold
/// more than a script could make.
#[derive(Debug, Clone, PartialEq)]
pub struct Text(String);

impl Text {
    pub fn new(s: String) -> Res<Text> {
        string_fits(utf16_len(&s) as f64)?;
        Ok(Text(s))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn into_string(self) -> String {
        self.0
    }
}

impl std::fmt::Display for Text {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl Text {
    /// The string form of a value the evaluator holds. Every value it holds was charged its [`Val::weight`] against
    /// the evaluation's budget, which is under V8's string limit, and a string form is shorter than the weight; only
    /// `ToPrimitive` calls it.
    fn stringified(s: String) -> Text {
        Text(s)
    }
}

impl From<&'static str> for Text {
    fn from(s: &'static str) -> Text {
        Text(s.to_owned())
    }
}

impl Text {
    /// The string of one UTF-16 code unit.
    pub fn unit(u: u16) -> Text {
        Text(String::from_utf16_lossy(&[u]))
    }
}

impl From<char> for Text {
    fn from(c: char) -> Text {
        Text(c.to_string())
    }
}

impl std::ops::Deref for Text {
    type Target = str;
    fn deref(&self) -> &str {
        &self.0
    }
}

impl PartialEq<str> for Text {
    fn eq(&self, other: &str) -> bool {
        self.0 == other
    }
}

impl PartialEq<&str> for Text {
    fn eq(&self, other: &&str) -> bool {
        self.0 == *other
    }
}

/// The one way to build a string a script controls the size of: it checks the running length before each push, so
/// nothing is allocated past V8's limit.
#[derive(Default)]
pub struct Bounded {
    text: String,
    units: usize,
}

impl Bounded {
    pub fn push_str(&mut self, s: &str) -> Res<()> {
        self.units = self.units.saturating_add(utf16_len(s));
        string_fits(self.units as f64)?;
        self.grow_for(s.len())?;
        self.text.push_str(s);
        Ok(())
    }

    pub fn push(&mut self, c: char) -> Res<()> {
        self.units += c.len_utf16();
        string_fits(self.units as f64)?;
        self.grow_for(c.len_utf8())?;
        self.text.push(c);
        Ok(())
    }

    /// Makes room for `extra` more bytes, charging the new buffer and the old one it is copied out of, which are both
    /// live while it moves.
    fn grow_for(&mut self, extra: usize) -> Res<()> {
        let need = self.text.len().saturating_add(extra);
        let capacity = self.text.capacity();
        if need > capacity {
            let grown = need.max(capacity.saturating_mul(2));
            charge(grown.saturating_add(capacity))?;
            self.text.reserve_exact(grown - self.text.len());
        }
        Ok(())
    }

    pub fn finish(self) -> Text {
        Text(self.text)
    }
}

pub fn throws<T>(what: impl Into<String>) -> Res<T> {
    Err(EvalError::Throws(what.into()))
}

/// ECMAScript WhiteSpace and LineTerminator.
pub fn is_js_space(c: char) -> bool {
    matches!(
        c,
        '\t' | '\n' | '\u{b}' | '\u{c}' | '\r' | ' ' | '\u{a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}'
            | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}' | '\u{feff}'
    )
}

pub fn trim_js(s: &str) -> &str {
    s.trim_matches(is_js_space)
}

/// `StringToNumber`.
pub fn str_to_num(s: &str) -> f64 {
    let t = trim_js(s);
    if t.is_empty() {
        return 0.0;
    }
    for (prefix, radix) in [("0x", 16), ("0X", 16), ("0o", 8), ("0O", 8), ("0b", 2), ("0B", 2)] {
        if let Some(digits) = t.strip_prefix(prefix) {
            if digits.is_empty() || !digits.chars().all(|c| c.is_digit(radix)) {
                return f64::NAN;
            }
            return digits.chars().fold(0.0, |acc, c| acc * f64::from(radix) + f64::from(c.to_digit(radix).unwrap_or(0)));
        }
    }
    let (sign, body) = match t.as_bytes()[0] {
        b'+' => (1.0, &t[1..]),
        b'-' => (-1.0, &t[1..]),
        _ => (1.0, t),
    };
    if body == "Infinity" {
        return sign * f64::INFINITY;
    }
    let b = body.as_bytes();
    let mut i = 0;
    let digits = |i: &mut usize| {
        let start = *i;
        while *i < b.len() && b[*i].is_ascii_digit() {
            *i += 1;
        }
        *i - start
    };
    let int = digits(&mut i);
    let mut frac = 0;
    if i < b.len() && b[i] == b'.' {
        i += 1;
        frac = digits(&mut i);
    }
    if int + frac == 0 {
        return f64::NAN;
    }
    if i < b.len() && (b[i] == b'e' || b[i] == b'E') {
        i += 1;
        if i < b.len() && (b[i] == b'+' || b[i] == b'-') {
            i += 1;
        }
        if digits(&mut i) == 0 {
            return f64::NAN;
        }
    }
    if i != b.len() {
        return f64::NAN;
    }
    body.parse::<f64>().map_or(f64::NAN, |n| sign * n)
}

/// `Number::toString(10)`: the shortest round-trip digits, laid out the way ECMAScript 6.1.6.1.20 prescribes.
pub fn num_to_str(n: f64) -> String {
    if n.is_nan() {
        return "NaN".into();
    }
    if n == 0.0 {
        return "0".into();
    }
    if n.is_infinite() {
        return if n > 0.0 { "Infinity" } else { "-Infinity" }.into();
    }
    let sci = format!("{:e}", n.abs());
    let (mantissa, exp) = sci.split_once('e').expect("a float prints in scientific form");
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let e: i32 = exp.parse().expect("an exponent");
    let k = digits.len() as i32;
    let point = e + 1;
    let body = if k <= point && point <= 21 {
        format!("{digits}{}", "0".repeat((point - k) as usize))
    } else if 0 < point && point <= 21 {
        format!("{}.{}", &digits[..point as usize], &digits[point as usize..])
    } else if -6 < point && point <= 0 {
        format!("0.{}{digits}", "0".repeat((-point) as usize))
    } else {
        let sign = if point - 1 < 0 { '-' } else { '+' };
        let power = (point - 1).abs();
        if k == 1 { format!("{digits}e{sign}{power}") } else { format!("{}.{}e{sign}{power}", &digits[..1], &digits[1..]) }
    };
    if n < 0.0 { format!("-{body}") } else { body }
}

/// `RegExp.prototype.source`: the pattern escaped so it reads back as a literal.
pub fn regex_source(source: &str) -> String {
    if source.is_empty() {
        return "(?:)".into();
    }
    let mut out = String::new();
    let mut in_class = false;
    let mut chars = source.chars();
    while let Some(c) = chars.next() {
        match c {
            '\\' => {
                out.push(c);
                if let Some(n) = chars.next() {
                    out.push(n);
                }
            }
            '[' => {
                in_class = true;
                out.push(c);
            }
            ']' => {
                in_class = false;
                out.push(c);
            }
            '/' if !in_class => out.push_str("\\/"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\u{2028}' => out.push_str("\\u2028"),
            '\u{2029}' => out.push_str("\\u2029"),
            other => out.push(other),
        }
    }
    out
}

/// A RegExp's flags in the canonical `dgimsuvy` order.
pub fn sorted_flags(flags: &str) -> String {
    "dgimsuvy".chars().filter(|c| flags.contains(*c)).collect()
}

pub fn to_int32(n: f64) -> i32 {
    if !n.is_finite() {
        return 0;
    }
    (n.trunc().rem_euclid(4_294_967_296.0) as u32) as i32
}

#[derive(Clone, Copy, PartialEq)]
pub enum Hint {
    Default,
    Number,
    String,
}

impl Val {
    pub fn truthy(&self) -> bool {
        match self {
            Val::Undefined | Val::Null => false,
            Val::Bool(b) => *b,
            Val::Num(n) => *n != 0.0 && !n.is_nan(),
            Val::Str(s) => !s.is_empty(),
            Val::Date(_) | Val::Regex(..) | Val::Array(_) | Val::Object(_) | Val::Color(_) | Val::Func(_) => true,
        }
    }

    pub fn is_object(&self) -> bool {
        matches!(self, Val::Date(_) | Val::Regex(..) | Val::Array(_) | Val::Object(_) | Val::Color(_) | Val::Func(_))
    }

    /// `ToPrimitive`: a Date answers a number for the `number` hint and its string otherwise; the other objects
    /// answer their string (`valueOf` returns the object itself).
    pub fn to_primitive(&self, hint: Hint) -> Val {
        match self {
            Val::Date(ms) if hint == Hint::Number => Val::Num(*ms),
            Val::Date(_) | Val::Regex(..) | Val::Array(_) | Val::Object(_) | Val::Color(_) | Val::Func(_) => Val::Str(Text::stringified(self.to_js_string())),
            other => other.clone(),
        }
    }

    pub fn to_number(&self) -> f64 {
        match self.to_primitive(Hint::Number) {
            Val::Undefined => f64::NAN,
            Val::Null => 0.0,
            Val::Bool(b) => f64::from(b),
            Val::Num(n) => n,
            Val::Str(s) => str_to_num(&s),
            _ => f64::NAN,
        }
    }

    /// `ToString`.
    pub fn to_js_string(&self) -> String {
        match self {
            Val::Undefined => "undefined".into(),
            Val::Null => "null".into(),
            Val::Bool(b) => b.to_string(),
            Val::Num(n) => num_to_str(*n),
            Val::Str(s) => s.as_str().to_owned(),
            Val::Date(ms) => date::to_date_string(*ms),
            Val::Regex(source, flags) => format!("/{}/{}", regex_source(source), sorted_flags(flags)),
            Val::Array(items) => {
                let mut out = String::new();
                for (i, v) in items.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    if !matches!(v, Val::Undefined | Val::Null) {
                        out.push_str(&v.to_js_string());
                    }
                }
                out
            }
            Val::Object(_) => "[object Object]".into(),
            Val::Color(c) => c.to_css(),
            Val::Func(source) => source.clone(),
        }
    }

    pub fn strict_eq(&self, other: &Val) -> bool {
        match (self, other) {
            (Val::Undefined, Val::Undefined) | (Val::Null, Val::Null) => true,
            (Val::Bool(a), Val::Bool(b)) => a == b,
            (Val::Num(a), Val::Num(b)) => a == b,
            (Val::Str(a), Val::Str(b)) => a == b,
            // Objects compare by identity. Only the tick's own objects (`datum`, `datum.value`, `item`) can be met
            // twice, and the evaluator answers for those where it sees the operands ([`super::Node::ref_path`]).
            _ => false,
        }
    }

    /// `IsLooselyEqual` (`==`).
    pub fn loose_eq(&self, other: &Val) -> bool {
        use Val::*;
        match (self, other) {
            (Undefined | Null, Undefined | Null) => true,
            (Undefined | Null, _) | (_, Undefined | Null) => false,
            (a, b) if a.is_object() && b.is_object() => a.strict_eq(b),
            (Num(_), Str(s)) => self.strict_eq(&Num(str_to_num(s))),
            (Str(s), Num(_)) => Num(str_to_num(s)).strict_eq(other),
            (Bool(b), x) => Num(f64::from(*b)).loose_eq(x),
            (x, Bool(b)) => x.loose_eq(&Num(f64::from(*b))),
            (a, b) if a.is_object() => a.to_primitive(Hint::Default).loose_eq(b),
            (a, b) if b.is_object() => a.loose_eq(&b.to_primitive(Hint::Default)),
            (a, b) => a.strict_eq(b),
        }
    }

    /// The string value of `s`, or the error V8 throws for one it cannot make.
    pub fn string(s: String) -> Res<Val> {
        Text::new(s).map(Val::Str)
    }

    /// [`Self::to_primitive`] that borrows a value which already is one, so a string is not copied to be read.
    fn primitive(&self, hint: Hint) -> std::borrow::Cow<'_, Val> {
        if self.is_object() { std::borrow::Cow::Owned(self.to_primitive(hint)) } else { std::borrow::Cow::Borrowed(self) }
    }

    /// `ToString` of a primitive, borrowing a string's own text.
    fn text_of(&self) -> Res<std::borrow::Cow<'_, str>> {
        Ok(match self {
            Val::Str(s) => std::borrow::Cow::Borrowed(s.as_str()),
            other => std::borrow::Cow::Owned(other.to_text()?),
        })
    }

    /// `'' + a`: the string a value coerces to.
    pub fn to_text(&self) -> Res<String> {
        // An array or object prints into a buffer that grows by doubling; a string is copied.
        charge(if let Val::Str(s) = self { s.len() } else { self.weight().saturating_mul(2) })?;
        Ok(self.to_primitive(Hint::Default).to_js_string())
    }

    /// `a + b`.
    pub fn add(&self, other: &Val) -> Res<Val> {
        let (l, r) = (self.primitive(Hint::Default), other.primitive(Hint::Default));
        if matches!(*l, Val::Str(_)) || matches!(*r, Val::Str(_)) {
            let (l, r) = (l.text_of()?, r.text_of()?);
            building((utf16_len(&l) + utf16_len(&r)) as f64, (l.len() + r.len()) as f64)?;
            let mut joined = String::with_capacity(l.len() + r.len());
            joined.push_str(&l);
            joined.push_str(&r);
            Ok(Val::Str(Text::new(joined)?))
        } else {
            Ok(Val::Num(l.to_number() + r.to_number()))
        }
    }

    /// The bytes this value takes, which an evaluation charges for it. A value's string form is always shorter than
    /// its weight: a number prints in under [`ELEMENT_BYTES`] units, a date in [`DATE_BYTES`] and a separator is
    /// one of the forty.
    pub fn weight(&self) -> usize {
        match self {
            // The text's own allocation, which the allocator rounds up and heads.
            Val::Str(s) => ELEMENT_BYTES + STRING_SLACK + s.len(),
            Val::Func(s) => ELEMENT_BYTES + s.len(),
            Val::Regex(source, flags) => ELEMENT_BYTES + source.len() + flags.len(),
            Val::Date(_) => DATE_BYTES,
            Val::Array(items) => ELEMENT_BYTES + items.iter().map(Val::weight).sum::<usize>(),
            Val::Object(props) => ELEMENT_BYTES + props.iter().map(|(k, v)| 24 + STRING_SLACK + k.len() + v.weight()).sum::<usize>(),
            _ => ELEMENT_BYTES,
        }
    }

    /// `IsLessThan` of `a < b` with the left operand evaluated first: `None` is `undefined` (a NaN operand).
    pub fn less_than(&self, other: &Val) -> Option<bool> {
        let (l, r) = (self.primitive(Hint::Number), other.primitive(Hint::Number));
        if let (Val::Str(a), Val::Str(b)) = (&*l, &*r) {
            return Some(a.encode_utf16().lt(b.encode_utf16()));
        }
        let (a, b) = (l.to_number(), r.to_number());
        if a.is_nan() || b.is_nan() { None } else { Some(a < b) }
    }
}

/// JS `%`: the dividend's sign, `NaN` for a zero or NaN divisor.
pub fn js_rem(a: f64, b: f64) -> f64 {
    if b == 0.0 || a.is_infinite() || a.is_nan() || b.is_nan() {
        f64::NAN
    } else if b.is_infinite() {
        a
    } else {
        a % b
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numbers_print_as_ecmascript_prints_them() {
        for (n, s) in [
            (0.0, "0"),
            (-0.0, "0"),
            (1.5, "1.5"),
            (100.0, "100"),
            (1e21, "1e+21"),
            (123456789012345680000.0, "123456789012345680000"),
            (1.5e-7, "1.5e-7"),
            (0.000001, "0.000001"),
            (0.1 + 0.2, "0.30000000000000004"),
            (-1234.5, "-1234.5"),
            (f64::INFINITY, "Infinity"),
        ] {
            assert_eq!(num_to_str(n), s, "{n:?}");
        }
    }

    #[test]
    fn strings_read_as_numbers_the_way_ecmascript_reads_them() {
        for (s, n) in [("", 0.0), ("  12  ", 12.0), ("0x1F", 31.0), ("1e3", 1000.0), (".5", 0.5), ("5.", 5.0), ("-Infinity", f64::NEG_INFINITY), ("\u{a0}7\u{feff}", 7.0)] {
            assert_eq!(str_to_num(s), n, "{s:?}");
        }
        for s in ["abc", "1 2", "0x", "+0x10", "--1", "1e", "inf", "nan", "1_000", "Infinityx"] {
            assert!(str_to_num(s).is_nan(), "{s:?}");
        }
    }

    #[test]
    fn loose_equality_follows_abstract_equality() {
        use Val::*;
        let eq = |a: Val, b: Val| a.loose_eq(&b);
        assert!(eq(Str("0".into()), Num(0.0)));
        assert!(eq(Str("".into()), Num(0.0)));
        assert!(eq(Null, Undefined));
        assert!(!eq(Null, Num(0.0)));
        assert!(eq(Bool(true), Str("1".into())));
        assert!(eq(Array(vec![Num(1.0)]), Str("1".into())));
        assert!(!eq(Str("a".into()), Str("A".into())));
        assert!(!eq(Num(f64::NAN), Num(f64::NAN)));
        assert!(eq(Str("0".into()), Bool(false)));
    }

    #[test]
    fn plus_concatenates_when_either_primitive_is_a_string() {
        use Val::*;
        assert_eq!(Str("a".into()).add(&Num(1.0)), Ok(Str("a1".into())));
        assert_eq!(Num(1.0).add(&Num(2.0)), Ok(Num(3.0)));
        assert_eq!(Array(vec![Num(1.0), Num(2.0)]).add(&Num(1.0)), Ok(Str("1,21".into())));
        assert_eq!(Null.add(&Num(1.0)), Ok(Num(1.0)));
        assert!(matches!(Undefined.add(&Num(1.0)), Ok(Num(n)) if n.is_nan()));
    }
}
