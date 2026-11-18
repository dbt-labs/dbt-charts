//! A KPI's `style.conditional` rules over one row, and the sign rules `good` stands for. A column's
//! rules are one ordered list: every matching non-default rule contributes its outputs, the last
//! match winning per output; a `default: true` rule fires only when no other rule in the column
//! matched. A rule paints the card's background, the headline's and the label's font color and
//! weight, and the support row's font color, weight and tone; a font that extends a named font takes
//! that font's color and weight under its own. A rule color is a hex color, `transparent` or `none`,
//! as production sanitizes it. A rule over an aggregate is the author's error (`aggregate_operand`),
//! raised before any rule is read.

use board_model::chart::conditional_formatting::{Between, Bound, Operand, Predicate, Threshold};
use board_model::chart::kpi::Good;
use board_model::facet::{Color, FontWeightValue};
use board_model::ids::ChartId;
use board_model::primitives::Tone;
use board_model::style::font::NamedFont;
use board_model::style::kpi::{KpiChartStyleConditional, KpiGlyphSet, KpiTonesStyle, SingleLineFontStyleConditional};
use diagnostic::{diag, refuse, Diagnostic};
use indexmap::IndexMap;
use serde_json::Value;

/// What `good` draws for a cell: the sign's glyph and the color of its tone.
#[derive(Debug, Default, PartialEq)]
pub struct Signed {
    pub glyph: Option<String>,
    pub tone_color: Option<String>,
}

/// What the matched rules set on the card, the later match winning per output.
#[derive(Debug, Default, PartialEq)]
pub struct CardPatch {
    /// `background`: the card's fill, over a gradient's and the card's own.
    pub background: Option<String>,
    /// `support.font.color`.
    pub color: Option<String>,
    /// `support.font.weight`: the support row's and its glyph's weight.
    pub support_weight: Option<FontWeightValue>,
    /// `support.tone`.
    pub tone: Option<Tone>,
    /// `value.font.color`: it paints the whole headline run, over the tone `good` derives.
    pub value_color: Option<String>,
    /// `value.font.weight`: the value slot's weight.
    pub value_weight: Option<FontWeightValue>,
    /// `label.font.color` and `label.font.weight`.
    pub label_color: Option<String>,
    pub label_weight: Option<FontWeightValue>,
}

/// `json_loose_eq`: numbers compare by value (`1 == 1.0`); everything else must match in type and value.
fn loose_eq(value: &Value, want: &Value) -> bool {
    match (value, want) {
        (Value::Number(a), Value::Number(b)) => a.as_f64() == b.as_f64(),
        _ => value == want,
    }
}

fn same_kind_eq(value: Option<&Value>, want: &Value) -> bool {
    let value = value.unwrap_or(&Value::Null);
    matches!(value, Value::Bool(_)) == matches!(want, Value::Bool(_)) && loose_eq(value, want)
}

/// A cell a date operand orders, as days since the epoch: a date or timestamp text, else
/// `ERR-CONDITIONAL-NOT-A-DATE` naming `column`.
pub fn date_cell_day(column: &str, cell: &Value) -> Result<f64, Diagnostic> {
    cell.as_str().and_then(crate::time_axis::date_text_day).ok_or_else(|| {
        Diagnostic::new(
            &diagnostic::codes::ERR_CONDITIONAL_NOT_A_DATE,
            [("column", Value::String(column.to_string())), ("value", cell.clone())],
        )
    })
}

/// Whether `p` orders by date: its `lt`, `lte`, `gt`, `gte` or `between` operand is one.
fn orders_dates(p: &Predicate) -> bool {
    let date = |b: &Bound| matches!(b, Bound::Threshold(Threshold::Date { .. }));
    [&p.lt, &p.lte, &p.gt, &p.gte].into_iter().flatten().any(date) || p.between.as_ref().is_some_and(|b| b.0.iter().any(date))
}

