//! Scatter layout: every pixel decision. Vega's `autosize: fit` negotiation runs over the plot rect,
//! both axes' label ink, the legends' stack and the annotations' reach; the marks, axes, legends and
//! annotations are placed in the plot it settles on and returned as nodes.

use std::borrow::Cow;
use std::collections::HashSet;

use measure::Measure;
use scene::{Attrs, Node};

use super::bind::Bound;
use super::{BarX, LogX, NominalRound, PlainXPaint, FacetX, LayeredLegend, LegendStack, LinearX, LogAxis, NO_EXTENT, PlainXAxis, PointDatum, RULE_POINT_SIZE, RuleFires, ScatterPanelOpts, XFrame, XLayers, YScale, category_text, color_legend_glyph_names, color_legend_layout, column_extent, date_axis, finite_frac, kept_reading, legend_entry_order, legend_gradient_id, linear_x_scale, log_x_axis, needs_band, plain_x_axis, point_glyph, point_scale_domain, point_x_fracs, point_x_padding, point_x_scale, quantitative_color, reads_chart_rows, rows_at, rule_aria, rule_point, shape_legend_fill, shape_merges_into_color, shape_scale_domain, size_area, size_legend, size_legend_nodes, unfilled_stroke, visible_tick_labels, x_has_nan_years, y_when_x_invalid};
use crate::Payload;
use crate::common::{AxisStyle, format_y_label, row_num, row_str, symbol_radius};
use crate::diagnostic::Diagnostic;
use crate::families::{facet_compose, legend, multiples};
use crate::families::axis_caption::{AxisCaption, CaptionScale, NumericScale, vega_axis_format};
use crate::families::color_scale::{ContinuousColor, GradientAt};
use crate::families::facet_compose::{GhostStyle, PanelGeometry, PanelXAxis};
use crate::families::facet_title::{TitleLayout, layout_title};
use crate::families::layers::{DualAxisTitles, LayerAxis, LayerAxisFrame, LayerAxisInk, LayerLabelStyle, LayerSide, MOVED_BASE_LABEL_PAD, OverlayBarLayer, OverlayLayer, YSides, layers_value_extent, panel_layer_rows, placed_bar_layer_marks};
use crate::families::legend::{VEGA_LEGEND_OFFSET, stack_legends};
use crate::families::temporal::{VegaType, infer_vega_type};
use crate::frame::ChartFrame;
use crate::svg::py_float_str;
use measure::FontBook;
use serde_json::Value;
use crate::vega_fit;
use crate::layout::{IntrinsicWidth, Layout, Rect};

/// A scatter fills the width it is given: nothing to hug.
pub fn intrinsic(_bound: &Bound, _measure: &Measure) -> Result<Option<IntrinsicWidth>, Diagnostic> {
    Ok(None)
}

/// A scatter draws in the slot sizing hands it: that slot's height is its natural height at any
/// width, and [`layout`] draws the cell it is given.
pub fn height_for_width(bound: &Bound, _width: f64, _measure: &Measure) -> Result<f64, Diagnostic> {
    Ok(bound.payload.height)
}

/// One plot in `cell`. A `multiples` chart is composed panel by panel at `render`'s string boundary.
pub fn layout(bound: &Bound, cell: Rect, measure: &Measure) -> Result<Layout, Diagnostic> {
    if bound.r.multiples.is_some() {
        return Err("scatter: layout draws one plot; a multiples chart is composed by render".into());
    }
    let payload: Cow<Payload> = if (cell.width, cell.height) == (bound.payload.width, bound.payload.height) {
        Cow::Borrowed(&*bound.payload)
    } else {
        Cow::Owned(Payload { width: cell.width, height: cell.height, ..Payload::clone(&bound.payload) })
    };
    let (fitted, diagnostics) = crate::diagnostic::collect(|| fit(bound, &payload, measure.fonts, None, None));
    let fitted = fitted?;
    Ok(Layout {
        insets: fitted.canvas.insets_of(&fitted.data_box),
        data_box: fitted.data_box,
        nodes: vec![fitted.svg],
        diagnostics,
        title_pos: fitted.title_pos,
    })
}

