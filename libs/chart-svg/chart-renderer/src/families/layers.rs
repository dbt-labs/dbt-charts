//! Shared `chart.layers` machinery — the overlay-layer types, row
//! resolution, mixed-legend law, and line-layer paint routine bar.rs and
//! line.rs both call. See `docs/laws-layers.md` for the probe evidence.
//!
//! What lives here is what genuinely has two callers today: the `line`
//! layer's own JSON shape, the cross-query row lookup
//! (`_overlay.py::_resolve_layer_rows`'s one divergence signal — the query
//! name, not whether one is authored), the mixed-mark legend's two-entry
//! law (`_mixed_mark_legend_symbols`), and the shared-scale paint routine
//! (`paint_layer_marks`/`emit_line_layer`, parameterized by the base's own
//! x-position law — band centers for bar, the fitted x scale for line).
//! Bar-only shapes (`type: bar` layers, grouped-base) also have their own
//! types/helpers here (`OverlayBarLayer`) — only `bar.rs` calls them, but they live alongside
//! the two-caller machinery rather than in `bar.rs` itself. A layer pinned to
//! a scale of its own (`ScaledLayer`, `LayerAxis`) is also shared: both
//! families measure, fit and paint its independent axis through this module.

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

use regex::Regex;
use serde::Deserialize;
use serde_json::Value;

use crate::diagnostic::Diagnostic;
use crate::common::{row_num, row_str, FormatSlot, MarkLabels};
use crate::families::axis_caption::{slot_format, AxisCaption, CaptionScale, NumericScale};
use crate::families::color_scale::ContinuousColor;
use crate::families::legend::{LegendConfig, LegendEntry, LegendSymbol};
use crate::svg::xml_escape_attr;
use crate::text::FontBook;
use crate::Payload;

#[derive(Deserialize)]
pub(crate) struct OverlayStroke {
    pub width: f64,
    #[serde(default)]
    pub color: Option<String>,
    #[serde(default)]
    pub cap: Option<String>,
    #[serde(default)]
    pub join: Option<String>,
    #[serde(default)]
    pub dasharray: Option<String>,
}

#[derive(Deserialize)]
pub(crate) struct OverlayLineMark {
    pub stroke: OverlayStroke,
    #[serde(default)]
    pub curve: Option<String>,
    #[serde(default)]
    pub connect: Option<bool>,
    /// Baked stroke-linecap for a disconnected band-step's separate
    /// plateaus (`connect: false`) — replaces `stroke.cap` there. Sibling
    /// of `connect`, not `stroke`, mirroring `line.rs`'s own `LineMark`
    /// (`emitters/_layers.py`'s `line_mark.disconnected_cap`).
    #[serde(default)]
    pub disconnected_cap: Option<String>,
    /// Share of its band a disconnected band-step plateau spans. Absent on an
    /// `area` layer's edge line (`ResolvedAreaLineStyle`), which has no step band.
    #[serde(default)]
    pub disconnected_width: Option<f64>,
    pub labels: MarkLabels,
}

/// The plateau share of a disconnected band step, which refuses when the
/// payload carries none. Shared by a line base's and a line layer's `line_mark`.
pub(crate) fn plateau_share(disconnected_width: Option<f64>, family: &str) -> Result<f64, String> {
    disconnected_width.ok_or_else(|| {
        format!("{family}: line_mark.disconnected_width must be resolved for a disconnected band step")
    })
}

/// An `area` layer's translucent fill under its line (`area_mark`).
#[derive(Deserialize)]
pub(crate) struct OverlayAreaMark {
    pub opacity: f64,
    #[serde(default)]
    pub curve: Option<String>,
}

#[derive(Deserialize)]
pub(crate) struct OverlayPointMark {
    #[serde(default)]
    pub size: Option<f64>,
    #[serde(default)]
    pub color: Option<String>,
    #[serde(default)]
    pub filled: Option<bool>,
    #[serde(default)]
    pub fill: Option<String>,
    pub stroke_width: f64,
    pub labels: MarkLabels,
}

/// A layer's `labels.format` is painted on its own axis, and a layer on the base's scale paints
/// the same tick labels (`_overlay.py:1583`); only the tooltip's affix has no painter and refuses.
pub(crate) fn refuse_unpainted_layer_affix(family: &str, raw: &Value) -> Result<(), String> {
    #[derive(Deserialize)]
    struct Axis {
        #[serde(default)]
        tooltip_format: Option<FormatSlot>,
    }
    #[derive(Deserialize)]
    struct Layer {
        #[serde(default)]
        axis_y: Option<Axis>,
    }
    let layer: Layer = Deserialize::deserialize(raw).map_err(|e| format!("{family}: layer payload: {e}"))?;
    let Some(axis) = layer.axis_y else { return Ok(()) };
    if let Some(key) = axis.tooltip_format.as_ref().and_then(|slot| slot.carried().next()) {
        return Err(format!("{family}: layer axis_y.tooltip_format {key:?} is not painted: a layer tooltip's affix is unprobed against production"));
    }
    Ok(())
}

/// The opacity of every area layer's legend circle: the last area layer's
/// (`_overlay.py`'s one `area_opacity`).
pub(crate) fn shared_area_opacity<'a>(layers: impl DoubleEndedIterator<Item = &'a OverlayLayer>) -> Option<f64> {
    layers.rev().find_map(|l| match l {
        OverlayLayer::Line(l) => l.area_mark.as_ref().map(|a| a.opacity),
        OverlayLayer::Bar(_) | OverlayLayer::Scatter(_) => None,
    })
}

/// The side a pinned layer's own y axis draws on (`axis_y.position`).
#[derive(Deserialize, Clone, Copy, PartialEq, Eq, Debug)]
#[serde(rename_all = "lowercase")]
pub(crate) enum LayerSide {
    Left,
    Right,
}

#[derive(Deserialize)]
pub(crate) struct OverlayAxisY {
    #[serde(default)]
    pub position: Option<LayerSide>,
    #[serde(default)]
    pub title: Option<String>,
    /// The layer's authored label overrides; its own axis paints
    /// `labels.format` where the base's format would otherwise apply.
    #[serde(default)]
    pub labels: Option<OverlayAxisLabels>,
    /// The layer's authored tooltip format, ahead of its label format and the
    /// base's own (`_overlay.py`'s `layer_tooltip_format`).
    #[serde(default)]
    pub tooltip_format: Option<FormatSlot>,
    /// `ticks.count` the layer's own axis asks for, else the base's.
    #[serde(default)]
    pub ticks: Option<OverlayAxisTicks>,
    /// `grid.visible` the layer's own axis asks for, else the base's.
    #[serde(default)]
    pub grid: Option<OverlayAxisGrid>,
    /// The ladder resolve bakes for a house SI layer format.
    #[serde(default)]
    pub tick_label: Option<crate::common::TickLabelConfig>,
    /// The layer's authored `scale.domain`, and whether its marks cut off at the
    /// plot edge outside it.
    #[serde(default)]
    pub scale: Option<OverlayAxisScale>,
}

#[derive(Deserialize)]
pub(crate) struct OverlayAxisScale {
    #[serde(default)]
    pub domain: Option<(f64, f64)>,
    #[serde(default = "clip_by_default")]
    pub clip: bool,
}

fn clip_by_default() -> bool {
    true
}

#[derive(Deserialize)]
pub(crate) struct OverlayAxisTicks {
    #[serde(default)]
    pub count: Option<f64>,
}

#[derive(Deserialize)]
pub(crate) struct OverlayAxisGrid {
    #[serde(default)]
    pub visible: Option<bool>,
}

impl OverlayAxisY {
    /// The format a layer's datum is described in: its own tooltip format,
    /// else its label format, else the base's (`_overlay.py`'s
    /// `layer_tooltip_format`).
    pub(crate) fn tooltip_format_or<'a>(&'a self, base: &'a FormatSlot) -> &'a FormatSlot {
        self.tooltip_format
            .as_ref()
            .or_else(|| self.labels.as_ref().and_then(|labels| labels.format.as_ref()))
            .unwrap_or(base)
    }
}

/// A layer's series row: its name, prefixed onto the color value when the
/// layer splits by color.
pub(crate) fn layer_series_text(name: &str, color_value: Option<&str>) -> String {
    match color_value {
        Some(value) => format!("{name}: {value}"),
        None => name.to_string(),
    }
}

/// The series row a block whose bar has none borrows from the first layer whose
/// color splits its rows over the shared scale (`_layer_series`), keyed by that
/// layer's category: the layer's paint for the row's value, SWATCH-bracketed,
/// since the hovered mark may be the uncolored bar. A numeric color has no paint
/// on the shared scale, so it lends nothing.
pub(crate) fn layer_series_swatches<'a>(
    layers: impl IntoIterator<Item = (&'a OverlayLayer, &'a [serde_json::Map<String, Value>], &'a LayerClaim)>,
    scale: &LayerScale,
    cat_field: &str,
) -> HashMap<String, String> {
    let split = layers.into_iter().find_map(|(layer, rows, claim)| {
        layer.color().filter(|cf| claim.numeric.is_none() && layer_color_promoted(rows, cf)).map(|cf| (layer, cf, rows))
    });
    let Some((layer, color_field, rows)) = split else { return HashMap::new() };
    rows.iter()
        .map(|row| {
            let value = row_str(row, color_field);
            let swatch = format!("{}{}{}{value}", crate::contract::SWATCH, scale.color_of(&value), crate::contract::SWATCH);
            (row_str(row, layer.x().unwrap_or(cat_field)), swatch)
        })
        .collect()
}

#[derive(Deserialize)]
pub(crate) struct OverlayAxisLabels {
    #[serde(default)]
    pub format: Option<FormatSlot>,
}

/// A `type: "line"` layer's raw JSON (`{Bar,Line}Resolved.layers[i]`),
/// parsed only after the caller's own validation has confirmed the shape is
/// one it models.
#[derive(Deserialize)]
pub(crate) struct OverlayLineLayer {
    /// Present on an `area` layer, which paints this fill, then the line.
    #[serde(default)]
    pub area_mark: Option<OverlayAreaMark>,
    pub line_mark: OverlayLineMark,
    pub point_mark: OverlayPointMark,
    pub axis_y: OverlayAxisY,
    #[serde(default)]
    pub x: Option<String>,
    pub y: String,
    #[serde(default)]
    pub label: Option<String>,
    #[serde(default)]
    pub color: Option<String>,
    #[serde(default)]
    pub query_name: Option<String>,
    #[serde(default)]
    pub label_is_house: bool,
}

impl OverlayLineLayer {
    pub(crate) fn layer_type(&self) -> LayerType {
        if self.area_mark.is_some() { LayerType::Area } else { LayerType::Line }
    }

    /// Whether any of the layer's rows paints outside `domain`
    /// (`_overlay.py::paints_outside_domain`): the gate for clipping a layer
    /// over a normalized base.
    pub(crate) fn leaves_domain(&self, rows: &[serde_json::Map<String, Value>], (lo, hi): (f64, f64)) -> bool {
        rows.iter().filter_map(|row| row_num(row, &self.y)).any(|v| !(lo.min(hi)..=lo.max(hi)).contains(&v))
    }

    /// The layer's authored curve: an area layer's fill and its edge line share
    /// `area_mark.curve`, a line layer draws `line_mark.curve`.
    pub(crate) fn curve_name(&self) -> Option<&str> {
        match &self.area_mark {
            Some(area) => area.curve.as_deref(),
            None => self.line_mark.curve.as_deref(),
        }
    }

    /// How far the layer's ink reaches from a datum: its stroke's full width
    /// (`boundStroke`), or its drawn point's radius plus its ring where larger.
    pub(crate) fn mark_reach(&self) -> f64 {
        let point = self.point_mark.size.filter(|s| *s > 0.0).map_or(0.0, |size| {
            crate::common::symbol_radius(size)
                + crate::common::symbol_stroke_ink(self.point_mark.filled, self.point_mark.stroke_width)
        });
        self.line_mark.stroke.width.max(point)
    }

    /// A band-step line layer's plateau share and stroke reach, where its
    /// plateaus add ink to the fit (`boundStroke`); `None` for anything else.
    /// An area layer's edge line adds none: production's bounds stop at its fill.
    pub(crate) fn band_step_ink(&self) -> Result<Option<(f64, f64)>, String> {
        if self.curve_name() != Some("step") || self.area_mark.is_some() {
            return Ok(None);
        }
        Ok(Some((self.plateau_share()?, self.mark_reach())))
    }

    /// `connect: false` on a line layer: each band-step row is its own plateau
    /// (an area layer's step is always connected).
    pub(crate) fn is_disconnected(&self) -> bool {
        self.area_mark.is_none() && self.line_mark.connect == Some(false)
    }

    /// The share of its band a band-step plateau spans: the whole band, or the
    /// resolved `disconnected_width` of a disconnected one.
    pub(crate) fn plateau_share(&self) -> Result<f64, String> {
        if self.is_disconnected() {
            plateau_share(self.line_mark.disconnected_width, "layer")
        } else {
            Ok(1.0)
        }
    }

    /// Parses a `line` or `area` layer, whose `area_mark` must agree with its type.
    pub(crate) fn parse(raw: &Value, family: &str) -> Result<Self, String> {
        let layer: OverlayLineLayer =
            Deserialize::deserialize(raw).map_err(|e| format!("{family}: layer payload: {e}"))?;
        let is_area = raw.get("type").and_then(Value::as_str) == Some("area");
        if is_area != layer.area_mark.is_some() {
            return Err(format!("{family}: a layer's type and its area_mark disagree: an area layer carries the one, a line layer neither"));
        }
        Ok(layer)
    }

    /// The legend glyph of the layer's entries: an area draws its fill's
    /// circle, at the opacity every area layer's circle shares
    /// (`shared_area_opacity`), a line its stroke.
    pub(crate) fn legend_glyph(&self, area_opacity: Option<f64>) -> LegendSymbol<'static> {
        match &self.area_mark {
            Some(_) => LegendSymbol::FadedCircle(area_opacity.expect("an area layer is among those sharing the opacity")),
            None => LegendSymbol::Stroke(2.0),
        }
    }

    /// The color a layer authors for its series: a line's stroke. An area's
    /// fill, like a bar's, always comes from the palette (`_layer_series_color`).
    pub(crate) fn authored_series_color(&self) -> Option<&str> {
        if self.area_mark.is_some() {
            return None;
        }
        self.line_mark.stroke.color.as_deref()
    }

    /// The slot the layer's value labels read: a line layer's `marks.point.labels`
    /// aliases `marks.line.labels`, the line slot winning when both are visible
    /// (`_overlay.py`'s `point_labels`); an area layer reads the line slot alone
    /// (`_layer_label_slots`).
    pub(crate) fn visible_labels(&self) -> Option<&MarkLabels> {
        let point = self.area_mark.is_none().then_some(&self.point_mark.labels);
        [Some(&self.line_mark.labels), point].into_iter().flatten().find(|ml| ml.visible)
    }
}

/// d3's curve for an authored `curve` literal (VL `interpolate`). A literal
/// `step` is d3's centered `curveStep` here: the band-aware plateau is a row
/// transform the caller applies on a band x, never a curve.
/// The link a layer carries: the chart's own, unless the layer reads another query's rows.
pub(crate) fn layer_link<'a>(
    link: Option<&'a crate::href::DatumLink<'a>>,
    (query, base_query): (Option<&str>, Option<&str>),
) -> Option<&'a crate::href::DatumLink<'a>> {
    link.filter(|_| query.is_none_or(|query| Some(query) == base_query))
}

pub(crate) fn curve_named(name: Option<&str>, family: &str) -> Result<d3rs::shape::Curve, String> {
    use d3rs::shape::Curve;
    Ok(match name {
        None | Some("linear") => Curve::Linear,
        Some("monotone") => Curve::MonotoneX,
        Some("step") => Curve::Step,
        Some("step-before") => Curve::StepBefore,
        Some("step-after") => Curve::StepAfter,
        Some("basis") => Curve::Basis,
        Some("natural") => Curve::Natural,
        Some("cardinal") => Curve::Cardinal,
        Some(other) => return Err(format!("{family}: curve={other} is none of the Curve literals")),
    })
}

/// The color fields of the layers drawing on the shared color scale: a layer colored by a
/// numeric field has a scale, and so a legend, of its own.
pub(crate) fn shared_color_fields<'a>(
    layers: impl IntoIterator<Item = (&'a OverlayLayer, &'a [serde_json::Map<String, Value>])>,
) -> Vec<&'a str> {
    layers
        .into_iter()
        .filter_map(|(layer, rows)| layer.color().filter(|field| numeric_layer_color(rows, field).is_none()))
        .collect()
}

/// The title of a layered chart's one merged color legend. Vega-Lite joins the
/// titles of the encodings the legend merges, an explicit one (the base's own
/// color field) outranking the implicit field names of the layers': a base with
/// no color field of its own takes its layers' fields as authored, once each, in
/// layer order, `legend.title.visible: false` dropping it as ever.
pub(crate) fn merged_legend_title<'a>(
    base_title: String,
    base_has_color: bool,
    layer_color_fields: impl IntoIterator<Item = &'a str>,
    cfg: &LegendConfig,
) -> String {
    if base_has_color || cfg.title.visible == Some(false) {
        return base_title;
    }
    let mut fields: Vec<&str> = Vec::new();
    for field in layer_color_fields {
        if !fields.contains(&field) {
            fields.push(field);
        }
    }
    if fields.is_empty() {
        return base_title;
    }
    fields.join(", ")
}

/// The title of an axis a layered chart's marks share. Vega-Lite joins the titles
/// of the encodings the axis holds, each distinct one once, in layer order.
pub(crate) fn merged_axis_title(titles: impl IntoIterator<Item = String>) -> String {
    let mut distinct: Vec<String> = Vec::new();
    for title in titles {
        if !distinct.contains(&title) {
            distinct.push(title);
        }
    }
    distinct.join(", ")
}

