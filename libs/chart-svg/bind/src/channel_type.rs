//! A chart field's channel type, read off its declared column type and its cells:
//! the one fact that picks an axis slot. Ported from `_channels.py::
//! _classify_to_channel_type` and `enrich.py`'s `classify_column_type`, `is_year_shaped`
//! and `_is_temporal_value`; every verdict Python reaches from a native `date`,
//! `Decimal` or `str` is reached here from the declared type, and a cell the JSON
//! boundary leaves ambiguous (a Unicode-digit year) is refused by name.

use column_types::ColumnTypes;
use diagnostic::{refuse, Diagnostic};
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


/// `enrich.py::classify_column_type`, reduced to the `"temporal"` verdict
/// versus anything else: `_classify_to_channel_type` maps production's
/// `"numeric"` and `"categorical"` alike to `"nominal"`, and the one reader of
/// the numeric vote, `y_is_numeric`, tallies it itself.
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
/// side here.
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

/// Whether a line's or a bar's `y` is numeric as `_domain.py::_first_non_numeric_y` reads it:
/// by `classify_column_type` over the first 20 non-null cells, so a numeric-looking string
/// counts as a number and a column with no non-null cell never trips the guard. `Ok(false)` is
/// production's "y is not numeric" error, which the caller raises with its own code.
///
/// The engine draws only a declared number, so a column production does not call non-numeric
/// but the schema declares otherwise (text that reads as numbers, or no value at all) refuses
/// by name: production draws it, and the production error would be the wrong answer.
pub fn y_is_numeric(types: &ColumnTypes, rows: &Rows, field: &str) -> Result<bool, Diagnostic> {
    let declared = types.get(field).map_err(Diagnostic::refused)?;
    let samples = first_non_null_samples(rows, field, 20);
    if !samples.is_empty() && !numeric_vote(field, &samples)? {
        return Ok(false);
    }
    if declared.is_number() {
        return Ok(true);
    }
    Err(Diagnostic::refused(refuse!(
        "a `y` column declared as text, whose cells read as numbers or hold no value, is not supported"
    )))
}

/// `classify_column_type`'s `"numeric"` verdict: not temporal, and over 80% of the samples are
/// native numbers or numeric strings (`enrich.py::_is_numeric_string`).
fn numeric_vote(field: &str, samples: &[&Value]) -> Result<bool, Diagnostic> {
    if classify_column_type(field, samples) == "temporal" {
        return Ok(false);
    }
    let mut numeric = 0usize;
    for cell in samples {
        numeric += match cell {
            Value::Number(_) => 1,
            Value::String(text) if !crate::column_kind::is_date_text(text) => usize::from(is_numeric_string(text)?),
            _ => 0,
        };
    }
    Ok(numeric as f64 / samples.len() as f64 > 0.8)
}

/// `enrich.py::_is_numeric_string`: Python's `float()` over the text with `,`, `$` and `%`
/// removed. A shape only Python's `float()` reads (an underscore or a Unicode digit) refuses
/// by name rather than guessing.
fn is_numeric_string(text: &str) -> Result<bool, Diagnostic> {
    let stripped = text.replace([',', '$', '%'], "");
    let stripped = stripped.trim();
    if stripped.parse::<f64>().is_ok() {
        return Ok(true);
    }
    let python_only = stripped.replace('_', "").parse::<f64>().is_ok_and(|_| stripped.contains('_'))
        || stripped.chars().any(|c| !c.is_ascii() && c.is_numeric());
    if python_only {
        return Err(Diagnostic::refused(refuse!(
            "a numeric string read by Python's `float()` through an underscore or a Unicode digit is not supported"
        )));
    }
    Ok(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn vote(field: &str, cells: &[Value]) -> Result<bool, Diagnostic> {
        numeric_vote(field, &cells.iter().collect::<Vec<_>>())
    }

    #[test]
    fn numeric_strings_and_native_numbers_vote_numeric_text_and_dates_do_not() {
        assert!(vote("y", &[json!("12.5"), json!("3,000"), json!("$4"), json!(7)]).unwrap());
        assert!(!vote("y", &[json!("a"), json!("b")]).unwrap());
        assert!(!vote("y", &[json!("12"), json!("b"), json!("c")]).unwrap());
        assert!(!vote("y", &[json!("2024-01-01"), json!("2024-02-01")]).unwrap());
        assert!(!vote("y", &[json!(true), json!(false)]).unwrap());
        assert!(!vote("month", &[json!("2024-01-01"), json!("5")]).unwrap());
    }

    #[test]
    fn a_shape_only_pythons_float_reads_refuses_by_name() {
        assert!(vote("y", &[json!("1_000")]).unwrap_err().to_string().contains("is not supported"));
        assert!(vote("y", &[json!("١٢")]).unwrap_err().to_string().contains("is not supported"));
        assert!(!vote("y", &[json!("café"), json!("a_b")]).unwrap());
    }
}
