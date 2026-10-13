//! Family-generic small-multiples composer — hoisted out of `bar.rs`'s
//! original `render_facet` so a second family (line, area) can compose N
//! per-panel renders into one faceted card without a
//! second reading of Vega's own `gridLayout`/`layoutHeaders`
//! closed-form reduction. `bar.rs::render_facet` and `line.rs::render_facet`
//! now do exactly two things: refuse the shapes their own arm doesn't
//! model, then hand this module a `render_pass` closure (their own
//! per-panel renderer, called once per `(panel_w, panel_h)` probe) plus a
//! `FacetSpec` describing the facet's own shape (row/column axis counts,
//! which baked `panel_axes` index is the row vs the column, whether a
//! mirror ghost is active, the shared x/y axis style, the header font).
//! Everything from the panel-width probe-and-correct through the final SVG
//! assembly (row/column headers, the mirror ghost, panel splicing, the
//! shared footer axis) lives here, once — "one law, one function," the
//! same rule this crate's `AGENTS.md` states for `wrap_title_block`/
//! `legend_grid`/`symbol_radius`.
//!
//! `PanelGeometry`/`PanelXAxis` are the minimal per-panel shape ANY facet
//! arm normalizes into — bar's own `BandXAxis`/`ContinuousXAxis` and line's
//! `LineXAxis` are three different axis painters behind one `paint`
//! dispatch, so this module never needs to know which family/arm drew a
//! panel.
//!
//! Byte identity: hoisting this body verbatim out of `bar.rs` changed
//! nothing about ITS OWN laws — `golden_sweep.py --group multiples` and
//! `board_sweep.py` both diff byte-identical on bar's own rungs 01-05
//! before/after this move (see `docs/laws-shared.md`'s facet ledger).
//!
//! **This composer's own height-correction law**: `correct_facet_child_extent` —
//! production's own single-pass facet-overshoot correction
//! (`_correct_facet_overshoot`, `converters/chart.py:729`) — replaces the
//! old closed-form `child_height` subtraction with a render-first probe:
//! one render at the naive `payload.height / panel_rows_n`, then the
//! composite's own measured overshoot past `payload.height` divided evenly
//! across every row, `Err` on a non-positive result rather than silently
//! skipped (production's own docstring allows a skip; this composer's every
//! other collapse guard already refuses instead). The composite's own outer
//! top margin is now real Vega's `ceil(-viewBounds.y1)` (`ViewLayout.js:
//! 165`'s `viewSizeLayout`), where `viewBounds` UNIONS every row-header/
//! row-footer/column-header/column-footer role's bounds — never sums them —
//! so `margin_top` is `header_band.max(topmost_row.top_axis_overhang).ceil()`,
//! measured TWICE at two different heights (the naive probe, folded into
//! the height-correction pass alone; the corrected final height, for the
//! real canvas/translate/paint) and never shared between them, mirroring
//! production's own probe-then-real-render shape. The column header now
//! anchors at `pad_t + margin_top - COLUMN_HEADER_BAND_HEIGHT` rather than
//! flush `pad_t`, so a large value-axis label font that makes the axis
//! overhang win the union rides the view origin down with the panels
//! (`ViewLayout.js`'s `layoutGroup`) instead of staying pinned at `pad_t`.
//! `canvas_for` takes `margin_top` as an explicit parameter (not a captured
//! variable) since it is evaluated at the two different margin values above.
//! `PanelGeometry.top_axis_overhang` is the new field this all reads —
//! each arm's own `fit`/`ink` closure tracks it via a `std::cell::Cell`
//! (bar's `render_vertical_continuous_x_panel`/`render_vertical_panel`,
//! line's `render_line_panel`), reset at the start of every `fit()` call
//! since an unbaked ladder's own retry loop must not let a stale call's
//! overhang survive into the next one's measurement.
//!
//! Every panel's own canvas starts `left_mark_overhang` LEFT of its own
//! plot (`canvas_x`'s own doc comment): the splice and the mirror ghost's
//! anchor both land at that canvas edge, while the shared footer axis and
//! the column header's own center are Vega roles that sit at the CELL's
//! own declared origin instead — `left_mark_overhang` to the right of the
//! canvas edge — so those two call sites add it back. The canvas's own
//! width reserves `left_mark_overhang` exactly ONCE (`canvas_for`'s
//! content origin is the same `canvas_x(0)` the splice uses, never
//! `margin_left` alone, which already reserves it under a different name
//! for the row-header column). Row spacing (`cell_for`) and the shared
//! x-axis's own y (`footer_y`) read `layoutHeaders`'s real law (`grid.js`)
//! directly — every panel's own REAL `ink.y2`, never a canonicalized
//! reading off panel 0, and the shared footer's own y is `Math.ceil` of
//! the MAX real `ink.y2` across the LAST ROW's cells (`grid.js:13`'s `agg`
//! callback for `layoutHeaders`'s column-footer pass, `grid.js:268`) —
//! never a per-column value, despite painting once per column.

use std::fmt::Write as _;

use serde_json::Value;

use crate::common::AxisStyle;
use crate::families::bar::{BandXAxis, ContinuousXAxis};
use crate::families::legend::VEGA_LEGEND_OFFSET;
use crate::families::facet_layout;
use crate::families::heatmap::AXIS_TRANSLATE;
use crate::families::heatmap_facet::HeatmapXAxis;
use crate::families::line::LineXAxis;
use crate::families::scatter_axis::TemporalXAxis;
use crate::families::multiples::{self, Panel};
use crate::svg::{xml_escape, xml_escape_attr, vega_escape_attr};
use crate::text::FontBook;
use crate::vega_fit;
use crate::Payload;

/// One per-panel x-axis painter, whichever family/arm drew the panel — see
/// each variant's own family module for its construction.
pub(crate) enum PanelXAxis {
    Band(BandXAxis),
    Continuous(ContinuousXAxis),
    Line(LineXAxis),
    Temporal(TemporalXAxis),
    Heat(HeatmapXAxis),
}

impl PanelXAxis {
    /// How far the shared x axis's labels reach left of the cell's plot origin
    /// (the `column-footer` role's `bounds.x1`, which `layoutGroup` unions into
    /// the view's left extent, `ViewLayout.js:72-82`). A point scale puts its
    /// first label's center ON the plot origin, so the reach is half that
    /// label's width. Every other arm's first label sits inside the cell: a band
    /// axis centers it half a step in, and a continuous or temporal axis anchors
    /// it flush to the origin.
    pub(crate) fn label_left_reach(&self) -> f64 {
        match self {
            PanelXAxis::Line(x) => x.label_left_reach(),
            PanelXAxis::Band(_)
            | PanelXAxis::Continuous(_)
            | PanelXAxis::Temporal(_)
            | PanelXAxis::Heat(_) => 0.0,
        }
    }

    /// Paints at the CALLER's own `plot_left`/`plot_w`/`plot_bottom` — the
    /// facet composite's own footer-band geometry, not the panel's own.
    pub(crate) fn paint(&self, svg: &mut String, ax_x: &AxisStyle, plot_left: f64, plot_w: f64, plot_bottom: f64) {
        match self {
            PanelXAxis::Band(x) => x.paint(svg, ax_x, plot_left, plot_w, plot_bottom),
            PanelXAxis::Continuous(x) => x.paint(svg, ax_x, plot_left, plot_w, plot_bottom),
            PanelXAxis::Line(x) => x.paint(svg, ax_x, plot_left, plot_w, plot_bottom),
            PanelXAxis::Temporal(x) => x.paint(svg, ax_x, plot_left, plot_w, plot_bottom),
            PanelXAxis::Heat(x) => x.paint(svg, ax_x, plot_left, plot_w, plot_bottom),
        }
    }
}

/// The primary value axis's own resolved label paint attributes — a
/// facet's `axis_y.mirror` ghost copy paints with these EXACT values,
/// never a second, independent read of `axis_y.labels.font` (which would
/// drift from the primary the moment a theme or board sets a non-default
/// label style; `mirror_axis.py`'s own contract is `dict(primary_axis)`
/// verbatim except `orient`/`grid`/`title`).
pub(crate) struct GhostStyle {
    pub(crate) val_font_size: f64,
    pub(crate) val_weight: String,
    pub(crate) val_color: String,
    pub(crate) val_family: String,
}

/// A facet's color legend, laid out and painted at its own origin `(0, 0)`
/// by whichever arm draws the panels: Vega resolves the legend once for the
/// whole composite (`layoutGroup` gathers the root's `role-legend` marks), so
/// the composer places it (`LegendChrome`), never a panel.
pub(crate) struct FacetLegend {
    pub(crate) svg: String,
    pub(crate) box_w: f64,
    pub(crate) box_h: f64,
}

