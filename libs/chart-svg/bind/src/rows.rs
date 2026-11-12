//! Reading dataset rows: cell access, Python and JS comparison laws, authored sort, and the
//! wire's cell tags. Nothing here measures text or builds a node.

use std::borrow::Cow;

use column_types::ColumnTypes;
use serde::Deserialize;
use serde_json::Value;

/// What the wire tagged on one cell: the Python `str()` of the value, and the
/// non-finite float a `null` stands for (`None` for a Decimal).
#[derive(Clone, Debug, PartialEq)]
pub struct CellTag {
    pub text: String,
    pub non_finite: Option<f64>,
}

/// The tags of one row's tagged cells, keyed by column.
pub type CellRow = std::collections::BTreeMap<String, CellTag>;

/// Per-row tags of the cells the wire tagged (`{"decimal": text}`,
/// `{"non_finite": kind, "text": text}`). A pivot labels its dimension columns
/// with the text, and KPI and spark_bar print a non-finite cell's text;
/// everything else reads the decoded number.
pub type CellText = Vec<CellRow>;

/// The tag of the cell at `row`, `column`, if the wire tagged it.
pub fn tag_at<'a>(tags: &'a [CellRow], row: usize, column: &str) -> Option<&'a CellTag> {
    tags.get(row)?.get(column)
}

/// The one place the wire's cell markers are read, so every family sees plain
/// numbers. JSON has no `NaN`/`Infinity`/exact Decimal, so the Python seam tags
/// a finite Decimal as `{"decimal": str(value)}` and a non-finite float or
/// Decimal as `{"non_finite": "nan" | "inf" | "-inf", "text": str(value)}`. A
/// Decimal becomes the float it converts to; a non-finite value becomes `null`,
/// the missing value every family already handles. Returns the tag of each
/// tagged top-level cell; a malformed marker is an error.
pub fn decode_cells(rows: &mut [serde_json::Map<String, Value>]) -> Result<CellText, String> {
    rows.iter_mut()
        .map(|row| {
            let mut tags = CellRow::new();
            for (column, cell) in row.iter_mut() {
                if let Some(tag) = decode_value(cell)? {
                    tags.insert(column.clone(), tag);
                }
            }
            Ok(tags)
        })
        .collect()
}

fn decode_value(v: &mut Value) -> Result<Option<CellTag>, String> {
    match v {
        Value::Object(o) => {
            let tag = match (o.get("decimal"), o.get("non_finite")) {
                (Some(Value::String(text)), None) if o.len() == 1 => {
                    let number = text
                        .parse::<f64>()
                        .ok()
                        .and_then(serde_json::Number::from_f64)
                        .ok_or_else(|| format!("cell tagged decimal is not a finite number: {text:?}"))?;
                    Some((CellTag { text: text.clone(), non_finite: None }, Value::Number(number)))
                }
                (None, Some(Value::String(kind))) if o.len() <= 2 => {
                    let non_finite = match kind.as_str() {
                        "nan" => f64::NAN,
                        "inf" => f64::INFINITY,
                        "-inf" => f64::NEG_INFINITY,
                        other => return Err(format!("cell tagged non_finite has unknown kind {other:?}")),
                    };
                    let text = o.get("text").and_then(Value::as_str).unwrap_or(kind);
                    Some((CellTag { text: text.to_string(), non_finite: Some(non_finite) }, Value::Null))
                }
                _ => None,
            };
            if let Some((tag, decoded)) = tag {
                *v = decoded;
                return Ok(Some(tag));
            }
            for child in o.values_mut() {
                decode_value(child)?;
            }
        }
        Value::Array(items) => {
            for child in items {
                decode_value(child)?;
            }
        }
        _ => {}
    }
    Ok(None)
}

/// The authored chart-level sort (`ChartSort` in
/// `compile/models/chart/authored/_annotations.py`) — orders a categorical
/// channel's domain. Shared by bar.rs and scatter.rs, which apply it with
/// their own family's aggregate.
#[derive(Deserialize)]
pub struct ChartSort {
    pub by: String,
    pub order: SortDir,
}

#[derive(Deserialize, Clone, Copy, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum SortDir {
    Asc,
    Desc,
}

/// The per-category aggregate an authored `sort` ranks by — production's
/// `EncodingSortField.op` (`utils.py::x_domain_order(..., op=)`): `Sum` for
/// a stacked bar sorted by its own measure (`bar_sort_op`), `Min` for every
/// other bar sort and every other dimension axis
/// (`emitters/_cartesian.py::dimension_sort_to_vl` pins `op: "min"`).
#[derive(Clone, Copy, PartialEq)]
pub enum SortAggregate {
    Sum,
    Min,
}

