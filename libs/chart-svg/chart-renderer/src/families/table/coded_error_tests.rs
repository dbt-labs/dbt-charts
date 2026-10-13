//! Production's coded table and pivot data errors (`ChartDataError.from_code`
//! in the table's pivot, transpose, swatch and format code): each test pins the code
//! and its params exactly, under the condition that raises it.

use super::tests::{base_table_theme, resolved_title_font, resolved_title_style};
use super::*;
use serde_json::json;

fn test_fonts() -> FontBook {
    let manifest = env!("CARGO_MANIFEST_DIR");
    let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
    FontBook::load_dir(&dir).expect("test fonts load")
}

fn table(columns: Value, rows: Vec<Value>) -> Payload {
    crate::test_payload(json!({
        "resolved": {
            "id": "t",
            "title": "T",
            "title_style": resolved_title_style(),
            "columns": columns,
            "style": {"table": base_table_theme(), "title_font": resolved_title_font()},
            "pivot_columns": null,
            "conditional_formatting": null,
        },
        "rows": rows,
        "width": 600.0,
        "height": 400.0,
        "background": "#ffffff",
        "fonts_dir": null,
        "padding": null,
    }))
}

fn pivot(rows: Vec<Value>, dims: Value, columns: Value, values: Value) -> Payload {
    let mut payload = table(json!({}), rows);
    payload.resolved["rows"] = dims;
    payload.resolved["pivot_columns"] = columns;
    payload.resolved["values"] = values;
    payload
}

fn raised(payload: &Payload) -> Diagnostic {
    render(payload, &test_fonts()).expect_err("production raises a coded error here")
}

fn sales() -> Vec<Value> {
    vec![
        json!({"region": "West", "quarter": "Q1", "revenue": 10.0}),
        json!({"region": "East", "quarter": "Q1", "revenue": 7.0}),
    ]
}

fn observed() -> Value {
    json!(["quarter", "region", "revenue"])
}

/// `_render_swatch_cell`.
#[test]
fn a_swatch_cell_that_is_not_a_color() {
    let columns = json!({"n": {"label": "N"}, "c": {"swatch": true}});
    let rows = vec![json!({"n": "a", "c": "#ff0000"}), json!({"n": "b", "c": "not a color"})];
    assert_eq!(
        raised(&table(columns, rows)),
        Diagnostic::new(
            &codes::ERR_TABLE_SWATCH_NOT_COLOR,
            [
                ("chart_id", json!("t")),
                ("swatch_column", json!("c")),
                ("row_index", json!(1)),
                ("value", json!("not a color")),
            ],
        )
    );
}

/// `_CSS_HEX_COLOR_PATTERN`'s `$` (colors.py:25) also matches before one
/// final newline, and `sanitize_color` returns the hex as written, so the
/// newline reaches the attribute as `escape_attr`'s `&#10;`.
#[test]
fn a_swatch_hex_with_one_trailing_newline_paints_as_written() {
    for (hex, fill) in [("#ff0000\n", "fill=\"#ff0000&#10;\""), ("#AbC\n", "fill=\"#AbC&#10;\"")] {
        let columns = json!({"n": {"label": "N"}, "c": {"swatch": true}});
        let svg = render(&table(columns, vec![json!({"n": "a", "c": hex})]), &test_fonts()).expect("a color");
        assert!(svg.contains(fill), "{hex:?}:\n{svg}");
    }
}

/// Only `$`'s one final `\n`: no other trailing character, no second
/// newline, and never after a keyword (`lower() in {...}` is exact).
#[test]
fn a_swatch_is_not_a_color_past_the_one_trailing_newline() {
    for value in ["#ff0000\n\n", "#ff0000\r", "#ff0000\r\n", "#ff0000 ", "\n#ff0000", "#ff00\n", "none\n"] {
        let columns = json!({"n": {"label": "N"}, "c": {"swatch": true}});
        let d = raised(&table(columns, vec![json!({"n": "a", "c": value})]));
        assert_eq!(d.code, codes::ERR_TABLE_SWATCH_NOT_COLOR.id, "{value:?}");
    }
}