/// `match_predicate`. `value` is `None` for a null cell.
fn matches(rule: &KpiChartStyleConditional, column: &str, value: Option<&Value>) -> Result<bool, Diagnostic> {
    let p = &rule.predicate;
    if p.default.is_some() {
        return Ok(true);
    }
    if let Some(want_null) = p.is_null {
        return Ok(value.is_none() == want_null);
    }
    if let Some(eq) = &p.eq {
        return Ok(same_kind_eq(value, literal(eq)?));
    }
    if let Some(ne) = &p.ne {
        return Ok(!same_kind_eq(value, literal(ne)?));
    }
    if let Some(candidates) = &p.r#in {
        if candidates.0.iter().any(|c| matches!(c.0, Value::Array(_) | Value::Object(_))) {
            return Err(Diagnostic::refused(refuse!("a KPI conditional rule `in` with a non-scalar candidate is not supported")));
        }
        return Ok(candidates.0.iter().any(|c| same_kind_eq(value, &c.0)));
    }
    let v = match value {
        Some(cell) if orders_dates(p) => date_cell_day(column, cell)?,
        _ => match value.and_then(Value::as_f64).filter(|v| v.is_finite()) {
            Some(v) => v,
            None => return Ok(false),
        },
    };
    if let Some(lt) = &p.lt {
        return Ok(v < key(lt)?);
    }
    if let Some(lte) = &p.lte {
        return Ok(v <= key(lte)?);
    }
    if let Some(gt) = &p.gt {
        return Ok(v > key(gt)?);
    }
    if let Some(gte) = &p.gte {
        return Ok(v >= key(gte)?);
    }
    if let Some(Between([low, high])) = &p.between {
        return Ok(key(low)? <= v && v <= key(high)?);
    }
    Err(Diagnostic::refused(refuse!("a KPI conditional rule with no predicate is not supported")))
}

/// `_validate_kpi_conditional`: a KPI reads one row, so a rule comparing to an aggregate (`has_aggregate`
/// over `eq`, `ne`, `lt`, `lte`, `gt`, `gte` and `between`) is the author's error, raised for the first
/// column holding one whether or not its rule matches.
pub fn aggregate_operand(id: &ChartId, conditional: &IndexMap<String, Vec<KpiChartStyleConditional>>) -> Option<Diagnostic> {
    let aggregate = |p: &Predicate| {
        [&p.eq, &p.ne].into_iter().flatten().any(|o| matches!(o, Operand::Extreme(_)))
            || [&p.lt, &p.lte, &p.gt, &p.gte].into_iter().flatten().chain(p.between.iter().flat_map(|b| b.0.iter())).any(|b| matches!(b, Bound::Aggregate(_)))
    };
    conditional
        .iter()
        .find(|(_, rules)| rules.iter().any(|rule| aggregate(&rule.predicate)))
        .map(|(column, _)| diag::err_kpi_conditional_aggregate(id.as_str(), column.as_str()))
}

fn literal(operand: &Operand) -> Result<&Value, Diagnostic> {
    match operand {
        Operand::Value(v) => Ok(v),
        Operand::Extreme(_) => Err(diag::err_internal("a KPI conditional aggregate operand reaches its match".to_string())),
    }
}

fn key(bound: &Bound) -> Result<f64, Diagnostic> {
    match bound {
        Bound::Threshold(t) => Ok(t.key()),
        Bound::Aggregate(_) => Err(diag::err_internal("a KPI conditional aggregate bound reaches its match".to_string())),
    }
}

/// A rule's color as chart-renderer `kpi.rs`'s `explicit_color` reads it (`sanitized_color`): a hex
/// color as written, `transparent` or `none` as `transparent`. Production errors on any other text too.
fn rule_color(color: &str) -> Result<String, Diagnostic> {
    crate::table::sanitized_color(color)
        .map(str::to_string)
        .ok_or_else(|| Diagnostic::refused(refuse!("a KPI conditional rule color that is not a hex color, transparent or none is not supported")))
}

/// The board's color for `tone`.
pub fn tone_color(tones: &KpiTonesStyle, tone: Tone) -> &str {
    match tone {
        Tone::Positive => tones.positive.as_str(),
        Tone::Negative => tones.negative.as_str(),
        Tone::Warning => tones.warning.as_str(),
        Tone::Info => tones.info.as_str(),
    }
}

/// A rule font's color and weight: its own, over those of the named font it extends
/// (`expand_named_fonts`; the registry's own chains are folded by the cascade). An unknown name is
/// the registry's error, raised for every rule, matched or not, as compile raises it.
fn rule_font(extends: Option<&String>, color: Option<&Color>, weight: Option<&FontWeightValue>, fonts: &IndexMap<String, NamedFont>, column: &str) -> Result<(Option<Color>, Option<FontWeightValue>), Diagnostic> {
    let named = match extends {
        None => None,
        Some(name) => Some(fonts.get(name).ok_or_else(|| {
            let known = fonts.keys().map(String::as_str).collect::<Vec<_>>().join(", ");
            diag::err_font_unknown(format!("Unknown font '{name}' at style.conditional.{column}. Known fonts: {known}."))
        })?),
    };
    Ok((color.or(named.and_then(|font| font.color.as_ref())).cloned(), weight.or(named.and_then(|font| font.weight.as_ref())).cloned()))
}

