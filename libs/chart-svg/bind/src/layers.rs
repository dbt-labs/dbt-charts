//! The data half of a layered chart: which rows a layer draws and how its color field groups, orders and
//! titles them. Marks, scales and legends are chart-renderer's `families::layers`.

use serde_json::Value;

use crate::rows::row_str;

/// A layer's color domain: the distinct non-null values of `field`, sorted
/// (`distinct_series_values`, `_cartesian.py`). A null value is no domain entry:
/// a row carrying one still draws, as a series the scale has no color for.
pub fn layer_color_values(rows: &[serde_json::Map<String, Value>], field: &str) -> Vec<String> {
    crate::rows::color_domain_of(rows.iter().filter(|row| row.get(field).is_some_and(|v| !v.is_null())), field)
}

/// The "color" of a series with no entry on the shared scale: Vega paints it
/// with no stroke or fill.
pub const UNSCALED_COLOR: &str = "none";

/// A color-grouped layer's series from the baseline up. No `order` encoding
/// reaches a layer, so Vega-Lite's stack sorts its color field descending (the
/// first value ends on top); rows with no color value sort lowest and so end on
/// top of every series.
pub fn layer_stack_order(domain: &[String], any_null: bool) -> Vec<Option<String>> {
    domain.iter().rev().cloned().map(Some).chain(any_null.then_some(None)).collect()
}

/// A row's color value: `None` where it has none.
pub fn layer_color_of(row: &serde_json::Map<String, Value>, field: &str) -> Option<String> {
    row.get(field).filter(|v| !v.is_null()).map(|_| row_str(row, field))
}

/// What an aria label reads for a color value a row has none of.
pub const NULL_COLOR_TEXT: &str = "null";

/// The value an aria label reads for a row's color.
pub fn layer_color_text(row: &serde_json::Map<String, Value>, field: &str) -> String {
    layer_color_of(row, field).unwrap_or_else(|| NULL_COLOR_TEXT.to_string())
}

/// Whether a layer's color distinguishes its rows, which promotes the color value into
/// each datum's description (`series_row_promoted`): more than one non-null value.
pub fn layer_color_promoted(rows: &[serde_json::Map<String, Value>], field: &str) -> bool {
    layer_color_values(rows, field).len() > 1
}

/// One series of a color-grouped layer: its color value (`None` for a row with none) and its rows.
pub type ColorGroup = (Option<String>, Vec<serde_json::Map<String, Value>>);

/// The series of a color-grouped line or area layer as Vega's facet meets them:
/// in order of first appearance in `rows`, each with its own rows, a row with no
/// color value being a series of its own (`None`).
pub fn layer_color_groups(rows: &[serde_json::Map<String, Value>], field: &str) -> Vec<ColorGroup> {
    let mut series: Vec<ColorGroup> = Vec::new();
    for row in rows {
        let value = layer_color_of(row, field);
        match series.iter_mut().find(|(seen, _)| *seen == value) {
            Some((_, own)) => own.push(row.clone()),
            None => series.push((value, vec![row.clone()])),
        }
    }
    series
}

/// Which rows a layer draws in one cell. Vega-Lite facets only the base's data:
/// a layer on the base query draws its panel's slice, except a band-step layer,
/// whose doubled rows are inline data and draw whole in every cell; a layer
/// reading another query draws that dataset (doubled, if band-step) whole.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum LayerRows {
    Panel,
    Chart,
    Dataset,
}

pub fn layer_rows_scope(
    is_facet: bool,
    band_step: bool,
    layer_query_name: Option<&str>,
    base_query_name: Option<&str>,
) -> LayerRows {
    match (is_facet && layer_query_name == base_query_name, band_step) {
        (true, false) => LayerRows::Panel,
        (true, true) => LayerRows::Chart,
        (false, _) => LayerRows::Dataset,
    }
}

/// The title of an axis a layered chart's marks share. Vega-Lite joins the titles
/// of the encodings the axis holds, each distinct one once, in layer order.
pub fn merged_axis_title(titles: impl IntoIterator<Item = String>) -> String {
    let mut distinct: Vec<String> = Vec::new();
    for title in titles {
        if !distinct.contains(&title) {
            distinct.push(title);
        }
    }
    distinct.join(", ")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// No `order` channel reaches a layer: color descending from the baseline, the
    /// first value on top, and no-color rows sorting lowest so they end on top.
    #[test]
    fn a_layer_stacks_its_color_descending_from_the_baseline_with_no_color_last() {
        let domain = ["a".to_string(), "b".to_string()];
        assert_eq!(layer_stack_order(&domain, false), [Some("b".to_string()), Some("a".to_string())]);
        assert_eq!(layer_stack_order(&domain, true), [Some("b".to_string()), Some("a".to_string()), None]);
    }

    #[test]
    fn merged_axis_title_joins_each_distinct_title_once_in_layer_order() {
        let titles = ["y", "z", "y", "w"].map(String::from);
        assert_eq!(merged_axis_title(titles), "y, z, w");
        assert_eq!(merged_axis_title(["y".to_string()]), "y");
    }
}
