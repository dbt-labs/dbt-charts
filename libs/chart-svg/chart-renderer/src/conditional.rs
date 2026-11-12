//! Conditional rules, shared by every family: one predicate per rule, an ordered walk where
//! each matching rule applies and a later match overwrites an earlier one key by key, and a
//! `default` rule that applies only when nothing else matched. A family deserializes its own
//! rule body beside a flattened [`Predicate`] and merges the rules [`matching`] returns.

use serde::Deserialize;
use serde_json::Value;

/// The condition half of a rule: exactly one operator is set.
#[derive(Deserialize, Clone, Default)]
pub(crate) struct Predicate {
    #[serde(default)]
    pub(crate) eq: Option<Value>,
    #[serde(default)]
    pub(crate) ne: Option<Value>,
    #[serde(default)]
    pub(crate) lt: Option<f64>,
    #[serde(default)]
    pub(crate) lte: Option<f64>,
    #[serde(default)]
    pub(crate) gt: Option<f64>,
    #[serde(default)]
    pub(crate) gte: Option<f64>,
    #[serde(default)]
    pub(crate) between: Option<(f64, f64)>,
    #[serde(default, rename = "in")]
    pub(crate) in_: Option<Vec<Value>>,
    #[serde(default)]
    pub(crate) is_null: Option<bool>,
    #[serde(default)]
    pub(crate) default: Option<bool>,
}

/// Whether `a` and `b` compare equal — a scalar-only port of Python's `==`:
/// numbers compare by value (`1 == 1.0`), everything else must match type
/// AND value. `value` is `None` for both a missing key and an explicit JSON
/// `null` (production's `row.get(col)`), so a `None` operand is treated as
/// `Value::Null`.
pub(crate) fn json_loose_eq(value: Option<&Value>, want: &Value) -> bool {
    let value = value.unwrap_or(&Value::Null);
    match (value, want) {
        (Value::Null, Value::Null) => true,
        (Value::Bool(a), Value::Bool(b)) => a == b,
        (Value::Number(a), Value::Number(b)) => a.as_f64() == b.as_f64(),
        (Value::String(a), Value::String(b)) => a == b,
        (Value::Array(a), Value::Array(b)) => a == b,
        (Value::Object(a), Value::Object(b)) => a == b,
        _ => false,
    }
}

pub(crate) fn is_bool_value(v: Option<&Value>) -> bool {
    matches!(v, Some(Value::Bool(_)))
}

/// `match_predicate` — the single active predicate on `rule` against
/// `value`. Errors only on a rule with no predicate op set (unreachable on
/// a Pydantic-validated payload; `_validate_exactly_one_predicate` requires
/// exactly one) and an `in` list holding a non-scalar candidate (JS/Python
/// equality on nested arrays/objects is not modeled here).
pub(crate) fn match_predicate(rule: &Predicate, value: Option<&Value>) -> Result<bool, String> {
    if rule.default == Some(true) {
        return Ok(true);
    }
    if let Some(want_null) = rule.is_null {
        return Ok(value.is_none() == want_null);
    }
    if let Some(eq) = &rule.eq {
        if is_bool_value(value) != matches!(eq, Value::Bool(_)) {
            return Ok(false);
        }
        return Ok(json_loose_eq(value, eq));
    }
    if let Some(ne) = &rule.ne {
        if is_bool_value(value) != matches!(ne, Value::Bool(_)) {
            return Ok(true);
        }
        return Ok(!json_loose_eq(value, ne));
    }
    if let Some(candidates) = &rule.in_ {
        for c in candidates {
            if !matches!(
                c,
                Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_)
            ) {
                return Err(
                    "conditional rule 'in' with a non-scalar candidate not supported".into(),
                );
            }
        }
        return Ok(candidates.iter().any(|c| {
            is_bool_value(value) == matches!(c, Value::Bool(_)) && json_loose_eq(value, c)
        }));
    }
    let Some(v) = coerce_numeric_cell(value) else {
        return Ok(false);
    };
    if let Some(lt) = rule.lt {
        return Ok(v < lt);
    }
    if let Some(lte) = rule.lte {
        return Ok(v <= lte);
    }
    if let Some(gt) = rule.gt {
        return Ok(v > gt);
    }
    if let Some(gte) = rule.gte {
        return Ok(v >= gte);
    }
    if let Some((lo, hi)) = rule.between {
        return Ok(lo <= v && v <= hi);
    }
    Err("conditional rule has no active predicate".into())
}

