//! A column's kind, decided once per `(QueryId, column)` from its declared type, and the
//! one table every channel role reads it through.
//!
//! Only a declared `string` column is read by value, and only an `integer` or `decimal`
//! column is read for year shape; every other declared type decides alone. Value reads scan
//! every non-null cell, never a sample.

use column_types::ColumnType;
use diagnostic::{diag, Diagnostic};
use serde_json::Value;
use std::sync::OnceLock;

use crate::temporal::{is_date_like_shape, is_standard_date_shape};


/// What a column holds, as far as a channel cares.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ColumnKind {
    /// No non-null cell.
    Empty,
    /// A declared integer, float or decimal. `year`: an integer or decimal column whose
    /// every value is a whole number in 1700..=2100.
    Number { year: bool },
    /// A declared date or timestamp.
    Date,
    Boolean,
    /// Text, which never reads as a number however it looks. `year`: every value is four
    /// digits in 1700..=2100. `dated`: the column's name says date and some value is
    /// date-shaped, or more than 80% of its values are. `date_like`: every value is a
    /// standard date or a date-like bucket, so it may be plotted.
    Text { year: bool, dated: bool, date_like: bool },
    /// A time of day, bytes, JSON, a list or a struct.
    Other,
}

/// How a channel reads its column.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    /// A category or position axis, a series or a facet field: a year reads as a date.
    Dimension,
    /// A plotted number (a bar, line or area y, `y_start`, a size, a theta, a gradient).
    Measure,
    /// An axis that may be a number, a date or a band (a scatter y).
    Free,
    /// A color or pattern field, which ramps over numbers and booleans.
    Color,
}

/// The scale family a channel draws on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChannelType {
    Quantitative,
    Temporal,
    Nominal,
}

const YEARS: std::ops::RangeInclusive<i64> = 1700..=2100;

/// The kind of column `name`, declared `declared`, whose cells are `cells`. The cells have
/// passed the dataset's decode against the same declaration.
pub fn column_kind<'a>(name: &str, declared: &ColumnType, cells: impl IntoIterator<Item = &'a Value>) -> ColumnKind {
    let mut cells = cells.into_iter().filter(|cell| !cell.is_null()).peekable();
    if cells.peek().is_none() {
        return ColumnKind::Empty;
    }
    match declared {
        ColumnType::Integer | ColumnType::Decimal => ColumnKind::Number { year: cells.all(is_year_number) },
        ColumnType::Float => ColumnKind::Number { year: false },
        ColumnType::Date | ColumnType::Datetime | ColumnType::DatetimeTz => ColumnKind::Date,
        ColumnType::Boolean => ColumnKind::Boolean,
        ColumnType::String => text_kind(name, cells.filter_map(Value::as_str)),
        ColumnType::Time | ColumnType::Bytes | ColumnType::Json | ColumnType::List(_) | ColumnType::Struct(_) => {
            ColumnKind::Other
        }
    }
}

fn is_year_number(cell: &Value) -> bool {
    match cell.as_i64() {
        Some(n) => YEARS.contains(&n),
        None => cell.as_f64().is_some_and(|f| f.fract() == 0.0 && YEARS.contains(&(f as i64))),
    }
}

fn text_kind<'a>(name: &str, texts: impl Iterator<Item = &'a str>) -> ColumnKind {
    let (mut count, mut dated, mut years, mut date_like) = (0usize, 0usize, true, true);
    for text in texts {
        count += 1;
        dated += usize::from(is_date_text(text));
        date_like &= is_standard_date_shape(text) || is_date_like_shape(text);
        // Python's `$` also matches before one trailing newline.
        let digits = text.strip_suffix('\n').unwrap_or(text);
        years &= digits.len() == 4
            && digits.bytes().all(|b| b.is_ascii_digit())
            && digits.parse().is_ok_and(|year: i64| YEARS.contains(&year));
    }
    let named = date_name().is_match(&name.to_lowercase());
    ColumnKind::Text {
        year: years && count > 0,
        dated: (named && dated > 0) || (count > 0 && dated as f64 / count as f64 > 0.8),
        date_like: date_like && count > 0,
    }
}

