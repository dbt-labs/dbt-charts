// Ported from libs/d3-format/src/d3_format/format.py and spec.py.
// Implements the subset of the d3-format grammar required by dbt charts
// theme/resolved payloads. Unsupported features (`#`, b/o/x/X/n types) return
// Err — never a silently wrong string.
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

/// The SI level (a power of 1000, clamped to the table) a number with this
/// decimal exponent prints at: d3's `Math.max(-8, Math.min(8, Math.floor(e / 3)))`.
fn si_level(exponent: i32) -> i32 {
    exponent.div_euclid(3).clamp(-8, 8)
}

fn si_prefix(level: i32) -> &'static str {
    SI_PREFIXES[(level + SI_PREFIX_OFFSET) as usize]
}

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
    group_within(value, grouping, usize::MAX)
}

/// V8's longest string, in UTF-16 units.
const MAX_STRING_UNITS: usize = (1 << 29) - 24;

/// The most digits `Number.prototype.toFixed`, `toExponential` and `toPrecision` accept.
const MAX_PRECISION: usize = 100;

/// `formatGroup` with its `width` cap: d3 passes infinity except when zero
/// padding is grouped with the digits, where it bounds the grouped length.
fn group_within(value: &str, grouping: &Grouping, width: usize) -> String {
    let mut remaining = value.len();
    let mut parts: Vec<&str> = Vec::new();
    let mut j = 0;
    let mut g = grouping.sizes[0];
    let mut length = 0usize;
    while remaining > 0 && g > 0 {
        if length.saturating_add(g + 1) > width {
            g = width.saturating_sub(length).max(1);
        }
        let start = remaining.saturating_sub(g);
        parts.push(&value[start..remaining]);
        remaining = start;
        length = length.saturating_add(g + 1);
        if length > width {
            break;
        }
        j = (j + 1) % grouping.sizes.len();
        g = grouping.sizes[j];
    }
    parts.reverse();
    parts.join(&grouping.separator)
}

struct Spec {
    fill: char,
    align: char,           // '>' (default), '<', '^', '='
    sign: char,            // '-' (default), '+', ' ', '('
    symbol: char,          // '\0', '$'
    symbol_at: Option<usize>,
    zero: bool,
    width: Option<usize>,
    comma: bool,
    precision: Option<usize>,
    trim: bool,
    type_: char,           // 'f', '%', 's', 'd', 'r', 'g', 'p', '\0'
}

impl Default for Spec {
    fn default() -> Self {
        Spec {
            fill: ' ',
            align: '>',
            sign: '-',
            symbol: '\0',
            symbol_at: None,
            zero: false,
            width: None,
            comma: false,
            precision: None,
            trim: false,
            type_: '\0',
        }
    }
}

/// The width a spec pads to, as `format` would read it, so a caller can price the padded string before it exists.
pub fn spec_width(spec: &str) -> Result<Option<usize>, String> {
    Ok(parse_spec(spec)?.width)
}