/// A layer's color domain: the distinct non-null values of `field`, sorted
/// (`distinct_series_values`, `_cartesian.py`). A null value is no domain entry:
/// a row carrying one still draws, as a series the scale has no color for.
pub(crate) fn layer_color_values(rows: &[serde_json::Map<String, Value>], field: &str) -> Vec<String> {
    crate::common::color_domain_of(rows.iter().filter(|row| row.get(field).is_some_and(|v| !v.is_null())), field)
}

/// The "color" of a series with no entry on the shared scale: Vega paints it
/// with no stroke or fill.
pub(crate) const UNSCALED_COLOR: &str = "none";

/// A color-grouped layer's series from the baseline up. No `order` encoding
/// reaches a layer, so Vega-Lite's stack sorts its color field descending (the
/// first value ends on top); rows with no color value sort lowest and so end on
/// top of every series.
pub(crate) fn layer_stack_order(domain: &[String], any_null: bool) -> Vec<Option<String>> {
    domain.iter().rev().cloned().map(Some).chain(any_null.then_some(None)).collect()
}

/// A row's color value: `None` where it has none.
pub(crate) fn layer_color_of(row: &serde_json::Map<String, Value>, field: &str) -> Option<String> {
    row.get(field).filter(|v| !v.is_null()).map(|_| row_str(row, field))
}

/// What an aria label reads for a color value a row has none of.
pub(crate) const NULL_COLOR_TEXT: &str = "null";

/// The value an aria label reads for a row's color.
pub(crate) fn layer_color_text(row: &serde_json::Map<String, Value>, field: &str) -> String {
    layer_color_of(row, field).unwrap_or_else(|| NULL_COLOR_TEXT.to_string())
}

/// Whether a layer's color distinguishes its rows, which promotes the color value into
/// each datum's description (`series_row_promoted`): more than one non-null value.
pub(crate) fn layer_color_promoted(rows: &[serde_json::Map<String, Value>], field: &str) -> bool {
    layer_color_values(rows, field).len() > 1
}

/// The series of a color-grouped line or area layer as Vega's facet meets them:
/// in order of first appearance in `rows`, each with its own rows, a row with no
/// color value being a series of its own (`None`).
pub(crate) fn layer_color_groups(
    rows: &[serde_json::Map<String, Value>],
    field: &str,
) -> Vec<(Option<String>, Vec<serde_json::Map<String, Value>>)> {
    let mut series: Vec<(Option<String>, Vec<serde_json::Map<String, Value>>)> = Vec::new();
    for row in rows {
        let value = layer_color_of(row, field);
        match series.iter_mut().find(|(seen, _)| *seen == value) {
            Some((_, own)) => own.push(row.clone()),
            None => series.push((value, vec![row.clone()])),
        }
    }
    series
}

/// Which rows a layer draws in one cell. Vega-Lite facets only the base's data:
/// a layer on the base query draws its panel's slice, except a band-step layer,
/// whose doubled rows are inline data and draw whole in every cell; a layer
/// reading another query draws that dataset (doubled, if band-step) whole.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum LayerRows {
    Panel,
    Chart,
    Dataset,
}

pub(crate) fn layer_rows_scope(
    is_facet: bool,
    band_step: bool,
    layer_query_name: Option<&str>,
    base_query_name: Option<&str>,
) -> LayerRows {
    match (is_facet && layer_query_name == base_query_name, band_step) {
        (true, false) => LayerRows::Panel,
        (true, true) => LayerRows::Chart,
        (false, _) => LayerRows::Dataset,
    }
}

/// `resolve_layer_rows` for one panel of a facet, by `layer_rows_scope`;
/// `payload` stays the whole chart for every other law.
pub(crate) fn panel_layer_rows<'a>(
    panel_rows: &'a [serde_json::Map<String, Value>],
    is_facet: bool,
    band_step: bool,
    payload: &'a Payload,
    layer_query_name: Option<&str>,
    base_query_name: Option<&str>,
    family: &str,
) -> Result<&'a [serde_json::Map<String, Value>], String> {
    match layer_rows_scope(is_facet, band_step, layer_query_name, base_query_name) {
        LayerRows::Panel => Ok(panel_rows),
        LayerRows::Chart => Ok(&payload.rows),
        LayerRows::Dataset => resolve_layer_rows(payload, layer_query_name, base_query_name, family),
    }
}

/// A layer's own rows: `payload.datasets[query_name]` whenever the layer's
/// `query_name` differs from the base's — `_overlay.py::_resolve_layer_
/// rows`'s one divergence signal (the query name, not whether one is
/// authored: it defaults to the base's own for nearly every layer) —
/// otherwise the base's own already-normalized `payload.rows`. `family`
/// names the caller in the error message (`"bar"`/`"line"`), matching each
/// family's own error-message convention.
pub(crate) fn resolve_layer_rows<'a>(
    payload: &'a Payload,
    layer_query_name: Option<&str>,
    base_query_name: Option<&str>,
    family: &str,
) -> Result<&'a [serde_json::Map<String, Value>], String> {
    if layer_query_name == base_query_name {
        return Ok(&payload.rows);
    }
    let qn = layer_query_name
        .ok_or_else(|| format!("{family}: a layer with no query name beside a base that names one"))?;
    payload
        .datasets
        .as_ref()
        .and_then(|d| d.get(qn))
        .map(Vec::as_slice)
        .ok_or_else(|| format!("{family}: a layer reading query {qn:?} has no dataset available"))
}

/// Vega-Lite's stack of a color-grouped layer's marks (an area's fills, a bar-type
/// layer's segments): at each x the series present stack from zero in `baseline_first`
/// order, rows sharing a series stacking in row order. An area's line and points stay
/// on the raw value.
pub(crate) struct LayerStack {
    x_field: String,
    color_field: String,
    y_field: String,
    cells: HashMap<(String, Option<String>), Vec<(f64, f64)>>,
    /// A numeric color's series each hold a zero-thickness cell at every x they
    /// have no row at (Vega-Lite's imputed zeros under a continuous color), in
    /// first-appearance x order.
    imputed_cells: Vec<((String, Option<String>), (f64, f64))>,
    /// Per color value, the `(x, href)` of the zero rows Vega-Lite's impute pass adds
    /// under a link, in the order it generates them.
    imputed: Vec<(Option<String>, Vec<(String, String)>)>,
}

impl LayerStack {
    /// The default stack of a layer with no `order` encoding (`layer_stack_order`).
    pub(crate) fn descending(
        rows: &[serde_json::Map<String, Value>],
        x_field: &str,
        color_field: &str,
        y_field: &str,
    ) -> Self {
        Self::ordered(rows, x_field, color_field, y_field, true)
    }

    /// The same stack read along a horizontal chart's measure axis: the first value of the
    /// domain sits at the baseline, a null-colored row below all.
    pub(crate) fn ascending(
        rows: &[serde_json::Map<String, Value>],
        x_field: &str,
        color_field: &str,
        y_field: &str,
    ) -> Self {
        Self::ordered(rows, x_field, color_field, y_field, false)
    }

    fn ordered(
        rows: &[serde_json::Map<String, Value>],
        x_field: &str,
        color_field: &str,
        y_field: &str,
        descending: bool,
    ) -> Self {
        let any_null = rows.iter().any(|row| layer_color_of(row, color_field).is_none());
        if numeric_layer_color(rows, color_field).is_some() {
            // A numeric color orders its series by value.
            let mut values: Vec<(f64, String)> = rows
                .iter()
                .filter_map(|row| Some((row_num(row, color_field)?, row_str(row, color_field))))
                .collect();
            values.sort_by(|a, b| if descending { b.0.total_cmp(&a.0) } else { a.0.total_cmp(&b.0) });
            values.dedup_by(|a, b| a.1 == b.1);
            let texts = values.into_iter().map(|(_, text)| Some(text));
            let baseline_first: Vec<Option<String>> = if descending {
                texts.chain(any_null.then_some(None)).collect()
            } else {
                any_null.then_some(None).into_iter().chain(texts).collect()
            };
            return Self::build(rows, x_field, color_field, y_field, &baseline_first, true);
        }
        let values = layer_color_values(rows, color_field);
        let baseline_first: Vec<Option<String>> = if descending {
            layer_stack_order(&values, any_null)
        } else {
            any_null.then_some(None).into_iter().chain(values.into_iter().map(Some)).collect()
        };
        Self::new(rows, x_field, color_field, y_field, &baseline_first)
    }

    /// Under a link the stack also imputes a zero row per (color, href) group at every x
    /// the group has no row at.
    pub(crate) fn imputing(
        mut self,
        rows: &[serde_json::Map<String, Value>],
        link: Option<&crate::href::DatumLink>,
    ) -> Result<Self, String> {
        if let Some(link) = link {
            self.imputed = imputed_rows(rows, &self.x_field, &self.color_field, link)?;
        }
        Ok(self)
    }

    pub(crate) fn new(
        rows: &[serde_json::Map<String, Value>],
        x_field: &str,
        color_field: &str,
        y_field: &str,
        baseline_first: &[Option<String>],
    ) -> Self {
        Self::build(rows, x_field, color_field, y_field, baseline_first, false)
    }

    fn build(
        rows: &[serde_json::Map<String, Value>],
        x_field: &str,
        color_field: &str,
        y_field: &str,
        baseline_first: &[Option<String>],
        impute: bool,
    ) -> Self {
        let mut columns: Vec<(String, crate::common::StackZero)> = Vec::new();
        let mut cells: HashMap<(String, Option<String>), Vec<(f64, f64)>> = HashMap::new();
        let mut imputed = Vec::new();
        if impute {
            for row in rows.iter().filter(|row| row_num(row, y_field).is_some()) {
                let x = row_str(row, x_field);
                if !columns.iter().any(|(seen, _)| *seen == x) {
                    columns.push((x, crate::common::StackZero::new(0.0)));
                }
            }
        }
        for series in baseline_first {
            for row in rows.iter().filter(|row| layer_color_of(row, color_field) == *series) {
                let Some(val) = row_num(row, y_field) else { continue };
                let x = row_str(row, x_field);
                let slot = match columns.iter().position(|(seen, _)| *seen == x) {
                    Some(slot) => slot,
                    None => {
                        columns.push((x.clone(), crate::common::StackZero::new(0.0)));
                        columns.len() - 1
                    }
                };
                cells.entry((x, series.clone())).or_default().push(columns[slot].1.push(val));
            }
            if impute {
                for (x, stack) in columns.iter_mut() {
                    if !cells.contains_key(&(x.clone(), series.clone())) {
                        imputed.push(((x.clone(), series.clone()), stack.push(0.0)));
                    }
                }
            }
        }
        LayerStack {
            x_field: x_field.to_string(),
            color_field: color_field.to_string(),
            y_field: y_field.to_string(),
            cells,
            imputed: Vec::new(),
            imputed_cells: imputed,
        }
    }

    /// The top of each x column's positive stack: where the imputed zero rows sit (no
    /// sort index puts them last, and a zero stacks on the positive side).
    pub(crate) fn column_top(&self, x: &str) -> f64 {
        self.cells
            .iter()
            .filter(|((cell_x, _), _)| cell_x == x)
            .flat_map(|(_, segments)| segments.iter().filter(|(start, end)| end >= start).map(|&(_, end)| end))
            .fold(0.0, f64::max)
    }

    /// The zero-thickness cells a series of `rows` holds where it has no row, as
    /// `(x text, (start, end))`.
    pub(crate) fn imputed_cells_for(&self, rows: &[serde_json::Map<String, Value>]) -> Vec<(&str, (f64, f64))> {
        let Some(first) = rows.first() else { return Vec::new() };
        let series = layer_color_of(first, &self.color_field);
        self.imputed_cells.iter().filter(|((_, s), _)| *s == series).map(|((x, _), span)| (x.as_str(), *span)).collect()
    }

    pub(crate) fn x_field(&self) -> &str {
        &self.x_field
    }

    pub(crate) fn color_field(&self) -> &str {
        &self.color_field
    }

    pub(crate) fn imputed_for(&self, color: &Option<String>) -> &[(String, String)] {
        self.imputed.iter().find(|(seen, _)| seen == color).map_or(&[], |(_, items)| items)
    }

    /// The `(start, end)` of each of `rows`' segments on its column's stack, in row
    /// order (`rows` being the rows it was built from, or a color value's share of
    /// them); a row with no value has no segment.
    pub(crate) fn of_rows(&self, rows: &[serde_json::Map<String, Value>]) -> Vec<Option<(f64, f64)>> {
        let mut taken: HashMap<(String, Option<String>), usize> = HashMap::new();
        rows.iter()
            .map(|row| {
                row_num(row, &self.y_field)?;
                let key = (row_str(row, &self.x_field), layer_color_of(row, &self.color_field));
                let nth = taken.entry(key.clone()).or_insert(0);
                let cell = self.cells.get(&key).and_then(|segments| segments.get(*nth)).copied();
                *nth += 1;
                cell
            })
            .collect()
    }
}

/// Vega-Lite's impute pass over a stacked area under a link: the stack's groups are the
/// link plus the color field unless that is the x field itself (then the imputed row takes the
/// x key as its color), and every group is filled with a zero at each x it has no row at.
pub(crate) struct ImputeGroups {
    groups: Vec<(Option<String>, String)>,
    keys: Vec<(String, Option<String>)>,
    present: HashSet<(usize, usize)>,
    color_is_x: bool,
}

impl ImputeGroups {
    /// `cells` are every row's `(x, color, href)` in row order, synthetic gap-fill rows included.
    pub(crate) fn new(cells: impl IntoIterator<Item = (String, Option<String>, String)>, color_is_x: bool) -> Self {
        let mut groups: Vec<(Option<String>, String)> = Vec::new();
        let mut keys: Vec<(String, Option<String>)> = Vec::new();
        let mut present: HashSet<(usize, usize)> = HashSet::new();
        for (x, color, href) in cells {
            let group = (if color_is_x { None } else { color.clone() }, href);
            let g = groups.iter().position(|seen| *seen == group).unwrap_or_else(|| {
                groups.push(group);
                groups.len() - 1
            });
            let k = keys.iter().position(|(seen, _)| *seen == x).unwrap_or_else(|| {
                keys.push((x, color));
                keys.len() - 1
            });
            present.insert((g, k));
        }
        Self { groups, keys, present, color_is_x }
    }

    /// A series' hrefs in the order its rows first show them.
    pub(crate) fn hrefs_of(&self, color: &str) -> Vec<&str> {
        self.groups.iter().filter(|(group_color, _)| group_color.as_deref() == Some(color)).map(|(_, href)| href.as_str()).collect()
    }

    /// The zero rows the pass adds, per color: `(x, href)` group after group.
    fn imputed(&self) -> Vec<(Option<String>, Vec<(String, String)>)> {
        let mut imputed: Vec<(Option<String>, Vec<(String, String)>)> = Vec::new();
        for (g, (group_color, href)) in self.groups.iter().enumerate() {
            for (k, (x, key_color)) in self.keys.iter().enumerate() {
                if self.present.contains(&(g, k)) {
                    continue;
                }
                let color = if self.color_is_x { key_color } else { group_color };
                match imputed.iter_mut().find(|(seen, _)| seen == color) {
                    Some((_, items)) => items.push((x.clone(), href.clone())),
                    None => imputed.push((color.clone(), vec![(x.clone(), href.clone())])),
                }
            }
        }
        imputed
    }
}

fn imputed_rows(
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    color_field: &str,
    link: &crate::href::DatumLink,
) -> Result<Vec<(Option<String>, Vec<(String, String)>)>, String> {
    let cells = rows
        .iter()
        .map(|row| Ok((row_str(row, x_field), layer_color_of(row, color_field), link.href(row)?)))
        .collect::<Result<Vec<_>, String>>()?;
    Ok(ImputeGroups::new(cells, x_field == color_field).imputed())
}

/// How far above its plot the scope groups of a color-grouped or band-step line or
/// area layer reach, `0` when they stay inside: Vega-Lite wraps each series' paths in
/// a scope group whose stroke-expanded bounds (`boundStroke`, the full width) join the
/// view's `yBounds`, so a top legend clears them. An area's fill, unstroked, reaches
/// only its stack top. `y_at` is the layer's own value-to-y scale.
pub(crate) fn scope_group_top(
    l: &OverlayLineLayer,
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    band_step: bool,
    y_at: impl Fn(f64) -> f64,
) -> f64 {
    let Some(color_field) = l.color.as_deref() else {
        if !band_step {
            return 0.0;
        }
        return rows
            .iter()
            .filter_map(|row| row_num(row, &l.y))
            .map(|v| y_at(v) - l.line_mark.stroke.width)
            .fold(0.0, f64::min);
    };
    let ends: Vec<Option<(f64, f64)>> = match &l.area_mark {
        Some(_) => LayerStack::descending(rows, x_field, color_field, &l.y).of_rows(rows),
        None => vec![None; rows.len()],
    };
    rows.iter()
        .zip(ends)
        .filter_map(|(row, span)| {
            let top = y_at(row_num(row, &l.y)?);
            let top = if layer_stroked(l, row, color_field) { top - l.line_mark.stroke.width } else { top };
            Some(span.map_or(top, |(_, end)| top.min(y_at(end))))
        })
        .fold(0.0, f64::min)
}

/// Whether a row's series paints a stroke (and so expands its bounds, `boundStroke`):
/// a series with no color value has none unless the layer authors one over them all.
pub(crate) fn layer_stroked(l: &OverlayLineLayer, row: &serde_json::Map<String, Value>, color_field: &str) -> bool {
    l.line_mark.stroke.color.is_some() || layer_color_of(row, color_field).is_some()
}

