// Ported from libs/d3-format/src/d3_format/format.py and spec.py.
// Implements the subset of the d3-format grammar required by dbt charts
// theme/resolved payloads. Unsupported features (fill/align/width, b/o/x/X/e/n
// types) return Err — never a silently wrong string.
//
// Key d3 deviations from Rust's std formatting:
//   - Negative sign is the locale's minus (U+2212 MINUS SIGN in en-US), not
//     U+002D hyphen-minus.
//   - Rounding uses round-half-up (JavaScript Math.round semantics).
//   - SI type uses significant-digit precision, not decimal-place precision.

use crate::predefined_formats::{
    PredefinedSpec, PREDEFINED_SPECS, PREDEFINED_SUB_UNIT_FALLBACK, PREDEFINED_TIME_SPECS,
};

// SI prefix table; index 8 = "" (10^0), 9 = "k", etc.
const SI_PREFIXES: [&str; 17] = [
    "y", "z", "a", "f", "p", "n", "\u{00b5}", "m", "", "k", "M", "G", "T", "P", "E", "Z", "Y",
];
const SI_PREFIX_OFFSET: i32 = 8;

/// d3-format's thousands grouping (`locale.grouping` + `locale.thousands`):
/// group sizes taken from the right and cycled, joined by `separator`.
#[derive(Clone, Debug, PartialEq)]
pub struct Grouping {
    pub sizes: Vec<usize>,
    pub separator: String,
}

/// d3-format's `formatLocale(definition)`: the vocabulary every number is
/// written in. A locale without `grouping` does not group, as in d3.
#[derive(Clone, Debug, PartialEq)]
pub struct Locale {
    pub decimal: String,
    pub grouping: Option<Grouping>,
    /// `[prefix, suffix]` a `$` spec places around the number.
    pub currency: [String; 2],
    /// The ten digits `0`-`9` are respelled as, when the locale has its own.
    pub numerals: Option<[String; 10]>,
    pub percent: String,
    pub minus: String,
    pub nan: String,
}

impl Locale {
    /// d3-format's `defaultLocale.js`: en-US.
    pub fn en_us() -> Locale {
        Locale {
            decimal: ".".to_string(),
            grouping: Some(Grouping { sizes: vec![3], separator: ",".to_string() }),
            currency: ["$".to_string(), String::new()],
            numerals: None,
            percent: "%".to_string(),
            minus: "\u{2212}".to_string(),
            nan: "NaN".to_string(),
        }
    }
}

/// d3-format's `formatGroup(grouping, thousands)` at unbounded width. `value`
/// is the integer digits only (ASCII); a size of zero or an empty size list
/// would drop digits, so the caller validates them.
fn group(value: &str, grouping: &Grouping) -> String {
    let mut remaining = value.len();
    let mut parts: Vec<&str> = Vec::new();
    let mut j = 0;
    let mut g = grouping.sizes[0];
    while remaining > 0 && g > 0 {
        let start = remaining.saturating_sub(g);
        parts.push(&value[start..remaining]);
        remaining = start;
        j = (j + 1) % grouping.sizes.len();
        g = grouping.sizes[j];
    }
    parts.reverse();
    parts.join(&grouping.separator)
}

struct Spec {
    sign: char,            // '-' (default), '+', ' ', '('
    symbol: char,          // '\0', '$'
    comma: bool,
    precision: Option<usize>,
    trim: bool,
    type_: char,           // 'f', '%', 's', 'd', 'r', 'g', 'p', '\0'
}

impl Default for Spec {
    fn default() -> Self {
        Spec { sign: '-', symbol: '\0', comma: false, precision: None, trim: false, type_: '\0' }
    }
}

fn parse_spec(s: &str) -> Result<Spec, String> {
    let bytes = s.as_bytes();
    let n = bytes.len();
    let mut i = 0usize;
    let mut spec = Spec::default();

    // fill + align: second char is align → first is fill; or first char is align.
    let is_align = |c: u8| matches!(c, b'<' | b'>' | b'^' | b'=');
    if n >= 2 && is_align(bytes[1]) {
        return Err(format!("unsupported fill/align in d3 spec {s:?}"));
    }
    if n >= 1 && is_align(bytes[0]) {
        return Err(format!("unsupported align in d3 spec {s:?}"));
    }

    // sign: '-' default, '+' (always show sign), ' ' (space for positive), '(' (parens for negative)
    if i < n && matches!(bytes[i], b'-' | b'+' | b'(' | b' ') {
        spec.sign = bytes[i] as char;
        i += 1;
    }

    // symbol: '$' supported; '#' (alternate form) not needed
    if i < n && bytes[i] == b'$' {
        spec.symbol = '$';
        i += 1;
    } else if i < n && bytes[i] == b'#' {
        return Err(format!("unsupported alternate form '#' in d3 spec {s:?}"));
    }

    // zero flag: with no width it pads nothing, so it is accepted and ignored;
    // a width (zero-padded or not) is not modeled.
    if i < n && bytes[i] == b'0' {
        i += 1;
    }
    if i < n && bytes[i].is_ascii_digit() {
        return Err(format!("unsupported zero-pad/width in d3 spec {s:?}"));
    }

    // comma grouping
    if i < n && bytes[i] == b',' {
        spec.comma = true;
        i += 1;
    }

    // precision: .N
    if i < n && bytes[i] == b'.' {
        let dot = i;
        i += 1;
        let start = i;
        while i < n && bytes[i].is_ascii_digit() {
            i += 1;
        }
        if i == start {
            return Err(format!("precision requires a digit after '.' in d3 spec {s:?} at {dot}"));
        }
        spec.precision = Some(s[start..i].parse::<usize>().unwrap());
    }

    // trim trailing zeros
    if i < n && bytes[i] == b'~' {
        spec.trim = true;
        i += 1;
    }

    // type letter
    if i < n {
        match bytes[i] {
            b'f' | b'%' | b's' | b'd' | b'r' | b'g' | b'p' | b'e' => {
                spec.type_ = bytes[i] as char;
                i += 1;
            }
            b'b' | b'o' | b'x' | b'X' | b'n' | b'c' => {
                return Err(format!(
                    "unsupported type {:?} in d3 spec {s:?} — only f/%/s/d/r/g/p/e are implemented",
                    bytes[i] as char
                ));
            }
            c => {
                return Err(format!("invalid type char {:?} in d3 spec {s:?}", c as char));
            }
        }
    }

    if i < n {
        return Err(format!("unexpected trailing chars {:?} in d3 spec {s:?}", &s[i..]));
    }

    Ok(spec)
}