fn font_of(font: Option<&SingleLineFontStyleConditional>, fonts: &IndexMap<String, NamedFont>, column: &str, color: &mut Option<String>, weight: &mut Option<FontWeightValue>) -> Result<(), Diagnostic> {
    let Some(font) = font else { return Ok(()) };
    let (set_color, set_weight) = rule_font(font.extends.as_ref(), font.color.as_ref(), font.weight.as_ref(), fonts, column)?;
    if let Some(set) = set_color {
        *color = Some(rule_color(set.as_str())?);
    }
    if let Some(set) = set_weight {
        *weight = Some(set);
    }
    Ok(())
}

/// Every rule's named fonts resolve, matched or not, as compile raises an unknown name; nothing
/// else of a rule that does not match is read.
fn check_fonts(rule: &KpiChartStyleConditional, fonts: &IndexMap<String, NamedFont>, column: &str) -> Result<(), Diagnostic> {
    let ruled = [rule.value.as_ref().and_then(|value| value.font.as_ref()), rule.label.as_ref().and_then(|label| label.font.as_ref())];
    for font in ruled.into_iter().flatten() {
        rule_font(font.extends.as_ref(), None, None, fonts, column)?;
    }
    if let Some(font) = rule.support.as_ref().and_then(|support| support.font.as_ref()) {
        rule_font(font.extends.as_ref(), None, None, fonts, column)?;
    }
    Ok(())
}

/// What `rule` sets on the card: its background, the value's and label's font color and weight, and
/// the support row's font color, weight and tone.
fn apply(rule: &KpiChartStyleConditional, fonts: &IndexMap<String, NamedFont>, column: &str, out: &mut CardPatch) -> Result<(), Diagnostic> {
    if let Some(background) = &rule.background {
        out.background = Some(rule_color(background.as_str())?);
    }
    font_of(rule.value.as_ref().and_then(|value| value.font.as_ref()), fonts, column, &mut out.value_color, &mut out.value_weight)?;
    font_of(rule.label.as_ref().and_then(|label| label.font.as_ref()), fonts, column, &mut out.label_color, &mut out.label_weight)?;
    let Some(support) = &rule.support else {
        return Ok(());
    };
    if let Some(font) = &support.font {
        let (color, weight) = rule_font(font.extends.as_ref(), font.color.as_ref(), font.weight.as_ref(), fonts, column)?;
        if let Some(color) = color {
            out.color = Some(rule_color(color.as_str())?);
        }
        if let Some(weight) = weight {
            out.support_weight = Some(weight);
        }
    }
    if let Some(tone) = support.tone {
        out.tone = Some(tone);
    }
    Ok(())
}

/// The support row's patch from every column's rules, each tested against its own cell of `row`.
pub fn card_patch(conditional: &IndexMap<String, Vec<KpiChartStyleConditional>>, row: &serde_json::Map<String, Value>, fonts: &IndexMap<String, NamedFont>) -> Result<CardPatch, Diagnostic> {
    let mut out = CardPatch::default();
    for (column, rules) in conditional {
        let Some(cell) = row.get(column) else {
            return Err(Diagnostic::refused(refuse!("a KPI `style.conditional.{column}` on a column the query does not return is not supported")));
        };
        let cell = Some(cell).filter(|cell| !cell.is_null());
        for rule in rules {
            check_fonts(rule, fonts, column)?;
        }
        let (mut matched_any, mut default_rule) = (false, None);
        for rule in rules {
            if rule.predicate.default.is_some() {
                default_rule = Some(rule);
                continue;
            }
            if matches(rule, column, cell)? {
                matched_any = true;
                apply(rule, fonts, column, &mut out)?;
            }
        }
        if let (false, Some(rule)) = (matched_any, default_rule) {
            apply(rule, fonts, column, &mut out)?;
        }
    }
    Ok(out)
}

