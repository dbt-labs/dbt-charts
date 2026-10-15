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
//! types/helpers here (`OverlayBarLayer`, `union_cat_domain`,
//! `column_is_nominal`) — only `bar.rs` calls them, but they live alongside
//! the two-caller machinery rather than in `bar.rs` itself. A layer pinned to
//! a side of its own (`PinnedLayer`, `LayerAxis`) is also shared: both
//! families measure, fit and paint its independent axis through this module.

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

use regex::Regex;
use serde::Deserialize;
use serde_json::Value;

use crate::diagnostic::Diagnostic;
use crate::common::{row_num, row_str, FormatSlot, MarkLabels};
use crate::families::legend::{LegendEntry, LegendSymbol};
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

/// A layer's own right axis paints its `labels.format` affix
/// (`_overlay.py:1583`); every other layer format affix, and an authored
/// layer `scale.domain`, reaches no painter here and refuses by name instead
/// of vanishing.
pub(crate) fn refuse_unpainted_layer_affix(family: &str, raw: &Value) -> Result<(), String> {
    #[derive(Deserialize)]
    struct Labels {
        #[serde(default)]
        format: Option<FormatSlot>,
    }
    #[derive(Deserialize)]
    struct Axis {
        #[serde(default)]
        position: Option<String>,
        #[serde(default)]
        scale: Option<Value>,
        #[serde(default)]
        ticks: Option<Value>,
        #[serde(default)]
        grid: Option<Value>,
        #[serde(default)]
        labels: Option<Labels>,
        #[serde(default)]
        tick_label: Option<Value>,
        #[serde(default)]
        tooltip_format: Option<FormatSlot>,
    }
    #[derive(Deserialize)]
    struct Layer {
        #[serde(default)]
        axis_y: Option<Axis>,
    }
    let layer: Layer = serde_json::from_value(raw.clone()).map_err(|e| format!("{family}: layer payload: {e}"))?;
    let Some(axis) = layer.axis_y else { return Ok(()) };
    if axis.scale.as_ref().and_then(|scale| scale.get("domain")).is_some_and(|domain| !domain.is_null()) {
        return Err(format!(
            "{family}: layer axis_y.scale.domain is not painted: a layer's authored domain, and the plot clip \
             (or `clip: false`) that goes with it, have no layer law here"
        ));
    }
    // A pinned layer's own axis applies these (`_overlay.py`); the dual-axis
    // painter derives its domain, ladder and grid from the data alone.
    if axis.position.is_some() {
        let authored = |slot: &Option<Value>, key: &str| slot.as_ref().is_some_and(|v| !v[key].is_null());
        for (slot, section, key) in [(&axis.ticks, "ticks", "count"), (&axis.grid, "grid", "visible")] {
            if authored(slot, key) {
                return Err(format!(
                    "{family}: a pinned layer's authored axis_y.{section}.{key} is not supported: its own axis is derived from the data"
                ));
            }
        }
    }
    // A layer's own right axis paints its `labels.format` affix on every
    // non-zero tick (`format_y_label` through `FormatSlot::paint_tick`,
    // `compose_axis_format`, which composes `repeat: every` the same way); a
    // layer on the base's scale has no axis to paint it on, and an anchor
    // ladder, the `tick_label` resolve bakes for a house SI layer format
    // (`_layers.py`, painted through `inject_axis_numeral_expr`) and the
    // tooltip's affix have no layer law.
    if let Some(slot) = axis.labels.and_then(|labels| labels.format) {
        let own_axis = axis.position.is_some();
        let baked_ladder = axis.tick_label.is_some_and(|tl| !tl.is_null());
        let anchors = slot.repeat.as_deref() == Some("anchor");
        if let Some(key) = slot.carried().find(|key| !own_axis || baked_ladder || (*key == "repeat" && anchors)) {
            let reason = if !own_axis {
                "a layer on the base's scale paints no axis of its own"
            } else if baked_ladder {
                "a layer's baked tick_label ladder is not ported (inject_axis_numeral_expr)"
            } else {
                "a layer axis bakes no anchor ladder"
            };
            return Err(format!("{family}: layer axis_y.labels.format {key:?} is not painted: {reason}"));
        }
    }
    if let Some(key) = axis.tooltip_format.as_ref().and_then(|slot| slot.carried().next()) {
        return Err(format!("{family}: layer axis_y.tooltip_format {key:?} is not painted: a layer tooltip's affix is unprobed against production"));
    }
    Ok(())
}

