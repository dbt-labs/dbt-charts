//! The bar legend: its entries, their order and the layered legend's parts.

use crate::families::color_scale::{ContinuousColor, NumericLegends};
use crate::families::layers::{LayerClaim, LayerScale, OverlayLayer};
use crate::families::legend::{LegendConfig, LegendEntry, LegendSymbol, fold_legend_token, legend_title_text, narrow_legend_entries, resolve_legend_values};

use std::collections::HashMap;

use super::BarResolved;

/// The legends of the layers colored by a numeric field, in layer order.
pub(crate) fn numeric_legends_of<'a>(
    base: Option<(&'a ContinuousColor, bool, String, Option<&'a crate::common::FormatSlot>)>,
    layers: impl Iterator<Item = (&'a OverlayLayer, &'a LayerClaim)> + Clone,
    legend: &LegendConfig,
    chart_id: &str,
) -> Result<NumericLegends<'a>, String> {
    let mut legends = NumericLegends::new(
        base.into_iter().chain(layers.clone().filter(|(_, claim)| claim.legend).filter_map(|(layer, claim)| {
            claim.numeric.as_ref().map(|color| {
                (color, matches!(layer, OverlayLayer::Bar(_) | OverlayLayer::Scatter(_)), layer.color().expect("a numeric layer has a color").to_string(), None)
            })
        })),
        legend,
        &format!("layer_grad-{}", crate::contract::safe_svg_id(chart_id)),
    )?;
    legends.unlisted_line = layers
        .into_iter()
        .any(|(layer, claim)| claim.legend && claim.numeric.is_some() && matches!(layer, OverlayLayer::Line(l) if l.area_mark.is_none()));
    Ok(legends)
}

/// A layered bar chart's legend, built alike on either orientation: the base's own entries
/// (one per color value, or the one labeled fill of a colorless base), then each layer's claims.
pub(super) struct LayeredBarLegend {
    pub(super) shown: bool,
    pub(super) has_layers: bool,
    pub(super) has_line: bool,
    pub(super) base_label: String,
    /// The base as displayed (a grouped or stacked per-series list); empty for a single series.
    pub(super) displayed: Vec<(String, String)>,
    /// `displayed`, else the one labeled fill the shared scale took.
    pub(super) base_entries: Vec<(String, String)>,
    pub(super) bar_layer_legend: Vec<(String, String)>,
    pub(super) scatter_legend: Vec<(String, String, bool)>,
    pub(super) bar_layer_colors: Vec<Option<String>>,
    pub(super) color_field: Option<String>,
}

impl LayeredBarLegend {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn new(
        r: &BarResolved,
        shown: bool,
        base_label: &str,
        displayed: &[(String, String)],
        base_scale_entries: &[(String, String)],
        color_domain: &[String],
        layer_scale: &LayerScale,
        claims: &[LayerClaim],
        layers: &[&OverlayLayer],
    ) -> Result<Self, String> {
        let has_scatter = layers.iter().any(|l| matches!(l, OverlayLayer::Scatter(_)));
        let base_entries: Vec<(String, String)> =
            if displayed.is_empty() { base_scale_entries.to_vec() } else { displayed.to_vec() };
        // A color-split base claims one palette slot per color value ahead of the layers.
        let scatter_base: Vec<(String, String)> = if color_domain.is_empty() {
            vec![(base_label.to_string(), r.style.single_series_fill.clone())]
        } else if displayed.is_empty() {
            color_domain.iter().map(|v| (v.clone(), String::new())).collect()
        } else {
            displayed.to_vec()
        };
        // Every layer's claims, bar and scatter alike, in the order the shared scale took them.
        let bar_layer_legend = layer_scale.layer_entries().into_iter().map(|(l, f)| (l.to_string(), f.to_string())).collect();
        let scatter_legend = if shown && has_scatter {
            scatter_legend_entries(
                &scatter_base,
                claims,
                layer_scale,
                &r.legend,
                !color_domain.is_empty(),
                &layers.iter().map(|l| matches!(l, OverlayLayer::Scatter(_))).collect::<Vec<_>>(),
            )
        } else {
            Vec::new()
        };
        Ok(Self {
            shown,
            has_layers: !layers.is_empty(),
            has_line: layers.iter().any(|l| matches!(l, OverlayLayer::Line(_))),
            base_label: base_label.to_string(),
            displayed: displayed.to_vec(),
            base_entries,
            bar_layer_legend,
            scatter_legend,
            bar_layer_colors: layers
                .iter()
                .filter_map(|l| match l {
                    OverlayLayer::Bar(bl) => Some(bl.color.clone()),
                    _ => None,
                })
                .collect(),
            color_field: r.color.clone(),
        })
    }

