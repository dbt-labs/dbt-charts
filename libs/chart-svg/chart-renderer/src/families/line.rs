//! Line family — single-series and multi-series line charts.
//!
//! Orientation is always vertical: categorical x axis (point scale, insertion
//! order), continuous y axis (linear scale, inverted so domain_max = top).
//!
//! Multi-series uses a series rail drawn to the right of the chart: each
//! series label is anchored at the y-position of that series' last non-null
//! data point, then cascaded to avoid collisions with neighboring labels.
//!
//! `curve: step` is production's band-aware plateau (`step_band.py`) on the
//! RESOLVED VL x type, not on whether a time unit was detected: it bands a
//! nominal x (plain categorical strings) AND a calendar-bucketed temporal x
//! (production forces THAT x-scale to ordinal too, for exactly this curve —
//! `type_inference.resolve_cartesian_x_type`; the port refuses the bucketed
//! one), but never a quantitative (numeric) x, an authored
//! `axis_x.type: temporal` x, or a standard ISO date no calendar grain
//! explains: those stay d3's plain center-anchored passthrough regardless of
//! curve — see `d3rs::shape::Curve`. Every banded row doubles onto its
//! band's left/right edge and the pair draws with `curveStepAfter` in
//! dataset order.
//!
//! `stack: zero`/`center` on an AREA is Vega's stack transform: each
//! series' baseline is the running sum of every series stacked below it at
//! that x, in production's `sorted_series_by_stack_order` order
//! (`style.stack_order`: the default descending global sum, `data`'s first
//! encounter, or `alphabetical` — `common::sorted_series_by_stack_order`) —
//! a THIRD list beside `spatial_domain` and `palette_domain`, never a
//! reordering of either). `center` additionally
//! floats every column up by `(max_column_total - this_column_total) / 2`
//! (Vega-Lite's own centered-offset formula, probe-pinned in
//! `tests/test_vl_probe.py` — not a naive `-total/2` silhouette) and
//! suppresses the zero rule outright. The stacked recipe is also a
//! different PAINT than the overlap one: one combined fill+stroke layer per
//! series (the perimeter stroke, not a separate top-edge line), no halo, no
//! points — already baked onto this payload's `line_mark`/`point_mark`
//! fields at resolve time, so only the backdrop and the two fill/stroke
//! emission blocks need their own `is_stacked` branch. A stacked area's y
//! domain is Vega-Lite's own (production bakes no `domain_max`/`domain_min`
//! for it — `resolve/chart/area.py` forces both `None`): `d3_nice(0,
//! stacked_total_max, 10)`, the same `d3_nice(_, _, 10)` call the
//! non-stacked path already makes — UNLESS the axis carries an authored
//! `scale.continuous.domain`, which reaches Vega verbatim with no `nice`
//! applied (probed) and is read the same way whether or not the chart is
//! stacked (`ax_y.authored_continuous_domain()`, the same field the zero
//! rule's `excludes_zero` check already reads two lines above) — OR a
//! zero-anchored axis on a unit spec (no rail, support table, or layer),
//! whose pinned `domainMin` (`type_inference.py::y_zero_scale`) suppresses
//! `nice` so the top edge is the raw `stacked_total_max`. A visible
//! endpoint rail changes nothing about that niced default even though
//! `endpoint_labels.py` PINS the main pane's domain to `[0,
//! stacked_total_max]` for `center`: the pin lands on the hconcat's SHARED
//! y scale and Vega-Lite's scale merge re-applies its default `nice` to the
//! union (probe-pinned in `test_vl_probe.py`: the same pane reads 0..161 as
//! a unit spec, 0..180 under a shared-y hconcat — `listening_streamgraph`'s
//! own reference reads "values from 0 to 180"), which is exactly
//! `d3_nice(0, stacked_total_max, 10)` again. An authored domain on that
//! shared scale is unprobed and refuses. Neither a null measure nor a
//! genuinely absent (x, series) row breaks a stacked series' own run the
//! way the base (unstacked) runs law does — production's
//! `sparse_band_transforms` fills both with a real 0 ("a null and an
//! absent row reach the same wedge", `_layers.py`), so either stays in as
//! a defined, zero-height vertex.
//!
//! `stack: normalize` on an area is Vega's `stackNormalize` offset
//! (`vega-encode/src/Stack.js`): each series' (y0, y1) is the SAME running
//! sum `stack: zero` accumulates, divided by that x column's own total —
//! `offset_at` stays 0 (no `center`-style float), and `stacked_runs_of`
//! divides `columns[i].bounds[idx]` by `columns[i].total` before mapping through the y scale, rather than adding an
//! offset before mapping raw sums. The rendered y domain is always
//! `[0, 1]`, un-niced (`_stacked_y_domain`, `endpoint_labels.py`, and
//! `_bake_normalize_domain`, `compile/resolve/chart/_domain.py:687`, agree:
//! normalize's domain never merge-nices the way `center`'s does), read off
//! `ax_y.authored_continuous_domain()` — production ALWAYS bakes this field
//! to `(0.0, 1.0)` at resolve time unless the author already authored their
//! own domain (`resolve/chart/area.py:471-477`), so an authored value other
//! than exactly `(0.0, 1.0)` is a shape this port has not probed and
//! refuses, mirroring the existing rail+authored-domain refusal's own
//! caution below. The axis label format is unconditionally pinned to
//! `.0%` (`emitters/_cartesian.py::pin_normalize_axis_format`) — never the
//! resolved `axis_y.labels.format`, which a normalize-stacked axis's own
//! render-time format ignores by production law, not merely a fixture
//! coincidence. Both baseline rules (`datum: 0` and `datum: 1`) fire
//! unconditionally whenever `grid.visible` and `threshold.visible`, with no
//! zero-anchor/straddle test and independent of any authored percent
//! format — production splits this across two call sites
//! (`_apply_zero_or_top`'s own `_insert_top_rules` for datum 0,
//! `_apply_unity` for datum 1 when a percent format is authored,
//! `_insert_top_rules` again for both when it is not — `baseline.py:
//! 330-347`), but the net visual result is the same pair of rules either
//! way, so this port draws both off one shared gate. A column whose total
//! is `<= 0` (every series absent/zero at that x, negatives already
//! refused above) divides 0/0, which clamps to 0, so its edges paint at the
//! plot top rather than refusing. The endpoint
//! rail's own per-series anchor (`_stacked_midpoints`'s `mid / col_total`
//! branch, `endpoint_labels.py`) divides the SAME midpoint by the anchor
//! column's own total; the group-total tooltip footer and share-of-total
//! row (`_stack_group_total`, `structured_tooltip.py:237-253`) fire on
//! `zero` and `normalize` alike, never `center` (no meaningful group sum on
//! a silhouette).
//!
//! A wide (`y: [a, b]`) chart unfolds into long rows once, at the top of
//! `bind`, before any other law runs — see
//! `common::unfold_wide_rows`'s doc comment. Its color domain
//! (`wide_series_names`, sorted, authored-measure-complete) seeds
//! `spatial_domain`, and — line/area only, the one place production
//! reorders fold emission — `spatial_domain` is then reassigned to
//! `legend_domain` (display order, last value descending) for mark PAINT
//! order; past those two wide-specific substitutions, everything else
//! (rail, legend, palette index) runs unchanged, generic over
//! `color_field`/`payload.rows` as it already is for an authored `color:`
//! chart. See `AGENTS.md`'s "Wide-measures fold" bullet for the full law.
//!
//! Unsupported shapes → clear `Err`:
//! - multiples layout
//! - non-empty layers
//! - a wide chart with an attached `support_table` or `style.dashes` (no
//!   corpus row for either; dashes never reach a wide chart in production
//!   either — `_apply_line_color_encoding` is unreachable from the folded
//!   arm)
//! - band-step on a wide chart (covered by the existing multi-series
//!   band-step gate below — a wide chart is multi-series by construction)
//! - log scale on the y axis
//! - unknown authored time_unit string on the x axis
//! - `axis_y.mirror` off the fit arm, with an authored `expr`, a
//!   `tick_label`, a facet `format` or a `ruler` other than `axis_y.ruler`
//!   (`AxisStyle::mirror_ghost`; the fit arm paints a ghost `format` through
//!   `FormatSlot::paint_tick`), with a wide/list y, alongside a visible
//!   endpoint-label rail — see `mirror_ghost_x`'s doc comment for the
//!   ported `mirror: true` fit-arm and facet shapes (production's
//!   `ResolvedMirrorAxis` object, whose `ruler` equals `axis_y.ruler`, is the
//!   same request)
//! - a data-driven multi-series color encoding under band-step
//! - band-step with a non-zero `axis_x.scale.padding`
//! - `stack` on a LINE chart (line has no stack concept at all)
//! - a negative value on a `center` or `normalize` stacked area, and on any
//!   stacked area with a visible endpoint rail (production's
//!   ERR-ENDPOINT-LABELS-NEGATIVE-STACK)
//! - a log measure axis on a stacked area
//!
//! An authored bucketed `time_unit` on nominal strings is an `Err`-only
//! over-refusal: production draws a band step there.

use crate::diagnostic::Diagnostic;
use std::collections::{HashMap, HashSet};

use crate::families::axis_caption::AxisCaption;
use crate::families::facet_compose;
use crate::families::facet_title;
use crate::families::multiples;

use serde::Deserialize;
use serde_json::Value;

use crate::common::{
    row_num, row_str, row_str_ref,
    symbol_radius, AxisStyle, ChartSort, MarkLabels,
    TitleStyle, FormatSlot,
};
use crate::families::layers::{
    resolve_layer_rows, OverlayBarLayer, OverlayLayer, OverlayLineLayer,
    OverlayScatterLayer, YSides,
};
use crate::families::heatmap::{flat_label, rotated_label, svg_text_anchor, LabelFace};
use crate::families::support_table_strip;
use crate::families::legend::{
    legend_title_text, LegendConfig,
};
use crate::families::temporal::{
    inline_label, VegaType, BareMark, BareTick, ResolvedVegaLabels, TemporalLabel, TimeUnit,
};
use scene::{Attrs, Node};
use crate::vega_fit;
use measure::{FontBook, Measure};
use print::print;
use crate::diagnostic::warn;
use crate::layout::Rect;
use crate::Payload;

// VL x-axis offset: gap between the plot bottom edge and the x-axis rule
// (vl spec: encoding.x.axis.offset = 1.0).
const X_AXIS_OFFSET: f64 = 1.0;

pub mod bind;
pub mod layout;

// ── Payload structs ──────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub(crate) struct LineResolved {
    /// Chart-root `ResolvedChart.link`: a channel-placeholder template painted
    /// as an `<a>` around each datum's marks.
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    pub link: Option<String>,
    #[serde(default)]
    pub link_open: crate::href::LinkOpen,
    /// The host's board URL scheme, from the payload; rewrites each datum href.
    #[serde(skip)]
    link_context: Option<crate::href::LinkContext>,
    /// The columns a link's `{{ y }}` and `{{ color }}` read: the authored
    /// ones, or on a wide chart the first measure and the authored dimension
    /// (else the fold's raw measure name). Set before the fold overwrites
    /// `color`.
    #[serde(skip)]
    pub link_y: String,
    /// The x holds labeled buckets (`2024-Q1`), which production rewrites to real dates.
    #[serde(skip)]
    pub link_x_labeled: bool,
    #[serde(skip)]
    pub link_color: Option<String>,
    /// Set where a wide chart with no authored `color` folds: its series are the `y` measure names.
    #[serde(skip)]
    pub series_from_measures: bool,
    /// The rows as queried, set where a wide chart folds: a support_table strip reads them.
    #[serde(skip)]
    pub prefold_rows: Vec<serde_json::Map<String, Value>>,
    pub id: String,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    pub title: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    pub subtitle: Option<String>,
    pub title_style: Option<TitleStyle>,
    /// Effective chart background: chart-local `style.<family>.background`
    /// when authored, else the board background — production's own
    /// `_SharedResolvedChartFields.background`
    /// (`compile/models/chart/resolved/_base.py:158`). This is the VL spec
    /// background behind the canvas rect (`render/chart/session.py:184`,
    /// `presentation.py:74`), NOT `Payload.background` (the board-level
    /// default only) — the two diverge whenever a chart authors its own
    /// `style.background`. It is the canvas rect's fill alone: ink reads
    /// `canvas`.
    pub background: String,
    /// The chart's opaque composited canvas
    /// (`_SharedResolvedChartFields.canvas`,
    /// `compile/models/chart/resolved/_base.py:169`): the fill production
    /// hands `emit_area_layer`/`emit_line_layer` (`emitters/area.py:520,633`,
    /// `emitters/line.py:547,622`) and `render_cartesian_overlay`'s
    /// `canvas` arg (`emitters/area.py:676`, `emitters/line.py:662`) for the
    /// area backdrop and every halo/knockout, where `background` may be
    /// `transparent`.
    #[serde(deserialize_with = "crate::common::de_canvas")]
    pub canvas: String,
    pub x: String,
    pub y: String,
    #[serde(default)]
    pub annotations: Vec<crate::annotation::Annotation>,
    #[serde(default)]
    pub x_label: Option<String>,
    #[serde(default)]
    pub y_label: Option<String>,
    pub field_labels: crate::common::FieldLabels,
    #[serde(default)]
    pub(crate) tooltip: Vec<crate::families::tooltip_extra::TooltipField>,
    /// Authored `chart.sort` — reorders the x domain by its per-category
    /// MIN aggregate (`common::dimension_sort_domain`).
    #[serde(default)]
    pub sort: Option<ChartSort>,
    pub color: Option<String>,
    /// How the query prints the `color` column.
    #[serde(default)]
    pub color_format: Option<crate::common::FormatSlot>,
    #[serde(default)]
    pub resolved_channels: HashMap<String, crate::common::StyleChannel>,
    /// The continuous scale a numeric `color` column draws and the field it reads, set at render.
    #[serde(skip)]
    pub numeric_color: Option<(crate::families::color_scale::ContinuousColor, String)>,
    #[serde(default)]
    pub stack: Option<String>,
    pub palette: Vec<String>,
    /// The palette a series color reads: the chart's own, else the board's, else Vega's default.
    #[serde(skip)]
    pub series_palette: Vec<String>,
    /// Board-wide value→slot bindings for this chart's categorical fields
    /// (#7167) — see `common::category_scale_for`/`series_color`.
    #[serde(default)]
    pub category_colors: Vec<crate::common::CategoryColorScale>,
    pub multiples: Option<crate::families::multiples::MultiplesConfig>,
    /// Baked small-multiples partition dimensions (`resolved.panel_axes`) —
    /// present exactly when `multiples` is (resolve bakes one `PartitionAxis`
    /// per `multiples.rows`/`multiples.columns` field actually authored).
    #[serde(default)]
    pub panel_axes: Vec<crate::families::multiples::PartitionAxis>,
    #[serde(default)]
    pub wide_measures: Vec<String>,
    #[serde(default)]
    pub layers: Vec<Value>,
    /// The chart's own resolved query name — every layer defaults to it
    /// (`compile/resolve/_layers.py`) unless it authors its own `query:`.
    /// `None` on any payload predating this field; only the layers gate
    /// reads it, and only when `layers` is non-empty.
    #[serde(default)]
    pub query_name: Option<String>,
    pub legend: LegendConfig,
    pub style: LineStyle,
    /// A non-null `support_table` grows the card past the plot's own
    /// `payload.height` for the attached strip's rows — see `AGENTS.md`'s
    /// geometry contract. `source`/`aggregate` entries render; `per_series`
    /// entries are refused (`support_table_strip::parse_config`).
    #[serde(default)]
    pub support_table: Option<Value>,
    /// The support_table strip's resolved style — present exactly when
    /// `support_table` is (baked at `compile.resolve.chart._kwargs`).
    #[serde(default)]
    pub effective_support_table_style: Option<support_table_strip::SupportTableStyle>,
    /// Resolved chart's own baked axis-label gap, required for a `position: bottom` strip.
    #[serde(default)]
    pub support_table_axis_offset: Option<f64>,
}

#[derive(Deserialize)]
pub(crate) struct LineStyle {
    pub axis_x: AxisStyle,
    pub axis_y: AxisStyle,
    pub line_mark: LineMark,
    pub series_label: SeriesLabel,
    pub endpoint_labels: EndpointLabels,
    pub single_series_fill: String,
    /// `style.stack_order` (`value`/`data`/`alphabetical`) — the stacked
    /// area's baseline-first series rule, `common::sorted_series_by_stack_order`.
    #[serde(default)]
    pub stack_order: Option<String>,
    #[serde(default)]
    pub area_mark: Option<AreaMark>,
    /// Width-resolved title font (production's `chart_title_spec` output for
    /// this card's width tier — a nested card gets 14px Inter where a
    /// standard slot gets 18px Source Serif). Always preferred over
    /// `title_style.font`, which carries the un-tiered base.
    #[serde(default)]
    pub title_font: Option<crate::common::Font>,
    /// Point markers on data points (`size` is the Vega symbol AREA; 0 =
    /// off). Production layers a canvas-colored halo symbol at
    /// `size × halo_multiplier` under a line-colored point.
    #[serde(default)]
    pub point_mark: Option<PointMark>,
    /// `style.charts.dashes`: the strokeDash range production binds to the
    /// color field of a series-colored line (`emitters/line.py`); empty
    /// when the board authors none.
    #[serde(default)]
    pub dashes: Vec<Vec<f64>>,
    /// Production's `_house_register_text_encoding` gate, baked per label
    /// slot: `line_`/`point_` on a line chart's two slots, the bare
    /// `label_is_house` on an area's single one.
    #[serde(default)]
    pub line_label_is_house: bool,
    #[serde(default)]
    pub point_label_is_house: bool,
    #[serde(default)]
    pub label_is_house: bool,
    /// L3: production's `label_usable_ratio` — the fraction of the SLOT
    /// width (`payload.width`, minus any endpoint-rail span) the x-label
    /// crowding band is measured against, not the fitted plot width. Same
    /// top-level `style.label_usable_ratio` key bar.rs's `BarStyle` reads.
    #[serde(default)]
    pub label_usable_ratio: Option<f64>,
    /// The format of the tooltip's value/total rows -- an absent or empty
    /// spec means no tooltip aria-labels for this chart (`tooltip_labels`
    /// gate, same law as bar/pie/scatter/heatmap).
    #[serde(default)]
    pub tooltip_format: FormatSlot,
}

#[derive(Deserialize)]
pub(crate) struct PointMark {
    /// Vega symbol AREA; 0 or null = no point markers (area payloads bake
    /// null here).
    #[serde(default)]
    pub size: Option<f64>,
    /// `marks.point.labels` is production's ALIAS for `marks.line.labels` on
    /// a LINE chart (`_apply_line` prefers the explicit line slot and falls
    /// back here); an area chart never reads it.
    #[serde(default)]
    pub labels: Option<MarkLabels>,
    /// Draw markers only at each series' last datum (`labels.visible: last`).
    #[serde(default)]
    pub endpoint_only: bool,
}

/// The halo point layer sets no `strokeWidth` of its own
/// (`_layers.py::_line_halo_sublayers`), so it draws at VL's default point
/// strokeWidth — and Vega's `boundStroke` grows a mark's bounds by the FULL
/// stroke width, not half of it.
const HALO_POINT_STROKE_W: f64 = 2.0;


/// How far a point marker's ink reaches from its datum: its halo symbol plus
/// that stroke, or the bare point where the halo is smaller. The foreground
/// point's own compiled strokeWidth is inert — `filled: true` with no stroke
/// color paints no stroke, so `boundStroke` skips it.
///
/// `halo_multiplier == 0.0` is a distinct case, not a small halo:
/// production's `_line_halo_sublayers` returns `[]` there (no halo layer at
/// all), so the reach is the bare point radius, never
/// `symbol_radius(size × 0) + HALO_POINT_STROKE_W` — that term is ink for a
/// layer that is never drawn.
fn point_mark_reach(size: f64, halo_multiplier: f64) -> f64 {
    if halo_multiplier == 0.0 {
        return symbol_radius(size);
    }
    symbol_radius(size).max(symbol_radius(size * halo_multiplier) + HALO_POINT_STROKE_W)
}

/// A series' `stroke-dasharray` list (`""` for a solid line): Vega's ordinal
/// strokeDash scale cycles its range by the series' index in the palette
/// domain. The marks and the legend sample both read it.
fn series_dash(palette_domain: &[String], dashes: &[Vec<f64>], series: &str) -> String {
    let i = palette_domain.iter().position(|s| s == series).unwrap_or(0);
    dashes[i % dashes.len()].iter().map(f64::to_string).collect::<Vec<_>>().join(",")
}

/// Mirror ghost label x (`style.axis_y.mirror: true`, `mirror_axis.py`):
/// production copies the primary axis's own VL config verbatim but for
/// `orient`/`grid`/`title`, so the ghost's offset from ITS edge is the
/// PRIMARY's own reserved width (`primary_reserve`, `y_tick_eff + gutter`),
/// sign-flipped onto the LEFT edge's own 0.5 crisp anchor — never
/// re-measured. A left primary's ghost sits on the right edge instead.
fn mirror_ghost_x(primary_reserve: f64) -> f64 {
    0.5 - primary_reserve
}

/// support_table per_series row order for a non-stacked line/non-stacked
/// area — port of `_series_order_strip`'s "Line / non-stacked area" branch
/// (support_table_attachment.py:2693-2718): ascending by each series' own
/// y value at the chart's LAST x (ties broken alphabetically), so
/// series_order[0] sits at the visual bottom of the chart, matching how the
/// strip reads top-to-bottom against the chart's own top-to-bottom line
/// order. "Last x" is the greatest `x_field` value of the rows it is handed, which
/// callers rank by axis position (`x_ranked_rows`).
fn last_y_ascending_order(
    rows: &[serde_json::Map<String, serde_json::Value>],
    x_field: &str,
    y_field: &str,
    color_field: &str,
    distinct: &[String],
) -> Result<Vec<String>, String> {
    // Production compares raw values with `max`, skipping nulls.
    let mut last_x: Option<&Value> = None;
    for x in rows.iter().filter_map(|r| r.get(x_field)).filter(|v| !v.is_null()) {
        if last_x.map_or(Ok(true), |held| crate::common::raw_x_order(x, held).map(|o| o.is_gt()))? {
            last_x = Some(x);
        }
    }
    let is_last_x = |r: &serde_json::Map<String, Value>| match (last_x, r.get(x_field)) {
        (Some(last), Some(x)) if !x.is_null() => crate::common::raw_x_order(x, last).is_ok_and(|o| o.is_eq()),
        _ => false,
    };
    let mut last_y: HashMap<String, f64> = distinct.iter().map(|s| (s.clone(), 0.0)).collect();
    for row in rows.iter().filter(|r| is_last_x(r)) {
        let s = row_str(row, color_field);
        if s.is_empty() {
            continue;
        }
        if let Some(y) = row_num(row, y_field) {
            last_y.insert(s, y);
        }
    }
    let mut out = distinct.to_vec();
    out.sort_by(|a, b| last_y[a].partial_cmp(&last_y[b]).unwrap().then_with(|| a.cmp(b)));
    Ok(out)
}

#[derive(Deserialize)]
pub(crate) struct LineMark {
    pub stroke: StrokeAttrs,
    pub halo_multiplier: f64,
    /// Absent on area's `line_mark` slice (area charts have no step curve
    /// concept); present on line's, where production gates
    /// `bucket_aligned_temporal` on `curve == "step"` (`_cartesian.py:347`).
    #[serde(default)]
    pub curve: Option<String>,
    /// Band-step only (`curve: step` on a nominal/ordinal x): whether
    /// adjacent plateaus are joined (`true`/absent, the default) or left as
    /// separate segments (`false`) — `step_band.py`'s `connect` parameter.
    /// No area equivalent (`_apply_area_step_band` always hardcodes
    /// `connect=True`), so `AreaMark` carries no such field.
    #[serde(default)]
    pub connect: Option<bool>,
    /// Baked stroke-linecap for a disconnected band-step's separate
    /// plateaus (`connect: false`) — replaces `stroke.cap` there, since the
    /// default round cap would overhang each plateau's flush band edge.
    #[serde(default)]
    pub disconnected_cap: Option<String>,
    /// Share of its band a disconnected band-step plateau spans
    /// (`style.charts.marks.line.disconnected_width`). Absent on area's
    /// `line_mark` slice, which has no disconnected step.
    #[serde(default)]
    pub disconnected_width: Option<f64>,
    /// Authored value labels (`style.marks.line.labels`) — production's
    /// `value_labels` feature reads this slot for a line AND for an area
    /// (`_apply_area` never looks at `area_mark`, which carries no `labels`
    /// key at all).
    #[serde(default)]
    pub labels: Option<MarkLabels>,
}

#[derive(Deserialize)]
pub(crate) struct StrokeAttrs {
    pub width: Option<f64>,
    pub cap: Option<String>,
    pub join: Option<String>,
    /// The authored `style.marks.line.stroke.dasharray`, painted on the
    /// foreground line only — never its halo (`emitters/_layers.py:283`).
    #[serde(default)]
    pub dasharray: Option<String>,
    /// An explicit top-edge/fg-line stroke color, whether authored
    /// (`style.marks.line.stroke.color`) or baked by `area.py`'s stack-mode
    /// swap (`marks.area.stacked.stroke`, `on_stacked_recipe = is_stacked
    /// or is_single_series` — a single-series area reaches this even with
    /// `stack: "none"`). When present it paints on EVERY series, color
    /// encoding or not: line's `_line_fg_sublayers` (`emitters/
    /// _layers.py:283-303`) sets `pin_child_colors=has_color_enc`
    /// (`emitters/line.py:611`), pinning the fg layer's own `encoding.
    /// stroke` to this literal and overriding the parent `encoding.color`;
    /// area's `_area_fg_layers` (`_layers.py:766-791`) does the same with
    /// `pin_child_colors=color_ch is not None` (`emitters/area.py:599`).
    /// Only when this field is unset does the series' own fill color paint.
    #[serde(default)]
    pub color: Option<String>,
}

#[derive(Deserialize)]
pub(crate) struct SeriesLabel {
    pub font_family: String,
    pub font_size: f64,
    pub font_weight: String,
    pub dark_companion_palette: Vec<String>,
    /// `(pinned color, ink)` pairs for the `category_colors` pins outside the
    /// palette (`common::category_ink`).
    pub pin_companions: Vec<(String, String)>,
    /// Gap between the plot right edge and the rail label start on the
    /// LEGACY (non-concat) rail arms; payload field. The rail-concat arm
    /// uses `EndpointLabels.label_offset` — production's real hconcat
    /// spacing — instead.
    pub gap_px: f64,
}

#[derive(Deserialize)]
pub(crate) struct EndpointLabels {
    pub visible: bool,
    /// hconcat spacing between the main pane and the label pane
    /// (translate.py `_wrap_hconcat_label_pane`: `spacing: label_offset`).
    /// Required on `EndpointLabelsConfig` (extra="forbid", theme-populated),
    /// so a payload without it is malformed — deserialization errors rather
    /// than silently landing the rail at a default offset.
    pub label_offset: f64,
}

#[derive(Deserialize, Default)]
pub(crate) struct AreaMark {
    pub opacity: f64,
    #[serde(default)]
    pub backdrop: bool,
    /// Area's curve lives on area_mark, not line_mark (theme/marks.py
    /// documents step/step-before/step-after for the area mark too).
    #[serde(default)]
    pub curve: Option<String>,
}

/// A temporal label at `angle`: its text, row count, and the align/baseline
/// Vega-Lite picks for that angle. A tilt of 90 degrees or more prints the
/// inline form; a shallower one keeps both rows.
pub(crate) fn tilted_temporal_label(
    tl: &TemporalLabel,
    angle: f64,
    format_tu: TimeUnit,
    edge: crate::common::XEdge,
) -> (String, usize, &'static str, &'static str) {
    let inline = crate::families::temporal::is_steep_tilt(angle);
    // `vl_x_label_anchor`'s precondition is an angle already normalized to
    // [0, 360) (`vega_fit.rs`); `pick_tilt`'s raw ladder value (e.g. -90)
    // is not — bar.rs normalizes before calling it too.
    let (angle_align, angle_baseline) = if angle == 0.0 {
        ("center", edge.baseline())
    } else {
        vega_fit::vl_x_label_anchor(vega_fit::normalize_angle(angle), edge == crate::common::XEdge::Bottom)
    };
    let (text, rows) = if inline {
        (inline_label(tl, format_tu), 1)
    } else {
        (tl.row1.clone(), tl.rows())
    };
    (text, rows, angle_align, if angle != 0.0 { angle_baseline } else { edge.baseline() })
}

// ── Overlay layers (chart.layers) ────────────────────────────────────────────

/// `chart.layers` on a line base: any run of `type: "line"` and `type:
/// "scatter"` layers in authored order (scatter layers need an ungrouped base),
/// each sharing the base's own x (or their own x column) and shared y scale, or
/// every layer on a scale of its own once one pins a side (see
/// `docs/laws-layers.md`). Every other shape (bar layers, wide measures, a
/// support_table, curve=step band mode) refuses by name below.
/// `OverlayLineLayer` and the row/legend/paint
/// machinery live in `families::layers` — bar.rs's shared-scale line layer
/// calls the same code.
fn validate_line_layers(r: &LineResolved, payload: &Payload) -> Result<Vec<OverlayLayer>, String> {
    let mut overlays: Vec<OverlayLayer> = Vec::with_capacity(r.layers.len());
    for raw in &r.layers {
        // Named by its own authored type before a full struct deserialize fails
        // on a missing family-specific field with a much less useful message.
        let layer_type = raw.get("type").and_then(Value::as_str).unwrap_or("");
        match layer_type {
            "line" | "area" => {
                let layer = validate_line_layer(raw, r, payload)?;
                overlays.push(OverlayLayer::Line(Box::new(layer)));
            }
            "scatter" => {
                let layer = validate_scatter_overlay(raw, r, payload)?;
                overlays.push(OverlayLayer::Scatter(Box::new(layer)));
            }
            "bar" => {
                let layer = validate_bar_overlay(raw, r, payload)?;
                overlays.push(OverlayLayer::Bar(Box::new(layer)));
            }
            other => return Err(format!("line: a {other:?} layer is none of line, area, scatter or bar")),
        }
    }
    Ok(overlays)
}

fn validate_line_layer(raw: &Value, r: &LineResolved, payload: &Payload) -> Result<OverlayLineLayer, String> {
    let layer = OverlayLineLayer::parse(raw, "line")?;
    resolve_layer_rows(payload, layer.query_name.as_deref(), r.query_name.as_deref(), "line")?;
    Ok(layer)
}

fn validate_scatter_overlay(raw: &Value, r: &LineResolved, payload: &Payload) -> Result<OverlayScatterLayer, String> {
    let layer = OverlayScatterLayer::parse(raw, "line")?;
    resolve_layer_rows(payload, layer.query_name.as_deref(), r.query_name.as_deref(), "line")?;
    Ok(layer)
}

fn validate_bar_overlay(raw: &Value, r: &LineResolved, payload: &Payload) -> Result<OverlayBarLayer, String> {
    let layer = OverlayBarLayer::parse(raw, "line")?;
    resolve_layer_rows(payload, layer.query_name.as_deref(), r.query_name.as_deref(), "line")?;
    Ok(layer)
}

// ── Public entry point ───────────────────────────────────────────────────────

/// Which plot-rect negotiation a line/area chart takes — decided once, from
/// the payload alone, before any measurement (`docs/laws-line-area.md`).
#[derive(Clone, Copy, PartialEq, Eq, Debug, serde::Serialize)]
pub(crate) enum Shape {
    /// Vega's autosize-fit margin algebra (`vega_fit::fit_plot`): no
    /// endpoint rail, and no support_table unless composed on top over a
    /// right y axis (a left axis takes none).
    Fit,
    /// The endpoint-rail hconcat (`_wrap_hconcat_label_pane`): a rail, a left
    /// y axis, and no support_table unless a composed top strip with the
    /// legend suppressed.
    RailConcat,
}

/// What the plot-rect negotiation hands to the scales and the emission —
/// built once by whichever arm runs, so nothing escapes an arm by mutation.
#[derive(Clone, serde::Serialize)]
pub(crate) struct PlotRect {
    shape: Shape,
    plot_w: f64,
    plot_h: f64,
    left: f64,
    top: f64,
    bottom: f64,
    /// `vega_fit::FitMargins.raw_left`'s own doc comment — real Vega's cell
    /// `x2` reach past `plot_w`, unceiled and symmetric with `left`'s own
    /// (already-ceiled) left reach. `0.0` on the rail-concat and legacy
    /// arms: `compose_facet` never reaches either shape (`FacetFeature`
    /// refuses `multiples` alongside anything that would route there before
    /// a facet panel is ever drawn), so this field is dead on both.
    raw_left: f64,
    /// The same reach past the plot's right edge: `raw_left`, less how far the last datum sits
    /// inside it (a band scale, or a bar layer's bucket-extended scale).
    raw_right: f64,
    /// `vega_fit`'s own title anchor; the legacy arm has none.
    title_pos: Option<(f64, f64)>,
    temporal_labels: Option<Vec<TemporalLabel>>,
    raw_openers: bool,
    x_angle: f64,
    /// How far a rotated x label column reaches below the plot bottom (`0.0`
    /// for flat labels): a facet footer's rotated `<text>` carries no `y`
    /// attribute the composer could measure.
    x_reach: f64,
    x_format_tu: Option<TimeUnit>,
    tick_indices: Option<Vec<usize>>,
    /// Vega resolves the bare temporal axis at runtime against the arm's
    /// final plot rect, so the arm hands its ticks (and the parity mask over
    /// their label text) back this way.
    bare_axis: Option<(Vec<BareTick>, Vec<bool>)>,
    /// Vega's parity overlap on the y labels: the Vega arms record which
    /// labels stay; the legacy arm draws every one.
    y_visible: Option<Vec<bool>>,
    /// The legend's (x, y) in plot coordinates, set by the arm that lays it out.
    legend_pos: (f64, f64),
    y_gutter: f64,
    /// Rail label pane x in plot coordinates (rail-concat arm only): the
    /// concat grid CEILS pane 0's own bound — the mark reach, the last
    /// label's overhang and the chart title's ink, unioned by `pane0_x2` —
    /// and adds `spacing` raw.
    rail_x_rel: Option<f64>,
    /// The canvas is the payload's slot everywhere but the composed
    /// autosize-pad arm, which grows it around the ink instead, and the rail
    /// concat, whose own corrected layout can land a pixel wider (C2 —
    /// `payload.width` is only the TARGET the overshoot pass shrinks pane 0
    /// against).
    canvas_w: f64,
    canvas_h: f64,
    /// Production reserves a `position: top` strip in `padding.top` on an
    /// UNTITLED chart (`apply_chart_support_table_post_pass` — VL emits no
    /// title element for an offset to anchor on); on a titled chart the
    /// strip's own ink grows the margin and `title.offset` carries only the
    /// font-size correction, so the composed arm's own top padding is not
    /// always the payload's.
    composed_pad_t: f64,
    pub(crate) composed_rows: Option<Vec<support_table_strip::ComposedRow>>,
    /// The fit arm's own final `m.right` (`pad_plot`/`fit_plot`'s measured
    /// right-side value-axis-gutter overhang) — a facet panel's own `ink`
    /// union past `plot_right` needs it (mirrors bar's own
    /// `facet_axis_right_overhang`). `0.0` off the rail-concat/legacy arms,
    /// which a facet panel never reaches (an active endpoint rail or a
    /// non-modeled shape both refuse under `multiples` before this seam is
    /// reached).
    facet_axis_right_overhang: f64,
    /// See `facet_compose::PanelGeometry.top_axis_overhang`'s own doc
    /// comment — escapes the fit arm's own `if shape == Shape::Fit { ... }`
    /// block the same way `facet_axis_right_overhang` does (`top_overhang`
    /// is a `std::cell::Cell` local to that block). `0.0` off the rail-
    /// concat/legacy arms, which a facet panel never reaches.
    top_axis_overhang: f64,
}

pub(crate) fn shape(show_rail: bool) -> Shape {
    if show_rail { Shape::RailConcat } else { Shape::Fit }
}

/// The line/area small-multiples composer's own per-panel overrides —
/// every gate is inert (`false`/`true` matching the single-chart
/// behavior) when `facet` is `None`, the single-chart path's only call
/// shape; `line.rs::render_facet` (rows-only, columns-only or a grid) is the
/// one real caller of `Some(FacetPanelOpts)`. Mirrors bar's own
/// `suppress_x_axis`/`value_domain_rows` params (`render_vertical_panel`),
/// split into named gates because a columns or grid facet needs
/// `paint_value_axis`/`fold_axis_gutter` to vary independently of
/// `suppress_x_axis` (only the LAST column of a row paints the shared value
/// axis; only a single-column facet folds the axis gutter into its own
/// row's ink).
pub(crate) struct FacetPanelOpts<'a> {
    /// A facet composite draws its own shared x-axis ONCE, below the last
    /// row — every panel skips its own x-axis ink reservation (`pad_plot`
    /// instead of `fit_plot`) and paint.
    pub(crate) suppress_x_axis: bool,
    /// Only the last column of a row paints the shared value axis; every
    /// other panel still reserves its own gutter (for row alignment) but
    /// draws nothing.
    pub(crate) paint_value_axis: bool,
    /// A single-column (rows-only) facet folds the value-axis gutter into
    /// its own row's ink, the same way bar's `suppress_x_axis` ink union
    /// already does for its rows-only composer; a multi-column facet
    /// reserves that gutter separately (`composite_axis_reserve`) instead.
    pub(crate) fold_axis_gutter: bool,
    /// Real Vega's `row_footer` (a rows-only facet's shared value axis)
    /// anchors at `ceil(cell x2)` (`vega-view-transforms/src/layout/
    /// grid.js:262`, `ceil` from `grid.js:13`'s own `agg` callback) — the
    /// mark's own right overhang past `plot_w`, symmetric with `left_mark_
    /// overhang`'s own left reserve, never `plot_w` alone. `0.0` off the
    /// single-chart wrapper and the composer's own probe pass (where
    /// `left_mark_overhang` isn't measured yet).
    pub(crate) value_axis_right_overhang: f64,
    /// This panel's canonical key, the `panel` an annotation names.
    pub(crate) panel_key: &'a str,
    /// This panel's place in `multiples::regroup` order; keeps clip ids unique.
    pub(crate) panel_ordinal: usize,
    /// `axis_y.mirror` resolves truthy and this facet's shared x is
    /// non-quantitative with no detected temporal cadence — the SAME
    /// "genuinely nominal" test the single-chart fit arm gates on
    /// (`line::layout::fit`'s own `mirror_wants_band`). Real Vega
    /// compiles the shared x scale as BAND, not POINT, whenever the
    /// mirror ghost's own invisible interaction rule is present
    /// (`render_facet`'s own doc comment) — `line::layout::fit` reads this
    /// to switch `pos_at`/`label_x_at`/`band_center`/`x_rule_at` onto band
    /// centers, the SAME position-only law `is_band_step` already carries
    /// for `curve: step`, without engaging that flag's row-doubling
    /// transform. The composer paints the ghost itself (`mirror_active` is
    /// always forced `false` under `Some(facet)`); this flag exists only
    /// so a mirrored panel's own marks/ticks land where the ghost's band
    /// scale actually puts them.
    pub(crate) mirror_band_x: bool,
    /// `multiples.scale: "shared"` (the default): the x point scale and the
    /// y extent/ladder are computed off the WHOLE chart's rows, not just
    /// this panel's own subset.
    pub(crate) shared_domain_rows: &'a [serde_json::Map<String, Value>],
    /// `multiples.scale: "independent"`: the y extent/ladder come from this
    /// panel's own rows and its value axis is embedded in its cell, anchored
    /// at the plot edge; the x scale stays shared.
    pub(crate) independent_scale: bool,
}

/// The minimal per-panel geometry a facet composite needs off a line/area
/// panel — normalized the same way bar's own `PanelGeometry` is, so a future
/// composer reads one shape regardless of which family drew the panel.
pub(crate) struct LinePanelGeometry {
    pub(crate) ink: vega_fit::Bounds,
    /// The widest line of the value axis's rotated title when this panel paints it.
    pub(crate) value_title_w: f64,
    /// The gutter the value-axis ladder reserves — `paint_value_axis: true`
    /// panels draw it; every other panel still measures it for row-height
    /// alignment.
    pub(crate) axis_w_overhang: f64,
    pub(crate) widest_val_tick_label_width: f64,
    pub(crate) val_gutter: f64,
    pub(crate) val_tick_size: f64,
    /// `(y position, formatted label)` for every VISIBLE value tick,
    /// captured regardless of `paint_value_axis` — a composer paints these
    /// itself on whichever panel ends up drawing the shared axis.
    pub(crate) val_ticks: Vec<(f64, String)>,
    /// `facet_compose::PanelGeometry.ghost_labels`.
    pub(crate) ghost_labels: Vec<String>,
    /// The primary value axis's own resolved label paint attributes —
    /// mirrors bar's `PanelGeometry`-adjacent fields of the same name,
    /// carried unconditionally (not just when this panel's own facet is
    /// mirrored) so `render_facet` can build a `facet_compose::GhostStyle`
    /// the same way bar's own composer call does, off any panel.
    pub(crate) val_font_size: f64,
    pub(crate) val_weight: String,
    pub(crate) val_color: String,
    pub(crate) val_family: String,
    pub(crate) val_caption: AxisCaption,
    pub(crate) axis: LineXAxis,
    /// See `facet_compose::PanelGeometry.top_axis_overhang`'s own doc
    /// comment — the same quantity, tracked by this panel's own fit
    /// closure via a `std::cell::Cell`, reset at the start of every
    /// `run_fit()` call (a retry can change `tick_values` between
    /// iterations, and a stale call's overhang must not survive into the
    /// next one's measurement). `0.0` unless `suppress_x_axis` is set and a
    /// tick label's own ink crosses this panel's `y=0`.
    pub(crate) top_axis_overhang: f64,
    /// The chart-wide color legend a facet composer lifts off any panel.
    pub(crate) facet_legend: Option<facet_compose::FacetLegend>,
    /// The group origin of the value axis, when this panel paints it.
    pub(crate) value_axis_origin: Option<(f64, f64)>,
}

/// The single-chart x-axis chrome (tick marks, labels, title — nominal,
/// bare-temporal, temporal, and tilted-temporal branches alike), baked once
/// by `line::layout::fit` at this panel's own resolved `plot_w`/temporal
/// ladder — the analog of bar's `BandXAxis`/`ContinuousXAxis`:
/// `paint`'s own `plot_w` sizes the title band only, tick/label x positions
/// are already baked at construction time (the same law `BandXAxis.band`'s
/// own range bakes at construction, never re-derived from a later `plot_w`).
pub(crate) struct LineXAxis {
    ad: f64,
    use_band_positions: bool,
    x_font_size: f64,
    x_tick_len: f64,
    x_tick_eff: f64,
    x_label_pad: f64,
    x_axis_drop: f64,
    /// The stroke of the baseline rule on the plot edge this axis hangs from, which its ticks continue.
    floor_rule: Option<String>,
    label_rows: usize,
    edge: crate::common::XEdge,
    x_title_text: String,
    caption: AxisCaption,
    title_key: Option<crate::contract::LabelKey>,
    /// Mirrors `axis_tick_xs` — raw (un-crisped, `plot_left`-relative)
    /// positions for every gridline/tick opener, whichever branch below
    /// supplies the label text.
    tick_xs: Vec<f64>,
    body: LineXAxisBody,
    /// See `PlotRect::x_reach`.
    rotated_reach: Option<f64>,
}

enum LineXAxisBody {
    /// `tick_xs` (above) doubles as each bare tick's own label x — both are
    /// `frac_x_at(t.frac)`, the same positions, over the same tick list.
    Bare { ticks: Vec<BareTick>, visible: Vec<bool>, xs: Vec<f64>, angle: f64 },
    Tilted { labels: Vec<TemporalLabel>, xs: Vec<f64>, angle: f64, format_tu: TimeUnit, align: Option<&'static str> },
    FlatTemporal { labels: Vec<TemporalLabel>, xs: Vec<f64>, anchors: Vec<&'static str> },
    /// `left_reach`: how far the leftmost label's ink reaches left of the plot origin
    /// (`PanelXAxis::label_left_reach`).
    Nominal { labels: Vec<String>, xs: Vec<f64>, left_reach: f64, angle: f64, align: Option<&'static str> },
}

/// Vega's `tickRound` (default true) snaps a gridline/tick to a whole
/// pixel before the crisp `+ad` translate; a BAND scale (band-step's x
/// channel) snaps differently — `floor(center) + 0.5`, not `round(pos) +
/// ad` — the same law heatmap's own category ticks carry (`heatmap.rs`'s
/// `AXIS_TRANSLATE` comment: "a label keeps its exact band position ... a
/// tick snaps to a whole pixel first"). One function, shared by both the
/// construction site (`line::layout::fit`'s own fit-pass ink closure) and
/// `LineXAxis::nodes`'s post-construction repaint — one law, one function,
/// never a second reading of it.
fn tick_snap(pos: f64, ad: f64, band_step: bool) -> f64 {
    if band_step {
        pos.floor() + 0.5
    } else {
        pos.round() + ad
    }
}

/// `attrs` with the series' `stroke-dasharray`, when it draws a dash.
fn with_dash(attrs: Attrs, dash: Option<String>) -> Attrs {
    match dash {
        Some(list) => attrs.set("stroke-dasharray", list),
        None => attrs,
    }
}

impl LineXAxis {
    /// See `PanelXAxis::label_left_reach`. Only a nominal axis puts its first
    /// label's half width left of the plot origin; a temporal axis anchors it
    /// flush.
    pub(crate) fn label_left_reach(&self) -> f64 {
        match &self.body {
            LineXAxisBody::Nominal { left_reach, .. } => *left_reach,
            _ => 0.0,
        }
    }

    /// See `PanelXAxis::rotated_footer_reach`.
    pub(crate) fn rotated_footer_reach(&self) -> Option<f64> {
        self.rotated_reach
    }

    /// The axis at the CALLER's own `plot_left`/`plot_w`/`plot_bottom` — the
    /// panel's own (the single-chart wrapper's call), or `facet_compose::
    /// compose_facet`'s shared footer band.
    pub(crate) fn nodes(&self, ax_x: &AxisStyle, plot_left: f64, plot_w: f64, (plot_bottom, floor_y): (f64, f64), fonts: &FontBook) -> Result<Vec<Node>, String> {
        let clip = |text: &str| ax_x.labels.clip(text, fonts);
        let x_rule_at = |pos: f64| tick_snap(pos, self.ad, self.use_band_positions);
        let sign = self.edge.sign();
        // A second label row hangs below the first on either edge (Vega's multi-line text grows downward).
        let rows_at = |anchor: f64, adv: f64| (anchor, anchor + adv);
        let mut items: Vec<Node> = Vec::new();
        if self.x_tick_eff > 0.0 {
            let end_y = plot_bottom + self.x_axis_drop + sign * self.x_tick_len;
            let xs = self.tick_xs.iter().map(|&x| plot_left + x_rule_at(x));
            items.extend(crate::common::x_tick_marks(&ax_x.ticks, self.floor_rule.as_deref(), &[], xs, (floor_y, end_y), 3)?);
        }
        if ax_x.labels.visible.unwrap_or(true) {
            let xf = &ax_x.labels.font;
            let xwt = xf.weight_str();
            let face = |anchor| LabelFace {
                places: 3,
                anchor,
                family: xf.family.as_deref().unwrap_or("sans-serif"),
                size: xf.size_or(11.0),
                weight: &xwt,
                fill: xf.color.as_deref().unwrap_or("#626366"),
            };
            let xsz = xf.size_or(11.0);
            let label_y = plot_bottom
                + self.x_axis_drop
                + sign * (self.x_tick_eff + self.x_label_pad)
                + measure::baseline_shift(self.edge.baseline(), xsz);
            let anchor_y = plot_bottom + self.x_axis_drop + sign * (self.x_tick_eff + self.x_label_pad);
            let edge_half = ax_x.edge_rule_half_width(self.x_tick_eff > 0.0);
            match &self.body {
                LineXAxisBody::Bare { ticks, visible, xs, angle } => {
                    for ((t, &keep), &x) in ticks.iter().zip(visible).zip(xs) {
                        if !keep || t.mark == BareMark::TickOnly {
                            continue;
                        }
                        if *angle != 0.0 {
                            let (align, baseline) = bare_label_anchor(*angle, t.frac, plot_w, self.edge);
                            items.push(rotated_label(
                                (plot_left + x + self.ad, anchor_y, *angle, measure::baseline_shift(baseline, xsz)),
                                &face(svg_text_anchor(align)),
                                (&clip(&t.text)?, ""),
                                false,
                            ));
                            continue;
                        }
                        let row2_text = t.row2.as_deref().filter(|row| !row.is_empty());
                        let (row1_y, row2_y) = rows_at(label_y, xsz + 2.0);
                        let anchor = flush_anchor_px(t.frac * plot_w, plot_w);
                        let gx = plot_left + x + self.ad + edge_nudge(anchor, edge_half);
                        items.push(flat_label((gx, row1_y), &face(anchor), false, &clip(&t.text)?));
                        if let Some(row2) = row2_text {
                            items.push(flat_label((gx, row2_y), &face(anchor), false, &clip(row2)?));
                        }
                    }
                }
                LineXAxisBody::Tilted { labels, xs, angle, format_tu, align } => {
                    let (default_align, baseline) =
                        vega_fit::vl_x_label_anchor(vega_fit::normalize_angle(*angle), true);
                    let text_anchor = svg_text_anchor(align.unwrap_or(default_align));
                    let dy = measure::baseline_shift(baseline, xsz);
                    for (tl, &x) in labels.iter().zip(xs) {
                        let gx = plot_left + x + if self.use_band_positions { 0.0 } else { self.ad };
                        let (text, rows, ..) = tilted_temporal_label(tl, *angle, *format_tu, self.edge);
                        for (row, row_text) in [text.as_str(), tl.row2.as_str()].into_iter().take(rows).enumerate() {
                            let row_dy = dy + row as f64 * vega_fit::label_row_advance(xsz);
                            items.push(rotated_label((gx, anchor_y, *angle, row_dy), &face(text_anchor), (&clip(row_text)?, ""), false));
                        }
                    }
                }
                LineXAxisBody::FlatTemporal { labels, xs, anchors } => {
                    for ((tl, &x), &anchor) in labels.iter().zip(xs).zip(anchors) {
                        let (row1_y, row2_y) = rows_at(label_y, xsz + 2.0);
                        let flush = if flush_anchor_px(x, plot_w) == anchor { edge_nudge(anchor, edge_half) } else { 0.0 };
                        let gx = plot_left + x + if self.use_band_positions { 0.0 } else { self.ad } + flush;
                        items.push(flat_label((gx, row1_y), &face(anchor), false, &clip(&tl.row1)?));
                        if !tl.row2.is_empty() {
                            items.push(flat_label((gx, row2_y), &face(anchor), false, &clip(&tl.row2)?));
                        }
                    }
                }
                LineXAxisBody::Nominal { labels, xs, angle, align, .. } if *angle != 0.0 => {
                    let (align, baseline) = nominal_label_anchor(*angle, self.edge, *align);
                    let text_anchor = svg_text_anchor(align);
                    let dy = measure::baseline_shift(baseline, xsz);
                    for (xv, &x) in labels.iter().zip(xs) {
                        let gx = plot_left + x + if self.use_band_positions { 0.0 } else { self.ad };
                        items.push(rotated_label((gx, anchor_y, *angle, dy), &face(text_anchor), (&clip(xv)?, ""), false));
                    }
                }
                LineXAxisBody::Nominal { labels, xs, align, .. } => {
                    let anchor = match align {
                        Some("left") => "start",
                        Some("right") => "end",
                        _ => "middle",
                    };
                    for (xv, &x) in labels.iter().zip(xs) {
                        let gx = plot_left + x + if self.use_band_positions { 0.0 } else { self.ad };
                        items.push(flat_label((gx, label_y), &face(anchor), false, &clip(xv)?));
                    }
                }
            }
        }
        // The title clears the label block's far row: the last one under a bottom axis, the first over a top one.
        let x_label_baseline = plot_bottom
            + self.x_axis_drop
            + sign * (self.x_tick_eff + self.x_label_pad)
            + measure::baseline_shift(self.edge.baseline(), self.x_font_size)
            + sign * (self.label_rows - 1) as f64 * (self.x_font_size + 2.0);
        items.extend(crate::common::axis_title_flat(
            &self.x_title_text,
            &ax_x.title,
            plot_left + plot_w / 2.0,
            x_label_baseline,
            self.x_font_size,
            self.title_key,
            self.edge,
        ));
        crate::contract::axis_group(crate::contract::AxisChannel::X, &self.caption, (plot_left + 0.5, plot_bottom + self.x_axis_drop), items)
    }
}

impl LineResolved {
    /// A numeric `color` column is a continuous scale, not a set of series: the
    /// chart draws one line (or area) in the scale's color for its first datum, and a
    /// legend of the scale names it.
    fn use_numeric_color(&mut self, rows: &[serde_json::Map<String, Value>]) {
        let quantitative = self.resolved_channels.get("color").is_some_and(|ch| ch.is_continuous());
        let Some(field) = self.color.clone().filter(|_| quantitative && self.wide_measures.is_empty()) else { return };
        let Some(color) = crate::families::layers::numeric_layer_color(rows, &field) else { return };
        self.color = None;
        self.numeric_color = Some((color, field));
    }

    fn numeric_base(&self) -> Result<Option<(&crate::families::color_scale::ContinuousColor, bool, String, Option<&crate::common::FormatSlot>)>, String> {
        let Some((color, field)) = self.numeric_color.as_ref() else { return Ok(None) };
        Ok(Some((color, false, legend_title_text(field, &self.legend, &self.field_labels)?, self.color_format.as_ref())))
    }

    pub(crate) fn from_payload(payload: &Payload) -> Result<Self, String> {
        let mut r: Self = Deserialize::deserialize(&payload.resolved).map_err(|e| format!("line payload: {e}"))?;
        r.series_palette = payload.series_palette(&r.palette);
        r.link_context = payload.link_context.clone();
        r.link_y = r.y.clone();
        r.link_color = r.color.clone();
        r.link_x_labeled = crate::families::temporal::has_labeled_bucket(&payload.rows, &r.x);
        Ok(r)
    }
}

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    render_inner(payload, fonts, false)
}

/// The data range the rail cascade clamps into. A stacked chart cascades over
/// `_stacked_y_domain` (`endpoint_labels.py`): `[0, max column total]`, or
/// `[0, 1]` normalized, whatever domain is authored. Otherwise
/// `_y_domain`: the authored domain when ascending, else the raw data
/// extent, never the niced rendered one (`f64::clamp` panics on `lo > hi`).
fn rail_cascade_bounds(
    stacked: bool,
    normalize: bool,
    max_col_total: f64,
    authored: Option<(f64, f64)>,
    (data_min, data_max): (f64, f64),
) -> (f64, f64) {
    if stacked {
        (0.0, if normalize { 1.0 } else { max_col_total })
    } else {
        match authored {
            Some((lo, hi)) if hi >= lo => (lo, hi),
            _ => (data_min, data_max),
        }
    }
}

/// A stacked chart's `(x, series)` index: `None` is a row that EXISTS with a
/// null measure; an absent cell has no entry. The last row of a cell wins.
type StackData = HashMap<(String, String), Option<f64>>;

fn stack_data(
    rows: &[serde_json::Map<String, Value>],
    x_field: &str,
    color_field: Option<&str>,
    y_field: &str,
) -> StackData {
    let mut data = StackData::new();
    for row in rows {
        let series = color_field.map(|cf| row_str(row, cf)).unwrap_or_default();
        data.insert((row_str(row, x_field), series), row_num(row, y_field));
    }
    data
}

/// One x column of the stack over `baseline_order`.
struct StackColumn {
    /// The column's net sum over `baseline_order`.
    total: f64,
    /// `bounds[i]` = the (start, end) a `zero` stack gives the series at
    /// `baseline_order[i]`: positives pile up from 0, negatives down from 0;
    /// the area's `y` edge is `end`.
    bounds: Vec<(f64, f64)>,
    /// The positive pile's height.
    pos: f64,
    /// The negative pile's depth.
    neg: f64,
}

/// Production's `sparse_band_transforms` impute pass fills both a null-measure
/// row and a genuinely absent row with 0 -- "a null and an absent row reach the
/// same wedge" (`_layers.py`).
fn stack_column(data: &StackData, x: &str, baseline_order: &[String], signed: bool) -> StackColumn {
    let mut sums = crate::common::StackZero::new(0.0);
    let mut total = 0.0_f64;
    let mut bounds: Vec<(f64, f64)> = Vec::with_capacity(baseline_order.len());
    for s in baseline_order {
        let v = match data.get(&(x.to_string(), s.clone())) {
            Some(Some(v)) => *v,
            Some(None) | None => 0.0,
        };
        // `center` and `normalize` pile magnitudes (`vega-transforms/Stack.js`).
        let v = if signed { v } else { v.abs() };
        total += v;
        bounds.push(sums.push(v));
    }
    StackColumn { total, bounds, pos: sums.pos_total(), neg: sums.neg_total() }
}

/// The stacked value nearest the domain's top end, whose stroke overhang the
/// top margin reserves: the tallest column total on an ascending domain, the
/// baseline side on a descending one; every `normalize` column ends at 1.
fn stacked_top_datum(ascending: bool, normalize: bool, max_col_total: f64, min_col_floor: f64) -> f64 {
    if normalize {
        if ascending { 1.0 } else { 0.0 }
    } else if ascending {
        max_col_total
    } else {
        min_col_floor
    }
}



// Called by area.rs with draw_areas=true.
pub(crate) fn render_inner(
    payload: &Payload,
    fonts: &FontBook,
    draw_areas: bool,
) -> Result<String, Diagnostic> {
    let bound = bind::bind(payload, draw_areas)?;
    // A facet composite has no single plot rect, so it is composed panel by panel here, at the
    // string boundary; `lib.rs::render_layout` refuses it for `--dump-layout`.
    if bound.r.multiples.is_some() {
        return render_facet(bound, fonts);
    }
    print_bound(&bound, fonts).map(|(svg, _)| svg)
}

/// `bound` laid out in the payload's card, its WARNs forwarded, and printed, with the plot rect.
fn print_bound(bound: &bind::Bound, fonts: &FontBook) -> Result<(String, PlotRect), Diagnostic> {
    let measure = Measure { fonts };
    let cell = Rect { x: 0.0, y: 0.0, width: bound.payload.width, height: bound.payload.height };
    let (laid_out, plot) = layout::layout_with_plot(bound, cell, &measure)?;
    laid_out.diagnostics.into_iter().for_each(warn);
    Ok((print(&laid_out.nodes), plot))
}

/// The SVG together with the plot rect its negotiation settled on — what
/// `lib.rs::render_layout` dumps for audit.
pub(crate) fn render_with_layout(
    payload: &Payload,
    fonts: &FontBook,
    draw_areas: bool,
) -> Result<(String, PlotRect), Diagnostic> {
    print_bound(&bind::bind(payload, draw_areas)?, fonts)
}

/// Where `row`'s `field` sits in an x domain.
fn domain_position(x_domain: &[String], row: &serde_json::Map<String, Value>, field: &str) -> Option<usize> {
    let x = row_str_ref(row, field);
    x_domain.iter().position(|d| *d == x)
}

/// Small-multiples composite entry point for line/area — `render_inner`'s
/// own gate calls this instead of `layout` whenever
/// `payload.resolved.multiples` is set. Scope this arm: vertical, nominal x
/// only, no color channel, single-series fill per panel, right-side value
/// axis, `multiples.scale: "shared"`, a fully populated rows x columns grid
/// (rows-only, columns-only or both), line and area alike, both through the
/// SAME `line::layout::fit(draw_areas)` call and the family-generic
/// `facet_compose::compose_facet`. A chart-level title
/// is hoisted onto the facet root and painted once by the composer
/// (`FacetTitle`), never per panel. Refuses (by name,
/// before any panel renders) rather than approximate: a color channel (a
/// facet's own color legend sits ONCE over the composite — unmodeled),
/// `support_table`, wide measures, a non-right value axis, `style.dashes`.
/// `axis_y.mirror` renders: `mirror_axis.py`'s own
/// ghost mechanism adds an invisible interaction `rule` mark sharing this
/// composer's nominal `x` field, and real Vega compiles that shared x
/// scale as BAND instead of POINT (`docs/laws-line-area.md`'s
/// band-position section) — `FacetPanelOpts.mirror_band_x` threads that
/// fact into each panel so its marks/ticks land at the band's own
/// `step*(i+0.5)` centers. A ghost `format` or `expr` still refuses here — a
/// separate unmodeled shape, unrelated to the point-to-band flip. Only the LAST column of a row paints
/// the shared value axis (real Vega's `row_footer`, one per ROW), and only a
/// single-column facet folds that gutter into its panels' ink — a multi-column
/// row reserves it once at the composer (`composite_axis_reserve`).
/// `multiples.scale: "independent"` paints and folds every panel's own value
/// axis over its own y extent, and a tilted or bare temporal x reports the
/// reach of its rotated labels (`LineXAxis::rotated_footer_reach`) for the
/// composer to size the footer by.
fn render_facet(mut bound: bind::Bound, fonts: &FontBook) -> Result<String, Diagnostic> {
    let draw_areas = bound.draw_areas;
    let payload: &Payload = &bound.payload;
    // An area facet reuses this SAME composer — `line::layout::fit` is
    // already `draw_areas`-generic (the backdrop/halo/fill paint sites all
    // read `bound.r.canvas`/`bound.r.background`, matching the single-chart arms), and
    // `FacetPanelOpts`/`facet_compose::compose_facet` have no line/area
    // branching. `bound.r.stack != "none"` below (unprobed against production's
    // stacked-area facet shape — no corpus/ladder board pairs the two) is
    // the only area-specific refusal this arm still needs, and it's already
    // generic.
    let fam = if draw_areas { "area" } else { "line" };
    let facet_x_scale = match crate::families::temporal::infer_vega_type(&payload.rows, &bound.r.x) {
        VegaType::Nominal | VegaType::Ordinal => crate::ViewScale::Discrete,
        VegaType::Quantitative | VegaType::Temporal => crate::ViewScale::Continuous,
    };
    let legend_edge = if (bound.r.color.is_some() || bound.r.numeric_color.is_some() || !bound.r.layers.is_empty()) && bound.r.legend.visible {
        facet_compose::facet_legend_edge(&bound.r.legend.position)
    } else {
        facet_compose::LegendEdge::Top
    };
    // `axis_y.mirror`'s own ghost mechanism adds an extra invisible
    // interaction `rule` mark (`{"type": "rule", "opacity": 0}`, sharing the
    // SAME nominal `x` field with no `x2`) to the compiled VL spec whenever
    // `axis_y.mirror` is truthy, and real Vega then compiles the shared `x`
    // scale as BAND, not POINT: vertices/ticks land at `step*(i+0.5)` (band
    // centers, `step = plot_w / n`) instead of `i/(n-1) * plot_w` (point
    // scale, padding 0) — for an area mark as for a line (`test_vl_probe.py::
    // test_area_under_the_mirror_rule_lands_on_band_centers`).
    // `line::layout::fit`'s own `mirror_wants_band`/`use_band_positions`
    // port that law (`laws-line-area.md`'s band-position section).
    // `has_mirror` here mirrors bar's own `FacetSpec.has_mirror` —
    // `compose_facet` paints the shared ghost off it and `render_pass` below
    // threads `mirror_band_x` into every panel so its own marks/ticks land
    // where that ghost's band scale puts them. The relabel forms refuse
    // inside `AxisStyle::mirror_requested`, regardless of family; the
    // composer's ghost repaints the primary's labels, so a ghost `format`
    // refuses there too.
    let has_mirror = bound.r.style.axis_y.mirror_requested(fam)?;
    let mc = bound.r.multiples.clone().expect("render_inner's own gate checked payload.resolved.multiples is set");
    let independent = mc.scale == "independent";
    if independent && has_mirror {
        return Err(multiples::independent_mirror_error(payload.chart_id()?));
    }
    if bound.r.panel_axes.is_empty() {
        return Err(format!(
            "{fam}: multiples with no baked panel_axes — resolve() should always supply at least one"
        ).into());
    }
    let axes = bound.r.panel_axes.clone();
    let mut panels = multiples::regroup(&axes, &payload.rows).map_err(|e| format!("{fam}: {e}"))?;
    // A color that is also a partition column still names the series inside its panel.
    if let Some(color_axis) = axes.iter().position(|a| Some(a.field.as_str()) == bound.r.color.as_deref()) {
        for panel in &mut panels {
            let value = panel.key[color_axis].clone();
            for row in &mut panel.rows {
                row.insert(axes[color_axis].field.clone(), value.clone());
            }
        }
    }
    if panels.is_empty() {
        return Err(format!("{fam}: multiples produced zero panels — no row matched the baked panel_axes").into());
    }
    let panel_rows_n = multiples::panel_axis_cardinality(&axes, mc.rows.as_deref());
    let panel_cols_n = multiples::panel_axis_cardinality(&axes, mc.columns.as_deref());
    let missing = multiples::empty_leading_cells(panels.len(), panel_rows_n, panel_cols_n)?;
    let row_axis_idx = mc.rows.as_deref().and_then(|f| axes.iter().position(|a| a.field == f));
    let col_axis_idx = mc.columns.as_deref().and_then(|f| axes.iter().position(|a| a.field == f));
    let header_font_family = bound.r.legend.label.font.family.clone().unwrap_or_else(|| "sans-serif".into());
    // `multiples.scale: "shared"` (the only mode reached here — `independent`
    // is refused above): the x point scale and the y extent/ladder are
    // computed off the WHOLE chart's rows, fed to `line::layout::fit` as an
    // explicit override — `FacetPanelOpts.shared_domain_rows`.
    let shared_domain_rows = payload.rows.as_slice();
    // The title belongs to the facet root, not to any panel: built against
    // the padded card, then taken off `r` so no panel reserves or paints it.
    let pad = payload.padding.as_ref();
    let (title_text, subtitle_text) = (bound.r.title.clone(), bound.r.subtitle.clone());
    let (title_font, title_style) = (bound.r.style.title_font.clone(), bound.r.title_style.clone());
    let title_src = facet_title::TitleSource {
        family: fam,
        title: title_text.as_deref(),
        subtitle: subtitle_text.as_deref(),
        title_font: title_font.as_ref(),
        title_style: title_style.as_ref(),
    };
    let title_plan = match facet_title::layout_title(
        &title_src,
        fonts,
        payload.width - pad.map_or(0.0, |p| p.left + p.right),
    )? {
        facet_title::TitleLayout::Plan(plan) => {
            plan.warn_truncated(&bound.r.id);
            Some(plan)
        }
        facet_title::TitleLayout::Untitled => None,
    };
    bound.r.title = None;
    bound.r.subtitle = None;
    // A left value axis rides the composer's `row_header`, which also paints its title.
    let left_primary = !independent && bound.r.style.axis_y.position.as_deref() == Some("left");
    let left_title_text = crate::common::axis_title_text(&bound.r.y, bound.r.y_label.as_deref(), &bound.r.field_labels)?;
    let left_title_style = bound.r.style.axis_y.title.clone();
    if left_primary {
        bound.r.style.axis_y.title.visible = Some(false);
    }

    let render_pass = |pw: f64, ph: f64, value_axis_right_overhang: f64| -> Result<Vec<(String, facet_compose::PanelGeometry)>, Diagnostic> {
        panels
            .iter()
            .enumerate()
            .map(|(panel_i, panel)| -> Result<(String, facet_compose::PanelGeometry), Diagnostic> {
                let panel_key = serde_json::to_string(&panel.key).expect("a panel key re-serializes");
                let facet_opts = FacetPanelOpts {
                    panel_key: &panel_key,
                    panel_ordinal: panel_i,
                    suppress_x_axis: true,
                    // Real Vega's shared value axis (`row_footer`) appears once per ROW, at
                    // that row's last column; a multi-column row reserves its gutter once at
                    // the composer (`composite_axis_reserve`), never folded into a panel.
                    paint_value_axis: independent || (missing + panel_i) % panel_cols_n == panel_cols_n - 1,
                    fold_axis_gutter: independent || panel_cols_n == 1,
                    value_axis_right_overhang,
                    mirror_band_x: has_mirror,
                    shared_domain_rows,
                    independent_scale: independent,
                };
                // Production's own compile fails on a pinned layer inside small
                // multiples (vl-convert: unrecognized scale `child_layer_0_y`).
                if YSides::of(&bound.overlays).is_some() {
                    return Err(crate::refuse!("{fam}: multiples with a dual-axis layer not supported").into());
                }
                let layout::Fitted { svg: card, plot, geometry: geom, cuts, .. } =
                    layout::fit(&bound, payload, &panel.rows, (pw, ph), None, fonts, Some(&facet_opts))?;
                let cuts = cuts.expect("a facet panel is fitted with its cuts");
                Ok((
                    print::print(&[card]),
                    facet_compose::PanelGeometry {
                        content_start: cuts.content_start,
                        svg_end: cuts.svg_end,
                        ink: geom.ink,
                        axis_w_overhang: geom.axis_w_overhang,
                        // `plot.left` is `m.left` alone (this call passes
                        // `pad: None`, so the panel's own internal `pad_l`
                        // is 0): a point scale's own left mark overhang
                        // (`axis_x.scale.padding: 0` plus stroke width)
                        // lands entirely here.
                        left_mark_overhang: plot.left,
                        plot_top: plot.top,
                        raw_right_reach: plot.raw_right,
                        widest_val_tick_label_width: geom.widest_val_tick_label_width,
                        val_gutter: geom.val_gutter,
                        val_tick_size: geom.val_tick_size,
                        val_ticks: geom.val_ticks,
                        ghost_labels: geom.ghost_labels,
                        val_tick_marks: Vec::new(),
                        // Built unconditionally off this panel's own
                        // primary label paint attributes, mirroring bar's
                        // own `render_facet` call — `compose_facet`'s
                        // ghost-paint loop only reads it `if spec.
                        // has_mirror`.
                        mirror_style: Some(facet_compose::GhostStyle {
                            caption: geom.val_caption,
                            val_font_size: geom.val_font_size,
                            val_weight: geom.val_weight,
                            val_color: geom.val_color,
                            val_family: geom.val_family,
                        }),
                        axis: facet_compose::PanelXAxis::Line(geom.axis),
                        top_axis_overhang: geom.top_axis_overhang,
                        value_title_w: geom.value_title_w,
                        legend: geom.facet_legend,
                        value_axis: geom.value_axis_origin.map(|origin| facet_compose::ValueAxisInk { range: cuts.value_axis(), origin }),
                    },
                ))
            })
            .collect()
    };

    let spec = facet_compose::FacetSpec {
        family: fam,
        panels: &panels,
        axes: &axes,
        panel_rows_n,
        panel_cols_n,
        row_axis_idx,
        col_axis_idx,
        has_mirror,
        left_axis_only: left_primary && !has_mirror,
        left_title: left_primary.then_some((left_title_text.as_str(), &left_title_style)),
        mirror_in_panel: false,
        own_value_axes: independent,
        y_axes_in_cells: independent,
        extra_axis_px: 0.0,
        cell_x_axis: false,
        axis_x: &bound.r.style.axis_x,
        axis_y: &bound.r.style.axis_y,
        header_font_family: &header_font_family,
        background: bound.r.background.as_str(),
        x_scale: facet_x_scale,
    };
    // One chart-wide legend: every panel builds the same one; the first
    // render at the naive width lifts it.
    let facet_legend = if (bound.r.color.is_some() || bound.r.numeric_color.is_some() || !bound.r.layers.is_empty()) && bound.r.legend.visible {
        let naive_w = multiples::panel_width(payload, panel_cols_n, has_mirror, 0.0, facet_x_scale)?;
        let naive_h = payload.height / panel_rows_n as f64;
        render_pass(naive_w, naive_h, 0.0)?.into_iter().next().and_then(|(_, g)| g.legend)
    } else {
        None
    };
    let chrome = facet_compose::FacetChrome {
        title: title_plan.as_ref().map(|plan| facet_compose::TitleChrome {
            ink: plan.ink.clone(),
            paint: Box::new(|svg, x, y| plan.paint(&title_src, svg, x, y)),
        }),
        legends: facet_legend.as_ref().map(|lg| facet_compose::LegendChrome {
            edge: legend_edge,
            width: Box::new(|_, _| lg.box_w),
            height: Box::new(|_| lg.box_h),
            paint: Box::new(|svg, x, y, _, _| svg.push_str(&print::print(&[lg.placed(x, y)]))),
        }).into_iter().collect(),
    };
    let chrome = (chrome.title.is_some() || !chrome.legends.is_empty()).then_some(chrome);
    Ok(facet_compose::compose_facet_with_chrome(payload, fonts, &spec, chrome.as_ref(), &render_pass)?)
}

/// d3's `count` ticks over a numeric point domain's span, each with its
/// fraction of the point range: interpolated between the two points it falls
/// between, so a tick sits where the line passes that value.
fn quantitative_x_ticks(domain: &[String], count: usize) -> Result<Vec<(String, f64)>, String> {
    let numbers = domain
        .iter()
        .map(|v| v.parse::<f64>().map_err(|_| format!("quantitative x value {v:?} is not a number")))
        .collect::<Result<Vec<f64>, String>>()?;
    let (Some(lo), Some(hi)) = (numbers.iter().copied().reduce(f64::min), numbers.iter().copied().reduce(f64::max))
    else {
        return Ok(Vec::new());
    };
    if numbers.len() < 2 || lo == hi {
        return Ok(Vec::new());
    }
    let last = (numbers.len() - 1) as f64;
    Ok(d3rs::ticks::d3_ticks(lo, hi, count)
        .into_iter()
        .filter_map(|t| {
            let j = numbers.windows(2).position(|w| (w[0].min(w[1])..=w[0].max(w[1])).contains(&t))?;
            let (a, b) = (numbers[j], numbers[j + 1]);
            let at = if a == b { j as f64 } else { j as f64 + (t - a) / (b - a) };
            Some((crate::annotation::model::Scalar::Number(t).text(), at / last))
        })
        .collect())
}


fn y_family(ax_y: &AxisStyle) -> &str {
    ax_y.labels.font.family.as_deref().unwrap_or("sans-serif")
}

fn y_font_size(ax_y: &AxisStyle) -> f64 {
    ax_y.labels.font.size_or(11.0)
}

/// L9/A2: Vega's `labelFlush` keys on the label's own scale POSITION landing
/// exactly at a range end, never on its domain index — which is the only
/// test available for the bare temporal axis, whose ticks are dates rather
/// than domain members.
/// Vega's `labelFlush` (`flush(range, pos, 1, ...)`), the one law for a label's edge anchor: a label within 1px
/// of a range end flushes to it, the nearer end winning a tie, so a collapsed plot ends every label. `span` is
/// the range width and `pos` the tick's own position in it.
pub(crate) fn flush_anchor_px(pos: f64, span: f64) -> &'static str {
    let (near_start, near_end) = (pos.abs(), (span - pos).abs());
    if near_start < near_end && near_start <= 1.0 {
        "start"
    } else if near_end <= 1.0 {
        "end"
    } else {
        "middle"
    }
}

/// How far an edge-anchored label moves outward so its advance box ends at the outer edge of the rule under it
/// (`half` its half stroke); an unflushed label stays put.
pub(crate) fn edge_nudge(anchor: &str, half: f64) -> f64 {
    match anchor {
        "start" => -half,
        "end" => half,
        _ => 0.0,
    }
}

/// [`flush_anchor`] in `vega_fit::text_ink`'s alignment vocabulary.
/// What the bare axis's labels do under `_temporal_layout`'s verdict on the
/// grain `detect_time_unit` finds: its tilt, and whether `labelOverlap` thins
/// them. A layout that still collides after every strategy hands the thinning
/// back to Vega when `skip` allows it (`_defer_thinning_when_uncertain`). No
/// such grain leaves Vega-Lite's own flat, parity-thinned default.
///
/// An authored angle bypasses the overlap ladder (`resolve_axis_x_overlap`): the labels sit at it, thinned only
/// where the axis authors no `overlap` block.
pub(crate) fn bare_label_layout(
    layout: Option<&ResolvedVegaLabels>,
    skip: bool,
    labels: &crate::common::AxisLabels,
) -> (f64, bool) {
    match labels.angle {
        Some(angle) => (angle, labels.overlap.is_none()),
        None => layout.map_or((0.0, true), |l| (l.angle, l.parity || (l.collision.is_some() && skip))),
    }
}

/// The widths a bare temporal axis's tick labels measure: the wider row of a two-row label, capped at the label limit.
fn bare_label_widths(ticks: &[BareTick], fonts: &FontBook, family: &str, font_size: f64, limit: f64) -> Result<Vec<f64>, String> {
    ticks
        .iter()
        .map(|t| {
            let mut w = fonts.measure_shaped(&t.text, family, font_size)?;
            if let Some(row2) = &t.row2 {
                w = w.max(fonts.measure_shaped(row2, family, font_size)?);
            }
            Ok(w.min(limit))
        })
        .collect()
}

/// The ink of a bare temporal axis's tick labels under their tilt, and the labels `labelOverlap: parity` keeps:
/// Vega runs the parity pass over the rotated ink.
#[allow(clippy::too_many_arguments)]
fn bare_label_ink(
    ticks: &[BareTick],
    widths: &[f64],
    angle: f64,
    parity: bool,
    separation: f64,
    font_size: f64,
    x_at: impl Fn(f64) -> f64,
    plot_w: f64,
    row_y: f64,
    edge: crate::common::XEdge,
) -> (Vec<vega_fit::Bounds>, Vec<bool>) {
    let ink: Vec<vega_fit::Bounds> = ticks
        .iter()
        .zip(widths)
        .map(|(t, &w)| {
            let x = x_at(t.frac);
            let (align, baseline) = bare_label_anchor(angle, t.frac, plot_w, edge);
            let mut flat = vega_fit::text_ink(x, row_y, w, font_size, align, baseline);
            flat.y2 += (t.rows() - 1) as f64 * (font_size + 2.0);
            if angle != 0.0 { flat.rotate(angle, x, row_y) } else { flat }
        })
        .collect();
    // A boundary tick that paints no label neither blocks nor survives the parity pass.
    let labeled: Vec<usize> = (0..ink.len()).filter(|&i| ticks[i].mark != BareMark::TickOnly).collect();
    let mut visible = vec![false; ink.len()];
    let kept = if parity {
        vega_fit::parity_visible(&labeled.iter().map(|&i| ink[i].clone()).collect::<Vec<_>>(), separation)
    } else {
        vec![true; labeled.len()]
    };
    for (&i, keep) in labeled.iter().zip(kept) {
        visible[i] = keep;
    }
    (ink, visible)
}

/// A bare tick label's align and baseline: Vega-Lite's rotated-axis anchor
/// under a tilt, else the flat axis's edge flush.
pub(crate) fn bare_label_anchor(angle: f64, frac: f64, plot_w: f64, edge: crate::common::XEdge) -> (&'static str, &'static str) {
    if angle == 0.0 {
        (flush_align(frac * plot_w, plot_w), edge.baseline())
    } else {
        vega_fit::vl_x_label_anchor(vega_fit::normalize_angle(angle), edge == crate::common::XEdge::Bottom)
    }
}

/// A point-scale label's anchor: the angle's own, but for an authored `labelAlign`.
fn nominal_label_anchor(angle: f64, edge: crate::common::XEdge, authored: Option<&'static str>) -> (&'static str, &'static str) {
    let (align, baseline) = vega_fit::vl_x_label_anchor(vega_fit::normalize_angle(angle), edge == crate::common::XEdge::Bottom);
    (authored.unwrap_or(align), baseline)
}

pub(crate) fn flush_align(pos: f64, span: f64) -> &'static str {
    match flush_anchor_px(pos, span) {
        "start" => "left",
        "end" => "right",
        _ => "center",
    }
}

/// Select a series fill color: the board's plan for `scale`'s field when it
/// binds one, else palette[palette_domain index] for multi (Vega's ordinal
/// scale cycles a short range by index), or the series string itself (which
/// is set to single_series_fill) for single-series.
fn series_fill(
    series: &str,
    palette_domain: &[String],
    palette: &[String],
    is_multi: bool,
    scale: Option<&crate::common::CategoryColorScale>,
) -> Result<String, String> {
    if !is_multi {
        return Ok(series.to_string()); // single_series_fill was stored as the "series" key
    }
    crate::common::series_color(series, palette_domain, palette, scale)
}

/// The color channel's title in Vega-Lite's own description of a mark: an area names the field as it is written
/// (or its authored label), where a line's is cased as a title.
fn description_color_title(fam: &str, color_field: &str, legend: &crate::families::legend::LegendConfig, field_labels: &crate::common::FieldLabels) -> Result<String, String> {
    match fam {
        "area" => Ok(field_labels.get(color_field).cloned().unwrap_or_else(|| color_field.to_string())),
        _ => crate::families::legend::color_channel_title(color_field, legend, field_labels),
    }
}

#[cfg(test)]
mod tests {
    use super::{render_with_layout, shape, FacetPanelOpts, LineXAxisBody, Shape};
    use crate::diagnostic::Diagnostic;
    use crate::families::cartesian::cascade_rail_anchors;
    use crate::common::VALUE_AXIS_BREATHING_ROOM;
    use measure::FontBook;
    use crate::Payload;
    use std::collections::HashMap;

    fn names(placed: &[(String, f64)]) -> Vec<&str> {
        placed.iter().map(|(n, _)| n.as_str()).collect()
    }


    /// Renders in the en-US time vocabulary the fixtures' dates are drawn in; the
    /// engine entry point scopes the board's own.
    fn render(payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
        let _en = crate::time_locale::en_us_scope();
        super::render(payload, fonts)
    }

    fn render_inner(payload: &Payload, fonts: &FontBook, draw_areas: bool) -> Result<String, Diagnostic> {
        let _en = crate::time_locale::en_us_scope();
        super::render_inner(payload, fonts, draw_areas)
    }

    const TEST_FONT: &str = "'Inter Variable', Inter, sans-serif";

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    /// The composed arm grows its canvas around the strip's ink, so
    /// `render_fitted` shrinks the width and re-renders; the dump must
    /// describe THAT render. Deletion counterfactual: dumping the caller's
    /// own payload instead of the fitted one reports the probe's rect.
    /// `labelFlush` is a pixel test against the range ends: a tick within 1px flushes, the nearer end wins a
    /// tie, a collapsed range flushes right.
    #[test]
    fn a_label_within_a_pixel_of_a_range_end_flushes_to_it() {
        assert_eq!(crate::families::line::flush_anchor_px(0.0, 400.0), "start");
        assert_eq!(crate::families::line::flush_anchor_px(0.35, 400.0), "start");
        assert_eq!(crate::families::line::flush_anchor_px(1.5, 400.0), "middle");
        assert_eq!(crate::families::line::flush_anchor_px(399.4, 400.0), "end");
        assert_eq!(crate::families::line::flush_anchor_px(0.0, 0.0), "end");
    }

    #[test]
    fn the_layout_dump_describes_the_width_corrected_render_of_a_composed_strip_chart() {
        let _en = crate::time_locale::en_us_scope();
        let mut payload_json = make_line_payload(true, false);
        payload_json["resolved"]["chart_type"] = serde_json::json!("line");
        payload_json["resolved"]["id"] = serde_json::json!("dump_composed");
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "signups", "format": ",d", "label": "Signups"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let fonts = test_fonts();
        let svg = crate::render_fitted(&payload, &fonts).expect("renders").svg;
        let layout = crate::render_layout(&payload, &fonts).expect("dumps");
        let (w, h) = canvas_dims(&svg);
        assert_eq!(layout["canvas_w"].as_f64().unwrap(), w);
        assert_eq!(layout["canvas_h"].as_f64().unwrap(), h);
        assert_eq!(layout["shape"], "Fit");
        let (_, uncorrected) = render_with_layout(&payload, &fonts, false).unwrap();
        assert!(uncorrected.canvas_w > payload.width, "fixture must overshoot the slot");
        assert_ne!(layout["plot_w"].as_f64().unwrap(), uncorrected.plot_w);
    }

    /// A bottom strip under an endpoint rail rides pane 0 below the axis band.
    #[test]
    fn a_bottom_support_table_strip_under_an_endpoint_rail_grows_the_canvas() {
        use serde_json::json;
        let mut p = make_rail_strip_payload();
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = json!(false);
        p["resolved"]["support_table"] = json!({"entries": [{"per_series": "signups", "format": ",d"}]});
        p["resolved"]["effective_support_table_style"] = support_table_style_json_bottom();
        p["resolved"]["support_table_axis_offset"] = json!(20.0);
        let p_height = p["height"].as_f64().unwrap();
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let (_, h) = canvas_dims(&svg);
        assert!(h > p_height, "the strip rows reach below the plot: {svg}");
    }

    /// Production keeps a bottom strip's plot at its declared height and grows
    /// the canvas under the axis (`padding.bottom` carries the strip and the
    /// axis gap).
    #[test]
    fn a_bottom_support_table_strip_grows_the_canvas_on_either_axis_side() {
        let mut payload_json = make_line_payload(true, false);
        payload_json["resolved"]["chart_type"] = serde_json::json!("line");
        payload_json["resolved"]["id"] = serde_json::json!("bottom_strip");
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "signups", "format": ",d", "label": "Signups"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json_bottom();
        payload_json["resolved"]["support_table_axis_offset"] = serde_json::json!(20.0);
        payload_json["resolved"]["title"] = serde_json::Value::Null;
        payload_json["resolved"]["subtitle"] = serde_json::Value::Null;
        let h = payload_json["height"].as_f64().unwrap();
        let svg = render(&Payload::deserialize(&payload_json).unwrap(), &test_fonts())
            .expect("a bottom strip renders");
        let (_, canvas_h) = canvas_dims(&svg);
        assert!(canvas_h >= h + 20.0 + 30.0, "axis gap and strip are added under the declared height: {canvas_h} vs {h}");
        payload_json["resolved"]["title"] = serde_json::json!("A title");
        let titled = render(&serde_json::from_value::<Payload>(payload_json).unwrap(), &test_fonts())
            .expect("a titled bottom strip renders");
        assert!(titled.contains(">A title<"), "{titled}");
        let mut left = make_line_payload(false, false);
        left["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "signups", "format": ",d", "label": "Signups"}]
        });
        left["resolved"]["effective_support_table_style"] = support_table_style_json_bottom();
        left["resolved"]["support_table_axis_offset"] = serde_json::json!(20.0);
        let svg = render(&serde_json::from_value::<Payload>(left).unwrap(), &test_fonts()).expect("a left axis takes the pad arm");
        let (_, left_h) = canvas_dims(&svg);
        assert!(left_h >= h + 20.0 + 30.0, "{left_h} vs {h}");
    }

    /// A nominal x axis rotates its labels about their anchor at the authored angle,
    /// anchored by Vega-Lite's rotated-axis default, and the plot gives up the width
    /// the last label's rotated ink reaches.
    #[test]
    fn an_authored_angle_on_a_nominal_x_axis_rotates_its_labels() {
        let mut p = make_line_payload(true, false);
        for (row, name) in p["rows"].as_array_mut().unwrap().iter_mut().zip(["North", "South", "East"]) {
            row["week"] = serde_json::json!(name);
        }
        let flat = render(&Payload::deserialize(&p).unwrap(), &test_fonts()).unwrap();
        assert!(!flat.contains("rotate(45)"), "{flat}");
        p["resolved"]["style"]["axis_x"]["labels"]["angle"] = serde_json::json!(45.0);
        let tilted = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).unwrap();
        assert_eq!(tilted.matches("text-anchor=\"start\" transform=\"translate(").count(), 3, "{tilted}");
        assert_eq!(tilted.matches("rotate(45) translate(0,9)").count(), 3, "{tilted}");
    }

    /// A nominal x axis paints each band through an authored `labels.expr`.
    #[test]
    fn an_authored_labels_expr_rewrites_a_nominal_x_axis() {
        let mut p = make_line_payload(true, false);
        for (row, name) in p["rows"].as_array_mut().unwrap().iter_mut().zip(["North", "South", "East"]) {
            row["week"] = serde_json::json!(name);
        }
        p["resolved"]["style"]["axis_x"]["labels"]["expr"] = serde_json::json!("upper(datum.value) + datum.index");
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).unwrap();
        for label in ["NORTH0", "SOUTH0.5", "EAST1"] {
            assert!(svg.contains(&format!(">{label}<")), "{label}: {svg}");
        }
    }

    /// Area shares line's panel, so its bottom strip takes the same pad arm.
    #[test]
    fn an_area_bottom_support_table_strip_grows_the_canvas() {
        let _en = crate::time_locale::en_us_scope();
        let mut p = make_line_payload(true, false);
        p["resolved"]["chart_type"] = serde_json::json!("area");
        p["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "signups", "format": ",d", "label": "Signups"}]
        });
        p["resolved"]["effective_support_table_style"] = support_table_style_json_bottom();
        p["resolved"]["support_table_axis_offset"] = serde_json::json!(20.0);
        let svg = crate::families::area::render(&serde_json::from_value(p).unwrap(), &test_fonts()).expect("renders");
        assert!(canvas_dims(&svg).1 > 0.0 && svg.contains(">Signups<"), "{svg}");
    }

    #[test]
    fn a_rail_takes_the_rail_concat_and_no_rail_takes_the_fit_arm() {
        assert_eq!(shape(false), Shape::Fit);
        assert_eq!(shape(true), Shape::RailConcat);
    }

    fn axis_json(visible_title: bool, position: Option<&str>) -> serde_json::Value {
        serde_json::json!({
            "grid": {"visible": false, "threshold": null, "width": 1.0, "color": "#D0D3D9", "opacity": 1.0},
            "ticks": {"color": "#EDEFF2", "visible": true, "count": null, "length": 6.0},
            "labels": {
                "font": {"family": TEST_FONT, "color": "#626366", "size": 11.0, "weight": "400"},
                "padding": 6.0, "min_gap": null, "format": null, "visible": true
            },
            "title": {
                "font": {"family": TEST_FONT, "size": 11.0, "weight": "600", "color": "#8A8C8F"},
                "padding": 10.0,
                "visible": visible_title
            },
            "position": position,
            "mirror": null, "band_position": 0.5, "scale": {},
            "tick_values": [], "domain_max": null, "domain_min": null,
            "ruler": null, "tick_label": {"format": ",.0~f"},
            "time_unit": null, "fiscal_year_start_month": 1, "is_quantitative": true, "zero_anchored": false, "fill": "null", "line": null
        })
    }

    /// The value axis as production themes bake it: `ticks.visible: false`
    /// and a `ticks.count` of 6 (the x axes bake none).
    fn value_axis_json(visible_title: bool, position: Option<&str>) -> serde_json::Value {
        let mut axis = axis_json(visible_title, position);
        axis["ticks"]["visible"] = serde_json::json!(false);
        axis["ticks"]["color"] = serde_json::json!("#D0D3D9");
        axis["ticks"]["count"] = serde_json::json!(6.0);
        axis
    }

    /// `make_line_payload`'s value axis: hidden ticks, except on the right
    /// axis, whose fit-arm tests pin the tick reservation.
    fn line_value_axis_json(visible_title: bool, y_on_right: bool) -> serde_json::Value {
        let side = if y_on_right { "right" } else { "left" };
        let mut axis = value_axis_json(visible_title, Some(side));
        axis["ticks"]["visible"] = serde_json::json!(y_on_right);
        axis
    }

    fn make_line_payload(y_on_right: bool, titles_visible: bool) -> serde_json::Value {
        let rows = serde_json::json!([
            {"week": "2026-01-01", "signups": 100.0},
            {"week": "2026-01-08", "signups": 150.0},
            {"week": "2026-01-15", "signups": 130.0},
        ]);
        let resolved = serde_json::json!({
            "id": "line", "title": null, "title_style": null, "background": "#ffffff", "canvas": "#ffffff",
            "x": "week", "y": "signups",
            "color": null, "stack": "none",
            "palette": ["#3164a3"],
            "multiples": null, "field_labels": {}, "wide_measures": [], "layers": [],
            "legend": {
                "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
                "title": {"font": {"case": "title", "family": TEST_FONT}},
                "label": {"font": {"family": TEST_FONT, "size": 11.0}}
            },
            "style": {
                "axis_x": axis_json(titles_visible, None),
                "axis_y": line_value_axis_json(titles_visible, y_on_right),
                "line_mark": {"stroke": {"width": 4.0, "cap": "round", "join": "round"}, "halo_multiplier": 1.5},
                "series_label": {"font_family": TEST_FONT, "font_size": 11.0, "font_weight": "400", "dark_companion_palette": [], "pin_companions": [], "gap_px": 8.0},
                "endpoint_labels": {"visible": false, "label_offset": 6.0},
                "single_series_fill": "#3164a3",
                "label_usable_ratio": 0.8
            }
        });
        serde_json::json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "board_style": crate::with_required_board_fields(serde_json::json!({})),
            "padding": {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0}
        })
    }


    use serde_json::Value;

    const ANNOTATION_CONTRACT: &str = include_str!("../../../fixtures/annotations/contract.json");

    /// Contract entry `index`, re-anchored onto `make_line_payload`'s dates and values.
    fn annotation(index: usize, x: Value, y: Value) -> Value {
        let all: Vec<Value> = serde_json::from_str(ANNOTATION_CONTRACT).unwrap();
        let mut a = all.into_iter().find(|a| a["index"] == index).unwrap();
        a["x"] = x;
        a["y"] = y;
        a
    }

    fn annotated_line(annotations: Vec<Value>) -> String {
        let mut json = make_line_payload(false, false);
        json["resolved"]["annotations"] = Value::Array(annotations);
        let payload: Payload = serde_json::from_value(json).unwrap();
        render(&payload, &test_fonts()).expect("renders")
    }

    fn value(v: Value) -> Value {
        serde_json::json!({"kind": "value", "value": v})
    }

    fn first_line_x(svg: &str) -> f64 {
        let d = svg.split("<path d=\"M").nth(1).unwrap();
        d.split(',').next().unwrap().parse().unwrap()
    }

    #[test]
    fn annotations_paint_behind_and_over_the_marks() {
        let svg = annotated_line(vec![
            annotation(3, serde_json::json!({"kind": "range", "from": "2026-01-05", "to": "2026-01-10"}), Value::Null),
            annotation(0, Value::Null, value(serde_json::json!(140.0))),
        ]);
        let background = svg.find("annotations-background").expect("a background group");
        let foreground = svg.find("annotations-foreground").expect("a foreground group");
        let mark = svg.find("layer_3_marks").expect("the line mark");
        assert!(background < mark && mark < foreground, "{svg}");
        assert!(svg.contains("<clipPath id=\"line-ann0\">"), "{svg}");
    }

    #[test]
    fn a_line_stack_runs_halo_tint_rule_line_text() {
        let mut rule = annotation(0, Value::Null, value(serde_json::json!(140.0)));
        rule["plane"] = "background".into();
        let svg = annotated_line(vec![
            rule,
            annotation(3, serde_json::json!({"kind": "range", "from": "2026-01-05", "to": "2026-01-10"}), Value::Null),
        ]);
        let at = |needle: &str| svg.find(needle).unwrap_or_else(|| panic!("{needle} missing: {svg}"));
        let halo = at("layer_1_marks");
        let tint = at("<rect x=\"308\"");
        let rule = at("<line x1=\"0\" y1=\"65\"");
        let main = at("layer_3_marks");
        let text = at("annotations-text\"><g data");
        assert!(halo < tint && tint < rule && rule < main && main < text, "{svg}");
    }

    #[test]
    fn point_halos_paint_under_a_background_tint() {
        let mut json = make_line_payload(false, false);
        json["resolved"]["style"]["point_mark"] =
            serde_json::json!({"size": 100.0, "filled": true, "stroke_width": 2.0, "labels": {"visible": false}});
        json["resolved"]["annotations"] = Value::Array(vec![annotation(
            3,
            serde_json::json!({"kind": "range", "from": "2026-01-05", "to": "2026-01-10"}),
            Value::Null,
        )]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let tint = svg.find("annotations-background").expect("the band tint");
        let halo_line = svg.find("layer_1_marks").expect("the line halo");
        let last_halo_point = svg.rfind("layer_1_marks").unwrap();
        assert!(halo_line < tint && last_halo_point < tint, "{svg}");
        assert!(tint < svg.find("layer_3_marks").unwrap(), "{svg}");
    }

    /// A haloed, point-marked line with a note on the 2026-01-08 datum, carrying
    /// the visible dot's radius resolve bakes for `size: 100`.
    fn note_on_a_point(edit: impl FnOnce(&mut Value)) -> String {
        let mut note = annotation(7, value(serde_json::json!("2026-01-08")), value(serde_json::json!(150.0)));
        note["style"]["circle"]["point_radius"] = 5.0.into();
        edit(&mut note);
        let mut json = make_line_payload(false, false);
        json["resolved"]["style"]["point_mark"] =
            serde_json::json!({"size": 100.0, "filled": true, "stroke_width": 2.0, "labels": {"visible": false}});
        json["resolved"]["annotations"] = Value::Array(vec![note]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        render(&payload, &test_fonts()).expect("renders")
    }

    fn num_attr(tag: &str, name: &str) -> f64 {
        let rest = tag.split(&format!(" {name}=\"")).nth(1).unwrap();
        rest.split('"').next().unwrap().parse().unwrap()
    }

    /// The enclosing circle's (cx, cy, r) in the annotation layer.
    fn enclosure(svg: &str) -> (f64, f64, f64) {
        let tag = svg.split("<circle cx=").nth(1).unwrap_or_else(|| panic!("no circle: {svg}"));
        let tag = format!(" cx={tag}");
        (num_attr(&tag, "cx"), num_attr(&tag, "cy"), num_attr(&tag, "r"))
    }

    #[test]
    fn a_connector_arrow_tips_the_visible_dot_edge_not_the_halo() {
        let ringed = note_on_a_point(|n| n["enclose"] = "circle".into());
        let (cx, cy, _) = enclosure(&ringed);
        let svg = note_on_a_point(|_| {});
        let arrow = svg.split("annotations-foreground").nth(1).unwrap();
        let d = arrow.split("Z\"").next().unwrap().rsplit("<path d=\"M").next().unwrap();
        let (x, y) = d.split('L').next().unwrap().split_once(',').unwrap();
        let (x, y): (f64, f64) = (x.parse().unwrap(), y.parse().unwrap());
        let gap = ((x - cx).powi(2) + (y - cy).powi(2)).sqrt();
        assert!((gap - 5.0).abs() < 0.05, "tip {gap}px from the datum: {svg}");
    }

    #[test]
    fn halo_auto_haloes_a_note_on_an_area_fill_and_not_beside_a_bare_line() {
        let _en = crate::time_locale::en_us_scope();
        let mut note = annotation(10, Value::Null, Value::Null);
        note["panel"] = Value::Null;
        note["style"]["text"]["halo_over_marks"] = true.into();
        note["style"]["text"]["position"] = serde_json::json!({"edge": "bottom", "align": "start", "overlay": true, "auto_edge": false, "auto_align": false});
        let mut json = make_line_payload(false, false);
        json["resolved"]["annotations"] = Value::Array(vec![note]);
        let line: Payload = serde_json::from_value(json.clone()).unwrap();
        json["resolved"]["chart_type"] = serde_json::json!("area");
        let area: Payload = serde_json::from_value(json).unwrap();
        let haloed = |svg: String| svg.contains("stroke-linejoin=\"round\">Dashed = forecast");
        assert!(haloed(crate::families::area::render(&area, &test_fonts()).unwrap()));
        assert!(!haloed(render(&line, &test_fonts()).unwrap()));
    }

    #[test]
    fn no_annotations_paint_nothing() {
        let svg = annotated_line(Vec::new());
        assert!(!svg.contains("annotations"), "{svg}");
        assert!(!svg.contains("-ann0"), "{svg}");
    }

    #[test]
    fn an_annotation_date_past_the_data_widens_the_time_extent() {
        let plain = first_line_x(&annotated_line(Vec::new()));
        let band = annotation(3, serde_json::json!({"kind": "range", "from": "2025-12-01", "to": "2025-12-15"}), Value::Null);
        let widened = first_line_x(&annotated_line(vec![band]));
        assert!(widened > plain + 1.0, "the first point moves right: {plain} -> {widened}");
    }

    #[test]
    fn rule_text_above_the_top_tick_reserves_top_margin() {
        let top = |svg: &str| -> f64 {
            let g = svg.split("<g transform=\"translate(").nth(1).unwrap();
            g.split(')').next().unwrap().split(',').nth(1).unwrap().parse().unwrap()
        };
        let plain = annotated_line(Vec::new());
        let ruled = annotated_line(vec![annotation(0, Value::Null, value(serde_json::json!(150.0)))]);
        assert!(top(&ruled) > top(&plain) + 5.0, "{} -> {}", top(&plain), top(&ruled));
    }

    #[test]
    fn a_facet_panel_s_ink_holds_its_annotation_text() {
        let _en = crate::time_locale::en_us_scope();
        let top_ink = |annotations: Vec<Value>| -> (f64, f64) {
            let mut json = make_line_payload(false, false);
            json["resolved"]["annotations"] = Value::Array(annotations);
            let payload: Payload = serde_json::from_value(json).unwrap();
            let opts = FacetPanelOpts {
                panel_key: "",
                panel_ordinal: 0,
                suppress_x_axis: false,
                paint_value_axis: true,
                fold_axis_gutter: false,
                value_axis_right_overhang: 0.0,
                mirror_band_x: false,
                shared_domain_rows: &payload.rows,
                independent_scale: false,
            };
            let (_, plot, geom) = render_panel(&payload, &test_fonts(), 480.0, 240.0, Some(&opts));
            let pad_t = payload.padding.as_ref().map(|p| p.top).unwrap_or(0.0);
            (geom.ink.y1, pad_t + plot.top)
        };
        let (plain, plain_top) = top_ink(Vec::new());
        let (ruled, ruled_top) = top_ink(vec![annotation(0, Value::Null, value(serde_json::json!(150.0)))]);
        // The plain panel's ink reaches above its plot only by half a tick label.
        let tick_reach = plain_top - plain;
        assert!(ruled < ruled_top - tick_reach - 5.0, "the rule's text above the plot is panel ink: {ruled} vs plot top {ruled_top}");
    }

    /// The wide rail payload with a trend on `revenue`, its text at `position`.
    fn rail_with_trend(position: Value) -> Result<String, crate::diagnostic::Diagnostic> {
        let mut trend = annotation(12, Value::Null, Value::Null);
        trend["series"] = Value::Null;
        trend["text"] = "trend".into();
        trend["trend"] = serde_json::json!({"line": [["2026-01-01", 100.0], ["2026-01-08", 150.0]], "interval": null});
        trend["style"]["text"]["position"] = position;
        let mut json = make_wide_line_payload();
        json["resolved"]["annotations"] = Value::Array(vec![trend]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        render(&payload, &test_fonts())
    }

    /// The `x` of the `<text>` element reading `label`, and the canvas width.
    fn text_x(svg: &str, label: &str) -> f64 {
        let at = svg.find(&format!(">{label}</text>")).unwrap_or_else(|| panic!("no {label}: {svg}"));
        let tag = &svg[svg[..at].rfind("<text").unwrap()..at];
        tag.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
    }

    #[test]
    fn a_trend_label_outside_the_line_end_sits_between_the_plot_and_the_rail() {
        let outside = serde_json::json!({"edge": "right", "align": "center", "overlay": false, "auto_edge": false, "auto_align": false});
        let with_text = rail_with_trend(outside).expect("margin text beside a rail renders");
        let rail_at = with_text.find(" Revenue<").unwrap();
        let rail_x: f64 = with_text[..rail_at].rsplit("translate(").next().unwrap().split(',').next().unwrap().parse().unwrap();
        // The pane's origin is 41px in; the text is wider than 20px and starts at its edge.
        assert!(41.0 + text_x(&with_text, "trend") + 20.0 < rail_x, "{rail_x}: {with_text}");
    }

    /// The plot's clip width: what the margin ink negotiation left it.
    fn plot_width(svg: &str) -> f64 {
        svg.split("<clipPath id=\"c-ann0\"><rect x=\"0\" y=\"0\" width=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
    }

    #[test]
    fn overlay_trend_text_on_the_right_edge_ends_inside_the_plot() {
        let overlay = serde_json::json!({"edge": "right", "align": "center", "overlay": true, "auto_edge": false, "auto_align": false});
        let outside = serde_json::json!({"edge": "right", "align": "center", "overlay": false, "auto_edge": false, "auto_align": false});
        let (inside, beside) = (rail_with_trend(overlay).expect("inside text needs no margin"), rail_with_trend(outside).unwrap());
        assert!(text_x(&inside, "trend") <= plot_width(&inside) + 41.0, "{inside}");
        assert!(plot_width(&inside) > plot_width(&beside), "overlay text reserves no margin");
    }

    #[test]
    fn a_side_legend_clears_trend_text_outside_the_plot() {
        for rail in [false, true] {
            assert_legend_clears_trend_text(rail);
        }
    }

    fn assert_legend_clears_trend_text(rail: bool) {
        let mut json = make_multi_series_line_payload_with_bottom_legend();
        json["resolved"]["legend"]["position"]["edge"] = "right".into();
        json["resolved"]["style"]["endpoint_labels"]["visible"] = rail.into();
        let mut trend = annotation(12, Value::Null, Value::Null);
        trend["series"] = Value::Null;
        trend["text"] = "a trend label wider than the gap".into();
        trend["style"]["text"]["max_width"] = Value::Null;
        trend["trend"] = serde_json::json!({"line": [["2026-01-01", 50.0], ["2026-01-08", 100.0]], "interval": null});
        trend["style"]["text"]["position"] = serde_json::json!({"edge": "right", "align": "center", "overlay": false, "auto_edge": false, "auto_align": false});
        json["resolved"]["annotations"] = Value::Array(vec![trend]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let label = svg.split("role-legend-label").nth(1).unwrap();
        let legend_x: f64 = label.split("<text x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        // 32 characters at 11px span about 170px.
        assert!(legend_x > text_x(&svg, "a trend label wider than the gap") + 170.0, "rail {rail}: legend at {legend_x}: {svg}");
    }

    // ── `fit` seam ─────────────────────────────────────────

    /// Right y, 3 NOMINAL x points (never a temporal-shaped string —
    /// `make_line_payload`'s own "week" field is ISO-date-shaped and would
    /// take the temporal ladder), no title — the fit arm's plainest shape,
    /// isolating the facet gates from any temporal-cadence machinery.
    fn make_fit_line_payload() -> serde_json::Value {
        let mut p = make_line_payload(true, false);
        p["rows"] = serde_json::json!([
            {"month": "Jan", "signups": 100.0},
            {"month": "Feb", "signups": 150.0},
            {"month": "Mar", "signups": 130.0},
        ]);
        p["resolved"]["x"] = serde_json::json!("month");
        p
    }

    /// One panel of `payload` through `layout::fit` — every call site below only ever varies
    /// `svg_w`/`svg_h`/`facet`, so this pins the rest (no wide fold, no layer) the same way every
    /// other facet-seam test does.
    fn render_panel(
        payload: &Payload,
        fonts: &FontBook,
        svg_w: f64,
        svg_h: f64,
        facet: Option<&FacetPanelOpts>,
    ) -> (String, super::PlotRect, super::LinePanelGeometry) {
        let bound = super::bind::bind(payload, false).expect("binds");
        let fitted = super::layout::fit(&bound, payload, &payload.rows, (svg_w, svg_h), payload.padding.as_ref(), fonts, facet)
            .expect("renders");
        (print::print(&[fitted.svg]), fitted.plot, fitted.geometry)
    }

    /// d3-scale `normalize`: an all-zero measure is a zero-extent domain, so
    /// every mark sits at the plot's midpoint and an area floors at that same
    /// zero.
    #[test]
    fn a_zero_extent_value_domain_puts_marks_and_the_area_floor_at_the_midpoint() {
        let mut p = make_fit_line_payload();
        for row in p["rows"].as_array_mut().unwrap() {
            row["signups"] = serde_json::json!(0.0);
        }
        let payload: Payload = serde_json::from_value(p).unwrap();
        let fonts = test_fonts();
        for draw_areas in [false, true] {
            let (svg, rect) = render_with_layout(&payload, &fonts, draw_areas).unwrap();
            let mid = rect.plot_h / 2.0;
            let marks = if draw_areas { "layer_2_marks" } else { "layer_1_marks" };
            let group = svg.split(marks).nth(1).unwrap_or_else(|| panic!("{marks}: {svg}"));
            let d = group.split("d=\"").nth(1).unwrap().split('"').next().unwrap();
            let ys: Vec<f64> = d
                .split(['M', 'L', 'Z'])
                .filter_map(|pt| pt.split(',').nth(1)?.parse().ok())
                .collect();
            assert!(!ys.is_empty() && ys.iter().all(|y| *y == mid), "area={draw_areas} path {d} off the midpoint {mid}");
        }
    }

    /// A1: the single-chart wrapper is exactly `layout::fit(..,
    /// facet: None)` — pins that `render_with_layout` adds nothing of its
    /// own beyond `bind`'s wide fold and layer validation ahead of the
    /// call. Mutation: swap the panel call's `svg_w`/`svg_h` for a
    /// different payload's and this assertion catches the drift.
    #[test]
    fn single_chart_path_is_the_panel_seam_with_no_facet_opts() {
        let payload: Payload = serde_json::from_value(make_fit_line_payload()).unwrap();
        let fonts = test_fonts();
        let (svg_render, _) = render_with_layout(&payload, &fonts, false).unwrap();
        let (svg_panel, _, _) = render_panel(&payload, &fonts, payload.width, payload.height, None);
        assert_eq!(svg_render, svg_panel, "the wrapper adds nothing beyond the panel call");
    }

    /// A2: `suppress_x_axis` takes `pad_plot` (never shrinks the declared
    /// box, unlike the single-chart `fit_plot`) and paints no x-axis
    /// chrome. Mutation: drop the `suppress_x_axis` arm of the `pad_plot`/
    /// `fit_plot` choice (always `fit_plot`) and the canvas assertion
    /// fails — `fit_plot` shrinks the plot into the 480x179 box instead of
    /// letting axis ink overhang it, so `canvas_dims` no longer echoes the
    /// declared box on a chart whose natural fit would want more room.
    #[test]
    fn suppress_x_axis_takes_pad_plot_and_paints_no_x_labels() {
        let payload: Payload = serde_json::from_value(make_fit_line_payload()).unwrap();
        let fonts = test_fonts();
        let opts = FacetPanelOpts {
            panel_key: "",
            panel_ordinal: 0,
            suppress_x_axis: true,
            paint_value_axis: true,
            fold_axis_gutter: false,
            value_axis_right_overhang: 0.0,
            mirror_band_x: false,
            shared_domain_rows: &payload.rows,
            independent_scale: false,
        };
        let (svg, plot, _) = render_panel(&payload, &fonts, 480.0, 179.0, Some(&opts));
        assert_eq!(canvas_dims(&svg), (480.0, 179.0), "pad never shrinks the declared box");
        assert_eq!(
            plot.plot_w, 480.0,
            "pad_plot returns plot_w verbatim — a fit_plot shrink would leave it under 480 \
             once the y-axis gutter's own overhang is subtracted"
        );
        assert!(!svg.contains(">Jan<"), "x labels are suppressed: {svg}");
        assert!(svg.contains("<line"), "x gridlines still draw as cell content: {svg}");
    }

    /// A3: `paint_value_axis: false` still reserves the gutter (the plot
    /// rect is unaffected) and still measures `val_ticks`, but draws
    /// neither the y tick marks nor the y labels. Mutation: drop the
    /// `paint_value_axis` gate on the y-label `<text>` loop (always draw)
    /// and the label-count assertion fails.
    #[test]
    fn paint_value_axis_false_keeps_gridlines_drops_labels_and_captures_val_ticks() {
        let mut payload_json = make_fit_line_payload();
        payload_json["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let fonts = test_fonts();
        let opts_on = FacetPanelOpts {
            panel_key: "",
            panel_ordinal: 0,
            suppress_x_axis: false,
            paint_value_axis: true,
            fold_axis_gutter: false,
            value_axis_right_overhang: 0.0,
            mirror_band_x: false,
            shared_domain_rows: &payload.rows,
            independent_scale: false,
        };
        let opts_off = FacetPanelOpts { paint_value_axis: false, ..opts_on };
        let (svg_on, _, geom_on) = render_panel(&payload, &fonts, payload.width, payload.height, Some(&opts_on));
        let (svg_off, _, geom_off) =
            render_panel(&payload, &fonts, payload.width, payload.height, Some(&opts_off));
        assert!(
            svg_off.matches("<text").count() < svg_on.matches("<text").count(),
            "y tick labels drop when paint_value_axis is false"
        );
        assert!(
            svg_off.matches("<line").count() < svg_on.matches("<line").count(),
            "y tick MARKS drop (gridlines, drawn separately, are unaffected by this gate)"
        );
        assert!(!geom_on.val_ticks.is_empty());
        assert_eq!(
            geom_on.val_ticks, geom_off.val_ticks,
            "val_ticks is captured regardless of paint_value_axis"
        );
    }

    /// A visible x grid gives a facet cell a grid-only bottom axis anchored at
    /// `plot_h + offset`, and Vega bounds the cell by that anchor, never by the
    /// gridline strokes: the bottom ink reaches `super::X_AXIS_OFFSET` below the plot
    /// whatever the grid's width, and the grid off adds nothing. Mutation: size
    /// the reach from the grid width (`ad + width / 2`) and the width-12 case
    /// lands at `plot_bottom + 6.5`.
    #[test]
    fn visible_x_grid_extends_the_panels_bottom_ink() {
        let bottom_ink = |grid: serde_json::Value| {
            let mut payload_json = make_fit_line_payload();
            payload_json["resolved"]["style"]["axis_x"]["grid"] = grid;
            // A hairline keeps the mark's own ink inside the axis offset.
            payload_json["resolved"]["style"]["line_mark"]["stroke"]["width"] = serde_json::json!(0.5);
            let payload: Payload = serde_json::from_value(payload_json).unwrap();
            let fonts = test_fonts();
                let opts = FacetPanelOpts {
                panel_key: "",
                panel_ordinal: 0,
                suppress_x_axis: true,
                paint_value_axis: true,
                fold_axis_gutter: false,
                value_axis_right_overhang: 0.0,
                mirror_band_x: false,
                shared_domain_rows: &payload.rows,
                independent_scale: false,
            };
            let (_, plot, geom) = render_panel(&payload, &fonts, 480.0, 179.0, Some(&opts));
            let pad_t = payload.padding.as_ref().map(|p| p.top).unwrap_or(0.0);
            (geom.ink.y2, pad_t + plot.top + plot.plot_h)
        };
        let (off_y2, off_bottom) = bottom_ink(serde_json::json!({"visible": false}));
        assert!(off_y2 < off_bottom + super::X_AXIS_OFFSET, "no grid, no grid axis: {off_y2} vs plot bottom {off_bottom}");
        for width in [1.0, 12.0] {
            let (on_y2, on_bottom) = bottom_ink(serde_json::json!({"visible": true, "width": width, "color": "#EDEFF2"}));
            assert!((on_y2 - (on_bottom + super::X_AXIS_OFFSET)).abs() < 1e-6, "grid width {width}: {on_y2} vs plot bottom {on_bottom}");
        }
    }

    /// A4: `fold_axis_gutter` widens `geom.ink` by the fit arm's own right
    /// margin overhang (`pad_plot`'s measured value-axis gutter) — inert
    /// (`ink.x2` == the plot's own right edge) when unset. `scale.padding`
    /// insets the marks so the line stroke's own expansion stays inside the
    /// plot rect, isolating this assertion from the mark-ink union.
    /// Mutation: drop the `fold_axis_gutter` union (`ink` never grows) and
    /// the folded/unfolded `ink.x2` values collapse to the same number.
    #[test]
    fn fold_axis_gutter_widens_ink_by_pad_plot_right_margin() {
        let mut payload_json = make_fit_line_payload();
        payload_json["resolved"]["style"]["axis_x"]["scale"] = serde_json::json!({"padding": 20.0});
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let fonts = test_fonts();
        let opts_folded = FacetPanelOpts {
            panel_key: "",
            panel_ordinal: 0,
            suppress_x_axis: true,
            paint_value_axis: true,
            fold_axis_gutter: true,
            value_axis_right_overhang: 0.0,
            mirror_band_x: false,
            shared_domain_rows: &payload.rows,
            independent_scale: false,
        };
        let opts_unfolded = FacetPanelOpts { fold_axis_gutter: false, ..opts_folded };
        let (_, plot, geom_folded) = render_panel(&payload, &fonts, 480.0, 179.0, Some(&opts_folded));
        let (_, _, geom_unfolded) = render_panel(&payload, &fonts, 480.0, 179.0, Some(&opts_unfolded));
        let pad_l = payload.padding.as_ref().map(|p| p.left).unwrap_or(0.0);
        let plot_right = pad_l + plot.left + plot.plot_w;
        assert!(
            (geom_unfolded.ink.x2 - plot_right).abs() < 1e-6,
            "unfolded ink stops at the plot's own right edge: {} vs {plot_right}",
            geom_unfolded.ink.x2
        );
        let expected = plot_right + plot.facet_axis_right_overhang;
        assert!(
            (geom_folded.ink.x2 - expected).abs() < 1e-6,
            "folded ink reaches the plot right edge plus the fit arm's own right margin: {} vs {expected}",
            geom_folded.ink.x2
        );
        assert!(plot.facet_axis_right_overhang > 0.0, "the right-axis gutter must be real overhang for this to test anything");
    }

    /// `LinePanelGeometry`'s remaining measurement fields, verified against
    /// this single-chart test's own `facet: None` call (`facet_compose::
    /// compose_facet` reads `widest_val_tick_label_width`/`val_gutter` the
    /// same way bar's own `PanelGeometry` does off `render_vertical_panel`).
    #[test]
    fn panel_geometry_exposes_axis_measurements() {
        let payload: Payload = serde_json::from_value(make_fit_line_payload()).unwrap();
        let fonts = test_fonts();
        let (_, _, geom) = render_panel(&payload, &fonts, payload.width, payload.height, None);
        assert!(geom.widest_val_tick_label_width > 0.0);
        assert!(geom.val_gutter > 0.0);
    }

    /// A5: `shared_domain_rows` pins the y ladder to the UNION extent, not
    /// this panel's own rows — the panel still draws its own (narrower)
    /// data, but the scale/ladder reflects the wider shared domain.
    /// Mutation: `domain_rows = rows` (ignore `shared_domain_rows`) and
    /// `val_ticks` stops differing between the two calls below.
    #[test]
    fn shared_domain_rows_pins_the_ladder_to_the_union_extent() {
        let payload: Payload = serde_json::from_value(make_fit_line_payload()).unwrap();
        let shared_rows: Vec<serde_json::Map<String, serde_json::Value>> = serde_json::from_value(
            serde_json::json!([
                {"month": "Jan", "signups": 100.0},
                {"month": "Feb", "signups": 150.0},
                {"month": "Mar", "signups": 130.0},
                {"month": "Apr", "signups": 2400.0},
            ]),
        )
        .unwrap();
        let fonts = test_fonts();
        let own_opts = FacetPanelOpts {
            panel_key: "",
            panel_ordinal: 0,
            suppress_x_axis: false,
            paint_value_axis: true,
            fold_axis_gutter: false,
            value_axis_right_overhang: 0.0,
            mirror_band_x: false,
            shared_domain_rows: &payload.rows,
            independent_scale: false,
        };
        let shared_opts = FacetPanelOpts { shared_domain_rows: &shared_rows, ..own_opts };
        let (_, _, geom_own) = render_panel(&payload, &fonts, payload.width, payload.height, Some(&own_opts));
        let (_, _, geom_shared) =
            render_panel(&payload, &fonts, payload.width, payload.height, Some(&shared_opts));
        assert_ne!(
            geom_own.val_ticks, geom_shared.val_ticks,
            "shared_domain_rows must pin the ladder to the union extent, not this panel's own rows"
        );
    }

    /// A6: `LineXAxis::nodes` (the nominal branch here) reproduces exactly
    /// the byte range the single-chart path itself drew for its x-axis tick
    /// marks/labels — the struct is a byte-identical extraction, not a
    /// second reading of the law. Mutation: hand-edit one formatted
    /// coordinate inside `paint`'s nominal branch (e.g. drop the `+ ad`) and
    /// `svg.contains(&painted)` fails.
    #[test]
    fn a_gridline_without_an_ink_refuses_by_name() {
        let mut p = make_line_payload(false, false);
        p["resolved"]["style"]["axis_y"]["grid"] = serde_json::json!({"visible": true});
        let payload: Payload = serde_json::from_value(p).unwrap();
        let err = render(&payload, &test_fonts()).unwrap_err().to_string();
        assert_eq!(err, "line: axis_y.grid.color absent (theme required)");
    }

    #[test]
    fn line_x_axis_painter_reproduces_the_single_chart_bytes() {
        let payload: Payload = serde_json::from_value(make_fit_line_payload()).unwrap();
        let fonts = test_fonts();
        let (svg, plot, geom) = render_panel(&payload, &fonts, payload.width, payload.height, None);
        let bound = super::bind::bind(&payload, false).unwrap();
        let painted = print::print(&geom.axis.nodes(&bound.r.style.axis_x, 0.0, plot.plot_w, (plot.plot_h, plot.plot_h), &fonts).unwrap());
        assert!(!painted.is_empty());
        assert!(
            svg.contains(&painted),
            "the painter's own output must appear verbatim in the single-chart SVG:\n{painted}"
        );
        // Pin the FIRST label's own coordinate (plot_left 0 + x 0 + ad 0.5)
        // so a formula drop inside `paint` that ALSO moves the single-chart
        // `svg` output the same way (self-consistent but wrong) still
        // fails — `svg.contains(&painted)` alone can't catch that class,
        // since both sides would shift together.
        assert!(
            painted.contains("<text x=\"0.500\" y=\"360.500\""),
            "Jan's own label sits at plot_left(0) + x(0) + ad(0.5): {painted}"
        );
    }
    fn assert_label_order(svg: &str, names: &[&str]) {
        let pos: Vec<usize> = names
            .iter()
            .map(|n| svg.find(&format!(">{n}<")).unwrap_or_else(|| panic!("{n} label missing: {svg}")))
            .collect();
        assert!(pos.windows(2).all(|w| w[0] < w[1]), "expected {names:?}, positions {pos:?}");
    }

    /// A `sort_month_payload` shared by the ascending/descending/refusal
    /// tests below: query order (Feb, Mar, Jan), alphabetical (Feb, Jan,
    /// Mar) and both sort directions are all distinguishable — the same
    /// distinguishing shape `fixtures/authored-sort-x-order.yml` uses.
    fn sort_month_payload(seq: serde_json::Value) -> serde_json::Value {
        let mut p = make_line_payload(true, false);
        p["resolved"]["x"] = serde_json::json!("month");
        p["resolved"]["sort"] = seq;
        // A real resolved axis for a nominal "month" field never bakes
        // `is_quantitative: true` — `axis_json`'s own default is a shared
        // fixture convenience, not a faithful reading for this field.
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        p["rows"] = serde_json::json!([
            {"month": "Feb", "signups": 200.0, "seq": 2.0},
            {"month": "Mar", "signups": 50.0, "seq": 3.0},
            {"month": "Jan", "signups": 100.0, "seq": 1.0}
        ]);
        p
    }

    /// An authored `chart.sort` reorders the x domain by its per-category
    /// MIN aggregate, never the query row order — production's
    /// `dimension_sort_to_vl`/`pin_sorted_domain`
    /// (`emitters/_cartesian.py`). Query order is (Feb, Mar, Jan).
    #[test]
    fn line_honors_an_authored_ascending_sort() {
        let p = sort_month_payload(serde_json::json!({"by": "seq", "order": "asc"}));
        let svg = render(&serde_json::from_value(p).unwrap(), &test_fonts()).expect("renders");
        assert_label_order(&svg, &["Jan", "Feb", "Mar"]);
    }

    #[test]
    fn line_honors_an_authored_descending_sort() {
        let p = sort_month_payload(serde_json::json!({"by": "seq", "order": "desc"}));
        let svg = render(&serde_json::from_value(p).unwrap(), &test_fonts()).expect("renders");
        assert_label_order(&svg, &["Mar", "Feb", "Jan"]);
    }

    /// Area shares line's own domain code (`render_inner(draw_areas=true)`);
    /// one direct coverage test rather than re-deriving the law.
    #[test]
    fn area_honors_an_authored_sort() {
        let mut p = sort_month_payload(serde_json::json!({"by": "seq", "order": "asc"}));
        p["resolved"]["chart_type"] = serde_json::json!("area");
        let svg = crate::families::area::render(&serde_json::from_value(p).unwrap(), &test_fonts())
            .expect("renders");
        assert_label_order(&svg, &["Jan", "Feb", "Mar"]);
    }

    /// A ragged stacked grid under an authored sort: the domain is pinned to
    /// the sorted order (`pin_sorted_domain`, `_cartesian.py:274`) before the
    /// impute pass (`sparse_band_transforms`, `_layers.py:905-933`) fills the
    /// absent (Mar, gamma) pair with 0, so gamma's pinched vertex lands at
    /// the sorted LAST x. Under query row order (Feb, Mar, Jan) it would
    /// land in the middle — the fixture's `area_stacked_sorted`.
    #[test]
    fn stacked_area_authored_sort_pins_the_domain_the_impute_pass_fills() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["resolved"]["x"] = json!("month");
        p["resolved"]["sort"] = json!({"by": "seq", "order": "asc"});
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = json!(false);
        p["rows"] = json!([
            {"month": "Feb", "seq": 2.0, "series": "alpha", "signups": 200.0},
            {"month": "Feb", "seq": 2.0, "series": "gamma", "signups": 120.0},
            {"month": "Mar", "seq": 3.0, "series": "alpha", "signups": 50.0},
            {"month": "Jan", "seq": 1.0, "series": "alpha", "signups": 100.0},
            {"month": "Jan", "seq": 1.0, "series": "gamma", "signups": 60.0}
        ]);
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true)
            .expect("renders");
        assert_label_order(&svg, &["Jan", "Feb", "Mar"]);
        let edges = stacked_area_edges(&svg);
        let (gamma_top, gamma_bottom) = &edges[1];
        assert_eq!(gamma_top.len(), 3, "gamma's run is unbroken across the absent Mar: {svg}");
        let heights: Vec<f64> = gamma_top.iter().zip(gamma_bottom).map(|(t, b)| b.1 - t.1).collect();
        assert!(
            heights[0] > 1.0 && heights[1] > 1.0 && heights[2].abs() < 0.01,
            "gamma pinches to zero only at the sorted last x (Mar): {heights:?}: {svg}"
        );
    }

    /// Area shares line's band-step path: the same `Feb, Apr, Mar, Jan`
    /// probe with `sort: seq desc` and `style.marks.area.curve: step`
    /// (`band_step_under_an_authored_sort_draws_the_path_in_query_row_order`)
    /// renders the area fill's top edge in query order, then the baseline
    /// walked back over those points. Production's fill path on a 535px
    /// plot (133.75px bands): `M267.5,27.852 L401.25,27.852 L401.25,27.852
    /// L0,27.852 L0,288.963 L133.75,288.963 L133.75,288.963 L133.75,288.963
    /// L133.75,167.111 L267.5,167.111 L267.5,167.111 L401.25,167.111
    /// L401.25,201.926 L535,201.926 L535,201.926 L535,376 L535,376
    /// L401.25,376 L401.25,376 L267.5,376 L267.5,376 L133.75,376
    /// L133.75,376 L133.75,376 L133.75,376 L0,376 L0,376 L401.25,376
    /// L401.25,376 L267.5,376 Z` — x/133.75 = 2 3 3 0 0 1 1 1 1 2 2 3 3 4 4
    /// then 4 4 3 3 2 2 1 1 1 1 0 0 3 3 2.
    #[test]
    fn area_band_step_under_an_authored_sort_draws_the_path_in_query_row_order() {
        let mut p = step_sort_payload(None, "desc");
        p["resolved"]["chart_type"] = serde_json::json!("area");
        p["resolved"]["style"]["area_mark"] =
            serde_json::json!({"opacity": 0.16, "backdrop": false, "curve": "step"});
        let svg = crate::families::area::render(&serde_json::from_value(p).unwrap(), &test_fonts())
            .expect("renders");
        assert_label_order(&svg, &["Apr", "Mar", "Feb", "Jan"]);
        let vertices = path_vertices(&path_d(&svg, "fill-opacity=\"0.16\""));
        let (top, baseline) = vertices.split_at(15);
        assert_band_units(
            &vertices,
            4.0,
            &[
                2., 3., 3., 0., 0., 1., 1., 1., 1., 2., 2., 3., 3., 4., 4., 4., 4., 3., 3., 2.,
                2., 1., 1., 1., 1., 0., 0., 3., 3., 2.,
            ],
        );
        assert_y_follows(
            top,
            &[
                200.0, 200.0, 200.0, 200.0, 50.0, 50.0, 50.0, 50.0, 120.0, 120.0, 120.0, 120.0,
                100.0, 100.0, 100.0,
            ],
        );
        assert!(baseline.iter().all(|v| v.1 > top[4].1), "baseline runs along the plot bottom");
    }

    /// A sort column of numbers and text makes Python's sort raise, which
    /// `x_domain_order` answers with row order — see `dimension_sort_domain`'s
    /// doc comment.
    #[test]
    fn line_authored_sort_over_numbers_and_text_keeps_row_order() {
        let mut p = sort_month_payload(serde_json::json!({"by": "seq", "order": "asc"}));
        p["rows"] = serde_json::json!([
            {"month": "Feb", "signups": 200.0, "seq": "second"},
            {"month": "Jan", "signups": 100.0, "seq": 1.0}
        ]);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert_label_order(&svg, &["Feb", "Jan"]);
    }

    /// A NUMERIC-LOOKING string sort value ("9", "2", "10") is text under
    /// MIN — production's real `min` (`utils.py::_js_min`) orders a JSON
    /// string LEXICOGRAPHICALLY, never by parsing it as a number first, so
    /// "10" ranks before "2" and "9".
    #[test]
    fn line_authored_sort_ranks_numeric_looking_text_lexicographically() {
        let mut p = sort_month_payload(serde_json::json!({"by": "seq", "order": "asc"}));
        p["rows"] = serde_json::json!([
            {"month": "Feb", "signups": 200.0, "seq": "9"},
            {"month": "Mar", "signups": 50.0, "seq": "2"},
            {"month": "Jan", "signups": 100.0, "seq": "10"}
        ]);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert_label_order(&svg, &["Jan", "Mar", "Feb"]);
    }

    /// Unranked categories (no value for `sort.by` on any row)
    /// trail the ranked ones in BOTH directions — production's
    /// `x_domain_order`'s own `ranked + unranked` return, never a
    /// direction-dependent placement. Query order is (Feb, Mar[unranked],
    /// Jan); `Mar` carries no `seq` at all.
    fn sort_month_payload_with_one_unranked_category(order: &str) -> serde_json::Value {
        let mut p = sort_month_payload(serde_json::json!({"by": "seq", "order": order}));
        p["rows"] = serde_json::json!([
            {"month": "Feb", "signups": 200.0, "seq": 2.0},
            {"month": "Mar", "signups": 50.0},
            {"month": "Jan", "signups": 100.0, "seq": 1.0}
        ]);
        p
    }

    #[test]
    fn line_authored_sort_trails_an_unranked_category_ascending() {
        let p = sort_month_payload_with_one_unranked_category("asc");
        let svg = render(&serde_json::from_value(p).unwrap(), &test_fonts()).expect("renders");
        assert_label_order(&svg, &["Jan", "Feb", "Mar"]);
    }

    #[test]
    fn line_authored_sort_trails_an_unranked_category_descending() {
        let p = sort_month_payload_with_one_unranked_category("desc");
        let svg = render(&serde_json::from_value(p).unwrap(), &test_fonts()).expect("renders");
        assert_label_order(&svg, &["Feb", "Jan", "Mar"]);
    }

    /// One table-driven sweep over x-value SHAPES × OPTIONS (curve=step, a
    /// non-calendar fiscal start, an authored `axis_x.type: ordinal` or
    /// `temporal`), each pinned to what production actually does with an
    /// authored sort on that combination — `Sorts` (pins the reversed
    /// order), `Ignores` (byte-identical sorted vs unsorted — a
    /// quantitative/production-temporal/authored-temporal x), or `Refuses`
    /// (an ordinal-and-sorted shape this port cannot model under a sort,
    /// or a bucket-family-SHAPED but calendar-invalid value production
    /// raises on regardless of sort). Replaces the seven single-shape
    /// tests this table originally subsumed; `seq` is always `1.0, 2.0,
    /// 3.0` in QUERY order (= `values` order), so a `desc` sort reverses
    /// `values` exactly.
    ///
    /// The five lowercase/out-of-range/no-space rows (`2024-q1`, `2024-w01`,
    /// `2022-13`, `13/2022`, `W12024`) are `Sorts`, not `Ignores`: none
    /// matches a `_BUCKET_FAMILIES` pattern (case-sensitive `Q`/`W`, a
    /// `01`-`12`/`01`-`53` value range, mandatory `week_spelled`
    /// whitespace — `x_values_are_bucket_family_shaped`'s own doc comment),
    /// so `normalize_labeled_temporal` never rewrites them; they stay raw
    /// ordinal strings and production sorts them, same as a half-year.
    ///
    /// The `fiscal` rows pin the grain split: yearly and quarterly x
    /// (including the cadence-detected quarter-end and same-day-yearly
    /// ISO columns) refuse, monthly/weekly/daily x ignore the sort.
    fn shape_case_payload(x_field: &str, values: &[serde_json::Value; 3], is_quantitative: bool) -> serde_json::Value {
        let mut p = make_line_payload(true, false);
        p["resolved"]["x"] = serde_json::json!(x_field);
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(is_quantitative);
        let rows: Vec<serde_json::Value> = values
            .iter()
            .enumerate()
            .map(|(i, v)| {
                let mut row = serde_json::Map::new();
                row.insert(x_field.to_string(), v.clone());
                row.insert("signups".to_string(), serde_json::json!(100.0 + i as f64 * 10.0));
                row.insert("seq".to_string(), serde_json::json!((i + 1) as f64));
                serde_json::Value::Object(row)
            })
            .collect();
        p["rows"] = serde_json::json!(rows);
        p
    }

    enum SortExpect {
        Sorts,
        /// Production sorts an ordinal calendar axis: the labels move with their buckets.
        Reorders,
        Ignores,
        Refuses(&'static str),
    }

    struct ShapeCase {
        name: &'static str,
        values: [serde_json::Value; 3],
        is_quantitative: bool,
        curve_step: bool,
        fiscal_4: bool,
        axis_ordinal_type: bool,
        axis_temporal_type: bool,
        expect: SortExpect,
    }

    #[test]
    fn line_authored_sort_x_value_shapes() {
        let _locale = crate::time_locale::en_us_scope();
        let _locale = crate::time_locale::en_us_scope();
        fn s(v: &str) -> serde_json::Value {
            serde_json::json!(v)
        }
        fn y(v: i64) -> serde_json::Value {
            serde_json::json!(v)
        }

        let cases = vec![
            // ── Sorts: production resolves nominal/ordinal and pins it ──
            ShapeCase {
                name: "half_year_hyphen",
                values: [s("2024-H1"), s("2024-H2"), s("2025-H1")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Sorts,
            },
            ShapeCase {
                name: "half_year_spelled",
                values: [s("H1 2024"), s("H2 2024"), s("H1 2025")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Sorts,
            },
            ShapeCase {
                name: "plain_categories",
                values: [s("Feb"), s("Mar"), s("Jan")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Sorts,
            },
            ShapeCase {
                name: "lowercase_iso_quarter",
                values: [s("2024-q1"), s("2024-q2"), s("2024-q3")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Sorts,
            },
            ShapeCase {
                name: "lowercase_iso_week",
                values: [s("2024-w01"), s("2024-w02"), s("2024-w03")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Sorts,
            },
            ShapeCase {
                name: "yearmonth_month_out_of_range",
                values: [s("2022-13"), s("2022-14"), s("2022-15")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Sorts,
            },
            ShapeCase {
                name: "mm_yyyy_month_out_of_range",
                values: [s("13/2022"), s("14/2022"), s("15/2022")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Sorts,
            },
            ShapeCase {
                name: "week_spelled_no_space",
                values: [s("W12024"), s("W22024"), s("W32024")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Sorts,
            },
            // ── Ignores: production resolves temporal/quantitative ──
            ShapeCase {
                name: "iso_quarter",
                values: [s("2024-Q1"), s("2024-Q2"), s("2024-Q3")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            ShapeCase {
                name: "q_yyyy",
                values: [s("Q1 2024"), s("Q2 2024"), s("Q3 2024")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            ShapeCase {
                name: "mon_yyyy",
                values: [s("Jan 2024"), s("Feb 2024"), s("Mar 2024")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            // `mon_yyyy`'s `\s+` is Python's regex whitespace class, which
            // matches Unicode `White_Space` (a non-breaking space, U+00A0,
            // included) — not just ASCII. A non-breaking space between the
            // month abbreviation and the year still resolves the bucket
            // family and hence `temporal`, the same as the ASCII row above.
            ShapeCase {
                name: "mon_yyyy_non_breaking_space",
                values: [s("Jan\u{a0}2024"), s("Feb\u{a0}2024"), s("Mar\u{a0}2024")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            // The `mon_dd_yyyy` and `week_spelled` families take any Unicode
            // whitespace before the year, as production's `\s+` does, so a
            // non-breaking space still resolves `temporal`.
            ShapeCase {
                name: "mon_dd_yyyy_non_breaking_space",
                values: [s("Feb 15,\u{a0}2024"), s("Mar 15,\u{a0}2024"), s("Jan 15,\u{a0}2024")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            ShapeCase {
                name: "week_spelled_non_breaking_space",
                values: [s("W2\u{a0}2024"), s("W3\u{a0}2024"), s("W1\u{a0}2024")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            ShapeCase {
                name: "week_spelled",
                values: [s("W1 2024"), s("W2 2024"), s("W3 2024")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            ShapeCase {
                name: "int_year",
                values: [y(2019), y(2020), y(2021)],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            ShapeCase {
                name: "string_year",
                values: [s("2019"), s("2020"), s("2021")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            ShapeCase {
                name: "iso_dates",
                values: [s("2024-01-01"), s("2024-01-08"), s("2024-01-15")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            ShapeCase {
                name: "hourly_timestamps",
                values: [s("2024-01-01T01:00:00"), s("2024-01-01T02:00:00"), s("2024-01-01T03:00:00")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            ShapeCase {
                name: "int_near_1700_floor",
                values: [y(1699), y(1700), y(1701)],
                is_quantitative: true,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            // A quantitative x ignores the sort, so even `curve: step` (d3's
            // plain step passthrough there, never band-doubled) draws the
            // same path sorted or not.
            ShapeCase {
                name: "quantitative_step",
                values: [y(5), y(10), y(15)],
                is_quantitative: true,
                curve_step: true,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            ShapeCase {
                name: "int_near_2100_ceiling",
                values: [y(2099), y(2100), y(2101)],
                is_quantitative: true,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            // ── Reorders: production sorts an ordinal calendar shape ──
            ShapeCase {
                name: "year_step",
                values: [y(2019), y(2020), y(2021)],
                is_quantitative: false,
                curve_step: true,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Reorders,
            },
            ShapeCase {
                name: "fy_step",
                values: [s("FY2022"), s("FY2023"), s("FY2024")],
                is_quantitative: false,
                curve_step: true,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Reorders,
            },
            ShapeCase {
                name: "iso_quarter_step",
                values: [s("2024-Q1"), s("2024-Q2"), s("2024-Q3")],
                is_quantitative: false,
                curve_step: true,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Reorders,
            },
            ShapeCase {
                name: "mon_yyyy_step",
                values: [s("Jan 2024"), s("Feb 2024"), s("Mar 2024")],
                is_quantitative: false,
                curve_step: true,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Reorders,
            },
            // A plain categorical x has no calendar reading to refuse: the
            // band-step path walks the rows in query order over the sorted
            // band positions, as production's does
            // (`band_step_under_an_authored_sort_draws_the_path_in_query_row_order`).
            ShapeCase {
                name: "plain_categories_step",
                values: [s("Feb"), s("Mar"), s("Jan")],
                is_quantitative: false,
                curve_step: true,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Sorts,
            },
            ShapeCase {
                name: "year_fiscal",
                values: [y(2019), y(2020), y(2021)],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: true,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Reorders,
            },
            ShapeCase {
                name: "iso_quarter_fiscal",
                values: [s("2024-Q1"), s("2024-Q2"), s("2024-Q3")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: true,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Reorders,
            },
            ShapeCase {
                name: "fy_fiscal",
                values: [s("FY2022"), s("FY2023"), s("FY2024")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: true,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Reorders,
            },
            ShapeCase {
                name: "iso_date_authored_ordinal",
                values: [s("2024-01-01"), s("2024-01-08"), s("2024-01-15")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: true,
                axis_temporal_type: false,
                expect: SortExpect::Reorders,
            },
            // Production reaches `yearquarter`/`year` by CADENCE
            // (`_CADENCE_PERIOD_DAYS`), not calendar position, for
            // quarter-end and same-day-yearly ISO columns, and
            // `detect_time_unit` ports that rung, so the fiscal predicate
            // sees the real grain here.
            ShapeCase {
                name: "quarter_end_iso_fiscal",
                values: [s("2022-03-31"), s("2022-06-30"), s("2022-09-30")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: true,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Reorders,
            },
            ShapeCase {
                name: "mid_year_yearly_iso_fiscal",
                values: [s("2019-06-15"), s("2020-06-15"), s("2021-06-15")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: true,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Reorders,
            },
            // Only `year` and `yearquarter` flip ordinal under a fiscal start
            // (`resolve_cartesian_x_type`), so every finer grain stays a
            // temporal x that ignores the sort, as it does at fiscal start 1.
            // The weekly rows are Sundays: a row off its week's Sunday start
            // under a fiscal start is a separate by-name refusal.
            ShapeCase {
                name: "monthly_iso_fiscal",
                values: [s("2024-01-01"), s("2024-02-01"), s("2024-03-01")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: true,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            ShapeCase {
                name: "weekly_iso_fiscal",
                values: [s("2023-12-31"), s("2024-01-07"), s("2024-01-14")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: true,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            ShapeCase {
                name: "daily_iso_fiscal",
                values: [s("2024-01-01"), s("2024-01-02"), s("2024-01-03")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: true,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            // `curve: step` over an ISO-date x no calendar grain explains, or an
            // authored-temporal one, draws Vega-Lite's plain step: the sort is ignored.
            ShapeCase {
                name: "ungridded_iso_dates_step",
                values: [s("2024-01-01"), s("2024-01-29"), s("2024-02-26")],
                is_quantitative: false,
                curve_step: true,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Ignores,
            },
            ShapeCase {
                name: "monthly_iso_dates_step_authored_temporal",
                values: [s("2024-01-01"), s("2024-02-01"), s("2024-03-01")],
                is_quantitative: false,
                curve_step: true,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: true,
                expect: SortExpect::Ignores,
            },
            // An authored `axis_x.type: temporal` over values no date
            // parses from refuses by name.
            ShapeCase {
                name: "plain_categories_authored_temporal",
                values: [s("Feb"), s("Mar"), s("Jan")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: true,
                expect: SortExpect::Refuses("ERR-X-VALUE-NOT-A-DATE"),
            },
            // `2023-W53` matches `_BUCKET_FAMILIES`' `iso_week`
            // regex SHAPE (`01`-`53`), but 2023 has only 52 ISO weeks, so
            // production raises constructing the date — refuses rather
            // than silently sorting it as an ordinary category.
            ShapeCase {
                name: "invalid_iso_week_in_family_shape",
                values: [s("2023-W51"), s("2023-W52"), s("2023-W53")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Refuses("unrecognized bucket format"),
            },
            // `week_spelled`'s `\s*` applies only INSIDE the
            // optional `(?:eek\s*)?` group — a bare `W` admits no
            // whitespace before its digits at all, so `"W 3 2024"` matches
            // no `_BUCKET_FAMILIES` pattern. Production never rewrites it,
            // resolves `nominal`, and sorts it, the same as any plain
            // category.
            ShapeCase {
                name: "bare_w_with_space_is_a_category",
                values: [s("W 1 2024"), s("W 2 2024"), s("W 3 2024")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Sorts,
            },
            // Same non-match, so `"W 53 2023"` is never bucket-
            // family-SHAPED at all — it does not reach the invalid-value
            // refusal above (that refusal requires a `classify_bucket`
            // match first) and instead sorts as a plain category, exactly
            // like the row above.
            ShapeCase {
                name: "bare_w_with_space_and_week_53_is_a_category",
                values: [s("W 51 2023"), s("W 52 2023"), s("W 53 2023")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Sorts,
            },
            // A bare-ISO-date column with a genuine WEEKLY cadence
            // (biweekly Mondays, gap 14 days) resolves `yearweek` in
            // production's `detect_time_unit` (exact days +
            // `statistics.median`) and in this port's weekly rung, and
            // `curve: step` flips it `ordinal` and sorts. The
            // step-under-sort refusal keys on the x being neither
            // quantitative nor a plain category (a standard ISO date is
            // production-temporal), not on the detected grain.
            ShapeCase {
                name: "biweekly_iso_dates_step",
                values: [s("2024-02-19"), s("2024-03-04"), s("2024-03-18")],
                is_quantitative: false,
                curve_step: true,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Reorders,
            },
            // `week_spelled`'s own invalid-bucket branch: 2023 has only 52
            // ISO weeks, so `"Week 53 2023"` matches the family SHAPE but
            // production raises on it.
            ShapeCase {
                name: "invalid_week_spelled_in_family_shape",
                values: [s("Week 51 2023"), s("Week 52 2023"), s("Week 53 2023")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Refuses("unrecognized bucket format"),
            },
            // `mon_dd_yyyy`'s own invalid-bucket branch: 2024 is a leap
            // year (`Feb 29` is valid), but `Feb 30` never exists.
            ShapeCase {
                name: "invalid_mon_dd_yyyy_in_family_shape",
                values: [s("Feb 28, 2024"), s("Feb 29, 2024"), s("Feb 30, 2024")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Refuses("unrecognized bucket format"),
            },
            // `mm_dd_yyyy` (`_MM_DD_YYYY_RE`) admits day `01`-`31`
            // structurally, so `"02/30/2024"` matches the family SHAPE
            // (unlike `mon_dd_yyyy`'s day count, whose regex (`\d{1,2}`)
            // allows any one or two digits, `0`-`99`, rather than bounding
            // to `01`-`31`) but is still calendar-invalid — production
            // raises the same way.
            ShapeCase {
                name: "invalid_mm_dd_yyyy_in_family_shape",
                values: [s("02/15/2024"), s("02/29/2024"), s("02/30/2024")],
                is_quantitative: false,
                curve_step: false,
                fiscal_4: false,
                axis_ordinal_type: false,
                axis_temporal_type: false,
                expect: SortExpect::Refuses("unrecognized bucket format"),
            },
        ];

        for c in &cases {
            let mut sorted = shape_case_payload("x", &c.values, c.is_quantitative);
            if c.curve_step {
                sorted["resolved"]["style"]["line_mark"]["curve"] = serde_json::json!("step");
            }
            if c.fiscal_4 {
                sorted["resolved"]["style"]["axis_x"]["fiscal_year_start_month"] = serde_json::json!(4);
            }
            if c.axis_ordinal_type {
                sorted["resolved"]["style"]["axis_x"]["type"] = serde_json::json!("ordinal");
            }
            if c.axis_temporal_type {
                sorted["resolved"]["style"]["axis_x"]["type"] = serde_json::json!("temporal");
            }
            sorted["resolved"]["sort"] = serde_json::json!({"by": "seq", "order": "desc"});
            let mut unsorted = sorted.clone();
            unsorted["resolved"]["sort"] = serde_json::Value::Null;

            match &c.expect {
                SortExpect::Ignores => {
                    let sorted_svg = render(&serde_json::from_value(sorted).unwrap(), &test_fonts())
                        .unwrap_or_else(|e| panic!("{}: must render — {e}", c.name));
                    let unsorted_svg =
                        render(&serde_json::from_value(unsorted).unwrap(), &test_fonts())
                            .unwrap_or_else(|e| panic!("{}: must render — {e}", c.name));
                    assert_eq!(
                        sorted_svg, unsorted_svg,
                        "{}: an authored sort must be a byte-identical no-op",
                        c.name
                    );
                }
                SortExpect::Reorders => {
                    let sorted_svg = render(&serde_json::from_value(sorted).unwrap(), &test_fonts())
                        .unwrap_or_else(|e| panic!("{}: must render — {e}", c.name));
                    let unsorted_svg = render(&serde_json::from_value(unsorted).unwrap(), &test_fonts())
                        .unwrap_or_else(|e| panic!("{}: must render — {e}", c.name));
                    // `seq` rises with x, so `desc` puts the last bucket first and every
                    // label keeps its bucket (a gap-filled bucket trails, unranked).
                    let (sorted_groups, unsorted_groups) = (x_label_groups(&sorted_svg), x_label_groups(&unsorted_svg));
                    assert_eq!(sorted_groups.first(), unsorted_groups.last(), "{}: the last bucket leads", c.name);
                    let (mut a, mut b) = (sorted_groups.clone(), unsorted_groups.clone());
                    a.sort();
                    b.sort();
                    assert_eq!(a, b, "{}: the same labels, moved", c.name);
                    assert_ne!(sorted_groups, unsorted_groups, "{}: the sort moved them", c.name);
                }
                SortExpect::Sorts => {
                    let svg = render(&serde_json::from_value(sorted).unwrap(), &test_fonts())
                        .unwrap_or_else(|e| panic!("{}: must render — {e}", c.name));
                    let labels: Vec<&str> =
                        c.values.iter().rev().map(|v| v.as_str().unwrap()).collect();
                    assert_label_order(&svg, &labels);
                }
                SortExpect::Refuses(needle) => {
                    // The bucket-family-shaped-but-invalid refusal fires
                    // whether or not a sort is authored.
                    let err = render(&serde_json::from_value(sorted).unwrap(), &test_fonts())
                        .expect_err(&format!("{}: must refuse", c.name));
                    assert!(err.contains(needle), "{}: {err}", c.name);
                }
            }
        }
    }

    /// Area shares line's sort gates, so a fiscal start sorts only the
    /// `Year`/`YearQuarter` grains there too.
    #[test]
    fn area_fiscal_start_sort_reorders_only_year_and_yearquarter() {
        let _locale = crate::time_locale::en_us_scope();
        let area_render = |dates: [&str; 3]| {
            let values = dates.map(|d| serde_json::json!(d));
            let mut p = shape_case_payload("x", &values, false);
            p["resolved"]["chart_type"] = serde_json::json!("area");
            p["resolved"]["style"]["axis_x"]["fiscal_year_start_month"] = serde_json::json!(4);
            p["resolved"]["sort"] = serde_json::json!({"by": "seq", "order": "desc"});
            crate::families::area::render(&serde_json::from_value(p).unwrap(), &test_fonts())
        };
        area_render(["2024-01-01", "2024-02-01", "2024-03-01"]).expect("monthly renders");
        area_render(["2022-03-31", "2022-06-30", "2022-09-30"]).expect("quarterly renders");
    }

    /// A `type: line` overlay layer sharing the base's y scale: no
    /// `axis_y.position`, no `x`, no `color`. `query_name` mirrors bar.rs's
    /// own `line_layer_json` — `None` shares the base's own query/rows,
    /// `Some(qn)` reads `payload.datasets[qn]`.
    fn line_layer_json(query_name: Option<&str>) -> serde_json::Value {
        serde_json::json!({
            "type": "line",
            "line_mark": {
                "disconnected_width": 0.9,
                "stroke": {"width": 4.0, "cap": "round", "join": "round"},
                "curve": "linear",
                "labels": {"visible": false}
            },
            "point_mark": {
                "size": 46.24, "filled": true, "stroke_width": 4.0,
                "labels": {"visible": false}
            },
            "axis_y": {"position": null},
            "x": null,
            "y": "trend",
            "label": "Trend",
            "color": null,
            "ink": "#779bc9",
            "query_name": query_name
        })
    }

    /// A line layer over a year below 100 is undefined at that NaN row, as the base line is.
    #[test]
    fn a_line_layer_breaks_at_a_year_below_100() {
        let mut json = make_line_payload(true, false);
        json["rows"] = serde_json::json!([
            {"week": "0099-01-01", "signups": 100.0, "trend": 80.0},
            {"week": "0100-01-01", "signups": 150.0, "trend": 90.0},
            {"week": "0101-01-01", "signups": 130.0, "trend": 85.0},
            {"week": "0102-01-01", "signups": 140.0, "trend": 95.0},
        ]);
        json["resolved"]["layers"] = serde_json::json!([line_layer_json(None)]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let layer = main_line_d(&svg);
        assert_eq!(layer.matches('L').count(), 2, "the NaN row is no vertex of the layer line: {layer}");
        assert!(layer.starts_with("M0,"), "{layer}");
    }

    /// A colorless chart whose layers all share the base's grouping names the base and
    /// every layer on the endpoint-label rail (`layered_endpoint_rail_fires`).
    #[test]
    fn a_layered_chart_with_a_visible_rail_names_every_layer_on_it() {
        let _en = crate::time_locale::en_us_scope();
        let svg = super::render(
            &serde_json::from_value::<Payload>({
                let mut p = layered_line_payload_with_cross_query_layer();
                p["resolved"]["style"]["endpoint_labels"] = serde_json::json!({"visible": true, "label_offset": 6.0});
                p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
                p["resolved"]["legend"]["visible"] = serde_json::json!(false);
                p
            })
            .unwrap(),
            &test_fonts(),
        )
        .expect("renders");
        assert!(svg.contains(">Trend</text>"), "{svg}");
    }

    /// The rail keys its anchors by label, so a layer repeating the base's name raises.
    #[test]
    fn a_layer_repeating_a_label_beside_the_rail_raises_the_collision_code() {
        let mut p = layered_line_payload_with_cross_query_layer();
        p["resolved"]["style"]["endpoint_labels"] = serde_json::json!({"visible": true, "label_offset": 6.0});
        p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        p["resolved"]["legend"]["visible"] = serde_json::json!(false);
        p["resolved"]["layers"][0]["label"] =
            serde_json::json!(display_text::slug_to_text(p["resolved"]["y"].as_str().unwrap()).unwrap());
        let err = super::render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).unwrap_err();
        assert_eq!(err.code, "ERR-LAYER-LABEL-COLLISION", "{err:?}");
    }

    /// A line base + a cross-query line layer sharing the base's own x
    /// vocabulary and y scale — `charts/layered-charts_2/revenue_vs_trend`'s
    /// own probed shape (`docs/laws-layers.md`): a baked `axis_y.domain_max`
    /// past the base's own headroom (the shared-scale nice quirk's input),
    /// a two-color palette, and `legend.visible/position/direction` baked
    /// as production's own `force_legend_visible` + top/horizontal law bakes
    /// for any layered chart.
    fn make_layered_line_payload() -> serde_json::Value {
        let mut p = make_line_payload(true, false);
        p["resolved"]["query_name"] = serde_json::json!("actual");
        p["resolved"]["palette"] = serde_json::json!(["#40639c", "#779bc9"]);
        p["resolved"]["style"]["single_series_fill"] = serde_json::json!("#40639c");
        p["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(3654.6784615384618);
        p["resolved"]["style"]["axis_y"]["tick_values"] =
            serde_json::json!([0.0, 1000.0, 2000.0, 3000.0, 4000.0]);
        p["resolved"]["style"]["axis_y"]["tick_label"] = serde_json::json!({"format": ",.0~f"});
        p["resolved"]["legend"]["visible"] = serde_json::json!(true);
        p["resolved"]["legend"]["position"] = serde_json::json!({"edge": "top", "align": "start", "overlay": false});
        p["resolved"]["legend"]["direction"] = serde_json::json!("horizontal");
        p["resolved"]["legend"]["label"]["padding"] = serde_json::json!(8.0);
        p
    }

    fn layered_line_payload_with_cross_query_layer() -> serde_json::Value {
        let mut p = make_layered_line_payload();
        p["resolved"]["layers"] = serde_json::json!([line_layer_json(Some("trend_query"))]);
        p["datasets"] = serde_json::json!({
            "trend_query": [
                {"week": "2026-01-01", "trend": 110.0},
                {"week": "2026-01-08", "trend": 140.0},
                {"week": "2026-01-15", "trend": 160.0}
            ]
        });
        p
    }

    fn year_below_100_bar_layer_payload(years: &[u32]) -> serde_json::Value {
        let mut json = make_line_payload(true, false);
        json["rows"] = serde_json::Value::Array(
            years
                .iter()
                .map(|y| serde_json::json!({"week": format!("{y:04}-01-01"), "signups": 100.0 + *y as f64, "trend": 80.0}))
                .collect(),
        );
        json["resolved"]["layers"] = serde_json::json!([{
            "type": "bar",
            "bar_mark": {
                "border": {"width": 1.0, "color": "#FFFFFF", "radius": 2.0},
                "band_width": 0.8,
                "labels": {"visible": false, "position": "middle", "field": null, "format": null, "dx": null, "dy": null, "font": null}
            },
            "axis_y": {"position": null},
            "x": null, "y": "trend", "label": "Trend", "color": null, "ink": "#779bc9", "query_name": null
        }]);
        json
    }

    /// Vega draws a bar at a NaN position as a path of no width at x 0.
    #[test]
    fn a_bar_layer_row_at_a_year_below_100_is_a_bar_of_no_width_at_zero() {
        let payload: Payload = serde_json::from_value(year_below_100_bar_layer_payload(&[99, 100, 101, 102])).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(svg.contains("d=\"M0.00,") && svg.contains("h0v"), "{svg}");
    }

    /// Vega stacks every bar at a NaN position into one pile.
    #[test]
    fn bar_layer_rows_at_years_below_100_stack_into_one_pile() {
        let payload: Payload = serde_json::from_value(year_below_100_bar_layer_payload(&[50, 51, 52, 53])).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let bars: Vec<(f64, f64)> = svg
            .split("d=\"M0.00,")
            .skip(1)
            .map(|rest| {
                let (top, rest) = rest.split_once('h').unwrap();
                let height = rest.split_once("v").unwrap().1.split('h').next().unwrap();
                (top.parse().unwrap(), height.parse().unwrap())
            })
            .collect();
        // Each bar paints its fill and its outline pass.
        let bars: Vec<(f64, f64)> = bars.into_iter().step_by(2).collect();
        assert_eq!(bars.len(), 4, "{svg}");
        for pair in bars.windows(2) {
            assert!((pair[1].0 + pair[1].1 - pair[0].0).abs() < 0.02, "each bar rests on the last: {bars:?}");
        }
    }

    /// The pile outgrows the area base's domain, so the fit pushes the plot past the canvas
    /// and shrinks it to no height, as Vega's does.
    #[test]
    fn an_area_with_a_bar_layer_over_every_year_below_100_collapses_its_plot_off_the_canvas() {
        let mut json = year_below_100_bar_layer_payload(&[50, 51, 52, 53]);
        json["resolved"]["chart_type"] = serde_json::json!("area");
        json["resolved"]["style"]["area_mark"] = serde_json::json!({"opacity": 0.16, "backdrop": false, "curve": "linear"});
        // The base's baked ladder, which the bars' pile (44) far outgrows.
        json["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([0.0, 5.0, 10.0, 15.0, 20.0]);
        json["resolved"]["style"]["axis_y"]["domain_max"] = serde_json::json!(20.0);
        // The layer's legend sits above the bars' pile, which the fit then holds.
        json["resolved"]["legend"]["visible"] = serde_json::json!(true);
        json["resolved"]["legend"]["position"] = serde_json::json!({"edge": "top", "align": "start", "overlay": false});
        json["resolved"]["legend"]["direction"] = serde_json::json!("horizontal");
        json["resolved"]["legend"]["label"]["padding"] = serde_json::json!(8.0);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = crate::families::area::render(&payload, &test_fonts()).expect("renders");
        assert!(group_y(&svg) > 400.0, "{}", group_y(&svg));
    }

    /// A bar layer's row with no x has no place on the base's scale: Vega drops its mark
    /// rather than the renderer failing on it.
    #[test]
    fn a_bar_layer_row_with_a_null_x_is_dropped() {
        for labels in [false, true] {
            let mut p = layered_line_payload_with_cross_query_layer();
            p["resolved"]["layers"] = serde_json::json!([{
                "type": "bar",
                "bar_mark": {
                    "border": {"width": 1.0, "color": "#FFFFFF", "radius": 2.0},
                    "band_width": 0.8,
                    "labels": {"visible": labels, "position": "middle", "field": null, "format": null, "dx": null, "dy": null, "font": null}
                },
                "axis_y": {"position": null},
                "x": null, "y": "trend", "label": "Trend", "color": null, "ink": "#779bc9", "query_name": "trend_query"
            }]);
            p["datasets"]["trend_query"] = serde_json::json!([
                {"week": "2026-01-01", "trend": 110.0},
                {"week": null, "trend": 150.0}
            ]);
            if labels {
                p["board_style"] = serde_json::json!({
                    "font": TEST_FONT, "text_mark_font": {"family": TEST_FONT, "size": 11.0}, "secondary_color": null, "tones": null,
                    "title_case": null, "continuous_height": null,
                    "format_locale": crate::number_locale::en_us_wire(), "time_locale": crate::time_locale::en_us_wire()
                });
            }
            render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
                .unwrap_or_else(|e| panic!("labels {labels}: {e:?}"));
        }
    }

    /// A hollow line-layer point authors no `fill`: on a transparent card the
    /// engine's default interior is the chart's opaque `canvas` (`layers.rs`),
    /// never `background`.
    #[test]
    fn a_hollow_line_layer_point_knocks_out_with_the_canvas() {
        let mut p = layered_line_payload_with_cross_query_layer();
        p["resolved"]["layers"][0]["point_mark"]["filled"] = serde_json::json!(false);
        p["resolved"]["layers"][0]["point_mark"]["fill"] = serde_json::Value::Null;
        p["resolved"]["background"] = serde_json::json!("transparent");
        p["resolved"]["canvas"] = serde_json::json!("#d9a45b");
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let circle = svg
            .split("<circle ")
            .skip(1)
            .map(|c| &c[..c.find("/>").expect("circle end")])
            .find(|c| c.contains("stroke=\"#779bc9\""))
            .unwrap_or_else(|| panic!("no hollow layer point: {svg}"));
        assert!(circle.contains("fill=\"#d9a45b\""), "{circle}");
    }

    /// The shared-y-scale line-layer shape (`docs/laws-layers.md`): the
    /// layer's own line paints at the shared color scale's next unclaimed
    /// slot (`palette[1]`, since the colorless base seeds the scale with
    /// just its own one entry), and the shared scale itself nices past its
    /// own baked headroom once a second series joins it — the SAME quirk
    /// `bar.rs`'s own shared-scale layer arm already ports.
    #[test]
    fn a_line_layer_on_a_line_base_shares_the_niced_y_scale_and_paints_at_the_next_palette_slot() {
        let payload: Payload = serde_json::from_value(layered_line_payload_with_cross_query_layer()).unwrap();
        let svg = render(&payload, &test_fonts()).expect("a shared-scale line layer on a line base renders");

        assert!(svg.contains("stroke=\"#40639c\""), "base line paints at palette[0]: {svg}");
        assert!(svg.contains("stroke=\"#779bc9\""), "layer line paints at palette[1]: {svg}");

        // `d3_nice(0, 3654.68, 10) == 4000`: the baked domainMax renders
        // exact (3654.68, no "4,000" rung) without a layer, but a shared
        // scale flips Vega-Lite's `nice` default to true once the layer
        // joins it, re-admitting the baked ladder's own top rung.
        assert!(svg.contains("4,000"), "the niced domain re-admits the baked 4,000 rung: {svg}");
    }

    fn render_layered(edit: impl FnOnce(&mut serde_json::Value)) -> Result<String, crate::diagnostic::Diagnostic> {
        let mut p = layered_line_payload_with_cross_query_layer();
        edit(&mut p);
        render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
    }

    /// A log axis bakes no ladder, so the layers' share of the one y scale
    /// comes from their own rows: a layer reaching a decade below the base
    /// opens that decade (Vega-Lite unions the layers' domains).
    #[test]
    fn a_layer_over_a_log_base_widens_the_shared_log_domain_to_its_own_rows() {
        let log_base = |p: &mut serde_json::Value| {
            let ay = &mut p["resolved"]["style"]["axis_y"];
            ay["scale"] = serde_json::json!({"continuous": {"type": "log"}});
            ay["tick_values"] = serde_json::json!([]);
            ay["domain_max"] = serde_json::Value::Null;
            ay["tick_label"] = serde_json::Value::Null;
            ay["labels"]["format"] = serde_json::json!(".3~s");
            ay["position"] = serde_json::json!("right");
        };
        let wide = render_layered(|p| {
            log_base(p);
            p["datasets"]["trend_query"][0]["trend"] = serde_json::json!(5.0);
        })
        .expect("a log base beside a layer renders");
        let narrow = render_layered(log_base).expect("renders");
        assert!(wide.contains(">1<"), "the layer's decade opens the domain: {wide}");
        assert!(!narrow.contains(">1<"), "the base alone spans the layer's decade: {narrow}");
    }

    /// The area's zero baseline joins the shared log domain: its floor is zero,
    /// so no rung exists and every mark position is Vega's NaN, drawn as 0.
    #[test]
    fn an_area_layer_over_a_log_base_collapses_every_position_to_zero() {
        let svg = render_layered(|p| {
            let ay = &mut p["resolved"]["style"]["axis_y"];
            ay["scale"] = serde_json::json!({"continuous": {"type": "log"}});
            ay["tick_values"] = serde_json::json!([]);
            ay["domain_max"] = serde_json::Value::Null;
            ay["tick_label"] = serde_json::Value::Null;
            ay["labels"]["format"] = serde_json::json!(".3~s");
            ay["position"] = serde_json::json!("right");
            p["resolved"]["layers"][0]["type"] = serde_json::json!("area");
            p["resolved"]["layers"][0]["area_mark"] = serde_json::json!({"opacity": 0.3, "curve": "linear"});
        })
        .expect("renders");
        assert!(!svg.contains("NaN"), "{svg}");
        assert!(!svg.contains("role-axis-label"), "a zero-floored log axis draws no rung: {svg}");
    }

    #[test]
    fn a_layer_over_a_stacked_area_renders() {
        render_layered(|p| p["resolved"]["stack"] = serde_json::json!("zero")).expect("renders");
    }

    #[test]
    fn a_layer_naming_another_x_column_is_placed_on_that_column() {
        render_layered(|p| p["resolved"]["layers"][0]["x"] = serde_json::json!("week")).expect("renders");
        let svg = render_layered(|p| {
            p["resolved"]["layers"][0]["x"] = serde_json::json!("other");
            p["datasets"]["trend_query"].as_array_mut().unwrap().iter_mut().for_each(|row| {
                row["other"] = serde_json::json!("2026-06-01");
            });
        })
        .expect("renders");
        assert!(svg.contains(">Mar<"), "the layer's own x joins the axis: {svg}");
    }

    /// One line per color value, each at the next shared-scale slot after the base.
    #[test]
    fn a_color_grouped_layer_paints_one_line_per_value_at_successive_slots() {
        let svg = render_layered(|p| {
            p["resolved"]["palette"] = serde_json::json!(["#40639c", "#779bc9", "#608470"]);
            p["resolved"]["layers"][0]["color"] = serde_json::json!("region");
            p["resolved"]["layers"][0]["ink"] = serde_json::Value::Null;
            p["resolved"]["layers"][0]["series_inks"] = serde_json::json!({"East": "#779bc9", "West": "#608470"});
            p["datasets"] = serde_json::json!({"trend_query": [
                {"week": "2026-01-01", "trend": 110.0, "region": "East"},
                {"week": "2026-01-08", "trend": 140.0, "region": "East"},
                {"week": "2026-01-01", "trend": 90.0, "region": "West"},
                {"week": "2026-01-08", "trend": 120.0, "region": "West"}
            ]});
        })
        .expect("renders");
        assert!(svg.contains("stroke=\"#779bc9\"") && svg.contains("stroke=\"#608470\""), "{svg}");
    }

    fn render_grouped_layer(edit: impl FnOnce(&mut serde_json::Value)) -> String {
        render_layered(|p| {
            p["resolved"]["layers"][0]["color"] = serde_json::json!("region");
            p["resolved"]["layers"][0]["ink"] = serde_json::Value::Null;
            p["resolved"]["layers"][0]["series_inks"] = serde_json::json!({"East": "#779bc9", "West": "#608470"});
            edit(&mut p["resolved"]["layers"][0]);
            p["datasets"] = serde_json::json!({"trend_query": [
                {"week": "2026-01-01", "trend": 110.0, "region": "East"},
                {"week": "2026-01-08", "trend": 140.0, "region": "East"},
                {"week": "2026-01-01", "trend": 90.0, "region": "West"},
                {"week": "2026-01-08", "trend": 120.0, "region": "West"}
            ]});
        })
        .expect("renders")
    }

    /// An authored stroke pins the lines of a color-grouped layer; its points
    /// still follow their series.
    #[test]
    fn an_authored_stroke_pins_the_lines_of_a_color_grouped_layer_and_not_its_points() {
        let svg = render_grouped_layer(|layer| layer["line_mark"]["stroke"]["color"] = serde_json::json!("#cc0000"));
        assert_eq!(svg.matches("stroke=\"#cc0000\" stroke-width").count(), 2, "{svg}");
        assert!(!svg.contains("fill=\"#cc0000\""), "points keep their series colors: {svg}");
    }

    /// An authored point color pins the points of a color-grouped layer; its
    /// lines still follow their series.
    #[test]
    fn an_authored_point_color_pins_the_points_of_a_color_grouped_layer_and_not_its_lines() {
        let svg = render_grouped_layer(|layer| layer["point_mark"]["color"] = serde_json::json!("#cc0000"));
        assert!(!svg.contains("stroke=\"#cc0000\" stroke-width"), "lines keep their series colors: {svg}");
        assert_eq!(svg.matches("fill=\"#cc0000\"").count(), 4, "{svg}");
    }

    /// A layer paints the ink resolve baked for it: an authored literal ahead
    /// of it claims no palette color, so the layer after it holds slot 1.
    #[test]
    fn a_layer_after_an_authored_one_paints_its_baked_ink() {
        let svg = render_layered(|p| {
            p["resolved"]["palette"] = serde_json::json!(["#40639c", "#779bc9", "#608470"]);
            let mut authored = line_layer_json(Some("trend_query"));
            authored["line_mark"]["stroke"]["color"] = serde_json::json!("#cc0000");
            authored["ink"] = serde_json::json!("#cc0000");
            let mut scatter = scatter_layer_json();
            scatter["ink"] = serde_json::json!("#779bc9");
            p["resolved"]["layers"] = serde_json::json!([authored, scatter]);
        })
        .expect("renders");
        assert!(svg.matches("fill=\"#779bc9\"").count() >= 2, "marker points and legend swatch: {svg}");
        assert!(!svg.contains("#608470"), "{svg}");
    }

    /// The shared scale nices an authored pair once a layer joins it, a
    /// descending pair around the same rounding.
    #[test]
    fn an_authored_domain_beside_a_layer_is_nice_rounded() {
        for domain in [[0.0, 3654.68], [3654.68, 0.0]] {
            let svg = render_layered(|p| {
                p["resolved"]["style"]["axis_y"]["scale"] = serde_json::json!({"continuous": {"domain": domain}});
            })
            .expect("renders");
            assert!(svg.contains("4,000"), "{domain:?}: {svg}");
        }
    }

    #[test]
    fn a_layer_value_label_is_painted_and_joins_the_fit() {
        let plain = render_layered(|_| {}).expect("renders");
        let labeled = render_layered(|p| {
            p["resolved"]["layers"][0]["line_mark"]["labels"] = serde_json::json!({"visible": true});
        })
        .expect("renders");
        assert!(labeled.contains(">160</text>") && !plain.contains(">160</text>"), "{labeled}");
    }

    /// An area base keeps its circle legend glyph beside the layer's stroke.
    #[test]
    fn a_line_layer_over_an_area_base_renders() {
        let mut p = layered_line_payload_with_cross_query_layer();
        p["resolved"]["stack"] = serde_json::json!("none");
        let payload: Payload = serde_json::from_value(p).unwrap();
        render_inner(&payload, &test_fonts(), true).expect("renders");
    }

    fn scatter_layer_json() -> serde_json::Value {
        serde_json::json!({
            "type": "scatter",
            "point_mark": {"size": 100.0, "filled": true, "stroke_width": 2.0, "labels": {"visible": false}},
            "axis_y": {"position": null},
            "x": null, "y": "trend", "label": "Marker", "color": null, "ink": "#608470", "query_name": "trend_query"
        })
    }

    /// Layers stack in authored order: line layers take successive slots and
    /// a scatter layer follows them.
    #[test]
    fn two_line_layers_and_a_scatter_layer_take_successive_slots() {
        let svg = render_layered(|p| {
            p["resolved"]["palette"] = serde_json::json!(["#40639c", "#779bc9", "#608470", "#775770"]);
            let mut second = line_layer_json(Some("trend_query"));
            second["label"] = serde_json::json!("Second");
            second["ink"] = serde_json::json!("#608470");
            let mut scatter = scatter_layer_json();
            scatter["ink"] = serde_json::json!("#775770");
            p["resolved"]["layers"] = serde_json::json!([line_layer_json(Some("trend_query")), second, scatter]);
        })
        .expect("renders");
        for fill in ["#779bc9", "#608470", "#775770"] {
            assert!(svg.contains(fill), "{fill}: {svg}");
        }
        assert!(svg.contains(">Marker<"), "{svg}");
    }

    /// A label repeated over a plain base is legal: the ordinal color scale
    /// dedupes it, so the legend draws one row and the repeat keeps the color
    /// its own claim took.
    #[test]
    fn a_layer_label_repeated_across_layers_or_the_base_draws_one_legend_row() {
        let second = line_layer_json(Some("trend_query"));
        let svg = render_layered(|p| {
            p["resolved"]["layers"] = serde_json::json!([line_layer_json(Some("trend_query")), second.clone()]);
        })
        .expect("a repeated layer label renders");
        assert_eq!(svg.matches(">Trend</text>").count(), 1, "one legend row: {svg}");
        let mut base_named = line_layer_json(Some("trend_query"));
        base_named["label"] = serde_json::json!("signups");
        render_layered(|p| p["resolved"]["layers"] = serde_json::json!([base_named])).expect("the base's own title renders");
    }

    /// A layer label equal to a series of a color-split base would merge two
    /// legend rows: production raises `ERR_LAYER_LABEL_COLLISION`.
    #[test]
    fn a_layer_label_equal_to_a_grouped_base_series_is_rejected_by_name() {
        let err = render_layered(|p| {
            p["resolved"]["color"] = serde_json::json!("region");
            for (i, row) in p["rows"].as_array_mut().unwrap().iter_mut().enumerate() {
                row["region"] = serde_json::json!(if i % 2 == 0 { "East" } else { "West" });
            }
            p["resolved"]["layers"][0]["label"] = serde_json::json!("East");
        })
        .unwrap_err();
        assert_eq!(err.code, "ERR-LAYER-LABEL-COLLISION", "{err}");
        assert_eq!(err.params["label"], "East");
    }

    /// A grouped layer's value already on the scale shares that entry's color
    /// (`_overlay.py` skips a claimed value), never raising.
    #[test]
    fn a_grouped_layer_value_equal_to_another_layer_label_shares_its_color() {
        let mut plain = line_layer_json(Some("trend_query"));
        plain["label"] = serde_json::json!("West");
        let mut grouped = line_layer_json(Some("trend_query"));
        grouped["color"] = serde_json::json!("region");
        grouped["ink"] = serde_json::Value::Null;
        grouped["series_inks"] = serde_json::json!({"East": "#608470", "West": "#779bc9"});
        render_layered(|p| {
            p["resolved"]["layers"] = serde_json::json!([plain, grouped]);
            p["datasets"] = serde_json::json!({"trend_query": [
                {"week": "2026-01-01", "trend": 110.0, "region": "East"},
                {"week": "2026-01-08", "trend": 90.0, "region": "West"}
            ]});
        })
        .expect("a value already on the scale renders");
    }

    /// An `area` layer as production resolves it: an edge line with a stroke,
    /// halo and labels only (no `curve`, `connect` or `disconnected_width`).
    fn area_layer_json(opacity: f64, label: &str) -> serde_json::Value {
        serde_json::json!({
            "type": "area",
            "area_mark": {"opacity": opacity, "curve": null, "backdrop": false},
            "line_mark": {
                "stroke": {"width": 2.0, "color": null, "cap": null, "join": null, "dasharray": null},
                "halo_multiplier": 2.0,
                "labels": {"visible": false}
            },
            "point_mark": {"size": null, "color": null, "filled": true, "fill": null, "stroke_width": 2.0, "labels": {"visible": false}},
            "axis_y": {"position": null},
            "x": null, "y": "trend", "label": label, "color": null, "ink": "#779bc9", "query_name": "trend_query"
        })
    }

    /// Production unions a layer's x values into the shared scale: a layer row
    /// past the base's own x extends the axis.
    #[test]
    fn a_layer_row_outside_the_base_x_domain_widens_the_x_axis() {
        let widened = render_layered(|p| {
            p["datasets"]["trend_query"].as_array_mut().unwrap().push(serde_json::json!({"week": "2026-06-01", "trend": 170.0}));
        })
        .expect("renders");
        let own = render_layered(|_| {}).expect("rows on the base's own x render");
        assert!(widened.contains(">Mar<") && !own.contains(">Mar<"), "{widened}");
    }


    /// The palette errors belong to the layered path: a multi-series line
    /// without layers renders on an empty palette or past a short one.
    #[test]
    fn a_line_chart_without_layers_renders_on_an_empty_or_short_palette() {
        let render_with = |palette: serde_json::Value, scales: serde_json::Value| {
            let mut p = make_layered_line_payload();
            p["resolved"]["palette"] = palette;
            p["resolved"]["category_colors"] = scales;
            p["resolved"]["color"] = serde_json::json!("region");
            for (i, row) in p["rows"].as_array_mut().unwrap().iter_mut().enumerate() {
                row["region"] = serde_json::json!(if i % 2 == 0 { "East" } else { "West" });
            }
            render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
        };
        render_with(serde_json::json!([]), serde_json::json!([])).expect("an empty palette renders");
        render_with(
            serde_json::json!(["#40639c", "#779bc9"]),
            serde_json::json!([{"field": "other", "slots": {"x": 99}}]),
        )
        .expect("an unrelated board slot past the palette renders");
    }


    /// A scatter layer over an area base keeps the circle legend glyph of the
    /// base's rows.
    #[test]
    fn a_scatter_layer_over_an_area_base_renders() {
        let mut p = layered_line_payload_with_cross_query_layer();
        p["resolved"]["stack"] = serde_json::json!("none");
        p["resolved"]["layers"] = serde_json::json!([scatter_layer_json()]);
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert!(svg.contains(">Marker<") && !svg.contains("d=\"M-10,0L10,0\""), "{svg}");
    }

    /// An area layer's color groups stack where they share an x: the first
    /// value on top, the rest closing down onto the one below instead of the zero.
    #[test]
    fn an_area_layer_with_its_own_color_stacks_its_groups() {
        let mut layer = area_layer_json(0.16, "Area");
        layer["color"] = serde_json::json!("region");
        layer["ink"] = serde_json::Value::Null;
        layer["series_inks"] = serde_json::json!({"East": "#779bc9", "West": "#608470"});
        let svg = render_layered(|p| {
            p["resolved"]["palette"] = serde_json::json!(["#40639c", "#779bc9", "#608470"]);
            p["resolved"]["layers"] = serde_json::json!([layer]);
            p["datasets"] = serde_json::json!({"trend_query": [
                {"week": "2026-01-01", "trend": 110.0, "region": "East"},
                {"week": "2026-01-01", "trend": 40.0, "region": "West"},
                {"week": "2026-01-08", "trend": 140.0, "region": "East"},
                {"week": "2026-01-08", "trend": 50.0, "region": "West"}
            ]});
        })
        .expect("renders");
        let closing_y = |fill: &str| -> f64 {
            let at = svg.find(&format!("fill=\"{fill}\" fill-opacity=\"0.16\"")).expect(fill);
            let start = svg[..at].rfind("d=\"").expect("a path") + 3;
            let d = svg[start..at].split('"').next().expect("d");
            d.trim_end_matches('Z').rsplit(',').next().expect("a vertex").parse().expect("a number")
        };
        // West is on the bottom (closing on the plot's floor), East above it.
        assert!(closing_y("#779bc9") < closing_y("#608470"), "East closes onto West's top: {svg}");
    }

    /// With only area layers the base keeps the default short stroke; a plain
    /// line layer brings the long mixed stroke back. Every area circle takes the
    /// last area layer's opacity.
    #[test]
    fn area_layer_legend_glyphs_follow_the_mixed_stroke_law() {
        let area_only = render_layered(|p| {
            p["resolved"]["layers"] = serde_json::json!([area_layer_json(0.16, "A"), area_layer_json(0.3, "B")]);
        })
        .expect("renders");
        assert!(area_only.contains("d=\"M-5,0L5,0\""), "{area_only}");
        assert_eq!(area_only.matches("stroke-width=\"1.5\" opacity=\"0.3\"").count(), 2, "{area_only}");
        let mixed = render_layered(|p| {
            let mut line = line_layer_json(Some("trend_query"));
            line["label"] = serde_json::json!("Line");
            p["resolved"]["layers"] = serde_json::json!([area_layer_json(0.16, "A"), line]);
        })
        .expect("renders");
        assert!(mixed.contains("d=\"M-10,0L10,0\"") && !mixed.contains("d=\"M-5,0L5,0\""), "{mixed}");
    }

    #[test]
    fn an_area_layer_with_a_monotone_curve_curves_its_fill_and_its_edge_line() {
        let mut layer = area_layer_json(0.16, "Area");
        layer["area_mark"]["curve"] = serde_json::json!("monotone");
        let svg = render_layered(|p| p["resolved"]["layers"] = serde_json::json!([layer])).expect("renders");
        let paths: Vec<&str> = svg.match_indices("<path d=\"M").map(|(i, _)| svg[i..].split("/>").next().unwrap()).collect();
        let cubic = |tag: &&str| tag.contains('C');
        assert!(paths.iter().any(|t| t.contains("fill-opacity=\"0.16\"") && cubic(t)), "curved fill: {svg}");
        assert!(paths.iter().any(|t| t.contains("fill=\"none\"") && t.contains("#") && cubic(t)), "curved edge: {svg}");
    }

    #[test]
    fn an_area_type_without_an_area_mark_is_rejected_by_name() {
        let mut layer = area_layer_json(0.16, "Area");
        layer.as_object_mut().expect("an object").remove("area_mark");
        let err = render_layered(|p| p["resolved"]["layers"] = serde_json::json!([layer])).unwrap_err();
        assert!(err.contains("area_mark"), "{err}");
    }

    /// The fill takes the palette slot, closes down to the value zero and paints
    /// before the edge line; an authored stroke colors the edge only.
    #[test]
    fn an_area_layer_fills_its_palette_slot_down_to_zero_and_an_authored_stroke_colors_the_edge() {
        let svg = render_layered(|p| {
            let mut layer = area_layer_json(0.16, "Area");
            layer["line_mark"]["stroke"]["color"] = serde_json::json!("#cc0000");
            p["resolved"]["layers"] = serde_json::json!([layer]);
        })
        .expect("renders");
        let area_at = svg.find("mark-area").expect("an area mark");
        let line_at = svg[area_at..].find("mark-line").expect("the edge line follows") + area_at;
        assert!(svg[area_at..line_at].contains("fill=\"#779bc9\" fill-opacity=\"0.16\""), "{svg}");
        assert!(svg[line_at..].contains("stroke=\"#cc0000\" stroke-width"), "{svg}");
        assert!(!svg.contains("fill=\"#cc0000\""), "no fill or legend swatch takes the stroke: {svg}");
    }

    /// An authored domain that excludes the area's zero baseline clips the fill
    /// at the plot rect, as production's clipped layer does.
    #[test]
    fn an_area_layer_over_a_pinned_domain_excluding_zero_closes_at_the_plot_bottom() {
        let svg = render_layered(|p| {
            p["resolved"]["layers"] = serde_json::json!([area_layer_json(0.16, "Area")]);
            p["resolved"]["style"]["axis_y"]["scale"] = serde_json::json!({"continuous": {"domain": [1000.0, 3000.0]}});
        })
        .expect("renders");
        let area = &svg[svg.find("mark-area").expect("an area mark")..];
        let d = area.split("d=\"").nth(1).and_then(|r| r.split('"').next()).expect("the area path");
        let ys: Vec<f64> = d
            .split('L')
            .skip(1)
            .map(|pt| pt.split(',').nth(1).expect("y").trim_end_matches('Z').parse().expect("number"))
            .collect();
        // The payload's plot is 300 tall; zero sits below the domain's floor, so the
        // fill is clipped at the plot rect rather than squeezed onto its edge.
        assert!(ys[ys.len() - 3..].iter().all(|y| *y > 300.0), "{ys:?}: {svg}");
        assert!(svg.contains("<clipPath id=\"dbt-area-clip-") && svg.contains("height=\"300\"/></clipPath>"), "{svg}");
    }

    /// A scatter layer over a color-grouped base renders beside the base's circle glyphs.
    #[test]
    fn a_scatter_layer_over_a_color_grouped_base_renders() {
        let grouped = |p: &mut serde_json::Value, layer: serde_json::Value| {
            p["resolved"]["color"] = serde_json::json!("region");
            for (i, row) in p["rows"].as_array_mut().unwrap().iter_mut().enumerate() {
                row["region"] = serde_json::json!(if i % 2 == 0 { "East" } else { "West" });
            }
            p["resolved"]["layers"] = serde_json::json!([layer]);
        };
        render_layered(|p| grouped(p, scatter_layer_json())).expect("a scatter layer renders");
        render_layered(|p| grouped(p, line_layer_json(Some("trend_query")))).expect("a line layer renders");
    }

    /// `_mixed_mark_legend_symbols` patches the stroke glyph only when a line
    /// layer is present: a line base with scatter layers alone keeps Vega's
    /// default short stroke.
    #[test]
    fn a_line_base_with_only_scatter_layers_keeps_the_short_stroke_glyph() {
        let scatter_only = render_layered(|p| p["resolved"]["layers"] = serde_json::json!([scatter_layer_json()])).expect("renders");
        assert!(scatter_only.contains("d=\"M-5,0L5,0\"") && !scatter_only.contains("d=\"M-10,0L10,0\""), "{scatter_only}");
        let with_line = render_layered(|_| {}).expect("renders");
        assert!(with_line.contains("d=\"M-10,0L10,0\""), "{with_line}");
    }

    /// A color-grouped line layer's paths are scope groups, unclipped unlike the
    /// base's, so the stroke-expanded top of one on the plot's top edge joins `yBounds`
    /// and pushes a top legend up by it; an ungrouped layer's bare mark does not.
    #[test]
    fn a_color_grouped_line_layer_at_the_plot_top_pushes_a_top_legend_up_by_its_stroke() {
        let plot_y = |grouped: bool| -> f64 {
            let mut layer = line_layer_json(Some("trend_query"));
            layer["axis_y"]["position"] = serde_json::json!("right");
            layer["line_mark"]["stroke"]["width"] = serde_json::json!(5.0);
            if grouped {
                layer["color"] = serde_json::json!("region");
                layer["ink"] = serde_json::Value::Null;
                layer["series_inks"] = serde_json::json!({"East": "#779bc9", "West": "#608470"});
            }
            let svg = render_layered(|p| {
                p["resolved"]["palette"] = serde_json::json!(["#40639c", "#779bc9", "#608470"]);
                p["resolved"]["legend"]["title"]["visible"] = serde_json::json!(false);
                p["resolved"]["layers"] = serde_json::json!([layer]);
                p["datasets"] = serde_json::json!({"trend_query": [
                    {"week": "2026-01-01", "trend": 50.0, "region": "East"},
                    {"week": "2026-01-08", "trend": 200.0, "region": "West"},
                    {"week": "2026-01-15", "trend": 100.0, "region": "East"}
                ]});
            })
            .expect("renders");
            let at = svg.find("<g transform=\"translate(").expect("the plot group") + "<g transform=\"translate(".len();
            svg[at..].split(')').next().and_then(|t| t.split(',').nth(1)).expect("y").parse().expect("a number")
        };
        assert_eq!(plot_y(true) - plot_y(false), 5.0, "{} vs {}", plot_y(true), plot_y(false));
    }

    /// Production drops a null color value from the scale's domain but still
    /// draws the row as a series with no color: no stroke, and no legend entry.
    #[test]
    fn a_null_color_value_on_a_color_grouped_line_layer_is_a_series_with_no_stroke() {
        let svg = render_layered(|p| {
            p["resolved"]["layers"][0]["color"] = serde_json::json!("region");
            p["resolved"]["layers"][0]["ink"] = serde_json::Value::Null;
            p["resolved"]["layers"][0]["series_inks"] = serde_json::json!({"West": "#779bc9"});
            p["datasets"] = serde_json::json!({"trend_query": [
                {"week": "2026-01-01", "trend": 110.0, "region": null},
                {"week": "2026-01-08", "trend": 140.0, "region": "West"}
            ]});
        })
        .expect("renders");
        assert!(svg.contains("stroke=\"none\" stroke-width"), "the null group strokes nothing: {svg}");
        assert_eq!(svg.matches(">West</text>").count(), 1, "{svg}");
        assert!(svg.contains("stroke=\"#779bc9\" stroke-width"), "West takes palette[1], the null claims none: {svg}");
    }

    /// Rows of an area layer sharing an x stack in row order, so over a grouped
    /// base each x's wedge spans both rows.
    #[test]
    fn an_area_layer_over_a_grouped_base_stacks_the_rows_sharing_an_x() {
        let svg = render_layered(|p| {
            p["resolved"]["color"] = serde_json::json!("region");
            for (i, row) in p["rows"].as_array_mut().unwrap().iter_mut().enumerate() {
                row["region"] = serde_json::json!(if i % 2 == 0 { "East" } else { "West" });
            }
            p["resolved"]["layers"] = serde_json::json!([area_layer_json(0.16, "Area")]);
        })
        .expect("renders");
        assert!(svg.contains("fill-opacity=\"0.16\""), "{svg}");
    }

    /// Layers claim palette slots and paint in authored order whatever their type.
    #[test]
    fn a_line_layer_after_a_scatter_layer_takes_the_next_slot_and_paints_after_it() {
        let svg = render_layered(|p| {
            p["resolved"]["palette"] = serde_json::json!(["#40639c", "#779bc9", "#608470", "#775770"]);
            let mut scatter = scatter_layer_json();
            scatter["ink"] = serde_json::json!("#779bc9");
            let mut line = line_layer_json(Some("trend_query"));
            line["ink"] = serde_json::json!("#608470");
            p["resolved"]["layers"] = serde_json::json!([scatter, line]);
        })
        .expect("renders");
        let (marker, trend) = (svg.find(">Marker<").expect("marker legend"), svg.find(">Trend<").expect("trend legend"));
        assert!(marker < trend, "legend entries follow authored order: {svg}");
        let (scatter_ink, line_ink) = (svg.find("#779bc9").expect("scatter slot"), svg.find("#608470").expect("line slot"));
        assert!(scatter_ink < line_ink, "the scatter layer claims and paints first: {svg}");
    }

    #[test]
    fn a_scatter_layer_with_no_point_size_payload_is_rejected_by_name() {
        let mut layer = scatter_layer_json();
        layer["point_mark"] = serde_json::json!({"stroke_width": 2.0, "labels": {"visible": false}});
        let err = render_layered(|p| p["resolved"]["layers"] = serde_json::json!([layer])).unwrap_err();
        assert!(err.contains("point_mark.size"), "{err}");
    }

    #[test]
    /// A scatter layer grouped by its own color paints each point in its row's
    /// value color, over an authored point color, and draws its values as circles.
    fn a_scatter_layer_with_its_own_color_paints_each_point_in_its_values_color() {
        let mut layer = scatter_layer_json();
        layer["color"] = serde_json::json!("region");
        layer["ink"] = serde_json::Value::Null;
        layer["series_inks"] = serde_json::json!({"East": "#779bc9", "West": "#608470"});
        layer["point_mark"]["color"] = serde_json::json!("#cc0000");
        let svg = render_layered(|p| {
            p["resolved"]["palette"] = serde_json::json!(["#40639c", "#779bc9", "#608470"]);
            p["resolved"]["layers"] = serde_json::json!([layer]);
            p["datasets"] = serde_json::json!({"trend_query": [
                {"week": "2026-01-01", "trend": 110.0, "region": "West"},
                {"week": "2026-01-08", "trend": 140.0, "region": "East"},
                {"week": "2026-01-15", "trend": 160.0, "region": null}
            ]});
        })
        .expect("renders");
        assert!(!svg.contains("#cc0000"), "the encoding outranks the mark's own color: {svg}");
        for fill in ["#779bc9", "#608470"] {
            assert!(svg.contains(&format!("r=\"5.000\" fill=\"{fill}\"")), "{fill}: East takes palette[1], West palette[2]: {svg}");
        }
        assert!(svg.contains("r=\"5.000\" fill=\"none\""), "the null row's point has no color: {svg}");
        assert_eq!(svg.matches("d=\"M5,0A5,5,0,1,1,-5,0A5,5,0,1,1,5,0\"").count(), 2, "one circle glyph per value: {svg}");
    }

    /// Every labeled layer prints its captions, the line layer's and the
    /// scatter layer's alike.
    #[test]
    fn every_labeled_layer_prints_its_values() {
        let svg = render_layered(|p| {
            let mut first = line_layer_json(Some("trend_query"));
            first["line_mark"]["labels"] = serde_json::json!({"visible": true});
            let mut scatter = scatter_layer_json();
            scatter["point_mark"]["labels"] = serde_json::json!({"visible": true, "position": "bottom"});
            p["resolved"]["layers"] = serde_json::json!([first, scatter]);
        })
        .expect("renders");
        assert!(svg.matches("font-size=\"11\"").count() > 4, "{svg}");
    }

    /// A layer's `labels.field` prints that column instead of the measure.
    #[test]
    fn a_layer_value_label_prints_another_field() {
        let svg = render_layered(|p| {
            p["resolved"]["layers"][0]["line_mark"]["labels"] = serde_json::json!({"visible": true, "field": "note"});
            for (i, row) in p["datasets"]["trend_query"].as_array_mut().unwrap().iter_mut().enumerate() {
                row["note"] = serde_json::json!(format!("n{i}"));
            }
        })
        .expect("renders");
        assert!(svg.contains(">n0</text>") && svg.contains(">n2</text>"), "{svg}");
    }

    /// `_mixed_mark_legend_symbols`: a line base's own `base_symbol` IS
    /// `stroke` (unlike bar's `square`), so BOTH the base's and the layer's
    /// legend entries carry the `Stroke` glyph (`d="M-10,0L10,0"`) — two of
    /// them, each in its own series fill.
    #[test]
    fn a_line_layer_on_a_line_base_yields_two_stroke_legend_entries() {
        let payload: Payload = serde_json::from_value(layered_line_payload_with_cross_query_layer()).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");

        assert_eq!(svg.matches("d=\"M-10,0L10,0\"").count(), 2, "two Stroke legend glyphs: {svg}");
        assert!(
            svg.contains("fill=\"#40639c\" stroke=\"#40639c\""),
            "base legend swatch strokes its own fill: {svg}"
        );
        assert!(
            svg.contains("fill=\"#779bc9\" stroke=\"#779bc9\""),
            "layer legend swatch strokes its own fill: {svg}"
        );
        assert!(svg.contains(">Signups<"), "base's own default legend label: {svg}");
        assert!(svg.contains(">Trend<"), "layer's authored label: {svg}");
    }

    /// A visible tick of length 0 draws and reserves nothing, so it is the
    /// hidden-tick chart and does not refuse.
    #[test]
    fn a_zero_length_visible_tick_renders() {
        let mut json = make_line_payload(false, false);
        json["resolved"]["style"]["axis_y"]["ticks"] =
            serde_json::json!({"visible": true, "count": 6.0, "length": 0.0});
        render(&serde_json::from_value::<Payload>(json).unwrap(), &test_fonts())
            .expect("a zero-length tick is the hidden-tick chart");
    }

    /// A layer type that is none of line, area, scatter or bar is named before a
    /// struct deserialize even runs.
    #[test]
    fn an_unknown_layer_type_on_a_line_base_is_rejected_by_name() {
        let mut json = make_line_payload(false, false);
        json["resolved"]["layers"] = serde_json::json!([{"type": "heatmap", "y": "trend"}]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).unwrap_err();
        assert!(err.contains("\"heatmap\""), "names the layer type: {err}");
        assert!(err.starts_with("line:"), "names the base family: {err}");
    }

    /// A pinned layer on a line base owns an independent scale: the base re-pins
    /// to the opposite edge and the layer's own axis takes its own ladder.
    #[test]
    fn a_pinned_layer_on_a_line_base_gets_its_own_axis_on_the_side_it_pins() {
        for side in ["right", "left"] {
            let mut json = make_line_payload(true, false);
            let mut layer = line_layer_json(None);
            layer["axis_y"]["position"] = serde_json::json!(side);
            layer["y"] = serde_json::json!("signups");
            layer["axis_y"]["title"] = serde_json::json!("Layer title");
            json["resolved"]["layers"] = serde_json::json!([layer]);
            let payload: Payload = serde_json::from_value(json).unwrap();
            let svg = render(&payload, &test_fonts()).expect("renders");
            // The title rotates away from the plot: +90 on a right axis, -90 on a left one.
            let title = svg
                .split("<text ")
                .find(|t| t.contains(">Layer title<") || t.contains(">Layer title</"))
                .unwrap_or_else(|| panic!("{side}: the layer's own title: {svg}"));
            let rotation = if side == "right" { "rotate(90 " } else { "rotate(-90 " };
            assert!(title.contains(rotation), "{side}: {title}");
            // The base's own labels take the edge the layer leaves: a right-pinned
            // layer moves a right base left (end-anchored, 2px padding).
            let origin_x: f64 = svg.split("<g transform=\"translate(").nth(1).and_then(|r| r.split(',').next()).unwrap().parse().unwrap();
            let base_label = svg.split("<text ").find(|t| t.contains(">130</text>") || t.contains(">150</text>")).expect("a base label");
            let x: f64 = base_label.split("x=\"").nth(1).and_then(|r| r.split('"').next()).unwrap().parse().unwrap();
            assert_eq!(x < origin_x + 100.0, side == "right", "{side}: base label x {x}: {base_label}");
        }
    }


    /// The plot group's translate y — `pad_t + top_margin`.
    fn group_y(svg: &str) -> f64 {
        let i = svg.find("<g transform=\"translate(").expect("plot group present");
        svg[i..].split(',').nth(1).unwrap().split(')').next().unwrap().parse().unwrap()
    }

    /// The `<svg>` element's own `width`/`height` — the composed arm grows
    /// them past the payload dims.
    fn canvas_dims(svg: &str) -> (f64, f64) {
        let attr = |name: &str| -> f64 {
            svg.split(&format!("{name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        (attr("width"), attr("height"))
    }

    /// The main (foreground) line's own `d` attribute: the LAST
    /// `fill="none"` stroked path — halo lines (also `fill="none"`) paint
    /// before it, so `.last()` always lands on the foreground stroke.
    fn main_line_d(svg: &str) -> String {
        svg.match_indices("<path d=\"M")
            .map(|(i, _)| svg[i..].split("/>").next().unwrap())
            .filter(|tag| tag.contains("fill=\"none\""))
            .last()
            .expect("main line path present")
            .split("d=\"")
            .nth(1)
            .unwrap()
            .split('"')
            .next()
            .unwrap()
            .to_string()
    }

    fn plot_group_x(svg: &str) -> f64 {
        let idx = svg.find("<g transform=\"translate(").expect("plot group present");
        svg[idx..idx + 60]
            .split("translate(")
            .nth(1)
            .unwrap()
            .split(',')
            .next()
            .unwrap()
            .parse()
            .unwrap()
    }

    fn support_table_style_json() -> serde_json::Value {
        serde_json::json!({
            "font": {"family": TEST_FONT, "size": 11.0, "color": "#6F685D"},
            "divider": {"gap": 4.0, "visible": false, "color": "#626366", "width": 1.0, "dasharray": null},
            "row": {"padding": {"vertical": 2.0, "horizontal": 4.0},
                    "divider": {"visible": false, "color": "#626366", "width": 1.0, "dasharray": null}},
            "label": {"font": {"family": TEST_FONT, "size": 11.0, "color": "#6F685D"}},
            "padding_top": 8.0,
            "padding_bottom": 4.0,
            "label_max_lines": 2,
            "position": "top",
        })
    }

    fn support_table_style_json_bottom() -> serde_json::Value {
        let mut v = support_table_style_json();
        v["position"] = serde_json::json!("bottom");
        v
    }

    fn make_multi_series_line_payload_with_bottom_legend() -> serde_json::Value {
        let rows = serde_json::json!([
            {"week": "2026-01-01", "series": "A", "signups": 100.0},
            {"week": "2026-01-08", "series": "A", "signups": 150.0},
            {"week": "2026-01-01", "series": "B", "signups": 40.0},
            {"week": "2026-01-08", "series": "B", "signups": 60.0},
        ]);
        let resolved = serde_json::json!({
            "id": "c", "title": null, "title_style": null, "background": "#ffffff", "canvas": "#ffffff",
            "x": "week", "y": "signups",
            "color": "series", "stack": "none",
            "palette": ["#3164a3", "#779bc9"],
            "multiples": null, "field_labels": {}, "wide_measures": [], "layers": [],
            "legend": {
                "visible": true, "position": {"edge": "bottom", "align": "start", "overlay": false}, "direction": "vertical",
                "title": {"font": {"case": "title", "family": TEST_FONT}},
                "label": {"padding": 8.0, "font": {"family": TEST_FONT, "size": 11.0}}
            },
            "style": {
                "axis_x": axis_json(false, None),
                "axis_y": value_axis_json(false, None),
                "line_mark": {"stroke": {"width": 4.0, "cap": "round", "join": "round"}, "halo_multiplier": 1.5},
                "series_label": {"font_family": TEST_FONT, "font_size": 11.0, "font_weight": "400", "dark_companion_palette": [], "pin_companions": [], "gap_px": 8.0},
                "endpoint_labels": {"visible": false, "label_offset": 6.0},
                "single_series_fill": "#3164a3",
                "label_usable_ratio": 0.8
            }
        });
        serde_json::json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0}
        })
    }

    // ── Front-end contract: structured-tooltip aria-labels ──────────────

    /// Each datum is announced by exactly one mark.
    #[test]
    fn a_datum_is_announced_by_exactly_one_mark() {
        use serde_json::json;
        let labels = |point_size: Option<f64>| {
            let mut p = make_line_payload(true, false);
            p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
            if let Some(sz) = point_size {
                p["resolved"]["style"]["point_mark"] = json!({"size": sz});
            }
            let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
                .expect("renders");
            (svg.matches("; Signups: 1").count(), svg)
        };
        let (with_points, svg) = labels(Some(46.24));
        assert_eq!(with_points, 3, "{svg}");
        assert!(!svg.contains("opacity=\"0\" data-dct-role=\"point\" aria-label"), "{svg}");
        let (without_points, svg) = labels(None);
        assert_eq!(without_points, 3, "{svg}");
        assert!(svg.contains("opacity=\"0\" data-dct-role=\"point\" aria-label"), "{svg}");

        // An area follows the same law: a drawn point sub-layer announces each
        // datum, so its hover target is a hidden mark set; with no point
        // drawn the hover target announces (`announce_hover_target=not
        // point_layers`).
        let area_svg = |point_size: Option<f64>| {
            let mut p = make_stacked_area_payload("zero");
            p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
            if let Some(sz) = point_size {
                p["resolved"]["style"]["point_mark"] = json!({"size": sz});
            }
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true)
                .expect("renders")
        };
        let svg = area_svg(Some(46.24));
        assert!(!svg.contains("opacity=\"0\" data-dct-role=\"point\" aria-label"), "{svg}");
        assert!(svg.contains("<g class=\"mark-symbol role-mark layer_3_marks\" aria-hidden=\"true\"><circle"), "{svg}");
        let svg = area_svg(None);
        assert!(svg.contains("opacity=\"0\" data-dct-role=\"point\" aria-label"), "{svg}");
        // Only the reference rules are hidden.
        assert_eq!(
            svg.matches("aria-hidden").count(),
            svg.matches("mark-rule role-mark").count() + svg.matches("role-axis-grid").count(),
            "{svg}"
        );
    }

    /// `tooltip_format` present wraps every datum's invisible hover-target
    /// point in a `.role-mark` group with a structured aria-label and hides the
    /// zero rule; absent, today's unwrapped/unlabeled status quo.
    #[test]
    fn tooltip_format_wraps_every_point_and_hides_the_zero_rule() {
        use serde_json::json;
        let mut p = make_line_payload(true, false);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(true);
        p["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
            json!({"color": "#8A8C8F", "width": 2.0});
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(
            svg.contains("<g class=\"mark-symbol role-mark layer_3_marks\""),
            "hover-target wraps in a role-mark group: {svg}"
        );
        assert!(svg.contains("r=\"8.944\" fill=\"#3164a3\" opacity=\"0\""), "{svg}");
        assert!(svg.contains("Signups: 100"), "{svg}");
        assert!(svg.contains("Signups: 150"), "{svg}");
        assert!(svg.contains("Signups: 130"), "{svg}");
        assert!(
            svg.contains("<g class=\"mark-rule role-mark layer_0_marks\" aria-hidden=\"true\""),
            "the zero rule is hidden from screen readers: {svg}"
        );
        assert!(!svg.contains("aria-label=\"Signups: 0\""), "{svg}");

        let plain = render(
            &serde_json::from_value::<Payload>(make_line_payload(true, false)).unwrap(),
            &test_fonts(),
        )
        .expect("renders");
        assert!(!plain.contains("graphics-object"));
        assert!(crate::contract::has_no_mark_labels(&plain));
    }

    /// The value labels are ONE text mark set stamped `data-dbt-value-label`
    /// (`converters/chart.py::_stamp_value_label_layers`), each label
    /// carrying the byte-identical aria-label of its own point
    /// (`charts/line-graphs_10/revenue_labeled`) -- `recedableMarks` reaches
    /// a text mark only through the stamp.
    #[test]
    fn value_labels_are_one_stamped_group_twinned_to_their_points() {
        use serde_json::json;
        let mut p = make_line_payload(true, false);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        set_value_labels(&mut p, ",.1f");
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert_eq!(svg.matches("data-dbt-value-label=\"true\"").count(), 1, "{svg}");
        let open = svg.find("<g class=\"mark-text role-mark ").expect("text group");
        let group = &svg[open..open + svg[open..].find("</g>").unwrap()];
        assert!(group.contains("data-dbt-value-label=\"true\" role=\"graphics-object\""), "{group}");
        assert_eq!(group.matches("<text ").count(), 3, "every label inside the one group: {group}");
        for text in ["100.0", "150.0", "130.0"] {
            let tag = text_tag(&svg, text);
            assert!(tag.contains(" role=\"graphics-symbol\" aria-roledescription=\"text mark\""), "{tag}");
            let aria = tag.split("aria-label=\"").nth(1).unwrap().split('"').next().unwrap();
            let point = format!("aria-label=\"{aria}\" role=\"graphics-symbol\" aria-roledescription=\"point\"");
            assert!(svg.contains(&point), "the label's point carries the same description: {aria}");
        }
    }

    /// An authored label font style reaches the value-label `<text>`.
    #[test]
    fn an_authored_italic_value_label_font_reaches_the_text() {
        use serde_json::json;
        let mut p = make_line_payload(true, false);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        set_value_labels(&mut p, ",.1f");
        p["resolved"]["style"]["line_mark"]["labels"]["font"]["style"] = json!("italic");
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(text_tag(&svg, "100.0").contains(" font-style=\"italic\""), "{svg}");
    }

    /// The invisible per-datum hover target is a `point` item at
    /// `opacity="0"` -- `chart_interactivity.js`'s `isStrokedMark` keys the
    /// tooltip swatch (a line, not a filled square) on exactly that pair.
    #[test]
    fn the_hover_target_is_a_point_item_at_zero_opacity() {
        use serde_json::json;
        let mut p = make_line_payload(true, false);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert_eq!(
            svg.matches("opacity=\"0\" data-dct-role=\"point\" aria-label=\"\u{2061}1\u{2013}7 Jan 2026; Signups: 100\" \
                         role=\"graphics-symbol\" aria-roledescription=\"point\"").count(),
            1,
            "{svg}"
        );
    }

    /// A multi-series chart's own series/order roles: `_series_order_role`'s
    /// `legend_domain` (last-value order) ranks the tied-drawn "A"/"B"
    /// series -- A's last value (150) beats B's (60), so A ranks 0.
    #[test]
    fn tooltip_format_multi_series_carries_series_and_order_roles() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains("⁢A; ‌0; Signups: 100"), "{svg}");
        assert!(svg.contains("⁢B; ‌1; Signups: 40"), "{svg}");
    }

    /// A stacked (`stack: zero`) area's group `Σ` footer -- unconditional
    /// once the stack/negative gate passes, same law as bar's own stacked
    /// total.
    #[test]
    fn tooltip_format_stacked_zero_area_carries_a_total_footer() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert!(svg.contains("Signups: 100; ⁣Σ: 140"), "{svg}");
        assert!(svg.contains("Signups: 40; ⁣Σ: 140"), "{svg}");
    }

    /// A `stack: center` (streamgraph) silhouette is NOT commensurable --
    /// `_stack_group_total`'s own gate excludes it, so no Σ footer
    /// renders even though the chart is stacked.
    #[test]
    fn tooltip_format_stacked_center_area_carries_no_total_footer() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("center");
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert!(svg.contains("Signups: 100"), "{svg}");
        assert!(!svg.contains("Σ"), "{svg}");
    }

    /// A band-step row is doubled into the pair at its band's two edges
    /// (`apply_step_band`), both announcing the row's one description.
    #[test]
    fn tooltip_format_labels_a_band_step_row_on_both_of_its_edges() {
        use serde_json::json;
        let mut band = make_band_step_line_payload(None);
        band["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        let svg = render(&serde_json::from_value::<Payload>(band).unwrap(), &test_fonts()).expect("renders");
        let labels: Vec<&str> = svg
            .split("<circle ")
            .filter(|tag| tag.contains("aria-roledescription=\"point\"") && tag.contains("aria-label="))
            .collect();
        assert!(!labels.is_empty(), "{svg}");
        assert_eq!(labels.len() % 2, 0, "every row announces on both edges: {svg}");
    }

    /// The board plans `category_colors` once per board (#7167): a value's
    /// slot wins over this chart's own alphabetical position — "A" sorts
    /// first locally (would take palette[0]) but owns board slot 1, "B"
    /// owns slot 0. Fill AND legend swatch both follow the board slot.
    #[test]
    fn board_slot_wins_the_series_fill_over_chart_local_alphabetical_order() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["category_colors"] =
            json!([{"field": "series", "slots": {"A": 1, "B": 0}, "overrides": {}}]);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let fill_before_label = |label: &str| -> String {
            let marker = format!(">{label}</text>");
            let end = svg.find(&marker).unwrap_or_else(|| panic!("label {label} present: {svg}"));
            let text_start = svg[..end].rfind("<text").expect("owning <text> tag present");
            let head = &svg[..text_start];
            let fill_idx = head.rfind("fill=\"").expect("swatch fill present");
            head[fill_idx + 6..].split('"').next().unwrap().to_string()
        };
        assert_eq!(fill_before_label("A"), "#779bc9", "{svg}");
        assert_eq!(fill_before_label("B"), "#3164a3", "{svg}");

        // The stroke itself, not just the legend swatch — A's points (100,
        // 150) sit higher (smaller y) than B's (40, 60), so the smaller
        // first-vertex y identifies A's line.
        let strokes: Vec<(i64, &str)> = svg
            .match_indices("<path d=\"M")
            .map(|(i, _)| svg[i..].split("/>").next().unwrap())
            .filter(|t| t.contains("fill=\"none\"") && !t.contains("stroke=\"#ffffff\""))
            .map(|t| {
                let y: f64 =
                    t.split("d=\"M").nth(1).unwrap().split(['L', ' ']).next().unwrap().split(',').nth(1).unwrap().parse().unwrap();
                (y.round() as i64, t.split("stroke=\"").nth(1).unwrap().split('"').next().unwrap())
            })
            .collect();
        assert_eq!(strokes.len(), 2, "one stroked line per series: {svg}");
        let (a_stroke, b_stroke) = if strokes[0].0 < strokes[1].0 {
            (strokes[0].1, strokes[1].1)
        } else {
            (strokes[1].1, strokes[0].1)
        };
        assert_eq!(a_stroke, "#779bc9", "A's stroke follows its board slot: {svg}");
        assert_eq!(b_stroke, "#3164a3", "B's stroke follows its board slot: {svg}");
    }

    /// area.rs is `line::render_inner(.., draw_areas=true)`, so the area
    /// fill path shares the same board-scale wiring as the line stroke —
    /// pinned separately since area paints a distinct layer (colored area
    /// fills, not strokes). "A" draws first (data order) but owns board
    /// slot 1; "B" second, slot 0.
    #[test]
    fn area_board_slot_wins_the_series_fill_over_chart_local_alphabetical_order() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["style"]["area_mark"] = json!({"opacity": 0.5, "backdrop": false});
        p["resolved"]["category_colors"] =
            json!([{"field": "series", "slots": {"A": 1, "B": 0}, "overrides": {}}]);
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true)
            .expect("renders");
        let fills: Vec<&str> = svg
            .match_indices("fill-opacity=\"0.5\"")
            .map(|(i, _)| {
                let head = &svg[..i];
                let fill_idx = head.rfind("fill=\"").expect("area fill present");
                head[fill_idx + 6..].split('"').next().unwrap()
            })
            .collect();
        assert_eq!(fills, vec!["#779bc9", "#3164a3"], "{svg}");
    }

    /// The area backdrop's fill is `chart.canvas` — the resolved chart's
    /// OWN opaque composited canvas — never the board-level
    /// `Payload.background`, nor the chart's own `background`, which may be
    /// `transparent` (`emit_area_layer`'s `background` arg,
    /// `emitters/area.py:520` and `:633`, both `chart.canvas`; the resolved
    /// field is `_SharedResolvedChartFields.canvas`,
    /// `compile/models/chart/resolved/_base.py:169`). The canvas rect still
    /// paints `chart.background` (`session.py:184`). A chart in a
    /// multi-chart board resolves `background: transparent` over its card.
    #[test]
    fn area_backdrop_fill_reads_the_chart_own_canvas_not_the_background() {
        use serde_json::json;
        let mut p = make_line_payload(true, false);
        p["resolved"]["style"]["area_mark"] = json!({"opacity": 1.0, "backdrop": true});
        p["resolved"]["background"] = json!("transparent");
        p["resolved"]["canvas"] = json!("#d9a45b");
        p["background"] = json!("#ffffff");
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true)
            .expect("renders");
        let backdrop_fill = svg
            .match_indices("fill-opacity=\"1\"/>")
            .next()
            .map(|(i, _)| {
                let head = &svg[..i];
                let fill_idx = head.rfind("fill=\"").expect("backdrop fill present");
                head[fill_idx + 6..].split('"').next().unwrap()
            })
            .expect("area backdrop path present");
        assert_eq!(backdrop_fill, "#d9a45b", "{svg}");
        // The canvas rect is the chart-level background
        // (`session.py:184` sets the VL spec background to
        // `chart.background`; `presentation.py:74` writes it).
        let canvas_rect = svg.split("<rect ").nth(1).expect("canvas rect present");
        assert!(canvas_rect.starts_with("width=") && canvas_rect.contains("fill=\"transparent\"/>"), "{svg}");
    }

    /// A single-series area's top-edge line is on the SAME stacked recipe a
    /// genuinely stacked area is (`resolve/chart/area.py`'s `on_stacked_
    /// recipe = is_stacked or is_single_series`, `chart.stack` staying
    /// `"none"` either way): the resolve-time swap already replaced
    /// `line_mark.stroke` wholesale with `marks.area.stacked.stroke`
    /// (`theme.background`, white by default) before this payload was
    /// built, so `line_mark.stroke.color` carries an explicit color same as
    /// a stacked chart's does. `_area_style_props`/`_line_fg_sublayers`
    /// (`emitters/_layers.py:576-577`, `:283-288`) paint that explicit
    /// color unconditionally — `has_explicit_line_color` short-circuits the
    /// single-series color fallback entirely — so the unstacked ("overlap")
    /// paint arm must read it too, not just the stacked arm's already-ported
    /// `is_stacked` branch. This renderer used to always paint the base
    /// series' own fill here, ignoring a resolved `line_mark.stroke.color`
    /// on the unstacked arm — `playground/card-background-parity-lab`'s
    /// `trend` area strokes its top edge with the series color `#3164a3`
    /// instead of the resolved white.
    #[test]
    fn single_series_area_top_edge_strokes_the_resolved_color_not_the_series_fill() {
        use serde_json::json;
        let mut p = make_line_payload(false, false);
        p["resolved"]["style"]["area_mark"] = json!({"opacity": 1.0, "backdrop": true});
        p["resolved"]["style"]["line_mark"] = json!({
            "stroke": {"width": 1.0, "color": "#ffffff", "cap": "round", "join": "round"},
            "halo_multiplier": 0.0
        });
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true)
            .expect("renders");
        assert!(
            svg.contains("stroke=\"#ffffff\""),
            "top-edge line must stroke the resolved line_mark.stroke.color, \
             not fall through to the series fill:\n{svg}"
        );
        assert!(
            !svg.contains("stroke=\"#3164a3\""),
            "top-edge line must not paint the series' own fill once an \
             explicit stroke color is resolved:\n{svg}"
        );
    }

    /// A resolved `line_mark.stroke.color` wins EVEN on a multi-series
    /// (color-encoded) chart, on both line and area — production paints it
    /// with no `is_multi`/`has_color_encoding` gate at all: `_line_fg_
    /// sublayers` (`emitters/_layers.py:283-303`) sets `pin_child_colors=
    /// has_color_enc` (`emitters/line.py:611`) so the fg layer's own
    /// encoding pins EVERY series to the literal `stroke.color`, not just
    /// the ungated single-series case; area's own `_area_fg_layers`
    /// (`_layers.py:766-791`) does the equivalent with `pin_child_colors=
    /// color_ch is not None` (`emitters/area.py:599`), setting `encoding.
    /// stroke = {value: <color>}` on the child layer, which overrides the
    /// parent's own `encoding.color`. Every corpus line/area with BOTH a
    /// resolved stroke color and a color encoding is stacked (the stacked
    /// arm already ports this correctly), so this gate previously read as
    /// harmless — it was reachable only via a hand-built probe board, not
    /// the corpus.
    #[test]
    fn multi_series_line_and_area_top_edge_strokes_the_resolved_color_on_every_series() {
        use serde_json::json;
        for draw_areas in [false, true] {
            let mut p = make_multi_series_line_payload_with_bottom_legend();
            p["resolved"]["style"]["line_mark"]["stroke"]["color"] = json!("#ff0000");
            if draw_areas {
                p["resolved"]["style"]["area_mark"] = json!({"opacity": 1.0, "backdrop": true});
            }
            let svg = render_inner(
                &serde_json::from_value::<Payload>(p).unwrap(),
                &test_fonts(),
                draw_areas,
            )
            .expect("renders");
            let stroke_count = svg.matches("stroke=\"#ff0000\"").count();
            assert_eq!(
                stroke_count, 2,
                "draw_areas={draw_areas}: both series' top-edge lines must \
                 stroke the resolved color, not just the single-series \
                 case:\n{svg}"
            );
            assert!(
                !svg.contains("fill=\"none\" stroke=\"#3164a3\"")
                    && !svg.contains("fill=\"none\" stroke=\"#779bc9\""),
                "draw_areas={draw_areas}: no series may fall back to its own \
                 fill once an explicit stroke color is resolved:\n{svg}"
            );
        }
    }

    // ── Wide measures ────────────────────────────────────────────────────

    /// A two-measure (`y: [revenue, units_sold]`) wide line — the resolver
    /// bakes `y` as `WIDE_VALUE_FIELD` and `wide_measures` as the authored
    /// list; the raw `revenue`/`units_sold` columns still live on each row,
    /// same as production's own resolved payload (`revenue_and_units`'s
    /// dump: `y: "__dbt_charts_wide_value__"`, `wide_measures: ["revenue",
    /// "units_sold"]`, `color: null`).
    fn make_wide_line_payload() -> serde_json::Value {
        use serde_json::json;
        let rows = json!([
            {"month": "2026-01-01", "revenue": 100.0, "units_sold": 40.0},
            {"month": "2026-01-08", "revenue": 150.0, "units_sold": 60.0},
        ]);
        let resolved = json!({
            "id": "c", "title": null, "title_style": null, "background": "#ffffff", "canvas": "#ffffff",
            "x": "month", "y": "__dbt_charts_wide_value__",
            "color": null, "stack": "none",
            "palette": ["#3164a3", "#779bc9"],
            "multiples": null, "field_labels": {}, "wide_measures": ["revenue", "units_sold"], "layers": [],
            "legend": {
                "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
                "title": {"font": {"case": "title", "family": TEST_FONT}},
                "label": {"padding": 8.0, "font": {"family": TEST_FONT, "size": 11.0}}
            },
            "style": {
                "axis_x": axis_json(false, None),
                "axis_y": value_axis_json(false, None),
                "line_mark": {"stroke": {"width": 4.0, "cap": "round", "join": "round"}, "halo_multiplier": 1.5},
                "series_label": {"font_family": TEST_FONT, "font_size": 11.0, "font_weight": "400", "dark_companion_palette": [], "pin_companions": [], "gap_px": 8.0},
                "endpoint_labels": {"visible": true, "label_offset": 6.0},
                "single_series_fill": "#3164a3",
                "label_usable_ratio": 0.8
            }
        });
        serde_json::json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "board_style": crate::with_required_board_fields(serde_json::json!({})),
            "padding": {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0}
        })
    }

    /// The rail draws each measure's HUMANIZED label (`slug_to_text`,
    /// production's `default_axis_title`/`resolve_wide_measure_labels`) —
    /// the raw column name still keys `unfold_wide_rows`' `WIDE_LABEL_FIELD`
    /// and every palette/lookup, but the PAINTED text is display text, same
    /// as `revenue_and_units.ref.svg`'s own rail (`revenue` stays as-is
    /// since it tokenizes to itself; `units_sold` paints "units sold", never
    /// the raw underscore-joined column name).
    #[test]
    fn wide_line_rail_labels_are_humanized_not_the_raw_measure_names() {
        let payload: Payload = serde_json::from_value(make_wide_line_payload()).unwrap();
        let svg = render(&payload, &test_fonts()).expect("wide line renders");
        assert!(svg.contains(">150 Revenue<"), "single-token measure reads unchanged: {svg}");
        assert!(svg.contains(">60 Units sold<"), "humanized, space-joined: {svg}");
        assert!(!svg.contains("units_sold<"), "the raw underscore-joined name never paints: {svg}");
    }

    #[test]
    fn wide_line_rail_paints_an_authored_field_label_verbatim() {
        let mut v = make_wide_line_payload();
        v["resolved"]["field_labels"] = serde_json::json!({"units_sold": "Units SHIPPED"});
        let payload: Payload = serde_json::from_value(v).unwrap();
        let svg = render(&payload, &test_fonts()).expect("wide line renders");
        assert!(svg.contains(">60 Units SHIPPED<"), "{svg}");
        assert!(!svg.contains("units sold<"), "{svg}");
    }

    /// `display_order` (`sorted_series_by_last_value` over the unfolded
    /// rows) ranks by each series' last value descending: revenue's last
    /// point (150) beats units_sold's (60), so revenue's rail label sits
    /// above (smaller y).
    #[test]
    fn wide_line_rail_orders_by_last_value_descending() {
        let payload: Payload = serde_json::from_value(make_wide_line_payload()).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let y_of = |label: &str| -> f64 {
            let marker = format!(">{label}<");
            let end = svg.find(&marker).unwrap_or_else(|| panic!("{label} present: {svg}"));
            let tag_start = svg[..end].rfind("<text").unwrap();
            svg[tag_start..end]
                .split(" y=\"")
                .nth(1)
                .unwrap()
                .split('"')
                .next()
                .unwrap()
                .parse()
                .unwrap()
        };
        assert!(y_of("150 Revenue") < y_of("60 Units sold"), "{svg}");
    }

    /// The cascade positions each rail label near its own data height, so
    /// the test above passes even with a no-op (authored-order) list feeding
    /// it — real y-values already separate the two labels. Ties are the one
    /// place list ORDER is load-bearing: `sorted_series_by_last_value`'s
    /// stable sort breaks a tie alphabetically over `wide_series_names`'
    /// SORTED base — `(-values[s], s)` — never over authored/first-
    /// appearance order. `wide_measures` here is authored REVERSED
    /// (`["zeta", "alpha"]`) precisely so a first-appearance-order base
    /// would rank `zeta` first and this goes red without the fix.
    #[test]
    fn wide_line_rail_ties_break_alphabetically_over_the_sorted_domain() {
        use serde_json::json;
        let mut p = make_wide_line_payload();
        p["resolved"]["wide_measures"] = json!(["zeta", "alpha"]);
        p["rows"] = json!([
            {"month": "2026-01-01", "zeta": 40.0, "alpha": 40.0},
            {"month": "2026-01-08", "zeta": 100.0, "alpha": 100.0},
        ]);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let y_of = |label: &str| -> f64 {
            let marker = format!(">{label}<");
            let end = svg.find(&marker).unwrap_or_else(|| panic!("{label} present: {svg}"));
            let tag_start = svg[..end].rfind("<text").unwrap();
            svg[tag_start..end]
                .split(" y=\"")
                .nth(1)
                .unwrap()
                .split('"')
                .next()
                .unwrap()
                .parse()
                .unwrap()
        };
        assert!(
            y_of("100 Alpha") < y_of("100 Zeta"),
            "tied last values break alphabetically (alpha before zeta), not authored order: {svg}"
        );
    }

    /// `wide_series_names` — the sorted, authored-measure-COMPLETE
    /// color domain — must feed the palette index, not a scan of the
    /// folded rows. `mid` is null on every row (absent from every row
    /// entirely, same fold outcome as an explicit null cell), so a scan of
    /// the folded rows sees only `["alpha", "zeta"]` and hands `zeta`
    /// `palette[1]`; the sorted, complete domain is `["alpha", "mid",
    /// "zeta"]` and `zeta` must keep `palette[2]`.
    #[test]
    fn wide_line_null_measure_still_owns_its_sorted_palette_slot() {
        use serde_json::json;
        let mut p = make_wide_line_payload();
        p["resolved"]["wide_measures"] = json!(["alpha", "mid", "zeta"]);
        p["resolved"]["palette"] = json!(["#aa0000", "#00bb00", "#0000cc"]);
        p["rows"] = json!([
            {"month": "2026-01-01", "alpha": 10.0, "zeta": 5.0},
            {"month": "2026-01-08", "alpha": 20.0, "zeta": 15.0},
        ]);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(
            svg.contains("fill=\"#0000cc\">15 Zeta<"),
            "zeta keeps its sorted slot 2 despite mid's total absence: {svg}"
        );
    }

    /// a RAGGED cross product (a dimension
    /// value missing a measure entirely — ordinary in real data) must not
    /// shift a later series' palette slot. `B` has no `m1` row at all,
    /// so the complete sorted domain is `["A - m1", "A - m2", "B - m1",
    /// "B - m2"]` (`B - m2` at slot 3); a scan of the folded rows never
    /// sees `B - m1` at all and shifts `B - m2` to slot 2.
    #[test]
    fn wide_line_ragged_dimension_cross_product_keeps_every_slot() {
        use serde_json::json;
        let mut p = make_wide_line_payload();
        p["resolved"]["color"] = json!("cat");
        p["resolved"]["wide_measures"] = json!(["m1", "m2"]);
        p["resolved"]["palette"] = json!(["#111111", "#222222", "#333333", "#444444"]);
        p["rows"] = json!([
            {"month": "2026-01-01", "cat": "A", "m1": 10.0, "m2": 20.0},
            {"month": "2026-01-08", "cat": "A", "m1": 15.0, "m2": 25.0},
            {"month": "2026-01-01", "cat": "B", "m2": 5.0},
            {"month": "2026-01-08", "cat": "B", "m2": 8.0},
        ]);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(
            svg.contains("fill=\"#444444\">8 B - M2<"),
            "B - M2 keeps its sorted slot 3 even though B - m1 has no rows at all: {svg}"
        );
    }

    /// production reorders a wide line/area's fold emission to
    /// `display_order` (last value descending), which DOUBLES as mark PAINT
    /// order on an unstacked area — `emitters/area.py:342`'s `fold_order`
    /// comment. `zzz` ends higher (200) than `aaa` (20), so production
    /// paints `zzz` first (behind) and `aaa` last (on top, visible); authored
    /// order (`["aaa", "zzz"]`) would paint `aaa` first and `zzz` on top,
    /// completely occluding `aaa`'s band (`aaa` spans y 292..325, `zzz`
    /// spans 0..325 — a full-height area). Checked via DOM paint order of
    /// the opaque `fill-opacity="0.8"` area paths, not via geometry alone.
    #[test]
    fn wide_area_paints_in_display_order_not_authored_order() {
        use serde_json::json;
        let mut p = make_wide_line_payload();
        p["resolved"]["wide_measures"] = json!(["aaa", "zzz"]);
        p["resolved"]["palette"] = json!(["#aa0000", "#0000cc"]);
        p["resolved"]["style"]["area_mark"] = json!({"opacity": 0.8, "backdrop": false});
        p["rows"] = json!([
            {"month": "2026-01-01", "aaa": 20.0, "zzz": 10.0},
            {"month": "2026-01-08", "aaa": 20.0, "zzz": 200.0},
        ]);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render_inner(&payload, &test_fonts(), true).expect("renders");
        let fills: Vec<&str> = svg
            .match_indices("fill-opacity=\"0.8\"")
            .map(|(i, _)| {
                let head = &svg[..i];
                let fill_idx = head.rfind("fill=\"").expect("area fill present");
                head[fill_idx + 6..].split('"').next().unwrap()
            })
            .collect();
        assert_eq!(
            fills,
            vec!["#0000cc", "#aa0000"],
            "zzz paints first (behind), aaa last (on top) — display order, not authored order: {svg}"
        );
    }

    /// A `color:` dimension crossed with the measures composes
    /// `<value> - <measure>` per `unfold_wide_rows`/`_label_expression` —
    /// the dimension VALUE passes through verbatim (a query value, not a
    /// column name), only the measure half is humanized
    /// (`humanize_wide_series_name`'s own split).
    #[test]
    fn wide_line_with_dimension_composes_value_and_measure_labels() {
        use serde_json::json;
        let mut p = make_wide_line_payload();
        p["resolved"]["color"] = json!("category");
        p["rows"] = json!([
            {"month": "2026-01-01", "category": "Tools", "revenue": 100.0, "units_sold": 40.0},
            {"month": "2026-01-08", "category": "Tools", "revenue": 150.0, "units_sold": 60.0},
        ]);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(svg.contains(">150 Tools - Revenue<"), "{svg}");
        assert!(svg.contains(">60 Tools - Units sold<"), "measure half humanized: {svg}");
    }

    /// Band-step (`curve: step` on a nominal x) refuses on any multi-series
    /// chart already; a wide chart is multi-series by construction
    /// (`resolve_wide_measure_channels` always injects a series-mode color
    /// channel), so the existing gate covers it without a new one.
    #[test]
    fn wide_line_band_step_refuses() {
        use serde_json::json;
        let mut p = make_wide_line_payload();
        p["resolved"]["style"]["line_mark"]["curve"] = json!("step");
        // A real nominal x's resolved axis carries is_quantitative: false —
        // the shared axis_json() default of true would wrongly gate
        // band-step off (see make_band_step_line_payload's matching note).
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = json!(false);
        p["rows"] = json!([
            {"month": "Q1", "revenue": 100.0, "units_sold": 40.0},
            {"month": "Q2", "revenue": 150.0, "units_sold": 60.0},
        ]);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let err = render(&payload, &test_fonts()).unwrap_err();
        assert!(err.to_string().contains("ERR-BAND-STEP-MULTI-METRIC"), "{err}");
    }

    /// A wide (`y: [subscriptions, services, hardware]`) stacked area: three
    /// measures over three months, `stack: zero`, the stacked recipe's
    /// white perimeter stroke, no rail and no legend so the marks are the
    /// only painted series. `services` carries the largest global sum,
    /// `subscriptions` the next, `hardware` the least.
    fn make_wide_stacked_area_payload(stack_order: Option<&str>) -> serde_json::Value {
        use serde_json::json;
        let mut p = make_wide_line_payload();
        p["resolved"]["wide_measures"] = json!(["subscriptions", "services", "hardware"]);
        p["resolved"]["stack"] = json!("zero");
        p["resolved"]["palette"] = json!(["#111111", "#222222", "#333333"]);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(false);
        p["resolved"]["style"]["stack_order"] = json!(stack_order);
        p["resolved"]["style"]["area_mark"] = json!({"opacity": 1.0, "backdrop": false});
        p["resolved"]["style"]["line_mark"] = json!({
            "stroke": {"width": 1.0, "color": "#ffffff", "cap": "round", "join": "round"},
            "halo_multiplier": 0.0
        });
        p["rows"] = json!([
            {"month": "2026-01-01", "subscriptions": 40000.0, "services": 90000.0, "hardware": 15000.0},
            {"month": "2026-02-01", "subscriptions": 46000.0, "services": 92000.0, "hardware": 14000.0},
            {"month": "2026-03-01", "subscriptions": 53000.0, "services": 95000.0, "hardware": 16000.0},
        ]);
        p
    }

    /// Fill color of each stacked area path, in DOM (paint) order.
    fn stacked_area_fills(svg: &str) -> Vec<String> {
        svg.match_indices("<path d=\"M")
            .map(|(i, _)| svg[i..].split("/>").next().unwrap())
            .filter(|t| t.contains("fill-opacity=") && t.contains("stroke=\"#ffffff\""))
            .map(|t| t.split(" fill=\"").nth(1).unwrap().split('"').next().unwrap().to_string())
            .collect()
    }

    fn render_wide_stacked(p: serde_json::Value) -> Result<String, String> {
        let payload: Payload = serde_json::from_value(p).unwrap();
        render_inner(&payload, &test_fonts(), true).map_err(|e| e.to_string())
    }

    /// `_emit_multi_metric_area`'s stacked arm: the baseline order is
    /// `sorted_series_by_stack_order` over the unfolded rows (default:
    /// descending global sum), so `services` sits on zero, `subscriptions`
    /// on it, `hardware` on top — while the marks PAINT in fold order
    /// (`fold_order=measures`, the authored list) and the palette follows
    /// the baseline (`palette_order = baseline_order`).
    #[test]
    fn wide_area_stacked_value_order_stacks_largest_measure_at_the_baseline() {
        let _locale = crate::time_locale::en_us_scope();
        let svg = render_wide_stacked(make_wide_stacked_area_payload(None)).expect("renders");
        let edges = stacked_area_edges(&svg);
        assert_eq!(edges.len(), 3, "one stacked path per measure: {svg}");
        let (subs_top, subs_bottom) = &edges[0];
        let (services_top, services_bottom) = &edges[1];
        let (_hw_top, hw_bottom) = &edges[2];
        assert!((services_bottom[0].1 - services_bottom[2].1).abs() < 1e-6, "services rests on zero: {svg}");
        for i in 0..3 {
            assert!((subs_bottom[i].1 - services_top[i].1).abs() < 1e-6, "subscriptions on services at {i}: {svg}");
            assert!((hw_bottom[i].1 - subs_top[i].1).abs() < 1e-6, "hardware on subscriptions at {i}: {svg}");
        }
        assert_eq!(
            stacked_area_fills(&svg),
            ["#222222", "#111111", "#333333"],
            "paint order is the authored list; palette slots follow baseline order: {svg}"
        );
    }

    /// `style.stack_order: data` ranks by first encounter over the unfolded
    /// rows (`sorted_series_by_stack_order`), so the listed first measure
    /// sits on zero even though `services` is larger.
    #[test]
    fn wide_area_stacked_data_order_stacks_in_listed_order() {
        let _locale = crate::time_locale::en_us_scope();
        let svg = render_wide_stacked(make_wide_stacked_area_payload(Some("data"))).expect("renders");
        let edges = stacked_area_edges(&svg);
        let (subs_top, subs_bottom) = &edges[0];
        let (services_top, services_bottom) = &edges[1];
        let (_hw_top, hw_bottom) = &edges[2];
        assert!((subs_bottom[0].1 - subs_bottom[2].1).abs() < 1e-6, "subscriptions rests on zero: {svg}");
        for i in 0..3 {
            assert!((services_bottom[i].1 - subs_top[i].1).abs() < 1e-6, "services on subscriptions at {i}: {svg}");
            assert!((hw_bottom[i].1 - services_top[i].1).abs() < 1e-6, "hardware on services at {i}: {svg}");
        }
        assert_eq!(stacked_area_fills(&svg), ["#111111", "#222222", "#333333"], "{svg}");
    }

    /// A measure null on the first rows is encountered later under
    /// `stack_order: data` ("a measure null in the first rows appears later",
    /// `docs/charts/area-charts.md`): `unfold_wide_rows` drops its null
    /// cells, so its first encounter is its first non-null row.
    #[test]
    fn wide_area_stacked_data_order_ranks_a_late_starting_measure_last() {
        let _locale = crate::time_locale::en_us_scope();
        use serde_json::json;
        let mut p = make_wide_stacked_area_payload(Some("data"));
        p["rows"][0]["subscriptions"] = json!(null);
        let svg = render_wide_stacked(p).expect("renders");
        let edges = stacked_area_edges(&svg);
        // Paint order is first appearance in the folded rows (services,
        // hardware from row 0, then subscriptions from row 1), which is also
        // the baseline order here: each band rests on the one painted before.
        let (services_top, services_bottom) = &edges[0];
        let (hw_top, hw_bottom) = &edges[1];
        let (_subs_top, subs_bottom) = &edges[2];
        assert!((services_bottom[0].1 - services_bottom[2].1).abs() < 1e-6, "services rests on zero: {svg}");
        for i in 0..3 {
            assert!((hw_bottom[i].1 - services_top[i].1).abs() < 1e-6, "hardware on services at {i}: {svg}");
            assert!((subs_bottom[i].1 - hw_top[i].1).abs() < 1e-6, "subscriptions on hardware at {i}: {svg}");
        }
        assert_eq!(stacked_area_fills(&svg), ["#111111", "#222222", "#333333"], "{svg}");
    }

    #[test]
    fn wide_area_stacked_alphabetical_order_stacks_by_name() {
        let _locale = crate::time_locale::en_us_scope();
        let svg = render_wide_stacked(make_wide_stacked_area_payload(Some("alphabetical"))).expect("renders");
        let edges = stacked_area_edges(&svg);
        let (_subs_top, subs_bottom) = &edges[0];
        let (services_top, services_bottom) = &edges[1];
        let (hw_top, hw_bottom) = &edges[2];
        assert!((hw_bottom[0].1 - hw_bottom[2].1).abs() < 1e-6, "hardware rests on zero: {svg}");
        for i in 0..3 {
            assert!((services_bottom[i].1 - hw_top[i].1).abs() < 1e-6, "services on hardware at {i}: {svg}");
            assert!((subs_bottom[i].1 - services_top[i].1).abs() < 1e-6, "subscriptions on services at {i}: {svg}");
        }
    }

    /// A stacked chart's legend lists top of stack first (`_emit_multi_
    /// metric_area`: `display_order = reversed(baseline_order)`), and a wide
    /// chart's legend carries no title (`fold_wide_measures`: `"title":
    /// None`) — never the synthetic fold field's name.
    #[test]
    fn wide_area_stacked_legend_lists_top_of_stack_first_without_a_title() {
        let _locale = crate::time_locale::en_us_scope();
        use serde_json::json;
        let mut p = make_wide_stacked_area_payload(None);
        p["resolved"]["legend"]["visible"] = json!(true);
        p["resolved"]["legend"]["position"] = json!({"edge": "right", "align": "start", "overlay": false});
        let svg = render_wide_stacked(p).expect("renders");
        let at = |label: &str| svg.find(&format!(">{label}<")).unwrap_or_else(|| panic!("{label}: {svg}"));
        assert!(at("Hardware") < at("Subscriptions") && at("Subscriptions") < at("Services"), "{svg}");
        assert!(!svg.to_lowercase().contains("wide label"), "no synthetic legend title: {svg}");
    }

    /// A measure with no non-null cell has no folded row, so production
    /// draws no mark for it while it still owns a palette slot and a
    /// legend entry.
    #[test]
    fn wide_area_stacked_with_an_all_null_measure_paints_no_mark_but_keeps_its_slot() {
        let _locale = crate::time_locale::en_us_scope();
        use serde_json::json;
        let mut p = make_wide_stacked_area_payload(None);
        for row in p["rows"].as_array_mut().unwrap() {
            row["hardware"] = json!(null);
        }
        p["resolved"]["legend"]["visible"] = json!(true);
        p["resolved"]["legend"]["position"] = json!({"edge": "right", "align": "start", "overlay": false});
        let svg = render_wide_stacked(p).expect("renders");
        assert_eq!(stacked_area_fills(&svg), ["#222222", "#111111"], "{svg}");
        assert!(
            svg.contains("fill=\"#333333\" stroke=\"#333333\" stroke-width=\"1.5\"/></g><g class=\"mark-text role-legend-label\""),
            "{svg}"
        );
    }

    /// `sorted_series_by_stack_order` admits `value`, `data`, `alphabetical`;
    /// any other authored string refuses by name.
    #[test]
    fn stacked_area_unknown_stack_order_refuses() {
        let err = render_wide_stacked(make_wide_stacked_area_payload(Some("reverse"))).unwrap_err();
        assert!(err.contains("stack_order") && err.contains("reverse"), "{err}");
    }

    /// `style.stack_order` governs an authored-`color:` stacked area just as
    /// it does a wide one (`_area_spatial_order` passes
    /// `chart.style.stack_order`): "A" (global sum 250) would sit on zero by
    /// default; `data` puts the first-encountered "B" (sum 100) there instead.
    #[test]
    fn stacked_area_data_order_honors_style_stack_order() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["resolved"]["style"]["stack_order"] = json!("data");
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "B", "signups": 40.0},
            {"week": "2026-01-08", "series": "B", "signups": 60.0},
            {"week": "2026-01-01", "series": "A", "signups": 100.0},
            {"week": "2026-01-08", "series": "A", "signups": 150.0},
        ]);
        let svg = render_wide_stacked(p).expect("renders");
        let edges = stacked_area_edges(&svg);
        let (b_top, b_bottom) = &edges[0];
        let (_a_top, a_bottom) = &edges[1];
        assert!((b_bottom[0].1 - b_bottom[1].1).abs() < 1e-6, "B rests on zero: {svg}");
        for i in 0..2 {
            assert!((a_bottom[i].1 - b_top[i].1).abs() < 1e-6, "A on B at {i}: {svg}");
        }
    }

    /// A wide line's strip reads the rows as queried and paints each cell
    /// once per measure, as production's column-keeping fold does.
    #[test]
    fn wide_line_with_support_table_paints_each_cell_once_per_measure() {
        use serde_json::json;
        let mut p = make_wide_line_payload();
        p["resolved"]["support_table"] = json!({
            "entries": [{"aggregate": "sum", "source": "revenue", "format": ",d", "label": "Total"}]
        });
        p["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("wide line with a strip renders");
        assert_eq!(svg.matches(">Total<").count(), 1, "{svg}");
    }

    /// production's dash encoding lives only in
    /// `_apply_line_color_encoding` (`emitters/line.py:130-240`), reached
    /// via `_build_line_top_encoding` — the folded arm (`_emit_folded_line`)
    /// returns before ever calling it (`emitters/line.py:462`), so
    /// production renders a wide line SOLID regardless of `style.dashes`.
    /// The `dashed` gate excludes a wide chart, so it paints solid.
    #[test]
    fn wide_line_stays_solid_under_dashes() {
        use serde_json::json;
        let mut p = make_wide_line_payload();
        p["resolved"]["style"]["dashes"] = json!([[6.0, 4.0], [2.0, 2.0]]);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).unwrap();
        assert!(!svg.contains("stroke-dasharray"), "{svg}");
    }

    // ── Stacked area ─────────────────────────────────────────────────────

    /// A two-series stacked-area payload off the same rows
    /// `make_multi_series_line_payload_with_bottom_legend` uses — A: 100,
    /// 150; B: 40, 60. A's global sum (250) beats B's (100), so
    /// production's `sorted_series_by_stack_order` (area never authors
    /// `stack_order`, so this is always its default) puts A at the
    /// baseline. Grid + zero enabled so the zero-rule tests below can
    /// observe it firing/not firing.
    fn make_stacked_area_payload(stack: &str) -> serde_json::Value {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["stack"] = json!(stack);
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["area_mark"] = json!({"opacity": 1.0, "backdrop": false});
        p["resolved"]["style"]["line_mark"] = json!({
            "stroke": {"width": 1.0, "color": "#ffffff", "cap": "round", "join": "round"},
            "halo_multiplier": 0.0
        });
        p["resolved"]["style"]["axis_y"]["grid"] =
            json!({"visible": true, "threshold": {"color": "#8A8C8F", "width": 2.0}, "width": 1.5, "color": "#D0D3D9", "opacity": 1.0});
        p
    }

    /// Every stacked area's combined fill+stroke `<path>`, in DOM (paint)
    /// order, as `(top_edge, bottom_edge)` point lists — both left-to-right
    /// in x — parsed from the `d` string `stacked_area_path` emits (top
    /// edge forward, then the bottom edge back right-to-left, per its own
    /// doc comment).
    fn stacked_area_edges(svg: &str) -> Vec<(Vec<(f64, f64)>, Vec<(f64, f64)>)> {
        svg.match_indices("<path d=\"M")
            .map(|(i, _)| svg[i..].split("/>").next().unwrap())
            .filter(|t| t.contains("fill-opacity=") && t.contains("stroke=\"#ffffff\""))
            .map(|t| {
                let d = t.split("d=\"").nth(1).unwrap().split('"').next().unwrap();
                let pts: Vec<(f64, f64)> = d
                    .trim_end_matches('Z')
                    .split(['M', 'L'])
                    .filter(|s| !s.is_empty())
                    .map(|pair| {
                        let mut it = pair.split(',');
                        (it.next().unwrap().parse().unwrap(), it.next().unwrap().parse().unwrap())
                    })
                    .collect();
                let half = pts.len() / 2;
                let bottom: Vec<(f64, f64)> = pts[half..].iter().rev().copied().collect();
                (pts[..half].to_vec(), bottom)
            })
            .collect()
    }

    /// A stacked area's PALETTE index is baseline order too, not the
    /// generic alphabetical `palette_domain` an unstacked chart uses
    /// (`_apply_area_color_encoding`'s `palette_order = reversed(order)` —
    /// `order` is top-of-stack-first, so its reverse is baseline order
    /// exactly). "X" (global sum 250) beats "A" (100) despite sorting
    /// AFTER it alphabetically — production paints "X" in `palette[0]`,
    /// never the alphabetical assignment "A" would otherwise take.
    #[test]
    fn stacked_area_palette_follows_baseline_order_not_alphabetical() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "A", "signups": 40.0},
            {"week": "2026-01-08", "series": "A", "signups": 60.0},
            {"week": "2026-01-01", "series": "X", "signups": 100.0},
            {"week": "2026-01-08", "series": "X", "signups": 150.0},
        ]);
        p["resolved"]["palette"] = json!(["#111111", "#222222"]);
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert!(svg.contains("fill=\"#111111\""), "X (baseline) takes palette[0]: {svg}");
        assert!(svg.contains("fill=\"#222222\""), "A takes palette[1]: {svg}");
        // A's own fill (not X's) is the SECOND color in DOM order, since
        // paint order is still first-appearance (A before X) — unaffected
        // by the palette reassignment above.
        let fills: Vec<&str> = svg
            .match_indices("fill=\"")
            .map(|(i, _)| svg[i + 6..].split('"').next().unwrap())
            .filter(|f| *f == "#111111" || *f == "#222222")
            .collect();
        assert_eq!(fills, vec!["#222222", "#111111"], "A paints first, X second: {svg}");
    }

    /// `sorted_series_by_stack_order`'s baseline order (largest sum first)
    /// is a THIRD list beside `spatial_domain` (paint order, unaffected by
    /// stacking) and the STACK POSITION itself — the running sum
    /// accumulates from A (baseline) up through B, never the reverse.
    #[test]
    fn stacked_area_zero_orders_baseline_by_descending_sum() {
        let p = make_stacked_area_payload("zero");
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let edges = stacked_area_edges(&svg);
        assert_eq!(edges.len(), 2, "one combined fill+stroke path per series: {svg}");
        let (a_top, a_bottom) = &edges[0];
        let (_b_top, b_bottom) = &edges[1];
        assert!((a_bottom[0].1 - a_bottom[1].1).abs() < 1e-6, "A's baseline is flat across x: {svg}");
        for i in 0..2 {
            assert!(
                (b_bottom[i].1 - a_top[i].1).abs() < 1e-6,
                "B stacks directly on A — B's bottom is A's own top at point {i}: {svg}"
            );
        }
        // The perimeter stroke lives on the combined fill path — no
        // separate stroke-only top-edge line, unlike the overlap recipe.
        assert!(!svg.contains("fill=\"none\""), "no separate stroke-only line for a stacked area: {svg}");
    }

    /// `stack: center` floats each column up by `(max_total - this_total) /
    /// 2` (Vega-Lite's own centered-offset formula, not a naive `-total/2`
    /// silhouette) — the max-total column sits flush at the true zero
    /// baseline, every shorter column floats above it. Center also
    /// suppresses the zero rule outright (`base_stack_center`): y=0 is the
    /// silhouette's centerline, not a meaningful baseline.
    #[test]
    fn stacked_area_center_floats_each_column_by_half_its_shortfall_from_the_max() {
        let p = make_stacked_area_payload("center");
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let edges = stacked_area_edges(&svg);
        let (_a_top, a_bottom) = &edges[0];
        // Week 1 total 140, week 2 total 210 (the max) — week 2 floats at
        // offset 0 (the true baseline, largest pixel y); week 1 floats up
        // by (210-140)/2 = 35 data units, a SMALLER pixel y.
        assert!(a_bottom[1].1 > a_bottom[0].1, "the max-total column sits lowest, unshifted: {svg}");
        assert!(!svg.contains("#8A8C8F"), "center suppresses the zero rule: {svg}");
        // Pin the EXACT offset, not just its sign — the naive `-total/2`
        // silhouette (`-140/2 = -70`) also satisfies the ordering check
        // above but lands week 1's baseline at a materially different
        // pixel than the real `(210-140)/2 = 35`. Week 2 floats at offset
        // 0, so its own bottom pixel IS the domain-0 pixel (`plot_h`) with
        // no further arithmetic needed; the niced domain top is 220
        // (`d3_nice(0, 210, 10)`, same law `stacked_area_y_domain_nices_
        // the_stacked_total_not_the_raw_data_max` pins independently).
        let plot_h = a_bottom[1].1;
        let px_per_unit = plot_h / 220.0;
        let expected_week1 = plot_h - 35.0 * px_per_unit;
        assert!(
            (a_bottom[0].1 - expected_week1).abs() < 0.5,
            "week 1's baseline sits at offset 35, not -70 (-total/2): got={} expected={expected_week1}: {svg}",
            a_bottom[0].1
        );
    }

    /// The unbaked stacked-area y domain nices the STACKED column total
    /// (210, week 2), not the raw per-row data max (150, A's own biggest
    /// cell) — the two mutations the campaign brief names (drop the
    /// `d3_nice` call; feed `data_max` instead of the stacked total) land
    /// materially different tops: `d3_nice(0, 210, 10) == 220`,
    /// `d3_nice(0, 150, 10) == 160`, raw (undropped) `210` a third value
    /// again — pinned as literals so this test can't validate itself
    /// against the same call site it's checking.
    #[test]
    fn stacked_area_y_domain_nices_the_stacked_total_not_the_raw_data_max() {
        let p = make_stacked_area_payload("zero");
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let edges = stacked_area_edges(&svg);
        let (_a_top, a_bottom) = &edges[0];
        let (b_top, _b_bottom) = &edges[1];
        let plot_h = a_bottom[1].1; // week 2's own baseline — domain 0.
        let expected_top = plot_h * (1.0 - 210.0 / 220.0);
        assert!(
            (b_top[1].1 - expected_top).abs() < 0.5,
            "domain top is d3_nice(0, 210, 10) = 220, not raw 210 or data_max-derived 160: \
             got={} expected={expected_top}: {svg}",
            b_top[1].1
        );
    }

    #[test]
    fn stacked_area_zero_draws_the_zero_rule() {
        let p = make_stacked_area_payload("zero");
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert!(svg.contains("#8A8C8F"), "{svg}");
    }

    /// A null measure contributes nothing to the running sum (Vega's
    /// `stack` transform, probed against a minimal spec) — a SEPARATE
    /// concern from whether the null series' own point stays in its run
    /// (it does, per `stacked_area_null_row_draws_a_continuous_zero_
    /// height_wedge_not_a_gap` above). C, third and smallest in baseline
    /// order, sits directly on A's shoulder at week 2 (B's null column) —
    /// its own baseline skips B entirely rather than reserving B's usual
    /// height.
    #[test]
    fn stacked_area_null_row_contributes_zero_to_the_running_sum() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "A", "signups": 100.0},
            {"week": "2026-01-08", "series": "A", "signups": 100.0},
            {"week": "2026-01-01", "series": "B", "signups": 40.0},
            {"week": "2026-01-08", "series": "B", "signups": null},
            {"week": "2026-01-01", "series": "C", "signups": 10.0},
            {"week": "2026-01-08", "series": "C", "signups": 10.0},
        ]);
        p["resolved"]["palette"] = json!(["#111111", "#222222", "#333333"]);
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let edges = stacked_area_edges(&svg);
        assert_eq!(edges.len(), 3, "{svg}");
        let (a_top, _) = &edges[0];
        let (_, c_bottom) = &edges[2];
        assert!(
            (c_bottom[1].1 - a_top[1].1).abs() < 1e-6,
            "B's null at week 2 contributes 0 — C sits directly on A's top: {svg}"
        );
    }

    /// Production attaches `sparse_band_transforms` to every stacked area
    /// (`emitters/area.py:488-491`): the filter drops a null row, then the
    /// impute puts it back as a REAL 0 — `_layers.py:834-837`'s own words,
    /// "a null and an absent row reach the same wedge". So a stacked area's
    /// null point stays IN its own run at zero height, never breaking it
    /// into two subpaths the way the base (unstacked) runs law does.
    #[test]
    fn stacked_area_null_row_draws_a_continuous_zero_height_wedge_not_a_gap() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "A", "signups": 100.0},
            {"week": "2026-01-08", "series": "A", "signups": 100.0},
            {"week": "2026-01-01", "series": "B", "signups": 40.0},
            {"week": "2026-01-08", "series": "B", "signups": null},
        ]);
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let edges = stacked_area_edges(&svg);
        assert_eq!(edges.len(), 2, "{svg}");
        let (a_top, _) = &edges[0];
        let (b_top, b_bottom) = &edges[1];
        assert_eq!(b_top.len(), 2, "B still carries both x's as ONE continuous run: {svg}");
        assert!(
            (b_top[1].1 - b_bottom[1].1).abs() < 1e-6,
            "B's null point at week 2 is a defined, zero-height vertex: {svg}"
        );
        assert!(
            (b_bottom[1].1 - a_top[1].1).abs() < 1e-6,
            "B's own baseline at week 2 is still A's top, unaffected by B's own nullness: {svg}"
        );
    }

    #[test]
    fn stacked_area_normalize_without_a_baked_domain_still_spans_zero_to_one() {
        let p = make_stacked_area_payload("normalize");
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert!(svg.contains("100%"), "{svg}");
    }

    /// A visible rail pins a normalize stack to `[0, 1]`; without one the
    /// authored pair reaches Vega.
    #[test]
    fn stacked_area_normalize_authored_domain_yields_to_a_visible_rail_only() {
        use serde_json::json;
        let render = |rail: bool| {
            let mut p = make_stacked_area_payload("normalize");
            p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 2.0]);
            p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(rail);
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders")
        };
        let railed = render(true);
        assert!(railed.contains("100%") && !railed.contains("200%"), "{railed}");
        assert!(render(false).contains("200%"));
    }

    /// Vega's `stackNormalize` divides by the column's own sum: a column where every
    /// series is zero has no share, and its edges paint at the plot top.
    #[test]
    fn stacked_area_normalize_zero_total_column_paints_at_the_plot_top() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("normalize");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1.0]);
        p["rows"][0]["signups"] = json!(0.0);
        p["rows"][2]["signups"] = json!(0.0);
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert!(!svg.contains("NaN"), "an empty column's NaN edges paint as 0: {svg}");
    }

    /// The hit targets of a column with no total sit at the plot top, never at a NaN
    /// transform (which blanks an export). `tooltip_format` is what draws them.
    #[test]
    fn stacked_area_normalize_zero_total_column_hit_targets_are_finite() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("normalize");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1.0]);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        p["rows"][0]["signups"] = json!(0.0);
        p["rows"][2]["signups"] = json!(0.0);
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert!(svg.contains("opacity=\"0\" data-dct-role=\"point\""), "hit targets are drawn: {svg}");
        assert!(!svg.contains("NaN)") && !svg.contains("NaN,"), "a NaN transform blanks the export: {svg}");
    }

    /// A zero-total column has no share, so the tooltip/aria prints no percent
    /// row, and a value-label caption there sits at the plot top, never at NaN.
    #[test]
    fn stacked_area_normalize_zero_total_column_prints_no_nan_share_or_caption() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("normalize");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1.0]);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        set_value_labels(&mut p, ",.1f");
        p["resolved"]["style"]["line_mark"]["labels"]["font"]["family"] = json!("sans-serif");
        p["rows"][0]["signups"] = json!(0.0);
        p["rows"][2]["signups"] = json!(0.0);
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert!(!svg.contains("NaN"), "no NaN share in aria, no NaN caption position: {svg}");
    }

    /// A mirror axis rides one invisible rule per datum; on a normalized stack each
    /// starts at the stack top, not at the raw value thousands of pixels off-canvas.
    #[test]
    fn stacked_area_normalize_mirror_ghosts_start_on_the_plot() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("normalize");
        p["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let ys: Vec<f64> = svg
            .split("<line transform=\"translate(")
            .skip(1)
            .filter(|l| l.contains("opacity=\"0\""))
            .map(|l| l.split(')').next().unwrap().split(',').nth(1).unwrap().parse().unwrap())
            .collect();
        assert_eq!(ys.len(), 4, "two series x two weeks: {svg}");
        assert!(ys.iter().all(|y| (0.0..=400.0).contains(y)), "{ys:?}");
    }

    /// Each series' (y0, y1) is the SAME running sum `stack: zero` builds,
    /// divided by that x column's OWN total — never `max_col_total` (the
    /// ceiling a `zero`/`center` domain would otherwise nice against). The
    /// two weeks here carry DIFFERENT column totals (140, 210) and
    /// DIFFERENT A/B splits, so a bug dividing by the wrong total (the
    /// max, or the other column's) moves A's own top edge off the
    /// expected fraction.
    #[test]
    fn stacked_area_normalize_divides_by_each_column_s_own_total() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("normalize");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1.0]);
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let edges = stacked_area_edges(&svg);
        assert_eq!(edges.len(), 2, "one combined fill+stroke path per series: {svg}");
        let (a_top, a_bottom) = &edges[0];
        let (b_top, _b_bottom) = &edges[1];
        // A's own baseline (value 0, normalized 0/total = 0 regardless of
        // column) and B's own top (value = column total, normalized to
        // exactly 1.0) are both column-INDEPENDENT reference pixels —
        // deriving the expected fraction from them, rather than a
        // hardcoded plot height, keeps this test honest about the actual
        // negotiated layout.
        let plot_bottom = a_bottom[0].1;
        assert!((a_bottom[1].1 - plot_bottom).abs() < 0.01, "baseline is column-independent: {svg}");
        assert!((b_top[1].1 - b_top[0].1).abs() < 0.01, "domain top is column-independent: {svg}");
        let plot_top = b_top[0].1;
        for (i, frac_a) in [(0usize, 100.0 / 140.0), (1usize, 150.0 / 210.0)] {
            let expected = plot_bottom + (plot_top - plot_bottom) * frac_a;
            assert!(
                (a_top[i].1 - expected).abs() < 0.5,
                "week {i}: a_top={:?} expected={expected} plot_bottom={plot_bottom} plot_top={plot_top}: {svg}",
                a_top[i]
            );
        }
    }

    /// A stacked area's invisible hover targets sit on each series' stack top,
    /// not at the raw datum value: a raw value on a normalized `[0, 1]` axis
    /// lands thousands of pixels off-canvas, and resvg then drops the whole
    /// chart.
    #[test]
    fn stacked_area_hover_targets_sit_on_the_stack_tops() {
        use serde_json::json;
        for stack in ["zero", "normalize"] {
            let mut p = make_stacked_area_payload(stack);
            p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
            let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true)
                .expect("renders");
            let edges = stacked_area_edges(&svg);
            let hover_ys: Vec<f64> = svg
                .split("<circle transform=\"translate(")
                .skip(1)
                .filter(|c| c.contains("opacity=\"0\""))
                .map(|c| c.split(')').next().unwrap().split(',').nth(1).unwrap().parse().unwrap())
                .collect();
            assert_eq!(hover_ys.len(), 4, "{stack}: two series x two weeks: {svg}");
            let tops: Vec<f64> = edges.iter().flat_map(|(top, _)| top.iter().map(|v| v.1)).collect();
            for y in &hover_ys {
                assert!(
                    tops.iter().any(|t| (t - y).abs() < 0.01),
                    "{stack}: hover y {y} is not an area top edge {tops:?}: {svg}"
                );
            }
        }
    }

    /// `common::format_y_label` gives a baked `ruler` and then `tick_label`
    /// precedence over the format string it is handed — `axis_json`'s own
    /// default (shared by every fixture in this module) already bakes a
    /// non-percent `tick_label: {"format": ",.0~f"}`, exactly what resolve
    /// leaves standing when the author writes no `axis_y.labels.format` of their
    /// own (`p8_normalized_area`'s own `.0%` authoring hid this: the baked
    /// `tick_label` never got a chance to win there). Pinning `labels_
    /// format` to `.0%` alone is not enough; `is_normalize` must bypass
    /// `format_y_label` outright.
    #[test]
    fn stacked_area_normalize_axis_labels_pin_to_percent_over_a_baked_tick_label() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("normalize");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1.0]);
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert!(svg.contains(">0%<"), "0% tick label present: {svg}");
        assert!(svg.contains(">100%<"), "100% tick label present: {svg}");
        assert!(!svg.contains(">0<"), "no plain-digit tick label leaks through the baked tick_label: {svg}");
    }

    /// Production's `measure_axis_to_vl` computes `labelPadding` from the
    /// RESOLVED `tick_label`/`labels.format` (`vl_field_maps.py:348-417`)
    /// BEFORE `pin_normalize_axis_format` ever runs (`emitters/area.py:303`
    /// measures, `:310-311` pins after) — the reserve is sized off the
    /// axis's own resolved ladder, never the painted `%` glyphs. Probed
    /// off a real board with hidden endpoint labels and no authored
    /// `axis_y.labels.format`: resolve bakes
    /// `tick_label.format=".3~r"`, `si_format=".3~s"`, `scientific_
    /// format=".3~e"` — every `[0,1]`-domain cover candidate this axis
    /// can draw is a "nice" decimal (`0`, `0.1`, ..., `0.9`, `1`), each
    /// narrower under `.3~r` ("0.9") than the SAME value's `%` glyph
    /// under the bug's `.0%` pin ("90%") — the `%` glyph is wide enough
    /// on its own to widen every candidate it's appended to. Reverting the
    /// fix (measuring with the percent pin instead of the resolved
    /// format) over-reserves the gutter and NARROWS the plot.
    #[test]
    fn stacked_area_normalize_gutter_estimate_uses_the_resolved_format_not_percent() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("normalize");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1.0]);
        p["resolved"]["style"]["axis_y"]["position"] = json!("right");
        p["resolved"]["style"]["axis_y"]["tick_label"] =
            json!({"format": ".3~r", "si_format": ".3~s", "scientific_format": ".3~e"});
        p["resolved"]["style"]["axis_y"]["labels"]["format"] = json!(".3~s");
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true)
            .expect("renders");
        // The fit arm's own right-side plot edge — a plain gridline's `x2`
        // in local (post-translate) coordinates.
        let plot_w: f64 = svg
            .split("<line x1=\"0.000\" y1=\"")
            .nth(1)
            .and_then(|s| s.split("x2=\"").nth(1))
            .and_then(|s| s.split('"').next())
            .unwrap()
            .parse()
            .unwrap();
        // Measured (`test_fonts()`, this same axis shape): the resolved
        // `.3~r` ladder reserves a gutter that leaves plot_w=538; pinning
        // the measurement to `.0%` instead (the bug this test guards)
        // over-reserves on the `%` glyph and narrows it to plot_w=531.
        assert!(
            plot_w > 535.0,
            "the gutter must measure the resolved `.3~r` ladder, not the wider `.0%` pin: \
             plot_w={plot_w}: {svg}"
        );
    }

    /// `_CartesianTickResolution((), None)` (`compile/resolve/chart/
    /// area.py:474-477`) bakes NO tick count for a normalize-stacked
    /// axis — the first-pass ladder (`line.rs`'s own `else if
    /// is_normalize` arm) must use Vega's own default `ceil(height/40)`
    /// unconditionally, never the theme's baked `ticks.count` (`6` on
    /// this suite's own `axis_json` default). Isolated to a `Shape::
    /// Legacy` render (`axis_y.position` unset, no endpoint rail): that
    /// arm never re-fits (`rungs_at` is only ever called from the `Fit`
    /// and `RailConcat` arms), so its drawn ladder is this first pass's
    /// own output, verbatim — a mutation of the refit gate alone cannot
    /// make this test pass or fail.
    #[test]
    fn stacked_area_normalize_first_pass_ignores_the_baked_ticks_count() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("normalize");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1.0]);
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        // The baked `ticks.count: 6` would draw 7 labels (`d3_ticks(0, 1,
        // 6)`); `ceil(400 / 40) = 10` draws 11 — the panel's declared
        // height (400, this suite's default) drives the count instead.
        assert_eq!(
            svg.matches("%</text>").count(),
            11,
            "ceil(height/40), not the baked ticks.count, sizes the first-pass ladder: {svg}"
        );
    }

    /// `rungs_at`'s `is_normalize ||` clause re-fits the
    /// ladder to the ACTUAL plot height even when the theme bakes a
    /// `ticks.count` the generic gate would otherwise defer to
    /// (`ax_y.ticks.count.is_none()` alone would miss it, same as the
    /// first-pass comment above). A `Shape::RailConcat` render (an
    /// endpoint rail, `axis_y.position` unset) is the one arm whose
    /// SECOND `rungs_at` call reads a chrome-shrunk `plot_h` genuinely
    /// smaller than the FIRST call's raw `svg_h` (`line.rs`'s own `let
    /// plot_h = svg_h - chrome_h.ceil()`) — at `height: 220` with a
    /// title and a visible legend, that shrink crosses a `ceil(*/40)`
    /// step boundary the `Shape::Fit`/`Shape::Legacy` arms in this suite
    /// never cross, so only this shape can isolate the gate from the
    /// first pass it deliberately duplicates.
    #[test]
    fn stacked_area_normalize_rungs_at_refits_past_the_baked_ticks_count() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("normalize");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1.0]);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        p["resolved"]["legend"]["visible"] = json!(true);
        p["height"] = json!(220.0);
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        // Measured (`test_fonts()`, this same shape): the chrome-shrunk
        // refit draws 3 labels; disabling the `is_normalize ||` clause
        // (so the gate falls back to `ax_y.ticks.count.is_none()`, false
        // here) freezes the first pass's own 6.
        assert_eq!(
            svg.matches("%</text>").count(),
            3,
            "the refit at the chrome-shrunk plot height must win over the first pass: {svg}"
        );
    }

    /// `_apply_zero_or_top`'s own normalize-stack branch draws BOTH the 0
    /// and 1 datum rules unconditionally whenever `grid.visible` and
    /// `threshold.visible` (`baseline.py:330-347`) — never gated on an
    /// authored percent format the way the generic zero-straddle/unity
    /// checks are. `axis_json`'s own default carries no `labels.format`
    /// and no `tooltip_format` (no percent authored anywhere), yet both
    /// rules still fire.
    #[test]
    fn stacked_area_normalize_baseline_rules_fire_without_an_authored_percent_format() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("normalize");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1.0]);
        // Explicit `zero: false`, with raw row values (40..150) that never
        // straddle 0 — the generic (non-normalize) zero-rule's own
        // straddle check would suppress the rule here; `normalize`'s law
        // ignores it entirely, so this is the shape that actually
        // distinguishes the two gates (both would otherwise fire "by
        // coincidence", since the un-gated default already straddles).
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["zero"] = json!(false);
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let rules = baseline_rules(&svg);
        assert_eq!(rules.len(), 2, "both the 0 and 1 datum rules fire regardless of scale.zero: {svg}");
    }

    /// A normalize stack's reference rules are hidden mark sets under any
    /// axis format, the 100% rule included.
    #[test]
    fn stacked_area_normalize_rules_are_hidden_under_any_axis_format() {
        use serde_json::json;
        let render_with = |percent: bool| {
            let mut p = make_stacked_area_payload("normalize");
            p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1.0]);
            p["resolved"]["style"]["tooltip_format"] = json!(".0%");
            if percent {
                p["resolved"]["style"]["axis_y"]["labels"]["format"] = json!(".0%");
            }
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true)
                .expect("renders")
        };
        let hidden = "<g class=\"mark-rule role-mark layer_0_marks\" aria-hidden=\"true\"><line";
        for percent in [false, true] {
            let svg = render_with(percent);
            assert_eq!(svg.matches(hidden).count(), 2, "{svg}");
            assert!(!svg.contains("aria-label=\"signups: 100%\""), "{svg}");
            assert!(!svg.contains("aria-label=\"signups: 0%\""), "{svg}");
        }
    }

    /// The Fit arm's own candidate-height ink union (`own_lo`/`own_hi`,
    /// feeding `frac_hi`/`frac_lo`) must use `y_max` (the `[0, 1]` domain
    /// ceiling) as the stacked mark's own top for `is_normalize`, never
    /// `max_col_total` (the RAW, pre-normalize column sum) — normalize
    /// draws every column's top flush at 100% regardless of that column's
    /// raw total, so a raw total BELOW 1.0 (fractional row values, as
    /// here) is the one shape where the two disagree: `max_col_total`
    /// under-reserves margin for a mark whose drawn top is actually at
    /// the domain ceiling. Hidden labels/grid/threshold and a fat stroke
    /// isolate this term the same way the rail-midpoint test does — the
    /// only remaining ink source that can move the fitted top margin.
    #[test]
    fn stacked_area_normalize_fit_ink_uses_the_domain_ceiling_not_the_raw_column_total() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("normalize");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1.0]);
        p["resolved"]["style"]["axis_y"]["position"] = json!("right");
        p["resolved"]["style"]["axis_y"]["labels"]["visible"] = json!(false);
        p["resolved"]["style"]["axis_y"]["grid"]["threshold"] = json!(null);
        p["resolved"]["style"]["line_mark"]["stroke"]["width"] = json!(40.0);
        // A raw column total (0.15) well below the `[0, 1]` domain — the
        // shape that distinguishes the two candidates for `own_hi`.
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "A", "signups": 0.1},
            {"week": "2026-01-08", "series": "A", "signups": 0.1},
            {"week": "2026-01-01", "series": "B", "signups": 0.05},
            {"week": "2026-01-08", "series": "B", "signups": 0.05},
        ]);
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        // Measured (`test_fonts()`, this same shape): reserving to the
        // domain ceiling pushes the plot's own top margin to 56; using
        // the raw 0.15 total instead under-reserves it to 22.
        assert!(
            svg.contains("translate(56.000,56.000)"),
            "the mark's fit-ink top must reach the domain ceiling, not the raw column total: {svg}"
        );
    }

    /// `_stack_group_total`'s gate is `chart.stack in ("normalize",
    /// "zero")` (`structured_tooltip.py:237-253`) — the `Σ` footer for
    /// `normalize`; only `normalize` also gets the percent-of-total `%`
    /// row ahead of it.
    #[test]
    fn stacked_area_normalize_tooltip_carries_share_and_total() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("normalize");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1.0]);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        // Week 1 (A=100, B=40, total=140) and week 2 (A=150, B=60,
        // total=210) share the SAME 100:40 ratio, so both weeks' %
        // percentages coincide (71%/29%) while the Σ footer differs —
        // distinguishing a correct per-column division from a constant or
        // globally-wrong one.
        assert!(svg.contains("%: 71%; Signups: 100; ⁣Σ: 140"), "{svg}");
        assert!(svg.contains("%: 29%; Signups: 40; ⁣Σ: 140"), "{svg}");
        assert!(svg.contains("%: 71%; Signups: 150; ⁣Σ: 210"), "{svg}");
        assert!(svg.contains("%: 29%; Signups: 60; ⁣Σ: 210"), "{svg}");
    }

    /// The endpoint rail anchors each series at its own stacked SHARE
    /// midpoint (`_stacked_midpoints`'s `mid / col_total` branch) — A, the
    /// baseline series, anchors lowest on the page; B, stacked on top of
    /// it, anchors higher. Also the regression test for `rail plot box too
    /// small (456×-8272)`: before the `top_ink_at`/fit-box fix, a visible
    /// rail on a normalize-stacked area refused to render at all.
    #[test]
    fn stacked_area_normalize_endpoint_rail_anchors_at_the_stacked_share_midpoint() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("normalize");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1.0]);
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true)
            .expect("renders — a visible rail must not blow up the plot-box negotiation");
        let label_y = |label: &str| -> f64 {
            let marker = format!(">{label}</text>");
            let end = svg.find(&marker).unwrap_or_else(|| panic!("label {label} present: {svg}"));
            let text_start = svg[..end].rfind("<text").expect("owning <text> tag present");
            let tag = &svg[text_start..end];
            tag.split(" y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        assert!(label_y("A") > label_y("B"), "A (baseline) anchors lower than B (stacked above it): {svg}");
        // Pin the EXACT anchor, not just the ordering (see the `zero`-stack
        // sibling test's own comment for why ordering alone can't catch a
        // top-instead-of-midpoint mutation).
        let edges = stacked_area_edges(&svg);
        let (b_top, b_bottom) = &edges[1];
        let b_midpoint_px = (b_top[1].1 + b_bottom[1].1) / 2.0;
        let shift = measure::baseline_shift("middle", 11.0);
        assert!(
            (label_y("B") - (b_midpoint_px + shift)).abs() < 1.0,
            "B's rail anchor is the segment SHARE midpoint, not its top: label_y={} expected={} (mid={b_midpoint_px}, shift={shift}): {svg}",
            label_y("B"),
            b_midpoint_px + shift
        );
    }

    /// A colorless stack is one series resting on zero.
    #[test]
    fn stacked_area_with_no_color_field_renders_one_area() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["resolved"]["color"] = json!(null);
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert_eq!(svg.matches("mark-area role-mark").count(), 1, "{svg}");
    }

    /// `_area_stacked_layer`'s own docstring: "the theme's stacked recipe
    /// always supplies a stroke, so no single-series stroke fallback is
    /// needed here" — production emits NO `stroke` prop at all when the
    /// color is absent (`_layers.py`), never a background-colored one.
    /// A missing stroke color on this payload is therefore a contract
    /// violation, not a legitimate "no stroke" state to paper over with
    /// `bg`.
    #[test]
    fn stacked_area_missing_stroke_color_refuses_rather_than_defaulting_to_bg() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["resolved"]["style"]["line_mark"] = json!({
            "stroke": {"width": 1.0, "cap": "round", "join": "round"},
            "halo_multiplier": 0.0
        });
        let err = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).unwrap_err();
        assert!(err.contains("stroke"), "{err}");
    }

    /// Positives pile up from zero and negatives down from it
    /// (`stackZero`); the domain reaches below zero.
    #[test]
    fn stacked_area_negative_value_stacks_below_zero() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        let zero_rule_y = |p: serde_json::Value| -> f64 {
            let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
            let rule = svg.split("stroke=\"#8A8C8F\"").next().unwrap();
            rule.rsplit("y1=\"").next().unwrap().split('"').next().unwrap().parse().unwrap()
        };
        // The zero rule sits above the plot floor: the domain reaches below zero.
        let floor_zero_y = zero_rule_y(p.clone());
        p["rows"][0]["signups"] = json!(-5.0);
        let below_zero_y = zero_rule_y(p);
        assert!(below_zero_y < floor_zero_y, "zero rule at {below_zero_y}, not above the floor at {floor_zero_y}");
    }

    /// `zero: true` pins `domainMin` to the ladder's lowest rung, which a negative
    /// stack's floor sits above.
    #[test]
    fn stacked_area_negative_value_under_zero_true_pins_the_domain_to_the_ladder() {
        let _en = crate::time_locale::en_us_scope();
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["rows"][0]["signups"] = json!(-5.0);
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["zero"] = json!(true);
        p["resolved"]["style"]["axis_y"]["tick_values"] = json!([-20.0, 0.0, 100.0, 200.0]);
        let svg = super::render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert!(svg.contains("\u{2212}20"), "the ladder's lowest rung is the floor: {svg}");
    }

    /// A stacked rail cascades over `[0, max column total]` whatever domain is
    /// authored, and a descending one never falls back to the raw extent.
    #[test]
    fn the_stacked_rail_cascades_over_the_stacked_domain() {
        use super::rail_cascade_bounds;
        assert_eq!(rail_cascade_bounds(true, false, 210.0, Some((0.0, 1000.0)), (5.0, 90.0)), (0.0, 210.0));
        assert_eq!(rail_cascade_bounds(true, false, 210.0, Some((1000.0, 0.0)), (5.0, 90.0)), (0.0, 210.0));
        assert_eq!(rail_cascade_bounds(true, true, 210.0, Some((0.0, 1.0)), (5.0, 90.0)), (0.0, 1.0));
        assert_eq!(rail_cascade_bounds(false, false, 0.0, Some((1000.0, 0.0)), (5.0, 90.0)), (5.0, 90.0));
        assert_eq!(rail_cascade_bounds(false, false, 0.0, Some((0.0, 1000.0)), (5.0, 90.0)), (0.0, 1000.0));
        assert_eq!(rail_cascade_bounds(false, false, 0.0, None, (5.0, 90.0)), (5.0, 90.0));
    }

    /// The top margin reserves the overhang of the stacked mark nearest the
    /// domain's top end, which flips with the domain's direction.
    #[test]
    fn the_stacked_top_datum_follows_the_domain_direction() {
        use super::stacked_top_datum;
        assert_eq!(stacked_top_datum(true, false, 210.0, -20.0), 210.0);
        assert_eq!(stacked_top_datum(false, false, 210.0, -20.0), -20.0);
        assert_eq!(stacked_top_datum(true, true, 210.0, 0.0), 1.0);
        assert_eq!(stacked_top_datum(false, true, 210.0, 0.0), 0.0);
    }

    /// Production's `_wide_negative` drops the group-total footer when any
    /// value is negative.
    #[test]
    fn a_negative_stacked_area_prints_no_group_total() {
        use serde_json::json;
        let render_with = |negative: bool| {
            let mut p = make_stacked_area_payload("zero");
            p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
            if negative {
                p["rows"][0]["signups"] = json!(-5.0);
            }
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders")
        };
        assert!(render_with(false).contains('Σ'), "the footer prints for non-negative data");
        assert!(!render_with(true).contains('Σ'), "and not once a value is negative");
    }

    /// `center` and `normalize` pile `|v|`: a negative value is its magnitude, so the
    /// area draws as if the sign were positive.
    #[test]
    fn stacked_area_negative_value_beside_a_center_stack_piles_its_magnitude() {
        let _en = crate::time_locale::en_us_scope();
        use serde_json::json;
        let render = |value: f64| {
            let mut p = make_stacked_area_payload("center");
            p["rows"][0]["signups"] = json!(value);
            super::render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders")
        };
        assert_eq!(render(-5.0), render(5.0));
    }

    /// `authored_continuous_domain()` (`ax_y.scale.continuous.domain`) is
    /// reachable on a stacked area even though `domain_min`/`domain_max`
    /// never are (`resolve/chart/area.py` bakes those to `None` for this
    /// shape) — production honors an authored domain outright (Vega
    /// emits it as the scale's literal `domain`, no `nice` applied: probed
    /// against `vl_convert`). Column totals here are 140/210; an authored
    /// [0, 1000] must widen the domain the marks draw against, not get
    /// silently ignored in favor of the niced ~220 default.
    #[test]
    fn stacked_area_honors_an_authored_y_axis_domain() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1000.0]);
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let edges = stacked_area_edges(&svg);
        let (_a_top, a_bottom) = &edges[0];
        let (b_top, _b_bottom) = &edges[1];
        let plot_h = a_bottom[0].1; // A's own baseline pixel — domain 0.
        let expected_top = plot_h * (1.0 - 210.0 / 1000.0);
        assert!(
            (b_top[1].1 - expected_top).abs() < 0.5,
            "b_top={:?} expected={expected_top} plot_h={plot_h}: {svg}",
            b_top[1]
        );
    }

    /// Production emits an authored domain in AUTHORED order; Vega inverts the
    /// axis for a descending pair.
    #[test]
    fn stacked_area_descending_authored_domain_inverts_the_axis() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([1000.0, 0.0]);
        render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
    }

    /// Production's `sparse_band_transforms` impute pass fills a missing
    /// (x, series) pair with 0 — "a null and an absent row reach the same
    /// wedge" (`_layers.py`) — so a genuinely ABSENT row draws exactly like
    /// a present null: the stack sum treats it as a zero contribution and
    /// the series' own run stays unbroken, pinched to zero height at that x.
    #[test]
    fn stacked_area_missing_row_imputes_zero_like_a_null_row() {
        let mut p = make_stacked_area_payload("zero");
        let mut rows = p["rows"].as_array().unwrap().clone();
        rows.remove(2); // B's week-1 row — absent, not null.
        p["rows"] = serde_json::json!(rows);
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let edges = stacked_area_edges(&svg);
        assert_eq!(edges.len(), 2, "one combined fill+stroke path per series: {svg}");
        // B is baseline_order[1] (A's global sum 250 beats B's 60 now that
        // its week-1 row is absent, not just smaller). Its run stays
        // UNBROKEN across both weeks (2 points, not two 1-point runs) —
        // an absent row must not fragment the path the way a break would.
        let (b_top, b_bottom) = &edges[1];
        assert_eq!(b_top.len(), 2, "B's run is unbroken across the absent week: {svg}");
        assert_eq!(b_bottom.len(), 2, "{svg}");
        // At the absent week, B's own top/bottom collapse to the SAME
        // pixel — A's own top edge, since B contributes 0 to the stack —
        // a real zero-height vertex, not a gap.
        assert!(
            (b_top[0].1 - b_bottom[0].1).abs() < 0.01,
            "B's imputed vertex is zero-height at the absent week: top={:?} bottom={:?}: {svg}",
            b_top[0],
            b_bottom[0]
        );
        // The second week (both rows present) still stacks B on top of A
        // with real height, unaffected by the imputed neighbor.
        assert!(
            (b_top[1].1 - b_bottom[1].1).abs() > 1.0,
            "B still has real height at the present week: top={:?} bottom={:?}: {svg}",
            b_top[1],
            b_bottom[1]
        );
    }

    /// `top_ink_at`'s `mark_dist` term unifies on the tallest
    /// column's own STACKED total (`max_col_total`) for every stack mode,
    /// not just `normalize` — a raw per-row datum is the wrong "topmost
    /// mark" reference for ANY stacked area. A fat stroke (`line_mark.
    /// stroke.width: 20`) and hidden tick labels isolate the `mark_dist`
    /// term as the ONLY contributor to `top_ink_at`'s own ink (the tick-
    /// label-half term this closure also folds in is otherwise the
    /// binding one on ordinary fonts/strokes, masking a 1-2px difference
    /// here entirely — confirmed by probing the un-isolated shape first).
    /// `data_max` (a single row's own 20) sits far below the niced domain
    /// ceiling the mark's own top edge actually touches (`max_col_total`,
    /// 100): the old reading's `mark_dist` comes out so large that `ink`
    /// clamps to 0 (no margin at all reserved for a 20px-wide stroke's own
    /// overhang), where the fixed reading reserves the real ~20px.
    #[test]
    fn stacked_area_zero_top_ink_uses_the_stacked_total_not_a_raw_datum() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        p["resolved"]["style"]["axis_y"]["labels"]["visible"] = json!(false);
        p["resolved"]["style"]["line_mark"]["stroke"]["width"] = json!(20.0);
        p["resolved"]["palette"] =
            json!(["#111111", "#222222", "#333333", "#444444", "#555555"]);
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "A", "signups": 20.0},
            {"week": "2026-01-08", "series": "A", "signups": 20.0},
            {"week": "2026-01-01", "series": "B", "signups": 20.0},
            {"week": "2026-01-08", "series": "B", "signups": 20.0},
            {"week": "2026-01-01", "series": "C", "signups": 20.0},
            {"week": "2026-01-08", "series": "C", "signups": 20.0},
            {"week": "2026-01-01", "series": "D", "signups": 20.0},
            {"week": "2026-01-08", "series": "D", "signups": 20.0},
            {"week": "2026-01-01", "series": "E", "signups": 20.0},
            {"week": "2026-01-08", "series": "E", "signups": 20.0},
        ]);
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true)
            .expect("renders");
        let edges = stacked_area_edges(&svg);
        assert_eq!(edges.len(), 5, "{svg}");
        let translate_y: f64 = svg
            .split("translate(")
            .nth(1)
            .unwrap()
            .split(',')
            .nth(1)
            .unwrap()
            .split(')')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        assert!(
            translate_y > 25.0,
            "the plot's own top margin reserves the fat stroke's real overhang \
             at the stacked ceiling (~20px over the 8px pad+tick default), not \
             a raw datum's much smaller one: translate_y={translate_y}: {svg}"
        );
    }

    /// The endpoint rail anchors each series at its own stacked segment
    /// midpoint (bar's `_stacked_midpoints`, ported for area) — A, the
    /// baseline series, anchors lowest on the page; B, stacked on top of
    /// it, anchors higher.
    #[test]
    fn stacked_area_endpoint_rail_anchors_at_the_stacked_midpoint() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let label_y = |label: &str| -> f64 {
            let marker = format!(">{label}</text>");
            let end = svg.find(&marker).unwrap_or_else(|| panic!("label {label} present: {svg}"));
            let text_start = svg[..end].rfind("<text").expect("owning <text> tag present");
            let tag = &svg[text_start..end];
            tag.split(" y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        assert!(label_y("A") > label_y("B"), "A (baseline) anchors lower than B (stacked above it): {svg}");
        // Pin the EXACT anchor, not just the ordering — a mutation
        // anchoring at the segment TOP (`bounds[idx].1`) instead of the
        // midpoint still keeps A below B (it's still monotone), so the
        // ordering check alone can't catch it. B's own top/bottom pixel
        // edges (week 2, its own value 60) come straight out of the
        // rendered fill path, so the midpoint pixel is just their mean —
        // no domain/px-per-unit arithmetic to re-derive.
        let edges = stacked_area_edges(&svg);
        let (b_top, b_bottom) = &edges[1];
        let b_midpoint_px = (b_top[1].1 + b_bottom[1].1) / 2.0;
        // The rail-concat arm shifts a "middle"-baseline label by
        // `round(0.30 * font_size)` off its raw scale position
        // (`measure::baseline_shift`) — the SAME shift the emission site
        // applies, so this isn't a magic constant, it's the documented law.
        let shift = measure::baseline_shift("middle", 11.0);
        assert!(
            (label_y("B") - (b_midpoint_px + shift)).abs() < 1.0,
            "B's rail anchor is the segment MIDPOINT, not its top: label_y={} expected={} (mid={b_midpoint_px}, shift={shift}): {svg}",
            label_y("B"),
            b_midpoint_px + shift
        );
    }

    /// Production's `_stacked_midpoints` anchors each series at its own
    /// most-recent NON-NULL x independently (`last_nonnull_xy_per_series`'s
    /// walk-back) — a series absent at the domain's last x still anchors,
    /// at its own last real column's own midpoint, never at a column it
    /// has no value in.
    #[test]
    fn stacked_area_endpoint_rail_anchors_a_series_at_its_own_last_real_x() {
        let mut p = make_stacked_area_payload("zero");
        let mut rows = p["rows"].as_array().unwrap().clone();
        rows.remove(3); // B's week-2 row -- absent, not null.
        p["rows"] = serde_json::json!(rows);
        p["resolved"]["style"]["endpoint_labels"] = serde_json::json!({"visible": true, "label_offset": 6.0});
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let label_y = |label: &str| -> f64 {
            let marker = format!(">{label}</text>");
            let end = svg.find(&marker).unwrap_or_else(|| panic!("label {label} present: {svg}"));
            let text_start = svg[..end].rfind("<text").expect("owning <text> tag present");
            let tag = &svg[text_start..end];
            tag.split(" y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        // B's own last real column is week 1 (A=100 baseline, B=40 on top):
        // midpoint = 100 + 40/2 = 120, in DATA units -- convert through the
        // same y_scale the rendered edges use (probed off A's own week-1
        // edge, present at both x's, which the removal never touches).
        let edges = stacked_area_edges(&svg);
        let (a_top, a_bottom) = &edges[0];
        // A's week-1 top/bottom are its own [0, 100] segment: px-per-unit
        // off that span, then B's week-1 midpoint (120) converts from the
        // shared zero baseline (A's own bottom, y=0).
        let px_per_unit = (a_bottom[0].1 - a_top[0].1) / 100.0;
        let b_week1_mid_px = a_bottom[0].1 - 120.0 * px_per_unit;
        let shift = measure::baseline_shift("middle", 11.0);
        assert!(
            (label_y("B") - (b_week1_mid_px + shift)).abs() < 1.0,
            "B anchors at its own last real x (week 1), not week 2 (where it's absent): \
             label_y={} expected={} : {svg}",
            label_y("B"),
            b_week1_mid_px + shift
        );
    }

    /// `features/endpoint_labels.py` pins the main pane's y-scale domain to
    /// `[0, max_column_total]` whenever `center`'s rail is visible — and the
    /// hconcat's SHARED y scale nices that pin (probe-pinned in
    /// `test_vl_probe.py`: unit 0..161, shared 0..180). So the railed render
    /// draws on the `d3_nice(0, 210, 10) == 220` domain: the max-total
    /// column's top edge sits 10/220 of the plot
    /// below pixel 0, never AT it (an un-niced pin would put it at 0).
    #[test]
    fn stacked_area_center_with_a_visible_rail_draws_on_the_niced_shared_scale() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("center");
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let edges = stacked_area_edges(&svg);
        let (_a_top, a_bottom) = &edges[0];
        let (b_top, _b_bottom) = &edges[1];
        let plot_h = a_bottom[1].1; // week 2's own baseline — domain 0.
        let expected_top = plot_h * (1.0 - 210.0 / 220.0);
        assert!(
            (b_top[1].1 - expected_top).abs() < 0.5,
            "the shared scale nices the pin: top is 210/220 of the plot, not pixel 0: got={} expected={expected_top}: {svg}",
            b_top[1].1
        );
    }

    /// A zero-anchored measure scale pins `domainMin` (`y_zero_scale`), which
    /// suppresses Vega-Lite's default `nice` on a UNIT spec: the stacked
    /// area's top edge is the raw stacked total (210, week 2), at pixel 0 —
    /// not the niced 220 a visible rail's shared scale draws.
    #[test]
    fn stacked_area_without_a_rail_on_a_zero_anchored_scale_is_not_niced() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["zero"] = json!(true);
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let edges = stacked_area_edges(&svg);
        let (b_top, _b_bottom) = &edges[1];
        assert!(b_top[1].1.abs() < 0.5, "the raw stacked total 210 is the domain top, pixel 0: {svg}");
    }

    /// The same zero-anchored scale under a visible rail rides the hconcat's
    /// shared y scale, which re-applies the default `nice`: 210 nices to 220.
    #[test]
    fn stacked_area_with_a_rail_on_a_zero_anchored_scale_is_niced() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["zero"] = json!(true);
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        let svg =
            render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        let edges = stacked_area_edges(&svg);
        let (_a_top, a_bottom) = &edges[0];
        let (b_top, _b_bottom) = &edges[1];
        let plot_h = a_bottom[1].1;
        let expected_top = plot_h * (1.0 - 210.0 / 220.0);
        assert!((b_top[1].1 - expected_top).abs() < 0.5, "niced 220 top: got={} want={expected_top}: {svg}", b_top[1].1);
    }

    /// An authored domain with a visible rail renders like the un-railed one.
    #[test]
    fn stacked_area_authored_domain_with_a_visible_rail_renders() {
        use serde_json::json;
        let mut p = make_stacked_area_payload("zero");
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        p["resolved"]["style"]["axis_y"]["scale"]["continuous"]["domain"] = json!([0.0, 1000.0]);
        render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
    }

    /// The board slot governs the endpoint rail's companion ink too
    /// (`series_ink` inside the rail loop) — "A" locally leads (largest
    /// last value, would take companion[0]) but its board slot is 1.
    #[test]
    fn rail_ink_follows_the_board_slot_over_chart_local_order() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        p["resolved"]["style"]["series_label"]["dark_companion_palette"] = json!(["#111111", "#222222"]);
        p["resolved"]["category_colors"] =
            json!([{"field": "series", "slots": {"A": 1, "B": 0}, "overrides": {}}]);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let rail_fill = |label: &str| -> String {
            let marker = format!(" {label}</text>");
            let end = svg.find(&marker).unwrap_or_else(|| panic!("label {label} present: {svg}"));
            let text_start = svg[..end].rfind("<text").expect("owning <text> tag present");
            let tag = &svg[text_start..end];
            tag.split("fill=\"").nth(1).unwrap().split('"').next().unwrap().to_string()
        };
        assert_eq!(rail_fill("A"), "#222222", "A takes its board slot 1's companion: {svg}");
        assert_eq!(rail_fill("B"), "#111111", "B takes its board slot 0's companion: {svg}");
    }

    /// `style.dashes` is production's strokeDash encoding (`emitters/line.py`):
    /// a series-colored line binds the dash palette to the color field on
    /// a domain pinned to data-insertion order and pins the color scale to
    /// the same domain; Vega's ordinal scales cycle a short range by index
    /// (probed), so one ink colors every series and a fifth series takes
    /// the first dash again. The halo under each line shares the encoding.
    /// Series are told apart by their first vertex's y (their Jan value),
    /// so the assertion reads which series carries which dash — under the
    /// alphabetical nominal default `zeta` would draw solid and `five`
    /// with `2,6`.
    #[test]
    fn a_dash_palette_binds_to_the_series_in_data_order_on_one_cycled_ink() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "zeta", "signups": 100.0},
            {"week": "2026-01-08", "series": "zeta", "signups": 150.0},
            {"week": "2026-01-01", "series": "alpha", "signups": 80.0},
            {"week": "2026-01-08", "series": "alpha", "signups": 60.0},
            {"week": "2026-01-01", "series": "mid", "signups": 60.0},
            {"week": "2026-01-08", "series": "mid", "signups": 80.0},
            {"week": "2026-01-01", "series": "last", "signups": 40.0},
            {"week": "2026-01-08", "series": "last", "signups": 20.0},
            {"week": "2026-01-01", "series": "five", "signups": 20.0},
            {"week": "2026-01-08", "series": "five", "signups": 10.0},
        ]);
        p["resolved"]["palette"] = json!(["#1f77b4"]);
        p["resolved"]["style"]["dashes"] = json!([[12.0, 16.0], [2.0, 6.0], [4.0, 4.0], []]);
        p["resolved"]["legend"]["visible"] = json!(false);
        let svg = render(&Payload::deserialize(&p).unwrap(), &test_fonts()).expect("renders");
        // (first vertex y, dash) per stroked path, split by ink and halo.
        let strokes = |stroke: &str| -> Vec<(i64, Option<String>)> {
            let mut v: Vec<(i64, Option<String>)> = svg
                .match_indices("<path d=\"M")
                .map(|(i, _)| svg[i..].split("/>").next().unwrap())
                .filter(|t| t.contains("fill=\"none\"") && t.contains(&format!("stroke=\"{stroke}\"")))
                .map(|t| {
                    let y: f64 = t.split("d=\"M").nth(1).unwrap().split(['L', ' ']).next().unwrap().split(',').nth(1).unwrap().parse().unwrap();
                    (y.round() as i64, t.split("stroke-dasharray=\"").nth(1).map(|s| s.split('"').next().unwrap().to_string()))
                })
                .collect();
            v.sort();
            v
        };
        let inks = strokes("#1f77b4");
        assert_eq!(inks.len(), 5, "one ink for every series: {svg}");
        // Top to bottom at Jan: zeta 100, alpha 80, mid 60, last 40, five 20.
        let dashes: Vec<Option<String>> = inks.iter().map(|(_, d)| d.clone()).collect();
        let expect: Vec<Option<String>> =
            ["12,16", "2,6", "4,4", "", "12,16"].iter().map(|d| (!d.is_empty()).then(|| d.to_string())).collect();
        assert_eq!(dashes, expect, "{svg}");
        assert_eq!(strokes("#ffffff"), inks.iter().map(|(y, d)| (*y, d.clone())).collect::<Vec<_>>(), "halos share the dash: {svg}");
        assert!(!svg.contains("#ccc"), "{svg}");
        // The legend draws each series' dash on a long stroke sample.
        p["resolved"]["legend"]["visible"] = json!(true);
        let svg = render(&Payload::deserialize(&p).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains("d=\"M-22.361,0L22.361,0\"") && svg.contains("stroke-linecap=\"round\" stroke-dasharray=\"12,16\""), "{svg}");
        // An area chart never binds dashes (`emitters/area.py` has no
        // strokeDash path), whatever its style carries.
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["area_mark"] = json!({"opacity": 0.16, "backdrop": false});
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert!(!svg.contains("stroke-dasharray"), "{svg}");
    }

    /// Legacy-arm regression net for the overlap-remedies port (L1-L5): the
    /// legacy margin arm (left y axis, no rail) must draw the SAME two-row
    /// flat labels it always has — no parity thinning, no tilt, no inline
    /// fold — since none of the five laws below ever reach it (they wire
    /// into `resolve_vega_label_layout`, called only from the fit/rail
    /// arms; the legacy arm keeps calling `resolve_temporal_labels`
    /// unchanged). A previous branch's round-1 review caught exactly this
    /// kind of legacy-arm regression from a shared-helper edit, hence this
    /// byte-identical pin captured before any of L1-L5 landed.
    #[test]
    fn fit_arm_daily_two_row_labels_are_untouched_by_the_overlap_remedies() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_line_payload(false, false);
        let rows: Vec<serde_json::Value> = (1..=10)
            .map(|d| serde_json::json!({"week": format!("2026-01-{d:02}"), "signups": 100.0 + d as f64}))
            .collect();
        p["rows"] = serde_json::json!(rows);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        // No rotation, no hidden labels, no inlined single-row day+context.
        assert!(!svg.contains("rotate("), "{svg}");
        assert!(!svg.contains("opacity=\"0\""), "{svg}");
        assert!(!svg.contains("1 Jan'26"), "inline fold must not reach the legacy arm: {svg}");
        // Every one of the 10 days still draws its own row1 day-number label.
        for d in 1..=10 {
            assert!(svg.contains(&format!(">{d}</text>")), "day {d} label missing: {svg}");
        }
        // The first label still carries its year-context row2.
        assert!(svg.contains(">Jan'26</text>"), "{svg}");
    }

    /// `labels.time_unit: none` is Vega's own per-grain format on every bucket
    /// whatever the margins: the legacy arm (a left value axis beside a support strip) draws the
    /// author's format flat on each one instead of the cadence ladder's openers.
    #[test]
    fn the_fit_arm_paints_labels_time_unit_none_on_every_bucket() {
        let mut p = make_composed_strip_payload(false);
        p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        p["resolved"]["style"]["axis_y"]["ticks"]["visible"] = serde_json::json!(false);
        let rows: Vec<serde_json::Value> = (0..24)
            .map(|i| {
                serde_json::json!({"week": format!("{}-{:02}-01", 2025 + i / 12, i % 12 + 1), "signups": 100.0 + i as f64})
            })
            .collect();
        p["rows"] = serde_json::json!(rows);
        let axis = &mut p["resolved"]["style"]["axis_x"]["labels"];
        axis["time_unit"] = serde_json::json!("none");
        axis["format"] = serde_json::json!("%b %Y");
        axis["overlap"] = serde_json::json!({"skip": false, "tilt": false});
        let _locale = crate::time_locale::en_us_scope();
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let painted = svg.matches(" 2025</text>").count() + svg.matches(" 2026</text>").count();
        assert_eq!(painted, 24, "{svg}");
    }

    /// `labels.time_unit: none` keeps its crowding remedies on the legacy arm: a
    /// tilt rotates the labels and parity hides the ones that still collide.
    #[test]
    fn the_fit_arm_tilts_and_thins_crowded_labels_under_time_unit_none() {
        let mut p = make_composed_strip_payload(false);
        p["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("left");
        p["resolved"]["style"]["axis_y"]["ticks"]["visible"] = serde_json::json!(false);
        let rows: Vec<serde_json::Value> = (0..36)
            .map(|i| {
                serde_json::json!({"week": format!("{}-{:02}-01", 2024 + i / 12, i % 12 + 1), "signups": 100.0 + i as f64})
            })
            .collect();
        p["rows"] = serde_json::json!(rows);
        let axis = &mut p["resolved"]["style"]["axis_x"]["labels"];
        axis["time_unit"] = serde_json::json!("none");
        axis["format"] = serde_json::json!("%b %Y");
        axis["overlap"] = serde_json::json!({"skip": false, "tilt": true});
        axis["tilt_increments"] = serde_json::json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        axis["min_gap"] = serde_json::json!(4.0);
        let _locale = crate::time_locale::en_us_scope();
        let svg = render(&Payload::deserialize(&p).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains("rotate("), "{svg}");
        p["resolved"]["style"]["axis_x"]["labels"]["overlap"] = serde_json::json!({"skip": true, "tilt": false});
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let painted = svg.matches(" 2024</text>").count() + svg.matches(" 2025</text>").count() + svg.matches(" 2026</text>").count();
        assert!(painted < 36, "parity thins the labels: {painted}");
    }

    /// Five Mondays of January 2024 with `overlap: {skip, tilt}` authored: a
    /// weekly axis inside one month has no month opener to promote to, so
    /// its native cadence meets parity and tilt directly.
    fn make_weekly_tilt_payload(width: f64, height: f64) -> serde_json::Value {
        let mut p = make_line_payload(true, false);
        let rows: Vec<serde_json::Value> = (0..5)
            .map(|i| serde_json::json!({"week": format!("2024-01-{:02}", 1 + 7 * i), "signups": 100.0 + i as f64}))
            .collect();
        p["rows"] = serde_json::json!(rows);
        p["width"] = serde_json::json!(width);
        p["height"] = serde_json::json!(height);
        let ax = &mut p["resolved"]["style"]["axis_x"]["labels"];
        ax["overlap"] = serde_json::json!({"skip": true, "tilt": true});
        ax["tilt_increments"] = serde_json::json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        ax["min_gap"] = serde_json::json!(16.0);
        p
    }

    /// HIGH-3/HIGH-7 render-level regression: L1's tilt ladder on a narrow
    /// weekly band picks -90, and the emitted rotation
    /// anchors at Vega's own `vl_x_label_anchor` baseline for a NORMALIZED
    /// angle — `middle` (`dy = round(0.30·11) = 3`), not `top`
    /// (`dy = round(0.79·11) = 9`, the bug a raw negative angle produces).
    #[test]
    fn narrow_weekly_tilt_rotates_at_vega_s_normalized_middle_baseline() {
        let p = make_weekly_tilt_payload(100.0, 300.0);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains("rotate(-90)"), "expected a -90 tilt: {svg}");
        let dy = svg
            .split("rotate(-90) translate(0,")
            .nth(1)
            .unwrap()
            .split(')')
            .next()
            .unwrap()
            .parse::<f64>()
            .unwrap();
        assert_eq!(dy, 3.0, "middle baseline (round(0.30*11)), not top's 9: {svg}");
    }

    /// MEDIUM(a) round-2 regression: an authored `axis_x.labels.align`
    /// must win the tilted emission's anchor the same way it already wins
    /// the fit arm's own ink (`:1064`) — Vega-Lite's `labelAlign` overrides
    /// a rotated axis's default anchor. Vega's own default for a -90 tilt
    /// on a bottom axis is `align: right`; authoring `left` here must flip
    /// the drawn `text-anchor` to `start`, not stay at the default `end`.
    #[test]
    fn tilted_temporal_emission_honors_an_authored_label_align() {
        let mut p = make_weekly_tilt_payload(100.0, 300.0);
        p["resolved"]["style"]["axis_x"]["labels"]["align"] = serde_json::json!("left");
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains("rotate(-90)"), "expected a -90 tilt: {svg}");
        assert!(
            svg.contains("<text text-anchor=\"start\""),
            "an authored labels.align=left must anchor the rotated label at start, \
             not Vega's own -90 default (end): {svg}"
        );
    }

    /// HIGH-4/HIGH-7 render-level regression: Vega's `labelOverlap: parity`
    /// hides label TEXT only — ticks and gridlines stay at every opener.
    /// So the tick/gridline count must exceed the number of VISIBLE label
    /// `<text>` elements once parity actually thins something.
    #[test]
    fn parity_hides_label_text_but_not_ticks_or_gridlines() {
        let mut p = make_weekly_tilt_payload(100.0, 300.0);
        p["resolved"]["style"]["axis_x"]["grid"]["visible"] = serde_json::json!(true);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let x_tick_count = svg.matches("stroke=\"#EDEFF2\" stroke-width=\"1\"/>").count();
        assert_eq!(x_tick_count, 5, "every one of the 5 openers keeps its tick/gridline: {svg}");
        let visible_labels = svg.matches("<text text-anchor=").count();
        assert!(
            visible_labels < 5,
            "parity must have hidden some label text ({visible_labels} of 5): {svg}"
        );
    }

    /// A 24-month payload whose two flushed edges (`_pair_clears` doubles a
    /// flushed label's full width, not its half) sit right at L3's band
    /// boundary: at `payload.width = 960` (`band = 960*0.8/24 = 32`) both
    /// the left edge (Jan, full 18.95px + Feb's half 9.82px = 28.77px) and
    /// the tighter right edge (Nov's half 10.32px + Dec's flushed-full
    /// 20.64px = 30.95px) clear; at the ~837px fitted plot a wide right
    /// y-gutter reserves here (`band = 837*0.8/24 = 27.9`), the right edge
    /// no longer clears. Measured against the real Inter metrics
    /// (`test_fonts()`), not a stand-in — this is what makes the boundary
    /// constructible without guessing.
    fn make_monthly_ladder_payload(width: f64) -> serde_json::Value {
        let mut p = make_line_payload(true, false);
        let rows: Vec<serde_json::Value> = (0..24)
            .map(|i| {
                let (y, m) = (2024 + i / 12, i % 12 + 1);
                serde_json::json!({"week": format!("{y:04}-{m:02}-01"), "signups": 999_999_999.0 + i as f64})
            })
            .collect();
        p["rows"] = serde_json::json!(rows);
        p["width"] = serde_json::json!(width);
        p["height"] = serde_json::json!(300.0);
        let ax = &mut p["resolved"]["style"]["axis_x"]["labels"];
        ax["overlap"] = serde_json::json!({"skip": true, "tilt": false});
        p["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::json!(",.0f");
        p
    }

    /// HIGH-7/L3 render-level regression, replacing the vacuous
    /// `tilt_decision_is_stable_under_a_wider_y_gutter` (round-2 review:
    /// its 30-daily-label/400px fixture sat at the saturation point where
    /// both `payload.width` and the fitted `pw` pick the same steepest
    /// tilt rung — mutating `line.rs:1010` from `payload.width` to `pw`
    /// left it green). `make_monthly_ladder_payload`'s 24-month domain
    /// sits AT the flat/bucketed-ladder boundary instead: its own
    /// y-gutter (a right axis formatting 999,999,999-scale values,
    /// `,.0f`) narrows the fitted plot from 960px to ~837px, straddling
    /// the 30.95px right-edge threshold on either side of the mutation.
    /// The rail-concat arm (`:1231`, `cadence_w = payload.width -
    /// (rail_pane_w + spacing)`) has no equivalent mutation: it is a
    /// single deterministic subtraction, with no iteratively-fitted `pw`
    /// candidate standing in for the slot width to swap in.
    #[test]
    fn bucketed_ladder_band_is_the_slot_width_not_the_fitted_plot() {
        let _locale = crate::time_locale::en_us_scope();
        let p = make_monthly_ladder_payload(960.0);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        // Flat (correct, band from payload.width=960 → 32px, both edges
        // clear): every month keeps its own label, so Feb is drawn. The
        // `payload.width`→`pw` mutation narrows the band to ~27.9px, the
        // right edge (Nov/Dec) no longer clears, and the ladder steps to
        // quarterly (Jan/Apr/Jul/Oct only) — Feb disappears.
        assert!(svg.contains(">Feb</text>"), "band must come from payload.width (32px), not pw (~27.9px): {svg}");
    }

    fn attr(tag: &str, name: &str) -> f64 {
        tag.split(&format!(" {name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
    }

    fn text_tag(svg: &str, label: &str) -> String {
        let pos = svg.find(&format!(">{label}</text>")).unwrap_or_else(|| panic!("no {label}: {svg}"));
        svg[svg[..pos].rfind("<text").unwrap()..pos].to_string()
    }

    /// The `<text>` tag of a rail label, which reads "{value} {name}".
    fn rail_tag(svg: &str, name: &str) -> String {
        let pos = svg.find(&format!(" {name}</text>")).unwrap_or_else(|| panic!("no rail label {name}: {svg}"));
        svg[svg[..pos].rfind("<text").unwrap()..pos].to_string()
    }

    /// The line's own vertex x-positions, in domain order (relative to the
    /// plot group) — one per `M`/`L` command in the FIRST `<path d="M...">`.
    fn path_vertex_xs(svg: &str) -> Vec<f64> {
        let d_start = svg.find("d=\"M").unwrap() + 4; // past `d="M`
        let d_end = d_start + svg[d_start..].find('"').unwrap();
        svg[d_start..d_end]
            .split(['M', 'L'])
            .map(|pair| pair.split(',').next().unwrap().parse().unwrap())
            .collect()
    }

    /// The line's own vertex y-positions (canvas coordinates, top-down),
    /// in domain order — the y-sibling of `path_vertex_xs`, same source path.
    fn path_vertex_ys(svg: &str) -> Vec<f64> {
        let d_start = svg.find("d=\"M").unwrap() + 4;
        let d_end = d_start + svg[d_start..].find('"').unwrap();
        svg[d_start..d_end]
            .split(['M', 'L'])
            .map(|pair| pair.split(',').nth(1).unwrap().parse().unwrap())
            .collect()
    }

    /// The plot group's translate and the rightmost x any series line
    /// reaches (its last vertex, at the plot's right edge), in canvas
    /// coordinates.
    fn plot_origin_and_line_end(svg: &str) -> (f64, f64, f64) {
        let t = svg.split("<g transform=\"translate(").nth(1).unwrap();
        let mut it = t.split(')').next().unwrap().split(',');
        let (gx, gy): (f64, f64) = (it.next().unwrap().parse().unwrap(), it.next().unwrap().parse().unwrap());
        let mut end = f64::NEG_INFINITY;
        for (i, _) in svg.match_indices("<path d=\"") {
            let tag = svg[i..].split("/>").next().unwrap();
            if !tag.contains("fill=\"none\"") || !tag.contains("stroke=\"#3164a3\"") {
                continue;
            }
            let d = tag.split("d=\"").nth(1).unwrap().split('"').next().unwrap();
            for pair in d.split(|c| c == 'M' || c == 'L').filter(|p| !p.trim().is_empty()) {
                let x: f64 = pair.trim().split(',').next().unwrap().trim().parse().unwrap();
                end = end.max(x);
            }
        }
        (gx, gy, gx + end)
    }

    /// The legend's box for the fixture: `ceil(max(title, entries))` wide
    /// (the 12px "Series" title wins over the one-letter labels) and a
    /// 17px title band over two 11px rows tall.
    fn fixture_legend_box(fonts: &FontBook) -> (f64, f64) {
        let entries = 20.0 + fonts.measure("A", TEST_FONT, 11.0).unwrap().max(fonts.measure("B", TEST_FONT, 11.0).unwrap());
        (entries.max(fonts.measure("Series", TEST_FONT, 12.0).unwrap()).ceil(), 17.0 + 29.0)
    }

    /// Production's endpoint-rail hconcat keeps the legend inside the main
    /// pane, and Vega anchors a right legend at `ceil(x2) + 18` of the
    /// pane's plot ∪ axes ∪ the line stroke's ink (probe: the anchor moves
    /// with the line's full stroke width, not with point symbols):
    /// `series-label-placement/line_endpoint_labels_legend_kept` puts it at
    /// 368 = ceil(346 + 4) + 18 with a 67 × 62 box, and the rail pane
    /// follows the box at the hconcat spacing. Symbols are the series-
    /// colored circles Vega draws for a line legend.
    #[test]
    fn rail_right_legend_sits_past_the_line_stroke_and_the_rail_follows_it() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        p["resolved"]["legend"]["position"] = json!({"edge": "right", "align": "start", "overlay": false});
        p["resolved"]["legend"]["title"]["font"] = json!({"family": TEST_FONT, "size": 12.0, "weight": 600.0, "case": "title"});
        let fonts = test_fonts();
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &fonts).expect("renders");
        let (gx, gy, line_end) = plot_origin_and_line_end(&svg);
        let (box_w, _) = fixture_legend_box(&fonts);
        let title = text_tag(&svg, "Series");
        assert_eq!(attr(&title, "x"), line_end + 4.0 + 18.0, "legend at ceil(plot + stroke) + 18: {title}");
        assert_eq!(attr(&title, "y"), gy + 9.0, "title at baseline top: {title}");
        let circles: Vec<&str> = svg.match_indices("A5,5,0,1,1,-5,0A5,5,0,1,1,5,0").map(|(i, _)| {
            svg[svg[..i].rfind("<path").unwrap()..i + 40].split("/>").next().unwrap()
        }).collect();
        assert_eq!(circles.len(), 2, "one circle symbol per series: {svg}");
        // The rail pane sits after the legend box at the hconcat spacing.
        let rail_text = svg.find("<text x=\"0\" ").expect("the rail's labels");
        let rail_group = &svg[svg[..rail_text].rfind("<g transform=\"translate(").unwrap() + "<g transform=\"translate(".len()..];
        let rail_x: f64 = rail_group.split(',').next().unwrap().parse().unwrap();
        assert_eq!(rail_x, attr(&title, "x") + box_w + 6.0, "rail after the legend box: {svg}");
        assert!(gx > 16.0, "{svg}");
    }

    /// A bottom legend on the same hconcat anchors at `ceil(y2) + 18` of
    /// the plot ∪ the x axis's band, left-aligned with the plot, and the
    /// pane gives up `18 + box` of height for it — the legend is the
    /// pane's bottom-most ink, so its box ends at the padded edge
    /// (`line_endpoint_labels_legend_bottom`: 280 = 225 + 37 + 18, box
    /// 67 × 62 on a 408px canvas: 46 + 280 + 62 = 388 = 408 − 16 − 4 of
    /// probe-rounding on the pane).
    #[test]
    fn rail_bottom_legend_hangs_below_the_axis_band_at_the_plot_left() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        p["resolved"]["legend"]["title"]["font"] = json!({"family": TEST_FONT, "size": 12.0, "weight": 600.0, "case": "title"});
        let fonts = test_fonts();
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &fonts).expect("renders");
        let (gx, _, _) = plot_origin_and_line_end(&svg);
        let (_, box_h) = fixture_legend_box(&fonts);
        let title = text_tag(&svg, "Series");
        assert_eq!(attr(&title, "x"), gx, "left-aligned with the plot: {title}");
        assert_eq!(attr(&title, "y"), 400.0 - 16.0 - box_h + 9.0, "box ends at the padded edge: {title}");
    }

    /// Without a rail the fit arm reserves the same boxes: a right legend
    /// is the view's rightmost ink, a bottom one its bottom-most.
    #[test]
    fn fit_arm_legends_are_the_outermost_ink() {
        use serde_json::json;
        let fonts = test_fonts();
        let (box_w, box_h) = fixture_legend_box(&fonts);
        for position in ["right", "bottom"] {
            let mut p = make_multi_series_line_payload_with_bottom_legend();
            p["resolved"]["style"]["axis_y"]["position"] = json!("right");
            p["resolved"]["legend"]["position"] = json!({"edge": position, "align": "start", "overlay": false});
            p["resolved"]["legend"]["title"]["font"] = json!({"family": TEST_FONT, "size": 12.0, "weight": 600.0, "case": "title"});
            // No y-axis title: the right axis's outermost ink is its
            // flip-aligned (end-anchored) tick labels' shared x.
            p["resolved"]["style"]["axis_y"]["title"]["visible"] = json!(false);
            let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &fonts).expect("renders");
            let (gx, gy, _) = plot_origin_and_line_end(&svg);
            let title = text_tag(&svg, "Series");
            // Every axis label as (x, y, anchor), in plot coordinates: the
            // right axis's tick labels are end-anchored, the x labels
            // middle-anchored; legend text is start-anchored.
            let labels: Vec<(f64, f64, String)> = svg
                .match_indices("<text")
                .map(|(i, _)| {
                    let tag = &svg[i..svg[i..].find("</text>").unwrap() + i];
                    let anchor = tag.split("text-anchor=\"").nth(1).unwrap().split('"').next().unwrap();
                    (attr(tag, "x"), attr(tag, "y"), anchor.to_string())
                })
                .collect();
            if position == "right" {
                assert_eq!(attr(&title, "x"), 600.0 - 16.0 - box_w, "right legend at the padded edge: {title}");
                assert_eq!(attr(&title, "y"), gy + 9.0, "{title}");
                // Anchored 18px past the right y-axis labels' own ink, not
                // the plot: their end-anchored x (less the crisp 0.5),
                // ceiled in plot coordinates.
                let axis_ink = labels
                    .iter()
                    .filter(|(_, _, anchor)| anchor == "end")
                    .map(|(x, _, _)| x - 0.5)
                    .fold(f64::NEG_INFINITY, f64::max);
                assert_eq!(attr(&title, "x"), axis_ink.ceil() + gx + 18.0, "{svg}");
            } else {
                assert_eq!(attr(&title, "x"), gx, "{title}");
                assert_eq!(attr(&title, "y"), 400.0 - 16.0 - box_h + 9.0, "bottom legend at the padded edge: {title}");
                // Anchored 18px below the x labels' rows (top baseline at
                // +9 past the crisp 0.5, one fontSize tall), ceiled in plot
                // coordinates; the legend's own text sits below the title.
                let row_bottom = labels
                    .iter()
                    .filter(|(_, y, anchor)| anchor != "end" && *y < attr(&title, "y") - gy)
                    .map(|(_, y, _)| y - 9.5 + 11.0)
                    .fold(f64::NEG_INFINITY, f64::max);
                assert_eq!(attr(&title, "y"), row_bottom.ceil() + gy + 18.0 + 9.0, "{svg}");
            }
        }
    }

    /// A `position: top`, `direction: horizontal` legend rides the fit arm's
    /// TOP ink the same way bar's does: Vega's symbol-legend entry grid
    /// (`legend.rs::legend_grid`), reserved `VEGA_LEGEND_OFFSET (18) +
    /// box_h` above the plot's own top edge, left-aligned with it — the law
    /// `quick-guide_7/review_target_by_month`'s reference draws (legend
    /// group at `translate(0, -47)` = `-(18 + 29)`, a 3-series/2-column
    /// grid whose second row costs 16px, box 29 high).
    #[test]
    fn fit_arm_top_horizontal_legend_reserves_vega_grid_ink() {
        use crate::families::legend::legend_grid;
        use serde_json::json;
        let fonts = test_fonts();
        let three_series = |columns: u32| {
            let mut p = make_multi_series_line_payload_with_bottom_legend();
            p["rows"] = json!([
                {"week": "2026-01-01", "series": "A", "signups": 100.0},
                {"week": "2026-01-08", "series": "A", "signups": 150.0},
                {"week": "2026-01-01", "series": "B", "signups": 40.0},
                {"week": "2026-01-08", "series": "B", "signups": 60.0},
                {"week": "2026-01-01", "series": "C", "signups": 10.0},
                {"week": "2026-01-08", "series": "C", "signups": 20.0},
            ]);
            p["resolved"]["palette"] = json!(["#3164a3", "#779bc9", "#608470"]);
            p["resolved"]["style"]["axis_y"]["position"] = json!("right");
            p["resolved"]["legend"]["position"] = json!({"edge": "top", "align": "start", "overlay": false});
            p["resolved"]["legend"]["direction"] = json!("horizontal");
            p["resolved"]["legend"]["columns"] = json!(columns);
            p["resolved"]["legend"]["title"]["visible"] = json!(false);
            p
        };
        // columns=3 puts all three entries in a single row (box_h 13);
        // columns=2 wraps to two rows (box_h 29) — a 16px taller grid.
        let one_row = render(&serde_json::from_value::<Payload>(three_series(3)).unwrap(), &fonts)
            .expect("renders");
        let svg = render(&serde_json::from_value::<Payload>(three_series(2)).unwrap(), &fonts)
            .expect("renders");
        let (gx, gy, _) = plot_origin_and_line_end(&svg);
        let (_, gy_one_row, _) = plot_origin_and_line_end(&one_row);

        let ws: Vec<f64> =
            ["A", "B", "C"].iter().map(|s| fonts.measure(s, TEST_FONT, 11.0).unwrap()).collect();
        let grid = legend_grid(&ws, &[None; 3], false, 11.0, 2, 8.0);
        assert_eq!(grid.box_h, 29.0, "two 11px rows at 0.25/16");
        assert_eq!(gy - gy_one_row, 16.0, "the second legend row costs exactly 16px of plot top");

        // The plot's top margin is exactly `pad_t + VEGA_LEGEND_OFFSET +
        // box_h` (pad_t 16 from the fixture): the legend reservation wins
        // the fit's top-ink union outright over the line stroke's own
        // (much smaller) reach above the plot.
        assert_eq!(
            gy,
            16.0 + crate::families::legend::VEGA_LEGEND_OFFSET + grid.box_h,
            "plot top reserves pad_t + offset + box_h: gy={gy}"
        );

        let c = grid.size / 2.0;
        let mid = measure::baseline_shift("middle", 11.0);
        let legend_top = gy - 18.0 - grid.box_h;
        for (label, col, row) in [("A", 0, 0), ("B", 1, 0), ("C", 0, 1)] {
            let tag = text_tag(&svg, label);
            assert_eq!(attr(&tag, "x"), gx + grid.col_x[col] + grid.label_x, "{label} label x: {tag}");
            assert_eq!(attr(&tag, "y"), legend_top + grid.row_y[row] + c + mid, "{label} label y: {tag}");
        }
    }

    /// Area takes the same top-legend law — `render_inner` is shared, so
    /// the grid/reservation logic is exercised through `area::render`
    /// (draw_areas=true), not re-derived.
    #[test]
    fn area_takes_the_same_top_legend_law() {
        use crate::families::legend::legend_grid;
        use serde_json::json;
        let fonts = test_fonts();
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["style"]["axis_y"]["position"] = json!("right");
        p["resolved"]["legend"]["position"] = json!({"edge": "top", "align": "start", "overlay": false});
        p["resolved"]["legend"]["direction"] = json!("horizontal");
        p["resolved"]["legend"]["columns"] = json!(2);
        p["resolved"]["legend"]["title"]["visible"] = json!(false);
        p["resolved"]["style"]["area_mark"] = json!({"opacity": 0.16, "backdrop": false, "curve": null});
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &fonts, true)
            .expect("area renders a top horizontal legend");
        assert!(svg.contains(">A</text>") && svg.contains(">B</text>"), "{svg}");

        // Two entries at columns=2 is a single row (box_h 13) — a distinct
        // shape from the line fit test's two-row 29, so this pins its own
        // geometry rather than merely reusing the earlier assertion.
        let ws: Vec<f64> =
            ["A", "B"].iter().map(|s| fonts.measure(s, TEST_FONT, 11.0).unwrap()).collect();
        let grid = legend_grid(&ws, &[None; 2], false, 11.0, 2, 8.0);
        assert_eq!(grid.box_h, 13.0, "one row of two entries");
        let (gx, gy, _) = plot_origin_and_line_end(&svg);
        assert_eq!(
            gy,
            16.0 + crate::families::legend::VEGA_LEGEND_OFFSET + grid.box_h,
            "area's plot top reserves pad_t + offset + box_h, same as line's"
        );
        let a = text_tag(&svg, "A");
        assert_eq!(attr(&a, "x"), gx + grid.col_x[0] + grid.label_x, "left-aligned with the plot");
    }

    /// A right-axis, rail-less line chart with a multi-series legend on
    /// `edge` — the fit arm, the only arm a left or horizontal legend rides.
    fn fit_arm_legend_payload(edge: &str, direction: &str) -> serde_json::Value {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["style"]["axis_y"]["position"] = json!("right");
        p["resolved"]["legend"]["position"] = json!({"edge": edge, "align": "start", "overlay": false});
        p["resolved"]["legend"]["direction"] = json!(direction);
        p["resolved"]["legend"]["title"]["font"] = json!({"family": TEST_FONT, "size": 12.0, "weight": 600.0, "case": "title"});
        p
    }

    /// A left legend is the view's leftmost ink: Vega anchors it at
    /// `floor(xBounds.x1) - offset - box_w` (`legend.js` `legendParams`,
    /// `case Left`), `xBounds` being the plot rect ∪ the data marks' own
    /// stroked ink — here the line's 4px stroke at the plot's left edge — so
    /// the plot origin sits `pad_l + 4 + 18 + box_w` in
    /// (`series-label-placement`'s `line_legend_left` shape: legend
    /// `translate(-82, 0)` = `-(4 + 18 + 60)`).
    #[test]
    fn fit_arm_left_legend_shifts_the_plot_origin_by_the_box_plus_offset() {
        let fonts = test_fonts();
        let svg = render(
            &serde_json::from_value::<Payload>(fit_arm_legend_payload("left", "vertical")).unwrap(),
            &fonts,
        )
        .expect("renders");
        let (gx, gy, _) = plot_origin_and_line_end(&svg);
        let (box_w, _) = fixture_legend_box(&fonts);
        assert_eq!(gx, 16.0 + 4.0 + crate::families::legend::VEGA_LEGEND_OFFSET + box_w, "plot origin: {svg}");
        let title = text_tag(&svg, "Series");
        assert_eq!(attr(&title, "x"), 16.0, "the legend is the view's leftmost ink: {title}");
        assert_eq!(attr(&title, "y"), gy + 9.0, "start-aligned at the plot's top: {title}");
    }

    /// A left value axis with no rail takes the fit arm: its labels sit
    /// `labelPadding` left of the plot, and a left legend clears that axis
    /// ink (`xBounds.x1` is the labels' far edge, not the plot rect's 0), so
    /// the plot origin sits `pad_l + ceil(widest label + 6) + 18 + box_w` in.
    #[test]
    fn a_left_axis_left_legend_clears_the_axis_labels() {
        use serde_json::json;
        let fonts = test_fonts();
        let mut p = fit_arm_legend_payload("left", "vertical");
        p["resolved"]["style"]["axis_y"]["position"] = json!("left");
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &fonts).expect("renders");
        let (gx, _, _) = plot_origin_and_line_end(&svg);
        let (box_w, _) = fixture_legend_box(&fonts);
        let widest = svg
            .match_indices("x=\"-5.500\" ")
            .map(|(i, _)| {
                let text = svg[i..].split('>').nth(1).unwrap().split('<').next().unwrap();
                fonts.measure(text, TEST_FONT, 11.0).unwrap()
            })
            .fold(0.0_f64, f64::max);
        assert!(widest > 0.0, "left labels anchor at -(labelPadding) + 0.5: {svg}");
        assert_eq!(gx, 16.0 + (widest + 6.0).ceil() + crate::families::legend::VEGA_LEGEND_OFFSET + box_w, "plot origin: {svg}");
        assert_eq!(attr(&text_tag(&svg, "Series"), "x"), 16.0, "the legend is the view's leftmost ink");
    }

    /// A visible tick on a left fit-arm axis reserves its length and moves
    /// the labels past it (`sign * (tickSize + labelPadding)`).
    #[test]
    fn a_left_axis_tick_moves_the_labels_past_it() {
        let mut json = make_line_payload(false, false);
        json["resolved"]["style"]["axis_y"]["ticks"] = serde_json::json!({"visible": true, "color": "#D0D3D9", "count": 6.0, "length": 6.0});
        let svg = render(&serde_json::from_value::<Payload>(json).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains("x=\"-11.500\" "), "labels at -(6 + 6) + 0.5: {svg}");
        assert!(svg.contains("x1=\"-5.500\" "), "tick marks end at the plot edge: {svg}");
    }

    /// The anchor reads the marks' own left ink, not a constant: a scale
    /// padding that keeps the stroke inside the plot rect leaves
    /// `xBounds.x1` at the rect's own 0.
    #[test]
    fn fit_arm_left_legend_anchors_on_the_marks_left_ink() {
        use serde_json::json;
        let fonts = test_fonts();
        let mut p = fit_arm_legend_payload("left", "vertical");
        p["resolved"]["style"]["axis_x"]["scale"] = json!({"padding": 20.0});
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &fonts).expect("renders");
        let (gx, _, _) = plot_origin_and_line_end(&svg);
        let (box_w, _) = fixture_legend_box(&fonts);
        assert_eq!(gx, 16.0 + crate::families::legend::VEGA_LEGEND_OFFSET + box_w, "no stroke ink past the rect: {svg}");
    }

    /// A horizontal legend on a bottom strip is one Vega row
    /// (`entryColumns`: every entry in one row when `columns` is unset), so
    /// the strip reserves a single 13px row under its `18` offset.
    #[test]
    fn fit_arm_bottom_horizontal_legend_reserves_vega_row_height() {
        use crate::families::legend::legend_grid;
        use serde_json::json;
        let fonts = test_fonts();
        let mut p = fit_arm_legend_payload("bottom", "horizontal");
        p["resolved"]["legend"]["title"]["visible"] = json!(false);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &fonts).expect("renders");
        let (gx, _, _) = plot_origin_and_line_end(&svg);
        let ws: Vec<f64> =
            ["A", "B"].iter().map(|s| fonts.measure(s, TEST_FONT, 11.0).unwrap()).collect();
        let grid = legend_grid(&ws, &[None; 2], false, 11.0, 0, 8.0);
        assert_eq!(grid.box_h, 13.0, "one 11px row");
        let c = grid.size / 2.0;
        let mid = measure::baseline_shift("middle", 11.0);
        let legend_top = 400.0 - 16.0 - grid.box_h;
        for (label, col) in [("A", 0), ("B", 1)] {
            let tag = text_tag(&svg, label);
            assert_eq!(attr(&tag, "x"), gx + grid.col_x[col] + grid.label_x, "{label} label x: {tag}");
            assert_eq!(attr(&tag, "y"), legend_top + grid.row_y[0] + c + mid, "{label} label y: {tag}");
        }
    }

    /// `columns` flows a horizontal legend row-major on any edge
    /// (vega-parser `legend-symbol.js`: `floor(index / columns)`): with two
    /// columns A and B share row 0 and C wraps to row 1.
    #[test]
    fn horizontal_legend_columns_flow_row_major_on_the_left_edge() {
        use crate::families::legend::legend_grid;
        use serde_json::json;
        let fonts = test_fonts();
        let mut p = fit_arm_legend_payload("left", "horizontal");
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "A", "signups": 100.0},
            {"week": "2026-01-08", "series": "A", "signups": 150.0},
            {"week": "2026-01-01", "series": "B", "signups": 40.0},
            {"week": "2026-01-08", "series": "B", "signups": 60.0},
            {"week": "2026-01-01", "series": "C", "signups": 10.0},
            {"week": "2026-01-08", "series": "C", "signups": 20.0},
        ]);
        p["resolved"]["palette"] = json!(["#3164a3", "#779bc9", "#608470"]);
        p["resolved"]["legend"]["columns"] = json!(2);
        p["resolved"]["legend"]["title"]["visible"] = json!(false);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &fonts).expect("renders");
        let ws: Vec<f64> =
            ["A", "B", "C"].iter().map(|s| fonts.measure(s, TEST_FONT, 11.0).unwrap()).collect();
        let grid = legend_grid(&ws, &[None; 3], false, 11.0, 2, 8.0);
        let (gx, gy, _) = plot_origin_and_line_end(&svg);
        let legend_x = gx - (4.0 + crate::families::legend::VEGA_LEGEND_OFFSET + grid.width.ceil());
        let c = grid.size / 2.0;
        let mid = measure::baseline_shift("middle", 11.0);
        for (label, col, row) in [("A", 0, 0), ("B", 1, 0), ("C", 0, 1)] {
            let tag = text_tag(&svg, label);
            assert_eq!(attr(&tag, "x"), legend_x + grid.col_x[col] + grid.label_x, "{label} label x: {tag}");
            assert_eq!(attr(&tag, "y"), gy + grid.row_y[row] + c + mid, "{label} label y: {tag}");
        }
    }

    /// A left legend beside an endpoint rail widens the plot's left margin by
    /// its box and the offset, as a right one widens the right.
    #[test]
    fn a_left_legend_beside_a_rail_widens_the_left_margin() {
        use serde_json::json;
        let origin_x = |edge: &str, direction: &str| {
            let mut p = fit_arm_legend_payload(edge, direction);
            p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
            let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
                .unwrap_or_else(|e| panic!("{edge} {direction}: {e}"));
            plot_origin_and_line_end(&svg).0
        };
        assert!(origin_x("left", "vertical") > origin_x("right", "vertical") + 18.0);
        origin_x("bottom", "horizontal");
        origin_x("right", "horizontal");
    }

    /// A vertical legend over authored columns (which Vega fills
    /// column-major) draws on the rail concat as on the fit arm.
    #[test]
    fn a_vertical_legend_over_columns_renders_beside_a_rail() {
        use serde_json::json;
        let mut p = fit_arm_legend_payload("right", "vertical");
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        p["resolved"]["legend"]["columns"] = json!(2);
        render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("the rail concat draws it");
    }

    /// A left legend beside a ghost mirror axis anchors left of the ghost's
    /// labels, which `xBounds` also unions: the legend clears the ghost ink,
    /// so the plot origin is `pad_l + ceil(ghost far edge) + 18 + box_w`.
    #[test]
    fn a_left_legend_anchors_left_of_the_mirror_ghost() {
        use serde_json::json;
        let fonts = test_fonts();
        let plain = fit_arm_legend_payload("left", "vertical");
        let mut mirrored = plain.clone();
        mirrored["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
        let origin = |p: serde_json::Value| {
            let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &fonts).expect("renders");
            plot_origin_and_line_end(&svg).0
        };
        assert!(origin(mirrored) > origin(plain), "the ghost labels sit left of the plot");
    }

    /// A top horizontal legend riding the endpoint-rail concat (left y axis)
    /// sits above the plot, which gives up its box and the legend offset.
    #[test]
    fn top_horizontal_legend_on_rail_concat_reserves_the_top() {
        use serde_json::json;
        let plot_top = |edge: &str| {
            let mut p = make_multi_series_line_payload_with_bottom_legend();
            p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
            p["resolved"]["legend"]["position"] = json!({"edge": edge, "align": "start", "overlay": false});
            p["resolved"]["legend"]["direction"] = json!("horizontal");
            p["resolved"]["legend"]["title"]["visible"] = json!(false);
            let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
            plot_origin_and_line_end(&svg).1
        };
        assert!(plot_top("top") > plot_top("bottom") + 18.0);
    }

    /// A top horizontal legend keeps its title: Vega stacks the title band
    /// (`titleFontSize + titlePadding`, 12 + 5) over the entry row inside the
    /// same box, so the plot gives up `18 + 17 + 13` above its top edge and
    /// the title sits at the box's own top (`_kwargs.py` bakes the title
    /// away only when the author left `title.visible` unset).
    #[test]
    fn fit_arm_top_horizontal_legend_reserves_its_title_band() {
        let fonts = test_fonts();
        let svg = render(
            &serde_json::from_value::<Payload>(fit_arm_legend_payload("top", "horizontal")).unwrap(),
            &fonts,
        )
        .expect("renders");
        let (_, gy, _) = plot_origin_and_line_end(&svg);
        let box_h = 17.0 + 13.0; // the title band over one 11px entry row
        assert_eq!(gy, 16.0 + crate::families::legend::VEGA_LEGEND_OFFSET + box_h, "plot top: {svg}");
        let title = text_tag(&svg, "Series");
        assert_eq!(
            attr(&title, "y"),
            gy - crate::families::legend::VEGA_LEGEND_OFFSET - box_h + measure::baseline_shift("top", 12.0),
            "{title}"
        );
    }

    /// A top horizontal legend over a composed `position: top` strip draws:
    /// Vega's `yBounds` carries the plot and the x axis, not the strip's bare
    /// text marks, so the legend sits `offset + box_h` above the plot with
    /// the strip's rows between it and the plot.
    #[test]
    fn top_horizontal_legend_over_a_composed_top_strip_draws() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["style"]["axis_y"]["position"] = json!("right");
        p["resolved"]["legend"]["position"] = json!({"edge": "top", "align": "start", "overlay": false});
        p["resolved"]["legend"]["direction"] = json!("horizontal");
        p["resolved"]["legend"]["columns"] = json!(2);
        p["resolved"]["legend"]["title"]["visible"] = json!(false);
        p["resolved"]["support_table"] = json!({
            "entries": [{"source": "signups", "aggregate": "sum", "format": ",d", "label": "Signups"}]
        });
        p["resolved"]["effective_support_table_style"] = support_table_style_json();
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains(">Signups<"), "the strip row label draws: {svg}");
        let legend_y = text_tag(&svg, "A");
        let strip_y = text_tag(&svg, "Signups");
        assert!(attr(&legend_y, "y") < attr(&strip_y, "y"), "the legend sits above the strip row: {legend_y} {strip_y}");
    }

    /// The y axis's labels take Vega's parity overlap rule under the theme's
    /// `labels.min_gap` (`labelSeparation`), as bar's value axes do: on a
    /// short canvas the 20-step ladder 40–140 pitches under 27px, so every
    /// other label is hidden (60, 100, 140 go; 40, 80, 120 stay) on both
    /// Vega arms — the rail concat with a bottom legend and the fit arm.
    /// `series-label-placement/line_endpoint_labels_legend_bottom`: ten 2k
    /// labels at a 22.5px pitch, five drawn.
    #[test]
    fn y_labels_take_vega_parity_under_min_gap() {
        use serde_json::json;
        let fonts = test_fonts();
        for arm in ["rail", "fit"] {
            let mut p = make_multi_series_line_payload_with_bottom_legend();
            p["height"] = json!(220.0);
            p["resolved"]["style"]["axis_y"]["labels"]["min_gap"] = json!(16.0);
            if arm == "rail" {
                p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
            } else {
                p["resolved"]["style"]["axis_y"]["position"] = json!("right");
                p["resolved"]["legend"]["visible"] = json!(false);
            }
            let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &fonts).expect("renders");
            let drawn: Vec<&str> = ["40", "60", "80", "100", "120", "140"]
                .into_iter()
                .filter(|l| svg.contains(&format!(">{l}</text>")))
                .collect();
            assert_eq!(drawn, ["40", "80", "120"], "{arm}: every other label hidden: {svg}");
        }
    }

    /// `make_line_payload(false, false)` is single-series (no `color:`) — a
    /// per_series entry needs the chart's own color-domain series list, so
    /// this refuses via `visual_row_count`'s `series_count > 0` guard before
    /// any strip row is built, on every arm (the composed fit arm,
    /// rail-concat, and — the one this single-series fixture actually
    /// reaches — the legacy splice).
    #[test]
    fn attached_support_table_with_per_series_entry_and_no_color_field_is_rejected() {
        let mut payload_json = make_line_payload(false, false);
        payload_json["resolved"]["support_table"] =
            serde_json::json!({"entries": [{"per_series": "signups", "format": ",d"}]});
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let fonts = test_fonts();
        let err = render(&payload, &fonts).unwrap_err();
        assert!(err.contains("per_series"), "error names the unsupported shape: {err}");
        assert!(err.contains("color encoding"), "names the missing input: {err}");
    }

    /// `playground/support-table-attached`'s `weekly_line` shape: a single
    /// `source:` row attached to a line chart. Deletion counterfactual: the
    /// old blanket refusal (or an un-grown canvas) both go red here — either
    /// `render` errors, or the canvas height stays at `payload.height` while
    /// the strip text is missing.
    #[test]
    fn attached_support_table_grows_the_card_and_renders_the_strip() {
        let mut payload_json = make_line_payload(false, false);
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "signups", "format": ",d", "label": "Signups"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload_h = payload_json["height"].as_f64().unwrap();
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("support_table line renders");

        // padding_top(8) + row_h(14 + 2*2=18) + padding_bottom(4) = 30: the pad canvas
        // keeps the plot at the declared height and grows around the strip.
        assert!(canvas_dims(&svg).1 >= payload_h + 30.0, "canvas grows by the strip height: {svg}");
        assert!(svg.contains(">Signups<"), "row label rendered: {svg}");
        assert!(svg.contains(">100<") && svg.contains(">150<") && svg.contains(">130<"), "cell values rendered: {svg}");
    }

    /// A `support_table` chart renders under production's `autosize: {type:
    /// pad, contains: padding}` (S1): the payload's width/height ARE the plot
    /// rect and the canvas grows around the ink, instead of the legacy
    /// splice's "canvas = payload dims + strip_height".
    ///
    /// The three pinned terms:
    /// * S3 — `plot_top = pad_t + title_fs + theme_offset +
    ///   support_table_title_offset_correction(title_fs) + ink_extent`,
    ///   porting production's closed form
    ///   (`support_table_attachment.py:4425`, written at `:4951-4962`): the
    ///   strip's own ink extent grows the view-bounds top margin under
    ///   autosize:pad; `title.offset` only cancels Vega's own title-baseline
    ///   algebra, a font-size-only term, never `strip_h` itself.
    /// * S6 — the first cell overhangs the plot's left edge by its FULL
    ///   shaped width and joins the canvas union, where the legacy splice
    ///   never grows the canvas sideways at all.
    /// * S2/S4 — rows at `-(13 + 18i)` painted at the middle-baseline
    ///   shift, cells anchored on the point's own x with no band dx, stubs
    ///   at `plot_w + axis_y.labels.padding`.
    #[test]
    fn attached_support_table_takes_the_composed_autosize_pad_arm() {
        let mut payload_json = make_line_payload(true, false);
        payload_json["resolved"]["title"] = serde_json::json!("Weekly Signups");
        payload_json["resolved"]["title_style"] = serde_json::json!({
            "font": {"family": TEST_FONT, "size": 18.0, "weight": "500", "color": "#494A4C"},
            "position": {"offset": 10.0}
        });
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "signups", "format": ",d", "label": "Signups"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("composed support_table line renders");

        let cell_w = ["100", "150", "130"]
            .iter()
            .map(|t| fonts.measure_shaped(t, TEST_FONT, 11.0).unwrap())
            .fold(0.0_f64, f64::max);
        let stub_w = fonts.measure_shaped("Signups", TEST_FONT, 11.0).unwrap();
        let (canvas_w, canvas_h) = canvas_dims(&svg);
        let plot_x = plot_group_x(&svg);
        let plot_y = group_y(&svg);

        // S6: the left margin is the widest cell's own overhang, not the
        // 4px line stroke the mark ink alone would give.
        assert!(cell_w > 4.0, "the cell must be the binding left ink for this test to mean anything");
        assert_eq!(plot_x, 16.0 + cell_w.ceil(), "plot_left = pad_l + the first cell's full width");
        // S3: 16 + 18 (title fs) + 10 (theme offset) + 7 (correction(18) =
        //     11 - 18 + round(0.79*18)=14) + 19 (ink extent: 13 +
        //     round(0.8*11) - 3) = 70.
        assert_eq!(plot_y, 70.0, "title.offset corrects to a font-size-only term: {svg}");
        // S1: the plot rect IS the payload view (600x400) and the canvas
        // ceils around the ink — the stub column is the rightmost of it.
        assert_eq!(
            canvas_w,
            plot_x + 600.0 + (6.0 + stub_w).ceil() + 16.0,
            "canvas = ceil(padding + ink union) around a 600-wide plot: {svg}"
        );
        assert!(
            canvas_h > plot_y + 400.0 + 16.0,
            "canvas clears the deepened title block, the plot and the x band: {canvas_h}"
        );

        // S2/S4: row 0 at -13 painted at the middle-baseline shift (+3),
        // cells anchored on the point x itself (no band dx), stub past the
        // plot's right edge by axis_y.labels.padding.
        // The strip paints last, so read the LAST match — "100" is also a y
        // tick label here.
        let attr = |tag: &str, name: &str| -> String {
            let idx = svg.rfind(&format!(">{tag}<")).unwrap();
            let start = svg[..idx].rfind("<text").unwrap();
            svg[start..idx].split(&format!("{name}=\"")).nth(1).unwrap().split('"').next().unwrap().to_string()
        };
        assert_eq!(attr("Signups", "y"), format!("{:.2}", plot_y - 10.0));
        assert_eq!(attr("Signups", "x"), format!("{:.2}", plot_x + 600.0 + 6.0));
        assert_eq!(attr("Signups", "text-anchor"), "start");
        assert_eq!(attr("100", "text-anchor"), "end");
        assert_eq!(attr("100", "x"), format!("{plot_x:.2}"), "no band dx on a line's point");
    }

    /// `axis_y.mirror` on a nominal x flips the composed strip's own shared
    /// x channel to a band scale too — `cell_xs` (the strip's per-cell x
    /// positions) must land on band centers (`step*(i+0.5)`), the SAME
    /// `use_band_positions` law every other position call site now reads,
    /// not the point-scale positions this arm builds unconditionally.
    /// Mutations: reverting `cell_xs`'s own band branch back to
    /// `point_scale.position_at(i)` moves every pinned x to the point-scale
    /// formula, and re-passing `band_centered = use_band_positions` adds the
    /// bar-only `dx` to every cell — either fails this test.
    #[test]
    fn support_table_strip_cells_land_at_band_centers_under_mirror() {
        let mut payload_json = make_line_payload(true, false);
        payload_json["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        payload_json["rows"] = serde_json::json!([
            {"week": "Jan", "signups": 100.0},
            {"week": "Feb", "signups": 150.0},
            {"week": "Mar", "signups": 130.0},
        ]);
        payload_json["resolved"]["style"]["axis_y"]["mirror"] = serde_json::json!(true);
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "signups", "format": ",d", "label": "Signups"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("mirrored composed support_table line renders");

        let attr = |tag: &str, name: &str| -> String {
            let idx = svg.rfind(&format!(">{tag}<")).unwrap();
            let start = svg[..idx].rfind("<text").unwrap();
            svg[start..idx].split(&format!("{name}=\"")).nth(1).unwrap().split('"').next().unwrap().to_string()
        };
        // Production writes NO `dx` on a line/area strip
        // (`support_table_attachment.py:1176-1181`: "For line/area charts
        // dx is None: the aligned edge sits straight on the mark's x
        // position"), so each cell's raw `x` IS the band center
        // `plot_x + step*(i+0.5)` (3 bands over the 600px plot, step 200) —
        // not the point-scale positions (step 300, first cell at `plot_x`)
        // and not band center + the bar-only `dx = max_bare_w / 2`.
        let plot_x: f64 =
            svg.split("translate(").nth(1).unwrap().split(',').next().unwrap().parse().unwrap();
        for (label, i) in [("100", 0), ("150", 1), ("130", 2)] {
            assert_eq!(
                attr(label, "x"),
                format!("{:.2}", plot_x + 200.0 * (i as f64 + 0.5)),
                "cell {i} sits exactly on its band center: {svg}"
            );
        }
    }

    /// `area.rs` is `line::render_inner(payload, fonts, true)`, so the
    /// composed arm must be reachable from it too — `weekly_area_stacked`'s
    /// family. Deletion counterfactual: gating the arm on the line mark
    /// alone leaves this on the legacy splice, whose canvas is the payload
    /// dims plus `strip_height` and whose plot never reaches 600 wide.
    #[test]
    fn area_takes_the_composed_autosize_pad_arm_too() {
        let mut payload_json = make_line_payload(true, false);
        payload_json["resolved"]["style"]["area_mark"] =
            serde_json::json!({"opacity": 0.16, "backdrop": false});
        payload_json["resolved"]["support_table"] = serde_json::json!({
            "entries": [{"source": "signups", "format": ",d", "label": "Signups"}]
        });
        payload_json["resolved"]["effective_support_table_style"] = support_table_style_json();
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let fonts = test_fonts();
        let svg = render_inner(&payload, &fonts, true).expect("composed support_table area renders");

        let stub_w = fonts.measure_shaped("Signups", TEST_FONT, 11.0).unwrap();
        let (canvas_w, _) = canvas_dims(&svg);
        let plot_x = plot_group_x(&svg);
        assert_eq!(canvas_w, plot_x + 600.0 + (6.0 + stub_w).ceil() + 16.0);
        // Untitled: production has no title element to hang `title.offset`
        // on, so it reserves the strip in `padding.top` instead
        // (`apply_chart_support_table_post_pass`'s `padding.top += strip_h` arm)
        // — pad_t(16) + strip_h(8 + 18 + 4) + the strip's own ink extent
        // (13 + round(0.8*11) - 3).
        assert_eq!(
            group_y(&svg),
            16.0 + 30.0 + 19.0,
            "untitled composed top = pad_t + strip_h + ink extent: {svg}"
        );
    }

    /// The composed `position: top` strip on the fit arm (`weekly_line`'s
    /// shape): right value axis, no legend.
    fn make_composed_strip_payload(titled: bool) -> serde_json::Value {
        use serde_json::json;
        let mut p = make_line_payload(true, false);
        p["resolved"]["support_table"] = json!({
            "entries": [{"source": "signups", "format": ",d", "label": "Signups"}]
        });
        p["resolved"]["effective_support_table_style"] = support_table_style_json();
        if titled {
            p["resolved"]["title"] = json!("Weekly Signups");
            p["resolved"]["title_style"] = json!({
                "font": {"family": TEST_FONT, "size": 18.0, "weight": "500", "color": "#494A4C"},
                "position": {"offset": 10.0}
            });
        }
        p
    }

    /// Vega's `autosize: pad` adds the plot height verbatim between two margins
    /// that are each ceil'd on their own (`ViewLayout.js:165-168`, `:198-201`;
    /// `vega_fit::pad_plot`), so a fractional plot height stays fractional in
    /// the canvas. Production's canvas for `weekly_line` at spec height 261,
    /// 261.3333 and 261.9 is 414, 414.3333 and 414.9 titled, 445, 445.3333
    /// and 445.9 untitled.
    #[test]
    fn support_table_on_the_composed_arm_keeps_a_fractional_plot_height_in_the_canvas() {
        for titled in [true, false] {
            let base_json = make_composed_strip_payload(titled);
            let h = base_json["height"].as_f64().unwrap();
            let render_h = |height: f64| -> f64 {
                let mut payload_json = base_json.clone();
                payload_json["height"] = serde_json::json!(height);
                let payload: Payload = serde_json::from_value(payload_json).unwrap();
                canvas_dims(&render(&payload, &test_fonts()).expect("renders")).1
            };
            let whole = render_h(h);
            for frac in [0.3333, 0.5, 0.9] {
                let canvas_h = render_h(h + frac);
                assert!(
                    (canvas_h - (whole + frac)).abs() < 1e-9,
                    "titled={titled}: canvas {canvas_h} against {} at plot height {}",
                    whole + frac,
                    h + frac
                );
            }
        }
    }

    /// A titled chart's strip is ink inside the ceil'd top margin
    /// (`ViewLayout.js:165-168`), so the plot origin and the canvas stay whole
    /// pixels at a fractional row pitch. An untitled chart's strip sits in
    /// `padding.top`, outside any ceil: the origin keeps the strip's fraction
    /// and the canvas carries it (`weekly_line` untitled at
    /// `row.padding.vertical` 2.1, 2.35 and 2.7: origin 138.6, 141.1 and 145.2,
    /// canvas 446.6, 449.1 and 453.2 at spec height 261).
    #[test]
    fn support_table_on_the_composed_arm_keeps_the_untitled_strip_fractional() {
        for titled in [true, false] {
            for vertical in [2.1, 2.35, 2.7] {
                let mut payload_json = make_composed_strip_payload(titled);
                payload_json["resolved"]["effective_support_table_style"]["row"]["padding"]
                    ["vertical"] = serde_json::json!(vertical);
                let h = payload_json["height"].as_f64().unwrap();
                let payload: Payload = serde_json::from_value(payload_json).unwrap();
                let svg = render(&payload, &test_fonts()).expect("renders");
                let (origin, canvas) = (group_y(&svg), canvas_dims(&svg).1);
                let label = format!("titled={titled} vertical={vertical}");
                if titled {
                    assert_eq!(origin.fract(), 0.0, "{label}: origin is whole: {svg}");
                    assert_eq!(canvas.fract(), 0.0, "{label}: canvas is whole: {svg}");
                } else {
                    assert!(origin.fract() > 1e-6, "{label}: origin {origin} is whole: {svg}");
                    let below_plot = canvas - origin - h;
                    assert!(
                        (below_plot - below_plot.round()).abs() < 1e-9,
                        "{label}: canvas {canvas} carries the origin's fraction {origin}: {svg}"
                    );
                }
            }
        }
    }

    /// The modeled rail-concat + strip sub-shape: `position: top`, left
    /// value axis, no legend. `weekly_area_stacked`'s own shape — two weeks,
    /// two series, a `$`-symbol row and a plain row.
    fn make_rail_strip_payload() -> serde_json::Value {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        // `render_fitted` (the width-correction wrapper the strip's own
        // canvas overshoot now relies on) requires both fields present.
        p["resolved"]["chart_type"] = json!("line");
        p["resolved"]["id"] = json!("rail_strip_test");
        p["resolved"]["resolved_channels"] = json!({"color": {"mode": "series", "data_field": p["resolved"]["color"]}});
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        p["resolved"]["style"]["series_label"]["dark_companion_palette"] =
            json!(["#111111", "#222222"]);
        p["resolved"]["support_table"] = json!({
            "entries": [
                {"aggregate": "sum", "source": "signups", "format": {"spec": "$,.0f", "raw": "currency_whole"}, "label": "Total $"},
                {"aggregate": "sum", "source": "signups", "format": ",d", "label": "Units"}
            ]
        });
        p["resolved"]["effective_support_table_style"] = support_table_style_json();
        p
    }

    /// A series pinned to a color outside the palette is labeled in that
    /// color's own ink (`series_label.pin_companions`), not the companion of
    /// the palette slot it does not paint.
    #[test]
    fn a_pinned_series_label_takes_its_pinned_colors_ink() {
        use serde_json::json;
        let mut p = make_rail_strip_payload();
        p["resolved"]["category_colors"] = json!([
            {"field": "series", "slots": {"A": 0, "B": 1}, "overrides": {"B": "#999999"}}
        ]);
        p["resolved"]["style"]["series_label"]["pin_companions"] = json!([["#999999", "#555555"]]);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let label_tag = |name: &str| {
            let end = svg.find(&format!(" {name}</text>")).unwrap_or_else(|| panic!("{name} label: {svg}"));
            svg[svg[..end].rfind("<text").unwrap()..end].to_string()
        };
        assert!(label_tag("A").contains("fill=\"#111111\""), "{}", label_tag("A"));
        assert!(label_tag("B").contains("fill=\"#555555\""), "{}", label_tag("B"));
    }

    /// A TITLED variant of `make_rail_strip_payload` — the shape both corpus
    /// cases (`weekly_area_stacked`, `region_bar_grouped`) actually take.
    fn make_titled_rail_strip_payload() -> serde_json::Value {
        use serde_json::json;
        let mut p = make_rail_strip_payload();
        p["resolved"]["title"] = json!("Weekly Signups");
        p["resolved"]["title_style"] = json!({
            "font": {"family": TEST_FONT, "size": 18.0, "weight": "500", "color": "#494A4C"},
            "position": {"offset": 10.0}
        });
        p
    }

    /// S1/S7: pane 0 is under `autosize: pad`, and pad's own title law
    /// pad-anchors the title at the canvas padding edge — `title.offset`
    /// only moves the PLOT down (S3), never the title itself. Regression
    /// for a bug where `title_pos.1` didn't subtract the strip's ink extent
    /// (`ink_ext`): the title landed 37px low, exactly `ink_ext` for this
    /// n=2-row fixture — 86% of `weekly_area_stacked`'s residual diff_frac
    /// before the fix. Mutation counterfactual: reverting `title_pos` to
    /// the non-strip closed form (`-top_ink - ti.ink_h - offset`, no
    /// pad-anchor) fails this.
    #[test]
    fn support_table_on_the_rail_concat_pad_anchors_the_title() {
        let payload_json = make_titled_rail_strip_payload();
        let pad_t = payload_json["padding"]["top"].as_f64().unwrap();
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("titled support_table rail concat renders");
        let pos = svg.find(">Weekly Signups<").expect("title text present");
        let tag_start = svg[..pos].rfind("<text").unwrap();
        let y: f64 = svg[tag_start..pos]
            .split("y=\"")
            .nth(1)
            .unwrap()
            .split('"')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        let expected_y = pad_t + measure::baseline_shift("top", 18.0);
        assert_eq!(
            y, expected_y,
            "the title sits at the canvas padding edge, not `ink_ext` below it: {svg}"
        );
    }

    /// S7/S3 pinned exactly (a loose `>` bound survives reverting `plot_h` to
    /// the chrome-subtracted formula whenever the strip's OWN chrome already
    /// exceeds the legacy splice's growth — mutation M3 in review round 1 —
    /// and separately survives reverting `strip_title_offset` to `ti.offset`,
    /// since that only shrinks `top_margin` — mutation M4). `canvas_h`/
    /// `group_y` are exact functions of `plot_h` (verbatim `payload.height`)
    /// and `title.offset = theme_offset + support_table_title_offset_
    /// correction(title_fs)`, so both mutants move these pinned numbers and
    /// neither survives.
    #[test]
    fn support_table_on_the_rail_concat_pins_exact_canvas_and_group_y_titled() {
        let payload: Payload = serde_json::from_value(make_titled_rail_strip_payload()).unwrap();
        let svg = render(&payload, &test_fonts()).expect("titled support_table rail concat renders");
        // The strip's own overhang now grows the canvas honestly past the
        // declared 600 (see `render_vertical`'s `strip_overhang` doc comment
        // on the rail-concat arm) rather than competing for `plot_w` —
        // `render_fitted`, not bare `render`, is what corrects it back
        // toward the declared width in real use (`_grows_the_canvas_past_
        // the_slot` covers that half; this test's own job — S7/S3's exact
        // `canvas_h`/`group_y` — is unaffected by the width law).
        assert_eq!(canvas_dims(&svg), (622.0, 545.0), "{svg}");
        assert_eq!(group_y(&svg), 92.0, "{svg}");
    }

    /// The untitled sibling (S3's `padding.top` branch, not `title.offset`):
    /// `composed_pad_t` gains `strip_h` directly when there is no title
    /// element to hang the offset on. Kills mutation M5 (dropping
    /// `untitled_strip_h`).
    #[test]
    fn support_table_on_the_rail_concat_pins_exact_canvas_and_group_y_untitled() {
        let payload: Payload = serde_json::from_value(make_rail_strip_payload()).unwrap();
        let svg = render(&payload, &test_fonts()).expect("untitled support_table rail concat renders");
        // See the titled sibling's own comment on the width half.
        assert_eq!(canvas_dims(&svg), (622.0, 510.0), "{svg}");
        assert_eq!(group_y(&svg), 57.0, "{svg}");
    }

    /// A width correction measures its overhead off a first render wearing the
    /// style production resolved at the slot width (`slot_resolved`); the
    /// corrected re-render wears the payload's own resolve. A slot resolve
    /// that hides the rail (the tiny tier) measures a different overhead.
    #[test]
    fn the_width_correction_measures_its_overhead_off_the_slot_widths_resolve() {
        let _en = crate::time_locale::en_us_scope();
        let mut p = make_rail_strip_payload();
        p["width"] = serde_json::json!(300.0);
        let fonts = test_fonts();
        let own: Payload = serde::Deserialize::deserialize(&p).unwrap();
        let (_, own_corrected) = crate::render_width_fitted(&own, &fonts).expect("renders");
        let mut slot = p["resolved"].clone();
        slot["style"]["endpoint_labels"]["visible"] = serde_json::json!(false);
        p["resolved"]["slot_resolved"] = slot;
        let with_slot: Payload = serde_json::from_value(p).unwrap();
        let (_, slot_corrected) = crate::render_width_fitted(&with_slot, &fonts).expect("renders");
        let (own_w, slot_w) = (own_corrected.unwrap().width, slot_corrected.clone().unwrap().width);
        assert_ne!(slot_w, own_w);
        assert!(slot_corrected.unwrap().resolved.get("slot_resolved").is_none());
    }

    /// Vega's `autosize: pad` adds the plot height verbatim between two margins
    /// that are each ceil'd on their own (`ViewLayout.js:165-168`, `:198-201`;
    /// `vega_fit::pad_plot`), so a fractional plot height stays fractional in
    /// the canvas. The production canvas at spec height 261, 261.3333 and
    /// 261.9 is 438, 438.3333 and 438.9 (`weekly_line_by_category`), and 402,
    /// 402.3333 and 402.9 (`weekly_area_stacked`), titled or not.
    #[test]
    fn support_table_on_the_rail_concat_keeps_a_fractional_plot_height_in_the_canvas() {
        use serde_json::json;
        for titled in [true, false] {
            let base_json =
                if titled { make_titled_rail_strip_payload() } else { make_rail_strip_payload() };
            let h = base_json["height"].as_f64().unwrap();
            let render_h = |height: f64| -> f64 {
                let mut payload_json = base_json.clone();
                payload_json["height"] = json!(height);
                let payload: Payload = serde_json::from_value(payload_json).unwrap();
                canvas_dims(&render(&payload, &test_fonts()).expect("renders")).1
            };
            let whole = render_h(h);
            for frac in [0.3333, 0.5, 0.9] {
                let canvas_h = render_h(h + frac);
                assert!(
                    (canvas_h - (whole + frac)).abs() < 1e-9,
                    "titled={titled}: canvas {canvas_h} against {} at plot height {}",
                    whole + frac,
                    h + frac
                );
            }
        }
    }

    /// A fractional row pitch leaves a fraction in the strip's ink, but the
    /// margin above the plot is one ceil'd overhang (`ViewLayout.js:165-168`):
    /// production's plot origin and canvas stay whole pixels at a whole plot
    /// height (`row.padding.vertical` 2.1 on `weekly_line_by_category`,
    /// `weekly_area_stacked`, titled or not).
    #[test]
    fn support_table_on_the_rail_concat_origin_is_the_ceiled_top_margin() {
        use serde_json::json;
        for titled in [true, false] {
            let mut payload_json =
                if titled { make_titled_rail_strip_payload() } else { make_rail_strip_payload() };
            payload_json["resolved"]["effective_support_table_style"]["row"]["padding"]
                ["vertical"] = json!(2.1);
            let payload: Payload = serde_json::from_value(payload_json).unwrap();
            let svg = render(&payload, &test_fonts()).expect("renders");
            assert_eq!(
                group_y(&svg).fract(),
                0.0,
                "titled={titled}: origin is a whole pixel: {svg}"
            );
            assert_eq!(canvas_dims(&svg).1.fract(), 0.0, "titled={titled}: canvas is whole: {svg}");
        }
    }

    /// `playground/support-table-attached/weekly_line_by_category`'s shape:
    /// a non-stacked, colored line's `per_series:` entry expands to one
    /// strip row per series, in `_series_order_strip`'s "Line" order —
    /// ascending by each series' own y at the chart's last x
    /// (support_table_attachment.py:2693-2718). `make_rail_strip_payload`'s
    /// underlying fixture (`make_multi_series_line_payload_with_bottom_
    /// legend`) has A=150, B=60 at the last week (2026-01-08), so B (lower)
    /// sits closer to the plot than A — series_order = [B, A], reversed
    /// from `palette_domain`'s own alphabetical [A, B]. Label ink still
    /// follows `palette_domain` (the SAME lookup the endpoint rail's own
    /// label ink uses), so it does NOT reverse with the row order. Mutation
    /// counterfactual: reverting `per_series_order` to `palette_domain`
    /// (alphabetical) swaps the row order and fails the first assertion.
    #[test]
    fn support_table_on_the_rail_concat_expands_a_per_series_entry() {
        use serde_json::json;
        let mut p = make_rail_strip_payload();
        // `axis_json`'s test-fixture default is `is_quantitative: true`
        // regardless of the x field's real shape — this fixture's x is the
        // ISO date/week strings `last_y_ascending_order` is sound for, not
        // a genuinely numeric x, so mark it the way a real date x resolves.
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = json!(false);
        p["resolved"]["support_table"] = json!({"entries": [{"per_series": "signups", "format": ",d"}]});
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("per_series rail concat renders");

        // The rail ALSO paints an "A"/"B" label of its own, earlier in the
        // markup than the strip — `rfind` picks the strip's own stub, the
        // last (and only other) occurrence.
        let stub = |label: &str| -> (usize, String) {
            let pos = svg.rfind(&format!(">{label}<")).unwrap_or_else(|| panic!("{label} stub missing: {svg}"));
            let tag = &svg[svg[..pos].rfind("<text").unwrap()..pos];
            let fill = tag.split("fill=\"").nth(1).unwrap().split('"').next().unwrap().to_string();
            (pos, fill)
        };
        let (b_pos, b_fill) = stub("B");
        let (a_pos, a_fill) = stub("A");
        assert!(b_pos < a_pos, "B (lower last-x y) sits closer to the plot than A: {svg}");
        assert_eq!(a_fill, "#111111", "A keeps palette_domain's own (alphabetical) companion ink");
        assert_eq!(b_fill, "#222222");
        assert!(svg.contains(">100<") && svg.contains(">150<"), "A's cells (week1=100, week2=150): {svg}");
        assert!(svg.contains(">40<") && svg.contains(">60<"), "B's cells (week1=40, week2=60): {svg}");
    }

    /// S7: on the modeled rail-concat sub-shape the strip splices into pane
    /// 0's own `autosize: pad`, so the canvas is left to overshoot the slot
    /// — never fit back into `payload.height` the way every other rail
    /// concat does, and by MORE than the legacy splice's own modest
    /// `+ strip_height` growth (S7 also carries the doubled title-offset/
    /// ink-extent gap, S3). Mutation counterfactual: reverting `plot_h` to
    /// the chrome-subtracted formula collapses the canvas back to (at most)
    /// the legacy splice's own bound and this assertion fails.
    #[test]
    fn support_table_on_the_rail_concat_grows_the_canvas_past_the_slot() {
        let payload_json = make_rail_strip_payload();
        let payload_h = payload_json["height"].as_f64().unwrap();
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("support_table rail concat renders");
        let (_, canvas_h) = canvas_dims(&svg);
        assert!(
            canvas_h > legacy_top_splice_canvas_h(payload_h),
            "the canvas overshoots the slot by MORE than the legacy splice's own \
             `+ strip_height` growth: canvas_h={canvas_h} legacy_bound={}\n{svg}",
            legacy_top_splice_canvas_h(payload_h)
        );
    }

    /// S9's left-axis carve-out: a trimmed (`$,.0f`) symbol row cannot anchor
    /// on a left-oriented value axis (`hang = "right"` in production — the
    /// affix would be absent from every non-anchor cell), so every cell
    /// keeps its own `$` — unlike the composed (non-concat) arm's
    /// `weekly_line`, which anchors on its RIGHT axis. The plain (no
    /// symbol) row still declares nothing either way (nothing to hang).
    #[test]
    fn support_table_on_the_rail_concat_never_anchors_a_trimmed_symbol_row() {
        let payload_json = make_rail_strip_payload();
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("support_table rail concat renders");
        // aggregate: sum over both series — week 1: 100+40=140, week 2: 150+60=210.
        assert!(svg.contains(">$140<"), "the would-be anchor cell keeps its own $: {svg}");
        assert!(svg.contains(">$210<"), "every OTHER cell keeps its $ too — no anchor: {svg}");
    }

    /// Pane 0's own plot width — the line's own path spans exactly
    /// `[0, plot_w]`.
    fn rail_strip_plot_w(svg: &str) -> f64 {
        let pos = svg.find("<path d=\"M0,").expect("a line path is drawn");
        let d = &svg[pos..].split('"').nth(1).unwrap();
        d.split('L').nth(1).unwrap().split(',').next().unwrap().parse().unwrap()
    }

    /// Production's `_apply_support_table_strip` (`vega_lite.py:624`) runs
    /// BEFORE `_correct_concat_overshoot` (`converters/chart.py:904`) —
    /// the corrector operates on a spec that already carries the strip's
    /// layers, confirmed by instrumenting both functions on a real board
    /// render (11 layers on pane 0 for `weekly_line_by_category`: 3 mark +
    /// 8 strip). Ablating that probed spec's own layer list and re-probing
    /// `vegalite_to_scenegraph` shows the strip's CELLS (data-driven `x`,
    /// growing right) contribute exactly 0 to the probe on both corpus
    /// rows, while its ROW LABELS (static `x`, in the left gutter)
    /// contribute the whole 31px `weekly_line_by_category` overhang — the
    /// row-label term is already modeled separately, by this function's
    /// own `y_extent` wide-stub widening, which is why `plot_w`'s pane-0
    /// negotiation here is governed by the mark's right ink alone,
    /// regardless of the strip's own CELL reach — `rail_strip_plot_w` is
    /// identical whether the wide currency cell is present or not, as long
    /// as the mark's own ink is unchanged, on these two rows specifically
    /// (validated where the cell's overhang is small; not shown general —
    /// see the doc's own refusal on this point). What the strip's cell
    /// overhang DOES do is move the rail pane (Vega's real `gridLayout`
    /// positions it off pane 0's own rendered bounds, not its declared
    /// width) and so the canvas, honestly, past pane 0's own settled width
    /// in the uncorrected render; `render_fitted` then shrinks pane 0 back
    /// toward the declared width and re-adds the (unchanged) overhang.
    /// Mutation counterfactual: reinstating `composed_right_overhang` in
    /// `right_ink` narrows `plot_w` on the strip-present case (the pin below
    /// is exactly what catches that).
    #[test]
    fn support_table_on_the_rail_concat_never_competes_for_plot_w_but_grows_the_canvas() {
        let _en = crate::time_locale::en_us_scope();
        let payload: Payload = serde_json::from_value(make_rail_strip_payload()).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let plot_w_with_strip = rail_strip_plot_w(&svg);

        let mut thick = make_rail_strip_payload();
        thick["resolved"]["style"]["line_mark"]["stroke"]["width"] = serde_json::json!(60.0);
        let payload2: Payload = serde_json::from_value(thick).unwrap();
        let svg2 = render(&payload2, &test_fonts()).expect("renders");
        assert_eq!(rail_strip_plot_w(&svg2), 427.0, "a thick stroke's own mark reach wins: {svg2}");
        // Pins the real value: an `assert_ne!` alone cannot tell the overhang's
        // presence from its absence.
        assert_eq!(
            plot_w_with_strip, 483.0,
            "the strip's own cell reach must not narrow plot_w: {svg}"
        );

        let canvas_width_attr = |svg: &str| -> f64 {
            svg.split("width=\"")
                .nth(1)
                .unwrap()
                .split('"')
                .next()
                .unwrap()
                .parse::<f64>()
                .unwrap()
        };
        // Bare `render()` carries the strip's overhang BEFORE `render_
        // fitted`'s overshoot correction — that raw number is what proves
        // the strip really does grow the canvas past the declared slot.
        let canvas_uncorrected = canvas_width_attr(&svg);
        assert!(
            canvas_uncorrected > 600.0,
            "the strip's own overhang widens the uncorrected canvas past the declared width: \
             {canvas_uncorrected}"
        );

        // `render_fitted` then shrinks pane 0 back toward 600 and re-folds
        // the (unchanged) overhang into `ink_x2` before ceiling the whole
        // sum — `ink_x2.ceil()` can still land the corrected total EXACTLY
        // on the declared width for a specific overhang value (ceiling an
        // already-integral sum is a no-op), so a strict `> 600.0` here
        // would be fragile to that boundary case. What must hold is the
        // weaker, still meaningful bound: the correction never shrinks the
        // canvas BELOW the declared slot, i.e., it never clips the strip's
        // own content.
        let canvas_with_strip = canvas_width_attr(
            &crate::render_fitted(&payload, &test_fonts()).expect("renders").svg,
        );
        assert!(
            canvas_with_strip >= 600.0,
            "render_fitted's correction must not shrink the canvas below the declared width: \
             {canvas_with_strip}"
        );
    }

    /// S6/S8, the left-hang half: a stub label wider than the axis-only
    /// gutter (`y_label_pad + max_y_label_w`) widens `y_extent` itself —
    /// `left_margin` and the plot group's own x both move with it, exactly
    /// the way the strip's right-reaching cells widen `right_ink` above.
    /// `region_bar_grouped`'s "Top cat" stub is the corpus case; here the
    /// label is forced past `LABEL_STUB_LIMIT_PX` (80px) so the widened
    /// gutter is deterministic regardless of font metrics. Mutation
    /// counterfactual: dropping the `y_extent.max(..)` term collapses
    /// `left_margin` back to the axis-only `y_extent_ink` and both
    /// assertions fail.
    #[test]
    fn support_table_on_the_rail_concat_widens_y_extent_for_a_wide_stub() {
        let mut narrow = make_rail_strip_payload();
        narrow["resolved"]["style"]["axis_y"]["ticks"]["visible"] = serde_json::json!(true);
        narrow["resolved"]["support_table"]["entries"][0]["label"] = serde_json::json!("$");
        narrow["resolved"]["support_table"]["entries"][1]["label"] = serde_json::json!("N");
        let payload: Payload = serde_json::from_value(narrow).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let narrow_group_x = plot_group_x(&svg);

        let mut wide = make_rail_strip_payload();
        wide["resolved"]["style"]["axis_y"]["ticks"]["visible"] = serde_json::json!(true);
        wide["resolved"]["support_table"]["entries"][0]["label"] =
            serde_json::json!("Average Order Revenue Per Signup");
        let payload2: Payload = serde_json::from_value(wide).unwrap();
        let svg2 = render(&payload2, &test_fonts()).expect("renders");
        let wide_group_x = plot_group_x(&svg2);

        assert_eq!(narrow_group_x, 47.0, "a narrow stub stays on the axis-only gutter: {svg}");
        assert_eq!(
            wide_group_x, 97.0,
            "a stub past the axis gutter widens left_margin by the capped label width: {svg2}"
        );
    }

    /// `position: bottom` keeps the pre-existing legacy splice exactly as
    /// before this arm — the `top` pair below now refuse instead (see
    /// `support_table_right_axis_rail_keeps_the_legacy_splice`'s own doc
    /// comment): this bound is kept only as the bottom-splice sanity check.
    fn legacy_top_splice_canvas_h(payload_h: f64) -> f64 {
        // padding_top(8) + row_h(14 + 2*2=18)*2 rows + padding_bottom(4) = 48.
        payload_h + 48.0
    }

    /// A right value axis with an active rail mirrors the rail-concat arm: the
    /// stub column hangs past the axis at the spec width the concat never
    /// shrank, and pane 0's right bound reaches it.
    #[test]
    fn support_table_right_axis_rail_renders_on_the_rail_concat() {
        let mut payload_json = make_rail_strip_payload();
        payload_json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        render(&payload, &test_fonts()).expect("a right-axis rail with a strip renders");
    }

    /// A legend kept beside an active rail sits inside pane 0 beside the strip.
    #[test]
    fn support_table_legend_kept_alongside_rail_renders_on_the_rail_concat() {
        let mut payload_json = make_rail_strip_payload();
        payload_json["resolved"]["legend"]["visible"] = serde_json::json!(true);
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        render(&payload, &test_fonts()).expect("a kept legend beside a rail and a strip renders");
    }

    /// Production's per_series render-time legend suppression
    /// (`_suppress_series_legend`, `support_table_attachment.py:4408-4446`)
    /// is chart-type-agnostic — it runs on ANY chart's already-built spec,
    /// not just bar's. `legend_active` must re-derive `!has_per_series` the
    /// same way `bar.rs`'s `legend_shown` does. Untitled + rail disabled. Mutation counterfactual: dropping `&&
    /// !has_per_series` from `legend_active` draws the legend production
    /// removes and fails this test.
    #[test]
    fn support_table_per_series_suppresses_a_kept_legend() {
        use serde_json::json;
        let mut p = make_rail_strip_payload();
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(false);
        p["resolved"]["legend"]["visible"] = json!(true);
        p["resolved"]["support_table"] = json!({"entries": [{"per_series": "signups", "format": ",d"}]});
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("untitled per_series with a kept legend renders");
        // `emit_vertical_legend`'s circle symbol (`<path transform=
        // "translate(...)">`) is a mark tag no other line emission uses —
        // its absence is a clean signal the legend column was never drawn.
        assert!(
            !svg.contains("<path transform=\"translate("),
            "production nulls the legend for a per_series entry: {svg}"
        );
    }

    /// `last_y_ascending_order`'s "last x" compares raw values like Python's
    /// `max`: a quantitative x of 1/9/12 ends at 12, not at the lexical "9", so
    /// the series sort by their value at 12.
    #[test]
    fn support_table_per_series_orders_a_quantitative_x_by_the_numeric_last_value() {
        let row = |x: f64, series: &str, y: f64| {
            serde_json::json!({"week": x, "series": series, "signups": y}).as_object().unwrap().clone()
        };
        let rows = vec![
            row(1.0, "A", 100.0), row(9.0, "A", 60.0), row(12.0, "A", 160.0),
            row(1.0, "B", 40.0), row(9.0, "B", 150.0), row(12.0, "B", 130.0),
        ];
        let distinct = vec!["A".to_string(), "B".to_string()];
        assert_eq!(super::last_y_ascending_order(&rows, "week", "signups", "series", &distinct).unwrap(), ["B", "A"]);
        // A null x is skipped, not a switch to a text max; a mix of numbers and text refuses.
        let mut with_null = rows.clone();
        with_null.push(serde_json::json!({"week": null, "series": "A", "signups": 1.0}).as_object().unwrap().clone());
        assert_eq!(super::last_y_ascending_order(&with_null, "week", "signups", "series", &distinct).unwrap(), ["B", "A"]);
        // 12 and 12.0 are the same last x.
        let mut mixed_repr = rows.clone();
        mixed_repr.push(serde_json::json!({"week": 12, "series": "B", "signups": 500.0}).as_object().unwrap().clone());
        assert_eq!(super::last_y_ascending_order(&mixed_repr, "week", "signups", "series", &distinct).unwrap(), ["A", "B"]);
        let mut mixed = rows;
        mixed.push(serde_json::json!({"week": "next", "series": "A", "signups": 1.0}).as_object().unwrap().clone());
        assert!(super::last_y_ascending_order(&mixed, "week", "signups", "series", &distinct).unwrap_err().contains("no order"));
    }

    /// `curve: step` over a calendar-bucketed x resolves the x ordinal
    /// (`type_inference.py:292-294`), and production gap-fills every bucket
    /// before banding: a missing week is a band with a null row, which breaks
    /// the connected path, and every riser stands on a band edge.
    #[test]
    fn curve_step_on_a_calendar_bucketed_x_bands_every_bucket_and_breaks_at_a_gap() {
        let mut payload_json = make_line_payload(false, false);
        payload_json["rows"] = serde_json::json!([
            {"week": "2026-01-01", "signups": 100.0},
            {"week": "2026-01-08", "signups": 150.0},
            {"week": "2026-01-22", "signups": 130.0},
        ]);
        payload_json["resolved"]["style"]["line_mark"]["curve"] = serde_json::json!("step");
        payload_json["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        let svg = render(&serde_json::from_value(payload_json).unwrap(), &test_fonts()).expect("renders");
        let d = main_line_d(&svg);
        assert_eq!(d.matches('M').count(), 2, "the empty week breaks the path: {d}");
        let xs = distinct_xs(&d);
        let right = *xs.last().unwrap();
        for x in &xs {
            let bands = x / right * 4.0;
            assert!((bands - bands.round()).abs() < 1e-3, "off a band edge of four: {xs:?}");
        }
    }

    /// The root dash legend truncates a label past Vega's `labelLimit` (160) with an
    /// ellipsis rather than widening the canvas.
    #[test]
    fn the_root_dash_legend_truncates_a_long_series_label() {
        use serde_json::json;
        let long = "a-very-long-series-name-that-runs-well-past-the-label-limit";
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        for row in p["rows"].as_array_mut().unwrap() {
            if row["series"] == "A" {
                row["series"] = json!(long);
            }
        }
        p["resolved"]["style"]["dashes"] = json!([[12.0, 16.0], [2.0, 6.0]]);
        p["resolved"]["legend"]["visible"] = json!(true);
        p["resolved"]["legend"]["position"]["edge"] = json!("right");
        p["width"] = json!(1400.0);
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains("…</text>"), "{svg}");
        // The unbreakable name cannot wrap beside its value, so the rail drops the value and paints it whole.
        assert_eq!(svg.matches(&format!("{long}</text>")).count(), 1, "only the rail label stays whole: {svg}");
    }

    /// An endpoint rail beside a right y axis rides the rail concat, whose
    /// root stroke-sample dash legend sits past the rail pane.
    #[test]
    fn a_dashed_legend_beside_a_rail_and_a_right_axis_renders() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["style"]["dashes"] = json!([[12.0, 16.0], [2.0, 6.0]]);
        p["resolved"]["legend"]["visible"] = json!(true);
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        p["resolved"]["style"]["axis_y"]["position"] = json!("right");
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains("stroke-dasharray=\"12,16\""), "{svg}");
    }

    /// A genuinely QUANTITATIVE x (`axis_x.is_quantitative: true`, numeric
    /// values that never detect a calendar grain) resolves `x_type_from_data
    /// == "quantitative"` in production regardless of `curve: step` —
    /// `step_band.is_band_step` only fires for `("nominal", "ordinal")`, so
    /// this stays d3's plain `curveStep` passthrough: one riser per segment,
    /// at the segment's own MIDPOINT (not its end), never band-doubled.
    #[test]
    fn curve_step_on_a_quantitative_x_draws_d3_s_step_path_not_band_doubled() {
        let rows = serde_json::json!([
            {"day": 1.0, "signups": 100.0},
            {"day": 2.0, "signups": 150.0},
            {"day": 3.0, "signups": 130.0},
        ]);
        let mut payload_json = make_line_payload(false, false);
        payload_json["resolved"]["x"] = serde_json::json!("day");
        payload_json["rows"] = rows;
        payload_json["resolved"]["style"]["line_mark"]["curve"] = serde_json::json!("step");
        payload_json["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(true);
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("quantitative curve: step renders");
        let main = main_line_d(&svg);
        // Three points -> two segments -> two risers, each a pair of lineTo
        // (flat run to the midpoint, then the vertical jump) plus the final
        // lineEnd extra reaching the last point — 5 `L`s total, never a `C`,
        // and never the far larger vertex count row-doubling would produce.
        assert_eq!(main.matches('L').count(), 5, "two centered step risers: {main}");
        assert!(!main.contains('C'), "a step curve is polylines, not cubics: {main}");
    }

    /// `step-before`/`step-after` on a temporal x are likewise plain d3
    /// passthroughs (never band-doubled) — each draws one riser per segment
    /// with no `lineEnd` extra (the pen already sits at the segment's own
    /// end after `point()`), so exactly 4 `L`s over three points.
    #[test]
    fn curve_step_before_and_after_on_a_temporal_x_render_without_the_lineend_extra() {
        for curve in ["step-before", "step-after"] {
            let mut payload_json = make_line_payload(false, false);
            payload_json["resolved"]["style"]["line_mark"]["curve"] = serde_json::json!(curve);
            // A temporal x resolves `is_quantitative: false`, the value
            // production emits — the literal-`step` refusal must not fire on
            // its siblings even there.
            payload_json["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
            let payload: Payload = serde_json::from_value(payload_json).unwrap();
            let svg = render(&payload, &test_fonts()).expect("renders");
            let main = main_line_d(&svg);
            assert_eq!(main.matches('L').count(), 4, "curve={curve}: {main}");
        }
    }

    /// Area's curve lives on style.area_mark (production gates cadence on
    /// it, emitters/area.py:220); `week` is temporal here too, so this is
    /// the same plain-passthrough shape as the line tests above, drawn over
    /// BOTH the top and the (curved, but flat) bottom edge.
    #[test]
    fn area_curve_step_after_on_a_temporal_x_renders_over_both_edges() {
        let mut payload_json = make_line_payload(false, false);
        payload_json["resolved"]["style"]["area_mark"] =
            serde_json::json!({"opacity": 0.16, "backdrop": false, "curve": "step-after"});
        payload_json["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let svg = render_inner(&payload, &test_fonts(), true).expect("area step-after renders");
        // The colored-area fill path is the one whose fill isn't "none".
        let area_d = svg
            .match_indices("<path d=\"M")
            .map(|(i, _)| svg[i..].split("/>").next().unwrap())
            .find(|tag| !tag.contains("fill=\"none\"") && tag.contains("fill-opacity"))
            .expect("colored area path present")
            .split("d=\"")
            .nth(1)
            .unwrap()
            .split('"')
            .next()
            .unwrap()
            .to_string();
        assert!(area_d.ends_with('Z'), "area path closes: {area_d}");
        // Top edge: 3 points, `moveTo` + 2 risers = 4 `L`s. Bottom edge: the
        // area generator's OWN first bottom point is a `lineTo` (continuing
        // the top edge, per `append_edge`'s `Edge::AreaBottom` branch), plus
        // its own 2 risers = 5 `L`s. 4 + 5 = 9.
        assert_eq!(area_d.matches('L').count(), 9, "two risers over each of two edges: {area_d}");
    }

    /// Band mode is the LITERAL `step` only (`step_band.is_band_step`):
    /// `step-before`/`step-after` on the same nominal x stay plain d3
    /// passthroughs — three vertices, never four band edges.
    #[test]
    fn step_before_and_after_on_a_nominal_x_never_band_double() {
        for curve in ["step-before", "step-after"] {
            let mut json = make_band_step_line_payload(None);
            json["resolved"]["style"]["line_mark"]["curve"] = serde_json::json!(curve);
            let payload: Payload = serde_json::from_value(json).unwrap();
            let svg = render(&payload, &test_fonts()).expect("renders");
            assert_eq!(distinct_xs(&main_line_d(&svg)).len(), 3, "curve={curve}: {}", main_line_d(&svg));
        }
    }

    /// A band-step payload: `cat` is a plain string domain (never detected
    /// temporal), three distinct non-monotonic values so a transposed riser
    /// would be visually obvious. `connect` is line-only (area has no such
    /// field — `_apply_area_step_band` always hardcodes `connect=True`).
    fn make_band_step_line_payload(connect: Option<bool>) -> serde_json::Value {
        let rows = serde_json::json!([
            {"cat": "c0", "val": 10.0},
            {"cat": "c1", "val": 30.0},
            {"cat": "c2", "val": 20.0},
        ]);
        let mut line_mark = serde_json::json!({
            "stroke": {"width": 4.0, "cap": "round", "join": "round"},
            "halo_multiplier": 0.0,
            "curve": "step",
            "disconnected_cap": "butt",
            "disconnected_width": 0.9,
        });
        if let Some(c) = connect {
            line_mark["connect"] = serde_json::json!(c);
        }
        // A real nominal x's resolved axis carries is_quantitative: false
        // (verified against a dumped production payload) — the shared
        // `axis_json()` default of `true` is a stand-in for the common
        // numeric-y case and would wrongly gate band-step off.
        let mut axis_x_json = axis_json(false, None);
        axis_x_json["is_quantitative"] = serde_json::json!(false);
        let resolved = serde_json::json!({
            "id": "c", "title": null, "title_style": null, "background": "#ffffff", "canvas": "#ffffff",
            "x": "cat", "y": "val",
            "color": null, "stack": "none",
            "palette": ["#3164a3"],
            "multiples": null, "field_labels": {}, "wide_measures": [], "layers": [],
            "legend": {
                "visible": false, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
                "title": {"font": {"case": "title", "family": TEST_FONT}},
                "label": {"font": {"family": TEST_FONT, "size": 11.0}}
            },
            "style": {
                "axis_x": axis_x_json,
                "axis_y": value_axis_json(false, None),
                "line_mark": line_mark,
                "series_label": {"font_family": TEST_FONT, "font_size": 11.0, "font_weight": "400", "dark_companion_palette": [], "pin_companions": [], "gap_px": 8.0},
                "endpoint_labels": {"visible": false, "label_offset": 6.0},
                "single_series_fill": "#3164a3",
                "label_usable_ratio": 0.8
            }
        });
        serde_json::json!({
            "resolved": resolved,
            "rows": rows,
            "width": 600.0,
            "height": 400.0,
            "background": "#ffffff",
            "fonts_dir": null,
            "padding": {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0}
        })
    }

    /// Unique x values found in a path `d`, in first-seen order — used to
    /// read back the band edges a doubled-row step-after path visits without
    /// having to predict the plot's margins independently.
    fn distinct_xs(d: &str) -> Vec<f64> {
        let mut xs: Vec<f64> = Vec::new();
        for pair in d.split(['M', 'L']).filter(|s| !s.is_empty()) {
            let x: f64 = pair.split(',').next().unwrap().parse().unwrap();
            if xs.last().is_none_or(|&last| (last - x).abs() > 1e-6) {
                xs.push(x);
            }
        }
        xs
    }

    /// A numeric attribute's value off a tag string (`text_tag`'s output).
    fn tag_attr(tag: &str, name: &str) -> f64 {
        tag.split(&format!("{name}=\""))
            .nth(1)
            .unwrap()
            .split('"')
            .next()
            .unwrap()
            .parse()
            .unwrap()
    }

    /// Reference: `fixtures/step-band-curve-connected`'s own SVG draws its
    /// x-axis labels at `(i + 0.5) * plot_w / n` inside a group carrying the
    /// Vega axis's own +0.5 crisp translate ("c00" at local 24.05, absolute
    /// 24.55 = (0.5) * 49.1) — a genuine BAND scale, not the point scale
    /// every other categorical (non-step) x axis takes: VL auto-types the x
    /// channel to `band` once an `xOffset` channel (the doubled rows) rides
    /// along it, so `bandwidth('x')` resolves for `step_band.py`'s own
    /// offset-range expressions.
    #[test]
    fn band_step_x_labels_sit_at_band_centers_not_point_scale_edges() {
        let payload: Payload =
            serde_json::from_value(make_band_step_line_payload(None)).unwrap();
        let svg = render(&payload, &test_fonts()).expect("band step renders");
        let xs: Vec<f64> = ["c0", "c1", "c2"].iter().map(|l| tag_attr(&text_tag(&svg, l), "x")).collect();
        // Evenly spaced (a band division), and the FIRST label sits well
        // inside the plot — never at x=0, which is what a point scale (one
        // vertex per category, first at the range start) would draw.
        let step = xs[1] - xs[0];
        assert!(step > 0.0, "labels run left to right: {xs:?}");
        assert!((xs[2] - xs[1] - step).abs() < 1e-6, "evenly spaced band centers: {xs:?}");
        assert!(xs[0] > step * 0.25, "first label centers inside its band, not at the range start: {xs:?}");
    }

    /// A visible x grid snaps to `floor(band_center) + 0.5` — the SAME law
    /// `heatmap.rs`'s own band-scale category axis already carries
    /// (`plot_left + center.floor() + AXIS_TRANSLATE`, `heatmap.rs:419`), not
    /// the plain `round(pos) + 0.5` this file's `rule_at` uses for every
    /// other (point-scale) categorical/temporal x axis. (`bar.rs`'s
    /// `horizontal_grid_rules_snap_to_a_whole_pixel_labels_do_not` pins the
    /// same `floor(label) + 0.5` FORM, but on a quantitative measure axis
    /// driven by authored `tick_values` — a different scale, not this one.)
    #[test]
    fn band_step_x_gridlines_snap_to_floor_band_center() {
        let mut json = make_band_step_line_payload(None);
        json["resolved"]["style"]["axis_x"]["grid"] =
            serde_json::json!({"visible": true, "threshold": null, "width": 1.0, "color": "#EDEFF2", "opacity": 1.0});
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let label_xs: Vec<f64> =
            ["c0", "c1", "c2"].iter().map(|l| tag_attr(&text_tag(&svg, l), "x")).collect();
        // Gridlines run from the plot TOP (y1="0.5"); ticks share the same
        // default color but sit at the plot BOTTOM — filter them out.
        let grid_xs: Vec<f64> = svg
            .match_indices("y1=\"0.5\" x2=")
            .map(|(idx, _)| {
                let tag_start = svg[..idx].rfind("<line").expect("grid line");
                tag_attr(&svg[tag_start..idx], "x1")
            })
            .collect();
        assert_eq!(grid_xs.len(), 3, "one gridline per band: {svg}");
        for (gx, lx) in grid_xs.iter().zip(&label_xs) {
            assert!(
                (gx - (lx.floor() + 0.5)).abs() < 1e-6,
                "grid {gx} is not floor(label {lx}) + 0.5: {svg}"
            );
        }
    }

    /// The reference's own left margin (an autosize-fit `y_on_right` chart)
    /// comes from the point-marker halo's reach past the leftmost mark, NOT
    /// from the first axis label's own half-width — because that label sits
    /// at its band's CENTER, well inside the plot, contributing no edge
    /// overhang. A long first-category string must not widen the margin.
    #[test]
    fn band_step_on_a_right_axis_does_not_reserve_margin_for_a_wide_first_label() {
        let mut json = make_band_step_line_payload(None);
        json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
        json["resolved"]["style"]["line_mark"]["halo_multiplier"] = serde_json::json!(0.0);
        json["rows"][0]["cat"] = serde_json::json!("a very long first category label");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let group_x = plot_group_x(&svg);
        let pad_l = 16.0;
        assert!(
            group_x - pad_l < 5.0,
            "left margin should be near-zero (no point markers, no y-axis chrome on the \
             left) — a band-scale label centered well inside the plot must not widen it: \
             group_x={group_x}: {svg}"
        );
    }

    /// A connected band-step's marks run flush to the band edges (0/pw), so
    /// the mark-ink bounds anchor at the plot edge exactly like a point
    /// scale — the left margin is `ceil(point_reach)`, no inset subtracted.
    #[test]
    fn band_step_connected_mark_bounds_anchor_at_the_band_edge() {
        let mut json = make_band_step_line_payload(None);
        json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
        json["resolved"]["style"]["point_mark"] = serde_json::json!({"size": 900.0});
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let point_reach = 900.0_f64.sqrt() / 2.0; // halo_multiplier 0.0 -> bare radius
        let left_margin = plot_group_x(&svg) - 16.0;
        assert_eq!(left_margin, point_reach.ceil(), "band edge sits at 0: {svg}");
    }

    /// A disconnected band-step's first plateau starts inset from the plot
    /// edge by the theme's `disconnected_width` share of the band
    /// (`line_mark.disconnected_width`), so the mark-ink bounds must anchor
    /// there, not at the plot edge: `left_margin = ceil(point_reach −
    /// plateau_x0)`, where `plateau_x0` is the SAME local x the plateau
    /// itself draws at (`distinct_xs(main_line_d)`'s first entry) — an
    /// independent code path from the bounds union this test exercises, so
    /// this pins the two staying in sync rather than re-deriving one from
    /// the other.
    #[test]
    fn band_step_disconnected_mark_bounds_anchor_at_the_plateau_inset() {
        let mut json = make_band_step_line_payload(Some(false));
        json["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
        json["resolved"]["style"]["point_mark"] = serde_json::json!({"size": 900.0});
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let point_reach = 900.0_f64.sqrt() / 2.0; // halo_multiplier 0.0 -> bare radius
        let plateau_x0 = distinct_xs(&main_line_d(&svg))[0];
        assert!(plateau_x0 > 0.0, "plateau insets past the plot edge: {svg}");
        let left_margin = plot_group_x(&svg) - 16.0;
        assert_eq!(
            left_margin,
            (point_reach - plateau_x0).ceil(),
            "left margin should anchor at the plateau inset, not the plot edge: {svg}"
        );
    }

    /// A band scale reads `scale.padding` as the fractional inner and outer
    /// padding (`BandGeometry`), so a padded band-step line differs from the
    /// unpadded one instead of insetting its range in pixels.
    #[test]
    fn band_step_with_scale_padding_opens_gaps_between_bands() {
        let plain = render(&serde_json::from_value(make_band_step_line_payload(None)).unwrap(), &test_fonts())
            .expect("renders");
        let mut json = make_band_step_line_payload(None);
        json["resolved"]["style"]["axis_x"]["scale"] = serde_json::json!({"padding": 0.1});
        let padded = render(&serde_json::from_value(json).unwrap(), &test_fonts()).expect("renders");
        assert_ne!(plain, padded);
    }

    /// Connected band-step (the default `connect`): production's
    /// `step_band.py` doubles each of the 3 rows onto its band's left (0)
    /// and right (1) edge, forces `step-after`, and joins them into ONE
    /// continuous path — band `i`'s right edge and band `i+1`'s left edge
    /// are the SAME float, so the doubled-vertex path visits exactly N+1
    /// distinct, evenly-spaced x positions, never N*2.
    #[test]
    fn curve_step_on_a_band_x_doubles_rows_to_evenly_spaced_band_edges() {
        let payload: Payload =
            serde_json::from_value(make_band_step_line_payload(None)).unwrap();
        let svg = render(&payload, &test_fonts()).expect("band step renders");
        let xs = distinct_xs(&main_line_d(&svg));
        assert_eq!(xs.len(), 4, "3 bands -> 4 shared edges: {xs:?}");
        let bandwidth = xs[1] - xs[0];
        assert!(bandwidth > 0.0, "bands run left to right: {xs:?}");
        // A width is the difference of two printed edges and `bandwidth` of two more, so four
        // printed coordinates, each within `PATH_COORD_TOLERANCE` of its position.
        for w in xs.windows(2) {
            assert!(
                (w[1] - w[0] - bandwidth).abs() <= 4.0 * d3rs::path::PATH_COORD_TOLERANCE,
                "every band is the same width: {xs:?}"
            );
        }
    }

    /// `connect: false` insets each plateau by the theme's
    /// `style.charts.marks.line.disconnected_width` (0.9 — 5% inset per side) and draws
    /// one subpath per band instead of one continuous chain — production's
    /// `detail` channel split, mirrored here as separate `M` subpaths within
    /// the same `<path>` (equivalent ink; see `step_band.py`'s docstring).
    #[test]
    fn band_step_disconnected_insets_each_plateau_and_splits_the_path() {
        let payload: Payload =
            serde_json::from_value(make_band_step_line_payload(Some(false))).unwrap();
        let svg = render(&payload, &test_fonts()).expect("disconnected band step renders");
        let d = main_line_d(&svg);
        assert_eq!(d.matches('M').count(), 3, "one subpath per band: {d}");
        assert!(!d.contains('Z'), "an open 2-point run under a non-round cap never closes: {d}");
        let xs = distinct_xs(&d);
        assert_eq!(xs.len(), 6, "3 disconnected 2-point plateaus: {xs:?}");
        let plateau_w = xs[1] - xs[0];
        let gap = xs[2] - xs[1];
        // inset fraction lo = (1-0.9)/2 = 0.05 of the band width on EACH
        // side, so the gap between two plateaus is 2*lo = 0.1 of a band and
        // the plateau itself is 0.9 of one — ratio gap/plateau = 1/9.
        assert!(
            (gap / plateau_w - 1.0 / 9.0).abs() < 1e-3,
            "5% inset each side of a 0.9-wide plateau: plateau={plateau_w} gap={gap}"
        );
        assert!(
            svg.contains("stroke-linecap=\"butt\""),
            "disconnected_cap replaces the authored round stroke cap: {svg}"
        );
    }

    #[test]
    fn band_step_disconnected_plateau_spans_the_authored_share_of_its_band() {
        let mut raw = make_band_step_line_payload(Some(false));
        raw["resolved"]["style"]["line_mark"]["disconnected_width"] = serde_json::json!(0.5);
        let payload: Payload = serde_json::from_value(raw).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let xs = distinct_xs(&main_line_d(&svg));
        let (plateau_w, gap) = (xs[1] - xs[0], xs[2] - xs[1]);
        assert!((gap / plateau_w - 1.0).abs() < 1e-3, "half a band each: plateau={plateau_w} gap={gap}");
    }

    #[test]
    fn band_step_disconnected_without_a_resolved_width_refuses_by_name() {
        let mut raw = make_band_step_line_payload(Some(false));
        raw["resolved"]["style"]["line_mark"]
            .as_object_mut()
            .unwrap()
            .remove("disconnected_width");
        let payload: Payload = serde_json::from_value(raw).unwrap();
        let err = render(&payload, &test_fonts()).unwrap_err();
        assert!(err.contains("line_mark.disconnected_width"), "{err}");
    }

    /// Query order `Feb, Apr, Mar, Jan` with `seq` `2, 4, 3, 1`: ascending
    /// sort puts the bands at `Jan, Feb, Mar, Apr`, descending at `Apr, Mar,
    /// Feb, Jan`, while the rows — and so the doubled step path — stay in
    /// query order either way.
    fn step_sort_payload(connect: Option<bool>, order: &str) -> serde_json::Value {
        let mut p = make_band_step_line_payload(connect);
        p["rows"] = serde_json::json!([
            {"cat": "Feb", "val": 200.0, "seq": 2.0},
            {"cat": "Apr", "val": 50.0, "seq": 4.0},
            {"cat": "Mar", "val": 120.0, "seq": 3.0},
            {"cat": "Jan", "val": 100.0, "seq": 1.0},
        ]);
        p["resolved"]["sort"] = serde_json::json!({"by": "seq", "order": order});
        p
    }

    fn path_vertices(d: &str) -> Vec<(f64, f64)> {
        d.trim_end_matches('Z')
            .split(['M', 'L'])
            .filter(|s| !s.is_empty())
            .map(|pair| {
                let (x, y) = pair.split_once(',').unwrap();
                (x.parse().unwrap(), y.parse().unwrap())
            })
            .collect()
    }

    /// Each vertex's x as a count of band widths from the plot's left edge
    /// (the connected path's last vertex sits on the right edge, `bands`
    /// bands over), so the assertion holds at any canvas size.
    fn band_units(vertices: &[(f64, f64)], bands: f64) -> Vec<f64> {
        let right_edge = vertices.iter().map(|v| v.0).fold(f64::MIN, f64::max);
        vertices.iter().map(|v| v.0 / (right_edge / bands)).collect()
    }

    /// One band's width in pixels, read off the connected path's printed right edge.
    fn band_px(vertices: &[(f64, f64)], bands: f64) -> f64 {
        vertices.iter().map(|v| v.0).fold(f64::MIN, f64::max) / bands
    }

    /// A band unit is a quotient of printed path coordinates, and each printed coordinate sits
    /// within `PATH_COORD_TOLERANCE` of its position (d3-shape@3.2.0 `withPath` digits 3,
    /// d3-path's `Math.round(x * k) / k`). A quotient of `printed_operands` of them (a vertex x
    /// and the right edge it is divided by: 2) moves by at most
    /// `printed_operands * PATH_COORD_TOLERANCE / band_px` units.
    fn assert_units(got: &[f64], want: &[f64], band_px: f64, printed_operands: f64) {
        assert_eq!(got.len(), want.len(), "vertex count: {got:?}");
        let tolerance = printed_operands * d3rs::path::PATH_COORD_TOLERANCE / band_px;
        for (g, w) in got.iter().zip(want) {
            assert!((g - w).abs() <= tolerance, "band units {got:?} != {want:?}");
        }
    }

    /// [`assert_units`] for a connected path's vertices measured against its own right edge.
    fn assert_band_units(vertices: &[(f64, f64)], bands: f64, want: &[f64]) {
        assert_units(&band_units(vertices, bands), want, band_px(vertices, bands), 2.0);
    }

    /// Pixel y orders opposite to the measure: vertex `i` sits above vertex
    /// `j` exactly when its measure is larger.
    fn assert_y_follows(vertices: &[(f64, f64)], measures: &[f64]) {
        assert_eq!(vertices.len(), measures.len());
        for (i, a) in vertices.iter().enumerate() {
            for (j, b) in vertices.iter().enumerate() {
                assert_eq!(
                    (a.1 - b.1).abs() < 1e-6,
                    measures[i] == measures[j],
                    "y of vertex {i} vs {j}: {vertices:?}"
                );
                assert_eq!(a.1 < b.1 - 1e-6, measures[i] > measures[j], "y order {i} vs {j}");
            }
        }
    }

    /// Production's `apply_step_band` doubles every row onto its band's two
    /// edges and the Vega-Lite line mark joins the doubled points in QUERY
    /// order, while `pin_sorted_domain` only reorders which band each
    /// category sits in. Probed through the production render on `Feb, Apr,
    /// Mar, Jan` (`seq` 2, 4, 3, 1), `curve: step`, `sort: seq desc`
    /// (domain `Apr, Mar, Feb, Jan`, bands 134px wide): the path is
    /// `M268,27.852 L402,27.852 L402,27.852 L0,27.852 L0,288.963
    /// L134,288.963 L134,288.963 L134,288.963 L134,167.111 L268,167.111
    /// L268,167.111 L402,167.111 L402,201.926 L536,201.926 L536,201.926`
    /// — x/134 = 2 3 3 0 0 1 1 1 1 2 2 3 3 4 4, the path running back and
    /// forth across the sorted bands. Ascending (domain `Jan, Feb, Mar,
    /// Apr`) draws x/134 = 1 2 2 3 3 4 4 2 2 3 3 0 0 1 1.
    #[test]
    fn band_step_under_an_authored_sort_draws_the_path_in_query_row_order() {
        let measures = [
            200.0, 200.0, 200.0, 200.0, 50.0, 50.0, 50.0, 50.0, 120.0, 120.0, 120.0, 120.0,
            100.0, 100.0, 100.0,
        ];
        for (order, labels, units) in [
            (
                "desc",
                ["Apr", "Mar", "Feb", "Jan"],
                [2., 3., 3., 0., 0., 1., 1., 1., 1., 2., 2., 3., 3., 4., 4.],
            ),
            (
                "asc",
                ["Jan", "Feb", "Mar", "Apr"],
                [1., 2., 2., 3., 3., 4., 4., 2., 2., 3., 3., 0., 0., 1., 1.],
            ),
        ] {
            let payload: Payload = serde_json::from_value(step_sort_payload(None, order)).unwrap();
            let svg = render(&payload, &test_fonts()).unwrap_or_else(|e| panic!("{order}: {e}"));
            assert_label_order(&svg, &labels);
            let vertices = path_vertices(&main_line_d(&svg));
            assert_band_units(&vertices, 4.0, &units);
            assert_y_follows(&vertices, &measures);
        }
    }

    /// A null measure breaks the path where its row sits in QUERY order, not
    /// where its band sits: with `Mar` null, production's path is `M268,…
    /// L402 L402 L0 L0 L134 L134` (`Feb`, `Apr`) then a new subpath `M402,…
    /// L536 L536` (`Jan`) — x/134 = 2 3 3 0 0 1 1 | 3 4 4.
    #[test]
    fn band_step_under_an_authored_sort_breaks_at_a_null_in_query_row_order() {
        let mut p = step_sort_payload(None, "desc");
        p["rows"][2]["val"] = serde_json::Value::Null;
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("renders");
        let d = main_line_d(&svg);
        let runs: Vec<Vec<(f64, f64)>> = d.split('M').skip(1).map(path_vertices).collect();
        assert_eq!(runs.len(), 2, "one break at the null: {d}");
        let all: Vec<(f64, f64)> = runs.concat();
        let units = band_units(&all, 4.0);
        let (first, second) = units.split_at(runs[0].len());
        let band = band_px(&all, 4.0);
        assert_units(first, &[2., 3., 3., 0., 0., 1., 1.], band, 2.0);
        assert_units(second, &[3., 4., 4.], band, 2.0);
    }

    /// `connect: false` splits the path into one inset plateau per band, in
    /// query order too: production's subpaths begin at `Feb`'s left edge
    /// (band 2 of the descending domain, `M277.263` on a 135.25px band),
    /// then `Apr` (0, `M6.762`), `Mar` (1, `M142.013`), `Jan` (3,
    /// `M412.513`) — each 5% of a band in from its band's left edge.
    #[test]
    fn disconnected_band_step_under_an_authored_sort_splits_in_query_row_order() {
        let payload: Payload =
            serde_json::from_value(step_sort_payload(Some(false), "desc")).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let d = main_line_d(&svg);
        let plateaus: Vec<Vec<(f64, f64)>> =
            d.split('M').skip(1).map(|run| path_vertices(run)).collect();
        assert_eq!(plateaus.len(), 4, "one subpath per band: {d}");
        let band_w = (plateaus[0][1].0 - plateaus[0][0].0) / 0.9;
        let left_bands: Vec<f64> = plateaus.iter().map(|p| p[0].0 / band_w).collect();
        // `band_w` divides two printed x differences by 0.9, so it carries up to 2/0.9 printed
        // tolerances; a unit `x / band_w` of at most 3.05 then moves by
        // `(1 + 3.05 * 2 / 0.9)` tolerances over `band_w`, under 9.
        assert_units(&left_bands, &[2.05, 0.05, 1.05, 3.05], band_w, 9.0);
    }

    /// `step_band.apply_step_band` refuses a data-driven multi-series color
    /// encoding outright — the per-band `detail`/`xOffset` grouping can't
    /// honor more than one series.
    #[test]
    fn band_step_multi_series_refuses() {
        let mut json = make_band_step_line_payload(None);
        json["resolved"]["color"] = serde_json::json!("series");
        json["rows"] = serde_json::json!([
            {"cat": "c0", "series": "A", "val": 10.0},
            {"cat": "c0", "series": "B", "val": 5.0},
        ]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).unwrap_err();
        assert!(
            err.contains(crate::codes::ERR_STEP_BAND_MULTI_SERIES.id),
            "names the multi-series band-step refusal: {err}"
        );
    }

    #[test]
    fn left_axis_line_draws_bottom_x_title_and_rotated_y_title() {
        let payload: Payload =
            serde_json::from_value(make_line_payload(false, true)).unwrap();
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        // Default titles are the bare field names (production's
        // slug_to_text — no title-casing, no font.case applied).
        assert!(svg.contains(">Week<"), "x title text present: {svg}");
        assert!(svg.contains(">Signups<"), "y title text present: {svg}");
        assert!(svg.contains("rotate(-90"), "left y-axis title rotates -90: {svg}");

        // Reservation grew left_margin — pin against the untitled render.
        let untitled: Payload =
            serde_json::from_value(make_line_payload(false, false)).unwrap();
        let untitled_svg = render(&untitled, &fonts).expect("renders");
        assert!(
            plot_group_x(&svg) > plot_group_x(&untitled_svg) + 15.0,
            "titled left_margin={} should clear untitled left_margin={} by roughly the \
             title band (font.size + padding = 21)",
            plot_group_x(&svg),
            plot_group_x(&untitled_svg)
        );
    }

    #[test]
    fn right_axis_line_rotates_y_title_the_other_way() {
        let payload: Payload = serde_json::from_value(make_line_payload(true, true)).unwrap();
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains(">Signups<"), "y title text present: {svg}");
        assert!(svg.contains("rotate(90 "), "right y-axis title rotates +90: {svg}");
    }

    /// Regression: line.rs's chart-title emission wrote `r.title` straight
    /// through `xml_escape` without ever consulting `title_style.font.case`,
    /// unlike every other VL family (bar/heatmap/pie/scatter all route
    /// through `common::emit_title`, which does apply it; `table.rs` hand-rolls
    /// its own title path for unrelated reasons). Production always
    /// resolves `title_style` (never `None`) and applies `apply_case` at VL
    /// emission time (`spec_builders.set_chart_title`), so a themed
    /// `case: "title"` title-cases "Third width — line" to "Third Width —
    /// Line". Deletion counterfactual: reverting the emission fix back to
    /// `xml_escape(title)` makes this fail (raw text, lowercase "width"/
    /// "line" survive the em dash untouched).
    #[test]
    fn chart_title_applies_font_case_like_other_families() {
        let mut payload_json = make_line_payload(false, false);
        payload_json["resolved"]["title"] = serde_json::json!("Third width — line");
        payload_json["resolved"]["title_style"] = serde_json::json!({
            "font": {"family": TEST_FONT, "size": 18.0, "weight": "500", "color": "#494A4C", "case": "title"}
        });
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(
            svg.contains(">Third Width \u{2014} Line<"),
            "title is title-cased per title_style.font.case: {svg}"
        );
        assert!(
            !svg.contains(">Third width \u{2014} line<"),
            "raw untransformed title must not survive: {svg}"
        );
    }

    /// Regression for the `y_on_right` right-side reservation (`right_content`'s
    /// `y_title_band` term in the `(plot_w, plot_h, left_margin)` match arm):
    /// the sibling test above only checks text presence and rotation sign,
    /// neither of which move if the reservation is dropped — deleting
    /// `y_title_band` from `right_content` grows `plot_w` by the same amount
    /// the title shifts right, pushing it past the `pad_r` inset instead of
    /// clearing it.
    #[test]
    fn right_axis_line_y_title_stays_within_pad_r() {
        let payload: Payload = serde_json::from_value(make_line_payload(true, true)).unwrap();
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let group_x = plot_group_x(&svg);
        let title_pos = svg.find(">Signups<").unwrap();
        let title_tag_start = svg[..title_pos].rfind("<text").unwrap();
        let local_title_x: f64 = svg[title_tag_start..title_pos]
            .split("x=\"")
            .nth(1)
            .unwrap()
            .split('"')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        let abs_title_x = group_x + local_title_x;

        // Canvas width 600, pad_r 16 — the title must sit within (not past)
        // the right padding inset.
        assert!(
            abs_title_x <= 600.0 - 16.0 + 0.5,
            "right-axis title x={abs_title_x} must stay within the pad_r=16 inset (<= 584)"
        );
    }

    /// Regression for the x-title bottom-margin reservation (`bottom_margin`'s
    /// `x_title_band` term): the sibling test above only reads the plot
    /// group's *x* translate (left_margin), which the x-title never touches —
    /// deleting `x_title_band` from `bottom_margin` leaves that test green
    /// while shrinking `plot_h` back to the untitled value. Read plot_h via
    /// the x-axis tick mark's `y1` (== plot_h) instead.
    fn plot_h_from_tick(svg: &str) -> f64 {
        let idx = svg.find("<line x1=").expect("x-axis tick line present");
        svg[idx..idx + 60]
            .split("y1=\"")
            .nth(1)
            .unwrap()
            .split('"')
            .next()
            .unwrap()
            .parse()
            .unwrap()
    }

    #[test]
    fn left_axis_line_x_title_shrinks_plot_height() {
        let titled: Payload =
            serde_json::from_value(make_line_payload(false, true)).unwrap();
        let untitled: Payload =
            serde_json::from_value(make_line_payload(false, false)).unwrap();
        let fonts = test_fonts();
        let titled_svg = render(&titled, &fonts).expect("renders");
        let untitled_svg = render(&untitled, &fonts).expect("renders");

        // x_title_band = title.font.size(11) + title.padding(10) = 21.
        let titled_h = plot_h_from_tick(&titled_svg);
        let untitled_h = plot_h_from_tick(&untitled_svg);
        assert!(
            (untitled_h - titled_h - 21.0).abs() < 0.5,
            "titled plot_h={titled_h} should be exactly 21 less than untitled plot_h={untitled_h}"
        );
    }

    /// Same reservation, y_on_right arm — the review's deletion
    /// counterfactual: dropping x_title_band from the y_on_right branch
    /// grows plot_h by 21 and pushes the title past the canvas edge while
    /// every other assertion stays green. This pins that branch's term.
    #[test]
    fn right_axis_line_x_title_shrinks_plot_height() {
        let titled: Payload =
            serde_json::from_value(make_line_payload(true, true)).unwrap();
        let untitled: Payload =
            serde_json::from_value(make_line_payload(true, false)).unwrap();
        let fonts = test_fonts();
        let titled_svg = render(&titled, &fonts).expect("renders");
        let untitled_svg = render(&untitled, &fonts).expect("renders");

        let titled_h = plot_h_from_tick(&titled_svg);
        let untitled_h = plot_h_from_tick(&untitled_svg);
        assert!(
            (untitled_h - titled_h - 21.0).abs() < 0.5,
            "y_on_right: titled plot_h={titled_h} should be exactly 21 less than untitled plot_h={untitled_h}"
        );
    }

    /// Regression: with two-row temporal x labels, the bottom title must sit
    /// below row two's baseline by `label_font_size + padding` — mirroring
    /// bar.rs's `has_two_row_labels` fix (this term already exists here and
    /// is the sibling the review points to as correct), but nothing
    /// previously exercised it. Deleting the `has_two_row_labels` term from
    /// `x_label_baseline` must fail this test.
    #[test]
    fn two_row_temporal_x_title_sits_below_row_two() {
        let _locale = crate::time_locale::en_us_scope();
        let mut payload_json = make_line_payload(false, true);
        payload_json["resolved"]["x"] = serde_json::json!("month");
        payload_json["rows"] = serde_json::json!([
            {"month": "2024-01-01", "signups": 100.0},
            {"month": "2024-02-01", "signups": 150.0},
            {"month": "2024-03-01", "signups": 130.0},
        ]);
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        assert!(svg.contains(">Month<"), "bottom x title present: {svg}");
        let row2_pos = svg.find(">2024<").expect("row2 year label present");
        let row2_tag_start = svg[..row2_pos].rfind("<text").unwrap();
        let row2_y: f64 = svg[row2_tag_start..row2_pos]
            .split("y=\"")
            .nth(1)
            .unwrap()
            .split('"')
            .next()
            .unwrap()
            .parse()
            .unwrap();

        let title_pos = svg.find(">Month<").unwrap();
        let title_tag_start = svg[..title_pos].rfind("<text").unwrap();
        let title_y: f64 = svg[title_tag_start..title_pos]
            .split("y=\"")
            .nth(1)
            .unwrap()
            .split('"')
            .next()
            .unwrap()
            .parse()
            .unwrap();

        // x_font_size=11, title.padding=10 → title baseline = row2_y + 21.
        assert!(title_y > row2_y, "title_y={title_y} must sit below row2's baseline y={row2_y}");
        assert!(
            (title_y - row2_y - 21.0).abs() < 0.5,
            "title_y={title_y} should be row2_y({row2_y}) + label_font_size(11) + padding(10) \
             = {}",
            row2_y + 21.0
        );
    }

    #[test]
    fn hidden_axis_title_reserves_no_margin() {
        let visible: Payload = serde_json::from_value(make_line_payload(false, true)).unwrap();
        let hidden: Payload = serde_json::from_value(make_line_payload(false, false)).unwrap();
        let fonts = test_fonts();
        let visible_svg = render(&visible, &fonts).expect("renders");
        let hidden_svg = render(&hidden, &fonts).expect("renders");
        assert!(!hidden_svg.contains(">week<"));
        assert!(!hidden_svg.contains(">signups<"));
        assert!(plot_group_x(&visible_svg) > plot_group_x(&hidden_svg));
    }

    #[test]
    fn no_collision_keeps_every_anchor_at_its_own_value() {
        // Anchors already `min_gap` apart or wider need no pushing.
        let anchors = vec![("a".to_string(), 90.0), ("b".to_string(), 50.0), ("c".to_string(), 10.0)];
        let placed = cascade_rail_anchors(&anchors, 10.0, &crate::families::cartesian::one_line_extras(&anchors), 0.0, 100.0).0;
        assert_eq!(placed, vec![("a".to_string(), 90.0), ("b".to_string(), 50.0), ("c".to_string(), 10.0)]);
    }

    #[test]
    fn descending_branch_pushes_crowded_anchors_apart_without_dropping() {
        // Mean (196) is above the domain midpoint (100), so the descending
        // (push-downward) branch fires. All three anchors fit once pushed
        // apart because the domain is wide relative to the cluster.
        let anchors = vec![("hi".to_string(), 200.0), ("mid".to_string(), 199.0), ("lo".to_string(), 190.0)];
        let placed = cascade_rail_anchors(&anchors, 20.0, &crate::families::cartesian::one_line_extras(&anchors), 0.0, 200.0).0;
        assert_eq!(names(&placed), vec!["hi", "mid", "lo"], "descending order preserved: {placed:?}");
        assert_eq!(placed[0].1, 200.0, "top anchor keeps its own position");
        assert_eq!(placed[1].1, 180.0, "pushed exactly min_gap below the previous placement");
        assert_eq!(placed[2].1, 160.0, "pushed exactly min_gap below the previous placement");
    }

    #[test]
    fn ascending_branch_pushes_crowded_anchors_apart_without_dropping() {
        // Mean (11) is below the domain midpoint (100), so the ascending
        // (push-upward) branch fires instead of the descending one.
        let anchors = vec![("lo".to_string(), 10.0), ("mid".to_string(), 11.0), ("hi".to_string(), 20.0)];
        let placed = cascade_rail_anchors(&anchors, 20.0, &crate::families::cartesian::one_line_extras(&anchors), 0.0, 200.0).0;
        assert_eq!(names(&placed), vec!["lo", "mid", "hi"], "ascending order preserved: {placed:?}");
        assert_eq!(placed[0].1, 10.0, "bottom anchor keeps its own position");
        assert_eq!(placed[1].1, 30.0, "pushed exactly min_gap above the previous placement");
        assert_eq!(placed[2].1, 50.0, "pushed exactly min_gap above the previous placement");
    }

    #[test]
    fn a_label_that_cannot_fit_evicts_a_pushed_neighbor_not_an_isolated_one() {
        // `b` was pushed off its anchor to make room for `c`, so when `c` still
        // overflows the floor the pushed `b` is the one dropped; `c` then fits
        // under `a` on its own.
        let anchors = vec![("a".to_string(), 500.0), ("b".to_string(), 499.0), ("c".to_string(), 498.0)];
        let (placed, dropped) = cascade_rail_anchors(&anchors, 300.0, &crate::families::cartesian::one_line_extras(&anchors), 0.0, 500.0);
        assert_eq!(names(&placed), vec!["a", "c"], "{placed:?}");
        assert_eq!(placed[0].1, 500.0);
        assert_eq!(placed[1].1, 200.0);
        assert_eq!(dropped, vec!["b".to_string()]);
    }

    #[test]
    fn branch_selector_compares_against_the_domain_midpoint_not_the_anchor_span() {
        // Anchors {10, 15, 20} sit in the LOWER half of domain [0, 200] (mid
        // 100), so the ascending (push-upward) branch must fire even though
        // the anchors' own span midpoint (15) would pick descending if a
        // buggy port compared against that instead of the domain's.
        let anchors = vec![("a".to_string(), 10.0), ("b".to_string(), 15.0), ("c".to_string(), 20.0)];
        let placed = cascade_rail_anchors(&anchors, 3.0, &crate::families::cartesian::one_line_extras(&anchors), 0.0, 200.0).0;
        assert_eq!(names(&placed), vec!["a", "b", "c"], "ascending order: {placed:?}");
        assert!(placed[0].1 < placed[1].1 && placed[1].1 < placed[2].1, "y increases along the ascending walk");
    }

    #[test]
    fn anchors_outside_the_domain_are_clamped_not_treated_as_a_collision() {
        let anchors = vec![("over".to_string(), 999.0), ("under".to_string(), -50.0)];
        let placed = cascade_rail_anchors(&anchors, 5.0, &crate::families::cartesian::one_line_extras(&anchors), 0.0, 100.0).0;
        assert_eq!(placed.len(), 2, "both anchors placed, no drop: {placed:?}");
        assert!(placed.iter().any(|(n, y)| n == "over" && *y == 100.0));
        assert!(placed.iter().any(|(n, y)| n == "under" && *y == 0.0));
    }

    fn nov_16_months() -> serde_json::Value {
        let rows: Vec<serde_json::Value> = (0..16)
            .map(|i| {
                let total = 10 + i;
                let year = 2024 + total / 12;
                let month = total % 12 + 1;
                serde_json::json!({"month": format!("{year:04}-{month:02}-01"), "value": 60 + i})
            })
            .collect();
        serde_json::Value::Array(rows)
    }

    fn nov_16_months_payload(width: f64, flush: serde_json::Value) -> Payload {
        let mut payload_json = make_line_payload(false, false);
        payload_json["rows"] = nov_16_months();
        payload_json["resolved"]["x"] = serde_json::json!("month");
        payload_json["resolved"]["y"] = serde_json::json!("value");
        payload_json["width"] = serde_json::json!(width);
        payload_json["resolved"]["style"]["axis_x"]["labels"]["flush"] = flush;
        payload_json["resolved"]["style"]["axis_x"]["labels"]["overlap"] =
            serde_json::json!({"skip": true, "tilt": true});
        serde_json::from_value(payload_json).unwrap()
    }

    /// Regression for `line.rs`'s temporal-cadence-choice width: production's
    /// y-axis gutter takes real plot space that `avail_w` (payload width
    /// minus padding alone) doesn't net out. A wide y-axis label ("123,456")
    /// widens that gutter enough that the true plot width falls narrower
    /// than the `Nov`→`Dec` coarsening boundary while `avail_w` alone stays
    /// on the wide side of it — so feeding the cadence choice `avail_w`
    /// wrongly renders every month here where the real plot renders quarter
    /// openers. Deletion counterfactual: reverting `line.rs`'s
    /// `resolve_temporal_labels` call to `avail_w` (the pre-fix behavior)
    /// makes this render every month instead, and this test fails.
    #[test]
    fn wide_y_axis_gutter_narrows_the_real_plot_width_used_for_cadence() {
        let _locale = crate::time_locale::en_us_scope();
        let mut payload_json = make_line_payload(false, false);
        let mut rows = nov_16_months();
        for (i, row) in rows.as_array_mut().unwrap().iter_mut().enumerate() {
            row["value"] = serde_json::json!(100_000 + i as i64 * 1000);
        }
        payload_json["rows"] = rows;
        payload_json["resolved"]["x"] = serde_json::json!("month");
        payload_json["resolved"]["y"] = serde_json::json!("value");
        payload_json["width"] = serde_json::json!(560.0);
        payload_json["resolved"]["style"]["axis_x"]["labels"]["overlap"] =
            serde_json::json!({"skip": true, "tilt": true});
        let payload: Payload = serde_json::from_value(payload_json).unwrap();
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");

        let non_quarter = ["Feb", "Mar", "May", "Jun", "Aug", "Sep", "Nov", "Dec"];
        assert!(
            !non_quarter.iter().any(|m| svg.contains(&format!(">{m}<"))),
            "a wide y-axis label gutter must narrow the real plot enough to              coarsen to quarter openers, even though the padding-only avail_w              would still fit every month:\n{svg}"
        );
        assert_eq!(
            x_label_texts(&svg),
            ["Q4", "2024", "Q1", "2025", "Q2", "Q3", "Q4", "Q1", "2026"],
            "quarter labels must render on the coarsened axis"
        );
    }

    /// Regression for the `edge_labels_flushed` parity fix (`line.rs`'s
    /// `ax_x.labels.flush != Some(false)`): at this exact 500px width and
    /// 16-month Nov-start domain, "Nov" flushed to the plot's left edge
    /// reserves its full row-1 width rather than half, so `Nov`→`Dec`
    /// fails to clear and the ladder promotes visibility to
    /// quarter openers — matching production. An authored `flush: false`
    /// halves that reservation, the pair fits, and every month renders
    /// instead. Deletion counterfactual: hardcoding `edge_labels_flushed =
    /// false` in `render_inner` (the pre-fix behavior) makes the default
    /// (unauthored `flush`) case render monthly too, and this test fails.
    /// (Width lowered from the original 630px after `temporal.rs`'s
    /// year-row fold fix: folding "Nov"'s stacked "2024" row into its
    /// reserved width — the bug — used to make 630px already tight enough
    /// to coarsen; row-1-only reservation needs a narrower plot to
    /// reproduce the same boundary.)
    #[test]
    fn authored_flush_false_widens_the_temporal_coarsening_boundary() {
        let _locale = crate::time_locale::en_us_scope();
        let fonts = test_fonts();
        let non_quarter = ["Feb", "Mar", "May", "Jun", "Aug", "Sep", "Nov", "Dec"];

        let default_svg = render(&nov_16_months_payload(500.0, serde_json::Value::Null), &fonts)
            .expect("renders");
        assert!(
            !non_quarter.iter().any(|m| default_svg.contains(&format!(">{m}<"))),
            "default (flushed) axis coarsens to quarter openers at this width:\n{default_svg}"
        );

        let unflushed_svg =
            render(&nov_16_months_payload(500.0, serde_json::json!(false)), &fonts)
                .expect("renders");
        assert!(
            non_quarter.iter().any(|m| unflushed_svg.contains(&format!(">{m}<"))),
            "flush:false halves the edge reservation, so every month fits:\n{unflushed_svg}"
        );
    }

    /// Regression for `common.rs`'s `AxisLabels.flush`: production types it
    /// `bool | float | None` (Vega's `labelFlush` also accepts a pixel
    /// threshold). A numeric `flush` must not panic deserialization — it
    /// only needs to not equal the literal `false` that turns flushing off.
    #[test]
    fn numeric_flush_value_does_not_panic_deserialization() {
        let _locale = crate::time_locale::en_us_scope();
        let fonts = test_fonts();
        let payload = nov_16_months_payload(630.0, serde_json::json!(4));
        render(&payload, &fonts).expect("numeric flush must not panic or error");
    }

    /// Wiring pin: `render_inner` must pass `ax_x.fiscal_year_start_month`
    /// through to `resolve_temporal_labels` (line.rs:427), not a literal `1`.
    /// Twelve monthly points get a "YYYY" row-2 annotation on year openers:
    /// under the calendar default only January (the first point) carries one,
    /// so "2024" appears once; under a March fiscal year start, March also
    /// opens a fiscal year, so "2024" appears twice. Mirrors the bar-side pin
    /// in `bar.rs` — both call sites were flagged unpinned in review.
    #[test]
    fn line_fiscal_year_start_month_wiring_shifts_year_opener_label() {
        let _locale = crate::time_locale::en_us_scope();
        use serde_json::json;
        let render_with_fiscal_start = |fiscal_start: u8| -> String {
            let mut payload_json = make_line_payload(false, false);
            payload_json["resolved"]["x"] = json!("month");
            payload_json["resolved"]["y"] = json!("signups");
            payload_json["rows"] = json!((1..=12)
                .map(|m| json!({"month": format!("2024-{m:02}-01"), "signups": 100.0 + m as f64}))
                .collect::<Vec<_>>());
            payload_json["resolved"]["style"]["axis_x"]["fiscal_year_start_month"] =
                json!(fiscal_start);
            payload_json["width"] = json!(1200.0);
            let payload: Payload = serde_json::from_value(payload_json).unwrap();
            let fonts = test_fonts();
            render(&payload, &fonts).expect("renders")
        };

        let calendar_svg = render_with_fiscal_start(1);
        let fiscal_svg = render_with_fiscal_start(3);

        let count_year_labels = |svg: &str| svg.matches(">2024<").count();
        assert_eq!(
            count_year_labels(&calendar_svg),
            1,
            "calendar year: only January opens 2024: {calendar_svg}"
        );
        assert_eq!(
            count_year_labels(&fiscal_svg),
            2,
            "fiscal year starting March: January (first) and March (fiscal open) both carry 2024: {fiscal_svg}"
        );
    }

    /// Fit-path payload (right axis, no legend/rail) with json tweaks applied
    /// on top of the base fixture before deserializing.
    fn fit_payload(mutate: impl FnOnce(&mut serde_json::Value)) -> Payload {
        let mut json = make_line_payload(true, false);
        mutate(&mut json);
        serde_json::from_value(json).unwrap()
    }

    /// `(x1, y1, x2, y2)` of every `<line>` inside the plot group.
    fn plot_lines(svg: &str) -> Vec<(f64, f64, f64, f64)> {
        svg.split("<g class=\"mark-rule role-axis-grid\" aria-hidden=\"true\">")
            .skip(1)
            .flat_map(|group| {
                let group = group.split("</g>").next().unwrap();
                group
                    .match_indices("<line ")
                    .map(|(i, _)| {
                        let tag = group[i..].split("/>").next().unwrap();
                        (attr(tag, "x1"), attr(tag, "y1"), attr(tag, "x2"), attr(tag, "y2"))
                    })
                    .collect::<Vec<_>>()
            })
            .collect()
    }

    /// Vega's `tickRound` (default true) snaps every grid rule and tick to
    /// a whole pixel before the axis group's +0.5 crisp translate; the
    /// labels keep the exact scale position (six_series reference: rules
    /// at 45/86/131 where the labels sit at 45.015/85.674/130.689).
    #[test]
    fn grid_rules_and_ticks_snap_to_whole_pixels_on_the_fit_arm() {
        let payload = fit_payload(|j| {
            j["width"] = serde_json::json!(500.0);
            j["resolved"]["style"]["axis_x"]["grid"]["visible"] = serde_json::json!(true);
            j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
        });
        let svg = render(&payload, &test_fonts()).expect("renders");
        let lines = plot_lines(&svg);
        assert!(lines.len() >= 6, "{svg}");
        for (x1, y1, x2, y2) in &lines {
            let coord = if x1 == x2 { *x1 } else { *y1 };
            assert!(((coord - 0.5).fract()).abs() < 1e-9, "rule at {x1},{y1},{x2},{y2} not snapped: {svg}");
        }
        // The middle x label keeps its unrounded point-scale position.
        let mid = text_tag(&svg, "5");
        assert!((attr(&mid, "x") - 0.5).fract().abs() > 1e-6, "label snapped: {mid}");
    }

    /// An unbaked, uncounted value axis takes Vega-Lite's default
    /// `tickCount`, `ceil(height/40)`, on the rail concat's pane: a 0–60
    /// domain on a ~350px pane gets d3's step-5 ladder (13 rungs), not the
    /// provisional count-6 ladder's step 10 (six_series reference: 13).
    #[test]
    fn an_unbaked_ladder_on_the_rail_arm_takes_vega_lite_s_default_count() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "A", "signups": 12.0},
            {"week": "2026-01-08", "series": "A", "signups": 60.0},
            {"week": "2026-01-01", "series": "B", "signups": 40.0},
            {"week": "2026-01-08", "series": "B", "signups": 30.0},
        ]);
        p["height"] = json!(432.0);
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        p["resolved"]["style"]["axis_y"]["ticks"]["count"] = json!(null);
        set_zero_style(&mut p, true);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let rungs = plot_lines(&svg).iter().filter(|(x1, _, x2, _)| *x1 == 0.5 && x2 - x1 > 50.0).count();
        assert_eq!(rungs, 13, "{svg}");
        assert!(svg.contains(">5</text>") && svg.contains(">55</text>"), "{svg}");
    }

    /// The same default on the fit arm, at the fitted plot height: a
    /// 100–150 domain on a ~350px plot asks d3 for nine rungs and gets the
    /// step-5 ladder (11 rungs); the count-6 ladder would draw six.
    #[test]
    fn an_unbaked_ladder_on_the_fit_arm_takes_vega_lite_s_default_count() {
        let payload = fit_payload(|j| {
            j["height"] = serde_json::json!(400.0);
            j["resolved"]["style"]["axis_y"]["ticks"]["count"] = serde_json::json!(null);
            j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
        });
        let svg = render(&payload, &test_fonts()).expect("renders");
        let rungs = plot_lines(&svg).iter().filter(|(x1, _, x2, _)| *x1 == 0.5 && x2 - x1 > 50.0).count();
        assert_eq!(rungs, 11, "{svg}");
        assert!(svg.contains(">105</text>"), "{svg}");
    }

    const ZERO_RULE_COLOR: &str = "#123456";

    fn set_zero_style(json: &mut serde_json::Value, grid_visible: bool) {
        json["resolved"]["style"]["axis_y"]["grid"] = serde_json::json!({
            "visible": grid_visible,
            "threshold": {"color": ZERO_RULE_COLOR, "width": 2.0},
            "width": 1.0, "color": "#D0D3D9", "opacity": 1.0
        });
    }

    /// `_insert_zero_rule` (baseline.py): with a visible grid and a zero
    /// style, the datum-0 layer renders AND pulls 0 into the y domain
    /// (positive-only data included) — the ladder restarts at 0.
    #[test]
    fn zero_rule_pulls_domain_to_zero() {
        let payload = fit_payload(|j| set_zero_style(j, true));
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(svg.contains(ZERO_RULE_COLOR), "zero rule drawn: {svg}");
        assert!(svg.contains(">0</text>"), "domain pulled to 0 → 0 tick: {svg}");
    }

    /// baseline.py:377 `if not chart.style.axis_y.grid.visible: return` — a
    /// hidden grid suppresses the rule layer, so nothing pulls the domain
    /// either: the ladder stays inside the data extent.
    #[test]
    fn zero_rule_suppressed_by_hidden_grid() {
        let payload = fit_payload(|j| set_zero_style(j, false));
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(!svg.contains(ZERO_RULE_COLOR), "no rule with grid hidden: {svg}");
        assert!(!svg.contains(">0</text>"), "domain stays [100,150]: {svg}");
        assert!(svg.contains(">100</text>"), "data-extent ladder: {svg}");
    }

    /// An AUTHORED `scale.continuous.domain` excluding 0 suppresses the rule
    /// (production reads `authored_measure_domain`, never the baked
    /// domain_min/domain_max) — no rule, no domain pull.
    #[test]
    fn zero_rule_suppressed_by_authored_domain_excluding_zero() {
        let payload = fit_payload(|j| {
            set_zero_style(j, true);
            j["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [50.0, 200.0]}});
        });
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(!svg.contains(ZERO_RULE_COLOR), "authored [50,200] suppresses: {svg}");
        assert!(!svg.contains(">0</text>"), "no 0 pulled into the ladder: {svg}");
    }

    /// Regression (#8343 generalize): `grid.threshold.visible` is an
    /// independent off-switch from `grid.visible` — production silences the
    /// rule without hiding this axis's other gridlines (`baseline.py`'s
    /// `if not threshold_style.visible: return`, checked before the
    /// `grid_visible` gate `build_zero_rule_if_applicable` also honors).
    #[test]
    fn zero_rule_suppressed_by_threshold_visible_false_even_with_grid_visible() {
        let payload = fit_payload(|j| {
            j["resolved"]["style"]["axis_y"]["grid"] = serde_json::json!({
                "visible": true,
                "threshold": {"visible": false, "color": ZERO_RULE_COLOR, "width": 2.0},
                "width": 1.0, "color": "#D0D3D9", "opacity": 1.0
            });
        });
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(!svg.contains(ZERO_RULE_COLOR), "no rule with threshold.visible: false: {svg}");
        assert!(!svg.contains(">0</text>"), "domain stays [100,150]: {svg}");
    }

    /// Point markers: a halo circle (halo fill, r = √(size·mult)/2 with the
    /// halo point layer's own stroke) under the line and a line-colored point
    /// (r = √size/2) over it, one pair per datum — UNclipped, like
    /// production's point overlays (`_layers.py`'s CLIP_TO_PLOT covers the
    /// halo line / hit-target / area backdrop only, so edge markers keep the
    /// `point_reach` the fit margin reserves).
    ///
    /// Vega-scenegraph redefines d3-shape's `circle` symbol so `size` is the
    /// area of the BOUNDING SQUARE — radius √size/2, not d3's √(size/π),
    /// which is 2/√π = 1.128× too big. The halo point layer sets no
    /// `strokeWidth` (`_layers.py::_line_halo_sublayers`), so it takes VL's
    /// default point strokeWidth 2, painted in the halo color.
    ///
    /// Oracle: `boards/examples_2/trends`' reference, whose `point_mark.size`
    /// is 46.24 at `halo_multiplier` 2, draws
    /// `M4.808,0A4.808,… stroke-width="2"` under `M3.4,0A3.4,…`.
    #[test]
    fn point_markers_take_vegas_bounding_square_radius() {
        let payload = fit_payload(|j| {
            j["resolved"]["style"]["point_mark"] = serde_json::json!({"size": 46.24});
            j["resolved"]["style"]["line_mark"]["halo_multiplier"] = serde_json::json!(2.0);
        });
        let svg = render(&payload, &test_fonts()).expect("renders");
        // √46.24/2 = 3.4, √92.48/2 = 4.808
        assert_eq!(svg.matches("r=\"4.808\"").count(), 3, "one halo per datum: {svg}");
        assert_eq!(svg.matches("r=\"3.400\"").count(), 3, "one point per datum: {svg}");
        let halo = svg.find("r=\"4.808\"").unwrap();
        let halo_tag = svg[svg[..halo].rfind("<circle").unwrap()..].split("/>").next().unwrap();
        assert!(
            halo_tag.contains("stroke-width=\"2\""),
            "halo takes VL's default point strokeWidth: {halo_tag}"
        );
        assert!(!svg.contains("clip-path"), "point overlays stay unclipped: {svg}");
        let point = svg.find("r=\"3.400\"").unwrap();
        let main_line = svg.find("stroke=\"#3164a3\" stroke-width=\"4\"").unwrap();
        assert!(halo < main_line && main_line < point, "halo under line, point over: {svg}");
    }

    /// The point-marker halo's own `fill`/`stroke` is the CHART's own
    /// opaque canvas (production's `_line_halo_sublayers`,
    /// `emitters/_layers.py:194-253`, paints every halo mark from `halo_
    /// color` — `chart.canvas` — directly, never reading `point_mark.
    /// fill`), never the board-level `payload.background` nor the chart's
    /// possibly-transparent `background`: the same contract `area_backdrop_
    /// fill_reads_the_chart_own_canvas_not_the_background` pins for the
    /// area backdrop, reached here for the halo. The fixture carries
    /// production's baked `point_mark.fill: #FFFFFF`, so a halo that reads
    /// that field instead of the chart canvas paints `#FFFFFF` and fails
    /// this assertion.
    #[test]
    fn point_halo_fill_reads_the_chart_own_canvas_not_the_background() {
        let payload = fit_payload(|j| {
            j["resolved"]["style"]["point_mark"] = serde_json::json!({"size": 46.24, "fill": "#FFFFFF"});
            j["resolved"]["style"]["line_mark"]["halo_multiplier"] = serde_json::json!(2.0);
            j["resolved"]["background"] = serde_json::json!("transparent");
            j["resolved"]["canvas"] = serde_json::json!("#d9a45b");
            j["background"] = serde_json::json!("#ffffff");
        });
        let svg = render(&payload, &test_fonts()).expect("renders");
        let halo = svg.find("r=\"4.808\"").unwrap();
        let halo_tag = svg[svg[..halo].rfind("<circle").unwrap()..].split("/>").next().unwrap();
        assert!(
            halo_tag.contains("fill=\"#d9a45b\"") && halo_tag.contains("stroke=\"#d9a45b\""),
            "the halo paints the chart's own canvas, not the board's: {halo_tag}"
        );
    }

    /// With the y axis on the right, the plot's LEFT chrome is nothing but the
    /// first vertex's halo reach — √(size·mult)/2 plus the halo symbol's FULL
    /// strokeWidth, since Vega's `boundStroke` expands a bound by the whole
    /// width — and Vega anchors the title at `padding + ceil(chrome) − chrome`.
    ///
    /// Oracle: the ten right-orient-y corpus rows (grid_line ×3, the cadence
    /// ladders, the time-unit matrix) all land title x = 16 + ceil(6.80833) −
    /// 6.80833 = 16.19167 at size 46.24, halo_multiplier 2. d3's radius with a
    /// half-stroke expansion gives 16.5745 instead.
    #[test]
    fn a_right_axis_title_is_anchored_at_the_unrounded_halo_reach() {
        let payload = fit_payload(|j| {
            set_title_and_style(j, None);
            j["resolved"]["style"]["point_mark"] = serde_json::json!({"size": 46.24});
            j["resolved"]["style"]["line_mark"]["halo_multiplier"] = serde_json::json!(2.0);
        });
        let svg = render(&payload, &test_fonts()).expect("renders");
        let reach = (46.24_f64 * 2.0).sqrt() / 2.0 + 2.0;
        let expected = 16.0 + reach.ceil() - reach;
        assert_eq!(
            plot_group_x(&svg),
            16.0 + reach.ceil(),
            "the plot clears the WHOLE halo stroke: {svg}"
        );
        assert_eq!(
            attr(&text_tag(&svg, "Revenue"), "x"),
            (expected * 100.0).round() / 100.0,
            "title anchored at the unrounded halo reach: {svg}"
        );
    }

    /// `halo_multiplier: 0` means production's `_line_halo_sublayers`
    /// (`emitters/_layers.py`) returns `[]` — no halo point layer exists at
    /// all, so its ink is 0. `point_mark_reach` must reserve only the
    /// foreground point's own `√size/2`, not `max(√size/2, 2.0)`: that 2.0
    /// was `symbol_radius(size × 0) + HALO_POINT_STROKE_W`, ink for a layer
    /// that is never drawn.
    ///
    /// Oracle: `theme/marks.py` (`compile/models/style/theme/marks.py`)
    /// documents `halo_multiplier` 0 as "disables the halo".
    #[test]
    fn halo_multiplier_zero_reserves_only_the_bare_point_radius() {
        let payload = fit_payload(|j| {
            set_title_and_style(j, None);
            // A thin line stroke so it never masks point_mark_reach in the
            // `ink.max(line_stroke_w, point_reach)` union below — the ceil'd
            // difference (2.0 vs 1.5) rounds the same either way, so only the
            // UNROUNDED title x, per the right-axis title law above, exposes
            // the fictional halo term.
            j["resolved"]["style"]["line_mark"]["stroke"]["width"] = serde_json::json!(1.0);
            j["resolved"]["style"]["point_mark"] = serde_json::json!({"size": 9.0});
            j["resolved"]["style"]["line_mark"]["halo_multiplier"] = serde_json::json!(0.0);
        });
        let svg = render(&payload, &test_fonts()).expect("renders");
        let reach = 9.0_f64.sqrt() / 2.0; // 1.5 — not max(1.5, 2.0) = 2.0
        let expected = 16.0 + reach.ceil() - reach;
        assert_eq!(
            attr(&text_tag(&svg, "Revenue"), "x"),
            (expected * 100.0).round() / 100.0,
            "title anchored at the bare radius reach, not the halo+stroke term: {svg}"
        );
        assert_eq!(svg.matches("<circle").count(), 3, "one foreground point per datum, no halo overlay: {svg}");
        assert!(!svg.contains("stroke-width=\"2\""), "no halo point layer is drawn at multiplier 0: {svg}");
        assert!(!svg.contains("stroke-width=\"0\""), "no halo line path is drawn at multiplier 0 either: {svg}");
    }

    /// A negative `halo_multiplier` makes production's halo stroke width negative and
    /// its halo symbol size negative, whose Vega path is `MNaN,0`: both are painted as
    /// production writes them, and neither takes the foreground's place.
    #[test]
    fn negative_halo_multiplier_paints_the_negative_stroke_and_nan_halo_points() {
        let mut json = make_line_payload(true, false);
        json["resolved"]["style"]["line_mark"]["halo_multiplier"] = serde_json::json!(-1.0);
        json["resolved"]["style"]["point_mark"] = serde_json::json!({"size": 30.0});
        let svg = render(&serde_json::from_value(json).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains("stroke=\"#ffffff\" stroke-width=\"-4\""), "halo line at -4: {svg}");
        assert_eq!(svg.matches("d=\"MNaN,0\"").count(), 3, "one NaN halo symbol per datum: {svg}");
        assert_eq!(svg.matches("<circle").count(), 3, "the foreground points stay: {svg}");
    }

    /// `left`/`right` labels sit on a `middle` baseline (`_LINE_POS_MAP` gives
    /// them none, which Vega-Lite reads as middle), 3px below `top`'s anchor.
    #[test]
    fn left_and_right_value_labels_take_the_middle_baseline() {
        assert_eq!(crate::common::value_label_anchor("left"), Some(("right", "middle", -4.0, 0.0)));
        assert_eq!(crate::common::value_label_anchor("right"), Some(("left", "middle", 4.0, 0.0)));
    }

    /// `curve: monotone` is d3's `curveMonotoneX`, and the halo line traces
    /// the same curve as the foreground line it sits behind (production pins
    /// `interpolate: fg_interp` on the halo sub-layer).
    #[test]
    fn curve_monotone_draws_cubics_on_both_the_line_and_its_halo() {
        let payload = fit_payload(|j| {
            j["resolved"]["style"]["line_mark"]["curve"] = serde_json::json!("monotone");
        });
        let svg = render(&payload, &test_fonts()).expect("renders");
        let curved: Vec<&str> = svg
            .match_indices("<path d=\"M")
            .map(|(i, _)| svg[i..].split("/>").next().unwrap())
            .filter(|tag| tag.contains("fill=\"none\""))
            .collect();
        assert_eq!(curved.len(), 2, "halo line + main line: {svg}");
        for tag in &curved {
            let d = tag.split("d=\"").nth(1).unwrap().split('"').next().unwrap();
            assert!(d.contains('C') && !d.contains('L'), "cubic, not a polyline: {d}");
        }
    }

    /// A curve outside the `Curve` literals is a payload error: drawing it as
    /// straight segments is a plausible WRONG shape.
    #[test]
    fn unported_curves_are_rejected_instead_of_drawing_straight_segments() {
        let mut json = make_line_payload(false, false);
        json["resolved"]["style"]["line_mark"]["curve"] = serde_json::json!("bundle");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).unwrap_err();
        assert_eq!(err, "line: curve=bundle is none of the Curve literals");
    }

    /// `natural`, `basis` and `cardinal` are d3's splines: cubic segments.
    #[test]
    fn spline_curves_draw_cubic_segments() {
        for curve in ["natural", "basis", "cardinal"] {
            let mut json = make_line_payload(false, false);
            json["resolved"]["style"]["line_mark"]["curve"] = serde_json::json!(curve);
            let payload: Payload = serde_json::from_value(json).unwrap();
            let svg = render(&payload, &test_fonts()).unwrap();
            assert!(svg.contains('C'), "{curve}: {svg}");
        }
    }


    fn set_title_and_style(json: &mut serde_json::Value, subtitle: Option<&str>) {
        json["resolved"]["title"] = serde_json::json!("Revenue");
        json["resolved"]["subtitle"] = serde_json::json!(subtitle);
        json["resolved"]["title_style"] = serde_json::json!({
            "font": {"family": "serif", "size": 18.0, "weight": "600", "color": "#111111"},
            "position": {"offset": 10.0},
            "subtitle": {"font": {"family": TEST_FONT, "size": 12.0, "weight": "400", "color": "#8A8C8F"}}
        });
        json["resolved"]["style"]["title_font"] =
            serde_json::json!({"family": TEST_FONT, "size": 14.0, "weight": "600", "color": "#222222"});
    }

    /// The width-resolved `style.title_font` (14px here) always beats the
    /// un-tiered `title_style.font` (18px serif) for the drawn title.
    #[test]
    fn width_resolved_title_font_beats_title_style_font() {
        let payload = fit_payload(|j| set_title_and_style(j, None));
        let svg = render(&payload, &test_fonts()).expect("renders");
        let before = &svg[..svg.find(">Revenue<").unwrap()];
        let title_tag = &before[before.rfind("<text").unwrap()..];
        assert!(title_tag.contains("font-size=\"14\""), "tiered 14px wins: {title_tag}");
        assert!(!title_tag.contains("font-family=\"serif\""), "tiered family wins: {title_tag}");
    }

    /// Subtitle: drawn one title line + SUBTITLE_PADDING below the title
    /// baseline row, and its ink deepens the fit top margin by
    /// `3 + sub_fs + q(sub) − q(title)` (oracle-pinned in vega_fit tests) —
    /// here 3 + 12 − 1 − 0 = 14 on top of the title-only margin.
    #[test]
    fn subtitle_draws_and_deepens_top_margin() {
        let fonts = test_fonts();
        let title_only = fit_payload(|j| set_title_and_style(j, None));
        let with_sub = fit_payload(|j| set_title_and_style(j, Some("A subtitle line")));
        let base_svg = render(&title_only, &fonts).expect("renders");
        let sub_svg = render(&with_sub, &fonts).expect("renders");
        assert!(sub_svg.contains(">A subtitle line</text>"), "subtitle drawn: {sub_svg}");

        assert_eq!(group_y(&sub_svg) - group_y(&base_svg), 14.0, "margin delta: {sub_svg}");

        // Drawn geometry: subtitle baseline sits title_fs + 3 below the
        // title's top row, shifted by its own top-baseline offset.
        let t_y: f64 = {
            let i = sub_svg.find(">Revenue<").unwrap();
            let tag = &sub_svg[..i];
            let tag = &tag[tag.rfind("<text").unwrap()..];
            tag.split("y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        let s_y: f64 = {
            let i = sub_svg.find(">A subtitle line<").unwrap();
            let tag = &sub_svg[..i];
            let tag = &tag[tag.rfind("<text").unwrap()..];
            tag.split("y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        // title baseline = row + bs(top,14)=11; subtitle = row + 14 + 3 +
        // bs(top,12)=9 → delta = 17 + 9 − 11 = 15.
        assert_eq!(s_y - t_y, 15.0, "subtitle baseline offset: {sub_svg}");
    }

    /// `axis_x.scale.padding` insets the point range at both ends: the first
    /// data point starts at x = padding, not 0.
    #[test]
    fn x_scale_padding_insets_point_range() {
        let fonts = test_fonts();
        let padded = fit_payload(|j| {
            j["resolved"]["style"]["axis_x"]["scale"] = serde_json::json!({"padding": 24.0});
        });
        let svg = render(&padded, &fonts).expect("renders");
        let d_start = svg.find("d=\"M").unwrap();
        let first_x: f64 = svg[d_start + 4..].split(',').next().unwrap().parse().unwrap();
        assert_eq!(first_x, 24.0, "first point inset by scale.padding: {svg}");

        let unpadded = fit_payload(|_| {});
        let svg0 = render(&unpadded, &fonts).expect("renders");
        let d0 = svg0.find("d=\"M").unwrap();
        let first_x0: f64 = svg0[d0 + 4..].split(',').next().unwrap().parse().unwrap();
        assert_eq!(first_x0, 0.0, "no padding → range starts at 0: {svg0}");
    }

    /// Author `style.marks.line.labels` in the shape production compiles a
    /// value-label text layer from (`charts/line-graphs_10`).
    fn set_value_labels(json: &mut serde_json::Value, format: &str) {
        json["resolved"]["style"]["line_mark"]["labels"] = serde_json::json!({
            "visible": true, "position": "top", "format": format,
            "field": null, "dx": null, "dy": null,
            "font": {"family": null, "color": "#6b7280", "size": 11.0}
        });
    }

    /// Oracle: `format_d3(v, ",.1f", "US$", "", None, "before_prefix")`
    /// (`painted_label_text`, `features/value_labels.py`).
    #[test]
    fn a_value_label_prefix_is_painted_and_measured_into_the_fit() {
        let fonts = test_fonts();
        let svg = render(
            &fit_payload(|j| {
                set_value_labels(j, ",.1f");
                j["resolved"]["style"]["line_mark"]["labels"]["format"] =
                    serde_json::json!({"spec": ",.1f", "prefix": "US$", "raw": ",.1f"});
            }),
            &fonts,
        )
        .expect("renders");
        for text in ["US$100.0", "US$150.0", "US$130.0"] {
            text_tag(&svg, text);
        }
    }

    /// `labels.visible: "last"` prints one label per series: the value at its last
    /// datum on the drawn x axis (150 -> 130 here is the fixture's own order).
    #[test]
    fn last_only_value_labels_print_the_final_datum_once() {
        let fonts = test_fonts();
        let svg = render(
            &fit_payload(|j| {
                set_value_labels(j, ",.1f");
                j["resolved"]["style"]["line_mark"]["labels"]["visible"] = serde_json::json!("last");
            }),
            &fonts,
        )
        .expect("renders");
        text_tag(&svg, "130.0");
        for gone in ["100.0", "150.0"] {
            assert!(!svg.contains(&format!(">{gone}<")), "{gone} is not the last datum: {svg}");
        }
    }

    /// The `<text>` y of every value label, in the order drawn.
    fn value_label_ys(svg: &str, texts: &[&str]) -> Vec<f64> {
        texts.iter().map(|t| attr(&text_tag(svg, t), "y")).collect()
    }

    /// L6: `style.marks.line.labels` compiles a VL text layer (`baseline:
    /// bottom`, `dy: -4`) whose ink joins the autosize-fit union on every
    /// edge. Each label draws at `y − 4 + bs(bottom)` = `y − 6` at 11px in
    /// the authored color/format; the first datum's label, centered at
    /// x = 0, overhangs the plot's left edge by half its measured width
    /// (`ceil`'d into the margin, where the bare chart's widest left ink is
    /// the 4px line stroke); and the top datum's label reaches
    /// `4 + |bs(bottom)| + round(0.8·fs)` = 15px above the plot top, past
    /// the top tick label's own 6px half.
    #[test]
    fn value_labels_draw_and_join_the_fit_bounds() {
        let fonts = test_fonts();
        let plain = render(&fit_payload(|_| {}), &fonts).expect("renders");
        let labeled =
            render(&fit_payload(|j| set_value_labels(j, ",.1f")), &fonts).expect("renders");

        let texts = ["100.0", "150.0", "130.0"];
        for text in texts {
            let tag = text_tag(&labeled, text);
            assert!(tag.contains("fill=\"#6b7280\""), "authored color: {tag}");
            assert!(tag.contains("font-size=\"11\""), "authored size: {tag}");
            assert!(tag.contains("text-anchor=\"middle\""), "VL text align center: {tag}");
        }
        let label_ys = value_label_ys(&labeled, &texts);
        let mut point_ys = Vec::new();
        let d_start = labeled.find("d=\"M").unwrap() + 4;
        let d_end = d_start + labeled[d_start..].find('"').unwrap();
        for pair in labeled[d_start..d_end].split(['M', 'L']) {
            point_ys.push(pair.split(',').nth(1).unwrap().parse::<f64>().unwrap());
        }
        assert_eq!(point_ys.len(), 3, "{labeled}");
        for (label_y, point_y) in label_ys.iter().zip(&point_ys) {
            assert_eq!(label_y - point_y, -6.0, "dy − |bs(bottom, 11)|: {labeled}");
        }

        let first_half = fonts.measure_shaped("100.0", TEST_FONT, 11.0).unwrap() / 2.0;
        assert_eq!(plot_group_x(&plain), 20.0, "bare left ink is the 4px stroke: {plain}");
        assert_eq!(
            plot_group_x(&labeled),
            16.0 + first_half.ceil(),
            "the first label's half width becomes left margin: {labeled}"
        );
        assert_eq!(
            group_y(&labeled) - group_y(&plain),
            9.0,
            "the top datum's label ink (15) replaces the tick label's half (6): {labeled}"
        );

        // Right edge: widen the format until the last label's half outruns
        // the value-axis gutter, and the right margin follows it too.
        let wide =
            render(&fit_payload(|j| set_value_labels(j, ",.6f")), &fonts).expect("renders");
        let last_half = fonts.measure_shaped("130.000000", TEST_FONT, 11.0).unwrap() / 2.0;
        let plot_w = path_vertex_xs(&wide).last().copied().unwrap();
        assert_eq!(
            600.0 - 16.0 - 16.0 - (plot_group_x(&wide) - 16.0) - plot_w,
            last_half.ceil(),
            "the last label's half width becomes right margin: {wide}"
        );
    }

    /// The two measured lines production's `title_overflow.py` wraps
    /// `OVERFLOWING_SUBTITLE` onto at the 568px padded-card limit (greedy
    /// word fill, no ellipsis — the whole text fits in two lines).
    const OVERFLOWING_SUBTITLE_LINE_1: &str =
        "Weekly signups across the quarter, measured against the plan and the prior year, \
         with the seasonal";
    const OVERFLOWING_SUBTITLE_LINE_2: &str = "adjustment applied to every bucket.";

    /// L7: a subtitle wider than the padded card wraps to two measured
    /// lines (`title_overflow.py` wrap-two) and Vega's title group grows by
    /// `sub_fs + 2` — the text mark's default lineHeight — per extra line,
    /// on the fit arm rather than dropping to the legacy margins.
    #[test]
    fn an_overflowing_subtitle_wraps_to_two_measured_lines() {
        let fonts = test_fonts();
        let long = format!(
            "{OVERFLOWING_SUBTITLE_LINE_1} {OVERFLOWING_SUBTITLE_LINE_2}"
        );
        // Discriminating: the whole subtitle outruns the 568px padded card
        // at its 12px slot, so production wraps rather than drawing one row.
        assert!(fonts.measure(&long, TEST_FONT, 12.0).unwrap() > 568.0);
        let one_line = render(
            &fit_payload(|j| set_title_and_style(j, Some("A subtitle line"))),
            &fonts,
        )
        .expect("renders");
        let wrapped =
            render(&fit_payload(|j| set_title_and_style(j, Some(&long))), &fonts).expect("renders");

        let first = text_tag(&wrapped, OVERFLOWING_SUBTITLE_LINE_1);
        let second = text_tag(&wrapped, OVERFLOWING_SUBTITLE_LINE_2);
        assert_eq!(attr(&second, "x"), attr(&first, "x"), "lines share the title anchor");
        assert_eq!(attr(&second, "y") - attr(&first, "y"), 14.0, "sub_fs + 2 advance");
        assert_eq!(
            group_y(&wrapped) - group_y(&one_line),
            14.0,
            "the extra line deepens the fit top margin: {wrapped}"
        );
    }

    /// Two long series names, so the rail pane is wide enough that the
    /// pane-0 wrap limit and the card limit are far apart.
    const RAIL_SERIES: [&str; 2] =
        ["Northwest Regional Operations", "Southeast Regional Operations"];
    /// Measures about 520px at the subtitle's 12px slot: under the 568px card
    /// limit (`avail_w`), over the pane-0 limit
    /// (`avail_w − rail_pane_w − label_offset`), so it draws ONE line if the
    /// wrap basis is the card and TWO if it is the pane.
    const RAIL_SUBTITLE: &str =
        "Weekly signups by region, measured against the prior year's own baseline, restated monthly.";
    const RAIL_SUBTITLE_LINE_1: &str =
        "Weekly signups by region, measured against the prior year's own";
    const RAIL_SUBTITLE_LINE_2: &str = "baseline, restated monthly.";

    fn rail_payload(subtitle: &str) -> Payload {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        let [a, b] = RAIL_SERIES;
        p["rows"] = json!([
            {"week": "2026-01-01", "series": a, "signups": 100.0},
            {"week": "2026-01-08", "series": a, "signups": 150.0},
            {"week": "2026-01-01", "series": b, "signups": 40.0},
            {"week": "2026-01-08", "series": b, "signups": 60.0},
        ]);
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        set_title_and_style(&mut p, Some(subtitle));
        serde_json::from_value(p).unwrap()
    }

    /// L7 on the RAIL arm: production re-runs the overflow pass after the
    /// concat correction against pane 0's own footprint
    /// (`converters/chart.py`: `target_width − pane1_width − spacing −
    /// pad_l − pad_r`), not the whole card — the title spans the pane, and
    /// wrapping at the card limit leaves the title block a line short.
    #[test]
    fn the_rail_arm_wraps_the_title_block_at_the_pane_limit_not_the_card() {
        let fonts = test_fonts();
        // Discriminating: between the two limits, so only the pane basis wraps.
        let sub_w = fonts.measure(RAIL_SUBTITLE, "Inter", 12.0).unwrap();
        let wrapped = render(&rail_payload(RAIL_SUBTITLE), &fonts).expect("renders");
        let canvas: f64 = wrapped.split("width=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        let group = wrapped[..wrapped.find("Northwest").unwrap()].rfind("<g transform=\"translate(").unwrap();
        let rail_x: f64 = wrapped[group..].split("translate(").nth(1).unwrap().split(',').next().unwrap().parse().unwrap();
        let rail_pane_w = canvas - 16.0 - rail_x;
        let card_limit = 600.0 - 16.0 - 16.0;
        assert!(sub_w < card_limit, "fits the card at {sub_w}");
        assert!(sub_w > card_limit - (rail_pane_w + 6.0), "outruns the pane limit");

        let one_line = render(&rail_payload("Short."), &fonts).expect("renders");
        let first = text_tag(&wrapped, RAIL_SUBTITLE_LINE_1);
        let second = text_tag(&wrapped, RAIL_SUBTITLE_LINE_2);
        assert_eq!(attr(&second, "x"), attr(&first, "x"), "lines share the title anchor");
        assert_eq!(attr(&second, "y") - attr(&first, "y"), 14.0, "sub_fs + 2 advance");
        assert_eq!(
            group_y(&wrapped) - group_y(&one_line),
            14.0,
            "the extra line deepens the rail arm's top margin: {wrapped}"
        );
    }

    /// L7 on the RAIL arm, the HEIGHT half of the sibling test above:
    /// production's own pane-shrink decision (`_correct_concat_
    /// overshoot`'s height branch, `converters/chart.py`) reads the FIRST
    /// (wide, pre-correction) title/subtitle wrap's chrome — the sibling
    /// test's own "card limit" wrap, which lands `RAIL_SUBTITLE` on ONE
    /// line. Only the SECOND (narrow, pane-limit) wrap — TWO lines, same
    /// sibling test — ever paints, so the shrink leaves the pane one whole
    /// subtitle line (`fontSize + 2` = 14.0 here) short of what the real
    /// render needs; the correction is single-pass (`docs/laws-line-
    /// area.md`'s rail-concat height section) and accepts the residual
    /// rather than re-running. `charts/extensibility_1`'s `slope` chart is
    /// the corpus row this fires on — production renders it 295.667 tall
    /// for a requested 282.667 at width 376 (`bump`, same board, whose
    /// longer subtitle already wraps to two lines at BOTH widths, stays
    /// flat at 282.667). Reproduced here with this file's own rail
    /// fixture: mutation-pinned — reverting `plot_h`'s `title_stack_probe`
    /// read (or `canvas_h`'s own growth line) back to the plain `title_
    /// stack`/`svg_h` this test was written against drops both
    /// assertions.
    #[test]
    fn the_rail_arm_grows_past_its_slot_when_the_pane_wrap_adds_a_line() {
        let fonts = test_fonts();
        let requested_height = rail_payload(RAIL_SUBTITLE).height;
        assert_eq!(requested_height, 400.0);

        let mut measuring = rail_payload(RAIL_SUBTITLE);
        measuring.measuring = true;
        let wrapped = render(&measuring, &fonts).expect("renders");
        assert!(
            wrapped.contains("height=\"414.0\""),
            "grows by exactly one subtitle line (400 + fontSize(12) + 2) \
             on the sizing pass's own measurement call: {wrapped}"
        );

        // A board paints each chart exactly once, directly at its already-
        // decided slot height (`Payload::measuring`'s own doc comment) — it
        // never re-measures. Re-running this SAME payload at the GROWN
        // 414.0, with `measuring` at its real (board-paint) default of
        // `false`, must land back on exactly 414.0, not grow a second time.
        let mut painted = rail_payload(RAIL_SUBTITLE);
        painted.height = 414.0;
        let repainted = render(&painted, &fonts).expect("renders");
        assert!(
            repainted.contains("height=\"414.0\""),
            "a board paint of the already-grown slot height is idempotent, \
             not a second measurement: {repainted}"
        );

        // Mutation guard: a subtitle short enough that the wide pre-
        // correction wrap and the narrow pane wrap agree (both one line)
        // never disagrees, so the pane-shrink decision already matches
        // the real chrome and the canvas stays at the requested height —
        // on the measuring pass too.
        let mut flat = rail_payload("Short.");
        flat.measuring = true;
        let flat_svg = render(&flat, &fonts).expect("renders");
        assert!(
            flat_svg.contains("height=\"400.0\""),
            "no wrap disagreement, no growth: {flat_svg}"
        );
    }

    /// The `top_ink` rebind law, pinned against a real corpus payload: the
    /// corpus `themes/4` board's own `trend` chart — a captured `board_
    /// sweep.py` payload (`resolved`/`rows` verbatim, only
    /// the duplicate `resolved.query` block trimmed), no title/subtitle
    /// disagreement in play (`title_stack_probe == title_stack` — this
    /// chart's short, single-line title wraps identically at both bases).
    /// Requested/declared height 432; production renders it 426 — `top_ink`
    /// is a property of the FINAL, corrected-height render's own y-tick
    /// mask (`mask_final`, `plot_h`), not the probe's wider one
    /// (`svg_h`), so the canvas comes out shorter even with no wrap
    /// disagreement at all. Mutation-pinned: reading `chrome_h_final`'s
    /// `top_ink` from `mask_probe`/`svg_h` instead of the rebind
    /// (`top_ink_at(&mask_final, &tick_values, plot_h)`) drops this back
    /// to 432.0.
    const THEMES_4_TREND_JSON: &str =
        include_str!("../../../fixtures/rail_concat/themes_4_trend.json");

    #[test]
    fn the_rail_arm_s_canvas_follows_the_final_render_s_own_top_ink_not_the_probe_s() {
        let fonts = test_fonts();
        let payload: Payload = serde_json::from_str(THEMES_4_TREND_JSON).unwrap();
        assert_eq!(payload.height, 432.0, "the board's own requested height");
        let svg = render(&payload, &fonts).expect("renders");
        assert!(
            svg.contains("height=\"426.0\""),
            "the final ladder's own top_ink shrinks the canvas below the \
             requested 432, matching production: {svg}"
        );
    }

    /// The temporal fit path positions by the UTC time scale: with monthly
    /// buckets Jan/Feb/Mar, Feb sits at 31/59 of the span (spans 31 and 28
    /// days), not the point-scale midpoint.
    #[test]
    fn temporal_x_positions_are_time_proportional() {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = make_line_payload(true, false);
        json["rows"] = serde_json::json!([
            {"week": "2026-01-01", "signups": 100.0},
            {"week": "2026-02-01", "signups": 150.0},
            {"week": "2026-03-01", "signups": 130.0},
        ]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let d_start = svg.find("d=\"M").unwrap();
        let d = &svg[d_start + 3..svg[d_start..].find(' ').unwrap() + d_start + 40];
        let mut xs = Vec::new();
        for seg in svg[d_start + 4..].split('"').next().unwrap().split(['M', 'L']) {
            if seg.is_empty() {
                continue;
            }
            xs.push(seg.split(',').next().unwrap().parse::<f64>().unwrap());
        }
        assert_eq!(xs.len(), 3, "three vertices: {d}");
        let frac = (xs[1] - xs[0]) / (xs[2] - xs[0]);
        assert!(
            (frac - 31.0 / 59.0).abs() < 1e-6,
            "Feb vertex at 31/59 of the span, got {frac}: {svg}"
        );
    }

    /// Vega-Lite sorts a line's vertices along its temporal x, so query row
    /// order never reaches the path.
    #[test]
    fn temporal_line_walks_vertices_in_time_order_not_row_order() {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = make_line_payload(true, false);
        json["rows"] = serde_json::json!([
            {"week": "2026-03-01", "signups": 130.0},
            {"week": "2026-01-01", "signups": 100.0},
            {"week": "2026-02-01", "signups": 150.0},
        ]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let xs = path_vertex_xs(&svg);
        assert_eq!(xs.len(), 3, "{svg}");
        assert!(xs.windows(2).all(|w| w[0] < w[1]), "vertices ascend in x: {xs:?}");
    }

    /// A linked line's path carries its first datum in time order.
    #[test]
    fn temporal_line_path_links_its_first_datum_in_time_order() {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = make_line_payload(true, false);
        json["resolved"]["link"] = serde_json::json!("/p/{{ y }}");
        json["resolved"]["link_open"] = serde_json::json!("page");
        json["rows"] = serde_json::json!([
            {"week": "2026-03-01", "signups": 130.0},
            {"week": "2026-01-01", "signups": 100.0},
            {"week": "2026-02-01", "signups": 150.0},
        ]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let path_at = svg.find("d=\"M").unwrap();
        let href = svg[..path_at].rsplit("href=\"").next().unwrap().split('"').next().unwrap();
        assert_eq!(href, "/p/100", "{svg}");
    }

    /// A stacked area's edges also walk its temporal x in time order.
    #[test]
    fn temporal_stacked_area_walks_vertices_in_time_order_not_row_order() {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = make_stacked_area_payload("zero");
        json["rows"] = serde_json::json!([
            {"week": "2026-01-15", "series": "A", "signups": 120.0},
            {"week": "2026-01-01", "series": "A", "signups": 100.0},
            {"week": "2026-01-08", "series": "A", "signups": 150.0},
            {"week": "2026-01-08", "series": "B", "signups": 60.0},
            {"week": "2026-01-15", "series": "B", "signups": 50.0},
            {"week": "2026-01-01", "series": "B", "signups": 40.0},
        ]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render_inner(&payload, &test_fonts(), true).expect("renders");
        let edges = stacked_area_edges(&svg);
        assert_eq!(edges.len(), 2, "{svg}");
        for (top, _) in &edges {
            assert!(top.windows(2).all(|w| w[0].0 < w[1].0), "top edge ascends in x: {top:?}");
        }
    }

    /// Every temporal x value is parsed once; one that is no date refuses by name.
    #[test]
    fn a_temporal_x_value_that_does_not_parse_is_refused_by_name() {
        let p = plain_x_curve_payload(&["2024-01-01", "nope", "2024-03-01"], "linear", Some("temporal"));
        let err = render(&serde_json::from_value(p).unwrap(), &test_fonts()).expect_err("an unparseable temporal x refuses");
        let text = err.to_string();
        assert!(text.contains("ERR-X-VALUE-NOT-A-DATE"), "{text}");
        for param in ["chart_id=\"line\"", "x_field=\"week\"", "value=\"nope\""] {
            assert!(text.contains(param), "{param}: {text}");
        }
    }

    /// The last x is the latest instant, not the greatest text: `13:00+05:00` is 08:00Z.
    #[test]
    fn mixed_offset_timestamps_rank_last_x_by_instant() {
        use serde_json::json;
        let rows = json!([
            {"week": "2022-01-01T12:00:00+00:00", "series": "B", "signups": 2.0},
            {"week": "2022-01-01T13:00:00+05:00", "series": "A", "signups": 5.0},
            {"week": "2022-01-01T13:00:00+05:00", "series": "B", "signups": 3.0},
            {"week": "2022-01-01T12:00:00+00:00", "series": "A", "signups": 1.0},
        ]);
        let mut legend = make_multi_series_line_payload_with_bottom_legend();
        legend["rows"] = rows.clone();
        legend["resolved"]["style"]["axis_x"]["is_quantitative"] = json!(false);
        let svg = render(&serde_json::from_value::<Payload>(legend).unwrap(), &test_fonts()).expect("renders");
        let label_at = |svg: &str, name: &str| svg.rfind(&format!(">{name}<")).unwrap_or_else(|| panic!("{name} missing: {svg}"));
        assert!(label_at(&svg, "B") < label_at(&svg, "A"), "legend lists the larger last value (B=2 at 12:00Z) first: {svg}");
        let xs = path_vertex_xs(&svg);
        assert!(xs.windows(2).all(|w| w[0] < w[1]), "path ascends: {xs:?}");

        let mut strip = make_rail_strip_payload();
        strip["rows"] = rows;
        strip["resolved"]["style"]["axis_x"]["is_quantitative"] = json!(false);
        strip["resolved"]["support_table"] = json!({"entries": [{"per_series": "signups", "format": ",d"}]});
        let svg = render(&serde_json::from_value::<Payload>(strip).unwrap(), &test_fonts()).expect("renders");
        assert!(label_at(&svg, "A") < label_at(&svg, "B"), "strip rows ascend by the value at 12:00Z (A=1, B=2): {svg}");
    }

    /// A layer's labeled bucket joins the base's by its date, not by where it was listed.
    #[test]
    fn a_layer_only_labeled_bucket_merges_by_date() {
        let mut p = make_layered_line_payload();
        p["resolved"]["layers"] = serde_json::json!([line_layer_json(Some("trend_query"))]);
        p["rows"] = serde_json::json!([
            {"week": "2024-Q1", "signups": 100.0},
            {"week": "2024-Q2", "signups": 150.0},
            {"week": "2024-Q3", "signups": 130.0},
        ]);
        p["datasets"] = serde_json::json!({
            "trend_query": [
                {"week": "2024-Q1", "trend": 110.0},
                {"week": "2023-Q4", "trend": 90.0},
                {"week": "2024-Q2", "trend": 140.0}
            ]
        });
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let paths: Vec<Vec<f64>> = svg
            .match_indices("<path d=\"M")
            .map(|(i, _)| &svg[i..])
            .filter(|tag| {
                let tag = tag.split("/>").next().unwrap();
                tag.contains("fill=\"none\"") && !tag.contains("stroke=\"#ffffff\"")
            })
            .map(|tag| {
                tag["<path d=\"M".len()..]
                    .split('"')
                    .next()
                    .unwrap()
                    .split(['M', 'L'])
                    .map(|pair| pair.split(',').next().unwrap().parse().unwrap())
                    .collect()
            })
            .collect();
        assert_eq!(paths.len(), 2, "base and layer paths: {svg}");
        for xs in &paths {
            assert!(xs.windows(2).all(|w| w[0] < w[1]), "ascends: {paths:?}");
        }
        assert!(paths[1][0] < paths[0][0], "2023-Q4 sits left of the base's first bucket: {paths:?}");
    }

    /// L9: on a weekly (`utcyearweek`) x axis the LINE'S OWN vertices always
    /// float to the Sunday week start — but whether the LABELS follow the
    /// raw (unfloored) date or the same floored position depends on the
    /// resolved label cadence. Native (every-bucket) cadence emits no
    /// opener `values` at all (a plain `tickCount` interval), so Vega's
    /// ticks sit at the floored positions, matching the vertices exactly —
    /// this test. The opener-cadence case (raw dates, off-vertex, possibly
    /// dropped past the floored domain end) is the next test.
    #[test]
    fn weekly_native_labels_flush_at_the_floored_vertex_not_the_raw_date() {
        // Few weeks, wide plot: native (every-bucket) cadence fits flat, so
        // production emits NO opener `values` — just a `tickCount` interval
        // — and Vega's own ticks sit at the scale's native (floored)
        // positions, exactly matching the vertices. Confirmed against
        // `time-unit-label-cadence-matrix/week_auto_native`'s reference: 5
        // weeks flush their first tick at the range start.
        let mut json = make_line_payload(true, false);
        json["rows"] = serde_json::json!([
            {"week": "2025-09-08", "signups": 100.0},
            {"week": "2025-09-15", "signups": 105.0},
            {"week": "2025-09-22", "signups": 110.0},
            {"week": "2025-09-29", "signups": 115.0},
            {"week": "2025-10-06", "signups": 120.0},
            {"week": "2025-10-13", "signups": 125.0},
            {"week": "2025-10-20", "signups": 130.0},
        ]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");

        // The line's own first vertex sits at the floored domain start.
        let d_start = svg.find("d=\"M").unwrap();
        let first_vertex = &svg[d_start + 4..svg[d_start..].find(',').unwrap() + d_start];
        assert_eq!(first_vertex, "0", "first vertex flush at the floored domain start: {svg}");

        // The label for that same row ("8", the day-of-month) IS flushed —
        // native cadence has no raw-date opener shift to center it off.
        let tag = text_tag(&svg, "8");
        assert!(tag.contains("text-anchor=\"start\""), "native cadence flushes, no raw-date shift: {tag}");
        assert!(attr(&tag, "x") <= 1.0, "at the plot's left edge: {tag}");

        // Every bucket gets a label under native cadence — nothing is
        // off-domain (a floored opener's position IS its own vertex).
        assert!(svg.contains(">20</text>"), "native cadence draws every bucket: {svg}");
    }

    /// L9's raw-date placement is gated to OPENER cadence — production only
    /// emits an explicit `values` array (raw, unfloored dates) when the
    /// resolved label grain is coarser than the data grain (the render-local
    /// visibility thinning at `type_inference.py:852-882`, condition at
    /// 870-871); a wide domain
    /// that coarsens to monthly openers (unlike the native-cadence test
    /// above) exercises that path. 39 Mondays from 2025-09-08: the domain's
    /// OWN last row (2026-06-01) is itself a month opener (day 1 ≤ 7), so
    /// its raw date outruns the Sunday-floored domain end by exactly one
    /// day and Vega drops it — the off-domain case; "Oct" (idx 4, the first
    /// month whose Monday falls in its first 7 days) is the first VISIBLE
    /// opener, and its raw date is not the domain's own start, so it isn't
    /// flushed AND its x sits strictly right of vertex 4 (the raw-date
    /// shift) — not merely "off the left edge", which the floored
    /// placement satisfies too and would not catch L9 being deleted.
    #[test]
    fn weekly_openers_sit_on_the_raw_dates_and_the_first_is_not_flushed() {
        use crate::families::temporal::{civil_from_days, days_from_civil};
        let base = days_from_civil(2025, 9, 8); // Monday
        let rows: Vec<serde_json::Value> = (0..39)
            .map(|i| {
                let (y, m, d) = civil_from_days(base + i * 7);
                serde_json::json!({"week": format!("{y:04}-{m:02}-{d:02}"), "signups": 100.0 + i as f64})
            })
            .collect();
        let mut json = make_line_payload(true, false);
        json["rows"] = serde_json::Value::Array(rows);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");

        let vertices = path_vertex_xs(&svg);
        assert_eq!(vertices[0], 0.0, "vertex still flush at the floored domain start: {svg}");

        // The first VISIBLE opener ("Oct", domain_idx 4) is NOT flushed —
        // its raw date isn't the domain's own start, so Vega centers it —
        // and its x sits strictly right of ITS OWN vertex: the raw-date
        // shift the floored placement would not produce.
        let tag = text_tag(&svg, "Oct");
        assert!(tag.contains("text-anchor=\"middle\""), "off-start opener centers: {tag}");
        assert!(
            attr(&tag, "x") > vertices[4] + 1.0,
            "opener sits right of its own (floored) vertex — the raw-date shift: label {} vs vertex {}: {svg}",
            attr(&tag, "x"),
            vertices[4]
        );

        // The domain's own last row (2026-06-01, itself a month opener)
        // outruns the floored domain end by one day — Vega drops it, so
        // "Jun" never appears, while "May" (the prior, in-domain opener)
        // still does.
        assert!(!svg.contains(">Jun<"), "off-domain opener is dropped: {svg}");
        assert!(svg.contains(">May<"), "the prior, in-domain opener still draws: {svg}");
    }

    /// The rail-concat arm's own opener-cadence path — same law, same
    /// fixture shape as the fit-arm test above, but through
    /// `rail_raw_openers` (not `fit_raw_openers`) and the rail arm's own
    /// `right_ink` overhang machinery, which also reads `label_extent` via
    /// `opener_frac_at`.
    #[test]
    fn rail_weekly_openers_sit_on_the_raw_dates_and_the_first_is_not_flushed() {
        use crate::families::temporal::{civil_from_days, days_from_civil};
        let base = days_from_civil(2025, 9, 8); // Monday
        let rows: Vec<serde_json::Value> = (0..39)
            .map(|i| {
                let (y, m, d) = civil_from_days(base + i * 7);
                serde_json::json!({"week": format!("{y:04}-{m:02}-{d:02}"), "series": "A", "signups": 100.0 + i as f64})
            })
            .collect();
        let mut json = make_multi_series_line_payload_with_bottom_legend();
        json["rows"] = serde_json::Value::Array(rows);
        json["resolved"]["style"]["endpoint_labels"] = serde_json::json!({"visible": true, "label_offset": 6.0});
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");

        let vertices = path_vertex_xs(&svg);
        assert_eq!(vertices[0], 0.0, "vertex still flush at the floored domain start: {svg}");

        let tag = text_tag(&svg, "Oct");
        assert!(tag.contains("text-anchor=\"middle\""), "off-start opener centers: {tag}");
        assert!(
            attr(&tag, "x") > vertices[4] + 1.0,
            "opener sits right of its own (floored) vertex — the raw-date shift: label {} vs vertex {}: {svg}",
            attr(&tag, "x"),
            vertices[4]
        );

        assert!(!svg.contains(">Jun<"), "off-domain opener is dropped: {svg}");
        assert!(svg.contains(">May<"), "the prior, in-domain opener still draws: {svg}");
    }

    /// RL1: Vega-Lite's `labelFlush` default only flushes a CONTINUOUS x
    /// scale's edge labels — a nominal/point scale is never flushed, so the
    /// last label stays centered on its band and its half-width overhangs
    /// pane 0 unconditionally (no authored `labels.align` needed; unlike
    /// L8's temporal case, an authored align on this axis type refuses
    /// upstream, so this is the axis's only overhang term). Reference
    /// `quick-guide_7/target_by_month`: pane 0 373, bounded by "Mar"'s
    /// centered half-width, not the mark reach alone.
    fn nominal_rail_payload(mutate: impl FnOnce(&mut serde_json::Value)) -> Payload {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"month": "Jan", "series": "A", "signups": 40.0},
            {"month": "Feb", "series": "A", "signups": 120.0},
            {"month": "March Openings", "series": "A", "signups": 150.0},
            {"month": "Jan", "series": "B", "signups": 20.0},
            {"month": "Feb", "series": "B", "signups": 60.0},
            {"month": "March Openings", "series": "B", "signups": 90.0},
        ]);
        p["resolved"]["x"] = json!("month");
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        // `rail_plot_w` reads pane 0's width off a drawn y gridline.
        p["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(true);
        mutate(&mut p);
        serde_json::from_value(p).unwrap()
    }

    #[test]
    fn nominal_x_last_label_overhang_bounds_pane_zero_unconditionally() {
        let fonts = test_fonts();
        let plot_w_for = |last_label: &str| -> f64 {
            let payload = nominal_rail_payload(|p| {
                p["rows"][2]["month"] = serde_json::json!(last_label);
                p["rows"][5]["month"] = serde_json::json!(last_label);
            });
            let svg = render(&payload, &fonts).expect("renders");
            rail_plot_w(&svg)
        };
        let short = plot_w_for("A");
        let long = plot_w_for("A Very Long Trailing Category Label");
        assert!(
            long < short - 50.0,
            "a nominal x's centered LAST label overhangs pane 0 whether or not any \
             labels.align is authored (unlike a temporal axis, which is flushed by \
             default): short={short}, long={long}"
        );
        // every other label-ink union in this file clamps at
        // `x_limit` (`labels.max_width` or the 180px default) — Vega
        // bounds the mark at its limit, not at the untruncated string
        // it draws. Two labels straddling the limit (188px and well
        // past it) must reserve the SAME ink once both are clamped.
        let over_limit = plot_w_for("A Very Long Trailing Category Label");
        let further_over_limit =
            plot_w_for("A Very Long Trailing Category Label That Keeps Going Further Still");
        assert_eq!(
            over_limit, further_over_limit,
            "two last labels past the 180px limit must clamp to the same reserved \
             ink, not grow pane 0's deficit with the raw string width: \
             over_limit={over_limit}, further_over_limit={further_over_limit}"
        );
    }

    /// The last centered x label bounds pane 0 at `position + w / 2`: Vega
    /// does not add the axis group's 0.5 crisp translate to a label's
    /// bound (`vl_probe`: the hconcat rail steps at `k - w / 2`, not
    /// `k - w / 2 - 0.5`; `tests/test_vl_probe.py`). The labels below put
    /// `w / 2` in the upper half of a pixel, where a padded reach lifts
    /// the ceil that places the rail.
    #[test]
    fn nominal_x_last_label_reach_is_its_half_width_without_the_axis_translate() {
        let fonts = test_fonts();
        let mut discriminating = 0;
        for last in ["Mar", "Apr", "Jun", "Q3", "Sept", "After", "Later", "Total"] {
            let payload = nominal_rail_payload(|p| {
                p["rows"][2]["month"] = serde_json::json!(last);
                p["rows"][5]["month"] = serde_json::json!(last);
            });
            let svg = render(&payload, &fonts).expect("renders");
            let tag = text_tag(&svg, last);
            let family = tag.split("font-family=\"").nth(1).unwrap().split('"').next().unwrap();
            let size = attr(&tag, "font-size");
            let half = fonts.measure(last, &family.replace("&quot;", "\""), size).unwrap() / 2.0;
            let plot_w = rail_plot_w(&svg);
            let (rail_x, _) = rail_x_and_canvas(&svg);
            let rail_rel = rail_x - plot_group_x(&svg);
            assert_eq!(
                rail_rel,
                (plot_w + half).ceil() + 6.0,
                "{last}: half={half} plot_w={plot_w}"
            );
            if half.fract() > 0.5 {
                discriminating += 1;
            }
        }
        assert!(discriminating > 0, "no label put w / 2 in a pixel's upper half");
    }

    /// The L8 site: an authored `labels.align` on a temporal x axis bounds
    /// pane 0 at the last label's `position + w / 2` (centered) or
    /// `position + w` (left-aligned) — the label's geometry at the scale
    /// position, with no axis-group translate in the bound. The week-ending
    /// days below put `w / 2` or `w` in a pixel's upper half, where a padded
    /// reach lifts the ceil that places the rail.
    #[test]
    fn authored_align_temporal_last_label_reach_has_no_axis_translate() {
        use serde_json::json;
        let fonts = test_fonts();
        let sundays = ["2026-01-04", "2026-01-11", "2026-01-18", "2026-01-25"];
        let mut discriminating = 0;
        for (align, weeks, last_day) in [
            ("center", 3, "18"),
            ("center", 4, "25"),
            ("left", 3, "18"),
            ("left", 4, "25"),
        ] {
            let mut p = make_multi_series_line_payload_with_bottom_legend();
            p["rows"] = sundays[..weeks]
                .iter()
                .enumerate()
                .flat_map(|(i, week)| {
                    ["A", "B"].map(|series| {
                        json!({"week": week, "series": series, "signups": 10.0 + 7.0 * i as f64})
                    })
                })
                .collect();
            p["resolved"]["legend"]["visible"] = json!(false);
            p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
            p["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(true);
            p["resolved"]["style"]["axis_x"]["labels"]["align"] = json!(align);
            let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &fonts).expect("renders");
            let tag = text_tag(&svg, last_day);
            let family = tag.split("font-family=\"").nth(1).unwrap().split('"').next().unwrap();
            let width = fonts
                .measure(last_day, &family.replace("&quot;", "\""), attr(&tag, "font-size"))
                .unwrap();
            let reach = if align == "center" { width / 2.0 } else { width };
            let plot_w = rail_plot_w(&svg);
            let (rail_x, _) = rail_x_and_canvas(&svg);
            let rail_rel = rail_x - plot_group_x(&svg);
            assert_eq!(
                rail_rel,
                (plot_w + reach).ceil() + 6.0,
                "{align} {last_day}: reach={reach} plot_w={plot_w}"
            );
            if reach.fract() > 0.5 {
                discriminating += 1;
            }
        }
        assert!(discriminating > 0, "no label put its reach in a pixel's upper half");
    }

    /// MEDIUM-1: RL1's last-label position is `PointScale::position_at(n-1)`,
    /// not a hard-coded range end — with a single x category the point scale
    /// steps 0.0 and the emission draws the only label at the LEFT edge
    /// (`x_scale_pad`), with no right overhang at all. A range-end
    /// assumption would still reserve ink for a label that never draws
    /// anywhere near the right edge.
    #[test]
    fn a_single_category_x_s_only_label_sits_at_the_left_edge_not_the_range_end() {
        let fonts = test_fonts();
        let plot_w_for = |label: &str| -> f64 {
            let p = nominal_rail_payload(|p| {
                p["rows"] = serde_json::json!([
                    {"month": label, "series": "A", "signups": 40.0},
                    {"month": label, "series": "B", "signups": 20.0},
                ]);
            });
            let svg = render(&p, &fonts).expect("renders");
            rail_plot_w(&svg)
        };
        // Both labels stay under the 180px `x_limit`, so HIGH-1's clamp
        // cannot be the reason these come out equal.
        let short = plot_w_for("A");
        let long = plot_w_for("A Moderately Long Only Category");
        assert_eq!(
            short, long,
            "a single-category domain's only label sits at the LEFT edge \
             (`PointScale`'s step is 0.0), so its width must not narrow pane 0 \
             as if the label overhung the right edge the way a range-end \
             assumption would: short={short}, long={long}"
        );
    }

    /// L8: authored `axis_x.labels.align` sets every x label's SVG
    /// text-anchor (Vega's `labelAlign`, applied to every label — not just
    /// the flush edges) on both Vega arms; the rail arm has no generic
    /// ink-union, so the last label's overhang joins pane0's right ink
    /// closed-form and the rail pane follows it out.
    #[test]
    fn authored_labels_align_anchors_every_label_and_widens_the_rail() {
        use serde_json::json;
        // Sunday-anchored rows (unlike the L9 test's Mondays) so every raw
        // date equals its own floored week start — no L9 opener drop to
        // entangle with this L8-only assertion.
        let weekly_sundays = json!([
            {"week": "2026-01-04", "series": "A", "signups": 12.0},
            {"week": "2026-01-11", "series": "A", "signups": 30.0},
            {"week": "2026-01-18", "series": "A", "signups": 60.0},
        ]);
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = weekly_sundays;
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        p["resolved"]["style"]["axis_x"]["labels"]["align"] = json!("left");
        let baseline = Payload::deserialize(&p).unwrap();
        let baseline_svg = render(&baseline, &test_fonts()).expect("renders");
        let (baseline_gx, _, baseline_end) = plot_origin_and_line_end(&baseline_svg);

        // Every temporal label anchors "start" — including the interior
        // and last ones the flush edges would otherwise center/flush-right.
        for label in ["4", "11", "18"] {
            let tag = text_tag(&baseline_svg, label);
            assert!(tag.contains("text-anchor=\"start\""), "{label} anchors start: {tag}");
        }

        // Without the authored align, the last label's own ink barely
        // overhangs the plot (Vega flushes it right instead) — the rail
        // arm's right chrome is narrower, so pane0 (the plot width) is
        // WIDER than under the authored left-align, whose full-width last
        // label overhangs much further.
        p["resolved"]["style"]["axis_x"]["labels"]["align"] = json!(null);
        let unaligned = serde_json::from_value::<Payload>(p).unwrap();
        let unaligned_svg = render(&unaligned, &test_fonts()).expect("renders");
        let (unaligned_gx, _, unaligned_end) = plot_origin_and_line_end(&unaligned_svg);
        let baseline_plot_w = baseline_end - baseline_gx;
        let unaligned_plot_w = unaligned_end - unaligned_gx;
        assert!(
            unaligned_plot_w > baseline_plot_w,
            "left-aligned labels' overhang narrows pane0: {baseline_plot_w} vs {unaligned_plot_w}"
        );
    }

    /// L10(a): the rail arm's x-label baseline is the SAME Vega formula the
    /// fit arm uses (`plot_h + offset 1 + 0.5 + tick + pad + round(0.79·fs)`),
    /// not the legacy `xsz*0.8` expression — the two arms must land on the
    /// exact same y for equal inputs.
    #[test]
    fn rail_arm_x_label_baseline_matches_the_fit_arm_formula() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        let rail_payload = serde_json::from_value::<Payload>(p).unwrap();
        let rail_svg = render(&rail_payload, &test_fonts()).expect("renders");
        // `plot_h_from_tick` reads the x-axis tick's y1, the plot's bottom edge.
        let tick_y = plot_h_from_tick(&rail_svg);
        let label_y = attr(&text_tag(&rail_svg, "1"), "y");
        let expected = tick_y + 1.0 + 0.5 + 6.0 + 6.0 + measure::baseline_shift("top", 11.0);
        assert!(
            (label_y - expected).abs() < 1e-6,
            "rail label baseline {label_y} != fit-arm formula {expected}: {rail_svg}"
        );
    }

    /// A multi-series (rail-firing) weekly payload with `overlap: {skip,
    /// tilt}` authored — the rail-concat sibling of `make_weekly_tilt_payload`
    /// above (a weekly x crossed with a `color` series, endpoint-label rail
    /// forcing the hconcat arm).
    fn make_weekly_tilt_rail_payload(width: f64, height: f64) -> serde_json::Value {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        let rows: Vec<serde_json::Value> = (0..5)
            .flat_map(|i| {
                let day = 1 + 7 * i;
                [
                    json!({"week": format!("2024-01-{day:02}"), "series": "A", "signups": 100.0 + i as f64}),
                    json!({"week": format!("2024-01-{day:02}"), "series": "B", "signups": 50.0 + i as f64}),
                ]
            })
            .collect();
        p["rows"] = json!(rows);
        p["width"] = json!(width);
        p["height"] = json!(height);
        let ax = &mut p["resolved"]["style"]["axis_x"]["labels"];
        ax["overlap"] = json!({"skip": true, "tilt": true});
        ax["tilt_increments"] = json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        ax["min_gap"] = json!(16.0);
        p
    }

    /// HIGH: the rail-concat arm must draw the SAME tilted temporal x axis
    /// the fit arm already does, not refuse it. Mirrors the fit arm's own
    /// `narrow_weekly_tilt_rotates_at_vega_s_normalized_middle_baseline`/
    /// `parity_hides_label_text_but_not_ticks_or_gridlines`.
    #[test]
    fn rail_arm_renders_a_tilted_temporal_x_axis() {
        let p = make_weekly_tilt_rail_payload(110.0, 300.0);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains("rotate(-90)"), "expected a -90 tilt: {svg}");
        let dy = svg
            .split("rotate(-90) translate(0,")
            .nth(1)
            .unwrap()
            .split(')')
            .next()
            .unwrap()
            .parse::<f64>()
            .unwrap();
        assert_eq!(dy, 3.0, "middle baseline (round(0.30*11)), not top's 9: {svg}");
        let visible_labels = svg.matches("<text text-anchor=").count();
        assert!(visible_labels > 0 && visible_labels < 5, "parity must thin some labels ({visible_labels} of 5): {svg}");
    }

    /// HIGH-2 regression: Vega's runtime `labelOverlap: parity` decision
    /// is evaluated against the CORRECTED pane width the concat's overshoot
    /// correction lands on, not L10's probe width (`payload.width`) — a
    /// STATIC, Python-side pre-computation for the cadence/tilt CHOICE
    /// only. At `payload.width` every weekly opener clears `min_gap`; at
    /// the corrected (narrower) `plot_w` parity needs two halvings (every
    /// 4th). This pins the DRAWN SET at the corrected width, not merely a
    /// count that passes at either cadence.
    #[test]
    fn rail_arm_parity_runs_at_the_corrected_width_not_the_probe_width() {
        let p = make_weekly_tilt_rail_payload(110.0, 300.0);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let opened: Vec<&str> = svg
            .match_indices("rotate(-90) translate(0,3)\"")
            .map(|(i, _)| {
                let tag = &svg[i..];
                let text = &tag[tag.find('>').unwrap() + 1..];
                &text[..text.find("</text>").unwrap()]
            })
            .collect();
        assert_eq!(
            opened,
            vec!["1 Jan'24", "29"],
            "the corrected-width cadence (every 4th opener), not the wider probe's every-2nd: {svg}"
        );
    }

    /// The bottom band's tilt term is the widest VISIBLE label's own
    /// rotated ink (`resolve_tilted_temporal_label` + `vega_fit::text_ink`/
    /// `Bounds::rotate`, the SAME primitives the fit arm's ink union
    /// carries) — not the flat font-height term the untilted rail arm
    /// still uses. A wide pane (flat, two-row opener) and a narrow one
    /// (steepest -90 tilt) share every other chrome term (no title, no
    /// legend, same y data, same height), so `plot_h`'s difference must
    /// equal the two `x_band` tail terms' own difference: dropping the
    /// rotated-extent term (reverting to the flat `x_font_size` reservation)
    /// collapses this delta to ~13px instead of ~30px.
    #[test]
    fn rail_arm_tilt_grows_the_bottom_band_by_the_rotated_label_extent() {
        let fonts = test_fonts();
        let wide = make_weekly_tilt_rail_payload(3000.0, 300.0);
        let narrow = make_weekly_tilt_rail_payload(110.0, 300.0);
        let wide_svg = render(&serde_json::from_value::<Payload>(wide).unwrap(), &fonts).expect("renders");
        let narrow_svg = render(&serde_json::from_value::<Payload>(narrow).unwrap(), &fonts).expect("renders");
        assert!(!wide_svg.contains("rotate("), "wide pane stays flat: {wide_svg}");
        assert!(narrow_svg.contains("rotate(-90)"), "narrow pane tilts: {narrow_svg}");

        // Widest VISIBLE narrow-pane label sets the rotated extent — at
        // exactly -90° with a "right"/"middle" anchor, `Bounds::rotate`
        // collapses the box's own font-size term to zero, so the reserved
        // extent equals the label's measured WIDTH exactly (verified via
        // the primitives directly, not asserted as a literal).
        let widest_w = narrow_svg
            .match_indices("<text text-anchor=\"end\" transform=\"translate(")
            .map(|(i, _)| narrow_svg[i..].split("</text>").next().unwrap())
            .map(|tag| tag.rsplit('>').next().unwrap())
            .map(|label| fonts.measure(label, TEST_FONT, 11.0).unwrap())
            .fold(0.0_f64, f64::max);
        assert!(widest_w > 20.0, "the opener's inlined day+context label must be the widest: {widest_w}");
        let extent = crate::vega_fit::text_ink(0.0, 0.0, widest_w, 11.0, "right", "middle")
            .rotate(-90.0, 0.0, 0.0)
            .y2;
        assert!((extent - widest_w).abs() < 1e-9, "a -90 tilt's extent is exactly the label width: {extent}");

        let x_band_wide = 11.0 + 11.0 + 2.0; // font_size + two-row (opener context)
        let x_band_narrow = extent;
        let expected_delta = x_band_narrow - x_band_wide;

        let wide_tick_y = plot_h_from_tick(&wide_svg);
        let narrow_tick_y = plot_h_from_tick(&narrow_svg);
        let actual_delta = wide_tick_y - narrow_tick_y;
        assert!(
            (actual_delta - expected_delta).abs() < 1.5,
            "plot_h delta {actual_delta} should track the rotated-extent delta {expected_delta} \
             (wide tick_y={wide_tick_y}, narrow tick_y={narrow_tick_y}): {narrow_svg}"
        );
    }

    /// The rail arm keeps the fit arm's own refusal: a tilt shy of the
    /// inline threshold (`|angle| < 90`) on a label that still carries a
    /// two-row context — production rotates the two-row block as one unit,
    /// unmodeled. A ladder capped at -60 with a narrow band never reaches
    /// -90, so the daily opener's context row stays unfolded under a
    /// partial tilt.
    #[test]
    fn rail_arm_rotates_a_two_row_label_as_one_block_under_a_partial_tilt() {
        use serde_json::json;
        let mut p = make_weekly_tilt_rail_payload(110.0, 300.0);
        p["resolved"]["style"]["axis_x"]["labels"]["tilt_increments"] = json!([0.0, -30.0, -60.0]);
        render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("a partial tilt keeps both rows");
    }

    /// `_wrap_hconcat_label_pane` stamps the rail text mark's own `limit`
    /// at the pane's own (capped) width, so a series name that would
    /// overrun the pane ellipsizes rather than drawing past it — the
    /// SAME `rail_pane_w` this arm already computes for the pane's own
    /// declared width, not a second measurement.
    #[test]
    fn rail_concat_arm_truncates_a_label_that_overruns_the_pane() {
        use serde_json::json;
        let fonts = test_fonts();
        let long = "Northwest Regional Operations and Field Support Division for the Greater Metropolitan Area and Surrounding Counties Coordination Office";
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"week": "2026-01-01", "series": long, "signups": 100.0},
            {"week": "2026-01-08", "series": long, "signups": 150.0},
            {"week": "2026-01-01", "series": "B", "signups": 40.0},
            {"week": "2026-01-08", "series": "B", "signups": 60.0},
        ]);
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        let payload: Payload = serde_json::from_value(p).unwrap();
        let cap = 600.0 * 0.33;
        assert!(
            fonts.measure(long, TEST_FONT, 11.0).unwrap() + 4.0 > cap,
            "the fixture's own name must outrun the 0.33×payload.width pane cap to discriminate"
        );
        let svg = render(&payload, &fonts).expect("renders");
        let end = svg.find("…</tspan>").expect("an ellipsized rail label is drawn");
        let start = svg[..end].rfind('>').unwrap() + 1;
        let drawn = &svg[start..end + "…".len()];
        assert_eq!(svg.matches(long).count(), svg.matches(&format!("{long}</title>")).count(), "the full name appears only in the hover title: {svg}");
        let w = fonts.measure(drawn, TEST_FONT, 11.0).unwrap();
        assert!(w <= cap + 1e-6, "drawn label {drawn:?} measures {w}, past the {cap}px pane cap");
    }

    /// L10(b): Vega's gridLayout ROUNDS the rail pane's offset and the
    /// autosize canvas is CEIL'd afterwards — two roundings, not one ceil
    /// of the whole chrome sum. A rail width just above vs just below a
    /// half-pixel boundary must land on different sides of that rounding,
    /// which a single `chrome_w.ceil()` cannot reproduce.
    #[test]
    fn rail_offset_takes_ceil_of_pane0_ink_not_round_of_pane0_ink_plus_spacing() {
        use serde_json::json;
        // The varying term (`line_mark.stroke.width`, feeding `right_reach`)
        // sits INSIDE the ceil in Vega's own gridLayout (`ceil(pane0 +
        // ink)`, spacing added raw afterward) — `payload.width` here is 600
        // (integer), so 600+3.9=603.9 and 600+4.1=604.1 straddle an integer
        // boundary the ceil catches (603.9→604, 604.1→605) but a
        // `round(pane0+ink+spacing)` of the OLD (wrong) model would not
        // (both round to the same spacing-shifted integer) — confirmed
        // against this exact fixture before writing the assertion (never
        // chase a boundary you haven't independently verified): plot width
        // steps by exactly 1 between the two.
        let plot_w_at = |stroke_w: f64| -> f64 {
            let mut p = make_multi_series_line_payload_with_bottom_legend();
            p["rows"] = json!([
                {"week": "2026-01-04", "series": "A", "signups": 12.0},
                {"week": "2026-01-11", "series": "A", "signups": 30.0},
                {"week": "2026-01-18", "series": "A", "signups": 60.0},
            ]);
            p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
            p["resolved"]["style"]["line_mark"]["stroke"]["width"] = json!(stroke_w);
            let payload = serde_json::from_value::<Payload>(p).unwrap();
            let svg = render(&payload, &test_fonts()).expect("renders");
            let (gx, _, end_x) = plot_origin_and_line_end(&svg);
            (end_x - gx).round()
        };
        let below = plot_w_at(3.9);
        let above = plot_w_at(4.1);
        assert_eq!(below, above + 1.0, "ceil(pane0+ink) steps the plot width by exactly 1 at the integer boundary: {below} vs {above}");
    }

    /// The rail arm's own y-axis ink extent, re-derived from what the pane
    /// actually draws: the widest drawn tick label plus `labels.padding` and
    /// the tick length (`axis_json`'s 6 + 6). On that arm the tick labels are
    /// the only marks anchored at `0.5 − padding − tick length`, so their x
    /// attribute selects them exactly. Deliberately independent of the
    /// renderer's own `y_extent` so the assertions below pin a formula
    /// rather than echo one.
    fn rail_y_extent_ink(svg: &str, fonts: &FontBook) -> f64 {
        let key = format!("<text x=\"{:.3}\"", 0.5 - 6.0 - 6.0);
        let mut widest = 0.0_f64;
        for (i, _) in svg.match_indices(&key) {
            let tag = &svg[i..];
            let label = &tag[tag.find('>').unwrap() + 1..tag.find("</text>").unwrap()];
            widest = widest.max(fonts.measure(label, TEST_FONT, 11.0).unwrap());
        }
        assert!(widest > 0.0, "the rail pane draws y tick labels: {svg}");
        widest + 6.0 + 6.0
    }

    /// A rail payload with a baked ladder, so the drawn y labels — and so
    /// the axis ink the chrome is derived from — are known to the test.
    fn rail_ladder_payload(mutate: impl FnOnce(&mut serde_json::Value)) -> Payload {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"week": "2026-01-04", "series": "A", "signups": 40.0},
            {"week": "2026-01-11", "series": "A", "signups": 120.0},
            {"week": "2026-01-18", "series": "A", "signups": 150.0},
            {"week": "2026-01-04", "series": "B", "signups": 20.0},
            {"week": "2026-01-11", "series": "B", "signups": 60.0},
            {"week": "2026-01-18", "series": "B", "signups": 90.0},
        ]);
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        p["resolved"]["style"]["axis_y"]["tick_values"] = json!([0.0, 50.0, 100.0, 150.0]);
        p["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(true);
        // A drawn y gridline spans `[0.5, plot_w + 0.5]`, so the pane's own
        // width is readable from the SVG rather than re-derived.
        p["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(true);
        p["resolved"]["style"]["axis_y"]["domain_min"] = json!(0.0);
        p["resolved"]["style"]["axis_y"]["domain_max"] = json!(150.0);
        set_title_and_style(&mut p, None);
        mutate(&mut p);
        serde_json::from_value(p).unwrap()
    }

    /// C3: the rail concat anchors its title at pane 0's UNROUNDED left ink.
    /// Vega lays the y axis out with an integer (ceil'd) extent before the
    /// panes negotiate widths, but the title group is anchored at the view
    /// bounds' own `x1` — the axis's raw ink — so the title lands at
    /// `padding.left + ceil(ink) − ink`, inside `[padding.left, +1)`. Bar's
    /// rail arm carries the same law (`y_extent_ink`, PR #8362). Reference
    /// title x: 16.9404 (`series-label-placement/line_endpoint_labels`, ink
    /// 44.0596 at plot origin 61), 16.8003 (`win_rate_trend`). Ceiling the
    /// extent at birth pins every rail title at a flat `padding.left`.
    #[test]
    fn the_rail_title_anchors_at_the_unrounded_y_ink_not_its_ceil() {
        let fonts = test_fonts();
        let svg = render(&rail_ladder_payload(|_| {}), &fonts).expect("renders");
        let ink = rail_y_extent_ink(&svg, &fonts);
        assert!(ink.fract() > 0.0, "the fixture's extent must be fractional to discriminate: {ink}");
        assert_eq!(plot_group_x(&svg), 16.0 + ink.ceil(), "the plot takes the ceil'd extent: {svg}");
        let title_x = attr(&text_tag(&svg, "Revenue"), "x");
        assert!(
            (title_x - (16.0 + ink.ceil() - ink)).abs() < 5e-3,
            "title at pad_l + ceil(ink) − ink = {}, got {title_x}: {svg}",
            16.0 + ink.ceil() - ink
        );
    }

    /// The rail group's absolute x and the emitted canvas width.
    fn rail_x_and_canvas(svg: &str) -> (f64, f64) {
        let canvas: f64 = svg.split("width=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
        // The rail pane is the group its labels are drawn inside.
        let label = svg.find(" A</text>").expect("the rail draws its series labels");
        let group = svg[..label].rfind("<g transform=\"translate(").expect("rail group");
        let rail_x: f64 =
            svg[group..].split("translate(").nth(1).unwrap().split(',').next().unwrap().parse().unwrap();
        (rail_x, canvas)
    }

    /// The plot pane's own width, read off a y gridline (`[0.5, plot_w + 0.5]`).
    fn rail_plot_w(svg: &str) -> f64 {
        let grid = svg
            .match_indices("<line ")
            .map(|(i, _)| svg[i..].split("/>").next().unwrap())
            .find(|tag| attr(tag, "x1") == 0.5 && attr(tag, "x2") > 1.0)
            .expect("a y gridline is drawn");
        attr(grid, "x2") - 0.5
    }

    /// The drawn title block's ink: its widest row in the title's own
    /// family, capped at the limit the rail arm compiles (pane 0's
    /// footprint — `avail_w − rail_pane_w − spacing`, truncated).
    fn rail_title_ink(svg: &str, fonts: &FontBook, family: &str, size: f64) -> f64 {
        let (rail_x, canvas) = rail_x_and_canvas(svg);
        let limit = (600.0 - 16.0 - 16.0 - (canvas - 16.0 - rail_x) - 6.0).trunc();
        let mut widest = 0.0_f64;
        for (i, _) in svg.match_indices(&format!("font-size=\"{size}\"")) {
            let tag = &svg[svg[..i].rfind('<').unwrap()..];
            if !tag.starts_with("<text") || !tag.contains(family) {
                continue;
            }
            let open = tag.find('>').unwrap() + 1;
            widest = widest.max(fonts.measure(&tag[open..tag.find("</text>").unwrap()], family, size).unwrap());
        }
        assert!(widest > 0.0, "the title is drawn: {svg}");
        widest.min(limit)
    }

    /// The rail concat wraps and clips the title at pane 0's footprint
    /// (`avail_w − rail_pane_w − spacing`), so even a title far wider than the
    /// plot cannot move the rail or the canvas: the rail's reserved width and
    /// its painted x are one law.
    #[test]
    fn a_title_wider_than_the_plot_never_moves_the_rail() {
        let fonts = test_fonts();
        const SERIF: &str = "'Source Serif 4', serif";
        let short = render(&rail_ladder_payload(|_| {}), &fonts).expect("renders");
        let wide = render(
            &rail_ladder_payload(|p| {
                p["resolved"]["style"]["title_font"] = serde_json::json!(
                    {"family": SERIF, "size": 18.0, "weight": "500", "color": "#494A4C"}
                );
                p["resolved"]["title_style"]["overflow"] = serde_json::json!("clip");
                p["resolved"]["title"] = serde_json::json!(
                    "Weekly signups by region against the prior year's own rolling baseline for every product line"
                );
            }),
            &fonts,
        )
        .expect("renders");
        assert_eq!(rail_x_and_canvas(&wide), rail_x_and_canvas(&short), "{wide}");
    }

    /// C2's other half: the corrected pane's rail offset CEILS pane 0's own
    /// bound and adds `spacing` raw — the L10(b) law the uncorrected pass
    /// already follows — never `round(pane0 + ink + spacing)`. Discriminating
    /// at a stroke fraction below a half pixel, where the two disagree.
    #[test]
    fn the_corrected_rail_offset_ceils_pane_zero_and_adds_spacing_raw() {
        let fonts = test_fonts();
        let stroke = 3.2;
        let svg = render(
            &rail_ladder_payload(|p| {
                p["resolved"]["style"]["line_mark"]["stroke"]["width"] = serde_json::json!(stroke);
            }),
            &fonts,
        )
        .expect("renders");
        let plot_w = rail_plot_w(&svg);
        let rail_rel = rail_x_and_canvas(&svg).0 - plot_group_x(&svg);
        let frac = (plot_w + stroke).fract();
        assert!(
            frac > 0.0 && frac < 0.5,
            "the fixture's bound must fall below a half pixel, where round and ceil part: {frac}"
        );
        assert_eq!(
            rail_rel,
            (plot_w + stroke).ceil() + 6.0,
            "ceil(plot_w + reach) + spacing, not round(sum): plot_w {plot_w}, got {rail_rel}: {svg}"
        );
    }

    /// C5 — #8241's `_apply_unity`: a percent-formatted measure axis whose
    /// rendered domain reaches 1.0 draws a full-width rule at `datum: 1` in
    /// the zero grid's own ink. Vega rounds grid and tick positions but
    /// never a rule mark, so it sits at the EXACT scale position — the
    /// `win_rate_trend` reference carries it at y 74.04 where the 100%
    /// gridline is at 74.5, `stroke-width` 2, spanning the pane.
    ///
    /// The other three gates of production's predicate cannot vary on this
    /// family: the measure always rides y (`_y_carries_the_measure` guards
    /// a rotated scatter), a normalize stack never reaches this renderer
    /// (refused above), and `_measure_field` is the payload's own `y`.
    fn percent_payload(mutate: impl FnOnce(&mut serde_json::Value)) -> Payload {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        let ay = &mut p["resolved"]["style"]["axis_y"];
        ay["labels"]["format"] = json!(".0%");
        ay["tick_label"] = json!(null);
        ay["grid"] = json!({
            "visible": true, "opacity": 1.0, "width": 1.5, "color": "#CEC1AB",
            "threshold": {"color": "#918878", "width": 2.0}
        });
        ay["scale"] = json!({"continuous": {"zero": false}});
        mutate(&mut p);
        serde_json::from_value(p).unwrap()
    }

    /// Every `stroke-width="2"` rule mark, as `(y1, x1, x2)`.
    fn baseline_rules(svg: &str) -> Vec<(f64, f64, f64)> {
        svg.match_indices("<line ")
            .map(|(i, _)| svg[i..].split("/>").next().unwrap())
            .filter(|tag| tag.contains("stroke-width=\"2\""))
            .map(|tag| (attr(tag, "y1"), attr(tag, "x1"), attr(tag, "x2")))
            .collect()
    }

    #[test]
    fn a_percent_axis_reaching_one_draws_the_unity_rule_at_the_exact_datum_position() {
        use serde_json::json;
        let fonts = test_fonts();
        let payload = percent_payload(|p| {
            p["rows"] = json!([
                {"week": "2026-01-04", "series": "A", "signups": 0.42},
                {"week": "2026-01-11", "series": "A", "signups": 1.0},
                {"week": "2026-01-18", "series": "A", "signups": 0.67},
                {"week": "2026-01-04", "series": "B", "signups": 0.31},
                {"week": "2026-01-11", "series": "B", "signups": 0.55},
                {"week": "2026-01-18", "series": "B", "signups": 0.48},
            ]);
            // `win_rate_trend`'s own baked ladder and headroomed bounds: the
            // rendered domain lands [0.2, 1.1], putting `datum: 1` a ninth
            // of the pane down — a fraction the grid's rounding hides.
            let ay = &mut p["resolved"]["style"]["axis_y"];
            ay["tick_values"] = json!([0.2, 0.4, 0.6, 0.8, 1.0, 1.2]);
            ay["domain_min"] = json!(0.2224);
            ay["domain_max"] = json!(1.0576);
        });
        let svg = render(&payload, &fonts).expect("renders");
        let rules = baseline_rules(&svg);
        assert_eq!(rules.len(), 1, "the unity rule alone (zero: false, no data at 0): {svg}");
        let (rule_y, x1, x2) = rules[0];
        assert!(x1 <= 0.0, "the rule spans the pane from its left edge: {svg}");
        assert!(x2 >= rail_plot_w(&svg), "…to its right edge: {svg}");
        // The 1.0 datum's own vertex is drawn on the same scale, unrounded.
        let vertex_y: f64 = {
            let d = svg[svg.find("d=\"M").unwrap() + 4..].split('"').next().unwrap().to_string();
            d.split(['M', 'L'])
                .filter(|seg| !seg.trim().is_empty())
                .nth(1)
                .unwrap()
                .split(',')
                .nth(1)
                .unwrap()
                .trim()
                .parse()
                .unwrap()
        };
        // Both are the same scale position; the rule prints to 3 dp.
        assert!(
            (rule_y - vertex_y).abs() < 5e-4,
            "rule at the datum's own exact y {vertex_y}, got {rule_y}: {svg}"
        );
        let grid_y = svg
            .match_indices("<line ")
            .map(|(i, _)| svg[i..].split("/>").next().unwrap())
            .filter(|tag| tag.contains("stroke-width=\"1.5\""))
            .map(|tag| attr(tag, "y1"))
            .find(|y| (y - rule_y).abs() < 1.0)
            .expect("the 100% gridline is drawn");
        assert!(grid_y != rule_y, "Vega rounds the grid but never the rule: {grid_y} vs {rule_y}");
    }

    #[test]
    fn a_percent_axis_stopping_short_of_one_draws_no_unity_rule() {
        use serde_json::json;
        let svg = render(
            &percent_payload(|p| {
                p["rows"] = json!([
                    {"week": "2026-01-04", "series": "A", "signups": 0.42},
                    {"week": "2026-01-11", "series": "A", "signups": 0.58},
                    {"week": "2026-01-18", "series": "A", "signups": 0.51},
                    {"week": "2026-01-04", "series": "B", "signups": 0.31},
                    {"week": "2026-01-11", "series": "B", "signups": 0.40},
                    {"week": "2026-01-18", "series": "B", "signups": 0.36},
                ]);
            }),
            &test_fonts(),
        )
        .expect("renders");
        assert!(baseline_rules(&svg).is_empty(), "nothing the axis pins reaches 1.0: {svg}");
    }

    /// A string-spelled authored domain never reaches a line's y scale (it reads
    /// the strict numeric pair), so the data extent 0.31..0.58 is what is drawn
    /// and `datum: 1` is outside it, whatever `["0", "2"]` would coerce to.
    #[test]
    fn a_string_spelled_domain_does_not_widen_the_unity_gate() {
        use serde_json::json;
        let svg = render(
            &percent_payload(|p| {
                p["rows"] = json!([
                    {"week": "2026-01-04", "series": "A", "signups": 0.42},
                    {"week": "2026-01-11", "series": "A", "signups": 0.58},
                    {"week": "2026-01-18", "series": "A", "signups": 0.51},
                    {"week": "2026-01-04", "series": "B", "signups": 0.31},
                    {"week": "2026-01-11", "series": "B", "signups": 0.40},
                    {"week": "2026-01-18", "series": "B", "signups": 0.36},
                ]);
                p["resolved"]["style"]["axis_y"]["scale"] = json!({"continuous": {"zero": false, "domain": ["0", "2"]}});
            }),
            &test_fonts(),
        )
        .expect("renders");
        assert!(baseline_rules(&svg).is_empty(), "the drawn domain excludes 1.0: {svg}");
    }

    /// C5's precedence, clause 1 — **never the tick ladder**. `_domain_reaches`
    /// reads `effective_measure_domain`, which is only ever the edges the
    /// emitter PINS; a rung is a tick position, not a domain edge, and
    /// production's own docstring records that reading the ladder made the
    /// gate non-monotonic in headroom (a `headroom: 0.05` percent axis pins
    /// `domainMax 0.9785` while its ladder still tops at 1.0, and VL clips
    /// that rung). Both directions, so the ladder read is dead either way.
    ///
    /// Mutant killed: `hi`/`lo` taken from `tick_values.last()/.first()`
    /// instead of `domain_max`/`domain_min`.
    #[test]
    fn the_unity_gate_reads_the_pinned_domain_edges_never_the_tick_ladder() {
        use serde_json::json;
        let fonts = test_fonts();
        let rows = json!([
            {"week": "2026-01-04", "series": "A", "signups": 0.42},
            {"week": "2026-01-11", "series": "A", "signups": 0.93},
            {"week": "2026-01-18", "series": "A", "signups": 0.67},
            {"week": "2026-01-04", "series": "B", "signups": 0.31},
            {"week": "2026-01-11", "series": "B", "signups": 0.55},
            {"week": "2026-01-18", "series": "B", "signups": 0.48},
        ]);
        // Ladder tops at 1.0, the pinned domain stops at production's own
        // headroom-0.05 example: the rung is outside the rendered domain.
        let ladder_over = render(
            &percent_payload(|p| {
                p["rows"] = rows.clone();
                let ay = &mut p["resolved"]["style"]["axis_y"];
                ay["tick_values"] = json!([0.2, 0.4, 0.6, 0.8, 1.0]);
                ay["domain_max"] = json!(0.9785);
            }),
            &fonts,
        )
        .expect("renders");
        assert!(
            baseline_rules(&ladder_over).is_empty(),
            "the ladder's 1.0 rung is not a domain edge: {ladder_over}"
        );
        // And the converse: the pinned domain reaches 1.0 where the ladder
        // stops short, so a ladder read would suppress a rule Vega paints.
        let ladder_under = render(
            &percent_payload(|p| {
                p["rows"] = rows.clone();
                let ay = &mut p["resolved"]["style"]["axis_y"];
                ay["tick_values"] = json!([0.2, 0.4, 0.6, 0.8]);
                ay["domain_max"] = json!(1.05);
            }),
            &fonts,
        )
        .expect("renders");
        assert_eq!(
            baseline_rules(&ladder_under).len(),
            1,
            "the pinned domain reaches 1.0 whatever the ladder draws: {ladder_under}"
        );
    }

    /// C5's percent gate reads the resolved axis's `labels.format` alone
    /// (`_resolved_percent_format(axis)`, `baseline.py:119`): a chart carries no
    /// format of its own to fall back on.
    #[test]
    fn the_unity_gate_reads_the_axis_format_alone() {
        use serde_json::json;
        let fonts = test_fonts();
        let unformatted_axis = |p: &mut serde_json::Value| {
            p["rows"] = json!([
                {"week": "2026-01-04", "series": "A", "signups": 0.42},
                {"week": "2026-01-11", "series": "A", "signups": 1.0},
                {"week": "2026-01-18", "series": "A", "signups": 0.67},
                {"week": "2026-01-04", "series": "B", "signups": 0.31},
                {"week": "2026-01-11", "series": "B", "signups": 0.55},
                {"week": "2026-01-18", "series": "B", "signups": 0.48},
            ]);
            p["resolved"]["style"]["axis_y"]["labels"]["format"] = json!(null);
        };
        let bare = render(&percent_payload(&unformatted_axis), &fonts).expect("renders");
        assert!(
            baseline_rules(&bare).is_empty(),
            "the axis does not format as a percent: {bare}"
        );
        let stray = render(
            &percent_payload(|p| {
                unformatted_axis(p);
                p["resolved"]["format"] = json!(".0%");
            }),
            &fonts,
        )
        .expect("renders");
        assert!(baseline_rules(&stray).is_empty(), "{stray}");
    }

    /// C5's precedence, clause 3 — a zero-anchored axis whose low edge
    /// resolve left unbaked takes its FLOOR, not the data's own minimum:
    /// `zero_anchor_floor(zero_anchor_domain_floor(scale.values, ticks))`,
    /// byte for byte what `y_zero_scale` pins as `domainMin`. An AUTHORED
    /// `scale.values` never sources that pin — it states tick positions, not
    /// a domain the data is guaranteed to sit inside — so it falls to the
    /// literal 0.0. Both cases put every row above 1.0, where the data
    /// extent alone would answer "does not reach".
    ///
    /// Mutants killed: deleting the zero-anchor branch (the only caller of
    /// `AxisStyle::is_zero_anchored`), and dropping its `scale.values`
    /// filter.
    #[test]
    fn a_zero_anchored_axis_takes_its_ladder_floor_and_never_an_authored_values_list() {
        use serde_json::json;
        let fonts = test_fonts();
        let above_unity = |p: &mut serde_json::Value| {
            p["rows"] = json!([
                {"week": "2026-01-04", "series": "A", "signups": 1.2},
                {"week": "2026-01-11", "series": "A", "signups": 1.5},
                {"week": "2026-01-18", "series": "A", "signups": 1.35},
                {"week": "2026-01-04", "series": "B", "signups": 1.25},
                {"week": "2026-01-11", "series": "B", "signups": 1.4},
                {"week": "2026-01-18", "series": "B", "signups": 1.3},
            ]);
            p["resolved"]["style"]["axis_y"]["domain_max"] = json!(1.6);
        };
        // A COMPUTED zero-anchored ladder: its bottom rung is the floor, so
        // the domain spans 1.0 even though no row is anywhere near it.
        let computed = render(
            &percent_payload(|p| {
                above_unity(p);
                let ay = &mut p["resolved"]["style"]["axis_y"];
                ay["scale"] = json!({"continuous": {"zero": true}});
                ay["tick_values"] = json!([0.0, 0.5, 1.0, 1.5]);
            }),
            &fonts,
        )
        .expect("renders");
        assert_eq!(
            baseline_rules(&computed).len(),
            2,
            "the zero rule and the unity rule, off the ladder's own floor: {computed}"
        );
        // An AUTHORED `scale.values` disqualifies the ladder, and the floor
        // is the literal 0.0 — which still spans 1.0. Read the list instead
        // and the floor would be 1.1, above the datum.
        let authored = render(
            &percent_payload(|p| {
                above_unity(p);
                let ay = &mut p["resolved"]["style"]["axis_y"];
                ay["scale"] = json!({"continuous": {"zero": true}, "values": [1.1, 1.3, 1.5]});
                ay["tick_values"] = json!([1.1, 1.3, 1.5]);
            }),
            &fonts,
        )
        .expect("renders");
        assert_eq!(
            baseline_rules(&authored).len(),
            2,
            "an authored values list never sources the floor pin: {authored}"
        );
    }

    /// An authored `labels.align` anchors every label of a non-temporal x axis, and the
    /// fit measures the ink it draws.
    #[test]
    fn labels_align_on_a_non_temporal_x_axis_anchors_its_labels() {
        let mut json = make_line_payload(true, false);
        json["rows"] = serde_json::json!([
            {"week": "Product A", "signups": 100.0},
            {"week": "Product B", "signups": 150.0},
            {"week": "Product C", "signups": 130.0},
        ]);
        json["resolved"]["style"]["axis_x"]["labels"]["align"] = serde_json::json!("left");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert_eq!(svg.matches("text-anchor=\"start\"").count(), 3, "{svg}");
    }

    // ── Mirror y-axis (style.axis_y.mirror) ─────────────────────────────────

    /// Every `<text>` tag painting exactly `>{label}</text>`, in document
    /// order — `text_tag` only ever finds the first, which can't tell the
    /// primary's own label from a ghost repainting the same string.
    fn text_tags_all<'a>(svg: &'a str, label: &str) -> Vec<&'a str> {
        let needle = format!(">{label}</text>");
        svg.match_indices(&needle).map(|(pos, _)| &svg[svg[..pos].rfind("<text").unwrap()..pos]).collect()
    }

    /// Area shares `render_inner` with line, so its fit arm paints the ghost too.
    #[test]
    fn mirror_on_area_paints_the_ghost() {
        let _en = crate::time_locale::en_us_scope();
        let mut json = make_line_payload(true, false);
        json["resolved"]["style"]["axis_y"]["mirror"] = serde_json::json!(true);
        json["resolved"]["style"]["area_mark"] = serde_json::json!({"opacity": 0.16, "backdrop": false});
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = crate::families::area::render(&payload, &test_fonts()).expect("area mirror renders");
        assert!(svg.matches("text-anchor=\"end\"").count() >= 2, "{svg}");
    }

    #[test]
    fn mirror_expr_object_speaks_the_ghost_labels() {
        let mut json = make_line_payload(true, false);
        json["resolved"]["style"]["axis_y"]["mirror"] =
            serde_json::json!({"expr": "datum.value + '%'"});
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("the ghost's expr renders");
        assert!(svg.contains("%</text>"), "the ghost labels carry the expression: {svg}");
    }

    fn mirror_settings_payload(ruler: serde_json::Value, mirror: Option<serde_json::Value>) -> Payload {
        use serde_json::json;
        let mut p = make_line_payload(true, false); // right axis => fit arm
        p["resolved"]["style"]["axis_y"]["tick_values"] = json!([0.0, 40000.0]);
        p["rows"] = json!([
            {"week": "2026-01-01", "signups": 0.0},
            {"week": "2026-01-08", "signups": 40000.0},
        ]);
        p["resolved"]["style"]["axis_y"]["ruler"] = ruler.clone();
        p["resolved"]["style"]["axis_y"]["mirror"] =
            mirror.unwrap_or_else(|| json!({"format": null, "expr": null, "ruler": ruler, "tick_label": null}));
        serde_json::from_value(p).unwrap()
    }

    fn compacting_ruler() -> serde_json::Value {
        serde_json::json!({
            "exponent": 3, "mode": "repeat", "register": "narrative", "digit_spec": ",.1~f",
            "anchor_at_start": true, "reservation": "", "prefix": ""
        })
    }

    /// Production bakes every truthy `axis_y.mirror` into a
    /// `ResolvedMirrorAxis` whose `ruler` is the primary's own
    /// (`axis_cascade.py` builds it for the opposite edge), so `mirror: true`
    /// arrives as this object and must paint what a bare `true` paints: the
    /// ghost repeats the primary's labels, compacted when the primary is.
    #[test]
    fn mirror_settings_object_renders_like_a_bare_true() {
        use serde_json::json;
        for ruler in [serde_json::Value::Null, compacting_ruler()] {
            let bare = mirror_settings_payload(ruler.clone(), Some(json!(true)));
            let object = mirror_settings_payload(ruler.clone(), None);
            let bare_svg = render(&bare, &test_fonts()).expect("bare true renders");
            let object_svg = render(&object, &test_fonts()).expect("the settings object renders");
            assert_eq!(object_svg, bare_svg, "ruler {ruler}");
        }
        let svg = render(&mirror_settings_payload(compacting_ruler(), None), &test_fonts()).unwrap();
        assert_eq!(text_tags_all(&svg, "40k").len(), 2, "primary and ghost both compact: {svg}");
        assert_eq!(text_tags_all(&svg, "40,000").len(), 0, "{svg}");
    }

    /// A mirror's own `format` and a ghost ruler that is not the primary's relabel only
    /// the ghost; a baked ghost ladder and unknown keys refuse by name.
    #[test]
    fn mirror_settings_object_relabels_the_ghost_and_refuses_ladders_and_unknown_keys_by_name() {
        use serde_json::json;
        let render_with = |ruler: serde_json::Value, mirror: serde_json::Value| {
            render(&mirror_settings_payload(ruler, Some(mirror)), &test_fonts())
        };
        let null = serde_json::Value::Null;
        let relabel = |key: &str, value: serde_json::Value| {
            let mut m = json!({"format": null, "expr": null, "ruler": null, "tick_label": null});
            m[key] = value;
            m
        };
        let err = render_with(null.clone(), relabel("tick_label", json!({"format": ",.0f"}))).expect_err("must refuse");
        assert!(err.contains("line: axis_y.mirror.tick_label"), "{err}");
        let err = render_with(null.clone(), relabel("mirror_ruler", null.clone())).expect_err("must refuse");
        assert!(err.contains("\"mirror_ruler\", which is not a ResolvedMirrorAxis field"), "{err}");

        let svg = render_with(compacting_ruler(), relabel("format", json!("$,.0f"))).expect("a ghost format renders");
        assert_eq!(text_tags_all(&svg, "40k").len(), 1, "the primary stays compacted: {svg}");
        assert_eq!(text_tags_all(&svg, "$40,000").len(), 1, "the ghost paints its own format: {svg}");
        let svg = render_with(null.clone(), relabel("ruler", compacting_ruler())).expect("a ghost ruler renders");
        assert_eq!(text_tags_all(&svg, "40,000").len(), 1, "{svg}");
        assert_eq!(text_tags_all(&svg, "40k").len(), 1, "the ghost paints through its own ruler: {svg}");
    }

    /// A left axis's ghost sits on the right edge, start-anchored at the primary's own
    /// tick and label padding.
    #[test]
    fn mirror_on_a_left_axis_draws_the_ghost_on_the_right_edge() {
        let mut json = make_line_payload(false, false);
        json["resolved"]["style"]["axis_y"]["mirror"] = serde_json::json!(true);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("a left mirror renders");
        // Every y label is painted twice: end-anchored left of the plot, start-anchored right of it.
        let labels = |anchor: &str| -> Vec<(f64, String)> {
            svg.split("<text x=\"")
                .skip(1)
                .filter(|t| t.contains(&format!("text-anchor=\"{anchor}\"")))
                .filter_map(|t| {
                    let x = t.split('"').next()?.parse().ok()?;
                    let text = t.split_once('>')?.1.split('<').next()?.to_string();
                    Some((x, text))
                })
                .filter(|(_, text)| text.chars().all(|c| c.is_ascii_digit() || c == ','))
                .collect()
        };
        let primary: Vec<(f64, String)> = labels("end").into_iter().filter(|(x, _)| *x < 0.0).collect();
        let ghost: Vec<(f64, String)> = labels("start").into_iter().filter(|(x, _)| *x > 300.0).collect();
        assert!(!primary.is_empty(), "{svg}");
        assert_eq!(primary.iter().map(|(_, t)| t).collect::<Vec<_>>(), ghost.iter().map(|(_, t)| t).collect::<Vec<_>>());
    }

    /// Production's law (`mirror_axis.py`): the ghost repaints the SAME
    /// labels at the SAME y positions and anchor as the primary, at the
    /// primary's own reserved offset sign-flipped onto the opposite edge —
    /// `primary_x + ghost_x == plot_w + 1.0` is that law's own invariant
    /// (`plot_w + 0.5 + offset` and `0.5 - offset` sum to `plot_w + 1.0`
    /// regardless of `offset`'s value). Sign-flip the mirror or drop the
    /// primary's own offset and this goes red.
    #[test]
    fn mirror_repaints_primary_labels_on_the_opposite_edge_and_grows_the_margin() {
        use serde_json::json;
        let mut p = make_line_payload(true, false); // right axis => fit arm
        p["resolved"]["style"]["axis_y"]["tick_values"] = json!([0.0, 10000.0, 20000.0, 30000.0, 40000.0]);
        p["rows"] = json!([
            {"week": "2026-01-01", "signups": 0.0},
            {"week": "2026-01-08", "signups": 40000.0},
        ]);
        let baseline: Payload = serde::Deserialize::deserialize(&p).unwrap();
        let baseline_svg = render(&baseline, &test_fonts()).expect("baseline renders");
        assert_eq!(text_tags_all(&baseline_svg, "40,000").len(), 1, "no ghost without mirror");
        let (baseline_gx, _, baseline_end) = plot_origin_and_line_end(&baseline_svg);
        let baseline_plot_w = baseline_end - baseline_gx;

        p["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
        let mirrored: Payload = serde_json::from_value(p).unwrap();
        let mirrored_svg = render(&mirrored, &test_fonts()).expect("mirror renders");
        let (mirrored_gx, _, mirrored_end) = plot_origin_and_line_end(&mirrored_svg);
        let mirrored_plot_w = mirrored_end - mirrored_gx;

        let tags = text_tags_all(&mirrored_svg, "40,000");
        assert_eq!(tags.len(), 2, "the ghost repaints the primary's own label: {mirrored_svg}");
        for tag in &tags {
            assert!(tag.contains("text-anchor=\"end\""), "same anchor as the primary: {tag}");
        }
        let xs: Vec<f64> = tags.iter().map(|t| attr(t, "x")).collect();
        let (primary_x, ghost_x) = if xs[0] > xs[1] { (xs[0], xs[1]) } else { (xs[1], xs[0]) };
        assert!(
            (primary_x + ghost_x - (mirrored_plot_w + 1.0)).abs() < 0.01,
            "ghost sits at the primary's own offset, sign-flipped onto the opposite \
             edge's 0.5 crisp anchor: primary_x={primary_x} ghost_x={ghost_x} \
             plot_w={mirrored_plot_w}"
        );
        assert!(
            mirrored_plot_w < baseline_plot_w,
            "the opposite margin grows to reserve the ghost's own ink: \
             baseline={baseline_plot_w} mirrored={mirrored_plot_w}"
        );
    }

    /// Production's ghost inherits `ticks`/`tickSize` from the primary
    /// unchanged (`mirror_axis.py:162-165` overrides only `orient`/`grid`/
    /// `title`) — a ticks-visible primary draws a ticks-visible ghost, one
    /// mark per the primary's own tick_values, sign-flipped onto the LEFT
    /// edge's `x1="0.500"` crisp anchor.
    ///
    /// Regression for a reviewer-caught HIGH: an earlier version of this
    /// test asserted total `<line>` COUNT EQUALITY between the baseline and
    /// the mirrored render — satisfied by drawing NO ghost ticks at all, so
    /// it silently locked the missing-ticks bug in instead of catching it.
    #[test]
    fn mirror_repaints_the_primary_ticks_on_the_opposite_edge() {
        use serde_json::json;
        let mut p = make_line_payload(true, false);
        p["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(true);
        p["resolved"]["style"]["axis_y"]["tick_values"] = json!([0.0, 10000.0, 20000.0, 30000.0, 40000.0]);
        p["rows"] = json!([
            {"week": "2026-01-01", "signups": 0.0},
            {"week": "2026-01-08", "signups": 40000.0},
        ]);
        // A ghost tick is a horizontal `<line>` (`y1 == y2`) anchored at the
        // left edge's `x1 = 0.5` reaching NEGATIVE x — the only marks with
        // negative x in this fixture; the x-axis's own leftmost category
        // tick also sits at `x1 = 0.5` but is VERTICAL (`x1 == x2`).
        let ghost_ticks = |svg: &str| -> usize {
            svg.match_indices("<line")
                .filter(|&(i, _)| {
                    let end = svg[i..].find("/>").map(|e| i + e).unwrap_or(svg.len());
                    let tag = &svg[i..end];
                    attr(tag, "x1") == 0.5 && attr(tag, "x2") < 0.0
                })
                .count()
        };
        let baseline: Payload = serde::Deserialize::deserialize(&p).unwrap();
        let baseline_svg = render(&baseline, &test_fonts()).expect("baseline renders");
        assert_eq!(ghost_ticks(&baseline_svg), 0, "no ghost ticks without mirror");

        p["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
        let mirrored: Payload = serde_json::from_value(p).unwrap();
        let mirrored_svg = render(&mirrored, &test_fonts()).expect("mirror renders");
        assert_eq!(
            ghost_ticks(&mirrored_svg),
            5,
            "one ghost tick per the primary's own 5 baked tick_values: {mirrored_svg}"
        );
    }

    /// The ghost draws no grid and no title — the two keys production's
    /// `dict(primary_axis)` DOES override (`mirror_axis.py:162-165`), unlike
    /// `ticks` above.
    #[test]
    fn mirror_paints_no_second_grid_or_title() {
        use serde_json::json;
        let mut p = make_line_payload(true, true); // titles visible
        p["resolved"]["y_label"] = json!("Signups");
        p["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(true);
        p["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(false);
        // A y-gridline spans the whole plot width (`x1 = 0.5`, `x2 = plot_w
        // + 0.5`) — distinct from the x-axis's own leftmost vertical
        // category tick, which also sits at `x1 = 0.5` but has `x2 == x1`.
        let grid_lines = |svg: &str| -> usize {
            svg.match_indices("<line")
                .filter(|&(i, _)| {
                    let end = svg[i..].find("/>").map(|e| i + e).unwrap_or(svg.len());
                    let tag = &svg[i..end];
                    attr(tag, "x1") == 0.5 && attr(tag, "x2") > 10.0
                })
                .count()
        };
        let baseline: Payload = serde::Deserialize::deserialize(&p).unwrap();
        let baseline_svg = render(&baseline, &test_fonts()).expect("baseline renders");
        let baseline_grid = grid_lines(&baseline_svg);
        assert!(baseline_grid > 0, "fixture actually draws a grid: {baseline_svg}");

        p["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
        let mirrored: Payload = serde_json::from_value(p).unwrap();
        let mirrored_svg = render(&mirrored, &test_fonts()).expect("mirror renders");
        assert_eq!(
            grid_lines(&mirrored_svg),
            baseline_grid,
            "the ghost adds no grid lines of its own: {mirrored_svg}"
        );
        assert_eq!(
            mirrored_svg.matches(">Signups<").count(),
            1,
            "the ghost paints no title of its own: {mirrored_svg}"
        );
    }

    /// Regression for the load-bearing per-label ghost fit-ink loop (the
    /// whole content of the commit that fixed `revenue_wide`'s MISMATCH):
    /// the reserved left margin must clear the ghost's OWN widest label ink
    /// — `gutter + widest`, not just `gutter` alone, which is all a
    /// box-only reservation could ever satisfy (the primary's own gutter
    /// already bakes its label width in; the ghost's flip-inward anchor
    /// does not, so a bare box under-reserves by a full label width).
    /// Deleting the per-label loop drops the margin to ~`ceil(gutter)`,
    /// well under this bound.
    #[test]
    fn mirror_ghost_labels_left_margin_clears_the_widest_labels_own_ink() {
        use serde_json::json;
        let mut p = make_line_payload(true, false);
        p["resolved"]["style"]["axis_y"]["tick_values"] = json!([0.0, 10000.0, 20000.0, 30000.0, 40000.0]);
        p["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(false);
        p["rows"] = json!([
            {"week": "2026-01-01", "signups": 0.0},
            {"week": "2026-01-08", "signups": 40000.0},
        ]);
        p["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        let (gx, _, _) = plot_origin_and_line_end(&svg);
        let left_margin = gx - 16.0; // fixture's own pad_l

        // The same measurement the render uses for the primary's own
        // gutter: `measure::stand_in_family` maps every non-Source-Serif
        // family (including the tabular numeric one) onto "Inter" — the
        // stand-in `gutter_of` actually measures with, not the drawn font.
        let mf = measure::stand_in_family("'dbt Sans Tabular', Inter, system-ui, sans-serif");
        let widest = ["0", "10,000", "20,000", "30,000", "40,000"]
            .iter()
            .map(|s| fonts.measure(s, mf, 11.0).unwrap())
            .fold(0.0_f64, f64::max);
        let gutter = widest + VALUE_AXIS_BREATHING_ROOM;
        assert!(
            left_margin >= gutter + widest - 1.0,
            "left_margin={left_margin} must clear gutter({gutter}) + widest \
             label ink({widest}); a box-only reservation tops out at \
             ceil(gutter)≈{}",
            gutter.ceil()
        );
    }

    /// A flip-aligned right value axis sizes its gutter (production's baked
    /// `labelPadding`) from the DRAWN ladder — `axis.drawn_tick_values`, the
    /// baked rungs inside the baked domain. A baked rung past the baked
    /// `domain_max` is never drawn and never sizes the gutter.
    #[test]
    fn right_value_gutter_measures_the_drawn_ladder_not_a_rung_past_the_domain() {
        use serde_json::json;
        let render_with = |ladder: serde_json::Value| {
            let mut p = make_line_payload(true, false);
            p["resolved"]["style"]["axis_y"]["domain_max"] = json!(150.0);
            p["resolved"]["style"]["axis_y"]["tick_values"] = ladder;
            render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders")
        };
        let label_x = |svg: &str, text: &str| -> f64 {
            let i = svg.find(&format!(">{text}</text>")).expect("label draws");
            let tag = &svg[svg[..i].rfind("<text").unwrap()..i];
            tag.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        let drawn = render_with(json!([0.0, 50.0, 100.0, 150.0]));
        let with_far_rung = render_with(json!([0.0, 50.0, 100.0, 150.0, 90_000_000.0]));
        assert!(!with_far_rung.contains(">90,000,000<"), "the rung past the domain is not drawn: {with_far_rung}");
        assert_eq!(
            label_x(&with_far_rung, "150"),
            label_x(&drawn, "150"),
            "the undrawn rung must not size the gutter: {with_far_rung}"
        );
    }

    /// The ghost's reserve is exactly `ceil(gutter + widest label)`: Vega
    /// lays the axis out at x = 0 and only the SVG renderer adds the 0.5
    /// crisp shift, so the ink never subtracts it. `revenue_wide`'s ghost
    /// (`29.744 + 22.688 = 52.43`) reserves 53; anchoring the ink at the
    /// drawn `0.5 − reserve` reserved 52.
    #[test]
    fn mirror_ghost_reserve_is_the_ceil_of_gutter_plus_widest_label_without_the_crisp_shift() {
        use serde_json::json;
        let mut p = make_line_payload(true, false);
        p["resolved"]["style"]["axis_y"]["tick_values"] = json!([0.0, 10000.0, 20000.0, 30000.0, 40000.0]);
        p["resolved"]["style"]["axis_y"]["ticks"]["visible"] = json!(false);
        p["rows"] = json!([
            {"week": "2026-01-01", "signups": 0.0},
            {"week": "2026-01-08", "signups": 40000.0},
        ]);
        p["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
        let y_family = p["resolved"]["style"]["axis_y"]["labels"]["font"]["family"].as_str().unwrap().to_string();
        let payload: Payload = serde_json::from_value(p).unwrap();
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        let (gx, _, _) = plot_origin_and_line_end(&svg);
        let left_margin = gx - 16.0; // fixture's own pad_l

        let labels = ["0", "10,000", "20,000", "30,000", "40,000"];
        let mf = measure::stand_in_family(&y_family);
        let gutter = labels.iter().map(|s| fonts.measure(s, mf, 11.0).unwrap()).fold(0.0_f64, f64::max)
            + VALUE_AXIS_BREATHING_ROOM;
        let widest = labels.iter().map(|s| fonts.measure_shaped(s, &y_family, 11.0).unwrap()).fold(0.0_f64, f64::max);
        assert_eq!(
            left_margin,
            (gutter + widest).ceil(),
            "left_margin must be ceil(gutter {gutter} + widest {widest}), not ceil of that less 0.5: {svg}"
        );
    }

    /// `axis_y.mirror` on a GENUINELY NOMINAL x (month NAMES, never a
    /// date-like string) hits the point-to-band scale flip — real Vega
    /// compiles the shared x as a BAND scale (`step*(i+0.5)` centers, `step
    /// = plot_w / n`), confirmed against `vl_convert.vegalite_to_
    /// scenegraph` (`laws-line-area.md`'s band-position section): on this
    /// fixture (3 categories, right-axis fit arm, the ghost's
    /// `ceil(gutter + widest label)` reserve narrows `plot_w` to 487) the
    /// vertices land at `162.333·(i+0.5)` = 81.167/243.5/405.833 — pinned
    /// below. Mutation: drop `use_band_positions` back to reading
    /// `is_band_step` alone at `fit_x_at`'s own site and the vertices move
    /// to the point-scale positions `i/(n-1)*plot_w` (0/243.5/487) instead.
    #[test]
    fn mirror_on_a_nominal_x_renders_band_positions() {
        let mut p = make_line_payload(true, false); // right axis => fit arm
        // `axis_json`'s own test-fixture default bakes `is_quantitative:
        // true` regardless of the real x field's shape (harmless for most
        // fit-arm tests, which never branch on it for a plain nominal x) —
        // this gate DOES, so state the real shape explicitly, the same way
        // every facet nominal-x test in this file already overrides it.
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        p["rows"] = serde_json::json!([
            {"week": "Jan", "signups": 100.0},
            {"week": "Feb", "signups": 150.0},
            {"week": "Mar", "signups": 130.0},
        ]);
        p["resolved"]["style"]["axis_y"]["mirror"] = serde_json::json!(true);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("nominal x + mirror renders band positions");
        assert!(
            svg.contains("d=\"M81.167,338L243.5,0L405.833,135.2\""),
            "vertices must land at band centers step*(i+0.5), step=487/3, printed to three \
             decimals (81.1666.. -> 81.167, 405.8333.. -> 405.833): {svg}"
        );
        // X ticks snap to `floor(center) + 0.5` (a BAND scale's own
        // `tickRound` law, `tick_snap`'s doc comment).
        assert!(svg.contains("x1=\"81.500\" y1=\"338.000\" x2=\"81.500\" y2=\"345.500\""), "{svg}");
        assert!(svg.contains("x1=\"243.500\" y1=\"338.000\" x2=\"243.500\" y2=\"345.500\""), "{svg}");
        assert!(svg.contains("x1=\"405.500\" y1=\"338.000\" x2=\"405.500\" y2=\"345.500\""), "{svg}");
        // Primary + ghost pair — the mirror ghost still paints (the band
        // flip changes X positions only, never the Y-axis mirror itself).
        assert!(svg.matches("text-anchor=\"end\"").count() >= 2, "primary + ghost pair: {svg}");
    }

    /// The fit arm's autosize ink union re-measures value-label ink at
    /// EVERY candidate width (L6, `pos_at`), so it must read the same band
    /// centers the vertex/paint code already does under `use_band_
    /// positions` — not `pos_at`'s point-scale formula, which the panel's
    /// OWN vertex-ink union (right above it, `:3946`) was already fixed to
    /// avoid. A wide (`,.20f`) format on the first/last category makes the
    /// two formulas' ink reach diverge enough to move the canvas: the point
    /// formula puts index 0 at the plot's left edge (maximal left overhang)
    /// while the band formula centers it half a band step inside, so
    /// measuring at the wrong position mis-sizes `canvas_w`. Mutation:
    /// reverting the `use_band_positions` branch at the L6 site back to
    /// unconditional `pos_at(*i, false)` changes the pinned width below.
    #[test]
    fn value_label_ink_under_mirror_measures_at_band_centers_not_point_positions() {
        let mut p = make_line_payload(true, false); // right axis => fit arm
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        p["rows"] = serde_json::json!([
            {"week": "Jan", "signups": 100.0},
            {"week": "Feb", "signups": 150.0},
            {"week": "Mar", "signups": 130.0},
        ]);
        p["resolved"]["style"]["axis_y"]["mirror"] = serde_json::json!(true);
        set_value_labels(&mut p, ",.20f");
        p["board_style"] = serde_json::json!({
            "text_mark_font": {"family": "sans-serif", "size": 11.0},
            "format_locale": crate::number_locale::en_us_wire(),
            "time_locale": crate::time_locale::en_us_wire(),
        });
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts())
            .expect("mirrored nominal x with wide value labels renders");
        // The plot's own left margin (`<g transform="translate(x, y)">`) is
        // the y-axis gutter negotiated against every mark's ink, the wide
        // value label included — measuring the first label's ink at the
        // point-scale left edge (x=0, maximal left overhang) instead of its
        // true band center (x=81.17, well inside the plot) inflates that
        // negotiated margin from 66 by a label's half width.
        let plot_x: f64 = svg.split("translate(").nth(1).unwrap().split(',').next().unwrap().parse().unwrap();
        assert_eq!(plot_x, 66.0, "left margin from the label's true (band-centered) ink, not its point-scale ink: {svg}");
        // The paint site (`fit_x_at`, already band-aware pre-fix) and the
        // ink-measurement site must now agree: the first label's own text
        // sits at the SAME band center the ink union measured it at.
        let label_x = attr(&text_tag(&svg, "100.00000000000000000000"), "x");
        assert_eq!(label_x, 81.17, "label paints where its own ink was measured: {svg}");
    }

    /// The mirror counterpart of the refusal above: a TEMPORAL x (daily
    /// dates, `charts/axis-labels_0/revenue_wide`'s own shape) is
    /// unaffected and keeps rendering — `detected_data_tu` detects the
    /// daily cadence, so the gate above never fires.
    #[test]
    fn mirror_on_a_temporal_x_still_renders() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_line_payload(true, false); // right axis => fit arm
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        p["rows"] = serde_json::json!([
            {"week": "2026-01-01", "signups": 100.0},
            {"week": "2026-01-02", "signups": 150.0},
            {"week": "2026-01-03", "signups": 130.0},
        ]);
        p["resolved"]["style"]["axis_y"]["mirror"] = serde_json::json!(true);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("temporal x + mirror renders");
        assert!(svg.matches("text-anchor=\"end\"").count() >= 2, "primary + ghost pair: {svg}");
    }

    /// `date` advanced by `days`, as an ISO string — the 28-day cadence
    /// `detect_time_unit` refuses.
    fn iso_plus_days(date: &str, days: i64) -> String {
        let (y, m, d) = crate::time_axis::parse_iso_date(date).unwrap();
        let (y2, m2, d2) = crate::families::temporal::civil_from_days(
            crate::families::temporal::days_from_civil(y, m, d) + days,
        );
        format!("{y2:04}-{m2:02}-{d2:02}")
    }

    /// A 28-day-cadence ISO date x under mirror draws (probed against
    /// production: `axis-mirror/11`).
    #[test]
    fn mirror_on_an_undetected_cadence_iso_date_x_renders() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_line_payload(true, false); // right axis => fit arm
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        let rows: Vec<serde_json::Value> = (0..6)
            .map(|i| serde_json::json!({
                "week": iso_plus_days("2024-01-07", i * 28),
                "signups": 100.0 + i as f64,
            }))
            .collect();
        p["rows"] = serde_json::json!(rows);
        p["resolved"]["style"]["axis_y"]["mirror"] = serde_json::json!(true);
        let payload: Payload = serde_json::from_value(p).unwrap();
        render(&payload, &test_fonts()).expect("undetected-cadence ISO date x under mirror renders");
    }

    /// `axis_y.mirror`'s band-position law and `curve: step`'s band-step
    /// transform compose.
    #[test]
    fn mirror_combined_with_curve_step_renders() {
        let mut p = make_line_payload(true, false); // right axis => fit arm
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        p["rows"] = serde_json::json!([
            {"week": "Jan", "signups": 100.0},
            {"week": "Feb", "signups": 150.0},
            {"week": "Mar", "signups": 130.0},
        ]);
        p["resolved"]["style"]["axis_y"]["mirror"] = serde_json::json!(true);
        p["resolved"]["style"]["line_mark"]["curve"] = serde_json::json!("step");
        let payload: Payload = serde_json::from_value(p).unwrap();
        render(&payload, &test_fonts()).expect("mirror + curve=step renders");
    }

    /// An authored sort reorders the domain under mirror's band scale
    #[test]
    fn mirror_combined_with_authored_sort_renders() {
        let mut p = make_line_payload(true, false); // right axis => fit arm
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        p["rows"] = serde_json::json!([
            {"week": "Jan", "signups": 100.0},
            {"week": "Feb", "signups": 150.0},
            {"week": "Mar", "signups": 130.0},
        ]);
        p["resolved"]["style"]["axis_y"]["mirror"] = serde_json::json!(true);
        p["resolved"]["sort"] = serde_json::json!({"by": "signups", "order": "asc"});
        let payload: Payload = serde_json::from_value(p).unwrap();
        render(&payload, &test_fonts()).expect("mirror + authored sort renders");
    }

    /// x positions of the x-axis tick marks (the vertical strokes below the
    /// plot — the only `<line>`s whose y1 exceeds the plot height).
    fn x_tick_xs(svg: &str) -> Vec<f64> {
        let mut xs = Vec::new();
        for tag in svg.split("<line").skip(1) {
            let end = tag.find("/>").unwrap_or(tag.len());
            let tag = &tag[..end];
            let attr = |k: &str| -> Option<f64> {
                let i = tag.find(&format!("{k}=\""))? + k.len() + 2;
                tag[i..].split('"').next()?.parse().ok()
            };
            let (Some(x1), Some(x2), Some(y1), Some(y2)) =
                (attr("x1"), attr("x2"), attr("y1"), attr("y2"))
            else {
                continue;
            };
            if (x1 - x2).abs() < 1e-9 && y2 > y1 && y2 - y1 < 10.0 {
                xs.push(x1);
            }
        }
        xs
    }

    /// The x-axis label texts in document order — the axis labels drawn
    /// below the x tick row (the y ladder shares their fill, so the tick
    /// row's own depth is what tells the two apart).
    fn x_label_texts(svg: &str) -> Vec<String> {
        let row_y = x_tick_bottom(svg);
        svg.split("<text")
            .skip(1)
            .filter(|t| t.contains("fill=\"#626366\""))
            .filter_map(|t| {
                let head = t.split_once('>')?.0;
                let i = head.find("y=\"")? + 3;
                let y: f64 = head[i..].split('"').next()?.parse().ok()?;
                (y > row_y).then(|| t.split_once('>')?.1.split('<').next().map(str::to_string))?
            })
            .collect()
    }

    /// The bottom edge of the x tick row.
    fn x_tick_bottom(svg: &str) -> f64 {
        svg.split("<line")
            .skip(1)
            .filter_map(|tag| {
                let tag = &tag[..tag.find("/>").unwrap_or(tag.len())];
                let attr = |k: &str| -> Option<f64> {
                    let i = tag.find(&format!("{k}=\""))? + k.len() + 2;
                    tag[i..].split('"').next()?.parse().ok()
                };
                let (x1, x2, y1, y2) = (attr("x1")?, attr("x2")?, attr("y1")?, attr("y2")?);
                ((x1 - x2).abs() < 1e-9 && y2 > y1 && y2 - y1 < 10.0).then_some(y2)
            })
            .fold(0.0, f64::max)
    }

    /// A1: Vega facets a multi-series line/area by the color field and
    /// emits one mark group per key in the data's FIRST-APPEARANCE row
    /// order — probed on `area_won_stacked`'s own compiled spec: reordering
    /// `data.values` reorders the scenegraph's area groups, while swapping
    /// `color.scale.domain`/`range` for the alphabetical pair does not. The
    /// palette index stays alphabetical, which production reaches by
    /// permuting the emitted `color.scale.range` against a domain in that
    /// spatial order. So the two orders are separate lists, and reading the
    /// painted fills in document order asserts both at once: `zulu` paints
    /// FIRST (it appears first in the rows) carrying palette slot 1 (it
    /// sorts last), `alpha` second carrying slot 0.
    #[test]
    fn area_series_paint_in_first_appearance_order_with_an_alphabetical_palette_index() {
        let _en = crate::time_locale::en_us_scope();
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "zulu", "signups": 100.0},
            {"week": "2026-01-08", "series": "zulu", "signups": 150.0},
            {"week": "2026-01-01", "series": "alpha", "signups": 40.0},
            {"week": "2026-01-08", "series": "alpha", "signups": 60.0},
        ]);
        p["resolved"]["palette"] = json!(["#aa0000", "#00bb00"]);
        p["resolved"]["style"]["area_mark"] = json!({"opacity": 0.16, "backdrop": false});
        let area_fills = |p: &serde_json::Value| -> Vec<String> {
            let payload: Payload = serde::Deserialize::deserialize(p).unwrap();
            let svg = crate::families::area::render(&payload, &test_fonts()).expect("renders");
            svg.match_indices("fill-opacity=\"0.16\"")
                .map(|(i, _)| {
                    let f = svg[..i].rfind("fill=\"").expect("an area path carries a fill");
                    svg[f + 6..f + 13].to_string()
                })
                .collect()
        };
        assert_eq!(
            area_fills(&p),
            vec!["#00bb00", "#aa0000"],
            "zulu (first in the rows, palette slot 1) paints under alpha (slot 0)"
        );
        // The probe was taken on Vega's compiled spec, so pin the fit arm
        // too — a right value axis with no rail is the shape it read.
        p["resolved"]["style"]["axis_y"]["position"] = json!("right");
        assert_eq!(area_fills(&p), vec!["#00bb00", "#aa0000"], "same on the fit arm");
    }

    /// A1 on the endpoint rail: `sorted_series_by_last_value` is a STABLE
    /// sort over a list already in first-appearance order, so two series
    /// tying on their last value keep that order — not the alphabetical
    /// one. `cascade_rail_anchors` sorts stably too, so feeding it the
    /// spatial domain is the whole law. Both series end at 60, so only the
    /// tie-break decides which label sits above the other.
    #[test]
    fn tied_rail_rows_break_in_first_appearance_order() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "zulu", "signups": 100.0},
            {"week": "2026-01-08", "series": "zulu", "signups": 60.0},
            {"week": "2026-01-01", "series": "alpha", "signups": 40.0},
            {"week": "2026-01-08", "series": "alpha", "signups": 60.0},
        ]);
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        p["resolved"]["style"]["series_label"]["dark_companion_palette"] =
            json!(["#111111", "#222222"]);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let zulu = svg.find(" zulu<").expect("zulu rail row");
        let alpha = svg.find(" alpha<").expect("alpha rail row");
        assert!(
            zulu < alpha,
            "the tie keeps first-appearance order, so zulu is placed (and emitted) first"
        );
    }

    /// A rail label prints the series' own final value, then its name, through the y
    /// axis's own tick-label law: `tick_label`, else `labels.format`, else the default
    /// `,.0~f`; a negative takes the axis's U+2212; the post-cascade nudge moves the
    /// label, never the number.
    #[test]
    fn a_series_rail_label_prints_the_final_value_through_the_axis_law_then_the_name() {
        use serde_json::json;
        // (tick_label, labels.format, A's final, B's final, expected A, expected B)
        let cases = [
            (json!({"format": ",.2~f"}), json!(null), 1500.0, 1499.5, "1,500 A", "1,499.5 B"),
            (json!(null), json!("$,.0f"), 1500.0, 1499.0, "$1,500 A", "$1,499 B"),
            (json!(null), json!(null), 1500.0, 1499.0, "1,500 A", "1,499 B"),
            (json!({"format": ",.2~f"}), json!(null), 1.0 / 3.0, -2.5, "0.33 A", "\u{2212}2.5 B"),
        ];
        for (tick_label, labels_format, a_last, b_last, want_a, want_b) in cases {
            let mut p = make_multi_series_line_payload_with_bottom_legend();
            p["rows"] = json!([
                {"week": "2026-01-01", "series": "A", "signups": 100.0},
                {"week": "2026-01-08", "series": "A", "signups": a_last},
                {"week": "2026-01-01", "series": "B", "signups": 40.0},
                {"week": "2026-01-08", "series": "B", "signups": b_last},
            ]);
            p["resolved"]["legend"]["visible"] = json!(false);
            p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
            p["resolved"]["style"]["axis_y"]["tick_label"] = tick_label;
            p["resolved"]["style"]["axis_y"]["labels"]["format"] = labels_format;
            let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
            assert!(svg.contains(&format!(">{want_a}</text>")), "{want_a}: {svg}");
            assert!(svg.contains(&format!(">{want_b}</text>")), "{want_b}: {svg}");
        }
    }

    /// The rail renders for `names` (series ending 150, 60, then 100) at 600px wide,
    /// returning its SVG and the diagnostics.
    fn rail_cascade_render(names: &[&str]) -> (String, Vec<crate::diagnostic::Diagnostic>) {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        let ends = [150.0, 60.0, 100.0];
        p["rows"] = json!(names
            .iter()
            .zip(ends)
            .flat_map(|(name, end)| [
                json!({"week": "2026-01-01", "series": name, "signups": 40.0}),
                json!({"week": "2026-01-08", "series": name, "signups": end}),
            ])
            .collect::<Vec<_>>());
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        let (svg, warnings) =
            crate::diagnostic::collect(|| render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()));
        (svg.expect("renders"), warnings)
    }

    /// Rung 1 of the rail cascade: `{value} {name}` fits the pane cap on one line.
    #[test]
    fn a_rail_label_that_fits_stays_on_one_line() {
        let (svg, warnings) = rail_cascade_render(&["Berlin", "Paris"]);
        assert!(svg.contains(">150 Berlin</text>") && svg.contains(">60 Paris</text>"), "{svg}");
        assert!(warnings.iter().all(|w| w.code != "WARN-RAIL-VALUE-DROPPED"), "{warnings:?}");
    }

    /// Rung 2: too wide for one line, the value leads line 1 and the name wraps at a word
    /// boundary onto line 2; both lines stay whole and nothing warns.
    #[test]
    fn a_rail_label_too_wide_for_one_line_wraps_with_the_value_leading() {
        let (svg, warnings) = rail_cascade_render(&["Northern Operations Division", "Paris"]);
        assert!(svg.contains("<tspan>150 Northern</tspan>") && svg.contains("dy=\"13\">Operations Division</tspan>"), "{svg}");
        assert!(!svg.contains('\u{2026}'), "a wrapped label is not clipped: {svg}");
        let codes: Vec<&str> = warnings.iter().map(|w| w.code).collect();
        assert!(!codes.contains(&"WARN-RAIL-VALUE-DROPPED") && !codes.contains(&"WARN-SERIES-LABEL-TRUNCATED"), "nothing was cut or dropped: {codes:?}");
    }

    /// Step 3: the value-led label cannot wrap into the value cap, but the name alone wraps into
    /// the rail cap, so every label drops its value and the series that forced it is named.
    #[test]
    fn a_rail_whose_names_fit_wrapped_only_without_values_drops_every_value() {
        let (svg, warnings) = rail_cascade_render(&["Northwest Regional Operations Division Office", "Paris", "Rome"]);
        assert!(svg.contains(">Paris</text>") && svg.contains(">Rome</text>"), "a rail never mixes value-bearing and name-only labels: {svg}");
        assert!(!svg.contains("60 Paris") && !svg.contains("100 Rome") && !svg.contains("150 Northwest"), "{svg}");
        assert!(!svg.contains('\u{2026}'), "a dropped value saves the name from the cut: {svg}");
        let dropped: Vec<_> = warnings.iter().filter(|w| w.code == "WARN-RAIL-VALUE-DROPPED").collect();
        let [warning] = dropped.as_slice() else { panic!("{warnings:?}") };
        assert_eq!(
            serde_json::to_value(&warning.params).unwrap()["series_names"],
            serde_json::json!(["Northwest Regional Operations Division Office"])
        );
        assert!(warnings.iter().all(|w| w.code != "WARN-SERIES-LABEL-TRUNCATED"), "{warnings:?}");
    }

    /// Step 4: even the name alone would be cut over two lines, so dropping values saves
    /// nothing: the values stay, the name wraps, and only line two's end is cut.
    #[test]
    fn a_rail_whose_names_would_be_cut_anyway_keeps_its_values_and_cuts_line_two() {
        let long = "Northwest Regional Operations and Field Support Division for the Greater Metropolitan Area and Surrounding Counties Coordination Office";
        let (svg, warnings) = rail_cascade_render(&[long, "Paris", "Rome"]);
        assert!(svg.contains(">60 Paris</text>") && svg.contains(">100 Rome</text>") && svg.contains("<tspan>150 Northwest</tspan>"), "{svg}");
        assert!(svg.contains("\u{2026}</tspan>"), "line two is cut: {svg}");
        assert!(warnings.iter().all(|w| w.code != "WARN-RAIL-VALUE-DROPPED"), "{warnings:?}");
        let cut: Vec<_> = warnings.iter().filter(|w| w.code == "WARN-SERIES-LABEL-TRUNCATED").collect();
        let [warning] = cut.as_slice() else { panic!("{warnings:?}") };
        assert_eq!(serde_json::to_value(&warning.params).unwrap()["series_names"], serde_json::json!([long]));
    }

    /// A rail label cut with an ellipsis reveals what it reads uncut on hover, in one title;
    /// a label that is not cut carries no tooltip.
    #[test]
    fn a_cut_rail_label_carries_its_full_text_in_a_title_and_an_uncut_one_none() {
        let long = "Northwest Regional Operations and Field Support Division for the Greater Metropolitan Area and Surrounding Counties Coordination Office";
        let (svg, _) = rail_cascade_render(&[long, "Paris", "Rome"]);
        assert_eq!(svg.matches(&format!("<title>150 {long}</title>")).count(), 1, "{svg}");
        assert_eq!(svg.matches("<title>").count(), 1, "only the cut label carries a title: {svg}");
        let (svg, _) = rail_cascade_render(&["Berlin", "Paris"]);
        assert!(!svg.contains("<title>"), "{svg}");
    }

    /// A stacked area's anchor is a cumulative midpoint, not the series' value,
    /// so its label stays name-only.
    #[test]
    fn a_stacked_rail_label_stays_name_only() {
        let mut p = make_stacked_area_payload("zero");
        p["resolved"]["style"]["endpoint_labels"] = serde_json::json!({"visible": true, "label_offset": 6.0});
        let svg = render_inner(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts(), true).expect("renders");
        assert!(svg.contains(">A</text>") && svg.contains(">B</text>"), "{svg}");
    }

    /// Two series over "Before"/"After": the legend lists them by their value at
    /// the axis's last column ("After"), not at the lexically greatest ("Before").
    #[test]
    fn the_legend_orders_series_by_the_axis_last_category_not_the_lexical_one() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"week": "Before", "series": "P", "signups": 10.0},
            {"week": "After", "series": "P", "signups": 1.0},
            {"week": "Before", "series": "Q", "signups": 1.0},
            {"week": "After", "series": "Q", "signups": 10.0},
        ]);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let q = svg.find(">Q<").expect("Q legend entry");
        let pp = svg.find(">P<").expect("P legend entry");
        assert!(q < pp, "Q ends highest on \"After\": {svg}");
    }

    /// The layered rail anchors each layer at its value on the axis's last
    /// column, not the lexically greatest x.
    #[test]
    fn the_layered_rail_anchors_on_the_axis_last_category() {
        use serde_json::json;
        let mut p = layered_line_payload_with_cross_query_layer();
        p["rows"] = json!([
            {"week": "Before", "signups": 100.0},
            {"week": "After", "signups": 10.0},
        ]);
        p["datasets"] = json!({"trend_query": [
            {"week": "Before", "trend": 10.0},
            {"week": "After", "trend": 100.0},
        ]});
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        p["resolved"]["style"]["axis_y"]["position"] = json!("left");
        p["resolved"]["legend"]["visible"] = json!(false);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let label_y = |label: &str| -> f64 {
            let end = svg.find(&format!(">{label}</text>")).unwrap_or_else(|| panic!("{label} on the rail: {svg}"));
            let tag = &svg[svg[..end].rfind("<text").unwrap()..end];
            tag.split(" y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        assert!(label_y("Trend") < label_y("Signups"), "Trend ends above Signups on \"After\": {svg}");
    }

    /// A labeled calendar bucket that crosses a year boundary: "Q1 2024" sorts before
    /// "Q4 2023" as text, and the rows list the later quarter first, so neither the
    /// lexical max nor the last row is the axis's last column.
    #[test]
    fn a_labeled_bucket_crossing_a_year_boundary_anchors_the_rail_chronologically() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"week": "Q1 2024", "series": "A", "signups": 150.0},
            {"week": "Q4 2023", "series": "A", "signups": 100.0},
            {"week": "Q1 2024", "series": "B", "signups": 60.0},
            {"week": "Q4 2023", "series": "B", "signups": 40.0},
        ]);
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains(">150 A</text>") && svg.contains(">60 B</text>"), "values at Q1 2024: {svg}");
    }

    /// A layer with its own query lists its rows in the opposite order to the base. Each
    /// entry still ends on the axis's last bucket, "Q1 2024", whichever way its rows run.
    #[test]
    fn a_layer_with_its_own_query_in_the_opposite_row_order_anchors_on_the_axis_last_bucket() {
        use serde_json::json;
        let mut p = layered_line_payload_with_cross_query_layer();
        p["rows"] = json!([
            {"week": "Q4 2023", "signups": 100.0},
            {"week": "Q1 2024", "signups": 10.0},
        ]);
        p["datasets"] = json!({"trend_query": [
            {"week": "Q1 2024", "trend": 100.0},
            {"week": "Q4 2023", "trend": 10.0},
        ]});
        p["resolved"]["style"]["endpoint_labels"] = json!({"visible": true, "label_offset": 6.0});
        p["resolved"]["style"]["axis_y"]["position"] = json!("left");
        p["resolved"]["legend"]["visible"] = json!(false);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let label_y = |label: &str| -> f64 {
            let end = svg.find(&format!(">{label}</text>")).unwrap_or_else(|| panic!("{label} on the rail: {svg}"));
            let tag = &svg[svg[..end].rfind("<text").unwrap()..end];
            tag.split(" y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        assert!(label_y("Trend") < label_y("Signups"), "Trend ends above Signups on Q1 2024: {svg}");
    }

    /// A wide `y: [a, b]` chart folds its measures into series; each still reads its
    /// value on the axis's last category ("After"), not the lexically greatest ("Before").
    #[test]
    fn a_wide_rail_reads_each_measure_on_the_axis_last_category() {
        use serde_json::json;
        let mut p = make_wide_line_payload();
        p["rows"] = json!([
            {"month": "Before", "revenue": 100.0, "units_sold": 40.0},
            {"month": "After", "revenue": 150.0, "units_sold": 60.0},
        ]);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains(">150 Revenue<") && svg.contains(">60 Units sold<"), "{svg}");
    }

    /// A null x is no column on the axis: the walk-back skips its row, so its value never
    /// becomes a label's.
    #[test]
    fn a_rail_skips_a_row_with_a_null_x() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "A", "signups": 100.0},
            {"week": "2026-01-08", "series": "A", "signups": 150.0},
            {"week": null, "series": "A", "signups": 999.0},
            {"week": "2026-01-01", "series": "B", "signups": 40.0},
            {"week": "2026-01-08", "series": "B", "signups": 60.0},
        ]);
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains(">150 A</text>"), "{svg}");
    }

    /// The rail's reserved width and its painted x are one law: labels start one rail gap
    /// past the plot's last point, and no painted text passes the canvas edge. A wide title
    /// must not push the rail off the plot it reserved room for.
    #[test]
    fn rail_labels_sit_one_gap_past_the_plot_and_stay_inside_the_canvas() {
        use serde_json::json;
        let attr = |tag: &str, name: &str| -> f64 {
            tag.split(&format!(" {name}=\"")).nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        let months = ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01"];
        let rows: Vec<_> = months
            .iter()
            .enumerate()
            .flat_map(|(i, m)| {
                [
                    json!({"week": m, "series": "Electronics", "signups": 100000.0 + 20000.0 * i as f64}),
                    json!({"week": m, "series": "Accessories", "signups": 60000.0 + 24000.0 * i as f64}),
                ]
            })
            .collect();
        p["rows"] = json!(rows);
        p["width"] = json!(560.0);
        p["resolved"]["title"] = json!("Weekly revenue by category, the long way around, for every region we sell in");
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let canvas = attr(&svg[svg.find("<svg").unwrap()..], "width");
        let label_end = svg.find("Electronics</text>").unwrap();
        let rail_group = &svg[..svg[..label_end].rfind("<text").unwrap()];
        let rail_x: f64 = rail_group.rsplit("translate(").next().unwrap().split(',').next().unwrap().parse().unwrap();
        let plot = svg.find("stroke-width=\"4\"").unwrap();
        let path = &svg[svg[..plot].rfind("<path").unwrap()..plot];
        let line_end: f64 = path.rsplit('L').next().unwrap().split(',').next().unwrap().parse().unwrap();
        let origin: f64 = svg[..svg.find(&path[..40]).unwrap()].rsplit("translate(").next().unwrap().split(',').next().unwrap().parse().unwrap();
        assert!(
            (rail_x - (origin + line_end)).abs() <= 12.0,
            "labels start at the line end plus the gap: rail {rail_x}, line end {}: {svg}",
            origin + line_end
        );
        let label_w = crate::common::measure_at_css_weight(&test_fonts(), "200,000 Electronics", TEST_FONT, 11.0, "400").unwrap();
        assert!(rail_x + label_w <= canvas, "rail text inside the canvas ({canvas}): {svg}");
    }

    /// A wrapped label the stack has no room for sends every value off the rail, and the names
    /// then paint on one line: the pane reserves their width too, so the text stays inside the
    /// pane and the canvas, and nothing is cut.
    #[test]
    fn a_late_value_drop_drops_every_value_and_paints_inside_the_reserved_pane() {
        use serde_json::json;
        let name = "North East Sales Team";
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"week": "2026-01-01", "series": name, "signups": 1.0},
            {"week": "2026-01-08", "series": name, "signups": 2.0},
            {"week": "2026-01-01", "series": "Paris", "signups": 1.0},
            {"week": "2026-01-08", "series": "Paris", "signups": 3.0},
        ]);
        p["width"] = json!(360.0);
        p["height"] = json!(90.0);
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        let (svg, warnings) =
            crate::diagnostic::collect(|| render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders"));
        let codes: Vec<&str> = warnings.iter().map(|w| w.code).collect();
        assert!(codes.contains(&"WARN-RAIL-VALUE-DROPPED"), "the value drops for lack of room: {codes:?}");
        assert!(!codes.contains(&"WARN-SERIES-LABEL-TRUNCATED"), "nothing is cut: {codes:?}");
        assert!(svg.contains(">Paris</text>") && !svg.contains("3 Paris"), "every label drops its value: {svg}");
        let canvas = attr(&svg[svg.find("<svg").unwrap()..], "width");
        let at = svg.find("North East").unwrap();
        let rail_x: f64 = svg[..at].rsplit("translate(").next().unwrap().split(',').next().unwrap().parse().unwrap();
        let painted = svg[at..].split('<').next().unwrap();
        let painted_w = crate::common::measure_at_css_weight(&test_fonts(), painted, TEST_FONT, 11.0, "400").unwrap();
        assert!(rail_x + painted_w <= canvas, "{painted:?} ends at {} of {canvas}: {svg}", rail_x + painted_w);
    }

    /// A value-bearing rail never takes more than the value cap of the card, so the plot is not
    /// starved on a narrow card: "200,000 Electronics" is wider than that at 376px, so the
    /// label takes its next step and wraps, the value leading.
    #[test]
    fn a_narrow_card_wraps_a_rail_label_before_it_starves_the_plot() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "Electronics", "signups": 100000.0},
            {"week": "2026-02-01", "series": "Electronics", "signups": 200000.0},
            {"week": "2026-01-01", "series": "Accessories", "signups": 60000.0},
            {"week": "2026-02-01", "series": "Accessories", "signups": 100000.0},
        ]);
        p["width"] = json!(376.0);
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        let (svg, warnings) =
            crate::diagnostic::collect(|| render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders"));
        assert!(svg.contains("<tspan>200,000</tspan>") && svg.contains("dy=\"13\">Electronics</tspan>"), "{svg}");
        assert!(warnings.iter().all(|w| w.code != "WARN-RAIL-VALUE-DROPPED"), "wrapping is not dropping: {warnings:?}");
    }

    /// `apply_legend_entry_order` is fed by `sorted_series_by_last_value`
    /// (`_cartesian.py:1598-1602`), which breaks a tie on the LAST value
    /// by `(-value, name)` — alphabetically, never first appearance. Both
    /// series here end at 60.0 with "zulu" appearing first in the rows;
    /// production still places "alpha" first because "alpha" < "zulu".
    #[test]
    fn tied_legend_entries_break_alphabetically_not_by_first_appearance() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "zulu", "signups": 100.0},
            {"week": "2026-01-08", "series": "zulu", "signups": 60.0},
            {"week": "2026-01-01", "series": "alpha", "signups": 40.0},
            {"week": "2026-01-08", "series": "alpha", "signups": 60.0},
        ]);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let zulu = svg.find(">zulu<").expect("zulu legend entry");
        let alpha = svg.find(">alpha<").expect("alpha legend entry");
        assert!(
            alpha < zulu,
            "the tie breaks alphabetically, not by row order: alpha first"
        );
    }

    /// Same law, but pinned through the ORDER role rank in the structured
    /// tooltip aria-label rather than the legend's paint position —
    /// `order_domain`'s plain (non-stacked, non-dashed) branch is exactly
    /// `legend_domain`, so a tied last value must rank the two series
    /// alphabetically there too (`_cartesian.py:1598-1602`'s `(-value,
    /// name)` key). Regression coverage for the bug where the port's
    /// comparator had no tie-break at all and left rank order to whatever
    /// `sort_by` happened to do with equal keys.
    #[test]
    fn tied_order_rank_breaks_alphabetically_not_by_first_appearance() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = json!([
            {"week": "2026-01-01", "series": "zulu", "signups": 100.0},
            {"week": "2026-01-08", "series": "zulu", "signups": 60.0},
            {"week": "2026-01-01", "series": "alpha", "signups": 40.0},
            {"week": "2026-01-08", "series": "alpha", "signups": 60.0},
        ]);
        p["resolved"]["style"]["tooltip_format"] = json!(",.2~f");
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(
            svg.contains("\u{2062}alpha; \u{200c}0"),
            "alpha ties zulu at 60.0 and wins the (-value, name) tie-break, so it ranks 0th"
        );
    }

    /// A2: when `detect_time_unit` finds no cadence (28-day steps),
    /// production emits no `values`/`labelExpr`/`timeUnit` and omits
    /// `labelOverlap`, so Vega-Lite falls back to a BARE temporal axis —
    /// `tickCount: {signal: ceil(width/40)}` + `labelOverlap: true`. Vega
    /// then runs d3-time's `timeTicks` of the local time scale over the domain,
    /// formats each tick with d3's multi-scale UTC format, and parity-thins at
    /// `labelSeparation: 16`. Probed on `area_review`'s compiled spec: a
    /// 2024-01-07 → 2025-06-29 domain draws 17 MONTHLY ticks (not one per
    /// datum) labeled `February … December, 2025, February … June`, with
    /// only every fourth label visible.
    #[test]
    fn a_domain_with_no_detectable_cadence_draws_vegas_bare_temporal_axis() {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = make_line_payload(true, false);
        json["rows"] = serde_json::Value::Array(
            (0..20)
                .map(|i| {
                    serde_json::json!({
                        "week": iso_plus_days("2024-01-07", i * 28),
                        "signups": 100.0 + i as f64,
                    })
                })
                .collect(),
        );
        json["resolved"]["style"]["axis_x"]["labels"]["min_gap"] = serde_json::json!(16.0);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");

        assert!(!svg.contains("2024-01-07"), "no raw ISO date label survives");
        let tick_xs = x_tick_xs(&svg);
        assert_eq!(tick_xs.len(), 17, "d3 utcMonth ticks, not one per datum: {tick_xs:?}");
        assert!(
            tick_xs[0] > 1.0,
            "the first datum (2024-01-07) is not a month start, so it gets no tick: {tick_xs:?}"
        );
        assert_eq!(
            x_label_texts(&svg),
            vec!["February", "June", "October", "February", "June"],
            "d3's multi-scale format (%B at a month boundary, %Y at a year one), parity-thinned"
        );
    }

    /// An axis `time_unit: monthofyear` puts every date on the part's own cycle: the points draw in
    /// month order (Vega-Lite sorts a line by the x it plots) and the ticks name the months.
    #[test]
    fn a_month_of_year_axis_draws_its_points_in_month_order_on_the_months() {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = make_line_payload(true, false);
        json["rows"] = serde_json::json!([
            {"week": "2022-03-17", "signups": 100.0},
            {"week": "2023-01-25", "signups": 150.0},
            {"week": "2024-12-19", "signups": 120.0},
        ]);
        json["resolved"]["style"]["axis_x"]["time_unit"] = serde_json::json!("monthofyear");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert_eq!(x_label_texts(&svg).first().map(String::as_str), Some("Jan"), "{svg}");
        assert_eq!(x_label_texts(&svg).last().map(String::as_str), Some("Dec"), "{svg}");
        let path = main_line_d(&svg);
        let ys: Vec<&str> = path.split(['M', 'L']).filter(|p| !p.is_empty()).map(|p| p.split(',').nth(1).unwrap()).collect();
        assert_eq!(ys.len(), 3, "{path}");
        assert!(path.starts_with("M0,"), "the January point sits at the range start: {path}");
    }

    /// An authored `labels.values` blanks the label of every tick outside the set; the ticks stay.
    #[test]
    fn labels_values_blank_the_labels_outside_the_set() {
        let _locale = crate::time_locale::en_us_scope();
        let months = (0..12).map(|i| (2024, i + 1));
        let mut payload = authored_tick_payload(month_rows(months), "yearmonth", None);
        payload.resolved["style"]["axis_x"]["ticks"]["time_unit"] = serde_json::Value::Null;
        payload.resolved["style"]["axis_x"]["labels"]["values"] = serde_json::json!(["2024-02-01", "20240501"]);
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert_eq!(x_label_texts(&svg), ["Feb", "May"], "{svg}");
        assert_eq!(x_tick_xs(&svg).len(), 12);
    }

    /// A bare temporal axis with no label grain rotates every tick at the authored angle and thins none.
    #[test]
    fn an_authored_angle_on_a_bare_axis_with_no_label_grain_rotates_every_label() {
        let _locale = crate::time_locale::en_us_scope();
        let mut json = make_line_payload(true, false);
        json["rows"] = serde_json::Value::Array(
            (0..20)
                .map(|i| serde_json::json!({"week": iso_plus_days("2024-01-07", i * 28), "signups": 100.0 + i as f64}))
                .collect(),
        );
        json["resolved"]["style"]["axis_x"]["labels"]["angle"] = serde_json::json!(30.0);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(svg.matches("rotate(30)").count() >= 5 && !svg.contains("text-anchor=\"middle\""), "{svg}");
    }

    fn authored_tick_payload(rows: serde_json::Value, unit: &str, step: Option<f64>) -> Payload {
        let mut json = make_line_payload(true, false);
        json["rows"] = rows;
        let axis = &mut json["resolved"]["style"]["axis_x"];
        axis["grid"]["visible"] = serde_json::json!(true);
        let ticks = &mut axis["ticks"];
        ticks["time_unit"] = serde_json::json!(unit);
        ticks["step"] = serde_json::json!(step);
        ticks["count"] = serde_json::Value::Null;
        serde_json::from_value(json).unwrap()
    }

    fn weekly_rows(first: &str, gap_days: i64, n: i64) -> serde_json::Value {
        serde_json::Value::Array(
            (0..n)
                .map(|i| {
                    serde_json::json!({
                        "week": iso_plus_days(first, i * gap_days),
                        "signups": 100.0 + i as f64,
                    })
                })
                .collect(),
        )
    }

    /// One row per listed month start (`YYYY-MM`), so a gap is just a month
    /// left out of the list.
    fn month_rows(months: impl IntoIterator<Item = (i32, u32)>) -> serde_json::Value {
        serde_json::Value::Array(
            months
                .into_iter()
                .enumerate()
                .map(|(i, (y, m))| {
                    let week = format!("{y}-{m:02}-01");
                    serde_json::json!({"week": week, "signups": 100.0 + i as f64})
                })
                .collect(),
        )
    }

    /// x positions of the vertical gridlines spanning the plot.
    fn x_grid_xs(svg: &str) -> Vec<f64> {
        let mut xs = Vec::new();
        for tag in svg.split("<line").skip(1) {
            let tag = &tag[..tag.find("/>").unwrap_or(tag.len())];
            let attr = |k: &str| -> Option<f64> {
                let i = tag.find(&format!("{k}=\""))? + k.len() + 2;
                tag[i..].split('"').next()?.parse().ok()
            };
            let (Some(x1), Some(x2), Some(y1), Some(y2)) =
                (attr("x1"), attr("x2"), attr("y1"), attr("y2"))
            else {
                continue;
            };
            if (x1 - x2).abs() < 1e-9 && y2 - y1 > 50.0 {
                xs.push(x1);
            }
        }
        xs
    }

    /// Positions relative to the first, as a share of the last: tick `i` of
    /// calendar days `days` sits at `(days[i] - days[0]) / (days[n] - days[0])`
    /// of the span the ticks cover, whatever the plot width.
    fn assert_on_calendar(xs: &[f64], days: &[i64]) {
        assert_eq!(xs.len(), days.len(), "{xs:?}");
        let (span_x, span_d) = (xs[xs.len() - 1] - xs[0], (days[days.len() - 1] - days[0]) as f64);
        for (x, d) in xs.iter().zip(days) {
            let want = (d - days[0]) as f64 / span_d;
            assert!(
                ((x - xs[0]) / span_x - want).abs() < 0.01,
                "tick at {x} is off its calendar day {d}: {xs:?} vs {days:?}"
            );
        }
    }

    fn day_of(y: i32, m: u8, d: u8) -> i64 {
        crate::families::temporal::days_from_civil(y, m, d)
    }

    /// An authored `ticks.time_unit` + `step` is production's
    /// `tickCount: {interval, step}` (`type_inference.py:1026-1052`): on the
    /// bare axis Vega ticks at `utcMonth.every(3)` — Jan/Apr/Jul/Oct — not
    /// the count-driven monthly ladder the same data draws unauthored (17).
    #[test]
    fn an_authored_tick_cadence_replaces_the_bare_axis_ladder() {
        let _locale = crate::time_locale::en_us_scope();
        let rows = weekly_rows("2024-01-07", 28, 20);
        let svg = render(&authored_tick_payload(rows, "yearmonth", Some(3.0)), &test_fonts())
            .expect("renders");
        let days = [
            day_of(2024, 4, 1),
            day_of(2024, 7, 1),
            day_of(2024, 10, 1),
            day_of(2025, 1, 1),
            day_of(2025, 4, 1),
        ];
        assert_on_calendar(&x_tick_xs(&svg), &days);
        assert_on_calendar(&x_grid_xs(&svg), &days);
    }

    /// The same law on a bucketed (yearly) axis: ticks and gridlines sit on
    /// the authored years only, and each tick speaks.
    #[test]
    fn an_authored_tick_cadence_ticks_a_bucketed_axis_on_its_own_boundaries() {
        let rows = serde_json::Value::Array(
            (2006..=2025)
                .map(|y| serde_json::json!({"week": format!("{y}-01-01"), "signups": y as f64}))
                .collect(),
        );
        let payload = authored_tick_payload(rows, "year", Some(5.0));
        let svg = render(&payload, &test_fonts()).expect("renders");
        let days = [day_of(2010, 1, 1), day_of(2015, 1, 1), day_of(2020, 1, 1), day_of(2025, 1, 1)];
        assert_on_calendar(&x_tick_xs(&svg), &days);
        assert_on_calendar(&x_grid_xs(&svg), &days);
        assert_eq!(x_label_texts(&svg), vec!["2010", "2015", "2020", "2025"]);
    }

    /// Weekly Monday rows under monthly ticks: only two month starts fall on a
    /// row's Monday (Apr 1, Jul 1), yet production labels all eleven ticks —
    /// a tick speaks because its own date opens a label period, not because
    /// a row sits on it. Probed against the reference render.
    #[test]
    fn authored_ticks_finer_than_the_rows_label_every_tick() {
        let rows = weekly_rows("2024-01-01", 7, 46);
        let svg = render(&authored_tick_payload(rows, "yearmonth", None), &test_fonts())
            .expect("renders");
        let days: Vec<i64> = (1..=11).map(|m| day_of(2024, m, 1)).collect();
        assert_on_calendar(&x_tick_xs(&svg), &days);
        assert_on_calendar(&x_grid_xs(&svg), &days);
        assert_eq!(
            x_label_texts(&svg),
            ["Jan", "2024", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov"]
        );
    }

    /// Monthly rows under weekly ticks: the label gate has no day clause for
    /// monthly data, so every Sunday of Jan/Apr/Jul/Oct speaks (and every
    /// January one carries its year).
    #[test]
    fn authored_ticks_coarser_than_the_rows_repeat_the_opening_months_label() {
        let _locale = crate::time_locale::en_us_scope();
        let months = (0..24).map(|i| (2024 + i / 12, (i % 12 + 1) as u32));
        let mut payload = authored_tick_payload(month_rows(months), "yearweek", None);
        let labels = &mut payload.resolved["style"]["axis_x"]["labels"];
        labels["time_unit"] = serde_json::json!("yearquarter");
        let svg = render(&payload, &test_fonts()).expect("renders");
        let sundays: Vec<i64> = (day_of(2024, 1, 1)..=day_of(2025, 12, 1))
            .filter(|d| (d + 4).rem_euclid(7) == 0)
            .collect();
        assert_on_calendar(&x_grid_xs(&svg), &sundays);
        assert_on_calendar(&x_tick_xs(&svg), &sundays);
        // The Sundays of Jan/Apr/Jul/Oct speak (the gate has no day clause
        // for monthly rows), each January one with its year row.
        let mut want: Vec<&str> = Vec::new();
        for &day in &sundays {
            let (y, m, _) = crate::families::temporal::civil_from_days(day);
            if (m - 1) % 3 == 0 {
                want.push(["Q1", "Q2", "Q3", "Q4"][usize::from((m - 1) / 3)]);
                if m == 1 {
                    want.push(if y == 2024 { "2024" } else { "2025" });
                }
            }
        }
        assert_eq!(x_label_texts(&svg), want);
    }

    /// A month the rows skip is still a tick: Q3 and Q4 2024 sit in a hole
    /// of the data and production still labels them.
    #[test]
    fn authored_ticks_label_a_quarter_the_rows_skip() {
        let _locale = crate::time_locale::en_us_scope();
        let months = [(2024, 2), (2024, 3), (2024, 4), (2024, 5)]
            .into_iter()
            .chain((0..13).map(|i| (2024 + (11 + i) / 12, ((11 + i) % 12 + 1) as u32)));
        let mut payload = authored_tick_payload(month_rows(months), "yearmonth", None);
        let labels = &mut payload.resolved["style"]["axis_x"]["labels"];
        labels["time_unit"] = serde_json::json!("yearquarter");
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert_eq!(
            x_label_texts(&svg),
            ["Q2", "2024", "Q3", "Q4", "Q1", "2025", "Q2", "Q3", "Q4"]
        );
        let months: Vec<i64> = (0..23)
            .map(|i| day_of(2024 + (1 + i) / 12, ((1 + i) % 12 + 1) as u8, 1))
            .collect();
        assert_on_calendar(&x_grid_xs(&svg), &months);
    }

    fn count_tick_payload(rows: serde_json::Value, count: f64) -> Payload {
        let mut json = make_line_payload(true, false);
        json["rows"] = rows;
        json["resolved"]["style"]["axis_x"]["ticks"]["count"] = serde_json::json!(count);
        json["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        serde_json::from_value(json).unwrap()
    }

    /// An authored domain narrower than the data leaves a zero-width plot: every
    /// label sits on the one pixel and Vega's flush tie goes to the end, so each
    /// text is end-anchored.
    #[test]
    fn labels_on_a_zero_width_plot_all_end_anchor() {
        let _locale = crate::time_locale::en_us_scope();
        let rows: Vec<_> = (0..24)
            .map(|h| serde_json::json!({"week": format!("2024-03-05T{h:02}:00:00"), "signups": 100.0 + h as f64}))
            .collect();
        let mut json = make_line_payload(true, false);
        json["rows"] = serde_json::Value::Array(rows);
        json["resolved"]["style"]["axis_x"]["scale"] =
            serde_json::json!({"continuous": {"domain": ["2024-03-05T06:00:00", "2024-03-05T15:00:00"]}});
        let svg = render(&serde_json::from_value(json).unwrap(), &test_fonts()).expect("renders");
        let labels: Vec<&str> = svg.split("<text ").filter(|t| t.contains(">6am<") || t.contains(">2pm<")).collect();
        assert_eq!(labels.len(), 2, "{svg}");
        assert!(labels.iter().all(|t| t.contains("text-anchor=\"end\"")), "{labels:?}");
    }

    /// Three Mondays three weeks apart have no calendar cadence, so the axis
    /// is Vega's bare time scale and an authored `ticks.count` is its
    /// `tickCount: N` — the 2-day rung at 12 (22 ticks over the 42 days), the
    /// week rung at 3 (6 Sundays) — whatever the pane width the default
    /// `ceil(plot_w / 40)` would read.
    #[test]
    fn an_authored_tick_count_picks_the_d3_rung_on_a_bare_temporal_axis() {
        let _locale = crate::time_locale::en_us_scope();
        let rows = weekly_rows("2024-01-01", 21, 3);
        for (count, ticks) in [(3.0, 6), (12.0, 22)] {
            let svg = render(&count_tick_payload(rows.clone(), count), &test_fonts())
                .expect("renders");
            assert_eq!(x_tick_xs(&svg).len(), ticks, "ticks.count {count}: {svg}");
        }
    }

    /// Eighteen monthly rows under `ticks.count: 5` tick the 3-month rung of
    /// d3's ladder (Jan, Apr, Jul, Oct, Jan, Apr: 6 ticks), not the 18 rows
    /// and not the month grain's own boundaries.
    #[test]
    fn an_authored_tick_count_ticks_the_count_ladder_on_a_bucketed_axis() {
        let _locale = crate::time_locale::en_us_scope();
        let months = (0..18).map(|i| (2023 + i / 12, (i % 12 + 1) as u32));
        let svg = render(&count_tick_payload(month_rows(months), 5.0), &test_fonts())
            .expect("renders");
        assert_eq!(x_tick_xs(&svg).len(), 6, "{svg}");
    }

    /// A day-grained bucketed axis under `ticks.count` ticks the d3 ladder
    /// over its span: a couple of days fall on the half-days.
    #[test]
    fn an_authored_tick_count_on_a_day_grained_bucketed_axis_ticks_its_half_days() {
        let _locale = crate::time_locale::en_us_scope();
        let svg = render(
            &count_tick_payload(weekly_rows("2024-03-05", 1, 2), 5.0),
            &test_fonts(),
        )
        .expect("renders");
        assert_eq!(x_tick_xs(&svg).len(), 3, "{svg}");
    }

    /// d3 ticks nothing for a count below 1, so the axis draws no x ticks.
    #[test]
    fn an_authored_tick_count_below_one_draws_no_x_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        for count in [0.0, -3.0] {
            let svg = render(&count_tick_payload(weekly_rows("2024-01-01", 21, 3), count), &test_fonts())
                .expect("renders");
            assert!(x_tick_xs(&svg).is_empty(), "ticks.count {count}: {svg}");
        }
    }

    /// Rows on the 15th and 14th have no row on a month start, but the gaps
    /// are whole months (`_detect_cadence`), so production's axis is a monthly
    /// one: `timeUnit: yearmonth` floors each row to its month start and the
    /// ticks are the eight month starts, not a daily or weekly ladder over
    /// the raw dates.
    #[test]
    fn roughly_monthly_rows_off_the_month_start_draw_a_monthly_axis() {
        let _locale = crate::time_locale::en_us_scope();
        let days = [
            "2024-01-15", "2024-02-14", "2024-03-15", "2024-04-14", "2024-05-15", "2024-06-14",
            "2024-07-15", "2024-08-14",
        ];
        let rows: Vec<serde_json::Value> = days
            .iter()
            .enumerate()
            .map(|(i, d)| serde_json::json!({"week": d, "signups": 100.0 + i as f64}))
            .collect();
        let mut json = make_line_payload(true, false);
        json["rows"] = serde_json::Value::Array(rows);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert_eq!(x_tick_xs(&svg).len(), 8, "one tick per month start: {svg}");
    }

    /// The x-axis label texts grouped by position, left to right (a two-row
    /// label is one group).
    fn x_label_groups(svg: &str) -> Vec<Vec<String>> {
        let mut groups: Vec<(f64, Vec<String>)> = Vec::new();
        for (x, text) in x_label_positions(svg) {
            match groups.last_mut() {
                Some((gx, texts)) if (*gx - x).abs() < 0.01 => texts.push(text),
                _ => groups.push((x, vec![text])),
            }
        }
        groups.into_iter().map(|(_, texts)| texts).collect()
    }

    /// `(x, text)` of the x-axis label rows, left to right.
    fn x_label_positions(svg: &str) -> Vec<(f64, String)> {
        let row_y = x_tick_bottom(svg);
        let mut out: Vec<(f64, String)> = svg
            .split("<text")
            .skip(1)
            .filter(|t| t.contains("fill=\"#626366\""))
            .filter_map(|t| {
                let (head, body) = t.split_once('>')?;
                let attr = |k: &str| -> Option<f64> {
                    let i = head.find(&format!("{k}=\""))? + k.len() + 2;
                    head[i..].split('"').next()?.parse().ok()
                };
                (attr("y")? > row_y).then(|| Some((attr("x")?, body.split('<').next()?.to_string())))?
            })
            .collect();
        out.sort_by(|a, b| a.0.total_cmp(&b.0));
        out
    }

    /// An authored `sort` over an `axis_x.type: ordinal` date axis ranks the
    /// buckets by the measure; each bucket's label stays on its own bucket, so
    /// the later months (the larger values) sit to the left under `desc`.
    #[test]
    fn a_sort_on_an_authored_ordinal_date_axis_moves_each_label_with_its_bucket() {
        let mut json = make_line_payload(true, false);
        json["rows"] = month_rows((1..=6).map(|m| (2024, m)));
        json["resolved"]["style"]["axis_x"]["type"] = serde_json::json!("ordinal");
        json["resolved"]["sort"] = serde_json::json!({"by": "signups", "order": "desc"});
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let months: Vec<String> =
            x_label_positions(&svg).into_iter().map(|l| l.1).filter(|t| t != "2024").collect();
        assert_eq!(months, ["Jun", "May", "Apr", "Mar", "Feb", "Jan"], "{svg}");
    }

    fn rail_json(rows: serde_json::Value) -> serde_json::Value {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = rows;
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        p["resolved"]["style"]["series_label"]["dark_companion_palette"] =
            json!(["#111111", "#222222"]);
        p
    }

    fn authored_tick_rail_payload(
        rows: serde_json::Value,
        unit: &str,
        step: Option<f64>,
    ) -> Payload {
        use serde_json::json;
        let mut p = rail_json(rows);
        let ticks = &mut p["resolved"]["style"]["axis_x"]["ticks"];
        ticks["time_unit"] = json!(unit);
        ticks["step"] = json!(step);
        ticks["count"] = serde_json::Value::Null;
        serde_json::from_value(p).unwrap()
    }

    fn two_series_rows(dates: impl Iterator<Item = String>) -> serde_json::Value {
        use serde_json::json;
        serde_json::Value::Array(
            dates
                .enumerate()
                .flat_map(|(i, d)| {
                    [
                        json!({"week": d, "series": "Core", "signups": 100.0 + i as f64}),
                        json!({"week": d, "series": "Growth", "signups": 50.0 + i as f64}),
                    ]
                })
                .collect(),
        )
    }

    /// The endpoint-rail arm computes its x ink in closed form and has no
    /// authored-tick geometry, so a bucketed axis there refuses by name — with
    /// more rows than ticks too, where a row-indexed read of the ticks would
    /// run off their end.
    #[test]
    fn an_authored_tick_cadence_reaches_a_bucketed_rail_axis() {
        let _locale = crate::time_locale::en_us_scope();
        for (step, months) in [(None, (0..24).collect::<Vec<_>>()), (Some(3.0), (0..24).step_by(3).collect())] {
            let dates = (0..24).map(|i| format!("{}-{:02}-01", 2024 + i / 12, i % 12 + 1));
            let payload = authored_tick_rail_payload(two_series_rows(dates), "yearmonth", step);
            let svg = render(&payload, &test_fonts()).expect("renders");
            let days: Vec<_> = months.iter().map(|m| day_of(2024 + m / 12, (m % 12 + 1) as u8, 1)).collect();
            assert_on_calendar(&x_tick_xs(&svg), &days);
        }
    }

    /// Boundary ticks separate one period's label from the next, so an interval axis draws them where the theme hides ticks.
    #[test]
    fn an_interval_axis_draws_its_boundary_ticks_though_the_theme_hides_them() {
        let _locale = crate::time_locale::en_us_scope();
        let months = (0..24).map(|i| (2024 + i / 12, (i % 12 + 1) as u32));
        let mut json = make_line_payload(true, false);
        json["rows"] = month_rows(months);
        json["resolved"]["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!("yearquarter");
        json["resolved"]["style"]["axis_x"]["ticks"]["visible"] = serde_json::json!(false);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert_eq!(x_tick_xs(&svg).len(), 8, "a boundary tick for each quarter the data opens");
    }

    /// Interval mode on the endpoint-rail arm: quarter labels sit at their
    /// period midpoints, between the quarter-boundary ticks.
    #[test]
    fn quarter_labels_on_the_rail_arm_sit_between_the_boundary_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let dates = (0..24).map(|i| format!("{}-{:02}-01", 2024 + i / 12, i % 12 + 1));
        let mut json = rail_json(two_series_rows(dates));
        json["resolved"]["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!("yearquarter");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let ticks = x_tick_xs(&svg);
        let labels: Vec<f64> = x_label_positions(&svg)
            .into_iter()
            .filter(|l| l.1.starts_with('Q'))
            .map(|l| l.0)
            .collect();
        assert_eq!(labels.len(), 8, "{svg}");
        assert_eq!(ticks.len(), 8, "{ticks:?}");
        for (label, pair) in labels.iter().zip(ticks.windows(2)) {
            let mid = (pair[0] + pair[1]) / 2.0;
            assert!((label - mid).abs() < 0.6, "label at {label}, period midpoint {mid}: {ticks:?}");
        }
    }

    /// An authored `labels.align` keeps labels on their ticks.
    #[test]
    fn an_authored_label_align_keeps_quarter_labels_on_the_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let months = (0..24).map(|i| (2024 + i / 12, (i % 12 + 1) as u32));
        let mut json = make_line_payload(true, false);
        json["rows"] = month_rows(months);
        let labels = &mut json["resolved"]["style"]["axis_x"]["labels"];
        labels["time_unit"] = serde_json::json!("yearquarter");
        labels["align"] = serde_json::json!("left");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let ticks = x_tick_xs(&svg);
        let labels: Vec<_> = x_label_positions(&svg).into_iter().filter(|l| l.1.starts_with('Q')).take(ticks.len()).collect();
        assert!(!labels.is_empty(), "no quarter labels");
        for (x, text) in labels {
            assert!(ticks.iter().any(|t| (t - x).abs() < 0.6), "{text} at {x} is off the ticks {ticks:?}");
        }
    }

    /// A bare axis (28-day rows) reaches the rail arm, which draws Vega's own
    /// ticks: the authored interval replaces the count-driven ladder there.
    #[test]
    fn an_authored_tick_cadence_reaches_the_bare_rail_axis() {
        let _locale = crate::time_locale::en_us_scope();
        let dates = (0..20).map(|i| iso_plus_days("2024-01-07", i * 28));
        let payload = authored_tick_rail_payload(two_series_rows(dates), "yearmonth", Some(3.0));
        let svg = render(&payload, &test_fonts()).expect("renders");
        let days = [
            day_of(2024, 4, 1),
            day_of(2024, 7, 1),
            day_of(2024, 10, 1),
            day_of(2025, 1, 1),
            day_of(2025, 4, 1),
        ];
        assert_on_calendar(&x_tick_xs(&svg), &days);
    }

    /// A payload on the default cadence: no authored `ticks.time_unit`, no
    /// `ticks.count`, gridlines on so each tick shows twice.
    fn native_tick_payload(rows: serde_json::Value) -> Payload {
        let mut json = make_line_payload(true, false);
        json["rows"] = rows;
        let axis = &mut json["resolved"]["style"]["axis_x"];
        axis["grid"]["visible"] = serde_json::json!(true);
        axis["ticks"]["count"] = serde_json::Value::Null;
        serde_json::from_value(json).unwrap()
    }

    fn dated_rows<'a>(dates: impl IntoIterator<Item = &'a str>) -> serde_json::Value {
        serde_json::Value::Array(
            dates
                .into_iter()
                .enumerate()
                .map(|(i, week)| serde_json::json!({"week": week, "signups": 100.0 + i as f64}))
                .collect(),
        )
    }

    /// With nothing authored and no coarser label grain, production hands
    /// Vega `tickCount: {interval, step}` from the label grain
    /// (`type_inference.py:1049-1052`): June and July have no row, yet the
    /// reference render draws their tick, gridline and label (probed against
    /// `vl_convert`: Jan/2024, Feb ... Dec, twelve labels over ten rows).
    #[test]
    fn a_native_monthly_axis_draws_the_months_no_row_fills() {
        let _locale = crate::time_locale::en_us_scope();
        let months = (1..=12u32).filter(|m| !matches!(m, 6 | 7)).map(|m| (2024, m));
        let svg = render(&native_tick_payload(month_rows(months)), &test_fonts()).expect("renders");
        let days: Vec<i64> = (1..=12).map(|m| day_of(2024, m, 1)).collect();
        assert_on_calendar(&x_tick_xs(&svg), &days);
        assert_on_calendar(&x_grid_xs(&svg), &days);
        let want = [
            "Jan", "2024", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
            "Dec",
        ];
        assert_eq!(x_label_texts(&svg), want);
    }

    /// The same law on a weekly axis: the skipped week of Jan 22 keeps its
    /// tick (a Sunday, where `utcWeek` boundaries sit), and each Sunday tick
    /// reads as its Monday bucket (`utcOffset('day', ..., 1)`,
    /// `time_unit_detect.py:2013-2017`), so the texts are the Mondays 1, 8,
    /// 15, 22, 29 with the anchor's `Jan'24` context.
    #[test]
    fn a_native_weekly_axis_draws_the_week_no_row_fills_on_its_monday() {
        let rows = dated_rows(["2024-01-01", "2024-01-08", "2024-01-15", "2024-01-29"]);
        let svg = render(&native_tick_payload(rows), &test_fonts()).expect("renders");
        let sundays: Vec<i64> =
            [(2023, 12, 31), (2024, 1, 7), (2024, 1, 14), (2024, 1, 21), (2024, 1, 28)]
                .into_iter()
                .map(|(y, m, d)| day_of(y, m, d))
                .collect();
        assert_on_calendar(&x_tick_xs(&svg), &sundays);
        assert_on_calendar(&x_grid_xs(&svg), &sundays);
        assert_eq!(x_label_texts(&svg), ["1", "Jan'24", "8", "15", "22", "29"]);
    }

    /// Rows on a Wednesday floor to the same Sunday buckets, and the text is
    /// still the bucket's Monday, never the row's own day.
    #[test]
    fn a_native_weekly_axis_reads_the_monday_of_a_wednesday_row() {
        let rows = dated_rows(["2024-01-03", "2024-01-10", "2024-01-17", "2024-01-31"]);
        let svg = render(&native_tick_payload(rows), &test_fonts()).expect("renders");
        assert_eq!(x_label_texts(&svg), ["1", "Jan'24", "8", "15", "22", "29"]);
    }

    #[test]
    fn a_native_quarterly_axis_draws_the_quarter_no_row_fills() {
        let rows =
            dated_rows(["2023-01-01", "2023-04-01", "2023-10-01", "2024-01-01", "2024-04-01"]);
        let svg = render(&native_tick_payload(rows), &test_fonts()).expect("renders");
        let days: Vec<i64> =
            [(2023, 1), (2023, 4), (2023, 7), (2023, 10), (2024, 1), (2024, 4)]
                .into_iter()
                .map(|(y, m)| day_of(y, m, 1))
                .collect();
        assert_on_calendar(&x_tick_xs(&svg), &days);
        assert_on_calendar(&x_grid_xs(&svg), &days);
        assert_eq!(x_label_texts(&svg), ["Q1", "2023", "Q2", "Q3", "Q4", "Q1", "2024", "Q2"]);
    }

    #[test]
    fn a_native_yearly_axis_draws_the_year_no_row_fills() {
        let rows =
            dated_rows(["2015-01-01", "2016-01-01", "2017-01-01", "2019-01-01", "2020-01-01"]);
        let svg = render(&native_tick_payload(rows), &test_fonts()).expect("renders");
        let days: Vec<i64> = (2015..=2020).map(|y| day_of(y, 1, 1)).collect();
        assert_on_calendar(&x_tick_xs(&svg), &days);
        assert_on_calendar(&x_grid_xs(&svg), &days);
        assert_eq!(x_label_texts(&svg), ["2015", "2016", "2017", "2018", "2019", "2020"]);
    }

    #[test]
    fn a_native_daily_axis_draws_the_day_no_row_fills() {
        let _locale = crate::time_locale::en_us_scope();
        let dates = ["2024-03-01", "2024-03-02", "2024-03-03", "2024-03-04", "2024-03-06"];
        let svg = render(&native_tick_payload(dated_rows(dates)), &test_fonts()).expect("renders");
        let days: Vec<i64> = (1..=6).map(|d| day_of(2024, 3, d)).collect();
        assert_on_calendar(&x_tick_xs(&svg), &days);
        assert_on_calendar(&x_grid_xs(&svg), &days);
        assert_eq!(x_label_texts(&svg), ["1", "Mar'24", "2", "3", "4", "5", "6"]);
    }

    /// A narrow card: the label ladder's remedies on, as a resolved payload
    /// carries them (`overlap` skip and tilt, the theme's `min_gap`).
    fn narrow_tick_payload(rows: serde_json::Value, width: f64) -> Payload {
        let mut payload = native_tick_payload(rows);
        payload.width = width;
        let labels = &mut payload.resolved["style"]["axis_x"]["labels"];
        labels["overlap"] = serde_json::json!({"skip": true, "tilt": true});
        labels["min_gap"] = serde_json::json!(16.0);
        payload
    }

    /// A card too narrow for twelve month labels steps up to quarters, enumerated from the calendar span: July
    /// opens a quarter though no row sits on it, so the axis ticks Jan, Apr, Jul and Oct and names each quarter
    /// between its ticks.
    #[test]
    fn a_thinned_monthly_axis_openers_include_a_month_no_row_fills() {
        let _locale = crate::time_locale::en_us_scope();
        let months = (1..=12u32).filter(|m| *m != 7).map(|m| (2024, m));
        let payload = narrow_tick_payload(month_rows(months), 300.0);
        let svg = render(&payload, &test_fonts()).expect("renders");
        let days: Vec<i64> = [1, 4, 7, 10].into_iter().map(|m| day_of(2024, m, 1)).collect();
        assert_on_calendar(&x_tick_xs(&svg), &days);
        assert_on_calendar(&x_grid_xs(&svg), &days);
        assert_eq!(x_label_texts(&svg), ["Q1", "2024", "Q2", "Q3", "Q4"]);
    }

    /// Weekly rows under month labels: the first week of February has no row, yet the axis ticks the month
    /// boundaries Jan 1, Feb 1 and Mar 1 and names each month between them.
    #[test]
    fn a_thinned_weekly_axis_openers_include_a_week_no_row_fills() {
        let dates = [
            "2024-01-01", "2024-01-08", "2024-01-15", "2024-01-22", "2024-01-29", "2024-02-12",
            "2024-02-19", "2024-02-26", "2024-03-04", "2024-03-11", "2024-03-18",
        ];
        let payload = narrow_tick_payload(dated_rows(dates), 300.0);
        let svg = render(&payload, &test_fonts()).expect("renders");
        let days: Vec<i64> =
            [(1, 1), (2, 1), (3, 1)].into_iter().map(|(m, d)| day_of(2024, m, d)).collect();
        assert_on_calendar(&x_tick_xs(&svg), &days);
        assert_on_calendar(&x_grid_xs(&svg), &days);
        assert_eq!(x_label_texts(&svg), ["Jan", "2024", "Feb", "Mar"]);
    }

    /// Daily rows from `first` for `days` days on a 600px card: weekly ticks (Mondays) carry the day labels.
    fn daily_week_tick_labels(first: &str, days: i64) -> Vec<String> {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> = (0..days).map(|i| iso_plus_days(first, i)).collect();
        let mut payload = native_tick_payload(dated_rows(dates.iter().map(String::as_str)));
        payload.width = 600.0;
        let svg = render(&payload, &test_fonts()).expect("renders");
        x_label_texts(&svg)
    }

    /// Mar 3 is 6 of 7 days before the Mar 9 tick, and May 17 is 6 of 7 past the May 11 tick: both end points keep
    /// the room a label needs beside their neighbor and are named, the first carrying the context row its tick gives up.
    #[test]
    fn a_week_ticked_axis_labels_a_first_and_last_point_with_room_beside_their_neighbor() {
        let labels = daily_week_tick_labels("2026-03-03", 76);
        assert_eq!(labels.first().map(String::as_str), Some("3"), "{labels:?}");
        assert_eq!(labels.get(1).map(String::as_str), Some("Mar'26"), "{labels:?}");
        assert_eq!(labels.last().map(String::as_str), Some("17"), "{labels:?}");
    }

    /// Mar 6 is 3 of 7 days before the first tick and May 12 one day past the last: both would crowd their neighbor.
    #[test]
    fn a_week_ticked_axis_leaves_end_points_crowding_their_neighbor_unlabeled() {
        let labels = daily_week_tick_labels("2026-03-06", 68);
        assert_eq!(labels.first().map(String::as_str), Some("9"), "{labels:?}");
        assert_eq!(labels.last().map(String::as_str), Some("11"), "{labels:?}");
    }

    /// A month label over a domain inside one month has no boundary to center between: the axis keeps its own
    /// weekly labels instead of one label for the whole domain.
    #[test]
    fn a_month_label_over_a_one_month_domain_falls_back_to_week_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> = (0..23).map(|i| iso_plus_days("2026-03-03", i)).collect();
        let mut payload = native_tick_payload(dated_rows(dates.iter().map(String::as_str)));
        payload.width = 600.0;
        payload.resolved["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!("yearmonth");
        let labels = x_label_texts(&render(&payload, &test_fonts()).expect("renders"));
        assert!(labels.iter().any(|l| l == "16"), "weekly day labels: {labels:?}");
        assert!(!labels.iter().any(|l| l == "Mar"), "no single month label: {labels:?}");
    }

    /// Twenty-four years of monthly rows on a 300px card: year labels cannot all fit, so the axis thins them on
    /// their ticks rather than printing every year overlapped between boundary ticks.
    #[test]
    fn a_long_monthly_range_thins_year_labels_on_their_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let months = (0..24 * 12).map(|i| (2000 + i / 12, (i % 12 + 1) as u32));
        let payload = narrow_tick_payload(month_rows(months), 300.0);
        let svg = render(&payload, &test_fonts()).expect("renders");
        let labels = x_label_texts(&svg);
        assert_eq!(labels, ["2000", "2005", "2010", "2015", "2020"]);
        assert_eq!(x_tick_xs(&svg).len(), labels.len(), "ticks follow the thinned labels");
    }

    /// Ten years of daily rows ending Dec 31: the last year is one day short of whole, yet its label fits its slice.
    #[test]
    fn a_daily_range_ending_on_dec_31_keeps_its_last_full_year() {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> = (0..3653).map(|i| iso_plus_days("2016-01-01", i)).collect();
        let payload = narrow_tick_payload(dated_rows(dates.iter().map(String::as_str)), 300.0);
        let labels = x_label_texts(&render(&payload, &test_fonts()).expect("renders"));
        assert!(labels.iter().any(|l| l == "2025"), "{labels:?}");
    }

    /// The first point labels itself in the on-tick fallback and the end labels anchor outward, the first
    /// at the plot's left edge and the last at its right.
    #[test]
    fn end_labels_anchor_outward_in_the_on_tick_fallback() {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> = (0..23).map(|i| iso_plus_days("2026-03-03", i)).collect();
        let mut payload = native_tick_payload(dated_rows(dates.iter().map(String::as_str)));
        payload.width = 600.0;
        payload.resolved["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!("yearmonth");
        let svg = render(&payload, &test_fonts()).expect("renders");
        let row_y = x_tick_bottom(&svg);
        let anchors: Vec<(String, String)> = svg
            .split("<text")
            .skip(1)
            .filter(|t| t.contains("fill=\"#626366\""))
            .filter_map(|t| {
                let (head, body) = t.split_once('>')?;
                let attr = |k: &str| head.split(&format!("{k}=\"")).nth(1)?.split('"').next().map(str::to_string);
                (attr("y")?.parse::<f64>().ok()? > row_y).then(|| Some((attr("text-anchor")?, body.split('<').next()?.to_string())))?
            })
            .collect();
        assert_eq!(anchors.first(), Some(&("start".to_string(), "3".to_string())), "{anchors:?}");
        assert_eq!(anchors.last(), Some(&("end".to_string(), "25".to_string())), "{anchors:?}");
    }

    #[test]
    fn an_authored_label_align_keeps_the_end_point_labels() {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> = (0..40).map(|i| iso_plus_days("2026-03-03", i)).collect();
        let texts = |align: Option<&str>| {
            let mut payload = native_tick_payload(dated_rows(dates.iter().map(String::as_str)));
            payload.width = 300.0;
            if let Some(align) = align {
                payload.resolved["style"]["axis_x"]["labels"]["align"] = serde_json::json!(align);
            }
            x_label_positions(&render(&payload, &test_fonts()).expect("renders")).into_iter().map(|l| l.1).collect::<Vec<_>>()
        };
        let plain = texts(None);
        assert_eq!(plain.first().map(String::as_str), Some("3"), "{plain:?}");
        assert_eq!(texts(Some("center")), plain);
    }

    /// A month names a period, so an end-point label would only repeat the tick label of its own month.
    #[test]
    fn an_authored_label_align_adds_no_end_point_label_to_month_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> = (0..181).map(|i| iso_plus_days("2026-01-01", i)).collect();
        let mut payload = native_tick_payload(dated_rows(dates.iter().map(String::as_str)));
        payload.resolved["style"]["axis_x"]["labels"]["align"] = serde_json::json!("left");
        let texts: Vec<String> = x_label_positions(&render(&payload, &test_fonts()).expect("renders")).into_iter().map(|l| l.1).collect();
        assert_eq!(texts.iter().filter(|t| t.as_str() == "Jun").count(), 1, "{texts:?}");
    }

    /// Daily rows starting Jan 2 under month ticks that begin at Feb 1: "Jan" names a month no tick labels, so the first
    /// point keeps its label.
    #[test]
    fn an_authored_label_align_labels_a_first_month_no_tick_names() {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> = (0..181).map(|i| iso_plus_days("2026-01-02", i)).collect();
        let mut payload = native_tick_payload(dated_rows(dates.iter().map(String::as_str)));
        payload.resolved["style"]["axis_x"]["labels"]["align"] = serde_json::json!("left");
        let texts: Vec<String> = x_label_positions(&render(&payload, &test_fonts()).expect("renders")).into_iter().map(|l| l.1).collect();
        assert_eq!(texts.first().map(String::as_str), Some("Jan"), "{texts:?}");
    }

    /// Hourly rows under day labels draw a gridline at each midnight only, and each day's name sits at noon between
    /// them; forty days leave no room to name every day, so the axis keeps the clock's own ticks.
    #[test]
    fn day_labels_over_hourly_rows_grid_only_the_midnights() {
        let _locale = crate::time_locale::en_us_scope();
        let render_days = |days: i64| {
            let rows: Vec<String> = (0..days * 24)
                .map(|h| format!("{}T{:02}:00:00", iso_plus_days("2024-03-01", h / 24), h % 24))
                .collect();
            let mut payload = native_tick_payload(dated_rows(rows.iter().map(String::as_str)));
            payload.width = 600.0;
            payload.resolved["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!("yearmonthdate");
            render(&payload, &test_fonts()).expect("renders")
        };
        let svg = render_days(5);
        assert_eq!(x_grid_xs(&svg).len(), 5, "a gridline per midnight");
        let labels = x_label_positions(&svg);
        assert_eq!(labels.iter().map(|l| l.1.as_str()).collect::<Vec<_>>(), ["1", "Mar'24", "2", "3", "4", "5"], "{labels:?}");
        let (grid, noon) = (x_grid_xs(&svg), labels[2].0);
        assert!(noon > grid[1] && noon < grid[2], "day 2 sits between its midnights: {noon} {grid:?}");
        assert!(x_grid_xs(&render_days(40)).len() < 40, "forty days draw no midnight gridline under every day");
    }

    /// With no authored label unit, hourly rows whose ticks land on midnights name each day at noon, as an authored
    /// `yearmonthdate` does; forty days cannot name every day and keep the clock axis.
    #[test]
    fn hourly_rows_on_day_ticks_name_each_day_at_noon_without_an_authored_unit() {
        let _locale = crate::time_locale::en_us_scope();
        let render_days = |days: i64, unit: Option<&str>| {
            let rows: Vec<String> = (0..days * 24)
                .map(|h| format!("{}T{:02}:00:00", iso_plus_days("2024-03-01", h / 24), h % 24))
                .collect();
            let mut payload = native_tick_payload(dated_rows(rows.iter().map(String::as_str)));
            payload.width = 600.0;
            if let Some(unit) = unit {
                payload.resolved["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!(unit);
            }
            render(&payload, &test_fonts()).expect("renders")
        };
        let auto = render_days(5, None);
        assert_eq!(x_label_positions(&auto), x_label_positions(&render_days(5, Some("yearmonthdate"))));
        assert_eq!(x_grid_xs(&auto).len(), 5, "a gridline per midnight");
        let clock = render_days(40, None);
        assert_eq!(x_label_positions(&clock), x_label_positions(&render_days(40, Some("auto"))));
        assert!(x_grid_xs(&clock).len() < 40, "forty days draw no midnight gridline under every day");
    }

    /// An authored `labels.align` or `labels.angle` keeps hourly rows on their clock ticks: the automatic noon day labels
    /// leave authored placement alone.
    #[test]
    fn hourly_rows_with_authored_label_placement_keep_the_clock_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let rows: Vec<String> = (0..5 * 24)
            .map(|h| format!("{}T{:02}:00:00", iso_plus_days("2024-03-01", h / 24), h % 24))
            .collect();
        let render_with = |key: &str, value: serde_json::Value| {
            let mut payload = native_tick_payload(dated_rows(rows.iter().map(String::as_str)));
            payload.width = 600.0;
            payload.resolved["style"]["axis_x"]["labels"][key] = value;
            render(&payload, &test_fonts()).expect("renders")
        };
        for (key, value) in [("align", serde_json::json!("left")), ("angle", serde_json::json!(45))] {
            assert!(render_with(key, value).contains(">Midnight</text>"), "{key}");
        }
    }

    /// Labels on the first and last gridline reach the outer edge of their 1px stroke: half a pixel past the line's
    /// center, so a 1px sliver of the rule does not show beside the glyphs.
    #[test]
    fn end_labels_reach_the_outer_edge_of_the_gridline_under_them() {
        let _locale = crate::time_locale::en_us_scope();
        let dates: Vec<String> = (0..50).map(|i| iso_plus_days("2026-03-09", i)).collect();
        let mut payload = native_tick_payload(dated_rows(dates.iter().map(String::as_str)));
        payload.width = 600.0;
        let svg = render(&payload, &test_fonts()).expect("renders");
        let (grid, labels) = (x_grid_xs(&svg), x_label_positions(&svg));
        let (first_grid, last_grid) = (grid.iter().copied().fold(f64::MAX, f64::min), grid.iter().copied().fold(f64::MIN, f64::max));
        assert!((labels[0].0 - (first_grid - 0.5)).abs() < 1e-6, "{} vs {first_grid}", labels[0].0);
        assert!((labels.last().unwrap().0 - (last_grid + 0.5)).abs() < 1e-6, "{} vs {last_grid}", labels.last().unwrap().0);
    }

    /// The endpoint-rail arm's x ink is closed form over the rows, so a hole
    /// there refuses by name; a series with no hole keeps the rows' axis.
    #[test]
    fn a_native_hole_on_the_rail_arm_refuses_by_name() {
        let hole = (0..24).filter(|i| !matches!(i, 5 | 6));
        let dates = hole.map(|i| format!("{}-{:02}-01", 2024 + i / 12, i % 12 + 1));
        let mut json = rail_json(two_series_rows(dates));
        json["resolved"]["style"]["axis_x"]["ticks"]["count"] = serde_json::Value::Null;
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("rail cannot draw the empty bucket");
        assert!(err.contains("no row fills") && err.contains("endpoint-rail"), "{err}");
    }

    /// `labels.time_unit: none` paints Vega-Lite's own per-grain format on every
    /// interval tick, a bucket no row fills included.
    #[test]
    fn a_native_hole_under_labels_none_labels_the_empty_bucket() {
        let _locale = crate::time_locale::en_us_scope();
        let months = (1..=12u32).filter(|m| !matches!(m, 6 | 7)).map(|m| (2024, m));
        let mut payload = native_tick_payload(month_rows(months));
        payload.resolved["style"]["axis_x"]["labels"]["time_unit"] = serde_json::json!("none");
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(svg.contains(">Jun 2024<") && svg.contains(">Jul 2024<"), "{svg}");
    }

    /// Value labels sit on the data rows, never on the authored ticks: with
    /// more rows than `step: 3` ticks each label is still centered on its own
    /// vertex, and the fit measures them there.
    #[test]
    fn value_labels_stay_on_the_rows_under_authored_ticks() {
        let _locale = crate::time_locale::en_us_scope();
        let months = (0..24).map(|i| (2024 + i / 12, (i % 12 + 1) as u32));
        let mut json = make_line_payload(true, false);
        json["rows"] = month_rows(months);
        let axis = &mut json["resolved"]["style"]["axis_x"];
        axis["ticks"]["time_unit"] = serde_json::json!("yearmonth");
        axis["ticks"]["step"] = serde_json::json!(3.0);
        axis["ticks"]["count"] = serde_json::Value::Null;
        set_value_labels(&mut json, ",.0f");
        let payload: Payload = serde_json::from_value(json).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        let vertex_xs: Vec<f64> = svg[svg.find("d=\"M").unwrap() + 4..]
            .split('"')
            .next()
            .unwrap()
            .split(['M', 'L'])
            .filter(|p| !p.is_empty())
            .map(|p| p.split(',').next().unwrap().parse().unwrap())
            .collect();
        let label_xs: Vec<f64> = svg
            .split("<text")
            .skip(1)
            .filter(|t| t.contains("fill=\"#6b7280\""))
            .map(|t| {
                let i = t.find(" x=\"").unwrap() + 4;
                t[i..].split('"').next().unwrap().parse().unwrap()
            })
            .collect();
        assert_eq!(label_xs.len(), 24);
        assert_eq!(vertex_xs.len(), 24);
        for (l, v) in label_xs.iter().zip(&vertex_xs) {
            assert!((l - v).abs() < 0.01, "label at {l} not on its vertex {v}");
        }
    }

    /// A2 on the rail-concat arm — `area_review`'s own shape. The bare axis
    /// is Vega's, not the fit arm's, so both Vega arms draw it.
    #[test]
    fn the_bare_temporal_axis_reaches_the_rail_concat_arm() {
        let _locale = crate::time_locale::en_us_scope();
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = serde_json::Value::Array(
            (0..20)
                .flat_map(|i| {
                    let d = iso_plus_days("2024-01-07", i * 28);
                    [
                        json!({"week": d, "series": "Core", "signups": 100.0 + i as f64}),
                        json!({"week": d, "series": "Growth", "signups": 50.0 + i as f64}),
                    ]
                })
                .collect(),
        );
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        p["resolved"]["style"]["series_label"]["dark_companion_palette"] =
            json!(["#111111", "#222222"]);
        p["resolved"]["style"]["axis_x"]["labels"]["min_gap"] = json!(16.0);
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(!svg.contains("2024-01-07"), "no raw ISO date label survives");
        assert_eq!(x_tick_xs(&svg).len(), 17, "d3 utcMonth ticks on the rail arm too");
        assert_eq!(
            x_label_texts(&svg),
            vec!["February", "June", "October", "February", "June"],
            "same multi-scale labels, same two parity rounds, on the rail pane's own width"
        );
    }

    /// RL1's gate is the SCALE TYPE, not `detect_time_unit`'s verdict: a
    /// cadence it refuses (28-day steps) is still a CONTINUOUS time scale
    /// whose edge labels Vega-Lite flushes, and whose own label overhang
    /// the bare arm refuses outright — so pane 0 is bounded by the mark
    /// reach alone, exactly as on a detected monthly cadence. Keyed on
    /// `detected_data_tu.is_none()` alone, RL1 measured the raw ISO string
    /// as a centered last label and shrank `area_review`'s pane by ~33px.
    #[test]
    fn a_bare_temporal_rail_pane_is_not_bounded_by_a_centered_last_label() {
        let _locale = crate::time_locale::en_us_scope();
        use serde_json::json;
        let pane_w_for = |dates: Vec<String>| -> f64 {
            let mut p = make_multi_series_line_payload_with_bottom_legend();
            p["rows"] = serde_json::Value::Array(
                dates
                    .iter()
                    .enumerate()
                    .flat_map(|(i, d)| {
                        [
                            json!({"week": d, "series": "Core", "signups": 100.0 + i as f64}),
                            json!({"week": d, "series": "Growth", "signups": 50.0 + i as f64}),
                        ]
                    })
                    .collect(),
            );
            p["resolved"]["legend"]["visible"] = json!(false);
            p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
            p["resolved"]["style"]["series_label"]["dark_companion_palette"] =
                json!(["#111111", "#222222"]);
            p["resolved"]["style"]["axis_x"]["labels"]["min_gap"] = json!(16.0);
            p["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(true);
            let payload: Payload = serde_json::from_value(p).unwrap();
            rail_plot_w(&render(&payload, &test_fonts()).expect("renders"))
        };
        let bare = pane_w_for((0..20).map(|i| iso_plus_days("2024-01-07", i * 28)).collect());
        let monthly = pane_w_for(
            (0..20).map(|i| format!("{:04}-{:02}-01", 2024 + i / 12, 1 + i % 12)).collect(),
        );
        assert_eq!(
            bare, monthly,
            "a continuous time scale's pane 0 is the mark reach alone whether or not \
             detect_time_unit names its cadence"
        );
    }

    /// A bare axis's tilted labels reach `w * cos(angle)` right of their tick: one rotated-ink law for the fit and
    /// the rail arm.
    #[test]
    fn bare_label_ink_is_the_rotated_box_the_parity_pass_reads() {
        use super::bare_label_ink;
        use crate::families::temporal::BareTick;
        let tick = |frac| BareTick { day: 0.0, frac, text: String::new(), row2: None, mark: crate::families::temporal::BareMark::Both };
        let ticks = [tick(0.1), tick(0.2)];
        let (ink, visible) = bare_label_ink(&ticks, &[90.0, 90.0], 45.0, true, 0.0, 11.0, |f| 100.0 * f, 100.0, 0.0, crate::common::XEdge::Bottom);
        let reach = 90.0 * std::f64::consts::FRAC_1_SQRT_2;
        assert!((ink[0].x2 - (10.0 + reach)).abs() < 1e-9, "{:?}", ink[0]);
        assert_eq!(visible.len(), 2);
    }

    /// A last visible label reaching past the rail arm's pane widens pane 0's right ink, measured over the pane
    /// at the declared width; the chart still draws.
    #[test]
    fn a_bare_temporal_label_overhanging_the_rail_pane_renders() {
        let _locale = crate::time_locale::en_us_scope();
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["width"] = json!(300.0);
        p["rows"] = serde_json::Value::Array(
            (0..8)
                .flat_map(|i| {
                    let d = iso_plus_days("2024-06-21", i * 28);
                    [
                        json!({"week": d, "series": "Core", "signups": 100.0 + i as f64}),
                        json!({"week": d, "series": "Growth", "signups": 50.0 + i as f64}),
                    ]
                })
                .collect(),
        );
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        p["resolved"]["style"]["series_label"]["dark_companion_palette"] =
            json!(["#111111", "#222222"]);
        let payload: Payload = serde_json::from_value(p).unwrap();
        render(&payload, &test_fonts()).expect("renders");
    }

    /// A five-bucket fixture covering all three gap shapes at once:
    /// `aa` carries a NULL-measure row mid-series, `bb` is simply MISSING a
    /// row for that bucket, and `cc` alternates value/null so every run is
    /// a single point.
    fn make_gappy_payload() -> serde_json::Value {
        use serde_json::json;
        let weeks = ["2026-01-01", "2026-01-08", "2026-01-15", "2026-01-22", "2026-01-29"];
        let mut rows = Vec::new();
        for (i, w) in weeks.iter().enumerate() {
            rows.push(json!({"week": w, "series": "aa", "signups": if i == 2 { json!(null) } else { json!(300.0) }}));
            if i != 2 {
                rows.push(json!({"week": w, "series": "bb", "signups": 200.0}));
            }
            rows.push(json!({"week": w, "series": "cc", "signups": if i % 2 == 1 { json!(null) } else { json!(100.0) }}));
        }
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = serde_json::Value::Array(rows);
        p["resolved"]["palette"] = json!(["#aa0000", "#00bb00", "#0000cc"]);
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["area_mark"] = json!({"opacity": 0.16, "backdrop": false});
        p
    }

    /// The `d` of the path carrying `marker` — the nearest `d="…"` before it.
    fn path_d(svg: &str, marker: &str) -> String {
        let i = svg.find(marker).unwrap_or_else(|| panic!("no path painted {marker}"));
        let head = &svg[..i];
        let d = head.rfind("d=\"").expect("the path carries a d");
        svg[d + 3..].split('"').next().unwrap().to_string()
    }

    /// Point count of each `M…` subpath (one per defined run).
    fn subpath_lens(d: &str) -> Vec<usize> {
        d.split('M').skip(1).map(|run| run.matches('L').count() + 1).collect()
    }

    /// Vega-Lite gives every line/area mark the default `defined` predicate
    /// `isValid(x) && isFinite(+x) && isValid(y) && isFinite(+y)` (read off
    /// `area_won_stacked`'s compiled Vega), so d3-shape ends the current
    /// subpath at a datum whose measure is null and starts a new one after
    /// it — a run of ONE defined point is its own closed `M x,y Z`, which
    /// under the mark's round cap paints a dot. A bucket with no ROW AT
    /// ALL is a different thing: that datum never reaches d3, so the
    /// mark connects straight across it. (Both rules checked against the
    /// reference: `Download`'s runs come out `5, 8, 5, 8, 2` under
    /// break-at-null-only and `1, 1, 3, 7, 1, 5, 8, 1, 1` if a missing row
    /// broke the run too.)
    #[test]
    fn a_null_measure_breaks_the_line_but_a_missing_row_does_not() {
        let payload: Payload = serde_json::from_value(make_gappy_payload()).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert_eq!(
            subpath_lens(&path_d(&svg, "stroke=\"#aa0000\" stroke-width=\"4\"")),
            vec![2, 2],
            "a null-measure row splits the series in two"
        );
        assert_eq!(
            subpath_lens(&path_d(&svg, "stroke=\"#00bb00\" stroke-width=\"4\"")),
            vec![4],
            "a bucket with no row at all is not a break — the mark connects across it"
        );
        let cc = path_d(&svg, "stroke=\"#0000cc\" stroke-width=\"4\"");
        assert_eq!(subpath_lens(&cc), vec![1, 1, 1], "three one-point runs: {cc}");
        // d3's `curveLinear.lineEnd` closes a run of exactly one point
        // (`_point === 1`), which under the mark's round cap is what paints
        // the dot — Vega's own SVG for three isolated points is
        // `M40,133.333ZM200,133.333ZM360,133.333Z`. A longer run is left
        // open.
        assert_eq!(cc.matches('Z').count(), 3, "every one-point line run closes: {cc}");
        assert_eq!(
            path_d(&svg, "stroke=\"#aa0000\" stroke-width=\"4\"").matches('Z').count(),
            0,
            "a run of two or more points stays open"
        );
    }

    /// The area generator ends each run with `Z` before the next `M`, and a
    /// single-point run closes as `M x,top L x,bottom Z` — pinned against
    /// the reference SVG, whose short runs read exactly
    /// `M833.043,244.55L833.043,335Z`.
    #[test]
    fn a_null_measure_breaks_the_area_into_closed_subpaths() {
        let _en = crate::time_locale::en_us_scope();
        let payload: Payload = serde_json::from_value(make_gappy_payload()).unwrap();
        let svg = crate::families::area::render(&payload, &test_fonts()).expect("renders");
        let aa = path_d(&svg, "fill=\"#aa0000\" fill-opacity");
        assert_eq!(aa.matches('Z').count(), 2, "one closed subpath per defined run: {aa}");
        assert!(aa.contains("ZM"), "the runs are one path, not two marks: {aa}");
        assert_eq!(
            path_d(&svg, "fill=\"#00bb00\" fill-opacity").matches('Z').count(),
            1,
            "a missing row leaves one run"
        );
        let cc = path_d(&svg, "fill=\"#0000cc\" fill-opacity");
        assert_eq!(subpath_lens(&cc), vec![2, 2, 2], "single-point runs close top→bottom: {cc}");
    }

    /// Z1: production's `_insert_rule` (`baseline.py:37-82`) inserts an AREA
    /// chart's `datum: 0` rule right after the last `area` sub-layer —
    /// above the colored fills, below the foreground stroke — where a LINE
    /// chart's rule is prepended under everything. Reference `grid_area`:
    /// the colored `layer_2` areas, then the zero rule, then the `layer_4`
    /// line strokes.
    #[test]
    fn an_area_chart_paints_its_zero_rule_over_the_fills_and_under_the_line() {
        let _en = crate::time_locale::en_us_scope();
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["style"]["axis_y"]["grid"] =
            json!({"visible": true, "threshold": {"color": "#8A8C8F", "width": 2.0}, "width": 1.0, "color": "#D0D3D9", "opacity": 1.0});
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = crate::families::area::render(&payload, &test_fonts()).expect("renders");

        let area_i = svg.find("fill-opacity=\"0.16\"").expect("a colored area is drawn");
        let rule_i = svg.find("stroke-width=\"2\"").expect("the zero rule is drawn");
        let line_i = svg.find("stroke-width=\"4\" stroke-linecap").expect("a main line stroke is drawn");
        assert!(
            area_i < rule_i && rule_i < line_i,
            "the zero rule paints between the colored area fills and the main line strokes: \
             area@{area_i} rule@{rule_i} line@{line_i}: {svg}"
        );
    }

    /// Z1's other half: a LINE chart's zero rule stays prepended under
    /// everything (`baseline.py`'s line branch is unchanged), so it paints
    /// before the halo AND the main stroke.
    #[test]
    fn a_line_chart_still_paints_its_zero_rule_under_everything() {
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["style"]["axis_y"]["grid"] =
            json!({"visible": true, "threshold": {"color": "#8A8C8F", "width": 2.0}, "width": 1.0, "color": "#D0D3D9", "opacity": 1.0});
        let payload: Payload = serde_json::from_value(p).unwrap();
        let svg = render(&payload, &test_fonts()).expect("renders");

        let rule_i = svg.find("stroke-width=\"2\"").expect("the zero rule is drawn");
        let halo_i = svg.find("stroke-width=\"6\"").expect("the halo stroke is drawn");
        let line_i = svg.find("stroke-width=\"4\" stroke-linecap").expect("a main line stroke is drawn");
        assert!(
            rule_i < halo_i && halo_i < line_i,
            "the zero rule stays under the halo and the main stroke on a line chart: \
             rule@{rule_i} halo@{halo_i} line@{line_i}: {svg}"
        );
    }

    /// A minimal single-series right-axis payload shaped like the two
    /// swept log rows (`axis-scales_0/world_population_log`,
    /// `line-graphs_6/revenue_log`), with x values below 1700 so they stay
    /// nominal rather than being read as years: a log-typed `axis_y.scale.continuous`
    /// with an explicit `labels.format` (so d3's own minor-mantissa
    /// `tickFormat` blanking never fires — every rung gets real text) and
    /// `ticks.visible: false` (so no tick-mark line shares the grid's
    /// stroke color, keeping the grid-line count countable).
    fn make_log_line_payload() -> serde_json::Value {
        let mut p = make_line_payload(true, false);
        p["rows"] = serde_json::json!([
            {"week": "1000", "signups": 990000000.0},
            {"week": "1200", "signups": 1650000000.0},
            {"week": "1500", "signups": 2536000000.0},
            {"week": "1650", "signups": 6127000000.0},
            {"week": "1699", "signups": 7795000000.0},
        ]);
        let ay = &mut p["resolved"]["style"]["axis_y"];
        ay["scale"] = serde_json::json!({"continuous": {"type": "log"}});
        ay["labels"]["format"] = serde_json::json!(".3~s");
        ay["labels"]["min_gap"] = serde_json::json!(16.0);
        ay["grid"]["visible"] = serde_json::json!(true);
        ay["ticks"]["visible"] = serde_json::json!(false);
        // `tick_label` (a distinct override field ahead of `labels.format`
        // in `format_y_label`'s own precedence) is unset on both real
        // corpus rows — clear the fixture's default so `.3~s` actually
        // governs, as it does in production.
        ay["tick_label"] = serde_json::Value::Null;
        p
    }

    /// d3's log `nice` floors and ceils to powers of the authored base, and its
    /// integer-base ticks are the powers themselves when a decade's multiples
    /// are only `1`.
    #[test]
    fn a_base_two_log_axis_draws_powers_of_two() {
        let mut p = make_log_line_payload();
        p["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"type": "log", "log": {"base": 2.0}}});
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        for rung in [">537M<", ">1.07G<", ">2.15G<", ">4.29G<", ">8.59G<"] {
            assert!(svg.contains(rung), "{rung} missing: {svg}");
        }
        assert!(!svg.contains(">1G<"), "a base-ten rung on a base-two axis: {svg}");
    }

    /// An authored pair reaches Vega verbatim: no `nice` rounds it out to powers.
    #[test]
    fn an_authored_log_domain_is_drawn_verbatim() {
        let mut p = make_log_line_payload();
        p["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"type": "log", "domain": [500_000_000.0, 10_000_000_000.0]}});
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains(">500M<"), "the authored floor is a rung: {svg}");
        assert!(!svg.contains(">100M<") && !svg.contains(">400M<"), "a rung below the authored floor: {svg}");
    }

    /// A bare 4-digit-year x domain (`"1000"`, `"1200"`, `"1500"`, `"1650"`,
    /// `"1699"`) is production's own NOMINAL/point scale, evenly spaced —
    /// its `aria-label` reads "X-axis for a discrete scale", never
    /// temporal — even though every value individually PARSES as a year
    /// via `parse_iso_date`. `detect_time_unit` must never even run on it:
    /// production's `infer_vega_type_from_data` classifies the raw string
    /// SHAPE nominal first (no date-like structure at all), and only a
    /// nominal/ordinal/temporal verdict there lets `detect_time_unit`
    /// execute. Vertices stay evenly spaced, not weighted by the irregular
    /// 200/300/150/49-year gaps a (wrongly) detected yearly cadence would
    /// calendar-space them by. The values sit below `[1700, 2100]`, the
    /// range `is_year_shaped` rewrites to temporal.
    #[test]
    fn bare_year_x_values_stay_on_the_nominal_point_scale_not_a_detected_yearly_cadence() {
        let p = make_log_line_payload();
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("renders");
        let path = svg
            .split("<path d=\"M")
            .nth(1)
            .expect("line path present")
            .split('"')
            .next()
            .unwrap();
        let xs: Vec<f64> = path
            .split(['M', 'L'])
            .filter(|s| !s.is_empty())
            .map(|pt| pt.split(',').next().unwrap().parse().unwrap())
            .collect();
        assert_eq!(xs.len(), 5, "{path}");
        let step = xs[1] - xs[0];
        for w in xs.windows(2) {
            assert!(
                (w[1] - w[0] - step).abs() < 1e-6,
                "point scale: every gap equals the first ({step}): {xs:?}"
            );
        }
    }

    /// A bare-year x domain INSIDE `[1700, 2100]` is the opposite case from
    /// the test above: production's `normalize_labeled_temporal` rewrites
    /// each bare year to `YYYY-01-01` before `infer_vega_type_from_data`
    /// ever runs, so the column resolves temporal with a yearly cadence and
    /// calendar-spaced positions — 2020 is a leap year, so the 2020→2021
    /// gap (366 days) is wider than the other three (365 days each), unlike
    /// the even nominal spacing `bare_year_x_values_stay_on_the_nominal_
    /// point_scale_not_a_detected_yearly_cadence` pins for an out-of-range
    /// domain.
    #[test]
    fn year_shaped_x_values_inside_1700_2100_calendar_space_with_a_widened_leap_year_gap() {
        let mut p = make_line_payload(true, false);
        p["rows"] = serde_json::json!([
            {"week": "2019", "signups": 100.0},
            {"week": "2020", "signups": 150.0},
            {"week": "2021", "signups": 130.0},
            {"week": "2022", "signups": 170.0},
            {"week": "2023", "signups": 160.0},
        ]);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("renders");
        let path = svg
            .split("<path d=\"M")
            .nth(1)
            .expect("line path present")
            .split('"')
            .next()
            .unwrap();
        let xs: Vec<f64> = path
            .split(['M', 'L'])
            .filter(|s| !s.is_empty())
            .map(|pt| pt.split(',').next().unwrap().parse().unwrap())
            .collect();
        assert_eq!(xs.len(), 5, "{path}");
        let gaps: Vec<f64> = xs.windows(2).map(|w| w[1] - w[0]).collect();
        let leap_gap = gaps[1]; // 2020 -> 2021
        for (i, &g) in gaps.iter().enumerate() {
            if i == 1 {
                continue;
            }
            assert!(
                leap_gap - g > 0.1,
                "the 2020->2021 gap ({leap_gap}) must be wider than gap {i} ({g}): {gaps:?}"
            );
        }
    }

    fn year_axis_payload(years: &[&str], width: f64) -> serde_json::Value {
        let mut p = make_line_payload(true, false);
        p["rows"] = serde_json::Value::Array(
            years.iter().map(|y| serde_json::json!({"week": y, "signups": 100.0})).collect(),
        );
        p["width"] = serde_json::json!(width);
        let labels = &mut p["resolved"]["style"]["axis_x"]["labels"];
        labels["overlap"] = serde_json::json!({"skip": true, "tilt": true});
        labels["tilt_increments"] = serde_json::json!([0.0, -30.0, -45.0, -60.0, -90.0]);
        p
    }

    fn month_end_rows() -> serde_json::Value {
        let dates = ["2025-08-03", "2025-08-31", "2025-09-30", "2025-10-31", "2025-11-30", "2025-12-31", "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30", "2026-07-31", "2026-08-31"];
        serde_json::Value::Array(dates.iter().enumerate().map(|(i, d)| serde_json::json!({"week": d, "signups": 100.0 + i as f64})).collect())
    }

    /// A grain dropped for the scaffold budget (a billing start beside month ends) labels the continuous axis
    /// by the curated two-tier ladder, a tick per label, never d3's full month names.
    #[test]
    fn a_dropped_grain_line_axis_labels_by_the_ladder_not_full_month_names() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_line_payload(true, false);
        p["rows"] = month_end_rows();
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        for name in ["August", "September", "October", "December"] {
            assert!(!svg.contains(&format!(">{name}<")), "{name}: {svg}");
        }
        for label in ["Sep", "Jan", "2025", "2026"] {
            assert!(svg.contains(&format!(">{label}<")), "{label}: {svg}");
        }
        assert_eq!(x_tick_xs(&svg).len(), 12, "a tick per month label: {svg}");
    }

    /// An authored angle or continuous domain names its own axis: the dropped-grain ladder stands down. An
    /// unauthored domain (the null placeholder a touched `scale.continuous` carries) does not.
    #[test]
    fn an_authored_angle_or_domain_opts_out_of_the_dropped_grain_ladder() {
        let _locale = crate::time_locale::en_us_scope();
        let render_with = |edit: &dyn Fn(&mut serde_json::Value)| {
            let mut p = make_line_payload(true, false);
            p["rows"] = month_end_rows();
            edit(&mut p["resolved"]["style"]["axis_x"]);
            render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders")
        };
        let angled = render_with(&|ax| ax["labels"]["angle"] = serde_json::json!(-45.0));
        assert!(angled.contains(">September<"), "d3's own labels under an authored angle: {angled}");
        let windowed = render_with(&|ax| ax["scale"] = serde_json::json!({"continuous": {"domain": ["2025-09-01", "2026-03-01"]}}));
        assert!(windowed.contains(">October<") && !windowed.contains(">Oct<"), "d3's own labels over an authored window: {windowed}");
        let placeholder = render_with(&|ax| ax["scale"] = serde_json::json!({"continuous": {"zero": null, "type": null, "domain": null}}));
        assert!(placeholder.contains(">Sep<") && !placeholder.contains(">September<"), "{placeholder}");
    }

    /// The rail-concat arm reserves the ladder's two label rows: the pane ends a row higher and the year-context row clears the x title.
    #[test]
    fn a_dropped_grain_ladder_on_the_rail_arm_reserves_its_two_rows() {
        let _locale = crate::time_locale::en_us_scope();
        use serde_json::json;
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["rows"] = serde_json::Value::Array(
            month_end_rows().as_array().unwrap().iter().flat_map(|r| {
                [json!({"week": r["week"], "series": "Core", "signups": r["signups"]}), json!({"week": r["week"], "series": "Growth", "signups": r["signups"]})]
            }).collect(),
        );
        p["resolved"]["legend"]["visible"] = json!(false);
        p["resolved"]["style"]["endpoint_labels"]["visible"] = json!(true);
        p["resolved"]["style"]["series_label"]["dark_companion_palette"] = json!(["#111111", "#222222"]);
        p["resolved"]["style"]["axis_x"]["title"]["visible"] = json!(true);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let ys = |needle: &str| -> f64 {
            let at = svg.find(&format!(">{needle}</text>")).unwrap_or_else(|| panic!("{needle}: {svg}"));
            let head = &svg[..at];
            let y = &head[head.rfind(" y=\"").unwrap() + 4..];
            y[..y.find('"').unwrap()].parse().unwrap()
        };
        let (year_row, title, month_row) = (ys("2026"), ys("Week"), ys("Sep"));
        assert!(title >= year_row + 11.0, "the title ({title}) clears the year row ({year_row}): {svg}");
        // The band holds two rows, so the pane ends a row above where one reserved row would end it (339.5).
        assert!(month_row <= 330.0, "month row at {month_row}: {svg}");
    }

    /// A year-shaped x too sparse for a yearly cadence (`1800`..`2025`, 225
    /// years for five points) keeps no grain, but the year ladder still climbs
    /// round steps (5, 10, 25, 50, 100) before it ever tilts: the labels
    /// stay horizontal, one tick per label, at any width.
    #[test]
    fn a_long_year_span_steps_round_and_stays_horizontal() {
        let _locale = crate::time_locale::en_us_scope();
        for width in [360.0, 640.0, 1000.0] {
            let p = year_axis_payload(&["1800", "1900", "1950", "2000", "2025"], width);
            let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
            assert!(!svg.contains("rotate("), "{width}: {svg}");
            let years: Vec<i32> = x_label_texts(&svg).iter().map(|t| t.parse().unwrap()).collect();
            assert!(years.len() >= 2 && years.len() <= 12, "{width}: {years:?}");
            let step = years[1] - years[0];
            assert!([5, 10, 25, 50, 100].contains(&step), "{width}: {years:?}");
            assert!(years.windows(2).all(|w| w[1] - w[0] == step), "{width}: {years:?}");
            assert_eq!(x_tick_xs(&svg).len(), years.len(), "{width}: a tick per label: {svg}");
        }
    }

    /// A thirty-year span is dense but short: it still steps 5 or 10, never the coarse steps.
    #[test]
    fn a_thirty_year_span_still_steps_five_or_ten() {
        let _locale = crate::time_locale::en_us_scope();
        let years: Vec<String> = (1990..=2020).map(|y| format!("{y}")).collect();
        let refs: Vec<&str> = years.iter().map(String::as_str).collect();
        let p = year_axis_payload(&refs, 300.0);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        let labels: Vec<i32> = x_label_texts(&svg).iter().map(|t| t.parse().unwrap()).collect();
        assert!(labels.len() >= 3, "{labels:?}");
        let step = labels[1] - labels[0];
        assert!((step == 5 || step == 10) && labels.windows(2).all(|w| w[1] - w[0] == step), "{labels:?}");
    }

    /// The tooltip header detects the yearly grain even where the axis drops it
    /// for being over the scaffold budget, so a year-shaped x announces "1800",
    /// never the ISO date `normalize_labeled_temporal` rewrote it to.
    #[test]
    fn a_sparse_year_shaped_x_announces_the_year_in_the_tooltip_header() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_line_payload(true, false);
        p["rows"] = serde_json::json!([
            {"week": "1800", "signups": 100.0},
            {"week": "1900", "signups": 150.0},
            {"week": "1950", "signups": 130.0},
            {"week": "2000", "signups": 170.0},
            {"week": "2025", "signups": 160.0},
        ]);
        p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("renders");
        assert!(svg.contains("1800; Signups: "), "{svg}");
        assert!(!svg.contains("1800-01-01"), "{svg}");
    }

    /// A bare axis whose bucketed layout still collides once tilt is spent
    /// (sparse days inside one year stop the cadence ladder at month) hands the
    /// thinning back to Vega (`_defer_thinning_when_uncertain`), so colliding
    /// labels are parity-thinned rather than all kept.
    #[test]
    fn a_colliding_bare_axis_with_no_tilt_left_is_still_parity_thinned() {
        let _locale = crate::time_locale::en_us_scope();
        let mut p = make_line_payload(true, false);
        let rows: Vec<serde_json::Value> = [1, 3, 6, 12]
            .iter()
            .flat_map(|m| (1..=5).map(move |d| format!("2024-{m:02}-{d:02}")))
            .enumerate()
            .map(|(i, week)| serde_json::json!({"week": week, "signups": 100.0 + (i * i % 17) as f64}))
            .collect();
        p["rows"] = serde_json::Value::Array(rows);
        p["width"] = serde_json::json!(240.0);
        let labels = &mut p["resolved"]["style"]["axis_x"]["labels"];
        labels["overlap"] = serde_json::json!({"skip": true, "tilt": false});
        labels["min_gap"] = serde_json::json!(16.0);
        labels["font"]["size"] = serde_json::json!(40.0);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("renders");
        let (ticks, texts) = (x_tick_xs(&svg).len(), x_label_texts(&svg).len());
        assert_eq!(ticks, 4, "{svg}");
        assert!(texts < ticks, "{texts} labels over {ticks} ticks: {svg}");
    }

    /// A warehouse DECIMAL/NUMERIC year column (BigQuery NUMERIC, Snowflake
    /// NUMBER) round-trips through JSON as a float (`2019.0`, not the bare
    /// integer `2019`) — `common::is_year_shaped`'s own Decimal arm already
    /// tolerates this (`utils.py:284-313`'s integral-Decimal branch), so the
    /// anchor derivation must too, not just the plain-integer JSON case the
    /// leap-year-gap test above pins.
    #[test]
    fn year_shaped_x_values_as_integral_floats_calendar_space_with_a_widened_leap_year_gap() {
        let mut p = make_line_payload(true, false);
        p["rows"] = serde_json::json!([
            {"week": 2019.0, "signups": 100.0},
            {"week": 2020.0, "signups": 150.0},
            {"week": 2021.0, "signups": 130.0},
            {"week": 2022.0, "signups": 170.0},
            {"week": 2023.0, "signups": 160.0},
        ]);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("renders");
        let path = svg
            .split("<path d=\"M")
            .nth(1)
            .expect("line path present")
            .split('"')
            .next()
            .unwrap();
        let xs: Vec<f64> = path
            .split(['M', 'L'])
            .filter(|s| !s.is_empty())
            .map(|pt| pt.split(',').next().unwrap().parse().unwrap())
            .collect();
        assert_eq!(xs.len(), 5, "{path}");
        let gaps: Vec<f64> = xs.windows(2).map(|w| w[1] - w[0]).collect();
        let leap_gap = gaps[1]; // 2020 -> 2021
        for (i, &g) in gaps.iter().enumerate() {
            if i == 1 {
                continue;
            }
            assert!(
                leap_gap - g > 0.1,
                "the 2020->2021 gap ({leap_gap}) must be wider than gap {i} ({g}): {gaps:?}"
            );
        }
    }

    /// A null x value in an otherwise year-shaped column is no bucket: it
    /// takes no band, yet its y still spans the value axis.
    #[test]
    fn a_null_value_in_a_year_shaped_x_column_takes_no_band() {
        let mut p = make_line_payload(true, false);
        p["rows"] = serde_json::json!([
            {"week": "2019", "signups": 100.0},
            {"week": null, "signups": 1500.0},
            {"week": "2021", "signups": 130.0},
        ]);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert_eq!(x_label_texts(&svg), ["2019", "2020", "2021"], "the gap year fills, the null row takes no band");
        assert!(svg.contains(">1,400<"), "the null row's y reaches the axis: {svg}");
    }

    /// A year-shaped x is a yearly bucketed grain: `curve: step` bands it, one band per year.
    #[test]
    fn curve_step_on_a_year_shaped_x_bands_one_per_year() {
        let mut p = make_line_payload(true, false);
        p["rows"] = serde_json::json!([
            {"week": "2019", "signups": 100.0},
            {"week": "2020", "signups": 150.0},
            {"week": "2021", "signups": 130.0},
        ]);
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        p["resolved"]["style"]["line_mark"]["curve"] = serde_json::json!("step");
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
        assert_eq!(distinct_xs(&main_line_d(&svg)).len(), 4, "three bands have four edges: {svg}");
        for year in ["2019", "2020", "2021"] {
            assert!(svg.contains(&format!(">{year}<")), "{year} labels its band: {svg}");
        }
    }

    /// Four-weekly ISO dates: no calendar grain fits their 28-day cadence, so
    /// `detect_time_unit` finds none (`_temporal_layout`'s empty label layout)
    /// and production resolves the x `temporal` from the data alone.
    const UNGRIDDED_ISO_DATES: [&str; 5] =
        ["2024-01-07", "2024-02-04", "2024-03-03", "2024-03-31", "2024-04-28"];

    /// A single-series `curve` payload over `xs` with the x resolved
    /// non-quantitative, the shape production emits for a date or a string.
    fn plain_x_curve_payload(
        xs: &[&str],
        curve: &str,
        authored_type: Option<&str>,
    ) -> serde_json::Value {
        let mut p = make_line_payload(false, false);
        let signups = [100.0, 150.0, 90.0, 170.0, 130.0];
        let rows: Vec<serde_json::Value> = (0..xs.len())
            .map(|i| serde_json::json!({"week": xs[i], "signups": signups[i % 5]}))
            .collect();
        p["rows"] = serde_json::json!(rows);
        p["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(false);
        p["resolved"]["style"]["line_mark"]["curve"] = serde_json::json!(curve);
        p["resolved"]["style"]["area_mark"] =
            serde_json::json!({"opacity": 0.16, "backdrop": false, "curve": curve});
        if let Some(t) = authored_type {
            p["resolved"]["style"]["axis_x"]["type"] = serde_json::json!(t);
        }
        p
    }

    /// Vega-Lite's `interpolate: step` on a continuous x is d3's `curveStep`:
    /// each riser stands at the MIDPOINT of its two datums' x positions and the
    /// path ends on the last datum — `2n` points, never the band-edge doubling.
    /// The datum positions are the same chart's `linear` vertices.
    fn assert_plain_step_over_linear_vertices(payload: serde_json::Value, areas: bool) {
        let curve_d = |curve: &str| {
            let mut p = payload.clone();
            p["resolved"]["style"]["line_mark"]["curve"] = serde_json::json!(curve);
            p["resolved"]["style"]["area_mark"]["curve"] = serde_json::json!(curve);
            let payload: Payload = serde_json::from_value(p).unwrap();
            main_line_d(&render_inner(&payload, &test_fonts(), areas).expect("renders"))
        };
        let datums = path_vertices(&curve_d("linear"));
        let step = path_vertices(&curve_d("step"));
        assert_eq!(step.len(), 2 * datums.len(), "d3 step is 2n points: {step:?}");
        for (i, pair) in datums.windows(2).enumerate() {
            let mid = (pair[0].0 + pair[1].0) / 2.0;
            let (before, after) = (step[1 + 2 * i], step[2 + 2 * i]);
            assert!((before.0 - mid).abs() < 1e-3 && (after.0 - mid).abs() < 1e-3, "{step:?}");
            assert!((before.1 - pair[0].1).abs() < 1e-3 && (after.1 - pair[1].1).abs() < 1e-3);
        }
    }

    /// An ISO-date x no calendar grain explains resolves `temporal` from the
    /// data (`type_inference.py:306`), so `step_band.is_band_step` is false
    /// (`step_band.py:77`) and production draws the plain step on a time scale.
    #[test]
    fn curve_step_on_an_ungridded_iso_date_x_draws_the_plain_step_not_a_band_step() {
        let _locale = crate::time_locale::en_us_scope();
        for areas in [false, true] {
            let p = plain_x_curve_payload(&UNGRIDDED_ISO_DATES, "step", None);
            assert_plain_step_over_linear_vertices(p, areas);
        }
    }

    /// An authored `axis_x.type: temporal` resolves `temporal`
    /// (`type_inference.py:285-289`) before the band-step flip, so the same
    /// plain step draws.
    #[test]
    fn curve_step_on_an_authored_temporal_iso_date_x_draws_the_plain_step() {
        let _locale = crate::time_locale::en_us_scope();
        for areas in [false, true] {
            let p = plain_x_curve_payload(&UNGRIDDED_ISO_DATES, "step", Some("temporal"));
            assert_plain_step_over_linear_vertices(p, areas);
        }
    }

    /// An authored `axis_x.type: ordinal` resolves the x ordinal ahead of any
    /// date reading (`type_inference.py:290-291`), so `step_band.is_band_step`
    /// is true and `curve: step` bands even over standard ISO dates a bare
    /// column would draw as the plain step: every riser stands on a band edge,
    /// a whole fraction of the plot width, not at a date midpoint.
    #[test]
    fn curve_step_on_an_authored_ordinal_iso_date_x_keeps_the_band_step() {
        for areas in [false, true] {
            let p = plain_x_curve_payload(&UNGRIDDED_ISO_DATES, "step", Some("ordinal"));
            let payload: Payload = serde_json::from_value(p).unwrap();
            let svg = render_inner(&payload, &test_fonts(), areas).expect("renders");
            let xs = distinct_xs(&main_line_d(&svg));
            let right = *xs.last().unwrap();
            assert_eq!(xs.len(), 6, "five bands have six edges: {xs:?}");
            for x in &xs {
                let bands = x / right * 5.0;
                assert!((bands - bands.round()).abs() < 1e-3, "off a band edge: {xs:?}");
            }
        }
    }

    /// Strings no calendar parses under an authored `type: temporal` have no instant to place a
    /// mark at: the chart refuses by name.
    #[test]
    fn curve_step_on_an_authored_temporal_x_of_non_dates_is_refused_by_name() {
        for areas in [false, true] {
            let p = plain_x_curve_payload(&["Feb", "Mar", "Jan"], "step", Some("temporal"));
            let err = render_inner(&serde_json::from_value(p).unwrap(), &test_fonts(), areas)
                .expect_err("an authored-temporal x of non-dates under curve=step must raise");
            assert!(err.to_string().contains("ERR-X-VALUE-NOT-A-DATE"), "{err}");
        }
    }

    /// A labeled bucket (`2024-Q1`) anchors to an ISO date in production, detects
    /// `yearquarter` and flips the x ordinal onto the bucketed-calendar band axis: one
    /// band per quarter, the first labeled with its year as a second row.
    #[test]
    fn curve_step_on_a_labeled_bucket_x_bands_one_per_quarter() {
        let p = plain_x_curve_payload(&["2024-Q1", "2024-Q2", "2024-Q3"], "step", None);
        let svg = render(&serde_json::from_value(p).unwrap(), &test_fonts()).expect("renders");
        assert_eq!(distinct_xs(&main_line_d(&svg)).len(), 4, "three bands have four edges: {svg}");
        for label in ["Q1", "2024", "Q2", "Q3"] {
            assert!(svg.contains(&format!(">{label}<")), "{label}: {svg}");
        }
    }

    /// A year-shaped x now resolves `detected_data_tu.is_some()`, which
    /// means `axis_x.labels.align` is now ADMITTED where it previously
    /// refused with "only modeled on a temporal x axis" — production's
    /// own `axis_style_to_vl` (`vl_field_maps.py:180-181`) passes
    /// `labels.align` straight through to VL's `labelAlign` unconditional
    /// on axis type, so a year-shaped x (temporal in production via
    /// `normalize_labeled_temporal`) honors it exactly like any other
    /// temporal x — a correctness fix, not a new gap. No corpus row
    /// reaches this combination.
    #[test]
    fn labels_align_on_a_year_shaped_x_is_now_admitted_not_refused() {
        let mut p = make_line_payload(true, false);
        p["rows"] = serde_json::json!([
            {"week": "2019", "signups": 100.0},
            {"week": "2020", "signups": 150.0},
            {"week": "2021", "signups": 130.0},
        ]);
        p["resolved"]["style"]["axis_x"]["labels"]["align"] = serde_json::json!("left");
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("axis_x.labels.align on a year-shaped x renders, matching production");
        assert!(svg.contains("<svg"));
    }

    /// A left-axis (legacy-arm) line with a year-shaped x built from
    /// integral floats (a warehouse Decimal year) used to render with NO
    /// x-axis labels at all: `resolve_temporal_labels` still read the raw
    /// `x_domain` (`"2019.0"`, not `parse_iso_date`-parseable), silently
    /// returning an empty label set. Fixed by threading `x_domain_for_
    /// temporal` through it, the same seam `x_time_ext`/`resolve_
    /// vega_x_labels` already use.
    #[test]
    fn fit_arm_temporal_labels_render_for_integral_float_years() {
        let mut p = make_line_payload(false, false); // left y axis => legacy arm
        p["rows"] = serde_json::json!([
            {"week": 2019.0, "signups": 100.0},
            {"week": 2020.0, "signups": 150.0},
            {"week": 2021.0, "signups": 130.0},
        ]);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("renders");
        text_tag(&svg, "2019");
        text_tag(&svg, "2021");
    }

    /// `tooltip_header_text` (`contract.rs`) used to parse the RAW `xv` it
    /// was handed, so a year-shaped x built from integral floats failed the
    /// whole chart with `tooltip header: unparsed date "2019.0"` — a loud
    /// but unnamed over-refusal, and production renders this chart.
    /// `point_aria` now reads `x_domain_for_temporal[i]` for the header.
    #[test]
    fn tooltip_header_renders_for_integral_float_years() {
        let mut p = make_line_payload(true, false);
        p["rows"] = serde_json::json!([
            {"week": 2019.0, "signups": 100.0},
            {"week": 2020.0, "signups": 150.0},
            {"week": 2021.0, "signups": 130.0},
        ]);
        p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("tooltip_format on float years renders, matching production");
        assert!(svg.contains("2019"), "tooltip header names the year: {svg}");
    }

    /// Regression: a non-ASCII x domain used to panic the renderer inside
    /// `x_values_are_temporal_candidates`'s shape gate (byte-offset
    /// slicing on a `&str` that isn't a char boundary). Same corpus row as
    /// above with the five x values swapped for 6-byte strings that lead
    /// with a 3-byte "€" glyph — landing byte offset 2 mid-character,
    /// which is exactly the `FYYYYY` arm's `b.len() == 6` shape (€ is a
    /// glyph the shipped test font actually has, unlike CJK).
    #[test]
    fn non_ascii_x_values_render_without_panicking() {
        let mut p = make_log_line_payload();
        p["rows"] = serde_json::json!([
            {"week": "€180", "signups": 990000000.0},
            {"week": "€190", "signups": 1650000000.0},
            {"week": "€195", "signups": 2536000000.0},
            {"week": "€200", "signups": 6127000000.0},
            {"week": "€202", "signups": 7795000000.0},
        ]);
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("renders without panicking on non-ASCII x labels");
        assert!(svg.contains("<svg"));
    }

    /// Production never bakes ticks for a log scale, so its own gutter
    /// sizing is ALWAYS `estimated_quantitative_tick_labels`
    /// (`_measured_label_padding.py:274-338`), whose log branch measures
    /// d3's log tick candidates (`_log_tick_candidates`, `:348-361`):
    /// `k * 10^n` over `[990M, 7795M]`'s decades, formatted straight (no
    /// ruler compaction — that only applies to the BAKED case). Under
    /// `.3~s` the widest is `"400M"`, wider than the `"300M"` the port's
    /// OWN drawn mantissa ladder ever reaches, so the gutter is WIDER than
    /// measuring the drawn ladder gives and the plot narrower.
    /// Oracle: `axis-scales_0/world_population_log`'s reference gutter.
    #[test]
    fn log_scale_y_axis_gutter_measures_the_log_candidates_not_its_own_drawn_ladder() {
        let p = make_log_line_payload();
        let fonts = test_fonts();
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &fonts).expect("renders");
        let plot_w = rail_plot_w(&svg);
        let mf = measure::stand_in_family(TEST_FONT);
        let drawn_widest = fonts.measure("300M", mf, 11.0).unwrap();
        let candidate_widest = fonts.measure("400M", mf, 11.0).unwrap();
        assert!(candidate_widest > drawn_widest, "fixture assumption: {candidate_widest} vs {drawn_widest}");
        assert_eq!(plot_w, 520.0, "{svg}");
    }

    /// The cover-ladder estimate is shared with the LINEAR right-axis fit
    /// arm's own unbaked case (no `tick_values`, no themed `ticks.count` —
    /// `rungs_at`'s own guard above), not special-cased to log. Same
    /// `[990M, 7795M]`/`.3~s` shape as the log fixture, on a plain linear
    /// axis instead: `rungs_at`'s own d3 ladder at `ceil(400/40) = 10`
    /// draws only `0`..`8G` (2-char widest, "8G"), narrower than the cover
    /// ladder's own widest candidate ("7.8G", 4 chars) — so the gutter
    /// here is wider than measuring the drawn ladder would give, same
    /// direction as the log arm, just on the opposite (drawn ladder
    /// narrower) side of the comparison.
    #[test]
    fn an_unbaked_linear_axis_gutter_also_measures_the_cover_ladder() {
        let payload = fit_payload(|j| {
            j["rows"] = serde_json::json!([
                {"week": "2026-01-01", "signups": 990000000.0},
                {"week": "2026-01-08", "signups": 7795000000.0},
            ]);
            j["height"] = serde_json::json!(400.0);
            j["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::json!(".3~s");
            j["resolved"]["style"]["axis_y"]["ticks"]["count"] = serde_json::json!(null);
            j["resolved"]["style"]["axis_y"]["tick_label"] = serde_json::Value::Null;
            j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
        });
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        assert!(svg.contains(">8G<"), "the drawn ladder's own widest is 8G: {svg}");
        assert!(!svg.contains(">7.8G<"), "7.8G is never drawn, only measured: {svg}");
        let mf = measure::stand_in_family(TEST_FONT);
        let drawn_widest = fonts.measure("8G", mf, 11.0).unwrap();
        let cover_widest = fonts.measure("7.8G", mf, 11.0).unwrap();
        assert!(cover_widest > drawn_widest, "fixture assumption: {cover_widest} vs {drawn_widest}");
        assert_eq!(rail_plot_w(&svg), 529.0, "{svg}");
    }

    /// A right-orient, ladder-less axis whose `tick_label` carries
    /// `si_format` (a ladder-less axis's own compaction — see
    /// `common::sub_unit_guarded_format`) must reserve the gutter for what
    /// it actually DRAWS ("1.5M"), not for the bare spec alone
    /// ("1500000") — `estimated_gutter_of`'s own `si`-gated arm. Pins the
    /// RESERVATION, which the drawn-label tests near `format_y_label_tests`
    /// (common.rs) cannot: reverting that arm to the old plain
    /// `crate::number_locale::d3_format(spec, v)` still draws "1.5M" correctly (the
    /// label formatter is untouched) but reserves gutter width for the far
    /// wider "1500000" instead, so the plot stays narrower than this pins.
    #[test]
    fn a_right_orient_ladder_less_axis_reserves_the_si_compacted_gutter() {
        let payload = fit_payload(|j| {
            j["rows"] = serde_json::json!([
                {"week": "2026-01-01", "signups": 5.0},
                {"week": "2026-01-08", "signups": 1_500_000.0},
            ]);
            j["height"] = serde_json::json!(400.0);
            j["resolved"]["style"]["axis_y"]["position"] = serde_json::json!("right");
            j["resolved"]["style"]["axis_y"]["ticks"]["count"] = serde_json::json!(null);
            j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
            j["resolved"]["style"]["axis_y"]["tick_label"] = serde_json::json!({
                "format": ".3~r", "si_format": ".3~s", "scientific_format": ".3~e"
            });
        });
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        // The drawn ladder itself already reads "1.6M" (d3's own nice top
        // rung), never "1600000" — `format_y_label`'s own si_format branch,
        // ported earlier. This test's own job is the gutter those labels
        // drive, a SEPARATE code path (`estimated_gutter_of`).
        assert!(svg.contains(">1.6M<"), "{svg}");
        assert!(!svg.contains(">1600000<"), "{svg}");
        let mf = measure::stand_in_family(TEST_FONT);
        let plain_widest = fonts.measure("1600000", mf, 11.0).unwrap();
        let si_widest = fonts.measure("1.6M", mf, 11.0).unwrap();
        assert!(si_widest < plain_widest, "fixture assumption: {si_widest} vs {plain_widest}");
        assert_eq!(rail_plot_w(&svg), 526.0, "{svg}");
    }

    /// Production's unbaked path (`vl_field_maps.py:387`) measures the
    /// cover-ladder candidates through a PLAIN `d3_format(spec, v)` —
    /// `estimated_quantitative_tick_labels` never takes a `ruler`/
    /// `tick_label`; only its BAKED sibling `quantitative_tick_labels`
    /// does. A compacting ruler (exponent 3, mode "repeat") divides every
    /// candidate by 1,000 and appends a "k" suffix when applied to the
    /// DRAWN ladder — measuring the cover candidates through that same
    /// ruler machinery (the bug) would report the divided-and-suffixed
    /// width, dramatically NARROWER than the plain, undivided `.3~s`
    /// format production actually measures with.
    #[test]
    fn log_scale_gutter_estimate_ignores_a_ruler_and_uses_plain_d3_format() {
        let mut p = make_log_line_payload();
        p["resolved"]["style"]["axis_y"]["ruler"] = serde_json::json!({
            "exponent": 3, "mode": "repeat", "register": "narrative", "digit_spec": ",.1~f",
            "anchor_at_start": true, "reservation": "", "prefix": ""
        });
        p["resolved"]["style"]["axis_y"]["tick_label"] = serde_json::Value::Null;
        let fonts = test_fonts();
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &fonts).expect("renders");
        // The plain-format widest log candidate is still "400M" (same
        // domain as the test above) — the ruler's divided, suffixed
        // rendering of that same value ("400,000k") never governs the
        // gutter.
        assert_eq!(rail_plot_w(&svg), 520.0, "{svg}");
    }

    /// Production measures the domain BOUNDS ONLY when `scale.continuous.
    /// domain` is authored (`vl_field_maps.py:377-385`, "raw data values
    /// outside that range can't produce ticks there") — never the raw data
    /// extent. Data reaching far past an authored `[0, 100]` domain must
    /// not widen the gutter past what that domain's own cover ladder needs
    /// ("100", 3 chars) — the data extent's own cover ladder reaches much
    /// wider candidates ("87.7G", "100G").
    #[test]
    fn an_authored_domain_measures_its_own_bounds_not_the_data_extent() {
        let payload = fit_payload(|j| {
            j["rows"] = serde_json::json!([
                {"week": "2026-01-01", "signups": 10000000.0},
                {"week": "2026-01-08", "signups": 87654321000.0},
            ]);
            j["height"] = serde_json::json!(400.0);
            j["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::json!(".3~s");
            j["resolved"]["style"]["axis_y"]["ticks"]["count"] = serde_json::json!(null);
            j["resolved"]["style"]["axis_y"]["tick_label"] = serde_json::Value::Null;
            j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
            j["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [0.0, 100.0]}});
        });
        let fonts = test_fonts();
        let svg = render(&payload, &fonts).expect("renders");
        // "100" (3 chars) is the domain's own widest cover candidate under
        // `.3~s` — far narrower than the data extent's own widest
        // ("87.7G"/"100G") would be. Measuring the data instead of the
        // domain (the pre-fix bug) gives plot_w 523, 12px narrower than
        // this — measuring the domain widens the plot to 535.
        assert_eq!(rail_plot_w(&svg), 535.0, "{svg}");
    }

    /// Production's `resolve_measure_y_scale` (`_cartesian.py:1564-1575`)
    /// emits an authored `scale.continuous.domain` VERBATIM, in authored
    /// order — a descending pair inverts Vega's y-axis instead of getting
    /// sorted. A rank/bump chart's own `domain: [6, 1]` (rank 1 at the
    /// top) is exactly this shape: the row carrying the SMALLEST domain
    /// value (closest to the domain's second/low-range entry) must land
    /// NEAREST THE TOP (smallest SVG y), and the row carrying the
    /// LARGEST domain-relative value must land nearest the bottom —
    /// inverted from the ordinary ascending-domain case.
    #[test]
    fn descending_authored_y_domain_inverts_the_axis_smaller_value_nearer_top() {
        let payload = fit_payload(|j| {
            // Base fixture rows: signups 100 (week 1), 150 (week 2), 130 (week 3).
            j["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [200.0, 100.0]}});
        });
        let svg = render(&payload, &test_fonts()).expect("renders");
        let ys = path_vertex_ys(&svg);
        assert_eq!(ys.len(), 3, "{svg}");
        // Domain [200, 100]: value 100 -> range end (top, y=0 relative to
        // the plot), value 150 -> the midpoint, value 130 -> 30% of the
        // way down from the top. Ascending SVG y (top to bottom):
        // week1 (100) < week3 (130) < week2 (150) — the OPPOSITE order an
        // ordinary ascending domain would draw.
        assert!(ys[0] < ys[2], "week1 (100) must sit above week3 (130): {ys:?}\n{svg}");
        assert!(ys[2] < ys[1], "week3 (130) must sit above week2 (150): {ys:?}\n{svg}");
    }

    /// The zero rule's own `excludes_zero` gate already reads `authored_
    /// domain` direction-agnostically (`(a, b) -> !(a.min(b) <= 0.0 && 0.0
    /// <= a.max(b))`), so a descending authored domain that excludes 0
    /// must suppress the rule exactly like an ascending one does
    /// (`zero_rule_suppressed_by_authored_domain_excluding_zero` above) —
    /// this pins the SAME law on the reversed-pair shape a rank/bump
    /// chart actually authors (`domain: [6, 1]`, no rank 0 to rule at).
    #[test]
    fn descending_authored_y_domain_excluding_zero_still_suppresses_the_zero_rule() {
        let payload = fit_payload(|j| {
            set_zero_style(j, true);
            j["rows"] = serde_json::json!([
                {"week": "2026-01-01", "signups": 4.0},
                {"week": "2026-01-08", "signups": 2.0},
                {"week": "2026-01-15", "signups": 5.0},
            ]);
            j["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [6.0, 1.0]}});
        });
        let svg = render(&payload, &test_fonts()).expect("renders");
        assert!(!svg.contains(ZERO_RULE_COLOR), "authored [6, 1] excludes 0, suppresses: {svg}");
        assert!(!svg.contains(">0</text>"), "no 0 pulled into the ladder: {svg}");
    }

    /// The baked tick-value filter (`ax_y.tick_values`, non-empty) must
    /// bounds-test against the domain's own [min, max] regardless of which
    /// end `y_min`/`y_max` hold — a descending domain keeps `y_min > y_max`
    /// by construction (§ above), and a filter that tested `tv >= y_min &&
    /// tv <= y_max` verbatim would find an EMPTY range and drop every rung.
    /// This also exercises tick PLACEMENT: the same rung values production
    /// bakes descending (`nice_tick_values`'s own `reverse` law) must still
    /// land top-to-bottom in descending numeric order.
    #[test]
    fn descending_authored_y_domain_keeps_every_baked_tick_and_places_them_top_to_bottom() {
        let payload = fit_payload(|j| {
            j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
            j["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": [200.0, 100.0]}});
            j["resolved"]["style"]["axis_y"]["tick_values"] =
                serde_json::json!([200.0, 150.0, 100.0]);
        });
        let svg = render(&payload, &test_fonts()).expect("renders");
        // Three gridlines, one per baked rung — the filter (§ above) keeps
        // all three even though `y_min (200) > y_max (100)`; a bounds test
        // written against `y_min..=y_max` verbatim would find an empty
        // range and drop them all instead.
        let grid_ys: Vec<f64> = plot_lines(&svg)
            .iter()
            // Gridlines span the full plot width; the right-axis tick
            // marks sharing their y positions are short (6px) stubs.
            .filter(|(x1, _, x2, _)| (x2 - x1).abs() > 50.0)
            .map(|&(_, y1, _, _)| y1)
            .collect();
        assert_eq!(grid_ys.len(), 3, "{svg}");
        // Painted in `ax_y.tick_values`'s own baked (descending) order —
        // [200, 150, 100] — and on a domain of [200, 100] the LARGER
        // value lands FURTHEST DOWN (largest y), same law the mark-vertex
        // test above pins: descending tick order and descending y-position
        // move together, never ascending.
        let mut wanted_desc = grid_ys.clone();
        wanted_desc.sort_by(|a, b| b.partial_cmp(a).unwrap());
        assert_eq!(grid_ys, wanted_desc, "gridlines come out in descending y order: {grid_ys:?}");
        // All three baked rungs have room and survive the label-overlap
        // pass — "100" (the domain's low/top-of-plot end) sits above "200",
        // with "150" surviving in between: every tick must land at its OWN
        // y position, not the same `ph` every tick would share if `y_frac`
        // ever fell to its degenerate-domain branch here.
        let y200 = attr(&text_tag(&svg, "200"), "y");
        let y150 = attr(&text_tag(&svg, "150"), "y");
        let y100 = attr(&text_tag(&svg, "100"), "y");
        assert!(y100 < y150, "100 must sit above 150: {y100} vs {y150}\n{svg}");
        assert!(y150 < y200, "150 must sit above 200: {y150} vs {y200}\n{svg}");
    }

    /// The fit arm's ink pass (`tick_ys`, `frac_hi`/`frac_lo`, the baseline-
    /// rule/value-label `y_frac` calls) guarded every position behind
    /// `y_span > 0.0`, so a descending domain (`y_span < 0`) fell to the
    /// `else` branch and stacked EVERY tick's ink at the exact same `ph` —
    /// `parity_visible` then thinned the (identically-positioned) ladder
    /// down to its two endpoints, and the fit's own top-margin measurement
    /// (`frac_hi`/`frac_lo`, unclamped to the real min/max of the two data
    /// fractions) landed on the wrong plot rect. A descending domain must
    /// paint every baked rung and land on the SAME plot rect its ascending
    /// mirror does — the fit arm's own geometry has no preferred direction.
    #[test]
    fn descending_authored_y_domain_paints_every_tick_and_matches_the_ascending_mirror_plot_rect()
    {
        let render_with_domain = |domain: [f64; 2], tick_values: [f64; 6]| -> String {
            let payload = fit_payload(|j| {
                j["resolved"]["style"]["axis_y"]["scale"] =
                    serde_json::json!({"continuous": {"domain": domain}});
                j["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!(tick_values);
            });
            render(&payload, &test_fonts()).expect("renders")
        };
        // Rung values chosen to never collide with the x-axis's own day
        // labels ("1"/"8"/"15") — a collision would double-count a survivor.
        let ascending = render_with_domain([0.0, 1000.0], [0.0, 200.0, 400.0, 600.0, 800.0, 1000.0]);
        let descending =
            render_with_domain([1000.0, 0.0], [1000.0, 800.0, 600.0, 400.0, 200.0, 0.0]);
        for label in ["0", "200", "400", "600", "800", "1,000"] {
            assert_eq!(
                text_tags_all(&descending, label).len(),
                1,
                "descending domain must paint every baked rung, {label} missing/duplicated: {descending}"
            );
        }
        assert_eq!(
            group_y(&descending),
            group_y(&ascending),
            "descending and ascending domains fit the SAME plot rect top: {descending}\n{ascending}"
        );
        assert_eq!(
            plot_h_from_tick(&descending),
            plot_h_from_tick(&ascending),
            "descending and ascending domains fit the SAME plot rect height: {descending}\n{ascending}"
        );
    }

    /// `frac_hi`/`frac_lo`'s own top margin is driven by the DATA extent,
    /// not the baked ladder — hide the ticks (`ticks.visible: false`) so
    /// no baked tick label's ink can dominate the plot rect and mask a
    /// regression in `frac_hi`/`frac_lo` themselves.
    fn frac_probe_payload(domain: [f64; 2], rows: [f64; 5], labels_on: bool) -> Payload {
        fit_payload(|j| {
            j["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": domain}});
            j["resolved"]["style"]["axis_y"]["tick_values"] =
                serde_json::json!([400.0, 300.0, 200.0, 100.0, 0.0, -100.0]);
            j["resolved"]["style"]["axis_y"]["ticks"]["visible"] = serde_json::json!(false);
            j["rows"] = serde_json::json!([
                {"week": "2026-01-01", "signups": rows[0]},
                {"week": "2026-01-08", "signups": rows[1]},
                {"week": "2026-01-15", "signups": rows[2]},
                {"week": "2026-01-22", "signups": rows[3]},
                {"week": "2026-01-29", "signups": rows[4]},
            ]);
            j["width"] = serde_json::json!(564.0);
            j["height"] = serde_json::json!(408.0);
            j["resolved"]["style"]["line_mark"]["halo_multiplier"] = serde_json::json!(2.0);
            j["resolved"]["style"]["point_mark"] = serde_json::json!({
                "size": 46.24, "filled": true, "fill": "#FFFFFF", "stroke_width": 4.0,
                "labels": {"visible": false, "format": ".3~s", "position": "top"}
            });
            j["resolved"]["style"]["axis_y"]["grid"] = serde_json::json!({
                "visible": true, "opacity": 1.0, "width": 1.5, "color": "#D0D3D9",
                "threshold": {"color": "#8A8C8F", "width": 2.0, "visible": true}
            });
            if labels_on {
                j["resolved"]["style"]["line_mark"]["labels"] =
                    serde_json::json!({"visible": true, "format": ".3~s", "position": "top"});
            }
        })
    }

    /// `frac_hi`/`frac_lo` are what measure the fit's own top margin off
    /// the data extent — reversing the domain with the SAME data must land
    /// the plot rect at the SAME top, since neither the mark reach nor
    /// which physical end each data value lands on changes the OVERALL
    /// margin the two extremes together demand.
    #[test]
    fn descending_fit_ink_plot_rect_matches_its_ascending_mirror() {
        let rows = [-50.0, 120.0, 60.0, 300.0, 350.0];
        let descending = render(&frac_probe_payload([400.0, -100.0], rows, false), &test_fonts())
            .expect("renders");
        let ascending = render(&frac_probe_payload([-100.0, 400.0], rows, false), &test_fonts())
            .expect("renders");
        assert_eq!(
            group_y(&descending),
            group_y(&ascending),
            "the fit's own top margin has no preferred direction: {descending}\n{ascending}"
        );
    }

    /// The case above alone cannot catch `frac_hi`/`frac_lo`'s own min/max
    /// ordering — `(f_max.min(f_min), f_max.max(f_min))` mutated to bare
    /// `(f_max, f_min)` — because its rows sit strictly INSIDE `[400,
    /// -100]`, so no overhang crosses the plot edge either way. Data must
    /// sit at BOTH domain ends for the ordering itself to matter (the
    /// guard-revert case above still needs its own rows-inside-domain
    /// shape: with data at the ends, the guard revert's `(0, 1)` fallback
    /// happens to coincide with the correct answer there too).
    #[test]
    fn descending_fit_ink_plot_rect_matches_its_ascending_mirror_with_data_at_both_domain_ends() {
        let descending = render(
            &frac_probe_payload([400.0, -100.0], [-100.0, 120.0, 60.0, 300.0, 400.0], false),
            &test_fonts(),
        )
        .expect("renders");
        let ascending = render(
            &frac_probe_payload([-100.0, 400.0], [-100.0, 120.0, 60.0, 300.0, 400.0], false),
            &test_fonts(),
        )
        .expect("renders");
        assert_eq!(
            group_y(&descending),
            group_y(&ascending),
            "data at both domain ends has no preferred direction either: {descending}\n{ascending}"
        );
    }

    /// The L6 value-label text layer's own ink pass feeds the SAME
    /// fit-margin union as `frac_hi`/`frac_lo` above, off each row's own
    /// `y_frac`. A
    /// descending domain and its ascending mirror land on the same plot
    /// rect only when EVERY row (not just the two domain endpoints) keeps
    /// its physical position — the descending row nearest the domain's top
    /// end must be the ascending row nearest ITS top end too, so `-90`
    /// (this domain's top-of-plot extreme) maps to `390` under the
    /// reversed domain, and `350` (the bottom extreme) maps to `-50`; the
    /// three interior values keep their own positions on both sides.
    #[test]
    fn descending_value_label_ink_plot_rect_matches_its_ascending_mirror() {
        let descending = render(
            &frac_probe_payload([400.0, -100.0], [-90.0, 120.0, 60.0, 300.0, 350.0], true),
            &test_fonts(),
        )
        .expect("renders");
        let ascending = render(
            &frac_probe_payload([-100.0, 400.0], [390.0, 120.0, 60.0, 300.0, -50.0], true),
            &test_fonts(),
        )
        .expect("renders");
        assert_eq!(
            group_y(&descending),
            group_y(&ascending),
            "value-label ink has no preferred direction either: {descending}\n{ascending}"
        );
    }

    /// A rank/bump chart is multi-series with a visible endpoint rail
    /// (`charts/extensibility_1::bump`'s own real shape), not the
    /// single-series fit-only case the tests above cover. Two things this
    /// arm-specific path needs beyond the fit arm's own fix:
    /// `cascade_rail_anchors` clamps into `[cascade_lo, cascade_hi]` via
    /// `f64::clamp`, which PANICS if `cascade_lo > cascade_hi` — passing
    /// the raw (descending) `y_min > y_max` straight through would crash
    /// the render outright, not just mis-render it, so this pins that the
    /// render completes at all. `top_ink_at`'s own `mark_dist` term (this
    /// module, § above `top_datum`) must also pick `data_min` (not
    /// `data_max`) as the datum nearest the domain's top end on a
    /// descending domain — under-reserving that margin would shrink the
    /// title block short of what the real mark ink needs.
    #[test]
    fn descending_authored_y_domain_on_the_rail_arm_renders_without_panicking() {
        let mut p = make_multi_series_line_payload_with_bottom_legend();
        p["resolved"]["legend"]["visible"] = serde_json::json!(false);
        p["resolved"]["style"]["endpoint_labels"] =
            serde_json::json!({"visible": true, "label_offset": 6.0});
        p["rows"] = serde_json::json!([
            {"week": "2026-01-01", "series": "A", "signups": 1.0},
            {"week": "2026-01-08", "series": "A", "signups": 1.0},
            {"week": "2026-01-01", "series": "B", "signups": 3.0},
            {"week": "2026-01-08", "series": "B", "signups": 2.0},
        ]);
        p["resolved"]["style"]["axis_y"]["scale"] =
            serde_json::json!({"continuous": {"domain": [3.0, 1.0]}});
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("renders without panicking on the rail arm's own clamp");
        // Each series' own colored stroke path (not its white halo, which
        // shares the same `d` but a different `stroke` color).
        let series_ys = |color: &str| -> Vec<f64> {
            let needle = format!("stroke=\"{color}\"");
            let d_start = svg.find(&needle).unwrap();
            let d = svg[..d_start].rfind("d=\"M").unwrap() + 4;
            let d_end = d + svg[d..].find('"').unwrap();
            svg[d..d_end]
                .split(['M', 'L'])
                .map(|pair| pair.split(',').nth(1).unwrap().parse().unwrap())
                .collect()
        };
        let a_ys = series_ys("#3164a3");
        let b_ys = series_ys("#779bc9");
        assert_eq!((a_ys.len(), b_ys.len()), (2, 2), "{svg}");
        // Series A (rank 1 throughout) must sit above series B (rank 3
        // then rank 2) at every x — smaller value nearer the top, same law
        // the fit-arm test above pins.
        assert!(a_ys[0] < b_ys[0], "series A (rank 1) must sit above series B's rank-3 point: {a_ys:?} {b_ys:?}\n{svg}");
        assert!(a_ys[1] < b_ys[1], "series A (rank 1) must sit above series B's rank-2 point: {a_ys:?} {b_ys:?}\n{svg}");
    }

    /// `px_per_unit` must `.abs()` the SPAN (`y_max - y_min`) before
    /// `.max(1e-9)`, never after — clamping the raw (possibly negative)
    /// span first picks the tiny positive floor instead of the real span
    /// on a descending domain, collapsing `min_data_gap` to ~0. Two series
    /// tied at the same value on their last x must still cascade apart on
    /// a descending domain, by the SAME pixel gap magnitude a mirrored
    /// ascending domain produces — production's own `pixel_gap /
    /// abs(slope)` (`endpoint_labels.py:435`) has no direction preference
    /// either.
    #[test]
    fn tied_rail_labels_cascade_apart_on_a_descending_domain_like_its_ascending_mirror() {
        let render_with_domain = |domain: [f64; 2]| -> String {
            let mut p = make_multi_series_line_payload_with_bottom_legend();
            p["resolved"]["legend"]["visible"] = serde_json::json!(false);
            p["resolved"]["style"]["endpoint_labels"] =
                serde_json::json!({"visible": true, "label_offset": 6.0});
            p["rows"] = serde_json::json!([
                {"week": "2026-01-01", "series": "A", "signups": 1.0},
                {"week": "2026-01-08", "series": "A", "signups": 2.0},
                {"week": "2026-01-01", "series": "B", "signups": 3.0},
                {"week": "2026-01-08", "series": "B", "signups": 2.0},
            ]);
            p["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": domain}});
            render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
                .expect("renders")
        };
        let descending = render_with_domain([3.0, 1.0]);
        let ascending = render_with_domain([1.0, 3.0]);
        let desc_gap = (attr(&rail_tag(&descending, "A"), "y") - attr(&rail_tag(&descending, "B"), "y")).abs();
        let asc_gap = (attr(&rail_tag(&ascending, "A"), "y") - attr(&rail_tag(&ascending, "B"), "y")).abs();
        assert!(desc_gap > 1.0, "tied labels must cascade apart on a descending domain: {descending}");
        assert!(
            (desc_gap - asc_gap).abs() < 0.5,
            "the cascade gap must match its ascending mirror in magnitude: desc={desc_gap} asc={asc_gap}\n{descending}\n{ascending}"
        );
    }

    /// `top_ink_at`'s `top_datum` (this module, § above `top_datum`) must
    /// pick `data_min` — not `data_max` — as the datum nearest a
    /// DESCENDING domain's top end (`y_min > y_max`): a series pinned
    /// exactly at the domain's low (top-of-plot) end needs the mark's full
    /// reach reserved above it, not zero. Series A and B carry the SAME
    /// point-marker size, so reversing the domain (which series sits
    /// nearest the top swaps from A to B) must reserve the identical
    /// margin and land the title at the SAME baseline either way.
    #[test]
    fn descending_domain_top_margin_reserves_the_mark_nearest_the_top_matching_its_ascending_mirror(
    ) {
        let render_with_domain = |domain: [f64; 2]| -> String {
            let mut p = make_multi_series_line_payload_with_bottom_legend();
            p["resolved"]["legend"]["visible"] = serde_json::json!(false);
            p["resolved"]["style"]["endpoint_labels"] =
                serde_json::json!({"visible": true, "label_offset": 6.0});
            p["rows"] = serde_json::json!([
                {"week": "2026-01-01", "series": "A", "signups": 1.0},
                {"week": "2026-01-08", "series": "A", "signups": 1.0},
                {"week": "2026-01-01", "series": "B", "signups": 6.0},
                {"week": "2026-01-08", "series": "B", "signups": 6.0},
            ]);
            p["resolved"]["style"]["axis_y"]["scale"] =
                serde_json::json!({"continuous": {"domain": domain}});
            // A large point marker isolates the MARK term from the label
            // term: a baked ladder with no rung near either domain end's
            // own value (`3.0`, not `1.0`/`6.0`) keeps the label term's
            // own overhang small, so the margin is dominated by the
            // marker's own reach at the real datum nearest the top —
            // exactly the term `top_datum` picks.
            p["resolved"]["style"]["point_mark"] = serde_json::json!({"size": 900.0});
            p["resolved"]["style"]["axis_y"]["tick_values"] = serde_json::json!([3.0]);
            set_title_and_style(&mut p, None);
            render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
                .expect("renders")
        };
        let descending = render_with_domain([6.0, 1.0]);
        let ascending = render_with_domain([1.0, 6.0]);
        assert_eq!(
            attr(&text_tag(&descending, "Revenue"), "y"),
            attr(&text_tag(&ascending, "Revenue"), "y"),
            "the top margin has no preferred direction: {descending}\n{ascending}"
        );
    }

    /// Production's gate is `if axis.tick_values: baked else: estimate` —
    /// no `ticks.count` term at all (`vl_field_maps.py:367`). A chart that
    /// bakes NEITHER (e.g. `multiples: {scale: independent}`, which never
    /// resolves a themed `ticks.count`) still estimates in production, even
    /// though `rungs_at`'s own d3 ladder (a DIFFERENT law, for what the
    /// port itself draws) also has no baked count to re-fit against.
    #[test]
    fn a_chart_with_no_baked_ticks_count_either_still_estimates() {
        let payload = fit_payload(|j| {
            j["rows"] = serde_json::json!([
                {"week": "2026-01-01", "signups": 990000000.0},
                {"week": "2026-01-08", "signups": 7795000000.0},
            ]);
            j["height"] = serde_json::json!(400.0);
            j["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::json!(".3~s");
            j["resolved"]["style"]["axis_y"]["ticks"]["count"] = serde_json::json!(6.0);
            j["resolved"]["style"]["axis_y"]["tick_label"] = serde_json::Value::Null;
            j["resolved"]["style"]["axis_y"]["grid"]["visible"] = serde_json::json!(true);
        });
        let svg = render(&payload, &test_fonts()).expect("renders");
        // Identical to `an_unbaked_linear_axis_gutter_also_measures_the_
        // cover_ladder`'s own fixture and result, `ticks.count: 6.0`
        // baked here instead of unset — the gate ignores it either way.
        assert_eq!(rail_plot_w(&svg), 529.0, "{svg}");
    }

    /// The niced domain [1e8, 1e10] (`log_nice_domain`, floor/ceil of the
    /// data extent's own log10 to base-10 powers) draws a GRIDLINE at
    /// every one of `log_ticks`' 19 decade multiples regardless of label
    /// visibility (matches the linear axis's own law: only the LABEL mask
    /// is selective, never the grid), while the LABEL text only survives
    /// `greedy_visible`'s forward pass — hiding at least one candidate
    /// under `min_gap: 16.0` — with the domain's own endpoints always kept
    /// (a greedy pass never drops the first candidate).
    #[test]
    fn log_scale_y_axis_draws_every_decade_gridline_and_greedy_hides_some_labels() {
        let p = make_log_line_payload();
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("renders");

        let grid_color = "#D0D3D9";
        let grid_lines = svg.matches(&format!("stroke=\"{grid_color}\" stroke-width=\"1\"")).count();
        assert_eq!(grid_lines, 19, "one gridline per decade multiple in [1e8, 1e10]: {svg}");

        // `greedy_visible` always keeps the FIRST candidate (the domain
        // floor) — never the last, which is only kept when it clears
        // whatever the pass last kept before it.
        assert!(svg.contains(">100M<"), "the domain floor stays visible: {svg}");

        let candidates = [
            "100M", "200M", "300M", "400M", "500M", "600M", "700M", "800M", "900M", "1G", "2G",
            "3G", "4G", "5G", "6G", "7G", "8G", "9G", "10G",
        ];
        let drawn: Vec<&str> =
            candidates.iter().filter(|c| svg.contains(&format!(">{c}<"))).copied().collect();
        // 400M/600M/700M/800M/900M/4G/6G/7G/8G/9G never draw at all — d3's
        // mantissa blanking removes them from the ladder before greedy ever
        // sees them (k = 10·10/19 ≈ 5.263: mantissas 1..5 survive, 6..9
        // don't) — leaving 11 survivors, of which greedy then hides two
        // more (700M/7G's neighbors drop out at THIS payload's own fitted
        // plot height under `min_gap: 16.0`, distinguishing `greedy_visible`
        // from `parity_visible`'s every-other halving).
        assert_eq!(
            drawn,
            vec!["100M", "200M", "300M", "500M", "1G", "2G", "3G", "5G", "10G"],
            "{svg}"
        );
    }

    /// A log-typed axis skips the zero/unity baseline rules outright
    /// (production's `build_zero_rule_if_applicable(log_scale=true)`
    /// always returns `None` — a log domain has no legal position for a
    /// literal 0 or 1 datum) even when the theme's zero grid style would
    /// otherwise fire one.
    #[test]
    fn log_scale_y_axis_never_draws_the_zero_rule() {
        let mut p = make_log_line_payload();
        p["resolved"]["style"]["axis_y"]["grid"]["threshold"] =
            serde_json::json!({"color": "#8A8C8F", "width": 2.0});
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("renders");
        assert!(!svg.contains("stroke-width=\"2\""), "no zero-rule stroke drawn: {svg}");
    }

    /// Production's `_apply_unity` (`baseline.py`) carries NO log gate —
    /// unlike `build_zero_rule_if_applicable`, which explicitly refuses a
    /// log scale (`_cartesian.py` ~1288, the test above). A percent-typed
    /// log axis whose data extent straddles 1.0 draws the unity rule in
    /// production; `common::unity_rule_fires`'s own unbaked-domain
    /// fallback (branch 4 of `effective_measure_domain`) already reads the
    /// data extent correctly for this shape — the log arm only needs to
    /// stop suppressing the call.
    #[test]
    fn log_scale_y_axis_draws_the_unity_rule_when_percent_data_straddles_one() {
        let mut p = make_log_line_payload();
        p["rows"] = serde_json::json!([
            {"week": "1000", "signups": 0.5},
            {"week": "1200", "signups": 0.8},
            {"week": "1500", "signups": 1.2},
            {"week": "1650", "signups": 1.6},
            {"week": "1699", "signups": 2.0},
        ]);
        let ay = &mut p["resolved"]["style"]["axis_y"];
        ay["labels"]["format"] = serde_json::json!(".0%");
        ay["grid"]["threshold"] = serde_json::json!({"color": "#8A8C8F", "width": 2.0});
        let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts())
            .expect("renders");
        assert!(svg.contains("stroke-width=\"2\""), "unity rule drawn on a log axis: {svg}");
    }

    /// A log axis fits a multi-series chart on either side: the ladder is the
    /// d3 log ladder over every series' rows, painted at the axis's own edge.
    #[test]
    fn log_scale_renders_on_a_multi_series_chart_on_either_side() {
        for right in [false, true] {
            let mut p = make_multi_series_line_payload_with_bottom_legend();
            if right {
                p["resolved"]["style"]["axis_y"] = value_axis_json(false, Some("right"));
                p["resolved"]["style"]["axis_y"]["ticks"]["visible"] = serde_json::json!(true);
            }
            p["resolved"]["style"]["axis_y"]["scale"] = serde_json::json!({"continuous": {"type": "log"}});
            p["resolved"]["style"]["axis_y"]["labels"]["format"] = serde_json::json!(".3~s");
            let svg = render(&serde_json::from_value::<Payload>(p).unwrap(), &test_fonts()).expect("renders");
            assert!(svg.contains("layer_1_marks"), "right={right}: {svg}");
        }
    }

    // ── Small multiples: rows-only line facet ────────────────────────────

    mod facet {
        use super::*;
        use serde_json::json;

        /// A rows-only line facet payload: 2 panels (North/South), 3
        /// nominal months, right axis, untitled, stroke width 4 — reuses
        /// `make_fit_line_payload`'s own axis/style JSON (the same shape
        /// bar's own `make_facet_payload` reuses `make_vertical_payload`).
        fn make_facet_line_payload(scale: &str) -> serde_json::Value {
            let mut payload_json = make_fit_line_payload();
            // `axis_json`'s own test-fixture default bakes `is_quantitative:
            // true` regardless of the real x field's shape (harmless for the
            // single-chart fit arm, which never branches on it for a plain
            // nominal x) — the facet composer DOES refuse a genuinely
            // quantitative x, so every facet fixture states the real shape
            // explicitly, matching how every other nominal-x test in this
            // file already overrides this same default.
            payload_json["resolved"]["style"]["axis_x"]["is_quantitative"] = json!(false);
            payload_json["resolved"]["multiples"] =
                json!({"rows": "region", "columns": null, "scale": scale});
            payload_json["resolved"]["panel_axes"] =
                json!([{"field": "region", "values": ["\"North\"", "\"South\""]}]);
            payload_json["rows"] = json!([
                {"month": "Jan", "region": "North", "signups": 100.0},
                {"month": "Feb", "region": "North", "signups": 150.0},
                {"month": "Mar", "region": "North", "signups": 130.0},
                {"month": "Jan", "region": "South", "signups": 90.0},
                {"month": "Feb", "region": "South", "signups": 60.0},
                {"month": "Mar", "region": "South", "signups": 120.0},
            ]);
            payload_json["width"] = json!(480.0);
            payload_json["height"] = json!(400.0);
            payload_json["padding"] = json!(null);
            payload_json
        }

        fn render_facet_payload(payload_json: serde_json::Value) -> Result<String, String> {
            let payload: Payload = serde_json::from_value(payload_json).unwrap();
            let fonts = test_fonts();
            render_inner(&payload, &fonts, false).map_err(|e| e.to_string())
        }

        /// B1: two row panels compose into one card — canvas size, each
        /// row's own `translate(x, y)`, and the shared x axis's own tick
        /// positions are pinned from THIS renderer's own arithmetic (no
        /// external oracle for this unit test, same convention A6 already
        /// uses): `margin_left = ceil(HEADER_TITLE_OFFSET (10) +
        /// max("North","South" shaped width) + left_mark_overhang (the
        /// panel's own stroke/halo reach past its own domain origin, no
        /// mirror))`, `canvas_w = (margin_left - left_mark_overhang) +
        /// panel_w` (`padding: null` — the composer's own content origin
        /// is `canvas_x(0)`, `left_mark_overhang` short of `margin_left`,
        /// never `margin_left` alone), South's own row starts at
        /// `child_height + grid padding (20)` below North's, and every
        /// shared-axis tick's own `x` is `canvas_x(dx) + left_mark_
        /// overhang + 0.5` — the CELL's own declared origin (`canvas_x(dx)`
        /// is the panel's own canvas edge a panel's content splices at;
        /// the footer axis is a separate Vega role that sits
        /// `left_mark_overhang` to the right of that edge, at the cell's
        /// own origin, per `facet_compose.rs`'s `canvas_x` doc comment).
        ///
        /// Mutations (each independently confirmed FAILED against this
        /// exact assertion, then reverted):
        /// - Deleting the `fold_axis_gutter` union — `canvas_w` shrinks
        ///   because the value-axis gutter no longer folds into `bounds`.
        /// - Forcing `shared_domain_rows = None`-equivalent (each panel
        ///   reads its own `rows` for the y ladder instead of the shared
        ///   `payload.rows`) — South's own top gridline moves off North's,
        ///   changing South's row content without changing the row OFFSET
        ///   itself, so this is cross-checked by the val_ticks-based test
        ///   below instead (row offset alone can't see a ladder-only
        ///   change).
        /// - Dropping the `+ left_mark_overhang` the footer axis adds to
        ///   `canvas_x(dx)` moves every tick's own `x` left by `left_mark_
        ///   overhang`, landing them back on the panel's own canvas edge
        ///   instead of the cell's real origin — confirmed FAILED against
        ///   every assertion below.
        #[test]
        fn rows_only_line_facet_geometry_matches_pinned_coordinates() {
            let svg = render_facet_payload(make_facet_line_payload("shared")).expect("renders");
            assert!(
                svg.starts_with(
                    r#"<svg xmlns="http://www.w3.org/2000/svg" width="437" height="400" viewBox="0 0 437 400">"#
                ),
                "canvas size (widens by `row_footer_overhang`: the value-axis anchor below reaches \
                 that much further right, and the ink fold that reserves canvas width reaches the \
                 same distance): {svg}"
            );
            // Two row-header labels, two row groups, each at the composer's
            // own `canvas_x(dx)` (not the raw, unshifted grid offset).
            assert_eq!(svg.matches(">North<").count(), 1);
            assert_eq!(svg.matches(">South<").count(), 1);
            assert!(
                svg.contains(r#"<g transform="translate(38.00,0.00)">"#),
                "North's own row offset: {svg}"
            );
            assert!(
                svg.contains(r#"<g transform="translate(38.00,200.00)">"#),
                "South's own row offset (North's own child_height + grid padding below): {svg}"
            );
            // The shared x axis (painted once, below South) ticks at Jan/
            // Feb/Mar — each at `canvas_x(dx) + left_mark_overhang + 0.5`,
            // the cell's own origin (`left_mark_overhang` right of the
            // panel splice above), so the footer's own ticks land on the
            // data they label rather than on the panel's own canvas edge.
            assert!(
                svg.contains(r#"<text x="42.500" y="398.500" text-anchor="middle""#)
                    && svg.contains(">Jan<"),
                "Jan tick at canvas_x(0) + left_mark_overhang + 0.5: {svg}"
            );
            assert!(
                svg.contains(r#"<text x="402.500" y="398.500" text-anchor="middle""#)
                    && svg.contains(">Mar<"),
                "Mar tick at canvas_x(360) + left_mark_overhang + 0.5: {svg}"
            );
            // The rows-only value axis (real Vega's `row_footer`) anchors
            // at `ceil(cell x2)` (`vega-view-transforms/src/layout/
            // grid.js:262`, `ceil` from `grid.js:13`'s own `agg` callback)
            // — the mark's own right overhang past `plot_w`, symmetric
            // with `left_mark_overhang`'s own left reserve for a point
            // scale — not `plot_w` alone. North's own "100" tick label
            // (local frame, inside its own row's `<g>`) sits at `plot_w
            // (360) + left_mark_overhang (4.0) + 0.5 + y_tick_eff +
            // y_gutter`, `left_mark_overhang` right of the un-shifted
            // `391.087` this rung painted before this fix.
            assert!(
                svg.contains(r#"<text x="395.087" y="99.056" text-anchor="end""#)
                    && svg.contains(">100<"),
                "North's own value-axis label reaches `left_mark_overhang` past `plot_w`: {svg}"
            );
        }

        /// `LinePanelGeometry.ink`'s mark box (`Bounds::new(.., y_scale.
        /// position(own_max), .., y_scale.position(own_min))`) must sort
        /// the two positions before building the box — `y_scale.position`
        /// is direction-agnostic, so on a descending domain `own_max`
        /// lands BELOW `own_min` in pixel space, and an unsorted
        /// `Bounds::new` (top-left semantics) silently drops the stroke/
        /// point overhang at both ends. A descending domain keeps the same top
        /// margin overhang its ascending mirror measures, same law the
        /// single-chart fit arm's `frac_hi`/`frac_lo` fix already carries.
        #[test]
        fn descending_authored_y_domain_on_a_facet_keeps_the_top_mark_overhang() {
            let render_with_domain = |domain: [f64; 2]| -> String {
                let mut p = make_facet_line_payload("shared");
                p["resolved"]["style"]["line_mark"]["stroke"]["width"] = json!(20.0);
                p["resolved"]["style"]["axis_y"]["scale"] =
                    json!({"continuous": {"domain": domain}});
                p["rows"] = json!([
                    {"month": "Jan", "region": "North", "signups": 0.0},
                    {"month": "Feb", "region": "North", "signups": 150.0},
                    {"month": "Mar", "region": "North", "signups": 130.0},
                    {"month": "Jan", "region": "South", "signups": 90.0},
                    {"month": "Feb", "region": "South", "signups": 60.0},
                    {"month": "Mar", "region": "South", "signups": 200.0},
                ]);
                render_facet_payload(p).expect("renders")
            };
            let descending = render_with_domain([200.0, 0.0]);
            let ascending = render_with_domain([0.0, 200.0]);
            // The mark overhang at the domain top lands in a panel's own top margin
            // whichever way the domain runs: North holds it under the descending
            // domain, South under the ascending one.
            let margin = r#"<g transform="translate(20.000,20.000)">"#;
            assert!(descending.contains(margin), "{descending}");
            assert!(ascending.contains(margin), "{ascending}");
        }

        /// `row_footer_overhang` (`facet_compose.rs`) must ceil the mark's
        /// own RAW, UNCEILED right reach — never `left_mark_overhang`,
        /// already rounded up once by `vega_fit::pad_plot`. The two only
        /// agree when the raw reach is itself integral (a flat line
        /// stroke, bar's crisp-stroke constant); a point mark's `size`-
        /// derived reach usually is not. Rung 06's own default point style
        /// (`point_mark.size: 46.24`, `line_mark.halo_multiplier: 2.0`)
        /// gives a raw reach of `point_mark_reach(46.24, 2.0) = 6.8083...`
        /// — real Vega ceils `plot_w + 6.8083` ONCE (`grid.js:13`'s `agg`,
        /// `:313`); rounding `left_mark_overhang` (`ceil(6.8083) = 7`)
        /// first and ceiling THAT sum instead only agrees when `plot_w`'s
        /// own fraction happens to push both sums across the same integer
        /// boundary — not the case here. `payload.width = 600.1` lands
        /// this fixture's own panel-width correction budget at exactly `plot_w
        /// = 480.1` (matching the corpus probe this regression is modeled
        /// on, `fixtures/multiples/06-line-rows` at its own slot width
        /// 600.1): the real law's `ceil(480.1 + 6.8083) - 480.1 = 6.9`; a
        /// rounded-first formula instead gives `ceil(480.1 + 7) - 480.1 =
        /// 7.9`, a full pixel past this test's own pinned label. Mutation:
        /// swap `raw_right_reach` back for
        /// `left_mark_overhang` in `facet_compose.rs`'s `row_footer_
        /// overhang` formula and North's own "100" tick label moves from
        /// `x="518.087"` to `x="519.087"`.
        #[test]
        fn line_facet_row_footer_ceils_the_raw_mark_reach_not_the_rounded_one() {
            let mut payload_json = make_facet_line_payload("shared");
            payload_json["resolved"]["style"]["point_mark"] = json!({"size": 46.24});
            payload_json["resolved"]["style"]["line_mark"]["halo_multiplier"] = json!(2.0);
            payload_json["width"] = json!(600.1);
            let svg = render_facet_payload(payload_json).expect("renders");
            assert!(
                svg.contains(r#"<text x="518.087" y="96.556" text-anchor="end""#)
                    && svg.contains(">100<"),
                "North's own value-axis label reaches `ceil(plot_w + raw_right_reach)`, \
                 not `ceil(plot_w + left_mark_overhang)`: {svg}"
            );
        }
        /// `raw_right_reach` must floor at `0.0` and read the REAL cell
        /// ink (`vega_fit::FitMargins.raw_left`), never a mark-only
        /// formula. A point scale's own `axis_x.scale.padding` insets the
        /// FIRST/LAST datum that many px inside `plot_w` on both ends
        /// (`PointScale::new`) — at `padding: 10` against this fixture's
        /// default `line_stroke_w: 4` (no point marks), a mark-only formula
        /// (`line_stroke_w.max(geom_point_reach) - x_scale_pad = 4 - 10 =
        /// -6`, UNFLOORED) anchors the value axis INSIDE the plot instead
        /// of past it. The real ink never reaches negative: `pad_plot`
        /// seeds its own `ink` closure with the plot rect `[0, plot_w]`
        /// itself, so `raw_left` floors at `0.0` here (the marks sit
        /// fully within `[10, plot_w - 10]`, short of both edges) —
        /// `row_footer_overhang = ceil(panel_w + 0.0) - panel_w`. Mutation:
        /// swap `plot.raw_left` back for the mark-only `line_stroke_w.max
        /// (geom_point_reach) - x_scale_pad` at this call's own
        /// `PanelGeometry` construction site (`render_facet`'s closure)
        /// and North's own "100" tick label moves from `x="512.087"` to
        /// `x="506.087"`.
        #[test]
        fn line_facet_row_footer_reach_floors_at_zero_not_a_negative_mark_only_term() {
            let mut payload_json = make_facet_line_payload("shared");
            payload_json["resolved"]["style"]["axis_x"]["scale"] = json!({"padding": 10.0});
            payload_json["width"] = json!(600.1);
            let svg = render_facet_payload(payload_json).expect("renders");
            assert!(
                svg.contains(r#"<text x="512.087" y="99.056" text-anchor="end""#)
                    && svg.contains(">100<"),
                "North's own value-axis label reaches `ceil(plot_w + raw_left.max(0.0))`, \
                 never a negative mark-only term: {svg}"
            );
        }
        /// Real Vega's `row_footer` anchors at `ceil(cell x2)` — the CEIL
        /// of `plot_w + raw_right_reach`, not that sum itself. The two
        /// agree whenever `plot_w` is already a whole number (every other
        /// rung in this suite), so a fractional `plot_w` is the only shape
        /// that can tell the ceiled law apart from the un-ceiled sum: at
        /// `payload.width = 481.5` this fixture's own panel-width correction
        /// budget lands `plot_w = 361.5`, so `ceil(361.5 + 4.0) - 361.5 =
        /// 4.5`, half a pixel past the un-ceiled `4.0` every other rung
        /// measures (this fixture's own `raw_right_reach` is `4.0` here,
        /// the same as `left_mark_overhang` — no point marks, `x_scale_pad:
        /// 0` — so this test can't tell the two terms apart; `line_facet_
        /// row_footer_ceils_the_raw_mark_reach_not_the_rounded_one` above
        /// does). Mutation: drop the `.ceil()` in `facet_compose.rs`'s own
        /// `row_footer_overhang` formula and this label lands back at
        /// `396.587` (`plot_w + 4.0 + 31.087`, the un-ceiled anchor).
        #[test]
        fn line_facet_row_footer_ceils_a_fractional_plot_width() {
            let mut payload_json = make_facet_line_payload("shared");
            payload_json["width"] = json!(481.5);
            let svg = render_facet_payload(payload_json).expect("renders");
            assert!(
                svg.contains(r#"<text x="397.087" y="99.056" text-anchor="end""#)
                    && svg.contains(">100<"),
                "North's own value-axis label reaches `ceil(plot_w + raw_right_reach)`, \
                 not the un-ceiled sum: {svg}"
            );
        }

        /// B2: exactly one row-header label per PANEL (rows-only: 1 column,
        /// so every panel IS its row's last — and only — column). Mutation:
        /// change `i % panel_cols_n == 0` to always-true in the composer's
        /// own row-header loop (paint the label on every column instead of
        /// column 0) — inert on a 1-column facet, so this test alone can't
        /// catch that mutation; it pins the COUNT law instead (2 rows -> 2
        /// labels, never one per panel-column-pair a wider grid would
        /// produce).
        #[test]
        fn rows_only_line_facet_draws_one_row_header_per_panel() {
            let svg = render_facet_payload(make_facet_line_payload("shared")).expect("renders");
            assert_eq!(svg.matches(">North<").count(), 1, "one row header for North: {svg}");
            assert_eq!(svg.matches(">South<").count(), 1, "one row header for South: {svg}");
        }

        /// The composite canvas rect is the CHART's own effective
        /// background (`FacetSpec.background`, `r.background.as_str()`),
        /// never the board-level `payload.background` — the same
        /// contract `area_backdrop_fill_reads_the_chart_own_background_
        /// not_the_board_background` pins for the single-chart path,
        /// reached here for the first time on the facet composer (`compose_
        /// facet`'s panel splice never carries a panel's own background
        /// rect through to the composite — only the one canvas rect this
        /// composer paints itself survives). Mutation: swap `spec.
        /// background` back for `payload.background.as_deref().unwrap_or
        /// ("#ffffff")` at `compose_facet`'s own `bg` binding and the
        /// canvas rect's fill reverts to `#ffffff`.
        #[test]
        fn line_facet_canvas_reads_the_chart_own_background_not_the_board_background() {
            let mut payload_json = make_facet_line_payload("shared");
            payload_json["resolved"]["background"] = json!("#d9a45b");
            payload_json["background"] = json!("#ffffff");
            let svg = render_facet_payload(payload_json).expect("renders");
            let canvas_rect = svg.split("<rect ").nth(1).expect("canvas rect present");
            assert!(
                canvas_rect.starts_with("width=") && canvas_rect.contains("fill=\"#d9a45b\"/>"),
                "the composite canvas paints the chart's own background, not the board's: {svg}"
            );
        }

        /// B3: a point scale with `axis_x.scale.padding: 0` (the theme
        /// default) puts the first datum at x=0, so its own stroke
        /// overhangs LEFT of the plot origin — `left_mark_overhang`
        /// (`plot.left`, read by `compose_facet` as `PanelGeometry.
        /// left_mark_overhang`) must carry a REAL, stroke-driven value
        /// here, never a bar-style flat constant: a fatter stroke reserves
        /// MORE room in the row-header column, so `margin_left` grows with
        /// it. `canvas_x(0)` (the panel splice) stays fixed regardless —
        /// `margin_left` and `left_mark_overhang` grow together and cancel
        /// in `canvas_x`'s own formula — but the canvas's own WIDTH still
        /// moves, because the mark's own real ink (`geom.ink`, built from
        /// the REAL stroke width, independent of the `left_mark_overhang`
        /// field this composer separately carries for the row-header
        /// reserve) widens `content_w` on its own. Pinned at the exact
        /// self-computed widths (no external oracle for this unit test,
        /// same convention A6 already uses) rather than a hand-picked
        /// growth threshold, since a flat-constant mutation moves the
        /// canvas the WRONG direction (it decouples `margin_left` from the
        /// real stroke while `content_w` keeps scaling with it, widening
        /// the canvas MORE, not collapsing it to a single value) — a
        /// growth-threshold assertion could not tell the two laws apart.
        /// Mutation: hardcode `left_mark_overhang` to a flat `2.0` (bar's
        /// own crisp-stroke value) at the `PanelGeometry` construction
        /// site in `line.rs` — the thick-stroke width moves off its pinned
        /// value.
        #[test]
        fn line_facet_mark_overhang_scales_with_the_stroke_width_not_a_flat_constant() {
            let canvas_w = |stroke_w: f64| -> f64 {
                let mut payload_json = make_facet_line_payload("shared");
                payload_json["resolved"]["style"]["line_mark"]["stroke"]["width"] = json!(stroke_w);
                let svg = render_facet_payload(payload_json).expect("renders");
                let start = svg.find("width=\"").expect("a width attr") + "width=\"".len();
                let end = svg[start..].find('"').expect("closing quote") + start;
                svg[start..end].parse().expect("numeric width")
            };
            assert_eq!(canvas_w(1.0), 431.0, "a 1px stroke's own overhang");
            assert_eq!(canvas_w(40.0), 480.0, "a 40px stroke's own overhang");
        }

        /// The mark-ink box (and so `geom.ink`) is built
        /// from THIS panel's own `rows`, never the facet's shared domain —
        /// a panel whose own max sits well below the shared max must not
        /// reserve phantom stroke overhang for a peak it never draws.
        /// Mutation: swap `own_max`/`own_min` back for `data_max`/`data_min`
        /// at either mark-ink site (the fit-pass union or the geometry
        /// mark box) and South's own `ink.y1` drops above `plot.top`
        /// (reserving room for North's own, taller peak).
        #[test]
        fn facet_mark_ink_reads_the_panels_own_rows_not_the_shared_domain() {
            let payload: Payload = serde_json::from_value(make_facet_line_payload("shared")).unwrap();
            let fonts = test_fonts();
                let opts = FacetPanelOpts {
                panel_key: "",
                panel_ordinal: 0,
                suppress_x_axis: true,
                paint_value_axis: true,
                fold_axis_gutter: true,
                value_axis_right_overhang: 0.0,
                mirror_band_x: false,
                shared_domain_rows: &payload.rows,
                independent_scale: false,
            };
            // South's own rows: max 120, well under North's shared-visible
            // max of 150 — a facet using the SHARED extent for ink would
            // still reserve room as though South's own peak were near
            // North's, expanding `ink.y1` above the plot's own top.
            let south_rows: Vec<serde_json::Map<String, serde_json::Value>> = serde_json::from_value(json!([
                {"month": "Jan", "signups": 90.0},
                {"month": "Feb", "signups": 60.0},
                {"month": "Mar", "signups": 120.0},
            ]))
            .unwrap();
            let bound = crate::families::line::bind::bind(&payload, false).expect("binds");
            let south = crate::families::line::layout::fit(&bound, &payload, &south_rows, (300.0, 179.0), None, &fonts, Some(&opts)).expect("renders");
            let (plot_south, geom_south) = (south.plot, south.geometry);
            assert!(
                (geom_south.ink.y1 - plot_south.top).abs() < 1e-6,
                "South's own max (120) sits well under the shared max (150) — ink.y1 must sit \
                 at plot.top (no phantom stroke overhang for a peak South never draws): \
                 ink.y1={} plot.top={}",
                geom_south.ink.y1,
                plot_south.top
            );
        }

        /// Coordinate-frame regression: `val_ticks` must be in the SAME
        /// absolute canvas frame `ink` is (`plot_top + y_scale.position(tv)
        /// + shift`), matching bar's own `PanelGeometry.val_ticks`
        /// (`bar.rs`'s value-label paint loop, `plot_top + linear.
        /// position(tv) + ...`) — a composer painting a mirror ghost off
        /// this field reads line and bar panels identically.
        /// Mutation: drop the `plot_top +` term and every `val_ticks` y
        /// value shifts DOWN by exactly `plot.top` (4.0 on this fixture,
        /// 3.0/1.0 label-baseline crisp rounding on top of that) —
        /// confirmed FAILED against the exact pinned value below (`202.5`
        /// moves to `198.5`).
        #[test]
        fn val_ticks_are_in_the_panels_own_canvas_frame() {
            let payload: Payload = serde_json::from_value(make_fit_line_payload()).unwrap();
            let fonts = test_fonts();
                let opts = FacetPanelOpts {
                panel_key: "",
                panel_ordinal: 0,
                suppress_x_axis: true,
                paint_value_axis: true,
                fold_axis_gutter: true,
                value_axis_right_overhang: 0.0,
                mirror_band_x: false,
                shared_domain_rows: &payload.rows,
                independent_scale: false,
            };
            let (_, plot, geom) = render_panel(&payload, &fonts, 480.0, 179.0, Some(&opts));
            assert_eq!(plot.top, 4.0, "this fixture's own fit-pass top margin: {geom:?}", geom = geom.val_ticks);
            assert_eq!(
                geom.val_ticks.first().map(|&(y, _)| y),
                Some(202.5),
                "the \"100\" tick's own absolute y (plot_top + y_scale.position(100.0)): {:?}",
                geom.val_ticks
            );
            for &(abs_y, _) in &geom.val_ticks {
                assert!(
                    abs_y >= plot.top - 1.0,
                    "val_ticks must sit at or below plot.top ({}) in the canvas frame, got {abs_y}",
                    plot.top
                );
            }
        }

        /// The x domain is bound off the chart's whole rows (`multiples.scale: shared`), never the
        /// panel's own `rows` — South's own rows never mention "Apr", but the chart's do, so
        /// South's own x axis must still carry an "Apr" tick.
        #[test]
        fn the_charts_rows_extend_the_x_domain_past_the_panels_own_categories() {
            let mut json = make_fit_line_payload();
            json["rows"] = json!([
                {"month": "Jan", "signups": 100.0},
                {"month": "Feb", "signups": 150.0},
                {"month": "Mar", "signups": 130.0},
                {"month": "Apr", "signups": 140.0},
            ]);
            let payload: Payload = serde_json::from_value(json).unwrap();
            let fonts = test_fonts();
            let panel_rows: Vec<serde_json::Map<String, serde_json::Value>> = payload.rows[..3].to_vec();
            let opts = FacetPanelOpts {
                panel_key: "",
                panel_ordinal: 0,
                suppress_x_axis: true,
                paint_value_axis: true,
                fold_axis_gutter: true,
                value_axis_right_overhang: 0.0,
                mirror_band_x: false,
                shared_domain_rows: &payload.rows,
                independent_scale: false,
            };
            let bound = crate::families::line::bind::bind(&payload, false).expect("binds");
            let fitted = crate::families::line::layout::fit(&bound, &payload, &panel_rows, (480.0, 179.0), None, &fonts, Some(&opts))
                .expect("renders");
            match &fitted.geometry.axis.body {
                LineXAxisBody::Nominal { labels: domain, .. } => {
                    assert!(domain.contains(&"Apr".to_string()), "domain must carry the shared-only category: {domain:?}");
                }
                _ => panic!("expected a nominal x axis body"),
            }
        }

        /// Under `suppress_x_axis`, x-axis chrome (labels,
        /// tick strokes, title band) never joins the fit-ink union — the
        /// negotiated margin is the SAME whether or not the x axis carries
        /// a visible title, since none of that ink is ever unioned.
        /// Mutation: drop any one of the label/tick-stroke/title ink skips
        /// (`line.rs`'s `if !suppress_x_axis` guards inside the fit-pass x
        /// axis block) and the titled fixture's own canvas grows past the
        /// untitled one's.
        #[test]
        fn suppress_x_axis_reserves_the_same_margin_with_or_without_an_x_title() {
            let untitled: Payload = serde_json::from_value(make_fit_line_payload()).unwrap();
            let mut titled_json = make_fit_line_payload();
            titled_json["resolved"]["style"]["axis_x"]["title"]["visible"] = json!(true);
            let titled: Payload = serde_json::from_value(titled_json).unwrap();
            let fonts = test_fonts();
            let opts_untitled = FacetPanelOpts {
                panel_key: "",
                panel_ordinal: 0,
                suppress_x_axis: true,
                paint_value_axis: true,
                fold_axis_gutter: true,
                value_axis_right_overhang: 0.0,
                mirror_band_x: false,
                shared_domain_rows: &untitled.rows,
                independent_scale: false,
            };
            let opts_titled = FacetPanelOpts { shared_domain_rows: &titled.rows, ..opts_untitled };
            let (_, plot_untitled, _) =
                render_panel(&untitled, &fonts, 480.0, 179.0, Some(&opts_untitled));
            let (_, plot_titled, _) = render_panel(&titled, &fonts, 480.0, 179.0, Some(&opts_titled));
            assert_eq!(
                (plot_untitled.top, plot_untitled.bottom, plot_untitled.canvas_h),
                (plot_titled.top, plot_titled.bottom, plot_titled.canvas_h),
                "an x title must not move the negotiated margin under suppress_x_axis"
            );
            // Pinned, not just relative: `plot.bottom` (`m.bottom`, the ink
            // overhang PAST the declared plot) stays at this fixture's own
            // mark-driven `4.0` — never the x-axis's OWN label/tick-stroke
            // reach (a nominal x with Jan/Feb/Mar labels and visible ticks
            // would push this well past 4.0 if either ink skip were
            // dropped, even though BOTH renders here would still agree
            // with each other — the titled-vs-untitled comparison above
            // only catches a skip that varies with the TITLE specifically).
            assert_eq!(
                plot_untitled.bottom, 4.0,
                "m.bottom must stay at the mark's own overhang, never grow from unskipped \
                 x-label/tick-stroke ink: top={} bottom={}",
                plot_untitled.top, plot_untitled.bottom
            );
        }

        /// The y-label ink clamp to `[0, ph]` (a facet panel's
        /// value axis is real Vega's `row_footer`, whose LABEL vertical
        /// overhang never shifts a row's own marks) means a crowded label
        /// near the plot's own top/bottom edge never grows the negotiated
        /// margin under `suppress_x_axis` — pinned the same way as the
        /// title gate above: the margin must be identical whether the
        /// y-axis label font is tiny or huge (huge enough to overhang the
        /// plot vertically without the clamp).
        #[test]
        fn suppress_x_axis_clamps_y_label_ink_so_a_huge_label_font_does_not_grow_the_margin() {
            let small: Payload = serde_json::from_value(make_fit_line_payload()).unwrap();
            let mut huge_json = make_fit_line_payload();
            huge_json["resolved"]["style"]["axis_y"]["labels"]["font"]["size"] = json!(60.0);
            let huge: Payload = serde_json::from_value(huge_json).unwrap();
            let fonts = test_fonts();
            let opts_small = FacetPanelOpts {
                panel_key: "",
                panel_ordinal: 0,
                suppress_x_axis: true,
                paint_value_axis: true,
                fold_axis_gutter: true,
                value_axis_right_overhang: 0.0,
                mirror_band_x: false,
                shared_domain_rows: &small.rows,
                independent_scale: false,
            };
            let opts_huge = FacetPanelOpts { shared_domain_rows: &huge.rows, ..opts_small };
            let (_, plot_small, _) = render_panel(&small, &fonts, 480.0, 179.0, Some(&opts_small));
            let (_, plot_huge, _) = render_panel(&huge, &fonts, 480.0, 179.0, Some(&opts_huge));
            assert_eq!(
                (plot_small.top, plot_small.bottom),
                (plot_huge.top, plot_huge.bottom),
                "a huge y-label font must not grow the vertical margin under suppress_x_axis \
                 (only its gutter WIDTH matters, clamped to [0, ph] on the vertical axis)"
            );
        }

        /// `paint_title`/the rotated y-title both gate off
        /// `suppress_x_axis`/`paint_value_axis` — a titled fixture must
        /// drop both titles under the facet-panel opts (composer paints
        /// its own shared x title; a non-last-column panel never draws the
        /// y title) while still drawing both on the single-chart path.
        #[test]
        fn facet_opts_suppress_both_axis_titles_on_a_titled_fixture() {
            let mut titled_json = make_fit_line_payload();
            titled_json["resolved"]["style"]["axis_x"]["title"]["visible"] = json!(true);
            titled_json["resolved"]["style"]["axis_y"]["title"]["visible"] = json!(true);
            let payload: Payload = serde_json::from_value(titled_json).unwrap();
            let fonts = test_fonts();
                let (svg_single, _, _) = render_panel(&payload, &fonts, payload.width, payload.height, None);
            assert!(svg_single.contains(">Month<"), "single-chart draws the x title: {svg_single}");
            assert!(svg_single.contains(">Signups<"), "single-chart draws the y title: {svg_single}");
            let opts = FacetPanelOpts {
                panel_key: "",
                panel_ordinal: 0,
                suppress_x_axis: true,
                paint_value_axis: false,
                fold_axis_gutter: true,
                value_axis_right_overhang: 0.0,
                mirror_band_x: false,
                shared_domain_rows: &payload.rows,
                independent_scale: false,
            };
            let (svg_facet, _, _) = render_panel(&payload, &fonts, 480.0, 179.0, Some(&opts));
            assert!(!svg_facet.contains(">Month<"), "facet panel drops the x title: {svg_facet}");
            assert!(!svg_facet.contains(">Signups<"), "facet panel drops the y title: {svg_facet}");
        }

        // ── Small multiples: columns-only and grid line/area facets ────────

        /// A columns-only line facet payload — 2 column panels (East/West),
        /// same query shape as `make_facet_line_payload("shared")` with
        /// `rows`/`columns` swapped, `fixtures/multiples/09-line-columns-no-
        /// mirror`'s shape. `mirror: false` is EXPLICIT here (never the
        /// auto-default a real columns facet bakes).
        fn make_facet_line_payload_columns() -> serde_json::Value {
            let mut payload_json = make_facet_line_payload("shared");
            payload_json["resolved"]["multiples"] =
                json!({"rows": null, "columns": "region", "scale": "shared"});
            payload_json["resolved"]["panel_axes"] =
                json!([{"field": "region", "values": ["\"East\"", "\"West\""]}]);
            payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(false);
            payload_json["rows"] = json!([
                {"month": "Jan", "region": "East", "signups": 100.0},
                {"month": "Feb", "region": "East", "signups": 150.0},
                {"month": "Mar", "region": "East", "signups": 130.0},
                {"month": "Jan", "region": "West", "signups": 90.0},
                {"month": "Feb", "region": "West", "signups": 60.0},
                {"month": "Mar", "region": "West", "signups": 120.0},
            ]);
            payload_json
        }

        /// An authored `labels.angle` on a categorical facet x rotates the shared
        /// footer's labels, and the rotated band takes its room from the plot.
        #[test]
        fn an_authored_angle_on_a_nominal_facet_x_rotates_the_footer_and_shortens_the_plot() {
            // The x axis group seats at the plot's bottom edge.
            let axis_y = |svg: &str| -> f64 {
                let at = svg.find("data-dct-role=\"x-axis\"").expect("an x axis group");
                let tail = &svg[at + svg[at..].find("translate(").unwrap() + 10..];
                tail.split(')').next().unwrap().split(',').nth(1).unwrap().parse().unwrap()
            };
            let flat = render_facet_payload(make_facet_line_payload_columns()).expect("renders");
            let mut tilted = make_facet_line_payload_columns();
            tilted["resolved"]["style"]["axis_x"]["labels"]["angle"] = json!(45.0);
            let tilted = render_facet_payload(tilted).expect("renders");
            assert!(tilted.contains("rotate(45)"), "rotated footer labels: {tilted}");
            assert!(axis_y(&tilted) < axis_y(&flat), "{} vs {}", axis_y(&tilted), axis_y(&flat));
        }

        /// A row x column grid facet payload — 2 rows (region) x 2 columns
        /// (segment), mirroring `fixtures/multiples/11-line-grid-no-mirror`'s
        /// own query shape (`mirror: false` explicit, same as `make_facet_
        /// line_payload_columns`). `panel_axes` order is
        /// `[rows, columns]` — rows outer, columns inner — matching
        /// `regroup`'s own row-major cartesian-product convention
        /// (`multiples.rs::cartesian_product`'s own doc comment).
        fn make_facet_line_payload_grid() -> serde_json::Value {
            let mut payload_json = make_fit_line_payload();
            payload_json["resolved"]["style"]["axis_x"]["is_quantitative"] = json!(false);
            payload_json["resolved"]["multiples"] =
                json!({"rows": "region", "columns": "segment", "scale": "shared"});
            payload_json["resolved"]["panel_axes"] = json!([
                {"field": "region", "values": ["\"East\"", "\"West\""]},
                {"field": "segment", "values": ["\"Retail\"", "\"Wholesale\""]},
            ]);
            payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(false);
            payload_json["rows"] = json!([
                {"month": "Jan", "region": "East", "segment": "Retail", "signups": 1200.0},
                {"month": "Feb", "region": "East", "segment": "Retail", "signups": 1800.0},
                {"month": "Jan", "region": "East", "segment": "Wholesale", "signups": 900.0},
                {"month": "Feb", "region": "East", "segment": "Wholesale", "signups": 1400.0},
                {"month": "Jan", "region": "West", "segment": "Retail", "signups": 1100.0},
                {"month": "Feb", "region": "West", "segment": "Retail", "signups": 1600.0},
                {"month": "Jan", "region": "West", "segment": "Wholesale", "signups": 800.0},
                {"month": "Feb", "region": "West", "segment": "Wholesale", "signups": 1300.0},
            ]);
            payload_json["width"] = json!(600.0);
            payload_json["height"] = json!(432.0);
            payload_json["padding"] = json!(null);
            payload_json
        }

        /// The x of the first `<text>` whose content is `label`.
        fn first_text_x(svg: &str, label: &str) -> f64 {
            let at = svg
                .find(&format!(">{label}<"))
                .unwrap_or_else(|| panic!("no {label} label: {svg}"));
            let open = svg[..at].rfind("<text x=\"").expect("a text element opens first");
            svg[open + 9..].split('"').next().unwrap().parse().unwrap()
        }

        /// The x of the first panel's splice `<g transform="translate(x,y)">`.
        fn first_panel_x(svg: &str) -> f64 {
            let at = svg.find("<g transform=\"translate(").expect("a panel group");
            svg[at + 24..].split(',').next().unwrap().parse().unwrap()
        }

        /// A columns-only facet has no row title, so its left margin is the
        /// WIDER of the cell's own mark reach (`left_mark_overhang`, 4px at this
        /// stroke width) and the shared x axis's first label, which a point
        /// scale centers on the plot origin and so reaches `width / 2` past it
        /// (`viewSizeLayout`'s `ceil(-viewBounds.x1)` over the union of every
        /// role, `ViewLayout.js:163`/`:72-82` — probed in
        /// `test_vl_probe.py::test_columns_facet_left_margin_unions_the_first_
        /// footer_label`; the footer's 0.5 crisp translate is not part of the
        /// reach). The first panel's canvas starts `left_mark_overhang` short
        /// of that margin (`canvas_x`), and the footer's first label sits at the
        /// cell origin plus the 0.5 crisp translate.
        ///
        /// Mutation: dropping the `.max(footer_left_reach)` term from
        /// `compose_facet`'s `frame_left_extent` leaves every case at the
        /// 4px mark reach and fails "Jan" and "January" (`fixtures/multiples/
        /// 09-line-columns-no-mirror`: canvas 595 against production's 598).
        #[test]
        fn columns_only_line_facet_left_margin_unions_the_first_footer_label() {
            let fonts = test_fonts();
            let mark_reach = 4.0;
            for (label, wider_than_marks) in [("J", false), ("Jan", true), ("January", true)] {
                let mut payload_json = make_facet_line_payload_columns();
                for row in payload_json["rows"].as_array_mut().unwrap() {
                    if row["month"] == "Jan" {
                        row["month"] = json!(label);
                    }
                }
                let svg = render_facet_payload(payload_json).expect("renders");
                let half = fonts.measure_shaped(label, TEST_FONT, 11.0).unwrap() / 2.0;
                assert_eq!(half.ceil() > mark_reach, wider_than_marks, "{label}");
                let margin_left = half.ceil().max(mark_reach);
                assert_eq!(first_text_x(&svg, label), margin_left + 0.5, "{label}: footer label x");
                let splice_x = margin_left - mark_reach;
                assert_eq!(first_panel_x(&svg), splice_x, "{label}: panel splice x");
            }
        }

        /// A columns-only facet paints the shared value axis ONCE (real Vega's
        /// `row_footer`, at the row's last column), one header per column, and
        /// reserves the gutter once at the composer: the canvas lands where
        /// production's 09 does (`598×432` there; this payload's own fonts and
        /// padding give `433×400`).
        ///
        /// Mutation: `paint_value_axis: true` on every panel paints "60" twice.
        #[test]
        fn columns_only_line_facet_paints_one_value_axis_and_a_header_per_column() {
            let svg = render_facet_payload(make_facet_line_payload_columns()).expect("renders");
            let head = r#"<svg xmlns="http://www.w3.org/2000/svg" width="433" height="400""#;
            assert!(svg.starts_with(head), "{svg}");
            assert_eq!(svg.matches(">60<").count(), 1, "one value axis for the one row: {svg}");
            assert_eq!(svg.matches(">East<").count(), 1);
            assert_eq!(svg.matches(">West<").count(), 1);
            assert_eq!(svg.matches(">Jan<").count(), 2, "a footer label per column");
        }

        /// A row x column grid: the value axis is painted once per ROW (the last
        /// column's panel), a header per row and per column, and the gutter is
        /// NOT folded into any cell — column 1 sits `padding (20) + left reach +
        /// plot_w + right reach` right of column 0, the same stride
        /// `test_vl_probe.py::test_grid_facet_value_axis_stays_out_of_the_cell`
        /// pins on Vega. Here: splice x 34 -> 302 (268) against a 240px plot
        /// (the footer's Jan -> Feb labels, 38.5 -> 278.5) plus 20 plus the 4px
        /// reach on each side. The canvas is `fixtures/multiples/11-line-grid-
        /// no-mirror`'s shape: one overshoot correction takes the composite to
        /// the card (591 at this payload's fonts and no padding).
        ///
        /// Mutations: `fold_axis_gutter: true` on every panel widens each cell by
        /// the gutter, so the stride and canvas (the 614-against-601 miss on
        /// `11`) no longer match; `paint_value_axis: true` paints "1,000" four
        /// times.
        #[test]
        fn grid_line_facet_strides_by_the_cell_not_the_value_axis_gutter() {
            let svg = render_facet_payload(make_facet_line_payload_grid()).expect("renders");
            let head = r#"<svg xmlns="http://www.w3.org/2000/svg" width="591" height="432""#;
            assert!(svg.starts_with(head), "{svg}");
            let axes = svg.matches(">1,000<").count();
            assert_eq!(axes, 2, "one value axis per row, not per panel: {svg}");
            for header in ["East", "West", "Retail", "Wholesale"] {
                assert_eq!(svg.matches(&format!(">{header}<")).count(), 1, "{header}");
            }
            assert_eq!(svg.matches(">Jan<").count(), 2, "the shared footer paints once per column");
            let (col0, col1) = (first_panel_x(&svg), 302.0);
            assert!(svg.contains(r#"<g transform="translate(302.00,24.00)">"#), "{svg}");
            let plot_w = 278.5 - 38.5;
            let footer_origin = col0 + 4.0 + 0.5;
            assert_eq!(first_text_x(&svg, "Jan"), footer_origin, "canvas + reach + crisp");
            assert_eq!(col1 - col0, plot_w + 20.0 + 4.0 + 4.0, "{svg}");
        }

        /// The area counterpart: `render_inner` dispatches `multiples` to
        /// `render_facet` for both `draw_areas` values, so a grid area facet
        /// composes through the same arithmetic (`fixtures/multiples/10-area-
        /// grid-no-mirror`).
        #[test]
        fn grid_area_facet_renders_the_same_grid_as_the_line_one() {
            let payload: Payload = serde_json::from_value(make_facet_line_payload_grid()).unwrap();
            let fonts = test_fonts();
            let svg = render_inner(&payload, &fonts, true).expect("area grid facet renders");
            let head = r#"<svg xmlns="http://www.w3.org/2000/svg" width="591" height="432""#;
            assert!(svg.starts_with(head), "{svg}");
            assert_eq!(svg.matches(">1,000<").count(), 2, "{svg}");
        }

        /// The auto-mirrored default (`_apply_multiples_mirror`) is what a REAL
        /// columns facet ships with — `fixtures/multiples/07-line-columns` never
        /// authors `axis_y.mirror` itself. The mirror ghost (the shared value
        /// axis repeated on the left edge) joins the primary, so a label of the
        /// ladder paints twice for the one row, and an AREA columns facet takes
        /// the same band-position law (`08-area-grid`).
        #[test]
        fn columns_only_facet_with_the_auto_mirrored_default_paints_the_ghost() {
            for draw_areas in [false, true] {
                let mut payload_json = make_facet_line_payload_columns();
                payload_json["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
                let payload: Payload = serde_json::from_value(payload_json).unwrap();
                let fonts = test_fonts();
                let svg = render_inner(&payload, &fonts, draw_areas).expect("renders");
                let labels = svg.matches(">60<").count();
                assert_eq!(labels, 2, "primary + ghost, draw_areas={draw_areas}: {svg}");
            }
        }

        /// An AREA facet under `axis_y.mirror` takes the same band-position law
        /// as a line (`test_vl_probe.py::test_area_under_the_mirror_rule_lands_
        /// on_band_centers`): each panel's area vertices sit at the band centers
        /// `step * (i + 0.5)`, `step = plot_w / 3` (`plot_w` 140 once the ghost
        /// takes its gutter: 23.333, 70, 116.667), not the point scale's
        /// `i / (n - 1) * plot_w`. The unmirrored area facet keeps the point
        /// positions (0, 90, 180 at its 180px plot).
        ///
        /// Mutation: `mirror_band_x: has_mirror && !draw_areas` in
        /// `render_facet` leaves the area on the point scale and fails the
        /// mirrored assertions.
        #[test]
        fn area_columns_facet_under_mirror_renders_band_positions() {
            let fonts = test_fonts();
            let render = |mirror: bool| {
                let mut p = make_facet_line_payload_columns();
                p["resolved"]["style"]["axis_y"]["mirror"] = json!(mirror);
                let payload: Payload = serde_json::from_value(p).unwrap();
                render_inner(&payload, &fonts, true).expect("area columns facet renders")
            };
            let mirrored = render(true);
            for fill in [
                r#"d="M23.333,193.333L70,0L116.667,77.333L116.667,348L70,348L23.333,348Z""#,
                r#"d="M23.333,232L70,348L116.667,116L116.667,348L70,348L23.333,348Z""#,
            ] {
                assert!(mirrored.contains(fill), "band centers {fill}: {mirrored}");
            }
            let point = render(false);
            assert!(point.contains(r#"d="M0,193.333L90,0L180,77.333L180,348L90,348L0,348Z""#), "{point}");
        }

        /// A facet panel with an authored `axis_y.mirror: true` must never
        /// paint its own ghost — the composer owns it. Mutation:
        /// drop the `&& facet.is_none()` term off `mirror_active` and this
        /// facet-shaped call paints a second, self-drawn ghost axis.
        #[test]
        fn facet_opts_force_mirror_off_even_when_authored() {
            let mut p = make_fit_line_payload();
            p["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
            let payload: Payload = serde_json::from_value(p).unwrap();
            let fonts = test_fonts();
                let opts = FacetPanelOpts {
                panel_key: "",
                panel_ordinal: 0,
                suppress_x_axis: true,
                paint_value_axis: true,
                fold_axis_gutter: true,
                value_axis_right_overhang: 0.0,
                mirror_band_x: false,
                shared_domain_rows: &payload.rows,
                independent_scale: false,
            };
            let (svg_facet, _, _) = render_panel(&payload, &fonts, 480.0, 179.0, Some(&opts));
            let (svg_single, _, _) = render_panel(&payload, &fonts, payload.width, payload.height, None);
            let single_count = svg_single.matches("text-anchor=\"end\"").count();
            let facet_count = svg_facet.matches("text-anchor=\"end\"").count();
            assert!(
                single_count > facet_count,
                "the single-chart path paints a ghost (more end-anchored labels: {single_count}) \
                 that a facet panel must never paint on its own ({facet_count}): {svg_facet}"
            );
        }

        // ── Refusals ─────────────────────────────────────────────────────

        fn expect_refuses(mut payload_json: serde_json::Value, mutate: impl FnOnce(&mut serde_json::Value)) -> String {
            mutate(&mut payload_json);
            render_facet_payload(payload_json).expect_err("must refuse")
        }

        /// The title is hoisted onto the facet root: painted once by the
        /// composer, never once per panel.
        #[test]
        fn chart_level_title_paints_once_on_the_facet_root() {
            let mut p = make_facet_line_payload("shared");
            p["resolved"]["title"] = json!("Revenue");
            let svg = render_facet_payload(p).expect("titled facet renders");
            assert_eq!(svg.matches(">Revenue<").count(), 1, "{svg}");
        }

        /// A rows-only AREA facet renders through the same composer a
        /// rows-only LINE facet does — `render_inner` dispatches `multiples`
        /// to `render_facet` for both `draw_areas` values, and this arm has
        /// no area-specific refusal left (`grid_area_facet_renders`, above,
        /// covers the grid shape; this is the rows-only one).
        #[test]
        fn rows_only_area_facet_renders() {
            let payload: Payload = serde_json::from_value(make_facet_line_payload("shared")).unwrap();
            let fonts = test_fonts();
            let svg = render_inner(&payload, &fonts, true).expect("area facet renders");
            assert!(svg.matches(">North<").count() == 1 && svg.matches(">South<").count() == 1);
        }

        /// A stacked area facet shares one y scale: the union of every panel's
        /// own stack, so a panel that stacks lower still draws against the
        /// tallest panel's ladder.
        #[test]
        fn stacked_area_multiples_share_the_stack_extent() {
            let rows = |north: f64, south: f64| {
                let mut rows = Vec::new();
                for (region, v) in [("North", north), ("South", south)] {
                    for month in ["Jan", "Feb", "Mar"] {
                        rows.push(json!({"month": month, "region": region, "channel": "a", "signups": v}));
                        rows.push(json!({"month": month, "region": region, "channel": "b", "signups": v}));
                    }
                }
                rows
            };
            let mut p = make_facet_line_payload("shared");
            p["resolved"]["stack"] = json!("zero");
            p["resolved"]["color"] = json!("channel");
            p["resolved"]["legend"]["visible"] = json!(false);
            p["rows"] = json!(rows(100.0, 100.0));
            let even = render_facet_payload(p.clone()).expect("renders");
            p["rows"] = json!(rows(100.0, 300.0));
            let skewed = render_facet_payload(p).expect("renders");
            // North stacks to 200 in both renders; South's 600 must raise North's ladder too.
            assert!(!even.contains(">600<") && skewed.contains(">600<"), "{skewed}");
            let rows: Vec<serde_json::Map<String, serde_json::Value>> = serde_json::from_value(json!([
                {"x": "a", "s": "p", "y": 3.0}, {"x": "a", "s": "q", "y": 4.0},
                {"x": "b", "s": "p", "y": -2.0}, {"x": "b", "s": "q", "y": 1.0},
            ]))
            .unwrap();
            let data = crate::families::line::stack_data(&rows, "x", Some("s"), "y");
            let order = ["p".to_string(), "q".to_string()];
            let (a, b) = (crate::families::line::stack_column(&data, "a", &order, true), crate::families::line::stack_column(&data, "b", &order, true));
            assert_eq!((a.pos, a.neg, a.total), (7.0, 0.0, 7.0));
            assert_eq!((b.pos, b.neg, b.total), (1.0, -2.0, -1.0));
        }

        /// A series a panel lacks keeps its palette slot: the color domain is
        /// the chart's, so `web` paints in `palette[1]` in both panels and the
        /// legend lists both series.
        #[test]
        fn a_series_missing_from_one_panel_keeps_its_palette_slot() {
            let mut p = make_facet_line_payload("shared");
            p["resolved"]["color"] = json!("channel");
            p["resolved"]["palette"] = json!(["#aa0000", "#00bb00"]);
            p["resolved"]["legend"]["visible"] = json!(true);
            p["resolved"]["legend"]["direction"] = json!("horizontal");
            p["resolved"]["legend"]["position"] = json!({"edge": "top", "align": "start", "overlay": false});
            p["resolved"]["legend"]["label"]["padding"] = json!(8.0);
            p["resolved"]["legend"]["title"]["padding"] = json!(0.0);
            let render = |south: &[&str]| {
                let mut p = p.clone();
                let mut rows = Vec::new();
                for (region, channels) in [("North", &["store", "web"][..]), ("South", south)] {
                    for channel in channels {
                        for (month, v) in [("Jan", 100.0), ("Feb", 150.0), ("Mar", 130.0)] {
                            rows.push(json!({"month": month, "region": region, "channel": channel, "signups": v}));
                        }
                    }
                }
                p["rows"] = json!(rows);
                render_facet_payload(p).expect("renders")
            };
            let missing = render(&["web"]);
            let full = render(&["store", "web"]);
            assert!(missing.contains(">store<") && missing.contains(">web<"), "the legend lists both series");
            // `web` paints in slot 1 in both panels whether or not South has `store`.
            let web = |svg: &str| svg.matches("stroke=\"#00bb00\"").count();
            assert_eq!(web(&missing), web(&full));
        }

        /// A bottom legend hangs `offset` below the grid's ink where a top one
        /// hangs above it, so the canvas grows by the same amount either way.
        #[test]
        fn a_color_legend_on_the_bottom_edge_costs_what_a_top_one_does() {
            let canvas_height = |edge: &str| -> f64 {
                let mut p = make_facet_line_payload("shared");
                p["resolved"]["color"] = json!("channel");
                p["resolved"]["palette"] = json!(["#aa0000", "#00bb00"]);
                p["resolved"]["legend"]["visible"] = json!(true);
                p["resolved"]["legend"]["direction"] = json!("horizontal");
                p["resolved"]["legend"]["position"] = json!({"edge": edge, "align": "start", "overlay": false});
                p["resolved"]["legend"]["label"]["padding"] = json!(8.0);
                p["resolved"]["legend"]["title"]["padding"] = json!(0.0);
                let rows: Vec<_> = ["store", "web"]
                    .iter()
                    .flat_map(|channel| {
                        ["North", "South"].iter().flat_map(move |region| {
                            [("Jan", 100.0), ("Feb", 150.0), ("Mar", 130.0)]
                                .into_iter()
                                .map(move |(month, v)| json!({"month": month, "region": region, "channel": channel, "signups": v}))
                        })
                    })
                    .collect();
                p["rows"] = json!(rows);
                let svg = render_facet_payload(p).expect("renders");
                let at = svg.find(" height=\"").expect("canvas height") + 9;
                svg[at..at + svg[at..].find('"').expect("quote")].parse().expect("number")
            };
            let (bottom, top) = (canvas_height("bottom"), canvas_height("top"));
            assert_eq!(bottom, top, "bottom {bottom} top {top}");
        }

        /// A layer reading another query owns its dataset: both cells draw all of it.
        #[test]
        fn a_layer_reading_another_query_draws_all_of_it_in_every_panel() {
            let mut p = make_facet_line_payload("shared");
            let mut layer = super::line_layer_json(Some("other"));
            layer["y"] = json!("signups");
            p["resolved"]["layers"] = json!([layer]);
            p["datasets"] = json!({"other": [
                {"month": "Jan", "signups": 10.0},
                {"month": "Feb", "signups": 200.0},
                {"month": "Mar", "signups": 30.0},
            ]});
            let paths = |svg: &str| -> Vec<String> {
                svg.match_indices("<path d=\"M").map(|(i, _)| svg[i + 9..].split('"').next().expect("d").to_string()).collect()
            };
            // The layer's 200 joins the shared scale, so only its path tops the plot.
            let layer_paths: Vec<String> =
                paths(&render_facet_payload(p).expect("renders")).into_iter().filter(|d| d.contains("L180,0L")).collect();
            assert_eq!(layer_paths.len(), 2, "{layer_paths:?}");
            assert_eq!(layer_paths[0], layer_paths[1], "one dataset, drawn in each cell");
        }

        /// A band-step layer's doubled rows are inline data: each cell draws the
        /// whole chart's rows, not its panel's slice.
        #[test]
        fn a_band_step_layer_draws_every_panels_rows_in_each_cell() {
            let mut p = make_facet_line_payload("shared");
            let mut layer = super::line_layer_json(None);
            layer["y"] = json!("signups");
            layer["line_mark"]["curve"] = json!("step");
            p["resolved"]["layers"] = json!([layer]);
            let svg = render_facet_payload(p).expect("renders");
            // The doubled rows of all six datums make a path far longer than one panel's.
            let layer_paths: Vec<&str> = svg
                .match_indices("<path d=\"M")
                .map(|(i, _)| svg[i + 9..].split('"').next().expect("d"))
                .filter(|d| d.matches('L').count() > 12)
                .collect();
            assert_eq!(layer_paths.len(), 2, "{layer_paths:?}");
            assert_eq!(layer_paths[0], layer_paths[1], "both cells draw the same whole dataset");
        }

        #[test]
        fn a_layer_over_a_color_grouped_base_renders() {
            let mut p = make_facet_line_payload("shared");
            p["resolved"]["color"] = json!("channel");
            let mut layer = super::line_layer_json(None);
            layer["y"] = json!("signups");
            p["resolved"]["layers"] = json!([layer]);
            render_facet_payload(p).expect("renders");
        }

        #[test]
        fn a_layer_with_its_own_color_grouping_renders() {
            let mut p = make_facet_line_payload("shared");
            let mut layer = super::line_layer_json(None);
            layer["y"] = json!("signups");
            layer["color"] = json!("month");
            layer["ink"] = json!(null);
            let months: std::collections::BTreeSet<&str> = p["rows"].as_array().unwrap().iter().filter_map(|r| r["month"].as_str()).collect();
            layer["series_inks"] = months.iter().map(|m| (m.to_string(), json!("#779bc9"))).collect::<serde_json::Map<_, _>>().into();
            p["resolved"]["layers"] = json!([layer]);
            render_facet_payload(p).expect("renders");
        }

        fn dated_facet_payload() -> serde_json::Value {
            let mut p = make_facet_line_payload("shared");
            for row in p["rows"].as_array_mut().unwrap() {
                let month = match row["month"].as_str().unwrap() {
                    "Jan" => "2024-01-01",
                    "Feb" => "2024-02-01",
                    _ => "2024-03-01",
                };
                row["month"] = json!(month);
                row["target"] = json!(100.0);
            }
            p
        }

        /// A layer moves the base line off the facet's own dataset: over a temporal
        /// x each panel's base line walks every row of the chart, so both panels
        /// draw the same six-vertex path.
        #[test]
        fn a_layer_over_a_temporal_x_walks_every_row_in_each_panel() {
            let _locale = crate::time_locale::en_us_scope();
            let plain = render_facet_payload(dated_facet_payload()).expect("a dated facet renders without a layer");
            let mut layer = super::line_layer_json(None);
            layer["y"] = json!("target");
            let mut payload = dated_facet_payload();
            payload["resolved"]["layers"] = json!([layer]);
            let svg = render_facet_payload(payload).expect("a layer over a temporal x renders");
            let vertices = |svg: &str| -> Vec<usize> {
                svg.split("<path d=\"M").skip(1).map(|p| p.split('"').next().unwrap().matches('L').count() + 1).collect()
            };
            assert!(vertices(&plain).iter().all(|&n| n <= 3), "a panel's own rows draw three vertices");
            assert_eq!(vertices(&svg).iter().filter(|&&n| n == 6).count(), 4, "halo and stroke in each panel");
        }

        #[test]
        fn a_scatter_layer_renders() {
            let mut layer = super::scatter_layer_json();
            layer["y"] = json!("signups");
            layer["query_name"] = json!(null);
            let mut p = make_facet_line_payload("shared");
            p["resolved"]["layers"] = json!([layer]);
            render_facet_payload(p).expect("renders");
        }

        #[test]
        fn a_layer_with_value_labels_renders() {
            let mut p = make_facet_line_payload("shared");
            let mut layer = super::line_layer_json(None);
            layer["y"] = json!("signups");
            layer["line_mark"]["labels"]["visible"] = json!(true);
            p["resolved"]["layers"] = json!([layer]);
            render_facet_payload(p).expect("renders");
        }

        #[test]
        fn stack_zero_renders() {
            let mut p = make_facet_line_payload("shared");
            p["resolved"]["stack"] = json!("zero");
            render_facet_payload(p).expect("renders");
        }

        /// A left value axis is the composer's `row_header` copy: its labels sit left of every
        /// panel, one ladder per row, and the panels draw none on the right.
        #[test]
        fn left_value_axis_paints_in_the_row_header() {
            let mut p = make_facet_line_payload("shared");
            p["resolved"]["style"]["axis_y"]["position"] = json!("left");
            let svg = render_facet_payload(p).expect("renders");
            let axes: Vec<&str> = svg.split("data-dct-role=\"y-axis\"").skip(1).collect();
            assert!(!axes.is_empty(), "{svg}");
            for axis in axes {
                let axis = &axis[..axis.find("</g></g></g>").unwrap_or(axis.len())];
                let xs: Vec<f64> = axis
                    .split("<text x=\"")
                    .skip(1)
                    .filter_map(|t| t.split('"').next()?.parse().ok())
                    .collect();
                assert!(!xs.is_empty() && xs.iter().all(|x| *x < 100.0), "{xs:?}");
            }
        }

        /// A band-step panel doubles every row onto its band's two edges, per panel.
        #[test]
        fn curve_step_bands_each_panel() {
            let mut p = make_facet_line_payload("shared");
            p["resolved"]["style"]["line_mark"]["curve"] = json!("step");
            let svg = render_facet_payload(p).expect("a stepped facet renders");
            let ds: Vec<&str> = svg
                .match_indices("<path d=\"M")
                .map(|(i, _)| svg[i..].split("/>").next().unwrap())
                .filter(|t| t.contains("fill=\"none\"") && !t.contains("#ffffff"))
                .collect();
            assert_eq!(ds.len(), 2, "one path per panel: {svg}");
            for tag in ds {
                let d = tag.split("d=\"").nth(1).unwrap().split('"').next().unwrap();
                assert_eq!(distinct_xs(d).len(), 4, "three bands have four edges: {d}");
            }
        }

        /// The dash palette is a strokeDash encoding on the color field: a panel chart
        /// with no color field draws every line solid whatever `style.dashes` holds.
        #[test]
        fn dashes_without_a_color_field_draw_solid_lines() {
            let mut p = make_facet_line_payload("shared");
            p["resolved"]["style"]["dashes"] = json!([[4.0, 2.0]]);
            let svg = render_facet_payload(p).expect("renders");
            assert!(!svg.contains("stroke-dasharray"), "{svg}");
        }

        /// With a color field the dash palette binds to it in every panel.
        #[test]
        fn dashes_with_a_color_field_draw_dashed_series_in_every_panel() {
            let mut p = make_facet_line_payload("shared");
            p["resolved"]["style"]["dashes"] = json!([[4.0, 2.0], [2.0, 2.0]]);
            p["resolved"]["color"] = json!("channel");
            p["resolved"]["palette"] = json!(["#3164a3"]);
            let rows: Vec<serde_json::Value> = p["rows"]
                .as_array()
                .unwrap()
                .iter()
                .flat_map(|row| {
                    ["store", "web"].map(|channel| {
                        let mut r = row.clone();
                        r["channel"] = json!(channel);
                        r
                    })
                })
                .collect();
            p["rows"] = json!(rows);
            let svg = render_facet_payload(p).expect("renders");
            assert!(svg.matches("stroke-dasharray=\"4,2\"").count() >= 2, "{svg}");
            assert!(svg.contains("stroke-dasharray=\"2,2\""), "{svg}");
        }

        /// A panel prints its own rows' captions.
        #[test]
        fn value_labels_print_in_every_panel() {
            let mut p = make_facet_line_payload("shared");
            p["resolved"]["style"]["line_mark"]["labels"] = json!({"visible": true});
            let svg = render_facet_payload(p).expect("renders");
            assert!(svg.contains(">120</text>"), "{svg}");
        }

        /// `axis_y.mirror: true` renders on a ROWS-ONLY facet — the mirror
        /// ghost's own invisible interaction rule flips Vega's shared
        /// nominal x scale from point to band regardless of facet shape,
        /// and `line::layout::fit`'s own `mirror_wants_band`/`use_band_
        /// positions` port that law per panel (`FacetPanelOpts.mirror_
        /// band_x`, set by `render_facet` off `has_mirror`); forcing
        /// `mirror: true` onto `fixtures/multiples/06-line-rows`'s own
        /// (otherwise-unaffected) shape is exactly how that law was probed
        /// against a live scenegraph. Pins both panels' own vertex
        /// positions (band centers, `step*(i+0.5)`, `step = 280/3`) and the
        /// shared footer axis's own tick positions (`floor(center) + 0.5`). The expected `d`
        /// strings are those band centers (46.6666.., 233.3333.. and the panel's y values)
        /// printed through d3-path's `Math.round(v * 1e3) / 1e3`.
        #[test]
        fn mirror_true_on_rows_only_renders_band_positions() {
            let svg = render_facet_payload({
                let mut p = make_facet_line_payload("shared");
                p["resolved"]["style"]["axis_y"]["mirror"] = json!(true);
                p
            })
            .expect("rows-only facet with mirror renders band positions");
            assert!(
                svg.contains("d=\"M46.667,95.556L140,0L233.333,38.222\""),
                "North's own vertices at band centers, step*(i+0.5), step=280/3: {svg}"
            );
            assert!(
                svg.contains("d=\"M46.667,114.667L140,172L233.333,57.333\""),
                "South's own vertices at the SAME band centers: {svg}"
            );
            assert!(svg.contains("x1=\"133.500\""), "shared footer tick, floor(center)+0.5: {svg}");
            assert!(svg.contains("x1=\"227.500\""), "{svg}");
            assert!(svg.contains("x1=\"320.500\""), "{svg}");
            assert!(svg.matches(">North<").count() == 1 && svg.matches(">South<").count() == 1);
        }

        /// A ghost `format` relabels only the composer's shared ghost: the panels'
        /// primary ladder keeps its own labels.
        #[test]
        fn mirror_object_form_relabels_the_ghost() {
            let mut p = make_facet_line_payload("shared");
            p["resolved"]["style"]["axis_y"]["mirror"] = json!({"format": "$,.0f"});
            let svg = render_facet_payload(p).expect("renders");
            assert!(svg.contains(">$100<"), "the ghost paints its own format: {svg}");
            assert!(svg.contains(">100<"), "the primary keeps its own: {svg}");
        }

        /// An independent scale fits each panel's own y extent and ladder: the two
        /// rows' value axes read differently, where a shared one repeats a ladder.
        #[test]
        fn scale_independent_gives_each_panel_its_own_y_ladder() {
            let ladders = |scale: &str| -> Vec<Vec<String>> {
                let svg = render_facet_payload(make_facet_line_payload(scale)).expect("renders");
                svg.split("data-dct-role=\"y-axis\"")
                    .skip(1)
                    .map(|axis| {
                        let axis = &axis[..axis.find("</g></g></g>").unwrap_or(axis.len())];
                        axis.split("text-anchor=\"end\"")
                            .skip(1)
                            .filter_map(|t| t.split_once('>').and_then(|(_, rest)| rest.split_once('<')).map(|(l, _)| l.to_string()))
                            .collect()
                    })
                    .collect()
            };
            let independent = ladders("independent");
            assert_eq!(independent.len(), 2, "{independent:?}");
            assert_ne!(independent[0], independent[1], "each panel its own ladder");
            let shared = ladders("shared");
            assert_eq!(shared[0], shared[1], "a shared scale repeats one ladder");
        }

        /// A baked `panel_axes` value no row names has no header: the composer
        /// reports the mismatch rather than label a track it cannot place.
        #[test]
        fn a_baked_panel_value_without_rows_is_an_invariant_error() {
            let err = expect_refuses(make_facet_line_payload("shared"), |p| {
                p["resolved"]["panel_axes"] = json!([
                    {"field": "region", "values": ["\"North\"", "\"South\"", "\"West\""]},
                ]);
            });
            assert_eq!(err, "line: multiples axis \"region\" has 2 populated values for a 3-track grid");
        }

    }

    mod torn_baseline {
        use super::*;
        use serde_json::json;

        fn torn_payload(mutate: impl FnOnce(&mut serde_json::Value)) -> Payload {
            fit_payload(|j| {
                j["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(true);
                j["resolved"]["style"]["axis_y"]["grid"]["torn_baseline"] =
                    json!({"visible": true, "amplitude": 4.0, "wavelength": 14.0, "sharpness": 0.0});
                mutate(j);
            })
        }

        fn tear(svg: &str) -> Option<String> {
            let i = svg.find("class=\"torn-baseline\"")?;
            let start = svg[..i].rfind("<path").unwrap();
            Some(svg[start..].split("/>").next().unwrap().to_string())
        }

        /// The wave's resting y: the `M` point sits on the edge.
        fn tear_edge(tag: &str) -> f64 {
            path_numbers(tag)[1]
        }

        /// Every number in the tear's `d`: the `M` point, then six per `C`.
        fn path_numbers(tag: &str) -> Vec<f64> {
            let d = tag.split("d=\"").nth(1).unwrap().split('"').next().unwrap();
            d.split(['M', 'C', ',', ' ']).filter(|t| !t.is_empty()).map(|t| t.parse().unwrap()).collect()
        }

        fn tear_ys(tag: &str) -> Vec<f64> {
            path_numbers(tag).iter().skip(1).step_by(2).copied().collect()
        }

        /// The y of every full-width horizontal gridline.
        fn gridline_ys(svg: &str) -> Vec<f64> {
            plot_lines(svg).into_iter().filter(|l| l.1 == l.3 && l.2 - l.0 > 100.0).map(|l| l.1).collect()
        }

        #[test]
        fn positive_data_tears_the_bottom_edge_instead_of_its_gridline() {
            let plain = render(&fit_payload(|j| {
                j["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(true);
            }), &test_fonts()).expect("renders");
            let bottom = gridline_ys(&plain).into_iter().fold(f64::MIN, f64::max);
            let svg = render(&torn_payload(|_| {}), &test_fonts()).expect("renders");
            let tag = tear(&svg).unwrap_or_else(|| panic!("no tear: {svg}"));
            assert_eq!(tear_edge(&tag), bottom, "{tag}");
            assert!(!gridline_ys(&svg).contains(&bottom), "the straight bottom gridline is replaced: {svg}");
            assert_eq!(gridline_ys(&svg).len(), gridline_ys(&plain).len() - 1, "{svg}");
        }

        #[test]
        fn negative_data_tears_the_top_edge() {
            let svg = render(
                &torn_payload(|j| {
                    for (row, v) in j["rows"].as_array_mut().unwrap().iter_mut().zip([-100.0, -150.0, -130.0]) {
                        row["signups"] = json!(v);
                    }
                }),
                &test_fonts(),
            )
            .expect("renders");
            let tag = tear(&svg).unwrap_or_else(|| panic!("no tear: {svg}"));
            let ys = gridline_ys(&svg);
            assert!(ys.iter().all(|&y| y > tear_edge(&tag)), "tear above every straight gridline: {svg}");
            assert_eq!(ys.len(), 5, "the top gridline (−100) is replaced by the tear: {svg}");
        }

        #[test]
        fn a_descending_positive_domain_tears_the_top_edge() {
            let svg = render(
                &torn_payload(|j| {
                    j["resolved"]["style"]["axis_y"]["scale"] = json!({"continuous": {"domain": [200.0, 50.0]}});
                }),
                &test_fonts(),
            )
            .expect("renders");
            let tag = tear(&svg).unwrap_or_else(|| panic!("no tear: {svg}"));
            assert!(gridline_ys(&svg).iter().all(|&y| y > tear_edge(&tag)), "50 sits at the top: {svg}");
        }

        #[test]
        fn the_tear_draws_with_the_grid_hidden() {
            let svg = render(
                &torn_payload(|j| j["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(false)),
                &test_fonts(),
            )
            .expect("renders");
            let tag = tear(&svg).unwrap_or_else(|| panic!("no tear: {svg}"));
            assert!(tag.contains("stroke=\"#D0D3D9\""), "the tear is stroked: {tag}");
            assert!(gridline_ys(&svg).is_empty(), "{svg}");
        }

        #[test]
        fn a_domain_that_includes_zero_has_no_tear() {
            let svg = render(
                &torn_payload(|j| {
                    j["resolved"]["style"]["axis_y"]["scale"] = json!({"continuous": {"domain": [0.0, 200.0]}});
                }),
                &test_fonts(),
            )
            .expect("renders");
            assert!(tear(&svg).is_none(), "{svg}");
        }

        #[test]
        fn off_by_default() {
            let svg = render(
                &fit_payload(|j| j["resolved"]["style"]["axis_y"]["grid"]["visible"] = json!(true)),
                &test_fonts(),
            )
            .expect("renders");
            assert!(tear(&svg).is_none(), "{svg}");
        }

        #[test]
        fn visible_false_draws_no_tear() {
            let svg = render(
                &torn_payload(|j| j["resolved"]["style"]["axis_y"]["grid"]["torn_baseline"]["visible"] = json!(false)),
                &test_fonts(),
            )
            .expect("renders");
            assert!(tear(&svg).is_none(), "{svg}");
        }

        #[test]
        fn the_wave_stays_inside_the_plot_at_the_bottom_edge() {
            let svg = render(&torn_payload(|_| {}), &test_fonts()).expect("renders");
            let tag = tear(&svg).unwrap();
            let edge = tear_edge(&tag);
            let ys = tear_ys(&tag);
            assert!(ys.iter().all(|&y| y <= edge + 1e-9 && y >= edge - 4.0 - 1e-9), "{tag}");
            assert_eq!(*ys.last().unwrap(), edge, "ends on the edge: {tag}");
        }

        #[test]
        fn the_wave_stays_inside_the_plot_at_the_top_edge() {
            let svg = render(
                &torn_payload(|j| {
                    for (row, v) in j["rows"].as_array_mut().unwrap().iter_mut().zip([-100.0, -150.0, -130.0]) {
                        row["signups"] = json!(v);
                    }
                }),
                &test_fonts(),
            )
            .expect("renders");
            let tag = tear(&svg).unwrap();
            let edge = tear_edge(&tag);
            assert!(tear_ys(&tag).iter().all(|&y| y >= edge - 1e-9 && y <= edge + 4.0 + 1e-9), "{tag}");
        }

        #[test]
        fn amplitude_sets_the_crest_height() {
            let svg = render(
                &torn_payload(|j| j["resolved"]["style"]["axis_y"]["grid"]["torn_baseline"]["amplitude"] = json!(7.0)),
                &test_fonts(),
            )
            .expect("renders");
            let tag = tear(&svg).unwrap();
            let edge = tear_edge(&tag);
            let crest = tear_ys(&tag).iter().fold(edge, |m, &y| m.min(y));
            assert!((edge - crest - 7.0).abs() < 0.01, "crest {crest} vs edge {edge}: {tag}");
        }

        #[test]
        fn sharpness_one_is_a_zigzag() {
            let svg = render(
                &torn_payload(|j| j["resolved"]["style"]["axis_y"]["grid"]["torn_baseline"]["sharpness"] = json!(1.0)),
                &test_fonts(),
            )
            .expect("renders");
            let n = path_numbers(&tear(&svg).unwrap());
            let mut a = (n[0], n[1]);
            for seg in n[2..].chunks(6) {
                let b = (seg[4], seg[5]);
                for c in [(seg[0], seg[1]), (seg[2], seg[3])] {
                    let cross = (b.0 - a.0) * (c.1 - a.1) - (b.1 - a.1) * (c.0 - a.0);
                    assert!(cross.abs() < 0.05, "control {c:?} off the chord {a:?}->{b:?}");
                }
                a = b;
            }
        }

        #[test]
        fn sharpness_zero_is_not_a_zigzag() {
            let svg = render(&torn_payload(|_| {}), &test_fonts()).expect("renders");
            let n = path_numbers(&tear(&svg).unwrap());
            let (a, seg) = ((n[0], n[1]), &n[2..8]);
            let cross = (seg[4] - a.0) * (seg[1] - a.1) - (seg[5] - a.1) * (seg[0] - a.0);
            assert!(cross.abs() > 0.5, "a sine quarter bows off its chord");
        }

        fn torn_grid_payload(mutate: impl FnOnce(&mut serde_json::Value)) -> Payload {
            torn_payload(|j| {
                j["resolved"]["style"]["axis_x"]["grid"]["visible"] = json!(true);
                mutate(j);
            })
        }

        fn tear_key(j: &mut serde_json::Value, key: &str, value: f64) {
            j["resolved"]["style"]["axis_y"]["grid"]["torn_baseline"][key] = json!(value);
        }

        /// x of every vertical gridline in the plot group.
        fn x_gridline_xs(svg: &str) -> Vec<f64> {
            plot_lines(svg).into_iter().filter(|l| l.0 == l.2 && l.3 - l.1 > 100.0).map(|l| l.0).collect()
        }

        /// The x of every point where the tear rests on its edge.
        fn tear_trough_xs(tag: &str) -> Vec<f64> {
            let n = path_numbers(tag);
            let edge = n[1];
            std::iter::once((n[0], n[1]))
                .chain(n[2..].chunks(6).map(|s| (s[4], s[5])))
                .filter(|p| (p.1 - edge).abs() < 1e-6)
                .map(|p| p.0)
                .collect()
        }

        #[test]
        fn troughs_land_on_the_x_gridlines() {
            let svg = render(&torn_grid_payload(|j| tear_key(j, "wavelength", 30.0)), &test_fonts()).expect("renders");
            let xs = x_gridline_xs(&svg);
            let troughs = tear_trough_xs(&tear(&svg).unwrap());
            assert!(xs.len() >= 2, "{svg}");
            for x in xs {
                assert!(troughs.iter().any(|t| (t - x).abs() < 0.01), "no trough on gridline {x}: {troughs:?}");
            }
        }

        /// A tear drawn at `wavelength` over `rows` daily points.
        fn rhythm_svg(rows: usize, wavelength: f64) -> String {
            render(
                &torn_grid_payload(|j| {
                    tear_key(j, "wavelength", wavelength);
                    let daily: Vec<_> = (0..rows)
                        .map(|i| json!({"week": format!("2026-01-{:02}", 1 + i), "signups": 100.0 + 5.0 * i as f64}))
                        .collect();
                    j["rows"] = json!(daily);
                }),
                &test_fonts(),
            )
            .expect("renders")
        }

        /// Gridline xs, trough xs and the tear tag of `rhythm_svg`.
        fn rhythm(rows: usize, wavelength: f64) -> (Vec<f64>, Vec<f64>, String) {
            let svg = rhythm_svg(rows, wavelength);
            let tag = tear(&svg).unwrap();
            (x_gridline_xs(&svg), tear_trough_xs(&tag), tag)
        }

        /// The period, in px, of `gap` times a span or 1/subdivision that lands
        /// closest to `wavelength` in log space.
        fn nearest_period(gap: f64, wavelength: f64) -> f64 {
            let mut options: Vec<f64> = (1..=64).flat_map(|n| [gap / n as f64, gap * n as f64]).collect();
            options.sort_by(|a, b| (a / wavelength).ln().abs().total_cmp(&(b / wavelength).ln().abs()));
            options[0]
        }

        #[test]
        fn one_period_per_column_when_the_wavelength_matches_the_gap() {
            let (xs, troughs, _) = rhythm(3, 267.0);
            let gap = xs[1] - xs[0];
            assert!(troughs.iter().filter(|&&t| t >= xs[0] - 0.01 && t <= xs[1] + 0.01).count() == 2, "{troughs:?} {xs:?} {gap}");
        }

        #[test]
        fn a_column_is_subdivided_when_that_lands_nearer_the_wavelength() {
            // gap/wavelength ≈ 1.45: two teeth per column (0.73×) beat one (1.45×) in log space.
            let (xs, troughs, _) = rhythm(3, 184.0);
            let inside = troughs.iter().filter(|&&t| t >= xs[0] - 0.01 && t <= xs[1] + 0.01).count();
            assert_eq!(inside, 3, "{troughs:?} vs {xs:?}");
        }

        #[test]
        fn narrow_columns_span_several_gridlines_instead_of_cramming_teeth() {
            let wavelength = 240.0;
            let (xs, troughs, tag) = rhythm(10, wavelength);
            let gap = (xs[xs.len() - 1] - xs[0]) / (xs.len() - 1) as f64;
            let period = nearest_period(gap, wavelength);
            assert!(gap < wavelength / 2.0, "the column must be narrow for this to mean anything: {gap}");
            assert!((troughs[1] - troughs[0] - period).abs() < 1.0, "period {period} from gap {gap}: {troughs:?}");
            assert!((period / wavelength).ln().abs() < 2f64.sqrt().ln() + 1e-9, "{period} vs {wavelength}");
            let x2 = tear_vertices(&tag).last().unwrap().0;
            for t in &troughs {
                assert!(xs.iter().any(|x| (x - t).abs() < 0.01) || (t - x2).abs() < 0.01, "trough {t} off the grid: {xs:?}");
            }
            let edge = tear_edge(&tag);
            assert_eq!(*tear_ys(&tag).last().unwrap(), edge, "the leftover span still rests on the edge: {tag}");
        }

        /// Every `(x, y)` the tear passes through at a cubic boundary.
        fn tear_vertices(tag: &str) -> Vec<(f64, f64)> {
            let n = path_numbers(tag);
            std::iter::once((n[0], n[1])).chain(n[2..].chunks(6).map(|s| (s[4], s[5]))).collect()
        }

        fn assert_gridlines_end_on_the_tear(svg: &str) {
            let tag = tear(svg).unwrap();
            let vertices = tear_vertices(&tag);
            let verticals: Vec<_> = plot_lines(svg).into_iter().filter(|l| l.0 == l.2 && l.3 - l.1 > 100.0).collect();
            assert!(!verticals.is_empty(), "{svg}");
            for l in verticals {
                let v = vertices.iter().find(|v| (v.0 - l.0).abs() < 0.01).unwrap_or_else(|| panic!("no tear vertex at {l:?}"));
                assert!((l.3 - v.1).abs() < 0.01, "gridline {l:?} should end on the tear at y {}", v.1);
            }
        }

        #[test]
        fn a_gridline_mid_tooth_ends_on_the_wave_where_it_crosses_it() {
            // 10 daily points at wavelength 240 span 4 columns: the tear's stops
            // are every 4th gridline, and the trailing stretch is uneven, so most
            // gridlines fall inside a tooth rather than on a trough.
            let svg = rhythm_svg(10, 240.0);
            let tag = tear(&svg).unwrap();
            let n = path_numbers(&tag);
            let edge = tear_edge(&tag);
            let vertices = tear_vertices(&tag);
            let cubics: Vec<[(f64, f64); 4]> = n[2..]
                .chunks(6)
                .scan((n[0], n[1]), |from, c| {
                    let cubic = [*from, (c[0], c[1]), (c[2], c[3]), (c[4], c[5])];
                    *from = (c[4], c[5]);
                    Some(cubic)
                })
                .collect();
            // The wave's y at `x`, by dense sampling of the cubic that spans it.
            let wave_y = |x: f64| {
                let c = cubics.iter().find(|c| c[0].0 <= x && x <= c[3].0).unwrap();
                (0..=100_000)
                    .map(|i| {
                        let (t, u) = (i as f64 / 100_000.0, 1.0 - i as f64 / 100_000.0);
                        let at = |f: fn(&(f64, f64)) -> f64| {
                            u * u * u * f(&c[0]) + 3.0 * u * u * t * f(&c[1]) + 3.0 * u * t * t * f(&c[2]) + t * t * t * f(&c[3])
                        };
                        (at(|p| p.0), at(|p| p.1))
                    })
                    .min_by(|a, b| (a.0 - x).abs().total_cmp(&(b.0 - x).abs()))
                    .unwrap()
                    .1
            };
            let verticals: Vec<_> = plot_lines(&svg).into_iter().filter(|l| l.0 == l.2 && l.3 - l.1 > 100.0).collect();
            let mid_tooth: Vec<_> = verticals.iter().filter(|l| !vertices.iter().any(|v| (v.0 - l.0).abs() < 0.01)).collect();
            assert!(mid_tooth.len() >= 3, "stops every 4th gridline leave several gridlines off a trough: {verticals:?}");
            let flank = mid_tooth.iter().filter(|l| l.3 > edge - 4.0 + 0.1).count();
            assert!(flank >= 2, "gridlines on a flank stop short of the crest: {mid_tooth:?}");
            for l in mid_tooth {
                assert!(l.3 >= edge - 4.0 - 0.01 && l.3 < edge, "{l:?} must lie on the tooth, edge {edge}");
                assert!((l.3 - wave_y(l.0)).abs() < 0.02, "{l:?} should end at the wave's y {}", wave_y(l.0));
            }
        }

        /// The x of every cubic start that rests on `edge`.
        fn trough_xs(cubics: &[crate::torn::Cubic], edge: f64) -> Vec<f64> {
            cubics.iter().filter(|c| (c[0].1 - edge).abs() < 1e-9).map(|c| c[0].0).chain(cubics.last().map(|c| c[3].0)).collect()
        }

        #[test]
        fn no_tooth_is_squeezed_beside_a_plot_end_whatever_the_fractional_width() {
            // Regular 100 px columns at wavelength 100: a tick `lead` px from an
            // end leaves a stretch that is a fraction of a period, so it either
            // carries a tooth of at least period/√2 or no stop at all.
            let tear = crate::common::TornBaseline { visible: true, amplitude: 4.0, wavelength: 100.0, sharpness: 1.0 };
            for lead in [5.0, 30.0, 51.0, 56.0, 60.0, 69.0, 71.0, 120.0] {
                let ticks = [lead, lead + 100.0, lead + 200.0];
                let x2 = ticks[2] + lead;
                let widths: Vec<f64> = trough_xs(&crate::torn::torn_cubics(0.0, x2, 0.0, -1.0, &ticks, &tear), 0.0)
                    .windows(2)
                    .map(|w| w[1] - w[0])
                    .collect();
                assert!(widths.iter().all(|&w| w > 70.0), "lead {lead}: {widths:?}");
            }
        }

        #[test]
        fn band_step_edges_do_not_squeeze_a_whole_tooth_into_half_a_column() {
            // Band-step ticks sit half a step inside the plot, so the first and
            // last stretches are half an interior period wide.
            let mut j = make_band_step_line_payload(None);
            j["resolved"]["style"]["axis_x"]["grid"] =
                json!({"visible": true, "threshold": null, "width": 1.0, "color": "#EDEFF2", "opacity": 1.0});
            j["resolved"]["style"]["axis_y"]["grid"] = json!({
                "visible": true, "threshold": null, "width": 1.0, "color": "#D0D3D9", "opacity": 1.0,
                "torn_baseline": {"visible": true, "amplitude": 4.0, "wavelength": 170.0, "sharpness": 1.0}
            });
            j["resolved"]["style"]["axis_y"]["scale"] = json!({"continuous": {"domain": [50.0, 200.0]}});
            let svg = render(&serde_json::from_value(j).unwrap(), &test_fonts()).expect("renders");
            let troughs = tear_trough_xs(&tear(&svg).unwrap());
            let widths: Vec<f64> = troughs.windows(2).map(|w| w[1] - w[0]).collect();
            // A tooth never drops below period/√2: ~126 px of this fixture's ~179 px column.
            assert!(widths.iter().all(|&w| w > 125.0), "no squeezed tooth: {troughs:?}");
        }

        #[test]
        fn x_gridlines_end_on_the_tear_at_their_own_x() {
            let svg = render(&torn_grid_payload(|j| tear_key(j, "wavelength", 30.0)), &test_fonts()).expect("renders");
            assert_gridlines_end_on_the_tear(&svg);
        }

        #[test]
        fn x_gridlines_end_on_a_top_edge_tear() {
            let svg = render(
                &torn_grid_payload(|j| {
                    j["resolved"]["style"]["axis_y"]["scale"] = json!({"continuous": {"domain": [200.0, 50.0]}});
                }),
                &test_fonts(),
            )
            .expect("renders");
            let tag = tear(&svg).unwrap();
            let vertices = tear_vertices(&tag);
            let verticals: Vec<_> = plot_lines(&svg).into_iter().filter(|l| l.0 == l.2 && l.3 - l.1 > 100.0).collect();
            assert!(!verticals.is_empty(), "{svg}");
            for l in verticals {
                let v = vertices.iter().find(|v| (v.0 - l.0).abs() < 0.01).unwrap();
                assert!((l.1 - v.1).abs() < 0.01, "gridline {l:?} should start on the tear at y {}", v.1);
            }
        }

        #[test]
        fn x_gridlines_run_to_the_edge_without_a_tear() {
            let svg = render(
                &torn_grid_payload(|j| j["resolved"]["style"]["axis_y"]["grid"]["torn_baseline"]["visible"] = json!(false)),
                &test_fonts(),
            )
            .expect("renders");
            let bottom = gridline_ys(&svg).into_iter().fold(f64::MIN, f64::max);
            let verticals: Vec<_> = plot_lines(&svg).into_iter().filter(|l| l.0 == l.2 && l.3 - l.1 > 100.0).collect();
            assert!(!verticals.is_empty(), "{svg}");
            for l in verticals {
                assert_eq!(l.3, bottom, "{l:?}");
            }
        }

        fn x_grid(j: &mut serde_json::Value, key: &str, value: serde_json::Value) {
            j["resolved"]["style"]["axis_x"]["grid"][key] = value;
        }

        #[test]
        fn the_tear_strokes_with_the_x_grid_ink() {
            for visible in [true, false] {
                let svg = render(
                    &torn_payload(|j| {
                        x_grid(j, "color", json!("#112233"));
                        x_grid(j, "width", json!(2.5));
                        x_grid(j, "visible", json!(visible));
                    }),
                    &test_fonts(),
                )
                .expect("renders");
                let tag = tear(&svg).unwrap();
                assert!(tag.contains("stroke=\"#112233\"") && tag.contains("stroke-width=\"2.5\""), "visible={visible}: {tag}");
            }
        }

        #[test]
        fn drawn_x_gridlines_with_no_resolved_ink_are_refused() {
            let err = render(
                &torn_payload(|j| {
                    x_grid(j, "visible", json!(true));
                    x_grid(j, "color", json!(null));
                    j["resolved"]["style"]["axis_y"]["grid"]["torn_baseline"]["visible"] = json!(false);
                }),
                &test_fonts(),
            )
            .expect_err("no ink");
            assert!(err.contains("axis_x.grid"), "{err}");
        }

        #[test]
        fn a_tear_with_no_resolved_x_grid_ink_is_refused() {
            let err = render(&torn_payload(|j| x_grid(j, "color", json!(null))), &test_fonts()).expect_err("no ink");
            assert!(err.contains("axis_x.grid.color"), "{err}");
        }

        #[test]
        fn an_area_chart_ignores_it() {
            let _en = crate::time_locale::en_us_scope();
            let svg = crate::families::area::render(&torn_payload(|_| {}), &test_fonts()).expect("renders");
            assert!(tear(&svg).is_none(), "{svg}");
        }
    }

    // ── A resolved format's affix on the value axis and the tooltip ──────

    fn affixed_line_payload(slot: serde_json::Value, tooltip: bool) -> Payload {
        use serde_json::json;
        fit_payload(|j| {
            let ay = &mut j["resolved"]["style"]["axis_y"];
            ay["tick_label"] = json!(null);
            ay["tick_values"] = json!([0.0, 50.0, 100.0, 150.0, 200.0]);
            ay["domain_min"] = json!(0.0);
            ay["domain_max"] = json!(200.0);
            ay["grid"]["visible"] = json!(true);
            if tooltip {
                j["resolved"]["style"]["tooltip_format"] = slot;
            } else {
                ay["labels"]["format"] = slot;
            }
        })
    }

    /// The plain value axis, as `compose_axis_format` paints it: the zero tick
    /// bare, every other tick wearing the affix. Oracle:
    /// `quantitative_tick_labels((0, 50, 100, 150, 200),
    /// ResolvedFormat(spec=",.0f", prefix="US$"), None, None)` is `['0',
    /// 'US$50', 'US$100', 'US$150', 'US$200']`; with `suffix=" CHF"`, `['0',
    /// '50 CHF', '100 CHF', '150 CHF', '200 CHF']`.
    #[test]
    fn a_plain_value_axis_paints_its_affix_and_leaves_the_zero_tick_bare() {
        use serde_json::json;
        for (slot, ticks) in [
            (json!({"spec": ",.0f", "prefix": "US$", "raw": "integer"}), ["US$50", "US$100", "US$150", "US$200"]),
            (json!({"spec": ",.0f", "suffix": " CHF", "raw": "integer"}), ["50 CHF", "100 CHF", "150 CHF", "200 CHF"]),
        ] {
            let svg = render(&affixed_line_payload(slot, false), &test_fonts()).expect("renders");
            for tick in ticks {
                assert!(svg.contains(&format!(">{tick}<")), "{tick}: {svg}");
            }
            assert!(svg.contains(">0<"), "the zero tick stays bare: {svg}");
            assert!(!svg.contains(">US$0<") && !svg.contains(">0 CHF<"), "{svg}");
        }
    }

    /// The right-hand label gutter reserves the affixed strings' width, so the
    /// plot narrows by as much as `US$200` outgrows `200`.
    #[test]
    fn the_value_axis_gutter_measures_the_affixed_labels() {
        use serde_json::json;
        let plot_w = |slot: serde_json::Value| {
            rail_plot_w(&render(&affixed_line_payload(slot, false), &test_fonts()).expect("renders"))
        };
        let bare = plot_w(json!({"spec": ",.0f", "raw": "integer"}));
        let affixed = plot_w(json!({"spec": ",.0f", "prefix": "US$", "raw": "integer"}));
        let fonts = test_fonts();
        let family = measure::stand_in_family(TEST_FONT);
        let widened = fonts.measure("US$200", family, 11.0).unwrap() - fonts.measure("200", family, 11.0).unwrap();
        assert!(((bare - affixed) - widened).abs() <= 1.0, "plot narrowed {} not {widened}", bare - affixed);
    }

    /// Oracle: `format_d3(150, ",.0f", "US$", "", None, placement)` is
    /// `'US$150'`; with `suffix=" CHF"`, `'150 CHF'`. The zero rule's own
    /// description stays the encoding's bare spec (`"format":
    /// tooltip_format.spec`, `_cartesian.py:1689`), which the corpus reference
    /// pins as `delta: 0`.
    #[test]
    fn a_tooltip_row_paints_its_affix_and_the_zero_rule_stays_hidden() {
        use serde_json::json;
        for (slot, shown) in [
            (json!({"spec": ",.0f", "prefix": "US$", "raw": "integer"}), "US$150"),
            (json!({"spec": ",.0f", "suffix": " CHF", "raw": "integer"}), "150 CHF"),
        ] {
            let mut p = affixed_line_payload(slot, true);
            p.resolved["style"]["axis_y"]["grid"]["visible"] = json!(true);
            p.resolved["style"]["axis_y"]["grid"]["threshold"] = json!({"color": "#8A8C8F", "width": 2.0});
            let svg = render(&p, &test_fonts()).expect("renders");
            assert!(svg.contains(&format!("Signups: {shown}\"")), "{shown}: {svg}");
            assert!(!svg.contains("aria-label=\"signups: 0\""), "{svg}");
        }
    }

    /// A facet panel's description names an area's color field as written and a line's cased as a title.
    #[test]
    fn a_description_names_the_color_field_as_the_family_cases_it() {
        let legend: crate::families::legend::LegendConfig = serde_json::from_value(serde_json::json!({
            "visible": true, "position": {"edge": "right", "align": "start", "overlay": false},
            "direction": "vertical", "columns": null,
            "label": {"font": {"size": 11.0}, "padding": 8.0},
            "title": {"font": {"case": "title"}, "visible": false},
        }))
        .unwrap();
        let labels = crate::common::FieldLabels::default();
        assert_eq!(crate::families::line::description_color_title("area", "my_seg", &legend, &labels).unwrap(), "my_seg");
        assert_eq!(crate::families::line::description_color_title("line", "my_seg", &legend, &labels).unwrap(), "My Seg");
    }

    #[test]
    fn a_quantitative_x_ticks_at_round_steps_where_the_line_passes_them() {
        let widened: Vec<String> = (0..=40).map(|i| i.to_string()).collect();
        let ticks = super::quantitative_x_ticks(&widened, 6).unwrap();
        let texts: Vec<&str> = ticks.iter().map(|(t, _)| t.as_str()).collect();
        assert_eq!(texts, ["0", "5", "10", "15", "20", "25", "30", "35", "40"]);
        assert!((ticks[1].1 - 5.0 / 40.0).abs() < 1e-9);
        let uneven: Vec<String> = ["0", "1", "10"].map(String::from).to_vec();
        let ticks = super::quantitative_x_ticks(&uneven, 2).unwrap();
        assert_eq!(ticks.iter().map(|(t, _)| t.as_str()).collect::<Vec<_>>(), ["0", "5", "10"]);
        assert!((ticks[1].1 - (1.0 + 4.0 / 9.0) / 2.0).abs() < 1e-9, "5 sits between the points 1 and 10");
    }

    // ── The family contract ──────────────────────────────────────────────

    fn contract_payload(mutate: impl FnOnce(&mut serde_json::Value)) -> Payload {
        let mut json = make_fit_line_payload();
        mutate(&mut json);
        serde_json::from_value(json).unwrap()
    }

    /// `bind` reads no font: the x domain is derived from the rows' instants, so a temporal x
    /// seats left to right whatever order the query returned.
    #[test]
    fn bind_derives_the_temporal_x_in_time_order() {
        let _en = crate::time_locale::en_us_scope();
        let json = plain_x_curve_payload(&["2024-03-01", "2024-01-01", "2024-02-01"], "linear", Some("temporal"));
        let payload: Payload = serde_json::from_value(json).unwrap();
        let bound = super::bind::bind(&payload, false).expect("line binds");
        assert_eq!(bound.x.domain, ["2024-01-01", "2024-02-01", "2024-03-01"]);

        let categories = contract_payload(|_| {});
        let bound = super::bind::bind(&categories, false).expect("line binds");
        assert_eq!(bound.x.domain, ["Jan", "Feb", "Mar"], "a category keeps its first-appearance order");
    }

    /// An x value that is no date refuses at bind, ahead of any layout.
    #[test]
    fn bind_refuses_a_temporal_x_value_that_is_no_date() {
        let json = plain_x_curve_payload(&["2024-01-01", "nope", "2024-03-01"], "linear", Some("temporal"));
        let payload: Payload = serde_json::from_value(json).unwrap();
        for draw_areas in [false, true] {
            let err = super::bind::bind(&payload, draw_areas).err().expect("bind refuses");
            assert!(err.to_string().contains("ERR-X-VALUE-NOT-A-DATE"), "{err}");
        }
    }

    /// A line fills the width it is given and draws in the slot sizing hands it.
    #[test]
    fn a_line_hugs_nothing_and_takes_the_slot_height() {
        let payload = contract_payload(|_| {});
        let bound = super::bind::bind(&payload, false).expect("line binds");
        let fonts = test_fonts();
        let measure = measure::Measure { fonts: &fonts };
        assert_eq!(super::layout::intrinsic(&bound, &measure).unwrap(), None);
        assert_eq!(super::layout::height_for_width(&bound, 123.0, &measure).unwrap(), payload.height);
    }

    /// `layout` draws the cell it is given and raises its WARNs itself: the title is cut and the
    /// finding comes back in the layout, none reaching the sink.
    #[test]
    fn layout_draws_the_given_cell_and_returns_its_warnings() {
        let long = "A title that keeps going and going until no budget holds it, however it wraps";
        for draw_areas in [false, true] {
            let payload = contract_payload(|json| json["resolved"]["title"] = serde_json::json!(long.repeat(4)));
            let bound = super::bind::bind(&payload, draw_areas).expect("line binds");
            let fonts = test_fonts();
            let measure = measure::Measure { fonts: &fonts };
            let cell = crate::layout::Rect { x: 0.0, y: 0.0, width: 420.0, height: 260.0 };
            let (laid, sunk) = crate::diagnostic::collect(|| super::layout::layout(&bound, cell, &measure));
            let laid = laid.expect("line lays out");
            assert!(sunk.is_empty(), "{sunk:?}");
            assert!(!laid.diagnostics.is_empty(), "the cut title warns");
            let codes: Vec<_> = laid.diagnostics.iter().map(|d| d.code).collect();
            assert_eq!(codes, ["WARN-CHART-TITLE-TRUNCATED"]);
            let [scene::Node::Svg { extent, .. }] = laid.nodes.as_slice() else { panic!("one root") };
            assert_eq!((extent.width, extent.height), (420.0, 260.0));
            let data = laid.data_box;
            assert!(data.x >= 0.0 && data.y >= 0.0 && data.x + data.width <= 420.0 && data.y + data.height <= 260.0);
            assert_eq!(laid.insets, crate::layout::Rect { x: 0.0, y: 0.0, width: 420.0, height: 260.0 }.insets_of(&data));
        }
    }

    /// A multiples chart is composed panel by panel by `render`; handed to `layout` whole it is an
    /// engine-invariant error.
    #[test]
    fn layout_refuses_a_multiples_chart() {
        let payload = contract_payload(|json| {
            json["resolved"]["multiples"] = serde_json::json!({"rows": null, "columns": "month", "scale": "shared"});
        });
        let bound = super::bind::bind(&payload, false).expect("line binds");
        let fonts = test_fonts();
        let cell = crate::layout::Rect { x: 0.0, y: 0.0, width: payload.width, height: payload.height };
        let err = super::layout::layout(&bound, cell, &measure::Measure { fonts: &fonts }).err().expect("layout refuses");
        assert!(err.to_string().contains("layout draws one plot"), "{err}");
    }

    /// `render` is `bind`, `layout` into the payload's card, and `print`; the warnings reach the
    /// sink once, from the layout's own list.
    #[test]
    fn render_prints_the_layout_of_the_bound_chart() {
        for draw_areas in [false, true] {
            let payload = contract_payload(|json| json["resolved"]["title"] = serde_json::json!("T ".repeat(300)));
            let bound = super::bind::bind(&payload, draw_areas).expect("line binds");
            let fonts = test_fonts();
            let cell = crate::layout::Rect { x: 0.0, y: 0.0, width: payload.width, height: payload.height };
            let laid = super::layout::layout(&bound, cell, &measure::Measure { fonts: &fonts }).unwrap();
            let (svg, sunk) = crate::diagnostic::collect(|| render_inner(&payload, &fonts, draw_areas));
            assert_eq!(svg.unwrap(), print::print(&laid.nodes));
            assert_eq!(sunk, laid.diagnostics);
        }
    }

    /// A grained timestamp with an offset is bucketed by its UTC instant, as the same instant spelled in UTC is.
    #[test]
    fn an_offset_timestamp_draws_where_its_utc_spelling_does() {
        let offset = ["2024-03-01T12:00:00Z", "2024-03-02T23:30:00-05:00", "2024-03-04T12:00:00Z"];
        let utc = ["2024-03-01T12:00:00Z", "2024-03-03T04:30:00Z", "2024-03-04T12:00:00Z"];
        for draw_areas in [false, true] {
            let draw = |xs: &[&str]| {
                let mut p = plain_x_curve_payload(xs, "linear", Some("temporal"));
                p["resolved"]["style"]["axis_x"]["time_unit"] = serde_json::json!("yearmonthdate");
                render_inner(&serde_json::from_value(p).unwrap(), &test_fonts(), draw_areas).expect("renders")
            };
            assert_eq!(
                crate::time_axis::testing::geometry(&draw(&offset)),
                crate::time_axis::testing::geometry(&draw(&utc)),
                "areas: {draw_areas}"
            );
        }
    }

    /// A layer's own x values are read by the axis `bind` builds, so one that is no date refuses at bind.
    #[test]
    fn bind_refuses_a_layer_x_value_that_is_no_date() {
        let mut p = layered_line_payload_with_cross_query_layer();
        p["datasets"]["trend_query"][1]["week"] = serde_json::json!("later");
        let payload: Payload = serde_json::from_value(p).unwrap();
        let err = super::bind::bind(&payload, false).err().expect("bind refuses");
        assert!(err.to_string().contains("ERR-X-VALUE-NOT-A-DATE"), "{err}");
    }

    /// The tooltip names the day the point sits on: `2024-03-02T01:00+05:30` is March 1 in UTC.
    #[test]
    fn a_tooltip_names_the_utc_day_of_an_offset_timestamp() {
        let xs = ["2024-03-02T01:00:00+05:30", "2024-03-03T12:00:00Z", "2024-03-04T12:00:00Z"];
        for draw_areas in [false, true] {
            let mut p = plain_x_curve_payload(&xs, "linear", Some("temporal"));
            p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
            let svg = render_inner(&serde_json::from_value(p).unwrap(), &test_fonts(), draw_areas).expect("renders");
            assert!(svg.contains("1 Mar 2024; Signups: "), "areas {draw_areas}: {svg}");
            assert!(!svg.contains("2 Mar 2024; Signups: "), "areas {draw_areas}: {svg}");
        }
    }

    /// On a New York board an aware timestamp's tooltip header and `labels.expr` ticks read the New York
    /// clock; a naive one reads as written.
    #[test]
    fn an_aware_line_reads_its_tooltip_and_tick_clock_in_the_board_zone() {
        let _locale = crate::time_locale::en_us_scope();
        let _zone = crate::time_zone::zone_scope("America/New_York");
        let render = |xs: &[&str]| {
            let mut p = plain_x_curve_payload(xs, "linear", Some("temporal"));
            p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
            p["resolved"]["style"]["axis_x"]["labels"]["expr"] = serde_json::json!("timeFormat(datum.value, '%H') + 'h'");
            render_inner(&serde_json::from_value(p).unwrap(), &test_fonts(), false).expect("renders")
        };
        let x_axis = |svg: &str| svg.split("aria-label=\"X-axis").nth(1).and_then(|rest| rest.split("aria-label=\"Y-axis").next()).expect("an x axis").to_string();
        // 07:00Z is 03:00 EDT on the spring-forward day.
        let aware = render(&["2024-03-10T05:00:00Z", "2024-03-10T07:00:00Z", "2024-03-10T09:00:00Z"]);
        assert!(aware.contains("3:00am; Signups: ") && !aware.contains("7:00am; Signups: "), "{aware}");
        assert!(aware.contains("Sunday, 10 March 2024, 12:00:00 AM to"), "the caption reads the New York clock: {aware}");
        assert!(x_axis(&aware).contains(">03h<") && !x_axis(&aware).contains(">07h<"), "{}", x_axis(&aware));
        let naive = render(&["2024-03-10T05:00:00", "2024-03-10T07:00:00", "2024-03-10T09:00:00"]);
        assert!(naive.contains("7:00am; Signups: "), "{naive}");
        assert!(x_axis(&naive).contains(">07h<"), "{}", x_axis(&naive));
    }

    /// Under a day grain the bucket is the UTC day, and the axis names it in its ticks and its aria text.
    #[test]
    fn a_day_grain_buckets_an_offset_timestamp_by_its_utc_day() {
        let xs = ["2024-03-01T23:30:00-05:00", "2024-03-03T12:00:00Z", "2024-03-05T23:30:00-05:00"];
        for draw_areas in [false, true] {
            let mut p = plain_x_curve_payload(&xs, "linear", Some("temporal"));
            p["resolved"]["style"]["axis_x"]["time_unit"] = serde_json::json!("yearmonthdate");
            p["resolved"]["style"]["tooltip_format"] = serde_json::json!(",.2~f");
            let svg = render_inner(&serde_json::from_value(p).unwrap(), &test_fonts(), draw_areas).expect("renders");
            assert!(svg.contains("from March 02, 2024 to March 06, 2024"), "areas {draw_areas}: {svg}");
            let x_axis = svg.split("aria-label=\"X-axis").nth(1).and_then(|rest| rest.split("aria-label=\"Y-axis").next()).expect("an x axis");
            assert!(x_axis.contains(">6</text>"), "areas {draw_areas}: {x_axis}");
            assert!(svg.contains("2 Mar 2024; Signups: "), "areas {draw_areas}: {svg}");
        }
    }

    /// A number x that no date shape explains stays a linear scale, widened to an annotation past the data.
    #[test]
    fn an_annotation_past_a_quantitative_four_digit_x_widens_the_linear_scale() {
        let mut json = make_line_payload(false, false);
        json["rows"] = serde_json::json!([
            {"week": 1000, "signups": 100.0}, {"week": 1100, "signups": 150.0}, {"week": 1200, "signups": 130.0},
        ]);
        json["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(true);
        json["resolved"]["annotations"] = Value::Array(vec![annotation(0, value(serde_json::json!(1600)), Value::Null)]);
        let svg = render(&serde_json::from_value::<Payload>(json).unwrap(), &test_fonts()).expect("renders");
        assert!(svg.contains("X-axis for a linear scale with values from 1,000 to 1,600"), "{svg}");
    }

    /// A layout that fails has no `Layout` to carry its findings: the WARNs it collected before the
    /// refusal reach the sink, as they did when the render wrote to it directly.
    #[test]
    fn a_failed_layout_forwards_the_warnings_it_collected() {
        let mut json = make_line_payload(false, false);
        json["rows"] = serde_json::json!([
            {"week": 2019, "signups": 100.0}, {"week": 2020, "signups": 150.0}, {"week": 2021, "signups": 130.0},
        ]);
        json["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(true);
        json["resolved"]["title"] = serde_json::json!("T ".repeat(300));
        json["resolved"]["annotations"] = Value::Array(vec![annotation(0, value(serde_json::json!(2020.5)), Value::Null)]);
        let payload: Payload = serde_json::from_value(json).unwrap();
        let (rendered, sunk) = crate::diagnostic::collect(|| render(&payload, &test_fonts()));
        assert_eq!(rendered.unwrap_err().code, "ERR-ANNOTATION-PAYLOAD");
        let codes: Vec<_> = sunk.iter().map(|d| d.code).collect();
        assert_eq!(codes, ["WARN-CHART-TITLE-TRUNCATED"]);
    }

    /// A year-shaped x is a time scale whatever `is_quantitative` says, so an annotation number past the data is
    /// refused by name before it widens the axis, and so is one inside the data.
    #[test]
    fn an_annotation_number_past_a_year_shaped_x_is_refused_by_name() {
        for anchor in [2021.5, 2025.0, 2025.5] {
            let mut json = make_line_payload(false, false);
            json["rows"] = serde_json::json!([
                {"week": 2019, "signups": 100.0}, {"week": 2020, "signups": 150.0}, {"week": 2021, "signups": 130.0},
                {"week": 2022, "signups": 140.0}, {"week": 2023, "signups": 160.0},
            ]);
            json["resolved"]["style"]["axis_x"]["is_quantitative"] = serde_json::json!(true);
            json["resolved"]["annotations"] = Value::Array(vec![annotation(0, value(serde_json::json!(anchor)), Value::Null)]);
            let err = render(&serde_json::from_value::<Payload>(json).unwrap(), &test_fonts()).expect_err("refused");
            assert!(format!("{err:?}").contains("ERR-ANNOTATION-PAYLOAD") && format!("{err:?}").contains("a number on a time axis"), "{anchor}: {err:?}");
        }
    }
}
