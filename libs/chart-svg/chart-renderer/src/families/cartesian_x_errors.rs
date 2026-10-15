//! The ERR codes production raises while it builds a cartesian x axis, decided
//! from the resolved x type (`x_type::resolve_cartesian_x_type`) and the
//! authored axis, in the order `build_cartesian_x_encoding` raises them.

use serde::Deserialize;
use serde_json::{json, Map, Value};

use super::table_dates::parse_temporal;
use super::temporal::{
    civil_from_days, days_in_month, days_from_civil, floor_to_bucket_start, infer_vega_type, is_time_format, normalize_labeled_temporal,
    TimeUnit, VegaType,
};
use super::x_type::{parse_date, py_cell_key, resolve_cartesian_x_type, resolve_filled_x, scaffold_rows, Grain, XAxis, XResolution};
use crate::codes;
use crate::common::{AxisStyle, PyKey};
use crate::diagnostic::{py_repr, Diagnostic};

type Row = Map<String, Value>;

#[derive(Deserialize, Default)]
struct Mark {
    curve: Option<String>,
}

#[derive(Deserialize)]
struct Style {
    axis_x: AxisStyle,
    axis_y: Option<AxisStyle>,
    line_mark: Option<Mark>,
    area_mark: Option<Mark>,
}

#[derive(Deserialize)]
struct Facts {
    id: String,
    chart_type: String,
    x: Option<String>,
    y: Option<Value>,
    color: Option<String>,
    orientation: Option<String>,
    style: Style,
    #[serde(default)]
    wide_measures: Vec<String>,
    #[serde(default)]
    panel_axes: Vec<super::multiples::PartitionAxis>,
}

/// `dash` is production's em dash, kept out of the source as an escape.
const DASH: &str = "\u{2014}";

/// `is_utc_safe_ordinal_date`: `YYYY-MM` or a real `YYYY-MM-DD`.
fn utc_safe_date(s: &str) -> bool {
    let b = s.as_bytes();
    let digits = |r: std::ops::Range<usize>| b.get(r).is_some_and(|d| d.iter().all(u8::is_ascii_digit));
    if !(digits(0..4) && b.get(4) == Some(&b'-') && digits(5..7)) {
        return false;
    }
    let year = s[..4].parse::<i32>().unwrap_or(0);
    let month = s[5..7].parse::<u32>().unwrap_or(0);
    if !(1..=12).contains(&month) {
        return false;
    }
    match b.len() {
        7 => true,
        10 if b[7] == b'-' && digits(8..10) => (1..=days_in_month(year, month)).contains(&s[8..10].parse::<u32>().unwrap_or(0)),
        _ => false,
    }
}

/// `_parse_label_value_date`: an authored `labels.values` entry is an ISO date
/// or datetime. The basic and week forms `fromisoformat` also reads are not
/// modeled, so they refuse by name rather than raise.
fn label_value_date(value: &Value) -> Result<(), Diagnostic> {
    let invalid = || Diagnostic::new(&codes::ERR_LABEL_VALUES_INVALID_DATE, [("value", value.clone())]);
    let Some(text) = value.as_str() else { return Err(invalid()) };
    match parse_temporal(text) {
        Ok(Some(_)) => Ok(()),
        Err(_) => Err(invalid()),
        Ok(None) => {
            let iso_like = text.get(..4).is_some_and(|y| y.bytes().all(|b| b.is_ascii_digit()))
                && text.bytes().all(|b| b.is_ascii_digit() || matches!(b, b'-' | b'W' | b'T' | b':' | b' ' | b'.' | b'+' | b'Z'));
            if iso_like && text.len() >= 8 {
                return Err(format!("labels.values entry {text:?} is an ISO form this port does not model").into());
            }
            Err(invalid())
        }
    }
}

/// `reads_as_number`: whether d3 can read a tick value as a number.
fn reads_as_number(value: &Value) -> bool {
    match value {
        Value::Number(_) | Value::Bool(_) => true,
        Value::String(s) => {
            let text = s.trim();
            if text.is_empty() {
                return true;
            }
            let lower = text.to_ascii_lowercase();
            let word = lower.trim_start_matches(['+', '-']);
            if text.contains('_') || matches!(word, "nan" | "inf" | "infinity") {
                return false;
            }
            text.parse::<f64>().is_ok()
        }
        _ => false,
    }
}

