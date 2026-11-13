//! `style.conditional` on a point mark (scatter and point map): per-column rules that restyle
//! the point drawn for a row. The rule types mirror the Python rule body
//! (`ScatterChartStyleConditional`, `PointMapChartStyleConditional`); evaluation is
//! [`crate::conditional::matching`].

use std::collections::BTreeMap;

use diagnostic::Diagnostic;
use serde::Deserialize;
use serde_json::{Map, Value};

/// One rule: a predicate beside the point style it applies.
#[derive(Deserialize)]
pub(crate) struct PointRule {
    #[serde(flatten)]
    predicate: crate::conditional::Predicate,
    #[serde(default)]
    marks: Option<PointRuleMarks>,
}

#[derive(Deserialize)]
struct PointRuleMarks {
    #[serde(default)]
    point: Option<PointRulePoint>,
    #[serde(default)]
    text: Option<PointRuleText>,
}

#[derive(Deserialize)]
struct PointRulePoint {
    #[serde(default)]
    color: Option<String>,
    #[serde(default)]
    fill: Option<String>,
    #[serde(default)]
    filled: Option<bool>,
    #[serde(default)]
    size: Option<f64>,
    #[serde(default)]
    shape: Option<String>,
    #[serde(default)]
    opacity: Option<f64>,
    #[serde(default)]
    stroke_width: Option<f64>,
}

#[derive(Deserialize)]
struct PointRuleText {
    #[serde(default)]
    font: Option<PointRuleFont>,
}

#[derive(Deserialize)]
struct PointRuleFont {
    #[serde(default)]
    color: Option<String>,
    #[serde(default)]
    weight: Option<Value>,
}

/// What the matched rules set on one point, key by key; unset keys keep the static style.
#[derive(Default)]
pub(crate) struct PointPatch {
    pub(crate) color: Option<String>,
    pub(crate) fill: Option<String>,
    pub(crate) filled: Option<bool>,
    pub(crate) size: Option<f64>,
    pub(crate) shape: Option<String>,
    pub(crate) opacity: Option<f64>,
    pub(crate) stroke_width: Option<f64>,
    pub(crate) label_color: Option<String>,
    pub(crate) label_weight: Option<String>,
}

/// The patch the rules make to the point drawn for `row`. Rules on different columns never set
/// the same key (resolve refuses it), so column order is immaterial.
pub(crate) fn patch_of(
    rules: Option<&BTreeMap<String, Vec<PointRule>>>,
    row: &Map<String, Value>,
    measures: &[String],
) -> Result<PointPatch, Diagnostic> {
    let mut patch = PointPatch::default();
    for (column, column_rules) in rules.into_iter().flatten() {
        if crate::conditional::other_series(row, column, measures) {
            continue;
        }
        for rule in crate::conditional::matching(column_rules, |r| &r.predicate, column, row.get(column))? {
            let Some(marks) = &rule.marks else { continue };
            if let Some(point) = &marks.point {
                patch.color = point.color.clone().or(patch.color);
                patch.fill = point.fill.clone().or(patch.fill);
                patch.filled = point.filled.or(patch.filled);
                patch.size = point.size.or(patch.size);
                patch.shape = point.shape.clone().or(patch.shape);
                patch.opacity = point.opacity.or(patch.opacity);
                patch.stroke_width = point.stroke_width.or(patch.stroke_width);
            }
            if let Some(font) = marks.text.as_ref().and_then(|t| t.font.as_ref()) {
                patch.label_color = font.color.clone().or(patch.label_color);
                if let Some(weight) = &font.weight {
                    let css = crate::families::table::bind::font_weight_as_css(weight);
                    if !crate::families::table::VALID_FONT_WEIGHTS.contains(&css.as_str()) {
                        return Err(format!("point: conditional font weight {css:?} is not a CSS weight").into());
                    }
                    patch.label_weight = Some(css);
                }
            }
        }
    }
    Ok(patch)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn rules(list: Value) -> BTreeMap<String, Vec<PointRule>> {
        serde_json::from_value(list).unwrap()
    }

    fn row(value: Value) -> Map<String, Value> {
        value.as_object().unwrap().clone()
    }

    #[test]
    fn a_later_match_wins_per_key_and_default_waits() {
        let all = rules(json!({"score": [
            {"gt": 0, "marks": {"point": {"color": "#111111", "size": 50}}},
            {"gt": 5, "marks": {"point": {"color": "#222222"}}},
            {"default": true, "marks": {"point": {"opacity": 0.4}}},
        ]}));
        let hit = patch_of(Some(&all), &row(json!({"score": 9})), &[]).unwrap();
        assert_eq!((hit.color.as_deref(), hit.size, hit.opacity), (Some("#222222"), Some(50.0), None));
        let miss = patch_of(Some(&all), &row(json!({"score": -1})), &[]).unwrap();
        assert_eq!((miss.color, miss.opacity), (None, Some(0.4)));
    }

    #[test]
    fn a_font_weight_that_is_no_css_weight_is_refused() {
        let all = rules(json!({"s": [{"eq": 1, "marks": {"text": {"font": {"weight": "heavy"}}}}]}));
        assert!(patch_of(Some(&all), &row(json!({"s": 1})), &[]).is_err());
    }
}
