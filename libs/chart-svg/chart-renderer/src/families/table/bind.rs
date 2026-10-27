//! What a table derives from its chart and rows before any size is known: columns
//! resolved against the rows, cells formatted, the per-column demand scans and
//! the pivot reshape. Per-row styles, links and glyphs resolve in `lay_out`.
//! `bind` is the entry point.

use crate::families::color_scale::nice_domain;
use super::sanitize_color;
use super::svg_font_family;
use super::text_format;
use super::Blank;
use super::BoardFormats;
use super::Bound;
use super::Cell;
use super::Chart;
use super::ColConfig;
use super::ColMeta;
use super::ColumnFont;
use super::DimOrder;
use super::Drawn;
use super::EmptyResolved;
use super::Look;
use super::PivotLevel;
use super::PivotResult;
use super::Resolved;
use super::Rows;
use super::RowRole;
use super::RuleOverrides;
use super::Source;
use super::Spanner;
use super::SuffixMode;
use super::WhenRule;
use super::BOARD_FORMATS;
use super::NULL_DISPLAY;
use super::PIVOT_LEAF_SEP;
use super::VALID_FONT_WEIGHTS;
use crate::codes;
use crate::common::format_kpi_parts;
use crate::common::format_numeric_auto;
use crate::common::py_rstrip;
use crate::common::tag_at;
use crate::common::CellRow;
use crate::common::CellTag;
use crate::common::FormatParts;
use crate::common::FormatSlot;
use crate::common::PartsOptions;
use crate::common::ScaleTarget;
use crate::common::ScaledColumn;
use crate::common::{py_scalar_key, PyKey};
use crate::diagnostic::py_repr;
use crate::diagnostic::py_str;
use crate::diagnostic::Diagnostic;
use crate::families::table_dates;
use crate::Payload;
use crate::TonesStyle;
use d3rs::py_float::float_repr;
use d3rs::py_str::is_py_space;
use mdsvg::pygments_re::is_py_word;
use serde::Deserialize;
use serde_json::json;
use serde_json::Value;
use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::fmt::Write as _;

/// What the board redefined `name` to (`style.formats`), if it did.
pub(super) fn board_alias_format(name: &str) -> Result<Option<FormatSlot>, String> {
    BOARD_FORMATS.with(|slot| {
        slot.borrow()
            .0
            .get(name)
            .map(|alias| {
                Deserialize::deserialize(alias)
                    .map_err(|e| format!("table: board format {name:?}: {e}"))
            })
            .transpose()
    })
}

/// The board's plain-digit sibling of each adaptive member
/// (`board_style.sub_unit_specs`); `None` outside a payload scope.
pub(super) fn board_sub_unit_specs() -> Option<BTreeMap<String, String>> {
    BOARD_FORMATS.with(|slot| slot.borrow().1.clone())
}

/// The spec the board redefined `name` to.
pub(super) fn board_alias(name: &str) -> Result<Option<String>, String> {
    Ok(board_alias_format(name)?.map(|format| format.spec))
}

/// A column's symbol mode: its format's `repeat`, else the table's
/// (`column_symbol_mode`, `compile/format.py:488`). The one read of a column
/// `repeat`, shared by the prefix/unit lane and the shared-scale magnitude
/// word gate.
pub(super) fn column_symbol_mode<'a>(format: Option<&FormatSlot>, table_mode: &'a str) -> &'a str {
    match format.and_then(|f| f.repeat.as_deref()) {
        None => table_mode,
        Some("anchor") => "anchors",
        Some(_) => "all",
    }
}

/// `font_weight_as_css` (theme/board.py): a whole-number JSON float prints
/// as its bare integer string ("700"), a fractional one prints via Rust's
/// own `Display` (matching Python's `str(w)`), a JSON string passes
/// through unchanged.
pub(crate) fn font_weight_as_css(w: &Value) -> String {
    match w {
        Value::String(s) => s.clone(),
        Value::Number(n) => {
            let f = n.as_f64().unwrap_or(0.0);
            if f.fract() == 0.0 {
                format!("{}", f as i64)
            } else {
                float_repr(f)
            }
        }
        _ => String::new(),
    }
}

/// Merge one matched (or default) rule's style keys
/// into `result`, last-match-wins per key. A rule that sets `glyph` clears
/// any `glyph_color` an earlier rule left behind — a later rule swapping
/// the glyph without naming its own color must not inherit the previous
/// rule's ink. `tone` only ever supplies `glyph_color`, and only when the
/// rule didn't set `glyph_color` itself, resolved when `tones` is present;
/// `tones` absent is a by-design SKIP here (
/// comment: "a reachable, by-design skip"), never an error — a caller that
/// actually needs the resolved color reads
/// `RuleOverrides::tone_needs_board_tones` and refuses itself
/// (`resolve_cell_style`).
pub(super) fn apply_rule_outputs(
    rule: &WhenRule,
    result: &mut RuleOverrides,
    tones: Option<&TonesStyle>,
) -> Result<(), String> {
    if let Some(bg) = &rule.background {
        result.background = Some(bg.clone());
    }
    if let Some(font) = &rule.font {
        if let Some(c) = &font.color {
            result.color = Some(c.clone());
        }
        if let Some(w) = &font.weight {
            result.weight = Some(font_weight_as_css(w));
        }
        if let Some(s) = &font.style {
            result.style = Some(s.clone());
        }
        if let Some(d) = &font.decoration {
            result.decoration = Some(d.clone());
        }
    }
    if let Some(g) = &rule.glyph {
        result.glyph = Some(g.clone());
        result.glyph_color = None;
    }
    if let Some(gc) = &rule.glyph_color {
        result.glyph_color = Some(gc.clone());
    } else if let Some(tone) = &rule.tone {
        match tones {
            Some(tones) => {
                let hex = tones
                    .resolve(tone)
                    .ok_or_else(|| format!("conditional rule tone '{tone}' not recognized"))?;
                result.glyph_color = Some(hex.to_string());
            }
            None => result.tone_needs_board_tones = true,
        }
    }
    Ok(())
}

