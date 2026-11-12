//! A KPI's `style.conditional` rules over one row, and the sign rules `good` stands for. A column's
//! rules are one ordered list: every matching non-default rule contributes its outputs, the last
//! match winning per output; a `default: true` rule fires only when no other rule in the column
//! matched. This slice paints the support row only, so a rule that styles the card, the headline or
//! the label, a font weight, or a color that is not a hex literal (color tokens resolve before the
//! card paints and this stage does not) is refused by name.

use board_model::chart::kpi::Good;
use board_model::primitives::Tone;
use board_model::style::kpi::{KpiChartStyleConditional, KpiGlyphSet, KpiTonesStyle};
use diagnostic::{refuse, Diagnostic};
use indexmap::IndexMap;
use serde_json::Value;

/// What `good` draws for a cell: the sign's glyph and the color of its tone.
#[derive(Debug, Default, PartialEq)]
pub struct Signed {
    pub glyph: Option<String>,
    pub tone_color: Option<String>,
}

/// What the matched rules set on the support row, the later match winning per output.
#[derive(Debug, Default, PartialEq)]
pub struct SupportPatch {
    /// `support.font.color`.
    pub color: Option<String>,
    /// `support.tone`.
    pub tone: Option<Tone>,
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

/// `match_predicate`. `value` is `None` for a null cell.
fn matches(rule: &KpiChartStyleConditional, value: Option<&Value>) -> Result<bool, Diagnostic> {
    let p = &rule.predicate;
    if p.default.is_some() {
        return Ok(true);
    }
    if let Some(want_null) = p.is_null {
        return Ok(value.is_none() == want_null);
    }
    if let Some(eq) = &p.eq {
        return Ok(same_kind_eq(value, &eq.0));
    }
    if let Some(ne) = &p.ne {
        return Ok(!same_kind_eq(value, &ne.0));
    }
    if let Some(candidates) = &p.r#in {
        if candidates.0.iter().any(|c| matches!(c.0, Value::Array(_) | Value::Object(_))) {
            return Err(Diagnostic::refused(refuse!("a KPI conditional rule `in` with a non-scalar candidate is not supported")));
        }
        return Ok(candidates.0.iter().any(|c| same_kind_eq(value, &c.0)));
    }
    let Some(v) = value.and_then(Value::as_f64).filter(|v| v.is_finite()) else {
        return Ok(false);
    };
    if let Some(lt) = p.lt {
        return Ok(v < lt.get());
    }
    if let Some(lte) = p.lte {
        return Ok(v <= lte.get());
    }
    if let Some(gt) = p.gt {
        return Ok(v > gt.get());
    }
    if let Some(gte) = p.gte {
        return Ok(v >= gte.get());
    }
    if let Some(between) = &p.between {
        return Ok(between.0 .0[0] <= v && v <= between.0 .0[1]);
    }
    Err(Diagnostic::refused(refuse!("a KPI conditional rule with no predicate is not supported")))
}

fn hex(color: &str) -> Result<String, Diagnostic> {
    if color.starts_with('#') {
        Ok(color.to_string())
    } else {
        Err(Diagnostic::refused(refuse!("a KPI conditional rule color that is not a hex literal is not supported")))
    }
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

/// A rule paints only the support row's font color and tone, whether or not it matches.
fn check_outputs(column: &str, rule: &KpiChartStyleConditional) -> Result<(), Diagnostic> {
    let at = |what: &str| Diagnostic::refused(refuse!("a KPI `style.conditional.{column}` rule that sets {what} is not supported"));
    if rule.background.is_some() {
        return Err(at("`background`"));
    }
    if rule.value.is_some() || rule.label.is_some() {
        return Err(at("`value` or `label`"));
    }
    if let Some(font) = rule.support.as_ref().and_then(|support| support.font.as_ref()) {
        if font.extends.is_some() || font.weight.is_some() {
            return Err(at("a support font other than `color`"));
        }
    }
    Ok(())
}

fn apply(rule: &KpiChartStyleConditional, out: &mut SupportPatch) -> Result<(), Diagnostic> {
    let Some(support) = &rule.support else {
        return Ok(());
    };
    if let Some(color) = support.font.as_ref().and_then(|font| font.color.as_ref()) {
        out.color = Some(hex(color.as_str())?);
    }
    if let Some(tone) = support.tone {
        out.tone = Some(tone);
    }
    Ok(())
}

/// The support row's patch from every column's rules, each tested against its own cell of `row`.
pub fn support_patch(conditional: &IndexMap<String, Vec<KpiChartStyleConditional>>, row: &serde_json::Map<String, Value>) -> Result<SupportPatch, Diagnostic> {
    let mut out = SupportPatch::default();
    for (column, rules) in conditional {
        let Some(cell) = row.get(column) else {
            return Err(Diagnostic::refused(refuse!("a KPI `style.conditional.{column}` on a column the query does not return is not supported")));
        };
        let cell = Some(cell).filter(|cell| !cell.is_null());
        for rule in rules {
            check_outputs(column, rule)?;
        }
        let (mut matched_any, mut default_rule) = (false, None);
        for rule in rules {
            if rule.predicate.default.is_some() {
                default_rule = Some(rule);
                continue;
            }
            if matches(rule, cell)? {
                matched_any = true;
                apply(rule, &mut out)?;
            }
        }
        if let (false, Some(rule)) = (matched_any, default_rule) {
            apply(rule, &mut out)?;
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
