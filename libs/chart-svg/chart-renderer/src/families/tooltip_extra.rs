//! The columns a chart's `tooltip:` adds to a mark's hover card.
//!
//! Python resolve decides each entry's label and format (`resolve_tooltip`);
//! this paints a row's value with them, and refuses a value that does not fit.

use super::table_dates::{is_time_format, parse_temporal, strftime, TemporalError};
use crate::common::{format_mark_label, FormatSlot};
use crate::contract::{extra_row, SEMICOLON_ESCAPE};
use d3rs::native::NULL_DISPLAY;
use serde::Deserialize;
use serde_json::{Map, Value};

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct TooltipField {
    column: String,
    label: String,
    /// `null` where the column's values print as the text they are.
    #[serde(deserialize_with = "Option::deserialize")]
    format: Option<FormatSlot>,
}

impl TooltipField {
    fn value(&self, row: &Map<String, Value>) -> Result<String, String> {
        let wrong = |e: String| format!("tooltip column {:?}: {e}", self.column);
        let cell = row.get(&self.column).ok_or_else(|| wrong("missing from the row".into()))?;
        let text = match (cell, &self.format) {
            (Value::Null, _) => NULL_DISPLAY.to_string(),
            (Value::Number(n), Some(format)) if !is_time_format(&format.spec) => {
                // The value-label register: a house format prints a sub-1 value in digits, never milli.
                let value = n.as_f64().ok_or_else(|| wrong(format!("{n} is not a finite number")))?;
                format_mark_label(Some(format), value, format.is_house()).map_err(wrong)?
            }
            (Value::String(text), Some(format)) if is_time_format(&format.spec) => match parse_temporal(text.trim()) {
                Ok(Some(moment)) => strftime(moment, &format.spec).map_err(wrong)?,
                Ok(None) => return Err(wrong(format!("{text:?} is not a date"))),
                Err(TemporalError::Invalid(why) | TemporalError::Unported(why)) => return Err(wrong(why)),
            },
            (_, Some(format)) => return Err(wrong(format!("{cell} does not fit the format {:?}", format.spec))),
            (Value::String(text), None) => text.clone(),
            (Value::Number(_) | Value::Bool(_), None) => cell.to_string(),
            (_, None) => return Err(wrong(format!("{cell} is not a scalar"))),
        };
        if text.contains(SEMICOLON_ESCAPE) {
            return Err(wrong(format!("{text:?} holds the character reserved to escape ';'")));
        }
        Ok(text.replace(';', &SEMICOLON_ESCAPE.to_string()))
    }
}

/// The authored rows of one datum, in authored order.
pub(crate) fn rows_for(fields: &[TooltipField], row: &Map<String, Value>) -> Result<Vec<String>, String> {
    fields.iter().map(|field| Ok(extra_row(&field.label, &field.value(row)?))).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn rows(fields: Value, row: Value) -> Vec<String> {
        let fields: Vec<TooltipField> = serde_json::from_value(fields).unwrap();
        rows_for(&fields, row.as_object().unwrap()).unwrap()
    }

    #[test]
    fn a_number_prints_through_the_resolved_format() {
        let out = rows(
            json!([{"column": "share", "label": "% of budget", "format": ".1%"}]),
            json!({"share": 0.25}),
        );
        assert_eq!(out, ["\u{200D}% of budget: 25.0%"]);
    }

    #[test]
    fn text_prints_as_written_and_a_bool_as_true_or_false() {
        let out = rows(
            json!([
                {"column": "approved", "label": "Approved", "format": null},
                {"column": "open", "label": "Open", "format": null}
            ]),
            json!({"approved": "yes", "open": true}),
        );
        assert_eq!(out, ["\u{200D}Approved: yes", "\u{200D}Open: true"]);
    }

    #[test]
    fn a_null_prints_the_empty_value_text_tables_use() {
        let out = rows(
            json!([
                {"column": "approved", "label": "Approved", "format": null},
                {"column": "share", "label": "Share", "format": ".1%"}
            ]),
            json!({"approved": null, "share": null}),
        );
        assert_eq!(out, ["\u{200D}Approved: \u{2014}", "\u{200D}Share: \u{2014}"]);
    }

    #[test]
    fn a_date_prints_through_the_resolved_time_format() {
        let _scope = crate::time_locale::en_us_scope();
        let out = rows(
            json!([{"column": "closed_on", "label": "Closed", "format": "%-d %b %Y"}]),
            json!({"closed_on": "2024-01-31"}),
        );
        assert_eq!(out, ["\u{200D}Closed: 31 Jan 2024"]);
    }

    #[test]
    fn a_number_format_the_engine_rejects_refuses_by_name() {
        let fields: Vec<TooltipField> =
            serde_json::from_value(json!([{"column": "n", "label": "N", "format": "not-a-real-spec"}])).unwrap();
        let err = rows_for(&fields, json!({"n": 1}).as_object().unwrap()).unwrap_err();
        assert!(err.contains("tooltip column \"n\""), "{err}");
    }

    fn err(fields: Value, row: Value) -> String {
        let fields: Vec<TooltipField> = serde_json::from_value(fields).unwrap();
        rows_for(&fields, row.as_object().unwrap()).unwrap_err()
    }

    /// A `;` in a value would split the row when the runtime splits the label.
    #[test]
    fn a_semicolon_in_a_value_is_escaped_for_the_runtime_to_decode() {
        let out = rows(
            json!([{"column": "note", "label": "Note", "format": null}]),
            json!({"note": "up: 3; down: 1"}),
        );
        assert_eq!(out, ["\u{200D}Note: up: 3\u{E000} down: 1"]);
    }

    #[test]
    fn a_value_holding_the_escape_character_refuses() {
        let e = err(
            json!([{"column": "note", "label": "Note", "format": null}]),
            json!({"note": "a\u{E000}b"}),
        );
        assert!(e.contains("tooltip column \"note\""), "{e}");
    }

    #[test]
    fn a_missing_column_refuses_rather_than_print_nothing() {
        let e = err(json!([{"column": "gone", "label": "Gone", "format": null}]), json!({"other": 1}));
        assert!(e.contains("tooltip column \"gone\"") && e.contains("missing"), "{e}");
    }

    #[test]
    fn a_value_of_the_wrong_kind_for_its_format_refuses() {
        let e = err(json!([{"column": "d", "label": "D", "format": "%-d %b %Y"}]), json!({"d": "not a date"}));
        assert!(e.contains("tooltip column \"d\""), "{e}");
        let e = err(json!([{"column": "d", "label": "D", "format": "%-d %b %Y"}]), json!({"d": 5}));
        assert!(e.contains("tooltip column \"d\""), "{e}");
        let e = err(json!([{"column": "n", "label": "N", "format": ".1%"}]), json!({"n": "text"}));
        assert!(e.contains("tooltip column \"n\""), "{e}");
        let e = err(json!([{"column": "n", "label": "N", "format": null}]), json!({"n": [1]}));
        assert!(e.contains("tooltip column \"n\""), "{e}");
    }

    #[test]
    fn the_resolved_entry_is_read_exactly() {
        let unknown = serde_json::from_value::<Vec<TooltipField>>(
            json!([{"column": "a", "label": "A", "format": null, "divider": true}]),
        );
        assert!(unknown.is_err());
        let no_format = serde_json::from_value::<Vec<TooltipField>>(json!([{"column": "a", "label": "A"}]));
        assert!(no_format.is_err());
    }
}