/// Production checks a swatch while painting the cell, after layout has
/// formatted every cell, so a cell format error wins over an earlier row's
/// bad swatch.
#[test]
fn a_cell_format_error_is_raised_before_a_bad_swatch() {
    let _locale = crate::time_locale::en_us_scope();
    let columns = json!({"at": {"label": "At"}, "c": {"swatch": true}});
    let rows = vec![
        json!({"at": "2024-03-05T09:30:00", "c": "not a color"}),
        json!({"at": "2024-03-05T25:00:00", "c": "#ff0000"}),
    ];
    assert_eq!(raised(&table(columns, rows)).code, codes::ERR_TABLE_TEMPORAL_FORMAT_INVALID.id);
}

/// A static export pre-renders at most `STATIC_MULTI_PAGE_MAX_PAGES` pages;
/// a row past them is never painted, so its swatch is never checked.
#[test]
fn a_bad_swatch_on_a_page_the_export_does_not_render_is_not_checked() {
    let columns = json!({"n": {"label": "N"}, "c": {"swatch": true}});
    let mut rows: Vec<Value> = (0..400).map(|i| json!({"n": format!("r{i}"), "c": "#ff0000"})).collect();
    rows[399]["c"] = json!("not a color");
    let svg = render(&table(columns, rows), &test_fonts()).expect("the bad row is never painted");
    assert!(svg.contains("data-page=\"20\""), "the export is capped:\n{svg}");
}

/// `_authored_spanner_level`.
#[test]
fn a_spanner_naming_a_column_the_query_lacks() {
    let mut payload = table(json!({}), vec![json!({"a": "x", "b": 1.0, "c": 2.0})]);
    payload.resolved["spanners"] = json!([{"label": "Money", "columns": ["b", "ghost"]}]);
    assert_eq!(
        raised(&payload),
        Diagnostic::new(
            &codes::ERR_TABLE_SPANNER_UNKNOWN_COLUMN,
            [
                ("chart_id", json!("t")),
                ("label", json!("Money")),
                ("unknown", json!(["ghost"])),
                ("columns", json!(["a", "b", "c"])),
            ],
        )
    );
}

/// `_authored_spanner_level`.
#[test]
fn a_spanner_whose_columns_are_not_adjacent() {
    let mut payload = table(json!({}), vec![json!({"a": "x", "b": 1.0, "c": 2.0})]);
    payload.resolved["spanners"] = json!([{"label": "Split", "columns": ["a", "c"]}]);
    assert_eq!(
        raised(&payload),
        Diagnostic::new(
            &codes::ERR_TABLE_SPANNER_NOT_ADJACENT,
            [
                ("chart_id", json!("t")),
                ("label", json!("Split")),
                ("spanner_columns", json!(["a", "c"])),
                ("visible_columns", json!(["a", "b", "c"])),
            ],
        )
    );
}

/// `_transpose_data_for_render`.
#[test]
fn a_transposed_table_over_two_rows() {
    let mut payload = table(json!({}), vec![json!({"a": "x"}), json!({"a": "y"})]);
    payload.resolved["style"]["table"]["transpose"] = json!(true);
    assert_eq!(
        raised(&payload),
        Diagnostic::new(&codes::ERR_TABLE_TRANSPOSE_ROW_COUNT, [("row_count", json!(2))])
    );
}

/// `_transpose_data_for_render`.
#[test]
fn a_transposed_table_hiding_a_column_the_query_lacks() {
    let columns = json!({"ghost": {"visible": false}, "a": {"label": "A"}});
    let mut payload = table(columns, vec![json!({"b": 1.0, "a": "x"})]);
    payload.resolved["style"]["table"]["transpose"] = json!(true);
    assert_eq!(
        raised(&payload),
        Diagnostic::new(
            &codes::ERR_TABLE_TRANSPOSE_VISIBLE_UNKNOWN_COLUMN,
            [("unaddressed", json!(["ghost"])), ("available", json!(["a", "b"]))],
        )
    );
}