/// Whether `a` and `b` compare equal — a scalar-only port of Python's `==`:
/// numbers compare by value (`1 == 1.0`), everything else must match type
/// AND value. `value` is `None` for both a missing key and an explicit JSON
/// `null` (production's `row.get(col)`), so a `None` operand is treated as
/// `Value::Null`.
pub(super) fn json_loose_eq(value: Option<&Value>, want: &Value) -> bool {
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

pub(super) fn is_bool_value(v: Option<&Value>) -> bool {
    matches!(v, Some(Value::Bool(_)))
}

/// `match_predicate` — the single active predicate on `rule` against
/// `value`. Errors only on a rule with no predicate op set (unreachable on
/// a Pydantic-validated payload; `_validate_exactly_one_predicate` requires
/// exactly one) and an `in` list holding a non-scalar candidate (JS/Python
/// equality on nested arrays/objects is not modeled here).
pub(super) fn match_predicate(rule: &WhenRule, value: Option<&Value>) -> Result<bool, String> {
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

/// `resolve_conditional_styles`: every non-default rule that matches
/// contributes (last-match-wins per key); a trailing `default: true` rule
/// fires only when nothing else matched. Empty `rules` short-circuits to
/// "no overrides", same as production's `if not when_rules: return {}`.
pub(crate) fn resolve_conditional_styles(
    rules: &[WhenRule],
    value: Option<&Value>,
    tones: Option<&TonesStyle>,
) -> Result<RuleOverrides, String> {
    let mut result = RuleOverrides::default();
    let mut matched_any = false;
    let mut default_rule: Option<&WhenRule> = None;
    for rule in rules {
        if rule.default == Some(true) {
            default_rule = Some(rule);
            continue;
        }
        if match_predicate(rule, value)? {
            matched_any = true;
            apply_rule_outputs(rule, &mut result, tones)?;
        }
    }
    if !matched_any {
        if let Some(rule) = default_rule {
            apply_rule_outputs(rule, &mut result, tones)?;
        }
    }
    Ok(result)
}

/// `resolve_cell_glyph_from_overrides`: a matched rule's `glyph` wins
/// (with its own `glyph_color`, possibly `None`); otherwise the column's
/// static `glyph`/`glyph_color`. `tones` is `None` here — glyph-only
/// callers (the two measurement sites) never need a tone-resolved color,
/// only the glyph string itself.
pub(super) fn resolve_cell_glyph(
    cfg: &ColConfig,
    when_rules: &[WhenRule],
    value: Option<&Value>,
) -> Result<Option<String>, String> {
    let overrides =
        resolve_conditional_styles(when_rules, value, None).map_err(|e| format!("table: {e}"))?;
    if overrides.glyph.is_some() {
        return Ok(overrides.glyph);
    }
    Ok(cfg.glyph.clone())
}

/// `glyph_run`: the run painted ahead of a text cell's value — the glyph
/// plus its separating space, or empty. The one spelling every measurement
/// site (wrap layout) and the text-cell paint site share.
pub(super) fn glyph_run(glyph: Option<&str>) -> String {
    glyph.map_or_else(String::new, |g| format!("{g} "))
}

/// Cheap gate ahead of the
/// per-cell `resolve_cell_glyph` call, which otherwise evaluates every
/// `when` rule against every sampled value at each of this file's five
/// glyph-aware measurement sites.
pub(super) fn glyph_possible(col: &ColMeta) -> bool {
    !col.when_rules.is_empty() || col.cfg.glyph.is_some()
}

/// A cell's glyph run composed in front of `rendered`: the string every
/// glyph-aware measurement site measures ("every width measurement composes this
/// in front of the cell text"). The other, additive shape (`glyph width + widest
/// token width`, summed separately rather than concatenated) belongs to
/// `column_inputs`' word floors, which compose it locally instead.
pub(super) fn cell_text_with_glyph(
    col: &ColMeta,
    raw_value: Option<&Value>,
    rendered: &str,
) -> Result<String, String> {
    if !glyph_possible(col) {
        return Ok(rendered.to_string());
    }
    let glyph = resolve_cell_glyph(&col.cfg, &col.when_rules, raw_value)?;
    Ok(format!("{}{rendered}", glyph_run(glyph.as_deref())))
}

pub(super) fn expand_hex(c: &str) -> String {
    let h = c.trim_start_matches('#');
    if h.len() == 3 {
        let mut out = String::with_capacity(7);
        out.push('#');
        for ch in h.chars() {
            out.push(ch);
            out.push(ch);
        }
        out
    } else {
        format!("#{h}")
    }
}

pub(super) fn parse_hex(c: &str) -> (i64, i64, i64) {
    let h = expand_hex(c);
    let h = h.trim_start_matches('#');
    let r = i64::from_str_radix(&h[0..2], 16).unwrap_or(0);
    let g = i64::from_str_radix(&h[2..4], 16).unwrap_or(0);
    let b = i64::from_str_radix(&h[4..6], 16).unwrap_or(0);
    (r, g, b)
}

/// Linear interpolation between two hex colors, rounding
/// each channel `int(a + (b-a)*t + 0.5)` — Python's `int()` truncation of a
/// value nudged up by 0.5, i.e. round-half-up, not `f64::round`'s
/// round-half-away-from-zero (identical for positive channel values, which
/// every RGB channel here is).
pub(super) fn lerp_color(c1: &str, c2: &str, t: f64) -> String {
    let (r1, g1, b1) = parse_hex(c1);
    let (r2, g2, b2) = parse_hex(c2);
    let r = (r1 as f64 + (r2 - r1) as f64 * t + 0.5) as i64;
    let g = (g1 as f64 + (g2 - g1) as f64 * t + 0.5) as i64;
    let b = (b1 as f64 + (b2 - b1) as f64 * t + 0.5) as i64;
    format!("#{r:02x}{g:02x}{b:02x}")
}

/// `resolve_palette_stops`: an inline literal hex list passes through; a
/// named palette's baked `resolved_stops` is used otherwise. Refuses a
/// named-palette scheme with no baked stops (a caller bug in production,
/// per `resolve_palette_stops`'s own docstring — reachable here only from a
/// hand-built payload).
pub(crate) fn resolve_palette_stops(target: &ScaleTarget) -> Result<Vec<String>, String> {
    if let Value::Array(items) = &target.palette {
        if items.is_empty() {
            return Err("table: scale palette must not be empty".into());
        }
        return items
            .iter()
            .map(|v| {
                v.as_str()
                    .map(String::from)
                    .ok_or_else(|| "table: scale palette entry is not a string".to_string())
            })
            .collect();
    }
    match &target.resolved_stops {
        Some(stops) if !stops.is_empty() => Ok(stops.clone()),
        _ => Err("table: scale palette without resolved stops not supported".into()),
    }
}

/// `resolve_hinge`: `None` for sequential; a degenerate domain (`lo == hi`)
/// falls back to sequential regardless of what was authored; an explicit
/// float short-circuits; `"auto"` runs the zero-crossing → percent-crosses-
/// one → midpoint decision tree.
pub(crate) fn resolve_hinge(
    hinge: Option<&Value>,
    lo: f64,
    hi: f64,
    col_format: Option<&str>,
) -> Option<f64> {
    let hinge = hinge?;
    if lo == hi {
        return None;
    }
    if let Some(f) = hinge.as_f64() {
        return Some(f);
    }
    if hinge.as_str() != Some("auto") {
        return None;
    }
    if lo < 0.0 && 0.0 <= hi {
        return Some(0.0);
    }
    let is_percent = col_format.is_some_and(|f| f.contains('%'));
    if is_percent && lo < 1.0 && 1.0 < hi {
        return Some(1.0);
    }
    Some((lo + hi) / 2.0)
}

/// `diverging_arm_denominators`: the distance each arm's ramp runs over, one
/// shared wider-arm width when asymmetric.
pub(crate) fn arm_denominators(pivot: f64, (lo, hi): (f64, f64), asymmetric: bool) -> (f64, f64) {
    let (neg, pos) = (pivot - lo, hi - pivot);
    if asymmetric {
        let wider = neg.max(pos).max(1e-12);
        return (wider, wider);
    }
    (neg.max(1e-12), pos.max(1e-12))
}

/// Interpolate at normalized position `t` (clamped to
/// `[0, 1]`) along one arm's stop list.
pub(super) fn interpolate_arm(t: f64, stops: &[String]) -> String {
    let t = t.clamp(0.0, 1.0);
    let n_segments = stops.len() - 1;
    let segment = t * n_segments as f64;
    let i = (segment as usize).min(n_segments - 1);
    lerp_color(&stops[i], &stops[i + 1], segment - i as f64)
}

/// `interpolate_scale_color`: sequential (`hinge: None`) linearly
/// interpolates the whole palette across `[min_val, max_val]`; diverging
/// splits the (odd-length, >= 3 stop) palette at its middle stop and maps
/// each side of `hinge` onto its own arm, `arm_mode` deciding whether the
/// two arms share one intensity unit (`asymmetric`, the longer arm) or each
/// stretches fully to its own extreme (`symmetric`).
pub(crate) fn interpolate_scale_color(
    value: f64,
    min_val: f64,
    max_val: f64,
    palette: &[String],
    hinge: Option<f64>,
    arm_mode: &str,
) -> Result<String, String> {
    let palette: Vec<String> = palette.iter().map(|c| expand_hex(c)).collect();
    let Some(hinge) = hinge else {
        if min_val == max_val {
            return Ok(palette[palette.len() / 2].clone());
        }
        let t = ((value - min_val) / (max_val - min_val)).clamp(0.0, 1.0);
        let n_segments = palette.len() - 1;
        let segment = t * n_segments as f64;
        let i = (segment as usize).min(n_segments - 1);
        return Ok(lerp_color(&palette[i], &palette[i + 1], segment - i as f64));
    };
    if palette.len() < 3 || palette.len().is_multiple_of(2) {
        return Err(format!(
            "table: diverging palette must have an odd number of stops >= 3, got {}",
            palette.len()
        ));
    }
    let hinge = hinge.clamp(min_val, max_val);
    let (neg_denom, pos_denom) =
        arm_denominators(hinge, (min_val, max_val), arm_mode == "asymmetric");
    let mid = palette.len() / 2;
    let mut neg_stops: Vec<String> = palette[..=mid].to_vec();
    neg_stops.reverse();
    let pos_stops: Vec<String> = palette[mid..].to_vec();
    let clamped = value.clamp(min_val, max_val);
    if clamped <= hinge {
        Ok(interpolate_arm((hinge - clamped) / neg_denom, &neg_stops))
    } else {
        Ok(interpolate_arm((clamped - hinge) / pos_denom, &pos_stops))
    }
}

/// `compute_scale_domain`: explicit `min`/`max` win outright; otherwise the
/// domain is `rows`' own numeric extent for `field` (`(0, 1)` when no row
/// has a numeric value — production's "no numerics" fallback). `rows` must
/// already exclude summary/total rows (`scale_rows` in production — a
/// grand-total value would otherwise skew the gradient toward one end).
pub(crate) fn compute_scale_domain(
    rows: &[&serde_json::Map<String, Value>],
    field: &str,
    min: Option<f64>,
    max: Option<f64>,
) -> (f64, f64) {
    if let (Some(lo), Some(hi)) = (min, max) {
        return (lo, hi);
    }
    let values: Vec<f64> = rows
        .iter()
        .filter_map(|r| coerce_numeric_cell(r.get(field)))
        .collect();
    if values.is_empty() {
        return (min.unwrap_or(0.0), max.unwrap_or(1.0));
    }
    let lo = min.unwrap_or_else(|| values.iter().cloned().fold(f64::INFINITY, f64::min));
    let hi = max.unwrap_or_else(|| values.iter().cloned().fold(f64::NEG_INFINITY, f64::max));
    (lo, hi)
}

/// `resolve_table_style_value`: a spec naming a row field
/// takes that row's value (null or empty is no value), anything else is the
/// literal.
pub(super) fn resolve_style_input(
    spec: &str,
    row: &serde_json::Map<String, Value>,
) -> Option<String> {
    match row.get(spec) {
        None => Some(spec.to_string()),
        Some(Value::Null) => None,
        Some(Value::String(s)) if s.is_empty() => None,
        Some(v) => Some(py_str(v)),
    }
}

/// `is_sanitizable_color` + `sanitize_color` (colors.py:499-524) for a value
/// that is present: 3- or 6-digit hex passes, `transparent`/`none` (any case)
/// is `transparent`, and anything else is not a color.
pub(crate) fn sanitized_color(color: &str) -> Option<String> {
    let hex = color
        .strip_prefix('#')
        .is_some_and(|d| matches!(d.len(), 3 | 6) && d.chars().all(|c| c.is_ascii_hexdigit()));
    if hex {
        Some(color.to_string())
    } else if matches!(color.to_ascii_lowercase().as_str(), "transparent" | "none") {
        Some("transparent".to_string())
    } else {
        None
    }
}

/// A column's static `font.color` and `font.weight` for one row: each resolves column-ID-first like `background`. The color is `None`
/// when unauthored or unusable (the caller keeps the body color — the branch is
/// taken either way, so the link color never shows through); the weight is
/// `None` unless it resolves to a valid CSS weight.
pub(super) fn column_font_lanes(
    col: &ColMeta,
    row: &serde_json::Map<String, Value>,
) -> (Option<String>, Option<String>) {
    let Some(font) = &col.cfg.font else {
        return (None, None);
    };
    let color = font
        .color
        .as_deref()
        .filter(|c| !c.is_empty())
        .and_then(|spec| resolve_style_input(spec, row))
        .and_then(|raw| sanitized_color(&raw));
    let weight = font
        .weight
        .as_ref()
        .filter(|w| !matches!(w, Value::Null) && !matches!(w, Value::String(s) if s.is_empty()))
        .and_then(|w| resolve_style_input(&font_weight_as_css(w), row))
        .filter(|w| VALID_FONT_WEIGHTS.contains(&w.as_str()));
    (color, weight)
}

/// `resolve_cell_conditional_styles` + the outer `overrides` call
/// The scale layer (skipped for a summary/total row,
/// `null_color` on a null value, the interpolated color on a numeric one)
/// supplies the base `background`; a matching when-rule then overrides
/// `background`/`color`/`weight`/`style`/`decoration` on top, and supplies
/// `glyph`/`glyph_color` unconditionally (production's `resolve_cell_glyph_
/// from_overrides` always reads the WITH-tones `overrides`, never the
/// scale-path's own tones-less recomputation — see this file's module doc
/// for why one call covers both). One evaluation path, so both paint sites
/// (the numeric lane and the text cell) and this file's two glyph-only
/// measurement sites (via `resolve_cell_glyph`, tones-free) agree.
pub(super) fn resolve_cell_style(
    col: &ColMeta,
    row: &serde_json::Map<String, Value>,
    value: Option<&Value>,
    scale_rows: &[&serde_json::Map<String, Value>],
    row_role: RowRole,
    tones: Option<&TonesStyle>,
) -> Result<RuleOverrides, String> {
    // With a scale the column's static fill and ink are read unresolved
    // (`resolve_cell_conditional_styles`): a field reference is no color there.
    // Without one the fill resolves per row and the ink is `column_font_lanes`'.
    let scale = col.cfg.scale.as_ref();
    let mut background: Option<String> =
        col.cfg.background.as_deref().and_then(|spec| match scale {
            Some(_) => sanitized_color(spec),
            None => resolve_style_input(spec, row).and_then(|raw| sanitized_color(&raw)),
        });
    let mut scaled_color: Option<String> = scale
        .and_then(|_| col.cfg.font.as_ref()?.color.as_deref())
        .and_then(sanitized_color);
    if let (Some(scale), false) = (scale, is_summary_role(row_role)) {
        for (target, slot) in [
            (scale.background.as_ref(), &mut background),
            (scale.color.as_ref(), &mut scaled_color),
        ] {
            let Some(target) = target else { continue };
            match value {
                None => {
                    if target.null_color.is_some() {
                        *slot = target.null_color.clone();
                    }
                }
                Some(v) => {
                    if let Some(num) = coerce_numeric_cell(Some(v)) {
                        let raw = compute_scale_domain(
                            scale_rows,
                            col.name.as_str(),
                            target.min,
                            target.max,
                        );
                        let (lo, hi) = nice_domain(target.min, target.max, target.nice.unwrap_or(false), raw);
                        let palette = resolve_palette_stops(target)?;
                        let col_format = col
                            .cfg
                            .format
                            .as_ref()
                            .map(|f| f.spec.as_str())
                            .map(|f| crate::common::predefined_number_spec(f).unwrap_or(f));
                        let hinge = resolve_hinge(target.hinge.as_ref(), raw.0, raw.1, col_format);
                        *slot = Some(interpolate_scale_color(
                            num,
                            lo,
                            hi,
                            &palette,
                            hinge,
                            &target.arm_mode,
                        )?);
                    }
                }
            }
        }
    }

    let overrides = resolve_conditional_styles(&col.when_rules, value, tones)
        .map_err(|e| format!("table: {e}"))?;
    // The one caller that actually paints `glyph_color` — a matched tone
    // rule that needed board tones and had none is a genuinely malformed
    // payload here (production's own `tones` is a required board field,
    // never structurally absent at a real render), unlike a glyph-only
    // caller (`resolve_cell_glyph`), which never even looks at this flag.
    if overrides.tone_needs_board_tones {
        return Err("table: conditional rule tone with no board tones".into());
    }
    if overrides.background.is_some() {
        background = overrides.background;
    }
    let (glyph, glyph_color) = if overrides.glyph.is_some() {
        (overrides.glyph, overrides.glyph_color)
    } else {
        (col.cfg.glyph.clone(), col.cfg.glyph_color.clone())
    };

    Ok(RuleOverrides {
        background,
        color: overrides.color.or(scaled_color),
        weight: overrides.weight,
        style: overrides.style,
        decoration: overrides.decoration,
        glyph,
        glyph_color,
        tone_needs_board_tones: false,
    })
}

// ── Per-cell links ─────────────────────────────────────────────────────────────

/// `\w+` per Python's `re` on a `str` pattern: `str.isalnum()` or `_` per
/// character, over the interpreter's Unicode data.
pub(super) fn is_word(s: &str) -> bool {
    !s.is_empty() && s.chars().all(is_py_word)
}

/// Parses the text between `{{`/`}}` against `_TEMPLATE_RE`'s inner grammar
/// (`\s*(\w+)(?:\s*\|\s*(\w+))?\s*`) — `None` when the
/// whole span doesn't match, exactly like a regex non-match: the `{{ }}` is
/// then left as literal text rather than treated as a placeholder.
pub(super) fn parse_template_inner(inner: &str) -> Option<(String, Option<String>)> {
    let trimmed = inner.trim_matches(is_py_space);
    match trimmed.split_once('|') {
        Some((col, filt)) => {
            let (col, filt) = (
                col.trim_matches(is_py_space),
                filt.trim_matches(is_py_space),
            );
            (is_word(col) && is_word(filt)).then(|| (col.to_string(), Some(filt.to_string())))
        }
        None => is_word(trimmed).then(|| (trimmed.to_string(), None)),
    }
}

/// Python's `urllib.parse.quote_plus` for a UTF-8 string with the default
/// (empty) `safe` set: RFC 3986 unreserved bytes (`A-Za-z0-9_.-~`) pass
/// through, a space becomes `+`, everything else percent-encodes per byte.
pub(super) fn urlencode_plus(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => {
                out.push(b as char)
            }
            b' ' => out.push('+'),
            _ => {
                let _ = write!(out, "%{b:02X}");
            }
        }
    }
    out
}

