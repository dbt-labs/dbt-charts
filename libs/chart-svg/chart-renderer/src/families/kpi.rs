//! KPI family renderer (native SVG, no Vega-Lite): the stacked, inline and
//! compact variants, the support row, the headline glyph, per-slot fonts, label
//! overflow, the conditional and gradient color and background channels, and the
//! house number and date formats under the board's number locale.

use d3rs::py_float::format_g;
use serde::Deserialize;
use serde_json::{json, Value};

use super::table::bind::{
    compute_scale_domain, font_weight_as_css, interpolate_scale_color, resolve_conditional_styles,
    resolve_hinge, resolve_palette_stops, sanitized_color,
};
use super::table::{WhenRule, VALID_FONT_WEIGHTS};
use super::color_scale::nice_domain;
use super::table_dates;
use crate::common::ScaleTarget;
use crate::codes;
use crate::common::{
    format_kpi_parts, FormatParts, FormatSlot, PartsOptions,
};
use crate::diagnostic::{warn, Diagnostic};
use crate::layout::{Insets, IntrinsicWidth, Layout, Rect};
use crate::svg::py_float_str;
use crate::{Payload, TonesStyle};
use measure::weighted::weighted_width;
use measure::wrap::wrap_label;
use measure::{FontBook, Measure};
use print::print;
use scene::{Attrs, Chars, Escape, Extent, Node, TextBlock};
const CAP_HEIGHT_RATIO: f64 = 0.7;
const DESCENDER_RATIO: f64 = 0.2;
const AFFIX_ELEVATION: f64 = 0.37;
// Minimum gap between the value baseline and the label block.
const MINIMUM_TITLE_VALUE_GAP: f64 = 14.0;
// Horizontal gap between adjacent slots in inline/compact.
const INLINE_GAP: f64 = 14.0;
// The `font-weight` a slot paints when its authored weight is not a CSS weight.
const FALLBACK_SLOT_WEIGHT: &str = "500";

/// The board's plain-digit sibling spec of each adaptive member.
type SubUnitSpecs = std::collections::BTreeMap<String, String>;

// ── Payload structs ────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct Resolved {
    id: String,
    label: String,
    value: String,
    /// `ResolvedKpiChart.glyph` (`resolved/kpi.py`): the headline glyph, null
    /// when none. Always serialized.
    #[serde(deserialize_with = "Option::deserialize")]
    glyph: Option<String>,
    /// The sign rules `good` lowers to on the headline column
    /// (`ResolvedKpiChart.value_rules`), null when the chart sets none.
    #[serde(deserialize_with = "Option::deserialize")]
    value_rules: Option<Vec<WhenRule>>,
    #[serde(deserialize_with = "Option::deserialize")]
    support: Option<SupportCfg>,
    variant: Variant,
    #[serde(deserialize_with = "Option::deserialize")]
    format: Option<FormatSlot>,
    format_native: bool,
    style: KpiStyleWrap,
    resolved_channels: serde_json::Map<String, Value>,
    // An empty string is no link.
    #[serde(deserialize_with = "crate::common::de_nonempty_string")]
    link: Option<String>,
    /// How `link` opens; the implied `text_link` always navigates the page.
    link_open: crate::href::LinkOpen,
    /// The link an `email` / `url` headline implies (`ResolvedKpiChart.text_link`),
    /// below the chart's own `link`.
    #[serde(deserialize_with = "crate::common::de_nonempty_string")]
    text_link: Option<String>,
}

#[derive(Deserialize, Clone, Copy, PartialEq)]
#[serde(rename_all = "lowercase")]
enum Variant {
    Stacked,
    Inline,
    Compact,
}

/// `ResolvedKpiSupportConfig`: the support block's content. Its `format` is
/// the resolved `style.support.format` and its `tone` lives under
/// `style.kpi.support`.
#[derive(Deserialize)]
struct SupportCfg {
    value: Option<String>,
    label: Option<String>,
    format: Option<FormatSlot>,
    glyph: Option<String>,
    /// The color-resolved `conditional_formatting` rules keyed on the support
    /// column (`resolved/kpi.py:24`). The key always serializes, null when the
    /// chart authors none, so a payload without it is refused rather than read
    /// as "no rules".
    #[serde(deserialize_with = "Option::deserialize")]
    rules: Option<Vec<WhenRule>>,
}

#[derive(Deserialize)]
struct KpiStyleWrap {
    kpi: KpiChartStyle,
}

/// `KpiChartStyle` (`models/style/theme/kpi.py`), the slice the renderer reads.
#[derive(Deserialize)]
struct KpiChartStyle {
    font: CardInk,
    value: ValueStyle,
    label: LabelStyle,
    support: SupportStyle,
    content_padding: Padding,
    /// The card height an inline or compact card wants, and the floor a
    /// stacked card's slot starts from.
    default_height: f64,
    #[serde(deserialize_with = "Option::deserialize")]
    background: Option<String>,
    /// Whole-card alignment of the value/label/support text column
    /// (`KpiChartStyle.align`); None keeps the left-anchored geometry.
    #[serde(deserialize_with = "Option::deserialize")]
    align: Option<String>,
    border: BorderStyle,
}

/// `kpi.font`: every slot font starts from it; the renderer reads only its ink.
#[derive(Deserialize)]
struct CardInk {
    #[serde(deserialize_with = "Option::deserialize")]
    color: Option<String>,
}

#[derive(Deserialize)]
struct ValueStyle {
    font: FontPayload,
    affix: TextSlot,
    glyph: GlyphSlot,
}

/// `KpiValueGlyphStyle`: the authored glyph's font, and the font of the
/// glyphs a `good` rule draws.
#[derive(Deserialize)]
struct GlyphSlot {
    font: FontPayload,
    arrow: TextSlot,
}

#[derive(Deserialize)]
struct TextSlot {
    font: FontPayload,
}

#[derive(Deserialize)]
struct LabelStyle {
    font: FontPayload,
    overflow: Overflow,
}

#[derive(Deserialize, Clone, Copy)]
#[serde(rename_all = "kebab-case")]
enum Overflow {
    Clip,
    Truncate,
    WrapTwo,
    Wrap,
}

#[derive(Deserialize)]
struct SupportStyle {
    font: FontPayload,
    #[serde(deserialize_with = "Option::deserialize")]
    tone: Option<String>,
    glyph: TextSlot,
}

/// The card's corner radius; the border itself is drawn by the chart wrapper
/// (`rendering.py::_card_border`), not by the card.
#[derive(Deserialize)]
struct BorderStyle {
    radius: f64,
}

#[derive(Deserialize)]
struct Padding {
    top: f64,
    right: f64,
    bottom: f64,
    left: f64,
}

impl Padding {
    /// `SpacingValues.horizontal`.
    fn horizontal(&self) -> f64 {
        self.left + self.right
    }
}

/// Keys the previous resolved shape carried that this one moved; a payload
/// still carrying one is from an older production and refuses by name.
fn refuse_retired_keys(resolved: &Value) -> Result<(), String> {
    if resolved["support"].get("tone").is_some() {
        return Err("kpi: `support.tone` is retired: the support tone is `style.kpi.support.tone`".into());
    }
    let kpi = &resolved["style"]["kpi"];
    for (key, successor) in [
        ("glyph", "the chart-root `glyph` drawn in `style.kpi.value.glyph.font`"),
        ("affix", "`style.kpi.value.affix.font`"),
    ] {
        if kpi.get(key).is_some() {
            return Err(format!("kpi: `style.kpi.{key}` is retired: it is {successor}"));
        }
    }
    Ok(())
}

// ── Slot fonts ──────────────────────────────────────────────

/// A `SingleLineFontStyle` as the payload carries it. The cascade fills every
/// field but `color`; `line_height` is not a field of a single-line slot.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FontPayload {
    extends: Option<String>,
    family: Option<String>,
    #[serde(deserialize_with = "Option::deserialize")]
    color: Option<String>,
    size: Option<f64>,
    weight: Option<Value>,
    style: Option<String>,
    decoration: Option<String>,
    case: Option<String>,
}

/// One KPI text slot's cascade-complete typography, plus the
/// slot's own ink.
struct SlotFont {
    family: String,
    size: f64,
    weight: String,
    italic: bool,
    decoration: String,
    case: String,
    color: Option<String>,
}

impl SlotFont {
    /// Resolves one slot's font from the cascade-complete payload.
    fn resolve(font: &FontPayload, path: &str) -> Result<Self, String> {
        let unresolved = |field: &str| format!("kpi: font {path} is unresolved: `{field}` is null — the cascade fills every slot font from kpi.font");
        if font.extends.is_some() {
            return Err(format!("kpi: font {path} still carries `extends`: the cascade expands it before render"));
        }
        let family = font.family.clone().ok_or_else(|| unresolved("family"))?;
        let size = font.size.ok_or_else(|| unresolved("size"))?;
        let weight = font.weight.as_ref().ok_or_else(|| unresolved("weight"))?;
        let style = font.style.as_deref().ok_or_else(|| unresolved("style"))?;
        let decoration = font.decoration.clone().ok_or_else(|| unresolved("decoration"))?;
        let case = font.case.clone().ok_or_else(|| unresolved("case"))?;
        let italic = match style {
            "normal" => false,
            "italic" => true,
            other => return Err(format!("kpi: font {path} style {other:?} is not normal or italic")),
        };
        if !matches!(decoration.as_str(), "none" | "line-through" | "underline") {
            return Err(format!("kpi: font {path} decoration {decoration:?} is not none, line-through or underline"));
        }
        let css_weight = font_weight_as_css(weight);
        let weight = if VALID_FONT_WEIGHTS.contains(&css_weight.as_str()) {
            css_weight
        } else {
            FALLBACK_SLOT_WEIGHT.to_string()
        };
        Ok(SlotFont { family, size, weight, italic, decoration, case, color: font.color.clone() })
    }

    fn attrs(&self) -> Attrs {
        let mut attrs = Attrs::new()
            .quick("font-family", self.family.as_str())
            .quick("font-size", py_float_str(self.size))
            .quick("font-weight", self.weight.as_str());
        if self.italic {
            attrs = attrs.quick("font-style", "italic");
        }
        if self.decoration != "none" {
            attrs = attrs.quick("text-decoration", self.decoration.as_str());
        }
        attrs
    }

    /// The text under the slot's case transform.
    fn cased(&self, text: &str) -> Result<String, String> {
        display_text::case::apply_case(text, &self.case)
    }

    fn measure(&self, m: &Measure, text: &str, numeric: bool) -> Result<f64, String> {
        weighted_width(m.fonts, &self.family, &self.weight, self.italic, numeric, self.size, text)
    }
}

/// Every slot's typography, resolved once.
struct Slots {
    value: SlotFont,
    affix: SlotFont,
    glyph: SlotFont,
    arrow: SlotFont,
    label: SlotFont,
    support: SlotFont,
    support_glyph: SlotFont,
}

impl Slots {
    fn resolve(kpi: &KpiChartStyle) -> Result<Self, String> {
        Ok(Slots {
            value: SlotFont::resolve(&kpi.value.font, "value.font")?,
            affix: SlotFont::resolve(&kpi.value.affix.font, "value.affix.font")?,
            glyph: SlotFont::resolve(&kpi.value.glyph.font, "value.glyph.font")?,
            arrow: SlotFont::resolve(&kpi.value.glyph.arrow.font, "value.glyph.arrow.font")?,
            label: SlotFont::resolve(&kpi.label.font, "label.font")?,
            support: SlotFont::resolve(&kpi.support.font, "support.font")?,
            support_glyph: SlotFont::resolve(&kpi.support.glyph.font, "support.glyph.font")?,
        })
    }
}


fn coerce_numeric_cell(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64().filter(|f| f.is_finite()),
        Value::String(s) => s.trim().parse::<f64>().ok().filter(|f| f.is_finite()),
        _ => None,
    }
}

fn cell_to_display(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => {
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        other => other.to_string(),
    }
}

/// `(prefix, number_str, suffix, is_numeric)` of one cell.
/// `cascaded` is true only for the headline value, whose format can be a
/// board-wide default: a non-date spec meeting a date falls back to
/// `date_short` there, and is an error on the support slot.
fn format_value_parts(
    cell: &Value,
    spec: Option<&FormatSlot>,
    native: bool,
    chart_id: &str,
    cascaded: bool,
    keep_affix_gaps: bool,
    formats: Option<&serde_json::Map<String, Value>>,
    sub_unit_specs: &SubUnitSpecs,
) -> Result<(String, String, String, bool), Diagnostic> {
    if let Value::String(s) = cell {
        let temporal = table_dates::parse_temporal(s.trim());
        if !matches!(temporal, Ok(None)) {
            return format_temporal(temporal, s, spec, chart_id, cascaded, formats);
        }
    }
    match coerce_numeric_cell(cell) {
        None => {
            let text = if cell.is_null() {
                String::new()
            } else {
                cell_to_display(cell)
            };
            Ok((String::new(), text, String::new(), false))
        }
        Some(numeric) => {
            let parts = spec.map(FormatSlot::parts).unwrap_or_default();
            let options = PartsOptions {
                native,
                keep_affix_gaps,
                sign_in_prefix: true,
                sub_unit_specs: Some(sub_unit_specs),
                ..PartsOptions::default()
            };
            let lanes = format_kpi_parts(numeric, &parts, &options)?;
            Ok((lanes.prefix, lanes.number, lanes.suffix, true))
        }
    }
}

/// The strftime spec an authored KPI format names (`date_short` when unset).
fn authored_time_spec(authored: Option<&str>) -> &str {
    let name = authored.filter(|a| !a.is_empty()).unwrap_or("date_short");
    d3rs::format::predefined_time_spec(name).unwrap_or(name)
}

/// A time format name through the board's `style.formats`: `(spec, prefix,
/// suffix)`. An alias that names no spec cannot stand for the name.
fn time_alias<'a>(
    name: &'a str,
    formats: Option<&'a serde_json::Map<String, Value>>,
) -> Result<(&'a str, &'a str, &'a str), String> {
    let Some(alias) = formats.and_then(|f| f.get(name)) else {
        return Ok((authored_time_spec(Some(name)), "", ""));
    };
    let spec = alias
        .as_str()
        .or_else(|| alias.get("spec")?.as_str())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| format!("kpi: format alias {name:?} names no spec"))?;
    let affix = |key: &str| alias.get(key).and_then(Value::as_str).unwrap_or("");
    Ok((authored_time_spec(Some(spec)), affix("prefix"), affix("suffix")))
}

fn format_temporal(
    parsed: Result<Option<table_dates::Moment>, table_dates::TemporalError>,
    cell: &str,
    spec: Option<&FormatSlot>,
    chart_id: &str,
    cascaded: bool,
    formats: Option<&serde_json::Map<String, Value>>,
) -> Result<(String, String, String, bool), Diagnostic> {
    let FormatParts { spec: authored, prefix, suffix, .. } =
        spec.map(FormatSlot::parts).unwrap_or_default();
    // A name the board redefined (`style.formats`) resolves through its alias,
    // the alias's affix filling what the slot left unset (`column_format("time")`).
    let name = authored.filter(|a| !a.is_empty()).unwrap_or("date_short");
    let (resolved, alias_prefix, alias_suffix) = time_alias(name, formats)?;
    let prefix = if prefix.is_empty() { alias_prefix } else { prefix };
    let suffix = if suffix.is_empty() { alias_suffix } else { suffix };
    let (spec_text, prefix, suffix) = if table_dates::is_time_format(resolved) {
        (resolved, prefix, suffix)
    } else if cascaded {
        // The board-wide default tier: `date_short` through the board's table.
        time_alias("date_short", formats)?
    } else {
        return Err(Diagnostic::new(
            &codes::ERR_KPI_FORMAT_KIND_MISMATCH,
            [
                ("chart_id", json!(chart_id)),
                ("spec", json!(crate::common::predefined_number_spec(resolved).unwrap_or(resolved))),
            ],
        ));
    };
    let invalid = |reason: String| {
        Diagnostic::new(
            &codes::ERR_KPI_TEMPORAL_FORMAT_INVALID,
            [
                ("chart_id", json!(chart_id)),
                ("cell", json!(cell)),
                ("spec", json!(spec_text)),
                ("reason", json!(reason)),
            ],
        )
    };
    table_dates::validate_spec(spec_text).map_err(&invalid)?;
    let moment = match parsed {
        Ok(Some(moment)) => moment,
        Ok(None) => unreachable!("only temporal cells reach here"),
        Err(table_dates::TemporalError::Invalid(reason)) => return Err(invalid(reason)),
        Err(table_dates::TemporalError::Unported(why)) => return Err(why.into()),
    };
    let text = table_dates::strftime(moment, spec_text)?;
    Ok((String::new(), format!("{prefix}{text}{suffix}"), String::new(), false))
}

// ── Literal channels ────────────────────────────────────────────────────────

/// One resolved style channel (`ResolvedStyleChannel`).
#[derive(Deserialize)]
struct Channel {
    mode: String,
    #[serde(default)]
    data_field: String,
    #[serde(default)]
    literal_value: Option<String>,
    #[serde(default)]
    scale: Option<ScaleTarget>,
    #[serde(default)]
    rules: Vec<WhenRule>,
    #[serde(default)]
    fallback_scale: Option<ScaleTarget>,
}