// Replicate JavaScript's Number.toString() for values >= 1e21.
// JS uses exponential notation with explicit sign on exponent, e.g. "1e+25".
fn js_number_to_string(v: f64) -> String {
    // Use Rust's {:e} which gives "1e25"; reformat to JS "1e+25".
    let s = format!("{:e}", v);
    if let Some(pos) = s.find('e') {
        let mantissa = &s[..pos];
        let exp: i32 = s[pos + 1..].parse().unwrap_or(0);
        if exp >= 0 {
            format!("{mantissa}e+{exp}")
        } else {
            format!("{mantissa}e{exp}")
        }
    } else {
        s
    }
}

/// Round `abs_val`'s EXACT decimal expansion half-up at `decimals` places.
///
/// The former `(x·10^p + 0.5).floor()` scaled a COPY of the value first, and
/// the f64 multiply can round a below-half remainder up onto the boundary:
/// 102.85's exact double is 102.84999…, but ×10 lands on exactly 1028.5 —
/// read as .9 where the reference library prints .8. `{:.1074}` prints the
/// exact expansion (a double's fraction always terminates within 1074
/// digits), so the remainder comparison below is exact. Ties (an exact .5
/// remainder, only possible for dyadic values) round up on the absolute
/// value — the reference python d3_format's tie rule (`-2.25` → `−2.3`),
/// which the goldens' own painter (vl-convert's d3.js) only leaves at
/// negative dyadic ties and extreme magnitudes the corpus never reaches.
fn round_exact_abs(abs_val: f64, decimals: usize) -> String {
    let s = format!("{abs_val:.1074}");
    let dot = s.find('.').expect("fixed-precision formatting always prints a dot");
    let frac = &s[dot + 1..];
    let mut digits: Vec<u8> =
        s[..dot].bytes().chain(frac[..decimals].bytes()).collect();
    if frac.as_bytes()[decimals] >= b'5' {
        let mut i = digits.len();
        loop {
            if i == 0 {
                digits.insert(0, b'1');
                break;
            }
            i -= 1;
            if digits[i] == b'9' {
                digits[i] = b'0';
            } else {
                digits[i] += 1;
                break;
            }
        }
    }
    let int_len = digits.len() - decimals;
    let int_part = std::str::from_utf8(&digits[..int_len]).expect("ascii digits");
    if decimals == 0 {
        int_part.to_string()
    } else {
        format!(
            "{int_part}.{}",
            std::str::from_utf8(&digits[int_len..]).expect("ascii digits")
        )
    }
}

fn fmt_fixed(abs_val: f64, decimals: usize) -> String {
    // JS toFixed returns Number::toString (exponential) at >= 1e21; below
    // that the exact expansion prints full digits — the former u64::MAX
    // guard kicked in two orders of magnitude early and printed "1e+20"
    // where d3 writes the digits out.
    if abs_val >= 1e21 {
        return js_number_to_string(abs_val);
    }
    round_exact_abs(abs_val, decimals)
}

// Replicate d3's formatDecimalParts(x, p): (coefficient_digits, exponent).
// x must be positive, finite, and nonzero. Rounds the EXACT decimal
// expansion (see `round_exact_abs` — the former scale-then-add-half copy
// could mis-round at the boundary), half-up.
fn format_decimal_parts(x: f64, p: usize) -> (String, i32) {
    debug_assert!(x > 0.0 && x.is_finite(), "format_decimal_parts: x must be positive finite");
    let p = p.max(1);
    let s = format!("{x:.1074}");
    let dot = s.find('.').expect("fixed-precision formatting always prints a dot");
    let (ip, fp) = (&s[..dot], &s[dot + 1..]);
    // First significant digit sets the decimal exponent; the digit stream
    // continues past the removed dot.
    let (stream, adj): (Vec<u8>, i32) = if ip != "0" {
        (ip.bytes().chain(fp.bytes()).collect(), ip.len() as i32 - 1)
    } else {
        let nz = fp
            .bytes()
            .position(|b| b != b'0')
            .expect("x is positive and nonzero, so a significant digit exists");
        (fp[nz..].bytes().collect(), -(nz as i32) - 1)
    };
    let mut digits: Vec<u8> = stream[..p.min(stream.len())].to_vec();
    while digits.len() < p {
        digits.push(b'0');
    }
    if stream.get(p).is_some_and(|&b| b >= b'5') {
        let mut i = digits.len();
        loop {
            if i == 0 {
                digits.insert(0, b'1');
                break;
            }
            i -= 1;
            if digits[i] == b'9' {
                digits[i] = b'0';
            } else {
                digits[i] += 1;
                break;
            }
        }
    }
    let coefficient = String::from_utf8(digits).expect("ascii digits");
    // Carry can add a digit (e.g. 9.95 → 10.0 at p=1); exponent adjusts up.
    let exp_out = adj + (coefficient.len() as i32 - p as i32);
    let coefficient = if coefficient.len() > p {
        coefficient[..p].to_string()
    } else {
        coefficient
    };
    (coefficient, exp_out)
}

