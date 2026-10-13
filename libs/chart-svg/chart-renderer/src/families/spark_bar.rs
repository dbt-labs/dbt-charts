//! Spark-bar family renderer (native SVG, no Vega-Lite): compact horizontal
//! ranking bars with a label column, a proportional bar track, and a tabular
//! count column.
//!
//! Its layout constants not carried in the payload are engine constants owned here.

use std::collections::BTreeSet;

use serde::Deserialize;
use serde_json::{json, Value};

use crate::codes;
use crate::common::{tag_at, CellRow, CellTag, Font};
use crate::diagnostic::{py_repr, warn, Diagnostic};
use crate::svg::py_float_str;
use crate::text::FontBook;
use crate::xml::{Attrs, Xml};
use crate::{Payload, SparkBarTitle};

// Engine constants for the "+ N more" row and the text rhythm.
const MORE_ROWS_FONT_SIZE: f64 = 10.0;
const MORE_ROWS_OFFSET_Y: f64 = 12.0;
const MORE_ROWS_BOTTOM_PADDING: f64 = 8.0;
const TEXT_BASELINE_OFFSET: f64 = 4.0;
const SIDE_PADDING: f64 = 4.0;
const AVG_CHAR_WIDTH_PX: f64 = 7.0;

#[derive(Deserialize)]
struct Resolved {
    id: String,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    x: Option<String>,
    y: Option<String>,
    style: StyleWrap,
}

#[derive(Deserialize)]
struct StyleWrap {
    spark_bar: SparkBarStyle,
    title_font: Option<Font>,
}

#[derive(Deserialize)]
struct SparkBarStyle {
    min_width: f64,
    bar: BarStyle,
    label: SlotToggle,
    count: SlotToggle,
    max_bars: usize,
    font: Font,
    border: BorderStyle,
    subtitle: SubtitleStyle,
}

#[derive(Deserialize)]
struct BarStyle {
    height: f64,
    padding: f64,
    color: String,
    background: String,
}

#[derive(Deserialize)]
struct SlotToggle {
    visible: bool,
    width: f64,
}

#[derive(Deserialize)]
struct BorderStyle {
    radius: f64,
}

#[derive(Deserialize)]
struct SubtitleStyle {
    font: Font,
}

/// Named-key preference first,
/// then the first string column (y) / first numeric column (x).
fn auto_detect_fields(
    rows: &[serde_json::Map<String, Value>],
    tags: &[CellRow],
    x: Option<String>,
    y: Option<String>,
) -> (Option<String>, Option<String>) {
    let Some(first) = rows.first() else {
        return (x, y);
    };
    let y_field = y.or_else(|| {
        first
            .keys()
            .find(|k| matches!(k.to_lowercase().as_str(), "value" | "label" | "category" | "name"))
            .cloned()
            .or_else(|| {
                first
                    .iter()
                    .find(|(_, v)| matches!(v, Value::String(_)))
                    .map(|(k, _)| k.clone())
            })
    });
    let x_field = x.or_else(|| {
        first
            .keys()
            .find(|k| matches!(k.to_lowercase().as_str(), "frequency" | "count" | "freq" | "n" | "total"))
            .cloned()
            .or_else(|| {
                first
                    .iter()
                    .find(|(k, v)| numeric_cell(v, tag_at(tags, 0, k)).is_some())
                    .map(|(k, _)| k.clone())
            })
    });
    (x_field, y_field)
}

/// A row's magnitude cell as f64. Only real JSON numbers count —
/// production's `normalize_scalar_for_json` returns a str unchanged, so a
/// VARCHAR magnitude column FAILS validation there and must fail here too
/// (the harness serializes Decimal as a number, never a string). A null the wire
/// tagged non-finite is that float.
fn numeric_cell(v: &Value, tag: Option<&CellTag>) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::Null => tag.and_then(|tag| tag.non_finite),
        _ => None,
    }
}

/// The value's magnitude as a fraction of `max_value`, and
/// whether it falls left of the midline. A non-finite value is the null rule:
/// no bar. Unsigned layouts clamp negatives to zero.
fn signed_fraction(value: f64, max_value: f64, signed: bool) -> (f64, bool) {
    if !value.is_finite() {
        return (0.0, false);
    }
    if !signed {
        let clamped = value.min(max_value).max(0.0);
        return (if max_value > 0.0 { clamped / max_value } else { 0.0 }, false);
    }
    let clamped = value.min(max_value).max(-max_value);
    (if max_value > 0.0 { clamped.abs() / max_value } else { 0.0 }, clamped < 0.0)
}