/// The color a scale gives the row's cell: `interpolate_scale_color` over the
/// one-row domain, `null_color` for a non-numeric cell.
fn scale_color(
    scale: &ScaleTarget,
    field: &str,
    row: &serde_json::Map<String, Value>,
    col_format: Option<&str>,
) -> Result<Option<String>, String> {
    let Some(numeric) = row.get(field).and_then(coerce_numeric_cell) else {
        return Ok(scale.null_color.clone().filter(|c| !c.is_empty()));
    };
    let raw = compute_scale_domain(&[row], field, scale.min, scale.max);
    let (lo, hi) = nice_domain(scale.min, scale.max, scale.nice.unwrap_or(false), raw);
    let hinge = resolve_hinge(scale.hinge.as_ref(), raw.0, raw.1, col_format);
    let stops = resolve_palette_stops(scale)?;
    interpolate_scale_color(numeric, lo, hi, &stops, hinge, &scale.arm_mode).map(Some)
}

/// The color a `color` or
/// `background` channel gives this row, `None` when it gives none.
fn evaluate_channel(
    channels: &serde_json::Map<String, Value>,
    name: &str,
    row: &serde_json::Map<String, Value>,
    col_format: Option<&str>,
) -> Result<Option<String>, String> {
    let Some(raw) = channels.get(name).filter(|c| !c.is_null()) else {
        return Ok(None);
    };
    let ch: Channel =
        Deserialize::deserialize(raw).map_err(|e| format!("kpi: resolved_channels.{name}: {e}"))?;
    match ch.mode.as_str() {
        "literal" => Ok(ch.literal_value),
        "gradient" => match &ch.scale {
            Some(scale) => scale_color(scale, &ch.data_field, row, col_format),
            None => Ok(None),
        },
        "conditional" => {
            let overrides = resolve_conditional_styles(&ch.rules, row.get(&ch.data_field).filter(|v| !v.is_null()), None)?;
            let matched = if name == "background" { overrides.background } else { overrides.color };
            match (&matched, &ch.fallback_scale) {
                (None, Some(scale)) => scale_color(scale, &ch.data_field, row, col_format),
                _ => Ok(matched),
            }
        }
        // A bare `color: <field>` authoring is a Vega series encoding; the card paints none.
        "series" => Ok(None),
        other => Err(format!("kpi: unknown resolved_channels.{name} mode '{other}'")),
    }
}

// ── Colors ─────────────────────────────────────────

/// An authored ink, or None when
/// unauthored; `sanitize_color` raises on an invalid one.
fn explicit_color(color: Option<&str>) -> Result<Option<String>, String> {
    match color.filter(|c| !c.is_empty()) {
        None => Ok(None),
        Some(c) => sanitized_color(c)
            .map(Some)
            .ok_or_else(|| format!("kpi: invalid color value: {c:?}")),
    }
}

/// A slot's own color when authored, else
/// `fallback`.
fn own_fill(color: Option<&str>, fallback: &str) -> Result<String, String> {
    Ok(explicit_color(color)?.unwrap_or_else(|| fallback.to_string()))
}

/// Every ink the card paints, resolved once.
struct Colors {
    value_fill: String,
    affix_fill: String,
    glyph_fill: String,
    label_fill: String,
    card_fill: Option<String>,
    secondary: String,
}

fn resolve_colors(
    r: &Resolved,
    row: &serde_json::Map<String, Value>,
    slots: &Slots,
    kpi: &KpiChartStyle,
    board_background: Option<&str>,
    secondary: &str,
    tone_color: Option<String>,
) -> Result<Colors, String> {
    let col_format = r
        .format
        .as_ref()
        .map(|f| f.spec.as_str())
        .filter(|s| !s.is_empty())
        .map(|spec| {
        crate::common::predefined_number_spec(spec).unwrap_or(spec)
    });
    let channel_bg = evaluate_channel(&r.resolved_channels, "background", row, col_format)?;
    // An authored color channel outranks the derived tone.
    let channel_color = evaluate_channel(&r.resolved_channels, "color", row, col_format)?
        .or(tone_color);
    // Card background: channel > explicit chart-local override, which counts
    // only when it differs from the board background: board
    // propagation (the same color) is transparent to the KPI card.
    let explicit_background = match (&kpi.background, board_background) {
        (Some(bg), Some(board)) if bg != board => Some(bg.as_str()),
        (Some(bg), None) => Some(bg.as_str()),
        _ => None,
    };
    let card_fill = match channel_bg {
        Some(bg) => Some(bg),
        None => explicit_color(explicit_background)?,
    };
    let card_ink = kpi
        .font
        .color
        .as_deref()
        .ok_or("kpi: style.kpi.font.color is unresolved: the cascade fills it from charts.font.color")?;
    // Channel (or derived tone) > the value slot's own ink > the card ink.
    let value_fill = match (&channel_color, slots.value.color.as_deref()) {
        (Some(channel), _) => channel.clone(),
        (None, Some(ink)) if !ink.is_empty() => own_fill(Some(ink), card_ink)?,
        _ => card_ink.to_string(),
    };
    // Conditional formatting paints the whole value run, over slot colors
    // — Python `or`, so an empty channel color falls through.
    let channel_ink = channel_color.as_deref().filter(|c| !c.is_empty());
    let slot_fill = |slot: &SlotFont| -> Result<String, String> {
        match channel_ink {
            Some(c) => Ok(c.to_string()),
            None => own_fill(slot.color.as_deref(), &value_fill),
        }
    };
    Ok(Colors {
        affix_fill: slot_fill(&slots.affix)?,
        glyph_fill: slot_fill(&slots.glyph)?,
        label_fill: own_fill(slots.label.color.as_deref(), card_ink)?,
        value_fill,
        card_fill,
        secondary: secondary.to_string(),
    })
}

// ── Support row ────────────────────────────────────

struct SupportRow {
    glyph: String,
    value_str: String,
    explainer: String,
    value_fill: String,
    glyph_fill: String,
}

/// The predefined formats that write a signed change.
const DELTA_FORMATS: [&str; 4] = ["delta", "percent_delta", "percent_number_delta", "percentage_points_delta"];

fn resolve_support_row(
    support: &Option<SupportCfg>,
    row: &serde_json::Map<String, Value>,
    kpi: &KpiChartStyle,
    slots: &Slots,
    tones: Option<&TonesStyle>,
    secondary: &str,
    chart_id: &str,
    formats: Option<&serde_json::Map<String, Value>>,
    sub_unit_specs: &SubUnitSpecs,
) -> Result<Option<SupportRow>, Diagnostic> {
    let Some(support) = support else {
        return Ok(None);
    };
    let cell = match &support.value {
        Some(col) => column_cell(row, col, chart_id)?.clone(),
        None => Value::Null,
    };
    // The support value keeps the BI default register and, as one flat
    // string, its affixes' gaps (`kpi_value_text`).
    let value_str = {
        let (s_prefix, s_number, s_suffix, _) =
            format_value_parts(&cell, support.format.as_ref(), false, chart_id, false, true, formats, sub_unit_specs)?;
        slots.support.cased(&format!("{s_prefix}{s_number}{s_suffix}"))?
    };

    // the rules see the raw support cell (a null cell is
    // Python's `None`); a matched rule's glyph replaces the authored one, even
    // an empty one.
    let styled = resolve_conditional_styles(
        support.rules.as_deref().unwrap_or(&[]),
        Some(&cell).filter(|v| !v.is_null()),
        tones,
    )
    .map_err(|e| format!("kpi: support {e}"))?;
    if styled.tone_needs_board_tones {
        return Err("kpi: support conditional rule tone with no board tones".into());
    }
    let glyph = slots
        .support_glyph
        .cased(&styled.glyph.unwrap_or_else(|| support.glyph.clone().unwrap_or_default()))?;
    let explainer = slots.support.cased(support.label.as_deref().unwrap_or_default())?;
    if value_str.is_empty() && explainer.is_empty() && glyph.is_empty() {
        return Ok(None);
    }
    let board_tones = || tones.ok_or("kpi: support tone with no board tones");
    let is_delta = support
        .format
        .as_ref()
        .and_then(|f| f.raw.as_deref())
        .is_some_and(|raw| DELTA_FORMATS.contains(&raw));
    // A delta value is colored by its sign and `tone` is the direction
    // (positive: up is good, negative: up is bad; unset reads as positive);
    // zero and a non-number stay un-toned. warning/info are fixed colors.
    let tone_color = match (kpi.support.tone.as_deref(), is_delta) {
        (None, false) => None,
        (None | Some("positive" | "negative"), true) => {
            let up_is_good = kpi.support.tone.as_deref() != Some("negative");
            match cell.as_f64() {
                Some(v) if v != 0.0 => {
                    let name = if (v > 0.0) == up_is_good { "positive" } else { "negative" };
                    board_tones()?.resolve(name)
                }
                _ => None,
            }
        }
        (Some(tone), _) => Some(
            board_tones()?
                .resolve(tone)
                .ok_or_else(|| format!("kpi: unknown support tone {tone:?}"))?,
        ),
    };
    let row_color = explicit_color(slots.support.color.as_deref())?;
    let glyph_ink = explicit_color(slots.support_glyph.color.as_deref())?;
    // A matched rule's `tone` colors the value like its `font.color` does.
    let cf_color = styled.color.filter(|c| !c.is_empty()).or(styled.tone_color);
    let value_fill = cf_color
        .clone()
        .or_else(|| row_color.clone())
        .or_else(|| tone_color.map(str::to_string))
        .unwrap_or_else(|| secondary.to_string());
    let glyph_fill = styled
        .glyph_color
        .filter(|c| !c.is_empty())
        .or(cf_color)
        .or(glyph_ink)
        .or_else(|| tone_color.map(str::to_string))
        .or(row_color)
        .unwrap_or_else(|| secondary.to_string());

    Ok(Some(SupportRow { glyph, value_str, explainer, value_fill, glyph_fill }))
}

/// One painted tspan: `(font, fill, text)`.
struct Run<'a> {
    font: &'a SlotFont,
    fill: &'a str,
    text: String,
}

/// Glyph, value, explainer — the explainer reads every support
/// font field except color, it stays secondary.
fn support_runs<'a>(row: &'a SupportRow, slots: &'a Slots, secondary: &'a str) -> Vec<Run<'a>> {
    let mut runs = Vec::new();
    if !row.glyph.is_empty() {
        runs.push(Run {
            font: &slots.support_glyph,
            fill: &row.glyph_fill,
            text: format!("{} ", row.glyph),
        });
    }
    if !row.value_str.is_empty() {
        runs.push(Run {
            font: &slots.support,
            fill: &row.value_fill,
            text: row.value_str.clone(),
        });
    }
    if !row.explainer.is_empty() {
        let spacer = if !row.value_str.is_empty() || !row.glyph.is_empty() {
            " "
        } else {
            ""
        };
        runs.push(Run {
            font: &slots.support,
            fill: secondary,
            text: format!("{spacer}{}", row.explainer),
        });
    }
    runs
}

fn runs_width(runs: &[Run], m: &Measure) -> Result<f64, String> {
    runs.iter().map(|r| r.font.measure(m, &r.text, false)).sum()
}

/// Cuts the explainer (the last support run) with an ellipsis until the row fits
/// `available_width`, warning when it does. The glyph and value stay whole, so a
/// row they overflow alone leaves the explainer as `…` and still overflows.
fn fit_explainer(
    b: &Bound,
    m: &Measure,
    runs: &mut [Run],
    available_width: f64,
    diagnostics: &mut Vec<Diagnostic>,
) -> Result<bool, String> {
    let Some(support) = b.support.as_ref().filter(|s| !s.explainer.is_empty()) else {
        return Ok(false);
    };
    let Some((last, head)) = runs.split_last_mut() else {
        return Ok(false);
    };
    let room = available_width - runs_width(head, m)?;
    if last.font.measure(m, &last.text, false)? <= room {
        return Ok(false);
    }
    let spacer = if last.text.starts_with(' ') { " " } else { "" };
    let chars: Vec<char> = last.text.trim_start().chars().collect();
    let mut cut = String::new();
    for keep in (0..chars.len()).rev() {
        let kept: String = chars[..keep].iter().collect();
        cut = format!("{spacer}{}\u{2026}", kept.trim_end());
        if last.font.measure(m, &cut, false)? <= room {
            break;
        }
    }
    last.text = cut;
    diagnostics.push(Diagnostic::new(
        &codes::WARN_KPI_SUPPORT_TRUNCATED,
        [
            ("chart_id", json!(b.id)),
            ("authored_text", json!(support.explainer)),
        ],
    ));
    Ok(true)
}

/// `extra` rides before the font attributes.
fn run_tspans(runs: &[Run], extra: &Attrs) -> Vec<Node> {
    runs.iter()
        .map(|r| {
            let attrs = extra.clone().extend(r.font.attrs()).quick("fill", r.fill);
            tspan(attrs, r.text.as_str())
        })
        .collect()
}

// ── Bind ───────────────────────────────────────────────────────────────────

/// A KPI after `bind`: everything its payload, data and style decide, before
/// any width does.
pub struct Bound {
    id: String,
    variant: Variant,
    kpi: KpiChartStyle,
    slots: Slots,
    colors: Colors,
    value: ValueRun,
    label_text: String,
    support: Option<SupportRow>,
    /// The chart-root link, already resolved against the link context in Python
    /// and scheme-checked (`checked_href`), that wraps the card's value in `<a>`.
    link: Option<CardLink>,
    /// The card padding this KPI's render box was shrunk by, if any —
    /// the card rect reaches back out over it (`common::card_box`), matching
    /// production's `card_box(w, h, inset)` (`svg_utils.py:173`).
    inset: Option<crate::Padding>,
}

struct CardLink {
    url: String,
    open: crate::href::LinkOpen,
}

/// The headline glyph + prefix + number + suffix,
/// cased and formatted.
struct ValueRun {
    glyph: String,
    prefix: String,
    number: String,
    suffix: String,
    is_numeric: bool,
}

pub fn bind(payload: &Payload) -> Result<Bound, Diagnostic> {
    refuse_retired_keys(&payload.resolved)?;
    let r: Resolved =
        Deserialize::deserialize(&payload.resolved).map_err(|e| format!("kpi payload: {e}"))?;
    let kpi = &r.style.kpi;

    let Some(first) = payload.rows.first() else {
        return Err(Diagnostic::new(
            &codes::ERR_KPI_NO_DATA,
            [("chart_id", json!(r.id))],
        ));
    };
    if payload.rows.len() > 1 {
        return Err(Diagnostic::new(
            &codes::ERR_KPI_MULTIROW,
            [
                ("chart_id", json!(r.id)),
                ("row_count", json!(payload.rows.len())),
            ],
        ));
    }
    // A non-finite cell is null after the seam; the card prints its `str()` text.
    let mut row = first.clone();
    for (column, tag) in payload.cell_text.first().into_iter().flatten() {
        if tag.non_finite.is_some() {
            row.insert(column.clone(), Value::String(tag.text.clone()));
        }
    }
    let row = &row;
    let mut slots = Slots::resolve(kpi)?;
    let cell = column_cell(row, &r.value, &r.id)?;
    let board_formats = payload
        .board_style
        .as_ref()
        .and_then(|b| b.formats.as_ref());
    let sub_unit_specs = payload
        .board_style
        .as_ref()
        .and_then(|b| b.sub_unit_specs.as_ref())
        .ok_or("kpi: payload carries no board_style.sub_unit_specs (the board's plain-digit siblings of currency and number)")?;

    let (prefix, number_str, suffix, is_numeric) = format_value_parts(
        cell,
        r.format.as_ref(),
        r.format_native,
        &r.id,
        true,
        false,
        board_formats,
        sub_unit_specs,
    )?;
    // Support-row and no-tone fills use the board-level `secondary_color` token and tone
    // palette (`ResolvedStyle.secondary_color` / `.tones`), carried in the envelope's
    // `board_style` slice.
    let board = payload.board_style.as_ref();
    let secondary = board.and_then(|b| b.secondary_color.as_deref()).ok_or(
        "kpi: payload carries no board_style.secondary_color (the support row's and explainer's ink)",
    )?;
    let tones = board.and_then(|b| b.tones.as_ref());

    // The headline's rules see the raw cell; a matched rule's glyph replaces the
    // authored one and its tone colors the whole run.
    let value_styled = resolve_conditional_styles(
        r.value_rules.as_deref().unwrap_or(&[]),
        Some(cell).filter(|v| !v.is_null()),
        tones,
    )
    .map_err(|e| format!("kpi: value {e}"))?;
    if value_styled.tone_needs_board_tones {
        return Err("kpi: value conditional rule tone with no board tones".into());
    }
    // A glyph a rule draws takes the arrow font; an authored `glyph` keeps its own.
    if value_styled.glyph.is_some() {
        std::mem::swap(&mut slots.glyph, &mut slots.arrow);
    }
    let value = ValueRun {
        glyph: slots.glyph.cased(
            &value_styled.glyph.or_else(|| r.glyph.clone()).unwrap_or_default(),
        )?,
        prefix: slots.affix.cased(&prefix)?,
        number: slots.value.cased(&number_str)?,
        suffix: slots.affix.cased(&suffix)?,
        is_numeric,
    };
    // An empty label is honored as "no label": the slot stays reserved, no
    // text is emitted.
    let label_text = slots.label.cased(&r.label)?;

    let colors = resolve_colors(
        &r,
        row,
        &slots,
        kpi,
        payload.background.as_deref(),
        secondary,
        value_styled.tone_color,
    )?;
    let support = resolve_support_row(
        &r.support,
        row,
        kpi,
        &slots,
        tones,
        secondary,
        &r.id,
        board_formats,
        sub_unit_specs,
    )?;
    let (url, open) = match r.link.as_deref() {
        Some(url) => (Some(url), r.link_open),
        None => (r.text_link.as_deref(), crate::href::LinkOpen::Page),
    };
    let link = url
        .map(|url| {
            Ok::<_, String>(CardLink {
                url: crate::href::checked_href(url)?.to_string(),
                open,
            })
        })
        .transpose()?;

    Ok(Bound {
        id: r.id,
        variant: r.variant,
        kpi: r.style.kpi,
        slots,
        colors,
        value,
        label_text,
        support,
        link,
        inset: payload.padding,
    })
}