fn parse_spec(s: &str) -> Result<Spec, String> {
    let bytes = s.as_bytes();
    let n = bytes.len();
    let mut i = 0usize;
    let mut spec = Spec::default();

    // fill + align: second char is align → first is fill; or first char is align.
    let is_align = |c: char| matches!(c, '<' | '>' | '^' | '=');
    let mut chars = s.chars();
    match (chars.next(), chars.next()) {
        (Some(fill), Some(align)) if is_align(align) => {
            spec.fill = fill;
            spec.align = align;
            i = fill.len_utf8() + 1;
        }
        (Some(align), _) if is_align(align) => {
            spec.align = align;
            i = 1;
        }
        _ => {}
    }

    // sign: '-' default, '+' (always show sign), ' ' (space for positive), '(' (parens for negative)
    if i < n && matches!(bytes[i], b'-' | b'+' | b'(' | b' ') {
        spec.sign = bytes[i] as char;
        i += 1;
    }

    // symbol: '$' supported; '#' (alternate form) not needed
    if i < n && bytes[i] == b'$' {
        spec.symbol = '$';
        spec.symbol_at = Some(i);
        i += 1;
    } else if i < n && bytes[i] == b'#' {
        return Err(format!("unsupported alternate form '#' in d3 spec {s:?}"));
    }

    if i < n && bytes[i] == b'0' {
        spec.zero = true;
        i += 1;
    }
    // An explicit "0" flag and a "0" fill under "=" are one request (d3 collapses them).
    if spec.zero || (spec.fill == '0' && spec.align == '=') {
        spec.zero = true;
        spec.fill = '0';
        spec.align = '=';
    }
    let width_start = i;
    while i < n && bytes[i].is_ascii_digit() {
        i += 1;
    }
    if i > width_start {
        // d3 pads with `new Array(width - length + 1).join(fill)`: V8 refuses a string past its length limit.
        let width = s[width_start..i].parse::<usize>().ok().filter(|w| *w <= MAX_STRING_UNITS);
        spec.width = Some(width.ok_or_else(|| "Invalid string length".to_string())?);
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
        // `toFixed`, `toExponential` and `toPrecision` throw a RangeError past 100 digits.
        let precision = s[start..i].parse::<usize>().ok().filter(|p| *p <= MAX_PRECISION);
        spec.precision = Some(precision.ok_or_else(|| format!("toFixed() digits argument must be between 0 and {MAX_PRECISION}"))?);
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
    round_half_up_abs(abs_val, decimals)
}

/// Whether `x·10^k` lies exactly halfway between two integers: the only case
/// where half-up rounding at `k` places can differ from Rust's correctly
/// rounded (round-half-even) formatting. With `x = odd·2^low`,
/// `2·x·10^k = odd·5^k·2^(low+k+1)` must be an odd integer.
fn is_half_tie(x: f64, k: i32) -> bool {
    if x == 0.0 {
        return false;
    }
    let bits = x.to_bits();
    let biased = ((bits >> 52) & 0x7ff) as i32;
    let fraction = bits & ((1 << 52) - 1);
    let (mantissa, exp) = if biased == 0 { (fraction, -1074) } else { (fraction | (1 << 52), biased - 1075) };
    let zeros = mantissa.trailing_zeros();
    let (odd, low) = (mantissa >> zeros, exp + zeros as i32);
    if k >= 0 {
        return low + k + 1 == 0;
    }
    // A mantissa below 2^53 has at most 22 factors of five.
    let j = -k;
    low + 1 == j && j <= 22 && odd % 5u64.pow(j as u32) == 0
}

/// [`round_exact_abs`], printing only the digits it keeps unless the value
/// is a tie at `decimals`.
fn round_half_up_abs(abs_val: f64, decimals: usize) -> String {
    if is_half_tie(abs_val, decimals as i32) {
        return round_exact_abs(abs_val, decimals);
    }
    format!("{abs_val:.decimals$}")
}

// Replicate d3's formatDecimalParts(x, p): (coefficient_digits, exponent).
// x must be positive, finite, and nonzero. Rounds the EXACT decimal
// expansion (see `round_exact_abs` — the former scale-then-add-half copy
// could mis-round at the boundary), half-up.
fn format_decimal_parts(x: f64, p: usize) -> (String, i32) {
    debug_assert!(x > 0.0 && x.is_finite(), "format_decimal_parts: x must be positive finite");
    let p = p.max(1);
    let s = format!("{x:.*e}", p - 1);
    let (mantissa, exp) = s.split_once('e').expect("exponential formatting always prints an e");
    let exp: i32 = exp.parse().expect("exponential formatting prints an integer exponent");
    // Only a tie rounded down to even can differ from half-up, and rounding
    // down never carries, so `exp` is the value's own exponent there.
    if is_half_tie(x, p as i32 - 1 - exp) {
        return format_decimal_parts_exact(x, p);
    }
    (mantissa.replace('.', ""), exp)
}

fn format_decimal_parts_exact(x: f64, p: usize) -> (String, i32) {
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
    let prefix_level = si_level(exponent);
    let prefix_exp = prefix_level * 3;
    let prefix = si_prefix(prefix_level);
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
    /// A fill, an align, zero padding or a width is authored.
    pub padded: bool,
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
        padded: s.width.is_some() || s.zero || s.align != '>' || s.fill != ' ',
    })
}