/// A layered chart's one shared `color:{datum}` scale (`_overlay.py`'s
/// `scale_domain`/`scale_range`): the base's entries, then each layer's claims
/// in authored order. An unauthored claim takes the first palette color the
/// board and the scale have not taken (`_free_palette_color`), not the next
/// positional slot; a claimed label already on the scale paints with its
/// first occurrence, as Vega's ordinal domain dedupes.
pub(crate) struct LayerScale<'a> {
    palette: &'a [String],
    single_series_fill: &'a str,
    category_colors: &'a [crate::common::CategoryColorScale],
    color_split_base: bool,
    /// The layered endpoint-label rail keys its anchors by label, so a repeat
    /// would collapse two of them.
    rail_may_fire: bool,
    base_len: usize,
    domain: Vec<String>,
    range: Vec<String>,
    glyphs: HashMap<String, LegendSymbol<'static>>,
    /// The labels a bar layer drew (`square_datums`): a square stroked in its own fill.
    squares: HashSet<String>,
}

impl<'a> LayerScale<'a> {
    pub(crate) fn with_rail(mut self, rail_may_fire: bool) -> Self {
        self.rail_may_fire = rail_may_fire;
        self
    }

    /// `base` is every color the base paints on the scale: one entry per
    /// series, or its one labeled fill. Building one resolves nothing: the
    /// palette and board checks run in the claims, as production runs them
    /// only on its layered path, so a chart without layers never trips them.
    pub(crate) fn new(
        palette: &'a [String],
        single_series_fill: &'a str,
        category_colors: &'a [crate::common::CategoryColorScale],
        base: &[(String, String)],
        color_split_base: bool,
    ) -> Self {
        LayerScale {
            palette,
            single_series_fill,
            category_colors,
            color_split_base,
            rail_may_fire: false,
            base_len: base.len(),
            domain: base.iter().map(|(label, _)| label.clone()).collect(),
            range: base.iter().map(|(_, fill)| fill.clone()).collect(),
            glyphs: HashMap::new(),
            squares: HashSet::new(),
        }
    }

    /// A grouped layer or a color-split base needs a palette to draw from
    /// (`ERR_LAYERED_NO_PALETTE`).
    fn require_palette(&self) -> Result<(), Diagnostic> {
        if self.palette.is_empty() {
            return Err(Diagnostic::new(&crate::codes::ERR_LAYERED_NO_PALETTE, []));
        }
        Ok(())
    }

    /// Every color the board binds to a category value, drawn by this chart or
    /// not: an unauthored layer must not take one (empty without a palette).
    fn board_colors(&self) -> Result<Vec<String>, Diagnostic> {
        let mut board = Vec::new();
        if self.palette.is_empty() {
            return Ok(board);
        }
        for scale in self.category_colors {
            for value in scale.slots.keys() {
                board.push(
                    crate::common::category_color(scale, value, self.palette)
                        .map_err(Diagnostic::refused)?,
                );
            }
        }
        Ok(board)
    }

    fn free_color(&self) -> Result<String, Diagnostic> {
        if self.palette.is_empty() {
            return Ok(self.single_series_fill.to_string());
        }
        let board = self.board_colors()?;
        let taken: HashSet<String> = board.iter().chain(&self.range).map(|c| c.to_lowercase()).collect();
        Ok(self
            .palette
            .iter()
            .find(|c| !taken.contains(&c.to_lowercase()))
            .unwrap_or(&self.palette[self.range.len() % self.palette.len()])
            .clone())
    }

    /// A layer claims `label` with its authored color, else the next free one,
    /// and returns that color: bar and line marks paint it as a literal (the
    /// emitter's single-series color), where `color_of` is what an encoded
    /// mark or the legend reads back.
    pub(crate) fn claim(&mut self, label: &str, authored: Option<&str>) -> Result<String, Diagnostic> {
        if self.color_split_base {
            self.require_palette()?;
        }
        let fill = match authored {
            Some(color) => color.to_string(),
            None => self.free_color()?,
        };
        self.domain.push(label.to_string());
        self.range.push(fill.clone());
        Ok(fill)
    }

    /// Raises `ERR_LAYER_LABEL_COLLISION` for a label the scale already holds.
    /// Production raises it only where a repeated domain entry would merge two
    /// legend rows of a color-split base or collapse two rail anchors; elsewhere a repeat is legal (the
    /// ordinal scale dedupes it).
    pub(crate) fn refuse_repeat(&self, label: &str) -> Result<(), Diagnostic> {
        if (self.color_split_base || self.rail_may_fire) && self.domain.iter().any(|d| d == label) {
            return Err(Diagnostic::new(&crate::codes::ERR_LAYER_LABEL_COLLISION, [("label", Value::from(label))]));
        }
        Ok(())
    }

    /// A color-grouped layer claims each of its values the scale lacks.
    pub(crate) fn claim_values(&mut self, values: &[String]) -> Result<(), Diagnostic> {
        self.require_palette()?;
        for value in values {
            if !self.domain.contains(value) {
                self.claim(value, None)?;
            }
        }
        Ok(())
    }

    /// The glyph a layer's entry draws, keyed by label as production's
    /// `stroke_datums`/`circle_datums` are. A stroke outranks a circle whatever
    /// the layer order, as the symbolType ternary chain does.
    pub(crate) fn set_glyph(&mut self, label: &str, glyph: LegendSymbol<'static>) {
        let kept = self.glyphs.get(label).is_some_and(|held| {
            matches!(held, LegendSymbol::Stroke(_)) && !matches!(glyph, LegendSymbol::Stroke(_))
        });
        if !kept {
            self.glyphs.insert(label.to_string(), glyph);
        }
    }

    /// A bar layer's entry draws a square under any stroke or circle glyph
    /// (`square_datums`, the chain's lowest rung above the base's own).
    pub(crate) fn set_square(&mut self, label: &str) {
        self.squares.insert(label.to_string());
    }

    /// The color a series paints with: its claimed one, none where the rows have no value.
    pub(crate) fn color_of_value(&self, value: Option<&str>) -> &str {
        value.map_or(UNSCALED_COLOR, |value| self.color_of(value))
    }

    /// The color `row`'s own value of `field` paints with.
    pub(crate) fn color_of_row(&self, row: &serde_json::Map<String, Value>, field: &str) -> &str {
        self.color_of_value(layer_color_of(row, field).as_deref())
    }

    /// The fill `label` paints with. Vega's ordinal scale dedupes the domain
    /// and indexes the range by the deduped position, so a repeated label's
    /// own claim shifts nothing but its (wasted) range entry.
    pub(crate) fn color_of(&self, label: &str) -> &str {
        let mut distinct: Vec<&str> = Vec::new();
        for entry in &self.domain {
            if !distinct.contains(&entry.as_str()) {
                distinct.push(entry);
            }
            if entry == label {
                return &self.range[distinct.len() - 1];
            }
        }
        panic!("the layer claimed its label");
    }

    /// The layers' own entries: each distinct label past the base, once.
    pub(crate) fn layer_entries(&self) -> Vec<(&str, &str)> {
        let mut seen: Vec<&str> = self.domain[..self.base_len].iter().map(String::as_str).collect();
        let mut out = Vec::new();
        for label in &self.domain[self.base_len..] {
            if !seen.contains(&label.as_str()) {
                seen.push(label);
                out.push((label.as_str(), self.color_of(label)));
            }
        }
        out
    }

    /// The mixed-mark legend (`_mixed_mark_legend_symbols`, `_overlay.py`):
    /// `base` as displayed, then the layers' own entries. An entry draws the
    /// glyph set for its label, else `base_glyph` (`Square` for a bar base,
    /// `Stroke` for a line base, since `base_symbol` is the base MARK's own
    /// glyph).
    pub(crate) fn legend_entries<'s>(
        &'s self,
        base: &'s [(String, String)],
        base_glyph: impl Fn(&'s str) -> LegendSymbol<'s>,
    ) -> Vec<LegendEntry<'s>> {
        base.iter()
            .map(|(label, fill)| (label.as_str(), fill.as_str()))
            .chain(self.layer_entries())
            .map(|(label, fill)| LegendEntry {
                label,
                fill,
                symbol: Some(match self.glyphs.get(label) {
                    Some(&glyph) => glyph,
                    None if self.squares.contains(label) => LegendSymbol::Square(fill),
                    None => base_glyph(fill),
                }),
                series_key: None,
            })
            .collect()
    }
}

/// What one layer claimed on the shared color scale.
pub(crate) struct LayerClaim {
    /// The layer's legend label.
    pub label: String,
    /// The color values a layer grouping by its own `color` claimed.
    pub values: Vec<String>,
    /// The color a layer with no `color` took for its label, which its marks
    /// paint as a literal even when the label repeats an earlier entry.
    pub fill: Option<String>,
    /// Whether the claimed values draw as circles in the legend: a scatter
    /// layer's own grouping.
    pub circle: bool,
    /// The sequential scale of a layer colored by a numeric field: it claims no
    /// slot on the shared scale and paints each value by position on this one.
    pub numeric: Option<ContinuousColor>,
}

impl LayerClaim {
    /// The color a group of rows sharing `value` of the layer's color `field` paints.
    pub(crate) fn group_color(
        &self,
        scale: &LayerScale,
        field: &str,
        value: Option<&str>,
        rows: &[serde_json::Map<String, Value>],
    ) -> String {
        match &self.numeric {
            Some(numeric) => rows
                .iter()
                .find_map(|row| row_num(row, field))
                .map_or_else(|| UNSCALED_COLOR.to_string(), |v| numeric.fill(v)),
            None => scale.color_of_value(value).to_string(),
        }
    }

    /// The color `row`'s own value of the layer's color `field` paints.
    pub(crate) fn row_color(&self, scale: &LayerScale, field: &str, row: &serde_json::Map<String, Value>) -> String {
        match &self.numeric {
            Some(numeric) => row_num(row, field).map_or_else(|| UNSCALED_COLOR.to_string(), |v| numeric.fill(v)),
            None => scale.color_of_row(row, field).to_string(),
        }
    }
}

/// The sequential scale a layer's `color` field draws over `rows`, when Vega-Lite types
/// the field quantitative (`infer_vega_type_from_data`): its first 10 rows hold only
/// numbers and nulls. The domain is the field's extent over every row.
pub(crate) fn numeric_layer_color(rows: &[serde_json::Map<String, Value>], field: &str) -> Option<ContinuousColor> {
    let first = rows.first()?;
    if !first.contains_key(field) {
        return None;
    }
    let sample = || rows.iter().take(10).filter_map(|row| row.get(field)).filter(|v| !v.is_null());
    if sample().next().is_none() || !sample().all(|v| v.is_number()) {
        return None;
    }
    let values = rows.iter().filter_map(|row| row_num(row, field));
    let (lo, hi) = values.fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| (lo.min(v), hi.max(v)));
    Some(ContinuousColor::blues(lo, hi))
}

/// Every layer's claim on the shared scale, in authored order (`_overlay.py`):
/// a layer grouping by a `color` field claims each value the scale lacks, any
/// other claims its label; each entry draws its mark's legend glyph. `layers`
/// pairs each layer with the rows it reads.
pub(crate) fn claim_layers(
    scale: &mut LayerScale,
    layers: &[(&OverlayLayer, &[serde_json::Map<String, Value>])],
    field_labels: &crate::common::FieldLabels,
) -> Result<Vec<LayerClaim>, Diagnostic> {
    let area_opacity = shared_area_opacity(layers.iter().map(|(l, _)| *l));
    layers
        .iter()
        .map(|&(layer, rows)| {
            let label = crate::common::axis_title_text(layer.y(), layer.label(), field_labels)?;
            let (glyph, authored) = match layer {
                OverlayLayer::Bar(_) => (None, None),
                OverlayLayer::Line(l) => (Some(l.legend_glyph(area_opacity)), l.authored_series_color()),
                OverlayLayer::Scatter(l) => {
                    (l.color.is_some().then(|| LegendSymbol::FadedCircle(area_opacity.unwrap_or(1.0))), l.point_mark.color.as_deref())
                }
            };
            if let Some(numeric) = layer.color().and_then(|field| numeric_layer_color(rows, field)) {
                return Ok(LayerClaim { label, values: Vec::new(), fill: None, circle: false, numeric: Some(numeric) });
            }
            let Some(color_field) = layer.color() else {
                scale.refuse_repeat(&label)?;
                let fill = scale.claim(&label, authored)?;
                if let Some(glyph) = glyph {
                    scale.set_glyph(&label, glyph);
                }
                if matches!(layer, OverlayLayer::Bar(_)) {
                    scale.set_square(&label);
                }
                return Ok(LayerClaim { label, values: Vec::new(), fill: Some(fill), circle: false, numeric: None });
            };
            let values = layer_color_values(rows, color_field);
            scale.claim_values(&values)?;
            if let Some(glyph) = glyph {
                values.iter().for_each(|value| scale.set_glyph(value, glyph));
            }
            if matches!(layer, OverlayLayer::Bar(_)) {
                values.iter().for_each(|value| scale.set_square(value));
            }
            Ok(LayerClaim { label, values, fill: None, circle: matches!(layer, OverlayLayer::Scatter(_)), numeric: None })
        })
        .collect()
}

/// A band x's step geometry: its bandwidth and the centers of its first and
/// last band (the bands a `left`/`right` caption has no room beside).
#[derive(Clone, Copy)]
pub(crate) struct StepBand {
    pub bandwidth: f64,
    pub first: f64,
    pub last: f64,
}

