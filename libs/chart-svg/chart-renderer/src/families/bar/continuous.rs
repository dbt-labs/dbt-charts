//! The vertical bar on a quantitative x: a linear x scale padded by the half-bar reservation, in a closed-form
//! arm of its own.

use crate::common::{VALUE_AXIS_BREATHING_ROOM, axis_title_text, format_y_label, row_num, row_str};
use crate::diagnostic::Diagnostic;
use crate::families::axis_caption::value_axis_caption;
use crate::families::cartesian::LinearScale;
use crate::families::facet_compose::PanelGeometry;
use crate::families::layers::{AreaFrame, BarLayerMark, DualAxisTitles, LayerAxis, LayerAxisFrame, LayerAxisInk, LayerScale, LayerSide, LayerStack, MOVED_BASE_LABEL_PAD, OverlayAxisY, OverlayLayer, PointColor, YSides, claim_layers, layer_color_groups, layer_color_promoted, layer_color_text, layer_series_text, merged_axis_title, merged_legend_title, panel_layer_rows, placed_bar_layer_marks, scope_group_top};
use crate::families::legend::{LegendEntry, LegendSymbol, drawn_symbol_legend, legend_fit_bounds, legend_origin, legend_slide, legend_title_text, vega_legend};
use crate::families::{facet_compose, multiples};
use crate::frame::{ChartFrame, FrameTitle};
use crate::layout::Rect;
use crate::svg::py_float_str;
use crate::{Padding, Payload, vega_fit};

use measure::FontBook;
use scene::{Attrs, Node};
use serde_json::Value;
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};

use super::axes::ContinuousXAxis;
use super::bind::zero_label;
use super::domain::nice_shared_scale;
use super::labels::{LabelPoint, LabelSpec, label_font, label_spot, mark_labels, zero_label_at};
use super::layout::Drawn;
use super::legend::{grouped_legend_domain, numeric_legends_of, scatter_legend_entries};
use super::marks::{Rounded, bar_passes, baseline_rule_layers, baseline_rules, collapsed_bars, hover_band_for, layer_bar, layer_bar_labels, link_hrefs, unity_rule};
use super::tooltip::{NativeTitles, bar_datum_aria};
use super::{BarResolved, ContinuousWidth, MarkStyle, continuous_pad_px, min_adjacent_step, min_gap, x_resolution};

/// The overlay layers a quantitative-x bar draws on its one linear x scale, in
/// authored order, with the payload their own datasets are read from.
pub(super) struct XLayers<'a> {
    pub(super) all: &'a [OverlayLayer],
    pub(super) payload: &'a Payload,
}

/// Thin wrapper: reads the whole-chart box/rows off `payload` and calls
/// `render_vertical_continuous_x_panel` — the single-chart path's own "one
/// panel, the whole card" call, `suppress_x_axis: false`.
pub(super) fn render_vertical_continuous_x(
    payload: &Payload,
    fonts: &FontBook,
    r: &BarResolved,
    layers: &XLayers,
) -> Result<Option<Drawn>, Diagnostic> {
    Ok(render_vertical_continuous_x_panel(
        &payload.rows,
        &payload.rows,
        payload.width,
        payload.height,
        payload.padding.as_ref(),
        // The chart's OWN effective background — see `BarResolved::background`.
        r.background.as_str(),
        fonts,
        payload.board_style.as_ref(),
        r,
        layers,
        false,
        // One panel, the whole card: it paints its own value axis.
        true,
        true,
        // The single-chart wrapper has no `row_footer` role at all — its
        // own value axis anchors flush at `plot_w`.
        0.0,
        None,
        false,
        false,
        None,
    )?
    .map(|(drawn, _geom)| drawn))
}

/// The sub-shapes the continuous-x arm draws, whatever the x scale: a single unstacked
/// series of numeric values with no value labels, on a baked right value axis.
pub(super) fn continuous_x_shape_ok(rows: &[serde_json::Map<String, Value>], r: &BarResolved) -> bool {
    // Production's routing predicate is `is_vega_numeric_value`
    // (type_inference.py), which deliberately does NOT accept numeric
    // strings — a string-typed numeric column compiles a NOMINAL band
    // scale. Only genuine JSON numbers (finite by construction) pass.
    let field_number_typed = |field: &str| {
        let mut saw = false;
        for row in rows {
            if let Some(v) = row.get(field) {
                if v.is_null() {
                    continue;
                }
                if !v.is_number() {
                    return false;
                }
                saw = true;
            }
        }
        saw
    };
    let ax_x = &r.style.axis_x;
    let ax_y = &r.style.axis_y;
    let x_cont = ax_x.scale.get("continuous");
    let x_scale_untouched = x_cont
        .and_then(|c| c.get("type"))
        .and_then(Value::as_str)
        .is_none_or(|t| t == "linear")
        && x_cont.and_then(|c| c.get("domain")).is_none_or(Value::is_null)
        && ax_x.scale.get("nice").and_then(Value::as_bool) != Some(false);
    let labels_on = r.style.mark.labels.as_ref().is_some_and(|l| l.visible);
    field_number_typed(&r.y)
        && r.color.is_none()
        && r.stack == "none"
        && !labels_on
        && x_scale_untouched
        && ax_y.position.as_deref() == Some("right")
        && !ax_y.tick_values.is_empty()
}

/// The x scale is linear only where the one x-type law resolves it
/// quantitative: a year-shaped column is rewritten to dates first, and an
/// authored `axis_x.type` overrides the data.
pub(super) fn x_is_linear(r: &BarResolved, chart_rows: &[serde_json::Map<String, Value>]) -> Result<bool, String> {
    Ok(x_resolution(r, chart_rows)?.resolution.vl_type == crate::families::temporal::VegaType::Quantitative)
}

/// One bar's horizontal extent in plot pixels, and the x its value label centers on.
pub(super) struct Placed {
    pub(super) left: f64,
    pub(super) width: f64,
    pub(super) center: f64,
}

/// The quantitative-x arm's routing gate: the sub-shapes
/// `render_vertical_continuous_x_panel` draws on a linear x scale.
pub(super) fn continuous_x_gate(
    rows: &[serde_json::Map<String, Value>],
    chart_rows: &[serde_json::Map<String, Value>],
    r: &BarResolved,
) -> Result<bool, String> {
    Ok(x_is_linear(r, chart_rows)? && r.style.axis_x.labels.format.is_some() && continuous_x_shape_ok(rows, r))
}