/// Whether `column`'s rules skip the mark drawn for `row`: a multi-y fold draws one mark per
/// measure from one source row, and a rule on a folded measure restyles that measure's series.
pub(crate) fn other_series(row: &serde_json::Map<String, Value>, column: &str, measures: &[String]) -> bool {
    measures.iter().any(|m| m == column)
        && row.get(::bind::wide::WIDE_KEY_FIELD).and_then(Value::as_str).is_some_and(|key| key != column)
}

/// The rules to apply for `value`, in order: every non-default rule that matches, or, when
/// none did, the `default: true` rule. `predicate` reads a rule's condition.
pub(crate) fn matching<'a, R>(
    rules: &'a [R],
    predicate: impl Fn(&R) -> &Predicate,
    value: Option<&Value>,
) -> Result<Vec<&'a R>, String> {
    let mut hits = Vec::new();
    let mut default_rule = None;
    for rule in rules {
        let condition = predicate(rule);
        if condition.default == Some(true) {
            default_rule = Some(rule);
        } else if match_predicate(condition, value)? {
            hits.push(rule);
        }
    }
    if hits.is_empty() {
        hits.extend(default_rule);
    }
    Ok(hits)
}

/// One `style.conditional` rule of a mark family (pie, heatmap, geoshape): a predicate beside
/// the per-datum style it applies. Compile refuses every key outside these.
#[derive(Deserialize)]
pub(crate) struct MarkRule {
    #[serde(flatten)]
    pub(crate) predicate: Predicate,
    #[serde(default)]
    marks: Option<RuleMarks>,
}

#[derive(Deserialize, Default)]
struct RuleMarks {
    #[serde(default)]
    slice: Option<RulePaint>,
    #[serde(default)]
    rect: Option<RulePaint>,
    #[serde(default)]
    geoshape: Option<RulePaint>,
    #[serde(default)]
    text: Option<RuleText>,
}

/// A mark's paint: `color` on a slice or rect, `fill` on a geoshape.
#[derive(Deserialize)]
struct RulePaint {
    #[serde(default)]
    color: Option<String>,
    #[serde(default)]
    fill: Option<String>,
    #[serde(default)]
    opacity: Option<f64>,
    #[serde(default)]
    stroke: Option<RuleStroke>,
}

#[derive(Deserialize)]
struct RuleStroke {
    #[serde(default)]
    color: Option<String>,
    #[serde(default)]
    width: Option<f64>,
}

#[derive(Deserialize)]
struct RuleText {
    #[serde(default)]
    font: Option<RuleFont>,
}

#[derive(Deserialize)]
struct RuleFont {
    #[serde(default)]
    color: Option<String>,
    #[serde(default)]
    weight: Option<Value>,
}

/// Which mark of a family a rule's paint is read from.
#[derive(Clone, Copy)]
pub(crate) enum MarkKind {
    Slice,
    Rect,
    Geoshape,
}

/// What the matched rules set on one datum's mark, key by key; unset keys keep the static style.
#[derive(Default)]
pub(crate) struct MarkPatch {
    pub(crate) fill: Option<String>,
    pub(crate) opacity: Option<f64>,
    pub(crate) stroke: Option<String>,
    pub(crate) stroke_w: Option<f64>,
    pub(crate) font_color: Option<String>,
    pub(crate) font_weight: Option<String>,
}