/// Paints a shared-scale `type: line` layer's own ink: a plain stroked
/// path through `rows` at `x_of`/`y_of`'s positions (never deduplicated —
/// production gives this layer no `x`/`xOffset`/`data` override of its
/// own, so Vega draws it straight through every row in order), then its
/// own point markers when `point_mark.size` is authored. Halo is never
/// drawn (`suppress_halo=True`, `_layers.py`) — a layer over a different
/// base series must not paint a knockout mask over it. `x_of` returns
/// `None` to skip a row with no position on the base's own domain (a bar
/// base's category not present there); `y_of` is the shared/independent
/// linear scale's own `position(val)`, already clamped to the plot.
/// `step_lead` is the share of the band a step layer's plateaus start after
/// (`0.0` unless a grouped base's series fill the band's first slots).
/// An area layer's fill spans each row's pile (`area`): rows sharing an x stack
/// in row order (Vega-Lite's default `stack: zero` on an area mark with no
/// series to order by), each row's span running from the pile below it to its
/// own end.
/// `series_color` is the layer's (or color value's) shared-scale color: the
/// area fill and the hover targets take it, the path takes an authored
/// `stroke.color` over it, and the points an authored `point_mark.color`.
/// `stacked` gives an area row's `(start, end)` on the stack of its color
/// groups: the fill spans it while the line and points stay on the raw value.
pub(crate) fn paint_line_layer_marks(
    svg: &mut String,
    l: &OverlayLineLayer,
    rows: &[serde_json::Map<String, Value>],
    // A NaN x is Vega's NaN position: the mark sits at 0 and the line or area is undefined there.
    x_of: impl Fn(&serde_json::Map<String, Value>) -> Option<f64>,
    label_x_of: Option<&dyn Fn(&serde_json::Map<String, Value>) -> Option<f64>>,
    y_of: impl Fn(f64) -> f64,
    frame: &AreaFrame,
    horizontal: bool,
    series_color: &str,
    canvas: &str,
    mut describe: impl FnMut(&serde_json::Map<String, Value>, f64) -> Result<Option<String>, String>,
    board_style: Option<&crate::BoardStyle>,
    mut describe_label: impl FnMut(&serde_json::Map<String, Value>, f64) -> Result<Option<String>, String>,
    step_band: Option<StepBand>,
    step_lead: f64,
    stacked: Option<&LayerStack>,
    link: Option<&crate::href::DatumLink>,
) -> Result<(), String> {
    use std::fmt::Write as _;

    // A band step's marks are drawn from data Vega-Lite rewrites, so they carry no link; its labels still do.
    let mark_link = link.filter(|_| !(l.curve_name() == Some("step") && step_band.is_some()));
    let marks_from = svg.len();

    let mut points: Vec<(f64, f64)> = Vec::with_capacity(rows.len());
    // Each point's own link; a path's is its first datum's.
    let mut hrefs: Vec<Option<String>> = Vec::with_capacity(rows.len());
    // Each point's area span `(top, bottom)` in the pile of the rows at its x.
    let mut spans: Vec<(f64, f64)> = Vec::with_capacity(rows.len());
    let mut piles: HashMap<u64, crate::common::StackZero> = HashMap::new();
    let mut labels: Vec<Option<String>> = Vec::with_capacity(rows.len());
    let label_style = l
        .visible_labels()
        .map(|ml| LayerLabelStyle::new(ml, l.label_is_house, rows, board_style))
        .transpose()?;
    let mut captions: Vec<(LayerCaption, Option<String>)> = Vec::new();
    let mut label_points: Vec<(f64, f64)> = Vec::new();
    let mut caption_hrefs: Vec<Option<String>> = Vec::new();
    let stack_spans = stacked.map(|stack| stack.of_rows(rows));
    let mut undefined: Vec<bool> = Vec::with_capacity(rows.len());
    // Where a row with no value sits: the number of drawn points before it
    // and its position along the dimension channel. It draws nothing but ends
    // the path, as Vega's `defined` predicate does.
    let mut gaps: Vec<(usize, f64)> = Vec::new();
    // The x and row of every drawn point, for the link's impute pass.
    let mut kept_x: Vec<f64> = Vec::with_capacity(rows.len());
    let mut kept_rows: Vec<&serde_json::Map<String, Value>> = Vec::with_capacity(rows.len());
    for (index, row) in rows.iter().enumerate() {
        let Some(x) = x_of(row) else { continue };
        let Some(val) = row_num(row, &l.y) else {
            gaps.push((points.len(), x));
            continue;
        };
        kept_x.push(x);
        kept_rows.push(row);
        undefined.push(x.is_nan());
        // An undefined row shares no pile with the row that really sits at 0.
        let pile_key = if x.is_nan() { u64::MAX - index as u64 } else { x.to_bits() };
        let x = if x.is_nan() { 0.0 } else { x };
        points.push(if horizontal { (y_of(val), x) } else { (x, y_of(val)) });
        hrefs.push(link.map(|link| link.href(row)).transpose()?);
        let (below, end) = match &stack_spans {
            Some(spans) => spans[index].expect("a row with a value is on the stack"),
            None => piles.entry(pile_key).or_insert_with(|| crate::common::StackZero::new(0.0)).push(val),
        };
        spans.push(((frame.free_y)(end), (frame.free_y)(below)));
        labels.push(describe(row, val)?);
        if let Some(style) = &label_style {
            // A band-width plateau's `left`/`right` caption anchors at its band's edge.
            let band = step_band
                .filter(|_| l.curve_name() == Some("step"))
                .map(|sb| ((x - sb.first).abs() < 1e-9, (x - sb.last).abs() < 1e-9));
            // A caption sits at the label x column when the family reads another than the mark's.
            if let Some(label_x) = label_x_of.map_or(Some(x), |label_x_of| label_x_of(row)) {
                label_points.push(if horizontal { (y_of(val), label_x) } else { (label_x, y_of(val)) });
                captions.push((style.caption(row, val, band)?, describe_label(row, val)?));
                caption_hrefs.push(hrefs[hrefs.len() - 1].clone());
            }
        }
    }
    // A link adds its href to the stack's groups: the impute pass puts a zero row per href at
    // every x it lacks, and the stack orders each column by href, descending.
    let mut linked_items: Option<Vec<(f64, f64, f64)>> = None;
    if let (Some(_), None, Some(_)) = (&l.area_mark, stacked, mark_link) {
        let groups: Vec<&str> = hrefs.iter().flatten().map(String::as_str).fold(Vec::new(), |mut seen, href| {
            if !seen.contains(&href) {
                seen.push(href);
            }
            seen
        });
        let mut columns: Vec<f64> = Vec::new();
        for (&x, _) in kept_x.iter().zip(&undefined).filter(|(_, undefined)| !**undefined) {
            if !columns.iter().any(|seen| seen.to_bits() == x.to_bits()) {
                columns.push(x);
            }
        }
        // (kept index, x, value, href): the rows, then the imputed zeros group by group.
        let mut items: Vec<(Option<usize>, f64, f64, &str)> = Vec::new();
        for (i, &row) in kept_rows.iter().enumerate().filter(|(i, _)| !undefined[*i]) {
            items.push((Some(i), kept_x[i], row_num(row, &l.y).expect("a kept row has its value"), hrefs[i].as_deref().expect("a linked row has an href")));
        }
        for group in &groups {
            for &x in &columns {
                if !items.iter().any(|&(i, ix, _, href)| i.is_some() && ix.to_bits() == x.to_bits() && href == *group) {
                    items.push((None, x, 0.0, group));
                }
            }
        }
        let mut piles: HashMap<u64, crate::common::StackZero> = HashMap::new();
        let mut order: Vec<usize> = (0..items.len()).collect();
        order.sort_by(|&a, &b| items[b].3.cmp(items[a].3));
        let mut ends: Vec<(f64, f64)> = vec![(0.0, 0.0); items.len()];
        for &item in &order {
            ends[item] = piles.entry(items[item].1.to_bits()).or_insert_with(|| crate::common::StackZero::new(0.0)).push(items[item].2);
        }
        for (&(kept, ..), &(below, end)) in items.iter().zip(&ends) {
            if let Some(kept) = kept {
                spans[kept] = ((frame.free_y)(end), (frame.free_y)(below));
            }
        }
        let mut path: Vec<(f64, f64, f64)> = items.iter().zip(&ends).map(|(&(_, x, ..), &(below, end))| (x, (frame.free_y)(end), (frame.free_y)(below))).collect();
        path.sort_by(|a, b| a.0.total_cmp(&b.0));
        linked_items = Some(path);
    }
    let line_color = l.line_mark.stroke.color.as_deref().unwrap_or(series_color);
    let join = l.line_mark.stroke.join.as_deref().unwrap_or("miter");
    let curve = match curve_named(l.curve_name(), "layer")? {
        d3rs::shape::Curve::MonotoneX if horizontal => d3rs::shape::Curve::MonotoneY,
        other => other,
    };
    // A literal `step` on a band x is `step_band.apply_step_band`: every row
    // doubles onto its band's two edges, drawn in row order through
    // `step-after` (`order: False`), and `connect: false` makes each row its
    // own inset plateau. Its symbols ride the doubled rows; its labels the
    // undoubled ones.
    let band_step = step_band.map(|sb| sb.bandwidth).filter(|_| curve == d3rs::shape::Curve::Step);
    let disconnected = band_step.is_some() && l.is_disconnected();
    let plateau = l.plateau_share()?;
    let cap = crate::common::resolve_stroke_cap(
        Some(l.line_mark.stroke.cap.as_deref().unwrap_or("butt")),
        disconnected,
        l.line_mark.disconnected_cap.as_deref(),
    );
    let dash = l.line_mark.stroke.dasharray.as_deref().map_or(Ok(String::new()), |raw| {
        let fit = band_step.filter(|_| disconnected).map(|bw| bw * plateau);
        Ok::<_, String>(crate::step_band::attr(&crate::step_band::parse("layer", raw, fit.is_some())?, fit))
    })?;
    let (curve, runs, area_runs, mark_points, mark_labels, mark_hrefs, run_hrefs): (_, Vec<Vec<(f64, f64)>>, Vec<Vec<(f64, f64, f64)>>, Vec<(f64, f64)>, Vec<Option<String>>, Vec<Option<String>>, Vec<Option<String>>) =
        match band_step {
            Some(bw) => {
                let (lo, hi) = crate::step_band::band_step_edges(plateau);
                let pairs: Vec<[(f64, f64); 2]> = points
                    .iter()
                    .map(|&(cx, y)| {
                        // Over a grouped base the plateau is the last slot of the shared offset
                        // scale, which spans the inset band.
                        let edge = |at: f64| {
                            let u = (at - lo) / (hi - lo);
                            cx - bw / 2.0 + bw * (lo + (hi - lo) * (step_lead + (1.0 - step_lead) * u))
                        };
                        [(edge(lo), y), (edge(hi), y)]
                    })
                    .collect();
                let flat: Vec<(f64, f64)> = pairs.iter().flatten().copied().collect();
                let spanned: Vec<[(f64, f64, f64); 2]> =
                    pairs.iter().zip(&spans).map(|(p, &(top, bottom))| p.map(|(x, _)| (x, top, bottom))).collect();
                // `connect: false` is a `detail` on the x field: one path per band,
                // holding every row of that band in row order.
                let mut bands: Vec<(u64, Vec<usize>)> = Vec::new();
                for (i, &(cx, _)) in points.iter().enumerate() {
                    match bands.iter_mut().find(|(key, _)| *key == cx.to_bits()) {
                        Some((_, rows_at)) => rows_at.push(i),
                        None => bands.push((cx.to_bits(), vec![i])),
                    }
                }
                // A row with no value ends the connected path at its band.
                let cuts: Vec<usize> = gaps.iter().map(|&(before, _)| before).collect();
                let runs = if disconnected {
                    bands.iter().map(|(_, at)| at.iter().flat_map(|&i| pairs[i]).collect()).collect()
                } else {
                    runs_cut_before(&pairs, &cuts)
                };
                let area_runs = if disconnected {
                    bands.iter().map(|(_, at)| at.iter().flat_map(|&i| spanned[i]).collect()).collect()
                } else {
                    runs_cut_before(&spanned, &cuts)
                };
                let doubled_labels = labels.iter().flat_map(|label| [label.clone(), label.clone()]).collect();
                let doubled_hrefs = hrefs.iter().flat_map(|href| [href.clone(), href.clone()]).collect();
                let run_hrefs = if disconnected {
                    bands.iter().map(|(_, at)| hrefs[at[0]].clone()).collect()
                } else {
                    vec![hrefs[0].clone()]
                };
                (d3rs::shape::Curve::StepAfter, runs, area_runs, flat, doubled_labels, doubled_hrefs, run_hrefs)
            }
            None => {
                // Vega sorts a line/area mark's vertices along its dimension channel (the
                // scale domain's order), while its symbols keep the data's own order.
                // A row with no value keeps its place in that order and ends the run it sits in.
                let along = |i: usize| if horizontal { points[i].1 } else { points[i].0 };
                let mut order: Vec<(f64, usize, Option<usize>)> = (0..points.len())
                    .filter(|&i| !undefined[i])
                    .map(|i| (along(i), 2 * i + 1, Some(i)))
                    .chain(gaps.iter().map(|&(before, x)| (x, 2 * before, None)))
                    .collect();
                order.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
                let (mut path_runs, mut span_runs) = (vec![Vec::new()], vec![Vec::new()]);
                for &(_, _, point) in &order {
                    match point {
                        Some(i) => {
                            path_runs.last_mut().expect("a run is open").push(points[i]);
                            span_runs.last_mut().expect("a run is open").push((along(i), spans[i].0, spans[i].1));
                        }
                        None if !path_runs.last().expect("a run is open").is_empty() => {
                            path_runs.push(Vec::new());
                            span_runs.push(Vec::new());
                        }
                        None => {}
                    }
                }
                if path_runs.len() > 1 && path_runs.last().is_some_and(Vec::is_empty) {
                    path_runs.pop();
                    span_runs.pop();
                }
                // A stack's imputed zero cells join the run their dimension position falls in.
                if let Some(stack) = stacked {
                    for (x_text, (below, end)) in stack.imputed_cells_for(rows) {
                        let mut stand_in = serde_json::Map::new();
                        stand_in.insert(stack.x_field().to_string(), Value::String(x_text.to_string()));
                        if let Some(x) = x_of(&stand_in) {
                            let at = span_runs
                                .iter()
                                .position(|run| run.first().zip(run.last()).is_some_and(|(a, b)| a.0 <= x && x <= b.0))
                                .unwrap_or(0);
                            let run = &mut span_runs[at];
                            run.push((x, (frame.free_y)(end), (frame.free_y)(below)));
                            run.sort_by(|a, b| a.0.total_cmp(&b.0));
                        }
                    }
                }
                // Each run's link is its first point's; a row with no x leads the path and its link.
                let mut run_hrefs: Vec<Option<String>> = Vec::new();
                let mut open = true;
                for &(_, _, point) in &order {
                    match point {
                        Some(i) if open => {
                            run_hrefs.push(hrefs[i].clone());
                            open = false;
                        }
                        Some(_) => {}
                        None => open = true,
                    }
                }
                run_hrefs.resize(path_runs.len(), None);
                if let Some(first) = (0..points.len()).find(|&i| undefined[i]) {
                    run_hrefs[0] = hrefs[first].clone();
                }
                if let Some(linked) = linked_items.take() {
                    span_runs = vec![linked];
                    path_runs.truncate(1);
                }
                (curve, path_runs, span_runs, points.clone(), labels.clone(), hrefs.clone(), run_hrefs)
            }
        };
    if let Some(area) = &l.area_mark {
        let d = if horizontal { d3rs::shape::stacked_area_path_horizontal(&area_runs, curve) } else { d3rs::shape::stacked_area_path(&area_runs, curve) };
        // A clipped pile is cut at the plot rect, as Vega cuts the mark, never
        // squeezed onto the edge.
        let (plot_w, plot_h) = frame.plot;
        let leaves_plot = frame.clip
            && area_runs.iter().flatten().any(|&(_, top, bottom)| [top, bottom].iter().any(|v| *v < 0.0 || *v > if horizontal { plot_w } else { plot_h }));
        let clip_id = format!("dbt-area-clip-{plot_w}-{plot_h}");
        let clip_attr = if leaves_plot {
            let _ = write!(svg, "<clipPath id=\"{clip_id}\"><rect width=\"{plot_w}\" height=\"{plot_h}\"/></clipPath>");
            format!(" clip-path=\"url(#{clip_id})\"")
        } else {
            String::new()
        };
        crate::contract::connected_group_open(svg, "area", "layer_1_marks");
        let first = run_hrefs.first().cloned().flatten();
        svg.push_str(&crate::href::anchor_open(crate::href::anchor_for(mark_link, &first)));
        let _ = write!(
            svg,
            "<path d=\"{d}\" fill=\"{}\" fill-opacity=\"{}\" stroke-opacity=\"0\" opacity=\"1\"{clip_attr}/>",
            xml_escape_attr(series_color),
            area.opacity,
        );
        svg.push_str(crate::href::anchor_close(crate::href::anchor_for(mark_link, &first)));
        crate::contract::mark_group_close(svg);
    }
    // The imputed zero rows of this color value form their own mark group, zero-high atop each column.
    if let (Some(area), Some(stack), Some(first), Some(_)) = (&l.area_mark, stacked, rows.first(), mark_link) {
        let mut items: Vec<(f64, f64, &str)> = Vec::new();
        for (key, href) in stack.imputed_for(&layer_color_of(first, stack.color_field())) {
            let mut probe = serde_json::Map::new();
            probe.insert(stack.x_field().to_string(), Value::String(key.clone()));
            if let Some(x) = x_of(&probe).filter(|x| !x.is_nan()) {
                items.push((x, (frame.free_y)(stack.column_top(key)), href));
            }
        }
        items.sort_by(|a, b| a.0.total_cmp(&b.0));
        if let Some(&(_, _, first_href)) = items.first() {
            let run: Vec<(f64, f64, f64)> = items.iter().map(|&(x, y, _)| (x, y, y)).collect();
            let d = d3rs::shape::stacked_area_path(&[run], curve);
            let first_href = Some(first_href.to_string());
            crate::contract::connected_group_open(svg, "area", "layer_1_marks");
            svg.push_str(&crate::href::anchor_open(crate::href::anchor_for(mark_link, &first_href)));
            let _ = write!(
                svg,
                "<path d=\"{d}\" fill=\"{}\" fill-opacity=\"{}\" stroke-opacity=\"0\" opacity=\"1\"/>",
                xml_escape_attr(series_color),
                area.opacity,
            );
            svg.push_str(crate::href::anchor_close(crate::href::anchor_for(mark_link, &first_href)));
            crate::contract::mark_group_close(svg);
        }
    }
    // A disconnected plateau is its own path, never joined to its neighbors.
    for (run, run_href) in runs.iter().zip(&run_hrefs) {
        let d = d3rs::shape::line_path(std::slice::from_ref(run), curve);
        crate::contract::connected_group_open(svg, "line", "layer_1_marks");
        svg.push_str(&crate::href::anchor_open(crate::href::anchor_for(mark_link, run_href)));
        let _ = write!(
            svg,
            "<path d=\"{d}\" fill=\"none\" stroke=\"{line_color}\" \
             stroke-width=\"{}\" stroke-linecap=\"{cap}\" stroke-linejoin=\"{join}\"{dash}/>",
            l.line_mark.stroke.width, line_color = xml_escape_attr(line_color), cap = xml_escape_attr(cap), join = xml_escape_attr(join)
        );
        svg.push_str(crate::href::anchor_close(crate::href::anchor_for(mark_link, run_href)));
        crate::contract::mark_group_close(svg);
    }
    // A visible point overlay only draws when `point_mark.size` is authored
    // (production's `point_size` gate, `_layers.py`); a hollow point
    // (`filled: false`) paints its own `fill`, else the chart canvas (an
    // engine default: `_emit_point_mark` leaves it unset), with the series
    // color as its stroke. The invisible hover target sits over every datum
    // (`_hover_target_point`); it announces the datum only when no drawn
    // point does.
    let announced = mark_labels.iter().any(Option::is_some);
    let drawn = l.point_mark.size.filter(|s| *s > 0.0);
    if let Some(size) = drawn {
        let r_pt = crate::common::symbol_radius(size);
        if announced {
            crate::contract::mark_group_open(svg, "symbol", "layer_2_marks")?;
        }
        for ((&(px, py), label), href) in mark_points.iter().zip(&mark_labels).zip(&mark_hrefs) {
            let point_color = l.point_mark.color.as_deref().unwrap_or(series_color);
            svg.push_str(&crate::href::anchor_open(crate::href::anchor_for(mark_link, href)));
            paint_overlay_point(svg, &l.point_mark, px, py, r_pt, point_color, canvas, label.as_deref())?;
            svg.push_str(crate::href::anchor_close(crate::href::anchor_for(mark_link, href)));
        }
        if announced {
            crate::contract::mark_group_close(svg);
        }
    }
    if announced {
        let hover_r = crate::common::symbol_radius(crate::common::HOVER_TARGET_SIZE);
        if drawn.is_some() {
            crate::contract::hidden_mark_group_open(svg, "symbol", "layer_3_marks");
        } else {
            crate::contract::mark_group_open(svg, "symbol", "layer_3_marks")?;
        }
        for ((&(px, py), label), href) in mark_points.iter().zip(&mark_labels).zip(&mark_hrefs) {
            let aria = if drawn.is_some() { None } else { label.as_deref() };
            let label_attr = crate::contract::aria_attr(aria, crate::contract::ItemRole::Point)?;
            svg.push_str(&crate::href::anchor_open(crate::href::anchor_for(mark_link, href)));
            let _ = write!(
                svg,
                "<circle transform=\"translate({px:.3},{py:.3})\" r=\"{hover_r:.3}\" fill=\"{}\" \
                 opacity=\"0\"{label_attr}/>",
                xml_escape_attr(series_color)
            );
            svg.push_str(crate::href::anchor_close(crate::href::anchor_for(mark_link, href)));
        }
        crate::contract::mark_group_close(svg);
    }
    // An area clips itself; a line's own marks clip as a group, its value labels do not.
    if frame.clip && l.area_mark.is_none() {
        clip_marks_from(svg, marks_from, frame.plot);
    }
    if let Some(style) = &label_style {
        let marks = layer_label_marks(label_points.iter().zip(captions).zip(caption_hrefs), step_band, horizontal);
        paint_layer_value_labels(svg, style, &marks, link.map(|link| link.open).unwrap_or_default())?;
    }
    Ok(())
}

