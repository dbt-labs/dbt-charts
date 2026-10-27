//! Geoshape family (`type: map` / `type: geoshape`): a base map, or a choropleth
//! (`emitters/geo.py` `GeoshapeEmitter`). Production hands Vega-Lite a `geoshape`
//! spec; this draws what Vega draws from it:
//!
//! - a background layer of every feature in the neutral fill, then, for a
//!   choropleth, an overlay joined to the rows (`lookup` on the join key, last
//!   row wins, keys compared as JS strings): a quantitative overlay keeps the
//!   features whose row has a number, a nominal one every feature, the
//!   unmatched stroked with no fill;
//! - the projection fitted to the view the way Vega-Lite assembles it
//!   ([`super::projection`]);
//! - a gradient legend for a quantitative value, a symbol legend for a nominal one.
//!
//! A choropleth's lookup layers carry no link, so neither layer is wrapped; rows that match no
//! feature leave the overlay empty and a quantitative legend over an empty (`NaN`) domain.
//!
//! `bind` holds what the data decides; `layout` fits it to the cell and returns
//! nodes, which `render` prints between the frame and the title.

use std::collections::HashMap;
use std::sync::Arc;

use d3rs::geojson::{Feature, FeatureCollection, GeoJson};
use serde::Deserialize;
use serde_json::{json, Map, Value};

use super::js_cell;
use super::projection;
use super::source::{decode, Decoded, GeoSource};
use crate::codes;
use crate::common::{
    category_scale_for, series_color, CategoryColorScale, FieldLabels, FormatSlot, Font, ScaleTarget,
    Stroke, TitleStyle,
};
use crate::contract;
use crate::diagnostic::Diagnostic;
use crate::families::color_scale::{js_number_string, ContinuousColor};
use crate::families::heatmap::{category_legend_entries, CategoryScale};
use crate::families::color_scale::GradientAt;
use crate::families::legend::{
    color_channel_title, legend_fit_bounds, legend_origin, legend_slide, legend_title_text, slide_block,
    symbol_legend, vega_legend, fold_symbol_limit, LegendConfig, LegendSymbol, VEGA_LEGEND_SYMBOL_LIMIT,
};
use crate::frame::{ChartFrame, FrameTitle};
use crate::href::LinkOpen;
use crate::layout::{Layout, Rect};
use scene::{Attrs, Node};
use print::print;
use measure::FontBook;
use crate::vega_fit::Bounds;
use crate::Payload;

/// The slice of a resolved geoshape chart this family reads.
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
    resolved_channels: HashMap<String, Value>,
    style: Style,
    geo_url: String,
    geo_format_type: String,
    geo_feature: Option<String>,
    geo_property: Option<String>,
    geo_join_key: String,
    geo_projection_type: String,
    geo_projection_params: Option<Map<String, Value>>,
    lookup_field: Option<String>,
    value_field: Option<String>,
    #[serde(default)]
    geo_key_format: String,
    #[serde(default)]
    geo_key_examples: Vec<String>,
    #[serde(default)]
    geo_source_name: String,
}

#[derive(Deserialize)]
struct Style {
    geoshape: FamilyStyle,
    tooltip_format: FormatSlot,
    title_font: Option<Font>,
}

#[derive(Deserialize)]
struct FamilyStyle {
    color: Option<ColorStyle>,
    marks: Marks,
}

#[derive(Deserialize)]
struct ColorStyle {
    gradient: Option<ScaleTarget>,
}

#[derive(Deserialize)]
struct Marks {
    geoshape: Option<MarkStyle>,
}

#[derive(Deserialize)]
struct MarkStyle {
    fill: Option<String>,
    stroke: Option<Stroke>,
}

/// How the overlay colors its features and what the legend lists.
enum Coloring {
    /// A nominal value: one fill per distinct string, in first-seen order.
    Categories(CategoryScale),
    /// A quantitative value: the scale over the matched values, and the legend
    /// ends it pins (`None`: Vega's own ticks).
    Gradient { scale: ContinuousColor, legend_ends: Option<Vec<String>> },
}