/// Scatter through Vega's own fit-margin algebra
/// (`vega_fit::fit_plot`), the same port bar and line/area render through.
/// Layout facts are pinned against the captured VL/Vega/scenegraph specs:
/// a continuous axis is linear with `nice: true` (d3 nice, default count 10)
/// and `zero` unless authored off, ticked by d3 inside-domain ticks at the
/// axis `tickCount`; y additionally renders the baked ladder/domain when it
/// is the measure axis; whichever of x/y is the categorical channel takes
/// Vega's `point` scale (one band center per sorted domain value, `padding`
/// read from the resolved axis's own `scale.padding` — the theme bakes
/// `axis_x.scale.padding: 0` but leaves `axis_y.scale.padding` unset, so a
/// nominal x's bands run edge-to-edge while a nominal y's dot plot keeps
/// VL's own raw default of 0.5); grid and tick positions round to whole
/// pixels while labels keep exact positions; the symbol radius is √size/2
/// (Vega's square-area convention). x and y are never both categorical —
/// the caller already refuses that shape.
///
/// `payload` is the card to draw: `bound.payload` sized to the cell, or a facet panel's own rows. `panel` is
/// the facet panel's overrides.
pub(super) fn fit(
    bound: &Bound,
    payload: &Payload,
    fonts: &FontBook,
    panel: Option<&ScatterPanelOpts>,
    y_ladder: Option<(usize, u8, f64)>,
) -> Result<Fitted, Diagnostic> {
    let Bound { r, overlays, wide_series, shape, .. } = bound;
    let (y_nominal, x_nominal, x_dates) = (shape.y_nominal, shape.x_nominal, shape.x_dates);
    let svg_w = payload.width;
    let view_w = payload.view_width(if x_nominal { crate::ViewScale::Discrete } else { crate::ViewScale::Continuous })?;
    let svg_h = payload.height;
    // A facet panel is laid out by `pad_plot` against its composite's
    // padding, never its own: the card padding belongs to the composite.
    let pad = if panel.is_some() { None } else { payload.padding.as_ref() };
    let domain_rows: &[serde_json::Map<String, Value>] = panel.map_or(&payload.rows, |o| o.shared_rows);
    // Each layer's rows: a layer on the base query draws over this panel's
    // own rows, while the scales read the whole chart's.
    let layer_rows = overlays
        .iter()
        .map(|o| {
            // A panel's payload carries only its own rows; the chart's are the shared ones.
            if panel.is_some() && reads_chart_rows(o, x_nominal) {
                return Ok(domain_rows);
            }
            panel_layer_rows(&payload.rows, panel.is_some(), false, payload, o.query_name(), r.query_name.as_deref(), "scatter")
        })
        .collect::<Result<Vec<_>, _>>()?;
    let layer_domain_rows = overlays
        .iter()
        .map(|o| panel_layer_rows(domain_rows, panel.is_some(), false, payload, o.query_name(), r.query_name.as_deref(), "scatter"))
        .collect::<Result<Vec<_>, _>>()?;
    // Once a layer pins a side, or a categorical base y cannot share its scale with a
    // measure, every layer owns its value scale and axis (`resolve.scale.y:
    // independent`); the base takes the side the first pinned layer leaves.
    let baked_left = r.style.axis_y.position.as_deref() == Some("left");
    let y_sides = YSides::of(overlays).or_else(|| {
        let side = if baked_left { LayerSide::Left } else { LayerSide::Right };
        (y_nominal && !overlays.is_empty()).then(|| YSides { base: side, layers: vec![side; overlays.len()] })
    });
    let independent = y_sides.is_some();
    let shared_layers: Vec<(&OverlayLayer, &[serde_json::Map<String, Value>])> = if independent {
        Vec::new()
    } else {
        overlays.iter().zip(layer_domain_rows.iter().copied()).collect()
    };
    let nan_years = x_dates && x_has_nan_years(payload, r)?;
    let nan_x = |date: &str| nan_years && crate::families::temporal::timeunit_nan(date);
    let layer_y_extent =
        if shared_layers.is_empty() { NO_EXTENT } else { layers_value_extent(&shared_layers, &r.x, &nan_x) };
    // Every layer's x column joins the shared x scale, a pinned layer's included:
    // only the value scale is independent.
    let layer_x_extent = overlays
        .iter()
        .zip(layer_domain_rows.iter())
        .map(|(overlay, rows)| column_extent(rows.iter(), overlay.x().unwrap_or(&r.x)))
        .fold(NO_EXTENT, |(lo, hi), (l, h)| (lo.min(l), hi.max(h)));
    let pad_l = pad.map(|p| p.left).unwrap_or(0.0);
    let pad_r = pad.map(|p| p.right).unwrap_or(0.0);
    let pad_t = pad.map(|p| p.top).unwrap_or(0.0);
    let pad_b = pad.map(|p| p.bottom).unwrap_or(0.0);
    // The chart's OWN effective background — see `ScatterResolved::background`.
    let bg = r.background.as_str();
    let avail_w = svg_w - pad_l - pad_r;
    let ax_x = &r.style.axis_x;
    let x_edge = ax_x.x_edge();
    let ax_y = &r.style.axis_y;
    // `mirror_axis.py` appends an invisible rule layer on the same x, and a
    // layered nominal x compiles to a band scale (padding 0) instead of the
    // theme's point scale: centers at `(i + 0.5) / n`.
    let mirror_wants_band = x_nominal
        && match panel {
            None => ax_y.mirror_requested("scatter")?,
            Some(o) => o.has_mirror,
        };
    // The unbaked y ladder's tick count and which pass of the settling loop
    // this is; the first pass guesses the card's own height.
    // `scale.padding` widens an authored domain by pixels of the plot's own
    // height, so the same pass also carries that height; `scale.round` rounds
    // every position the scale yields.
    let y_pad = ax_y.scale.get("padding").and_then(Value::as_f64).unwrap_or(0.0);
    let y_round = ax_y.scale.get("round").and_then(Value::as_bool) == Some(true);
    let y_ladder_pass = y_ladder.unwrap_or_else(|| {
        let plot_h = panel.map_or(svg_h - pad_t - pad_b, |o| o.plot_h);
        (crate::families::cartesian::vl_default_tick_count(plot_h).max(1), 0, plot_h)
    });

    // ── BaselineFeature's zero rule, scatter's half (#8241) ───────────────
    // Production's `_insert_zero_rule`: a `datum: 0` rule on the measure y,
    // gated off a rotated (dot-plot) axis by `_y_carries_the_measure`
    // (`chart.style.axis_y.is_quantitative`, the RESOLVED classification —
    // never re-derived from `y_nominal` here, which is this file's own
    // channel-kind heuristic and would answer the same on every swept case
    // but is a different fact). `y_zero_rule_fires` (shared with
    // `render_legacy`, see its own doc comment) carries the rest of the
    // gate. Scatter carries no `layers` (`bind` rejects them); its own wide-
    // measure fold runs in `bind`, before this point, so
    // `payload.rows`/`r.y` are already the FOLDED long rows/`WIDE_VALUE_
    // FIELD` on a wide chart — production's shared-domain union (both the
    // zero rule's straddle check and the unity rule's `effective_measure_
    // domain` data fallback) is exactly the base y column's own raw extent
    // over those folded rows (`column_extent`, unconditioned on whether x
    // parsed, matching production's `numeric_column_values` scan), with no
    // separate union step needed.
    // BaselineFeature asks whether 0 lies in the domain every mark on the scale
    // shares: a bar layer always counts, as it paints from 0.
    let rule_layer_y = if shared_layers.iter().any(|(o, _)| matches!(o, OverlayLayer::Bar(_))) {
        (layer_y_extent.0.min(0.0), layer_y_extent.1.max(0.0))
    } else {
        layer_y_extent
    };
    // `BaselineFeature` skips a chart under an independent facet scale. The x
    // threshold rules read the base's own x column; only the shared y domain
    // unions the layers'.
    let independent_scale = panel.is_some_and(|o| o.independent_scale);
    let whole_y = column_extent(domain_rows, &r.y);
    let fires = if independent_scale { RuleFires::none() } else { RuleFires::of(r, domain_rows, rule_layer_y, !overlays.is_empty()) };
    let zero_rule_fires = fires.y_zero;
    // ── BaselineFeature's unity rule, scatter's other half ────────────────
    // Production's `_apply_unity`: a percent-formatted measure axis with a
    // visible grid draws a `datum: 1` rule wherever the domain the emitter
    // PINS reaches 1.0 — `common::unity_rule_fires`, the exact gate
    // `line.rs` already ports (`effective_measure_domain`), reused verbatim
    // rather than copied; it owns its own `_y_carries_the_measure`
    // (`is_quantitative`) check, gating off a dot plot's categorical y the
    // same as the zero rule above. Unlike the zero rule this one never
    // pulls its datum into the domain: the gate already demands the pinned
    // domain contain 1.0.
    // A pinned layer makes the scale independent, which skips `BaselineFeature`.
    let unity_rule_fires = fires.y_unity && !independent;

    // ── BaselineFeature's x-threshold rules, independent of the y ones ────
    // Production's `_apply_x_threshold` (`baseline.py`): a `datum: 0`/
    // `datum: 1` rule on a QUANTITATIVE x axis — a chart can straddle 0 (or
    // reach 1.0 on a ratio) on x AND y at once, an ordinary x-y scatter, and
    // each axis earns its own rule. `x_data_min`/`x_data_max` mirror the
    // y-side raw scan above, over the base x column alone.
    let (x_zero_rule_fires, x_unity_rule_fires) = (fires.x_zero, fires.x_unity);
    let rule_shape = fires.shape_layer(r);

    // `BaselineFeature`'s `datum` rule fracs, in PRODUCTION'S paint order —
    // `apply()` calls `_apply_zero_or_top`, `_apply_unity`, then
    // `_apply_x_threshold` for datum 0 and 1, and `_insert_rule` routes
    // every one of a scatter's rules to `spec.underlays` (beneath the
    // points, #8343), appended in that call order. `baseline_rule_fracs`
    // holds the two Y rules (index 0 zero, index 1 unity, as a fraction of
    // plot HEIGHT); `baseline_rule_fracs_x` holds the two X rules (index 0
    // zero, index 1 unity, as a fraction of plot WIDTH) — a separate array
    // since the two scales are independent. Finalized once each axis's own
    // domain is known (below); `None` means that rule does not fire. Read
    // by both the ink union and the emission below (mirrors `line.rs`'s
    // `baseline_rule_data`), so neither can disagree about which rule is
    // which or draw them out of order.
    let mut baseline_rule_fracs: [Option<f64>; 2] = [None, None];

    // ── Rows Vega keeps (`data_0`: every measure a finite number) ────────
    // `kept_reading` is the one predicate; a size channel joins the SAME
    // filter as x/y, so a null size drops its row from the marks AND from
    // every domain, x and y included.
    let mut x_min = f64::INFINITY;
    let mut x_max = f64::NEG_INFINITY;
    let mut y_dmin = f64::INFINITY;
    let mut y_dmax = f64::NEG_INFINITY;
    let mut kept: Vec<(f64, &serde_json::Map<String, Value>)> = Vec::new();
    // The measure y of each kept row, parallel to `kept` — empty when y is
    // the categorical channel.
    let mut kept_y: Vec<f64> = Vec::new();
    // The size value of each kept row, parallel to `kept` — empty when no
    // size channel is bound.
    let mut kept_size: Vec<f64> = Vec::new();
    // A layered facet's base layer carries the whole chart's data of its own, so
    // every panel draws every base mark.
    let base_rows: &[serde_json::Map<String, Value>] =
        if panel.is_some() && x_dates && !overlays.is_empty() { domain_rows } else { &payload.rows };
    for row in base_rows {
        let Some((xv, yv, sv)) = kept_reading(row, r, x_nominal, x_dates, y_nominal) else {
            // A row with no valid x draws no mark but still shows in the y domain.
            if let Some(yv) = y_when_x_invalid(row, r, x_nominal, x_dates, y_nominal) {
                y_dmin = y_dmin.min(yv);
                y_dmax = y_dmax.max(yv);
            }
            continue;
        };
        if let Some(yv) = yv {
            y_dmin = y_dmin.min(yv);
            y_dmax = y_dmax.max(yv);
            kept_y.push(yv);
        }
        if let Some(sv) = sv {
            kept_size.push(sv);
        }
        if !x_nominal {
            x_min = x_min.min(xv);
            x_max = x_max.max(xv);
        }
        kept.push((xv, row));
    }
    // Layers on the shared scales join their domains (Vega-Lite unions them).
    y_dmin = y_dmin.min(layer_y_extent.0);
    y_dmax = y_dmax.max(layer_y_extent.1);
    (y_dmin, y_dmax) = crate::annotation::widen_y((y_dmin, y_dmax), &r.annotations, crate::annotation::YAxis::Base, panel.filter(|o| o.independent_scale).map(|o| o.panel_key), ax_y.is_log_scale());
    if !x_nominal {
        x_min = x_min.min(layer_x_extent.0);
        x_max = x_max.max(layer_x_extent.1);
        // Annotation numbers join the extent as data does; a date axis takes
        // its annotation days in `date_axis`.
        if !x_dates {
            (x_min, x_max) = crate::annotation::widen_numbers((x_min, x_max), &r.annotations);
        }
    }
    // The color and shape scales read the rows Vega keeps over the whole
    // chart (`domain_rows`), not this panel's own `kept`.
    let drawn = rows_at(domain_rows, &bound.drawn);
    // `_value_tooltip_field`'s own type inference (`infer_vega_type_from_
    // data`) would call an all-date-like nominal y "temporal" and format it
    // through `tooltip_header_date_expr` -- this port has no ported
    // detection for that. Same posture as line/area's own unmodeled shapes:
    // gated off silently rather than refused, since the points already
    // render correctly today and a new `Err` here would regress an
    // already-passing pixel row. No corpus row hits this: every dot-plot y
    // in the swept corpus is a plain category.
    let y_is_date_like = y_nominal
        && kept.iter().all(|&(_, row)| {
            crate::families::temporal::parse_iso_date(&row_str(row, &r.y)).is_some()
        });
    // `StructuredTooltipFeature.applies_to` refuses a wide scatter outright
    // (`features/structured_tooltip.py`: "a wide scatter falls back to the
    // plain per-mark VL tooltip instead of reading a field that isn't
    // there") — a wide chart's own point aria is built separately below
    // (`wide_tooltip`), and a baseline rule fired on a wide chart (no
    // corpus row does) silently paints with none, the same accepted
    // "skip wiring rather than refuse" convention every unmodeled aria
    // shape in this crate already follows.

    // A quantitative x row paints its own `x_tooltip_format` when the resolve
    // bakes one, else the measure's digits bare: the affix is y's own
    // currency/unit label and never paints x's unrelated measure
    // (`_scatter_roles`, `structured_tooltip.py:567`).
    let x_tooltip_format = r.x_tooltip_row_format();
    let tooltip_labels =
        r.wide_measures.is_empty() && !r.style.tooltip_format.spec.is_empty() && !y_is_date_like;
    // ── Size scale: linear, zero-anchored, over Vega-Lite's fixed default
    // point-mark range — `symbol_radius` (√size/2) turns a scaled AREA back
    // into the drawn circle's radius, same convention the marks already use.
    let size_domain = bound.size_domain;
    // ── X scale: nominal (`point`, `scale.padding`) or continuous (zero
    // unless authored off, then d3 nice count 10) ────────────────────────
    // Both shapes resolve to a FRACTION of the plot width (0 at the left),
    // the same convention `y_ticks`/`y_nominal` below already keeps for y —
    // `x_ticks` holds fractions in both branches, never a raw tick value,
    // so every later consumer (grid, ticks, labels, points) reads one
    // uniform `frac * plot_w`. `kept_x_frac` is `kept`'s own per-row x
    // fraction, index-parallel with `kept`.
    let mut x_ticks: Vec<f64> = Vec::new();
    let mut x_labels: Vec<String> = Vec::new();
    // What the category axis paints: the categories, or what an authored `labels.expr` makes of each.
    let mut x_shown: Vec<String> = Vec::new();
    // Nominal-x category tilt ladder: bar's own law
    // (`vega_fit::category_label_layout`'s crowding ladder,
    // `vega_fit::vl_x_label_anchor`'s anchor — both already shared
    // functions every cartesian family calls directly) reused verbatim for
    // scatter's point-scale x. Stays flat off the nominal-x arm — the
    // continuous-x arm has no categorical labels to tilt.
    let mut x_cat_angle = 0.0_f64;
    let mut x_cat_align = "center";
    let mut x_cat_baseline = "top";
    let x_title_plain = crate::common::axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels)?;
    let facet_grid = multiples::grid_counts(r.multiples.as_ref(), &r.panel_axes);
    let (x_box, y_box) = if panel.is_some() {
        multiples::facet_title_boxes((payload.width, payload.height), (facet_grid.1, facet_grid.0), ax_y.mirror_requested("scatter")?, 0.0)
    } else {
        ((svg_w, 1.0), (svg_h, 1.0))
    };
    let x_title_text = crate::common::wrapped_axis_title(&r.id, "x_label", &x_title_plain, &ax_x.title.font, x_box.0, x_box.1, fonts)?;
    // A date x: one UTC time scale over the distinct dates, the labels and
    // gridlines at the shared temporal ladder's openers.
    let plain_x: Option<&PlainXAxis> = panel.and_then(|o| match &o.x_axis {
        FacetX::Plain(axis) => Some(*axis),
        FacetX::Date(_) => None,
    });
    let temporal_x: Option<crate::families::scatter_axis::TemporalXAxis> = if let Some(opts) = panel {
        match &opts.x_axis {
            FacetX::Date(axis) => Some((*axis).clone()),
            FacetX::Plain(_) => None,
        }
    } else if x_dates {
        let axis = date_axis(
            payload,
            r,
            &drawn,
            &XLayers::of(r, overlays, &layer_domain_rows),
            svg_w,
            x_title_text.clone(),
            panel.is_none().then_some(crate::contract::LabelKey::X),
            fonts,
        )?;
        if axis.raises_no_marks() {
            return Err(crate::labels_expr::throw_diagnostic(payload));
        }
        if let Some(label_count) = axis.collision {
            crate::families::cartesian::warn_axis_label_collision(&r.id, &r.x, label_count);
        }
        Some(axis)
    } else {
        None
    };
    // A date axis places its ticks and points per plot width (`x_frame`).
    let mut linear_x: Option<LinearX> = None;
    let mut log_x: Option<(f64, f64)> = None;
    // A band x's bandwidth as a fraction of the plot width.
    let mut x_bandwidth: Option<f64> = None;
    let kept_x_frac: Vec<f64> = if temporal_x.is_some() || plain_x.is_some() {
        vec![0.0; kept.len()]
    } else if x_nominal {
        // Production orders only a categorical y (a dot plot) by `chart.sort`.
        // The layers' union scale lists its categories in paint order, never sorted.
        let domain = if overlays.is_empty() {
            point_scale_domain(&payload.rows, &kept, &r.x, None)?
        } else {
            let mut seen: Vec<String> = Vec::new();
            for &(_, row) in &kept {
                let category = category_text(row, &r.x);
                if !seen.contains(&category) {
                    seen.push(category);
                }
            }
            let layer_x_rows: Vec<(&[serde_json::Map<String, Value>], &str)> = overlays
                .iter()
                .zip(layer_domain_rows.iter())
                .map(|(overlay, rows)| (*rows, overlay.x().unwrap_or(r.x.as_str())))
                .collect();
            crate::families::layers::union_x_domain(&seen, &layer_x_rows, false).domain
        };
        let (fracs, bandwidth) = if overlays.iter().any(needs_band) {
            point_x_scale(ax_x, domain.len(), true)
        } else {
            (point_x_fracs(if mirror_wants_band { 0.5 } else { point_x_padding(ax_x) }, domain.len()), None)
        };
        x_bandwidth = bandwidth;
        for (category, &frac) in domain.iter().zip(&fracs) {
            x_ticks.push(frac);
            x_labels.push(category.clone());
        }
        x_shown = crate::labels_expr::band_texts("x", &x_labels, &ax_x.labels).map_err(|e| format!("scatter: {e}"))?;
        // `resolve_axis_x_overlap` (`emitters/_label_overlap.py:718-731`)
        // short-circuits on an AUTHORED `labels.angle` before the crowding
        // ladder ever runs — bar's own category axis reads it the same way
        // (`bar.rs`'s own `cat_angle` match, `Some(angle) => angle` first).
        let x_label_font_size = ax_x.labels.font.size_or(11.0);
        let x_label_layout = match ax_x.labels.angle {
            Some(angle) => vega_fit::CategoryLabels { angle, collision: None },
            None => vega_fit::category_label_layout(
                &ax_x.labels,
                &x_labels,
                payload.width,
                r.style.label_usable_ratio,
                fonts,
                x_label_font_size,
            )?,
        };
        if let Some(label_count) = x_label_layout.collision {
            crate::families::cartesian::warn_axis_label_collision(&r.id, &r.x, label_count);
        }
        x_cat_angle = vega_fit::normalize_angle(x_label_layout.angle);
        (x_cat_align, x_cat_baseline) = if x_cat_angle == 0.0 {
            ("center", x_edge.baseline())
        } else {
            vega_fit::vl_x_label_anchor(x_cat_angle, x_edge == crate::common::XEdge::Bottom)
        };
        kept.iter()
            .map(|&(_, row)| {
                let category = category_text(row, &r.x);
                domain.iter().position(|d| *d == category).map(|i| fracs[i]).ok_or_else(|| {
                    format!("scatter: x value {category:?} is outside the point-scale domain")
                })
            })
            .collect::<Result<_, String>>()?
    } else if kept.is_empty() && overlays.is_empty() {
        // No row has an x: the scale's domain is empty, so its axis draws no gridline, tick or label.
        Vec::new()
    } else if ax_x.is_quantitative && ax_x.is_log_scale() {
        log_x = Some((x_min, x_max));
        vec![0.0; kept.len()]
    } else {
        let lin = linear_x_scale(r, x_min, x_max, (x_zero_rule_fires, x_unity_rule_fires))?;
        linear_x = Some(lin);
        vec![0.0; kept.len()]
    };
    // Continuous x's own labelFlush behavior (edges anchor start/end, not
    // centered) only applies to that scale; a nominal x's point-scale bands
    // always center their label under the band, at every position including
    // the two edge bands.
    let x_label_flush = !x_nominal;
    if let Some(plain) = plain_x {
        x_bandwidth = plain.bandwidth();
    }
    let x_round = ax_x.scale.get("round").and_then(Value::as_bool) == Some(true);
    // d3's band and point scales floor the step and round the start under
    // `scale.round`, where a continuous scale rounds each position.
    let nominal_round = match plain_x {
        Some(plain) => plain.nominal_round,
        None => (x_round && x_nominal).then(|| {
            // A lone chart's mirror ghost builds a point scale at padding 0.5.
            let band = x_bandwidth.is_some();
            let padding = if mirror_wants_band && !band { 0.5 } else { point_x_padding(ax_x) };
            let (inner, outer) = if band { (padding, padding) } else { (1.0, padding) };
            NominalRound { inner, outer, n: x_ticks.len() }
        }),
    };
    let bar_x = BarX {
        linear: linear_x.as_ref().or_else(|| plain_x.and_then(PlainXAxis::linear)),
        log: log_x.map(|extent| LogX { ax: ax_x, extent }).or_else(|| plain_x.and_then(PlainXAxis::log)),
        bandwidth: x_bandwidth,
        nominal_round,
        temporal: temporal_x.as_ref(),
    };
    // A band x's step geometry at a plot `pw` wide, which a step layer's plateaus
    // fill: its bandwidth and the centers of its first and last band.
    let step_band = |pw: f64| -> Option<crate::families::layers::StepBand> {
        let bandwidth = bar_x.bandwidth(pw)? * pw;
        let (first, last) = match (&temporal_x, plain_x) {
            (Some(axis), _) => axis.band_ends(pw)?,
            (None, Some(plain)) => plain.band_ends()?,
            (None, None) => (*x_ticks.first()?, *x_ticks.last()?),
        };
        let (first, last) = match (&nominal_round, plain_x) {
            (Some(round), Some(plain)) => {
                let centers = plain.category_centers();
                (round.snap(first, centers, pw), round.snap(last, centers, pw))
            }
            (Some(round), None) => (round.snap(first, &x_ticks, pw), round.snap(last, &x_ticks, pw)),
            (None, _) => (first, last),
        };
        Some(crate::families::layers::StepBand { bandwidth, first: first * pw, last: last * pw })
    };
    // A layer row's x as a fraction of a plot `pw` wide, on the base's own x
    // scale; `None` where the row has no position on it (Vega drops the mark).
    let layer_x_frac = |row: &serde_json::Map<String, Value>, field: &str, pw: f64| -> Option<f64> {
        if let Some(axis) = &temporal_x {
            row.get(field).filter(|v| !v.is_null() && v.as_str() != Some("")).map(|_| axis.frac(&row_str(row, field), pw))
        } else if let Some(plain) = plain_x {
            let frac = plain.layer_frac(row, field, pw)?;
            Some(match &nominal_round {
                Some(round) => round.snap(frac, plain.category_centers(), pw),
                None => frac,
            })
        } else if let Some(lin) = &linear_x {
            row_num(row, field).map(|v| lin.frac(lin.domain_at(pw), v))
        } else if let Some((lo, hi)) = log_x {
            let ladder = log_x_axis(ax_x, lo, hi, pw).ok()?.0;
            row_num(row, field).map(|v| ladder.frac_from_left(v))
        } else {
            let category = category_text(row, field);
            let frac = x_labels.iter().position(|label| *label == category).map(|i| x_ticks[i])?;
            Some(match &nominal_round {
                Some(round) => round.snap(frac, &x_ticks, pw),
                None => frac,
            })
        }
    };

    // A bar layer's value labels are a text layer on the base's own x field: per
    // rect that has a base x, the rect and the x its label centers on.
    let bar_label_sites = |bl: &OverlayBarLayer,
                           rows: &[serde_json::Map<String, Value>],
                           marks: &[crate::families::layers::BarLayerMark<'_>],
                           pw: f64|
     -> Result<(Vec<usize>, Vec<f64>), String> {
        let mut sited = Vec::with_capacity(marks.len());
        let mut centers = Vec::with_capacity(marks.len());
        for (i, mark) in marks.iter().enumerate() {
            let Some(x_frac) = layer_x_frac(mark.row, &r.x, pw) else { continue };
            let (_, _, label_x) = bar_x.slot(bl, (rows, r.x.as_str()), (mark.row, x_frac * pw), pw, true)?;
            sited.push(i);
            centers.push(label_x);
        }
        Ok((sited, centers))
    };

    // ── Y scale ──────────────────────────────────────────────────────────
    // Both shapes resolve to a fraction of the plot height (0 at the top):
    // the measure from production's baked ladder and domain, a categorical
    // channel from Vega's point scale (`padding 0.5` — step = extent / n,
    // band center i = step · (0.5 + i)).
    let series_of = |row: &serde_json::Map<String, Value>| {
        r.color.as_deref().map(|cf| row_str(row, cf)).unwrap_or_default()
    };
    // Per-point radius: from the size scale when bound (index-parallel with
    // `kept`/`kept_size`), else the mark's own constant literal size — same
    // `symbol_radius` (√size/2) convention either way.
    let mark_radii: Vec<f64> = if let Some(domain) = size_domain {
        kept_size.iter().map(|&sv| symbol_radius(size_area(domain, sv))).collect()
    } else {
        vec![symbol_radius(r.style.point_mark.size); kept.len()]
    };
    let mut y_ticks: Vec<f64> = Vec::new();
    let mut y_labels: Vec<String> = Vec::new();
    // Each tick's value, the number a mirror ghost's own format paints.
    let mut y_tick_values: Vec<f64> = Vec::new();
    let mut points_data: Vec<PointDatum> = Vec::new();
    // A layer's value as a fraction of the plot height from its top.
    let mut layer_y_frac: Box<dyn Fn(f64) -> f64> = Box::new(|_| f64::NAN);
    // The base y scale an annotation maps through; every branch below sets it.
    let mut y_scale: Option<YScale> = None;
    let y_scale_caption: CaptionScale;
    if y_nominal {
        // A facet's panels share the whole chart's categories unless each
        // draws its own.
        let domain = if panel.is_some_and(|o| !o.embedded_y) {
            let every: Vec<(f64, &serde_json::Map<String, Value>)> = drawn.iter().map(|&row| (0.0, row)).collect();
            point_scale_domain(domain_rows, &every, &r.y, r.sort.as_ref())?
        } else {
            point_scale_domain(&payload.rows, &kept, &r.y, r.sort.as_ref())?
        };
        y_scale_caption = CaptionScale::Discrete { values: domain.clone(), format: vega_axis_format(ax_y, false)? };
        let bands = domain.len() as f64;
        let band = |i: usize| (0.5 + i as f64) / bands;
        let time_format = r
            .style
            .axis_y
            .labels
            .spec()
            .filter(|s| crate::families::temporal::is_time_format(s))
            .map(|spec| crate::families::temporal::AuthoredTimeFormat::parse("scatter", Some(spec)))
            .transpose()?
            .flatten();
        let spoken = crate::labels_expr::band_texts("y", &domain, &ax_y.labels).map_err(|e| format!("scatter: {e}"))?;
        for (i, category) in domain.iter().enumerate() {
            y_ticks.push(band(i));
            y_tick_values.push(crate::common::js_number(category));
            y_labels.push(ax_y.labels.cased(&match (ax_y.labels.expr.as_ref(), time_format, crate::families::temporal::parse_iso_date(category)) {
                (Some(_), ..) => spoken[i].clone(),
                (None, Some(format), Some((y, m, d))) => format.text(y, m, d),
                _ => category.clone(),
            })?);
        }
        for (idx, &(_, row)) in kept.iter().enumerate() {
            let category = category_text(row, &r.y);
            let i = domain.iter().position(|d| *d == category).ok_or_else(|| {
                format!("scatter: y value {category:?} is outside the point-scale domain")
            })?;
            points_data.push((kept_x_frac[idx], band(i), series_of(row), mark_radii[idx], row));
        }
        y_scale = Some(YScale::Points { fracs: (0..domain.len()).map(band).collect(), domain });
    } else if ax_y.is_log_scale() {
        // A shared facet scale spans every panel's rows; an independent one fits
        // the panel's own.
        let extent_rows: Vec<&serde_json::Map<String, Value>> = if independent_scale {
            kept.iter().map(|&(_, row)| row).collect()
        } else {
            domain_rows.iter().collect()
        };
        let (y_lo, y_hi) = extent_rows
            .iter()
            .filter_map(|row| kept_reading(row, r, x_nominal, x_dates, y_nominal)?.1)
            .fold(layer_y_extent, |(lo, hi), yv| (lo.min(yv), hi.max(yv)));
        let (y_lo, y_hi) = crate::annotation::widen_y((y_lo, y_hi), &r.annotations, crate::annotation::YAxis::Base, panel.filter(|o| o.independent_scale).map(|o| o.panel_key), true);
        // A layered spec reads the axis's own baked count where a lone chart's
        // log axis keeps d3's default.
        let y_tick_count = if overlays.is_empty() { 10 } else { ax_y.ticks.count.unwrap_or(6.0) as usize };
        let ladder = LogAxis::new(ax_y, y_lo, y_hi, y_tick_count, None, y_ladder_pass.2)?;
        if crate::labels_expr::view::active() {
            crate::labels_expr::view::register_log("y", ladder.lo, ladder.hi, crate::labels_expr::view::Span::Height);
        }
        y_scale_caption = CaptionScale::Numeric { kind: NumericScale::Log, domain: (ladder.lo, ladder.hi), format: vega_axis_format(ax_y, true)? };
        for (i, &tv) in ladder.ticks.iter().enumerate() {
            y_ticks.push(ladder.frac_from_top(tv));
            y_tick_values.push(tv);
            y_labels.push(if ladder.mantissa_visible[i] {
                ax_y.labels.apply_expr("y", tv, format_y_label(
                    tv,
                    ax_y.ruler.as_ref(),
                    ax_y.tick_label.as_ref(),
                    ax_y.labels.format.as_ref(),
                    i,
                    ladder.ticks.len(),
                )?, i, ladder.ticks.len())?
            } else {
                String::new()
            });
        }
        if unity_rule_fires {
            baseline_rule_fracs[1] = Some(ladder.frac_from_top(1.0));
        }
        for (idx, (&(_, row), &yv)) in kept.iter().zip(kept_y.iter()).enumerate() {
            points_data.push((kept_x_frac[idx], ladder.frac_from_top(yv), series_of(row), mark_radii[idx], row));
        }
        y_scale = Some(YScale::Bent(vega_fit::Bend::Log, (ladder.lo, ladder.hi)));
        layer_y_frac = Box::new(move |v| ladder.frac_from_top(v));
    } else {
        let (y_min, y_max) = match authored_y_domain(ax_y, y_ladder_pass.2) {
            // A layer union brings Vega-Lite's default `nice` back, which rounds an
            // authored pair too.
            Some((lo, hi)) if !shared_layers.is_empty() => {
                if lo > hi {
                    let (nice_hi, nice_lo) = d3rs::ticks::d3_nice(hi, lo, 10);
                    (nice_lo, nice_hi)
                } else {
                    d3rs::ticks::d3_nice(lo, hi, 10)
                }
            }
            Some(pair) => pair,
            None => unauthored_measure_domain(ax_y, y_dmin, y_dmax, y_ladder_pass.2, !shared_layers.is_empty()),
        };
        y_scale_caption = CaptionScale::Numeric { kind: NumericScale::of_axis(ax_y)?, domain: (y_min, y_max), format: vega_axis_format(ax_y, true)? };
        // d3 maps a collapsed domain onto the range's midpoint with the one
        // value as its only tick; a `NaN` one (an unparsable authored pair)
        // has neither ticks nor positions.
        let y_span = y_max - y_min;
        let y_bend = ax_y.bend();
        if crate::labels_expr::view::active() {
            crate::labels_expr::view::register_bent("y", y_bend, y_min, y_max, crate::labels_expr::view::Span::Height);
        }
        let y_frac = move |v: f64| -> f64 {
            if y_span == 0.0 {
                return 0.5;
            }
            let top = y_bend.fwd(y_max);
            finite_frac((top - y_bend.fwd(v)) / (top - y_bend.fwd(y_min)))
        };
        if zero_rule_fires {
            baseline_rule_fracs[0] = Some(y_frac(0.0));
        }
        if unity_rule_fires {
            baseline_rule_fracs[1] = Some(y_frac(1.0));
        }
        y_scale = Some(YScale::Bent(y_bend, (y_min, y_max)));
        layer_y_frac = Box::new(y_frac);
        let y_eps = y_span.abs() * 1e-9;
        // A ladder production bakes is drawn as is; an unbaked one is Vega's
        // own d3 ticks at `ceil(height / 40)`, which the fitted height decides
        // (settled below, one pass per change).
        let ladder: Vec<f64> = if y_span.is_nan() {
            Vec::new()
        } else if y_span == 0.0 {
            vec![y_min]
        } else if ax_y.tick_values.is_empty() {
            let (count, ..) = y_ladder_pass;
            crate::families::cartesian::unbaked_ticks(ax_y, y_min, y_max, count)
        } else {
            ax_y.tick_values
                .iter()
                .copied()
                .filter(|&tv| tv >= y_min.min(y_max) - y_eps && tv <= y_min.max(y_max) + y_eps)
                .collect()
        };
        for (i, &tv) in ladder.iter().enumerate() {
            y_ticks.push(y_frac(tv));
            y_tick_values.push(tv);
            y_labels.push(ax_y.labels.apply_expr("y", tv, format_y_label(
                tv,
                ax_y.ruler.as_ref(),
                ax_y.tick_label.as_ref(),
                ax_y.labels.format.as_ref(),
                i,
                ladder.len(),
            )?, i, ladder.len())?);
        }
        for (idx, (&(_, row), &yv)) in kept.iter().zip(kept_y.iter()).enumerate() {
            points_data.push((kept_x_frac[idx], y_frac(yv), series_of(row), mark_radii[idx], row));
        }
    }

    // ── Fonts and paddings ───────────────────────────────────────────────
    let x_font_size = ax_x.labels.font.size_or(11.0);
    let x_family = ax_x.labels.font.family_or("sans-serif");
    let x_label_pad = ax_x.labels.padding.unwrap_or(6.0);
    let x_axis_offset = ax_x.ticks.offset.unwrap_or(0.0);
    let x_ticks_visible = ax_x.ticks.visible.unwrap_or(true);
    let x_tick_len = ax_x.ticks.length.unwrap_or(6.0);
    let x_tick_eff = if x_ticks_visible { x_tick_len } else { 0.0 };
    let x_limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    // The x label's `labelFlush` anchor at a position on a plot `pw` wide.
    let x_flush_align =
        |pos: f64, pw: f64| if x_label_flush { crate::families::line::flush_align(pos, pw) } else { "center" };
    let y_font_size = ax_y.labels.font.size_or(11.0);
    let y_family = ax_y.labels.font.family_or("sans-serif");
    let y_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);

    // Visible y tick marks sit between the plot edge and the labels.
    let y_tick_eff = ax_y.tick_len();
    let y_label_ws: Vec<f64> = y_labels
        .iter()
        .map(|lbl| fonts.measure_shaped(lbl, y_family, y_font_size))
        .collect::<Result<_, _>>()?;
    // Right-axis label geometry: `y_label_off` is the labels' anchor past
    // the plot edge, `y_label_edge` the ink the rotated title clears. The
    // measure ladder is right-aligned against production's baked gutter
    // (the measured labelPadding of the DRAWN ladder, `axis.drawn_tick_values`:
    // the baked rungs inside the baked domain, `common::drawn_tick_values`,
    // in the proportional Inter stand-in); a point scale's categories are
    // start-aligned at labelPadding and run outward, so their own width is
    // the edge.
    // An unset `labels.align` is Vega's own default for a right axis: labels
    // start at `labelPadding` and run outward, as a point scale's do.
    // The measured `labelPadding` of the DRAWN ladder, the gutter a right-aligned
    // axis reserves past the tick (`mirror_axis.py` re-measures the ghost's the same way).
    let measured_off = || -> Result<f64, String> {
        let mf = measure::stand_in_family(y_family);
        let mut max_w = 0.0_f64;
        // An axis with no baked ladder reserves the cover estimate instead of
        // measuring rungs it never bakes.
        if ax_y.is_log_scale() || ax_y.tick_values.is_empty() {
            max_w = crate::common::estimated_tick_label_gutter(
                fonts,
                y_family,
                y_font_size,
                y_limit,
                ax_y.tick_label.as_ref(),
                ax_y.labels.format.as_ref(),
                ax_y,
                // Production sizes the gutter once over the whole dataset.
                if independent_scale { whole_y.0 } else { y_dmin },
                if independent_scale { whole_y.1 } else { y_dmax },
            )? - crate::common::VALUE_AXIS_BREATHING_ROOM;
        }
        let drawn = crate::common::drawn_tick_values(ax_y);
        for (i, &tv) in drawn.iter().enumerate() {
            let lbl = ax_y.labels.apply_expr("y", tv, format_y_label(
                tv,
                ax_y.ruler.as_ref(),
                ax_y.tick_label.as_ref(),
                ax_y.labels.format.as_ref(),
                i,
                drawn.len(),
            )?, i, drawn.len())?;
            max_w = max_w.max(fonts.measure(&lbl, mf, y_font_size)?);
        }
        Ok(y_tick_eff + max_w.min(y_limit) + crate::common::VALUE_AXIS_BREATHING_ROOM)
    };
    // A left axis puts its labels end-anchored `labelPadding` past the tick, so
    // the gutter is the widest label itself; `labels.align` is a right-axis
    // control. A pinned layer forces the base to the complementary side regardless of
    // its own baked position (`_resolved_layer_y_orients`, `_overlay.py`), where Vega
    // pops the base axis's own `labelAlign` and `labelPadding`.
    let y_left = y_sides.as_ref().map_or(baked_left, |s| s.base == LayerSide::Left);
    // A facet's shared left value axis is the composite's `row_header`, a role apart from the cell.
    let shared_left = y_left && panel.is_some_and(|o| !o.embedded_y);
    let base_moved = y_left != baked_left;
    let y_label_pad = if base_moved { MOVED_BASE_LABEL_PAD } else { ax_y.labels.padding.unwrap_or(6.0) };
    let (y_label_off, y_label_align, y_label_anchor, y_label_edge) = if y_ticks.is_empty() {
        // A scale with no ticks draws no tick marks and no labels to reserve.
        (0.0, if y_left { "right" } else { "left" }, if y_left { "end" } else { "start" }, 0.0)
    } else if y_left {
        let off = y_tick_eff + y_label_pad;
        let widest = y_label_ws.iter().fold(0.0_f64, |acc, &w| acc.max(w)).min(y_limit);
        (off, "right", "end", off + widest)
    } else if y_nominal || ax_y.labels.align_kept("right").is_none() || base_moved {
        let off = y_tick_eff + y_label_pad;
        let widest = y_label_ws.iter().fold(0.0_f64, |acc, &w| acc.max(w)).min(y_limit);
        (off, "left", "start", off + widest)
    } else {
        let off = measured_off()?;
        (off, "right", "end", off)
    };
    if crate::labels_expr::view::active() && panel.is_none() && ax_y.labels.upright() {
        use crate::labels_expr::view::{register_axis, AxisSpec, Orient};
        let orient = if y_left { Orient::Left } else { Orient::Right };
        register_axis("y", AxisSpec { orient, offset: y_label_off, shift: 0.0, align: Some(y_label_align), baseline: None, angle: 0.0, flush: None, style: ax_y.labels.item_style()? });
    }

    // `axis_y.mirror: true`: a ghost axis on the opposite edge repeating the
    // primary's labels and ticks at the primary's own reserved offset
    // (`mirror_axis.py`); a facet's panels paint theirs through the composer.
    let ghost = ax_y.mirror_ghost("scatter")?;
    let mirror_active = panel.is_none() && ghost.is_some();
    // The ghost's own labels and their widths (a format or ruler of its own relabels the primary's).
    let ghost_texts = match &ghost {
        Some(g) => g.texts(&y_tick_values, &y_labels)?,
        None => Vec::new(),
    };
    let ghost_ws = ghost
        .as_ref()
        .map_or(Ok(Vec::new()), |g| g.widths(&y_tick_values, &y_labels, &y_label_ws, |label| fonts.measure_shaped(label, y_family, y_font_size)))?;
    // A left axis authored `labels.align: right` is the ghost's own edge: the
    // ghost copies it and `mirror_axis.py` re-measures its `labelPadding` over
    // the labels, so they end-anchor one label width past the start-anchored spot.
    let (ghost_off, ghost_anchor) = if y_left && mirror_active && ax_y.labels.align.as_deref() == Some("right") {
        (measured_off()?, "end")
    } else {
        (y_label_off, if y_left { "start" } else { "end" })
    };

    // A start-aligned (or left-axis) log gutter ends at its widest label Vega keeps: a
    // label `labelOverlap: greedy` hides is drawn at opacity 0 and adds no
    // ink, so the edge depends on the plot size.
    // The ink of rung `i`'s label on a plot `pw` wide and `ph` tall.
    let y_label_ink = |pw: f64, ph: f64, i: usize| {
        let anchor_x = if y_left { -y_label_off } else { pw + y_label_off };
        vega_fit::text_ink_rows(anchor_x, y_px(ph, y_ticks[i], y_round), y_label_ws[i].min(y_limit), y_font_size, y_label_align, "middle", crate::text::line_count(&y_labels[i]))
    };
    // Vega's `labelOverlap` thins a measure axis (parity, greedy on a log one); a point scale's
    // categories all draw.
    let y_thinned = |pw: f64, ph: f64| -> Vec<bool> {
        if y_nominal && !ax_y.is_log_scale() {
            vec![true; y_labels.len()]
        } else {
            visible_tick_labels(&y_labels, ax_y.labels.min_gap.unwrap_or(0.0), ax_y.is_log_scale(), |i| y_label_ink(pw, ph, i))
        }
    };
    let y_edge_at = |pw: f64, ph: f64| -> f64 {
        if !(y_label_align == "left" || y_left) {
            return y_label_edge;
        }
        let visible = y_thinned(pw, ph);
        let widest = y_label_ws.iter().zip(&visible).filter(|(_, &v)| v).fold(0.0_f64, |acc, (&w, _)| acc.max(w.min(y_limit)));
        y_label_off + widest
    };
    // The far edge of a left axis's mirrored ghost, which the legend clears.
    let ghost_edge_at = |pw: f64, ph: f64| -> f64 { if ghost_anchor == "end" { ghost_off } else { y_edge_at(pw, ph) } };

    // `r.y` is the synthetic `WIDE_VALUE_FIELD` on a wide chart — its own
    // default title is production's joined measure-name list
    // (`wide_measures_title`, mirrors `emitters/scatter.py`'s own
    // `chart.y_label or wide_measures_title(chart.wide_measures)?`), never
    // `slug_to_text` on the synthetic field name.
    let y_title_plain = if wide_series.is_empty() {
        crate::common::axis_title_text(&r.y, r.y_label.as_deref(), &r.field_labels)?
    } else {
        match r.y_label.as_deref() { Some(l) => crate::common::collapse_title_newlines(l), None => crate::common::wide_measures_title(&r.wide_measures, &r.field_labels)? }
    };
    let base_y_title = y_title_plain.clone();
    let y_title_plain = if overlays.is_empty() || independent {
        y_title_plain
    } else {
        let layer_titles = overlays
            .iter()
            .map(|o| crate::common::axis_title_text(o.y(), o.label(), &r.field_labels))
            .collect::<Result<Vec<_>, _>>()?;
        crate::families::layers::merged_axis_title(std::iter::once(y_title_plain).chain(layer_titles))
    };
    let y_title_text = crate::common::wrapped_axis_title(&r.id, "y_label", &y_title_plain, &ax_y.title.font, y_box.0, y_box.1, fonts)?;
    let dual_titles = DualAxisTitles::new(independent, ax_y, r.y_label.as_deref());
    let (val_title_style, layer_title_style) = (dual_titles.base(ax_y), dual_titles.layer(ax_y));
    let y_title_extra = crate::common::y_title_extra(val_title_style, &y_title_text);
    let x_title_w = crate::common::widest_title_line(&ax_x.title, &x_title_text, fonts)?;
    let y_title_w = crate::common::widest_title_line(val_title_style, &y_title_text, fonts)?;
    // Each independent layer's own scale, ladder, labels and title; an unpinned one
    // sits on the base's side.
    let (independents, layer_zero_rules) =
        y_sides.as_ref().map_or_else(Default::default, |sides| sides.scaled(overlays, &layer_rows, &r.x, ax_y));
    let layer_axes: Vec<LayerAxis> = independents
        .iter()
        .zip(overlays)
        .enumerate()
        .map(|(i, (p, overlay))| {
            let extent = match overlay {
                OverlayLayer::Bar(bl) => placed_bar_layer_marks(bl, p.rows, p.x, &nan_x, |row| layer_x_frac(row, p.x, 1.0).is_some())
                    .iter()
                    .fold((0.0_f64, 0.0_f64), |(lo, hi), m| (lo.min(m.from).min(m.to), hi.max(m.from).max(m.to))),
                OverlayLayer::Line(_) | OverlayLayer::Scatter(_) => p.value_extent(p.rows.iter()),
            };
            let extent = crate::annotation::widen_y(extent, &r.annotations, crate::annotation::YAxis::Layer(i), None, false);
            let frame = LayerAxisFrame {
                family: "scatter",
                ax_y,
                fonts,
                font_family: y_family,
                font_size: y_font_size,
                label_limit: y_limit,
                tick_size: y_tick_eff,
                title_style: layer_title_style,
                field_labels: &r.field_labels,
            };
            // A layer over a categorical y inherits no baked ladder: Vega's own count
            // follows the plot height, settled below like an unbaked base ladder.
            match (y_nominal, p.axis_y.ticks.as_ref().and_then(|t| t.count)) {
                (true, None) => p.axis_at(extent, &frame, y_ladder_pass.0),
                _ => p.axis(extent, &frame),
            }
        })
        .collect::<Result<_, String>>()?;
    // A layer value's offset from the plot top: on the layer's own scale, else the shared one.
    let layer_y_at = |index: usize, v: f64, ph: f64| match layer_axes.get(index) {
        Some(la) => la.scale(ph).position(v),
        None => ph * layer_y_frac(v),
    };
    // A line or scatter layer's value labels join the fit like any text mark: per
    // layer row its measure, caption width and spot.
    let layer_label_fit = overlays
        .iter()
        .zip(&layer_rows)
        .enumerate()
        .filter_map(|(index, (overlay, rows))| {
            let ml = match overlay {
                OverlayLayer::Line(l) => l.visible_labels(),
                OverlayLayer::Scatter(sl) => sl.point_mark.labels.visible.then_some(&sl.point_mark.labels),
                OverlayLayer::Bar(_) => None,
            }?;
            let is_house = match overlay {
                OverlayLayer::Line(l) => l.label_is_house,
                OverlayLayer::Scatter(sl) => sl.label_is_house,
                OverlayLayer::Bar(_) => unreachable!("a bar layer carries no line or scatter labels"),
            };
            Some((index, overlay, ml, is_house, *rows))
        })
        .map(|(index, overlay, ml, is_house, rows)| {
            let style = LayerLabelStyle::new(ml, is_house, rows, payload.board_style.as_ref())?;
            let items = crate::common::valued_rows(rows.iter(), overlay.y())
                .into_iter()
                .map(|(row, val)| {
                    let caption = style.caption(row, val, None)?;
                    Ok((row, val, style.width(fonts, &caption.text)?, caption.spot))
                })
                .collect::<Result<Vec<_>, String>>()?;
            Ok((index, style, r.x.as_str(), items))
        })
        .collect::<Result<Vec<_>, String>>()?;
    // A bar layer's rects over the rows that have an x, per overlay.
    let layer_bar_marks: Vec<Option<Vec<crate::families::layers::BarLayerMark>>> = overlays
        .iter()
        .zip(&layer_rows)
        .map(|(overlay, rows)| match overlay {
            OverlayLayer::Bar(bl) => {
                let x_field = bl.x.as_deref().unwrap_or(r.x.as_str());
                Ok(Some(placed_bar_layer_marks(bl, rows, x_field, &nan_x, |row| layer_x_frac(row, x_field, 1.0).is_some())))
            }
            OverlayLayer::Line(_) | OverlayLayer::Scatter(_) => Ok(None),
        })
        .collect::<Result<_, String>>()?;
    let x_title_extra = crate::common::x_title_extra(&ax_x.title, &x_title_text);

    // ── Title (width-resolved font slot) ────────────────────────────────
    // An over-wide title or subtitle takes the wrap-two law on every channel shape.
    // A facet panel carries no title: the composite owns it.
    let title_plan = if panel.is_some() {
        None
    } else {
        match layout_title(&r.title_source(), fonts, avail_w)? {
            TitleLayout::Untitled => None,
            TitleLayout::Plan(plan) => {
                plan.warn_truncated(&r.id);
                Some(plan)
            }
        }
    };
    let title_ink = title_plan.as_ref().map(|plan| plan.ink.clone());

    // ── Legend entries + panel width ─────────────────────────────────────
    // `wide_series` (the sorted, authored-measure-COMPLETE domain,
    // `common::wide_series_names` + humanization) feeds a wide chart's
    // domain instead of a row scan — a measure null on every row still owns
    // a domain/palette slot, same law bar.rs's own `color_domain` split
    // applies (`build_chart_domains`).
    let color_domain: &Vec<String> = &bound.color_domain;
    // The board's plan for this chart's color field, if it binds one
    // (#7167) — the point/legend fill defers to it over the local
    // alphabetical color_domain position.
    let category_scale: Option<&crate::common::CategoryColorScale> =
        r.color.as_deref().and_then(|cf| crate::common::category_scale_for(&r.category_colors, cf));
    // `_dot_plot_roles`: one category axis, one measure axis, a color split.
    let dot_plot = r.color.is_some()
        && match (infer_vega_type(&payload.rows, &r.x), infer_vega_type(&payload.rows, &r.y)) {
            (VegaType::Nominal, VegaType::Quantitative) | (VegaType::Quantitative, VegaType::Nominal) => true,
            _ => false,
        };
    let layered = !overlays.is_empty();
    let layered_legend =
        LayeredLegend::new(r, payload, overlays, color_domain.as_slice(), category_scale, &base_y_title, y_nominal)?;
    let layered_entries = layered_legend.entries(&r.legend);
    let numeric_legends = crate::families::bar::numeric_legends_of(
        None,
        overlays.iter().zip(&layered_legend.plan),
        &r.legend,
        &r.id,
    )?;
    // A layer forces the legend on over a colorless base: one entry per painted
    // series, each carrying its own mark's glyph. A quantitative color keeps
    // only its gradient.
    let legend_shown = panel.is_none()
        && r.legend.visible
        && (layered && !quantitative_color(r) && !layered_entries.is_empty()
            || r.color.is_some() && !color_domain.is_empty());
    let color_scale = bound.color_range.map(|(lo, hi)| ContinuousColor::blues(lo, hi));
    let gradient_title = match (&color_scale, r.color.as_deref()) {
        (Some(_), Some(cf)) if r.legend.visible && panel.is_none() => {
            Some(legend::legend_title_text(cf, &r.legend, &r.field_labels)?)
        }
        _ => None,
    };
    let gradient_id = legend_gradient_id(r);
    // The gradient legend's box at a plot size: its bar follows the plot.
    let gradient_block = |pw: f64, ph: f64| -> Result<Option<(f64, f64)>, String> {
        let (Some(scale), Some(title)) = (&color_scale, &gradient_title) else { return Ok(None) };
        GradientAt::measure(&r.legend, r.color_format.as_ref(), scale, (&gradient_id, title), fonts, (pw, ph)).map(Some)
    };
    // The shape scale: one Vega symbol per category of the shape field's
    // sorted domain, `range.symbol` order. The same field on color merges the
    // two encodings into the color legend (its symbols vary); a shape alone
    // draws its own unconfigured legend.
    let shape_domain =
        r.shape.as_deref().map_or_else(Vec::new, |sf| shape_scale_domain(drawn.iter().copied(), sf, rule_shape));
    let merged_shape = shape_merges_into_color(r, rule_shape, panel.is_none() && ax_y.mirror_requested("scatter")?);
    let shape_legend = match r.shape.as_deref().filter(|_| panel.is_none() && !merged_shape) {
        Some(shape_field) => Some(crate::families::scatter_shape::ShapeLegend::new(
            fonts,
            payload,
            shape_field.to_string(),
            shape_domain.clone(),
        )?),
        None => None,
    };
    // A shape channel on the color field merges into the color legend with
    // its own glyphs.
    let mut wide_raw: Vec<&str> = r.wide_measures.iter().map(String::as_str).collect();
    wide_raw.sort_unstable();
    let legend_order = if legend_shown {
        let raw_measures = (!wide_series.is_empty()).then_some(wide_raw.as_slice());
        legend_entry_order(&r.legend, &color_domain, raw_measures)?
    } else {
        Vec::new()
    };
    let unfilled = r.style.point_mark.filled == Some(false);
    let mark_symbol = r.style.point_mark.shape.as_ref().and_then(Value::as_str).filter(|_| r.shape.is_none());
    let legend_glyph_names = color_legend_glyph_names(r, color_domain.len(), merged_shape);
    let base_glyph = layered_legend.base_glyph;
    let color_legend = if legend_shown && layered {
        let layout = legend::vega_legend(&layered_entries, layered_legend.mixed_marks, &layered_legend.title(r)?, &r.legend, fonts)?;
        let layout = numeric_legends.stack_onto(layout, &r.legend, fonts, (payload.width, payload.height))?;
        Some((layered_entries.iter().map(|e| e.label.to_string()).collect(), layout))
    } else if legend_shown {
        let ring = unfilled.then_some(legend_glyph_names.as_slice());
        Some(color_legend_layout(r, &color_domain, &legend_order, fonts, ring, mirror_active)?)
    } else {
        None
    };
    // `_stamp_series_keys` (`converters/chart.py:467-488`) stamps a legend
    // label when a tooltip names a series row and none ranks it with a
    // `ROLE_ORDER`: a dot plot's tooltip always names its series
    // (`_dot_plot_roles`) and ranks it only under a board color scale.
    let stamp_legend_series = tooltip_labels && dot_plot && category_scale.is_none();
    let (legend_w, legend_h) = color_legend.as_ref().map_or((0.0, 0.0), |(_, lg)| (lg.box_w, lg.box_h()));

    // The size legend's symbols follow an authored point shape, unless a shape
    // channel owns the mark's glyph.
    let size_legend_shown = panel.is_none() && r.size.is_some() && r.legend.visible;
    let size_legend = match size_domain.filter(|_| size_legend_shown) {
        Some(domain) => Some(size_legend(r, fonts, domain, mark_symbol.unwrap_or("circle"))?),
        None => None,
    };
    let (size_legend_w, size_legend_h) = size_legend.as_ref().map_or((0.0, 0.0), |sl| (sl.w, sl.h));
    // The size legend stacks under the color legend at Vega's fixed
    // inter-legend margin; with no color legend drawn it is the only legend
    // and sits at the legend column's origin.
    // The shape legend registers last (`encoding` order: color, size, shape).
    // Color and size follow `legend.position`; a shape legend always stacks
    // at the right, under them when that is the edge. Beside a side edge the
    // legends flow downward, beside a top or bottom one along it, each
    // `VEGA_LEGEND_GROUP_MARGIN` past the last; the stack shares its origin.
    let edge = r.legend.position.edge.as_str();
    let stack_vertical = matches!(edge, "left" | "right");
    // A rule layer's shape legend registers ahead of the others and sits above
    // them on the right edge.
    let right_strip = edge == "right" && !r.legend.position.overlay;
    let shape_block = shape_legend.as_ref().filter(|_| right_strip).map(|sl| (sl.w, sl.h));
    // Where each legend sits in the shared stack, given the color legend's
    // box (a gradient's follows the plot, so the stack is laid out per fit
    // pass).
    let legend_stack = |color_block: Option<(f64, f64)>| -> LegendStack {
        let size_block = size_legend_shown.then_some((size_legend_w, size_legend_h));
        let (lead, trail) = if rule_shape { (shape_block, None) } else { (None, shape_block) };
        let slots = [lead, color_block, size_block, trail];
        let placed: Vec<(usize, (f64, f64))> = slots.iter().enumerate().filter_map(|(i, b)| Some((i, (*b)?))).collect();
        let (along, _) = stack_legends(&placed.iter().map(|&(_, b)| b).collect::<Vec<_>>(), !stack_vertical);
        let mut offsets = [0.0_f64; 4];
        for (&(slot, _), off) in placed.iter().zip(along) {
            offsets[slot] = off;
        }
        let at = |off: f64| if stack_vertical { (0.0, off) } else { (off, 0.0) };
        let (_, size) = stack_legends(&[color_block, size_block].into_iter().flatten().collect::<Vec<_>>(), !stack_vertical);
        LegendStack { color_off: at(offsets[1]), size_off: at(offsets[2]), shape_y: offsets[if rule_shape { 0 } else { 3 }], size }
    };
    let color_legend_shown = legend_shown || gradient_title.is_some();
    let edge_stack = color_legend_shown || size_legend_shown;

    // Production's `value_labels` text layer (`_POINT_POS_MAP`) draws one label
    // per kept point at the board font; its ink joins the fit like any mark's,
    // so text and measured advance are fixed ahead of the plot rect.
    let value_labels = r.style.point_mark.labels.as_ref().filter(|l| l.visible);
    let value_label_font = value_labels.and_then(|ml| ml.font.as_ref());
    let value_label_family = match (value_labels, value_label_font.and_then(|f| f.family.as_deref())) {
        (None, _) => "",
        (Some(_), Some(f)) => f,
        (Some(_), None) => payload
            .board_style
            .as_ref()
            .and_then(|b| b.font.as_deref())
            .ok_or("scatter: point value labels need board_style.font")?,
    };
    let value_label_size = value_label_font.and_then(|f| f.size).unwrap_or(11.0);
    let spot = value_labels
        .map(|ml| crate::common::CaptionSpot::of(ml, None))
        .transpose()
        .map_err(|e| format!("scatter: {e}"))?;
    // Unread when no labels are drawn.
    let (value_label_align, value_label_baseline, value_label_dx, value_label_dy) =
        spot.map_or(("center", "bottom", 0.0, 0.0), |s| (s.align, s.baseline, s.dx, s.dy));
    // (text, shaped advance) per point, parallel to `points_data`.
    // A categorical y gives the text layer's quantitative `y` no position, so
    // Vega draws none of it.
    let mut value_label_texts: Vec<(String, f64)> = Vec::new();
    if let Some(ml) = value_labels.filter(|_| !y_nominal) {
        let field_is_text = ml.field_is_text(&payload.rows);
        for &(_, _, _, _, row) in &points_data {
            let yv = row_num(row, &r.y)
                .ok_or_else(|| "scatter: internal error: value label y missing".to_string())?;
            let (text, _) = ml
                .field_text(r.style.label_is_house, field_is_text, Some(row), yv)
                .map_err(|e| format!("scatter: {e}"))?;
            let width = fonts.measure_shaped(&text, value_label_family, value_label_size)?;
            value_label_texts.push((text, width));
        }
    }

    // ── Mark geometry ────────────────────────────────────────────────────
    // Vega symbol size is the AREA of the bounding square (data mark
    // d="M3.873,0A…" for size 60; legend symbol r=5 at size 100) — each
    // point's own radius already lives in `points_data` (`mark_radii`,
    // above), constant with no size channel and per-point scaled with one.
    let mark_sw = r.style.point_mark.stroke_width.unwrap_or(0.0);
    let mark_opacity = r.style.point_mark.opacity.unwrap_or(1.0);
    let symbol_stroke_ink = crate::common::symbol_stroke_ink(r.style.point_mark.filled, mark_sw);

    // The x scale's ticks and points at a plot `pw` wide: a date axis moves
    // with the width (a padded scale, Vega's own bare ticks), every other
    // axis was placed above.
    let x_frame_unrounded = |pw: f64| -> Result<XFrame, String> {
        if let Some(axis) = &temporal_x {
            if crate::labels_expr::view::active() {
                axis.register(pw);
            }
            return Ok(XFrame {
                ticks: axis.tick_fracs(pw, fonts)?,
                labels: Vec::new(),
                scale: None,
                points: kept.iter().map(|&(_, row)| axis.frac(&row_str(row, &r.x), pw)).collect(),
                rules: [None, None],
            });
        }
        if let Some(axis) = plain_x {
            if crate::labels_expr::view::active() {
                axis.register(pw)?;
            }
            let (mut ticks, labels, rules) = axis.ticks_at(pw)?;
            // A band axis sits half a pixel left of its band centers.
            if axis.bandwidth().is_some() {
                ticks.iter_mut().for_each(|t| *t -= 0.5 / pw);
            }
            let points = kept.iter().map(|&(xv, row)| axis.frac(xv, row, &r.x, pw)).collect::<Result<_, String>>()?;
            return Ok(XFrame { ticks, labels, scale: Some(axis.caption_scale(ax_x, pw)?), points, rules });
        }
        if let Some((lo, hi)) = log_x {
            let (ladder, labels) = log_x_axis(ax_x, lo, hi, pw)?;
            if crate::labels_expr::view::active() {
                crate::labels_expr::view::register_log("x", ladder.lo, ladder.hi, crate::labels_expr::view::Span::Width);
            }
            return Ok(XFrame {
                ticks: ladder.ticks.iter().map(|&tv| ladder.frac_from_left(tv)).collect(),
                labels,
                scale: Some(CaptionScale::Numeric { kind: NumericScale::Log, domain: (ladder.lo, ladder.hi), format: vega_axis_format(ax_x, true)? }),
                points: kept.iter().map(|&(xv, _)| ladder.frac_from_left(xv)).collect(),
                rules: [None, None],
            });
        }
        if let Some(lin) = &linear_x {
            let (ticks, labels, rules, domain) = lin.at(pw)?;
            if crate::labels_expr::view::active() {
                crate::labels_expr::view::register_bent("x", lin.bend, domain.0, domain.1, crate::labels_expr::view::Span::Width);
            }
            return Ok(XFrame {
                ticks,
                labels,
                scale: Some(CaptionScale::Numeric { kind: NumericScale::of_axis(ax_x)?, domain, format: vega_axis_format(ax_x, true)? }),
                points: kept.iter().map(|&(xv, _)| lin.frac(domain, xv)).collect(),
                rules,
            });
        }
        // A band axis sits half a pixel left of its band centers (Vega places a
        // band tick at `scale(d) + bandwidth / 2` under the axis's own +0.5 shift).
        let band_shift = if x_bandwidth.is_some() { 0.5 / pw } else { 0.0 };
        if crate::labels_expr::view::active() && x_nominal {
            use crate::labels_expr::view::{register_band, register_point_padded, Span};
            let padding = if mirror_wants_band && x_bandwidth.is_none() { 0.5 } else { point_x_padding(ax_x) };
            if x_bandwidth.is_some() {
                register_band("x", &x_labels, padding, padding, Span::Width)?;
            } else {
                register_point_padded("x", &x_labels, padding, Span::Width)?;
            }
        }
        Ok(XFrame {
            ticks: x_ticks.iter().map(|t| t - band_shift).collect(),
            labels: x_shown.clone(),
            scale: Some(CaptionScale::Discrete { values: x_labels.clone(), format: vega_axis_format(ax_x, false)? }),
            points: kept_x_frac.clone(),
            rules: [None, None],
        })
    };

    // `scale.round` rounds every position the scale yields.
    let x_frame = |pw: f64| -> Result<XFrame, String> {
        let mut frame = x_frame_unrounded(pw)?;
        if let Some(round) = &nominal_round {
            let shift = if x_bandwidth.is_some() { 0.5 / pw } else { 0.0 };
            // A facet's ticks are already rounded, so its unrounded centers come from the scale.
            let centers: Vec<f64> = match plain_x {
                Some(plain) => plain.category_centers().to_vec(),
                None => frame.ticks.iter().map(|t| t + shift).collect(),
            };
            for frac in &mut frame.points {
                *frac = round.snap(*frac, &centers, pw);
            }
            for (tick, center) in frame.ticks.iter_mut().zip(&centers) {
                *tick = round.snap(*center, &centers, pw) - shift;
            }
        } else if x_round {
            let round = |frac: &mut f64| *frac = (*frac * pw).round() / pw;
            frame.ticks.iter_mut().for_each(round);
            frame.points.iter_mut().for_each(round);
            frame.rules.iter_mut().flatten().for_each(round);
        }
        Ok(frame)
    };

    // The ink of every layer's marks and value labels at a plot `pw` by `ph`, in the plot's frame.
    let layer_ink = |pw: f64, ph: f64| -> Result<vega_fit::Bounds, String> {
        let mut b = vega_fit::Bounds::empty_new();
        let b = &mut b;
        // A shared-scale layer's marks reach past a datum by their stroke
        // or point ring.
        for (overlay_index, (overlay, rows)) in overlays.iter().zip(&layer_rows).enumerate() {
            let reach = match overlay {
        OverlayLayer::Line(l) => l.mark_reach(),
        OverlayLayer::Scatter(sl) => {
            symbol_radius(sl.size())
                + crate::common::symbol_stroke_ink(sl.point_mark.filled, sl.point_mark.stroke_width)
        }
        OverlayLayer::Bar(bl) => {
            // A clipped layer adds no ink past the plot.
            if crate::families::layers::layer_clip(overlay.layer_type(), overlay.y(), rows, ax_y, false, independent) {
                continue;
            }
            let stroke = bl.bar_mark.border.width.unwrap_or(0.0);
            let x_field = overlay.x().unwrap_or(&r.x);
            let marks = layer_bar_marks[overlay_index].as_ref().expect("a bar layer has its marks");
            for mark in marks {
                let x_frac = layer_x_frac(mark.row, x_field, pw).expect("marks keep rows with an x");
                let (left, width, _) = bar_x.slot(bl, (rows, x_field), (mark.row, x_frac * pw), pw, bl.x.is_none())?;
                let (y1, y2) = (layer_y_at(overlay_index, mark.from, ph), layer_y_at(overlay_index, mark.to, ph));
                b.union(
                    &vega_fit::Bounds::new(left, y1.min(y2), left + width, y1.max(y2)).expand(stroke),
                );
            }
            continue;
        }
            };
            // A band-step layer's plateaus reach its band edges, where its stroke adds ink.
            let plateaus = match (overlay, step_band(pw)) {
        (OverlayLayer::Line(l), Some(band)) => l.band_step_ink()?.map(|(plateau, reach)| (band, plateau, reach)),
        _ => None,
            };
            for row in *rows {
        let (Some(x_frac), Some(v)) =
            (layer_x_frac(row, overlay.x().unwrap_or(&r.x), pw), row_num(row, overlay.y()))
        else {
            continue;
        };
        let (px, py) = (x_frac * pw, layer_y_at(overlay_index, v, ph));
        b.union(&vega_fit::Bounds::new(px, py, px, py).expand(reach));
        if let Some((band, plateau, plateau_reach)) = &plateaus {
            let (lo, hi) = crate::step_band::band_step_edges(*plateau);
            let left = px - band.bandwidth / 2.0;
            b.union(
                &vega_fit::Bounds::new(left + band.bandwidth * lo, py, left + band.bandwidth * hi, py)
                    .expand(*plateau_reach),
            );
        }
            }
        }
        for (overlay_index, style, x_field, items) in &layer_label_fit {
            for (row, val, width, spot) in items {
        let Some(x_frac) = layer_x_frac(row, x_field, pw) else { continue };
        b.union(&style.ink(x_frac * pw, layer_y_at(*overlay_index, *val, ph), *width, spot));
            }
        }
        for (overlay_index, ((overlay, rows), marks)) in overlays.iter().zip(&layer_rows).zip(&layer_bar_marks).enumerate() {
            let (OverlayLayer::Bar(bl), Some(marks)) = (overlay, marks) else { continue };
            let (sited, centers) = bar_label_sites(bl, rows, marks, pw)?;
            let label_marks: Vec<_> = sited.iter().map(|&i| marks[i]).collect();
            // A bucketed date axis seats its labels at the bucket start, which Vega's fit
            // never reads as ink.
            let bucketed = bar_x
                .temporal
                .zip(label_marks.first())
                .is_some_and(|(axis, mark)| axis.bucket_span(&row_str(mark.row, &r.x), pw).is_some());
            if !bucketed {
                crate::families::bar::layer_bar_label_ink(
        bl, rows, &label_marks, &centers, &|v| layer_y_at(overlay_index, v, ph), ph, fonts, payload.board_style.as_ref(), b,
                )?;
            }
        }
        Ok(*b)
    };

    // The chart's own ink past the plot, set by the fit's ink closure on every
    // pass; the last write is the drawn size's.
    let annotation_reach = std::cell::Cell::new(crate::annotation::Reach::default());
    // The annotation maps at a plot size: the scales the marks draw with. A
    // scale an annotation cannot be mapped through is refused by name.
    let annotation_ctx = |pw: f64, ph: f64| -> Result<crate::annotation::AnnotationCtx, Diagnostic> {
        use crate::annotation::{AnnotationCtx, AxisMap, Orientation, PlotRect};
        let refuse = |reason: &str| {
            let index = r.annotations.first().map_or(0, |a| a.index);
            crate::annotation::model::payload_error(&r.id, index, reason)
        };
        let x = if x_nominal {
            let frame = x_frame(pw)?;
            let shift = if x_bandwidth.is_some() { 0.5 / pw } else { 0.0 };
            let positions = frame.ticks.iter().map(|t| (t + shift) * pw).collect();
            AxisMap::Point { domain: plain_x.and_then(PlainXAxis::raw_domain).map_or_else(|| x_labels.clone(), <[String]>::to_vec), positions, days: None, range: (0.0, pw) }
        } else if let Some(axis) = bar_x.temporal {
            axis.day_domain(pw).map(|extent| AxisMap::Time { extent, range: (0.0, pw) }).ok_or_else(|| refuse("annotations on a date x need its time scale, not a point scale"))?
        } else if let Some(lin) = bar_x.linear {
            let domain = lin.domain_at(pw);
            match lin.bend {
                vega_fit::Bend::Linear => AxisMap::Linear { domain, range: (0.0, pw) },
                bend => AxisMap::Bent { bend, domain, range: (0.0, pw) },
            }
        } else if let Some(log) = &bar_x.log {
            let ladder = log.ladder(pw)?;
            AxisMap::Log { domain: (ladder.lo, ladder.hi), range: (0.0, pw) }
        } else {
            return Err(refuse("annotations cannot map this x axis"));
        };
        let y = match y_scale.as_ref().expect("every y branch sets its scale") {
            YScale::Bent(vega_fit::Bend::Linear, domain) => AxisMap::Linear { domain: *domain, range: (ph, 0.0) },
            YScale::Bent(vega_fit::Bend::Log, domain) => AxisMap::Log { domain: *domain, range: (ph, 0.0) },
            YScale::Bent(bend, domain) => AxisMap::Bent { bend: *bend, domain: *domain, range: (ph, 0.0) },
            YScale::Points { fracs, domain } => AxisMap::Point {
                domain: domain.clone(),
                positions: fracs.iter().map(|f| f * ph).collect(),
                days: None,
                range: (ph, 0.0),
            },
        };
        let layer_y = crate::families::layers::layer_y_maps(&y, &layer_axes, overlays.len(), ph);
        Ok(AnnotationCtx {
            annotations: &r.annotations,
            chart_id: &r.id,
            panel: panel.map(|o| o.panel_key),
            plot: PlotRect { x: 0.0, y: 0.0, w: pw, h: ph },
            reach: annotation_reach.get(),
            x,
            y,
            layer_y,
            orientation: Orientation::Vertical,
            fonts,
            canvas: bg,
            clip_id: format!("{}-ann{}", crate::contract::safe_svg_id(&r.id), panel.map_or(0, |o| o.panel_ordinal)),
        })
    };
    if !r.annotations.is_empty() {
        annotation_ctx(svg_w, svg_h)?;
    }

    // ── Fit ──────────────────────────────────────────────────────────────
    let mut fit_err: Option<String> = None;
    // How far the topmost drawn value label reaches above the plot's y=0
    // (see `PanelGeometry::top_axis_overhang`); only a facet reads it.
    let top_overhang = std::cell::Cell::new(0.0_f64);
    // A color legend off the right edge: its origin in plot coordinates, set
    // on every pass (the last is the fitted layout).
    let legend_xy = std::cell::Cell::new((0.0, 0.0));
    let mut ink_pass = |pw: f64, ph: f64, b: &mut vega_fit::Bounds| {
            top_overhang.set(0.0);
            if fit_err.is_some() {
                return;
            }
            let ink = |b: &mut vega_fit::Bounds| -> Result<(), String> {
                let xf = x_frame(pw)?;
                // Label band + rotated y title, on the axis's own side.
                let y_reach = if shared_left { 0.0 } else { y_edge_at(pw, ph) + y_title_extra };
                b.union(&if y_left {
                    vega_fit::Bounds::new(-y_reach, 0.0, pw, ph)
                } else {
                    vega_fit::Bounds::new(0.0, 0.0, pw + y_reach, ph)
                });
                if y_title_extra > 0.0 && panel.is_none_or(|o| o.paint_value_axis) {
                    let x = if y_left { -y_reach } else { pw + y_reach };
                    b.union(&vega_fit::Bounds::new(x, ph / 2.0 - y_title_w / 2.0, x, ph / 2.0 + y_title_w / 2.0));
                }
                let log_visible = Some(y_thinned(pw, ph));
                for (i, &frac) in y_ticks.iter().enumerate() {
                    if log_visible.as_ref().is_some_and(|v| !v[i]) {
                        continue;
                    }
                    let mut label = y_label_ink(pw, ph, i);
                    // A facet's value axis is the composite's `row_footer`, a
                    // role apart from the cell: a label overhanging its own
                    // row's top or bottom edge never moves the row's marks —
                    // only the gutter's WIDTH reaches the cell.
                    if panel.is_some() {
                        top_overhang.set(top_overhang.get().min(label.y1));
                        label.y1 = label.y1.max(0.0);
                        label.y2 = label.y2.min(ph);
                    }
                    if shared_left {
                        continue;
                    }
                    b.union(&label);
                    if mirror_active {
                        let (x, align) = if y_left { (pw + ghost_off, if ghost_anchor == "end" { "right" } else { "left" }) } else { (-y_label_off, "right") };
                        b.union(&vega_fit::text_ink(x, y_px(ph, frac, y_round), ghost_ws[i].min(y_limit), y_font_size, align, "middle"));
                    }
                }
                // X labels: flush anchors at the plot edges on a continuous
                // scale (labelFlush default true); a nominal scale's bands
                // always center, edges included — tilted per the ladder
                // above (`x_cat_angle`), reusing bar's own rotated-ink law
                // (`common::rotated_category_label_ink`).
                let row_y = x_edge.at(ph, x_axis_offset + x_tick_eff + x_label_pad);
                // The x axis's own bounds: Vega's `yBounds` beyond the plot
                // rect (`ViewLayout.js:62-65`), read by a bottom legend.
                let mut x_axis = vega_fit::Bounds::new(0.0, 0.0, pw, ph);
                // A facet draws its shared x axis once, below the last row:
                // no panel reserves ink for it.
                if panel.is_none() {
                    if let Some(axis) = &temporal_x {
                        axis.union_label_ink(pw, ph, &mut x_axis, fonts)?;
                    }
                }
                let x_label_visible = (ax_x.is_quantitative && panel.is_none()).then(|| {
                    let widths = xf
                        .labels
                        .iter()
                        .map(|lbl| fonts.measure(lbl, x_family, x_font_size))
                        .collect::<Result<Vec<f64>, _>>()?;
                    Ok::<Vec<bool>, String>(visible_tick_labels(&xf.labels, ax_x.labels.min_gap.unwrap_or(0.0), ax_x.is_log_scale(), |i| {
                        let pos = xf.ticks[i] * pw;
                        vega_fit::text_ink_rows(
                            pos,
                            row_y,
                            widths[i].min(x_limit),
                            x_font_size,
                            x_flush_align(pos, pw),
                            x_edge.baseline(),
                            crate::text::line_count(&xf.labels[i]),
                        )
                    }))
                });
                let x_label_visible = x_label_visible.transpose()?;
                for (i, (&frac, lbl)) in
                    xf.ticks.iter().zip(xf.labels.iter()).enumerate().filter(|_| panel.is_none())
                {
                    if x_label_visible.as_ref().is_some_and(|v| !v[i]) {
                        continue;
                    }
                    let pos = frac * pw;
                    // Shaped: Vega bounds the drawn label (laws-shared.md, shaped vs raw).
                    let w = fonts.measure_shaped(lbl, x_family, x_font_size)?;
                    if x_cat_angle != 0.0 {
                        x_axis.union(&crate::common::rotated_category_label_ink(
                            pos,
                            row_y,
                            w.min(x_limit),
                            x_font_size,
                            x_cat_align,
                            x_cat_baseline,
                            x_cat_angle,
                            crate::text::line_count(lbl) - 1,
                        ));
                        continue;
                    }
                    let align = x_flush_align(pos, pw);
                    x_axis.union(&vega_fit::text_ink_rows(
                        pos,
                        row_y,
                        w.min(x_limit),
                        x_font_size,
                        align,
                        x_edge.baseline(),
                        crate::text::line_count(lbl),
                    ));
                }
                // Outermost drawn tick strokes.
                if x_ticks_visible && panel.is_none() {
                    let tw = ax_x.ticks.width.unwrap_or(1.0);
                    if let (Some(&first), Some(&last)) = (xf.ticks.first(), xf.ticks.last()) {
                        for frac in [first, last] {
                            let pos = frac * pw;
                            let seat = x_edge.edge_y(ph);
                            x_axis.union(&vega_fit::Bounds::new(pos, seat, pos, seat).expand(tw));
                        }
                    }
                }
                b.union(&x_axis);
                if x_title_extra > 0.0 && panel.is_none() {
                    let (y1, y2) = match x_edge {
                        crate::common::XEdge::Bottom => (0.0, ph + (b.y2 - ph).max(x_axis_offset) + x_title_extra),
                        crate::common::XEdge::Top => (-((-b.y1).max(x_axis_offset) + x_title_extra), ph),
                    };
                    x_axis.union(&vega_fit::Bounds::new(pw / 2.0 - x_title_w / 2.0, y1, pw / 2.0 + x_title_w / 2.0, y2));
                    b.union(&x_axis);
                }
                // Symbol ink: every point expanded by its own circle radius
                // and, when unfilled, its stroke (a filled point has no stroke
                // color, so boundStroke adds none).
                for (&(_, y_frac, _, pr, _), &x_frac) in points_data.iter().zip(&xf.points) {
                    let px = x_frac * pw;
                    let py = y_px(ph, y_frac, y_round);
                    b.union(&vega_fit::Bounds::new(px, py, px, py).expand(pr + symbol_stroke_ink));
                }
                for ((&(_, y_frac, _, _, _), &x_frac), (_, w)) in points_data.iter().zip(&xf.points).zip(&value_label_texts) {
                    b.union(&vega_fit::text_ink(
                        x_frac * pw + value_label_dx,
                        y_px(ph, y_frac, y_round) + value_label_dy,
                        *w,
                        value_label_size,
                        value_label_align,
                        value_label_baseline,
                    ));
                }
                b.union(&layer_ink(pw, ph)?);
                for (la, zero_rule) in layer_axes.iter().zip(&layer_zero_rules) {
                    la.union_axis_ink(b, (pw, ph), 0.0, y_limit, y_font_size);
                    if let Some(width) = zero_rule {
                        let y = la.scale(ph).position(0.0);
                        b.union(&vega_fit::Bounds::new(0.0, y, pw, y).expand(*width));
                    }
                }
                // Baseline rules' own ink — each a full-plot-width (Y) or
                // full-plot-height (X) stroke at its exact scale position,
                // unioned the way `line.rs`'s ink pass does, or the fitted
                // margins are wrong once it paints (it can land outside
                // [0, ph]/[0, pw] entirely, e.g. an all-negative measure
                // axis). Each pair shares ONE `grid.threshold` style
                // (production styles them from the same zero_style object
                // per axis), so the width is read once per axis.
                if let Some(z) = ax_y.grid.threshold.as_ref() {
                    for frac in baseline_rule_fracs.into_iter().flatten() {
                        let ry = y_px(ph, frac, y_round);
                        b.union(
                            &vega_fit::Bounds::new(0.0, ry, pw, ry).expand(z.width.unwrap_or(2.0)),
                        );
                        if rule_shape {
                            b.union(
                                &vega_fit::Bounds::new(0.0, ry, 0.0, ry)
                                    .expand(crate::common::symbol_radius(RULE_POINT_SIZE)),
                            );
                        }
                    }
                }
                if let Some(zx) = ax_x.grid.threshold.as_ref() {
                    for frac in xf.rules.into_iter().flatten() {
                        let rx = pw * frac;
                        b.union(
                            &vega_fit::Bounds::new(rx, 0.0, rx, ph).expand(zx.width.unwrap_or(2.0)),
                        );
                        if rule_shape {
                            b.union(
                                &vega_fit::Bounds::new(rx, 0.0, rx, 0.0)
                                    .expand(crate::common::symbol_radius(RULE_POINT_SIZE)),
                            );
                        }
                    }
                }
                // Annotation text clears the ink so far: axes, titles, marks.
                annotation_reach.set(crate::annotation::Reach::past(b, pw, ph));
                let mut annotation_x2 = 0.0_f64;
                if !r.annotations.is_empty() {
                    if let Some(ab) = annotation_ctx(pw, ph).ok().and_then(|ctx| crate::annotation::bounds(&ctx)) {
                        b.union(&ab);
                        annotation_x2 = ab.x2;
                    }
                }
                // Legend: a right one (with the size and shape legends
                // stacked under it) at the view ink's right edge + offset;
                // any other edge through `legendParams`, the y axis sitting
                // right so `xBounds.x1` is the plot's own 0.
                let ghost_reach = if y_left && mirror_active { ghost_edge_at(pw, ph) } else { 0.0 };
                let right_edge = layer_axes.iter().fold(if y_left { pw + ghost_reach } else { pw + y_reach }, |edge, la| {
                    la.widen_axes((0.0, edge), pw, 0.0).1
                });
                // A side legend clears annotation text past the plot too.
                let right_edge = right_edge.max(annotation_x2);
                let lx = right_edge + VEGA_LEGEND_OFFSET;
                let color_block = if legend_shown { Some((legend_w, legend_h)) } else { gradient_block(pw, ph)? };
                let stack = legend_stack(color_block);
                if edge_stack && !right_strip {
                    let x_bounds = (if y_left { -y_reach } else { 0.0 }, pw + ghost_reach);
                    let x_bounds = layer_axes.iter().fold(x_bounds, |bounds, la| la.widen_axes(bounds, pw, 0.0));
                    let x_bounds = (x_bounds.0, x_bounds.1.max(annotation_x2));
                    let origin = legend::legend_origin(&r.legend.position, x_bounds, (x_axis.y1, x_axis.y2), (pw, ph), stack.size);
                    legend_xy.set(origin);
                    if let Some(reach) = legend::legend_fit_bounds(&r.legend.position, origin, stack.size) {
                        b.union(&reach);
                    }
                } else {
                    // A side legend joins the view bounds by its far edge alone
                    // (`legend_fit_bounds`), so one taller than its plot costs
                    // the plot nothing.
                    let widths = [
                        color_block.map(|(w, _)| w),
                        size_legend_shown.then_some(size_legend_w),
                        shape_legend.as_ref().map(|sl| sl.w),
                    ];
                    for w in widths.into_iter().flatten() {
                        b.union(&vega_fit::Bounds::new(lx + w, 0.0, lx + w, 0.0));
                    }
                }
                if let Some(sl) = shape_legend.as_ref().filter(|_| !right_strip) {
                    b.union(&vega_fit::Bounds::new(lx, stack.shape_y, lx + sl.w, stack.shape_y + sl.h));
                }
                Ok(())
            };
            if let Err(e) = ink(b) {
                fit_err = Some(e);
            }
    };
    let m = match panel {
        Some(o) => vega_fit::pad_plot(o.plot_w, o.plot_h, &mut ink_pass, None),
        None => vega_fit::fit_plot(view_w, svg_h, (pad_l, pad_r, pad_t, pad_b), &mut ink_pass, title_ink.as_ref()),
    };
    if let Some(e) = fit_err {
        return Err(e.into());
    }
    let (plot_w, plot_h) = (m.plot_w, m.plot_h);
    // Vega counts the unbaked ladder against the plot it lands in; the labels
    // move the plot only by their width, so the count settles in a pass or two.
    let nominal_layer_ladder = y_nominal && independent;
    if (!y_nominal && !ax_y.is_log_scale() && ax_y.tick_values.is_empty() || nominal_layer_ladder)
        && (panel.is_none() || independent_scale)
    {
        let (count, pass, _) = y_ladder_pass;
        let settled = crate::families::cartesian::vl_default_tick_count(plot_h).max(1);
        if settled != count {
            if pass >= 4 {
                return Err(format!("scatter: the y ladder of {:?} does not settle on a tick count", r.y).into());
            }
            return fit(bound, payload, fonts, panel, Some((settled, pass + 1, plot_h)));
        }
    }
    // A padded domain is drawn over the height it was widened for.
    if y_pad != 0.0 && panel.is_none() && (plot_h - y_ladder_pass.2).abs() > 1e-9 {
        let (count, pass, _) = y_ladder_pass;
        if pass >= 4 {
            return Err(format!("scatter: the padded y domain of {:?} does not settle on a plot height", r.y).into());
        }
        return fit(bound, payload, fonts, panel, Some((count, pass + 1, plot_h)));
    }
    // A facet panel's canvas starts at its plot's top edge: Vega's grid
    // layout works in the cell's own frame, where a mark reaching above the
    // plot has a NEGATIVE bound that the composite (not the panel) turns into
    // row spacing and its top margin. Only the left ink is kept as a margin,
    // which `compose_facet`'s `canvas_x` already subtracts back out.
    let (left_margin, top_margin) = (
        panel.map_or(m.left, |o| m.left.max(o.left_overhang)),
        if panel.is_some() { 0.0 } else { m.top },
    );

    // ── Emission ─────────────────────────────────────────────────────────
    // Outer canvas dims are `payload.width`/`payload.height` echoed
    // verbatim — vl-convert never re-rounds them the way it rounds
    // INTERNAL mark coordinates (the `{:.3}` grid/rule lines elsewhere in
    // this file); `py_float_str` matches its own float-to-string exactly
    // (whole numbers keep `.0`, others print full precision — same fix as
    // bar.rs's/line.rs's sibling arms, found via a repeating-decimal
    // `aspect_ratio` height, a `chrome_bytes`-only board-sweep DIFF
    // invisible to the pixel-only per-chart ratchet).
    // A panel's canvas is its plot plus the ink margins the fit measured.
    let (canvas_w, canvas_h) = match panel {
        Some(_) => (left_margin + plot_w + m.right, top_margin + plot_h),
        None => (view_w, svg_h),
    };
    let frame = ChartFrame::new(canvas_w, canvas_h, (pad_l, pad_r, pad_t, pad_b), bg, py_float_str);
    let head = match (m.title_pos, &title_plan) {
        (Some((tx, ty)), Some(plan)) => plan.nodes(&r.title_source(), pad_l + left_margin + tx, pad_t + top_margin + ty),
        _ => Vec::new(),
    };

    let group_x = pad_l + left_margin;
    let group_y = pad_t + top_margin;
    let mut plot: Vec<Node> = Vec::new();

    let xf = x_frame(plot_w)?;
    let points_data: Vec<PointDatum> =
        points_data.into_iter().zip(&xf.points).map(|(mut point, &x_frac)| { point.0 = x_frac; point }).collect();
    let x_pos = |frac: f64| frac * plot_w;
    let y_pos = |frac: f64| y_px(plot_h, frac, y_round);

    // Gridlines — grid/tick positions round to whole pixels (Vega
    // tickRound), then take the axis group's +0.5 crispness shift; labels
    // keep exact positions.
    if ax_x.grid.visible.unwrap_or(false) {
        let gc = crate::common::required_ink(ax_x.grid.color.as_deref(), "scatter", "axis_x.grid.color")?;
        let gw = ax_x.grid.width.unwrap_or(1.0);
        let go = ax_x.grid.opacity.unwrap_or(1.0);
        let lines = xf
            .ticks
            .iter()
            .map(|&frac| {
                let gx = x_pos(frac).round() + 0.5;
                Node::Line(
                    Attrs::new()
                        .set("x1", format!("{gx:.3}"))
                        .set("y1", "0.5")
                        .set("x2", format!("{gx:.3}"))
                        .set("y2", format!("{:.3}", plot_h + 0.5))
                        .set("stroke", gc)
                        .set("stroke-width", gw.to_string())
                        .set("opacity", go.to_string()),
                )
            })
            .collect();
        plot.push(crate::contract::grid_group(lines));
    }
    if ax_y.grid.visible.unwrap_or(false) {
        let gc = crate::common::required_ink(ax_y.grid.color.as_deref(), "scatter", "axis_y.grid.color")?;
        let gw = ax_y.grid.width.unwrap_or(1.0);
        let go = ax_y.grid.opacity.unwrap_or(1.0);
        let lines = y_ticks
            .iter()
            .map(|&frac| {
                let gy = y_pos(frac).round() + 0.5;
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

    plot.extend(crate::families::layers::layer_grids(&layer_axes, ax_y, (plot_w, plot_h), "scatter")?);
    let annotations = (!r.annotations.is_empty()).then(|| annotation_ctx(plot_w, plot_h)).transpose()?;
    if let Some(ctx) = &annotations {
        plot.extend(crate::annotation::nodes(ctx, crate::annotation::Plane::Background)?);
    }

    // Points.
    let color_for = |series: &str| -> Result<String, String> {
        if color_domain.is_empty() {
            return Ok(r.style.single_series_fill.clone());
        }
        crate::common::series_color(series, &color_domain, &r.series_palette, category_scale)
    };
    // A date x typed ordinal (an authored type, a fiscal year) rides as a string, which `timeFormat` reads as invalid.
    let x_law = if x_dates {
        let panel_fields: Vec<&str> = r.panel_axes.iter().map(|a| a.field.as_str()).collect();
        Some(crate::families::x_type::resolve_cartesian_x_type(
            &payload.rows,
            &r.x,
            &crate::families::x_type::XAxis::of(&r.style.axis_x),
            "scatter",
            false,
            &panel_fields,
        )?)
    } else {
        None
    };
    let x_link_text = match &x_law {
        _ if crate::families::temporal::has_labeled_bucket(&payload.rows, &r.x) => crate::href::LinkText::Date,
        Some(law) if law.vl_type == crate::families::temporal::VegaType::Temporal => crate::href::LinkText::Date,
        Some(_) => crate::href::LinkText::InvalidDate,
        None => crate::href::LinkText::Raw,
    };
    let datum_link = r.link.as_deref().map(|template| crate::href::DatumLink {
        template,
        open: r.link_open,
        x: crate::href::LinkField { name: &r.x, text: x_link_text },
        y: Some(crate::href::LinkField::raw(&r.link_y)),
        color: r.link_color.as_deref().map(crate::href::LinkField::raw),
        context: r.link_context.as_ref(),
    });
    // A layered base whose x rows were rewritten (labeled buckets, or canonicalized to a bucketed
    // calendar grain) carries its own data and loses its links (`_disable_private_data_channel`).
    let base_has_own_data = x_law.as_ref().is_some_and(|law| matches!(law.fill_grain, crate::families::x_type::Grain::Calendar(_)))
        || r.link_x_labeled;
    let base_linked = overlays.is_empty() || !base_has_own_data;
    let miter_limit = r
        .style
        .point_mark
        .shape
        .as_ref()
        .and_then(Value::as_str)
        .and_then(|symbol| crate::families::scatter_shape::miter_limit(crate::families::scatter_shape::half_extents(symbol, 1.0)));
    // Front-end contract: `_scatter_roles`, ported. y (dependent) leads, x
    // (independent) follows; a bound color channel promotes to a swatched
    // header. `xv` here is already the row's raw x reading (not the scaled
    // position) -- reused directly rather than re-reading the row.
    let y_axis_title = base_y_title.clone();
    let x_axis_title = crate::common::axis_title_text(&r.x, r.x_label.as_deref(), &r.field_labels)?;
    let color_title = match r.color.as_deref() {
        Some(cf) => legend::color_channel_title(cf, &r.legend, &r.field_labels)?,
        None => String::new(),
    };
    // Baseline rules — `BaselineFeature`'s `datum: 0`/`datum: 1` layers.
    // Production's `_insert_rule` (#8343) routes a scatter's rule to
    // `spec.underlays`, painted BENEATH the points. `apply()` calls
    // `_apply_zero_or_top`, `_apply_unity`, then `_apply_x_threshold` for
    // datum 0 and 1 — so the Y-zero rule (`baseline_rule_fracs[0]`) always
    // paints before the Y-unity rule (`baseline_rule_fracs[1]`), which
    // paints before the X-zero rule (`baseline_rule_fracs_x[0]`), which
    // paints before the X-unity rule (`baseline_rule_fracs_x[1]`) — each
    // array's own index order, Y before X. Each paints at its EXACT scale
    // position — Vega rounds grid and tick positions but never a rule mark.
    //
    // Mark-group ids: `translate.py`'s `_translate_standard` only promotes
    // to a VL `layer[]` spec when a baseline feature actually appends an
    // underlay/rule layer — `vl["layer"] = [*under_layers, main_layer,
    // *rule_layers]` — so a fired rule's layer index is its position in
    // the UNDERLAYS list (only the rules that actually fire), not its
    // fixed slot in `baseline_rule_fracs`/`baseline_rule_fracs_x`.
    // `fired_rule_count` tracks that running position across BOTH loops;
    // the points inherit whatever index comes after the last fired rule.
    let mut fired_rule_count = 0;
    if let Some(z) = ax_y.grid.threshold.as_ref() {
        let zc = z.color.as_deref().unwrap_or("#8A8C8F");
        let zw = z.width.unwrap_or(2.0);
        for (i, frac) in baseline_rule_fracs.iter().enumerate() {
            let Some(frac) = frac else { continue };
            let gy = y_pos(*frac);
            let rule_layer_name = if rule_shape {
                format!("layer_{fired_rule_count}_layer_0_marks")
            } else {
                format!("layer_{fired_rule_count}_marks")
            };
            plot.push(crate::contract::item_group(
                "rule",
                &rule_layer_name,
                crate::contract::ItemRole::RuleMark,
                crate::contract::Reads::Hidden,
                |aria| {
                    Node::Line(
                        Attrs::new()
                            .set("x1", "0.000")
                            .set("y1", format!("{gy:.3}"))
                            .set("x2", format!("{plot_w:.3}"))
                            .set("y2", format!("{gy:.3}"))
                            .set("stroke", zc)
                            .set("stroke-width", zw.to_string())
                            .extend(aria),
                    )
                },
            )?);
            if let Some(shape_field) = r.shape.as_deref().filter(|_| rule_shape) {
                let rule_value = if i == 0 { 0.0 } else { 1.0 };
                let aria = rule_aria(tooltip_labels, &r.style.tooltip_format.spec, &y_axis_title, rule_value)?;
                plot.extend(rule_point((0.0, gy), (shape_field, aria.as_ref(), fired_rule_count), (zc, zw))?);
            }
            fired_rule_count += 1;
        }
    }
    if let Some(zx) = ax_x.grid.threshold.as_ref() {
        let zc = zx.color.as_deref().unwrap_or("#8A8C8F");
        let zw = zx.width.unwrap_or(2.0);
        for (i, frac) in xf.rules.iter().enumerate() {
            let Some(frac) = frac else { continue };
            let gx = x_pos(*frac);
            let layer = fired_rule_count;
            let rule_layer_name = if rule_shape {
                format!("layer_{layer}_layer_0_marks")
            } else {
                format!("layer_{layer}_marks")
            };
            fired_rule_count += 1;
            plot.push(crate::contract::item_group(
                "rule",
                &rule_layer_name,
                crate::contract::ItemRole::RuleMark,
                crate::contract::Reads::Hidden,
                |aria| {
                    Node::Line(
                        Attrs::new()
                            .set("x1", format!("{gx:.3}"))
                            .set("y1", "0.000")
                            .set("x2", format!("{gx:.3}"))
                            .set("y2", format!("{plot_h:.3}"))
                            .set("stroke", zc)
                            .set("stroke-width", zw.to_string())
                            .extend(aria),
                    )
                },
            )?);
            if let Some(shape_field) = r.shape.as_deref().filter(|_| rule_shape) {
                let rule_value = if i == 0 { 0.0 } else { 1.0 };
                let aria = rule_aria(tooltip_labels, &r.style.tooltip_format.spec, &x_axis_title, rule_value)?;
                plot.extend(rule_point((gx, 0.0), (shape_field, aria.as_ref(), layer), (zc, zw))?);
            }
        }
    }

    // The points sit one layer index past the last fired rule (`main_layer`
    // in `_translate_standard`'s `[*under_layers, main_layer,
    // *rule_layers]`); with no fired rule the spec stays un-layered, so
    // Vega's own default group name ("marks") applies instead.
    let points_layer_name = if fired_rule_count > 0 || value_labels.is_some() {
        format!("layer_{fired_rule_count}_marks")
    } else {
        "marks".to_string()
    };

    let ghosts_on = (panel.is_some_and(|o| o.has_mirror) || mirror_active) && tooltip_labels;
    let ghost_ink = if ghosts_on {
        r.style.axis_y.labels.font.color.clone().ok_or("scatter: axis_y.labels.font.color absent (theme required)")?
    } else {
        String::new()
    };
    let mut ghost_points: Vec<(String, f64, f64, String, Option<String>)> = Vec::new();
    // With a shape channel the ghost rule layer carries a small point per datum beside its rule.
    let mut ghost_symbols: Vec<(String, f64, f64, String, String, Option<String>)> = Vec::new();
    let value_label_ink = value_label_font.and_then(|f| f.color.as_deref()).unwrap_or("#313233");
    let mut value_label_marks: Vec<(f64, f64, Option<String>, String, Option<String>)> = Vec::new();
    for (x_frac, y_frac, series, mark_r, row) in &points_data {
        let (cx, cy) = (x_pos(*x_frac), y_pos(*y_frac));
        let channel_ink = match &color_scale {
            Some(scale) => {
                let field = r.color.as_deref().expect("a color scale implies a color channel");
                scale.fill(row_num(row, field).expect("a drawn row carries its quantitative color reading"))
            }
            None => color_for(series)?,
        };
        // An unfilled mark keeps the authored interior and strokes in the
        // color channel's ink, or the mark's own color with no channel.
        let (fill, stroke) = if unfilled {
            let stroke = if r.color.is_some() { channel_ink.as_str() } else { unfilled_stroke(r) };
            (
                r.style.point_mark.fill.clone().expect("an unfilled mark's interior is checked in bind"),
                Some(stroke.to_string()),
            )
        } else {
            (channel_ink.clone(), None)
        };
        let aria = if tooltip_labels && dot_plot {
            let (category_field, measure_field, value_title) =
                if y_nominal { (&r.y, &r.x, &x_axis_title) } else { (&r.x, &r.y, &y_axis_title) };
            let series_field = r.color.as_deref().expect("dot_plot implies a color channel");
            let value = row_num(row, measure_field)
                .expect("a dot plot's measure axis is quantitative");
            let series_value = row_str(row, series_field);
            Some(crate::contract::dot_plot_aria_label(&crate::contract::DotPlotTooltip {
                category: &row_str(row, category_field),
                // A bound category-color scale bakes its domain, and the
                // tooltip then ranks the series in it (`_series_order_role`).
                order: category_scale.map(|_| crate::contract::tooltip_order_rank(&color_domain, &series_value)),
                series: &series_value,
                value_title,
                value,
                value_format: &r.style.tooltip_format,
            })?)
        } else if tooltip_labels && panel.is_some() {
            // A facet panel's marks carry Vega-Lite's own description (the
            // structured tooltip does not apply under `multiples`): x, y, then
            // the color channel, each as `title: value`.
            let x_value = match &temporal_x {
                Some(axis) => axis.native_date(&row_str(row, &r.x))?,
                None => row_str(row, &r.x),
            };
            let y_value = if y_nominal {
                category_text(row, &r.y)
            } else {
                let yv = row_num(row, &r.y)
                    .ok_or_else(|| "scatter: internal error: tooltip y value missing".to_string())?;
                r.style.tooltip_format.paint(yv).map_err(|e| format!("scatter: tooltip value {e}"))?
            };
            let mut rows = vec![(x_axis_title.as_str(), x_value), (y_axis_title.as_str(), y_value)];
            if let Some(cf) = r.color.as_deref() {
                rows.push((color_title.as_str(), row_str(row, cf)));
            }
            Some(crate::contract::native_label(&rows))
        } else if tooltip_labels {
            let header = r.color.as_deref().map(|cf| row_str(row, cf));
            let y_nominal_str;
            let y_value = if y_nominal {
                y_nominal_str = category_text(row, &r.y);
                crate::contract::ScatterValue::Nominal(&y_nominal_str)
            } else {
                let yv = row_num(row, &r.y)
                    .ok_or_else(|| "scatter: internal error: tooltip y value missing".to_string())?;
                crate::contract::ScatterValue::Quantitative(yv, &r.style.tooltip_format)
            };
            let x_nominal_str;
            let x_value = if let Some(axis) = &temporal_x {
                x_nominal_str = axis.tooltip_date(&row_str(row, &r.x))?;
                crate::contract::ScatterValue::Nominal(&x_nominal_str)
            } else if x_nominal {
                x_nominal_str = category_text(row, &r.x);
                crate::contract::ScatterValue::Nominal(&x_nominal_str)
            } else {
                let xv = row_num(row, &r.x)
                    .ok_or_else(|| "scatter: internal error — tooltip x value missing".to_string())?;
                crate::contract::ScatterValue::Quantitative(xv, &x_tooltip_format)
            };
            let tt = crate::contract::ScatterTooltip {
                header: header.as_deref(),
                y_title: &y_axis_title,
                y_value,
                x_title: &x_axis_title,
                x_value,
            };
            Some(crate::contract::scatter_aria_label(&tt)?)
        } else if !r.wide_measures.is_empty() && !r.style.tooltip_format.spec.is_empty() {
            // `StructuredTooltipFeature` refuses a wide scatter (see
            // `tooltip_labels`'s own doc comment above), so production's
            // `NativeTooltipAffixFeature` (`native_tooltip.py:213`) writes the
            // description straight off `_emit_wide_scatter`'s own
            // `encoding.tooltip` array: `[x, value, Series]`, plain
            // "title: value" rows joined "; ", no `ROLE_HEADER`/swatch markers
            // at all, the measure composed like every other affix. The x row
            // names no format of its own: a date prints its unit's `timeUnit`
            // text, any other x its value.
            let x_str = match &temporal_x {
                Some(axis) => axis.tooltip_date(&row_str(row, &r.x))?,
                None => match row.get(&r.x).and_then(Value::as_f64) {
                    Some(v) => crate::families::color_scale::js_number_string(v),
                    None => category_text(row, &r.x),
                },
            };
            let yv = row_num(row, &r.y)
                .ok_or_else(|| "scatter: internal error: tooltip y value missing".to_string())?;
            let ys = r.style.tooltip_format.paint(yv).map_err(|e| format!("scatter: tooltip value {e}"))?;
            let series_title = display_text::language::tr("tooltip.series", None, &[])?;
            Some(format!("{x_axis_title}: {x_str}; {y_axis_title}: {ys}; {series_title}: {series}"))
        } else {
            None
        };
        let label_attrs = crate::contract::aria_attrs(aria.as_deref(), crate::contract::ItemRole::Point)?;
        let d = point_glyph(r, row, &shape_domain, *mark_r)?;
        let href = datum_link.as_ref().filter(|_| base_linked).map(|link| link.href(row)).transpose()?;
        let anchor = r.link_open.anchor(href.as_deref());
        let glyph = Attrs::new()
            .set("transform", format!("translate({cx:.4},{cy:.4})"))
            .set("d", d)
            .set("fill", fill.as_str());
        let glyph = match &stroke {
            Some(stroke) => glyph
                .set("stroke", stroke.as_str())
                .set("stroke-width", mark_sw.to_string())
                .set_if(miter_limit.is_some(), "stroke-miterlimit", miter_limit.unwrap_or_default()),
            None => glyph.set_if(mark_sw > 0.0, "stroke-width", mark_sw.to_string()),
        };
        let mark = crate::href::linked(
            anchor,
            Node::Path(glyph.set("opacity", mark_opacity.to_string()).extend(label_attrs)),
        );
        plot.push(if aria.is_some() { crate::contract::mark_group("symbol", &points_layer_name, vec![mark])? } else { mark });
        if value_labels.is_some() && !y_nominal {
            // The text layer inherits the color channel as its fill.
            let ink = if r.color.is_some() { channel_ink.clone() } else { value_label_ink.to_string() };
            value_label_marks.push((cx, cy, aria.clone(), ink, href.clone()));
        }
        if let (true, Some(label)) = (ghosts_on, &aria) {
            let stroke = if r.color.is_some() { channel_ink.clone() } else { ghost_ink.clone() };
            ghost_points.push((label.clone(), cx, cy, stroke, href.clone()));
            if r.shape.is_some() {
                let glyph = point_glyph(r, row, &shape_domain, crate::common::symbol_radius(RULE_POINT_SIZE))?;
                ghost_symbols.push((glyph, cx, cy, fill.clone(), label.clone(), href.clone()));
            }
        }
    }
    if !value_label_marks.is_empty() {
        let weight = value_label_font.map(|f| f.weight_str()).unwrap_or_else(|| "400".into());
        let font_style = value_label_font.and_then(|f| f.style.as_deref());
        let baseline = value_label_dy + measure::baseline_shift(value_label_baseline, value_label_size);
        let mut labels = Vec::new();
        for ((cx, cy, aria, color, href), (text, _)) in value_label_marks.iter().zip(&value_label_texts) {
            let label_attrs = crate::contract::aria_attrs(aria.as_deref(), crate::contract::ItemRole::TextMark)?;
            let anchor = r.link_open.anchor(href.as_deref());
            let label = crate::common::value_label(
                cx + value_label_dx,
                cy + baseline,
                value_label_align,
                value_label_family,
                value_label_size,
                &weight,
                font_style,
                color,
                label_attrs,
                text,
            );
            labels.push(crate::href::linked(anchor, label));
        }
        if tooltip_labels {
            plot.push(crate::contract::value_label_group(&format!("layer_{}_marks", fired_rule_count + 1), labels)?);
        } else {
            plot.extend(labels);
        }
    }
    let native_affix = r.style.tooltip_format.has_affix()
        || x_tooltip_format.has_affix()
        || overlays.iter().any(|o| o.axis_y().tooltip_format_or(&r.style.tooltip_format).has_affix());
    // Overlay layers paint after the base's marks, on its x scale and the
    // shared value scale.
    let mut defined_clips: HashSet<String> = HashSet::new();
    for (index, (overlay, claim)) in overlays.iter().zip(&layered_legend.plan).enumerate() {
        let label = &claim.label;
        let claimed_fill = &claim.fill.clone().unwrap_or_default();
        let rows = layer_rows[index];
        let x_field = overlay.x().unwrap_or(&r.x);
        let x_title = if overlay.x().is_some() { x_field } else { x_axis_title.as_str() };
        let x_of = |row: &serde_json::Map<String, Value>| layer_x_frac(row, x_field, plot_w).map(|f| f * plot_w);
        let y_of = |val: f64| layer_y_at(index, val, plot_h);
        // A line or area layer reads a NaN x as undefined at that row.
        let line_x_of = |row: &serde_json::Map<String, Value>| {
            let x = x_of(row)?;
            Some(if temporal_x.as_ref().is_some_and(|axis| axis.is_nan_at(&row_str(row, x_field))) { f64::NAN } else { x })
        };
        let layer_format = overlay.axis_y().tooltip_format_or(&r.style.tooltip_format);
        let describe_at = |x_field: &str, x_title: &str, row: &serde_json::Map<String, Value>, val: f64| -> Result<Option<String>, String> {
            if !tooltip_labels {
                return Ok(None);
            }
            // A layered chart has no structured card: with any affixed measure format the card is
            // rewritten row by row (`NativeTooltipAffixFeature`), x through its own format.
            let x_text = match &temporal_x {
                Some(axis) => axis.tooltip_date(&row_str(row, x_field))?,
                None if native_affix && !x_nominal && x_field == r.x.as_str() => match row_num(row, x_field) {
                    Some(xv) => x_tooltip_format.paint(xv)?,
                    None => row_str(row, x_field),
                },
                None => row_str(row, x_field),
            };
            let val_text = if native_affix { layer_format.paint(val)? } else { val.to_string() };
            Ok(Some(crate::contract::native_label(&[(x_title, x_text), (label.as_str(), val_text)])))
        };
        let caption = |row: &serde_json::Map<String, Value>, val: f64| describe_at(x_field, x_title, row, val);
        // A scatter base's value labels are a text layer on the base's own x field,
        // whatever x the layer's marks sit at.
        let label_x_of = |row: &serde_json::Map<String, Value>| layer_x_frac(row, &r.x, plot_w).map(|f| f * plot_w);
        let label_caption = |row: &serde_json::Map<String, Value>, val: f64| describe_at(&r.x, x_axis_title.as_str(), row, val);
        match overlay {
            OverlayLayer::Line(l) => {
                let stack = match (&l.area_mark, l.color.as_deref()) {
                    (Some(_), Some(cf)) => Some(
                        crate::families::layers::LayerStack::descending(rows, x_field, cf, &l.y)
                            .imputing(rows, crate::families::layers::layer_link(datum_link.as_ref(), (l.query_name.as_deref(), r.query_name.as_deref())))
                            .map_err(|e| format!("scatter: {e}"))?,
                    ),
                    _ => None,
                };
                let line_subset = |subset: &[serde_json::Map<String, Value>], series_color: &str| -> Result<Vec<Node>, String> {
                    crate::families::layers::line_layer_marks(
                        l,
                        subset,
                        line_x_of,
                        Some(&label_x_of),
                        y_of,
                        &crate::families::layers::AreaFrame {
                            free_y: &y_of,
                            plot: (plot_w, plot_h),
                            clip: crate::families::layers::layer_clip(overlay.layer_type(), overlay.y(), rows, ax_y, false, independent),
                        },
                        false,
                        series_color,
                        bg,
                        caption,
                        payload.board_style.as_ref(),
                        label_caption,
                        step_band(plot_w),
                        0.0,
                        stack.as_ref(),
                        crate::families::layers::layer_link(datum_link.as_ref(), (l.query_name.as_deref(), r.query_name.as_deref())),
                    )
                    .map_err(|e| format!("scatter: {e}"))
                };
                match l.color.as_deref() {
                    Some(cf) => {
                        for (value, subset) in crate::families::layers::layer_color_groups(rows, cf) {
                            plot.extend(line_subset(&subset, &claim.group_color(&layered_legend.scale, cf, value.as_deref(), &subset))?);
                        }
                    }
                    None => plot.extend(line_subset(rows, claimed_fill)?),
                }
            }
            OverlayLayer::Scatter(sl) => {
                let encoded = |row: &serde_json::Map<String, Value>| {
                    claim.row_color(&layered_legend.scale, sl.color.as_deref().expect("an encoded layer groups by color"), row)
                };
                plot.extend(
                    crate::families::layers::scatter_layer_marks(
                        sl,
                        rows,
                        sl.size(),
                        1 + index,
                        false,
                        x_of,
                        Some(&label_x_of),
                        y_of,
                        caption,
                        if sl.color.is_some() {
                            crate::families::layers::PointColor::Encoded(&encoded)
                        } else {
                            crate::families::layers::PointColor::Claimed(claimed_fill)
                        },
                        bg,
                        payload.board_style.as_ref(),
                        label_caption,
                        crate::families::layers::layer_link(datum_link.as_ref(), (sl.query_name.as_deref(), r.query_name.as_deref())),
                        crate::families::layers::layer_clip(overlay.layer_type(), overlay.y(), rows, ax_y, false, independent).then_some((plot_w, plot_h)),
                    )
                    .map_err(|e| format!("scatter: {e}"))?,
                );
            }
            OverlayLayer::Bar(bl) => {
                let marks = layer_bar_marks[index].as_ref().expect("a bar layer has its marks");
                let clip = crate::families::layers::layer_clip(overlay.layer_type(), overlay.y(), rows, ax_y, false, independent);
                let mut bars = Vec::new();
                for mark in marks {
                    let cx = x_of(mark.row).expect("marks keep rows with an x");
                    let (left, width, _) = bar_x.slot(bl, (rows, x_field), (mark.row, cx), plot_w, bl.x.is_none())?;
                    let fill = match bl.color.as_deref() {
                        Some(cf) => claim.row_color(&layered_legend.scale, cf, mark.row),
                        None => claimed_fill.clone(),
                    };
                    let aria = caption(mark.row, mark.value)?;
                    bars.extend(crate::families::bar::layer_bar(
                        &y_of,
                        plot_h,
                        clip,
                        bl,
                        mark,
                        (left, width),
                        &fill,
                        aria.as_deref(),
                        crate::families::layers::layer_link(datum_link.as_ref(), (bl.query_name.as_deref(), r.query_name.as_deref())),
                    )?);
                }
                let (sited, label_centers) = bar_label_sites(bl, rows, marks, plot_w)?;
                let label_marks: Vec<_> = sited.iter().map(|&i| marks[i]).collect();
                bars.extend(crate::families::bar::layer_bar_labels(
                    bl,
                    rows,
                    &label_marks,
                    &label_centers,
                    &y_of,
                    plot_h,
                    |mark| label_caption(mark.row, mark.value).expect("a plotted date parsed at resolve"),
                    crate::families::layers::layer_link(datum_link.as_ref(), (bl.query_name.as_deref(), r.query_name.as_deref())),
                    (payload.board_style.as_ref(), bg),
                )?);
                if clip {
                    let id = crate::families::layers::layer_clip_id((plot_w, plot_h), &r.id);
                    let defined = !defined_clips.insert(id.clone());
                    plot.extend(crate::families::layers::layer_clip_group(&id, (plot_w, plot_h), defined, bars));
                } else {
                    plot.extend(bars);
                }
            }
        }
        // A layer with its own scale carries its own zero rule over its own marks.
        if let (Some(la), Some(Some(width)), Some(zl)) =
            (layer_axes.get(index), layer_zero_rules.get(index), ax_y.grid.threshold.as_ref())
        {
            let zc = zl.color.as_deref().unwrap_or("#8A8C8F");
            let gy = la.scale(plot_h).position(0.0);
            plot.push(crate::contract::item_group(
                "rule",
                &format!("layer_{}_marks", 1 + overlays.len() + index),
                crate::contract::ItemRole::RuleMark,
                crate::contract::Reads::Hidden,
                |aria| {
                    Node::Line(
                        Attrs::new()
                            .set("x1", "0.000")
                            .set("y1", format!("{gy:.3}"))
                            .set("x2", format!("{plot_w:.3}"))
                            .set("y2", format!("{gy:.3}"))
                            .set("stroke", zc)
                            .set("stroke-width", width.to_string())
                            .extend(aria),
                    )
                },
            )?);
        }
    }
    if let Some(ctx) = &annotations {
        plot.extend(crate::annotation::nodes(ctx, crate::annotation::Plane::Foreground)?);
    }
    if ghosts_on {
        let rules: Vec<crate::contract::GhostRule> = ghost_points
            .iter()
            .map(|(aria, x, y, stroke, href)| crate::contract::GhostRule {
                aria: Some(aria),
                x: *x,
                y: *y,
                stroke,
                anchor: r.link_open.anchor(href.as_deref()),
            })
            .collect();
        plot.push(crate::contract::ghost_layer(&rules, crate::contract::GhostRun::Down(plot_h))?);
        if !ghost_symbols.is_empty() {
            let mut symbols = Vec::new();
            for (glyph, cx, cy, fill, label, href) in &ghost_symbols {
                let anchor = r.link_open.anchor(href.as_deref());
                let symbol = Node::Path(
                    Attrs::new()
                        .set("transform", format!("translate({cx:.4},{cy:.4})"))
                        .set("d", glyph.as_str())
                        .set("fill", fill.as_str())
                        .set("stroke-width", "2")
                        .set("opacity", "1")
                        .extend(crate::contract::aria_attrs(Some(label), crate::contract::ItemRole::Point)?),
                );
                symbols.push(crate::href::linked(anchor, symbol));
            }
            plot.push(crate::contract::mark_group("symbol", "ghost_symbol_marks", symbols)?);
        }
    }

    // X ticks + labels.
    if let Some(axis) = temporal_x.as_ref().filter(|_| panel.is_none()) {
        plot.extend(axis.nodes(ax_x, 0.0, plot_w, x_edge.edge_y(plot_h), fonts)?);
    }
    if temporal_x.is_none() && plain_x.is_none() {
        plot.extend(plain_x_axis(
            &PlainXPaint {
                ax_x,
                ticks: &xf.ticks,
                labels: &xf.labels,
                plot_left: 0.0,
                plot_w,
                plot_bottom: x_edge.edge_y(plot_h),
                angle: x_cat_angle,
                align: x_cat_align,
                baseline: x_cat_baseline,
                flush: x_label_flush,
                point_label_crisp: 0.5,
                title: &x_title_text,
                caption: &AxisCaption::new(&x_title_text, &ax_x.title, xf.scale.clone().expect("a plain x axis names its scale")).bare_if_undrawn(ax_x),
                title_key: panel.is_none().then_some(crate::contract::LabelKey::X),
            },
            fonts,
        )?);
    }

    // The value axis: drawn by the panel that owns it (a facet's last
    // column), anchored `value_axis_dx` past the plot edge.
    let paint_value_axis = panel.is_none_or(|o| o.paint_value_axis);
    let value_axis_dx = panel.filter(|o| !o.embedded_y).map_or(0.0, |o| o.value_axis_right_overhang);
    // The axis group's origin and the direction its ticks, labels and title run.
    let (y_axis_x, y_outward) = if y_left { (0.5, -1.0) } else { (plot_w + value_axis_dx + 0.5, 1.0) };
    let value_axis_from = plot.len();
    let y_caption = AxisCaption::new(&y_title_text, val_title_style, y_scale_caption).bare_if_undrawn(ax_y);
    let y_label_color = ax_y.labels.font.color.as_deref().unwrap_or("#626366");
    let y_label_weight = ax_y.labels.font.weight_str();
    let log_visible = Some(y_thinned(plot_w + value_axis_dx, plot_h));
    if paint_value_axis {
        let mut items = Vec::new();
        // Y tick marks: 6px lines between the plot edge and the label gutter.
        if y_tick_eff > 0.0 {
            let tc = crate::common::required_ink(ax_y.ticks.color.as_deref(), "scatter", "axis_y.ticks.color")?;
            let tw = ax_y.ticks.width.unwrap_or(1.0);
            for &frac in &y_ticks {
                let gy = y_pos(frac).round() + 0.5;
                items.push(Node::Line(
                    Attrs::new()
                        .set("x1", format!("{y_axis_x:.3}"))
                        .set("y1", format!("{gy:.3}"))
                        .set("x2", format!("{:.3}", y_axis_x + y_outward * y_tick_eff))
                        .set("y2", format!("{gy:.3}"))
                        .set("stroke", tc)
                        .set("stroke-width", tw.to_string()),
                ));
            }
        }
        // Y labels: right-aligned at the gutter's outer edge, middle baseline.
        let y_label_x = y_axis_x + y_outward * y_label_off;
        for (i, (&frac, lbl)) in y_ticks.iter().zip(y_labels.iter()).enumerate() {
            if log_visible.as_ref().is_some_and(|v| !v[i]) {
                continue;
            }
            let gy = y_pos(frac) + 0.5 + measure::baseline_shift("middle", y_font_size);
            items.push(Node::text(
                Attrs::new()
                    .set("x", format!("{y_label_x:.3}"))
                    .set("y", format!("{gy:.3}"))
                    .set("text-anchor", y_label_anchor)
                    .vega("font-family", y_family)
                    .set("font-size", y_font_size.to_string())
                    .set("font-weight", y_label_weight.as_str())
                    .set("fill", y_label_color),
                ax_y.labels.clip(lbl, fonts)?,
            ));
        }
        items.extend(crate::common::axis_title_rotated(
            &y_title_text,
            val_title_style,
            y_axis_x + y_outward * y_edge_at(plot_w, plot_h),
            plot_h / 2.0,
            y_outward,
            panel.is_none().then_some(crate::contract::LabelKey::Y),
        ));
        plot.extend(crate::contract::axis_group(crate::contract::AxisChannel::Y, &y_caption, (y_axis_x, 0.0), items)?);
    }
    let value_axis = value_axis_from..plot.len();
    // Each layer's own axis, on the side it sits; only the first y title
    // of the chart edits its label, so the first layer's stands in for a base title
    // that draws nothing.
    for (axis_index, la) in layer_axes.iter().enumerate() {
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
            },
            (axis_index == 0 && panel.is_none() && crate::common::axis_title_band(val_title_style, &y_title_text) == 0.0)
                .then_some(crate::contract::LabelKey::Y),
        )?);
    }
    if mirror_active {
        // The ghost is `dict(primary_axis)` but for `orient`/`grid`/`title`, so
        // its ticks and labels repeat the primary's on the opposite edge, in
        // that edge's own 0.5 crisp frame.
        let (ghost_x, ghost_out) = if y_left { (plot_w + 0.5, 1.0) } else { (0.5, -1.0) };
        let mut items = Vec::new();
        if y_tick_eff > 0.0 {
            let tc = crate::common::required_ink(ax_y.ticks.color.as_deref(), "scatter", "axis_y.ticks.color")?;
            let tw = ax_y.ticks.width.unwrap_or(1.0);
            for &frac in &y_ticks {
                let gy = y_pos(frac).round() + 0.5;
                items.push(Node::Line(
                    Attrs::new()
                        .set("x1", format!("{ghost_x:.3}"))
                        .set("y1", format!("{gy:.3}"))
                        .set("x2", format!("{:.3}", ghost_x + ghost_out * y_tick_eff))
                        .set("y2", format!("{gy:.3}"))
                        .set("stroke", tc)
                        .set("stroke-width", tw.to_string()),
                ));
            }
        }
        for (i, (&frac, lbl)) in y_ticks.iter().zip(ghost_texts.iter()).enumerate() {
            if log_visible.as_ref().is_some_and(|v| !v[i]) {
                continue;
            }
            let gy = y_pos(frac) + 0.5 + measure::baseline_shift("middle", y_font_size);
            items.push(Node::text(
                Attrs::new()
                    .set("x", format!("{:.3}", ghost_x + ghost_out * ghost_off))
                    .set("y", format!("{gy:.3}"))
                    .set("text-anchor", ghost_anchor)
                    .vega("font-family", y_family)
                    .set("font-size", y_font_size.to_string())
                    .set("font-weight", y_label_weight.as_str())
                    .set("fill", y_label_color),
                ax_y.labels.clip(lbl, fonts)?,
            ));
        }
        plot.extend(crate::contract::axis_group(
            crate::contract::AxisChannel::Y,
            &y_caption.untitled(),
            (ghost_x, 0.0),
            items,
        )?);
    }

    // Legend. The group's x is `ceil(x2) + 18`, `x2` the y-axis chrome's
    // right edge — `layoutAxis` translates an axis's bounds by its POSITION
    // only, without the axis group's own +0.5 crisp translate (the label's
    // drawn x carries that shift; this quantity does not), so `x2` is the
    // plot rect's own width plus the label gutter and title band, unshifted.
    // Vega ceils this integer AFTER the fit resolves, a separate rounding
    // rule from the exact-float ink union that negotiated the fit itself.
    let x2 = match (y_left, mirror_active) {
        (true, false) => plot_w,
        (true, true) => plot_w + ghost_edge_at(plot_w, plot_h),
        (false, _) => plot_w + y_edge_at(plot_w, plot_h) + y_title_extra,
    };
    let x2 = layer_axes.iter().fold(x2, |edge, la| la.widen_axes((0.0, edge), plot_w, 0.0).1);
    let annotation_x2 = annotations.as_ref().and_then(crate::annotation::bounds).map_or(0.0, |b| b.x2);
    let x2 = x2.max(annotation_x2);
    let lx = x2.ceil() + VEGA_LEGEND_OFFSET;
    // The stack's origin: at the view ink's right edge on the right, else
    // where `legendParams` anchored it. A centered or end-aligned legend
    // slides every legend of the view together (`legend_shifts`).
    let base = if right_strip { (lx, 0.0) } else { legend_xy.get() };
    let color_block = if legend_shown { Some((legend_w, legend_h)) } else { gradient_block(plot_w, plot_h)? };
    let LegendStack { color_off, size_off, shape_y, .. } = legend_stack(color_block);
    let shape_x = lx;
    let mut rects = Vec::new();
    if let Some((w, h)) = color_block {
        rects.push((base.0 + color_off.0, base.1 + color_off.1, w, h));
    }
    if size_legend_shown {
        rects.push((base.0 + size_off.0, base.1 + size_off.1, size_legend_w, size_legend_h));
    }
    if let Some(sl) = &shape_legend {
        rects.push((shape_x, shape_y, sl.w, sl.h));
    }
    // Production slides only a shown legend (`vega_lite.py`'s `needs_slide`).
    let slide = if r.legend.visible {
        legend::slide_union(&r.id, &r.legend.position, &rects, (plot_w, plot_h)).map_err(|d| d.to_string())?
    } else {
        (0.0, 0.0)
    };
    if let Some((_, lg)) = color_legend.as_ref().filter(|_| layered) {
        let (x, y) = (base.0 + color_off.0 + slide.0, base.1 + color_off.1 + slide.1);
        plot.extend(legend::drawn_symbol_legend(&layered_entries, lg, &r.legend, base_glyph, x, y, false));
        plot.extend(numeric_legends.nodes_after(lg, &r.legend, fonts, (plot_w, plot_h), (x, y))?);
    }
    if let Some((labels, lg)) = color_legend.as_ref().filter(|_| !layered) {
        let fills = color_domain
            .iter()
            .map(|label| crate::common::series_color(label, &color_domain, &r.series_palette, category_scale))
            .collect::<Result<Vec<_>, _>>()?;
        let glyphs = legend_glyph_names
            .iter()
            .map(|name| crate::families::scatter_shape::path(name, 5.0))
            .collect::<Result<Vec<_>, _>>()?;
        let interior = r.style.point_mark.fill.as_deref().filter(|_| unfilled);
        let entries: Vec<legend::LegendEntry> = legend_order
            .iter()
            .zip(labels)
            .map(|(&idx, label)| legend::LegendEntry {
                label,
                fill: &fills[idx],
                symbol: Some(match interior {
                    Some(interior) => legend::LegendSymbol::Ring(
                        &glyphs[idx],
                        interior,
                        mark_opacity,
                        crate::families::scatter_shape::half_extents(legend_glyph_names[idx], 5.0),
                    ),
                    None if mirror_active => legend::LegendSymbol::Circle,
                    None => legend::LegendSymbol::FilledGlyph(&glyphs[idx]),
                }),
                series_key: Some(&color_domain[idx]),
            })
            .collect();
        let (x, y) = (base.0 + color_off.0 + slide.0, base.1 + color_off.1 + slide.1);
        plot.extend(legend::drawn_symbol_legend(
            &entries,
            lg,
            &r.legend,
            if mirror_active { legend::LegendSymbol::Circle } else { legend::LegendSymbol::FilledCircle },
            x,
            y,
            stamp_legend_series,
        ));
    }

    if let (Some(scale), Some(title)) = (&color_scale, &gradient_title) {
        let at = (base.0 + color_off.0 + slide.0, base.1 + color_off.1 + slide.1);
        plot.extend(GradientAt::nodes(&r.legend, r.color_format.as_ref(), scale, (&gradient_id, title), fonts, (plot_w, plot_h), at)?);
    }

    if let Some(sl) = &shape_legend {
        let fill = shape_legend_fill(r, &fires);
        plot.extend(sl.nodes(shape_x + slide.0, shape_y + slide.1, fill, mark_opacity));
    }

    if let Some(sl) = &size_legend {
        plot.extend(size_legend_nodes(sl, r, base.0 + size_off.0 + slide.0, base.1 + size_off.1 + slide.1));
    }

    // The panel's ink in its own canvas frame: the plot rect, every mark's
    // reach past it, and — on a single-column facet — the value-axis gutter.
    // A log axis's labels that Vega's greedy overlap keeps at this plot size.
    let y_drawn = y_thinned(plot_w, plot_h);
    let panel_axis: Option<PanelXAxis> = panel
        .map(|o| -> Result<PanelXAxis, String> {
            Ok(match &o.x_axis {
                FacetX::Date(axis) => PanelXAxis::Temporal((*axis).clone()),
                FacetX::Plain(axis) => PanelXAxis::Plain(axis.sized_for(ax_x, plot_w, fonts)?),
            })
        })
        .transpose()?;
    let panel_layer_ink = panel.map(|_| layer_ink(plot_w, plot_h)).transpose()?;
    let group = Attrs::new().set("transform", format!("translate({group_x:.3},{group_y:.3})"));
    let cuts = panel.map(|_| frame.cuts(&head, &group, &plot, &value_axis, &[]));
    let geometry = panel.zip(cuts).map(|(o, cuts)| {
        let (plot_left, plot_top) = (pad_l + left_margin, pad_t + top_margin);
        let (plot_right, plot_bottom) = (plot_left + plot_w, plot_top + plot_h);
        let mut ink = vega_fit::Bounds::new(plot_left, plot_top, plot_right, plot_bottom);
        // The layers' marks past the plot are cell ink too.
        if let Some(mut marks) = panel_layer_ink {
            marks.translate(plot_left, plot_top);
            ink.union(&marks);
        }
        // So are the annotations' text and strokes, which the composite sizes
        // its row spacing from.
        if let Some(mut marks) = annotations.as_ref().and_then(crate::annotation::bounds) {
            marks.translate(plot_left, plot_top);
            ink.union(&marks);
        }
        for &(x_frac, y_frac, _, mark_r, _) in &points_data {
            let (px, py) = (plot_left + x_pos(x_frac), plot_top + y_pos(y_frac));
            ink.union(&vega_fit::Bounds::new(px, py, px, py).expand(mark_r + symbol_stroke_ink));
        }
        for (&(x_frac, y_frac, _, _, _), (_, w)) in points_data.iter().zip(&value_label_texts) {
            ink.union(&vega_fit::text_ink(
                plot_left + x_pos(x_frac) + value_label_dx,
                plot_top + y_pos(y_frac) + value_label_dy,
                *w,
                value_label_size,
                value_label_align,
                value_label_baseline,
            ));
        }
        // The cell's x gridlines run to `axis.offset` past the plot, on the side its axis hangs from
        // (`bboxFull` counts a cell's axis-grid child), whatever their stroke.
        if ax_x.grid.visible.unwrap_or(false) {
            for &frac in &xf.ticks {
                let gx = plot_left + x_pos(frac);
                let (y1, y2) = match ax_x.x_edge() {
                    crate::common::XEdge::Bottom => (plot_top, plot_bottom + x_axis_offset),
                    crate::common::XEdge::Top => (plot_top - x_axis_offset, plot_bottom),
                };
                ink.union(&vega_fit::Bounds::new(gx, y1, gx, y2));
            }
        }
        if o.fold_axis_gutter {
            ink.union(&vega_fit::Bounds::new(
                pad_l,
                plot_top,
                plot_right + if o.embedded_y { 0.0 } else { o.value_axis_right_overhang } + m.right,
                plot_bottom,
            ));
        }
        if let Some(zx) = ax_x.grid.threshold.as_ref() {
            for frac in xf.rules.into_iter().flatten() {
                let rx = plot_left + x_pos(frac);
                ink.union(&vega_fit::Bounds::new(rx, plot_top, rx, plot_bottom).expand(zx.width.unwrap_or(2.0)));
            }
        }
        // An independent scale embeds the value axis in the cell: a tick label
        // reaching past the plot is cell ink.
        if o.embedded_y {
            for (&frac, _) in y_ticks.iter().zip(&y_drawn).filter(|(_, &drawn)| drawn) {
                facet_compose::union_embedded_label_ink(
                    &mut ink,
                    plot_top + y_pos(frac),
                    y_font_size,
                    (plot_left, plot_top, plot_right, plot_bottom),
                );
            }
        }
        let label_shift = measure::baseline_shift("middle", y_font_size) + 0.5;
        PanelGeometry {
            content_start: cuts.content_start,
            svg_end: cuts.svg_end,
            ink,
            axis_w_overhang: left_margin + m.right,
            left_mark_overhang: left_margin,
            plot_top,
            // `raw_left` is the left reach, which stands for the right one when
            // every mark is the same size; a size channel puts a differently
            // sized mark at each edge, so the marks' own reaches differ.
            raw_right_reach: {
                let reach = |edge_of: fn(f64, f64, f64) -> f64| {
                    points_data.iter().map(|p| edge_of(p.0 * plot_w, p.3 + symbol_stroke_ink, plot_w)).fold(0.0_f64, f64::max)
                };
                let (left_marks, right_marks) = (reach(|x, r, _| r - x), reach(|x, r, w| x + r - w));
                (m.raw_left - left_marks + right_marks).max(0.0)
            },
            widest_val_tick_label_width: y_label_ws
                .iter()
                .zip(&y_drawn)
                .filter(|(_, &drawn)| drawn)
                .fold(0.0_f64, |acc, (&w, _)| acc.max(w.min(y_limit))),
            val_gutter: y_label_off - y_tick_eff,
            val_tick_size: y_tick_eff,
            val_tick_marks: if y_tick_eff > 0.0 {
                y_ticks.iter().map(|&frac| crate::common::tick_row(plot_top, y_pos(frac))).collect()
            } else {
                Vec::new()
            },
            val_ticks: y_ticks
                .iter()
                .zip(&y_labels)
                .zip(&y_drawn)
                .filter(|(_, &drawn)| drawn)
                .map(|((&frac, label), _)| (plot_top + y_pos(frac) + label_shift, label.clone()))
                .collect(),
            ghost_labels: if ghost.is_some() {
                ghost_texts.iter().zip(&y_drawn).filter(|(_, &drawn)| drawn).map(|(label, _)| label.clone()).collect()
            } else {
                Vec::new()
            },
            mirror_style: Some(GhostStyle {
                caption: y_caption.clone(),
                val_font_size: y_font_size,
                val_weight: y_label_weight.clone(),
                val_color: y_label_color.to_string(),
                val_family: y_family.to_string(),
            }),
            axis: panel_axis.expect("a panel render carries its x axis"),
            top_axis_overhang: (-top_overhang.get()).max(0.0),
            value_title_w: if o.paint_value_axis && y_title_extra > 0.0 { y_title_w } else { 0.0 },
            legend: None,
            value_axis: paint_value_axis
                .then(|| crate::families::facet_compose::ValueAxisInk { range: cuts.value_axis, origin: (group_x, group_y) }),
        }
    });
    let mut body = head;
    body.push(Node::group(group, plot));
    Ok(Fitted {
        svg: frame.svg(body),
        data_box: Rect { x: group_x, y: group_y, width: plot_w, height: plot_h },
        canvas: Rect { x: 0.0, y: 0.0, width: canvas_w, height: canvas_h },
        title_pos: m.title_pos,
        geometry,
    })
}