/// Reorder `base_order` (a categorical domain in first-occurrence row
/// order) by an authored `sort`'s per-category aggregate — the one ranking
/// law bar, line/area, and heatmap all share, parameterized on `agg`
/// (`x_domain_order`'s own `op=` in production; bar calls this with
/// `bar_sort_op`'s verdict, line/area/heatmap with `Min`).
///
/// `pin_sorted_domain` bakes the resulting order as an explicit VL
/// `scale.domain` — so an unranked category (no value for the sort field on
/// any row) always TRAILS, on both directions, matching `x_domain_order`'s
/// own `ranked + unranked` return: pinning an explicit domain means Vega
/// just draws the array as given, never consulting its own native
/// sort-comparator placement for what's absent. Ties are stable
/// (`sort_by`), so ties keep first-occurrence order.
///
/// Under `Sum`, a numeric string parses (`row_num` tolerates it, matching
/// production's own `coerce_numeric_cell` under `op == "sum"`); any other
/// non-numeric value refuses by name here — a deliberate divergence, since
/// `coerce_numeric_cell` instead reads it as no value (drops that row, the
/// same as an absent one) — the same refusal bar.rs's own `CatOrder::Field`
/// doc comment documents. Under `Min` the sort value is compared as itself
/// (`utils.py::_js_min`): numbers numerically, strings by code point, a value
/// of the other kind passed over; keys of both kinds make Python's sort raise,
/// which `x_domain_order` answers with row order.
pub fn dimension_sort_domain(
    rows: &[serde_json::Map<String, Value>],
    field: &str,
    base_order: Vec<String>,
    sort: Option<&ChartSort>,
    agg: SortAggregate,
    family: &str,
) -> Result<Vec<String>, String> {
    let Some(sort) = sort else {
        return Ok(base_order);
    };
    let mut mins: std::collections::HashMap<String, Value> = std::collections::HashMap::new();
    let mut sums: std::collections::HashMap<String, f64> = std::collections::HashMap::new();
    for row in rows {
        let Some(raw) = row.get(&sort.by).filter(|v| !v.is_null()) else { continue };
        let cat = row_str(row, field);
        match agg {
            SortAggregate::Min => match mins.get_mut(&cat) {
                // `_js_min`: a value Python cannot compare against the min is passed over.
                Some(min) => {
                    if raw_x_order(raw, min).is_ok_and(std::cmp::Ordering::is_lt) {
                        *min = raw.clone();
                    }
                }
                None => {
                    mins.insert(cat, raw.clone());
                }
            },
            SortAggregate::Sum => {
                let val = row_num(row, &sort.by).filter(|v| v.is_finite()).ok_or_else(|| {
                    format!(
                        "{family}: sort by {:?} is not supported — Vega's `sum` over \
                         non-numeric values is not ported",
                        sort.by
                    )
                })?;
                *sums.entry(cat).or_insert(0.0) += val;
            }
        }
    }
    let keys: std::collections::HashMap<String, Value> = match agg {
        SortAggregate::Min => mins,
        SortAggregate::Sum => sums
            .into_iter()
            .map(|(cat, total)| {
                let total = serde_json::Number::from_f64(total)
                    .ok_or_else(|| format!("{family}: the sum of {:?} over {cat:?} is not finite", sort.by))?;
                Ok((cat, Value::Number(total)))
            })
            .collect::<Result<_, String>>()?,
    };
    let (mut ranked, unranked): (Vec<String>, Vec<String>) =
        base_order.iter().cloned().partition(|c| keys.contains_key(c));
    // A column of both kinds makes Python's sort raise: `x_domain_order` keeps row order.
    let numeric = ranked.iter().all(|c| matches!(keys[c], Value::Number(_) | Value::Bool(_)));
    let textual = ranked.iter().all(|c| keys[c].is_string());
    if !numeric && !textual {
        return Ok(base_order);
    }
    ranked.sort_by(|a, b| {
        let ord = raw_x_order(&keys[a], &keys[b]).expect("one kind of key is ordered");
        if sort.order == SortDir::Desc { ord.reverse() } else { ord }
    });
    ranked.extend(unranked);
    Ok(ranked)
}

