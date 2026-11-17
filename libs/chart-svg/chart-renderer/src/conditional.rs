//! Conditional rules, shared by every family: one predicate per rule, an ordered walk where
//! each matching rule applies and a later match overwrites an earlier one key by key, and a
//! `default` rule that applies only when nothing else matched. A family deserializes its own
//! rule body beside a flattened [`Predicate`] and merges the rules [`matching`] returns.

use diagnostic::Diagnostic;
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
    pub(crate) lt: Option<Threshold>,
    #[serde(default)]
    pub(crate) lte: Option<Threshold>,
    #[serde(default)]
    pub(crate) gt: Option<Threshold>,
    #[serde(default)]
    pub(crate) gte: Option<Threshold>,
    #[serde(default)]
    pub(crate) between: Option<(Threshold, Threshold)>,
    #[serde(default, rename = "in")]
    pub(crate) in_: Option<Vec<Value>>,
    #[serde(default)]
    pub(crate) is_null: Option<bool>,
    #[serde(default)]
    pub(crate) default: Option<bool>,
}

/// A comparison operand: a number, or ISO date text ordered as days since the epoch.
#[derive(Clone, Copy)]
pub(crate) struct Threshold {
    key: f64,
    date: bool,
}

impl<'de> Deserialize<'de> for Threshold {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        match Value::deserialize(d)? {
            Value::Number(n) => n.as_f64().map(|key| Threshold { key, date: false }),
            Value::String(text) if text.len() >= 10 => {
                d3rs::calendar::date_text_day(&text).map(|key| Threshold { key, date: true })
            }
            _ => None,
        }
        .ok_or_else(|| serde::de::Error::custom("a conditional operand is a number or an ISO date"))
    }
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
/// `value`, a cell of `column`. Errors only on a rule with no predicate op set (unreachable on
/// a Pydantic-validated payload; `_validate_exactly_one_predicate` requires
/// exactly one) and an `in` list holding a non-scalar candidate (JS/Python
/// equality on nested arrays/objects is not modeled here).
pub(crate) fn match_predicate(rule: &Predicate, column: &str, value: Option<&Value>) -> Result<bool, Diagnostic> {
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
                return Err(Diagnostic::refused(
                    "conditional rule 'in' with a non-scalar candidate not supported",
                ));
            }
        }
        return Ok(candidates.iter().any(|c| {
            is_bool_value(value) == matches!(c, Value::Bool(_)) && json_loose_eq(value, c)
        }));
    }
    let bounds = [rule.lt, rule.lte, rule.gt, rule.gte].into_iter().flatten();
    let dated = bounds.chain(rule.between.into_iter().flat_map(|(lo, hi)| [lo, hi])).any(|t| t.date);
    let v = match value.filter(|v| !v.is_null()) {
        Some(cell) if dated => bind::conditional::date_cell_day(column, cell)?,
        _ => match coerce_numeric_cell(value) {
            Some(v) => v,
            None => return Ok(false),
        },
    };
    if let Some(lt) = rule.lt {
        return Ok(v < lt.key);
    }
    if let Some(lte) = rule.lte {
        return Ok(v <= lte.key);
    }
    if let Some(gt) = rule.gt {
        return Ok(v > gt.key);
    }
    if let Some(gte) = rule.gte {
        return Ok(v >= gte.key);
    }
    if let Some((lo, hi)) = rule.between {
        return Ok(lo.key <= v && v <= hi.key);
    }
    Err(Diagnostic::refused("conditional rule has no active predicate"))
}

/// Whether `column`'s rules skip the mark drawn for `row`: a multi-y fold draws one mark per
/// measure from one source row, and a rule on a folded measure restyles that measure's series.
pub(crate) fn other_series(row: &serde_json::Map<String, Value>, column: &str, measures: &[String]) -> bool {
    measures.iter().any(|m| m == column)
        && row.get(::bind::wide::WIDE_KEY_FIELD).and_then(Value::as_str).is_some_and(|key| key != column)
}

/// The rules to apply for `value`, a cell of `column`, in order: every non-default rule that matches, or, when
/// none did, the `default: true` rule. `predicate` reads a rule's condition.
pub(crate) fn matching<'a, R>(
    rules: &'a [R],
    predicate: impl Fn(&R) -> &Predicate,
    column: &str,
    value: Option<&Value>,
) -> Result<Vec<&'a R>, Diagnostic> {
    let mut hits = Vec::new();
    let mut default_rule = None;
    for rule in rules {
        let condition = predicate(rule);
        if condition.default == Some(true) {
            default_rule = Some(rule);
        } else if match_predicate(condition, column, value)? {
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
    #[serde(default)]
    pub(crate) axis_x: Option<RuleAxisX>,
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
    ) -> Result<MarkPatch, Diagnostic> {
        let mut patch = MarkPatch::default();
        for (column, column_rules) in rules.into_iter().flatten() {
            for rule in matching(column_rules, |r| &r.predicate, column, row.get(column))? {
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
                            return Err(format!("conditional font weight {css:?} is not a CSS weight").into());
                        }
                        patch.font_weight = Some(css);
                    }
                }
            }
        }
        Ok(patch)
    }
}