/// `ERR-LABEL-VALUES-NOT-TEMPORAL`: authored `labels.values` only filter dates
/// a non-temporal axis can parse as UTC.
fn label_values_not_temporal(rows: &[Row], field: &str) -> Result<(), Diagnostic> {
    for cell in rows.iter().filter_map(|row| row.get(field)).filter(|v| !v.is_null()) {
        if !cell.as_str().is_some_and(utc_safe_date) {
            return Err(Diagnostic::new(
                &codes::ERR_LABEL_VALUES_NOT_TEMPORAL,
                [
                    ("cause", json!(format!("it contains {}, which isn't a UTC-safe ISO date", py_repr(cell)))),
                    ("field", json!(field)),
                    (
                        "remedy",
                        json!("Use date-only ISO dates (YYYY-MM-DD or YYYY-MM) or date objects in the query, or remove labels.values."),
                    ),
                ],
            ));
        }
    }
    Ok(())
}

/// `_gate_time_format`: a time spec over a non-temporal axis paints `Invalid
/// Date` for any cell that is not a date. Dates reach the wire as ISO strings,
/// so any ISO date or datetime string counts as one: a deliberate superset of
/// production, which refuses a datetime-shaped `str`. A bucket label such as
/// `Jan 2024` is not a date.
fn time_format(spec: &str, setting: &str, field: &str, rows: &[Row], remedy: Option<&str>) -> Result<(), Diagnostic> {
    let iso_date = |s: &str| s.as_bytes().get(4) == Some(&b'-') && s.as_bytes().get(7) == Some(&b'-') && parse_date(s).is_some();
    let is_date = |cell: &Value| cell.as_str().is_some_and(|s| utc_safe_date(s) || iso_date(s));
    let Some(offender) = rows.iter().filter_map(|row| row.get(field)).find(|v| !v.is_null() && !is_date(v)) else { return Ok(()) };
    Err(Diagnostic::new(
        &codes::ERR_TIME_FORMAT_NOT_TEMPORAL,
        [
            ("field", json!(field)),
            ("fmt", json!(spec)),
            ("found", json!(py_repr(offender))),
            ("remedy", json!(remedy.unwrap_or("Use a number format for numbers, or query the field as a date."))),
            ("setting", json!(setting)),
        ],
    ))
}

/// `gate_label_format`: a number spec cannot read a non-numeric category axis.
fn label_format(facts: &Facts, field: &str, vl_type: VegaType, rows: &[Row], remedy: Option<&str>) -> Result<(), Diagnostic> {
    gate_label_format(facts.style.axis_x.labels.format.as_ref(), "axis_x.labels.format", field, vl_type, rows, remedy)
}

/// `gate_label_format`.
fn gate_label_format(
    format: Option<&crate::common::FormatSlot>,
    setting: &str,
    field: &str,
    vl_type: VegaType,
    rows: &[Row],
    remedy: Option<&str>,
) -> Result<(), Diagnostic> {
    let Some(spec) = format.map(|f| f.spec.as_str()) else { return Ok(()) };
    if is_time_format(spec) {
        return if vl_type == VegaType::Temporal { Ok(()) } else { time_format(spec, setting, field, rows, remedy) };
    }
    if vl_type == VegaType::Quantitative {
        return Ok(());
    }
    let numeric = rows.iter().filter_map(|row| row.get(field)).filter(|v| !v.is_null()).all(reads_as_number);
    if vl_type != VegaType::Temporal && numeric {
        return Ok(());
    }
    let default_remedy = if vl_type == VegaType::Temporal {
        format!("This axis is temporal {DASH} d3's number grammar doesn't read over dates. Author a time spec here (e.g. \"%b %Y\").")
    } else {
        format!("axis_x addresses the dimension channel {DASH} author the number format on style.axis_y.labels.format, the measure axis.")
    };
    Err(Diagnostic::new(
        &codes::ERR_LABEL_FORMAT_AXIS_MISMATCH,
        [
            ("field", json!(field)),
            ("fmt", json!(spec)),
            ("remedy", json!(remedy.map_or(default_remedy, str::to_string))),
            ("setting", json!(setting)),
        ],
    ))
}

/// `apply_x_tick_cadence`'s bare-step gate.
fn tick_step(facts: &Facts, field: &str, vl_type: VegaType, remedy: Option<&str>) -> Result<(), Diagnostic> {
    let ticks = &facts.style.axis_x.ticks;
    if ticks.step.is_none() || ticks.time_unit.is_some() || vl_type == VegaType::Quantitative {
        return Ok(());
    }
    let default_remedy = if vl_type == VegaType::Temporal {
        "Author ticks.time_unit alongside step (e.g. time_unit: year, step: 5) to name a calendar cadence.".to_string()
    } else {
        format!("This axis has a discrete domain, which has no numeric tick interval {DASH} remove ticks.step.")
    };
    Err(Diagnostic::new(
        &codes::ERR_TICKS_STEP_NOT_QUANTITATIVE,
        [
            ("field", json!(field)),
            ("remedy", json!(remedy.map_or(default_remedy, str::to_string))),
            ("vl_type", json!(vl_type.name())),
        ],
    ))
}