/// How many distinct `field` values `rows` carry under Python's equality, as
/// `len({row[field] ...})` counts the bands of the raw query result.
pub fn distinct_values(rows: &[serde_json::Map<String, Value>], field: &str) -> usize {
    rows.iter()
        .filter(|row| row.contains_key(field))
        .map(|row| py_scalar_key(&row[field]))
        .collect::<std::collections::HashSet<_>>()
        .len()
}

/// `count_horizontal_bar_categories` (`_cartesian.py`): the distinct `str()` of
/// the category field over the rows that carry it non-null.
pub fn count_horizontal_bar_categories(rows: &[serde_json::Map<String, Value>], field: &str) -> usize {
    rows.iter()
        .filter(|row| row.get(field).is_some_and(|v| !v.is_null()))
        .map(|row| row_str(row, field))
        .collect::<std::collections::HashSet<_>>()
        .len()
}

/// Numeric value from a row column; text reads as none, however it looks.
pub fn row_num(row: &serde_json::Map<String, Value>, field: &str) -> Option<f64> {
    match row.get(field)? {
        Value::Number(n) => n.as_f64(),
        _ => None,
    }
}

/// The rows whose `field` holds a number, each with that number. A null or non-numeric measure
/// draws no mark and no label (Vega skips the datum), so every pass over a measure reads rows here.
pub fn valued_rows<'a>(
    rows: impl IntoIterator<Item = &'a serde_json::Map<String, Value>>,
    field: &str,
) -> Vec<(&'a serde_json::Map<String, Value>, f64)> {
    rows.into_iter().filter_map(|row| row_num(row, field).map(|val| (row, val))).collect()
}

