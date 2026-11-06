//! A chart field's channel type, read off its declared column type and its cells:
//! the one fact that picks an axis slot. Ported from `_channels.py::
//! _classify_to_channel_type` and `enrich.py`'s `classify_column_type`, `is_year_shaped`
//! and `_is_temporal_value`; every verdict Python reaches from a native `date`,
//! `Decimal` or `str` is reached here from the declared type, and a cell the JSON
//! boundary leaves ambiguous (a Unicode-digit year) is refused by name.

use column_types::ColumnTypes;
use diagnostic::Diagnostic;
use serde_json::{Map, Value};

/// Which of the three kinds of axis a field draws: `Quantitative` and `Temporal`
/// are continuous, `Nominal` is a band.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChannelType {
    Quantitative,
    Temporal,
    Nominal,
}

/// A dataset's rows.
pub type Rows = [Map<String, Value>];




const YEAR_MIN: i64 = 1700;

const YEAR_MAX: i64 = 2100;


/// `enrich.py::classify_column_type`, reduced to the two-way split this
/// crate's one caller reads (`"temporal"` vs anything else — production's
/// own `"numeric"`/`"categorical"` split is unobservable through `_classify_
/// to_channel_type`, since both map to `"nominal"` there; no caller in
/// this crate needs the split itself, so it is not ported — reintroduce
/// `is_numeric_string`/the numeric vote if one ever does).
///
/// A column-NAME gate (any of twelve temporal-word patterns, `re.search`
/// over the lowercased name — not anchored, so `"day"` matches inside
/// `"birthday"` too) paired with a temporal-valued sample in the FIRST 5
/// wins outright; otherwise an 80%-majority vote over every sample decides.
///
/// The vote counts a native `date`/`datetime` object and a temporal-shaped
/// string alike (`enrich.py:108-122`), so a `date` serialized through
/// `rows.json` as `YYYY-MM-DD` votes temporal with no need to tell it from a
/// genuine string of the same shape. A native number counts toward neither
/// side here (production's numeric tally is unobservable at the one caller).
fn classify_column_type(field: &str, samples: &[&Value]) -> &'static str {
    let name_lower = field.to_lowercase();
    let first_five = &samples[..samples.len().min(5)];
    if crate::column_kind::date_name().is_match(&name_lower) && first_five.iter().any(|v| crate::column_kind::is_date_text(v.as_str().unwrap_or_default())) {
        return "temporal";
    }
    let temporal_count = samples
        .iter()
        .filter(|v| matches!(v, Value::String(_)) && crate::column_kind::is_date_text(v.as_str().unwrap_or_default()))
        .count();
    if temporal_count as f64 / samples.len() as f64 > 0.8 {
        "temporal"
    } else {
        "categorical"
    }
}

enum YearVerdict {
    YearShaped,
    /// A 4-character string matching `year_string_re` (Unicode `\d{4}`, the
    /// same class Python's `\d` matches — decimal digits, category Nd) that
    /// contains at least one NON-ASCII character (e.g. full-width
    /// `"２０２１"`, Arabic-Indic `"٢٠٢١"`). Python's own `\d`/`int()` are
    /// Unicode-digit-VALUE-aware (`int("２０２１") == 2021`), so production
    /// can resolve a real year value here that this crate cannot compute
    /// without a second Unicode number parser — refused by name rather than
    /// silently read as `NotYearShaped` (which would let a caller guess
    /// "not temporal" for a column production calls temporal). An all-ASCII
    /// 4-digit string never reaches this verdict: its value is computed
    /// directly, in or out of range.
    AmbiguousUnicodeDigits,
    NotYearShaped,
}

/// `^\d{4}$`, Unicode mode (Rust regex's default) — matches exactly the
/// class Python's own `_YEAR_STRING_PATTERN` does (`\d` is category Nd,
/// decimal digit, in both), so a Unicode-numeric-but-not-decimal-digit
/// shape (a superscript, category No) never matches here either, on either
/// side.
fn year_string_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"^\d{4}$").expect("valid literal"))
}

