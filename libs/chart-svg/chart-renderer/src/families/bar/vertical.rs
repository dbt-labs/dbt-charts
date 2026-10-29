//! The vertical bar panel on a band or temporal x: columns over a category axis.

use crate::common::{VALUE_AXIS_BREATHING_ROOM, axis_title_band, axis_title_text, color_domain_of, row_num, row_str};
use crate::diagnostic::Diagnostic;
use crate::families::axis_caption::{AxisCaption, category_axis_caption, date_value_caption, value_axis_caption};
use crate::families::cartesian::{BandScale, LinearScale, vl_default_tick_count};
use crate::families::layers::{AreaFrame, DomainCut, DualAxisTitles, LayerAxis, LayerAxisFrame, LayerAxisInk, LayerClaim, LayerScale, LayerSide, LayerStack, LayerType, MOVED_BASE_LABEL_PAD, OverlayAxisY, OverlayBarLayer, OverlayLayer, OverlayLineLayer, OverlayScatterLayer, PointColor, YSides, claim_layers, layer_color_groups, layer_color_of, layer_color_promoted, layer_color_text, layer_color_values, layer_series_swatches, layer_series_text, layer_stack_order, merged_legend_title, resolve_layer_rows, row_order_stack, scope_group_top, union_x_domain};
use crate::families::legend::{LegendEntry, LegendSymbol, VegaLegend, drawn_symbol_legend, legend_fit_bounds, legend_origin, legend_room, legend_slide, legend_title_text, limited_labels, title_only_legend, vega_legend, with_drawn_labels};
use crate::families::scatter_axis::TemporalXAxis;
use crate::families::span::RangedBars;
use crate::families::temporal::{AuthoredTimeFormat, LabelUnit, TemporalLabel, TimeUnit, axis_time_format, complete_buckets, grain_format, parse_authored_time_unit, resolve_vega_label_layout};
use crate::families::{facet_compose, multiples, span, support_table_strip};
use crate::frame::{ChartFrame, FrameTitle};
use crate::layout::Rect;
use crate::svg::num_full;
use crate::time_axis::{Grain as AxisGrain, TimeAxis};
use crate::vega_fit::category_label_layout;
use crate::{Padding, Payload, codes, vega_fit};

use measure::FontBook;
use scene::{Attrs, Node};
use serde_json::{Value, json};
use std::borrow::Cow;
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};

use super::axes::{BandInterval, BandXAxis, band_tick_snap, bar_temporal_labels, bucket_x, cadence_ratio, format_tick_labels, rotated_rows_reach};
use super::bind::zero_label;
use super::domain::{CenterStack, RawX, SegmentSpan, SegmentSpans, bar_value_extent, base_series, build_cat_domain, build_data_map, Extras, cat_order, category_totals, collapsed_authored_domain, grouped_rail_anchors, grouped_spans, is_tier, layered_by_sign, nice_shared_scale, niced_rail_domain, outer_segments, stack_label_spans, stack_reach, stack_spans, stacked_rail_anchors, tallest_total, tier_depths, tier_name, unbaked_rungs, value_domain_and_ticks, with_tiers};
use super::facet::color_1to1_with_cat;
use super::labels::{FitHide, LabelBuild, LabelDecor, LabelDecors, LabelGeometry, LabelLayer, LabelSpec, LayerLabelFit, LayerLabelFitItem, category_clearance, label_fit_span, label_hang, mark_labels, plain_sites, step_band_of, value_label_layers};
use super::layout::{Drawn, XScale, clipped_if, warn_vertical_density};
use super::legend::{LayeredBarLegend, bar_link_keys, grouped_legend_domain, numeric_legends_of, stacked_legend_entries};
use super::marks::{BandColumns, ColumnSplit, Columns, RangedLayerAria, Rounded, bar_passes, baseline_rule_layers, baseline_rules, collapsed_bars, direction_layers, grouped_overlap_fraction, hover_band_for, labels_on_zero_stack, layer_block_rows, layer_day_numbers, link_hrefs, painted_series_bars, ranged_extent, strip_cell_aria, strip_centers, unity_rule};
use super::time_x::TimeX;
use super::tooltip::{NativeTitles, bar_datum_aria, imputed_datum_aria, stack_group_total, tooltip_order_domain};
use super::{BarResolved, x_resolution};

/// Geometry `render_vertical` hands back so `attach_support_table_strip` can
/// align the strip to the exact plot box and band positions the bars
/// themselves were drawn into — not a second, independently recomputed
/// domain/margin (the source of the drift bug this replaces).
pub(crate) struct BarStripGeometry {
    pub(crate) plot_left: f64,
    /// The y of the plot origin in this panel's own canvas frame.
    pub(crate) plot_top: f64,
    /// Byte offset right after the panel's own background rect — see where
    /// it's captured, right after that `write!` call.
    pub(crate) content_start: usize,
    /// Byte offset of the SVG's own closing `</svg>` tag — `svg[content_start..svg_end]`
    /// is exactly this panel's own drawn content, background/wrapper stripped.
    pub(crate) svg_end: usize,
    /// This panel's own local ink bounds, in its own canvas frame — see the
    /// doc comment where this is computed, right after `band` is built.
    /// `render_facet` reads this as vega's `bboxFull` quantity (grid.js
    /// `bounds: "full"`) when feeding `facet_layout::grid_layout`; the
    /// single-chart path (`render_vertical`) never reads it.
    pub(crate) ink: vega_fit::Bounds,
    /// The value-axis gutter's own width overhang past the plot rect
    /// (`m.left + m.right` at whichever side actually carries it) — this
    /// SAME magnitude is folded into `ink` above ONLY where `fold_axis_
    /// gutter` is true (`render_vertical_panel`'s own doc comment): a
    /// rows-only facet (`panel_cols_n == 1`), where every panel IS a row's
    /// own last column, folds it into every panel's own ink; a multi-column
    /// row folds it into NOTHING (real Vega's `row_footer` role sits
    /// outside `cell` entirely), so `grid_layout`'s own bounds union never
    /// sees it there. Reported here UNFOLDED regardless, because `render_
    /// facet`'s composer also needs the bare number: once for the
    /// multi-column axis reserve past the last column, and again for `axis_y.
    /// mirror`'s ghost reserve, which paints OUTSIDE any panel's own
    /// canvas and so needs its own additional composer-level reservation
    /// `ink` can't carry. The single-chart path never reads it.
    pub(crate) axis_w_overhang: f64,
    /// `PlotRect.raw_right_reach` — see `vega_fit::FitMargins.raw_left`'s
    /// own doc comment. `facet_compose::PanelGeometry.raw_right_reach`
    /// reads this straight off `render_facet`'s own `geom`.
    pub(crate) raw_right_reach: f64,
    /// The widest value-tick label's own measured width (`fonts.measure`
    /// over the DRAWN labels, the PAINT font — NOT `val_gutter`'s own
    /// `measured_label_padding` term below, which measures the stand-in
    /// font over the drawn ladder; the two are different
    /// quantities and only this one is correct for the ghost's own
    /// outward ink) — a facet's `axis_y.mirror` ghost copy needs this on
    /// top of the axis gutter: `packages/vega-view-transforms`'s axis-label
    /// encoder positions BOTH copies' anchor at `sign*(tickSize +
    /// labelPadding)` regardless of `labelAlign`, but this theme forces
    /// `labelAlign: right` on the value axis, so the PRIMARY's own text
    /// extends BACK toward the plot from that anchor (already inside
    /// the axis gutter) while the ghost's text extends FURTHER OUTWARD
    /// past its own (sign-flipped) anchor by exactly this width — probed
    /// against `fixtures/multiples/02-columns-only`'s real compiled Vega
    /// axes (byte-identical `labelAlign`/`labelPadding` on both copies):
    /// primary reserve 34.78, ghost reserve 65.99, the SAME ~30.78px gap
    /// this term supplies. The single-chart path never reads it.
    pub(crate) widest_val_tick_label_width: f64,
    /// The value axis's own baked `labelPadding` — `gutter_of`'s return
    /// value (stand-in-font-measured over the drawn ladder, never the paint
    /// font), the SAME quantity a single-chart right-axis
    /// arm already reserves. A facet's `axis_y.mirror` primary reserve is
    /// `tick_size + val_gutter`, never a second hand-reconstruction of
    /// `labelPadding` from `widest_val_tick_label_width` (a different
    /// quantity: the PAINT font's widest DRAWN label, correct only for the
    /// ghost's own outward ink, not for the spec's reserved band). `0.0` on
    /// the continuous-x arm's own single-panel (rows-only) shape — a
    /// multi-column facet on that arm refuses outright, see `render_facet`'s
    /// own doc comment.
    pub(crate) val_gutter: f64,
    /// The value axis's own tick length past the gutter's origin — see
    /// `PanelGeometry.val_tick_size`.
    pub(crate) val_tick_size: f64,
    /// Every VISIBLE value-tick's own `(absolute_y, label)` pair, in this
    /// panel's own local frame — an `axis_y.mirror` ghost paints the SAME
    /// pairs on the opposite edge; see `widest_val_tick_label_width`'s doc.
    /// The single-chart path never reads it.
    pub(crate) val_ticks: Vec<(f64, String)>,
    /// `PanelGeometry.ghost_labels`.
    pub(crate) ghost_labels: Vec<String>,
    /// Every rung's tick-mark y in the same frame (`PanelGeometry.val_tick_marks`).
    pub(crate) val_tick_marks: Vec<f64>,
    /// The primary value axis's own resolved label paint attributes
    /// (`val_font_size`/`val_weight`/`val_color`/`val_family`) — a facet's
    /// `axis_y.mirror` ghost copy paints with these EXACT values, never a
    /// second, independent read of `axis_y.labels.font` (which would drift
    /// from the primary the moment a theme or board sets a non-default
    /// label style; `mirror_axis.py`'s own contract is `dict(primary_axis)`
    /// verbatim except `orient`/`grid`/`title`). The single-chart path
    /// never reads these.
    pub(crate) val_font_size: f64,
    pub(crate) val_weight: String,
    pub(crate) val_style: String,
    pub(crate) val_color: String,
    pub(crate) val_family: String,
    /// The value axis's description, which the ghost copy repeats untitled.
    pub(crate) val_caption: AxisCaption,
    /// Everything needed to paint this panel's own x-axis a SECOND time, at
    /// a facet composite's own footer-band geometry — see `PanelXAxis`'s
    /// own doc comment. The single-chart path never reads this.
    pub(crate) axis: facet_compose::PanelXAxis,
    /// See `PanelGeometry.top_axis_overhang`'s own doc comment — the same
    /// quantity, computed by this arm's own `fit` closure. The single-chart
    /// path never reads this (`suppress_x_axis` is false there, so the
    /// term that feeds it is never even measured).
    pub(crate) top_axis_overhang: f64,
    /// The widest line of the value axis's rotated title when this panel paints it.
    pub(crate) value_title_w: f64,
    pub(crate) value_axis: Option<facet_compose::ValueAxisInk>,
    /// A facet panel's color legend, painted at its own origin: the legend
    /// is chart-level, drawn once by the composer, never inside a panel.
    /// `None` outside a facet panel or without a visible legend.
    pub(crate) facet_legend: Option<facet_compose::FacetLegend>,
}

/// Thin wrapper: reads the whole-chart box/rows off `payload` and calls
/// `render_vertical_panel` — the single-chart path's own "one panel, the
/// whole card" call. A facet composite (small multiples) calls
/// `render_vertical_panel` directly, once per panel, with that panel's own
/// rows and its own (narrower) box instead of the whole payload's.
pub(super) fn render_vertical(
    payload: &Payload,
    fonts: &FontBook,
    r: &BarResolved,
    composed_strip: Option<&support_table_strip::SupportTableConfig>,
    wide_series: &[String],
    layers: &[OverlayLayer],
) -> Result<(Drawn, Option<BarStripGeometry>, bool), Diagnostic> {
    render_vertical_panel(
        &payload.rows,
        payload.width,
        payload.height,
        payload.padding.as_ref(),
        // The chart's OWN effective background — see `BarResolved::background`.
        r.background.as_str(),
        payload,
        fonts,
        r,
        composed_strip,
        wide_series,
        layers,
        // Not a facet panel at all — `BarFacetPanelOpts` is a facet-only
        // distinction (`render_vertical_panel`'s own doc comment).
        None,
    )
}

/// Which plot-rect negotiation a vertical bar panel takes — decided once,
/// before any measurement, by whether an endpoint rail is active.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(super) enum Shape {
    /// The endpoint-rail hconcat (`_wrap_hconcat_label_pane`): a stacked
    /// rail on a left value axis, its legend (any edge but a floating one)
    /// inside pane 0.
    RailConcat,
    /// Vega's own margin algebra — `vega_fit::fit_plot` for the single-chart
    /// card, `vega_fit::pad_plot` for a facet panel and for a support_table
    /// chart (production's `autosize: pad`: the plot IS the payload view, the
    /// strip floats around it, the canvas grows around the ink): no rail.
    Fit,
}

/// Length of the band axis's tick marks: drawn when the theme or author made
/// them visible, or when thinned labels restore them, and only beside labels.
fn band_tick_len(ax_x: &crate::common::AxisStyle, restored: bool) -> f64 {
    let drawn = restored || ax_x.ticks.visible == Some(true);
    if drawn && ax_x.labels.visible != Some(false) { ax_x.ticks.length.unwrap_or(6.0) } else { 0.0 }
}

/// `ERR-ANNOTATION-UNSUPPORTED` for a bar shape whose slots the annotation maps cannot follow.
pub(super) fn annotations_unsupported(r: &BarResolved, chart_type: &str) -> Diagnostic {
    Diagnostic::new(
        &crate::codes::ERR_ANNOTATION_UNSUPPORTED,
        [("chart_id", Value::String(r.id.clone())), ("chart_type", Value::String(chart_type.into()))],
    )
}

/// The calendar dates category labels lead with (`YYYY-MM-DD`, then nothing or a `T` or space and anything): the shape
/// that routes a band axis to the temporal label tilt. A label heuristic, not an x value.
pub(super) fn leading_dates(domain: &[String]) -> Vec<(i32, u8, u8)> {
    domain.iter().filter_map(|label| crate::time_axis::parse_iso_date(label)).collect()
}

/// A numeric x lands on a category axis as strings, which a number anchor cannot name.
pub(super) fn refuse_numeric_x_on_bands(r: &BarResolved) -> Result<(), Diagnostic> {
    if r.annotations.iter().any(|a| a.x_numbers().next().is_some()) {
        return Err(annotations_unsupported(r, "numeric-x category-axis bar"));
    }
    Ok(())
}

pub(super) fn shape(show_rail: bool) -> Shape {
    if show_rail { Shape::RailConcat } else { Shape::Fit }
}

/// What the plot-rect negotiation hands to the scales and the emission —
/// built once by whichever arm runs, so nothing escapes an arm by mutation.
pub(super) struct PlotRect {
    pub(super) plot_left: f64,
    pub(super) plot_top: f64,
    pub(super) plot_w: f64,
    pub(super) plot_h: f64,
    /// The Vega arms' own title anchor; the composed and legacy arms have
    /// none (`emit_title`'s single row).
    pub(super) title_pos: Option<(f64, f64)>,
    /// The fit arm's laid-out legend (x, y, group); every other arm keeps
    /// the legacy column/row.
    pub(super) legend: Option<(f64, f64, VegaLegend)>,
    /// Rail label pane x in plot coordinates (rail-concat arm only).
    pub(super) rail_x_rel: Option<f64>,
    pub(super) canvas_w: f64,
    pub(super) canvas_h: f64,
    pub(super) composed_rows: Option<Vec<support_table_strip::ComposedRow>>,
    /// The fit arm's final `m.left + m.right` (`pad_plot`'s measured
    /// value-axis-gutter overhang, both sides) — `BarStripGeometry.
    /// axis_w_overhang`, read unfolded by `render_facet`'s own budget/ghost
    /// math. `0.0` on every other arm (a facet panel is always the fit arm
    /// — `FacetFeature` refuses `multiples` alongside anything that would
    /// route elsewhere before this field is ever read).
    pub(super) facet_axis_gutter: f64,
    /// The fit arm's final `m.right` (`pad_plot`'s measured right-side
    /// value-axis-gutter overhang) — the facet-panel `ink` union past
    /// `plot_right` needs it.
    pub(super) facet_axis_right_overhang: f64,
    /// The fit arm's `m.raw_left` — see `vega_fit::FitMargins.raw_left`'s
    /// own doc comment. `0.0` on every other arm (a facet panel is always the
    /// fit arm — `FacetFeature` refuses `multiples` alongside anything that
    /// would route elsewhere before this field is ever read).
    pub(super) raw_right_reach: f64,
    /// Vega's parity overlap on the value labels: the fit closure records
    /// which labels stay on every pass; every other arm draws every label.
    pub(super) val_visible: Option<Vec<bool>>,
    // The value ladder and the category labels as the arm re-derived them:
    // the rail re-filters the ladder against its niced domain and re-runs
    // the cadence pass at the pane width; the fit re-counts an unbaked
    // ladder at its final height.
    pub(super) linear_domain_max: f64,
    pub(super) tick_values: Vec<f64>,
    pub(super) val_tick_labels: Vec<String>,
    pub(super) max_val_w: f64,
    pub(super) val_gutter: f64,
    pub(super) temporal_labels: Option<Vec<TemporalLabel>>,
    pub(super) x_tick_len: f64,
    pub(super) label_rows: usize,
    /// See `BarStripGeometry.top_axis_overhang`'s own doc comment. `0.0` on
    /// every non-facet arm (the same "a facet panel is always the fit arm"
    /// reasoning `raw_right_reach` below already states).
    pub(super) top_axis_overhang: f64,
}

/// Facet-only geometry for one `render_vertical_panel` call — `None` from
/// the single-chart wrapper (`render_vertical`), `Some` from the facet
/// composer's own `render_pass` closure. Mirrors `line.rs::FacetPanelOpts`
/// (same fields, same "one struct, not a growing bool list" law) — a
/// second reading of that shape here would be the review finding this
/// crate's own AGENTS.md objections table warns against.
pub(crate) struct BarFacetPanelOpts<'a> {
    /// What the axis titles wrap against.
    pub(crate) title_boxes: multiples::FacetTitleBoxes,
    /// The card's width, the room the chart-level legend fits in.
    pub(crate) legend_w: f64,
    /// The panel width the emitters judge crowding at (`RenderBox.width`): the
    /// width the spec declared, which a panel left at the default view width
    /// does not draw at.
    pub(crate) decision_w: f64,
    /// A facet composite draws its own shared x-axis ONCE, below the last
    /// row — every panel skips its own x-axis ink reservation (`pad_plot`
    /// instead of `fit_plot`) and paint. `true` on every real facet call;
    /// unit tests toggle it to isolate one law at a time.
    pub(crate) suppress_x_axis: bool,
    /// The panel draws its own x axis inside its cell (a narrowed x domain:
    /// Vega then gives the cell its own axis, not a shared `column-footer`),
    /// reserving its ink, while every other `suppress_x_axis` law stands.
    pub(crate) cell_x_axis: bool,
    /// Only the last column of a row paints the shared value axis; every
    /// other panel still reserves its own gutter (for row alignment) but
    /// draws nothing.
    pub(crate) paint_value_axis: bool,
    /// A single-column (rows-only) facet folds the value-axis gutter into
    /// its own row's ink; a multi-column facet reserves that gutter
    /// separately (`composite_axis_reserve`) instead.
    pub(crate) fold_axis_gutter: bool,
    /// `multiples.scale: "shared"` (the default): `Some(all_rows)` overrides
    /// `value_domain_and_ticks`'s own row source with the composer's
    /// pre-regroup row set. `multiples.scale: "independent"`: `None`, so
    /// each panel computes its own unbaked domain from its own `rows`.
    pub(crate) value_domain_rows: Option<&'a [serde_json::Map<String, Value>]>,
    /// Real Vega's `row_footer` (a rows-only facet's shared value axis)
    /// anchors at `ceil(cell x2)` (`vega-view-transforms/src/layout/
    /// grid.js:262`, `ceil` from `grid.js:13`'s own `agg` callback) — the
    /// mark's own right overhang past `plot_w`. `0.0` under
    /// `independent_scale` (`BarFacetPanelOpts.independent_scale`'s own
    /// doc comment).
    pub(crate) value_axis_right_overhang: f64,
    /// `multiples.scale: "independent"` (a mirror is a resolve error): real Vega drops the shared
    /// `row_footer` role entirely, embedding the value axis IN this
    /// panel's own `cell`, and never draws a zero/threshold rule at all
    /// (`BaselineFeature.apply` — the pooled zero/unity verdict is computed once over
    /// every panel's rows, which would be wrong for a panel with its own
    /// domain, so the feature skips the whole chart). This ONE flag gates
    /// both effects — see each read site's own doc comment.
    pub(crate) independent_scale: bool,
    /// Vega-Lite drops a bar's constant `stroke` from the legend swatches of a vertical
    /// columns or grid facet whose scales are shared, so each swatch is stroked in its own color.
    pub(crate) legend_swatch_stroke_is_fill: bool,
    /// The whole chart's rows, before the panel split. The color scale, the
    /// stack order and the legend are chart-level: Vega-Lite resolves a
    /// facet's `color` (and `xOffset`) scale shared across every panel, so a
    /// panel holding one series still paints it in that series' own palette
    /// slot, and a stack orders its segments by chart-wide totals
    /// (`emitters/bar.py`: `distinct_series_values(data, ...)`,
    /// `_stacked_series_order(series, data, ...)`, `data` = every row).
    pub(crate) chart_rows: &'a [serde_json::Map<String, Value>],
    /// A `center` stack's tallest pile across every panel, which each panel
    /// floats its categories against. Only a bar that is a single unit stacks
    /// the whole dataset; once the spec is layered (a sign split, a value
    /// label, a layer) each layer stacks inside its own panel, so `None`.
    pub(crate) center_stack: Option<CenterStack>,
    /// The both-signs stack across every panel that a `middle` label centers
    /// on: its explicit `stack` transform is not lifted into a panel.
    pub(crate) center_label_stack: CenterStack,
    /// A rows or grid facet shares its category scale across every panel (VL
    /// narrows `x` per panel only under a columns-only facet,
    /// `_domain_subset_narrowing_candidates`: `x` never narrows when
    /// `multiples.rows` is set), so the domain is the union over
    /// `chart_rows`. A columns-only facet's per-panel domain is either the
    /// full one or the narrowed one, both equal to the panel's own.
    pub(crate) share_cat_domain: bool,
    /// Where the category domain comes from: every panel's rows when shared, this panel's own
    /// when narrowed, either including rows dropped for a null numeric color.
    pub(crate) cat_rows: &'a [serde_json::Map<String, Value>],
    /// `emitters/bar.py::_is_color_1to1_with_x`, folded per panel: `true`
    /// only when EVERY panel with data maps each category to one color, the
    /// verdict that suppresses `xOffset` and keys the palette off the
    /// alphabetical domain. A single panel's rows can be 1:1 while a sibling
    /// is genuinely grouped, so the panel cannot decide it alone.
    pub(crate) color_one_to_one: bool,
    /// `axis_y.mirror`: each panel carries the invisible ghost rule layer that
    /// the mirrored right-edge axis rides.
    pub(crate) has_mirror: bool,
    /// A left value axis with no mirror: the composer paints it as the row's
    /// `row_header` copy, so the panel reserves and paints no right axis.
    pub(crate) ghost_only: bool,
    /// The value axis is a left one the panel renders on the right edge (see `render_facet`): it paints the
    /// mirror ghost's labels there, and the composer's `row_header` copy the primary's.
    pub(crate) left_primary: bool,
    /// The grouped column count a panel divided its band into.
    pub(crate) series: &'a Cell<usize>,
    /// This panel's canonical key, the `panel` an annotation names.
    pub(crate) panel_key: &'a str,
    /// This panel's place in `multiples::regroup` order; keeps clip ids unique.
    pub(crate) panel_ordinal: usize,
}