/// Python's comparison of two raw values: numbers (bools included) with numbers,
/// strings with strings, any other pair has no order (`TypeError`).
pub fn raw_x_order(a: &Value, b: &Value) -> Result<std::cmp::Ordering, String> {
    // A bool is an int to Python.
    let number = |v: &Value| match v {
        Value::Number(n) => n.as_f64(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        _ => None,
    };
    let order = match (a, b) {
        (Value::String(a), Value::String(b)) => Some(a.cmp(b)),
        _ => number(a).zip(number(b)).and_then(|(a, b)| a.partial_cmp(&b)),
    };
    order.ok_or_else(|| format!("x values {a} and {b} have no order"))
}

/// `rows` with each non-null `x_field` replaced by its position on the drawn x axis, so a
/// "last x" read follows the axis (the latest instant on a time axis, the last category on a
/// band one) instead of comparing text ("Before" outranks "After"). A null or absent x stays
/// as it is (the walk-backs skip it); an x the axis does not draw is an error, never a
/// dropped row.
pub fn x_ranked_rows(
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    domain: &[String],
) -> Result<Vec<serde_json::Map<String, Value>>, String> {
    let rank: std::collections::HashMap<&str, usize> = domain.iter().enumerate().map(|(at, x)| (x.as_str(), at)).collect();
    rows.iter()
        .map(|row| {
            let mut ranked = row.clone();
            if row.get(x_field).is_some_and(|x| !x.is_null()) {
                let x = row_str(row, x_field);
                let at = rank.get(x.as_str()).ok_or_else(|| format!("x value {x:?} is not on the drawn x axis"))?;
                ranked.insert(x_field.to_string(), Value::from(*at));
            }
            Ok(ranked)
        })
        .collect()
}

/// Production's `last_nonnull_value_per_series` (`_cartesian.py`): each
/// series' value at its own greatest x, found by walking the rows in order and
/// replacing the held pair whenever a row's raw x is `>=` the held one (Python's
/// comparison of the raw values, so the later of two equal xs wins and an
/// unsorted string x compares lexicographically; a time axis hands in its
/// instants, not its text). A row with a null series, x or
/// y is skipped; an x pair of different types has no order and is refused.
pub fn last_nonnull_value_per_series(
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    y_field: &str,
    series_field: &str,
) -> Result<std::collections::HashMap<String, f64>, String> {
    let mut latest: std::collections::HashMap<String, (&Value, f64)> = std::collections::HashMap::new();
    for row in rows {
        let (Some(x), Some(y)) = (row.get(x_field).filter(|v| !v.is_null()), row_num(row, y_field)) else {
            continue;
        };
        if row.get(series_field).is_none_or(Value::is_null) {
            continue;
        }
        let series = row_str(row, series_field);
        let replaces = match latest.get(&series) {
            None => true,
            Some((held, _)) => raw_x_order(x, held).map_err(|e| format!("{e} (series {series:?})"))?.is_ge(),
        };
        if replaces {
            latest.insert(series, (x, y));
        }
    }
    Ok(latest.into_iter().map(|(series, (_, y))| (series, y)).collect())
}

/// Production's `_wide_endpoint_positions`: a column's value at its greatest
/// raw x, the first of equal xs winning. A row with a null x or y is skipped.
pub fn last_nonnull_value(
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    y_field: &str,
) -> Result<Option<f64>, String> {
    let mut latest: Option<(&Value, f64)> = None;
    for row in rows {
        let (Some(x), Some(y)) = (row.get(x_field).filter(|v| !v.is_null()), row_num(row, y_field)) else {
            continue;
        };
        let replaces = match latest {
            None => true,
            Some((held, _)) => raw_x_order(x, held)?.is_gt(),
        };
        if replaces {
            latest = Some((x, y));
        }
    }
    Ok(latest.map(|(_, y)| y))
}

/// Port of production's `utils.py::is_year_shaped` — value-only, no
/// column-name signal: every non-null `field` value is an int (a JSON
/// `Value::Number` with no fractional part), a 4-digit string matching
/// `^\d{4}$`, or an integral float (a warehouse NUMERIC/Decimal year comes
/// back as a JSON number that may still carry a `.0`) — each in
/// `[1700, 2100]`. A JSON `Value::Bool` never matches (`Value::Number`
/// can't confuse a bool with a number the way Python's `isinstance(v,
/// int)` can, so there is no separate bool exclusion to port). Empty input
/// (every row's `field` null or absent) is not year-shaped.
///
/// `time_unit_detect.normalize_labeled_temporal` rewrites a year-shaped x
/// to ISO dates BEFORE `resolve_cartesian_x_type` ever classifies it, so a
/// bare year resolves `temporal` in production even though it has no
/// date-like STRUCTURAL shape at all — `x_values_are_temporal_candidates`
/// alone does not catch it. This predicate is the missing precondition a
/// categorical-x gate (line/area's authored-sort gate) needs alongside it.
pub fn is_year_shaped(types: &ColumnTypes, rows: &[serde_json::Map<String, Value>], field: &str) -> Result<bool, String> {
    use crate::column_kind::{field_kind_in, ColumnKind, Window};
    // Every non-null cell, as `normalize_labeled_temporal` reads `values`: it then rewrites every row.
    match field_kind_in(types, rows, field, Window::All).map_err(|d| d.to_string())? {
        ColumnKind::Text { ambiguous_year: true, .. } => Err(crate::column_kind::unicode_digit_year().to_string()),
        ColumnKind::Number { year } | ColumnKind::Text { year, .. } => Ok(year),
        ColumnKind::Empty | ColumnKind::Date | ColumnKind::Boolean | ColumnKind::Other => Ok(false),
    }
}

/// A color channel's legend/palette domain: the distinct values, sorted the
/// way Vega-Lite's nominal scale sorts an unpinned domain.
pub fn color_domain_of<'a>(
    rows: impl IntoIterator<Item = &'a serde_json::Map<String, Value>>,
    color_field: &str,
) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut domain: Vec<String> = rows
        .into_iter()
        .map(|row| row_str(row, color_field))
        .filter(|v| seen.insert(v.clone()))
        .collect();
    domain.sort();
    domain
}

#[cfg(test)]
mod is_year_shaped_tests {
    use super::*;
    use column_types::ColumnType;

    fn declared(kind: ColumnType) -> ColumnTypes {
        ColumnTypes::default().with("year", kind.clone()).with("code", kind)
    }

    fn year_shaped(kind: ColumnType, rows: &[serde_json::Map<String, Value>], field: &str) -> bool {
        is_year_shaped(&declared(kind), rows, field).expect("declared")
    }

    fn rows(field: &str, values: &[Value]) -> Vec<serde_json::Map<String, Value>> {
        values
            .iter()
            .map(|v| {
                let mut m = serde_json::Map::new();
                m.insert(field.to_string(), v.clone());
                m
            })
            .collect()
    }

    /// `[1700, 2100]` is inclusive at both ends (`utils.py::is_year_shaped`'s
    /// own `YEAR_MIN <= y <= YEAR_MAX`), string or number alike.
    #[test]
    fn the_1700_2100_range_is_inclusive_at_both_ends() {
        let r = rows("year", &[Value::String("1700".into()), Value::String("2100".into())]);
        assert!(year_shaped(ColumnType::String, &r, "year"));
        let r = rows("year", &[serde_json::json!(1700), serde_json::json!(2100)]);
        assert!(year_shaped(ColumnType::Integer, &r, "year"));
    }