/// The opacity of every area layer's legend circle: the last area layer's
/// (`_overlay.py`'s one `area_opacity`).
pub(crate) fn shared_area_opacity<'a>(layers: impl DoubleEndedIterator<Item = &'a OverlayLineLayer>) -> Option<f64> {
    layers.rev().find_map(|l| l.area_mark.as_ref().map(|a| a.opacity))
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
            serde_json::from_value(raw.clone()).map_err(|e| format!("{family}: layer payload: {e}"))?;
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

    /// Refuses the label details no painter models: a labeled column other
    /// than the measure and a position other than `top`.
    pub(crate) fn refuse_unported_labels(&self, family: &str) -> Result<(), String> {
        self.visible_labels().map_or(Ok(()), |ml| refuse_unported_label_details(family, ml))
    }
}

/// d3's curve for an authored `curve` literal (VL `interpolate`). A literal
/// `step` is d3's centered `curveStep` here: the band-aware plateau is a row
/// transform the caller applies on a band x, never a curve.
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

pub(crate) fn refuse_unported_label_details(family: &str, ml: &MarkLabels) -> Result<(), String> {
    if let Some(field) = &ml.field {
        return Err(format!("{family}: layer value labels field {field:?} not supported — only the measure value"));
    }
    match ml.position.as_deref() {
        None | Some("top") => Ok(()),
        Some(other) => Err(format!("{family}: layer value labels position {other:?} not supported — only top")),
    }
}

/// True when every non-null value of `field` across `rows` is a JSON
/// string — mirrors production's `infer_vega_type_from_data` far enough to
/// gate the one modeled layer-color shape: a genuinely nominal/ordinal
/// field. A column with no values at all is nominal by the same fallback
/// `infer_vega_type_from_data` uses (`data or field not in data[0]` →
/// `"nominal"`).
pub(crate) fn column_is_nominal(rows: &[serde_json::Map<String, Value>], field: &str) -> bool {
    rows.iter().filter_map(|row| row.get(field)).filter(|v| !v.is_null()).all(Value::is_string)
}

/// `resolve_layer_rows` for one panel of a facet: a layer on the base query
/// draws over that panel's own rows (facets refuse a layer on another query),
/// while `payload` stays the whole chart for every other law.
pub(crate) fn panel_layer_rows<'a>(
    panel_rows: &'a [serde_json::Map<String, Value>],
    is_facet: bool,
    payload: &'a Payload,
    layer_query_name: Option<&str>,
    base_query_name: Option<&str>,
    family: &str,
) -> Result<&'a [serde_json::Map<String, Value>], String> {
    if is_facet {
        debug_assert_eq!(layer_query_name, base_query_name, "facets refuse a cross-query layer");
        return Ok(panel_rows);
    }
    resolve_layer_rows(payload, layer_query_name, base_query_name, family)
}

/// Refuses a null color value on a color-grouped layer. Production's
/// `distinct_series_values` drops it quietly before the layer claims its
/// colors; claiming it here would add a phantom blank series and shift every
/// later color, so it refuses instead of dropping.
pub(crate) fn refuse_null_layer_color(
    rows: &[serde_json::Map<String, Value>],
    field: &str,
    family: &str,
) -> Result<(), String> {
    if rows.iter().any(|row| row.get(field).is_none_or(Value::is_null)) {
        return Err(format!("{family}: a layer color field {field:?} with a null value is not supported"));
    }
    Ok(())
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
    base_len: usize,
    domain: Vec<String>,
    range: Vec<String>,
    glyphs: HashMap<String, LegendSymbol<'static>>,
}