/// A column name that reads as a date or time; a substring match, so `birthday` counts.
pub(crate) fn date_name() -> &'static regex::RegexSet {
    static RE: OnceLock<regex::RegexSet> = OnceLock::new();
    RE.get_or_init(|| {
        regex::RegexSet::new([
            "date", "time", "timestamp", "created", "updated", "_at$", "day", "week", "month", "year", "quarter",
            "period",
        ])
        .expect("valid patterns")
    })
}

/// A date-shaped text, matched from its start: `\n?$` stands for Python's `$`, which also
/// matches before one trailing newline.
pub fn is_date_text(text: &str) -> bool {
    static RE: OnceLock<regex::RegexSet> = OnceLock::new();
    RE.get_or_init(|| {
        regex::RegexSet::new([
            r"(?i)^\d{4}-\d{2}-\d{2}",
            r"(?i)^\d{2}/\d{2}/\d{4}",
            r"(?i)^\d{2}-\d{2}-\d{4}",
            r"(?i)^\d{4}/\d{2}/\d{2}",
            r"(?i)^\w{3}\s+\d{1,2},?\s+\d{4}",
            r"(?i)^\d{4}-Q[1-4]\n?$",
            r"(?i)^Q[1-4]\s*\d{4}\n?$",
            r"(?i)^\d{4}Q[1-4]\n?$",
            r"(?i)^\d{4}-\d{2}\n?$",
            r"(?i)^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}\n?$",
            r"(?i)^\d{4}-W\d{2}\n?$",
        ])
        .expect("valid patterns")
    })
    .is_match(text)
}