/// Closed-form arm for the QUANTITATIVE-x vertical bar: an all-numeric x
/// field compiles to a LINEAR x scale (`type_inference.py`), padded by the
/// half-bar reservation (`emitters/bar.py`: `padding = max(resolved,
/// effective_bar_size/2)`) and niced (d3 count 10, after the pixel padding
/// expands the domain by `span·p/(pw − 2p)` — Vega's `padLinear`); bar width
/// is `continuous_bar_size_prop`'s clamp — `clamp(min_adjacent_step_px −
/// gap, min_size, max_size)` — or the literal authored `size`. Layout goes
/// through `vega_fit` like the band fit arm. Returns Ok(None) for any
/// sub-shape this arm doesn't model (non-numeric x, multi-series, stacking,
/// value labels, an authored x-scale transform) — the caller falls through
/// to the band legacy, the pre-port status quo.
#[allow(clippy::too_many_arguments)]
pub(super) fn render_vertical_continuous_x_panel(
    rows: &[serde_json::Map<String, Value>],
    chart_rows: &[serde_json::Map<String, Value>],
    svg_w: f64,
    svg_h: f64,
    pad: Option<&Padding>,
    bg: &str,
    fonts: &FontBook,
    board_style: Option<&crate::BoardStyle>,
    r: &BarResolved,
    layers: &XLayers,
    // A facet composite's own shared x-axis is drawn ONCE by the composer,
    // below the last row — see `render_vertical_panel`'s own doc comment on
    // the same flag for the full rationale; this arm mirrors it exactly.
    suppress_x_axis: bool,
    // Only the last column of a row paints the shared value axis; every other
    // panel still reserves its own gutter (for row alignment). Always `true`
    // off a facet.
    paint_value_axis: bool,
    // A single-column (rows-only) facet folds the gutter into its row's ink; a
    // multi-column row reserves it once at the composer instead.
    fold_axis_gutter: bool,
    // Real Vega's `row_footer` (a rows-only facet's shared value axis)
    // anchors at `ceil(cell x2)` (`vega-view-transforms/src/layout/
    // grid.js:262`, `ceil` from `grid.js:13`'s own `agg` callback) — the
    // mark's own right overhang past `plot_w`, symmetric with `left_mark_
    // overhang`'s own left reserve, never `plot_w` alone. `0.0` off the
    // single-chart wrapper.
    value_axis_right_overhang: f64,
    // A facet panel's canonical key and its place in `multiples::regroup`
    // order, which annotations name and clip ids number.
    panel: Option<(&str, usize)>,
    // The composer paints the only value axis (a left one with no mirror), so this panel reserves and paints none.
    ghost_only: bool,
    // The value axis is a left one rendered on the right edge (`render_facet`): it paints the mirror
    // ghost's labels there and leaves the primary's to the composer's `row_header`.
    left_flip: bool,
    // What the axis titles wrap against; `None` off a facet.
    title_boxes: Option<multiples::FacetTitleBoxes>,
) -> Result<Option<(Drawn, Option<PanelGeometry>)>, Diagnostic> {
    let paint_value_axis = paint_value_axis && !ghost_only;
    if !continuous_x_gate(rows, chart_rows, r)? {
        return Ok(None);
    }
    let ax_x = &r.style.axis_x;
    let x_edge = ax_x.x_edge();
    let ax_y = &r.style.axis_y;
    let x_title_text = axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels)?;
    let ((x_extent, x_panels), (y_extent, y_panels)) = title_boxes.unwrap_or(((svg_w, 1.0), (svg_h, 1.0)));
    let x_title_paint = crate::common::wrapped_axis_title(&r.id, "x_label", &x_title_text, &ax_x.title.font, x_extent, x_panels, fonts)?;
    let pad_l = pad.map(|p| p.left).unwrap_or(0.0);
    let pad_r = pad.map(|p| p.right).unwrap_or(0.0);
    let pad_t = pad.map(|p| p.top).unwrap_or(0.0);
    let pad_b = pad.map(|p| p.bottom).unwrap_or(0.0);
    let avail_w = svg_w - pad_l - pad_r;
    let view_w = crate::view_width(svg_w, board_style, crate::ViewScale::Continuous)?;

    // ── Data ─────────────────────────────────────────────────────────────
    let mut bars_data: Vec<(f64, f64)> = Vec::new();
    let mut bar_x_text: Vec<String> = Vec::new();
    // The row key each bar's link template reads its channels through.
    let mut bar_cat: Vec<String> = Vec::new();
    let mut bar_extra: Vec<Vec<String>> = Vec::new();
    let mut y_dmin = f64::INFINITY;
    let mut y_dmax = f64::NEG_INFINITY;
    // The step ladder reads the x COLUMN alone (production sorts the
    // distinct x values regardless of each row's y), but the SCALE extent
    // excludes rows Vega's invalid-filter drops (a null-y row contributes
    // no mark and no domain — scenegraph-verified byte-identical with the
    // row deleted) — the two are genuinely asymmetric in production.
    let mut xs: Vec<f64> = Vec::new();
    for row in rows {
        let Some(xv) = row_num(row, &r.x) else { continue };
        xs.push(xv);
        let Some(yv) = row_num(row, &r.y) else { continue };
        y_dmin = y_dmin.min(yv);
        y_dmax = y_dmax.max(yv);
        bars_data.push((xv, yv));
        bar_x_text.push(row_str(row, &r.x));
        bar_cat.push(row_str(row, &r.x));
        bar_extra.push(crate::families::tooltip_extra::rows_for(&r.tooltip, row)?);
    }
    if bars_data.is_empty() {
        return Ok(None);
    }
    // Each layer draws this panel's rows of the base query, or its own dataset; every
    // layer shares the linear x scale, so its own x column joins the scale's extent.
    let layer_rows: Vec<&[serde_json::Map<String, Value>]> = layers
        .all
        .iter()
        .map(|l| panel_layer_rows(rows, suppress_x_axis, false, layers.payload, l.query_name(), r.query_name.as_deref(), "bar"))
        .collect::<Result<_, _>>()?;
    // The shared scales read the whole chart's rows, so every panel agrees on its domains.
    let domain_layer_rows: Vec<&[serde_json::Map<String, Value>]> = layers
        .all
        .iter()
        .map(|l| panel_layer_rows(chart_rows, suppress_x_axis, false, layers.payload, l.query_name(), r.query_name.as_deref(), "bar"))
        .collect::<Result<_, _>>()?;
    // Once any layer pins a side every layer owns a scale and axis; the base takes the other side.
    let y_sides = YSides::of(layers.all);
    let independent_y = y_sides.is_some();
    let val_on_right = y_sides.as_ref().is_none_or(|s| s.base == LayerSide::Right);
    let (x_min_raw, x_max_raw) = bars_data
        .iter()
        .map(|&(x, _)| x)
        .chain(layers.all.iter().zip(&domain_layer_rows).flat_map(|(l, rows)| {
            let x_field = l.x().unwrap_or(&r.x);
            rows.iter().filter_map(move |row| row_num(row, x_field))
        }))
        .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), x| (lo.min(x), hi.max(x)));
    xs.sort_by(f64::total_cmp);
    xs.dedup();
    if x_max_raw <= x_min_raw {
        return Ok(None); // a single x value has no continuous span to draw on
    }
    let (x_min_raw, x_max_raw) = crate::annotation::widen_numbers((x_min_raw, x_max_raw), &r.annotations);
    let min_step = min_gap(xs);

    // ── Width ladder + scale padding ─────────────────────────────────────
    let mark = &r.style.mark;
    let width = ContinuousWidth::of(mark)?;
    let pad_px = continuous_pad_px(mark, ax_x)?;
    // Vega padLinear + nice, per candidate plot width: the pixel padding
    // expands the RAW domain by span·p/(pw − 2p), then d3's nice loop
    // (count 10) rounds the padded extent.
    let x_domain_at = |pw: f64| -> (f64, f64) {
        let span = x_max_raw - x_min_raw;
        let denom = pw - 2.0 * pad_px;
        let frac = if denom > 0.0 { span * pad_px / denom } else { 0.0 };
        d3rs::ticks::d3_nice(x_min_raw - frac, x_max_raw + frac, 10)
    };
    let bar_w_at = |pw: f64, x_lo: f64, x_hi: f64| -> f64 { width.at(min_step * pw / (x_hi - x_lo)) };

    let place = |pw: f64| -> Vec<Placed> {
        let (x_lo, x_hi) = x_domain_at(pw);
        let bw = bar_w_at(pw, x_lo, x_hi);
        bars_data
            .iter()
            .map(|&(xv, _)| {
                let center = (xv - x_lo) / (x_hi - x_lo) * pw;
                Placed { left: center - bw / 2.0, width: bw, center }
            })
            .collect()
    };
    // A bar layer sizes its own bars on the shared scale from its own x column.
    let layer_bars: Vec<Option<(ContinuousWidth, f64, Vec<BarLayerMark>)>> = layers
        .all
        .iter()
        .zip(&layer_rows)
        .map(|(l, rows)| {
            let OverlayLayer::Bar(bl) = l else { return Ok(None) };
            let x_field = bl.x.as_deref().unwrap_or(&r.x);
            Ok(Some((ContinuousWidth::of(&bl.bar_mark)?, min_adjacent_step(rows, x_field), placed_bar_layer_marks(bl, rows, x_field, &crate::families::layers::never_nan, |row| row_num(row, x_field).is_some()))))
        })
        .collect::<Result<_, String>>()?;
    let layer_bar_w_at = |bar: &(ContinuousWidth, f64, Vec<BarLayerMark>), pw: f64, x_lo: f64, x_hi: f64| -> f64 {
        bar.0.at(bar.1 * pw / (x_hi - x_lo))
    };

    // ── Y domain and baked ladder ────────────────────────────────────────
    // Every layer on the shared value scale joins its data extent; a baked
    // `domain_min`/`domain_max` still pins its own end.
    if !independent_y {
        let pairs: Vec<_> = layers.all.iter().zip(domain_layer_rows.iter().copied()).collect();
        let (lo, hi) = crate::families::layers::layers_value_extent(&pairs, &r.x, &crate::families::layers::never_nan);
        y_dmin = y_dmin.min(lo);
        y_dmax = y_dmax.max(hi);
    }
    let y_zero = !ax_y.is_zero_false();
    let authored = ax_y.authored_continuous_domain();
    let (y_min, y_max) = match authored {
        Some((lo, hi)) => crate::families::cartesian::authored_domain_pair(ax_y, lo, hi),
        None => {
            // A bar pins its `domainMin` at the ladder's floor rung, layered or not.
            let zero_floor = crate::common::zero_anchor_pinned_floor(ax_y).unwrap_or_else(|| y_dmin.min(0.0));
            (
                ax_y.domain_min.unwrap_or(if y_zero { zero_floor } else { y_dmin }),
                ax_y.domain_max.unwrap_or(if y_zero { y_dmax.max(0.0) } else { y_dmax }),
            )
        }
    };
    if y_max == y_min || (authored.is_none() && y_max < y_min) {
        return Err("bar: y domain is degenerate (max ≤ min)".into());
    }
    // A layer shares the value scale, which Vega-Lite then nices (`nice_shared_scale`).
    let layered = !layers.all.is_empty();
    let (y_min, y_max, niced_ticks) = if layered && !independent_y {
        nice_shared_scale(ax_y, y_min, y_max)
    } else {
        (y_min, y_max, None)
    };
    let y_span = y_max - y_min;
    let zero_text = zero_label(r, rows, &LinearScale::new(y_min, y_max, svg_h, 0.0))?;
    // Vega-Lite anchors the bars at the zero rule only when the pair, read as
    // authored, holds 0 (`domainHasZero`: `first <= 0 <= last`); otherwise
    // (a descending pair too) they hang from the plot bottom.
    let rule_at = 0.0f64.clamp(y_min.min(y_max), y_min.max(y_max));
    let zero_ref = match authored {
        Some((lo, hi)) if !(lo <= 0.0 && 0.0 <= hi) => y_min,
        _ => rule_at,
    };
    let y_ticks = niced_ticks
        .unwrap_or_else(|| crate::families::cartesian::ladder_within_domain(&ax_y.tick_values, y_min, y_max));
    let y_ruler = ax_y.ruler.as_ref();
    let y_tick_label = ax_y.tick_label.as_ref();
    let y_fmt = ax_y.labels.format.as_ref();
    let y_labels: Vec<String> = y_ticks
        .iter()
        .enumerate()
        .map(|(i, &tv)| ax_y.labels.apply_expr("y", tv, format_y_label(tv, y_ruler, y_tick_label, y_fmt, i, y_ticks.len())?, i, y_ticks.len()))
        .collect::<Result<_, _>>()?;

    // ── Fonts, gutters, titles ───────────────────────────────────────────
    let x_font_size = ax_x.labels.font.size_or(11.0);
    let x_family = ax_x.labels.font.family_or("sans-serif");
    let x_label_pad = ax_x.labels.padding.unwrap_or(6.0);
    let x_axis_offset = ax_x.ticks.offset.unwrap_or(0.0);
    let x_ticks_visible = ax_x.ticks.visible.unwrap_or(true);
    let x_tick_len = ax_x.ticks.length.unwrap_or(6.0);
    let x_tick_eff = if x_ticks_visible { x_tick_len } else { 0.0 };
    let x_limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let x_fmt = ax_x.labels.format.as_ref();
    let x_tick_count = ax_x.ticks.count.unwrap_or(6.0) as usize;
    let y_font_size = ax_y.labels.font.size_or(11.0);
    let y_family = ax_y.labels.font.family_or("sans-serif");
    let y_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let y_label_ws: Vec<f64> = y_labels
        .iter()
        .map(|lbl| fonts.measure_shaped(lbl, y_family, y_font_size))
        .collect::<Result<_, _>>()?;
    // A pinned layer moves the base axis to the other edge, where Vega pops the
    // axis's own `labelPadding` for its default.
    let y_label_pad = if val_on_right { ax_y.labels.padding.unwrap_or(6.0) } else { MOVED_BASE_LABEL_PAD };
    let y_gutter = if val_on_right {
        let mf = measure::stand_in_family(y_family);
        let drawn = crate::common::drawn_tick_values(ax_y);
        let mut max_w = 0.0_f64;
        for (i, &tv) in drawn.iter().enumerate() {
            let lbl = ax_y.labels.apply_expr("y", tv, format_y_label(tv, y_ruler, y_tick_label, y_fmt, i, drawn.len())?, i, drawn.len())?;
            max_w = max_w.max(fonts.measure(&lbl, mf, y_font_size)?);
        }
        max_w.min(y_limit) + VALUE_AXIS_BREATHING_ROOM
    } else {
        y_label_pad + y_label_ws.iter().copied().fold(0.0, f64::max).min(y_limit)
    };
    let y_tick_eff = ax_y.tick_len();
    // An authored `labels.align` is the flipped own-side layout the gutter
    // above sizes; with none, Vega-Lite's default grows the labels away from
    // the plot, left-anchored at `labelPadding` (`render_vertical_panel`'s
    // `val_away`).
    let val_away = val_on_right && ax_y.labels.align_kept("right").is_none();
    let y_label_gutter = if val_away { 0.0 } else { y_gutter };
    let ghost_pad = if val_away { y_label_pad } else { y_gutter };
    // `axis_y.mirror`: a ghost axis on the left edge repeating the labels at
    // the primary's own offset (a facet's composer paints its own).
    let ghost = ax_y.mirror_ghost("bar")?;
    let mirror_active = r.multiples.is_none() && ghost.is_some();
    let ghost_texts = ghost.as_ref().map_or(Ok(Vec::new()), |g| g.texts(&y_ticks, &y_labels))?;
    let ghost_ws = ghost
        .as_ref()
        .map_or(Ok(Vec::new()), |g| g.widths(&y_ticks, &y_labels, &y_label_ws, |label| fonts.measure_shaped(label, y_family, y_font_size)))?;
    // The labels the right-hand axis paints and their widths.
    let (right_texts, right_ws) = if left_flip { (&ghost_texts, &ghost_ws) } else { (&y_labels, &y_label_ws) };
    let y_label_y_anchor = |pw: f64| -> (f64, &'static str) {
        if !val_on_right {
            (-(y_tick_eff + y_label_pad), "right")
        } else if val_away {
            (pw + y_tick_eff + y_label_pad, "left")
        } else {
            (pw + y_tick_eff + y_gutter, "right")
        }
    };

    let y_title_text = axis_title_text(&r.y, r.y_label.as_deref(), &r.field_labels)?;
    let y_axis_title = if independent_y {
        y_title_text.clone()
    } else {
        let layer_titles = layers
            .all
            .iter()
            .map(|l| axis_title_text(l.y(), l.label(), &r.field_labels))
            .collect::<Result<Vec<_>, _>>()?;
        merged_axis_title(std::iter::once(y_title_text.clone()).chain(layer_titles))
    };
    let y_title_paint = crate::common::wrapped_axis_title(&r.id, "y_label", &y_axis_title, &ax_y.title.font, y_extent, y_panels, fonts)?;
    let dual_titles = DualAxisTitles::new(independent_y, ax_y, r.y_label.as_deref());
    let (val_title_style, layer_title_style) = (dual_titles.base(ax_y), dual_titles.layer(ax_y));
    let y_title_extra = crate::common::y_title_extra(val_title_style, &y_title_paint);
    let x_title_extra = crate::common::x_title_extra(&ax_x.title, &x_title_paint);
    let x_title_w = crate::common::widest_title_line(&ax_x.title, &x_title_paint, fonts)?;
    let y_title_w = crate::common::widest_title_line(val_title_style, &y_title_paint, fonts)?;
    // Each layer's own scale, ladder, labels and title once any layer pins a side.
    let (scaled, layer_zero_rules) =
        y_sides.as_ref().map_or_else(Default::default, |sides| sides.scaled(layers.all, &layer_rows, &r.x, ax_y));
    let layer_axes: Vec<LayerAxis> = scaled
        .iter()
        .zip(&layer_bars)
        .enumerate()
        .map(|(i, (p, bars))| {
            let extent = match bars {
                Some((_, _, marks)) => marks.iter().fold((0.0_f64, 0.0_f64), |(lo, hi), m| {
                    (lo.min(m.from).min(m.to), hi.max(m.from).max(m.to))
                }),
                None => p.value_extent(p.rows.iter()),
            };
            let extent = crate::annotation::widen_y(extent, &r.annotations, crate::annotation::YAxis::Layer(i), None, false);
            p.axis(
                extent,
                &LayerAxisFrame {
                    family: "bar",
                    ax_y,
                    fonts,
                    font_family: y_family,
                    font_size: y_font_size,
                    label_limit: y_limit,
                    tick_size: y_tick_eff,
                    title_style: layer_title_style,
                    field_labels: &r.field_labels,
                },
            )
        })
        .collect::<Result<_, _>>()?;
    // The chart's own ink past the plot, set by the fit's ink closure on every
    // pass; the last write is the drawn size's.
    let annotation_reach = Cell::new(crate::annotation::Reach::default());
    // The annotation maps at a plot size: the scales the bars draw with.
    let annotation_ctx = |pw: f64, ph: f64| {
        use crate::annotation::{AnnotationCtx, AxisMap, Orientation, PlotRect};
        let y = AxisMap::Linear { domain: (y_min, y_max), range: (ph, 0.0) };
        let layer_y = crate::families::layers::layer_y_maps(&y, &layer_axes, layers.all.len(), ph);
        AnnotationCtx {
            annotations: &r.annotations,
            chart_id: &r.id,
            panel: panel.map(|(key, _)| key),
            plot: PlotRect { x: 0.0, y: 0.0, w: pw, h: ph },
            reach: annotation_reach.get(),
            x: AxisMap::Linear { domain: x_domain_at(pw), range: (0.0, pw) },
            y,
            layer_y,
            orientation: Orientation::Vertical,
            fonts,
            canvas: r.canvas.as_str(),
            clip_id: format!("{}-ann{}", crate::contract::safe_svg_id(&r.id), panel.map_or(0, |(_, ordinal)| ordinal)),
            occupied: Vec::new(),
        }
    };
    // A line or scatter layer's value labels join the fit like any text mark:
    // per layer row its x, measure, caption width and spot, on the shared scale
    // or the layer's own.
    let layer_label_fit: Vec<(usize, crate::families::layers::LayerLabelStyle, Vec<(f64, f64, f64, crate::common::CaptionSpot)>)> =
        layers
            .all
            .iter()
            .zip(&layer_rows)
            .enumerate()
            .filter_map(|(i, (layer, rows))| {
                let (ml, is_house) = match layer {
                    OverlayLayer::Line(l) => (l.visible_labels()?, l.label_is_house),
                    OverlayLayer::Scatter(sl) => (&sl.point_mark.labels, sl.label_is_house),
                    OverlayLayer::Bar(_) => return None,
                };
                ml.visible.then_some((i, layer, ml, is_house, *rows))
            })
            .map(|(i, layer, ml, is_house, rows)| {
                let style = crate::families::layers::LayerLabelStyle::new(ml, is_house, rows, board_style)?;
                let x_field = layer.x().unwrap_or(&r.x);
                let items = crate::common::valued_rows(rows.iter(), layer.y())
                    .into_iter()
                    .filter_map(|(row, val)| row_num(row, x_field).map(|x| (x, val, row)))
                    .map(|(x, val, row)| {
                        let caption = style.caption(row, val, None).map_err(|e| format!("bar: {e}"))?;
                        Ok((x, val, style.width(fonts, &caption.text)?, caption.spot))
                    })
                    .collect::<Result<Vec<_>, String>>()?;
                Ok((i, style, items))
            })
            .collect::<Result<_, String>>()?;

    // The same title block the band arms compile: production runs
    // `apply_title_overflow_to_spec` on this unit spec too, so a title or
    // subtitle wider than the padded card wraps to two measured lines and
    // the group grows by `fontSize + 2` per extra line.
    let title = FrameTitle::new(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        r.style.title_font.as_ref(),
        r.title_style.as_ref(),
        avail_w,
        fonts,
    )?;
    title.warn_truncated(&r.id);

    // A layer forces the legend on over a colorless base: one label-scale
    // entry per painted series, each carrying its own mark's glyph.
    let legend_shown = r.legend.visible && layered;
    let base_entries = [(y_title_text.clone(), r.style.single_series_fill.clone())];
    let mut layer_scale = LayerScale::new(&base_entries, false);
    let claim_pairs: Vec<_> = layers.all.iter().zip(layer_rows.iter().copied()).collect();
    let claims = claim_layers(&mut layer_scale, &claim_pairs, &r.field_labels)?;
    let numeric_legends = numeric_legends_of(None, layers.all.iter().zip(&claims), &r.legend, &r.id)?;
    let ring = LegendSymbol::BorderedCircle(mark.border.color.as_deref().unwrap_or("none"));
    let has_line = layers.all.iter().any(|l| matches!(l, OverlayLayer::Line(_)));
    let scatter_entries: Vec<(String, String, bool)> = if legend_shown && !has_line {
        scatter_legend_entries(&base_entries, &claims, &layer_scale, &r.legend, false, &layers.all.iter().map(|l| matches!(l, OverlayLayer::Scatter(_))).collect::<Vec<_>>())
    } else {
        Vec::new()
    };
    let legend_entries: Vec<LegendEntry> = if legend_shown && has_line {
        let entries = layer_scale.legend_entries(&base_entries, LegendSymbol::Square);
        // An authored full-order `legend.values` reorders the combo's entries.
        match &r.legend.values {
            Some(_) => {
                let labels: Vec<String> = entries.iter().map(|e| e.label.to_string()).collect();
                grouped_legend_domain(&labels, &r.legend, &HashMap::new())
                    .iter()
                    .map(|label| {
                        entries
                            .iter()
                            .find(|e| e.label == label)
                            .cloned()
                            .expect("grouped_legend_domain only returns labels from its own input")
                    })
                    .collect()
            }
            None => entries,
        }
    } else {
        scatter_entries
            .iter()
            .map(|(label, fill, circle)| LegendEntry { label, fill, symbol: circle.then_some(ring), series_key: None, recede: None })
            .collect()
    };
    let legend_title = merged_legend_title(
        legend_title_text("", &r.legend, &r.field_labels)?,
        false,
        crate::families::layers::shared_color_fields(layers.all.iter().zip(layer_rows.iter().copied())),
        &r.legend,
    );
    let legend = (legend_shown && !legend_entries.is_empty())
        .then(|| {
            vega_legend(&legend_entries, numeric_legends.unlisted_line, &legend_title, &r.legend, fonts)
                .and_then(|lg| numeric_legends.stack_onto(lg, &r.legend, fonts, (svg_w, svg_h)))
        })
        .transpose()?;
    // The legend's origin in plot coordinates, set on every fit pass; the last
    // write is the final layout the chart is drawn at.
    let legend_xy = Cell::new((0.0, 0.0));

    let stroke_color = mark.border.color.as_deref().unwrap_or("#FAFAFA");
    let stroke_w = mark.border.width.unwrap_or(0.0);
    let corner_r = mark.border.radius.unwrap_or(0.0);
    let threshold = ax_y
        .grid
        .threshold
        .as_ref()
        .filter(|_| ax_y.grid.visible.unwrap_or(false) && ax_y.grid.threshold_visible());
    let rules = baseline_rules(ax_y, false, unity_rule(r, rows), !rows.is_empty());
    // The genuine-zero label: one text mark per bar, blank unless the bar is
    // painted invisibly short (`zero_label_blank`).
    let zero_label_font = zero_text.as_ref().map(|ml| label_font(ml, board_style)).transpose()?;
    let zero_label_widths: Vec<f64> = match (&zero_text, &zero_label_font) {
        (Some(ml), Some(font)) => {
            let spec = LabelSpec::new(ml, false, false, rows, &r.y);
            bars_data
                .iter()
                .map(|&(_, yv)| fonts.measure(&spec.text(None, yv, None)?.0, &font.family, font.size))
                .collect::<Result<_, _>>()?
        }
        _ => Vec::new(),
    };

    // ── Fit ──────────────────────────────────────────────────────────────
    // A facet panel (`suppress_x_axis`) compiles under real Vega's
    // `autosize: pad`: the plot rect is the composer's own declared
    // `svg_w`×`svg_h` (`panel_w`×`child_height`) verbatim, and axis ink
    // overflows outward — never the `fit` shrink this single-chart wrapper
    // uses to keep ink inside a fixed card. Same `ink` closure either way
    // (`vega_fit::pad_plot`'s doc comment): only which law resolves the
    // margins differs.
    let mut fit_err: Option<String> = None;
    // Real Vega's `viewSizeLayout` (`ViewLayout.js`) computes the
    // composite's own outer top margin as `ceil(-viewBounds.y1)`, where
    // `viewBounds` unions every row-header/row-footer/col-header/col-footer
    // ROLE group's own bounds across the WHOLE facet — never the per-row
    // `cell` ink `render_facet`'s own `dy_step` uses. A row-facet's shared
    // value axis IS that role (`row_footer`/`row_header`, per this file's
    // "a row-facet's shared axis is a separate role" law above), and only
    // the TOPMOST row's own copy sits unshifted at y=0, so only ITS
    // topmost tick label's own unclamped overhang can push `viewBounds.y1`
    // negative — every other row's identical copy is pushed down by its
    // own `dy_step` translate and never crosses 0. Tracked here (the SAME
    // `label_ink.y1` this closure already computes, before the `suppress_
    // x_axis` clamp below throws it away) so `render_facet` can reserve it
    // ONCE at the composite's own top, exactly mirroring `ceil(-viewBounds.
    // y1)` — probe-verified against a live `vega@6.2.0` scenegraph on the
    // real compiled `facet-panel-axis-title` spec: `row_footer`'s own row-0
    // axis group bounds at `y1=-3.7880813143970897` and the frame root's
    // own bounds.y1/`view._origin[1]` agree exactly (`ceil(3.788...) = 4`)
    // — the same 4px this composite's own top origin was short by.
    let top_overhang = std::cell::Cell::new(0.0_f64);
    let mut ink_pass = |pw: f64, ph: f64, b: &mut vega_fit::Bounds| {
            if fit_err.is_some() {
                return;
            }
            let ink = |b: &mut vega_fit::Bounds| -> Result<(), String> {
                let placed = place(pw);
                let (x_lo, x_hi) = x_domain_at(pw);
                let x_span = x_hi - x_lo;
                let axis_edge = if val_on_right && !ghost_only {
                    pw + y_tick_eff + y_label_gutter + y_title_extra
                } else if val_on_right {
                    pw
                } else {
                    -(y_tick_eff + y_gutter + y_title_extra)
                };
                b.union(&if val_on_right {
                    vega_fit::Bounds::new(0.0, 0.0, axis_edge, ph)
                } else {
                    vega_fit::Bounds::new(axis_edge, 0.0, 0.0, ph)
                });
                if y_title_extra > 0.0 && !suppress_x_axis {
                    let x = if val_on_right { pw + y_tick_eff + y_gutter + y_title_extra } else { axis_edge };
                    b.union(&vega_fit::Bounds::new(x, ph / 2.0 - y_title_w / 2.0, x, ph / 2.0 + y_title_w / 2.0));
                }
                for la in &layer_axes {
                    la.union_axis_ink(b, (pw, ph), 0.0, y_limit, y_font_size);
                }
                for (ghost_i, (&tv, &lw)) in y_ticks.iter().zip(right_ws.iter()).enumerate() {
                    let y = ph * (y_max - tv) / y_span;
                    let (label_x, label_align) = y_label_y_anchor(pw);
                    let mut label_ink = vega_fit::text_ink(
                        label_x,
                        y,
                        lw.min(y_limit),
                        y_font_size,
                        label_align,
                        "middle",
                    );
                    // A facet panel's value axis is real Vega's `row_footer`
                    // — a role structurally separate from `cell` that never
                    // shifts a row's own bars to make room for a tick label
                    // overhanging past the row's own top/bottom edge (only
                    // its own gutter WIDTH matters to `cell`'s neighbor,
                    // never its label ink's vertical reach). Clamping to
                    // `[0, ph]` keeps that width contribution while
                    // dropping the vertical one only `render_vertical`'s
                    // own single-chart `fit_plot` wants.
                    if suppress_x_axis {
                        top_overhang.set(top_overhang.get().min(label_ink.y1));
                        label_ink.y1 = label_ink.y1.max(0.0);
                        label_ink.y2 = label_ink.y2.min(ph);
                    }
                    if !ghost_only {
                        b.union(&label_ink);
                    }
                    if mirror_active {
                        b.union(&vega_fit::text_ink(-(y_tick_eff + ghost_pad), y, ghost_ws[ghost_i].min(y_limit), y_font_size, "right", "middle"));
                    }
                }
                // A facet panel that suppresses its own x-axis (every row
                // but the last, drawn once by the composer) reserves no ink
                // for it here either — the whole point of suppressing is to
                // give that room back to the plot, matching the band arm's
                // own `suppress_x_axis` gate on its own ink union.
                let mut x_axis_y2 = ph;
                let mut x_axis_y1 = 0.0_f64;
                if !suppress_x_axis {
                    let row_y = x_edge.at(ph, x_axis_offset + x_tick_eff + x_label_pad);
                    for tv in d3rs::ticks::d3_ticks(x_lo, x_hi, x_tick_count) {
                        let lbl = ax_x.format_only_label(x_fmt.expect("gated above"), tv).map_err(|e| format!("bar x label format: {e}"))?;
                        let pos = (tv - x_lo) / x_span * pw;
                        let w = fonts.measure(&lbl, x_family, x_font_size)?;
                        let align = if pos <= 1.0 {
                            "left"
                        } else if pos >= pw - 1.0 {
                            "right"
                        } else {
                            "center"
                        };
                        let ink = vega_fit::text_ink(pos, row_y, w.min(x_limit), x_font_size, align, x_edge.baseline());
                        x_axis_y2 = x_axis_y2.max(ink.y2);
                        x_axis_y1 = x_axis_y1.min(ink.y1);
                        b.union(&ink);
                    }
                    if x_title_extra > 0.0 {
                        let (y1, y2) = match x_edge {
                            crate::common::XEdge::Bottom => (0.0, ph + (b.y2 - ph).max(0.0) + x_title_extra),
                            crate::common::XEdge::Top => (-((-b.y1).max(0.0) + x_title_extra), ph),
                        };
                        let title_reach = vega_fit::Bounds::new(pw / 2.0 - x_title_w / 2.0, y1, pw / 2.0 + x_title_w / 2.0, y2);
                        x_axis_y2 = x_axis_y2.max(title_reach.y2);
                        x_axis_y1 = x_axis_y1.min(title_reach.y1);
                        b.union(&title_reach);
                    }
                }
                // Annotation text clears the ink so far: axes, titles, marks.
                annotation_reach.set(crate::annotation::Reach::past(b, pw, ph));
                if let Some(ab) = crate::annotation::bounds(&annotation_ctx(pw, ph)) {
                    b.union(&ab);
                }
                // A facet panel's legend is chart-level: the composer lays it out.
                if let Some(lg) = legend.as_ref().filter(|_| !suppress_x_axis) {
                    let size = (lg.box_w, lg.box_h());
                    let base_axes = if val_on_right {
                        (0.0, pw + y_tick_eff + y_label_gutter + y_title_extra)
                    } else {
                        (-(y_tick_eff + y_gutter + y_title_extra), pw)
                    };
                    let axes = layer_axes.iter().fold(base_axes, |axes, la| la.widen_axes(axes, pw, 0.0));
                    // A rounded, stroked bar compiles to a scope group per datum whose
                    // stroke-expanded top joins `yBounds`, so a top legend clears bars
                    // reaching the plot's top edge; so do a color-grouped line's paths.
                    let linear = LinearScale::new(y_min, y_max, ph, 0.0);
                    let reaches_top = |mark: &MarkStyle, scale: &LinearScale, top_value: f64| {
                        let stroke = mark.border.width.unwrap_or(0.0);
                        let stroked = mark.border.color.as_deref().is_some_and(|c| c != "none") && stroke > 0.0;
                        if mark.border.radius.unwrap_or(0.0) > 0.0 && stroked {
                            (scale.position(top_value) - stroke).min(0.0)
                        } else {
                            0.0
                        }
                    };
                    let base_top = reaches_top(mark, &linear, bars_data.iter().fold(f64::NEG_INFINITY, |hi, &(_, y)| hi.max(y)));
                    let scale_of = |i: usize| if independent_y { layer_axes[i].scale(ph) } else { LinearScale::new(y_min, y_max, ph, 0.0) };
                    let layer_bars_top = layers
                        .all
                        .iter()
                        .zip(&layer_bars)
                        .enumerate()
                        .filter_map(|(i, (layer, bars))| match (layer, bars) {
                            (OverlayLayer::Bar(bl), Some((_, _, marks))) => {
                                let top = if independent_y {
                                    layer_axes[i].value_max
                                } else {
                                    marks.iter().fold(0.0_f64, |hi, m| hi.max(m.from).max(m.to))
                                };
                                Some(reaches_top(&bl.bar_mark, &scale_of(i), top))
                            }
                            _ => None,
                        })
                        .fold(0.0, f64::min);
                    let line_top = layers
                        .all
                        .iter()
                        .zip(&layer_rows)
                        .enumerate()
                        .filter_map(|(i, (layer, rows))| match layer {
                            OverlayLayer::Line(l) => {
                                let scale = scale_of(i);
                                Some(scope_group_top(l, rows, l.x.as_deref().unwrap_or(&r.x), false, |v| scale.position(v)))
                            }
                            _ => None,
                        })
                        .fold(0.0, f64::min);
                    let bars_top = base_top.min(layer_bars_top).min(line_top);
                    let axes = (axes.0, axes.1.max(crate::annotation::text_x2(&annotation_ctx(pw, ph))));
                    let origin = legend_origin(&r.legend.position, axes, (bars_top.min(x_axis_y1), x_axis_y2), (pw, ph), size);
                    legend_xy.set(origin);
                    if let Some(reach) = legend_fit_bounds(&r.legend.position, origin, size) {
                        b.union(&reach);
                    }
                }
                // Bar rect ink (boundStroke: full border width).
                let y0 = ph * (y_max - zero_ref) / y_span;
                let y_rule = ph * (y_max - rule_at) / y_span;
                for (&(_, yv), p) in bars_data.iter().zip(&placed) {
                    let yt = ph * (y_max - yv) / y_span;
                    b.union(&vega_fit::Bounds::new(p.left, yt.min(y0), p.left + p.width, yt.max(y0)).expand(stroke_w));
                }
                // A bar layer's rects paint unclipped, so the fit holds them.
                let scale_of = |i: usize| if independent_y { layer_axes[i].scale(ph) } else { LinearScale::new(y_min, y_max, ph, 0.0) };
                for (i, (layer, bars)) in layers.all.iter().zip(&layer_bars).enumerate() {
                    let (OverlayLayer::Bar(bl), Some(bar)) = (layer, bars) else { continue };
                    let bw = layer_bar_w_at(bar, pw, x_lo, x_hi);
                    let stroke = bl.bar_mark.border.width.unwrap_or(0.0);
                    let scale = scale_of(i);
                    for mark in &bar.2 {
                        let cx = (row_num(mark.row, bl.x.as_deref().unwrap_or(&r.x)).expect("marks keep rows with an x") - x_lo) / x_span * pw;
                        let (from, to) = (scale.position(mark.from).min(ph), scale.position(mark.to).min(ph));
                        b.union(&vega_fit::Bounds::new(cx - bw / 2.0, from.min(to), cx + bw / 2.0, from.max(to)).expand(stroke));
                    }
                }
                for (i, style, items) in &layer_label_fit {
                    let scale = scale_of(*i);
                    for (x, val, width, spot) in items {
                        let cx = (x - x_lo) / x_span * pw;
                        b.union(&style.ink(cx, scale.position(*val).clamp(0.0, ph), *width, spot));
                    }
                }
                if let Some(rules) = rules {
                    if rules.zero {
                        b.union(&vega_fit::Bounds::new(0.0, y_rule, pw, y_rule).expand(rules.width));
                    }
                    if rules.unity {
                        let y1 = ph * (y_max - 1.0) / y_span;
                        b.union(&vega_fit::Bounds::new(0.0, y1 - rules.width / 2.0, pw, y1 + rules.width / 2.0));
                    }
                }
                if let (Some(ml), Some(font)) = (&zero_text, &zero_label_font) {
                    let linear = LinearScale::new(y_min, y_max, ph, 0.0);
                    let spec = LabelSpec::new(ml, false, false, rows, &r.y);
                    // A layer that labels nothing at this size moves nothing.
                    let labels_any = bars_data.iter().any(|&(_, yv)| !zero_label_at(&spec, yv, &linear, ph, font.size).2);
                    for ((&(_, yv), p), &width) in bars_data.iter().zip(&placed).zip(&zero_label_widths).filter(|_| labels_any) {
                        let (extent, side, blank) = zero_label_at(&spec, yv, &linear, ph, font.size);
                        let spot = label_spot(ml, "above", None, p.center, extent, None, side);
                        b.union(&vega_fit::text_ink(spot.x, spot.y, if blank { 0.0 } else { width }, font.size, spot.align(), spot.baseline));
                    }
                }
                Ok(())
            };
            if let Err(e) = ink(b) {
                fit_err = Some(e);
            }
    };
    let m = if suppress_x_axis {
        vega_fit::pad_plot(svg_w, svg_h, &mut ink_pass, title.ink())
    } else {
        vega_fit::fit_plot(view_w, svg_h, (pad_l, pad_r, pad_t, pad_b), &mut ink_pass, title.ink())
    };
    if let Some(e) = fit_err {
        return Err(e.into());
    }
    let stroked_layer_marks = layers.all.iter().any(|l| matches!(l, OverlayLayer::Line(_) | OverlayLayer::Scatter(_)));
    collapsed_bars(false, (m.plot_w, m.plot_h), stroked_layer_marks, (layers.payload.chart_id()?, svg_w), rows, &r.y)?;

    // The composite's own outer top-margin reserve — see `top_overhang`'s
    // own doc comment above. `0.0` whenever no tick's own label crosses
    // the panel's own y=0 (the common case: this only fires for the
    // TOPMOST row of a facet, and only when that row's own topmost tick
    // label's ink reaches above it).
    let top_axis_overhang = (-top_overhang.get()).max(0.0);
    let (plot_w, plot_h) = (m.plot_w, m.plot_h);
    let placed = place(plot_w);
    let (x_lo, x_hi) = x_domain_at(plot_w);
    let x_ticks = crate::families::cartesian::unbaked_ticks(ax_x, x_lo, x_hi, x_tick_count);
    let x_labels: Vec<String> = x_ticks
        .iter()
        .map(|&tv| {
            ax_x.format_only_label(x_fmt.expect("gated above"), tv).map_err(|e| format!("bar x label format: {e}"))
        })
        .collect::<Result<_, _>>()?;

    // ── Emission ─────────────────────────────────────────────────────────
    // Outer canvas dims are `payload.width`/`payload.height` echoed
    // verbatim — vl-convert never re-rounds them the way it rounds
    // INTERNAL mark coordinates (the `{:.3}` grid/rule lines elsewhere in
    // this file); `py_float_str` matches its own float-to-string exactly
    // (whole numbers keep `.0`, others print full precision — found via a
    // repeating-decimal `aspect_ratio` height, `data-chart-height="298.667"`
    // vs production's `"298.6666666666667"`, a `chrome_bytes`-only board-
    // sweep DIFF invisible to the pixel-only per-chart ratchet).
    let frame = ChartFrame::new(view_w, svg_h, (pad_l, pad_r, pad_t, pad_b), bg, py_float_str);
    let mut head: Vec<Node> = title.nodes(m.title_pos, (pad_l + m.left, pad_t + m.top));
    let group_x = pad_l + m.left;
    let group_y = pad_t + m.top;
    let tooltip_labels = !r.style.tooltip_format.spec.is_empty();
    let stamp_legend_series = tooltip_labels && layered;
    let swatch_stroke = mark.border.color.as_deref().unwrap_or("none");
    let mut facet_legend: Option<facet_compose::FacetLegend> = None;
    if let Some(lg) = &legend {
        if suppress_x_axis {
            let mut nodes: Vec<scene::Node> = drawn_symbol_legend(&legend_entries, lg, &r.legend, LegendSymbol::Square(swatch_stroke), 0.0, 0.0, stamp_legend_series).into_iter().collect();
            nodes.extend(numeric_legends.nodes_after(lg, &r.legend, fonts, (m.plot_w, m.plot_h), (0.0, 0.0))?);
            facet_legend = Some(facet_compose::FacetLegend { nodes, box_w: lg.box_w, box_h: lg.box_h() });
        } else {
            let (lx, ly) = legend_xy.get();
            let (dx, dy) = legend_slide(&r.id, &r.legend.position, lg, (m.plot_w, m.plot_h))?;
            head.extend(drawn_symbol_legend(&legend_entries, lg, &r.legend, LegendSymbol::Square(swatch_stroke), group_x + lx + dx, group_y + ly + dy, stamp_legend_series));
            head.extend(numeric_legends.nodes_after(lg, &r.legend, fonts, (m.plot_w, m.plot_h), (group_x + lx + dx, group_y + ly + dy))?);
        }
    }
    let mut plot: Vec<Node> = Vec::new();
    let defined_clips: RefCell<HashSet<String>> = RefCell::new(HashSet::new());

    let (x_lo, x_hi) = x_domain_at(plot_w);
    let x_span = x_hi - x_lo;
    let x_pos = |tv: f64| (tv - x_lo) / x_span * plot_w;
    let y_scale = LinearScale::new(y_min, y_max, plot_h, 0.0);
    let y_pos = |tv: f64| y_scale.position(tv);

    if ax_y.grid.visible.unwrap_or(false) {
        let gc = crate::common::required_ink(ax_y.grid.color.as_deref(), "bar", "axis_y.grid.color")?;
        let gw = ax_y.grid.width.unwrap_or(1.0);
        let go = ax_y.grid.opacity.unwrap_or(1.0);
        let lines: Vec<Node> = y_ticks
            .iter()
            .map(|&tv| {
                let gy = y_pos(tv).round() + 0.5;
                Node::Line(
                    Attrs::new()
                        .set("x1", "0.5")
                        .set("y1", format!("{gy:.3}"))
                        .set("x2", format!("{:.3}", plot_w + 0.5))
                        .set("y2", format!("{gy:.3}"))
                        .set("stroke", gc)
                        .set("stroke-width", gw.to_string())
                        .set("opacity", go.to_string()),
                )
            })
            .collect();
        plot.push(crate::contract::grid_group(lines));
    }
    plot.extend(crate::families::layers::layer_grids(&layer_axes, ax_y, (plot_w, plot_h), "bar")?);

    let annotations = annotation_ctx(plot_w, plot_h);
    plot.extend(crate::annotation::nodes(&annotations, crate::annotation::Plane::Background)?);

    // Bars. A facet panel's marks carry Vega's default description instead
    // (the facet composer owns it), so only the single chart wires the tooltip.
    let fill = &r.style.single_series_fill;
    let y0 = y_pos(zero_ref);
    let y_rule = y_pos(rule_at);
    let floor_span = crate::common::floor_rule_span(plot_w, ax_x, x_ticks_visible, x_ticks.iter().map(|&tv| x_pos(tv).round() + 0.5));
    // The value axis edges the ticks start from: a baseline rule reaches them so its continued tick meets it.
    let val_edge_x = if val_on_right { plot_w + value_axis_right_overhang + 0.5 } else { 0.5 };
    let val_edges: Vec<f64> = if y_tick_eff > 0.0 && paint_value_axis {
        std::iter::once(val_edge_x).chain(mirror_active.then_some(if val_on_right { 0.5 } else { plot_w + 0.5 })).collect()
    } else {
        Vec::new()
    };
    let (rx1, rx2) = crate::common::join_rule_to_edges(floor_span, &val_edges);
    // A value tick at a painted baseline rule's value is that rule continued.
    let val_rules = match (rules, threshold.filter(|_| crate::common::rule_reaches(floor_span, val_edge_x))) {
        (Some(rules), Some(zl)) => crate::common::value_axis_rules(
            0.0,
            [rules.zero.then_some(y_rule), rules.unity.then(|| y_pos(1.0))].into_iter().flatten(),
            zl.stroke(),
            rules.width,
        ),
        _ => Vec::new(),
    };
    let floor_rule = threshold.and_then(|zl| {
        let base = rules.into_iter().flat_map(|rules| [rules.zero.then_some(y_rule), rules.unity.then(|| y_pos(1.0))]).flatten();
        let nested = layer_axes.iter().zip(&layer_zero_rules).filter(|(_, width)| width.is_some()).map(|(la, _)| la.scale(plot_h).position(0.0));
        crate::common::rule_on_edge(base.chain(nested), x_edge.edge_y(plot_h)).then(|| zl.stroke().to_string())
    });
    let x_axis = facet_compose::PanelXAxis::Continuous(ContinuousXAxis {
        x_ticks,
        x_labels,
        x_lo,
        x_span: x_hi - x_lo,
        x_ticks_visible,
        x_tick_len,
        x_axis_drop: x_edge.sign() * x_axis_offset + 0.5,
        floor_rule,
        edge: x_edge,
        x_tick_eff,
        x_label_pad,
        x_family: x_family.to_string(),
        x_font_size,
        x_title_text: x_title_paint.clone(),
        caption: value_axis_caption(&x_title_paint, &ax_x.title, ax_x, (x_lo, x_hi), false)?,
        title_key: (!suppress_x_axis).then_some(crate::contract::LabelKey::X),
    });
    let native = suppress_x_axis.then(|| NativeTitles { x: x_title_text.clone(), series: String::new() });
    let value_title = crate::common::axis_title_text(&r.y, r.y_label.as_deref(), &r.field_labels)?;
    let tooltip_fmt = &r.style.tooltip_format;
    // A layered single-series base names itself as the datum's series, so base
    // and layer rows on the same x don't collapse into one tooltip entry.
    let combo_series = layered.then_some(value_title.as_str());
    let mut hover_band = hover_band_for(r, rows, suppress_x_axis);
    let mut painted_layers = 1;
    let mut bar_arias: Vec<Option<String>> = Vec::with_capacity(bars_data.len());
    let link = r.datum_link(&r.x, crate::href::LinkText::Raw);
    let hrefs = link_hrefs(link.as_ref(), rows, (&r.x, None), false)?;
    // The mirror's invisible rule layer: one rule per datum from its bar's top to the floor.
    let ghosts_on = mirror_active || (r.multiples.is_some() && r.style.axis_y.mirror_requested("bar")?);
    let mut ghost_points: Vec<(Option<String>, f64, f64, String, Option<String>)> = Vec::new();
    let ghost_ink = if ghosts_on {
        crate::common::required_ink(ax_y.labels.font.color.as_deref(), "bar", "axis_y.labels.font.color")?.to_string()
    } else {
        String::new()
    };
    // The base bars, which annotation text tests for its halo.
    let mut under = Vec::new();
    for ((((&(_, yv), x_text), cat), p), extra) in bars_data.iter().zip(&bar_x_text).zip(&bar_cat).zip(&placed).zip(&bar_extra) {
        let yt = y_pos(yv);
        let (top, h) = if yt <= y0 { (yt, y0 - yt) } else { (y0, yt - y0) };
        let aria = bar_datum_aria(
            tooltip_labels, x_text, None, false, None, combo_series, None, &value_title, tooltip_fmt, yv, None, None, extra, native.as_ref(),
        )?;
        bar_arias.push(aria.clone());
        if let Some(band) = hover_band.as_mut() {
            band.push(x_text, "", aria.as_deref(), hrefs.get(&(cat.clone(), String::new())).map(String::as_str), p.left, p.width, Some(yv));
        }
        let key = (cat.clone(), String::new());
        if ghosts_on {
            ghost_points.push((aria.clone(), p.left + p.width / 2.0, yt, ghost_ink.clone(), hrefs.get(&key).cloned()));
        }
        under.push(crate::annotation::DataMark::rect(p.left, top, p.width, h));
        plot.extend(bar_passes(false, p.left,
            top,
            p.width,
            h,
            fill,
            mark.opacity,
            stroke_color,
            stroke_w,
            corner_r,
            Rounded::Positive, // this arm's own negative-value corner law is unmodeled (out of scope)
            false,
            None,
            aria.as_deref(),
            r.selection.mark(r.link_open.anchor(hrefs.get(&key).map(String::as_str))))?);
    }
    if ghosts_on {
        let rules: Vec<crate::contract::GhostRule> = ghost_points
            .iter()
            .map(|(aria, x, y, stroke, href)| crate::contract::GhostRule {
                aria: aria.as_deref(),
                x: *x,
                y: *y,
                stroke,
                anchor: r.link_open.anchor(href.as_deref()),
            })
            .collect();
        plot.push(crate::contract::ghost_layer(&rules, crate::contract::GhostRun::Down(plot_h))?);
    }
    // Overlay layers paint on the shared x scale and a value scale: the base's,
    // or the layer's own once any layer pins a side, each in authored order.
    let describe_layer = |axis: &OverlayAxisY,
                          x_field: &str,
                          name: &str,
                          color_value: Option<&str>,
                          row: &serde_json::Map<String, Value>,
                          val: f64| {
        let series = layer_series_text(name, color_value);
        bar_datum_aria(
            tooltip_labels, &row_str(row, x_field), None, false, None, Some(series.as_str()), None, name,
            axis.tooltip_format_or(tooltip_fmt), val, None, None, &[], native.as_ref(),
        )
    };
    let layer_caption = |x_field: &str, name: &str, row: &serde_json::Map<String, Value>, val: f64| {
        tooltip_labels.then(|| {
            crate::contract::native_label(&[(x_field, row_str(row, x_field)), (name, val.to_string())])
        })
    };
    let paint_layer = |out: &mut Vec<Node>, i: usize, scale: &LinearScale| -> Result<(), Diagnostic> {
        let layer = &layers.all[i];
        let layer_rows = layer_rows[i];
        let layer_clipped = crate::families::layers::layer_clip(layer.layer_type(), layer.y(), layer_rows, ax_y, false, independent_y);
        let x_field = layer.x().unwrap_or(&r.x);
        let x_title = if layer.x().is_some() { x_field } else { x_title_text.as_str() };
        let name = crate::common::axis_title_text(layer.y(), layer.label(), &r.field_labels)?;
        match layer {
            OverlayLayer::Bar(bl) => {
                let (_, _, marks) = layer_bars[i].as_ref().expect("a bar layer sized its bars");
                let bar = layer_bars[i].as_ref().expect("a bar layer sized its bars");
                let promoted = bl.color.as_deref().is_some_and(|cf| layer_color_promoted(layer_rows, cf));
                let bw = layer_bar_w_at(bar, plot_w, x_lo, x_hi);
                // A scale pinned away from the 0 baseline clips the layer and drops its radius.
                let clip = crate::families::layers::layer_clip(layer.layer_type(), layer.y(), layer_rows, ax_y, false, independent_y);
                let mut layer_rects: Vec<Node> = Vec::new();
                for mark in marks {
                    let fill = match (&claims[i].fill, bl.color.as_deref()) {
                        (Some(claimed), _) => claimed.clone(),
                        (None, Some(color)) => claims[i].row_color(&layer_scale, color, mark.row),
                        (None, None) => unreachable!("a colorless bar layer claimed its fill"),
                    };
                    let color_value = bl.color.as_deref().filter(|_| promoted).map(|cf| layer_color_text(mark.row, cf));
                    let aria = describe_layer(&bl.axis_y, x_field, &name, color_value.as_deref(), mark.row, mark.value)?;
                    let cx = x_pos(row_num(mark.row, x_field).expect("marks keep rows with an x"));
                    layer_rects.extend(layer_bar(&|v| scale.position(v), plot_h, clip, bl, mark, (cx - bw / 2.0, bw), &fill, aria.as_deref(), crate::families::layers::layer_link(link.as_ref(), (bl.query_name.as_deref(), r.query_name.as_deref())))?);
                }
                if clip {
                    out.extend(crate::families::layers::clipped_layer(&mut defined_clips.borrow_mut(), (plot_w, plot_h), &r.id, layer_rects));
                } else {
                    out.extend(layer_rects);
                }
                out.extend(layer_bar_labels(
                    bl,
                    layer_rows,
                    marks,
                    &marks.iter().map(|mark| x_pos(row_num(mark.row, x_field).expect("marks keep rows with an x"))).collect::<Vec<_>>(),
                    &|v| scale.position(v),
                    plot_h,
                    |mark| layer_caption(x_title, &name, mark.row, mark.value),
                    crate::families::layers::layer_link(link.as_ref(), (bl.query_name.as_deref(), r.query_name.as_deref())),
                    (board_style, r.canvas.as_str()),
                    (crate::families::layers::LabelCrowd { chart_id: &r.id, layer: i, fonts }, &vec![bw; marks.len()]),
                )?);
            }
            OverlayLayer::Line(l) => {
                let promoted = l.color.as_deref().is_some_and(|cf| layer_color_promoted(layer_rows, cf));
                let paint = |series_rows: &[serde_json::Map<String, Value>],
                             series_color: &str,
                             stacked: Option<&LayerStack>|
                 -> Result<Vec<Node>, String> {
                    crate::families::layers::line_layer_marks(
                        l,
                        series_rows,
                        |row| row_num(row, x_field).map(x_pos),
                        None,
                        |val| crate::families::layers::clamp_unless_clipped(scale.position(val), plot_h, layer_clipped),
                        &AreaFrame { free_y: &|val| scale.position(val), plot: (plot_w, plot_h), clip: crate::families::layers::layer_clip(layer.layer_type(), layer.y(), layer_rows, ax_y, false, independent_y) },
                        false,
                        series_color,
                        r.canvas.as_str(),
                        |row, val| {
                            let color_value = l.color.as_deref().filter(|_| promoted).map(|cf| layer_color_text(row, cf));
                            describe_layer(&l.axis_y, x_field, &name, color_value.as_deref(), row, val)
                        },
                        board_style,
                        |row, val| Ok(layer_caption(x_title, &name, row, val)),
                        None,
                        0.0,
                        stacked,
crate::families::layers::layer_link(link.as_ref(), (l.query_name.as_deref(), r.query_name.as_deref())),
crate::families::layers::LabelCrowd { chart_id: &r.id, layer: i, fonts },
)
                    .map_err(|e| format!("bar: {e}"))
                };
                match l.color.as_deref() {
                    Some(cf) => {
                        let stack = l
                            .area_mark
                            .is_some()
                            .then(|| LayerStack::descending(layer_rows, x_field, cf, &l.y).imputing(layer_rows, crate::families::layers::layer_link(link.as_ref(), (l.query_name.as_deref(), r.query_name.as_deref()))))
                            .transpose()
                            .map_err(|e| format!("bar: {e}"))?;
                        for (value, rows_for_value) in layer_color_groups(layer_rows, cf) {
                            let color = claims[i].group_color(&layer_scale, cf, value.as_deref(), &rows_for_value);
                            out.extend(paint(&rows_for_value, &color, stack.as_ref())?);
                        }
                    }
                    None => out.extend(paint(layer_rows, claims[i].fill.as_deref().expect("an ungrouped line layer claimed its fill"), None)?),
                }
            }
            OverlayLayer::Scatter(sl) => {
                let encoded = |row: &serde_json::Map<String, Value>| {
                    claims[i].row_color(&layer_scale, sl.color.as_deref().expect("an encoded layer groups by color"), row)
                };
                out.extend(crate::families::layers::scatter_layer_marks(
                    sl,
                    layer_rows,
                    sl.size(),
                    1 + i,
                    false,
                    |row| row_num(row, x_field).map(x_pos),
                    None,
                    |val| crate::families::layers::clamp_unless_clipped(scale.position(val), plot_h, layer_clipped),
                    |row, val| {
                        // A pinned layer owns its measure: it names itself and formats in its own axis' format.
                        if independent_y {
                            return describe_layer(&sl.axis_y, x_field, &name, None, row, val);
                        }
                        bar_datum_aria(
                            tooltip_labels, &row_str(row, x_field), None, false, None, combo_series, None, &value_title,
                            tooltip_fmt, val, None, None, &[], native.as_ref(),
                        )
                    },
                    match &claims[i].fill {
                        Some(fill) => PointColor::Claimed(fill),
                        None => PointColor::Encoded(&encoded),
                    },
                    r.canvas.as_str(),
                    board_style,
                    |row, val| Ok(layer_caption(x_title, &name, row, val)),
crate::families::layers::layer_link(link.as_ref(), (sl.query_name.as_deref(), r.query_name.as_deref())), layer_clipped.then_some((plot_w, plot_h)),
crate::families::layers::LabelCrowd { chart_id: &r.id, layer: i, fonts },
)
                .map_err(|e| format!("bar: {e}"))?);
            }
        }
        Ok(())
    };
    if !independent_y {
        for i in 0..layers.all.len() {
            paint_layer(&mut plot, i, &y_scale)?;
        }
    }
    if let Some(band) = &hover_band {
        plot.push(band.node(true, &LinearScale::new(y_min, y_max, plot_h, 0.0), plot_h, r.link_open)?);
    }

    // Zero rule over the bars (BaselineFeature layer order).
    if let (Some(rules), Some(zl)) = (rules, threshold) {
        let zc = zl.stroke();
        let zw = rules.width;
        let rule_ys: Vec<f64> = [rules.zero.then_some(y_rule), rules.unity.then(|| y_pos(1.0))].into_iter().flatten().collect();
        painted_layers += rule_ys.len();
        plot.extend(baseline_rule_layers(1, &rule_ys, |y| {
            Attrs::new()
                    .set("x1", format!("{rx1:.2}"))
                    .set("y1", format!("{y:.2}"))
                    .set("x2", format!("{rx2:.2}"))
                    .set("y2", format!("{y:.2}"))
                    .set("stroke", zc)
                    .set("stroke-width", zw.to_string())
        })?);
    }
    // Each layer on its own scale paints after the base's zero rule, and its own
    // zero rule closes its entry.
    for (i, (la, rule_width)) in layer_axes.iter().zip(&layer_zero_rules).enumerate() {
        let scale = la.scale(plot_h);
        paint_layer(&mut plot, i, &scale)?;
        if let (Some(width), Some(zl)) = (rule_width, threshold) {
            let zc = zl.stroke();
            let layer_edge = if la.side == LayerSide::Right { plot_w + 0.5 } else { 0.5 };
            let (lx1, lx2) = crate::common::join_rule_to_edges(floor_span, &[layer_edge]);
            plot.extend(baseline_rule_layers(painted_layers + 1 + i, &[scale.position(0.0)], |y| {
                Attrs::new()
                    .set("x1", format!("{lx1:.2}"))
                    .set("y1", format!("{y:.2}"))
                    .set("x2", format!("{lx2:.2}"))
                    .set("y2", format!("{y:.2}"))
                    .set("stroke", zc)
                    .set("stroke-width", width.to_string())
            })?);
        }
    }
    if let (Some(ml), Some(font)) = (&zero_text, &zero_label_font) {
        let linear = LinearScale::new(y_min, y_max, plot_h, 0.0);
        let spec = LabelSpec::new(ml, false, false, rows, &r.y);
        let points = bars_data
            .iter()
            .zip(&bar_arias)
            .zip(&placed)
            .zip(&bar_cat)
            .map(|(((&(_, yv), aria), p), cat)| {
                let (extent, side, blank) = zero_label_at(&spec, yv, &linear, plot_h, font.size);
                Ok(LabelPoint {
                    center: p.center,
                    extent,
                    aria: aria.clone(),
                    series_fill: None,
                    blank,
                    side,
                    span: None,
                    text: spec.text(None, yv, None)?.0,
                    href: hrefs.get(&(cat.clone(), String::new())).cloned(),
                    recede: r.selection.recede_of(hrefs.get(&(cat.clone(), String::new())).map(String::as_str)),
                })
            })
            .collect::<Result<Vec<LabelPoint>, String>>()?;
        plot.extend(mark_labels(ml, false, false, true, &points, painted_layers, board_style, 0.0, 0.0, r.canvas.as_str(), r.link_open)?);
    }
    let annotations = crate::annotation::AnnotationCtx { occupied: under, ..annotations };
    plot.extend(crate::annotation::nodes(&annotations, crate::annotation::Plane::Foreground)?);

    // X ticks + labels + title (flush anchors at the plot edges) — a facet
    // panel that suppresses its own x-axis skips this whole call, matching
    // the ink gate above. `plot_left: 0.0`/`plot_bottom: plot_h` since this
    // call sits inside the `<g transform="translate(group_x,group_y)">`
    // opened above (LOCAL coordinates) — the composer's own footer-band
    // call passes real absolute values instead (no enclosing transform).
    if !suppress_x_axis {
        plot.extend(x_axis.nodes(ax_x, 0.0, plot_w, (x_edge.edge_y(plot_h), x_edge.edge_y(plot_h)), fonts)?);
    }

    // Y ticks + labels, on the side the base's axis sits.
    let y_label_color = ax_y.labels.font.color.as_deref().unwrap_or("#626366");
    let y_label_weight = ax_y.labels.font.weight_str();
    let value_axis_start = plot.len();
    let y_caption = value_axis_caption(&y_title_paint, val_title_style, ax_y, (y_min, y_max), false)?;
    if paint_value_axis {
    let (edge_x, outward) = if val_on_right { (plot_w + value_axis_right_overhang + 0.5, 1.0) } else { (0.5, -1.0) };
    let mut y_items = crate::common::value_ticks(
        &ax_y.ticks,
        crate::common::value_tick_rows(0.0, plot_h, y_tick_eff, y_ticks.iter().map(|&tv| y_pos(tv))),
        edge_x,
        outward,
        y_tick_eff,
        &crate::common::thresholds(&val_rules),
    )?;
    let y_label_x = match (val_on_right, val_away) {
        (true, true) => edge_x + y_tick_eff + y_label_pad,
        (true, false) => edge_x + y_tick_eff + y_gutter,
        (false, _) => edge_x - y_tick_eff - y_label_pad,
    };
    let y_label_anchor = if val_away { "start" } else { "end" };
    for (&tv, lbl) in y_ticks.iter().zip(right_texts.iter()) {
        let gy = y_pos(tv) + 0.5 + measure::baseline_shift("middle", y_font_size);
        y_items.push(Node::text(
            Attrs::new()
                .set("x", format!("{y_label_x:.3}"))
                .set("y", format!("{gy:.3}"))
                .set("text-anchor", y_label_anchor)
                .vega("font-family", y_family)
                .set("font-size", y_font_size.to_string())
                .set("font-weight", y_label_weight.as_str())
                .set("fill", y_label_color),
            lbl.as_str(),
        ));
    }

    y_items.extend(crate::common::axis_title_rotated(
        &y_title_paint,
        val_title_style,
        if val_on_right { y_label_x } else { y_label_x - y_label_ws.iter().copied().fold(0.0, f64::max) },
        plot_h / 2.0,
        outward,
        (!suppress_x_axis).then_some(crate::contract::LabelKey::Y),
    ));
    plot.extend(crate::contract::axis_group(crate::contract::AxisChannel::Y, &y_caption, (edge_x, 0.0), y_items)?);
    }
    let value_axis_end = plot.len();
    if mirror_active {
        let mut ghost_items = crate::common::value_ticks(
            &ax_y.ticks,
            crate::common::value_tick_rows(0.0, plot_h, y_tick_eff, y_ticks.iter().map(|&tv| y_pos(tv))),
            0.5,
            -1.0,
            y_tick_eff,
            &crate::common::thresholds(&val_rules),
        )?;
        for (&tv, lbl) in y_ticks.iter().zip(ghost_texts.iter()) {
            let gy = y_pos(tv) + 0.5 + measure::baseline_shift("middle", y_font_size);
            ghost_items.push(Node::text(
                Attrs::new()
                    .set("x", format!("{:.3}", 0.5 - y_tick_eff - ghost_pad))
                    .set("y", format!("{gy:.3}"))
                    .set("text-anchor", "end")
                    .vega("font-family", y_family)
                    .set("font-size", y_font_size.to_string())
                    .set("font-weight", y_label_weight.as_str())
                    .set("fill", y_label_color),
                lbl.as_str(),
            ));
        }
        plot.extend(crate::contract::axis_group(crate::contract::AxisChannel::Y, &y_caption.untitled(), (0.5, 0.0), ghost_items)?);
    }

    // Each layer's own axis, on the side it pins; only the first y title of the
    // chart edits its label, so a layer's stands in for a base title that draws nothing.
    for (la, zero_width) in layer_axes.iter().zip(&layer_zero_rules) {
        let edge_x = match la.side {
            LayerSide::Right => plot_w + 0.5,
            LayerSide::Left => 0.5,
        };
        plot.extend(crate::families::layers::layer_axis(
            la,
            &la.scale(plot_h),
            edge_x,
            (0.0, plot_h),
            &LayerAxisInk {
                font_family: y_family,
                font_size: y_font_size,
                font_weight: &y_label_weight,
                font_color: y_label_color,
                tick_size: y_tick_eff,
                ticks: &ax_y.ticks,
                title_style: layer_title_style,
                zero_rule: zero_width.zip(threshold).filter(|_| crate::common::rule_reaches(floor_span, edge_x)).map(|(width, zl)| (zl.stroke(), width)),
            },
            (!suppress_x_axis && crate::common::axis_title_band(val_title_style, &y_title_paint) == 0.0)
                .then_some(crate::contract::LabelKey::Y),
        )?);
    }

    let group_attrs = Attrs::new().set("transform", format!("translate({group_x:.3},{group_y:.3})"));
    // The splice points a facet composite cuts the printed panel at.
    let cuts = panel.map(|_| frame.cuts(&head, &group_attrs, &plot, &[], Some(&crate::frame::ValueAxisAt::Plot(value_axis_start..value_axis_end))));
    let svg: Node = {
        let mut body = head;
        body.push(Node::group(group_attrs, plot));
        frame.svg(body)
    };

    // `ink`: bars + zero-rule, boundStroke-expanded (mirrors
    // `render_vertical_panel`'s own `ink` field, the same law) — the plot
    // rect's own bounds union the zero-rule's and every bar's own stroke
    // expansion, in the panel's ABSOLUTE canvas frame.
    let plot_left = group_x;
    let plot_top = group_y;
    let plot_right = plot_left + plot_w;
    let plot_bottom = plot_top + plot_h;
    let mut ink = vega_fit::Bounds::new(plot_left, plot_top, plot_right, plot_bottom);
    // See `render_vertical_panel`'s own identical union: under `autosize:
    // pad`, `pad_plot` reports the value-axis gutter as `m.left`/`m.right`
    // overhang rather than shrinking the plot to make room for it, so a
    // facet panel's own ink must include it explicitly. Vertical margins
    // (`m.top`/`m.bottom`) are NOT unioned in here: real Vega's row-facet
    // Y-axis is `row_footer`, a role structurally separate from `cell` that
    // never contributes to a row's own row-spacing ink (only the row's own
    // bars/gridlines do) — unioning them would double-count a top tick
    // label's own overhang into every row's own height.
    if suppress_x_axis && fold_axis_gutter {
        ink.union(&vega_fit::Bounds::new(
            plot_left - m.left,
            plot_top,
            plot_right + value_axis_right_overhang + m.right,
            plot_bottom,
        ));
    }
    if let Some(rules) = rules {
        for y in [rules.zero.then_some(y_rule), rules.unity.then(|| y_pos(1.0))].into_iter().flatten() {
            ink.union(&vega_fit::Bounds::new(plot_left, plot_top + y, plot_right, plot_top + y).expand(rules.width));
        }
    }
    if stroke_w > 0.0 {
        for (&(_, yv), p) in bars_data.iter().zip(&placed) {
            let yt = y_pos(yv);
            let bar_ink = vega_fit::Bounds::new(
                plot_left + p.left,
                plot_top + yt.min(y0),
                plot_left + p.left + p.width,
                plot_top + yt.max(y0),
            )
            .expand(stroke_w);
            ink.union(&bar_ink);
        }
    }

    let drawn = Drawn {
        svg,
        canvas: Rect { x: 0.0, y: 0.0, width: view_w, height: svg_h },
        data_box: Rect { x: plot_left, y: plot_top, width: plot_w, height: plot_h },
        title_pos: m.title_pos,
    };
    let geometry = cuts
        .map(|cuts| -> Result<_, Diagnostic> {
            Ok(facet_compose::PanelGeometry {
            content_start: cuts.content_start,
            svg_end: cuts.svg_end,
            ink,
            axis_w_overhang: m.left + m.right,
            left_mark_overhang: m.left,
            plot_top,
            raw_right_reach: if val_on_right { m.raw_left } else { m.raw_right },
            widest_val_tick_label_width: y_labels
                .iter()
                .try_fold(0.0_f64, |acc, l| fonts.measure_shaped(l, y_family, y_font_size).map(|w| acc.max(w)))?,
            val_gutter: ghost_pad,
            val_tick_size: y_tick_eff,
            val_ticks: y_ticks
                .iter()
                .zip(y_labels.iter())
                .map(|(&tv, lbl)| {
                    (group_y + y_pos(tv) + 0.5 + measure::baseline_shift("middle", y_font_size), lbl.clone())
                })
                .collect(),
            val_tick_marks: crate::common::value_tick_rows(
                plot_top,
                plot_h,
                y_tick_eff,
                y_ticks.iter().map(|&tv| y_pos(tv)),
            ),
            ghost_labels: if r.multiples.is_some() && !left_flip && ghost.is_some() { ghost_texts.clone() } else { Vec::new() },
            mirror_style: Some(facet_compose::GhostStyle {
                caption: y_caption.clone(),
                val_font_size: y_font_size,
                val_weight: y_label_weight.to_string(),
                val_color: y_label_color.to_string(),
                val_family: y_family.to_string(),
            }),
            axis: x_axis,
            top_axis_overhang,
            value_title_w: if paint_value_axis && y_title_extra > 0.0 { crate::common::widest_title_line(val_title_style, &y_title_paint, fonts)? } else { 0.0 },
            legend: facet_legend,
            value_axis: paint_value_axis
                .then(|| facet_compose::ValueAxisInk { range: cuts.value_axis(), origin: (group_x, group_y) }),
            })
        })
        .transpose()?;
    Ok(Some((drawn, geometry)))
}