impl<'a> LayerScale<'a> {
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
            base_len: base.len(),
            domain: base.iter().map(|(label, _)| label.clone()).collect(),
            range: base.iter().map(|(_, fill)| fill.clone()).collect(),
            glyphs: HashMap::new(),
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
    /// legend rows of a color-split base; elsewhere a repeat is legal (the
    /// ordinal scale dedupes it).
    pub(crate) fn refuse_repeat(&self, label: &str) -> Result<(), Diagnostic> {
        if self.color_split_base && self.domain.iter().any(|d| d == label) {
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
    /// `stroke_datums`/`circle_datums`/`square_datums` are.
    pub(crate) fn set_glyph(&mut self, label: &str, glyph: LegendSymbol<'static>) {
        self.glyphs.insert(label.to_string(), glyph);
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
                symbol: Some(self.glyphs.get(label).copied().unwrap_or_else(|| base_glyph(fill))),
                series_key: None,
            })
            .collect()
    }
}

/// A layer over the base query draws once per panel, over that panel's rows;
/// one reading another query has no partition law here.
pub(crate) fn refuse_facet_layer_query(
    family: &str,
    layers: &[Value],
    base_query: Option<&str>,
) -> Result<(), String> {
    if layers.iter().any(|raw| raw.get("query_name").and_then(Value::as_str) != base_query) {
        return Err(format!("{family}: multiples with a layer reading a different query not supported"));
    }
    Ok(())
}

