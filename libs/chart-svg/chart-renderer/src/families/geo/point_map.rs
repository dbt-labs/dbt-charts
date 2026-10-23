//! Point map family (`type: point_map` / `type: bubble_map`): circles at
//! longitude/latitude over an optional basemap (`emitters/geo.py`
//! `PointMapEmitter`). Production hands Vega-Lite a `circle` spec, layered over a
//! `geoshape` basemap when the chart names one; this draws what Vega draws from it:
//!
//! - the projection fitted to the basemap and the points the way Vega-Lite assembles
//!   it ([`super::projection`]); the circles are clipped to the view;
//! - a `size` measure on a zero-based area scale over `[0, 1000]`, or with `collapse`
//!   the count of rows sharing an exact coordinate, biggest pile drawn first;
//! - a color channel: a nominal one takes the palette in sorted order, a quantitative
//!   one the default ramp;
//! - the description Vega-Lite writes for a mark with a tooltip: each described
//!   channel, then the tooltip list, a title seen once;
//! - a color legend and a size legend, stacked at the right with Vega's own legend
//!   defaults: the emitter forwards none of the chart's `legend` style.
//!
//! Rows production drops before Vega-Lite sees them (a negative `size`; under
//! `albersUsa`, a coordinate outside its three hand-written boxes) are counted
//! against the rows as they arrived, as the warning detectors do. Vega-Lite itself
//! then filters a row whose size or continuous color is not a finite number from the
//! marks, but not from the projection fit; a null coordinate projects as zero and stays
//! out of the fit; a point the projection drops is drawn at the view's origin.
//!
//! `bind` holds what the data decides; `layout` fits it to the cell and returns nodes.

use std::collections::HashMap;

use d3rs::geojson::{Feature, FeatureCollection, GeoJson, Geometry};
use serde::Deserialize;
use serde_json::{json, Map, Value};

use super::geoshape::{decode_features, GeoSource};
use super::projection;
use crate::codes;
use crate::common::{
    category_scale_for, series_color, CategoryColorScale, FieldLabels, FormatSlot, Font,
    TitleStyle,
};
use crate::contract;
use crate::diagnostic::{warn, Diagnostic};
use crate::families::color_scale::{js_number_string, ContinuousColor, GradientAt};
use crate::families::legend::{
    color_channel_title, fold_symbol_limit, legend_fit_bounds, legend_origin, stack_legends, symbol_legend, vega_default_legend,
    vega_legend, LegendConfig, LegendEntry, LegendSymbol, SizeLegend, SizePaint, SizeRows, VEGA_LEGEND_SYMBOL_LIMIT,
};
use crate::families::heatmap::date_cell_day;
use crate::families::temporal::{infer_vega_type, VegaType};
use crate::frame::{ChartFrame, FrameTitle};
use crate::href::LinkOpen;
use crate::layout::{Layout, Rect};
use scene::{Attrs, Node};
use print::print;
use measure::FontBook;
use crate::vega_fit::Bounds;
use crate::Payload;

/// (min_lat, min_lon, max_lat, max_lon) of `albersUsa`'s three regions: the boxes
/// production filters rows with (`_ALBERS_USA_REGIONS`), an approximation of the
/// projection's own insets that this port keeps so output matches.
const ALBERS_USA_REGIONS: [(f64, f64, f64, f64); 3] = [
    (24.0, -125.0, 50.0, -66.0),
    (51.0, -180.0, 72.0, -130.0),
    (18.0, -162.0, 23.0, -154.0),
];

/// The size scale's range top: mark area is proportional to the value (`_AREA_PROPORTIONAL_SIZE_RANGE_MAX`).
const AREA_RANGE_MAX: f64 = 1000.0;
/// The tooltip list's length cap (`build_point_tooltip_fields`).
const MAX_TOOLTIP_FIELDS: usize = 6;
/// Vega's default `labelOffset`: a symbol's cell to its label.
const SYMBOL_LABEL_OFFSET: f64 = 4.0;
/// The title Vega-Lite gives a count aggregate that names none, and production's own (`COUNT_TITLE`).
const COUNT_TITLE: &str = "#";
const COUNT_OF_RECORDS: &str = "Count of Records";

/// The slice of a resolved point-map chart this family reads.
#[derive(Deserialize)]
struct Resolved {
    id: String,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    title_style: Option<TitleStyle>,
    background: String,
    legend: LegendConfig,
    field_labels: FieldLabels,
    #[serde(default)]
    palette: Vec<String>,
    #[serde(default)]
    category_colors: Vec<CategoryColorScale>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    link: Option<String>,
    #[serde(default)]
    link_open: LinkOpen,
    #[serde(default)]
    resolved_channels: HashMap<String, ColorChannel>,
    style: Style,
    latitude: Option<String>,
    longitude: Option<String>,
    size: Option<String>,
    collapse: bool,
    basemap_fill: Option<String>,
    basemap_stroke: Option<String>,
    basemap_geo_url: Option<String>,
    basemap_geo_format: Option<Map<String, Value>>,
    geo_projection_type: String,
    geo_projection_params: Option<Map<String, Value>>,
}

#[derive(Deserialize)]
struct ColorChannel {
    mode: String,
    data_field: Option<String>,
    #[serde(default)]
    categorical: bool,
}

#[derive(Deserialize)]
struct Style {
    point_mark: PointMark,
    single_series_fill: String,
    tooltip_format: FormatSlot,
    title_font: Option<Font>,
}

#[derive(Deserialize)]
struct PointMark {
    size: f64,
    opacity: f64,
}

/// How the color channel paints the circles and what its legend lists.
enum Coloring {
    /// A nominal field: one fill per distinct value, sorted.
    Categories { domain: Vec<String>, fills: Vec<String>, title: String },
    /// A quantitative field: the ramp over the drawn values' extent.
    Gradient { scale: ContinuousColor, title: String },
}

/// The size legend's entries: each labeled value and the circle area it draws.
struct SizeEntries {
    title: String,
    labels: Vec<String>,
    areas: Vec<f64>,
}

/// One circle: where it sits, how big, what paints it and what it says.
struct Point {
    /// Longitude and latitude; `None` for a map with no coordinate fields, drawn at the view's center.
    at: Option<(f64, f64)>,
    area: f64,
    fill: String,
    label: String,
    href: Option<String>,
}

/// The circles and the legends' content.
struct Marks {
    coloring: Option<Coloring>,
    size_entries: Option<SizeEntries>,
    points: Vec<Point>,
    /// Where the projection is fitted: every coordinate production hands Vega-Lite, drawn or not (its
    /// invalid filter drops a row from the marks, not from the fit).
    fit_points: Vec<(f64, f64)>,
}