    /// One value just outside either bound fails the WHOLE domain, string or
    /// number alike — the gate is a per-value AND, not a range over the
    /// domain's extremes.
    #[test]
    fn a_value_one_outside_either_bound_fails_the_whole_domain() {
        let r = rows("year", &[Value::String("1699".into()), Value::String("2000".into())]);
        assert!(!year_shaped(ColumnType::String, &r, "year"));
        let r = rows("year", &[Value::String("2000".into()), Value::String("2101".into())]);
        assert!(!year_shaped(ColumnType::String, &r, "year"));
        let r = rows("year", &[serde_json::json!(1699), serde_json::json!(2000)]);
        assert!(!year_shaped(ColumnType::Integer, &r, "year"));
    }

    /// A non-year 4-digit number outside `[1700, 2100]` — e.g. a product
    /// code or a quantity — never counts as year-shaped, string or number.
    #[test]
    fn a_non_year_4_digit_number_is_not_year_shaped() {
        let r = rows("code", &[Value::String("1234".into()), Value::String("5678".into())]);
        assert!(!year_shaped(ColumnType::String, &r, "code"));
        let r = rows("code", &[serde_json::json!(1234), serde_json::json!(5678)]);
        assert!(!year_shaped(ColumnType::Integer, &r, "code"));
    }

    /// A declared float is a measure, not a year; a declared decimal is one.
    #[test]
    fn a_declared_float_is_never_year_shaped() {
        let r = rows("year", &[serde_json::json!(2020.0), serde_json::json!(2021.0)]);
        assert!(!year_shaped(ColumnType::Float, &r, "year"));
        assert!(year_shaped(ColumnType::Decimal, &r, "year"));
    }
}

/// Display string from a row column. This is production's Vega-drawn
/// convention (axis tick labels, strip cell text — anywhere the raw datum
/// reaches Vega, which stringifies with JS `toString`), not Python's
/// `str()`: a bool renders JSON-native `"true"`/`"false"`. The one place
/// production instead builds a label host-side with Python `str()` — wide
/// series naming — uses `wide_dimension_str` below, not this function; do
/// not special-case `Value::Bool` here, or every one of `row_str`'s other
/// ~29 call sites across bar/heatmap/line/pie/scatter/support_table_strip
/// would silently disagree with what Vega actually draws.
pub fn row_str(row: &serde_json::Map<String, Value>, field: &str) -> String {
    cell_str(row.get(field))
}

/// `row_str` of one cell.
pub fn cell_str(cell: Option<&Value>) -> String {
    row_cell_str(cell).into_owned()
}

/// `row_str` without copying a string cell.
pub fn row_str_ref<'a>(row: &'a serde_json::Map<String, Value>, field: &str) -> Cow<'a, str> {
    row_cell_str(row.get(field))
}

fn row_cell_str(cell: Option<&Value>) -> Cow<'_, str> {
    match cell {
        Some(Value::String(s)) => Cow::Borrowed(s),
        Some(Value::Null) | None => Cow::Borrowed(""),
        Some(other) => Cow::Owned(other.to_string()),
    }
}

/// JS `Number(string)` (ECMA-262 StringToNumber): `None` where it is `NaN`.
/// Blank is 0; `0x`/`0o`/`0b` prefixes read an unsigned integer; `Infinity`
/// is the only named value; decimals take no digit separators and no
/// trailing garbage.
pub fn js_string_to_number(s: &str) -> Option<f64> {
    let t = s.trim_matches(|c: char| c.is_whitespace() || c == '\u{feff}');
    if t.is_empty() {
        return Some(0.0);
    }
    let radix = match t.get(..2) {
        Some("0x" | "0X") => Some(16),
        Some("0o" | "0O") => Some(8),
        Some("0b" | "0B") => Some(2),
        _ => None,
    };
    if let Some(radix) = radix {
        let digits = &t[2..];
        if digits.is_empty() || !digits.chars().all(|c| c.is_digit(radix)) {
            return None;
        }
        return Some(digits.chars().fold(0.0, |acc, c| acc * f64::from(radix) + f64::from(c.to_digit(radix).unwrap_or(0))));
    }
    let (sign, body) = match t.as_bytes()[0] {
        b'-' => (-1.0, &t[1..]),
        b'+' => (1.0, &t[1..]),
        _ => (1.0, t),
    };
    if body == "Infinity" {
        return Some(sign * f64::INFINITY);
    }
    let (mantissa, exponent) = body.split_once(['e', 'E']).map_or((body, None), |(m, e)| (m, Some(e)));
    let (int, frac) = mantissa.split_once('.').map_or((mantissa, None), |(i, f)| (i, Some(f)));
    let all_digits = |part: &str| part.bytes().all(|b| b.is_ascii_digit());
    let mantissa_ok = all_digits(int) && frac.is_none_or(all_digits) && !(int.is_empty() && frac.is_none_or(str::is_empty));
    let exponent_ok = exponent.is_none_or(|e| {
        let digits = e.strip_prefix(['+', '-']).unwrap_or(e);
        !digits.is_empty() && all_digits(digits)
    });
    if !mantissa_ok || !exponent_ok {
        return None;
    }
    body.parse::<f64>().ok().map(|v| sign * v)
}