fn column_cell<'a>(
    row: &'a serde_json::Map<String, Value>,
    column: &str,
    chart_id: &str,
) -> Result<&'a Value, Diagnostic> {
    row.get(column).ok_or_else(|| {
        Diagnostic::new(
            &codes::ERR_KPI_VALUE_COLUMN_NOT_FOUND,
            [
                ("chart_id", json!(chart_id)),
                ("value_column", json!(column)),
            ],
        )
    })
}

// ── The vertical law ───────────────────────────────────────────────────────

/// Vertical slot geometry for the stacked value / label / support layout,
/// shared by `height_for_width` and `layout`.
struct Vertical {
    value_baseline: f64,
    prefix_baseline: f64,
    glyph_baseline: f64,
    label_baseline_first: f64,
    label_line_height: f64,
    support_baseline: f64,
    minimum_height: f64,
}

/// The prefix and glyph ride above the
/// value baseline by a fraction of the size gap.
fn affix_baselines(value_baseline: f64, slots: &Slots) -> (f64, f64) {
    (
        value_baseline - (slots.value.size - slots.affix.size) * AFFIX_ELEVATION,
        value_baseline - (slots.value.size - slots.glyph.size) * AFFIX_ELEVATION,
    )
}

fn vertical(b: &Bound) -> Vertical {
    let pad = &b.kpi.content_padding;
    let overflow = b.kpi.label.overflow;
    let slots = &b.slots;

    let label_line_height = slots.label.size + 4.0;
    let cap_height = slots.value.size * CAP_HEIGHT_RATIO;
    let value_baseline = pad.top + cap_height;
    let (prefix_baseline, glyph_baseline) = affix_baselines(value_baseline, slots);
    let label_cap_height = slots.label.size * CAP_HEIGHT_RATIO;
    let label_baseline_first = value_baseline + MINIMUM_TITLE_VALUE_GAP + label_cap_height;
    // A single-line overflow mode reserves one label line; the mode is
    // theme-wide, so every KPI on a board shifts by the same amount.
    let label_slot_lines = if matches!(overflow, Overflow::Clip | Overflow::Truncate) {
        1.0
    } else {
        2.0
    };
    let support_baseline = label_baseline_first + label_slot_lines * label_line_height;

    let minimum_height = if b.support.is_some() {
        support_baseline + slots.support.size * DESCENDER_RATIO + pad.bottom
    } else {
        let label_slot_bottom = label_baseline_first + (label_slot_lines - 1.0) * label_line_height;
        label_slot_bottom + slots.label.size * DESCENDER_RATIO + pad.bottom
    };
    Vertical {
        value_baseline,
        prefix_baseline,
        glyph_baseline,
        label_baseline_first,
        label_line_height,
        support_baseline,
        minimum_height,
    }
}

/// The arrangement a card is drawn in at `width`: an inline card whose one row
/// overflows the card stacks instead, unless it is a bare value that stacking
/// would overflow the same.
fn arrangement(b: &Bound, m: &Measure, width: f64) -> Result<Variant, String> {
    if b.variant != Variant::Inline {
        return Ok(b.variant);
    }
    let can_fall_back = !b.label_text.is_empty() || b.support.is_some();
    let overflows = inline_run_width(b, m)? > width - b.kpi.content_padding.horizontal();
    Ok(if can_fall_back && overflows {
        Variant::Stacked
    } else {
        Variant::Inline
    })
}

/// The height a card draws in a cell `requested` tall: a stacked card grows to
/// the minimum its slots need, inline and compact keep the cell's.
fn card_height(arrangement: Variant, vertical: &Vertical, requested: f64) -> f64 {
    match arrangement {
        Variant::Stacked => requested.max(vertical.minimum_height),
        Variant::Inline | Variant::Compact => requested,
    }
}

/// The height the card draws at `width` in a cell of the theme's
/// `default_height`, the box a card with no slot of its own takes.
pub fn height_for_width(bound: &Bound, width: f64, measure: &Measure) -> Result<f64, Diagnostic> {
    Ok(card_height(
        arrangement(bound, measure, width)?,
        &vertical(bound),
        bound.kpi.default_height,
    ))
}

// ── Width ──────────────────────────────────────────────────────────────────

/// The width of the value run, each part in its own slot font; the number is
/// measured as tabular figures, and the trailing `+2.0` mirrors the `dx="2"`
/// kern before the suffix tspan.
fn measure_value_run(b: &Bound, m: &Measure) -> Result<f64, String> {
    let (run, slots) = (&b.value, &b.slots);
    let mut width = 0.0;
    if !run.glyph.is_empty() {
        width += slots.glyph.measure(m, &format!("{} ", run.glyph), false)?;
    }
    if run.is_numeric && !run.prefix.is_empty() {
        width += slots.affix.measure(m, &run.prefix, false)?;
    }
    width += slots.value.measure(m, &run.number, true)?;
    if run.is_numeric && !run.suffix.is_empty() {
        width += slots.affix.measure(m, &run.suffix, false)? + 2.0;
    }
    Ok(width)
}

/// One `INLINE_GAP` before each slot actually emitted; the support glyph /
/// value / explainer chain reads as one joined string with no inner gap.
fn inline_run_width(b: &Bound, m: &Measure) -> Result<f64, String> {
    let mut run_width = measure_value_run(b, m)?;
    if !b.label_text.is_empty() {
        run_width += INLINE_GAP + b.slots.label.measure(m, &b.label_text, false)?;
    }
    let support_runs = b
        .support
        .as_ref()
        .map(|s| support_runs(s, &b.slots, &b.colors.secondary))
        .unwrap_or_default();
    if !support_runs.is_empty() {
        run_width += INLINE_GAP + runs_width(&support_runs, m)?;
    }
    Ok(run_width)
}

/// The top and bottom runs of the compact right column, and whether the label's
/// `data-authored-kind` rides the top or the bottom one.
struct CompactColumn<'a> {
    top: Vec<Run<'a>>,
    bottom: Vec<Run<'a>>,
    kind_on_top: bool,
    kind_on_bottom: bool,
}

fn compact_column(b: &Bound) -> CompactColumn<'_> {
    let slots = &b.slots;
    // Only the label names an authored key (`label:`), so only its <text>
    // carries the leaf kind.
    let label_runs: Vec<Run> = if b.label_text.is_empty() {
        vec![]
    } else {
        vec![Run {
            font: &slots.label,
            fill: &b.colors.label_fill,
            text: b.label_text.clone(),
        }]
    };
    let mut support_runs = b
        .support
        .as_ref()
        .map(|s| support_runs(s, slots, &b.colors.secondary))
        .unwrap_or_default();
    let has_label = !label_runs.is_empty();
    let has_support = !support_runs.is_empty();
    let (top, bottom, kind_on_top, kind_on_bottom) = match b.support.as_ref() {
        _ if has_label && has_support => (label_runs, support_runs, true, false),
        _ if has_label => (vec![], label_runs, false, true),
        // Split case: glyph + value on top, the explainer (unspaced) on the
        // value baseline.
        Some(s) if !s.value_str.is_empty() && !s.explainer.is_empty() => {
            support_runs.pop();
            (
                support_runs,
                vec![Run {
                    font: &slots.support,
                    fill: &b.colors.secondary,
                    text: s.explainer.clone(),
                }],
                false,
                false,
            )
        }
        _ => (vec![], support_runs, false, false),
    };
    CompactColumn {
        top,
        bottom,
        kind_on_top,
        kind_on_bottom,
    }
}

/// Compact's value column and right column are one rigid block under
/// `align`: the right column's x derives from the value run's own width.
fn compact_block_width(
    m: &Measure,
    value_run_width: f64,
    column: &CompactColumn,
) -> Result<f64, String> {
    let right_block_width = runs_width(&column.top, m)?.max(runs_width(&column.bottom, m)?);
    let has_right_column = !column.top.is_empty() || !column.bottom.is_empty();
    Ok(if has_right_column {
        value_run_width + INLINE_GAP + right_block_width
    } else {
        value_run_width
    })
}

/// The narrowest width that keeps the headline whole, and the width the card's
/// own arrangement asks for: the widest of value, label and support when
/// stacked, the one row when inline, the rigid block when compact.
pub fn intrinsic(bound: &Bound, measure: &Measure) -> Result<Option<IntrinsicWidth>, Diagnostic> {
    let padding = bound.kpi.content_padding.horizontal();
    let value = measure_value_run(bound, measure)?;
    let arrangement = match bound.variant {
        Variant::Inline => inline_run_width(bound, measure)?,
        Variant::Compact => compact_block_width(measure, value, &compact_column(bound))?,
        Variant::Stacked => {
            let label = if bound.label_text.is_empty() {
                0.0
            } else {
                bound
                    .slots
                    .label
                    .measure(measure, &bound.label_text, false)?
            };
            let support = bound
                .support
                .as_ref()
                .map(|s| support_runs(s, &bound.slots, &bound.colors.secondary))
                .unwrap_or_default();
            value.max(label).max(runs_width(&support, measure)?)
        }
    };
    Ok(Some(IntrinsicWidth {
        min: padding + value,
        preferred: padding + arrangement,
    }))
}

/// The title-overflow wrap, reported as
/// `(lines, truncated)`.
fn wrap_label_lines(
    b: &Bound,
    m: &Measure,
    requested_width: f64,
) -> Result<(Vec<String>, bool), String> {
    if b.label_text.is_empty() {
        return Ok((vec![], false));
    }
    let pad = &b.kpi.content_padding;
    let label = &b.slots.label;
    // `compute_title_limit`: `int(width - left - right)`, none at or below zero.
    let limit = (requested_width - pad.left - pad.right) as i64;
    let (rendered, truncated) = if limit <= 0 || requested_width <= 0.0 {
        (b.label_text.clone(), false)
    } else {
        prepare_title_text(&b.label_text, b.kpi.label.overflow, limit as f64, label, m)?
    };
    let lines: Vec<String> = rendered.lines().take(2).map(str::to_string).collect();
    Ok((
        if lines.is_empty() {
            vec![b.label_text.clone()]
        } else {
            lines
        },
        truncated,
    ))
}

/// `prepare_title_text` (title_overflow.py:33-46, 49-82): the overflow mode's
/// line budget over the wrap, joined with newlines.
fn prepare_title_text(
    text: &str,
    overflow: Overflow,
    limit: f64,
    label: &SlotFont,
    m: &Measure,
) -> Result<(String, bool), String> {
    let (max_lines, ellipsis) = match overflow {
        Overflow::Clip => (Some(1), false),
        Overflow::Truncate => (Some(1), true),
        Overflow::Wrap => (None, false),
        Overflow::WrapTwo => (Some(2), true),
    };
    let (lines, truncated) = wrap_label(
        m.fonts,
        measure::stand_in_family(&label.family),
        text,
        limit,
        label.size,
        max_lines,
        ellipsis,
    )?;
    Ok(match overflow {
        Overflow::Clip | Overflow::Truncate => (
            lines.first().cloned().unwrap_or_else(|| text.to_string()),
            truncated,
        ),
        Overflow::Wrap => (lines.join("\n"), false),
        Overflow::WrapTwo => (lines.join("\n"), truncated),
    })
}

// ── Alignment ───────────────────────────────────────────────

/// `align` is a whole-card choice, so right/center is honored
/// only when EVERY run fits — one overflowing run drops the card back to
/// left-anchored geometry rather than mixing alignments per run.
fn card_align<'a>(
    chart_id: &str,
    align: Option<&'a str>,
    available_width: f64,
    run_widths: &[f64],
    diagnostics: &mut Vec<Diagnostic>,
) -> Option<&'a str> {
    match align {
        Some(a @ ("right" | "center")) => {
            if run_widths.iter().fold(0.0_f64, |m, &w| m.max(w)) > available_width {
                diagnostics.push(Diagnostic::new(
                    &codes::WARN_KPI_ALIGN_OVERFLOW,
                    [("chart_id", json!(chart_id))],
                ));
                None
            } else {
                Some(a)
            }
        }
        other => other,
    }
}

/// X for a run under the card's effective align.
fn run_x(content_x: f64, available_width: f64, run_width: f64, align: Option<&str>) -> f64 {
    match align {
        Some("right") => content_x + available_width - run_width,
        Some("center") => content_x + (available_width - run_width) / 2.0,
        _ => content_x,
    }
}

// ── Nodes ───────────────────────────────────────────────────

/// The parts of the card in document order, the newline the markup has always
/// put between them written as text.
#[derive(Default)]
struct Parts(Vec<Node>);

fn newline() -> Node {
    Node::Chars(Chars::new("\n", Escape::Quick))
}

fn tspan(attrs: Attrs, text: impl Into<String>) -> Node {
    Node::Tspan(TextBlock { attrs, text: Chars::new(text, Escape::Quick) })
}

fn title(text: &str) -> Node {
    Node::Title(Chars::new(text, Escape::Quick))
}

impl Parts {
    fn push(&mut self, node: Node) {
        if !self.0.is_empty() {
            self.0.push(newline());
        }
        self.0.push(node);
    }

    fn append(&mut self, other: Parts) {
        if other.0.is_empty() {
            return;
        }
        if !self.0.is_empty() {
            self.0.push(newline());
        }
        self.0.extend(other.0);
    }

    /// `inner` inside the card's link when it has one.
    fn append_linked(&mut self, b: &Bound, inner: Parts) {
        let Some(link) = &b.link else {
            return self.append(inner);
        };
        let mut attrs = Attrs::new().quick("href", link.url.as_str());
        if let Some(open) = link.open.open_attr() {
            attrs = attrs.quick("data-dbt-link-open", open);
        }
        if let Some(target) = link.open.target() {
            attrs = attrs.quick("target", target);
        }
        let mut children = vec![newline()];
        children.extend(inner.0);
        children.push(newline());
        self.push(Node::Link { attrs, children });
    }
}

/// Where the card's text runs sit under its width and the height it draws.
struct Stack {
    width: f64,
    height: f64,
    vertical: Vertical,
    label_lines: Vec<String>,
    label_truncated: bool,
}

fn text_row(x: f64, y: f64, children: Vec<Node>) -> Node {
    Node::TextRuns {
        attrs: Attrs::new()
            .quick("x", py_float_str(x))
            .quick("y", py_float_str(y))
            .quick("text-anchor", "start"),
        children,
    }
}

/// The card fill reaches back out over the padding the render box was shrunk by.
fn chrome(b: &Bound, parts: &mut Parts, w: f64, h: f64) {
    let Some(fill) = b.colors.card_fill.as_deref() else {
        return;
    };
    let (x, y, w, h) = crate::common::card_box(w, h, b.inset.as_ref());
    parts.push(Node::Rect(
        Attrs::new()
            .quick("x", format_g(x))
            .quick("y", format_g(y))
            .quick("width", format_g(w))
            .quick("height", format_g(h))
            .quick("rx", format_g(b.kpi.border.radius))
            .quick("fill", fill),
    ));
}

fn value_tspan(font: &SlotFont, y: f64, fill: &str, text: String, extra: Attrs) -> Node {
    let attrs = Attrs::new().quick("y", py_float_str(y)).extend(font.attrs()).quick("fill", fill);
    tspan(attrs.extend(extra), text)
}