/// The minimal per-panel geometry a facet composite needs off ANY arm —
/// `compose_facet` reads only these fields regardless of which family/arm
/// drew the panel, so each arm normalizes into this shape rather than the
/// composer knowing any one arm's own full geometry struct.
pub(crate) struct PanelGeometry {
    pub(crate) content_start: usize,
    pub(crate) svg_end: usize,
    pub(crate) ink: vega_fit::Bounds,
    /// The value-axis-gutter overhang, already folded into `ink` above and
    /// reported unfolded here too.
    pub(crate) axis_w_overhang: f64,
    /// `m.left` — how far this panel's own ink (a y-axis crisp stroke on
    /// bar's band scale, a value-axis-gutter fold plus mark overhang on a
    /// line/area point scale) reaches LEFT of the panel's own PLOT origin,
    /// carried directly (never `axis_w_overhang` minus a right-only share
    /// computed elsewhere and subtracted back — that round trip served no
    /// purpose once every caller could report this term itself). Bar's own
    /// band x-scale measures a nonzero `2.0` on every rung this composer
    /// has probed (the same flat crisp-stroke allowance the retired
    /// `HEADER_ROW_OFFSET` constant used to hardcode) — NOT `0.0`, despite
    /// an earlier version of this comment claiming a band axis never
    /// overhangs left. `compose_facet` reads this to
    /// place every panel's own PLOT origin (not its canvas origin, which
    /// sits `m.left` further right) at the grid cell's own declared x —
    /// see `compose_facet`'s own `canvas_x` doc comment.
    pub(crate) left_mark_overhang: f64,
    /// The mark's own RAW right overhang past `plot_w`, UNCEILED — never
    /// `left_mark_overhang` (already `(-b.x1).ceil()`, `vega_fit::
    /// pad_plot`'s own law). Real Vega's `row_footer` ceils the cell's raw
    /// `bounds.x2` ONCE (`grid.js:13`'s `agg`, `:313`); ceiling an
    /// already-ceiled `left_mark_overhang` instead agrees with that only
    /// when the raw reach is itself integral, which it usually is NOT (a
    /// line point mark's `size`-derived reach, a band axis with no
    /// zero-rule, a value-axis `scale.padding`).
    ///
    /// Real Vega's cell `bounds.x2` is the union of the group rect
    /// `[0, width]`, the mark ink, and the zero/baseline rule — the SAME
    /// quantity `vega_fit::pad_plot`'s own `ink` closure already builds to
    /// compute the LEFT margin. `vega_fit::FitMargins.raw_left` is that
    /// closure's own raw (unceiled, floored at 0) `-b.x1` — both arms read
    /// `m.raw_left` off the SAME `pad_plot`/`fit_plot` call that measures
    /// `left_mark_overhang`, rather than re-deriving a right-side term
    /// from a hand-picked subset of the marks (a mark-only formula misses
    /// the zero/baseline rule and a value-axis `scale.padding`, and floors
    /// at nothing rather than 0). See `vega_fit::FitMargins.raw_left`'s
    /// own doc comment for when that left-side reading is and is NOT the
    /// right-side reach too — a facet arm with a left value axis, title,
    /// or legend in its own `ink` closure must refuse by name rather than
    /// read `raw_left` here (bar's own `axis_y.position: "left"` refusal).
    pub(crate) raw_right_reach: f64,
    /// The PAINT font's widest DRAWN value-axis label — the ghost's own
    /// outward-ink term. `0.0` where an arm never populates it.
    pub(crate) widest_val_tick_label_width: f64,
    /// The SPEC value-axis gutter (`gutter_of`'s own return, the same
    /// quantity a single-chart right-axis fit arm reserves). `0.0` where an
    /// arm never populates it.
    pub(crate) val_gutter: f64,
    /// The primary value axis's own tick length (`0.0` when its ticks are
    /// hidden): Vega anchors an axis label at `sign * ((tickSize if ticks
    /// visible else 0) + labelPadding)` (`utils.py:853-860`), so the primary's
    /// paint and the ghost's reserve both read it from the one panel that
    /// painted the axis. `0.0` where an arm never populates it.
    pub(crate) val_tick_size: f64,
    /// `(y position, formatted label)` for every VISIBLE value tick,
    /// captured regardless of whether THIS panel paints them — the composer
    /// repaints them on whichever panel/edge ends up drawing the axis.
    pub(crate) val_ticks: Vec<(f64, String)>,
    /// The panel-frame y of every rung's tick mark (Vega's `tickRound` row
    /// plus the axis group's crisp 0.5), captured like `val_ticks` but for
    /// every rung: label overlap thins labels, never marks. Empty where an
    /// arm never mirrors or hides its ticks.
    pub(crate) val_tick_marks: Vec<f64>,
    /// Mirror-ghost paint style — `Some` on an arm whose facet shapes
    /// admit `axis_y.mirror` (bar's and line/area's columns/grid shapes),
    /// `None` on an arm that never does — an `Option`, not four sentinel
    /// `0.0`/empty-string fields, so a caller that never resolves a real
    /// mirror style cannot construct an invalid "has a style but it's all
    /// zeros" state. `compose_facet`'s own ghost-paint loop only runs
    /// `if has_mirror`, and every `has_mirror: true` caller supplies `Some`
    /// on every panel, so reading it there is an `expect`, not a fallback.
    pub(crate) mirror_style: Option<GhostStyle>,
    pub(crate) axis: PanelXAxis,
    /// How far the value axis's own topmost tick label overhangs above this
    /// panel's own `y=0`, unclamped — `0.0` unless a label crosses it. Real
    /// Vega's row-facet composite reserves this ONCE, unioned against the
    /// column-header band (never summed — `ViewLayout.js:72-82`), at the
    /// whole composite's own outer top margin (`ceil(-viewBounds.y1)`,
    /// `ViewLayout.js`'s `viewSizeLayout`), not per row — see
    /// `compose_facet`'s own `probe_margin_top`/`margin_top`. Each arm's
    /// own `fit`/`ink` closure tracks this via a `std::cell::Cell`, reset at
    /// the start of every `fit()` call (an unbaked ladder's own retry loop
    /// must not let a stale call's overhang survive into the next one's
    /// measurement).
    pub(crate) top_axis_overhang: f64,
    /// `None` on an arm with no color legend; every panel of a colored bar
    /// facet carries the same one, and the family lifts panel 0's into the
    /// composite's `LegendChrome`.
    pub(crate) legend: Option<FacetLegend>,
}

/// The deepest `y`/`y1`/`y2` attribute value drawn in an SVG fragment —
/// `compose_facet`'s own probe for how far a `PanelXAxis::paint` call
/// reaches below the `plot_bottom` it was given (font/tick geometry only,
/// so painting at `plot_bottom: 0.0` makes this exactly that reach).
/// Measuring the real rendered output rather than re-deriving Vega's own
/// text/tick ink formula a second time.
fn max_y_attr(svg: &str) -> f64 {
    let mut max = 0.0_f64;
    for attr in ["y=\"", "y1=\"", "y2=\""] {
        for seg in svg.split(attr).skip(1) {
            if let Some(v) = seg.split('"').next().and_then(|s| s.parse::<f64>().ok()) {
                max = max.max(v);
            }
        }
    }
    max
}

/// Display text for one small-multiples panel-axis value — the facet
/// header's own label. `Panel.key` entries are the ORIGINAL (non-
/// canonicalized) `serde_json::Value`s `regroup` carries alongside the
/// canonical string form; a string value renders bare (never re-quoted),
/// matching a VL nominal field's own label text — every other scalar
/// (number/bool/null) falls back to its JSON form.
pub(crate) fn facet_value_label(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// A facet's own shape — everything `compose_facet` needs beyond the
/// `render_pass` closure's own per-panel output. `panels` is read only for
/// the row/column header labels (`render_pass` already captured its own
/// copy for the per-panel render loop).
pub(crate) struct FacetSpec<'a> {
    /// The caller's own family name (`"bar"`/`"line"`/`"area"`) — every
    /// collapse-guard error this composer raises carries it, so a family-
    /// generic error message still reads as that family's own (matching
    /// the family-scoped prefix every refusal ahead of `compose_facet`
    /// already uses).
    pub(crate) family: &'a str,
    pub(crate) panels: &'a [Panel],
    pub(crate) panel_rows_n: usize,
    pub(crate) panel_cols_n: usize,
    pub(crate) row_axis_idx: Option<usize>,
    pub(crate) col_axis_idx: Option<usize>,
    pub(crate) has_mirror: bool,
    pub(crate) axis_x: &'a AxisStyle,
    /// The value axis's tick style: the mirror ghost copies the primary's
    /// marks (`mirror_axis.py` keeps `ticks`/`tickSize`).
    pub(crate) axis_y: &'a AxisStyle,
    pub(crate) header_font_family: &'a str,
    /// The composite canvas's own background fill — every caller now
    /// passes the chart's OWN resolved `background` (`r.background.
    /// as_str()`; line: `LineResolved::background`, bar:
    /// `BarResolved::background`), never `payload.background` (the
    /// board-level default only).
    pub(crate) background: &'a str,
}