/// One panel's worth of axes + marks + grid + title/legend chrome for a
/// vertical bar — `rows`/`w`/`h`/`pad`/`bg` are the panel's OWN data and box
/// (the single-chart wrapper above passes the whole `Payload`'s; a facet
/// composite passes one row group and one narrower panel box per call).
/// `r`/`composed_strip`/`wide_series`/`layer`/`bar_layer`/`scatter_layer`
/// stay whole-chart (support_table, layers, and wide-measure folding are not
/// modeled inside a facet panel yet — `FacetFeature` already refuses a
/// `support_table` alongside `multiples` in production, and this module's
/// own `multiples` gate refuses before any of this is reachable per-panel).
pub(crate) fn render_vertical_panel(
    rows: &[serde_json::Map<String, Value>],
    w: f64,
    h: f64,
    pad: Option<&Padding>,
    bg: &str,
    // Still needed whole, alongside `rows` above, for a layer/bar-layer's
    // own cross-query dataset lookup (`resolve_layer_rows` reads
    // `payload.datasets`) — layers are chart-global, not per-panel, so this
    // stays the WHOLE envelope even when `rows`/`w`/`h` are one panel's own.
    payload: &Payload,
    fonts: &FontBook,
    r: &BarResolved,
    composed_strip: Option<&support_table_strip::SupportTableConfig>,
    wide_series: &[String],
    layers: &[OverlayLayer],
    // `None` from the single-chart wrapper; `Some` from the facet composer's
    // own `render_pass` closure — see `BarFacetPanelOpts`'s own doc comment
    // for every field.
    facet: Option<&BarFacetPanelOpts>,
) -> Result<(Drawn, Option<BarStripGeometry>, bool), Diagnostic> {
    let suppress_x_axis = facet.is_some_and(|f| f.suppress_x_axis);
    let view_w = if facet.is_some() { w } else { payload.view_width(crate::ViewScale::Discrete)? };
    let decision_w = facet.map_or(w, |f| f.decision_w);
    let paint_x_axis = !suppress_x_axis || facet.is_some_and(|f| f.cell_x_axis);
    let ghost_only = facet.is_some_and(|f| f.ghost_only);
    let paint_value_axis = facet.map_or(true, |f| f.paint_value_axis) && !ghost_only;
    let fold_axis_gutter = facet.map_or(true, |f| f.fold_axis_gutter);
    // Production's emitter rewrites a labeled or year-shaped x to ISO dates
    // before anything reads it; a layer reading its own query is rewritten
    // the same way below.
    let rows_normalized = crate::families::temporal::normalize_labeled_temporal(rows, &r.x)?;
    let rows = &*rows_normalized;
    let chart_rows_normalized =
        facet.map(|f| crate::families::temporal::normalize_labeled_temporal(f.chart_rows, &r.x)).transpose()?;
    let value_domain_rows = facet.and_then(|f| f.value_domain_rows);
    let value_axis_right_overhang = facet.map_or(0.0, |f| f.value_axis_right_overhang);
    // `independent_scale` gates two effects, each at its own read site:
    // the embedded axis's own law (the right-side anchor/ink reads
    // `plot_w` alone) and `zero_w`'s own suppression (no zero/threshold
    // rule at all — `BarFacetPanelOpts.independent_scale`'s own doc
    // comment carries both citations).
    let independent_scale = facet.is_some_and(|f| f.independent_scale);
    // Chart-level color context: a facet panel's own `rows` are only its
    // slice (`BarFacetPanelOpts::chart_rows`).
    let chart_rows = chart_rows_normalized.as_deref().or(facet.map(|f| f.chart_rows)).unwrap_or(rows);
    let cat_rows = facet.map_or(r.all_rows.as_deref().unwrap_or(rows), |f| f.cat_rows);
    let eff_title_style = crate::common::effective_title_style(
        r.style.title_font.as_ref(),
        r.title_style.as_ref(),
    );
    let title_ref = eff_title_style.as_ref().or(r.title_style.as_ref());
    let pad_l = pad.map(|p| p.left).unwrap_or(0.0);
    let pad_r = pad.map(|p| p.right).unwrap_or(0.0);
    let pad_t = pad.map(|p| p.top).unwrap_or(0.0);
    let pad_b = pad.map(|p| p.bottom).unwrap_or(0.0);
    let mark_band_width = r.style.mark.band_width;

    let cat_field = &r.x;
    let val_field = &r.y;
    let color_field = r.color.as_deref();
    let is_grouped = color_field.is_some();
    // See render_horizontal's comment: stacked shares the category domain
    // with grouped, but not the wider grouped-bar band padding or the
    // sub-band split.
    let is_stacked = is_grouped && r.stack != "none";
    let normalize = r.stack == "normalize";

    // Vertical bars: an authored sort, else query order (`CatOrder` —
    // production pins `sort: null`) — overridden below to chronological for a
    // temporal domain.
    let order = cat_order(r.sort.as_ref(), r.sorts_by_value_by_default(), val_field, &r.stack);
    let mut cat_domain = build_cat_domain(cat_rows, cat_field, val_field, order)?;
    // Each bar-type layer's own rows, mirroring the line layer's own
    // divergence signal below — `validate_layers` already confirmed every
    // dataset exists. Parallel to `bar_layers`.
    // A cross-query layer's own rows (`validate_layers` already confirmed
    // the dataset exists whenever `query_name` diverges), labeled and year x
    // values rewritten like the base's; every other layer shares the base's
    // own already-normalized rows, mirroring `_overlay.py::_resolve_layer_rows`'s
    // one divergence signal.
    let own_layer_rows = |query_name: Option<&str>, x: Option<&str>| -> Result<Cow<'_, [serde_json::Map<String, Value>]>, String> {
        if query_name == r.query_name.as_deref() {
            return Ok(Cow::Borrowed(rows));
        }
        let own_rows = resolve_layer_rows(payload, query_name, r.query_name.as_deref(), "bar")?;
        crate::families::temporal::normalize_labeled_temporal(own_rows, x.unwrap_or(cat_field))
    };
    let filled_x = x_resolution(r, chart_rows)?;
    let law = filled_x.resolution;
    let detected_data_tu: Option<TimeUnit> = match law.grain {
        crate::families::x_type::Grain::Calendar(tu) => Some(tu),
        _ => None,
    };
    // A time axis keys every datum by its bucket's start, or its UTC instant, a layer's too.
    let time_axis = law.vl_type == crate::families::temporal::VegaType::Temporal;
    // Every x date of the chart is read once, here, by the one date reader; what follows reads the axis.
    let mut x_axis = TimeAxis::new(
        AxisGrain {
            unit: detected_data_tu,
            part: match law.grain {
                crate::families::x_type::Grain::Part(part) => Some(part),
                _ => None,
            },
            band: false,
        },
        HashMap::new(),
    );
    // A literal `step` doubles onto band edges only where the x resolves a band
    // scale (`is_band_step`); a grained-temporal x draws d3's centered step.
    let band_x = layers.iter().any(OverlayLayer::is_step_curve)
        && crate::families::cartesian_x_errors::x_is_band(&payload.resolved, &payload.rows)?;
    let layer_rows_store = layers
        .iter()
        .map(|l| {
            use crate::families::layers::{layer_rows_scope, LayerRows};
            let l_rows = match layer_rows_scope(facet.is_some(), band_x && l.is_step_curve(), l.query_name(), r.query_name.as_deref()) {
                LayerRows::Chart => Cow::Borrowed(chart_rows),
                LayerRows::Panel | LayerRows::Dataset => own_layer_rows(l.query_name(), l.x())?,
            };
            Ok(if time_axis { super::time_x::at_bucket_starts(&mut x_axis, l_rows, l.x().unwrap_or(cat_field)) } else { l_rows })
        })
        .collect::<Result<Vec<_>, String>>()?;
    let layer_rows: Vec<&[serde_json::Map<String, Value>]> = layer_rows_store.iter().map(|c| &**c).collect();
    // Each kind's own layers, rows and authored positions.
    let mut bar_layers: Vec<&OverlayBarLayer> = Vec::new();
    let mut bar_layer_rows_list: Vec<&[serde_json::Map<String, Value>]> = Vec::new();
    let mut bar_layer_at: Vec<usize> = Vec::new();
    let mut line_layers: Vec<&OverlayLineLayer> = Vec::new();
    let mut line_layer_rows: Vec<&[serde_json::Map<String, Value>]> = Vec::new();
    let mut line_layer_at: Vec<usize> = Vec::new();
    let mut scatter_layers: Vec<&OverlayScatterLayer> = Vec::new();
    let mut scatter_layer_rows: Vec<&[serde_json::Map<String, Value>]> = Vec::new();
    let mut scatter_layer_at: Vec<usize> = Vec::new();
    for (position, (layer, &layer_own_rows)) in layers.iter().zip(&layer_rows).enumerate() {
        match layer {
            OverlayLayer::Bar(l) => {
                bar_layers.push(l);
                bar_layer_rows_list.push(layer_own_rows);
                bar_layer_at.push(position);
            }
            OverlayLayer::Line(l) => {
                line_layers.push(l);
                line_layer_rows.push(layer_own_rows);
                line_layer_at.push(position);
            }
            OverlayLayer::Scatter(l) => {
                scatter_layers.push(l);
                scatter_layer_rows.push(layer_own_rows);
                scatter_layer_at.push(position);
            }
        }
    }
    let line_band_step: Vec<bool> = line_layers.iter().map(|l| band_x && l.curve_name() == Some("step")).collect();
    let layer_band_step = line_band_step.contains(&true);
    // The union domain feeds the SAME band scale every bar (base or
    // layer) reads — `overlay_x_domain_values`/`_reconcile_x_domain`
    // (`_overlay.py`) pin this on the shared x scale before any axis
    // ticks or band positions are computed. Every layer contributes its
    // own x column in authored order.
    let layer_x_columns: Vec<(&[serde_json::Map<String, Value>], &str)> =
        layers.iter().zip(&layer_rows).map(|(l, rows)| (*rows, l.x().unwrap_or(cat_field))).collect();
    let x_union = union_x_domain(&cat_domain, &layer_x_columns, r.sort.is_some());
    if let Some(layer_only) = &x_union.paint_order {
        crate::families::cartesian::warn_x_domain_paint_order(&r.id, cat_field, layer_only);
    }
    cat_domain = x_union.domain;
    // a wide chart's color domain is the sorted, authored-
    // measure-COMPLETE `wide_series` (computed pre-fold in `render()`),
    // never a scan of the folded rows — a measure null on every row has
    // no folded row for `color_domain_of` to see, but still owns a
    // domain/palette/`n_series` slot.
    let color_domain =
        if !r.wide_measures.is_empty() { wide_series.to_vec() } else { color_field.map_or_else(Vec::new, |cf| color_domain_of(chart_rows, cf)) };
    let stack_order: Vec<String> = if is_stacked {
        let cf = color_field.expect("is_stacked implies color");
        crate::common::sorted_series_by_stack_order(
            &color_domain,
            chart_rows,
            cf,
            val_field,
            r.style.stack_order.as_deref(),
        )?
    } else {
        Vec::new()
    };
    // Palette slots: alphabetical color domain when color is 1:1 with the
    // category, baseline-first stack order otherwise (see color_1to1_with_cat).
    let color_is_1to1 = |cf: &str| {
        facet.map_or_else(|| color_1to1_with_cat(rows, cat_field, cf), |f| f.color_one_to_one)
    };
    let palette_domain: &[String] = if is_stacked
        && (r.palette.is_empty() || color_is_1to1(color_field.expect("is_stacked implies color")))
    {
        &color_domain
    } else {
        &stack_order
    };
    // The board's plan for this chart's color field, if it binds one
    // (#7167) — every fill/ink lookup below defers to it over the local
    // palette_domain/color_domain position.
    let category_scale: Option<&crate::common::CategoryColorScale> =
        color_field.and_then(|cf| crate::common::category_scale_for(&r.category_colors, cf));

    // Front-end contract: computed here (ahead of `legend_entries_typed`
    // below) since the legend's own `data-dbt-series` stamp gate needs it.
    // See the module doc above `tooltip_header_text` for the full law.
    // A ranged bar under scatter layers describes one category in a single
    // combined block (`structured_tooltip.py:_span_values`, swatched rows) —
    // unported, so neither the bar nor its dots carry a label, as on the
    // horizontal arm.
    let tooltip_labels = !r.style.tooltip_format.spec.is_empty();
    // A wide fold names its series by the measure's display label.
    let wide_labels = display_text::wide_measure_labels_for(&r.wide_measures, &r.field_labels)?;
    let numeric_names = r.numeric_series_names(&color_domain)?;
    let series_text = |s: &str| {
        numeric_names.get(s).cloned().unwrap_or_else(|| crate::common::wide_series_display(s, &wide_labels))
    };
    let order_domain =
        tooltip_order_domain(is_stacked, is_grouped, true, &stack_order, &color_domain, &r.legend, category_scale);
    // A layer joins the base's color field to one shared scale with an explicit
    // domain, which the series rank reads back (`_series_order_role`).
    let order_domain = order_domain
        .or_else(|| (is_grouped && (!line_layers.is_empty() || !bar_layers.is_empty() || !r.wide_measures.is_empty())).then(|| color_domain.clone()));
    // `_stamp_series_keys` (converters/chart.py:367-385): a promoted series
    // row with NO `ROLE_ORDER` rank baked alongside it. Stacked always
    // ranks (`order_domain` is `Some`), so this reduces to "grouped, no
    // board category-color scale". A combo's legend names its series too: the
    // base's literal label and the layer's own (`combo_series`) are series rows
    // with no rank.
    let stamp_legend_series =
        tooltip_labels
            && (is_grouped || !layers.is_empty())
            && order_domain.is_none();

    // support_table per_series: row order + label ink for a `per_series:`
    // entry — `_series_order_strip`'s stacked-bar branch
    // (support_table_attachment.py:2668-2679) is `sorted_series_by_stack_
    // order`, exactly this file's own `stack_order` (already the rail's own
    // row order, `stacked_rail_anchors(&cat_domain, &stack_order, ...)`);
    // ink is `common::series_ink` on `palette_domain` — the SAME lookup the
    // rail's own label ink uses (bar.rs's other `series_ink` calls).
    // Computed only when a per_series entry is actually present —
    // `series_ink` hard-errors past a too-small board category-color scale,
    // which a chart with no per_series entry must never pay for. Empty
    // (harmless) on a non-stacked/grouped bar, where `stack_order` is never
    // built — production's own single-column law for that shape
    // (`_per_series_column_header`) isn't ported, and `build_rows_composed`
    // refuses a per_series entry with an empty `series_order` by name.
    // Computed here (ahead of `legend_shown` below) because a normal
    // per_series entry also suppresses an otherwise-shown legend
    // (`_endpoint_labels_visible_default`'s own effect plus
    // `apply_chart_support_table_post_pass`'s `suppress_legend` —
    // support_table_attachment.py:161-169/4315).
    let has_per_series =
        composed_strip.is_some_and(|c| c.entries.iter().any(|e| e.per_series.is_some()));
    let per_series_order: Vec<String> = if has_per_series { stack_order.clone() } else { Vec::new() };
    let per_series_ink: Vec<String> = per_series_order
        .iter()
        .map(|s| {
            crate::common::series_ink(
                s,
                palette_domain,
                &r.series_palette,
                &r.style.series_label.dark_companion_palette,
                &r.style.series_label.pin_companions,
                category_scale,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;

    // axis_x = category axis (band, at bottom); axis_y = value axis (linear, at left/right).
    let ax_x = &r.style.axis_x;
    let x_edge = ax_x.x_edge();
    let ax_y = &r.style.axis_y;

    // Measure axis side is baked by the Python resolver to a concrete "left" or
    // "right" — no silent default; anything else is a compile-time bug upstream.
    let baked_on_right = match ax_y.position.as_deref() {
        Some("left") => false,
        Some("right") => true,
        Some(other) => {
            return Err(format!("bar: axis_y.position {other:?} is neither left nor right").into())
        }
        None => return Err("bar: axis_y.position absent (theme required)".into()),
    };
    // Dual axis: a pinned layer forces the BASE to the complementary side
    // regardless of its own baked position (`_resolved_layer_y_orients`,
    // `_overlay.py`) — the vertical bar family's own unlayered default is
    // "right", so an un-pinned base often needs this re-pin once a layer
    // claims a side. `axis_y.mirror` cannot combine with this — refused
    // earlier in `render()`.
    let y_sides = YSides::of(layers);
    let independent_y = y_sides.is_some();
    let bar_clip = |bl: &OverlayBarLayer, rows: &[serde_json::Map<String, Value>]| {
        crate::families::layers::layer_clip(crate::families::layers::LayerType::Bar, &bl.y, rows, ax_y, normalize, independent_y)
    };
    let scatter_clip = |sl: &OverlayScatterLayer, rows: &[serde_json::Map<String, Value>]| {
        crate::families::layers::layer_clip(crate::families::layers::LayerType::Scatter, &sl.y, rows, ax_y, normalize, independent_y)
    };
    let line_clip = |l: &OverlayLineLayer, rows: &[serde_json::Map<String, Value>]| {
        crate::families::layers::layer_clip(l.layer_type(), &l.y, rows, ax_y, normalize, independent_y)
    };
    let val_on_right = y_sides.as_ref().map_or(baked_on_right, |s| s.base == LayerSide::Right);
    // `axis_y.mirror`: a ghost axis repeating the labels on the opposite edge
    // (`mirror_axis.py`). A facet's composer paints its own, once.
    let ghost = ax_y.mirror_ghost("bar")?;
    let mirror_active = facet.is_none() && ghost.is_some();
    let left_flip = facet.is_some_and(|f| f.left_primary);
    // `_overlay.py`'s dual-axis re-pin (`:1086-1099`) pops the base axis's
    // own `labelAlign`/`labelPadding` from its VL dict whenever the orient
    // actually changes — "a house-format alias may have forced labelAlign
    // 'right' and computed a right-side labelPadding — those values are
    // wrong on the opposite side" — leaving NO `labelPadding` key at all,
    // not the themed `axis_y.labels.padding` (6.0). Vega's own compiled
    // default for an axis with no `labelPadding` is 2.0 (probed: stripping
    // both keys from `dual-y-axis-lab/revenue_vs_conversion`'s base axis —
    // already re-pinned by production — and re-rendering still draws the
    // first label at `x=-2`, `layout_facts`/`render_chart_artifact`
    // end-to-end, not a spec guess). The themed `ax_y.labels.padding` is
    // ONLY valid for the base's own un-re-pinned side.
    let base_label_pad = if val_on_right != baked_on_right { MOVED_BASE_LABEL_PAD } else { ax_y.labels.padding.unwrap_or(6.0) };

    let time: Option<TimeX> = if law.vl_type == crate::families::temporal::VegaType::Temporal {
        if super::time_x::has_year_below_100(&mut x_axis, rows, &r.x) {
            return Err(payload.painted_no_marks());
        }
        let layer_x: Vec<super::time_x::LayerX> = layers
            .iter()
            .zip(&layer_rows)
            .filter_map(|(layer, l_rows)| {
                let field = layer.x().unwrap_or(&r.x);
                let dates: Vec<String> =
                    l_rows.iter().filter(|row| row.get(field).is_some_and(|v| !v.is_null())).map(|row| row_str(row, field)).collect();
                (!dates.is_empty()).then(|| super::time_x::LayerX { dates, x_field: field.to_string(), on_base_x: field == r.x, spans_buckets: matches!(layer.layer_type(), crate::families::layers::LayerType::Bar) })
            })
            .collect();
        Some(TimeX::read(&mut x_axis, r, rows, chart_rows, &layer_x, r.style.mark.labels.as_ref().is_some_and(|ml| ml.visible) && r.wide_measures.is_empty())?)
    } else {
        None
    };
    let rows = time.as_ref().map_or(rows, |t| t.rows.as_slice());
    if let Some(t) = &time {
        cat_domain = t.cats.clone();
    }
    // A right-orient axis whose labels author no `align` keeps Vega-Lite's
    // default: `labelAlign: left` at the themed `labelPadding`, the ink
    // growing away from the plot. Production flip-aligns them and bakes the
    // measured gutter into `labelPadding` only when `align == orient`
    // (`measure_axis_to_vl`), which a house-format alias forces, so an
    // authored d3 `axis_y.labels.format` (`$,.0f`) reaches this arm. With no
    // number, authored domain or authored ticks to measure the labels from it
    // drops that `labelAlign` again (`_fallback_reversed_label_align`).
    // A date measure's rows carry day numbers here, but production measures date strings: nothing to read.
    let date_measure = r.measure_type.as_deref() == Some("temporal") && r.y_start.is_some();
    let gutter_unmeasurable = ax_y.tick_values.is_empty()
        && ax_y.authored_continuous_domain().is_none()
        && (date_measure || !rows.iter().any(|row| row_num(row, val_field).is_some()));
    let val_away = val_on_right && (ax_y.labels.align_kept("right").is_none() || gutter_unmeasurable);
    let domain_strs: Vec<&str> = cat_domain.iter().map(String::as_str).collect();
    // The authored strings, ahead of the labeled-bucket normalization.
    let authored_x: Vec<String> =
        facet.map_or(payload.rows.as_slice(), |f| f.chart_rows).iter().map(|row| row_str(row, cat_field)).collect();
    let authored_x: Vec<&str> = authored_x.iter().map(String::as_str).collect();
    let x_standard_dates = crate::families::temporal::x_values_are_standard_dates(&authored_x);
    // Front-end contract only, same law as line/area's own `x_is_standard_date`
    // (`infer_vega_type_from_data`'s STRICT `all_temporal` gate): a daily-date x
    // that `detect_time_unit` doesn't bucket still headers through the
    // continuous-date fallback, never the raw ISO string.
    let x_is_standard_date = crate::families::temporal::x_values_are_standard_dates(&domain_strs);
    // What the labels paint is keyed by the grain they resolve to
    // (`resolve_label_time_unit`), not the data's own.
    let label_unit = detected_data_tu
        .filter(|_| time.is_none())
        .map(|tu| LabelUnit::resolve(tu, ax_x.labels.time_unit.as_deref()))
        .transpose()
        .map_err(|e| format!("bar: {e}"))?;
    // A standard date no calendar grain explains paints as raw bands here.
    let raw_date_format = if time.is_none() && detected_data_tu.is_none() && x_is_standard_date {
        AuthoredTimeFormat::parse("bar", ax_x.labels.spec())?
    } else {
        None
    };
    let authored_time_format = match label_unit {
        Some(unit) => AuthoredTimeFormat::parse("bar", axis_time_format(ax_x.labels.spec(), &ax_x.grain_formats, unit.grain()))?,
        None => None,
    };
    // Production's tooltip detects its own grain over the rows' dates, not the axis's.
    let tooltip_tu = time.as_ref().map_or(detected_data_tu, |t| t.tooltip_grain);
    let grain_header_format = AuthoredTimeFormat::parse("bar", grain_format(&ax_x.grain_formats, tooltip_tu))?;
    // Timed rows within two days head their tooltip with the time of day instead (`subday_header_for`).
    let subday_header = time.as_ref().filter(|_| tooltip_tu.is_none()).and_then(|t| crate::families::subday::Header::of(&t.timed, ax_x.labels.clock));
    let x_is_standard_date = x_is_standard_date && subday_header.is_none();
    let header_date = |date: String| -> String {
        match (&subday_header, time.as_ref().and_then(|t| t.day_of(&date))) {
            (Some(header), Some(at)) => header.text(at).expect("a plotted date formats"),
            _ => date,
        }
    };

    // Temporal domain: VL's temporal encoding always sorts chronologically,
    // regardless of row/query order — ISO date strings sort correctly as text.
    if detected_data_tu.is_some() && time.is_none() {
        // A band scale keeps a value the grain detection tolerated (under a tenth of the column) as a category,
        // after the dates.
        let (mut dates, mut categories) = (Vec::new(), Vec::new());
        for value in cat_domain.drain(..) {
            match x_axis.add(&value) {
                Ok(()) => dates.push(value),
                Err(crate::time_axis::Unreadable::NotADate(_)) => categories.push(value),
                Err(outside) => return Err(outside.diagnostic(&r.id, cat_field, "bar")),
            }
        }
        x_axis.sort_by_instant(&mut dates);
        categories.sort();
        cat_domain = dates.into_iter().chain(categories).collect();
        // Production's ordinal scaffold (`gap_fill_ordinal_time` →
        // `complete_ordinal_time_series`): every calendar bucket in the
        // span gets a band, the missing ones empty. The scaffold spans the grain
        // the fill ran at, which the filled domain may detect as a finer one
        // (yearly April starts read as quarters).
        if let Some(fill_tu) = filled_x.filled {
            let widened: Vec<String> =
                cat_domain.iter().cloned().chain(crate::annotation::x_dates_outside(&cat_domain.iter().filter_map(|date| x_axis.get(date).map(|instant| instant.at)).collect::<Vec<_>>(), &r.annotations)).collect();
            cat_domain = complete_buckets(&widened, fill_tu, ax_x.fiscal_year_start_month);
        }
    }
    // B5 — authored axis_x.labels.expr: rewrite every category label BEFORE
    // it is measured (tilt decision, rotation reach) and drawn — the same
    // gutter/ink/text triple `cat_domain` otherwise feeds directly. `cat_domain`
    // itself stays raw: it is still the BandScale/data-lookup key. A
    // calendar-grain axis draws `temporal_labels` instead, which rewrite their
    // own text from the same expression.
    let labels_expr = ax_x
        .labels
        .expr
        .as_deref()
        .filter(|_| time.is_none())
        .map(|expr| crate::labels_expr::Expr::parse(expr).map(|e| e.on("x")).map_err(|e| format!("bar: labels.expr {expr:?}: {e:?}")))
        .transpose()?;
    let cat_labels: Vec<String> = match &labels_expr {
        None => match raw_date_format {
            Some(format) => cat_domain
                .iter()
                // A band keeps a value that is no date as the category it is.
                .map(|c| x_axis.civil(c).map_or_else(|_| c.clone(), |(y, m, d)| format.text(y, m, d)))
                .collect(),
            None => cat_domain.clone(),
        },
        Some(expr) => cat_domain
            .iter()
            .enumerate()
            .map(|(i, c)| expr.rewrite_band(c, i, cat_domain.len()).map_err(|e| format!("bar: {e}")))
            .collect::<Result<_, _>>()?,
    };
    let cat_labels = cat_labels
        .iter()
        .map(|label| ax_x.labels.cased(label))
        .collect::<Result<Vec<String>, String>>()?;

    // An independent facet scale bakes no stacked domain: each panel stacks to
    // its own tallest total, which Vega rounds out.
    let stacked_domain_max = r.stacked_domain_max.or_else(|| {
        (independent_scale && r.stack == "zero")
            .then(|| d3rs::ticks::d3_nice(0.0, tallest_total(&category_totals(rows, cat_field, val_field)), 10).1)
    });
    let layer_days = layer_day_numbers(layers.iter().zip(layer_rows.iter().copied()));
    let date_axis_at = |count: usize| match (date_measure, r.y_start.as_deref()) {
        (true, Some(start_field)) => {
            span::date_axis(value_domain_rows.unwrap_or(rows), [val_field, start_field], &layer_days, count)?
                .map(Some)
                .ok_or_else(|| payload.painted_no_marks())
        }
        _ => Ok(None),
    };
    let date_axis = date_axis_at(ax_y.ticks.count.map_or_else(|| vl_default_tick_count(h), |count| count as usize))?;
    // A date axis with no authored count re-ticks at the final plot height, like an unbaked numeric one.
    let date_rungs = |plot_h: f64| -> Result<Option<(Vec<f64>, Vec<String>)>, Diagnostic> {
        match (&date_axis, ax_y.ticks.count) {
            (Some(_), None) => Ok(date_axis_at(vl_default_tick_count(plot_h))?.map(|axis| (axis.ticks, axis.labels))),
            _ => Ok(None),
        }
    };
    let rail_pins_domain = r.stack == "center" && r.style.endpoint_labels.visible;
    if date_axis.is_none() && !rail_pins_domain && collapsed_authored_domain(ax_y, value_domain_rows.unwrap_or(rows), val_field) {
        return Err(payload.painted_no_marks());
    }
    let (mut v_domain_min, mut linear_domain_max, mut tick_values) = match &date_axis {
        Some(axis) => (axis.domain.0, axis.domain.1, axis.ticks.clone()),
        None => value_domain_and_ticks(
            value_domain_rows.unwrap_or(rows),
            cat_field,
            val_field,
            ax_y,
            &r.stack,
            stacked_domain_max,
            true,
            r.y_start.as_deref(),
            rail_pins_domain,
            labels_on_zero_stack(r),
        )?,
    };
    // The single-series and grouped columns, their value labels and a
    // dual-axis layer position from the zero rule wherever the floor puts it.
    // A shared-scale layer, a facet panel and a genuine-zero label position
    // from a zero-at-the-bottom baseline (or an unprobed one), so under a
    // negative floor they would scale the axis one way and draw the marks
    // another. The stacked loops read the floor themselves (Vega's
    // stackZero law).
    // A shared-scale overlay layer merges the base's y encoding into a
    // second `layer[]` entry sharing ONE Vega-Lite scale — compiling that
    // union flips the scale's `nice` default from unset (an un-nested
    // baked `domainMax` renders EXACT, never rounded) to `true`, which then
    // extends the baked domain past its own headroom pin at render time
    // (`nest_zero_rule`'s own docstring names the identical VL quirk for
    // its dual-axis zero-rule nesting; production does not counter it here
    // the way it does there, so the shared-scale case actually renders the
    // extended domain — probed: `column_line_combo_review`'s baked
    // `domainMax` 165240 renders as `d3_nice(0, 165240, 10) == (0, 180000)`,
    // matching the reference's own "values from 0 to 180000" axis and its
    // Jan bar top). Fires for the layer's own drawing too, since both marks
    // share this one scale. The count is d3's own FIXED nice count (10),
    // NEVER the axis's own displayed `tickCount` — the SAME distinction the
    // dual-axis layer's own domain law already draws (`d3_nice(0, max, 10)`
    // below) — probed on `grouped-bar-with-layer`: `ax_y.ticks.count` bakes
    // 6, `d3_nice(0, 248.4, 6) == 250` (the port's pre-fix, WRONG value),
    // but the reference's own axis reads "values from 0 to 260" —
    // `d3_nice(0, 248.4, 10) == 260`, matching exactly.
    // An unpinned bar-type or scatter-type layer shares this same one
    // Vega-Lite scale too (a pinned layer owns its own scale), so the identical
    // quirk fires for either unconditionally — probed:
    // `charts/extensibility_1`'s `lollipop` bakes `stacked_domain_max`
    // 84.5, and the reference's own axis reads "values from 0 to 100" —
    // `d3_nice(0, 84.5, 10) == 100`, matching exactly (6 gridlines, not 5).
    let shares_this_scale = !layers.is_empty() && !independent_y && date_axis.is_none();
    if shares_this_scale {
        let ranged = r.y_start.is_some();
        // An authored pair is nice-rounded too once a layer joins the scale
        // (it is verbatim without one), from its own low end.
        let floored = ranged || ax_y.authored_continuous_domain().is_some() || v_domain_min != 0.0;
        let (niced_min, niced_max, niced_ticks) =
            nice_shared_scale(ax_y, if floored { v_domain_min } else { 0.0 }, linear_domain_max);
        if let Some(ticks) = niced_ticks {
            tick_values = ticks;
        }
        if floored {
            v_domain_min = niced_min;
        }
        linear_domain_max = niced_max;
    }
    let zero_text = zero_label(r, rows, &LinearScale::new(v_domain_min, linear_domain_max, h, 0.0))?;
    // A zoomed (`zero: false`) floor above 0 leaves the columns' zero baseline
    // below the plot. A chart of nothing but such bars paints no mark at all and
    // production raises it (`rendering.py`: every mark degenerate while some
    // measure is nonzero); beside a layer or in a facet the bars draw past the
    // plot edge, unclipped. A ranged bar's fitted floor sits above 0 by design, and
    // an authored pair keeps its bars (they hang past the edge, unclipped).
    if v_domain_min > 0.0 && r.y_start.is_none() && ax_y.authored_continuous_domain().is_none() {
        let bar_only = layers.is_empty() && facet.is_none();
        let nonzero = rows.iter().any(|row| row_num(row, val_field).is_some_and(|v| v != 0.0));
        if bar_only && nonzero {
            return Err(payload.painted_no_marks());
        }
    }
    let val_tick_labels = match &date_axis {
        Some(axis) => axis.labels.clone(),
        None => format_tick_labels(&tick_values, ax_y, normalize, "y")?,
    };

    // ── Layout geometry ────────────────────────────────────────────────────
    // See render_horizontal: r.legend.visible already is production's final
    // legend-vs-endpoint-labels decision — trust it, don't re-derive it.
    // ONE exception: a normal per_series entry suppresses an otherwise-shown
    // legend at RENDER time, not resolve time (`_suppress_series_legend`,
    // called from `apply_chart_support_table_post_pass` only after the base
    // spec — legend included — is already built,
    // support_table_attachment.py:4315/1955-1959) — so `resolved.legend.
    // visible` alone is stale here and must be re-derived, the one
    // documented departure from "trust it". A layer forces the legend on
    // even off a colorless single-series base — production's own
    // `force_legend_visible` bakes `legend.visible: true` for exactly this
    // shape (probed: `column_line_combo_review`'s base has no `color` field
    // yet `resolved.legend.visible` is already `true`), so `!color_domain.
    // is_empty()` alone would wrongly suppress it here.
    let legend_shown = r.legend.visible
        && (!color_domain.is_empty() || !layers.is_empty())
        && !composed_strip.is_some_and(|c| c.retires_series_legend());
    let legend_top = legend_shown && r.legend.position.edge == "top";
    // A layered chart's legend is the label-scale legend
    // (`_mixed_mark_legend_symbols`, `_overlay.py:649`), never the color-
    // field legend `stacked_legend_entries` builds: one entry per painted
    // series — the base's own single fill under its axis title text, then
    // each layer's own color under its own title text — each carrying its
    // OWN mark's symbol so a bar base and a line layer never share one
    // glyph. `axis_title_text` mirrors production's `label or
    // default_axis_title(y_field)` (an authored `y_label` on the base,
    // `layer.label` on the layer, else the field's own slug).
    // Computed whenever the base has its own color domain, layer or not:
    // a layer over a grouped base keeps the grouped legend's OWN entries
    // (see `legend_entries_typed` below), it does not fall back to the
    // layer-only 2-entry shape.
    let legend_entries: Vec<(String, String)> = if legend_shown && !color_domain.is_empty() && r.numeric_color.is_none() {
        if is_stacked {
            let reversed: Vec<String> = stack_order.iter().rev().cloned().collect();
            stacked_legend_entries(&reversed, palette_domain, &r.series_palette, category_scale, &wide_labels)?
        } else {
            stacked_legend_entries(
                &grouped_legend_domain(&color_domain, &r.legend, &wide_labels),
                &color_domain,
                &r.series_palette,
                category_scale,
                &wide_labels,
            )?
        }
    } else {
        Vec::new()
    };
    let legend_title = merged_legend_title(
        if r.numeric_color.is_some() { String::new() } else { legend_title_text(color_field.unwrap_or(""), &r.legend, &r.field_labels)? },
        color_field.is_some(),
        crate::families::layers::shared_color_fields(layers.iter().zip(layer_rows.iter().copied())),
        &r.legend,
    );
    let base_legend_label = crate::common::axis_title_text(val_field, r.y_label.as_deref(), &r.field_labels)?;
    // `_overlay.py`: every layer claims its color on the base's shared
    // `color:{datum}` scale in authored order — the base's own entries (one per
    // grouped/color-split series, or the one labeled fill of a colorless base)
    // come first, then each unauthored layer takes the first palette color
    // neither the board nor the scale has taken (probed:
    // `grouped-bar-with-layer`'s reference line uses `palette[2]`, the base's
    // 2-entry color domain's next free color).
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
                let domain = if is_stacked { palette_domain } else { &color_domain };
                Ok((v.clone(), crate::common::series_color(v, domain, &r.series_palette, category_scale)?))
            })
            .collect::<Result<_, String>>()?
    };
    let mut layer_scale = LayerScale::new(&base_scale_entries, color_field.is_some() && !numeric_base);
    // Every layer claims in authored order: a colorless one its label and the
    // color it paints, one grouping by its own color each value the scale
    // lacks, one sharing the base's color field nothing new.
    let claims = claim_layers(
        &mut layer_scale,
        &layers.iter().zip(layer_rows.iter().copied()).collect::<Vec<_>>(),
        &r.field_labels,
    )?;
    let bar_layer_fills: Vec<Option<String>> = layers
        .iter()
        .zip(&claims)
        .filter(|(layer, _)| matches!(layer, OverlayLayer::Bar(_)))
        .map(|(_, claim)| claim.fill.clone())
        .collect();
    let bar_layer_claims: Vec<&LayerClaim> = layers
        .iter()
        .zip(&claims)
        .filter(|(layer, _)| matches!(layer, OverlayLayer::Bar(_)))
        .map(|(_, claim)| claim)
        .collect();
    let line_claims: Vec<&LayerClaim> = layers
        .iter()
        .zip(&claims)
        .filter(|(layer, _)| matches!(layer, OverlayLayer::Line(_)))
        .map(|(_, claim)| claim)
        .collect();
    let scatter_claims: Vec<&LayerClaim> = layers
        .iter()
        .zip(&claims)
        .filter(|(layer, _)| matches!(layer, OverlayLayer::Scatter(_)))
        .map(|(_, claim)| claim)
        .collect();
    let numeric_legends = numeric_legends_of(r.numeric_base()?, layers.iter().zip(&claims), &r.legend, &r.id)?;
    let layered_legend = LayeredBarLegend::new(
        r,
        legend_shown,
        &base_legend_label,
        &legend_entries,
        &base_scale_entries,
        if numeric_base { &[] } else { &color_domain },
        &layer_scale,
        &claims,
        &layers.iter().collect::<Vec<_>>(),
    )?;
    let legend_entries_typed: Vec<LegendEntry> = layered_legend.entries(r, &layer_scale);
    let legend_labels = limited_labels(&legend_entries_typed, numeric_legends.unlisted_line, &legend_title, &r.legend, fonts, legend_room(&r.legend, Some(facet.map_or(view_w, |f| f.legend_w))))?;
    let series_keys = crate::selected::series_keys(&legend_entries_typed, &bar_link_keys(r, &color_domain, &wide_labels));
    let legend_entries_typed = r.selection.legend(with_drawn_labels(&legend_entries_typed, &legend_labels), &series_keys);

    let val_font_size = ax_y.labels.font.size_or(11.0);
    // Own-side alignment (the default: value axis on the right with labels
    // flipped inward) replaces the themed labelPadding with the measured
    // breathing room — see VALUE_AXIS_BREATHING_ROOM. An explicit left-side
    // value axis keeps its normal away-from-plot growth, which Vega-Lite's
    // own autosize already reserves correctly from the themed padding.
    let val_pad = if val_on_right && !val_away {
        VALUE_AXIS_BREATHING_ROOM
    } else {
        ax_y.labels.padding.unwrap_or(6.0)
    };
    // Vega anchors an axis label at `sign * ((tickSize if ticks visible else
    // 0) + labelPadding)` (`utils.py:853-860`, `measured_label_padding`), so a
    // value axis's labels and ink sit `val_tick_size` past the gutter's own
    // origin, on either side; `facet_compose` reserves the same term for the
    // ghost axis, and the dual-axis layer's own axis reads the same tick style.
    let val_tick_size = ax_y.tick_len();
    let val_family = ax_y.labels.font.family.as_deref().unwrap_or("sans-serif");

    // Value-axis margin = widest value tick label + padding from label to plot edge;
    // this margin sits on whichever side `val_on_right` names, the other side
    // gets only the fixed no-label padding.
    let max_val_w = val_tick_labels
        .iter()
        .try_fold(0.0_f64, |acc, s| {
            fonts.measure_shaped(s, val_family, val_font_size).map(|mw| acc.max(mw))
        })?;
    // Axis titles: category (bottom, chart.x/ax_x) and value (rotated on
    // whichever side val_on_right names, chart.y/ax_y) — see
    // render_horizontal's comment for the channel mapping (unchanged for
    // vertical bars: no flip).
    let cat_title_text = axis_title_text(cat_field, r.x_label.as_deref(), &r.field_labels)?;
    // A folded val has no single measure field to title from — fall back to
    // the joined measure names (`wide_measures_title`), same as an authored
    // `y_label` always would override either fallback.
    let val_title_text = if r.wide_measures.is_empty() {
        axis_title_text(val_field, r.y_label.as_deref(), &r.field_labels)?
    } else {
        match r.y_label.as_deref() { Some(l) => crate::common::collapse_title_newlines(l), None => crate::common::wide_measures_title(&r.wide_measures, &r.field_labels)? }
    };
    let axis_titles = DualAxisTitles::new(independent_y, ax_y, r.y_label.as_deref());
    let layer_title_style = axis_titles.layer(ax_y);
    let val_title_style = axis_titles.base(ax_y);
    let ((x_extent, x_panels), (y_extent, y_panels)) = facet.map_or(((w, 1.0), (h, 1.0)), |o| o.title_boxes);
    let cat_title_paint = crate::common::wrapped_axis_title(&r.id, "x_label", &cat_title_text, &ax_x.title.font, x_extent, x_panels, fonts)?;
    let val_title_paint = crate::common::wrapped_axis_title(&r.id, "y_label", &val_title_text, &ax_y.title.font, y_extent, y_panels, fonts)?;
    let cat_title_w = crate::common::widest_title_line(&ax_x.title, &cat_title_paint, fonts)?;
    let val_title_w = crate::common::widest_title_line(val_title_style, &val_title_paint, fonts)?;
    let show_rail_v = r.style.endpoint_labels.visible && color_field.is_some();
    let sl = &r.style.series_label;
    let el = &r.style.endpoint_labels;
    // A grouped bar's rail anchors each series at its own last bar, so its label reads
    // "{value} {name}" in the value axis's resolved format; a stacked anchor is a
    // cumulative midpoint and stays name-only.
    let grouped_rail = match color_field.filter(|_| show_rail_v && !is_stacked) {
        Some(color) => Some(grouped_rail_anchors(rows, &cat_domain, cat_field, val_field, color, ax_y.authored_continuous_domain())?),
        None => None,
    };
    let rail_value = |series: &str| -> Result<Option<String>, String> {
        let anchor = grouped_rail.iter().flat_map(|(anchors, _)| anchors).find(|(s, _)| s == series);
        anchor.map(|(_, value)| crate::families::cartesian::rail_value_text(*value, ax_y)).transpose()
    };
    let rail_field = if r.series_from_measures { "y" } else { "color" };
    let rail_font = (sl.font_family.as_str(), sl.font_size, sl.font_weight.as_str());
    let (rail_pane_w, rail_plans) = if show_rail_v {
        let entries = color_domain.iter().map(|series| Ok((series.clone(), rail_value(series)?, series.clone()))).collect::<Result<Vec<_>, String>>()?;
        let plans = crate::families::cartesian::plan_rail_labels(&entries, w, rail_font, fonts)?;
        let width_of = |texts: &[String]| {
            crate::families::cartesian::rail_pane_width(texts, &sl.font_family, sl.font_size, &sl.font_weight, fonts, w)
        };
        let reserved = crate::families::cartesian::rail_reserved_lines(&plans);
        (width_of(&reserved)?, plans)
    } else {
        (0.0, HashMap::new())
    };
    // Production crowds the rail concat's pane, so the time axis resolves at its width.
    let rail_span = if show_rail_v { rail_pane_w + el.label_offset } else { 0.0 };
    let time_axis_w = w - rail_span;
    let time_axis: Option<TemporalXAxis> =
        time.as_ref().map(|t| t.axis(r, time_axis_w, cat_title_paint.clone(), Some(crate::contract::LabelKey::X), fonts)).transpose()?;
    if time.as_ref().is_some_and(|t| t.width_is_nan) || time_axis.as_ref().is_some_and(TemporalXAxis::raises_no_marks) {
        return Err(payload.painted_no_marks());
    }

    let cat_font_size = ax_x.labels.font.size.ok_or(
        "bar: axis_x.labels.font.size absent (theme required)",
    )?;
    let cat_pad = ax_x.labels.padding.ok_or(
        "bar: axis_x.labels.padding absent (theme required)",
    )?;
    let cat_family = ax_x.labels.font.family.as_deref().unwrap_or("sans-serif");

    // The cadence ladder measures against production's usable width
    // (`_temporal_layout`: `chart_width * label_usable_ratio`).
    let avail_w = w - pad_l - pad_r;
    // Once a bar's calendar labels grow sparser than its buckets, ticks show
    // under the painted labels, overriding the theme's hidden default.
    let mut x_ticks_restored = false;
    // The label vocabulary and the period the ladder settled on, which a band axis centers its labels over.
    let mut label_periods: Option<crate::families::temporal::LabelPeriods> = None;
    let mut temporal_tilt = 0.0_f64;
    // `AxisLabelLayout.label_block_height` of a date axis the tilt resolver
    // laid out; a bottom strip's axis gap grows by what it exceeds the
    // reserved lines.
    let mut temporal_block = cat_font_size;
    let mut temporal_fold_tu = None;
    let bottom_strip = r.effective_support_table_style.as_ref().is_some_and(|s| s.position == "bottom");
    let mut x_parity_separation: Option<f64> = None;
    let temporal_labels: Option<Vec<TemporalLabel>> = match (detected_data_tu, label_unit) {
        (Some(data_tu), Some(unit)) => {
            let authored_label_tu =
                ax_x.labels.time_unit.as_deref().and_then(parse_authored_time_unit);
            let mut measure = |s: &str| ax_x.labels.measure(fonts, s, cat_font_size);
            let resolved = bar_temporal_labels(
                &cat_domain,
                data_tu,
                unit,
                ax_x,
                authored_time_format,
                w * cadence_ratio(r)?,
                w,
                r.style.label_usable_ratio,
                labels_expr.as_ref(),
                fonts,
                cat_font_size,
            )?;
            x_ticks_restored = resolved.ticks_restored;
            if let LabelUnit::Calendar(format_tu) = unit {
                label_periods = Some(crate::families::temporal::LabelPeriods {
                    format_tu,
                    visibility_tu: resolved.visibility_tu,
                    thinned: resolved.year_step > 1,
                });
            }
            temporal_tilt = resolved.angle.unwrap_or(0.0);
            x_parity_separation = resolved.parity.then(|| ax_x.labels.min_gap.unwrap_or(0.0));
            // A bucketed cadence that still does not clear flat is tilted (or
            // parity-thinned) by production (`_temporal_layout`). The crowding
            // width is the panel's own `chart_width * label_usable_ratio`; a
            // tilt folds the two-row labels inline, parity thinning stays
            // unmodeled.
            let mut tls = resolved.labels;
            if ax_x.labels.angle.is_none()
                && matches!(unit, LabelUnit::Calendar(_))
                && labels_expr.is_none()
                && (bottom_strip
                    || (facet.is_some()
                        && matches!(data_tu, TimeUnit::Year | TimeUnit::YearQuarter | TimeUnit::YearMonth)))
            {
                // Production lays the labels out at the card width less the endpoint
                // rail's span (`reserved_width`).
                let layout_w = if show_rail_v {
                    w - (rail_pane_w + r.style.endpoint_labels.label_offset)
                } else {
                    w
                };
                if let Some(overlap) = ax_x.labels.overlap.as_ref().filter(|o| o.tilt) {
                    let ratio = r.style.label_usable_ratio.ok_or(
                        "bar: label_usable_ratio absent (theme required for overlap.tilt)",
                    )?;
                    let increments = ax_x.labels.tilt_increments.as_deref().ok_or(
                        "bar: labels.tilt_increments absent (theme required for overlap.tilt)",
                    )?;
                    let layout = resolve_vega_label_layout(
                        &leading_dates(&cat_domain),
                        data_tu,
                        authored_label_tu.unwrap_or(data_tu),
                        authored_label_tu.is_none(),
                        layout_w * ratio,
                        false,
                        false,
                        false,
                        ax_x.fiscal_year_start_month,
                        overlap.skip,
                        true,
                        Some(increments),
                        cat_font_size,
                        authored_time_format,
                        &mut measure,
                    )?;
                    if layout.parity {
                        x_parity_separation = Some(ax_x.labels.min_gap.unwrap_or(0.0));
                    }
                    temporal_block = layout.block_height;
                    if layout.angle != 0.0 {
                        temporal_tilt = layout.angle;
                        temporal_fold_tu = Some(layout.format_tu);
                        for tl in &mut tls {
                            let (text, rows, ..) = crate::families::line::tilted_temporal_label(tl, layout.angle, layout.format_tu, x_edge);
                            let row2 = if rows == 2 { tl.row2.clone() } else { String::new() };
                            *tl = TemporalLabel { domain_idx: tl.domain_idx, row1: text, row2 };
                        }
                    }
                }
            }
            Some(tls)
        }
        _ => None,
    };
    // An authored angle wins before any type routing
    // (`resolve_axis_x_overlap`); then a date-valued axis (every value parses
    // as a date, the temporal verdict) takes production's `_temporal_layout`,
    // whose tilt after the cadence choice is modeled only on a facet's
    // bucketed axis or under a bottom strip (`temporal_tilt`).
    let all_dates = leading_dates(&cat_domain).len() == cat_domain.len();
    let cat_angle_raw = match ax_x.labels.angle {
        Some(angle) => angle,
        None if all_dates => temporal_tilt,
        None => {
            // Production's tilt is picked in Python from the raw band values, before Vega runs any `labelExpr`.
            let measured = if labels_expr.is_some() { &cat_domain } else { &cat_labels };
            category_label_layout(&ax_x.labels, measured, decision_w, r.style.label_usable_ratio, fonts, cat_font_size)?.angle
        }
    };
    let mut cat_angle = vega_fit::normalize_angle(cat_angle_raw);
    // The plot the edge slices are judged against: the card less the value axis, before the fit settles it.
    let plot_w_estimate = (avail_w - val_tick_size - base_label_pad - max_val_w.min(ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT))).max(1.0);
    // Labels named over a calendar period coarser than the buckets center on that period.
    let band_intervals = match (detected_data_tu, label_periods) {
        (Some(data_tu), Some(periods))
            if temporal_labels.is_some()
                && x_ticks_restored
                && crate::families::temporal::interval_labels_allowed(&ax_x.labels, ax_x.ticks.time_unit.is_some() || ax_x.ticks.count.is_some(), authored_time_format.is_some()) =>
        {
            let mut measure = |s: &str| ax_x.labels.font.measure_weighted(fonts, s, cat_font_size);
            crate::families::temporal::band_interval_axis(
                &cat_domain,
                data_tu,
                periods,
                plot_w_estimate,
                ax_x.fiscal_year_start_month,
                &mut measure,
            )
            .map_err(|e| format!("bar: {e}"))?
        }
        _ => None,
    };
    // Interval labels step up the cadence instead of thinning.
    if band_intervals.is_some() {
        x_parity_separation = None;
        // Interval labels sit flat; a tilt picked for the on-tick layout no longer applies.
        cat_angle = 0.0;
        temporal_block = cat_font_size;
    }
    let label_rows = match (&band_intervals, &temporal_labels) {
        (Some(iv), _) => iv.labels.iter().map(|l| l.rows()).max().unwrap_or(1),
        (None, Some(ls)) => ls.iter().map(|l| l.rows()).max().unwrap_or(1),
        (None, None) => cat_labels.iter().map(|c| crate::text::line_count(c)).max().unwrap_or(1),
    };
    // A bottom strip's axis gap grows to clear the label block
    // (`AxisLabelLayout.label_block_height`): the formatted vocabulary at an
    // authored angle, the resolver's padded widths at a picked tilt.
    let strip_axis_gap_growth = match r.effective_support_table_style.as_ref() {
        Some(style) if style.position == "bottom" => {
            let quantitative =
                crate::families::temporal::infer_vega_type(rows, cat_field) == crate::families::temporal::VegaType::Quantitative;
            let block = match (ax_x.labels.angle, detected_data_tu) {
                (Some(_), _) if quantitative => cat_font_size,
                (Some(angle), Some(data_tu)) if all_dates => {
                    let mut measure = |s: &str| ax_x.labels.measure(fonts, s, cat_font_size);
                    crate::families::temporal::pinned_block_height(
                        &cat_domain,
                        ax_x.labels.time_unit.as_deref().and_then(parse_authored_time_unit).unwrap_or(data_tu),
                        authored_time_format,
                        angle,
                        cat_font_size,
                        &mut measure,
                    )?
                }
                (None, _) if all_dates => temporal_block,
                (pinned, _) => {
                    // The picked tilt measures each label padded by a word space; an authored
                    // angle measures the cased raw band values bare, whatever `labels.expr` shows.
                    let widths = vega_fit::category_label_widths(&ax_x.labels, &cat_domain, fonts, cat_font_size, pinned.is_none())?;
                    vega_fit::label_block_height(widths.into_iter().fold(0.0_f64, f64::max), cat_font_size, cat_angle_raw)
                }
            };
            support_table_strip::tilted_axis_gap_growth(style, &ax_x.labels, cat_font_size, block)
        }
        _ => 0.0,
    };
    // The gap between the plot bottom and a bottom strip's first row.
    let strip_axis_offset = match r.effective_support_table_style.as_ref() {
        Some(style) if style.position == "bottom" => {
            r.support_table_axis_offset.ok_or("bar: support_table position=bottom requires support_table_axis_offset")?
                + strip_axis_gap_growth
        }
        _ => 0.0,
    };
    let (mut cat_align, mut cat_baseline) = if cat_angle == 0.0 {
        ("center", x_edge.baseline())
    } else {
        vega_fit::vl_x_label_anchor(cat_angle, x_edge == crate::common::XEdge::Bottom)
    };
    // The labels the rotated column draws: a tilted temporal axis draws its
    // visible inline-folded labels, a category axis every label.
    let rotated_texts: Vec<(String, String)> = match (&temporal_labels, cat_angle != 0.0) {
        (Some(tls), true) => tls.iter().map(|tl| (tl.row1.clone(), tl.row2.clone())).collect(),
        _ => cat_labels.iter().map(|c| (c.clone(), String::new())).collect(),
    };
    // Restored x ticks below the plot: their length pushes the label band
    // down and joins the bottom chrome on every arm (the tick is emitted
    // unconditionally when restored, so every arm must reserve it).
    let x_tick_len = band_tick_len(ax_x, x_ticks_restored);

    // See render_horizontal's matching comment: stacked bars never get the
    // grouped-bar outer band padding.
    // A color 1:1 with the category is not grouped either: production's
    // `_is_color_1to1_with_x` suppresses the offset channel, so the band
    // stays plain and each category's one bar takes its own color.
    let one_to_one = is_grouped
        && !is_stacked
        && color_is_1to1(color_field.expect("is_grouped implies color"));
    let (band_pi, band_po) = if is_grouped && !is_stacked && !one_to_one {
        (r.style.grouped.padding_inner, r.style.grouped.padding_outer)
    } else {
        (0.0, 0.0)
    };
    let make_band = |pw: f64| match (&time, &time_axis) {
        (Some(t), Some(axis)) if t.bars_on_band() => BandScale::anchored(cat_domain.clone(), t.band_starts(axis, pw), t.band_starts(axis, pw)),
        (Some(t), Some(axis)) => BandScale::anchored(cat_domain.clone(), t.starts(axis, pw), t.anchors(axis, pw)),
        _ => BandScale::new(cat_domain.clone(), 0.0, pw, band_pi, band_po),
    };

    // The x axis's own label and title ink in plot coordinates, set by the
    // fit's last layout pass: a cell that draws its own axis folds it into its ink.
    let x_axis_ink = Cell::new(vega_fit::Bounds::empty_new());
    // ── Plot-rect negotiation ──────────────────────────────────────────────
    // Same split as render_horizontal: the vega_fit path runs Vega's own
    // autosize-fit algebra; the concat-compiled endpoint-label shape lays its
    // panes out the way the concat does.
    // support_table charts run `autosize: pad` in production: the modeled
    // sub-shape takes the composed arm below (plot = payload view, canvas
    // grown around the ink). `shape` names the arm once, below, after the
    // gates' own law comments.

    // Composed support-table arm (production's autosize-pad shape): the plot
    // IS the payload view verbatim, the strip floats above it at the ported
    // row offsets, and the canvas grows around the ink — title, strip, axis
    // gutters, stub labels — plus padding. Modeled on either axis side and
    // with or without a color encoding (a kept legend, an active endpoint
    // rail, wide measures/layers, or axis titles are still unmodeled and
    // keep the legacy splice below): `!r.style.endpoint_labels.
    // visible` already excludes the one shape this arm doesn't model with a
    // color field (`show_rail_v`'s own `el.visible` conjunct), so admitting
    // `color_field.is_some()` here only reaches a rail-disabled multi-series
    // bar — `weekly_bar_grouped`'s own per_series shape, right-axis with the
    // legend suppressed by `has_per_series` (probed: `gs._prod_render` on the
    // real corpus board, plot_top/plot_left/canvas match this arm's existing
    // formula verbatim once color_field is let through) — never a shape with
    // an active rail alongside it.
    let composed_cfg: Option<&support_table_strip::SupportTableConfig> = composed_strip
        .filter(|_| r.effective_support_table_style.is_some() && !show_rail_v);
    // The rail-concat's own modeled sub-shape (S7): a strip with a resolved
    // style is the only condition this filter checks, the rest (left axis, no
    // legend) live in `rail_concat`'s own gate below, alongside the
    // shape's other existing conjuncts.
    let rail_strip_cfg: Option<&support_table_strip::SupportTableConfig> =
        composed_strip.filter(|_| r.effective_support_table_style.is_some());

    let ax_off_x = ax_x.ticks.offset.unwrap_or(0.0);
    let ax_off_y = ax_y.ticks.offset.unwrap_or(0.0);
    let val_limit = ax_y.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    // Own-side (right) value labels are flip-aligned: production bakes the
    // measured gutter into VL `labelPadding` — measured over the DRAWN
    // ladder (`axis.drawn_tick_values`, the baked rungs inside the domain;
    // a rung past the domain max is never drawn and never sizes the
    // gutter) with the Inter stand-in get_font_path uses for every
    // non-serif family (the labels PAINT in dbt Sans Tabular, but the
    // spec's gutter was sized with proportional Inter advances — bc1's
    // columns: labelPadding 32.837 = width("100K" in Inter) + 4). A left
    // value axis keeps the themed padding with labels growing away from
    // the plot, sized by Vega's own runtime ink (real font files, drawn
    // labels only). The ladder is resolve's own (`common::drawn_tick_values`,
    // the baked rungs inside the baked domain), not the one this arm draws:
    // a layered bar's merged scale re-admits a niced rung Vega then draws,
    // and a stacked arm's `stacked_domain_max` drops one, but production
    // sized the gutter before either happened.
    let drawn_gutter_labels: Vec<String> =
        format_tick_labels(&crate::common::drawn_tick_values(ax_y), ax_y, normalize, "y")?;
    let gutter_of = |max_w: f64| -> Result<f64, String> {
        if val_on_right && !val_away {
            let gutter_labels = &drawn_gutter_labels;
            let measure_family = measure::stand_in_family(val_family);
            let gutter_max = gutter_labels.iter().try_fold(0.0_f64, |acc, s| {
                fonts.measure(s, measure_family, val_font_size).map(|mw| acc.max(mw))
            })?;
            Ok(gutter_max.min(val_limit) + VALUE_AXIS_BREATHING_ROOM)
        } else {
            Ok(base_label_pad + max_w.min(val_limit))
        }
    };
    // Independent-scale facet: `ax_y.tick_values` is empty ONLY when the
    // axis is genuinely UNBAKED — Python's `fold_panels` (`_chart_rows.py`)
    // returns `None` ticks for `multiples.scale: "independent"`; a plain or
    // shared-scale bar always bakes `tick_values` at compile time
    // (`_resolve_cartesian_ticks`), so this gate reaches the independent-
    // scale facet's own unbaked case, AND a single-chart unbaked right-axis
    // bar whenever `ax_y.ticks.count` is `None` (matches production: both
    // are the same "no baked ladder" condition, not an independent-scale-
    // only path). Production's own `measure_axis_to_vl` (`vl_field_maps.
    // py:298-401`) runs ONCE per chart, over the WHOLE dataset (`data`) —
    // never re-run per panel, even though a facet's own per-panel SCALE
    // may be independent — so its `estimated_quantitative_tick_labels`
    // cover-ladder gutter (`_measured_label_padding.py:219-267`) is the
    // SAME reserve every panel shares, sized off EVERY row's own value
    // across every panel, not this panel's own smaller/larger subset — the
    // real law behind `fixtures/multiples/05-independent-scale`'s own
    // identical tick-label anchor x on both rows despite wildly different
    // domains (East maxes at 2,400; West at 210,000). Candidate generation
    // and measuring both live in `common::estimated_tick_label_gutter`,
    // shared with `line.rs`'s own ladder-less axis.
    let estimated_val_gutter = || -> Result<f64, String> {
        let raw_max =
            payload.rows.iter().filter_map(|row| row_num(row, val_field)).fold(f64::NEG_INFINITY, f64::max);
        let raw_min =
            payload.rows.iter().filter_map(|row| row_num(row, val_field)).fold(f64::INFINITY, f64::min);
        crate::common::estimated_tick_label_gutter(
            fonts,
            val_family,
            val_font_size,
            val_limit,
            ax_y.tick_label.as_ref(),
            ax_y.labels.format.as_ref(),
            ax_y,
            raw_min,
            raw_max,
        )
    };
    let val_gutter = if date_measure && val_away {
        // Left-aligned date labels grow away from the axis: padding plus the widest label.
        base_label_pad + max_val_w
    } else if val_on_right && ax_y.tick_values.is_empty() {
        estimated_val_gutter()?
    } else {
        gutter_of(max_val_w)?
    };
    // Dual axis: the layer's own INDEPENDENT y scale — probed law
    // (docs/laws-layers.md "Dual axis"). Zero union then
    // `d3_nice(_, _, 10)` (d3's own default nice count — NOT the axis's
    // displayed tickCount) from the layer's OWN values only, never unioned
    // with the base's; its ladder is `d3_ticks(0, niced_max, tickCount)`
    // with the BASE's own `axis_y.ticks.count` (`_overlay.py:1059` — the
    // layer inherits it when it authors none); its tick label font/format
    // and gutter formula are the SAME ones the base's own right-side axis
    // already uses (`axis_to_vl(axis_y)` builds the layer's fresh axis dict
    // from the base chart's style, not the layer's own — which the
    // resolved payload only carries authored overrides on). None of this
    // depends on the negotiated plot size, so it is computed once, not
    // re-derived inside the fit closure the way the base's own unbaked
    // ladder is.
    let (scaled, layer_zero_rules) =
        y_sides.as_ref().map_or_else(Default::default, |sides| sides.scaled(layers, &layer_rows, cat_field, ax_y));
    // A bar layer's span per row: rows sharing a category stack in row order
    // (the layer's `y` keeps Vega-Lite's default `stack: zero`, with no series to
    // order by), a ranged layer spans its two columns instead.
    let layer_bar_spans: Vec<Vec<(String, f64, f64)>> = scaled
        .iter()
        .map(|p| {
            if p.layer_type != LayerType::Bar {
                return Vec::new();
            }
            let in_domain = p.rows.iter().filter(|row| cat_domain.contains(&row_str(row, p.x)));
            match p.y_start {
                Some(start) => in_domain
                    .filter_map(|row| Some((row_str(row, p.x), row_num(row, start)?, row_num(row, p.y)?)))
                    .collect(),
                None => row_order_stack(in_domain, p.x, p.y, &crate::families::layers::never_nan),
            }
        })
        .collect();
    let layer_axes: Vec<LayerAxis> = scaled
        .iter()
        .zip(&layer_bar_spans)
        .enumerate()
        .map(|(i, (p, spans))| {
            let extent = if p.layer_type == LayerType::Bar {
                spans.iter().fold((0.0_f64, 0.0_f64), |(lo, hi), (_, a, b)| (lo.min(*a).min(*b), hi.max(*a).max(*b)))
            } else {
                p.value_extent(p.rows.iter().filter(|row| cat_domain.contains(&row_str(row, p.x))))
            };
            let extent = crate::annotation::widen_y(extent, &r.annotations, crate::annotation::YAxis::Layer(i), None, false);
            p.axis(
                extent,
                &LayerAxisFrame {
                    family: "bar",
                    ax_y,
                    fonts,
                    font_family: val_family,
                    font_size: val_font_size,
                    label_limit: val_limit,
                    tick_size: val_tick_size,
                    title_style: layer_title_style,
                    field_labels: &r.field_labels,
                },
            )
        })
        .collect::<Result<_, _>>()?;

    // Composed support_table arm (any x type — probed on both a temporal
    // `weekly_bar` and a nominal `product_bar`): the reference renderer's
    // OWN visible bar mark on this shape is a plain square-cornered path
    // (`M0,30.2h47.2v377.8h-47.2Z`) despite the spec's own
    // `mark.cornerRadiusEnd: 2.0`. The rounded-corner sibling
    // (`class="foreground"`) IS a second painted path — it carries its own
    // `stroke`/`stroke-width` — not inert clip geometry; it is drawn in the
    // card background color OVER the square fill's corners, so the visible
    // result reads rounded either way and the pixel diff is neutral
    // (~1e-5 diff_frac, ablated). Square corners here match the fill
    // geometry's own path data, not a Vega layout law — see
    // docs/laws-support-table.md's corner-radius section.
    let corner_r = if composed_cfg.is_some() { 0.0 } else { r.style.mark.border.radius.unwrap_or(0.0) };
    let stroke_color = r.style.mark.border.color.as_deref().unwrap_or("none");
    let stroke_w = r.style.mark.border.width.unwrap_or(0.0);
    let drawn_stroke_w = if stroke_color == "none" { 0.0 } else { stroke_w };
    let data_map = build_data_map(rows, cat_field, val_field, color_field, r.y_start.is_none())?;
    let extras = Extras::build(rows, cat_field, color_field, r.y_start.is_none(), &r.tooltip)?;
    let link = r.datum_link(
        cat_field,
        match (&time, x_standard_dates) {
            (Some(_), _) | (None, true) => crate::href::LinkText::Date,
            (None, false) => crate::href::LinkText::Raw,
        },
    );
    // Under a layer, a date x that is not authored `temporal` leaves an unstacked base's own marks unlinked.
    let base_link = link.as_ref().filter(|_| {
        layers.is_empty() || is_stacked || ax_x.resolved_authored_type() == Some("temporal") || !(x_standard_dates || detected_data_tu.is_some() || time.is_some())
    });
    let hrefs = link_hrefs(base_link, rows, (cat_field, color_field), time.is_some())?;
    let strip_links = crate::href::first_datum_links(base_link, rows, cat_field)?;
    let anchor_of = |key: &(String, String)| r.selection.mark(r.link_open.anchor(hrefs.get(key).map(String::as_str)));
    let raw_x = time.as_ref().map(|_| RawX::of(rows, cat_field, val_field, color_field, r.y_start.is_none()));
    // A series row earns its place only where the color field tells rows apart (`series_row_promoted`).
    let series_promoted = time.is_none() || color_domain.len() > 1;
    // A facet panel's marks carry Vega-Lite's own description instead of the structured tooltip.
    let tip = |cat: &str, series: &str| -> String {
        let date = raw_x.as_ref().map_or(cat, |x| x.date(cat, series)).to_string();
        match (&time, facet.is_some()) {
            (Some(t), true) => crate::families::temporal::default_time_unit_text(&date, t.grain).expect("a plotted date parses"),
            _ => header_date(date),
        }
    };
    // Rows of one `(bucket, series)` on a time axis stack over each other, in row order.
    let tier_depth = tier_depths(&data_map);
    let stack_paint_order = with_tiers(&stack_order, &tier_depth);
    // A strip's layers inherit the grouped bar's `xOffset` channel, but the
    // stubs' rows carry no series: that missing value joins the offset scale's
    // domain as one more sub-band ahead of the series, which narrows every
    // column to `1/(n + 1)` of the band.
    let strip_offset_member = composed_cfg.is_some();
    let column_split = (is_grouped && !is_stacked && !one_to_one)
        .then(|| {
            let overlap = grouped_overlap_fraction(r.style.overlap.as_ref(), color_domain.len())?;
            Ok::<_, String>(ColumnSplit {
                n_series: color_domain.len(),
                lead: usize::from(strip_offset_member),
                width_steps: (1.0 + overlap).max(0.05),
                layered: !layers.is_empty(),
            })
        })
        .transpose()?;
    // The first and last drawn bar as `(category index, series slot)`: Vega
    // bounds one rect per data row, so only a category with a row owns a bar.
    // A band that does not split its columns has the one slot, 0.
    let bar_series: Vec<&str> =
        if is_grouped { color_domain.iter().map(String::as_str).collect() } else { vec![""] };
    let drawn_bars: Vec<(usize, usize)> = cat_domain
        .iter()
        .enumerate()
        .flat_map(|(ci, cat)| {
            let (data_map, split) = (&data_map, column_split.is_some());
            bar_series.iter().enumerate().filter_map(move |(si, series)| {
                data_map
                    .contains_key(&(cat.clone(), (*series).to_string()))
                    .then_some((ci, if split { si } else { 0 }))
            })
        })
        .collect();
    let drawn_ends = drawn_bars.first().copied().zip(drawn_bars.last().copied());
    let center_stack = (r.stack == "center").then(|| match facet.and_then(|f| f.center_stack) {
        Some(whole) => whole,
        None => {
            let all_rows = facet.map_or(rows, |f| f.chart_rows);
            CenterStack::over([rows], cat_field, val_field, CenterStack::splits(r.style.mark.border.radius, all_rows, val_field))
        }
    });
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

    // Zero-rule presence/width — shared by the fit branch's ink closure and
    // the composed branch's own left/right ink (a rule's bounds expand by
    // its FULL stroke width — the established boundStroke model).
    // `independent_scale`: production's `BaselineFeature.apply` returns
    // early for a chart under an independent multiples scale
    // (`render/chart/features/baseline.py:318-321`, `multiples_scale_
    // independent(chart)`) — the zero/unity verdict is computed ONCE off
    // the pooled union of every panel's own rows, which would be wrong for
    // a panel with its own domain, so the feature skips the whole chart
    // rather than paint a rule from the wrong pool. No zero or threshold
    // rule layer exists on an independent-scale panel at all.
    // The unity verdict reads the pooled column (`_apply_unity`), never one panel's slice.
    // A right-pinned layer likewise skips the feature (`resolve.scale.y ==
    // "independent"`): `_overlay.py` re-adds the zero rule alone.
    let rules = if independent_scale {
        None
    } else {
        baseline_rules(ax_y, r.y_start.is_some(), unity_rule(&r, chart_rows) && !independent_y, !chart_rows.is_empty())
    };

    // ── Endpoint-label rail (production's `right_pane` hconcat for a
    //    vertical stacked bar with a series color — the same concat the
    //    line/area rail port models; `endpoint_rail_layout`,
    //    emitters/_endpoint_rail.py). Production resolves the value axis
    //    LEFT when the rail claims the right side.
    // Resolve raises this upstream; the rail arm reserves no gutter for a pinned layer's axis.
    if show_rail_v && independent_y {
        return Err(
            "bar: ERR-LAYER-AXIS-POSITION-ENDPOINT-LABELS: a layer pinned to an axis side beside an endpoint-label rail"
                .into(),
        );
    }
    let shape = shape(show_rail_v);
    if r.support_table.is_some() && r.effective_support_table_style.is_none() {
        return Err("bar: support_table present but effective_support_table_style absent".into());
    }
    let x_scale = XScale::of(&law);
    if column_split.is_some() && matches!(x_scale, XScale::ContinuousTemporal) {
        crate::diagnostic::warn(Diagnostic::new(
            &codes::WARN_BAR_GROUPED_SERIES_COINCIDE,
            [("chart_id", json!(payload.chart_id()?)), ("field", json!(cat_field))],
        ));
    }
    let series = column_split.as_ref().filter(|_| r.layers.is_empty()).map_or(1, |c| c.n_series);
    if let Some(f) = facet {
        f.series.set(series);
    } else if !show_rail_v {
        warn_vertical_density(payload.chart_id()?, r, rows, decision_w, series, x_scale)?;
    }
    if val_away && shape != Shape::Fit {
        return Err("bar: a default-aligned right value axis is modeled on the fit arm only".into());
    }

    // Production's `apply_title_overflow_to_spec` wraps a title or subtitle
    // wider than its own basis onto two measured lines, and Vega's title
    // group grows by `fontSize + 2` per extra line. Both Vega arms reserve
    // and draw that block; the composed and legacy arms keep `emit_title`'s
    // single unwrapped row.
    //
    // The basis is the padded card on the fit arm, but NOT on the rail
    // concat: production re-runs the whole pass there from the raw strings
    // after `_correct_concat_overshoot`, against pane 0's own footprint
    // (`converters/chart.py`: `target_width − pane1_width − spacing − pad_l
    // − pad_r`) — the title spans the pane's gutter + plot, not the card.
    // Family-agnostic in production: the re-run keys on `"hconcat" in spec`,
    // which bar's `right_pane` rail compiles to exactly as line's does.
    let title_wrap_w =
        if shape == Shape::RailConcat { avail_w - rail_span } else { avail_w };
    let title = FrameTitle::new(
        r.title.as_deref(),
        r.subtitle.as_deref(),
        r.style.title_font.as_ref(),
        r.title_style.as_ref(),
        title_wrap_w,
        fonts,
    )?;
    title.warn_truncated(&r.id);
    let title_fs = title.font_size;
    let mut rail_anchors: Vec<(String, f64)> = Vec::new();
    // The cascade's packing bounds — production's raw stacked extent, not
    // the rendered domain (see `tallest_total`). Set by the rail arm.
    let mut rail_cascade_max: f64 = 0.0;
    let mut rail_cascade_min: f64 = 0.0;

    // Vega's parity overlap on the value labels (`labelSeparation` is the
    // theme's min_gap; production forwards none when unset, Vega's 0).
    let val_separation = ax_y.labels.min_gap.unwrap_or(0.0);
    // Every stack segment's and grouped bar's span: what each bar is drawn
    // from and each value label anchors to.
    let category_totals_all =
        if is_stacked { category_totals(rows, cat_field, val_field) } else { HashMap::new() };
    // A ranged bar's start column, per (category, series), and the tooltip law
    // that reads it (a span's shape).
    let ranged = r
        .y_start
        .as_deref()
        .map(|start_field| {
            let starts = build_data_map(rows, cat_field, start_field, color_field, false)?;
            RangedBars::new(start_field, &r.field_labels, starts, &data_map, date_axis.is_some())
        })
        .transpose()?;
    let painted_grouped = (is_grouped && !is_stacked).then(|| {
        let (mut painted, layers) = painted_series_bars(
            &color_domain,
            &cat_domain,
            &data_map,
            ranged.as_ref(),
            rows,
            (cat_field, color_field.expect("a grouped bar has a color field")),
            r.style.mark.border.radius,
            r.wide_measures.is_empty(),
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
    // Value labels in the fit: `autosize: fit` unions every text mark's
    // bounds, a blanked one (`fit_hides_label`, the genuine-zero label's other
    // rows) as a zero-width box one line tall, so a label at the floor or past
    // the top of its column moves the margins the way an axis label does
    // (probed on `labels/05`: `above` labels at dy −40 lift the plot top 27px
    // whether drawn or blank).
    let authored_labels = r.style.mark.labels.as_ref().filter(|ml| ml.visible && r.wide_measures.is_empty());
    let plain_rows: Vec<&serde_json::Map<String, Value>> = rows.iter().collect();
    let label_stack = (r.stack == "center").then(|| match facet {
        Some(f) => f.center_label_stack,
        None => CenterStack::over([rows], cat_field, val_field, false),
    });
    let label_spans =
        stack_label_spans(r, label_stack.as_ref(), &cat_domain, &stack_order, &data_map, &category_totals_all);
    let label_layers = value_label_layers(&LabelBuild {
        r,
        rows,
        plain_rows: &plain_rows,
        cat_field,
        cat_domain: &cat_domain,
        val_field,
        color_field,
        is_grouped,
        is_stacked,
        horizontal: false,
        spans: &spans,
        label_spans: label_spans.as_ref(),
        stack_order: &stack_order,
        totals: &category_totals_all,
        zero_text: zero_text.as_ref(),
        span: ranged.as_ref().filter(|_| authored_labels.is_some()),
        fonts,
        board_style: payload.board_style.as_ref(),
        link: link.as_ref(),
    })?;
    // Vega-Lite refuses an `xOffset` over a continuous x: production's own grouped bar under a strip crashes there.
    if time.as_ref().is_some_and(|t| !t.is_discrete()) && is_grouped && !is_stacked && r.support_table.is_some() {
        return Err(crate::refuse!("bar: grouped bars on a time x axis under a support table are not supported").into());
    }
    if time.as_ref().is_some_and(TimeX::bars_on_band) && r.support_table.is_some() {
        return Err(crate::refuse!("bar: a support table under grouped value labels on a time x axis is not supported").into());
    }
    if time.as_ref().is_some_and(TimeX::bars_on_band) && !bar_layers.is_empty() {
        return Err(crate::refuse!("bar: a bar layer over grouped value labels on a time x axis is not supported").into());
    }
    // A step layer's xOffset is a point scale over its band edges and the base's
    // series: it replaces a grouped base's offset band scale, so the base's bars
    // keep their white stroke but lose their width, and sit on the point scale's
    // first slots with the layer's edges on its last two.
    let step_over_grouped = layer_band_step && is_grouped && !is_stacked;
    let columns_for = |band: &BandScale, pw: f64| match (&time, &time_axis) {
        (Some(t), Some(axis)) if !t.bars_on_band() => Columns::Timed(t.columns(axis, pw, column_split.as_ref())),
        (Some(t), Some(axis)) => Columns::Timed(t.band_columns(
            axis,
            pw,
            column_split.as_ref().expect("a band on a time axis splits its columns"),
        )),
        _ if step_over_grouped => Columns::Banded(BandColumns::point_slots(band.bandwidth(), color_domain.len())),
        _ => Columns::Banded(BandColumns::new(band.bandwidth(), mark_band_width, r.style.mark.max_size, column_split.as_ref())),
    };
    let step_layers: Vec<(usize, &OverlayLineLayer, &[serde_json::Map<String, Value>], f64, f64)> =
        if layer_band_step && shape == Shape::Fit {
            line_layers
                .iter()
                .zip(&line_layer_rows)
                .zip(&line_layer_at)
                .zip(&line_band_step)
                .filter(|(_, &step)| step)
                .filter_map(|(((l, rows), &at), _)| {
                    l.band_step_ink().transpose().map(|ink| ink.map(|(plateau, reach)| (at, *l, *rows, plateau, reach)))
                })
                .collect::<Result<_, _>>()?
        } else {
            Vec::new()
        };
    // The fit test the draw loop blanks with (`fit_hides_label`): the span and
    // font size, at each pass's own plot height.
    let label_hide_cfg: Option<(f64, f64)> = match label_layers.first() {
        Some(base) if (base.stacked || base.ranged) && !base.zero_only => {
            label_fit_span(ax_y, &r.stack, stacked_domain_max)
                .filter(|_| is_grouped || r.layers.is_empty())
                .map(|span| {
                    r.style
                        .label_font_size
                        .map(|fs| (span, fs))
                        .ok_or("bar: style.label_font_size absent (resolved payload required)")
                })
                .transpose()?
        }
        _ => None,
    };
    // A layer's value labels join the fit like any text mark:
    // `(value, shaped width, category)` per layer row, with the layer's own
    // label style, on the shared scale or the layer's own right axis (`dual`).
    // A layer beside a support_table or a facet refuses, so only the fit arm and
    // the rail concat paint them.
    let layer_label_fit: Vec<LayerLabelFit> = if matches!(shape, Shape::Fit | Shape::RailConcat) && facet.is_none() {
        let lines = line_layers
            .iter()
            .zip(&line_layer_rows)
            .zip(&line_layer_at)
            .zip(&line_band_step)
            .filter_map(|(((l, rows), &at), &band_step)| {
                l.visible_labels().map(|ml| (ml, l.x.as_deref().unwrap_or(cat_field), l.y.as_str(), l.label_is_house, at, *rows, band_step))
            });
        let scatters = scatter_layers.iter().zip(&scatter_layer_rows).zip(&scatter_layer_at).filter_map(|((sl, rows), &at)| {
            let ml = &sl.point_mark.labels;
            ml.visible.then(|| (ml, sl.x.as_deref().unwrap_or(cat_field), sl.y.as_str(), sl.label_is_house, at, *rows, false))
        });
        lines
            .chain(scatters)
            .map(|(ml, layer_x, y, is_house, layer, rows, band_step)| {
                let style = crate::families::layers::LayerLabelStyle::new(ml, is_house, rows, payload.board_style.as_ref())?;
                let items = crate::common::valued_rows(rows.iter(), y)
                    .into_iter()
                    .map(|(row, val)| {
                        let cat = row_str(row, layer_x);
                        let band = band_step.then(|| (cat_domain.first() == Some(&cat), cat_domain.last() == Some(&cat)));
                        let caption = style.caption(row, val, band).map_err(|e| format!("bar: {e}"))?;
                        Ok(LayerLabelFitItem { val, width: style.width(fonts, &caption.text)?, cat, spot: caption.spot })
                    })
                    .collect::<Result<Vec<_>, String>>()?;
                Ok(LayerLabelFit { style, items, layer })
            })
            .collect::<Result<Vec<_>, String>>()?
    } else {
        Vec::new()
    };
    let cat_limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
    // (domain index, max row width, rows) per drawn bottom label. Shaped:
    // Vega bounds the drawn label (laws-shared.md, shaped vs raw).
    let x_clear = Cell::new(0.0_f64);
    let cat_label_ink: Vec<(f64, f64, usize)> = match (&band_intervals, &temporal_labels) {
        (Some(iv), _) => iv
            .labels
            .iter()
            .zip(&iv.label_spans)
            .map(|(tl, &(from, to))| {
                let w1 = fonts.measure_shaped(&tl.row1, cat_family, cat_font_size)?;
                let w2 = if tl.row2.is_empty() { 0.0 } else { fonts.measure_shaped(&tl.row2, cat_family, cat_font_size)? };
                Ok(((from + to) / 2.0, w1.max(w2).min(cat_limit), tl.rows()))
            })
            .collect::<Result<_, String>>()?,
        (None, Some(tls)) => tls
            .iter()
            .map(|tl| {
                let w1 = fonts.measure_shaped(&tl.row1, cat_family, cat_font_size)?;
                let w2 = if tl.row2.is_empty() { 0.0 } else { fonts.measure_shaped(&tl.row2, cat_family, cat_font_size)? };
                Ok((tl.domain_idx as f64 + 0.5, w1.max(w2).min(cat_limit), tl.rows()))
            })
            .collect::<Result<_, String>>()?,
        // `cat_labels` (the `labels.expr`-rewritten parallel to raw
        // `cat_domain`, same order, see the `cat_labels` binding above)
        // is what both draw loops below actually emit; measuring the raw
        // domain here would reserve overhang for a label under an
        // authored `labels.expr` never draws.
        (None, None) => cat_labels
            .iter()
            .enumerate()
            .map(|(i, c)| {
                fonts.measure_shaped(c, cat_family, cat_font_size).map(|lw| (i as f64 + 0.5, lw.min(cat_limit), crate::text::line_count(c)))
            })
            .collect::<Result<_, String>>()?,
    };
    // Production's autosize-pad support_table shape: the cells and stubs are
    // ink like any other mark, measured once at the payload's own width, a
    // pad layout never renegotiates the plot, so their x positions are final.
    let composed: Option<(Vec<support_table_strip::ComposedRow>, f64, &support_table_strip::SupportTableStyle)> =
        match composed_cfg {
            None => None,
            Some(cfg) => {
            let strip_style = r
                .effective_support_table_style
                .as_ref()
                .expect("composed_cfg gate checked effective_support_table_style");
            let n_rows = support_table_strip::visual_row_count(cfg, per_series_order.len())?;
            let strip_h = support_table_strip::strip_height(strip_style, n_rows);
            let band = BandScale::new(cat_domain.clone(), 0.0, w, band_pi, band_po);
            let centers = strip_centers(&band, &cat_domain, time.as_ref().zip(time_axis.as_ref()), w);
            // Single-series band x (the gate) is production's `sort: null`
            // data-order anchor; a temporal-detected domain is its ascending
            // anchor; a sorted category x anchors on the first carrying cell of
            // its pinned domain (`_pinned_domain_anchor`), which is the band
            // order painted here, either way the strip anchors on the first
            // carrying cell. Only an unpinnable sort (a `::date` category,
            // `_is_json_scalar_domain`) stays `nowhere` in production; this arm
            // does not model it. The per-entry provenance refusal, raw d3
            // literals never anchor, lives in `build_rows_composed`.
            let mut strip_rows = support_table_strip::build_rows_composed(
                        &r.id,
                cfg,
                rows,
                cat_field,
                &cat_domain,
                &centers,
                fonts,
                strip_style,
                true,
                // The cells sit on a category BAND, so production computes the
                // entry dx that centers the number lane on it; on a time scale
                // only a bucket is a band, an instant is a point.
                time.as_ref().is_none_or(TimeX::spans_buckets),
                val_on_right,
                // A per_series entry needs `color_field` (`build_rows_composed`'s
                // own "requires a color encoding" refusal otherwise), a
                // single-series chart with no color encoding just carries an
                // empty `per_series_order` below.
                color_field,
                &per_series_order,
                &per_series_ink,
                r.strip_linked().then_some(&strip_links), detected_data_tu.is_some() || time.is_some())?;
            let strip_aria = strip_cell_aria(r, &cat_title_text, &cat_domain, time.as_ref(), color_field)?;
            strip_rows.iter_mut().for_each(|row| row.describe(&strip_aria));
            // The offset scale places every strip layer: a series-less row (a
            // source entry's cells, every stub) in the sub-band the missing value
            // opens, a `per_series` row's cells in their own series' sub-band.
            if let Some(split) = column_split.as_ref().filter(|split| split.lead > 0) {
                let offsets = support_table_strip::sub_band_center_offsets(band.bandwidth(), split.n_series + split.lead, false);
                let step = support_table_strip::sub_band_step(band.bandwidth(), split.n_series + split.lead, false);
                for row in strip_rows.iter_mut() {
                    // A wide chart's copies each sit in their own measure's sub-band.
                    if row.series.is_none() && row.copy_dx.len() > 1 {
                        for (k, copy_dx) in row.copy_dx.iter_mut().enumerate() {
                            *copy_dx = offsets[split.lead + k];
                        }
                        row.stub_dx = step / 2.0;
                        continue;
                    }
                    let slot = match &row.series {
                        None => 0,
                        Some(series) => {
                            split.lead
                                + color_domain
                                    .iter()
                                    .position(|c| c == series)
                                    .expect("a per_series row prints a series of the color domain")
                        }
                    };
                    row.cells.iter_mut().for_each(|cell| cell.x += offsets[slot]);
                    row.stub_dx = step / 2.0;
                }
            }
            // Never thinned: a number sits under every bucket; density only warns.
            support_table_strip::warn_strip_overflow(&r.id, &strip_rows, w, strip_style.row.padding.horizontal);
        Some((strip_rows, strip_h, strip_style))
            }
        };
    if time.is_some() && time_axis.is_some() && !r.annotations.is_empty() {
        return Err(annotations_unsupported(r, "time-axis bar"));
    }
    refuse_numeric_x_on_bands(r)?;
    let annotation_days: Option<Vec<f64>> = x_axis.days(&cat_domain);
    // The chart's own ink past the plot, set by the fit's ink closure on every
    // pass; the last write is the drawn size's.
    let annotation_reach = Cell::new(crate::annotation::Reach::default());
    // The rail arm re-nices before the annotation maps capture the scale.
    // normalize keeps its fixed [0, 1] domain and quartile ladder. For
    // everything else the domain nices and `niced_rail_domain` re-derives
    // the ladder — re-filtering the baked one when the axis has one, or
    // generating a fresh d3-ticks ladder at the niced max when it does
    // not (`ax_y.tick_values.is_empty()`, `rail_concat` admits both) —
    // then re-formats, and the gutter re-measures from the labels Vega
    // actually draws (a dropped rung can carry the widest string, or —
    // with an anchored "$" prefix — move the symbol to a kept rung).
    let (linear_domain_max, tick_values, val_tick_labels, max_val_w) = if shape == Shape::RailConcat && !normalize && !(r.stack == "center" && ax_y.authored_continuous_domain().is_some()) {
        let (domain_max, ticks) = niced_rail_domain(ax_y, linear_domain_max);
        let labels = format_tick_labels(&ticks, ax_y, normalize, "y")?;
        let max_w = labels.iter().try_fold(0.0_f64, |acc, l| {
            fonts.measure_shaped(l, val_family, val_font_size).map(|lw| acc.max(lw))
        })?;
        (domain_max, ticks, labels, max_w)
    } else {
        (linear_domain_max, tick_values, val_tick_labels, max_val_w)
    };
    // The annotation maps at a plot origin and size: the scales the bars draw with.
    let annotation_ctx = |origin: (f64, f64), pw: f64, ph: f64| {
        use crate::annotation::{AnnotationCtx, AxisMap, Orientation, PlotRect};
        let y = AxisMap::Linear { domain: (v_domain_min, linear_domain_max), range: (ph, 0.0) };
        let layer_y = crate::families::layers::layer_y_maps(&y, &layer_axes, layers.len(), ph);
        AnnotationCtx {
            annotations: &r.annotations,
            chart_id: &r.id,
            panel: facet.map(|f| f.panel_key),
            plot: PlotRect { x: origin.0, y: origin.1, w: pw, h: ph },
            reach: annotation_reach.get(),
            x: AxisMap::Band {
                domain: cat_domain.clone(),
                geometry: crate::families::cartesian::BandGeometry::new(cat_domain.len(), 0.0, pw, band_pi, band_po),
                days: annotation_days.clone(),
                range: (0.0, pw),
            },
            y,
            layer_y,
            orientation: Orientation::Vertical,
            fonts,
            canvas: r.canvas.as_str(),
            clip_id: format!("{}-ann{}", crate::contract::safe_svg_id(&r.id), facet.map_or(0, |f| f.panel_ordinal)),
            occupied: Vec::new(),
        }
    };
    let plot = if shape == Shape::RailConcat {
        // Single-pass port of production's probe-shrink concat layout,
        // mirroring line.rs's rail arm: pane0 gives up the chrome the probe
        // measures (padding + left-axis extent + unclipped right ink +
        // hconcat spacing + label pane); both panes give up the vertical
        // chrome (title/subtitle band + x-axis band); sums ceil'd the way
        // the probe's integer scenegraph dimensions are. Cadence re-derives
        // against the rail-reduced width (production feeds
        // `box.width − resolve_endpoint_rail_span(...)` into the crowding
        // pass).
        let spacing = el.label_offset;
        let cadence_w = w - rail_span;
        let folded_tilt = temporal_fold_tu.is_some() && temporal_tilt != 0.0;
        let (temporal_labels, x_ticks_restored) = match (detected_data_tu, label_unit) {
            (Some(_), Some(_)) if folded_tilt => (temporal_labels, x_ticks_restored),
            (Some(data_tu), Some(unit)) => {
                let resolved = bar_temporal_labels(
                    &cat_domain,
                    data_tu,
                    unit,
                    ax_x,
                    authored_time_format,
                    cadence_w * cadence_ratio(r)?,
                    cadence_w,
                    r.style.label_usable_ratio,
                    labels_expr.as_ref(),
                    fonts,
                    cat_font_size,
                )?;
                // Production crowds the rail's pane, not the card: the tilt and
                // parity the narrower width asks for are the ones drawn.
                if let (None, Some(angle), true) = (ax_x.labels.angle, resolved.angle, band_intervals.is_none()) {
                    cat_angle = vega_fit::normalize_angle(angle);
                    (cat_align, cat_baseline) = if cat_angle == 0.0 {
                        ("center", x_edge.baseline())
                    } else {
                        vega_fit::vl_x_label_anchor(cat_angle, x_edge == crate::common::XEdge::Bottom)
                    };
                }
                x_parity_separation = (resolved.parity && band_intervals.is_none()).then(|| ax_x.labels.min_gap.unwrap_or(0.0));
                (Some(resolved.labels), resolved.ticks_restored)
            }
            _ => (None, x_ticks_restored),
        };
        let x_tick_len = band_tick_len(ax_x, x_ticks_restored);
        let label_rows = match &temporal_labels {
            Some(ls) => ls.iter().map(|l| l.rows()).max().unwrap_or(1),
            None => cat_labels.iter().map(|c| crate::text::line_count(c)).max().unwrap_or(1),
        };
        let cat_label_row_h = if cat_angle != 0.0 {
            let rows: Vec<(String, String)> = match &temporal_labels {
                Some(tls) => tls.iter().map(|tl| (tl.row1.clone(), tl.row2.clone())).collect(),
                None => cat_labels.iter().map(|c| (c.clone(), String::new())).collect(),
            };
            rotated_rows_reach(&rows, fonts, cat_family, cat_font_size, ax_x.labels.max_width, (cat_align, cat_baseline, cat_angle), false, x_edge)?
        } else if x_edge == crate::common::XEdge::Top {
            // A second row hangs below the first, into the plot.
            cat_font_size
        } else {
            cat_font_size + (label_rows - 1) as f64 * (cat_font_size + 2.0)
        };

        // support_table (S7/S8): the strip's rows are pane-0 ink too — built
        // once here, at the pre-correction width `w` (mirrors line.rs's own
        // probe-then-final split; the band scale's positions DO move with
        // the pane width, unlike a point scale's, so the FINAL cells are
        // rebuilt at the corrected `plot_w` below for actual rendering,
        // while this probe only measures S8's overhang term into
        // `right_ink`).
        let rail_strip_rows: Option<(Vec<support_table_strip::ComposedRow>, f64, f64)> =
            match rail_strip_cfg {
                None => None,
                Some(cfg) => {
                    let strip_style = r.effective_support_table_style.as_ref().ok_or(
                        "bar: support_table present but effective_support_table_style absent",
                    )?;
                    let n_rows = support_table_strip::visual_row_count(cfg, per_series_order.len())?;
                    let strip_h = support_table_strip::strip_height(strip_style, n_rows);
                    let ink_ext = support_table_strip::composed_ink_extent(strip_style, n_rows);
                    let band = BandScale::new(cat_domain.clone(), 0.0, w, band_pi, band_po);
                    let centers = strip_centers(&band, &cat_domain, time.as_ref().zip(time_axis.as_ref()), w);
                    let rows = support_table_strip::build_rows_composed(
                    &r.id,
                        cfg, rows, cat_field, &cat_domain, &centers, fonts, strip_style,
                        true, time.as_ref().is_none_or(TimeX::spans_buckets), val_on_right, color_field, &per_series_order, &per_series_ink,
                        r.strip_linked().then_some(&strip_links), false)?;
                    Some((rows, strip_h, ink_ext))
                }
            };
        // Ports production's closed form (`support_table_attachment.py:4425`):
        // the strip floats the title via a closed-form font-size correction,
        // not `strip_h`
        // — the strip's own ink extent (`ink_ext` below) already grows the
        // view-bounds top margin under autosize:pad, same as the composed
        // (non-concat) arm's own
        // `plot_top` term.
        // A bottom strip leaves the title where an unstripped pad chart has it.
        let rail_bottom_strip = rail_strip_rows.is_some()
            && r.effective_support_table_style.as_ref().is_some_and(|st| st.position == "bottom");
        let strip_title_offset: Option<f64> = rail_strip_rows.as_ref().filter(|_| !rail_bottom_strip).map(|_| {
            let theme_offset =
                title_ref.and_then(|t| t.position.as_ref()).and_then(|p| p.offset).unwrap_or(0.0);
            theme_offset + support_table_strip::support_table_title_offset_correction(title_fs)
        });
        // Bars sit inside their bands (never past the plot's right edge), so
        // the unclipped right ink is the zero rule's full stroke alone —
        // maxed against the strip's own right overhang past the pane (S8).
        // UNPORTED GAP, not a law: this measures the strip's cells on the
        // rows built at the UNCORRECTED probe width `w`, but `rail_x_rel`
        // below (and the strip rebuild at line ~3341) both key off the
        // CORRECTED `plot_w`. A BAND scale's cell positions move with the
        // pane width (unlike line.rs's point scale, genuinely
        // width-invariant by construction), so if this term ever binds —
        // the strip beats the zero rule's stroke — the overhang measured
        // here is stale by the width correction's own delta. No swept case
        // binds (`region_bar_grouped`'s own currency cell stays under the
        // zero rule's stroke).
        let val_title_extra = crate::common::y_title_extra(val_title_style, &val_title_paint);
        // A right value axis puts its gutter in pane 0's right ink; a strip
        // then mirrors with it: its cells grow leftward, and the stub column
        // hangs past the axis at the spec width the concat never shrank
        // (`attach_support_table` reads `spec["width"]`, the unreduced card
        // width).
        let rail_right = val_on_right;
        let stub_pad = ax_y.labels.padding.unwrap_or(6.0);
        let right_ink = match &rail_strip_rows {
            _ if rail_right => {
                rules.map_or(0.0, |b| b.width).max(ax_off_y + val_tick_size + val_gutter + val_title_extra)
            }
            Some((rows, _, _)) => {
                rules.map_or(0.0, |b| b.width).max(support_table_strip::composed_right_overhang(rows, w))
            }
            None => rules.map_or(0.0, |b| b.width),
        };
        let stub_x2 = match &rail_strip_rows {
            Some((rows, _, _)) if rail_right => {
                w + stub_pad + rows.iter().fold(0.0_f64, |a, row| a.max(row.label_w))
            }
            _ => 0.0,
        };
        let cat_title_extra = crate::common::x_title_extra(&ax_x.title, &cat_title_paint);
        // Theme-visible ticks and the thinned-cadence restoration meet in
        // one band term — never summed, the tick is drawn once either way.
        let x_tick_eff = if ax_x.ticks.visible.unwrap_or(true) {
            ax_x.ticks.length.unwrap_or(6.0)
        } else {
            0.0
        }
        .max(x_tick_len);
        let x_reach = match &time_axis {
            Some(axis) => {
                let mut ink = vega_fit::Bounds::empty_new();
                axis.union_label_ink(cadence_w, 0.0, &mut ink, fonts)?;
                let reach = match x_edge {
                    crate::common::XEdge::Bottom => ink.y2,
                    crate::common::XEdge::Top => -ink.y1,
                };
                reach + cat_title_extra
            }
            None => ax_off_x + x_tick_eff + cat_pad + cat_label_row_h + cat_title_extra,
        };
        // The value axis's lowest tick label hangs below the plot where the x axis paints nothing under it.
        let value_label_hang = vega_fit::text_ink(0.0, 0.0, 0.0, val_font_size, "right", "middle").y2;
        let (x_band_above, x_band) = match x_edge {
            crate::common::XEdge::Bottom => (0.0, x_reach.max(value_label_hang)),
            crate::common::XEdge::Top => (x_reach, value_label_hang),
        };
        // The ink above pane 0's plot: the x axis band over a top axis.
        let above = |top_ink: f64| top_ink.max(x_band_above);
        // Only the block's vertical stack reaches this arm's chrome. UNPORTED
        // GAP, not a law: Vega's gridLayout unions the title's ink into
        // pane 0's bound and ceils the pane's own bound before adding
        // `spacing` (line.rs's `pane0_x2`; AGENTS.md's rail paragraph), so
        // this arm's title-free chrome and its `round(plot_w + right_ink +
        // spacing)` below are both a pixel adrift wherever a bar rail's
        // title outruns its mark reach. No swept bar case does.
        // support_table (S3): the strip floats the title via
        // `strip_title_offset`, porting production's closed-form correction
        // (`support_table_attachment.py:4425`), same as the composed
        // (non-concat) arm's own `plot_top` term.
        let title_stack = title
            .ink()
            .map(|ti| ti.ink_h + strip_title_offset.unwrap_or(ti.offset) - ti.dy_quirk)
            .unwrap_or(0.0);
        // support_table (S7): the pane keeps its OWN `autosize: pad` — its
        // declared height is the plot rect verbatim, exactly like the
        // composed (non-concat) arm's `h`, never chrome-shrunk to fit the
        // slot, so the top-ink basis is `h` too rather than the
        // chrome-subtracted approximation the non-strip arm probes with.
        let plot_h0 = if rail_strip_rows.is_some() {
            h
        } else {
            h - (pad_t + pad_b + title_stack + x_band_above + x_band).ceil()
        };
        // A legend kept beside the rail sits inside pane 0 (`legendParams`'
        // `xBounds`/`yBounds`: the plot rect and the axes of its own
        // orientation) and widens its bounds like any other ink.
        let legend = ((legend_shown && (!legend_entries_typed.is_empty() || numeric_legends.len() > 0)) || title_only_legend(legend_shown, &r.legend))
            .then(|| {
                vega_legend(&legend_entries_typed, numeric_legends.unlisted_line, &legend_title, &r.legend, fonts)
                    .and_then(|lg| numeric_legends.stack_onto(lg, &r.legend, fonts, (w, h)))
            })
            .transpose()?;
        // The legend's reach above and below a pane `ph` tall, whose x axis
        // band is `x_band` (`legendParams`: a top legend sits above the plot's
        // top, a bottom one below the axis band).
        let legend_y_reach = |ph: f64| -> (f64, f64) {
            let Some(lg) = &legend else { return (0.0, 0.0) };
            let size = (lg.box_w, lg.box_h());
            let mut origin = legend_origin(&r.legend.position, (0.0, 0.0), (0.0, ph + x_band), (w, ph), size);
            if r.legend.position.edge == "top" && !r.legend.position.overlay {
                origin.1 -= x_band_above;
            }
            legend_fit_bounds(&r.legend.position, origin, size).map_or((0.0, 0.0), |reach| (-reach.y1, (reach.y2 - ph).max(0.0)))
        };
        // The value labels' ink unioned into pane 0's own bounds at a pane
        // `pw` wide and `ph` tall: every edge a label crosses grows the pane's
        // margin on that side.
        let time_axis_err = std::cell::RefCell::new(None::<String>);
        let on_label_geom = |pw: f64, ph: f64, f: &mut dyn FnMut(&LabelGeometry)| {
            let band = make_band(pw);
            let linear = LinearScale::new(v_domain_min, linear_domain_max, ph, 0.0);
            let cols = match &time {
                Some(_) => columns_for(&band, pw),
                None => Columns::Banded(BandColumns::new(band.bandwidth(), mark_band_width, r.style.mark.max_size, column_split.as_ref())),
            };
            let center = |cat: &str, band_x: f64, slot: Option<usize>| {
                let cols = cols.of(cat);
                cols.label_center(band_x, slot.unwrap_or(0))
            };
            f(&LabelGeometry { band: &band, center: &center, linear: &linear });
        };
        let label_ink_at = |pw: f64, ph: f64| -> vega_fit::Bounds {
            let hide = label_hide_cfg.map(|(span, font_size)| FitHide { plot_h: ph, span, font_size });
            let mut ink = vega_fit::Bounds::new(0.0, 0.0, pw, ph);
            on_label_geom(pw, ph, &mut |geom| {
                for layer in &label_layers {
                    layer.ink(geom, if layer.total { None } else { hide }, &mut ink);
                }
            });
            // The time axis's labels and ticks are pane ink too: one centered near an end reaches past it.
            if let Some(axis) = &time_axis {
                if let Err(e) = axis.union_label_ink(pw, ph, &mut ink, fonts) {
                    time_axis_err.borrow_mut().get_or_insert(e);
                }
            }
            ink
        };
        // The probe measures them at the declared width `w`.
        let label_ink_of = |ph: f64| label_ink_at(w, ph);
        let label_top_ink = |ph: f64| (-label_ink_of(ph).y1).max(0.0);
        // A time axis centers its bars on instants the plot's own edges can sit on, so the last bar's
        // half width and stroke hang past a pane `pw` wide.
        let time_bars_overhang_at = |pw: f64| -> f64 {
            match drawn_ends {
                Some(ends) if time.is_some() && drawn_stroke_w > 0.0 => {
                    let band = make_band(pw);
                    let cols = columns_for(&band, pw);
                    let ext = cols.x_extent(&band, &cat_domain, ends);
                    (ext.1 + drawn_stroke_w - pw).max(0.0)
                }
                _ => 0.0,
            }
        };
        // Top ink at probe time: the top y tick's label half, reduced by its
        // distance from the plot top; the stacked marks stay under the
        // headroomed domain max, reaching at most their border stroke. A value
        // label past the top reaches further: the probe measures it at the
        // declared pane height `h`.
        let top_ink_of = |tick_values: &[f64], labels_ph: f64| -> f64 {
            let label_half =
                vega_fit::middle_rise(val_font_size);
            let mut ink = 0.0_f64;
            if let Some(&top_tick) = tick_values.last() {
                let dist = if linear_domain_max > 0.0 {
                    (linear_domain_max - top_tick) / linear_domain_max * plot_h0
                } else {
                    0.0
                };
                ink = ink.max(label_half - dist);
            }
            // Bars past the domain (an authored one, or rows stacked over each other) are drawn unclipped above the plot.
            let domain_span = linear_domain_max - v_domain_min;
            if value_extent.1 > linear_domain_max && domain_span > 0.0 {
                ink = ink.max((value_extent.1 - linear_domain_max) / domain_span * labels_ph + drawn_stroke_w);
            }
            // A layer caption above the plot's top reaches into the margin, at
            // the height of the pane it is drawn in (the probe's is the spec's own).
            for LayerLabelFit { style, items, .. } in &layer_label_fit {
                for LayerLabelFitItem { val, width, spot, .. } in items {
                    let y = if linear_domain_max > 0.0 {
                        ((linear_domain_max - val) / linear_domain_max * labels_ph).clamp(0.0, labels_ph)
                    } else {
                        labels_ph
                    };
                    ink = ink.max(-style.ink(0.0, y, *width, spot).y1);
                }
            }
            ink.max(label_top_ink(labels_ph)).max(legend_y_reach(h).0).max(0.0)
        };
        // The x axis band, grown to the legend a bottom edge hangs below it.
        // A value label hanging below the plot (past a negative column's end)
        // pushes the category labels down to keep `VALUE_AXIS_BREATHING_ROOM`
        // from it; `x_clear` carries the last pass's push to the paint.
        let x_clear_of = |ph: f64| {
            if time_axis.is_some() {
                return 0.0;
            }
            let mut hang = 0.0;
            on_label_geom(w, ph, &mut |geom| hang = label_hang(&label_layers, geom, ph, x_edge));
            category_clearance(hang, ax_off_x + x_tick_len + cat_pad)
        };
        let bottom_band_of = |ph: f64| {
            let clear = x_clear_of(ph);
            x_clear.set(clear);
            (x_band + clear).max(legend_y_reach(ph).1).max(label_ink_of(ph).y2 - ph)
        };
        // `_correct_concat_overshoot`: a pane the chrome would shrink to nothing
        // keeps its declared height, and the canvas grows to hold the chrome.
        let plot_h_of = |tick_values: &[f64]| -> f64 {
            if rail_strip_rows.is_some() {
                h
            } else {
                let top_ink = top_ink_of(tick_values, h);
                let provisional = h - (pad_t + pad_b + above(top_ink) + title_stack + x_band).ceil();
                let chrome_h = pad_t + pad_b + above(top_ink) + title_stack + bottom_band_of(provisional);
                let shrunk = h - chrome_h.ceil();
                if shrunk > 0.0 { shrunk } else { h }
            }
        };
        // An unbaked, uncounted value axis takes Vega-Lite's default
        // `tickCount`, `ceil(height/40)`, at the pane's final height
        // (`unbaked_rungs`), replacing `niced_rail_domain`'s provisional
        // ladder. The ladder reaches the height only through `top_ink`'s
        // sub-pixel term, so one pass settles it; the loop guards the ceil.
        let (mut tick_values, mut val_tick_labels, mut max_val_w) =
            (tick_values, val_tick_labels, max_val_w);
        let mut plot_h = plot_h_of(&tick_values);
        for _ in 0..4 {
            let Some((rungs, labels)) = (match date_rungs(plot_h)? {
                Some(dated) => Some(dated),
                None => unbaked_rungs(ax_y, normalize, false, 0.0, linear_domain_max, plot_h)
                    .map(|rungs| format_tick_labels(&rungs, ax_y, normalize, "y").map(|labels| (rungs, labels)))
                    .transpose()?,
            }) else { break };
            if rungs == tick_values {
                break;
            }
            tick_values = rungs;
            val_tick_labels = labels;
            max_val_w = val_tick_labels.iter().try_fold(0.0_f64, |acc, l| {
                fonts.measure_shaped(l, val_family, val_font_size).map(|lw| acc.max(lw))
            })?;
            plot_h = plot_h_of(&tick_values);
        }
        let top_ink = top_ink_of(&tick_values, plot_h);
        // The value axis's own ink extent. Vega lays each axis out with an
        // integer (ceil'd) extent before the panes negotiate widths — but
        // the title group keeps the UNROUNDED one (see `title_pos` below).
        let left_cell_ink = match &rail_strip_rows {
            Some((rows, _, _)) if rail_right => rows
                .iter()
                .flat_map(|row| row.cells.iter().map(move |cell| cell.w - (cell.x + row.dx)))
                .fold(0.0_f64, f64::max),
            _ => 0.0,
        };
        let y_extent_ink = if rail_right {
            rules.map_or(0.0, |b| b.width).max(left_cell_ink)
        } else {
            max_val_w + val_pad + val_tick_size + val_title_extra
        }
        .max(-label_ink_of(plot_h).x1);
        let mut y_extent = y_extent_ink.ceil();
        // support_table (S6, the left-hang half): the stub label is pane-0
        // ink on the LEFT too — it sits at `-val_pad` (S4), growing further
        // left by its own (already `_LABEL_STUB_LIMIT_PX`-capped) shaped
        // width — so a stub wider than the axis-only gutter widens
        // `y_extent` itself, exactly the way the strip's right-reaching
        // cells widen `right_ink` above. `region_bar_grouped`'s "Top cat"
        // stub is the corpus case that reaches this.
        if let Some((rows, _, _)) = rail_strip_rows.as_ref().filter(|_| !rail_right) {
            let widest_stub_w = rows.iter().fold(0.0_f64, |a, row| a.max(row.label_w));
            y_extent = y_extent.max((val_pad + widest_stub_w).ceil());
        }
        // Two-pass port of Vega's `gridLayout` (the bundled vega-view-
        // transforms source; `line.rs`'s own `_correct_concat_overshoot`
        // carries the identical structure, copied here rather than
        // invented a third time): pane 0's own bound is CEIL'D on its own,
        // `spacing` added raw, never `round(plot_w + right_ink + spacing)`
        // as one sum — and the canvas is left to overshoot the target
        // width on this first (uncorrected) pass before the SAME layout
        // re-runs on the corrected pane.
        let legend_at = |pane_w: f64| {
            legend.as_ref().map(|lg| {
                let size = (lg.box_w, lg.box_h());
                let axis_x1 = if rail_right { 0.0 } else { -y_extent_ink };
                let axis_x2 = if rail_right {
                    pane_w + ax_off_y + val_tick_size + val_gutter + val_title_extra
                } else {
                    pane_w
                };
                let axis_x2 = axis_x2.max(crate::annotation::text_x2(&annotation_ctx((0.0, 0.0), pane_w, plot_h)));
                let mut origin = legend_origin(&r.legend.position, (axis_x1, axis_x2), (0.0, plot_h + x_band), (pane_w, plot_h), size);
                // A top legend sits above the x axis hung over the plot.
                if r.legend.position.edge == "top" && !r.legend.position.overlay {
                    origin.1 -= x_band_above;
                }
                (origin, legend_fit_bounds(&r.legend.position, origin, size))
            })
        };
        let legend_reach = |pane_w: f64| legend_at(pane_w).and_then(|(_, reach)| reach);
        if let Some(reach) = legend_reach(w) {
            y_extent = y_extent.max((-reach.x1).ceil());
        }
        let pane0_x2 = |pane_w: f64| {
            let legend_x2 = legend_reach(pane_w).map_or(0.0, |reach| reach.x2);
            // Annotation text past the plot's right edge is pane 0's own ink.
            let annotation_right = crate::annotation::bounds(&annotation_ctx((0.0, 0.0), pane_w, plot_h))
                .map_or(0.0, |b| (b.x2 - pane_w).max(0.0));
            let right = right_ink
                .max(label_ink_at(pane_w, h).x2 - pane_w)
                .max(time_bars_overhang_at(pane_w))
                .max(annotation_right);
            (pane_w + right).max(stub_x2).max(legend_x2)
        };
        let uncorrected_x2 = pane0_x2(w);
        if let Some(e) = time_axis_err.take() {
            return Err(e.into());
        }
        let rail_offset_uncorrected = uncorrected_x2.ceil() + spacing;
        let canvas_w_uncorrected =
            (pad_l + pad_r + y_extent + rail_offset_uncorrected + rail_pane_w).ceil();
        let plot_w = 2.0 * w - canvas_w_uncorrected;
        // `_correct_concat_overshoot`: a pane the width overshoot leaves with no
        // width is a bug signal, never a clamp.
        if plot_w <= 0.0 {
            return Err(Diagnostic::new(
                &codes::ERR_CONCAT_OVERSHOOT_NONPOSITIVE,
                [
                    ("new_w", json!(plot_w)),
                    ("orig_w", json!(w)),
                    ("overshoot", json!(canvas_w_uncorrected - w)),
                    ("target_width", json!(w)),
                ],
            ));
        }
        let left_margin = y_extent;
        // support_table (S3): the strip's own ink extent rides on top of the
        // title's bumped offset — the doubled gap the composed (non-concat)
        // arm's own `plot_top` already carries. The concat spec carries no
        // padding bump for an untitled strip (the composed arm's `padding.top`
        // one), so the ink alone sets the margin; the top tick label's own
        // overhang sits inside the strip's rows, never past them.
        let ink_ext = rail_strip_rows.as_ref().filter(|_| !rail_bottom_strip).map_or(0.0, |(_, _, ie)| *ie);
        // One ceil over the whole overhang, as `pad_plot` takes it
        // (`ViewLayout.js:165-168`): the plot origin is a whole pixel.
        let legend_top = legend_reach(w).map_or(0.0, |reach| -reach.y1);
        let top_margin = (above(top_ink).max(ink_ext).max(legend_top) + title_stack).ceil();
        // The correction re-runs the SAME grid layout on the shrunk pane:
        // ceil pane 0's own bound, add `spacing` raw. Every other term of
        // the canvas sum is unchanged, so the canvas moves by exactly what
        // that ceil'd bound did — computed as a delta rather than re-added,
        // which keeps it exactly `w` whenever nothing moves.
        let corrected_x2 = pane0_x2(plot_w);
        let canvas_w = canvas_w_uncorrected + corrected_x2.ceil() - uncorrected_x2.ceil();
        let (canvas_h, composed_rows) = if rail_strip_rows.is_some() {
            // S7: the canvas is left to overshoot the slot — summed
            // directly, the one place this arm departs from the
            // non-`support_table` rail concat's own canvas_h (the untouched
            // `h` default). The plot height is added verbatim between margins
            // that are each ceil'd on their own (`ViewLayout.js:165-168`,
            // `:198-201`; `vega_fit::pad_plot`), so a fractional height stays
            // fractional.
            // A bottom strip's rows reach below the axis band; the concat's padding
            // carries no bump for the strip, so the margin is the ceil'd ink of
            // whichever reaches further.
            let legend_bottom = legend_reach(plot_w).map_or(0.0, |reach| reach.y2 - plot_h);
            // The strip path never reaches `bottom_band_of`, so the category
            // labels' clearance from a hanging value label is set here.
            let strip_clear = x_clear_of(plot_h);
            x_clear.set(strip_clear);
            let x_band = x_band + strip_clear;
            let bottom_margin = match (&rail_strip_rows, rail_bottom_strip) {
                (Some((rows, _, _)), true) => {
                    let strip_style = r.effective_support_table_style.as_ref().expect("a rail strip implies its style");
                    let mut strip_ink = vega_fit::Bounds::new(0.0, 0.0, 0.0, plot_h);
                    support_table_strip::composed_ink(
                        &mut strip_ink,
                        rows,
                        strip_style,
                        ax_y.labels.padding.unwrap_or(6.0),
                        plot_w,
                        false,
                        plot_h,
                        strip_axis_offset,
                    );
                    x_band.max(strip_ink.y2 - plot_h).max(legend_bottom).ceil()
                }
                _ => x_band.max(legend_bottom).ceil(),
            };
            let canvas_h = pad_t + top_margin + plot_h + bottom_margin + pad_b;
            // The strip's cells are positioned by the FINAL (corrected)
            // plot_w — production's own re-render at the shrunk pane width —
            // so they're rebuilt here rather than reusing the probe's own
            // `rail_strip_rows` (built at the uncorrected `w` purely to
            // measure S8's overhang term above).
            let cfg = rail_strip_cfg.expect("rail_strip_rows implies rail_strip_cfg");
            let strip_style = r
                .effective_support_table_style
                .as_ref()
                .expect("rail_strip_rows implies effective_support_table_style");
            let band = BandScale::new(cat_domain.clone(), 0.0, plot_w, band_pi, band_po);
            let centers = strip_centers(&band, &cat_domain, time.as_ref().zip(time_axis.as_ref()), plot_w);
            let mut composed_rows = support_table_strip::build_rows_composed(
                    &r.id,
                cfg, rows, cat_field, &cat_domain, &centers, fonts, strip_style, true,
                time.as_ref().is_none_or(TimeX::spans_buckets), val_on_right, color_field, &per_series_order, &per_series_ink,
                r.strip_linked().then_some(&strip_links), false)?;
            let strip_aria = strip_cell_aria(r, &cat_title_text, &cat_domain, time.as_ref(), color_field)?;
            composed_rows.iter_mut().for_each(|row| row.describe(&strip_aria));
            if rail_right {
                composed_rows.iter_mut().for_each(|row| row.stub_dx = w - plot_w);
            }
            (canvas_h, Some(composed_rows))
        } else if label_top_ink(plot_h) > 0.0 {
            // A label past the plot top is chrome the pane's height does not
            // absorb (`vega_fit::pad_plot`).
            (pad_t + top_margin + plot_h + bottom_band_of(plot_h).ceil() + pad_b, None)
        } else {
            // The shipped height is the corrected pane's real chrome around it: the
            // spec's own `h` unless the final pane's top ink moved off the probe's.
            (plot_h + (pad_t + pad_b + above(top_ink) + title_stack + bottom_band_of(plot_h)).ceil(), None)
        };
        // Vega anchors the title group at the view bounds' own left edge —
        // the axis's unrounded ink extent — while the plot group's translate
        // takes the ceil'd one, so the title lands at `padding + ceil(ink) −
        // ink`, inside `[padding, padding + 1)`. Reference title x: 16.199
        // (`column_stacked_review`, ink 44.80078 at origin 61), 16.800
        // (`column_stacked_100_review`), 16.983 (`hero_bookings`), 16.602
        // (`hero_queue_age`), 16.734 (`column_tickets_by_priority_temporal`).
        // support_table (S1/S7): a strip pad-anchors the title at the canvas
        // padding edge, same as line.rs's own rail+strip arm — the group
        // offset negated (`-left_margin, -top_margin`), routed through the
        // SAME `emit_title_block_vega` call every other shape here uses, so
        // an authored subtitle keeps reserving and drawing (MEDIUM-1, round
        // 2: the old separate `emit_title_vega(pad_l, pad_t)` bypass drew
        // the title only, silently dropping a subtitle `title_stack` still
        // reserved room for).
        let title_pos = title.ink().map(|ti| {
            if rail_strip_rows.is_some() {
                (-left_margin, -top_margin)
            } else {
                // The hoisted title anchors at pane 0's bounds, a left legend included.
                let left = legend_reach(plot_w).map_or(-y_extent_ink, |reach| reach.x1.min(-y_extent_ink));
                (left, -above(top_ink) - ti.ink_h - ti.offset)
            }
        });
        PlotRect {
            plot_left: pad_l + left_margin,
            plot_top: pad_t + top_margin,
            plot_w,
            plot_h,
            title_pos,
            legend: legend_at(plot_w)
                .zip(legend)
                .map(|(((x, y), _), lg)| (pad_l + left_margin + x, pad_t + top_margin + y, lg)),
            rail_x_rel: Some(corrected_x2.ceil() + spacing),
            canvas_w,
            canvas_h,
            composed_rows,
            facet_axis_gutter: 0.0,
            top_axis_overhang: 0.0,
            facet_axis_right_overhang: 0.0,
            raw_right_reach: 0.0,
            val_visible: None,
            linear_domain_max,
            tick_values,
            val_tick_labels,
            max_val_w,
            val_gutter,
            temporal_labels,
            x_tick_len,
            label_rows,
        }
    } else {
        // Per-label measurements for the ink closure.
        let val_label_rows = std::cell::RefCell::new(Vec::<usize>::new());
        let measure_labels = |labels: &[String]| -> Result<Vec<f64>, String> {
            *val_label_rows.borrow_mut() = labels.iter().map(|l| crate::text::line_count(l)).collect();
            labels.iter().map(|l| fonts.measure_shaped(l, val_family, val_font_size)).collect()
        };
        let mut val_label_ws = measure_labels(&val_tick_labels)?;
        let val_title_extra = crate::common::y_title_extra(val_title_style, &val_title_paint);
        let cat_title_extra = crate::common::x_title_extra(&ax_x.title, &cat_title_paint);
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
        let mut val_visible: Option<Vec<bool>> = None;
        // See `render_vertical_continuous_x_panel`'s own `top_overhang` doc
        // comment (same law, same probe evidence) — reset at the START of
        // every `fit()` call (below), never accumulated ACROSS calls: an
        // unbaked ladder's own `tick_values` can change between the retry
        // loop's iterations, and a stale call's overhang must not survive
        // into the next one's measurement.
        let top_overhang = std::cell::Cell::new(0.0_f64);
        // A facet panel (`suppress_x_axis`) compiles under real Vega's
        // `autosize: pad`: the plot rect is the composer's own declared
        // `w`×`h` (`panel_w`×`child_height`) verbatim, and axis ink overflows
        // outward, growing the panel's own ink past `h` — never the `fit`
        // shrink the single-chart wrapper uses to keep ink inside a fixed
        // card. Same `ink` closure either way (`vega_fit::pad_plot`'s doc
        // comment): only which law resolves the margins differs.
        // A top strip floats the title by the theme's own offset (never VL's
        // 10.0 default) plus production's closed-form font-size correction
        // (`support_table_attachment.py:4951-4962`); the strip's own ink
        // extent clears separately, so the offset never carries `strip_h`. A
        // bottom strip leaves the title as an unstripped pad chart has it.
        let composed_title = composed.as_ref().and_then(|(_, _, strip_style)| {
            if strip_style.position == "bottom" {
                return title.ink().cloned();
            }
            let theme_offset = title_ref.and_then(|t| t.position.as_ref()).and_then(|p| p.offset).unwrap_or(0.0);
            let correction = support_table_strip::support_table_title_offset_correction(title_fs);
            title.ink().map(|t| vega_fit::TitleInk { offset: theme_offset + correction, ..t.clone() })
        });
        let fit_err = std::cell::RefCell::new(None::<String>);
        let mut fit = |tick_values: &[f64], primary_labels: &[String], primary_ws: &[f64], val_gutter: f64| {
            top_overhang.set(0.0);
            // The right edge of a flipped left axis paints the mirror's ghost labels.
            let flipped_ws = match ghost.as_ref().filter(|_| left_flip) {
                Some(g) => g.widths(tick_values, primary_labels, primary_ws, |label| fonts.measure_shaped(label, val_family, val_font_size)),
                None => Ok(primary_ws.to_vec()),
            }
            .unwrap_or_else(|e| {
                fit_err.borrow_mut().get_or_insert(e);
                primary_ws.to_vec()
            });
            let val_label_ws = flipped_ws.as_slice();
            let mut ink_pass = |pw: f64, ph: f64, b: &mut vega_fit::Bounds| {
                // The value scale's position of `v`, pinned to the plot
                // bottom on a degenerate domain.
                let y_at = |v: f64| {
                    if linear_domain_max != v_domain_min {
                        ph * (linear_domain_max - v) / (linear_domain_max - v_domain_min)
                    } else {
                        ph
                    }
                };
                // Value axis gutter (right by default) + rotated title.
                let mut val_axis_x2 =
                    if val_on_right && !ghost_only { pw + ax_off_y + val_tick_size + val_gutter + val_title_extra } else { pw };
                if val_title_extra > 0.0 && facet.is_none() {
                    let x = if val_on_right { val_axis_x2 } else { -(ax_off_y + val_tick_size + val_gutter + val_title_extra) };
                    b.union(&vega_fit::Bounds::new(x, ph / 2.0 - val_title_w / 2.0, x, ph / 2.0 + val_title_w / 2.0));
                }
                if val_on_right {
                    b.union(&vega_fit::Bounds::new(0.0, 0.0, val_axis_x2, ph));
                } else {
                    b.union(&vega_fit::Bounds::new(
                        -(ax_off_y + val_tick_size + val_gutter + val_title_extra),
                        0.0,
                        0.0,
                        ph,
                    ));
                }
                // Value tick labels' ink (middle baseline, right-aligned at
                // the gutter's far edge): the top tick can overhang the plot
                // top; on a left axis the label widths set the gutter.
                // Vega's parity overlap hides every other label under the
                // theme's min_gap; only the visible labels' ink counts.
                let rows = val_label_rows.borrow();
                let label_ink: Vec<vega_fit::Bounds> = tick_values
                    .iter()
                    .zip(val_label_ws.iter())
                    .zip(rows.iter())
                    .map(|((&tv, &lw), &lines)| {
                        let y = y_at(tv);
                        let (x, align) = if val_away {
                            (pw + ax_off_y + val_tick_size + base_label_pad, "left")
                        } else if val_on_right {
                            (pw + ax_off_y + val_tick_size + val_gutter, "right")
                        } else {
                            (-(ax_off_y + val_tick_size + base_label_pad), "right")
                        };
                        vega_fit::text_ink_rows(x, y, lw.min(val_limit), val_font_size, align, "middle", lines)
                    })
                    .collect();
                let visible = vega_fit::parity_visible(&label_ink, val_separation);
                // The value axis's own left edge: `xBounds.x1` for a left
                // legend.
                let mut y_axis_x1 = if val_on_right {
                    0.0
                } else {
                    -(ax_off_y + val_tick_size + val_gutter + val_title_extra)
                };
                for (ink, _) in label_ink.iter().zip(&visible).filter(|(_, &v)| v) {
                    y_axis_x1 = y_axis_x1.min(ink.x1);
                    // A facet panel's value axis is real Vega's
                    // `row_footer` (shared scale) or embedded IN `cell`
                    // (independent scale) — either way, this closure's own
                    // `b`/`m.top`/`m.bottom` never carry it: `m.top`/
                    // `m.left` DO shift this panel's own `plot_top`/
                    // `plot_left` (`pad_plot`'s own law), so a raw
                    // negative `y1` here would wrongly push this panel's
                    // own content inward rather than just reserving room
                    // at the facet's own outer top margin. Clamping to
                    // `[0, ph]` keeps the width contribution (still needed
                    // for `val_gutter`'s own fit) while dropping the
                    // vertical one — `parity_visible` above already saw
                    // each label's own true (unclamped) ink for collision
                    // detection, and `top_overhang`, tracked unclamped
                    // just below, is the SEPARATE, correct home for the
                    // topmost tick's own overhang (`PanelGeometry.
                    // top_axis_overhang` → `compose_facet`'s
                    // `margin_top`). The independent-scale embedded axis's
                    // own BOTTOM overhang (the "0" label's descent below
                    // `ph`) is real row height too, but is ported at the
                    // val-label PAINT loop below instead, unioning
                    // straight into `PanelGeometry.ink` (the composite's
                    // own measured height) rather than this closure's `b`.
                    if suppress_x_axis {
                        top_overhang.set(top_overhang.get().min(ink.y1));
                        if !ghost_only {
                            b.union(&vega_fit::Bounds::new(ink.x1, ink.y1.max(0.0), ink.x2, ink.y2.min(ph)));
                        }
                    } else {
                        b.union(ink);
                    }
                }
                if mirror_active {
                    let (x, align) = if val_on_right {
                        (-(ax_off_y + val_tick_size + if val_away { base_label_pad } else { val_gutter }), "right")
                    } else {
                        (pw + ax_off_y + val_tick_size + base_label_pad, "left")
                    };
                    let ghost_ws = ghost.as_ref().map_or(Ok(Vec::new()), |g| {
                        g.widths(tick_values, primary_labels, val_label_ws, |label| fonts.measure_shaped(label, val_family, val_font_size))
                    });
                    let ghost_ws = match ghost_ws {
                        Ok(ws) => ws,
                        Err(e) => {
                            fit_err.borrow_mut().get_or_insert(e);
                            return;
                        }
                    };
                    for ((&tv, &lw), _) in tick_values.iter().zip(&ghost_ws).zip(&visible).filter(|(_, &v)| v) {
                        let ghost = vega_fit::text_ink(x, y_at(tv), lw.min(val_limit), val_font_size, align, "middle");
                        y_axis_x1 = y_axis_x1.min(ghost.x1);
                        val_axis_x2 = val_axis_x2.max(ghost.x2);
                        b.union(&ghost);
                    }
                }
                val_visible = Some(visible);
                // A bar's own mark border stroke, wherever it crosses this
                // panel's own `y=0`, is counted once — by `bar_ink`'s own
                // union into `PanelGeometry.ink` (below), which
                // `facet_layout::grid_layout`'s own `offset_value` (a
                // direct port of `vega-view-transforms/src/layout/
                // grid.js`'s row-alignment padding) already turns into the
                // clearance the NEXT row needs before it. Vega's own
                // `viewSizeLayout` (`ViewLayout.js:165`) reserves upward
                // ink exactly once, as `top = ceil(-viewBounds.y1)`, over
                // the UNION of every role's own bounds — never a second,
                // per-mark reading folded into `top_axis_overhang` on top
                // of it.
                // A shared-scale layer's points sit where their data does, which on a time axis can be the plot's own
                // edge: their reach there is margin.
                if !independent_y {
                    let band = make_band(pw);
                    for (layer, l_rows) in layers.iter().zip(&layer_rows).filter(|(layer, _)| !matches!(layer, OverlayLayer::Bar(_))) {
                        let reach = layer.mark_reach();
                        for row in l_rows.iter().filter(|row| row_num(row, layer.y()).is_some()) {
                            if let Some(cx) = band.center(&row_str(row, layer.x().unwrap_or(cat_field))) {
                                b.union(&vega_fit::Bounds::new(cx - reach, ph / 2.0, cx + reach, ph / 2.0));
                            }
                        }
                    }
                }
                // Dual axis: each layer's own gutter and title are extra ink on
                // its side, beyond the base's own on the other.
                for ((((la, p), layer), bar_spans), zero_rule) in
                    layer_axes.iter().zip(&scaled).zip(layers).zip(&layer_bar_spans).zip(&layer_zero_rules)
                {
                    la.union_axis_ink(b, (pw, ph), ax_off_y, val_limit, val_font_size);
                    let scale = la.scale(ph);
                    let band = make_band(pw);
                    // A point at the layer's domain top pokes its symbol past the plot; a
                    // line layer's stroked path adds the full stroke width (`boundStroke`)
                    // and its drawn points are unclipped.
                    let mark_r = layer.mark_reach();
                    if la.domain_max > la.domain_min && la.cut != DomainCut::Clip {
                        if let OverlayLayer::Bar(bl) = layer {
                            // A bar's border adds its stroke to the rect (`bar_ink`).
                            let border = &bl.bar_mark.border;
                            let mark_stroke = border.width.unwrap_or(0.0);
                            if mark_stroke > 0.0 && border.color.as_deref().is_some_and(|c| c != "none") {
                                for (cat, start, end) in bar_spans {
                                    let Some(bx) = band.position(cat) else { continue };
                                    let (y1, y2) = (scale.position(*end), scale.position(*start));
                                    b.union(&vega_fit::Bounds::new(bx, y1.min(y2), bx + band.bandwidth(), y1.max(y2)).expand(mark_stroke));
                                }
                            }
                        } else {
                            for row in p.rows {
                                let (Some(cx), Some(v)) = (band.center(&row_str(row, p.x)), row_num(row, p.y)) else {
                                    continue;
                                };
                                let y = scale.position(v);
                                b.union(&vega_fit::Bounds::new(cx - mark_r, y - mark_r, cx + mark_r, y + mark_r));
                            }
                        }
                    }
                    // The layer's own zero rule sits at its own scale's zero.
                    if let Some(rule_width) = zero_rule {
                        let y = scale.position(0.0);
                        b.union(&vega_fit::Bounds::new(0.0, y, pw, y).expand(*rule_width));
                    }
                }
                // Bottom band axis labels: centered on band centers, shifted
                // down by the axis offset; two-row temporal labels stack one
                // lineHeight (fs+2) further. Skipped entirely when
                // `suppress_x_axis` — a facet panel's own shared x-axis is
                // drawn once by the composer (`families/multiples.rs`), not
                // per panel, so a panel's own plot rect must not reserve
                // margin for ink it will never paint.
                // The category axis's own bottom edge: `yBounds.y2` for a
                // bottom legend.
                let mut x_axis_y2 = ph;
                let mut x_axis_y1 = 0.0_f64;
                let mut axis_ink = vega_fit::Bounds::empty_new();
                if paint_x_axis {
                    if let Some(axis) = &time_axis {
                        let mut ink = vega_fit::Bounds::empty_new();
                        if let Err(e) = axis.union_label_ink(pw, ph, &mut ink, fonts) {
                            fit_err.borrow_mut().get_or_insert(e);
                        }
                        b.union(&ink);
                        axis_ink.union(&ink);
                        x_axis_y2 = x_axis_y2.max(ink.y2);
                        x_axis_y1 = x_axis_y1.min(ink.y1);
                    } else {
                        let band = BandScale::new(cat_domain.clone(), 0.0, pw, band_pi, band_po);
                        // A value label hanging below the plot pushes the category
                        // labels down to keep its gap from them.
                        let hang = {
                            let linear = LinearScale::new(v_domain_min, linear_domain_max, ph, 0.0);
                            let cols = columns_for(&band, pw);
                            let center = |cat: &str, band_x: f64, slot: Option<usize>| cols.of(cat).label_center(band_x, slot.unwrap_or(0));
                            label_hang(&label_layers, &LabelGeometry { band: &band, center: &center, linear: &linear }, ph, x_edge)
                        };
                        let clear = category_clearance(hang, ax_off_x + x_tick_len + cat_pad);
                        x_clear.set(clear);
                        let row_y = x_edge.at(ph, ax_off_x + x_tick_len + cat_pad + clear);
                        let mut label_inks: Vec<vega_fit::Bounds> = Vec::with_capacity(cat_label_ink.len());
                        for &(at, lw, rows) in &cat_label_ink {
                            let Some(cx) = bucket_x(&band, &cat_domain, at) else { continue };
                            // Rotated ink — `common::rotated_category_label_ink`
                            // is the shared rotation law (vega-scenegraph's
                            // `text.js bound()` → `Bounds.rotate`, no expand)
                            // scatter's own nominal-x tilt arm reuses verbatim;
                            // this band axis's OWN ±0.5 crisp-translate term
                            // (probe evidence below) is added here, at the call
                            // site, never inside the shared function. The
                            // rendered label itself sits at the exact band
                            // center (wk_26
                            // scenegraph: label local x 9.1539 inside the +0.5
                            // crisp group = center 9.6539 absolute); the axis's
                            // OWN side extent stays arithmetic (tick + padding +
                            // label rows — no expand). The ±0.5 below is a
                            // BAND-axis term, not part of the shared ink law: a
                            // band label sits inside the axis group's own +0.5
                            // crisp translate, and vega's layout ROUNDS the
                            // resulting overhang where `fit_plot` ceils —
                            // `round(o + 1) ≡ ceil(o + 0.5)` away from exact
                            // halves — so this ±0.5 folds that crisp translate
                            // back into the existing ceil; a point-scale axis
                            // (scatter's nominal x) carries no such translate
                            // and must not add it.
                            label_inks.push(crate::common::rotated_category_label_ink(
                                cx,
                                row_y,
                                lw,
                                cat_font_size,
                                cat_align,
                                cat_baseline,
                                cat_angle,
                                rows - 1,
                            ));
                        }
                        let shown = match x_parity_separation {
                            Some(separation) => vega_fit::parity_visible(&label_inks, separation),
                            None => vec![true; label_inks.len()],
                        };
                        for (mut ink, _) in label_inks.into_iter().zip(shown).filter(|(_, shown)| *shown) {
                            ink.x1 -= 0.5;
                            ink.x2 += 0.5;
                            b.union(&ink);
                            axis_ink.union(&ink);
                            x_axis_y2 = x_axis_y2.max(ink.y2);
                            x_axis_y1 = x_axis_y1.min(ink.y1);
                        }
                    }
                    if cat_title_extra > 0.0 {
                        let (y1, y2) = match x_edge {
                            crate::common::XEdge::Bottom => (0.0, ph + (b.y2 - ph).max(0.0) + cat_title_extra),
                            crate::common::XEdge::Top => (-((-b.y1).max(0.0) + cat_title_extra), ph),
                        };
                        let title_reach =
                            vega_fit::Bounds::new(pw / 2.0 - cat_title_w / 2.0, y1, pw / 2.0 + cat_title_w / 2.0, y2);
                        b.union(&title_reach);
                        axis_ink.union(&title_reach);
                        x_axis_y2 = x_axis_y2.max(title_reach.y2);
                        x_axis_y1 = x_axis_y1.min(title_reach.y1);
                    }
                    x_axis_ink.set(axis_ink);
                }
                // Annotation text clears the ink so far: axes, titles, marks.
                annotation_reach.set(crate::annotation::Reach::past(b, pw, ph));
                if let Some(ab) = crate::annotation::bounds(&annotation_ctx((0.0, 0.0), pw, ph)) {
                    b.union(&ab);
                }
                let strip_ink = composed.as_ref().map(|(strip_rows, _, strip_style)| {
                    let mut ink = vega_fit::Bounds::empty_new();
                    support_table_strip::composed_ink(
                        &mut ink,
                        strip_rows,
                        strip_style,
                        ax_y.labels.padding.unwrap_or(6.0),
                        pw,
                        val_on_right,
                        if strip_style.position == "bottom" { ph } else { 0.0 },
                        strip_axis_offset,
                    );
                    ink
                });
                // A facet panel's legend is chart-level: the composer lays it
                // out against the whole composite (`facet_compose::
                // FacetLegend`), so it is no panel's ink.
                if let Some(lg) = legend.as_ref().filter(|_| facet.is_none()) {
                    // `xBounds` is the plot rect ∪ the value axis (the bars
                    // and rules are bare marks, the bottom axis joins
                    // `yBounds`, and layoutAxis translates an axis's bounds
                    // by its position without the 0.5 crisp delta — decoded
                    // from the vega source and probe-pinned across
                    // labelPadding steps); `yBounds` the plot rect ∪ the
                    // category axis.
                    let size = (lg.box_w, lg.box_h());
                    // A rounded bar compiles to one scope group per datum, so
                    // its stroke-expanded top joins `yBounds`: bars reaching
                    // the plot's top edge push a top legend out by it. Vega-Lite
                    // groups only stacked, banded bars; a continuous-x bar takes this
                    // term without that group, and a ranged bar takes none (not at its
                    // top edge, nor past an authored domain).
                    let base_bars_top = match drawn_ends {
                        Some(_) if r.y_start.is_none() && corner_r > 0.0 && stroke_color != "none" && stroke_w > 0.0 => {
                            (y_at(value_extent.0).min(y_at(value_extent.1)) - stroke_w).min(0.0)
                        }
                        _ => 0.0,
                    };
                    // A rounded, stroked bar layer reaching its own domain top does the same.
                    let layer_bars_top = layer_axes
                        .iter()
                        .zip(layers)
                        .filter_map(|(la, layer)| match layer {
                            OverlayLayer::Bar(bl) if la.cut != DomainCut::Clip => {
                                let border = &bl.bar_mark.border;
                                let stroke = border.width.unwrap_or(0.0);
                                (border.radius.unwrap_or(0.0) > 0.0 && border.color.as_deref().is_some_and(|c| c != "none") && stroke > 0.0)
                                    .then(|| (la.scale(ph).position(la.value_max) - stroke).min(0.0))
                            }
                            OverlayLayer::Bar(_) | OverlayLayer::Line(_) | OverlayLayer::Scatter(_) => None,
                        })
                        .fold(0.0, f64::min);
                    // A color-grouped line or area layer's paths are scope groups too.
                    let layer_lines_top = line_layers
                        .iter()
                        .zip(&line_layer_rows)
                        .zip(&line_layer_at)
                        .zip(&line_band_step)
                        .map(|(((l, l_rows), &at), &band_step)| {
                            let y_of = |v: f64| if independent_y { layer_axes[at].scale(ph).position(v) } else { y_at(v) };
                            // A connected step is one path whose bounds stay on its values.
                            scope_group_top(l, l_rows, l.x.as_deref().unwrap_or(cat_field), band_step && l.is_disconnected(), y_of)
                        })
                        .fold(0.0, f64::min);
                    // A shared-scale bar layer's own stack does too.
                    let shared_layers_top = bar_layers
                        .iter()
                        .zip(&bar_layer_rows_list)
                        .filter(|(bl, bl_rows)| !independent_y && bl.y_start.is_none() && !bar_clip(bl, bl_rows))
                        .map(|(bl, bl_rows)| {
                            let border = &bl.bar_mark.border;
                            let stroke = border.width.unwrap_or(0.0);
                            if border.radius.unwrap_or(0.0) > 0.0 && border.color.as_deref().is_some_and(|c| c != "none") && stroke > 0.0 {
                                (y_at(stack_reach(bl_rows, cat_field, &bl.y).1) - stroke).min(0.0)
                            } else {
                                0.0
                            }
                        })
                        .fold(0.0, f64::min);
                    let bars_top = base_bars_top.min(layer_bars_top).min(layer_lines_top).min(shared_layers_top);
                    // The legend clears every value axis, the layers' own included.
                    let (axes_x1, axes_x2) =
                        layer_axes.iter().fold((y_axis_x1, val_axis_x2), |axes, la| la.widen_axes(axes, pw, ax_off_y));
                    let axes_x2 = axes_x2.max(crate::annotation::text_x2(&annotation_ctx((0.0, 0.0), pw, ph)));
                    // ... and the strip's rows, ink beside the plot like an axis.
                    let mut bounds = vega_fit::Bounds::new(axes_x1, bars_top.min(x_axis_y1), axes_x2, x_axis_y2);
                    if let Some(ink) = &strip_ink {
                        bounds.union(ink);
                    }
                    let origin = legend_origin(
                        &r.legend.position, (bounds.x1, bounds.x2), (bounds.y1, bounds.y2), (pw, ph), size,
                    );
                    legend_xy.set(origin);
                    if let Some(reach) = legend_fit_bounds(&r.legend.position, origin, size) {
                        b.union(&reach);
                    }
                }
                // Zero rule along the plot bottom; the 100% rule, when drawn,
                // is a stroke of its own at its scale position (the plot top
                // for a normalize stack).
                if let Some(rules) = rules {
                    if rules.zero {
                        let zero_y = y_at(0.0);
                        b.union(&vega_fit::Bounds::new(0.0, zero_y, pw, zero_y).expand(rules.width));
                    }
                    // A facet panel's rule is cell ink (`PanelGeometry.ink`), not margin.
                    if rules.unity && facet.is_none() {
                        let unity_y = y_at(1.0);
                        b.union(&vega_fit::Bounds::new(0.0, unity_y, pw, unity_y).expand(rules.width));
                    }
                }
                // The bars' rect, stroke-expanded (`boundStroke`, the full
                // width — `docs/laws-bar.md`, "Vertical bar rects reserve
                // their stroke"): in x, the outer bars' edges, so a band that
                // leaves less padding than the stroke is wide puts the border
                // past the plot edge; in y, the value extent's ends, so a bar
                // on the domain top or the baseline overhangs the plot. A
                // facet panel's bars reach its neighbors through
                // `PanelGeometry.ink` (`bar_ink`), counted once there.
                if let (Some(ends), true) =
                    (drawn_ends, facet.is_none() && (drawn_stroke_w > 0.0 || ax_y.authored_continuous_domain().is_some()))
                {
                    let band = make_band(pw);
                    let cols = columns_for(&band, pw);
                    let (x1, x2) = cols.x_extent(&band, &cat_domain, ends);
                    let (lo, hi) = value_extent;
                    b.union(&vega_fit::Bounds::spanning(x1, y_at(hi), x2, y_at(lo)).expand(drawn_stroke_w));
                }
                // A shared-scale bar layer's own stack can reach past the domain
                // the base pins: Vega draws it unclipped and the view grows to hold it.
                for (bl, bl_rows) in bar_layers.iter().zip(&bar_layer_rows_list).filter(|(bl, bl_rows)| !independent_y && bl.y_start.is_none() && !bar_clip(bl, bl_rows)) {
                    // Its baseline stays on the plot (a zoomed floor above 0), so only
                    // a negative stack reaches below the domain.
                    let (lo, hi) = stack_reach(bl_rows, cat_field, &bl.y);
                    let stroke = bl.bar_mark.border.width.unwrap_or(0.0);
                    let (lo, hi) = (lo.min(0.0).max(v_domain_min), hi.max(v_domain_min));
                    b.union(&vega_fit::Bounds::new(pw / 2.0, y_at(hi) - stroke, pw / 2.0, y_at(lo) + stroke));
                }
                // The base's own columns hang from 0, past the plot edge under a zoomed floor.
                if r.y_start.is_none() && v_domain_min > 0.0 && drawn_ends.is_some() {
                    b.union(&vega_fit::Bounds::new(pw / 2.0, y_at(0.0) + stroke_w, pw / 2.0, y_at(0.0) + stroke_w));
                }
                // Value-label ink, at the same spot the draw loop paints
                // (`label_spot`), recomputed per pass with the band and
                // the value scale at this pass's size.
                for LayerLabelFit { style, items, layer } in &layer_label_fit {
                    let band = BandScale::new(cat_domain.clone(), 0.0, pw, band_pi, band_po);
                    let linear = if independent_y {
                        layer_axes[*layer].scale(ph)
                    } else {
                        LinearScale::new(v_domain_min, linear_domain_max, ph, 0.0)
                    };
                    for LayerLabelFitItem { val, width, cat, spot } in items {
                        let Some(band_x) = band.position(cat) else { continue };
                        let x = band_x + band.bandwidth() * spot.edge.unwrap_or(0.5);
                        b.union(&style.ink(x, linear.position(*val).clamp(0.0, ph), *width, spot));
                    }
                }
                // A band-step layer's plateaus run to the plot's own edges, where its
                // stroke adds ink (`boundStroke`).
                for &(at, l, l_rows, plateau, reach) in &step_layers {
                    let band = BandScale::new(cat_domain.clone(), 0.0, pw, band_pi, band_po);
                    let linear = if independent_y {
                        layer_axes[at].scale(ph)
                    } else {
                        LinearScale::new(v_domain_min, linear_domain_max, ph, 0.0)
                    };
                    let (lo, hi) = crate::step_band::band_step_edges(plateau);
                    for row in l_rows {
                        let (Some(band_x), Some(val)) = (band.position(&row_str(row, l.x.as_deref().unwrap_or(cat_field))), row_num(row, &l.y)) else {
                            continue;
                        };
                        let y = linear.position(val).clamp(0.0, ph);
                        let bw = band.bandwidth();
                        b.union(&vega_fit::Bounds::new(band_x + bw * lo, y, band_x + bw * hi, y).expand(reach));
                    }
                }
                // An unclipped area's pile past the plot paints there, so the fit grows to hold it.
                for (l, l_rows) in line_layers.iter().zip(&line_layer_rows).filter(|(l, l_rows)| {
                    l.area_mark.is_some() && !independent_y && !line_clip(l, l_rows)
                }) {
                    let band = make_band(pw);
                    let linear = LinearScale::new(v_domain_min, linear_domain_max, ph, 0.0);
                    for (cat, start, end) in row_order_stack(l_rows.iter(), l.x.as_deref().unwrap_or(cat_field), &l.y, &crate::families::layers::never_nan) {
                        let Some(cx) = band.center(&cat) else { continue };
                        b.union(&vega_fit::Bounds::new(cx, linear.position(end), cx, linear.position(start)));
                    }
                }
                if facet.is_none() && !label_layers.is_empty() {
                    let band = make_band(pw);
                    let linear = LinearScale::new(v_domain_min, linear_domain_max, ph, 0.0);
                    let cols = columns_for(&band, pw);
                    let center = |cat: &str, band_x: f64, slot: Option<usize>| {
                        let cols = cols.of(cat);
                        cols.label_center(band_x, slot.unwrap_or(0))
                    };
                    let geom = LabelGeometry { band: &band, center: &center, linear: &linear };
                    let hide = label_hide_cfg.map(|(span, font_size)| FitHide { plot_h: ph, span, font_size });
                    for layer in &label_layers {
                        layer.ink(&geom, if layer.total { None } else { hide }, b);
                    }
                }
                if let Some(ink) = &strip_ink {
                    b.union(ink);
                }
            };
            if composed.is_some() {
                vega_fit::pad_plot(w, h, &mut ink_pass, composed_title.as_ref())
            } else if suppress_x_axis {
                vega_fit::pad_plot(w, h, &mut ink_pass, title.ink())
            } else {
                vega_fit::fit_plot(view_w, h, (pad_l, pad_r, pad_t, pad_b), &mut ink_pass, title.ink())
            }
        };
        let (mut tick_values, mut val_tick_labels, mut max_val_w, mut val_gutter) =
            (tick_values, val_tick_labels, max_val_w, val_gutter);
        let mut m = fit(&tick_values, &val_tick_labels, &val_label_ws, val_gutter);
        // An unbaked, uncounted value axis takes Vega-Lite's default
        // `tickCount`, `ceil(height/40)`, at the plot's final height
        // (`unbaked_rungs`). Here the rungs' labels size the value gutter,
        // which moves `plot_w`, not `plot_h` — so the count is stable after
        // one pass; the loop only guards a ladder whose label widths
        // change the height through the fit's own rounding.
        for _ in 0..4 {
            let Some((rungs, labels)) = (match date_rungs(m.plot_h)? {
                Some(dated) => Some(dated),
                None => unbaked_rungs(ax_y, normalize, independent_scale && layers.is_empty(), v_domain_min, linear_domain_max, m.plot_h)
                    .map(|rungs| format_tick_labels(&rungs, ax_y, normalize, "y").map(|labels| (rungs, labels)))
                    .transpose()?,
            }) else { break };
            if rungs == tick_values {
                break;
            }
            tick_values = rungs;
            val_tick_labels = labels;
            val_label_ws = measure_labels(&val_tick_labels)?;
            max_val_w = val_label_ws.iter().copied().fold(0.0, f64::max);
            // `val_gutter` stays the SAME whole-dataset estimate in the
            // independent-scale case (see its own initial computation,
            // above) — it never depends on this panel's own re-derived
            // rungs, only on `gutter_of`'s drawn-ladder case does a re-fit
            // ever need to recompute it.
            if date_measure && val_away {
                val_gutter = base_label_pad + max_val_w;
            } else if !(val_on_right && ax_y.tick_values.is_empty()) {
                val_gutter = gutter_of(max_val_w)?;
            }
            m = fit(&tick_values, &val_tick_labels, &val_label_ws, val_gutter);
        }
        if let Some(e) = fit_err.take() {
            return Err(e.into());
        }
        // A layer's stroked marks keep a collapsed plot from reading as blank
        // (`mark_extents.py`: a flat line still has a stroke), so it draws flat.
        let stroked_layer_marks = !line_layers.is_empty() || !scatter_layers.is_empty();
        collapsed_bars(false, (m.plot_w, m.plot_h), stroked_layer_marks, (payload.chart_id()?, payload.width), rows, val_field)?;
        // Autosize-pad: the canvas is the padded plot plus the ink that
        // overhangs it, not the payload's own slot. `pad_plot` ceils each
        // margin; the plot height and an untitled strip (`padding.top`) are
        // summed verbatim (`ViewLayout.js:165-168`, `:198-201`). A bottom strip
        // rides `padding.bottom`, with the axis gap before it.
        let (pad_t, canvas_w, canvas_h, composed_rows) = match composed {
            None => (pad_t, view_w, h, None),
            Some((strip_rows, strip_h, strip_style)) => {
                let bottom_strip = strip_style.position == "bottom";
                let pad_t = if composed_title.is_none() && !bottom_strip { pad_t + strip_h } else { pad_t };
                (
                    pad_t,
                    (pad_l + m.left + m.plot_w + m.right + pad_r).ceil(),
                    pad_t
                        + m.top
                        + m.plot_h
                        + m.bottom
                        + pad_b
                        + if bottom_strip { strip_h + strip_axis_offset } else { 0.0 },
                    Some(strip_rows),
                )
            }
        };
        let legend_pos = legend.map(|lg| {
            let (x, y) = legend_xy.get();
            (pad_l + m.left + x, pad_t + m.top + y, lg)
        });
        PlotRect {
            plot_left: pad_l + m.left,
            plot_top: pad_t + m.top,
            plot_w: m.plot_w,
            plot_h: m.plot_h,
            title_pos: m.title_pos,
            legend: legend_pos,
            rail_x_rel: None,
            canvas_w,
            canvas_h,
            composed_rows,
            facet_axis_gutter: m.left + m.right,
            top_axis_overhang: (-top_overhang.get()).max(0.0),
            facet_axis_right_overhang: m.right,
            raw_right_reach: if val_on_right { m.raw_left } else { m.raw_right },
            val_visible,
            linear_domain_max,
            tick_values,
            val_tick_labels,
            max_val_w,
            val_gutter,
            temporal_labels,
            x_tick_len,
            label_rows,
        }
    };
    let PlotRect {
        plot_left,
        plot_top,
        plot_w,
        plot_h,
        title_pos: vega_title_pos,
        legend: vega_legend,
        rail_x_rel,
        canvas_w,
        canvas_h,
        composed_rows,
        facet_axis_gutter,
        top_axis_overhang,
        facet_axis_right_overhang,
        raw_right_reach,
        val_visible,
        linear_domain_max,
        tick_values,
        val_tick_labels,
        max_val_w,
        val_gutter,
        temporal_labels,
        x_tick_len,
        label_rows,
    } = plot;
    let plot_right = plot_left + plot_w;
    let plot_bottom = plot_top + plot_h;
    // `independent_scale`'s "embedded value axis" effect: a facet panel's
    // own right-side axis reserve/anchor reads `plot_w` alone, never
    // `plot_right`/`value_axis_right_overhang` — both of which stay
    // untouched for the mark ink boxes below (`ink`/`zero_ink`/`bar_ink`),
    // which still span the REAL `[plot_left, plot_right]` plot rect
    // regardless of scale mode. Real Vega embeds the value axis IN this
    // panel's own `cell` under independent resolve, so its own right-side
    // reserve/anchor is `plot_w` alone, the SAME law a single-chart
    // right-axis arm uses — never `plot_right` (`plot_left + plot_w`,
    // `row_footer`'s own left-overhang term) plus `value_axis_right_
    // overhang` (`row_footer`'s own `ceil(cell x2)` reach), both of which
    // only apply to the SEPARATE composited role the shared-scale case
    // paints. Confirmed against `fixtures/multiples/05-independent-scale`'s
    // own reference SVG: the axis group sits at local `x = plot_w + 0.5`
    // inside each `role-scope cell`, never `plot_w + left_mark_overhang +
    // row_footer_overhang + 0.5` the way a rows-only `row_footer` anchors.
    let (facet_axis_right_base, value_axis_right_overhang) =
        if independent_scale { (plot_w, 0.0) } else { (plot_right, value_axis_right_overhang) };

    // ── SVG emission ───────────────────────────────────────────────────────
    // The rail concat is a Vega render like the fit and composed arms — its
    // axis ink carries the same crisp +0.5 group, tick-0 gridline, and
    // flip-aligned label conventions (hero_bookings ref).
    let frame = ChartFrame::new(canvas_w, canvas_h, (pad_l, pad_r, pad_t, pad_b), bg, num_full);
    // What precedes the plot group: the title and the legends.
    //
    // A title the fit placed paints at that anchor; otherwise it hangs from the
    // padding's top edge with its subtitle (`wrap_title_block` only fills a
    // subtitle when the title itself wrapped to a line).
    let mut head: Vec<Node> = title.nodes(vega_title_pos, (plot_left, plot_top));
    if head.is_empty() {
        head.extend(r.title.as_deref().map(|text| crate::common::plain_title_node(text, title_ref, pad_l, pad_t)));
        if !title.block().sub_lines.is_empty() {
            head.extend(crate::common::subtitle_nodes(&title.block().sub_lines, title_ref, pad_l, pad_t, title_fs));
        }
    }
    // A `position: top` strip splices at the plot's own top — below a
    // `legend.position: top` legend, not above it (production anchors the
    // strip band to the plot, never between the title and the legend).

    let linear = LinearScale::new(v_domain_min, linear_domain_max, plot_h, 0.0);
    if crate::labels_expr::view::active() {
        use crate::labels_expr::view::{register_band, register_linear, AxisSpec, Orient, Span};
        register_linear("y", v_domain_min, linear_domain_max, Span::Height);
        match &time_axis {
            Some(axis) => axis.register(plot_w),
            None => register_band("x", &cat_domain, band_pi, band_po, Span::Width)?,
        }
        if facet.is_none() && time.is_none() && !all_dates {
            let (align, baseline) = (crate::labels_expr::view::anchor(&cat_align)?, crate::labels_expr::view::anchor(&cat_baseline)?);
            let spec = AxisSpec { orient: Orient::Bottom, offset: x_tick_len + cat_pad + x_clear.get(), shift: -0.5, align: Some(align), baseline: Some(baseline), angle: cat_angle, flush: None, style: ax_x.labels.item_style()? };
            crate::labels_expr::view::register_axis("x", spec);
        }
    }

    // S10: anchors and cascade bounds are drawn regardless of which arm
    // resolved the plot rect — `rail_concat`'s own gate can refuse the
    // rail for reasons unrelated to it having series to label at all (a
    // legend, a right value axis, `support_table`, wide measures, layers),
    // and line/area's own inline fallback already draws in every such case.
    // Neither term depends on the plot rect: `stacked_rail_anchors` and
    // `tallest_total` are value-space, off the raw stack, not the rendered
    // (niced/normalized) domain.
    if show_rail_v && is_stacked {
        let tallest = tallest_total(&category_totals(rows, cat_field, val_field));
        rail_anchors = stacked_rail_anchors(
            &cat_domain, &stack_order, &data_map, normalize, (r.stack == "center").then_some(tallest),
        )?;
        rail_cascade_max = if normalize { 1.0 } else { tallest };
    } else if let Some(grouped) = grouped_rail.clone() {
        (rail_anchors, (rail_cascade_min, rail_cascade_max)) = grouped;
    }

    let band = make_band(plot_w);

    // Facet-panel ink union: the panel's own local bounds (vega's `bboxFull`
    // quantity, what `families/facet_layout.rs`'s `grid_layout` reads) in this
    // panel's own canvas frame (`x1=0` is the panel's own origin, matching
    // `plot_left`/`plot_right`/`plot_top`/`plot_bottom` verbatim, not
    // re-anchored). Two `boundStroke` contributors beyond the nominal plot
    // rect (vega.js:15959-15965, "expand by the full stroke width" —
    // `docs/laws-shared.md`'s own existing law for this, applied here to
    // row-spacing instead of single-panel margin fit): the zero/threshold
    // rule (`zero_w`, a horizontal line spanning the full plot width,
    // expanded on EVERY side — this is what pushes `x1`/`x2` past
    // `plot_left`/`plot_right`, not any data mark) and the tallest bar's own
    // border stroke (`stroke_w`, only when `style.mark.border.color` is not
    // `"none"`). Scoped to a single-series, zero-anchored bar (this crate's
    // only facet-composing shape today — `families/multiples.rs` refuses
    // anything else before this field is ever read); a grouped/stacked
    // caller would need the real stack top, not one row's own raw value.
    let mut ink = vega_fit::Bounds::new(plot_left, plot_top, plot_right, plot_bottom);
    if facet.is_some_and(|f| f.cell_x_axis) {
        let a = x_axis_ink.get();
        ink.union(&vega_fit::Bounds::new(plot_left + a.x1, plot_top + a.y1, plot_left + a.x2, plot_top + a.y2));
    }
    // A facet panel's own value-axis gutter is real ink too, under
    // `autosize: pad`: `pad_plot` never shrinks `plot_w`/`plot_h` to make
    // room for it the way the single-chart wrapper's `fit_plot` does, so the
    // gutter instead sits OUTSIDE `[plot_left, plot_right]` — exactly the
    // left/right overhang `pad_plot` already measured (`plot_left - pad_l`
    // recovers `m.left`; its right-side twin survives past the fit arm's own `if`
    // as `PlotRect::facet_axis_right_overhang`; the mirror ghost's OWN extra
    // width is reserved once at the composer, on top of this per-panel fold,
    // since the ghost paints outside any panel's own canvas). Vertical margins (`m.top`/`m.bottom`)
    // are NOT unioned in
    // here: real Vega's row-facet Y-axis is `row_footer`, a role
    // structurally separate from `cell` that never contributes to a row's
    // own row-spacing ink (only the row's own bars/gridlines do) —
    // unioning them would double-count a top tick label's own overhang
    // into every row's own height. Gated on `fold_axis_gutter`, not just
    // `suppress_x_axis` — see that param's own doc comment for the full
    // derivation of why a multi-column row must NOT fold this into any
    // one column's own ink (`grid_layout`'s `align: "all"` law would
    // propagate the fold to every OTHER column's own slot too).
    if suppress_x_axis && fold_axis_gutter {
        ink.union(&vega_fit::Bounds::new(
            pad_l,
            plot_top,
            facet_axis_right_base + value_axis_right_overhang + facet_axis_right_overhang,
            plot_bottom,
        ));
    }
    if let Some(rules) = rules {
        if rules.zero {
            let zero_y = plot_top + linear.position(0.0).clamp(0.0, plot_h);
            ink.union(&vega_fit::Bounds::new(plot_left, zero_y, plot_right, zero_y).expand(rules.width));
        }
        if rules.unity && facet.is_some() {
            let unity_y = plot_top + linear.position(1.0);
            ink.union(&vega_fit::Bounds::new(plot_left, unity_y, plot_right, unity_y).expand(rules.width));
        }
    }
    if stroke_color != "none" && stroke_w > 0.0 {
        let (min_val, max_val) = value_extent;
        let bar_top = plot_top + linear.position(max_val);
        let bar_bottom = plot_top + linear.position(min_val);
        // Vega's real mark bounds are the RECT's own box, stroke-expanded
        // in every direction (`vega.js:15959-15965`, `boundStroke`) —
        // narrower in x than `[plot_left, plot_right]` by each outer
        // bar's own band padding (`BandColumns::pad`, the SAME term the
        // paint loop below uses). Unioned, never substituted, against
        // `ink`'s own `[plot_left, plot_right]` seed above: a normal inset
        // band (padding at least as wide as the stroke) is a no-op here,
        // and only a full-width band (`mark.band_width` near `1`, the pad
        // under `stroke_w`) widens the canvas by the uncovered stroke
        // reach — the one shape whose real Vega bound actually pushes
        // past the plot edge.
        if let Some(ends) = drawn_ends {
            let cols = columns_for(&band, plot_w);
            let (rect_x1, rect_x2) = cols.x_extent(&band, &cat_domain, ends);
            let bar_ink = vega_fit::Bounds::spanning(
                plot_left + rect_x1,
                bar_top,
                plot_left + rect_x2,
                bar_bottom,
            )
            .expand(stroke_w);
            ink.union(&bar_ink);
        }
    }

    let swatch_stroke = r.style.mark.border.color.as_deref().unwrap_or("none");
    let facet_swatch = if facet.is_some_and(|f| f.legend_swatch_stroke_is_fill) {
        LegendSymbol::SquareInFill
    } else {
        LegendSymbol::Square(swatch_stroke)
    };
    let mut facet_legend: Option<facet_compose::FacetLegend> = None;
    if legend_top || (legend_shown && facet.is_some()) {
        match &vega_legend {
            Some((_, _, lg)) if facet.is_some() => {
                let mut nodes: Vec<scene::Node> = drawn_symbol_legend(&legend_entries_typed, lg, &r.legend, facet_swatch, 0.0, 0.0, stamp_legend_series).into_iter().collect();
                nodes.extend(numeric_legends.nodes_after(lg, &r.legend, fonts, (plot_w, plot_h), (0.0, 0.0))?);
                facet_legend = Some(facet_compose::FacetLegend { nodes, box_w: lg.box_w, box_h: lg.box_h() });
            }
            Some((lx, ly, lg)) => {
                let (dx, dy) = legend_slide(&r.id, &r.legend.position, lg, (plot_w, plot_h))?;
                head.extend(drawn_symbol_legend(&legend_entries_typed, lg, &r.legend, LegendSymbol::Square(swatch_stroke), *lx + dx, *ly + dy, stamp_legend_series));
                head.extend(numeric_legends.nodes_after(lg, &r.legend, fonts, (plot_w, plot_h), (*lx + dx, *ly + dy))?);
            }
            None => return Err("bar: a shown legend reached an arm that lays none out".into()),
        }
    }

    // Byte offset within the body where the title's (and, for
    // `legend.position: top`, the legend's) own markup ends — see
    // `support_table_strip::wrap_with_strip`'s doc comment for why a titled
    // `position: top` strip splices here instead of before everything. Taken
    // *after* the top-legend emission (it must stay in the unshifted `before`
    // slice alongside the title, since the strip goes between the legend and
    // the plot) but *before* the right-edge legend: that legend labels the
    // plot itself and shifts down with it when the strip pushes plot_top,
    // exactly as line.rs's own right-legend emission does after its split.

    if legend_shown && !legend_top && facet.is_none() {
        match &vega_legend {
            Some((lx, ly, lg)) => {
                let (dx, dy) = legend_slide(&r.id, &r.legend.position, lg, (plot_w, plot_h))?;
                head.extend(drawn_symbol_legend(&legend_entries_typed, lg, &r.legend, LegendSymbol::Square(swatch_stroke), *lx + dx, *ly + dy, stamp_legend_series));
                head.extend(numeric_legends.nodes_after(lg, &r.legend, fonts, (plot_w, plot_h), (*lx + dx, *ly + dy))?);
            }
            None => return Err("bar: a shown legend reached an arm that lays none out".into()),
        }
    }

    let mut plot: Vec<Node> = Vec::new();
    let defined_clips: RefCell<HashSet<String>> = RefCell::new(HashSet::new());

    // Gridlines: horizontal lines at value ticks. Under the vega-fit path
    // the grid axis group carries Vega's +0.5 crispness translate in both
    // coordinates; the tick-0 gridline draws too (under the zero rule).
    let grid = &ax_y.grid;
    let gd = 0.5;
    let tear = crate::torn::ValueTear::of(
        grid.torn_baseline.as_ref().filter(|_| ranged.as_ref().is_some_and(|rb| !rb.is_date())),
        v_domain_min,
        linear_domain_max,
    );
    if grid.visible.unwrap_or(false) {
        let gc = crate::common::required_ink(grid.color.as_deref(), "bar", "grid.color")?;
        let gw = grid.width.unwrap_or(1.0);
        let mut lines: Vec<Node> = Vec::new();
        for &tv in &tick_values {
            if tear.as_ref().is_some_and(|t| t.edge == tv) {
                continue;
            }
            // Vega's tickRound (default true) rounds the grid rule's scale
            // position to a whole pixel; label positions stay unrounded
            // (hero_bookings ref: rule at 241 where the label sits at
            // 240.75 + baseline).
            let y = linear.position(tv).round() + gd;
            if y < -0.5 || y > plot_h + 1.0 {
                continue;
            }
            lines.push(Node::Line(
                Attrs::new()
                    .set("x1", format!("{gd:.1}"))
                    .set("y1", format!("{y:.2}"))
                    .set("x2", format!("{:.2}", plot_w + gd))
                    .set("y2", format!("{y:.2}"))
                    .set("stroke", gc)
                    .set("stroke-width", gw.to_string()),
            ));
        }
        plot.push(crate::contract::grid_group(lines));
    }

    if let Some(t) = &tear {
        let gc = crate::common::required_ink(grid.color.as_deref(), "bar", "grid.color")?;
        plot.push(t.node(linear.position(t.edge), plot_h, plot_w, false, gc, grid.width.unwrap_or(1.0)));
    }

    // Dual axis: each layer's own independent scale draws its OWN grid —
    // production's compiled scenegraph carries one `role-axis-grid` group per
    // axis (probed: `layered-charts_0`'s reference), all painted BEFORE any
    // mark, same as the base's. Same theme grid style (color/width — the
    // layer's `axis_y.grid` is unauthored on every probed row, inheriting the
    // base's), different domain, so the rungs land at different y than the
    // base's own.
    let layer_linear: Vec<LinearScale> = layer_axes.iter().map(|la| la.scale(plot_h)).collect();
    for (la, ll) in layer_axes.iter().zip(&layer_linear) {
        if la.grid_visible {
            let gc = crate::common::required_ink(grid.color.as_deref(), "bar", "grid.color")?;
            let gw = grid.width.unwrap_or(1.0);
            let mut lines: Vec<Node> = Vec::new();
            for &tv in &la.tick_values {
                let y = ll.position(tv).round() + gd;
                if y < -0.5 || y > plot_h + 1.0 {
                    continue;
                }
                lines.push(Node::Line(
                    Attrs::new()
                        .set("x1", format!("{gd:.1}"))
                        .set("y1", format!("{y:.2}"))
                        .set("x2", format!("{:.2}", plot_w + gd))
                        .set("y2", format!("{y:.2}"))
                        .set("stroke", gc)
                        .set("stroke-width", gw.to_string()),
                ));
            }
            plot.push(crate::contract::grid_group(lines));
        }
    }

    // Annotations paint in the plot group's own frame. The rail arm reserves
    // room for text past the right edge; no arm reserves the other edges.
    let annotations = annotation_ctx((0.0, 0.0), plot_w, plot_h);
    if shape != Shape::Fit {
        let reserved = crate::annotation::Reserved { top: false, right: shape == Shape::RailConcat };
        crate::annotation::refuse_margin_ink(&annotations, reserved)?;
    }
    plot.extend(crate::annotation::nodes(&annotations, crate::annotation::Plane::Background)?);

    // Structured-tooltip content for the paint loop below (see the module
    // doc above `tooltip_header_text`); `tooltip_labels`/`order_domain`/
    // `stamp_legend_series` are computed earlier, ahead of the legend.
    // `None` on every datum paints the pre-existing unlabeled status quo.
    let value_title = val_title_text.as_str();
    let tooltip_fmt = &r.style.tooltip_format;
    let native = facet
        .map(|_| -> Result<_, String> {
            let series = match color_field {
                Some(cf) => crate::families::legend::color_channel_title(cf, &r.legend, &r.field_labels)?,
                None => String::new(),
            };
            Ok(NativeTitles { x: cat_title_text.clone(), series })
        })
        .transpose()?;
    // A scatter-layer combo's own single-series base has no real color
    // channel to promote into a "series" tooltip row -- production
    // synthesizes one from the base's own y-title so the base and the
    // layer's matched rows don't collide on the SAME (header, series)
    // dedup key `chart_interactivity.js` groups by
    // (`structured_tooltip.py`'s `if chart.layers and not series:
    // series = (TooltipField(base_label, base_label, literal=True),)` --
    // `base_label` is the SAME title `value_title` already carries).
    // Combo-only, single-series-base-only -- `validate_scatter_layer`
    // already refused a color-grouped base, which would carry a real
    // series row of its own.
    let combo_series = (!layers.is_empty()).then_some(value_title);
    let has_negative = rows.iter().any(|row| row_num(row, val_field).is_some_and(|v| v < 0.0));
    let center = r.stack == "center";
    // What the bar loops below add to each row's label: its description and
    // inherited fill.
    let mut label_decors = LabelDecors::new();
    // Layers over a ranged bar share its rows, so bar and layers all carry the
    // one combined block (`_span_values`); a layer with its own query leaves
    // the bar its own span label and the layer none.
    let shares_rows = |query: &Option<String>| query.is_none() || *query == r.query_name;
    let layers_share_rows = !layers.is_empty() && layers.iter().all(|l| shares_rows(&l.query_name().map(str::to_string)));
    let span_block = match &ranged {
        Some(rb) if tooltip_labels && layers_share_rows => {
            let block_layers: Vec<(&OverlayLayer, &LayerClaim)> = layers.iter().zip(&claims).collect();
            let (named, scatter_swatch) = layer_block_rows(&block_layers, &r.style.single_series_fill);
            let inner = bar_layers
                .iter()
                .find_map(|l| {
                    let start = l.y_start.as_ref()?;
                    Some((|| {
                        Ok::<_, String>(span::Ends {
                            end: l.y.clone(),
                            end_title: display_text::field_title(&l.y, &r.field_labels)?,
                            start: start.clone(),
                            start_title: display_text::field_title(start, &r.field_labels)?,
                        })
                    })())
                })
                .transpose()?;
            let base = span::Ends {
                end: val_field.clone(),
                end_title: value_title.to_string(),
                start: r.y_start.clone().expect("a ranged bar has a start column"),
                start_title: rb.start_title.clone(),
            };
            Some(span::SpanBlock::new(rows, &base, named, inner.as_ref(), scatter_swatch.as_deref(), date_axis.is_some()))
        }
        _ => None,
    };
    // An ink wick has no color of its own; the series row is borrowed from
    // the body layer's color split, keyed to that layer's paint
    // (`_layer_series`).
    let layer_series = if color_field.is_none() && span_block.is_some() {
        layer_series_swatches(layers.iter().zip(&layer_rows).zip(&claims).map(|((layer, rows), claim)| (layer, *rows, claim)), &layer_scale, cat_field)
    } else {
        HashMap::new()
    };
    let block_aria = |datum: &serde_json::Map<String, Value>, series: Option<&str>, layer_bar: bool| -> Result<Option<String>, String> {
        let block = span_block.as_ref().expect("callers check the block");
        let header = crate::contract::tooltip_header_text(
            &header_date(super::time_x::tooltip_date(datum, cat_field)),
            tooltip_tu,
            x_is_standard_date,
            grain_header_format,
        )?;
        if layer_bar {
            block.layer_bar_label(&header, series, datum, tooltip_fmt).map(Some)
        } else {
            block.label(&header, series, datum, tooltip_fmt).map(Some)
        }
    };
    // The bar layers a ranged bar splits into (`direction_layers`): the zero
    // rule and the value labels number after them.
    let mut bar_layers_painted = 1usize;

    // A genuine zero's label layer makes a time-axis spec layered, which drops the band.
    let mut hover_band =
        if time.is_some() && zero_text.is_some() { None } else { hover_band_for(r, rows, facet.is_some()) };
    // `(label, x, y, stroke)` of each datum the mirror's ghost rule layer carries.
    let ghosts_on = facet.is_some_and(|f| f.has_mirror) || mirror_active;
    let mut ghost_points: Vec<(Option<String>, f64, f64, String, Option<String>)> = Vec::new();
    let ghost_ink = if ghosts_on {
        r.style.axis_y.labels.font.color.clone().ok_or("bar: axis_y.labels.font.color absent (theme required)")?
    } else {
        String::new()
    };

    // Columns (bars growing upward from baseline)
    let cols = columns_for(&band, plot_w);
    let continuous_time = time.as_ref().is_some_and(|t| !t.is_discrete());
    let double_stroke = time.as_ref().map_or(true, TimeX::double_stroke);
    let clip_ns = time.as_ref().filter(|t| t.double_stroke()).map(|_| crate::contract::safe_svg_id(&r.id));
    // The base bars, which annotation text tests for its halo.
    let mut under = Vec::new();
    if is_stacked {
        for cat in &cat_domain {
            let Some(band_x) = band.position(cat) else { continue };
            let columns = cols.of(cat);
            let col_x = columns.left(band_x, 0);
            // A time axis's stack is one clipped group: its segments stroke once and the group's outline strokes
            // the whole, which only the top end rounds.
            let mut stack_ends: Option<(f64, f64)> = None;
            let total = if normalize {
                let Some(&t) = category_totals_all.get(cat) else { continue };
                t
            } else {
                0.0 // unused when !normalize
            };
            let outers = outer_segments(&stack_paint_order, cat, &data_map);
            for series in layered_by_sign(&stack_paint_order, cat, &data_map) {
                let stored = data_map.get(&(cat.clone(), series.clone())).copied();
                let raw_val = stored.unwrap_or(0.0);
                let aria = if stored.is_some() {
                    let total_row = match (&raw_x, has_negative || center) {
                        (_, true) => None,
                        (Some(x), false) => Some(x.total(cat, series)?),
                        (None, false) => Some(stack_group_total(&category_totals_all, cat)?),
                    };
                    bar_datum_aria(
                        tooltip_labels, &tip(cat, series), tooltip_tu, x_is_standard_date, grain_header_format, series_promoted.then(|| series_text(base_series(series))).as_deref(),
                        order_domain.as_deref().filter(|_| series_promoted).map(|d| crate::contract::tooltip_order_rank(d, base_series(series))),
                        value_title, tooltip_fmt, raw_val,
                        (normalize && !has_negative).then_some(raw_val / total), total_row, extras.of(&(cat.clone(), series.clone()))?, native.as_ref(),
                    )?
                } else {
                    None
                };
                let key = (cat.clone(), series.clone());
                // A row Vega has no datum for draws nothing; a zero row still
                // draws its (zero-height) bar and keeps its label, at the stack
                // as it stands (`y0 == y1 == the running sum`).
                if stored.is_none() {
                    // A tier no row reaches is no cell.
                    if is_tier(series) {
                        continue;
                    }
                    // A stack over a calendar x is imputed to every (bucket, series)
                    // pair, which the hover band draws a bar for; a nominal x is not.
                    if let (Some(band), true) = (hover_band.as_mut(), detected_data_tu.is_some()) {
                        let imputed = imputed_datum_aria(
                            tooltip_labels, cat, tooltip_tu, x_is_standard_date, grain_header_format, &series_text(series),
                            order_domain.as_deref().map(|d| crate::contract::tooltip_order_rank(d, series)),
                        )?;
                        band.push(cat, series, imputed.as_deref(), None, col_x, columns.width, None);
                    }
                    continue;
                }
                if let Some(band) = hover_band.as_mut() {
                    band.push(cat, series, aria.as_deref(), hrefs.get(&key).map(String::as_str), col_x, columns.width, Some(raw_val));
                }
                let SegmentSpan { start: cum_before, end: cum_after, .. } = spans[&key];
                label_decors.insert(key.clone(), LabelDecor { aria: aria.clone(), fill: None });
                if ghosts_on {
                    let fill = crate::common::series_color(base_series(series), palette_domain, &r.series_palette, category_scale)?;
                    ghost_points.push((aria.clone(), col_x + columns.width / 2.0, linear.position(cum_after), fill, hrefs.get(&key).cloned()));
                }
                // linear.position maps value → y, decreasing monotonically
                // with value regardless of sign — the smaller value's
                // position is always the larger (bottom) y.
                let y1 = linear.position(cum_before);
                let y2 = linear.position(cum_after);
                let (top_y, bottom_y) = if y1 <= y2 { (y1, y2) } else { (y2, y1) };
                let seg_h = bottom_y - top_y;
                let fill = crate::common::series_color(base_series(series), palette_domain, &r.series_palette, category_scale)?;
                // Corner radius flips with sign (Vega's mixed-sign layer
                // split — see `bar_passes`'s doc comment): a positive
                // segment rounds its top (far) end, a negative one its
                // bottom.
                // A time axis's stack is one clipped group: only its top segment keeps the rounded end.
                let segment_r = if (if raw_val < 0.0 { outers.1 } else { outers.0 }) == Some(series) { corner_r } else { 0.0 };
                stack_ends = Some(stack_ends.map_or((top_y, bottom_y), |(top, bottom)| (top.min(top_y), bottom.max(bottom_y))));
                under.push(crate::annotation::DataMark::Tinted(Box::new(crate::annotation::DataMark::rect(col_x, top_y, columns.width, seg_h)), fill.to_string(), r.style.mark.opacity.unwrap_or(1.0)));
                plot.extend(bar_passes(false, col_x, top_y, columns.width, seg_h, &fill, r.style.mark.opacity, stroke_color, stroke_w,
                    segment_r,
                    Rounded::by_sign(raw_val), false, clip_ns.as_deref(), aria.as_deref(), anchor_of(&key))?);
            }
            if let (Some((top, bottom)), true) = (stack_ends, continuous_time && stroke_color != "none" && stroke_w > 0.0) {
                let (r, x2) = (crate::common::bar_corner_radius(corner_r, columns.width, bottom - top), col_x + columns.width);
                plot.push(Node::Path(
                    Attrs::new()
                        .set(
                            "d",
                            format!(
                                "M {col_x:.2},{bottom:.2} V {:.2} A {r:.2},{r:.2} 0 0,1 {:.2},{top:.2} H {:.2} A {r:.2},{r:.2} 0 0,1 {x2:.2},{:.2} V {bottom:.2} Z",
                                top + r, col_x + r, x2 - r, top + r
                            ),
                        )
                        .set("fill", "none")
                        .set("stroke", stroke_color)
                        .set("stroke-width", stroke_w.to_string()),
                ));
            }
        }
    } else if is_grouped {
        // 1:1 color: one full-band bar per category (`one_to_one`) at the
        // theme's band_width. Otherwise `width: {band: 1 + f}` from
        // production's overlap fraction (an overlapping band > 1 overflows
        // the sub-band), offset by `(1 − band)/2` of the CATEGORY scale's
        // bandwidth — Vega-Lite's band offset reads the outer scale.

        let (painted, layers) = painted_grouped.as_ref().expect("a grouped bar's painted series come with its spans");
        bar_layers_painted = *layers;
        for (si, series, cat) in painted {
            let fill = crate::common::series_color(base_series(series), &color_domain, &r.series_palette, category_scale)?;
            let Some(band_x) = band.position(cat) else { continue };
            let columns = cols.of(cat);
            let col_x = columns.left(band_x, *si);
            let key = (cat.clone(), series.clone());
            let Some(&val) = data_map.get(&key) else { continue };
            let start = ranged.as_ref().map(|rb| rb.starts.get(&key).copied());
            // A band with no row for this series has no bar (`build_data_map`
            // reads no start there either).
            let (bar_top, bar_h, ends) = match (start, spans.get(&key)) {
                (Some(None), _) => continue,
                (Some(Some(start)), _) => ranged_extent(|v| linear.position(v), val, Some(start)),
                // A row stacked over its cell's earlier ones spans from where they end.
                (None, Some(span)) if is_tier(series) => ranged_extent(|v| linear.position(v), span.end, Some(span.start)),
                (None, _) => ranged_extent(|v| linear.position(v), val, None),
            };
            let order = order_domain.as_deref().filter(|_| series_promoted).map(|d| crate::contract::tooltip_order_rank(d, base_series(series)));
            let plain = || {
                bar_datum_aria(
                    tooltip_labels, &tip(cat, series), tooltip_tu, x_is_standard_date, grain_header_format, series_promoted.then(|| series_text(base_series(series))).as_deref(), order,
                    value_title, tooltip_fmt, val, None, None, extras.of(&key)?, native.as_ref(),
                )
            };
            let aria = match (&ranged, start) {
                (Some(_), Some(Some(_))) if span_block.is_some() => {
                    let datum = rows
                        .iter()
                        .find(|row| row_str(row, cat_field) == *cat && row_str(row, color_field.expect("a series bar has a color field")) == *series)
                        .expect("a painted bar has its row");
                    block_aria(datum, Some(series_text(series).as_str()), false)?
                }
                (Some(rb), Some(Some(start))) => rb.aria(
                    tooltip_labels, &tip(cat, series), tooltip_tu, x_is_standard_date, grain_header_format, Some(series_text(series).as_str()), order,
                    (value_title, tooltip_fmt), (start, val), plain,
                )?,
                _ => plain()?,
            };
            label_decors.insert(key.clone(), LabelDecor { aria: aria.clone(), fill: Some(fill.clone()) });
            if let Some(band) = hover_band.as_mut() {
                band.push(cat, series, aria.as_deref(), hrefs.get(&key).map(String::as_str), col_x, columns.width, Some(val));
            }
            if ghosts_on {
                ghost_points.push((aria.clone(), col_x + columns.width / 2.0, bar_top, fill.clone(), hrefs.get(&key).cloned()));
            }
            under.push(crate::annotation::DataMark::Tinted(Box::new(crate::annotation::DataMark::rect(col_x, bar_top, columns.width, bar_h)), fill.to_string(), r.style.mark.opacity.unwrap_or(1.0)));
            plot.extend(bar_passes(false, col_x, bar_top, columns.width, bar_h, &fill, r.style.mark.opacity, stroke_color, stroke_w,
                corner_r,
                ends, double_stroke, clip_ns.as_deref(), aria.as_deref(), anchor_of(&key))?);
        }
    } else {
        let fill = &r.style.single_series_fill;
        let painted: Vec<&String> = match &ranged {
            None => cat_domain.iter().collect(),
            Some(_) if date_axis.is_some() => cat_domain.iter().collect(),
            Some(rb) => {
                let rises = |cat: &&String| {
                    let key = ((*cat).clone(), String::new());
                    rb.starts.get(&key).zip(data_map.get(&key)).is_some_and(|(start, end)| end >= start)
                };
                let (ordered, layers) = direction_layers(cat_domain.iter().collect(), rises, r.style.mark.border.radius);
                bar_layers_painted = layers;
                ordered
            }
        };
        let single_depth = tier_depth.get("").copied().unwrap_or(0);
        for (cat, key) in painted.into_iter().flat_map(|cat| (0..=single_depth).map(move |tier| (cat, (cat.clone(), tier_name("", tier))))) {
            let Some(band_x) = band.position(cat) else { continue };
            let columns = cols.of(cat);
            let col_x = columns.left(band_x, 0);
            // A category with no row (a synthesized calendar bucket) has no datum.
            let Some(&val) = data_map.get(&key) else { continue };
            let start = ranged.as_ref().and_then(|rb| rb.starts.get(&key).copied());
            let (bar_top, bar_h, ends) = match spans.get(&key) {
                Some(span) => {
                    let (top, h, _) = ranged_extent(|v| linear.position(v), span.end, Some(span.start));
                    (top, h, Rounded::by_sign(val))
                }
                // A date span has no sign: both ends round.
                None => match ranged_extent(|v| linear.position(v), val, start) {
                    (top, h, _) if date_axis.is_some() && start.is_some() => (top, h, Rounded::Both),
                    extent => extent,
                },
            };
            let plain = || {
                bar_datum_aria(
                    tooltip_labels, &tip(cat, &key.1), tooltip_tu, x_is_standard_date, grain_header_format, combo_series, None, value_title, tooltip_fmt, val, None, None, extras.of(&key)?, native.as_ref(),
                )
            };
            let aria = match (&ranged, start) {
                (Some(_), Some(_)) if span_block.is_some() => {
                    let datum = rows.iter().find(|row| row_str(row, cat_field) == *cat).expect("a painted bar has its row");
                    block_aria(datum, layer_series.get(cat).map(String::as_str), false)?
                }
                (Some(rb), Some(start)) => rb.aria(
                    tooltip_labels, &tip(cat, &key.1), tooltip_tu, x_is_standard_date, grain_header_format, None, None,
                    (value_title, tooltip_fmt), (start, val), plain,
                )?,
                _ => plain()?,
            };
            if ranged.is_some() {
                label_decors.insert(key.clone(), LabelDecor { aria: aria.clone(), fill: None });
            }
            if let Some(band) = hover_band.as_mut() {
                band.push(cat, "", aria.as_deref(), hrefs.get(&key).map(String::as_str), col_x, columns.width, Some(val));
            }
            if ghosts_on {
                ghost_points.push((aria.clone(), col_x + columns.width / 2.0, bar_top, ghost_ink.clone(), hrefs.get(&key).cloned()));
            }
            under.push(crate::annotation::DataMark::Tinted(Box::new(crate::annotation::DataMark::rect(col_x, bar_top, columns.width, bar_h)), fill.to_string(), r.style.mark.opacity.unwrap_or(1.0)));
            plot.extend(bar_passes(false, col_x, bar_top, columns.width, bar_h, fill, r.style.mark.opacity, stroke_color, stroke_w, corner_r,
                ends, double_stroke, clip_ns.as_deref(), aria.as_deref(), anchor_of(&key))?);
        }
    }

    // An overlay layer paints on the base's own `band` x scale but its OWN
    // y scale (`scale` param): the base's `linear` for a shared-scale
    // layer, or the layer's own independent `layer_linear` for a dual-axis
    // one. Halo is always suppressed for an overlay line layer
    // (`suppress_halo=True`, `_layers.py`) — a layer over a DIFFERENT base
    // series must not paint a knockout mask over it.
    // Production gives this layer no `x`/`xOffset`/`data` override of its
    // own (`_overlay.py`'s shared-scale template), so Vega draws it
    // straight through every ROW in order — never deduplicated to one
    // point per category. On a single-series base that's the same thing
    // (one row per category); on a grouped base it draws a coincident pair
    // per category (both sub-series rows carry the same layer value at the
    // outer band's own center, probed: `grouped-bar-with-layer`'s compiled
    // `layer[1]` has no `data` key at all). `rows` is the base's own rows
    // for a same-query layer, its own dataset for a cross-query one, and
    // one color value's own subset for a color-grouped layer.
    // The layer's own description of a datum (`_layer_tooltip_description`):
    // the base's header, the layer's name as the series row (prefixed onto the
    // color value when the layer splits by color), and one value row under that
    // name in the layer's own tooltip format.
    let describe_layer = |axis: &OverlayAxisY,
                          x_field: &str,
                          name: &str,
                          color_value: Option<&str>,
                          row: &serde_json::Map<String, Value>,
                          val: f64| {
        let series = layer_series_text(name, color_value);
        bar_datum_aria(
            tooltip_labels, &header_date(super::time_x::tooltip_date(row, x_field)), tooltip_tu, x_is_standard_date,
            grain_header_format, Some(series.as_str()), None, name, axis.tooltip_format_or(tooltip_fmt),
            val, None, None, &[], None,
        )
    };
    let line_layer_promoted: Vec<bool> = line_layers
        .iter()
        .zip(&line_layer_rows)
        .map(|(l, l_rows)| l.color.as_deref().is_some_and(|cf| layer_color_promoted(l_rows, cf)))
        .collect();
    let describe_layer_row = |line: usize, row: &serde_json::Map<String, Value>, val: f64| {
        let l = line_layers[line];
        if span_block.is_some() {
            let cat = row_str(row, l.x.as_deref().unwrap_or(cat_field));
            return block_aria(row, layer_series.get(&cat).map(String::as_str), false);
        }
        let color_value = l.color.as_deref().filter(|_| line_layer_promoted[line]).map(|cf| layer_color_text(row, cf));
        describe_layer(&l.axis_y, l.x.as_deref().unwrap_or(cat_field), &line_claims[line].label, color_value.as_deref(), row, val)
    };
    let paint_layer_marks = |line: usize,
                              scale: &LinearScale,
                              rows: &[serde_json::Map<String, Value>],
                              series_color: &str,
                              stacked: Option<&LayerStack>|
     -> Result<Vec<Node>, String> {
        let l = line_layers[line];
        // Past its own authored domain a layer's marks keep their position: cut off or overrunning, never flattened.
        let (own_floor, plot_top_edge) = if independent_y && layer_axes[line_layer_at[line]].cut != DomainCut::Within {
            (f64::NEG_INFINITY, f64::INFINITY)
        } else {
            (0.0, plot_h)
        };
        crate::families::layers::line_layer_marks(
            l,
            rows,
            |row| band.center(&row_str(row, l.x.as_deref().unwrap_or(cat_field))),
            None,
            |val| scale.position(val).clamp(own_floor, plot_top_edge),
            &AreaFrame {
                free_y: &|val| scale.position(val),
                plot: (plot_w, plot_h),
                // A layer on its own scale clips as a whole group (`paint_layer`'s own-scale wrap).
                clip: !independent_y
                    && (v_domain_min > 0.0 || (normalize && l.leaves_domain(line_layer_rows[line], ax_y.authored_continuous_domain().unwrap_or((0.0, 1.0))))),
            },
            false,
            series_color,
            r.canvas.as_str(),
            |row, val| describe_layer_row(line, row, val),
            payload.board_style.as_ref(),
            |row, val| {
                let name = line_claims[line].label.as_str();
                Ok(tooltip_labels.then(|| {
                    crate::contract::native_label(&[
                        (l.x.as_deref().unwrap_or(cat_title_text.as_str()), row_str(row, l.x.as_deref().unwrap_or(cat_field))),
                        (name, val.to_string()),
                    ])
                }))
            },
            line_band_step[line].then(|| step_band_of(&band, &cat_domain)),
            if step_over_grouped { color_domain.len() as f64 / (color_domain.len() as f64 + 1.0) } else { 0.0 },
            stacked,
crate::families::layers::layer_link(link.as_ref(), (l.query_name.as_deref(), r.query_name.as_deref())),
crate::families::layers::LabelCrowd { chart_id: &r.id, layer: line_layer_at[line], fonts },
)
        .map_err(|e| format!("bar: {e}"))
    };

    // The line layer's marks: one line (+ points) per color value, each in the
    // color it claimed on the shared scale; an authored stroke is pinned on every
    // grouped line and an authored point color on every point (`pin_child_colors`).
    let paint_layer_series = |line: usize, scale: &LinearScale| -> Result<Vec<Node>, String> {
        let (l, l_rows) = (line_layers[line], line_layer_rows[line]);
        match l.color.as_deref() {
            Some(cf) => {
                let stack = l
                    .area_mark
                    .is_some()
                    .then(|| LayerStack::descending(l_rows, l.x.as_deref().unwrap_or(cat_field), cf, &l.y).imputing(l_rows, crate::families::layers::layer_link(link.as_ref(), (l.query_name.as_deref(), r.query_name.as_deref()))))
                    .transpose()
                    .map_err(|e| format!("bar: {e}"))?;
                let mut series_nodes = Vec::new();
                for (value, rows_for_value) in layer_color_groups(l_rows, cf) {
                    let slot = line_claims[line].group_color(&layer_scale, cf, value.as_deref(), &rows_for_value);
                    series_nodes.extend(paint_layer_marks(line, scale, &rows_for_value, &slot, stack.as_ref())?);
                }
                Ok(series_nodes)
            }
            None => {
                let fill = line_claims[line].fill.as_deref().expect("a colorless line layer claimed its fill");
                paint_layer_marks(line, scale, l_rows, fill, None)
            }
        }
    };

    // A bar-type layer paints its own bars on the SAME band scale (the
    // union domain built above), at its own palette slot — `emit_bar_layer`
    // (`_overlay.py`), reusing the base's own single-series bar formula
    // (`x_is_banded` width/pad law is identical; `_fix_bar_band_width`
    // rewrites the degraded `{"band": f}` shorthand to an explicit
    // `bandwidth('x')` expression under a layered spec, the SAME numeric
    // result this port's own band-width formula already computes) since
    // `validate_bar_layer` refuses a grouped/color-split base — no xOffset
    // scale exists for this shape.
    // A `type: "bar"` layer sharing the base's own `color` field over a
    // `stack: "zero"` base paints its own fresh stacked segments — see
    // `validate_bar_layer_over_stacked_base`'s doc comment. Same band
    // position as the base (SAME `cat_domain`/`band`), same shared palette
    // (`series_color` against the base's own `palette_domain`/
    // `category_scale`, never a new slot), so a category the layer has no
    // rows for draws nothing (a series with no entry in `layer_data_map`
    // is skipped, an authored zero is drawn). Painted at the layer's OWN `bar_mark` (band width,
    // border, corner radius) — `emit_bar_layer` (`_overlay.py`) is called
    // with `bar_mark=layer.bar_mark`, never the base's — and its own value
    // labels, when authored, same as any other bar-type layer.
    // Each bar layer paints the color it claimed on the shared scale beside the legend.
    let bar_layer_aria = |bl: &OverlayBarLayer,
                          row: &serde_json::Map<String, Value>,
                          val: f64,
                          color_promoted: bool|
     -> Result<Option<String>, String> {
        if span_block.is_some() {
            let cat = row_str(row, bl.x.as_deref().unwrap_or(cat_field));
            return block_aria(row, layer_series.get(&cat).map(String::as_str), false);
        }
        let name = crate::common::axis_title_text(&bl.y, bl.label.as_deref(), &r.field_labels)?;
        let color_value = bl.color.as_deref().filter(|_| color_promoted).map(|cf| layer_color_text(row, cf));
        describe_layer(&bl.axis_y, bl.x.as_deref().unwrap_or(cat_field), &name, color_value.as_deref(), row, val)
    };
    // A bar layer's OWN value labels (`_build_layer_label_specs` →
    // `_build_bar_text_layer`): every bar-type layer alike, `is_stacked=False`
    // ("An overlay bar layer never stacks"), so a label sits at the plain
    // per-row value position, never a stack's cumulative midpoint. A value
    // label is a text mark under Vega-Lite's own description: its x, then the
    // layer's measure at its raw value.
    let paint_layer_labels = |(layer, bl): (usize, &OverlayBarLayer),
                              bl_x: &str,
                              bar_layer_rows: &[serde_json::Map<String, Value>],
                              linear: &LinearScale,
                              bar_width: &dyn Fn(&str) -> f64|
     -> Result<Vec<Node>, Diagnostic> {
        let Some(ml) = bl.bar_mark.labels.as_ref().filter(|ml| ml.visible) else { return Ok(Vec::new()) };
        let layer_value_title = crate::common::axis_title_text(&bl.y, bl.label.as_deref(), &r.field_labels)?;
        let x_title = bl.x.as_deref().unwrap_or(cat_title_text.as_str());
        let spec = LabelSpec::new(ml, false, bl.label_is_house, bar_layer_rows, &bl.y);
        let sites = plain_sites(bar_layer_rows, (bl_x, &bl.y), crate::families::layers::layer_link(link.as_ref(), (bl.query_name.as_deref(), r.query_name.as_deref())), &spec, None, None)?;
        let label_layer = LabelLayer::new(spec, sites, false, false, false, false, false, fonts, payload.board_style.as_ref())?;
        let center = |cat: &str, _: f64, _: Option<usize>| band.center(cat).expect("a labeled category is on the band scale");
        let geom = LabelGeometry { band: &band, center: &center, linear };
        label_layer.warn_if_crowded(&r.id, Some(layer), &geom, None, Some(&|site| bar_width(&site.cat)));
        let points = label_layer.points(&geom, None, &LabelDecors::new(), &crate::selected::Selection::default(), |site| {
            Ok(tooltip_labels.then(|| {
                crate::contract::native_label(&[(x_title, site.cat.to_string()), (layer_value_title.as_str(), site.value.to_string())])
            }))
        })?;
        Ok(mark_labels(ml, false, false, true, &points, 0, payload.board_style.as_ref(), 0.0, 0.0, r.canvas.as_str(), r.link_open)?)
    };
    // A scale pinned away from the 0 baseline clips every bar layer and drops its radius.
    let paint_bar_layer = |out: &mut Vec<Node>, bl_index: usize, linear: &LinearScale| -> Result<(), Diagnostic> {
        let (bl, bar_layer_rows) = (bar_layers[bl_index], bar_layer_rows_list[bl_index]);
        // A layer on its own scale clips only past its authored domain.
        let layer_clip = if independent_y {
            layer_axes[bar_layer_at[bl_index]].cut == DomainCut::Clip
        } else {
            crate::families::layers::baseline_outside_shared_domain(ax_y)
        };
        let layer_radius = |bl: &OverlayBarLayer| if layer_clip { 0.0 } else { bl.bar_mark.border.radius.unwrap_or(0.0) };
        let bl_x = bl.x.as_deref().unwrap_or(cat_field);
        let bl_link = crate::families::layers::layer_link(link.as_ref(), (bl.query_name.as_deref(), r.query_name.as_deref()));
        // A layer bar's `(left, width)` over `cat`: its column on a time axis, else its inner share of the band.
        let layer_columns = time
            .as_ref()
            .zip(time_axis.as_ref())
            .map(|(t, axis)| {
                let own_dates: Vec<String> = bar_layer_rows.iter().filter(|row| row_num(row, &bl.y).is_some() && row.get(bl_x).is_some_and(|v| !v.is_null())).map(|row| row_str(row, bl_x)).collect();
                t.layer_columns(axis, plot_w, &bl.bar_mark, column_split.as_ref(), &own_dates).map(Columns::Timed)
            })
            .transpose()?;
        let bar_extent = |band_x: f64, cat: &str| match &layer_columns {
            Some(cols) => {
                let columns = cols.of(cat);
                (columns.left(band_x, 0), columns.width)
            }
            None => {
                let bw = band.bandwidth();
                let mark_w = (bw * bl.bar_mark.band_width).min(bw);
                (band_x + (bw - mark_w) / 2.0, mark_w)
            }
        };
        if let Some(start_field) = bl.y_start.as_deref() {
            // A ranged layer (a candlestick's body, open to close) draws one
            // rect per row at its band, spanning its two ends. Its fill is the
            // shared scale's: the base's own color field, else the layer's own
            // grouping values or label as claimed beside the legend.
            let bl_corner_r = layer_radius(bl);
            let bl_stroke_color = bl.bar_mark.border.color.as_deref().unwrap_or("none");
            let bl_stroke_w = bl.bar_mark.border.width.unwrap_or(0.0);
            let own_aria = (span_block.is_none())
                .then(|| RangedLayerAria::new(bl, start_field, bar_layer_rows, &r.field_labels, date_axis.is_some()))
                .transpose()?;
            let mut bars: Vec<Node> = Vec::new();
            for row in bar_layer_rows {
                let cat = row_str(row, bl_x);
                let Some(band_x) = band.position(&cat) else { continue };
                let (Some(end), Some(start)) = (row_num(row, &bl.y), row_num(row, start_field)) else { continue };
                // A value the base's own domain holds paints off it; any other is a claim on the shared scale.
                let fill = match bl.color.as_deref() {
                    Some(cf) if color_field == Some(cf) && color_domain.contains(&row_str(row, cf)) => {
                        crate::common::series_color(&row_str(row, cf), &color_domain, &r.series_palette, category_scale)?
                    }
                    Some(cf) => bar_layer_claims[bl_index].row_color(&layer_scale, cf, row),
                    None => bar_layer_fills[bl_index].clone().expect("a colorless layer claimed its fill"),
                };
                let (top, h, ends) = ranged_extent(|v| linear.position(v), end, Some(start));
                let aria = if span_block.is_some() {
                    let series = match bl.color.as_deref() {
                        Some(cf) if color_field == Some(cf) && color_domain.contains(&row_str(row, cf)) => Some(series_text(&row_str(row, cf))),
                        _ => layer_series.get(&cat).cloned(),
                    };
                    block_aria(row, series.as_deref(), true)?
                } else if let Some(own) = own_aria.as_ref().filter(|_| tooltip_labels) {
                    let header = crate::contract::tooltip_header_text(&cat, detected_data_tu, x_is_standard_date, grain_header_format)?;
                    Some(own.label(bl, &header, row, bl.axis_y.tooltip_format_or(tooltip_fmt))?)
                } else {
                    None
                };
                let (col_x, mark_w) = bar_extent(band_x, &cat);
                let href = bl_link.map(|link| link.href(row)).transpose()?;
                bars.extend(bar_passes(false, col_x, top, mark_w, h, &fill, bl.bar_mark.opacity, bl_stroke_color, bl_stroke_w, bl_corner_r, ends, true, None, aria.as_deref(), crate::href::anchor_for(bl_link, &href))?);
            }
            out.extend(clipped_if(layer_clip, &defined_clips, (plot_w, plot_h), &r.id, bars));
            out.extend(paint_layer_labels((bar_layer_at[bl_index], bl), bl_x, bar_layer_rows, linear, &|cat| bar_extent(band.position(cat).expect("a labeled category is on the band scale"), cat).1)?);
            return Ok(());
        }
        if bl.color.is_some() {
            let layer_color_field = bl.color.as_deref().expect("bl.color.is_some()");
            // A layer grouping by a field of its own paints its claimed
            // shared-scale colors; one sharing the base's `color` shares its scale.
            let shares_base_color = color_field == Some(layer_color_field);
            let layer_domain: Vec<String> = if shares_base_color {
                let mut domain = color_domain.clone();
                domain.extend(layer_color_values(bar_layer_rows, layer_color_field).into_iter().filter(|v| !color_domain.contains(v)));
                domain
            } else {
                layer_color_values(bar_layer_rows, layer_color_field)
            };
            // Over a stacked base sharing its color the base's stack order holds; otherwise no
            // `order` encoding reaches the layer, so Vega-Lite stacks by the color field
            // descending: the first value ends on top.
            let series_from_baseline: Vec<Option<String>> = if shares_base_color && is_stacked {
                let (seen, unseen): (Vec<String>, Vec<String>) = layer_domain.iter().cloned().partition(|v| color_domain.contains(v));
                unseen
                    .into_iter()
                    .chain(crate::common::sorted_series_by_stack_order(&seen, bar_layer_rows, layer_color_field, &bl.y, None)?)
                    .map(Some)
                    .collect()
            } else {
                let any_null = bar_layer_rows.iter().any(|row| layer_color_of(row, layer_color_field).is_none());
                layer_stack_order(&layer_domain, any_null)
            };
            // Rows sharing a category and series stack in row order.
            let spans = LayerStack::new(bar_layer_rows, bl_x, layer_color_field, &bl.y, &series_from_baseline)
                .of_rows(bar_layer_rows);
            let bar_layer_promoted = layer_color_promoted(bar_layer_rows, layer_color_field);
            // The layer's own mark style, not the base's — `emit_bar_layer`
            // (`_overlay.py`) is called with `bar_mark=layer.bar_mark`, its
            // OWN `band_width`/`border`, never the base's.
            let bl_corner_r = layer_radius(bl);
            let bl_stroke_color = bl.bar_mark.border.color.as_deref().unwrap_or("none");
            let bl_stroke_w = bl.bar_mark.border.width.unwrap_or(0.0);
            let mut rows_of: HashMap<(String, Option<String>), Vec<usize>> = HashMap::new();
            for (index, row) in bar_layer_rows.iter().enumerate() {
                rows_of.entry((row_str(row, bl_x), layer_color_of(row, layer_color_field))).or_default().push(index);
            }
            let mut bars: Vec<Node> = Vec::new();
            for cat in &cat_domain {
                let Some(band_x) = band.position(cat) else { continue };
                let (col_x, mark_w) = bar_extent(band_x, cat);
                for series in &series_from_baseline {
                    for &index in rows_of.get(&(cat.clone(), series.clone())).into_iter().flatten() {
                        let datum = &bar_layer_rows[index];
                        // A row with no value has no segment.
                        let Some((cum_before, cum_after)) = spans[index] else { continue };
                        let raw_val = row_num(datum, &bl.y).expect("a stacked row has its value");
                        let (y1, y2) = (linear.position(cum_before).min(plot_h), linear.position(cum_after).min(plot_h));
                        let (top_y, bottom_y) = if y1 <= y2 { (y1, y2) } else { (y2, y1) };
                        let seg_h = bottom_y - top_y;
                        let fill = match series {
                            Some(series) if shares_base_color && color_domain.contains(series) => {
                                // A grouped base colors its series off the alphabetical domain; a stacked one off its stack order.
                                let layer_palette = if is_stacked { palette_domain } else { &color_domain };
                                crate::common::series_color(series, layer_palette, &r.series_palette, category_scale)?
                            }
                            other => match bl.color.as_deref() {
                                Some(cf) => bar_layer_claims[bl_index].group_color(&layer_scale, cf, other.as_deref(), std::slice::from_ref(datum)),
                                None => layer_scale.color_of_value(other.as_deref()).to_string(),
                            },
                        };
                        let aria = bar_layer_aria(bl, datum, raw_val, bar_layer_promoted)?;
                        let href = bl_link.map(|link| link.href(datum)).transpose()?;
                        bars.extend(bar_passes(false, col_x, top_y, mark_w, seg_h, &fill, bl.bar_mark.opacity, bl_stroke_color, bl_stroke_w, bl_corner_r, Rounded::by_sign(raw_val), true, None, aria.as_deref(), crate::href::anchor_for(bl_link, &href))?);
                    }
                }
            }
            out.extend(clipped_if(layer_clip, &defined_clips, (plot_w, plot_h), &r.id, bars));
            out.extend(paint_layer_labels((bar_layer_at[bl_index], bl), bl_x, bar_layer_rows, linear, &|cat| bar_extent(band.position(cat).expect("a labeled category is on the band scale"), cat).1)?);
        } else {
            let bl_corner_r = layer_radius(bl);
            let bl_stroke_color = bl.bar_mark.border.color.as_deref().unwrap_or("none");
            let bl_stroke_w = bl.bar_mark.border.width.unwrap_or(0.0);
            let bl_fill = bar_layer_fills[bl_index].as_deref().expect("a colorless layer claimed its fill");
            let mut bars: Vec<Node> = Vec::new();
            // A row with no value draws no mark and takes no place in the stack.
            let valued = || bar_layer_rows.iter().filter(|row| row_num(row, &bl.y).is_some());
            for (row, (cat, before, after)) in
                valued().zip(row_order_stack(valued(), bl_x, &bl.y, &crate::families::layers::never_nan))
            {
                let Some(band_x) = band.position(&cat) else { continue };
                let val = row_num(row, &bl.y).expect("filtered to valued rows");
                // A bar layer's baseline stays on the plot when a zoomed floor puts 0 below it.
                let (y1, y2) = (linear.position(before).min(plot_h), linear.position(after).min(plot_h));
                let (bar_top, bar_bottom) = if y1 <= y2 { (y1, y2) } else { (y2, y1) };
                let bar_h = bar_bottom - bar_top;
                let aria = bar_layer_aria(bl, row, val, false)?;
                let (col_x, mark_w) = bar_extent(band_x, &cat);
                let href = bl_link.map(|link| link.href(row)).transpose()?;
                bars.extend(bar_passes(false, col_x, bar_top, mark_w, bar_h, bl_fill, bl.bar_mark.opacity, bl_stroke_color, bl_stroke_w, bl_corner_r, Rounded::by_sign(val), true, None, aria.as_deref(), crate::href::anchor_for(bl_link, &href))?);
            }
            out.extend(clipped_if(layer_clip, &defined_clips, (plot_w, plot_h), &r.id, bars));
            // The layer's OWN value labels (`_build_layer_label_specs` →
            // `_build_bar_text_layer`) paint right after its own bars, before
            // the zero rule — the base's own labels paint last (see below).
            out.extend(paint_layer_labels((bar_layer_at[bl_index], bl), bl_x, bar_layer_rows, linear, &|cat| bar_extent(band.position(cat).expect("a labeled category is on the band scale"), cat).1)?);
        }
        Ok(())
    };

    // A scatter-type layer paints its own points. On the shared scale the aria
    // label reuses the base's `bar_datum_aria`/`combo_series` — probed against
    // `charts/extensibility_1`'s `lollipop` reference: base and layer both emit
    // the identical "header; series; value" label per category. A layer on its
    // own scale describes itself.
    let paint_scatter_layer = |scatter: usize, scale: &LinearScale| -> Result<Vec<Node>, String> {
        let (sl, sl_rows) = (scatter_layers[scatter], scatter_layer_rows[scatter]);
        let claim = &scatter_claims[scatter];
        let encoded = |row: &serde_json::Map<String, Value>| {
            claim.row_color(&layer_scale, sl.color.as_deref().expect("an encoded layer groups by color"), row)
        };
        let size = sl.size();
        crate::families::layers::scatter_layer_marks(
            sl,
            sl_rows,
            size,
            1 + scatter_layer_at[scatter],
            false,
            |row| band.center(&row_str(row, sl.x.as_deref().unwrap_or(cat_field))),
            None,
            |val| {
                let past_own_domain = independent_y && layer_axes[scatter_layer_at[scatter]].cut != DomainCut::Within;
                if past_own_domain { scale.position(val) } else { scale.position(val).clamp(0.0, plot_h) }
            },
            |row, val| {
                if r.y_start.is_some() {
                    let cat = row_str(row, sl.x.as_deref().unwrap_or(cat_field));
                    return span_block.as_ref().map(|_| block_aria(row, layer_series.get(&cat).map(String::as_str), false)).transpose().map(Option::flatten);
                }
                // A right-pinned layer owns its measure: `_layer_tooltip_description`
                // names the layer and formats it in its own axis' tooltip format.
                // Beside a base on another measure it names its own.
                let name = crate::common::axis_title_text(&sl.y, sl.label.as_deref(), &r.field_labels)?;
                if independent_y || name != value_title {
                    return describe_layer(&sl.axis_y, sl.x.as_deref().unwrap_or(cat_field), &name, None, row, val);
                }
                let cat = header_date(super::time_x::tooltip_date(row, sl.x.as_deref().unwrap_or(cat_field)));
                bar_datum_aria(
                    tooltip_labels, &cat, tooltip_tu, x_is_standard_date, grain_header_format, combo_series, None,
                    value_title, tooltip_fmt, val, None, None, &[], native.as_ref(),
                )
            },
            match &claim.fill {
                Some(fill) => PointColor::Claimed(fill),
                None => PointColor::Encoded(&encoded),
            },
            r.canvas.as_str(),
            payload.board_style.as_ref(),
            |row, val| {
                let layer_title = crate::common::axis_title_text(&sl.y, sl.label.as_deref(), &r.field_labels)?;
                Ok(tooltip_labels.then(|| {
                    crate::contract::native_label(&[
                        (sl.x.as_deref().unwrap_or(cat_title_text.as_str()), row_str(row, sl.x.as_deref().unwrap_or(cat_field))),
                        (layer_title.as_str(), val.to_string()),
                    ])
                }))
            },
            crate::families::layers::layer_link(link.as_ref(), (sl.query_name.as_deref(), r.query_name.as_deref())), scatter_clip(sl, sl_rows).then_some((plot_w, plot_h)),
            crate::families::layers::LabelCrowd { chart_id: &r.id, layer: scatter_layer_at[scatter], fonts },
        )
        .map_err(|e| format!("bar: {e}"))
    };

    // Shared-scale paint order: base bar, then every layer in authored order,
    // then the zero rule LAST — production's own `[base_spec, *layer_specs]`
    // list, one entry per authored `chart.layers` item, base first
    // (`_overlay.py:914`). The rule sits last because production appends it as
    // an ordinary top-level sibling AFTER every data layer once a second layer
    // exists (`_overlay.py`'s own dual-axis comment: nesting the rule inside
    // layer 0 is needed only under `resolve.scale.y: independent`, to avoid a
    // degenerate [0,0] scale — a shared scale has no such risk, so the rule is
    // free to sit wherever authored-layer order plus "reference line always on
    // top" puts it: last).
    let mut within_kind = [0usize; 3];
    let layer_kind_index: Vec<usize> = layers
        .iter()
        .map(|layer| {
            let counter = match layer {
                OverlayLayer::Bar(_) => &mut within_kind[0],
                OverlayLayer::Line(_) => &mut within_kind[1],
                OverlayLayer::Scatter(_) => &mut within_kind[2],
            };
            *counter += 1;
            *counter - 1
        })
        .collect();
    let paint_layer = |out: &mut Vec<Node>, position: usize, scale: &LinearScale| -> Result<(), Diagnostic> {
        let kind_index = layer_kind_index[position];
        match &layers[position] {
            OverlayLayer::Bar(_) => paint_bar_layer(out, kind_index, scale),
            OverlayLayer::Line(_) => Ok(out.extend(paint_layer_series(kind_index, scale)?)),
            OverlayLayer::Scatter(_) => Ok(out.extend(paint_scatter_layer(kind_index, scale)?)),
        }
    };
    if !independent_y {
        for position in 0..layers.len() {
            paint_layer(&mut plot, position, &linear)?;
        }
    }

    // Zero line at the bottom of the plot (value = 0), painted OVER the
    // columns: production compiles it as its own layer AFTER the bar layer
    // (`layer[0] = {mark: bar}`, `layer[1] = {mark: rule, y.datum: 0}`;
    // a normalize stack adds `y.datum: 1`), so Vega's rule covers the bars'
    // white stroke instead of sitting under it. Same gates as the margin
    // ink: hidden grid OR a hidden threshold suppresses it entirely
    // (see `baseline_rules`) — a normalize stack's 0%/100% rules are not
    // exempted from `grid.visible`. Gates on `rules` (never a
    // second, independent read of `grid.visible`/`threshold_visible()` —
    // "one law, one function"), which is ALSO how `independent_scale`
    // suppresses the rule entirely — see `rules`'s own assignment above.
    // Layers painted so far: the bars, then the zero rule when one is.
    // The rules reach under the outermost tick strokes, interval boundaries included.
    let interval: Option<BandInterval> = match band_intervals {
        Some(iv) => {
            let at = |coord: f64| bucket_x(&band, &cat_domain, coord).ok_or("bar: an interval label sits off the band");
            Some(BandInterval {
                ticks: iv.ticks.iter().map(|&coord| at(coord)).collect::<Result<_, _>>()?,
                labels: iv
                    .labels
                    .into_iter()
                    .zip(iv.label_spans)
                    .map(|(tl, (from, to))| Ok((tl, at((from + to) / 2.0)?)))
                    .collect::<Result<_, &str>>()?,
            })
        }
        None => None,
    };
    let boundary_xs = interval.iter().flat_map(|iv| &iv.ticks).map(|&x| band_tick_snap(x));
    let date_tick_xs: Vec<f64> = match &time_axis {
        Some(axis) => axis.tick_fracs(plot_w, fonts)?.into_iter().map(|frac| (plot_w * frac).round() + 0.5).collect(),
        None => cat_domain.iter().filter_map(|cat| band.position(cat)).map(|x| band_tick_snap(x + band.bandwidth() / 2.0)).collect(),
    };
    let ticks_drawn = x_tick_len > 0.0 || time_axis.as_ref().is_some_and(TemporalXAxis::draws_ticks);
    let floor_span = crate::common::floor_rule_span(plot_w, ax_x, ticks_drawn, boundary_xs.chain(date_tick_xs));
    // The value axis edges the ticks start from, in plot coordinates: a baseline rule reaches them so its continued tick meets it.
    let val_edge_x = if val_on_right { facet_axis_right_base + value_axis_right_overhang + ax_off_y + 0.5 } else { plot_left - ax_off_y + 0.5 };
    let val_edges: Vec<f64> = if val_tick_size > 0.0 && paint_value_axis {
        let ghost_edge_x = if val_on_right { plot_left - ax_off_y + 0.5 } else { plot_right + ax_off_y + 0.5 };
        std::iter::once(val_edge_x).chain(mirror_active.then_some(ghost_edge_x)).map(|x| x - plot_left).collect()
    } else {
        Vec::new()
    };
    let (rx1, rx2) = crate::common::join_rule_to_edges(floor_span, &val_edges);
    let mut painted_layers = bar_layers_painted;
    if let Some(band) = &hover_band {
        plot.push(band.node(true, &linear, plot_h, r.link_open)?);
    }
    if let Some(zl) = &grid.threshold {
        if let Some(rules) = rules {
            let zc = zl.stroke();
            let zw = zl.width.unwrap_or(2.0);
            let rule_ys: Vec<f64> = [rules.zero.then(|| linear.position(0.0)), rules.unity.then(|| linear.position(1.0))]
                .into_iter()
                .flatten()
                .collect();
            painted_layers += rule_ys.len();
            plot.extend(baseline_rule_layers(bar_layers_painted, &rule_ys, |y| {
                Attrs::new()
                    .set("x1", format!("{rx1:.2}"))
                    .set("y1", format!("{y:.2}"))
                    .set("x2", format!("{rx2:.2}"))
                    .set("y2", format!("{y:.2}"))
                    .set("stroke", zc)
                    .set("stroke-width", zw.to_string())
            })?);
        }
    }

    if ghosts_on {
        let rules: Vec<crate::contract::GhostRule> = ghost_points
            .iter()
            .map(|(aria, x, y, stroke, href)| crate::contract::GhostRule { aria: aria.as_deref(), x: *x, y: *y, stroke, anchor: r.link_open.anchor(href.as_deref()) })
            .collect();
        plot.push(crate::contract::ghost_layer(&rules, crate::contract::GhostRun::Down(plot_h))?);
    }

    // Dual-axis paint order: base bar, THEN the zero rule (production nests
    // it inside the base's own `layer[0]` wrapper — see the shared-scale
    // comment above for why), THEN each layer on its own independent scale,
    // each followed by its own zero rule nested in its entry — probed against
    // `layered-charts_0`'s compiled scenegraph: `layer_0_layer_0_marks` (bars),
    // `layer_0_layer_1_marks` (rule), `layer_1_layer_0_marks` (line),
    // `layer_1_layer_1_marks` (point).
    for (position, (ll, zero_rule)) in layer_linear.iter().zip(&layer_zero_rules).enumerate() {
        // A bar layer opens its own clip; every other mark is cut off as one group past its authored domain.
        let clipped = layer_axes[position].cut == DomainCut::Clip && !matches!(layers[position], OverlayLayer::Bar(_));
        let mut painted: Vec<Node> = Vec::new();
        paint_layer(&mut painted, position, ll)?;
        plot.extend(clipped_if(clipped, &defined_clips, (plot_w, plot_h), &r.id, painted));
        if let (Some(rule_width), Some(zl)) = (zero_rule, &grid.threshold) {
            let zc = zl.stroke();
            let layer_edge = if layer_axes[position].side == LayerSide::Right { plot_right + ax_off_y + 0.5 } else { plot_left - ax_off_y + 0.5 } - plot_left;
            let (lx1, lx2) = crate::common::join_rule_to_edges(floor_span, &[layer_edge]);
            plot.extend(baseline_rule_layers(painted_layers + 1 + position, &[ll.position(0.0)], |y| {
                Attrs::new()
                    .set("x1", format!("{lx1:.2}"))
                    .set("y1", format!("{y:.2}"))
                    .set("x2", format!("{lx2:.2}"))
                    .set("y2", format!("{y:.2}"))
                    .set("stroke", zc)
                    .set("stroke-width", rule_width.to_string())
            })?);
        }
    }

    let mut tail: Vec<Node> = Vec::new();

    // Authored bar value labels, then the stack-total label: one text layer
    // each, after the layers already painted (`painted_layers`). Built from
    // the base's OWN rows, never `cat_domain` — a bar-type layer's union
    // domain carries bands (`bar_layer`'s own categories) the base has no row
    // for at all, and `data_map` has no entry to distinguish "no row" from "a
    // genuine 0".
    let label_center = |cat: &str, band_x: f64, slot: Option<usize>| {
        let columns = cols.of(cat);
        columns.label_center(band_x, slot.unwrap_or(0))
    };
    let label_geom = LabelGeometry { band: &band, center: &label_center, linear: &linear };
    let label_hide = label_hide_cfg.map(|(span, font_size)| FitHide { plot_h, span, font_size });
    for layer in &label_layers {
        if facet.is_some() {
            // A facet cell's bounds carry its labels' ink; the grid lays rows out around it.
            let mut label_ink = vega_fit::Bounds::empty_new();
            layer.ink(&label_geom, if layer.total { None } else { label_hide }, &mut label_ink);
            label_ink.translate(plot_left, plot_top);
            ink.union(&label_ink);
        }
        // A scatter layer paints its own, unmodeled number of layers between
        // the bars and the base labels, so the group's ordinal is unknown
        // there: those labels stay bare.
        under.extend(layer.site_inks(&label_geom, if layer.total { None } else { label_hide }).map(|(_, b)| crate::annotation::DataMark::Box(b)));
        let ordinal_known = layer.total || scatter_layers.is_empty();
        let hide = if layer.total { None } else { label_hide };
        layer.warn_if_crowded(&r.id, None, &label_geom, hide, Some(&|site| cols.of(&site.cat).width));
        let points = layer.points(&label_geom, hide, &label_decors, &r.selection, |site| {
            if !ordinal_known {
                return Ok(None);
            }
            let cat = site.cat.as_str();
            // Beside bar layers the base's labels are described by their x alone.
            if tooltip_labels && !bar_layers.is_empty() {
                return Ok(Some(crate::contract::native_label(&[(cat_title_text.as_str(), cat.to_string())])));
            }
            let plain = || {
                bar_datum_aria(
                    tooltip_labels, cat, tooltip_tu, x_is_standard_date, grain_header_format, combo_series, None,
                    value_title, tooltip_fmt, site.raw, None, None, &[], native.as_ref(),
                )
            };
            match ranged.as_ref().and_then(|rb| Some((rb, *rb.starts.get(&(cat.to_string(), String::new()))?))) {
                Some((rb, start)) => rb.aria(
                    tooltip_labels, cat, tooltip_tu, x_is_standard_date, grain_header_format, None, None,
                    (value_title, tooltip_fmt), (start, site.raw), plain,
                ),
                None => plain(),
            }
        })?;
        tail.extend(mark_labels(&layer.spec.ml, false, layer.stacked, !layer.total, &points, painted_layers,
            payload.board_style.as_ref(), plot_left, plot_top, r.canvas.as_str(), r.link_open)?);
        if !layer.total {
            painted_layers += 1;
        }
    }

    // Over the value labels; the plot origin is offset into the page frame.
    let annotations = crate::annotation::AnnotationCtx { occupied: under, ..annotation_ctx((plot_left, plot_top), plot_w, plot_h) };
    tail.extend(crate::annotation::nodes(&annotations, crate::annotation::Plane::Foreground)?);

    // Shaped widths, the fit arm's `cat_label_ink` measurer (Vega bounds the
    // drawn label); the title reads the same ink the margin reserves.
    let cat_title_reach = if cat_angle != 0.0 {
        Some(rotated_rows_reach(&rotated_texts, fonts, cat_family, cat_font_size, ax_x.labels.max_width, (cat_align, cat_baseline, cat_angle), true, x_edge)?)
    } else {
        None
    };
    // Category labels + tick marks + axis title (below plot, centered on
    // each band) — `BandXAxis`'s own doc comment carries the law. Built
    // regardless of `suppress_x_axis`: a facet composite reads it off ANY
    // panel (its own last one) to paint the shared axis once, even when
    // this particular panel's own copy stayed silent.
    let floor_rule = grid.threshold.as_ref().and_then(|zl| {
        let base = rules.into_iter().flat_map(|rules| [rules.zero.then(|| linear.position(0.0)), rules.unity.then(|| linear.position(1.0))]).flatten();
        let nested = layer_linear.iter().zip(&layer_zero_rules).filter(|(_, width)| width.is_some()).map(|(ll, _)| ll.position(0.0));
        crate::common::rule_on_edge(base.chain(nested), x_edge.edge_y(plot_h)).then(|| zl.stroke().to_string())
    });
    let x_axis = match &time_axis {
        Some(axis) => {
            let mut axis = axis.clone();
            axis.floor_rule = floor_rule;
            facet_compose::PanelXAxis::Temporal(axis)
        }
        None => facet_compose::PanelXAxis::Band(BandXAxis {
        band: band.clone(),
        cat_domain: cat_domain.clone(),
        cat_shown: cat_labels.iter().map(|l| ax_x.labels.clip(l, fonts)).collect::<Result<_, _>>()?,
        temporal_labels: temporal_labels.clone(),
        interval,
        cat_angle,
        cat_baseline: cat_baseline.to_string(),
        cat_align: cat_align.to_string(),
        x_tick_len,
        ax_off_x,
        floor_rule,
        cat_pad: cat_pad + x_clear.get(),
        cat_font_size,
        cat_family: cat_family.to_string(),
        label_rows,
        parity: match (x_parity_separation, &temporal_labels) {
            (Some(separation), Some(tls)) => {
                let cat_limit = ax_x.labels.max_width.unwrap_or(vega_fit::DEFAULT_VL_LABEL_LIMIT);
                let row_w = |row: &str| if row.is_empty() { Ok(0.0) } else { fonts.measure_shaped(row, cat_family, cat_font_size) };
                let widths = tls
                    .iter()
                    .map(|tl| Ok::<f64, String>(row_w(&tl.row1)?.max(row_w(&tl.row2)?).min(cat_limit)))
                    .collect::<Result<Vec<_>, _>>()?;
                Some((separation, widths))
            }
            _ => None,
        },
        cat_title_text: cat_title_paint.clone(),
        caption: category_axis_caption(&cat_title_paint, ax_x, &cat_domain)?,
        title_key: facet.is_none().then_some(crate::contract::LabelKey::X),
        edge: x_edge,
        cat_title_reach,
        }),
    };
    if paint_x_axis {
        tail.extend(x_axis.nodes(ax_x, plot_left, plot_w, (plot_top + x_edge.edge_y(plot_h), plot_top + x_edge.edge_y(plot_h)), fonts)?);
    }

    // Value-axis labels — one per tick, anchored to whichever side
    // val_on_right names. Vega-fit: flip-aligned right labels share one
    // right edge at the measured gutter's far side (labelPadding), the axis
    // group's +0.5 crispness translate applies in both coordinates, and the
    // middle baseline shifts by Vega's rounded offset.
    let val_color = ax_y.labels.font.color.as_deref().unwrap_or("#626366");
    let val_weight = ax_y.labels.font.weight_str();
    let (val_label_x, val_anchor) = if val_away {
        (facet_axis_right_base + value_axis_right_overhang + ax_off_y + 0.5 + val_tick_size + base_label_pad, "start")
    } else if val_on_right {
        (facet_axis_right_base + value_axis_right_overhang + ax_off_y + 0.5 + val_tick_size + val_gutter, "end")
    } else {
        (plot_left - ax_off_y + 0.5 - val_tick_size - base_label_pad, "end")
    };
    let val_baseline_shift = measure::baseline_shift("middle", val_font_size);
    let val_y_delta = 0.5;
    // One mark per rung, from the axis edge outward; label overlap thins the
    // labels, never the marks.
    let val_tick_marks = crate::common::value_tick_rows(
        plot_top,
        plot_bottom - plot_top,
        val_tick_size,
        tick_values.iter().map(|&tv| linear.position(tv)),
    );
    // A value tick at a painted baseline rule's value is that rule continued.
    let val_rules = match (rules, grid.threshold.as_ref().filter(|_| crate::common::rule_reaches(floor_span, val_edge_x - plot_left))) {
        (Some(rules), Some(zl)) => crate::common::value_axis_rules(
            plot_top,
            [rules.zero.then(|| linear.position(0.0)), rules.unity.then(|| linear.position(1.0))].into_iter().flatten(),
            zl.stroke(),
            rules.width,
        ),
        _ => Vec::new(),
    };
    let value_axis_start = tail.len();
    let (edge_x, outward) = (val_edge_x, if val_on_right { 1.0 } else { -1.0 });
    if crate::labels_expr::view::active() && facet.is_none() && ax_y.labels.upright() {
        use crate::labels_expr::view::{AxisSpec, Orient};
        let orient = if val_on_right { Orient::Right } else { Orient::Left };
        let align = match val_anchor {
            "start" => "left",
            "end" => "right",
            other => return Err(format!("bar: value axis anchor {other:?} has no label align").into()),
        };
        let spec = AxisSpec { orient, offset: outward * (val_label_x - edge_x), shift: 0.0, align: Some(align), baseline: None, angle: 0.0, flush: None, style: ax_y.labels.item_style()? };
        crate::labels_expr::view::register_axis("y", spec);
    }
    let val_caption = match &date_axis {
        Some(_) => date_value_caption(&val_title_paint, ax_y, (v_domain_min, linear_domain_max))?,
        None => value_axis_caption(&val_title_paint, val_title_style, ax_y, (v_domain_min, linear_domain_max), normalize)?,
    };
    let mut value_items: Vec<Node> = Vec::new();
    if paint_value_axis {
        value_items.extend(crate::common::value_ticks(
            &ax_y.ticks,
            val_tick_marks.iter().copied(),
            edge_x,
            outward,
            val_tick_size,
            &crate::common::thresholds(&val_rules),
        )?);
    }
    // A facet's `axis_y.mirror` ghost copy paints the SAME labels at the
    // SAME y positions on the opposite edge (`mirror_axis.py`'s own
    // `dict(primary_axis)` — only `orient`/`grid`/`title` differ) — captured
    // here, alongside the primary's own paint loop, so `render_facet` never
    // re-derives tick visibility/positions a second time.
    let mut val_ticks: Vec<(f64, String)> = Vec::new();
    let mut ghost_ticks: Vec<(f64, String)> = Vec::new();
    let ghost_texts = ghost.as_ref().map_or(Ok(Vec::new()), |g| g.texts(&tick_values, &val_tick_labels))?;
    for (i, (&tv, label)) in tick_values.iter().zip(val_tick_labels.iter()).enumerate() {
        let tick_y = plot_top + linear.position(tv) + val_y_delta;
        if tick_y < plot_top - 1.0 || tick_y > plot_bottom + 1.0 || val_visible.as_ref().is_some_and(|v| !v[i]) {
            continue;
        }
        let abs_y = tick_y + val_baseline_shift;
        val_ticks.push((abs_y, label.clone()));
        if ghost.is_some() && !left_flip {
            ghost_ticks.push((abs_y, ghost_texts[i].clone()));
        }
        // Independent-scale: the axis is embedded in the `cell`, so each tick
        // label's ink past the plot, the top label's ascent as well as the "0"
        // label's descent, is cell ink `layoutHeaders` lays rows out around
        // (a shared scale's `row_footer` is a separate role and never is).
        // It grows `PanelGeometry.ink`, never `plot_top`/`m.top`; the
        // composer unions the first row's top overhang with the plot's own
        // top ink (a max), so the same overhang is not reserved twice.
        if independent_scale {
            // `tick_y - val_y_delta`, never `abs_y` or the raw `tick_y`:
            // `text_ink`'s own `dy` already applies the "middle" baseline
            // shift internally (the same law the `fit` closure's own
            // label-ink computation, above, feeds a raw unshifted `y`
            // into) — `abs_y` has that SAME shift folded in a second time
            // for painting, which would double-apply it. `val_y_delta`
            // (the axis group's own 0.5 crisp `translate`) also has no
            // place in this ink: Vega's `axisLayout`
            // (`vega-view-transforms/src/layout/axis.js:77`) shifts the
            // axis's own bounds by `(x, y)` only — the crisp delta
            // (`axis.js:22`) lands on `item.x`/`item.y` (`:79`), painted,
            // never folded into the bounds `gridLayout` reads.
            facet_compose::union_embedded_label_ink(
                &mut ink,
                tick_y - val_y_delta,
                val_font_size,
                (plot_left, plot_top, plot_right, plot_bottom),
            );
        }
        if paint_value_axis {
            value_items.push(Node::text(
                Attrs::new()
                    .set("x", format!("{val_label_x:.2}"))
                    .set("y", format!("{abs_y:.2}"))
                    .set("text-anchor", val_anchor)
                    .vega("font-family", val_family)
                    .set("font-size", val_font_size.to_string())
                    .quick_some("font-style", ax_y.labels.font.style.as_deref())
                    .set("font-weight", val_weight.as_str())
                    .set("fill", val_color),
                ax_y.labels.clip(if left_flip { &ghost_texts[i] } else { label }, fonts)?,
            ));
        }
    }

    // Axis titles. Under vega-fit the right-side label x is already the
    // gutter's far edge (labels are right-aligned to it).
    let val_label_edge_x = if val_away {
        facet_axis_right_base + value_axis_right_overhang + ax_off_y + 0.5 + val_tick_size + val_gutter
    } else if val_on_right {
        val_label_x
    } else {
        val_label_x - max_val_w
    };
    if paint_value_axis {
        value_items.extend(crate::common::axis_title_rotated(
            &val_title_paint,
            val_title_style,
            val_label_edge_x,
            plot_top + plot_h / 2.0,
            if val_on_right { 1.0 } else { -1.0 },
            facet.is_none().then_some(crate::contract::LabelKey::Y),
        ));
        tail.extend(crate::contract::axis_group(
            crate::contract::AxisChannel::Y,
            &val_caption,
            (edge_x, plot_top),
            value_items,
        )?);
    }
    let value_axis_end = tail.len();
    // The ghost is `dict(primary_axis)` but for `orient`/`grid`/`title`: the
    // same ticks, labels and offsets, sign-flipped onto the opposite edge.
    if mirror_active {
        let (edge_x, outward, ghost_x, ghost_anchor) = if val_on_right {
            let pad = if val_away { base_label_pad } else { val_gutter };
            (plot_left - ax_off_y + 0.5, -1.0, plot_left - ax_off_y + 0.5 - val_tick_size - pad, "end")
        } else {
            (plot_right + ax_off_y + 0.5, 1.0, plot_right + ax_off_y + 0.5 + val_tick_size + base_label_pad, "start")
        };
        let mut ghost_items = crate::common::value_ticks(&ax_y.ticks, val_tick_marks.iter().copied(), edge_x, outward, val_tick_size, &crate::common::thresholds(&val_rules))?;
        for (abs_y, label) in &ghost_ticks {
            ghost_items.push(Node::text(
                Attrs::new()
                    .set("x", format!("{ghost_x:.2}"))
                    .set("y", format!("{abs_y:.2}"))
                    .set("text-anchor", ghost_anchor)
                    .vega("font-family", val_family)
                    .set("font-size", val_font_size.to_string())
                    .quick_some("font-style", ax_y.labels.font.style.as_deref())
                    .set("font-weight", val_weight.as_str())
                    .set("fill", val_color),
                ax_y.labels.clip(label, fonts)?,
            ));
        }
        tail.extend(crate::contract::axis_group(
            crate::contract::AxisChannel::Y,
            &val_caption.untitled(),
            (edge_x, plot_top),
            ghost_items,
        )?);
    }

    // Dual axis: each layer's own axis, on its own `layer_linear` scale and its
    // side. Only the first y title of the chart edits its label: the first
    // layer's stands in for a base title that draws nothing.
    for (position, ((la, ll), zero_width)) in layer_axes.iter().zip(&layer_linear).zip(&layer_zero_rules).enumerate() {
        let edge_x = match la.side {
            LayerSide::Right => plot_right + ax_off_y + 0.5,
            LayerSide::Left => plot_left - ax_off_y + 0.5,
        };
        tail.extend(crate::families::layers::layer_axis(
            la,
            ll,
            edge_x,
            (plot_top, plot_bottom),
            &LayerAxisInk {
                font_family: val_family,
                font_size: val_font_size,
                font_weight: &val_weight,
                font_style: ax_y.labels.font.style.as_deref(),
                font_color: val_color,
                tick_size: val_tick_size,
                ticks: &ax_y.ticks,
                title_style: layer_title_style,
                zero_rule: zero_width.zip(grid.threshold.as_ref()).filter(|_| crate::common::rule_reaches(floor_span, edge_x - plot_left)).map(|(width, zl)| (zl.stroke(), width)),
            },
            (facet.is_none() && position == 0 && axis_title_band(val_title_style, &val_title_paint) == 0.0)
                .then_some(crate::contract::LabelKey::Y),
        )?);
    }
    // Rail labels (production's right pane): anchored at the cascaded
    // stacked midpoints, painted in the companion ink of each series'
    // palette slot, positioned off the same linear scale the bars use.
    if show_rail_v {
        let gap_px = sl
            .gap_px
            .ok_or("bar: series_label.gap_px absent (theme required on the rail arm)")?;
        // The gap converts through the rendered scale's slope, but the
        // packing BOUNDS are the raw stacked extent (`tallest_total`) —
        // production measures px-per-unit on the drawn scale while holding
        // every nudged label inside the raw data extent, so none escapes
        // into the headroom and the branch midpoint is the data's own.
        let px_per_unit = (linear_domain_max > v_domain_min).then(|| (plot_h / (linear_domain_max - v_domain_min)).abs());
        let (cascaded, painted_plans, forcing) = crate::families::cartesian::place_rail_labels(
            &r.id,
            &rail_anchors,
            &rail_plans,
            gap_px,
            crate::families::cartesian::rail_line_height(sl.font_size),
            px_per_unit,
            (rail_cascade_min, rail_cascade_max),
        )?;
        crate::families::cartesian::warn_rail_plan(&r.id, rail_field, &color_domain, str::to_string, &painted_plans, &forcing);
        let rail_x = plot_left + rail_x_rel.expect("the rail arm places its labels");
        let mut rail_labels: Vec<Node> = Vec::new();
        // A grouped bar has no stack order: its series take the palette in color-domain order.
        let rail_palette: &[String] = if is_stacked { palette_domain } else { &color_domain };
        for (series, v) in &cascaded {
            let ry = linear.position(*v) + measure::baseline_shift("middle", sl.font_size);
            let rcolor = crate::common::series_ink(
                series,
                rail_palette,
                &r.series_palette,
                &sl.dark_companion_palette,
                &sl.pin_companions,
                category_scale,
            )?;
            let rail_label_attrs = match color_field.filter(|_| tooltip_labels) {
                Some(field) => crate::contract::series_label_attrs(field, series)?,
                None => Attrs::new(),
            };
            // A wrapped label's lines straddle the anchor.
            let plan = &painted_plans[series];
            let line_h = crate::families::cartesian::rail_line_height(sl.font_size);
            let first_y = ry - plan.extra_px(line_h) / 2.0;
            rail_labels.push(crate::families::cartesian::rail_label_node(
                Attrs::new()
                    .set("x", "0")
                    .set("y", format!("{first_y:.3}"))
                    .set("text-anchor", "start")
                    .vega("font-family", sl.font_family.as_str())
                    .set("font-size", sl.font_size.to_string())
                    .set("font-weight", sl.font_weight.as_str())
                    .set("font-style", sl.font_style.as_str())
                    .set("fill", rcolor.as_str())
                    .extend(rail_label_attrs),
                plan,
                line_h,
            ));
        }
        let rail = if tooltip_labels {
            vec![crate::contract::mark_group("text", "concat_1_marks", rail_labels)?]
        } else {
            rail_labels
        };
        tail.push(Node::group(Attrs::new().set("transform", format!("translate({rail_x:.3},{plot_top:.3})")), rail));
    }

    if let Some(rows) = &composed_rows {
        let strip_style = r
            .effective_support_table_style
            .as_ref()
            .expect("composed_rows implies effective_support_table_style");
        tail.extend(support_table_strip::composed_rows_nodes(
            rows,
            strip_style,
            ax_y.labels.padding.unwrap_or(6.0),
            plot_w,
            plot_left,
            if strip_style.position == "bottom" { plot_top + plot_h } else { plot_top },
            val_on_right,
            strip_axis_offset,
            r.strip_linked().then_some(r.link_open),
        )?);
    }

    let group_attrs = Attrs::new().set("transform", format!("translate({plot_left:.2},{plot_top:.2})"));
    // The splice points a facet composite cuts the printed panel at.
    let cuts = facet.map(|_| frame.cuts(&head, &group_attrs, &plot, &tail, Some(&crate::frame::ValueAxisAt::Tail(value_axis_start..value_axis_end))));
    let svg: Node = {
        let mut body = head;
        body.push(Node::group(group_attrs, plot));
        body.extend(tail);
        frame.svg(body)
    };

    // Row-label gutter: the same column the y-axis tick labels themselves
    // sit in (production: `support_table_attachment.py`'s `_label_stub_*`
    // layers), derived from the resolved `axis_y.position`. Reuses `val_pad`
    // (the y tick labels' own gutter width) rather than the themed
    // `axis_y.labels.padding` — the two differ by `VALUE_AXIS_BREATHING_ROOM`
    // vs the themed padding on the val-on-right default.
    let composed_done = composed_rows.is_some();
    let drawn = Drawn {
        svg,
        canvas: Rect { x: 0.0, y: 0.0, width: canvas_w, height: canvas_h },
        data_box: Rect { x: plot_left, y: plot_top, width: plot_w, height: plot_h },
        title_pos: vega_title_pos,
    };
    let geometry = cuts.map(|cuts| {
        BarStripGeometry {
            plot_left,
            plot_top,
            content_start: cuts.content_start,
            svg_end: cuts.svg_end,
            ink,
            axis_w_overhang: facet_axis_gutter,
            raw_right_reach,
            widest_val_tick_label_width: max_val_w,
            // A date axis's labels start-align at the themed padding, which its ghost copies.
            val_gutter: if val_away { base_label_pad } else { val_gutter },
            val_tick_size,
            val_ticks,
            ghost_labels: if facet.is_some() && !left_flip { ghost_ticks.iter().map(|(_, label)| label.clone()).collect() } else { Vec::new() },
            val_tick_marks,
            val_font_size,
            val_weight,
            val_style: ax_y.labels.font.style_attr(),
            val_color: val_color.to_string(),
            val_family: val_family.to_string(),
            val_caption,
            axis: x_axis,
            top_axis_overhang,
            value_title_w: if paint_value_axis && crate::common::y_title_extra(val_title_style, &val_title_paint) > 0.0 { val_title_w } else { 0.0 },
            facet_legend,
            value_axis: paint_value_axis
                .then(|| facet_compose::ValueAxisInk { range: cuts.value_axis(), origin: (0.0, 0.0) }),
        }
    });
    Ok((drawn, geometry, composed_done))
}