/// Glyph, prefix, number and suffix, each in its own slot
/// font and on its own baseline. Shared by all three variants.
fn value_tspans(
    b: &Bound,
    value_baseline: f64,
    prefix_baseline: f64,
    glyph_baseline: f64,
) -> Vec<Node> {
    let (run, slots, colors) = (&b.value, &b.slots, &b.colors);
    let mut nodes = Vec::new();
    if !run.glyph.is_empty() {
        nodes.push(value_tspan(
            &slots.glyph,
            glyph_baseline,
            &colors.glyph_fill,
            format!("{} ", run.glyph),
            Attrs::new(),
        ));
    }
    if run.is_numeric && !run.prefix.is_empty() {
        nodes.push(value_tspan(
            &slots.affix,
            prefix_baseline,
            &colors.affix_fill,
            run.prefix.clone(),
            Attrs::new(),
        ));
    }
    nodes.push(value_tspan(
        &slots.value,
        value_baseline,
        &colors.value_fill,
        run.number.clone(),
        Attrs::new(),
    ));
    if run.is_numeric && !run.suffix.is_empty() {
        nodes.push(value_tspan(
            &slots.affix,
            value_baseline,
            &colors.affix_fill,
            run.suffix.clone(),
            Attrs::new().quick("dx", "2"),
        ));
    }
    nodes
}

// ── Stacked variant ───────────────────────────────────────

fn stacked(
    b: &Bound,
    m: &Measure,
    st: &Stack,
    diagnostics: &mut Vec<Diagnostic>,
) -> Result<Parts, String> {
    let slots = &b.slots;
    let v = &st.vertical;
    let mut parts = Parts::default();
    chrome(b, &mut parts, st.width, st.height);

    // Usable content width, shared by every row's align computation: value,
    // label and support each anchor to this same right edge / centerline,
    // measuring their own run width independently.
    let content_x = b.kpi.content_padding.left;
    let available_width = st.width - content_x - b.kpi.content_padding.right;
    let value_run_width = measure_value_run(b, m)?;
    let mut support_runs = b
        .support
        .as_ref()
        .map(|s| support_runs(s, slots, &b.colors.secondary))
        .unwrap_or_default();
    let support_cut = fit_explainer(b, m, &mut support_runs, available_width, diagnostics)?;
    // Alignment is a whole-card choice, so every run on the card is measured
    // before it is decided.
    let label_widths = st
        .label_lines
        .iter()
        .map(|line| slots.label.measure(m, line, false))
        .collect::<Result<Vec<_>, _>>()?;
    let mut run_widths = vec![value_run_width];
    run_widths.extend(&label_widths);
    let support_width = if support_runs.is_empty() {
        None
    } else {
        Some(runs_width(&support_runs, m)?)
    };
    run_widths.extend(support_width);
    let align = card_align(
        &b.id,
        b.kpi.align.as_deref(),
        available_width,
        &run_widths,
        diagnostics,
    );
    let value_x = run_x(content_x, available_width, value_run_width, align);

    let value = text_row(
        value_x,
        v.value_baseline,
        value_tspans(b, v.value_baseline, v.prefix_baseline, v.glyph_baseline),
    );
    let mut linked = Parts::default();
    linked.push(value);
    parts.append_linked(b, linked);

    // Label: a reserved slot, top-aligned within it. The slot is reserved even
    // when the label is empty — only the <text> is skipped.
    if !b.label_text.is_empty() {
        let line_xs: Vec<f64> = label_widths
            .iter()
            .map(|w| run_x(content_x, available_width, *w, align))
            .collect();
        // The full text rides a <title> when the rendered text differs from
        // it, whichever overflow mode cut it.
        let mut children = Vec::new();
        if st.label_truncated {
            children.push(title(&b.label_text));
        }
        for (index, (line, line_x)) in st.label_lines.iter().zip(&line_xs).enumerate() {
            let line_y = v.label_baseline_first + index as f64 * v.label_line_height;
            children.push(newline());
            children.push(tspan(
                Attrs::new().quick("x", py_float_str(*line_x)).quick("y", py_float_str(line_y)),
                line.as_str(),
            ));
        }
        children.push(newline());
        let attrs = Attrs::new()
            .quick("x", py_float_str(line_xs[0]))
            .quick("y", py_float_str(v.label_baseline_first))
            .quick("data-authored-kind", "label")
            .quick("text-anchor", "start")
            .extend(slots.label.attrs())
            .quick("fill", b.colors.label_fill.as_str());
        parts.push(Node::TextRuns { attrs, children });
    }

    if let Some(width) = support_width {
        let support_x = run_x(content_x, available_width, width, align);
        let mut children = Vec::new();
        if let (true, Some(s)) = (support_cut, b.support.as_ref()) {
            children.push(title(&s.explainer));
        }
        children.extend(run_tspans(&support_runs, &Attrs::new()));
        parts.push(text_row(support_x, v.support_baseline, children));
    }
    Ok(parts)
}

// ── Inline variant ────────────────────────────────────────

fn inline(
    b: &Bound,
    m: &Measure,
    st: &Stack,
    diagnostics: &mut Vec<Diagnostic>,
) -> Result<Parts, String> {
    let (slots, kpi) = (&b.slots, &b.kpi);
    let pad = &kpi.content_padding;
    let (width, height) = (st.width, st.height);
    let cap_height = slots.value.size * CAP_HEIGHT_RATIO;
    // Vertically center the value's cap-box in the card.
    let value_baseline = (height + cap_height) / 2.0;
    let (prefix_baseline, glyph_baseline) = affix_baselines(value_baseline, slots);

    let support_runs = b
        .support
        .as_ref()
        .map(|s| support_runs(s, slots, &b.colors.secondary))
        .unwrap_or_default();
    let run_width = inline_run_width(b, m)?;
    let available_width = width - pad.horizontal();

    let mut parts = Parts::default();
    chrome(b, &mut parts, width, height);

    let align = card_align(
        &b.id,
        kpi.align.as_deref(),
        available_width,
        &[run_width],
        diagnostics,
    );
    let row_x = run_x(pad.left, available_width, run_width, align);
    let mut children = value_tspans(b, value_baseline, prefix_baseline, glyph_baseline);
    let baseline = Attrs::new().quick("y", py_float_str(value_baseline));
    let gap = Attrs::new().quick("dx", py_float_str(INLINE_GAP));
    if !b.label_text.is_empty() {
        let attrs = baseline
            .clone()
            .quick("data-authored-kind", "label")
            .extend(slots.label.attrs())
            .quick("fill", b.colors.label_fill.as_str())
            .extend(gap.clone());
        children.push(tspan(attrs, b.label_text.as_str()));
    }
    if !support_runs.is_empty() {
        let first = baseline.clone().extend(gap);
        children.extend(run_tspans(&support_runs[..1], &first));
        children.extend(run_tspans(&support_runs[1..], &baseline));
    }
    let mut linked = Parts::default();
    linked.push(text_row(row_x, value_baseline, children));
    parts.append_linked(b, linked);
    Ok(parts)
}

// ── Compact variant ───────────────────────────────────────

fn compact(
    b: &Bound,
    m: &Measure,
    st: &Stack,
    diagnostics: &mut Vec<Diagnostic>,
) -> Result<Parts, String> {
    let (slots, kpi) = (&b.slots, &b.kpi);
    let pad = &kpi.content_padding;
    let (width, height) = (st.width, st.height);
    let cap_height = slots.value.size * CAP_HEIGHT_RATIO;
    let value_baseline = (height + cap_height) / 2.0;
    let (prefix_baseline, glyph_baseline) = affix_baselines(value_baseline, slots);

    let has_label = !b.label_text.is_empty();
    let has_support = b
        .support
        .as_ref()
        .is_some_and(|s| !support_runs(s, slots, &b.colors.secondary).is_empty());
    let column = compact_column(b);

    let value_run_width = measure_value_run(b, m)?;
    let available_width = width - pad.horizontal();
    let block_width = compact_block_width(m, value_run_width, &column)?;
    let CompactColumn {
        top,
        bottom,
        kind_on_top,
        kind_on_bottom,
    } = column;
    let align = card_align(
        &b.id,
        kpi.align.as_deref(),
        available_width,
        &[block_width],
        diagnostics,
    );
    let block_x = run_x(pad.left, available_width, block_width, align);
    let right_column_x = block_x + value_run_width + INLINE_GAP;

    // The top line sits one top-line-font line-height above the baseline so the
    // bottom line stays flush with the value baseline: the label's when label
    // and support share the column, else a split-support piece's.
    let top_line_size = if has_label && has_support {
        slots.label.size
    } else {
        slots.support.size
    };
    let top_baseline = value_baseline - (top_line_size + 4.0);

    let mut parts = Parts::default();
    chrome(b, &mut parts, width, height);
    let mut linked = Parts::default();
    linked.push(text_row(
        block_x,
        value_baseline,
        value_tspans(b, value_baseline, prefix_baseline, glyph_baseline),
    ));
    let right_column_text =
        |parts: &mut Parts, runs: &[Run], baseline: f64, is_label: bool| {
            let extra = if is_label {
                Attrs::new().quick("data-authored-kind", "label")
            } else {
                Attrs::new()
            };
            parts.push(text_row(right_column_x, baseline, run_tspans(runs, &extra)));
        };
    if !top.is_empty() {
        right_column_text(&mut linked, &top, top_baseline, kind_on_top);
    }
    if !bottom.is_empty() {
        right_column_text(&mut linked, &bottom, value_baseline, kind_on_bottom);
    }
    parts.append_linked(b, linked);
    Ok(parts)
}

// ── Layout ─────────────────────────────────────────────────

/// `cell` is the card as drawn: its width and requested height. A stacked card
/// grows to the minimum its slots need; an inline card that falls back to stacked
/// does too, and a compact or fitting inline card keeps the requested height.
pub fn layout(bound: &Bound, cell: Rect, measure: &Measure) -> Result<Layout, Diagnostic> {
    let mut diagnostics = Vec::new();
    let (label_lines, label_truncated) = wrap_label_lines(bound, measure, cell.width)?;
    if label_truncated {
        diagnostics.push(Diagnostic::new(
            &codes::WARN_KPI_LABEL_TRUNCATED,
            [
                ("chart_id", json!(bound.id)),
                ("authored_text", json!(bound.label_text)),
            ],
        ));
    }
    let arrangement = arrangement(bound, measure, cell.width)?;
    if arrangement != bound.variant {
        diagnostics.push(Diagnostic::new(
            &codes::WARN_KPI_INLINE_VARIANT_FALLBACK_TO_STACKED,
            [
                ("chart_id", json!(bound.id)),
                ("authored_text", json!("inline")),
            ],
        ));
    }
    let vertical = vertical(bound);
    let height = card_height(arrangement, &vertical, cell.height);
    let stack = Stack {
        width: cell.width,
        height,
        vertical,
        label_lines,
        label_truncated,
    };
    let parts = match arrangement {
        Variant::Inline => inline(bound, measure, &stack, &mut diagnostics)?,
        Variant::Compact => compact(bound, measure, &stack, &mut diagnostics)?,
        Variant::Stacked => stacked(bound, measure, &stack, &mut diagnostics)?,
    };
    let pad = &bound.kpi.content_padding;
    let data_box = Rect {
        x: pad.left,
        y: pad.top,
        width: cell.width - pad.horizontal(),
        height: height - pad.top - pad.bottom,
    };
    let insets = Insets {
        top: pad.top,
        right: pad.right,
        bottom: pad.bottom,
        left: pad.left,
    };
    Ok(Layout {
        data_box,
        insets,
        nodes: vec![Node::Svg {
            extent: Extent {
                width: cell.width,
                height: height,
            },
            attrs: Attrs::new()
                .quick("xmlns", "http://www.w3.org/2000/svg")
                .quick("width", py_float_str(cell.width))
                .quick("height", py_float_str(height))
                .quick(
                    "viewBox",
                    format!("0 0 {} {}", py_float_str(cell.width), py_float_str(height)),
                ),
            children: parts.0,
        }],
        diagnostics,
        title_pos: None,
    })
}

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    let bound = bind(payload)?;
    let measure = Measure { fonts };
    let cell = Rect {
        x: 0.0,
        y: 0.0,
        width: payload.width,
        height: payload.height,
    };
    let laid_out = layout(&bound, cell, &measure)?;
    laid_out.diagnostics.into_iter().for_each(warn);
    Ok(print(&laid_out.nodes))
}

#[cfg(test)]
mod format_tests {
    use super::*;

    // Expected strings generated by running the production formatter as the
    // KPI calls it:
    //   uv run python -c "
    //     from dbt_charts.core.compile.format_value import format_kpi_parts
    //     print(format_kpi_parts(<value>, <format_input>, native=<native>,
    //                            sign_in_prefix=True))"

    /// The wire `resolve_format_parts(authored)` emits: a predefined name
    /// resolves to its spec and rides along as `raw`.
    fn wire(authored: &str, prefix: &str, suffix: &str) -> FormatSlot {
        let spec = crate::common::predefined_number_spec(authored).unwrap_or(authored);
        serde_json::from_value(json!({"spec": spec, "prefix": prefix, "suffix": suffix, "raw": authored}))
            .unwrap()
    }

    fn en_us() -> SubUnitSpecs {
        ["currency", "currency_compact"]
            .map(|raw| (raw.to_string(), "$,.2f".to_string()))
            .into_iter()
            .chain(["number", "compact"].map(|raw| (raw.to_string(), d3rs::format::SUB_UNIT_NUMBER_SPEC.to_string())))
            .collect()
    }

    /// `format_kpi_parts(..., sign_in_prefix=True)`: how the KPI calls it.
    fn kpi_lanes_under(
        value: f64,
        parts: &FormatParts,
        native: bool,
        sub_unit_specs: &SubUnitSpecs,
    ) -> Result<(String, String, String), Diagnostic> {
        let options = PartsOptions {
            native,
            sign_in_prefix: true,
            sub_unit_specs: Some(sub_unit_specs),
            ..PartsOptions::default()
        };
        let lanes = format_kpi_parts(value, parts, &options)?;
        Ok((lanes.prefix, lanes.number, lanes.suffix))
    }

    fn kpi_lanes(value: f64, parts: &FormatParts, native: bool) -> (String, String, String) {
        kpi_lanes_under(value, parts, native, &en_us()).unwrap()
    }

