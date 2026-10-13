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
//! Bar-only shapes (`type: bar` layers, dual-axis, grouped-base) also have
//! their own types/helpers here (`OverlayBarLayer`, `LayerAxis`,
//! `union_cat_domain`, `column_is_nominal`) — only `bar.rs` calls them, but
//! they live alongside the two-caller machinery rather than in `bar.rs`
//! itself.

use std::collections::HashSet;

use serde::Deserialize;
use serde_json::Value;

use crate::common::{row_num, row_str, FormatSlot, MarkLabels};
use crate::families::legend::{LegendEntry, LegendSymbol};
use crate::svg::xml_escape_attr;
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
    pub labels: MarkLabels,
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
/// (`_overlay.py:1583`); every other layer format affix reaches no painter
/// here and refuses by name instead of vanishing.
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
    // A layer's own right axis paints its `labels.format` affix on every
    // non-zero tick (`format_y_label` through `FormatSlot::paint_tick`,
    // `compose_axis_format`, which composes `repeat: every` the same way); a
    // layer on the base's scale has no axis to paint it on, and an anchor
    // ladder, the `tick_label` resolve bakes for a house SI layer format
    // (`_layers.py`, painted through `inject_axis_numeral_expr`) and the
    // tooltip's affix have no layer law.
    if let Some(slot) = axis.labels.and_then(|labels| labels.format) {
        let own_axis = axis.position.as_deref() == Some("right");
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

#[derive(Deserialize)]
pub(crate) struct OverlayAxisY {
    #[serde(default)]
    pub position: Option<String>,
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
        .ok_or_else(|| format!("{family}: a layer reading a different query than the base is not supported"))?;
    payload
        .datasets
        .as_ref()
        .and_then(|d| d.get(qn))
        .map(Vec::as_slice)
        .ok_or_else(|| format!("{family}: a layer reading query {qn:?} has no dataset available"))
}

/// The mixed-mark legend's shared-scale law (`_mixed_mark_legend_symbols`,
/// `_overlay.py:649`): one entry per painted series, the base's own
/// entry/entries first (each carrying `base_glyph`'s own glyph for that
/// entry's fill — `Square` for a bar base, `Stroke` for a line base, since
/// `base_symbol` there is the base MARK's own glyph, not always a square),
/// then the layer's own entry (or one per distinct color value, when the
/// layer authors its own `color`) — always a `Stroke` glyph, a line layer's
/// own mark. `base_entries` is the base's already-built (label, fill) pairs
/// for a grouped/color-split base; an empty slice falls back to the single
/// `fallback` (label, fill) swatch a colorless single-series base draws.
/// `layer` is the layer's own (label, fill), read only when it authors no
/// `color` of its own (`layer_color_domain` empty).
pub(crate) fn line_layer_legend_entries<'a>(
    base_glyph: impl Fn(&'a str) -> LegendSymbol<'a>,
    base_entries: &'a [(String, String)],
    fallback: (&'a str, &'a str),
    layer: (&'a str, &'a str),
    layer_color_domain: &'a [String],
    layer_color_fills: &'a [String],
) -> Vec<LegendEntry<'a>> {
    let (fallback_label, fallback_fill) = fallback;
    let (layer_label, layer_fill) = layer;
    let mut entries: Vec<LegendEntry> = if base_entries.is_empty() {
        vec![LegendEntry {
            label: fallback_label,
            fill: fallback_fill,
            symbol: Some(base_glyph(fallback_fill)),
            series_key: None,
        }]
    } else {
        base_entries
            .iter()
            .map(|(label, fill)| LegendEntry {
                label: label.as_str(),
                fill: fill.as_str(),
                symbol: Some(base_glyph(fill.as_str())),
                series_key: None,
            })
            .collect()
    };
    if layer_color_domain.is_empty() {
        entries.push(LegendEntry {
            label: layer_label,
            fill: layer_fill,
            symbol: Some(LegendSymbol::Stroke(2.0)),
            series_key: None,
        });
    } else {
        // One entry per distinct color value — the value itself is the
        // label (`color_enc`'s own domain, not `layer.label`).
        for (value, fill) in layer_color_domain.iter().zip(layer_color_fills.iter()) {
            entries.push(LegendEntry {
                label: value.as_str(),
                fill: fill.as_str(),
                symbol: Some(LegendSymbol::Stroke(2.0)),
                series_key: None,
            });
        }
    }
    entries
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
pub(crate) fn paint_line_layer_marks(
    svg: &mut String,
    l: &OverlayLineLayer,
    rows: &[serde_json::Map<String, Value>],
    x_of: impl Fn(&serde_json::Map<String, Value>) -> Option<f64>,
    y_of: impl Fn(f64) -> f64,
    line_color: &str,
    canvas: &str,
    mut describe: impl FnMut(&serde_json::Map<String, Value>, f64) -> Result<Option<String>, String>,
) -> Result<(), String> {
    use std::fmt::Write as _;

    let mut points: Vec<(f64, f64)> = Vec::with_capacity(rows.len());
    let mut labels: Vec<Option<String>> = Vec::with_capacity(rows.len());
    for row in rows {
        let Some(x) = x_of(row) else { continue };
        let val = row_num(row, &l.y)
            .ok_or_else(|| format!("layer row: missing or non-numeric field {:?}", l.y))?;
        points.push((x, y_of(val)));
        labels.push(describe(row, val)?);
    }
    let cap = l.line_mark.stroke.cap.as_deref().unwrap_or("butt");
    let join = l.line_mark.stroke.join.as_deref().unwrap_or("miter");
    let dash = l.line_mark.stroke.dasharray.as_deref().map_or(Ok(String::new()), |raw| {
        crate::step_band::attr("layer", &crate::step_band::parse("layer", raw)?, None)
    })?;
    let d = d3rs::shape::line_path(std::slice::from_ref(&points), d3rs::shape::Curve::Linear);
    crate::contract::connected_group_open(svg, "line", "layer_1_marks");
    let _ = write!(
        svg,
        "<path d=\"{d}\" fill=\"none\" stroke=\"{line_color}\" \
         stroke-width=\"{}\" stroke-linecap=\"{cap}\" stroke-linejoin=\"{join}\"{dash}/>",
        l.line_mark.stroke.width, line_color = xml_escape_attr(line_color), cap = xml_escape_attr(cap), join = xml_escape_attr(join)
    );
    crate::contract::mark_group_close(svg);
    // A visible point overlay only draws when `point_mark.size` is authored
    // (production's `point_size` gate, `_layers.py`); a hollow point
    // (`filled: false`) paints its own `fill`, else the chart canvas (an
    // engine default: `_emit_point_mark` leaves it unset), with the series
    // color as its stroke. The invisible hover target sits over every datum
    // (`_hover_target_point`); it announces the datum only when no drawn
    // point does.
    let announced = labels.iter().any(Option::is_some);
    let drawn = l.point_mark.size.filter(|s| *s > 0.0);
    if let Some(size) = drawn {
        let r_pt = crate::common::symbol_radius(size);
        if announced {
            crate::contract::mark_group_open(svg, "symbol", "layer_2_marks");
        }
        for (&(px, py), label) in points.iter().zip(&labels) {
            paint_overlay_point(svg, &l.point_mark, px, py, r_pt, line_color, canvas, label.as_deref());
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
        for (&(px, py), label) in points.iter().zip(&labels) {
            let aria = if drawn.is_some() { None } else { label.as_deref() };
            let label_attr = crate::contract::aria_attr(aria, crate::contract::ItemRole::Point);
            let _ = write!(
                svg,
                "<circle transform=\"translate({px:.3},{py:.3})\" r=\"{hover_r:.3}\" fill=\"{}\" \
                 opacity=\"0\"{label_attr}/>",
                xml_escape_attr(line_color)
            );
        }
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
) -> Result<(), String> {
    let r_pt = crate::common::symbol_radius(size);
    crate::contract::mark_group_open(svg, "symbol", &format!("layer_{layer_index}_marks"));
    for row in rows {
        let Some(cross) = x_of(row) else { continue };
        let val = row_num(row, &l.y)
            .ok_or_else(|| format!("layer row: missing or non-numeric field {:?}", l.y))?;
        let measure = y_of(val);
        let (x, y) = if horizontal { (measure, cross) } else { (cross, measure) };
        let aria = aria_of(row, val)?;
        paint_overlay_point(svg, &l.point_mark, x, y, r_pt, series_color, canvas, aria.as_deref());
    }
    crate::contract::mark_group_close(svg);
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

/// A dual-axis layer's own independent right-orient y scale — computed once
/// from the layer's own row values, never from the negotiated plot size.
/// Bar-only: line's own admitted shape refuses a right-pinned layer axis
/// (unprobed).
pub(crate) struct LayerAxis {
    pub domain_max: f64,
    pub tick_values: Vec<f64>,
    pub tick_labels: Vec<String>,
    pub label_ws: Vec<f64>,
    /// Where each label starts past the plot edge: a visible tick's length
    /// plus the theme `labels.padding`. The labels start-anchor here (the
    /// layer's axis is the bare template with `labelAlign` dropped).
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

/// The union x domain for a bar-type layer sharing the base's own x field:
/// `base_domain` unchanged when the layer contributes no category the base
/// doesn't already have, else the two columns' distinct values in ONE
/// numerically-ascending order (`x_domain.py::rendered_x_domain`'s own
/// `defined_domain_order`/`extend_domain_in_base_order` numeric branch —
/// the only ordering law this port models; a non-numeric new value refuses
/// rather than guessing a paint order via the unordered-union WARN path,
/// which is unported). Bar-only.
pub(crate) fn union_cat_domain(
    base_domain: &[String],
    layer_rows: &[serde_json::Map<String, Value>],
    x_field: &str,
) -> Result<Vec<String>, String> {
    let mut seen: HashSet<String> = base_domain.iter().cloned().collect();
    let mut new_values: Vec<String> = Vec::new();
    for row in layer_rows {
        let v = row_str(row, x_field);
        if seen.insert(v.clone()) {
            new_values.push(v);
        }
    }
    if new_values.is_empty() {
        return Ok(base_domain.to_vec());
    }
    let mut parsed: Vec<(f64, String)> = Vec::with_capacity(base_domain.len() + new_values.len());
    for v in base_domain.iter().chain(new_values.iter()) {
        let n = v.trim().parse::<f64>().map_err(|_| {
            format!(
                "bar: a bar-type layer x union value {v:?} is not numeric — only a numeric \
                 x union is supported"
            )
        })?;
        parsed.push((n, v.clone()));
    }
    parsed.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    Ok(parsed.into_iter().map(|(_, v)| v).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