    pub(super) fn entries<'s>(&'s self, r: &'s BarResolved, layer_scale: &'s LayerScale) -> Vec<LegendEntry<'s>> {
    let legend_entries_typed: Vec<LegendEntry> = if self.shown && self.has_line {
        // `_mixed_mark_legend_symbols`'s own `symbolStrokeColor`
        // (`_overlay.py:717`): once a mixed legend carries ANY stroke
        // (line) entry, every entry on the shared color scale strokes
        // in its OWN fill, not the mark's border color — a bar's
        // legend square reads as a solid swatch here, unlike a plain
        // (non-layered) bar legend's border-stroked square. A grouped
        // base keeps the grouped legend's OWN per-series entries here
        // (probed: `grouped-bar-with-layer`'s reference legend carries
        // "Enterprise, Mid-Market, Quota" — the base's two color-domain
        // values, not a single y-field-labeled swatch); a colorless
        // single-series base falls back to the one y-field swatch.
        layer_scale.legend_entries(&self.base_entries, LegendSymbol::Square)
    } else if self.shown && self.bar_layer_colors.iter().any(|c| c.is_none() || c.as_deref() != self.color_field.as_deref()) {
        // A bar-type layer's own legend entry paints the SAME square glyph
        // as the base's (`emit_bar_layer`'s legend law: square, never
        // stroke) — `symbol: None` falls back to `emit_vega_legend`'s own
        // default `Square(swatch_stroke)`, the mark's real border color,
        // since a bar+bar legend never triggers the mixed-mark
        // `symbolStrokeColor` self-stroke rule (that rule only fires once
        // a `Stroke` glyph joins the scale). An unauthored bar-layer label
        // resolving to the SAME text as an entry already in the list
        // (this task's own probed fixture: both default to their shared
        // field name) collapses to one entry — Vega-Lite's ordinal color
        // scale dedupes a repeated domain value to its first occurrence
        // (d3-scale's own `domain()` setter skips a value it has already
        // indexed).
        let mut entries: Vec<LegendEntry> = if self.displayed.is_empty() {
            vec![LegendEntry {
                label: self.base_label.as_str(),
                fill: r.style.single_series_fill.as_str(),
                symbol: None,
                series_key: None,
                recede: None,
            }]
        } else {
            self.displayed
                .iter()
                .map(|(label, fill)| LegendEntry {
                    label: label.as_str(),
                    fill: fill.as_str(),
                    symbol: None,
                    series_key: None,
                    recede: None,
                })
                .collect()
        };
        for (label, fill) in &self.bar_layer_legend {
            if !entries.iter().any(|e| e.label == label) {
                entries.push(LegendEntry {
                    label: label.as_str(),
                    fill: fill.as_str(),
                    symbol: None,
                    series_key: None,
                    recede: None,
                });
            }
        }
        entries
    } else if !self.scatter_legend.is_empty() {
        // A grouped layer's values draw as circles ringed in the mark's border,
        // as the base's squares are (`circle_datums`).
        let ring = LegendSymbol::BorderedCircle(r.style.mark.border.color.as_deref().unwrap_or("none"));
        self.scatter_legend
            .iter()
            .map(|(label, fill, circle)| LegendEntry {
                label: label.as_str(),
                fill: fill.as_str(),
                symbol: circle.then_some(ring),
                series_key: None,
                recede: None,
            })
            .collect()
    } else {
        self.displayed
            .iter()
            .map(|(label, fill)| LegendEntry {
                label: label.as_str(),
                fill: fill.as_str(),
                symbol: None,
                series_key: None,
                recede: None,
            })
            .collect()
    };
    // An authored `legend.values` narrows and orders the whole entry list of a layered chart.
    let legend_entries_typed = if self.shown && self.has_layers {
        narrow_legend_entries(legend_entries_typed, &r.legend)
    } else {
        legend_entries_typed
    };
    legend_entries_typed
    }
}