/// JS `Number(v)` of a JSON scalar; `None` where it is `NaN`.
pub fn js_to_number(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::String(s) => js_string_to_number(s),
        _ => None,
    }
}

/// JS `a < b` on two non-null JSON scalars: two strings compare by UTF-16
/// code unit, anything else as numbers (a numeric string coerces, a
/// non-numeric one is `NaN` and compares false both ways).
pub fn js_less(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::String(x), Value::String(y)) => x.encode_utf16().lt(y.encode_utf16()),
        _ => matches!((js_to_number(a), js_to_number(b)), (Some(x), Some(y)) if x < y),
    }
}

/// Vega's `ascending` comparator on two JSON scalars with JS `<`/`>`
/// coercion: numbers compare numerically (a numeric string coerces), strings
/// lexicographically, and a number against a non-numeric string is "equal".
fn js_ascending(a: &Value, b: &Value) -> i32 {
    match (a, b) {
        // `ascending` ranks a null before any other value.
        (Value::Null, Value::Null) => 0,
        (Value::Null, _) => -1,
        (_, Value::Null) => 1,
        _ if js_less(a, b) => -1,
        _ if js_less(b, a) => 1,
        _ => 0,
    }
}

/// An ordinal scale's `domain` (first-appearance order) sorted the way Vega
/// compiles `sort: {op: "min", field: sort.by, order}`: each category's key is
/// the JS `min` of the sort field over the `rows` it names (nulls skipped, the
/// first value kept unless a later one is less), and a category with no value
/// ranks below every other, first ascending and last descending.
pub fn min_ranked_domain(
    domain: Vec<String>,
    rows: &[&serde_json::Map<String, Value>],
    category_of: impl Fn(&serde_json::Map<String, Value>) -> String,
    sort: &ChartSort,
) -> Result<Vec<String>, String> {
    let keyed: Vec<(String, Value)> = domain
        .into_iter()
        .map(|category| {
            let key = rows
                .iter()
                .filter(|row| category_of(row) == category)
                .filter_map(|row| row.get(&sort.by).filter(|v| !v.is_null()))
                .fold(None::<&Value>, |min, v| min.filter(|m| !js_less(v, m)).or(Some(v)));
            (category, key.cloned().unwrap_or(Value::Null))
        })
        .collect();
    js_rank(&keyed, sort.order)
}

/// `items` ordered by their `key` the way Vega sorts a domain by an
/// aggregate: `Array.prototype.sort` with `ascending` (a null key first) or
/// its reverse. A key column of one type is a total order, so any stable
/// sort agrees; a mix of numbers and strings makes the comparator
/// non-transitive and the order is whatever V8's insertion sort produces.
pub fn js_rank<T: Clone>(items: &[(T, Value)], order: SortDir) -> Result<Vec<T>, String> {
    let sign = if order == SortDir::Desc { -1 } else { 1 };
    let cmp = |a: &(T, Value), b: &(T, Value)| sign * js_ascending(&a.1, &b.1);
    let numeric = items.iter().all(|(_, k)| matches!(k, Value::Null | Value::Number(_) | Value::Bool(_)));
    let textual = items.iter().all(|(_, k)| k.is_null() || k.is_string());
    let mut ranked: Vec<usize> = (0..items.len()).collect();
    if numeric || textual {
        ranked.sort_by(|&a, &b| cmp(&items[a], &items[b]).cmp(&0));
    } else {
        d3rs::js_sort::js_sort(&mut ranked, |a, b| f64::from(cmp(&items[a], &items[b])))?;
    }
    Ok(ranked.into_iter().map(|i| items[i].0.clone()).collect())
}

