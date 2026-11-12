//! A column's kind, decided once per `(QueryId, column)` from its declared type, and the
//! one table every channel role reads it through.
//!
//! Only a declared `string` or `json` column is read by value, and only an `integer`, `decimal`
//! or `json` column is read for year shape; every other declared type decides alone. A value read
//! sees the window its production reader sees (`Window`): the first `SAMPLE` non-null cells for
//! the channel type law (`enrich.py::first_non_null_samples`), the first rows for Vega's type
//! inference, every non-null cell where production rewrites every row. Sampling is the law where
//! production samples, not an approximation of a full scan.

use column_types::{ColumnType, ColumnTypes};
use diagnostic::{diag, refuse, Diagnostic};
use serde_json::{Map, Value};
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
    /// date-shaped in the first five, or more than 80% of its values are. `date_like`: every
    /// value is a standard date or a date-like bucket, so it may be plotted. `standard`: every
    /// value is a standard date (`type_inference.py::is_standard_date`). `numeric`: more
    /// than 80% of the values read as numbers to Python's `float()` (after `,`, `$` and `%`
    /// are dropped), which production draws as a measure and the engine refuses by name.
    /// `ambiguous_year`: every value is a four-digit year, one of them in non-ASCII digits,
    /// whose value Python reads and this crate cannot; a dimension refuses it by name.
    Text { year: bool, dated: bool, date_like: bool, standard: bool, numeric: bool, ambiguous_year: bool },
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

/// The cells of a column a production reader looks at. Production's readers do not share one
/// window, so the window is the caller's, and each caller names the one its production reader uses.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Window {
    /// The first `n` non-null cells (`enrich.py::first_non_null_samples`; the channel type law).
    NonNull(usize),
    /// The non-null cells of the first `n` rows (`type_inference.py::infer_vega_type_from_data`,
    /// `core/utils.py::vega_infers_quantitative`).
    Rows(usize),
    /// Every non-null cell (`time_unit_detect.py::normalize_labeled_temporal`, which then rewrites
    /// every row, so a sample would be wrong).
    All,
}

/// How many non-null cells the channel type law reads.
pub const SAMPLE: usize = 20;

const YEARS: std::ops::RangeInclusive<i64> = 1700..=2100;

/// The kind of column `name`, declared `declared`, whose windowed cells are `cells`. The cells have
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
        ColumnType::Json => {
            // A json cell can be a bare number or text, which production reads as one, years included.
            let cells: Vec<&Value> = cells.collect();
            if cells.iter().all(|cell| cell.is_number()) {
                ColumnKind::Number { year: cells.iter().all(|cell| is_year_number(cell)) }
            } else if cells.iter().all(|cell| cell.is_string()) {
                text_kind(name, cells.iter().filter_map(|cell| cell.as_str()))
            } else {
                ColumnKind::Other
            }
        }
        ColumnType::Time | ColumnType::Bytes | ColumnType::List(_) | ColumnType::Struct(_) => ColumnKind::Other,
    }
}

fn is_year_number(cell: &Value) -> bool {
    match cell.as_i64() {
        Some(n) => YEARS.contains(&n),
        None => cell.as_f64().is_some_and(|f| f.fract() == 0.0 && YEARS.contains(&(f as i64))),
    }
}

pub(crate) fn text_kind<'a>(name: &str, texts: impl Iterator<Item = &'a str>) -> ColumnKind {
    let (mut count, mut dated, mut head_dated, mut numeric) = (0usize, 0usize, false, 0usize);
    let (mut years, mut ambiguous, mut date_like, mut standard) = (true, false, true, true);
    for text in texts {
        let is_date = is_date_text(text);
        head_dated |= is_date && count < HEAD;
        count += 1;
        dated += usize::from(is_date);
        numeric += usize::from(!is_date && is_numeric_text(text));
        let is_standard = is_standard_date_shape(text);
        standard &= is_standard;
        date_like &= is_standard || is_date_like_shape(text);
        // Python's `$` also matches before one trailing newline.
        match year_text(text.strip_suffix('\n').unwrap_or(text)) {
            YearText::Year => {}
            YearText::Ambiguous => ambiguous = true,
            YearText::No => years = false,
        }
    }
    let named = date_name().is_match(&name.to_lowercase());
    let all = count > 0 && years;
    ColumnKind::Text {
        year: all && !ambiguous,
        dated: (named && head_dated) || (count > 0 && dated as f64 / count as f64 > 0.8),
        date_like: date_like && count > 0,
        standard: standard && count > 0,
        numeric: count > 0 && numeric as f64 / count as f64 > 0.8,
        ambiguous_year: all && ambiguous,
    }
}

/// How many leading samples the name gate of `classify_column_type` reads.
const HEAD: usize = 5;

enum YearText {
    Year,
    /// `^\d{4}$` over non-ASCII decimal digits: Python's `int()` reads their value, this crate
    /// cannot.
    Ambiguous,
    No,
}