/// An explicit
/// `link:` string that names a data column verbatim substitutes that
/// column's own value for THIS row (`None`/absent → no link). Otherwise
/// every `{{ col }}` / `{{ col | urlencode }}` placeholder substitutes
/// `row[col]`'s string form — `urlencode` percent-encodes it
/// (`urlencode_plus`, matching `quote_plus`), any other filter name is an
/// `Err` (never a silent pass-through), and a `None` placeholder anywhere
/// makes the WHOLE row's link `None` (production's `has_none` short-
/// circuit). A template-free string with no `{{ }}` match anywhere is the
/// href verbatim, same as production's `_TEMPLATE_RE.search()` miss. An
/// all-templates-resolved-to-empty-string result is also `None`
/// (`resolved or None`) — mirrored here as
/// `out.is_empty()`.
pub(super) fn resolve_cell_link(
    link: &str,
    row: &serde_json::Map<String, Value>,
    all_data_columns: &[String],
) -> Result<Option<String>, Diagnostic> {
    if all_data_columns.iter().any(|c| c == link) {
        // `str(val)` is returned as-is, but the caller's `if cell_link:`
        // drops an empty string — so an empty column value is no link.
        return Ok(row
            .get(link)
            .filter(|v| !v.is_null())
            .map(py_str)
            .filter(|s| !s.is_empty()));
    }

    let mut out = String::new();
    let mut has_template = false;
    let mut has_none = false;
    let mut i = 0usize;
    while i < link.len() {
        if let Some(rest) = link[i..].strip_prefix("{{") {
            if let Some(close_rel) = rest.find("}}") {
                let inner = &rest[..close_rel];
                if let Some((col_name, filter_name)) = parse_template_inner(inner) {
                    has_template = true;
                    match row.get(col_name.as_str()) {
                        None | Some(Value::Null) => has_none = true,
                        Some(v) => {
                            let raw = py_str(v);
                            match filter_name.as_deref() {
                                None => out.push_str(&raw),
                                Some("urlencode") => out.push_str(&urlencode_plus(&raw)),
                                Some(other) => {
                                    return Err(Diagnostic::new(
                                        &codes::ERR_TABLE_LINK_FILTER_UNKNOWN,
                                        [("filter_name", json!(other))],
                                    ));
                                }
                            }
                        }
                    }
                    i += 2 + close_rel + 2;
                    continue;
                }
            }
        }
        let ch = link[i..].chars().next().expect("i < link.len()");
        out.push(ch);
        i += ch.len_utf8();
    }

    if !has_template {
        return Ok(Some(link.to_string()));
    }
    if has_none {
        return Ok(None);
    }
    Ok((!out.is_empty()).then_some(out))
}

/// `fractional_digit_count` (`numeral_scale.py:431`): the digits after the
/// active locale's decimal mark. Only digits count, so a trailing non-digit
/// (an accounting `)`) does not corrupt the depth.
pub(crate) fn count_decimals(s: &str) -> usize {
    d3rs::numeral_pad::fractional_digit_count(
        s,
        &crate::number_locale::active_number_locale().locale.decimal,
    )
}

/// Select the trailing decimal-alignment pad for `num_str` from a pre-built
/// `pad_table` — production's `decimal_pad_for` (numeral_scale.py:434-455).
/// `precision = pad_table.len() - 2`; a whole number (no fractional digits)
/// takes the `precision + 1` slot, a value with `frac` fractional digits
/// takes `precision - frac`. An empty `pad_table` means the column bakes no
/// alignment at all — every call site can call this unconditionally, the
/// Python caller's own `if pad_table` guard folded in here. A value deeper
/// than the table was built for is `Err`, named, matching production's
/// `IndexError`.
pub(crate) fn decimal_pad_for<'a>(
    pad_table: &'a [String],
    num_str: &str,
) -> Result<&'a str, String> {
    if pad_table.is_empty() {
        return Ok("");
    }
    // `build_decimal_pad_table` always emits precision + 2 entries; a
    // one-entry table names no precision at all.
    let precision = pad_table.len().checked_sub(2).ok_or_else(|| {
        format!(
            "decimal_pad_for: pad_table with {} entry names no precision",
            pad_table.len()
        )
    })?;
    let frac = count_decimals(num_str);
    if frac > precision {
        return Err(format!(
            "decimal_pad_for: pad_table precision {precision} < value {num_str:?}'s {frac} fractional digits"
        ));
    }
    let idx = if frac > 0 {
        precision - frac
    } else {
        precision + 1
    };
    Ok(pad_table[idx].as_str())
}

pub(super) fn is_summary_role(role: RowRole) -> bool {
    matches!(role, RowRole::Summary | RowRole::Total)
}

/// A JSON number or numeric string
/// coerces to `f64`, anything else (including `null`) is not numeric. JSON
/// cannot encode NaN/±Infinity, so the `Value::Number` arm's own guard has
/// no reachable input — every `Value::Number` this parses is finite. The
/// string arm can still parse to a non-finite value (`"inf"`, `"1e999"`),
/// which production's own guard (`math.isfinite(result)`) rejects too.
pub(crate) fn coerce_numeric_cell(value: Option<&Value>) -> Option<f64> {
    match value? {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse().ok().filter(|v: &f64| v.is_finite()),
        _ => None,
    }
}

pub(super) fn resolve_row_roles(
    rows: &[serde_json::Map<String, Value>],
    row_role_col: Option<&str>,
) -> Vec<RowRole> {
    rows.iter()
        .map(|row| {
            let Some(col) = row_role_col else {
                return RowRole::Value;
            };
            // Column-ID-first (`resolve_table_style_value`): a name that is not a
            // column of the row is the literal role every row takes.
            let role_text = match row.get(col) {
                Some(Value::String(s)) => s.as_str(),
                Some(_) => return RowRole::Value,
                None => col,
            };
            match role_text.trim().to_lowercase().as_str() {
                "summary" => RowRole::Summary,
                "total" => RowRole::Total,
                _ => RowRole::Value,
            }
        })
        .collect()
}

/// A suffix token that carries the number's magnitude rather than a unit like
/// "%": production's `magnitude_suffixes()` (`compile/format_value.py:104`)
/// gates `suffix_gap` on it. A magnitude suffix sits 3px off the number, a
/// unit suffix touches it directly. The set is d3's raw SI letters and both
/// registers' words of the ACTIVE locale, bare; `m` (d3 milli) is a distinct,
/// non-magnitude prefix production gives a 0px gap.
pub(crate) fn is_magnitude_suffix(s: &str) -> bool {
    let bare = s.trim();
    crate::number_locale::active_number_locale()
        .magnitude_suffixes()
        .any(|suffix| suffix == bare)
}

/// The string production measures a numeric/date cell by — one rule, used at
/// both of its measurement sites (`measure_column_demands` and
/// `_has_overflow`): `format_table_cell_value`'s DISPLAY string, in which a
/// magnitude word sits behind the gap it carries (`text/magnitude_words.py`:
/// a no-break space or none) though the lanes paint the two apart. A
/// shared-scale column measures its scaled digit string
/// (`prefix + number + suffix`), since that, not the whole-precision SI
/// string, is what its cells paint.
/// Affixes are the UNGATED ones: production formats every sampled cell as its
/// own anchor, so `symbol_mode: anchors` never narrows what is measured.
pub(super) fn measured_cell_text(cell: &Cell) -> String {
    compose_measured_text(cell, &cell.text)
}

/// `measured_cell_text` without the decimal pad: the fit cascade measures the
/// text `format_table_cell_value` returns, not the padded lane text the demand
/// and floor passes read (`_has_overflow` vs `_rendered_cell_text`).
pub(super) fn unpadded_cell_text(cell: &Cell) -> String {
    let number = cell
        .text
        .strip_suffix(cell.pad.as_str())
        .expect("pad is the tail of text");
    match &cell.fit_affixes {
        Some((prefix, suffix)) => format!("{prefix}{number}{suffix}"),
        None => compose_measured_text(cell, number),
    }
}

pub(super) fn compose_measured_text(cell: &Cell, number: &str) -> String {
    let prefix = cell.full_prefix.as_deref().unwrap_or("");
    let suffix = cell.full_suffix.as_deref().unwrap_or("");
    format!("{prefix}{number}{suffix}")
}

/// The d3 type letter of `spec` when it has one, `None` when the spec does not
/// parse (the formatter reports that itself).
pub(super) fn percent_type(spec: &str) -> Option<char> {
    d3rs::format::parse_parts(spec).ok().map(|p| p.type_)
}

/// A non-anchor row of a `symbol_mode: anchors` column keeps no prefix, and a
/// suffix only when it opens with a magnitude word (`table.py:2657-2663`): the
/// authored suffix is peeled off first, since it is never a magnitude
/// ("EUR" starts with E).
pub(super) fn non_anchor_suffix(suffix: String, explicit_suffix: &str) -> Option<String> {
    let computed = suffix
        .strip_suffix(py_rstrip(explicit_suffix))
        .unwrap_or(&suffix);
    let magnitude = crate::number_locale::active_number_locale()
        .magnitude_suffixes()
        .any(|word| computed.starts_with(word));
    (magnitude && !suffix.is_empty()).then_some(suffix)
}

/// A refusal raised under a numeric column names the table; a registered
/// code keeps its own params.
pub(super) fn in_table(refusal: Diagnostic) -> Diagnostic {
    if refusal.code == codes::ERR_ENGINE_REFUSED.id {
        return Diagnostic::refused(format!("table: {refusal}"));
    }
    refusal
}

/// A numeric column's cells: magnitudes through the column's formatter, every
/// other non-null cell (a sentinel such as "N/A", a date) as text
/// (`format_table_cell_value`'s non-numeric arm).
pub(super) fn format_numeric_col(
    rows: &[serde_json::Map<String, Value>],
    col: &ColMeta,
    symbol_mode: &str,
    row_roles: &[RowRole],
) -> Result<Vec<Cell>, Diagnostic> {
    let mut cells = format_magnitudes(rows, col, symbol_mode, row_roles)?;
    for (cell, row) in cells.iter_mut().zip(rows) {
        let raw = row.get(col.name.as_str());
        if numeric_value(raw).is_some() || raw.is_none_or(Value::is_null) {
            continue;
        }
        let text = match raw {
            Some(Value::String(s)) if s.trim().is_empty() => continue,
            Some(Value::String(s)) => format_temporal_cell(col, s)?.unwrap_or_else(|| s.clone()),
            Some(other) => py_str(other),
            None => continue,
        };
        *cell = Cell {
            text,
            ..Default::default()
        };
    }
    Ok(cells)
}