/// Production's own single-pass facet-overshoot correction
/// (`_correct_facet_overshoot`, `converters/chart.py:729`), the height
/// branch: probe once at the naive per-panel extent, measure the
/// composite's own real total ink, then divide the OVERSHOOT (never the
/// shortfall) evenly across every panel. One-directional and single-pass,
/// matching production exactly — `if overshoot > 0` (a probe that comes
/// back no taller than `target` is not spare room to grow into, it means
/// the naive guess already fits, so `naive` returns unchanged) and no
/// second probe-and-recheck loop (production's own docstring: "Single-pass,
/// not iterative"). `compose_facet` calls this once, feeding it its own
/// `panel_h_guess`/probed composite canvas height/`payload.height`/
/// `panel_rows_n` — the exact same four terms production's branch reads as
/// `orig_h`/`probe["height"]`/`target_height`/`panel_rows`.
///
/// Pinned directly against production's own real numbers for two fixtures
/// (`correct_facet_child_extent_tests`, below): `dbt-charts/tests/visual/
/// fixtures/facet-panel-axis-title.yml` (naive `802.0/7.0`, probed `993.0`,
/// target `802.0`, 7 rows → `87.28571428571428`) and `fixtures/multiples/
/// 01-rows-only.yml` (naive `216.0`, probed `506.0`, target `432.0`, 2 rows
/// → `179.0`), both captured by wrapping the real `_correct_facet_overshoot`
/// with a spy that records its own probe/target/corrected floats while
/// rendering each board through the real production pipeline.
///
/// Unlike production, a corrected extent that lands non-positive is not
/// silently skipped here (production's own docstring: "A shrink that would
/// leave a panel non-positive is skipped rather than raised... the card
/// boundary still wins, this correction just gets it closer") — this
/// composer's every other collapse guard (`panel_w`, the old closed-form
/// `child_height`) already returns `Err` rather than ship a silently
/// unfixed too-small panel, and this correction keeps that same convention:
/// the caller checks `child_height <= 0.0` itself and refuses.
fn correct_facet_child_extent(naive: f64, probed_composite: f64, target: f64, panel_count: usize) -> f64 {
    let overshoot = probed_composite - target;
    if overshoot > 0.0 { naive - overshoot / panel_count as f64 } else { naive }
}

/// Composes N panels (one `render_pass(pw, ph, row_footer_overhang)` call
/// per probe/final pass) into one faceted card — hoisted verbatim out of
/// bar's original `render_facet` (see `docs/laws-shared.md`'s
/// facet ledger for the probe evidence behind every law below). The caller
/// (`bar.rs::render_facet`, `line.rs::render_facet`) has already refused
/// every shape this composer doesn't model — see each caller's own doc
/// comment for its own refusal list.
///
/// `row_footer_overhang` is real Vega's `row_footer` law
/// (`vega-view-transforms/src/layout/grid.js:262`, `ceil` from `grid.js
/// :13`'s own `agg` callback): the shared value axis anchors at
/// `ceil(cell x2)` — the CELL's own RAW real ink, which reaches `raw_
/// right_reach` past `plot_w` for a mark whose own stroke/halo overhangs
/// the plot symmetrically (the SAME `expand()` a point scale with `axis_x.
/// scale.padding: 0` or a band scale's crisp-stroke allowance already
/// reserves on the LEFT, via `left_mark_overhang`'s own already-ceiled
/// reading of that SAME raw quantity) — never `plot_w` alone, never
/// `left_mark_overhang` itself (ceiling an already-ceiled reach only
/// agrees with Vega's single `ceil` when the raw reach is integral — see
/// `PanelGeometry.raw_right_reach`'s own doc comment), and never
/// conditioned on column count: `layoutHeaders`'s own `start = ncols-1,
/// stride = ncols` reaches the LAST column of every row the same way
/// whether there is one column or several. `0.0` on the probe call
/// (`raw_right_reach` isn't measured yet); `ceil(plot_w + raw_right_
/// reach) - plot_w` on the final call of EVERY facet shape — each
/// caller's own per-panel paint code adds this to its value-axis
/// tick/label anchor, and, on a rows-only facet only (`fold_axis_gutter`),
/// to the ink fold that reserves canvas width for it; a multi-column
/// facet's `composite_axis_reserve` already reserves that width at the
/// composer level, so it takes no ink change — only the anchor moves.
type RenderPass<'a> = dyn Fn(f64, f64, f64) -> Result<Vec<(String, PanelGeometry)>, String> + 'a;

/// A shared category axis drawn once per grid row in the `row_header` role,
/// left of column 0 (a band y axis every panel shares). Each row's panel
/// reports the labels as `val_ticks` and their paint style as `mirror_style`.
pub(crate) struct RowAxisSpec {
    /// How far left of the plot the header axis reaches: the row titles
    /// stack outside it (`[title][10][axis][cell]`).
    pub(crate) reserve: f64,
    /// Where a label starts, relative to the plot's left edge.
    pub(crate) label_x: f64,
}

/// What a family paints above a facet grid — Vega's `title` role and a
/// `legend` role with `orient: top`. Each is an extent the composite stacks
/// plus a paint closure handed the absolute origin of its group.
pub(crate) struct FacetChrome<'a> {
    pub(crate) title: Option<TitleChrome<'a>>,
    pub(crate) legend: Option<LegendChrome<'a>>,
}

pub(crate) struct TitleChrome<'a> {
    pub(crate) ink: vega_fit::TitleInk,
    /// A title row Vega cut to its native `limit`: the view then reserves that
    /// limit exactly, which `ink.width` (the measured string) does not
    /// reproduce, so the composite refuses to widen for it.
    pub(crate) truncated: bool,
    pub(crate) paint: Box<dyn Fn(&mut String, f64, f64) + 'a>,
}

pub(crate) struct LegendChrome<'a> {
    /// The legend's width beside a unit `panel_w` wide: a gradient legend is
    /// as long as the unit (clamped), a symbol legend ignores it.
    pub(crate) width: Box<dyn Fn(f64) -> f64 + 'a>,
    pub(crate) height: f64,
    /// Paints at the absolute `(x, y)` of the legend group, beside a unit
    /// `panel_w` wide.
    pub(crate) paint: Box<dyn Fn(&mut String, f64, f64, f64) + 'a>,
}

/// Where the composite's top-anchored chrome lands, in the frame's own
/// coordinates (origin at the first cell's plot origin), and the top margin
/// that holds it.
struct ChromeStack {
    margin_top: f64,
    legend_y: f64,
    title_y: f64,
}

/// `ViewLayout.js:layoutGroup` for the root group: a top legend anchors at
/// `floor(yBounds.y1) − offset` and grows upward by its height
/// (`layout/legend.js:legendParams`, `case Top`); the title then stacks
/// `title.offset` above everything before it (`layout/title.js`, `case Top`);
/// the view's top margin is `ceil(−viewBounds.y1)` (`viewSizeLayout`).
/// `frame_top` is how far the frame's own content reaches above its origin.
fn stack_chrome_above(frame_top: f64, chrome: Option<&FacetChrome>) -> ChromeStack {
    let mut y1 = -frame_top;
    let mut legend_y = 0.0;
    let mut title_y = 0.0;
    if let Some(legend) = chrome.and_then(|c| c.legend.as_ref()) {
        legend_y = y1.floor() - VEGA_LEGEND_OFFSET - legend.height;
        y1 = y1.min(legend_y);
    }
    if let Some(title) = chrome.and_then(|c| c.title.as_ref()) {
        title_y = y1 - title.ink.ink_h - title.ink.offset;
        y1 = y1.min(title_y + title.ink.dy_quirk);
    }
    ChromeStack { margin_top: (-y1).max(0.0).ceil(), legend_y, title_y }
}

/// Composes the facet against `payload.height` as the box the chart must
/// fill. Production sizes a chart's box from a first render at the static
/// estimate and keeps THAT render (its `render_cache`), so a composite that
/// overshoots the estimate by more than the two pixels the sizing clamp
/// forgives is placed in a box exactly as tall as its own canvas. A render
/// at the box height would overshoot again, so the target is solved back to
/// the estimate whose canvas lands on the box: `target -= canvas - box`,
/// iterated to a fixed point. A sizing render (`measuring`) measures the
/// natural overshoot and never solves. Only a composite that hangs chrome
/// solves; one without (an untitled, uncolored bar or line facet) keeps the
/// unsolved canvas its rungs were pinned with.
///
/// With a title and/or a top legend above the grid.
pub(crate) fn compose_facet_with_chrome(
    payload: &Payload,
    fonts: &FontBook,
    spec: &FacetSpec,
    chrome: Option<&FacetChrome>,
    render_pass: &RenderPass,
) -> Result<String, String> {
    compose_facet_full(payload, fonts, spec, chrome, None, render_pass)
}

