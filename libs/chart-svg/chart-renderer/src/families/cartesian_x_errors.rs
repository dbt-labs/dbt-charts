//! The ERR codes production raises while it builds a cartesian x axis, decided
//! from the resolved x type (`x_type::resolve_cartesian_x_type`) and the
//! authored axis, in the order `build_cartesian_x_encoding` raises them.

use std::borrow::Cow;

use serde::Deserialize;
use serde_json::{json, Map, Value};

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

/// `_parse_label_value_date`: an authored `labels.values` entry is a string
/// `datetime.fromisoformat` reads, else it raises.
fn label_value_date(value: &Value) -> Result<(), Diagnostic> {
    if value.as_str().is_some_and(iso_format_reads) {
        return Ok(());
    }
    Err(Diagnostic::new(&codes::ERR_LABEL_VALUES_INVALID_DATE, [("value", value.clone())]))
}

/// Whether CPython's `datetime.fromisoformat` accepts `text`: a date as
/// `YYYY-MM-DD`, `YYYYMMDD`, `YYYY-Www[-D]` or `YYYYWww[D]`, then any one
/// separator character and `HH[:MM[:SS[.f]]]` with an optional `Z` or UTC
/// offset (`_pydatetime._parse_isoformat_date` and `_parse_isoformat_time`).
fn iso_format_reads(text: &str) -> bool {
    let chars: Vec<char> = text.chars().collect();
    if chars.len() < 7 {
        return false;
    }
    let Some(at) = iso_separator_at(&chars) else { return false };
    iso_date_reads(&chars[..at.min(chars.len())]) && (at >= chars.len() || iso_time_reads(&chars[at + 1..]))
}

/// `_find_isoformat_datetime_separator`: where the date ends and the separator sits.
fn iso_separator_at(c: &[char]) -> Option<usize> {
    if c.len() == 7 {
        return Some(7);
    }
    let digit = |i: usize| c.get(i).is_some_and(char::is_ascii_digit);
    Some(match (c[4], c[5]) {
        ('-', 'W') => match c.get(8) {
            Some('-') => match c.len() {
                9 => return None,
                n if n > 10 && digit(10) => 8,
                _ => 10,
            },
            _ => 8,
        },
        ('-', _) => 10,
        ('W', _) => {
            let end = (7..c.len()).find(|&i| !digit(i)).unwrap_or(c.len());
            if end < 9 {
                end
            } else if end % 2 == 0 {
                7
            } else {
                8
            }
        }
        _ => 8,
    })
}

/// A run of ASCII digits read as a number; `None` for an empty or short run.
fn iso_number(c: &[char], from: usize, len: usize) -> Option<u32> {
    let run = c.get(from..from + len)?;
    run.iter().all(char::is_ascii_digit).then(|| run.iter().collect::<String>().parse().ok()).flatten()
}

/// `_parse_isoformat_date` then the `datetime` constructor's range checks.
fn iso_date_reads(d: &[char]) -> bool {
    if !matches!(d.len(), 7 | 8 | 10) {
        return false;
    }
    let Some(year) = iso_number(d, 0, 4).filter(|y| (1..=9999).contains(y)) else { return false };
    let dash = d[4] == '-';
    let mut at = 4 + usize::from(dash);
    if d.get(at) == Some(&'W') {
        let Some(week) = iso_number(d, at + 1, 2) else { return false };
        at += 3;
        let mut day = 1;
        if d.len() > at {
            if (d[at] == '-') != dash {
                return false;
            }
            at += usize::from(dash);
            let Some(n) = iso_number(d, at, 1) else { return false };
            day = n;
        }
        let year = year as i32;
        let jan1_weekday = (days_from_civil(year, 1, 1) + 4).rem_euclid(7);
        let long_year = jan1_weekday == 4 || jan1_weekday == 3 && super::temporal::is_leap_year(year);
        let weeks = if long_year { 53 } else { 52 };
        if !(1..=weeks).contains(&week) || !(1..=7).contains(&day) {
            return false;
        }
        let week1_monday = days_from_civil(year, 1, 4) - (days_from_civil(year, 1, 4) + 3).rem_euclid(7);
        let (y, _, _) = civil_from_days(week1_monday + i64::from((week - 1) * 7 + day - 1));
        return y <= 9999;
    }
    let Some(month) = iso_number(d, at, 2) else { return false };
    at += 2;
    if (d.get(at) == Some(&'-')) != dash {
        return false;
    }
    at += usize::from(dash);
    let Some(day) = iso_number(d, at, 2) else { return false };
    (1..=12).contains(&month) && (1..=days_in_month(year as i32, month)).contains(&day)
}

/// `_parse_hh_mm_ss_ff`: `HH[:?MM[:?SS[{.,}f+]]]` as `[h, m, s, micro]`.
fn iso_clock(c: &[char]) -> Option<[u32; 4]> {
    let mut parts = [0; 4];
    let (mut at, mut dashed) = (0, false);
    for part in 0..3 {
        parts[part] = iso_number(c, at, 2)?;
        at += 2;
        let next = c.get(at).copied();
        if part == 0 {
            dashed = next == Some(':');
        }
        if next.is_none() || part >= 2 {
            break;
        }
        if dashed && next != Some(':') {
            return None;
        }
        at += usize::from(dashed);
    }
    if at < c.len() {
        if !matches!(c[at], '.' | ',') {
            return None;
        }
        let fraction = &c[at + 1..];
        if !fraction.iter().all(char::is_ascii_digit) {
            return None;
        }
        let used = fraction.len().min(6);
        let micro: u32 = fraction[..used].iter().collect::<String>().parse().unwrap_or(0);
        parts[3] = micro * 10u32.pow(6 - used as u32);
    }
    Some(parts)
}