pub(super) fn format_magnitudes(
    rows: &[serde_json::Map<String, Value>],
    col: &ColMeta,
    symbol_mode: &str,
    row_roles: &[RowRole],
) -> Result<Vec<Cell>, Diagnostic> {
    let n = rows.len();
    // Collect raw f64 values (None = null/missing).
    let raw: Vec<Option<f64>> = rows
        .iter()
        .map(|r| numeric_value(r.get(col.name.as_str())))
        .collect();
    // A column with no format prints the engine's `number`, which the board may
    // have redefined.
    let number_alias = match col.cfg.format {
        None => board_alias_format("number")?,
        Some(_) => None,
    };
    let format_cfg = col.cfg.format.as_ref().or(number_alias.as_ref());

    // A column with no `format:` takes the theme's `number` (SI) format
    // (`format_kpi_parts(..., default_number=True)`). `raw` names the
    // predefined format whose house rules and sub-unit fallback the spec alone
    // cannot recover; a bare predefined name in `spec` resolves to it.
    let fmt_name = format_cfg.map_or("number", |fmt| {
        fmt.raw
            .as_deref()
            .filter(|raw| crate::common::predefined_number_spec(raw).is_some())
            .unwrap_or(&fmt.spec)
    });
    let (explicit_prefix, explicit_suffix) =
        format_cfg.map_or(("", ""), |fmt| (fmt.prefix.as_str(), fmt.suffix.as_str()));
    let parts = FormatParts {
        spec: Some(crate::common::predefined_number_spec(fmt_name).unwrap_or(fmt_name)),
        raw: Some(fmt_name),
        prefix: explicit_prefix,
        suffix: explicit_suffix,
        notation: format_cfg.and_then(|fmt| fmt.notation.as_deref()),
        ..FormatParts::default()
    };
    let shared = col.cfg.shared_scale.as_ref();
    // A left-anchored column has no decimal lane to pad toward.
    let pad_table: &[String] = if col.is_numeric && col.cfg.align.as_deref() == Some("left") {
        &[]
    } else {
        &col.cfg.decimal_pad_table
    };
    let sub_unit_specs = board_sub_unit_specs();

    // symbol_mode == "anchors" limits a numeric cell's currency prefix and
    // unit suffix to the first row plus any summary/total row — mirrors
    // table.py's `is_anchor_row`/`symbol_mode == "anchors"` gate. A magnitude
    // suffix carries the number's value, so it stays on every row. Under
    // "all" (the default) every row is its own anchor.
    let is_symbol_anchor =
        |i: usize| -> bool { symbol_mode != "anchors" || i == 0 || is_summary_role(row_roles[i]) };

    // A shared scale's anchor row is the first one that can carry the
    // magnitude word (table.py:1981-1988): a zero never does.
    let magnitude_anchor_row = raw
        .iter()
        .position(|v| v.is_some_and(|x| x != 0.0))
        .unwrap_or(0);
    let scaled = |is_anchor: bool| {
        shared.map(|ss| ScaledColumn {
            exponent: ss.exponent,
            digit_spec: ss.digit_spec.as_str(),
            repeat: ss.mode == SuffixMode::Repeat,
            is_anchor,
        })
    };

    (0..n)
        .map(|i| {
            let Some(v) = raw[i] else {
                return Ok(Cell {
                    text: NULL_DISPLAY.to_string(),
                    is_null: true,
                    ..Default::default()
                });
            };
            let cell = match rows[i].get(col.name.as_str()) {
                Some(Value::Number(n)) => Some(n),
                _ => None,
            };
            // Production paints `$` as its own lane but MEASURES the cell
            // through d3 with the `$` still in the spec, where it displaces a
            // `%`/`p` suffix; one Cell cannot carry both strings, so that
            // pair refuses instead of painting either.
            let spec = parts.spec.expect("every numeric column has a spec");
            if spec.contains('$') && matches!(percent_type(spec), Some('%' | 'p')) {
                return Err(Diagnostic::refused(format!(
                    "table: format {spec:?}: `$` with a `%` or `p` type is not supported"
                )));
            }
            let lanes = format_kpi_parts(
                v,
                &parts,
                &PartsOptions {
                    keep_affix_gaps: true,
                    anchored: symbol_mode == "anchors",
                    shared_scale: scaled(
                        symbol_mode != "anchors"
                            || is_summary_role(row_roles[i])
                            || i == magnitude_anchor_row,
                    ),
                    sub_unit_specs: sub_unit_specs.as_ref(),
                    cell,
                    ..PartsOptions::default()
                },
            )
            .map_err(in_table)?;
            // The decimal pad aligns the number lane on the locale's mark.
            let pad = decimal_pad_for(pad_table, &lanes.number)?;
            let (prefix, suffix) = if is_symbol_anchor(i) {
                (
                    (!lanes.prefix.is_empty()).then_some(lanes.prefix),
                    (!lanes.suffix.is_empty()).then_some(lanes.suffix),
                )
            } else {
                (None, non_anchor_suffix(lanes.suffix, explicit_suffix))
            };
            // Width measurement reads the cell as every row's own anchor, in
            // the locale's own currency placement: a shared-scale cell as its
            // lanes (`_rendered_cell_text`), any other as `format_value`
            // wraps it, authored whitespace included.
            let measured = format_kpi_parts(
                v,
                &parts,
                &PartsOptions {
                    keep_affix_gaps: true,
                    shared_scale: scaled(true),
                    sub_unit_specs: sub_unit_specs.as_ref(),
                    cell,
                    ..PartsOptions::default()
                },
            )
            .map_err(in_table)?;
            let (full_prefix, full_suffix) = if shared.is_some() {
                (measured.prefix, measured.suffix)
            } else {
                (measured.measured_prefix, measured.measured_suffix)
            };
            let fit_affixes = match shared {
                Some(_) => {
                    let fit = format_kpi_parts(
                        v,
                        &parts,
                        &PartsOptions {
                            keep_affix_gaps: true,
                            anchored: symbol_mode == "anchors",
                            shared_scale: scaled(true),
                            cell,
                            ..PartsOptions::default()
                        },
                    )
                    .map_err(in_table)?;
                    Some((fit.prefix, fit.suffix))
                }
                None => None,
            };
            Ok(Cell {
                text: format!("{}{pad}", lanes.number),
                pad: pad.to_string(),
                prefix,
                suffix,
                full_prefix: (!full_prefix.is_empty()).then_some(full_prefix),
                full_suffix: (!full_suffix.is_empty()).then_some(full_suffix),
                fit_affixes,
                is_null: false,
            })
        })
        .collect()
}

