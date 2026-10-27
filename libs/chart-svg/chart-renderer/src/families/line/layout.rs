//! Line and area layout: the card, plot, axes, marks, legends and annotations laid out in the cell
//! and returned as nodes, with the WARNs the layout raises. Every pixel decision is here.


use super::bind::Bound;
use crate::time_axis::TimeColumns;
use super::{FacetPanelOpts, HALO_POINT_STROKE_W, LinePanelGeometry, LineXAxis, LineXAxisBody, PlotRect, Shape, StackColumn, X_AXIS_OFFSET, bare_label_ink, bare_label_layout, bare_label_widths, description_color_title, domain_position, flush_anchor_px, last_y_ascending_order, mirror_ghost_x, nominal_label_anchor, point_mark_reach, quantitative_x_ticks, rail_cascade_bounds, series_dash, series_fill, shape, stack_column, stack_data, stacked_top_datum, tick_snap, tilted_temporal_label, with_dash, x_ranked_rows, y_family, y_font_size};
use crate::diagnostic::Diagnostic;
use std::collections::{HashMap, HashSet};

use crate::families::axis_caption::{value_axis_caption, x_axis_caption};
use crate::families::cartesian::recascade_rail_anchors;
use crate::families::facet_compose;
use crate::families::multiples;

use serde_json::Value;

use crate::common::{
    axis_title_band, axis_title_text, dimension_sort_domain, distinct_values,
    format_mark_label, format_y_label, is_year_shaped, row_num, row_str,
    symbol_radius, unity_rule_fires, CaptionSpot, MarkLabels, SortAggregate, FormatSlot, VALUE_AXIS_BREATHING_ROOM,
};
use crate::families::cartesian::{
    vl_default_tick_count, BandGeometry, LinearScale, LogScale, MeasureScale, PointScale,
};
use crate::families::layers::{
    bar_layer_marks, placed_bar_layer_marks, layer_color_text, layer_color_groups, layer_axis, layer_stroked, line_layer_marks, merged_legend_title, scope_group_top, LayerStack, NULL_COLOR_TEXT,
    panel_layer_rows, scatter_layer_marks, resolve_layer_rows, AreaFrame, PointColor,
    DomainCut, DualAxisTitles, LayerAxis, LayerAxisFrame, LayerAxisInk, LayerSide, OverlayBarLayer, OverlayLayer, StepBand, YSides, MOVED_BASE_LABEL_PAD,
};
use crate::families::heatmap::{flat_label, tick_line, LabelFace};
use crate::families::support_table_strip;
use crate::families::legend::{
    drawn_symbol_legend, legend_slide, legend_title_text,
    legend_fit_bounds, legend_from_widths, legend_origin, legend_overlay_origin, limit_legend_text, narrow_legend_entries, LegendConfig,
    vega_default_legend, LegendEntry, LegendSymbol, VegaLegend, VEGA_LEGEND_OFFSET,
};
use crate::families::temporal::{
    authored_tick_count, authored_tick_interval, bare_temporal_ticks, complete_buckets,
    interval_tick_days,
    frac_of, interval_tick_labels, native_tick_interval, none_tick_labels,
    resolve_vega_x_labels, temporal_label_half_width, time_unit_floor,
    x_values_are_temporal_candidates, VegaType,
    AuthoredTickCadence, AuthoredTimeFormat, BareTick, ResolvedVegaLabels, TemporalLabel,
    TimePart, TimeUnit, UtcTickInterval,
};
use crate::frame::{ChartFrame, FrameTitle};
use crate::svg::{py_float_str};
use scene::{Attrs, Node};
use crate::vega_fit;
use measure::{FontBook, Measure};
use crate::{Padding, Payload};
use crate::layout::{IntrinsicWidth, Layout, Rect};

/// A line or area chart fills the width it is given: nothing to hug.
pub fn intrinsic(_bound: &Bound, _measure: &Measure) -> Result<Option<IntrinsicWidth>, Diagnostic> {
    Ok(None)
}

/// A line or area chart draws in the slot sizing hands it: that slot's height is its natural height
/// at any width, and [`layout`] draws the cell it is given.
pub fn height_for_width(bound: &Bound, _width: f64, _measure: &Measure) -> Result<f64, Diagnostic> {
    Ok(bound.payload.height)
}

/// One plot in `cell`. A `multiples` chart is composed panel by panel at `render`'s string boundary.
pub fn layout(bound: &Bound, cell: Rect, measure: &Measure) -> Result<Layout, Diagnostic> {
    layout_with_plot(bound, cell, measure).map(|(laid_out, _)| laid_out)
}

/// [`layout`] with the plot rect its negotiation settled on, which `--dump-layout` reports.
pub(super) fn layout_with_plot(bound: &Bound, cell: Rect, measure: &Measure) -> Result<(Layout, PlotRect), Diagnostic> {
    if bound.r.multiples.is_some() {
        return Err("line: layout draws one plot; a multiples chart is composed by render".into());
    }
    let payload: std::borrow::Cow<Payload> = if (cell.width, cell.height) == (bound.payload.width, bound.payload.height) {
        std::borrow::Cow::Borrowed(&*bound.payload)
    } else {
        std::borrow::Cow::Owned(Payload { width: cell.width, height: cell.height, ..Payload::clone(&bound.payload) })
    };
    let (fitted, diagnostics) = crate::diagnostic::collect(|| {
        fit(bound, &payload, &payload.rows, (payload.width, payload.height), payload.padding.as_ref(), measure.fonts, None)
    });
    // A layout that fails has no `Layout` to carry what it found: those WARNs reach the sink, as they
    // did when the render wrote to it directly.
    let fitted = fitted.inspect_err(|_| diagnostics.iter().cloned().for_each(crate::diagnostic::warn))?;
    let laid_out = Layout {
        insets: fitted.canvas.insets_of(&fitted.data_box),
        data_box: fitted.data_box,
        nodes: vec![fitted.svg],
        diagnostics,
        title_pos: fitted.plot.title_pos,
    };
    Ok((laid_out, fitted.plot))
}

/// A fitted card: the node, the box its plot occupies, the plot rect and, for a facet panel, the
/// ink the composite sizes its cells from.
pub(super) struct Fitted {
    pub(super) svg: Node,
    pub(super) data_box: Rect,
    pub(super) canvas: Rect,
    pub(super) plot: PlotRect,
    pub(super) geometry: LinePanelGeometry,
    /// Where a facet composite cuts the printed card; only a facet panel is cut.
    pub(super) cuts: Option<crate::frame::PanelCuts>,
}

/// One panel's worth of axes + marks + grid + title/legend chrome for a
/// line/area chart — `rows`/`svg_w`/`svg_h`/`pad` are the panel's OWN
/// data and box (the single-chart wrapper above passes the whole `Payload`'s;
/// a facet composite (`render_facet`) passes one panel's row
/// group and one narrower panel box per call, same shape as bar's
/// `render_vertical_panel`). `payload` stays the WHOLE envelope alongside
/// `rows` — nothing below reads anything else off it (support_table/
/// wide-measure folding and the layer validation already ran in `bind`,
/// before this call).
/// `wide_series`/`wide_labels`/`layer` are chart-global: the wide fold and
/// the layer validation ran on the WHOLE chart, never per-panel.
/// `facet: None` (the single-chart wrapper's own call) makes every gate
/// below inert; `render_facet`'s own call passes `Some(FacetPanelOpts)`.
pub(super) fn fit(
    bound: &Bound,
    payload: &Payload,
    rows: &[serde_json::Map<String, Value>],
    (svg_w, svg_h): (f64, f64),
    pad: Option<&Padding>,
    fonts: &FontBook,
    facet: Option<&FacetPanelOpts>,
) -> Result<Fitted, Diagnostic> {
    let Bound { r, overlays, draw_areas, wide_series, wide_labels, x, .. } = bound;
    let draw_areas = *draw_areas;
    // A numeric color paints the panel's one line in the scale's color for the panel's first datum.
    let single_fill: String = match &r.numeric_color {
        Some((color, field)) => rows
            .iter()
            .find_map(|row| row_num(row, field))
            .map_or_else(|| r.style.single_series_fill.clone(), |first| color.fill(first)),
        None => r.style.single_series_fill.clone(),
    };
    let has_layers = !overlays.is_empty();
    let y_sides = YSides::of(overlays);
    let display_label = |s: &str| crate::common::wide_series_display(s, &wide_labels);
    // `multiples.scale: "shared"` (the default): the x point scale and the
    // y extent/ladder are computed off the WHOLE chart's rows; every other
    // site below (the data index, `series_points`, mark-ink `frac_hi`/
    // `frac_lo`) reads this panel's own `rows` instead.
    let domain_rows: &[serde_json::Map<String, Value>] = facet.map_or(rows, |f| f.shared_domain_rows);
    let suppress_x_axis = facet.is_some_and(|f| f.suppress_x_axis);
    let paint_value_axis = facet.map_or(true, |f| f.paint_value_axis);
    let fold_axis_gutter = facet.is_some_and(|f| f.fold_axis_gutter);
    let independent_scale = facet.is_some_and(|f| f.independent_scale);
    // An independent scale embeds the axis in the cell, anchored at the plot edge.
    let value_axis_right_overhang = facet.filter(|f| !f.independent_scale).map_or(0.0, |f| f.value_axis_right_overhang);
    // Mirror y-axis (`style.axis_y.mirror`): production's `MirrorAxisFeature`
    // (`mirror_axis.py`) builds the ghost as `dict(primary_axis)` and
    // overrides only THREE keys — `orient` (flipped), `grid: False`,
    // `title: None` (`mirror_axis.py:162-165`) — so `ticks`/`tickSize`
    // (and the VL `domain` line, unmodeled for either axis on this port,
    // a pre-existing gap unrelated to mirror) carry over unchanged. Same
    // labels (unless the ghost carries its own `format`), y positions,
    // y_tick_eff, reserved offset (the measured
    // gutter on a right primary, the themed padding on a left one), anchor
    // AND ticks as the primary — only the grid and title are suppressed —
    // sign-flipped onto the opposite edge. The `true` and `format` forms are
    // ported below (the fit arm only); an `expr`/`tick_label` relabel is an
    // unmodeled shape. A wide y or a layer under mirror raises its ERR in
    // `cartesian_errors` before any family renders, and a facet threads its
    // own `has_mirror` per panel (`mirror_band_x`).
    let mirror = r.style.axis_y.mirror_ghost("line")?;
    let mirror_active = mirror.is_some();
    // `mirror_active` only ever drives the single-chart ghost paint below
    // (`facet.is_none()`) — a facet computes its OWN `has_mirror` fact in
    // `render_facet` and threads it per-panel via `FacetPanelOpts.
    // mirror_band_x` instead, since the composer paints the shared ghost
    // once, not each panel; an area facet takes the band-position law through
    // `mirror_band_x` with the composer painting the ghost.
    let mirror_active = mirror_active && facet.is_none();
    let support_table_config = match &r.support_table {
        None => None,
        Some(v) => {
            let fam = if draw_areas { "area" } else { "line" };
            Some(support_table_strip::parse_config(v, fam)?.folded_from(&r.prefold_rows, r.wide_measures.len()))
        }
    };
    // The authored curve, read off the mark slice the family actually draws
    // with — line_mark for lines, area_mark for areas (production reads
    // style.area_mark.curve for areas, emitters/area.py:220). Anything with
    // no ported path refuses loudly: drawing a staircase or a spline as
    // straight segments through the right vertices is a plausible WRONG
    // shape, which is worse than no render at all. `curve` here is the
    // PLAIN d3 passthrough family — a literal `step` is overridden to
    // `StepAfter` further down once the RESOLVED x type is known (only a
    // plain category bands; quantitative and temporal-resolving never do) —
    // see `is_band_step` below.
    let curve_name = if draw_areas {
        r.style.area_mark.as_ref().and_then(|a| a.curve.as_deref())
    } else {
        r.style.line_mark.curve.as_deref()
    };
    let mut curve = crate::families::layers::curve_named(curve_name, if draw_areas { "area" } else { "line" })?;
    // `stack: zero`/`center`/`normalize` on AREA only — Vega's stack
    // transform (offset `zero`/`center`/`normalize`), ported below. A line's
    // payload always carries `none`/null.
    let is_stacked = r.stack.as_deref().is_some_and(|s| s != "none");
    let is_normalize = r.stack.as_deref() == Some("normalize");

    // The chart's OWN effective background (`r.background`), never
    // `payload.background` (the board-level default only) — an authored
    // `style.background` on this chart must win for the canvas rect, same as
    // production's VL spec background. The area backdrop and the
    // halo-knockout colors below read the chart's opaque `canvas`, same as
    // `emit_area_layer`/`emit_line_layer`'s `chart.canvas` (see
    // `LineResolved::canvas`), because `background` may be `transparent`.
    let bg = r.background.as_str();
    let canvas = r.canvas.as_str();
    let ax_y = &r.style.axis_y;
    let x_edge = r.style.axis_x.x_edge();
    let is_log = ax_y.is_log_scale();
    // A pinned layer forces the BASE to the free side regardless of its own
    // baked position (`_resolved_layer_y_orients`, `_overlay.py`).
    let baked_on_right = ax_y.position.as_deref() == Some("right");
    // A left value axis on a shared-scale facet rides the composer's `row_header`; the panel paints
    // what Vega's `row_footer` holds, the mirror's ghost: start-aligned labels on the right edge.
    let left_primary = !baked_on_right && facet.is_some_and(|f| !f.independent_scale);
    let ghost_only = left_primary && facet.is_some_and(|f| !f.mirror_band_x);
    let paint_value_axis = paint_value_axis && !ghost_only;
    let y_on_right = left_primary || y_sides.as_ref().map_or(baked_on_right, |s| s.base == LayerSide::Right);
    // A base re-pinned onto the right edge, or one with an authored `labels.expr`,
    // loses its `labelAlign`, so its labels start at the edge rather than flip inward.
    let y_away = y_on_right && (!baked_on_right || ax_y.labels.expr.is_some() && ax_y.labels.align_kept("right").is_none());
    let is_multi = r.color.is_some();
    let color_field = r.color.as_deref();
    let x_is_quantitative = r.style.axis_x.is_quantitative;
    // A wide (folded) line never reaches `_apply_line_color_encoding`, the only
    // place a dash palette is encoded, so it paints solid.
    let dashed = is_multi && !draw_areas && r.wide_measures.is_empty() && !r.style.dashes.is_empty();
    // A colorless chart with layers names the base and every layer on the rail
    // (`layered_endpoint_rail_fires`): only when no layer splits by a color field.
    let layered_rail = !is_multi
        && r.style.endpoint_labels.visible
        && !overlays.is_empty()
        && overlays.iter().all(|o| o.color().is_none() || matches!(o, OverlayLayer::Scatter(_)));
    let show_rail = (is_multi || layered_rail) && r.style.endpoint_labels.visible;
    let has_per_series = support_table_config
        .as_ref()
        .is_some_and(|c| c.entries.iter().any(|e| e.per_series.is_some()));
    // `retires_series_legend`: production's render-time legend suppression
    // (`_suppress_series_legend`, support_table_attachment.py:4408-4446)
    // is chart-type-agnostic — it mutates the already-built spec for ANY
    // chart carrying a per_series entry, not just bar's — so
    // `r.legend.visible` alone is stale for this one shape, the same
    // documented departure bar's `legend_shown` makes.
    // A layer forces the legend on even off a colorless single-series base
    // — production's own `force_legend_visible` bakes `legend.visible: true`
    // for exactly this shape (`docs/laws-layers.md`), so `is_multi` alone
    // would wrongly suppress it here.
    let legend_wanted = (is_multi || has_layers || r.numeric_color.is_some())
        && r.legend.visible
        && !support_table_config.as_ref().is_some_and(|c| c.retires_series_legend());
    // A facet's color legend is chart-level: the composer lays it out once
    // beside or above the grid, so no panel measures or paints one.
    let facet_legend_wanted = legend_wanted && facet.is_some();
    let legend_active = legend_wanted && facet.is_none();
    let composed_strip = support_table_config
        .as_ref()
        .map(|cfg| {
            r.effective_support_table_style
                .is_some()
                .then_some(cfg)
                .ok_or("line: support_table present but effective_support_table_style absent")
        })
        .transpose()?;
    let shape = shape(show_rail);
    // ── Shape-dependent refusals ─────────────────────────────────────────
    // Every refusal decidable from the payload and the arm alone, ahead of
    // any measurement; the data- and geometry-dependent ones stay beside
    // the value they read.
    // A visible rail pins the main pane's domain (`endpoint_labels.py`): normalize
    // to `[0, 1]`, center to `[0, max total]` (the shared scale then nices it),
    // whatever was authored. Without a rail a normalize stack keeps its authored
    // pair, which resolve bakes as `[0, 1]` when none was.
    let authored_domain: Option<(f64, f64)> = match (show_rail, r.stack.as_deref()) {
        (true, Some("normalize")) => Some((0.0, 1.0)),
        (true, Some("center")) => None,
        _ => ax_y.authored_continuous_domain().or(is_normalize.then_some((0.0, 1.0))),
    };
    let legend_edge = r.legend.position.edge.as_str();
    // A floating legend sits inside the plot (`legend_overlay_origin`) and
    // reserves nothing, so only a strip names an edge.
    let legend_strip = legend_active && !r.legend.position.overlay;
    let show_legend_top = legend_strip && legend_edge == "top";
    let show_legend_left = legend_strip && legend_edge == "left";
    let show_legend_right = legend_strip && legend_edge == "right";
    let show_legend_bottom = legend_strip && legend_edge == "bottom";
    let early_overlay_rows = overlays
        .iter()
        .map(|overlay| resolve_layer_rows(payload, overlay.query_name(), r.query_name.as_deref(), "line"))
        .collect::<Result<Vec<_>, _>>()?;
    let legend_title = merged_legend_title(
        legend_title_text(color_field.unwrap_or(""), &r.legend, &r.field_labels)?,
        color_field.is_some(),
        crate::families::layers::shared_color_fields(
            overlays.iter().zip(&early_overlay_rows).map(|(overlay, rows)| (overlay, *rows)),
        ),
        &r.legend,
    );

    fn visible_labels(l: &Option<MarkLabels>) -> Option<&MarkLabels> {
        l.as_ref().filter(|ml| ml.visible)
    }
    let (value_labels, value_labels_house) = match visible_labels(&r.style.line_mark.labels) {
        Some(ml) if draw_areas => (Some(ml), r.style.label_is_house),
        Some(ml) => (Some(ml), r.style.line_label_is_house),
        None if draw_areas => (None, false),
        None => match r.style.point_mark.as_ref().and_then(|pm| visible_labels(&pm.labels)) {
            Some(ml) => (Some(ml), r.style.point_label_is_house),
            None => (None, false),
        },
    };
    if let Some(ml) = value_labels {
        if let Some(other) = ml.position.as_deref().filter(|p| crate::common::value_label_anchor(p).is_none()) {
            return Err(format!("line: value labels position {other:?} is none of top, bottom, left, right or middle").into());
        }
    }

    // ── Domains ──────────────────────────────────────────────────────────────
    // x: unique category values in data-insertion order (VL sort=null),
    // reordered by an authored `chart.sort`'s per-category MIN aggregate
    // (`common::dimension_sort_domain` — `emitters/_cartesian.py::
    // dimension_sort_to_vl`/`pin_sorted_domain`) — but ONLY when x resolves
    // nominal/ordinal. `pin_sorted_domain` no-ops off `_CATEGORICAL_TYPES`
    // (`_cartesian.py:307`), and a quantitative or calendar-detected
    // temporal x is outside it (`type_inference.resolve_cartesian_x_type`),
    // so `sort` is ignored there exactly as it is unauthored — the base
    // (unsorted) domain feeds the temporal/quantitative detection below,
    // then the sort applies to its own result only on the categorical arm.
    let x_field = &r.x;
    let y_field = &r.y;
    let fam = if draw_areas { "area" } else { "line" };

    // `bind` read the x law and parsed each distinct x once; the layers below may bring more.
    let law = x.law;
    let x_is_dates = x.is_dates;
    let law_grain_tu = x.time.grain().unit;
    let x_part = x.time.grain().part;
    let x_is_band = x.is_band;
    let x_domain_unsorted: Vec<String> = x.domain.clone();

    // Temporal label cadence — detect grain from x domain ISO dates.
    // `None` means non-temporal (raw string labels used as-is).
    let domain_strs: Vec<&str> = x_domain_unsorted.iter().map(String::as_str).collect();
    let timed = crate::families::subday::SubdaySource::of(&x.time, domain_strs.iter().copied());
    // production's `infer_vega_type_from_data` classifies the x field's raw
    // STRING SHAPE before `detect_time_unit`'s own semantic parser ever
    // runs — a bare 4-digit year parses fine but has no date-like
    // structure at all, so it never reaches `detect_time_unit` and stays
    // nominal (`x_values_are_temporal_candidates`, ported from that gate).
    let x_is_temporal_candidate = x_values_are_temporal_candidates(&domain_strs);
    // `normalize_labeled_temporal`'s `is_year_shaped` branch runs BEFORE
    // `infer_vega_type_from_data`/`detect_time_unit`, rewriting a bare year
    // in `[1700, 2100]` to `YYYY-01-01` — so a year-shaped x resolves
    // temporal (and calendar-spaced, a leap year widening its own gap) in
    // production even though it fails `x_is_temporal_candidate`'s own
    // structural gate above. `is_year_shaped` (`common.rs`) is the single
    // predicate both this cadence gate and the categorical-sort gate below
    // read — never a second, narrower reimplementation.
    let x_is_year_shaped = is_year_shaped(&payload.rows, x_field);

    let view_w = if facet.is_some() {
        svg_w
    } else {
        let scale = match law.vl_type {
            VegaType::Nominal | VegaType::Ordinal => crate::ViewScale::Discrete,
            VegaType::Quantitative | VegaType::Temporal => crate::ViewScale::Continuous,
        };
        payload.view_width(scale)?
    };
    let detected_data_tu = law_grain_tu;
    // Vega's `timeunit` transform yields NaN for a year below 100 over a calendar grain (JavaScript's
    // Date constructor reads 0-99 as 19xx).
    let nan_x = |date: &str| x.time.get(date).is_some_and(|instant| instant.nan);
    // The tooltip header detects the grain on its own (`header_tooltip_field`), so
    // it keeps one the axis dropped for being over the scaffold budget.
    let header_tu = match law.fill_grain {
        crate::families::x_type::Grain::Calendar(tu) => Some(tu),
        _ if x_part.is_some() => crate::families::temporal::detect_time_unit(&domain_strs),
        _ => detected_data_tu,
    };
    // Timed values within two days head their tooltip with the time of day (`subday_header_for`).
    let subday_header = crate::families::subday::Header::of(&timed, r.style.axis_x.labels.clock).filter(|_| header_tu.is_none());
    let header_text = |date: &str, at: Option<f64>, x_is_standard_date: bool, grain_format: Option<AuthoredTimeFormat>| -> Result<String, String> {
        match (&subday_header, at) {
            (Some(header), Some(at)) => header.text(at),
            _ => crate::contract::tooltip_header_text(date, header_tu, x_is_standard_date, grain_format),
        }
    };
    // `axis_y.mirror` on a GENUINELY NOMINAL x (not quantitative, not
    // calendar-bucketed temporal) hits the point-to-band scale flip:
    // mirror's own ghost mechanism adds an invisible interaction `rule`
    // mark that shares this chart's `x` encoding, and real Vega then
    // compiles that shared x scale as BAND instead of POINT (probed
    // directly against `vl_convert.vegalite_to_scenegraph` on a plain
    // single-chart spec, a faceted one, and `fixtures/multiples/06-line-
    // rows` with `mirror: true` forced — see `laws-line-area.md`'s
    // band-position section for the derivation). Vertices/ticks land at
    // `step*(i+0.5)` (band centers, `step = plot_w / n`, VL's own `scale.
    // padding: 0` on the mirrored x encoding) instead of `i/(n-1) *
    // plot_w` (point scale) — `mirror_wants_band` below is this port's own
    // reading of "does the shared x resolve to that band scale", read by
    // `use_band_positions` further down to switch `pos_at`/`label_x_at`/
    // `band_center`/`x_rule_at` without engaging `is_band_step`'s own
    // `curve: step` row-doubling transform. `facet.mirror_band_x` carries
    // the SAME test computed once by `render_facet` (`mirror_active` is
    // always forced `false` under `Some(facet)` — the composer owns the
    // ghost, but this panel's own marks/ticks must still land where that
    // ghost's band scale puts them).
    let mirror_wants_band = (mirror_active || facet.is_some_and(|f| f.mirror_band_x))
        && !x_is_quantitative
        && detected_data_tu.is_none();
    // `detected_data_tu.is_none()` is this port's own cadence detection: an ISO date x
    // no calendar grain explains also takes the band flip under mirror.
    // Front-end contract only: `infer_vega_type_from_data`'s STRICT
    // `all_temporal` gate (`x_values_are_standard_dates`) -- a field this
    // strict types "temporal" even with no detected bucketed grain, so its
    // structured-tooltip header still formats through the continuous-date
    // fallback (`tooltip_header_text`'s own doc comment). Confirmed against
    // the corpus: `playground/chart-lab-import-review/area_review`'s own
    // 28-day cadence matches no bucketed grain yet still headers "Apr 27,
    // 2025", never the raw "2024-01-07".
    let x_is_standard_date = crate::families::temporal::x_values_are_standard_dates(&domain_strs);
    // The grain a redefined `date_<grain>` name is keyed by is the one the
    // labels resolve to (`resolve_label_time_unit`), not the data's own.
    let label_grain = match detected_data_tu {
        Some(tu) => crate::families::temporal::LabelUnit::resolve(tu, r.style.axis_x.labels.time_unit.as_deref())?.grain(),
        None => None,
    };
    // A standard date no calendar grain explains is a continuous axis: Vega
    // labels every one of its own ticks through the format.
    let authored_time_format = match detected_data_tu {
        Some(_) => AuthoredTimeFormat::parse(
            fam,
            crate::families::temporal::axis_time_format(
                r.style.axis_x.labels.spec(),
                &r.style.axis_x.grain_formats,
                label_grain,
            ),
        )?,
        None if x_is_standard_date => AuthoredTimeFormat::parse(fam, r.style.axis_x.labels.spec())?,
        None => None,
    };
    let grain_header_format = AuthoredTimeFormat::parse(
        fam,
        crate::families::temporal::grain_format(&r.style.axis_x.grain_formats, header_tu),
    )?;
    let x_is_authored_ordinal = r.style.axis_x.resolved_authored_type() == Some("ordinal");
    // A category: not quantitative (resolve's flag), and neither the data nor the
    // law makes it a date (year and labeled-bucket columns are rewritten to
    // dates first, so `from_data` is already temporal for them).
    let x_is_categorical = !x_is_quantitative
        && matches!(law.from_data, VegaType::Nominal | VegaType::Ordinal)
        && matches!(law.vl_type, VegaType::Nominal | VegaType::Ordinal);
    // Re-derived off `x_is_categorical` (not the coarser `!x_is_quantitative
    // && detected_data_tu.is_none()` test the refusal above used) now that
    // the year-shaped/production-temporal/authored-temporal exclusions are
    // available too — a bare year or an ISO date, both of which production
    // resolves temporal (unaffected by the band flip) despite `detected_
    // data_tu.is_none()`, must not engage band positions.
    let mirror_wants_band = mirror_wants_band && x_is_categorical;
    let x_domain: Vec<String> = if x_is_band {
        dimension_sort_domain(
            &payload.rows,
            x_field,
            x_domain_unsorted,
            r.sort.as_ref(),
            SortAggregate::Min,
            fam,
        )?
    } else {
        x_domain_unsorted
    };
    // A quantitative x is a linear scale: its marks sit where their values do, in ascending order
    // (a line mark sorts by its continuous x), and its ticks are d3's.
    let quant_x: Option<crate::families::scatter::LinearX> = {
        let values: Option<Vec<f64>> = x_domain.iter().map(|v| v.parse().ok()).collect();
        values.filter(|v| !v.is_empty() && x_is_quantitative && !x_is_dates && law.vl_type == VegaType::Quantitative).map(|values| {
            let (lo, hi) = values.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), &v| (lo.min(v), hi.max(v)));
            crate::families::scatter::LinearX::over(&r.style.axis_x, lo, hi, (false, false), r.style.axis_x.is_zero_anchored())
        })
    };
    let x_domain: Vec<String> = if quant_x.is_some() {
        let mut sorted = x_domain;
        sorted.sort_by(|a, b| a.parse::<f64>().unwrap().total_cmp(&b.parse::<f64>().unwrap()));
        sorted
    } else {
        x_domain
    };
    let quant_extent = quant_x.as_ref().map(|lin| lin.domain_at(svg_w));
    // Every layer's own x column widens the shared x scale, as production's
    // `overlay_x_domain_values` does; only a categorical scale warns that the
    // widened order is paint order (a continuous scale orders itself).
    let layer_x_columns = overlays
        .iter()
        .map(|overlay| {
            let band_step = x_is_band && overlay.is_step_curve();
            let layer_rows = panel_layer_rows(
                rows, facet.is_some(), band_step, payload, overlay.query_name(), r.query_name.as_deref(), "line",
            )?;
            Ok((layer_rows, overlay.x().unwrap_or(x_field.as_str())))
        })
        .collect::<Result<Vec<_>, String>>()?;
    let x_union = crate::families::layers::union_x_domain(&x_domain, &layer_x_columns, r.sort.is_some());
    let overlay_clips: Vec<bool> = overlays
        .iter()
        .zip(&layer_x_columns)
        .map(|(overlay, (rows, _))| crate::families::layers::layer_clip(overlay.layer_type(), overlay.y(), rows, &r.style.axis_y, is_normalize, y_sides.is_some()))
        .collect();
    if let (true, Some(layer_only)) = (x_is_categorical, &x_union.paint_order) {
        crate::families::cartesian::warn_x_domain_paint_order(&r.id, x_field, layer_only);
    }
    // A point scale seats an annotation number past the data as it seats data.
    let past_the_data = if x_is_quantitative {
        crate::annotation::x_numbers_outside(&x_union.domain, &r.annotations).map_err(|e| format!("{fam}: {e}"))?
    } else {
        Vec::new()
    };
    // A date x refuses an annotation number, as `AxisMap::Time` does for one inside the data.
    if let Some(number) = r.annotations.iter().find(|a| x_is_dates && !past_the_data.is_empty() && a.x_numbers().next().is_some()) {
        return Err(number.payload_error(&r.id, "a number on a time axis"));
    }
    let mut x_domain = if past_the_data.is_empty() {
        x_union.domain
    } else {
        crate::families::layers::extend_domain_in_base_order(&x_union.domain, &past_the_data)
            .ok_or_else(|| format!("{fam}: an annotation past the x data needs an x domain in numeric order"))?
    };
    if law.vl_type == VegaType::Temporal {
        x.time.sort(&mut x_domain);
    }
    // A number x widened by an annotation is no date axis, so it has no instants.
    let x_time: Option<TimeColumns> = (x.unparsed.is_none() && past_the_data.is_empty())
        .then(|| x.time.columns(&x_domain).expect("bind read every x key"));

    // Band-aware step: `curve: step` — the LITERAL name, never
    // `step-before`/`step-after` — is production's `step_band.is_band_step`
    // (`step_band.py:77`: `curve == "step" and x_type in ("nominal",
    // "ordinal")`), gated on the RESOLVED VL x type (`type_inference.
    // resolve_cartesian_x_type`), not on "did we detect a time unit". Three
    // resolutions, by what production does with the x:
    // - A plain category (`x_is_categorical`) resolves nominal/ordinal from the
    //   data itself and bands unconditionally: the row-doubling transform. So
    //   does an authored `axis_x.type: ordinal` (`:290-291`), ahead of any date
    //   reading, even over standard ISO dates.
    // - A QUANTITATIVE x, an authored `axis_x.type: temporal` (`:285-289`, ahead
    //   of the band flip at `:292-294`) and a standard ISO date no calendar
    //   grain explains (`x_type_from_data`, `:306`) never band: `step` passes
    //   through to Vega-Lite's own `interpolate: step`, d3's `curveStep` over
    //   the x scale. A standard date draws it on its bare temporal axis, the
    //   same one a `linear` curve gets.
    // - A CALENDAR-BUCKETED x (`detected_data_tu.is_some()`, or a labeled bucket such as
    //   `2024-Q1` that production anchors to an ISO date) is the one case `curve: step`
    //   itself flips ordinal (`test_step_with_iso_date_x_stays_ordinal`): the rows are
    //   gap-filled to one per bucket and the band-step transform applies.
    // Every other non-categorical x raises the no-marks error: an authored-temporal
    // column of non-dates, which Vega-Lite cannot place on a time scale at all.
    let curve_is_literal_step = curve_name == Some("step");
    // Forces the interpolate to `StepAfter` (`BAND_STEP_INTERPOLATE`)
    // regardless of what plain `step` would otherwise mean. `disconnected`
    // (line only — area's `_apply_area_step_band` always hardcodes
    // `connect=True`, so `AreaMark` carries no such field) draws each
    // plateau as its own subpath instead of one continuous chain.
    let is_band_step = curve_is_literal_step && x_is_band;
    // The position-only half of the band-scale law: MARKS and TICKS land
    // at band centers (`step*(i+0.5)`) whenever the shared x resolves to a
    // genuine band scale — `is_band_step`'s own `curve: step` shape, OR
    // `mirror_wants_band`'s mirror-ghost shape. Every POSITION call
    // site (`fit_x_at`/`band_center_x`/`label_x_at`/`x_rule_at`, the
    // `LineXAxisBody::Nominal` tick build, the ink-measurement closure's
    // own `pos_at`/`band_center` pair) reads this flag; every TRANSFORM
    // call site (row-doubling `runs_of_band`, the `disconnected` plateau
    // split, `StepAfter`'s own curve dispatch, the `is_multi`/`layer`/
    // value-label refusals just below) keeps reading `is_band_step` alone
    // — `curve: step`'s own row-doubling has no counterpart under a plain
    // mirror-ghost band scale, which draws ONE vertex per category, not
    // two band-edge points.
    // A literal-`step` line/area layer over a band x doubles its rows onto the
    // band edges on the shared x scale, which VL then compiles as a band scale
    // under the base too (`step_band.apply_step_band` via `_apply_layer_step_band`).
    let layer_band_step = x_is_band
        && overlays.iter().any(|o| matches!(o, OverlayLayer::Line(l) if l.curve_name() == Some("step")));
    // A bar layer needs a band scale, which VL then compiles under the base's marks too.
    let has_bar_layer = overlays.iter().any(|o| matches!(o, OverlayLayer::Bar(_)));
    let use_band_positions = is_band_step || mirror_wants_band || layer_band_step || (x_is_band && has_bar_layer);
    if is_band_step {
        curve = d3rs::shape::Curve::StepAfter;
        if is_multi {
            let chart_id = payload.resolved["id"].clone();
            return Err(if r.wide_measures.is_empty() {
                Diagnostic::new(&crate::codes::ERR_STEP_BAND_MULTI_SERIES, [("chart_id", chart_id)])
            } else {
                let chart_type = serde_json::json!(if draw_areas { "Area" } else { "Line" });
                Diagnostic::new(&crate::codes::ERR_BAND_STEP_MULTI_METRIC, [("chart_id", chart_id), ("chart_type", chart_type)])
            });
        }
    }
    let disconnected = is_band_step && !draw_areas && r.style.line_mark.connect == Some(false);
    let plateau_share = if disconnected { crate::families::layers::plateau_share(r.style.line_mark.disconnected_width, "line")? } else { 1.0 };
    // A dash palette (`style.dashes`) is a strokeDash encoding that outranks the
    // mark's own dasharray.
    let authored_dash = match r.style.line_mark.stroke.dasharray.as_deref() {
        Some(raw) if !dashed => Some(crate::step_band::parse("line", raw, disconnected)?),
        _ => None,
    };

    // A1: series DRAW order is the data's first-appearance row order. Vega
    // facets a multi-series line/area by the color field and emits one mark
    // group per key in that order — probed on `area_won_stacked`'s own
    // compiled spec: reordering `data.values` reorders the scenegraph's area
    // groups, while swapping `color.scale.domain`/`range` for the
    // alphabetical pair leaves them alone. Production reaches an
    // alphabetical PALETTE index over this spatial domain by permuting the
    // emitted `color.scale.range` (`emitters/area.py`'s `_area_spatial_order`
    // → `distinct_series_values`), so the two orders are two lists, not one.
    // An unstacked wide chart seeds this from `wide_series` (sorted,
    // complete) instead of scanning rows — a measure null on every row has
    // no folded row to be scanned from, but still owns a domain/palette
    // slot. A stacked wide chart scans the folded rows like any other: its
    // fold order is the authored measure list (`fold_order=measures`,
    // `_emit_multi_metric_area`), which first-appearance reproduces.
    let spatial_domain: Vec<String> = if !r.wide_measures.is_empty() && !is_stacked {
        wide_series.to_vec()
    } else {
        match color_field {
            // A colorless stack is one unnamed series.
            None if is_stacked => vec![String::new()],
            None => Vec::new(),
            Some(cf) => {
                let mut seen = HashSet::new();
                let mut domain: Vec<String> = Vec::new();
                // A facet's series are the chart's, not one panel's.
                for row in domain_rows {
                    let s = row_str(row, cf);
                    if seen.insert(s.clone()) {
                        domain.push(s);
                    }
                }
                domain
            }
        }
    };

    // Stacked-area accumulation order: production's
    // `sorted_series_by_stack_order` (`style.stack_order`; unauthored is the
    // "value" default — descending global sum, largest first, alphabetical
    // tie-break) — a THIRD list beside
    // `spatial_domain` (paint order, first-appearance, unaffected by
    // stacking — probed: reordering an explicit `order` field moves which
    // series sits at the baseline, but never the scenegraph's own paint
    // order) and the STACK POSITION each series occupies. `baseline_order[0]`
    // is the series painted first off zero; each later series stacks on the
    // running sum of every one before it, at every x independently (`stack:
    // center`'s own offset is column-local, computed further down once
    // `data`/`x_domain` exist). Computed here, ahead of `palette_domain`,
    // because `_apply_area_color_encoding`'s `palette_order = reversed(order)`
    // (`order` top-of-stack-first) is EXACTLY this list on a stacked area —
    // the palette index is baseline order too, not the generic alphabetical
    // assignment an unstacked chart uses.
    let mut baseline_order: Vec<String> = Vec::new();
    if is_stacked {
        let cf = color_field.unwrap_or_default();
        let ranked: &[String] = if r.wide_measures.is_empty() { &spatial_domain } else { &wide_series };
        baseline_order = crate::common::sorted_series_by_stack_order(
            ranked,
            domain_rows,
            cf,
            y_field,
            r.style.stack_order.as_deref(),
        )?;
    }

    // Palette index: alphabetical (the VL nominal default) off an unstacked
    // chart's `palette_domain`; baseline order on a stacked one (above). A
    // dash palette pins both the color and strokeDash scales to
    // data-insertion order instead (`_distinct_in_order`); area charts
    // never bind one.
    let palette_domain: Vec<String> = if is_stacked {
        baseline_order.clone()
    } else if dashed {
        spatial_domain.clone()
    } else {
        let mut domain = spatial_domain.clone();
        domain.sort();
        domain
    };

    // The board's plan for this chart's color field, if it binds one
    // (#7167) — every fill/ink lookup below defers to it over the local
    // `palette_domain` position (`common::series_color`/`series_ink`).
    let category_scale: Option<&crate::common::CategoryColorScale> =
        color_field.and_then(|cf| crate::common::category_scale_for(&r.category_colors, cf));

    // Legend display order: descending by each series' last non-null value
    // over the SPATIAL domain, ties broken by name ascending — production's
    // `sorted_series_by_last_value` (`_cartesian.py:1582-1602`) sorts
    // `with_value` series by the key `(-values[s], s)` (`_cartesian.py:
    // 1598-1602`) and appends `without_value` series (no non-null value
    // anywhere) sorted alphabetically at the end. The `f64::NEG_INFINITY`
    // sentinel below already sorts no-value series after every real-valued
    // one in a single unified sort, so `.then_with(a.cmp(b))` reproduces
    // both branches: real-valued ties break alphabetically, and any
    // no-value/no-value tie (equal sentinels) also lands in alphabetical
    // order. Feeds `apply_legend_entry_order` (emitters/line.py:158-162)
    // independently of which palette slot each series' color comes from.
    // A stacked chart lists top of stack first instead: the reversed
    // baseline order (`_apply_area_color_encoding`'s `order`,
    // `_emit_multi_metric_area`'s `display_order`).
    let legend_domain: Vec<String> = if is_stacked {
        baseline_order.iter().rev().cloned().collect()
    } else if dashed {
        // A dash palette locks the color and strokeDash domains to data-insertion order.
        spatial_domain.clone()
    } else {
        let last_value = match color_field {
            Some(cf) => crate::common::last_nonnull_value_per_series(&x_ranked_rows(domain_rows, x_field, &x_domain), x_field, y_field, cf)?,
            None => HashMap::new(),
        };
        let mut domain = spatial_domain.clone();
        domain.sort_by(|a, b| {
            let av = last_value.get(a).copied().unwrap_or(f64::NEG_INFINITY);
            let bv = last_value.get(b).copied().unwrap_or(f64::NEG_INFINITY);
            bv.partial_cmp(&av).unwrap().then_with(|| a.cmp(b))
        });
        domain
    };

    // a wide line/area's fold
    // emission reorders to `display_order` (`emitters/line.py:394`,
    // `area.py:342`'s `fold_order=display_order` on the unstacked branch),
    // which is exactly `legend_domain` above — and that fold order DOUBLES
    // as mark PAINT order (`_wide.py::fold_wide_measures`'s doc comment:
    // "unstacked/overlap area, which paints series front-to-back in row
    // sequence"). Every consumer below this point (`series_points`, the
    // rail cascade feed) reads `spatial_domain` as paint order, so a wide
    // chart's `spatial_domain` is display order, not first-appearance —
    // unlike an authored `color:` chart, where production leaves row order
    // alone and first-appearance IS correct (A1 above). `palette_domain`/
    // `baseline_order`/`legend_domain` itself are already computed off the
    // PRE-reassignment domain, so this reordering cannot desync them —
    // `palette_domain`'s own alphabetical `.sort()` is invariant to it
    // either way.
    let spatial_domain: Vec<String> =
        if !r.wide_measures.is_empty() && !is_stacked { legend_domain.clone() } else { spatial_domain };

    // support_table per_series: row order + label ink for a `per_series:`
    // entry. Row order mirrors `_series_order_strip`
    // (support_table_attachment.py:2635-2718): a stacked area takes its
    // "largest global sum" branch — exactly this file's own
    // `baseline_order` (already computed above); a line/non-stacked area
    // takes the "last-x y ascending" branch (`last_y_ascending_order`).
    // Ink is `common::series_ink` on `palette_domain` — the SAME lookup the
    // endpoint rail's own label ink uses (this file's other `series_ink`
    // call). Computed only when a per_series entry is actually present —
    // `series_ink` hard-errors past a too-small board category-color scale,
    // which a chart with no per_series entry must never pay for. Empty
    // (harmless) with no color field — `build_rows_composed` refuses a
    // per_series entry with an empty `series_order` by name.
    let per_series_order: Vec<String> = if !has_per_series {
        Vec::new()
    } else if is_stacked {
        baseline_order.clone()
    } else if let Some(cf) = color_field {
        last_y_ascending_order(&x_ranked_rows(domain_rows, x_field, &x_domain), x_field, y_field, cf, &spatial_domain)?
    } else {
        Vec::new()
    };
    let per_series_ink: Vec<String> = per_series_order
        .iter()
        .map(|s| {
            crate::common::series_ink(
                s,
                &palette_domain,
                &r.series_palette,
                &r.style.series_label.dark_companion_palette,
                &r.style.series_label.pin_companions,
                category_scale,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;

    // ── Data index: (x_val, series) → y_val ─────────────────────────────────
    // `None` is a bucket with no row at all; `Some(None)` is a row that
    // EXISTS with a null measure — see `series_points`'s doc comment below
    // for why the two draw differently. Built here (ahead of `series_points`,
    // its other consumer) because the stack accumulation below needs it too.
    // A layer moves the base line off the facet's own dataset: over a temporal
    // x it draws every row of the chart in each panel, a sawtooth through
    // every panel's points.
    let base_walks_every_row = facet.is_some() && has_layers && law.vl_type == VegaType::Temporal;
    let base_rows: &[serde_json::Map<String, Value>] = if base_walks_every_row { domain_rows } else { rows };
    let data = stack_data(base_rows, x_field, color_field, y_field);
    // The row each datum reads, for a caption printing another column.
    let mut label_rows: HashMap<(String, String), &serde_json::Map<String, Value>> = HashMap::new();
    for row in base_rows {
        let sv = color_field.map(|cf| row_str(row, cf)).unwrap_or_default();
        label_rows.insert((row_str(row, x_field), sv), row);
    }

    let mut columns: Vec<StackColumn> = Vec::new();
    let mut max_col_total = 0.0_f64;
    let mut min_col_floor = 0.0_f64;
    if is_stacked {
        for xv in &x_domain {
            let column = stack_column(&data, xv, &baseline_order, r.stack.as_deref() == Some("zero"));
            let (pos_sum, neg_sum) = (column.pos, column.neg);
            let col_total = pos_sum;
            max_col_total = max_col_total.max(col_total);
            min_col_floor = min_col_floor.min(neg_sum);
            columns.push(column);
        }
    }
    // A facet's shared y scale unions every panel's own stack (Vega stacks per
    // panel, then merges the scale domains).
    let (domain_col_total, domain_col_floor) = match facet {
        Some(_) if is_stacked && !independent_scale => {
            let (mut top, mut floor) = (0.0_f64, 0.0_f64);
            for panel in multiples::regroup(&r.panel_axes, domain_rows)? {
                let panel_data = stack_data(&panel.rows, x_field, color_field, y_field);
                for xv in &x_domain {
                    let column = stack_column(&panel_data, xv, &baseline_order, r.stack.as_deref() == Some("zero"));
                    top = top.max(column.pos);
                    floor = floor.min(column.neg);
                }
            }
            (top, floor)
        }
        _ => (max_col_total, min_col_floor),
    };
    // Column-local offset a series' running sum starts from: 0 for `zero`,
    // Vega-Lite's own centered-offset formula for `center` — probed against
    // asymmetric per-x totals: `(max_col_total - this_col_total) / 2`, not
    // the naive `-total/2` symmetric silhouette one might assume. Floats
    // every column up so the tallest sits flush at `[0, max_col_total]` and
    // none exceeds that ceiling — the same domain a `zero` stack renders on.
    let is_center = r.stack.as_deref() == Some("center");
    let offset_at = |x_idx: usize| -> f64 {
        if is_center { crate::common::center_offset(max_col_total, columns[x_idx].total) } else { 0.0 }
    };

    // raw y domain from payload or data extent
    let tick_count = ax_y.ticks.count.unwrap_or(6.0) as usize;
    // The y extent follows the panel under an independent scale; x stays shared.
    let y_domain_rows: &[serde_json::Map<String, Value>] = if independent_scale { base_rows } else { domain_rows };
    let data_min = y_domain_rows
        .iter()
        .filter_map(|row| row_num(row, y_field))
        .fold(f64::INFINITY, f64::min);
    let data_max = y_domain_rows
        .iter()
        .filter_map(|row| row_num(row, y_field))
        .fold(f64::NEG_INFINITY, f64::max);
    // A log axis or an independent scale bakes no ladder, so the layers' share
    // of the one y scale (Vega-Lite unions the layers' domains) comes from
    // their own rows.
    let (data_min, data_max) = if is_log || independent_scale {
        let mut extent = (data_min, data_max);
        for overlay in overlays {
            let layer_rows = panel_layer_rows(
                domain_rows, facet.is_some(), false, payload, overlay.query_name(), r.query_name.as_deref(), "line",
            )?;
            for v in layer_rows.iter().filter_map(|row| row_num(row, overlay.y())) {
                extent = (extent.0.min(v), extent.1.max(v));
            }
        }
        extent
    } else {
        (data_min, data_max)
    };
    // An area layer's fill baseline joins the shared domain at zero, which
    // a log scale cannot place (`log_nice_domain`).
    let log_domain_min = if overlays.iter().any(|o| matches!(o, OverlayLayer::Line(l) if l.area_mark.is_some())) {
        0.0
    } else {
        data_min
    };
    // Real Vega's cell ink is the panel's own marks, never the facet's
    // shared domain — `own_min`/`own_max` (THIS panel's own `rows`) feed
    // the mark-ink box at both sites below (the fit-pass ink union and
    // `LinePanelGeometry.ink`'s own mark box); `data_min`/`data_max`
    // (above) stay on the SHARED `domain_rows` extent for the domain,
    // headroom and zero/unity-rule decisions only (bar's own law: `bar.rs`'s
    // mark ink reads `rows`, never a facet's shared domain). Equal to
    // `data_min`/`data_max` off the single-chart path (`domain_rows ==
    // rows` whenever `facet` is `None`).
    let own_min = base_rows.iter().filter_map(|row| row_num(row, y_field)).fold(f64::INFINITY, f64::min);
    let own_max = base_rows.iter().filter_map(|row| row_num(row, y_field)).fold(f64::NEG_INFINITY, f64::max);
    let headroom = ax_y.scale.get("headroom").and_then(Value::as_f64).unwrap_or(0.0);

    // BaselineFeature's zero rule is a `datum: 0` layer, so whenever it
    // renders it also pulls 0 into the shared y domain (Vega-Lite unions
    // layer domains). Gates mirror production `_insert_zero_rule`
    // (baseline.py): grid.visible + a zero style, an AUTHORED
    // `scale.continuous.domain` excluding 0 suppresses the layer (the baked
    // domain_min/max never participate — `authored_measure_domain`), and
    // explicit zero:false only yields when the data itself straddles 0.
    let explicit_zero_false = ax_y.is_zero_false();
    // A baked edge pinned away from 0 overrules the rule as well
    // (`build_zero_rule_if_applicable`): the datum could not join the domain.
    let excludes_zero = authored_domain.is_some_and(|(a, b)| !(a.min(b) <= 0.0 && 0.0 <= a.max(b)))
        || ax_y.domain_min.is_some_and(|floor| floor > 0.0)
        || ax_y.domain_max.is_some_and(|ceiling| ceiling < 0.0);
    // A normalize-stacked bar/area reroutes BOTH its 0 and 1 datum rules
    // through `_apply_zero_or_top`'s own `_insert_top_rules` path
    // (`baseline.py:330-347`) instead of the zero-straddle/unity gates
    // below — always fires whenever not log and grid+threshold are
    // visible, with no zero-anchor or percent-format test at all (the
    // 0%/100% pair is meaningful unconditionally, since `normalize`
    // guarantees the domain is `[0, 1]`). This port draws both rules off
    // one shared gate rather than splitting across the two call sites
    // production uses to reach the same visual result — mirrors
    // `bar.rs`'s own `zero_w_for` gate.
    let normalize_rules_fire =
        !is_log && ax_y.grid.visible.unwrap_or(false) && ax_y.grid.threshold_visible();
    // Production skips `_bake_zero_flag`'s caller entirely for a log-typed
    // axis (`_domain.py::_bake_y_zero`) and `build_zero_rule_if_applicable`
    // refuses a log scale outright (`_cartesian.py` ~1288) — a log domain
    // cannot carry a literal 0 datum. And `_apply_zero_or_top`
    // (baseline.py) returns before ever considering the zero rule when
    // `chart.stack == "center"`: a streamgraph's y=0 is the silhouette's
    // visual centerline, not a meaningful baseline, so an explicit rule
    // there would read as chart noise.
    // Every layer on the shared scale joins its data extent: a bar from the 0
    // baseline, a line or scatter at its values, an area at its pile. An independent
    // facet scale already joined them above.
    let (layer_lo, layer_hi) = if y_sides.is_some() || is_log || independent_scale {
        (f64::INFINITY, f64::NEG_INFINITY)
    } else {
        let layers = overlays
            .iter()
            .map(|overlay| {
                panel_layer_rows(
                    domain_rows, facet.is_some(), false, payload, overlay.query_name(), r.query_name.as_deref(), "line",
                )
                .map(|rows| (overlay, rows))
            })
            .collect::<Result<Vec<_>, _>>()?;
        crate::families::layers::layers_value_extent(&layers, x_field.as_str(), &nan_x)
    };
    // BaselineFeature asks whether 0 lies in the domain every mark on the scale
    // shares (`_zero_in_shared_domain`): the base's values joined with each unpinned
    // layer's, a bar layer always counting (it paints from 0).
    let zero_in_shared_domain = (data_min.min(layer_lo) <= 0.0 && 0.0 <= data_max.max(layer_hi))
        || (y_sides.is_none() && has_bar_layer);
    // `BaselineFeature.apply` returns early under an independent facet scale
    // (`baseline.py:318-321`): no zero, unity or normalize rule exists there.
    let zero_rule_fires = if independent_scale {
        false
    } else if is_normalize {
        normalize_rules_fire
    } else {
        // `BaselineFeature` adds no zero rule to a chart whose own query has no rows.
        !domain_rows.is_empty()
            && !is_log
            && !is_center
            && ax_y.grid.visible.unwrap_or(false)
            && ax_y.grid.threshold_visible()
            && !excludes_zero
            && (!explicit_zero_false || zero_in_shared_domain)
    };
    // C5 — production's `_apply_unity` (`baseline.py`, PR #8241): a
    // percent-formatted measure axis carries a `datum: 1` rule wherever the
    // domain the emitter PINS reaches 1.0. Its other gates cannot vary on
    // this family: the measure always rides y (`_y_carries_the_measure`
    // guards a rotated scatter), `_measure_field` is the payload's own `y`.
    // Unlike the zero rule this one never pulls its datum into the domain:
    // the gate already demands the pinned domain contain 1.0, so the
    // `datum: 1` union is inert. `common::unity_rule_fires` is the ONE copy
    // of this gate — scatter.rs reuses it verbatim rather than re-deriving
    // it. Unlike the zero rule above, production's `_apply_unity` carries
    // NO log gate at all — a percent-formatted log axis whose data extent
    // straddles 1.0 draws this rule; `unity_rule_fires`'s own
    // unbaked-domain fallback (branch 4 of `effective_measure_domain`, the
    // data extent) already reads this shape correctly, since a log arm
    // never bakes a domain.
    let unity_rule_fires = if independent_scale {
        false
    } else if is_normalize {
        normalize_rules_fire
    } else {
        // A pinned layer makes the scale independent, which skips
        // `BaselineFeature`; `_overlay.py` re-adds the zero rule alone.
        unity_rule_fires(ax_y, data_min, data_max) && y_sides.is_none()
    };
    // The `datum` positions `BaselineFeature`'s rule layers render at, each
    // with whether it fires — read by both arms' ink unions and by the
    // emission, so no two of them can disagree about what is drawn.
    let baseline_rule_data = [(0.0, zero_rule_fires), (1.0, unity_rule_fires)];

    // The zero union happens BEFORE domain resolution: Vega's configureDomain
    // runs zero, then the domainMin/Max end overrides, then nice — so the
    // datum-0 pull participates in nice's input, not as a post-hoc widen.
    let (domain_lo, domain_hi) = if zero_rule_fires {
        (data_min.min(0.0), data_max.max(0.0))
    } else {
        (data_min, data_max)
    };
    let (domain_lo, domain_hi) = (domain_lo.min(layer_lo), domain_hi.max(layer_hi));
    // A zero-anchored measure scale pins `domainMin` to its baked ladder's lowest
    // rung, which a negative value pulls below 0.
    let domain_lo = if ax_y.is_zero_anchored() && ax_y.domain_min.is_none() && !is_log && !is_stacked {
        crate::common::effective_measure_domain(ax_y, Some((domain_lo, domain_hi))).map_or(domain_lo, |(lo, _)| lo)
    } else {
        domain_lo
    };

    // Apply d3 nice() only when y axis is on the left (multi-series charts).
    let (y_min, y_max) = if is_log {
        // An authored pair reaches Vega verbatim, in authored order; only an
        // authored `nice` rounds it out to powers of the base.
        match authored_domain {
            Some(pair) if ax_y.scale.get("nice").and_then(Value::as_bool) == Some(true) => {
                crate::families::cartesian::log_nice_pair(ax_y.log_base(), pair)
            }
            Some(pair) => pair,
            None if ax_y.scale.get("nice").and_then(Value::as_bool) == Some(false) => (log_domain_min, data_max),
            None => crate::families::cartesian::log_nice_domain(ax_y.log_base(), log_domain_min, data_max),
        }
    } else if is_stacked {
        // A stacked area's domain is Vega-Lite's own — production bakes no
        // `domain_max`/`domain_min` for it (a nice-rounded ladder rung would clip
        // marks above it) and applies no headroom (single-series-only there), so
        // the port takes the SAME `d3_nice(_, _, 10)` call the non-stacked path
        // below uses, fed the stacked column total instead of the raw per-row
        // max — probe-confirmed against `stacked_area`'s own reference (May's
        // 247-unit total nices to 260, not the 300 its separately-baked
        // `tick_values` cover ladder overshoots to; the existing tick-filtering
        // below already clips a ladder rung past the real domain).
        // A visible rail pins the main pane's domain to `[0, max_column_total]`
        // for `center` (`endpoint_labels.py`), and that pin lands on the hconcat's
        // SHARED y scale, whose merge re-applies Vega-Lite's default `nice`
        // (`listening_streamgraph` reads 0 to 180 off `[0, 161]`). The railed
        // domain is therefore `d3_nice(0, max_column_total, 10)`: the `None` arm.
        match authored_domain {
            // `ax_y.domain_min`/`domain_max` (the BAKED pair) are always
            // None on a stacked area (`resolve/chart/area.py` forces them
            // there), so guarding on those would be unreachable code —
            // `authored_continuous_domain()` is production's own spelling
            // of the field that actually reaches Vega
            // (`emitters/_cartesian.py`'s `authored_measure_domain`
            // branch), honored verbatim with no `nice` applied (probed).
            // Emitted in AUTHORED order (`resolve_measure_y_scale`:
            // `{"domain": list(authored_domain)}`): a descending pair
            // inverts Vega's axis.
            Some((a, b)) => (a, b),
            // A zero-anchored measure scale of a UNIT spec pins `domainMin`
            // (or `nice: false` when no ladder rung supplies a floor), and an
            // explicit `domainMin` is what suppresses Vega-Lite's default
            // `nice: true` (`type_inference.py::y_zero_scale`, probed): the
            // top edge is the raw stacked total. A visible rail concatenates
            // a second pane over a SHARED y scale, which re-applies the
            // default `nice` (the `listening_streamgraph` reading above); a
            // support table or a layer reshapes the spec the same way.
            None if !show_rail
                && ax_y.is_zero_anchored()
                && domain_col_floor == 0.0
                && ax_y.scale.get("nice").and_then(Value::as_bool) != Some(true)
                && r.support_table.is_none()
                && (y_sides.is_some() || !has_layers) =>
            {
                (0.0, domain_col_total)
            }
            // `y_zero_scale`: a zero-anchored scale pins `domainMin` to the ladder's
            // lowest rung (the data's own negative floor where no computed ladder
            // supplies one, `zero_anchor_pinned_floor`); the top then stays raw
            // unless the spec reshapes the scale and Vega-Lite's default `nice` returns.
            None if domain_col_floor < 0.0 && ax_y.is_zero_anchored() => {
                let ladder_floor = ax_y
                    .scale
                    .get("values")
                    .is_none_or(Value::is_null)
                    .then(|| ax_y.tick_values.iter().copied().fold(f64::INFINITY, f64::min))
                    .filter(|floor| floor.is_finite());
                let floor = ladder_floor.unwrap_or(domain_col_floor);
                let unit_spec = !show_rail
                    && ax_y.scale.get("nice").and_then(Value::as_bool) != Some(true)
                    && r.support_table.is_none()
                    && (y_sides.is_some() || !has_layers);
                if unit_spec { (floor, domain_col_total) } else { d3rs::ticks::d3_nice(floor, domain_col_total, 10) }
            }
            None => d3rs::ticks::d3_nice(domain_col_floor, domain_col_total, 10),
        }
    } else if let Some((a, b)) = authored_domain {
        // Production's `resolve_measure_y_scale` (`_cartesian.py:1564-1575`):
        // an authored `scale.continuous.domain` replaces the whole
        // data-driven zero/headroom/nice computation outright and reaches
        // Vega VERBATIM, in authored order — `{"domain": list(authored_
        // domain)}`, never sorted. Vega's own linear scale accepts a
        // descending pair (`domain[0] > domain[1]`) as-is: its
        // `interpolateNumber` term order (`LinearScale::position`, this
        // module's G1) is agnostic to which end is larger, it only flips
        // which physical range endpoint each domain value lands on — so
        // `(a, b)` needs no reordering here, and `LinearScale::new` below
        // already accepts `domain_min > domain_max`.
        (a, b)
    } else if independent_scale && ax_y.is_zero_anchored() {
        // `y_zero_scale`: a zero-anchored scale with no ladder to pin a floor
        // is `zero: true, nice: false`; an authored `nice: true` rounds it.
        let (lo, hi) = (data_min.min(0.0), data_max.max(0.0));
        if ax_y.scale.get("nice").and_then(Value::as_bool) == Some(true) { d3rs::ticks::d3_nice(lo, hi, 10) } else { (lo, hi) }
    } else {
        crate::families::cartesian::resolve_measure_domain(
            domain_lo,
            domain_hi,
            ax_y.domain_min,
            ax_y.domain_max,
            ax_y.ticks.count,
            headroom,
            ax_y.is_zero_false(),
            shape == Shape::RailConcat,
        )
    };
    // A layer merges the base's y encoding into a second `layer[]` entry
    // sharing ONE Vega-Lite scale — compiling that union flips the scale's
    // `nice` default from unset (an un-nested baked `domainMax` renders
    // EXACT, never rounded) to `true`, extending a baked domain past its
    // own headroom pin at render time — the SAME quirk `bar.rs`'s own
    // shared-scale layer arm already ports (`docs/laws-layers.md`; probed:
    // `charts/layered-charts_2/revenue_vs_trend`'s baked `domainMax`
    // 3654.68 renders as `d3_nice(0, 3654.68, 10) == (0, 4000)`, matching
    // the reference's own "values from 0 to 4,000" axis). A log domain is
    // already niced to powers of ten, and a stacked area never reaches here
    // alongside a layer (refused above).
    // A visible rail's hconcat merges the panes' y scales the same way, so an
    // authored pair is rounded too. d3's scale `nice` swaps a descending pair
    // around the same rounding.
    let renice = (has_layers && y_sides.is_none())
        || (shape == Shape::RailConcat && authored_domain.is_some() && !is_stacked);
    let (y_min, y_max) = match (renice, is_log, y_min > y_max) {
        (false, _, _) => (y_min, y_max),
        (true, true, _) => crate::families::cartesian::log_nice_pair(ax_y.log_base(), (y_min, y_max)),
        (true, false, false) => d3rs::ticks::d3_nice(y_min, y_max, 10),
        (true, false, true) => {
            let (lo, hi) = d3rs::ticks::d3_nice(y_max, y_min, 10);
            (hi, lo)
        }
    };

    // ── Tick values ──────────────────────────────────────────────────────────
    // A log axis's tick count is a FIXED d3 default (10), never
    // `ceil(extent/40)`, regardless of what the payload carries:
    // `ERR_TICKS_COUNT_REQUIRES_NON_LOG_SCALE` only refuses the chart's
    // OWN AUTHORED count at compile time (`_authored_axis_y_ticks_count`
    // is deliberately read pre-cascade) — the THEME's own default still
    // resolves onto the axis regardless, so a real log-scale payload does
    // carry a `ticks.count` (`axis-scales_0/world_population_log`'s own
    // resolved style: `"ticks": {"count": 6, …}"`). It is simply never
    // consulted here, because Vega-Lite compiles no `tickCount` signal for
    // a log-typed axis either way (probe: the compiled Vega axis carries
    // `tickCount: {signal: "ceil(height/40)"}` for a linear scale and no
    // `tickCount` key at all for a log one, at every probed height 40..2000
    // — so `scale.ticks()`/`tickFormat()` both run with d3's own unset-count
    // default, literal 10). The refusal gate above correctly does NOT list
    // `ticks.count` — doing so would refuse both swept log rows, since the
    // theme default is always present.
    // d3 hands a descending domain's ticks back reversed.
    let log_scale_ticks = || {
        let mut ticks = crate::families::cartesian::log_ticks(ax_y.log_base(), y_min.min(y_max), y_min.max(y_max), 10);
        if y_max < y_min {
            ticks.reverse();
        }
        ticks
    };
    let log_scale_tick_count = if is_log { log_scale_ticks().len() } else { 0 };
    let mut tick_values: Vec<f64> = if is_log {
        // Authored `scale.values` replace the scale's ticks; Vega draws those inside the domain.
        match ax_y.authored_scale_values() {
            Some(values) => crate::families::cartesian::ladder_within_domain(&values, y_min, y_max),
            None => log_scale_ticks(),
        }
    } else if !ax_y.tick_values.is_empty() {
        // A descending authored domain (`y_min > y_max`) keeps its baked
        // ladder in AUTHORED order; only the range test is direction-agnostic.
        crate::families::cartesian::ladder_within_domain(&ax_y.tick_values, y_min, y_max)
    } else if (is_normalize && !has_layers) || (independent_scale && !has_layers) {
        // An independent facet scale is never baked and its spec carries no `tickCount`
        // (a layered spec keeps the theme's).
        // A normalize-stacked axis with no layer is ALWAYS ladder-less — resolve gives it
        // no tick bake at all (`_CartesianTickResolution((), None)`,
        // `compile/resolve/chart/area.py:474-477`), so the theme's own
        // baked `ticks.count` (still present on the resolved style, same
        // as a log axis's — see the log comment above) is never consulted
        // by production's VL emission either. Vega falls back to its own
        // default `tickCount`, `ceil(height/40)`, never `tick_count`
        // above — `rungs_at` recomputes this again once the real plot
        // height is known; this first pass uses the panel's own declared
        // `svg_h`, the same height `rungs_at`'s own first call reuses.
        crate::families::cartesian::unbaked_ticks(ax_y, y_min, y_max, vl_default_tick_count(svg_h))
    } else {
        // No baked ladder → Vega computes its own d3 ticks INSIDE the
        // domain (production's nice_tick_values is the cover-ladder used
        // only when it also bakes the matching domain bounds; using it
        // here painted ticks past the domain, off the top of the plot).
        crate::families::cartesian::unbaked_ticks(ax_y, y_min, y_max, tick_count)
    };

    let ruler = ax_y.ruler.as_ref();
    let tick_label = ax_y.tick_label.as_ref();
    // A normalize-stacked axis is always a 0-100% share axis, unconditionally
    // — never a default the resolved `axis_y.labels.format` can opt out of
    // (`emitters/_cartesian.py::pin_normalize_axis_format`, shared by bar and
    // area). Pinned here rather than read off the resolved style.
    let normalize_format = is_normalize.then(|| FormatSlot { spec: ".0%".to_string(), ..FormatSlot::default() });
    let labels_format = normalize_format.as_ref().or(ax_y.labels.format.as_ref());
    // A log axis's minor decade-multiple labels are BLANKED by d3's own
    // `scale.tickFormat()` whenever more ticks are drawn than the count
    // asked for (`log_tick_mantissa_visible`) — and, unlike the working
    // theory this port started from, an authored `labels.format` does NOT
    // suppress that blanking: it only changes what STRING the surviving
    // ticks draw. Probe-confirmed against `axis-scales_0/world_population_
    // log`'s own reference SVG — with `labels.format: ".3~s"` authored, the
    // reference still draws all 19 `<text>` tags (one per decade
    // multiple, matching the grid) — but a blanked tick's tag is
    // self-closing, empty bounds, no content, never an `opacity="0"` on
    // real text; only the 11 mantissa survivors ever carry text, and only
    // THOSE ever get `greedy_visible`'s own `opacity="0"` (below). So a
    // blanked entry contributes NO text (and no ink) to anything
    // downstream — the gutter, the fit union, or the greedy candidate
    // sequence — same as if it were never in the ladder.
    //
    // Derives fresh from whichever `ticks` slice the caller passes, rather
    // than a mask captured once against the INITIAL `tick_values` and
    // indexed positionally — `tick_values` is `mut` and reassigned by
    // `rungs_at` on other arms; a stale positional mask would either index
    // panic or silently reserve gutter width for a rung that was never
    // re-evaluated. Every call site below passes the SAME slice it is
    // itself iterating, so this stays correct even if a future change ever
    // makes `rungs_at` reachable for `is_log` (today it never is).
    let is_mantissa_hidden = |ticks: &[f64], i: usize| -> bool {
        is_log
            && !ax_y.composes_numeral_expr()
            && ax_y.authored_scale_values().is_none()
            && !crate::families::cartesian::log_tick_mantissa_visible(ax_y.log_base(), ticks, log_scale_tick_count, 10)[i]
    };

    // Format all tick labels (with reservation chars for SVG output).
    // The lines each value label paints on, kept beside the widths the last `ladder_labels` call returned.
    let y_label_rows = std::cell::RefCell::new(Vec::<usize>::new());
    let primary_labels = |ticks: &[f64]| -> Result<Vec<String>, String> {
        ticks
            .iter()
            .enumerate()
            .map(|(i, &tv)| {
                if is_mantissa_hidden(ticks, i) {
                    return Ok(String::new());
                }
                if is_normalize {
                    return crate::common::normalize_percent_label(tv);
                }
                ax_y.labels.apply_expr("y", tv, format_y_label(tv, ruler, tick_label, labels_format, i, ticks.len())?, i, ticks.len())
            })
            .collect()
    };
    let ladder_labels = |ticks: &[f64]| -> Result<(Vec<String>, Vec<f64>), String> {
        let labels = primary_labels(ticks)?;
        *y_label_rows.borrow_mut() = labels.iter().map(|l| crate::text::line_count(l)).collect();
        let ws = labels
            .iter()
            .map(|lbl| fonts.measure_shaped(lbl, y_family(ax_y), y_font_size(ax_y)))
            .collect::<Result<_, _>>()?;
        Ok((labels, ws))
    };
    // The ghost axis's own labels and widths when it carries a `format`
    // (`mirror_axis.py`: `compose_axis_format` on the ghost axis, the same
    // painter the primary's plain `labels.format` takes); `None` when it
    // repeats the primary's.
    let ghost_ladder = |ticks: &[f64]| -> Result<Option<(Vec<String>, Vec<f64>)>, String> {
        let primary = primary_labels(ticks)?;
        let Some(labels) = mirror.as_ref().map(|g| g.labels(ticks, &primary)).transpose()?.flatten() else { return Ok(None) };
        let ws = labels
            .iter()
            .map(|lbl| fonts.measure_shaped(lbl, y_family(ax_y), y_font_size(ax_y)))
            .collect::<Result<_, _>>()?;
        Ok(Some((labels, ws)))
    };
    let (mut formatted_ticks, mut y_label_ws) = ladder_labels(&tick_values)?;
    // The MARGIN reserves for every label, as production bakes it.
    let mut max_y_label_w = y_label_ws.iter().copied().fold(0.0_f64, f64::max);
    // An unbaked, uncounted value axis takes Vega-Lite's default
    // `tickCount`, `ceil(height/40)`, as d3 rungs at the pane's height —
    // the Vega arms re-fit on them; the legacy arm keeps the provisional
    // count-6 ladder above. A log axis never re-fits: its tick count is the
    // fixed default above, independent of the fitted extent (same probe).
    // `normalize` is treated as uncounted UNCONDITIONALLY, ignoring the
    // theme's baked `ticks.count` — production's own resolve never bakes
    // one for it (see the first-pass comment above), so `ax_y.ticks.count.
    // is_none()` alone would miss the theme's non-`None` default.
    let rungs_at = |extent: f64| -> Option<Vec<f64>> {
        (!is_log && ((is_normalize && !has_layers) || (independent_scale && !has_layers) || (ax_y.tick_values.is_empty() && ax_y.ticks.count.is_none())))
            .then(|| {
                let count = vl_default_tick_count(extent);
                crate::families::cartesian::unbaked_ticks(ax_y, y_min, y_max, count)
            })
    };
    // Vega's parity overlap on the y labels (`labelSeparation` is the
    // theme's min_gap; production forwards none when unset, Vega's 0).
    let y_separation = ax_y.labels.min_gap.unwrap_or(0.0);
    // Vega-Lite compiles a log-typed quantitative axis with `labelOverlap:
    // "greedy"`, not the `true`/"parity" default every other quantitative axis
    // gets: a single forward pass keeping each label that clears the LAST KEPT
    // one. It runs over the MANTISSA-SURVIVOR ink only: a blanked rung carries a
    // self-closing, empty-text tag in the reference SVG, never `opacity="0"`
    // (an `opacity="0"` only ever marks a greedy-HIDDEN survivor), so it neither
    // becomes a greedy candidate nor blocks its neighbors.
    let visible_y_labels = |ticks: &[f64], ink: &[vega_fit::Bounds]| -> Vec<bool> {
        if !is_log {
            return vega_fit::parity_visible(ink, y_separation);
        }
        let (survivor_idx, survivor_ink): (Vec<usize>, Vec<vega_fit::Bounds>) =
            ink.iter().enumerate().filter(|(i, _)| !is_mantissa_hidden(ticks, *i)).map(|(i, &b)| (i, b)).unzip();
        let mut full = vec![false; ink.len()];
        for (idx, vis) in survivor_idx.into_iter().zip(vega_fit::greedy_visible(&survivor_ink, y_separation)) {
            full[idx] = vis;
        }
        full
    };

    // ── Title geometry ───────────────────────────────────────────────────────
    // Width-resolved title font (style.title_font) wins over the un-tiered
    // title_style.font — a nested card titles at 14px Inter, not 18px serif.
    let eff_title_style = crate::common::effective_title_style(
        r.style.title_font.as_ref(),
        r.title_style.as_ref(),
    );
    let eff_title_ref = eff_title_style.as_ref().or(r.title_style.as_ref());
    let title_font_size = eff_title_ref.and_then(|ts| ts.font.size).unwrap_or(18.0);

    // ── x-axis label geometry ────────────────────────────────────────────────
    let ax_x = &r.style.axis_x;
    let x_font_size = ax_x.labels.font.size_or(11.0);
    let x_label_pad = ax_x.labels.padding.unwrap_or(6.0);
    let x_tick_len = ax_x.ticks.length.unwrap_or(6.0);
    let x_family = ax_x.labels.font.family.as_deref().unwrap_or("sans-serif");
    // A quantitative x on the point scale ticks where a linear axis over the
    // same numbers would (the theme's baked `tickCount`), not at every point.
    let quant_x_ticks: Option<Vec<(String, f64)>> = match ax_x.ticks.count {
        Some(count) if x_is_quantitative && !use_band_positions => {
            Some(quantitative_x_ticks(&x_domain, count as usize).map_err(|e| format!("{fam}: {e}"))?)
        }
        _ => None,
    };
    // L1-L4's overlap remedies (Vega arms only — the legacy arm never reads
    // these). `label_usable_ratio` itself is required, but only where a
    // temporal axis actually needs it, so its `.ok_or` stays local to each
    // arm's `Some(data_tu)` branch rather than unconditionally here.
    let label_usable_ratio_opt = r.style.label_usable_ratio;
    let x_overlap = ax_x.labels.overlap.as_ref();
    let x_overlap_skip = x_overlap.is_some_and(|o| o.skip);
    let x_overlap_tilt = x_overlap.is_some_and(|o| o.tilt);
    let x_tilt_increments = ax_x.labels.tilt_increments.as_deref();
    let x_separation = ax_x.labels.min_gap.unwrap_or(0.0);

    // Padding, `svg_w`/`svg_h` are the panel's own box, already params.
    let pad_l = pad.map(|p| p.left).unwrap_or(0.0);
    let pad_r = pad.map(|p| p.right).unwrap_or(0.0);
    let pad_t = pad.map(|p| p.top).unwrap_or(0.0);
    let pad_b = pad.map(|p| p.bottom).unwrap_or(0.0);
    let avail_w = svg_w - pad_l - pad_r;

    // Axis titles — see bar.rs's matching comment / common.rs's
    // axis_title_band doc for the measured production constant. x title
    // always sits at the bottom (chart.x/ax_x); y title rotates on
    // whichever side y_on_right names (chart.y/ax_y). Computed ahead of the
    // temporal cadence choice below: title bands are a function of title
    // text/style alone, never of x-axis label content.
    let x_title_text = axis_title_text(x_field, r.x_label.as_deref(), &r.field_labels)?;
    // A folded y has no single measure field to title from — fall back to
    // the joined measure names (`wide_measures_title`), same as an authored
    // `y_label` always would override either fallback.
    let y_title_text = if r.wide_measures.is_empty() {
        axis_title_text(y_field, r.y_label.as_deref(), &r.field_labels)?
    } else {
        match r.y_label.as_deref() { Some(l) => crate::common::collapse_title_newlines(l), None => crate::common::wide_measures_title(&r.wide_measures, &r.field_labels)? }
    };
    let y_axis_title = if y_sides.is_none() {
        let layer_titles = overlays
            .iter()
            .map(|o| axis_title_text(o.y(), o.label(), &r.field_labels))
            .collect::<Result<Vec<_>, _>>()?;
        crate::families::layers::merged_axis_title(std::iter::once(y_title_text.clone()).chain(layer_titles))
    } else {
        y_title_text.clone()
    };
    let facet_grid = crate::families::multiples::grid_counts(r.multiples.as_ref(), &r.panel_axes);
    let ((x_extent, x_panels), (y_extent, y_panels)) = if facet.is_some() {
        multiples::facet_title_boxes((payload.width, payload.height), (facet_grid.1, facet_grid.0), ax_y.mirror_requested("line")?, 0.0)
    } else {
        ((svg_w, 1.0), (svg_h, 1.0))
    };
    let x_title_paint = crate::common::wrapped_axis_title(&r.id, "x_label", &x_title_text, &ax_x.title.font, x_extent, x_panels, fonts)?;
    let y_title_paint = crate::common::wrapped_axis_title(&r.id, "y_label", &y_axis_title, &ax_y.title.font, y_extent, y_panels, fonts)?;
    let axis_titles = DualAxisTitles::new(y_sides.is_some(), ax_y, r.y_label.as_deref());
    let layer_title_style = axis_titles.layer(ax_y);
    let y_title_style = axis_titles.base(ax_y);
    let y_title_band = axis_title_band(y_title_style, &y_title_paint);

    // Front-end contract: structured-tooltip content for this chart's own
    // datum points, zero/unity rule, and (multi-series) endpoint-rail
    // labels. Gated off entirely (no aria, today's unwrapped/unlabeled
    // status quo -- never a refusal, since each of these shapes already
    // renders correctly and a new `Err` here would regress a passing pixel
    // row) for the shapes this pass doesn't model: a wide-measures fold, a
    // `chart.layers` combo (`_cartesian_roles`'s wide/combo branches build a
    // different role shape this port doesn't reproduce), and band-step
    // (`curve: step` on a band x — production's per-row doubling means this
    // port's plain per-x iteration below wouldn't match its row count).
    let tooltip_labels = !r.style.tooltip_format.spec.is_empty();

    // ── y-axis label measurement ─────────────────────────────────────────────
    let y_font_sz = y_font_size(ax_y);
    let y_fam = y_family(ax_y);
    // Own-side alignment (the default: value axis on the right with labels
    // flipped inward) replaces the themed labelPadding with the measured
    // breathing room, same as bar.rs's val_pad. A left-side value axis keeps
    // its normal away-from-plot growth, which autosize already reserves
    // correctly from the themed padding.
    // The re-pin pops the base axis's own `labelAlign`/`labelPadding` when it
    // moves to the other edge, leaving Vega's own default padding (2.0).
    let y_label_pad = if left_primary {
        ax_y.labels.padding.unwrap_or(6.0)
    } else if y_away || (!y_on_right && baked_on_right) {
        MOVED_BASE_LABEL_PAD
    } else if y_on_right && !y_away {
        VALUE_AXIS_BREATHING_ROOM
    } else {
        ax_y.labels.padding.unwrap_or(6.0)
    };

    // ── Rail measurement (multi-series with endpoint labels) ─────────────────
    let sl = &r.style.series_label;

    // A1: `cascade_rail_anchors` sorts stably, so the order it is fed
    // is the tie-break — first appearance, as production's own stable
    // `sorted_series_by_last_value` gives it.
    //
    // Stacked: production's `_stacked_midpoints` anchors each series at
    // its own most-recent NON-NULL x, independently — reusing the SAME
    // per-series walk-back the unstacked arm's own anchors already run
    // below (`last_nonnull_xy_per_series`), and each anchor's own
    // COLUMN (`columns[anchor]`, already 0.0-seeded per absent/null
    // series by the accumulation loop above) rather than one shared
    // trailing column. A series with no real value anywhere falls back
    // to the domain's LAST x, zero-height there (`columns[last]`
    // already carries its 0.0 seed) — the seam between its neighbors,
    // never a missing anchor (the rail replaces the legend; a dropped
    // series would paint unnamed).
    let rail_anchors: Vec<(String, f64)> = if !show_rail {
        Vec::new()
    } else if layered_rail {
        // `_apply_layered_single_series`: each entry is a layer (the base
        // first) at its own most recent non-null value; a repeated label keeps
        // its first place and its last value.
        let mut entries: Vec<(String, &[serde_json::Map<String, Value>], &str, &str)> =
            vec![(y_title_text.clone(), domain_rows, x_field.as_str(), y_field.as_str())];
        for overlay in overlays {
            let label = axis_title_text(overlay.y(), overlay.label(), &r.field_labels)?;
            let layer_rows = resolve_layer_rows(payload, overlay.query_name(), r.query_name.as_deref(), "line")?;
            entries.push((label, layer_rows, overlay.x().unwrap_or(x_field.as_str()), overlay.y()));
        }
        let mut out: Vec<(String, f64)> = Vec::new();
        for (label, entry_rows, x, y) in entries {
            let Some(value) = crate::common::last_nonnull_value(&x_ranked_rows(entry_rows, x, &x_domain), x, y)? else { continue };
            match out.iter_mut().find(|(l, _)| *l == label) {
                Some(slot) => slot.1 = value,
                None => out.push((label, value)),
            }
        }
        out
    } else if is_stacked {
        let last = x_domain.len() - 1;
        let mut out = Vec::with_capacity(baseline_order.len());
        // A wide measure null on every row has no painted mark but still
        // owns a rail entry, after the painted series.
        let unpainted = baseline_order.iter().filter(|s| !spatial_domain.contains(s));
        for series in spatial_domain.iter().chain(unpainted) {
            let anchor = x_domain
                .iter()
                .enumerate()
                .rev()
                .find_map(|(i, xv)| {
                    data.get(&(xv.clone(), series.clone())).copied().flatten().map(|_| i)
                })
                .unwrap_or(last);
            let idx = baseline_order.iter().position(|s| s == series).expect("series in baseline_order");
            let col = &columns[anchor];
            // `_stacked_midpoints`'s `mid / col_total` branch
            // (`endpoint_labels.py`): the SAME midpoint, divided by
            // its own anchor column's total when it has one.
            let raw_mid = (col.bounds[idx].0 + col.bounds[idx].1) / 2.0;
            let mid = match (is_normalize, col.total > 0.0) {
                (true, true) => raw_mid / col.total,
                (true, false) => raw_mid,
                (false, _) => offset_at(anchor) + raw_mid,
            };
            out.push((series.clone(), mid));
        }
        out
    } else {
        let cf = color_field.expect("a rail names its series by a color field");
        let ranked = x_ranked_rows(rows, x_field, &x_domain);
        let last_value = crate::common::last_nonnull_value_per_series(&ranked, x_field, y_field, cf)?;
        spatial_domain
            .iter()
            .filter_map(|series| last_value.get(series).map(|yv| (series.clone(), *yv)))
            .collect()
    };

    // Where the rail's labels land after the cascade, in data space, for a
    // pane `pane_h` tall. Callers convert to pixels through the scale, which
    // handles either direction. The span is `.abs()`ed, so a descending
    // domain keeps its slope.
    let rail_cascade = |pane_h: f64| -> Vec<(String, f64)> {
        let y_span = (y_max - y_min).abs();
        let px_per_unit = (y_span > 0.0).then(|| (pane_h / y_span).abs());
        // The layered rail's shared domain spans every layer's own rows too
        // (`_layered_y_domain`); a series-color rail reads the base rows alone.
        let extent = overlays.iter().filter(|_| layered_rail).fold((data_min, data_max), |(lo, hi), overlay| {
            let layer_rows = resolve_layer_rows(payload, overlay.query_name(), r.query_name.as_deref(), "line")
                .expect("validate_line_layers already confirmed this dataset exists");
            layer_rows.iter().filter_map(|row| row_num(row, overlay.y())).fold((lo, hi), |(lo, hi), v| (lo.min(v), hi.max(v)))
        });
        let (lo, hi) = rail_cascade_bounds(is_stacked, is_normalize, max_col_total, authored_domain, extent);
        recascade_rail_anchors(&r.id, &rail_anchors, sl.gap_px, px_per_unit, lo, hi)
    };

    let el = &r.style.endpoint_labels;
    let rail_pane_w = if show_rail {
        // Wide measures: the rail measures the PAINTED (humanized) text,
        // never the raw column name `palette_domain` itself carries.
        let rail_series: Vec<String> = if layered_rail {
            rail_anchors.iter().map(|(label, _)| label.clone()).collect()
        } else {
            palette_domain.iter().map(|s| display_label(s)).collect()
        };
        crate::families::cartesian::warn_rail_labels_cut(
            &r.id,
            if r.series_from_measures { "y" } else { "color" },
            &rail_series,
            &sl.font_family,
            sl.font_size,
            &sl.font_weight,
            fonts,
            svg_w,
        )?;
        crate::families::cartesian::rail_pane_width(
            &rail_series,
            &sl.font_family,
            sl.font_size,
            &sl.font_weight,
            fonts,
            svg_w,
        )?
    } else {
        0.0
    };

    // ── Legend measurement (multi-series; endpoint_labels.visible bakes the
    // suppression decision into r.legend.visible already — an author who
    // overrides it explicitly gets a legend alongside the rail, see
    // `series-label-placement`'s "Keeping the legend anyway" cases).
    // `direction == "vertical"` (right/bottom) is the general law; a
    // `direction: horizontal` legend is modeled only at `position: top` on
    // this fit arm — Vega's symbol-legend entry grid, the same law bar's
    // own top legend carries (`legend.rs::legend_grid`/`vega_legend`,
    // reused verbatim). Err on anything else rather than guess a layout.
    let legend_fills: Vec<String> = legend_domain
        .iter()
        .map(|s| series_fill(s, &palette_domain, &r.series_palette, true, category_scale))
        .collect::<Result<Vec<_>, _>>()?;
    // A plain line layer turns on the mixed stroke legend for every stroke row.
    let has_plain_line = overlays.iter().any(|o| matches!(o, OverlayLayer::Line(l) if l.area_mark.is_none()));
    // Wide measures: each entry's PAINTED label is display text
    // (`display_label`), never the raw column name `legend_domain` itself
    // carries for lookup purposes.
    let legend_display_labels: Vec<String> = legend_domain.iter().map(|s| display_label(s)).collect();
    let layer_base_entries: Vec<(String, String)> = if is_multi {
        legend_display_labels.iter().cloned().zip(legend_fills.iter().cloned()).collect()
    } else {
        vec![(y_title_text.clone(), single_fill.clone())]
    };
    // The layers' shared `color:{datum}` scale: each overlay claims its label
    // (or, grouped by color, its values) after the base's own entries. A label
    // repeated over a color-split base raises; elsewhere the ordinal scale dedupes it.
    let mut layer_scale = crate::families::layers::LayerScale::new(&layer_base_entries, is_multi).with_rail(layered_rail);
    // Per overlay: its legend label, color domain, and the color its own claim
    // took, which an ungrouped layer paints as a literal even when its label
    // repeats an earlier entry (the legend keeps that entry).
    let overlay_rows = overlays
        .iter()
        .map(|overlay| resolve_layer_rows(payload, overlay.query_name(), r.query_name.as_deref(), "line"))
        .collect::<Result<Vec<_>, _>>()?;
    let layers: Vec<_> = overlays.iter().zip(overlay_rows).collect();
    let overlay_plan = crate::families::layers::claim_layers(&mut layer_scale, &layers, &r.field_labels)?;
    let numeric_legends =
        crate::families::bar::legend::numeric_legends_of(r.numeric_base()?, overlays.iter().zip(&overlay_plan), &r.legend, &r.id)?;
    // Vega's ordinal strokeDash scale cycles its range by domain index; an
    // empty entry is a solid line. One pattern per legend entry.
    let series_dashes: Vec<String> = legend_domain
        .iter()
        .map(|series| if dashed { series_dash(&palette_domain, &r.style.dashes, series) } else { String::new() })
        .collect();
    // Under the endpoint-rail concat the strokeDash scale is shared across the panes, so its
    // legend sits at the concat root and the color legend inside the plot pane draws solid.
    let dash_legend_at_root = dashed && legend_active && shape == Shape::RailConcat;
    let legend_dashes: Vec<String> =
        if dash_legend_at_root { vec![String::new(); series_dashes.len()] } else { series_dashes.clone() };
    let legend_entries: Vec<LegendEntry> = if has_layers {
        // A grouped base with no line layer keeps its circle glyphs.
        let has_line_overlay = overlays.iter().any(|o| matches!(o, OverlayLayer::Line(_)));
        let base_glyph = if draw_areas || (is_multi && !has_line_overlay && !has_bar_layer) {
            LegendSymbol::Circle
        } else if !has_plain_line {
            LegendSymbol::ShortStroke
        } else {
            LegendSymbol::Stroke(2.0)
        };
        layer_scale.legend_entries(&layer_base_entries, |_| base_glyph)
    } else if r.numeric_color.is_some() {
        Vec::new()
    } else {
        legend_display_labels
            .iter()
            .zip(&legend_fills)
            .zip(&legend_dashes)
            .map(|((s, f), dash)| LegendEntry {
                label: s.as_str(),
                fill: f.as_str(),
                symbol: dashed.then_some(LegendSymbol::Dash(dash.as_str())),
                series_key: None,
                recede: None,
            })
            .collect()
    };
    let legend_entries = if has_layers { narrow_legend_entries(legend_entries, &r.legend) } else { legend_entries };
    // ── Plot width + left margin ─────────────────────────────────────────────
    // Computed ahead of the temporal cadence choice below: neither depends on
    // bottom_margin (x-axis label row height), only on the y-axis/legend/rail
    // geometry measured above — so the cadence choice can use the real final
    // plot width instead of `avail_w` (which still includes the y-axis
    // gutter/legend/rail column the plot itself doesn't get). Passing the
    // wider `avail_w` there under-coarsens a narrow chart's x-axis (regression
    // on `playground/xaxis-cadence-ladder`'s `nov_line`: a real 28.2px band
    // needs quarter openers, but `avail_w`'s wider approximation let every
    // month "fit").
    // `data` (the (x_val, series) → y_val index) is built earlier, ahead of
    // the stack accumulation above — `None` is a row that EXISTS with a
    // null/non-finite measure, which is not the same thing as a bucket with
    // no row at all: Vega-Lite's default `defined` predicate (`isValid(x) &&
    // isFinite(+x) && isValid(y) && isFinite(+y)`) rejects the former and
    // d3-shape ends the mark's current subpath there, while the latter never
    // reaches d3 and the mark draws straight across it. Production's own
    // rows carry both (its pivot fills part of the series × bucket grid with
    // nulls) — see `series_points`.

    // ── Plot-rect negotiation ────────────────────────────────────────────
    // Default: Vega's own fit-margin algebra (vega_fit) on the resolved
    // inputs — production compiles the right-axis line/area shapes as a
    // plain `layer` spec under `autosize: fit`, the same ceil'd ink
    // negotiation bar.rs already runs. Left-axis layouts, endpoint rails,
    // and legends compile as concat specs with production-precomputed
    // dimensions (the hconcat comments on the constants above), and
    // support_table switches off autosize-fit — all keep the legacy
    // measured-constant margins. Assumed on the fit path (present in the
    // payload, not read): no authored labels.expr on either axis (see
    // AGENTS.md's fit-path gap bullets). `labels.align` on the x axis IS
    // read on both Vega arms (L8, below) — never on this legacy arm.
    // A `position: top` support_table compiles to the SAME plain layer spec,
    // switched to `autosize: {type: pad, contains: padding}`: the payload
    // dims become the plot rect and the canvas grows around the ink
    // (`apply_chart_support_table_post_pass`). So it takes this arm with the
    // strip spliced into the ink pass, and only the shapes production
    // compiles differently — a bottom strip, a left axis, a rail — stay on
    // the legacy splice.
    // Endpoint-rail concat: production compiles multi-series line/area with
    // endpoint labels to an hconcat of [main pane, label pane] and corrects
    // the pane sizes with a single scenegraph probe
    // (converters/chart._correct_concat_overshoot). The chrome that probe
    // measures is closed-form here, so one pass reproduces the corrected
    // pane geometry without a probe. Gated to the shape the corpus compiles:
    // left y axis, no legend kept alongside the rail, and either no support
    // table or one production splices into pane 0 exactly this arm already
    // models — a `position: top` strip with no legend riding along (S7):
    // production has no modeled path for a `position: bottom` strip, a
    // right y axis, or a kept legend alongside the rail, so those keep the
    // legacy splice unchanged.
    // Authored x scale padding (VL `scale.padding`, pixels): insets the
    // point range on both ends — marks sit at [pad, pw − pad], so they stop
    // contributing edge ink once the padding exceeds their stroke reach.
    let authored_x_padding = ax_x.scale.get("padding").and_then(Value::as_f64).unwrap_or(0.0);
    // A band scale reads `scale.padding` as the fractional inner and outer
    // padding (`BandGeometry`); a point scale as a pixel inset of its range.
    let (x_scale_pad, x_band_padding) =
        if use_band_positions { (0.0, authored_x_padding) } else { (authored_x_padding, 0.0) };
    let x_band_geometry = |width: f64| {
        BandGeometry::new(x_domain.len(), 0.0, width, x_band_padding, x_band_padding)
    };
    let x_ticks_visible = ax_x.ticks.visible.unwrap_or(true);
    let x_tick_eff = if x_ticks_visible { x_tick_len } else { 0.0 };
    let x_limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let y_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let line_stroke_w = r.style.line_mark.stroke.width.unwrap_or(4.0);


    // A2: `detect_time_unit` refusing a cadence does not make the axis
    // non-temporal. Production's `_temporal_layout` returns an empty
    // `AxisLabelLayout` (`_label_overlap.py:215-222` — the return whose
    // docstring names the 28-day gap): no `values`, no `labelExpr`, no
    // `timeUnit`, `labelOverlap` OMITTED. Vega-Lite then compiles its bare
    // continuous default — a local `time` scale (no `scale.type`; renders run
    // `TZ=UTC`) whose ticks come from d3's `timeDay` field filter — and the
    // ticks become
    // Vega's OWN — see `temporal::bare_temporal_ticks`.
    let bare_temporal = detected_data_tu.is_none()
        && (x_is_temporal_candidate || x_is_year_shaped)
        && x_time.as_ref().is_some_and(|time| time.extent().is_some());
    // Any axis that draws a continuous scale's own ticks: a bare time scale, or a linear number one.
    let continuous_axis = bare_temporal || quant_x.is_some();


    // The bare axis still lays its labels out under the grain
    // `detect_time_unit` finds (`_temporal_layout`), though the x-type law drops
    // that grain from the encoding: a year column too sparse for its yearly
    // scaffold tilts and thins by the year-grain ladder.
    let bare_layout_tu: Option<TimeUnit> = match law.fill_grain {
        crate::families::x_type::Grain::Calendar(tu) if bare_temporal => Some(tu),
        // An authored `none` is no grain `_temporal_layout` takes, so it detects its own.
        crate::families::x_type::Grain::None if bare_temporal && ax_x.time_unit.as_ref().and_then(Value::as_str) == Some("none") => {
            crate::families::temporal::detect_time_unit(&x_domain.iter().map(String::as_str).collect::<Vec<_>>())
        }
        _ => None,
    };

    // An authored `axis_x.ticks.time_unit` is production's own d3 interval
    // for a temporal x (`type_inference.py:1026-1052`); off a temporal axis
    // it raises (`ERR_TICKS_INTERVAL_NOT_TEMPORAL`), before this point. An authored `ticks.count` beside it cannot
    // be authored at all (`DimensionTicksStyle`'s `AtMostOneOf`). A count
    // alone is `tickCount: N` on a temporal x (`apply_x_tick_cadence`,
    // `:417-418`): on a bare axis it chooses the d3 rung, and an ordinal or
    // quantitative x reads it elsewhere or not at all.
    let x_tick_cadence: Option<AuthoredTickCadence> = match ax_x.ticks.time_unit.as_deref() {
        None => {
            match ax_x.ticks.count {
                Some(count) if bare_temporal || detected_data_tu.is_some() => {
                    Some(AuthoredTickCadence::Count(authored_tick_count(count)))
                }
                _ => None,
            }
        }
        Some(unit) => {
            let authored_ordinal = r.style.axis_x.resolved_authored_type() == Some("ordinal");
            let temporal_vega_arm = detected_data_tu.is_some() || bare_temporal;
            if authored_ordinal || !temporal_vega_arm {
                return Err(crate::refuse!(
                    "{fam}: axis_x.ticks.time_unit {unit:?} off a temporal x axis on a Vega arm is not supported: production raises ERR_TICKS_INTERVAL_NOT_TEMPORAL on a non-temporal or authored-ordinal axis, and the legacy margin arm draws no calendar ticks"
                ).into());
            }
            Some(authored_tick_interval(unit, ax_x.ticks.step).map_err(|e| e.in_context(fam))?)
        }
    };

    // A bare axis over timed values ticks and labels on the clock.
    let subday_plan = if bare_temporal {
        crate::families::subday::plan_for(
            &timed,
            ax_x,
            x_tick_cadence,
            authored_time_format.is_some(),
            if shape == Shape::RailConcat { svg_w - (rail_pane_w + el.label_offset) } else { svg_w },
            svg_w,
        )
    } else {
        None
    };
    // L6: production's one `value_labels` feature (`features/value_labels.py`)
    // compiles a VL `text` layer over the same data the marks draw, and
    // `autosize: fit` unions its ink like any other mark's — the label
    // centered on the first datum overhangs the plot's left edge by half its
    // width (`revenue_labeled`'s origin 20 → 36).
    // `_LINE_POS_MAP["top"]`: `baseline: bottom, dy: -4`, with an authored
    // `dx`/`dy` composing as a nudge on top of it. Text and measured advance
    // are fixed ahead of the fit — only the positions move with the plot
    // rect. Measured SHAPED, which is what Vega's own layout measures: the
    // kern pairs in a grouped decimal ("8,640.0" — 39.86 shaped against
    // 40.95 raw advances) decide `revenue_labeled`'s left margin.
    // Unread when no labels are drawn.
    let (value_label_family, value_label_size) = value_labels
        .map(|ml| crate::common::crowd_label_font(ml.font.as_ref(), payload.board_style.as_ref()))
        .transpose()?
        .unwrap_or_default();
    // A band-aware step curve anchors left/right captions at their band's edge; the
    // domain's leading (left) or trailing (right) band, when labeled, has no room on
    // that side and falls back to `top` at the band's center.
    let label_spot = |i: usize| -> CaptionSpot {
        let band = is_band_step.then(|| (i == 0, i + 1 == x_domain.len()));
        match value_labels {
            Some(ml) => CaptionSpot::of(ml, band).expect("position validated above"),
            None => CaptionSpot::top(),
        }
    };
    let value_label_field_is_text = value_labels.is_some_and(|ml| ml.field_is_text(domain_rows));
    // (domain index, value, plotted value, text, measured width, series, series fill). A
    // multi-series chart labels every series in row order, each in its own color.
    let value_label_marks: Vec<(usize, f64, f64, String, f64, String, String)> = match value_labels {
        Some(ml) => {
            let mut keys: Vec<(usize, String)> = Vec::new();
            match color_field {
                None => keys.extend((0..x_domain.len()).map(|i| (i, String::new()))),
                Some(cf) => {
                    let mut seen = HashSet::new();
                    for row in rows {
                        let Some(i) = domain_position(&x_domain, row, x_field) else { continue };
                        let series = row_str(row, cf);
                        if seen.insert((i, series.clone())) {
                            keys.push((i, series));
                        }
                    }
                }
            }
            let mut marks = Vec::with_capacity(keys.len());
            for (i, series) in keys {
                let Some(v) = data.get(&(x_domain[i].clone(), series.clone())).copied().flatten() else {
                    continue;
                };
                let (text, _) = ml
                    .field_text(value_labels_house, value_label_field_is_text, label_rows.get(&(x_domain[i].clone(), series.clone())).copied(), v)
                    .map_err(|e| format!("line: {e}"))?;
                let width = fonts.measure_shaped(&text, &value_label_family, value_label_size)?;
                // A layered spec's text layer does not inherit the base's color channel.
                let fill = if is_multi && !has_layers {
                    series_fill(&series, &palette_domain, &r.series_palette, is_multi, category_scale)?
                } else {
                    String::new()
                };
                // A stacked area's text layer stacks with its marks, so a caption
                // sits at its segment's end.
                let at = if is_stacked {
                    let idx = baseline_order.iter().position(|s| *s == series).expect("series in baseline_order");
                    let col = &columns[i];
                    if is_normalize { col.bounds[idx].1 / col.total } else { offset_at(i) + col.bounds[idx].1 }
                } else {
                    v
                };
                marks.push((i, v, at, text, width, series, fill));
            }
            marks
        }
        _ => vec![],
    };

    // Every labeled layer's captions join the fit like the base's own: one
    // text mark per layer row, measured shaped. Per layer: its style and, per
    // row, (domain index, value, width, spot).
    let layer_label_fits: Vec<(usize, crate::families::layers::LayerLabelStyle, Vec<(usize, f64, f64, CaptionSpot)>)> = overlays
        .iter()
        .enumerate()
        .filter_map(|(layer, overlay)| match overlay {
            OverlayLayer::Line(l) => l.visible_labels().map(|ml| {
                (layer, ml, l.x.as_deref(), &l.y, l.label_is_house, l.query_name.as_deref(), layer_band_step && l.curve_name() == Some("step"))
            }),
            OverlayLayer::Scatter(sl) => sl.point_mark.labels.visible.then(|| {
                (layer, &sl.point_mark.labels, sl.x.as_deref(), &sl.y, sl.label_is_house, sl.query_name.as_deref(), false)
            }),
            OverlayLayer::Bar(_) => None,
        })
        .map(|(layer, ml, own_x, y, is_house, query, band_step)| {
            let rows = resolve_layer_rows(payload, query, r.query_name.as_deref(), "line")?;
            let style = crate::families::layers::LayerLabelStyle::new(ml, is_house, rows, payload.board_style.as_ref())?;
            let mut items = Vec::with_capacity(rows.len());
            for (row, v) in crate::common::valued_rows(rows.iter(), y) {
                let Some(i) = domain_position(&x_domain, row, own_x.unwrap_or(x_field)) else { continue };
                let band = band_step.then(|| (i == 0, i + 1 == x_domain.len()));
                let caption = style.caption(row, v, band).map_err(|e| format!("line: {e}"))?;
                items.push((i, v, style.width(fonts, &caption.text)?, caption.spot));
            }
            Ok((layer, style, items))
        })
        .collect::<Result<Vec<_>, String>>()?;
    // A bar layer's value labels join the fit too: per layer its rects (over the
    // layer's own rows) and each rect's index on the shared x domain.
    let bar_label_fits: Vec<(usize, &OverlayBarLayer, &[serde_json::Map<String, Value>], Vec<crate::families::layers::BarLayerMark>, Vec<usize>)> =
        overlays
            .iter()
            .enumerate()
            .filter_map(|(layer, overlay)| match overlay {
                OverlayLayer::Bar(bl) if bl.bar_mark.labels.as_ref().is_some_and(|ml| ml.visible) => Some((layer, bl.as_ref())),
                _ => None,
            })
            .map(|(layer, bl)| {
                let rows = resolve_layer_rows(payload, bl.query_name.as_deref(), r.query_name.as_deref(), "line")?;
                let layer_x = bl.x.as_deref().unwrap_or(x_field);
                let marks = placed_bar_layer_marks(bl, rows, layer_x, &nan_x, |row| x_domain.contains(&row_str(row, layer_x)));
                let slots = marks
                    .iter()
                    .map(|m| domain_position(&x_domain, m.row, layer_x).expect("placed marks sit on the domain"))
                    .collect();
                Ok((layer, bl, rows, marks, slots))
            })
            .collect::<Result<Vec<_>, String>>()?;
    // A band-step layer's plateaus reach its band edges, where its stroke adds ink
    // (`boundStroke`): (layer, domain index, value, stroke width, plateau share).
    let mut layer_step_marks: Vec<(usize, usize, f64, f64, f64)> = Vec::new();
    if layer_band_step {
        for (layer, overlay) in overlays.iter().enumerate() {
            let OverlayLayer::Line(l) = overlay else { continue };
            let Some((plateau, reach)) = l.band_step_ink()? else { continue };
            for (row, v) in crate::common::valued_rows(resolve_layer_rows(payload, l.query_name.as_deref(), r.query_name.as_deref(), "line")?.iter(), &l.y) {
                let Some(i) = domain_position(&x_domain, row, l.x.as_deref().unwrap_or(x_field)) else { continue };
                layer_step_marks.push((layer, i, v, reach, plateau));
            }
        }
    }
    // `value_labels_crowd_width.py` reads only `line_mark.labels` (an area's labels ride
    // its line), never the point-mark alias the painted labels may fall back to. An
    // endpoint-label rail wraps production's spec in a concat with no root encoding,
    // which that detector skips.
    if let (Some(ml), true) = (
        visible_labels(&r.style.line_mark.labels),
        r.wide_measures.is_empty()
            && (matches!(law.vl_type, VegaType::Nominal | VegaType::Ordinal) || x_is_authored_ordinal)
            && !show_rail,
    ) {
        let house = if draw_areas { r.style.label_is_house } else { r.style.line_label_is_house };
        let (family, size) = crate::common::crowd_label_font(ml.font.as_ref(), payload.board_style.as_ref())?;
        let label_field = ml.field.as_deref().unwrap_or(y_field);
        let mut widest = 0.0_f64;
        for row in &payload.rows {
            let Some(v) = row.get(label_field).and_then(serde_json::Value::as_f64) else { continue };
            let text = format_mark_label(ml.format.as_ref(), v, house).map_err(|e| format!("line: {e}"))?;
            widest = widest.max(fonts.measure(&text, &family, size)?);
        }
        crate::common::warn_value_labels_crowd(
            payload.chart_id()?,
            payload.width,
            distinct_values(&payload.rows, x_field),
            widest,
        );
    }

    // L7: production's `apply_title_overflow_to_spec` wraps a title or
    // subtitle wider than its own basis onto two measured lines, and Vega's
    // title group grows by `fontSize + 2` per extra line. Both Vega arms
    // reserve and draw that block; the legacy margin arm keeps
    // `emit_title`'s single unwrapped row.
    //
    // The basis is the padded card on the fit arm, but NOT on the rail
    // concat: production re-runs the whole pass (title and subtitle alike,
    // from the raw strings) after `_correct_concat_overshoot` against
    // pane 0's own footprint — `converters/chart.py`'s `target_width −
    // pane1_width − spacing − pad_l − pad_r`, which `title_overflow.py`
    // motivates as "the title spans pane[0]'s gutter + plot, not the card".
    // Pane 0 itself carries zero padding in the compiled spec, so
    // `compute_title_limit`'s `int()` truncation applies to that width
    // unchanged — the same `.trunc()` `wrap_title_block` already does.
    let title_wrap_w =
        if shape == Shape::RailConcat { avail_w - (rail_pane_w + el.label_offset) } else { avail_w };
    let new_title = |wrap_w: f64| {
        FrameTitle::new(
            r.title.as_deref(),
            r.subtitle.as_deref(),
            r.style.title_font.as_ref(),
            r.title_style.as_ref(),
            wrap_w,
            fonts,
        )
    };
    let title = new_title(title_wrap_w)?;
    // A rail concat defers its title wrap to the render pass, which records nothing.
    if !show_rail {
        title.warn_truncated(&r.id);
    }

    // production's `_correct_concat_overshoot` (`converters/chart.py`) runs
    // `apply_title_overflow_to_spec` TWICE on the rail concat, at two
    // DIFFERENT widths: once before its own probe, against pane 0's
    // pre-correction width — which literally equals `payload.width`
    // verbatim (`_wrap_hconcat_label_pane` hands pane 0 the whole target
    // width before any shrink) — and once after, against the corrected
    // `title_wrap_w` above (`title`, the one that paints). The
    // height branch of that SAME probe-and-shrink pass decides how much
    // the pane may shrink using ONLY the first (wide) wrap's chrome —
    // it runs before the second, narrower wrap ever exists. A subtitle
    // that fits one line at the wide pre-correction width but needs two
    // at the narrower post-correction one (`docs/laws-line-area.md`'s
    // rail-concat height section carries the probed pair: `charts/
    // extensibility_1`'s `slope` wraps to one line at 376px, two at
    // 286.87px — `bump`'s longer subtitle wraps to two at both, so it
    // never triggers this) adds a whole `fontSize + 2` line of REAL
    // chrome the shrink decision never saw, and the correction is
    // single-pass (`docs/laws-line-area.md`'s own citation) — it does
    // not re-run to notice. Two title blocks, never one:
    // `title` (this arm's real, narrow, PAINTED block) decides the
    // canvas's true chrome; `title_probe` (wide, probe-only, never
    // painted) decides how far the pane was allowed to shrink. Only a
    // `payload.measuring` render (`Payload::measuring`'s own doc) ever
    // reads it, so it's only ever built there — every other RailConcat
    // render (the real board paint, at an already-decided slot height)
    // leaves it `None` and pays nothing for it.
    let title_probe = if payload.measuring && shape == Shape::RailConcat {
        Some(new_title(svg_w)?)
    } else {
        None
    };

    // L8: authored `axis_x.labels.align` (VL `labelAlign`) overrides Vega's
    // flush-edge default for EVERY x label (`vl_field_maps.py`'s raw
    // passthrough to `labelAlign`, unconditional on axis type) — Vega arms
    // only; the legacy margin arm never reads `labels.align` (pre-port
    // status quo). A resolved payload only ever carries left/right/center
    // for a bottom axis (`_resolve_own_side_align` collapses inward/outward
    // to `None` with no left/right edge to resolve against), so any other
    // string is a payload invariant violation, not an authoring choice to
    // silently ignore. Not modeled on a non-temporal (point-scale) x axis —
    // no corpus case authors it there, and applying it would need its own
    // anchor/ink path through the non-temporal label loop — so that shape
    // refuses too rather than moving the margins for an alignment nothing
    // downstream draws.
    let x_label_align: Option<&'static str> = match ax_x.labels.align.as_deref() {
        None => None,
        Some("left") => Some("left"),
        Some("right") => Some("right"),
        Some("center") => Some("center"),
        Some(other) => {
            return Err(format!("line: unsupported axis_x.labels.align {other:?}").into())
        }
    };

    // The legend on a Vega arm is Vega's own group (`VegaLegend`): a right
    // one anchored at `ceil(x2) + 18` of the plot ∪ the y axis ∪ the line
    // stroke's ink (probe: the anchor follows the full stroke width, not
    // the point symbols), a left one at `floor(x1) - 18 - box_w` of the plot
    // ∪ the marks' ink, a bottom one at `ceil(y2) + 18` of the plot ∪ the x
    // axis's band, left-aligned with the plot. Its columns are
    // `legend::entry_columns` (the resolver bakes the wrap of a one-row
    // legend that cannot fit); the legacy margin arm keeps its measured
    // column.
    // `labelLimit`/`titleLimit`: the box bounds a wider text at the limit
    // while the drawn text is the truncated one.
    // Only an area's series recede: a line is too thin for a static recede to read.
    let selection = if draw_areas {
        crate::selected::Selection::of(
            payload,
            r.link.as_deref(),
            &r.link_color.as_deref().map(|color| ("color", color)).into_iter().collect::<Vec<_>>(),
        )?
    } else {
        crate::selected::Selection::default()
    };
    let series: Vec<(String, &str)> = legend_domain
        .iter()
        .map(|s| (display_label(s), crate::common::wide_link_key(s, !r.wide_measures.is_empty(), r.series_from_measures)))
        .collect();
    let series_keys = crate::selected::series_keys(&legend_entries, &series);
    let legend_entries = selection.legend(legend_entries, &series_keys);
    let limited_text = legend_active
        .then(|| limit_legend_text(legend_entries.iter().map(|e| e.label), &legend_title, &r.legend, fonts))
        .transpose()?;
    let vega_legend_box = limited_text
        .as_ref()
        .map(|lt| {
            let symbols: Vec<Option<LegendSymbol>> = legend_entries.iter().map(|e| e.symbol).collect();
            legend_from_widths(&lt.label_ws, &symbols, numeric_legends.unlisted_line, &lt.title, lt.title_w, &r.legend)
                .and_then(|lg| numeric_legends.stack_onto(lg, &r.legend, fonts, (payload.width, payload.height)))
        })
        .transpose()?;
    let legend_entries: Vec<LegendEntry> = match &limited_text {
        Some(lt) => legend_entries
            .iter()
            .zip(&lt.labels)
            .map(|(e, drawn)| LegendEntry {
                label: drawn,
                fill: e.fill,
                symbol: e.symbol,
                series_key: None,
                recede: e.recede,
            })
            .collect(),
        None => legend_entries,
    };
    // That root legend is Vega's own default-styled one, listing the dash patterns in the stroke
    // halo's canvas color (the strokeDash scale is encoded on the halo and the line alike).
    let root_dash_legend: Option<(VegaLegend, LegendConfig, Vec<String>)> = if dash_legend_at_root {
        let cfg = vega_default_legend(&r.legend);
        let lt = limit_legend_text(legend_display_labels.iter().map(String::as_str), &legend_title, &cfg, fonts)?;
        let symbols: Vec<Option<LegendSymbol>> =
            series_dashes.iter().map(|dash| Some(LegendSymbol::DashCircle(dash.as_str()))).collect();
        Some((legend_from_widths(&lt.label_ws, &symbols, false, &lt.title, lt.title_w, &cfg)?, cfg, lt.labels))
    } else {
        None
    };
    let facet_legend = if facet_legend_wanted {
        let lt = limit_legend_text(legend_entries.iter().map(|e| e.label), &legend_title, &r.legend, fonts)?;
        // Under a columns facet with shared scales Vega-Lite drops the line's own symbol stroke width.
        let default_circle = if !has_layers && !dashed && r.multiples.as_ref().is_some_and(|m| m.columns.is_some() && m.scale != "independent") && fam == "line" {
            LegendSymbol::ThinCircle
        } else {
            LegendSymbol::Circle
        };
        let symbols: Vec<Option<LegendSymbol>> = legend_entries.iter().map(|e| e.symbol.or(Some(default_circle))).collect();
        let lg = legend_from_widths(&lt.label_ws, &symbols, numeric_legends.unlisted_line, &lt.title, lt.title_w, &r.legend)?;
        let lg = numeric_legends.stack_onto(lg, &r.legend, fonts, (payload.width, payload.height))?;
        let drawn: Vec<LegendEntry> = legend_entries
            .iter()
            .zip(&lt.labels)
            .map(|(e, label)| LegendEntry { label, fill: e.fill, symbol: e.symbol, series_key: None, recede: e.recede })
            .collect();
        let mut nodes: Vec<Node> = drawn_symbol_legend(&drawn, &lg, &r.legend, default_circle, 0.0, 0.0, false).into_iter().collect();
        nodes.extend(numeric_legends.nodes_after(&lg, &r.legend, fonts, (payload.width, payload.height), (0.0, 0.0))?);
        Some(facet_compose::FacetLegend { nodes, box_w: lg.box_w, box_h: lg.box_h() })
    } else {
        None
    };

    // Production's temporal x is a continuous UTC scale — positions are time
    // fractions of the bucket-timestamp span (Feb 1 sits at 31/151 of a
    // Jan-Jun domain, not 1/5). Day-number fractions reproduce vega's ms
    // fractions exactly for the date-grained buckets TimeUnit models.
    //
    // L9: on `utcyearweek` the scale domain and every mark's own position
    // floor to the Sunday week start (d3 `utcWeek.floor`, Vega-Lite's
    // `timeUnit` transform on the encoding field) — `x_time_ext.pos`
    // drives vertex/gridline positions. Production's axis `values` stay the
    // RAW (unfloored) dates (`label_opener_values`), positioned against
    // that SAME floored extent — `label_extent` below, consumed by
    // `label_x_at`. Every other grain floors to itself, so `label_extent`
    // and `utc_fracs` agree everywhere else.
    // Every DATE-MATH site reads the instants parsed once (`x_time`); the raw
    // `x_domain` stays the join key for `data`/layer-row lookups, the
    // support-table strip and legend identity, and `x_time.iso` the date
    // text a labeled bucket or year reads as.
    // A bar layer spans its time bucket, so a continuous temporal x scale runs on to
    // the end of the last bucket a bar occupies when that passes every other datum
    // (VL's `timeUnit` `_end` field joins the domain).
    let bucketed_bars = detected_data_tu.is_some() && has_bar_layer && !x_is_band;
    let bucketed_bar_end: Option<(f64, String)> = match (detected_data_tu, &x_time) {
        (Some(_), Some(time)) if bucketed_bars => {
            let mut last_end = None;
            for overlay in overlays {
                let OverlayLayer::Bar(bl) = overlay else { continue };
                let layer_x = bl.x.as_deref().unwrap_or(x_field);
                for row in resolve_layer_rows(payload, bl.query_name.as_deref(), r.query_name.as_deref(), "line")? {
                    let Some(Value::String(date)) = row.get(layer_x) else { continue };
                    if let Some(end) = x.time.get(date).and_then(|instant| instant.end) {
                        last_end = Some(last_end.map_or(end, |last: f64| last.max(end)));
                    }
                }
            }
            let last_datum = time.pos.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            last_end.filter(|end| *end > last_datum).map(|end| (end, crate::families::temporal::instant_iso(end)))
        }
        _ => None,
    };
    let x_time_ext: Option<TimeColumns> = x_time.as_ref().map(|time| match &bucketed_bar_end {
        Some((end, iso)) => time.extended(*end, iso.clone()),
        None => time.clone(),
    });
    // A grain over values no date parses from has no bucket to floor them to.
    if let (Some(_), None, Some(value)) = (detected_data_tu, &x_time, &x.unparsed) {
        return Err(format!("line: unparseable temporal x value {value:?}").into());
    }
    let x_domain_for_temporal: &[String] = x_time_ext.as_ref().map_or(x_domain.as_slice(), |time| time.iso.as_slice());
    let color_title = match color_field {
        Some(cf) => description_color_title(fam, cf, &r.legend, &r.field_labels)?,
        None => String::new(),
    };
    let strip_aria = support_table_strip::CellAria {
        x: if support_table_config.is_some() {
            x_domain_for_temporal
                .iter()
                .map(|x| {
                    let text = match detected_data_tu {
                        Some(tu) => crate::families::temporal::default_time_unit_text(x, Some(tu))
                            .map_err(|e| format!("line: support_table cell description: {e}"))?,
                        None => x.clone(),
                    };
                    Ok(format!("{x_title_text}: {text}"))
                })
                .collect::<Result<_, String>>()?
        } else {
            Vec::new()
        },
        color_title: color_field.map(|_| color_title.clone()),
    };
    let floor_to_grain = |domain: &[String]| -> Result<Vec<String>, String> {
        match detected_data_tu {
            Some(data_tu) => domain
                .iter()
                .map(|d| {
                    let floored = time_unit_floor(d, data_tu)
                        .ok_or_else(|| format!("line: unparseable temporal x value {d:?}"))?;
                    Ok(floored)
                })
                .collect(),
            _ => Ok(domain.to_vec()),
        }
    };
    // A tooltip heads the row's own date, not the instant a part maps it to.
    let header_dates: &[String] = if x_part.is_some() { &x_domain } else { x_domain_for_temporal };
    // The buckets the Vega arms' labels name. Production enumerates the whole
    // calendar span to find the label openers (`label_cadence_values`,
    // `type_inference.py:625-639`), so a bucket no row fills can open a label
    // period; `TemporalLabel.domain_idx` indexes this list, not the rows.
    // An ordinal calendar axis (a band x over a bucketed grain) labels the buckets
    // its domain holds, ascending: the domain is already filled, at the grain the
    // fill used, which need not be the detected one.
    let x_ordinal_calendar = x_is_band && detected_data_tu.is_some();
    let x_expr: Option<crate::labels_expr::Expr> = r
        .style
        .axis_x
        .labels
        .expr
        .as_deref()
        .map(|src| crate::labels_expr::Expr::parse(src).map(|e| e.on("x")).map_err(|e| format!("line: labels.expr {src:?}: {e:?}")))
        .transpose()?;
    // Production draws the native ticks of an axis that speaks an authored expression, not the cadence's.
    if x_expr.is_some() && x_tick_cadence.is_some() {
        return Err(crate::refuse!("{fam}: an authored x tick cadence beside an authored axis_x.labels.expr is not supported").into());
    }
    // The text a nominal x label paints: `x_domain` stays the raw lookup key.
    let x_shown: Vec<String> = match &x_expr {
        Some(expr) if detected_data_tu.is_none() && !bare_temporal => {
            x_domain.iter().enumerate().map(|(i, c)| expr.rewrite_band(c, i, x_domain.len()).map_err(|e| format!("line: {e}"))).collect::<Result<_, _>>()?
        }
        // A one-instant date domain ticks that instant under d3's multi-scale format.
        _ if x_is_temporal_candidate && !x_is_band && detected_data_tu.is_none() && x_domain.len() == 1 => {
            match &x_time {
                Some(time) => vec![crate::families::temporal::multi_format_at(time.at[0]).map_err(|e| format!("{fam}: {e}"))?],
                None => x_domain.clone(),
            }
        }
        _ => x_domain.clone(),
    };
    let x_shown: Vec<String> = x_shown.iter().map(|text| ax_x.labels.cased(text)).collect::<Result<_, _>>()?;
    // A date axis speaks the expression at each tick's instant; one that throws
    // there fails Vega's whole view, whichever dates the ticks land on.
    if let Some(expr) = x_expr.as_ref().filter(|_| detected_data_tu.is_some() || bare_temporal) {
        let thrown = crate::families::temporal::first_throw_at_dates(
            expr,
            x_domain_for_temporal,
            // The day of the text beside it: on a part axis that text is the cycle instant.
            &{
                let time = x_time_ext.as_ref().ok_or_else(|| format!("x value {:?} is not an ISO date", x_domain.first()))?;
                let days = if x_part.is_some() { &time.pos } else { &time.at };
                days.iter().map(|day| day.floor()).collect::<Vec<_>>()
            },
            x_ordinal_calendar,
            |day| crate::families::temporal::datum_label(detected_data_tu, day),
        )
        .map_err(|e| format!("line: {e}"))?;
        if let Some(throw) = thrown {
            return Err(format!("line: {throw}").into());
        }
    }
    let speak_bare = |ticks: Vec<BareTick>| -> Result<Vec<BareTick>, String> {
        ax_x.labels.cased_ticks(match &x_expr {
            None => ticks,
            Some(expr) => crate::families::temporal::speak_bare(ticks, expr)?,
        })
    };
    // A NaN row stays out of the scale domain, its marks sit at x 0 and its line or area is
    // undefined there.
    let x_nan: Vec<bool> = x_time_ext.as_ref().map_or_else(|| vec![false; x_domain_for_temporal.len()], |time| time.nan.clone());
    let finite_of = |domain: &[String]| -> Vec<String> {
        domain.iter().zip(&x_nan).filter(|&(_, &nan)| !nan).map(|(date, _)| date.clone()).collect()
    };
    let label_domain: Vec<String> = match detected_data_tu.or(bare_layout_tu) {
        Some(_) if x_ordinal_calendar => {
            let time = x_time_ext.as_ref().expect("an ordinal calendar axis reads parsed instants");
            let mut order: Vec<usize> = (0..time.pos.len()).collect();
            order.sort_by(|&a, &b| time.pos[a].total_cmp(&time.pos[b]));
            order.into_iter().map(|i| time.iso[i].clone()).collect()
        }
        Some(data_tu) => {
            let widened: Vec<String> = finite_of(x_domain_for_temporal)
                .into_iter()
                .chain(crate::annotation::x_dates_outside(&x_time_ext.as_ref().map_or_else(Vec::new, |time| time.at.clone()), &r.annotations))
                .collect();
            complete_buckets(&widened, data_tu, ax_x.fiscal_year_start_month)
        }
        _ => x_domain_for_temporal.to_vec(),
    };
    let speak_labels = |tls: Vec<TemporalLabel>| -> Result<Vec<TemporalLabel>, String> {
        ax_x.labels.cased_labels(match &x_expr {
            None => tls,
            Some(expr) => crate::families::temporal::speak_labels(tls, &label_domain, expr, detected_data_tu, x_ordinal_calendar)?,
        })
    };
    let label_domain_floored: Vec<String> = floor_to_grain(&label_domain)?;
    // An ordinal calendar axis positions its labels on the band or point scale
    // rather than a UTC scale: a label sits where its own bucket sits in the
    // (possibly sorted) domain.
    let label_slots: Vec<usize> = if x_ordinal_calendar {
        label_domain
            .iter()
            .map(|bucket| x_domain_for_temporal.iter().position(|d| d == bucket).expect("a label names a bucket of the domain"))
            .collect()
    } else {
        (0..label_domain.len()).collect()
    };
    let x_is_continuous_date = (detected_data_tu.is_some() || bare_temporal) && !x_ordinal_calendar;
    // `{{ x }}` of a date: a real date on a time scale prints `%Y-%m-%d`; one Vega keeps a string
    // (an ordinal band) is an invalid date.
    let link = r.link.as_deref().map(|template| crate::href::DatumLink {
        template,
        open: r.link_open,
        x: crate::href::LinkField {
            name: x_field,
            text: match ((x_is_continuous_date && !(is_band_step && x_is_standard_date)) || r.link_x_labeled, x_is_standard_date) {
                (true, _) => crate::href::LinkText::Date,
                (false, true) => crate::href::LinkText::InvalidDate,
                (false, false) => crate::href::LinkText::Raw,
            },
        },
        y: Some(crate::href::LinkField::raw(&r.link_y)),
        color: r.link_color.as_deref().map(crate::href::LinkField::raw),
        context: r.link_context.as_ref(),
    });
    // A layered base that carries its own data (labeled buckets rewritten, the gap-fill pass, a band
    // step) loses its links (`_disable_private_data_channel`); a stack's hoisted order clears them.
    let base_has_own_data = x.rows_rewritten
        || crate::families::x_type::XAxis::of(&r.style.axis_x).rewrites_rows(&law);
    let base_linked = !has_layers || (!is_band_step && (is_stacked || !base_has_own_data));
    let all_hrefs: HashMap<(String, String), String> = match &link {
        Some(link) => label_rows.iter().map(|(key, row)| Ok((key.clone(), link.href(row)?))).collect::<Result<_, String>>()?,
        None => HashMap::new(),
    };
    let strip_links = crate::href::first_datum_links(link.as_ref(), rows, x_field)?;
    let no_hrefs = HashMap::new();
    let hrefs = if base_linked { &all_hrefs } else { &no_hrefs };
    // A series' marks that draw a datum read its own; a path reads its first.
    let href_at = |i: usize, data_key: &str| hrefs.get(&(x_domain[i].clone(), data_key.to_string())).map(String::as_str);
    let data_key_of = |paint_key: &str| if is_multi { paint_key.to_string() } else { String::new() };
    let utc_fracs: Option<Vec<f64>> = if (detected_data_tu.is_some() || bare_temporal) && !x_ordinal_calendar {
        let time = x_time_ext.as_ref().expect("a time scale reads parsed instants");
        match time.extent() {
            Some(extent) => Some(time.pos.iter().zip(&x_nan).map(|(&pos, &nan)| if nan { 0.0 } else { frac_of(pos, extent) }).collect()),
            // d3 centers a degenerate domain; a domain with no finite date maps nothing.
            None if x_nan.contains(&true) => Some(x_nan.iter().map(|&nan| if nan { 0.0 } else { 0.5 }).collect()),
            None => None,
        }
    } else if let (Some(lin), Some(domain)) = (&quant_x, quant_extent) {
        Some(x_domain.iter().map(|v| lin.frac(domain, v.parse().expect("a quantitative x domain is numeric"))).collect())
    } else if x_is_temporal_candidate && !x_is_band && detected_data_tu.is_none() && x_domain.len() == 1 {
        // d3 centers a one-instant domain.
        Some(vec![0.5])
    } else {
        None
    };
    // An authored scale domain is the extent the continuous axis spans.
    let authored_extent = crate::families::scatter_axis::authored_day_domain(ax_x, fam)?;
    // Annotation dates widen the extent as data does; the points stay the data's own.
    let label_extent: Option<(f64, f64)> = if (detected_data_tu.is_some() || bare_temporal) && !x_ordinal_calendar {
        x_time_ext
            .as_ref()
            .expect("a time scale reads parsed instants")
            .extent()
            .map(|data| authored_extent.unwrap_or_else(|| crate::annotation::widen_extent(data, &r.annotations)))
    } else {
        None
    };
    let bare_extent = label_extent.or(quant_extent);
    let utc_fracs: Option<Vec<f64>> = match (utc_fracs, label_extent, authored_extent) {
        (Some(_), Some(extent), _) if authored_extent.is_some() || crate::annotation::x_day_bounds(&r.annotations).next().is_some() => {
            let time = x_time_ext.as_ref().expect("a time scale reads parsed instants");
            Some(time.pos.iter().zip(&x_nan).map(|(&pos, &nan)| if nan { 0.0 } else { frac_of(pos, extent) }).collect())
        }
        (fracs, ..) => fracs,
    };
    // NaN rows leave the label domain a subset of the rows, so a label's index names a finite
    // date, not a row.
    let labels_off_rows = x_nan.contains(&true);
    // The Vega arms' L1-L5 x labels. They depend on the slot width, never on
    // the fitted plot, so one resolution serves every fit candidate; the rail
    // arm's slot is the rail-reduced one (`_endpoint_rail.py:148-170`).
    let edge_labels_flushed = !x_ordinal_calendar && ax_x.labels.flush.as_ref() != Some(&serde_json::Value::Bool(false));
    let x_slot_w = if shape == Shape::RailConcat {
        svg_w - (rail_pane_w + el.label_offset)
    } else if let Some(f) = facet {
        // Production judges crowding at the panel width its spec gave
        // each panel, before the composite's width correction.
        let (_, cols) = multiples::grid_counts(r.multiples.as_ref(), &r.panel_axes);
        multiples::facet_panel_width(payload.width, cols, f.mirror_band_x, 0.0)
    } else {
        svg_w
    };
    let vega_x_labels: Option<ResolvedVegaLabels> = match detected_data_tu.or(bare_layout_tu) {
        Some(data_tu) => {
            let label_usable_ratio = label_usable_ratio_opt.ok_or(
                "line: label_usable_ratio absent (theme required for a temporal x axis)",
            )?;
            let slot_w = x_slot_w;
            let mut measure = |t: &str| ax_x.labels.measure(fonts, t, x_font_size);
            Some(resolve_vega_x_labels(
                &label_domain,
                data_tu,
                ax_x.labels.time_unit.as_deref(),
                slot_w * label_usable_ratio,
                edge_labels_flushed,
                false,
                !x_ordinal_calendar, // continuous_temporal
                ax_x.fiscal_year_start_month,
                x_overlap_skip,
                x_overlap_tilt,
                x_tilt_increments,
                x_font_size,
                authored_time_format,
                ax_x.labels.angle,
                &mut measure,
            )?)
        }
        _ => None,
    };
    // A time-part axis crowds the part's own band texts (`_generic_temporal_labels`).
    let part_layout: Option<crate::families::temporal::GenericLayout> = match x_part {
        Some(part) if bare_temporal => {
            let label_usable_ratio = label_usable_ratio_opt.ok_or("line: label_usable_ratio absent (theme required for a temporal x axis)")?;
            let texts = crate::families::temporal::part_band_texts(&x_domain, part, ax_x.labels.time_unit.as_deref(), authored_time_format)
                .map_err(|e| format!("{fam}: {e}"))?;
            let gap = ax_x.labels.font.measure_weighted(fonts, " ", x_font_size)?;
            let widths = texts
                .iter()
                .map(|t| ax_x.labels.measure(fonts, t, x_font_size).map(|w| w + gap))
                .collect::<Result<Vec<_>, _>>()?;
            Some(crate::families::temporal::generic_layout(
                &widths,
                x_slot_w * label_usable_ratio,
                x_overlap_skip,
                x_overlap_tilt,
                x_tilt_increments,
                x_font_size,
            )?)
        }
        _ => None,
    };
    if let Some(label_count) = vega_x_labels.as_ref().and_then(|l| l.collision).or_else(|| part_layout.as_ref().and_then(|l| l.collision)) {
        crate::families::cartesian::warn_axis_label_collision(&r.id, x_field, label_count);
    }
    // The gap between the plot bottom and a bottom strip's first row.
    let strip_axis_offset = match r.effective_support_table_style.as_ref() {
        Some(st) if st.position == "bottom" && composed_strip.is_some() => {
            let baked = r
                .support_table_axis_offset
                .ok_or("line: support_table position=bottom requires support_table_axis_offset")?;
            let labels = &r.style.axis_x.labels;
            let font_size = labels.font.size.ok_or("line: axis_x.labels.font.size absent (theme required)")?;
            let block = match (ax_x.labels.angle, detected_data_tu) {
                // No resolved grain: production measures the datum text bare (a raw
                // quantitative axis keeps the flat row).
                (Some(angle), None) => {
                    let quantitative = crate::families::temporal::infer_vega_type(rows, x_field)
                        == crate::families::temporal::VegaType::Quantitative;
                    if quantitative {
                        font_size
                    } else {
                        let mut widest = 0.0_f64;
                        for value in &x_shown {
                            widest = widest.max(labels.measure(fonts, value, font_size)?);
                        }
                        vega_fit::label_block_height(widest, font_size, angle)
                    }
                }
                _ => vega_x_labels.as_ref().map_or(font_size, |l| l.block_height),
            };
            baked + support_table_strip::tilted_axis_gap_growth(st, labels, font_size, block)
        }
        _ => 0.0,
    };
    // Production's default cadence: with no authored `ticks.time_unit` or
    // `ticks.count` and no injected `values` (no coarser label grain, no
    // visibility thinning), `tickCount: {interval, step}` comes from the
    // label grain (`type_inference.py:1049-1052`), so Vega ticks every
    // calendar boundary of it — an empty bucket included.
    let labels_raw = ax_x.labels.time_unit.as_deref().is_some_and(|s| s == "none" || TimePart::parse(s).is_some());
    let native_candidate: Option<UtcTickInterval> =
        match (detected_data_tu, vega_x_labels.as_ref(), label_extent) {
            (Some(data_tu), Some(res), Some(extent))
                if x_tick_cadence.is_none()
                    && res.visibility_tu == data_tu
                    && res.year_step == 1
                    && res.format_tu == data_tu =>
            {
                let interval = native_tick_interval(data_tu);
                // The rail arm's x ink is closed form over the rows: it paints
                // no tick no row sits on, so it keeps the rows' axis where the
                // two agree.
                if shape != Shape::RailConcat {
                    Some(interval)
                } else {
                    let why = "on the endpoint-rail arm, whose x ink is closed form over the rows";
                    let mut row_days: Vec<i64> = x_time_ext
                        .as_ref()
                        .expect("a time scale reads parsed instants")
                        .pos
                        .iter()
                        .map(|&pos| pos as i64)
                        .collect();
                    row_days.sort_unstable();
                    if interval_tick_days((extent.0 as i64, extent.1 as i64), interval) != row_days {
                        return Err(crate::refuse!(
                            "{fam}: a bucketed temporal x axis with a bucket no row fills is not supported {why}: Vega ticks the empty bucket (type_inference.py:1049-1052)"
                        ).into());
                    }
                    None
                }
            }
            _ => None,
        };
    // The interval's ticks on a bucketed axis, in day numbers and as
    // fractions of the label extent. The bare axis takes the cadence inside
    // `bare_temporal_ticks` instead.
    // The bare axis's ticks over `domain` at plot width `pw`: the part's own on a part axis.
    let dropped_grain_ladder: Option<&ResolvedVegaLabels> = vega_x_labels.as_ref().filter(|_| {
        bare_temporal
            && x_part.is_none()
            && crate::families::temporal::keeps_dropped_grain_ladder(ax_x, law.fill_grain, x_tick_cadence, authored_time_format.is_some(), subday_plan.is_some(), authored_extent.is_some())
    });
    let bare_ticks = |domain: (f64, f64), pw: f64| -> Result<Vec<BareTick>, String> {
        if let Some(lin) = &quant_x {
            let (fracs, labels, _, scale) = lin.at(pw)?;
            return Ok(fracs
                .into_iter()
                .zip(labels)
                .map(|(frac, text)| BareTick { day: scale.0 + frac * (scale.1 - scale.0), frac, text, row2: None })
                .collect());
        }
        match x_part {
            Some(part) => crate::families::temporal::part_temporal_ticks(
                domain,
                pw,
                part,
                ax_x.ticks.count.map(authored_tick_count),
                authored_time_format,
            ),
            None => match dropped_grain_ladder {
                Some(resolved) => crate::families::temporal::ladder_bare_ticks(domain, &label_domain, resolved),
                None => bare_temporal_ticks(domain, pw, x_tick_cadence, authored_time_format, subday_plan.as_ref()),
            },
        }
    };
    // What the bare axis's labels do: the part's crowding verdict, else the grain's.
    let bare_layout = || -> (f64, bool) {
        match (&part_layout, ax_x.labels.angle) {
            (Some(_), Some(angle)) => (angle, ax_x.labels.overlap.is_none()),
            (Some(layout), None) => (layout.angle, layout.parity || layout.collision.is_some() && x_overlap_skip),
            (None, _) => bare_label_layout(vega_x_labels.as_ref(), x_overlap_skip, &ax_x.labels),
        }
    };
    // An authored `labels.values` blanks the label of every tick outside the set
    // (`label_values_filter_expr`); the tick itself stays.
    let listed_days: Option<Vec<f64>> = ax_x.labels.values.as_ref().map(|values| {
        values.iter().filter_map(|v| v.as_str().and_then(crate::families::temporal::iso_format_day)).collect()
    });
    let is_listed = |day: f64| listed_days.as_ref().is_none_or(|days| days.iter().any(|d| (d - day).abs() < 1e-9));
    let x_tick_days: Option<Vec<f64>> = if continuous_axis {
        None
    } else {
        match (x_tick_cadence, label_extent) {
            (Some(cadence), Some(extent)) => {
                let grain = detected_data_tu.expect("a bucketed cadence needs the detected grain");
                Some(cadence.bucketed_ticks(grain, extent))
            }
            (Some(_), None) => {
                return Err(format!("{fam}: axis_x.ticks.time_unit needs a parseable UTC x domain").into());
            }
            (None, extent) => native_candidate
                .zip(extent)
                .map(|(interval, extent)| interval_tick_days((extent.0 as i64, extent.1 as i64), interval).into_iter().map(|day| day as f64).collect()),
        }
    };
    let x_tick_fracs: Option<Vec<f64>> = x_tick_days.as_ref().zip(label_extent).map(|(days, e)| {
        days.iter().map(|&day| (day - e.0) / (e.1 - e.0)).collect()
    });
    // L9 gate: production only positions openers at their RAW dates when it
    // emits an explicit opener `values` array. An authored `labels.time_unit`
    // override (`label_tick_cadence`, type_inference.py:659) is one path
    // there, but the corpus's own weekly cases author none — the branch
    // that actually fires for them is the render-local ladder thinning in
    // the temporal `elif` (type_inference.py:852-882, condition at
    // 870-871): `visibility_time_unit != time_unit`, i.e. the resolved
    // LABEL grain (visibility_tu) is coarser than the encoding grain
    // (data_tu), e.g. line_signups' monthly openers over weekly data. When
    // the label grain equals the data grain (every bucket gets a label — a
    // plain `tickCount` interval, no authored `values`), Vega's own ticks
    // sit at the scale's native (floored) positions, matching the vertices
    // exactly — only the label
    // TEXT (`build_temporal_labels`'s format_tu row) shifts by a day, never
    // the position (`time-unit-label-cadence-matrix/week_auto_native`: the
    // first tick sits at x=0, flush, with the day text "1" over a floored
    // Dec-31 vertex).
    let opener_frac_at = |i: usize, raw: bool| -> f64 {
        match label_extent {
            Some(extent) => {
                let date = if raw { &label_domain[i] } else { &label_domain_floored[i] };
                crate::families::temporal::utc_fraction_at(date, extent).unwrap()
            }
            None => match &utc_fracs {
                // A domain left with one finite date centers its labels (d3's degenerate domain).
                Some(_) if x_nan.contains(&true) => 0.5,
                Some(fr) => fr[i],
                None => f64::NAN,
            },
        }
    };
    // A raw opener whose own date falls outside the floored domain (every
    // row shares one weekday, so the LAST row's raw date always outruns a
    // Sunday-floored domain end unless that weekday is itself Sunday) gets
    // no tick/label — Vega drops any axis label whose scale position lands
    // outside the range, and this is production's actual behavior here,
    // not an approximation: `label_opener_values` selects openers by
    // calendar rule alone, with no domain-clamping of its own. A no-op
    // under the native (non-raw) gate: a floored opener's position IS a
    // vertex position, always inside [0, 1].
    // The position of the `i`-th LABEL: a tick's, when the labels came from an
    // authored interval's ticks (`interval_tick_labels` indexes them), else
    // the opener row's. Data-row consumers keep `opener_frac_at`.
    let label_frac_at = |i: usize, raw: bool| -> f64 {
        match &x_tick_fracs {
            Some(fracs) => fracs[i],
            None => opener_frac_at(i, raw),
        }
    };
    let drop_off_domain_openers = |labels: Vec<TemporalLabel>, raw: bool| -> Vec<TemporalLabel> {
        if label_extent.is_none() {
            return labels;
        }
        labels
            .into_iter()
            .filter(|tl| (0.0..=1.0).contains(&label_frac_at(tl.domain_idx, raw)))
            .collect()
    };
    // L9: Vega's `labelFlush` keys on the label's own scale POSITION being
    // exactly at a range end, not on its domain index — on a floored weekly
    // domain with RAW opener dates, the raw first opener sits off the range
    // start, so it centers instead of flushing left.
    let temporal_edge_anchor = |idx: usize, raw: bool, pw: f64| -> &'static str {
        // A band axis flushes no edge label (`labelFlush` is a continuous-axis rule).
        if x_ordinal_calendar {
            return "middle";
        }
        flush_anchor_px(label_frac_at(idx, raw) * pw, pw)
    };


    // Flip-aligned right value gutter: production bakes the measured
    // labelPadding from the DRAWN ladder (`axis.drawn_tick_values`, the
    // baked rungs inside the domain — a rung past the domain max is never
    // drawn and never sizes the gutter) with the Inter stand-in — same
    // contract as bar.rs's val_gutter.
    let gutter_of = |drawn: &[f64]| -> Result<f64, String> {
        let mf = measure::stand_in_family(y_fam);
        let mut max_w = 0.0_f64;
        for (i, &tv) in drawn.iter().enumerate() {
            let lbl = ax_y.labels.apply_expr("y", tv, format_y_label(tv, ruler, tick_label, labels_format, i, drawn.len())?, i, drawn.len())?;
            max_w = max_w.max(fonts.measure(&lbl, mf, y_font_sz)?);
        }
        Ok(max_w.min(y_limit) + VALUE_AXIS_BREATHING_ROOM)
    };
    // Production's `estimated_quantitative_tick_labels` cover ladder
    // (`_measured_label_padding.py`, called from `vl_field_maps.py:340-
    // 387`): whenever the axis's tick content is UNBAKED — no
    // `tick_values`, the gate production actually applies (`if axis.
    // tick_values: baked else: estimate`, no `ticks.count` term at all) —
    // production cannot measure the ticks it will actually draw (Vega-Lite
    // picks its own "nice" count), so it bounds the widest label it could
    // PLAUSIBLY draw instead: the candidate VALUES are the AUTHORED
    // `scale.continuous.domain` bounds when one is set ("raw data values
    // outside that range can't produce ticks there") else the data's own
    // extent, run through `nice_tick_values` at `_ESTIMATE_TARGET_COUNTS`
    // `(3,4,5,6,8,10,12)`, unioned with those same edges and zero (a
    // zero-anchored scale is common and cheap to cover). A log axis (ALWAYS
    // unbaked: `_resolve_cartesian_ticks` returns `((), None)`
    // unconditionally for it) takes d3's log candidates over the raw
    // extent instead (`common::estimated_tick_label_gutter`); this arm's
    // own unbaked linear case takes the nice ladder above.
    //
    // The FORMATTER differs from the baked path too:
    // `estimated_quantitative_tick_labels(values, fmt, tick_label, continuous)`
    // applies the SAME per-tick si_format/scientific_format
    // guard `sub_unit_guarded_format` ports below (see `estimated_gutter_of`) —
    // but never a `ruler`'s compaction or an anchor-index currency prefix;
    // only its BAKED sibling `quantitative_tick_labels` takes those.
    // The measured format is `tick_label.format` when set, else `labels.format` —
    // `gutter_of`'s ruler/tick_label-aware `format_y_label` measures the
    // port's own DRAWN ladder correctly, but would apply a compacting ruler
    // or an anchor-index currency prefix to an unordered cover candidate no
    // production label ever carries.
    // Read the RESOLVED format here, never the `is_normalize`-pinned
    // `labels_format` local above: production's own gutter estimate runs
    // BEFORE the percent pin — `measure_axis_to_vl` computes `labelPadding`
    // from the resolved `tick_label`/`labels.format` (`vl_field_maps.py:
    // 348-417`), and `pin_normalize_axis_format` only overrides the format
    // Vega actually PAINTS with, afterward (`emitters/area.py:303` measures
    // first, `:310-311` pins after). A normalize-stacked axis CAN carry a
    // baked `tick_label` (the unauthored SI default, e.g. `.3~s`, whenever
    // the author writes no `axis_y.labels.format` of their own) — production
    // reserves the gutter for THAT ladder's widest label, then draws the
    // narrower `%` labels into it.
    // The per-tick `si_format`/`scientific_format` guard (set only for a
    // ladder-less axis) and the affix both live in
    // `common::estimated_tick_label_gutter`, shared with `bar.rs`: measuring
    // the bare spec alone would OVER-measure every candidate >= 1 (the axis
    // paints "1.5M"; the spec alone reads "1500000").
    // Production sizes it once over the whole dataset, so an independent
    // facet scale's panels all reserve the same gutter.
    let estimated_gutter = || -> Result<f64, String> {
        let extent = |fold: fn(f64, f64) -> f64, init: f64| {
            domain_rows.iter().filter_map(|row| row_num(row, y_field)).fold(init, fold)
        };
        crate::common::estimated_tick_label_gutter(
            fonts,
            y_fam,
            y_font_sz,
            y_limit,
            tick_label,
            ax_y.labels.format.as_ref(),
            ax_y,
            if independent_scale { extent(f64::min, f64::INFINITY) } else { data_min },
            if independent_scale { extent(f64::max, f64::NEG_INFINITY) } else { data_max },
        )
    };
    let y_ticks_unbaked = ax_y.tick_values.is_empty();
    // A left axis authored `labels.align: right` is its ghost's own edge: the ghost copies the align, and
    // `mirror_axis.py` re-measures its `labelPadding` over the labels, so they end-anchor a gutter past the tick.
    let ghost_aligned = mirror_active && !y_on_right && ax_y.labels.align.as_deref() == Some("right");
    let y_gutter = if y_on_right || ghost_aligned {
        if is_log || y_ticks_unbaked {
            estimated_gutter()?
        } else {
            gutter_of(&crate::common::drawn_tick_values(ax_y))?
        }
    } else {
        0.0
    };
    // Visible y tick marks sit between the plot edge and the label gutter
    // (ac3's scenegraph: label x = tickSize 6 + labelPadding 56).
    let y_tick_eff = if ax_y.ticks.visible.unwrap_or(true) {
        ax_y.ticks.length.unwrap_or(6.0)
    } else {
        0.0
    };
    // Each layer's own axis and zero rule, once any layer pins a side.
    let overlay_rows = overlays
        .iter()
        .map(|overlay| resolve_layer_rows(payload, overlay.query_name(), r.query_name.as_deref(), "line"))
        .collect::<Result<Vec<_>, _>>()
        .expect("validate_line_layers already confirmed every dataset exists");
    let (scaled, layer_zero_rules) =
        y_sides.as_ref().map_or_else(Default::default, |sides| sides.scaled(overlays, &overlay_rows, x_field, ax_y));
    let layer_axes: Vec<LayerAxis> = scaled
        .iter()
        .enumerate()
        .map(|(i, p)| {
            p.axis(
                crate::annotation::widen_y(p.value_extent(p.rows.iter()), &r.annotations, crate::annotation::YAxis::Layer(i), None, false),
                &LayerAxisFrame {
                    family: fam,
                    ax_y,
                    fonts,
                    font_family: y_fam,
                    font_size: y_font_sz,
                    label_limit: y_limit,
                    tick_size: y_tick_eff,
                    title_style: layer_title_style,
                    field_labels: &r.field_labels,
                },
            )
        })
        .collect::<Result<_, _>>()?;
    // The gutter an away-aligned right axis reserves: its padding and widest label.
    let away_gutter = |label_ws: &[f64]| y_label_pad + label_ws.iter().map(|w| w.min(y_limit)).fold(0.0_f64, f64::max);
    // A shared-scale line layer's stroke reaches past the first and last datum
    // too, where the layer's marks sit inside the plot (the spec clips the rest).
    let mut shared_layer_reach = 0.0_f64;
    for overlay in overlays {
        let OverlayLayer::Line(l) = overlay else { continue };
        if y_sides.is_some() {
            continue;
        }
        let rows = resolve_layer_rows(payload, l.query_name.as_deref(), r.query_name.as_deref(), "line")?;
        let (lo, hi) = (y_min.min(y_max), y_min.max(y_max));
        if rows.iter().filter_map(|row| row_num(row, &l.y)).all(|v| (lo..=hi).contains(&v)) {
            shared_layer_reach = shared_layer_reach.max(l.mark_reach());
        }
    }

    // A bar layer's scale runs on past the last bucket's start, leaving the last datum
    // inside the plot edge.
    let bucket_slack = |w: f64| {
        if bucketed_bar_end.is_some() {
            utc_fracs.as_ref().and_then(|fracs| fracs.get(x_domain.len() - 1)).map_or(0.0, |last| (1.0 - last) * w)
        } else {
            0.0
        }
    };
    // A band scale seats the last datum half a band inside the plot edge.
    let datum_slack = |w: f64| if use_band_positions { x_band_geometry(w).bandwidth() / 2.0 } else { bucket_slack(w) };
    // A quantitative x's numbers, or a category x's dates: what a value
    // anchor interpolates against on the point scale.
    let annotation_ordinals: Option<Vec<f64>> = if x_is_quantitative {
        x_domain.iter().map(|v| v.parse::<f64>().ok()).collect()
    } else {
        x_time.as_ref().map(|time| time.at.clone())
    };
    // The chart's own ink past the plot, set by the fit's ink closure on every
    // pass; the last write is the drawn size's.
    let annotation_reach = std::cell::Cell::new(crate::annotation::Reach::default());
    // The annotation maps at a plot size: the scales the marks draw with.
    let annotation_ctx = |pw: f64, ph: f64| {
        use crate::annotation::{AnnotationCtx, AxisMap, Orientation, PlotRect};
        let x = match label_extent {
            Some((lo, hi)) => AxisMap::Time { extent: (lo as f64, hi as f64), range: (x_scale_pad, pw - x_scale_pad) },
            None => {
                let points = PointScale::new(x_domain.clone(), x_scale_pad, pw - x_scale_pad);
                let positions = (0..x_domain.len())
                    .map(|i| if use_band_positions { x_band_geometry(pw).edge(i, 0.5) } else { points.position_at(i) })
                    .collect();
                AxisMap::Point { domain: x_domain.clone(), positions, days: annotation_ordinals.clone(), range: (0.0, pw) }
            }
        };
        let y = if is_log {
            AxisMap::Log { domain: (y_min, y_max), range: (ph, 0.0) }
        } else {
            AxisMap::Linear { domain: (y_min, y_max), range: (ph, 0.0) }
        };
        let layer_y = crate::families::layers::layer_y_maps(&y, &layer_axes, overlays.len(), ph);
        AnnotationCtx {
            annotations: &r.annotations,
            chart_id: &r.id,
            panel: facet.map(|f| f.panel_key),
            plot: PlotRect { x: 0.0, y: 0.0, w: pw, h: ph },
            reach: annotation_reach.get(),
            x,
            y,
            layer_y,
            orientation: Orientation::Vertical,
            fonts,
            canvas,
            clip_id: format!("{}-ann{}", crate::contract::safe_svg_id(&r.id), facet.map_or(0, |f| f.panel_ordinal)),
        }
    };
    // A nominal x lays its labels out by the ordinal overlap ladder, judged at the width the
    // emitters were given: the card's, or the panel width the facet spec declared.
    let crowding_w = match facet {
        Some(f) => {
            let (_, cols) = multiples::grid_counts(r.multiples.as_ref(), &r.panel_axes);
            multiples::facet_panel_width(payload.width, cols, f.mirror_band_x, 0.0)
        }
        None => svg_w,
    };
    let nominal_x_angle = || -> Result<f64, String> {
        match ax_x.labels.angle {
            Some(angle) => Ok(angle),
            None => Ok(vega_fit::category_label_layout(
                &ax_x.labels,
                &x_domain,
                crowding_w,
                label_usable_ratio_opt,
                fonts,
                x_font_size,
            )?
            .angle),
        }
    };
    let plot = if shape == Shape::Fit {
        let mut legend_pos = (0.0, 0.0);
        let mut y_gutter = y_gutter;
        let mut canvas_w = view_w;
        let mut canvas_h = svg_h;
        let mut composed_pad_t = pad_t;
        let mut composed_rows: Option<Vec<support_table_strip::ComposedRow>> = None;
        let mut bare_axis: Option<(Vec<BareTick>, Vec<bool>)> = None;
        let mut y_visible: Option<Vec<bool>> = None;
        let y_title_extra = crate::common::y_title_extra(y_title_style, &y_title_paint);
        let x_title_extra = crate::common::x_title_extra(&ax_x.title, &x_title_paint);
        let x_title_w = crate::common::widest_title_line(&ax_x.title, &x_title_paint, fonts)?;
        let y_title_w = crate::common::widest_title_line(y_title_style, &y_title_paint, fonts)?;
        // Baseline rule ink: every rule layer strokes at the threshold
        // grid's own width (`_full_rule_at` reads `axis_y.grid.threshold`).
        let baseline_rule_w = ax_y.grid.threshold.as_ref().map(|z| z.width.unwrap_or(2.0));
        // Domain fraction from the top (0 at `y_max`, 1 at `y_min`) — the
        // SAME `interpolateNumber` ratio `LinearScale`/`LogScale::position`
        // use, kept as a plot-height-independent closure because this ink
        // pass runs against CANDIDATE heights during the fit, before the
        // final `plot_h` (and the `y_scale` bound to it) exist.
        let y_frac = |v: f64| -> f64 {
            if is_log {
                let span = y_max.ln() - y_min.ln();
                if span == 0.0 { 0.5 } else { (y_max.ln() - v.ln()) / span }
            } else {
                crate::families::cartesian::linear_frac_from_top(y_min, y_max, v)
            }
        };
        // Data extremes as domain fractions, clamped into the plot: the main
        // line layer and the point overlays are UNclipped (`_layers.py`'s
        // CLIP_TO_PLOT covers only the halo line / hit-target / area
        // backdrop), so the stroke expands the data bbox by the full stroke
        // width (round join — no miter term) and points by their reach.
        // `own_min`/`own_max` (this PANEL's own `rows` — see their own doc
        // comment above) feed the mark box, never `data_min`/`data_max` —
        // except on a STACKED area, whose own topmost mark is the tallest
        // column's own stacked total (`max_col_total` for `zero`/`center`,
        // exactly 1 for `normalize` — every column ends at the share total,
        // wherever the domain's top sits), not any raw per-row value:
        // `own_min`/`own_max` here are on a totally different scale (e.g.
        // 7..22) than the plotted stack, so using them would clamp the box
        // to near-zero height. The bottom is always the baseline, `y_min`.
        let (own_lo, own_hi) = if is_stacked {
            (y_min, if is_normalize { 1.0 } else { max_col_total })
        } else {
            (own_min, own_max)
        };
        let (frac_hi, frac_lo) = {
            let f_max = y_frac(own_hi).clamp(0.0, 1.0);
            let f_min = y_frac(own_lo).clamp(0.0, 1.0);
            (f_max.min(f_min), f_max.max(f_min))
        };
        // A layer's marks sit on its own scale once any layer pins a side, else the base's.
        let layer_y_at = |layer: usize, v: f64, ph: f64| -> f64 {
            if !layer_axes.is_empty() {
                layer_axes[layer].scale(ph).position(v)
            } else {
                ph * y_frac(v)
            }
        };
        let mut fit_tlabels: Option<Vec<TemporalLabel>> = None;
        let mut fit_raw_openers = false;
        let mut fit_x_angle = 0.0_f64;
        let mut fit_x_reach = 0.0_f64;
        let mut fit_x_format_tu: Option<TimeUnit> = None;
        // the full opener set (ticks/gridlines), independent of
        // which labels parity hides — `None` off the temporal path, where
        // emission falls back to `temporal_labels`'s own domain indices.
        let mut fit_tick_indices: Option<Vec<usize>> = None;
        // Composed support-table strip: its rows are ink like any other
        // mark, so they are measured once here — a pad layout never
        // renegotiates the plot, so the cells' x positions (the parent's own
        // x encoding, at the payload's width) are already final.
        let ay_stub_pad = ax_y.labels.padding.unwrap_or(6.0);
        let composed: Option<(
            Vec<support_table_strip::ComposedRow>,
            f64,
            &support_table_strip::SupportTableStyle,
        )> = match composed_strip {
            None => None,
            Some(cfg) => {
                let style = r.effective_support_table_style.as_ref().ok_or(
                    "line: support_table present but effective_support_table_style absent",
                )?;
                let point_scale =
                    PointScale::new(x_domain.clone(), x_scale_pad, svg_w - x_scale_pad);
                // Band centers reuse `fit_x_at`/`band_center_x`'s own
                // formula (`x_scale_pad + band_width * (i + 0.5)`,
                // `band_width = plot_w / n` since `x_scale_pad` is 0 under
                // `use_band_positions` — the padding refusal above enforces
                // that) so the strip's cells land on the SAME shared-x-axis
                // band centers the panel's own ticks already sit at, not
                // this arm's unconditional point-scale positions.
                let cell_xs: Vec<f64> = (0..x_domain.len())
                    .map(|i| {
                        if use_band_positions {
                            x_band_geometry(svg_w).edge(i, 0.5)
                        } else {
                            match &utc_fracs {
                                Some(fr) => {
                                    x_scale_pad + (svg_w - 2.0 * x_scale_pad).max(0.0) * fr[i]
                                }
                                None => point_scale.position_at(i),
                            }
                        }
                    })
                    .collect();
                let mut rows = support_table_strip::build_rows_composed(
                    &r.id,
                    cfg,
                    rows,
                    x_field,
                    &x_domain,
                    &cell_xs,
                    fonts,
                    style,
                    // Production anchors the declared affix on the first
                    // carrying cell in paint order for both x shapes this arm
                    // reaches: a point scale keeps the data order its `sort:
                    // null` anchor assumes, and a temporal domain is
                    // ascending. A sorted category x anchors on the first
                    // carrying cell of its pinned domain
                    // (`_pinned_domain_anchor`); only an unpinnable sort
                    // (`_is_json_scalar_domain`) stays `nowhere`, which this
                    // arm does not model. A quantitative x has no pinned
                    // order to anchor on: every cell paints in full.
                    !x_is_quantitative,
                    // Never band-centered, even on a band scale: production
                    // writes the `dx` for bars only ("For line/area charts dx
                    // is None: the aligned edge sits straight on the mark's x
                    // position", `support_table_attachment.py:1176-1181`),
                    // so a mirrored line's cells sit at the bare band center.
                    false,
                    y_on_right,
                    color_field,
                    &per_series_order,
                    &per_series_ink,
                    r.link.is_some().then_some(&strip_links), detected_data_tu.is_some() || bare_temporal)?;
                rows.iter_mut().for_each(|row| row.describe(&strip_aria));
                let n_rows = support_table_strip::visual_row_count(cfg, per_series_order.len())?;
                let strip_h = support_table_strip::strip_height(style, n_rows);
                Some((rows, strip_h, style))
            }
        };
        // The composed `title.offset` is the THEME's own offset (or 0.0 —
        // never VL's 10.0 default) plus production's closed-form font-size
        // correction (`support_table_title_offset_correction`, from
        // `support_table_attachment.py:4425`; the write is at `:4951-4962`)
        // — the strip's own ink extent (measured separately, `vega_fit`'s
        // own ink pass below) already grows the view-bounds top margin under
        // autosize:pad, so `title.offset` never carries `strip_h` itself.
        let composed_title = composed.as_ref().and_then(|(_, _, strip_style)| {
            // A bottom strip leaves the title as an unstripped pad chart has it.
            if strip_style.position == "bottom" {
                return title.ink().cloned();
            }
            let theme_offset = eff_title_ref
                .and_then(|ts| ts.position.as_ref())
                .and_then(|pos| pos.offset)
                .unwrap_or(0.0);
            let correction =
                support_table_strip::support_table_title_offset_correction(title_font_size);
            title
                .ink()
                .map(|t| vega_fit::TitleInk { offset: theme_offset + correction, ..t.clone() })
        });
        // See `facet_compose::PanelGeometry.top_axis_overhang`'s own doc
        // comment (same law, same probe evidence as bar's own `top_overhang`
        // — mirrors `render_vertical_continuous_x_panel`/`render_vertical_
        // panel`'s identical Cell) — reset at the START of every `run_fit()`
        // call (below), never accumulated ACROSS calls: an unbaked ladder's
        // own `tick_values` can change between the retry loop's iterations,
        // and a stale call's overhang must not survive into the next one's
        // measurement.
        let top_overhang = std::cell::Cell::new(0.0_f64);
        let mut run_fit = |tick_values: &[f64],
                           y_label_ws: &[f64],
                           y_gutter: f64|
         -> Result<vega_fit::FitMargins, String> {
        top_overhang.set(0.0);
        // The right edge of a flipped left axis paints the mirror's ghost labels.
        let flipped_ws = match ghost_ladder(tick_values)?.filter(|_| left_primary) {
            Some((_, ws)) => ws,
            None => y_label_ws.to_vec(),
        };
        let y_label_ws = flipped_ws.as_slice();
        let y_gutter = if y_away { away_gutter(y_label_ws) } else { y_gutter };
        // A left axis' whole extent past the plot: tick, label gutter, title.
        // An axis that draws no tick or label (a log domain floored at zero has
        // none) contributes no bounds past its title.
        let y_gutter_reach = if tick_values.is_empty() { 0.0 } else { y_tick_eff + y_gutter };
        let left_axis_reach = if tick_values.is_empty() {
            y_title_extra
        } else {
            y_tick_eff + y_label_pad + y_label_ws.iter().map(|w| w.min(y_limit)).fold(0.0_f64, f64::max) + y_title_extra
        };
        fit_tlabels = None;
        fit_raw_openers = false;
        fit_tick_indices = None;
        let mut fit_err: Option<String> = None;
        let mut ink_pass = |pw: f64, ph: f64, b: &mut vega_fit::Bounds| {
                if fit_err.is_some() {
                    return;
                }
                let mut ink = |b: &mut vega_fit::Bounds| -> Result<(), String> {
                    // The mirror ghost's far-left ink: a left legend anchors
                    // left of every axis in `xBounds`, the ghost included.
                    let mut ghost_ink_x1 = 0.0_f64;
                    // A left axis's ghost reaches past the plot's right edge.
                    let mut ghost_ink_x2 = pw;
                    // Value gutter + rotated y title, past the plot's own
                    // edge: right of it on a right axis, left on a left one.
                    if ghost_only {
                        b.union(&vega_fit::Bounds::new(0.0, 0.0, pw, ph));
                    } else if y_on_right {
                        b.union(&vega_fit::Bounds::new(0.0, 0.0, pw + y_gutter_reach + y_title_extra, ph));
                        if y_title_extra > 0.0 && !suppress_x_axis {
                            let x = pw + y_gutter_reach + y_title_extra;
                            b.union(&vega_fit::Bounds::new(x, ph / 2.0 - y_title_w / 2.0, x, ph / 2.0 + y_title_w / 2.0));
                        }
                    } else {
                        b.union(&vega_fit::Bounds::new(-left_axis_reach, 0.0, 0.0, ph));
                    }
                    // Per y-tick label ink (the top tick can overhang the plot
                    // top); parity hides every other label under min_gap and
                    // only the visible ones' ink counts. `tick_ys` is the one
                    // source for each tick's row — the ghost loop below reads
                    // it too, rather than recomputing `y_frac` a second time.
                    let tick_ys: Vec<f64> = tick_values
                        .iter()
                        .map(|&tv| ph * y_frac(tv))
                        .collect();
                    let (label_anchor_x, label_align) = if y_away {
                        (pw + y_tick_eff + y_label_pad, "left")
                    } else if y_on_right {
                        (pw + y_tick_eff + y_gutter, "right")
                    } else {
                        (-(y_tick_eff + y_label_pad), "right")
                    };
                    let label_lines = y_label_rows.borrow();
                    let label_ink: Vec<vega_fit::Bounds> = tick_ys
                        .iter()
                        .zip(y_label_ws.iter())
                        .zip(label_lines.iter())
                        .map(|((&y, &lw), &lines)| {
                            vega_fit::text_ink_rows(label_anchor_x, y, lw.min(y_limit), y_font_sz, label_align, "middle", lines)
                        })
                        .collect();
                    let visible = visible_y_labels(tick_values, &label_ink);
                    // A facet panel's value axis is real Vega's `row_footer`
                    // — a role structurally separate from `cell` that never
                    // shifts a row's own bars/marks to make room for a tick
                    // label overhanging past the row's own top/bottom edge
                    // (only its own gutter WIDTH matters to `cell`'s
                    // neighbor, never its label ink's vertical reach).
                    // Clamping to `[0, ph]` keeps that width contribution
                    // while dropping the vertical one only the single-chart
                    // `fit_plot` wants (mirrors bar's own `suppress_x_axis`
                    // clamp on this identical union).
                    for (&ink, _) in label_ink.iter().zip(&visible).filter(|(_, &v)| v) {
                        if suppress_x_axis {
                            top_overhang.set(top_overhang.get().min(ink.y1));
                            let mut clamped = ink;
                            clamped.y1 = clamped.y1.max(0.0);
                            clamped.y2 = clamped.y2.min(ph);
                            if !ghost_only {
                                b.union(&clamped);
                            }
                        } else {
                            b.union(&ink);
                        }
                    }
                    // Mirror ghost: the ghost's OWN per-label width
                    // (`y_label_ws` — the ghost repaints the primary's labels
                    // unless it carries its own `format`),
                    // at the SAME y positions
                    // (`tick_ys`, above) and under the SAME visibility mask —
                    // only the x anchor (sign-flipped onto the opposite
                    // edge) differs. Unlike the primary's own
                    // gutter (whose box term above already reaches the
                    // label's far edge, since `y_gutter` bakes the width
                    // in), the ghost's anchor sits INSIDE its own reserved
                    // offset, so each label's own width extends the margin a
                    // second time — production's inherited-padding law, not
                    // a bug: the ghost keeps the primary's flip-inward
                    // offset even on the edge where it no longer touches the
                    // axis. This per-label loop is the ghost's ENTIRE
                    // fit-ink contribution — no separate box term is needed:
                    // it already reaches further than any flat reservation
                    // of `y_tick_eff + y_gutter` alone would. The ink anchors
                    // at the reserve itself: Vega lays the axis out at x = 0
                    // and the 0.5 crisp shift is the SVG renderer's alone
                    // (probed: `labelPadding` 20 with a 7.133px label reserves
                    // 28, `ceil(20 + 7.133)`, never `ceil(26.63)`) — only the
                    // drawn label (`mirror_ghost_x`) carries it.
                    if mirror_active {
                        let (ghost_x, ghost_align) =
                            if y_on_right {
                                (-(y_tick_eff + y_gutter), "right")
                            } else if ghost_aligned {
                                (pw + y_tick_eff + y_gutter, "right")
                            } else {
                                (pw + y_tick_eff + y_label_pad, "left")
                            };
                        let ghost_ws = ghost_ladder(tick_values)?.map(|(_, ws)| ws);
                        let ghost_ws = ghost_ws.as_deref().unwrap_or(y_label_ws);
                        for ((&y, &lw), _) in
                            tick_ys.iter().zip(ghost_ws).zip(&visible).filter(|(_, &v)| v)
                        {
                            let ghost = vega_fit::text_ink(ghost_x, y, lw.min(y_limit), y_font_sz, ghost_align, "middle");
                            ghost_ink_x1 = ghost_ink_x1.min(ghost.x1);
                            ghost_ink_x2 = ghost_ink_x2.max(ghost.x2);
                            b.union(&ghost);
                        }
                    }
                    y_visible = Some(visible);
                    // Bottom axis labels — the VL x.axis.offset shifts the whole
                    // gutter down; drawn set resolved at THIS candidate width.
                    let row_y = x_edge.at(ph, X_AXIS_OFFSET + x_tick_eff + x_label_pad);
                    // Vega's text bounds grow a second row downward on either edge, so over a top axis the row
                    // reserves nothing above it.
                    let grow_row2 = |ink: &mut vega_fit::Bounds| ink.y2 += x_font_size + 2.0;
                    // How far an ink reaches away from the plot edge.
                    let reach_of = |ink: &vega_fit::Bounds| match x_edge {
                        crate::common::XEdge::Bottom => ink.y2 - ph,
                        crate::common::XEdge::Top => -ink.y1,
                    };
                    let n = x_domain.len();
                    let inner_w = (pw - 2.0 * x_scale_pad).max(0.0);
                    // L9: label positions are the RAW opener date's fraction
                    // of the (floored) domain extent — `label_extent`, not
                    // `utc_fracs` (the floored vertex positions) — so a
                    // weekly opener sits off its own vertex by the data's
                    // weekday offset from Sunday. Only in opener-cadence
                    // mode (`raw_openers`, set below once the resolved
                    // label grain is known); in native (every-bucket) mode
                    // this falls to the same floored position as the vertex.
                    let pos_at = |i: usize, raw: bool| {
                        let frac = match label_extent {
                            Some(_) if labels_off_rows => utc_fracs.as_ref().expect("a NaN row has UTC fractions")[i],
                            Some(_) => opener_frac_at(i, raw),
                            None => match &utc_fracs {
                                Some(fr) => fr[i],
                                None if n > 1 => i as f64 / (n as f64 - 1.0),
                                None => 0.0,
                            },
                        };
                        x_scale_pad + inner_w * frac
                    };
                    let frac_pos = |frac: f64| x_scale_pad + inner_w * frac;
                    // Band-step's x channel is a genuine BAND scale (see
                    // `band_center_x`'s doc comment below, near `x_scale`) —
                    // its first/last label sits at its band's CENTER, well
                    // inside the plot, contributing no edge overhang to this
                    // ink union; recomputed per candidate `pw`, mirroring
                    // `pos_at`/`inner_w` above.
                    let band_center = |i: usize| -> f64 { x_band_geometry(pw).edge(i, 0.5) };
                    // The position of the `i`-th temporal label: an ordinal calendar
                    // axis puts it on the band or point of its own bucket.
                    let label_at = |i: usize, raw: bool| -> f64 {
                        if !x_ordinal_calendar {
                            return if labels_off_rows { frac_pos(opener_frac_at(i, raw)) } else { pos_at(i, raw) };
                        }
                        let slot = label_slots[i];
                        if use_band_positions { band_center(slot) } else { pos_at(slot, false) }
                    };
                    let mut bare_tick_xs: Vec<f64> = Vec::new();
                    let mut labels_meta: Vec<(usize, f64, usize)> = Vec::new();
                    match detected_data_tu {
                        Some(data_tu) => {
                            let resolved = vega_x_labels
                                .as_ref()
                                .expect("a detected cadence on a Vega arm resolves its x labels");
                            let tls = speak_labels(match &x_tick_days {
                                Some(days) if labels_raw => none_tick_labels(
                                    &days.iter().map(|at| at.floor() as i64).collect::<Vec<_>>(),
                                    data_tu,
                                    authored_time_format,
                                )
                                .map_err(|e| format!("{fam}: {e}"))?,
                                // Vega's own interval ticks, labeled where
                                // their date opens a label period; their
                                // positions are the ticks' (`label_frac_at`).
                                Some(days) => interval_tick_labels(
                                    &days.iter().map(|at| at.floor() as i64).collect::<Vec<_>>(),
                                    &finite_of(x_domain_for_temporal),
                                    data_tu,
                                    resolved.format_tu,
                                    resolved.visibility_tu,
                                    resolved.year_step,
                                    ax_x.fiscal_year_start_month,
                                    authored_time_format,
                                )
                                .map_err(|e| format!("{fam}: {e}"))?,
                                None => {
                                    fit_raw_openers = matches!(data_tu, TimeUnit::YearWeek)
                                        && resolved.visibility_tu != data_tu;
                                    drop_off_domain_openers(
                                        resolved.labels.clone(),
                                        fit_raw_openers,
                                    )
                                }
                            })?;
                            let angle = resolved.angle;
                            let mut ink_all: Vec<vega_fit::Bounds> = Vec::with_capacity(tls.len());
                            let mut meta_all: Vec<(usize, f64, usize)> = Vec::with_capacity(tls.len());
                            for tl in &tls {
                                let (text, rows, angle_align, baseline) =
                                    tilted_temporal_label(tl, angle, resolved.format_tu, x_edge);
                                let w = fonts.measure(&text, x_family, x_font_size)?.max(if !tl.row2.is_empty() {
                                    fonts.measure(&tl.row2, x_family, x_font_size)?
                                } else {
                                    0.0
                                });
                                // An authored `labels.align` (L8) wins outright.
                                // Otherwise: a tilt takes Vega-Lite's own
                                // rotated-axis default; flat, Vega flushes
                                // edge labels only on a continuous temporal
                                // axis, and only the label whose own POSITION
                                // sits at a range end (L9).
                                let align = match x_label_align {
                                    Some(a) => a,
                                    None if angle != 0.0 => angle_align,
                                    None => match temporal_edge_anchor(tl.domain_idx, fit_raw_openers, pw) {
                                        "start" => "left",
                                        "end" => "right",
                                        _ => "center",
                                    },
                                };
                                let x = match &x_tick_fracs {
                                    Some(fracs) => frac_pos(fracs[tl.domain_idx]),
                                    None => label_at(tl.domain_idx, fit_raw_openers),
                                };
                                let mut ti =
                                    vega_fit::text_ink(x, row_y, w.min(x_limit), x_font_size, align, baseline);
                                for _ in 1..rows {
                                    grow_row2(&mut ti);
                                }
                                if angle != 0.0 {
                                    ti = ti.rotate(angle, x, row_y);
                                }
                                ink_all.push(ti);
                                meta_all.push((tl.domain_idx, w, rows));
                            }
                            // L1's Vega-runtime parity pass over the labels'
                            // real ink — separate from L4's static year
                            // pre-halve, which `resolve_vega_x_labels` already
                            // baked into `tls`/`resolved.labels`.
                            let visible = if resolved.parity {
                                vega_fit::parity_visible(&ink_all, x_separation)
                            } else {
                                vec![true; ink_all.len()]
                            };
                            // An axis with no tick still seats its group at the axis offset, which the
                            // fit counts.
                            if tls.is_empty() && !suppress_x_axis {
                                let seat = x_edge.at(ph, X_AXIS_OFFSET);
                                b.union(&vega_fit::Bounds::new(0.0, seat, 0.0, seat));
                            }
                            let mut kept_tls = Vec::with_capacity(tls.len());
                            fit_x_reach = if angle != 0.0 {
                                ink_all.iter().zip(&visible).filter(|(_, &keep)| keep).fold(0.0_f64, |a, (ink, _)| a.max(reach_of(ink)))
                            } else {
                                0.0
                            };
                            for ((tl, ink), &keep) in tls.into_iter().zip(&ink_all).zip(&visible) {
                                let label_day = match &x_tick_days {
                                    Some(days) => Some(days[tl.domain_idx]),
                                    None => crate::time_axis::date_text_day(&label_domain[tl.domain_idx]),
                                };
                                if keep && label_day.is_none_or(&is_listed) {
                                    // `suppress_x_axis`: the label TEXT/META
                                    // still populates `fit_tlabels` below
                                    // (`facet_compose::compose_facet`'s own
                                    // shared footer reads it off `LineXAxis`)
                                    // — only this panel's own ink reservation
                                    // skips.
                                    if !suppress_x_axis {
                                        b.union(ink);
                                    }
                                    kept_tls.push(tl);
                                }
                            }
                            // Vega's `labelOverlap: parity` hides
                            // LABEL text only — ticks and gridlines stay at
                            // every opener regardless. `labels_meta` (tick-
                            // stroke ink + the two-row band-height check
                            // below) keeps the FULL set; only the drawn
                            // label text (`fit_tlabels`) is parity-filtered.
                            labels_meta = meta_all;
                            fit_x_angle = angle;
                            fit_x_format_tu = Some(resolved.format_tu);
                            fit_tick_indices = Some(labels_meta.iter().map(|&(idx, _, _)| idx).collect());
                            fit_tlabels = Some(kept_tls);
                        }
                        // A2: Vega's own d3 ticks, at dates that need not
                        // be data points — their label ink joins the fit
                        // like any other label set's, parity-thinned first
                        // (`labelOverlap: true` at the theme's
                        // `labelSeparation`).
                        None if continuous_axis => {
                            let extent = bare_extent.expect("a bare axis has a scale extent");
                            let ticks = speak_bare(bare_ticks((extent.0 as f64, extent.1 as f64), pw)?)?;
                            let (angle, parity) = bare_layout();
                            let widths = bare_label_widths(&ticks, fonts, x_family, x_font_size, x_limit)?;
                            let (ink_all, visible) =
                                bare_label_ink(&ticks, &widths, angle, parity, x_separation, x_font_size, &frac_pos, pw, row_y, x_edge);
                            fit_x_angle = angle;
                            fit_x_reach = if angle != 0.0 {
                                ink_all.iter().zip(&visible).filter(|(_, &keep)| keep).fold(0.0_f64, |a, (ink, _)| a.max(reach_of(ink)))
                            } else {
                                0.0
                            };
                            if !suppress_x_axis {
                                for (ink, &keep) in ink_all.iter().zip(&visible) {
                                    if keep {
                                        b.union(ink);
                                    }
                                }
                            }
                            bare_tick_xs = ticks.iter().map(|t| frac_pos(t.frac)).collect();
                            let visible = visible.iter().zip(&ticks).map(|(&v, t)| v && is_listed(t.day)).collect();
                            bare_axis = Some((ticks, visible));
                        }
                        None => {
                            let angle = nominal_x_angle()?;
                            let (align, baseline) = nominal_label_anchor(angle, x_edge, x_label_align);
                            fit_x_angle = angle;
                            let mut reach = 0.0_f64;
                            let at: Vec<(&str, Option<f64>)> = match &quant_x_ticks {
                                Some(ticks) => ticks.iter().map(|(t, f)| (t.as_str(), Some(*f))).collect(),
                                None => x_shown.iter().map(|v| (v.as_str(), None)).collect(),
                            };
                            for (i, (xv, frac)) in at.into_iter().enumerate() {
                                let w = fonts.measure_shaped(xv, x_family, x_font_size)?;
                                labels_meta.push((i, w, crate::text::line_count(xv)));
                                let x = match frac {
                                    Some(f) => frac_pos(f),
                                    None if use_band_positions => band_center(i),
                                    None => pos_at(i, false),
                                };
                                let ti = crate::common::rotated_category_label_ink(
                                    x, row_y, w.min(x_limit), x_font_size, align, baseline, angle, crate::text::line_count(xv) - 1,
                                );
                                reach = reach.max(ti.y2 - ph);
                                if !suppress_x_axis {
                                    b.union(&ti);
                                }
                            }
                            fit_x_reach = if angle != 0.0 { reach } else { 0.0 };
                        }
                    }
                    // The outermost drawn ticks' stroke (rules expand by the
                    // full stroke width). `suppress_x_axis`: a facet panel's
                    // shared x-axis paints once, below the last row — this
                    // panel reserves no ink for it.
                    if x_ticks_visible && !suppress_x_axis {
                        let tw = ax_x.ticks.width.unwrap_or(1.0);
                        let ends = if continuous_axis {
                            bare_tick_xs.first().zip(bare_tick_xs.last()).map(|(&a, &b)| (a, b))
                        } else if let Some(fracs) = &x_tick_fracs {
                            fracs
                                .first()
                                .zip(fracs.last())
                                .map(|(&a, &b)| (frac_pos(a), frac_pos(b)))
                        } else if let Some(ticks) = &quant_x_ticks {
                            ticks.first().zip(ticks.last()).map(|(a, b)| (frac_pos(a.1), frac_pos(b.1)))
                        } else if x_ordinal_calendar {
                            let xs = labels_meta.iter().map(|&(i, _, _)| label_at(i, false));
                            xs.clone().reduce(f64::min).zip(xs.reduce(f64::max))
                        } else if use_band_positions {
                            labels_meta.first().zip(labels_meta.last())
                                .map(|(f, l)| (band_center(f.0), band_center(l.0)))
                        } else {
                            labels_meta.first().zip(labels_meta.last()).map(|(f, l)| {
                                (pos_at(f.0, fit_raw_openers), pos_at(l.0, fit_raw_openers))
                            })
                        };
                        if let Some((x1, x2)) = ends {
                            let seat = x_edge.edge_y(ph);
                            b.union(&vega_fit::Bounds::new(x1, seat, x1, seat).expand(tw));
                            b.union(&vega_fit::Bounds::new(x2, seat, x2, seat).expand(tw));
                        }
                    }
                    if x_title_extra > 0.0 && !suppress_x_axis {
                        let (y1, y2) = match x_edge {
                            crate::common::XEdge::Bottom => (0.0, ph + (b.y2 - ph).max(0.0) + x_title_extra),
                            crate::common::XEdge::Top => (-((-b.y1).max(0.0) + x_title_extra), ph),
                        };
                        b.union(&vega_fit::Bounds::new(pw / 2.0 - x_title_w / 2.0, y1, pw / 2.0 + x_title_w / 2.0, y2));
                    }
                    // Main line layer stroke ink; visible point markers add
                    // their halo symbol's reach. Vega's autosize-fit bounds
                    // union seeds from the FIRST/LAST datum's own drawn
                    // position, not the plot edge: a point scale's marks sit
                    // at `[x_scale_pad, pw − x_scale_pad]`; band-step's marks
                    // sit at the band edges (`0`/`pw`) when connected, or —
                    // `connect: false` — at each plateau's own inset edge
                    // (`line_mark.disconnected_width`'s share of the band,
                    // the SAME inset `band_edge` below draws with).
                    let point_reach = match r.style.point_mark.as_ref().and_then(|pm| pm.size) {
                        Some(sz) if sz > 0.0 => {
                            point_mark_reach(sz, r.style.line_mark.halo_multiplier)
                        }
                        _ => 0.0,
                    };
                    let last_band = x_domain.len().saturating_sub(1);
                    let (datum_x0, datum_x1) = if is_band_step {
                        let (lo, hi) = crate::step_band::band_step_edges(plateau_share);
                        (x_band_geometry(pw).edge(0, lo), x_band_geometry(pw).edge(last_band, hi))
                    } else if use_band_positions {
                        // Mirror's own band-position law with no row-doubling:
                        // ONE vertex per category, at its band's CENTER — the
                        // first/last drawn datum sits half a band step inside
                        // the plot edge, never at the band EDGE `is_band_step`
                        // draws with above.
                        (x_band_geometry(pw).edge(0, 0.5), x_band_geometry(pw).edge(last_band, 0.5))
                    } else {
                        // The first and last datum sit at the range ends unless an authored domain frames them.
                        let (first, last) = utc_fracs
                            .as_ref()
                            .map_or((0.0, 1.0), |fr| (fr.iter().copied().fold(f64::INFINITY, f64::min), fr.iter().copied().fold(f64::NEG_INFINITY, f64::max)));
                        let inner = (pw - 2.0 * x_scale_pad).max(0.0);
                        (x_scale_pad + inner * first, (x_scale_pad + inner * last).max(x_scale_pad))
                    };
                    if !base_rows.is_empty() {
                        b.union(
                            &vega_fit::Bounds::new(datum_x0, ph * frac_hi, datum_x1, ph * frac_lo)
                                .expand(line_stroke_w.max(point_reach).max(shared_layer_reach)),
                        );
                    }
                    for (((la, p), overlay), zero_rule) in layer_axes.iter().zip(&scaled).zip(overlays).zip(&layer_zero_rules) {
                        la.union_axis_ink(b, (pw, ph), 0.0, y_limit, y_font_sz);
                        let scale = la.scale(ph);
                        let reach = overlay.mark_reach();
                        if la.cut == DomainCut::Clip {
                            continue;
                        }
                        for row in p.rows {
                            let (Some(i), Some(v)) =
                                (domain_position(&x_domain, row, p.x), row_num(row, p.y))
                            else {
                                continue;
                            };
                            let cx = if use_band_positions { band_center(i) } else { pos_at(i, false) };
                            let y = scale.position(v);
                            b.union(&vega_fit::Bounds::new(cx - reach, y - reach, cx + reach, y + reach));
                        }
                        if let Some(width) = zero_rule {
                            let y = scale.position(0.0);
                            b.union(&vega_fit::Bounds::new(0.0, y, pw, y).expand(*width));
                        }
                    }
                    // An unclipped area's pile past the plot paints there, so the fit grows to hold it.
                    if y_sides.is_none() && !(y_min.min(y_max) > 0.0 || y_min.max(y_max) < 0.0) {
                        for overlay in overlays {
                            let OverlayLayer::Line(l) = overlay else { continue };
                            if l.area_mark.is_none() {
                                continue;
                            }
                            let layer_rows =
                                panel_layer_rows(rows, facet.is_some(), x_is_band && l.curve_name() == Some("step"), payload, l.query_name.as_deref(), r.query_name.as_deref(), "line")
                                    .expect("validate_line_layers already confirmed this dataset exists");
                            for (cat, start, end) in crate::families::layers::row_order_stack(layer_rows.iter(), l.x.as_deref().unwrap_or(x_field), &l.y, &crate::families::layers::never_nan) {
                                let Some(i) = x_domain.iter().position(|d| *d == cat) else { continue };
                                let cx = if use_band_positions { band_center(i) } else { pos_at(i, false) };
                                b.union(&vega_fit::Bounds::new(cx, ph * y_frac(end), cx, ph * y_frac(start)));
                            }
                        }
                    }
                    for (layer, bl, bar_rows, marks, slots) in &bar_label_fits {
                        let centers: Vec<f64> = slots
                            .iter()
                            .map(|&i| if use_band_positions { band_center(i) } else { pos_at(i, false) })
                            .collect();
                        crate::families::bar::marks::layer_bar_label_ink(
                            bl, bar_rows, marks, &centers, &|v| layer_y_at(*layer, v, ph), ph, fonts, payload.board_style.as_ref(), b,
                        )?;
                    }
                    for &(layer, i, v, w, plateau) in &layer_step_marks {
                        let (lo, hi) = crate::step_band::band_step_edges(plateau);
                        let y = layer_y_at(layer, v, ph);
                        let geometry = x_band_geometry(pw);
                        b.union(&vega_fit::Bounds::new(geometry.edge(i, lo), y, geometry.edge(i, hi), y).expand(w));
                    }
                    if let Some(rw) = baseline_rule_w {
                        for (datum, fires) in baseline_rule_data {
                            if !fires {
                                continue;
                            }
                            let ry = ph * y_frac(datum);
                            b.union(&vega_fit::Bounds::new(0.0, ry, pw, ry).expand(rw));
                        }
                    }
                    // L6: the value-label text layer's own ink — recomputed
                    // at this candidate rect, like every other mark's. Under
                    // `use_band_positions` this must read the SAME
                    // `band_center` the vertex/datum-ink union above already
                    // switched to (:3946), matching the paint site's own
                    // `fit_x_at` (which is `band_center_x` under the same
                    // flag) — not `pos_at`'s point-scale formula.
                    for (i, _, at, _, w, _, _) in &value_label_marks {
                        let y = ph * y_frac(*at);
                        let spot = label_spot(*i);
                        let label_x = match spot.edge {
                            Some(frac) => x_band_geometry(pw).edge(*i, frac),
                            None if use_band_positions => band_center(*i),
                            None => pos_at(*i, false),
                        };
                        let mut ink = vega_fit::text_ink(
                            label_x + spot.dx,
                            y + spot.dy,
                            *w,
                            value_label_size,
                            spot.align,
                            spot.baseline,
                        );
                        // A facet cell's marks reach past its plot into the composite
                        // (the composer reads that reach off the panel's ink), never
                        // into the panel's own margins.
                        if facet.is_some() {
                            ink.y1 = ink.y1.max(0.0);
                            ink.y2 = ink.y2.min(ph);
                        }
                        b.union(&ink);
                    }
                    for (layer, style, items) in &layer_label_fits {
                        for (i, v, w, spot) in items {
                            let y = layer_y_at(*layer, *v, ph);
                            let label_x = match spot.edge {
                                Some(frac) => x_band_geometry(pw).edge(*i, frac),
                                None if use_band_positions => band_center(*i),
                                None => pos_at(*i, false),
                            };
                            b.union(&style.ink(label_x, y, *w, spot));
                        }
                    }
                    // Annotation text clears the ink so far: axes, titles, marks.
                    annotation_reach.set(crate::annotation::Reach::past(b, pw, ph));
                    if let Some(ab) = crate::annotation::bounds(&annotation_ctx(pw, ph)) {
                        b.union(&ab);
                    }
                    if let Some(lg) = &vega_legend_box {
                        // `legendParams` anchors a side legend on `xBounds` and a
                        // top/bottom one on `yBounds` (`legend.js:41-64`): the
                        // plot rect plus the axes of that orientation and the
                        // scope groups' bounds (`ViewLayout.js:62-82`). The line
                        // paths live in scope groups (stroke-expanded); symbol,
                        // text and rule marks are bare marks that reach only the
                        // view bounds (`:83-84`), so a left legend clears the
                        // line stroke's ink and nothing else.
                        let size = (lg.box_w, lg.box_h());
                        // A layered spec clips the base's marks to the plot rect, so
                        // their stroke adds no ink to the bounds.
                        let stroke_ink = if has_layers { 0.0 } else { line_stroke_w };
                        let x_bounds = if y_on_right {
                            (
                                (datum_x0 - stroke_ink).min(0.0).min(ghost_ink_x1),
                                (pw + y_tick_eff + y_gutter + y_title_extra).max(pw + stroke_ink),
                            )
                        } else {
                            ((datum_x0 - stroke_ink).min(-left_axis_reach), (pw + stroke_ink).max(ghost_ink_x2))
                        };
                        // The legend clears every value axis, the layer's own included.
                        let x_bounds = layer_axes.iter().fold(x_bounds, |bounds, la| la.widen_axes(bounds, pw, 0.0));
                        // A color-grouped or band-step line layer's paths are scope groups too
                        // and, unlike the base's, not clipped: their stroke-expanded extent joins
                        // the bounds.
                        let (mut x_bounds, mut scope_top) = (x_bounds, 0.0_f64);
                        for (layer, overlay) in overlays.iter().enumerate() {
                            // A rounded, stroked bar layer is a scope group per datum whose
                            // stroke-expanded top joins `yBounds`, unless clipping dropped its radius.
                            if let OverlayLayer::Bar(bl) = overlay {
                                let border = &bl.bar_mark.border;
                                let stroke = border.width.unwrap_or(0.0);
                                let clipped = match layer_axes.get(layer) {
                                    Some(la) => la.cut == DomainCut::Clip,
                                    None => y_sides.is_none() && crate::families::layers::baseline_outside_shared_domain(ax_y),
                                };
                                if border.radius.unwrap_or(0.0) > 0.0
                                    && border.color.as_deref().is_some_and(|c| c != "none")
                                    && stroke > 0.0
                                    && !clipped
                                {
                                    let layer_rows = panel_layer_rows(
                                        rows, facet.is_some(), false, payload, bl.query_name.as_deref(), r.query_name.as_deref(), "line",
                                    )?;
                                    let top = match layer_axes.get(layer) {
                                        Some(la) => la.value_max,
                                        None => bar_layer_marks(bl, layer_rows, bl.x.as_deref().unwrap_or(x_field), &nan_x)
                                            .iter()
                                            .fold(0.0_f64, |hi, m| hi.max(m.from).max(m.to)),
                                    };
                                    scope_top = scope_top.min(layer_y_at(layer, top, ph) - stroke);
                                }
                                continue;
                            }
                            let OverlayLayer::Line(l) = overlay else { continue };
                            let band_step = x_is_band && l.curve_name() == Some("step");
                            if l.color.is_none() && !band_step {
                                continue;
                            }
                            let layer_rows = panel_layer_rows(
                                rows, facet.is_some(), band_step, payload, l.query_name.as_deref(), r.query_name.as_deref(), "line",
                            )?;
                            if let Some(color_field) = l.color.as_deref() {
                                let w = l.line_mark.stroke.width;
                                for row in layer_rows {
                                    let Some(i) = domain_position(&x_domain, row, x_field) else { continue };
                                    if !layer_stroked(l, row, color_field) {
                                        continue;
                                    }
                                    let cx = if use_band_positions { band_center(i) } else { pos_at(i, false) };
                                    x_bounds = (x_bounds.0.min(cx - w), x_bounds.1.max(cx + w));
                                }
                            }
                            scope_top = scope_top.min(scope_group_top(l, layer_rows, x_field, band_step, |v| layer_y_at(layer, v, ph)));
                        }
                        let max_rows = labels_meta.iter().map(|&(_, _, rows)| rows).max().unwrap_or(1);
                        let x_axis_y2 = row_y + x_font_size + (max_rows - 1) as f64 * (x_font_size + 2.0) + x_title_extra;
                        let x_bounds = (x_bounds.0, x_bounds.1.max(crate::annotation::text_x2(&annotation_ctx(pw, ph))));
                        legend_pos = legend_origin(&r.legend.position, x_bounds, (scope_top, x_axis_y2), (pw, ph), size);
                        if let Some(reach) = legend_fit_bounds(&r.legend.position, legend_pos, size) {
                            b.union(&reach);
                        }
                    }
                    if let Some((rows, _, style)) = &composed {
                        support_table_strip::composed_ink(
                            b,
                            rows,
                            style,
                            ay_stub_pad,
                            pw,
                            y_on_right,
                            if style.position == "bottom" { ph } else { 0.0 },
                            strip_axis_offset,
                        );
                    }
                    Ok(())
                };
                if let Err(e) = ink(b) {
                    fit_err = Some(e);
                }
        };
        // A facet panel (`suppress_x_axis`) compiles under real Vega's
        // `autosize: pad`: the plot rect is the composer's own declared
        // `svg_w`x`svg_h` verbatim, and axis ink overflows outward — never
        // the `fit` shrink the single-chart wrapper uses to keep ink inside
        // a fixed card (mirrors bar's own `render_vertical_continuous_x_
        // panel` gate). `composed_title` stays the composed-strip-specific
        // title source; a facet panel's own title is refused before this
        // seam is ever reached, so `title.ink()` (empty) is the right
        // fallback, same as the single-chart `fit_plot` arm below reads.
        let m = if composed.is_some() {
            vega_fit::pad_plot(svg_w, svg_h, &mut ink_pass, composed_title.as_ref())
        } else if suppress_x_axis {
            vega_fit::pad_plot(svg_w, svg_h, &mut ink_pass, title.ink())
        } else {
            vega_fit::fit_plot(
                view_w,
                svg_h,
                (pad_l, pad_r, pad_t, pad_b),
                &mut ink_pass,
                title.ink(),
            )
        };
        if let Some(e) = fit_err {
            return Err(e);
        }
        Ok(m)
        };
        let mut m = run_fit(&tick_values, &y_label_ws, y_gutter)?;
        // The rungs' labels move the gutter (plot_w), not plot_h, so the
        // count settles after one pass; the loop guards a ladder whose
        // widths change the height through the fit's own rounding.
        for _ in 0..4 {
            let Some(rungs) = rungs_at(m.plot_h) else { break };
            if rungs == tick_values {
                break;
            }
            tick_values = rungs;
            (formatted_ticks, y_label_ws) = ladder_labels(&tick_values)?;
            // `rungs_at` only ever fires here on the unbaked case (its own
            // guard: no `tick_values`, no `ticks.count`, never log), so the
            // gutter is ALWAYS the cover-ladder estimate at this site too
            // — never re-derived from the re-fit `tick_values`, which
            // production's estimator was never a function of to begin
            // with (it depends only on the data extent / authored domain,
            // both fixed across this loop's iterations).
            y_gutter = if y_on_right { estimated_gutter()? } else { 0.0 };
            m = run_fit(&tick_values, &y_label_ws, y_gutter)?;
        }
        if y_away {
            y_gutter = away_gutter(&y_label_ws);
        }
        // See `top_overhang`'s own doc comment above — `0.0` whenever no
        // tick's own label crosses this panel's own `y=0` (the common
        // case: fires only for the TOPMOST row of a facet, and only when
        // that row's own topmost tick label's ink reaches above it).
        let top_axis_overhang = (-top_overhang.get()).max(0.0);
        if let Some((rows, strip_h, strip_style)) = composed {
            debug_assert_eq!(
                m.plot_w, svg_w,
                "the strip's cells were positioned at the payload width the pad layout keeps"
            );
            let bottom_strip = strip_style.position == "bottom";
            if composed_title.is_none() && !bottom_strip {
                composed_pad_t += strip_h;
            }
            // Autosize-pad: the canvas is the padded plot plus the ink that
            // overhangs it, not the payload's own slot. `pad_plot` ceils each
            // margin; the plot height and an untitled strip (`padding.top`)
            // are summed verbatim (`ViewLayout.js:165-168`, `:198-201`).
            canvas_w = (pad_l + m.left + m.plot_w + m.right + pad_r).ceil();
            // A bottom strip rides `padding.bottom`, with the axis gap before it.
            canvas_h = composed_pad_t
                + m.top
                + m.plot_h
                + m.bottom
                + pad_b
                + if bottom_strip { strip_h + strip_axis_offset } else { 0.0 };
            support_table_strip::warn_strip_overflow(&r.id, &rows, m.plot_w, strip_style.row.padding.horizontal);
            composed_rows = Some(rows);
        }
        PlotRect {
            shape,
            plot_w: m.plot_w,
            plot_h: m.plot_h,
            left: m.left,
            raw_left: m.raw_left,
            raw_right: if y_on_right { (m.raw_left - bucket_slack(m.plot_w)).max(0.0) } else { m.raw_right },
            top: m.top,
            bottom: m.bottom,
            title_pos: m.title_pos,
            temporal_labels: fit_tlabels,
            raw_openers: fit_raw_openers,
            x_angle: fit_x_angle,
            x_reach: fit_x_reach,
            x_format_tu: fit_x_format_tu,
            tick_indices: fit_tick_indices,
            bare_axis,
            y_visible,
            legend_pos,
            y_gutter,
            rail_x_rel: None,
            canvas_w,
            canvas_h,
            composed_pad_t,
            composed_rows,
            facet_axis_right_overhang: m.right,
            top_axis_overhang,
        }
    } else {
        let mut legend_pos = (0.0, 0.0);
        let mut canvas_h = svg_h;
        let mut composed_rows: Option<Vec<support_table_strip::ComposedRow>> = None;
        let mut bare_axis: Option<(Vec<BareTick>, Vec<bool>)> = None;
        // Single-pass port of production's probe-shrink concat layout:
        // pane0 gives up exactly the chrome the probe measures (padding +
        // left-axis extent + unclipped right mark ink + hconcat spacing +
        // label pane), both panes give up the vertical chrome (title band +
        // x-axis band), and the sums are ceil'd the way the probe's
        // integer scenegraph dimensions are.
        // Cadence is decided against the rail-reduced width, mirroring the
        // crowding pass (emitters/line.py: box.width − rail span).
        // Production probes the pane at its spec height, so the probe's
        // rungs — and the labels that size the pane — are the payload
        // height's.
        if let Some(rungs) = rungs_at(svg_h) {
            tick_values = rungs;
            (formatted_ticks, y_label_ws) = ladder_labels(&tick_values)?;
            max_y_label_w = y_label_ws.iter().copied().fold(0.0_f64, f64::max);
        }
        let spacing = el.label_offset;
        let mut rail_raw_openers = false;
        let mut rail_x_format_tu: Option<TimeUnit> = None;
        let mut rail_tick_indices: Option<Vec<usize>> = None;
        let mut rail_x_angle = 0.0_f64;
        // The tilt term the bottom band reserves in place of the flat
        // `x_font_size` one — the widest CANDIDATE label's own rotated ink
        // (see the `if rail_x_angle != 0.0` branch below), read where
        // `x_band` is built further down.
        let mut rail_tilt_extent = 0.0_f64;
        // How far a rotated label's ink reaches away from the plot edge.
        let tilt_reach = |ink: &vega_fit::Bounds| match x_edge {
            crate::common::XEdge::Bottom => ink.y2,
            crate::common::XEdge::Top => -ink.y1,
        };
        // A tilt's runtime overlap decision (Vega's own `labelOverlap:
        // parity` transform) can only be evaluated once `plot_w` is final
        // — vl-convert draws pane 0 at the CORRECTED width, not this
        // probe's `payload.width` (L10 covers the STATIC cadence/tilt
        // CHOICE above, a Python-side pre-computation; the runtime overlap
        // pass is Vega's own, against whatever width the compiled spec
        // actually declares). The candidate set and whether Vega even
        // compiles the transform (`resolved.parity`) are known now; the
        // re-run itself is deferred to just after `plot_w` corrects below.
        let mut rail_x_parity = false;
        let mut rail_tls_candidates: Option<Vec<TemporalLabel>> = None;
        let mut temporal_labels: Option<Vec<TemporalLabel>> = match detected_data_tu {
            None => None,
            Some(data_tu) => {
                let resolved = vega_x_labels
                    .as_ref()
                    .expect("a detected cadence on a Vega arm resolves its x labels");
                rail_x_angle = resolved.angle;
                rail_raw_openers = matches!(data_tu, TimeUnit::YearWeek) && resolved.visibility_tu != data_tu;
                // Authored interval ticks label the dates they fall on, at their own positions.
                let tls = match &x_tick_days {
                    Some(days) => {
                        rail_raw_openers = false;
                        speak_labels(
                            interval_tick_labels(
                                &days.iter().map(|at| at.floor() as i64).collect::<Vec<_>>(),
                                &finite_of(x_domain_for_temporal),
                                data_tu,
                                resolved.format_tu,
                                resolved.visibility_tu,
                                resolved.year_step,
                                ax_x.fiscal_year_start_month,
                                authored_time_format,
                            )
                            .map_err(|e| format!("{fam}: {e}"))?,
                        )?
                    }
                    None => speak_labels(drop_off_domain_openers(resolved.labels.clone(), rail_raw_openers))?,
                };
                rail_x_format_tu = Some(resolved.format_tu);
                // ticks/gridlines stay at the full opener set —
                // only the label TEXT is parity-filtered below.
                rail_tick_indices = Some(tls.iter().map(|tl| tl.domain_idx).collect());
                // L10: probed at pane0's spec width (vl-convert ignores fit
                // on concat children) — same basis for the flat and tilted
                // ink below.
                let inner_w = (svg_w - 2.0 * x_scale_pad).max(0.0);
                if rail_x_angle != 0.0 {
                    // A tilt draws each label once, inlined, at Vega-Lite's
                    // own rotated-axis anchor — the SAME per-label law the
                    // fit arm's ink union carries
                    // (`resolve_tilted_temporal_label`). `Bounds::rotate` is
                    // a rigid rotation about the anchor `(x, row_y)`, so a
                    // label's own reach below its anchor row (`ink.y2` at
                    // `x = row_y = 0.0`) is independent of its position —
                    // the widest CANDIDATE sets the reservation regardless
                    // of which ones the runtime overlap pass below (deferred
                    // to `plot_w`'s corrected width) ultimately keeps,
                    // because `parity_visible`'s repeated halving never
                    // drops index 0, and the widest label here is always
                    // the leading opener (day/week-first inline text).
                    for tl in &tls {
                        let (text, rows, angle_align, baseline) =
                            tilted_temporal_label(tl, rail_x_angle, resolved.format_tu, x_edge);
                        let w = fonts.measure(&text, x_family, x_font_size)?.max(if !tl.row2.is_empty() {
                            fonts.measure(&tl.row2, x_family, x_font_size)?
                        } else {
                            0.0
                        });
                        let align = x_label_align.unwrap_or(angle_align);
                        let ti = crate::common::rotated_category_label_ink(
                            0.0, 0.0, w.min(x_limit), x_font_size, align, baseline, rail_x_angle, rows - 1,
                        );
                        rail_tilt_extent = rail_tilt_extent.max(tilt_reach(&ti));
                    }
                    rail_x_parity = resolved.parity;
                    rail_tls_candidates = Some(tls);
                    None
                } else if resolved.parity {
                    let mut ink = Vec::with_capacity(tls.len());
                    for tl in &tls {
                        let frac = label_frac_at(tl.domain_idx, rail_raw_openers);
                        let x = x_scale_pad + inner_w * frac;
                        let w1 = fonts.measure(&tl.row1, x_family, x_font_size)?;
                        let w = if tl.row2.is_empty() {
                            w1
                        } else {
                            w1.max(fonts.measure(&tl.row2, x_family, x_font_size)?)
                        };
                        ink.push(vega_fit::text_ink(x, 0.0, w.min(x_limit), x_font_size, "center", "top"));
                    }
                    let visible = vega_fit::parity_visible(&ink, x_separation);
                    Some(tls.into_iter().zip(&visible).filter(|(_, &v)| v).map(|(tl, _)| tl).collect())
                } else {
                    Some(tls)
                }
            }
        };
        let rail_nominal_angle = nominal_x_angle()?;
        if detected_data_tu.is_none() && !continuous_axis && rail_nominal_angle != 0.0 {
            rail_x_angle = rail_nominal_angle;
            let (align, baseline) = nominal_label_anchor(rail_x_angle, x_edge, x_label_align);
            for xv in &x_shown {
                let w = fonts.measure_shaped(xv, x_family, x_font_size)?;
                let ti = crate::common::rotated_category_label_ink(
                    0.0, 0.0, w.min(x_limit), x_font_size, align, baseline, rail_x_angle, crate::text::line_count(xv) - 1,
                );
                rail_tilt_extent = rail_tilt_extent.max(tilt_reach(&ti));
            }
        }
        let label_rows = temporal_labels
            .as_ref()
            .and_then(|tls| tls.iter().map(|tl| tl.rows()).max())
            .unwrap_or_else(|| x_shown.iter().map(|x| crate::text::line_count(x)).max().unwrap_or(1))
            .max(1 + usize::from(subday_plan.as_ref().is_some_and(crate::families::subday::SubdayPlan::stacked)))
            .max(dropped_grain_ladder.map_or(1, |r| {
                crate::families::temporal::fold_steep(r.labels.clone(), Some(r.angle), r.format_tu).iter().map(TemporalLabel::rows).max().unwrap_or(1)
            }));
        // The value labels' ink in pane-local coordinates for a pane `pw` x `ph`:
        // text marks are unclipped, so the captions join pane 0's bounds like
        // any mark. `None` with no labels drawn.
        let rail_label_ink = |pw: f64, ph: f64| -> Option<vega_fit::Bounds> {
            let n = x_domain.len();
            let mut ink: Option<vega_fit::Bounds> = None;
            for (i, _, at, _, w, _, _) in &value_label_marks {
                let spot = label_spot(*i);
                let x = match spot.edge {
                    Some(frac) => x_band_geometry(pw).edge(*i, frac),
                    None => {
                        let frac = match &utc_fracs {
                            Some(fr) => fr[*i],
                            None if n > 1 => *i as f64 / (n as f64 - 1.0),
                            None => 0.0,
                        };
                        x_scale_pad + (pw - 2.0 * x_scale_pad).max(0.0) * frac
                    }
                };
                let y = if y_max != y_min { ph * (y_max - at) / (y_max - y_min) } else { ph };
                let b = vega_fit::text_ink(x + spot.dx, y + spot.dy, *w, value_label_size, spot.align, spot.baseline);
                match &mut ink {
                    Some(acc) => acc.union(&b),
                    None => ink = Some(b),
                }
            }
            ink
        };
        let y_title_extra = crate::common::y_title_extra(&ax_y.title, &y_title_paint);
        // C3: the y axis's own ink extent. Vega lays each axis out with an
        // integer (ceil'd) extent before the panes negotiate widths, so the
        // gutter enters the chrome sum already ceil'd — but the title group
        // is anchored at the view bounds' own `x1`, which keeps the raw ink
        // (see `title_pos` below). Bar's rail arm carries the same split.
        // A strip mirrors with a right value axis: its cells grow leftward past
        // the plot's left edge, the axis gutter is pane 0's right ink, and the
        // stub column hangs past the axis at the spec width the concat never
        // shrank (`attach_support_table` reads `spec["width"]`).
        let rail_right = y_on_right;
        // Unclipped mark ink at a pane edge: main line stroke (boundStroke,
        // full width), the point marker (sqrt(size)/2 — its compiled
        // strokeWidth is inert: `filled: true` with no stroke color paints no
        // stroke, so boundStroke skips it), the point halo symbol
        // (sqrt(size x mult)/2 plus its white stroke at VL's default point
        // strokeWidth 2, full expand), a baseline rule's stroke.
        let mark_reach = {
            let mut ink = line_stroke_w;
            if let Some(sz) = r.style.point_mark.as_ref().and_then(|pm| pm.size) {
                if sz > 0.0 {
                    ink = ink.max(point_mark_reach(sz, r.style.line_mark.halo_multiplier));
                }
            }
            ink
        };
        let rule_ink = (zero_rule_fires || unity_rule_fires)
            .then(|| ax_y.grid.threshold.as_ref().and_then(|z| z.width).unwrap_or(2.0));
        let mark_ink = rule_ink.map_or(mark_reach, |rule| mark_reach.max(rule));
        let y_extent_ink = if rail_right {
            mark_ink
        } else {
            max_y_label_w + y_label_pad + y_tick_eff + y_title_extra
        };
        let mut y_extent = y_extent_ink.ceil();
        // A caption hanging left of the plot origin widens the left margin like any mark.
        if let Some(b) = rail_label_ink(svg_w, svg_h) {
            y_extent = y_extent.max((-b.x1).ceil());
        }
        // support_table (S7/S8): the strip's rows are pane-0 ink too, exactly
        // as the composed (non-concat) arm's `composed_ink` already models —
        // built once here, at the point scale's own basis (`payload.width`).
        // The rightmost cell (align: start, growing right from its own point)
        // sits `x_scale_pad` from pane0's edge on either width candidate, so
        // its overhang past the pane is the same number whichever width
        // probes it — computed once and folded into `right_ink`/`right_reach`
        // below like every other pane-0 right-ink term, rather than
        // re-derived per pass the way the mark/label terms are.
        let rail_strip_rows: Option<(Vec<support_table_strip::ComposedRow>, f64, f64)> =
            match composed_strip {
                None => None,
                Some(cfg) => {
                    let style = r.effective_support_table_style.as_ref().ok_or(
                        "line: support_table present but effective_support_table_style absent",
                    )?;
                    let n_rows = support_table_strip::visual_row_count(cfg, per_series_order.len())?;
                    let strip_h = support_table_strip::strip_height(style, n_rows);
                    let ink_ext = support_table_strip::composed_ink_extent(style, n_rows);
                    let point_scale =
                        PointScale::new(x_domain.clone(), x_scale_pad, svg_w - x_scale_pad);
                    let cell_xs: Vec<f64> = (0..x_domain.len())
                        .map(|i| match &utc_fracs {
                            Some(fr) => {
                                x_scale_pad + (svg_w - 2.0 * x_scale_pad).max(0.0) * fr[i]
                            }
                            None => point_scale.position_at(i),
                        })
                        .collect();
                    let rows = support_table_strip::build_rows_composed(
                    &r.id,
                        cfg, rows, x_field, &x_domain, &cell_xs, fonts, style, true, false,
                        y_on_right, color_field, &per_series_order, &per_series_ink,
                        r.link.is_some().then_some(&strip_links), detected_data_tu.is_some() || bare_temporal)?;
                    Some((rows, strip_h, ink_ext))
                }
            };
        // support_table (S6, the left-hang half): the stub label is pane-0
        // ink on the LEFT too — it sits at `-ay_stub_pad` (S4), growing
        // further left by its own (already `_LABEL_STUB_LIMIT_PX`-capped)
        // shaped width — so a stub wider than the axis-only gutter widens
        // `y_extent` itself, exactly the way the strip's right-reaching
        // cells widen `right_ink` above. `region_bar_grouped`'s own "Top
        // cat" stub is the corpus case that reaches this (`weekly_area_
        // stacked`'s "Total $"/"Units" stay narrower than its axis gutter).
        if let Some((rows, _, _)) = rail_strip_rows.as_ref().filter(|_| !rail_right) {
            let widest_stub_w = rows.iter().fold(0.0_f64, |a, row| a.max(row.label_w));
            y_extent = y_extent.max((y_label_pad + widest_stub_w).ceil());
        }
        let stub_x2 = match &rail_strip_rows {
            Some((rows, _, _)) if rail_right => {
                svg_w + ax_y.labels.padding.unwrap_or(6.0) + rows.iter().fold(0.0_f64, |a, row| a.max(row.label_w))
            }
            _ => 0.0,
        };
        if let Some((rows, _, _)) = rail_strip_rows.as_ref().filter(|_| rail_right) {
            let left_cell_ink = rows
                .iter()
                .flat_map(|row| row.cells.iter().map(move |cell| cell.w - (cell.x + row.dx)))
                .fold(0.0_f64, f64::max);
            y_extent = y_extent.max(left_cell_ink.ceil());
        }
        // Ports production's closed form (`support_table_attachment.py:4425`):
        // the strip floats the title via a closed-form font-size correction,
        // not `strip_h`
        // — the strip's own ink extent already grows the view-bounds top
        // margin under autosize:pad, same as the composed (non-concat)
        // arm's own `composed_title`.
        // A bottom strip leaves the title where an unstripped pad chart has it.
        let rail_bottom_strip = rail_strip_rows.is_some()
            && r.effective_support_table_style.as_ref().is_some_and(|st| st.position == "bottom");
        let strip_title_offset: Option<f64> = rail_strip_rows.as_ref().filter(|_| !rail_bottom_strip).map(|_| {
            let theme_offset = eff_title_ref
                .and_then(|ts| ts.position.as_ref())
                .and_then(|pos| pos.offset)
                .unwrap_or(0.0);
            theme_offset + support_table_strip::support_table_title_offset_correction(title_font_size)
        });
        // The bare temporal axis's ticks over a pane `pw` wide, the parity mask over their labels and each
        // label's ink under its tilt.
        let bare_labels_at = |pw: f64| -> Result<(Vec<BareTick>, Vec<bool>, Vec<vega_fit::Bounds>, f64), String> {
            let extent = bare_extent.expect("a bare axis has a scale extent");
            let ticks = speak_bare(bare_ticks((extent.0 as f64, extent.1 as f64), pw)?)?;
            let (angle, parity) = bare_layout();
            let inner_w = (pw - 2.0 * x_scale_pad).max(0.0);
            let widths = bare_label_widths(&ticks, fonts, x_family, x_font_size, x_limit)?;
            let (tilt_ink, visible) = bare_label_ink(
                &ticks, &widths, angle, parity, x_separation, x_font_size, |frac| x_scale_pad + inner_w * frac, pw, 0.0, x_edge,
            );
            Ok((ticks, visible, tilt_ink, angle))
        };
        // A left legend clears the value axis's ink and widens pane 0's left margin.
        let left_legend_x1 = -y_extent_ink.max(line_stroke_w);
        if let (Some(lg), true) = (&vega_legend_box, show_legend_left) {
            let origin = legend_origin(&r.legend.position, (left_legend_x1, 0.0), (0.0, 0.0), (0.0, 0.0), (lg.box_w, lg.box_h()));
            y_extent = y_extent.max((-origin.0).ceil());
        }
        // A top legend sits above the plot's top: pane 0's top ink.
        let legend_top = match &vega_legend_box {
            Some(lg) if show_legend_top => VEGA_LEGEND_OFFSET + lg.box_h(),
            _ => 0.0,
        };
        let right_ink = {
            let mut ink = (mark_reach - datum_slack(svg_w)).max(0.0);
            // The grid rule on the extended scale's last tick sits on the plot edge.
            if bucketed_bar_end.is_some() && ax_x.grid.visible.unwrap_or(false) {
                ink = ink.max(ax_x.grid.width.unwrap_or(1.0));
            }
            if let Some(rule) = rule_ink {
                ink = ink.max(rule);
            }
            // L8: an authored `labels.align` joins the LAST visible label's
            // overhang past the plot into pane0's right ink — the fit arm
            // gets this for free from its generic ink union; the rail arm
            // has no such union, so it's ported closed-form here, taking
            // the ACTUAL overhang wherever the last label sits (Vega's own
            // bounds union does the same — L9's off-domain drop makes "the
            // last opener isn't at the range end" the normal case for
            // weekly data under a coarser opener cadence, so this cannot
            // assume the range-end position). Probed at `payload.width`
            // (pane0's uncorrected width, matching production's own probe
            // model — L10(b)). The label's bound is its own geometry at
            // the scale position, without the axis group's 0.5 crisp
            // translate (see RL1 below).
            if let (Some(align), Some(tls)) = (x_label_align, &temporal_labels) {
                if let Some(last) = tls.last() {
                    let mut measure = |t: &str| fonts.measure(t, x_family, x_font_size);
                    let w = 2.0 * temporal_label_half_width(last, &mut measure)?;
                    let frac = label_frac_at(last.domain_idx, rail_raw_openers);
                    let inner_w = (svg_w - 2.0 * x_scale_pad).max(0.0);
                    let pos = x_scale_pad + inner_w * frac;
                    let ti = vega_fit::text_ink(pos, 0.0, w, x_font_size, align, "top");
                    ink = ink.max((ti.x2 - svg_w).max(0.0));
                }
            }
            // RL1: Vega-Lite's `labelFlush` default only flushes a
            // CONTINUOUS x scale's edge labels — a nominal/point scale is
            // never flushed, so the last label stays centered on its own
            // band and its half-width overhangs pane 0 unconditionally (no
            // authored `labels.align` needed; this port refuses an authored
            // one on this axis type — see the L8 gotcha below — so this is
            // its only overhang term). `target_by_month`'s "Mar" is the
            // corpus case (pane 0 373, not the mark-reach-only 377). A
            // cadence `detect_time_unit` refuses is still a CONTINUOUS time
            // scale (flushed edges, and the bare arm refuses its own
            // overhang above), never this term. Clamped at `x_limit` like
            // every other label-ink union in this file — Vega bounds the
            // mark at its limit, not the untruncated string it draws. The
            // bound is the label's geometry at the scale position; the
            // axis group's 0.5 crisp translate is not part of it, so the
            // hconcat rail steps at `k - w / 2`, not `k - w / 2 - 0.5`
            // (`tests/test_vl_probe.py`, the pane-0 reach probe).
            if detected_data_tu.is_none() && !continuous_axis {
                if let Some(last) = x_shown.last() {
                    let w = fonts.measure(last, x_family, x_font_size)?;
                    // The probe scale, matching the strip code above (a
                    // single-category domain sits at the LEFT edge, not the
                    // range end — `PointScale` steps 0.0 there).
                    let point_scale =
                        PointScale::new(x_domain.clone(), x_scale_pad, svg_w - x_scale_pad);
                    let pos = point_scale.position_at(x_domain.len() - 1);
                    let (align, baseline) = nominal_label_anchor(rail_nominal_angle, x_edge, x_label_align);
                    let ti = crate::common::rotated_category_label_ink(
                        pos, 0.0, w.min(x_limit), x_font_size, align, baseline, rail_nominal_angle, 0,
                    );
                    ink = ink.max((ti.x2 - svg_w).max(0.0));
                }
            }
            if let Some(b) = rail_label_ink(svg_w, svg_h) {
                ink = ink.max((b.x2 - svg_w).max(0.0));
            }
            ink
        };
        let x_title_extra = crate::common::x_title_extra(&ax_x.title, &x_title_paint);
        // The block's vertical stack; its WIDTH bounds pane 0 too, through
        // `pane0_x2` below (C2). support_table (S3): the strip floats the
        // title via `strip_title_offset`, porting production's closed-form
        // correction (`support_table_attachment.py:4425`), same as the
        // composed (non-concat) arm's own `composed_title`.
        let title_stack = title
            .ink()
            .map(|ti| ti.ink_h + strip_title_offset.unwrap_or(ti.offset) - ti.dy_quirk)
            .unwrap_or(0.0);
        // The pane-shrink decision's own chrome estimate — the wide,
        // pre-correction wrap's title stack, always <= `title_stack`
        // above (a wider wrap basis wraps to no MORE lines than a
        // narrower one). Off any call that isn't `payload.measuring` — a
        // board paints each chart exactly once, at its already-decided
        // slot height, and re-deriving the pane-shrink residual there
        // would double it (`Payload::measuring`'s own doc comment carries
        // the full case) — this just reuses the real, narrow `title_
        // stack`, matching production's own cached-SVG reuse for that
        // render. The inner `.unwrap_or(0.0)` isn't reachable with a
        // different value than `title_stack` already has: both blocks
        // wrap the identical `cased_title`, so `title_probe.ink`
        // is `None` exactly when `title.ink()` is, i.e. exactly when
        // `title_stack` is already `0.0` too.
        let title_stack_probe = if payload.measuring {
            title_probe
                .as_ref()
                .and_then(|tb| tb.ink())
                .map(|ti| ti.ink_h + strip_title_offset.unwrap_or(ti.offset) - ti.dy_quirk)
                .unwrap_or(0.0)
        } else {
            title_stack
        };
        // Top ink above the plot at probe time: the top y tick's label half
        // (middle-baseline ink extends round(0.8·fs) − round(0.3·fs) above
        // its position) and the unclipped mark reach at the data maximum
        // (boundStroke: FULL stroke widths; the point-halo symbol carries a
        // fixed 2px stroke in the compiled specs), each reduced by its
        // pixel distance from the plot top.
        // A right legend is pane0's rightmost ink (its box past the line
        // stroke's ceil'd reach + 18); a bottom legend its bottom-most
        // (18 + box below the x band).
        let right_ink = if rail_right { right_ink.max(y_tick_eff + y_gutter + y_title_extra) } else { right_ink };
        // The legend sits past pane0's right bound: the stroke's ceil'd reach,
        // or a right value axis's ink when that reaches further.
        // Annotation text past the plot's right edge sits between the pane and
        // the rail or legend, in the pane's own ink.
        let annotation_right = |pane_w: f64| {
            crate::annotation::bounds(&annotation_ctx(pane_w, svg_h)).map_or(0.0, |b| (b.x2 - pane_w).max(0.0))
        };
        let legend_right_x_at = |pane_w: f64| {
            let reach = if rail_right { line_stroke_w.max(right_ink) } else { line_stroke_w };
            reach.max(annotation_right(pane_w)).ceil() + VEGA_LEGEND_OFFSET
        };
        let right_reach_at = |pane_w: f64| match &vega_legend_box {
            Some(lg) if show_legend_right => legend_right_x_at(pane_w) + lg.box_w,
            _ => right_ink.max(annotation_right(pane_w)),
        };
        // C2: pane 0's own right bound, in plot-relative coordinates — the
        // union Vega's `gridLayout` reads. The title group anchors at the
        // pane's `viewBounds.x1` (`−y_extent_ink`, C3), so its capped ink
        // ends at `title_ink − y_extent_ink` and a title wider than the
        // mark reach pushes the rail (probe on production's own
        // `line_endpoint_labels` spec, pane 0 pinned to 410×311: the
        // authored title puts the rail at 433, `title.text = "X"` at 423).
        // support_table (round 2, MEDIUM-2): a strip's title instead
        // pad-anchors at `−left_margin` (`left_margin = y_extent`, the
        // HIGH-1 fix above), so its own ink ends at `title_ink − y_extent`
        // there, not `− y_extent_ink` — using the un-strip anchor
        // over-reserves by `y_extent − y_extent_ink` whenever S6/S8's stub
        // widening raises `y_extent` past its own unrounded extent.
        // Corpus-silent (no swept case's title outruns its mark reach).
        let title_x2 = title.ink().map(|ti| {
            if rail_strip_rows.is_some() {
                ti.width - y_extent
            } else {
                ti.width - y_extent_ink
            }
        });
        // The tilted cadence labels' ink over a pane `pw` wide and which of them `labelOverlap: parity` keeps there.
        let tilt_candidates = rail_tls_candidates.take();
        let cadence_tilt = rail_x_angle;
        let rail_tilt_at = |pw: f64| -> Result<(Vec<vega_fit::Bounds>, Vec<bool>), String> {
            let tls = tilt_candidates.as_ref().expect("a tilt carries its candidates");
            let format_tu = rail_x_format_tu.expect("a tilt implies a resolved format_tu");
            let inner_w = (pw - 2.0 * x_scale_pad).max(0.0);
            let mut ink = Vec::with_capacity(tls.len());
            for tl in tls {
                let (text, rows, angle_align, baseline) = tilted_temporal_label(tl, cadence_tilt, format_tu, x_edge);
                let w = fonts.measure(&text, x_family, x_font_size)?.max(if !tl.row2.is_empty() {
                    fonts.measure(&tl.row2, x_family, x_font_size)?
                } else {
                    0.0
                });
                let align = x_label_align.unwrap_or(angle_align);
                let x = x_scale_pad + inner_w * opener_frac_at(tl.domain_idx, rail_raw_openers);
                ink.push(crate::common::rotated_category_label_ink(
                    x, 0.0, w.min(x_limit), x_font_size, align, baseline, cadence_tilt, rows - 1,
                ));
            }
            let visible = if rail_x_parity { vega_fit::parity_visible(&ink, x_separation) } else { vec![true; ink.len()] };
            Ok((ink, visible))
        };
        // A tilted bare axis's last label hangs past a pane `pane_w` wide: Vega resolves its ticks, and so the
        // overhang, at each pass's own width.
        let bare_overhang_at = |pane_w: f64| -> Result<f64, String> {
            if tilt_candidates.is_some() {
                let (ink, visible) = rail_tilt_at(pane_w)?;
                return Ok(ink.iter().zip(&visible).filter(|(_, &v)| v).map(|(ti, _)| (ti.x2 - pane_w).max(0.0)).fold(0.0, f64::max));
            }
            if !continuous_axis {
                return Ok(0.0);
            }
            let (_, visible, ink, _) = bare_labels_at(pane_w)?;
            Ok(ink.iter().zip(&visible).filter(|(_, &v)| v).map(|(ti, _)| (ti.x2 - pane_w).max(0.0)).fold(0.0, f64::max))
        };
        let pane0_x2 = |pane_w: f64| {
            let x2 = (pane_w + right_reach_at(pane_w)).max(stub_x2);
            title_x2.map_or(x2, |tx| x2.max(tx))
        };
        // Parity over the labels as placed at a pane height: right-aligned
        // at the gutter's far side, middle baseline.
        let y_mask_at = |ticks: &[f64], ws: &[f64], ph: f64| -> Vec<bool> {
            let label_lines = y_label_rows.borrow();
            let ink: Vec<vega_fit::Bounds> = ticks
                .iter()
                .zip(ws.iter())
                .zip(label_lines.iter())
                .map(|((&tv, &lw), &lines)| {
                    let y = ph * crate::families::cartesian::linear_frac_from_top(y_min, y_max, tv);
                    vega_fit::text_ink_rows(-(y_label_pad + y_tick_eff), y, lw.min(y_limit), y_font_sz, "right", "middle", lines)
                })
                .collect();
            visible_y_labels(ticks, &ink)
        };
        // The probe renders the pane at its spec height (vl-convert ignores
        // autosize fit on concat children), so the labels it thins are the
        // ones pitched over the full payload height — a top label the final
        // pane hides still reserved its overhang in the probe, which is why
        // the reference's legend_bottom pane leaves 4px of slack below.
        let mask_probe = y_mask_at(&tick_values, &y_label_ws, svg_h);
        // Top overhang of a pane `ph` tall whose visible labels are `mask`.
        let top_ink_at = |mask: &[bool], ticks: &[f64], ph: f64| -> f64 {
            let label_half = vega_fit::middle_rise(y_font_sz);
            let mut ink = 0.0_f64;
            if let Some((&top_tick, _)) = ticks.iter().zip(mask).rev().find(|(_, &v)| v) {
                let dist = crate::families::cartesian::linear_frac_from_top(y_min, y_max, top_tick) * ph;
                ink = ink.max(label_half - dist);
            }
            // The rail pane is a concat sibling whose label text hangs half
            // its height above its anchor, widening the view's top margin
            // when the topmost cascaded label sits near the domain's top. A
            // support-table strip sits above the plot inside the pane, so the
            // labels never reach past its top.
            if rail_strip_rows.is_none() || rail_bottom_strip {
                let rail_half = vega_fit::middle_rise(sl.font_size);
                for (_, v) in crate::diagnostic::collect(|| rail_cascade(ph)).0 {
                    ink = ink.max(rail_half - crate::families::cartesian::linear_frac_from_top(y_min, y_max, v) * ph);
                }
            }
            if let Some(b) = rail_label_ink(svg_w, ph) {
                ink = ink.max(-b.y1);
            }
            // Same ink model as right_ink above.
            let mut mark_reach = line_stroke_w;
            if let Some(sz) = r.style.point_mark.as_ref().and_then(|pm| pm.size) {
                if sz > 0.0 {
                    mark_reach =
                        mark_reach.max(point_mark_reach(sz, r.style.line_mark.halo_multiplier));
                }
            }
            // The real datum closest to the domain's TOP end (`y_max`,
            // which maps to position 0 by construction — see the y-scale
            // comment above `y_scale`'s own build): `data_max` on an
            // ascending domain (bigger value nearer the top), but
            // `data_min` on a descending one (rank 1 nearer the top, e.g.
            // a bump chart's own `domain: [6, 1]`) — using `data_max`
            // unconditionally would measure the WRONG extreme's overhang
            // on a descending domain (the mark actually farthest from the
            // top, not nearest it), silently under-reserving this margin.
            // A stacked area's own topmost mark is the tallest column's
            // own stacked total, never a raw per-row value: `max_col_
            // total` for `zero`/`center` (the un-niced running sum the
            // domain itself is built from), or exactly `y_max` for
            // `normalize` (every column's running sum divides to exactly
            // 1.0, the fixed domain ceiling — `stackNormalize`,
            // `vega-encode/src/Stack.js`). `data_max`/`data_min` are the
            // RAW per-row extent, nowhere near the plotted (stacked, or
            // `[0, 1]`-normalized) scale — reading them here would
            // mismeasure this term (a wild over-reserve for `normalize`;
            // a smaller, corpus-invisible error for `zero`/`center`, whose
            // raw data happens to sit within the niced domain's own
            // range).
            let top_datum = if is_stacked {
                stacked_top_datum(y_min <= y_max, is_normalize, domain_col_total, domain_col_floor)
            } else if y_min <= y_max {
                data_max
            } else {
                data_min
            };
            let mark_dist = crate::families::cartesian::linear_frac_from_top(y_min, y_max, top_datum) * ph;
            ink = ink.max(mark_reach - mark_dist);
            ink.max(0.0)
        };
        // Production probes the concat child at `effective_height` (the
        // card height), so the probe's mask AND its overhang distances
        // are the full payload height's.
        // Annotation text above the plot takes the top margin the way a tick
        // label's overhang does: probed at the card size, then re-measured
        // at the final plot box, where y-anchored text can reach higher.
        let ann_top = |pw: f64, ph: f64| {
            if rail_strip_rows.is_some() {
                0.0
            } else {
                crate::annotation::bounds(&annotation_ctx(pw, ph)).map_or(0.0, |b| (-b.y1).max(0.0))
            }
        };
        let top_ink = top_ink_at(&mask_probe, &tick_values, svg_h).max(ann_top(svg_w, svg_h));
        // L10(b): production's `_correct_concat_overshoot` probes pane0 at
        // its UNCORRECTED width (the full card width — pane0 initially
        // spans the whole target). Vega's own gridLayout (the bundled
        // vega-view-transforms source, `lt`'s per-column accumulation:
        // `A[a] = k + ut(s.x1)` then `A[a] += _[col-1]` where
        // `_[col] = Math.ceil(cell.bounds.x2)`) CEILS pane0's own bounds
        // and adds the concat `spacing` (`k`) RAW, uncounted — not
        // `round(pane0 + ink + spacing)` as a single sum (probe: an hconcat
        // pane0 width sweep steps the rail's x at the pane's OWN ceil
        // boundary, 564.0→571 vs 564.1→572, not at a rounding half-point).
        // Ported closed-form (no re-probe): the canvas ceil uses the SAME
        // `pad_l + y_extent` left margin and `ceil(pane0_x2(..)) + spacing`
        // rail offset the final `rail_x_rel` below recomputes against the
        // corrected `plot_w` — consistent because the algebra collapses to
        // the old one-ceil formula exactly whenever nothing here is
        // fractional. Known gap: Vega's own
        // autosize ceils the left and right ink separately where this
        // ceils their sum once — unexercised by the corpus (no swept case
        // has fractional ink on both sides at once).
        let uncorrected_x2 = pane0_x2(view_w).max(view_w + bare_overhang_at(view_w)?);
        let rail_offset_uncorrected = uncorrected_x2.ceil() + spacing;
        // Where the concat's content ends: the rail pane, then — when the strokeDash legend sits at
        // the root — its box, anchored at the ceil of the grid's right bound plus the legend offset.
        let content_end = |rail_offset: f64| match &root_dash_legend {
            Some((legend, _, _)) => (rail_offset + rail_pane_w).ceil() + VEGA_LEGEND_OFFSET + legend.box_w,
            None => rail_offset + rail_pane_w,
        };
        let canvas_w_uncorrected = (pad_l + y_extent + content_end(rail_offset_uncorrected) + pad_r).ceil();
        // `_correct_concat_overshoot` shrinks pane 0 by what the concat overshoots the card,
        // and raises when nothing is left; a card with no width has no target to correct to.
        let plot_w = if svg_w > 0.0 { 2.0 * svg_w - canvas_w_uncorrected } else { view_w };
        if svg_w > 0.0 && plot_w <= 0.0 {
            return Err(Diagnostic::new(
                &crate::codes::ERR_CONCAT_OVERSHOOT_NONPOSITIVE,
                [
                    ("new_w", serde_json::json!(plot_w)),
                    ("orig_w", serde_json::json!(svg_w)),
                    ("overshoot", serde_json::json!(canvas_w_uncorrected - svg_w)),
                    ("target_width", serde_json::json!(svg_w)),
                ],
            ));
        }
        // Unlike the cadence labels above, resolved against production's own
        // probe width, the bare axis is Vega RUNTIME behavior: `tickCount: {signal: ceil(concat_0_width
        // / 40)}` and `labelOverlap` both evaluate against the CORRECTED
        // pane, so they read `plot_w`. A bare label is always one row
        // (`has_two_row` is false), and an authored `labels.align`, the
        // only other path that puts a label's overhang into pane0's right
        // ink, already refuses without a detected cadence.
        if continuous_axis {
            let (ticks, visible, tilt_ink, angle) = bare_labels_at(plot_w)?;
            if angle != 0.0 {
                rail_x_angle = angle;
                rail_tilt_extent = tilt_ink.iter().zip(&visible).filter(|(_, &v)| v).fold(0.0_f64, |a, (ti, _)| a.max(tilt_reach(ti)));
            }
            bare_axis = Some((ticks, visible));
        }
        // A tilt reserves the widest CANDIDATE label's own rotated reach
        // (`rail_tilt_extent`, computed above from the SAME ink the fit
        // arm's union carries) in place of the flat font-height term.
        // Vega lays a tilted label out identically on a concat child, so
        // only the extent the pane's bottom band reserves changes.
        let x_band_of = |tilt_extent: f64| {
            X_AXIS_OFFSET
                + x_tick_eff
                + x_label_pad
                + if rail_x_angle != 0.0 {
                    tilt_extent
                } else if x_edge == crate::common::XEdge::Top {
                    // A second row hangs below the first, into the plot.
                    x_font_size
                } else {
                    x_font_size + (label_rows - 1) as f64 * (x_font_size + 2.0)
                }
                + x_title_extra
        };
        // The band the x axis takes on its side of the plot: above it under a top axis.
        let band_of = |tilt_extent: f64| match x_edge {
            crate::common::XEdge::Bottom => (0.0, x_band_of(tilt_extent)),
            crate::common::XEdge::Top => (x_band_of(tilt_extent), 0.0),
        };
        let (x_band_above, x_band) = band_of(rail_tilt_extent);
        // The ink above pane 0's plot: the value axis's top label or the x axis band, with a top legend stacked past it.
        let ink_above = |top_ink: f64, band_above: f64| match x_edge {
            crate::common::XEdge::Bottom => top_ink.max(legend_top),
            crate::common::XEdge::Top => top_ink.max(band_above) + legend_top,
        };
        // The height probe reads a bare axis's tilt at the declared width, where its ticks differ from the final pane's.
        let (x_band_above_probe, x_band_probe) = if continuous_axis && rail_x_angle != 0.0 {
            let (_, visible, ink, _) = bare_labels_at(view_w)?;
            band_of(ink.iter().zip(&visible).filter(|(_, &v)| v).fold(0.0_f64, |a, (ti, _)| a.max(tilt_reach(ti))))
        } else {
            (x_band_above, x_band)
        };
        let bottom_reach = match &vega_legend_box {
            Some(lg) if show_legend_bottom => VEGA_LEGEND_OFFSET + lg.box_h(),
            _ => 0.0,
        };
        // A caption hanging below the plot past the x-axis band reaches further than it.
        let bottom_reach_at = |ph: f64| {
            bottom_reach.max(rail_label_ink(view_w, ph).map_or(0.0, |b| (b.y2 - ph - x_band).max(0.0)))
        };
        // support_table (S7): the pane keeps its OWN `autosize: pad` — its
        // declared height is the plot rect verbatim, exactly like the
        // composed (non-concat) arm's `svg_h`, never chrome-shrunk to fit
        // the slot. The canvas is left to overshoot below, by whatever the
        // strip's own chrome (`ink_ext`, `title_stack`'s bumped offset)
        // adds above a plot this tall.
        let plot_h = if rail_strip_rows.is_some() {
            svg_h
        } else {
            // The shrink decision reads the PROBE chrome (`title_stack_
            // probe`), matching `_correct_concat_overshoot`'s own height
            // branch, which shrinks the pane using the wide pre-
            // correction wrap — never the real, narrower one this plot
            // ships with.
            let chrome_h_probe = pad_t + pad_b + ink_above(top_ink, x_band_above_probe) + title_stack_probe + x_band_probe + bottom_reach_at(svg_h);
            // A pane left with no height by tall chrome keeps its declared one.
            let shrunk = svg_h - chrome_h_probe.ceil();
            if shrunk > 0.0 { shrunk } else { svg_h }
        };
        // The final pane draws its own rungs, at its height.
        if let Some(rungs) = rungs_at(plot_h) {
            if rungs != tick_values {
                tick_values = rungs;
                (formatted_ticks, y_label_ws) = ladder_labels(&tick_values)?;
                max_y_label_w = y_label_ws.iter().copied().fold(0.0_f64, f64::max);
            }
        }
        let mask_final = y_mask_at(&tick_values, &y_label_ws, plot_h);
        // Rebound at the FINAL pane height, not the probe's: production
        // never re-verifies `_correct_concat_overshoot`'s shrink (it only
        // ever subtracts the measured overshoot, `converters/chart.py:
        // 682-697`) — the shipped height is simply vl-convert's real
        // render of the corrected spec, whatever ink IT naturally needs.
        // The top y tick's own overhang is a property of that real,
        // shrunk-height render's own ladder/mask (`mask_final`, `plot_h`),
        // not the probe's wider one, so `chrome_h_final` below must read
        // THIS `top_ink`, shadowing the probe-time binding above. A label
        // the final ladder thins or drops shrinks the canvas below
        // `svg_h` on its own, with no title-wrap disagreement in play at
        // all (`themes_4/trend`: 432 -> 426, matching production).
        let top_ink = top_ink_at(&mask_final, &tick_values, plot_h).max(ann_top(plot_w, plot_h));
        let y_visible = Some(mask_final);
        if let Some(lg) = &vega_legend_box {
            let size = (lg.box_w, lg.box_h());
            legend_pos = if r.legend.position.overlay {
                legend_overlay_origin(&r.legend.position, (plot_w, plot_h), size)
            } else if show_legend_right {
                (plot_w + legend_right_x_at(plot_w), 0.0)
            } else if show_legend_bottom {
                (0.0, (plot_h + x_band).ceil() + VEGA_LEGEND_OFFSET)
            } else {
                legend_origin(&r.legend.position, (left_legend_x1, plot_w), (0.0, plot_h + x_band), (plot_w, plot_h), size)
            };
            // A top legend sits above the x axis hung over the plot.
            if show_legend_top && !r.legend.position.overlay {
                legend_pos.1 -= x_band_above;
            }
        }
        if plot_w <= 0.0 || plot_h <= 0.0 {
            // The concat correction raises its own error for a width left at zero and a pane left no height keeps
            // its declared one, so a box reaching here is an engine invariant broken.
            return Err(format!("line: a rail plot box ({plot_w}×{plot_h}) is empty after the concat correction").into());
        }
        // Margins are padding-relative, like FitMargins (the emission adds
        // pad_l/pad_t once). support_table (S3): the strip's own ink extent
        // rides on top of the title's bumped offset — the doubled gap the
        // composed (non-concat) arm's own `plot_top` already carries.
        let ink_ext = rail_strip_rows.as_ref().filter(|_| !rail_bottom_strip).map_or(0.0, |(_, _, ie)| *ie);
        let left_margin = y_extent;
        // The concat spec carries no padding bump for an untitled strip (the
        // composed arm's `padding.top` one), so the ink alone sets the margin.
        // One ceil over the whole sum: `top = ceil(-viewBounds.y1)`
        // (`ViewLayout.js:165-168`).
        let top_margin = (ink_above(top_ink, x_band_above) + title_stack + ink_ext).ceil();
        // The REAL (final, narrow-wrap) chrome — what the canvas must
        // actually clear, as opposed to `chrome_h_probe` above, which only
        // ever decided how far the pane was allowed to shrink.
        let chrome_h_final = pad_t + pad_b + ink_above(top_ink, x_band_above) + title_stack + x_band + bottom_reach_at(plot_h);
        // support_table (S7): no height fit — `bottom_margin` is the SAME
        // chrome term the non-strip case's residual formula collapses to
        // (`x_band + bottom_reach`), computed directly since `plot_h` no
        // longer subtracts from `payload.height` for that residual to fall
        // out of. Non-strip: derived from `chrome_h_final` rather than
        // `svg_h - pad_t - top_margin - plot_h` — NOT algebraically the
        // same value in general. `plot_h` subtracts `chrome_h_probe`,
        // built from the PROBE `top_ink`/`title_stack_probe`; `chrome_h_
        // final` uses the FINAL (rebound) `top_ink` and the real `title_
        // stack`. Deriving from `chrome_h_final` directly reserves
        // whatever gap opens between the two for `canvas_h` below,
        // instead of letting it silently vanish into `plot_h`'s own
        // subtraction.
        let bottom_margin = if let Some((rows, _, _)) = rail_strip_rows.as_ref().filter(|_| rail_bottom_strip) {
            // The margin under the plot is the ceil'd ink of the axis band or
            // the last strip row, whichever reaches further. Unlike the composed
            // arm, the concat's padding carries no bump for the strip.
            let style = r.effective_support_table_style.as_ref().expect("a rail strip implies its style");
            let mut strip_ink = vega_fit::Bounds::new(0.0, 0.0, 0.0, plot_h);
            support_table_strip::composed_ink(
                &mut strip_ink, rows, style, ax_y.labels.padding.unwrap_or(6.0), plot_w, y_on_right, plot_h, strip_axis_offset,
            );
            (x_band + bottom_reach_at(plot_h)).max(strip_ink.y2 - plot_h).ceil()
        } else if rail_strip_rows.is_some() {
            (x_band + bottom_reach_at(plot_h)).ceil()
        } else {
            chrome_h_final.ceil() - pad_t - top_margin
        };
        // Non-strip canvas: `plot_h + ceil(chrome_h_final)`, NOT clamped
        // back to `svg_h` — production's own shipped height is likewise
        // never reconciled to its target (`converters/chart.py:682-697`
        // only ever subtracts the probed overshoot; whatever the real,
        // corrected-width render then needs IS the height). Two
        // independent things can move it off `svg_h`: (1) the pane-shrink
        // and real-render title wraps disagreeing (`title_stack_probe <
        // title_stack`) grows it by `_correct_concat_overshoot`'s own
        // single-pass residual; (2) the top y tick ladder/mask changing
        // between the probe height (`svg_h`) and the corrected one
        // (`plot_h`) moves `top_ink` independently of any title wrap
        // (the `top_ink` rebind above) and can shrink it instead. Only
        // when NEITHER moves does it reduce to `svg_h`.
        if rail_strip_rows.is_none() {
            canvas_h = plot_h + chrome_h_final.ceil();
        }
        // The correction re-runs the SAME grid layout on the shrunk pane:
        // ceil pane 0's own bound, add `spacing` raw. Every other term of
        // the canvas sum is unchanged, so the canvas moves by exactly what
        // that ceil'd bound did — computed as a delta rather than re-added,
        // which keeps it exactly `payload.width` whenever nothing moves.
        let corrected_x2 = pane0_x2(plot_w).max(plot_w + bare_overhang_at(plot_w)?);
        let mut rail_x_rel = Some(corrected_x2.ceil() + spacing);
        let mut canvas_w = if root_dash_legend.is_some() {
            (pad_l + y_extent + content_end(corrected_x2.ceil() + spacing) + pad_r).ceil()
        } else {
            canvas_w_uncorrected + corrected_x2.ceil() - uncorrected_x2.ceil()
        };
        // The tilt's runtime overlap pass, deferred from the cadence
        // resolution above: Vega evaluates `labelOverlap: parity` against
        // the FINAL pane width it actually draws with (vl-convert ignores
        // autosize fit on concat children, so the second/corrected render
        // IS a real Vega run, not a re-probe), never the first pass's
        // `payload.width`.
        if let Some(tls) = tilt_candidates.as_ref() {
            let (_, visible) = rail_tilt_at(plot_w)?;
            temporal_labels =
                Some(tls.iter().zip(&visible).filter(|(_, &v)| v).map(|(tl, _)| tl.clone()).collect());
        }
        if rail_strip_rows.is_some() {
            // S7: the canvas is left to overshoot the slot — summed directly
            // rather than fit within `payload.height`, the one place this
            // arm departs from the non-`support_table` rail concat's own
            // canvas_h (which stays the untouched `svg_h` default). The plot
            // height is added verbatim between margins that are each ceil'd
            // on their own (`ViewLayout.js:165-168`, `:198-201`;
            // `vega_fit::pad_plot`), so a fractional height stays fractional.
            canvas_h = pad_t + top_margin + plot_h + bottom_margin + pad_b;

            // The strip's cells are positioned by the FINAL (corrected)
            // plot_w — production's own re-render at the shrunk pane width —
            // so they're rebuilt here rather than reusing the probe's own
            // `rail_strip_rows` (built at the uncorrected `payload.width`
            // purely to measure S8's overhang term above).
            let cfg = composed_strip.expect("rail_strip_rows implies composed_strip");
            let style = r
                .effective_support_table_style
                .as_ref()
                .expect("rail_strip_rows implies effective_support_table_style");
            let point_scale = PointScale::new(x_domain.clone(), x_scale_pad, plot_w - x_scale_pad);
            let cell_xs: Vec<f64> = (0..x_domain.len())
                .map(|i| match &utc_fracs {
                    Some(fr) => x_scale_pad + (plot_w - 2.0 * x_scale_pad).max(0.0) * fr[i],
                    None => point_scale.position_at(i),
                })
                .collect();
            let mut rows = support_table_strip::build_rows_composed(
                    &r.id,
                cfg, rows, x_field, &x_domain, &cell_xs, fonts, style, true, false,
                y_on_right, color_field, &per_series_order, &per_series_ink,
                r.link.is_some().then_some(&strip_links), detected_data_tu.is_some() || bare_temporal)?;
            rows.iter_mut().for_each(|row| row.describe(&strip_aria));
            if rail_right {
                rows.iter_mut().for_each(|row| row.stub_dx = svg_w - plot_w);
            }
            support_table_strip::warn_strip_overflow(&r.id, &rows, plot_w, style.row.padding.horizontal);
            // CORRECTED ordering (an earlier version of this comment had it
            // backwards): production's `_apply_support_table_strip` runs
            // FIRST (`vega_lite.py:624`, on `render_resolved_chart`'s
            // returned spec), and `_correct_concat_overshoot` runs LATER, at
            // SVG-render time (`converters/chart.py:904`), on a spec that
            // ALREADY carries the strip's layers (11 for `weekly_line_by_
            // category`: 3 mark + 8 strip; 10 for `weekly_area_stacked`: 6
            // mark + 4 strip — confirmed by instrumenting both functions on
            // a real board render). Ablating the probed (uncorrected,
            // `payload.width`-wide) spec's own layer list and re-probing
            // `vegalite_to_scenegraph` shows the strip's CELLS (data-driven
            // `x`, left-aligned, growing right) contribute exactly 0 to that
            // probe on both rows, while the strip's ROW LABELS (static
            // `x: -6`, right-aligned, in the left gutter) contribute the
            // entire 31px `weekly_line_by_category` overhang (0 on
            // `weekly_area_stacked`) — production's own probe widths,
            // 762/737, therefore do NOT exclude the strip; they exclude
            // only the cells. The row-label term is already modeled here,
            // separately, by `y_extent`'s own wide-stub widening a few
            // lines up (`y_extent.max((y_label_pad + widest_stub_w).ceil())`)
            // — that is why `canvas_w_uncorrected`/`plot_w` already match
            // production's real 762/366 and 737/391 without this section
            // needing to touch them.
            //
            // What THIS section closes is a separate, later effect: once
            // `_correct_concat_overshoot` shrinks pane 0 to its corrected
            // width, the strip's cells (whose own text width does not
            // shrink with the plot) overhang that NARROWER plot more than
            // they did the wide uncorrected one — real Vega gridLayout
            // (`bounds: "full"`) positions the rail pane off pane 0's own
            // REAL rendered bounds, not its declared width, so a cell
            // reaching past the corrected `plot_w` moves the rail pane (and
            // the canvas) exactly the way the axis/mark ink in
            // `corrected_x2` already does. `ink_x2` below folds the strip's
            // reach into that SAME local-x2 quantity — replacing the
            // earlier law that instead added the overhang to `canvas_w` as
            // a separate post-hoc `.round()`ed term (fitted: `.round()`
            // was chosen because `.ceil()` did not land both rows,
            // `laws-support-table.md`'s prior text said so outright).
            // `viewSizeLayout` (`vega-view-transforms/src/ViewLayout.js`,
            // `type === Pad`) ceils its `left`/`right` terms independently,
            // never rounds a summed total — `ink_x2.ceil()` here matches
            // that shape.
            let strip_overhang = if rail_right { 0.0 } else { support_table_strip::composed_right_overhang(&rows, plot_w) };
            if strip_overhang > 0.0 {
                let ink_x2 = corrected_x2.max(plot_w + strip_overhang);
                rail_x_rel = Some(ink_x2.ceil() + spacing);
                canvas_w = (pad_l + y_extent + ink_x2.ceil() + spacing + rail_pane_w + pad_r).ceil();
            }
            composed_rows = Some(rows);
        }
        // Vega anchors the title group at the view bounds' own left edge —
        // the axis's UNROUNDED ink extent — while the plot group's translate
        // takes the ceil'd one, so the title lands at `padding.left +
        // ceil(ink) − ink`, inside `[padding.left, padding.left + 1)`.
        // Reference title x: 16.9404 (`line_endpoint_labels`, ink 44.0596 at
        // plot origin 61), 16.8003 (`win_rate_trend`).
        //
        // support_table (S1/S7): pane 0 is under `autosize: pad`, and pad's
        // own title law (`vega_fit::pad_plot`'s doc comment) pad-anchors the
        // title at the canvas padding edge regardless of what the plot's own
        // ink negotiated — the axis-extent formula above is a FIT-mode law
        // that doesn't hold once the strip's chrome moves the plot but not
        // the title. Emitted as `group_x/y + title_pos`, so the pad-anchor
        // is just the group offset negated (`bar.rs`'s strip branch instead
        // bypasses `title_pos` and calls `emit_title_vega(pad_l, pad_t)`
        // directly; this reaches the same anchor without a second title
        // emission path, keeping wrap/subtitle support on the strip's title
        // the way the composed (non-concat) arm already has it).
        let title_pos = title.ink().map(|ti| {
            if rail_strip_rows.is_some() {
                (-left_margin, -top_margin)
            } else {
                (-y_extent_ink, -top_ink - ti.ink_h - strip_title_offset.unwrap_or(ti.offset))
            }
        });
        PlotRect {
            shape,
            plot_w,
            plot_h,
            left: left_margin,
            raw_left: 0.0,
            raw_right: 0.0,
            top: top_margin,
            bottom: bottom_margin,
            title_pos,
            temporal_labels,
            raw_openers: rail_raw_openers,
            x_angle: rail_x_angle,
            x_reach: 0.0,
            x_format_tu: rail_x_format_tu,
            tick_indices: rail_tick_indices,
            bare_axis,
            y_visible,
            legend_pos,
            y_gutter,
            rail_x_rel,
            canvas_w,
            canvas_h,
            composed_pad_t: pad_t,
            composed_rows,
            facet_axis_right_overhang: 0.0,
            top_axis_overhang: 0.0,
        }
    };
    let layout = plot.clone();
    let PlotRect {
        shape: _,
        plot_w,
        plot_h,
        left: left_margin,
        raw_left: _,
        raw_right: _,
        top: top_margin,
        bottom: _,
        title_pos: vega_title_pos,
        temporal_labels,
        raw_openers,
        x_angle,
        x_reach,
        x_format_tu,
        tick_indices,
        bare_axis,
        y_visible,
        legend_pos,
        y_gutter,
        rail_x_rel,
        canvas_w,
        canvas_h,
        composed_pad_t,
        composed_rows,
        facet_axis_right_overhang,
        top_axis_overhang: facet_top_axis_overhang,
    } = plot;

    // Does any emitted label carry a non-empty context row?
    let label_rows = match (&temporal_labels, &bare_axis) {
        (Some(ls), _) => ls.iter().map(|l| l.rows()).max().unwrap_or(1),
        (None, Some((ticks, _))) => ticks.iter().map(|t| t.rows()).max().unwrap_or(1),
        (None, None) => x_shown.iter().map(|x| crate::text::line_count(x)).max().unwrap_or(1),
    };
    // Axis groups carry Vega's +0.5 crispness translate on the fit path;
    // the bottom axis additionally sits below the VL x.axis.offset.
    let ad = 0.5;
    // `tick_snap`'s own doc comment carries the law (Vega's `tickRound`,
    // the band-step exception). X-only: `rule_at` still serves the y-axis's
    // own gridlines, never band-step.
    let rule_at = |pos: f64| tick_snap(pos, ad, false);
    let x_rule_at = |pos: f64| tick_snap(pos, ad, use_band_positions);
    // Vega's `y = ±offset`, then the crisp half pixel that shifts down on either edge.
    let x_axis_drop = x_edge.sign() * X_AXIS_OFFSET + 0.5;


    // ── Scales ───────────────────────────────────────────────────────────────
    let x_scale = PointScale::new(x_domain.clone(), x_scale_pad, plot_w - x_scale_pad);
    if crate::labels_expr::view::active() {
        use crate::labels_expr::view::{register_band, register_log, register_linear, register_point, register_utc, Span};
        match (use_band_positions, label_extent) {
            (true, _) => register_band("x", &x_domain, x_band_padding, x_band_padding, Span::Width)?,
            (false, Some((lo, hi))) if utc_fracs.is_some() => {
                register_utc("x", lo * 864e5, hi * 864e5, Span::WidthInset(x_scale_pad))
            }
            (false, _) => register_point("x", &x_domain, Span::WidthInset(x_scale_pad))?,
        }
        if is_log {
            register_log("y", y_min, y_max, Span::Height);
        } else {
            register_linear("y", y_min, y_max, Span::Height);
        }
        use crate::labels_expr::view::{register_axis, AxisSpec, Orient};
        let (orient, offset, align) = if y_away {
            (Orient::Right, y_tick_eff + y_label_pad, "left")
        } else if y_on_right {
            (Orient::Right, y_tick_eff + y_gutter, "right")
        } else {
            (Orient::Left, y_tick_eff + y_label_pad, "right")
        };
        if facet.is_none() && ax_y.labels.upright() {
            register_axis("y", AxisSpec { orient, offset, shift: 0.0, align: Some(align), baseline: None, angle: 0.0, flush: None, style: ax_y.labels.item_style()? });
        }
    }
    // Band scale: VL auto-types the x channel to a genuine BAND scale
    // whenever a genuine band-scale trigger is present — `curve: step`'s
    // own `xOffset` channel (the doubled rows) riding along it, so
    // `bandwidth('x')` resolves for `step_band.py`'s own offset-range
    // expressions, OR `axis_y.mirror`'s own invisible interaction rule
    // mark sharing this chart's `x` encoding with no `x2` (`laws-line-
    // area.md`'s band-position section). A band scale's labels/ticks/
    // gridlines AND — with no `xOffset` doubling, the mirror-only case —
    // its marks too sit at each band's CENTER ((i+0.5)·bw), never the
    // point scale's per-category vertex position (`fit_x_at`'s own
    // non-band branch). Reference: `fixtures/step-band-curve-connected`'s
    // own SVG draws "c00" at absolute 24.55 = 0.5·49.1 on a 491px/10-band
    // plot; `fixtures/multiples/06-line-rows` with `mirror: true` forced
    // draws its own 3-band panel's vertices at 50.33/151/251.67 =
    // (100.67)·(i+0.5) on a 302px plot — confirmed a band, not point,
    // scale either way (a point scale's first position is the range
    // start, 0). Reusing `x_scale_pad`/`plot_w`, not a new scale —
    // `use_band_positions && x_scale_pad != 0.0` already refuses above.
    let band_geometry = x_band_geometry(plot_w);
    let band_center_x = |i: usize| band_geometry.edge(i, 0.5);
    // Position by domain index: utc-scale time fractions on the temporal fit
    // path, band centers on a band scale, the point scale otherwise
    // (utc_fracs is None off the fit path, so the point-scale arm is
    // exactly `x_scale.position_at` there).
    let fit_x_at = |i: usize| {
        if use_band_positions {
            band_center_x(i)
        } else {
            match &utc_fracs {
                Some(fr) => x_scale_pad + (plot_w - 2.0 * x_scale_pad).max(0.0) * fr[i],
                None => x_scale.position_at(i),
            }
        }
    };
    // L9: in opener-cadence mode, label/tick/gridline positions take the
    // RAW opener date's own fraction of the (floored) domain extent,
    // distinct from `fit_x_at`'s vertex fraction; `raw_openers` (threaded
    // out of the fit/rail arm above) gates this — in native (every-bucket)
    // mode Vega's own ticks sit at the floored positions, matching the
    // vertices exactly.
    let frac_x_at = |frac: f64| x_scale_pad + (plot_w - 2.0 * x_scale_pad).max(0.0) * frac;
    let label_x_at = |i: usize| match label_extent {
        Some(_) => frac_x_at(label_frac_at(i, raw_openers)),
        None if labels_off_rows => frac_x_at(label_frac_at(i, raw_openers)),
        None => fit_x_at(i),
    };
    // A temporal label's position: an ordinal calendar axis puts it on its own
    // bucket's band or point.
    let temporal_label_x_at = |i: usize| if x_ordinal_calendar { fit_x_at(label_slots[i]) } else { label_x_at(i) };
    // y range: plot_h = domain_min (bottom), 0 = domain_max (top) — inverted.
    let y_scale = if is_log {
        MeasureScale::Log(LogScale::new(y_min, y_max, plot_h, 0.0))
    } else {
        MeasureScale::Linear(LinearScale::new(y_min, y_max, plot_h, 0.0))
    };

    // Each series' points in x-domain order, split into d3-shape's DEFINED
    // RUNS: a bucket carrying a row whose measure is null fails Vega-Lite's
    // default `defined` predicate, so the line/area generator closes the
    // current subpath and opens a new one after it, while a bucket with no
    // row for this series is invisible to d3 and joins its neighbors
    // (`area_won_stacked`'s reference: `Download` draws runs of 5/8/5/8/2 —
    // the run lengths a null-only break gives, not the 1/1/3/7/1/5/8/1/1 a
    // missing row would also break into).
    let runs_of = |series: &str| -> Vec<Vec<(f64, f64)>> {
        let mut runs: Vec<Vec<(f64, f64)>> = Vec::new();
        let mut open = false;
        if base_walks_every_row {
            // One vertex per row, in x order with ties in query order.
            let x_at: HashMap<&str, usize> = x_domain.iter().enumerate().map(|(i, x)| (x.as_str(), i)).collect();
            let mut walk: Vec<(usize, Option<f64>)> = base_rows
                .iter()
                .filter(|row| color_field.map(|cf| row_str(row, cf)).unwrap_or_default() == series)
                .map(|row| (x_at[row_str(row, x_field).as_str()], row_num(row, y_field)))
                .collect();
            walk.sort_by_key(|&(i, _)| i);
            for (i, yv) in walk {
                match yv {
                    None => open = false,
                    Some(_) if x_nan[i] => open = false,
                    Some(yv) => {
                        if !open {
                            runs.push(Vec::new());
                            open = true;
                        }
                        runs.last_mut().unwrap().push((fit_x_at(i), y_scale.position(yv)));
                    }
                }
            }
            return runs;
        }
        for (i, xv) in x_domain.iter().enumerate() {
            match data.get(&(xv.clone(), series.to_string())) {
                None => {}
                Some(None) => open = false,
                Some(_) if x_nan[i] => open = false,
                Some(&Some(yv)) => {
                    if !open {
                        runs.push(Vec::new());
                        open = true;
                    }
                    runs.last_mut().unwrap().push((fit_x_at(i), y_scale.position(yv)));
                }
            }
        }
        runs
    };
    // Band-step: each row doubles onto its band's left (0) and right (1)
    // pixel edge — `step_band.apply_step_band`'s `xOffset` point scale bound
    // to the synthetic `__step_band_pos__` column, riding the SAME band
    // scale `band_width`/`band_center_x` above. `connect: false` insets
    // both edges by `line_mark.disconnected_width`'s share of the band,
    // split evenly (`lo`/`hi`) — `step_band.py`'s own `offset_range` formula.
    let (edge_lo, edge_hi) = crate::step_band::band_step_edges(plateau_share);
    let band_edge = |i: usize, frac: f64| band_geometry.edge(i, frac);
    // The doubled rows draw in QUERY order: `apply_step_band` doubles each
    // row in place and never reorders (`step_band.py:134-136`), and the
    // band-step line/area sub-layers set `order: False`
    // (`emitters/_layers.py:435` line, `:1067` area), so Vega-Lite joins
    // them in dataset order. Each lands on the band `x_domain` seats its
    // category in, which an authored sort permutes — `pin_sorted_domain`
    // (`emitters/_cartesian.py:275`, called at `emitters/line.py:413`)
    // pins only the scale's domain. The two orders are one list without a
    // sort.
    let band_step_walk: Vec<usize> = if is_band_step {
        let band_of: HashMap<&str, usize> =
            x_domain.iter().enumerate().map(|(i, x)| (x.as_str(), i)).collect();
        let mut seen = HashSet::new();
        rows.iter()
            .map(|row| band_of[row_str(row, x_field).as_str()])
            .filter(|&i| seen.insert(i))
            .collect()
    } else {
        Vec::new()
    };
    // A path's link is its first datum's in the order the mark draws them.
    let series_href = |data_key: &str| {
        let mut walk: Box<dyn Iterator<Item = usize>> =
            if is_band_step { Box::new(band_step_walk.iter().copied()) } else { Box::new(0..x_domain.len()) };
        walk.find(|&i| matches!(data.get(&(x_domain[i].clone(), data_key.to_string())), Some(Some(_)))).and_then(|i| href_at(i, data_key))
    };
    // A linked disconnected step draws each plateau as its own path, which carries its band's datum link.
    let line_paths = |runs: &[Vec<(f64, f64)>], data_key: &str| -> Vec<(String, Option<&str>)> {
        if !(disconnected && r.link.is_some()) {
            return vec![(d3rs::shape::line_path(runs, curve), series_href(data_key))];
        }
        let hrefs = band_step_walk
            .iter()
            .filter(|&&i| matches!(data.get(&(x_domain[i].clone(), data_key.to_string())), Some(Some(_))))
            .map(|&i| href_at(i, data_key));
        runs.iter().zip(hrefs).map(|(run, href)| (d3rs::shape::line_path(std::slice::from_ref(run), curve), href)).collect()
    };
    let runs_of_band = |series: &str| -> Vec<Vec<(f64, f64)>> {
        let mut runs: Vec<Vec<(f64, f64)>> = Vec::new();
        let mut open = false;
        for &i in &band_step_walk {
            match data.get(&(x_domain[i].clone(), series.to_string())) {
                None => {}
                Some(None) => open = false,
                Some(&Some(yv)) => {
                    let y = y_scale.position(yv);
                    let pair = [(band_edge(i, edge_lo), y), (band_edge(i, edge_hi), y)];
                    // Disconnected: every band is its own subpath, matching
                    // production's per-band `detail` split — never joined
                    // even mid-run. Connected: only a null/missing gap opens
                    // a new run, same as `runs_of` above.
                    if disconnected || !open {
                        runs.push(pair.to_vec());
                        open = true;
                    } else {
                        runs.last_mut().unwrap().extend(pair);
                    }
                }
            }
        }
        runs
    };
    // Vega facets a panel's marks by the color field in that panel's own row
    // order, so a facet paints the series it holds in first-appearance order
    // there, whatever order the chart-wide domain has.
    let paint_order: Vec<String> = match color_field {
        Some(cf) if facet.is_some() && r.wide_measures.is_empty() => {
            let mut seen: Vec<String> = Vec::new();
            for row in rows {
                let s = row_str(row, cf);
                if !seen.contains(&s) {
                    seen.push(s);
                }
            }
            seen
        }
        _ => spatial_domain.clone(),
    };
    let series_points: Vec<(String, Vec<Vec<(f64, f64)>>)> = if is_multi {
        // Band-step + multi-series already refused above, so `runs_of` (not
        // `runs_of_band`) is always the right choice here.
        // A1: paint order, so the spatial domain — `series_fill` still
        // indexes the palette by the alphabetical one.
        paint_order.iter().map(|s| (s.clone(), runs_of(s))).collect()
    } else if base_rows.is_empty() {
        // Vega draws no line mark over a base query with no rows.
        Vec::new()
    } else {
        let runs = if is_band_step { runs_of_band("") } else { runs_of("") };
        vec![(single_fill.clone(), runs)]
    };

    // Stacked area's own (top, bottom) vertex pairs, per point — the
    // running sum at `baseline_order`'s position for `series`, offset per
    // column (`offset_at`). UNLIKE `runs_of`'s base runs law, NEITHER a
    // null measure NOR a genuinely absent (x, series) row breaks the run
    // here: production attaches `sparse_band_transforms` to every stacked
    // area (`emitters/area.py`), whose filter+impute pass drops a null row
    // then puts it back as a real 0 — "a null and an absent row reach the
    // same wedge" (`_layers.py`) — so both draw a continuous band pinched
    // to zero height at that x, not a gap. `columns[i]` already folds both
    // into its running sum as 0 (see the accumulation loop above), so
    // every x in `x_domain` draws a point for every series, with no
    // presence check needed at all.
    // A link puts its `href` among the fields Vega-Lite stacks by, so the stacked area's
    // impute pass fills every (href, x) cell its series has: a path then runs through
    // each x once per href, the datum's own row first, then the imputed zero rows in
    // the order their hrefs first appear.
    // A gap-filled row has no datum and so no link: it is one more href group, after the data's own.
    let impute_groups = (!hrefs.is_empty()).then(|| {
        let datum_cells = base_rows.iter().filter_map(|row| {
            let key = (row_str(row, x_field), color_field.map(|cf| row_str(row, cf)).unwrap_or_default());
            matches!(data.get(&key), Some(Some(_))).then(|| Some((key.0.clone(), Some(key.1.clone()), hrefs.get(&key)?.clone())))
        });
        let datum_cells: Vec<(String, Option<String>, String)> = datum_cells.flatten().collect();
        let mut filled_cells: Vec<(String, Option<String>, String)> = Vec::new();
        for series in &paint_order {
            for x in &x_domain {
                if !matches!(data.get(&(x.clone(), series.clone())), Some(Some(_))) {
                    filled_cells.push((x.clone(), Some(series.clone()), crate::href::UNDEFINED_HREF.to_string()));
                }
            }
        }
        crate::families::layers::ImputeGroups::new(datum_cells.into_iter().chain(filled_cells), false)
    });
    let href_groups = |series: &str| -> Vec<&str> { impute_groups.as_ref().map_or_else(Vec::new, |groups| groups.hrefs_of(series)) };
    let datum_href = |i: usize, series: &str| match data.get(&(x_domain[i].clone(), series.to_string())) {
        Some(Some(_)) => hrefs.get(&(x_domain[i].clone(), series.to_string())).map(String::as_str),
        _ => (!hrefs.is_empty()).then_some(crate::href::UNDEFINED_HREF),
    };
    let stacked_runs_of = |series: &str| -> Vec<Vec<(f64, f64, f64)>> {
        let idx = baseline_order.iter().position(|s| s == series).expect("series in baseline_order");
        let groups = href_groups(series);
        let run: Vec<(f64, f64, f64)> = (0..x_domain.len())
            .flat_map(|i| {
                let col = &columns[i];
                // `stackNormalize` (`vega-encode/src/Stack.js`): each
                // series' (y0, y1) is the running sum divided by the
                // column's own total — never an added offset, since the
                // domain is fixed [0, 1] rather than data-driven.
                let (lo, hi) = if is_normalize {
                    (col.bounds[idx].0 / col.total, col.bounds[idx].1 / col.total)
                } else {
                    let off = offset_at(i);
                    (off + col.bounds[idx].0, off + col.bounds[idx].1)
                };
                // A column with no total has no share (0/0): Vega paints its NaN edges at the top of the plot.
                let at = |v: f64| Some(y_scale.position(v)).filter(|p| !p.is_nan()).unwrap_or(0.0);
                let vertex = |(lo, hi): (f64, f64)| (fit_x_at(i), at(hi), at(lo));
                if groups.len() < 2 {
                    return vec![vertex((lo, hi))];
                }
                let real = datum_href(i, series);
                let imputed = groups.iter().copied().filter(|group| Some(*group) != real);
                // With a color the `order` channel sorts the stack, so the datum's row
                // (first in the data) sits under the zero rows after it; a colorless
                // stack sorts its hrefs descending.
                // A fall stacks below zero, while the zero rows stack on the positive side.
                let (zero_lo, zero_hi) = if hi < lo {
                    let above: f64 = baseline_order[..idx]
                        .iter()
                        .filter_map(|s| data.get(&(x_domain[i].clone(), s.clone())).copied().flatten())
                        .filter(|v| *v > 0.0)
                        .sum();
                    (above, above)
                } else {
                    (lo, hi)
                };
                let spans = |group: &str| match real {
                    None => (lo, lo),
                    Some(real) if is_multi => if group == real { (lo, hi) } else { (zero_hi, zero_hi) },
                    Some(real) => match group.cmp(real) {
                        std::cmp::Ordering::Equal => (lo, hi),
                        std::cmp::Ordering::Greater => (zero_lo, zero_lo),
                        std::cmp::Ordering::Less => (zero_hi, zero_hi),
                    },
                };
                real.into_iter().chain(imputed).map(|group| vertex(spans(group))).collect()
            })
            .collect();
        vec![run]
    };
    type StackedSeriesRuns = Vec<(String, Vec<Vec<(f64, f64, f64)>>)>;
    let stacked_series_points: StackedSeriesRuns = if is_stacked {
        // A colorless stack keys its one series by the fill, like `series_points`.
        let key = |s: &String| if is_multi { s.clone() } else { single_fill.clone() };
        paint_order.iter().map(|s| (key(s), stacked_runs_of(s))).collect()
    } else {
        Vec::new()
    };

    // Line style
    let line_w = r.style.line_mark.stroke.width.unwrap_or(4.0);
    let halo_w = line_w * r.style.line_mark.halo_multiplier;
    // Shared with `layers.rs`'s disconnected step-layer overlay —
    // `common::resolve_stroke_cap`'s doc comment carries the law.
    let cap = crate::common::resolve_stroke_cap(
        r.style.line_mark.stroke.cap.as_deref(),
        disconnected,
        r.style.line_mark.disconnected_cap.as_deref(),
    );
    let join = r.style.line_mark.stroke.join.as_deref().unwrap_or("round");

    // Area style (if drawing areas)
    let area_opacity = r.style.area_mark.as_ref().map(|a| a.opacity).unwrap_or(0.16);
    let area_backdrop = r.style.area_mark.as_ref().map(|a| a.backdrop).unwrap_or(false);

    // ── Scene ────────────────────────────────────────────────────────────────
    // Outer canvas dims echo the caller's `payload.width`/`payload.height`
    // (or the composed autosize-pad canvas) verbatim — vl-convert never
    // re-rounds them the way it rounds INTERNAL mark coordinates (the
    // `{:.3}` grid/rule lines elsewhere in this file); `py_float_str`
    // matches its own float-to-string exactly (whole numbers keep `.0`,
    // others print full precision — found via a repeating-decimal
    // `aspect_ratio` height on bar.rs's sibling shape, `chrome_bytes`-only,
    // invisible to the pixel-only per-chart ratchet).
    let frame = ChartFrame::new(canvas_w, canvas_h, (pad_l, pad_r, pad_t, pad_b), bg, py_float_str);

    // Title — routes through the shared `common::plain_title_node` so
    // `title_style.font.case` gets applied the same way bar/heatmap/pie/
    // scatter already do (production always applies `apply_case` at
    // VL-emission time regardless of family). `table.rs` hand-rolls its own
    // title emission instead — see its `render()` for why it can't share
    // this path.
    // The plot group's origin: `composed_pad_t` is `pad_t` everywhere but an
    // untitled composed strip, which production reserves in the padding.
    let group_x = pad_l + left_margin;
    let group_y = composed_pad_t + top_margin;
    let mut head: Vec<Node> = title.nodes(vega_title_pos, (group_x, group_y));
    if head.is_empty() {
        head.extend(
            r.title.as_deref().map(|text| crate::common::plain_title_node(text, eff_title_ref, pad_l + left_margin, pad_t)),
        );
    }
    // The plot group's children, in paint order.
    let mut plot: Vec<Node> = Vec::new();

    // Positions that get a gridline/tick: the FULL opener set on a Vega
    // arm (`tick_indices` — Vega's parity transform hides label TEXT only,
    // never ticks/gridlines), label positions on the legacy arm (which
    // never thins), every domain point otherwise.
    // A2: on the bare axis they are Vega's own d3 rungs instead — dates
    // that need not be domain members, so their x comes from the scale.
    let axis_tick_xs: Vec<f64> = match &bare_axis {
        Some((ticks, _)) => ticks.iter().map(|t| frac_x_at(t.frac)).collect(),
        None if x_tick_fracs.is_some() => {
            x_tick_fracs.iter().flatten().map(|&f| frac_x_at(f)).collect()
        }
        None if quant_x_ticks.is_some() => quant_x_ticks.iter().flatten().map(|&(_, f)| frac_x_at(f)).collect(),
        None => match (&tick_indices, &temporal_labels) {
            (Some(idxs), _) => idxs.iter().map(|&i| temporal_label_x_at(i)).collect(),
            (None, Some(tls)) => tls.iter().map(|tl| temporal_label_x_at(tl.domain_idx)).collect(),
            (None, None) => (0..x_domain.len()).map(label_x_at).collect(),
        },
    };

    // Y gridlines (horizontal at each tick)
    let yg = &ax_y.grid;
    let (y_lo, y_hi) = (y_min.min(y_max), y_min.max(y_max));
    // Area fills from its floor like a bar, so only a line chart tears.
    let tear = yg.torn_baseline.as_ref().filter(|t| t.visible && !draw_areas);
    let torn_y = tear
        .and_then(|_| crate::torn::torn_edge_value(y_lo, y_hi))
        .map(|edge| rule_at(y_scale.position(edge)));
    // The tear's cubics, which the x gridlines below run down onto.
    let torn = tear.zip(torn_y).map(|(t, ty)| {
        let inward = crate::torn::torn_inward(ty, ad, plot_h);
        let tick_rules: Vec<f64> = axis_tick_xs.iter().map(|&x| x_rule_at(x)).collect();
        (inward, crate::torn::torn_cubics(ad, plot_w + ad, ty, inward, &tick_rules, t))
    });
    // X gridlines (vertical at each axis tick position). Under a tear each
    // ends on the wave at its own x instead of poking through it.
    let xg = &ax_x.grid;
    if xg.visible.unwrap_or(false) {
        let gc = crate::common::required_ink(xg.color.as_deref(), "line", "axis_x.grid.color")?;
        let gw = xg.width.unwrap_or(1.0);
        let y1_prec = if torn.is_some() { 3 } else { 1 };
        let mut lines = Vec::new();
        for &x in &axis_tick_xs {
            let gx = x_rule_at(x);
            let (gy1, gy2) = match &torn {
                Some((inward, cubics)) if *inward < 0.0 => (ad, crate::torn::torn_across_at(cubics, gx)),
                Some((_, cubics)) => (crate::torn::torn_across_at(cubics, gx), plot_h + ad),
                None => (ad, plot_h + ad),
            };
            lines.push(Node::Line(
                Attrs::new()
                    .set("x1", format!("{gx:.3}"))
                    .set("y1", format!("{gy1:.y1_prec$}"))
                    .set("x2", format!("{gx:.3}"))
                    .set("y2", format!("{gy2:.3}"))
                    .set("stroke", gc)
                    .set("stroke-width", gw.to_string()),
            ));
        }
        plot.push(crate::contract::grid_group(lines));
    }

    if yg.visible.unwrap_or(false) {
        let gc = crate::common::required_ink(yg.color.as_deref(), "line", "axis_y.grid.color")?;
        let gw = yg.width.unwrap_or(1.5);
        let (gx1, gx2) = (ad, plot_w + ad);
        let mut lines = Vec::new();
        for &tv in &tick_values {
            let gy = rule_at(y_scale.position(tv));
            if torn_y.is_some_and(|ty| (ty - gy).abs() < 0.5) {
                continue;
            }
            lines.push(tick_line(3, (gx1, gy), (gx2, gy), gc, gw));
        }
        plot.push(crate::contract::grid_group(lines));
    }

    // Dual axis: each layer's own independent scale draws its OWN grid, painted
    // before any mark like the base's.
    let layer_linear: Vec<LinearScale> = layer_axes.iter().map(|la| la.scale(plot_h)).collect();
    for (la, ls) in layer_axes.iter().zip(&layer_linear).filter(|(la, _)| la.grid_visible) {
        let gc = crate::common::required_ink(yg.color.as_deref(), "line", "axis_y.grid.color")?;
        let gw = yg.width.unwrap_or(1.5);
        let (gx1, gx2) = (ad, plot_w + ad);
        for &tv in &la.tick_values {
            let gy = rule_at(ls.position(tv));
            plot.push(tick_line(3, (gx1, gy), (gx2, gy), gc, gw));
        }
    }

    if let Some((_, cubics)) = &torn {
        // The vertical gridlines' ink, whether or not they are drawn.
        let gc = crate::common::required_ink(xg.color.as_deref(), "line", "axis_x.grid.color")?;
        let gw = xg.width.unwrap_or(1.0);
        plot.push(Node::Path(
            Attrs::new()
                .set("class", "torn-baseline")
                .set("d", crate::torn::torn_path(cubics, false))
                .set("fill", "none")
                .set("stroke", gc)
                .set("stroke-width", gw.to_string()),
        ));
    }

    // Baseline rules — `BaselineFeature`'s `datum: 0` and `datum: 1` layers,
    // both in the zero grid's ink, each at its EXACT scale position: Vega
    // rounds grid and tick positions, never a rule mark (`win_rate_trend`'s
    // reference rule is at y 74.04, its 100% gridline at 74.5). Paint
    // order (`_insert_rule`, `baseline.py:37-82`): a LINE chart's rule is
    // PREPENDED, under everything — emitted here. An AREA chart's rule is
    // inserted right after the last `area` sub-layer — above the colored
    // fills, below the foreground stroke — so that call is deferred past
    // the colored-area block below (Z1).
    // The value axis edges the ticks start from: a baseline rule reaches them so its continued tick meets it.
    let y_edge_x = if y_on_right { plot_w + value_axis_right_overhang + 0.5 } else { 0.5 };
    let y_edges: Vec<f64> = if y_tick_eff > 0.0 && paint_value_axis {
        std::iter::once(y_edge_x).chain(mirror_active.then_some(if y_on_right { 0.5 } else { plot_w + 0.5 })).collect()
    } else {
        Vec::new()
    };
    let floor_span = crate::common::floor_rule_span(plot_w, ax_x, ax_x.ticks.visible.unwrap_or(true), axis_tick_xs.iter().map(|&x| x_rule_at(x)));
    let baseline_rules = || -> Result<Vec<Node>, String> {
        let mut rules = Vec::new();
        if let Some(zl) = &yg.threshold {
            let zc = zl.stroke();
            let zw = zl.width.unwrap_or(2.0);
            let (gx1, gx2) = crate::common::join_rule_to_edges(floor_span, &y_edges);
            for (datum, fires) in baseline_rule_data {
                if !fires {
                    continue;
                }
                let gy = y_scale.position(datum);
                // A reference rule is hidden from screen readers
                // (`full_rule_at`'s `aria: false`).
                rules.push(crate::contract::item_group(
                    "rule", "layer_0_marks", crate::contract::ItemRole::RuleMark, crate::contract::Reads::Hidden,
                    |aria| {
                        Node::Line(
                            Attrs::new()
                                .set("x1", format!("{gx1:.3}"))
                                .set("y1", format!("{gy:.3}"))
                                .set("x2", format!("{gx2:.3}"))
                                .set("y2", format!("{gy:.3}"))
                                .set("stroke", zc)
                                .set("stroke-width", zw.to_string())
                                .extend(aria),
                        )
                    },
                )?);
            }
        }
        Ok(rules)
    };
    // A layer flips this: production appends the zero rule as an ordinary
    // top-level sibling AFTER every data layer once a second layer exists
    // (`nest_zero_rule`'s own dual-axis comment — nesting the rule inside
    // layer 0 is needed only under an independent scale, to avoid a
    // degenerate [0,0] domain; a shared scale has no such risk) — painted
    // once the layer's own ink is down, below.
    if !draw_areas && !has_layers {
        plot.extend(baseline_rules()?);
    }

    // Annotations paint in the plot group's own frame. A fixed-margin arm
    // reserved no room for their text, so text past the plot is refused there;
    // the rail arm reserves the top (without a support_table strip).
    let annotations = annotation_ctx(plot_w, plot_h);
    if shape != Shape::Fit {
        let reserved = crate::annotation::Reserved {
            top: shape == Shape::RailConcat && r.support_table.is_none(),
            right: shape == Shape::RailConcat,
        };
        crate::annotation::refuse_margin_ink(&annotations, reserved)?;
    }
    // An area keeps its tints under the whole area stack; a line's tints go
    // between the data's halo (lines and points) and the main lines (below).
    if draw_areas {
        plot.extend(crate::annotation::nodes(&annotations, crate::annotation::Plane::Background)?);
    }

    // Paint order for area charts: backdrop areas → halo lines → colored areas → zero rule → main lines.
    // For line charts: zero rule → halo lines → main lines.
    // A stacked area's own recipe (`_area_stacked_layer`) has no backdrop,
    // no halo, and no separate top-edge line at all — one combined
    // fill+stroke layer per series, painting the full perimeter (stroke on
    // the AREA mark itself). Production already bakes that recipe onto
    // THIS payload's `line_mark`/`point_mark` fields (halo_multiplier: 0,
    // point_mark.size: null), so the halo/point blocks below skip
    // themselves without any `is_stacked` check of their own — only the
    // backdrop and the two fill/stroke blocks need one.

    // Vega-Lite's area baseline (`zeroOrMin`): the scale's own 0 when the
    // domain definitely includes it, else the plot's bottom edge.
    let area_floor_y = if !is_log && y_lo <= 0.0 && 0.0 <= y_hi && (!explicit_zero_false || authored_domain.is_some()) {
        y_scale.position(0.0)
    } else {
        plot_h
    };

    // Backdrop areas (area only, fill=canvas, opacity=1)
    if draw_areas && area_backdrop && !is_stacked {
        for (series, runs) in &series_points {
            let d = d3rs::shape::area_path(runs, area_floor_y, curve);
            let anchor = r.link_open.anchor(series_href(&data_key_of(series)));
            let path = Node::Path(Attrs::new().set("d", d).set("fill", canvas).set("fill-opacity", "1"));
            plot.push(crate::contract::connected_group("area", "layer_0_marks", vec![crate::href::linked(anchor, path)]));
        }
    }

    // Vega's ordinal scale cycles a short range by domain index; an empty
    // dash entry is a solid line.
    let dash_of = |series: &str| -> Option<String> {
        let list = if dashed { series_dash(&palette_domain, &r.style.dashes, series) } else { String::new() };
        (!list.is_empty()).then_some(list)
    };

    // The foreground line's authored dash. Each disconnected plateau is its own
    // subpath, so the dash restarts on every one and is fitted to the band
    // (`_layers.py:397`); any other line keeps the authored list.
    let ink_dash = authored_dash
        .as_deref()
        .map(|list| crate::step_band::dasharray(list, disconnected.then_some(band_geometry.bandwidth() * plateau_share)));

    // Halo lines — canvas-colored wider stroke under each main line,
    // sharing the series' dash (the strokeDash encoding is chart-level).
    // `_line_halo_sublayers` drops the halo line and point together at
    // `halo_multiplier == 0`, so neither is emitted.
    let halo_stroke = canvas;
    if r.style.line_mark.halo_multiplier != 0.0 {
        for (series, runs) in &series_points {
            for (d, href) in line_paths(runs, &data_key_of(series)) {
                let anchor = r.link_open.anchor(href);
                let halo = Attrs::new()
                    .set("d", d)
                    .set("fill", "none")
                    .set("stroke", halo_stroke)
                    .set("stroke-width", halo_w.to_string())
                    .set("stroke-linecap", cap)
                    .set("stroke-linejoin", join);
                let halo = with_dash(halo, dash_of(series));
                plot.push(crate::contract::connected_group("line", "layer_1_marks", vec![crate::href::linked(anchor, Node::Path(halo))]));
            }
        }
    }
    // Colored areas (area only, fill=series_color, opacity=area_opacity).
    // Stacked: one combined fill+stroke path per series — `stroke` is the
    // resolved `line_mark.stroke` (production's stack-mode swap already put
    // `marks.area.stacked.stroke`'s color/width/cap/join there), painting
    // the fill's own full perimeter, not a separate top-edge-only line.
    if draw_areas && is_stacked {
        // `_area_stacked_layer`'s own docstring: "the theme's stacked
        // recipe always supplies a stroke" — production emits NO `stroke`
        // prop at all when the color is absent, never a background-
        // colored fallback, so a missing color here is a contract
        // violation to surface loudly, not a state to paper over.
        let stroke_color = r
            .style
            .line_mark
            .stroke
            .color
            .as_deref()
            .ok_or("area: stacked area's line_mark.stroke.color is required")?;
        for (series, runs) in &stacked_series_points {
            let color = series_fill(series, &palette_domain, &r.series_palette, is_multi, category_scale)?;
            let d = d3rs::shape::stacked_area_path(runs, curve);
            // The path's first item: the first x's own datum, or the imputed row there.
            let anchor = selection.mark(r.link_open.anchor(datum_href(0, &data_key_of(series))));
            let path = Node::Path(
                Attrs::new()
                    .set("d", d)
                    .set("fill", crate::href::Anchor::paint(anchor, &color))
                    .set("fill-opacity", area_opacity.to_string())
                    .set("stroke", crate::href::Anchor::paint(anchor, stroke_color))
                    .set("stroke-width", line_w.to_string())
                    .set("stroke-linecap", cap)
                    .set("stroke-linejoin", join),
            );
            plot.push(crate::contract::connected_group("area", "layer_2_marks", vec![crate::href::linked(anchor, path)]));
        }
        // Z1: an area chart's zero rule paints here — above the fills it
        // just drew, below the point-marker halos and main strokes below. A
        // layer on its own scale nests it after the base's whole ink instead.
        if y_sides.is_none() {
            plot.extend(baseline_rules()?);
        }
    } else if draw_areas {
        for (series, runs) in &series_points {
            let color = series_fill(series, &palette_domain, &r.series_palette, is_multi, category_scale)?;
            let d = d3rs::shape::area_path(runs, area_floor_y, curve);
            let anchor = selection.mark(r.link_open.anchor(series_href(&data_key_of(series))));
            let path = Node::Path(
                Attrs::new()
                    .set("d", d)
                    .set("fill", crate::href::Anchor::paint(anchor, &color))
                    .set("fill-opacity", area_opacity.to_string()),
            );
            plot.push(crate::contract::connected_group("area", "layer_2_marks", vec![crate::href::linked(anchor, path)]));
        }
        // Z1: an area chart's zero rule paints here — above the fills it
        // just drew, below the point-marker halos and main strokes below. A
        // layer on its own scale nests it after the base's whole ink instead.
        if y_sides.is_none() {
            plot.extend(baseline_rules()?);
        }
    }

    // Front-end contract: one datum's structured-tooltip aria-label --
    // shared by the halo/foreground point circles below, the invisible
    // hover-target point, and the value-label text mark (L6 further down),
    // all of which carry the SAME description per production's shared
    // top-level `encoding.description`. `BarTooltip`/`bar_aria_label`
    // (shared with bar -- see its own doc comment) build the exact same
    // role shape `_cartesian_roles` gives every non-wide, non-combo
    // cartesian chart.
    //
    // The color scale's own explicit `scale.domain` for the ORDER role
    // (`emitters/line.py`'s `spatial_color_scale` call sites): `legend_
    // domain` (last-value order) when plain, the dash palette's own
    // insertion order when dashed, or, on a stacked area, the REVERSED
    // baseline order (`emitters/area.py`'s `_area_spatial_order`, "the
    // stacked reversal above still governs the unbound case" -- the same
    // reversal law bar's own vertical stack uses). `None` when there's
    // nothing to rank (single series, or a promoted-but-uncased color
    // channel no swept corpus row exercises).
    let order_domain: Option<Vec<String>> = if !is_multi || spatial_domain.len() <= 1 {
        None
    } else if is_stacked {
        let mut d = baseline_order.clone();
        d.reverse();
        Some(d)
    } else if dashed {
        Some(spatial_domain.clone())
    } else {
        Some(legend_domain.clone())
    };
    // `x_domain_for_temporal[i]`, never the raw `x_domain[i]` a caller might
    // otherwise pass: a year-shaped x's raw value (`"2019.0"` for a
    // warehouse-Decimal year) isn't `parse_iso_date`-parseable, matching the
    // same seam `x_time_ext`/`resolve_vega_x_labels`/`resolve_
    // temporal_labels` already read — `data`'s own lookup keys stay on the
    // RAW domain (the join identity), only this header text reads the
    // anchored one.
    let point_aria = |data_key: &str, i: usize, yv: f64| -> Result<String, String> {
        // A facet panel's marks carry Vega-Lite's own description, one
        // `title: value` row per channel (`StructuredTooltipFeature` does not
        // apply under `multiples`).
        if facet.is_some() {
            let x_text = if x_is_continuous_date {
                crate::families::temporal::default_time_unit_text(&x_domain[i], detected_data_tu)?
            } else {
                x_domain[i].clone()
            };
            let mut rows = vec![
                (x_title_text.as_str(), x_text),
                (y_title_text.as_str(), r.style.tooltip_format.paint(yv)?),
            ];
            if is_multi && spatial_domain.len() > 1 {
                rows.push((color_title.as_str(), data_key.to_string()));
            }
            return Ok(crate::contract::native_label(&rows));
        }
        let header = header_text(&header_dates[i], x_time.as_ref().map(|time| time.at[i]), x_is_standard_date, grain_header_format)?;
        // A wide fold names its series by the measure's display label.
        let series_label = display_label(data_key);
        // A combo's single-series base names itself (`structured_tooltip.py`:
        // `if chart.layers and not series`), so it never collides with the
        // layer's own row at the same x.
        let series_opt = if is_multi && spatial_domain.len() > 1 {
            Some(series_label.as_str())
        } else if has_layers {
            Some(y_title_text.as_str())
        } else {
            None
        };
        let order = order_domain.as_ref().map(|d| crate::contract::tooltip_order_rank(d, data_key));
        // `_stack_group_total`'s gate (`structured_tooltip.py:237-253`):
        // `chart.stack in ("normalize", "zero")` -- both sum same-unit
        // segments, so both get the group-total footer; area's "center"
        // silhouette is NOT commensurable (there is no meaningful group
        // sum to display).
        // A negative value anywhere drops the footer (`_wide_negative`).
        let total = if is_stacked && min_col_floor == 0.0 && matches!(r.stack.as_deref(), Some("zero") | Some("normalize")) {
            Some(("Σ", columns[i].total))
        } else {
            None
        };
        // Only the normalized case also gets the percent-of-total share row
        // (`_stack_group_total`'s own doc comment: "raw segments alone
        // don't imply a meaningful share without the normalization").
        // The column total is already guarded `> 0` by the accumulation-loop
        // refusal above.
        let percent = if is_normalize {
            Some(yv / columns[i].total)
        } else {
            None
        };
        let tt = crate::contract::BarTooltip {
            header: &header,
            series: series_opt,
            order,
            value_title: &y_title_text,
            value_format: &r.style.tooltip_format,
            percent,
            total,
        };
        crate::contract::bar_aria_label(&tt, yv)
    };
    // (data lookup key, paint key) per series -- `data`'s own HashMap keys
    // a single-series chart's rows under `""` (see its own doc comment
    // above), while `series_fill`'s single-series branch reads back
    // whatever paint key it's handed as the literal fill
    // (`single_series_fill` stored as the "series" key) -- the two
    // sentinels differ, so both travel together here rather than reusing
    // `series_points`' own (paint-key-only) tuples.
    let series_keys_for_points: Vec<(String, String)> = if is_multi {
        spatial_domain.iter().map(|s| (s.clone(), s.clone())).collect()
    } else {
        vec![(String::new(), single_fill.clone())]
    };

    // Where one datum's symbols sit, in paint order: a point scale puts one
    // on its x; a band-step row is doubled into the pair at its band's two
    // edges (`apply_step_band`) and drawn in query order.
    let datum_order: Vec<usize> =
        if is_band_step { band_step_walk.clone() } else { (0..x_domain.len()).collect() };
    let datum_xs = |i: usize| -> Vec<f64> {
        if is_band_step {
            vec![band_edge(i, edge_lo), band_edge(i, edge_hi)]
        } else {
            vec![fit_x_at(i)]
        }
    };

    // Where a datum's hit target sits vertically: a stacked area's rides the
    // series' stack top (the raw value on a normalized `[0, 1]` axis lands
    // thousands of pixels off-canvas), anything else its own value. A column
    // with no total has no share (0/0): like the area edge, it sits at the
    // plot top rather than at a NaN transform.
    let datum_y = |data_key: &str, i: usize, yv: f64| -> f64 {
        if !is_stacked {
            return y_scale.position(yv);
        }
        let idx = baseline_order.iter().position(|s| s == data_key).expect("series in baseline_order");
        let col = &columns[i];
        let top = y_scale.position(if is_normalize { col.bounds[idx].1 / col.total } else { offset_at(i) + col.bounds[idx].1 });
        if top.is_nan() { 0.0 } else { top }
    };

    // Point-marker halos (canvas-colored circles under the main
    // lines) — production's own `_line_halo_sublayers` (`emitters/
    // _layers.py:194-253`) paints every halo point's `fill`/`stroke` from
    // `halo_color` (`chart.canvas`) directly, never reading `point_
    // mark.fill` at all: the halo's whole job is knocking out whatever's
    // underneath to match the canvas, which `point_mark.fill`'s own
    // resolved default (a flat `#FFFFFF`, independent of any authored
    // `style.background`) can't stand in for.
    let point_r = match r.style.point_mark.as_ref().and_then(|pm| pm.size) {
        Some(sz) if sz > 0.0 => Some((
            symbol_radius(sz),
            symbol_radius(sz * r.style.line_mark.halo_multiplier),
            canvas.to_string(),
        )),
        _ => None,
    };
    // The point overlays are deliberately UNclipped, matching production:
    // `_layers.py`'s CLIP_TO_PLOT goes on the halo line / hit-target / area
    // backdrop only — "every point overlay keep their reservation instead",
    // which is exactly the `point_reach` ink the fit closure reserves.
    //
    // `halo_multiplier == 0.0` draws nothing here: `_line_halo_sublayers`
    // returns `[]` at that value, so no halo point layer exists to paint. An
    // area's halo (`_area_halo_layers`) is a backdrop fill and a halo line
    // only, never a point layer.
    if r.style.line_mark.halo_multiplier != 0.0 && !draw_areas {
        if let Some((_, halo_r, halo_fill)) = &point_r {
            let mut halos = Vec::new();
            for (data_key, _) in &series_keys_for_points {
                for &i in &datum_order {
                    let Some(Some(yv)) = data.get(&(x_domain[i].clone(), data_key.clone())).copied() else {
                        continue;
                    };
                    let anchor = r.link_open.anchor(href_at(i, data_key));
                    for px in datum_xs(i) {
                        let py = datum_y(data_key, i, yv);
                        let at = Attrs::new().set("transform", format!("translate({px:.3},{py:.3})"));
                        let ring = |at: Attrs| {
                            at.set("fill", halo_fill.as_str())
                                .set("stroke", halo_fill.as_str())
                                .set("stroke-width", HALO_POINT_STROKE_W.to_string())
                        };
                        // A negative `halo_multiplier` makes the symbol size negative: Vega's
                        // symbol path is `MNaN,0`, which paints nothing.
                        let halo = if halo_r.is_nan() {
                            Node::Path(ring(at.set("d", "MNaN,0")))
                        } else {
                            Node::Circle(ring(at.set("r", format!("{halo_r:.3}"))))
                        };
                        halos.push(crate::href::linked(anchor, halo));
                    }
                }
            }
            // `aria: False` on the halo point layer: a hidden mark set.
            plot.push(crate::contract::hidden_mark_group("symbol", "layer_1_marks", halos));
        }
    }

    // A line's tints sit above all of the data's halo (lines and points) and
    // below the rules and the main lines.
    if !draw_areas {
        plot.extend(crate::annotation::nodes(&annotations, crate::annotation::Plane::Background)?);
    }

    // Main lines — a stacked area's perimeter stroke already painted above,
    // combined with its fill; drawing a second top-edge-only stroke here
    // would double it.
    if !is_stacked {
        for (series, runs) in &series_points {
            // A resolved `line_mark.stroke.color` wins over the series fill
            // outright, on EVERY series, whether or not the chart carries a
            // color encoding. Line's `_line_fg_sublayers` (`emitters/
            // _layers.py:283-303`) passes `pin_child_colors=has_color_enc`
            // (`emitters/line.py:611`): whenever a resolved stroke color is
            // present, that pin sets the fg layer's own `encoding.stroke =
            // {value: <color>}` on EVERY series, not just an unencoded one —
            // it overrides the parent `encoding.color`, it doesn't merely
            // fall back when that encoding is absent. Area's `_area_fg_
            // layers` (`_layers.py:766-791`) does the equivalent with
            // `pin_child_colors=color_ch is not None` (`emitters/
            // area.py:599`). A single-series AREA reaches a resolved color
            // even with `stack: "none"`: `resolve/chart/area.py`'s
            // `on_stacked_recipe = is_stacked or is_single_series` swaps
            // `line_mark.stroke` for `marks.area.stacked.stroke` (`theme.
            // background` by default) whether or not the chart is
            // genuinely stacked.
            let color = match &r.style.line_mark.stroke.color {
                Some(c) => c.clone(),
                None => series_fill(series, &palette_domain, &r.series_palette, is_multi, category_scale)?,
            };
            for (d, href) in line_paths(runs, &data_key_of(series)) {
                let anchor = selection.mark(r.link_open.anchor(href));
                let line = Attrs::new()
                    .set("d", d)
                    .set("fill", "none")
                    .set("stroke", crate::href::Anchor::paint(anchor, &color))
                    .set("stroke-width", line_w.to_string())
                    .set("stroke-linecap", cap)
                    .set("stroke-linejoin", join);
                let line = with_dash(line, ink_dash.clone().or_else(|| dash_of(series)));
                plot.push(crate::contract::connected_group("line", "layer_3_marks", vec![crate::href::linked(anchor, Node::Path(line))]));
            }
        }
    }

    // Point markers (line-colored circles at each data point, over the
    // line) — unclipped, same as the halos above.
    if let Some((r_pt, _, _)) = &point_r {
        let mut points = Vec::new();
        for (data_key, paint_key) in &series_keys_for_points {
            let color = series_fill(paint_key, &palette_domain, &r.series_palette, is_multi, category_scale)?;
            for &i in &datum_order {
                let Some(Some(yv)) = data.get(&(x_domain[i].clone(), data_key.clone())).copied() else {
                    continue;
                };
                let aria = if tooltip_labels { Some(point_aria(data_key, i, yv)?) } else { None };
                let label_attrs = crate::contract::aria_attrs(aria.as_deref(), crate::contract::ItemRole::Point)?;
                let anchor = r.link_open.anchor(href_at(i, data_key));
                for px in datum_xs(i) {
                    let py = datum_y(data_key, i, yv);
                    let circle = Attrs::new()
                        .set("transform", format!("translate({px:.3},{py:.3})"))
                        .set("r", format!("{r_pt:.3}"))
                        .set("fill", color.as_str())
                        .set_if(tooltip_labels, "opacity", "1")
                        .extend(label_attrs.clone());
                    points.push(crate::href::linked(anchor, Node::Circle(circle)));
                }
            }
        }
        if tooltip_labels {
            plot.push(crate::contract::mark_group("symbol", "layer_3_marks", points)?);
        } else {
            plot.extend(points);
        }
    }

    // The invisible hover target exists at every datum
    // (`_hover_target_point`, `HOVER_TARGET_SIZE = 320.0`). It announces the
    // datum only when no foreground point does -- a drawn point (a line's
    // `point_mark`, an area's point sub-layer) already announces it, and a
    // second labeled mark would read every value twice. Otherwise it is
    // `aria: False`, one hidden mark set.
    if tooltip_labels {
        let hover_r = symbol_radius(crate::common::HOVER_TARGET_SIZE);
        let announce = point_r.is_none();
        let mut targets = Vec::new();
        for (data_key, paint_key) in &series_keys_for_points {
            let color = series_fill(paint_key, &palette_domain, &r.series_palette, is_multi, category_scale)?;
            for &i in &datum_order {
                let Some(Some(yv)) = data.get(&(x_domain[i].clone(), data_key.clone())).copied() else {
                    continue;
                };
                let aria = if announce { Some(point_aria(data_key, i, yv)?) } else { None };
                let label_attrs = crate::contract::aria_attrs(aria.as_deref(), crate::contract::ItemRole::Point)?;
                let anchor = r.link_open.anchor(href_at(i, data_key));
                for px in datum_xs(i) {
                    let py = datum_y(data_key, i, yv);
                    let target = Attrs::new()
                        .set("transform", format!("translate({px:.3},{py:.3})"))
                        .set("r", format!("{hover_r:.3}"))
                        .set("fill", color.as_str())
                        .set("opacity", "0")
                        .extend(label_attrs.clone());
                    targets.push(crate::href::linked(anchor, Node::Circle(target)));
                }
            }
        }
        plot.push(if announce {
            crate::contract::mark_group("symbol", "layer_3_marks", targets)?
        } else {
            crate::contract::hidden_mark_group("symbol", "layer_3_marks", targets)
        });
    }

    // The mirrored right-edge axis rides an invisible rule layer, one rule per
    // datum down to the plot floor (`features/mirror_axis.py`), painted after
    // the hover targets.
    if tooltip_labels && (mirror_active || facet.is_some_and(|f| f.mirror_band_x)) {
        let ghost_ink = r
            .style
            .axis_y
            .labels
            .font
            .color
            .clone()
            .ok_or("line: axis_y.labels.font.color absent (theme required)")?;
        let mut ghosts: Vec<(String, f64, f64, String, Option<&str>)> = Vec::new();
        for (data_key, paint_key) in &series_keys_for_points {
            let stroke = if is_multi {
                series_fill(paint_key, &palette_domain, &r.series_palette, is_multi, category_scale)?
            } else {
                ghost_ink.clone()
            };
            // A band step doubles every row onto its band's two edges, and so does its ghost.
            let walk: Vec<usize> = if is_band_step { band_step_walk.clone() } else { (0..x_domain.len()).collect() };
            for i in walk {
                let Some(Some(yv)) = data.get(&(x_domain[i].clone(), data_key.clone())).copied() else {
                    continue;
                };
                let xs = if is_band_step { vec![band_edge(i, edge_lo), band_edge(i, edge_hi)] } else { vec![fit_x_at(i)] };
                for x in xs {
                    ghosts.push((point_aria(data_key, i, yv)?, x, datum_y(data_key, i, yv), stroke.clone(), href_at(i, data_key)));
                }
            }
        }
        let rules: Vec<crate::contract::GhostRule> = ghosts
            .iter()
            .map(|(aria, x, y, stroke, href)| crate::contract::GhostRule { aria: Some(aria), x: *x, y: *y, stroke, anchor: r.link_open.anchor(*href) })
            .collect();
        plot.push(crate::contract::ghost_layer(&rules, crate::contract::GhostRun::Down(plot_h))?);
    }

    // A shared-scale layer paints AFTER the base's own ink (halo, main
    // line, points), on the base's own x/y positions — `x_of` looks the
    // layer row's x value up in the shared `x_domain` (production's
    // `_resolve_layer_rows` + `canonicalize_cartesian_x_data` put both
    // series' rows on the SAME x vocabulary), `y_of` is the (possibly
    // niced-past-headroom) shared `y_scale`, unclamped — same as the
    // base's own unclipped main line/point ink. Halo is never drawn for a
    // layer (`suppress_halo=True`, `_layers.py`). The zero rule paints
    // LAST, after this ink, matching the paint-order flip above.
    if has_layers {
        // The clips this chart has defined so far: a second clipped layer of one plot reuses the definition.
        let mut defined_clips: HashSet<String> = HashSet::new();
        // The shared aria of one layer datum: the base header, the layer's name
        // as the series row, one value row in the layer's own tooltip format.
        let datum_aria = |row: &serde_json::Map<String, Value>,
                          layer_x: &str,
                          val: f64,
                          series: &str,
                          title: &str,
                          format: &FormatSlot|
         -> Result<Option<String>, String> {
            if !tooltip_labels {
                return Ok(None);
            }
            let i = domain_position(&x_domain, row, layer_x).expect("x_of placed this row");
            let header = header_text(&header_dates[i], x_time.as_ref().map(|time| time.at[i]), x_is_standard_date, grain_header_format)?;
            let tooltip = crate::contract::BarTooltip {
                header: &header,
                series: Some(series),
                order: None,
                value_title: title,
                value_format: format,
                percent: None,
                total: None,
            };
            crate::contract::bar_aria_label(&tooltip, val).map(Some)
        };
        let label_board_style = payload.board_style.as_ref();
        // A layer on its own scale reads it, drawn after the base's own zero
        // rule, which production nests inside the base's entry.
        let y_of = |index: usize, val: f64| {
            if layer_linear.is_empty() { y_scale.position(val) } else { layer_linear[index].position(val) }
        };
        if y_sides.is_some() {
            plot.extend(baseline_rules()?);
        }
        // Where a bar layer's rect for the x value `x_text` sits on the shared x scale
        // (`left`, `width`): a band's `band_width` share centered, a time bucket's
        // inner 80% (`bandPosition` with VL's 0.5px crisp offsets), or a bar centered
        // on its x with the continuous width ladder.
        let bar_slot = |bl: &OverlayBarLayer, layer_rows: &[serde_json::Map<String, Value>], x_text: &str| -> Result<(f64, f64, f64), String> {
            let layer_x = bl.x.as_deref().unwrap_or(x_field);
            let i = x_domain.iter().position(|d| d == x_text).expect("x_of placed this row");
            // A NaN position is a bar of no width at x 0.
            if x_nan[i] {
                return Ok((0.0, 0.0, 0.0));
            }
            if x_is_band {
                let bw = band_geometry.bandwidth();
                let width = (bw * bl.bar_mark.band_width).min(bw);
                let left = band_geometry.edge(i, 0.0) + (bw - width) / 2.0;
                return Ok((left, width, left + width / 2.0));
            }
            if let (Some(_), Some(extent)) = (detected_data_tu.filter(|_| bucketed_bars), label_extent) {
                let time = x_time.as_ref().expect("a bucketed bar axis reads parsed instants");
                let (ds, de) = (time.pos[i], time.end[i].expect("a bucketed bar axis reads each bucket's end"));
                let at = |days: f64| frac_x_at((days - extent.0 as f64) / (extent.1 - extent.0) as f64);
                let (left, width) = crate::families::bar::bucket_bar_extent(at(ds), at(de), crate::families::bar::DEFAULT_BAND_WIDTH);
                // The value labels sit at the bucket's own start, not on the bar.
                return Ok((left, width, at(ds)));
            }
            // A continuous x: the ladder reads the gap between the layer's own closest x values.
            let time = x_time.as_ref().filter(|_| law.vl_type == VegaType::Temporal);
            let numeric = |text: &str| match time {
                Some(time) => x_domain.iter().position(|d| d == text).map(|i| time.at[i]),
                None => text.parse::<f64>().ok().or_else(|| x.time.get(text).map(|instant| instant.at.floor())),
            };
            let values: Vec<f64> = x_domain.iter().filter_map(|d| numeric(d)).collect();
            let (lo, hi) = values.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), &v| (lo.min(v), hi.max(v)));
            let min_step = crate::families::bar::min_gap(layer_rows.iter().filter_map(|row| numeric(&row_str(row, layer_x))).collect());
            let width = crate::families::bar::ContinuousWidth::of(&bl.bar_mark)?.at(min_step * (plot_w - 2.0 * x_scale_pad) / (hi - lo));
            Ok((fit_x_at(i) - width / 2.0, width, fit_x_at(i)))
        };
        for (index, (overlay, claim)) in overlays.iter().zip(&overlay_plan).enumerate() {
            let (label, domain) = (&claim.label, &claim.values);
            let claimed_fill = &claim.fill.clone().unwrap_or_default();
            let y_of = |val: f64| y_of(index, val);
            let own_clip = layer_axes.get(index).is_some_and(|la| la.cut == DomainCut::Clip) && !matches!(overlay, OverlayLayer::Bar(_));
            let mut layer_nodes: Vec<Node> = Vec::new();
            match overlay {
                OverlayLayer::Line(l) => {
                    let layer_rows =
                        panel_layer_rows(rows, facet.is_some(), x_is_band && l.curve_name() == Some("step"), payload, l.query_name.as_deref(), r.query_name.as_deref(), "line")
                            .expect("validate_line_layers already confirmed this dataset exists");
                    let layer_x = l.x.as_deref().unwrap_or(x_field);
                    let color_promoted = domain.len() > 1;
                    let stack = match (&l.area_mark, l.color.as_deref()) {
                        (Some(_), Some(cf)) => Some(
                            LayerStack::descending(layer_rows, x_field, cf, &l.y)
                                .imputing(layer_rows, crate::families::layers::layer_link(link.as_ref(), (l.query_name.as_deref(), r.query_name.as_deref())))
                                .map_err(|e| format!("line: {e}"))?,
                        ),
                        _ => None,
                    };
                    let subset_marks = |rows: &[serde_json::Map<String, Value>],
                                        series_color: &str,
                                        color_value: Option<&str>|
                     -> Result<Vec<Node>, String> {
                        let series_text = crate::families::layers::layer_series_text(
                            label,
                            color_value.filter(|_| color_promoted),
                        );
                        line_layer_marks(
                            l,
                            rows,
                            |row| domain_position(&x_domain, row, layer_x).map(|i| if x_nan[i] { f64::NAN } else { fit_x_at(i) }),
                            None,
                            y_of,
                            &AreaFrame {
                                free_y: &y_of,
                                plot: (plot_w, plot_h),
                                clip: overlay_clips[index],
                            },
                            false,
                            series_color,
                            canvas,
                            |row, val| {
                                datum_aria(row, layer_x, val, &series_text, label, l.axis_y.tooltip_format_or(&r.style.tooltip_format))
                            },
                            label_board_style,
                            |row, val| {
                                Ok(tooltip_labels.then(|| {
                                    crate::contract::native_label(&[
                                        (l.x.as_deref().unwrap_or(x_title_text.as_str()), row_str(row, layer_x)),
                                        (label.as_str(), val.to_string()),
                                    ])
                                }))
                            },
                            layer_band_step.then(|| StepBand {
                                bandwidth: band_geometry.bandwidth(),
                                first: fit_x_at(0),
                                last: fit_x_at(x_domain.len() - 1),
                            }),
                            0.0,
                            stack.as_ref(),
                            crate::families::layers::layer_link(link.as_ref(), (l.query_name.as_deref(), r.query_name.as_deref())),
                        )
                        .map_err(|e| format!("line: {e}"))
                    };
                    match l.color.as_deref() {
                        Some(cf) => {
                            for (value, subset) in layer_color_groups(layer_rows, cf) {
                                let color = claim.group_color(&layer_scale, cf, value.as_deref(), &subset);
                                layer_nodes.extend(subset_marks(&subset, &color, Some(value.as_deref().unwrap_or(NULL_COLOR_TEXT)))?);
                            }
                        }
                        None => layer_nodes.extend(subset_marks(layer_rows, claimed_fill, None)?),
                    }
                }
                OverlayLayer::Bar(bl) => {
                    let layer_rows =
                        panel_layer_rows(rows, facet.is_some(), false, payload, bl.query_name.as_deref(), r.query_name.as_deref(), "line")
                            .expect("validate_line_layers already confirmed this dataset exists");
                    let layer_x = bl.x.as_deref().unwrap_or(x_field);
                    let marks = placed_bar_layer_marks(bl, layer_rows, layer_x, &nan_x, |row| x_domain.contains(&row_str(row, layer_x)));
                    let color_promoted = domain.len() > 1;
                    let clip = match layer_axes.get(index) {
                        Some(la) => la.cut == DomainCut::Clip,
                        None => y_sides.is_none() && crate::families::layers::baseline_outside_shared_domain(ax_y),
                    };
                    let mut centers: Vec<f64> = Vec::with_capacity(marks.len());
                    for mark in &marks {
                        let x_text = row_str(mark.row, layer_x);
                        let (left, width, label_x) = bar_slot(bl, layer_rows, &x_text)?;
                        centers.push(label_x);
                        let fill = match bl.color.as_deref() {
                            Some(cf) => claim.row_color(&layer_scale, cf, mark.row),
                            None => claimed_fill.clone(),
                        };
                        let color_value = bl.color.as_deref().filter(|_| color_promoted).map(|cf| layer_color_text(mark.row, cf));
                        let series = crate::families::layers::layer_series_text(label, color_value.as_deref());
                        let aria = datum_aria(mark.row, layer_x, mark.value, &series, label, bl.axis_y.tooltip_format_or(&r.style.tooltip_format))?;
                        layer_nodes.extend(
                            crate::families::bar::marks::layer_bar(&y_of, plot_h, clip, bl, mark, (left, width), &fill, aria.as_deref(), crate::families::layers::layer_link(link.as_ref(), (bl.query_name.as_deref(), r.query_name.as_deref())))
                                .map_err(|e| format!("line: {e}"))?,
                        );
                    }
                    layer_nodes.extend(
                        crate::families::bar::marks::layer_bar_labels(
                            bl,
                            layer_rows,
                            &marks,
                            &centers,
                            &y_of,
                            plot_h,
                            |mark| {
                                tooltip_labels.then(|| {
                                    crate::contract::native_label(&[
                                        (bl.x.as_deref().unwrap_or(x_title_text.as_str()), row_str(mark.row, layer_x)),
                                        (label.as_str(), mark.value.to_string()),
                                    ])
                                })
                            },
                            crate::families::layers::layer_link(link.as_ref(), (bl.query_name.as_deref(), r.query_name.as_deref())),
                            (label_board_style, canvas),
                        )
                        .map_err(|e| format!("line: {e}"))?,
                    );
                    if clip {
                        layer_nodes = crate::families::layers::clipped_layer(&mut defined_clips, (plot_w, plot_h), &r.id, layer_nodes);
                    }
                }
                OverlayLayer::Scatter(sl) => {
                    let layer_rows =
                        panel_layer_rows(rows, facet.is_some(), false, payload, sl.query_name.as_deref(), r.query_name.as_deref(), "line")
                            .expect("validate_line_layers already confirmed this dataset exists");
                    let color_promoted = domain.len() > 1;
                    let encoded = |row: &serde_json::Map<String, Value>| {
                        claim.row_color(&layer_scale, sl.color.as_deref().expect("an encoded layer groups by color"), row)
                    };
                    layer_nodes.extend(
                        scatter_layer_marks(
                            sl,
                            layer_rows,
                            sl.size(),
                            1 + index,
                            false,
                            |row| domain_position(&x_domain, row, sl.x.as_deref().unwrap_or(x_field)).map(fit_x_at),
                            None,
                            y_of,
                            |row, val| {
                                let color_value = sl.color.as_deref().filter(|_| color_promoted).map(|cf| layer_color_text(row, cf));
                                let series = crate::families::layers::layer_series_text(label, color_value.as_deref());
                                datum_aria(row, sl.x.as_deref().unwrap_or(x_field), val, &series, label, sl.axis_y.tooltip_format_or(&r.style.tooltip_format))
                            },
                            if sl.color.is_some() { PointColor::Encoded(&encoded) } else { PointColor::Claimed(claimed_fill) },
                            canvas,
                            label_board_style,
                            |_, _| Ok(None),
                            crate::families::layers::layer_link(link.as_ref(), (sl.query_name.as_deref(), r.query_name.as_deref())),
                            overlay_clips[index].then_some((plot_w, plot_h)),
                        )
                        .map_err(|e| format!("line: {e}"))?,
                    );
                }
            }
            if own_clip {
                layer_nodes = crate::families::layers::clipped_layer(&mut defined_clips, (plot_w, plot_h), &r.id, layer_nodes);
            }
            plot.extend(layer_nodes);
            // A layer on its own scale nests its zero rule over its own marks.
            if let (Some(Some(width)), Some(zl)) = (layer_zero_rules.get(index), &yg.threshold) {
                let zc = zl.stroke();
                let gy = layer_linear[index].position(0.0);
                let layer_edge = if layer_axes[index].side == LayerSide::Right { plot_w + value_axis_right_overhang + 0.5 } else { 0.5 };
                let (gx1, gx2) = crate::common::join_rule_to_edges(floor_span, &[layer_edge]);
                plot.push(crate::contract::item_group(
                    "rule", &format!("layer_{}_marks", 1 + overlays.len() + index), crate::contract::ItemRole::RuleMark,
                    crate::contract::Reads::Hidden,
                    |aria| {
                        Node::Line(
                            Attrs::new()
                                .set("x1", format!("{gx1:.3}"))
                                .set("y1", format!("{gy:.3}"))
                                .set("x2", format!("{gx2:.3}"))
                                .set("y2", format!("{gy:.3}"))
                                .set("stroke", zc)
                                .set("stroke-width", width.to_string())
                                .extend(aria),
                        )
                    },
                )?);
            }
        }
        // The zero rule paints LAST on a shared scale.
        if y_sides.is_none() {
            plot.extend(baseline_rules()?);
        }
    }
    // L6: the value-label text layer, drawn last as production layers it —
    // `y − 4` shifted by the `bottom` baseline's own font offset.
    if !value_label_marks.is_empty() {
        let f = value_labels.and_then(|ml| ml.font.as_ref());
        let color = f.and_then(|f| f.color.as_deref()).unwrap_or("#313233");
        let weight = f.map(|f| f.weight_str()).unwrap_or_else(|| "400".into());
        let font_style = f.and_then(|f| f.style.as_deref());
        let mut labels = Vec::new();
        for (i, v, at, text, _, series, fill) in &value_label_marks {
            // Front-end contract: this text mark shares its datum's description
            // with its own point (`charts/line-graphs_10/revenue_labeled`'s value
            // label and point carry byte-identical aria-labels).
            let aria =
                if tooltip_labels { Some(point_aria(series, *i, *v)?) } else { None };
            let label_attrs = crate::contract::aria_attrs(aria.as_deref(), crate::contract::ItemRole::TextMark)?;
            let spot = label_spot(*i);
            let label_x = spot.edge.map_or_else(|| fit_x_at(*i), |frac| band_edge(*i, frac));
            let anchor = r.link_open.anchor(all_hrefs.get(&(x_domain[*i].clone(), series.to_string())).map(String::as_str));
            let label = crate::common::value_label(
                label_x + spot.dx,
                y_scale.position(*at) + spot.dy + measure::baseline_shift(spot.baseline, value_label_size),
                spot.align,
                &value_label_family,
                value_label_size,
                &weight,
                font_style,
                // No authored label color: the text mark inherits the series' color.
                if fill.is_empty() || f.and_then(|f| f.color.as_deref()).is_some() { color } else { fill },
                label_attrs,
                text,
            );
            labels.push(crate::href::linked(anchor, label));
        }
        if tooltip_labels {
            plot.push(crate::contract::value_label_group("layer_4_marks", labels)?);
        } else {
            plot.extend(labels);
        }
    }
    plot.extend(crate::annotation::nodes(&annotations, crate::annotation::Plane::Foreground)?);

    // The single-chart x-axis chrome (tick marks, labels, title) — built
    // once here at this panel's own resolved `plot_w`/temporal ladder, then
    // drawn immediately below at this panel's own `plot_left: 0.0`/
    // `plot_w`/`plot_bottom: plot_h`. `facet_compose::compose_facet` builds
    // this per panel too but only DRAWS it on the last row, at the
    // composer's own footer-band geometry instead — every other panel's
    // own copy stays undrawn (`suppress_x_axis: true` skips the call
    // below), read only for its baked tick/label positions.
    let floor_rule = yg.threshold.as_ref().and_then(|zl| {
        let shared = baseline_rule_data.iter().filter(|(_, fires)| *fires).map(|&(datum, _)| y_scale.position(datum));
        let nested = layer_zero_rules.iter().enumerate().filter(|(_, width)| width.is_some()).map(|(i, _)| layer_linear[i].position(0.0));
        crate::common::rule_on_edge(shared.chain(nested), x_edge.edge_y(plot_h)).then(|| zl.stroke().to_string())
    });
    let x_axis = LineXAxis {
        ad,
        use_band_positions,
        x_font_size,
        x_tick_len,
        x_tick_eff,
        x_label_pad,
        x_axis_drop,
        floor_rule,
        label_rows,
        edge: x_edge,
        x_title_text: x_title_paint.clone(),
        caption: x_axis_caption(&x_title_paint, ax_x, &law, x_domain_for_temporal, plot_w)?,
        title_key: facet.is_none().then_some(crate::contract::LabelKey::X),
        tick_xs: axis_tick_xs,
        rotated_reach: (x_reach > 0.0).then_some(x_reach),
        body: if let Some((ticks, visible)) = bare_axis {
            LineXAxisBody::Bare { ticks, visible, angle: x_angle }
        } else if let Some(tlabels) = temporal_labels {
            if x_angle != 0.0 {
                let xs: Vec<f64> = tlabels.iter().map(|tl| temporal_label_x_at(tl.domain_idx)).collect();
                let format_tu = x_format_tu.expect("a tilted temporal axis carries its format_tu");
                let align = match x_label_align {
                    Some("left") => Some("left"),
                    Some("right") => Some("right"),
                    Some("center") => Some("center"),
                    _ => None,
                };
                LineXAxisBody::Tilted { labels: tlabels, xs, angle: x_angle, format_tu, align }
            } else {
                // L8: an authored `labels.align` wins outright; otherwise
                // L9's position-based flush test (not a domain-index test — a
                // floored weekly domain's raw first opener sits off the range
                // start, so it centers).
                let anchors: Vec<&'static str> = tlabels
                    .iter()
                    .map(|tl| match x_label_align {
                        Some("left") => "start",
                        Some("right") => "end",
                        Some("center") => "middle",
                        _ => temporal_edge_anchor(tl.domain_idx, raw_openers, plot_w),
                    })
                    .collect();
                let xs: Vec<f64> = tlabels.iter().map(|tl| temporal_label_x_at(tl.domain_idx)).collect();
                LineXAxisBody::FlatTemporal { labels: tlabels, xs, anchors }
            }
        } else {
            // Non-temporal: `label_x_at` already folds in `fit_x_at`'s own
            // band-position centering (`use_band_positions`).
            let (domain, xs): (Vec<String>, Vec<f64>) = match &quant_x_ticks {
                Some(ticks) => ticks.iter().map(|(t, f)| (t.clone(), frac_x_at(*f))).unzip(),
                None => (x_shown.clone(), (0..x_domain.len()).map(label_x_at).collect()),
            };
            let angle = nominal_x_angle()?;
            let (align, baseline) = nominal_label_anchor(angle, x_edge, x_label_align);
            let mut left_reach = 0.0_f64;
            for (xv, &x) in domain.iter().zip(&xs) {
                let w = fonts.measure_shaped(xv, x_family, x_font_size)?.min(x_limit);
                left_reach = left_reach.max(
                    -crate::common::rotated_category_label_ink(x, 0.0, w, x_font_size, align, baseline, angle, 0).x1,
                );
            }
            LineXAxisBody::Nominal { labels: domain, xs, left_reach, angle, align: x_label_align }
        },
    };
    if crate::labels_expr::view::active() && facet.is_none() {
        use crate::labels_expr::view::{register_axis, AxisSpec, Orient};
        let band_shift = if use_band_positions { -0.5 } else { 0.0 };
        let anchors = |align: &str, baseline: &str| -> Result<(Option<&'static str>, Option<&'static str>), String> {
            Ok((Some(crate::labels_expr::view::anchor(align)?), Some(crate::labels_expr::view::anchor(baseline)?)))
        };
        let laid = match &x_axis.body {
            LineXAxisBody::Nominal { angle, .. } => {
                let (align, baseline) = nominal_label_anchor(*angle, x_edge, x_label_align);
                Some((band_shift, None, anchors(align, baseline)?, vega_fit::normalize_angle(*angle)))
            }
            LineXAxisBody::Bare { angle, .. } if *angle == 0.0 => Some((0.0, Some(1.0), (None, None), 0.0)),
            LineXAxisBody::FlatTemporal { .. } => match x_label_align {
                None => Some((band_shift, Some(1.0), (None, None), 0.0)),
                Some(align) => Some((band_shift, None, (Some(crate::labels_expr::view::anchor(align)?), None), 0.0)),
            },
            LineXAxisBody::Tilted { angle, align, .. } => {
                let angle = vega_fit::normalize_angle(*angle);
                let (default_align, baseline) = vega_fit::vl_x_label_anchor(angle, true);
                Some((band_shift, None, anchors(align.unwrap_or(default_align), baseline)?, angle))
            }
            _ => None,
        };
        if let Some((shift, flush, (align, baseline), angle)) = laid {
            let offset = x_tick_eff + x_label_pad;
            register_axis("x", AxisSpec { orient: Orient::Bottom, offset, shift, align, baseline, angle, flush, style: ax_x.labels.item_style()? });
        }
    }
    if !suppress_x_axis {
        plot.extend(x_axis.nodes(ax_x, 0.0, plot_w, (x_edge.edge_y(plot_h), x_edge.edge_y(plot_h)), fonts)?);
    }

    let y_axis_origin_x = if y_on_right { plot_w + value_axis_right_overhang + 0.5 } else { 0.5 };
    let y_caption = value_axis_caption(&y_title_paint, y_title_style, ax_y, (y_min, y_max), is_normalize)?;
    // The value axis group's children, and the mirror ghost's own axis group.
    let mut value_axis: Vec<Node> = Vec::new();
    let mut ghost_axis: Vec<Node> = Vec::new();
    // A tick at a painted baseline rule's value is that rule continued.
    let y_rules = yg.threshold.as_ref().filter(|_| crate::common::rule_reaches(floor_span, y_edge_x)).map_or_else(Vec::new, |zl| {
        let at_rules = baseline_rule_data.iter().filter(|(_, fires)| *fires).map(|&(datum, _)| y_scale.position(datum));
        crate::common::value_axis_rules(0.0, at_rules, zl.stroke(), zl.width.unwrap_or(2.0))
    });
    let y_threshold_rules: Vec<_> = crate::common::thresholds(&y_rules);
    // Y tick marks (fit arm, right axis): 6px lines between the plot edge
    // and the label gutter, positions rounded then +0.5 (Vega tickRound).
    // `paint_value_axis: false` (a facet panel that isn't the last column
    // of its row) skips drawing — the gutter stays reserved, unpainted.
    if y_on_right && y_tick_eff > 0.0 && paint_value_axis {
        let tc = crate::common::required_ink(ax_y.ticks.color.as_deref(), "line", "axis_y.ticks.color")?;
        let tw = ax_y.ticks.width.unwrap_or(1.0);
        for &tv in &tick_values {
            let (gy, tc, tw) = crate::common::tick_look(&y_threshold_rules, y_scale.position(tv).round() + 0.5, tc, tw);
            let x1 = plot_w + value_axis_right_overhang + 0.5;
            value_axis.push(tick_line(3, (x1, gy), (x1 + y_tick_eff, gy), tc, tw));
        }
        // Mirror ghost: production's `dict(primary_axis)` overrides only
        // `orient`/`grid`/`title` (`mirror_axis.py:162-165`) — `ticks`/
        // `tickSize` carry over unchanged, so a ticks-visible primary gets a
        // ticks-visible ghost too, same stroke/width, sign-flipped onto the
        // opposite (left) edge's own 0.5 crisp anchor.
        if mirror_active {
            for &tv in &tick_values {
                let (gy, tc, tw) = crate::common::tick_look(&y_threshold_rules, y_scale.position(tv).round() + 0.5, tc, tw);
                ghost_axis.push(tick_line(3, (0.5, gy), (0.5 - y_tick_eff, gy), tc, tw));
            }
        }
    }

    // Left-axis y tick marks (fit and rail-concat arms).
    if !y_on_right && y_tick_eff > 0.0 && paint_value_axis {
        let tc = crate::common::required_ink(ax_y.ticks.color.as_deref(), "line", "axis_y.ticks.color")?;
        let tw = ax_y.ticks.width.unwrap_or(1.0);
        for &tv in &tick_values {
            let (gy, tc, tw) = crate::common::tick_look(&y_threshold_rules, y_scale.position(tv).round() + 0.5, tc, tw);
            value_axis.push(tick_line(3, (0.5 - y_tick_eff, gy), (0.5, gy), tc, tw));
            if mirror_active {
                ghost_axis.push(tick_line(3, (plot_w + 0.5, gy), (plot_w + 0.5 + y_tick_eff, gy), tc, tw));
            }
        }
    }

    // Y-axis labels — right-axis anchors at label width; left-axis at -pad.
    // `paint_value_axis: false` skips drawing (see `val_ticks` above).
    if ax_y.labels.visible.unwrap_or(true) && paint_value_axis {
        let ycol = ax_y.labels.font.color.as_deref().unwrap_or("#626366");
        let ywt = ax_y.labels.font.weight_str();
        let lx = if y_away {
            plot_w + value_axis_right_overhang + 0.5 + y_tick_eff + y_label_pad
        } else if y_on_right {
            plot_w + value_axis_right_overhang + 0.5 + y_tick_eff + y_gutter
        } else {
            0.5 - y_label_pad - y_tick_eff
        };
        // Mirror ghost: the primary's y positions and anchor (its labels too,
        // unless the ghost carries its own `format`), at
        // the primary's own reserved offset sign-flipped onto the opposite
        // edge (`mirror_active` only reaches here past the fit-arm gate
        // above, so the primary is always on the right at this call).
        let y_anchor = if y_away { "start" } else { "end" };
        let (ghost_x, ghost_anchor) = if y_on_right {
            (mirror_ghost_x(y_tick_eff + y_gutter), "end")
        } else if ghost_aligned {
            (plot_w + 0.5 + y_tick_eff + y_gutter, "end")
        } else {
            (plot_w + 0.5 + y_tick_eff + y_label_pad, "start")
        };
        let ghost_texts = ghost_ladder(&tick_values)?.map(|(labels, _)| labels);
        let y_shift = measure::baseline_shift("middle", y_font_sz) + 0.5;
        let face = |anchor| LabelFace { places: 3, anchor, family: y_fam, size: y_font_sz, weight: &ywt, fill: ycol };
        for (i, &tv) in tick_values.iter().enumerate() {
            if y_visible.as_ref().is_some_and(|v| !v[i]) {
                continue;
            }
            let gy = y_scale.position(tv) + y_shift;
            let label = if left_primary { ghost_texts.as_ref().map_or(&formatted_ticks[i], |texts| &texts[i]) } else { &formatted_ticks[i] };
            value_axis.push(flat_label((lx, gy), &face(y_anchor), false, &ax_y.labels.clip(label, fonts)?));
            if mirror_active {
                let ghost_label = ghost_texts.as_ref().map_or(label, |texts| &texts[i]);
                ghost_axis.push(flat_label((ghost_x, gy), &face(ghost_anchor), false, &ax_y.labels.clip(ghost_label, fonts)?));
            }
        }
    }

    let value_axis_start = plot.len();
    // The y title (see common.rs's axis_title_band doc for the measured
    // production constant). `paint_value_axis: false` skips it; a facet
    // panel that isn't the last column of its row reserves the gutter but
    // never draws into it.
    if paint_value_axis {
        let y_label_edge_x = if y_on_right {
            plot_w + value_axis_right_overhang + 0.5 + y_tick_eff + y_gutter
        } else {
            0.5 - y_label_pad - y_tick_eff - max_y_label_w
        };
        value_axis.extend(crate::common::axis_title_rotated(
            &y_title_paint,
            y_title_style,
            y_label_edge_x,
            plot_h / 2.0,
            if y_on_right { 1.0 } else { -1.0 },
            facet.is_none().then_some(crate::contract::LabelKey::Y),
        ));
        plot.extend(crate::contract::axis_group(crate::contract::AxisChannel::Y, &y_caption, (y_axis_origin_x, 0.0), value_axis)?);
    }
    let value_axis_end = plot.len();
    if paint_value_axis && mirror_active {
        let ghost_caption = y_caption.ghost(mirror.as_ref().and_then(|ghost| ghost.format.as_ref()))?;
        plot.extend(crate::contract::axis_group(
            crate::contract::AxisChannel::Y,
            &ghost_caption,
            (if y_on_right { 0.5 } else { plot_w + 0.5 }, 0.0),
            ghost_axis,
        )?);
    }

    // Dual axis: each layer's own axis, on its side.
    for (index, (la, ll)) in layer_axes.iter().zip(&layer_linear).enumerate() {
        let zero_width = layer_zero_rules.get(index).copied().flatten();
        let edge_x = match la.side {
            LayerSide::Right => plot_w + value_axis_right_overhang + 0.5,
            LayerSide::Left => 0.5,
        };
        let ywt = ax_y.labels.font.weight_str();
        plot.extend(layer_axis(
            la,
            ll,
            edge_x,
            (0.0, plot_h),
            &LayerAxisInk {
                font_family: y_fam,
                font_size: y_font_sz,
                font_weight: &ywt,
                font_color: ax_y.labels.font.color.as_deref().unwrap_or("#626366"),
                tick_size: y_tick_eff,
                ticks: &ax_y.ticks,
                title_style: layer_title_style,
                zero_rule: zero_width.zip(yg.threshold.as_ref()).filter(|_| crate::common::rule_reaches(floor_span, edge_x)).map(|(width, zl)| (zl.stroke(), width)),
            },
            // Only the first y title of the chart edits its label: the first
            // layer's stands in for a base title that draws nothing.
            (facet.is_none() && y_title_band == 0.0 && index == 0).then_some(crate::contract::LabelKey::Y),
        )?);
    }

    // What follows the plot group: the rail, the legends and the support_table strip.
    let mut tail: Vec<Node> = Vec::new();

    // Rail labels (multi-series, positioned in the rail panel to the right,
    // further out than the legend when both are shown). Anchored at each
    // series' own LAST non-null data point (production's
    // `last_nonnull_value_per_series` — the actual right endpoint, not a
    // placeholder midpoint), then spread apart by a bidirectional greedy
    // cascade so two close values never overlap (production's
    // `_apply_label_cascade`). Drawn *before* `legend_split` is captured:
    // the rail labels each anchor at `y_scale.position(...)`, the same scale
    // the (unshifted) lines use, so a `position: bottom` strip must not push
    // them down — only a bottom legend, which sits in a disjoint x band from
    // the rail, moves.
    if show_rail {
        // The pane sits at the concat grid's rounded offset (pane0 width +
        // right ink + spacing) in plot coordinates.
        let rel = rail_x_rel.expect("a rail renders through the rail-concat arm");
        let rail_x = pad_l + left_margin + rel;

        let cascaded = rail_cascade(plot_h);

        let mut rail: Vec<Node> = Vec::new();
        for (series, yv) in &cascaded {
            // Vega's middle-baseline shift (round(0.30·fs)).
            let ry = y_scale.position(*yv) + measure::baseline_shift("middle", sl.font_size);
            // The board slot when bound, else `companion_color_for_fill`'s
            // positional index into the companion palette.
            let rcolor = if layered_rail {
                crate::common::companion_for_fill(layer_scale.color_of(series), &r.palette, &sl.dark_companion_palette)
            } else {
                crate::common::series_ink(
                    series,
                    &palette_domain,
                    &r.series_palette,
                    &sl.dark_companion_palette,
                    &sl.pin_companions,
                    category_scale,
                )?
            };
            // `_wrap_hconcat_label_pane` stamps the text mark's own `limit` at
            // the pane's own (capped) width, so Vega ellipsizes a name that
            // would otherwise overrun the pane.
            let label = crate::common::truncate_label(
                &display_label(series),
                rail_pane_w,
                &sl.font_family,
                sl.font_size,
                fonts,
                false,
            )?;
            // Vega's own DEFAULT aria-label for a text mark bound to a
            // nominal field with no custom `description` -- the bare RAW
            // "field: value" pair (never the truncated display text),
            // confirmed against the corpus (`charts/area-charts_1/
            // stacked_area`'s own "channel: Organic"): `show_rail` implies
            // `is_multi` implies `color_field.is_some()`.
            // A wide fold's rail names a synthetic field Vega-Lite's description
            // does not list, so its labels announce nothing.
            let rail_announces = tooltip_labels && r.wide_measures.is_empty();
            let rail_label_attrs = if rail_announces {
                crate::contract::series_label_attrs(color_field.unwrap_or("__label"), series)?
            } else {
                Attrs::new()
            };
            let text = Node::text(
                Attrs::new()
                    .set("x", "0")
                    .set("y", format!("{ry:.3}"))
                    .set("text-anchor", "start")
                    .vega("font-family", sl.font_family.as_str())
                    .set("font-size", sl.font_size.to_string())
                    .set("font-weight", sl.font_weight.as_str())
                    .set("fill", crate::selected::ink(selection.recede_of_series(series), &rcolor))
                    .extend(rail_label_attrs),
                label,
            );
            rail.push(if rail_announces { crate::contract::mark_group("text", "concat_1_marks", vec![text])? } else { text });
        }
        tail.push(Node::group(Attrs::new().set("transform", format!("translate({rail_x:.3},{group_y:.3})")), rail));
        if let Some((legend, cfg, drawn_labels)) = &root_dash_legend {
            let entries: Vec<LegendEntry> = drawn_labels
                .iter()
                .zip(&series_dashes)
                .map(|(label, dash)| LegendEntry {
                    label: label.as_str(),
                    fill: canvas,
                    symbol: Some(LegendSymbol::DashCircle(dash.as_str())),
                    series_key: None,
                    recede: None,
                })
                .collect();
            let x = pad_l + left_margin + (rel + rail_pane_w).ceil() + VEGA_LEGEND_OFFSET;
            tail.extend(drawn_symbol_legend(&entries, legend, cfg, LegendSymbol::Circle, x, group_y, false));
        }
    }
    // Legend (multi-series): a right/bottom column with a title, or a top
    // horizontal entry grid with none — right of the plot, below the
    // x-axis labels, or above the plot itself; coexists with the rail, see
    // the "Keeping the legend anyway" cases in series-label-placement.yml.
    // `legend_pos` already carries whichever anchor the ink pass chose, so
    // this one `drawn_symbol_legend` call is position-agnostic.
    if let Some(lg) = &vega_legend_box {
        let (lx, ly) = legend_pos;
        let (dx, dy) = legend_slide(&r.id, &r.legend.position, lg, (plot_w, plot_h))?;
        tail.extend(drawn_symbol_legend(&legend_entries, lg, &r.legend, LegendSymbol::Circle, group_x + lx + dx, group_y + ly + dy, tooltip_labels && has_layers));
        tail.extend(numeric_legends.nodes_after(lg, &r.legend, fonts, (plot_w, plot_h), (group_x + lx + dx, group_y + ly + dy))?);
    }

    // The composed arm draws the strip inside the canvas it already grew
    // for it (the legacy splice below instead re-wraps a finished SVG).
    if let Some(rows) = &composed_rows {
        let style = r
            .effective_support_table_style
            .as_ref()
            .expect("composed_rows implies effective_support_table_style");
        tail.extend(support_table_strip::composed_rows_nodes(
            rows,
            style,
            ax_y.labels.padding.unwrap_or(6.0),
            plot_w,
            group_x,
            if style.position == "bottom" { group_y + plot_h } else { group_y },
            y_on_right,
            strip_axis_offset,
            r.link.is_some().then_some(r.link_open),
        )?);
    }

    // The splice points a facet composite cuts the printed panel at: after its background rect
    // and before its closing tag.
    let group_attrs = Attrs::new().set("transform", format!("translate({group_x:.3},{group_y:.3})"));
    let cuts = facet.map(|_| frame.cuts(&head, &group_attrs, &plot, &tail, Some(&crate::frame::ValueAxisAt::Plot(value_axis_start..value_axis_end))));
    let svg: Node = {
        let mut body = head;
        body.push(Node::group(group_attrs, plot));
        body.extend(tail);
        frame.svg(body)
    };

    // Every strip shape takes a pad arm that draws its own rows.
    if support_table_config.is_some() && composed_rows.is_none() {
        return Err("line: a support_table strip reached an arm that draws no rows".into());
    }

    // Facet-panel geometry — unused by the single-chart wrapper above
    // (`layout_with_plot` only reads `(svg, PlotRect)`); `line.rs::
    // render_facet`'s own `render_pass` closure reads this off every
    // panel, via `facet_compose::compose_facet`. Mirrors bar's own
    // `PanelGeometry`: the plot rect in this panel's ABSOLUTE canvas frame,
    // unioned with the mark/baseline-rule ink (the same union `run_fit`'s
    // own ink closure already computed for the margin negotiation,
    // recomputed here at the FINAL `plot_w`/`plot_h`) and, when
    // `fold_axis_gutter`, the value-axis gutter `pad_plot` reports as
    // overhang rather than shrinking the plot for.
    let plot_left = group_x;
    let plot_top = group_y;
    let plot_right = plot_left + plot_w;
    let plot_bottom = plot_top + plot_h;
    let mut ink = vega_fit::Bounds::new(plot_left, plot_top, plot_right, plot_bottom);
    // `val_ticks`: `(y position, formatted label)` for every VISIBLE value
    // tick, captured regardless of `paint_value_axis` — a composer paints
    // these itself on whichever panel/edge ends up drawing the shared
    // value axis. In the panel's own ABSOLUTE canvas frame (`plot_top +`),
    // the SAME frame `ink` above is in and bar's own `PanelGeometry.
    // val_ticks` already uses (`bar.rs`'s value-label paint loop) — a
    // composer painting a mirror ghost off this field reads line and bar
    // panels identically.
    let val_ticks: Vec<(f64, String)> = {
        let y_shift = measure::baseline_shift("middle", y_font_sz) + 0.5;
        tick_values
            .iter()
            .enumerate()
            .filter(|&(i, _)| !y_visible.as_ref().is_some_and(|v| !v[i]))
            .map(|(i, &tv)| (plot_top + y_scale.position(tv) + y_shift, formatted_ticks[i].clone()))
            .collect()
    };
    let ghost_labels: Vec<String> = match ghost_ladder(&tick_values)?.filter(|_| facet.is_some() && !left_primary) {
        Some((labels, _)) => labels
            .into_iter()
            .enumerate()
            .filter(|&(i, _)| !y_visible.as_ref().is_some_and(|v| !v[i]))
            .map(|(_, label)| label)
            .collect(),
        None => Vec::new(),
    };
    let geom_point_reach = match r.style.point_mark.as_ref().and_then(|pm| pm.size) {
        Some(sz) if sz > 0.0 => point_mark_reach(sz, r.style.line_mark.halo_multiplier),
        _ => 0.0,
    };
    let last_band = x_domain.len().saturating_sub(1);
    let (geom_datum_x0, geom_datum_x1) = if is_band_step {
        let (lo, hi) = crate::step_band::band_step_edges(plateau_share);
        (x_band_geometry(plot_w).edge(0, lo), x_band_geometry(plot_w).edge(last_band, hi))
    } else if use_band_positions {
        (x_band_geometry(plot_w).edge(0, 0.5), x_band_geometry(plot_w).edge(last_band, 0.5))
    } else {
        (x_scale_pad, (plot_w - x_scale_pad - bucket_slack(plot_w)).max(x_scale_pad))
    };
    // `y_scale.position` is direction-agnostic (G1) — on a descending
    // domain `own_max` lands BELOW `own_min` in pixel space, so the two
    // positions must be sorted before `Bounds::new` (which assumes
    // `y1 <= y2`, top-left semantics) or `union`/`expand` silently drop
    // the overhang at both ends.
    // A stacked area's topmost mark is its tallest column's stacked total, never
    // a raw row value (the fit pass's own `own_lo`/`own_hi`).
    let (ink_lo, ink_hi) = if is_stacked {
        (y_min, if is_normalize { 1.0 } else { max_col_total })
    } else {
        (own_min, own_max)
    };
    let (own_y1, own_y2) = {
        let a = plot_top + y_scale.position(ink_hi);
        let b = plot_top + y_scale.position(ink_lo);
        (a.min(b), a.max(b))
    };
    ink.union(
        &vega_fit::Bounds::new(
            plot_left + geom_datum_x0,
            own_y1,
            plot_left + geom_datum_x1,
            own_y2,
        )
        .expand(line_stroke_w.max(geom_point_reach)),
    );
    // A facet's layers draw this panel's own rows: their marks are cell ink too.
    for (overlay_index, overlay) in overlays.iter().enumerate().filter(|_| facet.is_some()) {
        let (y, reach) = (overlay.y(), overlay.mark_reach());
        // A bar paints from the 0 baseline.
        // A bar clipped to a scale that excludes 0 paints no ink past the plot.
        let paints_from_zero = matches!(overlay, OverlayLayer::Bar(_)) && !overlay_clips[overlay_index];
        let baseline = if paints_from_zero { 0.0 } else { f64::NAN };
        let (lo, hi) = rows
            .iter()
            .filter_map(|row| row_num(row, y))
            .chain((!baseline.is_nan()).then_some(baseline))
            .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| (lo.min(v), hi.max(v)));
        if lo.is_finite() {
            let (a, b) = (plot_top + y_scale.position(hi), plot_top + y_scale.position(lo));
            ink.union(
                &vega_fit::Bounds::new(plot_left + geom_datum_x0, a.min(b), plot_left + geom_datum_x1, a.max(b))
                    .expand(reach),
            );
        }
    }
    let geom_baseline_rule_w = ax_y.grid.threshold.as_ref().map(|z| z.width.unwrap_or(2.0));
    if let Some(rw) = geom_baseline_rule_w {
        for (datum, fires) in baseline_rule_data {
            if !fires {
                continue;
            }
            let ry = plot_top + y_scale.position(datum);
            ink.union(&vega_fit::Bounds::new(plot_left, ry, plot_right, ry).expand(rw));
        }
    }
    for (i, _, at, _, w, _, _) in &value_label_marks {
        let spot = label_spot(*i);
        let x = spot.edge.map_or_else(|| fit_x_at(*i), |frac| band_edge(*i, frac));
        ink.union(&vega_fit::text_ink(
            plot_left + x + spot.dx,
            plot_top + y_scale.position(*at) + spot.dy,
            *w,
            value_label_size,
            spot.align,
            spot.baseline,
        ));
    }
    // A visible x grid gives each facet cell a grid-only bottom axis anchored at
    // `plot_h + offset`; Vega bounds the cell by that anchor, never by the
    // gridline strokes (`axisLayout` skips the grid group), so the reach is the
    // axis offset whatever the grid's width.
    if ax_x.grid.visible.unwrap_or(false) {
        let reach = x_axis_drop - ad;
        let (y1, y2) = match x_edge {
            crate::common::XEdge::Bottom => (plot_bottom, plot_bottom + reach),
            crate::common::XEdge::Top => (plot_top + reach, plot_top),
        };
        ink.union(&vega_fit::Bounds::new(plot_left, y1, plot_right, y2));
    }
    if suppress_x_axis && fold_axis_gutter {
        ink.union(&vega_fit::Bounds::new(
            pad_l,
            plot_top,
            plot_right + value_axis_right_overhang + facet_axis_right_overhang,
            plot_bottom,
        ));
    }
    // An independent scale embeds the value axis in the cell, so a tick label
    // reaching past the plot is cell ink (a shared axis sits in its own role).
    if independent_scale {
        for (_, &tv) in tick_values.iter().enumerate().filter(|&(i, _)| !y_visible.as_ref().is_some_and(|v| !v[i])) {
            facet_compose::union_embedded_label_ink(
                &mut ink,
                plot_top + y_scale.position(tv),
                y_font_sz,
                (plot_left, plot_top, plot_right, plot_bottom),
            );
        }
    }
    if let Some(ab) = crate::annotation::bounds(&annotation_ctx(plot_w, plot_h)) {
        ink.union(&vega_fit::Bounds::new(plot_left + ab.x1, plot_top + ab.y1, plot_left + ab.x2, plot_top + ab.y2));
    }
    let geometry = LinePanelGeometry {
        ink,
        value_title_w: if paint_value_axis && y_on_right && crate::common::y_title_extra(y_title_style, &y_title_paint) > 0.0 {
            crate::common::widest_title_line(y_title_style, &y_title_paint, fonts)?
        } else {
            0.0
        },
        axis_w_overhang: left_margin + facet_axis_right_overhang,
        widest_val_tick_label_width: max_y_label_w,
        val_gutter: if y_away { y_label_pad } else { y_gutter },
        val_tick_size: y_tick_eff,
        val_ticks,
        ghost_labels,
        val_font_size: y_font_sz,
        val_weight: ax_y.labels.font.weight_str().to_string(),
        val_color: ax_y.labels.font.color.as_deref().unwrap_or("#626366").to_string(),
        val_family: y_fam.to_string(),
        val_caption: y_caption,
        axis: x_axis,
        top_axis_overhang: facet_top_axis_overhang,
        facet_legend,
        value_axis_origin: paint_value_axis.then_some((group_x, group_y)),
    };
    Ok(Fitted {
        svg,
        data_box: Rect { x: group_x, y: group_y, width: plot_w, height: plot_h },
        canvas: Rect { x: 0.0, y: 0.0, width: canvas_w, height: canvas_h },
        plot: layout,
        geometry,
        cuts,
    })
}