/// Fail closed on a missing
/// x, a typo'd column, a non-numeric value, or an all-NULL column — the
/// four routes to a silent all-zero chart. NULLs alone are sparse data.
fn validate_value_field(
    chart_id: &str,
    x_field: Option<&str>,
    rows: &[serde_json::Map<String, Value>],
    tags: &[CellRow],
) -> Result<(), Diagnostic> {
    if rows.is_empty() {
        return Ok(());
    }
    let not_numeric = |field: &str, reason: String| {
        Diagnostic::new(
            &codes::ERR_SPARK_BAR_VALUE_NOT_NUMERIC,
            [("chart_id", json!(chart_id)), ("field", json!(field)), ("reason", json!(reason))],
        )
    };
    let Some(xf) = x_field else {
        return Err(not_numeric(
            "<none>",
            "no x is authored and no numeric column could be auto-detected".into(),
        ));
    };
    if !rows.iter().any(|r| r.contains_key(xf)) {
        let available: BTreeSet<&String> = rows.iter().flat_map(|r| r.keys()).collect();
        return Err(Diagnostic::new(
            &codes::ERR_SPARK_BAR_VALUE_FIELD_NOT_FOUND,
            [
                ("chart_id", json!(chart_id)),
                ("field", json!(xf)),
                ("available", json!(available)),
            ],
        ));
    }
    let mut has_numeric = false;
    for (i, row) in rows.iter().enumerate() {
        match row.get(xf) {
            None => continue,
            Some(Value::Null) if tag_at(tags, i, xf).is_none() => continue,
            Some(v) => {
                if matches!(v, Value::Bool(_)) || numeric_cell(v, tag_at(tags, i, xf)).is_none() {
                    return Err(not_numeric(xf, format!("row value {} is not numeric", py_repr(v))));
                }
                has_numeric = true;
            }
        }
    }
    if !has_numeric {
        return Err(not_numeric(xf, "the column holds no numeric values".into()));
    }
    Ok(())
}

/// Production's count formatting keys on the PYTHON
/// type, which the JSON numeric type preserves: an integral FLOAT prints
/// `str(int(v))`, plain and ungrouped; any other float (fractional or
/// non-finite) prints `d3_format(",.1f")`; an int prints `d3_format(",d")`.
fn format_count(v: &Value, tag: Option<&CellTag>) -> Result<String, String> {
    let float = match v {
        Value::Number(n) if n.is_i64() || n.is_u64() => {
            return crate::number_locale::d3_format(",d", n.as_f64().unwrap_or(0.0));
        }
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        // A missing/NULL magnitude renders as production's `count_value = 0`
        // (an int) does.
        Value::Null => match tag.and_then(|tag| tag.non_finite) {
            Some(non_finite) => non_finite,
            None => return Ok("0".to_string()),
        },
        _ => 0.0,
    };
    if float.is_finite() && float.fract() == 0.0 {
        Ok(format!("{}", float as i64))
    } else {
        crate::number_locale::d3_format(",.1f", float)
    }
}

/// The width-tiered title font `resolve()` always bakes: production asserts it
/// before branching on whether the chart is titled and reads its weight, size
/// and family unconditionally (`spark_bar.py:417-423`), so an absent slot
/// refuses even for an untitled chart. Size is the whole-pixel `int(size)`.
struct TitleFont<'a> {
    size: i64,
    weight: String,
    family: &'a str,
}

fn title_font_of(title_font: Option<&Font>) -> Result<TitleFont<'_>, String> {
    let font = title_font.ok_or("spark_bar: style.title_font absent (theme required)")?;
    if font.weight.is_none() {
        return Err("spark_bar: style.title_font.weight absent (theme required)".into());
    }
    Ok(TitleFont {
        size: font.size.ok_or("spark_bar: style.title_font.size absent (theme required)")? as i64,
        weight: font.weight_str(),
        family: font
            .family
            .as_deref()
            .ok_or("spark_bar: style.title_font.family absent (theme required)")?,
    })
}

/// The title block above the bars as `(height, first baseline)`, before a row's
/// shift: none when untitled, and a subtitle adds the
/// title's whole-pixel font size.
fn title_block(
    title: Option<&str>,
    subtitle: Option<&str>,
    title_size: i64,
    geometry: &SparkBarTitle,
) -> (f64, f64) {
    let SparkBarTitle { height, baseline_y } = *geometry;
    match (title, subtitle) {
        (None, _) => (0.0, baseline_y),
        (Some(_), None) => (height, baseline_y),
        (Some(_), Some(_)) => (height + title_size as f64, baseline_y),
    }
}

#[derive(Deserialize)]
struct TitleOnly {
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    style: TitleFontStyle,
}

#[derive(Deserialize)]
struct TitleFontStyle {
    title_font: Option<Font>,
}

/// A titled spark_bar's `(ascent, tail)` in the row's title band, measured from
/// its top edge (`title_band.py:133-139`): the first baseline, and the block's
/// height past it.
pub(crate) fn title_geometry(resolved: &Value, geometry: &SparkBarTitle) -> Result<(f64, f64), String> {
    let r = TitleOnly::deserialize(resolved).map_err(|e| format!("spark_bar title: {e}"))?;
    let title_font = title_font_of(r.style.title_font.as_ref())?;
    let (height, baseline) = title_block(r.title.as_deref(), r.subtitle.as_deref(), title_font.size, geometry);
    Ok((baseline, height - baseline))
}