fn year_text(text: &str) -> YearText {
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    if !RE.get_or_init(|| regex::Regex::new(r"^\d{4}$").expect("valid literal")).is_match(text) {
        YearText::No
    } else if !text.is_ascii() {
        YearText::Ambiguous
    } else if text.parse().is_ok_and(|year: i64| YEARS.contains(&year)) {
        YearText::Year
    } else {
        YearText::No
    }
}

/// `enrich.py::_is_numeric_string`: Python's `float()` over the text with `,`, `$` and `%`
/// removed, which also reads an underscore between digits and any Unicode digit.
fn is_numeric_text(text: &str) -> bool {
    let stripped = text.replace([',', '$', '%'], "");
    let stripped = stripped.trim();
    stripped.parse::<f64>().is_ok()
        || (stripped.contains('_') && stripped.replace('_', "").parse::<f64>().is_ok())
        || stripped.chars().any(|c| !c.is_ascii() && c.is_numeric())
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

/// The non-null cells of `field` that `window` of `rows` shows.
pub(crate) fn window_cells<'a>(rows: &'a [Map<String, Value>], field: &'a str, window: Window) -> impl Iterator<Item = &'a Value> {
    let (rows_seen, cells_seen) = match window {
        Window::NonNull(n) => (usize::MAX, n),
        Window::Rows(n) => (n, usize::MAX),
        Window::All => (usize::MAX, usize::MAX),
    };
    rows.iter().take(rows_seen).filter_map(move |row| row.get(field)).filter(|cell| !cell.is_null()).take(cells_seen)
}

/// The kind of `field` over the `window` of `rows`, read through the dataset's declared type; an
/// undeclared field is an error.
pub fn field_kind_in(types: &ColumnTypes, rows: &[Map<String, Value>], field: &str, window: Window) -> Result<ColumnKind, Diagnostic> {
    let declared = types.get(field).map_err(Diagnostic::refused)?;
    Ok(column_kind(field, declared, window_cells(rows, field, window)))
}

/// The kind of `field` over its first [`SAMPLE`] non-null cells.
pub fn field_kind(types: &ColumnTypes, rows: &[Map<String, Value>], field: &str) -> Result<ColumnKind, Diagnostic> {
    field_kind_in(types, rows, field, Window::NonNull(SAMPLE))
}

/// The scale `field` draws on in `role`, read off its declared type and its first
/// [`SAMPLE`] non-null cells.
pub fn field_channel(types: &ColumnTypes, rows: &[Map<String, Value>], field: &str, role: Role) -> Result<ChannelType, Diagnostic> {
    channel_type(field_kind(types, rows, field)?, role, field)
}

/// Whether a line's or a bar's `y` is numeric as `_domain.py::_first_non_numeric_y` reads it.
/// `Ok(false)` is production's "y is not numeric" error, which the caller raises with its own
/// code. The engine draws only a declared number, so a column production draws but the schema
/// declares otherwise (text that reads as numbers, or no value at all) refuses by name.
pub fn y_is_numeric(types: &ColumnTypes, rows: &[Map<String, Value>], field: &str) -> Result<bool, Diagnostic> {
    let refused = || Diagnostic::refused(refuse!("a `y` column declared as text, whose cells read as numbers or hold no value, is not supported"));
    let declared_number = types.get(field).map_err(Diagnostic::refused)?.is_number();
    match field_kind(types, rows, field)? {
        ColumnKind::Number { .. } | ColumnKind::Empty => declared_number.then_some(true).ok_or_else(refused),
        ColumnKind::Text { dated: false, numeric: true, .. } => Err(refused()),
        ColumnKind::Date | ColumnKind::Boolean | ColumnKind::Text { .. } => Ok(false),
        // The engine draws no json, list, struct, time or bytes `y`, and production's vote over such a
        // column reads native numbers and numeric text alike: refuse rather than answer for it.
        ColumnKind::Other => Err(refused()),
    }
}

/// A column of 4-digit strings in non-ASCII digits: a year to Python's `int()`, which this crate
/// cannot compute without a second Unicode number parser.
pub(crate) fn unicode_digit_year() -> Diagnostic {
    Diagnostic::refused(refuse!("a dimension of 4-digit Unicode-numeric strings, a year only Python's `int()` can read, is not supported"))
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
        (Role::Dimension, K::Text { ambiguous_year: true, .. }) => return Err(unicode_digit_year()),
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
        let mut cells = vec![json!(2020); 19];
        cells.push(json!(5));
        assert_eq!(column_kind("v", &ColumnType::Integer, &cells), ColumnKind::Number { year: false });
    }

    fn column(kind: &str, cells: Vec<Value>) -> (ColumnTypes, Vec<Map<String, Value>>) {
        let declared = json!({"v": {"kind": kind, "element": null, "fields": null}});
        let rows = cells.into_iter().map(|cell| json!({"v": cell}).as_object().unwrap().clone()).collect();
        (serde_json::from_value(declared).unwrap(), rows)
    }

    #[test]
    fn a_read_sees_the_window_its_production_reader_does() {
        let year = ColumnKind::Number { year: true };
        let not_year = ColumnKind::Number { year: false };
        let mut cells = vec![Value::Null];
        cells.extend(vec![json!(2020); SAMPLE]);
        cells.push(json!(5));
        let (types, rows) = column("integer", cells);
        // The first SAMPLE non-null cells: the 5 past them is not seen.
        assert_eq!(field_kind(&types, &rows, "v").unwrap(), year);
        assert_eq!(field_kind_in(&types, &rows, "v", Window::All).unwrap(), not_year);
        // The first rows, null or not: the first three rows hold a null and two years.
        let (types, rows) = column("integer", vec![json!(2020), Value::Null, json!(2021), json!(5)]);
        assert_eq!(field_kind_in(&types, &rows, "v", Window::Rows(3)).unwrap(), year);
        assert_eq!(field_kind_in(&types, &rows, "v", Window::Rows(4)).unwrap(), not_year);
        let (types, rows) = column("string", vec![json!("2024"); SAMPLE].into_iter().chain([json!("x")]).collect());
        assert!(matches!(field_kind(&types, &rows, "v").unwrap(), ColumnKind::Text { year: true, .. }));
        assert!(matches!(field_kind_in(&types, &rows, "v", Window::All).unwrap(), ColumnKind::Text { year: false, .. }));
    }

    fn text(year: bool, dated: bool, date_like: bool) -> ColumnKind {
        ColumnKind::Text { year, dated, date_like, standard: false, numeric: false, ambiguous_year: false }
    }

    #[test]
    fn text_is_dated_by_its_name_or_a_vote_over_every_value() {
        let five_of_six = json!(["2024-01-02", "2024-Q1", "Jan 2024", "03/04/2024", "2024-W05", "n/a"]);
        assert_eq!(kind("label", ColumnType::String, five_of_six), text(false, true, false));
        let four_of_five = json!(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "n/a"]);
        assert_eq!(kind("label", ColumnType::String, four_of_five), text(false, false, false));
        let in_the_first_five = json!(["x", "y", "z", "w", "2024-01-02", "v"]);
        assert_eq!(kind("order_date", ColumnType::String, in_the_first_five), text(false, true, false));
        // The name gate reads the first five samples only, as `classify_column_type` does.
        let past_the_first_five = json!(["x", "y", "z", "w", "v", "2024-01-02"]);
        assert_eq!(kind("order_date", ColumnType::String, past_the_first_five), text(false, false, false));
    }

    #[test]
    fn text_is_a_year_or_plottable_only_when_every_value_is() {
        let years = kind("v", ColumnType::String, json!(["2024", "2025\n"]));
        assert_eq!(years, ColumnKind::Text { year: true, dated: false, date_like: false, standard: false, numeric: true, ambiguous_year: false });
        let standard = kind("v", ColumnType::String, json!(["2024-01-02", "2024-01-03"]));
        assert_eq!(standard, ColumnKind::Text { year: false, dated: true, date_like: true, standard: true, numeric: false, ambiguous_year: false });
        assert!(matches!(kind("v", ColumnType::String, json!(["12", "3.5"])), ColumnKind::Text { year: false, date_like: false, .. }));
        let buckets = kind("v", ColumnType::String, json!(["FY2024", "FY2025"]));
        assert_eq!(buckets, text(false, false, true));
        assert_eq!(channel_type(buckets, Role::Measure, "v").unwrap(), ChannelType::Temporal);
    }

    #[test]
    fn text_reads_as_numbers_as_pythons_float_does() {
        let numeric = |cells: Value| matches!(kind("v", ColumnType::String, cells), ColumnKind::Text { numeric: true, .. });
        assert!(numeric(json!(["12.5", "3,000", "$4", "5%", "1_000"])));
        assert!(numeric(json!(["١٢"])));
        assert!(!numeric(json!(["12", "b", "c"])));
        assert!(!numeric(json!(["2024-01-01", "2024-02-01"])));
    }

    #[test]
    fn a_unicode_digit_year_refuses_a_dimension_by_name() {
        let wide = kind("v", ColumnType::String, json!(["２０２１", "2022"]));
        assert!(matches!(wide, ColumnKind::Text { year: false, ambiguous_year: true, .. }));
        assert!(channel_type(wide, Role::Dimension, "v").unwrap_err().to_string().contains("is not supported"));
        assert_eq!(channel_type(wide, Role::Free, "v").unwrap(), ChannelType::Nominal);
        let disqualified = kind("v", ColumnType::String, json!(["２０２１", "9999"]));
        assert!(matches!(disqualified, ColumnKind::Text { year: false, ambiguous_year: false, .. }));
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