impl MarkPatch {
    /// The patch `rules` make to the mark drawn for `row`: every rule that matches its column's
    /// value applies, the later winning per key. Rules on different columns never set one key
    /// (resolve refuses it), so column order is immaterial.
    pub(crate) fn of(
        rules: Option<&std::collections::BTreeMap<String, Vec<MarkRule>>>,
        row: &serde_json::Map<String, Value>,
        kind: MarkKind,
    ) -> Result<MarkPatch, String> {
        let mut patch = MarkPatch::default();
        for (column, column_rules) in rules.into_iter().flatten() {
            for rule in matching(column_rules, |r| &r.predicate, row.get(column))? {
                let Some(marks) = &rule.marks else { continue };
                let paint = match kind {
                    MarkKind::Slice => &marks.slice,
                    MarkKind::Rect => &marks.rect,
                    MarkKind::Geoshape => &marks.geoshape,
                };
                if let Some(paint) = paint {
                    patch.fill = paint.color.clone().or(paint.fill.clone()).or(patch.fill);
                    patch.opacity = paint.opacity.or(patch.opacity);
                    if let Some(stroke) = &paint.stroke {
                        patch.stroke = stroke.color.clone().or(patch.stroke);
                        patch.stroke_w = stroke.width.or(patch.stroke_w);
                    }
                }
                if let Some(font) = marks.text.as_ref().and_then(|t| t.font.as_ref()) {
                    patch.font_color = font.color.clone().or(patch.font_color);
                    if let Some(weight) = &font.weight {
                        let css = crate::families::table::bind::font_weight_as_css(weight);
                        if !crate::families::table::VALID_FONT_WEIGHTS.contains(&css.as_str()) {
                            return Err(format!("conditional font weight {css:?} is not a CSS weight"));
                        }
                        patch.font_weight = Some(css);
                    }
                }
            }
        }
        Ok(patch)
    }
}

/// A JSON number coerces to `f64`; anything else (a string whatever it reads
/// like, `null`) is not numeric. JSON cannot encode NaN/±Infinity, so every
/// number here is finite.
pub(crate) fn coerce_numeric_cell(value: Option<&Value>) -> Option<f64> {
    value?.as_f64()
}


#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn rules(list: Value) -> Vec<Predicate> {
        serde_json::from_value(list).unwrap()
    }

    fn picked(list: Value, value: Value) -> Vec<usize> {
        let all = rules(list);
        matching(&all, |p| p, Some(&value))
            .unwrap()
            .into_iter()
            .map(|hit| all.iter().position(|p| std::ptr::eq(p, hit)).unwrap())
            .collect()
    }

    #[test]
    fn every_matching_rule_applies_in_order() {
        assert_eq!(picked(json!([{"gte": 1}, {"gte": 5}, {"lt": 0}]), json!(9)), [0, 1]);
    }

    #[test]
    fn a_later_matching_rule_overwrites_an_earlier_one_key_by_key() {
        let rules: std::collections::BTreeMap<String, Vec<MarkRule>> = serde_json::from_value(json!({
            "n": [
                {"gte": 1, "marks": {"slice": {"color": "#111111", "opacity": 0.5}}},
                {"gte": 5, "marks": {"slice": {"color": "#222222"}}}
            ]
        }))
        .unwrap();
        let row = json!({"n": 9}).as_object().unwrap().clone();
        let patch = MarkPatch::of(Some(&rules), &row, MarkKind::Slice).unwrap();
        assert_eq!(patch.fill.as_deref(), Some("#222222"));
        assert_eq!(patch.opacity, Some(0.5));
        assert!(MarkPatch::of(Some(&rules), &row, MarkKind::Rect).unwrap().fill.is_none());
    }

    #[test]
    fn default_applies_only_when_nothing_matched() {
        let list = json!([{"lt": 0}, {"default": true}]);
        assert_eq!(picked(list.clone(), json!(-1)), [0]);
        assert_eq!(picked(list, json!(3)), [1]);
    }
}