/// `compose_facet_with_chrome`, with a shared `row_header` category axis.
pub(crate) fn compose_facet_full(
    payload: &Payload,
    fonts: &FontBook,
    spec: &FacetSpec,
    chrome: Option<&FacetChrome>,
    row_axis: Option<&RowAxisSpec>,
    render_pass: &RenderPass,
) -> Result<String, String> {
    if spec.has_mirror && row_axis.is_some() {
        return Err(format!("{}: a mirrored value axis and a shared row axis cannot share the row header", spec.family));
    }
    const SIZING_CLAMP_PX: f64 = 2.0;
    const MAX_SOLVES: usize = 24;
    let (mut svg, mut canvas_h) = compose_facet_at(payload, fonts, spec, chrome, row_axis, render_pass)?;
    if payload.measuring || chrome.is_none() || canvas_h - payload.height <= SIZING_CLAMP_PX {
        return Ok(svg);
    }
    // The canvas is a step function of the target (every ink term is
    // ceiled), so `target -= canvas - box` can straddle the box; walking the
    // integer targets down from the first clamp-exceeding one finds the
    // nearest estimate that lands exactly on it.
    let mut solving = payload.clone();
    for step in 0..MAX_SOLVES {
        solving.height = payload.height - SIZING_CLAMP_PX - 1.0 - step as f64;
        (svg, canvas_h) = compose_facet_at(&solving, fonts, spec, chrome, row_axis, render_pass)?;
        if canvas_h == payload.height {
            return Ok(svg);
        }
    }
    Err(format!(
        "{}: no estimate within {MAX_SOLVES}px below the {} box lands the composite canvas on it (last {canvas_h})",
        spec.family, payload.height
    ))
}