/// The legend entries of a base and its layers' claims, as `(label, fill,
/// circle)`: the scale's domain (the base, then each layer's new labels and color
/// values), narrowed and ordered by an authored `legend.values` — Vega's legend
/// `values` shows exactly those, in that order. A grouped layer's values draw as
/// circles (`circle_datums`), and over a color-split base the base's own entries and a colorless scatter layer's do too. A value naming no entry draws nothing, and values that
/// name none draw the whole domain (`apply_legend_entry_order`).
pub(super) fn scatter_legend_entries(
    base: &[(String, String)],
    claims: &[LayerClaim],
    scale: &LayerScale,
    legend: &LegendConfig,
    circles_all: bool,
    scatter_claims: &[bool],
) -> Vec<(String, String, bool)> {
    let mut entries: Vec<(String, String, bool)> =
        base.iter().map(|(label, fill)| (label.clone(), fill.clone(), circles_all)).collect();
    for (claim, &scatter) in claims.iter().zip(scatter_claims).filter(|(claim, _)| claim.legend) {
        let own: Vec<(String, String, bool)> = match &claim.fill {
            Some(fill) => vec![(claim.label.clone(), fill.clone(), scatter && circles_all)],
            None => claim.values.iter().map(|value| (value.clone(), scale.color_of(value).to_string(), true)).collect(),
        };
        for entry in own {
            if !entries.iter().any(|(seen, _, _)| *seen == entry.0) {
                entries.push(entry);
            }
        }
    }
    let Some(values) = &legend.values else { return entries };
    if values.is_empty() {
        return Vec::new();
    }
    let domain: Vec<&str> = entries.iter().map(|(label, _, _)| label.as_str()).collect();
    resolve_legend_values(values, &domain, None).into_iter().map(|i| entries[i].clone()).collect()
}

/// Once-per-chart legend construction — `LegendState`. Built from a
/// `BarChartState`'s domains, so both live once per chart, shared by every
/// small-multiples panel once faceting composes them.
pub(super) struct LegendState {
    pub(super) shown: bool,
    pub(super) title: String,
    /// `(label, fill)` pairs in display order, BEFORE any layer-mixing —
    /// `render_vertical`'s own layer/bar-layer legend variants are built
    /// from these, not a second call to `stacked_legend_entries`.
    pub(super) entries: Vec<(String, String)>,
}

/// Build `LegendState`. `extra_admit`/`suppress`/`reverse_stack_for_legend`
/// are `render_vertical`'s own layer-aware extensions to an otherwise
/// shared formula — `render_horizontal` (no `chart.layers` support) passes
/// `false`/`false`/`false`, which reduces every one of these three exactly
/// to what it already computed inline:
/// - `extra_admit`: `layer.is_some() || bar_layer.is_some()` — a layer/
///   bar-layer forces the legend on even off a colorless single-series base
///   (production's `force_legend_visible`).
/// - `suppress`: `has_per_series` — a normal `support_table` per_series
///   entry suppresses an otherwise-shown legend at render time.
/// - `reverse_stack_for_legend`: vertical's stacked legend reads
///   `stack_order` reversed (`apply_legend_entry_order`'s own
///   orientation split — horizontal keeps `stack_order` as-is).
#[allow(clippy::too_many_arguments)]
pub(super) fn build_legend_state(
    color_domain: &[String],
    stack_order: &[String],
    palette_domain: &[String],
    is_stacked: bool,
    legend: &LegendConfig,
    palette: &[String],
    category_scale: Option<&crate::common::CategoryColorScale>,
    color_field: Option<&str>,
    extra_admit: bool,
    suppress: bool,
    reverse_stack_for_legend: bool,
    field_labels: &crate::common::FieldLabels,
    wide_labels: &HashMap<String, String>,
    numeric: bool,
) -> Result<LegendState, String> {
    let shown = legend.visible && (!color_domain.is_empty() || extra_admit) && !suppress;
    let entries: Vec<(String, String)> = if shown && !color_domain.is_empty() && !numeric {
        if is_stacked {
            let order: Vec<String> = if reverse_stack_for_legend {
                stack_order.iter().rev().cloned().collect()
            } else {
                stack_order.to_vec()
            };
            stacked_legend_entries(&order, palette_domain, palette, category_scale, wide_labels)?
        } else {
            stacked_legend_entries(
                &grouped_legend_domain(color_domain, legend, wide_labels),
                color_domain,
                palette,
                category_scale,
                wide_labels,
            )?
        }
    } else {
        Vec::new()
    };
    let title = if numeric { String::new() } else { legend_title_text(color_field.unwrap_or(""), legend, field_labels)? };
    Ok(LegendState { shown, title, entries })
}