    // Oracle: production's `format_kpi_parts(27.6, "percent")` raises
    // `ERR-PERCENT-RANGE` (format_spec '.1%', value 27.6); `percent_number`
    // (native) and `percent_delta` (`+.1%`) take the same value.
    #[test]
    fn a_percent_format_refuses_a_value_shaped_0_to_100() {
        let refusal = kpi_lanes_under(27.6, &wire("percent", "", "").parts(), false, &en_us()).unwrap_err();
        assert_eq!(refusal.to_string(), r#"ERR-PERCENT-RANGE: format_spec=".1%", value=27.6"#);
        assert_eq!(fmt(27.6, Some("percent_number"), false).1, "27.6");
        assert_eq!(fmt(27.6, Some("percent_delta"), false).1, "+2760.0");
    }

    fn fmt(value: f64, authored: Option<&str>, native: bool) -> (String, String, String) {
        let input = authored.map(|a| wire(a, "", ""));
        kpi_lanes(value, &input.as_ref().map(FormatSlot::parts).unwrap_or_default(), native)
    }

    fn fmt_affixed(value: f64, authored: &str, prefix: &str, suffix: &str) -> (String, String, String) {
        kpi_lanes(value, &wire(authored, prefix, suffix).parts(), false)
    }

    // Oracle: production's `format_kpi_parts(v, None)` under
    // `Regional(locale="de-DE").format_locale` prints `1.234,57`, `1.234.567`
    // and `−5,13` (`format_value.py:363-372` through the active `d3_format`).
    #[test]
    fn a_value_with_no_spec_prints_in_the_board_locale() {
        let parts = FormatParts::default();
        let de = crate::number_locale::de_de();
        let number = |v: f64| {
            crate::number_locale::with_number_locale(&de, || kpi_lanes(v, &parts, false)).1
        };
        assert_eq!(number(1234.567), "1.234,57");
        assert_eq!(number(1234567.0), "1.234.567");
        assert_eq!(number(-5.125), "\u{2212}5,13");
        assert_eq!(kpi_lanes(1234.567, &parts, false).1, "1,234.57");
    }

    #[test]
    fn currency_named_format() {
        // format_kpi_parts(4820000.0, "currency", None) -> ('$', '4.82', 'M')
        assert_eq!(fmt(4_820_000.0, Some("currency"), false), ("$".into(), "4.82".into(), "M".into()));
    }

    #[test]
    fn percent_named_format() {
        // format_kpi_parts(0.2173, "percent", None) -> ('', '21.7', '%')
        assert_eq!(fmt(0.2173, Some("percent"), false), ("".into(), "21.7".into(), "%".into()));
    }

    #[test]
    fn integer_named_format() {
        // format_kpi_parts(1234.0, "integer", None) -> ('', '1,234', '')
        assert_eq!(fmt(1234.0, Some("integer"), false), ("".into(), "1,234".into(), "".into()));
    }

    #[test]
    fn percent_delta_named_format() {
        // format_kpi_parts(0.081, "percent_delta", None) -> ('', '+8.1', '%')
        assert_eq!(fmt(0.081, Some("percent_delta"), false), ("".into(), "+8.1".into(), "%".into()));
    }

    // Oracle: `format_kpi_parts(value, spec, None, sign_in_prefix=True)`. A d3
    // spec rounds an exact binary tie half up (`d3_format.format._round_half_up`,
    // `format.py:81`), a native paints `format(float(v), spec)` (`_unit_value`,
    // `predefined_formats.py:296`), which rounds the exact value half to even.
    #[test]
    fn a_d3_spec_rounds_a_tie_half_up_and_a_native_as_python_f_does() {
        for (value, spec, number) in [
            (2.5, ".0f", "3"),
            (3.5, ".0f", "4"),
            (0.5, ".0f", "1"),
            (-2.5, ".0f", "\u{2212}3"),
            (0.125, ",.2f", "0.13"),
            (-0.125, ",.2f", "\u{2212}0.13"),
            (2.675, ",.2f", "2.67"),
            (1234.5, "integer", "1,235"),
            (1233.5, "integer", "1,234"),
            (0.125, ".0%", "13"),
            (0.25, "percent_number", "0.2"),
            (2.25, "percent_number", "2.2"),
            (0.35, "percent_number", "0.3"),
            (0.125, "percent_number_delta", "+0.1"),
            (2.25, "percentage_points_delta", "+2.2"),
        ] {
            assert_eq!(fmt(value, Some(spec), false).1, number, "{value} under {spec}");
        }
    }

    /// `fixtures/kpi_format_battery.json` is production's own
    /// `format_kpi_parts` output (`tests/test_kpi_format_battery.py` keeps it
    /// current): unformatted ties and near-ties, and the sign placement law.
    #[test]
    fn the_production_format_battery_formats_byte_for_byte() {
        #[derive(Deserialize)]
        struct Case {
            format: Option<FormatSlot>,
            keep_affix_gaps: bool,
            value: String,
            repr: String,
            parts: (String, String, String),
        }
        let cases: Vec<Case> =
            serde_json::from_str(include_str!("../../../fixtures/kpi_format_battery.json")).unwrap();
        assert!(cases.len() > 1100, "{} cases", cases.len());
        let misses: Vec<String> = cases
            .iter()
            .filter_map(|c| {
                let value = f64::from_bits(u64::from_str_radix(&c.value, 16).unwrap());
                let (p, n, s, _) =
                    format_value_parts(&json!(value), c.format.as_ref(), false, "battery", true, c.keep_affix_gaps, None, &en_us())
                        .unwrap();
                let got = (p, n, s);
                (got != c.parts).then(|| {
                    format!("{} keep_affix_gaps={}: {:?} want {:?}", c.repr, c.keep_affix_gaps, got, c.parts)
                })
            })
            .collect();
        assert!(misses.is_empty(), "{} of {} differ:\n{}", misses.len(), cases.len(), misses.join("\n"));
    }

    #[test]
    fn the_sign_leads_both_prefixes_unless_placed_after_an_authored_one() {
        // format_kpi_parts(2.7e6, "+$,.2s") -> ('+$', '2.7', 'M')
        assert_eq!(fmt(2.7e6, Some("+$,.2s"), false), ("+$".into(), "2.7".into(), "M".into()));
        // format_kpi_parts(-5.5, FormatConfig(spec="+,.1f", prefix="EUR")) -> ('−EUR', '5.5', '')
        assert_eq!(fmt_affixed(-5.5, "+,.1f", "EUR", ""), ("\u{2212}EUR".into(), "5.5".into(), "".into()));
        // format_kpi_parts(-5.5, FormatConfig(spec="+$,.2s", prefix="EUR ")) -> ('EUR', '−$5.5', '')
        assert_eq!(fmt_affixed(-5.5, "+$,.2s", "EUR ", ""), ("EUR".into(), "\u{2212}$5.5".into(), "".into()));
        // No prefix: the sign stays with the digits.
        assert_eq!(fmt(-5.5, Some("+,.1f"), false), ("".into(), "\u{2212}5.5".into(), "".into()));
    }

    #[test]
    fn an_unformatted_value_follows_d3_ties_zero_sign_and_exponent() {
        // format_kpi_parts(0.125, None) -> ('', '0.13', ''); (-0.001, None) -> ('', '0', '')
        assert_eq!(fmt(0.125, None, false), ("".into(), "0.13".into(), "".into()));
        assert_eq!(fmt(-5.125, None, false), ("".into(), "\u{2212}5.13".into(), "".into()));
        assert_eq!(fmt(-0.001, None, false), ("".into(), "0".into(), "".into()));
        assert_eq!(fmt(-0.0, None, false), ("".into(), "0".into(), "".into()));
        // format_kpi_parts(1.5e300, None) -> ('', '1.5e+300', '')
        assert_eq!(fmt(1.5e300, None, false), ("".into(), "1.5e+300".into(), "".into()));
    }

    #[test]
    fn null_format_integer_falls_back_to_exact_digits() {
        // format_kpi_parts(1234.0, None, None) -> ('', '1,234', '')
        assert_eq!(fmt(1234.0, None, false), ("".into(), "1,234".into(), "".into()));
    }

    #[test]
    fn null_format_float_falls_back_to_unpadded_two_decimals() {
        // format_kpi_parts(1234.5, None, None) -> ('', '1,234.50', '')
        assert_eq!(fmt(1234.5, None, false), ("".into(), "1,234.5".into(), "".into()));
        // format_kpi_parts(-1234.5, None, None) -> ('', '−1,234.5', '')
        assert_eq!(fmt(-1234.5, None, false), ("".into(), "\u{2212}1,234.5".into(), "".into()));
    }

    #[test]
    fn inline_d3_spec_with_unrecognized_si_prefix_stays_glued() {
        // format_kpi_parts(0.081, "+$,.2s") -> ('+$', '81m', '')
        // "m" (milli) is not a magnitude char production splits out — the whole
        // SI-formatted string stays in `number`, not `suffix`.
        assert_eq!(fmt(0.081, Some("+$,.2s"), false), ("+$".into(), "81m".into(), "".into()));
    }

    #[test]
    fn currency_whole_and_percent_whole_named_formats() {
        assert_eq!(crate::common::predefined_number_spec("currency_whole"), Some("$,.0f"));
        assert_eq!(crate::common::predefined_number_spec("percent_whole"), Some(".0%"));
        assert_eq!(crate::common::predefined_number_spec("delta"), Some("+,d"));
        assert_eq!(crate::common::predefined_number_spec("not_a_name"), None);
    }

    #[test]
    fn currency_below_one_falls_back_to_currency_full() {
        // format_kpi_parts(0.67, "currency", None) -> ('$', '0.67', '')
        assert_eq!(fmt(0.67, Some("currency"), false), ("$".into(), "0.67".into(), "".into()));
    }

    #[test]
    fn currency_at_or_above_one_is_unchanged() {
        // format_kpi_parts(12.99, "currency", None) -> ('$', '13', '')
        assert_eq!(fmt(12.99, Some("currency"), false), ("$".into(), "13".into(), "".into()));
        assert_eq!(fmt(1.0, Some("currency"), false), ("$".into(), "1".into(), "".into()));
    }

    #[test]
    fn negative_currency_below_one_falls_back() {
        // format_kpi_parts(-0.67, "currency") -> ('−$', '0.67', '')
        assert_eq!(fmt(-0.67, Some("currency"), false), ("\u{2212}$".into(), "0.67".into(), "".into()));
    }

    #[test]
    fn a_sub_unit_value_never_prints_an_si_prefix() {
        // From 0.005 up: the board's `_full` sibling. Below: significant digits,
        // scientific below SUB_UNIT_SCIENTIFIC_FLOOR.
        assert_eq!(fmt(0.5, Some("number"), false), ("".into(), "0.5".into(), "".into()));
        assert_eq!(fmt(0.671, Some("number"), false), ("".into(), "0.67".into(), "".into()));
        assert_eq!(fmt(0.0023, Some("number"), false), ("".into(), "0.0023".into(), "".into()));
        assert_eq!(fmt(0.0001, Some("currency"), false), ("$".into(), "0.0001".into(), "".into()));
        assert_eq!(fmt(0.0023, Some("currency"), false), ("$".into(), "0.0023".into(), "".into()));
        assert_eq!(fmt(-1e-12, Some("number"), false), ("".into(), "\u{2212}1e-12".into(), "".into()));
    }

    #[test]
    fn a_cascaded_date_falls_back_through_the_boards_date_short() {
        let formats = json!({"date_short": {"spec": "%Y", "prefix": "FY "}, "bad": {"prefix": "x"}});
        let formats = formats.as_object().unwrap();
        let cell = Value::String("2026-03-04".into());
        let input = Some(wire("integer", "", ""));
        let got = format_value_parts(&cell, input.as_ref(), false, "c", true, false, Some(formats), &en_us()).unwrap();
        assert_eq!(got.1, "FY 2026");
        let err = time_alias("bad", Some(formats)).unwrap_err();
        assert!(err.contains("names no spec"), "{err}");
    }

    #[test]
    fn a_board_redefined_sibling_sets_the_sub_unit_spec() {
        let mut locale = en_us();
        locale.insert("number".into(), ",.1f".into());
        let input = Some(wire("number", "", ""));
        let parts = input.as_ref().map(FormatSlot::parts).unwrap_or_default();
        let got = kpi_lanes_under(0.5, &parts, false, &locale).unwrap();
        assert_eq!(got, ("".into(), "0.5".into(), "".into()));
    }

    #[test]
    fn money_pound_prefix_below_one_falls_back() {
        // format_kpi_parts(0.67, FormatConfig(spec="number", prefix="£"), None)
        //   -> ('£', '0.67', '')
        // No "$" in the resolved spec ("number" -> ".3~s") — the affix alone
        // must be enough to detect money.
        assert_eq!(fmt_affixed(0.67, "number", "\u{a3}", ""), ("\u{a3}".into(), "0.67".into(), "".into()));
    }

    #[test]
    fn money_usd_suffix_below_one_falls_back() {
        // format_kpi_parts(0.67, FormatConfig(spec="number", suffix=" USD"), None)
        //   -> ('', '0.67', 'USD')
        assert_eq!(fmt_affixed(0.67, "number", "", " USD"), ("".into(), "0.67".into(), "USD".into()));
    }

    #[test]
    fn label_case_title_matches_apply_case() {
        // apply_case("tone — negative", "title") -> "Tone — Negative"
        assert_eq!(display_text::case::apply_case("tone — negative", "title").unwrap(), "Tone — Negative");
        // apply_case("quarterly revenue", "title") -> "Quarterly Revenue"
        assert_eq!(display_text::case::apply_case("quarterly revenue", "title").unwrap(), "Quarterly Revenue");
        // apply_case("up arrow (value-row glyph, no tone)", "title")
        //   -> "Up Arrow (Value-Row Glyph, No Tone)"
        assert_eq!(
            display_text::case::apply_case("up arrow (value-row glyph, no tone)", "title").unwrap(),
            "Up Arrow (Value-Row Glyph, No Tone)"
        );
    }
}

#[cfg(test)]
mod render_tests {
    use super::*;
    use serde_json::json;

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    /// The theme's `kpi.default_height`: under the stacked minimum, over nothing else.
    const DEFAULT_HEIGHT: f64 = 40.0;
    const SANS: &str = "'dbt Sans Tabular', 'Noto Emoji', Inter, system-ui, sans-serif";
    const SERIF: &str = "'Source Serif 4', 'Noto Emoji', Georgia, 'Times New Roman', serif";

    /// A cascade-complete `SingleLineFontStyle` (`models/primitives.py`), the
    /// shape every KPI slot font resolves to.
    fn slot(family: &str, size: f64, weight: serde_json::Value, color: Option<&str>) -> Value {
        json!({
            "extends": null,
            "family": family,
            "color": color,
            "size": size,
            "weight": weight,
            "style": "normal",
            "decoration": "none",
            "case": "none"
        })
    }

    /// A stacked KPI payload in production's resolved shape: value 1234,
    /// label "Quarterly Revenue", no support, serif value/affix/glyph slots
    /// and sans label/support slots (`_base.yaml` + `clarity.yaml`).
    fn kpi_payload(variant: &str, w: f64) -> Payload {
        let resolved = json!({
            "link_open": "page",
            "id": "t",
            "chart_type": "kpi",
            "label": "Quarterly Revenue",
            "value": "revenue",
            "glyph": null,
            "value_rules": null,
            "variant": variant,
            "support": null,
            "format": {"spec": ",.0f", "raw": "integer"},
            "format_native": false,
            "link": null,
            "text_link": null,
            "resolved_channels": {},
            "style": {"kpi": {
                "variant": variant,
                "default_height": DEFAULT_HEIGHT,
                "font": slot(SANS, 11.0, json!(500.0), Some("#222222")),
                "value": {
                    "font": slot(SERIF, 24.0, json!(500.0), None),
                    "format": null,
                    "affix": {"font": slot(SERIF, 11.0, json!(500.0), None)},
                    "glyph": {
                        "font": slot(SERIF, 11.0, json!(500.0), None),
                        "arrow": {"font": slot(SANS, 16.0, json!(500.0), None)}
                    }
                },
                "label": {
                    "font": slot(SANS, 11.0, json!(500.0), None),
                    "overflow": "truncate"
                },
                "support": {
                    "font": slot(SANS, 11.0, json!(500.0), None),
                    "format": null,
                    "tone": null,
                    "glyph": {"font": slot(SANS, 11.0, json!(500.0), None)}
                },
                "content_padding": {"left": 8.0, "right": 8.0, "top": 8.0, "bottom": 8.0},
                "align": null,
                "background": null,
                "border": {"width": 0.0, "color": "transparent", "radius": 0.0}
            }}
        });
        serde_json::from_value(json!({
            "resolved": resolved,
            "rows": [{"revenue": 1234.0, "growth_pct": 0.125}],
            "width": w,
            "height": 120.0,
            "background": null,
            "fonts_dir": null,
            "padding": null,
            "board_style": { "text_mark_font": {"family": "sans-serif", "size": 11.0},
                "secondary_color": "#626366",
                "tones": {
                    "positive": "#00884d",
                    "negative": "#b3261e",
                    "warning": "#8a6d00",
                    "info": "#0b57d0"
                },
                "format_locale": crate::number_locale::en_us_wire(),
                "time_locale": crate::time_locale::en_us_wire(),
                "sub_unit_specs": {"currency": "$,.2f", "number": ",.2f"}
            }
        }))
        .expect("payload parses")
    }

    fn with_support(mut p: Payload) -> Payload {
        p.resolved["support"] = json!({
            "value": "growth_pct",
            "label": "growth",
            "format": {"spec": "+.1%", "raw": null},
            "glyph": "▲",
            "rules": null
        });
        p
    }

    fn set(p: &mut Payload, pointer: &str, v: Value) {
        *p.resolved.pointer_mut(pointer).unwrap_or_else(|| panic!("no {pointer}")) = v;
    }

    fn svg_of(p: &Payload) -> String {
        render(p, &test_fonts()).expect("renders")
    }

    fn err_of(p: &Payload) -> String {
        render(p, &test_fonts()).expect_err("refuses").to_string()
    }

    /// `family`'s `text` at `size`/`weight` in the `font_file` named.
    fn measured(font_file: &str, text: &str, size: f64, weight: f32) -> f64 {
        test_fonts().measure_at_weight(text, font_file, size, Some(weight)).unwrap()
    }

    fn attr_xs(svg: &str, tag: &str) -> Vec<f64> {
        let needle = format!("<{tag} x=\"");
        svg.match_indices(&needle)
            .map(|(i, _)| {
                let rest = &svg[i + needle.len()..];
                rest[..rest.find('"').unwrap()].parse::<f64>().unwrap()
            })
            .collect()
    }

    fn text_xs(svg: &str) -> Vec<f64> {
        attr_xs(svg, "text")
    }

    // ── The headline's ResolvedFormat ────────────────────────────────────────

    /// The painted value text of a stacked KPI whose headline `format` is
    /// `wire` and whose value is `value`.
    fn headline_text(wire: Value, value: f64) -> Result<String, String> {
        let mut p = kpi_payload("stacked", 300.0);
        set(&mut p, "/format", wire);
        p.rows[0].insert("revenue".into(), json!(value));
        let svg = render(&p, &test_fonts()).map_err(|e| e.to_string())?;
        let value_row = svg.split("</text>").next().expect("a headline row");
        let spans = value_row.split("<tspan").skip(1);
        Ok(spans
            .filter_map(|t| t.split_once('>')?.1.split_once("</tspan>"))
            .map(|(s, _)| s)
            .collect())
    }

    // Oracle: `format_kpi_parts(v, finalize_kpi_value_format(authored, v),
    // sign_in_prefix=True)` for the authored format and value of each row.
    #[test]
    fn a_headline_format_paints_as_production_finalizes_it() {
        for (wire, value, want) in [
            // finalize_kpi_value_format("currency", 0.5) -> ('$', '0.50', '')
            (json!({"spec": "$.3~s", "notation": "narrative", "raw": "currency"}), 0.5, "$0.50"),
            // {prefix: "US$"} at 5.25 bakes no spec: ('US$', '5.25', '')
            (json!({"prefix": "US$", "notation": "narrative"}), 5.25, "US$5.25"),
            // {prefix: "EUR ", sign_placement: before_prefix} at -5.5 -> ('−EUR', '5.50', '')
            (
                json!({"spec": ",.2f", "prefix": "EUR ", "sign_placement": "before_prefix"}),
                -5.5,
                "\u{2212}EUR5.50",
            ),
            // the same without a placement: ('EUR', '−5.50', '')
            (json!({"spec": ",.2f", "prefix": "EUR "}), -5.5, "EUR\u{2212}5.50"),
        ] {
            assert_eq!(headline_text(wire.clone(), value).unwrap(), want, "{wire}");
        }
    }