/// An overlay feature and what it paints; a nominal feature no row matched has no fill.
struct Overlay {
    feature: usize,
    fill: Option<String>,
    label: String,
}

struct Choropleth {
    coloring: Coloring,
    overlay: Vec<Overlay>,
    title: String,
}

/// What the data decides: the features, the join and the colors.
pub struct Bound {
    resolved: Resolved,
    decoded: Arc<Decoded>,
    choropleth: Option<Choropleth>,
    /// The base map's link; a choropleth's overlay carries none.
    href: Option<String>,
}

impl Resolved {
    fn mark(&self) -> Result<&MarkStyle, String> {
        self.style.geoshape.marks.geoshape.as_ref().ok_or_else(|| {
            "geoshape: style.geoshape.marks.geoshape absent (theme required)".to_string()
        })
    }

    /// Vega-Lite clips a map whose projection the author placed (`scale` or `translate`);
    /// a fitted one is unclipped and its ink joins the view bounds.
    fn is_fit(&self) -> bool {
        projection::is_fit(self.geo_projection_params.as_ref())
    }

    /// How far the mark stroke grows the paths' bounds (`boundStroke`): its width, Vega's 1 without one.
    fn stroke_reach(&self) -> Result<f64, String> {
        let stroke = self.mark()?.stroke.as_ref();
        Ok(stroke.and_then(|s| Some((s.color.as_deref()?, s.width.unwrap_or(1.0)))).map_or(0.0, |(color, width)| {
            if color == "transparent" {
                0.0
            } else {
                width
            }
        }))
    }
}

/// A member of a datum as Vega's field accessor reads it: an object's key or a list's index.
fn member<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    match value {
        Value::Array(items) => items.get(key.parse::<usize>().ok()?),
        other => other.get(key),
    }
}

/// Each feature's join key text, read by `geo_join_key`'s dotted path from the datum Vega holds: a GeoJSON
/// item as the document spells it, a TopoJSON feature as `topojson.feature` builds it.
fn join_keys(source: &GeoSource, decoded: &Decoded, path: &str) -> Result<Vec<String>, String> {
    let walk = |root: Option<&Value>| -> Result<String, String> {
        let value = path.split('.').try_fold(root, |value, key| Some(member(value?, key)));
        crate::href::js_string(value.flatten())
    };
    let first = path.split('.').next();
    if matches!(first, Some("id" | "properties")) {
        let rest = path.split_once('.').map(|(_, rest)| rest);
        return decoded
            .features
            .iter()
            .map(|feature| {
                let root = if first == Some("id") { feature.id.as_ref() } else { Some(&feature.properties) };
                let value = rest.map_or(Ok(root), |rest| rest.split('.').try_fold(root, |value, key| Ok::<_, String>(value.and_then(|v| member(v, key)))))?;
                crate::href::js_string(value)
            })
            .collect();
    }
    if source.format_type == "json" {
        return decoded.items.iter().map(|item| walk(Some(item))).collect();
    }
    decoded
        .features
        .iter()
        .map(|feature| walk(Some(&serde_json::to_value(feature).map_err(|e| e.to_string())?)))
        .collect()
}