fn tu_name(tu: TimeUnit) -> &'static str {
    match tu {
        TimeUnit::Year => "year",
        TimeUnit::YearQuarter => "yearquarter",
        TimeUnit::YearMonth => "yearmonth",
        TimeUnit::YearWeek => "yearweek",
        TimeUnit::YearMonthDate => "yearmonthdate",
    }
}

/// `complete_ordinal_time_series`: placing is not aggregating, so two rows in
/// one `(bucket, series)` of a panel cannot share a band.
fn gap_fill_collision(facts: &Facts, field: &str, rows: &[Row], grain: Grain) -> Result<(), Diagnostic> {
    let Grain::Calendar(tu) = grain else { return Ok(()) };
    let axis = &facts.style.axis_x;
    let dim = facts.color.as_deref().filter(|c| !facts.panel_axes.iter().any(|a| a.field == *c));
    let mut panels: Vec<(Vec<PyKey>, Vec<&Row>)> = Vec::new();
    for row in rows {
        let key: Vec<PyKey> = facts.panel_axes.iter().map(|a| py_cell_key(row.get(&a.field))).collect();
        match panels.iter_mut().find(|(k, _)| *k == key) {
            Some((_, group)) => group.push(row),
            None => panels.push((key, vec![row])),
        }
    }
    for (_, panel) in panels {
        let parsed: Vec<Option<(i32, u8, u8)>> = panel
            .iter()
            .map(|row| {
                let text = row.get(field)?.as_str()?;
                parse_date(text).map(|(date, _)| date)
            })
            .collect();
        let Some(anchor) = parsed.iter().flatten().min().copied() else { continue };
        let bucket_of = |date: (i32, u8, u8)| match tu {
            TimeUnit::YearWeek => {
                let offset = days_from_civil(date.0, date.1, date.2) - days_from_civil(anchor.0, anchor.1, anchor.2);
                civil_from_days(days_from_civil(anchor.0, anchor.1, anchor.2) + 7 * offset.div_euclid(7))
            }
            _ => floor_to_bucket_start(date, tu, axis.fiscal_year_start_month),
        };
        let mut seen: Vec<(Vec<PyKey>, Vec<usize>)> = Vec::new();
        for (i, row) in panel.iter().enumerate() {
            let Some(cell) = row.get(field).filter(|c| !c.is_null()) else { continue };
            let bucket = match parsed[i] {
                Some(date) => {
                    let (y, m, d) = bucket_of(date);
                    format!("{y:04}-{m:02}-{d:02}")
                }
                None => py_repr(cell),
            };
            let mut key = vec![PyKey::Str(bucket)];
            key.extend(dim.map(|d| py_cell_key(row.get(d))));
            match seen.iter_mut().find(|(k, _)| *k == key) {
                Some((_, hits)) => hits.push(i),
                None => seen.push((key, vec![i])),
            }
        }
        let Some((key, hits)) = seen.into_iter().find(|(_, hits)| hits.len() > 1) else { continue };
        let dim_desc = dim.map_or(String::new(), |d| format!(" ({d}={})", py_repr(panel[hits[0]].get(d).unwrap_or(&Value::Null))));
        return Err(Diagnostic::new(
            &codes::ERR_GAP_FILL_BUCKET_COLLISION,
            [
                ("bucket", json!(match &key[0] { PyKey::Str(text) => text.as_str(), _ => "" })),
                ("dim_desc", json!(dim_desc)),
                ("time_unit", json!(tu_name(tu))),
                ("value_a", panel[hits[0]][field].clone()),
                ("value_b", panel[hits[1]][field].clone()),
                ("x_field", json!(field)),
            ],
        ));
    }
    Ok(())
}

fn x_axis_of(facts: &Facts) -> XAxis<'_> {
    XAxis::of(&facts.style.axis_x)
}