/// The number a lane cell paints: a number, or a string `float()` reads that is
/// not date-shaped (`"2024"` is a date, never a magnitude).
pub(super) fn numeric_value(raw: Option<&Value>) -> Option<f64> {
    match raw {
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::String(s)) if !is_date_like_str(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// A number, any cell of a column with an
/// authored `format:` (a null or blank one included), or a string `float()`
/// reads.
pub(super) fn is_numeric_cell(col: &ColMeta, raw: Option<&Value>) -> bool {
    match raw {
        Some(Value::Bool(_)) => false,
        Some(Value::Number(_)) => true,
        _ if col.cfg.format.is_some() => true,
        Some(Value::String(s)) => s.trim().parse::<f64>().is_ok(),
        _ => false,
    }
}

/// The cells painted in the tabular
/// numeric stack.
pub(super) fn wants_tabular_font(col: &ColMeta, raw: Option<&Value>) -> bool {
    is_numeric_cell(col, raw) || is_date_like_cell(raw)
}

/// A string in a right-aligned date lane that is neither date-like nor
/// numeric, with no authored format (a timestamp, say): production paints it
/// through the text branch in the body font, end-anchored at the lane's
/// `number_x`, not as a lane cell.
pub(super) fn is_plain_date_lane_cell(col: &ColMeta, raw: Option<&Value>) -> bool {
    col.is_date
        && matches!(raw, Some(Value::String(s)) if !s.trim().is_empty())
        && !wants_tabular_font(col, raw)
}

/// The strftime spec a temporal cell paints through: the authored `format:`
/// (`date_short`/`time_short` are the engine's two predefined time specs,
/// `PREDEFINED_TIME_SPECS`), else `date_short`.
pub(super) fn temporal_spec(col: &ColMeta) -> Result<String, String> {
    let authored = col
        .cfg
        .format
        .as_ref()
        .map(|f| f.spec.as_str())
        .filter(|s| !s.is_empty());
    let name = authored.unwrap_or("date_short");
    let Some(engine_spec) = d3rs::format::predefined_time_spec(name) else {
        return Ok(name.to_string());
    };
    Ok(board_alias(name)?.unwrap_or_else(|| engine_spec.to_string()))
}

/// `format_table_cell_value`'s temporal path for a
/// string cell: an ISO date or timestamp paints through the strftime spec,
/// `Ok(None)` for any other string (verbatim, `str(value)`). A non-strftime
/// spec meeting a temporal value is production's
/// `ERR_TABLE_FORMAT_KIND_MISMATCH`; a directive `table_dates::strftime` does
/// not reach errs by name.
pub(super) fn format_temporal_cell(col: &ColMeta, s: &str) -> Result<Option<String>, Diagnostic> {
    let spec_owned = temporal_spec(col)?;
    let spec = spec_owned.as_str();
    let refuse = |why: String| {
        format!(
            "table: column '{}' format {spec:?}: {why}",
            col.name.as_str()
        )
    };
    let parsed = table_dates::parse_temporal(s.trim());
    if matches!(parsed, Ok(None)) {
        return Ok(None);
    }
    if !table_dates::is_time_format(spec) {
        return Err(format_kind_mismatch(
            spec,
            "use a strftime spec (e.g. '%-d %b %Y') or remove format: to use date_short",
        ));
    }
    let invalid = |reason: String| {
        Diagnostic::new(
            &codes::ERR_TABLE_TEMPORAL_FORMAT_INVALID,
            [
                ("cell", json!(s)),
                ("spec", json!(spec)),
                ("reason", json!(reason)),
            ],
        )
    };
    table_dates::validate_spec(spec).map_err(&invalid)?;
    let moment = match parsed {
        Ok(Some(moment)) => moment,
        Ok(None) => unreachable!("returned above"),
        Err(table_dates::TemporalError::Invalid(reason)) => return Err(invalid(reason)),
        Err(table_dates::TemporalError::Unported(why)) => return Err(refuse(why).into()),
    };
    let date = table_dates::strftime(moment, spec).map_err(refuse)?;
    // The format's own affixes ride around the date (`format_table_cell_value`).
    let affixes = col.cfg.format.as_ref();
    let prefix = affixes.map(|f| f.prefix.as_str()).unwrap_or("");
    let suffix = affixes.map(|f| f.suffix.as_str()).unwrap_or("");
    Ok(Some(format!("{prefix}{date}{suffix}")))
}

pub(super) fn format_kind_mismatch(fmt: &str, remedy: &str) -> Diagnostic {
    Diagnostic::new(
        &codes::ERR_TABLE_FORMAT_KIND_MISMATCH,
        [("fmt", json!(fmt)), ("remedy", json!(remedy))],
    )
}

/// Format a date-lane column (invariant #11: right-aligned + date-like
/// content). Every temporal string paints through `format_temporal_cell`; every
/// other string (US/short/quarter/year-month/year shapes, and an impossible
/// calendar date) falls through verbatim. A shape production would format that
/// the port does not reach (a number under an authored format) errs by name.
pub(super) fn format_date_col(
    rows: &[serde_json::Map<String, Value>],
    col: &ColMeta,
) -> Result<Vec<Cell>, Diagnostic> {
    let authored = col.cfg.format.as_ref();
    let spec_owned = temporal_spec(col)?;
    let spec = spec_owned.as_str();
    let refuse = |why: String| {
        format!(
            "table: date column '{}' format {spec:?}: {why}",
            col.name.as_str()
        )
    };
    rows.iter()
        .map(|row| -> Result<Cell, Diagnostic> {
            match row.get(col.name.as_str()) {
                Some(Value::String(s)) if !s.trim().is_empty() => {
                    let text = match format_temporal_cell(col, s)? {
                        // A timestamp is not `is_date_like`, so under an authored
                        // format it takes the numeric lane's paint path
                        // Which this lane never draws.
                        Some(_) if authored.is_some() && !is_date_like_str(s) => {
                            return Err(Diagnostic::from(refuse(format!(
                            "the timestamp {s:?} under an authored format paints on the numeric \
                             lane, which is not supported"
                        ))));
                        }
                        Some(text) => text,
                        None if authored.is_some() && s.trim().parse::<f64>().is_ok() => {
                            // A year-shaped string stays verbatim under a strftime
                            // spec; any other numeric-looking string would take the
                            // numeric formatter production picks by spec kind.
                            if !(table_dates::is_time_format(spec) && is_date_like_str(s)) {
                                return Err(Diagnostic::from(refuse(format!(
                                    "numeric cell {s:?} under an authored format is not supported"
                                ))));
                            }
                            s.clone()
                        }
                        None => s.clone(),
                    };
                    Ok(Cell {
                        text,
                        ..Default::default()
                    })
                }
                // Invariant #11: a number mixed into an otherwise date-shaped
                // lane (`is_date` fires when *any* cell is date-like) still
                // carries a real value — production renders it on the numeric
                // lane it establishes, not the null sentinel.
                Some(Value::Number(n)) => {
                    let v = n.as_f64().ok_or_else(|| {
                        format!(
                            "table: date column '{}' has non-finite number",
                            col.name.as_str()
                        )
                    })?;
                    if authored.is_some() {
                        return Err(Diagnostic::from(refuse(format!(
                            "numeric cell {v} under an authored format is not supported"
                        ))));
                    }
                    let lanes = format_numeric_auto(v)?;
                    Ok(Cell {
                        text: lanes.number,
                        suffix: (!lanes.suffix.is_empty()).then_some(lanes.suffix),
                        full_suffix: (!lanes.measured_suffix.is_empty())
                            .then_some(lanes.measured_suffix),
                        ..Default::default()
                    })
                }
                _ => Ok(Cell {
                    text: NULL_DISPLAY.to_string(),
                    is_null: true,
                    ..Default::default()
                }),
            }
        })
        .collect()
}

/// Port of production's `_DATE_RE` (core/utils.py:29): the seven
/// recognized date shapes. Pattern-based, not semantic — production's
/// regex accepts any `\d{2}/\d{2}/\d{4}`, impossible dates included.
pub(super) fn is_date_like_str(s: &str) -> bool {
    const MONTHS: [&str; 12] = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    let s = s.trim();
    let b = s.as_bytes();
    let all_digits = |x: &[u8]| !x.is_empty() && x.iter().all(u8::is_ascii_digit);
    // ^\d{4}$ and ^\d{4}-\d{2}(-\d{2})?$
    if all_digits(b) && b.len() == 4 {
        return true;
    }
    if b.len() >= 7 && all_digits(&b[..4]) && b[4] == b'-' && all_digits(&b[5..7]) {
        if b.len() == 7 {
            return true;
        }
        if b.len() == 10 && b[7] == b'-' && all_digits(&b[8..10]) {
            return true;
        }
    }
    // ^\d{2}/\d{2}/\d{4}$
    if b.len() == 10
        && all_digits(&b[..2])
        && b[2] == b'/'
        && all_digits(&b[3..5])
        && b[5] == b'/'
        && all_digits(&b[6..10])
    {
        return true;
    }
    // ^Q[1-4]\s+\d{4}$
    if let Some(rest) = s.strip_prefix('Q') {
        let mut it = rest.splitn(2, char::is_whitespace);
        if let (Some(q), Some(y)) = (it.next(), it.next()) {
            if matches!(q, "1" | "2" | "3" | "4")
                && all_digits(y.trim_start().as_bytes())
                && y.trim_start().len() == 4
            {
                return true;
            }
        }
    }
    // Month-name forms: "Mar 15", "Mar 15, 2024", "Mar 15 2024", "15 Mar 2024"
    let parts: Vec<&str> = s.split_whitespace().collect();
    let is_month = |t: &str| MONTHS.contains(&t);
    let is_day = |t: &str| {
        let t = t.trim_end_matches(',');
        (1..=2).contains(&t.len()) && all_digits(t.as_bytes())
    };
    let is_year = |t: &str| t.len() == 4 && all_digits(t.as_bytes());
    match parts.as_slice() {
        [m, d] if is_month(m) && is_day(d) && !d.ends_with(',') => true,
        [m, d, y] if is_month(m) && is_day(d) && is_year(y) => true,
        [d, m, y] if is_day(d) && !d.ends_with(',') && is_month(m) && is_year(y) => true,
        _ => false,
    }
}

/// The date-lane membership test: a string cell matching any recognized
/// date shape (production's `is_date_like`, string arm).
pub(crate) fn is_date_like_cell(value: Option<&Value>) -> bool {
    matches!(value, Some(Value::String(s)) if is_date_like_str(s))
}

/// The leaf
/// columns one source row fills — one per measure when there are several,
/// else the bare column tuple.
pub(super) fn pivot_leaf_keys<S: AsRef<str>>(col_tuple: &[String], values: &[S]) -> Vec<String> {
    let tuple = col_tuple.join(PIVOT_LEAF_SEP);
    if values.len() > 1 {
        values
            .iter()
            .map(|m| format!("{tuple}{PIVOT_LEAF_SEP}{}", m.as_ref()))
            .collect()
    } else {
        vec![tuple]
    }
}

/// Group-header descriptor rows above the leaf header row.
pub(super) fn build_pivot_levels(
    col_tuples: &[Vec<String>],
    n_col_dims: usize,
    n_measures: usize,
) -> Vec<Vec<PivotLevel>> {
    let n_col_tuples = col_tuples.len();
    let leaves_per_tuple = if n_measures > 1 { n_measures } else { 1 };
    let n_group_dim_levels = if n_measures > 1 {
        n_col_dims
    } else {
        n_col_dims - 1
    };

    let mut levels = Vec::with_capacity(n_group_dim_levels);
    for dim_idx in 0..n_group_dim_levels {
        let mut level: Vec<PivotLevel> = Vec::new();
        let mut i = 0;
        while i < n_col_tuples {
            let label = col_tuples[i][dim_idx].clone();
            let mut j = i;
            while j < n_col_tuples && col_tuples[j][dim_idx] == label {
                j += 1;
            }
            let first_leaf = i * leaves_per_tuple;
            let n_span_leaves = (j - i) * leaves_per_tuple;
            level.push((label, first_leaf, n_span_leaves));
            i = j;
        }
        levels.push(level);
    }
    levels
}

/// Whether a `style.columns` entry authors `visible: false`.
pub(super) fn is_hidden(cfg: &Value) -> bool {
    cfg.get("visible") == Some(&Value::Bool(false))
}

/// `_transpose_data_for_render`'s two input errors: a transposed table reads
/// exactly one row, and a `visible: false` entry must name one of its columns.
pub(super) fn check_transpose_input(
    columns: &serde_json::Map<String, Value>,
    rows: &[serde_json::Map<String, Value>],
) -> Result<(), Diagnostic> {
    if rows.len() != 1 {
        return Err(Diagnostic::new(
            &codes::ERR_TABLE_TRANSPOSE_ROW_COUNT,
            [("row_count", json!(rows.len()))],
        ));
    }
    let row = &rows[0];
    let unaddressed: Vec<&String> = columns
        .iter()
        .filter(|(key, cfg)| is_hidden(cfg) && !row.contains_key(*key))
        .map(|(key, _)| key)
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    if unaddressed.is_empty() {
        return Ok(());
    }
    let available: BTreeSet<&String> = row.keys().collect();
    Err(Diagnostic::new(
        &codes::ERR_TABLE_TRANSPOSE_VISIBLE_UNKNOWN_COLUMN,
        [
            ("unaddressed", json!(unaddressed)),
            ("available", json!(available)),
        ],
    ))
}

/// A transposed table: its one row becomes N `(label, value)` rows, each value
/// formatted as its source column would print it, drawn as a two-column table
/// headed by the board language's "Metric" and "Value" words (`_transpose_data_for_render`).
pub(super) fn transposed_inputs(
    resolved: &Value,
    rows: &[serde_json::Map<String, Value>],
) -> Result<(Value, Vec<serde_json::Map<String, Value>>), Diagnostic> {
    let mut flat = resolved.clone();
    flat["style"]["table"]["transpose"] = json!(false);
    let flat_resolved: Resolved =
        Deserialize::deserialize(&flat).map_err(|e| format!("table payload: {e}"))?;
    check_transpose_input(&flat_resolved.columns, rows)?;
    let (cols, ..) = visible_columns(&flat_resolved, rows, &None)?;
    let row_roles = resolve_row_roles(rows, flat_resolved.style.table.row.role.as_deref());
    let cell_data = build_cell_data(rows, &cols, "all", &row_roles)?;
    let pivoted = cols
        .iter()
        .zip(&cell_data)
        .map(|(col, cells)| {
            let label = match col.cfg.label.clone() {
                Some(label) => label,
                None => display_text::slug_to_text(col.name.as_str())?,
            };
            let text = unpadded_cell_text(&cells[0]);
            let mut row = serde_json::Map::new();
            row.insert("__metric__".into(), json!(label));
            row.insert("__value__".into(), json!(text));
            Ok(row)
        })
        .collect::<Result<Vec<_>, String>>()?;
    flat["columns"] = json!({
        "__metric__": {"label": display_text::language::tr("table.metric", None, &[])?, "width": "40%", "link_open": "page", "header_link_open": "page"},
        "__value__": {"label": display_text::language::tr("table.value", None, &[])?, "align": "right", "link_open": "page", "header_link_open": "page"},
    });
    flat["conditional_formatting"] = Value::Null;
    flat["spanners"] = Value::Null;
    Ok((flat, pivoted))
}

/// A `visible: false` entry must address a post-pivot column or a measure key
/// the pivot fanned out; anything else is a typo that would silently hide
/// nothing (`_prepare_columns`).
pub(super) fn check_visible_addressable(
    chart_id: &str,
    columns: &serde_json::Map<String, Value>,
    rows: &[serde_json::Map<String, Value>],
    row_role: Option<&str>,
    fanned_measures: &BTreeSet<&str>,
) -> Result<(), Diagnostic> {
    let Some(first) = rows.first() else {
        return Ok(());
    };
    let addressable: BTreeSet<&str> = first
        .keys()
        .map(String::as_str)
        .chain(fanned_measures.iter().copied())
        .filter(|k| Some(*k) != row_role)
        .collect();
    let style_inputs: BTreeSet<&str> = columns
        .values()
        .flat_map(|cfg| {
            [
                cfg.get("background"),
                cfg.get("font").and_then(|f| f.get("color")),
                cfg.get("font").and_then(|f| f.get("weight")),
            ]
        })
        .filter_map(|v| v.and_then(Value::as_str))
        .collect();
    let unaddressed: BTreeSet<&str> = columns
        .iter()
        .filter(|(key, cfg)| {
            is_hidden(cfg)
                && !addressable.contains(key.as_str())
                && !style_inputs.contains(key.as_str())
                && Some(key.as_str()) != row_role
        })
        .map(|(key, _)| key.as_str())
        .collect();
    if unaddressed.is_empty() {
        return Ok(());
    }
    let shown: BTreeSet<String> = addressable
        .iter()
        .map(|k| k.replace(PIVOT_LEAF_SEP, " / "))
        .collect();
    Err(Diagnostic::new(
        &codes::ERR_TABLE_VISIBLE_UNKNOWN_COLUMN,
        [
            ("chart_id", json!(chart_id)),
            ("unaddressed", json!(unaddressed)),
            ("addressable", json!(shown)),
        ],
    ))
}

/// The authored spanners as `(label, first_idx, n)`
/// over the visible columns. A spanner whose columns are all hidden is
/// dropped; an unknown or non-adjacent one is an error.
pub(super) fn authored_spanner_level(
    chart_id: &str,
    spanners: &[Spanner],
    queried: &[&str],
    visible: &[&str],
) -> Result<Vec<PivotLevel>, Diagnostic> {
    for spanner in spanners {
        let unknown: Vec<&String> = spanner
            .columns
            .iter()
            .filter(|c| !queried.contains(&c.as_str()))
            .collect();
        if !unknown.is_empty() {
            return Err(Diagnostic::new(
                &codes::ERR_TABLE_SPANNER_UNKNOWN_COLUMN,
                [
                    ("chart_id", json!(chart_id)),
                    ("label", json!(spanner.label)),
                    ("unknown", json!(unknown)),
                    ("columns", json!(queried)),
                ],
            ));
        }
    }
    let mut level = Vec::new();
    for spanner in spanners {
        let mut idxs: Vec<usize> = spanner
            .columns
            .iter()
            .filter_map(|c| visible.iter().position(|v| v == c))
            .collect();
        idxs.sort_unstable();
        let (Some(first), Some(last)) = (idxs.first(), idxs.last()) else {
            continue;
        };
        if last - first + 1 != idxs.len() {
            return Err(Diagnostic::new(
                &codes::ERR_TABLE_SPANNER_NOT_ADJACENT,
                [
                    ("chart_id", json!(chart_id)),
                    ("label", json!(spanner.label)),
                    ("spanner_columns", json!(spanner.columns)),
                    ("visible_columns", json!(visible)),
                ],
            ));
        }
        level.push((spanner.label.clone(), *first, idxs.len()));
    }
    Ok(level)
}

/// `_render_swatch_cell`'s `sanitize_color`: a hex color as written, or
/// `transparent`/`none` as `transparent`; `None` for anything else. The hex
/// pattern's `$` (colors.py:25) also matches before one final `\n`, which
/// stays in the returned color.
pub(super) fn swatch_color(value: &Value) -> Option<&str> {
    let s = value.as_str()?;
    let hex = s
        .strip_suffix('\n')
        .unwrap_or(s)
        .strip_prefix('#')
        .is_some_and(|h| matches!(h.len(), 3 | 6) && h.bytes().all(|b| b.is_ascii_hexdigit()));
    if hex {
        Some(s)
    } else {
        matches!(s.to_ascii_lowercase().as_str(), "transparent" | "none").then_some("transparent")
    }
}

pub(super) fn is_swatch(col: &ColMeta) -> bool {
    col.cfg.swatch == Some(true)
}

/// A typo'd `columns`/`values`/`rows`
/// field, an empty measure set, or a literal row role used where a column
/// belongs, raised before any reshape. With `values` omitted the measures are
/// `infer_pivot_measure_names`'s: every field `rows`, `columns` and the role
/// column leave unclaimed. Reshaping with them is refused by the caller.
pub(super) fn validate_pivot_fields(
    data: &[serde_json::Map<String, Value>],
    rows: &[String],
    columns: &[String],
    values: Option<&[String]>,
    row_role: Option<&str>,
) -> Result<(Vec<String>, Vec<String>), Diagnostic> {
    let first = data.first().ok_or("table: a pivot needs rows to reshape")?;
    if values.is_none() && rows.is_empty() {
        return Err(Diagnostic::new(
            &codes::ERR_TABLE_PIVOT_DIMENSIONS_AMBIGUOUS,
            [],
        ));
    }
    let observed: BTreeSet<&String> = first.keys().collect();
    let not_found = |field_path: &str, field: &str| {
        Diagnostic::new(
            &codes::ERR_TABLE_PIVOT_FIELD_NOT_FOUND,
            [
                ("channel", json!(field_path)),
                ("field", json!(field)),
                ("observed", json!(observed)),
            ],
        )
    };
    if let Some(field) = columns.iter().find(|c| !first.contains_key(*c)) {
        return Err(not_found("pivot.column", field));
    }
    if let Some(role) =
        row_role.filter(|r| !first.contains_key(*r) && matches!(*r, "value" | "summary" | "total"))
    {
        return Err(Diagnostic::new(
            &codes::ERR_TABLE_PIVOT_ROW_ROLE_LITERAL,
            [("row_role", json!(role)), ("observed", json!(observed))],
        ));
    }
    // `infer_pivot_measure_names`: explicit, else every query column that
    // `rows`, `columns` and the role marker do not claim.
    let claimed =
        |k: &String| rows.contains(k) || columns.contains(k) || Some(k.as_str()) == row_role;
    let effective_values: Vec<String> = match values {
        Some(v) => v.to_vec(),
        None => first.keys().filter(|k| !claimed(k)).cloned().collect(),
    };
    if let Some(field) = effective_values.iter().find(|v| !first.contains_key(*v)) {
        return Err(not_found("pivot.value", field));
    }
    if effective_values.is_empty() {
        return Err(Diagnostic::new(
            &codes::ERR_TABLE_PIVOT_NO_MEASURES,
            [("observed", json!(observed))],
        ));
    }
    let effective_rows: Vec<String> = if rows.is_empty() {
        first
            .keys()
            .filter(|k| {
                !columns.contains(k)
                    && !effective_values.contains(k)
                    && Some(k.as_str()) != row_role
            })
            .cloned()
            .collect()
    } else {
        if let Some(field) = rows.iter().find(|r| !first.contains_key(*r)) {
            return Err(not_found("pivot.row", field));
        }
        rows.to_vec()
    };
    Ok((effective_rows, effective_values))
}

/// A pivot dimension whose every non-null value is a
/// calendar instant sorts chronologically, one whose every non-null value is a
/// number sorts ascending, any other keeps query order (the author's lever for
/// business-ordered categories and a trailing "Total"). Nulls sort last in a
/// sorted dimension; an unsorted dimension beside a sorted one is keyed by
/// first appearance so outer groups stay contiguous.
pub(super) fn order_col_tuples(
    col_tuples: Vec<Vec<String>>,
    columns: &[String],
    data: &[serde_json::Map<String, Value>],
    tags: &[CellRow],
) -> Vec<Vec<String>> {
    let instant_of = |v: &Value| -> Option<(u32, u32, u32, u32, u32, u32, u32)> {
        let Value::String(s) = v else { return None };
        if let Ok(Some(moment)) = table_dates::parse_temporal(s) {
            return Some(moment.instant());
        }
        let (y, m, d) = crate::families::temporal::parse_bucket_string(s)?;
        Some((u32::try_from(y).ok()?, m, d, 0, 0, 0, 0))
    };
    let dim_orders: Vec<Option<DimOrder>> = columns
        .iter()
        .map(|col| {
            let mut non_null: Vec<(String, Value)> = Vec::new();
            for (i, row) in data.iter().enumerate() {
                let tag = tags.get(i).and_then(|t| t.get(col));
                let value = dim_value(row.get(col).unwrap_or(&Value::Null), tag);
                let text = dim_str(&value, tag);
                if !value.is_null() && !non_null.iter().any(|(t, _)| *t == text) {
                    non_null.push((text, value));
                }
            }
            if non_null.is_empty() {
                return None;
            }
            let instants: Vec<_> = non_null
                .iter()
                .filter_map(|(t, v)| instant_of(v).map(|i| (t.clone(), i)))
                .collect();
            if instants.len() == non_null.len() {
                return Some(DimOrder::Instant(instants.into_iter().collect()));
            }
            let numbers: Vec<_> = non_null
                .iter()
                .filter_map(|(t, v)| match v {
                    Value::Number(n) => n.as_f64().filter(|f| !f.is_nan()).map(|f| (t.clone(), f)),
                    _ => None,
                })
                .collect();
            (numbers.len() == non_null.len())
                .then(|| DimOrder::Number(numbers.into_iter().collect()))
        })
        .collect();
    if dim_orders.iter().all(Option::is_none) {
        return col_tuples;
    }
    let first_seen: Vec<std::collections::HashMap<String, usize>> = (0..columns.len())
        .map(|dim| {
            let mut seen = std::collections::HashMap::new();
            for tuple in &col_tuples {
                let next = seen.len();
                seen.entry(tuple[dim].clone()).or_insert(next);
            }
            seen
        })
        .collect();
    let compare = |a: &Vec<String>, b: &Vec<String>| -> std::cmp::Ordering {
        for (dim, order) in dim_orders.iter().enumerate() {
            let (x, y) = (a[dim].as_str(), b[dim].as_str());
            let ordering = match order {
                Some(DimOrder::Instant(keys)) => match (keys.get(x), keys.get(y)) {
                    (Some(p), Some(q)) => p.cmp(q),
                    (p, q) => p.is_none().cmp(&q.is_none()),
                },
                Some(DimOrder::Number(keys)) => match (keys.get(x), keys.get(y)) {
                    (Some(p), Some(q)) => p.partial_cmp(q).unwrap_or(std::cmp::Ordering::Equal),
                    (p, q) => p.is_none().cmp(&q.is_none()),
                },
                None => first_seen[dim][x].cmp(&first_seen[dim][y]),
            };
            if ordering != std::cmp::Ordering::Equal {
                return ordering;
            }
        }
        std::cmp::Ordering::Equal
    };
    let mut ordered = col_tuples;
    ordered.sort_by(compare);
    ordered
}

/// A pivot dimension value's label: the wire's `str()` text for a tagged cell (a
/// Decimal prints `2.50`, not `2.5`), else Python's `str()` of the cell.
pub(super) fn dim_str(cell: &Value, tag: Option<&CellTag>) -> String {
    tag.map_or_else(|| py_str(cell), |tag| tag.text.clone())
}

/// A pivot dimension value as a key and a sort input: a tagged non-finite value
/// is its text, which leaves the dimension unsortable; anything else is the cell.
pub(super) fn dim_value(cell: &Value, tag: Option<&CellTag>) -> Value {
    match tag {
        Some(tag) if tag.non_finite.is_some() => Value::String(tag.text.clone()),
        _ => cell.clone(),
    }
}

/// A list or object cell is unhashable: production's row-key lookup raises
/// `TypeError`, so it is refused by name.
pub(super) fn py_key(column: &str, cell: &Value) -> Result<PyKey, Diagnostic> {
    if matches!(cell, Value::Array(_) | Value::Object(_)) {
        return Err(format!(
            "table: pivot row dimension '{column}' holds a list or object cell, which Python cannot key a wide row by"
        )
        .into());
    }
    Ok(py_scalar_key(cell))
}

/// Reshape long-form rows into a wide pivot matrix.
/// `pivot_table_data`: leaf columns in each dimension's canonical order
/// (`order_col_tuples`).
pub(super) fn pivot_table_data(
    data: &[serde_json::Map<String, Value>],
    tags: &[CellRow],
    row_dims: &[String],
    columns: &[String],
    values: &[String],
    row_role: Option<&str>,
) -> Result<PivotResult, Diagnostic> {
    if data.is_empty() {
        return Err("table: pivot on empty data not supported".into());
    }
    // An absent cascading role column is harmless; only a present one rides
    // onto the wide rows.
    let role_col = row_role.filter(|r| data[0].contains_key(*r));
    let role_of = |row: &serde_json::Map<String, Value>| -> &'static str {
        match resolve_row_roles(std::slice::from_ref(row), role_col)[0] {
            RowRole::Value => "value",
            RowRole::Summary => "summary",
            RowRole::Total => "total",
        }
    };
    let row_repr = |entry: &serde_json::Map<String, Value>| {
        row_dims
            .iter()
            .map(|k| format!("{k}={}", py_repr(entry.get(k).unwrap_or(&Value::Null))))
            .collect::<Vec<_>>()
            .join(", ")
    };

    let n_measures = values.len();

    // Distinct col-tuples in first-seen order, then canonically ordered.
    let mut col_tuples: Vec<Vec<String>> = Vec::new();
    let mut seen: std::collections::HashSet<Vec<String>> = std::collections::HashSet::new();
    for (i, row) in data.iter().enumerate() {
        let tuple: Vec<String> = columns
            .iter()
            .map(|c| dim_str(row.get(c).unwrap_or(&Value::Null), tag_at(tags, i, c)))
            .collect();
        if seen.insert(tuple.clone()) {
            col_tuples.push(tuple);
        }
    }

    let col_tuples = order_col_tuples(col_tuples, columns, data, tags);
    let leaf_cols: Vec<String> = col_tuples
        .iter()
        .flat_map(|ct| pivot_leaf_keys(ct, values))
        .collect();

    let mut row_order: Vec<Vec<PyKey>> = Vec::new();
    let mut row_map: std::collections::HashMap<Vec<PyKey>, serde_json::Map<String, Value>> =
        std::collections::HashMap::new();
    let mut filled: std::collections::HashMap<Vec<PyKey>, std::collections::HashSet<String>> =
        std::collections::HashMap::new();
    for (i, row) in data.iter().enumerate() {
        let row_key = row_dims
            .iter()
            .map(|k| {
                py_key(
                    k,
                    &dim_value(row.get(k).unwrap_or(&Value::Null), tag_at(tags, i, k)),
                )
            })
            .collect::<Result<Vec<_>, _>>()?;
        if !row_map.contains_key(&row_key) {
            let mut m = serde_json::Map::new();
            for k in row_dims {
                m.insert(
                    k.clone(),
                    dim_value(row.get(k).unwrap_or(&Value::Null), tag_at(tags, i, k)),
                );
            }
            if let Some(role) = role_col {
                m.insert(
                    role.to_string(),
                    row.get(role).cloned().unwrap_or(Value::Null),
                );
            }
            for leaf in &leaf_cols {
                m.insert(leaf.clone(), Value::Null);
            }
            row_order.push(row_key.clone());
            row_map.insert(row_key.clone(), m);
        } else if role_col.is_some() {
            let stored_role = role_of(&row_map[&row_key]);
            let this_role = role_of(row);
            if this_role != stored_role {
                return Err(Diagnostic::new(
                    &codes::ERR_TABLE_PIVOT_ROW_ROLE_CONFLICT,
                    [
                        ("row", json!(row_repr(&row_map[&row_key]))),
                        ("stored_role", json!(stored_role)),
                        ("this_role", json!(this_role)),
                    ],
                ));
            }
        }
        let col_tuple: Vec<String> = columns
            .iter()
            .map(|c| dim_str(row.get(c).unwrap_or(&Value::Null), tag_at(tags, i, c)))
            .collect();
        let entry = row_map.get_mut(&row_key).expect("row_key just inserted");
        let row_filled = filled.entry(row_key).or_default();
        // A single measure fills one leaf per row; `zip` pairs it with `values[0]`.
        for (leaf, m) in pivot_leaf_keys(&col_tuple, values).into_iter().zip(values) {
            if !row_filled.insert(leaf.clone()) {
                let tuple_repr = match col_tuple.as_slice() {
                    [one] => format!("({},)", py_repr(&json!(one))),
                    many => format!(
                        "({})",
                        many.iter()
                            .map(|c| py_repr(&json!(c)))
                            .collect::<Vec<_>>()
                            .join(", ")
                    ),
                };
                return Err(Diagnostic::new(
                    &codes::ERR_TABLE_PIVOT_DUPLICATE_CELL,
                    [
                        ("row", json!(row_repr(entry))),
                        ("pivot_column", json!(tuple_repr)),
                        ("measure", json!(m)),
                    ],
                ));
            }
            entry.insert(leaf, row.get(m).cloned().unwrap_or(Value::Null));
        }
    }
    let wide_rows: Vec<_> = row_order
        .into_iter()
        .map(|k| row_map.remove(&k).expect("row key was inserted above"))
        .collect();

    let single_dim = columns.len() == 1;
    let levels = if single_dim && n_measures == 1 {
        Vec::new()
    } else {
        build_pivot_levels(&col_tuples, columns.len(), n_measures)
    };

    let mut col_order = row_dims.to_vec();
    col_order.extend(leaf_cols);

    Ok(PivotResult {
        rows: wide_rows,
        n_row_dims: row_dims.len(),
        col_order,
        levels,
        single_measure: if n_measures == 1 {
            Some(values[0].clone())
        } else {
            None
        },
        values: values.to_vec(),
    })
}

/// The cell's series as a `line` spark reads it:
/// `None` is an empty series, a list is itself, anything else is one value.
pub(super) fn spark_series(value: &Value) -> &[Value] {
    match value {
        Value::Null => &[],
        Value::Array(items) => items,
        single => std::slice::from_ref(single),
    }
}

/// `SparkConfig.thresholds` as `_get_threshold_color` walks it: ascending by
/// numeric key. Keys reach the payload as JSON object keys (strings).
pub(super) fn parse_spark_thresholds(
    column: &str,
    thresholds: &Value,
) -> Result<Vec<(f64, String)>, String> {
    let Value::Object(map) = thresholds else {
        return Err(format!(
            "table: spark thresholds on column '{column}' is not a mapping"
        ));
    };
    let mut parsed: Vec<(f64, String)> = map
        .iter()
        .map(|(key, color)| {
            let at = key.parse::<f64>().map_err(|_| {
                format!("table: spark thresholds on column '{column}' has non-numeric key '{key}'")
            })?;
            let Value::String(color) = color else {
                return Err(format!(
                    "table: spark thresholds on column '{column}' key '{key}' is not a color string"
                ));
            };
            Ok((at, color.clone()))
        })
        .collect::<Result<_, String>>()?;
    parsed.sort_by(|a, b| a.0.total_cmp(&b.0));
    Ok(parsed)
}

/// Start at the theme bar color and
/// take each threshold the value reaches (`value >= threshold`) in ascending
/// key order, so the highest reached wins.
pub(super) fn spark_threshold_color<'a>(
    value: f64,
    thresholds: &'a [(f64, String)],
    default: &'a str,
) -> &'a str {
    thresholds
        .iter()
        .filter(|(at, _)| value >= *at)
        .last()
        .map_or(default, |(_, c)| c)
}