/// `_good_rules`: the sign rules `good` stands for on `cell`, as the outputs they give it. A positive
/// value takes the `up` glyph and a negative one the `down` glyph, each in the tone of its direction
/// (`good: up` makes up positive), and zero the `neutral` glyph alone. A null cell matches none.
/// Production refuses a cell that is no number, and a style that leaves `up` or `down` unset.
pub fn good_overrides(good: Good, glyphs: &KpiGlyphSet, tones: &KpiTonesStyle, cell: Option<&Value>) -> Result<Signed, Diagnostic> {
    let mut out = Signed::default();
    if cell.is_some_and(|cell| cell.as_f64().is_none_or(|v| !v.is_finite())) {
        return Err(Diagnostic::refused(refuse!("a KPI `good` over a cell that is not a number is not supported")));
    }
    let (Some(up), Some(down)) = (&glyphs.up, &glyphs.down) else {
        return Err(Diagnostic::refused(refuse!("a KPI `good` over a style with no `up` or `down` glyph is not supported")));
    };
    let (up_tone, down_tone) = match good {
        Good::Up => (Tone::Positive, Tone::Negative),
        Good::Down => (Tone::Negative, Tone::Positive),
    };
    let Some(v) = cell.and_then(Value::as_f64) else {
        return Ok(out);
    };
    let (glyph, tone) = if v > 0.0 {
        (up, Some(up_tone))
    } else if v < 0.0 {
        (down, Some(down_tone))
    } else {
        match &glyphs.neutral {
            Some(neutral) => (neutral, None),
            None => return Ok(out),
        }
    };
    out.glyph = Some(glyph.clone());
    out.tone_color = tone.map(|tone| tone_color(tones, tone).to_string());
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn date(day: u8) -> Bound {
        let text = format!("2026-01-{day:02}");
        Bound::Threshold(Threshold::Date { text, day: d3rs::calendar::days_from_civil(2026, 1, day) as f64 })
    }

    fn rule(predicate: Predicate) -> KpiChartStyleConditional {
        KpiChartStyleConditional { predicate, background: None, value: None, label: None, support: None }
    }

    fn unset() -> Predicate {
        Predicate { eq: None, ne: None, lt: None, lte: None, gt: None, gte: None, between: None, r#in: None, is_null: None, default: None }
    }

    #[test]
    fn a_date_rule_orders_date_cells_and_refuses_any_other_cell() {
        let gt = rule(Predicate { gt: Some(date(1)), ..unset() });
        assert!(matches(&gt, "d", Some(&json!("2026-01-02"))).unwrap());
        assert!(!matches(&gt, "d", Some(&json!("2025-12-31"))).unwrap());
        assert!(!matches(&gt, "d", None).unwrap());
        let between = rule(Predicate { between: Some(Between([date(1), date(31)])), ..unset() });
        assert!(matches(&between, "d", Some(&json!("2026-01-31"))).unwrap());
        let err = matches(&between, "d", Some(&json!(42))).unwrap_err();
        assert!(err.starts_with("ERR-CONDITIONAL-NOT-A-DATE"), "{err}");
    }

    fn rules(rule: Value) -> IndexMap<String, Vec<KpiChartStyleConditional>> {
        IndexMap::from([("g".to_string(), vec![board_model::fixtures::build(&rule).unwrap()])])
    }

    fn accent() -> IndexMap<String, NamedFont> {
        IndexMap::from([("accent".to_string(), board_model::fixtures::build(&json!({"color": "#8a2be2", "weight": 700})).unwrap())])
    }

    /// A rule font that extends a named font takes that font's color and weight under its own, as
    /// `expand_named_fonts` lays a slot's fields over its registry entry.
    #[test]
    fn a_rule_font_extends_its_named_font_under_its_own_fields() {
        let rule = json!({"default": true, "value": {"font": {"extends": "accent"}}, "support": {"font": {"extends": "accent", "color": "#111111"}}});
        let row = serde_json::Map::from_iter([("g".to_string(), json!(1))]);
        let patch = card_patch(&rules(rule), &row, &accent()).unwrap();
        assert_eq!(patch.value_color.as_deref(), Some("#8a2be2"));
        assert_eq!(patch.value_weight, accent()["accent"].weight.clone());
        assert_eq!(patch.color.as_deref(), Some("#111111"));
        assert_eq!(patch.support_weight, accent()["accent"].weight.clone());
    }

    #[test]
    fn a_rule_font_that_extends_an_unknown_font_is_the_font_registrys_error() {
        let rule = json!({"default": true, "label": {"font": {"extends": "nope"}}});
        let row = serde_json::Map::from_iter([("g".to_string(), json!(1))]);
        let err = card_patch(&rules(rule), &row, &accent()).unwrap_err();
        assert_eq!(err.code, "ERR-FONT-UNKNOWN");
    }
}
