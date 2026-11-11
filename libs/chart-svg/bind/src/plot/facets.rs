//! The facet partition: panels are the product of the row-axis and column-axis values in
//! first-seen order, empty combinations dropped. A key is a value's wire identity, so `2` and
//! `2.0` are two panels, as production's canonical JSON keeps them. Panels hold row indices, so the rows stay
//! where the query put them.

use std::collections::HashMap;

use diagnostic::{diag, Diagnostic};
use serde_json::{Map, Value};

use crate::bound::{Grid, Key, PanelId, Panels, Sharing};

/// A facet field and its decoded key per row.
#[derive(Clone, Copy)]
pub struct FacetAxis<'a> {
    pub field: &'a str,
    pub keys: &'a [Key],
}

/// `chart.multiples`: the axes (at least one) and how the panels share their scales.
#[derive(Clone, Copy)]
pub struct Facets<'a> {
    pub rows: Option<FacetAxis<'a>>,
    pub columns: Option<FacetAxis<'a>>,
    pub x: Sharing,
    pub y: Sharing,
    pub mirror: bool,
}

/// One drawn panel: its key (the row-axis value, then the column-axis value, for the axes set)
/// and the indices of its rows in query order.
#[derive(Debug, PartialEq)]
pub struct Panel {
    pub key: Vec<Key>,
    pub rows: Vec<usize>,
}

#[derive(Debug)]
pub struct Partition {
    pub panels: Vec<Panel>,
    pub layout: Panels,
}

/// Inside a panel a facet field is constant, so a color channel bound to it has nothing left to
/// distinguish: it is dropped.
pub fn panel_color<'a>(color: Option<&'a str>, facets: &Facets) -> Option<&'a str> {
    color.filter(|field| ![facets.rows, facets.columns].iter().flatten().any(|axis| axis.field == *field))
}

