//! The band order law: the one order bars, heatmap axes, facets, legends and series read.
//! First appearance, unless an authored sort ranks the categories by an aggregate of a column.

use std::collections::{HashMap, HashSet};

use d3rs::py_float::py_float_sum;
use diagnostic::{diag, Diagnostic};
use serde_json::{Map, Value};

use crate::model::{BandDomain, Key};
use crate::rows::raw_x_order;

/// How a category's rows fold to the one value it ranks by.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Aggregate {
    Sum,
    Min,
}

impl Aggregate {
    /// `Sum` is a stacked total, which exactly one sort means: a stacked bar sorted by its own
    /// measure. A grouped bar and a wide chart's folded columns rank by the column's smallest value.
    pub fn of_bar(sort_by: &str, measure: Option<&str>, stacked: bool) -> Self {
        if stacked && Some(sort_by) == measure { Self::Sum } else { Self::Min }
    }
}

/// An authored `sort`: the column, its direction and how a category's rows fold.
#[derive(Clone, Copy, Debug)]
pub struct Sort<'a> {
    pub by: &'a str,
    pub descending: bool,
    pub aggregate: Aggregate,
}

/// The distinct non-null `categories` (one decoded key per row of `rows`) in their final order;
/// `text` prints each key.
///
/// Without a sort that is first appearance. With one, the categories holding a value of `sort.by`
/// are ranked by their aggregate (ties keep first appearance) and the others follow in first
/// appearance, whichever the direction. A column that mixes numbers and text ranks nothing and
/// keeps first appearance.
pub fn band_domain(
    rows: &[Map<String, Value>],
    categories: &[Key],
    sort: Option<Sort>,
    text: impl Fn(&Key) -> String,
) -> Result<BandDomain, Diagnostic> {
    if let Some(sort) = sort {
        if let Some(row) = rows.iter().find(|row| !row.contains_key(sort.by)) {
            let mut available: Vec<&str> = row.keys().map(String::as_str).collect();
            available.sort();
            return Err(diag::err_sort_field_not_found(sort.by, available.join(", ")));
        }
    }
    let mut first_seen: Vec<Key> = Vec::new();
    let mut seen: HashSet<Key> = HashSet::new();
    for key in categories.iter().filter(|key| **key != Key::Null) {
        if seen.insert(key.clone()) {
            first_seen.push(key.clone());
        }
    }
    let keys = match sort {
        Some(sort) => ranked(rows, categories, sort, first_seen)?,
        None => first_seen,
    };
    let text = keys.iter().map(text).collect();
    Ok(BandDomain::new(keys, text).expect("distinct keys, one text each"))
}