    // A single-value slot ignores `repeat` (`FormatConfig.repeat`,
    // `models/primitives.py:257`), so it paints as the slot without it.
    #[test]
    fn a_headline_format_repeat_paints_as_without_it() {
        let plain = json!({"spec": ",.2f", "prefix": "EUR ", "sign_placement": "before_prefix"});
        let mut repeated = plain.clone();
        repeated["repeat"] = json!("every");
        assert_eq!(headline_text(repeated, -5.5), headline_text(plain, -5.5));
    }

    #[test]
    fn a_headline_format_key_it_does_not_read_refuses_by_name() {
        let err =
            headline_text(json!({"spec": ",.0f", "raw": "integer", "bogus": 1}), 5.0).unwrap_err();
        assert!(err.contains("unknown format key \"bogus\""), "{err}");
        let err = headline_text(json!({"spec": ",.0f", "repeat": "twice"}), 5.0).unwrap_err();
        assert!(err.contains("neither anchor nor every"), "{err}");
    }

    #[test]
    fn a_support_format_key_it_does_not_read_refuses_by_name() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        p.resolved["support"]["format"] =
            json!({"spec": "+.1%", "raw": "percent_delta", "bogus": 1});
        let err = err_of(&p);
        assert!(err.contains("unknown format key \"bogus\""), "{err}");
    }

    // ── Retired shape ────────────────────────────────────────────────────────

    #[test]
    fn the_retired_style_glyph_and_affix_blocks_refuse_by_name() {
        for (key, moved_to) in [("glyph", "style.kpi.value.glyph.font"), ("affix", "style.kpi.value.affix.font")] {
            let mut p = kpi_payload("stacked", 300.0);
            p.resolved["style"]["kpi"][key] = json!({"font": slot(SANS, 11.0, json!(500.0), None), "character": null});
            let err = err_of(&p);
            assert!(err.contains(&format!("`style.kpi.{key}`")) && err.contains(moved_to), "{err}");
        }
    }