// Replicate d3's formatPrefixAuto(x, p): (body, si_prefix_str).
fn format_si(abs_val: f64, precision: usize) -> (String, &'static str) {
    if abs_val == 0.0 {
        let body = if precision > 1 {
            format!("0.{}", "0".repeat(precision - 1))
        } else {
            "0".to_string()
        };
        return (body, "");
    }
    let (coefficient, exponent) = format_decimal_parts(abs_val, precision);
    let prefix_level = ((exponent as f64) / 3.0).floor() as i32;
    let prefix_level = prefix_level.clamp(-8, 8);
    let prefix_exp = prefix_level * 3;
    let prefix = SI_PREFIXES[(prefix_level + SI_PREFIX_OFFSET) as usize];
    // i = digits before the decimal point in the SI-scaled representation.
    let i = exponent - prefix_exp + 1;
    let n = coefficient.len() as i32;
    let body = if i == n {
        coefficient
    } else if i > n {
        format!("{}{}", coefficient, "0".repeat((i - n) as usize))
    } else if i > 0 {
        format!("{}.{}", &coefficient[..i as usize], &coefficient[i as usize..])
    } else {
        // Value is smaller than 1 in the chosen prefix unit (only reachable when
        // the prefix is clamped at the extremes of the SI table).
        let new_prec = (precision as i32 + i - 1).max(1) as usize;
        let (coeff2, _) = format_decimal_parts(abs_val, new_prec);
        // -i leading zeros after "0."
        format!("0.{}{}", "0".repeat((-i) as usize), coeff2)
    };
    (body, prefix)
}

// Replicate JavaScript's Number.prototype.toExponential(p), d3's `e` type.
fn format_exponential(abs_val: f64, precision: usize) -> String {
    if abs_val == 0.0 {
        let mantissa = if precision > 0 { format!("0.{}", "0".repeat(precision)) } else { "0".to_string() };
        return format!("{mantissa}e+0");
    }
    let (coefficient, exponent) = format_decimal_parts(abs_val, precision + 1);
    let mantissa = if precision > 0 {
        format!("{}.{}", &coefficient[..1], &coefficient[1..])
    } else {
        coefficient
    };
    let sign = if exponent >= 0 { '+' } else { '-' };
    format!("{mantissa}e{sign}{}", exponent.abs())
}

// Replicate d3's formatRounded(x, p), used by the 'r' type: fixed notation
// (never exponential), rounded to p significant digits.
fn format_rounded(abs_val: f64, precision: usize) -> String {
    if abs_val == 0.0 {
        return "0".to_string();
    }
    let (coefficient, exponent) = format_decimal_parts(abs_val, precision);
    let i = exponent + 1; // digits before the decimal point
    let n = coefficient.len() as i32;
    if i <= 0 {
        format!("0.{}{}", "0".repeat((-i) as usize), coefficient)
    } else if n > i {
        format!("{}.{}", &coefficient[..i as usize], &coefficient[i as usize..])
    } else {
        format!("{}{}", coefficient, "0".repeat((i - n) as usize))
    }
}

// Replicate JavaScript's Number.prototype.toPrecision(p), d3's `g` type:
// exponential when the exponent is >= p or < -6, fixed otherwise.
fn format_general(abs_val: f64, precision: usize) -> String {
    if abs_val == 0.0 {
        return if precision > 1 { format!("0.{}", "0".repeat(precision - 1)) } else { "0".to_string() };
    }
    let (coefficient, exponent) = format_decimal_parts(abs_val, precision);
    if exponent >= precision as i32 || exponent < -6 {
        let sign = if exponent >= 0 { '+' } else { '-' };
        let mantissa = if coefficient.len() > 1 {
            format!("{}.{}", &coefficient[..1], &coefficient[1..])
        } else {
            coefficient
        };
        return format!("{mantissa}e{sign}{}", exponent.abs());
    }
    let i = exponent + 1; // digits before the decimal point
    let n = coefficient.len() as i32;
    if i >= n {
        format!("{}{}", coefficient, "0".repeat((i - n) as usize))
    } else if i > 0 {
        format!("{}.{}", &coefficient[..i as usize], &coefficient[i as usize..])
    } else {
        format!("0.{}{}", "0".repeat((-i) as usize), coefficient)
    }
}