// ── Horizontal bar render ─────────────────────────────────────────────────────

/// A grouped bar's legend domain: `color_domain` (alphabetical, the VL
/// default), unless `legend.values` is authored as a FULL reordering of it
/// -- same members (an exact spelling, or a case/`-`/`_`/space-folded one,
/// per `fold_legend_token` above), different order -- in which case the
/// resolved domain order wins outright. Mirrors `emitters/_channels.py`'s
/// `resolve_legend_entries` + `bar.py`'s `not unmatched and sorted(resolved)
/// == sorted(series)` gate for this same (grouped, non-stacked) branch -- an
/// ambiguous fold (two domain entries fold to the same key) or any entry
/// that resolves to nothing is treated as WARN-severity does in Python:
/// silently not a full reorder, fall back to `color_domain`, no error. A
/// curated SUBSET is a legend-only filter (`apply_color_legend` bakes it
/// verbatim), never a usable domain here.
pub(super) fn grouped_legend_domain(
    color_domain: &[String],
    legend: &LegendConfig,
    wide_labels: &HashMap<String, String>,
) -> Vec<String> {
    let Some(values) = &legend.values else {
        return color_domain.to_vec();
    };
    // `wide_legend_aliases`: a wide entry answers to its raw name and its painted label.
    let painted = |entry: &String| crate::common::wide_series_display(entry, wide_labels);
    let mut folded_to_entries: std::collections::HashMap<String, Vec<&String>> =
        std::collections::HashMap::new();
    for entry in color_domain {
        let mut keys = vec![fold_legend_token(entry)];
        let label_key = fold_legend_token(&painted(entry));
        if label_key != keys[0] {
            keys.push(label_key);
        }
        for key in keys {
            folded_to_entries.entry(key).or_default().push(entry);
        }
    }
    let mut resolved: Vec<String> = Vec::with_capacity(values.len());
    for token in values {
        let hit = if let Some(entry) = color_domain.iter().find(|e| *e == token || painted(e) == *token) {
            Some(entry.clone())
        } else {
            match folded_to_entries.get(&fold_legend_token(token)).map(Vec::as_slice) {
                Some([single]) => Some((*single).clone()),
                _ => None,
            }
        };
        match hit {
            Some(entry) if !resolved.contains(&entry) => resolved.push(entry),
            Some(_) => {}
            None => return color_domain.to_vec(),
        }
    }
    let mut sorted_resolved = resolved.clone();
    sorted_resolved.sort();
    let mut sorted_domain = color_domain.to_vec();
    sorted_domain.sort();
    if sorted_resolved == sorted_domain {
        resolved
    } else {
        color_domain.to_vec()
    }
}

/// Build `(label, fill)` legend entries in *display* order.
///
/// Fill color keys off `palette_domain`; entries are sequenced independently
/// by `legend_domain` — production's
/// `apply_legend_entry_order` pins the legend to `order` (horizontal) or
/// `reversed(order)` (vertical) for a stacked bar; see `common::sorted_series_by_stack_order`'s
/// doc comment. A grouped (`stack = "none"`) color bar has no stack order to
/// pin to, so its caller passes `grouped_legend_domain` as `legend_domain` —
/// alphabetical display order, the VL default, unless an authored
/// `legend.values` full reordering wins.
/// One `(painted label, fill)` per legend series: the fill keys on the raw
/// series name, the label is its wide-measure display text
/// (`common::wide_series_display`; a non-wide series is its own label).
pub(super) fn stacked_legend_entries(
    legend_domain: &[String],
    palette_domain: &[String],
    palette: &[String],
    scale: Option<&crate::common::CategoryColorScale>,
    wide_labels: &HashMap<String, String>,
) -> Result<Vec<(String, String)>, String> {
    legend_domain
        .iter()
        .map(|s| {
            let fill = crate::common::series_color(s, palette_domain, palette, scale)?;
            Ok((crate::common::wide_series_display(s, wide_labels), fill))
        })
        .collect()
}

/// Each color series' painted legend label paired with the value its marks link by.
pub(super) fn bar_link_keys<'a>(r: &BarResolved, color_domain: &'a [String], wide_labels: &HashMap<String, String>) -> Vec<(String, &'a str)> {
    color_domain
        .iter()
        .map(|s| (crate::common::wide_series_display(s, wide_labels), crate::common::wide_link_key(s, !r.wide_measures.is_empty(), r.series_from_measures)))
        .collect()
}