// ── Main render ───────────────────────────────────────────────────────────────

/// Format every column's cells over `rows`, called with
/// whatever row slice its caller passes. A numeric column's `symbol_mode:
/// anchors` prefix/suffix (`format_numeric_col`'s `is_symbol_anchor`) reads
/// `i == 0` relative to THIS slice — production's own `row_idx == 0`/
/// `shared_scale_anchor_row` are computed the same way, over whichever
/// `rows` `_render_data_rows`'s caller handed it: the caller decides whether that is the whole table
/// or one page. `render()`'s single global call feeds MEASUREMENT (which
/// reads only the anchor-independent `.full_prefix`/`.full_suffix`) and the
/// non-paginated paint path; the paginated `static_multi_page` loop below
/// calls this AGAIN per page, over that page's own row slice, so each
/// page's own row 0 anchors independently — matching production's
/// `page_visible_data`/`_paint_data_rows` call, never the whole dataset's.
pub(super) fn build_cell_data(
    rows: &[serde_json::Map<String, Value>],
    cols: &[ColMeta],
    symbol_mode: &str,
    row_roles: &[RowRole],
) -> Result<Vec<Vec<Cell>>, Diagnostic> {
    cols.iter()
        .map(|col| {
            if col.is_numeric {
                if let Some(spec) = col.cfg.format.as_ref().map(|f| f.spec.as_str()) {
                    if table_dates::is_time_format(spec) {
                        return Err(format_kind_mismatch(
                            spec,
                            "use a d3-format numeric spec (e.g. '.1f') or remove format: to use \
                             the theme default",
                        ));
                    }
                }
                Ok(format_numeric_col(
                    rows,
                    col,
                    column_symbol_mode(col.cfg.format.as_ref(), symbol_mode),
                    row_roles,
                )?)
            } else if col.is_date {
                format_date_col(rows, col)
            } else {
                // Text-column cells go through production's
                // format_table_cell_value too: a None or whitespace-only
                // string renders the em-dash, never a blank cell.
                rows.iter()
                    .map(|row| {
                        Ok(match row.get(col.name.as_str()) {
                            Some(Value::String(v)) if !v.trim().is_empty() => {
                                if let Some(text) = format_temporal_cell(col, v)? {
                                    return Ok(Cell {
                                        text,
                                        ..Default::default()
                                    });
                                }
                                Cell {
                                    text: v.clone(),
                                    ..Default::default()
                                }
                            }
                            Some(Value::Null) | None | Some(Value::String(_)) => Cell {
                                text: NULL_DISPLAY.to_string(),
                                is_null: true,
                                ..Default::default()
                            },
                            // A list rule, checked ahead of
                            // every other shape: a spark `columns` cell's own
                            // array measures/paints as "[N items]" — never the
                            // raw JSON array text — so its column's demand (and
                            // therefore compact-pinned width) matches
                            // production exactly.
                            Some(Value::Array(items)) => Cell {
                                text: format!("[{} items]", items.len()),
                                ..Default::default()
                            },
                            // `str(True)`.
                            Some(Value::Bool(b)) => Cell {
                                text: (if *b { "True" } else { "False" }).to_string(),
                                ..Default::default()
                            },
                            Some(other) => Cell {
                                text: other.to_string(),
                                ..Default::default()
                            },
                        })
                    })
                    .collect()
            }
        })
        .collect::<Result<Vec<_>, _>>()
}