/// The step-after pairs of `pairs` flattened into one run per stretch between
/// `cuts` (each an index of the first pair after a gap); a gap with no pair
/// before it leaves no empty run.
fn runs_cut_before<P: Copy, const N: usize>(pairs: &[[P; N]], cuts: &[usize]) -> Vec<Vec<P>> {
    let mut bounds: Vec<usize> = cuts.to_vec();
    bounds.push(pairs.len());
    let mut start = 0;
    let mut runs = Vec::new();
    for end in bounds {
        if end > start {
            runs.push(pairs[start..end].iter().flatten().copied().collect());
        }
        start = start.max(end);
    }
    if runs.is_empty() {
        runs.push(Vec::new());
    }
    runs
}

/// Where an area layer clips: the unclamped value scale its piles are placed
/// with, the plot's `(width, height)`, and whether production clips the layer
/// to the plot (`_clip_layer_marks`: a zoomed scale, or a normalized one a
/// value leaves). An unclipped pile past the plot paints there and grows the fit.
pub(crate) struct AreaFrame<'a> {
    pub free_y: &'a dyn Fn(f64) -> f64,
    pub plot: (f64, f64),
    pub clip: bool,
}

/// The painted marks of a layer's captions at their datum `points`: a caption
/// with a band edge moves to that edge of its band.
fn layer_label_marks<'a>(
    captions: impl Iterator<Item = ((&'a (f64, f64), (LayerCaption, Option<String>)), Option<String>)>,
    step_band: Option<StepBand>,
    horizontal: bool,
) -> Vec<LayerLabelMark> {
    captions
        .map(|((&(px, py), (caption, aria)), href)| {
            let x = match (caption.spot.edge, step_band) {
                (Some(frac), Some(sb)) if !horizontal => px - sb.bandwidth / 2.0 + sb.bandwidth * frac,
                _ => px,
            };
            LayerLabelMark { x, y: py, caption, aria, href }
        })
        .collect()
}

/// A layer's value-label typography and caption law, shared by the painter
/// and every family's fit ink so the two never drift.
pub(crate) struct LayerLabelStyle<'a> {
    ml: &'a MarkLabels,
    is_house: bool,
    field_is_text: bool,
    pub font: crate::families::bar::LabelFont,
    pub font_style: String,
    pub color: &'a str,
}

/// One caption fixed ahead of layout: its text and where it sits.
pub(crate) struct LayerCaption {
    pub text: String,
    pub spot: crate::common::CaptionSpot,
}

impl<'a> LayerLabelStyle<'a> {
    /// `rows` are the layer's own rows, which decide whether `labels.field`
    /// prints text (`labels_draw_text`).
    pub(crate) fn new(
        ml: &'a MarkLabels,
        is_house: bool,
        rows: &[serde_json::Map<String, Value>],
        board_style: Option<&crate::BoardStyle>,
    ) -> Result<Self, String> {
        let font = crate::families::bar::label_font(ml, board_style)?;
        let color = ml.font.as_ref().and_then(|f| f.color.as_deref()).unwrap_or("#313233");
        Ok(LayerLabelStyle {
            ml,
            is_house,
            field_is_text: ml.field_is_text(rows),
            font,
            font_style: ml.font.as_ref().map(crate::common::Font::style_attr).unwrap_or_default(),
            color,
        })
    }

    /// The caption of `row`, whose measure is `val`. `band` is `Some((leading,
    /// trailing))` on a band-width mark (`CaptionSpot::of`).
    pub(crate) fn caption(
        &self,
        row: &serde_json::Map<String, Value>,
        val: f64,
        band: Option<(bool, bool)>,
    ) -> Result<LayerCaption, String> {
        let (text, _) = self.ml.field_text(self.is_house, self.field_is_text, Some(row), val)?;
        Ok(LayerCaption { text, spot: crate::common::CaptionSpot::of(self.ml, band)? })
    }

    /// The shaped width of one label's text.
    pub(crate) fn width(&self, fonts: &FontBook, text: &str) -> Result<f64, String> {
        fonts.measure_shaped(text, &self.font.family, self.font.size)
    }

    /// The ink of a caption placed at `(x, y)` (the datum, or its band edge
    /// when the spot has one), offset by the spot's `dx`/`dy`. The one law
    /// every family's fit shares.
    pub(crate) fn ink(&self, x: f64, y: f64, width: f64, spot: &crate::common::CaptionSpot) -> crate::vega_fit::Bounds {
        crate::vega_fit::text_ink(x + spot.dx, y + spot.dy, width, self.font.size, spot.align, spot.baseline)
    }
}

/// One caption to paint: its anchor `(x, y)` (the datum, or its band edge when
/// the spot has one), text, spot and datum description.
pub(crate) struct LayerLabelMark {
    pub x: f64,
    pub y: f64,
    pub caption: LayerCaption,
    pub aria: Option<String>,
    /// The datum's link, which wraps its label.
    pub href: Option<String>,
}

/// The layer's value-label text mark set, drawn after the layer's marks the
/// way production layers it, each at its caption's spot.
pub(crate) fn paint_layer_value_labels(
    svg: &mut String,
    style: &LayerLabelStyle,
    marks: &[LayerLabelMark],
    link_open: crate::href::LinkOpen,
) -> Result<(), String> {
    let stamped = marks.iter().any(|m| m.aria.is_some());
    if stamped {
        crate::contract::value_label_group_open(svg, "layer_4_marks")?;
    }
    for m in marks {
        let spot = &m.caption.spot;
        let anchor = link_open.anchor(m.href.as_deref());
        svg.push_str(&crate::href::anchor_open(anchor));
        crate::common::write_value_label(
            svg,
            m.x + spot.dx,
            m.y + spot.dy + crate::measure::baseline_shift(spot.baseline, style.font.size),
            spot.align,
            &style.font.family,
            style.font.size,
            &style.font.weight,
            &style.font_style,
            style.color,
            &crate::contract::aria_attr(m.aria.as_deref(), crate::contract::ItemRole::TextMark)?,
            &m.caption.text,
        );
        svg.push_str(crate::href::anchor_close(anchor));
    }
    if stamped {
        crate::contract::mark_group_close(svg);
    }
    Ok(())
}

/// One point marker (filled/hollow, per `OverlayPointMark`'s law) at
/// `(px, py)` -- shared by a line layer's own point overlay and a scatter
/// layer's own points, the SAME `_emit_point_mark`/`filled` formula either
/// draws (`vl_field_maps.py::_emit_point_mark`). `aria` carries the
/// datum's structured-tooltip label when the caller wires one (scatter
/// only today); `None` paints the pre-existing unlabeled status quo.
///
/// The circle is centered on its own origin and placed by `transform`, the way
/// Vega places a symbol: `chart_interactivity.js` reads a datum's position off
/// the element's screen CTM, so `cx`/`cy` would put every marker at the group
/// origin.
fn paint_overlay_point(
    svg: &mut String,
    pm: &OverlayPointMark,
    px: f64,
    py: f64,
    r_pt: f64,
    point_color: &str,
    canvas: &str,
    aria: Option<&str>,
) -> Result<(), String> {
    use std::fmt::Write as _;

    let filled = pm.filled.unwrap_or(true);
    let aria_attr = format!(
        "{}{}",
        if aria.is_some() { " opacity=\"1\"" } else { "" },
        crate::contract::aria_attr(aria, crate::contract::ItemRole::Point)?
    );
    if filled {
        let _ = write!(
            svg,
            "<circle transform=\"translate({px:.3},{py:.3})\" r=\"{r_pt:.3}\" fill=\"{point_color}\"{aria_attr}/>", point_color = xml_escape_attr(point_color)
        );
    } else {
        let hollow_fill = pm.fill.as_deref().unwrap_or(canvas);
        let _ = write!(
            svg,
            "<circle transform=\"translate({px:.3},{py:.3})\" r=\"{r_pt:.3}\" fill=\"{hollow_fill}\" \
             stroke=\"{point_color}\" stroke-width=\"{}\"{aria_attr}/>",
            pm.stroke_width, hollow_fill = xml_escape_attr(hollow_fill), point_color = xml_escape_attr(point_color)
        );
    }
    Ok(())
}

/// How a scatter layer's points take their color.
pub(crate) enum PointColor<'a> {
    /// The one color the layer claimed on the shared scale; an authored
    /// `point_mark.color` pins over it.
    Claimed(&'a str),
    /// Off the shared scale per row, which wins over an authored mark color
    /// (Vega-Lite's encoding outranks the mark property).
    Encoded(&'a dyn Fn(&serde_json::Map<String, Value>) -> String),
}

/// A `type: "scatter"` layer's own raw JSON -- bar-only (no other family
/// admits a scatter-type layer today). Unlike the line layer, a scatter
/// layer draws points ONLY (no path), always through EVERY row (no
/// `x`/`data` override is admitted -- `validate_layers` refuses one), so
/// it carries no `line_mark`/`curve` at all.
#[derive(Deserialize)]
pub(crate) struct OverlayScatterLayer {
    pub point_mark: OverlayPointMark,
    pub axis_y: OverlayAxisY,
    #[serde(default)]
    pub x: Option<String>,
    pub y: String,
    /// The layer's legend entry text, read only beside a ranged base
    /// (`validate_scatter_layer`).
    #[serde(default)]
    pub label: Option<String>,
    #[serde(default)]
    pub color: Option<String>,
    #[serde(default)]
    pub query_name: Option<String>,
    #[serde(default)]
    pub label_is_house: bool,
}

impl OverlayScatterLayer {
    /// A scatter layer's payload: the point IS the whole mark, so the resolve
    /// always bakes its size (`0` draws a zero-radius point, as production's
    /// `M0,0` symbol does).
    pub(crate) fn parse(raw: &Value, family: &str) -> Result<Self, String> {
        let layer: Self = Deserialize::deserialize(raw).map_err(|e| format!("{family}: layer payload: {e}"))?;
        match layer.point_mark.size {
            None => Err(format!("{family}: layer payload: a scatter layer has no point_mark.size")),
            Some(size) if size < 0.0 => {
                Err(format!("{family}: layer payload: a scatter layer's point_mark.size {size} is negative"))
            }
            Some(_) => Ok(layer),
        }
    }

    pub(crate) fn size(&self) -> f64 {
        self.point_mark.size.expect("OverlayScatterLayer::parse confirmed a point size")
    }
}

/// Paints a shared-scale `type: "scatter"` layer's own points -- the
/// lollipop law (`charts/extensibility_1`'s `lollipop` chart): a plain
/// point marker per row, wrapped in ONE `layer_N_marks` mark group (Vega's
/// own `mark-symbol` container holds every point of a `point` mark, unlike
/// bar's per-datum `mark-rect` wrapper -- probed against the compiled
/// scenegraph), `N` the scatter layer's own position in production's outer
/// `layer` list (base is `layer_0`), so `layer_index` is `1 +` its authored
/// position -- `1` for `lollipop` (no layer before it), `2` after a bar layer
/// (probed against `bullet`'s own reference: its bar layer is `layer_1_marks`,
/// its line layer `layer_2_layer_*`), and
/// `1`, `2` for a dumbbell's two scatter layers. `x_of`/`y_of` mirror
/// `paint_line_layer_marks`'s own laws (a bar base's band CENTER, the
/// shared/independent linear scale's position); a `horizontal` base swaps the
/// two onto x and y; `aria_of` builds each
/// datum's structured-tooltip label (`Ok(None)` when `tooltip_labels` is
/// off), propagating a real error (a format spec `d3rs::format` can't
/// parse) rather than swallowing it. `size` is the layer's
/// own (`OverlayScatterLayer::size`): the point IS the whole mark, unlike a
/// line layer's optional point overlay.
#[allow(clippy::too_many_arguments)]
pub(crate) fn paint_scatter_layer_marks(
    svg: &mut String,
    l: &OverlayScatterLayer,
    rows: &[serde_json::Map<String, Value>],
    size: f64,
    layer_index: usize,
    horizontal: bool,
    x_of: impl Fn(&serde_json::Map<String, Value>) -> Option<f64>,
    label_x_of: Option<&dyn Fn(&serde_json::Map<String, Value>) -> Option<f64>>,
    y_of: impl Fn(f64) -> f64,
    mut aria_of: impl FnMut(&serde_json::Map<String, Value>, f64) -> Result<Option<String>, String>,
    color: PointColor,
    canvas: &str,
    board_style: Option<&crate::BoardStyle>,
    mut describe_label: impl FnMut(&serde_json::Map<String, Value>, f64) -> Result<Option<String>, String>,
    link: Option<&crate::href::DatumLink>,
    clip: Option<(f64, f64)>,
) -> Result<(), String> {
    let r_pt = crate::common::symbol_radius(size);
    let marks_from = svg.len();
    crate::contract::mark_group_open(svg, "symbol", &format!("layer_{layer_index}_marks"))?;
    let ml = l.point_mark.labels.visible.then_some(&l.point_mark.labels);
    let label_style = ml.map(|ml| LayerLabelStyle::new(ml, l.label_is_house, rows, board_style)).transpose()?;
    let mut label_points: Vec<(f64, f64)> = Vec::new();
    let mut captions: Vec<(LayerCaption, Option<String>)> = Vec::new();
    let mut caption_hrefs: Vec<Option<String>> = Vec::new();
    for row in rows {
        let Some(cross) = x_of(row) else { continue };
        let Some(val) = row_num(row, &l.y) else { continue };
        let measure = y_of(val);
        let (x, y) = if horizontal { (measure, cross) } else { (cross, measure) };
        let aria = aria_of(row, val)?;
        let href = link.map(|link| link.href(row)).transpose()?;
        let anchor = link.and_then(|link| link.open.anchor(href.as_deref()));
        let point_color = match &color {
            PointColor::Claimed(claimed) => l.point_mark.color.as_deref().unwrap_or(claimed).to_string(),
            PointColor::Encoded(of_row) => of_row(row),
        };
        svg.push_str(&crate::href::anchor_open(anchor));
        paint_overlay_point(svg, &l.point_mark, x, y, r_pt, &point_color, canvas, aria.as_deref())?;
        svg.push_str(crate::href::anchor_close(anchor));
        if let Some(style) = &label_style {
            if let Some(label_cross) = label_x_of.map_or(Some(cross), |label_x_of| label_x_of(row)) {
                label_points.push(if horizontal { (measure, label_cross) } else { (label_cross, measure) });
                captions.push((style.caption(row, val, None)?, describe_label(row, val)?));
                caption_hrefs.push(href);
            }
        }
    }
    crate::contract::mark_group_close(svg);
    if let Some(plot) = clip {
        clip_marks_from(svg, marks_from, plot);
    }
    if let Some(style) = &label_style {
        let marks = layer_label_marks(label_points.iter().zip(captions).zip(caption_hrefs), None, horizontal);
        paint_layer_value_labels(svg, style, &marks, link.map(|link| link.open).unwrap_or_default())?;
    }
    Ok(())
}

/// A `type: "bar"` layer's own raw JSON — `bar_mark` is the SAME shape as
/// the base's own `r.style.mark` (`MarkStyle`), reused verbatim rather than
/// a near-duplicate struct.
#[derive(Deserialize)]
pub(crate) struct OverlayBarLayer {
    pub bar_mark: crate::families::bar::MarkStyle,
    pub axis_y: OverlayAxisY,
    #[serde(default)]
    pub x: Option<String>,
    pub y: String,
    /// The column each bar starts from — a candlestick's body runs `open`
    /// to `close`; absent, bars grow from zero.
    #[serde(default)]
    pub y_start: Option<String>,
    #[serde(default)]
    pub label: Option<String>,
    #[serde(default)]
    pub color: Option<String>,
    #[serde(default)]
    pub query_name: Option<String>,
    #[serde(default)]
    pub label_is_house: bool,
}

impl OverlayBarLayer {
    pub(crate) fn parse(raw: &Value, family: &str) -> Result<Self, String> {
        Deserialize::deserialize(raw).map_err(|e| format!("{family}: layer payload: {e}"))
    }
}

/// One authored `chart.layers` entry. Every cartesian family holds its layers as one
/// ordered list, since production paints and claims palette slots in authored
/// order. Boxed so no mark style inflates the enum.
pub(crate) enum OverlayLayer {
    Bar(Box<OverlayBarLayer>),
    Line(Box<OverlayLineLayer>),
    Scatter(Box<OverlayScatterLayer>),
}

/// The mark family of a layer.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum LayerType {
    Line,
    Area,
    Scatter,
    Bar,
}

impl OverlayLayer {
    pub(crate) fn axis_y(&self) -> &OverlayAxisY {
        match self {
            OverlayLayer::Bar(l) => &l.axis_y,
            OverlayLayer::Line(l) => &l.axis_y,
            OverlayLayer::Scatter(l) => &l.axis_y,
        }
    }

    pub(crate) fn x(&self) -> Option<&str> {
        match self {
            OverlayLayer::Bar(l) => l.x.as_deref(),
            OverlayLayer::Line(l) => l.x.as_deref(),
            OverlayLayer::Scatter(l) => l.x.as_deref(),
        }
    }

    pub(crate) fn y(&self) -> &str {
        match self {
            OverlayLayer::Bar(l) => &l.y,
            OverlayLayer::Line(l) => &l.y,
            OverlayLayer::Scatter(l) => &l.y,
        }
    }

    pub(crate) fn label(&self) -> Option<&str> {
        match self {
            OverlayLayer::Bar(l) => l.label.as_deref(),
            OverlayLayer::Line(l) => l.label.as_deref(),
            OverlayLayer::Scatter(l) => l.label.as_deref(),
        }
    }

    pub(crate) fn color(&self) -> Option<&str> {
        match self {
            OverlayLayer::Bar(l) => l.color.as_deref(),
            OverlayLayer::Line(l) => l.color.as_deref(),
            OverlayLayer::Scatter(l) => l.color.as_deref(),
        }
    }

    pub(crate) fn query_name(&self) -> Option<&str> {
        match self {
            OverlayLayer::Bar(l) => l.query_name.as_deref(),
            OverlayLayer::Line(l) => l.query_name.as_deref(),
            OverlayLayer::Scatter(l) => l.query_name.as_deref(),
        }
    }