/// `validate_map_lookup_key_contract`: a numeric-keyed source needs a lookup
/// column with at least one integer-like value.
fn check_lookup_key(r: &Resolved, lookup: &str, rows: &[Map<String, Value>]) -> Result<(), Diagnostic> {
    if r.geo_key_format != "numeric" {
        return Ok(());
    }
    let mut samples: Vec<String> = Vec::new();
    for value in rows.iter().filter_map(|row| row.get(lookup)).filter(|v| !v.is_null()) {
        let text = python_str(value);
        if !samples.contains(&text) {
            samples.push(text);
        }
        if samples.len() == 5 {
            break;
        }
    }
    if samples.is_empty() || rows.iter().filter_map(|row| row.get(lookup)).any(is_integer_key) {
        return Ok(());
    }
    let source = [r.geo_source_name.as_str(), r.geo_url.as_str()].into_iter().find(|s| !s.is_empty()).unwrap_or("custom");
    Err(Diagnostic::new(
        &codes::ERR_MAP_LOOKUP_KEY_MISMATCH,
        [
            ("chart_id", json!(r.id)),
            ("geo_source", json!(source)),
            ("geo_key", json!(r.geo_join_key)),
            ("lookup_field", json!(lookup)),
            ("expected_format", json!("numeric")),
            ("expected_samples", json!(r.geo_key_examples)),
            ("query_samples", json!(samples)),
        ],
    ))
}

/// Python's `str(value)` of a JSON cell.
fn python_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.to_string(),
        Value::Number(n) if n.is_f64() => d3rs::py_float::float_repr(n.as_f64().expect("is_f64")),
        other => other.to_string(),
    }
}

/// `utils.is_integer_key_value`.
fn is_integer_key(value: &Value) -> bool {
    match value {
        Value::Number(n) => n.is_i64() || n.is_u64() || n.as_f64().is_some_and(|f| f.fract() == 0.0 && f.is_finite()),
        Value::String(s) => {
            let stripped = s.trim();
            !stripped.is_empty()
                && (stripped.chars().all(|c| c.is_ascii_digit())
                    || stripped.parse::<f64>().is_ok_and(|f| f.is_finite() && f.fract() == 0.0))
        }
        _ => false,
    }
}

/// `+value` as Vega-Lite's quantitative filter reads it (`isValid(x) && isFinite(+x)`).
fn finite_number(value: Option<&Value>) -> Option<f64> {
    match value {
        None | Some(Value::Null) => None,
        cell => Some(js_cell::number(cell)).filter(|n| n.is_finite()),
    }
}

/// Vega-Lite's description of a mark: the color row, then the lookup column.
fn native_label(color: (&str, String), lookup: Option<(&str, String)>) -> String {
    let mut rows = vec![(color.0, color.1)];
    rows.extend(lookup);
    contract::native_label(&rows)
}

/// The card a measure format with an affix writes (`NativeTooltipAffixFeature`): the tooltip list
/// alone, the lookup row then the value row, one `title: value` each, a repeated title kept.
fn affixed_label(value: Option<(&str, String)>, lookup: Option<(&str, String)>) -> String {
    let rows = lookup.into_iter().chain(value);
    rows.map(|(title, text)| format!("{title}: {text}")).collect::<Vec<_>>().join("; ")
}