/// `core/utils.py::is_year_shaped`: every sample must qualify. A disqualifying
/// sample wins over an ambiguous one.
fn year_verdict(samples: &[&Value]) -> YearVerdict {
    let mut ambiguous_unicode_digits = false;
    for v in samples {
        match v {
            Value::Bool(_) => return YearVerdict::NotYearShaped,
            Value::Number(n) => {
                if let Some(i) = n.as_i64() {
                    if !(YEAR_MIN..=YEAR_MAX).contains(&i) {
                        return YearVerdict::NotYearShaped;
                    }
                } else if let Some(f) = n.as_f64() {
                    if f.fract() != 0.0 {
                        return YearVerdict::NotYearShaped;
                    }
                    let i = f as i64;
                    if !(YEAR_MIN..=YEAR_MAX).contains(&i) {
                        return YearVerdict::NotYearShaped;
                    }
                } else {
                    return YearVerdict::NotYearShaped;
                }
            }
            Value::String(s) => {
                // Python's `$` also matches before one trailing newline.
                let trimmed = s.strip_suffix('\n').unwrap_or(s);
                if !year_string_re().is_match(trimmed) {
                    return YearVerdict::NotYearShaped;
                }
                if trimmed.is_ascii() {
                    let i: i64 = trimmed.parse().expect("4 ASCII decimal digits");
                    if !(YEAR_MIN..=YEAR_MAX).contains(&i) {
                        return YearVerdict::NotYearShaped;
                    }
                } else {
                    // Python's `int()` reads any Unicode digit; computing its value needs a
                    // second number parser, so refuse by name instead of guessing.
                    ambiguous_unicode_digits = true;
                }
            }
            _ => return YearVerdict::NotYearShaped,
        }
    }
    if ambiguous_unicode_digits {
        YearVerdict::AmbiguousUnicodeDigits
    } else {
        YearVerdict::YearShaped
    }
}

/// The first `limit` non-null values of `field`, in row order
/// (`enrich.py::first_non_null_samples`).
fn first_non_null_samples<'a>(rows: &'a Rows, field: &str, limit: usize) -> Vec<&'a Value> {
    rows.iter().filter_map(|row| row.get(field)).filter(|cell| !cell.is_null()).take(limit).collect()
}

/// `_channels.py::_classify_to_channel_type`. The declared type decides when it
/// says more than "string": a date or timestamp is `Temporal`, a number
/// `Quantitative` (a dimension of year-shaped integers or decimals is `Temporal`).
/// A string, and any other kind, is read off its cells as production reads them:
/// a year-shaped dimension is `Temporal` (a verdict the JSON row boundary cannot
/// settle refuses by name), numbers are `Quantitative`, and `classify_column_type`'s
/// rule decides `Temporal` vs `Nominal`. A field with no non-null sample, or no
/// field, is `Nominal`; a field the schema does not declare is an error.
pub fn classify(
    types: &ColumnTypes,
    rows: &Rows,
    field: Option<&str>,
    is_dimension: bool,
) -> Result<ChannelType, Diagnostic> {
    let Some(field) = field else {
        return Ok(ChannelType::Nominal);
    };
    let declared = types.get(field).map_err(Diagnostic::refused)?;
    let samples = first_non_null_samples(rows, field, 20);
    if samples.is_empty() {
        return Ok(ChannelType::Nominal);
    }
    if declared.is_temporal() {
        return Ok(ChannelType::Temporal);
    }
    if declared.is_number() {
        let year = is_dimension
            && crate::rows::is_year_shaped(types, rows, field).map_err(Diagnostic::refused)?;
        return Ok(if year {
            ChannelType::Temporal
        } else {
            ChannelType::Quantitative
        });
    }
    if is_dimension {
        match year_verdict(&samples) {
            YearVerdict::YearShaped => return Ok(ChannelType::Temporal),
            YearVerdict::AmbiguousUnicodeDigits => {
                return Err(Diagnostic::refused(format!(
                    "{field}: a 4-digit Unicode-numeric string ambiguous \
                     as a year without a second Unicode number parser"
                )));
            }
            YearVerdict::NotYearShaped => {}
        }
    }
    if samples.iter().all(|cell| cell.is_number()) {
        return Ok(ChannelType::Quantitative);
    }
    Ok(if classify_column_type(field, &samples) == "temporal" {
        ChannelType::Temporal
    } else {
        ChannelType::Nominal
    })
}