/// What the data decides: the circles, the basemap and the legends' content.
pub struct Bound {
    resolved: Resolved,
    points: Vec<Point>,
    fit_points: Vec<(f64, f64)>,
    basemap: Option<Vec<GeoJson>>,
    coloring: Option<Coloring>,
    size_entries: Option<SizeEntries>,
    fit: bool,
}

/// Python's `float(value)` of a cell; `None` where it raises.
fn py_float(value: &Value) -> Option<f64> {
    match value {
        Value::Number(n) => n.as_f64(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// `row_in_projection`: `None` where either coordinate is missing or unreadable.
fn in_albers_usa(row: &Map<String, Value>, lat_field: &str, lon_field: &str) -> Option<bool> {
    let lat = py_float(row.get(lat_field)?)?;
    let lon = py_float(row.get(lon_field)?)?;
    Some(ALBERS_USA_REGIONS.iter().any(|&(lat0, lon0, lat1, lon1)| lat0 <= lat && lat <= lat1 && lon0 <= lon && lon <= lon1))
}

/// `row_has_negative_size`.
fn has_negative_size(row: &Map<String, Value>, size_field: &str) -> bool {
    row.get(size_field).and_then(py_float).is_some_and(|v| v < 0.0)
}

/// `+cell` for a cell Vega-Lite keeps (`isValid(x) && isFinite(+x)`): `None` where its invalid filter drops the row.
fn valid_number(value: Option<&Value>) -> Option<f64> {
    match value {
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::Bool(b)) => Some(f64::from(u8::from(*b))),
        Some(Value::String(s)) => crate::common::js_string_to_number(s).filter(|n| n.is_finite()),
        _ => None,
    }
}

/// A cell the filter above kept.
fn number(row: &Map<String, Value>, field: &str, what: &str) -> Result<f64, String> {
    number_of(row.get(field), what)
}

/// A coordinate as the projection reads the cell: JavaScript's `+cell`, so a null is the equator or the
/// prime meridian and a numeric text is its number. Vega-Lite filters none out.
fn coordinate(row: &Map<String, Value>, field: &str, what: &str) -> Result<f64, String> {
    match row.get(field) {
        Some(Value::Null) => Ok(0.0),
        cell => number_of(cell, what),
    }
}

fn number_of(cell: Option<&Value>, what: &str) -> Result<f64, String> {
    valid_number(cell).ok_or_else(|| crate::refuse!("a point map row with a missing or non-numeric {what} is not supported"))
}

/// The latitude and longitude fields; a point map with neither leaves its marks at the view's center.
fn coordinate_fields(r: &Resolved) -> Result<Option<(&str, &str)>, String> {
    match (r.latitude.as_deref(), r.longitude.as_deref()) {
        (Some(lat), Some(lon)) => Ok(Some((lat, lon))),
        (None, None) => Ok(None),
        _ => Err(crate::refuse!("a point map with only one of latitude and longitude is not supported")),
    }
}

/// The text Vega-Lite's description gives a quantitative cell (`format(x, "")`; a missing one is `null`).
fn quantitative_text(value: Option<&Value>) -> Result<String, String> {
    match value {
        None | Some(Value::Null) => Ok("null".into()),
        Some(Value::Number(n)) => crate::number_locale::d3_format("", n.as_f64().expect("a JSON number is an f64")),
        Some(Value::Bool(b)) => crate::number_locale::d3_format("", f64::from(u8::from(*b))),
        Some(Value::String(s)) => crate::number_locale::d3_format("", crate::common::js_string_to_number(s).unwrap_or(f64::NAN)),
        Some(_) => Err(crate::refuse!("a point map tooltip value in a numeric column that is not a scalar is not supported")),
    }
}

fn field_title(r: &Resolved, field: &str) -> Result<String, String> {
    match r.field_labels.get(field) {
        Some(label) => Ok(label.clone()),
        None => display_text::slug_to_text(field).map_err(|e| e.to_string()),
    }
}

/// A coordinate's tooltip title: the authored label, else the board's word.
fn coordinate_title(r: &Resolved, field: &str, word: &str) -> Result<String, String> {
    match r.field_labels.get(field) {
        Some(label) => Ok(label.clone()),
        None => Ok(display_text::language::tr(word, None, &[])?),
    }
}

/// Vega-Lite's description of a mark: `title: value` rows in order, a title already
/// described skipped (`tooltipData` keys its rows by title).
fn describe(rows: Vec<(String, String)>) -> String {
    let mut seen: Vec<String> = Vec::new();
    let mut parts = Vec::new();
    for (title, value) in rows {
        if !seen.contains(&title) {
            parts.push(format!("{title}: {value}"));
            seen.push(title);
        }
    }
    parts.join("; ")
}

/// The legend ladder of a count: Vega-Lite's `sequence` at a stride that grows with the
/// domain (`COUNT_LEGEND_VALUES_SIGNAL`).
fn count_ladder(max: f64) -> Vec<f64> {
    let stride = match max {
        m if m <= 5.0 => 1.0,
        m if m <= 10.0 => 2.0,
        m if m <= 25.0 => 5.0,
        m if m <= 50.0 => 10.0,
        m if m <= 100.0 => 20.0,
        m if m <= 250.0 => 50.0,
        m if m <= 500.0 => 100.0,
        m if m <= 1000.0 => 200.0,
        _ => 500.0,
    };
    let stop = max.ceil() + 1.0;
    let mut values = Vec::new();
    let mut v = stride;
    while v < stop {
        values.push(v);
        v += stride;
    }
    values
}

/// The legend of a size scale over `[0, max]`: the ticks that draw a circle, with their text.
fn size_ladder(max: f64, collapse: bool) -> Result<(Vec<f64>, Vec<String>), String> {
    if collapse {
        let values = count_ladder(max);
        let labels = values.iter().map(|&v| crate::number_locale::d3_format("d", v)).collect::<Result<_, _>>()?;
        return Ok((values, labels));
    }
    // A zero tick would draw a circle of no area, unless a zero domain gives every value the middle of the range.
    crate::families::legend::size_legend_ticks((0.0, max), max > 0.0)
}

fn area_of(value: f64, max: f64) -> f64 {
    crate::families::legend::unit_position(value, 0.0, max) * AREA_RANGE_MAX
}

/// Read the chart's rows into circles. Warns of the rows production drops.
pub fn bind(payload: &Payload) -> Result<Bound, Diagnostic> {
    let r: Resolved = Deserialize::deserialize(&payload.resolved).map_err(|e| format!("point_map payload: {e}"))?;
    let fields = coordinate_fields(&r)?;
    if r.collapse && (r.size.is_some() || r.resolved_channels.contains_key("color")) {
        return Err(crate::refuse!("a collapsed point map with a size or color channel is not supported").into());
    }
    let all = &payload.rows;
    let bounded = fields.filter(|_| r.geo_projection_type == "albersUsa");
    if let Some((lat_field, lon_field)) = bounded {
        let out = all.iter().filter(|row| in_albers_usa(row, lat_field, lon_field) == Some(false)).count();
        if out > 0 {
            warn(Diagnostic::new(
                &codes::WARN_POINT_MAP_OUT_OF_PROJECTION,
                [
                    ("chart_id", json!(r.id)),
                    ("dropped_count", json!(out)),
                    ("projection", json!(r.geo_projection_type)),
                    ("total_count", json!(all.len())),
                ],
            ));
        }
    }
    if let Some(size_field) = r.size.as_deref() {
        let negative = all.iter().filter(|row| has_negative_size(row, size_field)).count();
        if negative > 0 {
            warn(Diagnostic::new(
                &codes::WARN_POINT_MAP_NEGATIVE_SIZE_VALUES,
                [
                    ("chart_id", json!(r.id)),
                    ("dropped_count", json!(negative)),
                    ("size_field", json!(size_field)),
                    ("total_count", json!(all.len())),
                ],
            ));
        }
    }
    let rows: Vec<Map<String, Value>> = all
        .iter()
        .filter(|row| bounded.is_none_or(|(lat_field, lon_field)| in_albers_usa(row, lat_field, lon_field) != Some(false)))
        .filter(|row| r.size.as_deref().is_none_or(|f| !has_negative_size(row, f)))
        .cloned()
        .collect();

    let Marks { coloring, size_entries, points, fit_points } = bind_marks(payload, &r, &rows)?;
    let basemap = bind_basemap(payload, &r)?;
    let fit = projection::is_fit(r.geo_projection_params.as_ref());
    Ok(Bound { resolved: r, points, fit_points, basemap, coloring, size_entries, fit })
}

fn bind_basemap(payload: &Payload, r: &Resolved) -> Result<Option<Vec<GeoJson>>, Diagnostic> {
    let (Some(url), Some(format)) = (r.basemap_geo_url.as_deref(), r.basemap_geo_format.as_ref()) else {
        return Ok(None);
    };
    if r.basemap_fill.is_none() {
        return Err(crate::refuse!("a point map basemap with no fill is not supported").into());
    }
    let geometries = payload.geometries.as_ref().ok_or_else(|| {
        format!("basemap: payload.geometries is absent; basemap_geo_url {url:?} needs its document")
    })?;
    let text = |key: &str| format.get(key).and_then(Value::as_str);
    let source = GeoSource {
        role: "basemap",
        url,
        format_type: text("type").ok_or("basemap: basemap_geo_format names no type")?,
        feature: text("feature"),
        property: text("property"),
    };
    let features = decode_features(&source, geometries)?;
    Ok(Some(features.into_iter().map(GeoJson::Feature).collect()))
}

/// The circles of the rows production draws, and the legends' content.
fn bind_marks(
    payload: &Payload,
    r: &Resolved,
    all_rows: &[Map<String, Value>],
) -> Result<Marks, Diagnostic> {
    // Production attaches `link` to an unlayered map's circles and drops it under a basemap, never reading it.
    let layered = r.basemap_geo_url.is_some() && r.basemap_geo_format.is_some();
    let fields = coordinate_fields(r)?;
    let color_field = match r.resolved_channels.get("color") {
        None => None,
        Some(ch) if ch.mode == "series" => Some(ch.data_field.as_deref().ok_or("point_map: a series color channel names no data_field")?),
        Some(ch) => return Err(crate::refuse!("a point map color channel in {} mode is not supported", ch.mode).into()),
    };
    let color_type = color_field.map(|field| {
        if r.resolved_channels["color"].categorical {
            VegaType::Nominal
        } else {
            infer_vega_type(all_rows, field)
        }
    });

    // Vega-Lite filters a row whose size, or continuous color, is not a finite number
    // (`isValid(x) && isFinite(+x)`) before it draws or scales anything.
    let rows: Vec<Map<String, Value>> = all_rows
        .iter()
        .filter(|row| r.size.as_deref().is_none_or(|field| valid_number(row.get(field)).is_some()))
        .filter(|row| match (color_field, color_type) {
            (Some(field), Some(VegaType::Quantitative)) => valid_number(row.get(field)).is_some(),
            (Some(field), Some(VegaType::Temporal)) => row.get(field).and_then(date_cell_day).is_some(),
            _ => true,
        })
        .cloned()
        .collect();
    let rows = &rows[..];

    // The coordinates as the projection reads them, in row order; a point map with no coordinate
    // fields leaves every mark at the center of the view.
    let coords: Vec<Option<(f64, f64)>> = match fields {
        Some((lat_field, lon_field)) => rows
            .iter()
            .map(|row| Ok(Some((coordinate(row, lon_field, "longitude")?, coordinate(row, lat_field, "latitude")?))))
            .collect::<Result<_, String>>()?,
        None => vec![None; rows.len()],
    };

    // Collapse: one circle per exact coordinate, sized by its count, biggest first.
    let mut groups: Vec<((f64, f64), usize)> = Vec::new();
    if r.collapse {
        let Some((lat_field, lon_field)) = fields else {
            return Err(crate::refuse!("a collapsed point map with no latitude or longitude is not supported").into());
        };
        let mut index: HashMap<(u64, u64), usize> = HashMap::new();
        for row in rows {
            // Vega groups by the raw cell, so a text coordinate is a group of its own.
            let (Some(Value::Number(lon)), Some(Value::Number(lat))) = (row.get(lon_field), row.get(lat_field)) else {
                return Err(crate::refuse!("a collapsed point map with a coordinate that is not a number is not supported").into());
            };
            let (lon, lat) = (lon.as_f64().expect("a JSON number is an f64"), lat.as_f64().expect("a JSON number is an f64"));
            let key = ((lon + 0.0).to_bits(), (lat + 0.0).to_bits());
            match index.get(&key) {
                Some(&g) => groups[g].1 += 1,
                None => {
                    index.insert(key, groups.len());
                    groups.push(((lon, lat), 1));
                }
            }
        }
        groups.sort_by_key(|g| std::cmp::Reverse(g.1));
    }

    let size_values: Vec<f64> = match r.size.as_deref() {
        Some(field) => rows.iter().map(|row| number(row, field, "size")).collect::<Result<_, _>>()?,
        None if r.collapse => groups.iter().map(|g| g.1 as f64).collect(),
        None => Vec::new(),
    };
    let size_max = size_values.iter().copied().fold(0.0_f64, f64::max);
    let size_entries = if r.size.is_some() || r.collapse {
        let title = match r.size.as_deref() {
            Some(field) => color_channel_title(field, &r.legend, &r.field_labels)?,
            None => COUNT_TITLE.to_string(),
        };
        // No drawn row leaves the scale's domain empty: the legend keeps its title alone.
        let (values, labels) =
            if size_values.is_empty() { (Vec::new(), Vec::new()) } else { size_ladder(size_max, r.collapse)? };
        let areas = values.iter().map(|&v| area_of(v, size_max)).collect();
        Some(SizeEntries { title, labels, areas })
    } else {
        None
    };

    let coloring = match color_field {
        None => None,
        Some(field) => Some(bind_color(payload, r, rows, field, color_type.expect("a color field has a type"))?),
    };
    let color_text = |row: &Map<String, Value>, field: &str| -> Result<String, String> {
        match &coloring {
            Some(Coloring::Gradient { scale, .. }) if scale.is_dated() => {
                crate::families::heatmap::legend_date_label(row.get(field).and_then(date_cell_day).expect("filtered above"))
            }
            Some(Coloring::Gradient { .. }) => crate::number_locale::d3_format("", number(row, field, "color")?),
            _ => crate::href::js_string(row.get(field)),
        }
    };
    let fills: Vec<String> = match (&coloring, color_field) {
        (None, _) => vec![r.style.single_series_fill.clone(); rows.len()],
        (Some(Coloring::Categories { domain, fills, .. }), Some(field)) => rows
            .iter()
            .map(|row| {
                let label = category_label(row.get(field)).expect("every value was checked in bind_color");
                fills[domain.iter().position(|d| *d == label).expect("the domain holds every value")].clone()
            })
            .collect(),
        (Some(Coloring::Gradient { scale, .. }), Some(field)) if scale.is_dated() => rows
            .iter()
            .map(|row| scale.fill(row.get(field).and_then(date_cell_day).expect("filtered above")))
            .collect(),
        (Some(Coloring::Gradient { scale, .. }), Some(field)) => {
            rows.iter().map(|row| scale.fill(number(row, field, "color").expect("filtered above"))).collect()
        }
        (Some(_), None) => unreachable!("a coloring comes from a color field"),
    };

    // Tooltip rows common to every circle (titles), and the per-row cells.
    let lon_title = fields.map(|(_, lon)| coordinate_title(r, lon, "tooltip.longitude")).transpose()?;
    let lat_title = fields.map(|(lat, _)| coordinate_title(r, lat, "tooltip.latitude")).transpose()?;
    let color_title = match color_field {
        Some(f) => Some(color_channel_title(f, &r.legend, &r.field_labels)?),
        None => None,
    };
    let fmt = |v: f64| crate::number_locale::d3_format("", v);
    let default_area = r.style.point_mark.size;
    let mut points = Vec::new();
    if r.collapse {
        let (lat_field, lon_field) = fields.expect("checked above");
        let (lat_title, lon_title) = (lat_title.expect("fields are set"), lon_title.expect("fields are set"));
        for &((lon, lat), count) in &groups {
            let n = fmt(count as f64)?;
            let label = describe(vec![
                (lon_field.to_string(), fmt(lon)?),
                (lat_field.to_string(), fmt(lat)?),
                (COUNT_TITLE.to_string(), n.clone()),
                (COUNT_OF_RECORDS.to_string(), n.clone()),
                (lat_title.clone(), fmt(lat)?),
                (lon_title.clone(), fmt(lon)?),
                (COUNT_TITLE.to_string(), n),
            ]);
            let href = if layered { None } else { href_of(payload, r, None, None)? };
            points.push(Point {
                at: Some((lon, lat)),
                area: area_of(count as f64, size_max),
                fill: r.style.single_series_fill.clone(),
                label,
                href,
            });
        }
        let fit_points = groups.iter().map(|g| g.0).collect();
        return Ok(Marks { coloring, size_entries, points, fit_points });
    }

    // Columns that join the tooltip after the described channels: the first row's keys, as
    // `build_point_tooltip_fields` walks them, until the list holds `MAX_TOOLTIP_FIELDS`.
    let named: Vec<&str> = [fields.map(|f| f.0), fields.map(|f| f.1), r.size.as_deref(), color_field].into_iter().flatten().collect();
    let mut others: Vec<&str> = Vec::new();
    // `build_point_tooltip_fields` reads the first row and types over the data before Vega-Lite's filter.
    if let Some(first) = all_rows.first() {
        let mut listed = 2 * usize::from(fields.is_some()) + usize::from(r.size.is_some()) + usize::from(color_field.is_some());
        for field in first.keys() {
            if named.contains(&field.as_str()) {
                continue;
            }
            others.push(field);
            listed += 1;
            if listed >= MAX_TOOLTIP_FIELDS {
                break;
            }
        }
    }
    let other_types: Vec<VegaType> = others.iter().map(|f| infer_vega_type(all_rows, f)).collect();
    let size_title = match r.size.as_deref() {
        Some(f) => Some(color_channel_title(f, &r.legend, &r.field_labels)?),
        None => None,
    };
    let affixed = r.size.is_some() && r.style.tooltip_format.has_affix();
    let other_text = |row: &Map<String, Value>, field: &str, kind: VegaType| -> Result<String, String> {
        match kind {
            VegaType::Quantitative => quantitative_text(row.get(field)),
            VegaType::Temporal => match row.get(field) {
                None | Some(Value::Null) => Ok("null".to_string()),
                Some(cell) => crate::families::heatmap::legend_date_label(
                    date_cell_day(cell).ok_or_else(|| crate::refuse!("a point map tooltip date that does not parse is not supported"))?,
                ),
            },
            _ => crate::href::js_string(row.get(field)),
        }
    };
    if affixed && other_types.contains(&VegaType::Temporal) {
        return Err(crate::refuse!("a point map tooltip date beside an affixed size format is not supported").into());
    }
    for (i, row) in rows.iter().enumerate() {
        let at = coords[i];
        let coordinate_text = |field: &str| quantitative_text(row.get(field));
        let mut rows_described: Vec<(String, String)> = Vec::new();
        if affixed {
            // `NativeTooltipAffixFeature`: the card is the tooltip list alone, one `title: value` row per entry
            // in list order, a repeated title kept.
            if let (Some((lat_field, lon_field)), Some(lat_title), Some(lon_title)) = (fields, &lat_title, &lon_title) {
                rows_described.push((lat_title.clone(), coordinate_text(lat_field)?));
                rows_described.push((lon_title.clone(), coordinate_text(lon_field)?));
            }
            if let Some(field) = r.size.as_deref() {
                rows_described.push((field_title(r, field)?, r.style.tooltip_format.paint(size_values[i])?));
            }
            if let Some(field) = color_field {
                rows_described.push((field_title(r, field)?, color_text(row, field)?));
            }
            for (field, kind) in others.iter().zip(&other_types) {
                rows_described.push((field_title(r, field)?, other_text(row, field, *kind)?));
            }
            let label = rows_described.into_iter().map(|(title, value)| format!("{title}: {value}")).collect::<Vec<_>>().join("; ");
            let href = if layered { None } else { href_of(payload, r, Some(row), color_field)? };
            let area = if r.size.is_some() { area_of(size_values[i], size_max) } else { default_area };
            points.push(Point { at, area, fill: fills[i].clone(), label, href });
            continue;
        }
        // Vega-Lite's description: each described channel, then the tooltip list.
        if let Some((lat_field, lon_field)) = fields {
            rows_described.push((lon_field.to_string(), coordinate_text(lon_field)?));
            rows_described.push((lat_field.to_string(), coordinate_text(lat_field)?));
        }
        if let (Some(field), Some(title)) = (color_field, &color_title) {
            rows_described.push((title.clone(), color_text(row, field)?));
        }
        if let Some(title) = &size_title {
            rows_described.push((title.clone(), fmt(size_values[i])?));
        }
        if let (Some((lat_field, lon_field)), Some(lat_title), Some(lon_title)) = (fields, &lat_title, &lon_title) {
            rows_described.push((lat_title.clone(), coordinate_text(lat_field)?));
            rows_described.push((lon_title.clone(), coordinate_text(lon_field)?));
        }
        if let (Some(field), Some(_)) = (r.size.as_deref(), &size_title) {
            rows_described.push((field_title(r, field)?, r.style.tooltip_format.paint(size_values[i])?));
        }
        if let Some(field) = color_field {
            rows_described.push((field_title(r, field)?, color_text(row, field)?));
        }
        for (field, kind) in others.iter().zip(&other_types) {
            rows_described.push((field_title(r, field)?, other_text(row, field, *kind)?));
        }
        let href = if layered { None } else { href_of(payload, r, Some(row), color_field)? };
        let area = if r.size.is_some() { area_of(size_values[i], size_max) } else { default_area };
        points.push(Point { at, area, fill: fills[i].clone(), label: describe(rows_described), href });
    }
    // A null coordinate is projected as zero, but Vega-Lite leaves it out of the fit.
    let fit_points = match fields {
        Some((lat_field, lon_field)) => all_rows
            .iter()
            .filter(|row| !row.get(lat_field).is_some_and(Value::is_null) && !row.get(lon_field).is_some_and(Value::is_null))
            .map(|row| Ok((coordinate(row, lon_field, "longitude")?, coordinate(row, lat_field, "latitude")?)))
            .collect::<Result<_, String>>()?,
        None => Vec::new(),
    };
    Ok(Marks { coloring, size_entries, points, fit_points })
}

/// The circle's link: `link` with its `{{ color }}` placeholder read from the row.
fn href_of(
    payload: &Payload,
    r: &Resolved,
    row: Option<&Map<String, Value>>,
    color_field: Option<&str>,
) -> Result<Option<String>, Diagnostic> {
    let Some(template) = r.link.as_deref() else { return Ok(None) };
    let href = crate::href::resolve_channel_href(template, |channel| {
        let value = match (channel, row, color_field) {
            ("color", Some(row), Some(field)) => row.get(field),
            _ => {
                return Err(format!(
                    "link template references channel '{{{{ {channel} }}}}' but chart has no '{channel}' encoding assigned"
                ))
            }
        };
        crate::href::js_string(value)
    })?;
    let href = match &payload.link_context {
        Some(ctx) => crate::href::resolve_href(&href, ctx),
        None => href,
    };
    crate::href::checked_href(&href)?;
    Ok(Some(href))
}

/// A nominal color cell's text; Vega keys a null as the entry `null`. `None` for any other kind of cell.
fn category_label(cell: Option<&Value>) -> Option<String> {
    match cell {
        Some(Value::String(s)) => Some(s.clone()),
        Some(Value::Null) => Some("null".to_string()),
        _ => None,
    }
}

/// The color channel over `rows`, which hold only the cells Vega-Lite draws (`kind` types the field).
fn bind_color(
    payload: &Payload,
    r: &Resolved,
    rows: &[Map<String, Value>],
    field: &str,
    kind: VegaType,
) -> Result<Coloring, Diagnostic> {
    let title = color_channel_title(field, &r.legend, &r.field_labels)?;
    let extent = |values: Vec<f64>| -> Result<(f64, f64), Diagnostic> {
        if values.is_empty() {
            return Err(crate::refuse!("a point map color with no drawn row is not supported").into());
        }
        Ok(values.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), &v| (lo.min(v), hi.max(v))))
    };
    match kind {
        VegaType::Quantitative => {
            let values = rows.iter().map(|row| number(row, field, "color")).collect::<Result<Vec<_>, _>>()?;
            let (lo, hi) = extent(values)?;
            Ok(Coloring::Gradient { scale: ContinuousColor::blues(lo, hi), title })
        }
        VegaType::Temporal => {
            let days = rows.iter().map(|row| row.get(field).and_then(date_cell_day).expect("filtered in bind_marks")).collect();
            let (lo, hi) = extent(days)?;
            Ok(Coloring::Gradient { scale: ContinuousColor::dates(&d3rs::vega_schemes::BLUES, lo, hi), title })
        }
        VegaType::Nominal => {
            let mut domain: Vec<String> = Vec::new();
            let mut has_null = false;
            for row in rows {
                match (row.get(field), category_label(row.get(field))) {
                    (Some(Value::Null), _) => has_null = true,
                    (_, Some(label)) => {
                        if !domain.contains(&label) {
                            domain.push(label);
                        }
                    }
                    _ => return Err(crate::refuse!("a point map nominal color value that is not text is not supported").into()),
                }
            }
            domain.sort();
            if has_null {
                // Vega sorts a null ahead of every text; a text `null` would be a second entry of one name.
                if domain.iter().any(|d| d == "null") {
                    return Err(crate::refuse!("a point map nominal color holding both a null and the text null is not supported").into());
                }
                domain.insert(0, "null".to_string());
            }
            let palette = payload.series_palette(&r.palette);
            // The board's slots apply only to a chart that carries its own palette.
            let scale = if r.palette.is_empty() { None } else { category_scale_for(&r.category_colors, field) };
            let fills = domain.iter().map(|v| series_color(v, &domain, &palette, scale)).collect::<Result<Vec<_>, _>>()?;
            Ok(Coloring::Categories { domain, fills, title })
        }
        VegaType::Ordinal => Err(crate::refuse!("a point map color field that is date-like text is not supported").into()),
    }
}