/// The features a lookup column matches and how each is painted.
fn bind_choropleth(
    r: &Resolved,
    features: &[Feature],
    keys: &[String],
    rows: &[Map<String, Value>],
    (lookup, value_field): (&str, &str),
) -> Result<Choropleth, Diagnostic> {
    check_lookup_key(r, lookup, rows)?;
    // Vega's lookup index is a JS object: keys are their string coercion (a null key is `"null"`,
    // so a null row joins a feature whose key is null) and the last row of a key wins.
    let mut index: HashMap<String, &Map<String, Value>> = HashMap::new();
    for row in rows {
        index.insert(crate::href::js_string(row.get(lookup))?, row);
    }
    let row_of = |i: usize| index.get(&keys[i]).copied();
    let title = color_channel_title(value_field, &r.legend, &r.field_labels)?;
    let lookup_title = display_text::slug_to_text(lookup).map(|t| r.field_labels.get(lookup).cloned().unwrap_or(t))?;
    let in_data = rows.first().is_some_and(|row| row.contains_key(lookup));
    // Vega-Lite describes the lookup column after the color; an unmatched feature has it null.
    let lookup_row = |row: Option<&Map<String, Value>>| -> Result<Option<(&str, String)>, String> {
        if !in_data || value_field == lookup {
            return Ok(None);
        }
        let text = row.map_or(Ok("null".to_string()), |row| crate::href::js_string(row.get(lookup)))?;
        Ok(Some((lookup_title.as_str(), text)))
    };
    // The tooltip list drops the value row when the value is the lookup column itself.
    let value_title = (value_field != lookup)
        .then(|| display_text::slug_to_text(value_field).map(|t| r.field_labels.get(value_field).cloned().unwrap_or(t)))
        .transpose()?;
    let affixed = r.style.tooltip_format.has_affix();
    let own_type = crate::families::temporal::infer_vega_type(rows, value_field);
    let mut overlay = Vec::new();
    let coloring = if own_type == crate::families::temporal::VegaType::Quantitative {
        let gradient = r
            .style
            .geoshape
            .color
            .as_ref()
            .and_then(|c| c.gradient.as_ref())
            .ok_or("geoshape: style.geoshape.color.gradient absent (theme required)")?;
        let mut drawn: Vec<(usize, f64, &Map<String, Value>)> = Vec::new();
        for i in 0..features.len() {
            let Some(row) = row_of(i) else { continue };
            if let Some(n) = finite_number(row.get(value_field)) {
                drawn.push((i, n, row));
            }
        }
        // With no drawn value Vega's color domain is empty: its ends are NaN.
        let (lo, hi) = drawn
            .iter()
            .fold((f64::NAN, f64::NAN), |(lo, hi), &(_, n, _)| (if lo.is_nan() { n } else { lo.min(n) }, if hi.is_nan() { n } else { hi.max(n) }));
        // The python seam derives the same domain from the rows that joined a feature, so the
        // widened ends agree with the baked Vega-Lite scale; no drawn value leaves it empty.
        let extent = (lo.is_finite() && hi.is_finite()).then_some((lo, hi));
        let scale = ContinuousColor::gradient(gradient, extent, (lo, hi), r.legend.visible)?;
        for (feature, n, row) in drawn {
            let label = if affixed {
                let value = value_title.as_deref().map(|t| r.style.tooltip_format.paint(n).map(|text| (t, text))).transpose()?;
                affixed_label(value, lookup_row(Some(row))?)
            } else {
                native_label((&title, crate::number_locale::d3_format("", n)?), lookup_row(Some(row))?)
            };
            overlay.push(Overlay { feature, fill: Some(scale.fill(n)), label });
        }
        // Both bounds authored, or `nice: false`: the legend labels the scale's ends
        // (`apply_geo_choropleth_legend_endpoint_labels`); a nice scale keeps Vega's ticks.
        let legend_ends = (r.legend.values.is_none()
            && ((gradient.min.is_some() && gradient.max.is_some()) || gradient.nice == Some(false) || (gradient.nice.is_none() && !r.legend.visible)))
            .then(|| vec![js_number_string(scale.lo), js_number_string(scale.hi)]);
        Coloring::Gradient { scale, legend_ends }
    } else {
        let mut domain: Vec<String> = Vec::new();
        // Production's domain holds the text cells alone: a cell of any other kind keeps no fill.
        for row in rows {
            if let Some(Value::String(s)) = row.get(value_field) {
                if !domain.contains(s) {
                    domain.push(s.clone());
                }
            }
        }
        let scale = category_scale_for(&r.category_colors, value_field);
        let fills = domain.iter().map(|v| series_color(v, &domain, &r.palette, scale)).collect::<Result<Vec<_>, _>>()?;
        for i in 0..features.len() {
            let (fill, value, row) = match row_of(i) {
                Some(row) => match row.get(value_field) {
                    Some(Value::String(value)) => {
                        let slot = domain.iter().position(|d| d == value).expect("the domain holds every text cell");
                        (Some(fills[slot].clone()), value.clone(), Some(row))
                    }
                    Some(other) => (None, js_cell::description(other), Some(row)),
                    None => (None, "undefined".to_string(), Some(row)),
                },
                None => (None, "null".to_string(), None),
            };
            let label = if affixed {
                let value = value_title.as_deref().map(|t| (t, value));
                affixed_label(value, lookup_row(row)?)
            } else {
                native_label((&title, value), lookup_row(row)?)
            };
            overlay.push(Overlay { feature: i, fill, label });
        }
        Coloring::Categories(CategoryScale { domain, fills, null_is_js: false })
    };
    Ok(Choropleth { coloring, overlay, title: legend_title_text(value_field, &r.legend, &r.field_labels)? })
}