/// A rule's `axis_x`: the style it gives the x-axis labels it matches.
#[derive(Deserialize)]
pub(crate) struct RuleAxisX {
    #[serde(default)]
    labels: Option<RuleAxisLabels>,
}

#[derive(Deserialize)]
struct RuleAxisLabels {
    #[serde(default)]
    font: Option<RuleAxisFont>,
}

#[derive(Deserialize)]
struct RuleAxisFont {
    #[serde(default)]
    color: Option<String>,
}

/// The x column's rules with the label color each sets, owned so an axis that places its labels late can test them.
#[derive(Clone, Default)]
pub(crate) struct XLabelRules {
    column: String,
    rules: Vec<(Predicate, Option<String>)>,
}

impl XLabelRules {
    pub(crate) fn any(&self) -> bool {
        self.rules.iter().any(|(_, color)| color.is_some())
    }

    /// The color of a label whose own value is `tick`, the later match winning.
    pub(crate) fn fill(&self, tick: &Value) -> Result<Option<String>, Diagnostic> {
        let mut fill = None;
        for (_, color) in matching(&self.rules, |r| &r.0, &self.column, Some(tick))? {
            fill = color.clone().or(fill);
        }
        Ok(fill)
    }
}

/// A day since the epoch as the `YYYY-MM-DD` text a date rule tests.
pub(crate) fn iso_day(day: f64) -> Value {
    let (y, m, d) = d3rs::calendar::civil_from_days(day.floor() as i64);
    Value::String(format!("{y:04}-{m:02}-{d:02}"))
}

/// A chart's `style.conditional` as its x-axis labels read it. A rule on the x column tests a label's own value; a
/// rule on any other column tests the one row of its series the label names, and a label naming several is refused.
pub(crate) struct AxisLabelRules<'a, R> {
    pub(crate) family: &'static str,
    pub(crate) rules: Option<&'a std::collections::BTreeMap<String, Vec<R>>>,
    pub(crate) predicate: fn(&R) -> &Predicate,
    pub(crate) axis: fn(&R) -> Option<&RuleAxisX>,
    /// A multi-y chart's measures: a rule on one reads only that measure's rows.
    pub(crate) measures: &'a [String],
    pub(crate) x: &'a str,
}