/// The scale `column` of kind `kind` draws on in `role`. Text read as a measure is
/// refused: text never reads as a number.
pub fn channel_type(kind: ColumnKind, role: Role, column: &str) -> Result<ChannelType, Diagnostic> {
    use ChannelType::{Nominal, Quantitative, Temporal};
    use ColumnKind as K;
    Ok(match (role, kind) {
        (Role::Measure, K::Empty | K::Number { .. } | K::Boolean) => Quantitative,
        (Role::Measure, K::Date | K::Text { date_like: true, .. }) => Temporal,
        (Role::Measure, K::Text { .. } | K::Other) => return Err(diag::err_chart_measure_is_text(column)),
        (Role::Color, K::Empty | K::Number { .. } | K::Boolean) => Quantitative,
        (Role::Color, K::Date | K::Text { .. } | K::Other) => Nominal,
        (Role::Dimension, K::Number { year: true } | K::Text { year: true, .. }) => Temporal,
        (_, K::Number { .. }) => Quantitative,
        (_, K::Date | K::Text { dated: true, .. }) => Temporal,
        (_, K::Empty | K::Text { .. } | K::Boolean | K::Other) => Nominal,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn kind(name: &str, declared: ColumnType, cells: Value) -> ColumnKind {
        column_kind(name, &declared, cells.as_array().unwrap())
    }

    #[test]
    fn the_declared_type_decides_every_non_text_column() {
        assert_eq!(kind("v", ColumnType::Float, json!([2020.0, 2021.0])), ColumnKind::Number { year: false });
        assert_eq!(kind("v", ColumnType::Date, json!(["2024-01-01"])), ColumnKind::Date);
        assert_eq!(kind("v", ColumnType::Boolean, json!([true])), ColumnKind::Boolean);
        assert_eq!(kind("v", ColumnType::Json, json!([{"a": 1}])), ColumnKind::Other);
        assert_eq!(kind("v", ColumnType::String, json!([null, null])), ColumnKind::Empty);
    }

    #[test]
    fn an_integer_or_decimal_column_is_a_year_only_when_every_value_is_one() {
        assert_eq!(kind("v", ColumnType::Integer, json!([2020, null, 2021])), ColumnKind::Number { year: true });
        assert_eq!(kind("v", ColumnType::Decimal, json!([2020.0, 2021])), ColumnKind::Number { year: true });
        let mut cells = vec![json!(2020); 20];
        cells.push(json!(5));
        assert_eq!(column_kind("v", &ColumnType::Integer, &cells), ColumnKind::Number { year: false });
    }

    fn text(year: bool, dated: bool, date_like: bool) -> ColumnKind {
        ColumnKind::Text { year, dated, date_like }
    }

    #[test]
    fn text_is_dated_by_its_name_or_a_vote_over_every_value() {
        let five_of_six = json!(["2024-01-02", "2024-Q1", "Jan 2024", "03/04/2024", "2024-W05", "n/a"]);
        assert_eq!(kind("label", ColumnType::String, five_of_six), text(false, true, false));
        let four_of_five = json!(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "n/a"]);
        assert_eq!(kind("label", ColumnType::String, four_of_five), text(false, false, false));
        let one_of_six = json!(["x", "y", "z", "w", "v", "2024-01-02"]);
        assert_eq!(kind("order_date", ColumnType::String, one_of_six), text(false, true, false));
    }

    #[test]
    fn text_is_a_year_or_plottable_only_when_every_value_is() {
        assert_eq!(kind("v", ColumnType::String, json!(["2024", "2025\n"])), text(true, false, false));
        assert_eq!(kind("v", ColumnType::String, json!(["2024-01-02", "2024-01-03"])), text(false, true, true));
        assert_eq!(kind("v", ColumnType::String, json!(["12", "3.5"])), text(false, false, false));
        let buckets = kind("v", ColumnType::String, json!(["FY2024", "FY2025"]));
        assert_eq!(buckets, text(false, false, true));
        assert_eq!(channel_type(buckets, Role::Measure, "v").unwrap(), ChannelType::Temporal);
    }

    #[test]
    fn a_dimension_reads_a_year_as_a_date_and_a_free_axis_does_not() {
        let year = ColumnKind::Number { year: true };
        assert_eq!(channel_type(year, Role::Dimension, "y").unwrap(), ChannelType::Temporal);
        assert_eq!(channel_type(year, Role::Free, "y").unwrap(), ChannelType::Quantitative);
        let text_year = text(true, false, false);
        assert_eq!(channel_type(text_year, Role::Dimension, "y").unwrap(), ChannelType::Temporal);
        assert_eq!(channel_type(text_year, Role::Free, "y").unwrap(), ChannelType::Nominal);
    }

    #[test]
    fn a_color_ramps_over_numbers_and_booleans() {
        assert_eq!(channel_type(ColumnKind::Boolean, Role::Color, "b").unwrap(), ChannelType::Quantitative);
        assert_eq!(channel_type(ColumnKind::Boolean, Role::Dimension, "b").unwrap(), ChannelType::Nominal);
        assert_eq!(channel_type(ColumnKind::Date, Role::Color, "d").unwrap(), ChannelType::Nominal);
        assert_eq!(channel_type(text(false, true, true), Role::Free, "d").unwrap(), ChannelType::Temporal);
    }

    #[test]
    fn a_measure_is_a_number_a_boolean_or_dates_and_other_text_is_refused() {
        assert_eq!(channel_type(ColumnKind::Boolean, Role::Measure, "m").unwrap(), ChannelType::Quantitative);
        assert_eq!(channel_type(ColumnKind::Date, Role::Measure, "m").unwrap(), ChannelType::Temporal);
        assert_eq!(channel_type(text(false, false, true), Role::Measure, "m").unwrap(), ChannelType::Temporal);
        // Dated by its name, but not every value is a date: still text.
        let refused = channel_type(text(false, true, false), Role::Measure, "m").unwrap_err();
        assert_eq!(refused, diag::err_chart_measure_is_text("m"));
    }
}