/// A line or area drawn as a band-aware step.
fn band_step_of(facts: &Facts) -> bool {
    // A multi-measure chart's axis is built without the curve.
    if !facts.wide_measures.is_empty() {
        return false;
    }
    match facts.chart_type.as_str() {
        "line" => facts.style.line_mark.as_ref(),
        "area" => facts.style.area_mark.as_ref(),
        _ => None,
    }
    .and_then(|m| m.curve.as_deref())
        == Some("step")
}

/// Whether the x axis resolves a band scale (nominal or ordinal) once the chart's own curve has
/// had its say: the verdict `step_band.is_band_step` reads for the base and for every layer.
pub fn x_is_band(resolved: &Value, rows: &[Row]) -> Result<bool, Diagnostic> {
    let facts: Facts = serde_json::from_value(resolved.clone()).map_err(|e| format!("x axis facts: {e}"))?;
    let Some(field) = facts.x.as_deref() else { return Ok(false) };
    let rows = normalize_labeled_temporal(rows, field)?;
    let filled = resolve_filled_x(&rows, field, &x_axis_of(&facts), &facts.chart_type, band_step_of(&facts), &panel_fields_of(&facts))?;
    Ok(matches!(filled.resolution.vl_type, VegaType::Nominal | VegaType::Ordinal))
}

fn panel_fields_of(facts: &Facts) -> Vec<&str> {
    facts.panel_axes.iter().map(|a| a.field.as_str()).collect()
}

fn continuous_of<'a>(axis: &'a AxisStyle, key: &str) -> Option<&'a Value> {
    axis.scale.get("continuous").and_then(|c| c.get(key)).filter(|v| !v.is_null())
}

/// Raises the first x-axis ERR production raises for a bar, line or area.
pub fn check(resolved: &Value, rows: &[Row]) -> Result<(), Diagnostic> {
    let facts: Facts = serde_json::from_value(resolved.clone()).map_err(|e| format!("x axis facts: {e}"))?;
    let Some(field) = facts.x.as_deref() else { return Ok(()) };
    let axis = &facts.style.axis_x;
    if facts.chart_type == "heatmap" {
        return heatmap(&facts, field, rows);
    }
    if facts.chart_type == "scatter" && infer_vega_type(rows, field) == VegaType::Quantitative {
        return Ok(());
    }
    // Bar and the other cartesian emitters rewrite a labeled or year-shaped x
    // to ISO dates before anything below reads it.
    let rows = normalize_labeled_temporal(rows, field)?;
    let x_axis = x_axis_of(&facts);
    let panel_fields = panel_fields_of(&facts);
    if facts.chart_type == "bar" && facts.orientation.as_deref() == Some("horizontal") {
        // A horizontal bar's category never resolves a continuous scale, so the
        // scaffold is built whenever the grain is bucketed.
        let resolution = resolve_cartesian_x_type(&rows, field, &x_axis, "bar", false, &panel_fields)?;
        let resolution = XResolution { vl_type: VegaType::Ordinal, ..resolution };
        gap_fill(&facts, field, &rows, &x_axis, &resolution)?;
        // The bars are drawn from the filled rows, so the format gate reads them.
        let gate_rows = match resolution.fill_grain {
            Grain::Calendar(tu) if x_axis.fills_gaps(&resolution) => {
                date_only_x(scaffold_rows(&rows, field, &panel_fields, tu, x_axis.fiscal_year_start_month), field)
            }
            _ => Vec::new(),
        };
        let gate_rows = if gate_rows.is_empty() { rows.clone() } else { gate_rows };
        if axis.labels.values.is_some() {
            return Err(Diagnostic::new(
                &codes::ERR_LABEL_VALUES_NOT_TEMPORAL,
                [
                    ("cause", json!("a horizontal bar's categorical axis never honors it, regardless of date format")),
                    ("field", json!(field)),
                    ("remedy", json!("Set orientation: vertical, or remove labels.values.")),
                ],
            ));
        }
        let remedy = format!(
            "A horizontal bar's axis_x is its categorical axis {DASH} the measure is axis_y. Set orientation: vertical, or remove ticks.step."
        );
        tick_step(&facts, field, VegaType::Nominal, Some(&remedy))?;
        let remedy = format!(
            "orientation: horizontal rotates the chart, it does not swap the channels {DASH} axis_x still addresses the categories. Author the format on style.axis_y.labels.format, the measure axis."
        );
        return label_format(&facts, field, VegaType::Nominal, &gate_rows, Some(&remedy));
    }
    let mark = facts.chart_type.as_str();
    let mut scaffold = Vec::new();
    let resolution = if mark == "scatter" {
        resolve_cartesian_x_type(&rows, field, &x_axis, mark, band_step_of(&facts), &panel_fields)?
    } else {
        let x = resolve_filled_x(&rows, field, &x_axis, mark, band_step_of(&facts), &panel_fields)?;
        if let Some(tu) = x.filled {
            gap_fill_collision(&facts, field, &rows, Grain::Calendar(tu))?;
        }
        scaffold = x.scaffold;
        x.resolution
    };
    // The fill rewrites every x to its date-only ISO bucket and drops the rows
    // it cannot parse; the gates below read that result.
    let filled_rows = date_only_x(scaffold, field);
    let rows: &[Row] = if filled_rows.is_empty() { &rows } else { &filled_rows };
    let vl_type = resolution.vl_type;
    if axis.labels.values.is_some() && vl_type != VegaType::Temporal {
        label_values_not_temporal(rows, field)?;
    }
    if axis.ticks.time_unit.is_some() && vl_type != VegaType::Temporal {
        return Err(Diagnostic::new(
            &codes::ERR_TICKS_INTERVAL_NOT_TEMPORAL,
            [("field", json!(field)), ("vl_type", json!(vl_type.name()))],
        ));
    }
    tick_step(&facts, field, vl_type, None)?;
    label_format(&facts, field, vl_type, rows, None)?;
    for value in axis.labels.values.iter().flatten() {
        label_value_date(value)?;
    }
    let domain = continuous_of(axis, "domain");
    if domain.is_some() && !matches!(vl_type, VegaType::Temporal | VegaType::Quantitative) {
        return Err(Diagnostic::new(
            &codes::ERR_SCALE_DOMAIN_REQUIRES_CONTINUOUS_X,
            [("chart_id", json!(facts.id)), ("vl_type", json!(vl_type.name()))],
        ));
    }
    Ok(())
}