impl<R> AxisLabelRules<'_, R> {
    fn color_of(&self, rule: &R) -> Option<String> {
        (self.axis)(rule).and_then(|a| a.labels.as_ref()?.font.as_ref()?.color.clone())
    }

    fn axis_columns(&self) -> impl Iterator<Item = (&String, &Vec<R>)> {
        self.rules.into_iter().flatten().filter(|(_, rules)| rules.iter().any(|r| self.color_of(r).is_some()))
    }

    /// Whether any rule restyles the labels.
    pub(crate) fn any(&self) -> bool {
        self.axis_columns().next().is_some()
    }

    /// Whether a rule on a column other than x restyles the labels: such a rule reads the row a label names.
    pub(crate) fn reads_rows(&self) -> bool {
        self.axis_columns().any(|(column, _)| column != self.x)
    }

    /// Refuses a rule that reads rows on an axis whose labels name none (`why`).
    pub(crate) fn refuse_rows(&self, why: &str) -> Result<(), Diagnostic> {
        match self.axis_columns().find(|(column, _)| *column != self.x) {
            Some((column, _)) => Err(format!(
                "{}: {why}, so style.conditional.{column} cannot color the x-axis labels; key the rule on the x column instead",
                self.family
            )
            .into()),
            None => Ok(()),
        }
    }

    /// Refuses every rule that restyles the labels, on an axis that does not draw them per value (`why`).
    pub(crate) fn refuse_all(&self, why: &str) -> Result<(), Diagnostic> {
        match self.axis_columns().next() {
            Some((column, _)) => Err(format!("{}: {why}, so style.conditional.{column} cannot color the x-axis labels", self.family).into()),
            None => Ok(()),
        }
    }

    /// The color of the label `label`, whose own value is `tick` and which names `rows`. The later match wins.
    pub(crate) fn fill(
        &self,
        label: &str,
        tick: Option<&Value>,
        rows: &[&serde_json::Map<String, Value>],
    ) -> Result<Option<String>, Diagnostic> {
        let mut fill = None;
        for (column, rules) in self.axis_columns() {
            let value: Option<&Value> = if column == self.x {
                tick
            } else {
                // A label reads its rows' value of the column only when they all agree: several rows that
                // disagree would need combining, which a label never does.
                let mut values = rows.iter().filter(|row| !other_series(row, column, self.measures)).map(|row| row.get(column));
                let Some(first) = values.next() else { continue };
                if values.any(|value| value != first) {
                    return Err(format!(
                        "{}: the x-axis label {label:?} names several rows whose {column} differs (its period spans several x \
                         values, or several series share it), so style.conditional.{column} cannot color it; key the rule on the \
                         x column instead",
                        self.family
                    )
                    .into());
                }
                first
            };
            for rule in matching(rules, self.predicate, column, value)? {
                fill = self.color_of(rule).or(fill);
            }
        }
        Ok(fill)
    }

    /// The color of a label naming the x categories `cats` (one, or a run a coarser label spans): its value is the
    /// first category, its rows every row on them.
    pub(crate) fn category_fill(
        &self,
        label: &str,
        cats: &[String],
        rows: &[serde_json::Map<String, Value>],
    ) -> Result<Option<String>, Diagnostic> {
        // The label's value as its rows carry it, so a number category compares as a number.
        let first = cats.first();
        let tick = rows
            .iter()
            .find(|row| first.is_some_and(|cat| ::bind::rows::row_str(row, self.x) == *cat))
            .and_then(|row| row.get(self.x).cloned())
            .or_else(|| first.cloned().map(Value::String));
        self.span_fill(label, tick.as_ref(), cats, rows)
    }

    /// The color of a label whose own value is `tick` and which names the rows on the x categories `cats`.
    pub(crate) fn span_fill(
        &self,
        label: &str,
        tick: Option<&Value>,
        cats: &[String],
        rows: &[serde_json::Map<String, Value>],
    ) -> Result<Option<String>, Diagnostic> {
        let named: Vec<&serde_json::Map<String, Value>> = rows
            .iter()
            .filter(|row| {
                let text = ::bind::rows::row_str(row, self.x);
                cats.iter().any(|cat| *cat == text)
            })
            .collect();
        self.fill(label, tick, &named)
    }

    /// The x column's rules, for an axis that tests each label's own value only.
    pub(crate) fn x_rules(&self) -> XLabelRules {
        let rules = self.rules.and_then(|r| r.get(self.x)).into_iter().flatten();
        XLabelRules {
            column: self.x.to_string(),
            rules: rules.map(|r| ((self.predicate)(r).clone(), self.color_of(r))).collect(),
        }
    }

    /// Each category's label color, for an axis that draws one label per category.
    pub(crate) fn category_fills(
        &self,
        cats: &[String],
        rows: &[serde_json::Map<String, Value>],
    ) -> Result<std::collections::HashMap<String, String>, Diagnostic> {
        let mut fills = std::collections::HashMap::new();
        if !self.any() {
            return Ok(fills);
        }
        for cat in cats {
            if let Some(fill) = self.category_fill(cat, std::slice::from_ref(cat), rows)? {
                fills.insert(cat.clone(), fill);
            }
        }
        Ok(fills)
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
        matching(&all, |p| p, "c", Some(&value))
            .unwrap()
            .into_iter()
            .map(|hit| all.iter().position(|p| std::ptr::eq(p, hit)).unwrap())
            .collect()
    }

    #[test]
    fn a_date_operand_orders_date_cells_by_instant() {
        let list = json!([{"gt": "2026-01-01"}, {"between": ["2026-01-01", "2026-01-31T12:00:00"]}]);
        assert_eq!(picked(list.clone(), json!("2026-01-02")), [0, 1]);
        assert_eq!(picked(list.clone(), json!("2026-01-01T00:00:00Z")), [1]);
        assert!(picked(list, json!("2025-12-31")).is_empty());
        let all = rules(json!([{"lt": "2026-01-01"}]));
        assert!(matching(&all, |p| p, "c", None).unwrap().is_empty());
    }

    #[test]
    fn a_date_operand_refuses_a_cell_that_is_no_date() {
        let all = rules(json!([{"lt": "2026-01-01"}]));
        let err = matching(&all, |p| p, "signup", Some(&json!(42))).err().unwrap();
        assert!(err.starts_with("ERR-CONDITIONAL-NOT-A-DATE"), "{err}");
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