/// The order Vega's ordinal-scale `sort: true` produces for a domain of JSON
/// scalars: JavaScript `Array.prototype.sort` with `ascending`. A column that
/// mixes numbers and strings makes the comparator non-transitive, so the result
/// is whatever V8's insertion sort gives ([`js_rank`]).
pub fn vega_ascending_sort(values: Vec<Value>) -> Result<Vec<Value>, String> {
    let items: Vec<(Value, Value)> = values.into_iter().map(|v| (v.clone(), v)).collect();
    js_rank(&items, SortDir::Asc)
}

#[cfg(test)]
mod vega_ascending_tests {
    use super::*;
    use serde_json::json;

    fn sorted(v: Value) -> Vec<String> {
        let values = v.as_array().unwrap().clone();
        vega_ascending_sort(values).unwrap().iter().map(|x| x.to_string().trim_matches('"').to_string()).collect()
    }

    #[test]
    fn a_mixed_domain_sorts_as_v8_runs_it() {
        assert_eq!(sorted(json!([3, "b", 1, "a"])), ["3", "b", "1", "a"]);
        assert_eq!(sorted(json!([10, 2, "a", "B"])), ["2", "10", "B", "a"]);
        assert_eq!(sorted(json!(["d", "c", 2, 1, "a"])), ["c", "d", "1", "2", "a"]);
    }

    #[test]
    fn a_pure_domain_sorts_ascending() {
        assert_eq!(sorted(json!(["b", "a", "C"])), ["C", "a", "b"]);
    }
}

#[cfg(test)]
mod min_ranked_domain_tests {
    use super::*;
    use serde_json::json;

    fn rows(cells: &[(&str, Value)]) -> Vec<serde_json::Map<String, Value>> {
        cells
            .iter()
            .map(|(cat, by)| json!({"cat": cat, "by": by}).as_object().unwrap().clone())
            .collect()
    }

    fn ranked(cells: &[(&str, Value)], order: SortDir) -> Vec<String> {
        let rows = rows(cells);
        let refs: Vec<_> = rows.iter().collect();
        let domain = ["a", "b", "c", "d"].map(String::from).to_vec();
        let sort = ChartSort { by: "by".into(), order };
        min_ranked_domain(domain, &refs, |row| row_str(row, "cat"), &sort).unwrap()
    }

    /// JS `<` compares two strings by code unit, so a text sort key ranks
    /// lexicographically and its `min` is the first in that order.
    #[test]
    fn a_text_sort_key_ranks_by_its_lexicographic_min() {
        let cells = [("a", json!("z")), ("a", json!("m")), ("b", json!("b")), ("c", json!("a")), ("d", json!("B"))];
        assert_eq!(ranked(&cells, SortDir::Asc), ["d", "c", "b", "a"]);
        assert_eq!(ranked(&cells, SortDir::Desc), ["a", "b", "c", "d"]);
    }

    /// A category with no sort value ranks below every other: first ascending,
    /// last descending, ties keeping first-appearance order.
    #[test]
    fn a_category_with_no_value_leads_ascending_and_trails_descending() {
        let cells = [("a", json!(2)), ("b", Value::Null), ("c", json!(1)), ("c", json!(1)), ("d", json!(2))];
        assert_eq!(ranked(&cells, SortDir::Asc), ["b", "c", "a", "d"]);
        assert_eq!(ranked(&cells, SortDir::Desc), ["a", "d", "c", "b"]);
    }

    /// Numbers and bools coerce to numbers, a number against a non-numeric
    /// string compares false both ways, so the first value stays the `min`.
    #[test]
    fn a_value_js_cannot_order_against_the_min_leaves_it_in_place() {
        assert!(js_less(&json!(1), &json!("10")));
        assert!(js_less(&json!(false), &json!(true)));
        assert!(!js_less(&json!(1), &json!("x")) && !js_less(&json!("x"), &json!(1)));
        let cells = [("a", json!(5)), ("a", json!("x")), ("b", json!(7))];
        assert_eq!(ranked(&cells, SortDir::Asc), ["c", "d", "a", "b"]);
    }
}