/// A fitted chart: the card as a node, the box its plot occupies in it, and, for a facet panel,
/// the ink the composite sizes its cells from.
pub(super) struct Fitted {
    pub(super) svg: Node,
    pub(super) data_box: Rect,
    pub(super) canvas: Rect,
    pub(super) title_pos: Option<(f64, f64)>,
    pub(super) geometry: Option<PanelGeometry>,
}

/// The fit arm's y domain when the author pinned `scale.continuous.domain`
/// (a descending pair keeps its order and inverts the axis). Production hands
/// the pair to Vega verbatim (`emitters/scatter.py:354-369`, through
/// `vl_field_maps.py:114-119`'s `emit_resolved_scale_vl`); resolve bakes no
/// `domain_min`/`domain_max` beside it and the baked ladder spans the pair,
/// so the axis draws the rungs that land inside it (the caller's range
/// filter, `validTicks`, `vega.js:14547-14572`).
///
/// An authored `scale.nice: true` re-rounds the pair outward at d3's count 10
/// (`authored_domain_pair`), exactly as the legacy arm does. `scale.padding`
/// widens the pair by that many pixels of the `plot_h`-tall range first
/// (Vega's `padLinear`), and the nice rounding runs on the padded pair. A
/// datum outside the (niced) pair is drawn unclipped past the plot edge while
/// `autosize: fit` grows the view around it.
pub(super) fn authored_y_domain(ax_y: &AxisStyle, plot_h: f64) -> Option<(f64, f64)> {
    let (lo, hi) = ax_y.coerced_continuous_domain()?;
    let pad = ax_y.scale.get("padding").and_then(Value::as_f64).unwrap_or(0.0);
    let (lo, hi) = vega_fit::pad_domain((lo, hi), plot_h, pad, ax_y.bend());
    Some(crate::families::cartesian::authored_domain_pair(ax_y, lo, hi))
}