/// `_prepare_columns`.
#[test]
fn a_table_hiding_a_column_it_does_not_render() {
    let columns = json!({"a": {"label": "A"}, "ghost": {"visible": false}});
    assert_eq!(
        raised(&table(columns, vec![json!({"b": 1.0, "a": "x"})])),
        Diagnostic::new(
            &codes::ERR_TABLE_VISIBLE_UNKNOWN_COLUMN,
            [
                ("chart_id", json!("t")),
                ("unaddressed", json!(["ghost"])),
                ("addressable", json!(["a", "b"])),
            ],
        )
    );
}

fn field_not_found(channel: &str, observed: Value) -> Diagnostic {
    Diagnostic::new(
        &codes::ERR_TABLE_PIVOT_FIELD_NOT_FOUND,
        [("channel", json!(channel)), ("field", json!("ghost")), ("observed", observed)],
    )
}

/// `_validate_pivot_fields`.
#[test]
fn a_pivot_column_the_query_lacks() {
    let payload = pivot(sales(), json!(["region"]), json!(["ghost"]), json!(["revenue"]));
    assert_eq!(raised(&payload), field_not_found("pivot.column", observed()));
}

/// `_validate_pivot_fields`.
#[test]
fn a_pivot_row_role_that_is_a_literal_role() {
    let mut payload = pivot(sales(), json!(["region"]), json!(["quarter"]), json!(["revenue"]));
    payload.resolved["style"]["table"]["row"]["role"] = json!("total");
    assert_eq!(
        raised(&payload),
        Diagnostic::new(
            &codes::ERR_TABLE_PIVOT_ROW_ROLE_LITERAL,
            [("row_role", json!("total")), ("observed", observed())],
        )
    );
}

/// `_validate_pivot_fields`.
#[test]
fn a_pivot_value_the_query_lacks() {
    let payload = pivot(sales(), json!(["region"]), json!(["quarter"]), json!(["ghost"]));
    assert_eq!(raised(&payload), field_not_found("pivot.value", observed()));
}

/// `_validate_pivot_fields`, explicit `values: []`.
#[test]
fn a_pivot_with_no_measures() {
    let payload = pivot(sales(), json!(["region"]), json!(["quarter"]), json!([]));
    assert_eq!(
        raised(&payload),
        Diagnostic::new(&codes::ERR_TABLE_PIVOT_NO_MEASURES, [("observed", observed())])
    );
}

/// `values` omitted: `infer_pivot_measure_names` leaves no
/// field once `rows` and `columns` claim them all.
#[test]
fn a_pivot_whose_inferred_measures_are_empty() {
    let rows = vec![json!({"region": "West", "quarter": "Q1"})];
    let payload = pivot(rows, json!(["region"]), json!(["quarter"]), Value::Null);
    assert_eq!(
        raised(&payload),
        Diagnostic::new(&codes::ERR_TABLE_PIVOT_NO_MEASURES, [("observed", json!(["quarter", "region"]))])
    );
}

/// `_validate_pivot_fields`.
#[test]
fn a_pivot_row_the_query_lacks() {
    let payload = pivot(sales(), json!(["ghost"]), json!(["quarter"]), json!(["revenue"]));
    assert_eq!(raised(&payload), field_not_found("pivot.row", observed()));
}

/// with `values` omitted: the inferred measures pass, the
/// declared row does not.
#[test]
fn a_pivot_row_the_query_lacks_with_inferred_measures() {
    let payload = pivot(sales(), json!(["ghost"]), json!(["quarter"]), Value::Null);
    assert_eq!(raised(&payload), field_not_found("pivot.row", observed()));
}