pub fn render(payload: &Payload, _fonts: &FontBook) -> Result<String, Diagnostic> {
    let r: Resolved = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("spark_bar: resolved payload: {e}"))?;
    let sc = &r.style.spark_bar;
    let rows = &payload.rows;
    let tags = &payload.cell_text;

    // `spark_bar.py:383`, `:387`: the body ink is asserted and the muted ink
    // read with no fallback; the body size and family are the inherited
    // `charts.font` slots every painted label uses (`:244-245`, `:271-272`).
    let text_color = sc
        .font
        .color
        .as_deref()
        .ok_or("spark_bar: style.spark_bar.font.color absent (theme required)")?;
    let body_family = sc
        .font
        .family
        .as_deref()
        .ok_or("spark_bar: style.spark_bar.font.family absent (theme required)")?;
    let font_size = sc
        .font
        .size
        .ok_or("spark_bar: style.spark_bar.font.size absent (theme required)")?;
    let secondary_color = payload
        .board_style
        .as_ref()
        .ok_or("spark_bar: board_style absent (theme required)")?
        .muted
        .as_deref()
        .ok_or("spark_bar: board_style.muted absent (theme required)")?;
    let title_geometry_cfg = payload
        .board_style
        .as_ref()
        .and_then(|b| b.spark_bar_title)
        .ok_or("spark_bar: payload carries no board_style.spark_bar_title (the title block's height and baseline)")?;

    let (x_field, y_field) = auto_detect_fields(rows, tags, r.x.clone(), r.y.clone());
    validate_value_field(&r.id, x_field.as_deref(), rows, tags)?;

    let visible = &rows[..rows.len().min(sc.max_bars)];
    let num_bars = visible.len();
    let row_height = sc.bar.height + sc.bar.padding;
    // The envelope always carries the slot width (production's `width is
    // None -> preferred_width` arm is unreachable through the harness); an
    // explicit 0-width slot floors to min_width via the same max() as
    // production, never to preferred_width.
    let chart_width = payload.width.max(sc.min_width);

    let title = r.title.as_deref();
    let subtitle = r.subtitle.as_deref();
    let title_font = title_font_of(r.style.title_font.as_ref())?;
    let title_size = title_font.size;
    let (mut title_height, title_baseline) = title_block(title, subtitle, title_size, &title_geometry_cfg);
    // `spark_bar.py:427-431`: the row's shifted title band is added to the card,
    // so the bars keep their own room.
    let shift = payload.title_shift;
    title_height += shift.body_dy;
    let mut chart_height = if payload.height > 0.0 {
        payload.height + shift.body_dy
    } else {
        title_height + num_bars as f64 * row_height + sc.bar.padding
    };

    let left_padding = if sc.label.visible { sc.label.width } else { SIDE_PADDING };
    let right_padding = if sc.count.visible { sc.count.width } else { SIDE_PADDING };
    let bar_area_width = (chart_width - left_padding - right_padding).max(20.0);

    // Scale by the largest finite magnitude; a negative anywhere puts every bar
    // on a shared midline.
    let finite_values: Vec<f64> = x_field
        .as_deref()
        .map(|xf| {
            visible
                .iter()
                .enumerate()
                .filter_map(|(i, row)| row.get(xf).and_then(|v| numeric_cell(v, tag_at(tags, i, xf))))
                .filter(|v| v.is_finite())
                .collect()
        })
        .unwrap_or_default();
    let signed_layout = finite_values.iter().any(|v| *v < 0.0);
    let mut max_value = finite_values.iter().fold(0.0_f64, |m, v| m.max(v.abs()));
    if max_value == 0.0 {
        max_value = 1.0;
    }

    let current_y = if title.is_some() { title_height } else { 0.0 };
    let more_count = rows.len() - num_bars;
    let more_y = current_y + num_bars as f64 * row_height + MORE_ROWS_OFFSET_Y;
    // "+ N more" indicator when max_bars truncates and, matching production
    // exactly, the indicator's own bottom edge REPLACES chart_height even when
    // an explicit height was passed.
    if more_count > 0 {
        chart_height = more_y + MORE_ROWS_BOTTOM_PADDING;
    }

    let title_case = match title {
        Some(title_text) => {
            // Board-level style.title.font.case, same transform the chart and
            // table renderers apply; an absent case refuses by name.
            let case = payload
                .board_style
                .as_ref()
                .and_then(|b| b.title_case.as_deref())
                .ok_or("spark_bar: board_style.title.font.case absent (theme required)")?;
            Some(if case != "none" {
                crate::case::apply_case(title_text, case)?
            } else {
                title_text.to_string()
            })
        }
        None => None,
    };
    let placeholder_style = if payload.is_placeholder {
        Some(
            payload
                .board_style
                .as_ref()
                .and_then(|b| b.placeholder.as_ref())
                .ok_or("spark_bar: a placeholder chart needs board_style.placeholder")?,
        )
    } else {
        None
    };

    let mut xml = Xml::new();
    xml.open(
        "svg",
        Attrs::new()
            .set("xmlns", "http://www.w3.org/2000/svg")
            .set("width", py_float_str(chart_width))
            .set("height", py_float_str(chart_height))
            .set("viewBox", format!("0 0 {} {}", py_float_str(chart_width), py_float_str(chart_height))),
    );
    if let Some(style) = placeholder_style {
        xml.open("g", Attrs::new().set("opacity", py_float_str(style.opacity)));
    }
    xml.text("\n");

    if let Some(display) = &title_case {
        let title_baseline_y = title_baseline + shift.title_dy;
        xml.leaf(
            "text",
            Attrs::new()
                .set("x", "0")
                .set("y", py_float_str(title_baseline_y))
                .set("font-size", title_size.to_string())
                .set("font-weight", title_font.weight.as_str())
                .set("fill", text_color)
                .set("font-family", title_font.family)
                .set("data-authored-kind", "title"),
            display,
        );
        if let Some(subtitle_text) = subtitle {
            let sub_size = sc
                .subtitle
                .font
                .size
                .ok_or("spark_bar: style.spark_bar.subtitle.font.size absent (theme required)")?;
            xml.leaf(
                "text",
                Attrs::new()
                    .set("x", "0")
                    .set("y", py_float_str(title_baseline_y + title_size as f64))
                    .set("font-size", py_float_str(sub_size))
                    .set("fill", secondary_color)
                    .set("font-family", body_family)
                    .set("data-authored-kind", "subtitle"),
                subtitle_text,
            );
        }
    }

    let mut truncated_labels = 0;
    let bar_height = (sc.bar.height as i64).to_string();
    let radius = py_float_str(sc.border.radius);
    // `max(chart_width - ..., 20)`: the floor is the int 20, which prints bare.
    let bar_area_width_attr =
        if bar_area_width > 20.0 { py_float_str(bar_area_width) } else { "20".to_string() };
    for (i, row) in visible.iter().enumerate() {
        let row_y = current_y + i as f64 * row_height;
        let y_cell = y_field.as_deref().and_then(|yf| row.get(yf));
        let y_tag = y_field.as_deref().and_then(|yf| tag_at(tags, i, yf));
        let label_value = match (y_cell, y_tag) {
            // Python `str(value)` of a non-finite cell (`nan`, `NaN`, `-Infinity`).
            (Some(Value::Null), Some(tag)) => tag.text.clone(),
            (Some(Value::String(s)), _) => s.clone(),
            // Production is `str(row.get(y_field, ""))`: a PRESENT null key
            // returns None and stringifies to the literal "None"; a MISSING
            // key hits the "" default.
            (Some(Value::Null), None) => "None".to_string(),
            // Python str(bool) capitalizes — production paints "True"/"False".
            (Some(Value::Bool(b)), _) => if *b { "True".into() } else { "False".into() },
            (None, _) => {
                if y_field.is_some() { String::new() } else { format!("Item {}", i + 1) }
            }
            (Some(other), _) => other.to_string(),
        };
        let count_cell = x_field.as_deref().and_then(|xf| row.get(xf));
        let count_tag = x_field.as_deref().and_then(|xf| tag_at(tags, i, xf));
        let count_value = count_cell.and_then(|v| numeric_cell(v, count_tag)).unwrap_or(0.0);
        let (fraction, is_negative) = signed_fraction(count_value, max_value, signed_layout);
        let (bar_width, fill_x) = if signed_layout {
            let half_width = bar_area_width / 2.0;
            let bar_width = fraction * half_width;
            (bar_width, left_padding.trunc() + if is_negative { half_width - bar_width } else { half_width })
        } else {
            (fraction * bar_area_width, left_padding.trunc())
        };

        // Char-count truncation (production divides label.width by the
        // avg_char_width_px engine constant — no font measurement here).
        let max_label_chars = (sc.label.width / AVG_CHAR_WIDTH_PX) as usize;
        let display_label = if label_value.chars().count() > max_label_chars {
            truncated_labels += 1;
            let cut: String = label_value.chars().take(max_label_chars.saturating_sub(2)).collect();
            format!("{cut}...")
        } else {
            label_value
        };

        // Production hands the row renderer int(bar_height) — baselines
        // derive from the truncated height, not the raw theme float.
        let baseline_h = sc.bar.height.trunc();
        if sc.label.visible {
            let label_y = row_y + baseline_h / 2.0 + TEXT_BASELINE_OFFSET;
            xml.leaf(
                "text",
                Attrs::new()
                    .set("x", "0")
                    .set("y", format!("{label_y:.1}"))
                    .set("font-size", py_float_str(font_size))
                    .set("fill", text_color)
                    .set("font-family", body_family),
                &display_label,
            );
        }

        let bar_x = left_padding as i64;
        let bar = |x: String, width: String, fill: &str| {
            Attrs::new()
                .set("x", x)
                .set("y", format!("{row_y:.1}"))
                .set("width", width)
                .set("height", bar_height.clone())
                .set("fill", fill.to_string())
                .set("rx", radius.clone())
        };
        xml.empty("rect", bar(bar_x.to_string(), bar_area_width_attr.clone(), &sc.bar.background));
        if bar_width > 0.0 {
            xml.empty("rect", bar(format!("{fill_x:.1}"), format!("{bar_width:.1}"), &sc.bar.color));
        }

        if sc.count.visible {
            let count_x = chart_width - SIDE_PADDING;
            let count_y = row_y + baseline_h / 2.0 + TEXT_BASELINE_OFFSET;
            xml.leaf(
                "text",
                Attrs::new()
                    .set("x", format!("{count_x:.1}"))
                    .set("y", format!("{count_y:.1}"))
                    .set("font-size", py_float_str(font_size))
                    .set("fill", text_color)
                    .set("text-anchor", "end")
                    .set("font-family", body_family)
                    .set("style", "font-variant-numeric: tabular-nums lining-nums;"),
                &format_count(count_cell.unwrap_or(&Value::Null), count_tag)?,
            );
        }
    }

    if truncated_labels > 0 {
        warn(Diagnostic::new(
            &codes::WARN_SPARK_LABEL_TRUNCATED,
            [("chart_id", json!(r.id)), ("truncation_count", json!(truncated_labels))],
        ));
    }

    if more_count > 0 {
        xml.leaf(
            "text",
            Attrs::new()
                .set("x", py_float_str(chart_width / 2.0))
                .set("y", format!("{more_y:.1}"))
                .set("font-size", py_float_str(MORE_ROWS_FONT_SIZE))
                .set("fill", secondary_color)
                .set("text-anchor", "middle")
                .set("font-style", "italic")
                .set("font-family", body_family)
                .set("style", "font-variant-numeric: tabular-nums lining-nums;"),
            &format!("+ {more_count} more"),
        );
    }

    xml.text("\n");
    if let Some(style) = placeholder_style {
        xml.close("g");
        placeholder_overlay(&mut xml, style, chart_width, chart_height, &sc.font)?;
    }
    xml.close("svg");
    Ok(xml.finish())
}