/// A facet panel draws a line layer over its own rows, so a layer with its own
/// color grouping or value labels (fit against the whole chart's rows), or an
/// area or step shape no facet fixture probes, refuses.
pub(crate) fn refuse_facet_line_layer(family: &str, layer: &OverlayLineLayer) -> Result<(), String> {
    if layer.color.is_some() {
        return Err(format!("{family}: multiples with a layer with its own color grouping not supported"));
    }
    if layer.visible_labels().is_some() {
        return Err(format!("{family}: multiples with layer value labels not supported"));
    }
    if layer.area_mark.is_some() {
        return Err(format!("{family}: multiples with an area layer not supported"));
    }
    if layer.line_mark.curve.as_deref() == Some("step") {
        return Err(format!("{family}: multiples with a step layer not supported"));
    }
    if layer.line_mark.connect == Some(false) {
        return Err(format!("{family}: multiples with a disconnected layer not supported"));
    }
    Ok(())
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
/// An area layer closes down to `area_baseline`, the value zero clamped to the
/// plot: production clips an area whose baseline leaves a pinned domain
/// (`_baseline_outside_shared_domain`).
/// `series_color` is the layer's (or color value's) shared-scale color: the
/// area fill and the hover targets take it, the path takes an authored
/// `stroke.color` over it, and the points an authored `point_mark.color`.
pub(crate) fn paint_line_layer_marks(
    svg: &mut String,
    l: &OverlayLineLayer,
    rows: &[serde_json::Map<String, Value>],
    x_of: impl Fn(&serde_json::Map<String, Value>) -> Option<f64>,
    y_of: impl Fn(f64) -> f64,
    area_baseline: f64,
    horizontal: bool,
    series_color: &str,
    canvas: &str,
    mut describe: impl FnMut(&serde_json::Map<String, Value>, f64) -> Result<Option<String>, String>,
    board_style: Option<&crate::BoardStyle>,
    mut describe_label: impl FnMut(&serde_json::Map<String, Value>, f64) -> Result<Option<String>, String>,
    step_bandwidth: Option<f64>,
) -> Result<(), String> {
    use std::fmt::Write as _;

    let mut points: Vec<(f64, f64)> = Vec::with_capacity(rows.len());
    let mut labels: Vec<Option<String>> = Vec::with_capacity(rows.len());
    let mut values: Vec<(f64, Option<String>)> = Vec::with_capacity(rows.len());
    for row in rows {
        let Some(x) = x_of(row) else { continue };
        let val = row_num(row, &l.y)
            .ok_or_else(|| format!("layer row: missing or non-numeric field {:?}", l.y))?;
        points.push(if horizontal { (y_of(val), x) } else { (x, y_of(val)) });
        labels.push(describe(row, val)?);
        values.push((val, describe_label(row, val)?));
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
    let band_step = step_bandwidth.filter(|_| curve == d3rs::shape::Curve::Step);
    let disconnected = band_step.is_some() && l.is_disconnected();
    let plateau = l.plateau_share()?;
    let cap = crate::common::resolve_stroke_cap(
        Some(l.line_mark.stroke.cap.as_deref().unwrap_or("butt")),
        disconnected,
        l.line_mark.disconnected_cap.as_deref(),
    );
    let dash = l.line_mark.stroke.dasharray.as_deref().map_or(Ok(String::new()), |raw| {
        crate::step_band::attr("layer", &crate::step_band::parse("layer", raw)?, band_step.filter(|_| disconnected).map(|bw| bw * plateau))
    })?;
    let (curve, runs, mark_points, mark_labels): (_, Vec<Vec<(f64, f64)>>, Vec<(f64, f64)>, Vec<Option<String>>) =
        match band_step {
            Some(bw) => {
                let (lo, hi) = crate::step_band::band_step_edges(plateau);
                let pairs: Vec<[(f64, f64); 2]> = points
                    .iter()
                    .map(|&(cx, y)| [(cx - bw / 2.0 + bw * lo, y), (cx - bw / 2.0 + bw * hi, y)])
                    .collect();
                let flat: Vec<(f64, f64)> = pairs.iter().flatten().copied().collect();
                let runs = if disconnected { pairs.iter().map(|p| p.to_vec()).collect() } else { vec![flat.clone()] };
                let doubled_labels = labels.iter().flat_map(|label| [label.clone(), label.clone()]).collect();
                (d3rs::shape::Curve::StepAfter, runs, flat, doubled_labels)
            }
            None => {
                // Vega sorts a line/area mark's vertices along its dimension channel (the
                // scale domain's order), while its symbols keep the data's own order.
                let mut path_points = points.clone();
                path_points.sort_by(|a, b| if horizontal { a.1.total_cmp(&b.1) } else { a.0.total_cmp(&b.0) });
                (curve, vec![path_points], points.clone(), labels.clone())
            }
        };
    if let Some(area) = &l.area_mark {
        let d = d3rs::shape::area_path(&runs, area_baseline, curve);
        crate::contract::connected_group_open(svg, "area", "layer_1_marks");
        let _ = write!(
            svg,
            "<path d=\"{d}\" fill=\"{}\" fill-opacity=\"{}\" stroke-opacity=\"0\" opacity=\"1\"/>",
            xml_escape_attr(series_color),
            area.opacity,
        );
        crate::contract::mark_group_close(svg);
    }
    // A disconnected plateau is its own path, never joined to its neighbors.
    for run in &runs {
        let d = d3rs::shape::line_path(std::slice::from_ref(run), curve);
        crate::contract::connected_group_open(svg, "line", "layer_1_marks");
        let _ = write!(
            svg,
            "<path d=\"{d}\" fill=\"none\" stroke=\"{line_color}\" \
             stroke-width=\"{}\" stroke-linecap=\"{cap}\" stroke-linejoin=\"{join}\"{dash}/>",
            l.line_mark.stroke.width, line_color = xml_escape_attr(line_color), cap = xml_escape_attr(cap), join = xml_escape_attr(join)
        );
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
            crate::contract::mark_group_open(svg, "symbol", "layer_2_marks");
        }
        for (&(px, py), label) in mark_points.iter().zip(&mark_labels) {
            paint_overlay_point(svg, &l.point_mark, px, py, r_pt, series_color, canvas, label.as_deref());
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
            crate::contract::mark_group_open(svg, "symbol", "layer_3_marks");
        }
        for (&(px, py), label) in mark_points.iter().zip(&mark_labels) {
            let aria = if drawn.is_some() { None } else { label.as_deref() };
            let label_attr = crate::contract::aria_attr(aria, crate::contract::ItemRole::Point);
            let _ = write!(
                svg,
                "<circle transform=\"translate({px:.3},{py:.3})\" r=\"{hover_r:.3}\" fill=\"{}\" \
                 opacity=\"0\"{label_attr}/>",
                xml_escape_attr(series_color)
            );
        }
        crate::contract::mark_group_close(svg);
    }
    if let Some(ml) = l.visible_labels() {
        paint_layer_value_labels(svg, ml, l.label_is_house, board_style, &points, &values)?;
    }
    Ok(())
}

/// A layer's value-label typography and `_LINE_POS_MAP["top"]` offset, shared
/// by the painter and the line fit's ink so the two never drift.
pub(crate) struct LayerLabelStyle<'a> {
    pub font: crate::families::bar::LabelFont,
    pub font_style: String,
    pub color: &'a str,
    pub dx: f64,
    pub dy: f64,
}