/// The fill rewrites every x to its date-only ISO bucket.
fn date_only_x(rows: Vec<Row>, field: &str) -> Vec<Row> {
    rows.into_iter()
        .map(|mut row| {
            if let Some(((y, m, d), _)) = row.get(field).and_then(Value::as_str).and_then(parse_date) {
                row.insert(field.to_string(), json!(format!("{y:04}-{m:02}-{d:02}")));
            }
            row
        })
        .collect()
}

/// `gap_fill_ordinal_time`: the ordinal scaffold places every row in a bucket.
fn gap_fill(facts: &Facts, field: &str, rows: &[Row], axis: &XAxis, resolution: &XResolution) -> Result<(), Diagnostic> {
    if !axis.fills_gaps(resolution) {
        return Ok(());
    }
    gap_fill_collision(facts, field, rows, resolution.fill_grain)
}

const HEATMAP_FORMAT_REMEDY: &str = "Remove the format \u{2014} a heatmap has no measure axis. Both axes are grid dimensions and the value lives on the color channel, which carries no label format of its own.";

/// A heatmap's x and y are grid dimensions: nominal bands that still run the
/// authored-axis gates `build_cartesian_x_encoding` and the y gate apply.
fn heatmap(facts: &Facts, field: &str, raw_rows: &[Row]) -> Result<(), Diagnostic> {
    let axis = &facts.style.axis_x;
    let multi_measure = facts.y.as_ref().is_some_and(Value::is_array);
    // Only the single-measure branch canonicalizes its x rows.
    let normalized;
    let rows: &[Row] = if multi_measure {
        raw_rows
    } else {
        normalized = normalize_labeled_temporal(raw_rows, field)?;
        &normalized
    };
    if !multi_measure {
        if axis.labels.values.is_some() {
            label_values_not_temporal(rows, field)?;
        }
        if axis.ticks.time_unit.is_some() {
            return Err(Diagnostic::new(
                &codes::ERR_TICKS_INTERVAL_NOT_TEMPORAL,
                [("field", json!(field)), ("vl_type", json!("nominal"))],
            ));
        }
    }
    tick_step(facts, field, VegaType::Nominal, None)?;
    label_format(facts, field, VegaType::Nominal, rows, Some(HEATMAP_FORMAT_REMEDY))?;
    if !multi_measure {
        for value in axis.labels.values.iter().flatten() {
            label_value_date(value)?;
        }
    }
    if let (Some(y), Some(axis_y)) = (facts.y.as_ref().and_then(Value::as_str), facts.style.axis_y.as_ref()) {
        gate_label_format(axis_y.labels.format.as_ref(), "axis_y.labels.format", y, VegaType::Nominal, raw_rows, Some(HEATMAP_FORMAT_REMEDY))?;
    }
    Ok(())
}