/// Splits `rows` into panels. No facets, or no rows, is one panel holding every row.
pub fn partition(rows: &[Map<String, Value>], facets: Option<&Facets>) -> Result<Partition, Diagnostic> {
    let Some(facets) = facets.filter(|_| !rows.is_empty()) else {
        let all = Panel { key: Vec::new(), rows: (0..rows.len()).collect() };
        return Ok(Partition { panels: vec![all], layout: Panels::Single });
    };
    let axes: Vec<FacetAxis> = [facets.rows, facets.columns].into_iter().flatten().collect();
    let mut values: Vec<Vec<Key>> = vec![Vec::new(); axes.len()];
    let mut index: Vec<HashMap<Key, usize>> = vec![HashMap::new(); axes.len()];
    let mut groups: HashMap<Vec<usize>, Vec<usize>> = HashMap::new();
    for (at, row) in rows.iter().enumerate() {
        let missing: Vec<&str> = axes.iter().map(|a| a.field).filter(|f| !row.contains_key(*f)).collect();
        if !missing.is_empty() {
            let mut available: Vec<&str> = row.keys().map(String::as_str).collect();
            available.sort();
            return Err(diag::err_multiples_field_not_found(missing.join(", "), available.join(", ")));
        }
        let mut combo = Vec::with_capacity(axes.len());
        for (i, axis) in axes.iter().enumerate() {
            let key = axis.keys.get(at).expect("one decoded key per row of the facet field").clone();
            let slot = *index[i].entry(key.clone()).or_insert_with(|| {
                values[i].push(key);
                values[i].len() - 1
            });
            combo.push(slot);
        }
        groups.entry(combo).or_default().push(at);
    }
    let row_axis = facets.rows.map(|_| 0);
    let column_axis = facets.columns.map(|_| usize::from(facets.rows.is_some()));
    let row_count = row_axis.map_or(1, |at| values[at].len());
    let column_count = column_axis.map_or(1, |at| values[at].len());
    let mut panels = Vec::new();
    let mut cells = Vec::with_capacity(row_count * column_count);
    for r in 0..row_count {
        for c in 0..column_count {
            let combo: Vec<usize> = [row_axis.map(|_| r), column_axis.map(|_| c)].into_iter().flatten().collect();
            match groups.remove(&combo) {
                Some(rows) => {
                    let key = combo.iter().enumerate().map(|(i, &slot)| values[i][slot].clone()).collect();
                    cells.push(Some(PanelId(panels.len())));
                    panels.push(Panel { key, rows });
                }
                None => cells.push(None),
            }
        }
    }
    let tracks = |axis: Option<usize>| axis.map(|at| values[at].clone());
    let grid = Grid::new(tracks(row_axis), tracks(column_axis), cells, facets.x, facets.y, facets.mirror)
        .expect("an axis is set, distinct tracks, one cell each, panels numbered in cell order");
    Ok(Partition { panels, layout: Panels::Grid(grid) })
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

    fn axis<'a>(field: &'a str, keys: &'a [Key]) -> Option<FacetAxis<'a>> {
        Some(FacetAxis { field, keys })
    }

    fn keys(rows: &[Map<String, Value>], field: &str, declared: ColumnType) -> Vec<Key> {
        let schema = ColumnTypes(HashMap::from([(field.to_string(), declared)]));
        decode(&Dataset { rows: rows.to_vec().into(), schema }).unwrap().keys(field).unwrap().to_vec()
    }

    fn facets<'a>(rows: Option<FacetAxis<'a>>, columns: Option<FacetAxis<'a>>) -> Facets<'a> {
        Facets { rows, columns, x: Sharing::Shared, y: Sharing::Shared, mirror: false }
    }

    fn text(rows: &[Map<String, Value>], field: &str) -> Vec<Key> {
        keys(rows, field, ColumnType::String)
    }

    #[test]
    fn panels_are_the_row_major_product_in_first_seen_order_and_empty_cells_are_dropped() {
        let r = rows(json!([
            {"r": "b", "c": "y"}, {"r": "a", "c": "x"}, {"r": "b", "c": "x"}, {"r": "b", "c": "y"}
        ]));
        let (rk, ck) = (text(&r, "r"), text(&r, "c"));
        let f = facets(axis("r", &rk), axis("c", &ck));
        let p = partition(&r, Some(&f)).unwrap();
        let got: Vec<_> = p.panels.iter().map(|p| (p.key.clone(), p.rows.clone())).collect();
        let t = |s: &str| Key::Text(s.into());
        assert_eq!(got, [(vec![t("b"), t("y")], vec![0, 3]), (vec![t("b"), t("x")], vec![2]), (vec![t("a"), t("x")], vec![1])]);
        // rows [b, a] x columns [y, x]: (b,y) (b,x) (a,y) (a,x); (a,y) is empty.
        let Panels::Grid(grid) = p.layout else { panic!("a grid") };
        assert_eq!(grid.cells(), &[Some(PanelId(0)), Some(PanelId(1)), None, Some(PanelId(2))]);
    }

    #[test]
    fn a_decimal_column_holding_two_and_two_point_zero_is_two_panels() {
        let r = rows(json!([{"r": 2}, {"r": 2.0}]));
        let k = keys(&r, "r", ColumnType::Decimal);
        let p = partition(&r, Some(&facets(axis("r", &k), None))).unwrap();
        assert_eq!(p.panels.len(), 2);
    }

    #[test]
    fn timestamps_naming_one_instant_in_two_texts_are_two_panels() {
        let r = rows(json!([{"r": "2024-03-01T02:00:00Z"}, {"r": "2024-02-29T21:00:00-05:00"}]));
        let k = keys(&r, "r", ColumnType::DatetimeTz);
        assert_eq!(partition(&r, Some(&facets(axis("r", &k), None))).unwrap().panels.len(), 2);
    }

    #[test]
    fn no_facets_or_no_rows_is_one_panel_and_a_null_value_is_a_panel() {
        let r = rows(json!([{"r": null}, {"r": "a"}]));
        let k = text(&r, "r");
        assert_eq!(partition(&r, None).unwrap().panels.len(), 1);
        assert_eq!(partition(&[], Some(&facets(axis("zz", &[]), None))).unwrap().panels.len(), 1);
        assert_eq!(partition(&r, Some(&facets(axis("r", &k), None))).unwrap().panels[0].key, [Key::Null]);
    }

    #[test]
    fn a_row_missing_a_facet_field_refuses_even_after_complete_rows() {
        let r = rows(json!([{"r": "a", "c": 1}, {"r": "b"}]));
        let (rk, ck) = (text(&r, "r"), keys(&r, "c", ColumnType::Integer));
        let err = partition(&r, Some(&facets(axis("r", &rk), axis("c", &ck)))).unwrap_err();
        assert_eq!(err.registered().unwrap().id, "ERR-MULTIPLES-FIELD-NOT-FOUND");
        assert_eq!(err.message().unwrap(), "multiples field(s) c not found in the query result. Available columns: r.");
    }

    #[test]
    fn a_color_that_is_a_facet_field_is_dropped_per_panel() {
        let f = facets(axis("r", &[]), axis("c", &[]));
        assert_eq!(panel_color(Some("r"), &f), None);
        assert_eq!(panel_color(Some("k"), &f), Some("k"));
    }
}