#[cfg(test)]
mod row_str_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rows_rank_by_their_position_on_the_drawn_axis_and_an_off_axis_x_raises() {
        let rows = |xs: &[Value]| -> Vec<serde_json::Map<String, Value>> {
            xs.iter().enumerate().map(|(i, x)| json!({"x": x, "s": "a", "y": 10 * (i + 1)}).as_object().unwrap().clone()).collect()
        };
        let domain = ["Before".to_string(), "After".to_string()];
        let ranked = x_ranked_rows(&rows(&[json!("After"), json!("Before"), Value::Null]), "x", &domain).unwrap();
        assert_eq!(ranked.iter().map(|r| r["x"].clone()).collect::<Vec<_>>(), vec![json!(1), json!(0), Value::Null]);
        // "Before" > "After" as strings, but "After" is last on the axis.
        let last = last_nonnull_value_per_series(&ranked, "x", "y", "s").unwrap();
        assert_eq!(last["a"], 10.0, "the row on \"After\"");
        let err = x_ranked_rows(&rows(&[json!("During")]), "x", &domain).unwrap_err();
        assert!(err.contains("\"During\"") && err.contains("not on the drawn x axis"), "{err}");
    }

    #[test]
    fn row_str_renders_a_bool_json_native_not_pythons_str() {
        let row = json!({"active": true, "inactive": false}).as_object().unwrap().clone();
        assert_eq!(row_str(&row, "active"), "true");
        assert_eq!(row_str(&row, "inactive"), "false");
    }
}

/// A cell's Python equality class: `1`, `1.0` and `True` are one key, an
/// integral double equals the int it names, and `None` is its own key. A list
/// or object cell (unhashable in Python) keys by its JSON text.
#[derive(Clone, PartialEq, Eq, Hash)]
pub enum PyKey {
    None,
    Int(i128),
    Float(u64),
    Str(String),
}

pub fn py_scalar_key(cell: &Value) -> PyKey {
    match cell {
        Value::Null => PyKey::None,
        Value::Bool(b) => PyKey::Int(i128::from(*b)),
        Value::String(s) => PyKey::Str(s.clone()),
        Value::Number(n) if n.is_f64() => {
            let x = n.as_f64().expect("an f64 number");
            // Every JSON int fits in i128, so an integral double past it
            // equals no int.
            if x.fract() == 0.0 && x.abs() < 2f64.powi(127) {
                PyKey::Int(x as i128)
            } else {
                PyKey::Float(x.to_bits())
            }
        }
        Value::Number(n) => PyKey::Int(n.as_i64().map_or_else(|| i128::from(n.as_u64().expect("a u64")), i128::from)),
        Value::Array(_) | Value::Object(_) => PyKey::Str(cell.to_string()),
    }
}

#[cfg(test)]
mod distinct_values_tests {
    use super::*;
    use serde_json::json;

    fn rows(values: Vec<Value>) -> Vec<serde_json::Map<String, Value>> {
        values.into_iter().map(|v| json!({"x": v}).as_object().unwrap().clone()).collect()
    }

    /// `1`, `1.0` and `True` are one Python key; `"1"`, `None` and `""` are three more.
    #[test]
    fn distinct_values_follow_pythons_equality() {
        let cells = rows(vec![json!(1), json!(1.0), json!(true), json!("1"), json!(null), json!("")]);
        assert_eq!(distinct_values(&cells, "x"), 4);
    }
}

#[cfg(test)]
mod cell_tests {
    use super::*;

    #[test]
    fn the_seam_decodes_decimal_and_non_finite_cells_and_keeps_their_text() {
        let mut rows = vec![serde_json::json!({
            "d": {"decimal": "2.50"},
            "n": {"non_finite": "nan", "text": "NaN"},
            "f": 1.5,
            "spark": [1, {"non_finite": "inf", "text": "inf"}, {"decimal": "3"}],
        })
        .as_object()
        .unwrap()
        .clone()];
        let text = decode_cells(&mut rows).unwrap();
        assert_eq!(
            Value::Object(rows[0].clone()),
            serde_json::json!({"d": 2.5, "n": null, "f": 1.5, "spark": [1, null, 3.0]})
        );
        assert_eq!(text[0]["d"], CellTag { text: "2.50".into(), non_finite: None });
        assert_eq!(text[0].len(), 2);
        assert!(text[0]["n"].non_finite.unwrap().is_nan() && text[0]["n"].text == "NaN");
        assert_eq!(rows[0]["d"], serde_json::json!(2.5));
        let mut bad = vec![serde_json::json!({"d": {"decimal": "not a number"}}).as_object().unwrap().clone()];
        assert!(decode_cells(&mut bad).is_err());
        let mut unknown = vec![serde_json::json!({"n": {"non_finite": "huge"}}).as_object().unwrap().clone()];
        assert!(decode_cells(&mut unknown).is_err());
    }
}