/// The overlay's font weight as Python's `escape_attr` prints it: a keyword as
/// written, an int bare, a float with its `.0`.
fn overlay_weight(weight: &Value) -> Result<String, String> {
    match weight {
        Value::String(s) => Ok(s.clone()),
        Value::Number(n) if n.is_i64() || n.is_u64() => Ok(n.to_string()),
        Value::Number(n) => Ok(py_float_str(n.as_f64().ok_or("spark_bar: placeholder font weight is not a number")?)),
        other => Err(format!("spark_bar: placeholder font weight {other} is neither a keyword nor a number")),
    }
}

fn placeholder_overlay(
    xml: &mut Xml,
    style: &crate::PlaceholderStyle,
    width: f64,
    height: f64,
    font: &Font,
) -> Result<(), Diagnostic> {
    let overlay = &style.overlay;
    let text = match font.case.as_deref() {
        Some(case) if case != "none" => crate::case::apply_case(&overlay.text, case)?,
        _ => overlay.text.clone(),
    };
    xml.open("g", Attrs::new().set("class", "placeholder-overlay"));
    xml.empty(
        "rect",
        Attrs::new()
            .set("class", "dbt-pointer-inert")
            .set("x", "0")
            .set("y", "0")
            .set("width", py_float_str(width))
            .set("height", py_float_str(height))
            .set("fill", &overlay.background),
    );
    xml.leaf(
        "text",
        Attrs::new()
            .set("class", "dbt-pointer-inert")
            .set("x", py_float_str(width / 2.0))
            .set("y", py_float_str(height / 2.0))
            .set("text-anchor", "middle")
            .set("dominant-baseline", "middle")
            .set("font-family", font.family.as_deref().ok_or("spark_bar: style.spark_bar.font.family is unresolved")?)
            .set("font-size", py_float_str(overlay.font.size))
            .set("font-weight", overlay_weight(&overlay.font.weight)?)
            .set("fill", &overlay.font.color),
        &text,
    );
    xml.close("g");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn base_payload(rows: Vec<Value>) -> Payload {
        let resolved = json!({
            "id": "t",
            "chart_type": "spark_bar",
            "title": "",
            "subtitle": "",
            "x": "revenue",
            "y": "region",
            "style": {
                "spark_bar": {
                    "preferred_width": 200.0,
                    "min_width": 150.0,
                    "bar": {"height": 18.0, "padding": 2.0, "color": "#203c65", "background": "#e5e7eb"},
                    "label": {"visible": true, "width": 80.0},
                    "count": {"visible": true, "width": 50.0},
                    "max_bars": 10,
                    "font": {"family": "Inter, sans-serif", "color": "#222222", "size": 11.0},
                    "border": {"radius": 8.0},
                    "subtitle": {"font": {"size": 10.0}}
                },
                "title_font": {"family": "Inter, sans-serif", "color": "#494A4C", "size": 11.0, "weight": 600.0}
            }
        });
        serde_json::from_value(json!({
            "resolved": resolved,
            "rows": rows,
            "width": 168.0,
            "height": 82.0,
            "background": null,
            "fonts_dir": null,
            "padding": null,
            "board_style": {
                "muted": "#626366", "tones": null, "title_case": "title",
                "format_locale": crate::number_locale::en_us_wire(),
                "time_locale": crate::time_locale::en_us_wire(),
                "spark_bar_title": {"height": 24.0, "baseline_y": 16.0},
                "placeholder": {
                    "opacity": 0.4,
                    "overlay": {
                        "text": "add data", "background": "rgba(255, 255, 255, 0.5)",
                        "font": {"size": 16.0, "weight": 500.0, "color": "#666666"}
                    }
                }
            }
        }))
        .expect("payload parses")
    }

    fn fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    // Pinned against production's live render of styling_0/region_ranking
    // (168×82 slot, 4 rows): row pitch 20 (bar 18 + padding 2), label
    // baseline row_y + 9 + 4, bar track at x=80 (label.width) with
    // bar_area_width 38 (168 − 80 − 50), fill scaled to the max row
    // (654000/812000 × 38 = 30.6), counts end-anchored at 164 (width −
    // side_padding 4) formatted with thousands grouping.
    #[test]
    fn region_ranking_geometry_matches_production() {
        let svg = render(
            &base_payload(vec![
                json!({"region": "West", "revenue": 812000}),
                json!({"region": "East", "revenue": 654000}),
                json!({"region": "Central", "revenue": 431000}),
                json!({"region": "South", "revenue": 298000}),
            ]),
            &fonts(),
        )
        .expect("renders");
        assert!(svg.contains("<rect x=\"80\" y=\"0.0\" width=\"38.0\" height=\"18\" fill=\"#e5e7eb\" rx=\"8.0\"/>"), "bar track:\n{svg}");
        assert!(svg.contains("width=\"30.6\" height=\"18\" fill=\"#203c65\""), "East fill scales to max:\n{svg}");
        assert!(svg.contains("y=\"33.0\""), "second row baseline at 20 + 9 + 4:\n{svg}");
        assert!(svg.contains(">812,000</text>"), "grouped count:\n{svg}");
        assert!(svg.contains("x=\"164.0\""), "count end-anchored at width - side_padding:\n{svg}");
        assert!(svg.contains("height=\"82.0\""), "explicit slot height wins:\n{svg}");
    }

    // max_bars truncation paints the "+ N more" indicator and rewrites the
    // canvas height from the indicator's own bottom edge — even past an
    // explicit slot height (the indicator assigns chart_height
    // unconditionally in this arm).
    #[test]
    fn max_bars_truncation_paints_more_indicator_and_rewrites_height() {
        let mut p = base_payload(
            (0..13).map(|i| json!({"region": format!("R{i}"), "revenue": 100 - i})).collect(),
        );
        p.height = 82.0;
        let svg = render(&p, &fonts()).expect("renders");
        assert!(svg.contains("+ 3 more"), "10 of 13 rows painted:\n{svg}");
        assert!(!svg.contains(">R10<"), "rows past max_bars must not paint:\n{svg}");
        // more_y = 10*20 + 12 = 212; height = 212 + 8 = 220 — the slot's 82
        // is overwritten exactly as production does.
        assert!(
            svg.contains("height=\"220.0\""),
            "indicator bottom edge rewrites the canvas height past the slot:\n{svg}"
        );
    }

    // Production's count formatting keys on the Python numeric type
    // — an integral FLOAT prints str(int(v)) — plain,
    // no grouping — while an int prints grouped. Verified against a live
    // production render (812000.0 -> "812000"; 812000 -> "812,000").
    #[test]
    fn integral_float_counts_print_ungrouped() {
        let svg = render(
            &base_payload(vec![
                json!({"region": "West", "revenue": 812000.0}),
                json!({"region": "East", "revenue": 654000}),
            ]),
            &fonts(),
        )
        .expect("renders");
        assert!(svg.contains(">812000</text>"), "integral float stays ungrouped:\n{svg}");
        assert!(svg.contains(">654,000</text>"), "int groups:\n{svg}");
    }

    // A VARCHAR magnitude column fails closed: production's
    // normalize_scalar_for_json returns a str unchanged, so validation
    // raises rather than silently rendering zero-width bars.
    #[test]
    fn numeric_looking_string_magnitude_fails_closed() {
        let err = render(
            &base_payload(vec![json!({"region": "West", "revenue": "812000"})]),
            &fonts(),
        )
        .unwrap_err();
        assert_eq!(err.code, "ERR-SPARK-BAR-VALUE-NOT-NUMERIC");
        assert_eq!(err.params["reason"], "row value '812000' is not numeric");
    }

    // The title path: board_style.title_case transforms the authored title
    // — the subtitle paints the board muted ink one
    // title-size below the title baseline, and the rows shift down by
    // title_height 24 + title size.
    #[test]
    fn title_and_subtitle_case_ink_and_band() {
        let mut p = base_payload(vec![json!({"region": "West", "revenue": 10})]);
        p.resolved["title"] = json!("revenue by region");
        p.resolved["subtitle"] = json!("fiscal year");
        let svg = render(&p, &fonts()).expect("renders");
        assert!(
            svg.contains(">Revenue by Region</text>"),
            "board title_case transforms the title (minor word lowercase):\n{svg}"
        );
        assert!(
            svg.contains("y=\"27.0\"") && svg.contains("fill=\"#626366\""),
            "subtitle at title_baseline 16 + title size 11, muted ink:\n{svg}"
        );
        // title_height 24 + title size 11 = 35 -> first label baseline 35 + 9 + 4.
        assert!(
            svg.contains("y=\"48.0\""),
            "rows shift below the subtitled title band:\n{svg}"
        );
    }

    // A config override of the title block moves the painted title and the rows
    // below it together.
    #[test]
    fn the_payloads_title_geometry_sizes_and_paints_the_title() {
        let mut p = base_payload(vec![json!({"region": "West", "revenue": 10})]);
        p.resolved["title"] = json!("revenue");
        p.board_style.as_mut().unwrap().spark_bar_title =
            Some(crate::SparkBarTitle { height: 40.0, baseline_y: 30.0 });
        let svg = render(&p, &fonts()).expect("renders");
        assert!(svg.contains("<text x=\"0\" y=\"30.0\""), "title baseline: {svg}");
        // First label baseline: title_height 40 + 9 + 4.
        assert!(svg.contains("y=\"53.0\""), "rows start below the title block: {svg}");
    }

    // A placeholder fades the chart and lays the overlay over it, closing the SVG.
    #[test]
    fn a_placeholder_fades_the_content_and_paints_the_overlay() {
        let plain = render(&base_payload(vec![json!({"region": "West", "revenue": 10})]), &fonts()).unwrap();
        let mut p = base_payload(vec![json!({"region": "West", "revenue": 10})]);
        p.is_placeholder = true;
        let svg = render(&p, &fonts()).expect("renders");
        let (open, rest) = plain.split_at(plain.find('>').unwrap() + 1);
        let content = rest.strip_suffix("</svg>").unwrap();
        assert!(svg.starts_with(&format!("{open}<g opacity=\"0.4\">{content}</g><g class=\"placeholder-overlay\">")), "{svg}");
        assert!(svg.ends_with(
            "font-size=\"16.0\" font-weight=\"500.0\" fill=\"#666666\">add data</text></g></svg>"
        ), "{svg}");
        assert!(svg.contains("<rect class=\"dbt-pointer-inert\" x=\"0\" y=\"0\" width=\"168.0\" height=\"82.0\" fill=\"rgba(255, 255, 255, 0.5)\"/>"), "{svg}");
    }

    // The first numeric column is x, a non-finite cell included.
    #[test]
    fn auto_detected_x_is_the_first_numeric_column_even_when_non_finite() {
        let mut p = base_payload(vec![
            json!({"region": "West", "ratio": {"non_finite": "inf"}, "orders": 5}),
            json!({"region": "East", "ratio": 1.5, "orders": 7}),
        ]);
        p.resolved["x"] = Value::Null;
        p.resolved["y"] = Value::Null;
        let svg = render(&p, &fonts()).expect("renders");
        assert!(svg.contains(">Infinity</text>") && svg.contains(">1.5</text>"), "{svg}");
    }

    // The inline-edit hooks: the title and subtitle runs name the authored key
    // they came from.
    #[test]
    fn title_and_subtitle_carry_their_authored_kind() {
        let mut p = base_payload(vec![json!({"region": "West", "revenue": 10})]);
        p.resolved["title"] = json!("revenue");
        p.resolved["subtitle"] = json!("fiscal year");
        let svg = render(&p, &fonts()).expect("renders");
        assert!(svg.contains("data-authored-kind=\"title\">Revenue</text>"), "{svg}");
        assert!(svg.contains("data-authored-kind=\"subtitle\">fiscal year</text>"), "{svg}");
    }

    // Any negative value puts every bar on a shared midline: a negative grows
    // left from it, a positive right.
    #[test]
    fn a_negative_value_puts_the_bars_on_a_shared_midline() {
        let svg = render(
            &base_payload(vec![
                json!({"region": "West", "revenue": 40}),
                json!({"region": "East", "revenue": -20}),
            ]),
            &fonts(),
        )
        .expect("renders");
        // bar area 38 wide at x=80: midline 99, half 19; West fills 19 right
        // of it, East half that, ending at the midline.
        assert!(svg.contains("<rect x=\"99.0\" y=\"0.0\" width=\"19.0\""), "{svg}");
        assert!(svg.contains("<rect x=\"89.5\" y=\"20.0\" width=\"9.5\""), "{svg}");
    }

    // A non-finite value draws no bar and prints d3's text for it.
    #[test]
    fn a_non_finite_value_draws_no_bar_and_prints_its_text() {
        let svg = render(
            &base_payload(vec![
                json!({"region": "West", "revenue": 40}),
                json!({"region": "East", "revenue": {"non_finite": "inf"}}),
                json!({"region": "North", "revenue": {"non_finite": "nan"}}),
            ]),
            &fonts(),
        )
        .expect("renders");
        assert!(svg.contains(">Infinity</text>") && svg.contains(">NaN</text>"), "{svg}");
        assert_eq!(svg.matches("fill=\"#203c65\"").count(), 1, "only West fills: {svg}");
    }

    // Fail-closed validation: a wholly non-numeric x column is the
    // cartesian-order authoring mistake, never a silent all-zero chart.
    #[test]
    fn non_numeric_value_field_errors() {
        let resolved_rows = vec![json!({"region": "West", "revenue": "a lot"})];
        let err = render(&base_payload(resolved_rows), &fonts()).unwrap_err();
        assert_eq!(err.code, "ERR-SPARK-BAR-VALUE-NOT-NUMERIC");
        assert_eq!(err.params["reason"], "row value 'a lot' is not numeric");
    }

    fn refusal(rows: Vec<Value>, edit: impl FnOnce(&mut Payload)) -> crate::diagnostic::Diagnostic {
        let mut payload = base_payload(rows);
        edit(&mut payload);
        render(&payload, &fonts()).unwrap_err()
    }

    #[test]
    fn a_missing_value_field_lists_the_available_columns() {
        let d = refusal(vec![json!({"region": "West", "revenue": 1})], |p| {
            p.resolved["x"] = json!("nope");
        });
        assert_eq!(d.code, "ERR-SPARK-BAR-VALUE-FIELD-NOT-FOUND");
        assert_eq!(
            serde_json::to_value(&d.params).unwrap(),
            json!({"chart_id": "t", "field": "nope", "available": ["region", "revenue"]})
        );
    }

    #[test]
    fn no_x_authored_reports_the_none_field() {
        let d = refusal(vec![json!({"region": "West"})], |p| p.resolved["x"] = Value::Null);
        assert_eq!(d.code, "ERR-SPARK-BAR-VALUE-NOT-NUMERIC");
        assert_eq!(d.params["field"], "<none>");
        assert_eq!(
            d.params["reason"],
            "no x is authored and no numeric column could be auto-detected"
        );
    }

    #[test]
    fn an_all_null_value_column_holds_no_numeric_values() {
        let d = refusal(vec![json!({"region": "West", "revenue": null})], |_| {});
        assert_eq!(d.code, "ERR-SPARK-BAR-VALUE-NOT-NUMERIC");
        assert_eq!(d.params["reason"], "the column holds no numeric values");
    }

    #[test]
    fn truncated_labels_warn_once_with_their_count() {
        let long = "a label far too long for the label column";
        let payload = base_payload(vec![
            json!({"region": long, "revenue": 1}),
            json!({"region": long, "revenue": 2}),
            json!({"region": "ok", "revenue": 3}),
        ]);
        let (svg, warnings) = crate::diagnostic::collect(|| render(&payload, &fonts()));
        svg.unwrap();
        assert_eq!(warnings.len(), 1);
        assert_eq!(warnings[0].code, "WARN-SPARK-LABEL-TRUNCATED");
        assert_eq!(warnings[0].params["truncation_count"], 2);
    }

    // Every theme-supplied read refuses by name when the payload omits it —
    // production asserts or requires each (spark_bar.py:383, 387, 417-423,
    // 481-484), so a missing one is a broken cascade, not a default to paint.
    fn refused_by(edit: impl FnOnce(&mut Payload), expected: &str) {
        let mut p = base_payload(vec![json!({"region": "West", "revenue": 10})]);
        p.resolved["title"] = json!("Revenue");
        p.resolved["subtitle"] = json!("fiscal year");
        edit(&mut p);
        let message = render(&p, &fonts()).unwrap_err().to_string();
        assert_eq!(message, format!("spark_bar: {expected} absent (theme required)"));
    }

    #[test]
    fn an_absent_title_font_refuses_even_when_untitled() {
        let mut p = base_payload(vec![json!({"region": "West", "revenue": 10})]);
        p.resolved["style"].as_object_mut().unwrap().remove("title_font");
        let message = render(&p, &fonts()).unwrap_err().to_string();
        assert_eq!(message, "spark_bar: style.title_font absent (theme required)");
    }

    #[test]
    fn an_absent_title_font_size_refuses_by_name() {
        refused_by(
            |p| { p.resolved["style"]["title_font"].as_object_mut().unwrap().remove("size"); },
            "style.title_font.size",
        );
    }

    #[test]
    fn an_absent_title_font_weight_refuses_by_name() {
        refused_by(
            |p| { p.resolved["style"]["title_font"].as_object_mut().unwrap().remove("weight"); },
            "style.title_font.weight",
        );
    }

    #[test]
    fn an_absent_title_font_family_refuses_by_name() {
        refused_by(
            |p| { p.resolved["style"]["title_font"].as_object_mut().unwrap().remove("family"); },
            "style.title_font.family",
        );
    }

    #[test]
    fn a_subtitled_title_with_no_subtitle_font_size_refuses_by_name() {
        refused_by(
            |p| {
                p.resolved["style"]["spark_bar"]["subtitle"]["font"]
                    .as_object_mut()
                    .unwrap()
                    .remove("size");
            },
            "style.spark_bar.subtitle.font.size",
        );
    }

    #[test]
    fn the_subtitle_font_size_is_read_only_when_a_subtitle_paints() {
        let mut p = base_payload(vec![json!({"region": "West", "revenue": 10})]);
        p.resolved["title"] = json!("Revenue");
        p.resolved["style"]["spark_bar"]["subtitle"]["font"]
            .as_object_mut()
            .unwrap()
            .remove("size");
        render(&p, &fonts()).expect("a title-only spark_bar never reads the subtitle size");
    }

    #[test]
    fn an_absent_body_font_slot_refuses_by_name() {
        for slot in ["color", "family", "size"] {
            refused_by(
                |p| { p.resolved["style"]["spark_bar"]["font"].as_object_mut().unwrap().remove(slot); },
                &format!("style.spark_bar.font.{slot}"),
            );
        }
    }

    #[test]
    fn an_absent_board_muted_ink_refuses_by_name() {
        refused_by(|p| p.board_style = None, "board_style");
        refused_by(
            |p| p.board_style.as_mut().unwrap().muted = None,
            "board_style.muted",
        );
    }

    #[test]
    fn an_absent_board_title_case_refuses_by_name() {
        refused_by(
            |p| p.board_style.as_mut().unwrap().title_case = None,
            "board_style.title.font.case",
        );
    }

    #[test]
    fn title_and_subtitle_paint_the_theme_supplied_fonts() {
        let mut p = base_payload(vec![json!({"region": "West", "revenue": 10})]);
        p.resolved["title"] = json!("Revenue");
        p.resolved["subtitle"] = json!("fiscal year");
        p.resolved["style"]["title_font"] =
            json!({"family": "Serif X", "size": 14.0, "weight": 700.0});
        p.resolved["style"]["spark_bar"]["subtitle"]["font"]["size"] = json!(9.0);
        let svg = render(&p, &fonts()).expect("renders");
        assert!(
            svg.contains("font-size=\"14\" font-weight=\"700\" fill=\"#222222\" font-family=\"Serif X\" data-authored-kind=\"title\">Revenue"),
            "title paints the baked title_font:\n{svg}"
        );
        assert!(
            svg.contains("y=\"30.0\" font-size=\"9.0\" fill=\"#626366\""),
            "subtitle at baseline 16 + title size 14, subtitle.font.size 9:\n{svg}"
        );
    }
}