/// One probe-correct-render pass against `payload.height`; returns the
/// composite and its canvas height.
fn compose_facet_at(
    payload: &Payload,
    fonts: &FontBook,
    spec: &FacetSpec,
    chrome: Option<&FacetChrome>,
    row_axis: Option<&RowAxisSpec>,
    render_pass: &RenderPass,
) -> Result<(String, f64), String> {
    let panel_cols_n = spec.panel_cols_n;
    let panel_rows_n = spec.panel_rows_n;
    let has_mirror = spec.has_mirror;
    let bg = spec.background;

    // `extra_axis_px` (facet_extra_axis_width_px) budgets a narrowed per-panel
    // axis forced by `facet_independent_channels` — unreached here: x is
    // always shared for a rows/columns facet (nothing narrows it to a
    // per-panel subset), so `0.0` is the correct input, not an unmodeled
    // gap. This is only an INITIAL guess for the probe below —
    // `facet_panel_width`'s own flat `FACET_CHROME_PX` doesn't know whether
    // the shared value axis carries a title, so it is replaced with a
    // measured value before any panel is drawn for real.
    let panel_w_guess = multiples::facet_panel_width(payload.width, panel_cols_n, has_mirror, 0.0);
    let panel_h_guess = if payload.height > 0.0 { payload.height / panel_rows_n as f64 } else { 0.0 };
    if panel_w_guess <= 0.0 || panel_h_guess <= 0.0 {
        return Err(format!("{}: panel width/height collapsed to zero or below — the card is too small for this many panels", spec.family));
    }

    // `multiples.scale: "shared"` computes the measure domain off the
    // WHOLE chart's rows; `render_pass` itself already closed over that
    // as an explicit override. An independent-scale rows-only facet also
    // reaches this composer (bar's own `render_facet` admits it), each
    // panel computing its own per-panel domain instead — the `probe[0]`
    // uniformity argument below is a "shared"-scale-specific law, not a
    // gate on which modes reach `compose_facet` at all. `row_footer_
    // overhang` is `0.0` here — `raw_right_reach` isn't measured until
    // this very probe returns.
    let unanchored_probe = render_pass(panel_w_guess, panel_h_guess, 0.0)?;
    // The probe is a real Vega layout too, so its value axis is anchored at
    // `ceil(cell x2)` like the final render's: the marks' own right reach
    // (unknown until a first render measures it) is part of the width the
    // width correction reads.
    let probe = render_pass(
        panel_w_guess,
        panel_h_guess,
        (panel_w_guess + unanchored_probe[0].1.raw_right_reach).ceil() - panel_w_guess,
    )?;

    // Real Vega's `gridLayout` (`grid.js`) feeds each cell's own REAL,
    // per-panel `bbox` (`item.bounds`, `boundFull`) into row spacing —
    // never a single canonicalized value substituted for every row: a
    // `multiples.scale: "shared"` facet's panels usually carry the same
    // ladder, but which bar within EACH panel touches the domain max
    // (and so which panel's own ink reaches furthest) can still differ
    // panel to panel, and an independent-scale facet's panels have no
    // shared ladder to begin with. Defined here, early, rather than at
    // its own former call site below: `margin_top`/`probe_margin_top`
    // (just below) need this SAME grid union too, to read the topmost
    // row's own upward-reaching ink from every column, not just column 0.
    let grid_opt = facet_layout::GridOpt { padding: 20.0, columns: panel_cols_n };
    // Vega lays out a cell in its own frame, whose origin is the plot's: the
    // panel canvas starts `left_mark_overhang` left of it.
    let cell_for = |g: &PanelGeometry| {
        facet_layout::GridCell::new(vega_fit::Bounds::new(
            g.ink.x1 - g.left_mark_overhang,
            g.ink.y1,
            g.ink.x2 - g.left_mark_overhang,
            g.ink.y2,
        ))
    };
    let probe_cells: Vec<facet_layout::GridCell> = probe.iter().map(|(_, g)| cell_for(g)).collect();
    let (_, probe_bounds) = facet_layout::grid_layout(&probe_cells, &grid_opt);

    // Production's facet header is a VL `title`-role mark under the
    // spec's shared "guide-label" text style — Vega's own hardwired
    // `guide-label` defaults, `fontSize: 10`/`fill: #000` (probed against
    // `fixtures/facet-panel-axis-title` and `fixtures/multiples/01-rows-
    // only`'s own reference SVGs); the font FAMILY is the caller's own
    // `header_font_family` (the theme's base `font_sans` cascade, NOT
    // either channel axis' own — see each caller's own doc comment).
    let header_font_family = spec.header_font_family;
    const HEADER_FONT_SIZE: f64 = 10.0;
    const HEADER_COLOR: &str = "#000000";
    // Vega's own row-header layout constants (`packages/vega-view-
    // transforms/src/layout/grid.js`'s `trellisLayout`, `off.rowHeader` and
    // the header's own nested title-mark offset) — verified, not guessed:
    // probed live against `fixtures/facet-panel-axis-title`'s own reference
    // SVG (2019/2020/... row headers), whose real nested transforms decode
    // to EXACTLY `-(ROW_OFFSET + TITLE_OFFSET + shaped_width)` for every
    // label when `shaped_width` is measured with this crate's own
    // `FontBook::measure_shaped` (GPOS-kerned — the width VEGA itself
    // measures a title with) in the header's OWN font family above.
    // `HEADER_TITLE_OFFSET` is VL's `config.header.labelPadding` default
    // (`vega-lite/src/config.ts:358`). The row-header's own no-mirror gap
    // from the cell (real Vega's flat "2px" for bar) is now measured, not
    // hardcoded — see `left_mark_overhang`'s own doc comment below.
    const HEADER_TITLE_OFFSET: f64 = 10.0;
    // A column header's own band height — probe-verified against `fixtures/
    // multiples/02-columns-only`'s and `03-grid`'s own reference SVGs:
    // `column-header`'s frame is exactly `[0, -20, ..., 0]` (a flat 20px
    // band above row 0, `HEADER_TITLE_OFFSET + HEADER_FONT_SIZE`).
    const COLUMN_HEADER_BAND_HEIGHT: f64 = HEADER_TITLE_OFFSET + HEADER_FONT_SIZE;

    // Row headers (once per ROW) and column headers (once per COLUMN) are
    // independent bands — probe-verified against `03-grid`'s own reference
    // SVG.
    let row_labels: Vec<String> = spec
        .row_axis_idx
        .map(|ri| {
            (0..panel_rows_n).map(|row_i| facet_value_label(&spec.panels[row_i * panel_cols_n].key[ri])).collect()
        })
        .unwrap_or_default();
    let row_label_widths: Vec<f64> = row_labels
        .iter()
        .map(|l| fonts.measure_shaped(l, header_font_family, HEADER_FONT_SIZE))
        .collect::<Result<Vec<f64>, String>>()
        .map_err(|e| format!("{}: {e}", spec.family))?;
    let max_row_label_w = row_label_widths.iter().copied().fold(0.0_f64, f64::max);

    let col_labels: Vec<String> = spec
        .col_axis_idx
        .map(|ci| (0..panel_cols_n).map(|col_i| facet_value_label(&spec.panels[col_i].key[ci])).collect())
        .unwrap_or_default();
    // The column-header band's own reach above y=0 — one of the two terms
    // `margin_top` below unions against the topmost row's own value-axis
    // overhang (`ViewLayout.js:72-82`: `RowFooter`/`RowHeader`/`ColHeader`/
    // `ColFooter`/a `ScopeRole` cell all union into the SAME `xBounds`/
    // `yBounds`, never a per-role sum).
    let header_band = if spec.col_axis_idx.is_some() { COLUMN_HEADER_BAND_HEIGHT } else { 0.0 };

    let pad_l = payload.padding.as_ref().map_or(0.0, |p| p.left);
    let pad_r = payload.padding.as_ref().map_or(0.0, |p| p.right);
    let pad_t = payload.padding.as_ref().map_or(0.0, |p| p.top);
    let pad_b = payload.padding.as_ref().map_or(0.0, |p| p.bottom);

    // Real Vega's `viewSizeLayout` (`ViewLayout.js:165`) reserves the
    // composite's own outer top margin as `ceil(-viewBounds.y1)`, where
    // `viewBounds` UNIONS every row-header/row-footer/column-header/
    // column-footer role's own bounds — and every `ScopeRole` cell's own
    // ink too (`layoutGroup`, `ViewLayout.js:48-84`) — never sums them: a
    // column facet's header band and the topmost row's own value-axis
    // overhang share the SAME origin (both reach upward from local `y=0`),
    // so the true reserve is whichever term reaches FURTHER, not their
    // total. Real Vega repeats the value axis (`row_footer`/`row_header`)
    // once per row, but only the TOPMOST row's own copy sits unshifted at
    // `y=0`, so only ITS topmost tick label's own overhang can contribute
    // the axis side of that union (`PanelGeometry.top_axis_overhang`'s own
    // doc comment carries the scenegraph evidence). On a `multiples.scale:
    // "shared"` facet, every panel's value axis has the identical ladder,
    // so `probe[0]`'s own measurement is correct for whichever row
    // actually lands on top — for THIS
    // (naive-height) probe pass only; the topmost tick's own y-position
    // scales with panel height whenever it sits below the domain max (the
    // common baked-headroom case), so the height-correction probe below
    // must use this naive-height reading (matching production's own
    // naive-height scenegraph probe) while the REAL final canvas/paint
    // re-measures it at the corrected `child_height` instead (below,
    // after `final_panels` — never this same `probe[0]` value, which
    // would carry the wrong height's own tick position). `probe_bounds.
    // y1` folds in the WHOLE topmost row's own CELL ink — every column's
    // own mark bounds (`bar_ink`), unioned by `grid_layout` above, not
    // just column 0's — the SAME union, never a second, separately-
    // computed term: reading it here is the ONLY place that ink is
    // counted toward the composite's own top margin.
    let probe_margin_top = stack_chrome_above(
        header_band.max(probe[0].1.top_axis_overhang).max((-probe_bounds.y1).max(0.0)),
        chrome,
    )
    .margin_top;
    // `axis_y.mirror`'s ghost copy: `packages/vega-view-transforms`'
    // axis-label encoder positions BOTH the primary and the ghost's own
    // anchor at `sign*(tickSize + labelPadding)`, identical config on both
    // edges — see `docs/laws-shared.md`'s facet ledger for the probe
    // evidence this reserve formula is built from. `label_padding` is
    // `probe[0].1.val_gutter` — the SAME `gutter_of` call a single-chart
    // right-axis fit arm already makes, never a reconstruction from
    // `widest_val_tick_label_width` (the PAINT font's widest DRAWN label, a
    // different quantity, correct only for the ghost's own OUTWARD ink
    // below).
    let widest = match &probe[0].1.mirror_style {
        Some(style) if has_mirror => probe[0].1.val_ticks.iter().try_fold(0.0_f64, |acc, (_, label)| {
            fonts.measure_shaped(label, &style.val_family, style.val_font_size).map(|w| acc.max(w))
        })?,
        _ => probe[0].1.widest_val_tick_label_width,
    };
    let label_padding = probe[0].1.val_gutter;
    let primary_reserve = probe[0].1.val_tick_size + label_padding;
    let ghost_reserve = if has_mirror { primary_reserve + widest } else { row_axis.map_or(0.0, |a| a.reserve) };

    // `left_mark_overhang` (`m.left`) is the cell's own ink overhang past
    // its LEFT edge — a real Vega `row_header` shifts by exactly this much
    // (`layoutHeaders`'s real algorithm, unported here; this is the
    // closed-form reduction). On bar's own band x-scale this is the flat
    // 2px crisp-stroke allowance the ORIGINAL `HEADER_ROW_OFFSET` constant
    // named (every bar rung measures `m.left == 2.0` — the SAME crisp
    // overhang `CELL_INK_OVERFLOW`/`HEADER_ROW_OFFSET` already assumed as
    // a flat guess); a line/area point scale with `axis_x.scale.padding: 0`
    // and a nonzero mark reach measures a real, larger value instead — one
    // law, not a family-specific flat constant. This reserve depends only
    // on the shared band x-scale (never the per-panel value domain), so
    // it is uniform across every panel regardless of `multiples.scale`,
    // and `probe[0]`'s own measurement stands for the composite.
    let left_mark_overhang = probe[0].1.left_mark_overhang;
    // See `PanelGeometry.raw_right_reach`'s own doc comment — the UNCEILED
    // twin of `left_mark_overhang`, read the same way (uniform across
    // every panel for the same band-x-scale reason, so `probe[0]`'s own
    // measurement stands for the composite).
    let raw_right_reach = probe[0].1.raw_right_reach;
    // A row facet's own row-header role holds the row's own facet-value
    // title (e.g. "East"/"West"), the ghost axis when mirrored, and
    // `left_mark_overhang` — every panel's own plot-origin gap from its
    // canvas edge (`canvas_x`'s own doc comment) — as ADDITIVE sibling bands:
    // `[title][HEADER_TITLE_OFFSET][ghost][left_mark_overhang][cell]`,
    // never overlapping. `left_mark_overhang` is present EITHER WAY —
    // mirrored or not — since it reserves room for the cell's own ink, a
    // fact independent of whether a ghost sits beside it; the pre-fix
    // formula dropped it whenever mirrored (`ghost_reserve` replacing it
    // outright, not adding to it), landing the ghost/header/footer
    // `left_mark_overhang` too close to the cell — probed on `fixtures/
    // multiples/02-columns-only`/`03-grid` (both mirrored): 1.73px per
    // panel, matching `left_mark_overhang`'s own measured value (2.0) to
    // within the accepted font-measurement epsilon.
    let row_header_offset = ghost_reserve + left_mark_overhang;
    // The view's left margin is `ceil(-viewBounds.x1)` (`viewSizeLayout`,
    // `ViewLayout.js:163`), where `viewBounds` UNIONS every role's bounds
    // (`layoutGroup`, `ViewLayout.js:72-82`) — never sums them: the row header
    // band (title, ghost axis and `left_mark_overhang`, additive siblings) and the
    // shared x axis's leftmost label (`column-footer`) both reach left from the
    // same origin, so the margin is whichever reaches further. A columns-only
    // facet has no row title; its band alone can be narrower than the first
    // footer label's half width. The shared x is uniform across panels, so
    // `probe[0]` stands for the composite.
    let footer_left_reach = probe[0].1.axis.label_left_reach();
    // The frame's leftmost ink in frame coordinates: a title anchors here, and
    // its own text can be the widest thing in the view (`viewSizeLayout`'s
    // `right`).
    let frame_left_extent = if spec.row_axis_idx.is_some() {
        HEADER_TITLE_OFFSET + max_row_label_w + row_header_offset
    } else {
        row_header_offset
    }
    .max(footer_left_reach);
    let margin_left = frame_left_extent.ceil();
    // `grid_opt` is defined early, above (needed there by `margin_top`/
    // `probe_margin_top` too) — reused here verbatim.
    // A multi-column row's own axis gutter is NOT folded into any column's
    // own ink — it is reserved here instead, once, past the whole grid's
    // own ink, mirroring where real Vega's `row_footer` role actually sits.
    // A rows-only facet (`panel_cols_n == 1`) needs no such addition: the
    // fold already carries it inside `bounds`. Reads the measured
    // `axis_w_overhang`, the same ink the fold uses: the primary paints at
    // `val_tick_size + val_gutter` past the plot, exactly the `primary_reserve`
    // the ghost mirrors on the opposite edge.
    let composite_axis_reserve =
        if panel_cols_n > 1 { probe[0].1.axis_w_overhang - left_mark_overhang } else { 0.0 };
    let frame_origin_x = pad_l + margin_left;
    // The view is as wide as the widest of the grid, the legend and the
    // title (`viewSizeLayout`'s `right` is the union of every role's bounds).
    let widen_for_chrome = |grid_canvas_w: f64, panel_w: f64| -> f64 {
        let grid_canvas_w = grid_canvas_w.ceil();
        match chrome {
            None => grid_canvas_w,
            Some(chrome) => {
                let title_right = chrome.title.as_ref().map_or(0.0, |t| (t.ink.width - frame_left_extent).ceil());
                let legend_right = chrome.legend.as_ref().map_or(0.0, |l| (l.width)(panel_w).ceil());
                grid_canvas_w.max(frame_origin_x + title_right.max(legend_right) + pad_r)
            }
        }
    };
    // Production's width law is the same single-pass overshoot correction
    // the height gets (`_correct_facet_overshoot`, `converters/chart.py:731`):
    // the probe render at the flat `facet_panel_width` guess measures the
    // composite's real width, and the overshoot past the card comes off
    // every column evenly. The flat guess budgets a fixed chrome gutter; the
    // marks' own reach, a tilted axis or a wide title paint past it.
    let panel_w = {
        let probed_canvas_w = widen_for_chrome(
            pad_l + margin_left - left_mark_overhang
                + (probe_bounds.x2 - probe_bounds.x1 + composite_axis_reserve)
                + pad_r,
            panel_w_guess,
        );
        correct_facet_child_extent(panel_w_guess, probed_canvas_w, payload.width, panel_cols_n)
    };
    if panel_w <= 0.0 {
        return Err(format!("{}: panel width collapsed to zero or below after reserving the row-header column and the shared value axis", spec.family));
    }

    // How far below its own `plot_bottom` the shared footer axis paints
    // (tick + label + title) — measured from a real render rather than
    // re-deriving Vega's own ink formula a second time; `canvas_h` must
    // reserve this or the footer's own labels paint past the declared card
    // height.
    let mut footer_probe = String::new();
    probe.last().expect("panels is non-empty (checked by the caller)").1.axis.paint(
        &mut footer_probe,
        spec.axis_x,
        0.0,
        panel_w,
        0.0,
    );
    // `max_y_attr` reads the label text's own drawn `y` attribute, which is
    // its "top"-anchored BASELINE, not the bottom of its own glyph box —
    // `vega_fit::text_ink`'s box law puts that box's own bottom edge
    // `fs - round(0.8*fs)` past the drawn y (see `docs/laws-shared.md`'s
    // facet ledger for the live scenegraph probe this correction is built
    // from).
    let x_label_fs = spec.axis_x.labels.font.size_or(11.0);
    // The axis group is drawn `AXIS_TRANSLATE` past its layout position, but its
    // bounds are translated by `x`/`y` alone (`vega-view-transforms/src/layout/
    // axis.js:77`), so the measured reach drops that half pixel for every arm.
    let footer_reach = max_y_attr(&footer_probe) + (x_label_fs - (0.8 * x_label_fs).round()) - AXIS_TRANSLATE;

    // Vega's own view-level `autosize: {type: "pad"}` arithmetic
    // (`vega_fit::pad_plot`'s law, ported here for the facet composite):
    // canvas = padding + ink margins + declared content, on both axes.
    // `margin_top` is an explicit parameter, not a captured variable: this
    // formula must be evaluated at TWO different `margin_top` values (the
    // naive-height probe's own union, then the corrected final height's
    // own union, below) — a captured `margin_top` would need interior
    // mutability or re-definition for no benefit over just passing the
    // value the caller already has in hand. The content's own left edge is
    // `canvas_x(0)` (below) — the SAME point the splice uses, `left_mark_
    // overhang` left of `margin_left` — never `margin_left` itself:
    // `margin_left` already reserves `left_mark_overhang` for the row-
    // header column (`row_header_offset`), and `content_w` (`bounds.x2 -
    // bounds.x1` of cells whose own `ink` starts at the panel-canvas
    // frame's `pad_l == 0`, i.e. `left_mark_overhang` left of the plot)
    // carries that same term a second time — reserving it under BOTH names
    // widened every rows-only canvas by exactly `left_mark_overhang` (03:
    // 597 vs the reference's 595; 02: 564 vs 561, the extra pixel there the
    // ledger's own pre-existing font-measurement epsilon).
    let canvas_for = |content_w: f64, content_h: f64, margin_top: f64| -> (f64, f64) {
        (pad_l + margin_left - left_mark_overhang + content_w + pad_r, pad_t + margin_top + content_h + pad_b)
    };
    // A panel's own SVG canvas starts `left_mark_overhang` LEFT of its own
    // plot — `render_pass`'s per-panel callee bakes that margin in (`pad:
    // None`, so the panel's own internal `plot_left == m.left`), the same
    // way a real Vega cell group's origin sits at the PLOT'S OWN origin
    // while its ink (`GridCell.bounds`) extends `m.left` further left —
    // `layoutHeaders` (`grid.js`) places `row_header` at that ink edge
    // (`bounds.x1`), never at the group's own declared `x`. `canvas_x`
    // returns that CANVAS edge, not the plot origin: correct for the
    // splice (a panel's own content already carries its `m.left` as an
    // inner translate) and for the mirror ghost's own anchor (real Vega's
    // `row_header` sits at exactly this ink edge too) — but the SHARED
    // x-axis footer and the column-header center are real Vega roles that
    // sit at the CELL's own declared origin, `left_mark_overhang` to the
    // RIGHT of the canvas edge (`layoutHeaders` places `column_footer`/the
    // header band at the cell's own `x`, never its ink's left overhang) —
    // each call site below adds `left_mark_overhang` back for those two
    // roles. Splicing a panel's own canvas at the RAW grid offset `dx`
    // (this composer's previous law) put the canvas's own left edge — not
    // its plot origin — at the cell's declared x, so every panel's own
    // plot painted `left_mark_overhang` too far right: probed on
    // `fixtures/multiples/06-line-rows` (a line point scale's own `left_
    // mark_overhang` reaches 7px, `axis_x.scale.padding: 0` plus a nonzero
    // stroke), the shared x axis's Jan gridline landed at 64.5 against the
    // reference's 57.5 — every x label 7px right of the point/gridline it
    // names. `header_right_edge` needs no matching
    // change, since it reads no `dx` (a row header's own reserve is already a
    // fixed offset from `pad_l`, independent of any panel).
    let canvas_x = |dx: f64| -> f64 { pad_l + margin_left + dx - left_mark_overhang };

    // `cell_for`/`grid_opt` are defined early, above (needed there by
    // `margin_top`/`probe_margin_top` too) — reused here verbatim, never
    // a second reading of the same law.
    // `layoutHeaders`'s own `column-footer` law (`grid.js`'s `trellisLayout`
    // → `layoutHeaders(..., agg=max, isX=1, bound=boundFull, bf='y2',
    // start=cells-ncols, stride=1, ...)`): EVERY column-footer instance
    // shares ONE y (`y = init`, computed once, before the per-header loop
    // ever runs) — the `agg` callback IS `Math.ceil(Math.max(a, b))`
    // (`grid.js:13`'s own `max` definition, the callback `layoutHeaders`
    // passes for every column-footer pass, `grid.js:268`), so this is
    // `ceil` of the MAX of `bounds.y2` across every cell in the LAST row,
    // never panel 0's, and never a per-column value despite a footer
    // painting once per column. Probed live against `fixtures/multiples/
    // 06-line-rows`'s own compiled spec (`vega@6.2.0`, `node_modules/
    // vega-view-transforms/src/layout/grid.js` read directly, not
    // decompiled): `column-footer`'s own computed `y` matches `ceil` of
    // the rows-only case's single last-row cell exactly (`groups[1].
    // bounds.y2`).
    let footer_y = |ge: &[(String, PanelGeometry)], offsets: &[(f64, f64)], last_row_start: usize| -> f64 {
        (0..panel_cols_n)
            .map(|c| {
                let idx = last_row_start + c;
                let (_, dy) = offsets[idx];
                dy + ge[idx].1.ink.y2
            })
            .fold(0.0_f64, f64::max)
            .ceil()
    };


    // Production's own facet child-height law is NOT a closed-form
    // subtraction: `_correct_facet_overshoot` (`converters/chart.py:729`,
    // ported as `correct_facet_child_extent` above) compiles the naive
    // per-panel height (`panel_h_guess`), probes ONE real render for the
    // composite's own actual total ink, then divides the overshoot evenly
    // across every row before the real, corrected render — the SAME
    // "measure real ink, then correct" shape `panel_w` above already gets
    // from `axis_w_overhang`, just applied to the WHOLE composite instead
    // of one named term. `probe` (rendered at `panel_h_guess`, before this
    // correction) already IS that naive-height first pass; folding its own
    // panels through the SAME grid math the real render below uses gives
    // this composite's own real total height at the naive guess, exactly
    // what production's scenegraph probe reads off `probe["height"]`.
    //
    // `probe_bounds` (computed early, above) mirrors the real render's own
    // `cells`/`bounds` below — identical math, fed the naive-height probe
    // panels instead of the corrected-height final ones, matching
    // production's own "probe pass, then real pass" shape rather than a
    // second, hand-derived formula.
    let (_, probed_canvas_h) = canvas_for(
        probe_bounds.x2 - probe_bounds.x1 + composite_axis_reserve,
        probe_bounds.y2 - probe_bounds.y1.max(0.0) + footer_reach,
        probe_margin_top,
    );
    // Production's own probe height (`probe["height"]`, `vlc.
    // vegalite_to_scenegraph`) is Vega's own integer VIEW height —
    // `viewSizeLayout`'s `top`/`bottom` (and `left`/`right`) are each
    // `Math.ceil`'d before `viewHeight = height + top + bottom` sums them
    // (`ViewLayout.js:165,200`), so the whole sum is already integral by
    // construction. `canvas_for` above sums un-ceiled `content_h`/
    // `margin_top`/padding terms, so this composer must ceil the SUM once
    // here to reproduce that same integer quantity, mirroring the `.ceil()`
    // the real final canvas already applies below.
    let child_height = correct_facet_child_extent(panel_h_guess, probed_canvas_h.ceil(), payload.height, panel_rows_n);
    if child_height <= 0.0 {
        return Err(format!("{}: panel height collapsed to zero or below after correcting for the composite's own measured overshoot", spec.family));
    }

    // `row_footer`'s own `ceil(cell x2)` anchor (`grid.js:262`, `:13`)
    // applies at every column count — `layoutHeaders`'s `start = ncols-1,
    // stride = ncols` reaches the LAST column of every row, not just a
    // rows-only facet's sole column — so this is never gated on
    // `panel_cols_n`. The last column's own grid offset (`dx`) never
    // enters the `ceil`: `grid_layout`'s own `align: "all"` law builds
    // every non-first column's `dx` from already-integer terms alone
    // (`grid.js:13`'s `x_max = max(bounds.x2.ceil())` and `offset_value`,
    // both ceiled; `opt.padding`, a whole-number constant), so `ceil(dx +
    // panel_w + raw_right_reach) == dx + ceil(panel_w + raw_right_reach)`
    // for every column, and the `dx` term cancels out of the overhang this
    // composer actually needs (the anchor's reach PAST `plot_w`, not the
    // anchor's own absolute position). A multi-column facet's
    // `composite_axis_reserve` only widens the canvas for this reach — it
    // never moves the anchor itself, so both still need the SAME ceil
    // here. `raw_right_reach`, never `left_mark_overhang`: real Vega ceils
    // the cell's own RAW `bounds.x2` once (`grid.js:13`'s `agg`), and
    // `left_mark_overhang` is already a ceiled reach (`vega_fit::pad_
    // plot`'s own `(-b.x1).ceil()`) — ceiling it a second time only agrees
    // with the real law when the raw reach is itself integral (see
    // `PanelGeometry.raw_right_reach`'s own doc comment).
    let row_footer_overhang = (panel_w + raw_right_reach).ceil() - panel_w;
    let final_panels = render_pass(panel_w, child_height, row_footer_overhang)?;
    let cells: Vec<facet_layout::GridCell> = final_panels.iter().map(|(_, g)| cell_for(g)).collect();
    let (offsets, bounds) = facet_layout::grid_layout(&cells, &grid_opt);
    // The topmost tick's own y-position scales with panel height whenever
    // it sits below the domain max, so the SAME `probe[0]` reading above
    // (measured at the naive `panel_h_guess`) is the WRONG term for the
    // real, corrected-height render — production's own real Vega render
    // re-lays-out fresh at `child_height` and its own top margin reflects
    // THAT height, never the probe pass's. Re-measured here, off `bounds`
    // (the grid union at the real, corrected `child_height`), for the
    // final canvas, every panel's own `dy` translate, and the footer/
    // ghost paint below — all of which read `margin_top` from this point
    // on, never `probe_margin_top`. `bounds.y1` re-measures the SAME
    // topmost-row cell-ink union `probe_margin_top` folds in above.
    let chrome_stack = stack_chrome_above(
        header_band.max(final_panels[0].1.top_axis_overhang).max((-bounds.y1).max(0.0)),
        chrome,
    );
    let margin_top = chrome_stack.margin_top;
    let (canvas_w, canvas_h) = canvas_for(
        bounds.x2 - bounds.x1 + composite_axis_reserve,
        bounds.y2 - bounds.y1.max(0.0) + footer_reach,
        margin_top,
    );
    if let Some(title) = chrome.and_then(|c| c.title.as_ref()).filter(|t| t.truncated) {
        if frame_origin_x - frame_left_extent + title.ink.width > canvas_w.ceil() - pad_r {
            return Err(format!("{}: multiples with a truncated title wider than the panel grid not supported", spec.family));
        }
    }
    let canvas_w = widen_for_chrome(canvas_w, panel_w);
    let canvas_h = canvas_h.ceil();

    let mut svg = String::with_capacity(16384 * spec.panels.len().max(1));
    let _ = write!(
        svg,
        "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{canvas_w}\" height=\"{canvas_h}\" \
         viewBox=\"0 0 {canvas_w} {canvas_h}\">"
    );
    let _ = write!(svg, "<rect width=\"{canvas_w}\" height=\"{canvas_h}\" fill=\"{bg}\"/>", bg = xml_escape_attr(bg));

    // Every row-header label's right edge lands on the SAME absolute x
    // independent of its own width.
    let header_right_edge = pad_l + margin_left - row_header_offset - HEADER_TITLE_OFFSET;
    // A column header centers horizontally on its own column's declared
    // `panel_w` and top-anchors to `[pad_t + margin_top -
    // COLUMN_HEADER_BAND_HEIGHT, pad_t + margin_top]` (`baseline_shift
    // ("top", ...)`), never centered in its own `COLUMN_HEADER_BAND_HEIGHT`
    // band the way a row header centers on `child_height`. When the header
    // band wins the union (`margin_top == COLUMN_HEADER_BAND_HEIGHT`, the
    // common case) that anchor reduces to `pad_t`, flush to the padded
    // canvas edge; when a large value-axis label font makes the axis
    // overhang win instead, Vega's own header group rides the view origin
    // down with the panels rather than staying pinned at `pad_t`.
    if spec.col_axis_idx.is_some() {
        let header_top = pad_t + margin_top - COLUMN_HEADER_BAND_HEIGHT;
        for col_i in 0..panel_cols_n {
            let (dx, _) = offsets[col_i];
            let text_x = canvas_x(dx) + left_mark_overhang + panel_w / 2.0;
            let text_y = header_top + vega_fit::baseline_shift("top", HEADER_FONT_SIZE);
            crate::contract::facet_header_open(&mut svg, &col_labels[col_i]);
            let _ = write!(
                svg,
                "<text x=\"{text_x:.2}\" y=\"{text_y:.2}\" text-anchor=\"middle\" \
                 font-family=\"{}\" font-size=\"{HEADER_FONT_SIZE}\" fill=\"{HEADER_COLOR}\">{}</text>",
                vega_escape_attr(header_font_family),
                xml_escape(&col_labels[col_i]), HEADER_COLOR = xml_escape_attr(HEADER_COLOR)
            );
            crate::contract::mark_group_close(&mut svg);
        }
    }
    // `axis_y.mirror`'s ghost copy (rows-only, columns-only, or a full
    // grid): the SAME `(y, label)` pairs the primary axis painted, at the
    // LEFT edge of column 0 in each row, sign-flipped the way a single-
    // chart's own ghost mirrors. The ANCHOR sits at `primary_reserve` from
    // the cell's own left edge, `end` then carries the text outward by
    // `widest` on top of that (already how `ghost_reserve` was derived).
    // The paint itself reads the SAME resolved style the primary painted
    // with, carried on `PanelGeometry`, rather than a second, independent
    // read of the axis style.
    if has_mirror {
        for row_i in 0..panel_rows_n {
            let panel_idx = row_i * panel_cols_n;
            let (dx, dy) = offsets[panel_idx];
            let ghost_x = canvas_x(dx) - primary_reserve;
            let geom = &final_panels[panel_idx].1;
            let style = geom
                .mirror_style
                .as_ref()
                .expect("has_mirror: true requires every panel to supply its own mirror_style");
            crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::Y, (canvas_x(dx) + 0.5, pad_t + margin_top + dy));
            for (abs_y, label) in &geom.val_ticks {
                let text_y = pad_t + margin_top + dy + abs_y;
                let _ = write!(
                    svg,
                    "<text x=\"{ghost_x:.2}\" y=\"{text_y:.2}\" text-anchor=\"end\" \
                     font-family=\"{}\" font-size=\"{}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
                    vega_escape_attr(&style.val_family),
                    style.val_font_size,
                    xml_escape_attr(&style.val_weight),
                    xml_escape_attr(&style.val_color),
                    xml_escape(label)
                );
            }
            crate::common::emit_value_ticks(
                &mut svg,
                &spec.axis_y.ticks,
                geom.val_tick_marks.iter().map(|mark_y| pad_t + margin_top + dy + mark_y),
                canvas_x(dx) + 0.5,
                -1.0,
                geom.val_tick_size,
            );
            crate::contract::axis_group_close(&mut svg);
        }
    }
    // A shared category axis: the labels of column 0's panel in every grid
    // row, left-anchored `label_x` from the plot's left edge.
    if let Some(axis) = row_axis {
        for row_i in 0..panel_rows_n {
            let panel_idx = row_i * panel_cols_n;
            let (dx, dy) = offsets[panel_idx];
            let geom = &final_panels[panel_idx].1;
            let style = geom
                .mirror_style
                .as_ref()
                .expect("a shared row axis requires every panel to supply its own label style");
            crate::contract::axis_group_open(&mut svg, crate::contract::AxisChannel::Y, (canvas_x(dx) + left_mark_overhang, pad_t + margin_top + dy));
            for (abs_y, label) in &geom.val_ticks {
                let _ = write!(
                    svg,
                    "<text x=\"{:.2}\" y=\"{:.2}\" text-anchor=\"start\" \
                     font-family=\"{}\" font-size=\"{}\" font-weight=\"{}\" fill=\"{}\">{}</text>",
                    canvas_x(dx) + left_mark_overhang + axis.label_x,
                    pad_t + margin_top + dy + abs_y,
                    vega_escape_attr(&style.val_family),
                    style.val_font_size,
                    xml_escape_attr(&style.val_weight),
                    xml_escape_attr(&style.val_color),
                    xml_escape(label)
                );
            }
            crate::contract::axis_group_close(&mut svg);
        }
    }
    // Real Vega's row-header LABEL centers on the row's own COMPILED
    // `child_height` signal — a declared quantity, NEVER an ink
    // measurement — while `cell_for`'s own per-panel `ink.y2` is what
    // `GridCell` row-spacing correctly uses. The panels collect into the
    // facet's ONE `role-scope cell` group below, so a header run painted
    // here never lands among them: `chart_interactivity.js` reads every
    // child of that group as a panel.
    let mut cell = String::new();
    for (i, (panel_svg, geom)) in final_panels.iter().enumerate() {
        let (dx, dy) = offsets[i];
        if spec.row_axis_idx.is_some() && i % panel_cols_n == 0 {
            let row_i = i / panel_cols_n;
            let text_x = header_right_edge - row_label_widths[row_i];
            let cy = pad_t + margin_top + dy + child_height / 2.0;
            let text_y = cy + vega_fit::baseline_shift("middle", HEADER_FONT_SIZE);
            crate::contract::facet_header_open(&mut svg, &row_labels[row_i]);
            let _ = write!(
                svg,
                "<text x=\"{text_x:.2}\" y=\"{text_y:.2}\" text-anchor=\"start\" \
                 font-family=\"{}\" font-size=\"{HEADER_FONT_SIZE}\" fill=\"{HEADER_COLOR}\">{}</text>",
                vega_escape_attr(header_font_family),
                xml_escape(&row_labels[row_i]), HEADER_COLOR = xml_escape_attr(HEADER_COLOR)
            );
            crate::contract::mark_group_close(&mut svg);
        }
        let content = &panel_svg[geom.content_start..geom.svg_end];
        let _ = write!(
            cell,
            "<g transform=\"translate({:.2},{:.2})\">{content}</g>",
            canvas_x(dx),
            pad_t + margin_top + dy
        );
    }
    svg.push_str(
        "<g class=\"mark-group role-scope cell\" role=\"graphics-object\" \
         aria-roledescription=\"group mark container\">",
    );
    svg.push_str(&cell);
    svg.push_str("</g>");

    // The shared x-axis (`column_footer` in real Vega's own trellisLayout
    // — a role structurally separate from every row's own `cell`, never
    // squeezed into the last row's own canvas) is painted ONCE PER COLUMN,
    // all at the SAME y (`footer_y`'s own doc comment — `layoutHeaders`
    // computes this union once, before its own per-header loop runs), at
    // the CELL's own declared x (`canvas_x(dx) + left_mark_overhang`) — the
    // same plot-origin correction the column header above applies, never
    // the panel's own canvas edge `canvas_x` alone returns.
    let last_row_start = panel_rows_n.saturating_sub(1) * panel_cols_n;
    let footer_bottom = pad_t + margin_top + footer_y(&final_panels, &offsets, last_row_start);
    for col_i in 0..panel_cols_n {
        let panel_idx = last_row_start + col_i;
        let (dx, _) = offsets[panel_idx];
        let footer_left = canvas_x(dx) + left_mark_overhang;
        final_panels[panel_idx].1.axis.paint(&mut svg, spec.axis_x, footer_left, panel_w, footer_bottom);
    }

    if let Some(chrome) = chrome {
        if let Some(legend) = &chrome.legend {
            (legend.paint)(&mut svg, frame_origin_x, pad_t + margin_top + chrome_stack.legend_y, panel_w);
        }
        if let Some(title) = &chrome.title {
            (title.paint)(&mut svg, frame_origin_x - frame_left_extent, pad_t + margin_top + chrome_stack.title_y);
        }
    }

    svg.push_str("</svg>");
    Ok((svg, canvas_h))
}