/// `pivot_table_data`.
#[test]
fn a_pivot_with_neither_rows_nor_values() {
    let payload = pivot(sales(), Value::Null, json!(["quarter"]), Value::Null);
    assert_eq!(raised(&payload), Diagnostic::new(&codes::ERR_TABLE_PIVOT_DIMENSIONS_AMBIGUOUS, []));
}

/// `pivot_table_data`.
#[test]
fn pivoted_rows_merging_with_conflicting_roles() {
    let rows = vec![
        json!({"region": "West", "quarter": "Q1", "revenue": 10.0, "kind": "value"}),
        json!({"region": "West", "quarter": "Q2", "revenue": 12.0, "kind": "total"}),
    ];
    let mut payload = pivot(rows, json!(["region"]), json!(["quarter"]), json!(["revenue"]));
    payload.resolved["style"]["table"]["row"]["role"] = json!("kind");
    assert_eq!(
        raised(&payload),
        Diagnostic::new(
            &codes::ERR_TABLE_PIVOT_ROW_ROLE_CONFLICT,
            [("row", json!("region='West'")), ("stored_role", json!("value")), ("this_role", json!("total"))],
        )
    );
}

/// A pivot cell filled twice: `pivot_column` is the `repr()` of the column
/// tuple, a one-tuple keeping its trailing comma.
#[test]
fn a_pivot_cell_filled_twice() {
    let rows = vec![
        json!({"region": "West", "quarter": "Q1", "revenue": 10.0}),
        json!({"region": "West", "quarter": "Q1", "revenue": 12.0}),
    ];
    let payload = pivot(rows, json!(["region"]), json!(["quarter"]), json!(["revenue"]));
    assert_eq!(
        raised(&payload),
        Diagnostic::new(
            &codes::ERR_TABLE_PIVOT_DUPLICATE_CELL,
            [("row", json!("region='West'")), ("pivot_column", json!("('Q1',)")), ("measure", json!("revenue"))],
        )
    );

    let rows = vec![
        json!({"region": "West", "year": 2024, "quarter": "Q1", "revenue": 10.0, "units": 1}),
        json!({"region": "West", "year": 2024, "quarter": "Q1", "revenue": 12.0, "units": 2}),
    ];
    let payload = pivot(rows, json!(["region"]), json!(["year", "quarter"]), json!(["revenue", "units"]));
    assert_eq!(
        raised(&payload),
        Diagnostic::new(
            &codes::ERR_TABLE_PIVOT_DUPLICATE_CELL,
            [
                ("row", json!("region='West'")),
                ("pivot_column", json!("('2024', 'Q1')")),
                ("measure", json!("revenue")),
            ],
        )
    );
}

/// format_value.py:598 `format_table_cell_value`: an ISO timestamp the regex
/// accepts but `datetime.fromisoformat` rejects.
#[test]
fn a_timestamp_cell_with_an_out_of_range_hour() {
    let rows = vec![json!({"event": "deploy", "at": "2024-03-05T25:00:00"})];
    assert_eq!(
        raised(&table(json!({}), rows)),
        Diagnostic::new(
            &codes::ERR_TABLE_TEMPORAL_FORMAT_INVALID,
            [
                ("cell", json!("2024-03-05T25:00:00")),
                ("spec", json!("%-d %b %Y")),
                ("reason", json!("hour must be in 0..23")),
            ],
        )
    );
}

/// `resolve_cell_link`, reached through a column link.
#[test]
fn a_column_link_with_an_unknown_filter() {
    let columns = json!({"a": {"label": "A", "link": "/accounts/{{ a | upper }}"}});
    assert_eq!(
        raised(&table(columns, vec![json!({"a": "x"})])),
        Diagnostic::new(&codes::ERR_TABLE_LINK_FILTER_UNKNOWN, [("filter_name", json!("upper"))])
    );
}