    #[test]
    fn the_retired_support_tone_refuses_by_name() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        p.resolved["support"]["tone"] = json!("positive");
        let err = err_of(&p);
        assert!(err.contains("`support.tone`") && err.contains("style.kpi.support.tone"), "{err}");
    }

    #[test]
    fn a_slot_font_line_height_refuses_by_name() {
        let mut p = kpi_payload("stacked", 300.0);
        p.resolved["style"]["kpi"]["label"]["font"]["line_height"] = json!(1.25);
        let err = err_of(&p);
        assert!(err.contains("line_height"), "{err}");
    }

    #[test]
    fn an_unresolved_slot_font_refuses_with_its_path() {
        let mut p = kpi_payload("stacked", 300.0);
        set(&mut p, "/style/kpi/support/glyph/font/size", Value::Null);
        let err = err_of(&p);
        assert!(err.contains("support.glyph.font") && err.contains("unresolved"), "{err}");
    }

    #[test]
    fn a_payload_without_the_glyph_key_refuses_by_name() {
        let mut p = kpi_payload("stacked", 300.0);
        p.resolved.as_object_mut().unwrap().remove("glyph");
        assert!(err_of(&p).contains("missing field `glyph`"));
    }

    #[test]
    fn a_payload_without_the_board_muted_ink_refuses_by_name() {
        let mut p = kpi_payload("stacked", 300.0);
        p.board_style = None;
        assert!(err_of(&p).contains("board_style"));
    }

    // ── Slot fonts ─────────────────────────────────

    #[test]
    fn every_value_tspan_carries_its_own_slot_font_and_the_text_carries_none() {
        let mut p = kpi_payload("stacked", 300.0);
        set(&mut p, "/glyph", json!("▲"));
        set(&mut p, "/format", json!({"spec": "$,.0f"}));
        let svg = svg_of(&p);
        let (value_y, prefix_y, glyph_y) = (
            py_float_str(8.0 + 24.0 * CAP_HEIGHT_RATIO),
            py_float_str(8.0 + 24.0 * CAP_HEIGHT_RATIO - (24.0 - 11.0) * AFFIX_ELEVATION),
            py_float_str(8.0 + 24.0 * CAP_HEIGHT_RATIO - (24.0 - 11.0) * AFFIX_ELEVATION),
        );
        let serif = quick_xml::escape::escape(SERIF);
        let want = format!(
            "<text x=\"8.0\" y=\"{value_y}\" text-anchor=\"start\">\
             <tspan y=\"{glyph_y}\" font-family=\"{serif}\" font-size=\"11.0\" font-weight=\"500\" fill=\"#222222\">▲ </tspan>\
             <tspan y=\"{prefix_y}\" font-family=\"{serif}\" font-size=\"11.0\" font-weight=\"500\" fill=\"#222222\">$</tspan>\
             <tspan y=\"{value_y}\" font-family=\"{serif}\" font-size=\"24.0\" font-weight=\"500\" fill=\"#222222\">1,234</tspan></text>"
        );
        assert!(svg.contains(&want), "want {want}\ngot {svg}");
    }

    #[test]
    fn a_suffix_rides_the_value_baseline_in_the_affix_font_after_a_two_pixel_kern() {
        let mut p = kpi_payload("stacked", 300.0);
        set(&mut p, "/format", json!({"spec": ".2~s", "notation": "narrative"}));
        p.rows[0].insert("revenue".into(), json!(4_820_000.0));
        let svg = svg_of(&p);
        assert!(svg.contains("font-size=\"11.0\" font-weight=\"500\" fill=\"#222222\" dx=\"2\">mn</tspan>"), "{svg}");
    }

    #[test]
    fn style_and_decoration_write_attributes_only_when_not_default() {
        let mut p = kpi_payload("stacked", 300.0);
        set(&mut p, "/style/kpi/value/font/style", json!("italic"));
        set(&mut p, "/style/kpi/value/font/decoration", json!("underline"));
        set(&mut p, "/style/kpi/label/font/decoration", json!("line-through"));
        let svg = svg_of(&p);
        assert!(svg.contains("font-weight=\"500\" font-style=\"italic\" text-decoration=\"underline\" fill="), "{svg}");
        assert!(svg.contains("font-weight=\"500\" text-decoration=\"line-through\" fill="), "{svg}");
        assert_eq!(svg.matches("font-style=").count(), 1, "{svg}");
    }

    #[test]
    fn a_weight_outside_the_css_set_paints_at_500() {
        for (weight, painted) in [
            (json!("semibold"), "500"),
            (json!(450.0), "500"),
            (json!("bold"), "bold"),
            (json!(700.0), "700"),
            (json!("300"), "300"),
        ] {
            let mut p = kpi_payload("stacked", 300.0);
            set(&mut p, "/style/kpi/label/font/weight", weight.clone());
            let svg = svg_of(&p);
            let at = svg.find("data-authored-kind=\"label\"").expect("label text");
            assert!(
                svg[at..].contains(&format!("font-weight=\"{painted}\"")),
                "{weight}: {svg}"
            );
        }
    }

    #[test]
    fn each_slot_applies_its_own_case() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        set(&mut p, "/glyph", json!("up"));
        set(&mut p, "/style/kpi/label/font/case", json!("upper"));
        set(&mut p, "/style/kpi/value/glyph/font/case", json!("upper"));
        set(&mut p, "/style/kpi/support/font/case", json!("upper"));
        set(&mut p, "/style/kpi/support/glyph/font/case", json!("lower"));
        p.resolved["support"]["glyph"] = json!("UP");
        let svg = svg_of(&p);
        assert!(svg.contains(">QUARTERLY REVENUE</tspan>"), "label: {svg}");
        assert!(svg.contains(">UP </tspan>"), "headline glyph: {svg}");
        assert!(svg.contains(">up </tspan>"), "support glyph: {svg}");
        assert!(svg.contains("> GROWTH</tspan>"), "support explainer: {svg}");
        assert!(svg.contains(">+12.5%</tspan>"), "{svg}");
    }

    #[test]
    fn an_unknown_case_refuses() {
        let mut p = kpi_payload("stacked", 300.0);
        set(&mut p, "/style/kpi/label/font/case", json!("shouting"));
        assert!(err_of(&p).contains("shouting"));
    }

    // ── Measurement ────────────────────────────────

    #[test]
    fn the_value_run_measures_each_part_in_its_own_slot_font_at_its_weight() {
        let mut p = kpi_payload("stacked", 300.0);
        set(&mut p, "/glyph", json!("▲"));
        set(&mut p, "/format", json!({"spec": "$,.0f"}));
        set(&mut p, "/style/kpi/align", json!("right"));
        set(&mut p, "/style/kpi/value/font/weight", json!(700.0));
        set(&mut p, "/style/kpi/value/glyph/font/weight", json!(300.0));
        set(&mut p, "/style/kpi/value/affix/font/weight", json!(600.0));
        let x = text_xs(&svg_of(&p))[0];
        // Serif slots measure as Source Serif 4; the number is numeric but
        // Source Serif keeps its own file for tabular figures too.
        let width = measured("Source Serif 4", "▲ ", 11.0, 300.0)
            + measured("Source Serif 4", "$", 11.0, 600.0)
            + measured("Source Serif 4", "1,234", 24.0, 700.0);
        assert!((x + width - (300.0 - 8.0)).abs() < 0.05, "x={x} width={width}");
    }

    #[test]
    fn a_non_serif_slot_measures_as_inter_and_a_numeric_run_as_dbt_sans_tabular() {
        let mut p = kpi_payload("stacked", 300.0);
        set(&mut p, "/style/kpi/align", json!("right"));
        set(&mut p, "/style/kpi/value/font", slot(SANS, 24.0, json!(500.0), None));
        let xs = text_xs(&svg_of(&p));
        let value = measured("dbt Sans Tabular", "1,234", 24.0, 500.0);
        let label = measured("Inter Variable", "Quarterly Revenue", 11.0, 500.0);
        assert!((xs[0] + value - 292.0).abs() < 0.05, "value {xs:?}");
        assert!((xs[1] + label - 292.0).abs() < 0.05, "label {xs:?}");
    }

    #[test]
    fn an_italic_slot_measures_in_the_vendored_italic() {
        let mut upright = kpi_payload("stacked", 300.0);
        set(&mut upright, "/style/kpi/align", json!("right"));
        set(&mut upright, "/style/kpi/label/font/family", json!("'Inter Variable', Inter, sans-serif"));
        let mut italic = upright.clone();
        set(&mut italic, "/style/kpi/label/font/style", json!("italic"));
        let (up, it) = (text_xs(&svg_of(&upright))[1], text_xs(&svg_of(&italic))[1]);
        let w_up = measured("Inter Variable", "Quarterly Revenue", 11.0, 500.0);
        let w_it = measured("Inter Variable Italic", "Quarterly Revenue", 11.0, 500.0);
        assert!((w_up - w_it).abs() > 0.05, "premise: the italic advances differ");
        assert!((up + w_up - 292.0).abs() < 0.05 && (it + w_it - 292.0).abs() < 0.05, "{up} {it}");
    }

    #[test]
    fn a_support_run_measures_each_tspan_in_its_own_slot_font() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        set(&mut p, "/style/kpi/align", json!("right"));
        set(&mut p, "/style/kpi/support/font/weight", json!(700.0));
        set(&mut p, "/style/kpi/support/glyph/font/weight", json!(300.0));
        let xs = text_xs(&svg_of(&p));
        let width = measured("Inter Variable", "▲ ", 11.0, 300.0)
            + measured("Inter Variable", "+12.5%", 11.0, 700.0)
            + measured("Inter Variable", " growth", 11.0, 700.0);
        assert!((xs[2] + width - 292.0).abs() < 0.05, "{xs:?} {width}");
    }

    // ── Support explainer overflow ───────

    const EXPECTED_STACKED: (f64, f64) = (74.97447128471163, 114.12158736554012);
    const EXPECTED_STACKED_SUPPORT: (f64, f64) = (74.97447128471163, 114.12158736554012);
    const EXPECTED_INLINE: (f64, f64) = (74.97447128471163, 187.09605865025176);
    const EXPECTED_INLINE_SUPPORT: (f64, f64) = (74.97447128471163, 295.1464900423406);
    const EXPECTED_COMPACT: (f64, f64) = (74.97447128471163, 187.09605865025173);
    const EXPECTED_COMPACT_SUPPORT: (f64, f64) = (74.97447128471163, 187.09605865025173);
    const LONG_EXPLAINER: &str = "of them with a public bug report latency (the rest embargoed)";

    fn support_overflow(width: f64) -> (String, Vec<Diagnostic>) {
        let mut p = with_support(kpi_payload("stacked", width));
        set(&mut p, "/support/label", json!(LONG_EXPLAINER));
        let (svg, warnings) = crate::diagnostic::collect(|| svg_of(&p));
        (svg, warnings)
    }

    #[test]
    fn a_support_explainer_wider_than_the_card_is_cut_with_an_ellipsis_and_warns() {
        let (svg, warnings) = support_overflow(200.0);
        let support = &svg[svg.rfind("<text").unwrap()..];
        let painted = &support[support.find("</title>").unwrap()..];
        assert!(painted.contains("…</tspan>") && !painted.contains("embargoed"), "{support}");
        assert!(support.contains(&format!("<title>{LONG_EXPLAINER}</title>")), "{support}");
        let cut: Vec<_> = warnings.iter().filter(|w| w.code == "WARN-KPI-SUPPORT-TRUNCATED").collect();
        assert_eq!(cut.len(), 1, "{warnings:?}");
        assert_eq!(cut[0].params["authored_text"], json!(LONG_EXPLAINER));
    }

    #[test]
    fn a_cut_support_row_fits_the_card() {
        let (svg, _) = support_overflow(200.0);
        let support = &svg[svg.rfind("<text").unwrap()..];
        let text: String = support.split("<tspan").skip(1).map(|t| t[t.find('>').unwrap() + 1..t.find("</tspan>").unwrap()].to_string()).collect();
        let width = measured("Inter Variable", "▲ ", 11.0, 500.0)
            + measured("Inter Variable", &text["▲ ".len()..], 11.0, 500.0);
        assert!(width <= 200.0 - 16.0 + 0.05, "{text:?} {width}");
    }

    #[test]
    fn a_support_explainer_that_fits_is_untouched_and_silent() {
        let (svg, warnings) = support_overflow(900.0);
        assert!(svg.contains(LONG_EXPLAINER) && !svg.contains("<title>"), "{svg}");
        assert!(warnings.iter().all(|w| w.code != "WARN-KPI-SUPPORT-TRUNCATED"), "{warnings:?}");
    }

    // ── Stacked layout and label overflow ───────

    fn tspans_in_label(svg: &str) -> Vec<String> {
        let at = svg.find("data-authored-kind=\"label\"").expect("label text");
        let end = at + svg[at..].find("</text>").unwrap();
        svg[at..end]
            .split("<tspan ")
            .skip(1)
            .map(|t| t[t.find('>').unwrap() + 1..t.rfind("</tspan>").unwrap()].to_string())
            .collect()
    }

    const LONG: &str = "Quarterly recurring revenue across every region and segment";

    #[test]
    fn truncate_paints_one_line_with_an_ellipsis_and_the_full_text_as_a_title() {
        let mut p = kpi_payload("stacked", 120.0);
        set(&mut p, "/label", json!(LONG));
        let svg = svg_of(&p);
        let lines = tspans_in_label(&svg);
        assert_eq!(lines.len(), 1, "{svg}");
        assert!(lines[0].ends_with('…'), "{lines:?}");
        assert!(svg.contains(&format!("<title>{LONG}</title>")), "{svg}");
    }

    #[test]
    fn clip_paints_one_line_without_an_ellipsis() {
        let mut p = kpi_payload("stacked", 120.0);
        set(&mut p, "/label", json!(LONG));
        set(&mut p, "/style/kpi/label/overflow", json!("clip"));
        let lines = tspans_in_label(&svg_of(&p));
        assert_eq!(lines.len(), 1);
        assert!(!lines[0].ends_with('…') && LONG.starts_with(lines[0].as_str()), "{lines:?}");
    }

    #[test]
    fn wrap_two_paints_two_lines_and_reserves_a_second_label_slot() {
        let mut p = with_support(kpi_payload("stacked", 120.0));
        set(&mut p, "/label", json!(LONG));
        set(&mut p, "/style/kpi/label/overflow", json!("wrap-two"));
        set(&mut p, "/style/kpi/content_padding/bottom", json!(8.0));
        p.height = 10.0;
        let svg = svg_of(&p);
        assert_eq!(tspans_in_label(&svg).len(), 2, "{svg}");
        let label_y = 8.0 + 24.0 * CAP_HEIGHT_RATIO + MINIMUM_TITLE_VALUE_GAP + 11.0 * CAP_HEIGHT_RATIO;
        let support_y = label_y + 2.0 * 15.0;
        assert!(svg.contains(&format!("<text x=\"8.0\" y=\"{}\" text-anchor=\"start\"><tspan", py_float_str(support_y))), "{svg}");
        // Card height: support baseline + descender + bottom padding.
        let want_h = py_float_str(support_y + 11.0 * 0.2 + 8.0);
        assert!(svg.starts_with(&format!("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"120.0\" height=\"{want_h}\"")), "{svg}");
    }

    #[test]
    fn wrap_paints_every_line_but_the_stacked_card_keeps_two() {
        let mut p = kpi_payload("stacked", 70.0);
        set(&mut p, "/label", json!(LONG));
        set(&mut p, "/style/kpi/label/overflow", json!("wrap"));
        let lines = tspans_in_label(&svg_of(&p));
        assert_eq!(lines.len(), 2, "{lines:?}");
    }

    #[test]
    fn a_no_support_card_ends_at_the_last_label_slot_descender() {
        let mut p = kpi_payload("stacked", 300.0);
        p.height = 10.0;
        let svg = svg_of(&p);
        let label_y = 8.0 + 24.0 * CAP_HEIGHT_RATIO + MINIMUM_TITLE_VALUE_GAP + 11.0 * CAP_HEIGHT_RATIO;
        let want_h = py_float_str(label_y + 11.0 * 0.2 + 8.0);
        assert!(svg.contains(&format!("height=\"{want_h}\" viewBox")), "{svg}");
    }

    #[test]
    fn an_empty_label_still_reserves_its_slot_but_paints_no_text() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        set(&mut p, "/label", json!(""));
        let svg = svg_of(&p);
        assert!(!svg.contains("data-authored-kind"), "{svg}");
        let label_y = 8.0 + 24.0 * CAP_HEIGHT_RATIO + MINIMUM_TITLE_VALUE_GAP + 11.0 * CAP_HEIGHT_RATIO;
        assert!(svg.contains(&format!("y=\"{}\" text-anchor", py_float_str(label_y + 15.0))), "{svg}");
    }

    // ── Alignment ────────────────────────────────────────────────────────────

    #[test]
    fn align_right_flushes_every_run_to_the_content_right_edge() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        set(&mut p, "/style/kpi/align", json!("right"));
        let xs = text_xs(&svg_of(&p));
        let label = measured("Inter Variable", "Quarterly Revenue", 11.0, 500.0);
        assert!((xs[1] + label - 292.0).abs() < 0.05, "{xs:?}");
    }

    #[test]
    fn align_overflow_drops_to_left_for_the_whole_card() {
        let mut p = kpi_payload("stacked", 60.0);
        set(&mut p, "/style/kpi/align", json!("right"));
        for x in text_xs(&svg_of(&p)) {
            assert_eq!(x, 8.0);
        }
    }

    #[test]
    fn run_positions_print_as_python_floats() {
        for variant in ["stacked", "inline", "compact"] {
            let mut p = with_support(kpi_payload(variant, 400.0));
            set(&mut p, "/style/kpi/content_padding/left", json!(0.0));
            let svg = svg_of(&p);
            assert!(svg.contains("<text x=\"0.0\""), "{variant}: {svg}");
        }
    }

    // ── Support row ─────────────────────────────────────────

    #[test]
    fn support_tone_paints_value_and_glyph_and_the_explainer_stays_muted() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        set(&mut p, "/style/kpi/support/tone", json!("positive"));
        let svg = svg_of(&p);
        assert!(svg.contains("fill=\"#00884d\">▲ </tspan>"), "{svg}");
        assert!(svg.contains("fill=\"#00884d\">+12.5%</tspan>"), "{svg}");
        assert!(svg.contains("fill=\"#626366\"> growth</tspan>"), "{svg}");
    }

    /// A delta-format support value is colored by its sign; `tone` is the
    /// direction (positive: up is good, negative: up is bad); warning and
    /// info stay fixed colors; a non-delta format keeps tone as a fixed color.
    #[test]
    fn delta_support_is_colored_by_sign_and_tone_is_directionality() {
        let paint = |raw: Option<&str>, tone: Option<&str>, growth: f64| {
            let mut p = with_support(kpi_payload("stacked", 300.0));
            set(&mut p, "/support/format/raw", json!(raw));
            set(&mut p, "/style/kpi/support/tone", json!(tone));
            p.rows = vec![json!({"revenue": 1234.0, "growth_pct": growth}).as_object().unwrap().clone()];
            svg_of(&p)
        };
        let (good, bad, secondary) = ("#00884d", "#b3261e", "#626366");
        for (tone, up, down) in [
            (None, good, bad),
            (Some("positive"), good, bad),
            (Some("negative"), bad, good),
        ] {
            let svg = paint(Some("percent_delta"), tone, 0.125);
            assert!(svg.contains(&format!("fill=\"{up}\">+12.5%")), "{tone:?} up: {svg}");
            assert!(svg.contains(&format!("fill=\"{up}\">▲ ")), "{tone:?} up glyph: {svg}");
            let svg = paint(Some("percent_delta"), tone, -0.125);
            assert!(svg.contains(&format!("fill=\"{down}\">−12.5%")), "{tone:?} down: {svg}");
            let svg = paint(Some("percent_delta"), tone, 0.0);
            assert!(svg.contains(&format!("fill=\"{secondary}\">+0.0%")), "{tone:?} zero: {svg}");
        }
        let svg = paint(Some("percent_delta"), Some("warning"), -0.125);
        assert!(svg.contains("fill=\"#8a6d00\">−12.5%"), "warning is fixed: {svg}");
        let svg = paint(Some("percent_number_delta"), Some("info"), 0.125);
        assert!(svg.contains("fill=\"#0b57d0\">"), "info is fixed: {svg}");
        let svg = paint(Some("percent_number_delta"), None, -0.125);
        assert!(svg.contains(&format!("fill=\"{bad}\">")), "native delta format: {svg}");
        let svg = paint(Some("percent"), Some("positive"), -0.125);
        assert!(svg.contains(&format!("fill=\"{good}\">−12.5%")), "non-delta keeps a fixed tone: {svg}");
        let svg = paint(None, None, -0.125);
        assert!(svg.contains(&format!("fill=\"{secondary}\">−12.5%")), "non-delta without tone is secondary: {svg}");
    }

    #[test]
    fn support_without_tone_or_ink_paints_the_board_muted_ink() {
        let svg = svg_of(&with_support(kpi_payload("stacked", 300.0)));
        assert!(svg.contains("fill=\"#626366\">+12.5%</tspan>"), "{svg}");
    }

    /// The value reads cf > row ink > tone > secondary; the
    /// glyph reads its rule glyph_color > cf > glyph ink > tone > row ink >
    /// secondary.
    #[test]
    fn support_ink_precedence_value_and_glyph() {
        let paint = |tone: Option<&str>, row: Option<&str>, glyph: Option<&str>| {
            let mut p = with_support(kpi_payload("stacked", 300.0));
            set(&mut p, "/style/kpi/support/tone", json!(tone));
            set(&mut p, "/style/kpi/support/font/color", json!(row));
            set(&mut p, "/style/kpi/support/glyph/font/color", json!(glyph));
            svg_of(&p)
        };
        let svg = paint(Some("positive"), Some("#111111"), Some("#222222"));
        assert!(svg.contains("fill=\"#111111\">+12.5%"), "row ink beats tone: {svg}");
        assert!(svg.contains("fill=\"#222222\">▲ "), "glyph ink beats tone: {svg}");
        let svg = paint(Some("positive"), Some("#111111"), None);
        assert!(svg.contains("fill=\"#00884d\">▲ "), "tone beats row ink for the glyph: {svg}");
        let svg = paint(None, Some("#111111"), None);
        assert!(svg.contains("fill=\"#111111\">▲ "), "row ink is the glyph's last resort before secondary: {svg}");
        assert!(svg.contains("fill=\"#626366\"> growth"), "the explainer stays secondary: {svg}");
    }

    #[test]
    fn a_rule_color_outranks_every_slot_ink_and_its_glyph_color_outranks_the_color() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        set(&mut p, "/style/kpi/support/tone", json!("positive"));
        set(&mut p, "/style/kpi/support/font/color", json!("#111111"));
        set(&mut p, "/style/kpi/support/glyph/font/color", json!("#222222"));
        p.resolved["support"]["rules"] = json!([{"gt": 0, "font": {"color": "#aa0000"}}]);
        let svg = svg_of(&p);
        assert!(svg.contains("fill=\"#aa0000\">▲ ") && svg.contains("fill=\"#aa0000\">+12.5%"), "{svg}");
        p.resolved["support"]["rules"] = json!([{"gt": 0, "font": {"color": "#aa0000"}, "glyph_color": "#00aa00"}]);
        let svg = svg_of(&p);
        assert!(svg.contains("fill=\"#00aa00\">▲ ") && svg.contains("fill=\"#aa0000\">+12.5%"), "{svg}");
    }

    #[test]
    fn the_support_glyph_uses_the_support_glyph_font_and_the_value_the_support_font() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        set(&mut p, "/style/kpi/support/glyph/font/size", json!(14.0));
        set(&mut p, "/style/kpi/support/font/weight", json!(700.0));
        let svg = svg_of(&p);
        assert!(svg.contains("font-size=\"14.0\" font-weight=\"500\" fill=\"#626366\">▲ </tspan>"), "{svg}");
        assert!(svg.contains("font-size=\"11.0\" font-weight=\"700\" fill=\"#626366\">+12.5%</tspan>"), "{svg}");
    }

    #[test]
    fn a_tone_without_board_tones_refuses_by_name() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        set(&mut p, "/style/kpi/support/tone", json!("positive"));
        p.board_style.as_mut().unwrap().tones = None;
        assert!(err_of(&p).contains("support tone"), "{}", err_of(&p));
    }

    #[test]
    fn support_without_the_rules_key_refuses_by_name() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        p.resolved["support"].as_object_mut().unwrap().remove("rules");
        assert!(err_of(&p).contains("missing field `rules`"));
    }

    #[test]
    fn a_rule_the_shared_evaluator_cannot_model_names_the_kpi_support_row() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        p.resolved["support"]["rules"] = json!([{"in": [["nested"]], "glyph": "x"}]);
        assert!(err_of(&p).starts_with("kpi: support conditional rule 'in' with a non-scalar"));
    }

    #[test]
    fn a_rule_tone_without_board_tones_refuses_by_name() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        p.resolved["support"]["rules"] = json!([{"gt": 0, "tone": "positive"}]);
        p.board_style.as_mut().unwrap().tones = None;
        assert!(err_of(&p).contains("support conditional rule tone"));
    }

    #[test]
    fn a_rule_glyph_replaces_the_authored_one_and_is_cased_in_the_glyph_slot() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        p.resolved["support"]["rules"] = json!([{"gt": 0, "glyph": "up"}]);
        set(&mut p, "/style/kpi/support/glyph/font/case", json!("upper"));
        assert!(svg_of(&p).contains(">UP </tspan>"));
    }

    #[test]
    fn headline_rules_set_the_glyph_and_tone_the_whole_value_run() {
        let mut p = kpi_payload("stacked", 300.0);
        p.resolved["value_rules"] = json!([
            {"gt": 0, "glyph": "↑", "tone": "positive"},
            {"lt": 0, "glyph": "↓", "tone": "negative"}
        ]);
        let up = svg_of(&p);
        assert!(up.contains("fill=\"#00884d\">↑ "), "{up}");
        p.rows[0].insert("revenue".into(), json!(-5.0));
        let down = svg_of(&p);
        assert!(down.contains("↓ ") && !down.contains("↑"), "{down}");
        p.rows[0].insert("revenue".into(), json!(0.0));
        assert!(!svg_of(&p).contains('↑'));
    }

    #[test]
    fn a_rule_glyph_takes_the_arrow_font_and_an_authored_glyph_keeps_its_own() {
        let mut p = kpi_payload("stacked", 300.0);
        set(&mut p, "/glyph", json!("●"));
        let authored = svg_of(&p);
        assert!(authored.contains("font-size=\"11.0\"") && !authored.contains("font-size=\"16.0\""), "{authored}");
        p.resolved["value_rules"] = json!([{"gt": 0, "glyph": "↑"}]);
        let derived = svg_of(&p);
        assert!(derived.contains("font-size=\"16.0\"") && derived.contains("↑ "), "{derived}");
    }

    #[test]
    fn headline_without_the_value_rules_key_refuses_by_name() {
        let mut p = kpi_payload("stacked", 300.0);
        p.resolved.as_object_mut().unwrap().remove("value_rules");
        assert!(err_of(&p).contains("missing field `value_rules`"));
    }

    #[test]
    fn a_headline_rule_tone_without_board_tones_refuses_by_name() {
        let mut p = kpi_payload("stacked", 300.0);
        p.resolved["value_rules"] = json!([{"gt": 0, "tone": "positive"}]);
        p.board_style.as_mut().unwrap().tones = None;
        assert!(err_of(&p).contains("value conditional rule tone"));
    }

    // ── Inline ────────────────────────────────────────────

    #[test]
    fn inline_flows_value_label_and_support_on_one_baseline_with_a_gap_before_each_slot() {
        let svg = svg_of(&with_support(kpi_payload("inline", 400.0)));
        let y = py_float_str((120.0 + 24.0 * CAP_HEIGHT_RATIO) / 2.0);
        let label = format!(
            "<tspan y=\"{y}\" data-authored-kind=\"label\" font-family=\"{}\" font-size=\"11.0\" font-weight=\"500\" fill=\"#222222\" dx=\"14.0\">Quarterly Revenue</tspan>",
            quick_xml::escape::escape(SANS)
        );
        assert!(svg.contains(&label), "{svg}");
        // The first support run carries the gap; `extra` rides before the font attributes.
        assert!(svg.contains(&format!("<tspan y=\"{y}\" dx=\"14.0\" font-family=\"{}\" font-size=\"11.0\" font-weight=\"500\" fill=\"#626366\">▲ </tspan>", quick_xml::escape::escape(SANS))), "{svg}");
        assert!(svg.contains(&format!("<tspan y=\"{y}\" font-family=\"{}\" font-size=\"11.0\" font-weight=\"500\" fill=\"#626366\">+12.5%</tspan>", quick_xml::escape::escape(SANS))), "{svg}");
        assert_eq!(svg.matches("<text").count(), 1, "{svg}");
    }

    #[test]
    fn inline_that_does_not_fit_falls_back_to_the_stacked_arrangement() {
        let svg = svg_of(&with_support(kpi_payload("inline", 150.0)));
        assert!(svg.matches("<text").count() >= 3, "stacked paints value, label and support text: {svg}");
        assert!(!svg.contains("dx=\"14.0\""), "{svg}");
    }

    #[test]
    fn a_bare_inline_value_never_falls_back() {
        let mut p = kpi_payload("inline", 20.0);
        set(&mut p, "/label", json!(""));
        let svg = svg_of(&p);
        assert!(svg.contains("height=\"120.0\""), "{svg}");
    }

    #[test]
    fn inline_right_aligns_the_whole_row_as_one_run() {
        let mut left = with_support(kpi_payload("inline", 400.0));
        let mut right = left.clone();
        set(&mut right, "/style/kpi/align", json!("right"));
        set(&mut left, "/style/kpi/align", json!(null));
        assert!(text_xs(&svg_of(&right))[0] > text_xs(&svg_of(&left))[0] + 10.0);
    }

    // ── Compact ───────────────────────────────────────────

    fn compact_texts(svg: &str) -> Vec<(f64, String)> {
        svg.match_indices("<text x=\"")
            .map(|(i, _)| {
                let rest = &svg[i + 9..];
                let x = rest[..rest.find('"').unwrap()].parse().unwrap();
                let y = rest.split("y=\"").nth(1).unwrap();
                (x, y[..y.find('"').unwrap()].to_string())
            })
            .collect()
    }

    #[test]
    fn compact_with_label_and_support_puts_the_label_above_the_support_on_the_value_baseline() {
        let svg = svg_of(&with_support(kpi_payload("compact", 400.0)));
        let texts = compact_texts(&svg);
        assert_eq!(texts.len(), 3, "{svg}");
        let value_y = (120.0 + 24.0 * CAP_HEIGHT_RATIO) / 2.0;
        assert_eq!(texts[0].1, py_float_str(value_y));
        assert_eq!(texts[1].1, py_float_str(value_y - (11.0 + 4.0)), "label: one label line-height above");
        assert_eq!(texts[2].1, py_float_str(value_y));
        assert!(svg.contains("<text x=\"") && svg.contains("data-authored-kind=\"label\" font-family="), "{svg}");
        assert_eq!(texts[1].0, texts[2].0, "one right column");
    }

    #[test]
    fn compact_with_only_support_splits_value_over_explainer() {
        let mut p = with_support(kpi_payload("compact", 400.0));
        set(&mut p, "/label", json!(""));
        let svg = svg_of(&p);
        let texts = compact_texts(&svg);
        assert_eq!(texts.len(), 3, "{svg}");
        // The split explainer is unspaced and secondary; the top line carries the glyph and value.
        assert!(svg.contains("fill=\"#626366\">growth</tspan>"), "{svg}");
        let value_y = (120.0 + 24.0 * CAP_HEIGHT_RATIO) / 2.0;
        assert_eq!(texts[1].1, py_float_str(value_y - (11.0 + 4.0)));
    }

    #[test]
    fn compact_with_only_a_label_paints_it_on_the_value_baseline() {
        let svg = svg_of(&kpi_payload("compact", 400.0));
        let texts = compact_texts(&svg);
        assert_eq!(texts.len(), 2, "{svg}");
        assert_eq!(texts[0].1, texts[1].1);
    }

    #[test]
    fn compact_right_column_starts_a_gap_after_the_measured_value_run() {
        let mut p = with_support(kpi_payload("compact", 400.0));
        set(&mut p, "/style/kpi/content_padding/left", json!(0.0));
        let texts = compact_texts(&svg_of(&p));
        let value = measured("Source Serif 4", "1,234", 24.0, 500.0);
        assert!((texts[1].0 - (value + INLINE_GAP)).abs() < 0.05, "{texts:?} {value}");
    }

    // ── Card chrome and link ─────────────────────────────────────────────────

    #[test]
    fn the_card_rect_paints_a_fill_and_never_a_stroke() {
        let mut p = kpi_payload("stacked", 300.0);
        set(&mut p, "/style/kpi/border/color", json!("#cc0000"));
        assert!(!svg_of(&p).contains("stroke="), "a border is the wrapper's to draw");
        set(&mut p, "/style/kpi/background", json!("#d9a45b"));
        let svg = svg_of(&p);
        assert!(svg.contains("fill=\"#d9a45b\"/>") && !svg.contains("stroke="), "{svg}");
    }

    #[test]
    fn a_conditional_channel_paints_the_matched_rule_and_a_gradient_its_scale() {
        let mut p = kpi_payload("stacked", 300.0);
        set(
            &mut p,
            "/resolved_channels",
            json!({
                "color": {"mode": "conditional", "data_field": "revenue",
                          "rules": [{"gt": 0, "font": {"color": "#112233"}}]},
                "background": {"mode": "gradient", "data_field": "revenue",
                               "scale": {"palette": ["#000000", "#808080", "#ffffff"], "arm_mode": "sequential"}},
            }),
        );
        let svg = svg_of(&p);
        assert!(svg.contains("fill=\"#112233\""), "{svg}");
        assert!(svg.contains("<rect x=\"0\" y=\"0\" width=\"300\" height=\"120\" rx=\"0\" fill=\"#808080\""), "one-row domain takes the middle stop: {svg}");
    }

    fn text_kpi(format: &str, cell: Value) -> Payload {
        let mut p = kpi_payload("stacked", 300.0);
        set(&mut p, "/format", json!({"spec": format, "notation": "narrative", "raw": format}));
        p.rows = vec![json!({"revenue": cell}).as_object().unwrap().clone()];
        p
    }

    /// An `email` / `url` headline prints its value unchanged, and the link
    /// resolve baked rides beside the authored one: the chart's own link wins.
    #[test]
    fn a_text_format_prints_the_value_and_links_the_baked_text_link() {
        let mut p = text_kpi("email", json!("ann@example.com"));
        set(&mut p, "/text_link", json!("mailto:ann@example.com"));
        let svg = render(&p, &test_fonts()).unwrap();
        assert!(svg.contains("ann@example.com"), "{svg}");
        assert!(svg.contains("<a href=\"mailto:ann@example.com\""), "{svg}");
        set(&mut p, "/link", json!("/who"));
        let svg = render(&p, &test_fonts()).unwrap();
        assert!(svg.contains("<a href=\"/who\""), "{svg}");
        assert!(!svg.contains("mailto:"), "{svg}");
    }

    /// The chart's `link_open` governs its own `link`; the implied `text_link` is always page.
    #[test]
    fn a_link_mode_stamps_the_chart_link_but_never_the_text_link() {
        let mut p = text_kpi("email", json!("ann@example.com"));
        set(&mut p, "/text_link", json!("mailto:ann@example.com"));
        p.resolved["link_open"] = json!("modal");
        let svg = render(&p, &test_fonts()).unwrap();
        assert!(svg.contains("<a href=\"mailto:ann@example.com\">"), "{svg}");
        assert!(!svg.contains("data-dbt-link-open"), "{svg}");
        set(&mut p, "/link", json!("/who"));
        let modal = render(&p, &test_fonts()).unwrap();
        assert!(modal.contains("<a href=\"/who\" data-dbt-link-open=\"modal\">"), "{modal}");
        p.resolved["link_open"] = json!("new_tab");
        let tab = render(&p, &test_fonts()).unwrap();
        assert!(tab.contains("<a href=\"/who\" data-dbt-link-open=\"new_tab\" target=\"_blank\">"), "{tab}");
        p.resolved["link_open"] = json!("page");
        let page = render(&p, &test_fonts()).unwrap();
        assert!(page.contains("<a href=\"/who\">") && !page.contains("data-dbt-link-open"), "{page}");
        p.resolved["link_open"] = json!("popup");
        assert!(render(&p, &test_fonts()).unwrap_err().to_string().contains("popup"));
    }

    /// Production always bakes `text_link` (null when the format implies none),
    /// so a payload without it is a wire bug, refused by name.
    #[test]
    fn a_payload_without_text_link_is_refused() {
        let mut p = text_kpi("email", json!("ann@example.com"));
        p.resolved.as_object_mut().unwrap().remove("text_link");
        let err = render(&p, &test_fonts()).expect_err("refuses");
        assert!(err.to_string().contains("text_link"), "{err}");
    }

    #[test]
    fn a_percent_format_over_a_0_100_value_is_a_coded_error() {
        let mut p = kpi_payload("stacked", 300.0);
        set(&mut p, "/format", json!({"spec": ".1%"}));
        p.rows = vec![json!({"revenue": 1234.5}).as_object().unwrap().clone()];
        let err = render(&p, &test_fonts()).expect_err("refuses");
        assert_eq!(err.code, "ERR-PERCENT-RANGE");
    }

    // A board's `style.formats` alias reaches the card already resolved to the
    // spec it names, the authored affix riding beside it with the alias as `raw`.
    #[test]
    fn a_style_formats_alias_paints_its_spec_and_affix() {
        let mut p = kpi_payload("stacked", 300.0);
        let alias = json!({"spec": ",.1f", "prefix": "~", "notation": "narrative", "raw": "mine"});
        set(&mut p, "/format", alias);
        p.rows = vec![json!({"revenue": 4820.55}).as_object().unwrap().clone()];
        let svg = svg_of(&p);
        assert!(svg.contains(">~<") && svg.contains("4,820.6"), "{svg}");
    }

    // The support row formats through the same `_format_value_parts`, so a
    // percent format over a 0-100 support value raises the same code.
    #[test]
    fn a_percent_support_over_a_0_100_value_is_a_coded_error() {
        let mut p = with_support(kpi_payload("stacked", 300.0));
        set(&mut p, "/support/format", json!({"spec": ".1%", "raw": "percent"}));
        p.rows = vec![json!({"revenue": 1234.0, "growth_pct": 27.6}).as_object().unwrap().clone()];
        let err = render(&p, &test_fonts()).expect_err("refuses");
        assert_eq!(err.code, "ERR-PERCENT-RANGE");
        assert_eq!(err.params["value"].to_string(), "27.6");
    }

    // Oracle: `kpi_value_text(27, "percent", ...)` coerces the cell through
    // `coerce_numeric_cell` to a float before `format_kpi_parts`, so the
    // refusal quotes `27.0` for an integer, `27.5` for a float and `27.0` for
    // the string `"27"`.
    #[test]
    fn a_percent_refusal_quotes_the_kpi_cell_as_a_float() {
        let quoted = |cell: Value| {
            let mut p = kpi_payload("stacked", 300.0);
            set(&mut p, "/format", json!({"spec": ".1%"}));
            p.rows = vec![json!({"revenue": cell}).as_object().unwrap().clone()];
            render(&p, &test_fonts()).expect_err("refuses").params["value"].to_string()
        };
        assert_eq!(quoted(json!(27)), "27.0");
        assert_eq!(quoted(json!(27.5)), "27.5");
        assert_eq!(quoted(json!("27")), "27.0");
    }

    #[test]
    fn card_fill_rect_reaches_back_over_the_card_padding() {
        let mut unpadded = kpi_payload("stacked", 100.0);
        set(&mut unpadded, "/style/kpi/background", json!("#d9a45b"));
        unpadded.background = Some("#ffffff".to_string());
        let inner_h = extract_svg_height(&svg_of(&unpadded));
        let mut padded = unpadded.clone();
        padded.padding = Some(crate::Padding { left: 16.0, right: 16.0, top: 16.0, bottom: 16.0 });
        let svg = svg_of(&padded);
        assert!(
            svg.contains(&format!(
                "<rect x=\"-16\" y=\"-16\" width=\"132\" height=\"{}\" rx=\"0\" fill=\"#d9a45b\"",
                format_g(inner_h + 32.0)
            )),
            "{svg}"
        );
    }

    #[test]
    fn card_fill_rect_stays_at_the_inner_box_with_no_padding_to_reach_over() {
        let mut p = kpi_payload("stacked", 100.0);
        set(&mut p, "/style/kpi/background", json!("#d9a45b"));
        p.background = Some("#ffffff".to_string());
        let svg = svg_of(&p);
        assert!(svg.contains("<rect x=\"0\" y=\"0\" width=\"100\" height=\""), "{svg}");
    }

    #[test]
    fn card_rect_prints_its_box_and_radius_through_the_g_format() {
        // `_emit_card_chrome(palette, cfg, 312.456, 80.0, inset)` with
        // inset {left: 0.5, right: 0.25, top: 0.75, bottom: 0.125}, radius
        // 4.125, card_fill "#d9a45b":
        //   <rect x="-0.5" y="-0.75" width="313.206" height="80.875" rx="4.125" fill="#d9a45b"/>
        let mut p = kpi_payload("compact", 312.456);
        p.height = 80.0;
        set(&mut p, "/style/kpi/background", json!("#d9a45b"));
        set(&mut p, "/style/kpi/border/radius", json!(4.125));
        p.background = Some("#ffffff".to_string());
        p.padding = Some(crate::Padding { left: 0.5, right: 0.25, top: 0.75, bottom: 0.125 });
        let svg = svg_of(&p);
        assert!(
            svg.contains(
                "<rect x=\"-0.5\" y=\"-0.75\" width=\"313.206\" height=\"80.875\" rx=\"4.125\" fill=\"#d9a45b\"/>"
            ),
            "{svg}"
        );
    }

    #[test]
    fn height_for_width_is_the_height_a_stacked_card_grows_to() {
        let fonts = test_fonts();
        let measure = Measure { fonts: &fonts };
        for p in [kpi_payload("stacked", 300.0), with_support(kpi_payload("stacked", 300.0))] {
            let bound = bind(&p).unwrap();
            let natural = height_for_width(&bound, 300.0, &measure).unwrap();
            for requested in [0.0, natural, natural + 40.0] {
                let cell = Rect { x: 0.0, y: 0.0, width: 300.0, height: requested };
                let laid_out = layout(&bound, cell, &measure).unwrap();
                let [Node::Svg { extent, .. }] = laid_out.nodes.as_slice() else {
                    panic!("a card is one root");
                };
                assert_eq!(extent.height, requested.max(natural));
                let Insets { top, bottom, .. } = laid_out.insets;
                assert_eq!(top + laid_out.data_box.height + bottom, extent.height);
            }
        }
    }

    #[test]
    fn an_inline_card_that_falls_back_grows_to_height_for_width() {
        let fonts = test_fonts();
        let measure = Measure { fonts: &fonts };
        let bound = bind(&with_support(kpi_payload("inline", 150.0))).unwrap();
        let cell = Rect { x: 0.0, y: 0.0, width: 150.0, height: 10.0 };
        let laid_out = layout(&bound, cell, &measure).unwrap();
        let [Node::Svg { extent, .. }] = laid_out.nodes.as_slice() else {
            panic!("a card is one root");
        };
        assert_eq!(extent.height, height_for_width(&bound, 150.0, &measure).unwrap());
    }

    /// The height `layout` draws the card in a `height`-tall cell.
    fn drawn_height(bound: &Bound, width: f64, height: f64) -> f64 {
        let fonts = test_fonts();
        let cell = Rect { x: 0.0, y: 0.0, width, height };
        let laid_out = layout(bound, cell, &Measure { fonts: &fonts }).unwrap();
        let [Node::Svg { extent, .. }] = laid_out.nodes.as_slice() else {
            panic!("a card is one root");
        };
        extent.height
    }

    #[test]
    fn height_for_width_is_the_height_layout_draws_in_a_natural_height_cell() {
        let fonts = test_fonts();
        let measure = Measure { fonts: &fonts };
        for variant in ["stacked", "inline", "compact"] {
            for supported in [false, true] {
                for width in [60.0, 150.0, 300.0, 700.0] {
                    let mut p = kpi_payload(variant, width);
                    if supported {
                        p = with_support(p);
                    }
                    let bound = bind(&p).unwrap();
                    let natural = height_for_width(&bound, width, &measure).unwrap();
                    let at = format!("{variant} supported={supported} width={width}");
                    assert_eq!(drawn_height(&bound, width, natural), natural, "{at}");
                    assert_eq!(drawn_height(&bound, width, DEFAULT_HEIGHT), natural, "{at}");
                    // The sizing path renders a card with no slot of its own at the
                    // theme's default height and reads the box back.
                    p.height = DEFAULT_HEIGHT;
                    assert_eq!(extract_svg_height(&svg_of(&p)), natural, "{at}");
                }
            }
        }
    }

    #[test]
    fn an_inline_or_compact_card_that_fits_wants_the_theme_default_height() {
        let fonts = test_fonts();
        let measure = Measure { fonts: &fonts };
        for variant in ["inline", "compact"] {
            let bound = bind(&kpi_payload(variant, 700.0)).unwrap();
            assert_eq!(height_for_width(&bound, 700.0, &measure).unwrap(), DEFAULT_HEIGHT, "{variant}");
        }
    }

    #[test]
    fn an_inline_card_that_falls_back_wants_the_stacked_height() {
        let fonts = test_fonts();
        let measure = Measure { fonts: &fonts };
        let inline = bind(&with_support(kpi_payload("inline", 150.0))).unwrap();
        let stacked = bind(&with_support(kpi_payload("stacked", 150.0))).unwrap();
        let wanted = height_for_width(&inline, 150.0, &measure).unwrap();
        assert_eq!(wanted, height_for_width(&stacked, 150.0, &measure).unwrap());
        assert!(wanted > DEFAULT_HEIGHT);
    }

    #[test]
    fn intrinsic_pins_the_headline_and_the_arrangement_per_variant() {
        let fonts = test_fonts();
        let measure = Measure { fonts: &fonts };
        let cases = [
            (kpi_payload("stacked", 300.0), EXPECTED_STACKED),
            (with_support(kpi_payload("stacked", 300.0)), EXPECTED_STACKED_SUPPORT),
            (kpi_payload("inline", 300.0), EXPECTED_INLINE),
            (with_support(kpi_payload("inline", 300.0)), EXPECTED_INLINE_SUPPORT),
            (kpi_payload("compact", 300.0), EXPECTED_COMPACT),
            (with_support(kpi_payload("compact", 300.0)), EXPECTED_COMPACT_SUPPORT),
        ];
        for (index, (p, (min, preferred))) in cases.into_iter().enumerate() {
            let got = intrinsic(&bind(&p).unwrap(), &measure).unwrap().unwrap();
            assert!(
                (got.min - min).abs() < 1e-6 && (got.preferred - preferred).abs() < 1e-6,
                "case {index}: {got:?}"
            );
        }
    }

    #[test]
    fn layout_returns_the_warnings_it_decided_instead_of_sinking_them() {
        let fonts = test_fonts();
        let measure = Measure { fonts: &fonts };
        let mut p = with_support(kpi_payload("stacked", 200.0));
        set(&mut p, "/support/label", json!(LONG_EXPLAINER));
        let bound = bind(&p).unwrap();
        let cell = Rect { x: 0.0, y: 0.0, width: 200.0, height: 0.0 };
        let (laid_out, sunk) = crate::diagnostic::collect(|| layout(&bound, cell, &measure));
        assert!(sunk.is_empty(), "{sunk:?}");
        let codes: Vec<_> = laid_out.unwrap().diagnostics.iter().map(|d| d.code).collect();
        assert_eq!(codes, ["WARN-KPI-SUPPORT-TRUNCATED"]);
    }

    fn extract_svg_height(svg: &str) -> f64 {
        let key = "height=\"";
        let start = svg.find(key).expect("svg carries a height attribute");
        let rest = &svg[start + key.len()..];
        rest[..rest.find('"').unwrap()].parse().unwrap()
    }
}