#[cfg(test)]
mod tests {
    use super::{
        correct_facet_child_extent, facet_value_label, stack_chrome_above, FacetChrome, LegendChrome,
    };
    use serde_json::Value;

    /// `layout/legend.js:60-64`: a top legend's bottom edge is
    /// `floor(yBounds.y1) - offset`, so a fractional union top (`-20.5`) is
    /// floored to `-21` before the 18px offset and the 13px legend height.
    #[test]
    fn a_top_legend_anchors_at_the_floor_of_the_union_top() {
        let chrome = FacetChrome {
            title: None,
            legend: Some(LegendChrome {
                width: Box::new(|_| 0.0),
                height: 13.0,
                paint: Box::new(|_, _, _, _| {}),
            }),
        };
        let stack = stack_chrome_above(20.5, Some(&chrome));
        assert_eq!(stack.legend_y, -52.0);
        assert_eq!(stack.margin_top, 52.0);
    }

    #[test]
    fn facet_value_label_string_value_renders_bare() {
        assert_eq!(facet_value_label(&Value::from("East")), "East");
    }

    #[test]
    fn facet_value_label_number_value_falls_back_to_json_form() {
        assert_eq!(facet_value_label(&Value::from(2019)), "2019");
    }

    mod correct_facet_child_extent_tests {
        use super::*;