/// Decode the chart's geometry, join its rows and assign colors.
pub fn bind(payload: &Payload) -> Result<Bound, Diagnostic> {
    let resolved: Resolved =
        Deserialize::deserialize(&payload.resolved).map_err(|e| format!("geoshape payload: {e}"))?;
    let geometries = payload.geometries.as_ref().ok_or_else(|| {
        format!("geoshape: payload.geometries is absent; geo_url {:?} needs its document", resolved.geo_url)
    })?;
    let source = GeoSource {
        role: "geoshape",
        url: &resolved.geo_url,
        format_type: &resolved.geo_format_type,
        feature: resolved.geo_feature.as_deref(),
        property: resolved.geo_property.as_deref(),
        name: &resolved.geo_source_name,
    };
    if source.format_type == "json" && source.property.is_none() {
        // Vega reads the whole document as one datum and draws nothing; production turns an empty chart that
        // shows a value into an error and otherwise ships the blank chart.
        if resolved.value_field.as_deref().is_none_or(|f| payload.rows.iter().any(|row| js_cell::has_ink(row.get(f)))) {
            return Err(payload.painted_no_marks());
        }
        return Err(crate::refuse!("a json geo source with no geo_property and no value ink draws a blank chart that is not supported").into());
    }
    let decoded = decode(&source, geometries)?;
    resolved.mark()?;
    let rows: Vec<Map<String, Value>> = payload.rows.iter().map(js_cell::normalized).collect();
    let rows = &rows[..];
    let has_color_channel = resolved.resolved_channels.contains_key("color");
    let choropleth = match (&resolved.lookup_field, &resolved.value_field) {
        (Some(lookup), value) if !rows.is_empty() && (value.is_some() || has_color_channel) => {
            let value = value.as_deref().ok_or("geoshape: value_field absent (resolver required)")?;
            let keys = join_keys(&source, &decoded, &resolved.geo_join_key)?;
            Some(bind_choropleth(&resolved, &decoded.features, &keys, rows, (lookup, value))?)
        }
        _ => None,
    };
    // Production builds the link for either map, but only a base map's layer carries it: a
    // choropleth's lookup layers drop it. The template is still checked, so a channel the chart
    // lacks raises as it does there.
    let color_field = resolved.resolved_channels.get("color").and_then(|c| c.get("data_field")).and_then(Value::as_str);
    let href = match &resolved.link {
        Some(template) => {
            let href = crate::href::resolve_channel_href(template, |channel| match (channel, color_field) {
                ("color", Some(_)) if choropleth.is_some() => Ok(String::new()),
                _ => Err(format!("link template references channel '{{{{ {channel} }}}}' but a geoshape has no '{channel}' encoding")),
            })?;
            if choropleth.is_some() {
                None
            } else {
                let href = match &payload.link_context {
                    Some(ctx) => crate::href::resolve_href(&href, ctx),
                    None => href,
                };
                crate::href::checked_href(&href)?;
                Some(href)
            }
        }
        None => None,
    };
    Ok(Bound { resolved, decoded, choropleth, href })
}

/// The legend a layout draws.
enum LegendPlan<'a> {
    Symbols { entries: Vec<crate::families::legend::LegendEntry<'a>>, legend: crate::families::legend::VegaLegend },
    Gradient { scale: &'a ContinuousColor, ends: Option<&'a [String]> },
}