/// A y fraction's pixel position on a plot `extent` tall; `scale.round` rounds
/// what the scale yields.
pub(super) fn y_px(extent: f64, frac: f64, round: bool) -> f64 {
    let px = extent * frac;
    if round { px.round() } else { px }
}

/// The `(lo, hi)` of a measure axis with no authored domain. It is Vega's
/// linear scale as the scatter emitter compiles it (`emitters/scatter.py:282`
/// for x, `:354-358` for y), in `configureDomain` order: the column's extent
/// joined with 0 (Vega-Lite's default `zero: true`; `scale.continuous.zero:
/// false` skips the join), a baked `domain_min`/`domain_max` overwriting its
/// edge verbatim (`apply_domain_headroom_bounds`, `emitters/_cartesian.py:
/// 1265-1287`), `scale.padding` pixels widening the pair, then d3 nice at
/// count 10 (the default `nice: true`) only when neither edge is baked,
/// since Vega-Lite drops `nice` once `domainMin`/`domainMax` is set. A scale
/// shared with layers (`layered`) brings Vega-Lite's default `nice` back, which
/// rounds a baked bound too.
/// Headroom reaches an edge only as a baked bound (`_domain.py:456-661`).
/// `data_lo`/`data_hi` are the extent of the rows Vega keeps and `extent` the
/// plot length the padding is measured on.
pub(super) fn unauthored_measure_domain(ax: &AxisStyle, data_lo: f64, data_hi: f64, extent: f64, layered: bool) -> (f64, f64) {
    let pad = ax.scale.get("padding").and_then(Value::as_f64).unwrap_or(0.0);
    let (lo, hi) = if ax.is_zero_false() { (data_lo, data_hi) } else { (data_lo.min(0.0), data_hi.max(0.0)) };
    let (lo, hi) = (ax.domain_min.unwrap_or(lo), ax.domain_max.unwrap_or(hi));
    let (lo, hi) = vega_fit::pad_domain((lo, hi), extent, pad, ax.bend());
    let rounds = ax.scale.get("nice").and_then(Value::as_bool).unwrap_or(layered || !ax.has_baked_edge());
    if rounds && hi > lo { d3rs::ticks::d3_nice(lo, hi, 10) } else { (lo, hi) }
}