        /// Production's own real numbers for `dbt-charts/tests/visual/
        /// fixtures/facet-panel-axis-title.yml` at its real production slot
        /// (600×802, 7-row facet, `axis_y.position: right`, baked
        /// `tick_values`, authored x-label format) — captured by wrapping
        /// the real `_correct_facet_overshoot` (`converters/chart.py:729`)
        /// with a spy that records its own `orig_h`/`probe["height"]`/
        /// `target_height`/`panel_rows` while rendering this exact board
        /// through the real production pipeline (`_board_render_contexts`
        /// + `_prod_render`, `golden_sweep.py`'s own machinery). The spy
        /// recorded `orig_h=802.0/7.0=114.57142857142857`,
        /// `probe["height"]=993.0`, `target_height=802.0`, `panel_rows=7`,
        /// and — after `_correct_facet_overshoot` ran — `unit["height"]=
        /// 87.28571428571428`. Applying the SAME formula by hand:
        /// `overshoot = 993.0 - 802.0 = 191.0`, `corrected = 114.57142857142857
        /// - 191.0 / 7.0 = 87.28571428571428` — matching the spy's own
        /// captured `unit["height"]` exactly.
        #[test]
        fn matches_production_on_facet_panel_axis_title() {
            let naive = 802.0 / 7.0;
            let corrected = correct_facet_child_extent(naive, 993.0, 802.0, 7);
            assert!(
                (corrected - 87.28571428571428).abs() < 1e-9,
                "expected 114.57142857142857 - 191.0/7.0 = 87.28571428571428, got {corrected}"
            );
        }