impl<'a> LayerLabelStyle<'a> {
    pub(crate) fn new(ml: &'a MarkLabels, board_style: Option<&crate::BoardStyle>) -> Result<Self, String> {
        let font = crate::families::bar::label_font(ml, board_style)?;
        let color = ml.font.as_ref().and_then(|f| f.color.as_deref()).unwrap_or("#313233");
        Ok(LayerLabelStyle {
            font,
            font_style: ml.font.as_ref().map(crate::common::Font::style_attr).unwrap_or_default(),
            color,
            dx: ml.dx.unwrap_or(0.0),
            dy: ml.dy.unwrap_or(-4.0),
        })
    }

    /// The shaped width of one label's text.
    pub(crate) fn width(&self, fonts: &FontBook, text: &str) -> Result<f64, String> {
        fonts.measure_shaped(text, &self.font.family, self.font.size)
    }

    /// The ink of a label placed at the datum `(x, y)`: offset by `dx`/`dy`,
    /// centered, `baseline: bottom`. The one law every family's fit shares.
    pub(crate) fn ink(&self, x: f64, y: f64, width: f64) -> crate::vega_fit::Bounds {
        crate::vega_fit::text_ink(x + self.dx, y + self.dy, width, self.font.size, "center", "bottom")
    }
}

/// The layer's value-label text mark set, drawn after the layer's marks the
/// way production layers it: each label centered on its datum, `baseline:
/// bottom`, `dy: -4` (`_LINE_POS_MAP["top"]`).
pub(crate) fn paint_layer_value_labels(
    svg: &mut String,
    ml: &MarkLabels,
    is_house: bool,
    board_style: Option<&crate::BoardStyle>,
    points: &[(f64, f64)],
    values: &[(f64, Option<String>)],
) -> Result<(), String> {
    let style = LayerLabelStyle::new(ml, board_style)?;
    let stamped = values.iter().any(|(_, aria)| aria.is_some());
    if stamped {
        crate::contract::value_label_group_open(svg, "layer_4_marks");
    }
    for (&(px, py), (val, aria)) in points.iter().zip(values) {
        let text = crate::common::format_mark_label(ml.format.as_ref(), *val, is_house)?;
        let (x, y) = (px + style.dx, py + style.dy);
        crate::common::write_value_label(
            svg,
            x,
            y + crate::vega_fit::baseline_shift("bottom", style.font.size),
            "center",
            &style.font.family,
            style.font.size,
            &style.font.weight,
            &style.font_style,
            style.color,
            &crate::contract::aria_attr(aria.as_deref(), crate::contract::ItemRole::TextMark),
            &text,
        );
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
    series_color: &str,
    canvas: &str,
    aria: Option<&str>,
) {
    use std::fmt::Write as _;

    let point_color = pm.color.as_deref().unwrap_or(series_color);
    let filled = pm.filled.unwrap_or(true);
    let aria_attr = format!(
        "{}{}",
        if aria.is_some() { " opacity=\"1\"" } else { "" },
        crate::contract::aria_attr(aria, crate::contract::ItemRole::Point)
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

/// Paints a shared-scale `type: "scatter"` layer's own points -- the
/// lollipop law (`charts/extensibility_1`'s `lollipop` chart): a plain
/// point marker per row, wrapped in ONE `layer_N_marks` mark group (Vega's
/// own `mark-symbol` container holds every point of a `point` mark, unlike
/// bar's per-datum `mark-rect` wrapper -- probed against the compiled
/// scenegraph), `N` the scatter layer's own position in production's outer
/// `layer` list (base is `layer_0`). Scatter layers trail every `type:
/// "bar"` layer (`validate_layers`' own "bars, then a trailing line or run of
/// scatter layers" sequencing), so the i-th of them has index `1 +
/// bar_layers.len() + i` -- `1` for `lollipop` (no bar layers), `2` for a
/// `[bar, scatter]` combo (probed against `bullet`'s own reference: its bar
/// layer is `layer_1_marks`, its trailing line layer `layer_2_layer_*`), and
/// `1`, `2` for a dumbbell's two scatter layers. `x_of`/`y_of` mirror
/// `paint_line_layer_marks`'s own laws (a bar base's band CENTER, the
/// shared/independent linear scale's position); a `horizontal` base swaps the
/// two onto x and y; `aria_of` builds each
/// datum's structured-tooltip label (`Ok(None)` when `tooltip_labels` is
/// off), propagating a real error (a format spec `d3rs::format` can't
/// parse) rather than swallowing it. `size` is the caller's own
/// already-validated (`validate_layers`) positive point size -- a scatter
/// layer with none is refused before paint ever runs, since the point IS
/// the whole mark, unlike a line layer's optional point overlay.
#[allow(clippy::too_many_arguments)]
pub(crate) fn paint_scatter_layer_marks(
    svg: &mut String,
    l: &OverlayScatterLayer,
    rows: &[serde_json::Map<String, Value>],
    size: f64,
    layer_index: usize,
    horizontal: bool,
    x_of: impl Fn(&serde_json::Map<String, Value>) -> Option<f64>,
    y_of: impl Fn(f64) -> f64,
    mut aria_of: impl FnMut(&serde_json::Map<String, Value>, f64) -> Result<Option<String>, String>,
    series_color: &str,
    canvas: &str,
    board_style: Option<&crate::BoardStyle>,
    mut describe_label: impl FnMut(&serde_json::Map<String, Value>, f64) -> Result<Option<String>, String>,
) -> Result<(), String> {
    let r_pt = crate::common::symbol_radius(size);
    crate::contract::mark_group_open(svg, "symbol", &format!("layer_{layer_index}_marks"));
    let mut points: Vec<(f64, f64)> = Vec::with_capacity(rows.len());
    let mut values: Vec<(f64, Option<String>)> = Vec::with_capacity(rows.len());
    for row in rows {
        let Some(cross) = x_of(row) else { continue };
        let val = row_num(row, &l.y)
            .ok_or_else(|| format!("layer row: missing or non-numeric field {:?}", l.y))?;
        let measure = y_of(val);
        let (x, y) = if horizontal { (measure, cross) } else { (cross, measure) };
        let aria = aria_of(row, val)?;
        paint_overlay_point(svg, &l.point_mark, x, y, r_pt, series_color, canvas, aria.as_deref());
        points.push((x, y));
        values.push((val, describe_label(row, val)?));
    }
    crate::contract::mark_group_close(svg);
    if l.point_mark.labels.visible {
        paint_layer_value_labels(svg, &l.point_mark.labels, l.label_is_house, board_style, &points, &values)?;
    }
    Ok(())
}

/// A `type: "bar"` layer's own raw JSON — `bar_mark` is the SAME shape as
/// the base's own `r.style.mark` (`MarkStyle`), reused verbatim rather than
/// a near-duplicate struct. Bar-only: no line family admits a bar-type
/// layer.
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

/// Either modeled TRAILING overlay layer shape — a `type: "bar"` layer
/// never trails (`bar.rs::validate_layers` collects those into its own
/// `Vec<OverlayBarLayer>` directly). Both variants are boxed so neither's
/// own mark-style fields inflate the size of `Option<OverlayLayer>`, which
/// `bar::render()` carries by value even on the far more common `None`
/// case.
pub(crate) enum OverlayLayer {
    Line(Box<OverlayLineLayer>),
    Scatter(Box<OverlayScatterLayer>),
}

/// A dual-axis layer's own independent y scale — computed once from the
/// layer's own row values, never from the negotiated plot size.
pub(crate) struct LayerAxis {
    /// The side the layer's axis draws on; the base takes the other.
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
    pub title_text: String,
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

/// The y-axis title styles of a chart with a pinned layer. A dual axis needs
/// both sides labeled to tell the scales apart: production restores the base
/// title when the theme hid it (`_overlay.py`), unless an authored `y_label`
/// with a hidden title suppressed it, and titles the layer axis
/// unconditionally. A restored title carries no theme styling.
pub(crate) struct PinnedTitles {
    restored: Option<crate::common::AxisTitle>,
    base_suppressed: bool,
}

impl PinnedTitles {
    pub(crate) fn new(pinned: bool, ax_y: &crate::common::AxisStyle, y_label: Option<&str>) -> Self {
        let hidden = ax_y.title.visible == Some(false);
        PinnedTitles {
            restored: (pinned && hidden)
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

/// Rows sharing a category stack in row order (a bar layer's `y` keeps
/// Vega-Lite's default `stack: zero`, with no series to order by): each row's
/// `(category, start, end)`.
pub(crate) fn row_order_stack<'a>(
    rows: impl IntoIterator<Item = &'a serde_json::Map<String, Value>>,
    cat_field: &str,
    y: &str,
) -> Vec<(String, f64, f64)> {
    let mut stacks: HashMap<String, crate::common::StackZero> = HashMap::new();
    rows.into_iter()
        .filter_map(|row| {
            let cat = row_str(row, cat_field);
            let (start, end) = stacks.entry(cat.clone()).or_insert_with(|| crate::common::StackZero::new(0.0)).push(row_num(row, y)?);
            Some((cat, start, end))
        })
        .collect()
}

/// The mark family of a layer pinned to its own axis.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum PinnedKind {
    Line,
    Area,
    Scatter,
    Bar,
}

/// The one layer pinned to its own side (`axis_y.position`), which gets its
/// own independent value scale. A bar or area mark paints from the value-0
/// baseline, so its scale always carries the zero rule (`_overlay.py`'s
/// `layer_should_fire`).
pub(crate) struct PinnedLayer<'a> {
    pub side: LayerSide,
    pub kind: PinnedKind,
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

impl PinnedLayer<'_> {
    pub(crate) fn always_rules_zero(&self) -> bool {
        matches!(self.kind, PinnedKind::Area | PinnedKind::Bar)
    }

    /// The stroke width of the layer's own zero rule (`_overlay.py` nests it
    /// into the layer's entry): a bar or area paints from zero, so its scale
    /// always carries one; a line or scatter only where its values reach
    /// zero. `None` when the grid or its threshold is hidden.
    pub(crate) fn zero_rule_width(&self, ax_y: &crate::common::AxisStyle) -> Option<f64> {
        let values: Vec<f64> = self.rows.iter().filter_map(|row| row_num(row, self.y)).collect();
        let lo = values.iter().copied().fold(f64::INFINITY, f64::min);
        let hi = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let fires = self.always_rules_zero() || (lo <= 0.0 && 0.0 <= hi);
        if !fires || self.rows.is_empty() || !ax_y.grid.visible.unwrap_or(false) || !ax_y.grid.threshold_visible() {
            return None;
        }
        ax_y.grid.threshold.as_ref().map(|z| z.width.unwrap_or(2.0))
    }

    /// The layer's own scale, ladder, labels and title (`_overlay.py`'s
    /// dual-axis `layer_ay_vl`), from the `(min, max)` extent of its marks:
    /// the zero union with the layer's values only, never the base's, then
    /// d3's default nice count.
    pub(crate) fn axis(&self, extent: (f64, f64), frame: &LayerAxisFrame) -> Result<LayerAxis, String> {
        let LayerAxisFrame { family, ax_y, fonts, font_family, font_size, label_limit, tick_size, title_style, field_labels } =
            *frame;
        let (layer_min, layer_max) = (extent.0.min(0.0), extent.1.max(0.0));
        let (domain_min, domain_max) = d3rs::ticks::d3_nice(layer_min, layer_max, 10);
        let count = ax_y.ticks.count.unwrap_or(6.0).max(1.0) as usize;
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
                _ => crate::common::format_y_label(tv, None, None, layer_format, i, tick_values.len()),
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
        let title_text = self
            .axis_y
            .title
            .clone()
            .unwrap_or_else(|| crate::common::axis_title_text(self.y, self.label, field_labels));
        let title_extra = crate::common::y_title_extra(title_style, &title_text);
        Ok(LayerAxis {
            side: self.side,
            domain_min,
            domain_max,
            value_max: layer_max,
            tick_values,
            tick_labels,
            label_ws,
            label_offset,
            gutter,
            title_text,
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
    crate::contract::axis_group_open(svg, crate::contract::AxisChannel::Y, (edge_x, top));
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
        let y = tick_y + crate::vega_fit::baseline_shift("middle", ink.font_size);
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

/// The x domain after each bar-type layer's own x values join `base_domain`:
/// `base_domain` unchanged when no layer adds a category, the layer-only values
/// placed into the order the base states when it states one, else the union in
/// paint order across the independently-ordered result sets.
pub(crate) fn union_x_domain(
    base_domain: &[String],
    layers: &[&[serde_json::Map<String, Value>]],
    x_field: &str,
    sorted: bool,
) -> XUnion {
    let mut union: Vec<String> = base_domain.to_vec();
    let mut seen: HashSet<String> = union.iter().cloned().collect();
    let mut layer_only: Vec<Value> = Vec::new();
    let mut layer_only_keys: Vec<String> = Vec::new();
    let mut contributors = usize::from(!base_domain.is_empty());
    for rows in layers {
        let mut added = false;
        for row in *rows {
            let Some(value) = row.get(x_field).filter(|v| !v.is_null()) else { continue };
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
    use super::*;
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
            title_text: String::new(),
            title_extra: 15.0,
        }
    }

    /// A side legend anchors on the extent of every value axis: a right layer
    /// axis pushes the right edge past the plot, a left one the left edge.
    #[test]
    fn a_layer_axis_widens_the_extent_a_legend_anchors_on_by_its_own_side() {
        assert_eq!(axis(LayerSide::Right).widen_axes((-30.0, 400.0), 400.0, 0.0), (-30.0, 435.0));
        assert_eq!(axis(LayerSide::Left).widen_axes((-30.0, 400.0), 400.0, 0.0), (-35.0, 400.0));
        assert_eq!(axis(LayerSide::Right).widen_axes((-30.0, 500.0), 400.0, 0.0), (-30.0, 500.0));
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
        let described = " aria-label=\"a: 1\" role=\"graphics-symbol\" aria-roledescription=\"point\"";
        for (filled, want) in [
            (true, format!("<circle transform=\"translate(10.000,20.000)\" r=\"4.000\" fill=\"#111\" opacity=\"1\"{described}/>")),
            (
                false,
                format!("<circle transform=\"translate(10.000,20.000)\" r=\"4.000\" fill=\"#fff\" stroke=\"#111\" stroke-width=\"2\" opacity=\"1\"{described}/>"),
            ),
        ] {
            let mut svg = String::new();
            paint_overlay_point(&mut svg, &point_mark(filled), 10.0, 20.0, 4.0, "#111", "#fff", Some("a: 1"));
            assert_eq!(svg, want);
        }
    }

    #[test]
    fn an_authored_layer_domain_refuses_by_name_and_an_unset_one_does_not() {
        let layer = |scale: serde_json::Value| serde_json::json!({"axis_y": {"position": null, "scale": scale}});
        let err = refuse_unpainted_layer_affix("line", &layer(serde_json::json!({"domain": [230.0, 290.0], "clip": true})))
            .unwrap_err();
        assert!(err.contains("layer axis_y.scale.domain"), "{err}");
        for unset in [serde_json::Value::Null, serde_json::json!({"domain": null, "clip": true})] {
            refuse_unpainted_layer_affix("line", &layer(unset)).unwrap();
        }
    }
}