fn apply_trim(body: &str) -> String {
    if let Some(e_pos) = body.find('e') {
        let (mantissa, exp_part) = body.split_at(e_pos);
        let trimmed = if mantissa.contains('.') {
            mantissa.trim_end_matches('0').trim_end_matches('.')
        } else {
            mantissa
        };
        format!("{trimmed}{exp_part}")
    } else if body.contains('.') {
        body.trim_end_matches('0').trim_end_matches('.').to_string()
    } else {
        body.to_string()
    }
}

/// Comma-group the integer part of a digit string (no decimal point, no sign):
/// Python's `format(x, ",")`, which ignores the active locale.
pub fn group_digits(value: &str) -> String {
    group(value, &Grouping { sizes: vec![3], separator: ",".to_string() })
}

/// The parsed shape of a d3 spec, for callers that key behavior off a spec's
/// affixes and type (e.g. declare-once unit anchoring) rather than formatting
/// a value. Same grammar subset as `format` — an unsupported spec errs here
/// exactly as it would there.
pub struct SpecParts {
    /// `$` currency symbol present.
    pub symbol: bool,
    /// Type char: 'f', '%', 's', 'd', 'r', 'g', 'p', or '\0' (none, as written —
    /// `format` resolves it to `.12~g`).
    pub type_: char,
    /// `.N` precision, when given.
    pub precision: Option<usize>,
    /// `,` thousands grouping present.
    pub comma: bool,
    /// Sign char: `-` (default, no sign shown for a positive value), `+`,
    /// `(` (accounting parens), or ` ` (space for positive).
    pub sign: char,
    /// `~` trim flag: insignificant trailing zeros removed.
    pub trim: bool,
}

/// Parse a d3 spec into its parts without formatting anything.
pub fn parse_parts(spec: &str) -> Result<SpecParts, String> {
    let s = parse_spec(spec)?;
    Ok(SpecParts {
        symbol: s.symbol == '$',
        type_: s.type_,
        precision: s.precision,
        comma: s.comma,
        sign: s.sign,
        trim: s.trim,
    })
}

/// `numeral_scale.py`'s `sub_unit_digit_format` (:646) and
/// `sub_unit_scientific_format` (:690): the two specs a sub-1 value paints
/// with where an SI spec would print milli/micro prefixes — a ladder-less
/// axis's ticks and a house-register value label, both picking between them
/// by magnitude (`sub_unit_guarded_vega_expr`, `numeral_expr.py:106`) —
/// derived from the SI spec itself, since there is no ladder step to derive
/// a fixed-point precision from. The
/// digit spec swaps the SI type `s` for `r` (significant digits, none of
/// the milli/micro suffixes d3's own SI prefixes would add below 1) at the
/// same precision; the scientific one swaps it for `e` and drops comma
/// grouping. Both keep the sign and the currency symbol on the spec itself
/// (no anchor tick to split it onto), always trim, and default the
/// precision to 6 when the spec carries none. Production keeps the rest of
/// the spec too (fill, align, zero, width, `#`); `d3rs::format::parse_parts`
/// models none of them and refuses a spec carrying one, by name, rather
/// than have this rebuild drop it. Returns `(digit, scientific)`.
pub fn sub_unit_specs(si_spec: &str) -> Result<(String, String), String> {
    let parts = parse_parts(si_spec).map_err(|e| format!("{si_spec}: {e}"))?;
    let precision = parts.precision.unwrap_or(6);
    let sign = if parts.sign == '-' {
        String::new()
    } else {
        parts.sign.to_string()
    };
    let symbol = if parts.symbol { "$" } else { "" };
    let comma = if parts.comma { "," } else { "" };
    Ok((
        format!("{sign}{symbol}{comma}.{precision}~r"),
        format!("{sign}{symbol}.{precision}~e"),
    ))
}

/// `PREDEFINED_SUB_UNIT_FALLBACK`: the plain-digit sibling of a predefined SI
/// member, which never prints an SI prefix below 1 (d3's milli collides
/// case-only with the house million grammar).
pub fn sub_unit_fallback(raw: &str) -> Option<&'static str> {
    PREDEFINED_SUB_UNIT_FALLBACK.iter().find(|(name, _)| *name == raw).map(|(_, sibling)| *sibling)
}

/// The `PREDEFINED_SPECS` entry an engine number name resolves to; the natives
/// (`PREDEFINED_NATIVE`) have none.
pub fn predefined_spec(name: &str) -> Option<&'static PredefinedSpec> {
    PREDEFINED_SPECS.iter().find(|spec| spec.name == name)
}

/// `PREDEFINED_TIME_SPECS`: the strftime spec an engine time name stands for.
pub fn predefined_time_spec(name: &str) -> Option<&'static str> {
    PREDEFINED_TIME_SPECS.iter().find(|(n, _)| *n == name).map(|(_, spec)| *spec)
}

/// `SUB_UNIT_FLOOR` (`predefined_formats.py`): the smallest amount the
/// two-decimal `_full` sibling renders as a nonzero string.
pub const SUB_UNIT_FLOOR: f64 = 0.005;

/// `numeral_scale.py`'s `SUB_UNIT_SCIENTIFIC_FLOOR`: one unit of the finest
/// grid `_MAX_STEP_PRECISION` (10 places) can write.
pub const SUB_UNIT_SCIENTIFIC_FLOOR: f64 = 1e-10;