    /// How far the layer's ink reaches from a datum (a bar's border is the
    /// bar painter's own concern).
    pub(crate) fn mark_reach(&self) -> f64 {
        match self {
            OverlayLayer::Bar(_) => 0.0,
            OverlayLayer::Line(l) => l.mark_reach(),
            OverlayLayer::Scatter(l) => {
                crate::common::symbol_radius(l.size())
                    + crate::common::symbol_stroke_ink(l.point_mark.filled, l.point_mark.stroke_width)
            }
        }
    }

    /// A line or area layer drawing a literal `step` curve.
    pub(crate) fn is_step_curve(&self) -> bool {
        matches!(self, OverlayLayer::Line(l) if l.curve_name() == Some("step"))
    }

    pub(crate) fn layer_type(&self) -> LayerType {
        match self {
            OverlayLayer::Bar(_) => LayerType::Bar,
            OverlayLayer::Line(l) if l.area_mark.is_some() => LayerType::Area,
            OverlayLayer::Line(_) => LayerType::Line,
            OverlayLayer::Scatter(_) => LayerType::Scatter,
        }
    }
}

/// The y-axis plan of a chart whose layers pin a side. Once any layer pins
/// `axis_y.position`, every layer owns a scale and an axis
/// (`resolve.scale.y: independent`): a pinned one on its side, the rest on the
/// free side (left unless every pin is left). The base then takes the right only
/// when every layer's *resolved* side is left (`_resolved_layer_y_orients`,
/// `_overlay.py`'s `base_side`).
pub(crate) struct YSides {
    pub base: LayerSide,
    pub layers: Vec<LayerSide>,
}

impl YSides {
    /// `None` when no layer pins a side: every layer shares the base's scale.
    pub(crate) fn of(layers: &[OverlayLayer]) -> Option<YSides> {
        let pinned: Vec<LayerSide> = layers.iter().filter_map(|l| l.axis_y().position).collect();
        if pinned.is_empty() {
            return None;
        }
        let free = if pinned.iter().all(|side| *side == LayerSide::Left) { LayerSide::Right } else { LayerSide::Left };
        let sides: Vec<LayerSide> = layers.iter().map(|l| l.axis_y().position.unwrap_or(free)).collect();
        let base = if sides.iter().all(|side| *side == LayerSide::Left) { LayerSide::Right } else { LayerSide::Left };
        Some(YSides { base, layers: sides })
    }

    /// One `ScaledLayer` and zero-rule width per layer, each reading `rows[i]`.
    pub(crate) fn scaled<'a>(
        &self,
        layers: &'a [OverlayLayer],
        rows: &[&'a [serde_json::Map<String, Value>]],
        base_x: &'a str,
        ax_y: &crate::common::AxisStyle,
    ) -> (Vec<ScaledLayer<'a>>, Vec<Option<f64>>) {
        let scaled: Vec<ScaledLayer> = layers
            .iter()
            .zip(rows)
            .zip(&self.layers)
            .map(|((layer, &rows), &side)| ScaledLayer::new(layer, rows, base_x, side))
            .collect();
        let zero_rules = scaled.iter().map(|p| p.zero_rule_width(ax_y, self.base)).collect();
        (scaled, zero_rules)
    }
}

/// How a layer's marks meet its own authored domain (`_overlay.py`): within it,
/// cut off at the plot edge (a value outside while the scale's `clip` holds), or
/// painted past the plot (`clip: false`), which grows the fit.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum DomainCut {
    Within,
    Clip,
    Overrun,
}

/// A layer's own independent y scale — computed once from the layer's own row
/// values, never from the negotiated plot size.
pub(crate) struct LayerAxis {
    pub cut: DomainCut,
    /// The side the layer's axis draws on (`YSides`).
    pub side: LayerSide,
    pub domain_min: f64,
    pub domain_max: f64,
    /// The marks' own top, before the domain's nice rounding.
    pub value_max: f64,
    pub tick_values: Vec<f64>,
    pub tick_labels: Vec<String>,
    pub label_ws: Vec<f64>,
    /// Where each label starts past the plot edge: a visible tick's length
    /// plus the theme `labels.padding`. A right axis start-anchors its labels
    /// here (the layer's axis is the bare template with `labelAlign`
    /// dropped); a left one end-anchors them.
    pub label_offset: f64,
    /// `label_offset` plus the widest label — the far edge the rotated
    /// title's own padding starts from.
    pub gutter: f64,
    /// Whether the layer's own grid paints: its authored `grid.visible`, else the base's.
    pub grid_visible: bool,
    pub title_text: String,
    /// Vega's description of the layer's axis.
    pub caption: AxisCaption,
    /// The fit ink this title reserves (0.0 when the theme suppresses axis
    /// titles or `title_text` is empty) — the SAME formula the base's own
    /// `val_title_extra` uses, gated on this layer's own band since the
    /// base and the layer can disagree on whether their titles show.
    pub title_extra: f64,
}

impl LayerAxis {
    /// The axis's own scale at a plot height.
    pub(crate) fn scale(&self, plot_h: f64) -> crate::families::cartesian::LinearScale {
        crate::families::cartesian::LinearScale::new(self.domain_min, self.domain_max, plot_h, 0.0)
    }

    /// How far the axis reaches past the plot edge it sits on: its offset,
    /// label gutter and rotated title.
    pub(crate) fn reach(&self, offset: f64) -> f64 {
        offset + self.gutter + self.title_extra
    }

    /// The x extent the axis adds to a legend's anchor bounds, as
    /// `(left, right)` relative to a plot `pw` wide, given the extent the
    /// base's own axes already span.
    pub(crate) fn widen_axes(&self, (x1, x2): (f64, f64), pw: f64, offset: f64) -> (f64, f64) {
        match self.side {
            LayerSide::Right => (x1, x2.max(pw + self.reach(offset))),
            LayerSide::Left => (x1.min(-self.reach(offset)), x2),
        }
    }

    /// Unions the axis's ink into the fit: its gutter and title box, and each
    /// tick label's own ink. No `parity_visible` thinning: the layer's ladder
    /// is a handful of d3 ticks, and every one draws.
    pub(crate) fn union_axis_ink(
        &self,
        b: &mut crate::vega_fit::Bounds,
        (pw, ph): (f64, f64),
        offset: f64,
        label_limit: f64,
        font_size: f64,
    ) {
        use crate::vega_fit::{text_ink, Bounds};
        let right = self.side == LayerSide::Right;
        let reach = self.reach(offset);
        b.union(&if right { Bounds::new(0.0, 0.0, pw + reach, ph) } else { Bounds::new(-reach, 0.0, 0.0, ph) });
        let scale = self.scale(ph);
        for (&tv, &lw) in self.tick_values.iter().zip(&self.label_ws) {
            let (x, align) = if right {
                (pw + offset + self.label_offset, "left")
            } else {
                (-(offset + self.label_offset), "right")
            };
            b.union(&text_ink(x, scale.position(tv), lw.min(label_limit), font_size, align, "middle"));
        }
    }
}

/// The `labelPadding` Vega falls back to once a re-pin pops the base axis's own
/// padding (`_overlay.py`).
pub(crate) const MOVED_BASE_LABEL_PAD: f64 = 2.0;

/// The y-axis title styles of a chart whose layers own their axes. A dual axis needs
/// both sides labeled to tell the scales apart: production restores the base
/// title when the theme hid it (`_overlay.py`), unless an authored `y_label`
/// with a hidden title suppressed it, and titles the layer axis
/// unconditionally. A restored title carries no theme styling.
pub(crate) struct DualAxisTitles {
    restored: Option<crate::common::AxisTitle>,
    base_suppressed: bool,
}

impl DualAxisTitles {
    pub(crate) fn new(dual: bool, ax_y: &crate::common::AxisStyle, y_label: Option<&str>) -> Self {
        let hidden = ax_y.title.visible == Some(false);
        DualAxisTitles {
            restored: (dual && hidden)
                .then(|| crate::common::vega_default_axis_title(ax_y.title.font.family.clone())),
            base_suppressed: y_label.is_some_and(|l| !l.is_empty()) && hidden,
        }
    }

    pub(crate) fn layer<'a>(&'a self, ax_y: &'a crate::common::AxisStyle) -> &'a crate::common::AxisTitle {
        self.restored.as_ref().unwrap_or(&ax_y.title)
    }

    pub(crate) fn base<'a>(&'a self, ax_y: &'a crate::common::AxisStyle) -> &'a crate::common::AxisTitle {
        if self.base_suppressed { &ax_y.title } else { self.layer(ax_y) }
    }
}

/// Rows sharing a category (every NaN position is one) stack in row order (a bar layer's `y` keeps
/// Vega-Lite's default `stack: zero`, with no series to order by): each row's
/// `(category, start, end)`.
pub(crate) fn row_order_stack<'a>(
    rows: impl IntoIterator<Item = &'a serde_json::Map<String, Value>>,
    cat_field: &str,
    y: &str,
    nan: IsNan,
) -> Vec<(String, f64, f64)> {
    let mut stacks: HashMap<Option<String>, crate::common::StackZero> = HashMap::new();
    rows.into_iter()
        .filter_map(|row| {
            let cat = row_str(row, cat_field);
            // Vega groups every NaN position into one pile.
            let pile = (!nan(&cat)).then(|| cat.clone());
            let (start, end) = stacks.entry(pile).or_insert_with(|| crate::common::StackZero::new(0.0)).push(row_num(row, y)?);
            Some((cat, start, end))
        })
        .collect()
}

/// Whether an x text is Vega's NaN position (a year below 100 over a calendar grain).
pub(crate) type IsNan<'a> = &'a dyn Fn(&str) -> bool;

/// For a chart whose x has no NaN position.
pub(crate) fn never_nan(_: &str) -> bool {
    false
}

/// Whether the shared value scale pins a domain that excludes the 0 baseline a bar
/// or area mark paints from (`_baseline_outside_shared_domain`): only a pinned edge
/// counts, an unpinned one fits the marks.
pub(crate) fn baseline_outside_shared_domain(ax_y: &crate::common::AxisStyle) -> bool {
    let (lo, hi) = match ax_y.authored_continuous_domain() {
        Some((a, b)) => (Some(a.min(b)), Some(a.max(b))),
        None => (ax_y.domain_min, ax_y.domain_max),
    };
    lo.is_some_and(|lo| lo > 0.0) || hi.is_some_and(|hi| hi < 0.0)
}

/// Whether a layer's marks paint under the plot clip (`clip_layer`, `_overlay.py`). A layer
/// over a `normalize` base clips when any of its values leaves the pinned domain; a bar or area
/// layer also clips on any scale pinned away from its 0 baseline. A layer with its own scale
/// takes neither rule.
pub(crate) fn layer_clip(
    kind: LayerType,
    y_field: &str,
    rows: &[serde_json::Map<String, Value>],
    ax_y: &crate::common::AxisStyle,
    normalize: bool,
    independent: bool,
) -> bool {
    if independent {
        return false;
    }
    if normalize {
        let (lo, hi) = ax_y.authored_continuous_domain().unwrap_or((0.0, 1.0));
        let (lo, hi) = (lo.min(hi), lo.max(hi));
        if rows.iter().filter_map(|row| row_num(row, y_field)).any(|v| !(lo..=hi).contains(&v)) {
            return true;
        }
    }
    matches!(kind, LayerType::Area | LayerType::Bar) && baseline_outside_shared_domain(ax_y)
}

/// A value's position, left unclamped when the layer paints under the plot clip.
pub(crate) fn clamp_unless_clipped(position: f64, extent: f64, clipped: bool) -> f64 {
    if clipped { position } else { position.clamp(0.0, extent) }
}

/// Wraps what `svg` gained since `from` in the plot clip (`clip: true` on a line or scatter layer).
pub(crate) fn clip_marks_from(svg: &mut String, from: usize, (plot_w, plot_h): (f64, f64)) {
    let id = format!("dbt-area-clip-{plot_w}-{plot_h}");
    let open = format!("<clipPath id=\"{id}\"><rect width=\"{plot_w}\" height=\"{plot_h}\"/></clipPath><g clip-path=\"url(#{id})\">");
    svg.insert_str(from, &open);
    svg.push_str("</g>");
}

/// Opens the plot-sized clip a layer's marks paint under (`clip: true`); close it
/// with `</g>`. The id names the plot box and carries the chart's namespace suffix,
/// as every id production mints does (`_namespace_svg_ids`).
pub(crate) fn clip_open(svg: &mut String, (plot_w, plot_h): (f64, f64), chart_id: &str) {
    let id = format!("dbt-layer-clip-{plot_w}-{plot_h}-{}", crate::contract::safe_svg_id(chart_id));
    // One definition per id: a second clipped layer of the same plot reuses it.
    if !svg.contains(&format!("<clipPath id=\"{id}\">")) {
        let _ = std::fmt::Write::write_fmt(
            svg,
            format_args!("<clipPath id=\"{id}\"><rect x=\"0\" y=\"0\" width=\"{plot_w}\" height=\"{plot_h}\"/></clipPath>"),
        );
    }
    let _ = std::fmt::Write::write_fmt(svg, format_args!("<g clip-path=\"url(#{id})\">"));
}

/// One rect of a bar layer: spanning `from..to` on the value scale at its row's x.
#[derive(Clone, Copy)]
pub(crate) struct BarLayerMark<'a> {
    pub from: f64,
    pub to: f64,
    pub value: f64,
    pub row: &'a serde_json::Map<String, Value>,
}

/// A bar layer's rects. A ranged layer spans its two columns; a colorless one
/// stacks rows sharing an x in row order; one grouped by its own color stacks its
/// series in Vega-Lite's descending color order (no `order` encoding reaches a
/// layer). A row whose value is missing refuses by name, except a grouped
/// layer's, which has no segment.
pub(crate) fn bar_layer_marks<'a>(
    bl: &OverlayBarLayer,
    rows: &'a [serde_json::Map<String, Value>],
    x_field: &str,
    nan: IsNan,
) -> Vec<BarLayerMark<'a>> {
    if let Some(start) = bl.y_start.as_deref() {
        return rows
            .iter()
            .filter_map(|row| {
                let value = row_num(row, &bl.y)?;
                Some(BarLayerMark { from: row_num(row, start)?, to: value, value, row })
            })
            .collect();
    }
    let spans: Vec<Option<(f64, f64)>> = match bl.color.as_deref() {
        Some(color) => {
            let any_null = rows.iter().any(|row| layer_color_of(row, color).is_none());
            let order = layer_stack_order(&layer_color_values(rows, color), any_null);
            LayerStack::new(rows, x_field, color, &bl.y, &order).of_rows(rows)
        }
        None => {
            let mut stacked = row_order_stack(rows, x_field, &bl.y, nan).into_iter();
            rows.iter()
                .map(|row| row_num(row, &bl.y).and_then(|_| stacked.next()).map(|(_, from, to)| (from, to)))
                .collect()
        }
    };
    rows.iter()
        .zip(spans)
        .filter_map(|(row, span)| {
            let (from, to) = span?;
            Some(BarLayerMark { from, to, value: row_num(row, &bl.y)?, row })
        })
        .collect()
}

/// Each layer axis's own grid, over the base axis's theme style: the rungs of its
/// own ladder, rounded like the base's.
pub(crate) fn paint_layer_grids(
    svg: &mut String,
    layer_axes: &[LayerAxis],
    ax_y: &crate::common::AxisStyle,
    (plot_w, plot_h): (f64, f64),
    family: &str,
) -> Result<(), String> {
    for la in layer_axes.iter().filter(|la| la.grid_visible) {
        let gc = crate::common::required_ink(ax_y.grid.color.as_deref(), family, "axis_y.grid.color")?;
        let gw = ax_y.grid.width.unwrap_or(1.0);
        let go = ax_y.grid.opacity.unwrap_or(1.0);
        let scale = la.scale(plot_h);
        for &tv in &la.tick_values {
            let gy = scale.position(tv).round() + 0.5;
            let _ = std::fmt::Write::write_fmt(
                svg,
                format_args!(
                    "<line x1=\"0.5\" y1=\"{gy:.3}\" x2=\"{:.3}\" y2=\"{gy:.3}\" \
                     stroke=\"{}\" stroke-width=\"{gw}\" opacity=\"{go}\"/>",
                    plot_w + 0.5,
                    xml_escape_attr(gc)
                ),
            );
        }
    }
    Ok(())
}

/// A bar layer's rects over the rows `placed` gives a position on the base's x
/// scale: Vega drops a mark whose x is null or off the scale's domain.
pub(crate) fn placed_bar_layer_marks<'a>(
    bl: &OverlayBarLayer,
    rows: &'a [serde_json::Map<String, Value>],
    x_field: &str,
    nan: IsNan,
    placed: impl Fn(&serde_json::Map<String, Value>) -> bool,
) -> Vec<BarLayerMark<'a>> {
    bar_layer_marks(bl, rows, x_field, nan).into_iter().filter(|m| placed(m.row)).collect()
}

/// The value extent the layers on a shared scale add to it: a bar from its
/// baseline, an area at its pile, a line or scatter at its values.
pub(crate) fn layers_value_extent(
    layers: &[(&OverlayLayer, &[serde_json::Map<String, Value>])],
    base_x: &str,
    nan: IsNan,
) -> (f64, f64) {
    let mut extent = (f64::INFINITY, f64::NEG_INFINITY);
    let mut reach = |v: f64| extent = (extent.0.min(v), extent.1.max(v));
    for &(overlay, layer_rows) in layers {
        let layer_x = overlay.x().unwrap_or(base_x);
        match overlay {
            OverlayLayer::Bar(bl) => {
                for m in bar_layer_marks(bl, layer_rows, layer_x, nan) {
                    reach(m.from);
                    reach(m.to);
                }
            }
            OverlayLayer::Line(l) if l.area_mark.is_some() => {
                let piles: Vec<Option<(f64, f64)>> = match l.color.as_deref() {
                    Some(cf) => LayerStack::descending(layer_rows, layer_x, cf, &l.y).of_rows(layer_rows),
                    None => row_order_stack(layer_rows, layer_x, &l.y, &never_nan)
                        .into_iter()
                        .map(|(_, from, to)| Some((from, to)))
                        .collect(),
                };
                piles.into_iter().flatten().for_each(|(from, to)| {
                    reach(from);
                    reach(to);
                });
            }
            _ => layer_rows.iter().filter_map(|row| row_num(row, overlay.y())).for_each(&mut reach),
        }
    }
    extent
}