/// The circle's path at `area`, as Vega's `circle` symbol draws it.
fn circle_path(area: f64) -> Result<String, String> {
    if area == 0.0 {
        return Ok("M0,0".into());
    }
    crate::families::scatter_shape::path("circle", crate::common::symbol_radius(area))
}

/// Fit the bound map to the frame: the plot rect, the basemap, the circles, the legends.
pub fn layout(bound: &Bound, frame: &ChartFrame, title: &FrameTitle, fonts: &FontBook) -> Result<Layout, Diagnostic> {
    let r = &bound.resolved;
    let fit = bound.fit;
    let legend_cfg = vega_default_legend(&r.legend);
    // A gradient legend's labels sit Vega's `gradientLabelOffset` (2) off the bar, not a symbol legend's 4.
    let mut gradient_cfg = vega_default_legend(&r.legend);
    gradient_cfg.label.padding = Some(2.0);
    let basemap = bound.basemap.as_ref().map(|features| {
        GeoJson::FeatureCollection(FeatureCollection {
            features: features
                .iter()
                .map(|f| match f {
                    GeoJson::Feature(feature) => feature.clone(),
                    _ => unreachable!("bind holds features only"),
                })
                .collect(),
        })
    });
    let point_data = GeoJson::FeatureCollection(FeatureCollection {
        features: bound
            .fit_points
            .iter()
            .map(|&(lon, lat)| Feature {
                id: None,
                bbox: None,
                properties: Value::Null,
                geometry: Some(Geometry::Point { coordinates: vec![lon, lat] }),
            })
            .collect(),
    });
    // Vega-Lite fits the layers in order: the basemap, then the points.
    let fit_data: Vec<GeoJson> = basemap.iter().cloned().chain((!bound.fit_points.is_empty()).then_some(point_data)).collect();
    let fitted_at = |pw: f64, ph: f64| {
        projection::fitted(&r.geo_projection_type, r.geo_projection_params.as_ref(), fit_data.clone(), (pw, ph))
    };

    let grad_id = format!("geo_grad-{}", contract::safe_svg_id(&r.id));
    let folded_label;
    let symbol_entries: Vec<LegendEntry> = match &bound.coloring {
        Some(Coloring::Categories { domain, fills, .. }) => {
            let mut entries: Vec<LegendEntry> = domain
                .iter()
                .zip(fills)
                .map(|(label, fill)| LegendEntry { label, fill, symbol: Some(LegendSymbol::FilledCircle), series_key: None })
                .collect();
            if let Some((kept, label)) = fold_symbol_limit(entries.len(), VEGA_LEGEND_SYMBOL_LIMIT as usize) {
                folded_label = label;
                let folded = LegendEntry { label: &folded_label, ..entries[kept] };
                entries.truncate(kept);
                entries.push(folded);
            }
            entries
        }
        _ => Vec::new(),
    };
    let symbol_legend_layout = match &bound.coloring {
        Some(Coloring::Categories { title, .. }) => Some(vega_legend(&symbol_entries, false, title, &legend_cfg, fonts)?),
        _ => None,
    };
    let size_legend = bound
        .size_entries
        .as_ref()
        .map(|s| -> Result<SizeLegend, String> {
            let label_ws = crate::families::legend::size_label_widths(&s.labels, &legend_cfg, fonts)?;
            let glyphs = s
                .areas
                .iter()
                .map(|&area| crate::families::scatter_shape::path("circle", crate::common::symbol_radius(area)))
                .collect::<Result<_, _>>()?;
            let rows = SizeRows { labels: s.labels.clone(), glyphs, areas: s.areas.clone(), label_ws };
            let glyph = crate::families::scatter_shape::half_extents("circle", 1.0);
            SizeLegend::new(&s.title, rows, (SYMBOL_LABEL_OFFSET, false), glyph, &legend_cfg, fonts)
        })
        .transpose()?;
    let gradient_at = |pw: f64, ph: f64| -> Result<Option<GradientAt>, String> {
        match &bound.coloring {
            Some(Coloring::Gradient { scale, .. }) => GradientAt::new(&gradient_cfg, None, scale, (pw, ph)).map(Some),
            _ => Ok(None),
        }
    };
    // The legends' boxes at plot size `(pw, ph)`, color first.
    let blocks_at = |pw: f64, ph: f64| -> Result<Vec<(f64, f64)>, String> {
        let mut blocks = Vec::new();
        if let Some(legend) = &symbol_legend_layout {
            blocks.push((legend.box_w, legend.box_h()));
        }
        if let (Some(at), Some(Coloring::Gradient { scale, title })) = (gradient_at(pw, ph)?, &bound.coloring) {
            let legend = at.legend(&gradient_cfg, scale, &grad_id, title, fonts)?;
            blocks.push((legend.box_width(at.length), legend.height(at.length)));
        }
        if let Some(legend) = &size_legend {
            blocks.push((legend.w, legend.h));
        }
        Ok(blocks)
    };

    let stroke_reach = if r.basemap_stroke.is_some() { 1.0 } else { 0.0 };
    let mut stack_xy = (0.0, 0.0);
    let mut failure: Option<Diagnostic> = None;
    let mut ink = |pw: f64, ph: f64, b: &mut Bounds| -> Result<(), Diagnostic> {
        if let (true, Some(data)) = (fit, &basemap) {
            let path = fitted_at(pw, ph)?.into_path();
            let [[x0, y0], [x1, y1]] = path.bounds(data)?;
            b.union(&Bounds::new(x0, y0, x1, y1).expand(stroke_reach));
        }
        let blocks = blocks_at(pw, ph)?;
        if !blocks.is_empty() {
            let (_, size) = stack_legends(&blocks, false);
            stack_xy = legend_origin(&legend_cfg.position, (0.0, pw), (0.0, ph), (pw, ph), size);
            if let Some(extent) = legend_fit_bounds(&legend_cfg.position, stack_xy, size) {
                b.union(&extent);
            }
        }
        Ok(())
    };
    let m = frame.fit(title, |pw, ph, b| {
        if failure.is_none() {
            failure = ink(pw, ph, b).err();
        }
    });
    if let Some(error) = failure {
        return Err(error);
    }
    let (plot_left, plot_top) = frame.origin(&m);
    let (plot_w, plot_h) = (m.plot_w, m.plot_h);
    // With nothing to fit (no basemap, no coordinates) no mark needs the projection.
    let fitted = if fit_data.is_empty() { None } else { Some(fitted_at(plot_w, plot_h)?) };
    let placed: Vec<[f64; 2]> = bound
        .points
        .iter()
        .map(|p| match p.at {
            // No coordinate fields: the mark sits at the view's center.
            None => Ok([plot_w / 2.0, plot_h / 2.0]),
            // A point the projection drops (`albersUsa` outside its insets) has no position, and Vega
            // draws that mark at the view's origin.
            // With every coordinate null nothing is fitted and Vega draws the mark at the origin too.
            Some((lon, lat)) => match fitted.as_ref().and_then(|f| f.forward([lon, lat])) {
                None => Ok([0.0, 0.0]),
                Some(xy) if xy[0].is_finite() && xy[1].is_finite() => Ok(xy),
                Some(_) => Err(crate::refuse!("a point the projection places at a non-finite position is not supported")),
            },
        })
        .collect::<Result<_, String>>()?;
    let basemap_paths: Vec<Option<String>> = match &bound.basemap {
        Some(features) => {
            let path = fitted_at(plot_w, plot_h)?.into_path();
            features.iter().map(|f| path.path(f)).collect::<Result<_, _>>()?
        }
        None => Vec::new(),
    };

    let dim = frame.dim;
    let safe = contract::safe_svg_id(&r.id);
    let layered = bound.basemap.is_some();
    let clip_ids = ["geo-clip-0", "geo-clip-1"].map(|id| format!("{id}-{safe}"));
    let group = |name: &str, kind: &str, clip: Option<&str>, role: &str, children: Vec<Node>| -> Result<Node, Diagnostic> {
        let mut attrs = Attrs::new().set("class", format!("mark-{kind} role-mark {name}"));
        if let Some(id) = clip {
            attrs = attrs.set("clip-path", format!("url(#{id})"));
        }
        let container = display_text::language::tr(&format!("aria.container_{kind}"), None, &[])?;
        Ok(Node::group(attrs.set("role", role).set("aria-roledescription", container), children))
    };

    let mut layers = Vec::new();
    if layered {
        let shapes = basemap_paths
            .iter()
            .map(|d| {
                let mut attrs = Attrs::new().set("transform", "translate(0,0)");
                if let Some(d) = d {
                    attrs = attrs.set("d", d.as_str());
                }
                attrs = attrs.set("fill", r.basemap_fill.as_deref().expect("checked in bind"));
                if let Some(stroke) = &r.basemap_stroke {
                    attrs = attrs.set("stroke", stroke.as_str());
                }
                Node::Path(attrs)
            })
            .collect();
        // Vega-Lite clips a basemap whose projection the author placed.
        layers.push(group("layer_0_marks", "shape", (!fit).then_some(clip_ids[0].as_str()), "graphics-symbol", shapes)?);
    }
    let opacity = js_number_string(r.style.point_mark.opacity);
    let mut circles = Vec::new();
    for (p, xy) in bound.points.iter().zip(&placed) {
        let attrs = Attrs::new()
            .set("aria-label", p.label.as_str())
            .set("role", "graphics-symbol")
            .set("aria-roledescription", "circle")
            .set("transform", format!("translate({},{})", js_number_string(xy[0]), js_number_string(xy[1])))
            .set("d", circle_path(p.area)?)
            .set("fill", p.fill.as_str())
            .set("stroke-width", "2")
            .set("opacity", opacity.as_str());
        let path = Node::Path(attrs);
        circles.push(match &p.href {
            Some(url) => {
                let attrs = Attrs::new().set("href", url.as_str());
                let attrs = r.link_open.open_attr().map_or(attrs.clone(), |open| attrs.set("data-dbt-link-open", open));
                let attrs = r.link_open.target().map_or(attrs.clone(), |target| attrs.set("target", target));
                Node::Link { attrs, children: vec![path] }
            }
            _ => path,
        });
    }
    let points_clip = &clip_ids[usize::from(layered)];
    layers.push(group(
        if layered { "layer_1_marks" } else { "marks" },
        "symbol",
        Some(points_clip),
        "graphics-object",
        circles,
    )?);
    let mut nodes = vec![Node::group(
        Attrs::new().set("transform", format!("translate({plot_left:.2},{plot_top:.2})")),
        layers,
    )];

    // Legends: color, then size, stacked a margin apart at the right.
    let blocks = blocks_at(plot_w, plot_h)?;
    let (offsets, _) = stack_legends(&blocks, false);
    let mut offsets = offsets.into_iter();
    let (leg_x, leg_y) = (plot_left + stack_xy.0, plot_top + stack_xy.1);
    match &bound.coloring {
        Some(Coloring::Categories { .. }) => {
            let legend = symbol_legend_layout.as_ref().expect("built with the categories");
            let off = offsets.next().expect("one block per legend");
            nodes.push(symbol_legend(&symbol_entries, legend, &legend_cfg, LegendSymbol::FilledCircle, leg_x, leg_y + off, false));
        }
        Some(Coloring::Gradient { scale, title }) => {
            let at = gradient_at(plot_w, plot_h)?.expect("a gradient coloring");
            let legend = at.legend(&gradient_cfg, scale, &grad_id, title, fonts)?;
            let off = offsets.next().expect("one block per legend");
            nodes.extend(legend.nodes(leg_x, leg_y + off, at.length));
        }
        None => {}
    }
    if let Some(legend) = &size_legend {
        let off = offsets.next().expect("one block per legend");
        let opacity = r.style.point_mark.opacity;
        let paint = if bound.coloring.is_some() {
            SizePaint::Black { opacity: r.style.point_mark.opacity }
        } else {
            SizePaint::Static { fill: &r.style.single_series_fill, opacity }
        };
        nodes.extend(legend.nodes(&legend_cfg, paint, leg_x, leg_y + off));
    }

    let clip = |id: &String| Node::ClipPath {
        attrs: Attrs::new().set("id", id.as_str()),
        children: vec![Node::Rect(
            Attrs::new().set("x", "0").set("y", "0").set("width", dim(plot_w)).set("height", dim(plot_h)),
        )],
    };
    let mut defs = Vec::new();
    if layered && !fit {
        defs.push(clip(&clip_ids[0]));
    }
    defs.push(clip(points_clip));
    nodes.push(Node::Defs(defs));

    let data_box = Rect { x: plot_left, y: plot_top, width: plot_w, height: plot_h };
    let cell = Rect { x: 0.0, y: 0.0, width: frame.view_w, height: frame.h };
    Ok(Layout { insets: cell.insets_of(&data_box), data_box, nodes, diagnostics: title.truncations(&r.id), title_pos: m.title_pos })
}

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    let bound = bind(payload)?;
    let r = &bound.resolved;
    let frame = ChartFrame::of(payload, &r.background);
    let title = FrameTitle::new(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        r.style.title_font.as_ref(),
        r.title_style.as_ref(),
        frame.avail_w(),
        fonts,
    )?;
    let laid = layout(&bound, &frame, &title, fonts)?;
    for diagnostic in laid.diagnostics {
        crate::diagnostic::warn(diagnostic);
    }
    let mut svg = String::new();
    frame.open(&mut svg);
    title.paint(&mut svg, laid.title_pos, (laid.data_box.x, laid.data_box.y));
    svg.push_str(&print(&laid.nodes));
    svg.push_str("</svg>");
    Ok(svg)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(value: Value) -> Map<String, Value> {
        value.as_object().unwrap().clone()
    }

    #[test]
    fn the_count_ladder_strides_with_the_domain_and_never_passes_it() {
        assert_eq!(count_ladder(3.0), vec![1.0, 2.0, 3.0]);
        assert_eq!(count_ladder(7.0), vec![2.0, 4.0, 6.0]);
        assert_eq!(count_ladder(11.0), vec![5.0, 10.0]);
        assert_eq!(count_ladder(40.0), vec![10.0, 20.0, 30.0, 40.0]);
        assert_eq!(count_ladder(60.0), vec![20.0, 40.0, 60.0]);
        assert_eq!(count_ladder(200.0), vec![50.0, 100.0, 150.0, 200.0]);
        assert_eq!(count_ladder(400.0), vec![100.0, 200.0, 300.0, 400.0]);
        assert_eq!(count_ladder(900.0), vec![200.0, 400.0, 600.0, 800.0]);
        assert_eq!(count_ladder(1700.0), vec![500.0, 1000.0, 1500.0]);
    }

    #[test]
    fn a_size_ladder_skips_the_zero_tick_and_groups_thousands() {
        let (values, labels) = size_ladder(35.0, false).unwrap();
        assert_eq!(values, vec![5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0]);
        assert_eq!(labels.last().unwrap(), "35");
        let (_, labels) = size_ladder(4_500_000.0, false).unwrap();
        assert_eq!(labels, vec!["1,000,000", "2,000,000", "3,000,000", "4,000,000"]);
        let (_, labels) = size_ladder(1.0, false).unwrap();
        assert_eq!(labels, vec!["0.2", "0.4", "0.6", "0.8", "1.0"]);
        let (_, counts) = size_ladder(1200.0, true).unwrap();
        assert_eq!(counts, vec!["500", "1000"]);
    }

    #[test]
    fn area_is_proportional_to_the_value() {
        assert_eq!(area_of(0.0, 8.0), 0.0);
        assert_eq!(area_of(4.0, 8.0), 500.0);
        assert_eq!(area_of(8.0, 8.0), 1000.0);
    }

    #[test]
    fn a_zero_area_circle_is_an_empty_path() {
        assert_eq!(circle_path(0.0).unwrap(), "M0,0");
        assert_eq!(circle_path(60.0).unwrap(), "M3.873,0A3.873,3.873,0,1,1,-3.873,0A3.873,3.873,0,1,1,3.873,0");
    }

    #[test]
    fn a_description_lists_a_title_once_at_its_first_place() {
        let rows = vec![
            ("lat".to_string(), "1".to_string()),
            ("Size".to_string(), "5".to_string()),
            ("Latitude".to_string(), "1".to_string()),
            ("Size".to_string(), "5".to_string()),
        ];
        assert_eq!(describe(rows), "lat: 1; Size: 5; Latitude: 1");
    }

    #[test]
    fn the_albers_usa_boxes_include_their_edges_and_leave_unreadable_rows_undecided() {
        let inside = |lat: Value, lon: Value| in_albers_usa(&row(json!({"a": lat, "o": lon})), "a", "o");
        assert_eq!(inside(json!(24.0), json!(-125.0)), Some(true));
        assert_eq!(inside(json!(61.2), json!(-149.9)), Some(true));
        assert_eq!(inside(json!(21.3), json!(-157.8)), Some(true));
        assert_eq!(inside(json!(23.5), json!(-157.8)), Some(false));
        assert_eq!(inside(json!("40.7"), json!("-74")), Some(true));
        assert_eq!(inside(json!(true), json!(false)), Some(false));
        assert_eq!(inside(Value::Null, json!(-74.0)), None);
        assert_eq!(inside(json!("north"), json!(-74.0)), None);
        assert_eq!(inside(json!([1]), json!(-74.0)), None);
    }

    #[test]
    fn only_a_readable_negative_size_is_negative() {
        let negative = |v: Value| has_negative_size(&row(json!({"s": v})), "s");
        assert!(negative(json!(-0.5)) && negative(json!("-2")));
        assert!(!negative(json!(0)) && !negative(Value::Null) && !negative(json!("many")));
        assert!(!has_negative_size(&row(json!({})), "s"));
    }

    #[test]
    fn a_numeric_column_cell_prints_as_a_number_or_null() {
        assert_eq!(quantitative_text(Some(&json!(2.5))).unwrap(), "2.5");
        assert_eq!(quantitative_text(None).unwrap(), "null");
        assert_eq!(quantitative_text(Some(&Value::Null)).unwrap(), "null");
        assert_eq!(quantitative_text(Some(&json!("34.0"))).unwrap(), "34");
        assert_eq!(quantitative_text(Some(&json!("x"))).unwrap(), "NaN");
        assert!(quantitative_text(Some(&json!([1]))).unwrap_err().ends_with("not supported"));
    }
}