/// `compile/format.py::sub_unit_spec_for_value`: the spec a per-value surface
/// paints `value` with when `si_spec` has a plain-digit sibling (`full_spec`).
/// Zero and `|v| >= 1` keep `si_spec`.
pub fn sub_unit_spec_for_value(
    si_spec: &str,
    full_spec: &str,
    value: f64,
) -> Result<String, String> {
    let magnitude = value.abs();
    if magnitude == 0.0 || magnitude >= 1.0 {
        return Ok(si_spec.to_string());
    }
    if magnitude >= SUB_UNIT_FLOOR {
        return Ok(full_spec.to_string());
    }
    let (digit, scientific) = sub_unit_specs(si_spec)?;
    Ok(if magnitude < SUB_UNIT_SCIENTIFIC_FLOOR {
        scientific
    } else {
        digit
    })
}

/// Apply a d3-format spec string to `value` in `locale`'s vocabulary
/// (`locale.format(spec)(value)`).
///
/// Supported types: `f`, `%`, `s` (SI prefix), `d` (integer), `r` (rounded), `g`
/// (general), `p` (rounded percent), and the empty type (`.12~g`).
/// Supported modifiers: `,` (locale grouping), `.N` (precision), `~` (trim zeros), `$` (currency).
/// Unsupported features return `Err` with a description — never a silently wrong string.
pub fn format(locale: &Locale, spec: &str, value: f64) -> Result<String, String> {
    let out = format_ascii_digits(locale, spec, value)?;
    Ok(match &locale.numerals {
        Some(numerals) => out
            .chars()
            .map(|c| match c {
                '0'..='9' => numerals[(c as u8 - b'0') as usize].clone(),
                _ => c.to_string(),
            })
            .collect(),
        None => out,
    })
}