/// Fit the bound map to the frame: the plot rect, the paths, the legend.
pub fn layout(bound: &Bound, frame: &ChartFrame, title: &FrameTitle, fonts: &FontBook) -> Result<Layout, Diagnostic> {
    let r = &bound.resolved;
    let mark = r.mark()?;
    let reach = r.stroke_reach()?;
    let fit = r.is_fit();
    // The overlay's features are the background's, so fitting the background fits both.
    let fitter = projection::Fitter::new(
        &r.geo_projection_type,
        r.geo_projection_params.as_ref(),
        vec![GeoJson::FeatureCollection(FeatureCollection { features: bound.decoded.features.clone() })],
    );

    let grad_id = format!("geo_grad-{}", contract::safe_svg_id(&r.id));
    let legend_visible = r.legend.visible;
    // An authored `symbol_shape` takes the mark's stroke; a mark with none draws the shape fill-only.
    let default_symbol = match (r.legend.symbol_shape.as_deref(), mark.stroke.as_ref().and_then(|s| s.color.as_deref())) {
        (None, None) => LegendSymbol::FilledCircle,
        (_, Some(stroke)) => LegendSymbol::BorderedCircle(stroke),
        (Some(_), None) => LegendSymbol::FilledShape,
    };
    let symbol_entries;
    let folded_label;
    let plan = match bound.choropleth.as_ref().filter(|_| legend_visible) {
        Some(Choropleth { coloring: Coloring::Categories(scale), title, .. }) => {
            symbol_entries = category_legend_entries(scale, r.legend.values.as_deref());
            let mut entries: Vec<_> = symbol_entries
                .iter()
                .map(|e| crate::families::legend::LegendEntry { symbol: Some(default_symbol), ..*e })
                .collect();
            if let Some((kept, label)) =
                fold_symbol_limit(entries.len(), r.legend.symbol_limit.unwrap_or(VEGA_LEGEND_SYMBOL_LIMIT) as usize)
            {
                folded_label = label;
                let folded = crate::families::legend::LegendEntry { label: &folded_label, ..entries[kept] };
                entries.truncate(kept);
                entries.push(folded);
            }
            let legend = vega_legend(&entries, false, title, &r.legend, fonts)?;
            Some(LegendPlan::Symbols { entries, legend })
        }
        Some(Choropleth { coloring: Coloring::Gradient { scale, legend_ends }, .. }) => {
            Some(LegendPlan::Gradient { scale, ends: legend_ends.as_deref() })
        }
        None => None,
    };
    let legend_title = bound.choropleth.as_ref().map_or("", |c| c.title.as_str());

    // The legend's box at plot size `(pw, ph)`.
    let legend_size = |pw: f64, ph: f64| -> Result<Option<(f64, f64)>, Diagnostic> {
        Ok(match &plan {
            None => None,
            Some(LegendPlan::Symbols { legend, .. }) => Some((legend.box_w, legend.box_h())),
            Some(LegendPlan::Gradient { scale, ends }) => {
                let at = GradientAt::with_ends(&r.legend, None, scale, (pw, ph), *ends)?;
                let legend = at.legend(&r.legend, scale, &grad_id, legend_title, fonts)?;
                Some((legend.box_width(at.length), legend.height(at.length)))
            }
        })
    };

    let mut legend_xy = (0.0, 0.0);
    let mut failure: Option<Diagnostic> = None;
    let mut ink = |pw: f64, ph: f64, b: &mut Bounds| -> Result<(), Diagnostic> {
        if fit {
            let [[x0, y0], [x1, y1]] = fitter.layer_bounds((pw, ph))?;
            b.union(&Bounds::new(x0, y0, x1, y1).expand(reach));
        }
        if let Some(size) = legend_size(pw, ph)? {
            legend_xy = legend_origin(&r.legend.position, (0.0, pw), (0.0, ph), (pw, ph), size);
            if let Some(extent) = legend_fit_bounds(&r.legend.position, legend_xy, size) {
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
    let path = fitter.at((plot_w, plot_h))?.into_path();
    let paths: Vec<Option<String>> =
        bound.decoded.features.iter().map(|f| path.path(&GeoJson::Feature(f.clone()))).collect::<Result<_, _>>()?;

    let dim = frame.dim;
    let clip_ids = ["geo-clip-0", "geo-clip-1"].map(|id| format!("{id}-{}", contract::safe_svg_id(&r.id)));
    let link_attrs = bound.href.as_deref().map(|url| {
        let attrs = Attrs::new().set("href", url);
        let attrs = r.link_open.open_attr().map_or(attrs.clone(), |open| attrs.set("data-dbt-link-open", open));
        r.link_open.target().map_or(attrs.clone(), |target| attrs.set("target", target))
    });
    // A feature the projection draws nothing of (an `albersUsa` territory outside its insets) is
    // still a mark, with no `d`.
    let item_role = display_text::language::tr("aria.role_geoshape", None, &[])?;
    let shape = |d: Option<&str>, label: Option<&str>, fill: Option<&str>| {
        let mut attrs = Attrs::new();
        if let Some(label) = label {
            attrs = attrs
                .set("aria-label", label)
                .set("role", "graphics-symbol")
                .set("aria-roledescription", item_role.as_str());
        }
        attrs = attrs.set("transform", "translate(0,0)");
        if let Some(d) = d {
            attrs = attrs.set("d", d);
        }
        if let Some(fill) = fill {
            attrs = attrs.set("fill", fill);
        }
        if let Some(stroke) = &mark.stroke {
            if let Some(color) = &stroke.color {
                attrs = attrs.set("stroke", color.as_str());
                if let Some(width) = stroke.width {
                    attrs = attrs.set("stroke-width", width.to_string());
                }
            }
        }
        let path = Node::Path(attrs);
        match &link_attrs {
            Some(attrs) => Node::Link { attrs: attrs.clone(), children: vec![path] },
            None => path,
        }
    };
    let layered = bound.choropleth.is_some();
    let group = |name: &str, clip: Option<&str>, described: bool, children: Vec<Node>| -> Result<Node, Diagnostic> {
        let mut attrs = Attrs::new().set("class", format!("mark-shape role-mark {name}"));
        if let Some(id) = clip.filter(|_| !fit) {
            attrs = attrs.set("clip-path", format!("url(#{id})"));
        }
        let role = if described { "graphics-object" } else { "graphics-symbol" };
        let container = display_text::language::tr("aria.container_shape", None, &[])?;
        Ok(Node::group(attrs.set("role", role).set("aria-roledescription", container), children))
    };
    let background = bound
        .decoded
        .features
        .iter()
        .zip(&paths)
        .map(|(_, d)| shape(d.as_deref(), None, mark.fill.as_deref()))
        .collect();
    let mut layers = vec![group(if layered { "layer_0_marks" } else { "marks" }, Some(&clip_ids[0]), false, background)?];
    if let Some(choropleth) = &bound.choropleth {
        let overlay = choropleth
            .overlay
            .iter()
            .map(|o| shape(paths[o.feature].as_deref(), Some(&o.label), o.fill.as_deref()))
            .collect();
        layers.push(group("layer_1_marks", Some(&clip_ids[1]), true, overlay)?);
    }
    let mut nodes = vec![Node::group(
        Attrs::new().set("transform", format!("translate({plot_left:.2},{plot_top:.2})")),
        layers,
    )];

    let (leg_x, leg_y) = (plot_left + legend_xy.0, plot_top + legend_xy.1);
    let slide = |size: (f64, f64)| slide_block(&r.id, &r.legend.position, size, (plot_w, plot_h));
    match &plan {
        Some(LegendPlan::Symbols { entries, legend }) => {
            let (dx, dy) = legend_slide(&r.id, &r.legend.position, legend, (plot_w, plot_h))?;
            nodes.push(symbol_legend(entries, legend, &r.legend, default_symbol, leg_x + dx, leg_y + dy, false));
        }
        Some(LegendPlan::Gradient { scale, ends }) => {
            let at = GradientAt::with_ends(&r.legend, None, scale, (plot_w, plot_h), *ends)?;
            let legend = at.legend(&r.legend, scale, &grad_id, legend_title, fonts)?;
            let (dx, dy) = slide((legend.box_width(at.length), legend.height(at.length)))?;
            nodes.extend(legend.nodes(leg_x + dx, leg_y + dy, at.length));
        }
        None => {}
    }
    if !fit {
        let clip = |id: &String| Node::ClipPath {
            attrs: Attrs::new().set("id", id.as_str()),
            children: vec![Node::Rect(
                Attrs::new().set("x", "0").set("y", "0").set("width", dim(plot_w)).set("height", dim(plot_h)),
            )],
        };
        let used = if layered { &clip_ids[..] } else { &clip_ids[..1] };
        nodes.push(Node::Defs(used.iter().map(clip).collect()));
    }

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

    #[test]
    fn an_integer_key_is_a_whole_number_in_any_spelling() {
        for yes in [json!(6), json!(6.0), json!("6"), json!(" 06 "), json!("6.0"), json!("1e3")] {
            assert!(is_integer_key(&yes), "{yes}");
        }
        for no in [json!(6.5), json!("CA"), json!(""), json!(" "), json!("nan"), json!("inf"), json!(true), json!(null)] {
            assert!(!is_integer_key(&no), "{no}");
        }
    }

    #[test]
    fn a_join_key_is_the_text_javascript_indexes_by() {
        let key = |v: Option<Value>| crate::href::js_string(v.as_ref());
        assert_eq!(key(Some(json!(9))).unwrap(), "9");
        assert_eq!(key(Some(json!(9.0))).unwrap(), "9");
        assert_eq!(key(Some(json!("09"))).unwrap(), "09");
        assert_eq!(key(Some(json!(null))).unwrap(), "null");
        assert_eq!(key(None).unwrap(), "undefined");
        assert!(key(Some(json!([1]))).unwrap_err().contains("not a scalar"));
    }

    #[test]
    fn a_quantitative_value_is_valid_when_plus_x_is_finite() {
        let number = |v: Value| finite_number(Some(&v));
        assert_eq!(number(json!(2.5)), Some(2.5));
        assert_eq!(number(json!("12")), Some(12.0));
        assert_eq!(number(json!("")), Some(0.0));
        assert_eq!(number(json!(true)), Some(1.0));
        assert_eq!(number(json!("abc")), None);
        assert_eq!(number(json!(null)), None);
        assert_eq!(finite_number(None), None);
        assert_eq!(finite_number(Some(&json!([5]))), Some(5.0));
        assert_eq!(finite_number(Some(&json!([1, 2]))), None);
    }

    #[test]
    fn python_prints_a_cell_the_way_str_does() {
        assert_eq!(python_str(&json!(6)), "6");
        assert_eq!(python_str(&json!(6.0)), "6.0");
        assert_eq!(python_str(&json!(true)), "True");
        assert_eq!(python_str(&json!("CA")), "CA");
    }

    #[test]
    fn a_description_lists_the_color_then_the_lookup_column() {
        assert_eq!(native_label(("Rate", "4.1".into()), Some(("State", "9".into()))), "Rate: 4.1; State: 9");
        assert_eq!(native_label(("Rate", "4.1".into()), None), "Rate: 4.1");
        assert_eq!(affixed_label(Some(("Rate", "4.1 %".into())), Some(("State", "9".into()))), "State: 9; Rate: 4.1 %");
        assert_eq!(affixed_label(None, Some(("State", "9".into()))), "State: 9");
    }
}