/// `spec` without its `$` currency flag (`numeral_scale.without_symbol`):
/// the digits a bare zero tick paints.
pub fn without_symbol(spec: &str) -> Result<String, String> {
    let mut out = spec.to_string();
    if let Some(i) = parse_spec(spec)?.symbol_at {
        out.remove(i);
    }
    Ok(out)
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
/// the spec too (fill, align, zero, width, `#`); this rebuild carries none of
/// them, so a spec with one refuses by name rather than lose it. Returns
/// `(digit, scientific)`.
pub fn sub_unit_specs(si_spec: &str) -> Result<(String, String), String> {
    let parts = parse_parts(si_spec).map_err(|e| format!("{si_spec}: {e}"))?;
    if parts.padded {
        return Err(format!("{si_spec}: a padded spec's sub-unit siblings are not modeled"));
    }
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
/// Supported modifiers: fill and align, zero padding, width, `,` (locale grouping),
/// `.N` (precision), `~` (trim zeros), `$` (currency).
/// Unsupported features return `Err` with a description — never a silently wrong string.
pub fn format(locale: &Locale, spec: &str, value: f64) -> Result<String, String> {
    Ok(numerals(locale, format_ascii_digits(locale, spec, value)?))
}

/// d3's closing `numerals(value)` step: the locale's digit glyphs for `0`-`9`.
fn numerals(locale: &Locale, out: String) -> String {
    match &locale.numerals {
        Some(numerals) => out
            .chars()
            .map(|c| match c {
                '0'..='9' => numerals[(c as u8 - b'0') as usize].clone(),
                _ => c.to_string(),
            })
            .collect(),
        None => out,
    }
}

/// d3-format's `exponent(x)` (`exponent.js`): the decimal exponent of `|x|` in
/// exponential notation. NaN where `formatDecimalParts` has none: zero and
/// every non-finite `x`.
fn exponent(x: f64) -> f64 {
    if !x.is_finite() || x == 0.0 {
        return f64::NAN;
    }
    // `toExponential()` with no digit count keeps every digit, so the exponent never rounds up.
    format!("{:e}", x.abs()).split('e').nth(1).and_then(|e| e.parse::<i32>().ok()).map_or(f64::NAN, f64::from)
}

/// The `exponent` of the d3-format Vega bundles, which predates 3.1.2: 0 for zero.
fn vega_exponent(x: f64) -> f64 {
    if x == 0.0 { 0.0 } else { exponent(x) }
}

/// d3-format's `precisionFixed(step)` (`precisionFixed.js`): `max(0, -exponent(|step|))`,
/// `None` where that is NaN (a step of 0 or a non-finite step).
pub fn precision_fixed(step: f64) -> Option<usize> {
    precision_fixed_by(exponent, step)
}

/// [`precision_fixed`] under Vega's bundled d3-format, where a step of 0 has precision 0.
pub fn vega_precision_fixed(step: f64) -> Option<usize> {
    precision_fixed_by(vega_exponent, step)
}

fn precision_fixed_by(exponent: fn(f64) -> f64, step: f64) -> Option<usize> {
    let e = exponent(step.abs());
    (!e.is_nan()).then(|| (-e).max(0.0) as usize)
}

/// d3-scale's `tickFormat(start, stop, count, specifier)(value)`
/// (`d3-scale/src/tickFormat.js`): a number locale's format whose missing
/// precision is derived from the tick step a scale over `[start, stop]` would
/// use at `count` ticks, so every number of the span prints at one precision.
/// An `s` type always prints at one SI prefix for the whole span. `specifier`
/// defaults to `,f`.
pub fn tick_format(
    locale: &Locale,
    specifier: Option<&str>,
    start: f64,
    stop: f64,
    count: usize,
    value: f64,
) -> Result<String, String> {
    tick_format_by(exponent, locale, specifier, (start, stop), count, value)
}

/// [`tick_format`] under Vega's bundled d3-format, whose `exponent(0)` is 0: a collapsed
/// span or an all-zero domain prints whole numbers where d3-format 3.1.2 prints NaN or
/// six decimals.
pub fn vega_tick_format(
    locale: &Locale,
    specifier: Option<&str>,
    start: f64,
    stop: f64,
    count: usize,
    value: f64,
) -> Result<String, String> {
    tick_format_by(vega_exponent, locale, specifier, (start, stop), count, value)
}

fn tick_format_by(
    exponent: fn(f64) -> f64,
    locale: &Locale,
    specifier: Option<&str>,
    (start, stop): (f64, f64),
    count: usize,
    value: f64,
) -> Result<String, String> {
    let mut s = parse_spec(specifier.unwrap_or(",f"))?;
    let step = crate::ticks::d3_tick_step(start, stop, count);
    let max = start.abs().max(stop.abs());
    let unset = s.precision.is_none();
    // d3 leaves the precision unset where `precision*` is NaN, so the default of 6 applies.
    let digits = |p: f64| (!p.is_nan()).then_some(p.max(0.0) as usize);
    match s.type_ {
        's' => {
            // d3-scale `tickFormat`: `precisionPrefix`, then `formatPrefix(specifier, max)`.
            let prefix_exponent = (exponent(max) / 3.0).floor().clamp(-8.0, 8.0) * 3.0;
            if unset {
                s.precision = digits(prefix_exponent - exponent(step.abs()));
            }
            s.type_ = 'f';
            // A NaN exponent (max of 0) scales the value to NaN and has no prefix to print.
            let prefix = if prefix_exponent.is_nan() { "" } else { si_prefix(prefix_exponent as i32 / 3) };
            let scaled = format_parsed(locale, s, 10f64.powf(-prefix_exponent) * value, prefix)?;
            return Ok(numerals(locale, scaled));
        }
        '\0' | 'e' | 'g' | 'p' | 'r' if unset => {
            // `precisionRound(step, max)`.
            let range = max.abs() - step.abs();
            let gap = exponent(range) - exponent(step.abs());
            let precision = digits(if gap.is_nan() { gap } else { gap.max(0.0) + 1.0 });
            s.precision = precision.map(|p| p.saturating_sub(usize::from(s.type_ == 'e')));
        }
        'f' | '%' if unset => {
            s.precision = precision_fixed_by(exponent, step).map(|p| p.saturating_sub(2 * usize::from(s.type_ == '%')));
        }
        _ => {}
    }
    Ok(numerals(locale, format_parsed(locale, s, value, "")?))
}

/// vega-format's `formatFloat(specifier)(value)`: a spec with no precision
/// prints at twelve significant digits (ten for a percent, eleven for an
/// exponent) with the trailing zeros of the fraction trimmed. `specifier`
/// defaults to `,`.
pub fn format_float(locale: &Locale, specifier: Option<&str>, value: f64) -> Result<String, String> {
    let mut s = parse_spec(specifier.filter(|spec| !spec.is_empty()).unwrap_or(","))?;
    if s.type_ == '\0' || s.precision.is_some() {
        return format_parsed(locale, s, value, "");
    }
    s.precision = Some(12 - 2 * usize::from(s.type_ == '%') - usize::from(s.type_ == 'e'));
    Ok(trim_zeroes(&format_parsed(locale, s, value, "")?, &locale.decimal))
}

/// vega-format's `trimZeroes`: the fraction's trailing zeros (and a bare
/// decimal mark) removed, an exponent or suffix kept.
fn trim_zeroes(text: &str, decimal: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let decimal: Vec<char> = decimal.chars().collect();
    let Some(dec) = chars.windows(decimal.len()).position(|w| w == decimal.as_slice()) else {
        return text.to_string();
    };
    let rightmost_digit = match chars.iter().rposition(|&c| c == 'e') {
        Some(e) if e > 0 => Some(e),
        _ => (dec + 1..chars.len()).rev().find(|&i| chars[i].is_ascii_digit()).map(|i| i + 1),
    };
    let Some(rightmost) = rightmost_digit else {
        return text.to_string();
    };
    let end: String = chars[rightmost..].iter().collect();
    let mut idx = rightmost;
    loop {
        idx -= 1;
        if idx <= dec {
            break;
        }
        if chars[idx] != '0' {
            idx += 1;
            break;
        }
    }
    format!("{}{end}", chars[..idx].iter().collect::<String>())
}

// d3's `format(value)` closure up to its closing `numerals(value)` step.
fn format_ascii_digits(locale: &Locale, spec: &str, value: f64) -> Result<String, String> {
    format_parsed(locale, parse_spec(spec)?, value, "")
}

/// `suffix` is d3's `options.suffix` (`formatPrefix`'s SI prefix): it follows the currency or
/// percent suffix.
fn format_parsed(locale: &Locale, mut s: Spec, value: f64, suffix: &str) -> Result<String, String> {
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
        // A body with no leading digit joins the suffix whole, out of
        // grouping and zero padding (d3's split-suffix step).
        let prefix = format!("{sign_prefix}{base_prefix}");
        return Ok(pad(&s, locale, &prefix, String::new(), &format!("{}{base_suffix}{suffix}", locale.nan)));
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
        return Ok(pad(&s, locale, &value_prefix, String::new(), &format!("{body}{base_suffix}{suffix}{close}")));
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
                // d3-format 3.1.2 `formatDecimal`: `toLocaleString("en")` prints the shortest
                // round-trip digits padded with zeros, with the commas removed.
                let shortest = format!("{abs_val:e}");
                let (mantissa, exponent) = shortest.split_once('e').expect("{:e} always prints an exponent");
                let digits = mantissa.replace('.', "");
                let zeros = exponent.parse::<usize>().expect("a finite value >= 1e21") + 1 - digits.len();
                format!("{digits}{}", "0".repeat(zeros))
            } else {
                round_half_up_abs(abs_val, 0)
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
    let value_suffix = format!("{si_prefix}{base_suffix}{suffix}");

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

    let value_suffix = if paren_wrap { format!("{value_suffix})") } else { value_suffix };
    Ok(pad(&s, locale, &value_prefix, body, &value_suffix))
}

/// d3's closing steps: group the digits, pad to `width` with `fill`, align.
/// Without zero padding the digits are grouped alone; with it the padding is
/// grouped with them, so separators land inside the padded field.
fn pad(s: &Spec, locale: &Locale, prefix: &str, value: String, suffix: &str) -> String {
    let mut value = match (&locale.grouping, s.comma && !s.zero) {
        (Some(grouping), true) => group(&value, grouping),
        _ => value,
    };
    let length = prefix.chars().count() + value.chars().count() + suffix.chars().count();
    let mut padding = match s.width {
        Some(width) if width > length => s.fill.to_string().repeat(width - length),
        _ => String::new(),
    };
    if s.comma && s.zero {
        let width = match s.width {
            Some(width) if !padding.is_empty() => width - suffix.chars().count(),
            _ => usize::MAX,
        };
        let padded = format!("{padding}{value}");
        value = match &locale.grouping {
            Some(grouping) => group_within(&padded, grouping, width),
            None => padded,
        };
        padding.clear();
    }
    match s.align {
        '<' => format!("{prefix}{value}{suffix}{padding}"),
        '=' => format!("{prefix}{padding}{value}{suffix}"),
        '^' => {
            let fill: Vec<char> = padding.chars().collect();
            let (left, right) = fill.split_at(fill.len() / 2);
            let left: String = left.iter().collect();
            let right: String = right.iter().collect();
            format!("{left}{prefix}{value}{suffix}{right}")
        }
        _ => format!("{padding}{prefix}{value}{suffix}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn without_symbol_drops_only_the_currency_flag() {
        assert_eq!(without_symbol("$,.0f").unwrap(), ",.0f");
        assert_eq!(without_symbol("+$,.1~f").unwrap(), "+,.1~f");
        assert_eq!(without_symbol("$>$10,.0f").unwrap(), "$>10,.0f");
        assert_eq!(without_symbol(",.2f").unwrap(), ",.2f");
    }

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

    /// A lone compact value that rounds up to the next power of 1000 steps to the
    /// next prefix: `$1M`, never `$1,000K`.
    #[test]
    fn si_lone_value_rolls_over_to_the_next_prefix() {
        assert_eq!(fmt("$.2~s", 999_960.0), "$1M");
        assert_eq!(fmt("$.2~s", 999_999.5), "$1M");
        assert_eq!(fmt("$.2~s", -999_960.0), "\u{2212}$1M");
        assert_eq!(fmt(".2~s", 999_960_000.0), "1G");
        assert_eq!(fmt("$.2~s", 999_499.0), "$1M");
    }

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

    // `tick_format` is pinned by the d3-scale oracle; `format_float` is vega-format's.
    fn span(spec: Option<&str>, start: f64, stop: f64, value: f64) -> String {
        tick_format(&Locale::en_us(), spec, start, stop, 5, value).expect("a supported spec")
    }

    fn float(spec: Option<&str>, value: f64) -> String {
        format_float(&Locale::en_us(), spec, value).expect("a supported spec")
    }

    #[test]
    fn tick_format_takes_its_precision_from_the_tick_step() {
        assert_eq!(span(None, 0.0, 1500.0, 1500.0), "1,500");
        assert_eq!(span(None, 0.0, 0.5, 0.25), "0.3");
        assert_eq!(span(Some(".0%"), 0.0, 0.5, 0.5), "50%");
        assert_eq!(span(Some("%"), 0.0, 1.0, 0.5), "50%");
        assert_eq!(span(Some("r"), 0.0, 100.0, 33.333), "30");
        assert_eq!(span(Some("e"), 0.0, 1000.0, 1234.0), "1e+3");
        assert_eq!(span(Some("g"), 0.0, 10.0, 3.14159), "3");
        assert_eq!(span(Some(",f"), -5.0, 5.0, -2.5), "\u{2212}3");
        assert_eq!(span(Some("~s"), 0.0, 4_430_000.0, 4_430_000.0), "4M");
    }

    /// d3's `exponent` reads the exponent of the value itself, so a value that
    /// ROUNDS up to a power of ten (9.6 to one digit is 1e1) keeps its own.
    #[test]
    fn exponent_never_rounds_across_a_power_of_ten() {
        assert_eq!(exponent(9.6), 0.0);
        assert_eq!(exponent(99_999.0), 4.0);
        assert_eq!(exponent(0.096), -2.0);
        assert_eq!(precision_fixed(0.096), Some(2));
        assert_eq!(precision_fixed(0.0), None);
        assert_eq!(precision_fixed(1000.0), Some(0));
        assert_eq!(precision_fixed(0.05), Some(2));
        // 999.6 sits under 1e3: no SI prefix, where a rounded exponent would pick `k` and print `1k`.
        assert_eq!(span(Some("s"), 0.0, 999.6, 999.6), "1000");
    }

    #[test]
    fn tick_format_keeps_an_authored_precision() {
        assert_eq!(span(Some("$,.0f"), 406_240.0, 605_760.0, 406_240.0), "$406,240");
    }

    #[test]
    fn tick_format_fixes_one_si_prefix_for_the_whole_span() {
        assert_eq!(span(Some("s"), 0.0, 81_800.0, 81_800.0), "82k");
        assert_eq!(span(Some("s"), 0.0, 81_800.0, 40_900.0), "41k");
    }

    #[test]
    fn tick_format_of_a_collapsed_span_uses_the_default_precision() {
        assert_eq!(span(Some("f"), 0.0, 0.0, 3.0), "3.000000");
    }

    #[test]
    fn format_float_prints_twelve_digits_and_trims_the_fraction() {
        assert_eq!(float(None, 0.5), "0.5");
        assert_eq!(float(None, 1234.567_890_123_456_7), "1,234.56789012");
        assert_eq!(float(Some(".3~s"), 1e8), "100M");
        assert_eq!(float(Some("s"), 81_800.0), "81.8k");
        assert_eq!(float(Some(".0%"), 0.25), "25%");
        assert_eq!(float(Some("e"), 12_345.678), "1.2345678e+4");
        assert_eq!(float(Some(",.2f"), 1.5), "1.50");
        assert_eq!(float(Some("%"), 0.123_456_789), "12.3456789%");
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
    }

    // --- Fill, align, width and zero padding (d3's closing pad step) ---

    #[test]
    fn fill_align_width_and_zero_pad_like_the_reference() {
        // uv run python -c "from d3_format import format as f; print(repr(f(SPEC, VALUE)))"
        for (spec, value, want) in [
            ("'>8,.0f", 1234.0, "'''1,234"),
            ("'>8,.0f", -1234.0, "''\u{2212}1,234"),
            (">10.2f", 3.14159, "      3.14"),
            ("<10.2f", 3.14159, "3.14      "),
            ("^10.2f", 3.14159, "   3.14   "),
            ("*^9d", 42.0, "***42****"),
            ("=+10.1f", -5.25, "\u{2212}      5.3"),
            ("08.2f", -3.5, "\u{2212}0003.50"),
            ("010,d", 1234567.0, "01,234,567"),
            ("020,.2f", -1234.5, "\u{2212}0,000,000,001,234.50"),
            ("0>10.2f", 1.0, "0000001.00"),
            ("\u{b7}>8d", 7.0, "\u{b7}\u{b7}\u{b7}\u{b7}\u{b7}\u{b7}\u{b7}7"),
            (">6.0%", 0.25, "   25%"),
            ("(10,.0f", -1234.0, "   (1,234)"),
            ("$>12,.2f", 1234.5, "$$$$1,234.50"),
            (">8d", f64::NAN, "     NaN"),
            (">8d", f64::INFINITY, "       \u{221e}"),
            ("08d", f64::NEG_INFINITY, "\u{2212}000000\u{221e}"),
            ("4d", 123456.0, "123456"),
            (">8.3~s", 1500.0, "    1.5k"),
            // A non-finite body joins the suffix: never grouped, never split.
            (",f", f64::INFINITY, "Infinity"),
            ("$,.0f", f64::NEG_INFINITY, "\u{2212}$Infinity"),
            ("08,d", f64::INFINITY, "000,000\u{221e}"),
            ("09,.0f", f64::INFINITY, "0Infinity"),
            ("02,d", f64::INFINITY, "0\u{221e}"),
            ("010,f", f64::NAN, "000,000NaN"),
            (",d", f64::NAN, "NaN"),
        ] {
            assert_eq!(fmt(spec, value), want, "{spec} on {value}");
        }
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

#[cfg(test)]
mod fast_rounding_tests {
    use super::*;

    /// splitmix64: a dependency-free deterministic stream of raw bit patterns.
    fn doubles(seed: u64, n: usize) -> impl Iterator<Item = f64> {
        let mut state = seed;
        std::iter::repeat_with(move || {
            state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
            let mut z = state;
            z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
            z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
            z ^ (z >> 31)
        })
        .map(f64::from_bits)
        .map(f64::abs)
        .filter(|x| x.is_finite() && *x > 0.0)
        .take(n)
    }

    /// Values a chart formats: short decimals, dyadic ties, carries, and
    /// magnitudes from subnormal to the 1e21 fixed-notation ceiling.
    fn cases() -> Vec<f64> {
        let mut v = vec![
            102.85, 2.25, 0.5, 1.5, 2.5, 0.125, 0.375, 9.95, 9.5, 99.5, 999.5, 0.05, 0.15,
            1.005, 25.0, 250.0, 125.0, 75.0, 1e20, 9.999_999_999_999_999e20, 5e-324,
            f64::MIN_POSITIVE, 1.0, 10.0, 0.1, 0.3, 1e-7, 123_456_789.012_345_68,
            4_503_599_627_370_496.5, 9_007_199_254_740_993.0, 2.0f64.powi(-20), 3.0 * 2.0f64.powi(-12),
        ];
        for den in [2.0, 4.0, 8.0, 16.0, 32.0, 64.0] {
            for num in 0..400 {
                v.push(num as f64 / den);
            }
        }
        for i in 0..2000 {
            v.push((i * 5) as f64);
            v.push(i as f64 * 0.01);
        }
        for (i, x) in doubles(1, 20_000).enumerate() {
            let scale = 10f64.powi((i % 40) as i32 - 20);
            v.push((x.to_bits() % 1_000_000_007) as f64 / 1e4 * scale);
        }
        v.extend(doubles(2, 20_000).filter(|x| *x < 1e21));
        v.retain(|x| *x > 0.0 && *x < 1e21);
        v
    }

    #[test]
    fn fast_fixed_rounding_matches_the_exact_expansion() {
        for x in cases() {
            for d in 0..=20 {
                assert_eq!(round_half_up_abs(x, d), round_exact_abs(x, d), "x={x:e} d={d}");
            }
        }
    }

    #[test]
    fn fast_significant_rounding_matches_the_exact_expansion() {
        for x in cases() {
            for p in 1..=21 {
                assert_eq!(format_decimal_parts(x, p), format_decimal_parts_exact(x, p), "x={x:e} p={p}");
            }
        }
    }

    #[test]
    fn ties_round_half_up_on_the_absolute_value() {
        assert_eq!(round_half_up_abs(102.85, 1), "102.8");
        assert_eq!(round_half_up_abs(2.25, 1), "2.3");
        assert_eq!(round_half_up_abs(0.5, 0), "1");
        assert_eq!(round_half_up_abs(9.5, 0), "10");
        assert_eq!(format_decimal_parts(25.0, 1), ("3".to_string(), 1));
        assert_eq!(format_decimal_parts(9.5, 1), ("1".to_string(), 1));
    }
}