fn ranked(
    rows: &[Map<String, Value>],
    categories: &[Key],
    sort: Sort,
    first_seen: Vec<Key>,
) -> Result<Vec<Key>, Diagnostic> {
    let mut values: HashMap<Key, Vec<&Value>> = HashMap::new();
    for (row, category) in rows.iter().zip(categories).filter(|(_, key)| **key != Key::Null) {
        let Some(value) = row.get(sort.by).filter(|cell| !cell.is_null()) else { continue };
        values.entry(category.clone()).or_default().push(value);
    }
    if sort.aggregate == Aggregate::Sum {
        // The sorted column is the measure, which is a number column by the time a chart
        // reaches this law. A total that overflows stays a float and ranks as Python's `<`
        // ranks it: infinity last, NaN neither before nor after anything.
        let totals: HashMap<&Key, f64> = values
            .iter()
            .filter_map(|(category, cells)| {
                let numbers: Vec<f64> = cells.iter().filter_map(|cell| cell.as_f64()).collect();
                (!numbers.is_empty()).then(|| (category, py_float_sum(&numbers)))
            })
            .collect();
        let (mut ranked, unranked): (Vec<Key>, Vec<Key>) = first_seen.into_iter().partition(|k| totals.contains_key(k));
        ranked.sort_by(|a, b| {
            let order = totals[a].partial_cmp(&totals[b]).unwrap_or(std::cmp::Ordering::Equal);
            if sort.descending { order.reverse() } else { order }
        });
        ranked.extend(unranked);
        return Ok(ranked);
    }
    let mut folded: HashMap<Key, Value> = HashMap::new();
    for (category, cells) in &values {
        // A value that cannot be compared against the running minimum is passed over.
        let min = cells.iter().skip(1).fold(cells[0], |min, cell| {
            if raw_x_order(cell, min).is_ok_and(std::cmp::Ordering::is_lt) { cell } else { min }
        });
        folded.insert(category.clone(), min.clone());
    }
    let (mut ranked, unranked): (Vec<Key>, Vec<Key>) = first_seen.iter().cloned().partition(|k| folded.contains_key(k));
    let numeric = ranked.iter().all(|k| matches!(folded[k], Value::Number(_) | Value::Bool(_)));
    let textual = ranked.iter().all(|k| folded[k].is_string());
    if !numeric && !textual {
        return Ok(first_seen);
    }
    ranked.sort_by(|a, b| {
        let order = raw_x_order(&folded[a], &folded[b]).expect("one kind of value is ordered");
        if sort.descending { order.reverse() } else { order }
    });
    ranked.extend(unranked);
    Ok(ranked)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::decode::decode;
    use column_types::{ColumnType, ColumnTypes, Dataset};
    use serde_json::json;
    use std::collections::HashMap;

    fn rows(v: Value) -> Vec<Map<String, Value>> {
        v.as_array().unwrap().iter().map(|r| r.as_object().unwrap().clone()).collect()
    }

    fn keys_of(rows: &[Map<String, Value>], declared: &ColumnType) -> Vec<Key> {
        let schema = ColumnTypes(HashMap::from([("c".to_string(), declared.clone())]));
        let dataset = Dataset { rows: rows.to_vec().into(), schema };
        decode(&dataset).unwrap().keys("c").unwrap().to_vec()
    }

    fn domain(rows: &[Map<String, Value>], declared: &ColumnType, sort: Option<Sort>) -> Result<BandDomain, Diagnostic> {
        band_domain(rows, &keys_of(rows, declared), sort, |k| format!("{k:?}"))
    }

    fn order(rows: &[Map<String, Value>], sort: Option<Sort>) -> Result<Vec<String>, Diagnostic> {
        domain(rows, &ColumnType::String, sort).map(|d| {
            d.keys().iter().map(|k| match k { Key::Text(s) => s.clone(), other => format!("{other:?}") }).collect()
        })
    }

    const MIN_DESC: Sort = Sort { by: "v", descending: true, aggregate: Aggregate::Min };

    #[test]
    fn without_a_sort_the_order_is_first_appearance_and_a_null_category_has_no_band() {
        let r = rows(json!([{"c": "b"}, {"c": null}, {"c": "a"}, {"c": "b"}, {}]));
        assert_eq!(order(&r, None).unwrap(), ["b", "a"]);
    }

    #[test]
    fn a_sort_ranks_by_the_aggregate_ties_keep_appearance_and_unranked_trail_both_ways() {
        let r = rows(json!([
            {"c": "a", "v": 2}, {"c": "x", "v": null}, {"c": "b", "v": 5},
            {"c": "b", "v": 1}, {"c": "d", "v": 2}
        ]));
        // min: b=1, a=2, d=2 (tie keeps a before d); x has no value.
        assert_eq!(order(&r, Some(Sort { descending: false, ..MIN_DESC })).unwrap(), ["b", "a", "d", "x"]);
        assert_eq!(order(&r, Some(MIN_DESC)).unwrap(), ["a", "d", "b", "x"]);
        // sum: b=6, a=2, d=2.
        let sum = Sort { aggregate: Aggregate::Sum, ..MIN_DESC };
        assert_eq!(order(&r, Some(sum)).unwrap(), ["b", "a", "d", "x"]);
    }

    #[test]
    fn a_decimal_column_holds_two_and_two_point_zero_in_two_bands() {
        let r = rows(json!([{"c": 2}, {"c": 2.0}, {"c": 3}]));
        assert_eq!(domain(&r, &ColumnType::Decimal, None).unwrap().keys().len(), 3);
    }

    #[test]
    fn timestamps_naming_one_instant_in_two_texts_are_two_bands() {
        let r = rows(json!([{"c": "2024-03-01T02:00:00Z"}, {"c": "2024-02-29T21:00:00-05:00"}]));
        assert_eq!(domain(&r, &ColumnType::DatetimeTz, None).unwrap().keys().len(), 2);
    }

    #[test]
    fn a_column_of_numbers_and_text_ranks_nothing() {
        let r = rows(json!([{"c": "a", "v": 2}, {"c": "b", "v": "z"}]));
        assert_eq!(order(&r, Some(MIN_DESC)).unwrap(), ["a", "b"]);
    }

    #[test]
    fn a_stacked_total_ranks_by_the_sum_where_the_minimum_ranks_otherwise() {
        // Production render of this data (bar, color, style.stack: zero, sort by the measure
        // `v` desc) draws a, b, c; without the stack, b, a, c.
        let r = rows(json!([
            {"c": "a", "v": 5}, {"c": "a", "v": 7}, {"c": "b", "v": 9}, {"c": "c", "v": 1}
        ]));
        let sum = Sort { aggregate: Aggregate::Sum, ..MIN_DESC };
        assert_eq!(order(&r, Some(sum)).unwrap(), ["a", "b", "c"]);
        assert_eq!(order(&r, Some(MIN_DESC)).unwrap(), ["b", "a", "c"]);
        // A total that overflows to infinity ranks as Python ranks inf: above every finite total.
        let huge = rows(json!([{"c": "a", "v": 1e308}, {"c": "a", "v": 1e308}, {"c": "b", "v": 5}]));
        assert_eq!(order(&huge, Some(sum)).unwrap(), ["a", "b"]);
    }

    #[test]
    fn a_sort_column_missing_from_any_row_refuses_but_a_null_is_present() {
        let ok = rows(json!([{"c": "a", "v": null}]));
        assert!(order(&ok, Some(MIN_DESC)).is_ok());
        let bad = rows(json!([{"c": "a", "v": 1}, {"c": "b", "w": 1}]));
        let err = order(&bad, Some(MIN_DESC)).unwrap_err();
        assert_eq!(err.registered().unwrap().id, "ERR-SORT-FIELD-NOT-FOUND");
        assert_eq!(err.message().unwrap(), "sort.by column 'v' not found in the query result. Available columns: c, w.");
    }

    #[test]
    fn the_aggregate_is_a_sum_only_for_a_stacked_bar_sorted_by_its_own_measure() {
        assert_eq!(Aggregate::of_bar("v", Some("v"), true), Aggregate::Sum);
        assert_eq!(Aggregate::of_bar("v", Some("v"), false), Aggregate::Min);
        assert_eq!(Aggregate::of_bar("v", None, true), Aggregate::Min);
    }
}
