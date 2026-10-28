//! The horizontal bar panel: categories down the band axis, the value along the linear one.

use crate::common::{VALUE_AXIS_BREATHING_ROOM, axis_title_text, distinct_values, row_num, row_str};
use crate::diagnostic::Diagnostic;
use crate::families::axis_caption::{category_axis_caption, date_value_caption, value_axis_caption};
use crate::families::cartesian::{BandScale, LinearScale, vl_default_tick_count};
use crate::families::facet_compose::{GhostStyle, PanelGeometry};
use crate::families::layers::{AreaFrame, LayerClaim, LayerScale, LayerStack, OverlayBarLayer, OverlayLayer, PointColor, claim_layers, layer_color_groups, layer_color_promoted, layer_color_text, layer_series_swatches, layer_series_text, merged_legend_title, panel_layer_rows, resolve_layer_rows, union_x_domain};
use crate::families::legend::{LegendEntry, LegendSymbol, drawn_symbol_legend, legend_fit_bounds, legend_origin, legend_room, legend_slide, limited_labels, title_only_legend, vega_legend, with_drawn_labels};
use crate::families::span::RangedBars;
use crate::families::support_table_columns::{ColumnBlock, Side};
use crate::families::temporal::{AuthoredTimeFormat, detect_time_unit, grain_format};
use crate::families::{facet_compose, multiples, span, support_table_columns, support_table_strip};
use crate::frame::{ChartFrame, FrameTitle};
use crate::layout::Rect;
use crate::svg::num_full;
use crate::time_axis::{Grain as AxisGrain, TimeAxis};
use crate::{Padding, Payload, codes, vega_fit};

use measure::FontBook;
use scene::{Attrs, Node};
use serde_json::{Value, json};
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};

use super::axes::{HorizontalValueAxis, flush_edge, flush_threshold, format_tick_labels};
use super::BarResolved;
use super::bind::zero_label;
use super::domain::{BarChartState, CatOrder, CenterStack, SegmentSpans, TopRail, bar_value_extent, base_series, build_chart_domains, build_data_map, Extras, cat_order, category_totals, collapsed_authored_domain, grouped_spans, horizontal_min_height, is_tier, layered_by_sign, nice_shared_scale, niced_rail_domain, outer_segments, stack_label_spans, stack_spans, tier_depths, tier_name, unbaked_rungs, value_domain_and_ticks, with_tiers};
use super::facet::color_1to1_with_cat;
use super::labels::{LabelBuild, LabelDecor, LabelDecors, LabelGeometry, LabelLayer, LabelSpec, LayerLabelFit, LayerLabelFitItem, category_clearance, mark_labels, plain_sites, value_label_layers};
use super::layout::{Drawn, warn_band_too_narrow};
use super::legend::{LayeredBarLegend, LegendState, bar_link_keys, build_legend_state, numeric_legends_of};
use super::marks::{RangedLayerAria, Rounded, bar_passes, baseline_rule_layers, baseline_rules, capped_thickness, collapsed_bars, direction_layers, grouped_overlap_fraction, horizontal_bar_layer_marks, hover_band_for, labels_on_zero_stack, layer_block_rows, layer_day_numbers, link_hrefs, painted_series_bars, ranged_extent, strip_cell_aria, unity_rule};
use super::tooltip::{bar_datum_aria, stack_group_total, tooltip_order_domain};
use super::vertical::refuse_numeric_x_on_bands;

/// Each layer's own rows (`validate_layers` already confirmed a cross-query
/// dataset exists); a facet panel's layer on the base query draws that panel's slice.
pub(super) fn horizontal_layer_rows<'a>(
    payload: &'a Payload,
    r: &BarResolved,
    layers: &'a [OverlayLayer],
    panel_rows: Option<&'a [serde_json::Map<String, Value>]>,
) -> Vec<(&'a OverlayLayer, &'a [serde_json::Map<String, Value>])> {
    layers
        .iter()
        .map(|l| {
            let rows = match panel_rows {
                Some(panel_rows) => panel_layer_rows(panel_rows, true, false, payload, l.query_name(), r.query_name.as_deref(), "bar"),
                None => resolve_layer_rows(payload, l.query_name(), r.query_name.as_deref(), "bar"),
            }
            .expect("validate_layers already confirmed this dataset exists");
            (l, rows)
        })
        .collect()
}

/// Thin wrapper: reads the whole-chart box/rows off `payload` and calls
/// `render_horizontal_panel` — the single-chart path's own "one panel, the
/// whole card" call. A facet composite calls `render_horizontal_panel`
/// directly, once per panel, with that panel's own rows and its own
/// (narrower) box instead of the whole payload's.
pub(super) fn render_horizontal(
    payload: &Payload,
    fonts: &FontBook,
    r: &BarResolved,
    wide_series: &[String],
    layers: &[OverlayLayer],
    support_table: Option<&support_table_strip::SupportTableConfig>,
) -> Result<Drawn, Diagnostic> {
    // The chart's OWN effective background — see `BarResolved::background`.
    let bg = r.background.clone();
    let layers = horizontal_layer_rows(payload, r, layers, None);
    render_horizontal_panel(
        payload.chart_id()?,
        payload.authored_height,
        &payload.rows,
        payload.width,
        payload.height,
        payload.padding.as_ref(),
        &bg,
        fonts,
        r,
        wide_series,
        &layers,
        support_table,
        payload.board_style.as_ref(),
        None,
    )
    .map(|(drawn, _)| drawn)
}

/// Facet-only inputs of one `render_horizontal_panel` call; `None` from the
/// single-chart wrapper. The facet composer owns the title, the legend, the
/// category axis (the `row_header` role) and the value axis (`column_footer`),
/// so a panel paints and reserves cell ink only.
pub(crate) struct HorizontalFacetPanel {
    pub(crate) geometry: PanelGeometry,
    /// `None` when the left edge carries no copy of the category axis.
    pub(crate) row_axis: Option<RowAxisPlacement>,
}

/// Inter's cap height, in ems.
const CAP_HEIGHT_EM: f64 = 0.73;

/// One category label as painted: the first line's baseline, its lines, and the full name when
/// the lines cut it.
struct CatLabel {
    y: f64,
    lines: Vec<String>,
    full: Option<String>,
}

/// One edge's copy of the category axis: where its labels anchor from the plot
/// edge, and how far it reaches outward.
pub(super) struct EdgeCopy {
    pub(super) dx: f64,
    pub(super) anchor: &'static str,
    pub(super) reach: f64,
}

/// Where the category axis paints in the composer's `row_header` role, in the
/// panel's canvas frame.
pub(crate) struct RowAxisPlacement {
    /// How far left of the plot the axis reaches (labels and title).
    pub(crate) reserve: f64,
    pub(crate) label_x: f64,
    pub(crate) label_anchor: &'static str,
    /// The primary axis carries the title; a mirror's ghost has none.
    pub(crate) titled: bool,
    pub(crate) title_x: f64,
}

pub(crate) struct HorizontalFacetOpts<'a> {
    /// The whole chart's rows: the color scale, stack order and legend are
    /// chart-level, as is the value axis' domain under a shared scale.
    pub(crate) chart_rows: &'a [serde_json::Map<String, Value>],
    /// Where the category domain comes from: every panel's rows when the
    /// category scale is shared, this panel's own when it is narrowed.
    pub(crate) cat_rows: &'a [serde_json::Map<String, Value>],
    /// `emitters/bar.py::_is_color_1to1_with_x`, folded over the panels.
    pub(crate) color_one_to_one: bool,
    /// The rows the value axis' domain reads: every panel's under a shared
    /// scale, this panel's own under an independent one.
    pub(crate) value_domain_rows: &'a [serde_json::Map<String, Value>],
    /// `multiples.scale: "independent"`: no zero rule, and the value axis sits
    /// in the cell.
    pub(crate) independent_scale: bool,
    /// A narrowed category scale: Vega draws the category axis inside each
    /// cell instead of the shared `row_header` role.
    pub(crate) cell_cat_axis: bool,
    /// A `center` stack's tallest pile across every panel, which each panel
    /// floats its categories against; `None` once the spec is layered.
    pub(crate) center_stack: Option<CenterStack>,
    /// The both-signs stack across every panel that a `middle` label centers on.
    pub(crate) center_label_stack: CenterStack,
    /// The right edge's copy of the category axis (`axis_y.position: right`, or a mirror's ghost).
    pub(crate) right_edge: RightEdge,
    /// This panel's canonical key, the `panel` an annotation names.
    pub(crate) panel_key: &'a str,
    /// This panel's place in `multiples::regroup` order; keeps clip ids unique.
    pub(crate) panel_ordinal: usize,
    /// What the axis titles wrap against.
    pub(crate) title_boxes: multiples::FacetTitleBoxes,
}

/// Where a panel stands relative to the category axis' `row_footer` copy.
pub(crate) struct RightEdge {
    /// Only the last column of a row paints the copy.
    pub(crate) paint: bool,
    /// A rows-only facet folds the copy into the cell's ink; a wider grid reserves it once.
    pub(crate) fold: bool,
    /// `ceil(cell x2) - plot_w`: where the `row_footer` anchors past the plot.
    pub(crate) overhang: f64,
}