/// The visible columns of the table, in paint order, with each one's resolved
/// config — `_prepare_columns`. Returns the pivot's visible
/// row-dimension count and its group-header levels remapped onto the visible
/// leaves (both empty for a flat table). Shared by the render and the
/// natural-width measure, which size the same columns.
pub(super) fn visible_columns<'a>(
    r: &'a Resolved,
    rows: &'a [serde_json::Map<String, Value>],
    pivot: &'a Option<PivotResult>,
) -> Result<(Vec<ColMeta>, usize, Vec<Vec<PivotLevel>>), Diagnostic> {
    let row_role = r.style.table.row.role.as_deref();
    let chart_id = r.id.as_deref().unwrap_or_default();
    // Visible-column set mirrors production's `_render_table_svg_core`
    // When style.columns was authored, ITS keys are
    // the whole visible list — any query column the author didn't name
    // (a measure like `nrr`, or the row.role marker column) stays hidden,
    // never a "missing config" error. Only when nothing was authored does
    // the visible set fall back to every query column, minus row.role. A
    // pivot's own leaf/row-dim column order (`pivot_table_data`'s output)
    // overrides both — the raw query columns (and r.columns' own keys,
    // which still list the pre-pivot dimension fields) are never the
    // visible set once a pivot reshape ran.
    let col_names: Vec<&str> = if let Some(p) = &pivot {
        p.col_order.iter().map(|s| s.as_str()).collect()
    } else if !r.columns.is_empty() {
        r.columns.keys().map(|k| k.as_str()).collect()
    } else if let Some(first) = rows.first() {
        first
            .keys()
            .map(|k| k.as_str())
            .filter(|&k| Some(k) != row_role)
            .collect()
    } else {
        Vec::new()
    };

    if col_names.is_empty() && !r.style.table.row_numbers.visible {
        return Err("table: no columns".into());
    }

    let header_font_case = r
        .style
        .table
        .header
        .font
        .case
        .as_deref()
        .ok_or("table: header requires table.header.font.case")?;

    // Build column metadata.
    let cols: Vec<ColMeta> = col_names
        .iter()
        .enumerate()
        .map(|(idx, &name)| {
            // A pivot leaf column's own config key is the measure name
            // (multi-measure) or the innermost dimension value
            // (multi-dim single-measure) — the part after the last
            // separator — never the full synthesized leaf key. A
            // single-dim single-measure leaf (no separator) and any
            // row-dim column look themselves up directly, same as a plain
            // query column.
            let config_key = match name.rfind(PIVOT_LEAF_SEP) {
                Some(idx) => &name[idx + PIVOT_LEAF_SEP.len()..],
                None => name,
            };
            let is_leaf = pivot.as_ref().is_some_and(|p| idx >= p.n_row_dims);
            // col_names is derived above either from r.columns' own keys (every
            // name is present by construction) or, when r.columns is empty,
            // from the query's own columns — production's "no column-level
            // config" fallback (ResolvedTableChart.columns docstring), so an
            // absent entry there is a plain-query column, not an error.
            //
            // Single-dim single-measure pivot only: a leaf-keyed entry
            // (config_key == name, e.g. "Electronics") wins when present;
            // absent that, a measure-keyed entry fans out to every leaf —
            // same authoring shape as the multi-measure branch — but its
            // `label` doesn't fan (`_fanned_leaf_config`,
            // `measure_identity=False`): the header still names the leaf.
            // Every single-measure pivot leaf — single-dim (config_key ==
            // name) AND multi-dim (leaf carries no measure suffix, so a
            // measure-keyed lookup can never hit on its own) — fans the
            // measure's entry when no leaf-keyed entry exists
            let raw_cfg = r.columns.get(config_key).or_else(|| {
                if is_leaf {
                    pivot
                        .as_ref()?
                        .single_measure
                        .as_deref()
                        .and_then(|m| r.columns.get(m))
                } else {
                    None
                }
            });
            let fan_out_via_measure = raw_cfg.is_some() && r.columns.get(config_key).is_none();
            // A pivot with `pivot.levels` non-empty is the multi-dim-or-
            // multi-measure shape: EVERY leaf carrying a config — fanned via the
            // measure fallback or matched directly under its own leaf-part
            // key — goes through `_fanned_leaf_config`. Only the single-dim
            // single-measure shape (empty `levels`) lets a leaf-keyed direct
            // match skip it and keep its own authored width.
            let levels_nonempty = pivot.as_ref().is_some_and(|p| !p.levels.is_empty());
            let is_fanned_leaf = is_leaf && (fan_out_via_measure || levels_nonempty);
            let mut cfg: ColConfig = match raw_cfg {
                Some(v) => Deserialize::deserialize(v)
                    .map_err(|e| format!("table: column '{config_key}' config parse error: {e}"))?,
                None => ColConfig::default(),
            };
            if fan_out_via_measure {
                cfg.label = None;
                cfg.header_link = None;
            }
            if is_fanned_leaf {
                // The sizing slot never fans: a per-column width multiplied
                // across N leaves would inflate the table
                // (`_fanned_leaf_config` nulls both
                // `width` and `max_width` before `fill_table_column_defaults`
                // runs, so a leaf's own `max_width` doesn't block the
                // `column_defaults.width` fill below either).
                cfg.width = None;
                cfg.max_width = None;
            }
            // column_defaults fills each field independently (never gated on
            // another field).
            if let Some(defaults) = &r.column_defaults {
                if cfg.format.is_none() {
                    cfg.format = defaults.format.clone();
                }
                if cfg.width.is_none() && cfg.max_width.is_none() {
                    cfg.width = defaults.width.clone();
                }
                if cfg.align.is_none() {
                    cfg.align = defaults.align.clone();
                }
                if cfg.background.is_none() {
                    cfg.background = defaults.background.clone();
                }
                if cfg.font.is_none() {
                    cfg.font = defaults.font.as_ref().map(|f| ColumnFont {
                        color: f.color.clone(),
                        weight: f.weight.clone(),
                    });
                }
            }

            // A pivot leaf is built at render time, so its align verdict is too:
            // `classify_date_column_align` (right iff every non-null value is date-like).
            if is_leaf && cfg.align.is_none() {
                let mut values = rows
                    .iter()
                    .filter_map(|row| row.get(name))
                    .filter(|v| {
                        !v.is_null() && !matches!(v, Value::String(s) if s.trim().is_empty())
                    })
                    .peekable();
                if values.peek().is_some() && values.all(|v| is_date_like_cell(Some(v))) {
                    cfg.align = Some("right".to_string());
                }
            }

            // Chart-level `conditional_formatting` looks itself up under the
            // same key `cfg` did (`config_key`) — a pivot leaf's own
            // measure/dimension suffix, mirroring production's measure-keyed
            // remap for the shapes this corpus exercises.
            let when_rules: Vec<WhenRule> = r
                .conditional_formatting
                .as_ref()
                .and_then(|m| m.get(config_key))
                .map(|f| f.when.clone())
                .unwrap_or_default();

            // A column has a numeric lane when some cell is a magnitude;
            // a text or date cell beside them is painted on its own terms.
            let is_numeric = rows
                .iter()
                .any(|row| numeric_value(row.get(name)).is_some());

            // Date lane (invariant #11): a non-numeric column with date-like
            // content that is right-aligned, or that mixes in a cell the
            // numeric lane would take (an authored `format:` makes every cell
            // one). Trusts the resolved `align` verdict as
            // *whether* the column right-aligns; a plain right-aligned text
            // column with no date-shaped content (e.g. a pie attachment's
            // percent-string column) never earns this lane.
            let has_date_like = rows.iter().any(|row| is_date_like_cell(row.get(name)));
            let has_lane_cell = rows.iter().any(|row| {
                let v = row.get(name);
                !is_date_like_cell(v)
                    && (cfg.format.is_some()
                        || matches!(v, Some(Value::String(s)) if s.trim().parse::<f64>().is_ok()))
            });
            let is_date = !is_numeric
                && has_date_like
                && (cfg.align.as_deref() == Some("right") || has_lane_cell);

            // Production cases the AUTHORED label too: `display_name =
            // (label or slug_to_text(col))` then `apply_case(display_name,
            // header_case)` — an authored lowercase
            // label is not exempt from the header font case.
            let raw_label = match cfg.label.clone().filter(|s| !s.is_empty()) {
                Some(l) => l,
                None => display_text::slug_to_text(config_key)?,
            };
            let header = display_text::case::apply_case(&raw_label, header_font_case)?;

            Ok(ColMeta {
                name: name.to_string(),
                header,
                cfg,
                is_numeric,
                is_date,
                when_rules,
            })
        })
        .collect::<Result<Vec<_>, String>>()?;

    // `visible: false` is the sole mechanism that removes a column from the
    // rendered set. Production applies this filter to the
    // *post-pivot* column list too, then remaps every group-span descriptor
    // onto the surviving leaves — hiding a leaf narrows
    // the span that covers it instead of leaving a phantom column in its
    // index math.
    // `pivot` itself stays untouched — its col_order Strings are still
    // borrowed by every ColMeta::name in `cols` below — so the post-filter
    // shape is tracked in these two standalone locals instead of mutating
    // PivotResult in place.
    // Measure keys a pivot fanned onto its leaves (`_addressed_via_expansion`).
    let mut fanned_measures: BTreeSet<&str> = BTreeSet::new();
    if let Some(p) = &pivot {
        let values: &[String] = &p.values;
        let leaves = col_names.iter().skip(p.n_row_dims).copied();
        if p.levels.is_empty() {
            let measure = values.first().filter(|m| r.columns.contains_key(*m));
            if let Some(m) = measure {
                if leaves.clone().any(|leaf| !r.columns.contains_key(leaf)) {
                    fanned_measures.insert(m);
                }
            }
        } else {
            for leaf in leaves {
                let last = leaf.rsplit(PIVOT_LEAF_SEP).next().unwrap_or(leaf);
                if r.columns.contains_key(last) {
                    fanned_measures.insert(last);
                } else if !values.iter().any(|v| v == last) && values.len() == 1 {
                    if let Some(m) = values.first().filter(|m| r.columns.contains_key(*m)) {
                        fanned_measures.insert(m);
                    }
                }
            }
        }
    }
    check_visible_addressable(chart_id, &r.columns, rows, row_role, &fanned_measures)?;
    let visible_flags: Vec<bool> = cols.iter().map(|c| c.cfg.visible != Some(false)).collect();
    let (pivot_n_row_dims, pivot_levels_visible): (usize, Vec<Vec<PivotLevel>>) = match &pivot {
        Some(p) => {
            let n_leaves_total = cols.len() - p.n_row_dims;
            let mut leaf_map: Vec<Option<usize>> = Vec::with_capacity(n_leaves_total);
            let mut next_new = 0usize;
            for i in 0..n_leaves_total {
                if visible_flags[p.n_row_dims + i] {
                    leaf_map.push(Some(next_new));
                    next_new += 1;
                } else {
                    leaf_map.push(None);
                }
            }
            let remapped_levels: Vec<Vec<PivotLevel>> = p
                .levels
                .iter()
                .map(|level| {
                    level
                        .iter()
                        .filter_map(|(label, first_leaf, n_leaves)| {
                            let new_indices: Vec<usize> = (*first_leaf..first_leaf + n_leaves)
                                .filter_map(|old| leaf_map[old])
                                .collect();
                            match (new_indices.first(), new_indices.last()) {
                                (Some(&new_first), Some(&new_last)) => {
                                    Some((label.clone(), new_first, new_last - new_first + 1))
                                }
                                _ => None,
                            }
                        })
                        .collect::<Vec<_>>()
                })
                // A level whose every group lost all its leaves paints no band.
                .filter(|level| !level.is_empty())
                .collect();
            let new_n_row_dims = visible_flags[..p.n_row_dims].iter().filter(|&&v| v).count();
            (new_n_row_dims, remapped_levels)
        }
        None => (0, Vec::new()),
    };
    let cols: Vec<ColMeta> = cols
        .into_iter()
        .zip(visible_flags)
        .filter(|(_, v)| *v)
        .map(|(c, _)| c)
        .collect();
    if cols.is_empty() {
        return Err("table: no visible columns".into());
    }
    Ok((cols, pivot_n_row_dims, pivot_levels_visible))
}

