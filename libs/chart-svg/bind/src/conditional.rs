//! A KPI's `conditional_formatting` rules over one cell: `resolve_conditional_styles`
//! (`chart-renderer/src/families/table/bind.rs`, which the KPI support row shares). Every matching
//! non-default rule contributes its outputs, the last match winning per output; a `default: true`
//! rule fires only when no other rule matched. What a KPI support row never reads (a rule's
//! `background`, and a font's weight, style or decoration) is refused by name, as is a color that
//! is not a hex literal, since color tokens resolve before the card paints and this stage does not.

use board_model::chart::conditional_formatting::ConditionalRule;
use board_model::chart::kpi::Good;
use board_model::primitives::Tone;
use board_model::style::kpi::{KpiGlyphSet, KpiTonesStyle};
use diagnostic::{refuse, Diagnostic};
use model_core::Tri;
use serde_json::Value;

/// The outputs the matched rules contribute: the value's ink, the glyph and its ink, and the
/// `tone` color kept apart from the glyph's so the value can take it too.
#[derive(Debug, Default, PartialEq)]
pub struct RuleOverrides {
    pub color: Option<String>,
    pub glyph: Option<String>,
    pub glyph_color: Option<String>,
    pub tone_color: Option<String>,
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
fn matches(rule: &ConditionalRule, value: Option<&Value>) -> Result<bool, Diagnostic> {
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

/// `_validate_support_rules`: a KPI support rule paints only a font color, a glyph and its ink, so
/// any other output is refused whether or not its rule matches.
fn check_outputs(rule: &ConditionalRule) -> Result<(), Diagnostic> {
    if rule.background.is_some() {
        return Err(Diagnostic::refused(refuse!("a KPI conditional rule `background` is not supported")));
    }
    if let Some(font) = &rule.font {
        if font.extends != Tri::Unset || font.weight.is_some() || font.style.is_some() || font.decoration.is_some() {
            return Err(Diagnostic::refused(refuse!("a KPI conditional rule font other than `color` is not supported")));
        }
    }
    Ok(())
}

/// `_apply_rule_outputs`.
fn apply(rule: &ConditionalRule, out: &mut RuleOverrides, tones: &KpiTonesStyle) -> Result<(), Diagnostic> {
    if let Some(color) = rule.font.as_ref().and_then(|font| font.color.as_ref()) {
        out.color = Some(hex(color.as_str())?);
    }
    if let Some(glyph) = &rule.glyph {
        out.glyph = Some(glyph.as_str().to_string());
        out.glyph_color = None;
    }
    if let Some(color) = &rule.glyph_color {
        out.glyph_color = Some(hex(color.as_str())?);
    } else if let Some(tone) = rule.tone {
        out.glyph_color = Some(tone_color(tones, tone).to_string());
        out.tone_color = out.glyph_color.clone();
    }
    Ok(())
}

/// `_good_rules`: the sign rules `good` stands for on `cell`, as the outputs they give it. A positive
/// value takes the `up` glyph and a negative one the `down` glyph, each in the tone of its direction
/// (`good: up` makes up positive), and zero the `neutral` glyph alone. A null cell matches none.
/// Production refuses a cell that is no number, and a style that leaves `up` or `down` unset.
pub fn good_overrides(good: Good, glyphs: &KpiGlyphSet, tones: &KpiTonesStyle, cell: Option<&Value>) -> Result<RuleOverrides, Diagnostic> {
    let mut out = RuleOverrides::default();
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
    out.glyph_color = tone.map(|tone| tone_color(tones, tone).to_string());
    out.tone_color = out.glyph_color.clone();
    Ok(out)
}

/// The outputs `rules` give `value` (`None` for a null cell) on top of `start`, the sign rules of a
/// `good`, which the authored rules override output by output.
pub fn resolve_conditional_styles(rules: &[ConditionalRule], value: Option<&Value>, tones: &KpiTonesStyle, start: RuleOverrides) -> Result<RuleOverrides, Diagnostic> {
    rules.iter().try_for_each(check_outputs)?;
    let mut out = start;
    let (mut matched_any, mut default_rule) = (false, None);
    for rule in rules {
        if rule.predicate.default.is_some() {
            default_rule = Some(rule);
            continue;
        }
        if matches(rule, value)? {
            matched_any = true;
            apply(rule, &mut out, tones)?;
        }
    }
    if let (false, Some(rule)) = (matched_any, default_rule) {
        apply(rule, &mut out, tones)?;
    }
    Ok(out)
}