/// One panel's worth of axes + marks + grid + title/legend chrome for a
/// horizontal bar — `rows`/`w`/`payload_height`/`pad`/`bg` are the panel's
/// OWN data and box (the single-chart wrapper above passes the whole
/// `Payload`'s; a facet composite passes one row group and one narrower
/// panel box per call). `payload_height` is the FLOOR `h` derives from
/// (`h = payload_height.max(derived)` below). `render_horizontal` resolves
/// each scatter layer's own rows before the call and passes the support_table
/// config and the board's support-table ink in, so unlike
/// `render_vertical_panel` this needs no whole `&Payload` alongside `rows`.
pub(super) fn render_horizontal_panel(
    chart_id: &str,
    authored_height: Option<f64>,
    rows: &[serde_json::Map<String, Value>],
    w: f64,
    payload_height: f64,
    pad: Option<&Padding>,
    bg: &str,
    fonts: &FontBook,
    r: &BarResolved,
    wide_series: &[String],
    layers: &[(&OverlayLayer, &[serde_json::Map<String, Value>])],
    support_table: Option<&support_table_strip::SupportTableConfig>,
    board_style: Option<&crate::BoardStyle>,
    facet: Option<&HorizontalFacetOpts>,
) -> Result<(Drawn, Option<HorizontalFacetPanel>), Diagnostic> {
    let eff_title_style = crate::common::effective_title_style(
        r.style.title_font.as_ref(),
        r.title_style.as_ref(),
    );
    let title_ref = eff_title_style.as_ref().or(r.title_style.as_ref());
    let pad_l = pad.map(|p| p.left).unwrap_or(0.0);
    let pad_r = pad.map(|p| p.right).unwrap_or(0.0);
    let pad_t = pad.map(|p| p.top).unwrap_or(0.0);
    let pad_b = pad.map(|p| p.bottom).unwrap_or(0.0);
    let view_w = if facet.is_some() { w } else { crate::view_width(w, board_style, crate::ViewScale::Continuous)? };
    let mark_band_width = r.style.mark.band_width;

    let cat_field = &r.x;
    let val_field = &r.y;
    let color_field = r.color.as_deref();
    // Once-per-chart domains/stack-order/palette — see `build_chart_domains`.
    // Horizontal: legend/scale domain display order IS the stack order
    // (production's `apply_legend_entry_order(encoding["color"], order)` —
    // no reversal, see `common::sorted_series_by_stack_order`).
    let state = build_chart_domains(
        facet.map_or(rows, |f| f.chart_rows),
        facet.map_or(r.all_rows.as_deref().unwrap_or(rows), |f| f.cat_rows),
        facet.map(|f| f.color_one_to_one),
        cat_field,
        val_field,
        color_field,
        r.sort.as_ref(),
        r.sorts_by_value_by_default(),
        &r.stack,
        r.style.stack_order.as_deref(),
        &r.wide_measures,
        wide_series,
        r.palette.is_empty(),
    )?;
    let BarChartState {
        mut cat_domain,
        color_domain,
        stack_order,
        is_grouped,
        is_stacked,
        normalize,
        palette_domain,
        ..
    } = state;
    // Each layer's own x column widens the shared band domain, as on the vertical arm.
    let layer_x_columns: Vec<(&[serde_json::Map<String, Value>], &str)> =
        layers.iter().map(|(l, rows)| (*rows, l.x().unwrap_or(cat_field.as_str()))).collect();
    let x_union = union_x_domain(&cat_domain, &layer_x_columns, r.sort.is_some());
    if let Some(layer_only) = &x_union.paint_order {
        crate::families::cartesian::warn_x_domain_paint_order(&r.id, cat_field, layer_only);
    }
    // The label gutter is baked from the base's own categories, before any layer widens them.
    let base_cat_domain = std::mem::replace(&mut cat_domain, x_union.domain);
    // The board's plan for this chart's color field, if it binds one
    // (#7167) — every fill/ink lookup below defers to it over the local
    // palette_domain/color_domain position.
    let category_scale: Option<&crate::common::CategoryColorScale> =
        color_field.and_then(|cf| crate::common::category_scale_for(&r.category_colors, cf));

    // Front-end contract: computed here (ahead of `legend_entries_typed`
    // below) since the legend's own `data-dbt-series` stamp gate needs it.
    // See the module doc above `tooltip_header_text` for the full law. The
    // category axis is a band axis regardless of content, but the tooltip
    // header still reads the x as production does: formatted by the grain its
    // dates detect.
    let tooltip_labels = !r.style.tooltip_format.spec.is_empty();
    // A wide fold names its series by the measure's display label.
    let wide_labels = display_text::wide_measure_labels_for(&r.wide_measures, &r.field_labels)?;
    let numeric_names = r.numeric_series_names(&color_domain)?;
    let series_text = |s: &str| {
        numeric_names.get(s).cloned().unwrap_or_else(|| crate::common::wide_series_display(s, &wide_labels))
    };
    let horizontal_domain_strs: Vec<&str> = cat_domain.iter().map(String::as_str).collect();
    // The STRICT `all_temporal` fact `render_vertical` derives
    // (`x_values_are_standard_dates`): a standard-date x with no detected
    // bucket still headers through the continuous-date fallback.
    let x_is_standard_date =
        crate::families::temporal::x_values_are_standard_dates(&horizontal_domain_strs);
    let tooltip_tu = detect_time_unit(&horizontal_domain_strs);
    let grain_header_format =
        AuthoredTimeFormat::parse("bar", grain_format(&r.style.axis_x.grain_formats, tooltip_tu))?;
    let order_domain =
        tooltip_order_domain(is_stacked, is_grouped, false, &stack_order, &color_domain, &r.legend, category_scale);
    // A wide fold bakes its measure order into the color scale's domain, which
    // the series rank reads back.
    let order_domain =
        order_domain.or_else(|| (is_grouped && !r.wide_measures.is_empty()).then(|| color_domain.clone()));
    // `_stamp_series_keys` (converters/chart.py:367-385): a promoted series
    // row with NO `ROLE_ORDER` rank baked alongside it. Stacked always
    // ranks (`order_domain` is `Some`), so this reduces to "grouped, no
    // board category-color scale".
    let stamp_legend_series = tooltip_labels && is_grouped && order_domain.is_none();

    // ── Canvas height derivation ───────────────────────────────────────────
    // derived = CHROME_PX + n_cats × band_step; payload_height is the floor.
    let ax_x = &r.style.axis_x;
    let cat_font_size = ax_x
        .labels.font.size
        .ok_or("bar: axis_x.labels.font.size absent (theme required)")?;
    let cat_pad = ax_x
        .labels.padding
        .ok_or("bar: axis_x.labels.padding absent (theme required)")?;
    let n_categories = crate::common::count_horizontal_bar_categories(rows, cat_field);
    let derived = horizontal_min_height(r, n_categories)?;
    // A facet panel's height is the composer's, which applied the floor to the whole card.
    let h = if facet.is_some() { payload_height } else { payload_height.max(derived) };

    // ── Layout geometry ────────────────────────────────────────────────────
    // r.legend.visible is already the final decision: production folds
    // endpoint-label-vs-legend suppression into it at resolve
    // (compile/resolve/chart/_axes.py:723-731, _kwargs.py:122-124) — an
    // author who overrides the suppression (`style.legend.visible: true` on
    // a stacked bar) gets `legend.visible == true` from the payload, and
    // re-deriving the suppression here would silently override that choice.
    // A layer forces the legend on even off a colorless single-series base
    // (`force_legend_visible`); a stacked bar's per_series columns name every
    // series by their own headers, so the legend they would repeat is
    // suppressed (`apply_chart_support_table_post_pass`'s `_suppress_series_legend`)
    // — see `build_legend_state`'s own doc comment.
    let has_per_series = support_table.is_some_and(|c| c.entries.iter().any(|e| e.per_series.is_some()));
    let legend = build_legend_state(
        &color_domain,
        &stack_order,
        &palette_domain,
        is_stacked,
        &r.legend,
        &r.series_palette,
        category_scale,
        color_field,
        !layers.is_empty(),
        support_table.is_some_and(|c| c.retires_series_legend()) && is_stacked,
        false,
        &r.field_labels,
        &wide_labels,
        r.numeric_color.is_some(),
    )?;
    let LegendState { shown: legend_shown, title: legend_title, entries: legend_entries } = legend;
    let base_legend_label = axis_title_text(val_field, r.y_label.as_deref(), &r.field_labels)?;
    // The base's own entries come first on the shared scale: one palette slot per color
    // value of a split base, else the one labeled fill.
    let numeric_base = r.numeric_color.is_some();
    let base_scale_entries: Vec<(String, String)> = if numeric_base {
        // The base paints on its own continuous scale; only the layers claim the shared one.
        Vec::new()
    } else if color_domain.is_empty() {
        vec![(base_legend_label.clone(), r.style.single_series_fill.clone())]
    } else {
        color_domain
            .iter()
            .map(|v| {
                let domain = if is_stacked { &palette_domain } else { &color_domain };
                Ok((v.clone(), crate::common::series_color(v, domain, &r.series_palette, category_scale)?))
            })
            .collect::<Result<_, String>>()?
    };
    let mut layer_scale = LayerScale::new(&base_scale_entries, color_field.is_some() && !numeric_base);
    let claims = claim_layers(&mut layer_scale, layers, &r.field_labels)?;
    let numeric_legends = numeric_legends_of(r.numeric_base()?, layers.iter().zip(&claims).map(|((layer, _), claim)| (*layer, claim)), &r.legend, &r.id)?;
    let bar_layers: Vec<(&OverlayBarLayer, &[serde_json::Map<String, Value>])> = layers
        .iter()
        .filter_map(|&(layer, rows)| match layer {
            OverlayLayer::Bar(bl) => Some((bl.as_ref(), rows)),
            OverlayLayer::Line(_) | OverlayLayer::Scatter(_) => None,
        })
        .collect();
    let layered_legend = LayeredBarLegend::new(
        r,
        legend_shown,
        &base_legend_label,
        &legend_entries,
        &base_scale_entries,
        if numeric_base { &[] } else { &color_domain },
        &layer_scale,
        &claims,
        &layers.iter().map(|(l, _)| *l).collect::<Vec<_>>(),
    )?;
    let legend_entries_typed: Vec<LegendEntry> = layered_legend.entries(r, &layer_scale);
    let legend_title = merged_legend_title(
        legend_title,
        r.color.is_some(),
        crate::families::layers::shared_color_fields(layers.iter().map(|(l, rows)| (*l, *rows))),
        &r.legend,
    );
    let legend_labels = limited_labels(&legend_entries_typed, numeric_legends.unlisted_line, &legend_title, &r.legend, fonts, legend_room(&r.legend, facet.is_none().then_some(view_w)))?;
    let series_keys = crate::selected::series_keys(&legend_entries_typed, &bar_link_keys(r, &color_domain, &wide_labels));
    let legend_entries_typed = r.selection.legend(with_drawn_labels(&legend_entries_typed, &legend_labels), &series_keys);

    // Endpoint-label top rail: production's `endpoint_rail_layout` fires
    // "top_rail" for every horizontal-orientation stacked bar with a series
    // color, unconditionally — it never falls back to a side pane the way
    // line/area do, and `r.legend.visible` above already reflects the
    // suppression this causes (an authored override that keeps the legend
    // visible gets both — see `stacked-bar-stress-test`'s cream-theme
    // default and `series-label-placement`'s stacked cases).
    let show_rail = is_stacked && r.style.endpoint_labels.visible;
    // The concat port of that rail (`TopRail`). A stacked base never carries
    // layers on this orientation (`validate_layers` admits a line layer over
    // a single-series bar and scatter layers over a ranged one), so the rail
    // and a layer never meet.
    let top_rail = show_rail.then(|| TopRail::new(&r.style));

    let ax_y = &r.style.axis_y;
    // Zero rule fires only with a visible grid AND a visible threshold (see
    // `zero_w_for`); a normalize stack's 0%/100% top rules are gated
    // identically, not exempted from `grid.visible`.
    // An independent-scale panel draws no zero or threshold rule at all
    // (`BaselineFeature.apply` skips the chart: its pooled verdict would be
    // wrong for a panel with its own domain).
    let independent_scale = facet.is_some_and(|f| f.independent_scale);
    let value_rows = facet.map_or(rows, |f| f.value_domain_rows);
    let rules = if independent_scale {
        None
    } else {
        baseline_rules(ax_y, r.y_start.is_some(), unity_rule(&r, value_rows), !value_rows.is_empty())
    };
    let flush_thr = flush_threshold(ax_y.labels.flush.as_ref())?;
    // A bar between two date columns puts a time scale on the value axis
    // (the day-number rows come from `span::with_day_numbers`).
    // An unauthored tick count is Vega-Lite's `ceil(width/40)`, re-derived at the
    // fitted plot width below.
    let layer_days = layer_day_numbers(layers.iter().map(|(layer, rows)| (*layer, *rows)));
    let date_axis_at = |count: usize| match (r.measure_type.as_deref(), r.y_start.as_deref()) {
        (Some("temporal"), Some(start_field)) => {
            span::date_axis(value_rows, [val_field.as_str(), start_field], &layer_days, count)?
                .map(Some)
                .ok_or_else(|| crate::painted_no_marks(w, json!(chart_id), rows.len()))
        }
        _ => Ok(None),
    };
    let mut date_axis = date_axis_at(ax_y.ticks.count.map_or_else(|| vl_default_tick_count(w), |count| count as usize))?;
    if date_axis.is_none() && collapsed_authored_domain(ax_y, value_rows, &val_field) {
        return Err(crate::painted_no_marks(w, json!(chart_id), rows.len()));
    }
    let (mut linear_domain_min, mut linear_domain_max, mut tick_values) = match &date_axis {
        Some(axis) => (axis.domain.0, axis.domain.1, axis.ticks.clone()),
        None => value_domain_and_ticks(
            value_rows, cat_field, val_field, ax_y, &r.stack, r.stacked_domain_max, false, r.y_start.as_deref(),
            false, labels_on_zero_stack(&r),
        )?,
    };
    // A descending authored pair collapses the horizontal x scale to a single
    // point, so every mark paints with no width.
    if r.y_start.is_none()
        && ax_y.authored_continuous_domain().is_some_and(|(lo, hi)| lo > hi)
        && rows.iter().any(|row| row_num(row, val_field).is_some_and(|v| v != 0.0))
    {
        return Err(crate::painted_no_marks(w, json!(chart_id), rows.len()));
    }
    // A scatter layer shares the bar's one value scale, which then nices both
    // ends (`nice_shared_scale`), as does a line layer.
    if !layers.is_empty() && date_axis.is_none() {
        let (niced_min, niced_max, niced_ticks) = nice_shared_scale(ax_y, linear_domain_min, linear_domain_max);
        if let Some(ticks) = niced_ticks {
            tick_values = ticks;
        }
        (linear_domain_min, linear_domain_max) = (niced_min, niced_max);
    }
    if show_rail && !normalize {
        (linear_domain_max, tick_values) = niced_rail_domain(ax_y, linear_domain_max);
    }
    let mut val_tick_labels = match &date_axis {
        Some(axis) => axis.labels.clone(),
        None => format_tick_labels(&tick_values, ax_y, normalize, "x")?,
    };


    let cat_family = ax_x.labels.font.family.as_deref().unwrap_or("sans-serif");
    let h_labels_expr = ax_x
        .labels
        .expr
        .as_deref()
        .map(|expr| crate::labels_expr::Expr::parse(expr).map(|e| e.on("y")).map_err(|e| format!("bar: labels.expr {expr:?}: {e:?}")))
        .transpose()?;
    let shown_cat = |c: &str| -> Result<String, String> {
        let text = match &h_labels_expr {
            Some(expr) => expr
                .rewrite_band(c, cat_domain.iter().position(|d| d == c).ok_or_else(|| format!("bar: category {c:?} is not on the domain"))?, cat_domain.len())
                .map_err(|e| format!("bar: {e}"))?,
            None => c.to_string(),
        };
        ax_x.labels.cased(&text)
    };
    // An authored expr drops the flip gutter production measured in
    // Python, so Vega's own (shaped) text measure sizes the labels.
    let cat_text_w = |text: &str| {
        if h_labels_expr.is_some() {
            fonts.measure_shaped(text, cat_family, cat_font_size)
        } else {
            fonts.measure(text, cat_family, cat_font_size)
        }
    };
    let max_cat_w = base_cat_domain
        .iter()
        .try_fold(0.0_f64, |acc, s| Ok::<f64, String>(acc.max(cat_text_w(&shown_cat(s)?)?)))?;
    // Hoisted ahead of the plot-rect negotiation: the fit closure below
    // needs each single-series bar's own value (and its label's measured
    // text) to reserve the value-label overhang before the plot width is
    // known.
    let data_map = build_data_map(rows, cat_field, val_field, color_field, r.y_start.is_none())?;
    let extras = Extras::build(rows, cat_field, color_field, r.y_start.is_none(), &r.tooltip)?;
    // A horizontal band category is a nominal field, so a date prints verbatim.
    let link = r.datum_link(cat_field, crate::href::LinkText::Raw);
    // Under a layer, a date x that is not authored `temporal` leaves the base's own marks unlinked.
    let base_link = link.as_ref().filter(|_| layers.is_empty() || ax_x.resolved_authored_type() == Some("temporal") || !x_is_standard_date);
    let hrefs = link_hrefs(base_link, rows, (cat_field, color_field), false)?;
    let strip_links = crate::href::first_datum_links(base_link, rows, cat_field)?;
    let anchor_of = |key: &(String, String)| r.selection.mark(r.link_open.anchor(hrefs.get(key).map(String::as_str)));
    // Rows of one `(category, series)` stack over each other, in row order.
    let tier_depth = tier_depths(&data_map);
    let stack_paint_order = with_tiers(&stack_order, &tier_depth);
    let ranged = r
        .y_start
        .as_deref()
        .map(|start_field| {
            let starts = build_data_map(rows, cat_field, start_field, color_field, false)?;
            RangedBars::new(start_field, &r.field_labels, starts, &data_map, date_axis.is_some())
        })
        .transpose()?;
    // Scatter layers over a ranged bar share its rows, so bar and dots all
    // carry the one combined block (`_span_values`); a layer with its own
    // query leaves the bar its own span label and the dots none.
    let block_layers: Vec<(&OverlayLayer, &LayerClaim)> = layers.iter().zip(&claims).map(|((layer, _), claim)| (*layer, claim)).collect();
    let shares_rows = |query: Option<&str>| query.is_none() || query == r.query_name.as_deref();
    let span_block = match &ranged {
        Some(rb) if tooltip_labels && !layers.is_empty() && layers.iter().all(|(l, _)| shares_rows(l.query_name())) => {
            let (named, bar_swatch) = layer_block_rows(&block_layers, &r.style.single_series_fill);
            let inner = layers
                .iter()
                .find_map(|(layer, _)| match layer {
                    OverlayLayer::Bar(l) => l.y_start.as_ref().map(|start| (l, start)),
                    _ => None,
                })
                .map(|(l, start)| {
                    Ok::<_, String>(span::Ends {
                        end: l.y.clone(),
                        end_title: display_text::field_title(&l.y, &r.field_labels)?,
                        start: start.clone(),
                        start_title: display_text::field_title(start, &r.field_labels)?,
                    })
                })
                .transpose()?;
            let base = span::Ends {
                end: val_field.clone(),
                end_title: axis_title_text(val_field, r.y_label.as_deref(), &r.field_labels)?,
                start: r.y_start.clone().expect("a ranged bar has a start column"),
                start_title: rb.start_title.clone(),
            };
            Some(span::SpanBlock::new(rows, &base, named, inner.as_ref(), bar_swatch.as_deref(), date_axis.is_some()))
        }
        _ => None,
    };
    let layer_series = if r.color.is_none() && span_block.is_some() {
        layer_series_swatches(layers.iter().zip(&claims).map(|((layer, layer_rows), claim)| (*layer, *layer_rows, claim)), &layer_scale, cat_field)
    } else {
        HashMap::new()
    };
    let center = r.stack == "center";
    let center_stack = center.then(|| match facet.and_then(|f| f.center_stack) {
        Some(whole) => whole,
        None => {
            let all_rows = facet.map_or(rows, |f| f.chart_rows);
            CenterStack::over([rows], cat_field, val_field, CenterStack::splits(r.style.mark.border.radius, all_rows, val_field))
        }
    });
    let stroke_color = r.style.mark.border.color.as_deref().unwrap_or("none");
    let stroke_w = r.style.mark.border.width.unwrap_or(0.0);
    let drawn_stroke_w = if stroke_color == "none" { 0.0 } else { stroke_w };
    let value_extent = bar_value_extent(
        rows,
        val_field,
        r.y_start.as_deref(),
        normalize,
        is_stacked,
        is_grouped,
        &data_map,
        &cat_domain,
        &stack_paint_order,
        &color_domain,
        &tier_depth,
        center_stack.as_ref(),
    );
    // Value-label ink for the fit closure — production's `autosize: fit`
    // unions every mark's bounds, including the value-label TEXT marks, so
    // a positive bar's label (production's `dx: 4, align: left` at the
    // value's own scale position) can be the rightmost ink on the whole
    // arm whenever the last visible tick sits short of a headroom-driven
    // domainMax. Grouped/stacked shapes never reach here (`mark_labels`
    // draws single-series labels only).
    let zero_text = zero_label(&r, rows, &LinearScale::new(linear_domain_min, linear_domain_max, 0.0, w))?;
    // Axis titles: category (VL y, chart.x) on the left, measure (VL x,
    // chart.y) on the bottom — see common.rs's axis_title_band for the
    // measured constant. Horizontal-bar channel flip: category title uses
    // ax_x (chart.x's own axis style), matching production's
    // resolve_xy_titles(measure_field, cat_field, ..., ay, ax, ...) call in
    // emitters/bar.py, which threads chart.style.axis_x to the category
    // (VL-y) title and chart.style.axis_y to the measure (VL-x) title.
    let cat_title_text = axis_title_text(cat_field, r.x_label.as_deref(), &r.field_labels)?;
    // A folded val has no single measure field to title from — fall back to
    // the joined measure names (`wide_measures_title`), same as an authored
    // `y_label` always would override either fallback.
    let val_title_text = if r.wide_measures.is_empty() {
        axis_title_text(val_field, r.y_label.as_deref(), &r.field_labels)?
    } else {
        match r.y_label.as_deref() { Some(l) => crate::common::collapse_title_newlines(l), None => crate::common::wide_measures_title(&r.wide_measures, &r.field_labels)? }
    };
    let ((x_extent, x_panels), (y_extent, y_panels)) = facet.map_or(((w, 1.0), (h, 1.0)), |o| o.title_boxes);
    let cat_title_paint = crate::common::wrapped_axis_title(&r.id, "x_label", &cat_title_text, &ax_x.title.font, y_extent, y_panels, fonts)?;
    let val_title_paint = crate::common::wrapped_axis_title(&r.id, "y_label", &val_title_text, &ax_y.title.font, x_extent, x_panels, fonts)?;
    // Vega measures the title itself; it reaches past the plot's ends when
    // wider than the plot.
    let cat_title_w = crate::common::widest_title_line(&ax_x.title, &cat_title_paint, fonts)?;
    let val_title_w = crate::common::widest_title_line(&ax_y.title, &val_title_paint, fonts)?;

    let val_font_size = ax_y.labels.font.size_or(11.0);
    let val_pad = ax_y.labels.padding.unwrap_or(6.0);
    let val_family = ax_y.labels.font.family.as_deref().unwrap_or("sans-serif");

    // ── Plot-rect negotiation ──────────────────────────────────────────────
    // Vega's own fit-margin algebra (vega_fit) on the resolved inputs — the
    // same negotiation vl-convert performs on the compiled spec, so margins
    // land on the identical ceil'd pixels. The endpoint-rail concat is the
    // `TopRail` pane layout on that same arm.
    // Assumed on the fit path (present in the payload, not read): no
    // authored category labels.expr (production drops the flip gutter for
    // one — see `cat_gutter`).
    // Production bakes the measured flip-aligned label gutter straight into
    // VL `labelPadding` — max label width (capped at the 180px label
    // limit) plus breathing room, the labels left-aligned at it — only
    // when `labels.align` is the axis's own side (`emitters/bar.py`:
    // `cat_align == cat_orient`). Otherwise the labels take Vega-Lite's
    // default, right-aligned at −labelPadding with their ink extending
    // left by the measured width (probe: margins 23 and 69 at padding 6
    // against the flip model's 21). The category axis also carries the
    // themed `axis.offset` (ticks.offset). Category ticks are not modeled
    // on this arm in either case (Vega shifts both label models out by
    // tickSize when they draw; no swept theme draws them).
    // Grouped-bar outer band padding does NOT apply to a stacked (color +
    // stack != "none") band — production's `_resolve_overlap_fraction` early-
    // returns for any stack != "none", leaving the band at the plain
    // single-series padding (0/0). Stacked bars therefore reuse
    // `(0.0, 0.0)`, the same constants the single-series branch already uses.
    // A color 1:1 with the category is not grouped either: production's
    // `_is_color_1to1_with_x` suppresses the offset channel, so the band
    // stays plain and each category's one bar takes its own color.
    let one_to_one = is_grouped
        && !is_stacked
        && facet.map_or_else(
            || color_1to1_with_cat(rows, cat_field, color_field.expect("is_grouped implies color")),
            |f| f.color_one_to_one,
        );
    let split_columns = is_grouped && !is_stacked && !one_to_one;
    let (band_pi, band_po) = if split_columns {
        (r.style.grouped.padding_inner, r.style.grouped.padding_outer)
    } else {
        (0.0, 0.0)
    };
    // Every stack segment's and grouped bar's span: what each bar is drawn
    // from and each value label anchors to.
    let category_totals_all =
        if is_stacked { category_totals(rows, cat_field, val_field) } else { HashMap::new() };
    let painted_grouped = (is_grouped && !is_stacked).then(|| {
        let (mut painted, layers) = painted_series_bars(
            &color_domain,
            &cat_domain,
            &data_map,
            ranged.as_ref(),
            rows,
            (cat_field, color_field.expect("a grouped bar has a color field")),
            r.style.mark.border.radius,
            false,
        );
        // The rows stacked over a cell's first paint after it, at its series' slot.
        let tiers: Vec<(usize, String, String)> = painted
            .iter()
            .flat_map(|(si, series, cat)| {
                let depth = tier_depth.get(series).copied().unwrap_or(0);
                (1..=depth).map(move |tier| (*si, tier_name(series, tier), cat.clone()))
            })
            .filter(|(_, series, cat)| data_map.contains_key(&(cat.clone(), series.clone())))
            .collect();
        painted.extend(tiers);
        (painted, layers)
    });
    let spans = match &painted_grouped {
        _ if is_stacked => stack_spans(
            &cat_domain,
            &stack_paint_order,
            &data_map,
            normalize,
            &category_totals_all,
            center_stack.as_ref(),
        ),
        Some((painted, _)) => grouped_spans(painted, &data_map, ranged.as_ref()),
        // A colorless `center` bar is a stack of one series.
        None if center_stack.is_some() => {
            stack_spans(&cat_domain, &[String::new()], &data_map, false, &category_totals_all, center_stack.as_ref())
        }
        None if !tier_depth.is_empty() => {
            stack_spans(&cat_domain, &with_tiers(&[String::new()], &tier_depth), &data_map, false, &category_totals_all, None)
        }
        None => SegmentSpans::new(),
    };
    // A site's band-axis center: the band's middle, or its series' sub-band's.
    let horizontal_center = |bw: f64| {
        let (n_series, one_to_one) = (color_domain.len(), one_to_one);
        move |_: &str, band_y: f64, slot: Option<usize>| match slot {
            None => band_y + bw / 2.0,
            Some(si) => {
                let sub_step = support_table_strip::sub_band_step(bw, n_series, one_to_one);
                band_y + support_table_strip::sub_band_start(sub_step, si, one_to_one) + sub_step / 2.0
            }
        }
    };
    let authored_labels = r.style.mark.labels.as_ref().filter(|ml| ml.visible && r.wide_measures.is_empty());
    let label_layers = {
        let plain_rows: Vec<&serde_json::Map<String, Value>> =
            cat_domain.iter().filter_map(|cat| rows.iter().find(|row| row_str(row, cat_field) == *cat)).collect();
        let label_stack = center.then(|| match facet {
            Some(f) => f.center_label_stack,
            None => CenterStack::over([rows], cat_field, val_field, false),
        });
        let label_spans =
            stack_label_spans(&r, label_stack.as_ref(), &cat_domain, &stack_order, &data_map, &category_totals_all);
        value_label_layers(&LabelBuild {
            r: &r,
            rows,
            plain_rows: &plain_rows,
            cat_field,
            cat_domain: &cat_domain,
            val_field,
            color_field,
            is_grouped,
            is_stacked,
            horizontal: true,
            spans: &spans,
            label_spans: label_spans.as_ref(),
            stack_order: &stack_order,
            totals: &category_totals_all,
            zero_text: zero_text.as_ref(),
            span: ranged.as_ref().filter(|_| authored_labels.is_some()),
            fonts,
            board_style,
            link: link.as_ref(),
        })?
    };
    // A scale pinned away from the 0 baseline clips every bar layer and drops its radius.
    let layer_clips: Vec<bool> = layers.iter().map(|(l, rows)| crate::families::layers::layer_clip(l.layer_type(), l.y(), rows, ax_y, normalize, independent_scale)).collect();
    let bar_layer_clips: Vec<bool> = layers.iter().zip(&layer_clips).filter(|((l, _), _)| matches!(l, OverlayLayer::Bar(_))).map(|(_, &c)| c).collect();
    let bar_layer_marks = bar_layers
        .iter()
        .map(|(bl, bl_rows)| horizontal_bar_layer_marks(bl, bl_rows, bl.x.as_deref().unwrap_or(cat_field), &cat_domain, if bl.color.as_deref() == r.color.as_deref() { &color_domain } else { &[] }))
        .collect::<Result<Vec<_>, String>>()?;
    // A bar layer's value labels, one layer each, laid out like the base's.
    let bar_layer_labels: Vec<Option<LabelLayer>> = bar_layers
        .iter()
        .map(|(bl, bl_rows)| {
            let Some(ml) = bl.bar_mark.labels.as_ref().filter(|ml| ml.visible) else { return Ok(None) };
            let spec = LabelSpec::new(ml, false, bl.label_is_house, bl_rows, &bl.y);
            let sites = plain_sites(bl_rows.iter(), (bl.x.as_deref().unwrap_or(cat_field), &bl.y), crate::families::layers::layer_link(link.as_ref(), (bl.query_name.as_deref(), r.query_name.as_deref())), &spec, None, None)?;
            LabelLayer::new(spec, sites, true, false, false, false, false, fonts, board_style).map(Some)
        })
        .collect::<Result<_, String>>()?;
    // A scatter layer's captions join the fit like any text mark: per row, its
    // measure, caption width, category and spot.
    let layer_caption_ink: Vec<LayerLabelFit> = layers
        .iter()
        .enumerate()
        .filter_map(|(layer, &(l, rows))| match l {
            OverlayLayer::Scatter(sl) if sl.point_mark.labels.visible => Some((layer, sl.as_ref(), rows)),
            OverlayLayer::Bar(_) | OverlayLayer::Line(_) | OverlayLayer::Scatter(_) => None,
        })
        .map(|(layer, sl, rows)| {
            let style = crate::families::layers::LayerLabelStyle::new(
                &sl.point_mark.labels,
                sl.label_is_house,
                rows,
                board_style,
            )?;
            let items = crate::common::valued_rows(rows.iter(), &sl.y)
                .into_iter()
                .map(|(row, val)| {
                    let caption = style.caption(row, val, None).map_err(|e| format!("bar: {e}"))?;
                    Ok(LayerLabelFitItem {
                        val,
                        width: style.width(fonts, &caption.text)?,
                        cat: row_str(row, cat_field),
                        spot: caption.spot,
                    })
                })
                .collect::<Result<Vec<_>, String>>()?;
            Ok(LayerLabelFit { style, items, layer })
        })
        .collect::<Result<Vec<_>, String>>()?;
    // The category floor `h` was grown to, against the height an ancestor authored.
    if let Some(authored) = authored_height {
        let distinct = n_categories;
        let min_height = derived;
        if distinct > 0 && min_height > authored {
            crate::diagnostic::warn(Diagnostic::new(
                &codes::WARN_LAYOUT_MIN_EXCEEDS_HEIGHT,
                [
                    ("authored_height", json!(authored)),
                    ("chart_id", json!(chart_id)),
                    ("min_height", json!(min_height)),
                    ("n_categories", json!(distinct)),
                ],
            ));
        }
    }
    // An endpoint-label rail needs a stacked bar, which never splits its columns, so
    // a railed bar's bands are one series wide and never under the floor.
    let series = if split_columns { color_domain.len() } else { 1 };
    warn_band_too_narrow(chart_id, h, distinct_values(rows, cat_field), series);

    // The category axis follows `axis_y.position` on a horizontal bar.
    let cat_on_right = match ax_y.position.as_deref() {
        Some("left") => false,
        Some("right") => true,
        Some(other) => return Err(format!("bar: axis_y.position {other:?} is neither left nor right").into()),
        None => return Err("bar: axis_y.position absent (theme required)".into()),
    };
    let label_side = if cat_on_right { Side::Right } else { Side::Left };
    // ── Support-table column block (`attach_support_table_columns`) ────────
    let block: Option<ColumnBlock> = match support_table {
        None => None,
        Some(cfg) => {
            let style = r
                .effective_support_table_style
                .as_ref()
                .ok_or("bar: support_table present but effective_support_table_style absent")?;
            let band = BandScale::new(cat_domain.clone(), 0.0, h, band_pi, band_po);
            let centers: Vec<f64> = cat_domain
                .iter()
                .map(|c| band.position(c).map(|y| y + band.bandwidth() / 2.0))
                .collect::<Option<_>>()
                .ok_or("bar: a category has no band")?;
            // Column series: a stacked bar's segments share one band, so a
            // per_series entry gets a column per series, in stack order.
            let series_order: Vec<String> = match (has_per_series, is_stacked) {
                (false, _) => Vec::new(),
                (true, true) => stack_order.clone(),
                (true, false) => color_domain.clone(),
            };
            // A per_series column paints its series' companion ink while
            // `font.color` is `auto`; an explicit color paints every cell.
            let series_ink: Option<Vec<String>> = if has_per_series {
                r.support_table_series_ink
                    .ok_or("bar: a per_series column needs resolved.support_table_series_ink")?
                    .then(|| {
                        series_order
                            .iter()
                            .map(|s| {
                                if is_stacked {
                                    crate::common::series_ink(
                                        s,
                                        &palette_domain,
                                        &r.series_palette,
                                        &r.style.series_label.dark_companion_palette,
                                        &r.style.series_label.pin_companions,
                                        category_scale,
                                    )
                                } else {
                                    let companions = board_style
                                        .and_then(|b| b.dark_companion_palette.as_deref())
                                        .ok_or_else(|| {
                                            "bar: board_style.dark_companion_palette absent (sent with a per_series column)".to_string()
                                        })?;
                                    crate::common::alphabetical_companion_ink(s, &series_order, companions)
                                }
                            })
                            .collect::<Result<Vec<_>, _>>()
                    })
                    .transpose()?
            } else {
                None
            };
            // `sort: null` (an unsorted, colored bar) anchors declare-once
            // numerals in data order; a sorted axis anchors on the first
            // carrying cell of its pinned domain (`_pinned_domain_anchor`),
            // which `cat_domain` already is. A date category is no JSON
            // scalar (`_is_json_scalar_domain`), so its sort pins nothing and
            // every cell spells its own spec.
            let order = cat_order(r.sort.as_ref(), r.sorts_by_value_by_default(), &r.y, &r.stack);
            let anchored = match order {
                CatOrder::Query => true,
                CatOrder::ValueDesc | CatOrder::Field(..) => !x_is_standard_date,
            };
            // A grouped bar's per_series entry puts every series' cell on its
            // own sub-band of the category band (the `yOffset` scale); a color
            // 1:1 with the category carries no offset and the cells share the
            // band center.
            let sub_offsets: Option<Vec<f64>> = (has_per_series && !is_stacked)
                .then(|| support_table_strip::sub_band_center_offsets(band.bandwidth(), series_order.len(), one_to_one));
            let mut built = support_table_columns::build_block(
                &r.id,
                cfg,
                rows,
                cat_field,
                &cat_domain,
                &centers,
                fonts,
                style,
                Side::parse(&style.position)?,
                ax_y.labels.padding.ok_or("bar: axis_y.labels.padding absent (theme required)")?,
                anchored,
                color_field,
                &series_order,
                series_ink.as_deref(),
                sub_offsets.as_deref(),
                r.strip_linked().then_some(&strip_links),
            )?;
            built.describe(&strip_cell_aria(&r, &cat_title_text, &cat_domain, None, color_field)?);
            support_table_columns::warn_block_share(
                &r.id,
                built.footprint(),
                w,
                board_style
                    .and_then(|b| b.column_block_share_warn_ratio)
                    .ok_or("bar: board_style.column_block_share_warn_ratio absent (sent with a support_table)")?,
            );
            Some(built)
        }
    };
    // The header row is one row tall and rides in the top padding; a block
    // on the far side of the plot bumps the right padding by its footprint
    // (`apply_chart_support_table_post_pass`, which counts the block's own ink
    // in the view bounds as well). Only the canvas sees the bump: the title
    // wraps against the card's own padding.
    // Under the endpoint-label rail the chart pane's bounds reach up to the
    // header's ink, and the concat spaces the rail from those bounds.
    let rail_pane_overhang = {
        let header_top = match (&block, r.effective_support_table_style.as_ref()) {
            (Some(block), Some(style)) => {
                let mut b = vega_fit::Bounds::new(0.0, 0.0, 0.0, 0.0);
                block.ink(&mut b, style, 0.0, 1.0);
                -b.y1
            }
            _ => 0.0,
        };
        // A top legend sits inside the chart pane and lifts the pane's bounds.
        let legend_top_reach = match top_rail {
            Some(_) if (legend_shown && (!legend_entries_typed.is_empty() || numeric_legends.len() > 0)) || title_only_legend(legend_shown, &r.legend) => {
                let lg = vega_legend(&legend_entries_typed, false, &legend_title, &r.legend, fonts)?;
                let size = (lg.box_w, lg.box_h());
                let origin = legend_origin(&r.legend.position, (0.0, 0.0), (0.0, 0.0), (0.0, 0.0), size);
                legend_fit_bounds(&r.legend.position, origin, size).map_or(0.0, |reach| -reach.y1)
            }
            _ => 0.0,
        };
        rules.map_or(0.0, |b| b.width).max(header_top).max(legend_top_reach)
    };
    let pad_t = pad_t
        + r.effective_support_table_style.as_ref().filter(|_| block.is_some() && top_rail.is_none()).map_or(0.0, support_table_strip::row_height);
    // A block on the side opposite the category axis is not absorbed by the label gutter.
    let far_footprint = |side: Side| {
        block.as_ref().filter(|b| b.side == side && b.side != label_side).map_or(0.0, ColumnBlock::footprint)
    };
    let canvas_pad_l = far_footprint(Side::Left);
    let canvas_pad_r = pad_r + far_footprint(Side::Right);

    let ax_off = ax_x.ticks.offset.unwrap_or(0.0);
    let cat_limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    let cat_line_height = crate::families::cartesian::rail_line_height(cat_font_size);
    let cat_fits = |text: &str| Ok(fonts.measure(text, cat_family, cat_font_size)? <= cat_limit);
    let cat_cut = |text: &str| crate::common::truncate_label(text, cat_limit, cat_family, cat_font_size, fonts, false);
    // The lines a name paints: wrapped onto a second line when the row holds two (only that line
    // is cut), else the one-line cut.
    let cat_lines = |shown: &str, two_lines: bool| -> Result<Vec<String>, String> {
        if two_lines {
            Ok(crate::families::cartesian::two_line_label(shown, &cat_fits, &cat_cut, true)?
                .expect("a label that may be cut always has a layout"))
        } else {
            Ok(vec![cat_cut(shown)?])
        }
    };
    // Two lines overprint the next row's label unless the row step holds both; the step is the
    // plot height's, so each fit pass decides it afresh.
    let row_holds_two = |plot_h: f64| {
        cat_domain.len() < 2 || {
            let band = BandScale::new(cat_domain.clone(), 0.0, plot_h, band_pi, band_po);
            (band.position_at(1) - band.position_at(0)).abs() >= 2.0 * cat_line_height
        }
    };
    // The widest line the labels paint, as the gutter reserves: a wrapped label is narrower than
    // its one-line width.
    let widest_painted = |texts: &[String], two_lines: bool| -> Result<f64, String> {
        let mut widest = 0.0_f64;
        for text in texts {
            for line in cat_lines(text, two_lines)? {
                widest = widest.max(cat_text_w(&line)?);
            }
        }
        Ok(widest.min(cat_limit))
    };
    let shown_cats: Vec<String> = base_cat_domain.iter().map(|c| shown_cat(c)).collect::<Result<_, _>>()?;
    let cat_w_one_line = max_cat_w.min(cat_limit);
    let cat_w_wrapped = widest_painted(&shown_cats, true)?;
    let cat_w_at = |plot_h: f64| if row_holds_two(plot_h) { cat_w_wrapped } else { cat_w_one_line };
    let val_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    // A label aligned to the axis's own side is the flip model; production maps
    // every other align, and an absent one, to the default.
    let (own_side, opposite_side) = if cat_on_right { ("right", "left") } else { ("left", "right") };
    let cat_flip = ax_x.labels.align_kept(own_side) == Some(own_side);
    let anchor_at = |side: &str, own_aligned: bool| if (side == "left") == own_aligned { "start" } else { "end" };
    // `axis_y.mirror`: a ghost copy of the category axis on the right edge
    // (`mirror_axis.py` reflects the spec's y encoding, the category here).
    let mut ghost = r.style.axis_y.mirror_ghost("bar")?;
    // The ghost copies the category axis, so it wears that axis's case, not the measure axis's.
    if let Some(g) = &mut ghost {
        g.case = ax_x.labels.font.case.clone();
    }
    let mirror_active = ghost.is_some();
    // The ghost's own format reads each category as the number d3 sees (`gate_label_format`).
    let ghost_cats: Vec<String> = match &ghost {
        Some(g) if g.format.is_some() || g.ruler.is_some() => {
            let values: Vec<f64> = cat_domain.iter().map(|cat| crate::common::js_number(cat)).collect();
            g.texts(&values, &cat_domain)?
        }
        _ => cat_domain.iter().map(|cat| ax_x.labels.cased(cat)).collect::<Result<_, _>>()?,
    };
    let ghost_w_one_line = ghost_cats
        .iter()
        .try_fold(0.0_f64, |acc, c| fonts.measure_shaped(c, cat_family, cat_font_size).map(|w| acc.max(w)))?
        .min(cat_limit);
    let ghost_w_wrapped = widest_painted(&ghost_cats, true)?;
    let ghost_w_at = |plot_h: f64| if row_holds_two(plot_h) { ghost_w_wrapped } else { ghost_w_one_line };
    // The label anchor's distance from the axis, and the anchor side.
    let cat_anchor = anchor_at(own_side, cat_flip);
    // A block on the label side widens the axis's own `labelPadding` by its
    // footprint, opening the room between the labels and the plot edge.
    let block_footprint = block.as_ref().filter(|b| b.side == label_side).map_or(0.0, ColumnBlock::footprint);
    // `(label padding, gutter)` at a plot height: the labels' own width decides both.
    let cat_gutters = |plot_h: f64| {
        let cat_w = cat_w_at(plot_h);
        let label_pad = if cat_flip { cat_w + VALUE_AXIS_BREATHING_ROOM } else { cat_pad } + block_footprint;
        (label_pad, if cat_flip { label_pad } else { label_pad + cat_w })
    };
    // How far the category labels move off the plot so an outside value
    // label overhanging the plot's category edge keeps `VALUE_AXIS_BREATHING_ROOM`
    // from its own row's label; set by the ink closure on every pass, read
    // after the fit like `legend_xy`.
    let side_clear = Cell::new(0.0_f64);
    let cat_ws: HashMap<String, f64> = cat_domain
        .iter()
        .map(|c| Ok((c.clone(), fonts.measure(&shown_cat(c)?, cat_family, cat_font_size)?.min(cat_limit))))
        .collect::<Result<_, String>>()?;
    // `center` is deleted from the spec before the mirror copies the axis, so
    // the ghost carries no align and takes Vega's default: start-anchored at
    // the primary's `labelPadding`.
    let ghost_default = mirror_active && ax_x.labels.align.as_deref() == Some("center");
    let ghost_own_aligned = mirror_active && !ghost_default && !cat_flip;
    let ghost_anchor = anchor_at(opposite_side, ghost_own_aligned);
    // A ghost whose copied `labelAlign` is the ghost's own orient would paint
    // into the plot, so `mirror_axis.py` re-measures its `labelPadding` over
    // the labels of the measure ladder rather than the category labels.
    let ghost_gutter = if ghost_default {
        cat_pad
    } else if ghost_own_aligned {
        let val_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
        if ax_y.tick_values.is_empty() {
            let (lo, hi) = rows.iter().filter_map(|row| row_num(row, val_field)).fold(
                (f64::INFINITY, f64::NEG_INFINITY),
                |(lo, hi), v| (lo.min(v), hi.max(v)),
            );
            crate::common::estimated_tick_label_gutter(
                fonts, val_family, val_font_size, val_limit, ax_y.tick_label.as_ref(), ax_y.labels.format.as_ref(), ax_y, lo, hi,
            )?
        } else {
            let family = measure::stand_in_family(val_family);
            let widest = format_tick_labels(&crate::common::drawn_tick_values(ax_y), ax_y, normalize, "x")?
                .iter()
                .try_fold(0.0_f64, |acc, label| fonts.measure(label, family, val_font_size).map(|w| acc.max(w)))?;
            widest.min(val_limit) + VALUE_AXIS_BREATHING_ROOM
        }
    } else {
        // Start-anchored on the far edge, so the labels begin a label gap past the plot.
        VALUE_AXIS_BREATHING_ROOM
    };


    // Vega's parity overlap on the value labels (`labelSeparation` is the
    // theme's min_gap; production forwards none when unset, Vega's 0). The
    // fit closure records the visible mask on every pass; the legacy arms
    // draw every label.
    let val_separation = ax_y.labels.min_gap.unwrap_or(0.0);
    let val_visible: RefCell<Option<Vec<bool>>> = RefCell::new(None);
    // Production compiles one title BLOCK for both orientations
    // (`resolve/chart/bar.py` sets `subtitle_present` with no orientation
    // branch) and runs it through `apply_title_overflow_to_spec` against the
    // padded card — this arm's vconcat top rail included, since production
    // stamps the width on the concat ROOT and defers only the hconcat pane
    // basis (`vega_lite.py`'s `if "hconcat" not in vl`).
    let title = FrameTitle::new(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        r.style.title_font.as_ref(),
        r.title_style.as_ref(),
        w - pad_l - pad_r,
        fonts,
    )?;
    title.warn_truncated(&r.id);
    refuse_numeric_x_on_bands(&r)?;
    let annotation_days: Option<Vec<f64>> = TimeAxis::new(AxisGrain::default(), HashMap::new()).days(&cat_domain);
    // The chart's own ink past the plot, set by the fit's ink closure on every
    // pass; the last write is the drawn size's.
    let annotation_reach = Cell::new(crate::annotation::Reach::default());
    // The annotation maps at a plot origin and size: the categories run down
    // the plot, the measure across it.
    let annotation_ctx = |origin: (f64, f64), pw: f64, ph: f64| {
        use crate::annotation::{AnnotationCtx, AxisMap, Orientation, PlotRect};
        // Layers beside a horizontal bar share its measure scale.
        let y = AxisMap::Linear { domain: (linear_domain_min, linear_domain_max), range: (0.0, pw) };
        let layer_y = vec![y.clone(); layers.len()];
        AnnotationCtx {
            annotations: &r.annotations,
            chart_id: &r.id,
            panel: facet.map(|f| f.panel_key),
            plot: PlotRect { x: origin.0, y: origin.1, w: pw, h: ph },
            reach: annotation_reach.get(),
            x: AxisMap::Band {
                domain: cat_domain.clone(),
                geometry: crate::families::cartesian::BandGeometry::new(cat_domain.len(), 0.0, ph, band_pi, band_po),
                days: annotation_days.clone(),
                range: (0.0, ph),
            },
            y,
            layer_y,
            orientation: Orientation::Horizontal,
            fonts,
            canvas: r.canvas.as_str(),
            clip_id: format!("{}-ann{}", crate::contract::safe_svg_id(&r.id), facet.map_or(0, |f| f.panel_ordinal)),
            occupied: Vec::new(),
        }
    };
    let cat_title_extra = crate::common::y_title_extra(&ax_x.title, &cat_title_paint);
    let cat_axis_in_panel = facet.map_or(true, |f| f.cell_cat_axis);
    let val_axis_in_panel = facet.map_or(true, |f| f.independent_scale);
    // The resolver pins the value domain past the data (headroom), so the plot reaches past its
    // last tick. A mirror's ghost labels hang off the plot's right edge; where nothing paints
    // into the headroom (no value labels, layers or stack), they start a label gap past the
    // last tick or the longest bar instead, not past the empty headroom.
    let headroom_unpainted = mirror_active
        && facet.is_none()
        && !cat_on_right
        && layers.is_empty()
        && !is_stacked
        && label_layers.is_empty()
        && date_axis.is_none();
    let longest_bar = rows.iter().filter_map(|row| row_num(row, val_field)).fold(0.0_f64, f64::max);
    let headroom_pull = |plot_w: f64, ticks: &[f64]| -> f64 {
        if !headroom_unpainted {
            return 0.0;
        }
        let painted_end = ticks.last().copied().unwrap_or(0.0).max(longest_bar);
        plot_w * ((linear_domain_max - painted_end) / (linear_domain_max - linear_domain_min)).max(0.0)
    };
    let (plot_left, plot_top, plot_w, plot_h, vega_title_pos, vega_legend, canvas_w, canvas_h, cell_ink) = {
        // The lines each value label paints on, kept beside the widths the last call returned.
        let val_label_rows = std::cell::RefCell::new(Vec::<usize>::new());
        let measure_labels = |labels: &[String]| -> Result<Vec<f64>, String> {
            *val_label_rows.borrow_mut() = labels.iter().map(|l| crate::text::line_count(l)).collect();
            labels.iter().map(|l| fonts.measure_shaped(l, val_family, val_font_size)).collect()
        };
        let mut val_label_ws = measure_labels(&val_tick_labels)?;
        // Rotated left axis title: ink extends `round(0.21·fs)+round(0.8·fs)`
        // past its anchor at −(labelPadding + titlePadding); bottom title:
        // one text row (with its own baseline-rounding quirk) below
        // labels + titlePadding.
        let val_title_extra = crate::common::x_title_extra(&ax_y.title, &val_title_paint);
        let legend = ((legend_shown && (!legend_entries_typed.is_empty() || numeric_legends.len() > 0)) || title_only_legend(legend_shown, &r.legend))
            .then(|| {
                vega_legend(&legend_entries_typed, numeric_legends.unlisted_line, &legend_title, &r.legend, fonts)
                    .and_then(|lg| numeric_legends.stack_onto(lg, &r.legend, fonts, (w, h)))
            })
            .transpose()?;
        // The legend's origin in plot coordinates, set by the ink closure
        // on every pass; the last write is `fit_plot`'s final layout at the
        // negotiated size, which is the size the chart is drawn at.
        let legend_xy = Cell::new((0.0, 0.0));
        let ink = |tick_values: &[f64], val_label_ws: &[f64], pw: f64, ph: f64, b: &mut vega_fit::Bounds| {
                // The category band: the label gutter (flip-aligned or
                // end-anchored, see `cat_gutter`) and title, shifted out by the
                // axis offset past the plot edge the axis sits on. A facet's
                // shared one is the composer's `row_header`.
                let cat_reach = ax_off + cat_gutters(ph).1 + cat_title_extra;
                let (band_x1, band_x2) = if cat_on_right { (pw, pw + cat_reach) } else { (-cat_reach, 0.0) };
                if cat_axis_in_panel {
                    b.union(&vega_fit::Bounds::new(band_x1, 0.0, band_x2, ph));
                    if cat_title_extra > 0.0 {
                        let x = if cat_on_right { band_x2 } else { band_x1 };
                        b.union(&vega_fit::Bounds::new(x, ph / 2.0 - cat_title_w / 2.0, x, ph / 2.0 + cat_title_w / 2.0));
                    }
                }
                // The ghost keeps the primary's anchor and label padding on the
                // opposite edge, so its text reaches past the plot by that much.
                let (mut axes_x1, mut axes_x2) = (band_x1.min(0.0), band_x2.max(pw));
                if mirror_active && facet.is_none() {
                    let align = if ghost_anchor == "start" { "left" } else { "right" };
                    let ghost_x = if cat_on_right { -(ax_off + ghost_gutter) } else { pw - headroom_pull(pw, tick_values) + ax_off + ghost_gutter };
                    let ghost = vega_fit::text_ink(ghost_x, ph / 2.0, ghost_w_at(ph), cat_font_size, align, "middle");
                    axes_x1 = axes_x1.min(ghost.x1);
                    axes_x2 = axes_x2.max(ghost.x2);
                    b.union(&ghost);
                }
                // Bottom value axis labels (flush-aligned at the range ends);
                // Vega's parity overlap hides every other one under the
                // theme's min_gap, and only the visible labels' ink counts.
                let rows = val_label_rows.borrow();
                let label_ink: Vec<vega_fit::Bounds> = tick_values
                    .iter()
                    .zip(val_label_ws.iter())
                    .zip(rows.iter())
                    .map(|((&tv, &lw), &lines)| {
                        let span = linear_domain_max - linear_domain_min;
                        let x = if span > 0.0 { pw * (tv - linear_domain_min) / span } else { 0.0 };
                        let align = match flush_edge(x, pw, flush_thr) {
                            Some(true) => "left",
                            Some(false) => "right",
                            None => "center",
                        };
                        vega_fit::text_ink_rows(x, ph + val_pad, lw.min(val_limit), val_font_size, align, "top", lines)
                    })
                    .collect();
                let visible = vega_fit::parity_visible(&label_ink, val_separation);
                let mut x_axis_y2 = ph;
                for (ink, _) in label_ink.iter().zip(&visible).filter(|(_, &v)| v) {
                    if val_axis_in_panel {
                        b.union(ink);
                    }
                    x_axis_y2 = x_axis_y2.max(ink.y2);
                }
                val_visible.replace(Some(visible));
                if val_title_extra > 0.0 {
                    x_axis_y2 += val_title_extra;
                    if val_axis_in_panel {
                        b.union(&vega_fit::Bounds::new(pw / 2.0 - val_title_w / 2.0, 0.0, pw / 2.0 + val_title_w / 2.0, x_axis_y2));
                    }
                }
                // The bars' own rects, stroke-expanded (`boundStroke`, full
                // width): a bar ending on the domain max overhangs the plot's
                // right edge by its stroke, and a bar past an authored domain
                // by its whole overflow; the fit reserves both. Only the x
                // extent is unioned; a bar's y stays inside its band.
                let bar_ink = (drawn_stroke_w > 0.0 || ax_y.authored_continuous_domain().is_some()).then(|| {
                    let span = linear_domain_max - linear_domain_min;
                    let at = |v: f64| if span != 0.0 { pw * (v - linear_domain_min) / span } else { 0.0 };
                    let (lo, hi) = value_extent;
                    vega_fit::Bounds::spanning(at(lo), ph / 2.0, at(hi), ph / 2.0).expand(drawn_stroke_w)
                });
                // Annotation text clears the ink so far: axes, titles, marks.
                annotation_reach.set(crate::annotation::Reach::past(b, pw, ph));
                if let Some(ab) = crate::annotation::bounds(&annotation_ctx((0.0, 0.0), pw, ph)) {
                    b.union(&ab);
                }
                if let Some(lg) = legend.as_ref().filter(|_| facet.is_none()) {
                    // `xBounds` is the plot rect ∪ the category band (this arm's
                    // only y axis, and its ghost) ∪ the bars that overhang it;
                    // `yBounds` the plot rect ∪ the bottom value axis. The
                    // rules are bare marks that reach neither: the far rule does
                    // not move a right legend.
                    let x_bounds = bar_ink
                        .as_ref()
                        .map_or((axes_x1, axes_x2), |bars| (axes_x1.min(bars.x1), axes_x2.max(bars.x2)));
                    let size = (lg.box_w, lg.box_h());
                    let x_bounds = (x_bounds.0, x_bounds.1.max(crate::annotation::text_x2(&annotation_ctx((0.0, 0.0), pw, ph))));
                    let origin = legend_origin(&r.legend.position, x_bounds, (0.0, x_axis_y2), (pw, ph), size);
                    legend_xy.set(origin);
                    if let Some(reach) = legend_fit_bounds(&r.legend.position, origin, size) {
                        b.union(&reach);
                    }
                }
                let span = linear_domain_max - linear_domain_min;
                let at = |v: f64| if span > 0.0 { pw * (v - linear_domain_min) / span } else { 0.0 };
                if let Some(bars) = &bar_ink {
                    b.union(bars);
                }
                // A bar layer's marks reach wherever their values do, past the plot
                // when a stack outgrows the shared domain.
                for (((bl, _), marks), _) in bar_layers.iter().zip(&bar_layer_marks).zip(&bar_layer_clips).filter(|(_, &clipped)| !clipped) {
                    let stroke = bl.bar_mark.border.width.unwrap_or(0.0);
                    for m in marks {
                        let (lo, hi) = (at(m.from.min(m.to)), at(m.from.max(m.to)));
                        b.union(&vega_fit::Bounds::new(lo, ph / 2.0, hi, ph / 2.0).expand(stroke));
                    }
                }
                // A line or scatter layer's points on a date axis land on the plot's ends: each
                // symbol's bounds, stroke included, reach past them.
                for (layer, rows) in layers.iter().filter(|(l, _)| r.measure_type.as_deref() == Some("temporal") && l.axis_y().position.is_none()) {
                    let (point, stroked) = match layer {
                        OverlayLayer::Line(l) => (&l.point_mark, true),
                        OverlayLayer::Scatter(l) => (&l.point_mark, l.point_mark.filled == Some(false)),
                        OverlayLayer::Bar(_) => continue,
                    };
                    let reach = point.size.unwrap_or(0.0).sqrt() / 2.0 + if stroked { point.stroke_width } else { 0.0 };
                    for v in rows.iter().filter_map(|row| row_num(row, layer.y())) {
                        b.union(&vega_fit::Bounds::new(at(v) - reach, ph / 2.0, at(v) + reach, ph / 2.0));
                    }
                }
                // Zero rule — boundStroke expands by the FULL stroke width.
                // A zero-anchored negative floor moves the rule OFF the
                // plot's left edge (x=0 is then the domain floor, not
                // zero); the "-1mn" tick's own ink already unions that
                // edge, so only a genuinely interior rule needs its own
                // bounds. The 100% rule, when drawn, is a stroke of its own at
                // its scale position (the far edge for a normalize stack).
                if let Some(rules) = rules {
                    let span = linear_domain_max - linear_domain_min;
                    for (on, datum) in [(rules.zero, 0.0), (rules.unity, 1.0)] {
                        if on {
                            let x = if span > 0.0 { pw * (datum - linear_domain_min) / span } else { 0.0 };
                            b.union(&vega_fit::Bounds::new(x, 0.0, x, ph).expand(rules.width));
                        }
                    }
                }
                for LayerLabelFit { style, items, .. } in &layer_caption_ink {
                    let band = BandScale::new(cat_domain.clone(), 0.0, ph, band_pi, band_po);
                    let linear = LinearScale::new(linear_domain_min, linear_domain_max, 0.0, pw);
                    for LayerLabelFitItem { val, width, cat, spot } in items {
                        let Some(band_y) = band.position(cat) else { continue };
                        b.union(&style.ink(
                            linear.position(*val).clamp(0.0, pw),
                            band_y + band.bandwidth() / 2.0,
                            *width,
                            spot,
                        ));
                    }
                }
                if !label_layers.is_empty() || bar_layer_labels.iter().any(Option::is_some) {
                    let band = BandScale::new(cat_domain.clone(), 0.0, ph, band_pi, band_po);
                    let linear = LinearScale::new(linear_domain_min, linear_domain_max, 0.0, pw);
                    let center = horizontal_center(band.bandwidth());
                    let geom = LabelGeometry { band: &band, center: &center, linear: &linear };
                    for layer in label_layers.iter().chain(bar_layer_labels.iter().flatten()) {
                        layer.ink(&geom, None, b);
                    }
                    if cat_axis_in_panel && !cat_on_right {
                        let clear = label_layers
                            .iter()
                            .chain(bar_layer_labels.iter().flatten())
                            .filter(|layer| !layer.total)
                            .flat_map(|layer| layer.site_inks(&geom, None))
                            .filter(|(_, ink)| ink.x1 < 0.0)
                            .map(|(site, ink)| {
                                let row_w = if cat_flip { cat_ws[&site.cat] } else { 0.0 };
                                category_clearance(-ink.x1, cat_gutters(ph).0 + ax_off - row_w)
                            })
                            .fold(0.0_f64, f64::max);
                        side_clear.set(clear);
                        b.union(&vega_fit::Bounds::new(-(ax_off + cat_gutters(ph).1 + clear + cat_title_extra), ph / 2.0, 0.0, ph / 2.0));
                    }
                }
                // Concat layout stacks the rail pane `spacing` above the
                // chart pane's BOUNDS (the zero rule's expansion is this
                // arm's only ink above the plot; annotation text is refused
                // there), and the rail text's own
                // overhang above its pane is what the title stacks on.
                if let Some(rail) = &top_rail {
                    let pane_top = -rail_pane_overhang;
                    b.union(&vega_fit::Bounds::new(
                        0.0,
                        pane_top - rail.spacing - rail.height - rail.overhang,
                        0.0,
                        0.0,
                    ));
                }
        };
        // A support_table switches the spec to `autosize: pad`: the pane is the
        // spec size verbatim and the block's own ink joins the view bounds the
        // margins ceil around. A facet cell is the same `pad` layout: the plot
        // rect is the composer's `w`x`h` verbatim and ink overflows outward.
        // Everything else fits the outer card.
        let fit = |tick_values: &[f64], val_label_ws: &[f64]| match (&block, r.effective_support_table_style.as_ref()) {
            _ if facet.is_some() => {
                vega_fit::pad_plot(w, h, |pw, ph, b| ink(tick_values, val_label_ws, pw, ph, b), title.ink())
            }
            (Some(block), Some(style)) if top_rail.is_none() => vega_fit::pad_plot(
                w,
                h,
                |pw, ph, b| {
                    ink(tick_values, val_label_ws, pw, ph, b);
                    block.ink(b, style, pw, 1.0);
                },
                title.ink(),
            ),
            (Some(block), Some(style)) => vega_fit::fit_plot_height(
                w,
                h,
                (pad_l, pad_r, pad_t, pad_b),
                |pw, ph, b| {
                    ink(tick_values, val_label_ws, pw, ph, b);
                    block.ink(b, style, pw, ph / h);
                },
                title.ink(),
            ),
            _ => vega_fit::fit_passes(
                view_w,
                h,
                (pad_l, pad_r, pad_t, pad_b),
                if top_rail.is_some() { 1 } else { vega_fit::AUTOSIZE_PADDING_PASSES },
                false,
                |pw, ph, b| ink(tick_values, val_label_ws, pw, ph, b),
                title.ink(),
            ),
        };
        let mut m = fit(&tick_values, &val_label_ws);
        // An unbaked, uncounted value axis takes Vega-Lite's default
        // `tickCount`, `ceil(width/40)`, evaluated at the plot's final
        // width (`unbaked_rungs`). The fit re-runs on the new rungs so the
        // bottom band sees their labels; value-label ink is bottom-only, so
        // `plot_w` (and with it the count) is the same in both passes and
        // one re-fit is exact.
        if let Some(rungs) = unbaked_rungs(ax_y, normalize, false, linear_domain_min, linear_domain_max, m.plot_w) {
            if rungs != tick_values {
                tick_values = rungs;
                val_tick_labels = format_tick_labels(&tick_values, ax_y, normalize, "x")?;
                val_label_ws = measure_labels(&val_tick_labels)?;
                m = fit(&tick_values, &val_label_ws);
            }
        }
        if date_axis.is_some() && ax_y.ticks.count.is_none() {
            let refit = date_axis_at(vl_default_tick_count(m.plot_w))?.expect("a date axis stays one");
            if refit.ticks != tick_values {
                tick_values = refit.ticks.clone();
                val_tick_labels = refit.labels.clone();
                date_axis = Some(refit);
                val_label_ws = measure_labels(&val_tick_labels)?;
                m = fit(&tick_values, &val_label_ws);
            }
        }
        if m.plot_w <= 0.0 && top_rail.is_some() {
            // `_correct_concat_overshoot`: the rail concat's pane gives up the
            // width its ink overshoots the target by; none left is a bug signal.
            let mut probe = vega_fit::Bounds::new(0.0, 0.0, w, h);
            ink(&tick_values, &val_label_ws, w, h, &mut probe);
            let overshoot = pad_l + pad_r + (-probe.x1).ceil().max(0.0) + (probe.x2 - w).ceil().max(0.0);
            return Err(Diagnostic::new(
                &codes::ERR_CONCAT_OVERSHOOT_NONPOSITIVE,
                [
                    ("new_w", json!(w - overshoot)),
                    ("orig_w", json!(w)),
                    ("overshoot", json!(overshoot)),
                    ("target_width", json!(w)),
                ],
            ));
        }
        let stroked_layer_marks = layers.iter().any(|(l, _)| matches!(l, OverlayLayer::Line(_) | OverlayLayer::Scatter(_)));
        collapsed_bars(true, (m.plot_w, m.plot_h), stroked_layer_marks, (chart_id, w), rows, val_field)?;
        // The cell's own bounds in plot coordinates: what a facet grid lays rows out around.
        let mut cell_ink = vega_fit::Bounds::new(0.0, 0.0, m.plot_w, m.plot_h);
        ink(&tick_values, &val_label_ws, m.plot_w, m.plot_h, &mut cell_ink);
        let legend_pos = legend.map(|lg| {
            let (x, y) = legend_xy.get();
            (pad_l + canvas_pad_l + m.left + x, pad_t + m.top + y, lg)
        });
        let canvas = if block.is_some() {
            (
                pad_l + canvas_pad_l + m.left + m.plot_w + m.right + canvas_pad_r,
                pad_t + m.top + m.plot_h + m.bottom + pad_b,
            )
        } else {
            (view_w, h)
        };
        (pad_l + canvas_pad_l + m.left, pad_t + m.top, m.plot_w, m.plot_h, m.title_pos, legend_pos, canvas.0, canvas.1, cell_ink)
    };
    // The fit arms settle the plot height themselves; the block's cells were
    // centered on the nominal `h`.
    let block = block.map(|mut b| {
        b.rescale(plot_h / h);
        b
    });
    let plot_right = plot_left + plot_w;
    let plot_bottom = plot_top + plot_h;
    let linear = LinearScale::new(linear_domain_min, linear_domain_max, 0.0, plot_w);
    if crate::labels_expr::view::active() {
        use crate::labels_expr::view::{register_axis, register_band, register_linear, AxisSpec, Orient, Span};
        register_linear("x", linear_domain_min, linear_domain_max, Span::Width);
        register_band("y", &cat_domain, band_pi, band_po, Span::HeightDown)?;
        if facet.is_none() && ax_y.labels.upright() {
            let spec = AxisSpec { orient: Orient::Bottom, offset: val_pad, shift: 0.0, align: None, baseline: None, angle: 0.0, flush: flush_thr, style: ax_y.labels.item_style()? };
            register_axis("x", spec);
        }
        if facet.is_none() && ax_x.labels.upright() {
            let orient = if cat_on_right { Orient::Right } else { Orient::Left };
            let align = crate::labels_expr::view::anchor(match cat_anchor {
                "start" => "left",
                "end" => "right",
                other => return Err(format!("bar: category axis anchor {other:?} has no label align").into()),
            })?;
            let spec = AxisSpec { orient, offset: cat_gutters(plot_h).0, shift: -0.5, align: Some(align), baseline: None, angle: 0.0, flush: None, style: ax_x.labels.item_style()? };
            register_axis("y", spec);
        }
    }

    let corner_r = r.style.mark.border.radius.unwrap_or(0.0);
    let value_axis = HorizontalValueAxis {
        ticks: tick_values.clone(),
        labels: val_tick_labels.clone(),
        shown: val_tick_labels.iter().map(|l| ax_y.labels.clip(l, fonts)).collect::<Result<_, _>>()?,
        visible: val_visible.take(),
        domain: (linear_domain_min, linear_domain_max),
        flush_threshold: flush_thr,
        pad: val_pad,
        font_size: val_font_size,
        family: val_family.to_string(),
        limit: val_limit,
        color: ax_y.labels.font.color.as_deref().unwrap_or("#626366").to_string(),
        weight: ax_y.labels.font.weight_str(),
        title: val_title_paint.clone(),
        title_key: facet.is_none().then_some(crate::contract::LabelKey::Y),
        caption: match &date_axis {
            Some(axis) => date_value_caption(&val_title_paint, ax_y, axis.domain)?,
            None => value_axis_caption(&val_title_paint, &ax_y.title, ax_y, (linear_domain_min, linear_domain_max), normalize)?,
        },
        left_reach: 0.0,
        right_reach: 0.0,
    }
    .with_reach(plot_w, fonts)?;
    let cat_caption = category_axis_caption(&cat_title_paint, ax_x, &cat_domain)?;

    // ── SVG emission ───────────────────────────────────────────────────────
    let frame = ChartFrame::new(canvas_w, canvas_h, (pad_l, pad_r, pad_t, pad_b), bg, num_full);
    // What precedes the plot group: the title, the legend and the rail.
    let mut head: Vec<Node> = title.nodes(vega_title_pos, (plot_left, plot_top));
    if head.is_empty() {
        head.extend(r.title.as_deref().map(|text| crate::common::plain_title_node(text, title_ref, pad_l, pad_t)));
    }

    let band = BandScale::new(cat_domain.clone(), 0.0, plot_h, band_pi, band_po);

    let swatch_stroke = r.style.mark.border.color.as_deref().unwrap_or("none");
    // A facet's one legend hangs on the composite (`LegendChrome`), so the
    // panel hands it over painted at its own origin instead of placing it.
    let mut facet_legend: Option<facet_compose::FacetLegend> = None;
    if let Some((lx, ly, lg)) = &vega_legend {
        if facet.is_some() {
            let mut nodes: Vec<scene::Node> = drawn_symbol_legend(&legend_entries_typed, lg, &r.legend, LegendSymbol::Square(swatch_stroke), 0.0, 0.0, stamp_legend_series).into_iter().collect();
            nodes.extend(numeric_legends.nodes_after(lg, &r.legend, fonts, (plot_w, plot_h), (0.0, 0.0))?);
            facet_legend = Some(facet_compose::FacetLegend { nodes, box_w: lg.box_w, box_h: lg.box_h() });
        } else {
            let (dx, dy) = legend_slide(&r.id, &r.legend.position, lg, (plot_w, plot_h))?;
            head.extend(drawn_symbol_legend(&legend_entries_typed, lg, &r.legend, LegendSymbol::Square(swatch_stroke), *lx + dx, *ly + dy, stamp_legend_series));
            head.extend(numeric_legends.nodes_after(lg, &r.legend, fonts, (plot_w, plot_h), (*lx + dx, *ly + dy))?);
        }
    }

    // Rail: one label per series, centered on that series' segment within the
    // TOP category row (cat_domain's first entry — the row nearest the rail).
    if show_rail {
        if let Some(top_cat) = cat_domain.first() {
            let totals = if normalize {
                category_totals(rows, cat_field, val_field)
            } else {
                HashMap::new()
            };
            let total = if normalize {
                totals.get(top_cat).copied().ok_or_else(|| {
                    format!("bar: internal error — no stacked total computed for category {top_cat:?}")
                })?
            } else {
                0.0
            };
            let sl = &r.style.series_label;
            let rail = top_rail.as_ref().expect("a drawn rail has its pane");
            // The pane sits `spacing` below the rail's bottom edge, its own
            // top ink (the zero rule, the header) between them.
            let rail_y = plot_top - rail_pane_overhang - rail.spacing - rail.height + rail.text_dy;
            let mut cum = 0.0_f64;
            let mut rail_labels: Vec<Node> = Vec::new();
            for series in &stack_order {
                let raw_val = data_map.get(&(top_cat.clone(), series.clone())).copied().unwrap_or(0.0);
                if raw_val <= 0.0 {
                    continue;
                }
                let seg_val = if normalize { raw_val / total } else { raw_val };
                let seg_start = linear.position(cum);
                let seg_end = linear.position(cum + seg_val);
                cum += seg_val;
                let seg_mid = plot_left + (seg_start + seg_end) / 2.0;
                // Companion ink occupies the fill's palette slot — the
                // board slot when bound, else the local palette_domain
                // position the segment fills use.
                let rcolor = crate::common::series_ink(
                    series,
                    &palette_domain,
                    &r.series_palette,
                    &sl.dark_companion_palette,
                    &sl.pin_companions,
                    category_scale,
                )?;
                let rail_label_attrs = match color_field.filter(|_| tooltip_labels) {
                    Some(field) => crate::contract::series_label_attrs(field, series)?,
                    None => Attrs::new(),
                };
                rail_labels.push(Node::text(
                    Attrs::new()
                        .set("x", format!("{seg_mid:.2}"))
                        .set("y", format!("{rail_y:.2}"))
                        .set("text-anchor", "middle")
                        .vega("font-family", sl.font_family.as_str())
                        .set("font-size", sl.font_size.to_string())
                        .set("font-weight", sl.font_weight.as_str())
                        .set("font-style", sl.font_style.as_str())
                        .set("fill", rcolor.as_str())
                        .extend(rail_label_attrs),
                    series.as_str(),
                ));
            }
            if tooltip_labels {
                head.push(crate::contract::mark_group("text", "concat_0_marks", rail_labels)?);
            } else {
                head.extend(rail_labels);
            }
        }
    }

    let mut plot: Vec<Node> = Vec::new();
    let mut defined_clips: HashSet<String> = HashSet::new();

    // Gridlines: vertical lines at value ticks. The grid axis group carries
    // Vega's +0.5 crispness translate in both coordinates.
    let grid = &ax_y.grid;
    // Only a numeric ranged bar's value scale can exclude zero by design.
    let tear = crate::torn::ValueTear::of(
        grid.torn_baseline.as_ref().filter(|_| ranged.as_ref().is_some_and(|rb| !rb.is_date())),
        linear_domain_min,
        linear_domain_max,
    );
    if grid.visible.unwrap_or(false) {
        let gc = crate::common::required_ink(grid.color.as_deref(), "bar", "grid.color")?;
        let gw = grid.width.unwrap_or(1.0);
        let gd = 0.5;
        let mut lines: Vec<Node> = Vec::new();
        for &tv in &tick_values {
            if tear.as_ref().is_some_and(|t| t.edge == tv) {
                continue;
            }
            // Vega's tickRound (default true) snaps the grid rule's scale
            // position to a whole pixel inside the axis group's crisp
            // translate; the label keeps its exact position (`bar_review`
            // ref: labels 201.5/283.89/366.27/448.66/531.05, grid
            // 201.5/283.5/366.5/448.5/531.5 = floor(label) + 0.5).
            let pos = linear.position(tv);
            let x = pos.round() + gd;
            // A rung on the domain max snaps OUTWARD by up to `1 −
            // frac(plot_w)`, and Vega draws it there (probe: plot 331.6,
            // grid 0/83/166/249/332) — the same whole-pixel allowance the
            // vertical arm's clip carries.
            if x < -0.5 || x > plot_w + 1.0 {
                continue;
            }
            lines.push(Node::Line(
                Attrs::new()
                    .set("x1", format!("{x:.2}"))
                    .set("y1", format!("{gd:.1}"))
                    .set("x2", format!("{x:.2}"))
                    .set("y2", format!("{:.2}", plot_h + gd))
                    .set("stroke", gc)
                    .set("stroke-width", gw.to_string()),
            ));
        }
        plot.push(crate::contract::grid_group(lines));
    }
    if let Some(t) = &tear {
        let gc = crate::common::required_ink(grid.color.as_deref(), "bar", "grid.color")?;
        plot.push(t.node(linear.position(t.edge), plot_w, plot_h, true, gc, grid.width.unwrap_or(1.0)));
    }

    // The rail sits a fixed distance above the plot and reserves no room for annotation text.
    let annotations = annotation_ctx((0.0, 0.0), plot_w, plot_h);
    if top_rail.is_some() {
        crate::annotation::refuse_margin_ink(&annotations, crate::annotation::Reserved::default())?;
    }
    plot.extend(crate::annotation::nodes(&annotations, crate::annotation::Plane::Background)?);

    // Structured-tooltip content for the paint loop below (see the module
    // doc above `tooltip_header_text`); `tooltip_labels`/`order_domain`/
    // `stamp_legend_series` are computed earlier, ahead of the legend.
    let value_title = val_title_text.as_str();
    let tooltip_fmt = &r.style.tooltip_format;
    // The tooltip's group-total footer and `normalize`'s division read the
    // same per-category sum (`category_totals_all`, computed with the spans).
    let has_negative = rows.iter().any(|row| row_num(row, val_field).is_some_and(|v| v < 0.0));

    // What the bar loops below add to each row's label: its description and
    // inherited fill.
    let mut label_decors = LabelDecors::new();
    // The bar layers a ranged bar splits into (`direction_layers`): the zero
    // rule and the value labels number after them.
    let mut bar_layers_painted = 1usize;
    let mut hover_band = hover_band_for(&r, rows, facet.is_some());
    // `(label, x, y, stroke)` of each datum the mirror's ghost rule layer carries.
    let ghosts_on = mirror_active;
    let mut ghost_points: Vec<(Option<String>, f64, f64, String, Option<String>)> = Vec::new();
    let ghost_ink = if ghosts_on {
        ax_y.labels.font.color.clone().ok_or("bar: axis_y.labels.font.color absent (theme required)")?
    } else {
        String::new()
    };

    // The base bars, which annotation text tests for its halo.
    let mut under = Vec::new();
    // Bars
    if is_stacked {
        let bw = band.bandwidth();
        let mark_h = capped_thickness((bw * mark_band_width).min(bw), r.style.mark.max_size);
        let bar_pad = (bw - mark_h) / 2.0;

        for cat in &cat_domain {
            let Some(band_y) = band.position(cat) else { continue };
            let bar_y = band_y + bar_pad;
            let total = if normalize {
                let Some(&t) = category_totals_all.get(cat) else { continue };
                t
            } else {
                0.0 // unused when !normalize
            };
            let outers = outer_segments(&stack_paint_order, cat, &data_map);
            for series in layered_by_sign(&stack_paint_order, cat, &data_map) {
                let key = (cat.clone(), series.clone());
                // A row Vega has no datum for draws nothing.
                let Some(&raw_val) = data_map.get(&key) else { continue };
                let span = spans[&key];
                let aria = {
                    let total_row =
                        if has_negative || center { None } else { Some(stack_group_total(&category_totals_all, cat)?) };
                    bar_datum_aria(
                        tooltip_labels, cat, tooltip_tu, x_is_standard_date, grain_header_format, Some(series_text(base_series(series)).as_str()),
                        order_domain.as_deref().map(|d| crate::contract::tooltip_order_rank(d, base_series(series))),
                        value_title, tooltip_fmt, raw_val,
                        (normalize && !has_negative).then_some(raw_val / total), total_row, extras.of(&key)?, None,
                    )?
                };
                // A zero row still draws its (zero-width) bar and keeps its
                // label, at the stack as it stands (`y0 == y1 == the running
                // sum`).
                if let Some(band) = hover_band.as_mut() {
                    band.push(cat, series, aria.as_deref(), hrefs.get(&key).map(String::as_str), bar_y, mark_h, Some(raw_val));
                }
                label_decors.insert(key.clone(), LabelDecor { aria: aria.clone(), fill: None });
                let x1 = linear.position(span.start);
                let x2 = linear.position(span.end);
                let (seg_start, seg_end) = if x1 <= x2 { (x1, x2) } else { (x2, x1) };
                let seg_w = seg_end - seg_start;
                let fill = crate::common::series_color(base_series(series), &palette_domain, &r.series_palette, category_scale)?;
                if ghosts_on {
                    ghost_points.push((aria.clone(), x2, bar_y + mark_h / 2.0, fill.clone(), hrefs.get(&key).cloned()));
                }
                // Corner radius flips with sign (Vega's mixed-sign layer
                // split — see `bar_passes`'s doc comment and the vertical
                // arm's mirrored `Rounded::Negative`): a positive segment rounds
                // its right (far) end, a negative one its left.
                let outer = if raw_val < 0.0 { outers.1 } else { outers.0 } == Some(series);
                under.push(crate::annotation::DataMark::rect(seg_start, bar_y, seg_w, mark_h));
                plot.extend(bar_passes(true, seg_start, bar_y, seg_w, mark_h, &fill, r.style.mark.opacity, stroke_color, stroke_w, if outer { corner_r } else { 0.0 },
                    Rounded::by_sign(raw_val), false, None, aria.as_deref(), anchor_of(&key))?);
            }
        }
    } else if is_grouped {
        let outer_bw = band.bandwidth();
        // 1:1 color: one full-band bar per category (`one_to_one`) at the
        // theme's band_width. Otherwise the sub-band mark takes production's
        // overlap fraction: `height: {band: 1 + f}` (a gap or touching),
        // which Vega-Lite offsets by `(1 − band)/2` of the CATEGORY scale's
        // bandwidth — not the sub-step's — or, for a positive overlap, a
        // `(1 + f) × bandwidth('yOffset')` size centered on the sub-band.
        let sub_step = support_table_strip::sub_band_step(outer_bw, color_domain.len(), one_to_one);
        let (mark_h, sub_pad) = if one_to_one {
            let h = capped_thickness((sub_step * mark_band_width).min(sub_step), r.style.mark.max_size);
            (h, (sub_step - h) / 2.0)
        } else {
            let f = grouped_overlap_fraction(r.style.overlap.as_ref(), color_domain.len())?;
            let b = (1.0 + f).max(0.05);
            let h = capped_thickness(b * sub_step, r.style.mark.max_size);
            let centered = f > 0.0 || r.style.mark.max_size.is_some();
            (h, if centered { (sub_step - h) / 2.0 } else { (1.0 - b) / 2.0 * outer_bw })
        };

        // Bars span the zero rule to their value, whichever side of it that
        // is; a mixed-sign measure paints its `>= 0` sub-layer and then its
        // `< 0` one (`sign_layers`).
        let (painted, layers) = painted_grouped.as_ref().expect("a grouped bar's painted series come with its spans");
        bar_layers_painted = *layers;
        for (si, series, cat) in painted {
            let fill = crate::common::series_color(base_series(series), &color_domain, &r.series_palette, category_scale)?;
            let Some(band_y) = band.position(cat) else { continue };
            let sub_start = band_y + support_table_strip::sub_band_start(sub_step, *si, one_to_one);
            let bar_y = sub_start + sub_pad;
            let key = (cat.clone(), series.clone());
            let Some(&val) = data_map.get(&key) else { continue };
            let start = ranged.as_ref().map(|rb| rb.starts.get(&key).copied());
            let (bar_x, bar_w, ends) = match (start, spans.get(&key)) {
                (Some(None), _) => continue,
                (Some(Some(start)), _) => ranged_extent(|v| linear.position(v), val, Some(start)),
                // A row stacked over its cell's earlier ones spans from where they end.
                (None, Some(span)) if is_tier(series) => ranged_extent(|v| linear.position(v), span.end, Some(span.start)),
                (None, _) => ranged_extent(|v| linear.position(v), val, None),
            };
            let order = order_domain.as_deref().map(|d| crate::contract::tooltip_order_rank(d, base_series(series)));
            let plain = || {
                bar_datum_aria(
                    tooltip_labels, cat, tooltip_tu, x_is_standard_date, grain_header_format, Some(series_text(base_series(series)).as_str()), order,
                    value_title, tooltip_fmt, val, None, None, extras.of(&key)?, None,
                )
            };
            let aria = match (&ranged, start) {
                (Some(rb), Some(Some(start))) => rb.aria(
                    tooltip_labels, cat, tooltip_tu, x_is_standard_date, grain_header_format, Some(series_text(base_series(series)).as_str()), order,
                    (value_title, tooltip_fmt), (start, val), plain,
                )?,
                _ => plain()?,
            };
            label_decors.insert(key.clone(), LabelDecor { aria: aria.clone(), fill: Some(fill.clone()) });
            if let Some(band) = hover_band.as_mut() {
                band.push(cat, series, aria.as_deref(), hrefs.get(&key).map(String::as_str), bar_y, mark_h, Some(val));
            }
            if ghosts_on {
                ghost_points.push((aria.clone(), linear.position(val).clamp(0.0, plot_w), bar_y + mark_h / 2.0, fill.clone(), hrefs.get(&key).cloned()));
            }
            under.push(crate::annotation::DataMark::rect(bar_x, bar_y, bar_w, mark_h));
            plot.extend(bar_passes(true, bar_x, bar_y, bar_w, mark_h, &fill, r.style.mark.opacity, stroke_color, stroke_w, corner_r, ends, true, None, aria.as_deref(), anchor_of(&key))?);
        }
    } else {
        let bw = band.bandwidth();
        let mark_h = capped_thickness((bw * mark_band_width).min(bw), r.style.mark.max_size);
        let bar_pad = (bw - mark_h) / 2.0;
        // Zero-anchored measure axis: bars split by sign at the zero rule's
        // own x, not a hardcoded local origin — a negative floor moves the
        // zero rule off the plot's left edge (see `value_domain_and_ticks`).
        let zero_x = linear.position(0.0);
        // A ranged bar's rises and falls split into layers of their own on a
        // numeric axis; a date span has no sign (`bar_span_signs` reads only
        // numeric cells), so a gantt stays one layer that rounds both ends.
        let numeric_span = date_axis.is_none();
        let painted: Vec<&String> = match &ranged {
            Some(rb) if numeric_span => {
                let rises = |cat: &&String| {
                    let key = ((*cat).clone(), String::new());
                    rb.starts.get(&key).zip(data_map.get(&key)).is_some_and(|(start, end)| end >= start)
                };
                let (ordered, layers) = direction_layers(cat_domain.iter().collect(), rises, r.style.mark.border.radius);
                bar_layers_painted = layers;
                ordered
            }
            _ => cat_domain.iter().collect(),
        };
        let single_depth = tier_depth.get("").copied().unwrap_or(0);
        for (cat, key) in painted.into_iter().flat_map(|cat| (0..=single_depth).map(move |tier| (cat, (cat.clone(), tier_name("", tier))))) {
            let Some(band_y) = band.position(cat) else { continue };
            let bar_y = band_y + bar_pad;
            // A category with no row (a synthesized calendar bucket) has no datum.
            let Some(&val) = data_map.get(&key) else { continue };
            let val_x = linear.position(val);
            let start = ranged.as_ref().and_then(|rb| rb.starts.get(&key).copied());
            let (bar_x, bar_w, ends) = match start {
                _ if spans.contains_key(&key) => {
                    let span = &spans[&key];
                    let (x, w, _) = ranged_extent(|v| linear.position(v), span.end, Some(span.start));
                    (x, w, Rounded::by_sign(val))
                }
                None if val_x >= zero_x => (zero_x, val_x - zero_x, Rounded::Positive),
                None => (val_x, zero_x - val_x, Rounded::Negative),
                // Vega swaps a rect's `x`/`x2` into order (`adjustSpatial`,
                // `vega.js:35708-35740`); both ends round, but a numeric fall
                // keeps its left pair only (`bar_corner_props`).
                Some(start) => {
                    let start_x = linear.position(start);
                    let ends = if numeric_span && val < start { Rounded::Fall } else { Rounded::Both };
                    (val_x.min(start_x), (val_x - start_x).abs(), ends)
                }
            };
            let fill = r.style.single_series_fill.as_str();
            let plain = || {
                bar_datum_aria(
                    tooltip_labels, cat, tooltip_tu, x_is_standard_date, grain_header_format, (!layers.is_empty()).then_some(value_title), None, value_title, tooltip_fmt, val, None, None, extras.of(&key)?, None,
                )
            };
            let aria = match (&ranged, start, &span_block) {
                (Some(_), Some(_), Some(block)) => {
                    let datum = rows.iter().find(|row| row_str(row, cat_field) == *cat).expect("a ranged bar has its row");
                    Some(block.label(
                        &crate::contract::tooltip_header_text(cat, tooltip_tu, x_is_standard_date, grain_header_format)?,
                        layer_series.get(cat).map(String::as_str),
                        datum,
                        tooltip_fmt,
                    )?)
                }
                (Some(rb), Some(start), None) => {
                    rb.aria(tooltip_labels, cat, tooltip_tu, x_is_standard_date, grain_header_format, None, None, (value_title, tooltip_fmt), (start, val), plain)?
                }
                _ => plain()?,
            };
            if ranged.is_some() {
                label_decors.insert(key.clone(), LabelDecor { aria: aria.clone(), fill: None });
            }
            if let Some(band) = hover_band.as_mut() {
                band.push(cat, "", aria.as_deref(), hrefs.get(&key).map(String::as_str), bar_y, mark_h, Some(val));
            }
            if ghosts_on {
                ghost_points.push((aria.clone(), val_x, bar_y + mark_h / 2.0, ghost_ink.clone(), hrefs.get(&key).cloned()));
            }
            under.push(crate::annotation::DataMark::rect(bar_x, bar_y, bar_w, mark_h));
            plot.extend(bar_passes(true, bar_x, bar_y, bar_w, mark_h, fill, r.style.mark.opacity, stroke_color, stroke_w, corner_r,
                ends, true, None, aria.as_deref(), anchor_of(&key))?);
        }
    }

    // Layers paint on the base's own band scale and shared value scale in
    // authored order after the base (`layer_1_marks`, `layer_2_marks`, ...): a
    // bar layer at its own `bar_mark` (band width, border, corner radius), a
    // scatter point at its value's x on the row's band center.
    let mut bar_layer_index = 0;
    for (position, ((layer, layer_rows), claim)) in layers.iter().zip(&claims).enumerate() {
        match layer {
            OverlayLayer::Bar(bl) => {
                let (labels, marks) = (&bar_layer_labels[bar_layer_index], &bar_layer_marks[bar_layer_index]);
                bar_layer_index += 1;
                let layer_name = axis_title_text(&bl.y, bl.label.as_deref(), &r.field_labels)?;
                let color_promoted = bl
                    .color
                    .as_deref()
                    .is_some_and(|cf| layer_color_promoted(layer_rows, cf));
                let own_aria = bl
                    .y_start
                    .as_deref()
                    .map(|start| RangedLayerAria::new(bl, start, layer_rows, &r.field_labels, date_axis.is_some()))
                    .transpose()?;
                let describe = |row: &serde_json::Map<String, Value>, val: f64| {
                    if let Some(block) = &span_block {
                        let cat = row_str(row, bl.x.as_deref().unwrap_or(cat_field));
                        let header = crate::contract::tooltip_header_text(&cat, tooltip_tu, x_is_standard_date, grain_header_format)?;
                        let series = layer_series.get(&cat).map(String::as_str);
                        let label = if bl.y_start.is_some() {
                            block.layer_bar_label(&header, series, row, tooltip_fmt)
                        } else {
                            block.label(&header, series, row, tooltip_fmt)
                        };
                        return label.map(Some);
                    }
                    if let Some(own) = own_aria.as_ref().filter(|_| tooltip_labels) {
                        let cat = row_str(row, bl.x.as_deref().unwrap_or(cat_field));
                        let header = crate::contract::tooltip_header_text(&cat, tooltip_tu, x_is_standard_date, grain_header_format)?;
                        return own.label(bl, &header, row, bl.axis_y.tooltip_format_or(tooltip_fmt)).map(Some);
                    }
                    let color_value = bl.color.as_deref().filter(|_| color_promoted).map(|cf| layer_color_text(row, cf));
                    let series = layer_series_text(&layer_name, color_value.as_deref());
                    bar_datum_aria(
                        tooltip_labels, &row_str(row, bl.x.as_deref().unwrap_or(cat_field)), tooltip_tu, x_is_standard_date, grain_header_format,
                        Some(series.as_str()), None, layer_name.as_str(), bl.axis_y.tooltip_format_or(tooltip_fmt), val, None,
                        None, &[], None,
                    )
                };
                let bw = band.bandwidth();
                let mark_h = (bw * bl.bar_mark.band_width).min(bw);
                let bar_pad = (bw - mark_h) / 2.0;
                let layer_clip = layer_clips[position];
                let bl_corner_r = if layer_clip { 0.0 } else { bl.bar_mark.border.radius.unwrap_or(0.0) };
                let bl_stroke_color = bl.bar_mark.border.color.as_deref().unwrap_or("none");
                let bl_stroke_w = bl.bar_mark.border.width.unwrap_or(0.0);
                let mut layer_bars: Vec<Node> = Vec::new();
                for m in marks {
                    let Some(band_y) = band.position(&m.cat) else { continue };
                    let fill = match &claim.fill {
                        Some(fill) => fill.clone(),
                        None => match bl.color.as_deref() {
                            Some(cf) => claim.row_color(&layer_scale, cf, m.row),
                            None => layer_scale.color_of_value(m.series.as_deref()).to_string(),
                        },
                    };
                    let (x1, x2) = (linear.position(m.from), linear.position(m.to));
                    let aria = describe(m.row, m.value)?;
                    let layer_href = crate::families::layers::layer_link(link.as_ref(), (bl.query_name.as_deref(), r.query_name.as_deref())).map(|link| link.href(m.row)).transpose()?;
                    layer_bars.extend(bar_passes(
                        true, x1.min(x2), band_y + bar_pad, (x2 - x1).abs(), mark_h, &fill, bl.bar_mark.opacity, bl_stroke_color,
                        bl_stroke_w, bl_corner_r, if layer_clip { Rounded::Fall } else if bl.y_start.is_none() { Rounded::by_sign(m.value) } else if m.to >= m.from { Rounded::Both } else { Rounded::Fall }, true, None, aria.as_deref(),
                        r.link_open.anchor(layer_href.as_deref()),
                    )?);
                }
                if layer_clip {
                    plot.extend(crate::families::layers::clipped_layer(&mut defined_clips, (plot_w, plot_h), &r.id, layer_bars));
                } else {
                    plot.extend(layer_bars);
                }
                if let (Some(ml), Some(label_layer)) = (bl.bar_mark.labels.as_ref(), labels) {
                    let center = horizontal_center(band.bandwidth());
                    let geom = LabelGeometry { band: &band, center: &center, linear: &linear };
                    label_layer.warn_if_crowded(&r.id, Some(position), &geom, None, None);
                    let points = label_layer.points(&geom, None, &LabelDecors::new(), &crate::selected::Selection::default(), |site| {
                        Ok(tooltip_labels.then(|| {
                            crate::contract::native_label(&[
                                (bl.x.as_deref().unwrap_or(cat_title_text.as_str()), site.cat.clone()),
                                (layer_name.as_str(), site.value.to_string()),
                            ])
                        }))
                    })?;
                    plot.extend(mark_labels(ml, true, false, true, &points, 0, board_style, 0.0, 0.0, r.canvas.as_str(), r.link_open)?);
                }
            }
            OverlayLayer::Scatter(sl) => {
                let size = sl.size();
                let layer_name = axis_title_text(&sl.y, sl.label.as_deref(), &r.field_labels)?;
                let color_promoted = sl.color.as_deref().is_some_and(|cf| layer_color_promoted(layer_rows, cf));
                let encoded = |row: &serde_json::Map<String, Value>| {
                    claim.row_color(&layer_scale, sl.color.as_deref().expect("an encoded layer groups by color"), row)
                };
                plot.extend(crate::families::layers::scatter_layer_marks(sl,
                    layer_rows,
                    size,
                    1 + position,
                    true,
                    |row| band.position(&row_str(row, sl.x.as_deref().unwrap_or(cat_field))).map(|by| by + band.bandwidth() / 2.0),
                    None,
                    |val| crate::families::layers::clamp_unless_clipped(linear.position(val), plot_w, layer_clips[position]),
                    |row, val| {
                        let Some(block) = &span_block else {
                            if ranged.is_some() {
                                return Ok(None);
                            }
                            let color_value = sl.color.as_deref().filter(|_| color_promoted).map(|cf| layer_color_text(row, cf));
                            let series = layer_series_text(&layer_name, color_value.as_deref());
                            return bar_datum_aria(
                                tooltip_labels, &row_str(row, sl.x.as_deref().unwrap_or(cat_field)), tooltip_tu, x_is_standard_date, grain_header_format, Some(series.as_str()), None,
                                layer_name.as_str(), sl.axis_y.tooltip_format_or(tooltip_fmt), val, None, None, &[], None,
                            );
                        };
                        let cat = row_str(row, sl.x.as_deref().unwrap_or(cat_field));
                        let header = crate::contract::tooltip_header_text(&cat, tooltip_tu, x_is_standard_date, grain_header_format)?;
                        block.label(&header, layer_series.get(&cat).map(String::as_str), row, tooltip_fmt).map(Some)
                    },
                    match &claim.fill {
                        Some(fill) => PointColor::Claimed(fill),
                        None => PointColor::Encoded(&encoded),
                    },
                    r.canvas.as_str(),
                    board_style,
                    |_, _| Ok(None),
crate::families::layers::layer_link(link.as_ref(), (sl.query_name.as_deref(), r.query_name.as_deref())), layer_clips[position].then_some((plot_w, plot_h)),
crate::families::layers::LabelCrowd { chart_id: &r.id, layer: position, fonts },)
                .map_err(|e| format!("bar: {e}"))?);
            }
            OverlayLayer::Line(l) => {
                let layer_name = claim.label.as_str();
                let band = BandScale::new(cat_domain.clone(), 0.0, plot_h, band_pi, band_po);
                let layer_link = crate::families::layers::layer_link(link.as_ref(), (l.query_name.as_deref(), r.query_name.as_deref()));
                let paint = |rows: &[serde_json::Map<String, Value>],
                             fill: &str,
                             color_value: Option<&str>,
                             stack: Option<&LayerStack>|
                 -> Result<Vec<Node>, String> {
                    let series = layer_series_text(layer_name, color_value);
                    crate::families::layers::line_layer_marks(
                        l,
                        rows,
                        |row| band.position(&row_str(row, l.x.as_deref().unwrap_or(cat_field))).map(|by| by + band.bandwidth() / 2.0),
                        None,
                        |val| crate::families::layers::clamp_unless_clipped(linear.position(val), plot_w, layer_clips[position]),
                        &AreaFrame { free_y: &|val| linear.position(val), plot: (plot_w, plot_h), clip: layer_clips[position] },
                        true,
                        fill,
                        r.canvas.as_str(),
                        |row, val| {
                            bar_datum_aria(
                                tooltip_labels, &row_str(row, l.x.as_deref().unwrap_or(cat_field)), tooltip_tu, x_is_standard_date, grain_header_format, Some(series.as_str()), None,
                                layer_name, l.axis_y.tooltip_format_or(tooltip_fmt), val, None, None, &[], None,
                            )
                        },
                        board_style,
                        |_, _| Ok(None),
                        None,
                        0.0,
                        stack,
                        layer_link,
                        crate::families::layers::LabelCrowd { chart_id: &r.id, layer: position, fonts },
                    )
                    .map_err(|e| format!("bar: {e}"))
                };
                match l.color.as_deref() {
                    Some(cf) => {
                        let promoted = layer_color_promoted(layer_rows, cf);
                        let stack = l
                            .area_mark
                            .is_some()
                            .then(|| LayerStack::ascending(layer_rows, l.x.as_deref().unwrap_or(cat_field), cf, &l.y).imputing(layer_rows, layer_link))
                            .transpose()
                            .map_err(|e| format!("bar: {e}"))?;
                        for (value, rows_for_value) in layer_color_groups(layer_rows, cf) {
                            let text = value.clone().unwrap_or_else(|| crate::families::layers::NULL_COLOR_TEXT.to_string());
                            let color = claim.group_color(&layer_scale, cf, value.as_deref(), &rows_for_value);
                            plot.extend(paint(&rows_for_value, &color, Some(text.as_str()).filter(|_| promoted), stack.as_ref())?);
                        }
                    }
                    None => plot.extend(paint(layer_rows, claim.fill.as_deref().expect("a colorless line layer claimed its fill"), None, None)?),
                }
            }
        }
    }

    // Zero line, painted OVER the bars: production compiles it as its own
    // layer AFTER the bar layer (`layer[0] = {mark: bar}`, `layer[1] =
    // {mark: rule, x.datum: 0}`), so Vega's rule covers the bars' white
    // stroke instead of sitting under it. Same gates as the margin ink
    // above: hidden grid OR a hidden threshold suppresses it entirely
    // (see `zero_w_for`) — a normalize stack's 0%/100% rules are not
    // exempted from `grid.visible`.
    // Layers painted so far: the bars, then the zero rule when one is.
    let mut painted_layers = bar_layers_painted;
    if let Some(band) = &hover_band {
        plot.push(band.node(false, &linear, plot_w, r.link_open)?);
    }
    if let Some(zl) = &grid.threshold {
        if let Some(rules) = rules {
            let zc = zl.stroke();
            let zw = zl.width.unwrap_or(2.0);
            let rule_x = |on: bool, datum: f64| on.then(|| linear.position(datum));
            let (zero_x, unity_x) = (rule_x(rules.zero, 0.0), rule_x(rules.unity, 1.0));
            // The 100% rule is a layer of its own.
            let rule_xs: Vec<f64> = [zero_x, unity_x].into_iter().flatten().collect();
            painted_layers += rule_xs.len();
            plot.extend(baseline_rule_layers(bar_layers_painted, &rule_xs, |x| {
                Attrs::new()
                    .set("x1", format!("{x:.2}"))
                    .set("y1", "0")
                    .set("x2", format!("{x:.2}"))
                    .set("y2", format!("{plot_h:.2}"))
                    .set("stroke", zc)
                    .set("stroke-width", zw.to_string())
            })?);
        }
    }

    if !ghost_points.is_empty() {
        let rules: Vec<crate::contract::GhostRule> = ghost_points
            .iter()
            .map(|(aria, x, y, stroke, href)| crate::contract::GhostRule { aria: aria.as_deref(), x: *x, y: *y, stroke, anchor: r.link_open.anchor(href.as_deref()) })
            .collect();
        plot.push(crate::contract::ghost_layer(&rules, crate::contract::GhostRun::ToLeftEdge)?);
    }

    let mut tail: Vec<Node> = Vec::new();

    // Authored bar value labels, then the stack-total label: one text layer
    // each, after the layers already painted (`painted_layers`).
    let center = horizontal_center(band.bandwidth());
    let geom = LabelGeometry { band: &band, center: &center, linear: &linear };
    for layer in &label_layers {
        under.extend(layer.site_inks(&geom, None).map(|(_, b)| crate::annotation::DataMark::Box(b)));
        layer.warn_if_crowded(&r.id, None, &geom, None, None);
        let points = layer.points(&geom, None, &label_decors, &r.selection, |site| {
            bar_datum_aria(
                tooltip_labels, &site.cat, tooltip_tu, x_is_standard_date, grain_header_format, None, None, value_title,
                tooltip_fmt, site.value, None, None, &[], None,
            )
        })?;
        tail.extend(mark_labels(&layer.spec.ml, true, layer.stacked, !layer.total, &points, painted_layers, board_style,
            plot_left, plot_top, r.canvas.as_str(), r.link_open)?);
        if !layer.total {
            painted_layers += 1;
        }
    }

    if let (Some(block), Some(style)) = (&block, r.effective_support_table_style.as_ref()) {
        tail.extend(block.nodes(style, plot_w, plot_h, plot_left, plot_top, r.strip_linked().then_some(r.link_open))?);
    }

    // Over the value labels; the plot origin is offset into the page frame.
    let annotations = crate::annotation::AnnotationCtx { occupied: under, ..annotation_ctx((plot_left, plot_top), plot_w, plot_h) };
    tail.extend(crate::annotation::nodes(&annotations, crate::annotation::Plane::Foreground)?);

    // Category labels (left axis). Vega-fit path draws them the way the
    // compiled axis does: flip-aligned (anchor start at −labelPadding, the
    // whole gutter shifted by the axis offset) with the middle-baseline
    // shift Vega rounds (`round(0.30·fs)`). A facet's shared axis is the
    // composer's `row_header`, or a last-column `row_footer` copy: the panel
    // reports (or paints) the labels instead.
    let cat_color = ax_x.labels.font.color.as_deref().unwrap_or("#626366");
    let cat_weight = ax_x.labels.font.weight_str();
    let cat_attrs = |x: f64, y: f64, anchor: &str| {
        Attrs::new()
            .set("x", format!("{x:.2}"))
            .set("y", format!("{y:.2}"))
            .set("text-anchor", anchor)
            .vega("font-family", cat_family)
            .set("font-size", cat_font_size.to_string())
            .set("font-weight", cat_weight.as_str())
            .set("fill", cat_color)
    };
    let two_lines_fit = row_holds_two(plot_h);
    // A name wider than the gutter wraps onto a second line when the row holds two, and only that
    // line is cut; a cut name keeps its full text on hover. A wrapped block centers its ink (line
    // one's cap top to the last baseline) on the band; a single line keeps Vega's `middle` shift.
    let cat_paint = |shown: &str, center_y: f64| -> Result<CatLabel, String> {
        let lines = cat_lines(shown, two_lines_fit)?;
        let y = if lines.len() == 1 {
            center_y + measure::baseline_shift("middle", cat_font_size)
        } else {
            center_y + CAP_HEIGHT_EM * cat_font_size / 2.0 - cat_line_height * (lines.len() - 1) as f64 / 2.0
        };
        let full = Some(shown).filter(|full| lines.join(" ") != *full).map(str::to_string);
        Ok(CatLabel { y, lines, full })
    };
    let cat_node = |x: f64, anchor: &str, label: &CatLabel| {
        crate::families::cartesian::lines_label_node(
            cat_attrs(x, label.y, anchor),
            &label.lines,
            label.full.as_deref(),
            &format!("{x:.2}"),
            cat_line_height,
        )
    };
    let (cat_label_pad, cat_gutter) = (cat_gutters(plot_h).0 + side_clear.get(), cat_gutters(plot_h).1 + side_clear.get());
    let ghost_w = ghost_w_at(plot_h);
    let cat_edge_x = if cat_on_right { plot_right + ax_off } else { plot_left - ax_off };
    let cat_side = if cat_on_right { 1.0 } else { -1.0 };
    // Vega lists the x axis before the y axis.
    if val_axis_in_panel {
        tail.extend(value_axis.nodes(ax_y, plot_left, plot_w, plot_bottom)?);
    }
    let mut cat_items: Vec<Node> = Vec::new();
    let mut cat_labels: Vec<CatLabel> = Vec::new();
    let mut ghost_labels: Vec<CatLabel> = Vec::new();
    for (cat_i, cat) in cat_domain.iter().enumerate() {
        let Some(band_y) = band.position(cat) else { continue };
        let center_y = plot_top + band_y + band.bandwidth() / 2.0;
        // Axis groups carry Vega's +0.5 crispness translate; the band
        // axis label's encoded y bakes the −0.5 back out, so only x
        // shifts (bc0 scenegraph, flip model: label abs x = plot_left
        // − 52.974, baseline y = band center + 3 exactly).
        let label_x = cat_edge_x + cat_side * cat_label_pad + 0.5;
        // B6 — Vega's labelLimit: draw lines the gutter above
        // (`cat_w = max_cat_w.min(cat_limit)`) reserved, so a drawn
        // line never overhangs its own gutter.
        let label = cat_paint(&shown_cat(cat)?, center_y).map_err(|e| format!("bar: {e}"))?;
        if cat_axis_in_panel {
            cat_items.push(cat_node(label_x, cat_anchor, &label));
        }
        cat_labels.push(label);
        ghost_labels.push(cat_paint(&ghost_cats[cat_i], center_y).map_err(|e| format!("bar: {e}"))?);
    }

    // Axis titles.
    if cat_axis_in_panel {
        cat_items.extend(crate::common::axis_title_rotated(
            &cat_title_paint,
            &ax_x.title,
            cat_edge_x + cat_side * cat_gutter,
            plot_top + plot_h / 2.0,
            cat_side,
            facet.is_none().then_some(crate::contract::LabelKey::X),
        ));
        tail.extend(crate::contract::axis_group(
            crate::contract::AxisChannel::Y,
            &cat_caption,
            (cat_edge_x + 0.5, plot_top),
            cat_items,
        )?);
    }
    if mirror_active && cat_axis_in_panel {
        let ghost_edge_x = if cat_on_right { plot_left - ax_off } else { plot_right - headroom_pull(plot_w, &tick_values) + ax_off };
        let ghost_x = ghost_edge_x - cat_side * ghost_gutter + 0.5;
        let ghost_items: Vec<Node> = ghost_labels.iter().map(|label| cat_node(ghost_x, ghost_anchor, label)).collect();
        tail.extend(crate::contract::axis_group(
            crate::contract::AxisChannel::Y,
            &cat_caption.untitled(),
            (ghost_edge_x + 0.5, plot_top),
            ghost_items,
        )?);
    }
    // A facet's axis copies: the primary on `axis_y.position`'s side and the
    // mirror's ghost opposite. The composer's `row_header` holds the left one;
    // the last column of each row paints the right one at `row_footer`'s anchor,
    // `ceil(cell x2)`.
    let primary_copy = EdgeCopy { dx: cat_side * cat_label_pad + 0.5, anchor: cat_anchor, reach: ax_off + cat_gutter + cat_title_extra };
    let ghost_copy = (mirror_active && facet.is_some()).then(|| EdgeCopy {
        dx: -cat_side * ghost_gutter + 0.5,
        anchor: ghost_anchor,
        // A ghost anchored outward carries its labels past the gutter.
        reach: ax_off + ghost_gutter + if ghost_anchor == if cat_on_right { "end" } else { "start" } { ghost_w } else { 0.0 },
    });
    let (left_copy, right_copy) = if cat_on_right { (ghost_copy, Some(primary_copy)) } else { (Some(primary_copy), ghost_copy) };
    let right_edge = facet.map(|f| &f.right_edge);
    if let (Some(copy), Some(edge)) = (&right_copy, right_edge.filter(|e| e.paint && !cat_axis_in_panel)) {
        let edge_x = plot_left + plot_w + edge.overhang + ax_off;
        let right_labels = if cat_on_right { &cat_labels } else { &ghost_labels };
        let mut edge_items: Vec<Node> = right_labels.iter().map(|label| cat_node(edge_x + copy.dx, copy.anchor, label)).collect();
        if cat_on_right {
            edge_items.extend(crate::common::axis_title_rotated(&cat_title_paint, &ax_x.title, edge_x + cat_gutter, plot_top + plot_h / 2.0, 1.0, None));
        }
        tail.extend(crate::contract::axis_group(crate::contract::AxisChannel::Y, &cat_caption, (edge_x + 0.5, plot_top), edge_items)?);
    }
    let row_header_labels = if cat_axis_in_panel {
        Vec::new()
    } else {
        let left_labels = if cat_on_right { &ghost_labels } else { &cat_labels };
        left_labels.iter().map(|label| (label.y, label.lines.join(&measure::LINE_SEP.to_string()))).collect()
    };


    let group_attrs = Attrs::new().set("transform", format!("translate({plot_left:.2},{plot_top:.2})"));
    // The splice points a facet composite cuts the printed panel at.
    let cuts = facet.map(|_| frame.cuts(&head, &group_attrs, &plot, &tail, None));
    let svg: Node = {
        let mut body = head;
        body.push(Node::group(group_attrs, plot));
        body.extend(tail);
        frame.svg(body)
    };
    let mut ink = vega_fit::Bounds::new(
        plot_left + cell_ink.x1,
        plot_top + cell_ink.y1,
        plot_left + cell_ink.x2,
        plot_top + cell_ink.y2,
    );
    // The right-edge copy reaches `reach` past the anchor `ceil(x2)`; a rows-only
    // facet folds it into its cell, a wider grid reserves it once.
    let right_reach = if cat_axis_in_panel { 0.0 } else { right_copy.as_ref().map_or(0.0, |c| right_edge.map_or(0.0, |e| e.overhang) + c.reach) };
    if right_edge.is_some_and(|e| e.fold) {
        ink.union(&vega_fit::Bounds::new(plot_left, plot_top, plot_left + plot_w + right_reach, plot_top + plot_h));
    }
    let facet_panel = cuts.map(|cuts| HorizontalFacetPanel {
        // Under `pad` the panel's own margins are the canvas offsets, so `plot_left`/`plot_top` are `m.left`/`m.top`.
        geometry: PanelGeometry {
            content_start: cuts.content_start,
            svg_end: cuts.svg_end,
            ink,
            axis_w_overhang: plot_left + right_reach,
            left_mark_overhang: plot_left,
            plot_top,
            raw_right_reach: (cell_ink.x2 - plot_w).max(0.0),
            widest_val_tick_label_width: 0.0,
            val_gutter: 0.0,
            val_tick_size: 0.0,
            val_ticks: row_header_labels,
            val_tick_marks: Vec::new(),
            ghost_labels: Vec::new(),
            mirror_style: Some(GhostStyle {
                caption: cat_caption.clone(),
                val_font_size: cat_font_size,
                val_weight: cat_weight,
                val_color: cat_color.to_string(),
                val_family: cat_family.to_string(),
            }),
            axis: facet_compose::PanelXAxis::Value(value_axis),
            top_axis_overhang: 0.0,
            value_title_w: 0.0,
            legend: facet_legend,
            value_axis: None,
        },
        row_axis: left_copy.map(|copy| RowAxisPlacement {
            reserve: copy.reach,
            label_x: -ax_off + copy.dx,
            label_anchor: copy.anchor,
            titled: !cat_on_right,
            title_x: 0.5 - (ax_off + cat_gutter),
        }),
    });
    let drawn = Drawn {
        svg,
        canvas: Rect { x: 0.0, y: 0.0, width: canvas_w, height: canvas_h },
        data_box: Rect { x: plot_left, y: plot_top, width: plot_w, height: plot_h },
        title_pos: vega_title_pos,
    };
    Ok((drawn, facet_panel))
}