/// `_parse_isoformat_time` then the `datetime` constructor's range checks.
fn iso_time_reads(t: &[char]) -> bool {
    if t.len() < 2 {
        return false;
    }
    let find = |ch: char| t.iter().position(|&c| c == ch).map(|i| i + 1);
    let zone = find('-').or_else(|| find('+')).or_else(|| find('Z')).unwrap_or(0);
    let clock = if zone > 0 { &t[..zone - 1] } else { t };
    let Some([hour, minute, second, _]) = iso_clock(clock) else { return false };
    if hour > 23 || minute > 59 || second > 59 {
        return false;
    }
    if zone == 0 || zone == t.len() && t[zone - 1] == 'Z' {
        return true;
    }
    let tail = &t[zone..];
    if matches!(tail.len(), 0 | 1 | 3) {
        return false;
    }
    let Some([h, m, s, micro]) = iso_clock(tail) else { return false };
    // `timezone(offset)` takes only an offset strictly inside a day.
    (i64::from(h) * 3600 + i64::from(m) * 60 + i64::from(s)) * 1_000_000 + i64::from(micro) < 86_400_000_000
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
pub(crate) fn time_format(spec: &str, setting: &str, field: &str, rows: &[Row], remedy: Option<&str>) -> Result<(), Diagnostic> {
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
pub(super) fn gate_label_format(
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
    Ok(matches!(base_x_type(resolved, rows)?, Some(VegaType::Nominal | VegaType::Ordinal)))
}

/// The base's x encoding type, `None` when the chart has no x.
pub fn base_x_type(resolved: &Value, rows: &[Row]) -> Result<Option<VegaType>, Diagnostic> {
    let facts: Facts = Deserialize::deserialize(resolved).map_err(|e| format!("x axis facts: {e}"))?;
    let Some(field) = facts.x.as_deref() else { return Ok(None) };
    let rows = normalize_labeled_temporal(rows, field)?;
    let filled = resolve_filled_x(&rows, field, &x_axis_of(&facts), &facts.chart_type, band_step_of(&facts), &panel_fields_of(&facts))?;
    Ok(Some(filled.resolution.vl_type))
}

fn panel_fields_of(facts: &Facts) -> Vec<&str> {
    facts.panel_axes.iter().map(|a| a.field.as_str()).collect()
}

fn continuous_of<'a>(axis: &'a AxisStyle, key: &str) -> Option<&'a Value> {
    axis.scale.get("continuous").and_then(|c| c.get(key)).filter(|v| !v.is_null())
}

/// Raises the first x-axis ERR production raises for a bar, line or area.
pub fn check(resolved: &Value, rows: &[Row]) -> Result<(), Diagnostic> {
    let facts: Facts = Deserialize::deserialize(resolved).map_err(|e| format!("x axis facts: {e}"))?;
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
        let gate_rows = if gate_rows.is_empty() { rows.clone() } else { Cow::Owned(gate_rows) };
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

pub(super) const HEATMAP_FORMAT_REMEDY: &str = "Remove the format \u{2014} a heatmap has no measure axis. Both axes are grid dimensions and the value lives on the color channel, which carries no label format of its own.";

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

#[cfg(test)]
mod tests {
    use super::iso_format_reads;

    /// Each verdict is `datetime.fromisoformat`'s (CPython 3.13).
    #[test]
    fn a_labels_values_entry_reads_as_datetime_fromisoformat_does() {
        let reads = [
            "2024-02-01", "20240201", "2024-W06-4", "2024W064", "2024-W06", "2024W06", "2020-W53-1", "9999-W52-5",
            "2024-02-01T00:00", "2024-02-01T10", "20240201T1000", "2024-02-01 10:00:00.123456", "2024-02-01T10:00:00,5",
            "2024-02-01T10:00:00.1234567", "2024-02-01T10:00Z", "2024-02-01T10:00+05:30", "2024-02-01T10:00-0530",
            "2024-02-01T10:00+23:59", "2024-02-01T10:00+00:00", "2024-W06-4T10:00:00+05:30:15", "2024-02-01x10:00",
        ];
        let rejects = [
            "", "2024", "2024-02", "Jan 2024", "2024-02-", "2024-13-01", "2024-02-30", "0000-01-01", "2024-W53-1",
            "9999-W52-7", "2024-W6-4", "2024-W06-", "2024-0201", "2024-02-01x", "2024-02-01T24:00", "2024-02-01T10:60",
            "2024-02-01T10:00:60", "2024-02-01T10:00+24:00", "2024-02-01T10:00+5", "2024-02-01T10:0", "2024-02-01T10:00Zx",
            "2024-02-01T1:00",
        ];
        for text in reads {
            assert!(iso_format_reads(text), "{text:?} is read");
        }
        for text in rejects {
            assert!(!iso_format_reads(text), "{text:?} is rejected");
        }
    }
}