/// A layer on its own value scale (`YSides`). A bar or area mark paints from the
/// value-0 baseline, so its scale always carries the zero rule (`_overlay.py`'s
/// `layer_should_fire`).
pub(crate) struct ScaledLayer<'a> {
    pub side: LayerSide,
    pub layer_type: LayerType,
    /// The layer's x column: its own, else the base's.
    pub x: &'a str,
    pub y: &'a str,
    pub y_start: Option<&'a str>,
    pub axis_y: &'a OverlayAxisY,
    pub label: Option<&'a str>,
    pub rows: &'a [serde_json::Map<String, Value>],
}

/// Everything a pinned layer's own value axis is measured from: the base's
/// axis style (the layer inherits its template), the value-axis font, and
/// the title style the layer's title wears.
pub(crate) struct LayerAxisFrame<'a> {
    pub family: &'a str,
    pub ax_y: &'a crate::common::AxisStyle,
    pub fonts: &'a FontBook,
    pub font_family: &'a str,
    pub font_size: f64,
    pub label_limit: f64,
    pub tick_size: f64,
    pub title_style: &'a crate::common::AxisTitle,
    pub field_labels: &'a crate::common::FieldLabels,
}

impl<'a> ScaledLayer<'a> {
    /// `layer` on its own scale at `side`, reading `rows`.
    pub(crate) fn new(
        layer: &'a OverlayLayer,
        rows: &'a [serde_json::Map<String, Value>],
        base_x: &'a str,
        side: LayerSide,
    ) -> Self {
        ScaledLayer {
            side,
            layer_type: layer.layer_type(),
            x: layer.x().unwrap_or(base_x),
            y: layer.y(),
            y_start: match layer {
                OverlayLayer::Bar(l) => l.y_start.as_deref(),
                OverlayLayer::Line(_) | OverlayLayer::Scatter(_) => None,
            },
            axis_y: layer.axis_y(),
            label: layer.label(),
            rows,
        }
    }

    pub(crate) fn always_rules_zero(&self) -> bool {
        matches!(self.layer_type, LayerType::Area | LayerType::Bar)
    }

    /// The layer's authored `grid.visible`, else the base's.
    fn grid_visible(&self, ax_y: &crate::common::AxisStyle) -> bool {
        let own = self.axis_y.grid.as_ref().and_then(|g| g.visible);
        own.or(ax_y.grid.visible).unwrap_or(false)
    }

    /// The `(min, max)` the layer's marks reach on its own scale over `rows`:
    /// an area's reach is its pile (rows sharing an x stack), anything else's
    /// its values as they stand.
    pub(crate) fn value_extent<'b>(&self, rows: impl Iterator<Item = &'b serde_json::Map<String, Value>>) -> (f64, f64) {
        let reaches: Vec<f64> = if self.layer_type == LayerType::Area {
            row_order_stack(rows, self.x, self.y, &never_nan).into_iter().flat_map(|(_, start, end)| [start, end]).collect()
        } else {
            rows.filter_map(|row| row_num(row, self.y)).collect()
        };
        reaches.into_iter().fold((0.0_f64, 0.0_f64), |(lo, hi), v| (lo.min(v), hi.max(v)))
    }

    /// The stroke width of the layer's own zero rule (`_overlay.py` nests it
    /// into the layer's entry): a bar or area paints from zero, so its scale
    /// always carries one; a line or scatter only where its values reach
    /// zero. `None` when the grid or its threshold is hidden, and for a layer on
    /// the base's own side, which relies on the base's rule.
    pub(crate) fn zero_rule_width(&self, ax_y: &crate::common::AxisStyle, base_side: LayerSide) -> Option<f64> {
        if self.side == base_side {
            return None;
        }
        let values: Vec<f64> = self.rows.iter().filter_map(|row| row_num(row, self.y)).collect();
        let lo = values.iter().copied().fold(f64::INFINITY, f64::min);
        let hi = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let fires = self.always_rules_zero() || (lo <= 0.0 && 0.0 <= hi);
        if !fires || self.rows.is_empty() || !self.grid_visible(ax_y) || !ax_y.grid.threshold_visible() {
            return None;
        }
        ax_y.grid.threshold.as_ref().map(|z| z.width.unwrap_or(2.0))
    }

    /// The layer's own scale, ladder, labels and title (`_overlay.py`'s
    /// dual-axis `layer_ay_vl`), from the `(min, max)` extent of its marks:
    /// the zero union with the layer's values only, never the base's, then
    /// d3's default nice count.
    pub(crate) fn axis(&self, extent: (f64, f64), frame: &LayerAxisFrame) -> Result<LayerAxis, String> {
        let layer_count = self.axis_y.ticks.as_ref().and_then(|t| t.count);
        self.axis_at(extent, frame, layer_count.or(frame.ax_y.ticks.count).unwrap_or(6.0).max(1.0) as usize)
    }

    /// `axis` with the ladder's tick `count` given: the layer's own authored count, else
    /// the base axis's baked one, else wherever the caller reads Vega's own default.
    pub(crate) fn axis_at(&self, extent: (f64, f64), frame: &LayerAxisFrame, count: usize) -> Result<LayerAxis, String> {
        let LayerAxisFrame { family, ax_y, fonts, font_family, font_size, label_limit, tick_size, title_style, field_labels } =
            *frame;
        let (layer_min, layer_max) = (extent.0.min(0.0), extent.1.max(0.0));
        // An authored pair reaches Vega verbatim, in authored order; ticks fall inside it.
        let (domain_min, domain_max) = match self.axis_y.scale.as_ref().and_then(|scale| scale.domain) {
            Some(pair) => pair,
            None => d3rs::ticks::d3_nice(layer_min, layer_max, 10),
        };
        let tick_values = d3rs::ticks::d3_ticks(domain_min, domain_max, count);
        // No ruler, no `tick_label` bake — `_overlay.py`'s own comment on
        // its `ay_vl = axis_to_vl(axis_y)` seed: "axis_y is the BASE
        // chart's measure axis, and its ruler (if any) was baked for the
        // base's own tick ladder ... applying the base's ruler to it would
        // paint the wrong magnitude". The layer reads only the base's
        // plain `labels.format` literal (probed: `dual-y-axis-lab`'s base
        // carries an anchor-mode dollar ruler — reusing it on the layer's
        // 0..8 conversion values mangled every label), and when the base
        // bakes a ruler or `tick_label` that ladder owns the affix, so the
        // layer gets the literal's bare spec (`axis_to_vl`'s
        // `vl_format_key(fmt.spec)` arm). An authored layer `labels.format`
        // is the one override the layer's axis dict carries (its own affix
        // on every non-zero tick).
        let own_format = self.axis_y.labels.as_ref().and_then(|lb| lb.format.as_ref());
        let base_bare = own_format.is_none() && (ax_y.ruler.is_some() || ax_y.tick_label.is_some());
        let layer_format = own_format.or(ax_y.labels.format.as_ref());
        let tick_labels = tick_values
            .iter()
            .enumerate()
            .map(|(i, &tv)| match layer_format {
                Some(slot) if base_bare => crate::number_locale::d3_format(&slot.spec, tv)
                    .map_err(|e| format!("{family}: layer axis format {:?}: {e}", slot.spec)),
                _ => crate::common::format_y_label(
                    tv,
                    None,
                    self.axis_y.tick_label.as_ref(),
                    layer_format,
                    i,
                    tick_values.len(),
                ),
            })
            .collect::<Result<Vec<_>, _>>()?;
        // The layer axis is the bare `axis_to_vl(axis_y)` template: it never
        // routes through `measure_axis_to_vl`'s own-side gutter, so its
        // `labelPadding` stays the theme's flat value and the gutter is the
        // real label width plus that padding.
        let label_ws = tick_labels
            .iter()
            .map(|s| fonts.measure_shaped(s, font_family, font_size))
            .collect::<Result<Vec<_>, _>>()?;
        let widest = label_ws.iter().copied().fold(0.0_f64, f64::max);
        let label_offset = tick_size + ax_y.labels.padding.unwrap_or(6.0);
        let gutter = label_offset + widest.min(label_limit);
        let title_text = match self.axis_y.title.clone() {
            Some(title) => title,
            None => crate::common::axis_title_text(self.y, self.label, field_labels)?,
        };
        let title_extra = crate::common::y_title_extra(title_style, &title_text);
        let caption = AxisCaption::new(
            &title_text,
            title_style,
            CaptionScale::Numeric {
                kind: NumericScale::Linear,
                domain: (domain_min, domain_max),
                // An authored layer format is the layer encoding's own `format`, which the axis description reads
                // whatever the affix.
                format: layer_format.map_or(Ok(None), |slot| slot_format(slot, base_bare || own_format.is_some(), true))?,
            },
        );
        let own_scale = self.axis_y.scale.as_ref().filter(|scale| scale.domain.is_some());
        let cut = match own_scale {
            Some(scale) if self.rows.iter().filter_map(|row| row_num(row, self.y)).any(|v| {
                let (lo, hi) = scale.domain.expect("filtered to an authored domain");
                !(lo.min(hi)..=lo.max(hi)).contains(&v)
            }) =>
            {
                if scale.clip { DomainCut::Clip } else { DomainCut::Overrun }
            }
            _ => DomainCut::Within,
        };
        Ok(LayerAxis {
            cut,
            side: self.side,
            domain_min,
            domain_max,
            value_max: layer_max,
            tick_values,
            tick_labels,
            label_ws,
            label_offset,
            gutter,
            grid_visible: self.grid_visible(ax_y),
            title_text,
            caption,
            title_extra,
        })
    }
}

/// How a pinned layer's axis inks: the value-axis font and ticks it
/// inherits from the base, and its own title.
pub(crate) struct LayerAxisInk<'a> {
    pub font_family: &'a str,
    pub font_size: f64,
    pub font_weight: &'a str,
    pub font_color: &'a str,
    pub tick_size: f64,
    pub ticks: &'a crate::common::AxisTicks,
    pub title_style: &'a crate::common::AxisTitle,
}

/// Paints the pinned layer's own value axis on the side it pins: ticks and
/// labels per rung, then the rotated title past the widest label. `edge_x` is
/// the axis edge (the plot's right or left side plus its offset and Vega's
/// 0.5 crisp shift) and `top`/`bottom` the plot's extent in the frame the
/// axis is emitted in. A right axis start-anchors its labels at the theme
/// padding past the edge (and a visible tick's length), a left one
/// end-anchors them — never the base's flip-aligned gutter law. An empty
/// title draws nothing.
pub(crate) fn paint_layer_axis(
    svg: &mut String,
    la: &LayerAxis,
    scale: &crate::families::cartesian::LinearScale,
    edge_x: f64,
    (top, bottom): (f64, f64),
    ink: &LayerAxisInk,
    title_key: Option<crate::contract::LabelKey>,
) -> Result<(), String> {
    let (outward, anchor) = match la.side {
        LayerSide::Right => (1.0, "start"),
        LayerSide::Left => (-1.0, "end"),
    };
    let label_x = edge_x + outward * la.label_offset;
    let title_x = edge_x + outward * la.gutter;
    crate::contract::axis_group_open(svg, crate::contract::AxisChannel::Y, &la.caption, (edge_x, top))?;
    for (&tv, label) in la.tick_values.iter().zip(&la.tick_labels) {
        let tick_y = top + scale.position(tv) + 0.5;
        if tick_y < top - 1.0 || tick_y > bottom + 1.0 {
            continue;
        }
        crate::common::emit_value_ticks(
            svg,
            ink.ticks,
            (ink.tick_size > 0.0).then(|| crate::common::tick_row(top, scale.position(tv))),
            edge_x,
            outward,
            ink.tick_size,
        )?;
        let y = tick_y + crate::measure::baseline_shift("middle", ink.font_size);
        let _ = std::fmt::Write::write_fmt(
            svg,
            format_args!(
                "<text x=\"{label_x:.2}\" y=\"{y:.2}\" text-anchor=\"{anchor}\" \
                 font-family=\"{}\" font-size=\"{}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
                crate::svg::vega_escape_attr(ink.font_family),
                ink.font_size,
                xml_escape_attr(ink.font_weight),
                xml_escape_attr(ink.font_color),
                crate::svg::xml_escape(label),
            ),
        );
    }
    crate::common::emit_axis_title_rotated(
        svg,
        &la.title_text,
        ink.title_style,
        title_x,
        top + (bottom - top) / 2.0,
        outward,
        title_key,
    );
    crate::contract::axis_group_close(svg);
    Ok(())
}

/// A categorical x domain widened by bar-type layers, as Vega-Lite renders it
/// (`x_domain.py::rendered_x_domain`).
pub(crate) struct XUnion {
    pub domain: Vec<String>,
    /// The layer-contributed values painted after a base that states no order
    /// to place them into, when two or more datasets contributed.
    pub paint_order: Option<Vec<Value>>,
}

static LEX_SORTABLE_DATE_LIKE: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    [
        r"^\d{4}-Q[1-4]$",
        r"^\d{4}Q[1-4]$",
        r"^\d{4}-\d{2}$",
        r"^FY\d{4}$",
        r"^\d{4}-H[12]$",
        r"^\d{4}-W\d{2}$",
        r"^\d{4}-\d{2}-\d{2}$",
        r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$",
    ]
    .iter()
    .map(|p| Regex::new(&format!("(?i){p}")).expect("a literal pattern compiles"))
    .collect()
});

/// `defined_domain_order`: `values` in their one defined ascending order, or
/// `None` when they have none. Two rules count — every value is a date-like
/// bucket whose string order is chronological, or every value is a number —
/// and anything else (month names, `low/medium/high`) states an order the data
/// does not.
fn defined_domain_order(values: &[String]) -> Option<Vec<String>> {
    if values.is_empty() {
        return None;
    }
    if values.iter().all(|v| LEX_SORTABLE_DATE_LIKE.iter().any(|re| re.is_match(v))) {
        let mut sorted = values.to_vec();
        sorted.sort();
        return Some(sorted);
    }
    let mut numeric: Vec<(f64, &String)> = Vec::with_capacity(values.len());
    for v in values {
        numeric.push((v.trim().parse::<f64>().ok().filter(|n| n.is_finite())?, v));
    }
    numeric.sort_by(|a, b| a.0.partial_cmp(&b.0).expect("finite numbers compare"));
    Some(numeric.into_iter().map(|(_, v)| v.clone()).collect())
}

/// `extend_domain_in_base_order`: `base + extra` in the order `base` itself
/// states, ascending or descending, or `None` when it states none.
fn extend_domain_in_base_order(base: &[String], extra: &[String]) -> Option<Vec<String>> {
    let ordered = defined_domain_order(base)?;
    let ascending = if base == ordered.as_slice() {
        true
    } else if base.iter().eq(ordered.iter().rev()) {
        false
    } else {
        return None;
    };
    let all: Vec<String> = base.iter().chain(extra).cloned().collect();
    let mut merged = defined_domain_order(&all)?;
    if !ascending {
        merged.reverse();
    }
    Some(merged)
}

/// The x domain after each layer's own x values join `base_domain`:
/// `base_domain` unchanged when no layer adds a category, the layer-only values
/// placed into the order the base states when it states one, else the union in
/// paint order across the independently-ordered result sets.
pub(crate) fn union_x_domain(
    base_domain: &[String],
    layers: &[(&[serde_json::Map<String, Value>], &str)],
    sorted: bool,
) -> XUnion {
    let mut union: Vec<String> = base_domain.to_vec();
    let mut seen: HashSet<String> = union.iter().cloned().collect();
    let mut layer_only: Vec<Value> = Vec::new();
    let mut layer_only_keys: Vec<String> = Vec::new();
    let mut contributors = usize::from(!base_domain.is_empty());
    for (rows, x_field) in layers {
        let mut added = false;
        for row in *rows {
            let Some(value) = row.get(*x_field).filter(|v| !v.is_null()) else { continue };
            let key = row_str(row, x_field);
            if seen.insert(key.clone()) {
                union.push(key.clone());
                layer_only_keys.push(key);
                layer_only.push(value.clone());
                added = true;
            }
        }
        contributors += usize::from(added);
    }
    // An authored field sort tails the layer-only values after the sorted base
    // and never earns the warning.
    if layer_only.is_empty() || sorted {
        return XUnion { domain: union, paint_order: None };
    }
    if let Some(domain) = extend_domain_in_base_order(base_domain, &layer_only_keys) {
        return XUnion { domain, paint_order: None };
    }
    XUnion { domain: union, paint_order: (contributors > 1).then_some(layer_only) }
}

#[cfg(test)]
mod tests {

    /// A band-step layer owns its doubled rows: whole in every cell, whichever
    /// query it reads; any other facet layer on the base query keeps its slice.
    #[test]
    fn facet_layer_rows_scope_is_the_one_law() {
        use super::{layer_rows_scope as scope, LayerRows::*};
        assert!(scope(true, false, Some("q"), Some("q")) == Panel);
        assert!(scope(true, true, Some("q"), Some("q")) == Chart);
        assert!(scope(true, true, Some("other"), Some("q")) == Dataset);
        assert!(scope(true, false, Some("other"), Some("q")) == Dataset);
        assert!(scope(false, true, Some("q"), Some("q")) == Dataset);
    }
    use super::*;