/// Chart-level pivot: reshape long → wide. Scoped to the corpus's shapes —
/// see `pivot_table_data`'s doc comment for what's out of scope. Shared by
/// the render and the natural-width measure, which size the same columns.
pub(super) fn pivot_for(
    r: &Resolved,
    query_rows: &[serde_json::Map<String, Value>],
    cell_text: &[CellRow],
) -> Result<Option<PivotResult>, Diagnostic> {
    let row_role = r.style.table.row.role.as_deref();
    match &r.pivot_columns {
        // An empty result has nothing to reshape: the header shows the resolved
        // columns, as a flat table does.
        Some(columns) if !columns.is_empty() && !query_rows.is_empty() => {
            let (row_dims, values) = validate_pivot_fields(
                query_rows,
                r.rows.as_deref().unwrap_or_default(),
                columns,
                r.values.as_deref(),
                row_role,
            )?;
            // The row-detail template names BASE columns; a pivot reshapes
            // the rows into leaf columns, so no row could resolve it.
            if r.link.is_some() {
                return Err("table: a chart-root link on a pivot table not supported".into());
            }
            Ok(Some(pivot_table_data(
                query_rows, cell_text, &row_dims, columns, &values, row_role,
            )?))
        }
        _ => Ok(None),
    }
}

/// The rows the table lays out: the pivot's reshaped rows, else the query's.
pub(super) fn table_rows<'a>(
    query_rows: &'a [serde_json::Map<String, Value>],
    pivot: &'a Option<PivotResult>,
) -> &'a [serde_json::Map<String, Value>] {
    match pivot {
        Some(p) => &p.rows,
        None => query_rows,
    }
}

pub(super) fn bind_chart(src: Source) -> Result<Chart, Diagnostic> {
    let _formats = BoardFormats::enter(src.formats, src.sub_unit_specs);
    if let Some((resolved, rows)) = text_format::lower(src.resolved, src.rows) {
        return bind_chart(Source {
            resolved: &resolved,
            rows: &rows,
            ..src
        });
    }
    let r: Resolved =
        Deserialize::deserialize(src.resolved).map_err(|e| format!("table payload: {e}"))?;
    if r.style.table.transpose && !src.rows.is_empty() {
        let (resolved, rows) = transposed_inputs(src.resolved, src.rows)?;
        return bind_chart(Source {
            resolved: &resolved,
            rows: &rows,
            ..src
        });
    }

    let pivot = pivot_for(&r, src.rows, src.cell_text)?;
    let rows = table_rows(src.rows, &pivot);
    let (cols, pivot_n_row_dims, pivot_levels_visible) = visible_columns(&r, rows, &pivot)?;
    let pivoted = pivot.is_some();
    let th = &r.style.table;
    let row_roles = resolve_row_roles(rows, th.row.role.as_deref());

    // Spark `bar` auto-max + midline refusal,
    // scanned over the FULL dataset minus summary
    // rows (a grand-total value would otherwise re-anchor every detail
    // row's bar scale). `bar-normalize`/`columns` are excluded: normalize
    // always uses an explicit `max:`/theme default, and `columns` decides
    // its own layout from its own cell array (render_spark_columns below),
    // not a table-column-wide scan.
    let mut bar_auto_max: BTreeMap<String, f64> = BTreeMap::new();
    let mut signed_columns: BTreeSet<String> = BTreeSet::new();
    for col in &cols {
        let Some(spark) = &col.cfg.spark else {
            continue;
        };
        if !matches!(spark.spark_type.as_str(), "bar" | "column") {
            continue;
        }
        let detail_values: Vec<f64> = rows
            .iter()
            .zip(row_roles.iter())
            .filter(|(_, role)| !is_summary_role(**role))
            .filter_map(|(row, _)| coerce_numeric_cell(row.get(col.name.as_str())))
            .collect();
        if detail_values.iter().any(|v| *v < 0.0) {
            signed_columns.insert(col.name.clone());
        }
        if spark.spark_type == "bar" && spark.max.is_none() {
            if let Some(max_abs) = detail_values
                .iter()
                .map(|v| v.abs())
                .fold(None, |acc: Option<f64>, v| {
                    Some(acc.map_or(v, |a| a.max(v)))
                })
            {
                bar_auto_max.insert(col.name.clone(), max_abs);
            }
        }
    }

    let force_compact: BTreeSet<String> = cols
        .iter()
        .filter(|c| c.cfg.spark.is_some())
        .map(|c| c.name.clone())
        .collect();

    // Format all numeric/date-lane cells, over the FULL dataset — this
    // array feeds MEASUREMENT (column demand/word-floor/wrap, all reading
    // `.text`/`.full_prefix`/`.full_suffix`, never the anchor-masked
    // `.prefix`/`.suffix`), so it stays global regardless of pagination —
    // see `build_cell_data`'s own doc for why a paginated PAINT call
    // recomputes its own copy instead of slicing this one.
    let cell_data = build_cell_data(rows, &cols, &th.symbol_mode, &row_roles)?;
    let rows = rows.to_vec();

    Ok(Chart {
        resolved: r,
        rows,
        cols,
        pivoted,
        pivot_n_row_dims,
        pivot_levels_visible,
        row_roles,
        cell_data,
        bar_auto_max,
        signed_columns,
        force_compact,
        formats: src.formats.cloned(),
        sub_unit_specs: src.sub_unit_specs.cloned(),
    })
}

/// What drawing the chart reads of `payload`; every check here is one only a
/// drawn table makes, so the natural-width measure binds none of it.
pub(super) fn bind_drawn(chart: &Chart, payload: &Payload) -> Result<Drawn, Diagnostic> {
    let r = &chart.resolved;
    let th = &r.style.table;
    let pagination_control_height = payload
        .board_style
        .as_ref()
        .and_then(|b| b.pagination_control_height)
        .ok_or("table: the payload carries no board_style.pagination_control_height")?;

    // Authored spanners apply only when no pivot built the header groups.
    let mut header_levels = chart.pivot_levels_visible.clone();
    let row_role = th.row.role.as_deref();
    let chart_id = r.id.as_deref().unwrap_or_default();
    if let (true, Some(spanners), false) = (
        !chart.pivoted,
        r.spanners.as_deref().filter(|s| !s.is_empty()),
        chart.rows.is_empty(),
    ) {
        let queried: Vec<&str> = chart.rows[0]
            .keys()
            .map(String::as_str)
            .filter(|k| Some(*k) != row_role)
            .collect();
        let visible: Vec<&str> = chart.cols.iter().map(|c| c.name.as_str()).collect();
        let level = authored_spanner_level(chart_id, spanners, &queried, &visible)?;
        if !level.is_empty() {
            header_levels = vec![level];
        }
    }

    let font_size = th.font.size_or(11.0);
    let body_family = svg_font_family(th.font.family_or("sans-serif"));
    let numeral_family = payload
        .board_style
        .as_ref()
        .and_then(|b| b.numeral_family.clone())
        .ok_or("table: board_style.numeral_family (style.fonts.numeral) is missing")?;
    // A static `color` replaces the font's; every authored table color is
    // validated up front.
    let body_color = sanitize_color(th.color.as_ref().and_then(|c| c.static_color.as_deref()))?
        .or(th.font.color.as_deref())
        .unwrap_or("#222222");
    sanitize_color(th.row.stripe.as_ref().and_then(|s| s.color.as_deref()))?;
    let link_color = sanitize_color(
        payload
            .board_style
            .as_ref()
            .and_then(|b| b.font_color.as_deref()),
    )?;
    let header_size = th.header.font.size_or(font_size);
    let header_family = th
        .header
        .font
        .family
        .as_deref()
        .map(svg_font_family)
        .unwrap_or_else(|| body_family.clone());
    let header_color = sanitize_color(th.header.font.color.as_deref())?.unwrap_or(body_color);
    let header_weight = match th
        .header
        .font_compact
        .as_ref()
        .filter(|c| c.weight.is_some())
    {
        Some(compact) if font_size.trunc() <= 11.0 => compact.weight_str(),
        _ => th.header.font.weight_str(),
    };
    let look = Look {
        font_size,
        header_size,
        header_family,
        header_weight,
        header_color: header_color.to_string(),
        body_color: body_color.to_string(),
        link_color: link_color.map(str::to_string),
        body_family,
        numeral_family,
        pad: th.column_layout.cell_padding.trunc(),
        outer: th.outer_padding,
        row_h: th.row.height,
    };
    Ok(Drawn {
        look,
        header_levels,
        board_style: payload.board_style.clone(),
        padding: payload.padding,
        variables: payload.variables.clone(),
        filter_variables: payload.filter_variables.clone(),
        interactive_controls: payload.interactive_controls,
        link_context: payload.link_context.clone(),
        title_shift: payload.title_shift,
        pagination_control_height,
    })
}

pub fn bind(payload: &Payload) -> Result<Bound, Diagnostic> {
    if payload.is_empty_result() {
        let resolved: EmptyResolved = Deserialize::deserialize(&payload.resolved)
            .map_err(|e| format!("table payload: {e}"))?;
        // A table with columns draws their header band too, as does one with row
        // numbers, so those bind their rows.
        if resolved.columns.as_ref().is_none_or(|c| c.is_empty())
            && !resolved.style.table.row_numbers.visible
        {
            return Ok(Bound::Empty(Blank {
                resolved,
                padding: payload.padding,
                title_shift: payload.title_shift,
            }));
        }
    }
    let chart = bind_chart(Source {
        resolved: &payload.resolved,
        rows: &payload.rows,
        cell_text: &payload.cell_text,
        formats: payload
            .board_style
            .as_ref()
            .and_then(|b| b.formats.as_ref()),
        sub_unit_specs: payload
            .board_style
            .as_ref()
            .and_then(|b| b.sub_unit_specs.as_ref()),
    })?;
    let drawn = bind_drawn(&chart, payload)?;
    Ok(Bound::Rows(Rows { chart, drawn }))
}