// d3's `format(value)` closure up to its closing `numerals(value)` step.
fn format_ascii_digits(locale: &Locale, spec: &str, value: f64) -> Result<String, String> {
    let mut s = parse_spec(spec)?;
    // d3-format `formatSpecifier.js`: the empty type is `.12~g` — the default
    // precision is 12 (an authored one stands), the trim flag is forced.
    if s.type_ == '\0' {
        s.precision = s.precision.or(Some(12));
        s.trim = true;
        s.type_ = 'g';
    }

    let base_prefix = if s.symbol == '$' { locale.currency[0].as_str() } else { "" };
    // Currency displaces the percent suffix (d3 _value_suffix rule).
    let base_suffix = if s.symbol == '$' {
        locale.currency[1].as_str()
    } else if matches!(s.type_, '%' | 'p') {
        locale.percent.as_str()
    } else {
        ""
    };
    let minus = locale.minus.as_str();

    if value.is_nan() {
        // Sign applies to NaN; base_prefix included; no paren wrap.
        let sign_prefix = if s.sign == '+' {
            "+".to_string()
        } else if s.sign == ' ' {
            " ".to_string()
        } else {
            String::new()
        };
        return Ok(format!("{sign_prefix}{base_prefix}{}{base_suffix}", locale.nan));
    }

    let is_negative = value.is_sign_negative();
    let abs_val = value.abs();

    // Non-finite: sign and paren assembly apply; 'd' type uses ∞ symbol.
    if abs_val.is_infinite() {
        // 'd' type renders ∞/−∞ (Unicode) instead of "Infinity".
        let body = if s.type_ == 'd' { "∞" } else { "Infinity" };
        let paren_wrap = s.sign == '(' && is_negative;
        let value_prefix = if paren_wrap {
            format!("({base_prefix}")
        } else if is_negative {
            format!("{minus}{base_prefix}")
        } else if s.sign == '+' {
            format!("+{base_prefix}")
        } else if s.sign == ' ' {
            format!(" {base_prefix}")
        } else {
            base_prefix.to_string()
        };
        let close = if paren_wrap { ")" } else { "" };
        return Ok(format!("{value_prefix}{body}{base_suffix}{close}"));
    }

    // Default precision = 6; clamp per type (significant vs decimal).
    let precision = match s.precision {
        Some(p) => match s.type_ {
            's' | 'r' | 'g' | 'p' => p.clamp(1, 21),
            _ => p.min(20),
        },
        None => 6,
    };

    // Produce the numeric body and SI prefix string.
    let (body, si_prefix) = match s.type_ {
        'f' => {
            if abs_val >= 1e21 {
                (js_number_to_string(abs_val), "")
            } else {
                (fmt_fixed(abs_val, precision), "")
            }
        }
        '%' => {
            let scaled = abs_val * 100.0;
            if scaled >= 1e21 {
                (js_number_to_string(scaled), "")
            } else {
                (fmt_fixed(scaled, precision), "")
            }
        }
        's' => {
            let (b, p) = format_si(abs_val, precision);
            (b, p)
        }
        'd' => {
            let body = if abs_val >= 1e21 {
                js_number_to_string(abs_val)
            } else {
                round_exact_abs(abs_val, 0)
            };
            (body, "")
        }
        'r' => (format_rounded(abs_val, precision), ""),
        'e' => (format_exponential(abs_val, precision), ""),
        'g' => (format_general(abs_val, precision), ""),
        'p' => (format_rounded(abs_val * 100.0, precision), ""),
        _ => unreachable!("parse_spec validated the type"),
    };

    let body = if s.trim { apply_trim(&body) } else { body };

    // A negative value that formats to zero loses its sign (d3 rounds-to-zero test),
    // unless the spec requests an explicit + sign (in which case the sign is kept).
    let body_is_zero = body.parse::<f64>().map(|v| v == 0.0).unwrap_or(false);
    let is_negative = is_negative && !(body_is_zero && s.sign != '+');

    let paren_wrap = s.sign == '(' && is_negative;
    let value_prefix = if paren_wrap {
        // Parenthesized negative: "(base_prefix..." — closing ")" appended to suffix.
        format!("({base_prefix}")
    } else if is_negative {
        format!("{minus}{base_prefix}")
    } else if s.sign == '+' {
        format!("+{base_prefix}")
    } else if s.sign == ' ' {
        format!(" {base_prefix}")
    } else {
        base_prefix.to_string()
    };

    // si_prefix goes between the number body and any percent/currency suffix.
    let value_suffix = format!("{si_prefix}{base_suffix}");

    // Split the body at the first non-digit character so that the fraction/exponent
    // is held out of comma grouping (mirrors d3's maybeSuffix logic).
    // Applies to: d, e, f, g, p, r, s, %
    let split_suffix_types = ['d', 'e', 'f', 'g', 'p', 'r', 's', '%'];
    let (body, value_suffix) = if split_suffix_types.contains(&s.type_) {
        match body.find(|c: char| !c.is_ascii_digit()) {
            Some(pos) => {
                let (int_part, frac_part) = body.split_at(pos);
                // d3 swaps the decimal point for the locale's; an exponent or
                // other tail is kept as written.
                let frac_part = match frac_part.strip_prefix('.') {
                    Some(digits) => format!("{}{digits}", locale.decimal),
                    None => frac_part.to_string(),
                };
                (int_part.to_string(), format!("{frac_part}{value_suffix}"))
            }
            None => (body, value_suffix),
        }
    } else {
        (body, value_suffix)
    };

    let body = match (&locale.grouping, s.comma) {
        (Some(grouping), true) => group(&body, grouping),
        _ => body,
    };
    if paren_wrap {
        Ok(format!("{value_prefix}{body}{value_suffix})"))
    } else {
        Ok(format!("{value_prefix}{body}{value_suffix}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // All expected strings verified against:
    //   uv run python -c "from d3_format import format as d3fmt; print(d3fmt(spec, val))"

    fn fmt(spec: &str, val: f64) -> String {
        format(&Locale::en_us(), spec, val).expect(spec)
    }

    // --- exact-double rounding (regression: the multiply-then-add-half
    // rounding read 102.85's double — exactly 102.849999999999994… — as
    // 102.9, because ×10 lands on exactly 1028.5; the reference library
    // rounds the exact expansion and prints 102.8) ---

    #[test]
    fn fixed_rounds_the_exact_double_not_a_scaled_copy() {
        assert_eq!(fmt(",.1~f", 102.85), "102.8");
        assert_eq!(fmt(".2f", 1.005), "1.00"); // exact double 1.00499…
        assert_eq!(fmt(",.1~f", 97.469), "97.5");
    }

    #[test]
    fn fixed_ties_on_the_exact_half_round_up_on_abs() {
        assert_eq!(fmt(",.1~f", 2.25), "2.3"); // 2.25 is dyadic — a true tie
        assert_eq!(fmt(",.1~f", -2.25), "\u{2212}2.3");
        assert_eq!(fmt("d", -2.5), "\u{2212}3");
        assert_eq!(fmt(",.0f", 2.5), "3");
    }

    #[test]
    fn si_rounds_the_exact_double_at_the_carry_boundary() {
        assert_eq!(fmt(".3~s", 999.95), "1k"); // exact double 999.9499…
        assert_eq!(fmt(".3~s", 102850.0), "103k");
    }

    // --- SI prefix type 's' ---

    #[test]
    fn test_si_precision_trim() {
        assert_eq!(fmt(".3~s", 4_820_000.0), "4.82M");
        assert_eq!(fmt(".3~s", 0.0),         "0");
        assert_eq!(fmt(".3~s", 0.5),          "500m");
        assert_eq!(fmt(".3~s", 999.5),        "1k");
        assert_eq!(fmt(".3~s", 1_000_000.0),  "1M");
        assert_eq!(fmt(".3~s", -1234.56),     "\u{2212}1.23k");
        assert_eq!(fmt(".3~s", 1e-4),         "100\u{00b5}");
    }

    #[test]
    fn test_si_trim_default_precision() {
        assert_eq!(fmt("~s", 4_820_000.0), "4.82M");
        assert_eq!(fmt("~s", 1234.56),     "1.23456k");
    }

    #[test]
    fn test_si_no_trim() {
        assert_eq!(fmt("s", 4_820_000.0), "4.82000M");
        assert_eq!(fmt("s", 0.0),         "0.00000");
    }

    // --- Percent type '%' ---

    #[test]
    fn test_percent() {
        assert_eq!(fmt(".0%", 0.0),    "0%");
        assert_eq!(fmt(".0%", 0.5),    "50%");
        assert_eq!(fmt(".0%", -0.253), "\u{2212}25%");
        assert_eq!(fmt(".1%", 0.12345),"12.3%");
        assert_eq!(fmt(".1%", 1.0),    "100.0%");
        assert_eq!(fmt("%", 0.5),      "50.000000%");
    }

    // --- Fixed type 'f' ---

    #[test]
    fn test_fixed_comma() {
        assert_eq!(fmt(",.0f", 0.0),      "0");
        assert_eq!(fmt(",.0f", 999.5),    "1,000");
        assert_eq!(fmt(",.0f", 1e6),      "1,000,000");
        assert_eq!(fmt(",.0f", -1234.56), "\u{2212}1,235");
        assert_eq!(fmt(",.2f", 0.0),      "0.00");
        assert_eq!(fmt(",.2f", -1234.56), "\u{2212}1,234.56");
        assert_eq!(fmt(",.2f", 1e6),      "1,000,000.00");
    }

    #[test]
    fn test_fixed_plain() {
        assert_eq!(fmt(".0f", 0.0),    "0");
        assert_eq!(fmt(".0f", 999.5),  "1000");
        assert_eq!(fmt(".0f", -1234.56), "\u{2212}1235");
        assert_eq!(fmt(".2f", 0.0),    "0.00");
        assert_eq!(fmt(".2f", 0.5),    "0.50");
        assert_eq!(fmt(".2f", 1e-4),   "0.00");
        assert_eq!(fmt("f", 1234.56),  "1234.560000");
    }

    // --- Currency '$' ---

    #[test]
    fn test_currency() {
        assert_eq!(fmt("$,.0f", 0.0),        "$0");
        assert_eq!(fmt("$,.0f", 4_820_000.0),"$4,820,000");
        assert_eq!(fmt("$,.0f", -1234.56),   "\u{2212}$1,235");
        assert_eq!(fmt("$.2f", 0.5),         "$0.50");
        assert_eq!(fmt("$.2f", -1234.56),    "\u{2212}$1234.56");
    }

    // --- Integer type 'd' ---

    #[test]
    fn test_integer() {
        assert_eq!(fmt(",d", 0.0),      "0");
        assert_eq!(fmt(",d", 1e6),      "1,000,000");
        assert_eq!(fmt(",d", -1234.56), "\u{2212}1,235");
        assert_eq!(fmt("d", 0.0),       "0");
        assert_eq!(fmt("d", 999.5),     "1000");
        assert_eq!(fmt("d", -1234.56),  "\u{2212}1235");
    }

    // --- Sign specs (real engine specs from predefined_formats.py) ---

    #[test]
    fn test_sign_plus() {
        // uv run python -c "from d3_format import format as f; print(f('+.1%')(0.25))"
        assert_eq!(fmt("+.1%", 0.25), "+25.0%");
        // uv run python -c "from d3_format import format as f; print(f('+.1%')(-0.25))"
        assert_eq!(fmt("+.1%", -0.25), "\u{2212}25.0%");
        // uv run python -c "from d3_format import format as f; print(f('+,d')(1234))"
        assert_eq!(fmt("+,d", 1234.0), "+1,234");
        // uv run python -c "from d3_format import format as f; print(f('+,d')(-1234))"
        assert_eq!(fmt("+,d", -1234.0), "\u{2212}1,234");
        // uv run python -c "from d3_format import format as f; print(f('+.0f')(0.0))"
        assert_eq!(fmt("+.0f", 0.0), "+0");
    }

    #[test]
    fn test_sign_space() {
        // uv run python -c "from d3_format import format as f; print(repr(f(' .1f')(1.0)))"
        assert_eq!(fmt(" .1f", 1.0), " 1.0");
        // uv run python -c "from d3_format import format as f; print(repr(f(' .1f')(-1.0)))"
        assert_eq!(fmt(" .1f", -1.0), "\u{2212}1.0");
    }

    #[test]
    fn test_sign_paren() {
        // uv run python -c "from d3_format import format as f; print(f('(.1f')(1.0))"
        assert_eq!(fmt("(.1f", 1.0), "1.0");
        // uv run python -c "from d3_format import format as f; print(f('(.1f')(-1.0))"
        assert_eq!(fmt("(.1f", -1.0), "(1.0)");
    }

    // --- Rounds-to-zero with sign='+' preserves the minus sign ---

    #[test]
    fn test_sign_plus_rounds_to_zero() {
        // uv run python -c "from d3_format import format as f; print(repr(f('+.1%')(-0.0004)))"
        // → '−0.0%'  (minus preserved because sign='+')
        assert_eq!(fmt("+.1%", -0.0004), "\u{2212}0.0%");
        // Without explicit sign, a negative that rounds to zero drops the minus.
        assert_eq!(fmt(".1%", -0.0004), "0.0%");
    }

    // --- Non-finite and large-magnitude values ---

    #[test]
    fn test_infinity() {
        // uv run python -c "from d3_format import format as f; print(f('.2f')(float('inf')))"
        assert_eq!(fmt(".2f", f64::INFINITY), "Infinity");
        assert_eq!(fmt(".2f", f64::NEG_INFINITY), "\u{2212}Infinity");
        assert_eq!(fmt("+.2f", f64::INFINITY), "+Infinity");
        // paren sign with -inf: "(Infinity)" — sign/paren assembly applies to non-finite
        assert_eq!(fmt("(.2f", f64::NEG_INFINITY), "(Infinity)");
    }

    #[test]
    fn test_nan() {
        // uv run python -c "from d3_format import format as f; print(f('.2f')(float('nan')))"
        assert_eq!(fmt(".2f", f64::NAN), "NaN");
        // sign applies to NaN
        // uv run python -c "from d3_format import format as f; print(f('+.2f')(float('nan')))"
        assert_eq!(fmt("+.2f", f64::NAN), "+NaN");
    }

    #[test]
    fn test_d_type_infinity() {
        // d type uses ∞/−∞ symbol, not "Infinity"
        // uv run python -c "from d3_format import format as f; print(f(',d')(float('inf')))"
        assert_eq!(fmt(",d", f64::INFINITY), "∞");
        // uv run python -c "from d3_format import format as f; print(f(',d')(float('-inf')))"
        assert_eq!(fmt(",d", f64::NEG_INFINITY), "\u{2212}∞");
        // uv run python -c "from d3_format import format as f; print(f('+d')(float('inf')))"
        assert_eq!(fmt("+d", f64::INFINITY), "+∞");
    }

    #[test]
    fn test_d_type_u64_boundary() {
        // Values at/above u64::MAX must not overflow the cast (1.84e19 ≤ 2^64).
        // uv run python -c "from d3_format import format as f; print(f('.0f')(2.0**64))"
        // Python gives '18446744073709551616' (exact bigint arithmetic).
        // Rust cannot represent that exactly in f64; the nearest f64 = 2^64 = 1.8446744e19.
        // We fall through to js_number_to_string which produces "1.8446744073709552e+19".
        // That's an acceptable spike-level approximation; the important thing is no panic.
        let big = u64::MAX as f64;
        let result = format(&Locale::en_us(), "d", big);
        assert!(result.is_ok(), "d type at u64::MAX should not panic: {result:?}");
    }

    #[test]
    fn test_large_magnitude_f() {
        // uv run python -c "from d3_format import format as f; print(f('.2f')(1e25))"
        assert_eq!(fmt(".2f", 1e25), "1e+25");
    }

    // --- Unsupported features must Err, not produce garbage ---

    #[test]
    fn test_unsupported_type_errors() {
        assert!(format(&Locale::en_us(), "x", 1.0).is_err());
        assert!(format(&Locale::en_us(), "n", 1.0).is_err());
        assert!(format(&Locale::en_us(), "0>10.2f", 1.0).is_err()); // fill + align + width
    }

    // --- Rounded significant-digit type 'r' (d3's formatRounded) ---

    #[test]
    fn test_rounded_type() {
        // uv run python -c "from d3_format import format as f; print(repr(f('.3~r')(1234.5)))"
        assert_eq!(fmt(".3~r", 1234.5), "1230");
        // uv run python -c "from d3_format import format as f; print(repr(f('.3~r')(0.001234)))"
        assert_eq!(fmt(".3~r", 0.001234), "0.00123");
        // uv run python -c "from d3_format import format as f; print(repr(f('.3~r')(123)))"
        assert_eq!(fmt(".3~r", 123.0), "123");
        // uv run python -c "from d3_format import format as f; print(repr(f('.3~r')(0)))"
        assert_eq!(fmt(".3~r", 0.0), "0");
        // uv run python -c "from d3_format import format as f; print(repr(f('.3~r')(-45.6)))"
        assert_eq!(fmt(".3~r", -45.6), "\u{2212}45.6");
        // uv run python -c "from d3_format import format as f; print(repr(f('.3~r')(999.5)))"
        assert_eq!(fmt(".3~r", 999.5), "1000");
        // uv run python -c "from d3_format import format as f; print(repr(f('.3~r')(0.00012345)))"
        assert_eq!(fmt(".3~r", 0.00012345), "0.000123");
        // uv run python -c "from d3_format import format as f; print(repr(f('.3~r')(1500000)))"
        assert_eq!(fmt(".3~r", 1_500_000.0), "1500000");
        // uv run python -c "from d3_format import format as f; print(repr(f('.3~r')(1.0005)))"
        assert_eq!(fmt(".3~r", 1.0005), "1");
        // uv run python -c "from d3_format import format as f; print(repr(f('.3~r')(-0.0004)))"
        assert_eq!(fmt(".3~r", -0.0004), "\u{2212}0.0004");
        // Without trim, trailing zeros stay.
        // uv run python -c "from d3_format import format as f; print(repr(f('.3r')(123)))"
        assert_eq!(fmt(".3r", 123.0), "123");
        // uv run python -c "from d3_format import format as f; print(repr(f('.3r')(1.2)))"
        assert_eq!(fmt(".3r", 1.2), "1.20");
        // Comma grouping on the integer part.
        // uv run python -c "from d3_format import format as f; print(repr(f(',.3~r')(1500000)))"
        assert_eq!(fmt(",.3~r", 1_500_000.0), "1,500,000");
    }

    // --- exponential type ---
    // uv run python -c "from d3_format import format as f; print(f('.3~e', 2.3e-6))"

    #[test]
    fn the_e_type_prints_toexponential() {
        assert_eq!(fmt(".3~e", 2.3e-6), "2.3e-6");
        assert_eq!(fmt(".3~e", -1e-12), "\u{2212}1e-12");
        assert_eq!(fmt("$.3~e", 1.5e-11), "$1.5e-11");
        assert_eq!(fmt(".2e", 0.0), "0.00e+0");
        assert_eq!(fmt(".0e", 12345.0), "1e+4");
        assert_eq!(fmt(".3e", 9.9996), "1.000e+1");
    }
}