        /// Same spy, `libs/chart-svg/fixtures/multiples/01-rows-only.yml`
        /// at its real production slot (600×432, 2-row facet, unauthored
        /// `axis_y.position` — production's own unlayered-bar default is
        /// `"right"`, `_overlay.py:633`, the same default this composer's
        /// own hardcoded right-side math assumes throughout): spy recorded
        /// `orig_h=216.0`, `probe["height"]=506.0`, `target_height=432.0`,
        /// `panel_rows=2`, corrected `unit["height"]=179.0`. By hand:
        /// `overshoot = 506.0 - 432.0 = 74.0`, `corrected = 216.0 - 74.0/2.0
        /// = 179.0` — a real, non-trivial correction (not a no-op) on this
        /// rung too.
        #[test]
        fn matches_production_on_01_rows_only() {
            let corrected = correct_facet_child_extent(216.0, 506.0, 432.0, 2);
            assert_eq!(corrected, 179.0, "expected 216.0 - 74.0/2.0 = 179.0");
        }

        /// Production's own one-directional guard (`if overshoot > 0` in
        /// `_correct_facet_overshoot`): a probe that lands AT or BELOW the
        /// target is not spare room to grow into — it means the naive guess
        /// already fits, so the naive value returns unchanged, never grown.
        #[test]
        fn is_a_noop_when_the_probe_already_fits() {
            assert_eq!(correct_facet_child_extent(100.0, 90.0, 802.0, 7), 100.0);
            assert_eq!(correct_facet_child_extent(100.0, 802.0, 802.0, 7), 100.0);
        }
    }
}