    fn bar_layer(color: Option<&str>) -> OverlayBarLayer {
        OverlayBarLayer::parse(
            &serde_json::json!({
                "type": "bar",
                "bar_mark": {
                    "border": {"width": 1.0, "color": "#FFFFFF", "radius": 2.0},
                    "band_width": 0.8,
                    "labels": {"visible": false, "position": "middle", "field": null, "format": null, "dx": null, "dy": null, "font": null}
                },
                "axis_y": {"position": null},
                "x": "x", "y": "y", "label": null, "color": color, "query_name": null,
            }),
            "test",
        )
        .expect("a bar layer")
    }

    fn layer_rows(values: &[(&str, Option<f64>)]) -> Vec<serde_json::Map<String, Value>> {
        values
            .iter()
            .map(|(x, y)| serde_json::json!({"x": x, "y": y}).as_object().expect("an object").clone())
            .collect()
    }

    /// A row with no value draws no bar and takes no place in the stack: the
    /// marks of the rows after it keep their own spans.
    #[test]
    fn a_bar_layer_row_with_no_value_leaves_the_rows_after_it_their_own_spans() {
        let rows = layer_rows(&[("a", None), ("a", Some(5.0)), ("b", Some(7.0))]);
        let marks = bar_layer_marks(&bar_layer(None), &rows, "x", &never_nan);
        let spans: Vec<(f64, f64)> = marks.iter().map(|m| (m.from, m.to)).collect();
        assert_eq!(spans, [(0.0, 5.0), (0.0, 7.0)]);
        assert_eq!(marks[1].row["x"], "b");
    }

    /// A gap ends the connected path at its band; one at either end leaves no
    /// empty run.
    #[test]
    fn runs_cut_before_splits_at_each_gap_and_never_leaves_an_empty_run() {
        let pairs = [[1, 2], [3, 4], [5, 6], [7, 8]];
        assert_eq!(runs_cut_before(&pairs, &[]), vec![vec![1, 2, 3, 4, 5, 6, 7, 8]]);
        assert_eq!(runs_cut_before(&pairs, &[0, 2, 2, 4]), vec![vec![1, 2, 3, 4], vec![5, 6, 7, 8]]);
        assert_eq!(runs_cut_before(&pairs[..0], &[0]), vec![Vec::<i32>::new()]);
    }

    fn axis(side: LayerSide) -> LayerAxis {
        LayerAxis {
            side,
            domain_min: 0.0,
            domain_max: 10.0,
            value_max: 10.0,
            tick_values: vec![],
            tick_labels: vec![],
            label_ws: vec![],
            label_offset: 6.0,
            gutter: 20.0,
            grid_visible: false,
            title_text: String::new(),
            caption: AxisCaption {
                bare: false,
                title: None,
                scale: CaptionScale::Numeric { kind: NumericScale::Linear, domain: (0.0, 10.0), format: None },
            },
            title_extra: 15.0,
            cut: DomainCut::Within,
        }
    }

    fn scatter_at(position: Option<&str>) -> OverlayLayer {
        OverlayLayer::Scatter(Box::new(
            OverlayScatterLayer::parse(
                &serde_json::json!({
                    "type": "scatter",
                    "point_mark": {"size": 50.0, "stroke_width": 1.0, "labels": {"visible": false}},
                    "axis_y": {"position": position},
                    "x": null, "y": "y", "label": null, "color": null, "query_name": null,
                }),
                "test",
            )
            .expect("a scatter layer"),
        ))
    }

    /// `_resolved_layer_y_orients`: no pin shares the base's scale; a pin puts every
    /// layer on its own scale, the pinned ones on their side and the rest, with the
    /// base, on the free one (left, unless every pin is on the left).
    #[test]
    fn a_pinned_layer_gives_every_layer_a_scale_on_its_side_or_the_free_one() {
        assert!(YSides::of(&[scatter_at(None), scatter_at(None)]).is_none());
        let right = YSides::of(&[scatter_at(None), scatter_at(Some("right"))]).expect("a pin");
        assert_eq!((right.base, right.layers), (LayerSide::Left, vec![LayerSide::Left, LayerSide::Right]));
        let left = YSides::of(&[scatter_at(Some("left")), scatter_at(None)]).expect("a pin");
        assert_eq!((left.base, left.layers), (LayerSide::Left, vec![LayerSide::Left, LayerSide::Right]));
        let all_left = YSides::of(&[scatter_at(Some("left")), scatter_at(Some("left"))]).expect("pins");
        assert_eq!((all_left.base, all_left.layers), (LayerSide::Right, vec![LayerSide::Left, LayerSide::Left]));
        let both = YSides::of(&[scatter_at(Some("right")), scatter_at(Some("left")), scatter_at(None)]).expect("pins");
        assert_eq!(
            (both.base, both.layers),
            (LayerSide::Left, vec![LayerSide::Right, LayerSide::Left, LayerSide::Left])
        );
    }

    /// A side legend anchors on the extent of every value axis: a right layer
    /// axis pushes the right edge past the plot, a left one the left edge.
    #[test]
    fn a_layer_axis_widens_the_extent_a_legend_anchors_on_by_its_own_side() {
        assert_eq!(axis(LayerSide::Right).widen_axes((-30.0, 400.0), 400.0, 0.0), (-30.0, 435.0));
        assert_eq!(axis(LayerSide::Left).widen_axes((-30.0, 400.0), 400.0, 0.0), (-35.0, 400.0));
        assert_eq!(axis(LayerSide::Right).widen_axes((-30.0, 500.0), 400.0, 0.0), (-30.0, 500.0));
    }

    /// An area's reach on its own scale is its pile (rows sharing an x stack);
    /// any other layer reaches its own values.
    #[test]
    fn a_pinned_area_reaches_its_pile_and_a_pinned_line_its_values() {
        let rows: Vec<serde_json::Map<String, Value>> = [("a", 90.0), ("a", 90.0), ("b", 40.0)]
            .iter()
            .map(|(x, y)| serde_json::json!({"x": x, "y": y}).as_object().unwrap().clone())
            .collect();
        let axis_y: OverlayAxisY = serde_json::from_value(serde_json::json!({"position": "right"})).unwrap();
        let pinned = |layer_type| ScaledLayer {
            side: LayerSide::Right,
            layer_type,
            x: "x",
            y: "y",
            y_start: None,
            axis_y: &axis_y,
            label: None,
            rows: &rows,
        };
        assert_eq!(pinned(LayerType::Area).value_extent(rows.iter()), (0.0, 180.0));
        assert_eq!(pinned(LayerType::Line).value_extent(rows.iter()), (0.0, 90.0));
    }

    /// Layers widen a domain with their own x columns: a value no earlier column
    /// holds joins at the end of an unordered base, and in order into an ordered one.
    #[test]
    fn a_layers_own_x_column_widens_the_domain_in_the_order_the_base_states() {
        let rows = |field: &str, values: &[&str]| -> Vec<serde_json::Map<String, Value>> {
            values.iter().map(|v| serde_json::json!({ field: v }).as_object().unwrap().clone()).collect()
        };
        let own = rows("other", &["2024-02", "2024-04"]);
        let base: Vec<String> = ["2024-01", "2024-03"].iter().map(ToString::to_string).collect();
        let union = union_x_domain(&base, &[(own.as_slice(), "other")], false);
        assert_eq!(union.domain, ["2024-01", "2024-02", "2024-03", "2024-04"]);
        assert!(union.paint_order.is_none());
        let names = rows("n", &["Central"]);
        let base: Vec<String> = ["North", "South"].iter().map(ToString::to_string).collect();
        let union = union_x_domain(&base, &[(names.as_slice(), "n")], false);
        assert_eq!(union.domain, ["North", "South", "Central"]);
        assert_eq!(union.paint_order.map(|order| order.len()), Some(1));
    }

    fn point_mark(filled: bool) -> OverlayPointMark {
        serde_json::from_value(serde_json::json!({
            "size": 64.0, "color": null, "filled": filled, "fill": null,
            "stroke_width": 2.0, "labels": {"visible": false},
        }))
        .unwrap()
    }

    #[test]
    fn an_overlay_point_is_placed_by_transform_and_carries_its_description() {
        let described = " data-dct-role=\"point\" aria-label=\"a: 1\" role=\"graphics-symbol\" aria-roledescription=\"point\"";
        for (filled, want) in [
            (true, format!("<circle transform=\"translate(10.000,20.000)\" r=\"4.000\" fill=\"#111\" opacity=\"1\"{described}/>")),
            (
                false,
                format!("<circle transform=\"translate(10.000,20.000)\" r=\"4.000\" fill=\"#fff\" stroke=\"#111\" stroke-width=\"2\" opacity=\"1\"{described}/>"),
            ),
        ] {
            let mut svg = String::new();
            paint_overlay_point(&mut svg, &point_mark(filled), 10.0, 20.0, 4.0, "#111", "#fff", Some("a: 1")).unwrap();
            assert_eq!(svg, want);
        }
    }

    fn rows(values: &[(&str, Option<&str>, f64)]) -> Vec<serde_json::Map<String, Value>> {
        values
            .iter()
            .map(|(x, color, y)| {
                serde_json::json!({"x": x, "c": color, "y": y}).as_object().expect("an object").clone()
            })
            .collect()
    }

    /// Vega's facet meets series in order of first appearance, and a row with no
    /// color value is a series of its own.
    #[test]
    fn color_groups_follow_first_appearance_and_a_null_is_its_own_group() {
        let data = rows(&[("n", Some("b"), 1.0), ("s", None, 2.0), ("e", Some("a"), 3.0), ("w", Some("b"), 4.0)]);
        let groups: Vec<(Option<String>, usize)> =
            layer_color_groups(&data, "c").into_iter().map(|(value, own)| (value, own.len())).collect();
        assert_eq!(groups, [(Some("b".into()), 2), (None, 1), (Some("a".into()), 1)]);
        assert_eq!(layer_color_values(&data, "c"), ["a", "b"], "the domain drops the null and sorts");
    }

    /// No `order` channel reaches a layer: color descending from the baseline, the
    /// first value on top, and no-color rows sorting lowest so they end on top.
    #[test]
    fn a_layer_stacks_its_color_descending_from_the_baseline_with_no_color_last() {
        let domain = ["a".to_string(), "b".to_string()];
        assert_eq!(layer_stack_order(&domain, false), [Some("b".to_string()), Some("a".to_string())]);
        assert_eq!(layer_stack_order(&domain, true), [Some("b".to_string()), Some("a".to_string()), None]);
    }

    #[test]
    fn an_area_stack_piles_the_series_present_at_each_x() {
        let data = rows(&[
            ("n", Some("a"), 40.0),
            ("n", Some("b"), 20.0),
            ("s", Some("a"), 50.0),
            ("e", Some("b"), 30.0),
            ("e", None, 5.0),
        ]);
        let stack = LayerStack::descending(&data, "x", "c", "y");
        let ends: Vec<(f64, f64)> = stack.of_rows(&data).into_iter().map(|span| span.expect("stacked")).collect();
        // b is the baseline series, a on top of it; where one is absent the other
        // starts at zero; a null-colored row sits above both.
        assert_eq!([ends[1], ends[0]], [(0.0, 20.0), (20.0, 60.0)]);
        assert_eq!(ends[2], (0.0, 50.0));
        assert_eq!([ends[3], ends[4]], [(0.0, 30.0), (30.0, 35.0)]);
    }

    /// Vega-Lite joins the titles of the encodings a merged legend holds: the
    /// base's explicit one outranks the layers' implicit field names, which a base
    /// with no color field of its own takes once each, in layer order.
    #[test]
    fn a_layered_legend_titles_itself_by_its_layers_color_fields_only_when_the_base_has_none() {
        let legend = |title_visible: bool| -> LegendConfig {
            serde_json::from_value(serde_json::json!({
                "visible": true, "position": {"edge": "right", "align": "start", "overlay": false},
                "direction": "vertical", "columns": null,
                "label": {"font": {"family": "x", "size": 11.0}, "padding": 8.0},
                "title": {"font": {"family": "x"}, "visible": title_visible},
            }))
            .unwrap()
        };
        let title = |base_has_color: bool, fields: &[&'static str], visible: bool| {
            merged_legend_title("Base".to_string(), base_has_color, fields.iter().copied(), &legend(visible))
        };
        assert_eq!(title(false, &["seg", "region", "seg"], true), "seg, region");
        assert_eq!(title(true, &["seg"], true), "Base");
        assert_eq!(title(false, &[], true), "Base");
        assert_eq!(title(false, &["seg"], false), "Base", "a hidden title stays the base's own empty one");
    }

    fn numeric_rows(values: &[Value]) -> Vec<serde_json::Map<String, Value>> {
        values
            .iter()
            .enumerate()
            .map(|(i, v)| {
                let mut row = serde_json::Map::new();
                row.insert("x".into(), Value::String(format!("x{i}")));
                row.insert("y".into(), Value::from(10.0 * (i as f64 + 1.0)));
                row.insert("c".into(), v.clone());
                row
            })
            .collect()
    }

    /// Vega-Lite types a color field from its first 10 rows: numbers and nulls are
    /// quantitative, a string anywhere in them nominal, no value at all untyped.
    #[test]
    fn a_layer_color_is_numeric_when_its_first_ten_rows_hold_only_numbers_and_nulls() {
        let n = |v: f64| Value::from(v);
        let numeric = |rows: &[Value]| numeric_layer_color(&numeric_rows(rows), "c").is_some();
        assert!(numeric(&[n(1.0), n(2.0)]));
        assert!(numeric(&[n(1.0), Value::Null, n(2.0)]));
        assert!(!numeric(&[n(1.0), Value::String("a".into())]));
        assert!(!numeric(&[Value::Null, Value::Null]));
        let mut late_string = vec![n(1.0); 10];
        late_string.push(Value::String("a".into()));
        assert!(numeric(&late_string), "only the first ten rows type the field");
    }

    /// A numeric color's series each hold a zero-thickness cell at every x they have no
    /// row at, stacked from the baseline in descending value order; a nominal color's
    /// series draw only their own rows.
    #[test]
    fn a_numeric_color_imputes_zero_cells_and_stacks_its_largest_value_first() {
        let data = numeric_rows(&[Value::from(1.0), Value::from(2.0)]);
        let stack = LayerStack::descending(&data, "x", "c", "y");
        // Value 2 is the baseline series: at x0, where only 1 has a row, it is a zero cell at 0.
        assert_eq!(stack.imputed_cells_for(&data[1..]), [("x0", (0.0, 0.0))]);
        // Value 1 stacks on it: at x1 it is a zero cell on top of the 20.
        assert_eq!(stack.imputed_cells_for(&data[..1]), [("x1", (20.0, 20.0))]);
        let nominal = numeric_rows(&[Value::from("a"), Value::from("b")]);
        assert!(LayerStack::descending(&nominal, "x", "c", "y").imputed_cells_for(&nominal[..1]).is_empty());
    }

    /// A horizontal chart reads the stack along its measure axis: the first value of the
    /// domain sits at the baseline.
    #[test]
    fn an_ascending_stack_starts_the_first_value_at_the_baseline() {
        let data = rows(&[("n", Some("a"), 40.0), ("n", Some("b"), 20.0)]);
        let ends: Vec<(f64, f64)> = LayerStack::ascending(&data, "x", "c", "y")
            .of_rows(&data)
            .into_iter()
            .map(|span| span.expect("stacked"))
            .collect();
        assert_eq!(ends, [(0.0, 40.0), (40.0, 60.0)]);
    }

    /// A row with no value draws no segment and takes no place in its cell's order.
    #[test]
    fn a_row_with_no_value_takes_no_place_on_the_stack() {
        let mut data = rows(&[("n", Some("a"), 0.0), ("n", Some("a"), 30.0)]);
        data[0].insert("y".to_string(), Value::Null);
        let ends = LayerStack::descending(&data, "x", "c", "y").of_rows(&data);
        assert_eq!(ends, [None, Some((0.0, 30.0))]);
    }

    /// A series with no color value strokes nothing, so it adds no stroke width to the
    /// scope group's bound unless the layer authors one stroke over every series.
    #[test]
    fn a_series_with_no_color_value_reaches_the_bounds_without_a_stroke() {
        let layer = |stroke_color: Option<&str>| {
            OverlayLineLayer::parse(
                &serde_json::json!({
                    "type": "line",
                    "line_mark": {"stroke": {"width": 4.0, "color": stroke_color}, "labels": {"visible": false}},
                    "point_mark": {"stroke_width": 2.0, "labels": {"visible": false}},
                    "axis_y": {"position": null}, "x": null, "y": "y", "label": null, "color": "c", "query_name": null
                }),
                "test",
            )
            .expect("a line layer")
        };
        let data = rows(&[("n", None, 110.0), ("s", Some("a"), 50.0)]);
        let top = |l: &OverlayLineLayer| scope_group_top(l, &data, "x", false, |v| 100.0 - v);
        assert_eq!(top(&layer(None)), -10.0, "the null series' top is its datum");
        assert_eq!(top(&layer(Some("#cc0000"))), -14.0, "an authored stroke paints it too");
        let colored = rows(&[("n", Some("a"), 110.0), ("s", Some("b"), 50.0)]);
        assert_eq!(scope_group_top(&layer(None), &colored, "x", false, |v| 100.0 - v), -14.0);
    }

    /// Rows sharing an x and a color value stack in row order, as bar segments do.
    #[test]
    fn two_rows_of_one_series_at_one_x_stack_in_row_order() {
        let data = rows(&[("n", Some("a"), 40.0), ("n", Some("a"), 20.0), ("n", Some("b"), 5.0)]);
        let ends = LayerStack::descending(&data, "x", "c", "y").of_rows(&data);
        assert_eq!(ends, [Some((5.0, 45.0)), Some((45.0, 65.0)), Some((0.0, 5.0))]);
    }

    #[test]
    fn merged_axis_title_joins_each_distinct_title_once_in_layer_order() {
        let titles = ["y", "z", "y", "w"].map(String::from);
        assert_eq!(merged_axis_title(titles), "y, z, w");
        assert_eq!(merged_axis_title(["y".to_string()]), "y");
    }
}