/// The column tuple is `str()` of each cell, so a boolean reads `True`.
#[test]
fn a_pivot_cell_filled_twice_under_a_boolean_column() {
    let rows = vec![
        json!({"region": "West", "active": true, "revenue": 10.0}),
        json!({"region": "West", "active": true, "revenue": 12.0}),
    ];
    let payload = pivot(rows, json!(["region"]), json!(["active"]), json!(["revenue"]));
    assert_eq!(raised(&payload).params["pivot_column"], json!("('True',)"));
}

fn pivoted(rows: Vec<Value>) -> Result<PivotResult, Diagnostic> {
    let data: Vec<_> = rows.into_iter().map(|r| r.as_object().expect("a row object").clone()).collect();
    pivot_table_data(&data, &[], &["k".into()], &["c".into()], &["v".into()], None)
}

fn pivot_raised(rows: Vec<Value>) -> Diagnostic {
    pivoted(rows).err().expect("production raises here")
}

/// keys a wide row by the raw cells under Python equality
/// (`1 == 1.0 == True`, `0 == -0.0 == False`), and the wide row keeps the
/// first-seen cell.
#[test]
fn a_pivot_row_key_is_python_equality() {
    let wide = pivoted(vec![
        json!({"k": 1, "c": "a", "v": 1}),
        json!({"k": 1.0, "c": "b", "v": 2}),
        json!({"k": true, "c": "x", "v": 3}),
        json!({"k": 0, "c": "a", "v": 4}),
        json!({"k": -0.0, "c": "b", "v": 5}),
        json!({"k": false, "c": "x", "v": 6}),
    ])
    .expect("pivots");
    assert_eq!(wide.rows.len(), 2);
    assert_eq!((&wide.rows[0]["k"], &wide.rows[0]["b"], &wide.rows[0]["x"]), (&json!(1), &json!(2), &json!(3)));
    assert_eq!((&wide.rows[1]["k"], &wide.rows[1]["b"], &wide.rows[1]["x"]), (&json!(0), &json!(5), &json!(6)));
}

/// Python never equates a number with its text, `None` with `"None"`, or an
/// int with a float one ulp away (`2**53 + 1 != 2.0**53`).
#[test]
fn a_pivot_row_key_never_equates_across_text_or_inexact_numbers() {
    for (a, b) in [
        (json!(1), json!("1")),
        (json!(true), json!("True")),
        (json!(null), json!("None")),
        (json!(9007199254740993u64), json!(9007199254740992.0)),
        (json!(0.5), json!("0.5")),
    ] {
        let wide = pivoted(vec![json!({"k": a, "c": "a", "v": 1}), json!({"k": b, "c": "a", "v": 2})])
            .unwrap_or_else(|d| panic!("{a} vs {b}: {d}"));
        assert_eq!(wide.rows.len(), 2, "{a} vs {b}");
    }
}

/// `1` and `True` are one wide row, so their shared column is filled twice;
/// the message reprs the first-seen cell.
#[test]
fn a_pivot_cell_filled_twice_across_equal_numbers() {
    let raised = pivot_raised(vec![json!({"k": 1, "c": "a", "v": 1}), json!({"k": true, "c": "a", "v": 2})]);
    assert_eq!(raised.params["row"], json!("k=1"));
    let raised = pivot_raised(vec![json!({"k": 1e16, "c": "a", "v": 1}), json!({"k": 1e16, "c": "a", "v": 2})]);
    assert_eq!(raised.params["row"], json!("k=1e+16"));
}

/// A list or object cell is unhashable: production's row-key lookup raises
/// `TypeError`, so the engine refuses it by name.
#[test]
fn a_pivot_row_dimension_holding_a_list_or_object_is_refused_by_name() {
    for cell in [json!([1]), json!({"a": 1})] {
        let refused = pivot_raised(vec![json!({"k": cell, "c": "a", "v": 1})]);
        assert_eq!(refused.code, codes::ERR_ENGINE_REFUSED.id);
        assert!(refused.to_string().contains("'k'"), "{refused}");
    }
}
